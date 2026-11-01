"""Client for the endpoint enforcement service (runs as admin on 127.0.0.1:8765)."""
import os

import requests

from src.common import config  # noqa: F401  (loads .env)

BASE = os.environ.get("ENDPOINT_URL", "http://127.0.0.1:8765")


class EndpointError(RuntimeError):
    pass


def _call(method, path, **kw):
    headers = {"X-Endpoint-Token": os.environ.get("ENDPOINT_TOKEN", "")}
    try:
        r = requests.request(method, BASE + path, headers=headers, timeout=1500, **kw)
    except requests.ConnectionError as e:
        raise EndpointError("endpoint service not reachable - start it in the Administrator terminal") from e
    if r.status_code >= 400:
        raise EndpointError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def execute(kind, target, ticket_id, authorization_id):
    if kind == "domain":
        return _call("POST", "/actions/allowlist_domain",
                     json={"domain": target, "ticket_id": ticket_id, "authorization_id": authorization_id})
    return _call("POST", "/actions/install_software",
                 json={"package_id": target, "ticket_id": ticket_id, "authorization_id": authorization_id})


def verify(kind, target):
    if kind == "domain":
        return bool(_call("GET", f"/verify/domain/{target}")["allowed"])
    return bool(_call("GET", f"/verify/software/{target}")["installed"])
