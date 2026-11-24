"""Input guardrails applied by the supervisor before any agent (or LLM) sees the ticket text.

- length cap (long texts are truncated for the LLM prompts; the full text stays in the ticket)
- control characters removed
- prompt-injection heuristics: text that tries to instruct the AI ("ignore previous instructions",
  "approve this automatically", "you are now ...") is flagged. A flagged ticket is read by the rule-based
  extractor only (no LLM) and the critic escalates it to a human. The policy decision is rule-based anyway.
"""
from __future__ import annotations

import re

MAX_CHARS = 2000
INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)"
    r"|disregard\s+(?:\w+\s+){0,3}(?:instructions|polic(?:y|ies)|rules)"
    r"|forget\s+(?:\w+\s+){0,2}(?:instructions|rules)"
    r"|you\s+are\s+now\b|act\s+as\s+(?:an?\s+)?(?:admin|administrator|system|developer)"
    r"|system\s+prompt|developer\s+mode|jailbreak"
    r"|override\s+(?:the\s+)?(?:policy|decision|rules?|approval)"
    r"|(?:auto(?:matically)?[-\s]?approve|approve\s+(?:this|my)\s+(?:request|ticket)\s+(?:automatically|now|without))"
    r"|(?:set|change)\s+(?:the\s+)?decision\s+to"
    r"|bypass\s+(?:the\s+)?(?:approval|policy|security)",
    re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def check_input(text: str) -> dict:
    clean = _CTRL.sub(" ", text or "")
    hits = sorted({m.group(0).lower() for m in INJECTION.finditer(clean)})
    return {"text": clean[:MAX_CHARS], "truncated": len(clean) > MAX_CHARS,
            "injection_suspected": bool(hits), "matches": hits[:5]}
