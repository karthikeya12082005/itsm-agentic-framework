# A Multi-Agent Framework for Intelligent Enterprise IT Incident Management, Root Cause Analysis, and Policy-Aware Automated Resolution

B.E. (CSE) major project, Chaitanya Bharathi Institute of Technology (CBIT), Hyderabad.
Authors: **Karre Sai Karthik**, **Kasam Revan Sai Karthikeya** · Supervisor: **Dr. I. Srujana**

A LangGraph multi-agent system that works on real ServiceNow tickets. Each agent has its own role, its own tools
and its own LLM. LLMs only extract, retrieve, review and explain; **every decision that changes something is made
by deterministic, auditable code**:

1. **Synthetic ITSM datasets.** 1500 historical tickets, 60 SOPs and 60 policies, generated with
   Mistral-7B-Instruct and validated deterministically (`data/raw/`, notebook `data/raw/data_gen.ipynb`).
2. **Policy-aware access requests on a restricted Windows endpoint.** An employee asks for a website, an AI tool
   or software. The agents decide ALLOW / DENY / ALLOW_WITH_APPROVAL / ESCALATE_FOR_REVIEW, enforce the decision
   on the laptop through a fixed action catalogue, verify the real endpoint state, and record everything on the
   ServiceNow incident. A human is involved only when a policy requires an approval.
3. **Incident resolution on simulated services.** Monitoring opens an incident when one of four local services
   breaks. A ReAct RCA agent with Bayesian root-cause ranking finds the cause. A remediation is chosen from the
   SOPs and passes a policy check, a critic and, if required, an approval. It is executed, the fix is verified
   by predicates, and it is rolled back or escalated if the fix does not hold.

---------------------------------------------------------------------------------------------------------------

## Architecture

```mermaid
flowchart LR
    subgraph People
        EMP[Employee / caller]
        APR[Approver<br/>manager01, security01, cab01 ...]
    end
    subgraph ServiceNow["ServiceNow PDI (system of record)"]
        INC[(Incident table<br/>category access + IT categories)]
    end
    subgraph Agents["Agent worker - LangGraph (src/orchestrator, src/access, src/incident)"]
        SUP{{Supervisor<br/>route + guardrails}}
        UND[Understanding]
        RET[Retrieval]
        RCA[RCA<br/>ReAct + Bayes]
        POL[Policy and Risk<br/>rule engine]
        CRI[Critic]
        EXE[Execution]
        VER[Verification]
        COM[Communication]
        CP[(Checkpoints<br/>SQLite)]
    end
    ROUTER[[LLM router<br/>Gemini / Groq / Ollama<br/>timeouts, JSON schema, fallback]]
    KB[(Knowledge store<br/>Neon Postgres + pgvector<br/>BM25 + dense + RRF)]
    EP[Endpoint enforcement service<br/>FastAPI, Administrator<br/>Chrome policy + winget]
    LAB[Simulated services<br/>web, orders-api, cache, worker<br/>+ fault injector + monitor]
    DASH[Dashboard<br/>read-only]

    EMP -- "cli new / ServiceNow form" --> INC
    LAB -- "monitor opens incidents" --> INC
    INC -- "TicketSource (REST)" --> SUP
    SUP --> UND & RET & RCA & POL & CRI & EXE & VER & COM
    UND & RET & RCA & CRI & COM -.-> ROUTER
    RET --> KB
    RCA -- "read-only tools" --> LAB
    EXE -- "fixed action catalogue, audited, rollback spec" --> EP
    EXE -- "remediation catalogue" --> LAB
    VER -- "predicates on real state" --> EP & LAB
    COM -- "work notes, comments, state" --> INC
    APR -- "cli approve (checked against ServiceNow)" --> CP
    SUP --- CP
    DASH --> INC & CP
```

**Access flow:** supervisor -> Understanding (LLM extraction, grounded in the text) -> Retrieval (LLM query
rewrite + hybrid search) -> Policy and Risk (rules) -> Critic (a different LLM plus deterministic checks; it can
only block or escalate) -> approval interrupt if needed -> Execution -> Verification (max 2 attempts, then
ESCALATE) -> Communication.

**Incident flow:** supervisor -> triage -> retrieval -> RCA (ReAct tool loop, then Bayesian ranking) -> SOP plan
plus the policy of that SOP (rules) -> critic -> approval if needed -> remediation -> predicate verification ->
next plan or rollback -> escalate.

