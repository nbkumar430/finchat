"""Tests for JSON-grounded extractive summarizer."""

from app.local_summarizer import build_extractive_answer
from app.news_store import Article


def test_extractive_uses_article_sentences_only():
    articles = [
        Article(
            title="Apple supply update",
            link="https://example.com/a",
            ticker="AAPL",
            full_text=(
                "Apple expanded manufacturing partners in Asia. "
                "The company also cited strong demand for its phones. "
                "Analysts noted margin pressure in the quarter."
            ),
        ),
    ]
    out = build_extractive_answer("What is Apple doing with manufacturing?", articles, "AAPL")
    assert "tf" in out.lower() and "idf" in out.lower()
    assert "Apple expanded" in out or "demand" in out
    assert "•" in out


def test_extractive_empty_articles():
    out = build_extractive_answer("test?", [], None)
    assert "don" in out.lower() or "Matching" in out or "matching" in out
