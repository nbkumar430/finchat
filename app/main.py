"""FinChat – Financial News Chat Application.

A FastAPI-powered chat application that answers questions about recent
financial news using Vertex AI (Gemini Flash) for summarization.
Instrumented with Prometheus metrics and structured logging.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from app import chat_repository
from app.auth_deps import (
    get_current_user_for_persistence,
    get_current_user_optional,
    get_db_required,
    require_admin_user,
)
from app.auth_tokens import create_auth_token, verify_passcode
from app.chat_storage_gcs import backup_chat_db_if_configured, restore_chat_db_if_configured
from app.config import get_settings
from app.database import SessionLocal, configure_engine, get_db, init_db
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
from app.orm_models import UserORM
from app.schemas import (
    AdminTraceabilityResponse,
    ArticleRef,
    AuthMeResponse,
    AuthUserRead,
    ChatRequest,
    ChatResponse,
    ChatSessionSummary,
    ErrorResponse,
    HealthResponse,
    LoginRequest,
    MessagesListResponse,
    RegisterRequest,
    SessionCreateResponse,
    SessionListResponse,
)
from app.tracing import setup_tracing
from app.vertex_client import get_vertex_backend_status, init_vertex, summarize_with_json_first_policy

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

_COMMON_WEAK_PASSCODES: frozenset[str] = frozenset(
    {
        "admin",
        "password",
        "password123",
        "12345678",
        "123456789",
        "qwerty",
        "letmein",
        "welcome",
    }
)


def _validate_register_passcode(username: str, passcode: str) -> None:
    """Reject weak passcodes that trigger browser compromise warnings often."""
    candidate = passcode.strip()
    uname = username.strip().lower()
    if len(candidate) < 8:
        raise HTTPException(status_code=400, detail="Passcode must be at least 8 characters")
    if candidate.lower() in _COMMON_WEAK_PASSCODES or candidate.lower() == uname:
        raise HTTPException(status_code=400, detail="Choose a stronger passcode")
    # Require mixed character classes to discourage trivial/reused credentials.
    if not re.search(r"[A-Z]", candidate) or not re.search(r"[a-z]", candidate):
        raise HTTPException(status_code=400, detail="Use both uppercase and lowercase letters")
    if not re.search(r"\d", candidate):
        raise HTTPException(status_code=400, detail="Include at least one number")


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


def _set_auth_cookie(response: Response, token: str) -> None:
    st = get_settings()
    response.set_cookie(
        key=st.auth_cookie_name,
        value=token,
        max_age=st.auth_token_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=st.auth_cookie_secure,
        path="/",
    )


def _traceability_payload(request: Request) -> AdminTraceabilityResponse:
    """Build absolute URLs for Grafana + metrics (admin observability)."""
    st = get_settings()
    app_base = (st.app_public_base_url or str(request.base_url)).rstrip("/")
    g_base = st.grafana_public_url.strip()
    uid = st.grafana_golden_signals_uid
    if g_base:
        grafana_home = f"{g_base}/"
        golden = f"{g_base}/d/{uid}"
    else:
        grafana_home = "(Set env GRAFANA_PUBLIC_URL to your finchat-grafana Cloud Run URL)"
        golden = f"(Set GRAFANA_PUBLIC_URL)/d/{uid}"
    return AdminTraceabilityResponse(
        grafana_home_url=grafana_home,
        grafana_golden_signals_url=golden,
        app_metrics_url=f"{app_base}/metrics",
        api_docs_url=f"{app_base}/docs",
    )


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

    if SessionLocal is not None:
        with SessionLocal() as db_boot:
            chat_repository.seed_default_admin(
                db_boot,
                passcode=settings.admin_initial_passcode,
            )

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


@app.get(
    "/api/auth/config",
    tags=["Auth"],
    summary="Whether login is required for this deployment",
)
async def auth_config():
    st = get_settings()
    return {
        "require_auth": st.require_auth,
        "guest_chat_allowed": not st.require_auth,
    }


@app.get(
    "/api/auth/me",
    response_model=AuthMeResponse,
    tags=["Auth"],
    summary="Current session identity",
)
async def auth_me(
    user: Annotated[Optional[UserORM], Depends(get_current_user_optional)],
    db: Annotated[Optional[Session], Depends(get_db)],
):
    if db is None or user is None:
        return AuthMeResponse(user=None)
    return AuthMeResponse(user=AuthUserRead(username=user.username, is_admin=user.is_admin))


@app.post(
    "/api/auth/register",
    response_model=AuthMeResponse,
    tags=["Auth"],
    summary="Register a new user (passcode login)",
)
async def auth_register(body: RegisterRequest, db: Annotated[Session, Depends(get_db_required)]):
    if chat_repository.get_user_by_username(db, body.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken")
    _validate_register_passcode(body.username, body.passcode)
    user = chat_repository.create_user(db, username=body.username, passcode=body.passcode, is_admin=False)
    token = create_auth_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        secret=get_settings().auth_secret,
        max_age_seconds=get_settings().auth_token_max_age_seconds,
    )
    payload = AuthMeResponse(user=AuthUserRead(username=user.username, is_admin=user.is_admin))
    resp = JSONResponse(payload.model_dump(mode="json"))
    _set_auth_cookie(resp, token)
    return resp


@app.post(
    "/api/auth/login",
    response_model=AuthMeResponse,
    tags=["Auth"],
    summary="Passcode login (sets HttpOnly cookie)",
)
async def auth_login(body: LoginRequest, db: Annotated[Session, Depends(get_db_required)]):
    user = chat_repository.get_user_by_username(db, body.username)
    if user is None or not verify_passcode(body.passcode, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or passcode")
    token = create_auth_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        secret=get_settings().auth_secret,
        max_age_seconds=get_settings().auth_token_max_age_seconds,
    )
    payload = AuthMeResponse(user=AuthUserRead(username=user.username, is_admin=user.is_admin))
    resp = JSONResponse(payload.model_dump(mode="json"))
    _set_auth_cookie(resp, token)
    return resp


@app.post("/api/auth/logout", tags=["Auth"])
async def auth_logout():
    resp = JSONResponse({"ok": True})
    st = get_settings()
    resp.delete_cookie(st.auth_cookie_name, path="/", samesite="lax", secure=st.auth_cookie_secure)
    return resp


@app.get(
    "/api/admin/traceability",
    response_model=AdminTraceabilityResponse,
    tags=["Admin"],
    summary="Observability deep links (admin only)",
    description=(
        "Returns FinChat /metrics URL, Grafana home, and Golden Signals dashboard link. "
        "Requires admin. Set GRAFANA_PUBLIC_URL and FINCHAT_APP_PUBLIC_URL on Cloud Run for absolute URLs."
    ),
)
async def admin_traceability(
    request: Request,
    _admin: Annotated[UserORM, Depends(require_admin_user)],
):
    return _traceability_payload(request)


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
    user: Annotated[Optional[UserORM], Depends(get_current_user_for_persistence)],
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
    settings_chat = get_settings()
    if db is not None:
        if user is None:
            if request.session_id:
                raise HTTPException(status_code=401, detail="Sign in to continue a saved chat")
            if settings_chat.require_auth:
                raise HTTPException(status_code=401, detail="Login required")
        else:
            if request.session_id:
                sess = chat_repository.get_session_for_access(db, request.session_id, user)
                if sess is None:
                    raise HTTPException(status_code=404, detail="Unknown session_id")
                session_id_out = sess.id
            else:
                sess = chat_repository.create_session(db, user.id)
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
        summarization_attribution: Optional[str] = None,
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
                    summarization_attribution=summarization_attribution,
                )
            except Exception as exc:
                logger.warning("Chat persistence failed (assistant turn): %s", exc)
        return ChatResponse(
            answer=answer,
            sources=sources,
            ticker_filter=ticker,
            fallback_mode=fallback_mode,
            answer_source=answer_source,
            summarization_attribution=summarization_attribution,
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

    # JSON-first retrieval (title-weighted); match_strength drives strict → general escalation
    articles, json_match_strength = news_store.search_json_priority(query, ticker=ticker, max_results=5)
    if not articles and ticker:
        articles = news_store.get_by_ticker(ticker, limit=5)
        json_match_strength = "minimal" if articles else "none"

    CHAT_REQUESTS.labels(ticker=ticker or "all").inc()
    chat_settings = get_settings()
    answer_source = "openrouter" if chat_settings.summarization_provider == "openrouter" else "gemini"

    sources = [ArticleRef(title=a.title, ticker=a.ticker, link=a.link) for a in articles]
    fallback_mode = False
    summarization_attribution: Optional[str] = None

    if articles:
        context_parts = []
        for i, article in enumerate(articles, 1):
            body = article.full_text or ""
            cap = 4500
            snippet = body[:cap] + ("…" if len(body) > cap else "")
            context_parts.append(
                f"[Article {i}] Ticker: {article.ticker}\n"
                f"Title: {article.title}\n"
                f"Source URL: {article.link}\n"
                f"Full text:\n{snippet}\n"
            )
        context = "\n---\n".join(context_parts)
    else:
        context = ""

    try:
        answer, summarization_attribution = await run_in_threadpool(
            summarize_with_json_first_policy,
            query,
            context,
            json_match_strength=json_match_strength,
        )
    except Exception as exc:
        logger.warning(
            "LLM summarization failed; using extractive TF-IDF summary from JSON: %s",
            exc,
        )
        ERROR_COUNT.labels(type="vertex_ai_unavailable", endpoint="/api/chat").inc()
        if not articles:
            answer = (
                "I couldn't answer from the bundled news file or the AI service. "
                f"Supported tickers: {', '.join(sorted(SUPPORTED_TICKERS))}."
            )
            summarization_attribution = None
        else:
            try:
                answer = await run_in_threadpool(build_extractive_answer, query, articles, ticker)
                answer_source = "extractive"
                summarization_attribution = "Extractive TF-IDF · bundled JSON only (generative model unavailable)"
            except Exception as ex2:
                logger.error("Extractive summarization failed: %s", ex2, exc_info=True)
                answer = _build_headline_only_answer(query=query, sources=sources)
                answer_source = "headlines"
                fallback_mode = True
                summarization_attribution = "Headlines only · bundled JSON (degraded)"

    return respond(
        answer=answer,
        sources=sources,
        answer_source=answer_source,
        fallback_mode=fallback_mode,
        summarization_attribution=summarization_attribution,
    )


@app.get(
    "/api/sessions",
    response_model=SessionListResponse,
    tags=["Chat"],
    summary="List your chat threads",
    description=("Most recently updated first. Admins see every user's sessions with ``owner_username`` set."),
)
async def list_chat_sessions_endpoint(
    user: Annotated[Optional[UserORM], Depends(get_current_user_for_persistence)],
    db: Annotated[Session, Depends(get_db_required)],
):
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to see saved chats")
    rows = chat_repository.list_chat_sessions(db, user)
    summaries: list[ChatSessionSummary] = []
    for r in rows:
        owner_username = r.owner.username if (user.is_admin and r.owner) else None
        summaries.append(
            ChatSessionSummary(
                session_id=r.id,
                title=r.title,
                updated_at=r.updated_at,
                owner_username=owner_username,
            )
        )
    return SessionListResponse(sessions=summaries)


@app.post(
    "/api/sessions",
    response_model=SessionCreateResponse,
    tags=["Chat"],
    summary="Create a chat session",
    description="Create an empty chat thread. Optional: omit otherwise /api/chat creates one automatically.",
)
async def create_chat_session_endpoint(
    user: Annotated[Optional[UserORM], Depends(get_current_user_for_persistence)],
    db: Annotated[Session, Depends(get_db_required)],
):
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to create a saved chat")
    row = chat_repository.create_session(db, user.id)
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
    user: Annotated[Optional[UserORM], Depends(get_current_user_for_persistence)],
    db: Annotated[Session, Depends(get_db_required)],
):
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to load saved chats")
    if chat_repository.get_session_for_access(db, session_id, user) is None:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    rows = chat_repository.list_messages(db, session_id)
    return MessagesListResponse(
        session_id=session_id,
        messages=[chat_repository.orm_message_to_read(m) for m in rows],
    )
