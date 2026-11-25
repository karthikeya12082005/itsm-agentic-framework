"""Notifications (TODO 6.5): ServiceNow e-mail first, SMTP from the agents as the fallback.

ServiceNow sends the e-mails itself (platform notifications "incident created / comment left / resolved" to the
caller; "AI Service Desk: approval needed" to the approvers on the watch list - servicenow_portal_setup.py).
The agents only step in when that does not happen:

  queue(number, recipients, subject, body)   called when the agents write a customer-visible message
  process(source, grace_s)                   called by the worker every poll (and `cli notify`):
      ServiceNow source: after `grace_s` seconds, if NO e-mail of that incident created since the message was
          sent by ServiceNow (sys_email type 'sent'), send the message by SMTP (fallback) and note it on the ticket.
      local source (no ServiceNow e-mail at all): send by SMTP right away.
  Pending messages live in data/notify/pending.json (gitignored). Never logs the SMTP password.

.env: NOTIFY_DEMO_EMAIL (one mailbox; demo users get plus-addresses), SMTP_HOST, SMTP_PORT, SMTP_USER,
SMTP_PASSWORD (Gmail: an App Password). Without SMTP_* the fallback is reported as 'not configured'.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from src.common.config import PROJECT_ROOT

log = logging.getLogger("notify")
PENDING = PROJECT_ROOT / "data" / "notify" / "pending.json"
GRACE_S = 180                    # ServiceNow's mail job runs every minute; 3 minutes before the agents step in
_lock = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip().strip("'\"")


def plus_address(user: str) -> str | None:
    """Demo mailbox address for a user name: base+user@domain (None if NOTIFY_DEMO_EMAIL is not set)."""
    base = _env("NOTIFY_DEMO_EMAIL")
    if "@" not in base or not user:
        return None
    local, domain = base.split("@", 1)
    return f"{local.split('+')[0]}+{user}@{domain}"


def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_USER") and _env("SMTP_PASSWORD"))


def send_smtp(to: list[str], subject: str, body: str) -> tuple[bool, str]:
    """Send one plain-text e-mail. Returns (ok, detail). STARTTLS on 587, SSL on 465."""
    to = [a for a in to if a and "@" in a]
    if not to:
        return False, "no recipient address"
    if not smtp_configured():
        return False, "SMTP not configured (SMTP_HOST / SMTP_USER / SMTP_PASSWORD in .env)"
    host, port, user = _env("SMTP_HOST"), int(_env("SMTP_PORT", "587") or 587), _env("SMTP_USER")
    msg = EmailMessage()
    msg["From"] = f"AI Service Desk <{user}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
                s.login(user, _env("SMTP_PASSWORD"))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(user, _env("SMTP_PASSWORD"))
                s.send_message(msg)
        return True, f"sent to {len(to)} recipient(s)"
    except (smtplib.SMTPException, OSError) as e:          # never include credentials in the message
        return False, f"{type(e).__name__}: {str(e)[:150]}"


# ---------------------------------------------------------------- pending queue
def _load() -> dict:
    try:
        return json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(PENDING)


def queue(number: str, recipients: list[str], subject: str, body: str, source: str = "servicenow") -> None:
    """Remember a customer-visible message (one pending entry per ticket and recipient set)."""
    recipients = sorted({r for r in recipients if r and r not in ("ai.agent", "monitoring")})
    if not recipients:
        return
    with _lock:
        d = _load()
        key = f"{number}|{','.join(recipients)}"
        d[key] = {"number": number, "users": recipients, "subject": subject, "body": body, "source": source,
                  "since": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save(d)


def process(source, grace_s: float = GRACE_S, out=None) -> list[dict]:
    """Deliver pending messages of THIS ticket system that it did not send. Returns what happened per entry."""
    if not getattr(source, "notifications", True):
        return []
    with _lock:
        d = {k: e for k, e in _load().items() if e.get("source", "servicenow") == source.name}
    results, keep = [], {}
    for key, e in d.items():
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(e["since"])).total_seconds()
        status = source.mail_status(e["number"], e["since"]) if hasattr(source, "mail_status") else None
        if status is not None and status.get("sent"):
            results.append({"number": e["number"], "result": "sent by ServiceNow"})
            continue                                               # done: drop it
        if status is not None and age < grace_s:
            keep[key] = e                                          # give ServiceNow time
            continue
        addrs = [source.user_email(u) for u in e["users"]]
        ok, detail = send_smtp(addrs, e["subject"], e["body"])
        results.append({"number": e["number"], "result": ("SMTP fallback " if status is not None else "SMTP ")
                        + ("sent" if ok else "FAILED") + f" ({detail})"})
        if ok:
            try:
                source.add_note(e["number"], "Notification", f"e-mail to {', '.join(e['users'])} sent by the agents' "
                                + ("SMTP fallback (ServiceNow had not sent it after "
                                   f"{int(age)} s)" if status is not None else "SMTP (no ticket-system e-mail)"))
            except Exception:                                      # noqa: BLE001 - e.g. canceled incident is read-only
                pass
        elif "not configured" not in detail and age < 24 * 3600:
            keep[key] = e                                          # transient SMTP problem: retry next poll
    with _lock:
        cur = _load()
        for k in d:
            cur.pop(k, None)
        cur.update(keep)
        _save(cur)
    for r in results:
        (out or log.info)(f"notify {r['number']}: {r['result']}")
    return results
