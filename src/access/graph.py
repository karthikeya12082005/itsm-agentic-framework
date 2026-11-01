"""LangGraph orchestrator for the access-request flow (report Algorithm 3.1, adapted to Objective 2).

 START ─┬─(new)────> understand ─> retrieve ─> decide ─┬─ DENY / ESCALATE ──────────────> communicate ─> END
        │                         (unknown target ──>) │─ ALLOW ─> execute ─> verify ─┬─ PASSED ─> communicate
        │                                              │                     ^        └─ FAILED, retry ─┘ (max 2)
        │                                              └─ ALLOW_WITH_APPROVAL ─> request_approval ─> END (waits)
        └─(after approval)─> execute ... / (rejected) ─> communicate
"""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from src.access import agents


class FlowState(TypedDict, total=False):
    ticket: dict
    resume: bool
    approval_status: str
    understanding: dict
    evidence: dict
    policy: dict
    execution: dict
    attempts: int
    verified: bool
    final_state: str
    user_message: str
    notes: Annotated[list, operator.add]       # every agent appends its work note


def build_graph(retriever=None):
    g = StateGraph(FlowState)
    g.add_node("understand", lambda s: agents.understanding(s, retriever))
    g.add_node("retrieve", lambda s: agents.retrieval(s, retriever))
    g.add_node("decide", agents.policy_risk)
    g.add_node("request_approval", agents.request_approval)
    g.add_node("execute", agents.execution)
    g.add_node("verify", agents.verification)
    g.add_node("communicate", agents.communication)

    def route_start(s):
        if not s.get("resume"):
            return "understand"
        return "execute" if s.get("approval_status") == "APPROVED" else "communicate"

    def route_decision(s):
        d = s["policy"]["decision"]
        if d == "ALLOW":
            return "execute"
        if d == "ALLOW_WITH_APPROVAL":
            return "request_approval"
        return "communicate"

    def route_verify(s):
        if s.get("verified"):
            return "communicate"
        return "execute" if s.get("attempts", 0) < agents.MAX_ATTEMPTS else "communicate"

    g.add_conditional_edges(START, route_start, {"understand": "understand", "execute": "execute",
                                                 "communicate": "communicate"})
    g.add_conditional_edges("understand", lambda s: "retrieve" if s["understanding"]["known_target"] else "decide",
                            {"retrieve": "retrieve", "decide": "decide"})
    g.add_edge("retrieve", "decide")
    g.add_conditional_edges("decide", route_decision, {"execute": "execute", "request_approval": "request_approval",
                                                       "communicate": "communicate"})
    g.add_edge("request_approval", END)
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", route_verify, {"communicate": "communicate", "execute": "execute"})
    g.add_edge("communicate", END)
    return g.compile()