### Design rules (never broken)
- The final policy decision is rule-based (ESCALATE if the target is unknown; DENY if prohibited or the role is
  not eligible; ALLOW_WITH_APPROVAL if approval is required or the risk is HIGH or above; otherwise ALLOW).
- Every LLM call has a timeout, JSON-schema validation, a fallback chain and a log line, plus a rule-based fallback
  that works without any API key (`--rules-only`).
- Endpoint and lab changes happen only through fixed action catalogues. Each action is audit-logged with
  action_id, ticket_id, authorization_id and a rollback spec.
- Nothing is reported RESOLVED without a passing verification predicate on the real state.
- Agents never call ServiceNow directly; they go through the `TicketSource` interface (ServiceNow or a local
  SQLite desk).
- Approvals are given with the CLI. The approver is checked against ServiceNow: the line manager or a member of
  the right group, and never the requester.

---------------------------------------------------------------------------------------------------------------

## Repository layout

| Path | What |
|---|---|
| `data/raw/` | datasets, data dictionary, validation report, generation notebook |
| `src/knowledge/` | `prepare` (texts + 80/20 stratified split), BM25, embeddings (bge-small-en-v1.5), pgvector schema, `ingest`, `retriever` (BM25 / dense / RRF hybrid), `eval_retrieval` |
| `src/common/` | config, `llm_router` (per-agent models, fallback chain, cooldowns, warm-up), `llm_stats`, `check_llms`, `check_db` |
| `src/orchestrator/` | LangGraph state, supervisor graph + `route()`, guardrails, trace, SQLite checkpointer |
| `src/access/` | access agents, critic, communication, catalogue (30 targets / 11 policies), ticket sources, worker, CLI |
| `src/incident/` | incident agents, RCA (ReAct), Bayesian ranking, evidence features, read-only tools |
| `src/lab/` | simulated services, supervisor, fault injector (8 faults), remediation actions, monitor |
| `src/integrations/` | ServiceNow REST client, auth check, integration-user creation, idempotent setup |
| `src/dashboard/` | read-only web dashboard |
| `endpoint_service/` | Windows enforcement service (Chrome policies for `employee01`, winget installs), scripts |
| `evaluation/` | `eval_access`, `eval_incident`, `ablations`, labelled requests, `results/` |
| `scripts/` | demos, demo reset, pre-commit secret scan |
| `tests/` | offline unit tests (stdlib `unittest`, fake LLMs, fake endpoint, fake lab) |

---------------------------------------------------------------------------------------------------------------

## Setup (Windows 10/11)

Requirements: Python 3.12, Git, about 3 GB of disk space (Python packages and the embedding model), internet
access. Accounts: Neon (free), a ServiceNow Personal Developer Instance (free), a Google AI Studio key (free) and a
Groq key (free). Ollama and a Hugging Face token are optional. Without any LLM key everything still runs with
`--rules-only`.

