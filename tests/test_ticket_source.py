"""TicketSource tests (item 1.3): LocalSource on a temporary SQLite file, ServiceNowSource with a mock client.

    python -m unittest tests.test_ticket_source -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.access.ticket_source import LocalSource, ServiceNowSource, TicketSource, get_source
from src.integrations.servicenow_client import CLOSE_CODES


class LocalContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = LocalSource(Path(self.tmp.name) / "tickets.db")

    def tearDown(self):
        self.src.store.db.close()
        self.tmp.cleanup()

    def test_lifecycle(self):
        n = self.src.create("employee01", "need github.com")
        t = self.src.get(n)
        self.assertEqual((t["state"], t["requester_role"], t["requester_manager"]), ("NEW", "DEVELOPER", "manager01"))
        self.assertEqual(self.src.fetch_new(), [n])
        self.src.acknowledge(n)
        self.assertEqual(self.src.get(n)["state"], "IN_PROGRESS")
        self.assertEqual(self.src.fetch_new(), [])
        self.src.add_note(n, "Understanding Agent", "target=github.com")
        self.src.save_context(n, {"decision": "ALLOW_WITH_APPROVAL", "target": "github.com",
                                  "context": {"policy": {"approver_role": "SECURITY_TEAM"}}})
        self.src.set_status(n, "AWAITING_APPROVAL")
        t = self.src.get(n)
        self.assertEqual((t["state"], t["approval_status"], t["decision"]),
                         ("AWAITING_APPROVAL", "PENDING", "ALLOW_WITH_APPROVAL"))
        self.assertEqual(t["context"]["policy"]["approver_role"], "SECURITY_TEAM")
        self.src.record_approval(n, True, "security01", "SECURITY_TEAM")
        self.src.add_comment(n, "Done")
        self.src.set_status(n, "RESOLVED", "Done")
        t = self.src.get(n)
        self.assertEqual((t["state"], t["approval_status"], t["user_message"]), ("RESOLVED", "APPROVED", "Done"))
        self.assertTrue(t["resolved_at"])
        self.assertEqual([x["agent"] for x in t["work_notes"]], ["Understanding Agent", "Approval"])

    def test_fetch_new_oldest_first(self):
        a, b = self.src.create("employee01", "one"), self.src.create("employee01", "two")
        self.assertEqual(self.src.fetch_new(), [a, b])

    def test_unknown_status_and_ticket(self):
        n = self.src.create("employee01", "x")
        with self.assertRaises(ValueError):
            self.src.set_status(n, "DONE")
        with self.assertRaises(KeyError):
            self.src.get("RITM0000000")

    def test_approver_rules(self):
        t = self.src.get(self.src.create("employee01", "x"))
        self.assertEqual(self.src.approvers("LINE_MANAGER", t), {"manager01"})
        self.assertEqual(self.src.approvers("SECURITY_TEAM", t), {"security01"})
        self.assertEqual(self.src.approvers("CHANGE_ADVISORY_BOARD", t), {"cab01"})     # item 3.2
        self.assertEqual(self.src.approvers("AI_GOVERNANCE_BOARD", t), {"aigov01"})
        self.assertEqual(self.src.approvers("NO_SUCH_ROLE", t), set())
        self.assertTrue(self.src.may_approve("security01", "SECURITY_TEAM", t)[0])
        ok, why = self.src.may_approve("manager01", "SECURITY_TEAM", t)
        self.assertFalse(ok)
        self.assertIn("allowed: security01", why)
        self.assertFalse(self.src.may_approve("employee01", "LINE_MANAGER", t)[0])   # self-approval
        self.assertFalse(self.src.may_approve("manager01", None, t)[0])               # no role on decision
        self.assertFalse(self.src.may_approve("anyone", "NO_SUCH_ROLE", t)[0])
        self.assertFalse(self.src.may_approve("security01", "CHANGE_ADVISORY_BOARD", t)[0])

    def test_factory(self):
        other = get_source("local", path=Path(self.tmp.name) / "x.db")
        self.assertIsInstance(other, TicketSource)
        other.store.db.close()   # Windows cannot delete an open SQLite file
        with self.assertRaises(ValueError):
            get_source("jira")


IDS = {"group:AI Service Desk": "DESK", "group:Service Desk": "HUMANS", "user:employee01": "EMP",
       "user:ai.agent": "AGENT", "user:security01": "U9", "inc:INC0010100": "SYS100"}


def sn_source(tmp):
    """Mock client with a working journal: work notes written via add_work_note / update are read back."""
    sn = mock.MagicMock()
    sn.instance, sn.user = "https://dev000000.service-now.com", "ai.agent"
    journal = []

    def note(_table, _sid, text):
        journal.insert(0, {"created_on": "2026-11-24 10:00:00", "author": "AI Agent", "value": text})

    def update(table, sid, data, fields=None):
        if "work_notes" in data:
            note(table, sid, data["work_notes"])
        return {"sys_id": sid}
    sn.add_work_note.side_effect = note
    sn.update.side_effect = update
    sn.get_journal.side_effect = lambda *a: list(journal)
    src = ServiceNowSource(client=sn)
    src._cache.update(IDS)
    src.notifications = False                     # a fake instance never queues e-mails (6.5)
    return src, sn


def record(**kw):
    base = {"sys_id": "SYS100", "number": "INC0010100", "short_description": "need github.com",
            "description": "need github.com", "state": "1", "approval": "not requested", "assignment_group": "DESK",
            "caller_id": "EMP", "caller_id.user_name": "employee01", "caller_id.title": "DEVELOPER",
            "caller_id.manager.user_name": "manager01", "sys_created_on": "2026-11-24 10:00:00", "resolved_at": ""}
    return {**base, **kw}


class ServiceNowMapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src, self.sn = sn_source(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sent(self):
        return self.sn.update.call_args.args[2]

    def test_create_payload(self):
        self.sn.create.return_value = {"sys_id": "SYS101", "number": "INC0010101"}
        self.assertEqual(self.src.create("employee01", "x" * 200), "INC0010101")
        table, body = self.sn.create.call_args.args
        self.assertEqual(table, "incident")
        self.assertEqual((body["caller_id"], body["category"], body["assignment_group"]), ("EMP", "access", "DESK"))
        self.assertEqual((len(body["short_description"]), body["description"]), (160, "x" * 200))

    def test_status_payloads(self):
        self.src.set_status("INC0010100", "RESOLVED", "Done: GitHub is accessible")
        self.assertEqual(self.sent(), {"state": "6", "close_code": CLOSE_CODES["fixed"],
                                       "close_notes": "Done: GitHub is accessible"})
        self.assertIn("–", self.sent()["close_code"])  # the strict choice value uses an EN DASH
        self.src.set_status("INC0010100", "AWAITING_APPROVAL")
        self.assertEqual(self.sent(), {"state": "3", "approval": "requested"})
        self.src.set_status("INC0010100", "CLOSED_REJECTED", "denied by POL-0030")
        self.assertEqual(self.sent(), {"state": "8", "close_notes": "denied by POL-0030"})
        self.src.set_status("INC0010100", "ESCALATED", "x")
        self.assertEqual(self.sent(), {"state": "2", "assignment_group": "HUMANS", "assigned_to": ""})
        with self.assertRaises(ValueError):
            self.src.set_status("INC0010100", "DONE")

    def test_state_mapping(self):
        cases = [(record(state="1"), "NEW"), (record(state="2"), "IN_PROGRESS"),
                 (record(state="2", assignment_group="HUMANS"), "ESCALATED"),
                 (record(state="3", approval="requested"), "AWAITING_APPROVAL"),
                 (record(state="3"), "ON_HOLD"), (record(state="6"), "RESOLVED"), (record(state="7"), "RESOLVED"),
                 (record(state="8"), "CLOSED_REJECTED")]
        for rec, expected in cases:
            self.assertEqual(self.src._status(rec), expected, rec)

    def test_get_normalises_ticket(self):
        self.sn.get.return_value = record(state="3", approval="requested", short_description="need github.com...",
                                          description="need github.com for reviews")
        self.sn.get_journal.return_value = [  # newest first, as ServiceNow returns it
            {"created_on": "2026-11-24 10:00:02", "author": "AI Agent", "kind": "Work notes",
             "value": "[#2 Policy & Risk Agent] ALLOW_WITH_APPROVAL"},
            {"created_on": "2026-11-24 10:00:01", "author": "AI Agent", "kind": "Work notes",
             "value": "[#1 Understanding Agent] target=github.com\nsecond line"},
            {"created_on": "2026-11-24 10:00:00", "author": "Some Human", "kind": "Work notes", "value": "hand note"}]
        self.sn.get_journal.side_effect = None
        self.src._summary = lambda n: {"decision": "ALLOW_WITH_APPROVAL", "target": "github.com"}   # checkpoint
        t = self.src.get("INC0010100")
        self.assertEqual((t["state"], t["approval_status"], t["requester_role"], t["requester_manager"]),
                         ("AWAITING_APPROVAL", "PENDING", "DEVELOPER", "manager01"))
        self.assertEqual(t["description"], "need github.com for reviews")
        self.assertEqual((t["decision"], t["target"]), ("ALLOW_WITH_APPROVAL", "github.com"))
        self.assertEqual([(n["agent"], n["note"]) for n in t["work_notes"]],
                         [("Some Human", "hand note"), ("Understanding Agent", "target=github.com\nsecond line"),
                          ("Policy & Risk Agent", "ALLOW_WITH_APPROVAL")])

    def test_same_second_notes_ordered_by_sequence_and_trace_split(self):
        # live bug: notes written within one second came back as Policy before Understanding
        t = "2026-11-24 10:00:05"
        entries = [
            {"created_on": t, "author": "AI Agent", "value": "[#3 Approval Guard] manager01 REFUSED"},
            {"created_on": t, "author": "AI Agent", "value": "[#1 Orchestrator] picked up"},
            {"created_on": t, "author": "AI Agent", "value": "[#2 Agent trace]\n1. [Understanding Agent] target=x\n"
                                                          "2. [Retrieval Agent] SOP-0025\n3. [Policy & Risk Agent] AWA"}]
        from src.access.ticket_source import parse_work_notes
        self.assertEqual([n["agent"] for n in parse_work_notes(entries)],
                         ["Orchestrator", "Understanding Agent", "Retrieval Agent", "Policy & Risk Agent",
                          "Approval Guard"])
        self.assertEqual(parse_work_notes(entries)[1]["note"], "target=x")

    def test_trace_is_one_note_with_numbered_steps(self):
        self.src.add_trace("INC0010100", [("Understanding Agent", "a"), ("Policy & Risk Agent", "b")])
        text = self.sn.add_work_note.call_args.args[2]
        self.assertEqual(text, "[#1 Agent trace] \n1. [Understanding Agent] a\n2. [Policy & Risk Agent] b")
        self.src.add_note("INC0010100", "Approval Guard", "c")
        self.assertTrue(self.sn.add_work_note.call_args.args[2].startswith("[#2 Approval Guard]"))
        self.src.add_trace("INC0010100", [])   # nothing to write
        self.assertEqual(self.sn.add_work_note.call_count, 2)

    def test_ui_ticket_with_two_texts(self):
        self.sn.get.return_value = record(short_description="GitHub please", description="for code reviews")
        self.sn.get_journal.side_effect, self.sn.get_journal.return_value = None, []
        self.assertEqual(self.src.get("INC0010100")["description"], "GitHub please\nfor code reviews")

    def test_unknown_number_is_keyerror(self):
        self.sn.first.return_value = None
        with self.assertRaises(KeyError):
            self.src.get("INC9999999")

    def test_fetch_new_query(self):
        self.sn.query.return_value = [{"sys_id": "S1", "number": "INC1"}]
        self.assertEqual(self.src.fetch_new(), ["INC1"])
        self.assertEqual(self.sn.query.call_args.args[1], "assignment_group=DESK^state=1")
        self.assertEqual(self.sn.query.call_args.kwargs["order_by"], "sys_created_on")

    def test_acknowledge_assigns_agent(self):
        self.src.acknowledge("INC0010100")
        body = self.sent()
        self.assertEqual((body["state"], body["assigned_to"]), ("2", "AGENT"))
        self.assertTrue(body["work_notes"].startswith("[#1 Orchestrator] picked up"))
        self.src.add_note("INC0010100", "Execution Agent", "x")          # sequence continues from the journal
        self.assertTrue(self.sn.add_work_note.call_args.args[2].startswith("[#2 Execution Agent]"))

    def test_notes_comments_approval(self):
        self.src.add_note("INC0010100", "Execution Agent", "attempt 1 ok")
        self.sn.add_work_note.assert_called_with("incident", "SYS100", "[#1 Execution Agent] attempt 1 ok")
        self.src.add_comment("INC0010100", "Done")
        self.sn.add_comment.assert_called_with("incident", "SYS100", "Done")
        self.src.record_approval("INC0010100", False, "security01", "SECURITY_TEAM")
        args = self.sn.set_task_approval.call_args.args
        self.assertEqual(args[:3], ("incident", "SYS100", "rejected"))
        self.assertIn("security01 (SECURITY_TEAM) rejected", args[3])

    def test_approvers_from_servicenow(self):
        self.sn.group_members.return_value = [{"sys_id": "U9", "user_name": "security01"}]
        t = {"requester": "employee01", "requester_manager": "manager01"}
        self.assertEqual(self.src.approvers("SECURITY_TEAM", t), {"security01"})
        self.sn.group_members.assert_called_with("IT Security")
        self.assertEqual(self.src.approvers("LINE_MANAGER", t), {"manager01"})
        self.assertEqual(self.src.approvers("NO_SUCH_ROLE", t), set())

    def test_thread_keyed_by_instance(self):
        self.assertEqual(self.src.thread_id("INC0010100"), "https://dev000000.service-now.com|INC0010100")
        self.src.save_context("INC0010100", {"decision": "DENY"})        # no-op: context is in the checkpoint
        self.sn.update.assert_not_called()

    def test_fetch_stuck_filters_by_age(self):
        row = {"state": "2", "approval": "not requested", "hold_reason": "", "assignment_group": "DESK"}
        self.sn.query.return_value = [
            {**row, "sys_id": "S1", "number": "INC1", "sys_updated_on": "2020-01-01 00:00:00"},
            {**row, "sys_id": "S2", "number": "INC2", "sys_updated_on": "2999-01-01 00:00:00"},
            {**row, "sys_id": "S3", "number": "INC3", "sys_updated_on": "2020-01-01 00:00:00", "state": "3",
             "hold_reason": "1"},                                          # waiting for the employee: not stuck
            {**row, "sys_id": "S4", "number": "INC4", "sys_updated_on": "2020-01-01 00:00:00", "state": "3",
             "approval": "requested"}]                                     # waiting for approval: not active
        self.assertEqual(self.src.fetch_stuck(600), ["INC1"])
        self.assertEqual(self.sn.query.call_args.args[1], "assignment_group=DESK^stateIN2,3")
        self.assertEqual([(t["number"], t["state"]) for t in self.src.fetch_active()],
                         [("INC1", "IN_PROGRESS"), ("INC2", "IN_PROGRESS"), ("INC3", "WAITING_FOR_USER")])

    def test_waiting_for_user_payload(self):
        self.src.set_status("INC0010100", "WAITING_FOR_USER")
        self.assertEqual(self.sent(), {"state": "3", "hold_reason": "1"})

    def test_save_result_writes_trace_message_status(self):
        self.sn.group_members.return_value = [{"sys_id": "U9", "user_name": "security01"}]
        result = {"final_state": "AWAITING_APPROVAL", "user_message": "needs approval",
                  "trace": [{"agent": "Understanding Agent", "output": "github.com", "model": "groq:x",
                             "latency_s": 0.5}, ("Policy & Risk Agent", "ALLOW_WITH_APPROVAL")],
                  "understanding": {"target": "github.com"},
                  "policy": {"decision": "ALLOW_WITH_APPROVAL", "approver_role": "SECURITY_TEAM"}}
        self.src.save_result("INC0010100", {"requester": "employee01", "requester_manager": "manager01"}, result)
        trace = self.sn.add_work_note.call_args.args[2]
        self.assertIn("3. [Policy & Risk Agent] waiting for approval by SECURITY_TEAM: security01", trace)
        self.assertEqual(self.sn.add_work_note.call_count, 1)                 # one trace note per run
        self.sn.add_comment.assert_called_with("incident", "SYS100", "needs approval")
        # 6.5: the approvers join the watch list in the same update -> "approval needed" e-mail reaches them
        self.assertEqual(self.sent(), {"state": "3", "approval": "requested", "watch_list": "U9"})
        self.assertIn("1. [Understanding Agent] github.com | groq:x 0.50s", trace)


if __name__ == "__main__":
    unittest.main()
