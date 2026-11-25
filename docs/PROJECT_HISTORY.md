# Project history (from the planning chat with Claude, Oct–Nov 2026)

Condensed record of everything decided and built before work moved to VS Code. Read together with CLAUDE.md.

## 1. Synthetic dataset generation (Kaggle)
- Notebook `ITSM_Mistral7B_Dataset_Generation_Kaggle.ipynb` (45 cells) + guide; saved in the repo as
  `data/raw/data_gen.ipynb` (scanned Nov 24 2026: no secrets; HF token was read via Kaggle UserSecretsClient). Mistral-7B-Instruct-v0.3,
  4-bit NF4 (bitsandbytes), Kaggle 2x T4. Model loaded in 125 s.
- Principle: Python decides structure (categories, IDs, decisions, roles, risk, distributions); the LLM writes only
  text fields, prompted with fixed facts + linked SOP + linked policy. Checkpoint/resume.
- Validation fixes made during the runs:
  - Cell 20: NULLISH_RE / is_nullish / fill_nullish; placeholder regex matches whole values only (`^n/?a\.?$|^none\.?$`).
  - Cell 27 v2: negation-aware `bypasses_approval`; `policy_defaults(b)`; DENY check = prohibited_action >= 3 words.
    (POL-0030 had been rejected 15 times before this fix.)
  - Cell 31: rule 6 (never answer N/A/None/Unknown); FIELD_GUIDE for root_cause and execution_result.
  - Cell 32: `process_ticket_output` fills contributing_factors, and actions_taken for NOT_EXECUTED.
  - Cell 43: export only if final validation PASS; CSV round-trip check.
  - Ticket acceptance improved from ~55% to ~92%.
- Final: SOPs 71 attempts for 60; policies 87 for 60; tickets 1577 for 1500 (77 rejected). PASS. A session stop at
  1150/1500 was resumed from checkpoints.
- Quality review: 175 thin RCA/execution texts and 10 RAG query leaks. Decision: keep dataset; evaluate RCA on
  root_cause_category; keep leak tickets out of the retrieval test set.

## 2. Report decisions (Chapters 1–3 done; Chapter 4/5 drafted with a separate prompt)
- Five algorithms in the report: BM25, cosine similarity, RRF, Bayesian root-cause ranking, rule-based policy decision.
- Agent design change: Policy & Risk, Execution and Verification are deterministic code; LLMs only extract/explain.
- Sections 3.4 (Algorithm Design) and 3.5 (Model Description: agents and models, knowledge resources table,
  evaluation benchmarks, evaluation metrics) were rewritten; dataset creation is not described in Chapter 3.
  Knowledge-resources action catalogue row: "derived from the SOPs' action types".
- Chapter 4 screenshots used: Kaggle model load (Cell 17), ticket prompt builder (Cell 31), export PASS (Cell 43),
  retrieval evaluation tables, chatgpt.com blocked for employee01, chrome://policy after grants, decide() code,
  access-agent demo + approval output.

## 3. Restricted endpoint (Objective 2, part 1)
- Created local standard user employee01 (create_employee01.ps1). Chrome policies written into employee01's hive
  (HKEY_USERS\<SID> via reg load/unload of NTUSER.DAT): URLBlocklist of 10 domains, empty URLAllowlist,
  DownloadRestrictions=3, IncognitoModeAvailability=1; Edge URLBlocklist "*". Verified: chatgpt blocked,
  downloads blocked, Edge blocked, regedit access denied.
- Enforcement service (FastAPI) must run in Terminal (Admin) — running it in the normal VS Code terminal fails.
  Grant, rollback (needs action_id from the audit log, not the SID) and negative tests passed.
  `$last = Get-Content endpoint_service\audit\actions.jsonl | Select-Object -Last 1 | ConvertFrom-Json`
- employee01 must be signed out before running reset/apply scripts; Chrome must be restarted to pick up new policy.

## 4. Knowledge store
- Neon PostgreSQL 18.6 + pgvector 0.8.6; tables loaded 60 / 60 / 1500 (train 1199, test 301).
- Fix: `if qvec_literal is None:` (numpy truth-value bug).
- Retrieval results: see CLAUDE.md.

## 5. Access-request agents (Objective 2, part 2)
- LangGraph flow: understand -> retrieve -> decide -> (execute -> verify [retry <2] -> communicate) |
  request_approval -> END (resume later via START routing) | communicate.
