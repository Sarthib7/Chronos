"""Core data types passed between sources, pipeline, summarizer and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    """A single news item, from any source."""

    id: str
    title: str
    url: str
    canonical_url: str
    source: str
    published_at: datetime
    snippet: str = ""
    author: str | None = None
    source_weight: float = 1.0
    score: float = 0.0
    summary: str | None = None
    relevance: float | None = None
    points: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "author": self.author,
            "summary": self.summary,
            "relevance": self.relevance,
            "score": round(self.score, 4),
            "points": self.points,
        }


@dataclass
class Query:
    """What the caller asked for."""

    topic: str
    since: datetime
    limit: int = 10
    candidate_limit: int = 15


@dataclass
class Digest:
    """What Chronos returns."""

    query: Query
    generated_at: datetime
    articles: list[Article] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    summarizer: str = "extractive"

    def to_dict(self) -> dict:
        return {
            "topic": self.query.topic,
            "since": self.query.since.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "summarizer": self.summarizer,
            "failed_sources": self.failed_sources,
            "articles": [a.to_dict() for a in self.articles],
        }
