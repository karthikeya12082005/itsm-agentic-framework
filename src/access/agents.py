"""Agents for the access-request flow (Objective 2).
LLMs are used only to read the request; every decision that changes the laptop is deterministic."""
import json
import re

from src.access import endpoint_client
from src.access.catalog import ALTERNATIVES, EMPLOYEES, SOFTWARE_KEYWORDS, TARGETS
from src.access.store import now
from src.common.config import PROCESSED_DIR
from src.common.llm import gemini_json

RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
ENDPOINT_CRITICALITY = "LOW"          # a single employee laptop
MAX_ATTEMPTS = 2
DOMAIN_RE = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,24})\b", re.I)


def _load(name):
    with open(PROCESSED_DIR / name, encoding="utf-8") as f:
        return {r["id"]: r["record"] for r in map(json.loads, f)}


POLICIES = _load("policies.jsonl")
SOPS = _load("sops.jsonl")


# ---------------------------------------------------------------- Understanding Agent
def _normalise_target(raw):
    if not raw:
        return None
    raw = str(raw).strip()
    if raw in TARGETS:
        return raw
    low = raw.lower()
    m = DOMAIN_RE.search(low)
    if m and m.group(1) in TARGETS:
        return m.group(1)
    for kw, pkg in sorted(SOFTWARE_KEYWORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(kw)}\b", low):
            return pkg
    return m.group(1) if m else None          # a domain we do not manage -> handled as unknown


def extract_request(text):
    """LLM extraction with a rule-based fallback. Returns (target, justification, method)."""
    prompt = ("You read IT service requests from employees. Return JSON with keys "
              '"request_kind" ("website_access" | "software_install" | "other"), '
              '"target" (the website domain like "example.com", or the software name), '
              '"justification" (the business reason in one sentence, or ""). '
              f"Request: {text!r}")
    out = gemini_json(prompt)
    if isinstance(out, dict) and out.get("target"):
        target = _normalise_target(out["target"]) or _normalise_target(text)
        return target, str(out.get("justification") or ""), "llm"
    return _normalise_target(text), "", "rule-based"


def understanding(state, retriever=None):
    t = state["ticket"]
    target, justification, method = extract_request(t["description"])
    employee = EMPLOYEES.get(t["requester"], {"role": "EMPLOYEE", "department": "UNKNOWN"})
    known = target in TARGETS
    u = {"target": target, "known_target": known, "justification": justification, "extraction": method,
         "requester_role": employee["role"], "impact": "Low", "urgency": "Medium", "priority": "Low"}
    if known:
        info, pol = TARGETS[target], POLICIES[TARGETS[target]["policy_id"]]
        u.update(kind=info["kind"], label=info["label"], policy_id=info["policy_id"], sop_id=pol["sop_id"],
                 ticket_type=pol["ticket_type"], category=pol["category"], subcategory=pol["subcategory"],
                 service=pol["service"])
        if retriever is not None:                 # similarity-weighted k-NN confidence (report Table 3.4)
            res = retriever.retrieve(t["description"], "incidents", k=5, mode="dense")
            total = sum(i["cosine"] or 0 for i in res["items"]) or 1.0
            agree = sum(i["cosine"] or 0 for i in res["items"]
                        if retriever.meta["incidents"][i["id"]]["subcategory"] == pol["subcategory"])
            u["confidence"] = round(agree / total, 3)
    note = (f"target={target} ({'managed' if known else 'NOT in catalogue'}); extraction={method}"
            + (f"; {u['ticket_type']}/{u['category']}/{u['subcategory']}; policy {u['policy_id']}" if known else "")
            + (f"; k-NN confidence {u['confidence']}" if "confidence" in u else ""))
    return {"understanding": u, "notes": [("Understanding Agent", note)]}


# ---------------------------------------------------------------- Retrieval Agent
def retrieval(state, retriever=None):
    u = state["understanding"]
    ev = {"sop_id": u["sop_id"], "policy_id": u["policy_id"], "retrieved": False}
    if retriever is not None:
        res = retriever.retrieve_all(state["ticket"]["description"], k=3, filters={"category": u["category"]})
        ev.update(retrieved=True, weak=res["weak"],
                  top_sops=[i["id"] for i in res["sops"]["items"]],
                  similar_incidents=[i["id"] for i in res["incidents"]["items"]])
        ev["sop_in_top3"] = u["sop_id"] in ev["top_sops"]
        note = (f"linked {u['sop_id']} / {u['policy_id']}; hybrid top SOPs {ev['top_sops']} "
                f"(linked SOP in top-3: {ev['sop_in_top3']}); similar cases {ev['similar_incidents']}")
    else:
        note = f"linked {u['sop_id']} / {u['policy_id']} (retrieval skipped)"
    return {"evidence": ev, "notes": [("Retrieval Agent", note)]}


