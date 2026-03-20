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

from app.config import Settings, get_settings
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
_active_backend: str = "vertexai"  # "vertexai" | "apikey"
_api_key_client: Optional[genai.Client] = None  # noqa: UP007 (py3.9 compat)
_api_key_lock = threading.Lock()


def init_vertex() -> None:
    """Initialize Gemini AI client (API key or Vertex AI backend)."""
    global _client
    settings = get_settings()
    if settings.summarization_provider == "openrouter":
        try:
            from app.openrouter_client import get_openrouter_api_key

            get_openrouter_api_key(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OpenRouter API key not loaded at startup (will retry on first chat): %s",
                exc,
            )
        _client = None
        _initialize_concurrency_guard()
        logger.info(
            "Summarization provider=openrouter model=%s (Vertex client skipped)",
            settings.openrouter_model,
        )
        return
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


def _set_active_backend(backend: str) -> None:
    global _active_backend
    _active_backend = backend


def _get_api_key_client() -> genai.Client:
    """Lazy client for Gemini Developer API (AI Studio key)."""
    global _api_key_client
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set; cannot use API key fallback.")
    with _api_key_lock:
        if _api_key_client is None:
            _api_key_client = genai.Client(api_key=settings.gemini_api_key)
        return _api_key_client


def _api_key_model_candidates() -> list[str]:
    settings = get_settings()
    parts = [m.strip() for m in settings.gemini_api_fallback_models.split(",") if m.strip()]
    # Prefer configured primary first if not already in list
    primary = settings.vertex_model.strip()
    out: list[str] = []
    seen: set[str] = set()
    for m in [primary, *parts]:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _is_not_found_error(exc: Exception) -> bool:
    t = str(exc).upper()
    return "404" in t and "NOT_FOUND" in t


