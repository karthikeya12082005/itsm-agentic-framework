"""Statistics over logs/llm_calls.jsonl (one line per agent LLM call, written by llm_router): per-agent usage
(answered by the primary / secondary model or fell back to rules) and per provider:model latency and failures.
Used by evaluation/ablations.py (5.1) and the dashboard (5.2)."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict

from src.common.llm_router import LOG_FILE


def log_lines() -> int:
    if not LOG_FILE.exists():
        return 0
    with open(LOG_FILE, encoding="utf-8") as f:
        return sum(1 for _ in f)


def log_records(start: int = 0, end: int | None = None) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    out = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= start and (end is None or i < end):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def llm_usage(records: list[dict]) -> dict:
    """Per agent: calls answered by an LLM (primary / secondary) vs. rule-based / no answer. Calls made with the
    LLM switched off (rules variants: no attempts at all) are not counted."""
    per = defaultdict(Counter)
    for r in records:
        if not r.get("attempts"):
            continue
        a = per[r.get("agent")]
        a["calls"] += 1
        if r.get("method") == "llm":
            a["llm_primary" if r.get("fallback") == "none" else "llm_secondary"] += 1
        else:
            a["fallback_rules"] += 1
    tot = Counter()
    for c in per.values():
        tot.update(c)
    out = {k: dict(v) for k, v in sorted(per.items())}
    out["_all"] = dict(tot)
    out["_answered_share"] = round((tot["llm_primary"] + tot["llm_secondary"]) / tot["calls"], 3) if tot["calls"] else None
    return out


def _error_class(err: str) -> str:
    e = err.lower()
    for key, label in (("skipped", "skipped_cooldown"), ("not configured", "not_configured"), ("429", "http_429"),
                       ("503", "http_503"), ("500", "http_5xx"), ("502", "http_5xx"), ("timeout", "timeout"),
                       ("404", "http_404"), ("json", "invalid_output"), ("schema", "invalid_output"),
                       ("validation", "invalid_output")):
        if key in e:
            return label
    return "other"


def provider_latency(records: list[dict]) -> dict:
    """Per provider:model over every ATTEMPT (not only the final answer): answered calls, latency of the answers
    (median / p90 / mean), failure classes. Skipped attempts (cooldown) are counted but have no latency."""
    per = defaultdict(lambda: {"attempts": 0, "answered": 0, "lat": [], "errors": Counter()})
    for r in records:
        for a in r.get("attempts") or []:
            s = per[a.get("spec") or "?"]
            s["attempts"] += 1
            if a.get("error"):
                s["errors"][_error_class(str(a["error"]))] += 1
            else:
                s["answered"] += 1
                if a.get("latency_s") is not None:
                    s["lat"].append(float(a["latency_s"]))
    out = {}
    for spec, s in sorted(per.items()):
        lat = sorted(s["lat"])
        called = s["attempts"] - s["errors"]["skipped_cooldown"] - s["errors"]["not_configured"]
        out[spec] = {"attempts": s["attempts"], "called": called, "answered": s["answered"],
                     "success_rate_of_called": round(s["answered"] / called, 3) if called else None,
                     "latency_median_s": round(statistics.median(lat), 2) if lat else None,
                     "latency_p90_s": round(lat[min(len(lat) - 1, int(0.9 * len(lat)))], 2) if lat else None,
                     "latency_mean_s": round(statistics.mean(lat), 2) if lat else None,
                     "errors": dict(s["errors"])}
    return out
