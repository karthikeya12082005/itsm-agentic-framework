"""Start / stop / restart the lab services as background processes (no admin rights, no Docker).

    python -m src.lab.supervisor start            # all services (or: start web)
    python -m src.lab.supervisor status
    python -m src.lab.supervisor restart orders-api
    python -m src.lab.supervisor stop             # all
    python -m src.lab.supervisor reset            # stop, restore healthy state on disk, start (demo / eval)

PIDs are kept in data/lab/pids.json; only processes started here are ever stopped.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

import requests

from src.lab import config as C

PIDS = C.LAB_DIR / "pids.json"
CREATE_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _pids() -> dict:
    return C.read_json(PIDS, {})


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        return f'"{pid}"' in r.stdout
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def healthy(name: str, timeout: float = 2.0) -> dict | None:
    try:
        r = requests.get(C.url(name, "/health"), timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except requests.RequestException:
        return None


def up(name: str) -> bool:
    try:
        return requests.get(C.url(name, "/metrics"), timeout=2).status_code == 200
    except requests.RequestException:
        return False


def start(name: str, wait_s: float = 60) -> int:
    # 60 s: on this 7.6 GB laptop a service took > 30 s to start once while RAM was paging (0.7 GB free)
    if up(name):
        return _pids().get(name, 0)
    C.LAB_DIR.mkdir(parents=True, exist_ok=True)
    out = open(C.service_dir(name) / "process.log", "a", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "src.lab.service", name], cwd=str(C.LAB_DIR.parents[1]),
                         stdout=out, stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)
    pids = _pids()
    pids[name] = p.pid
    C.write_json(PIDS, pids)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if up(name):
            return p.pid
        if p.poll() is not None:
            raise RuntimeError(f"{name} exited during start (see data/lab/{name}/process.log)")
        time.sleep(0.3)
    raise RuntimeError(f"{name} did not answer within {wait_s:g}s")


def stop(name: str) -> bool:
    pid = _pids().get(name)
    if pid and _alive(pid):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            import os
            import signal
            os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not up(name):
            return True
        time.sleep(0.2)
    return not up(name)


def restart(name: str) -> int:
    stop(name)
    return start(name)


def status() -> dict:
    out = {}
    for name in C.SERVICES:
        h = healthy(name)
        out[name] = {"pid": _pids().get(name), "up": h is not None, "status": h["status"] if h else "down",
                     "failing": [k for k, v in (h or {}).get("checks", {}).items() if not v]}
    return out


def reset_disk_state() -> None:
    """Healthy release, valid certificate, empty spool, fresh logs - the state before any fault."""
    from src.lab.service import default_cert, default_deploy, default_dns, default_profile
    for name in C.SERVICES:
        d = C.service_dir(name)
        C.write_json(d / "deploy.json", default_deploy())
        C.write_json(d / "cert.json", default_cert())
        if name == "vpn-gateway":
            C.write_json(d / "dns.json", default_dns())
            C.write_json(d / "profile.json", default_profile())
        shutil.rmtree(d / "spool", ignore_errors=True)
        shutil.rmtree(d / "spool_archive", ignore_errors=True)
        (d / "spool").mkdir(exist_ok=True)
        for f in ("service.log", "process.log"):
            try:
                (d / f).unlink()
            except OSError:
                pass


def reset() -> None:
    """Stop everything, restore the healthy disk state, start all services."""
    for n in C.SERVICES:
        stop(n)
    reset_disk_state()
    for n in C.START_ORDER:
        start(n)


def main():
    cmd, names = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:] or list(C.SERVICES)
    if cmd == "start":
        order = [n for n in C.START_ORDER if n in names]
        for n in order:
            print(f"{n}: started pid {start(n)}")
    elif cmd == "stop":
        for n in names:
            print(f"{n}: {'stopped' if stop(n) else 'STILL RUNNING'}")
    elif cmd == "restart":
        for n in names:
            print(f"{n}: restarted pid {restart(n)}")
    elif cmd == "reset":
        reset()
    elif cmd != "status":
        sys.exit(__doc__)
    for n, s in status().items():
        print(f"  {n:<11} {s['status']:<9} pid={s['pid']}" + (f"  failing: {s['failing']}" if s["failing"] else ""))


if __name__ == "__main__":
    main()
