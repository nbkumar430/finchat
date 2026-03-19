"""OpenRouter chat completions client (API key from env only)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_openrouter_key_cache: Optional[str] = None
_key_lock = threading.Lock()


def reset_openrouter_key_cache_for_tests() -> None:
    """Clear cached API key (pytest only)."""
    global _openrouter_key_cache
    with _key_lock:
        _openrouter_key_cache = None


def get_openrouter_api_key(settings: Settings) -> str:
    """Return API key from OPEN_ROUTER_API_KEY (or legacy OPENROUTER_API_KEY)."""
    global _openrouter_key_cache
    with _key_lock:
        if _openrouter_key_cache:
            return _openrouter_key_cache

        direct = (settings.open_router_api_key or "").strip()
        if direct:
            _openrouter_key_cache = direct
            return _openrouter_key_cache

        raise RuntimeError(
            "OpenRouter is configured but OPEN_ROUTER_API_KEY is not set. "
            "Add it as a Cloud Run environment variable (or export OPEN_ROUTER_API_KEY locally). "
            "Legacy name OPENROUTER_API_KEY is also accepted."
        )


def post_chat_completion(
    *,
    api_key: str,
    model: str,
    user_content: str,
    base_url: str,
    http_referer: str,
    app_title: str,
    temperature: float = 0.3,
    max_tokens: int = 768,
    timeout_seconds: float = 90.0,
) -> str:
    """Call OpenRouter chat completions; return assistant text."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if http_referer:
        headers["HTTP-Referer"] = http_referer
    if app_title:
        headers["X-Title"] = app_title

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_content}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {data!r}")

    message = choices[0].get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError("OpenRouter returned an empty message.")
    return text


def openrouter_complete_user_prompt(settings: Settings, user_prompt: str) -> str:
    """Run one FinChat user prompt (full prompt string) through OpenRouter."""
    key = get_openrouter_api_key(settings)
    return post_chat_completion(
        api_key=key,
        model=settings.openrouter_model,
        user_content=user_prompt,
        base_url=settings.openrouter_base_url,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
        temperature=settings.openrouter_temperature,
        max_tokens=settings.openrouter_max_output_tokens,
        timeout_seconds=settings.openrouter_timeout_seconds,
    )


def probe_openrouter(settings: Settings) -> None:
    """Lightweight health check (small completion)."""
    start = time.perf_counter()
    key = get_openrouter_api_key(settings)
    _ = post_chat_completion(
        api_key=key,
        model=settings.openrouter_model,
        user_content="Reply with exactly: OK",
        base_url=settings.openrouter_base_url,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
        temperature=0.0,
        max_tokens=16,
        timeout_seconds=min(30.0, settings.openrouter_timeout_seconds),
    )
    elapsed = time.perf_counter() - start
    logger.info("OpenRouter health probe ok in %.2fs", elapsed)
