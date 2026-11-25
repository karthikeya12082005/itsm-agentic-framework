# Final ablations (TODO 5.1)

Generated 2026-11-25T17:16:14 by `python -m evaluation.ablations`.

Variants: **rules** = LLM off (rule-based path of every agent); **multi** = per-agent models from .env; **single(model)** = that one model for every agent (no secondary model: a rate-limited call falls back to rules); **-noret** = no hybrid retrieval. The policy decision is rule-based in every variant (design rule).

## A. Access requests (52 labelled catalogue requests, full graph, simulated endpoint)

| Metric | rules | rules-noret | multi | multi-noret | single(openai/gpt-oss-20b) | single(qwen/qwen3.8-27b) |
|---|---|---|---|---|---|---|
| Target accuracy | 0.981 | 0.981 | 0.981 | 0.981 | 0.981 | 0.962 |
| Decision exact match | 0.981 | 0.981 | 0.981 | 0.981 | 0.981 | 0.962 |
| Final-state match | 0.981 | 0.981 | 0.981 | 0.981 | 0.981 | 0.981 |
| Automation rate | 0.846 | 0.846 | 0.827 | 0.827 | 0.846 | 0.827 |
| Escalation rate | 0.135 | 0.135 | 0.154 | 0.154 | 0.135 | 0.154 |
| Approval rate | 0.288 | 0.288 | 0.288 | 0.288 | 0.288 | 0.288 |
| Verification pass rate | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| MTTR (s, batch) | 0.5 | 0.11 | 4.74 | 2.19 | 1.39 | 3.51 |
| Mean processing time (s) | 0.42 | 0.1 | 4.37 | 1.93 | 1.22 | 3.12 |
| Final-state mismatches | R51 | R51 | R51 | R51 | R51 | R51 |
| LLM calls answered by a model | - (no LLM) | - (no LLM) | 0.84 (194 calls) | 0.791 (148 calls) | 0.205 (195 calls) | 0.812 (192 calls) |
| Wall time (min) | 0.4 | 0.1 | 9.2 | 7.2 | 6.3 | 13.3 |

## B. Incident resolution (8 injected faults in the simulated services)

| Metric | rules | rules-noret | multi | multi-noret | single(openai/gpt-oss-20b) | single(qwen/qwen3.8-27b) |
|---|---|---|---|---|---|---|
| Incidents | 8 | 8 | 8 | 8 | 8 | 8 |
| Root-service accuracy | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| RCA top-1 | 1.0 | 1.0 | 1.0 | 1.0 | 0.875 | 1.0 |
| RCA top-3 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Remediation action correct | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Resolution rate | 1.0 | 1.0 | 1.0 | 1.0 | 0.875 | 1.0 |
| Escalation rate | 0.0 | 0.0 | 0.0 | 0.0 | 0.125 | 0.0 |
| MTTR mean (s) | 19.8 | 18.2 | 21.6 | 21.7 | 24.0 | 21.4 |
| MTTR median (s) | 15.8 | 14.4 | 17.6 | 14.4 | 7.5 | 16.2 |
| Rollbacks | 0 | 0 | 0 | 0 | 0 | 0 |
| Not resolved | none | none | none | none | memory_leak#1:ESCALATED | none |
| LLM calls answered by a model | - (no LLM) | - (no LLM) | 0.5 (28 calls) | 0.4 (25 calls) | 1.0 (38 calls) | 0.379 (29 calls) |
| Wall time (min) | 6.7 | 6.0 | 9.7 | 9.8 | 10.0 | 7.9 |

## C. Retrieval Agent: raw request text vs. LLM query rewrite

48 catalogue requests with a known target; gold = the target's policy and its SOP; hybrid retrieval with category filter. The LLM rewrote 48 of them (the rest fell back to the raw text).

| Query | SOP Hit@1 | SOP Hit@3 | SOP MRR@10 | Policy Hit@1 | Policy Hit@3 | Policy MRR@10 |
|---|---|---|---|---|---|---|
| raw text | 0.438 | 0.875 | 0.651 | 0.312 | 0.896 | 0.566 |
| LLM rewrite | 0.604 | 0.896 | 0.764 | 0.417 | 0.854 | 0.633 |

## D. Per-provider latency and reliability (every LLM attempt in logs/llm_calls.jsonl)

3085 logged agent calls from 2026-11-24T23:18:48 to 2026-11-25T17:15:23 (development, live runs and evaluations on free tiers).

| Provider:model | Attempts | Called | Answered | Success of called | Median s | p90 s | Mean s | Failures |
|---|---|---|---|---|---|---|---|---|
| gemini:gemini-3.5-flash | 220 | 48 | 23 | 0.479 | 2.01 | 6.87 | 3.01 | http_429 17, skipped_cooldown 172, timeout 8 |
| gemini:gemini-3.5-flash-lite | 627 | 327 | 293 | 0.896 | 1.46 | 4.61 | 2.04 | http_429 1, http_503 25, invalid_output 1, other 1, skipped_cooldown 300, timeout 6 |
| groq:openai/gpt-oss-120b | 723 | 318 | 271 | 0.852 | 0.99 | 1.46 | 1.12 | http_429 46, skipped_cooldown 405, timeout 1 |
| groq:openai/gpt-oss-20b | 643 | 489 | 481 | 0.984 | 0.92 | 1.11 | 0.9 | http_429 4, invalid_output 3, other 1, skipped_cooldown 154 |
| groq:qwen/qwen3.8-27b | 633 | 570 | 562 | 0.986 | 0.78 | 1.06 | 0.82 | http_429 5, invalid_output 1, skipped_cooldown 63, timeout 2 |
| ollama:llama3.2:3b | 5 | 5 | 5 | 1.0 | 4.14 | 5.51 | 4.34 | - |

Not shown: groq:no-such-model-xyz (deliberately invalid model id used to test the 404 fallback).

Per agent, calls with an LLM configured (final outcome of each call): communication 477 primary / 10 secondary / 74 rule-based; critic 75 primary / 109 secondary / 133 rule-based; rca 181 primary / 152 secondary / 209 rule-based; retrieval 173 primary / 123 secondary / 45 rule-based; understanding 335 primary / 0 secondary / 53 rule-based.
