"""Worker for the multi-agent graph. Uses ONLY the TicketSource interface for tickets.

  process_ticket   NEW -> acknowledge -> run the supervisor graph in the ticket's thread (checkpointed) ->
                   write the run's trace (one numbered note), the message and the final status back
  approve_ticket   approver given in the CLI is checked against the ticket source (LINE_MANAGER = caller's
                   manager, SECURITY_TEAM = member of "IT Security"; no self-approval). Wrong person -> refused
                   and noted, ticket keeps waiting. Right person -> decision recorded, the paused graph is
                   resumed from its checkpoint (Command(resume=...)).
  sweep_stuck      tickets left IN_PROGRESS (crash between acknowledge and save) are finished from their
                   checkpoint: re-saved, resumed, or re-run (item 2.8)
  run_poller       sweep + fetch NEW tickets every `interval` seconds and process them (Ctrl+C stops)

Safety: a crash anywhere in the agent pipeline escalates the ticket to a human (never RESOLVED without the
verification agent's predicate check). The policy decision is always the rule engine's (agents.decide).
"""
from __future__ import annotations

import logging
import time

from langgraph.types import Command

from src.access import agents
from src.access.store import now
from src.orchestrator import checkpoint as cp
from src.orchestrator.trace import step

log = logging.getLogger("access.worker")
SORRY = "Your request needs review by the IT team; we will update you."
STUCK_AFTER_S = 600


def _escalate(source, number, where, err):
    log.exception("%s failed for %s", where, number)
    source.add_note(number, "Orchestrator", f"{where} failed ({type(err).__name__}: {str(err)[:300]}); "
                                            "escalated to a human")
    source.add_comment(number, SORRY)
    source.set_status(number, "ESCALATED", reason=f"{where} failed: {type(err).__name__}")
    return "ESCALATED"


def _cfg(source, number):
    return cp.config(source.thread_id(number))


def _fresh_run(source, graph, number, t, extra=None):
    """Start a new thread for the ticket (an old checkpoint of the same number is discarded)."""
    cp.delete(graph.checkpointer, source.thread_id(number))
    state = {"ticket": t, "acknowledged_at": now(), "trace": [], **(extra or {})}
    return graph.invoke(state, _cfg(source, number))


def process_ticket(source, graph, number: str) -> str | None:
    """Run the agents on one NEW ticket. Returns the final status, or None if the ticket was not NEW."""
    t = source.get(number)
    if t["state"] != "NEW":
        return None
    source.acknowledge(number)
    t = {**t, "state": "IN_PROGRESS"}
    try:
        result = _fresh_run(source, graph, number, t)
    except Exception as e:  # noqa: BLE001 - any agent failure goes to a human
        return _escalate(source, number, "agent pipeline", e)
    source.save_result(number, t, result)
    return result["final_state"]


def _rebuilt_context(source, t, retriever=None) -> dict:
    """Checkpoint lost: rebuild understanding + decision with the deterministic agents (no LLM)."""
    st = {"ticket": t, "guardrails": {"text": t["description"]}}
    st["understanding"] = agents.understanding(st, retriever, use_llm=False)["understanding"]
    st["policy"] = agents.policy_risk(st)["policy"]
    return st


