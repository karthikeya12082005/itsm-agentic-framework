"""TicketSource - the ONLY way the access worker / CLI read and write tickets (item 1.3).

Agents never call ServiceNow (or SQLite) directly: the worker gives them a ticket dict and stores their result
through this interface. Two implementations:

  ServiceNowSource (default)  system of record = incident table of the PDI (lean design, see CLAUDE.md):
                              category 'access', assignment group "AI Service Desk", caller = employee record.
  LocalSource                 the old SQLite desk (data/tickets.db) - for tests and offline demos.

Neutral ticket statuses used by agents/worker/CLI and how ServiceNowSource stores them on the incident:
  NEW                 state 1 New
  IN_PROGRESS         state 2 In Progress, assigned_to = ai.agent (acknowledged -> MTTA)
  AWAITING_APPROVAL   state 3 On Hold + approval = requested (+ work note naming who may approve)
  RESOLVED            state 6 Resolved, close_code "Resolved – Permanent Fix Applied", close_notes = message
  CLOSED_REJECTED     state 8 Canceled, close_notes = reason (policy DENY or approver rejected)
  ESCALATED           state 2 In Progress, reassigned to the human group "Service Desk"
  WAITING_FOR_USER    state 3 On Hold + hold_reason 1 "Awaiting Caller" (e.g. close VS Code, item 3.1)

The agents' structured context (decision, policy, evidence, critic, trace) lives in the LangGraph checkpoint
of the ticket's thread (src/orchestrator/checkpoint.py, item 2.8; thread_id = thread_id(number)). Sources read
decision/target for list/show from it via attach_checkpoints(). Everything human-relevant is in ServiceNow.
(Phase 1 kept this in data/agent_state.db; that file is no longer used.)

Live self-test against the PDI (creates 3 "[selftest]" incidents and closes them):
    python -m src.access.ticket_source selftest
"""
from __future__ import annotations

import re
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.access.catalog import APPROVER_GROUPS, EMPLOYEES, LOCAL_GROUP_MEMBERS
from src.access.store import TicketStore, now
from src.orchestrator import checkpoint as cp
from src.orchestrator.trace import as_note, format_step

STATUSES = ("NEW", "IN_PROGRESS", "AWAITING_APPROVAL", "RESOLVED", "CLOSED_REJECTED", "ESCALATED",
            "WAITING_FOR_USER")
ACTIVE = ("IN_PROGRESS", "WAITING_FOR_USER")       # tickets the worker tends (jobs, user actions, stuck runs)
CLOSED = {"RESOLVED", "CLOSED_REJECTED"}
CONTEXT_KEYS = ("understanding", "evidence", "policy", "critic", "execution", "attempts", "verified")


