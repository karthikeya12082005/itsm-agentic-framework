"""Traceability (TODO 2.9): every agent step is a dict in state["trace"]

    {"agent", "model", "input", "output", "latency_s", "fallback"}

model = "provider:model" when an LLM answered, "rule-based" when the LLM chain failed and the deterministic
fallback was used, "deterministic" for agents that never use an LLM (policy, execution, verification).
The worker writes the steps of one graph run as ONE numbered work note (true order, see ticket_source)."""
from __future__ import annotations

import time
from functools import wraps

MAX_FIELD = 400


def _clip(text, n=MAX_FIELD) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def step(agent: str, output: str, *, input: str = "", llm=None, model: str = "deterministic") -> dict:
    """One trace entry. `llm` = LLMResult from llm_router (sets model / fallback / llm latency)."""
    s = {"agent": agent, "model": model, "input": _clip(input, 200), "output": _clip(output), "fallback": "none"}
    if llm is not None:
        s["model"] = llm.spec if llm.ok else llm.method
        s["fallback"] = llm.fallback
        s["llm_latency_s"] = round(llm.latency_s, 3)
        if llm.tokens:
            s["tokens"] = llm.tokens
    return s


def timed(fn):
    """Node wrapper: stamps the node's wall-clock latency on every trace entry it returns."""
    @wraps(fn)
    def run(state, *a, **kw):
        t0 = time.perf_counter()
        out = fn(state, *a, **kw) or {}
        dt = round(time.perf_counter() - t0, 3)
        for s in out.get("trace", []):
            s.setdefault("latency_s", dt)
        return out
    return run


def format_step(i: int, s: dict) -> str:
    """'3. [Critic Agent] APPROVE: ... | in: ... | gemini:gemini-3.5-flash 1.43s' (parsed back by ticket_source)."""
    if isinstance(s, (tuple, list)):                       # legacy (agent, note) pairs
        return f"{i}. [{s[0]}] {s[1]}"
    meta = s.get("model", "deterministic")
    if s.get("latency_s") is not None:
        meta += f" {s['latency_s']:.2f}s"
    if s.get("fallback") not in (None, "none"):
        meta += f" (fallback: {s['fallback']})"
    inp = f" | in: {s['input']}" if s.get("input") else ""
    return f"{i}. [{s['agent']}] {s['output']}{inp} | {meta}"


def as_note(s) -> tuple[str, str]:
    """(agent, text) for ticket sources that store one note per step."""
    if isinstance(s, (tuple, list)):
        return s[0], s[1]
    return s["agent"], format_step(0, s).split("] ", 1)[1]