def approve_ticket(source, graph, number: str, approver: str, approved: bool = True,
                   retriever=None) -> tuple[bool, str]:
    """Approver decision from the CLI. Returns (accepted?, explanation)."""
    t = source.get(number)
    if t["state"] != "AWAITING_APPROVAL":
        return False, f"{number} is {t['state']}, not awaiting approval"
    cfg = _cfg(source, number)
    snap = graph.get_state(cfg)
    waiting = "await_approval" in (snap.next or ()) and bool(snap.values.get("policy"))
    ctx = snap.values if waiting else _rebuilt_context(source, t, retriever)
    if ctx["policy"].get("decision") != "ALLOW_WITH_APPROVAL":
        return False, f"{number}: policy decision is {ctx['policy'].get('decision')}, nothing to approve"
    role = ctx["policy"].get("approver_role")
    ok, why = source.may_approve(approver, role, t)
    if not ok:
        source.add_note(number, "Approval Guard", f"approval attempt by {approver} REFUSED: {why}. "
                                                  "The ticket is still waiting for a valid approver.")
        return False, why
    status = "APPROVED" if approved else "REJECTED"
    decision = {"status": status, "by": approver, "role": role}
    source.record_approval(number, approved, approver, role)
    source.set_status(number, "IN_PROGRESS")
    t = {**t, "state": "IN_PROGRESS"}
    try:
        if waiting:
            n0 = len(snap.values.get("trace", []))
            result = graph.invoke(Command(resume=decision), cfg)
            steps = result["trace"][n0:]
        else:
            note = step("Orchestrator", "checkpoint of this ticket was missing; the graph was re-run with the "
                                        f"checked approval ({status} by {approver}, {role})")
            result = _fresh_run(source, graph, number, t, {"approval": decision, "trace": [note]})
            steps = result["trace"]
    except Exception as e:  # noqa: BLE001
        return True, f"{why}; {status.lower()} -> {_escalate(source, number, 'resumed agent pipeline', e)}"
    source.save_result(number, t, result, steps)
    if result.get("__interrupt__"):
        return True, f"{why}; recorded, but the re-run decision needs approval by another role -> waiting again"
    return True, f"{why}; {status.lower()} -> {result['final_state']}"


def sweep_stuck(source, graph, older_than_s: float = STUCK_AFTER_S, out=print) -> list[tuple[str, str]]:
    """Finish tickets stuck IN_PROGRESS for longer than older_than_s (known issue from Phase 1)."""
    done = []
    for number in source.fetch_stuck(older_than_s):
        cfg = _cfg(source, number)
        try:
            t = source.get(number)
            snap = graph.get_state(cfg)
            v = snap.values
            if not v:                                         # crashed before the graph started
                action, result = "no checkpoint -> agents re-run", _fresh_run(source, graph, number, t)
                steps = result["trace"]
            elif "await_approval" in (snap.next or ()):       # paused for approval
                if t.get("approval_status") in ("APPROVED", "REJECTED"):
                    role = v["policy"].get("approver_role")
                    d = {"status": t["approval_status"], "by": "approver recorded on the ticket", "role": role}
                    n0 = len(v.get("trace", []))
                    action, result = f"approval {d['status']} recorded -> graph resumed", \
                        graph.invoke(Command(resume=d), cfg)
                    steps = result["trace"][n0:]
                else:
                    action, result, steps = "result of the run was not saved -> re-saved (waiting)", v, v["trace"]
            elif snap.next:                                   # crashed in the middle of a run
                action, result = f"run interrupted before {snap.next} -> resumed from checkpoint", \
                    graph.invoke(None, cfg)
                steps = result["trace"]
            else:                                             # finished but not written back
                action, result, steps = "finished run was not saved -> re-saved", v, v["trace"]
            source.add_note(number, "Orchestrator", f"recovery sweep: {action}")
            source.save_result(number, t, result, steps)
            done.append((number, action))
            out(f"{number}: recovered ({action}) -> {result.get('final_state')}")
        except Exception as e:  # noqa: BLE001 - one bad ticket must not stop the sweep
            log.exception("sweep %s", number)
            out(f"{number}: sweep failed ({type(e).__name__}: {e})")
    return done


def run_poller(source, graph, interval: float = 30, once: bool = False, out=print,
               stuck_after_s: float = STUCK_AFTER_S) -> int:
    """Poll the ticket source for NEW tickets and process them. `once` = one pass (tests, cron)."""
    out(f"Worker on {source.name}: polling every {interval:g}s" + (" (one pass)" if once else " - Ctrl+C stops"))
    while True:
        try:
            numbers = source.fetch_new()
            sweep_stuck(source, graph, stuck_after_s, out)
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
