"""Final ablations (TODO 5.1). Saves evaluation/results/ablations_summary.{json,md} + ablation_*.csv.

    python -m evaluation.ablations --part rewrite                # Retrieval Agent: raw text vs LLM query rewrite
    python -m evaluation.ablations --part access                 # 52 catalogue requests, every graph variant
    python -m evaluation.ablations --part incident               # 8 injected faults, every graph variant
    python -m evaluation.ablations --part latency                # per-provider latency / failures from the log
    python -m evaluation.ablations --report                      # rewrite the .md from the JSON
    options: --variants rules multi ... (subset), --single provider:model, --pace S, --reps N, --limit N

Graph variants (same code, same data; only the LLM configuration and the retriever change):
    rules        LLM_MODE=off: every agent uses its rule-based path; hybrid retrieval on
    rules-noret  rules, no retrieval (no evidence / no k-NN prior)
    multi        per-agent model map from .env (Understanding, Retrieval, RCA, Critic, Communication) + retrieval
    multi-noret  per-agent models, no retrieval
    single       ONE model for every agent (--single, default groq:openai/gpt-oss-20b) + retrieval
Each run also records how many LLM calls were ANSWERED by a model vs. fell back to rules (read from
logs/llm_calls.jsonl between the start and the end of the run): free-tier rate limits make some calls fall back,
and a comparison is only meaningful together with that share.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime

from src.common.config import RESULTS_DIR
from src.common.llm_stats import llm_usage, log_lines, log_records, provider_latency

SUMMARY = RESULTS_DIR / "ablations_summary.json"
VARIANTS = ("rules", "rules-noret", "multi", "multi-noret", "single")
DEFAULT_SINGLE = "groq:openai/gpt-oss-20b"


def variant_mode(variant: str, single: str) -> tuple[str, bool]:
    """(mode for the evaluation runners, use retrieval)."""
    base, noret = variant.removesuffix("-noret"), variant.endswith("-noret")
    mode = {"rules": "rules", "multi": "agentic", "single": f"single:{single}"}[base]
    return mode, not noret


# ===================================================================== parts
def _retriever():
    from src.knowledge.embeddings import embed_query
    from src.knowledge.retriever import HybridRetriever
    print("Loading knowledge store ...")
    r = HybridRetriever.from_database()
    embed_query("warm up")
    return r


def _requests(limit=None) -> list[dict]:
    from evaluation.eval_access import REQUESTS
    with open(REQUESTS, encoding="utf-8") as f:
        return list(csv.DictReader(f))[:limit]


def part_access(variants, single, pace, limit, retriever) -> dict:
    from evaluation import eval_access as EA
    out = {}
    for v in variants:
        mode, use_ret = variant_mode(v, single)
        print(f"== access / {v} ({mode}, retrieval {'on' if use_ret else 'off'})")
        start = log_lines()
        t0 = time.time()
        rows = EA.run_mode(mode, _requests(limit), retriever if use_ret else None, pace)
        recs = log_records(start)
        s = EA.summarise(rows)
        s.update(variant=v, mode=mode, retrieval=use_ret, wall_min=round((time.time() - t0) / 60, 1),
                 llm=llm_usage(recs), latency=provider_latency(recs))
        print(f"{v}: decision {s['decision_exact_match']} final {s['first_final_state_match']} automation "
              f"{s['automation_rate']} escalation {s['escalation_rate']} mean {s['mean_processing_s']} s "
              f"LLM answered {s['llm']['_answered_share']}")
        out[v] = s
        if limit is None:
            EA.write_csv(RESULTS_DIR / f"ablation_access_{v}.csv", rows)
    return out


def part_incident(variants, single, pace, reps, retriever) -> dict:
    from evaluation import eval_incident as EI
    out = {}
    for v in variants:
        mode, use_ret = variant_mode(v, single)
        print(f"== incident / {v} ({mode}, retrieval {'on' if use_ret else 'off'})")
        start = log_lines()
        t0 = time.time()
        rows, summ = EI.eval_lab([mode], reps, retriever if use_ret else None, pace)
        recs = log_records(start)
        s = dict(summ[mode])
        s.update(variant=v, mode=mode, retrieval=use_ret, wall_min=round((time.time() - t0) / 60, 1),
                 llm=llm_usage(recs), latency=provider_latency(recs))
        print(f"{v}: top1 {s['rca_top1']} top3 {s['rca_top3']} resolved {s['resolution_rate']} MTTR "
              f"{s['mttr_s_mean']} s LLM answered {s['llm']['_answered_share']}")
        out[v] = s
        for r in rows:
            r["variant"] = v
        EI.write_csv(RESULTS_DIR / f"ablation_incident_{v}.csv", rows)
    return out


def part_rewrite(pace, limit, retriever) -> dict:
    """Retrieval Agent ablation on the labelled catalogue requests that name a catalogue target: hybrid retrieval
    (category filter, as in the graph) with the raw request text vs. the Retrieval Agent's LLM rewrite. Gold =
    the target's policy and that policy's SOP. Understanding is rule-based here so only the query differs."""
    from src.access.agents import POLICIES, TARGETS, extract_request, rewrite_query
    from src.common import llm_router
    from src.knowledge.eval_retrieval import first_hit_rank

    os.environ.pop("LLM_SINGLE", None)
    os.environ["LLM_MODE"] = "multi"
    llm_router.reset_cooldowns()
    rows = []
    start = log_lines()
    reqs = [r for r in _requests(limit) if r["expected_target"] in TARGETS]
    for i, rq in enumerate(reqs, 1):
        info = TARGETS[rq["expected_target"]]
        pol = POLICIES[info["policy_id"]]
        ex = extract_request(rq["text"], use_llm=False)
        u = {"request_kind": ex["request_kind"], "label": info["label"], "target": rq["expected_target"]}
        res = rewrite_query(rq["text"], u)
        row = {"id": rq["id"], "text": rq["text"], "rewrite": res.data.query, "rewritten": res.ok,
               "model": f"{res.provider}:{res.model}" if res.ok else res.method, "gold_policy": info["policy_id"],
               "gold_sop": pol["sop_id"]}
        for name, q in (("raw", rq["text"]), ("llm", res.data.query)):
            r = retriever.retrieve_all(q, k=10, filters={"category": pol["category"]})
            rs = first_hit_rank(r["sops"]["items"], pol["sop_id"])
            rp = first_hit_rank(r["policies"]["items"], info["policy_id"])
            row.update({f"{name}_sop_rank": rs, f"{name}_pol_rank": rp})
        rows.append(row)
        print(f"  {i:>2}/{len(reqs)} {rq['id']} sop rank raw {row['raw_sop_rank']} llm {row['llm_sop_rank']} | "
              f"policy raw {row['raw_pol_rank']} llm {row['llm_pol_rank']} ({row['model']})")
        if pace:
            time.sleep(pace)

    def agg(rows_, name):
        n = len(rows_)
        m = lambda key, k: round(sum(bool(r[key] and r[key] <= k) for r in rows_) / n, 3)  # noqa: E731
        mrr = lambda key: round(sum(1 / r[key] if r[key] else 0 for r in rows_) / n, 3)  # noqa: E731
        return {"sop_hit1": m(f"{name}_sop_rank", 1), "sop_hit3": m(f"{name}_sop_rank", 3),
                "sop_mrr10": mrr(f"{name}_sop_rank"), "policy_hit1": m(f"{name}_pol_rank", 1),
                "policy_hit3": m(f"{name}_pol_rank", 3), "policy_mrr10": mrr(f"{name}_pol_rank")}

    answered = [r for r in rows if r["rewritten"]]
    out = {"requests": len(rows), "rewritten_by_llm": len(answered), "raw": agg(rows, "raw"), "llm": agg(rows, "llm"),
           "raw_on_rewritten_subset": agg(answered, "raw") if answered else None,
           "llm_on_rewritten_subset": agg(answered, "llm") if answered else None,
           "llm_usage": llm_usage(log_records(start))}
    print("rewrite:", json.dumps({k: v for k, v in out.items() if k != "llm_usage"}))
    if limit is None:
        with open(RESULTS_DIR / "ablation_rewrite.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return out


def part_latency() -> dict:
    recs = log_records()
    return {"calls_logged": len(recs), "first": recs[0]["ts"] if recs else None, "last": recs[-1]["ts"] if recs else None,
            "per_model": provider_latency(recs), "usage": llm_usage(recs)}


# ===================================================================== report
def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, list):
        return ", ".join(map(str, v)) or "none"
    return str(v)


def _variant_table(sec: dict, keys: list[tuple[str, str]]) -> list[str]:
    vs = list(sec)
    lines = ["| Metric | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
    for k, label in keys:
        lines.append(f"| {label} | " + " | ".join(_fmt(sec[v].get(k)) for v in vs) + " |")
    lines.append("| LLM calls answered by a model | " + " | ".join(
        _fmt(sec[v]["llm"].get("_answered_share")) + f" ({sec[v]['llm']['_all'].get('calls', 0)} calls)"
        if sec[v]["llm"].get("_all") else "- (no LLM)" for v in vs) + " |")
    lines.append("| Wall time (min) | " + " | ".join(_fmt(sec[v].get("wall_min")) for v in vs) + " |")
    return lines


def write_markdown(s: dict) -> None:
    L = ["# Final ablations (TODO 5.1)", "", f"Generated {s.get('generated', '?')} by `python -m evaluation.ablations`.",
         "", "Variants: **rules** = LLM off (rule-based path of every agent); **multi** = per-agent models from .env; "
         f"**single** = one model for every agent ({s.get('single', DEFAULT_SINGLE)}); **-noret** = no hybrid "
         "retrieval. The policy decision is rule-based in every variant (design rule).", ""]
    if s.get("access"):
        L += ["## A. Access requests (52 labelled catalogue requests, full graph, simulated endpoint)", ""]
        L += _variant_table(s["access"], [
            ("target_accuracy", "Target accuracy"), ("decision_exact_match", "Decision exact match"),
            ("first_final_state_match", "Final-state match"), ("automation_rate", "Automation rate"),
            ("escalation_rate", "Escalation rate"), ("approval_rate", "Approval rate"),
            ("verification_pass_rate", "Verification pass rate"), ("mttr_s", "MTTR (s, batch)"),
            ("mean_processing_s", "Mean processing time (s)"), ("mismatches", "Final-state mismatches")])
        L.append("")
    if s.get("incident"):
        L += ["## B. Incident resolution (8 injected faults in the simulated services)", ""]
        L += _variant_table(s["incident"], [
            ("detected", "Incidents"), ("root_service_accuracy", "Root-service accuracy"), ("rca_top1", "RCA top-1"),
            ("rca_top3", "RCA top-3"), ("action_accuracy", "Remediation action correct"),
            ("resolution_rate", "Resolution rate"), ("escalation_rate", "Escalation rate"),
            ("mttr_s_mean", "MTTR mean (s)"), ("mttr_s_median", "MTTR median (s)"), ("rollbacks", "Rollbacks"),
            ("misses", "Not resolved")])
        L.append("")
    rw = s.get("rewrite")
    if rw:
        L += ["## C. Retrieval Agent: raw request text vs. LLM query rewrite", "",
              f"{rw['requests']} catalogue requests with a known target; gold = the target's policy and its SOP; "
              f"hybrid retrieval with category filter. The LLM rewrote {rw['rewritten_by_llm']} of them "
              "(the rest fell back to the raw text).", "",
              "| Query | SOP Hit@1 | SOP Hit@3 | SOP MRR@10 | Policy Hit@1 | Policy Hit@3 | Policy MRR@10 |",
              "|---|---|---|---|---|---|---|"]
        for k, label in (("raw", "raw text"), ("llm", "LLM rewrite"), ("raw_on_rewritten_subset", "raw (rewritten subset)"),
                         ("llm_on_rewritten_subset", "LLM (rewritten subset)")):
            a = rw.get(k)
            if a:
                L.append(f"| {label} | {a['sop_hit1']} | {a['sop_hit3']} | {a['sop_mrr10']} | {a['policy_hit1']} | "
                         f"{a['policy_hit3']} | {a['policy_mrr10']} |")
        L.append("")
    lat = s.get("latency")
    if lat:
        L += ["## D. Per-provider latency and reliability (every LLM attempt in logs/llm_calls.jsonl)", "",
              f"{lat['calls_logged']} logged agent calls from {lat['first']} to {lat['last']} (development, live runs "
              "and evaluations on free tiers).", "",
              "| Provider:model | Attempts | Called | Answered | Success of called | Median s | p90 s | Mean s | "
              "Failures |", "|---|---|---|---|---|---|---|---|---|"]
        probes = [spec for spec, m in lat["per_model"].items()
                  if not m["answered"] and set(m["errors"]) <= {"http_404", "skipped_cooldown"}]
        for spec, m in lat["per_model"].items():
            if spec in probes:
                continue
            L.append(f"| {spec} | {m['attempts']} | {m['called']} | {m['answered']} | {_fmt(m['success_rate_of_called'])} "
                     f"| {_fmt(m['latency_median_s'])} | {_fmt(m['latency_p90_s'])} | {_fmt(m['latency_mean_s'])} | "
                     + (", ".join(f"{k} {v}" for k, v in sorted(m["errors"].items())) or "-") + " |")
        u = lat["usage"]
        if probes:
            L += ["", f"Not shown: {', '.join(probes)} (deliberately invalid model id used to test the 404 fallback)."]
        L += ["", "Per agent, calls with an LLM configured (final outcome of each call): " + "; ".join(
            f"{a} {c.get('llm_primary', 0)} primary / {c.get('llm_secondary', 0)} secondary / "
            f"{c.get('fallback_rules', 0)} rule-based" for a, c in u.items() if not a.startswith("_")) + ".", ""]
    (RESULTS_DIR / "ablations_summary.md").write_text("\n".join(L), encoding="utf-8")


# ===================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=["access", "incident", "rewrite", "latency"], nargs="+", default=["latency"])
    ap.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    ap.add_argument("--single", default=DEFAULT_SINGLE, help="provider:model for the single-LLM variant")
    ap.add_argument("--pace", type=float, default=6.0, help="seconds between tickets / faults in LLM variants")
    ap.add_argument("--reps", type=int, default=1, help="incident part: repetitions of the 8 faults")
    ap.add_argument("--limit", type=int, help="access/rewrite: first N requests only (smoke test, not saved)")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    s = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
    if args.report:
        write_markdown(s)
        print("wrote evaluation/results/ablations_summary.md")
        return
    need_ret = any(p in args.part for p in ("access", "incident", "rewrite"))
    retriever = _retriever() if need_ret else None
    save = args.limit is None
    for part in args.part:
        if part == "access":
            r = part_access(args.variants, args.single, args.pace, args.limit, retriever)
            if save:
                s.setdefault("access", {}).update(r)
        elif part == "incident":
            s.setdefault("incident", {}).update(part_incident(args.variants, args.single, args.pace, args.reps,
                                                              retriever))
        elif part == "rewrite":
            r = part_rewrite(args.pace, args.limit, retriever)
            if save:
                s["rewrite"] = r
        else:
            s["latency"] = part_latency()
            print(json.dumps({k: (v["latency_median_s"], v["answered"], v["called"])
                              for k, v in s["latency"]["per_model"].items()}))
    if save:
        s["single"] = args.single
        s["generated"] = datetime.now().isoformat(timespec="seconds")
        SUMMARY.write_text(json.dumps(s, indent=1), encoding="utf-8")
        write_markdown(s)
        print("saved -> evaluation/results/ablations_summary.{json,md}")
    else:
        print("smoke test (--limit): nothing saved")


if __name__ == "__main__":
    main()
