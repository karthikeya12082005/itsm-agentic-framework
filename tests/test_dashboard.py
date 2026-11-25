"""Dashboard tests (item 5.2): every page renders from a local desk + in-memory checkpoints, user text is escaped,
unknown tickets are 404, the evaluation Markdown is converted. Offline: LLM off, endpoint and lab mocked."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.access.graph import build_graph
from src.access.ticket_source import LocalSource
from src.access.worker import process_ticket
from src.common import llm_router
from src.dashboard import app as D
from src.orchestrator import checkpoint as cp


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.dict(os.environ, {"LLM_MODE": "off"}),
                  mock.patch.object(llm_router, "_write_log"),
                  mock.patch("src.access.endpoint_client.status", side_effect=RuntimeError("service offline")),
                  mock.patch("src.lab.supervisor.status", return_value={
                      "web": {"status": "ok", "failing": []}, "cache": {"status": "down", "failing": ["up"]}})):
            p.start()
            self.addCleanup(p.stop)
        self.saver = cp.memory_saver()
        self.src = LocalSource(Path(self.tmp.name) / "t.db").attach_checkpoints(self.saver)
        graph = build_graph(None, checkpointer=self.saver)
        self.deny = self.src.create("employee01", "I need ChatGPT <script>alert(1)</script> for my code")
        process_ticket(self.src, graph, self.deny)
        self.unknown = self.src.create("employee01", "Please give me access to notion.so")
        process_ticket(self.src, graph, self.unknown)
        self.client = TestClient(D.create_app(self.src, self.saver))

    def tearDown(self):
        self.src.store.db.close()
        self.tmp.cleanup()

    def test_overview_lists_tickets_and_status(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        for s in (self.deny, self.unknown, "Closed / rejected", "Escalated", "not reachable", "service offline",
                  "cache", "chatgpt.com"):
            self.assertIn(s, r.text)

    def test_detail_shows_trace_policy_and_escapes_text(self):
        r = self.client.get(f"/ticket/{self.deny}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Policy &amp; Risk Agent", r.text)
        self.assertIn("DENY", r.text)
        self.assertIn("Understanding Agent", r.text)
        self.assertNotIn("<script>alert(1)", r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_api_and_404(self):
        rows = self.client.get("/api/tickets").json()
        self.assertEqual({r["number"] for r in rows}, {self.deny, self.unknown})
        self.assertEqual({r["flow"] for r in rows}, {"access"})
        detail = self.client.get(f"/api/ticket/{self.unknown}").json()
        self.assertEqual(detail["agents"]["policy"]["decision"], "ESCALATE_FOR_REVIEW")
        self.assertEqual(self.client.get("/ticket/RITM9999999").status_code, 404)
        self.assertEqual(self.client.get("/ticket/not-a-number").status_code, 404)

    def test_metrics_and_llm_pages(self):
        self.assertEqual(self.client.get("/metrics").status_code, 200)
        r = self.client.get("/llm")
        self.assertEqual(r.status_code, 200)
        self.assertIn("LLM off", r.text)                  # LLM_MODE=off -> empty chains


class MarkdownTest(unittest.TestCase):
    def test_tables_headings_lists(self):
        h = D.md_to_html("# Title\n\nSome **bold** `code` text\ncontinued.\n\n| M | a | b |\n|---|---|---|\n"
                         "| x | 0.5 | 1 |\n| y | - | R51 |\n\n- one\n- two <b>\n")
        self.assertIn("<h2>Title</h2>", h)
        self.assertIn("<b>bold</b>", h)
        self.assertIn("<code>code</code>", h)
        self.assertIn("text continued.", h)
        self.assertIn('<th class="num">a</th>', h)       # numeric column right-aligned with its header
        self.assertEqual(h.count("<tr>"), 3)             # header + 2 rows, separator dropped
        self.assertIn("<li>two &lt;b&gt;</li>", h)


if __name__ == "__main__":
    unittest.main()
