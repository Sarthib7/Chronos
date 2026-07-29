#!/usr/bin/env python3
"""Masumi job handler: one paid job produces one Chronos digest.

The SDK owns MIP-003, payment polling and decision logging. This module owns
exactly one thing — translating a buyer's input_data into a Query, and the
resulting Digest into text. Keeping it that thin is deliberate: the scraper
stays independently testable and has no idea it is being sold.
"""

from __future__ import annotations

import logging

from chronos.cli import parse_since, render_markdown
from chronos.digest import build_digest
from chronos.models import Query

logger = logging.getLogger(__name__)

TIMEFRAMES = ["24h", "3d", "7d", "14d", "30d"]
DEFAULT_TIMEFRAME = "7d"
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 3, 25, 10
MIN_CANDIDATES = 15


class NoResultsError(Exception):
    """Nothing matched. Raised so the job fails instead of billing for an empty digest."""


def read_topic(input_data: dict) -> str:
    """The one required field. An empty topic cannot produce a meaningful digest."""
    for key in ("topic", "text-1", "text"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("Field 'topic' is required and must be a non-empty string")


def read_option(input_data: dict, key: str, values: list[str], default: str) -> str:
    """Read a dropdown value.

    Marketplace clients are inconsistent: some send the selected value, some send
    its index, and some wrap either in a single-element list. Accept all three and
    fall back to the default rather than failing a paid job over a form quirk.
    """
    raw = input_data.get(key)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return values[raw] if 0 <= raw < len(values) else default
    text = str(raw).strip()
    if text in values:
        return text
    if text.isdigit() and 0 <= int(text) < len(values):
        return values[int(text)]
    return default


def read_limit(input_data: dict) -> int:
    """Clamp rather than reject: a buyer asking for 500 articles gets the maximum."""
    raw = input_data.get("limit", LIMIT_DEFAULT)
    if isinstance(raw, list):
        raw = raw[0] if raw else LIMIT_DEFAULT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return LIMIT_DEFAULT
    return max(LIMIT_MIN, min(LIMIT_MAX, value))


def build_query(input_data: dict) -> Query:
    limit = read_limit(input_data)
    timeframe = read_option(input_data, "timeframe", TIMEFRAMES, DEFAULT_TIMEFRAME)
    return Query(
        topic=read_topic(input_data),
        since=parse_since(timeframe),
        limit=limit,
        candidate_limit=max(limit, MIN_CANDIDATES),
    )


async def process_job(identifier_from_purchaser: str, input_data: dict) -> str:
    """Run one digest for one buyer. Returns markdown."""
    query = build_query(input_data)
    logger.info(
        "job for %s: topic=%r since=%s limit=%d",
        identifier_from_purchaser,
        query.topic,
        query.since.isoformat(),
        query.limit,
    )

    digest = await build_digest(query)

    logger.info(
        "digest ready: %d articles, summarizer=%s, failed_sources=%s",
        len(digest.articles),
        digest.summarizer,
        digest.failed_sources or "none",
    )

    # Fail the job rather than charge for an empty digest. Chronos indexes AI news,
    # so an off-domain or too-narrow topic legitimately matches nothing — but a
    # buyer who paid and received zero articles files a dispute, and rightly so.
    # Failing here leaves the refund path open instead of taking the money.
    if not digest.articles:
        raise NoResultsError(
            f"No articles matched {query.topic!r} since {query.since:%Y-%m-%d}. "
            f"Chronos covers AI and machine-learning news; try broader terms, "
            f"a longer lookback window, or wording the press actually uses."
        )

    return render_markdown(digest)