- Demo results: ChatGPT DENY (POL-0030, Gemini alternative); Gemini ALLOW, verified ~1 s; GitHub
  ALLOW_WITH_APPROVAL -> approved -> RESOLVED; VS Code ALLOW -> install FAILED twice -> ESCALATED.
- VS Code issue: first `is_installed` saw the per-user install (fixed: `--scope machine`). Then installer exit
  code 1 — log: "Setup has detected that Visual Studio Code is currently running" (silent install cancels).
- k-NN confidence low for real AI tool names (ChatGPT 0.197, Gemini 0.0) because the dataset uses fictional
  names; GitHub 0.817, VS Code 1.0. Decisions stay correct (catalogue + rules).
- Extraction ran rule-based (no GEMINI_API_KEY).
- Approval was given by manager01 although POL-0025 names SECURITY_TEAM -> approver role check needed.

## 6. ServiceNow (PDI dev195312, Brazil release — replaced by dev219979 in Nov 2026; new instance starts empty)
- "Internal Integration User" replaces "Web service access only". Users: employee01 (manager set via
  Configure -> Form Layout), manager01, ai.agent (itil, approval_admin). Group setup scripted.
- Nav filter "No Results" is normal: press Enter or use direct URLs (e.g. sys_user_list.do).
- 401 errors: admin password empty because .env wasn't saved; ai.agent password with special characters likely
  mismatched. Advice: 24-char alphanumeric password, single-quoted in .env, test with check_sn_auth.py.
- Paused at that point; the user has since decided ServiceNow is REQUIRED.

## 7. Security notes
- Neon password, ENDPOINT_TOKEN, ServiceNow admin and ai.agent passwords appeared in screenshots, and an
  apikey.txt was on the desktop. All should be rotated; never put secrets in chat, screenshots or Git.
- Nov 24 2026 (Phase 0.2): rotated. Neon password reset twice (the first new one was pasted into chat), ENDPOINT_TOKEN
  regenerated locally by Claude (40 chars, never displayed), ServiceNow admin password reset (it was visible in a
  dev219979 screenshot), old HF token revoked and OneDrive\Desktop\apikey.txt (an HF token) deleted.
  Old ai.agent password is moot (old instance gone).

## 8. Demo script (for viva)
Prep (Admin, employee01 signed out): reset + apply scripts, delete data/tickets.db. Show datasets, check_db,
saved retrieval results, employee01 blocked sites, start endpoint service (Admin), /health, then tickets one by
one with `cli new` + `cli process --all`: ChatGPT (DENY), Gemini (ALLOW), GitHub (approval, then `cli approve`),
show employee01 Chrome (restart Chrome), `cli list`, audit log; finally stop service and restore restrictions.

## 9. Move to VS Code / Claude Code — Phase 0 (Nov 24 2026)
- Repo scan: everything in CLAUDE.md exists EXCEPT `src/integrations/` (empty — the ServiceNow scripts were never
  committed; to be rewritten in 0.7 / 1.1 / 1.2). Offline check: decide() gives the expected decision for all 15
  catalogue targets. Found: rule-based extraction maps "I need ChatGPT to debug my Python code" to a Python install
  (TODO 2.4); rollback gaps (3.6); 1500 s endpoint timeout (3.7); data/processed must be rebuilt on a new machine (5.4).
- ServiceNow PDI changed: dev195312 -> **dev219979** (Brazil, empty instance; users/group/catalog items to recreate).
- 0.1 `.env.example` (17 keys) committed; `.gitignore` ignores `.env`, `*.env`, `.env.*` but keeps the template.
- 0.2 secrets rotated (see section 7). Endpoint tests: /health 200, /status with new token 200 (10 domains blocked,
  allowlist empty, DownloadRestrictions 3), wrong/no token 401. check_db OK (Neon now PostgreSQL 18.7).
- 0.3 Gemini: new key format `AQ.…` (53 chars). Key list has 49 generateContent models; gemini-2.5-flash-lite
  returns 404 for new users -> GEMINI_MODEL_LITE='gemini-3.5-flash-lite' (~1.3 s). One call timed out at 30 s.
