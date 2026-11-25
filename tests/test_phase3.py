"""Phase 3 flows (offline, rules-only): running-app pre-check (3.1), time-limited grants (3.3),
async install jobs (3.7), unknown targets (3.4).

    python -m unittest tests.test_phase3 -v
"""
import unittest
from datetime import datetime, timezone
from unittest import mock

from src.access import agents, endpoint_client
from src.access.worker import expire_grants, sweep_stuck, tend_active
from tests.test_multi_agent import Base


def quiet(*_):
    pass


class RunningAppPrecheck(Base):                       # 3.1
    use_fake_llm = False

    def test_open_app_waits_for_user_then_installs(self):
        self.ep.blocking = ["Code.exe"]
        n, final = self.run_ticket("Can you install VS Code on my laptop? I need it for development.")
        t = self.src.get(n)
        self.assertEqual((final, t["state"]), ("WAITING_FOR_USER", "WAITING_FOR_USER"))
        self.assertIn("close Code.exe", t["user_message"])
        self.assertEqual(self.ep.executed, [])                       # no attempt used, nothing escalated
        self.assertEqual(self.values(n)["attempts"], 0)
        tend_active(self.src, self.graph, out=quiet)                  # still open: nothing happens
        self.assertEqual(self.src.get(n)["state"], "WAITING_FOR_USER")
        self.ep.blocking = []                                         # employee closes VS Code
        tend_active(self.src, self.graph, out=quiet)
        self.assertEqual(self.src.get(n)["state"], "RESOLVED")
        self.assertEqual([e[1] for e in self.ep.executed], ["Microsoft.VisualStudioCode"])

    def test_app_never_closed_closes_ticket_not_escalated(self):
        self.ep.blocking = ["Code.exe"]
        n, _ = self.run_ticket("Please install VS Code")
        tend_active(self.src, self.graph, out=quiet, user_wait_s=-1)  # waiting time is over
        t = self.src.get(n)
        self.assertEqual(t["state"], "CLOSED_REJECTED")
        self.assertIn("still running", t["user_message"])
        self.assertIn("new request", t["user_message"])
        self.assertEqual(self.ep.executed, [])

    def test_domain_requests_skip_the_precheck(self):
        self.ep.blocking = ["chrome.exe"]
        n, final = self.run_ticket("Please give me access to gemini.google.com")
        self.assertEqual(final, "RESOLVED")


class AsyncInstallJobs(Base):                         # 3.7
    use_fake_llm = False

    def test_install_job_does_not_block_and_resumes_when_done(self):
        self.ep.async_jobs = True
        n, final = self.run_ticket("Please install Zoom for client meetings")
        self.assertEqual((final, self.src.get(n)["state"]), ("IN_PROGRESS", "IN_PROGRESS"))
        self.assertIn("installation of Zoom Workplace has started", self.src.get(n)["user_message"])
        tend_active(self.src, self.graph, out=quiet)                  # job still RUNNING
        self.assertEqual(self.src.get(n)["state"], "IN_PROGRESS")
        self.ep.jobs["A1"]["status"] = "SUCCEEDED"
        tend_active(self.src, self.graph, out=quiet)
        t = self.src.get(n)
        self.assertEqual(t["state"], "RESOLVED")
        notes = " ".join(x["note"] for x in t["work_notes"])
        self.assertIn("install job A1 finished -> SUCCEEDED", notes)
        self.assertIn("PASSED", notes)

    def test_failed_job_retries_then_escalates(self):
        self.ep.async_jobs, self.ep.verify_ok = True, False
        n, _ = self.run_ticket("Please install 7-Zip")
        for _ in range(4):
            for j in self.ep.jobs.values():
                j["status"] = "FAILED"
            tend_active(self.src, self.graph, out=quiet)
        self.assertEqual(self.src.get(n)["state"], "ESCALATED")      # never a fake RESOLVED
        self.assertEqual(len(self.ep.executed), agents.MAX_ATTEMPTS)

    def test_unknown_job_after_service_restart_counts_as_failed(self):
        self.ep.async_jobs = True
        n, _ = self.run_ticket("Please install Notepad++")
        with mock.patch.object(endpoint_client, "job", side_effect=endpoint_client.EndpointError("HTTP 404: unknown")):
            tend_active(self.src, self.graph, out=quiet)
        v = self.values(n)
        self.assertEqual(v["attempts"], 2)                           # attempt 1 failed -> attempt 2 started

    def test_sweep_leaves_paused_tickets_alone(self):
        self.ep.async_jobs = True
        n, _ = self.run_ticket("Please install Slack")
        self.assertEqual(sweep_stuck(self.src, self.graph, older_than_s=-60, out=quiet), [])
        self.assertEqual(len(self.ep.executed), 1)