# ---------------------------------------------------------------- Policy & Risk Agent (rule engine, eq. 3.6/3.7)
def decide(policy, role, target_known=True):
    if not target_known:
        return {"decision": "ESCALATE_FOR_REVIEW", "reason": "requested target is not in the managed catalogue"}
    eligible = set(policy["user_role"].split("|")) if policy["user_role"] != "NONE" else set()
    risk = max(policy["risk_level"], ENDPOINT_CRITICALITY, key=lambda r: RISK_RANK[r])
    if policy["default_decision"] == "DENY":
        return {"decision": "DENY", "risk": risk,
                "reason": f"{policy['policy_id']} prohibits this action ({policy['subcategory']})"}
    if role not in eligible:
        return {"decision": "DENY", "risk": risk,
                "reason": f"role {role} is not eligible under {policy['policy_id']}"}
    if str(policy["approval_required"]).lower() == "true" or RISK_RANK[risk] >= RISK_RANK["HIGH"]:
        return {"decision": "ALLOW_WITH_APPROVAL", "risk": risk, "approver_role": policy["approver_role"],
                "reason": f"{policy['policy_id']} requires approval by {policy['approver_role']} (risk {risk})"}
    return {"decision": "ALLOW", "risk": risk,
            "reason": f"{policy['policy_id']} allows this for role {role} without approval (risk {risk})"}


def policy_risk(state):
    u = state["understanding"]
    if not u["known_target"]:
        d = decide(None, u["requester_role"], target_known=False)
    else:
        d = decide(POLICIES[u["policy_id"]], u["requester_role"])
    d["authorization_id"] = f"AUTH-{state['ticket']['number']}-{d['decision']}"
    return {"policy": d, "notes": [("Policy & Risk Agent", f"{d['decision']}: {d['reason']}")]}


def request_approval(state):
    p = state["policy"]
    return {"final_state": "AWAITING_APPROVAL", "approval_status": "PENDING",
            "notes": [("Policy & Risk Agent", f"approval requested from {p['approver_role']}; nothing executed yet")]}


# ---------------------------------------------------------------- Execution Agent (no LLM)
def execution(state):
    u, p = state["understanding"], state["policy"]
    attempts = state.get("attempts", 0) + 1
    try:
        rec = endpoint_client.execute(u["kind"], u["target"], state["ticket"]["number"], p["authorization_id"])
        ok = rec.get("status") == "SUCCEEDED"
        note = f"attempt {attempts}: {rec['action']} {u['target']} -> {rec['status']} (audit {rec['action_id']})"
        return {"attempts": attempts, "execution": {"status": "SUCCEEDED" if ok else "FAILED",
                                                    "action_id": rec["action_id"]},
                "notes": [("Execution Agent", note)]}
    except endpoint_client.EndpointError as e:
        return {"attempts": attempts, "execution": {"status": "FAILED", "error": str(e)},
                "notes": [("Execution Agent", f"attempt {attempts} failed: {e}")]}


# ---------------------------------------------------------------- Verification Agent (predicate check, eq. 3.8)
def verification(state):
    u = state["understanding"]
    try:
        verified = state["execution"]["status"] == "SUCCEEDED" and endpoint_client.verify(u["kind"], u["target"])
    except endpoint_client.EndpointError:
        verified = False
    note = (f"check '{u['target']} is {'allowed' if u['kind'] == 'domain' else 'installed'}' -> "
            f"{'PASSED' if verified else 'FAILED'}")
    return {"verified": verified, "notes": [("Verification Agent", note)]}


# ---------------------------------------------------------------- Communication Agent
def communication(state):
    u, p = state["understanding"], state.get("policy", {})
    label = u.get("label") or u.get("target") or "your request"
    d = p.get("decision")
    if state.get("approval_status") == "REJECTED":
        final, msg = "CLOSED_REJECTED", f"Your request for {label} was rejected by the approver ({p.get('approver_role')})."
    elif d == "DENY":
        alt = ALTERNATIVES.get(u.get("policy_id"), "")
        final, msg = "CLOSED_REJECTED", f"Your request for {label} was not approved: {p['reason']}. {alt}".strip()
    elif d == "ESCALATE_FOR_REVIEW" or (d in ("ALLOW", "ALLOW_WITH_APPROVAL") and not state.get("verified")):
        reason = p.get("reason") if d == "ESCALATE_FOR_REVIEW" else "the change could not be verified"
        final, msg = "ESCALATED", f"Your request for {label} needs review by the IT team ({reason}). We will update you."
    else:
        what = "is now accessible in Chrome" if u.get("kind") == "domain" else "has been installed"
        final, msg = "RESOLVED", f"Done: {label} {what}. Reason: {p.get('reason')}."
    return {"final_state": final, "user_message": msg, "notes": [("Communication Agent", f"{final}: {msg}")]}