# ============================================================================================== interface
class TicketSource(ABC):
    """Ticket dict returned by get(): number, requester, requester_role, requester_manager, description,
    state (neutral), approval_status (PENDING/APPROVED/REJECTED/None), decision, target, context,
    work_notes [{time, agent, note}], user_message, created_at, acknowledged_at, resolved_at."""
    name = "abstract"

    @abstractmethod
    def create(self, requester: str, text: str) -> str:
        """Raise a new access ticket for `requester` (user name) with free text. Returns the ticket number."""

    @abstractmethod
    def create_incident(self, short: str, description: str, category: str, impact: int = 2,
                        urgency: int = 2) -> str:
        """Raise an incident from monitoring (Phase 4): any category except 'access', assigned to the agents."""

    @abstractmethod
    def get(self, number: str) -> dict:
        """The ticket as a dict (see class doc). KeyError if it does not exist."""

    @abstractmethod
    def list(self, limit: int = 20) -> list[dict]:
        """Recent tickets, newest first: number, requester, state, decision, target, created_at, resolved_at."""

    @abstractmethod
    def fetch_new(self, limit: int = 20) -> list[str]:
        """Numbers of NEW tickets waiting for the agents, oldest first (the poller's work queue)."""

    @abstractmethod
    def acknowledge(self, number: str) -> None:
        """NEW -> IN_PROGRESS; the agents have picked the ticket up."""

    @abstractmethod
    def add_note(self, number: str, agent: str, text: str) -> None:
        """Internal work note from one agent (trace)."""

    @abstractmethod
    def add_comment(self, number: str, text: str) -> None:
        """Customer-visible message to the requester."""

    @abstractmethod
    def set_status(self, number: str, status: str, reason: str = "") -> None:
        """Move the ticket to a neutral status (see module doc)."""

    @abstractmethod
    def record_approval(self, number: str, approved: bool, approver: str, role: str) -> None:
        """Store an approver's decision (only called after may_approve() said yes)."""

    @abstractmethod
    def save_context(self, number: str, data: dict) -> None:
        """Store the agents' structured result (decision, target, policy, evidence...) for resume/metrics."""

    @abstractmethod
    def approvers(self, role: str | None, ticket: dict) -> set[str]:
        """User names allowed to approve for a policy approver_role on this ticket."""

    @abstractmethod
    def fetch_active(self) -> list[dict]:
        """Agent tickets IN_PROGRESS or WAITING_FOR_USER: [{number, state, age_s}] (age = since last update)."""

    def fetch_stuck(self, older_than_s: float = 600) -> list[str]:
        """IN_PROGRESS tickets of the agents not touched for older_than_s seconds (crashed / unsaved runs)."""
        return [t["number"] for t in self.fetch_active() if t["state"] == "IN_PROGRESS" and t["age_s"] >= older_than_s]

    def link(self, number: str) -> str:
        return ""

    def thread_id(self, number: str) -> str:
        """LangGraph thread of this ticket (checkpointer key)."""
        return f"{self.name}|{number}"

    _saver = None

    def attach_checkpoints(self, saver) -> "TicketSource":
        """Read decision/target/context of tickets from the LangGraph checkpointer."""
        self._saver = saver
        return self

    def _summary(self, number: str) -> dict:
        return cp.summary(self._saver, self.thread_id(number)) if self._saver is not None else {}

    def add_trace(self, number: str, steps: list) -> None:
        """All agent steps of one graph run, in execution order (default: one note per step)."""
        for s in steps:
            self.add_note(number, *as_note(s))

    # ------------------------------------------------------------------ shared logic
    def may_approve(self, user: str, role: str | None, ticket: dict) -> tuple[bool, str]:
        """Approver check done in code (1.5): (allowed?, reason)."""
        if not role:
            return False, "the policy decision names no approver role"
        if user == ticket.get("requester"):
            return False, f"{user} is the requester (no self-approval)"
        allowed = self.approvers(role, ticket)
        if user in allowed:
            return True, f"{user} is {role}"
        return False, f"{user} is not {role} (allowed: {', '.join(sorted(allowed)) or 'nobody'})"

    def save_result(self, number: str, ticket: dict, result: dict, steps: list | None = None) -> None:
        """Write one graph run back: its agent steps (trace), the structured context, the message, the status.
        `steps` = the trace entries of THIS run (default: the whole trace in result)."""
        steps = list(result.get("trace", []) if steps is None else steps)
        final = result["final_state"]
        u, p = result.get("understanding") or {}, result.get("policy") or {}
        if final == "AWAITING_APPROVAL":
            role = p.get("approver_role")
            who = ", ".join(sorted(self.approvers(role, ticket))) or "nobody is configured for this role"
            steps.append({"agent": "Policy & Risk Agent", "model": "deterministic", "output":
                          f"waiting for approval by {role}: {who}. "
                          f"Approve: python -m src.access.cli approve {number} --by <user> [--reject]"})
        self.add_trace(number, steps)
        self.save_context(number, {
            "final_state": final, "decision": p.get("decision"), "target": u.get("target"),
            "policy_id": u.get("policy_id"), "sop_id": u.get("sop_id"),
            "approval_status": result.get("approval_status"), "user_message": result.get("user_message"),
            "context": {k: result.get(k) for k in CONTEXT_KEYS}})
        if result.get("user_message"):
            self.add_comment(number, result["user_message"])
        self.set_status(number, final, reason=result.get("user_message") or final)


