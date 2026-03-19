"""Pydantic models for API request/response schemas."""

from __future__ import annotations

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


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = ""
    articles_loaded: int = 0
    tickers: list[str] = []


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None  # noqa: UP007 (py3.9 compat)
