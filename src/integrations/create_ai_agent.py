"""Create (or repair) the ServiceNow integration user ai.agent on the PDI, using the admin account.

    python -m src.integrations.create_ai_agent

What it does (safe to re-run):
  1. logs in as admin (SERVICENOW_ADMIN_USER, default 'admin', + SERVICENOW_ADMIN_PASSWORD from .env),
  2. creates user SERVICENOW_USER (ai.agent) if missing; makes it active, not locked, an
     Internal Integration User (API only, cannot use the web UI),
  3. gives the roles itil + approval_admin + snc_basic_auth_api_access,
  4. prints the link to the user record, where the password is set ONCE by hand (see below).
Afterwards remove SERVICENOW_ADMIN_PASSWORD from .env (setup only).

Findings on PDI dev219979 (Brazil, Nov 2026) that shaped this script:
- Basic-auth restriction is enforced (glide.authenticate.basic_auth.restriction.enforce = true, since
  2026-11-12): REST calls with user/password are accepted only for users holding the role
  snc_basic_auth_api_access. Proven: admin got 401 without the role and 200 with it (role removed again).
  admin does not need that role, so this script logs in through login.do and calls the Table API with the
  web-session token (X-UserToken). ai.agent gets the role because the agents use Basic auth.
- Setting sys_user.user_password through the Table API (even with sysparm_input_display_value=true) is
  accepted but has NO effect on this release - the login keeps failing with 401. The password is therefore
  set in the UI with the user form's "Set Password" button and pasted into .env by the user.
"""
import re
import sys

import requests

from src.integrations.check_sn_auth import TIMEOUT, credentials, env, instance_url

ROLES = ["itil", "approval_admin", "snc_basic_auth_api_access"]


class Admin:
    """Table API access as admin through a web-login session (see module docstring)."""
    def __init__(self):
        self.root = instance_url()
        self.base = self.root + "/api/now/table/"
        user, pw = credentials(admin=True)
        self.s = requests.Session()
        self.s.get(self.root + "/login.do", timeout=TIMEOUT)
        r = self.s.post(self.root + "/login.do", timeout=TIMEOUT,
                        data={"user_name": user, "user_password": pw, "sys_action": "sysverb_login"})
        if re.search(r"User name or password invalid", r.text, re.I):
            sys.exit("admin web login failed: SERVICENOW_ADMIN_PASSWORD in .env is wrong")
        page = self.s.get(self.root + "/navpage.do", timeout=TIMEOUT).text
        m = re.search(r"g_ck\s*=\s*['\"]([0-9a-f]{20,})['\"]", page)
        if not m:
            sys.exit("admin web login failed (no session token) - wrong password, MFA, or instance hibernating?")
        self.s.headers.update({"X-UserToken": m.group(1), "Accept": "application/json",
                               "Content-Type": "application/json"})

    def _check(self, r, what):
        if "json" not in r.headers.get("Content-Type", ""):
            sys.exit(f"{what}: non-JSON answer (HTTP {r.status_code}) - is the instance hibernating?")
        if r.status_code == 401:
            sys.exit(f"{what}: HTTP 401 - admin session not accepted")
        if not r.ok:
            sys.exit(f"{what}: HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["result"]

    def get(self, table, query, fields, limit=1):
        r = self.s.get(self.base + table, timeout=TIMEOUT, params={
            "sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": limit})
        return self._check(r, f"GET {table}")

    def post(self, table, body, **params):
        return self._check(self.s.post(self.base + table, json=body, params=params, timeout=TIMEOUT),
                           f"POST {table}")

    def patch(self, table, sys_id, body, **params):
        return self._check(self.s.patch(self.base + f"{table}/{sys_id}", json=body, params=params,
                                        timeout=TIMEOUT), f"PATCH {table}")


def main():
    user = env("SERVICENOW_USER", "ai.agent")
    a = Admin()
    print(f"Instance: {a.root}  |  admin web login ...", end=" ")
    a.get("sys_user", "user_name=admin", "sys_id")
    print("OK")

    fields = "sys_id,user_name,active,locked_out,internal_integration_user,web_service_access_only"
    rows = a.get("sys_user", f"user_name={user}", fields)
    profile = {"user_name": user, "first_name": "AI", "last_name": "Agent",
               "email": "ai.agent@example.com", "active": "true", "locked_out": "false",
               "password_needs_reset": "false", "internal_integration_user": "true",
               "web_service_access_only": "false"}
    if rows:
        sys_id = rows[0]["sys_id"]
        a.patch("sys_user", sys_id, profile)
        print(f"User {user} exists - profile checked/updated")
    else:
        sys_id = a.post("sys_user", profile)["sys_id"]
        print(f"User {user} created")

    rec = a.get("sys_user", f"sys_id={sys_id}", fields)[0]
    print(f"Record: active={rec.get('active')} locked_out={rec.get('locked_out')} "
          f"internal_integration_user={rec.get('internal_integration_user', 'n/a')}")

    for role in ROLES:
        found = a.get("sys_user_role", f"name={role}", "sys_id")
        if not found:
            print(f"WARNING: role {role} does not exist on this instance")
            continue
        has = a.get("sys_user_has_role", f"user={sys_id}^role={found[0]['sys_id']}", "sys_id")
        if has:
            print(f"Role {role}: already granted")
        else:
            a.post("sys_user_has_role", {"user": sys_id, "role": found[0]["sys_id"]})
            print(f"Role {role}: granted")

    print(f"\nNext (by hand, once): open {a.root}/sys_user.do?sys_id={sys_id}")
    print("  -> Set Password -> Generate -> copy -> Save Password; paste it into .env as SERVICENOW_PASSWORD='...'")
    print("Then test with:  python -m src.integrations.check_sn_auth")


if __name__ == "__main__":
    main()
