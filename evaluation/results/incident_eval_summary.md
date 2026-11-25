# Objective 3 - incident resolution evaluation

Generated 2026-11-25T15:12:41 by `python -m evaluation.eval_incident`.

## A. Injected faults in the simulated services (8 fault types)

| Metric | rules | agentic |
|---|---|---|
| Runs (faults x reps) | 16 | 16 |
| Root-service accuracy | 1.0 | 1.0 |
| RCA top-1 | 1.0 | 0.938 |
| RCA top-3 | 1.0 | 1.0 |
| Remediation action correct | 1.0 | 1.0 |
| Resolution rate | 1.0 | 1.0 |
| Escalation rate | 0.0 | 0.0 |
| Needed approval | 0.5 | 0.438 |
| MTTR mean (s, injection -> verified) | 18.4 | 25.8 |
| MTTR median (s) | 13.7 | 20.8 |
| Rollbacks | 0 | 0 |
| Lab healthy after RESOLVED | 1.0 | 1.0 |
| Not resolved |  |  |

## B. Dataset test incidents: root_cause_category from creation-time text

| Method | n | Top-1 | Top-3 |
|---|---|---|---|
| Prior P(rc | category), train split | 194 | 0.196 | 0.521 |
| Naive Bayes, learned likelihoods | 194 | 0.268 | 0.588 |
| Naive Bayes + k-NN prior (hybrid retrieval) | 194 | 0.263 | 0.598 |
| Naive Bayes + k-NN + LLM hypothesis | 60 | 0.283 | 0.633 |

k-NN majority vote alone: top-1 0.325; LLM alone: top-1 0.255; LLM accuracy on a train sample (calibration) 0.267 -> alpha 0.267.
