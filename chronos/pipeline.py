"""Dedup, score and rank articles. Pure functions — no I/O, so this is fully testable."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from chronos.models import Article, Query

TITLE_DUP_THRESHOLD = 85
RECENCY_HALF_LIFE_HOURS = 48.0

W_KEYWORD = 0.5
W_RECENCY = 0.3
W_SOURCE = 0.2

W_RELEVANCE = 0.6
W_PRIOR = 0.4

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "at",
    "by", "from", "is", "are", "was", "were", "be", "as", "it", "its", "this",
    "that", "these", "those", "new", "news", "about", "into", "over", "how",
}


def tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def dedup(articles: list[Article]) -> list[Article]:
    """Collapse the same story appearing twice.

    Exact match on canonical URL first, then fuzzy match on title for syndicated
    reposts under different URLs. The earliest-published copy wins.
    """
    by_url: dict[str, Article] = {}
    for article in articles:
        existing = by_url.get(article.canonical_url)
        if existing is None or article.published_at < existing.published_at:
            by_url[article.canonical_url] = article

    kept: list[Article] = []
    for article in sorted(by_url.values(), key=lambda a: a.published_at):
        duplicate_of = next(
            (
                k
                for k in kept
                if fuzz.token_set_ratio(article.title.lower(), k.title.lower())
                >= TITLE_DUP_THRESHOLD
            ),
            None,
        )
        if duplicate_of is None:
            kept.append(article)
    return kept


def keyword_match(article: Article, topic_tokens: set[str]) -> float:
    """0..1. Title hits count double, since a topic word in the headline means more."""
    if not topic_tokens:
        return 0.0
    title_hits = len(topic_tokens & tokenize(article.title)) / len(topic_tokens)
    body_hits = len(topic_tokens & tokenize(article.snippet)) / len(topic_tokens)
    return min(1.0, (2 * title_hits + body_hits) / 2)


def recency(article: Article, now: datetime) -> float:
    age_hours = max(0.0, (now - article.published_at).total_seconds() / 3600)
    return math.exp(-age_hours / RECENCY_HALF_LIFE_HOURS)


def score_all(
    articles: list[Article], query: Query, now: datetime | None = None
) -> list[Article]:
    now = now or datetime.now(timezone.utc)
    topic_tokens = tokenize(query.topic)
    max_weight = max((a.source_weight for a in articles), default=1.0) or 1.0
    for article in articles:
        article.score = (
            W_KEYWORD * keyword_match(article, topic_tokens)
            + W_RECENCY * recency(article, now)
            + W_SOURCE * (article.source_weight / max_weight)
        )
    return articles


def select_candidates(articles: list[Article], query: Query) -> list[Article]:
    """Rank, drop off-topic items, spread across sources, and cut to the LLM budget.

    Articles with no topic-word overlap are dropped outright, even when that leaves
    the digest short or empty. Padding a requested topic with unrelated articles is
    worse than returning nothing: a buyer asking for "cardano" and receiving generic
    AI headlines has been given filler, not an answer.

    The per-source cap matters more than it looks: a high-volume firehose like the
    arXiv feed publishes hundreds of same-day items and will otherwise take every
    slot, turning a news digest into a paper list.
    """
    topic_tokens = tokenize(query.topic)
    ranked = sorted(articles, key=lambda a: a.score, reverse=True)
    on_topic = [a for a in ranked if keyword_match(a, topic_tokens) > 0]
    return spread_by_source(on_topic, query.candidate_limit)


def spread_by_source(ranked: list[Article], limit: int, cap: int | None = None) -> list[Article]:
    """Take the best `limit`, letting no single source exceed `cap` until others run dry."""
    cap = cap or max(2, limit // 4)
    counts: dict[str, int] = {}
    picked: list[Article] = []
    overflow: list[Article] = []
    for article in ranked:
        if len(picked) >= limit:
            break
        if counts.get(article.source, 0) < cap:
            picked.append(article)
            counts[article.source] = counts.get(article.source, 0) + 1
        else:
            overflow.append(article)
    # Only if the capped pass could not fill the budget do we let a source exceed its cap.
    for article in overflow:
        if len(picked) >= limit:
            break
        picked.append(article)
    return picked


def final_rank(articles: list[Article], limit: int) -> list[Article]:
    """Blend LLM relevance with the prior score, then spread across sources.

    The spread is applied here, not only to the candidate pool: capping the pool
    does nothing for the reader if the final cut re-fills with one loud source.
    """

    def key(article: Article) -> float:
        if article.relevance is None:
            return article.score
        return W_RELEVANCE * article.relevance + W_PRIOR * article.score

    ranked = sorted(articles, key=key, reverse=True)
    return spread_by_source(ranked, limit, cap=max(2, limit // 3))
