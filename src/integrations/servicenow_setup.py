"""One-time (idempotent) ServiceNow setup for the LEAN design on PDI dev219979 - item 1.2.

    python -m src.integrations.servicenow_setup          # create/repair records (needs SERVICENOW_ADMIN_PASSWORD)
    python -m src.integrations.servicenow_setup --test   # end-to-end check as ai.agent only (no admin needed)

Lean design (user's decision, Nov 24 2026): the Incident table is used for everything. Access requests are
incidents with category 'access' ("Access request") and free text; there are no catalog items. Human users are
plain records WITHOUT logins (no password is ever set); approvals are given through our CLI and recorded on the
incident by ai.agent (approval field + work notes; sysapproval_approver rows are not allowed on incidents
for non-admins - see servicenow_client).

Setup creates (users/groups: or updates to the values below; safe to re-run):
  * users     manager01 (line manager), employee01 (title DEVELOPER, manager manager01), security01,
              serviceowner01, cab01, aigov01, appowner01, dataowner01 (approver records, item 3.2)
  * groups    one per policy approver_role (catalog.APPROVER_GROUPS): "IT Security" = SECURITY_TEAM,
              "IT Service Owners", "Change Advisory Board", "AI Governance Board", "Application Owners",
              "Data Owners" - one member each (catalog.APPROVER_USERS)
              "AI Service Desk" = tickets handled by the agents, member ai.agent
  * choice    incident.category 'access' - already on the PDI as "Access & Password"; only created if missing
The admin session is a web login (Basic auth is restricted to snc_basic_auth_api_access holders, and admin must
not get that role) - see create_ai_agent.Admin.
"""
from __future__ import annotations

import argparse
import sys

from src.access.catalog import APPROVER_GROUPS, APPROVER_USERS
from src.common import config  # noqa: F401  (loads .env)
from src.integrations.servicenow_client import ServiceNowClient, ServiceNowError, _env

# ---------------------------------------------------------------------------------------------- desired state
# Users are created in this order (a manager before the users that reference it). No passwords: no logins.
USERS = [
    {"user_name": "manager01", "first_name": "Manager", "last_name": "01", "title": "LINE_MANAGER",
     "email": "manager01@example.com"},
    {"user_name": "security01", "first_name": "Security", "last_name": "01", "title": "SECURITY_ANALYST",
     "email": "security01@example.com"},
    {"user_name": "employee01", "first_name": "Employee", "last_name": "01", "title": "DEVELOPER",
     "email": "employee01@example.com", "manager": "@manager01"},  # '@name' = sys_id of that user
] + [  # one approver record per approver group (item 3.2), no logins
    {"user_name": u, "first_name": g, "last_name": "Approver", "title": title, "email": f"{u}@example.com"}
    for g, (u, title) in APPROVER_USERS.items() if u != "security01"
]
USER_DEFAULTS = {"active": "true", "locked_out": "false", "internal_integration_user": "false",
                 "web_service_access_only": "false"}

# policy approver_role -> ServiceNow group: APPROVER_GROUPS in src/access/catalog.py (single source of truth;
# LINE_MANAGER is resolved from the caller's manager field instead)
AGENT_GROUP = "AI Service Desk"
ESCALATION_GROUP = "Service Desk"   # built-in human group on the PDI; escalated tickets are reassigned to it
GROUPS = [
    {"name": group, "description": f"Approvers for policy approver_role {role} "
                                   "(approvals given via the agent CLI, recorded by ai.agent)",
     "members": [APPROVER_USERS[group][0]]}
    for role, group in APPROVER_GROUPS.items()
] + [
    {"name": AGENT_GROUP, "description": "Incidents handled by the multi-agent ITSM framework (ai.agent)",
     "members": ["@integration"]},  # '@integration' = SERVICENOW_USER (ai.agent)
]

# The Brazil PDI already ships incident.category 'access' = "Access & Password" (sequence 1) - we use it as is.
# (Lesson: the first setup run overwrote its label; restored from sys_update_version. Choices are now
# create-only: an existing platform choice is never modified.)
ACCESS_CATEGORY = {"value": "access", "label": "Access & Password"}


