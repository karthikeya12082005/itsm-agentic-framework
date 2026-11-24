# CLAUDE.md — ITSM Agentic Framework (project context for Claude Code)

## Project
B.E. CSE major project, CBIT Hyderabad: **"A Multi-Agent Framework for Intelligent Enterprise IT Incident
Management, Root Cause Analysis, and Policy-Aware Automated Resolution"**.
Authors: Karre Sai Karthik, Kasam Revan Sai Karthikeya. Supervisor: Dr. I. Srujana.
GitHub: karthikeya12082005 / itsm-agentic-framework (private).

## Objectives and status
1. **Synthetic datasets** (DONE) — generated on Kaggle with Mistral-7B-Instruct-v0.3 (4-bit NF4, 2x T4).
   "Python decides structure, LLM writes text"; deterministic validation; final validation PASS.
   Files in `data/raw/`: historical_incidents.csv (1500 tickets, 68 cols), incident_sops.csv (60, 22 cols),
   incident_policies.csv (60, 24 cols incl. default_decision), data_dictionary.md, validation_report.md/.json.
   Known: ~175 thin RCA/execution texts and 10 query-leak tickets (kept in train only). User chose to keep the data as is.
2. **Policy-aware access-request automation on a restricted Windows endpoint** (WORKING, demoed).
   Employee raises a ticket (website / AI tool / software). Agents decide ALLOW / DENY / ALLOW_WITH_APPROVAL /
   ESCALATE automatically; a human is involved only for ALLOW_WITH_APPROVAL. ChatGPT = DENY.
3. **Incident resolution on simulated services** (NOT STARTED) — simulated services + fault injector,
   Bayesian root-cause ranking, SOP-based remediation, predicate verification with bounded retry, rollback.

## REQUIRED scope for the final system (user's decision)
- **ServiceNow is REQUIRED, not optional.** ServiceNow PDI (dev195312, Brazil release) is the system of record for
  tickets: employees raise requests/incidents in ServiceNow; the agents read them via the REST Table API, write
  work notes, set state, and handle approvals (sysapproval_approver). The local SQLite desk (src/access/store.py)
  is only a fallback/test double behind the same interface. First task: fix the 401 auth for user ai.agent
  (role: Internal Integration User + itil + approval_admin; 24-char alphanumeric password; single-quoted in .env).
  Catalog items already scripted in src/integrations/servicenow_setup.py: "Request website or AI tool access",
  "Request software installation".
- **Agentic AI with LangGraph and MULTIPLE LLMs is REQUIRED.** It is a multi-agent system: each agent is a
  LangGraph node/subgraph with its own role, tools and its own configured LLM. Use agentic concepts explicitly:
  supervisor/orchestrator routing, tool calling, ReAct-style diagnosis loops, shared typed state, checkpointing
  (persist graph state so approvals can pause/resume), human-in-the-loop interrupts for approvals,
  a critic/reflection step before execution, guardrails, and full traceability of every agent step.
- **LLM per agent** (configurable in one place, e.g. `src/common/llm_router.py` + `.env`; provider-agnostic
  wrapper supporting Gemini API, Groq (Llama models) and local Ollama; verify current model IDs before use):
  - Understanding Agent: small fast model (extraction/classification to strict JSON schema).
  - Retrieval Agent: query rewriting / optional LLM re-ranking on top of BM25 + dense + RRF.
  - RCA Agent (Objective 3): stronger reasoning model generating hypotheses; combined with Bayesian ranking.
  - Policy & Risk Agent: deterministic rule decides; an LLM only writes the justification.
  - Critic/Verification support: a *different* model reviews the plan (reflection) — never overrides rules.
  - Communication Agent: small model for user/work-note messages.
  Every LLM call must have a rule-based fallback, JSON validation, timeouts, and be logged (agent, model, latency).
- Final evaluation must compare: single LLM vs multi-LLM, with vs without retrieval, rules-only vs agentic,
  plus decision exact match, automation rate, escalation rate, MTTA/MTTR, RCA top-1/top-3 accuracy.

## Design rules (do not break)
- The LLM never makes the policy decision. Policy & Risk, Execution and Verification are deterministic code;
  the LLM only extracts/explains. Rule-based fallback must always work without an API key.