# ============================================================================================== local (SQLite)
class LocalSource(TicketSource):
    """The SQLite desk (data/tickets.db, RITM9000001+). Employee roles/managers from catalog.EMPLOYEES."""
    name = "local"

    def __init__(self, path: Path | None = None):
        from src.access.store import DB_PATH
        self.path = Path(path) if path else DB_PATH
        self.store = TicketStore(self.path)

    def thread_id(self, number):
        return f"local|{self.path.resolve()}|{number}"

    def create(self, requester, text):
        return self.store.create(requester, text)

    def create_incident(self, short, description, category, impact=2, urgency=2):
        return self.store.create("monitoring", f"{short}\n{description}", category=category)

    def get(self, number):
        t = self.store.get(number)
        who = EMPLOYEES.get(t["requester"], {})
        t["requester_role"] = who.get("role", "EMPLOYEE")
        t["requester_manager"] = who.get("manager", "")
        t["category"] = t.get("category") or "access"
        return t

    def fetch_active(self):
        now_ = datetime.now(timezone.utc)
        out = []
        for r in reversed(self.store.list(100000)):
            if r["state"] in ACTIVE:
                ack = self.store.get(r["number"]).get("acknowledged_at")
                age = (now_ - datetime.fromisoformat(ack)).total_seconds() if ack else 10 ** 9
                out.append({"number": r["number"], "state": r["state"], "age_s": age})
        return out

    def list(self, limit=20):
        return self.store.list(limit)

    def fetch_new(self, limit=20):
        return [r["number"] for r in reversed(self.store.list(100000)) if r["state"] == "NEW"][:limit]

    def acknowledge(self, number):
        self.store.update(number, state="IN_PROGRESS", acknowledged_at=now())

    def add_note(self, number, agent, text):
        self.store.add_note(number, agent, text)

    def add_comment(self, number, text):
        self.store.update(number, user_message=text)

    def set_status(self, number, status, reason=""):
        if status not in STATUSES:
            raise ValueError(f"unknown status {status}")
        fields = {"state": status}
        if status in CLOSED:
            fields["resolved_at"] = now()
        if status == "AWAITING_APPROVAL":
            fields["approval_status"] = "PENDING"
        self.store.update(number, **fields)

    def record_approval(self, number, approved, approver, role):
        status = "APPROVED" if approved else "REJECTED"
        self.store.update(number, approval_status=status)
        self.store.add_note(number, "Approval", f"{approver} ({role}) {status.lower()} the request via the agent CLI")

    def save_context(self, number, data):
        cols = {k: data[k] for k in ("decision", "target", "policy_id", "sop_id", "approval_status", "context")
                if data.get(k) is not None}
        if cols:
            self.store.update(number, **cols)

    def approvers(self, role, ticket):
        if role == "LINE_MANAGER":
            return {ticket.get("requester_manager")} - {"", None}
        return set(LOCAL_GROUP_MEMBERS.get(APPROVER_GROUPS.get(role, ""), set()))


# ============================================================================================== ServiceNow
AGENT_GROUP = "AI Service Desk"
ESCALATION_GROUP = "Service Desk"
ACCESS_CATEGORY = "access"
SN_FIELDS = ("sys_id,number,short_description,description,state,approval,hold_reason,assignment_group,caller_id,"
             "category,"
             "caller_id.user_name,caller_id.title,caller_id.manager.user_name,sys_created_on,resolved_at")
SN_APPROVAL = {"requested": "PENDING", "approved": "APPROVED", "rejected": "REJECTED"}
# Our work notes: "[#<seq> <agent>] text". ServiceNow journal timestamps have 1-second resolution, so notes
# written within the same second come back in arbitrary order (seen live: Policy before Understanding);
# the per-ticket sequence number restores the true order. One graph run = one "Agent trace" note whose
# lines "<i>. [<agent>] <text>" are the steps in execution order.
_NOTE = re.compile(r"^\[(?:#(\d+) )?([^\]\n]+)\]\s*(.*)$", re.S)
_STEP = re.compile(r"^\d+\. \[([^\]\n]+)\] ", re.M)
TRACE = "Agent trace"