# ---------------------------------------------------------------------------------------------- setup (admin)
class Setup:
    def __init__(self):
        from src.integrations.create_ai_agent import Admin  # needs SERVICENOW_ADMIN_PASSWORD
        self.a = Admin()
        self.ids: dict[str, str] = {}

    def find(self, table, query, fields="sys_id"):
        r = self.a.s.get(self.a.base + table, timeout=30, params={
            "sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": 1,
            "sysparm_exclude_reference_link": "true"})
        rows = self.a._check(r, f"GET {table}")
        return rows[0] if rows else None

    def upsert(self, table, key_query, body, label):
        """Create the record or patch only the fields that differ. Returns sys_id."""
        row = self.find(table, key_query, "sys_id," + ",".join(body))
        if row is None:
            sys_id = self.a.post(table, body)["sys_id"]
            print(f"  created   {label}")
            return sys_id
        diff = {k: v for k, v in body.items() if str(row.get(k, "")) != str(v)}
        if diff:
            self.a.patch(table, row["sys_id"], diff)
            print(f"  updated   {label}: {', '.join(sorted(diff))}")
        else:
            print(f"  unchanged {label}")
        return row["sys_id"]

    def resolve(self, value):
        if value == "@integration":
            value = "@" + _env("SERVICENOW_USER", "ai.agent")
        if isinstance(value, str) and value.startswith("@"):
            name = value[1:]
            if name not in self.ids:
                row = self.find("sys_user", f"user_name={name}")
                if row is None:
                    sys.exit(f"user {name} does not exist")
                self.ids[name] = row["sys_id"]
            return self.ids[name]
        return value

    def run(self):
        print(f"Instance: {self.a.root}  (admin web session)")
        print("Users (records only, no passwords -> no logins):")
        for u in USERS:
            body = {**USER_DEFAULTS, **{k: self.resolve(v) for k, v in u.items()}}
            self.ids[u["user_name"]] = self.upsert("sys_user", f"user_name={u['user_name']}", body,
                                                   f"sys_user {u['user_name']} ({u['title']})")

        print("Groups:")
        for g in GROUPS:
            gid = self.upsert("sys_user_group", f"name={g['name']}",
                              {"name": g["name"], "description": g["description"], "active": "true"},
                              f"group {g['name']!r}")
            for m in g["members"]:
                # always a sys_id: a plain name happened to work on insert (ServiceNow resolves display
                # values) but never matched the 'user=<sys_id>' lookup -> duplicate insert -> 403 on re-run
                uid = self.resolve(m if m.startswith("@") else "@" + m)
                who = _env("SERVICENOW_USER", "ai.agent") if m == "@integration" else m.lstrip("@")
                if self.find("sys_user_grmember", f"group={gid}^user={uid}"):
                    print(f"  unchanged member {who} of {g['name']!r}")
                else:
                    self.a.post("sys_user_grmember", {"group": gid, "user": uid})
                    print(f"  added     member {who} to {g['name']!r}")

        print("Incident category (create-only, existing platform choices are never changed):")
        cat = self.find("sys_choice", f"name=incident^element=category^value={ACCESS_CATEGORY['value']}"
                                      "^language=en^inactive=false", "sys_id,label")
        if cat:
            print(f"  exists    incident.category {ACCESS_CATEGORY['value']!r} = {cat['label']!r} (used as is)")
        else:
            self.a.post("sys_choice", {"name": "incident", "element": "category", "language": "en",
                                       "value": ACCESS_CATEGORY["value"], "label": ACCESS_CATEGORY["label"],
                                       "inactive": "false", "sequence": "1"})
            print(f"  created   incident.category {ACCESS_CATEGORY['value']!r} = {ACCESS_CATEGORY['label']!r}")
        print("SETUP DONE - now run:  python -m src.integrations.servicenow_setup --test")
        return 0