- 0.4 Groq: no Llama chat models any more (only llama-prompt-guard classifiers). Chat models with JSON mode:
  openai/gpt-oss-20b (~0.5 s), openai/gpt-oss-120b, qwen/qwen3.8-27b, allam-2-7b.
- 0.5 Ollama 0.40.18 installed with winget to D:\Ollama (C: has ~6 GB free), OLLAMA_MODELS=D:\Ollama\models,
  llama3.2:3b (1.9 GB) on RTX 2050: cold ~9 s, warm ~2.6 s. First-ever load crashed once (CUDA v13 init, 0xc0000409);
  not reproducible. Only ~0.8 GB RAM free during load -> Ollama is a fallback, not a primary provider.
- Lesson: `eval_retrieval --limit 3` overwrote the saved 301-ticket results; restored from git and the script now
  refuses to save partial runs.
- Working style: Claude runs every terminal command itself; the user only does browser/ServiceNow/Admin-terminal
  steps and types secrets into .env (never into chat).
- 0.6 New HF read token (verified with whoami); HF warning gone.
- 0.7 ServiceNow dev219979: admin password correct (web login OK) but REST Basic auth 401. Cause: Basic-auth
  restriction enforced since 2026-11-12 — only users with role `snc_basic_auth_api_access` may use Basic auth on
  the API (proven by granting admin the role temporarily: 401 -> 200; role removed). Wrote check_sn_auth.py and
  create_ai_agent.py (admin via web session + X-UserToken). Setting user_password through the Table API is accepted
  but ineffective on this release (tried full update, password-only update, WSAO flag; background script via
  sys.scripts.do did not run). Final fix: ai.agent (Internal Integration User; itil, approval_admin,
  snc_basic_auth_api_access) + password set by hand with Set Password -> Generate -> pasted into .env.
  check_sn_auth: HTTP 200 twice. Admin password not rotated yet (user's choice) -> reset at the end of 1.2.
- 0.8 requirements.txt pinned (psycopg 3.3.6, pandas 3.1.0, numpy 2.5.4, sentence-transformers 6.2.0,
  huggingface_hub 2.4.0, langgraph 1.2.19, pydantic 2.14.2, fastapi 0.162.0, uvicorn 0.54.0, requests 2.34.2,
  python-dotenv 1.2.4); clean install + pip check; fresh-venv dry-run resolves. .gitignore extended for checkpoint
  DBs, logs and the audit folder (0 tracked files affected).
- **Phase 0 complete.** Next: Phase 1 (ServiceNow client 1.1, setup 1.2 incl. admin password reset, ...).

## 10. Phase 1 — ServiceNow integration (Nov 24 2026, branch phase-1-servicenow)
- Start check: check_db OK (PG 18.7), check_sn_auth 200, check_llms Gemini/Groq/Ollama OK (Gemini 11.7 s).
- 1.1 `servicenow_client.py` (Basic auth as ai.agent, retries/backoff/Retry-After, POST retried only when no
  record can exist, typed errors incl. hibernation). Live smoke found what mocks missed: read ACLs filter rows
  AFTER a page is fetched (19 of 20) -> paging stops on X-Total-Count; sys_journal_field (0 of 3 readable) and
  sys_choice (403) are hidden from ai.agent -> journal read from the record's own field; RITM states come from
  task.state.
- **Scope decision (user): LEAN design.** Asked whether catalog items / human logins are required: no. Incident
  table for everything (access requests = category `access`, built-in "Access & Password"), user records
  without logins (employee01 DEVELOPER/manager01, manager01, security01), groups "IT Security" (=SECURITY_TEAM)
  and "AI Service Desk" (ai.agent), approvals given in the CLI and recorded in ServiceNow.
- 1.2 setup mistakes and fixes: the first run overwrote the label of the existing `access` choice (restored
  from sys_update_version; choices are now create-only); group members were posted by name (worked by display
  value) but looked up by sys_id -> duplicate insert 403 on re-run (fixed). sysapproval_approver cannot be
  created on incidents by non-admins: field ACLs blank approver/sysapproval (HTTP 201, empty fields; 4 orphan
  rows deleted). User chose to record approvals in incident.approval + work notes. Admin password reset by the
  user; old one rejected; SERVICENOW_ADMIN_PASSWORD cleared from .env; setup --test passes as ai.agent only.
- 1.3 `ticket_source.py` (TicketSource; ServiceNowSource default, LocalSource for tests) + `worker.py` + CLI
  `--source`. Resolving needs a data-policy close_code from a STRICT choice list with EN DASHES ("Resolved –
  Permanent Fix Applied"; list via /api/now/ui/meta/incident); wrong values are silently blanked -> client now
  raises ServiceNowFieldDropped whenever a written field comes back blank. Escalations go to "Service Desk".
  Agent context for resume kept in data/agent_state.db (checkpointer replaces it in 2.8).
- 1.4 worker live: work notes written within one second came back out of order (Policy before Understanding)
  -> one numbered "Agent trace" note per run + per-ticket note sequence "[#n agent]".
- 1.5 approvals: wrong group, wrong manager and self-approval refused (noted on the ticket, CLI exit 2).
- 1.6 end to end with the endpoint service: ChatGPT Canceled; Gemini allowlisted + verified + Resolved;
  GitHub refused for manager01, approved by security01, allowlisted + verified + Resolved. Endpoint state and
  audit log checked independently; both grants rolled back afterwards (restrictions restored).
- 68 offline unit tests (stdlib unittest). Test incidents INC0010001-24 remain on the PDI (labelled or closed).
- User asked whether other services/apps must be supported: yes -> TODO 3.8 (catalogue extension per policy).
- **Phase 1 complete.** Next: Phase 2 (LLM router with per-agent models, supervisor, critic, checkpointer).

## 11. Phase 2 — Multi-agent LangGraph with multiple LLMs (Nov 24 2026, branch phase-2-multi-llm)
- Start check: check_db OK, check_sn_auth 200, check_llms all OK, 68 tests OK. Extra pings: gemini-3.5-flash
  2.7-3.3 s, gpt-oss-120b 1.2-1.4 s, qwen3.8-27b 0.8-0.9 s, gemini-3.1-flash-lite 503 (excluded).
- Model map (user chose "mixed providers"): fast Groq models where a ticket waits (understanding, RCA,
  communication), Gemini for retrieval query rewriting and for the critic (different vendor and model family).
- Provider probe: Groq accepts reasoning_effort=low for gpt-oss (fewer hidden reasoning tokens); Gemini 3.x
  accepts thinkingConfig.thinkingLevel (minimal for lite, low for flash). gemini-3.5-flash returned 503 three
  times then a 30 s read timeout during the probe -> this is the cause of the old "first ticket 20-45 s".
- 2.1 router design: chain primary -> other provider [-> Ollama opt-in] -> rule-based; per-agent timeout and a
  total budget; cooldown per model after 429/5xx/timeout (120 s) or 401/403/404 (900 s) so a failing model is
  skipped immediately for the rest of a worker run; one repair retry with the validation error; critic chain
  never contains the Understanding model. Live: retired model id -> 404 -> secondary; dead proxy -> rule-based.
- 2.2 `check_llms --agents`: all five agents answered by their primary model live.
- Graph redesign (2.3-2.9 built together, ticked one by one): new package `src/orchestrator/` (typed AgentState,
  supervisor hub-and-spoke graph with a pure `route()` function, guardrails, trace, checkpoint helpers).
  Agents: Understanding (LLM + grounding check), Retrieval (LLM query rewrite), Policy & Risk (unchanged rule
  engine), Critic (new, rules + LLM, only ALLOW/AWA plans, before approval), approval as LangGraph interrupt,
  Execution, Verification, Communication (LLM phrasing + text checks). Routing is never decided by an LLM.
- Prototype first: interrupt() + SqliteSaver across two processes works on langgraph 1.2.19 /
  langgraph-checkpoint-sqlite 3.1.3 (invoke returns `__interrupt__`; Command(resume=...) continues).
- ServiceNowSource no longer uses data/agent_state.db: decision/target/context are read from the checkpoint;
  the per-ticket note sequence is computed from the notes already on the incident (correct across processes).
- 2.4 rule fix: aliases for AI tools named without a domain + candidate scoring (request word before the
  mention, explicit domain), file names like script.py ignored. An LLM target that does not appear in the text is
  rejected (hallucination guard).
- Live run 1 (INC0010028-32) found two problems the fake-LLM tests could not:
  1) first ticket 29 s although its LLM call took 1 s: the k-NN confidence lookup lazily loads the embedding model
     (~22 s) -> embedding warm-up at worker start; plus LLM warm-up pings every agent model in parallel and puts a
     down model (gemini-3.5-flash timed out) into cooldown before the first ticket; cooldown doubles on repeated
     failures (max 900 s). First ticket now 7.7 s.
  2) the LLM critic (gpt-oss-120b) escalated a valid Gemini request: POL-0028's SOP action is ADD_GROUP_MEMBERSHIP
     for the dataset's fictional "Orbit Assist". The critic was right that the texts differ; the catalogue mapping
     had not been given to it -> CATALOGUE_NOTE + "action equivalence" fact. Checked live on both critic models.
