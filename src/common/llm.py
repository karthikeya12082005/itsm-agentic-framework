"""Minimal Gemini client (REST generateContent, JSON output). Returns None on any failure so callers
can fall back to deterministic logic - the workflow never depends on the LLM being available."""
import json
import os

import requests

from src.common import config  # noqa: F401  (loads .env)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_json(prompt: str, model: str = None, timeout: int = 30):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = model or os.environ.get("GEMINI_MODEL_LITE", "gemini-3.5-flash-lite")
    if not key:
        return None
    try:
        r = requests.post(GEMINI_URL.format(model=model), timeout=timeout,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                          json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        if r.status_code != 200:
            print(f"[llm] Gemini HTTP {r.status_code}: {r.text[:200]}")
            return None
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:                       # network / parse errors -> deterministic fallback
        print(f"[llm] Gemini unavailable ({type(e).__name__}); using rule-based fallback")
        return None
