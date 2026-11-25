"""Service desk CLI for the multi-agent framework. Tickets live in ServiceNow by default (lean design: incident
table, category 'access', assignment group "AI Service Desk"); every command goes through the TicketSource
interface. Agent state is checkpointed per ticket in data/checkpoints/agents.sqlite (LangGraph).

  python -m src.access.cli new "Please give me access to chatgpt.com, I need help with code"  [--user employee01]
  python -m src.access.cli worker [--once] [--interval 30]   poll for NEW tickets (+ stuck-ticket sweep)
  python -m src.access.cli process INC0010010                (or: process --all)
  python -m src.access.cli approve INC0010012 --by security01 [--reject]
  python -m src.access.cli sweep [--older-than 600]          resume finished installs / closed apps, finish
                                                             stuck tickets, revoke expired time-limited grants
  python -m src.access.cli grants                            active time-limited grants on the endpoint
  python -m src.access.cli show INC0010010                   ticket + work notes
  python -m src.access.cli trace INC0010010                  agent trace from the checkpoint (model, latency)
  python -m src.access.cli list
  python -m src.access.cli demo                               (creates and processes 4 sample tickets)
Options (before the command): --source servicenow|local (local = SQLite desk data/tickets.db, offline tests),
  --rules-only (no LLM at all: every agent uses its rule-based fallback), --llm-single provider:model (one model
  for every agent: ablation). Options of process/approve/worker/sweep/demo: --no-retrieval (skip the Neon
  knowledge store: faster, offline), --no-rewrite (no LLM query rewriting in the Retrieval Agent)."""
import argparse
import logging
import os
import sys
import textwrap

from src.access.ticket_source import SOURCES, get_source
from src.access.worker import approve_ticket, expire_grants, process_ticket, run_poller, tend_active
from src.orchestrator import checkpoint as cp

DEMO = ["I need ChatGPT (chatgpt.com) to help me debug Python code faster.",
        "Please give me access to gemini.google.com for drafting documentation.",
        "Requesting access to github.com to clone our open-source dependencies.",
        "Can you install VS Code on my laptop? I need it for development."]


def load_retriever(skip):
    if skip:
        return None
    import time

    from src.knowledge.embeddings import embed_query
    from src.knowledge.retriever import HybridRetriever
    t0 = time.perf_counter()
    print("Loading knowledge store (Neon + BM25 + embedding model) ...", end=" ", flush=True)
    r = HybridRetriever.from_database()
    embed_query("warm up")            # load the embedding model now, not inside the first ticket (~20 s)
    print(f"{time.perf_counter() - t0:.1f}s")
    return r


def warm_up_llms():
    """Ping every agent model in parallel; models that are down start in cooldown (no ticket waits for them)."""
    from src.common import llm_router
    if os.environ.get("LLM_MODE", "multi") == "off":
        return
    status = llm_router.warmup()
    print("LLM warm-up: " + "; ".join(f"{s} {st}" for s, st in status.items()))


def show(source, number):
    t = source.get(number)
    print(f"\n=== {t['number']}  [{t['state']}]  decision={t['decision']}  target={t['target']}"
          + (f"  approval={t['approval_status']}" if t.get("approval_status") else ""))
    print(f"    requester={t['requester']} ({t['requester_role']}, manager {t['requester_manager'] or '-'})  "
          f"created={t['created_at']}  resolved={t['resolved_at']}")
    if source.link(number):
        print(f"    {source.link(number)}")
    print(f"    request: {t['description']}")
    for n in t["work_notes"]:
        print(textwrap.fill(f"    - {n['agent']}: {n['note']}", 120, subsequent_indent="        "))
    if t["user_message"]:
        print(textwrap.fill(f"    message to user: {t['user_message']}", 120, subsequent_indent="        "))


