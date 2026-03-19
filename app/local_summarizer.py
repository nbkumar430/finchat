"""Extractive summarization from bundled JSON articles (source of truth).

Uses TF–IDF cosine-style scoring over sentences — a lightweight "k nearest"
sentence retrieval without external ML dependencies. All output is pulled from
article text only (no generative model).
"""

from __future__ import annotations

import math
import re
from typing import Optional

from app.news_store import Article

# Max sentences to collect per request (before ranking)
_MAX_SENTENCES = 120
# Top excerpts to show (KNN-style k)
_DEFAULT_TOP_K = 6
_MIN_SENTENCE_LEN = 25
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _split_sentences(text: str) -> list[str]:
    """Split article text into sentence-like units for ranking."""
    flat = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not flat:
        return []
    # Split on . ! ? followed by space; keep short clauses as one unit if needed
    raw = re.split(r"(?<=[.!?])\s+", flat)
    out: list[str] = []
    for chunk in raw:
        s = chunk.strip()
        if len(s) >= _MIN_SENTENCE_LEN:
            out.append(s)
    if not out and len(flat) >= _MIN_SENTENCE_LEN:
        return [flat[:1200]]
    return out[:_MAX_SENTENCES]


def _gather_sentences(articles: list[Article]) -> list[tuple[str, str, str]]:
    """List of (sentence, ticker, title) for provenance."""
    rows: list[tuple[str, str, str]] = []
    for art in articles:
        blob = f"{art.title}. {art.full_text}"
        for sent in _split_sentences(blob):
            rows.append((sent, art.ticker.upper(), art.title))
    return rows


def _tfidf_weights(
    sentences_tokens: list[list[str]],
) -> tuple[list[dict[str, float]], dict[str, float]]:
    """Per-sentence log-scaled TF and global IDF over this mini-corpus."""
    n_docs = len(sentences_tokens)
    df: dict[str, int] = {}
    for toks in sentences_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1

    def idf(term: str) -> float:
        return math.log((n_docs + 1) / (df.get(term, 0) + 1)) + 1.0

    idf_vec = {t: idf(t) for t in df}

    tfidf_docs: list[dict[str, float]] = []
    for toks in sentences_tokens:
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        denom = max(len(toks), 1)
        tfidf_docs.append({t: (c / denom) * idf_vec[t] for t, c in tf.items()})
    return tfidf_docs, idf_vec


def _score_sentence(
    sent_tfidf: dict[str, float],
    query_weights: dict[str, float],
    ticker: str,
    sentence_text: str,
) -> float:
    """Cosine similarity between query pseudo-vector and sentence vector."""
    dot = 0.0
    q2 = 0.0
    s2 = 0.0
    for term, qw in query_weights.items():
        q2 += qw * qw
        sw = sent_tfidf.get(term, 0.0)
        dot += qw * sw
    for sw in sent_tfidf.values():
        s2 += sw * sw
    denom = math.sqrt(q2) * math.sqrt(s2)
    sim = dot / denom if denom > 0 else 0.0
    st = sentence_text.lower()
    if ticker.lower() in st:
        sim *= 1.35
    return sim


def build_extractive_answer(
    query: str,
    articles: list[Article],
    ticker_filter: Optional[str],  # noqa: UP007 (py3.9 compat)
    top_k: int = _DEFAULT_TOP_K,
) -> str:
    """Build a readable answer from ranked article excerpts only."""
    if not articles:
        return (
            "I don’t have matching articles in the bundled dataset for that question. "
            "Try another ticker or phrasing."
        )

    rows = _gather_sentences(articles)
    if not rows:
        titles = "\n".join(f"- {a.ticker}: {a.title}" for a in articles[:5])
        return (
            "Here are the relevant headlines from the dataset:\n\n"
            f"{titles}\n\n"
            "Full article text was too short to extract sentences from."
        )

    sentences = [r[0] for r in rows]
    sent_tokens = [_tokenize(s) for s in sentences]

    # Query = user text + optional ticker + company tokens from titles
    query_extra = query
    if ticker_filter:
        query_extra = f"{query_extra} {ticker_filter}"
    for a in articles[:3]:
        query_extra = f"{query_extra} {a.title}"
    q_tokens = _tokenize(query_extra)
    if not q_tokens:
        q_tokens = _tokenize(" ".join(a.ticker for a in articles))

    _, idf_vec = _tfidf_weights(sent_tokens)
    q_tf: dict[str, int] = {}
    for t in q_tokens:
        q_tf[t] = q_tf.get(t, 0) + 1
    q_denom = max(len(q_tokens), 1)
    query_weights = {t: (q_tf[t] / q_denom) * idf_vec.get(t, 1.0) for t in q_tf}

    tfidf_docs, _ = _tfidf_weights(sent_tokens)

    scored: list[tuple[float, str, str, str]] = []
    for tfidf, (sent, tick, title) in zip(tfidf_docs, rows):  # noqa: B905 (py3.9 no strict=)
        sc = _score_sentence(tfidf, query_weights, tick, sent)
        scored.append((sc, sent, tick, title))

    scored.sort(key=lambda x: x[0], reverse=True)

    picked: list[tuple[str, str, str]] = []
    seen_norm: set[str] = set()
    for sc, sent, tick, title in scored:
        if sc <= 0 and picked:
            break
        norm = re.sub(r"\s+", " ", sent.lower())[:80]
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        picked.append((sent, tick, title))
        if len(picked) >= top_k:
            break

    if not picked:
        picked = [(scored[0][1], scored[0][2], scored[0][3])]

    tickers_mentioned = sorted({p[1] for p in picked})
    intro = (
        f"Here’s a concise readout based **only** on sentences from your bundled news JSON "
        f"(tickers highlighted: **{', '.join(tickers_mentioned)}**). "
        f"I ranked excerpts by relevance to your question using **TF–IDF similarity** "
        f"(k-nearest informative sentences — no generative model):\n\n"
        f"**Your question:** {query}\n\n"
    )
    bullets = "\n".join(f"• {sent}" for sent, _, _ in picked)
    footer = "\n\n---\n" "_Every bullet is taken from the article bodies or titles in Sources below._"
    return intro + bullets + footer