- Policy rule (report eq. 3.7): ESCALATE_FOR_REVIEW if target not in catalogue; DENY if policy prohibits or role
  not eligible; ALLOW_WITH_APPROVAL if approval required or risk >= HIGH; else ALLOW.
  Risk = max(policy risk, endpoint criticality LOW).
- Verification: predicate check on real endpoint state, MAX_ATTEMPTS = 2, then ESCALATE (never fake RESOLVED).
- Endpoint changes only through the enforcement service's fixed action catalogue; every action audit-logged with
  action_id, ticket_id, authorization_id and a rollback spec.
- Report algorithms (keep consistent): BM25, cosine similarity, RRF, Bayesian root-cause ranking, rule-based policy decision.

## Environment
- Windows 11 Home Single Language. Project root: `D:\Documents\college\Major Project\itsm-agentic-framework`
  (path has a space — always quote it).
- Python 3.12.5, venv `.venv` (`.\.venv\Scripts\Activate.ps1`). Use `python`, not `py -3.11`.
- Restricted local standard user: `employee01` (role DEVELOPER, manager manager01). Admin user: Karthikeya.
- Neon serverless PostgreSQL 18.6 + pgvector 0.8.6 (Singapore).
- `.env` (NEVER commit, never print values; single-quoted values): DATABASE_URL, ENDPOINT_TOKEN (>=24 chars),
  ENDPOINT_URL, optional GEMINI_API_KEY, GEMINI_MODEL_LITE, SERVICENOW_INSTANCE, SERVICENOW_USER='ai.agent',
  SERVICENOW_PASSWORD. Several secrets were exposed in screenshots earlier and should be rotated.

## Code layout
- `src/common/config.py` — paths, EMBED_MODEL=BAAI/bge-small-en-v1.5 (384-d, query instruction prefix), SEED=42,
  TEST_FRACTION=0.20, RRF_K=60, BM25_K1=1.2, BM25_B=0.75, WEAK_EVIDENCE_COSINE=0.55. `check_db.py`.
- `src/common/llm.py` — `gemini_json(prompt, model=None, timeout=30)` via REST generateContent; returns None on failure.
- `src/knowledge/` — prepare.py (jsonl + stratified split: 1199 train / 301 test), bm25.py, embeddings.py,
  schema.sql (kb_incidents / kb_sops / kb_policies, vector(384), HNSW cosine), ingest.py, retriever.py
  (PgVectorIndex, rrf_fuse, HybridRetriever: bm25|dense|hybrid, category filter, CANDIDATES=50), eval_retrieval.py.
- `endpoint_service/` — FastAPI on 127.0.0.1:8765, MUST run in an Administrator terminal; X-Endpoint-Token header.
  endpoint_config.py (MANAGED_DOMAINS, APPROVED_PACKAGES), registry_policy.py (reg load/unload of employee01
  NTUSER.DAT, Chrome URLBlocklist/URLAllowlist under HKEY_USERS\<SID>), winget_actions.py (machine scope;
  is_installed uses `--scope machine`), server.py (/health, /status, /verify/domain/{d}, /verify/software/{pkg},
  /actions/allowlist_domain, /actions/revoke_domain, /actions/install_software, /actions/rollback),
  audit at endpoint_service/audit/actions.jsonl, test_endpoint.py (allow / rollback <action_id> / negative).
  scripts/: create_employee01.ps1, apply_employee_policies.ps1, reset_employee_policies.ps1.
- `src/access/` — catalog.py (TARGETS -> policy, ALTERNATIVES, SOFTWARE_KEYWORDS, EMPLOYEES), store.py (SQLite
  data/tickets.db, RITM9000001+), endpoint_client.py, agents.py (understanding, retrieval, decide, policy_risk,
  request_approval, execution, verification, communication), graph.py (LangGraph StateGraph; START routes new /
  resume-approved / resume-rejected), cli.py (new, process [--all], approve [--reject] [--by], show, list, demo,
  --no-retrieval).
- `src/integrations/` — ServiceNow PDI dev195312 scripts: servicenow_setup.py (admin via getpass; checks users,
  group, roles; creates the two catalog items; `--test-order` via order_now), check_sn_auth.py, test_servicenow.py.
  Was paused because of a 401 for ai.agent — now REQUIRED; resume here.
