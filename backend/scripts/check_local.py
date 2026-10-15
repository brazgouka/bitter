"""Verify a local OpenAI-compatible LLM (Ollama, LM Studio, ...) is reachable.

Run from the backend directory:  python scripts/check_local.py
Reads LOCAL_MODEL_BASE_URL / LOCAL_MODEL / LOCAL_MODEL_API_KEY from backend/.env.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def main() -> None:
    base = settings.local_model_base_url.rstrip("/")
    model = settings.local_model
    headers = {"Content-Type": "application/json"}
    if settings.local_model_api_key:
        headers["Authorization"] = f"Bearer {settings.local_model_api_key}"

    print(f"Base URL: {base}")
    print(f"Model:    {model}")

    try:
        r = httpx.get(f"{base}/models", headers=headers, timeout=10)
        print(f"GET /models -> {r.status_code}")
        if r.status_code == 200:
            ids = [m.get("id") for m in r.json().get("data", [])]
            print("Available models:", ", ".join(i for i in ids if i) or "(none)")
    except Exception as exc:
        print(f"❌ Cannot reach {base}: {exc}")
        print("   Is the server running? For Ollama: `ollama serve` in another terminal.")
        sys.exit(1)

    print("Sending a tiny chat completion (first call may be slow while the model loads)…")
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Reply with exactly: Bitter local check OK"}
        ],
        "stream": False,
        "temperature": 0,
    }
    try:
        r = httpx.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"❌ Chat completion failed: {exc}")
        print(f"   Make sure the model is pulled, e.g. `ollama pull {model}`.")
        sys.exit(1)

    print(f"Response: {content}")
    print("✅ Local model check passed — Bitter can use ADVISOR_BACKEND=local.")


if __name__ == "__main__":
    main()
