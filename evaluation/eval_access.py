"""Objective-2 evaluation (TODO 3.5). Saves tables in evaluation/results/.

    python -m evaluation.eval_access                         # both parts, modes rules + agentic
    python -m evaluation.eval_access --part dataset          # only the dataset test tickets
    python -m evaluation.eval_access --part catalogue --modes rules agentic single:groq:openai/gpt-oss-20b
    options: --no-retrieval, --pace SECONDS (between tickets in LLM modes; free-tier rate limits), --limit N

Part 1 - dataset: the 77 ACCESS/SERVICE_REQUEST tickets of the held-out test split (data/processed).
  The dataset uses fictional product names, so the agents' real-world catalogue cannot read them. Instead:
    oracle    rule engine (eq. 3.7) on the ticket's own policy_id + requester role
    retrieved rule engine on the top-1 policy of hybrid retrieval (BM25 + dense + RRF, category filter)
  -> decision exact match vs the dataset label authorization_decision.
Part 2 - catalogue: evaluation/access_requests.csv (52 hand-labelled realistic requests for the real catalogue,
  incl. unknown targets, prompt injection, removal requests, ineligible roles, time limits) through the FULL
  multi-agent graph on a temporary local desk with a SIMULATED endpoint (every action succeeds and verifies;
  the live endpoint is tested separately - a batch must not change the laptop). Tickets that wait for approval
  are then approved by a valid approver (checked like the CLI) to measure the resolution path.
  Metrics: target accuracy, decision exact match, first-run final-state match, automation rate, escalation
  rate, approval rate, verification pass rate, MTTA, MTTR (batch: no queueing; production adds the poll
  interval), LLM fallback share.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest import mock

from src.common.config import PROCESSED_DIR, RESULTS_DIR

ROOT = Path(__file__).resolve().parent
REQUESTS = ROOT / "access_requests.csv"
DECISIONS = ("ALLOW", "ALLOW_WITH_APPROVAL", "DENY", "ESCALATE_FOR_REVIEW")


# ===================================================================== part 1: dataset test tickets
def eval_dataset(retriever) -> tuple[list[dict], dict]:
    from src.access.agents import POLICIES, decide
    rows = []
    recs = [json.loads(line) for line in open(PROCESSED_DIR / "incidents.jsonl", encoding="utf-8")]
    test = [r for r in recs if r["split"] == "test"
            and r["record"]["ticket_type"] in ("ACCESS_REQUEST", "SERVICE_REQUEST")]
    for r in test:
        t = r["record"]
        oracle = decide(POLICIES[t["policy_id"]], t["user_role"])["decision"]
        row = {"incident_id": t["incident_id"], "category": t["category"], "role": t["user_role"],
               "label": t["authorization_decision"], "policy": t["policy_id"], "oracle_decision": oracle,
               "scenario": t.get("scenario_type", "")}
        if retriever is not None:
            res = retriever.retrieve(r["query_text"], "policies", k=3, filters={"category": t["category"]})
            top = res["items"][0]["id"] if res["items"] else None
            row.update(retrieved_policy=top, policy_hit=top == t["policy_id"],
                       retrieved_decision=decide(POLICIES[top], t["user_role"])["decision"] if top else
                       "ESCALATE_FOR_REVIEW")
        rows.append(row)
    n = len(rows)
    summ = {"tickets": n, "labels": dict(Counter(x["label"] for x in rows)),
            "oracle_exact_match": round(sum(x["oracle_decision"] == x["label"] for x in rows) / n, 3),
            "oracle_exact_match_excl_escalate_labels": round(
                sum(x["oracle_decision"] == x["label"] for x in rows if x["label"] != "ESCALATE_FOR_REVIEW")
                / max(1, sum(x["label"] != "ESCALATE_FOR_REVIEW" for x in rows)), 3),
            "escalate_label_scenarios": dict(Counter(x["scenario"] for x in rows if x["label"] == "ESCALATE_FOR_REVIEW"))}
    if retriever is not None:
        summ.update(retrieved_policy_hit1=round(sum(x["policy_hit"] for x in rows) / n, 3),
                    retrieved_exact_match=round(sum(x["retrieved_decision"] == x["label"] for x in rows) / n, 3))
    summ["confusion_oracle"] = {f"{a}->{b}": c for (a, b), c in
                                Counter((x["label"], x["oracle_decision"]) for x in rows).items()}
    return rows, summ


# ===================================================================== part 2: catalogue requests, full graph
class SimulatedEndpoint:
    def __init__(self):
        self.executed = []

    def execute(self, kind, target, ticket_id, auth, expires_at=None):
        self.executed.append((kind, target, ticket_id))
        return {"status": "SUCCEEDED", "action": "ALLOWLIST_DOMAIN" if kind == "domain" else "INSTALL_SOFTWARE",
                "action_id": f"SIM-{len(self.executed)}", "expires_at": expires_at}

    def verify(self, kind, target):
        return True

    def precheck(self, package_id):
        return []


def _set_mode(mode: str) -> None:
    for k in ("LLM_MODE", "LLM_SINGLE"):
        os.environ.pop(k, None)
    if mode == "rules":
        os.environ["LLM_MODE"] = "off"
    elif mode.startswith("single:"):
        os.environ["LLM_MODE"], os.environ["LLM_SINGLE"] = "single", mode.split(":", 1)[1]
    elif mode != "agentic":
        raise SystemExit(f"unknown mode {mode} (rules | agentic | single:provider:model)")


def _secs(a, b):
    if not (a and b):
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def run_mode(mode: str, requests: list[dict], retriever, pace: float) -> list[dict]:
    from src.access.agents import TARGETS
    from src.access import endpoint_client
    from src.access.ticket_source import LocalSource
    from src.access.worker import approve_ticket, process_ticket
    from src.common import llm_router
    from src.orchestrator import checkpoint as cp
    from src.orchestrator.graph import build_graph

    _set_mode(mode)
    llm_router.reset_cooldowns()
    if mode != "rules":
        print("  warm-up:", llm_router.warmup())
    ep = SimulatedEndpoint()
    out = []
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(endpoint_client, "execute", side_effect=ep.execute), \
            mock.patch.object(endpoint_client, "verify", side_effect=ep.verify), \
            mock.patch.object(endpoint_client, "precheck", side_effect=ep.precheck):
        src = LocalSource(Path(tmp) / "eval.db")
        saver = cp.memory_saver()
        graph = build_graph(retriever, checkpointer=saver)
        for i, rq in enumerate(requests, 1):
            t0 = time.perf_counter()
            n = src.create(rq["requester"], rq["text"])
            first = process_ticket(src, graph, n)
            wall = time.perf_counter() - t0
            v = cp.values(saver, src.thread_id(n))
            u, p = v.get("understanding") or {}, v.get("policy") or {}
            approved = None
            if first == "AWAITING_APPROVAL":
                t = src.get(n)
                who = sorted(src.approvers(p.get("approver_role"), t) - {t["requester"]})
                if who:
                    approved = approve_ticket(src, graph, n, who[0], True)[0]
            t = src.get(n)
            v = cp.values(saver, src.thread_id(n))
            trace = v.get("trace", [])
            llm_steps = [s for s in trace if s.get("model") not in ("deterministic", "human")
                         and s["agent"] not in ("Supervisor",)]
            exp_t = rq["expected_target"] or None
            pred_t = u.get("target")
            target_ok = (pred_t == exp_t) if exp_t else (pred_t not in TARGETS)
            row = {"mode": mode, "id": rq["id"], "requester": rq["requester"], "tags": rq["tags"],
                   "expected_target": exp_t, "target": pred_t, "target_ok": target_ok,
                   "expected_decision": rq["expected_decision"], "decision": p.get("decision"),
                   "decision_ok": p.get("decision") == rq["expected_decision"],
                   "expected_final": rq["expected_final"], "first_final": first,
                   "final_ok": first == rq["expected_final"], "after_approval": t["state"], "approved": approved,
                   "critic": (v.get("critic") or {}).get("verdict"),
                   "critic_concerns": "; ".join((v.get("critic") or {}).get("concerns") or [])[:400],
                   "extraction": u.get("extraction"),
                   "verified": v.get("verified"), "executed": bool((v.get("execution") or {}).get("action_id")),
                   "mtta_s": _secs(t["created_at"], t["acknowledged_at"]),
                   "mttr_s": _secs(t["created_at"], t["resolved_at"]), "wall_s": round(wall, 2),
                   "llm_steps": len(llm_steps), "llm_fallbacks": sum(s.get("model") == "rule-based" or
                                                                    s.get("fallback") not in (None, "none")
                                                                    for s in llm_steps)}
            out.append(row)
            print(f"  [{mode}] {i:>2}/{len(requests)} {rq['id']} {str(pred_t)[:26]:<26} {str(p.get('decision')):<20}"
                  f" {first:<18} {'ok' if row['final_ok'] else 'MISMATCH'} {wall:.1f}s")
            if pace and mode != "rules":
                time.sleep(pace)
        src.store.db.close()
    return out


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    executed = [r for r in rows if r["executed"]]
    closed = [r for r in rows if r["after_approval"] in ("RESOLVED", "CLOSED_REJECTED")]
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None   # noqa: E731
    return {
        "requests": n,
        "target_accuracy": round(sum(r["target_ok"] for r in rows) / n, 3),
        "decision_exact_match": round(sum(r["decision_ok"] for r in rows) / n, 3),
        "first_final_state_match": round(sum(r["final_ok"] for r in rows) / n, 3),
        "automation_rate": round(len(closed) / n, 3),
        "escalation_rate": round(sum(r["after_approval"] == "ESCALATED" for r in rows) / n, 3),
        "approval_rate": round(sum(r["first_final"] == "AWAITING_APPROVAL" for r in rows) / n, 3),
        "verification_pass_rate": round(sum(bool(r["verified"]) for r in executed) / len(executed), 3)
        if executed else None,
        "mtta_s": mean([r["mtta_s"] for r in rows if r["mtta_s"] is not None]),
        "mttr_s": mean([r["mttr_s"] for r in rows if r["mttr_s"] is not None]),
        "mean_processing_s": mean([r["wall_s"] for r in rows]),
        "llm_fallback_share": round(sum(r["llm_fallbacks"] for r in rows) / max(1, sum(r["llm_steps"] for r in rows)),
                                    3),
        "mismatches": [r["id"] for r in rows if not r["final_ok"]],
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_markdown(summary: dict, path: Path) -> None:
    """Human-readable tables of access_eval_summary.json (for the report)."""
    lines = ["# Objective 2 - access request evaluation", "",
             f"Generated {summary.get('generated', '?')} by `python -m evaluation.eval_access`.", ""]
    d = summary.get("dataset")
    if d:
        lines += ["## Dataset test split (access / service requests)", "",
                  "| Setting | Value |", "|---|---|",
                  f"| Tickets | {d['tickets']} ({', '.join(f'{k} {v}' for k, v in d['labels'].items())}) |",
                  f"| Rule engine, ticket's own policy: decision exact match | {d['oracle_exact_match']:.3f} |",
                  f"| ... excluding ESCALATE_FOR_REVIEW labels | {d['oracle_exact_match_excl_escalate_labels']:.3f} |"]
        if "retrieved_exact_match" in d:
            lines += [f"| Hybrid retrieval: policy Hit@1 | {d['retrieved_policy_hit1']:.3f} |",
                      f"| Rule engine on the retrieved policy: decision exact match | {d['retrieved_exact_match']:.3f} |"]
        lines += ["", f"ESCALATE_FOR_REVIEW labels come from dataset scenarios {d['escalate_label_scenarios']} "
                      "(not derivable from policy + role).", ""]
    c = summary.get("catalogue") or {}
    if c:
        keys = [("requests", "Requests"), ("target_accuracy", "Target accuracy"),
                ("decision_exact_match", "Decision exact match"), ("first_final_state_match", "Final-state match"),
                ("automation_rate", "Automation rate"), ("escalation_rate", "Escalation rate"),
                ("approval_rate", "Approval rate"), ("verification_pass_rate", "Verification pass rate"),
                ("mtta_s", "MTTA (s, batch)"), ("mttr_s", "MTTR (s, batch, incl. approval)"),
                ("mean_processing_s", "Mean processing time (s)"), ("llm_fallback_share", "LLM fallback share"),
                ("mismatches", "Final-state mismatches")]
        modes = list(c)
        lines += ["## Catalogue requests through the multi-agent graph (evaluation/access_requests.csv)", "",
                  "| Metric | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
        for k, label in keys:
            vals = [c[m].get(k) for m in modes]
            lines.append(f"| {label} | " + " | ".join(", ".join(v) if isinstance(v, list) else
                                                       ("-" if v is None else str(v)) for v in vals) + " |")
        lines += ["", "Simulated endpoint (every action succeeds and verifies); approvals given by a valid approver. "
                      "The live endpoint and ServiceNow are tested separately (CLAUDE.md Phase 3).", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="only rewrite access_eval_summary.md from the JSON")
    ap.add_argument("--part", choices=["dataset", "catalogue", "both"], default="both")
    ap.add_argument("--modes", nargs="+", default=["rules", "agentic"])
    ap.add_argument("--no-retrieval", action="store_true")
    ap.add_argument("--pace", type=float, default=4.0, help="seconds between tickets in LLM modes")
    ap.add_argument("--limit", type=int, help="first N catalogue requests only (smoke test, not saved)")
    args = ap.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.report:
        s = json.loads((RESULTS_DIR / "access_eval_summary.json").read_text(encoding="utf-8"))
        write_markdown(s, RESULTS_DIR / "access_eval_summary.md")
        print("wrote evaluation/results/access_eval_summary.md")
        return
    retriever = None
    if not args.no_retrieval:
        from src.knowledge.embeddings import embed_query
        from src.knowledge.retriever import HybridRetriever
        print("Loading knowledge store ...")
        retriever = HybridRetriever.from_database()
        embed_query("warm up")
    summary_path = RESULTS_DIR / "access_eval_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    save = args.limit is None
    if args.part in ("dataset", "both"):
        rows, s = eval_dataset(retriever)
        print("dataset:", json.dumps({k: v for k, v in s.items() if k != "confusion_oracle"}))
        if save:
            write_csv(RESULTS_DIR / "access_dataset_decisions.csv", rows)
            summary["dataset"] = s
    if args.part in ("catalogue", "both"):
        with open(REQUESTS, encoding="utf-8") as f:
            requests = list(csv.DictReader(f))[: args.limit]
        for mode in args.modes:
            rows = run_mode(mode, requests, retriever, args.pace)
            s = summarise(rows)
            s["retrieval"] = retriever is not None
            print(f"{mode}:", json.dumps(s))
            if save:
                write_csv(RESULTS_DIR / f"access_catalogue_{mode.replace(':', '_').replace('/', '_')}.csv", rows)
                summary.setdefault("catalogue", {})[mode] = s
    if save:
        summary["generated"] = datetime.now().isoformat(timespec="seconds")
        summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        write_markdown(summary, RESULTS_DIR / "access_eval_summary.md")
        print(f"saved -> {summary_path.relative_to(ROOT.parent)} (+ .md and CSV tables)")
    else:
        print("smoke test (--limit): nothing saved")


if __name__ == "__main__":
    main()
