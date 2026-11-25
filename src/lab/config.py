"""Lab configuration: services, ports, dependencies, health thresholds and the fault catalogue (ground truth).

Topology (an online-shop backend):   web  ->  orders-api  ->  (SQLite DB with a connection pool)
                                      |            \\-> cache
                                      \\-> cache
                                      worker (order queue consumer, writes a spool directory)
"""
from __future__ import annotations

import json
import secrets

from src.common.config import PROJECT_ROOT

LAB_DIR = PROJECT_ROOT / "data" / "lab"                # gitignored (data/*)
HOST = "127.0.0.1"
SERVICES = {
    "web":        {"port": 9101, "deps": ["orders-api", "cache"], "kind": "web frontend"},
    "orders-api": {"port": 9102, "deps": ["cache"], "kind": "REST API with a database connection pool"},
    "cache":      {"port": 9103, "deps": [], "kind": "in-memory cache"},
    "worker":     {"port": 9104, "deps": [], "kind": "queue worker with a spool directory"},
}

# Health thresholds (monitoring and the verification predicates use the same numbers)
LATENCY_MS_MAX = 500
MEMORY_MB_MAX = 400
DISK_PCT_MAX = 90
ERROR_RATE_MAX = 0.2
POOL_FREE_MIN = 1
CERT_DAYS_MIN = 1
SPOOL_QUOTA_MB = 20

# Fault catalogue: what the injector does, and the ground truth used ONLY by the evaluation (never by agents).
# root_cause = dataset root_cause_category; sop = dataset SOP; dataset_category = dataset ticket category;
# sn_category = ServiceNow incident.category the monitoring uses.
FAULTS = {
    "service_down":      {"service": "web", "root_cause": "SOFTWARE_DEFECT", "sop": "SOP-0049",
                          "action": "RESTART_SERVICE", "dataset_category": "INFRASTRUCTURE",
                          "sn_category": "internal_applications",
                          "describe": "web process crashed (unhandled exception) and is not running"},
    "memory_leak":       {"service": "orders-api", "root_cause": "RESOURCE_EXHAUSTION", "sop": "SOP-0046",
                          "action": "RESTART_SERVICE", "dataset_category": "APPLICATION",
                          "sn_category": "internal_applications",
                          "describe": "orders-api leaks memory on every request until it crash-loops"},
    "high_latency":      {"service": "cache", "root_cause": "RESOURCE_EXHAUSTION", "sop": "SOP-0041",
                          "action": "CLEAR_CACHE", "dataset_category": "APPLICATION", "sn_category": "performance",
                          "describe": "cache is bloated with stale entries; lookups become slow"},
    "disk_full":         {"service": "worker", "root_cause": "CAPACITY_LIMIT", "sop": "SOP-0048",
                          "action": "FREE_DISK_SPACE", "dataset_category": "INFRASTRUCTURE",
                          "sn_category": "hardware", "describe": "worker spool volume is full of old files"},
    "bad_config":        {"service": "orders-api", "root_cause": "DEPLOYMENT_CHANGE", "sop": "SOP-0043",
                          "action": "ROLLBACK_DEPLOYMENT", "dataset_category": "APPLICATION",
                          "sn_category": "internal_applications",
                          "describe": "a new orders-api release with a broken configuration was deployed"},
    "cert_expired":      {"service": "web", "root_cause": "CERTIFICATE_EXPIRY", "sop": "SOP-0044",
                          "action": "RENEW_CERTIFICATE", "dataset_category": "APPLICATION",
                          "sn_category": "internal_applications", "describe": "web TLS certificate expired"},
    "db_pool_exhausted": {"service": "orders-api", "root_cause": "CONFIGURATION_ERROR", "sop": "SOP-0052",
                          "action": "TERMINATE_DB_SESSIONS", "dataset_category": "DATABASE",
                          "sn_category": "database",
                          "describe": "orders-api leaked database sessions; the connection pool is exhausted"},
    "dependency_down":   {"service": "orders-api", "root_cause": "DEPENDENCY_FAILURE", "sop": "SOP-0042",
                          "action": "RESTART_SERVICE", "dataset_category": "APPLICATION",
                          "sn_category": "internal_applications",
                          "describe": "orders-api is down; web (which depends on it) returns 5xx"},
}


def service_dir(name: str):
    d = LAB_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def lab_token() -> str:
    """Random token protecting the lab's admin endpoints (created on first use, data/lab/token, gitignored)."""
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    p = LAB_DIR / "token"
    if not p.exists():
        p.write_text(secrets.token_hex(20), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def url(name: str, path: str = "") -> str:
    return f"http://{HOST}:{SERVICES[name]['port']}{path}"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
