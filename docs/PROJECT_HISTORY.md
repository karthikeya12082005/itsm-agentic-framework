# Project history (from the planning chat with Claude, Oct–Nov 2026)

Condensed record of everything decided and built before work moved to VS Code. Read together with CLAUDE.md.

## 1. Synthetic dataset generation (Kaggle)
- Notebook `ITSM_Mistral7B_Dataset_Generation_Kaggle.ipynb` (45 cells) + guide. Mistral-7B-Instruct-v0.3,
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

## 6. ServiceNow (PDI dev195312, Brazil release)
- "Internal Integration User" replaces "Web service access only". Users: employee01 (manager set via
  Configure -> Form Layout), manager01, ai.agent (itil, approval_admin). Group setup scripted.
- Nav filter "No Results" is normal: press Enter or use direct URLs (e.g. sys_user_list.do).
- 401 errors: admin password empty because .env wasn't saved; ai.agent password with special characters likely
  mismatched. Advice: 24-char alphanumeric password, single-quoted in .env, test with check_sn_auth.py.
- Paused at that point; the user has since decided ServiceNow is REQUIRED.

## 7. Security notes
- Neon password, ENDPOINT_TOKEN, ServiceNow admin and ai.agent passwords appeared in screenshots, and an
  apikey.txt was on the desktop. All should be rotated; never put secrets in chat, screenshots or Git.

## 8. Demo script (for viva)
Prep (Admin, employee01 signed out): reset + apply scripts, delete data/tickets.db. Show datasets, check_db,
saved retrieval results, employee01 blocked sites, start endpoint service (Admin), /health, then tickets one by
one with `cli new` + `cli process --all`: ChatGPT (DENY), Gemini (ALLOW), GitHub (approval, then `cli approve`),
show employee01 Chrome (restart Chrome), `cli list`, audit log; finally stop service and restore restrictions.
