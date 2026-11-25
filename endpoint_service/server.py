"""Endpoint Enforcement Service (Objective 2), v2 (Phase 3).
A small localhost API that runs as Administrator and performs ONLY catalogue actions on
the restricted employee account. Agents call it; the LLM never gets admin rights or a shell.

Start (Administrator terminal, from the project root):
    .venv\\Scripts\\python.exe -m uvicorn endpoint_service.server:app --host 127.0.0.1 --port 8765

v2 changes:
  * start-up baseline: every managed domain is on employee01's URLBlocklist (missing ones added, audited)
  * GET  /precheck/software/{pkg}   processes that would make the installer cancel (3.1)
  * POST /actions/install_software  refuses with status BLOCKED while the app runs; otherwise starts a
                                    background job and answers RUNNING at once (3.7); GET /jobs/{action_id}
  * domain actions record the 'before' state; rollback restores exactly that (3.6); uninstall machine-scope
  * optional expires_at on domain grants; GET /grants lists active time-limited grants (3.3)
  * a second rollback of the same action is refused (409)
"""
import ctypes
import json
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import core
from . import registry_policy as rp
from . import winget_actions as wg
from .endpoint_config import APPROVED_PACKAGES, EMPLOYEE_USER, MANAGED_DOMAINS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
TOKEN = os.environ.get("ENDPOINT_TOKEN", "")
AUDIT_LOG = Path(__file__).resolve().parent / "audit" / "actions.jsonl"
AUDIT_LOG.parent.mkdir(exist_ok=True)

if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
    raise SystemExit("The endpoint service must run on Windows in an ADMINISTRATOR terminal.")
if len(TOKEN) < 24:
    raise SystemExit("Set ENDPOINT_TOKEN (at least 24 characters) in the project .env file.")

SID = rp.get_sid(EMPLOYEE_USER)


@asynccontextmanager
async def lifespan(_app):
    baseline()                                  # defined below; runs once when uvicorn starts
    yield


app = FastAPI(title="ITSM Endpoint Enforcement Service", version="2.0", lifespan=lifespan)

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")
TICKET_RE = r"^[A-Z]{2,5}[0-9]{4,12}$"          # e.g. RITM0010001, INC0010001
_AUDIT_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}                     # action_id -> running/finished install job (memory)


def require_token(x_endpoint_token: str = Header(default="")):
    if x_endpoint_token != TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing X-Endpoint-Token")


class DomainRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    ticket_id: str = Field(pattern=TICKET_RE)
    authorization_id: str = Field(min_length=3, max_length=64)   # issued by the Policy & Risk Agent
    expires_at: str | None = Field(default=None, max_length=40)  # ISO time: time-limited grant (3.3)


class SoftwareRequest(BaseModel):
    package_id: str
    ticket_id: str = Field(pattern=TICKET_RE)
    authorization_id: str = Field(min_length=3, max_length=64)


