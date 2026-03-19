"""Tests for OpenRouter client helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import get_settings
from app.openrouter_client import (
    get_openrouter_api_key,
    post_chat_completion,
    reset_openrouter_key_cache_for_tests,
)


@pytest.fixture(autouse=True)
def clear_openrouter_cache():
    reset_openrouter_key_cache_for_tests()
    yield
    reset_openrouter_key_cache_for_tests()


def test_get_openrouter_api_key_open_router_env(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test-key")
    get_settings.cache_clear()
    settings = get_settings()
    assert get_openrouter_api_key(settings) == "sk-or-test-key"


def test_get_openrouter_api_key_legacy_env(monkeypatch):
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-legacy-key")
    get_settings.cache_clear()
    settings = get_settings()
    assert get_openrouter_api_key(settings) == "sk-or-legacy-key"


def test_post_chat_completion_parses_response():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "  Hello world  "}}],
    }
    mock_response.raise_for_status = MagicMock()
    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_cls = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.openrouter_client.httpx.Client", mock_client_cls):
        out = post_chat_completion(
            api_key="k",
            model="m",
            user_content="hi",
            base_url="https://openrouter.ai/api/v1",
            http_referer="",
            app_title="",
        )
    assert out == "Hello world"
