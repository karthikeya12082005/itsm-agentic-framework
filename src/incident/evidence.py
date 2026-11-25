"""Turn RCA tool observations into (root service, binary evidence features) for the Bayesian ranking."""
from __future__ import annotations

import re

from src.lab import config as C

DEPTH = {"cache": 0, "orders-api": 1, "worker": 1, "web": 2}     # deeper = closer to the root of a chain
VICTIM_ONLY = {"errors", "latency"}
LOG_PATTERNS = {
    "crash_in_logs": r"unhandled exception|exited with code|terminated unexpectedly|access violation|FATAL",
    "oom_in_logs": r"OutOfMemory|heap (?:usage|exhausted)",
    "enospc_in_logs": r"No space left|spool volume usage",
    "config_error_in_logs": r"ValueError|KeyError|invalid literal|invalid config|deployed release",
    "tls_error_in_logs": r"TLS|certificate expired",
    "pool_timeout_in_logs": r"pool exhausted|acquiring DB connection",
    "session_leak_in_logs": r"not returned to pool",
}


def locate_root(health: dict[str, dict]) -> str | None:
    """health: service -> check_health() observation. Returns the root service of the event, or None."""
    failing = {s: h for s, h in health.items() if h.get("status") != "ok"}
    if not failing:
        return None
    down = [s for s, h in failing.items() if not h.get("up")]
    if down:
        return max(down, key=lambda s: DEPTH.get(s, 0))
    roots = []
    for s, h in failing.items():
        own = {c for c in h.get("failing", []) if not c.startswith("dependency:")}
        deps_failing = any(d in failing for d in C.SERVICES[s]["deps"])
        if deps_failing and own <= VICTIM_ONLY:
            continue                                   # symptoms explained by a failing dependency
        roots.append(s)
    pool = roots or list(failing)
    return max(pool, key=lambda s: (DEPTH.get(s, 0), len(failing[s].get("failing", []))))


def features(obs: dict, root: str, affected: str | None) -> dict[str, bool]:
    """obs: {(tool, service): observation}. Features describe the ROOT service (plus how it affects the entry)."""
    get = lambda tool, svc=root: obs.get((tool, svc)) or {}          # noqa: E731
    m, ps, dep, cert, disk = get("read_metrics"), get("process_status"), get("recent_deployments"), \
        get("check_certificate"), get("check_disk")
    logs = " \n".join(get("read_logs").get("lines", []))
    up = ps.get("up", get("check_health").get("up", True))
    aff_health = get("check_health", affected) if affected else {}
    f = {
        "service_down": not up,
        "dependency_down": (not up) and affected not in (None, root) and root in C.SERVICES.get(affected, {}).get("deps", []),
        "upstream_errors": affected not in (None, root) and "errors" in aff_health.get("failing", []),
        "high_memory": (m.get("memory_mb") or 0) > C.MEMORY_MB_MAX,
        "high_latency": (m.get("latency_ms") or 0) > C.LATENCY_MS_MAX,
        "cache_bloated": (m.get("cache_items") or 0) > 5000,
        "disk_full": (disk.get("used_pct") if disk else m.get("disk_used_pct") or 0) > C.DISK_PCT_MAX,
        "recent_deployment": len(dep.get("releases") or []) > 1 and (dep.get("last_deploy_minutes_ago") or 1e9) < 240,
        "cert_expired": cert.get("days_left") is not None and cert["days_left"] < 0,
        "pool_exhausted": (m.get("pool_size") or 0) > 0 and (m.get("pool_in_use") or 0) >= m.get("pool_size"),
        "high_error_rate": (m.get("error_rate") or 0) > C.ERROR_RATE_MAX,
    }
    for name, rx in LOG_PATTERNS.items():
        f[name] = bool(re.search(rx, logs, re.I))
    # naive Bayes assumes independent features: an out-of-memory exit is evidence for the OOM feature, not ALSO
    # for a generic crash (found live: one "FATAL: OutOfMemoryError ... exiting" line set both)
    other = "\n".join(ln for ln in logs.splitlines() if not re.search(LOG_PATTERNS["oom_in_logs"], ln, re.I))
    f["crash_in_logs"] = bool(re.search(LOG_PATTERNS["crash_in_logs"], other, re.I))
    return f
