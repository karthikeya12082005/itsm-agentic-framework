"""Remediation action catalogue for the lab (TODO 4.5) - the ONLY way agents change the simulated services.
Fixed actions (from the SOPs' action types), every call audited in data/lab/audit.jsonl with action_id,
ticket_id, authorization_id, before/after and a rollback spec (None = nothing to undo).

    RESTART_SERVICE        restart the process                          rollback: none (restart is safe)
    CLEAR_CACHE            drop all cache entries                       rollback: none (cache refills)
    ROLLBACK_DEPLOYMENT    re-deploy the previous release               rollback: REDEPLOY the release removed
    RENEW_CERTIFICATE      issue a new 90-day certificate               rollback: RESTORE_CERTIFICATE (old one)
    FREE_DISK_SPACE        move old spool files to spool_archive/<id>   rollback: RESTORE_FILES
    TERMINATE_DB_SESSIONS  end leaked database sessions                 rollback: none
    RESTART_NETWORK_DEVICE restart the VPN gateway (re-establishes the IPsec tunnel)  rollback: none
    UPDATE_DNS_RECORD      flush the resolver cache + re-sync the record from the zone  rollback: RESTORE_DNS
    PROVISION_VPN_PROFILE  re-issue the VPN profile (new 365-day credential)          rollback: RESTORE_PROFILE

    python -m src.lab.actions rollback <action_id> <ticket>
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

from src.lab import config as C
from src.lab import supervisor as S



def audit_path():
    return C.LAB_DIR / "audit.jsonl"            # resolved at call time (tests use a temporary lab dir)
_lock = threading.Lock()
CATALOGUE = ("RESTART_SERVICE", "CLEAR_CACHE", "ROLLBACK_DEPLOYMENT", "RENEW_CERTIFICATE", "FREE_DISK_SPACE",
             "TERMINATE_DB_SESSIONS", "RESTART_NETWORK_DEVICE", "UPDATE_DNS_RECORD", "PROVISION_VPN_PROFILE")
VPN_ONLY = {"RESTART_NETWORK_DEVICE", "UPDATE_DNS_RECORD", "PROVISION_VPN_PROFILE"}


class ActionError(RuntimeError):
    pass


def _audit(rec: dict) -> dict:
    rec = {"action_id": rec.pop("action_id", None) or str(uuid.uuid4()),
           "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}
    C.LAB_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, open(audit_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _admin(name, path):
    r = requests.post(C.url(name, path), headers={"X-Lab-Token": C.lab_token()}, timeout=10)
    if r.status_code != 200:
        raise ActionError(f"{name}{path}: HTTP {r.status_code} {r.text[:150]}")
    return r.json()


def settle(service: str, probes: int = 6) -> None:
    """After a change: clear the old error/latency window and send fresh traffic (metrics reflect the fix)."""
    # the service and everything that depends on it, transitively (6.1: vpn-gateway -> web -> orders-api is two
    # hops; resetting only direct dependents left the gateway "degraded" on stale errors after a fix)
    targets, todo = [], [service]
    while todo:
        s = todo.pop(0)
        if s not in targets:
            targets.append(s)
            todo += [n for n, v in C.SERVICES.items() if s in v["deps"]]
    for t in targets:
        try:
            _admin(t, "/admin/reset_stats")
        except (ActionError, requests.RequestException):
            pass
    for _ in range(probes):
        for t in targets:
            try:
                requests.post(C.url(t, "/work"), timeout=5)
            except requests.RequestException:
                pass


def execute(action: str, service: str, ticket_id: str, authorization_id: str) -> dict:
    if action not in CATALOGUE:
        raise ActionError(f"action {action} is not in the lab action catalogue")
    if service not in C.SERVICES:
        raise ActionError(f"unknown service {service}")
    if action in VPN_ONLY and service != "vpn-gateway":
        raise ActionError(f"{action} applies only to the vpn-gateway")
    d = C.service_dir(service)
    before, after, rollback, detail = {}, {}, None, ""
    try:
        if action == "RESTART_NETWORK_DEVICE":
            before = {"up": S.up(service)}
            pid = S.restart(service)
            after, detail = {"up": S.up(service), "pid": pid}, f"gateway restarted, tunnel re-established (pid {pid})"
        elif action == "UPDATE_DNS_RECORD":
            from src.lab.service import default_dns
            old = C.read_json(d / "dns.json", default_dns())
            new = {"zone": {**old.get("zone", {}), C.INTERNAL_HOST: C.INTERNAL_ADDR}, "cache": {}}
            C.write_json(d / "dns.json", new)
            before = {"cache_entries": len(old.get("cache", {})), "zone": old.get("zone")}
            after = {"cache_entries": 0, "zone": new["zone"]}
            detail = (f"resolver cache flushed ({before['cache_entries']} entries), {C.INTERNAL_HOST} -> "
                      f"{C.INTERNAL_ADDR} re-synced from the zone")
            rollback = {"action": "RESTORE_DNS", "params": {"service": service, "dns": old}}
        elif action == "PROVISION_VPN_PROFILE":
            old = C.read_json(d / "profile.json", {})
            new = {"not_after": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(timespec="seconds"),
                   "serial": f"VPN-{uuid.uuid4().hex[:6].upper()}"}
            C.write_json(d / "profile.json", new)
            before, after = {"not_after": old.get("not_after")}, {"not_after": new["not_after"]}
            detail = f"VPN profile {new['serial']} issued, valid until {new['not_after']}"
            rollback = {"action": "RESTORE_PROFILE", "params": {"service": service, "profile": old}}
        elif action == "RESTART_SERVICE":
            before = {"up": S.up(service)}
            pid = S.restart(service)
            after, detail = {"up": S.up(service), "pid": pid}, f"process restarted (pid {pid})"
        elif action == "CLEAR_CACHE":
            detail = f"{_admin(service, '/admin/cache/clear')['removed']} entries removed"
        elif action == "TERMINATE_DB_SESSIONS":
            detail = f"{_admin(service, '/admin/db/terminate_sessions')['terminated']} sessions terminated"
        elif action == "ROLLBACK_DEPLOYMENT":
            dep = C.read_json(d / "deploy.json", {})
            hist = dep.get("history", [])
            if len(hist) < 2:
                raise ActionError("no previous release to roll back to")
            removed, prev = hist[-1], hist[-2]
            before = {"version": dep.get("version")}
            dep.update(version=prev["version"], config=prev["config"], history=hist[:-1])
            C.write_json(d / "deploy.json", dep)
            after, detail = {"version": prev["version"]}, f"release {removed['version']} -> {prev['version']}"
            rollback = {"action": "REDEPLOY", "params": {"service": service, "release": removed}}
        elif action == "RENEW_CERTIFICATE":
            old = C.read_json(d / "cert.json", {})
            new = {"not_after": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(timespec="seconds"),
                   "serial": f"LAB-{uuid.uuid4().hex[:6].upper()}"}
            C.write_json(d / "cert.json", new)
            before, after = {"not_after": old.get("not_after")}, {"not_after": new["not_after"]}
            detail = f"new certificate {new['serial']} valid until {new['not_after']}"
            rollback = {"action": "RESTORE_CERTIFICATE", "params": {"service": service, "cert": old}}
        elif action == "FREE_DISK_SPACE":
            spool = d / "spool"
            files = sorted(spool.glob("*"), key=lambda p: p.stat().st_mtime)
            keep = files[-5:]                                    # newest files stay
            aid = str(uuid.uuid4())
            arch = d / "spool_archive" / aid
            arch.mkdir(parents=True, exist_ok=True)
            moved = [f.name for f in files if f not in keep]
            for name in moved:
                shutil.move(str(spool / name), str(arch / name))
            detail = f"{len(moved)} old spool files archived"
            rollback = {"action": "RESTORE_FILES", "params": {"service": service, "archive": str(arch), "files": moved}}
            settle(service)
            return _audit({"action_id": aid, "action": action, "service": service, "ticket_id": ticket_id,
                           "authorization_id": authorization_id, "status": "SUCCEEDED", "detail": detail,
                           "before": before, "after": after, "rollback": rollback})
    except (ActionError, requests.RequestException, RuntimeError, OSError) as e:
        return _audit({"action": action, "service": service, "ticket_id": ticket_id,
                       "authorization_id": authorization_id, "status": "FAILED", "detail": f"{type(e).__name__}: {e}",
                       "before": before, "after": after, "rollback": None})
    settle(service)
    return _audit({"action": action, "service": service, "ticket_id": ticket_id, "authorization_id": authorization_id,
                   "status": "SUCCEEDED", "detail": detail, "before": before, "after": after, "rollback": rollback})


def records() -> list[dict]:
    if not audit_path().exists():
        return []
    return [json.loads(x) for x in audit_path().read_text(encoding="utf-8").splitlines() if x.strip()]


def rollback(action_id: str, ticket_id: str) -> dict:
    recs = records()
    orig = next((r for r in recs if r["action_id"] == action_id and r["action"] != "ROLLBACK"), None)
    if orig is None or not orig.get("rollback"):
        raise ActionError("no rollback available for this action_id")
    if any(r["action"] == "ROLLBACK" and r.get("of_action_id") == action_id and r["status"] == "SUCCEEDED"
           for r in recs):
        raise ActionError("already rolled back")
    rb, p = orig["rollback"], orig["rollback"]["params"]
    d = C.service_dir(p["service"])
    if rb["action"] == "REDEPLOY":
        dep = C.read_json(d / "deploy.json", {})
        dep["history"] = dep.get("history", []) + [p["release"]]
        dep.update(version=p["release"]["version"], config=p["release"]["config"])
        C.write_json(d / "deploy.json", dep)
    elif rb["action"] == "RESTORE_CERTIFICATE":
        C.write_json(d / "cert.json", p["cert"])
    elif rb["action"] == "RESTORE_DNS":
        C.write_json(d / "dns.json", p["dns"])
    elif rb["action"] == "RESTORE_PROFILE":
        C.write_json(d / "profile.json", p["profile"])
    elif rb["action"] == "RESTORE_FILES":
        for name in p["files"]:
            src = __import__("pathlib").Path(p["archive"]) / name
            if src.exists():
                shutil.move(str(src), str(d / "spool" / name))
    settle(p["service"], probes=3)
    return _audit({"action": "ROLLBACK", "of_action_id": action_id, "service": p["service"], "ticket_id": ticket_id,
                   "authorization_id": "rollback", "status": "SUCCEEDED", "detail": rb["action"], "rollback": None})


def main():
    if sys.argv[1:2] == ["rollback"] and len(sys.argv) == 4:
        print(rollback(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