- Full history of decisions and problems solved so far: `docs/PROJECT_HISTORY.md` (read it).

## Catalogue (src/access/catalog.py)
chatgpt.com, chat.openai.com, openai.com, claude.ai, perplexity.ai -> POL-0030 (DENY, alternative Gemini);
gemini.google.com -> POL-0028 (ALLOW); github.com, pypi.org -> POL-0025 (ALLOW_WITH_APPROVAL, SECURITY_TEAM);
wetransfer.com, mega.nz -> POL-0027 (DENY); Google.Chrome -> POL-0017 (ALLOW); Microsoft.VisualStudioCode ->
POL-0018 (ALLOW for technical roles); Git.Git, Python.Python.3.12, Postman.Postman -> POL-0019 (AWA, LINE_MANAGER).

## Commands
```powershell
# Admin terminal: endpoint service
python -m uvicorn endpoint_service.server:app --host 127.0.0.1 --port 8765
# Normal terminal
python -m src.common.check_db
python -m src.knowledge.eval_retrieval [--category]
python -m src.access.cli demo | new "<text>" | process --all | approve <RITM> [--reject] | show <RITM> | list
# Restore restrictions (Admin, employee01 signed out)
powershell -ExecutionPolicy Bypass -File .\endpoint_service\scripts\reset_employee_policies.ps1
powershell -ExecutionPolicy Bypass -File .\endpoint_service\scripts\apply_employee_policies.ps1
```

## Results so far (keep consistent with report)
Retrieval, 301 test tickets (SOP Hit@1/Hit@3/MRR@10; Policy Hit@1/Hit@3/MRR@10; Incident P@5):
- no filter: BM25 .821/.920/.873; .757/.887/.827; .960 | Dense .867/.947/.908; .764/.904/.837; .958 |
  Hybrid .851/.930/.897; .814/.920/.871; .966
- category filter: BM25 .877/.964/.923; .844/.973/.906; .973 | Dense .904/.990/.946; .807/.980/.887; .976 |
  Hybrid .887/.980/.934; .857/.980/.917; .980
Access demo: ChatGPT DENY -> CLOSED_REJECTED; Gemini ALLOW -> RESOLVED (~1 s); GitHub AWAITING_APPROVAL ->
approved -> RESOLVED; VS Code install FAILED x2 -> ESCALATED (installer aborts when VS Code is running).
Chrome must be restarted for employee01 to pick up new policies.

## Known issues
- VS Code installs fail while VS Code is running (installer cancels silently).
- Approver identity is not checked (manager01 approved a SECURITY_TEAM item).
- k-NN confidence is low for real AI tool names (dataset uses fictional names) — decisions still correct.
- HF Hub "unauthenticated requests" warning (set HF_TOKEN).

---------------------------------------------------------------------------------------------------------------
# MASTER TODO LIST (the project must work perfectly end to end)
Rules for Claude: work phase by phase, in order. Before each item explain what you will do; after it, run the
listed test and show the output. Tick `[x]` here only when the acceptance test passes on the user's machine.
Never mark an item done on assumptions. Keep this list updated (add items you discover). Commit after each phase.

## Phase 0 — Accounts, API keys and environment (guide the user step by step; see "API KEY GUIDE" below)
- [ ] 0.1 Create `.env.example` (all variable names, no values) and make sure `.env` is in .gitignore.
- [ ] 0.2 Rotate exposed secrets: Neon password, ENDPOINT_TOKEN, ServiceNow admin + ai.agent passwords; delete
      Desktop apikey.txt after moving its value into .env. Test: check_db OK, endpoint /health OK.
- [ ] 0.3 Gemini API key. Test: `python -m src.common.check_llms` returns JSON from Gemini.
- [ ] 0.4 Groq API key. Test: same script returns JSON from Groq.
- [ ] 0.5 Ollama installed + one small local model pulled. Test: same script returns JSON from Ollama.
- [ ] 0.6 HF_TOKEN (read-only) to remove the HF warning. Test: no warning on eval_retrieval.
- [ ] 0.7 ServiceNow PDI awake and reachable; ai.agent login works. Test: `python -m src.integrations.check_sn_auth` = 200.
- [ ] 0.8 `.gitignore` contains .env, data/tickets.db, checkpoint DBs, logs, audit jsonl. `pip install -r requirements.txt` clean.

