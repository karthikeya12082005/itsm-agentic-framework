"""Notifications (item 6.5): plus-addresses, the queue, and the SMTP fallback decision. Offline: no mail is sent
(send_smtp is mocked), the queue lives in a temporary file."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src.common import notify


class FakeSN:
    """ServiceNow-like source: mail_status tells whether the platform sent the mail."""
    name, notifications = "servicenow", True

    def __init__(self, sent):
        self.sent, self.notes = sent, []

    def mail_status(self, number, since):
        return {"sent": self.sent, "types": ["sent" if self.sent else "send-ready"]}

    def user_email(self, user):
        return f"demo+{user}@example.org"

    def add_note(self, number, agent, text):
        self.notes.append(text)


class FakeLocal:
    """Local desk: no ticket-system e-mail at all (no mail_status) -> SMTP right away."""
    name, notifications = "local", True

    def __init__(self):
        self.notes = []

    def user_email(self, user):
        return f"demo+{user}@example.org"

    def add_note(self, number, agent, text):
        self.notes.append(text)


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(notify, "PENDING", Path(self.tmp.name) / "pending.json"),
                  mock.patch.dict(os.environ, {"NOTIFY_DEMO_EMAIL": "itsm.demo@gmail.com"})):
            p.start()
            self.addCleanup(p.stop)
        self.send = mock.patch.object(notify, "send_smtp", return_value=(True, "sent to 1 recipient(s)")).start()
        self.addCleanup(mock.patch.stopall)

    def age(self, seconds):
        d = notify._load()
        for e in d.values():
            e["since"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
        notify._save(d)

    def test_plus_address(self):
        self.assertEqual(notify.plus_address("employee01"), "itsm.demo+employee01@gmail.com")
        with mock.patch.dict(os.environ, {"NOTIFY_DEMO_EMAIL": ""}):
            self.assertIsNone(notify.plus_address("employee01"))

    def test_servicenow_sent_it_no_fallback(self):
        notify.queue("INC1", ["employee01"], "s", "b", source="servicenow")
        res = notify.process(FakeSN(sent=True))
        self.assertEqual(res[0]["result"], "sent by ServiceNow")
        self.send.assert_not_called()
        self.assertEqual(notify._load(), {})

    def test_waits_for_servicenow_then_falls_back(self):
        notify.queue("INC2", ["employee01"], "s", "b", source="servicenow")
        sn = FakeSN(sent=False)
        self.assertEqual(notify.process(sn), [])              # within the grace period: keep waiting
        self.send.assert_not_called()
        self.age(notify.GRACE_S + 5)
        res = notify.process(sn)
        self.assertIn("SMTP fallback sent", res[0]["result"])
        self.send.assert_called_once_with(["demo+employee01@example.org"], "s", "b")
        self.assertIn("SMTP fallback", sn.notes[0])
        self.assertEqual(notify._load(), {})

    def test_local_desk_sends_right_away_and_only_its_own_entries(self):
        notify.queue("RITM1", ["employee01"], "s", "b", source="local")
        notify.queue("INC3", ["employee01"], "s", "b", source="servicenow")
        res = notify.process(FakeLocal())
        self.assertEqual([r["number"] for r in res], ["RITM1"])
        self.assertEqual(list(notify._load()), ["INC3|employee01"])     # the ServiceNow entry is untouched

    def test_agent_and_monitoring_callers_are_not_mailed(self):
        notify.queue("INC4", ["ai.agent", "monitoring"], "s", "b")
        self.assertEqual(notify._load(), {})

    def test_temporary_desk_never_mails(self):
        notify.queue("RITM2", ["employee01"], "s", "b", source="local")
        quiet = FakeLocal()
        quiet.notifications = False
        self.assertEqual(notify.process(quiet), [])
        self.send.assert_not_called()

    def test_missing_smtp_config_is_reported_not_retried(self):
        self.send.return_value = (False, "SMTP not configured (SMTP_HOST / SMTP_USER / SMTP_PASSWORD in .env)")
        notify.queue("RITM3", ["employee01"], "s", "b", source="local")
        res = notify.process(FakeLocal())
        self.assertIn("FAILED", res[0]["result"])
        self.assertEqual(notify._load(), {})


if __name__ == "__main__":
    unittest.main()
