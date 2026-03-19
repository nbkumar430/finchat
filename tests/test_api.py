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
