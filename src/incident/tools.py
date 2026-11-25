"""Read-only diagnostic tools of the RCA Agent (ReAct actions). They observe the lab; they never change it.
Each tool returns a small JSON-able dict (the observation) - kept short for the LLM prompt."""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.lab import config as C


def _get(service, path, timeout=4):
    r = requests.get(C.url(service, path), timeout=timeout)
    r.raise_for_status()
    return r.json()


def process_status(service: str) -> dict:
    try:
        m = _get(service, "/metrics")
        return {"service": service, "up": True, "uptime_s": m["uptime_s"], "requests": m["requests"]}
    except (requests.RequestException, ValueError):
        return {"service": service, "up": False, "note": "no answer on its port (process not running)"}


def check_health(service: str) -> dict:
    try:
        h = _get(service, "/health")
        return {"service": service, "up": True, "status": h["status"],
                "failing": [k for k, v in h["checks"].items() if not v]}
    except (requests.RequestException, ValueError):
        return {"service": service, "up": False, "status": "down", "failing": ["not responding"]}


def read_metrics(service: str) -> dict:
    try:
        m = _get(service, "/metrics")
        return {k: m[k] for k in ("service", "latency_ms", "error_rate", "memory_mb", "disk_used_pct", "cache_items",
                                  "pool_in_use", "pool_size", "cert_days_left", "release", "uptime_s") if k in m}
    except (requests.RequestException, ValueError):
        return {"service": service, "up": False}


def read_logs(service: str, n: int = 25) -> dict:
    """Last log lines (from the file: also works when the process is down)."""
    try:
        lines = (C.service_dir(service) / "service.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    tail = [ln[24:][:200] for ln in lines[-n:]]                 # drop the timestamp
    seen, uniq = set(), []
    for ln in reversed(tail):                                    # newest first, repeated lines once
        key = ln.split(" ", 2)[-1][:80]
        if key not in seen:
            seen.add(key)
            uniq.append(ln)
    return {"service": service, "lines": uniq[:8]}


def recent_deployments(service: str) -> dict:
    dep = C.read_json(C.service_dir(service) / "deploy.json", {})
    hist = dep.get("history", [])
    last = hist[-1] if hist else {}
    age_min = None
    try:
        age_min = round((datetime.now(timezone.utc) - datetime.fromisoformat(last["time"])).total_seconds() / 60, 1)
    except (KeyError, ValueError, TypeError):
        pass
    return {"service": service, "current_release": dep.get("version"), "releases": [h["version"] for h in hist],
            "last_deploy_minutes_ago": age_min}


def check_certificate(service: str) -> dict:
    cert = C.read_json(C.service_dir(service) / "cert.json", {})
    try:
        days = (datetime.fromisoformat(cert["not_after"]) - datetime.now(timezone.utc)).total_seconds() / 86400
    except (KeyError, ValueError):
        days = None
    return {"service": service, "not_after": cert.get("not_after"), "days_left": round(days, 2) if days is not None else None}


def check_disk(service: str) -> dict:
    spool = C.service_dir(service) / "spool"
    files = [f for f in spool.glob("*") if f.is_file()]
    used = sum(f.stat().st_size for f in files)
    return {"service": service, "files": len(files), "used_mb": round(used / 1048576, 1),
            "quota_mb": C.SPOOL_QUOTA_MB, "used_pct": round(100 * used / (C.SPOOL_QUOTA_MB * 1048576), 1)}


def check_db_pool(service: str) -> dict:
    m = read_metrics(service)
    return {"service": service, "pool_in_use": m.get("pool_in_use"), "pool_size": m.get("pool_size")}


def check_dependencies(service: str) -> dict:
    return {"service": service, "dependencies": {d: check_health(d)["status"] for d in C.SERVICES[service]["deps"]}}


TOOLS = {"process_status": process_status, "check_health": check_health, "read_metrics": read_metrics,
         "read_logs": read_logs, "recent_deployments": recent_deployments, "check_certificate": check_certificate,
         "check_disk": check_disk, "check_db_pool": check_db_pool, "check_dependencies": check_dependencies}
TOOL_HELP = {
    "process_status": "is the service's process running (uptime)",
    "check_health": "health status and failing checks",
    "read_metrics": "latency, error rate, memory, disk, cache size, DB pool, certificate days, release",
    "read_logs": "newest distinct log lines (errors, crashes, deployments)",
    "recent_deployments": "release history and minutes since the last deployment",
    "check_certificate": "TLS certificate expiry",
    "check_disk": "spool volume usage",
    "check_db_pool": "database connection pool usage",
    "check_dependencies": "health of the services it depends on",
}


def run(tool: str, service: str) -> dict:
    if tool not in TOOLS:
        return {"error": f"unknown tool {tool}"}
    if service not in C.SERVICES:
        return {"error": f"unknown service {service}"}
    try:
        return TOOLS[tool](service)
    except Exception as e:  # noqa: BLE001 - a diagnostic must never crash the agent
        return {"service": service, "error": f"{type(e).__name__}: {e}"[:200]}
