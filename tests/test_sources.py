import json

import pytest

from chronos.config import FeedConfig
from chronos.sources import HackerNewsSource, RssSource
from tests.conftest import FIXTURES


@pytest.fixture
def rss_source() -> RssSource:
    return RssSource(FeedConfig(name="test", url="https://example.com/feed", weight=0.7))


class TestRssSource:
    def test_parses_valid_entries(self, rss_source, query):
        articles = rss_source.parse((FIXTURES / "sample_feed.xml").read_bytes(), query)
        titles = [a.title for a in articles]
        assert "OpenAI ships agentic payments protocol" in titles
        assert "A quiet week in robotics" in titles

    def test_drops_entries_older_than_window(self, rss_source, query):
        articles = rss_source.parse((FIXTURES / "sample_feed.xml").read_bytes(), query)
        assert "Ancient history of computing" not in [a.title for a in articles]

    def test_skips_entry_without_link_without_failing_feed(self, rss_source, query):
        articles = rss_source.parse((FIXTURES / "sample_feed.xml").read_bytes(), query)
        assert "Entry with no link at all" not in [a.title for a in articles]
        assert len(articles) == 2

    def test_canonicalizes_url_and_strips_html(self, rss_source, query):
        articles = rss_source.parse((FIXTURES / "sample_feed.xml").read_bytes(), query)
        first = next(a for a in articles if a.title.startswith("OpenAI"))
        assert first.canonical_url == "https://example.com/agentic-payments"
        assert "<p>" not in first.snippet
        assert first.author == "Jane Reporter"
        assert first.source == "rss:test"
        assert first.source_weight == 0.7

    def test_garbage_input_yields_no_articles(self, rss_source, query):
        assert rss_source.parse(b"this is not a feed", query) == []


class TestHackerNewsSource:
    @pytest.fixture
    def payload(self) -> dict:
        return json.loads((FIXTURES / "hn_search.json").read_text())

    def test_parses_stories(self, payload, query):
        articles = HackerNewsSource().parse(payload, query)
        assert "Show HN: An agent that pays for its own API calls" in [
            a.title for a in articles
        ]

    def test_textless_story_links_to_hn_thread(self, payload, query):
        articles = HackerNewsSource().parse(payload, query)
        ask = next(a for a in articles if a.title.startswith("Ask HN"))
        assert ask.url == "https://news.ycombinator.com/item?id=40000002"

    def test_drops_old_and_untitled_entries(self, payload, query):
        titles = [a.title for a in HackerNewsSource().parse(payload, query)]
        assert "Too old to matter" not in titles
        assert len(titles) == 2

    def test_carries_points(self, payload, query):
        articles = HackerNewsSource().parse(payload, query)
        assert next(a for a in articles if a.title.startswith("Show HN")).points == 142

    def test_empty_payload_is_safe(self, query):
        assert HackerNewsSource().parse({}, query) == []
