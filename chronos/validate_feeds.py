"""Check every feed in feeds.yaml actually returns parseable entries.

Run: python -m chronos.validate_feeds
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from chronos.config import load_feeds, load_settings
from chronos.digest import USER_AGENT


async def check(feed, client: httpx.AsyncClient) -> tuple[str, str]:
    try:
        response = await client.get(feed.url)
        if response.status_code != 200:
            return feed.name, f"HTTP {response.status_code}"
        parsed = feedparser.parse(response.content)
        if not parsed.entries:
            return feed.name, "0 entries"
        dated = [
            e
            for e in parsed.entries
            if e.get("published_parsed") or e.get("updated_parsed")
        ]
        if not dated:
            return feed.name, f"{len(parsed.entries)} entries but no dates"
        return feed.name, f"OK ({len(parsed.entries)} entries)"
    except Exception as exc:
        return feed.name, f"{type(exc).__name__}: {exc}"


async def main() -> None:
    settings = load_settings()
    feeds = load_feeds()
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        results = await asyncio.gather(*(check(feed, client) for feed in feeds))
    for name, status in results:
        marker = "ok  " if status.startswith("OK") else "FAIL"
        print(f"{marker} {name:22} {status}")
    bad = [name for name, status in results if not status.startswith("OK")]
    print(f"\n{len(results) - len(bad)}/{len(results)} feeds healthy")
    if bad:
        print("Remove or fix: " + ", ".join(bad))


if __name__ == "__main__":
    asyncio.run(main())
