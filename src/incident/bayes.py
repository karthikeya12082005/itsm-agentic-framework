"""Bayesian root-cause ranking (report algorithm; TODO 4.4).

    P(rc | e, h) ∝ P(rc) · Π_f P(e_f | rc) · P(h | rc)

  rc  root-cause category of the dataset (historical_incidents.root_cause_category, 18 classes)
  P(rc)          prior: P(rc | dataset category) from the TRAIN split (Laplace smoothing), optionally blended with
                 the root causes of the k most similar past incidents found by hybrid retrieval (k-NN)
  P(e_f | rc)    likelihood of each observed binary evidence feature (present or absent) - expert table below
                 (LIKELIHOOD: P(feature present | rc); unlisted pairs use BASE_RATE)
  P(h | rc)      the LLM's top hypothesis h as a NOISY observation: α if h == rc, else (1-α)/(K-1)
Absent features count too (P(absent | rc) = 1 - P(present | rc)), so "no certificate problem" lowers
CERTIFICATE_EXPIRY. The result is a full posterior; the agents use top-1 for remediation and report top-3.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from functools import lru_cache

from src.common.config import PROCESSED_DIR

BASE_RATE = 0.05                 # P(feature present | rc) when the table says nothing
LLM_ALPHA = 0.6                  # assumed accuracy of the LLM's top hypothesis
KNN_WEIGHT = 0.3                 # prior = (1 - w) * category prior + w * k-NN distribution

# P(feature present | root cause). Features come from src/incident/evidence.py.
LIKELIHOOD = {
    "service_down":        {"SOFTWARE_DEFECT": 0.75, "RESOURCE_EXHAUSTION": 0.35, "HARDWARE_FAULT": 0.6,
                            "DEPENDENCY_FAILURE": 0.5},
    "crash_in_logs":       {"SOFTWARE_DEFECT": 0.8, "RESOURCE_EXHAUSTION": 0.3, "DEPENDENCY_FAILURE": 0.4,
                            "HARDWARE_FAULT": 0.3},
    "dependency_down":     {"DEPENDENCY_FAILURE": 0.9, "NETWORK_FAILURE": 0.4},
    "upstream_errors":     {"DEPENDENCY_FAILURE": 0.85, "DEPLOYMENT_CHANGE": 0.6, "CONFIGURATION_ERROR": 0.6,
                            "NETWORK_FAILURE": 0.5, "SOFTWARE_DEFECT": 0.3},
    "high_memory":         {"RESOURCE_EXHAUSTION": 0.85, "SOFTWARE_DEFECT": 0.2, "CAPACITY_LIMIT": 0.2},
    "oom_in_logs":         {"RESOURCE_EXHAUSTION": 0.7, "SOFTWARE_DEFECT": 0.15},
    "high_latency":        {"RESOURCE_EXHAUSTION": 0.7, "CAPACITY_LIMIT": 0.5, "NETWORK_FAILURE": 0.4,
                            "DEPENDENCY_FAILURE": 0.3},
    "cache_bloated":       {"RESOURCE_EXHAUSTION": 0.7, "CONFIGURATION_ERROR": 0.15},
    "disk_full":           {"CAPACITY_LIMIT": 0.85, "RESOURCE_EXHAUSTION": 0.3, "CONFIGURATION_ERROR": 0.1},
    "enospc_in_logs":      {"CAPACITY_LIMIT": 0.8, "RESOURCE_EXHAUSTION": 0.25},
    "recent_deployment":   {"DEPLOYMENT_CHANGE": 0.9, "CONFIGURATION_ERROR": 0.35, "VERSION_CONFLICT": 0.5},
    "config_error_in_logs": {"CONFIGURATION_ERROR": 0.6, "DEPLOYMENT_CHANGE": 0.75, "VERSION_CONFLICT": 0.3},
    "cert_expired":        {"CERTIFICATE_EXPIRY": 0.95},
    "tls_error_in_logs":   {"CERTIFICATE_EXPIRY": 0.9, "CONFIGURATION_ERROR": 0.1},
    "pool_exhausted":      {"CONFIGURATION_ERROR": 0.7, "RESOURCE_EXHAUSTION": 0.4, "CAPACITY_LIMIT": 0.3,
                            "DEPLOYMENT_CHANGE": 0.2},
    "pool_timeout_in_logs": {"CONFIGURATION_ERROR": 0.6, "RESOURCE_EXHAUSTION": 0.35, "CAPACITY_LIMIT": 0.3},
    "session_leak_in_logs": {"CONFIGURATION_ERROR": 0.6, "SOFTWARE_DEFECT": 0.3},
    "high_error_rate":     {"SOFTWARE_DEFECT": 0.6, "DEPLOYMENT_CHANGE": 0.8, "CONFIGURATION_ERROR": 0.7,
                            "DEPENDENCY_FAILURE": 0.8, "CERTIFICATE_EXPIRY": 0.8, "CAPACITY_LIMIT": 0.3,
                            "EXPIRED_CREDENTIAL": 0.7, "NETWORK_FAILURE": 0.6},
}

# ServiceNow incident.category -> dataset category used for the prior
SN_TO_DATASET = {"internal_applications": "APPLICATION", "software": "SOFTWARE", "performance": "APPLICATION",
                 "hardware": "INFRASTRUCTURE", "database": "DATABASE", "network": "NETWORK",
                 "security": "SECURITY", "access": "IDENTITY_ACCESS"}


@lru_cache(maxsize=1)
def _train_incidents() -> list[dict]:
    rows = []
    with open(PROCESSED_DIR / "incidents.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["split"] == "train" and r["record"]["ticket_type"] == "INCIDENT":
                rows.append(r["record"])
    return rows


@lru_cache(maxsize=1)
def classes() -> tuple[str, ...]:
    return tuple(sorted({t["root_cause_category"] for t in _train_incidents()}))


def category_prior(dataset_category: str | None) -> dict[str, float]:
    """P(rc | category) from the train split, Laplace-smoothed (whole split if the category is unknown)."""
    rows = [t for t in _train_incidents() if t["category"] == dataset_category] or _train_incidents()
    c = Counter(t["root_cause_category"] for t in rows)
    total = sum(c.values()) + len(classes())
    return {rc: (c[rc] + 1) / total for rc in classes()}


def knn_distribution(root_causes: list[str]) -> dict[str, float] | None:
    if not root_causes:
        return None
    c = Counter(rc for rc in root_causes if rc in classes())
    total = sum(c.values()) + len(classes())
    return {rc: (c[rc] + 1) / total for rc in classes()}


# ---------------------------------------------------------------- text evidence (dataset tickets, item 4.6)
# Keyword features of an incident's text AT CREATION TIME (title, description, symptoms, observed state,
# telemetry, diagnostic data - never root_cause / hypothesis fields). Their likelihoods are LEARNED from the
# train split (learned_likelihoods), not set by hand.
TEXT_FEATURES = {
    "t_certificate": r"certificat|\btls\b|\bssl\b|handshake|x\.?509",
    "t_disk": r"\bdisk\b|storage|no space|volume (?:full|usage)|inode",
    "t_memory": r"\bmemory\b|\boom\b|out of memory|\bheap\b|swap",
    "t_cpu": r"\bcpu\b|saturat|load average",
    "t_latency": r"latency|slow|timeout|timed out|response time",
    "t_deploy": r"deploy|release|rollout|upgrade|new version|change window|patch",
    "t_config": r"config|misconfigur|setting|parameter|\bvalue\b",
    "t_pool": r"connection pool|\bpool\b|max(?:imum)? connections|sessions",
    "t_unavailable": r"\bdown\b|unavailable|unreachable|\b5\d\d\b|crash|outage|not responding",
    "t_dependency": r"dependen|upstream|downstream|third[- ]party|\bapi\b",
    "t_credential": r"password|credential|expired account|token|secret|\bkey\b|authenticat",
    "t_dns": r"\bdns\b|resolv|hostname|nslookup",
    "t_firewall": r"firewall|\bport\b|blocked|\bacl\b",
    "t_network": r"packet|network|\bvpn\b|routing|connectivity|bandwidth",
    "t_permission": r"permission|access denied|entitle|licen[cs]e|group membership|not authori",
    "t_security": r"malware|phish|suspicious|attack|breach|intrusion|security alert",
    "t_user": r"user error|mistyp|wrong (?:password|url)|did not follow|training",
    "t_hardware": r"hardware|\bfan\b|battery|\bdisk failure\b|motherboard|overheat",
    "t_version": r"version|incompatib|conflict|dependency hell|library",
    "t_capacity": r"capacity|quota|limit reached|throttl|max(?:imum)? (?:users|size)",
}
TEXT_FIELDS = ("title", "description", "symptoms", "observed_state", "telemetry_type", "diagnostic_data")


def text_features(record: dict) -> dict[str, bool]:
    import re
    text = " ".join(str(record.get(k) or "") for k in TEXT_FIELDS)
    return {f: bool(re.search(rx, text, re.I)) for f, rx in TEXT_FEATURES.items()}


@lru_cache(maxsize=1)
def learned_likelihoods() -> dict[str, dict[str, float]]:
    """P(text feature present | rc) from the TRAIN split with Laplace smoothing."""
    rows = _train_incidents()
    out = {f: {} for f in TEXT_FEATURES}
    for rc in classes():
        sub = [text_features(t) for t in rows if t["root_cause_category"] == rc]
        for f in TEXT_FEATURES:
            out[f][rc] = (sum(x[f] for x in sub) + 1) / (len(sub) + 2)
    return out


def rank(features: dict[str, bool], dataset_category: str | None = None, knn_root_causes: list[str] | None = None,
         llm_hypothesis: str | None = None, alpha: float = LLM_ALPHA,
         likelihood: dict | None = None) -> list[tuple[str, float]]:
    """Posterior over root causes, highest first. Log-space for numerical stability.
    likelihood: feature table to use (default: the lab's expert table LIKELIHOOD)."""
    table = likelihood or LIKELIHOOD
    prior = category_prior(dataset_category)
    knn = knn_distribution(knn_root_causes or [])
    if knn:
        prior = {rc: (1 - KNN_WEIGHT) * prior[rc] + KNN_WEIGHT * knn[rc] for rc in prior}
    k = len(classes())
    logp = {}
    for rc in classes():
        lp = math.log(prior[rc])
        for f, present in features.items():
            if f not in table:
                continue
            p = table[f].get(rc, BASE_RATE)
            lp += math.log(p if present else 1 - p)
        if llm_hypothesis in classes():
            lp += math.log(alpha if rc == llm_hypothesis else (1 - alpha) / (k - 1))
        logp[rc] = lp
    m = max(logp.values())
    z = sum(math.exp(v - m) for v in logp.values())
    post = {rc: math.exp(v - m) / z for rc, v in logp.items()}
    return sorted(post.items(), key=lambda kv: -kv[1])
