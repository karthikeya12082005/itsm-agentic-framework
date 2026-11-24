"""LangGraph checkpointer (TODO 2.8): one thread per ticket, persisted in data/checkpoints/agents.sqlite.

The checkpoint is the agents' memory: graph state after every node (understanding, evidence, policy, critic,
approval, execution, trace ...). It lets an approval pause the graph (interrupt) and resume days later in
another process, and lets the stuck-ticket sweep finish a run that crashed half-way.
It replaces the Phase-1 data/agent_state.db.

thread_id = "<ticket source key>|<ticket number>", e.g. "https://dev219979.service-now.com|INC0010031".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from src.common.config import PROJECT_ROOT

CHECKPOINT_DB = PROJECT_ROOT / "data" / "checkpoints" / "agents.sqlite"
SUMMARY_KEYS = ("final_state", "user_message", "acknowledged_at", "approval_status")


def sqlite_saver(path: Path = CHECKPOINT_DB) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")          # the worker and an `approve` CLI may run at the same time
    return SqliteSaver(conn)


def memory_saver() -> InMemorySaver:
    return InMemorySaver()


def config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def values(saver, thread_id: str) -> dict:
    """Latest saved graph state of a thread ({} if none). Works without building the graph."""
    tup = saver.get_tuple(config(thread_id))
    if tup is None:
        return {}
    return {k: v for k, v in tup.checkpoint.get("channel_values", {}).items() if not k.startswith("branch:")}


def summary(saver, thread_id: str) -> dict:
    """Decision summary of a ticket for list/show/metrics (decision, target, policy, sop, final state ...)."""
    v = values(saver, thread_id)
    if not v:
        return {}
    u, p = v.get("understanding") or {}, v.get("policy") or {}
    out = {k: v.get(k) for k in SUMMARY_KEYS}
    out.update(decision=p.get("decision"), target=u.get("target"), policy_id=u.get("policy_id"),
               sop_id=u.get("sop_id"), critic=(v.get("critic") or {}).get("verdict"), flow=v.get("flow"),
               context={k: v.get(k) for k in ("understanding", "evidence", "policy", "critic", "execution",
                                              "attempts", "verified")})
    return out


def delete(saver, thread_id: str) -> None:
    saver.delete_thread(thread_id)
