from datetime import datetime, timezone

import pytest

from chronos.cli import build_parser, parse_since, render_markdown
from chronos.models import Digest, Query
from tests.conftest import NOW, SINCE, make_article


class TestParseSince:
    @pytest.mark.parametrize(
        "value,expected_hours", [("24h", 24), ("7d", 168), ("2w", 336), ("1H", 1)]
    )
    def test_valid_windows(self, value, expected_hours):
        result = parse_since(value, now=NOW)
        assert (NOW - result).total_seconds() == expected_hours * 3600

    @pytest.mark.parametrize("value", ["7", "d7", "0d", "-3d", "7y", ""])
    def test_rejects_invalid(self, value):
        with pytest.raises(Exception):
            parse_since(value, now=NOW)

    def test_result_is_timezone_aware(self):
        assert parse_since("7d", now=NOW).tzinfo is not None


class TestRenderMarkdown:
    def _digest(self, articles, failed=None, summarizer="extractive") -> Digest:
        return Digest(
            query=Query(topic="agentic payments", since=SINCE, limit=5),
            generated_at=NOW,
            articles=articles,
            failed_sources=failed or [],
            summarizer=summarizer,
        )

    def test_renders_articles_with_metadata(self):
        article = make_article("Agentic payments ship", "https://a.com/1", source="hn")
        article.summary = "A short summary."
        article.relevance = 0.87
        article.points = 120
        out = render_markdown(self._digest([article]))
        assert "## 1. Agentic payments ship" in out
        assert "A short summary." in out
        assert "<https://a.com/1>" in out
        assert "120 points" in out
        assert "relevance 0.87" in out

    def test_empty_digest_says_so(self):
        out = render_markdown(self._digest([]))
        assert "No articles matched" in out

    def test_failed_sources_are_reported(self):
        out = render_markdown(self._digest([], failed=["rss:techcrunch-ai"]))
        assert "Sources unavailable: rss:techcrunch-ai" in out

    def test_summarizer_is_disclosed(self):
        out = render_markdown(self._digest([], summarizer="openrouter:test/model"))
        assert "Summarizer: openrouter:test/model" in out


class TestParser:
    def test_defaults(self):
        args = build_parser().parse_args(["ai agents"])
        assert args.topic == "ai agents"
        assert args.since == "7d"
        assert args.limit == 10
        assert args.format == "md"

    def test_rejects_unknown_format(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["topic", "--format", "pdf"])
