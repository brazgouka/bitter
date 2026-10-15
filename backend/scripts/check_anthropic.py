"""Verify the Anthropic (Claude) integration: key + model + a real API call.

Run from the backend directory:  python scripts/check_anthropic.py
Reads ANTHROPIC_API_KEY / ANTHROPIC_MODEL from backend/.env or the environment.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402


def main() -> None:
    key = settings.anthropic_api_key
    if not key:
        print("❌ ANTHROPIC_API_KEY is not set.")
        print("   Put it in backend/.env (ANTHROPIC_API_KEY=sk-ant-...) or export it.")
        sys.exit(1)

    model = settings.anthropic_model
    print(f"Key:   {key[:8]}…{key[-4:]}  (len {len(key)})")
    print(f"Model: {model}")
    print("Calling the Messages API…")

    from anthropic import Anthropic

    client = Anthropic(api_key=key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=64,
            messages=[
                {"role": "user", "content": "Reply with exactly: Bitter Anthropic check OK"}
            ],
        )
    except Exception as exc:  # surface the real reason clearly
        print(f"❌ Anthropic call failed: {type(exc).__name__}: {exc}")
        print(
            "   If this is a 'model not found' error, set ANTHROPIC_MODEL to an id\n"
            "   your account can access (e.g. claude-opus-4-8) in backend/.env."
        )
        sys.exit(1)

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    print(f"Response: {text.strip()}")
    print(f"Usage: in={msg.usage.input_tokens} out={msg.usage.output_tokens}")
    print("✅ Anthropic check passed — Claude is reachable and the model works.")


if __name__ == "__main__":
    main()
