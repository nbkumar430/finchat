"""FinChat – Financial News Chat Application.

A FastAPI-powered chat application that answers questions about recent
financial news using Vertex AI (Gemini Flash) for summarization.
Instrumented with Prometheus metrics and structured logging.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from app import chat_repository
from app.chat_storage_gcs import backup_chat_db_if_configured, restore_chat_db_if_configured
from app.config import get_settings
from app.database import configure_engine, get_db, init_db
from app.local_summarizer import build_extractive_answer
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
    MessagesListResponse,
    SessionCreateResponse,
)
from app.tracing import setup_tracing
from app.vertex_client import get_vertex_backend_status, init_vertex, summarize_news

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


def _build_headline_only_answer(query: str, sources: list[ArticleRef]) -> str:
    """Last-resort answer from titles only (should be rare if extractive path works)."""
    source_lines = [f"- {src.ticker}: {src.title}" for src in sources[:5]]
    source_text = "\n".join(source_lines) if source_lines else "- No matching articles."
    return (
        f"Here are the closest matches in the dataset for: **{query}**\n\n"
        f"{source_text}\n\n"
        "_Showing headlines only — article text could not be summarized automatically._"
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

    # Chat persistence: SQLite (+ optional GCS restore before schema init)
    configure_engine(settings)
    restore_chat_db_if_configured(settings)
    init_db()

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
    backup_chat_db_if_configured(get_settings())
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
        ai_backend_status=get_vertex_backend_status(),
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
        "a grounded summary. "
        "Pass session_id to continue a thread (see POST /api/sessions)."
    ),
)
async def chat(
    request: ChatRequest,
    db: Annotated[Optional[Session], Depends(get_db)],
):
    """Process a chat query about financial news."""
    query = request.query.strip()
    ticker = request.ticker.upper() if request.ticker else None

    logger.info(
        "Chat request: query=%r, ticker=%s",
        query[:100],
        ticker,
        extra={"user_query": query[:100], "ticker": ticker},
    )

    session_id_out: Optional[str] = None
    if db is not None:
        if request.session_id:
            sess = chat_repository.get_session(db, request.session_id)
            if sess is None:
                raise HTTPException(status_code=404, detail="Unknown session_id")
            session_id_out = sess.id
        else:
            sess = chat_repository.create_session(db)
            session_id_out = sess.id
        try:
            chat_repository.append_message(
                db,
                session_id=session_id_out,
                role="user",
                content=query,
                ticker_filter=ticker,
            )
        except Exception as exc:
            logger.warning("Chat persistence failed (user turn): %s", exc)

    def respond(
        answer: str,
        sources: list[ArticleRef],
        answer_source: str,
        *,
        fallback_mode: bool = False,
    ) -> ChatResponse:
        if db is not None and session_id_out:
            try:
                chat_repository.append_message(
                    db,
                    session_id=session_id_out,
                    role="assistant",
                    content=answer,
                    ticker_filter=ticker,
                    answer_source=answer_source,
                    sources=sources or None,
                    fallback_mode=fallback_mode,
                )
            except Exception as exc:
                logger.warning("Chat persistence failed (assistant turn): %s", exc)
        return ChatResponse(
            answer=answer,
            sources=sources,
            ticker_filter=ticker,
            fallback_mode=fallback_mode,
            answer_source=answer_source,
            session_id=session_id_out,
        )

    # ── Guardrail 1: reject unsupported tickers immediately ───────────
    if ticker and ticker not in SUPPORTED_TICKERS:
        logger.warning("Guardrail: unsupported ticker=%s", ticker)
        return respond(
            answer=(
                f"⚠️ I'm out of my scope. Ticker '{ticker}' is not in my knowledge base. "
                f"I can only answer questions about: {', '.join(sorted(SUPPORTED_TICKERS))}."
            ),
            sources=[],
            answer_source="headlines",
        )

    # ── Guardrail 2: reject off-topic queries (no recognized company/ticker) ──
    if not ticker and not _query_is_in_scope(query):
        logger.warning("Guardrail: off-topic query=%r", query[:80])
        return respond(
            answer=_OUT_OF_SCOPE_MSG.format(tickers=", ".join(sorted(SUPPORTED_TICKERS))),
            sources=[],
            answer_source="headlines",
        )

    # Search for relevant articles
    articles = news_store.search(query, ticker=ticker, max_results=5)

    if not articles and ticker:
        # Fallback: get latest articles for that ticker
        articles = news_store.get_by_ticker(ticker, limit=3)

    if not articles:
        return respond(
            answer=(
                "I couldn't find any relevant news articles for your query within "
                f"the available data. Supported tickers: {', '.join(sorted(SUPPORTED_TICKERS))}."
            ),
            sources=[],
            answer_source="headlines",
        )

    CHAT_REQUESTS.labels(ticker=ticker or "all").inc()

    # Build context from articles
    context_parts = []
    for i, article in enumerate(articles, 1):
        context_parts.append(
            f"[Article {i}] Ticker: {article.ticker}\nTitle: {article.title}\nContent: {article.full_text[:1500]}\n"
        )
    context = "\n---\n".join(context_parts)

    # Call LLM (Vertex / Gemini API / OpenRouter) for summarization
    sources = [ArticleRef(title=a.title, ticker=a.ticker, link=a.link) for a in articles]
    fallback_mode = False
    chat_settings = get_settings()
    answer_source = (
        "openrouter" if chat_settings.summarization_provider == "openrouter" else "gemini"
    )
    try:
        # Offload sync model call to threadpool to avoid blocking event loop under load.
        answer = await run_in_threadpool(summarize_news, query, context)
    except Exception as exc:
        logger.warning(
            "LLM summarization failed; using extractive TF-IDF summary from JSON: %s",
            exc,
        )
        ERROR_COUNT.labels(type="vertex_ai_unavailable", endpoint="/api/chat").inc()
        try:
            answer = await run_in_threadpool(build_extractive_answer, query, articles, ticker)
            answer_source = "extractive"
            fallback_mode = False
        except Exception as ex2:
            logger.error("Extractive summarization failed: %s", ex2, exc_info=True)
            answer = _build_headline_only_answer(query=query, sources=sources)
            answer_source = "headlines"
            fallback_mode = True

    return respond(
        answer=answer,
        sources=sources,
        answer_source=answer_source,
        fallback_mode=fallback_mode,
    )


@app.post(
    "/api/sessions",
    response_model=SessionCreateResponse,
    tags=["Chat"],
    summary="Create a chat session",
    description="Create an empty chat thread. Optional: omit otherwise /api/chat creates one automatically.",
)
async def create_chat_session_endpoint(db: Annotated[Optional[Session], Depends(get_db)]):
    if db is None:
        raise HTTPException(status_code=503, detail="Chat session persistence is disabled")
    row = chat_repository.create_session(db)
    return SessionCreateResponse(session_id=row.id, created_at=row.created_at)


@app.get(
    "/api/sessions/{session_id}/messages",
    response_model=MessagesListResponse,
    tags=["Chat"],
    summary="List messages in a session",
    description="Return persisted turns for a session (user and assistant), oldest first.",
)
async def list_chat_session_messages(
    session_id: str,
    db: Annotated[Optional[Session], Depends(get_db)],
):
    if db is None:
        raise HTTPException(status_code=503, detail="Chat session persistence is disabled")
    if chat_repository.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    rows = chat_repository.list_messages(db, session_id)
    return MessagesListResponse(
        session_id=session_id,
        messages=[chat_repository.orm_message_to_read(m) for m in rows],
    )
