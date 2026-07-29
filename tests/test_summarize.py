import pytest

from chronos.config import Settings
from chronos.summarize import (
    ExtractiveSummarizer,
    OpenRouterSummarizer,
    build_summarizer,
    build_user_prompt,
    parse_llm_response,
)
from tests.conftest import make_article


class TestParseLlmResponse:
    def test_plain_json_object(self):
        out = parse_llm_response('{"results": [{"index": 0, "summary": "s", "relevance": 0.9}]}')
        assert out == [{"index": 0, "summary": "s", "relevance": 0.9}]

    def test_fenced_json(self):
        out = parse_llm_response('```json\n{"results": [{"index": 1}]}\n```')
        assert out == [{"index": 1}]

    def test_bare_array(self):
        assert parse_llm_response('[{"index": 0}]') == [{"index": 0}]

    def test_json_embedded_in_prose(self):
        out = parse_llm_response('Sure! Here you go: {"results": [{"index": 2}]} Hope that helps.')
        assert out == [{"index": 2}]

    def test_malformed_returns_empty(self):
        assert parse_llm_response("not json at all") == []

    def test_empty_returns_empty(self):
        assert parse_llm_response("") == []


class TestExtractiveSummarizer:
    async def test_uses_leading_sentences(self):
        article = make_article(
            "T", "https://a.com/1", snippet="One. Two. Three. Four."
        )
        await ExtractiveSummarizer().run([article], "topic")
        assert article.summary == "One. Two."

    async def test_falls_back_to_title_when_no_snippet(self):
        article = make_article("Just a title", "https://a.com/2")
        await ExtractiveSummarizer().run([article], "topic")
        assert article.summary == "Just a title"

    async def test_leaves_relevance_unset(self):
        article = make_article("T", "https://a.com/3", snippet="Body.")
        await ExtractiveSummarizer().run([article], "topic")
        assert article.relevance is None


class TestOpenRouterSummarizer:
    @pytest.fixture
    def summarizer(self) -> OpenRouterSummarizer:
        return OpenRouterSummarizer(
            Settings(
                openrouter_api_key="k" * 24,
                openrouter_model="test/model",
                http_timeout=5,
            )
        )

    async def test_applies_summaries_and_relevance(self, summarizer, monkeypatch):
        async def fake_call(articles, topic):
            return '{"results": [{"index": 0, "summary": "Short summary", "relevance": 0.8}]}'

        monkeypatch.setattr(summarizer, "_call", fake_call)
        article = make_article("T", "https://a.com/1", snippet="Body text.")
        await summarizer.run([article], "topic")
        assert article.summary == "Short summary"
        assert article.relevance == 0.8

    async def test_relevance_is_clamped(self, summarizer, monkeypatch):
        async def fake_call(articles, topic):
            return '{"results": [{"index": 0, "summary": "s", "relevance": 4.2}]}'

        monkeypatch.setattr(summarizer, "_call", fake_call)
        article = make_article("T", "https://a.com/1", snippet="Body.")
        await summarizer.run([article], "topic")
        assert article.relevance == 1.0

    async def test_network_failure_falls_back_and_reports(self, summarizer, monkeypatch):
        async def boom(articles, topic):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(summarizer, "_call", boom)
        article = make_article("T", "https://a.com/1", snippet="Body text here.")
        result = await summarizer.run([article], "topic")
        assert result[0].summary == "Body text here."
        assert "openrouter failed" in summarizer.name
        assert "RuntimeError" in summarizer.name

    async def test_malformed_response_falls_back(self, summarizer, monkeypatch):
        async def garbage(articles, topic):
            return "the model rambled and returned no json"

        monkeypatch.setattr(summarizer, "_call", garbage)
        article = make_article("T", "https://a.com/1", snippet="Body text here.")
        await summarizer.run([article], "topic")
        assert article.summary == "Body text here."

    async def test_articles_skipped_by_model_still_get_summaries(
        self, summarizer, monkeypatch
    ):
        async def partial(articles, topic):
            return '{"results": [{"index": 0, "summary": "Done", "relevance": 0.5}]}'

        monkeypatch.setattr(summarizer, "_call", partial)
        first = make_article("A", "https://a.com/1", snippet="First body.")
        second = make_article("B", "https://a.com/2", snippet="Second body.")
        await summarizer.run([first, second], "topic")
        assert first.summary == "Done"
        assert second.summary == "Second body."

    async def test_out_of_range_index_is_ignored(self, summarizer, monkeypatch):
        async def bad_index(articles, topic):
            return '{"results": [{"index": 99, "summary": "wrong", "relevance": 0.5}]}'

        monkeypatch.setattr(summarizer, "_call", bad_index)
        article = make_article("A", "https://a.com/1", snippet="Real body.")
        await summarizer.run([article], "topic")
        assert article.summary == "Real body."

    async def test_empty_input_short_circuits(self, summarizer):
        assert await summarizer.run([], "topic") == []


class TestBuildSummarizer:
    def test_no_key_gives_extractive(self):
        settings = Settings(openrouter_api_key=None, openrouter_model="m", http_timeout=5)
        assert isinstance(build_summarizer(settings), ExtractiveSummarizer)

    def test_key_gives_openrouter(self):
        settings = Settings(
            openrouter_api_key="k" * 24, openrouter_model="m", http_timeout=5
        )
        assert isinstance(build_summarizer(settings), OpenRouterSummarizer)


def test_user_prompt_numbers_articles_and_includes_topic():
    articles = [
        make_article("First", "https://a.com/1", snippet="Body one."),
        make_article("Second", "https://a.com/2"),
    ]
    prompt = build_user_prompt(articles, "agentic payments")
    assert "Topic: agentic payments" in prompt
    assert "[0] First" in prompt
    assert "[1] Second" in prompt


class TestRetry:
    @pytest.fixture
    def summarizer(self) -> OpenRouterSummarizer:
        return OpenRouterSummarizer(
            Settings(
                openrouter_api_key="k" * 24,
                openrouter_model="test/model",
                http_timeout=5,
            )
        )

    async def test_unparseable_response_is_retried_once_and_recovers(
        self, summarizer, monkeypatch
    ):
        calls = []

        async def flaky(articles, topic):
            calls.append(1)
            if len(calls) == 1:
                return "sorry, here is some prose instead of json"
            return '{"results": [{"index": 0, "summary": "Recovered", "relevance": 0.7}]}'

        monkeypatch.setattr(summarizer, "_call", flaky)
        article = make_article("T", "https://a.com/1", snippet="Body.")
        await summarizer.run([article], "topic")
        assert len(calls) == 2
        assert article.summary == "Recovered"
        assert "failed" not in summarizer.name

    async def test_gives_up_after_the_retry(self, summarizer, monkeypatch):
        calls = []

        async def always_garbage(articles, topic):
            calls.append(1)
            return "never json"

        monkeypatch.setattr(summarizer, "_call", always_garbage)
        article = make_article("T", "https://a.com/1", snippet="Body text.")
        await summarizer.run([article], "topic")
        assert len(calls) == 2
        assert article.summary == "Body text."
        assert "unparseable response" in summarizer.name


class TestProviderErrorBody:
    async def test_error_body_with_http_200_is_reported_clearly(self, monkeypatch):
        from chronos.summarize import ProviderError, _describe

        assert _describe(ProviderError("rate limited upstream")) == (
            "provider error: rate limited upstream"
        )
