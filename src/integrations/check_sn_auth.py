"""Check that the ServiceNow PDI is awake and that the integration user can log in through the REST API.

    python -m src.integrations.check_sn_auth            # as SERVICENOW_USER (ai.agent)
    python -m src.integrations.check_sn_auth --admin    # as admin (uses SERVICENOW_ADMIN_PASSWORD from .env)

Prints the HTTP status, the user record and its roles. Never prints passwords.
Exit code 0 only if the login returns HTTP 200 with JSON.
"""
import argparse
import os
import sys

import requests

from src.common import config  # noqa: F401  (loads .env)

TIMEOUT = 30


def env(name, default=""):
    return os.environ.get(name, default).strip().strip("'\"")


def instance_url():
    url = env("SERVICENOW_INSTANCE").rstrip("/")
    if not url.startswith("https://") or "service-now.com" not in url:
        sys.exit("SERVICENOW_INSTANCE in .env must look like 'https://devNNNNNN.service-now.com'")
    return url


def credentials(admin=False):
    if admin:
        user, pw = env("SERVICENOW_ADMIN_USER", "admin"), env("SERVICENOW_ADMIN_PASSWORD")
        if not pw:
            sys.exit("SERVICENOW_ADMIN_PASSWORD is empty in .env (needed only while setting up)")
    else:
        user, pw = env("SERVICENOW_USER"), env("SERVICENOW_PASSWORD")
        if not user or not pw:
            sys.exit("SERVICENOW_USER / SERVICENOW_PASSWORD missing in .env")
    return user, pw


def get_json(session, url, **params):
    """GET a Table API URL. Returns (status_code, json or None, short error text)."""
    try:
        r = session.get(url, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
    except requests.RequestException as e:
        return None, None, f"{type(e).__name__}: {str(e)[:200]}"
    if "json" not in r.headers.get("Content-Type", ""):
        # A hibernating PDI answers with an HTML wake-up page instead of JSON
        hint = "instance is probably HIBERNATING - wake it at developer.servicenow.com" if r.status_code < 400 else ""
        return r.status_code, None, f"non-JSON response ({r.headers.get('Content-Type', '?')}). {hint}"
    return r.status_code, r.json(), "" if r.ok else r.text[:300]


def check(admin=False):
    base = instance_url()
    user, pw = credentials(admin)
    s = requests.Session()
    s.auth = (user, pw)
    print(f"Instance : {base}")
    print(f"User     : {user}  (password length {len(pw)}, not shown)")

    code, data, err = get_json(s, f"{base}/api/now/table/sys_user", sysparm_limit=1,
                               sysparm_query=f"user_name={user}",
                               sysparm_fields="sys_id,user_name,active,locked_out,internal_integration_user,"
                                              "web_service_access_only")
    print(f"HTTP     : {code}")
    if code != 200 or data is None:
        print(f"FAIL     : {err}")
        if code == 401:
            print("           401 = wrong user name/password, user inactive/locked, or no API access.")
        return 1
    rows = data.get("result", [])
    if not rows:
        print("FAIL     : login worked but the user record is not readable (missing roles?)")
        return 1
    u = rows[0]
    print(f"Record   : active={u.get('active')} locked_out={u.get('locked_out')} "
          f"internal_integration_user={u.get('internal_integration_user', 'n/a')}")
    code, data, _ = get_json(s, f"{base}/api/now/table/sys_user_has_role", sysparm_limit=100,
                             sysparm_query=f"user={u['sys_id']}", sysparm_fields="role.name",
                             sysparm_display_value="true")
    roles = sorted({r.get("role.name") for r in (data or {}).get("result", []) if r.get("role.name")})
    print(f"Roles    : {', '.join(roles) if roles else '(could not read roles)'}")
    print("RESULT   : OK - REST login works (HTTP 200)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin", action="store_true", help="check the admin login instead of the integration user")
    sys.exit(check(ap.parse_args().admin))


if __name__ == "__main__":
    main()