## Phase 1 — ServiceNow integration (REQUIRED)
- [ ] 1.1 `src/integrations/servicenow_client.py`: auth, retries, timeouts, pagination; get/update records,
      add work note, add comment, set state, query approvals. Unit test with a mocked session + live smoke test.
- [ ] 1.2 Run servicenow_setup.py: users (employee01, manager01, ai.agent), group "IT Security"/SECURITY_TEAM and
      line manager mapping, catalog items "Request website or AI tool access" and "Request software installation".
- [ ] 1.3 Ticket source abstraction: `TicketSource` interface with `ServiceNowSource` (default) and `LocalSource`
      (SQLite, tests only). Agents never call ServiceNow directly.
- [ ] 1.4 Poller/worker: picks up new RITMs (and later INCs), maps variables -> agent state, runs the graph,
      writes every agent step as a work note, final message as a customer-visible comment, sets state
      (Closed Complete / Closed Incomplete / Work in Progress / Pending approval).
- [ ] 1.5 Approvals in ServiceNow: create sysapproval_approver for the correct approver (role from policy),
      wait, resume the graph on approve/reject. Only the approver of the required group/role may approve.
- [ ] 1.6 End-to-end test: order each catalog item as employee01 in the PDI UI -> ChatGPT DENY, Gemini ALLOW,
      GitHub approval by the SECURITY_TEAM member -> RESOLVED; endpoint changes verified; notes visible in PDI.

## Phase 2 — Multi-agent LangGraph with multiple LLMs (REQUIRED)
- [ ] 2.1 `src/common/llm_router.py`: providers Gemini / Groq / Ollama behind one interface; per-agent model map
      from `.env`; strict JSON-schema output (pydantic) with repair/retry; timeouts; fallback chain
      (primary -> secondary provider -> rule-based); log agent, provider, model, latency, tokens, fallback used.
- [ ] 2.2 `src/common/check_llms.py`: pings every configured provider/model.
- [ ] 2.3 Supervisor/orchestrator node routing between agents (access-request flow vs incident flow).
- [ ] 2.4 Understanding Agent uses LLM extraction (schema-validated) with rule-based fallback.
- [ ] 2.5 Retrieval Agent: LLM query rewriting (optional flag) before BM25 + dense + RRF.
- [ ] 2.6 Critic/Reflection Agent with a DIFFERENT model: reviews proposed plan vs SOP/policy; can only block or
      escalate, never override the rule-based decision.
- [ ] 2.7 Communication Agent uses LLM for user messages and work notes (fallback templates).
- [ ] 2.8 LangGraph checkpointer (SQLite or Postgres) + `interrupt` for human approval; resume by thread_id = ticket.
- [ ] 2.9 Trace: every node writes {agent, model, input summary, output, latency} to state and to ServiceNow notes.
- [ ] 2.10 Tests: graph unit tests with fake LLMs; one run per provider; fallback test with network off.

## Phase 3 — Objective 2 hardening and evaluation
- [ ] 3.1 Pre-check running process before software install (Code.exe, chrome.exe, Postman.exe...): clear user
      message "close X and resubmit" instead of escalation; test with VS Code open.
- [ ] 3.2 Approver role enforcement (also in ServiceNow).
- [ ] 3.3 Revocation / time-limited access (duration variable) with automatic rollback when expired.
- [ ] 3.4 Unknown target -> ESCALATE_FOR_REVIEW; test with a domain not in the catalogue.
- [ ] 3.5 `evaluation/eval_access.py`: batch of labelled requests (incl. dataset access tickets) -> decision exact
      match, automation rate, escalation rate, approval rate, MTTA, MTTR, verification pass rate; save tables.

## Phase 4 — Objective 3: incident resolution on simulated services
- [ ] 4.1 Simulated services (e.g. FastAPI web app, worker queue, Postgres/SQLite DB, cache) with health
      endpoints, metrics and logs, run locally (docker optional; must work on Windows Home without admin).
- [ ] 4.2 Fault injector: service down, high latency, memory leak, disk full (simulated), bad config, expired
      certificate (simulated), DB connection pool exhausted — each mapped to dataset root_cause_category and SOP.
