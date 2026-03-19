#!/usr/bin/env python3
"""Quick chat verifier for FinChat deployment.

Usage:
  python scripts/verify_chat.py --base-url https://finchat-app-xxxxx.run.app
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.error
import urllib.request
from typing import Optional


def _request_json(url: str, method: str = "GET", payload: Optional[dict] = None) -> dict:  # noqa: UP007 (py3.9 compat)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify FinChat health and chat summarization.")
    parser.add_argument("--base-url", required=True, help="Base URL, e.g. https://finchat-app-xxxxx.run.app")
    parser.add_argument("--query", default="What is the latest news about Apple?")
    parser.add_argument("--ticker", default="AAPL")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    health_url = f"{base}/health"
    chat_url = f"{base}/api/chat"

    try:
        health = _request_json(health_url)
        print("HEALTH:", json.dumps(health, indent=2))

        payload = {"query": args.query, "ticker": args.ticker}
        chat = _request_json(chat_url, method="POST", payload=payload)
        print("CHAT:", json.dumps(chat, indent=2))

        fallback = bool(chat.get("fallback_mode"))
        ai_status = health.get("ai_backend_status")
        print(f"RESULT: ai_backend_status={ai_status}, fallback_mode={fallback}")
        if fallback:
            print("FAIL: summarization is not active (fallback mode).")
            return 2
        print("PASS: summarization is active.")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        with contextlib.suppress(Exception):
            print(exc.read().decode("utf-8"), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
