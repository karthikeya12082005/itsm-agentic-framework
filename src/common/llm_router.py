"""Provider-agnostic LLM router: one interface for Gemini, Groq and Ollama, one model per agent (TODO 2.1).

    from src.common.llm_router import complete_json
    res = complete_json("understanding", prompt, MySchema, system="...", fallback=lambda: {...})
    res.data      -> MySchema instance (LLM answer or rule-based fallback) or None
    res.method    -> "llm" | "rule-based" | "none"
    res.summary() -> "groq:openai/gpt-oss-20b 0.52s" / "rule-based (groq:... 503; gemini:... timeout)"

Per-agent model map (.env, format provider:model): LLM_UNDERSTANDING, LLM_RETRIEVAL, LLM_RCA, LLM_CRITIC,
LLM_COMMUNICATION. Chain for each call:
    primary (agent's model) -> secondary (the OTHER provider) [-> Ollama, opt-in] -> rule-based fallback
- Output is parsed as JSON and validated with a pydantic schema; one repair retry on the same model when the
  JSON is invalid, then the next model in the chain.
- Per-agent timeouts plus a total budget per call: the pipeline never waits long on an LLM.
- Cooldown ("circuit breaker"): a model that answered 429/5xx/timeout is skipped for COOLDOWN_S seconds, so a
  slow or overloaded model costs at most one timeout per worker run instead of one per ticket.
- The critic must use a different model from the Understanding Agent: that model is removed from its chain.
- Every call is logged (agent, provider, model, latency, tokens, fallback) to logs/llm_calls.jsonl and to the
  "llm" logger. Prompts and keys are never logged.

Optional process environment (not .env keys; used by tests and the ablation study):
    LLM_MODE=multi (default) | single | off     single = LLM_SINGLE (provider:model) for every agent, no secondary
    LLM_SINGLE=groq:openai/gpt-oss-20b
    LLM_OLLAMA_FALLBACK=1                        add local Ollama to every chain before the rule-based fallback
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from pydantic import BaseModel, ValidationError

from src.common.config import PROJECT_ROOT

log = logging.getLogger("llm")

AGENT_VARS = {"understanding": "LLM_UNDERSTANDING", "retrieval": "LLM_RETRIEVAL", "rca": "LLM_RCA",
              "critic": "LLM_CRITIC", "communication": "LLM_COMMUNICATION"}
# used only when the .env variable is empty (same as the confirmed map, Nov 24 2026)
DEFAULT_MODELS = {"understanding": "groq:openai/gpt-oss-20b", "retrieval": "gemini:gemini-3.5-flash-lite",
                  "rca": "groq:openai/gpt-oss-120b", "critic": "gemini:gemini-3.5-flash",
                  "communication": "groq:qwen/qwen3.8-27b"}
TIMEOUTS = {"understanding": 8, "retrieval": 6, "rca": 25, "critic": 12, "communication": 8}   # seconds per attempt
BUDGET_FACTOR = 2.5            # total time for one complete_json call = timeout * factor (then rule-based)
COOLDOWN_S = 120               # skip a model this long after 429 / 5xx / timeout
CONFIG_COOLDOWN_S = 900        # ... and this long after 401 / 403 / 404 (bad key, retired model)
OLLAMA_MIN_TIMEOUT = 30        # a cold Ollama load takes 9-13 s on this laptop
OLLAMA_DEFAULT = "ollama:llama3.2:3b"
LOG_FILE = PROJECT_ROOT / "logs" / "llm_calls.jsonl"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().strip("'\"")


def secondary_for(provider: str, agent: str) -> str:
    """The fallback model on the OTHER provider."""
    if agent == "critic":                        # never the Understanding model (gpt-oss-20b), see critic rule
        return "groq:openai/gpt-oss-120b" if provider != "groq" else "gemini:" + (_env("GEMINI_MODEL_LITE") or "gemini-3.5-flash-lite")
    if provider == "groq":
        return "gemini:" + (_env("GEMINI_MODEL_LITE") or "gemini-3.5-flash-lite")
    return "groq:openai/gpt-oss-20b"


# ------------------------------------------------------------------------------------------------ errors
class ProviderError(Exception):
    """kind: transient (429/5xx/timeout/network) | config (no key, 401/403/404, 400) | output (bad JSON)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def _http_error(r: requests.Response) -> ProviderError:
    body = r.text[:300].replace("\n", " ")
    if r.status_code == 400 and "json_validate_failed" in body:          # Groq: model wrote invalid JSON
        return ProviderError("output", f"HTTP 400 json_validate_failed")
    if r.status_code == 429 or r.status_code >= 500:
        ra = r.headers.get("Retry-After")
        try:
            ra = float(ra) if ra else None
        except ValueError:
            ra = None
        return ProviderError("transient", f"HTTP {r.status_code}: {body[:120]}", ra)
    return ProviderError("config", f"HTTP {r.status_code}: {body[:160]}")


