"""In-memory news article store with simple keyword search."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.metrics import NEWS_ARTICLES_LOADED

logger = logging.getLogger(__name__)


@dataclass
class Article:
    title: str
    link: str
    ticker: str
    full_text: str


class NewsStore:
    """Loads stock_news.json and provides search capabilities."""

    def __init__(self) -> None:
        self._articles: list[Article] = []
        self._by_ticker: dict[str, list[Article]] = {}

    def load(self, path: str) -> None:
        """Load articles from JSON file."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for ticker, items in raw.items():
            ticker_articles = []
            for item in items:
                article = Article(
                    title=item["title"],
                    link=item["link"],
                    ticker=item["ticker"],
                    full_text=item.get("full_text", ""),
                )
                self._articles.append(article)
                ticker_articles.append(article)
            self._by_ticker[ticker.upper()] = ticker_articles
            NEWS_ARTICLES_LOADED.labels(ticker=ticker.upper()).set(len(ticker_articles))

        logger.info(
            "Loaded %d articles across %d tickers",
            len(self._articles),
            len(self._by_ticker),
        )

    @property
    def tickers(self) -> list[str]:
        return sorted(self._by_ticker.keys())

    def search(
        self,
        query: str,
        ticker: str | None = None,
        max_results: int = 5,
    ) -> list[Article]:
        """Search articles by keyword and optional ticker filter."""
        pool = self._by_ticker.get(ticker.upper(), []) if ticker else self._articles
        query_lower = query.lower()
        keywords = query_lower.split()

        scored: list[tuple[int, Article]] = []
        for article in pool:
            text = f"{article.title} {article.full_text}".lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scored.append((score, article))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[:max_results]]

    def get_by_ticker(self, ticker: str, limit: int = 5) -> list[Article]:
        """Get latest articles for a specific ticker."""
        return self._by_ticker.get(ticker.upper(), [])[:limit]
