"""Live test of the endpoint service v2. Run in a NORMAL terminal (venv active) while the service runs in the
Administrator terminal. Every change it makes is rolled back; the final state equals the start state.

    python endpoint_service\\test_endpoint.py            # checks below (domains only, no installs)
    python endpoint_service\\test_endpoint.py install    # + async install of 7-Zip, verify, uninstall (rollback)

Checks: v2 running; every managed domain blocked by default; running-process pre-check (VS Code is open when
this runs from VS Code -> install BLOCKED, nothing changed); rollback restores the recorded before-state
(grant of ticket B on top of ticket A's grant, rollback of B keeps A's access); double rollback refused;
expires_at in the past refused; time-limited grant listed in /grants; unmanaged domain 403; wrong token 401.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from endpoint_service.endpoint_config import MANAGED_DOMAINS  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = "http://127.0.0.1:8765"
H = {"X-Endpoint-Token": os.environ["ENDPOINT_TOKEN"].strip()}
OK = True


def check(name, cond, info=""):
    global OK
    OK &= bool(cond)
    print(f"[{'OK' if cond else 'FAIL'}]   {name}" + (f": {info}" if info not in ("", None) else ""))


def get(path, **kw):
    return requests.get(BASE + path, headers=kw.pop("headers", H), timeout=120, **kw)


def post(path, body):
    return requests.post(BASE + path, headers=H, json=body, timeout=120)


def allowed(domain):
    return get(f"/verify/domain/{domain}").json()["allowed"]


h = get("/health").json()
check("service v2 running", h.get("version") == "2.0", h)
st = get("/status").json()
check("every managed domain is blocked by default", set(MANAGED_DOMAINS) <= set(st["blocklist"]),
      sorted(set(MANAGED_DOMAINS) - set(st["blocklist"])))
start_allow = st["allowlist"]
print("allowlist at start:", start_allow)

# ---- 3.1 pre-check
pc = get("/precheck/software/Microsoft.VisualStudioCode").json()
check("pre-check lists Code.exe (VS Code is open)", "Code.exe" in pc["blocking_processes"], pc)
if "Code.exe" in pc["blocking_processes"]:
    r = post("/actions/install_software", {"package_id": "Microsoft.VisualStudioCode", "ticket_id": "TEST0000001",
                                           "authorization_id": "TEST-AUTH"}).json()
    check("install refused while the app runs (BLOCKED, nothing changed)",
          r["status"] == "BLOCKED" and r["rollback"] is None, r.get("blocking_processes"))

# ---- 3.6 rollback restores the before-state
d = "kaggle.com"
a = post("/actions/allowlist_domain", {"domain": d, "ticket_id": "TEST0000002", "authorization_id": "TEST-A"}).json()
b = post("/actions/allowlist_domain", {"domain": d, "ticket_id": "TEST0000003", "authorization_id": "TEST-B"}).json()
check("grant A then grant B (B finds the domain already allowed)",
      allowed(d) and b["rollback"]["params"]["allowed"] is True, b["rollback"])
post("/actions/rollback", {"action_id": b["action_id"], "ticket_id": "TEST0000003"})
check("rollback of B keeps A's access (before-state restored)", allowed(d))
post("/actions/rollback", {"action_id": a["action_id"], "ticket_id": "TEST0000002"})
check("rollback of A removes the access", not allowed(d))
r = post("/actions/rollback", {"action_id": a["action_id"], "ticket_id": "TEST0000002"})
check("second rollback of A refused (409)", r.status_code == 409, r.status_code)

# ---- 3.3 time-limited grants
past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
r = post("/actions/allowlist_domain", {"domain": d, "ticket_id": "TEST0000004", "authorization_id": "TEST-T",
                                       "expires_at": past})
check("expires_at in the past refused (400)", r.status_code == 400, r.status_code)
fut = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
g = post("/actions/allowlist_domain", {"domain": d, "ticket_id": "TEST0000005", "authorization_id": "TEST-T",
                                       "expires_at": fut}).json()
listed = {x["action_id"]: x for x in get("/grants").json()["grants"]}
check("time-limited grant listed in /grants, not due yet", g["action_id"] in listed and
      not listed[g["action_id"]]["due"], listed.get(g["action_id"]))
post("/actions/rollback", {"action_id": g["action_id"], "ticket_id": "TEST0000005"})
check("rolled back grant no longer listed", g["action_id"] not in {x["action_id"] for x in get("/grants").json()["grants"]})

# ---- negative
r = post("/actions/allowlist_domain", {"domain": "example.org", "ticket_id": "TEST0000006", "authorization_id": "TEST-T"})
check("unmanaged domain refused (403)", r.status_code == 403, r.status_code)
r = get("/status", headers={"X-Endpoint-Token": "wrong"})
check("wrong token refused (401)", r.status_code == 401, r.status_code)

# ---- optional: async install + uninstall rollback (3.7 / 3.6)
if sys.argv[1:] == ["install"]:
    pkg = "7zip.7zip"
    before = get(f"/verify/software/{pkg}").json()["installed"]
    job = post("/actions/install_software", {"package_id": pkg, "ticket_id": "TEST0000007",
                                             "authorization_id": "TEST-INSTALL"}).json()
    check("install answers at once with a RUNNING job", job["status"] in ("RUNNING", "SUCCEEDED"), job["status"])
    t0 = time.time()
    while job["status"] == "RUNNING" and time.time() - t0 < 900:
        time.sleep(5)
        job = get(f"/jobs/{job['action_id']}").json()
    check(f"job finished in {time.time() - t0:.0f}s", job["status"] == "SUCCEEDED", job.get("message", "")[-120:])
    check("verify: installed (machine scope)", get(f"/verify/software/{pkg}").json()["installed"])
    if job.get("rollback"):
        r = requests.post(BASE + "/actions/rollback", headers=H, timeout=1500,
                          json={"action_id": job["action_id"], "ticket_id": "TEST0000007"}).json()
        check("rollback = uninstall --scope machine", r["status"] == "SUCCEEDED", r.get("message", "")[-120:])
        check("verify: not installed any more", not get(f"/verify/software/{pkg}").json()["installed"])
    else:
        print(f"[INFO] {pkg} was already installed before (installed={before}): no rollback, nothing removed")

end = get("/status").json()
check("allowlist back to the start state", end["allowlist"] == start_allow, end["allowlist"])
print("RESULT:", "OK" if OK else "FAILED")
sys.exit(0 if OK else 1)
