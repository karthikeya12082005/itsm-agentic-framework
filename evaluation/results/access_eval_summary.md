# Objective 2 - access request evaluation

Generated 2026-11-25T11:54:42 by `python -m evaluation.eval_access`.

## Dataset test split (access / service requests)

| Setting | Value |
|---|---|
| Tickets | 77 (ALLOW_WITH_APPROVAL 36, ALLOW 17, DENY 12, ESCALATE_FOR_REVIEW 12) |
| Rule engine, ticket's own policy: decision exact match | 0.844 |
| ... excluding ESCALATE_FOR_REVIEW labels | 1.000 |
| Hybrid retrieval: policy Hit@1 | 0.688 |
| Rule engine on the retrieved policy: decision exact match | 0.701 |

ESCALATE_FOR_REVIEW labels come from dataset scenarios {'escalated_no_permitted_action': 12} (not derivable from policy + role).

## Catalogue requests through the multi-agent graph (evaluation/access_requests.csv)

| Metric | rules | agentic |
|---|---|---|
| Requests | 52 | 52 |
| Target accuracy | 0.981 | 0.981 |
| Decision exact match | 0.981 | 0.981 |
| Final-state match | 0.981 | 0.981 |
| Automation rate | 0.846 | 0.827 |
| Escalation rate | 0.135 | 0.154 |
| Approval rate | 0.288 | 0.288 |
| Verification pass rate | 1.0 | 1.0 |
| MTTA (s, batch) | 0.02 | 0.02 |
| MTTR (s, batch, incl. approval) | 0.43 | 4.35 |
| Mean processing time (s) | 0.37 | 3.89 |
| LLM fallback share | 1.0 | 0.306 |
| Final-state mismatches | R51 | R51 |

Simulated endpoint (every action succeeds and verifies); approvals given by a valid approver. The live endpoint and ServiceNow are tested separately (CLAUDE.md Phase 3).