- Groq rate limit: gpt-oss-120b returned 429 after ~4 critic calls in a minute; with the LLM critic unavailable,
  the rule checks alone approved "Please REMOVE github.com" (a grant plan) -> deterministic removal-intent check.
- Live run 2 (INC0010033-38): all six as expected; GitHub approved in a second process (manager01 refused,
  security01 accepted) -> executed, verified, RESOLVED; notes in ServiceNow in true order with model + latency.
  Endpoint checked independently (allowlist gemini + github, audit with INC + AUTH + rollback spec), both
  rolled back with the new `endpoint_client rollback` (allowlist [] again, DownloadRestrictions 3).
- Live sweep: INC0010039 (acknowledged, never run) -> re-run -> Canceled; INC0010040 (finished, not saved) ->
  re-saved -> Escalated; second sweep empty.
- One run per provider with --llm-single on the local desk: groq / gemini / ollama all give the same decisions
  (8.9 s / 15.3 s / 41.3 s for 2 tickets).
- Deleting data/tickets.db for a clean local run was blocked by the auto-mode safety check -> tests use explicit
  ticket numbers instead (no need to delete user data).
- 118 offline tests OK (router 19, multi-agent 30 with a scripted FakeLLM, worker/source updated).
- **Phase 2 complete.** Next: Phase 3 (process pre-check, rollback correctness, async installs, catalogue, eval).

