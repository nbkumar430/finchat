"""Gemini AI client for summarizing financial news."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

from google import genai
from google.genai import types

from app.config import get_settings
from app.metrics import VERTEX_ERRORS, VERTEX_LATENCY

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
_health_cache_status: str = "degraded"
_health_cache_ts: float = 0.0
_HEALTH_TTL_SECONDS = 60.0
_state_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
_concurrency_sem: Optional[threading.BoundedSemaphore] = None  # noqa: UP007 (py3.9 compat)
_failure_streak = 0
_circuit_open_until = 0.0
_active_model: Optional[str] = None  # noqa: UP007 (py3.9 compat)


def init_vertex() -> None:
    """Initialize Gemini AI client (API key or Vertex AI backend)."""
    global _client
    settings = get_settings()
    # Prefer Vertex AI runtime auth on Cloud Run to avoid Gemini API key quota limits.
    if settings.use_vertex_ai:
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
    elif settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(
            "Gemini AI initialized with API key, model=%s",
            settings.vertex_model,
        )
    else:
        raise RuntimeError("No AI backend configured. Set USE_VERTEX_AI=true (recommended) or provide GEMINI_API_KEY.")
    _initialize_concurrency_guard()


def _initialize_concurrency_guard() -> None:
    global _concurrency_sem
    if _concurrency_sem is None:
        max_in_flight = max(1, get_settings().vertex_max_in_flight)
        _concurrency_sem = threading.BoundedSemaphore(value=max_in_flight)


def _cache_key(query: str, context: str, model: str) -> str:
    digest = hashlib.sha256(f"{model}\n{query}\n{context}".encode()).hexdigest()
    return digest


def _model_candidates() -> list[str]:
    settings = get_settings()
    fallbacks = [m.strip() for m in settings.vertex_fallback_models.split(",") if m.strip()]
    models: list[str] = [settings.vertex_model, *fallbacks]
    seen: set[str] = set()
    unique: list[str] = []
    for m in models:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


def _current_model() -> str:
    global _active_model
    if _active_model:
        return _active_model
    return get_settings().vertex_model


def _set_active_model(model: str) -> None:
    global _active_model
    _active_model = model


def _cache_get(key: str) -> Optional[str]:  # noqa: UP007 (py3.9 compat)
    settings = get_settings()
    now = time.time()
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, text = item
        if now - ts > settings.summary_cache_ttl_seconds:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return text


def _cache_put(key: str, text: str) -> None:
    settings = get_settings()
    now = time.time()
    with _cache_lock:
        _cache[key] = (now, text)
        _cache.move_to_end(key)
        while len(_cache) > settings.summary_cache_max_entries:
            _cache.popitem(last=False)


def _is_transient_error(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(marker in text for marker in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "TIMEOUT"))


def _is_circuit_open() -> bool:
    with _state_lock:
        return time.time() < _circuit_open_until


def _record_success() -> None:
    global _failure_streak
    with _state_lock:
        _failure_streak = 0


def _record_failure() -> None:
    global _failure_streak, _circuit_open_until
    settings = get_settings()
    with _state_lock:
        _failure_streak += 1
        if _failure_streak >= settings.vertex_circuit_threshold:
            _circuit_open_until = time.time() + float(settings.vertex_circuit_cooldown_seconds)


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
        model = _current_model()
        _client.models.generate_content(
            model=model,
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
        logger.warning(
            "Vertex backend health probe failed for model=%s backend=%s: %s",
            model,
            "vertexai" if settings.use_vertex_ai else "apikey",
            exc,
        )
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
    _initialize_concurrency_guard()

    settings = get_settings()
    if _is_circuit_open():
        raise RuntimeError("AI backend temporarily overloaded (circuit open).")

    # Bound prompt size to reduce token pressure during spikes.
    context = context[:7000]
    model = _current_model()
    cache_key = _cache_key(query=query, context=context, model=model)
    cached_answer = _cache_get(cache_key)
    if cached_answer is not None:
        logger.info("Gemini cache hit for model=%s", model)
        return cached_answer

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
    sem = _concurrency_sem
    if sem is None:
        raise RuntimeError("AI concurrency guard not initialized.")
    acquired = sem.acquire(timeout=1.5)
    if not acquired:
        raise RuntimeError("AI backend busy; too many concurrent requests.")
    try:
        last_exc: Optional[Exception] = None  # noqa: UP007 (py3.9 compat)
        candidate_models = _model_candidates()
        last_exc: Optional[Exception] = None  # noqa: UP007 (py3.9 compat)
        for candidate in candidate_models:
            for attempt in range(settings.vertex_max_retries):
                try:
                    response = _client.models.generate_content(
                        model=candidate,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            max_output_tokens=768,
                            top_p=0.8,
                        ),
                    )
                    if not response.text:
                        raise RuntimeError("Gemini AI returned an empty response.")
                    elapsed = time.perf_counter() - start
                    VERTEX_LATENCY.labels(model=candidate).observe(elapsed)
                    logger.info("Gemini AI response in %.2fs", elapsed, extra={"latency_ms": elapsed * 1000})
                    _health_cache_status = "up"
                    _health_cache_ts = time.time()
                    _record_success()
                    _set_active_model(candidate)
                    _cache_put(_cache_key(query=query, context=context, model=candidate), response.text)
                    return response.text
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    is_not_found = "404" in str(exc).upper() and "NOT_FOUND" in str(exc).upper()
                    if is_not_found:
                        logger.warning("Model unavailable in project: %s; trying next fallback model.", candidate)
                        break
                    if not _is_transient_error(exc) or attempt >= settings.vertex_max_retries - 1:
                        raise
                    backoff = settings.vertex_retry_base_seconds * (2**attempt)
                    logger.warning(
                        "Transient AI failure model=%s attempt=%d/%d backoff=%.2fs error=%s",
                        candidate,
                        attempt + 1,
                        settings.vertex_max_retries,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
        if last_exc:
            raise last_exc
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=model).observe(elapsed)
        raise RuntimeError("Gemini AI failed without explicit exception.")
    except Exception as exc:
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=settings.vertex_model).observe(elapsed)
        VERTEX_ERRORS.labels(model=settings.vertex_model, error_type=type(exc).__name__).inc()
        _record_failure()
        logger.error(
            "Gemini AI call failed for model=%s backend=%s: %s",
            _current_model(),
            "vertexai" if settings.use_vertex_ai else "apikey",
            exc,
            exc_info=True,
        )
        _health_cache_status = "degraded"
        _health_cache_ts = time.time()
        raise
    finally:
        sem.release()
