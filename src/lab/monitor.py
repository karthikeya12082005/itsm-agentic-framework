"""Monitoring (TODO 4.3): synthetic transactions + health checks of the lab; problems become INCIDENTS in the
ticket system (ServiceNow by default) through the TicketSource interface - never the access category.

    python -m src.lab.monitor --once                 # one check (creates at most one incident per event)
    python -m src.lab.monitor --interval 15          # keep watching (Ctrl+C stops)
    options: --source servicenow|local

Correlation: one incident per independent event (dependency-related failures belong together), opened on the CUSTOMER-FACING impacted service (web or worker) and listing
every failing service with its checks, metrics and the tail of its log. Finding the ROOT service and cause is
the RCA Agent's job. The category comes from the failing checks only (the monitor never reads the fault
ground truth): disk -> hardware, db_pool -> database, only latency -> performance, else internal_applications.
De-duplication: while an incident for the same entry service is open, no new one is created
(data/lab/monitor_state.json).
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import requests

from src.lab import config as C

STATE = "monitor_state.json"
ENTRY = ["web", "worker"]                         # customer-facing services
MARKER = "[lab-monitor]"


def probe() -> None:
    """Synthetic transactions: what a customer would do."""
    for svc in ENTRY + ["cache"]:
        path = "/get" if svc == "cache" else "/work"
        try:
            (requests.get if path == "/get" else requests.post)(C.url(svc, path), timeout=5)
        except requests.RequestException:
            pass


def snapshot() -> dict:
    """Per service: up?, failing checks, key metrics, last log lines."""
    out = {}
    for svc in C.SERVICES:
        s = {"up": False, "failing": [], "metrics": {}, "logs": []}
        try:
            h = requests.get(C.url(svc, "/health"), timeout=4).json()
            s.update(up=True, failing=[k for k, v in h["checks"].items() if not v])
            s["metrics"] = requests.get(C.url(svc, "/metrics"), timeout=4).json()
        except (requests.RequestException, ValueError):
            pass
        try:
            s["logs"] = (C.service_dir(svc) / "service.log").read_text(encoding="utf-8", errors="replace"
                                                                      ).splitlines()[-4:]
        except OSError:
            pass
        out[svc] = s
    return out


def problems(snap: dict) -> dict:
    return {svc: ("DOWN" if not s["up"] else ", ".join(s["failing"])) for svc, s in snap.items()
            if not s["up"] or s["failing"]}


def closure(svc: str) -> set[str]:
    """svc and everything it depends on, transitively."""
    out, todo = set(), [svc]
    while todo:
        s = todo.pop()
        if s not in out:
            out.add(s)
            todo += C.SERVICES[s]["deps"]
    return out


def events(probs: dict) -> list[dict]:
    """Group failing services into independent events: two services belong together when one depends on the
    other (transitively). Unrelated simultaneous faults become separate incidents."""
    groups: list[set] = []
    for s in probs:
        hit = [g for g in groups if any(s in closure(t) or t in closure(s) for t in g)]
        merged = {s}.union(*hit) if hit else {s}
        groups = [g for g in groups if g not in hit] + [merged]
    return [{svc: probs[svc] for svc in probs if svc in g} for g in groups]


def entry_service(snap: dict, probs: dict) -> str:
    """The customer-facing service the event is reported on."""
    for e in ENTRY:
        if e in probs:
            return e
    for e in ENTRY:                               # a backend problem that has not reached a frontend yet
        if any(s in closure(e) for s in probs):
            return e
    return next(iter(probs))


def category(snap: dict, probs: dict) -> str:
    failing = {c for s in snap.values() for c in s["failing"]}
    if "disk" in failing:
        return "hardware"
    if "db_pool" in failing:
        return "database"
    if failing and failing <= {"latency"} and all(s["up"] for s in snap.values()):
        return "performance"
    return "internal_applications"


KEY_METRICS = ("latency_ms", "error_rate", "memory_mb", "disk_used_pct", "pool_in_use", "pool_size",
               "cert_days_left", "release", "cache_items")


def describe(snap: dict, probs: dict, entry: str) -> tuple[str, str, int, int]:
    down = [s for s, p in probs.items() if p == "DOWN"]
    sev = 1 if down or entry == "web" else 2
    short = f"{MARKER} {entry} degraded: " + "; ".join(f"{s} {p}" for s, p in probs.items())
    lines = [f"Source: lab monitoring (synthetic transactions + health checks)",
             f"Affected service: {entry}",
             f"Detected at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             "Symptoms: " + "; ".join(f"{s}: {p}" for s, p in probs.items()),
             "Observed state:"]
    for svc, s in snap.items():
        if not s["up"]:
            lines.append(f"  {svc}: NOT RESPONDING")
        else:
            m = s["metrics"]
            lines.append(f"  {svc}: " + ", ".join(f"{k}={m.get(k)}" for k in KEY_METRICS if k in m)
                         + (f"  FAILING: {', '.join(s['failing'])}" if s["failing"] else "  ok"))
    lines.append("Diagnostic data (last log lines):")
    for svc in probs:
        for ln in snap[svc]["logs"][-3:]:
            lines.append(f"  {ln[:220]}")
    return short, "\n".join(lines), sev, sev


def check_once(source, out=print) -> str | None:
    """One monitoring cycle: one incident per independent event. Returns the first new incident number."""
    probe()
    snap = snapshot()
    probs = problems(snap)
    state = C.read_json(C.LAB_DIR / STATE, {})
    for key, num in list(state.items()):         # forget incidents that are closed
        try:
            if source.get(num)["state"] in ("RESOLVED", "CLOSED_REJECTED"):
                state.pop(key)
        except KeyError:
            state.pop(key)
    if not probs:
        C.write_json(C.LAB_DIR / STATE, state)
        out("monitor: all services healthy")
        return None
    created = []
    for ev in events(probs):
        entry = entry_service(snap, ev)
        if entry in state:
            out(f"monitor: {entry} problem already reported in {state[entry]} ({ev})")
            continue
        sub = {s: v for s, v in snap.items() if s in closure(entry) or s in ev}
        short, desc, impact, urgency = describe(sub, ev, entry)
        num = source.create_incident(short, desc, category(sub, ev), impact, urgency)
        state[entry] = num
        created.append(num)
        out(f"monitor: created {num} ({category(sub, ev)}): {short}")
    C.write_json(C.LAB_DIR / STATE, state)
    return created[0] if created else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="servicenow")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=15)
    args = ap.parse_args()
    from src.access.ticket_source import get_source
    source = get_source(args.source)
    while True:
        try:
            check_once(source)
        except Exception as e:  # noqa: BLE001 - monitoring must keep running
            print(f"monitor: check failed: {type(e).__name__}: {e}")
        if args.once:
            return
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
