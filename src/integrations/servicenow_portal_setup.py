"""Phase 6 ServiceNow setup (6.2 / 6.3 / 6.5): portal form, notifications, demo user e-mail addresses.
Idempotent; needs SERVICENOW_ADMIN_PASSWORD (admin web session, create_ai_agent.Admin) - clear it afterwards.

    python -m src.integrations.servicenow_portal_setup --check     # read-only: what exists, what would change
    python -m src.integrations.servicenow_portal_setup             # create / update
    python -m src.integrations.servicenow_portal_setup --test      # submit the portal form AS employee01 (admin
                                                                   # impersonation) and check the incident

What it touches (only these; existing platform records are never modified except the two properties):
  - demo user records (employee01, manager01, 6 approvers): email = NOTIFY_DEMO_EMAIL plus-address
    (name+employee01@gmail.com) so all demo mail lands in one inbox. Logins: these users are normal (not
    web-service-only) users without roles; this PDI has no snc_internal role, so a user WITHOUT roles can use the
    portals. Passwords are set by hand in the UI (API password writes have no effect on this PDI).
  - outbound e-mail: glide.email.smtp.active = true. Before switching it on, the backlog of never-sent mails
    (type send-ready, addressed to the old @example.com placeholders) is marked send-ignored - otherwise the PDI
    would try to send ~400 stale mails.
  - notification "AI Service Desk: approval needed" (incident, approval changes to requested, AI Service Desk group,
    recipients = watch list: the agents put the approvers there in the same update). The caller gets the platform's
    own notifications (incident created / comment left / resolved).
  - record producer "AI Service Desk request" (Service Catalog -> portals /sp and /esc): "What do you need?"
    (access | network | internal_applications | software | hardware) + "Describe it"; creates an incident with
    caller = the logged-in user, that category, assignment group AI Service Desk, contact type self-service -> the
    agent worker picks it up like any other ticket; the agents' replies are customer-visible comments on it.
"""
from __future__ import annotations

import argparse
import sys
import time

from src.access.catalog import APPROVER_USERS
from src.common.config import PROJECT_ROOT  # noqa: F401  (loads .env)
from src.integrations.check_sn_auth import env
from src.integrations.create_ai_agent import Admin

DEMO_USERS = ["employee01", "manager01"] + [u for u, _ in APPROVER_USERS.values()]
AGENT_GROUP = "AI Service Desk"
NOTIFICATION = "AI Service Desk: approval needed"
PRODUCER = "AI Service Desk request"
REQUEST_TYPES = [("access", "Access to a website, AI tool or software"),
                 ("network", "Problem: VPN or network"),
                 ("internal_applications", "Problem: an application or website (e.g. the shop)"),
                 ("software", "Problem: software on my laptop"),
                 ("hardware", "Problem: my laptop or other hardware")]
PRODUCER_SCRIPT = """// AI Service Desk request (Phase 6.3): one incident per request, handled by the AI agents
current.caller_id = gs.getUserID();
current.category = producer.request_type + '';
current.contact_type = 'self-service';
current.assignment_group.setDisplayValue('AI Service Desk');
var d = (producer.details + '').trim();
current.short_description = d.length > 157 ? d.substring(0, 157) + '...' : d;
current.description = d;
current.impact = 3;
current.urgency = 3;
"""
APPROVAL_HTML = """<p>An AI Service Desk request needs your approval.</p>
<p><b>${number}</b>: ${short_description}<br/>Requested for: ${caller_id}</p>
<p>The agents recorded the policy, the reason and who may approve in the work notes of ${number}. Nothing has
been changed yet.</p>
<p>Give your decision with the agent CLI (your identity is checked against ServiceNow):<br/>
<code>python -m src.access.cli approve ${number} --by &lt;your user name&gt;</code> &nbsp;(add <code>--reject</code>
to reject)</p>
<p>${URI_REF}</p>"""


def _val(v) -> str:
    """Table API value: reference fields come back as {link, value}."""
    return (v.get("value") if isinstance(v, dict) else v) or ""


def plus_address(user: str) -> str | None:
    base = (env("NOTIFY_DEMO_EMAIL") or "").strip()
    if "@" not in base:
        return None
    local, domain = base.split("@", 1)
    return f"{local.split('+')[0]}+{user}@{domain}"


