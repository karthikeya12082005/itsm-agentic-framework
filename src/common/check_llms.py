"""Ping every configured LLM provider and check that it returns valid JSON.

    python -m src.common.check_llms                      # all providers
    python -m src.common.check_llms --provider gemini    # one provider (gemini | groq | ollama)
    python -m src.common.check_llms --list               # also list every model the provider offers
    python -m src.common.check_llms --agents             # per-agent model map from .env, through llm_router

For each provider: lists the models the account can use RIGHT NOW (model IDs change often, so they are
never hard-coded here), picks the model to test (from .env if set, otherwise a small default from the
live list), sends one JSON prompt and reports the parsed answer and the latency.
API keys are read from .env and never printed.
"""
import argparse
import json
import os
import time

import requests

from src.common import config  # noqa: F401  (loads .env)

PROMPT = ('Classify this IT request and answer ONLY with JSON of the form '
          '{"request_kind": "website_access" | "software_install" | "other", "target": "<domain or software>"}. '
          'Request: "Please give me access to gemini.google.com for writing documentation."')
TIMEOUT = 30
AGENT_VARS = ["LLM_UNDERSTANDING", "LLM_RETRIEVAL", "LLM_RCA", "LLM_CRITIC", "LLM_COMMUNICATION"]


def _env(name):
    return os.environ.get(name, "").strip().strip("'\"")


def _models_from_agent_map(provider):
    """Models assigned to agents in .env (format provider:model) for this provider."""
    out = []
    for var in AGENT_VARS:
        val = _env(var)
        if val.lower().startswith(provider + ":"):
            out.append(val.split(":", 1)[1])
    return out


# Models that cannot answer a chat/JSON prompt (speech, images, safety classifiers, ...)
NON_CHAT = ("guard", "safeguard", "whisper", "orpheus", "tts", "transcribe", "image", "lyria", "robotics",
            "embedding", "computer-use", "deep-research", "nano-banana", "antigravity")


def is_chat_model(name):
    return not any(word in name.lower() for word in NON_CHAT)


def _pick(available, preferred, hints):
    """First preferred model that exists, else the first chat model matching a hint, else the first chat model."""
    for m in preferred:
        if m in available:
            return m
    chat = [m for m in available if is_chat_model(m)]
    for hint in hints:
        for m in chat:
            if all(h in m.lower() for h in hint):
                return m
    return chat[0] if chat else None


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):                     # tolerate ```json fences
        text = text.strip("`").split("\n", 1)[-1]
    return json.loads(text)


# ------------------------------------------------------------------------------------------------ providers
def gemini_models():
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=TIMEOUT,
                     headers={"x-goog-api-key": _env("GEMINI_API_KEY")}, params={"pageSize": 1000})
    r.raise_for_status()
    return sorted(m["name"].removeprefix("models/") for m in r.json().get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", []))


def gemini_call(model):
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      timeout=TIMEOUT, headers={"x-goog-api-key": _env("GEMINI_API_KEY")},
                      json={"contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
                            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def groq_models():
    r = requests.get("https://api.groq.com/openai/v1/models", timeout=TIMEOUT,
                     headers={"Authorization": f"Bearer {_env('GROQ_API_KEY')}"})
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []) if m.get("active", True))