# ---------------------------------------------------------------------------------------------- test (ai.agent)
def run_test() -> int:
    """Everything the agents will do in 1.3-1.5, as ai.agent only. Leaves one canceled test incident."""
    sn = ServiceNowClient.from_env()
    ok = True

    def check(name, cond, info=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'OK' if cond else 'FAIL'}]   {name}{': ' + str(info) if info else ''}")
        return cond

    print(f"Instance: {sn.instance}  user: {sn.user}  (no admin)")
    emp = sn.first("sys_user", "user_name=employee01",
                   "sys_id,user_name,title,active,manager,manager.user_name")
    check("employee01 readable", emp, emp and f"title={emp['title']} manager={emp['manager.user_name']}")
    if not emp:
        return 1
    check("role (title) = DEVELOPER", emp["title"] == "DEVELOPER")
    check("LINE_MANAGER = caller's manager = manager01", emp["manager.user_name"] == "manager01")

    sec = sn.group_members(APPROVER_GROUPS["SECURITY_TEAM"])
    for role, group in APPROVER_GROUPS.items():            # item 3.2: every approver role has its group
        members = [m["user_name"] for m in sn.group_members(group)]
        check(f"{role} = {group!r} has {APPROVER_USERS[group][0]}", APPROVER_USERS[group][0] in members, members)
    desk = sn.first("sys_user_group", f"name={AGENT_GROUP}", "sys_id,name")
    desk_members = sn.group_members(AGENT_GROUP)
    check(f"group {AGENT_GROUP!r} with {sn.user}", desk and any(m["user_name"] == sn.user for m in desk_members),
          [m["user_name"] for m in desk_members])
    if not (desk and sec):
        return 1

    inc = sn.create("incident", {
        "caller_id": emp["sys_id"], "category": ACCESS_CATEGORY["value"], "contact_type": "self-service",
        "assignment_group": desk["sys_id"], "impact": "3", "urgency": "3",
        "short_description": "[setup test] I need access to github.com for code reviews",
        "description": "Created by servicenow_setup --test (safe to ignore). Hi, I need GitHub to review "
                       "pull requests for our team project."}, fields="sys_id,number")
    sid = inc["sys_id"]
    print(f"[OK]   created {inc['number']}  {sn.record_url('incident', sid)}")
    try:
        r = sn.get("incident", sid, "category,caller_id,assignment_group,state", display_value="all")
        check(f"category shows {ACCESS_CATEGORY['label']!r}",
              r["category"]["display_value"] == ACCESS_CATEGORY["label"], r["category"])
        check("caller + assignment group stored", r["caller_id"]["value"] == emp["sys_id"] and
              r["assignment_group"]["value"] == desk["sys_id"],
              f"{r['caller_id']['display_value']} / {r['assignment_group']['display_value']}")
        found = sn.query("incident", f"category=access^assignment_group={desk['sys_id']}^state=1^sys_id={sid}",
                         "number")
        check("poller query finds it (category=access, group, state New)", len(found) == 1)

        # approvals are recorded on the incident itself (approval field + work notes), see servicenow_client
        sn.set_task_approval("incident", sid, "requested",
                             "[setup test] approval requested from SECURITY_TEAM (group 'IT Security')")
        sn.set_state("incident", sid, "on_hold")
        r = sn.get("incident", sid, "state,approval")
        check("On Hold + approval 'requested'", (r["state"], r["approval"]) == ("3", "requested"), r)

        # the approver check the CLI will do (1.5): who may approve a SECURITY_TEAM request?
        members = {m["user_name"] for m in sn.group_members(APPROVER_GROUPS["SECURITY_TEAM"])}
        check("manager01 may NOT approve SECURITY_TEAM", "manager01" not in members)
        check("security01 may approve SECURITY_TEAM", "security01" in members)

        sn.set_task_approval("incident", sid, "approved",
                             "[setup test] approved by security01 (IT Security / SECURITY_TEAM) via agent CLI")
        r = sn.get("incident", sid, "approval")
        check("approval 'approved' recorded", r["approval"] == "approved", r)

        sn.add_comment("incident", sid, "[setup test] customer-visible message")
        notes = [e["value"] for e in sn.get_journal("incident", sid)]
        check("approval work notes readable (newest first)", notes[:2] == [
            "[setup test] approved by security01 (IT Security / SECURITY_TEAM) via agent CLI",
            "[setup test] approval requested from SECURITY_TEAM (group 'IT Security')"], notes)
    except ServiceNowError as e:
        check("test step", False, f"{type(e).__name__}: {e}")
    finally:
        sn.set_state("incident", sid, "canceled", work_note="[setup test] finished - canceled",
                     extra={"close_notes": "setup test"})
        print(f"[OK]   cleanup: {inc['number']} canceled")
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="check the setup as ai.agent (creates 1 test incident)")
    args = ap.parse_args()
    sys.exit(run_test() if args.test else Setup().run())


if __name__ == "__main__":
    main()