def _generate_summary_with_client(
    client: genai.Client,
    model_candidates: list[str],
    prompt: str,
    query: str,
    context: str,
    settings: Settings,
    start: float,
    backend_label: str,
    *,
    prompt_style: str = "json_strict",
) -> str:
    """Try model IDs in order; return first successful summary text."""
    last_exc: Optional[Exception] = None  # noqa: UP007 (py3.9 compat)
    for candidate in model_candidates:
        for attempt in range(settings.vertex_max_retries):
            try:
                response = client.models.generate_content(
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
                logger.info(
                    "Gemini AI response backend=%s model=%s in %.2fs",
                    backend_label,
                    candidate,
                    elapsed,
                    extra={"latency_ms": elapsed * 1000},
                )
                _health_cache_status = "up"
                _health_cache_ts = time.time()
                _record_success()
                _set_active_model(candidate)
                _set_active_backend(backend_label)
                _cache_put(
                    _cache_key(
                        query=query,
                        context=context,
                        model=f"{backend_label}:{candidate}:{prompt_style}",
                    ),
                    response.text,
                )
                return response.text
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_not_found_error(exc):
                    logger.warning(
                        "Model unavailable (%s): %s; trying next.",
                        backend_label,
                        candidate,
                    )
                    break
                if not _is_transient_error(exc) or attempt >= settings.vertex_max_retries - 1:
                    raise
                backoff = settings.vertex_retry_base_seconds * (2**attempt)
                logger.warning(
                    "Transient AI failure backend=%s model=%s attempt=%d/%d backoff=%.2fs error=%s",
                    backend_label,
                    candidate,
                    attempt + 1,
                    settings.vertex_max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
    if last_exc:
        raise last_exc
    raise RuntimeError("Gemini AI failed without explicit exception.")


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
    settings = get_settings()
    now = time.time()
    if now - _health_cache_ts < _HEALTH_TTL_SECONDS:
        return _health_cache_status

    if settings.summarization_provider == "openrouter":
        try:
            from app.openrouter_client import probe_openrouter

            probe_openrouter(settings)
            _set_active_model(settings.openrouter_model)
            _set_active_backend("openrouter")
            _health_cache_status = "up"
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenRouter health probe failed: %s", exc)
            _health_cache_status = "degraded"
        _health_cache_ts = now
        return _health_cache_status

    try:
        if _client is None:
            init_vertex()
        if _client is None:
            _health_cache_status = "degraded"
            _health_cache_ts = now
            return _health_cache_status

        last_exc: Optional[Exception] = None  # noqa: UP007 (py3.9 compat)
        probe_models = _model_candidates() if settings.use_vertex_ai else _api_key_model_candidates()
        probe_backend = "vertexai" if settings.use_vertex_ai else "apikey"
        for candidate in probe_models:
            try:
                _client.models.generate_content(
                    model=candidate,
                    contents="Reply with OK only.",
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=8,
                        top_p=0.1,
                    ),
                )
                _set_active_model(candidate)
                _set_active_backend(probe_backend)
                _health_cache_status = "up"
                _health_cache_ts = now
                return _health_cache_status
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_not_found_error(exc):
                    logger.warning(
                        "Health probe model unavailable (%s): %s",
                        probe_backend,
                        candidate,
                    )
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("No healthy model candidate available.")
    except Exception as exc:
        # If Vertex probes failed but API key is configured, try Developer API once per cache window.
        if settings.use_vertex_ai and settings.vertex_fallback_to_api_key and settings.gemini_api_key:
            try:
                api = _get_api_key_client()
                for cand in _api_key_model_candidates():
                    try:
                        api.models.generate_content(
                            model=cand,
                            contents="Reply with OK only.",
                            config=types.GenerateContentConfig(
                                temperature=0.0,
                                max_output_tokens=8,
                                top_p=0.1,
                            ),
                        )
                        _set_active_model(cand)
                        _set_active_backend("apikey")
                        _health_cache_status = "up"
                        _health_cache_ts = now
                        return _health_cache_status
                    except Exception as api_exc:  # noqa: BLE001
                        if _is_not_found_error(api_exc):
                            logger.warning("Health probe API model unavailable: %s", cand)
                            continue
                        raise
            except Exception as api_probe_exc:  # noqa: BLE001
                logger.warning("API key health probe failed: %s", api_probe_exc)
        logger.warning(
            "AI backend health probe failed for model=%s backend=%s: %s",
            _current_model(),
            _active_backend,
            exc,
        )
        _health_cache_status = "degraded"
        _health_cache_ts = now
        return _health_cache_status


def _build_finchat_prompt_json_strict(query: str, context: str) -> str:
    """Priority 1: answer only from bundled JSON; tie claims to Source URL; summarize full_text."""
    return f"""You are FinChat. PRIORITY 1 — BUNDLED JSON ONLY.

The blocks below are FinChat's local stock news dataset (JSON). This is the ONLY source you may use for facts in this mode.

SCOPE tickers: AAPL, MSFT, AMZN, NFLX, NVDA, INTC, IBM.

RULES:
1. Treat the user's question as matching the *article Title* when possible; prefer the article(s) whose title is closest to the question.
2. Summarize using the **Full text** field (and title for orientation). Do not use outside news, the web, or your training data.
3. Every material claim must be traceable to the excerpts. When you combine ideas, cite which article (by title) and include its **Source URL** from the block (users rely on the link from JSON).
4. Do not fabricate quotes, numbers, dates, or events. Do not invent URLs.
5. No investment advice, buy/sell/hold, or price predictions.
6. If the question is outside these tickers or unrelated to the excerpts, say you are out of scope for uncovered names.

INSUFFICIENT DATA — reply with this EXACT sentence only (so FinChat can escalate), with no extra words:
The provided news articles do not contain enough information to answer this question.

--- BUNDLED NEWS ARTICLES (JSON) ---
{context}
--- END ARTICLES ---

User question: {query}

Answer (JSON sources only):"""


def _build_finchat_prompt_general_supplement(query: str, context: str) -> str:
    """Priority 2: JSON excerpts if any, then general financial knowledge for scoped tickers."""
    excerpt = context.strip() if context.strip() else "(No matching articles in the bundled JSON file.)"
    return f"""You are FinChat — SECONDARY / SUPPLEMENT mode.

Bundled JSON excerpts (may be partial or empty) appear below. Use them as **priority** when they clearly apply.

If excerpts are missing or insufficient, you MAY answer using **general, widely discussed public financial/market information** for these tickers only: AAPL, MSFT, AMZN, NFLX, NVDA, INTC, IBM.

REQUIREMENTS:
- Start with a short note: label which parts come from **bundled JSON** vs **general knowledge** (one line each is enough).
- No investment advice or price targets.
- Be concise and factual.

--- BUNDLED EXCERPTS (JSON, optional) ---
{excerpt}
--- END EXCERPTS ---

User question: {query}

Answer:"""


def _build_finchat_user_message(query: str, context: str, prompt_style: str) -> str:
    if prompt_style == "general_supplement":
        return _build_finchat_prompt_general_supplement(query, context)
    return _build_finchat_prompt_json_strict(query, context)


def _trim_context(context: str, prompt_style: str) -> str:
    limit = 12000 if prompt_style == "json_strict" else 9000
    return context[:limit] if len(context) > limit else context


_JSON_INSUFFICIENT_MARKERS = (
    "the provided news articles do not contain enough information",
    "provided news articles do not contain enough information",
)


def _json_answer_suggests_insufficient(text: str) -> bool:
    t = text.lower().strip()
    return any(m in t for m in _JSON_INSUFFICIENT_MARKERS)


def _attribution_label(settings: Settings) -> str:
    if settings.summarization_provider == "openrouter":
        short = settings.openrouter_model.split("/")[-1].replace("-", " ")
        return f"OpenRouter · {short}"
    return f"Gemini · {_current_model() or settings.vertex_model}"


def summarize_with_json_first_policy(
    query: str,
    context: str,
    *,
    json_match_strength: str,
) -> tuple[str, str]:
    """Run JSON-first summarization; escalate to general knowledge when needed.

    Returns:
        (answer, summarization_attribution) human-readable attribution line for UI.
    """
    settings = get_settings()
    base = _attribution_label(settings)

    if json_match_strength == "none" or not (context or "").strip():
        out = summarize_news(query, context or "", prompt_style="general_supplement")
        return out, f"{base} · general knowledge (no bundled JSON match)"

    primary = summarize_news(query, context, prompt_style="json_strict")
    if json_match_strength == "strong":
        return primary, f"{base} · summarized from bundled JSON (full_text + source links)"

    if json_match_strength in ("weak", "minimal") and _json_answer_suggests_insufficient(primary):
        logger.info("JSON-strict insufficient signal; using general supplement path.")
        secondary = summarize_news(query, context, prompt_style="general_supplement")
        return secondary, f"{base} · JSON thin — supplemented with general knowledge"

    if json_match_strength == "weak":
        return primary, f"{base} · summarized from bundled JSON (moderate title/query match)"

    return primary, f"{base} · summarized from bundled JSON (broad ticker pool; check source links)"


def _summarize_news_openrouter(query: str, context: str, *, prompt_style: str = "json_strict") -> str:
    """OpenRouter chat completions path (same prompt and cache semantics as Gemini)."""
    global _health_cache_status, _health_cache_ts
    from app.openrouter_client import openrouter_complete_user_prompt

    _initialize_concurrency_guard()
    settings = get_settings()
    if _is_circuit_open():
        raise RuntimeError("AI backend temporarily overloaded (circuit open).")

    context = _trim_context(context, prompt_style)
    model_label = settings.openrouter_model
    cache_key = _cache_key(query=query, context=context, model=f"openrouter:{model_label}:{prompt_style}")
    cached_answer = _cache_get(cache_key)
    if cached_answer is not None:
        logger.info("OpenRouter cache hit model=%s style=%s", model_label, prompt_style)
        return cached_answer

    prompt = _build_finchat_user_message(query, context, prompt_style)
    start = time.perf_counter()
    sem = _concurrency_sem
    if sem is None:
        raise RuntimeError("AI concurrency guard not initialized.")
    acquired = sem.acquire(timeout=1.5)
    if not acquired:
        raise RuntimeError("AI backend busy; too many concurrent requests.")
    try:
        text = openrouter_complete_user_prompt(settings, prompt)
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=model_label).observe(elapsed)
        _health_cache_status = "up"
        _health_cache_ts = time.time()
        _record_success()
        _set_active_model(model_label)
        _set_active_backend("openrouter")
        _cache_put(cache_key, text)
        logger.info(
            "OpenRouter response model=%s in %.2fs",
            model_label,
            elapsed,
            extra={"latency_ms": elapsed * 1000},
        )
        return text
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=model_label).observe(elapsed)
        VERTEX_ERRORS.labels(model=model_label, error_type=type(exc).__name__).inc()
        _record_failure()
        logger.error("OpenRouter call failed model=%s: %s", model_label, exc, exc_info=True)
        _health_cache_status = "degraded"
        _health_cache_ts = time.time()
        raise
    finally:
        sem.release()