def groq_call(model):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {_env('GROQ_API_KEY')}"},
                      json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": PROMPT}]})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _ollama_base():
    return (_env("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")


def ollama_models():
    r = requests.get(_ollama_base() + "/api/tags", timeout=10)
    r.raise_for_status()
    return sorted(m["name"] for m in r.json().get("models", []))


def ollama_call(model):
    r = requests.post(_ollama_base() + "/api/chat", timeout=120,       # first call loads the model into RAM
                      json={"model": model, "stream": False, "format": "json", "options": {"temperature": 0},
                            "messages": [{"role": "user", "content": PROMPT}]})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["message"]["content"]


PROVIDERS = {
    # name: (is configured?, list models, call, preferred models from .env, fallback name hints)
    "gemini": (lambda: bool(_env("GEMINI_API_KEY")), gemini_models, gemini_call,
               lambda: [_env("GEMINI_MODEL_LITE")] + _models_from_agent_map("gemini"),
               [("flash-lite",), ("flash",)]),
    "groq": (lambda: bool(_env("GROQ_API_KEY")), groq_models, groq_call,
             lambda: _models_from_agent_map("groq"),
             [("llama", "instant"), ("llama",), ("gpt-oss-20b",), ("qwen",)]),
    "ollama": (lambda: True, ollama_models, ollama_call,
               lambda: _models_from_agent_map("ollama"),
               [("llama",), ("qwen",), ("gemma",), ("phi",)]),
}
MISSING_HINT = {"gemini": "GEMINI_API_KEY is empty in .env",
                "groq": "GROQ_API_KEY is empty in .env",
                "ollama": "Ollama is not running / not installed (OLLAMA_BASE_URL)"}


def check(name, show_list=False):
    configured, list_models, call, preferred, hints = PROVIDERS[name]
    print(f"\n=== {name}")
    if not configured():
        print(f"  SKIPPED: {MISSING_HINT[name]}")
        return None
    try:
        available = list_models()
    except requests.RequestException as e:
        print(f"  NOT REACHABLE: {MISSING_HINT[name] if name == 'ollama' else type(e).__name__ + ': ' + str(e)[:200]}")
        return False
    print(f"  models available: {len(available)}")
    if show_list:
        for m in available:
            print(f"    - {m}")
    if not available:
        print("  FAIL: the provider returned no usable models" + (" (run: ollama pull <model>)" if name == "ollama" else ""))
        return False
    wanted = [m for m in preferred() if m]
    for m in wanted:
        if m not in available:
            print(f"  WARNING: model '{m}' from .env is NOT in the provider's current list")
    model = _pick(available, wanted, hints)
    print(f"  testing model: {model}")
    t0 = time.perf_counter()
    try:
        text = call(model)
        data = _parse_json(text)
    except Exception as e:                      # HTTP error, timeout, invalid JSON
        print(f"  FAIL after {time.perf_counter() - t0:.2f}s: {type(e).__name__}: {str(e)[:300]}")
        return False
    print(f"  OK in {time.perf_counter() - t0:.2f}s -> {json.dumps(data)}")
    return True


def check_agents():
    """Item 2.2: for every agent, show its chain, ping every model in it directly, then make one real call
    through the router (the model that answers, latency, tokens and fallback are what the agents will see)."""
    from pydantic import BaseModel

    from src.common import llm_router as R

    class Probe(BaseModel):
        request_kind: str
        target: str

    question = PROMPT.split("Request: ", 1)[1]
    ok_all, pinged = True, {}
    for agent, var in R.AGENT_VARS.items():
        chain = R.agent_chain(agent)
        print(f"\n=== {agent:<13} {var}={R.agent_model(agent)}")
        print(f"  chain: {' -> '.join(chain + ['rule-based'])}")
        for spec in chain:
            if spec in pinged:
                continue
            provider, model = R.parse_spec(spec)
            t0 = time.perf_counter()
            try:
                text, tokens = R.CALLERS[provider](model, None, PROMPT, R.TIMEOUTS[agent] * 2, None)
                _parse_json(text)
                pinged[spec] = f"OK {time.perf_counter() - t0:.2f}s ({tokens} tokens)"
            except Exception as e:  # noqa: BLE001
                pinged[spec] = f"FAIL {time.perf_counter() - t0:.2f}s {str(e)[:120]}"
            print(f"  ping {spec:<40} {pinged[spec]}")
        R.reset_cooldowns()                 # the router call below must not be skewed by the direct pings
        res = R.complete_json(agent, f"Classify this IT request. Request: {question}", Probe,
                              fallback=lambda: {"request_kind": "other", "target": ""})
        print(f"  router -> {res.method}: {res.summary()}  data={res.data.model_dump() if res.data else None}")
        ok_all &= res.ok
    print(f"\nAgents: {'all answered by an LLM' if ok_all else 'some agents used the rule-based fallback'}"
          f"  (log: {R.LOG_FILE.relative_to(R.PROJECT_ROOT)})")
    return ok_all


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=list(PROVIDERS))
    ap.add_argument("--list", action="store_true", help="print every available model")
    ap.add_argument("--agents", action="store_true",
                    help="per-agent check: chain, direct ping of each model, one call through the router")
    args = ap.parse_args()
    if args.agents:
        raise SystemExit(0 if check_agents() else 1)
    names = [args.provider] if args.provider else list(PROVIDERS)
    results = {n: check(n, args.list) for n in names}
    print("\nSummary: " + ", ".join(f"{n}={'OK' if r else 'SKIPPED' if r is None else 'FAIL'}"
                                    for n, r in results.items()))


if __name__ == "__main__":
    main()
