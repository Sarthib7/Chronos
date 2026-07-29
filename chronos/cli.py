"""Command line entrypoint: python -m chronos "topic" --since 7d"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from chronos.digest import build_digest
from chronos.models import Digest, Query

_SINCE_RE = re.compile(r"^(\d+)([hdw])$")
_UNIT_HOURS = {"h": 1, "d": 24, "w": 168}


def parse_since(value: str, now: datetime | None = None) -> datetime:
    """'24h', '7d', '2w' -> absolute UTC datetime."""
    match = _SINCE_RE.match(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid --since {value!r}; use forms like 24h, 7d, 2w"
        )
    amount, unit = int(match.group(1)), match.group(2)
    if amount < 1:
        raise argparse.ArgumentTypeError("--since must be at least 1")
    now = now or datetime.now(timezone.utc)
    return now - timedelta(hours=amount * _UNIT_HOURS[unit])


def render_markdown(digest: Digest) -> str:
    q = digest.query
    lines = [
        f"# {q.topic} — news digest",
        "",
        f"_{len(digest.articles)} articles since {q.since:%Y-%m-%d %H:%M UTC}, "
        f"generated {digest.generated_at:%Y-%m-%d %H:%M UTC}_",
        "",
    ]
    if not digest.articles:
        lines.append("No articles matched this topic in the given window.")
    for i, article in enumerate(digest.articles, 1):
        meta = [article.source, f"{article.published_at:%Y-%m-%d}"]
        if article.points is not None:
            meta.append(f"{article.points} points")
        if article.relevance is not None:
            meta.append(f"relevance {article.relevance:.2f}")
        lines += [
            f"## {i}. {article.title}",
            f"{article.summary or ''}".strip(),
            "",
            f"{' · '.join(meta)}  \n<{article.url}>",
            "",
        ]
    lines.append("---")
    lines.append(f"Summarizer: {digest.summarizer}")
    if digest.failed_sources:
        lines.append(f"Sources unavailable: {', '.join(digest.failed_sources)}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chronos", description="Ranked AI news digests on demand."
    )
    parser.add_argument("topic", help='what to track, e.g. "agentic payments"')
    parser.add_argument(
        "--since", default="7d", help="lookback window: 24h, 7d, 2w (default 7d)"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="articles in the digest (default 10)"
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=15,
        help="articles sent to the LLM; caps cost (default 15)",
    )
    parser.add_argument(
        "--format", choices=("md", "json"), default="md", help="output format"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    query = Query(
        topic=args.topic,
        since=parse_since(args.since),
        limit=max(1, args.limit),
        candidate_limit=max(args.limit, args.candidates),
    )
    digest = asyncio.run(build_digest(query))
    if args.format == "json":
        print(json.dumps(digest.to_dict(), indent=2))
    else:
        print(render_markdown(digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
