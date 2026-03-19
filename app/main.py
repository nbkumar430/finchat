"""FinChat – Financial News Chat Application.

A FastAPI-powered chat application that answers questions about recent
financial news using Vertex AI (Gemini Flash) for summarization.
Instrumented with Prometheus metrics and structured logging.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import get_settings
from app.logging_config import setup_logging
from app.metrics import (
    APP_INFO,
    CHAT_REQUESTS,
    ERROR_COUNT,
    IN_FLIGHT,
    REQUEST_COUNT,
    REQUEST_DURATION,
)
from app.news_store import NewsStore
from app.schemas import (
    ArticleRef,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
)
from app.tracing import setup_tracing
from app.vertex_client import init_vertex, summarize_news

logger = logging.getLogger(__name__)

# ── Guardrail constants ───────────────────────────────────────────────
# Only these tickers are supported — derived from the bundled stock_news.json
SUPPORTED_TICKERS: frozenset[str] = frozenset({"AAPL", "MSFT", "AMZN", "NFLX", "NVDA", "INTC", "IBM"})

# Company-name synonyms mapped to their ticker for query intent detection
_TICKER_SYNONYMS: dict[str, frozenset[str]] = {
    "AAPL": frozenset({"apple", "aapl", "iphone", "ipad", "mac", "tim cook"}),
    "MSFT": frozenset({"microsoft", "msft", "azure", "windows", "satya nadella"}),
    "AMZN": frozenset({"amazon", "amzn", "aws", "prime", "andy jassy"}),
    "NFLX": frozenset({"netflix", "nflx", "streaming"}),
    "NVDA": frozenset({"nvidia", "nvda", "gpu", "jensen huang"}),
    "INTC": frozenset({"intel", "intc", "pat gelsinger"}),
    "IBM": frozenset({"ibm", "international business machines", "watson"}),
}

# Flat set of all keywords for fast query scanning
_ALL_SCOPE_KEYWORDS: frozenset[str] = frozenset(kw for synonyms in _TICKER_SYNONYMS.values() for kw in synonyms)

_OUT_OF_SCOPE_MSG = (
    "⚠️ I'm out of my scope. I can only answer questions about financial news "
    "for the following stocks: {tickers}. "
    "Please ask about one of these companies: "
    "Apple (AAPL), Microsoft (MSFT), Amazon (AMZN), Netflix (NFLX), "
    "Nvidia (NVDA), Intel (INTC), or IBM."
)


def _query_is_in_scope(query: str) -> bool:
    """Return True if the query references at least one supported company/ticker."""
    q = query.lower()
    return any(kw in q for kw in _ALL_SCOPE_KEYWORDS)


# ── Global state ─────────────────────────────────────────────────────
news_store = NewsStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    settings = get_settings()
    setup_logging(settings.log_level)

    # Load news data
    news_store.load(settings.news_json_path)
    logger.info("News store loaded successfully")

    # Initialize Vertex AI
    try:
        init_vertex()
        logger.info("Vertex AI client initialized")
    except Exception as exc:
        logger.warning("Vertex AI init failed (will retry on first request): %s", exc)

    # Set app info metric
    APP_INFO.info(
        {
            "version": settings.app_version,
            "model": settings.vertex_model,
            "project": settings.gcp_project_id,
        }
    )

    yield
    logger.info("Application shutting down")


# ── FastAPI app ──────────────────────────────────────────────────────
app = FastAPI(
    title="FinChat – Financial News Assistant",
    description=(
        "Ask questions about recent financial news and get AI-powered "
        "summary responses. Covers tickers: AAPL, MSFT, AMZN, NFLX, "
        "NVDA, INTC, IBM."
    ),
    version=get_settings().app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
setup_tracing(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files for the chat UI
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Middleware: metrics collection ───────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record request metrics for the four golden signals."""
    if request.url.path == "/metrics":
        return await call_next(request)

    IN_FLIGHT.inc()
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        status = str(response.status_code)
        endpoint = request.url.path
        method = request.method

        REQUEST_DURATION.labels(method=method, endpoint=endpoint, status=status).observe(elapsed)
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()

        if response.status_code >= 400:
            ERROR_COUNT.labels(type=f"http_{status}", endpoint=endpoint).inc()

        logger.info(
            "%s %s -> %s (%.3fs)",
            method,
            endpoint,
            status,
            elapsed,
            extra={"status_code": int(status), "latency_ms": elapsed * 1000},
        )
        return response
    except Exception as exc:
        elapsed = time.perf_counter() - start
        ERROR_COUNT.labels(type=type(exc).__name__, endpoint=request.url.path).inc()
        logger.error("Request failed: %s", exc, exc_info=True)
        raise
    finally:
        IN_FLIGHT.dec()


