"""Endpoint Enforcement Service (Objective 2).
A small localhost API that runs as Administrator and performs ONLY catalogue actions on
the restricted employee account. Agents call it; the LLM never gets admin rights or a shell.

Start (Administrator terminal, from the project root):
    .venv\\Scripts\\python.exe -m uvicorn endpoint_service.server:app --host 127.0.0.1 --port 8765
"""
import ctypes
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

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
app = FastAPI(title="ITSM Endpoint Enforcement Service", version="1.0")

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")
TICKET_RE = r"^[A-Z]{2,5}[0-9]{4,12}$"          # e.g. RITM0010001, INC0010001


def require_token(x_endpoint_token: str = Header(default="")):
    if x_endpoint_token != TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing X-Endpoint-Token")


class DomainRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    ticket_id: str = Field(pattern=TICKET_RE)
    authorization_id: str = Field(min_length=3, max_length=64)   # issued by the Policy & Risk Agent


class SoftwareRequest(BaseModel):
    package_id: str
    ticket_id: str = Field(pattern=TICKET_RE)
    authorization_id: str = Field(min_length=3, max_length=64)


class RollbackRequest(BaseModel):
    action_id: str
    ticket_id: str = Field(pattern=TICKET_RE)


def _audit(record: dict) -> dict:
    record = {"action_id": str(uuid.uuid4()), "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "user": EMPLOYEE_USER, **record}
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


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


@app.get("/health")
def health():
    return {"status": "ok", "user": EMPLOYEE_USER, "sid": SID}


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


@app.post("/actions/allowlist_domain", dependencies=[Depends(require_token)])
def allowlist_domain(req: DomainRequest):
    d = _check_domain(req.domain)
    result = rp.set_domain_allowed(SID, d, True)
    return _audit({"action": "ALLOWLIST_DOMAIN", "params": {"domain": d}, "ticket_id": req.ticket_id,
                   "authorization_id": req.authorization_id, "status": "SUCCEEDED", **result,
                   "rollback": {"action": "REVOKE_DOMAIN", "params": {"domain": d}}})


@app.post("/actions/revoke_domain", dependencies=[Depends(require_token)])
def revoke_domain(req: DomainRequest):
    d = _check_domain(req.domain)
    result = rp.set_domain_allowed(SID, d, False)
    return _audit({"action": "REVOKE_DOMAIN", "params": {"domain": d}, "ticket_id": req.ticket_id,
                   "authorization_id": req.authorization_id, "status": "SUCCEEDED", **result,
                   "rollback": {"action": "ALLOWLIST_DOMAIN", "params": {"domain": d}}})


@app.post("/actions/install_software", dependencies=[Depends(require_token)])
def install_software(req: SoftwareRequest):
    p = _check_package(req.package_id)
    result = wg.install(p)
    ok = result["returncode"] == 0
    return _audit({"action": "INSTALL_SOFTWARE", "params": {"package_id": p}, "ticket_id": req.ticket_id,
                   "authorization_id": req.authorization_id, "status": "SUCCEEDED" if ok else "FAILED",
                   **result, "rollback": {"action": "UNINSTALL_SOFTWARE", "params": {"package_id": p}}
                   if result.get("changed") else None})


@app.post("/actions/rollback", dependencies=[Depends(require_token)])
def rollback(req: RollbackRequest):
    original = None
    if AUDIT_LOG.exists():
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("action_id") == req.action_id:
                original = rec
    if not original or not original.get("rollback"):
        raise HTTPException(404, "no rollback available for this action_id")
    rb = original["rollback"]
    if rb["action"] in ("REVOKE_DOMAIN", "ALLOWLIST_DOMAIN"):
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
