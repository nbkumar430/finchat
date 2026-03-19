"""Tests for the FastAPI endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with mocked Vertex AI."""
    with patch("app.main.init_vertex"):
        from app.main import app

        with TestClient(app) as c:
            yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "tickers" in data
    assert isinstance(data["tickers"], list)


def test_tickers_endpoint(client):
    resp = client.get("/api/tickers")
    assert resp.status_code == 200
    tickers = resp.json()
    assert isinstance(tickers, list)
    assert len(tickers) > 0


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "finchat_http_requests_total" in resp.text


def test_openapi_spec(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "FinChat – Financial News Assistant"
    assert "/api/chat" in spec["paths"]


def test_chat_missing_query(client):
    resp = client.post("/api/chat", json={"query": ""})
    assert resp.status_code == 422


@patch("app.main.summarize_news")
def test_chat_success(mock_summarize, client):
    mock_summarize.return_value = "Apple announced a new product."
    resp = client.post("/api/chat", json={"query": "What is Apple doing?", "ticker": "AAPL"})
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "sources" in data


@patch("app.main.summarize_news", side_effect=Exception("API error"))
def test_chat_vertex_failure(mock_summarize, client):
    resp = client.post("/api/chat", json={"query": "Tell me about Apple"})
    assert resp.status_code == 503


def test_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "FinChat" in resp.text


# ── Guardrail tests ───────────────────────────────────────────────────

def test_guardrail_unsupported_ticker(client):
    """Requests for tickers not in the dataset must be rejected gracefully."""
    for bad_ticker in ("TSLA", "GOOGL", "META", "AMGN", "XYZ"):
        resp = client.post("/api/chat", json={"query": f"Tell me about {bad_ticker}", "ticker": bad_ticker})
        assert resp.status_code == 200, f"Expected 200 for ticker {bad_ticker}"
        data = resp.json()
        answer = data["answer"].lower()
        assert "scope" in answer or "not in my knowledge" in answer, (
            f"Expected out-of-scope message for ticker {bad_ticker}, got: {data['answer']}"
        )
        assert data["sources"] == [], f"Expected no sources for unsupported ticker {bad_ticker}"


def test_guardrail_off_topic_query(client):
    """Completely off-topic queries must be rejected with an out-of-scope message."""
    off_topic_queries = [
        "What is the weather today?",
        "Give me a recipe for pasta",
        "Who won the football game last night?",
        "Tell me a joke",
        "What is 2 + 2?",
    ]
    for query in off_topic_queries:
        resp = client.post("/api/chat", json={"query": query})
        assert resp.status_code == 200, f"Expected 200 for off-topic query: {query}"
        data = resp.json()
        answer = data["answer"].lower()
        assert "scope" in answer, (
            f"Expected out-of-scope message for query '{query}', got: {data['answer']}"
        )
        assert data["sources"] == [], f"Expected no sources for off-topic query: {query}"


def test_guardrail_supported_tickers_pass(client):
    """Queries about supported tickers must pass the guardrail and reach the AI."""
    supported = [
        ("What is Apple doing?", "AAPL"),
        ("Latest Microsoft news", "MSFT"),
        ("Amazon earnings", "AMZN"),
        ("Netflix subscriber growth", "NFLX"),
        ("Nvidia GPU outlook", "NVDA"),
        ("Intel revenue", "INTC"),
        ("IBM cloud strategy", "IBM"),
    ]
    for query, ticker in supported:
        with patch("app.main.summarize_news", return_value=f"Summary for {ticker}."):
            resp = client.post("/api/chat", json={"query": query, "ticker": ticker})
            assert resp.status_code == 200, f"Expected 200 for {ticker}"
            data = resp.json()
            assert "scope" not in data["answer"].lower(), (
                f"Supported ticker {ticker} was incorrectly blocked: {data['answer']}"
            )


def test_guardrail_company_name_in_query_passes(client):
    """Queries that mention a company name (no explicit ticker) must pass the guardrail."""
    company_queries = [
        "What is Apple's latest news?",
        "Tell me about Microsoft's earnings",
        "Amazon AWS performance",
        "Netflix content strategy",
        "Nvidia revenue growth",
        "Intel chip roadmap",
    ]
    for query in company_queries:
        with patch("app.main.summarize_news", return_value="Some financial summary."):
            resp = client.post("/api/chat", json={"query": query})
            assert resp.status_code in (200, 503), f"Unexpected status for query: {query}"
            data = resp.json()
            assert "scope" not in data.get("answer", "").lower(), (
                f"Company-name query '{query}' was incorrectly blocked"
            )


def test_guardrail_case_insensitive_ticker(client):
    """Ticker matching must be case-insensitive."""
    for ticker_input in ("aapl", "Aapl", "AAPL"):
        with patch("app.main.summarize_news", return_value="Apple summary."):
            resp = client.post("/api/chat", json={"query": "Apple news", "ticker": ticker_input})
            assert resp.status_code == 200
            data = resp.json()
            assert "scope" not in data["answer"].lower(), (
                f"Ticker {ticker_input} was incorrectly blocked"
            )
