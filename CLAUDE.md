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
  to re-run servicenow_setup.py, then clear again). Still empty: LLM_* agent map (2.1). Claude checks .env only by key names / lengths / masked shape — never values.
  Common paste mistakes seen: whole `NAME='…'` line pasted inside the quotes; file not saved (Ctrl+S);
  whole file overwritten instead of one line edited.
- Ollama: D:\Ollama (ollama.exe not on PATH in old terminals), models in D:\Ollama\models (user env OLLAMA_MODELS),
  tray app starts with Windows and serves http://localhost:11434.

## Code layout
- `src/common/config.py` — paths, EMBED_MODEL=BAAI/bge-small-en-v1.5 (384-d, query instruction prefix), SEED=42,
  TEST_FRACTION=0.20, RRF_K=60, BM25_K1=1.2, BM25_B=0.75, WEAK_EVIDENCE_COSINE=0.55. `check_db.py`.
- `src/common/llm.py` — `gemini_json(prompt, model=None, timeout=30)` via REST generateContent; returns None on failure.
- `src/common/check_llms.py` — pings Gemini / Groq / Ollama: lists live models, skips non-chat models, sends one
  JSON prompt, prints latency (`--provider`, `--list`). Never prints keys.
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
- `src/access/` — catalog.py (TARGETS -> policy, ALTERNATIVES, SOFTWARE_KEYWORDS, EMPLOYEES (local only),
  APPROVER_GROUPS, LOCAL_GROUP_MEMBERS), store.py (SQLite data/tickets.db, RITM9000001+), endpoint_client.py,
  agents.py (understanding [role from ticket], retrieval, decide, policy_risk, request_approval, execution,
  verification, communication), graph.py (LangGraph StateGraph; START routes new / resume-approved /
  resume-rejected), ticket_source.py (TicketSource interface + ServiceNowSource (default, incident table; agent
  context in data/agent_state.db) + LocalSource; `python -m src.access.ticket_source selftest`), worker.py
  (process_ticket, approve_ticket with approver check, run_poller), cli.py (`--source servicenow|local`; new,
  worker [--once] [--interval], process [--all], approve --by <user> [--reject], show, list, demo, --no-retrieval).
- `src/integrations/` — (the earlier scripts were never committed and were lost; rewritten for dev219979)
  check_sn_auth.py (REST login test; `--admin`; detects hibernation), create_ai_agent.py (admin web session
  class `Admin`; creates ai.agent + roles idempotently), servicenow_client.py (1.1: `ServiceNowClient.from_env()`,
  typed errors, retries, ACL-safe paging, work notes/comments/state by name/journal/RITM variables/approvals;
  `python -m src.integrations.servicenow_client smoke [--write]`), servicenow_setup.py (1.2, lean design:
  user records, groups IT Security + AI Service Desk, APPROVER_GROUPS map; setup needs admin, `--test` runs
  as ai.agent only).
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
python -m src.access.cli new "<text>" | worker [--once] | process --all | approve <INC> --by <user> [--reject] | show <INC> | list | demo
python -m src.access.cli --source local demo      # offline SQLite desk
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
- Approver identity is not checked (manager01 approved a SECURITY_TEAM item).
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
- [ ] 1.5 Approvals recorded in ServiceNow: on ALLOW_WITH_APPROVAL set incident approval=requested + On Hold +
      work note naming who may approve (LINE_MANAGER -> caller's manager, SECURITY_TEAM -> IT Security member);
      `cli approve <INC> --by <user> [--reject]` checks the user against ServiceNow (wrong person -> refused,
      noted), sets approval approved/rejected with a work note, resumes the graph.
- [ ] 1.6 End-to-end test: create access incidents for employee01 (CLI `new` writes them to ServiceNow) ->
      ChatGPT DENY, Gemini ALLOW, GitHub approval refused for manager01 and accepted for security01 -> RESOLVED;
      endpoint changes verified; notes, comments, states and approval field visible in the PDI.

## Phase 2 — Multi-agent LangGraph with multiple LLMs (REQUIRED)
- [ ] 2.1 `src/common/llm_router.py`: providers Gemini / Groq / Ollama behind one interface; per-agent model map
      from `.env`; strict JSON-schema output (pydantic) with repair/retry; timeouts; fallback chain
      (primary -> secondary provider -> rule-based); log agent, provider, model, latency, tokens, fallback used.
- [ ] 2.2 `src/common/check_llms.py`: pings every configured provider/model.
- [ ] 2.3 Supervisor/orchestrator node routing between agents (access-request flow vs incident flow).
- [ ] 2.4 Understanding Agent uses LLM extraction (schema-validated) with rule-based fallback.
      Fix rule-based bug: "I need ChatGPT to debug my Python code" -> Python.Python.3.12 (software keyword wins;
      known AI-tool names without a domain are not recognised). Add tests for such sentences.
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
- [ ] 3.2 Approver role enforcement (done in 1.5 for the CLI; extend to other approver roles, e.g.
      IT_SERVICE_OWNER / CHANGE_ADVISORY_BOARD for Objective 3, with groups created by servicenow_setup.py).
- [ ] 3.3 Revocation / time-limited access (duration variable) with automatic rollback when expired.
- [ ] 3.4 Unknown target -> ESCALATE_FOR_REVIEW; test with a domain not in the catalogue.
- [ ] 3.6 Rollback correctness: winget uninstall must use `--scope machine` (as install does); domain rollback
      must restore the recorded "before" allowlist (not revoke a grant that existed before the ticket).
- [ ] 3.7 Endpoint client timeout (1500 s) must not block the poller: run installs asynchronously or per-ticket.
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