class Setup:
    def __init__(self, dry: bool):
        self.a, self.dry, self.changes = Admin(), dry, 0

    def say(self, what, status):
        print(f"  [{status:<9}] {what}")

    def change(self, what, fn):
        self.changes += 1
        if self.dry:
            self.say(what, "would set")
            return None
        out = fn()
        self.say(what, "set")
        return out

    def one(self, table, query, fields):
        rows = self.a.get(table, query, fields, limit=1)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ 6.2 users (e-mail for notifications)
    def users(self):
        print("Demo users (e-mail = plus-address of NOTIFY_DEMO_EMAIL; logins: passwords set in the UI)")
        if not plus_address("x"):
            self.say("NOTIFY_DEMO_EMAIL not set in .env -> e-mail addresses unchanged", "skipped")
            return
        for u in DEMO_USERS:
            r = self.one("sys_user", f"user_name={u}", "sys_id,email,active,locked_out,web_service_access_only")
            if not r:
                self.say(f"{u} missing - run servicenow_setup first", "MISSING")
                continue
            want = {"email": plus_address(u), "active": "true", "locked_out": "false",
                    "web_service_access_only": "false"}
            diff = {k: v for k, v in want.items() if r.get(k) != v}
            if diff:
                self.change(f"{u}: {', '.join(diff)}", lambda r=r, diff=diff: self.a.patch("sys_user", r["sys_id"], diff))
            else:
                self.say(f"{u}: e-mail and login flags", "unchanged")
        # the integration user is a machine: its placeholder address would make Gmail bounce every "work notes
        # added" mail back to the demo inbox once ServiceNow really sends -> notifications off for ai.agent
        agent = env("SERVICENOW_USER") or "ai.agent"
        r = self.one("sys_user", f"user_name={agent}", "sys_id,notification")
        if r and _val(r.get("notification")) != "1":
            self.change(f"{agent}: e-mail notifications off (machine user)",
                        lambda: self.a.patch("sys_user", r["sys_id"], {"notification": "1"}))
        elif r:
            self.say(f"{agent}: e-mail notifications off", "unchanged")

    # ------------------------------------------------------------------ 6.5 outbound e-mail
    def email(self):
        print("Outbound e-mail")
        prop = self.one("sys_properties", "name=glide.email.smtp.active", "sys_id,value")
        if prop and prop["value"] == "true":
            self.say("glide.email.smtp.active = true", "unchanged")
            return
        r = self.a.s.get(self.a.base + "sys_email", timeout=60,
                         params={"sysparm_query": "type=send-ready", "sysparm_limit": 1, "sysparm_fields": "sys_id"})
        total = int(r.headers.get("X-Total-Count") or 0)
        done = 0
        while not self.dry:
            rows = self.a.get("sys_email", "type=send-ready", "sys_id", limit=200)
            if not rows:
                break
            for row in rows:
                self.a.patch("sys_email", row["sys_id"], {"type": "send-ignored"})
            done += len(rows)
        self.say(f"{total if self.dry else done} never-sent mails (old placeholder addresses) -> send-ignored",
                 "would set" if self.dry else "set")
        self.change("glide.email.smtp.active = true",
                    lambda: self.a.patch("sys_properties", prop["sys_id"], {"value": "true"}))

    # ------------------------------------------------------------------ 6.5 outgoing mail account
    SMTP_ACCOUNT = "Gmail SMTP (AI Service Desk demo)"

    def smtp_account(self, rotate_password: bool = False):
        """The PDI has NO outgoing mail account (sys_email_account empty: queued mails never leave). This creates
        one that sends through the same Gmail account as the agents' fallback (SMTP_* in .env)."""
        print("Outgoing mail account (ServiceNow -> Gmail SMTP)")
        host, port, user = env("SMTP_HOST"), env("SMTP_PORT") or "587", env("SMTP_USER")
        pw = env("SMTP_PASSWORD").replace(" ", "")              # Gmail shows app passwords in groups of four
        if not (host and user and pw):
            self.say("SMTP_HOST / SMTP_USER / SMTP_PASSWORD not all set in .env", "skipped")
            return
        body = {"name": self.SMTP_ACCOUNT, "type": "smtp", "active": "true", "server": host, "port": port,
                "connection_security": "ssl" if port == "465" else "starttls", "enable_tls": "true",
                "authentication": "password", "user_name": user, "from": user, "email_user_label": "AI Service Desk"}
        r = self.one("sys_email_account", f"name={self.SMTP_ACCOUNT}", ",".join(["sys_id"] + list(body)))
        if r is None:
            self.change(f"create '{self.SMTP_ACCOUNT}' ({host}:{port})",
                        lambda: self.a.post("sys_email_account", {**body, "password": pw}))
            return
        diff = {k: v for k, v in body.items() if _val(r.get(k)) != v}
        if rotate_password:
            diff["password"] = pw
        if diff:
            self.change(f"update '{self.SMTP_ACCOUNT}': {', '.join(k for k in diff if k != 'password')}"
                        + (" + password" if "password" in diff else ""),
                        lambda: self.a.patch("sys_email_account", r["sys_id"], diff))
        else:
            self.say(f"'{self.SMTP_ACCOUNT}'", "unchanged")

    # ------------------------------------------------------------------ 6.5 approver notification
    def notification(self):
        print("Notification for approvers")
        grp = self.one("sys_user_group", f"name={AGENT_GROUP}", "sys_id")
        if not grp:
            sys.exit(f"group {AGENT_GROUP} missing - run servicenow_setup first")
        body = {"name": NOTIFICATION, "collection": "incident", "active": "true", "action_insert": "false",
                "action_update": "true", "generation_type": "engine",
                "condition": f"approvalCHANGESTOrequested^assignment_group={grp['sys_id']}^EQ",
                "recipient_fields": "watch_list", "send_self": "false",
                "subject": "Approval needed: ${number} - ${short_description}", "message_html": APPROVAL_HTML}
        r = self.one("sysevent_email_action", f"name={NOTIFICATION}", ",".join(["sys_id"] + list(body)))
        if r is None:
            self.change(f"create '{NOTIFICATION}'", lambda: self.a.post("sysevent_email_action", body))
            return
        diff = {k: v for k, v in body.items() if _val(r.get(k)) != v}
        if diff:
            self.change(f"update '{NOTIFICATION}': {', '.join(diff)}",
                        lambda: self.a.patch("sysevent_email_action", r["sys_id"], diff))
        else:
            self.say(f"'{NOTIFICATION}'", "unchanged")

    # ------------------------------------------------------------------ 6.3 portal form (record producer)
    def producer(self) -> str | None:
        print("Portal form (record producer)")
        cat = self.one("sc_catalog", "title=Service Catalog", "sys_id")
        if not cat:
            sys.exit("Service Catalog not found")
        category = (self.one("sc_category", f"sc_catalog={cat['sys_id']}^title=Can We Help You?", "sys_id")
                    or self.one("sc_category", f"sc_catalog={cat['sys_id']}^active=true", "sys_id"))
        body = {"name": PRODUCER, "table_name": "incident", "active": "true", "sc_catalogs": cat["sys_id"],
                "category": category["sys_id"] if category else "",
                "short_description": "Request access to a website, AI tool or software, or report an IT problem - "
                                     "handled by the AI Service Desk agents",
                "description": "<p>Describe what you need in your own words. AI agents read the request, check the "
                               "company policy, act on your laptop or the affected service, and reply here. Some "
                               "requests need an approval; you will see who approves.</p>",
                "script": PRODUCER_SCRIPT}
        r = self.one("sc_cat_item_producer", f"name={PRODUCER}", ",".join(["sys_id"] + list(body)))
        if r is None:
            r = self.change(f"create '{PRODUCER}'", lambda: self.a.post("sc_cat_item_producer", body))
            if r is None:                                      # dry run
                return None
        else:
            diff = {k: v for k, v in body.items() if _val(r.get(k)).strip() != v.strip()}
            if diff:
                self.change(f"update '{PRODUCER}': {', '.join(diff)}",
                            lambda: self.a.patch("sc_cat_item_producer", r["sys_id"], diff))
            else:
                self.say(f"'{PRODUCER}'", "unchanged")
        pid = r["sys_id"]
        self.variable(pid, "request_type", "What do you need?", "5", 100, REQUEST_TYPES)
        self.variable(pid, "details", "Describe your request or problem in your own words", "2", 200)
        return pid

    def variable(self, pid, name, question, vtype, order, choices=None):
        body = {"cat_item": pid, "name": name, "question_text": question, "type": vtype, "mandatory": "true",
                "order": str(order), "active": "true"}
        r = self.one("item_option_new", f"cat_item={pid}^name={name}", "sys_id,question_text,type,mandatory,order")
        if r is None:
            r = self.change(f"variable {name}", lambda: self.a.post("item_option_new", body))
            if r is None:
                return
        else:
            self.say(f"variable {name}", "unchanged")
        for i, (value, text) in enumerate(choices or [], 1):
            if not self.one("question_choice", f"question={r['sys_id']}^value={value}", "sys_id"):
                self.change(f"  choice {value}", lambda value=value, text=text, i=i: self.a.post(
                    "question_choice", {"question": r["sys_id"], "value": value, "text": text, "order": str(i * 100)}))


