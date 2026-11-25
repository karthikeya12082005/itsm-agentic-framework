"""Client for the endpoint enforcement service (runs as admin on 127.0.0.1:8765).

    python -m src.access.endpoint_client status              current allowlist / blocklist / download policy
    python -m src.access.endpoint_client rollback <action_id> <ticket>   undo one audited action (rollback spec)
"""
import json
import os
import sys

import requests

from src.common import config  # noqa: F401  (loads .env)

BASE = os.environ.get("ENDPOINT_URL", "http://127.0.0.1:8765")


class EndpointError(RuntimeError):
    pass


def _call(method, path, timeout=120, **kw):
    """Installs run as background jobs on the service (3.7), so no call needs the old 1500 s timeout."""
    headers = {"X-Endpoint-Token": os.environ.get("ENDPOINT_TOKEN", "")}
    try:
        r = requests.request(method, BASE + path, headers=headers, timeout=timeout, **kw)
    except requests.ConnectionError as e:
        raise EndpointError("endpoint service not reachable - start it in the Administrator terminal") from e
    except requests.Timeout as e:
        raise EndpointError(f"endpoint service timed out after {timeout}s") from e
    if r.status_code >= 400:
        raise EndpointError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def execute(kind, target, ticket_id, authorization_id, expires_at=None):
    """Domain: synchronous, status SUCCEEDED. Software (service v2): status RUNNING + action_id (poll job()),
    or BLOCKED + blocking_processes when the app is open (items 3.1 / 3.7)."""
    if kind == "domain":
        body = {"domain": target, "ticket_id": ticket_id, "authorization_id": authorization_id}
        if expires_at:
            body["expires_at"] = expires_at
        return _call("POST", "/actions/allowlist_domain", json=body)
    body = {"package_id": target, "ticket_id": ticket_id, "authorization_id": authorization_id}
    if expires_at:
        body["expires_at"] = expires_at
    return _call("POST", "/actions/install_software", json=body)


def precheck(package_id) -> list:
    """Running processes that would make the installer cancel (e.g. ['Code.exe'])."""
    return list(_call("GET", f"/precheck/software/{package_id}", timeout=60).get("blocking_processes", []))


def job(action_id) -> dict:
    """Install job: status RUNNING | SUCCEEDED | FAILED. EndpointError (404) = unknown (service restarted)."""
    return _call("GET", f"/jobs/{action_id}", timeout=30)


def grants() -> list:
    """Active time-limited grants (item 3.3): action_id, ticket_id, params, expires_at, due."""
    return _call("GET", "/grants", timeout=30)["grants"]


def verify(kind, target):
    if kind == "domain":
        return bool(_call("GET", f"/verify/domain/{target}")["allowed"])
    return bool(_call("GET", f"/verify/software/{target}")["installed"])


def status():
    return _call("GET", "/status")


def rollback(action_id, ticket_id):
    # an UNINSTALL rollback runs winget synchronously on the service (rare: manual or expiry)
    return _call("POST", "/actions/rollback", json={"action_id": action_id, "ticket_id": ticket_id}, timeout=1500)


if __name__ == "__main__":
    if sys.argv[1:2] == ["status"]:
        print(json.dumps(status(), indent=1))
    elif sys.argv[1:2] == ["rollback"] and len(sys.argv) == 4:
        print(json.dumps(rollback(sys.argv[2], sys.argv[3]), indent=1))
    else:
        print(__doc__)