# ── Endpoints ────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Serve the chat UI."""
    from pathlib import Path

    html = Path("static/index.html").read_text()
    return HTMLResponse(content=html)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
    description="Returns application health status, version, and loaded data summary.",
)
async def health():
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        articles_loaded=sum(len(news_store.get_by_ticker(t, limit=100)) for t in news_store.tickers),
        tickers=news_store.tickers,
    )


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint."""
    from starlette.responses import Response

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get(
    "/api/tickers",
    response_model=list[str],
    tags=["News"],
    summary="List available tickers",
    description="Returns all stock ticker symbols available in the news dataset.",
)
async def list_tickers():
    """List all available tickers."""
    return news_store.tickers


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal error"},
        503: {"model": ErrorResponse, "description": "AI service unavailable"},
    },
    tags=["Chat"],
    summary="Ask about financial news",
    description=(
        "Submit a question about recent financial news. Optionally filter "
        "by ticker symbol. The AI will search relevant articles and provide "
        "a grounded summary."
    ),
)
async def chat(request: ChatRequest):
    """Process a chat query about financial news."""
    query = request.query.strip()
    ticker = request.ticker.upper() if request.ticker else None

    logger.info(
        "Chat request: query=%r, ticker=%s",
        query[:100],
        ticker,
        extra={"user_query": query[:100], "ticker": ticker},
    )

    # ── Guardrail 1: reject unsupported tickers immediately ───────────
    if ticker and ticker not in SUPPORTED_TICKERS:
        logger.warning("Guardrail: unsupported ticker=%s", ticker)
        return ChatResponse(
            answer=(
                f"⚠️ I'm out of my scope. Ticker '{ticker}' is not in my knowledge base. "
                f"I can only answer questions about: {', '.join(sorted(SUPPORTED_TICKERS))}."
            ),
            sources=[],
            ticker_filter=ticker,
        )

    # ── Guardrail 2: reject off-topic queries (no recognized company/ticker) ──
    if not ticker and not _query_is_in_scope(query):
        logger.warning("Guardrail: off-topic query=%r", query[:80])
        return ChatResponse(
            answer=_OUT_OF_SCOPE_MSG.format(tickers=", ".join(sorted(SUPPORTED_TICKERS))),
            sources=[],
            ticker_filter=None,
        )

    # Search for relevant articles
    articles = news_store.search(query, ticker=ticker, max_results=5)

    if not articles and ticker:
        # Fallback: get latest articles for that ticker
        articles = news_store.get_by_ticker(ticker, limit=3)

    if not articles:
        return ChatResponse(
            answer=(
                "I couldn't find any relevant news articles for your query within "
                f"the available data. Supported tickers: {', '.join(sorted(SUPPORTED_TICKERS))}."
            ),
            sources=[],
            ticker_filter=ticker,
        )

    CHAT_REQUESTS.labels(ticker=ticker or "all").inc()

    # Build context from articles
    context_parts = []
    for i, article in enumerate(articles, 1):
        context_parts.append(
            f"[Article {i}] Ticker: {article.ticker}\nTitle: {article.title}\nContent: {article.full_text[:1500]}\n"
        )
    context = "\n---\n".join(context_parts)

    # Call Vertex AI for summarization
    try:
        answer = summarize_news(query, context)
    except Exception as exc:
        logger.error("Vertex AI summarization failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="AI summarization service is temporarily unavailable. Please try again.",
        ) from exc

    sources = [ArticleRef(title=a.title, ticker=a.ticker, link=a.link) for a in articles]

    return ChatResponse(answer=answer, sources=sources, ticker_filter=ticker)
