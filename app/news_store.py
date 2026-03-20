"""In-memory news article store — JSON-first search with title-weighted ranking."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.metrics import NEWS_ARTICLES_LOADED

logger = logging.getLogger(__name__)

# Trimmed query noise so "NFLX news" still boosts the ticker / tokens that matter
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "am",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "how",
        "why",
        "and",
        "or",
        "but",
        "if",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "up",
        "down",
        "in",
        "out",
        "on",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "about",
        "into",
        "news",
        "latest",
        "tell",
        "me",
        "give",
        "get",
        "show",
        "summary",
        "summarize",
        "please",
        "can",
        "could",
    }
)


@dataclass
class Article:
    title: str
    link: str
    ticker: str
    full_text: str


def _query_keywords(query: str) -> list[str]:
    """Meaningful tokens for matching (favors title ~ user question)."""
    raw = re.findall(r"[a-z0-9]{2,}", query.lower())
    return [w for w in raw if w not in _STOPWORDS]


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
        """Search articles by keyword and optional ticker filter (legacy)."""
        articles, _strength = self.search_json_priority(query, ticker=ticker, max_results=max_results)
        return articles

    def search_json_priority(
        self,
        query: str,
        ticker: str | None = None,
        max_results: int = 5,
    ) -> tuple[list[Article], str]:
        """Rank articles for bundled JSON-first answers.

        Returns:
            (articles, match_strength): match_strength is ``strong`` | ``weak`` | ``minimal`` | ``none``
            - strong: good title / question overlap (prefer summarizing full_text + link)
            - weak: related hits but loose title match
            - minimal: low signal within matched set, or broad ticker pool fallback
        """
        # Ignore tiny SequenceMatcher noise between unrelated strings
        title_sim_meaningful = 0.25

        pool = self._by_ticker.get(ticker.upper(), []) if ticker else self._articles
        kws = _query_keywords(query)
        if ticker:
            t = ticker.upper()
            kws = list(dict.fromkeys([*(w.lower() for w in kws), t.lower()]))

        qnorm = query.strip().lower()
        # (rank_score, text_match_score, article) — ticker bonus affects order only, not strength tier
        scored: list[tuple[float, float, Article]] = []

        for article in pool:
            title_l = article.title.lower()
            body_l = article.full_text.lower()

            title_kw_hits = sum(5 for kw in kws if kw and kw in title_l)
            body_kw_hits = sum(1 for kw in kws if kw and kw in body_l)

            title_sim = SequenceMatcher(None, qnorm[:400], title_l[:400]).ratio()
            kw_hit = title_kw_hits + body_kw_hits > 0
            # Global search (no ticker): require a real keyword hit — title-only similarity
            # is too noisy vs unrelated long questions. Scoped ticker search still allows
            # high title similarity when the user paraphrases the headline.
            if ticker:
                meaningful = kw_hit or title_sim >= title_sim_meaningful
            else:
                meaningful = kw_hit

            if not meaningful:
                continue

            match_score = float(title_kw_hits + body_kw_hits + title_sim * 15.0)
            rank_score = match_score
            if ticker and article.ticker.upper() == ticker.upper():
                rank_score += 3.0

            scored.append((rank_score, match_score, article))

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            if ticker and pool:
                return pool[:max_results], "minimal"
            return [], "none"

        _top_rank, top_match, top_art = scored[0]
        articles = [a for _, _, a in scored[:max_results]]

        title_sim_top = SequenceMatcher(None, qnorm[:400], top_art.title.lower()[:400]).ratio()
        strong = top_match >= 14.0 or (top_match >= 8.0 and title_sim_top >= 0.28)
        if strong:
            strength = "strong"
        elif top_match >= 5.0:
            strength = "weak"
        else:
            strength = "minimal"

        return articles, strength

    def get_by_ticker(self, ticker: str, limit: int = 5) -> list[Article]:
        """Get latest articles for a specific ticker."""
        return self._by_ticker.get(ticker.upper(), [])[:limit]
