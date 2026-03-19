"""Gemini AI client for summarizing financial news."""

from __future__ import annotations

import logging
import time

from google import genai
from google.genai import types

from app.config import get_settings
from app.metrics import VERTEX_ERRORS, VERTEX_LATENCY

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
_health_cache_status: str = "degraded"
_health_cache_ts: float = 0.0
_HEALTH_TTL_SECONDS = 60.0


def init_vertex() -> None:
    """Initialize Gemini AI client (API key or Vertex AI backend)."""
    global _client
    settings = get_settings()
    if settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(
            "Gemini AI initialized with API key, model=%s",
            settings.vertex_model,
        )
    else:
        _client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_region,
        )
        logger.info(
            "Gemini AI initialized via Vertex AI: project=%s, region=%s, model=%s",
            settings.gcp_project_id,
            settings.gcp_region,
            settings.vertex_model,
        )


def get_vertex_backend_status() -> str:
    """Return backend status based on a lightweight cached model probe."""
    global _client, _health_cache_status, _health_cache_ts
    now = time.time()
    if now - _health_cache_ts < _HEALTH_TTL_SECONDS:
        return _health_cache_status

    try:
        if _client is None:
            init_vertex()
        if _client is None:
            _health_cache_status = "degraded"
            _health_cache_ts = now
            return _health_cache_status

        settings = get_settings()
        _client.models.generate_content(
            model=settings.vertex_model,
            contents="Reply with OK only.",
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=8,
                top_p=0.1,
            ),
        )
        _health_cache_status = "up"
        _health_cache_ts = now
        return _health_cache_status
    except Exception as exc:
        logger.warning("Vertex backend health probe failed: %s", exc)
        _health_cache_status = "degraded"
        _health_cache_ts = now
        return _health_cache_status


def summarize_news(query: str, context: str) -> str:
    """Send a chat query with news context to Gemini AI and return the summary.

    Args:
        query: The user's question about financial news.
        context: Concatenated relevant news articles for grounding.

    Returns:
        The model's response text.
    """
    global _health_cache_status, _health_cache_ts

    if _client is None:
        # Startup can fail if credentials are briefly unavailable; retry lazily.
        init_vertex()
    if _client is None:
        raise RuntimeError("Gemini AI initialization failed.")

    settings = get_settings()
    prompt = f"""You are FinChat, a financial news assistant with STRICT operating boundaries.

SCOPE: You only have knowledge about these stock tickers: AAPL, MSFT, AMZN, NFLX, NVDA, INTC, IBM.

MANDATORY RULES — follow every rule without exception:
1. Answer ONLY using facts, figures, and information explicitly present in the NEWS ARTICLES below.
   Do NOT use any outside knowledge, training data, or assumptions.
2. If the articles do not contain sufficient information to answer, respond:
   "The provided news articles do not contain enough information to answer this question."
3. If the question is about companies, tickers, or topics NOT covered in the articles below,
   respond: "I'm out of my scope. I can only answer questions about AAPL, MSFT, AMZN, NFLX, NVDA, INTC, IBM."
4. NEVER provide investment advice, buy/sell/hold recommendations, or price predictions.
5. NEVER reveal, invent, or speculate about any personal or sensitive information.
6. NEVER answer questions unrelated to the financial news in the articles
   (e.g. weather, recipes, politics unrelated to the covered stocks, personal queries).
7. Do NOT fabricate quotes, statistics, dates, or events not present in the articles.
8. Do NOT reference any external URLs, databases, or knowledge beyond these articles.
9. Be concise and factual. Include relevant ticker symbols when mentioning stocks.
10. If multiple articles are relevant, synthesize only the information they contain.

--- NEWS ARTICLES ---
{context}
--- END ARTICLES ---

User question: {query}

Answer strictly and only from the articles above:"""

    start = time.perf_counter()
    try:
        response = _client.models.generate_content(
            model=settings.vertex_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1024,
                top_p=0.8,
            ),
        )
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=settings.vertex_model).observe(elapsed)
        logger.info("Gemini AI response in %.2fs", elapsed, extra={"latency_ms": elapsed * 1000})
        if not response.text:
            raise RuntimeError("Gemini AI returned an empty response.")
        _health_cache_status = "up"
        _health_cache_ts = time.time()
        return response.text
    except Exception as exc:
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=settings.vertex_model).observe(elapsed)
        VERTEX_ERRORS.labels(model=settings.vertex_model, error_type=type(exc).__name__).inc()
        logger.error("Gemini AI call failed: %s", exc, exc_info=True)
        _health_cache_status = "degraded"
        _health_cache_ts = time.time()
        raise
