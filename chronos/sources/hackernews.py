"""Hacker News source, via the public Algolia search API. No credentials required."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from chronos.models import Article, Query
from chronos.normalize import article_id, canonical_url, clean_text
from chronos.sources.base import get_with_retry

SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HITS_PER_PAGE = 50


class HackerNewsSource:
    name = "hn"

    def __init__(self, weight: float = 0.9):
        self.weight = weight

    async def fetch(self, query: Query, client: httpx.AsyncClient) -> list[Article]:
        since_ts = int(query.since.timestamp())
        response = await get_with_retry(
            client,
            SEARCH_URL,
            params={
                "query": query.topic,
                "tags": "story",
                "numericFilters": f"created_at_i>{since_ts}",
                "hitsPerPage": HITS_PER_PAGE,
            },
        )
        return self.parse(response.json(), query)

    def parse(self, payload: dict, query: Query) -> list[Article]:
        articles: list[Article] = []
        for hit in payload.get("hits", []):
            try:
                title = (hit.get("title") or "").strip()
                created = hit.get("created_at_i")
                if not title or created is None:
                    continue
                published = datetime.fromtimestamp(int(created), tz=timezone.utc)
                if published < query.since:
                    continue
                object_id = hit.get("objectID")
                # Ask HN and text posts have no external url; link to the HN thread.
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
                articles.append(
                    Article(
                        id=article_id(url),
                        title=title,
                        url=url,
                        canonical_url=canonical_url(url),
                        source=self.name,
                        published_at=published,
                        snippet=clean_text(hit.get("story_text") or ""),
                        author=hit.get("author") or None,
                        source_weight=self.weight,
                        points=hit.get("points"),
                    )
                )
            except Exception:
                continue
        return articles
