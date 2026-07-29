"""RSS/Atom source. One instance per feed, so one dead feed cannot take the others down."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser
import httpx

from chronos.config import FeedConfig
from chronos.models import Article, Query
from chronos.normalize import article_id, canonical_url, clean_text
from chronos.sources.base import get_with_retry


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


class RssSource:
    def __init__(self, feed: FeedConfig):
        self.feed = feed
        self.name = f"rss:{feed.name}"

    async def fetch(self, query: Query, client: httpx.AsyncClient) -> list[Article]:
        response = await get_with_retry(client, self.feed.url)
        return self.parse(response.content, query)

    def parse(self, payload: bytes | str, query: Query) -> list[Article]:
        """Parse feed bytes into articles. Malformed entries are skipped, not fatal."""
        parsed = feedparser.parse(payload)
        articles: list[Article] = []
        for entry in parsed.entries:
            try:
                url = entry.get("link")
                title = (entry.get("title") or "").strip()
                if not url or not title:
                    continue
                published = _entry_datetime(entry)
                if published is None or published < query.since:
                    continue
                articles.append(
                    Article(
                        id=article_id(url),
                        title=title,
                        url=url,
                        canonical_url=canonical_url(url),
                        source=self.name,
                        published_at=published,
                        snippet=clean_text(
                            entry.get("summary") or entry.get("description")
                        ),
                        author=entry.get("author") or None,
                        source_weight=self.feed.weight,
                    )
                )
            except Exception:
                continue
        return articles