def _post(url: str, timeout: float, **kw) -> dict:
    try:
        r = requests.post(url, timeout=timeout, **kw)
    except requests.Timeout:
        raise ProviderError("transient", f"timeout after {timeout:g}s") from None
    except requests.RequestException as e:
        raise ProviderError("transient", f"{type(e).__name__}") from None
    if r.status_code != 200:
        raise _http_error(r)
    try:
        return r.json()
    except ValueError:
        raise ProviderError("transient", "non-JSON HTTP answer") from None


# ------------------------------------------------------------------------------------------------ providers
# Each caller: (model, system, user, timeout, json_schema) -> (text, tokens). Raises ProviderError.
def _call_gemini(model, system, user, timeout, json_schema):
    key = _env("GEMINI_API_KEY")
    if not key:
        raise ProviderError("config", "GEMINI_API_KEY empty")
    gc: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json"}
    if model.startswith("gemini-3"):            # keep the hidden reasoning short: these are extraction tasks
        gc["thinkingConfig"] = {"thinkingLevel": "minimal" if "lite" in model else "low"}
    body = {"contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gc}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout,
              headers={"x-goog-api-key": key}, json=body)
    try:
        parts = d["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise ProviderError("output", f"no content (finishReason="
                                      f"{(d.get('candidates') or [{}])[0].get('finishReason')})") from None
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return text, (d.get("usageMetadata") or {}).get("totalTokenCount")


def _call_groq(model, system, user, timeout, json_schema):
    key = _env("GROQ_API_KEY")
    if not key:
        raise ProviderError("config", "GROQ_API_KEY empty")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body: dict[str, Any] = {"model": model, "temperature": 0, "messages": msgs, "max_completion_tokens": 2048,
                            "response_format": {"type": "json_object"}}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    d = _post("https://api.groq.com/openai/v1/chat/completions", timeout,
              headers={"Authorization": f"Bearer {key}"}, json=body)
    try:
        text = d["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise ProviderError("output", "no choices in answer") from None
    return text, (d.get("usage") or {}).get("total_tokens")


def _call_ollama(model, system, user, timeout, json_schema):
    base = (_env("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    d = _post(base + "/api/chat", max(timeout, OLLAMA_MIN_TIMEOUT),
              json={"model": model, "stream": False, "messages": msgs, "options": {"temperature": 0},
                    "format": json_schema or "json"})
    tokens = (d.get("prompt_eval_count") or 0) + (d.get("eval_count") or 0)
    return (d.get("message") or {}).get("content", ""), tokens or None


CALLERS: dict[str, Callable] = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}
KEY_VARS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "ollama": None}


def provider_configured(provider: str) -> bool:
    var = KEY_VARS.get(provider)
    return provider in CALLERS and (var is None or bool(_env(var)))


# ------------------------------------------------------------------------------------------------ chain
def parse_spec(spec: str) -> tuple[str, str]:
    """'groq:openai/gpt-oss-20b' -> ('groq', 'openai/gpt-oss-20b'); 'ollama:llama3.2:3b' keeps the tag."""
    provider, sep, model = (spec or "").strip().partition(":")
    provider = provider.strip().lower()
    if not sep or not model.strip() or provider not in CALLERS:
        raise ValueError(f"bad model spec {spec!r} (expected provider:model, provider in {sorted(CALLERS)})")
    return provider, model.strip()


def agent_model(agent: str) -> str:
    return _env(AGENT_VARS.get(agent, "")) or DEFAULT_MODELS.get(agent, DEFAULT_MODELS["understanding"])


def agent_chain(agent: str) -> list[str]:
    """Ordered list of provider:model specs to try for this agent (rule-based fallback is implicit)."""
    mode = (_env("LLM_MODE") or "multi").lower()
    if mode == "off":
        return []
    if mode == "single":
        return [_env("LLM_SINGLE") or agent_model("understanding")]
    primary = agent_model(agent)
    chain = [primary, secondary_for(parse_spec(primary)[0], agent)]
    if _env("LLM_OLLAMA_FALLBACK") == "1":
        chain.append(OLLAMA_DEFAULT)
    if agent == "critic":                       # reflection must come from a different model
        chain = [s for s in chain if s != agent_model("understanding")]
    seen, out = set(), []
    for s in chain:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


_cooldown: dict[str, float] = {}
_failures: dict[str, int] = {}
_lock = threading.Lock()
MAX_COOLDOWN_S = 900


def _cool(spec: str, seconds: float, grow: bool = True) -> None:
    """Skip `spec` for `seconds`; repeated transient failures double the wait (120, 240, 480, max 900 s)."""
    with _lock:
        n = _failures.get(spec, 0) + 1
        _failures[spec] = n
        if grow:
            seconds = min(seconds * 2 ** (n - 1), MAX_COOLDOWN_S)
        _cooldown[spec] = time.monotonic() + seconds


def _healthy(spec: str) -> None:
    with _lock:
        _failures.pop(spec, None)


def cooling(spec: str) -> float:
    """Seconds this model is still skipped (0 = available)."""
    with _lock:
        return max(0.0, _cooldown.get(spec, 0.0) - time.monotonic())


def reset_cooldowns() -> None:
    with _lock:
        _cooldown.clear()
        _failures.clear()


def warmup(agents=None, timeout: float = 6.0) -> dict[str, str]:
    """Ping every model in the agents' chains in parallel (worker start). A model that is down or overloaded
    goes into cooldown BEFORE the first ticket, so no ticket waits for its timeout. Returns spec -> status."""
    from concurrent.futures import ThreadPoolExecutor

    specs = sorted({s for a in (agents or AGENT_VARS) for s in agent_chain(a)})

    def ping(spec):
        provider, model = parse_spec(spec)
        if not provider_configured(provider):
            return spec, "not configured"
        if cooling(spec):
            return spec, "cooling"
        t0 = time.perf_counter()
        try:
            text, _ = CALLERS[provider](model, None, 'Reply with the JSON object {"ok": true} only.', timeout, None)
            parse_json(text)
        except ProviderError as e:
            if e.kind == "output":
                return spec, "answered (not JSON)"
            _cool(spec, COOLDOWN_S if e.kind == "transient" else CONFIG_COOLDOWN_S, grow=e.kind == "transient")
            return spec, f"DOWN ({str(e)[:60]}) -> cooldown"
        except ValueError:
            return spec, "answered (not JSON)"
        _healthy(spec)
        return spec, f"ok {time.perf_counter() - t0:.1f}s"

    if not specs:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(specs))) as pool:
        return dict(pool.map(ping, specs))


