"""Worker + LangGraph flow tests (items 1.4 / 1.5) on LocalSource. Offline: the LLM is switched off (rule-based
extraction) and the endpoint service is faked, so no API key, network or Administrator terminal is needed.

    python -m unittest tests.test_access_worker -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.access import agents
from src.common import llm_router
from src.access.graph import build_graph
from src.access.ticket_source import LocalSource
from src.access.worker import approve_ticket, process_ticket, run_poller
from src.orchestrator import checkpoint as cp


class FakeEndpoint:
    def __init__(self, verify_ok=True):
        self.verify_ok = verify_ok
        self.executed = []

    def execute(self, kind, target, ticket_id, authorization_id):
        self.executed.append((kind, target, ticket_id, authorization_id))
        return {"status": "SUCCEEDED", "action": "ALLOWLIST_DOMAIN" if kind == "domain" else "INSTALL_SOFTWARE",
                "action_id": f"A{len(self.executed)}"}

    def verify(self, kind, target):
        return self.verify_ok


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = LocalSource(Path(self.tmp.name) / "tickets.db")
        self.graph = build_graph(None, checkpointer=cp.memory_saver())
        self.ep = FakeEndpoint()
        for p in (mock.patch.dict(os.environ, {"LLM_MODE": "off"}),                # LLM off -> rule-based
                  mock.patch.object(llm_router, "_write_log"),                      # keep logs/ clean
                  mock.patch.object(agents.endpoint_client, "execute", side_effect=lambda *a: self.ep.execute(*a)),
                  mock.patch.object(agents.endpoint_client, "verify", side_effect=lambda *a: self.ep.verify(*a))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.src.store.db.close()
        self.tmp.cleanup()

    def run_ticket(self, text):
        n = self.src.create("employee01", text)
        return n, process_ticket(self.src, self.graph, n)

    def test_chatgpt_denied_without_touching_endpoint(self):
        n, final = self.run_ticket("I need ChatGPT (chatgpt.com) to help me debug code.")
        t = self.src.get(n)
        self.assertEqual((final, t["decision"], t["target"]), ("CLOSED_REJECTED", "DENY", "chatgpt.com"))
        self.assertIn("Gemini", t["user_message"])
        self.assertEqual(self.ep.executed, [])
        agents_seen = [x["agent"] for x in t["work_notes"]]
        for a in ("Understanding Agent", "Policy & Risk Agent", "Communication Agent"):
            self.assertIn(a, agents_seen)

    def test_gemini_allowed_executed_and_verified(self):
        n, final = self.run_ticket("Please give me access to gemini.google.com for documentation.")
        self.assertEqual(final, "RESOLVED")
        self.assertEqual(self.ep.executed, [("domain", "gemini.google.com", n, f"AUTH-{n}-ALLOW")])
        self.assertTrue(self.src.get(n)["resolved_at"])

    def test_failed_verification_escalates_after_two_attempts(self):
        self.ep.verify_ok = False
        n, final = self.run_ticket("Please give me access to gemini.google.com")
        self.assertEqual(final, "ESCALATED")      # never a fake RESOLVED
        self.assertEqual(len(self.ep.executed), agents.MAX_ATTEMPTS)

    def test_unknown_target_escalates_for_review(self):
        n, final = self.run_ticket("I need access to some-unknown-tool.org for a hackathon")
        self.assertEqual((final, self.src.get(n)["decision"]), ("ESCALATED", "ESCALATE_FOR_REVIEW"))
        self.assertEqual(self.ep.executed, [])

    def test_github_waits_then_only_security_team_may_approve(self):
        n, final = self.run_ticket("Requesting access to github.com to clone dependencies.")
        t = self.src.get(n)
        self.assertEqual((final, t["approval_status"]), ("AWAITING_APPROVAL", "PENDING"))
        self.assertIn("IT Security team", t["user_message"])
        self.assertTrue(any("security01" in x["note"] for x in t["work_notes"]))
        self.assertEqual(self.ep.executed, [])

        ok, why = approve_ticket(self.src, self.graph, n, "manager01")
        self.assertFalse(ok)
        self.assertIn("not SECURITY_TEAM", why)
        ok, _ = approve_ticket(self.src, self.graph, n, "employee01")
        self.assertFalse(ok)                                        # self-approval
        t = self.src.get(n)
        self.assertEqual(t["state"], "AWAITING_APPROVAL")           # still waiting
        self.assertEqual(sum(x["agent"] == "Approval Guard" for x in t["work_notes"]), 2)
        self.assertEqual(self.ep.executed, [])

        ok, why = approve_ticket(self.src, self.graph, n, "security01")
        self.assertTrue(ok, why)
        t = self.src.get(n)
        self.assertEqual((t["state"], t["approval_status"]), ("RESOLVED", "APPROVED"))
        self.assertEqual([e[1] for e in self.ep.executed], ["github.com"])

    def test_rejected_by_valid_approver(self):
        n, _ = self.run_ticket("Requesting access to github.com")
        ok, why = approve_ticket(self.src, self.graph, n, "security01", approved=False)
        self.assertTrue(ok, why)
        t = self.src.get(n)
        self.assertEqual((t["state"], t["approval_status"]), ("CLOSED_REJECTED", "REJECTED"))
        self.assertEqual(self.ep.executed, [])

    def test_line_manager_approval_for_software(self):
        n, final = self.run_ticket("Please install Postman, I need it to test our REST API.")
        self.assertEqual((final, self.src.get(n)["target"]), ("AWAITING_APPROVAL", "Postman.Postman"))
        self.assertFalse(approve_ticket(self.src, self.graph, n, "security01")[0])   # not the line manager
        ok, why = approve_ticket(self.src, self.graph, n, "manager01")
        self.assertTrue(ok, why)
        self.assertEqual(self.src.get(n)["state"], "RESOLVED")
        self.assertEqual(self.ep.executed[0][:2], ("software", "Postman.Postman"))

    def test_approve_only_when_waiting(self):
        n, _ = self.run_ticket("I need chatgpt.com")
        ok, why = approve_ticket(self.src, self.graph, n, "security01")
        self.assertFalse(ok)
        self.assertIn("not awaiting approval", why)

    def test_lost_context_is_rebuilt(self):
        n, _ = self.run_ticket("Requesting access to github.com")
        cp.delete(self.graph.checkpointer, self.src.thread_id(n))     # e.g. checkpoint file deleted
        ok, why = approve_ticket(self.src, self.graph, n, "security01")
        self.assertTrue(ok, why)
        notes = [x["note"] for x in self.src.get(n)["work_notes"]]
        self.assertTrue(any("checkpoint of this ticket was missing" in x for x in notes))
        self.assertEqual(self.src.get(n)["state"], "RESOLVED")

    def test_pipeline_crash_escalates(self):
        broken = mock.MagicMock()
        broken.invoke.side_effect = RuntimeError("LLM router exploded")
        n = self.src.create("employee01", "need gemini.google.com")
        self.assertEqual(process_ticket(self.src, broken, n), "ESCALATED")
        t = self.src.get(n)
        self.assertEqual(t["state"], "ESCALATED")
        self.assertIn("RuntimeError", t["work_notes"][-1]["note"])

    def test_only_new_tickets_are_processed(self):
        n, _ = self.run_ticket("I need chatgpt.com")
        self.assertIsNone(process_ticket(self.src, self.graph, n))

    def test_poller_once_processes_queue_in_order(self):
        a = self.src.create("employee01", "I need chatgpt.com")
        b = self.src.create("employee01", "Please give me access to gemini.google.com")
        lines = []
        self.assertEqual(run_poller(self.src, self.graph, once=True, out=lines.append), 0)
        self.assertEqual([x.split(":")[0] for x in lines[1:]], [a, b])
        self.assertEqual((self.src.get(a)["state"], self.src.get(b)["state"]), ("CLOSED_REJECTED", "RESOLVED"))
        self.assertEqual(self.src.fetch_new(), [])

    def test_poller_survives_source_outage(self):
        down = mock.MagicMock(name="source")
        down.name = "servicenow"
        down.fetch_new.side_effect = ConnectionError("instance hibernating")
        lines = []
        self.assertEqual(run_poller(down, self.graph, once=True, out=lines.append), 1)
        self.assertIn("poll failed", lines[-1])


if __name__ == "__main__":
    unittest.main()
