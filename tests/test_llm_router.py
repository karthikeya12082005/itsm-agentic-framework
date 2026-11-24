"""Offline tests for src/common/llm_router.py (fake providers, no network)."""
import logging
import os
import unittest
from unittest import mock

from pydantic import BaseModel

from src.common import llm_router as R


class Target(BaseModel):
    target: str
    kind: str = "domain"


ENV = {"GEMINI_API_KEY": "x" * 10, "GROQ_API_KEY": "y" * 10, "GEMINI_MODEL_LITE": "gemini-lite",
       "LLM_UNDERSTANDING": "groq:small", "LLM_RETRIEVAL": "gemini:gemini-lite", "LLM_RCA": "groq:big",
       "LLM_CRITIC": "gemini:gemini-flash", "LLM_COMMUNICATION": "groq:qwen", "LLM_MODE": "",
       "LLM_SINGLE": "", "LLM_OLLAMA_FALLBACK": ""}


class FakeProviders:
    """Scripted answers per provider:model; each entry is a string (answer) or a ProviderError."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def caller(self, provider):
        def call(model, system, user, timeout, js):
            spec = f"{provider}:{model}"
            self.calls.append((spec, user))
            answers = self.script.get(spec) or [R.ProviderError("config", "no script")]
            ans = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(ans, Exception):
                raise ans
            return ans, 42
        return call

    def patch(self):
        return mock.patch.dict(R.CALLERS, {p: self.caller(p) for p in ("gemini", "groq", "ollama")})


class EnvCase(unittest.TestCase):
    """Fake keys + agent map, no log file, clean cooldowns (no test methods here)."""
    def setUp(self):
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.log = mock.patch.object(R, "_write_log")
        self.logged = self.log.start()
        R.reset_cooldowns()
        logging.getLogger("llm").setLevel(logging.CRITICAL)      # fallback warnings are expected here

    def tearDown(self):
        self.log.stop()
        self.env.stop()
        R.reset_cooldowns()

    def run_call(self, script, agent="understanding", fallback=None, **kw):
        fake = FakeProviders(script)
        with fake.patch():
            res = R.complete_json(agent, "find the target", Target, fallback=fallback, **kw)
        return res, fake



class RouterTest(EnvCase):
    # ---------------------------------------------------------------- chain construction
    def test_parse_spec(self):
        self.assertEqual(R.parse_spec("groq:openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b"))
        self.assertEqual(R.parse_spec("ollama:llama3.2:3b"), ("ollama", "llama3.2:3b"))
        for bad in ("", "groq", "nope:model", "groq:"):
            with self.assertRaises(ValueError):
                R.parse_spec(bad)

    def test_chain_primary_then_other_provider(self):
        self.assertEqual(R.agent_chain("understanding"), ["groq:small", "gemini:gemini-lite"])
        self.assertEqual(R.agent_chain("retrieval"), ["gemini:gemini-lite", "groq:openai/gpt-oss-20b"])

    def test_critic_never_uses_understanding_model(self):
        with mock.patch.dict(os.environ, {"LLM_CRITIC": "groq:small"}):
            chain = R.agent_chain("critic")
        self.assertNotIn("groq:small", chain)
        self.assertTrue(chain)
        self.assertNotIn(R.agent_model("understanding"), R.agent_chain("critic"))

    def test_modes(self):
        with mock.patch.dict(os.environ, {"LLM_MODE": "off"}):
            self.assertEqual(R.agent_chain("rca"), [])
        with mock.patch.dict(os.environ, {"LLM_MODE": "single", "LLM_SINGLE": "groq:big"}):
            self.assertEqual(R.agent_chain("communication"), ["groq:big"])
        with mock.patch.dict(os.environ, {"LLM_OLLAMA_FALLBACK": "1"}):
            self.assertEqual(R.agent_chain("understanding")[-1], R.OLLAMA_DEFAULT)

    def test_empty_env_uses_default_map(self):
        with mock.patch.dict(os.environ, {"LLM_RCA": ""}):
            self.assertEqual(R.agent_model("rca"), R.DEFAULT_MODELS["rca"])

    # ---------------------------------------------------------------- calls
    def test_primary_answers(self):
        res, fake = self.run_call({"groq:small": ['{"target": "github.com"}']})
        self.assertTrue(res.ok)
        self.assertEqual((res.provider, res.model, res.fallback, res.tokens), ("groq", "small", "none", 42))
        self.assertEqual(res.data.target, "github.com")
        self.assertEqual(len(fake.calls), 1)
        self.logged.assert_called_once()
        self.assertEqual(self.logged.call_args[0][0]["agent"], "understanding")

    def test_fenced_and_wrapped_json(self):
        res, _ = self.run_call({"groq:small": ['Sure!\n```json\n{"target": "a.com"}\n```']})
        self.assertTrue(res.ok)
        res, _ = self.run_call({"groq:small": ['Here: {"target": "b.com"} done']})
        self.assertEqual(res.data.target, "b.com")

    def test_invalid_json_repaired_on_same_model(self):
        res, fake = self.run_call({"groq:small": ['not json', '{"target": "c.com"}']})
        self.assertTrue(res.ok)
        self.assertEqual(res.fallback, "none")
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("rejected", fake.calls[1][1])

    def test_schema_violation_repair_then_secondary(self):
        res, fake = self.run_call({"groq:small": ['{"wrong": 1}'], "gemini:gemini-lite": ['{"target": "d.com"}']})
        self.assertTrue(res.ok)
        self.assertEqual((res.provider, res.fallback), ("gemini", "secondary"))
        self.assertEqual([c[0] for c in fake.calls], ["groq:small", "groq:small", "gemini:gemini-lite"])

    def test_timeout_falls_back_and_cools_down(self):
        script = {"groq:small": [R.ProviderError("transient", "timeout after 8s")],
                  "gemini:gemini-lite": ['{"target": "e.com"}']}
        res, fake = self.run_call(script)
        self.assertEqual((res.provider, res.fallback), ("gemini", "secondary"))
        self.assertGreater(R.cooling("groq:small"), 0)
        res2, fake2 = self.run_call(script)                 # second call skips the cooling model entirely
        self.assertEqual([c[0] for c in fake2.calls], ["gemini:gemini-lite"])
        self.assertIn("cooldown", res2.attempts[0]["error"])

    def test_all_fail_uses_rule_based_fallback(self):
        script = {"groq:small": [R.ProviderError("transient", "HTTP 503")],
                  "gemini:gemini-lite": [R.ProviderError("transient", "HTTP 429")]}
        res, _ = self.run_call(script, fallback=lambda: {"target": "rules.com"})
        self.assertEqual((res.method, res.fallback, res.data.target), ("rule-based", "rule-based", "rules.com"))
        self.assertFalse(res.ok)
        self.assertIn("HTTP 503", res.summary())

    def test_no_keys_no_network(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GROQ_API_KEY": ""}):
            res, fake = self.run_call({}, fallback=lambda: {"target": "rules.com"})
        self.assertEqual(fake.calls, [])
        self.assertEqual(res.method, "rule-based")
        self.assertTrue(all(a["error"] == "not configured" for a in res.attempts))

    def test_no_fallback_returns_none(self):
        with mock.patch.dict(os.environ, {"LLM_MODE": "off"}):
            res, _ = self.run_call({})
        self.assertIsNone(res.data)
        self.assertEqual((res.method, res.fallback), ("none", "unavailable"))

    def test_broken_fallback_raises(self):
        with mock.patch.dict(os.environ, {"LLM_MODE": "off"}):
            with self.assertRaises(Exception):
                self.run_call({}, fallback=lambda: {"nope": 1})

    def test_config_error_moves_on(self):
        script = {"groq:small": [R.ProviderError("config", "HTTP 404 model retired")],
                  "gemini:gemini-lite": ['{"target": "f.com"}']}
        res, _ = self.run_call(script)
        self.assertEqual(res.provider, "gemini")
        self.assertGreater(R.cooling("groq:small"), R.COOLDOWN_S)

    def test_budget_limits_total_time(self):
        clock = iter([0.0, 0.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
        with mock.patch.object(R.time, "perf_counter", lambda: next(clock, 30.0)):
            res, fake = self.run_call({"groq:small": [R.ProviderError("transient", "timeout")]},
                                      fallback=lambda: {"target": "rules.com"})
        self.assertEqual(res.method, "rule-based")
        self.assertEqual(len(fake.calls), 1)               # secondary not tried: budget (8 s * 2.5) is spent


class WarmupTest(EnvCase):
    def test_warmup_cools_down_failing_models_before_first_ticket(self):
        fake = FakeProviders({"groq:small": ['{"ok": true}'],
                              "gemini:gemini-flash": [R.ProviderError("transient", "HTTP 503")]})
        with fake.patch():
            status = R.warmup(["understanding", "critic"])
        self.assertTrue(status["groq:small"].startswith("ok"))
        self.assertIn("DOWN", status["gemini:gemini-flash"])
        self.assertGreater(R.cooling("gemini:gemini-flash"), 0)
        self.assertEqual(R.cooling("groq:small"), 0)

    def test_cooldown_grows_on_repeated_failures_and_resets_on_success(self):
        R._cool("groq:small", 100)
        first = R.cooling("groq:small")
        R._cool("groq:small", 100)
        self.assertGreater(R.cooling("groq:small"), first + 50)          # 100 s, then 200 s
        R._healthy("groq:small")
        R._cool("groq:small", 100)
        self.assertLessEqual(R.cooling("groq:small"), 100.5)


class HttpErrorTest(unittest.TestCase):
    def resp(self, code, text="", headers=None):
        r = mock.Mock(status_code=code, text=text, headers=headers or {})
        return r

    def test_classification(self):
        self.assertEqual(R._http_error(self.resp(503)).kind, "transient")
        e = R._http_error(self.resp(429, headers={"Retry-After": "7"}))
        self.assertEqual((e.kind, e.retry_after), ("transient", 7.0))
        self.assertEqual(R._http_error(self.resp(404)).kind, "config")
        self.assertEqual(R._http_error(self.resp(400, '{"code":"json_validate_failed"}')).kind, "output")


if __name__ == "__main__":
    unittest.main()
