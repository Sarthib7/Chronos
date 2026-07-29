"""The one interface every news source implements."""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from chronos.models import Article, Query

RETRY_STATUSES = {429, 500, 502, 503, 504}


class Source(Protocol):
    """A place news comes from.

    Implementations know nothing about ranking, summarizing or output. They fetch,
    parse, and return articles. Anything they raise is caught by the caller and
    reported as a failed source; it never sinks the digest.
    """

    name: str

    async def fetch(self, query: Query, client: httpx.AsyncClient) -> list[Article]: ...


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    attempts: int = 2,
    backoff: float = 1.5,
) -> httpx.Response:
    """GET with one retry on rate-limit / server errors."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params)
            if response.status_code in RETRY_STATUSES and attempt < attempts - 1:
                await asyncio.sleep(backoff * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except (httpx.HTTPError, httpx.StreamError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                await asyncio.sleep(backoff * (attempt + 1))
                continue
            raise
    raise last_exc if last_exc else RuntimeError(f"failed to fetch {url}")