def portal_links(pid: str) -> dict:
    root = env("SERVICENOW_INSTANCE").rstrip("/")
    return {"form (Service Portal)": f"{root}/sp?id=sc_cat_item&sys_id={pid}",
            "form (Employee Center)": f"{root}/esc?id=sc_cat_item&sys_id={pid}",
            "my tickets (Service Portal)": f"{root}/sp?id=my_requests"}


def run_test() -> int:
    """Submit the portal form AS employee01 (admin impersonation, then back) and check the created incident."""
    a = Admin()
    pid = (a.get("sc_cat_item_producer", f"name={PRODUCER}", "sys_id") or [{}])[0].get("sys_id")
    emp = a.get("sys_user", "user_name=employee01", "sys_id")[0]["sys_id"]
    me = a.get("sys_user", "user_name=admin", "sys_id")[0]["sys_id"]
    ok = True

    def check(what, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'OK' if cond else 'FAIL'}]   {what}" + (f": {detail}" if detail else ""))

    check("record producer exists", pid)
    r = a.s.post(a.root + f"/api/now/ui/impersonate/{emp}", json={}, timeout=60)
    check("impersonating employee01", r.ok, f"HTTP {r.status_code}")
    try:
        r = a.s.post(a.root + f"/api/sn_sc/servicecatalog/items/{pid}/submit_producer", timeout=60,
                     json={"variables": {"request_type": "network",
                                         "details": "[portal test] I cannot connect to the VPN from home."}})
        res = r.json().get("result", {}) if "json" in r.headers.get("Content-Type", "") else {}
        num = res.get("number") or res.get("record_number") or ""
        check("form submitted as employee01", r.ok and num, f"HTTP {r.status_code} {num or r.text[:150]}")
        own = a.s.get(a.base + "incident", timeout=60, params={"sysparm_query": "caller_id.user_name!=employee01",
                                                               "sysparm_limit": 1, "sysparm_fields": "number"})
        check("employee01 cannot read other people's incidents", own.ok and not own.json().get("result"),
              f"{len(own.json().get('result', [])) if own.ok else own.status_code} rows")
    finally:
        a.s.post(a.root + f"/api/now/ui/impersonate/{me}", json={}, timeout=60)
    if num:
        time.sleep(1)
        inc = a.get("incident", f"number={num}", "number,caller_id.user_name,category,assignment_group.name,"
                                                 "contact_type,state,short_description,sys_id")[0]
        check("caller = employee01", inc["caller_id.user_name"] == "employee01", inc["caller_id.user_name"])
        check("category = network", inc["category"] == "network", inc["category"])
        check("assignment group = AI Service Desk (worker picks it up)", inc["assignment_group.name"] == AGENT_GROUP,
              inc["assignment_group.name"])
        check("state New, self-service", inc["state"] == "1" and inc["contact_type"] == "self-service",
              f"state {inc['state']}, {inc['contact_type']}")
        a.patch("incident", inc["sys_id"], {"state": "8", "close_notes": "[portal test] cleanup"})
        print(f"  cleanup: {num} canceled")
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="read-only: show what would change")
    ap.add_argument("--test", action="store_true", help="submit the form as employee01 and check the incident")
    ap.add_argument("--rotate-smtp-password", action="store_true", help="write SMTP_PASSWORD to the mail account again")
    args = ap.parse_args()
    if args.test:
        sys.exit(run_test())
    s = Setup(dry=args.check)
    s.users()
    s.email()
    s.smtp_account(args.rotate_smtp_password)
    s.notification()
    pid = s.producer()
    if pid:
        for k, v in portal_links(pid).items():
            print(f"  {k}: {v}")
    print(f"{'would change' if args.check else 'changed'}: {s.changes}")


if __name__ == "__main__":
    main()
