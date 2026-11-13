"""Structured-output model call: Gemini first, Groq when Gemini is busy.

Groq is for short text prompts only. Its free tier is reported at about 8,000 tokens a minute, which fits the
comparison and bull/bear calls but not a whole transcript.
"""
from __future__ import annotations
import json, os

import requests

from ingest.enrich import _generate, parse_gemini

GROQ = "https://api.groq.com/openai/v1"
GROQ_MAX_PROMPT_CHARS = 24_000  # roughly 6,000 tokens, leaving room for the answer
GROQ_PREFERENCE = ("gpt-oss-120b", "qwen", "gpt-oss")


def choose_groq_model(key: str) -> str:
    if os.environ.get("GROQ_MODEL"):
        return os.environ["GROQ_MODEL"]
    r = requests.get(f"{GROQ}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    names = [m["id"] for m in r.json().get("data", [])]
    for wanted in GROQ_PREFERENCE:
        match = next((n for n in names if wanted in n.lower()), None)
        if match:
            return match
    raise RuntimeError("No suitable Groq model; set GROQ_MODEL")


def parse_groq(payload: dict) -> tuple[dict, dict]:
    usage = payload.get("usage", {})
    return json.loads(payload["choices"][0]["message"]["content"]), {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}


def _call_groq(prompt: str, required: tuple[str, ...], key: str) -> tuple[dict, dict, str]:
    model = choose_groq_model(key)
    body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": f"{prompt}\n\nReturn one JSON object with these top-level keys: {', '.join(required)}."}]}
    r = requests.post(f"{GROQ}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=90)
    r.raise_for_status()
    data, tokens = parse_groq(r.json())
    missing = [k for k in required if k not in data]
    if missing:  # Groq doesn't enforce a schema, so check what came back
        raise ValueError(f"Groq response missing {missing}")
    return data, tokens, f"groq/{model}"


def complete_json(prompt: str, schema: dict, models: list[str], key: str, required: tuple[str, ...],
                  allow_groq: bool = True) -> tuple[dict, dict, str]:
    """(data, tokens, model used). Raises only if every allowed provider fails."""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema}}
    try:
        payload, model = _generate(body, models, key)
        data, tokens = parse_gemini(payload)
        return data, tokens, model
    except Exception:
        groq_key = os.environ.get("GROQ_API_KEY")
        if not allow_groq or not groq_key or len(prompt) > GROQ_MAX_PROMPT_CHARS:
            raise
        return _call_groq(prompt, required, groq_key)
