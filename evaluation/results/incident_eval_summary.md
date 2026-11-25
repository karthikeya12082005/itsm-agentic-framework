# Objective 3 - incident resolution evaluation

Generated 2026-11-25T13:49:13 by `python -m evaluation.eval_incident`.

## A. Injected faults in the simulated services (8 fault types)

| Metric | rules | agentic |
|---|---|---|
| Runs (faults x reps) | 16 | 16 |
| Root-service accuracy | 1.0 | 1.0 |
| RCA top-1 | 0.875 | 0.812 |
| RCA top-3 | 1.0 | 1.0 |
| Remediation action correct | 0.875 | 0.875 |
| Resolution rate | 0.75 | 0.75 |
| Escalation rate | 0.25 | 0.25 |
| Needed approval | 0.375 | 0.312 |
| MTTR mean (s, injection -> verified) | 18.8 | 28.8 |
| MTTR median (s) | 13.7 | 25.6 |
| Rollbacks | 0 | 0 |
| Lab healthy after RESOLVED | 1.0 | 1.0 |
| Not resolved | high_latency#1:ESCALATED, bad_config#1:ESCALATED, high_latency#2:ESCALATED, bad_config#2:ESCALATED | high_latency#1:ESCALATED, bad_config#1:ESCALATED, high_latency#2:ESCALATED, bad_config#2:ESCALATED |
