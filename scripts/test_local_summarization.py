#!/usr/bin/env python3
"""Smoke-test Gemini summarization outside HTTP (direct SDK call).

Requires one of:
  - USE_VERTEX_AI=false and GEMINI_API_KEY set (easiest for local laptops), or
  - USE_VERTEX_AI=true and Application Default Credentials with Vertex access.

Usage (from repo root):
  export GEMINI_API_KEY=... USE_VERTEX_AI=false
  python scripts/test_local_summarization.py

See docs/AI_SUMMARIZATION.md for failure scenarios.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    use_vertex = os.getenv("USE_VERTEX_AI", "false").lower() == "true"
    if not use_vertex and not os.getenv("GEMINI_API_KEY"):
        print(
            "Set GEMINI_API_KEY when USE_VERTEX_AI=false, or enable Vertex + ADC for USE_VERTEX_AI=true.",
            file=sys.stderr,
        )
        return 2

    # Fresh settings after env is set
    from app.config import get_settings
    from app.vertex_client import get_vertex_backend_status, init_vertex, summarize_news

    get_settings.cache_clear()
    init_vertex()
    status = get_vertex_backend_status()
    print(f"Health probe ai_backend_status: {status}")

    ctx = "[Article 1] Ticker: AAPL\nTitle: Test headline\nContent: Apple announced a software update for developers.\n"
    text = summarize_news("What did Apple announce?", ctx)
    print("Summarization OK, preview:", text[:400].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
