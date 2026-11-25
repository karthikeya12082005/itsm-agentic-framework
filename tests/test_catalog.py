"""Catalogue consistency (item 3.8): agents' catalogue <-> dataset policies <-> endpoint service lists.

    python -m unittest tests.test_catalog -v
"""
import unittest

from endpoint_service.endpoint_config import APPROVED_PACKAGES, MANAGED_DOMAINS, PACKAGE_PROCESSES
from src.access import agents
from src.access.catalog import APPROVER_GROUPS, APPROVER_USERS, TARGETS


class CatalogueConsistency(unittest.TestCase):
    def test_every_target_has_a_dataset_policy(self):
        for target, info in TARGETS.items():
            self.assertIn(info["policy_id"], agents.POLICIES, target)

    def test_endpoint_can_enforce_every_grantable_target(self):
        for target in TARGETS:
            d = agents.decide_for(target, "DEVELOPER")["decision"]
            kind = TARGETS[target]["kind"]
            if kind == "domain":
                self.assertIn(target, MANAGED_DOMAINS, target)          # also DENY domains: blocked by default
            elif d in ("ALLOW", "ALLOW_WITH_APPROVAL"):
                self.assertIn(target, APPROVED_PACKAGES, target)
                self.assertIn(target, PACKAGE_PROCESSES, target)
            else:
                self.assertNotIn(target, APPROVED_PACKAGES, f"{target} is {d} but installable")

    def test_level1_decisions(self):
        expected = {
            "dropbox.com": "DENY", "chat.deepseek.com": "DENY", "deepseek.com": "DENY",
            "stackoverflow.com": "ALLOW_WITH_APPROVAL", "docs.python.org": "ALLOW_WITH_APPROVAL",
            "huggingface.co": "ALLOW_WITH_APPROVAL", "kaggle.com": "ALLOW_WITH_APPROVAL",
            "colab.research.google.com": "ALLOW_WITH_APPROVAL",
            "Zoom.Zoom": "ALLOW", "SlackTechnologies.Slack": "ALLOW", "Microsoft.Teams": "ALLOW",
            "Notepad++.Notepad++": "ALLOW", "7zip.7zip": "ALLOW",
            "qBittorrent.qBittorrent": "DENY", "uTorrent": "DENY",
        }
        for target, decision in expected.items():
            self.assertEqual(agents.decide_for(target, "DEVELOPER")["decision"], decision, target)
        self.assertEqual(agents.decide_for("huggingface.co", "DEVELOPER")["approver_role"], "AI_GOVERNANCE_BOARD")
        self.assertEqual(agents.decide_for("stackoverflow.com", "DEVELOPER")["approver_role"], "SECURITY_TEAM")

    def test_every_approver_role_in_the_dataset_has_a_group(self):
        roles = {p["approver_role"] for p in agents.POLICIES.values() if p.get("approver_role")}
        self.assertEqual(roles - {"LINE_MANAGER"} - set(APPROVER_GROUPS), set())
        self.assertEqual(set(APPROVER_GROUPS.values()), set(APPROVER_USERS))

    def test_level1_sentences(self):
        cases = {
            "Please install Zoom for client meetings": "Zoom.Zoom",
            "Can I get Slack installed? Our team chats there": "SlackTechnologies.Slack",
            "I need Microsoft Teams for the weekly call": "Microsoft.Teams",
            "please install notepad++": "Notepad++.Notepad++",
            "I need 7-Zip to open a .7z archive": "7zip.7zip",
            "install qBittorrent so I can download Linux ISOs": "qBittorrent.qBittorrent",
            "I need uTorrent": "uTorrent",
            "I need access to stackoverflow.com for debugging help": "stackoverflow.com",
            "please unblock the Python documentation": "docs.python.org",
            "I need Hugging Face to download a model": "huggingface.co",
            "Requesting Kaggle access for a dataset": "kaggle.com",
            "I need Google Colab for GPU notebooks": "colab.research.google.com",
            "Can I use DeepSeek for coding": "chat.deepseek.com",
            "I need dropbox.com to share photos": "dropbox.com",
            "our teams need a shared drive": None,
        }
        for text, target in cases.items():
            self.assertEqual(agents.rule_target(text), target, text)


if __name__ == "__main__":
    unittest.main()
