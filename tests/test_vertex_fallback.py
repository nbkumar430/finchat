"""Tests for Vertex → Gemini API fallback behavior."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "env_vertex,env_key,expect_calls",
    [
        ("true", "dummy-key", 2),
        ("false", "dummy-key", 1),
    ],
)
def test_summarize_uses_api_fallback_after_vertex_failure(env_vertex, env_key, expect_calls):
    """When Vertex fails and a key exists, API path should be attempted."""
    from app import vertex_client as vc
    from app.vertex_client import summarize_news

    with patch.dict(
        os.environ,
        {
            "USE_VERTEX_AI": env_vertex,
            "GEMINI_API_KEY": env_key,
            "VERTEX_FALLBACK_TO_API_KEY": "true",
            "GCP_PROJECT_ID": "test-project",
            "GCP_REGION": "us-central1",
        },
        clear=False,
    ):
        from app.config import get_settings

        get_settings.cache_clear()
        # Reset module globals that retain client/circuit state between tests
        vc._client = MagicMock()  # noqa: SLF001
        vc._api_key_client = None  # noqa: SLF001
        vc._active_model = None  # noqa: SLF001
        vc._failure_streak = 0  # noqa: SLF001
        vc._circuit_open_until = 0.0  # noqa: SLF001
        vc._concurrency_sem = None  # noqa: SLF001

        calls: list[str] = []

        def fake_generate(client, model_candidates, prompt, query, context, settings, start, backend_label, **kwargs):
            calls.append(backend_label)
            if backend_label == "vertexai":
                raise RuntimeError("404 NOT_FOUND Publisher Model")
            return "OK from api"

        with (
            patch.object(vc, "_get_api_key_client", return_value=MagicMock()),
            patch.object(vc, "_generate_summary_with_client", side_effect=fake_generate),
        ):
            out = summarize_news("q?", "article context")
        assert out == "OK from api"
        assert len(calls) == expect_calls
        if env_vertex == "true":
            assert calls == ["vertexai", "apikey"]
        else:
            assert calls == ["apikey"]
