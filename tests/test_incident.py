"""Incident flow (Phase 4.4 / 4.5) offline: Bayesian ranking, root-service localisation, and the full graph on a
FAKE lab (diagnostic tools, remediation actions and probes patched; no processes, no network, no LLM).

    python -m unittest tests.test_incident -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.access.ticket_source import LocalSource
from src.access.worker import approve_ticket, process_ticket
from src.common import llm_router
from src.incident import agents as IA
from src.incident import bayes, evidence, tools
from src.orchestrator import checkpoint as cp
from src.orchestrator.graph import build_graph

HEALTHY = {"status": "ok", "up": True, "failing": []}


class Bayes(unittest.TestCase):
    def test_prior_is_a_distribution(self):
        p = bayes.category_prior("APPLICATION")
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)
        self.assertEqual(set(p), set(bayes.classes()))

    def test_evidence_moves_the_posterior(self):
        cert = bayes.rank({"cert_expired": True, "tls_error_in_logs": True, "high_error_rate": True}, "APPLICATION")
        self.assertEqual(cert[0][0], "CERTIFICATE_EXPIRY")
        disk = bayes.rank({"disk_full": True, "enospc_in_logs": True}, "INFRASTRUCTURE")
        self.assertEqual(disk[0][0], "CAPACITY_LIMIT")
        self.assertAlmostEqual(sum(p for _, p in cert), 1.0, places=6)

    def test_absent_evidence_counts(self):
        with_cert = dict(bayes.rank({"cert_expired": True}, "APPLICATION"))
        without = dict(bayes.rank({"cert_expired": False}, "APPLICATION"))
        self.assertGreater(with_cert["CERTIFICATE_EXPIRY"], without["CERTIFICATE_EXPIRY"])

    def test_llm_hypothesis_is_a_noisy_observation(self):
        base = dict(bayes.rank({"high_error_rate": True}, "APPLICATION"))
        llm = dict(bayes.rank({"high_error_rate": True}, "APPLICATION", llm_hypothesis="DEPLOYMENT_CHANGE"))
        self.assertGreater(llm["DEPLOYMENT_CHANGE"], base["DEPLOYMENT_CHANGE"])
        self.assertLess(llm["DEPLOYMENT_CHANGE"], 1.0)
        bogus = dict(bayes.rank({"high_error_rate": True}, "APPLICATION", llm_hypothesis="NOT_A_CLASS"))
        self.assertEqual(bogus, base)                                    # unknown labels are ignored

    def test_knn_blends_into_the_prior(self):
        p = dict(bayes.rank({}, "APPLICATION", knn_root_causes=["CERTIFICATE_EXPIRY"] * 5))
        q = dict(bayes.rank({}, "APPLICATION"))
        self.assertGreater(p["CERTIFICATE_EXPIRY"], q["CERTIFICATE_EXPIRY"])


class RootLocalisation(unittest.TestCase):
    def h(self, failing=(), up=True):
        return {"up": up, "status": "ok" if not failing and up else ("down" if not up else "degraded"),
                "failing": list(failing)}

    def test_chains(self):
        ok = self.h()
        self.assertIsNone(evidence.locate_root({"web": ok, "orders-api": ok, "cache": ok, "worker": ok}))
        latency = {"web": self.h(["latency"]), "orders-api": self.h(["latency"]), "cache": self.h(["latency"]),
                   "worker": ok}
        self.assertEqual(evidence.locate_root(latency), "cache")
        dep_down = {"web": self.h(["errors", "dependency:orders-api"]), "orders-api": self.h(up=False),
                    "cache": ok, "worker": ok}
        self.assertEqual(evidence.locate_root(dep_down), "orders-api")
        cert = {"web": self.h(["errors", "certificate"]), "orders-api": ok, "cache": ok, "worker": ok}
        self.assertEqual(evidence.locate_root(cert), "web")
        bad_release = {"web": self.h(["errors"]), "orders-api": self.h(["errors", "config", "db_pool"]),
                       "cache": ok, "worker": ok}
        self.assertEqual(evidence.locate_root(bad_release), "orders-api")


class EvidenceFeatures(unittest.TestCase):
    def test_deployment_zero_minutes_ago_counts_as_recent(self):     # 4.6 eval bug: 0.0 was treated as "none"
        obs = {("recent_deployments", "orders-api"): {"releases": ["1.0.0", "1.1.0"], "last_deploy_minutes_ago": 0.0}}
        self.assertTrue(evidence.features(obs, "orders-api", "web")["recent_deployment"])
        obs = {("recent_deployments", "orders-api"): {"releases": ["1.0.0"], "last_deploy_minutes_ago": None}}
        self.assertFalse(evidence.features(obs, "orders-api", "web")["recent_deployment"])

    def test_oom_line_is_not_also_a_generic_crash(self):
        obs = {("read_logs", "orders-api"): {"lines": ["ERROR orders-api FATAL: OutOfMemoryError - heap exhausted"]}}
        f = evidence.features(obs, "orders-api", "web")
        self.assertEqual((f["oom_in_logs"], f["crash_in_logs"]), (True, False))


class FakeLab:
    """Scenario-driven observations; `fixed` flips to healthy after the right action."""

    def __init__(self, root, symptom_health, metrics=None, logs=(), deploys=None, cert_days=90.0, fix_action=None):
        self.root, self.sym, self.metrics, self.logs = root, symptom_health, metrics or {}, list(logs)
        self.deploys, self.cert_days, self.fix_action = deploys, cert_days, fix_action
        self.fixed, self.executed, self.rolled_back = False, [], []

    def health(self, svc):
        if self.fixed or svc not in self.sym:
            return dict(HEALTHY, service=svc)
        up = self.sym[svc] != "DOWN"
        return {"service": svc, "up": up, "status": "degraded" if up else "down",
                "failing": [] if not up else self.sym[svc]}

    def run(self, tool, svc):
        if tool == "check_health":
            return self.health(svc)
        if tool == "process_status":
            return {"service": svc, "up": self.health(svc)["up"]}
        if tool in ("read_metrics", "check_db_pool"):
            return {"service": svc, **(self.metrics if svc == self.root and not self.fixed else {})}
        if tool == "read_logs":
            return {"service": svc, "lines": self.logs if svc == self.root else []}
        if tool == "recent_deployments":
            return self.deploys or {"service": svc, "releases": ["1.0.0"], "last_deploy_minutes_ago": None}
        if tool == "check_certificate":
            return {"service": svc, "days_left": self.cert_days if svc == self.root and not self.fixed else 90.0}
        if tool == "check_disk":
            return {"service": svc, "used_pct": self.metrics.get("disk_used_pct", 1.0) if svc == self.root else 1.0}
        return {}

    def execute(self, action, service, ticket, auth):
        self.executed.append((action, service))
        if action == self.fix_action and service == self.root:
            self.fixed = True
        return {"action_id": f"L{len(self.executed)}", "status": "SUCCEEDED", "detail": "fake",
                "rollback": {"action": "X", "params": {}} if action in ("ROLLBACK_DEPLOYMENT", "RENEW_CERTIFICATE",
                                                                         "FREE_DISK_SPACE") else None}

    def rollback(self, action_id, ticket):
        self.rolled_back.append(action_id)
        return {"action_id": f"R-{action_id}", "status": "SUCCEEDED"}


SCENARIOS = {
    "memory_leak": dict(root="orders-api", symptom_health={"orders-api": ["memory"]},
                        metrics={"memory_mb": 760, "error_rate": 0.0}, fix_action="RESTART_SERVICE",
                        logs=["WARNING orders-api heap usage 760 MB and growing (allocations not released)"]),
    "cert_expired": dict(root="web", symptom_health={"web": ["errors", "certificate"]}, cert_days=-2.0,
                         metrics={"error_rate": 1.0}, fix_action="RENEW_CERTIFICATE",
                         logs=["ERROR web request failed: RuntimeError: TLS handshake failed: server certificate "
                               "expired 2.0 days ago"]),
}


class IncidentFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = LocalSource(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.src.store.db.close)
        for p in (mock.patch.dict(os.environ, {"LLM_MODE": "off"}), mock.patch.object(llm_router, "_write_log"),
                  mock.patch.object(IA.monitor, "probe", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.saver = cp.memory_saver()
        self.graph = build_graph(None, checkpointer=self.saver)

    def run_incident(self, lab, category="internal_applications"):
        for p in (mock.patch.object(tools, "run", side_effect=lab.run),
                  mock.patch.object(tools, "check_health", side_effect=lab.health),
                  mock.patch.object(IA.lab_actions, "execute", side_effect=lab.execute),
                  mock.patch.object(IA.lab_actions, "rollback", side_effect=lab.rollback)):
            p.start()
            self.addCleanup(p.stop)
        n = self.src.create_incident("[lab-monitor] web degraded", f"Affected service: web\nSymptoms: {lab.sym}",
                                     category)
        return n, process_ticket(self.src, self.graph, n)

    def values(self, n):
        return cp.values(self.saver, self.src.thread_id(n))

    def test_memory_leak_resolved_automatically(self):
        lab = FakeLab(**SCENARIOS["memory_leak"])
        n, final = self.run_incident(lab)
        v = self.values(n)
        self.assertEqual(v["flow"], "incident")
        self.assertEqual(v["incident"]["rca"]["root_service"], "orders-api")
        self.assertEqual(v["incident"]["rca"]["ranking"][0][0], "RESOURCE_EXHAUSTION")
        self.assertEqual((v["policy"]["decision"], v["policy"]["policy_id"]), ("ALLOW", "POL-0046"))
        self.assertEqual(final, "RESOLVED")
        self.assertEqual(lab.executed, [("RESTART_SERVICE", "orders-api")])
        agents_seen = [s["agent"] for s in v["trace"]]
        for a in ("Triage Agent", "RCA Agent", "Policy & Risk Agent", "Critic Agent", "Execution Agent",
                  "Verification Agent", "Communication Agent"):
            self.assertIn(a, agents_seen)

    def test_certificate_needs_it_service_owner_approval(self):
        lab = FakeLab(**SCENARIOS["cert_expired"])
        n, final = self.run_incident(lab)
        v = self.values(n)
        self.assertEqual(v["incident"]["rca"]["ranking"][0][0], "CERTIFICATE_EXPIRY")
        self.assertEqual((final, v["policy"]["approver_role"]), ("AWAITING_APPROVAL", "IT_SERVICE_OWNER"))
        self.assertEqual(lab.executed, [])
        self.assertFalse(approve_ticket(self.src, self.graph, n, "security01")[0])
        ok, why = approve_ticket(self.src, self.graph, n, "serviceowner01")
        self.assertTrue(ok, why)
        self.assertEqual(self.src.get(n)["state"], "RESOLVED")
        self.assertEqual(lab.executed, [("RENEW_CERTIFICATE", "web")])

    def test_unverified_fix_is_rolled_back_then_escalated(self):
        lab = FakeLab(**{**SCENARIOS["cert_expired"], "fix_action": "NOTHING_FIXES_IT"})
        n, _ = self.run_incident(lab)
        ok, _ = approve_ticket(self.src, self.graph, n, "serviceowner01")
        self.assertTrue(ok)
        t, v = self.src.get(n), self.values(n)
        self.assertEqual(t["state"], "ESCALATED")                          # never a fake RESOLVED
        self.assertEqual(v["attempts"], IA.MAX_ATTEMPTS)
        self.assertEqual(lab.rolled_back, ["L1", "L2"])                      # each failed attempt undone

    def test_weak_evidence_escalates_without_action(self):
        lab = FakeLab(root="web", symptom_health={"web": ["errors"]}, metrics={"error_rate": 0.5})
        n, final = self.run_incident(lab)
        v = self.values(n)
        self.assertEqual((final, v["policy"]["decision"]), ("ESCALATED", "ESCALATE_FOR_REVIEW"))
        self.assertEqual(lab.executed, [])

    def test_incident_approval_without_checkpoint_is_refused(self):
        lab = FakeLab(**SCENARIOS["cert_expired"])
        n, _ = self.run_incident(lab)
        cp.delete(self.saver, self.src.thread_id(n))
        ok, why = approve_ticket(self.src, self.graph, n, "serviceowner01")
        self.assertFalse(ok)
        self.assertIn("checkpoint", why)


if __name__ == "__main__":
    unittest.main()
