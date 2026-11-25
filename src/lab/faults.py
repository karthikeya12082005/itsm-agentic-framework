"""Fault injector (TODO 4.2). Each fault is mapped to a dataset root_cause_category and SOP in config.FAULTS
(ground truth for the evaluation only - the agents never read it).

    python -m src.lab.faults list
    python -m src.lab.faults inject <fault>          # e.g. inject bad_config
    python -m src.lab.faults clear                   # = supervisor reset (healthy lab)
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from src.lab import config as C
from src.lab import supervisor as S


def _admin(name, path, body=None):
    r = requests.post(C.url(name, path), json=body, headers={"X-Lab-Token": C.lab_token()}, timeout=10)
    r.raise_for_status()
    return r.json()


def _log(name, level, msg):
    """Write a line into the service log as the service itself would (crash messages of a killed process)."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    with open(C.service_dir(name) / "service.log", "a", encoding="utf-8") as f:
        f.write(f"{stamp} {level} {name} {msg}\n")


def traffic(n: int = 8) -> None:
    """A few business requests so metrics and logs show the symptom."""
    for _ in range(n):
        for svc in ("web", "worker"):
            try:
                requests.post(C.url(svc, "/work"), timeout=5)
            except requests.RequestException:
                pass


def inject(fault: str) -> dict:
    if fault not in C.FAULTS:
        raise SystemExit(f"unknown fault {fault}; one of {sorted(C.FAULTS)}")
    f = C.FAULTS[fault]
    svc = f["service"]
    d = C.service_dir(svc)
    if fault == "service_down":
        _log(svc, "ERROR", "unhandled exception in request handler: AttributeError: 'NoneType' object has no "
                           "attribute 'items' (orders.py:88)")
        _log(svc, "CRITICAL", "worker process exited with code 1 - service stopped")
        S.stop(svc)
    elif fault == "dependency_down":
        _log(svc, "CRITICAL", "process terminated unexpectedly (exit code 3221225477, access violation)")
        S.stop(svc)
    elif fault == "memory_leak":
        _admin(svc, "/admin/fault", {"name": "memory_leak", "on": True})
        time.sleep(18)                                       # grow past the memory threshold
    elif fault == "high_latency":
        _admin(svc, "/admin/fault", {"name": "cache_bloat", "on": True})
        time.sleep(25)                                       # ~10k stale entries -> lookups > 500 ms
    elif fault == "disk_full":
        spool = d / "spool"
        spool.mkdir(exist_ok=True)
        chunk = b"0" * (1024 * 1024)
        for i in range(int(C.SPOOL_QUOTA_MB * 0.99)):
            (spool / f"batch-{i:04d}.dat").write_bytes(chunk)
        (spool / "batch-tail.dat").write_bytes(b"0" * int(0.009 * C.SPOOL_QUOTA_MB * 1024 * 1024))
        _log(svc, "WARNING", "spool volume usage above 95 % - old batch files are not being purged")
    elif fault == "bad_config":
        dep = C.read_json(d / "deploy.json", {})
        bad = {"version": "1.1.0", "config": {**dep.get("config", {}), "db_pool_size": "ten"}}
        dep["history"] = dep.get("history", []) + [{**bad, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}]
        dep.update(version=bad["version"], config=bad["config"])
        C.write_json(d / "deploy.json", dep)
        _log(svc, "INFO", "deployed release 1.1.0 (config change: db_pool_size)")
    elif fault == "cert_expired":
        cert = C.read_json(d / "cert.json", {})
        cert["not_after"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        C.write_json(d / "cert.json", cert)
    elif fault == "db_pool_exhausted":
        _admin(svc, "/admin/fault", {"name": "db_session_leak", "on": True})
        for _ in range(6):
            try:
                requests.post(C.url(svc, "/work"), timeout=5)
            except requests.RequestException:
                pass
    traffic()
    return {"fault": fault, "service": svc, "injected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main():
    logging.basicConfig(level=logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for k, v in C.FAULTS.items():
            print(f"{k:<18} {v['service']:<11} {v['describe']}")
    elif cmd == "inject" and len(sys.argv) == 3:
        print(inject(sys.argv[2]))
        for n, s in S.status().items():
            print(f"  {n:<11} {s['status']:<9}" + (f" failing: {s['failing']}" if s["failing"] else ""))
    elif cmd == "clear":
        S.reset()
        for n, s in S.status().items():
            print(f"  {n:<11} {s['status']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
