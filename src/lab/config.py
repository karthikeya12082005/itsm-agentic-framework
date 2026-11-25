"""Lab configuration: services, ports, dependencies, health thresholds and the fault catalogue (ground truth).

Topology (an online-shop backend):   web  ->  orders-api  ->  (SQLite DB with a connection pool)
                                      |            \\-> cache
                                      \\-> cache
                                      worker (order queue consumer, writes a spool directory)
Remote employees reach the web app through the VPN gateway (6.1):  vpn-gateway -> web
  vpn-gateway = IPsec tunnel to the data centre + internal DNS resolver + per-user VPN profiles (credentials).
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
    "vpn-gateway": {"port": 9105, "deps": ["web"],
                    "kind": "remote-access VPN gateway (IPsec tunnel, internal DNS resolver, VPN profiles)"},
}
START_ORDER = ("cache", "orders-api", "worker", "web", "vpn-gateway")      # dependencies first
INTERNAL_HOST = "shop.intranet.lab"            # the name remote users resolve through the gateway's DNS
INTERNAL_ADDR = "10.20.0.15"                   # its correct address (authoritative zone record)

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
    # ---- network / VPN (6.1)
    # expected_final: the correct outcome when MONITORING raises it - POL-0004's eligible roles exclude the on-call
    # SRE role, so the rule engine denies the automatic restart and the incident goes to the Service Desk (an
    # employee-reported VPN outage is fixed automatically, tests/test_incident.py). Default expectation: RESOLVED.
    "vpn_tunnel_down":   {"service": "vpn-gateway", "root_cause": "NETWORK_FAILURE", "sop": "SOP-0004",
                          "action": "RESTART_NETWORK_DEVICE", "dataset_category": "VPN", "sn_category": "network",
                          "expected_final": "ESCALATED",
                          "describe": "the gateway's IPsec tunnel to the data centre dropped (dead-peer detection)"},
    "dns_failure":       {"service": "vpn-gateway", "root_cause": "DNS_MISCONFIGURATION", "sop": "SOP-0001",
                          "action": "UPDATE_DNS_RECORD", "dataset_category": "NETWORK", "sn_category": "network",
                          "describe": "the gateway's DNS resolver serves a stale record for the internal shop name"},
    "vpn_credential_expired": {"service": "vpn-gateway", "root_cause": "EXPIRED_CREDENTIAL", "sop": "SOP-0010",
                          "action": "PROVISION_VPN_PROFILE", "dataset_category": "VPN", "sn_category": "network",
                          "describe": "the VPN profile (client credential) used by remote employees expired"},
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
