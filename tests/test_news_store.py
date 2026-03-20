"""Tests for the news store module."""

import json
import tempfile

import pytest

from app.news_store import NewsStore


@pytest.fixture
def sample_data():
    return {
        "AAPL": [
            {
                "title": "Apple launches new iPhone",
                "link": "https://example.com/1",
                "ticker": "AAPL",
                "full_text": "Apple Inc announced a new iPhone model with AI features.",
            },
            {
                "title": "Apple earnings beat expectations",
                "link": "https://example.com/2",
                "ticker": "AAPL",
                "full_text": "Apple reported quarterly earnings above analyst expectations.",
            },
        ],
        "MSFT": [
            {
                "title": "Microsoft Azure growth accelerates",
                "link": "https://example.com/3",
                "ticker": "MSFT",
                "full_text": "Microsoft cloud platform Azure saw 30% growth in revenue.",
            },
        ],
    }


@pytest.fixture
def store(sample_data):
    s = NewsStore()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_data, f)
        f.flush()
        s.load(f.name)
    return s


def test_load_articles(store):
    assert len(store.tickers) == 2
    assert "AAPL" in store.tickers
    assert "MSFT" in store.tickers


def test_get_by_ticker(store):
    aapl = store.get_by_ticker("AAPL")
    assert len(aapl) == 2
    assert aapl[0].ticker == "AAPL"


def test_get_by_ticker_case_insensitive(store):
    aapl = store.get_by_ticker("aapl")
    assert len(aapl) == 2


def test_get_by_ticker_unknown(store):
    result = store.get_by_ticker("GOOG")
    assert result == []


def test_search_by_keyword(store):
    results = store.search("iPhone")
    assert len(results) >= 1
    assert any("iPhone" in a.title for a in results)


def test_search_with_ticker_filter(store):
    results = store.search("growth", ticker="MSFT")
    assert len(results) >= 1
    assert all(a.ticker == "MSFT" for a in results)


def test_search_no_results(store):
    results = store.search("quantum computing blockchain")
    assert results == []


def test_search_respects_max_results(store):
    results = store.search("Apple", max_results=1)
    assert len(results) <= 1


def test_search_json_priority_returns_strength(store):
    arts, strength = store.search_json_priority("iPhone", max_results=2)
    assert len(arts) >= 1
    assert strength in ("strong", "weak", "minimal", "none")


def test_search_json_priority_ticker_fallback_minimal(store):
    """Ticker filter with vague query still returns pool with minimal strength."""
    arts, strength = store.search_json_priority("zzzthing", ticker="AAPL", max_results=3)
    assert len(arts) >= 1
    assert strength == "minimal"