## 12. Phase 3 — Objective 2 hardening and evaluation (Nov 24-25 2026, branch phase-3-hardening)
- User decisions: 3.8 = level 1 (data-only catalogue additions); all five missing approver groups created.
- 3.2: catalog.APPROVER_GROUPS covers every approver role in the dataset (SECURITY_TEAM, IT_SERVICE_OWNER,
  CHANGE_ADVISORY_BOARD, AI_GOVERNANCE_BOARD, APPLICATION_OWNER, DATA_OWNER; LINE_MANAGER = caller's manager).
  Admin password put back by the user for one setup run, cleared again by Claude; setup idempotent.
- Endpoint service v2 (one restart): pure logic in endpoint_service/core.py (unit-tested without admin);
  pre-check of running processes, installs refused while the app runs, async install jobs, before-state
  rollback (RESTORE_DOMAIN), uninstall --scope machine, double rollback refused, expires_at + GET /grants,
  missing managed domains added to the blocklist at start. FastAPI lifespan instead of deprecated on_event.
- New waiting states in the graph, both LangGraph interrupts resumed by the worker (tend_active each poll):
  await_job (install running; ticket stays In Progress) and await_user (app open; ticket On Hold / Awaiting
  Caller; automatic install once closed, closed with "resubmit" after 24 h). The stuck sweep skips them.
- ServiceNow finding: the On-Hold reason field on this Brazil PDI is `hold_reason` (1 = Awaiting Caller), not
  the documented `on_hold_reason` - found by reading /api/now/ui/meta/incident before writing it.
- Live endpoint test `endpoint_service\test_endpoint.py` (rewritten for v2): RESULT OK. Its first run failed
  on a test bug (authorization_id "T" shorter than the service's 3-character minimum -> 422, correct).
- Live on ServiceNow (INC0010042-47): VS Code (open) -> On Hold / Awaiting Caller -> time-out -> Canceled with
  "close it and submit a new request"; 7-Zip -> In Progress (job) -> sweep -> verified -> Resolved -> rollback
  uninstalled it (verify: not installed); "gemini.google.com for 2 minutes" -> Resolved until ..., expiry
  sweep revoked it with a note; Hugging Face -> security01 refused, aigov01 approved -> Resolved;
  qBittorrent and dropbox DENY. Endpoint restored (allowlist [], DownloadRestrictions 3).
- Durations accept minutes (needed to test expiry live; also good for the demo).
- 3.5 evaluation (evaluation/eval_access.py, results in evaluation/results/access_eval_summary.{json,md} + CSVs):
  dataset test split, 77 access/service tickets: rule engine with the ticket's own policy 84.4 % exact match
  (100 % excluding the 12 ESCALATE labels, all from the dataset scenario "escalated_no_permitted_action");
  hybrid-retrieved policy Hit@1 68.8 % -> decision exact match 70.1 %. Catalogue requests (52 hand-labelled):
  rules-only 51/52; agentic first run 50/52, second 49/52 - every agentic mismatch was a critic escalation:
  * R51 ("client wants ChatGPT but Gemini is fine - enable Gemini"): LLM reads Gemini correctly, the critic's
    "stricter rule reading" safety check escalates (rules read ChatGPT -> DENY). Kept on purpose (trade-off).
  * R19 / R52 (time-limited requests): the critic saw no time limit in the plan; for software the plan really
    could not honour it -> plan facts now state the limit, and time-limited installs were implemented
    (install job with expires_at; expiry rollback = uninstall).
  * R17: gpt-oss-120b escalated "for human approval" although approval is already in the plan -> explicit
    approval_step fact. (gemini-3.5-flash, the critic's primary, was in 429 quota cooldown during the runs.)
  Caveat for the report: the catalogue set was written together with the rule aliases, so the rules-only score
  is optimistic; the dataset part is the independent test.
- After the fixes, final agentic run: 51/52 (only R51), automation 0.827, MTTR 4.35 s; 30.6 % of LLM calls were
  answered by the secondary provider (Gemini 429 quota -> Groq), none fell back to rules.
- Time-limited installs live (INC0010048 "7-Zip for 2 minutes"): install job -> Resolved -> expiry sweep
  uninstalled it (verify: not installed). Endpoint restarted twice by the user in Phase 3 (v2, then installs
  with expires_at); the running schema was checked via /openapi.json because /health shows the same version.
- 142 offline tests. Test incidents INC0010041-48 on the PDI (setup test, Phase-3 live run), all closed.
- **Phase 3 complete.** Next: Phase 4 (simulated services, fault injector, RCA agent, remediation).

## 13. Phase 4 — Objective 3: incident resolution on simulated services (Nov 25 2026, branch phase-4-incidents)
- Lab (src/lab): four FastAPI services as plain local processes (web -> orders-api -> cache, worker), health /
  metrics / logs, admin endpoints behind a random token; supervisor start/stop/restart/reset without admin or
  Docker. Eight faults with ground truth (dataset root_cause_category + SOP), remediation action catalogue with
  audit + rollback specs (mirrors the endpoint service). Live: every fault injected and fixed by its action.
- Mistake caught before push: data/lab/ (incl. the lab token) was committed because data/ was not fully
  ignored. Amended (never pushed), data/lab/ ignored, token rotated, and the pre-commit scan now refuses
  token files, data/lab, data/checkpoints and logs.
- A unit test exposed that actions.AUDIT was bound at import time (tests wrote to the real audit log) -> path
  resolved at call time; the stray test records were removed.
- ServiceNow categories checked first: internal_applications, performance, hardware, database (no "monitoring"
  contact type -> left empty). Monitoring incidents: caller ai.agent, group AI Service Desk.
- Root causes chosen per fault from the SOPs' dataset labels (labels per SOP are noisy because synthetic):
  service_down SOFTWARE_DEFECT, memory_leak / high_latency RESOURCE_EXHAUSTION, disk_full CAPACITY_LIMIT,
  bad_config DEPLOYMENT_CHANGE, cert_expired CERTIFICATE_EXPIRY, db_pool_exhausted CONFIGURATION_ERROR,
  dependency_down DEPENDENCY_FAILURE. Approvals: POL-0043 CAB, POL-0044 / 0042 IT_SERVICE_OWNER, POL-0052
  APPLICATION_OWNER; POL-0041/46/48/49 ALLOW (on-call role SITE_RELIABILITY_ENGINEER, no new SN user).
- RCA design: ReAct (LLM picks read-only tools) + deterministic evidence completion + Bayesian ranking (prior
  from train split + k-NN, expert likelihood table for lab signals, LLM hypothesis as noisy observation).
  Rules-only live check: root 8/8, top-1 7/8, top-3 8/8. LLM framing differed (memory leak / crashed dependency
  called SOFTWARE_DEFECT) -> category definitions in the prompt (taxonomy, not test hints); alpha calibrated on
  train data in 4.6 instead of being tuned on the 8 faults.
- Live problems found and fixed: (1) two simultaneous unrelated faults were merged into one incident; the fix for
  one could never verify (the agent correctly escalated instead of faking RESOLVED) -> independent events by
  dependency relation, RCA scoped to the event; (2) after an OOM crash the single "FATAL: OutOfMemoryError" line
  set both crash and OOM features (naive-Bayes double count) -> separated; out-of-memory evidence selects
  SOP-0046 even when no memory metric is left; (3) the LLM critic escalated a correct restart as "only a
  mitigation" -> ITIL guidance: incident management restores service, the code fix is problem management.
- Live end to end on ServiceNow: INC0010049 broken release -> ReAct 4 tools -> DEPLOYMENT_CHANGE -> CAB approval
  (aigov01 refused, cab01 approved) -> ROLLBACK_DEPLOYMENT -> verified -> Resolved. INC0010052 memory leak and
  INC0010053 full disk (simultaneous) -> two incidents -> both Resolved automatically (34 s / 12.9 s).
  Test incidents INC0010050/51 (before the fixes) were escalated, then cancelled as superseded.
- Dataset RCA: naive Bayes with likelihoods LEARNED from the train split on creation-time text features (never
  root-cause / hypothesis fields): test top-1 0.268, top-3 0.588 vs prior-only 0.196.
- 4.6 evaluation (`python -m evaluation.eval_incident`, results in evaluation/results/incident_eval_*):
  * First run INVALID and stopped: half of the LLM calls were skipped by cooldowns (Groq free tier = 1000
    requests/day AND only 8000 tokens/minute per model; gpt-oss also spends hidden reasoning tokens; Gemini 429
    quota). Router changed: a 429 with Retry-After <= 20 s waits and retries the same model once, otherwise the
    model cools exactly Retry-After (no growth). Evaluation paced (--pace 25 dataset, 20 lab).
  * Neon closed the idle SSL connection during the slow paced run -> PgVectorIndex reconnects once.
  * Two real bugs found by the evaluation: (1) `last_deploy_minutes_ago or 1e9` treated a deployment 0.0
    minutes ago as "no deployment" -> bad_config escalated; fixed with an explicit None check + regression
    test; (2) high_latency (cache bloat) posterior 0.38 < the 0.5 confidence gate because every
    RESOURCE_EXHAUSTION manifestation had a high likelihood, so each ABSENT symptom over-penalised it ->
    manifestation likelihoods lowered (memory 0.45, OOM 0.35, latency 0.5, cache 0.35). Honest note: this
    expert-table change was made after the evaluation exposed it (lab faults are also the test set).
  * Final lab results (8 faults x 2 reps, LocalSource, approvals auto-given by the right group):
    rules: root service 1.0, RCA top-1 1.0, top-3 1.0, action 1.0, resolved 1.0, escalated 0, MTTR 18.4 s mean
    / 13.7 s median, 0 rollbacks, lab healthy after every RESOLVED. agentic: root 1.0, top-1 0.938 (LLM pushed
    dependency_down rep1 to SOFTWARE_DEFECT; still top-3 and the same RESTART_SERVICE remediation), top-3 1.0,
    action 1.0, resolved 1.0, MTTR 25.8 s / 20.8 s. In the agentic run the RCA LLM answered 11 of 20 calls and
    the critic LLM 9 of 17; the rest were rule-based fallbacks (Groq gpt-oss-120b 429 with ~6-7 min
    Retry-After after the dataset part's ~90 RCA calls; Gemini 503/429) - every decision still correct.
  * Dataset test incidents (194; root_cause_category from creation-time text): prior 0.196 / 0.521 (top-1 /
    top-3), naive Bayes 0.268 / 0.588, + k-NN prior 0.263 / 0.598, + LLM hypothesis (60 incidents) 0.283 /
    0.633; k-NN majority alone top-1 0.325, LLM alone 0.255; LLM calibration on 30 train incidents 0.267 ->
    alpha 0.267. The synthetic dataset's root-cause labels are only weakly tied to the ticket text, so absolute
    numbers are low; the ranking still improves on the prior at every step.
