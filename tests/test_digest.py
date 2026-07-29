"""End-to-end through the orchestrator with stub sources — no network."""

from __future__ import annotations

import httpx
import pytest

from chronos.config import Settings
from chronos.digest import build_digest
from chronos.models import Article, Query
from chronos.summarize import ExtractiveSummarizer
from tests.conftest import NOW, SINCE, make_article

SETTINGS = Settings(openrouter_api_key=None, openrouter_model="m", http_timeout=5)


class StubSource:
    def __init__(self, name: str, articles: list[Article]):
        self.name = name
        self.articles = articles

    async def fetch(self, query: Query, client: httpx.AsyncClient) -> list[Article]:
        return self.articles


class BrokenSource:
    name = "rss:broken"

    async def fetch(self, query: Query, client: httpx.AsyncClient) -> list[Article]:
        raise httpx.ConnectError("host unreachable")


@pytest.fixture
def query() -> Query:
    return Query(topic="agentic payments", since=SINCE, limit=3, candidate_limit=6)


async def test_end_to_end_produces_ranked_digest(query):
    sources = [
        StubSource(
            "rss:one",
            [
                make_article(
                    "Agentic payments go live", "https://one.com/a", source="rss:one"
                ),
                make_article("Unrelated gardening", "https://one.com/b", source="rss:one"),
            ],
        ),
        StubSource(
            "hn",
            [make_article("Agentic payments on HN", "https://hn.com/c", source="hn")],
        ),
    ]
    digest = await build_digest(
        query, sources=sources, settings=SETTINGS, summarizer=ExtractiveSummarizer()
    )
    assert digest.articles
    assert all(a.summary for a in digest.articles)
    assert digest.failed_sources == []
    assert "agentic payments" in digest.articles[0].title.lower()


async def test_failing_source_is_reported_not_fatal(query):
    working = StubSource(
        "rss:ok",
        [make_article("Agentic payments news", "https://ok.com/a", source="rss:ok")],
    )
    digest = await build_digest(
        query,
        sources=[working, BrokenSource()],
        settings=SETTINGS,
        summarizer=ExtractiveSummarizer(),
    )
    assert digest.failed_sources == ["rss:broken"]
    assert len(digest.articles) == 1


async def test_articles_outside_window_are_dropped(query):
    from datetime import timedelta

    source = StubSource(
        "rss:one",
        [
            make_article(
                "Agentic payments today", "https://one.com/new", source="rss:one"
            ),
            make_article(
                "Agentic payments in 2019",
                "https://one.com/old",
                source="rss:one",
                published_at=NOW - timedelta(days=900),
            ),
        ],
    )
    digest = await build_digest(
        query, sources=[source], settings=SETTINGS, summarizer=ExtractiveSummarizer()
    )
    assert [a.url for a in digest.articles] == ["https://one.com/new"]


async def test_duplicates_across_sources_appear_once(query):
    a = StubSource(
        "rss:one",
        [make_article("OpenAI ships agentic payments", "https://x.com/p", source="rss:one")],
    )
    b = StubSource(
        "rss:two",
        [
            make_article(
                "OpenAI Ships Agentic Payments", "https://x.com/p?utm_source=rss",
                source="rss:two",
            )
        ],
    )
    digest = await build_digest(
        query, sources=[a, b], settings=SETTINGS, summarizer=ExtractiveSummarizer()
    )
    assert len(digest.articles) == 1


async def test_no_results_is_a_valid_digest(query):
    digest = await build_digest(
        query,
        sources=[StubSource("rss:empty", [])],
        settings=SETTINGS,
        summarizer=ExtractiveSummarizer(),
    )
    assert digest.articles == []
    assert digest.to_dict()["articles"] == []


async def test_respects_limit(query):
    # Titles must be genuinely distinct, otherwise fuzzy dedup correctly folds them
    # into one story and the limit is never exercised. Topic relevance is carried by
    # the snippet, since select_candidates now drops anything with no topic overlap.
    topics = [
        "Cardano rolls out new settlement rails",
        "Stripe opens machine-to-machine billing",
        "Escrow contracts for autonomous buyers",
        "Wallet standards fragment across frameworks",
        "Micropayment fees drop for API brokers",
        "Regulators weigh machine-initiated transfers",
    ]
    articles = [
        make_article(
            title, f"https://a.com/{i}", source=f"s{i}", snippet="About agentic payments."
        )
        for i, title in enumerate(topics)
    ]
    digest = await build_digest(
        query,
        sources=[StubSource("rss:many", articles)],
        settings=SETTINGS,
        summarizer=ExtractiveSummarizer(),
    )
    assert len(digest.articles) == query.limit


@pytest.mark.live
async def test_live_smoke_against_real_sources():
    from datetime import timedelta

    from chronos.digest import default_sources

    query = Query(
        topic="AI agents",
        since=NOW.now(NOW.tzinfo) - timedelta(days=7),
        limit=5,
        candidate_limit=10,
    )
    digest = await build_digest(query, sources=default_sources())
    assert len(digest.articles) >= 1