### 1. Clone and install
```powershell
git clone https://github.com/karthikeya12082005/itsm-agentic-framework.git
cd itsm-agentic-framework
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure `.env`
```powershell
Copy-Item .env.example .env
notepad .env          # put every value between single quotes: NAME='value'
```
| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon: console.neon.tech -> project -> Connect -> connection string (`sslmode=require`) |
| `ENDPOINT_URL` | `'http://127.0.0.1:8765'` |
| `ENDPOINT_TOKEN` | any random 40 characters: ``-join ((48..57)+(65..90)+(97..122) \| Get-Random -Count 40 \| % {[char]$_})`` |
| `SERVICENOW_INSTANCE` | `'https://devXXXXXX.service-now.com'` (developer.servicenow.com -> My Instance) |
| `SERVICENOW_USER` / `SERVICENOW_PASSWORD` | `'ai.agent'` and its password (step 4) |
| `SERVICENOW_ADMIN_PASSWORD` | only while running the setup scripts in step 4; empty afterwards |
| `GEMINI_API_KEY`, `GEMINI_MODEL_LITE` | aistudio.google.com -> Get API key; `'gemini-3.5-flash-lite'` |
| `GROQ_API_KEY` | console.groq.com -> API Keys |
| `OLLAMA_BASE_URL` | `'http://localhost:11434'` (optional) |
| `HF_TOKEN` | huggingface.co -> Settings -> Access Tokens -> Read (optional) |
| `LLM_UNDERSTANDING` ... `LLM_COMMUNICATION` | `provider:model` per agent, e.g. `'groq:openai/gpt-oss-20b'`; see `python -m src.common.check_llms --list` |

`.env` is git-ignored. Run `python scripts/check_staged_secrets.py` before every commit.

### 3. Knowledge store (Neon + pgvector)
```powershell
python -m src.common.check_db          # connection + pgvector
python -m src.knowledge.prepare        # data/raw -> data/processed (texts + train/test split)
python -m src.knowledge.ingest         # schema + embeddings -> Neon (re-runnable)
```
`data/processed` is git-ignored, but the agents load it at import time, so `prepare` is required.

### 4. ServiceNow PDI (one time)
1. Wake the instance on developer.servicenow.com and put the admin password into `SERVICENOW_ADMIN_PASSWORD`.
2. `python -m src.integrations.create_ai_agent`: creates the integration user `ai.agent` with its roles and
   prints a link. Open it, click **Set Password -> Generate**, copy the password into `SERVICENOW_PASSWORD`, then
   click **Save Password**.
3. `python -m src.integrations.servicenow_setup`: creates the user records (employee01, manager01 and the
   approvers) and the groups (IT Security, AI Service Desk, Change Advisory Board, ...). It is idempotent.
4. Clear `SERVICENOW_ADMIN_PASSWORD` again, then check:
   `python -m src.integrations.check_sn_auth` (HTTP 200) and `python -m src.integrations.servicenow_setup --test`.

### 5. LLMs
```powershell
python -m src.common.check_llms --list      # live model ids per provider
python -m src.common.check_llms --agents    # each agent's chain answers through the router
```

### 6. Restricted endpoint (Objective 2 on a real laptop; Administrator terminal)
```powershell
powershell -ExecutionPolicy Bypass -File .\endpoint_service\scripts\create_employee01.ps1
# sign in once as employee01 (creates its profile), sign out again, then:
powershell -ExecutionPolicy Bypass -File .\endpoint_service\scripts\apply_employee_policies.ps1
python -m uvicorn endpoint_service.server:app --host 127.0.0.1 --port 8765     # keep this terminal open
```
Check it from a normal terminal with `python endpoint_service\test_endpoint.py`. Chrome must be restarted for
`employee01` to pick up new policies.

---------------------------------------------------------------------------------------------------------------

## Running

### One-command demos (normal terminal)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo_access.ps1 -AutoApprove       # 5 access requests, end to end
powershell -ExecutionPolicy Bypass -File scripts\demo_incident.ps1 -Fault bad_config -AutoApprove
powershell -ExecutionPolicy Bypass -File scripts\reset_demo.ps1                     # dry run: what would be undone
powershell -ExecutionPolicy Bypass -File scripts\reset_demo.ps1 -Apply -CancelOpen  # undo grants, cancel open tickets
```
Add `-Local` to use the offline SQLite desk instead of ServiceNow and `-RulesOnly` to run without LLMs. Without
`-AutoApprove`, the demo prints the `approve` command for you to run. The reset rolls back only grants that are
still live on the endpoint (domain still allowlisted, package still installed), newest first, through the
enforcement service. It reads the service's audit log, so run it from the same checkout as the endpoint service.
Only one checkout's lab can run at a time, because the lab services use fixed ports 9101-9104.

### Access requests by hand
```powershell
python -m src.access.cli new "Please give me access to github.com to clone dependencies"
python -m src.access.cli worker               # polls ServiceNow (Ctrl+C to stop); --once = one pass
python -m src.access.cli approve INC0010060 --by security01      # or --reject; the wrong person is refused
python -m src.access.cli show INC0010060      # ticket + work notes
python -m src.access.cli trace INC0010060     # agent trace: model, latency, fallback per step
python -m src.access.cli list | grants | sweep
```
Options: `--source local`, `--rules-only`, `--llm-single groq:openai/gpt-oss-20b`, `--no-retrieval`,
`--no-rewrite`, `-v` (log every LLM call).

### Incidents on the simulated services
```powershell
python -m src.lab.supervisor start            # web 9101, orders-api 9102, cache 9103, worker 9104 (no admin, no Docker)
python -m src.lab.faults list
python -m src.lab.faults inject memory_leak
python -m src.lab.monitor --once              # opens a ServiceNow incident for each independent failure
python -m src.access.cli worker --once        # the same worker handles incidents
python -m src.lab.supervisor reset            # healthy again
```

### Dashboard
```powershell
python -m src.dashboard                       # http://127.0.0.1:8800  (--source local for the offline desk)
```
It shows the system status (ServiceNow, endpoint service, lab), the tickets, each ticket's route, agent trace,
policy, critic, RCA ranking, remediation and work notes, the evaluation tables, and per-model LLM latency and
fallbacks. The dashboard is read-only.

