# Chronos

An AI news agent. Give it a topic and a time window; it returns a ranked, deduplicated
digest with short summaries.

Phase 1 (this repo today) is the scraper core plus a CLI. Phase 2 wraps it in a MIP-003
HTTP service; phase 3 registers it on Masumi and lists it on Sokosumi. See
`docs/superpowers/specs/2026-07-29-chronos-news-scraper-design.md`.

## Quick start

```bash
uv sync
uv run python -m chronos "agentic payments" --since 7d
```

Options:

```
--since   lookback window: 24h, 7d, 2w        (default 7d)
--limit   articles in the digest              (default 10)
--format  md | json                           (default md)
```

## Configuration

Copy `.env.example` to `.env`:

```
OPENROUTER_API_KEY=sk-or-...      # optional; without it, summaries are extractive
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
HTTP_TIMEOUT=15
```

Without a key Chronos still works — it falls back to extractive summaries and ranks on
keyword match, recency and source weight alone. With a key it makes exactly one LLM call
per digest, over at most `--candidates` articles, so cost per run is bounded.

## How it works

1. Every source is fetched concurrently. A source that fails is reported in the footer,
   never fatal to the digest.
2. Articles are deduplicated by canonical URL, then by fuzzy title match (syndicated
   reposts under different URLs).
3. Each article scores on keyword match (title hits weighted double), recency decay
   (48-hour half-life) and source weight.
4. The top candidates go to the LLM in one batch for a summary and a relevance score.
5. Final ranking blends relevance with the prior score, capped per source so one
   high-volume feed cannot take every slot.

## Sources

RSS feeds listed in `chronos/feeds.yaml`, plus Hacker News via the public Algolia API.
Neither needs credentials. Check feed health with:

```bash
uv run python -m chronos.validate_feeds
```

Reddit and X are not implemented. Both slot in behind the same `Source` protocol
(`chronos/sources/base.py`) without touching the pipeline: Reddit needs an OAuth app,
X needs paid API access.

## Tests

```bash
uv run pytest           # offline, fixture-backed
uv run pytest -m live   # hits real feeds
```
