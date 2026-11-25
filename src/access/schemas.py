"""JSON schemas (pydantic) of every LLM output in the access flow. The router validates each answer against
its schema; an answer that does not fit is repaired once, then the next model, then the rule-based fallback."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AccessExtraction(BaseModel):
    """Understanding Agent: what is requested (never whether it is allowed)."""
    request_kind: Literal["website_access", "software_install", "other"]
    target: str = Field("", max_length=200,
                        description="catalogue value if the request refers to a known target, else the domain or "
                                    "software name exactly as written; empty if nothing is requested")
    justification: str = Field("", max_length=400, description="business reason in one sentence, or empty")
    duration_hours: float | None = Field(None, ge=0, le=8760,
                                         description="how long access is needed, in hours, if the employee says "
                                                     "so (e.g. 'for 2 days' = 48); null if not stated")


class QueryRewrite(BaseModel):
    """Retrieval Agent: request rewritten as a knowledge-base search query."""
    query: str = Field(..., min_length=3, max_length=300)


class CriticReview(BaseModel):
    """Critic Agent: review of the plan before anything is executed. Can only approve, escalate or block."""
    verdict: Literal["APPROVE", "ESCALATE", "BLOCK"]
    concerns: list[str] = Field(default_factory=list, max_length=6)
    rationale: str = Field("", max_length=600)


class Message(BaseModel):
    """Communication Agent: message to the employee and a one-line internal work note."""
    user_message: str = Field(..., min_length=20, max_length=700)
    work_note: str = Field("", max_length=400)
