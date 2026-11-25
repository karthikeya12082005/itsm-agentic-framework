# Objective 3 - incident resolution evaluation

Generated 2026-11-25T20:27:29 by `python -m evaluation.eval_incident`.

## A. Injected faults in the simulated services (11 fault types)

| Metric | rules | agentic |
|---|---|---|
| Runs (faults x reps) | 22 | 22 |
| Root-service accuracy | 1.0 | 1.0 |
| RCA top-1 | 1.0 | 1.0 |
| RCA top-3 | 1.0 | 1.0 |
| Remediation action correct | 1.0 | 1.0 |
| Correct final outcome | 1.0 | 1.0 |
| Resolution rate | 0.909 | 0.909 |
| Escalation rate | 0.091 | 0.091 |
| Needed approval | 0.455 | 0.455 |
| MTTR mean (s, injection -> verified) | 20.6 | 23.0 |
| MTTR median (s) | 2.8 | 5.6 |
| Rollbacks | 0 | 0 |
| Lab healthy after RESOLVED | 1.0 | 1.0 |
| Wrong final outcome |  |  |

## B. Dataset test incidents: root_cause_category from creation-time text

| Method | n | Top-1 | Top-3 |
|---|---|---|---|
| Prior P(rc | category), train split | 194 | 0.196 | 0.521 |
| Naive Bayes, learned likelihoods | 194 | 0.268 | 0.588 |
| Naive Bayes + k-NN prior (hybrid retrieval) | 194 | 0.263 | 0.598 |
| Naive Bayes + k-NN + LLM hypothesis | 60 | 0.283 | 0.633 |

k-NN majority vote alone: top-1 0.325; LLM alone: top-1 0.255; LLM accuracy on a train sample (calibration) 0.267 -> alpha 0.267.
