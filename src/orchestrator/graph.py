"""Supervisor graph (TODO 2.3): one LangGraph StateGraph for every ticket, hub-and-spoke.

                      ┌──────────────────────── supervisor ────────────────────────┐
 START ─> supervisor ─┤  (input guardrails, flow selection, deterministic routing)  ├─> END
                      └─▲───▲───▲───▲───▲───▲───▲───▲───────────────────────────────┘
                        │   │   │   │   │   │   │   └ incident flow (Objective 3): triage, retrieve, rca, plan,
                        │   │   │   │   │   │   │     critic, (approval), remediate, verify, rollback, communicate
          understand ───┘   │   │   │   │   │   └ communicate (Communication Agent, LLM)
          retrieve ─────────┘   │   │   │   └ verify (predicate check)
          decide (rules) ───────┘   │   └ execute (enforcement service)
          critic (LLM + rules) ─────┘
          request_approval ─> await_approval (interrupt: human in the loop) ─> supervisor

Every agent node returns to the supervisor; `route()` (pure function, no LLM) picks the next node from the
shared state. The routing order for an access request:
  understand -> [retrieve if known target] -> decide -> DENY/ESCALATE: communicate
  ALLOW / ALLOW_WITH_APPROVAL -> critic -> (ESCALATE/BLOCK: communicate)
  ALLOW_WITH_APPROVAL -> request_approval -> await_approval (pause) -> REJECTED: communicate
  -> execute -> verify -> [execute again if FAILED and attempts < MAX_ATTEMPTS] -> communicate -> END
  install (Phase 3): execute -> RUNNING -> await_job (pause; worker resumes when the job ends) -> verify
                     app open -> BLOCKED -> request_user_action -> await_user (pause; worker resumes when the
                     app is closed -> execute, or after 24 h -> communicate "not completed")
With a checkpointer every step is persisted (thread_id = ticket), so an approval can resume days later.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.access import agents
from src.access.critic import critic
from src.incident import agents as incident_agents
from src.orchestrator.guardrails import check_input
from src.orchestrator.state import AgentState
from src.orchestrator.trace import step, timed

ACCESS_CATEGORIES = {"access", "", None}


def select_flow(ticket: dict) -> str:
    """access = category 'access' (lean design) or no category (local desk); every other category = incident."""
    return "access" if ticket.get("category") in ACCESS_CATEGORIES else "incident"


def route(s: dict) -> str:
    """Next node for the shared state (deterministic)."""
    if s.get("done"):
        return END
    if s.get("flow") == "incident":
        return incident_agents.route(s)
    if "understanding" not in s:
        return "understand"
    u = s["understanding"]
    if u.get("known_target") and "evidence" not in s:
        return "retrieve"
    if "policy" not in s:
        return "decide"
    d = s["policy"]["decision"]
    if d in ("DENY", "ESCALATE_FOR_REVIEW"):
        return "communicate"
    if "critic" not in s:
        return "critic"
    if s["critic"]["verdict"] != "APPROVE":
        return "communicate"
    if d == "ALLOW_WITH_APPROVAL":
        status = s.get("approval_status")
        if status == "REJECTED":
            return "communicate"
        if status != "APPROVED":
            # PENDING = already asked; a pre-approval (rebuilt lost checkpoint) needs no new request
            return "await_approval" if status == "PENDING" or s.get("approval") else "request_approval"
    attempts = s.get("attempts", 0)
    ex, ver = s.get("execution") or {}, s.get("verification") or {}
    st = ex.get("status")
    if st == "RUNNING":                         # install job on the endpoint service (3.7)
        return "await_job"
    if st == "BLOCKED":                         # the app is open: ask the employee to close it (3.1)
        return "await_user" if s.get("final_state") == "WAITING_FOR_USER" else "request_user_action"
    if st == "ABANDONED":
        return "communicate"
    if attempts == 0 or st == "READY":
        return "execute"
    if ver.get("attempt", 0) < ex.get("attempt", 0):
        return "verify"
    if ver.get("passed"):
        return "communicate"
    return "execute" if attempts < agents.MAX_ATTEMPTS else "communicate"


def supervisor(s):
    upd = {}
    if not s.get("flow"):
        t = s["ticket"]
        g = check_input(t.get("description", ""))
        flow = select_flow(t)
        upd.update(flow=flow, guardrails=g)
        gtxt = ("possible prompt injection " + str(g["matches"]) + " -> LLM extraction off, critic will escalate"
                if g["injection_suspected"] else "input checks passed") + ("; text truncated" if g["truncated"] else "")
        upd["trace"] = [step("Supervisor", f"flow={flow} (category={t.get('category') or 'none'}); {gtxt}",
                             input=f"ticket {t['number']} from {t.get('requester')}")]
    nxt = route({**s, **upd})
    upd.update(next=nxt, route=[nxt])
    return upd


def build_graph(retriever=None, checkpointer=None, rewrite_query: bool = True):
    """Compile the supervisor graph. Without a checkpointer approvals cannot pause (tests pass InMemorySaver)."""
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor)
    nodes = {
        "understand": timed(lambda s: agents.understanding(s, retriever)),
        "retrieve": timed(lambda s: agents.retrieval(s, retriever, rewrite_query)),
        "decide": timed(agents.policy_risk),
        "critic": timed(critic),
        "request_approval": timed(agents.request_approval),
        "await_approval": timed(agents.await_approval),
        "execute": timed(agents.execution),
        "await_job": timed(agents.await_job),
        "request_user_action": timed(agents.request_user_action),
        "await_user": timed(agents.await_user),
        "verify": timed(agents.verification),
        "communicate": timed(agents.communicate),
        # ---- incident flow (Objective 3)
        "triage": timed(incident_agents.triage),
        "incident_retrieve": timed(lambda s: incident_agents.retrieve(s, retriever)),
        "rca": timed(incident_agents.root_cause_analysis),
        "incident_plan": timed(incident_agents.plan),
        "incident_critic": timed(incident_agents.incident_critic),
        "incident_request_approval": timed(incident_agents.request_approval),
        "remediate": timed(incident_agents.remediate),
        "incident_verify": timed(incident_agents.verify),
        "incident_rollback": timed(incident_agents.rollback),
        "incident_communicate": timed(incident_agents.communicate),
    }
    for name, fn in nodes.items():
        g.add_node(name, fn)
        if name in ("request_approval", "incident_request_approval"):
            g.add_edge(name, "await_approval")
        elif name == "request_user_action":
            g.add_edge(name, "await_user")
        else:
            g.add_edge(name, "supervisor")
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["next"], {**{n: n for n in nodes}, END: END})
    return g.compile(checkpointer=checkpointer)
