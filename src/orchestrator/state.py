"""Shared typed state of the multi-agent graph (one LangGraph thread per ticket, persisted by the checkpointer)."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    # ---- input
    ticket: dict                 # number, requester, requester_role, requester_manager, description, category, ...
    acknowledged_at: str         # when the worker picked the ticket up (MTTA)
    approval: dict               # pre-approval when a lost checkpoint is rebuilt: {status, by, role}
    # ---- supervisor
    flow: str                    # access | incident | unsupported
    guardrails: dict             # input checks: truncated, injection_suspected, matches
    next: str                    # node chosen by the supervisor
    route: Annotated[list, operator.add]
    # ---- access flow (Objective 2)
    understanding: dict
    evidence: dict
    policy: dict                 # rule engine output (eq. 3.7) - the ONLY decision
    critic: dict                 # {verdict APPROVE|ESCALATE|BLOCK, concerns, checks, model}
    approval_status: str         # PENDING | APPROVED | REJECTED
    execution: dict
    attempts: int
    verification: dict           # {attempt, passed}
    verified: bool
    # ---- incident flow (Objective 3, Phase 4)
    incident: dict
    # ---- output
    final_state: str             # RESOLVED | CLOSED_REJECTED | ESCALATED | AWAITING_APPROVAL
    user_message: str
    done: bool
    trace: Annotated[list, operator.add]   # one dict per agent step (see orchestrator.trace)
