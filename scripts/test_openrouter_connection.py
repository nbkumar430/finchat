#!/usr/bin/env python3
"""Local connection test for OpenRouter (env API key only).

Set on Cloud Run or locally:
  OPEN_ROUTER_API_KEY   (preferred)
  OPENROUTER_API_KEY    (legacy alias)

Usage (from repo root):
  export SUMMARIZATION_PROVIDER=openrouter
  export OPEN_ROUTER_API_KEY='sk-or-v1-...'
  PYTHONPATH=. python scripts/test_openrouter_connection.py

Exit 0 on success; non-zero on failure.
"""

from __future__ import annotations

import os
import sys

# Repo root on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    os.environ.setdefault("SUMMARIZATION_PROVIDER", "openrouter")

    from app.config import get_settings
    from app.openrouter_client import get_openrouter_api_key, post_chat_completion

    get_settings.cache_clear()
    settings = get_settings()

    print("Settings:")
    print(f"  SUMMARIZATION_PROVIDER={settings.summarization_provider!r}")
    print(f"  OPENROUTER_MODEL={settings.openrouter_model!r}")
    print(f"  OPENROUTER_BASE_URL={settings.openrouter_base_url!r}")
    key_set = bool((settings.open_router_api_key or "").strip())
    print(f"  OPEN_ROUTER_API_KEY / OPENROUTER_API_KEY resolved: {key_set}")
    if not key_set:
        print(
            "\nERROR: Set OPEN_ROUTER_API_KEY (or legacy OPENROUTER_API_KEY).",
            file=sys.stderr,
        )
        return 2

    try:
        key = get_openrouter_api_key(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: Could not resolve API key: {exc}", file=sys.stderr)
        return 3

    print(f"  API key prefix: {key[:12]}… (length {len(key)})")

    user_content = 'Reply with exactly one word: "pong".'
    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"

    print("\n--- INPUT (sent to OpenRouter) ---")
    print(f"  POST {url}")
    print(f"  model: {settings.openrouter_model!r}")
    print("  messages[0].role: user")
    print(f"  messages[0].content: {user_content!r}")
    print("  temperature: 0.0, max_tokens: 32")

    try:
        reply = post_chat_completion(
            api_key=key,
            model=settings.openrouter_model,
            user_content=user_content,
            base_url=settings.openrouter_base_url,
            http_referer=settings.openrouter_http_referer,
            app_title=settings.openrouter_app_title,
            temperature=0.0,
            max_tokens=32,
            timeout_seconds=45.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: OpenRouter request failed: {exc}", file=sys.stderr)
        return 4

    print("\n--- OUTPUT (assistant message from OpenRouter) ---")
    print(f"  {reply!r}")
    print("\nOK — OpenRouter connection test succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
