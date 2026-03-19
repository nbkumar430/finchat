"""Vertex AI Gemini client for summarizing financial news."""

from __future__ import annotations

import logging
import time

import vertexai
from vertexai.generative_models import GenerativeModel

from app.config import get_settings
from app.metrics import VERTEX_ERRORS, VERTEX_LATENCY

logger = logging.getLogger(__name__)

_model: GenerativeModel | None = None


def init_vertex() -> None:
    """Initialize Vertex AI SDK."""
    global _model
    settings = get_settings()
    vertexai.init(project=settings.gcp_project_id, location=settings.gcp_region)
    _model = GenerativeModel(settings.vertex_model)
    logger.info(
        "Vertex AI initialized: project=%s, region=%s, model=%s",
        settings.gcp_project_id,
        settings.gcp_region,
        settings.vertex_model,
    )


def summarize_news(query: str, context: str) -> str:
    """Send a chat query with news context to Vertex AI and return the summary.

    Args:
        query: The user's question about financial news.
        context: Concatenated relevant news articles for grounding.

    Returns:
        The model's response text.
    """
    if _model is None:
        raise RuntimeError("Vertex AI not initialized. Call init_vertex() first.")

    settings = get_settings()
    prompt = f"""You are a helpful financial news assistant. Answer the user's question
based ONLY on the provided news articles. If the articles don't contain
enough information to answer, say so clearly.

Be concise but informative. Include relevant ticker symbols when mentioning stocks.
If multiple articles are relevant, synthesize the information.

--- NEWS ARTICLES ---
{context}
--- END ARTICLES ---

User question: {query}

Provide a clear, well-structured answer:"""

    start = time.perf_counter()
    try:
        response = _model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 1024,
                "top_p": 0.8,
            },
        )
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=settings.vertex_model).observe(elapsed)
        logger.info("Vertex AI response in %.2fs", elapsed, extra={"latency_ms": elapsed * 1000})
        return response.text
    except Exception as exc:
        elapsed = time.perf_counter() - start
        VERTEX_LATENCY.labels(model=settings.vertex_model).observe(elapsed)
        VERTEX_ERRORS.labels(model=settings.vertex_model, error_type=type(exc).__name__).inc()
        logger.error("Vertex AI call failed: %s", exc, exc_info=True)
        raise