def parse_work_notes(entries: list[dict]) -> list[dict]:
    """Journal entries (any order) -> [{time, agent, note}] in true order; trace notes split into steps."""
    rows = []
    for e in entries:
        m = _NOTE.match(e["value"])
        seq, agent, body = (int(m.group(1) or 0), m.group(2), m.group(3)) if m else (0, e["author"], e["value"])
        if agent == TRACE:
            heads = list(_STEP.finditer(body))
            for i, h in enumerate(heads):
                end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
                rows.append(((e["created_on"], seq, i), {"time": e["created_on"], "agent": h.group(1),
                                                         "note": body[h.end():end].strip()}))
        else:
            rows.append(((e["created_on"], seq, 0), {"time": e["created_on"], "agent": agent, "note": body}))
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


class ServiceNowSource(TicketSource):
    """Incident table of the PDI through ServiceNowClient (as ai.agent)."""
    name = "servicenow"

    def __init__(self, client=None, checkpoints=None):
        if client is None:
            from src.integrations.servicenow_client import ServiceNowClient
            client = ServiceNowClient.from_env()
        self.sn = client
        self._cache: dict[str, str] = {}
        if checkpoints is not None:
            self.attach_checkpoints(checkpoints)

    # ------------------------------------------------------------------ lookups (cached sys_ids)
    def _lookup(self, key, table, query, missing):
        if key not in self._cache:
            row = self.sn.first(table, query, "sys_id")
            if row is None:
                raise LookupError(missing)
            self._cache[key] = row["sys_id"]
        return self._cache[key]

    def _group(self, name):
        return self._lookup(f"group:{name}", "sys_user_group", f"name={name}",
                            f"group {name!r} missing in ServiceNow - run python -m src.integrations.servicenow_setup")

    def _user(self, user_name):
        return self._lookup(f"user:{user_name}", "sys_user", f"user_name={user_name}",
                            f"user {user_name!r} does not exist in ServiceNow")

    def _sys_id(self, number):
        try:
            return self._lookup(f"inc:{number}", "incident", f"number={number}", number)
        except LookupError:
            raise KeyError(number) from None

    def thread_id(self, number):
        return f"{self.sn.instance}|{number}"

    def link(self, number):
        return self.sn.record_url("incident", self._sys_id(number))

    def _status(self, r: dict) -> str:
        s = r["state"]
        if s == "1":
            return "NEW"
        if s == "2":
            return "IN_PROGRESS" if r["assignment_group"] == self._group(AGENT_GROUP) else "ESCALATED"
        if s == "3":
            if r.get("approval") == "requested":
                return "AWAITING_APPROVAL"
            # hold_reason (this PDI's field name; not on_hold_reason): 1 = Awaiting Caller
            return "WAITING_FOR_USER" if r.get("hold_reason") == "1" else "ON_HOLD"
        if s in ("6", "7"):
            return "RESOLVED"
        if s == "8":
            return "CLOSED_REJECTED"
        return f"SN_STATE_{s}"

    # ------------------------------------------------------------------ interface
    def create(self, requester, text):
        text = text.strip()
        short = text if len(text) <= 160 else text[:157].rstrip() + "..."
        rec = self.sn.create("incident", {
            "caller_id": self._user(requester), "category": ACCESS_CATEGORY, "contact_type": "self-service",
            "assignment_group": self._group(AGENT_GROUP), "impact": "3", "urgency": "3",
            "short_description": short, "description": text}, fields="sys_id,number")
        self._cache[f"inc:{rec['number']}"] = rec["sys_id"]
        return rec["number"]

    def create_incident(self, short, description, category, impact=2, urgency=2):
        """Monitoring incident (Phase 4). Caller = the integration user: there is no monitoring user record and
        the lean design creates no new users for it."""
        if category == ACCESS_CATEGORY:
            raise ValueError("monitoring incidents must not use the access category")
        rec = self.sn.create("incident", {
            "caller_id": self._user(self.sn.user), "category": category, "assignment_group": self._group(AGENT_GROUP),
            "impact": str(impact), "urgency": str(urgency), "short_description": short[:160],
            "description": description[:3900]}, fields="sys_id,number")
        self._cache[f"inc:{rec['number']}"] = rec["sys_id"]
        return rec["number"]

    def get(self, number):
        sid = self._sys_id(number)
        r = self.sn.get("incident", sid, SN_FIELDS)
        st = self._summary(number)
        # create() stores short_description = first 157 chars + "..." and description = full text;
        # a ticket typed in the UI may have only a short description, or two different texts
        short, desc = (r.get("short_description") or "").strip(), (r.get("description") or "").strip()
        head = short[:-3] if short.endswith("...") else short
        text = desc if desc and desc.startswith(head) else "\n".join(x for x in (short, desc) if x)
        notes = parse_work_notes(self.sn.get_journal("incident", sid, "work_notes"))
        return {"number": number, "sys_id": sid, "requester": r.get("caller_id.user_name", ""),
                "requester_role": r.get("caller_id.title") or "EMPLOYEE",
                "requester_manager": r.get("caller_id.manager.user_name") or "",
                "description": text, "category": r.get("category") or "",
                "state": self._status(r), "approval_status": SN_APPROVAL.get(r["approval"]),
                "created_at": r["sys_created_on"], "acknowledged_at": st.get("acknowledged_at"),
                "resolved_at": r.get("resolved_at") or None, "decision": st.get("decision"),
                "target": st.get("target"), "policy_id": st.get("policy_id"), "sop_id": st.get("sop_id"),
                "context": st.get("context") or {}, "user_message": st.get("user_message"), "work_notes": notes}

    def list(self, limit=20):
        me = self.sn.user
        # access requests AND monitoring incidents (Objective 3): everything the agents created or own.
        # (ServiceNow's ^OR binds to the condition before it only, so no category condition here.)
        rows = self.sn.query("incident", f"sys_created_by={me}^ORassignment_group={self._group(AGENT_GROUP)}",
                             "number,state,approval,category,assignment_group,caller_id.user_name,sys_created_on,"
                             "resolved_at", limit=limit, order_by="-number")
        out = []
        for r in rows:
            st = self._summary(r["number"])
            out.append({"number": r["number"], "requester": r.get("caller_id.user_name", ""),
                        "category": r.get("category") or "",
                        "state": self._status(r), "decision": st.get("decision"), "target": st.get("target"),
                        "created_at": r["sys_created_on"], "resolved_at": r.get("resolved_at") or None})
        return out

    def fetch_new(self, limit=20):
        rows = self.sn.query("incident", f"assignment_group={self._group(AGENT_GROUP)}^state=1",
                             "sys_id,number", limit=limit, order_by="sys_created_on")
        for r in rows:
            self._cache[f"inc:{r['number']}"] = r["sys_id"]
        return [r["number"] for r in rows]

    def fetch_active(self):
        rows = self.sn.query("incident", f"assignment_group={self._group(AGENT_GROUP)}^stateIN2,3",
                             "sys_id,number,state,approval,hold_reason,assignment_group,sys_updated_on",
                             limit=200, order_by="sys_updated_on")
        now_ = datetime.now(timezone.utc)
        out = []
        for r in rows:                         # sys_updated_on is UTC "YYYY-MM-DD HH:MM:SS" (display_value off)
            self._cache[f"inc:{r['number']}"] = r["sys_id"]
            st = self._status(r)
            if st in ACTIVE:
                upd = datetime.strptime(r["sys_updated_on"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                out.append({"number": r["number"], "state": st, "age_s": (now_ - upd).total_seconds()})
        return out

    def _tag(self, number, agent):
        """'[#<seq> <agent>]' with a per-ticket sequence number (true order despite 1-s timestamps).
        seq = highest number already on the ticket + 1 (read from the journal: correct across processes)."""
        entries = self.sn.get_journal("incident", self._sys_id(number), "work_notes")
        seqs = [int(m.group(1)) for e in entries if (m := _NOTE.match(e["value"])) and m.group(1)]
        return f"[#{max(seqs, default=0) + 1} {agent}]"

    def acknowledge(self, number):
        self.sn.update("incident", self._sys_id(number), {
            "state": "2", "assigned_to": self._user(self.sn.user),
            "work_notes": f"{self._tag(number, 'Orchestrator')} picked up by the multi-agent framework"},
            fields="sys_id")

    def add_note(self, number, agent, text):
        self.sn.add_work_note("incident", self._sys_id(number), f"{self._tag(number, agent)} {text}")

    def add_trace(self, number, steps):
        if steps:
            self.add_note(number, TRACE, "\n" + "\n".join(format_step(i, s) for i, s in enumerate(steps, 1)))

    def add_comment(self, number, text):
        self.sn.add_comment("incident", self._sys_id(number), text)

    def set_status(self, number, status, reason=""):
        from src.integrations.servicenow_client import CLOSE_CODES
        reason = (reason or status)[:3900]
        data = {
            "NEW": {"state": "1"},
            "IN_PROGRESS": {"state": "2"},
            "AWAITING_APPROVAL": {"state": "3", "approval": "requested"},
            "WAITING_FOR_USER": {"state": "3", "hold_reason": "1"},
            "RESOLVED": {"state": "6", "close_code": CLOSE_CODES["fixed"], "close_notes": reason},
            "CLOSED_REJECTED": {"state": "8", "close_notes": reason},
            "ESCALATED": {"state": "2", "assignment_group": self._group(ESCALATION_GROUP), "assigned_to": ""},
        }.get(status)
        if data is None:
            raise ValueError(f"unknown status {status}")
        self.sn.update("incident", self._sys_id(number), data, fields="sys_id,state")

    def record_approval(self, number, approved, approver, role):
        verb = "approved" if approved else "rejected"
        self.sn.set_task_approval("incident", self._sys_id(number), verb,
                                  f"{self._tag(number, 'Approval')} {approver} ({role}) {verb} the request via "
                                  f"the agent CLI (approver checked against ServiceNow; recorded by {self.sn.user})")

    def save_context(self, number, data):
        """Nothing to store: the structured context is in the LangGraph checkpoint (item 2.8)."""

    def approvers(self, role, ticket):
        if role == "LINE_MANAGER":
            return {ticket.get("requester_manager")} - {"", None}
        group = APPROVER_GROUPS.get(role or "")
        return {m["user_name"] for m in self.sn.group_members(group)} if group else set()


# ============================================================================================== factory
SOURCES = {"servicenow": ServiceNowSource, "local": LocalSource}


def get_source(name: str = "servicenow", **kwargs) -> TicketSource:
    if name not in SOURCES:
        raise ValueError(f"unknown ticket source {name!r}; use one of {sorted(SOURCES)}")
    return SOURCES[name](**kwargs)


# ============================================================================================== live self-test
def _selftest() -> int:
    src = ServiceNowSource()
    ok = True

    def check(name, cond, info=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'OK' if cond else 'FAIL'}]   {name}{': ' + str(info) if info not in ('', None) else ''}")

    print(f"ServiceNowSource on {src.sn.instance} as {src.sn.user}")
    a = src.create("employee01", "[selftest] Requesting access to github.com to review pull requests for our "
                                 "team project.")
    t = src.get(a)
    check(f"create + get {a}", t["state"] == "NEW" and t["requester"] == "employee01", src.link(a))
    check("requester role/manager from sys_user", (t["requester_role"], t["requester_manager"]) ==
          ("DEVELOPER", "manager01"), (t["requester_role"], t["requester_manager"]))
    check("description round trip", t["description"].startswith("[selftest] Requesting access to github.com"))
    check("fetch_new returns it", a in src.fetch_new(50))
    src.acknowledge(a)
    check("acknowledge -> IN_PROGRESS", src.get(a)["state"] == "IN_PROGRESS")
    check("no longer in fetch_new", a not in src.fetch_new(50))
    check("category read from the incident", t["category"] == ACCESS_CATEGORY, t["category"])
    check("stuck sweep sees it only after the age limit", a in src.fetch_stuck(-60) and a not in src.fetch_stuck(3600))
    src.add_note(a, "Understanding Agent", "target=github.com (managed)")
    src.add_trace(a, [{"agent": "Policy & Risk Agent", "output": "ALLOW_WITH_APPROVAL", "model": "deterministic"},
                      {"agent": "Critic Agent", "output": "APPROVE", "model": "groq:test", "latency_s": 0.5}])
    seqs = [int(m.group(1)) for e in src.sn.get_journal("incident", src._sys_id(a), "work_notes")
            if (m := _NOTE.match(e["value"])) and m.group(1)]
    check("note sequence numbers 1..3 from the journal", sorted(seqs) == [1, 2, 3], sorted(seqs))
    src.set_status(a, "AWAITING_APPROVAL")
    t = src.get(a)
    check("AWAITING_APPROVAL (On Hold + approval requested)",
          (t["state"], t["approval_status"]) == ("AWAITING_APPROVAL", "PENDING"), (t["state"], t["approval_status"]))
    check("approvers(SECURITY_TEAM)", src.approvers("SECURITY_TEAM", t) == {"security01"},
          src.approvers("SECURITY_TEAM", t))
    check("approvers(LINE_MANAGER) = caller's manager", src.approvers("LINE_MANAGER", t) == {"manager01"})
    check("manager01 refused for SECURITY_TEAM", not src.may_approve("manager01", "SECURITY_TEAM", t)[0],
          src.may_approve("manager01", "SECURITY_TEAM", t)[1])
    check("employee01 refused (self-approval)", not src.may_approve("employee01", "LINE_MANAGER", t)[0])
    check("security01 allowed", src.may_approve("security01", "SECURITY_TEAM", t)[0])
    src.record_approval(a, True, "security01", "SECURITY_TEAM")
    check("approval APPROVED recorded", src.get(a)["approval_status"] == "APPROVED")
    src.add_comment(a, "[selftest] Done: GitHub is now accessible.")
    src.set_status(a, "RESOLVED", "[selftest] Done: GitHub is now accessible.")
    t = src.get(a)
    check("RESOLVED with resolution code + notes", t["state"] == "RESOLVED" and t["resolved_at"], t["resolved_at"])
    check("work notes in order, agent names parsed", [n["agent"] for n in t["work_notes"]][:5] ==
          ["Orchestrator", "Understanding Agent", "Policy & Risk Agent", "Critic Agent", "Approval"],
          [n["agent"] for n in t["work_notes"]])

    b = src.create("employee01", "[selftest] I need access to some-unknown-tool.example")
    src.acknowledge(b)
    src.set_status(b, "ESCALATED", "needs review")
    check(f"ESCALATED {b} -> reassigned to {ESCALATION_GROUP!r}", src.get(b)["state"] == "ESCALATED")
    c = src.create("employee01", "[selftest] Please unblock chatgpt.com")
    src.acknowledge(c)
    src.set_status(c, "CLOSED_REJECTED", "[selftest] denied by POL-0030")
    check(f"CLOSED_REJECTED {c} (Canceled)", src.get(c)["state"] == "CLOSED_REJECTED")
    listed = {r["number"]: r for r in src.list(30)}
    check("list shows all three", all(n in listed for n in (a, b, c)),
          [(n, listed.get(n, {}).get("state")) for n in (a, b, c)])
    src.set_status(b, "CLOSED_REJECTED", "[selftest] cleanup")
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        sys.exit(_selftest())
    print(__doc__)
