"""Tests for the Masumi job handler — the translation layer only, no network."""

import pytest

import agent
from agent import build_query, process_job, read_limit, read_option, read_topic
from chronos.models import Digest, Query
from tests.conftest import NOW, make_article

TIMEFRAMES = agent.TIMEFRAMES


class TestReadTopic:
    def test_reads_topic_key(self):
        assert read_topic({"topic": "  agentic payments "}) == "agentic payments"

    def test_accepts_generated_schema_ids(self):
        assert read_topic({"text-1": "cardano"}) == "cardano"

    @pytest.mark.parametrize("payload", [{}, {"topic": ""}, {"topic": "   "}, {"topic": None}])
    def test_rejects_missing_or_empty(self, payload):
        with pytest.raises(ValueError, match="topic"):
            read_topic(payload)


class TestReadOption:
    def test_plain_value(self):
        assert read_option({"timeframe": "24h"}, "timeframe", TIMEFRAMES, "7d") == "24h"

    def test_value_wrapped_in_list(self):
        assert read_option({"timeframe": ["30d"]}, "timeframe", TIMEFRAMES, "7d") == "30d"

    def test_integer_index(self):
        assert read_option({"timeframe": 0}, "timeframe", TIMEFRAMES, "7d") == TIMEFRAMES[0]

    def test_index_wrapped_in_list(self):
        assert read_option({"timeframe": [1]}, "timeframe", TIMEFRAMES, "7d") == TIMEFRAMES[1]

    def test_numeric_string_index(self):
        assert read_option({"timeframe": "2"}, "timeframe", TIMEFRAMES, "7d") == TIMEFRAMES[2]

    @pytest.mark.parametrize(
        "payload", [{}, {"timeframe": None}, {"timeframe": []}, {"timeframe": "99y"}, {"timeframe": 99}]
    )
    def test_falls_back_to_default(self, payload):
        assert read_option(payload, "timeframe", TIMEFRAMES, "7d") == "7d"


class TestReadLimit:
    def test_plain_integer(self):
        assert read_limit({"limit": 7}) == 7

    def test_numeric_string(self):
        assert read_limit({"limit": "12"}) == 12

    def test_clamps_above_maximum(self):
        assert read_limit({"limit": 500}) == agent.LIMIT_MAX

    def test_clamps_below_minimum(self):
        assert read_limit({"limit": 0}) == agent.LIMIT_MIN

    @pytest.mark.parametrize("payload", [{}, {"limit": "many"}, {"limit": None}])
    def test_defaults_on_junk(self, payload):
        assert read_limit(payload) == agent.LIMIT_DEFAULT


class TestBuildQuery:
    def test_maps_input_to_query(self):
        query = build_query({"topic": "cardano", "timeframe": "24h", "limit": 5})
        assert query.topic == "cardano"
        assert query.limit == 5
        assert (query.since.now(query.since.tzinfo) - query.since).total_seconds() == pytest.approx(
            24 * 3600, abs=60
        )

    def test_candidate_limit_never_below_the_llm_floor(self):
        query = build_query({"topic": "cardano", "limit": 3})
        assert query.candidate_limit == agent.MIN_CANDIDATES

    def test_candidate_limit_grows_with_a_large_request(self):
        query = build_query({"topic": "cardano", "limit": 25})
        assert query.candidate_limit == 25


class TestProcessJob:
    async def test_returns_rendered_markdown(self, monkeypatch):
        captured = {}

        async def fake_build_digest(query: Query) -> Digest:
            captured["query"] = query
            article = make_article("Cardano ships agentic payments", "https://a.com/1", source="hn")
            article.summary = "A summary."
            return Digest(
                query=query,
                generated_at=NOW,
                articles=[article],
                failed_sources=[],
                summarizer="openrouter:test/model",
            )

        monkeypatch.setattr(agent, "build_digest", fake_build_digest)
        output = await process_job("buyer-123", {"topic": "cardano", "limit": 5})

        assert "# cardano — news digest" in output
        assert "Cardano ships agentic payments" in output
        assert captured["query"].topic == "cardano"

    async def test_invalid_input_raises_before_any_work(self, monkeypatch):
        async def must_not_run(query):
            raise AssertionError("digest should not be built for invalid input")

        monkeypatch.setattr(agent, "build_digest", must_not_run)
        with pytest.raises(ValueError, match="topic"):
            await process_job("buyer-123", {"timeframe": "7d"})


class TestEmptyDigestGuard:
    async def test_empty_digest_fails_the_job_instead_of_billing(self, monkeypatch):
        async def empty_digest(query: Query) -> Digest:
            return Digest(query=query, generated_at=NOW, articles=[], summarizer="extractive")

        monkeypatch.setattr(agent, "build_digest", empty_digest)
        with pytest.raises(agent.NoResultsError, match="cardano"):
            await process_job("buyer-123", {"topic": "cardano", "timeframe": "30d"})

    async def test_message_tells_the_buyer_what_to_change(self, monkeypatch):
        async def empty_digest(query: Query) -> Digest:
            return Digest(query=query, generated_at=NOW, articles=[], summarizer="extractive")

        monkeypatch.setattr(agent, "build_digest", empty_digest)
        with pytest.raises(agent.NoResultsError) as excinfo:
            await process_job("buyer-123", {"topic": "cardano"})
        assert "broader terms" in str(excinfo.value)