class RollbackRequest(BaseModel):
    action_id: str
    ticket_id: str = Field(pattern=TICKET_RE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(record: dict) -> dict:
    record = {"action_id": record.pop("action_id", None) or str(uuid.uuid4()), "time": _now(),
              "user": EMPLOYEE_USER, **record}
    with _AUDIT_LOCK, open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _records() -> list[dict]:
    if not AUDIT_LOG.exists():
        return []
    with _AUDIT_LOCK:
        return [json.loads(line) for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check_domain(domain: str) -> str:
    d = domain.strip().lower()
    if not DOMAIN_RE.match(d):
        raise HTTPException(400, f"invalid domain: {domain}")
    if d not in MANAGED_DOMAINS:
        raise HTTPException(403, f"domain not managed by policy catalogue: {d}")
    return d


def _check_package(pkg: str) -> str:
    if pkg not in APPROVED_PACKAGES:
        raise HTTPException(403, f"package not in approved catalogue: {pkg}")
    return pkg


def _check_expiry(value: str | None) -> str | None:
    if not value:
        return None
    try:
        exp = core.parse_time(value)
    except ValueError:
        raise HTTPException(400, f"invalid expires_at: {value}") from None
    if exp <= datetime.now(timezone.utc):
        raise HTTPException(400, "expires_at is in the past")
    return exp.isoformat(timespec="seconds")


def baseline():
    """Every managed domain must be blocked by default (3.8 added new ones)."""
    result = rp.ensure_blocked(SID, sorted(MANAGED_DOMAINS))
    if result["added"]:
        _audit({"action": "BASELINE_BLOCKLIST", "params": {"added": result["added"]}, "ticket_id": "BASELINE",
                "authorization_id": "service-start", "status": "SUCCEEDED", "rollback": None})
    return result


@app.get("/health")
def health():
    return {"status": "ok", "user": EMPLOYEE_USER, "sid": SID, "version": app.version}


@app.get("/status", dependencies=[Depends(require_token)])
def status():
    return {"user": EMPLOYEE_USER, **rp.get_policy_state(SID)}


@app.get("/verify/domain/{domain}", dependencies=[Depends(require_token)])
def verify_domain(domain: str):
    d = _check_domain(domain)
    state = rp.get_policy_state(SID)
    return {"domain": d, "allowed": d in state["allowlist"], "blocked_by_default": d in state["blocklist"]}


@app.get("/verify/software/{package_id}", dependencies=[Depends(require_token)])
def verify_software(package_id: str):
    p = _check_package(package_id)
    return {"package_id": p, "installed": wg.is_installed(p)}


@app.get("/precheck/software/{package_id}", dependencies=[Depends(require_token)])
def precheck_software(package_id: str):
    p = _check_package(package_id)
    return {"package_id": p, "blocking_processes": core.blocking_processes(p, core.running_processes())}


@app.post("/actions/allowlist_domain", dependencies=[Depends(require_token)])
def allowlist_domain(req: DomainRequest):
    d, exp = _check_domain(req.domain), _check_expiry(req.expires_at)
    result = rp.set_domain_allowed(SID, d, True)
    return _audit({"action": "ALLOWLIST_DOMAIN", "params": {"domain": d}, "ticket_id": req.ticket_id,
                   "authorization_id": req.authorization_id, "status": "SUCCEEDED", **result,
                   "expires_at": exp, "rollback": core.domain_rollback_spec(d, result["before"])})


@app.post("/actions/revoke_domain", dependencies=[Depends(require_token)])
def revoke_domain(req: DomainRequest):
    d = _check_domain(req.domain)
    result = rp.set_domain_allowed(SID, d, False)
    return _audit({"action": "REVOKE_DOMAIN", "params": {"domain": d}, "ticket_id": req.ticket_id,
                   "authorization_id": req.authorization_id, "status": "SUCCEEDED", **result,
                   "rollback": core.domain_rollback_spec(d, result["before"])})


def _install_job(action_id: str, req: SoftwareRequest) -> None:
    p = req.package_id
    try:
        result = wg.install(p)
        ok = result["returncode"] == 0
    except Exception as e:  # noqa: BLE001 - a crashed job must still be audited
        result, ok = {"changed": False, "returncode": -1, "message": f"{type(e).__name__}: {e}"}, False
    _JOBS[action_id] = _audit({
        "action_id": action_id, "action": "INSTALL_SOFTWARE", "params": {"package_id": p},
        "ticket_id": req.ticket_id, "authorization_id": req.authorization_id,
        "status": "SUCCEEDED" if ok else "FAILED", **result,
        "rollback": {"action": "UNINSTALL_SOFTWARE", "params": {"package_id": p}} if result.get("changed") else None})


@app.post("/actions/install_software", dependencies=[Depends(require_token)])
def install_software(req: SoftwareRequest):
    p = _check_package(req.package_id)
    blocking = core.blocking_processes(p, core.running_processes())
    if blocking:                              # the installer would cancel silently (seen with VS Code)
        return _audit({"action": "INSTALL_SOFTWARE", "params": {"package_id": p}, "ticket_id": req.ticket_id,
                       "authorization_id": req.authorization_id, "status": "BLOCKED",
                       "blocking_processes": blocking, "changed": False, "rollback": None})
    action_id = str(uuid.uuid4())
    _JOBS[action_id] = {"action_id": action_id, "action": "INSTALL_SOFTWARE", "params": {"package_id": p},
                        "ticket_id": req.ticket_id, "status": "RUNNING", "started": _now()}
    threading.Thread(target=_install_job, args=(action_id, req), daemon=True).start()
    return _JOBS[action_id]


@app.get("/jobs/{action_id}", dependencies=[Depends(require_token)])
def job(action_id: str):
    if action_id in _JOBS:
        return _JOBS[action_id]
    original, _ = core.rollback_state(_records(), action_id)   # finished before a service restart
    if original:
        return original
    raise HTTPException(404, "unknown job (the service may have restarted while it was running)")


@app.get("/grants", dependencies=[Depends(require_token)])
def grants():
    return {"grants": core.active_grants(_records())}


@app.post("/actions/rollback", dependencies=[Depends(require_token)])
def rollback(req: RollbackRequest):
    original, done = core.rollback_state(_records(), req.action_id)
    if not original or not original.get("rollback"):
        raise HTTPException(404, "no rollback available for this action_id")
    if done:
        raise HTTPException(409, f"action already rolled back by {done['action_id']}")
    rb = original["rollback"]
    if rb["action"] == "RESTORE_DOMAIN":
        d = _check_domain(rb["params"]["domain"])
        result, ok = rp.set_domain_allowed(SID, d, bool(rb["params"]["allowed"])), True
    elif rb["action"] in ("REVOKE_DOMAIN", "ALLOWLIST_DOMAIN"):          # v1 audit records
        result = rp.set_domain_allowed(SID, _check_domain(rb["params"]["domain"]), rb["action"] == "ALLOWLIST_DOMAIN")
        ok = True
    elif rb["action"] == "UNINSTALL_SOFTWARE":
        result = wg.uninstall(_check_package(rb["params"]["package_id"]))
        ok = result["returncode"] == 0
    else:
        raise HTTPException(400, "unknown rollback action")
    return _audit({"action": "ROLLBACK", "params": {"of_action_id": req.action_id, **rb}, "ticket_id": req.ticket_id,
                   "authorization_id": "rollback", "status": "SUCCEEDED" if ok else "FAILED", **result,
                   "rollback": None})
