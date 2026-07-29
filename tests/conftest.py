from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from chronos.models import Article, Query
from chronos.normalize import article_id, canonical_url

FIXTURES = Path(__file__).parent / "fixtures"

# Fixed clock so scoring and windowing are deterministic.
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def query() -> Query:
    return Query(topic="agentic payments", since=SINCE, limit=5, candidate_limit=10)


@pytest.fixture
def now() -> datetime:
    return NOW


def make_article(
    title: str,
    url: str,
    source: str = "rss:test",
    published_at: datetime | None = None,
    snippet: str = "",
    weight: float = 1.0,
) -> Article:
    return Article(
        id=article_id(url),
        title=title,
        url=url,
        canonical_url=canonical_url(url),
        source=source,
        published_at=published_at or NOW,
        snippet=snippet,
        source_weight=weight,
    )