# ------------------------------------------------------------------------------------------------ result
@dataclass
class LLMResult:
    agent: str
    method: str = "none"                      # llm | rule-based | none
    data: BaseModel | None = None
    provider: str | None = None
    model: str | None = None
    latency_s: float = 0.0                    # whole call, all attempts
    tokens: int | None = None
    fallback: str = "none"                    # none | secondary | ollama | rule-based | unavailable
    attempts: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.method == "llm"

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.model}" if self.provider else self.method

    def summary(self) -> str:
        if self.ok:
            fb = f", fallback={self.fallback}" if self.fallback != "none" else ""
            return f"{self.spec} {self.latency_s:.2f}s{fb}"
        why = "; ".join(f"{a['spec']} {a['error']}" for a in self.attempts if a.get("error"))
        return f"{self.method}" + (f" ({why})" if why else " (no LLM configured)")

    def record(self) -> dict:
        return {"agent": self.agent, "method": self.method, "provider": self.provider, "model": self.model,
                "latency_s": round(self.latency_s, 3), "tokens": self.tokens, "fallback": self.fallback,
                "attempts": self.attempts}


def parse_json(text: str) -> Any:
    """Parse the first JSON object in text (tolerates ```json fences and text around the object)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            raise
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj


def _schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), separators=(",", ":"))


def _write_log(rec: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")
    except OSError:
        pass                                     # logging must never break the pipeline


# ------------------------------------------------------------------------------------------------ main entry
def complete_json(agent: str, prompt: str, schema: type[BaseModel], *, system: str | None = None,
                  fallback: Callable[[], dict] | None = None, timeout: float | None = None,
                  chain: list[str] | None = None) -> LLMResult:
    """Ask the agent's model chain for JSON matching `schema`; fall back to `fallback()` (rule-based)."""
    timeout = timeout or TIMEOUTS.get(agent, 10)
    budget = timeout * BUDGET_FACTOR
    chain = agent_chain(agent) if chain is None else chain
    js = schema.model_json_schema()
    user = (f"{prompt}\n\nAnswer ONLY with one JSON object that matches this JSON schema "
            f"(no prose, no markdown):\n{_schema_text(schema)}")
    res = LLMResult(agent=agent)
    t_start = time.perf_counter()

    for idx, spec in enumerate(chain):
        try:
            provider, model = parse_spec(spec)
        except ValueError as e:
            res.attempts.append({"spec": spec, "error": str(e)})
            continue
        if not provider_configured(provider):
            res.attempts.append({"spec": spec, "error": "not configured"})
            continue
        left = cooling(spec)
        if left:
            res.attempts.append({"spec": spec, "error": f"skipped (cooldown {left:.0f}s)"})
            continue
        msg = user
        for repair in (False, True):
            remaining = budget - (time.perf_counter() - t_start)
            if remaining < 1.0:
                res.attempts.append({"spec": spec, "error": "call budget exhausted"})
                break
            t0 = time.perf_counter()
            att: dict[str, Any] = {"spec": spec, "repair": repair}
            text = ""
            try:
                text, tokens = CALLERS[provider](model, system, msg, min(timeout, remaining), js)
                att["latency_s"] = round(time.perf_counter() - t0, 3)
                data = schema.model_validate(parse_json(text))
            except ProviderError as e:
                att.update(latency_s=round(time.perf_counter() - t0, 3), error=str(e)[:160])
                res.attempts.append(att)
                if e.kind == "transient":
                    _cool(spec, min(max(e.retry_after or 0, COOLDOWN_S), 600))
                elif e.kind == "config":
                    _cool(spec, CONFIG_COOLDOWN_S, grow=False)
                if e.kind == "output" and not repair:
                    msg = user + "\n\nYour previous answer was not valid JSON. Answer again with ONLY the JSON object."
                    continue
                break
            except (ValueError, ValidationError) as e:
                err = str(e).replace("\n", " ")[:200]
                att["error"] = f"invalid output: {err}"
                res.attempts.append(att)
                if not repair:
                    msg = (user + f"\n\nYour previous answer was rejected: {err}\n"
                                  f"Previous answer: {str(text)[:500]}\n"
                                  "Answer again with ONLY a JSON object that matches the schema exactly.")
                    continue
                break
            res.attempts.append(att)
            _healthy(spec)
            res.method, res.data, res.provider, res.model, res.tokens = "llm", data, provider, model, tokens
            res.fallback = "none" if idx == 0 else ("ollama" if provider == "ollama" else "secondary")
            break
        if res.ok:
            break

    if not res.ok:
        if fallback is not None:
            try:
                res.data = schema.model_validate(fallback())
                res.method, res.fallback = "rule-based", "rule-based"
            except (ValueError, ValidationError) as e:   # a broken fallback is a bug: surface it
                log.error("rule-based fallback for %s produced invalid data: %s", agent, e)
                raise
        else:
            res.fallback = "unavailable"
    res.latency_s = time.perf_counter() - t_start

    rec = res.record()
    _write_log(rec)
    line = (f"agent={agent} provider={res.provider or '-'} model={res.model or '-'} "
            f"latency={res.latency_s:.2f}s tokens={res.tokens or '-'} fallback={res.fallback}")
    expected = res.fallback == "none" or not chain          # rules-only mode: the fallback IS the plan
    (log.info if expected else log.warning)(line)
    return res
