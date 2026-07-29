"""Orchestrator: sources → pipeline → summarizer → Digest."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from chronos.config import Settings, load_feeds, load_settings
from chronos.models import Article, Digest, Query
from chronos.pipeline import dedup, final_rank, score_all, select_candidates
from chronos.sources import HackerNewsSource, RssSource
from chronos.sources.base import Source
from chronos.summarize import Summarizer, build_summarizer

USER_AGENT = "Chronos/0.1 (+https://github.com/masumi-network)"


def default_sources() -> list[Source]:
    return [RssSource(feed) for feed in load_feeds()] + [HackerNewsSource()]


async def gather_articles(
    sources: list[Source], query: Query, settings: Settings
) -> tuple[list[Article], list[str]]:
    """Fetch every source concurrently. A source that fails is recorded, not raised."""
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        results = await asyncio.gather(
            *(source.fetch(query, client) for source in sources),
            return_exceptions=True,
        )

    articles: list[Article] = []
    failed: list[str] = []
    for source, result in zip(sources, results):
        if isinstance(result, BaseException):
            failed.append(source.name)
        else:
            articles.extend(result)
    return articles, failed


async def build_digest(
    query: Query,
    sources: list[Source] | None = None,
    settings: Settings | None = None,
    summarizer: Summarizer | None = None,
) -> Digest:
    settings = settings or load_settings()
    sources = sources if sources is not None else default_sources()
    summarizer = summarizer or build_summarizer(settings)

    articles, failed = await gather_articles(sources, query, settings)
    articles = [a for a in articles if a.published_at >= query.since]
    articles = dedup(articles)
    score_all(articles, query)
    candidates = select_candidates(articles, query)
    await summarizer.run(candidates, query.topic)

    return Digest(
        query=query,
        generated_at=datetime.now(timezone.utc),
        articles=final_rank(candidates, query.limit),
        failed_sources=failed,
        summarizer=summarizer.name,
    )