def summarize_news(query: str, context: str, *, prompt_style: str = "json_strict") -> str:
    """Send a chat query with news context to Gemini / OpenRouter and return the summary.

    Args:
        query: The user's question about financial news.
        context: Concatenated relevant news articles for grounding.
        prompt_style: ``json_strict`` (bundled JSON only) or ``general_supplement`` (JSON + general knowledge).
    """
    global _health_cache_status, _health_cache_ts

    settings = get_settings()
    if settings.summarization_provider == "openrouter":
        return _summarize_news_openrouter(query, context, prompt_style=prompt_style)

    if _client is None:
        # Startup can fail if credentials are briefly unavailable; retry lazily.
        init_vertex()
    if _client is None:
        raise RuntimeError("Gemini AI initialization failed.")
    _initialize_concurrency_guard()

    if _is_circuit_open():
        raise RuntimeError("AI backend temporarily overloaded (circuit open).")

    context = _trim_context(context, prompt_style)
    model = _current_model()
    cache_key = _cache_key(query=query, context=context, model=f"{_active_backend}:{model}:{prompt_style}")
    cached_answer = _cache_get(cache_key)
    if cached_answer is not None:
        logger.info("Gemini cache hit for model=%s style=%s", model, prompt_style)
        return cached_answer

    prompt = _build_finchat_user_message(query, context, prompt_style)

    start = time.perf_counter()
    sem = _concurrency_sem
    if sem is None:
        raise RuntimeError("AI concurrency guard not initialized.")
    acquired = sem.acquire(timeout=1.5)
    if not acquired:
        raise RuntimeError("AI backend busy; too many concurrent requests.")
    try:
        vertex_failed: Optional[Exception] = None  # noqa: UP007 (py3.9 compat)
        if settings.use_vertex_ai:
            try:
                return _generate_summary_with_client(
                    _client,
                    _model_candidates(),
                    prompt,
                    query,
                    context,
                    settings,
                    start,
                    "vertexai",
                    prompt_style=prompt_style,
                )
            except Exception as v_exc:  # noqa: BLE001
                vertex_failed = v_exc
                if settings.vertex_fallback_to_api_key and settings.gemini_api_key:
                    logger.warning(
                        "Vertex summarization failed (%s); attempting Gemini API key fallback.",
                        v_exc,
                    )
                else:
                    raise

        if settings.gemini_api_key and (
            not settings.use_vertex_ai or (settings.vertex_fallback_to_api_key and vertex_failed is not None)
        ):
            api_client = _get_api_key_client() if settings.use_vertex_ai else _client
            return _generate_summary_with_client(
                api_client,
                _api_key_model_candidates(),
                prompt,
                query,
                context,
                settings,
                start,
                "apikey",
                prompt_style=prompt_style,
            )

        if vertex_failed:
            raise vertex_failed
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
            _active_backend,
            exc,
            exc_info=True,
        )
        _health_cache_status = "degraded"
        _health_cache_ts = time.time()
        raise
    finally:
        sem.release()