def show_trace(source, saver, number):
    v = cp.values(saver, source.thread_id(number))
    if not v:
        print(f"{number}: no checkpoint (processed before Phase 2, or by another machine)")
        return
    print(f"=== {number}  flow={v.get('flow')}  final={v.get('final_state')}  route: "
          + " > ".join(r for r in v.get("route", []) if r != "__end__"))
    print(f"{'#':>2} {'agent':<20} {'model':<34} {'node s':>6} {'llm s':>6} {'fallback':<10} output")
    for i, s in enumerate(v.get("trace", []), 1):
        print(f"{i:>2} {s['agent'][:20]:<20} {s.get('model', '')[:34]:<34} {s.get('latency_s', 0):>6.2f} "
              f"{s.get('llm_latency_s', 0) or 0:>6.2f} {s.get('fallback', ''):<10} {s['output'][:90]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=sorted(SOURCES), default="servicenow", help="ticket system (default servicenow)")
    ap.add_argument("--rules-only", action="store_true", help="no LLM calls (rule-based fallbacks only)")
    ap.add_argument("--llm-single", metavar="PROVIDER:MODEL", help="use one model for every agent (ablation)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every LLM call")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="raise an access ticket"); n.add_argument("text")
    n.add_argument("--user", default="employee01")
    p = sub.add_parser("process", help="run the agents on one ticket or all NEW ones")
    p.add_argument("number", nargs="?"); p.add_argument("--all", action="store_true")
    a = sub.add_parser("approve", help="approver decision (checked against the ticket system)")
    a.add_argument("number"); a.add_argument("--by", required=True, help="user name of the approver")
    a.add_argument("--reject", action="store_true")
    w = sub.add_parser("worker", help="poll for NEW tickets and process them")
    w.add_argument("--interval", type=float, default=30); w.add_argument("--once", action="store_true")
    w.add_argument("--stuck-after", type=float, default=600, help="sweep tickets In Progress longer than this (s)")
    sw = sub.add_parser("sweep", help="tend paused/stuck tickets and revoke expired grants")
    sw.add_argument("--older-than", type=float, default=600)
    s = sub.add_parser("show"); s.add_argument("number")
    tr = sub.add_parser("trace"); tr.add_argument("number")
    sub.add_parser("list")
    sub.add_parser("grants")
    sub.add_parser("demo")
    for sp in (p, a, w, sw, sub.choices["demo"]):
        sp.add_argument("--no-retrieval", action="store_true")
        sp.add_argument("--no-rewrite", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(message)s")
    if args.verbose:
        logging.getLogger("llm").setLevel(logging.INFO)
    if args.rules_only:
        os.environ["LLM_MODE"] = "off"
    elif args.llm_single:
        os.environ["LLM_MODE"], os.environ["LLM_SINGLE"] = "single", args.llm_single

    saver = cp.sqlite_saver()
    source = get_source(args.source).attach_checkpoints(saver)

    if args.cmd == "new":
        num = source.create(args.user, args.text)
        print(f"Created {num}  {source.link(num)}")
    elif args.cmd == "show":
        show(source, args.number)
    elif args.cmd == "trace":
        show_trace(source, saver, args.number)
    elif args.cmd == "grants":
        from src.access import endpoint_client
        for g in endpoint_client.grants():
            print(f"{g['ticket_id']}  {g['action']:<18} {g['params']}  expires {g['expires_at']}"
                  + ("  DUE" if g["due"] else ""))
    elif args.cmd == "list":
        for r in source.list():
            print(f"{r['number']}  {r['state']:<18} {str(r['decision']):<20} {str(r['target']):<28} {r['created_at']}")
    else:
        from src.orchestrator.graph import build_graph
        retriever = load_retriever(args.no_retrieval)
        warm_up_llms()
        graph = build_graph(retriever, checkpointer=saver, rewrite_query=not args.no_rewrite)
        if args.cmd == "process":
            if not (args.all or args.number):
                sys.exit("give a ticket number or --all")
            for num in (source.fetch_new(200) if args.all else [args.number]):
                final = process_ticket(source, graph, num)
                if final is None:
                    print(f"{num} is {source.get(num)['state']} - skipped (only NEW tickets are processed)")
                else:
                    show(source, num)
        elif args.cmd == "approve":
            ok, why = approve_ticket(source, graph, args.number, args.by, not args.reject, retriever)
            print(("ACCEPTED: " if ok else "REFUSED: ") + why)
            if ok:
                show(source, args.number)
            sys.exit(0 if ok else 2)
        elif args.cmd == "worker":
            sys.exit(run_poller(source, graph, args.interval, args.once, stuck_after_s=args.stuck_after))
        elif args.cmd == "sweep":
            done = tend_active(source, graph, args.older_than)
            expired = expire_grants(source)
            print(f"sweep: {len(done)} ticket(s) resumed/recovered, {len(expired)} grant(s) expired")
        elif args.cmd == "demo":
            for text in DEMO:
                num = source.create("employee01", text)
                print(f"\nCreated {num}: {text}")
                process_ticket(source, graph, num)
                show(source, num)


if __name__ == "__main__":
    main()
