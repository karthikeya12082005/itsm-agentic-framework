"""Evaluate retrieval on the held-out test tickets: BM25 vs dense vs hybrid (RRF).

Ground truth comes from the dataset links: each ticket's sop_id and policy_id are the relevant
SOP/policy, and past incidents with the same subcategory are relevant cases.
    python -m src.knowledge.eval_retrieval              # no metadata filter (hardest setting)
    python -m src.knowledge.eval_retrieval --category   # filter by the ticket's category
"""
import argparse
import json
import time

from src.common.config import PROCESSED_DIR, RESULTS_DIR
from src.knowledge.embeddings import embed_query, to_pgvector
from src.knowledge.retriever import HybridRetriever

MODES = ["bm25", "dense", "hybrid"]


def first_hit_rank(items, gold):
    for rank, it in enumerate(items, start=1):
        if it["id"] == gold:
            return rank
    return None


def evaluate(retriever, tests, use_category=False, embed=lambda q: to_pgvector(embed_query(q))):
    agg = {m: {"sop_hit1": 0, "sop_hit3": 0, "sop_mrr": 0.0, "pol_hit1": 0, "pol_hit3": 0, "pol_mrr": 0.0,
               "inc_p5": 0.0} for m in MODES}
    t0 = time.time()
    for n, t in enumerate(tests, start=1):
        rec, q = t["record"], t["query_text"]
        filters = {"category": rec["category"]} if use_category else None
        qv = embed(q)
        lists = {s: retriever.candidates(q, s, qv, filters) for s in ("sops", "policies", "incidents")}
        for m in MODES:
            a = agg[m]
            sop = retriever.fuse(*lists["sops"], mode=m, k=10)
            pol = retriever.fuse(*lists["policies"], mode=m, k=10)
            inc = retriever.fuse(*lists["incidents"], mode=m, k=5)
            r = first_hit_rank(sop, rec["sop_id"])
            a["sop_hit1"] += r == 1; a["sop_hit3"] += bool(r and r <= 3); a["sop_mrr"] += 1 / r if r else 0
            r = first_hit_rank(pol, rec["policy_id"])
            a["pol_hit1"] += r == 1; a["pol_hit3"] += bool(r and r <= 3); a["pol_mrr"] += 1 / r if r else 0
            same = sum(retriever.meta["incidents"][it["id"]]["subcategory"] == rec["subcategory"] for it in inc)
            a["inc_p5"] += same / 5
        if n % 50 == 0:
            print(f"  {n}/{len(tests)} queries ({time.time() - t0:.0f}s)")
    n = len(tests)
    return {m: {k: round(v / n, 4) for k, v in agg[m].items()} for m in MODES}, n


def report(results, n, use_category):
    title = "with category filter" if use_category else "no metadata filter"
    lines = [f"## Retrieval evaluation ({n} held-out test tickets, {title})", "",
             "| Method | SOP Hit@1 | SOP Hit@3 | SOP MRR@10 | Policy Hit@1 | Policy Hit@3 | Policy MRR@10 | Incident P@5 |",
             "|---|---|---|---|---|---|---|---|"]
    names = {"bm25": "BM25 only", "dense": "Dense only (bge-small)", "hybrid": "Hybrid (BM25 + dense, RRF k=60)"}
    for m, r in results.items():
        lines.append(f"| {names[m]} | {r['sop_hit1']:.3f} | {r['sop_hit3']:.3f} | {r['sop_mrr']:.3f} | "
                     f"{r['pol_hit1']:.3f} | {r['pol_hit3']:.3f} | {r['pol_mrr']:.3f} | {r['inc_p5']:.3f} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", action="store_true", help="filter candidates by the ticket's category")
    ap.add_argument("--limit", type=int, default=0, help="evaluate only the first N test tickets")
    args = ap.parse_args()
    with open(PROCESSED_DIR / "incidents.jsonl", encoding="utf-8") as f:
        tests = [r for r in map(json.loads, f) if r["split"] == "test"]
    if args.limit:
        tests = tests[:args.limit]
    print(f"Loading retriever ... ({len(tests)} test tickets)")
    retriever = HybridRetriever.from_database()
    results, n = evaluate(retriever, tests, args.category)
    md = report(results, n, args.category)
    print("\n" + md)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"retrieval_eval{'_category' if args.category else ''}.md"
    out.write_text(md + "\n", encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