- [ ] 4.3 Monitoring -> auto-create ServiceNow INCIDENT with symptoms/observed state/diagnostic data.
- [ ] 4.4 RCA Agent: ReAct diagnosis loop with tools (check health, read logs, read metrics) + LLM hypotheses +
      Bayesian root-cause ranking (priors from historical_incidents, likelihoods from evidence).
- [ ] 4.5 Remediation via SOP actions (fixed action catalogue: restart service, rollback config, clear cache,
      scale worker, rotate cert...), policy check, critic review, execution, predicate verification,
      retry <= 2, rollback on failure, escalate otherwise.
- [ ] 4.6 Evaluation: RCA top-1/top-3 on injected faults and on dataset test tickets (root_cause_category), MTTR,
      resolution rate, rollback correctness.

## Phase 5 — Final evaluation, UI and demo
- [ ] 5.1 Ablations: single LLM vs multi-LLM; with vs without retrieval; rules-only vs agentic; per-provider latency.
- [ ] 5.2 Simple dashboard (Streamlit or FastAPI page) showing tickets, agent trace, metrics.
- [ ] 5.3 One-command demo scripts (`scripts/demo_access.ps1`, `scripts/demo_incident.ps1`) + reset script.
- [ ] 5.4 README with setup, architecture diagram, run steps; update CLAUDE.md and docs/PROJECT_HISTORY.md.
- [ ] 5.5 Full clean-machine run-through following README only; fix anything that fails.

---------------------------------------------------------------------------------------------------------------
# API KEY / ACCOUNT GUIDE
Rules for Claude: whenever a key/account is needed, give the user numbered click-by-click steps (website, button
names, what to copy), tell them the exact `.env` variable name, and give a test command. Never ask the user to
paste a key into the chat. Values in .env are single-quoted. Websites change — if a step doesn't match what the
user sees, ask for a screenshot and adapt.

| Variable | What | How to get it |
|---|---|---|
| DATABASE_URL | Neon Postgres | console.neon.tech -> project -> Connect -> copy connection string (psycopg, sslmode=require). Reset password: Roles -> reset. |
| ENDPOINT_TOKEN | secret for endpoint service | generate locally: `-join ((48..57)+(65..90)+(97..122) \| Get-Random -Count 40 \| % {[char]$_})` |
| ENDPOINT_URL | `'http://127.0.0.1:8765'` | fixed |
| GEMINI_API_KEY | Google Gemini | aistudio.google.com -> sign in -> Get API key -> Create API key -> copy |
| GROQ_API_KEY | Groq (Llama models) | console.groq.com -> sign in -> API Keys -> Create API Key -> copy (shown once) |
| OLLAMA_BASE_URL | local Ollama | install from ollama.com (Windows installer), then `ollama pull <small model>`; value `'http://localhost:11434'` |
| HF_TOKEN | Hugging Face (read) | huggingface.co -> Settings -> Access Tokens -> Create new token -> type Read |
| SERVICENOW_INSTANCE | PDI URL | developer.servicenow.com -> My Instance (wake it if hibernating) -> `'https://devXXXXXX.service-now.com'` |
| SERVICENOW_USER / SERVICENOW_PASSWORD | integration user | in PDI: sys_user_list.do -> ai.agent -> Set Password (24-char alphanumeric) -> Save |
| SERVICENOW_ADMIN_PASSWORD | only for setup scripts | developer portal -> My Instance -> Manage instance password; remove from .env after setup |
| LLM_UNDERSTANDING / LLM_RETRIEVAL / LLM_RCA / LLM_CRITIC / LLM_COMMUNICATION | per-agent model, format `provider:model` | choose from the providers' current model lists (verify IDs with check_llms.py) |

Free-tier limits apply (rate limits): the router must back off and fall back; never block the pipeline on an LLM.

---------------------------------------------------------------------------------------------------------------
## Working style the user wants
- Very clear numbered steps, exact commands, say which terminal (Admin vs normal). Give full code, not fragments.
- Step-by-step guidance for every account/API key needed (see API KEY GUIDE).
- Test changes before saying they work; show the test output. Never commit or print secrets.
- Keep the MASTER TODO LIST in this file up to date and commit after each phase.
