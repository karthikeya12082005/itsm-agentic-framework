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
- **ServiceNow is REQUIRED, not optional.** ServiceNow PDI (**dev219979**, Brazil release; replaced the old
  dev195312 in Nov 2026 — the new instance started EMPTY) is the system of record for tickets. The agents read
  tickets via the REST Table API, write work notes, set state, and record approvals on the incident.
  The local SQLite desk (src/access/store.py) is only a fallback/test double behind the same interface.
  Integration user ai.agent (Internal Integration User + itil + approval_admin + snc_basic_auth_api_access).
  **LEAN DESIGN (user's decision, Nov 24 2026):** the **Incident table for everything** — access requests are
  incidents with category `access` (built-in "Access & Password"), free text, caller employee01, assignment group
  "AI Service Desk"; Objective 3 incidents use the normal categories. NO catalog items. Human users (employee01,
  manager01, security01) are **records without logins** (no passwords). **Approvals are given through the CLI**
  (`approve <INC> --by <user>`, approver checked in code against ServiceNow: LINE_MANAGER = caller's manager,
  SECURITY_TEAM = member of group "IT Security") and **recorded on the incident** by ai.agent: approval field
  requested -> approved/rejected, state On Hold while waiting, work notes naming approver, group and policy
  (user's choice; sysapproval_approver rows are impossible on incidents for non-admins, see auth facts).
  ITIL note for the report: access requests are tagged category=access; a Service Catalog front end is future work.
- **Agentic AI with LangGraph and MULTIPLE LLMs is REQUIRED.** It is a multi-agent system: each agent is a
  LangGraph node/subgraph with its own role, tools and its own configured LLM. Use agentic concepts explicitly:
  supervisor/orchestrator routing, tool calling, ReAct-style diagnosis loops, shared typed state, checkpointing
  (persist graph state so approvals can pause/resume), human-in-the-loop interrupts for approvals,
  a critic/reflection step before execution, guardrails, and full traceability of every agent step.
- **LLM per agent** (configurable in one place, e.g. `src/common/llm_router.py` + `.env`; provider-agnostic
  wrapper supporting Gemini API, Groq (gpt-oss / Qwen models — Groq dropped Llama chat models) and local
  Ollama; verify current model IDs before use with `python -m src.common.check_llms --list`):
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
- Hardware: i5-12450H, 7.6 GB RAM (tight), NVIDIA RTX 2050 4 GB + Intel UHD, C: ~6 GB free (install big things
  on D:), D: ~190 GB free.
- Python 3.12.5, venv `.venv` (`.\.venv\Scripts\Activate.ps1`). Use `python`, not `py -3.11`.
- Restricted local standard user: `employee01` (role DEVELOPER, manager manager01). Admin user: Karthikeya.
- Neon serverless PostgreSQL 18.7 (was 18.6; Neon upgrades minor versions) + pgvector 0.8.6 (Singapore).
- `.env` (NEVER commit, never print values; single-quoted values) has the same 17 keys, in the same order, as the
  committed template `.env.example`. Set so far (Nov 24 2026): DATABASE_URL (rotated), ENDPOINT_URL,
  ENDPOINT_TOKEN (rotated, 40 chars), SERVICENOW_INSTANCE (dev219979), SERVICENOW_USER, GEMINI_API_KEY,
  GEMINI_MODEL_LITE, GROQ_API_KEY, OLLAMA_BASE_URL, HF_TOKEN (read), SERVICENOW_PASSWORD (ai.agent),
  SERVICENOW_ADMIN_PASSWORD (CLEARED after 1.2; admin password reset Nov 24 2026 — put it back only temporarily
  to re-run servicenow_setup.py, then clear again). LLM_* agent map set by Claude in 2.1 (model names, not secrets). Claude checks .env only by key names / lengths / masked shape — never values.
  Common paste mistakes seen: whole `NAME='…'` line pasted inside the quotes; file not saved (Ctrl+S);
  whole file overwritten instead of one line edited.
- Ollama: D:\Ollama (ollama.exe not on PATH in old terminals), models in D:\Ollama\models (user env OLLAMA_MODELS),
  tray app starts with Windows and serves http://localhost:11434.

## Code layout
- `src/common/config.py` — paths, EMBED_MODEL=BAAI/bge-small-en-v1.5 (384-d, query instruction prefix), SEED=42,
  TEST_FRACTION=0.20, RRF_K=60, BM25_K1=1.2, BM25_B=0.75, WEAK_EVIDENCE_COSINE=0.55. `check_db.py`.
- `src/common/llm.py` — old `gemini_json` (to be removed when agents use the router, 2.4).
- `src/common/llm_router.py` — `complete_json(agent, prompt, Schema, system=, fallback=)` -> LLMResult
  (method llm|rule-based|none, data, provider, model, latency_s, tokens, fallback, attempts, summary()).
  CALLERS dict (gemini/groq/ollama) is what tests patch with fake LLMs.
- `src/common/check_llms.py` — pings Gemini / Groq / Ollama: lists live models, skips non-chat models, sends one
  JSON prompt, prints latency (`--provider`, `--list`, `--agents` = per-agent chain via the router). Never prints keys.
- `src/knowledge/eval_retrieval.py --limit N` is a smoke test and does NOT save results (only full runs do).
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
- `src/orchestrator/` (Phase 2) — state.py (AgentState TypedDict: ticket, flow, guardrails, understanding,
  evidence, policy, critic, approval, execution, verification, final_state, route[], trace[]), graph.py
  (supervisor hub + `route()`; build_graph(retriever, checkpointer, rewrite_query, incident_node)),
  guardrails.py (check_input), trace.py (step/timed/format_step), checkpoint.py (sqlite_saver ->
  data/checkpoints/agents.sqlite, memory_saver for tests, values/summary/delete per thread).
- `src/access/` — catalog.py (TARGETS -> policy, TARGET_ALIASES, ALTERNATIVES, SOFTWARE_KEYWORDS, CATALOGUE_NOTE +
  ENFORCEMENT (critic context), EMPLOYEES (local only), APPROVER_GROUPS, LOCAL_GROUP_MEMBERS), store.py (SQLite
  data/tickets.db, RITM9000001+), endpoint_client.py (execute/verify/status/rollback; `python -m
  src.access.endpoint_client status | rollback <action_id> <ticket>`), schemas.py (pydantic outputs of each LLM
  agent), agents.py (rule_target/normalise_target/grounded, understanding (LLM + grounding), retrieval (query
  rewrite), decide/decide_for/policy_risk, request_approval, await_approval (interrupt), execution, verification,
  communicate), critic.py, communication.py (outcome + compose + message_ok), graph.py (re-export of
  orchestrator.graph), ticket_source.py (TicketSource + ServiceNowSource (default; context read from the
  checkpoint via attach_checkpoints; note seq from the journal; fetch_stuck) + LocalSource; `selftest`),
  worker.py (process_ticket, approve_ticket (resume via Command), sweep_stuck, run_poller), cli.py
  (`--source`, `--rules-only`, `--llm-single p:m`, `-v`; new, worker, process, approve, sweep, show, trace, list,
  demo; `--no-retrieval`, `--no-rewrite`). Worker start: embedding model warm-up + LLM warm-up (llm_router.warmup).
- `src/integrations/` — (the earlier scripts were never committed and were lost; rewritten for dev219979)
  check_sn_auth.py (REST login test; `--admin`; detects hibernation), create_ai_agent.py (admin web session
  class `Admin`; creates ai.agent + roles idempotently), servicenow_client.py (1.1: `ServiceNowClient.from_env()`,
  typed errors, retries, ACL-safe paging, work notes/comments/state by name/journal/RITM variables/approvals;
  `python -m src.integrations.servicenow_client smoke [--write]`), servicenow_setup.py (1.2, lean design:
  user records, groups IT Security + AI Service Desk, APPROVER_GROUPS map; setup needs admin, `--test` runs
  as ai.agent only).
- `src/incident/` (Phase 4) — agents.py (incident nodes + route), rca.py, bayes.py, evidence.py, tools.py.
- `src/lab/` (Phase 4) — config.py (SERVICES, thresholds, FAULTS ground truth), service.py, supervisor.py,
  faults.py, actions.py (remediation action catalogue + audit data/lab/audit.jsonl + rollback).
- `tests/` — stdlib unittest (no pytest): `python -m unittest discover -s tests -t . -v`.
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
python -m src.access.cli new "<text>" | worker [--once] | process --all | approve <INC> --by <user> [--reject] | show <INC> | trace <INC> | sweep | list | demo
python -m src.access.cli --source local demo      # offline SQLite desk
python -m src.access.cli --rules-only ...         # no LLM; --llm-single groq:openai/gpt-oss-20b = one model for all agents
python -m src.common.check_llms --agents          # per-agent model chain check
python -m src.access.endpoint_client status       # endpoint allowlist/blocklist; rollback <action_id> <ticket>
python scripts/check_staged_secrets.py            # before every commit
python -m unittest discover -s tests -t .         # all unit tests (offline)
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
Phase 1 on ServiceNow (Nov 24 2026): same decisions on PDI incidents (INC0010022-24), worker ~4.5 s per ticket
after the first (first ~20-45 s: Gemini latency); approver checks refuse wrong person / self-approval.
Phase 2 on ServiceNow (Nov 24 2026, multi-LLM, retrieval on): INC0010033-38 ChatGPT DENY 7.7 s (first ticket;
was 29 s before the embedding warm-up), Gemini RESOLVED 7.1 s, GitHub AWAITING 7.3 s -> approved in another
process -> RESOLVED, notion.so ESCALATED 4.9 s, prompt injection ESCALATED 4.2 s, "remove pypi.org" ESCALATED.
Phase 3 evaluation (evaluation/results/access_eval_summary.md, Nov 25 2026):
- Dataset test split, 77 access/service tickets: rule engine on the ticket's own policy 0.844 decision exact match
  (1.000 without the 12 ESCALATE labels = dataset scenario escalated_no_permitted_action); hybrid-retrieved policy
  Hit@1 0.688 -> decision exact match 0.701.
- 52 labelled catalogue requests, full graph, simulated endpoint: rules-only / agentic: target 0.981 / 0.981,
  decision 0.981 / 0.981, final state 0.981 / 0.981 (both miss R51: rules read ChatGPT; agentic reads Gemini but
  the critic's stricter-rule-reading check escalates - intended), automation 0.846 / 0.827, escalation
  0.135 / 0.154, approval 0.288 / 0.288, verification 1.0 / 1.0, MTTR 0.43 s / 4.35 s, agentic fallbacks 30.6 %
  all to the secondary provider (Gemini 429 quota), 0 rule-based.
Root cause of the old slow first ticket: ~20 s lazy load of the embedding model inside the first k-NN lookup
(+ Gemini timeouts) -> model loaded at worker start (17 s) + LLM warm-up puts down models in cooldown.

## ServiceNow authentication facts (PDI dev219979, Brazil, verified Nov 24 2026)
- Basic-auth restriction ENFORCED since 2026-11-12 (`glide.authenticate.basic_auth.restriction.enforce=true`,
  `allowed_roles=snc_basic_auth_api_access`, `allow_wsao=true`). REST with user/password works ONLY for users
  holding `snc_basic_auth_api_access` (proven: admin 401 without, 200 with; role removed from admin again).
  Exception table `sys_user_basic_auth_exception` is empty. API Access Policies for Table GET/POST are inactive.
- Admin automation therefore uses a WEB session (login.do -> g_ck -> `X-UserToken` header on the Table API):
  `src/integrations/create_ai_agent.Admin`. Admin never gets the Basic-auth role.
- Writing `sys_user.user_password` via Table API (also with sysparm_input_display_value=true) is accepted but has
  NO effect; background script via sys.scripts.do did not execute when posted. Passwords are set by hand in the UI.
- Login failures are logged: sysevent `login.failed` and syslog "Basic authentication failed for user: X".
- Instance hibernation shows up as a non-JSON (HTML) answer; `check_sn_auth` reports it.
- Read ACLs filter rows AFTER a page is fetched: a page of 20 can return 19 while more follow. Paging must
  advance by the requested size and stop on X-Total-Count (pre-ACL total), never on a short page.
- ai.agent cannot read `sys_choice` (403, admin-only) nor `sys_journal_field` (rows exist, 0 readable).
  Journal entries are read from the record's own work_notes/comments field as display value and parsed.
- sc_req_item has no own state choices; it inherits task.state: -5 Pending, 1 Open, 2 Work in Progress,
  3 Closed Complete, 4 Closed Incomplete, 7 Closed Skipped. Incident: 1 New, 2 In Progress, 3 On Hold,
  6 Resolved, 7 Closed, 8 Canceled (verified against sys_choice via admin session).
- Smoke/setup tests leave canceled test incidents ("[smoke test]" / "[setup test]", INC0010001-5) — harmless.
- sysapproval_approver on INCIDENTS cannot be created by non-admins: field ACLs allow approver/sysapproval on
  new rows only for change_request/std_change_proposal and catalog tasks; otherwise the insert returns 201
  with those fields silently BLANK (orphan rows; 4 were created in testing and deleted). -> approvals are
  recorded in incident.approval (+ work notes), which ai.agent can write.
- The Brazil PDI already has incident.category `access` = "Access & Password" (sequence 1). The first setup run
  overwrote its label; it was restored from sys_update_version. Setup never modifies existing platform choices.
- Reference fields accept a display value on insert (user "security01" resolved), but queries need sys_ids.
- Resolving an incident: a data policy makes close_code ("Resolution code") + close_notes mandatory, and
  close_code is a STRICT choice list (values contain an EN DASH, e.g. "Resolved – Permanent Fix Applied"; list
  via GET /api/now/ui/meta/incident). Any other value is silently blanked -> CLOSE_CODES in servicenow_client.
  Canceled (8) needs no code. Escalations go to the built-in group "Service Desk".
- Because of these silent drops, ServiceNowClient.create/update request back every field they write and raise
  ServiceNowFieldDropped if one came back blank (journal fields excluded).

## Known issues
- VS Code installs fail while VS Code is running (installer cancels silently).
- (fixed in 1.5) Approver identity is now checked against ServiceNow; wrong approver / self-approval refused.
- (fixed in 2.1/2.8) slow first ticket; stuck In Progress tickets (sweep).
- Gemini 3.5-flash / flash-lite are often 503 "high demand" or take 10-30 s (Nov 2026). The router's cooldown and
  warm-up handle it; the critic then runs on groq gpt-oss-120b.
- Gemini free tier: 429 quota on gemini-3.5-flash and -flash-lite during long batch runs (Nov 25 2026) ->
  critic and retrieval rewrite move to Groq (router secondary); evaluations pace tickets (--pace 5).
- Groq free tier (headers checked Nov 25 2026): per model 1000 requests/DAY and only 8000 tokens/MINUTE; a ReAct
  RCA prompt is ~1.5-2k tokens -> ~4 calls/min. Router (Nov 25): a 429 with Retry-After <= 20 s waits and retries
  the same model once; otherwise the model cools exactly Retry-After (no escalation). Batch evaluations must pace
  (eval_incident --pace 8 dataset / 20 lab). A first unpaced incident eval run was stopped because half of its LLM
  calls were skipped (cooldowns) - its numbers were not used.
- Groq free tier rate-limits gpt-oss-120b (HTTP 429 after ~4 critic calls within a minute, ~1.2k tokens each).
  Batch evaluation (3.5 / 5.1) must pace calls or accept fallbacks; the router logs every fallback.
- Phase-1 tickets (INC0010001-24) have no checkpoint: `list`/`show` show decision/target as None for them.
  data/agent_state.db is unused since 2.8 (gitignored; can be deleted by the user).
- Test incidents INC0010025-40 (selftest, live runs, sweep test) remain on the PDI, all closed or escalated.
  INC0010029 (Gemini) was ESCALATED by the LLM critic before the catalogue-note fix (expected then, see 2.6).
- k-NN confidence is low for real AI tool names (dataset uses fictional names) — decisions still correct.

---------------------------------------------------------------------------------------------------------------
# MASTER TODO LIST (the project must work perfectly end to end)
Rules for Claude: work phase by phase, in order. Before each item explain what you will do; after it, run the
listed test and show the output. Tick `[x]` here only when the acceptance test passes on the user's machine.
Never mark an item done on assumptions. Keep this list updated (add items you discover). Commit after each phase.

## Phase 0 — Accounts, API keys and environment (guide the user step by step; see "API KEY GUIDE" below)
- [x] 0.1 Create `.env.example` (all variable names, no values) and make sure `.env` is in .gitignore.
- [x] 0.2 Rotate exposed secrets: Neon password, ENDPOINT_TOKEN, ServiceNow admin + ai.agent passwords; delete
      Desktop apikey.txt after moving its value into .env. Test: check_db OK, endpoint /health OK.
- [x] 0.3 Gemini API key. Test: `python -m src.common.check_llms` returns JSON from Gemini.
      (check_llms.py written: lists live models per provider, pings with a JSON prompt. Verified Nov 2026:
      GEMINI_MODEL_LITE='gemini-3.5-flash-lite' ~1.3 s; gemini-2.5-flash-lite is listed but returns 404 "no longer
      available to new users"; one call hit the 30 s timeout -> router must time out + fall back.)
- [x] 0.4 Groq API key. Test: same script returns JSON from Groq.
      (Verified Nov 2026: Groq has NO Llama chat models any more — only llama-prompt-guard classifiers. JSON-capable
      chat models: openai/gpt-oss-20b ~0.5 s, openai/gpt-oss-120b ~0.7 s, qwen/qwen3.8-27b ~0.4 s, allam-2-7b.
      check_llms skips non-chat models (guard/whisper/tts/image).)
- [x] 0.5 Ollama installed + one small local model pulled. Test: same script returns JSON from Ollama.
      (Ollama 0.40.18 via winget at D:\Ollama; user env OLLAMA_MODELS=D:\Ollama\models because C: has only ~6 GB
      free. Model llama3.2:3b (1.9 GB, ~80% on RTX 2050 GPU): cold load ~9 s, warm ~2.6 s. The very first load
      after install crashed once with a CUDA v13 init error (0xc0000409, ggml_cuda_kernel_can_use_pdl); not
      reproducible on two cold reloads. If it recurs: set user env OLLAMA_LLM_LIBRARY=cuda_v12 and restart Ollama.
      Only ~0.8 GB RAM was free during load — keep Ollama optional, never the only provider.)
- [x] 0.6 HF_TOKEN (read-only) to remove the HF warning. Test: no warning on eval_retrieval.
      (Verified: whoami -> role read; `eval_retrieval --limit 3` prints no HF warning.)
- [x] 0.7 ServiceNow PDI (dev219979) awake and reachable; create ai.agent on the new instance; write
      `src/integrations/check_sn_auth.py` (missing). Test: `python -m src.integrations.check_sn_auth` = 200.
      (Verified twice: HTTP 200, roles incl. itil, approval_admin, snc_basic_auth_api_access. See "ServiceNow
      authentication facts" below — Basic-auth restriction and API password setting do not work.)
- [x] 0.8 `.gitignore` contains .env, data/tickets.db, checkpoint DBs, logs, audit jsonl. `pip install -r requirements.txt` clean.
      (requirements.txt pins the direct deps verified together; fresh-venv `pip install --dry-run` resolves cleanly,
      `pip check` OK. .gitignore: *.db/*.sqlite*/WAL files, data/checkpoints/, logs/, *.log, endpoint_service/audit/.
      New deps (Phase 2: langgraph-checkpoint-sqlite etc.) must be added here with a pinned version.)

**Phase 0 COMPLETE (Nov 24 2026)** — branch phase-0-setup merged into main.

## Phase 1 — ServiceNow integration (REQUIRED)
- [x] 1.1 `src/integrations/servicenow_client.py`: auth, retries, timeouts, pagination; get/update records,
      add work note, add comment, set state, query approvals. Unit test with a mocked session + live smoke test.
      (Nov 24 2026: 33 unit tests OK (tests/test_servicenow_client.py, fake session returning real Responses);
      live `smoke --write` OK on dev219979. Live run found 2 bugs the mocks missed: ACL short pages ended paging
      early; get_journal via sys_journal_field returned [] silently -> both fixed. get_ritm_variables is
      verified live only in 1.2 (no catalog variables exist yet).)
- [x] 1.2 Run servicenow_setup.py (lean design, admin web session `create_ai_agent.Admin`, idempotent):
      user records without logins: manager01 (LINE_MANAGER), employee01 (title DEVELOPER, manager manager01),
      security01; groups "IT Security" (= SECURITY_TEAM, member security01) and "AI Service Desk" (member
      ai.agent); incident category `access` (exists: "Access & Password"; create-only). `--test` (as ai.agent,
      not admin): create an access incident for employee01, read caller role/manager, approval requested ->
      approved on the incident, approver check, work notes, cancel. LAST STEP: user resets the PDI admin password (old one exposed) ->
      old password's web login must fail; Claude clears SERVICENOW_ADMIN_PASSWORD; `check_sn_auth` still 200.
      (Nov 24 2026: setup created 3 users + 2 groups, re-run all "unchanged"; admin password reset by user, old
      one rejected by web login; .env value cleared (17 keys, order intact); check_sn_auth 200; `--test` as
      ai.agent all OK (INC0010006). Design change on the way: approvals on the incident's approval field.)
- [x] 1.3 Ticket source abstraction: `TicketSource` interface with `ServiceNowSource` (default) and `LocalSource`
      (SQLite, tests only). Agents never call ServiceNow directly.
      (Nov 24 2026: src/access/ticket_source.py; neutral statuses mapped to incident state/approval/close code;
      65 unit tests OK (tests/test_ticket_source.py, tests/test_access_worker.py); live `selftest` OK
      (INC0010008 approval -> RESOLVED, INC0010009 ESCALATED, INC0010010 CLOSED_REJECTED).)
- [x] 1.4 Poller/worker: picks up new incidents (category access, assignment group AI Service Desk, state New),
      maps caller (role = title, manager) + free text -> agent state, runs the graph, writes every agent step as
      a work note, final message as a customer-visible comment, sets incident state (In Progress / On Hold =
      awaiting approval / Resolved / Canceled for DENY; close notes say why).
      (Nov 24 2026: `cli worker --once` on PDI: INC0010019 ChatGPT DENY -> Canceled; INC0010020 notion.so ->
      ESCALATED to "Service Desk"; INC0010021 Postman -> On Hold, approval requested, approver manager01 named.
      Live bug fixed: same-second work notes came back out of order -> one numbered "Agent trace" note per run
      + per-ticket note sequence "[#n agent]". First ticket of a run takes ~45 s (slow Gemini call) -> 2.1.)
- [x] 1.5 Approvals recorded in ServiceNow: on ALLOW_WITH_APPROVAL set incident approval=requested + On Hold +
      work note naming who may approve (LINE_MANAGER -> caller's manager, SECURITY_TEAM -> IT Security member);
      `cli approve <INC> --by <user> [--reject]` checks the user against ServiceNow (wrong person -> refused,
      noted), sets approval approved/rejected with a work note, resumes the graph.
      (Nov 24 2026, live: INC0010021 Postman (LINE_MANAGER): security01 REFUSED, employee01 REFUSED (self),
      manager01 --reject ACCEPTED -> Canceled, approval Rejected. INC0010015 GitHub (SECURITY_TEAM): manager01
      REFUSED, still On Hold/Requested. CLI exit code 2 on refusal. Approve path with execution -> 1.6.)
- [x] 1.6 End-to-end test: create access incidents for employee01 (CLI `new` writes them to ServiceNow) ->
      ChatGPT DENY, Gemini ALLOW, GitHub approval refused for manager01 and accepted for security01 -> RESOLVED;
      endpoint changes verified; notes, comments, states and approval field visible in the PDI.
      (Nov 24 2026, endpoint service running as Admin: INC0010022 ChatGPT -> Canceled, no endpoint action;
      INC0010023 Gemini -> allowlisted, verified, Resolved (close code "Resolved – Permanent Fix Applied");
      INC0010024 GitHub -> On Hold -> manager01 REFUSED -> security01 approved -> allowlisted, verified,
      Resolved, approval Approved. Independent check: endpoint /status allowlist = [gemini, github], chatgpt
      still blocked; audit entries carry INC number + AUTH id + rollback spec. Both grants rolled back via
      /actions/rollback -> allowlist [] again, 10 blocked, DownloadRestrictions 3.)

**Phase 1 COMPLETE (Nov 24 2026)** — branch phase-1-servicenow merged into main.

## Phase 2 — Multi-agent LangGraph with multiple LLMs (REQUIRED)
- [x] 2.1 `src/common/llm_router.py`: providers Gemini / Groq / Ollama behind one interface; per-agent model map
      from `.env`; strict JSON-schema output (pydantic) with repair/retry; timeouts; fallback chain
      (primary -> secondary provider -> rule-based); log agent, provider, model, latency, tokens, fallback used.
      (Nov 24 2026: model map confirmed by user = "mixed providers": UNDERSTANDING groq:openai/gpt-oss-20b,
      RETRIEVAL gemini:gemini-3.5-flash-lite, RCA groq:openai/gpt-oss-120b, CRITIC gemini:gemini-3.5-flash,
      COMMUNICATION groq:qwen/qwen3.8-27b. Secondary = other provider (critic -> gpt-oss-120b, never the
      Understanding model); Ollama opt-in (LLM_OLLAMA_FALLBACK=1). Per-agent timeouts (6-25 s) + total budget
      2.5x; cooldown 120 s after 429/5xx/timeout, 900 s after 401/403/404 -> a slow Gemini costs one timeout
      per run, not per ticket. One repair retry on invalid JSON/schema. Log: logs/llm_calls.jsonl + "llm" logger.
      Process env LLM_MODE=multi|single|off, LLM_SINGLE for ablations. 17 offline tests (tests/test_llm_router.py);
      live: retired model id -> 404 -> secondary; proxy off -> rule-based in <2 s, 2nd call skips instantly.)
- [x] 2.2 `src/common/check_llms.py`: pings every configured provider/model.
      (`--agents`: per agent chain, direct ping of each model, one router call. Live Nov 24 2026: all 5 agents
      answered by their primary (groq 0.8-1.1 s, gemini lite 2.1 s, gemini flash 1.4 s). Gemini is flaky today:
      3.5-flash and 3.5-flash-lite alternated between 1-3 s answers, 503 "high demand" and an 18.9 s answer.)
- [x] 2.3 Supervisor/orchestrator node routing between agents (access-request flow vs incident flow).
      (src/orchestrator/graph.py: hub-and-spoke, every agent returns to the supervisor; `route()` is a pure
      deterministic function; flow = access for category access, incident for any other category (Phase-4
      placeholder hands off to a human). Supervisor also runs input guardrails (length cap, control chars,
      prompt-injection patterns). 20-case route table test + live runs show route in the checkpoint.)
- [x] 2.4 Understanding Agent uses LLM extraction (schema-validated) with rule-based fallback.
      Fix rule-based bug: "I need ChatGPT to debug my Python code" -> Python.Python.3.12 (software keyword wins;
      known AI-tool names without a domain are not recognised). Add tests for such sentences.
      (catalog.TARGET_ALIASES + scored candidates (request word in the 4 words before, explicit domain); file
      names like script.py ignored. LLM target must be GROUNDED in the text (else rules); LLM "other" = nothing
      requested. 12-sentence test. Live INC0010033: extraction=llm, chatgpt.com, DENY.)
- [x] 2.5 Retrieval Agent: LLM query rewriting (optional flag) before BM25 + dense + RRF.
      (default on, `--no-rewrite` flag; live INC0010035: query "website_access GitHub clone open-source
      dependencies", linked SOP-0025 top-1. Effect on Hit@k measured in 5.1.)
- [x] 2.6 Critic/Reflection Agent with a DIFFERENT model: reviews proposed plan vs SOP/policy; can only block or
      escalate, never override the rule-based decision.
      (src/access/critic.py: runs only for ALLOW/AWA plans, before approval is requested; deterministic checks
      (injection flag, grounding, removal wording, rule reading stricter than LLM reading, plan consistency) +
      LLM review; verdict = most severe. Live finding: the LLM critic escalated Gemini because POL-0028's SOP
      action is ADD_GROUP_MEMBERSHIP for the fictional "Orbit Assist" -> catalogue note + action equivalence
      given as facts; then gemini-3.5-flash and gpt-oss-120b both: 3 valid plans APPROVE, "REMOVE github.com"
      BLOCK. With the critic LLM rate-limited (Groq 429) the rules alone approved the removal -> REMOVAL_INTENT
      check added.)
- [x] 2.7 Communication Agent uses LLM for user messages and work notes (fallback templates).
      (outcome decided in code; LLM phrases it; checked for false success/refusal claims, target named, approval
      mentioned -> else template; DENY alternative always appended. Live: one AWAITING text rejected -> template.)
- [x] 2.8 LangGraph checkpointer (SQLite or Postgres) + `interrupt` for human approval; resume by thread_id = ticket.
      (data/checkpoints/agents.sqlite (SqliteSaver, WAL), thread_id = "<instance>|<INC>"; await_approval node
      calls interrupt(); `cli approve` resumes with Command(resume=...) in ANOTHER process (live INC0010035:
      manager01 refused, security01 -> execute -> verify -> RESOLVED, only the new steps written).
      data/agent_state.db is no longer used (ServiceNow note sequence now derived from the journal).
      Lost checkpoint -> deterministic rebuild + pre-approval (role must match). Stuck sweep (`cli sweep`, also
      every worker poll, default 600 s): no checkpoint -> re-run; paused -> re-save / resume with recorded
      approval; mid-run -> resume from checkpoint; finished -> re-save. Live: INC0010039 re-run, INC0010040 re-saved.)
- [x] 2.9 Trace: every node writes {agent, model, input summary, output, latency} to state and to ServiceNow notes.
      (src/orchestrator/trace.py; one numbered "Agent trace" work note per run, lines
      "n. [Agent] output | in: ... | provider:model 0.58s (fallback: secondary)"; `cli trace <INC>` table.)
- [x] 2.10 Tests: graph unit tests with fake LLMs; one run per provider; fallback test with network off.
      (tests/test_multi_agent.py 30 tests (scripted FakeLLM per model), test_llm_router 19; 118 total OK.
      One run per provider (--llm-single, local desk, DENY + AWA): groq gpt-oss-20b 8.9 s, gemini 3.5-flash-lite
      15.3 s, ollama llama3.2:3b 41.3 s for 2 tickets - same decisions. Network off (requests.post raises):
      all 4 sample decisions unchanged, every step rule-based/deterministic.)

**Phase 2 COMPLETE (Nov 24 2026)** — branch phase-2-multi-llm merged into main.

## Phase 3 — Objective 2 hardening and evaluation
User decisions (Nov 24 2026, branch phase-3-hardening, nothing implemented yet):
- 3.8 scope = **Level 1 (data-only)**: POL-0020 DENY (qBittorrent, uTorrent), POL-0023 ALLOW (Zoom, Slack, Teams,
  Notepad++, 7-Zip), POL-0026 AWA SECURITY_TEAM (stackoverflow.com, docs.python.org), POL-0029 AWA
  AI_GOVERNANCE_BOARD (huggingface.co, kaggle.com, colab.research.google.com), more DENY sites (dropbox, deepseek).
- 3.2: create **all 5 approver groups** with one user record each (no login) via servicenow_setup.py:
  IT Service Owners/serviceowner01 (IT_SERVICE_OWNER), Change Advisory Board/cab01, AI Governance Board/aigov01,
  Application Owners/appowner01, Data Owners/dataowner01. Needs SERVICENOW_ADMIN_PASSWORD temporarily (guide the
  user), clear it afterwards. Update catalog.APPROVER_GROUPS + LOCAL_GROUP_MEMBERS (single source of truth).
- Design: endpoint service v2 (one restart): pre-check, async install jobs, before-state rollback, expiry; new
  neutral status WAITING_FOR_USER (ServiceNow On Hold + `hold_reason` 1 "Awaiting Caller" - on this PDI the field
  is `hold_reason`, NOT `on_hold_reason`, checked via /api/now/ui/meta/incident). Live evidence Nov 25 2026:
  `python endpoint_service\test_endpoint.py` RESULT OK; incidents INC0010042-47.
- [x] 3.1 Pre-check running process before software install (Code.exe, chrome.exe, Postman.exe...): clear user
      message "close X and resubmit" instead of escalation; test with VS Code open.
      (endpoint_config.PACKAGE_PROCESSES + GET /precheck/software; the service also refuses an install while the app
      runs (status BLOCKED, no attempt used). Graph: BLOCKED -> request_user_action (message "Please close
      Code.exe ...") -> await_user interrupt; worker tend_active re-checks every poll: closed -> install; still
      open after 24 h -> CLOSED_REJECTED "close it and submit a new request". Live with VS Code open: INC0010042
      -> On Hold / Awaiting Caller in ServiceNow; time-out path -> Canceled with that message.)
- [x] 3.2 Approver role enforcement (done in 1.5 for the CLI; extend to other approver roles, e.g.
      IT_SERVICE_OWNER / CHANGE_ADVISORY_BOARD for Objective 3, with groups created by servicenow_setup.py).
      (Every approver role in the dataset has a group: IT Security/security01, IT Service Owners/serviceowner01,
      Change Advisory Board/cab01, AI Governance Board/aigov01, Application Owners/appowner01, Data
      Owners/dataowner01 (catalog.APPROVER_GROUPS + APPROVER_USERS). Setup run with the admin password (then
      cleared; re-run all unchanged; `--test` as ai.agent OK for all 6 roles). Live INC0010045 Hugging Face
      (AI_GOVERNANCE_BOARD): security01 REFUSED (exit 2), aigov01 approved -> allowlisted -> verified -> RESOLVED.)
- [x] 3.3 Revocation / time-limited access (duration variable) with automatic rollback when expired.
      (agents.parse_duration "for 2 days", "for the next 3 hours", "today only", minutes; LLM duration used only if
      the text has a time word; capped at 30 days; domains AND installs (install expiry = uninstall; added after
      the eval showed the critic rightly escalating "7-Zip for 3 days"; live INC0010048 "7-Zip for 2 minutes":
      job -> Resolved -> expiry sweep uninstalled it, verify not installed). expires_at sent
      to the service and audited; GET /grants; worker expire_grants each poll -> audited rollback + note + comment.
      Live INC0010044 "gemini.google.com for 2 minutes": RESOLVED "until ... UTC", after expiry `cli sweep` ->
      revoked, note on the incident. `cli grants` lists active time-limited grants.)
- [x] 3.4 Unknown target -> ESCALATE_FOR_REVIEW; test with a domain not in the catalogue.
      (Unit tests: notion.so, some-unknown-tool.org, Figma, "laptop fan is loud" -> ESCALATE_FOR_REVIEW, nothing
      executed; endpoint refuses unmanaged domains (403, live test). Live: INC0010031 / INC0010036 notion.so.)
- [x] 3.6 Rollback correctness: winget uninstall must use `--scope machine` (as install does); domain rollback
      must restore the recorded "before" allowlist (not revoke a grant that existed before the ticket).
      (RESTORE_DOMAIN rollback spec = the domain's before-state; a second rollback is refused (409); v1 audit
      records still roll back. Live: grant A + grant B, rollback B keeps A's access, rollback A removes it;
      7-Zip install of INC0010043 rolled back -> uninstall --scope machine -> verify "not installed".)
- [x] 3.7 Endpoint client timeout (1500 s) must not block the poller: run installs asynchronously or per-ticket.
      (Service runs winget in a background job and answers RUNNING at once; GET /jobs/{id}. Graph: RUNNING ->
      await_job interrupt, ticket stays In Progress with "installation started" comment; worker tend_active polls
      the job and resumes -> verify. Client timeouts now 30-120 s (1500 s only for an uninstall rollback). Unknown
      job after a service restart = FAILED -> retry / verify real state. Live: INC0010043 7-Zip IN_PROGRESS ->
      sweep: job SUCCEEDED -> verified -> RESOLVED.)
- [x] 3.8 Extend the access catalogue beyond the 15 demo targets (user's request, Nov 24 2026). Level 1 (user's
      choice): +15 targets -> 30 targets under 11 policies: POL-0020 DENY (qBittorrent, uTorrent), POL-0023 ALLOW
      (Zoom, Slack, Teams, Notepad++, 7-Zip; machine-scope installers checked with winget), POL-0026 AWA
      SECURITY_TEAM (stackoverflow.com, docs.python.org), POL-0029 AWA AI_GOVERNANCE_BOARD (huggingface.co,
      kaggle.com, colab.research.google.com), DENY dropbox.com (POL-0027), deepseek.com / chat.deepseek.com
      (POL-0030). Service adds new managed domains to the blocklist at start (audited BASELINE); apply script
      updated. tests/test_catalog.py checks catalogue <-> policies <-> endpoint lists. Live: qBittorrent and
      dropbox DENY (INC0010046/47), Hugging Face approved (INC0010045). Level 2 (new action types) not done.
- [x] 3.5 `evaluation/eval_access.py`: batch of labelled requests (incl. dataset access tickets) -> decision exact
      match, automation rate, escalation rate, approval rate, MTTA, MTTR, verification pass rate; save tables.
      (Saved: evaluation/results/access_eval_summary.{json,md}, access_dataset_decisions.csv,
      access_catalogue_{rules,agentic}.csv; `--report` rewrites the .md. Numbers: see "Results so far".)

**Phase 3 COMPLETE (Nov 25 2026)** — branch phase-3-hardening merged into main.

## Phase 4 — Objective 3: incident resolution on simulated services
- [x] 4.1 Simulated services (e.g. FastAPI web app, worker queue, Postgres/SQLite DB, cache) with health
      endpoints, metrics and logs, run locally (docker optional; must work on Windows Home without admin).
      (src/lab: web 9101 -> orders-api 9102 (config release, DB connection pool) -> cache 9103; worker 9104 (spool
      dir). One FastAPI app per process (`python -m src.lab.service <name>`), GET /health /metrics /logs, POST
      /work; admin endpoints behind a random token in data/lab/token. `python -m src.lab.supervisor
      start|stop|status|restart|reset` (plain processes, PIDs in data/lab/pids.json, no admin, no Docker).)
- [x] 4.2 Fault injector: service down, high latency, memory leak, disk full (simulated), bad config, expired
      certificate (simulated), DB connection pool exhausted — each mapped to dataset root_cause_category and SOP.
      (`python -m src.lab.faults list|inject <fault>|clear`; 8 faults incl. dependency_down, ground truth in
      src/lab/config.FAULTS (eval only). Live Nov 25 2026: every fault injected -> symptom on /health -> its
      catalogue action (src/lab/actions.py, audited, rollback specs) -> all services healthy again. Symptoms
      spread like real ones (cache bloat -> latency on web, orders-api AND cache). tests/test_lab.py 9 tests.)
- [x] 4.3 Monitoring -> auto-create ServiceNow INCIDENT with symptoms/observed state/diagnostic data.
      (src/lab/monitor.py `--once | --interval 15`: synthetic transactions + /health of all services; failing
      services grouped into INDEPENDENT events by dependency relation (live finding: two unrelated faults were
      first merged into one incident -> the fix for one could never verify); one incident per event, reported on
      the customer-facing service, through TicketSource.create_incident (caller = ai.agent, group AI Service
      Desk, category from the failing checks only: hardware / database / performance / internal_applications -
      never access); description = affected service, symptoms, observed state, last log lines; de-duplicated
      while open (data/lab/monitor_state.json). Live: INC0010049, INC0010052, INC0010053.)
- [x] 4.4 RCA Agent: ReAct diagnosis loop with tools (check health, read logs, read metrics) + LLM hypotheses +
      Bayesian root-cause ranking (priors from historical_incidents, likelihoods from evidence).
      (src/incident: tools.py (9 read-only tools), rca.py (ReAct, LLM_RCA picks one tool per step, max 4, then
      hypotheses; deterministic evidence completion; category definitions in the prompt), evidence.py (root
      service by dependency analysis + binary features), bayes.py (prior P(rc | category) from the TRAIN split +
      k-NN of retrieved similar incidents, likelihood table, LLM top hypothesis as noisy observation alpha 0.6).
      Live check, 8 faults: rules-only root 8/8, top-1 7/8, top-3 8/8; with the LLM top-1 6/8 (taxonomy framing:
      leak = "software defect", dependency crash = "software defect") -> alpha to be calibrated in 4.6.
      Live fixes: OOM log line counted as both crash and OOM (naive-Bayes double count) -> separated.)
- [x] 4.5 Remediation via SOP actions (fixed action catalogue: restart service, rollback config, clear cache,
      scale worker, rotate cert...), policy check, critic review, execution, predicate verification,
      retry <= 2, rollback on failure, escalate otherwise.
      (src/incident/agents.py nodes in the supervisor graph: triage, incident_retrieve, rca, incident_plan
      (remediation map root cause + evidence -> SOP -> catalogue action on the root service; dataset policy of
      the SOP via the rule engine with role SITE_RELIABILITY_ENGINEER; posterior < 0.5 -> ESCALATE),
      incident_critic (rules + critic LLM; ITIL: restoring service is incident mgmt, code fix = problem mgmt),
      approval (shared await_approval interrupt; approvers from the new groups), remediate (src/lab/actions.py),
      incident_verify (root + affected service healthy), incident_rollback, retry with the next plan only if it
      needs no other approval, then ESCALATED. Live: INC0010049 bad release -> ReAct (4 tools) ->
      DEPLOYMENT_CHANGE -> POL-0043 needs CAB -> aigov01 REFUSED, cab01 approved -> ROLLBACK_DEPLOYMENT ->
      verified -> RESOLVED; INC0010052 memory leak -> POL-0046 ALLOW -> restart -> RESOLVED 34 s; INC0010053 disk
      full -> FREE_DISK_SPACE -> RESOLVED 12.9 s. tests/test_incident.py 11 tests (fake lab).)
- [ ] 4.6 Evaluation: RCA top-1/top-3 on injected faults and on dataset test tickets (root_cause_category), MTTR,
      resolution rate, rollback correctness.

## Phase 5 — Final evaluation, UI and demo
- [ ] 5.1 Ablations: single LLM vs multi-LLM; with vs without retrieval; rules-only vs agentic; per-provider latency.
- [ ] 5.2 Simple dashboard (Streamlit or FastAPI page) showing tickets, agent trace, metrics.
- [ ] 5.3 One-command demo scripts (`scripts/demo_access.ps1`, `scripts/demo_incident.ps1`) + reset script.
- [ ] 5.4 README with setup, architecture diagram, run steps; update CLAUDE.md and docs/PROJECT_HISTORY.md.
      Include `python -m src.knowledge.prepare` (data/processed is gitignored but agents.py loads it at import).
      Commit the dataset notebook data/raw/data_gen.ipynb (checked: no secrets inside).
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
| GEMINI_API_KEY | Google Gemini | aistudio.google.com -> sign in -> Get API key -> Create API key -> copy (new keys look like `AQ.…`, 53 chars, not `AIza…`). Paste ONLY the key between the quotes, not the whole `NAME='…'` line. |
| GROQ_API_KEY | Groq (gpt-oss, Qwen) | console.groq.com -> sign in -> API Keys -> Create API Key -> copy (shown once, starts `gsk_`) |
| OLLAMA_BASE_URL | local Ollama | install from ollama.com (Windows installer), then `ollama pull <small model>`; value `'http://localhost:11434'` |
| HF_TOKEN | Hugging Face (read) | huggingface.co -> Settings -> Access Tokens -> Create new token -> type Read |
| SERVICENOW_INSTANCE | PDI URL | developer.servicenow.com -> My Instance (wake it if hibernating) -> `'https://devXXXXXX.service-now.com'` |
| SERVICENOW_USER / SERVICENOW_PASSWORD | integration user | `python -m src.integrations.create_ai_agent` creates ai.agent + roles; then in PDI open the printed sys_user link -> Set Password -> Generate (no `'` in it) -> copy -> Save Password -> paste between the quotes in .env (generated passwords are 100 chars with symbols; fine when single-quoted) |
| SERVICENOW_ADMIN_PASSWORD | only for setup scripts | developer portal -> My Instance -> Manage instance password; remove from .env after setup |
| LLM_UNDERSTANDING / LLM_RETRIEVAL / LLM_RCA / LLM_CRITIC / LLM_COMMUNICATION | per-agent model, format `provider:model` | choose from the providers' current model lists (verify IDs with check_llms.py) |

Free-tier limits apply (rate limits): the router must back off and fall back; never block the pipeline on an LLM.

---------------------------------------------------------------------------------------------------------------
## Working style the user wants
- Very clear numbered steps, exact commands, say which terminal (Admin vs normal). Give full code, not fragments.
- Step-by-step guidance for every account/API key needed (see API KEY GUIDE).
- Test changes before saying they work; show the test output. Never commit or print secrets.
- Keep the MASTER TODO LIST in this file up to date and commit after each phase.
