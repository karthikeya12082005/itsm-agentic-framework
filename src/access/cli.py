"""Local ticket desk for Objective 2 (stands in for ServiceNow until the connector is added).

  python -m src.access.cli new "Please give me access to chatgpt.com, I need help with code"
  python -m src.access.cli process RITM9000001        (or: process --all)
  python -m src.access.cli approve RITM9000003        (or: approve RITM9000003 --reject)
  python -m src.access.cli show RITM9000001
  python -m src.access.cli list
  python -m src.access.cli demo                        (creates and processes 4 sample tickets)
Add --no-retrieval to skip loading the Neon knowledge store (faster, offline)."""
import argparse
import textwrap

from src.access.store import TicketStore, now

CLOSED = {"RESOLVED", "CLOSED_REJECTED"}


def load_retriever(skip):
    if skip:
        return None
    from src.knowledge.retriever import HybridRetriever
    print("Loading knowledge store (Neon + BM25) ...")
    return HybridRetriever.from_database()


def persist(store, number, result):
    for agent, note in result.get("notes", []):
        store.add_note(number, agent, note)
    u, p = result.get("understanding", {}), result.get("policy", {})
    fields = {"state": result["final_state"], "decision": p.get("decision"), "target": u.get("target"),
              "policy_id": u.get("policy_id"), "sop_id": u.get("sop_id"),
              "approval_status": result.get("approval_status"), "user_message": result.get("user_message"),
              "context": {k: result.get(k) for k in ("understanding", "evidence", "policy", "execution",
                                                       "attempts", "verified")}}
    if result["final_state"] in CLOSED:
        fields["resolved_at"] = now()
    store.update(number, **{k: v for k, v in fields.items() if v is not None})


def process(store, graph, number):
    t = store.get(number)
    if t["state"] != "NEW":
        print(f"{number} is {t['state']} - skipped")
        return
    store.update(number, state="IN_PROGRESS", acknowledged_at=now())
    result = graph.invoke({"ticket": t, "notes": []})
    persist(store, number, result)
    show(store, number)


def resume(store, graph, number, approved, approver):
    t = store.get(number)
    if t["state"] != "AWAITING_APPROVAL":
        print(f"{number} is {t['state']}, not awaiting approval")
        return
    status = "APPROVED" if approved else "REJECTED"
    store.add_note(number, "Approver", f"{approver} {status.lower()} the request")
    ctx = t["context"]
    state = {"ticket": t, "resume": True, "approval_status": status, "notes": [],
             "understanding": ctx["understanding"], "evidence": ctx.get("evidence") or {},
             "policy": ctx["policy"], "attempts": 0}
    result = graph.invoke(state)
    result.setdefault("approval_status", status)
    persist(store, number, result)
    show(store, number)


def show(store, number):
    t = store.get(number)
    print(f"\n=== {t['number']}  [{t['state']}]  decision={t['decision']}  target={t['target']}")
    print(f"    requester={t['requester']}  created={t['created_at']}  resolved={t['resolved_at']}")
    print(f"    request: {t['description']}")
    for n in t["work_notes"]:
        print(textwrap.fill(f"    - {n['agent']}: {n['note']}", 110, subsequent_indent="        "))
    if t["user_message"]:
        print(f"    message to user: {t['user_message']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new"); n.add_argument("text"); n.add_argument("--user", default="employee01")
    p = sub.add_parser("process"); p.add_argument("number", nargs="?"); p.add_argument("--all", action="store_true")
    a = sub.add_parser("approve"); a.add_argument("number"); a.add_argument("--reject", action="store_true")
    a.add_argument("--by", default="manager01")
    s = sub.add_parser("show"); s.add_argument("number")
    sub.add_parser("list")
    sub.add_parser("demo")
    for sp in (p, a, sub.choices["demo"]):
        sp.add_argument("--no-retrieval", action="store_true")
    args = ap.parse_args()
    store = TicketStore()

    if args.cmd == "new":
        print("Created", store.create(args.user, args.text))
    elif args.cmd == "show":
        show(store, args.number)
    elif args.cmd == "list":
        for r in store.list():
            print(f"{r['number']}  {r['state']:<18} {str(r['decision']):<20} {str(r['target']):<28} {r['created_at']}")
    else:
        from src.access.graph import build_graph
        graph = build_graph(load_retriever(args.no_retrieval))
        if args.cmd == "process":
            numbers = [r["number"] for r in store.list(200) if r["state"] == "NEW"] if args.all else [args.number]
            for num in numbers:
                process(store, graph, num)
        elif args.cmd == "approve":
            resume(store, graph, args.number, not args.reject, args.by)
        elif args.cmd == "demo":
            for text in ["I need ChatGPT (chatgpt.com) to help me debug Python code faster.",
                         "Please give me access to gemini.google.com for drafting documentation.",
                         "Requesting access to github.com to clone our open-source dependencies.",
                         "Can you install VS Code on my laptop? I need it for development."]:
                num = store.create("employee01", text)
                print(f"\nCreated {num}: {text}")
                process(store, graph, num)


if __name__ == "__main__":
    main()