### Tests and evaluation
```powershell
python -m unittest discover -s tests -t .                  # offline: fake LLMs, fake endpoint, fake lab
python -m src.knowledge.eval_retrieval [--category]        # BM25 vs dense vs hybrid on 301 test tickets
python -m evaluation.eval_access                           # Objective 2 (dataset + 52 labelled requests)
python -m evaluation.eval_incident                         # Objective 3 (8 faults x reps + 194 dataset incidents)
python -m evaluation.ablations --part access incident rewrite latency
```
Results are written to `evaluation/results/*.md|json|csv`. Free-tier LLM rate limits apply, so the evaluations
pace their calls (`--pace`) and report how many LLM calls were answered and how many fell back.

---------------------------------------------------------------------------------------------------------------

## Results (Nov 2026; details in `evaluation/results/`)

**Retrieval** (301 held-out tickets, category filter): hybrid BM25 + dense + RRF reaches SOP Hit@1 0.887,
Hit@3 0.980; policy Hit@1 0.857, Hit@3 0.980; similar-incident P@5 0.980.

**Access requests** (52 labelled requests, full graph, simulated endpoint):

| | rules-only | multi-LLM agentic |
|---|---|---|
| Decision exact match | 0.981 | 0.981 |
| Automation rate | 0.846 | 0.827 |
| Escalation rate | 0.135 | 0.154 |
| Verification pass rate | 1.0 | 1.0 |
| MTTR (batch) | 0.43 s | 4.35 s |

On the dataset's 77 access tickets, the rule engine matches the label in 0.844 of cases, and in 1.000 when the 12
labels that cannot be derived from policy and role are excluded.

**Incident resolution** (8 injected faults x 2 repetitions):

| | rules-only | multi-LLM agentic |
|---|---|---|
| Root-service accuracy | 1.0 | 1.0 |
| RCA top-1 / top-3 | 1.0 / 1.0 | 0.938 / 1.0 |
| Correct remediation action | 1.0 | 1.0 |
| Resolution rate | 1.0 | 1.0 |
| MTTR mean (injection -> verified) | 18.4 s | 25.8 s |

On 194 held-out dataset incidents, top-1 / top-3 root-cause accuracy from creation-time text is 0.196 / 0.521
with the prior alone and 0.268 / 0.588 with naive Bayes. Adding the LLM hypothesis gives 0.283 / 0.633
(measured on 60 incidents).

**Ablations** (`evaluation/results/ablations_summary.md`):
- **Rules vs agentic, single vs multi-LLM:** policy decisions and final states are the same in every variant,
  because the rule engine decides.
  - Access: final-state match 0.981 in every variant; decision match 0.962 with single qwen.
  - Incidents: all 8 faults resolved with rules, multi-LLM, and single qwen3.8-27b.
  - With single gpt-oss-20b, one fault (memory leak) was escalated: the small model's hypothesis lowered the
    posterior to 0.458, below the 0.5 confidence gate, so the system escalated instead of acting.
- **What the LLMs add:**
  - Grounded extraction of free text.
  - A stricter critic: one more access request is escalated (automation 0.827 vs 0.846).
  - Better retrieval queries: the query rewrite raises SOP Hit@1 from 0.438 to 0.604 and policy Hit@1 from 0.312
    to 0.417 on free-text requests.
  - Readable messages to the user.
- **Cost:** about 3–4 s per access ticket, compared with 0.4 s for rules only.
- **Median latency per answered call:**

  | Model | Median latency | Called attempts answered |
  |---|---|---|
  | Groq qwen3.8-27b | 0.77 s | 0.997 |
  | Groq gpt-oss-20b | 0.92 s | 0.984 |
  | Groq gpt-oss-120b | 0.99 s | 0.852 |
  | Gemini 3.5-flash-lite | 1.46 s | 0.896 |
  | Gemini 3.5-flash | 2.01 s | 0.479 (free-tier quota) |
  | Ollama llama3.2:3b (local) | 4.14 s | - |

## Known limitations
- Free-tier LLMs (Gemini 429/503, Groq tokens per minute) often fall back to the secondary model or to rules. The
  router logs every fallback, and decisions stay correct because they never depend on an LLM.
- VS Code cannot be installed while it is running. The agents detect this and ask the user to close it.
- The synthetic dataset's root-cause labels are only weakly tied to the ticket text, so the dataset RCA numbers
  are low in absolute terms; the injected-fault lab is the main RCA benchmark.
- Access requests are incidents with category `access`; a Service Catalog front end is future work.