class TimeLimitedAccess(Base):                        # 3.3
    use_fake_llm = False

    def test_duration_parsing(self):
        cases = {"I need github.com for 2 days": 48, "access to kaggle for the next 3 hours": 3,
                 "gemini for one week please": 168, "need gemini.google.com today only": 8,
                 "need gemini.google.com": None, "for 2 months": 1440,
                 "gemini.google.com for 30 minutes please": 0.5}
        for text, hours in cases.items():
            self.assertEqual(agents.parse_duration(text), hours, text)

    def test_time_limited_grant_has_expiry_and_message_says_until(self):
        n, final = self.run_ticket("Please give me access to gemini.google.com for 2 days")
        self.assertEqual(final, "RESOLVED")
        exp = datetime.fromisoformat(self.ep.expires[0])
        hours = (exp - datetime.now(timezone.utc)).total_seconds() / 3600
        self.assertAlmostEqual(hours, 48, delta=0.1)
        self.assertIn("until " + self.ep.expires[0][:10], self.src.get(n)["user_message"])

    def test_time_limited_install_gets_expiry_and_says_uninstall(self):
        n, final = self.run_ticket("Install the 7zip archiver for 3 days please")
        self.assertEqual(final, "RESOLVED")
        self.assertIsNotNone(self.ep.expires[0])                     # passed to the service -> /grants
        self.assertIn("uninstalled automatically", self.src.get(n)["user_message"])

    def test_cap_at_30_days(self):
        n, _ = self.run_ticket("Please give me access to gemini.google.com for 3 months")
        self.assertEqual(self.values(n)["understanding"]["duration_hours"], agents.MAX_GRANT_HOURS)

    def test_permanent_grant_has_no_expiry(self):
        self.run_ticket("Please give me access to gemini.google.com")
        self.assertEqual(self.ep.expires, [None])

    def test_expired_grants_are_revoked_and_noted(self):
        n, _ = self.run_ticket("Please give me access to gemini.google.com for 2 hours")
        due = [{"action_id": "A1", "ticket_id": n, "action": "ALLOWLIST_DOMAIN",
                "params": {"domain": "gemini.google.com"}, "expires_at": "2026-01-01T00:00:00+00:00", "due": True},
               {"action_id": "A9", "ticket_id": n, "action": "ALLOWLIST_DOMAIN",
                "params": {"domain": "kaggle.com"}, "expires_at": "2999-01-01T00:00:00+00:00", "due": False}]
        with mock.patch.object(endpoint_client, "grants", return_value=due), \
                mock.patch.object(endpoint_client, "rollback", return_value={"action_id": "R1", "status": "SUCCEEDED"}) as rb:
            self.assertEqual(expire_grants(self.src, out=quiet), [n])
        rb.assert_called_once_with("A1", n)
        t = self.src.get(n)
        self.assertIn("expired", t["work_notes"][-1]["note"])
        self.assertIn("has ended", t["user_message"])


class UnknownTargets(Base):                           # 3.4
    use_fake_llm = False

    def test_unknown_domain_and_software_escalate_for_review(self):
        for text in ("I need access to notion.so for team notes", "Please install Figma for UI mock-ups"):
            n, final = self.run_ticket(text)
            v = self.values(n)
            self.assertEqual((final, v["policy"]["decision"]), ("ESCALATED", "ESCALATE_FOR_REVIEW"), text)
        self.assertEqual(self.ep.executed, [])


if __name__ == "__main__":
    unittest.main()
