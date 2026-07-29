"""Summarization. One LLM call per digest, with a no-key fallback that always works."""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

import httpx

from chronos.config import Settings
from chronos.models import Article
from chronos.normalize import first_sentences

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SNIPPET_CHARS = 400
ATTEMPTS = 2


class UnparseableResponse(Exception):
    """The call succeeded but the model did not return usable JSON."""


class ProviderError(Exception):
    """OpenRouter returned an error body rather than completions."""


def _describe(exc: Exception) -> str:
    """Short, safe error label — never echoes the API key."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, UnparseableResponse):
        return "unparseable response"
    if isinstance(exc, ProviderError):
        return f"provider error: {exc}"
    return type(exc).__name__

SYSTEM_PROMPT = (
    "You rank and summarize news articles for a reader tracking a specific topic. "
    "For each numbered article, write a factual summary of at most 40 words and rate "
    "how relevant it is to the topic from 0.0 (unrelated) to 1.0 (directly about it). "
    "Judge relevance from the title and snippet only; never invent facts. "
    'Reply with JSON: {"results": [{"index": <int>, "summary": "<str>", '
    '"relevance": <float>}]}. Include every article exactly once.'
)


class Summarizer(Protocol):
    name: str

    async def run(self, articles: list[Article], topic: str) -> list[Article]: ...


class ExtractiveSummarizer:
    """No network, no key, no cost. Leaves relevance unset so ranking uses the prior score."""

    name = "extractive"

    async def run(self, articles: list[Article], topic: str) -> list[Article]:
        for article in articles:
            article.summary = first_sentences(article.snippet) or article.title
        return articles


class OpenRouterSummarizer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.name = f"openrouter:{settings.openrouter_model}"
        self.fallback = ExtractiveSummarizer()

    async def run(self, articles: list[Article], topic: str) -> list[Article]:
        if not articles:
            return articles

        # Cheap models intermittently answer with prose instead of JSON. Observed
        # once in normal use: a single retry recovers it, and silently serving
        # extractive summaries for a paid job is worse than one extra call.
        for attempt in range(ATTEMPTS):
            try:
                content = await self._call(articles, topic)
                results = parse_llm_response(content)
                if not results:
                    raise UnparseableResponse("model returned no usable JSON")
                self._apply(articles, results)
                break
            except Exception as exc:
                reason = _describe(exc)
                if attempt < ATTEMPTS - 1:
                    logger.warning("OpenRouter attempt %d failed (%s); retrying", attempt + 1, reason)
                    continue
                # Any failure — bad key, rate limit, malformed JSON — still yields a
                # digest, but the reason is reported rather than silently swallowed.
                logger.warning("OpenRouter summarization failed (%s); using extractive", reason)
                self.name = f"extractive (openrouter failed: {reason})"
                return await self.fallback.run(articles, topic)

        # Any article the model skipped still needs a summary.
        missing = [a for a in articles if a.summary is None]
        if missing:
            await self.fallback.run(missing, topic)
        return articles

    async def _call(self, articles: list[Article], topic: str) -> str:
        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(articles, topic)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "X-Title": "Chronos",
        }
        async with httpx.AsyncClient(timeout=self.settings.http_timeout * 4) as client:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()

        # OpenRouter reports some upstream failures in the body with HTTP 200.
        # Without this the next line raises a bare KeyError and the digest footer
        # blames "KeyError" instead of the actual provider error.
        if "choices" not in body:
            raise ProviderError(str(body.get("error", body))[:200])
        return body["choices"][0]["message"]["content"]

    @staticmethod
    def _apply(articles: list[Article], results: list[dict]) -> None:
        for result in results:
            index = result.get("index")
            if not isinstance(index, int) or not 0 <= index < len(articles):
                continue
            article = articles[index]
            summary = result.get("summary")
            if isinstance(summary, str) and summary.strip():
                article.summary = summary.strip()
            relevance = result.get("relevance")
            if isinstance(relevance, (int, float)):
                article.relevance = max(0.0, min(1.0, float(relevance)))


def build_user_prompt(articles: list[Article], topic: str) -> str:
    lines = [f"Topic: {topic}", "", "Articles:"]
    for index, article in enumerate(articles):
        snippet = article.snippet[:SNIPPET_CHARS]
        lines.append(f"[{index}] {article.title}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def parse_llm_response(content: str) -> list[dict]:
    """Tolerate models that wrap JSON in prose or code fences."""
    if not content:
        return []
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        for key in ("results", "articles", "items", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def build_summarizer(settings: Settings) -> Summarizer:
    return OpenRouterSummarizer(settings) if settings.llm_enabled else ExtractiveSummarizer()
