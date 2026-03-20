"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Chat request payload."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="User's question about financial news",
        json_schema_extra={"example": "What is the latest news about Apple?"},
    )
    ticker: Optional[str] = Field(  # noqa: UP007 (py3.9 compat)
        None,
        description="Optional ticker symbol to filter news (e.g., AAPL, MSFT)",
        json_schema_extra={"example": "AAPL"},
    )
    session_id: Optional[str] = Field(  # noqa: UP007
        None,
        description="Existing chat thread ID from POST /api/sessions or prior ChatResponse",
        max_length=36,
    )


class ArticleRef(BaseModel):
    """Reference to a source article."""

    title: str
    ticker: str
    link: str


class ChatResponse(BaseModel):
    """Chat response payload."""

    answer: str = Field(..., description="AI-generated summary answer")
    sources: list[ArticleRef] = Field(
        default_factory=list,
        description="Source articles used for the answer",
    )
    ticker_filter: Optional[str] = Field(  # noqa: UP007 (py3.9 compat)
        None,
        description="Ticker filter applied, if any",
    )
    fallback_mode: bool = Field(
        False,
        description="True when a degraded headline-only fallback is used (rare)",
    )
    answer_source: str = Field(
        "gemini",
        description="gemini | openrouter | extractive | headlines — how the answer was produced",
    )
    summarization_attribution: Optional[str] = Field(  # noqa: UP007
        None,
        description="Human-readable line: model + whether answer is JSON-grounded or supplemented",
    )
    session_id: Optional[str] = Field(  # noqa: UP007
        None,
        description="Chat thread ID when persistence is enabled; pass on the next request to continue the thread",
    )


class SessionCreateResponse(BaseModel):
    """Response after creating a new chat session."""

    session_id: str
    created_at: datetime


class RegisterRequest(BaseModel):
    """Create an account (first-time / additional users)."""

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Letters, numbers, underscore, hyphen",
    )
    passcode: str = Field(..., min_length=4, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    passcode: str = Field(..., min_length=1, max_length=128)


class AuthUserRead(BaseModel):
    username: str
    is_admin: bool


class AuthMeResponse(BaseModel):
    user: Optional[AuthUserRead] = None  # noqa: UP007


class ChatSessionSummary(BaseModel):
    session_id: str
    title: Optional[str] = None  # noqa: UP007
    updated_at: datetime
    owner_username: Optional[str] = None  # noqa: UP007 — set for admin view (all users)


class SessionListResponse(BaseModel):
    sessions: list[ChatSessionSummary]


class AdminTraceabilityResponse(BaseModel):
    """Admin-only links tying chat traffic to Prometheus metrics + Grafana dashboards."""

    grafana_home_url: str = Field(..., description="Grafana UI (Cloud Run service)")
    grafana_golden_signals_url: str = Field(
        ...,
        description="Deep link to the FinChat four golden signals dashboard",
    )
    app_metrics_url: str = Field(
        ...,
        description="FinChat Prometheus exposition endpoint (raw metrics text)",
    )
    api_docs_url: str = Field(default="/docs", description="OpenAPI docs path (same origin as FinChat app)")
    traceability_note: str = Field(
        default=(
            "Flow: FinChat HTTP handlers emit Prometheus metrics on /metrics → "
            "Prometheus sidecar inside the Grafana Cloud Run container scrapes that URL → "
            "Grafana queries the local Prometheus (Golden Signals dashboard). "
            "Log in to Grafana as admin if you need to edit dashboards (password in Secret Manager / bootstrap)."
        ),
    )


class ChatMessageRead(BaseModel):
    """One persisted chat turn (user or assistant)."""

    id: int
    role: str
    content: str
    ticker_filter: Optional[str] = None  # noqa: UP007
    answer_source: Optional[str] = None  # noqa: UP007
    fallback_mode: Optional[bool] = None  # noqa: UP007
    summarization_attribution: Optional[str] = None  # noqa: UP007
    sources: list[ArticleRef] = Field(default_factory=list)
    created_at: datetime


class MessagesListResponse(BaseModel):
    """Paginated-style list of messages for a session."""

    session_id: str
    messages: list[ChatMessageRead]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = ""
    articles_loaded: int = 0
    tickers: list[str] = []
    ai_backend_status: str = "degraded"


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None  # noqa: UP007 (py3.9 compat)
