"""Endpoint service v2 pure logic (items 3.1, 3.3, 3.6): no admin rights needed.

    python -m unittest tests.test_endpoint_core -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from endpoint_service import core

TASKLIST = ('"System Idle Process","0","Services","0","8 K"\r\n'
            '"Code.exe","1234","Console","1","150,000 K"\r\n'
            '"chrome.exe","555","Console","1","90,000 K"\r\n')


class Processes(unittest.TestCase):
    def test_parse_and_blocking(self):
        running = core.parse_tasklist_csv(TASKLIST)
        self.assertIn("code.exe", running)
        self.assertEqual(core.blocking_processes("Microsoft.VisualStudioCode", running), ["Code.exe"])
        self.assertEqual(core.blocking_processes("Google.Chrome", running), ["chrome.exe"])
        self.assertEqual(core.blocking_processes("Postman.Postman", running), [])
        self.assertEqual(core.blocking_processes("Python.Python.3.12", running), [])


class Rollback(unittest.TestCase):
    def test_rollback_restores_before_state(self):
        self.assertEqual(core.domain_rollback_spec("github.com", []),
                         {"action": "RESTORE_DOMAIN", "params": {"domain": "github.com", "allowed": False}})
        # granted before this ticket -> the ticket's rollback must keep it allowed
        self.assertTrue(core.domain_rollback_spec("github.com", ["github.com"])["params"]["allowed"])

    def test_rollback_state_detects_double_rollback(self):
        recs = [{"action_id": "A1", "action": "ALLOWLIST_DOMAIN", "status": "SUCCEEDED", "rollback": {"x": 1}},
                {"action_id": "R1", "action": "ROLLBACK", "status": "SUCCEEDED", "params": {"of_action_id": "A1"}}]
        orig, done = core.rollback_state(recs, "A1")
        self.assertEqual((orig["action_id"], done["action_id"]), ("A1", "R1"))
        self.assertEqual(core.rollback_state(recs[:1], "A1")[1], None)
        self.assertEqual(core.rollback_state(recs, "nope"), (None, None))


class Grants(unittest.TestCase):
    def test_active_and_due(self):
        now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        past, future = (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()
        rb = {"action": "RESTORE_DOMAIN", "params": {"domain": "x", "allowed": False}}
        recs = [
            {"action_id": "A1", "action": "ALLOWLIST_DOMAIN", "status": "SUCCEEDED", "expires_at": past, "rollback": rb,
             "ticket_id": "INC1", "params": {"domain": "github.com"}},
            {"action_id": "A2", "action": "ALLOWLIST_DOMAIN", "status": "SUCCEEDED", "expires_at": future,
             "rollback": rb, "ticket_id": "INC2", "params": {"domain": "kaggle.com"}},
            {"action_id": "A3", "action": "ALLOWLIST_DOMAIN", "status": "SUCCEEDED", "expires_at": None,
             "rollback": rb},                                                   # permanent grant
            {"action_id": "A4", "action": "ALLOWLIST_DOMAIN", "status": "SUCCEEDED", "expires_at": past,
             "rollback": rb, "ticket_id": "INC4"},
            {"action_id": "R4", "action": "ROLLBACK", "status": "SUCCEEDED", "params": {"of_action_id": "A4"}},
        ]
        recs.append({"action_id": "S1", "action": "INSTALL_SOFTWARE", "status": "SUCCEEDED", "expires_at": past,
                     "ticket_id": "INC5", "params": {"package_id": "7zip.7zip"},
                     "rollback": {"action": "UNINSTALL_SOFTWARE", "params": {"package_id": "7zip.7zip"}}})
        recs.append({"action_id": "S2", "action": "INSTALL_SOFTWARE", "status": "SUCCEEDED", "expires_at": None,
                     "rollback": None})                                 # already installed before: no expiry
        g = {x["action_id"]: x["due"] for x in core.active_grants(recs, now)}
        self.assertEqual(g, {"A1": True, "A2": False, "S1": True})


if __name__ == "__main__":
    unittest.main()
