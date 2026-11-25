"""Pure logic of the endpoint service (no admin rights, no registry, no winget) - unit-tested offline.

Used by server.py:
  running_processes / blocking_processes   item 3.1 (an installer cancels while the app is running)
  domain_rollback_spec                     item 3.6 (rollback restores the domain's recorded 'before' state)
  rollback_state / active_grants           items 3.3 / 3.6 (time-limited grants, no double rollback)
"""
from __future__ import annotations

import csv
import io
import subprocess
from datetime import datetime, timezone

from .endpoint_config import PACKAGE_PROCESSES


# ------------------------------------------------------------------------------------------- processes (3.1)
def parse_tasklist_csv(text: str) -> set[str]:
    """`tasklist /FO CSV /NH` output -> lower-case image names."""
    return {row[0].strip().lower() for row in csv.reader(io.StringIO(text)) if row and row[0].strip()}


def running_processes() -> set[str]:
    """Image names of all processes (all sessions - the service runs as Administrator)."""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60)
    return parse_tasklist_csv(r.stdout)


def blocking_processes(package_id: str, running: set[str]) -> list[str]:
    return [p for p in PACKAGE_PROCESSES.get(package_id, []) if p.lower() in running]


# ------------------------------------------------------------------------------------------- rollback (3.6)
def domain_rollback_spec(domain: str, before: list[str]) -> dict:
    """Undo exactly this action: put the domain back into the state it had before (allowed or not).
    A grant that existed before the ticket is therefore never revoked by the ticket's rollback."""
    return {"action": "RESTORE_DOMAIN", "params": {"domain": domain, "allowed": domain in before}}


def rollback_state(records: list[dict], action_id: str) -> tuple[dict | None, dict | None]:
    """(original record, its successful ROLLBACK record or None)."""
    original = rollback = None
    for rec in records:
        if rec.get("action_id") == action_id and rec.get("action") != "ROLLBACK":
            original = rec
        elif (rec.get("action") == "ROLLBACK" and rec.get("status") == "SUCCEEDED"
              and (rec.get("params") or {}).get("of_action_id") == action_id):
            rollback = rec
    return original, rollback


# ------------------------------------------------------------------------------------------- grants (3.3)
def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def active_grants(records: list[dict], now: datetime | None = None) -> list[dict]:
    """Time-limited grants that succeeded and were not rolled back yet; `due` = expired now."""
    now = now or datetime.now(timezone.utc)
    rolled = {(r.get("params") or {}).get("of_action_id") for r in records
              if r.get("action") == "ROLLBACK" and r.get("status") == "SUCCEEDED"}
    out = []
    for r in records:
        exp = parse_time(r.get("expires_at"))
        if exp and r.get("status") == "SUCCEEDED" and r.get("action") != "ROLLBACK" \
                and r.get("action_id") not in rolled and r.get("rollback"):
            out.append({"action_id": r["action_id"], "ticket_id": r.get("ticket_id"), "action": r["action"],
                        "params": r.get("params"), "expires_at": r["expires_at"], "due": exp <= now})
    return out
