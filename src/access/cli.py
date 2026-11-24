"""Access-request desk (Objective 2). Tickets live in ServiceNow by default (lean design: incident table,
category 'access', assignment group "AI Service Desk"); every command goes through the TicketSource interface.

  python -m src.access.cli new "Please give me access to chatgpt.com, I need help with code"  [--user employee01]
  python -m src.access.cli worker [--once] [--interval 30]   poll for NEW access tickets and run the agents
  python -m src.access.cli process INC0010010                (or: process --all)
  python -m src.access.cli approve INC0010012 --by security01 [--reject]
  python -m src.access.cli show INC0010010
  python -m src.access.cli list
  python -m src.access.cli demo                               (creates and processes 4 sample tickets)
Options: --source servicenow|local (before the command; local = SQLite desk data/tickets.db, offline tests),
         --no-retrieval (skip loading the Neon knowledge store: faster, offline)."""
import argparse
import sys
import textwrap

from src.access.ticket_source import SOURCES, get_source
from src.access.worker import approve_ticket, process_ticket, run_poller

DEMO = ["I need ChatGPT (chatgpt.com) to help me debug Python code faster.",
        "Please give me access to gemini.google.com for drafting documentation.",
        "Requesting access to github.com to clone our open-source dependencies.",
        "Can you install VS Code on my laptop? I need it for development."]


def load_retriever(skip):
    if skip:
        return None
    from src.knowledge.retriever import HybridRetriever
    print("Loading knowledge store (Neon + BM25) ...")
    return HybridRetriever.from_database()


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
        print(textwrap.fill(f"    - {n['agent']}: {n['note']}", 110, subsequent_indent="        "))
    if t["user_message"]:
        print(textwrap.fill(f"    message to user: {t['user_message']}", 110, subsequent_indent="        "))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=sorted(SOURCES), default="servicenow", help="ticket system (default servicenow)")
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
    s = sub.add_parser("show"); s.add_argument("number")
    sub.add_parser("list")
    sub.add_parser("demo")
    for sp in (p, a, w, sub.choices["demo"]):
        sp.add_argument("--no-retrieval", action="store_true")
    args = ap.parse_args()
    source = get_source(args.source)

    if args.cmd == "new":
        num = source.create(args.user, args.text)
        print(f"Created {num}  {source.link(num)}")
    elif args.cmd == "show":
        show(source, args.number)
    elif args.cmd == "list":
        for r in source.list():
            print(f"{r['number']}  {r['state']:<18} {str(r['decision']):<20} {str(r['target']):<28} {r['created_at']}")
    else:
        from src.access.graph import build_graph
        retriever = load_retriever(args.no_retrieval)
        graph = build_graph(retriever)
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
            sys.exit(run_poller(source, graph, args.interval, args.once))
        elif args.cmd == "demo":
            for text in DEMO:
                num = source.create("employee01", text)
                print(f"\nCreated {num}: {text}")
                process_ticket(source, graph, num)
                show(source, num)


if __name__ == "__main__":
    main()
