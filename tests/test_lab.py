"""Lab services, faults on disk and the remediation action catalogue (items 4.1 / 4.2 / 4.5). Offline: services run
in-process (FastAPI TestClient) on a temporary lab directory; no ports, no processes.

    python -m unittest tests.test_lab -v
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.lab import actions as A
from src.lab import config as C
from src.lab.service import build_app


class LabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(C, "LAB_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        settle = mock.patch.object(A, "settle", lambda *a, **k: None)
        settle.start()
        self.addCleanup(settle.stop)
        self.token = {"X-Lab-Token": C.lab_token()}

    def client(self, name):
        import logging
        c = TestClient(build_app(name))
        self.addCleanup(lambda: [h.close() for h in logging.getLogger(f"lab.{name}").handlers])
        self.addCleanup(lambda: logging.getLogger(f"lab.{name}").handlers.clear())
        return c


class Services(LabCase):
    def test_worker_disk_full(self):
        w = self.client("worker")
        self.assertTrue(w.post("/work").json()["ok"])
        spool = C.service_dir("worker") / "spool"
        (spool / "big.dat").write_bytes(b"0" * int(C.SPOOL_QUOTA_MB * 1024 * 1024))
        h = w.get("/health").json()
        self.assertEqual((h["status"], h["checks"]["disk"]), ("degraded", False))
        r = w.post("/work")
        self.assertEqual(r.status_code, 500)
        self.assertIn("No space left", r.json()["detail"])
        self.assertIn("No space left", " ".join(w.get("/logs").json()["lines"]))

    def test_orders_bad_release(self):
        o = self.client("orders-api")
        d = C.service_dir("orders-api")
        dep = C.read_json(d / "deploy.json", {})
        dep["config"]["db_pool_size"] = "ten"
        C.write_json(d / "deploy.json", dep)
        r = o.post("/work")
        self.assertEqual(r.status_code, 500)
        self.assertIn("ValueError", r.json()["detail"])
        self.assertFalse(o.get("/health").json()["checks"]["config"])

    def test_web_expired_certificate(self):
        d = C.service_dir("web")
        w = self.client("web")
        C.write_json(d / "cert.json", {"not_after": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                                       "serial": "X"})
        r = w.post("/work")
        self.assertIn("certificate expired", r.json()["detail"])
        self.assertFalse(w.get("/health").json()["checks"]["certificate"])

    def test_admin_needs_token_and_clears_cache(self):
        c = self.client("cache")
        self.assertEqual(c.post("/admin/cache/clear").status_code, 401)
        self.assertEqual(c.post("/admin/fault", json={"name": "rm -rf", "on": True}, headers=self.token).status_code, 400)
        self.assertEqual(c.post("/admin/cache/clear", headers=self.token).json(), {"removed": 0})

    def test_db_session_leak_exhausts_pool(self):
        o = self.client("orders-api")
        o.post("/admin/fault", json={"name": "db_session_leak", "on": True}, headers=self.token)
        for _ in range(5):
            o.post("/work")
        r = o.post("/work")
        self.assertIn("pool exhausted", r.json()["detail"])
        self.assertEqual(o.post("/admin/db/terminate_sessions", headers=self.token).json()["terminated"], 5)
        self.assertEqual(o.get("/metrics").json()["pool_in_use"], 0)


class VpnGateway(LabCase):
    """6.1: the three VPN failure modes fail a remote session BEFORE it reaches web (no network calls needed)."""

    def test_expired_profile(self):
        g = self.client("vpn-gateway")
        d = C.service_dir("vpn-gateway")
        C.write_json(d / "profile.json", {"not_after": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                                          "serial": "VPN-X"})
        r = g.post("/work")
        self.assertEqual(r.status_code, 500)
        self.assertIn("VPN authentication failed", r.json()["detail"])
        self.assertFalse(g.get("/health").json()["checks"]["vpn_profile"])

    def test_tunnel_down_runtime_fault(self):
        g = self.client("vpn-gateway")
        g.post("/admin/fault", json={"name": "tunnel_down", "on": True}, headers=self.token)
        self.assertIn("IPsec tunnel", g.post("/work").json()["detail"])
        h = g.get("/health").json()
        self.assertFalse(h["checks"]["tunnel"])
        self.assertTrue(h["checks"]["dns"])

    def test_stale_dns_cache(self):
        g = self.client("vpn-gateway")
        d = C.service_dir("vpn-gateway")
        dns = C.read_json(d / "dns.json", {})
        dns["cache"][C.INTERNAL_HOST] = {"addr": None}
        C.write_json(d / "dns.json", dns)
        self.assertIn("NXDOMAIN", g.post("/work").json()["detail"])
        self.assertFalse(g.get("/metrics").json()["dns_ok"])


class ActionCatalogue(LabCase):
    def test_vpn_actions_only_on_the_gateway(self):
        with self.assertRaises(A.ActionError):
            A.execute("UPDATE_DNS_RECORD", "web", "INC1", "AUTH")

    def test_update_dns_record_and_restore(self):
        self.client("vpn-gateway")                       # creates the default state files
        d = C.service_dir("vpn-gateway")
        bad = {"zone": {C.INTERNAL_HOST: C.INTERNAL_ADDR}, "cache": {C.INTERNAL_HOST: {"addr": None}}}
        C.write_json(d / "dns.json", bad)
        rec = A.execute("UPDATE_DNS_RECORD", "vpn-gateway", "INC4", "AUTH")
        self.assertEqual(rec["status"], "SUCCEEDED")
        self.assertEqual(C.read_json(d / "dns.json", {})["cache"], {})
        A.rollback(rec["action_id"], "INC4")              # restores the recorded before-state
        self.assertEqual(C.read_json(d / "dns.json", {}), bad)

    def test_provision_vpn_profile_and_restore(self):
        self.client("vpn-gateway")
        d = C.service_dir("vpn-gateway")
        old = {"not_after": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "serial": "VPN-OLD"}
        C.write_json(d / "profile.json", old)
        rec = A.execute("PROVISION_VPN_PROFILE", "vpn-gateway", "INC5", "AUTH")
        new = C.read_json(d / "profile.json", {})
        self.assertGreater(datetime.fromisoformat(new["not_after"]), datetime.now(timezone.utc))
        A.rollback(rec["action_id"], "INC5")
        self.assertEqual(C.read_json(d / "profile.json", {}), old)

    def test_unknown_action_refused(self):
        with self.assertRaises(A.ActionError):
            A.execute("DROP_DATABASE", "orders-api", "INC1", "AUTH")
        with self.assertRaises(A.ActionError):
            A.execute("RESTART_SERVICE", "mainframe", "INC1", "AUTH")

    def test_rollback_deployment_and_its_rollback(self):
        self.client("orders-api")                        # creates deploy.json
        d = C.service_dir("orders-api")
        from src.lab import faults
        with mock.patch.object(faults, "traffic", lambda *a: None):
            faults.inject("bad_config")
        rec = A.execute("ROLLBACK_DEPLOYMENT", "orders-api", "INC1", "AUTH-1")
        self.assertEqual((rec["status"], C.read_json(d / "deploy.json", {})["version"]), ("SUCCEEDED", "1.0.0"))
        A.rollback(rec["action_id"], "INC1")             # re-deploys the removed release
        self.assertEqual(C.read_json(d / "deploy.json", {})["version"], "1.1.0")
        with self.assertRaises(A.ActionError):
            A.rollback(rec["action_id"], "INC1")         # only once

    def test_renew_certificate_and_restore(self):
        self.client("web")
        d = C.service_dir("web")
        old = C.read_json(d / "cert.json", {})
        rec = A.execute("RENEW_CERTIFICATE", "web", "INC2", "AUTH-2")
        self.assertNotEqual(C.read_json(d / "cert.json", {})["serial"], old["serial"])
        A.rollback(rec["action_id"], "INC2")
        self.assertEqual(C.read_json(d / "cert.json", {}), old)

    def test_free_disk_space_and_restore(self):
        self.client("worker")
        spool = C.service_dir("worker") / "spool"
        for i in range(12):
            (spool / f"f{i:02d}.dat").write_bytes(b"x")
        rec = A.execute("FREE_DISK_SPACE", "worker", "INC3", "AUTH-3")
        self.assertEqual(len(list(spool.glob("*"))), 5)                       # newest kept
        A.rollback(rec["action_id"], "INC3")
        self.assertEqual(len(list(spool.glob("*"))), 12)
        self.assertTrue(all(r["ticket_id"] in ("INC3",) for r in A.records()))


if __name__ == "__main__":
    unittest.main()
