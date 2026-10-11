import json
import re
import time

import requests

from .config import cfg

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_last_call = 0.0


class GeminiError(RuntimeError):
    pass


def parse_response(r):
    parts = (((r.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        info = r.get("promptFeedback") or r.get("error") or {}
        raise ValueError("Gemini returned no text: " + json.dumps(info)[:200])
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def call(model, system, user, schema, temperature=0.2, max_tokens=8192, retries=5):
    """One structured-JSON Gemini call with spacing between calls and retry/backoff."""
    global _last_call
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    delay = 5
    last_err = "unknown"
    for _ in range(retries):
        wait = cfg.min_gap - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.post(
                URL.format(model=model),
                headers={"x-goog-api-key": cfg.gemini_key, "Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
        except requests.RequestException:
            _last_call = time.time()
            last_err = "network error"
        else:
            _last_call = time.time()
            if r.status_code == 200:
                try:
                    return parse_response(r.json())
                except (ValueError, KeyError) as e:  # truncated / empty output: retry
                    last_err = f"bad output ({type(e).__name__})"
            elif r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
            else:
                raise GeminiError(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise GeminiError(f"Gemini failed after {retries} tries ({last_err})")
