"""One simulated service (FastAPI). Run:  python -m src.lab.service <web|orders-api|cache|worker>

Public endpoints:  GET /health  (status ok|degraded + checks)   GET /metrics   GET /logs?n=50
                   POST /work   (one business request; web -> orders-api -> cache, worker -> spool file)
Admin endpoints (X-Lab-Token, used ONLY by the fault injector and the remediation action catalogue):
  POST /admin/fault {"name", "on"}       runtime faults: memory_leak, db_session_leak, cache_bloat, tunnel_down
  POST /admin/cache/clear                POST /admin/db/terminate_sessions     POST /admin/reload
State on disk (data/lab/<name>/): deploy.json (release + config + history), cert.json, spool/, service.log;
vpn-gateway also dns.json (zone record + resolver cache) and profile.json (VPN profile = remote-user credential).
Runtime faults live in the process (a restart clears them, like a real leak); disk faults survive restarts.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import requests
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from src.lab import config as C

GOOD_CONFIG = {"db_pool_size": 5, "cache_ttl_s": 60, "request_timeout_s": 2}


def default_deploy():
    return {"version": "1.0.0", "config": dict(GOOD_CONFIG),
            "history": [{"version": "1.0.0", "config": dict(GOOD_CONFIG), "time": "initial"}]}


def default_cert():
    return {"not_after": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(timespec="seconds"),
            "serial": "LAB-0001"}


def default_dns():
    """vpn-gateway resolver: authoritative zone record + resolver cache (empty = resolve from the zone)."""
    return {"zone": {C.INTERNAL_HOST: C.INTERNAL_ADDR}, "cache": {}}


def default_profile():
    """The VPN profile (client credential) remote employees connect with."""
    return {"not_after": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(timespec="seconds"),
            "serial": "VPN-0001"}


class Fault(BaseModel):
    name: str
    on: bool = True


def build_app(name: str) -> FastAPI:
    d = C.service_dir(name)
    spool = d / "spool"
    spool.mkdir(exist_ok=True)
    if not (d / "deploy.json").exists():
        C.write_json(d / "deploy.json", default_deploy())
    if not (d / "cert.json").exists():
        C.write_json(d / "cert.json", default_cert())
    if name == "vpn-gateway":
        if not (d / "dns.json").exists():
            C.write_json(d / "dns.json", default_dns())
        if not (d / "profile.json").exists():
            C.write_json(d / "profile.json", default_profile())

    log = logging.getLogger(f"lab.{name}")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.FileHandler(d / "service.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s " + name + " %(message)s"))
        log.addHandler(h)

    st = {"started": time.time(), "faults": set(), "leaked_mb": 0.0, "pool_in_use": 0,
          "cache": {}, "latencies": deque(maxlen=20), "results": deque(maxlen=50), "requests": 0}
    lock = threading.Lock()
    token = C.lab_token()
    app = FastAPI(title=f"lab service {name}")

    def deploy():
        return C.read_json(d / "deploy.json", default_deploy())

    def cert_days_left():
        c = C.read_json(d / "cert.json", default_cert())
        return (datetime.fromisoformat(c["not_after"]) - datetime.now(timezone.utc)).total_seconds() / 86400

    def pool_size():
        try:
            return int(deploy()["config"]["db_pool_size"])
        except (KeyError, TypeError, ValueError):
            return 0

    def disk_used_pct():
        used = sum(f.stat().st_size for f in spool.glob("*") if f.is_file())
        return round(100 * used / (C.SPOOL_QUOTA_MB * 1024 * 1024), 1)

    def memory_mb():
        return round(60 + st["leaked_mb"], 1)

    # --------------------------------------------------------------- vpn-gateway state (6.1)
    def resolve(host):
        """Resolver: a cached answer wins (also a cached NXDOMAIN); otherwise the authoritative zone record."""
        dns = C.read_json(d / "dns.json", default_dns())
        hit = dns.get("cache", {}).get(host)
        if hit is not None:
            return hit.get("addr")                       # None = cached negative answer (NXDOMAIN)
        return dns.get("zone", {}).get(host)

    def dns_ok():
        return resolve(C.INTERNAL_HOST) == C.INTERNAL_ADDR

    def profile_days_left():
        p = C.read_json(d / "profile.json", default_profile())
        return (datetime.fromisoformat(p["not_after"]) - datetime.now(timezone.utc)).total_seconds() / 86400

    def tunnel_up():
        return "tunnel_down" not in st["faults"]

    # --------------------------------------------------------------- background "traffic" (leaks grow over time)
    def ticker():
        while True:
            time.sleep(1)
            with lock:
                if "memory_leak" in st["faults"]:
                    st["leaked_mb"] += 25
                    if st["leaked_mb"] % 100 < 25:
                        log.warning("heap usage %.0f MB and growing (allocations not released)", memory_mb())
                    if memory_mb() > 900:
                        log.error("FATAL: OutOfMemoryError - heap exhausted (%.0f MB), process exiting", memory_mb())
                        logging.shutdown()
                        os._exit(3)                              # crash loop: the supervisor sees it down
                if "cache_bloat" in st["faults"] and name == "cache":
                    for i in range(400):
                        st["cache"][f"stale:{len(st['cache'])}:{i}"] = "x" * 64
    threading.Thread(target=ticker, daemon=True).start()

    def admin(x_lab_token):
        if x_lab_token != token:
            raise HTTPException(401, "invalid lab token")

    # --------------------------------------------------------------- business request
    def do_work():
        t0 = time.perf_counter()
        st["requests"] += 1
        if name == "web":
            if cert_days_left() < 0:
                raise RuntimeError("TLS handshake failed: server certificate expired "
                                   f"{-cert_days_left():.1f} days ago (serial {C.read_json(d / 'cert.json', {}).get('serial')})")
            try:
                r = requests.post(C.url("orders-api", "/work"), timeout=3)
            except requests.RequestException as e:
                raise RuntimeError(f"upstream orders-api unreachable: {type(e).__name__}") from None
            if r.status_code != 200:
                raise RuntimeError(f"upstream orders-api returned HTTP {r.status_code}: {r.text[:120]}")
        elif name == "orders-api":
            cfg = deploy()["config"]
            size = int(cfg["db_pool_size"])                  # bad release: "ten" -> ValueError
            with lock:
                if st["pool_in_use"] >= size:
                    raise RuntimeError(f"timeout acquiring DB connection: pool exhausted ({st['pool_in_use']}/{size} "
                                       "sessions in use)")
                st["pool_in_use"] += 1
            try:
                requests.get(C.url("cache", "/get"), params={"k": "order:1"}, timeout=3)
            except requests.RequestException:
                log.warning("cache unavailable, reading from database")
            finally:
                if "db_session_leak" not in st["faults"]:
                    with lock:
                        st["pool_in_use"] -= 1
                else:
                    log.warning("DB session not returned to pool (in use %d/%d)", st["pool_in_use"], size)
        elif name == "cache":
            time.sleep(min(len(st["cache"]) / 20000, 3))     # bloated cache -> slow lookups
        elif name == "vpn-gateway":                         # one remote employee session -> the shop web app
            if profile_days_left() < 0:
                p = C.read_json(d / "profile.json", {})
                raise RuntimeError(f"VPN authentication failed for remote user: VPN profile {p.get('serial')} expired "
                                   f"{-profile_days_left():.1f} days ago (credential no longer valid)")
            if not tunnel_up():
                raise RuntimeError("IPsec tunnel to dc-1 (198.51.100.7) is down: IKE SA not established, "
                                   "dead peer detection timeout")
            addr = resolve(C.INTERNAL_HOST)
            if addr != C.INTERNAL_ADDR:
                raise RuntimeError(f"DNS resolution failed for {C.INTERNAL_HOST}: "
                                   + ("NXDOMAIN (answer from the resolver cache)" if addr is None else
                                      f"resolved to {addr}, connection refused"))
            try:
                r = requests.post(C.url("web", "/work"), timeout=4)
            except requests.RequestException as e:
                raise RuntimeError(f"upstream web unreachable through the tunnel: {type(e).__name__}") from None
            if r.status_code != 200:
                raise RuntimeError(f"upstream web returned HTTP {r.status_code}: {r.text[:120]}")
        elif name == "worker":
            if disk_used_pct() >= 99:
                raise RuntimeError("OSError: [Errno 28] No space left on device (spool volume)")
            (spool / f"order-{time.time_ns()}.json").write_text('{"order": 1}', encoding="utf-8")
        return (time.perf_counter() - t0) * 1000

    @app.post("/work")
    def work():
        try:
            ms = do_work()
            st["latencies"].append(ms)
            st["results"].append(True)
            return {"ok": True, "latency_ms": round(ms, 1)}
        except Exception as e:  # noqa: BLE001 - every failure is a 5xx with a log line
            st["results"].append(False)
            log.error("request failed: %s: %s", type(e).__name__, e)
            raise HTTPException(500, f"{type(e).__name__}: {e}") from None

    @app.get("/get")
    def get(k: str = ""):
        t0 = time.perf_counter()
        time.sleep(min(len(st["cache"]) / 20000, 3))
        st["latencies"].append((time.perf_counter() - t0) * 1000)
        st["results"].append(True)
        return {"k": k, "hit": k in st["cache"]}

    # --------------------------------------------------------------- observability
    def metrics_dict():
        lat = list(st["latencies"])
        res = list(st["results"])
        m = {"service": name, "pid": os.getpid(), "uptime_s": round(time.time() - st["started"]),
             "requests": st["requests"], "latency_ms": round(sum(lat) / len(lat), 1) if lat else 0.0,
             "error_rate": round(1 - sum(res) / len(res), 3) if res else 0.0,
             "memory_mb": memory_mb(), "disk_used_pct": disk_used_pct(), "cache_items": len(st["cache"]),
             "pool_in_use": st["pool_in_use"], "pool_size": pool_size(),
             "cert_days_left": round(cert_days_left(), 2), "release": deploy()["version"]}
        if name == "vpn-gateway":
            m.update(tunnel="up" if tunnel_up() else "down", dns_ok=dns_ok(),
                     profile_days_left=round(profile_days_left(), 2))
        return m

    @app.get("/metrics")
    def metrics():
        return metrics_dict()

    @app.get("/health")
    def health():
        m = metrics_dict()
        checks = {"memory": m["memory_mb"] <= C.MEMORY_MB_MAX, "errors": m["error_rate"] <= C.ERROR_RATE_MAX,
                  "latency": m["latency_ms"] <= C.LATENCY_MS_MAX}
        if name == "web":
            checks["certificate"] = m["cert_days_left"] >= C.CERT_DAYS_MIN
        if name == "orders-api":
            checks["config"] = pool_size() > 0
            checks["db_pool"] = m["pool_size"] - m["pool_in_use"] >= C.POOL_FREE_MIN
        if name == "worker":
            checks["disk"] = m["disk_used_pct"] <= C.DISK_PCT_MAX
        if name == "vpn-gateway":
            checks["tunnel"] = m["tunnel"] == "up"
            checks["dns"] = m["dns_ok"]
            checks["vpn_profile"] = m["profile_days_left"] >= 0
        for dep in C.SERVICES[name]["deps"]:
            try:
                checks[f"dependency:{dep}"] = requests.get(C.url(dep, "/metrics"), timeout=1).status_code == 200
            except requests.RequestException:
                checks[f"dependency:{dep}"] = False
        return {"service": name, "status": "ok" if all(checks.values()) else "degraded", "checks": checks,
                "release": m["release"]}

    @app.get("/logs")
    def logs(n: int = 50):
        try:
            lines = (d / "service.log").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        return {"service": name, "lines": lines[-max(1, min(n, 500)):]}

    # --------------------------------------------------------------- admin (fault injector / action catalogue)
    @app.post("/admin/fault")
    def fault(f: Fault, x_lab_token: str = Header(default="")):
        admin(x_lab_token)
        if f.name not in ("memory_leak", "db_session_leak", "cache_bloat", "tunnel_down"):
            raise HTTPException(400, f"unknown runtime fault {f.name}")
        with lock:
            (st["faults"].add if f.on else st["faults"].discard)(f.name)
        return {"faults": sorted(st["faults"])}

    @app.post("/admin/cache/clear")
    def cache_clear(x_lab_token: str = Header(default="")):
        admin(x_lab_token)
        with lock:
            n = len(st["cache"])
            st["cache"].clear()
            st["faults"].discard("cache_bloat")
            st["latencies"].clear()
        log.info("cache cleared (%d entries removed)", n)
        return {"removed": n}

    @app.post("/admin/db/terminate_sessions")
    def terminate_sessions(x_lab_token: str = Header(default="")):
        admin(x_lab_token)
        with lock:
            n = st["pool_in_use"]
            st["pool_in_use"] = 0
            st["faults"].discard("db_session_leak")
            st["results"].clear()
        log.info("terminated %d idle database sessions", n)
        return {"terminated": n}

    @app.post("/admin/reset_stats")
    def reset_stats(x_lab_token: str = Header(default="")):
        admin(x_lab_token)
        st["latencies"].clear()
        st["results"].clear()
        return {"ok": True}

    @app.get("/admin/state")
    def state(x_lab_token: str = Header(default="")):
        admin(x_lab_token)
        return {"faults": sorted(st["faults"]), "leaked_mb": st["leaked_mb"]}

    log.info("started (pid %d, release %s)", os.getpid(), deploy()["version"])
    return app


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in C.SERVICES:
        sys.exit(f"usage: python -m src.lab.service <{'|'.join(C.SERVICES)}>")
    name = sys.argv[1]
    uvicorn.run(build_app(name), host=C.HOST, port=C.SERVICES[name]["port"], log_level="warning")


if __name__ == "__main__":
    main()
