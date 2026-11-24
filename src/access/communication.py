"""Communication Agent (TODO 2.7): the OUTCOME is decided in code; the LLM only phrases the message.

outcome(state)  deterministic final status + reason (never RESOLVED without the verification predicate)
compose(...)    LLM writes the user message + a one-line work note from fixed facts; the text is checked
                (no success claim unless RESOLVED, no refusal wording when RESOLVED, names the target,
                mentions approval while waiting) and replaced by the template if the check fails.
"""
from __future__ import annotations

import json
import re

from src.access.catalog import ALTERNATIVES
from src.access.schemas import Message
from src.common.llm_router import complete_json
from src.orchestrator.trace import step

APPROVER_TEXT = {"LINE_MANAGER": "your line manager", "SECURITY_TEAM": "the IT Security team"}
SUCCESS_CLAIM = re.compile(r"\b(?:is now (?:accessible|available|unblocked|enabled|installed)|has been (?:installed|"
                           r"granted|enabled|unblocked|allowlisted|whitelisted)|access (?:is|has been) granted|"
                           r"you (?:can|may) now)\b", re.I)
REFUSAL_CLAIM = re.compile(r"\b(?:denied|rejected|not approved|cannot be granted|declined|could not be)\b", re.I)
SYSTEM = ("You write short, polite messages from an IT service desk to an employee about their access request. "
          "State exactly the outcome you are given, nothing more. Never promise anything that is not in the facts. "
          "Plain text, no markdown, no greeting line, no signature, at most 3 sentences.")


def outcome(state) -> tuple[str, str]:
    """(final_state, reason) from the agents' results - deterministic."""
    p, c = state.get("policy") or {}, state.get("critic") or {}
    d = p.get("decision")
    if state.get("approval_status") == "REJECTED":
        return "CLOSED_REJECTED", f"rejected by the approver ({p.get('approver_role')})"
    if d == "DENY":
        return "CLOSED_REJECTED", p.get("reason", "denied by policy")
    if d == "ESCALATE_FOR_REVIEW":
        return "ESCALATED", p.get("reason", "needs review")
    if c.get("verdict") in ("ESCALATE", "BLOCK"):
        return "ESCALATED", "the plan review found a problem: " + "; ".join(c.get("concerns") or ["see work notes"])[:300]
    if d in ("ALLOW", "ALLOW_WITH_APPROVAL") and state.get("verified"):
        return "RESOLVED", p.get("reason", "")
    if d in ("ALLOW", "ALLOW_WITH_APPROVAL"):
        return "ESCALATED", "the change could not be verified"
    return "ESCALATED", "no decision was made"


def _facts(state, final, reason) -> dict:
    u, p = state.get("understanding") or {}, state.get("policy") or {}
    return {"outcome": final, "request": u.get("label") or u.get("target") or "your request",
            "target": u.get("target"), "kind": u.get("kind"), "policy_id": u.get("policy_id"),
            "decision": p.get("decision"), "reason": reason,
            "approver": APPROVER_TEXT.get(p.get("approver_role"), p.get("approver_role")),
            "alternative": ALTERNATIVES.get(u.get("policy_id"), "") if final == "CLOSED_REJECTED" and
            p.get("decision") == "DENY" else ""}


def template(f: dict) -> str:
    label = f["request"]
    if f["outcome"] == "AWAITING_APPROVAL":
        return (f"Your request for {label} is allowed by policy {f['policy_id']} but needs approval by "
                f"{f['approver']}. Nothing has been changed yet; we will update you after the decision.")
    if f["outcome"] == "CLOSED_REJECTED":
        if f["decision"] != "DENY":
            return f"Your request for {label} was rejected by the approver ({f['approver']})."
        return f"Your request for {label} was not approved: {f['reason']}. {f['alternative']}".strip()
    if f["outcome"] == "ESCALATED":
        return f"Your request for {label} needs review by the IT team ({f['reason']}). We will update you."
    what = "is now accessible in Chrome" if f["kind"] == "domain" else "has been installed"
    return f"Done: {label} {what}. Reason: {f['reason']}."


def message_ok(msg: str, f: dict) -> tuple[bool, str]:
    low = msg.lower()
    if f["outcome"] != "RESOLVED" and SUCCESS_CLAIM.search(msg):
        return False, "claims success although the request is not resolved"
    if f["outcome"] == "RESOLVED" and REFUSAL_CLAIM.search(msg):
        return False, "uses refusal wording although the request is resolved"
    if f["outcome"] == "AWAITING_APPROVAL" and "approv" not in low:
        return False, "does not mention the pending approval"
    names = [w for w in ((f.get("target") or "").lower(), (f.get("request") or "").split(" (")[0].lower()) if w]
    if f.get("target") and not any(n in low for n in names):
        return False, "does not name the requested target"
    return True, ""


def compose(state, final: str, reason: str) -> tuple[str, dict]:
    """(user message, trace step of the Communication Agent)."""
    f = _facts(state, final, reason)
    prompt = ("Facts (JSON):\n" + json.dumps({k: v for k, v in f.items() if v}) +
              "\nWrite `user_message` for the employee (mention the request by name, the outcome and, if given, "
              "the reason / approver / alternative) and `work_note`: one internal sentence for the IT team.")
    res = complete_json("communication", prompt, Message, system=SYSTEM,
                        fallback=lambda: {"user_message": template(f), "work_note": ""})
    msg = res.data.user_message.strip()
    check = ""
    if res.ok:
        ok, why = message_ok(msg, f)
        if not ok:
            check = f"; LLM text rejected ({why}: \"{msg[:120]}\") -> template"
            msg = template(f)
    if f["alternative"] and f["alternative"].split("(")[0][:20].lower() not in msg.lower():
        msg = f"{msg} {f['alternative']}"
    note = res.data.work_note.strip() if res.ok else ""
    out = f"{final}: {msg}" + (f" [note: {note}]" if note else "") + check
    return msg, step("Communication Agent", out, input=f"outcome={final}; reason={reason}", llm=res)
