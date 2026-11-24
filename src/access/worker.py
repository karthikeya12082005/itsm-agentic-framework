"""Worker for the access-request agents (items 1.4 and 1.5). Uses ONLY the TicketSource interface.

  process_ticket   NEW -> acknowledge -> run the LangGraph flow -> write every agent note, the message and the
                   final status back through the ticket source (ServiceNow: work notes, comment, state)
  approve_ticket   approver given in the CLI is checked against the ticket source (LINE_MANAGER = caller's
                   manager, SECURITY_TEAM = member of "IT Security"; no self-approval). Wrong person -> refused
                   and noted, ticket keeps waiting. Right person -> decision recorded, graph resumed.
  run_poller       fetch NEW tickets every `interval` seconds and process them (Ctrl+C stops)

Safety: a crash anywhere in the agent pipeline escalates the ticket to a human (never RESOLVED without the
verification agent's predicate check). The policy decision is always the rule engine's (agents.decide).
"""
from __future__ import annotations

import logging
import time

from src.access import agents

log = logging.getLogger("access.worker")
SORRY = "Your request needs review by the IT team; we will update you."


def _escalate(source, number, where, err):
    log.exception("%s failed for %s", where, number)
    source.add_note(number, "Orchestrator", f"{where} failed ({type(err).__name__}: {str(err)[:300]}); "
                                            "escalated to a human")
    source.add_comment(number, SORRY)
    source.set_status(number, "ESCALATED", reason=f"{where} failed: {type(err).__name__}")
    return "ESCALATED"


def process_ticket(source, graph, number: str) -> str | None:
    """Run the agents on one NEW ticket. Returns the final status, or None if the ticket was not NEW."""
    t = source.get(number)
    if t["state"] != "NEW":
        return None
    source.acknowledge(number)
    t = {**t, "state": "IN_PROGRESS"}
    try:
        result = graph.invoke({"ticket": t, "notes": []})
    except Exception as e:  # noqa: BLE001 - any agent failure goes to a human
        return _escalate(source, number, "agent pipeline", e)
    source.save_result(number, t, result)
    return result["final_state"]


def _context(source, t, retriever=None) -> dict:
    """Agent context saved at approval time; if it was lost, rebuild it with the deterministic agents."""
    ctx = t.get("context") or {}
    if ctx.get("policy") and ctx.get("understanding"):
        return ctx
    st = {"ticket": t, "notes": []}
    st["understanding"] = agents.understanding(st, retriever)["understanding"]
    st["evidence"] = agents.retrieval(st, retriever)["evidence"] if st["understanding"]["known_target"] else {}
    st["policy"] = agents.policy_risk(st)["policy"]
    source.add_note(t["number"], "Orchestrator", "saved agent context was missing; rebuilt it with the "
                                                 f"rule-based agents -> {st['policy']['decision']}")
    return {k: st[k] for k in ("understanding", "evidence", "policy")}


def approve_ticket(source, graph, number: str, approver: str, approved: bool = True,
                   retriever=None) -> tuple[bool, str]:
    """Approver decision from the CLI. Returns (accepted?, explanation)."""
    t = source.get(number)
    if t["state"] != "AWAITING_APPROVAL":
        return False, f"{number} is {t['state']}, not awaiting approval"
    ctx = _context(source, t, retriever)
    if ctx["policy"].get("decision") != "ALLOW_WITH_APPROVAL":
        return False, f"{number}: policy decision is {ctx['policy'].get('decision')}, nothing to approve"
    role = ctx["policy"].get("approver_role")
    ok, why = source.may_approve(approver, role, t)
    if not ok:
        source.add_note(number, "Approval Guard", f"approval attempt by {approver} REFUSED: {why}. "
                                                  "The ticket is still waiting for a valid approver.")
        return False, why
    status = "APPROVED" if approved else "REJECTED"
    source.record_approval(number, approved, approver, role)
    source.set_status(number, "IN_PROGRESS")
    state = {"ticket": t, "resume": True, "approval_status": status, "notes": [], "attempts": 0,
             "understanding": ctx["understanding"], "evidence": ctx.get("evidence") or {}, "policy": ctx["policy"]}
    try:
        result = graph.invoke(state)
    except Exception as e:  # noqa: BLE001
        return True, f"{why}; {status.lower()} -> {_escalate(source, number, 'resumed agent pipeline', e)}"
    result.setdefault("approval_status", status)
    source.save_result(number, t, result)
    return True, f"{why}; {status.lower()} -> {result['final_state']}"


def run_poller(source, graph, interval: float = 30, once: bool = False, out=print) -> int:
    """Poll the ticket source for NEW tickets and process them. `once` = one pass (tests, cron)."""
    out(f"Worker on {source.name}: polling every {interval:g}s" + (" (one pass)" if once else " - Ctrl+C stops"))
    while True:
        try:
            numbers = source.fetch_new()
        except Exception as e:  # noqa: BLE001 - e.g. PDI hibernating; keep polling
            out(f"poll failed: {type(e).__name__}: {e}")
            if once:
                return 1
            time.sleep(interval)
            continue
        for number in numbers:
            t0 = time.perf_counter()
            try:
                final = process_ticket(source, graph, number)
                out(f"{number}: {final or 'skipped (not NEW)'} in {time.perf_counter() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001 - one bad ticket must not stop the worker
                log.exception("processing %s", number)
                out(f"{number}: FAILED to process ({type(e).__name__}: {e})")
        if once:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            out("worker stopped")
            return 0
