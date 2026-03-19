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
from app.vertex_client import init_vertex, summarize_news

logger = logging.getLogger(__name__)

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
    APP_INFO.info({
        "version": settings.app_version,
        "model": settings.vertex_model,
        "project": settings.gcp_project_id,
    })

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

    # Search for relevant articles
    articles = news_store.search(query, ticker=ticker, max_results=5)

    if not articles and ticker:


        # Fallback: get latest articles for that ticker
            articles = news_store.get_by_ticker(ticker, limit=3)

    if not articles:
        return ChatResponse(
            answer="I couldn't find any relevant news articles for your query. "
            f"Available tickers: {', '.join(news_store.tickers)}",
            sources=[],
            ticker_filter=ticker,
        )

    CHAT_REQUESTS.labels(ticker=ticker or "all").inc()

    # Build context from articles
    context_parts = []
    for i, article in enumerate(articles, 1):
        context_parts.append(
            f"[Article {i}] Ticker: {article.ticker}\n"
            f"Title: {article.title}\n"
            f"Content: {article.full_text[:1500]}\n"
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

    sources = [
        ArticleRef(title=a.title, ticker=a.ticker, link=a.link)
        for a in articles
    ]

    return ChatResponse(answer=answer, sources=sources, ticker_filter=ticker)
