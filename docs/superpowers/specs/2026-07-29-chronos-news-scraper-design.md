# Chronos — AI News Agent (Design)

Date: 2026-07-29
Status: approved (phase 1 MVP)

## Goal

A paid AI-news agent listed on Sokosumi. A buyer submits a topic and a timeframe; the
agent returns a ranked, deduplicated digest of AI news with short summaries.

## Phasing

The full product is three independent deliverables. Each gets its own spec and plan.

| Phase | Deliverable | Blocking dependency |
|---|---|---|
| 1 (this spec) | Scraper core + CLI. `python -m chronos "topic" --since 7d` prints a digest. | none |
| 2 | MIP-003 HTTP service (FastAPI) wrapping phase 1. Public deployment. | phase 1 |
| 3 | Masumi node access, wallet funding, on-chain registration, Sokosumi listing. | phase 2 deployed at a public URL |

Phase 1 contains no HTTP server, no persistence, and no blockchain code.

## Phase 1 scope

**In:** RSS sources, Hacker News, dedup, ranking, LLM summarization via OpenRouter,
markdown/JSON CLI output, offline tests.

**Out (deferred, deliberately):** Reddit, X/Twitter, full-article text extraction,
persistence/database, scheduling, alerting, HTTP layer, payments.

Rationale for source choice: RSS and Hacker News need zero credentials, so the MVP is
buildable and testable immediately. Reddit requires the user to create an OAuth app;
X requires paid API access. Both are added later as adapters behind the same `Source`
protocol, with no changes to the pipeline.

## Architecture

```
chronos/
  models.py        Article, Query, Digest
  config.py        env loading, feed list
  feeds.yaml       curated RSS sources with weights
  sources/
    base.py        Source protocol
    rss.py         feedparser-based RSS source
    hackernews.py  Algolia HN search API
  pipeline.py      dedup, scoring, top-N cut
  summarize.py     OpenRouterSummarizer + ExtractiveSummarizer
  cli.py           argparse entrypoint
  __main__.py
tests/
  fixtures/        recorded feed XML and HN JSON
```

Every module has one job and a narrow interface:

- A **source** takes a `Query` and returns `list[Article]`. It knows nothing about
  ranking, summarizing, or output. Adding a source means adding one file.
- The **pipeline** takes `list[Article]` and a `Query` and returns an ordered, deduped,
  truncated `list[Article]`. Pure function of its inputs — no I/O, so it is trivially
  testable.
- A **summarizer** takes `list[Article]` and a topic and returns the same articles with
  `summary` and `relevance` populated. Two implementations, chosen by whether an API key
  is present.
- The **CLI** wires the three together and formats output.

## Data model

```python
@dataclass
class Article:
    id: str                    # sha1 of canonical_url
    title: str
    url: str
    canonical_url: str         # scheme/host lowercased, tracking params stripped
    source: str                # "rss:techcrunch" | "hn"
    published_at: datetime     # timezone-aware UTC
    snippet: str               # feed description / HN story text, plain text
    author: str | None = None
    score: float = 0.0         # pre-LLM rank score
    summary: str | None = None
    relevance: float | None = None   # 0..1, from LLM
    points: int | None = None  # HN score, None for RSS

@dataclass
class Query:
    topic: str
    since: datetime            # timezone-aware UTC
    limit: int = 10            # articles in final digest
    candidate_limit: int = 15  # articles sent to the LLM

@dataclass
class Digest:
    query: Query
    generated_at: datetime
    articles: list[Article]
    failed_sources: list[str]  # source name -> shown in output footer
    summarizer: str            # "openrouter:<model>" or "extractive"
```

## Data flow

1. Parse CLI args into a `Query`. `--since 7d|24h|30d` is parsed to an absolute UTC datetime.
2. Fetch all sources concurrently with `asyncio.gather(..., return_exceptions=True)`.
   Each source has its own timeout.
3. Merge results. Drop anything older than `query.since`.
4. Deduplicate: group by `canonical_url`; then collapse near-duplicate titles
   (normalized token-set ratio ≥ 0.85). Keep the earliest-published member of each group.
5. Score each article:
   `score = 0.5*keyword_match + 0.3*recency + 0.2*source_weight`
   - `keyword_match`: fraction of query tokens (stopwords removed) present in
     title+snippet, with title matches weighted double.
   - `recency`: `exp(-age_hours / 48)`.
   - `source_weight`: from `feeds.yaml`, default 1.0, normalized to 0..1.
6. Drop every article with zero topic-word overlap, then sort by score and cut to
   `candidate_limit`, letting no single source exceed a per-source cap.
7. Summarize: one LLM call for the whole batch. Returns, per article, a ≤40-word summary
   and a 0..1 relevance score.
8. Re-rank: `final = 0.6*relevance + 0.4*score`. Apply the per-source cap again, then cut
   to `limit`.
9. Render markdown (default) or JSON.

Two rules in those steps are product decisions, not implementation details:

- **No off-topic filler.** An article with no overlap with the requested topic is dropped
  even when that leaves the digest short or empty. A buyer asking for "cardano" and
  receiving generic AI headlines has been handed filler, not an answer. An empty digest
  says so plainly.
- **Per-source cap, enforced on the final cut.** High-volume feeds (arXiv publishes
  hundreds of same-day papers) otherwise take every slot and turn a news digest into a
  paper list. The cap is `max(2, limit // 3)`. It is applied to the final ranking, not
  only to the candidate pool — capping the pool achieves nothing if the final cut refills
  from one loud source. A source may exceed its cap only when no other source can fill
  the digest, since returning a short digest is worse than a slightly lopsided one.

The top-N cut before step 7 is the cost control: the LLM sees ~15 articles per run
regardless of how many were fetched.

## Summarization

Provider: OpenRouter, chat completions endpoint, `response_format` JSON.

Default model `google/gemini-2.5-flash-lite`, chosen from OpenRouter's live model list on
2026-07-29 ($0.05/$0.20 per million tokens on the batch tier, 1M context). Overridable
with `OPENROUTER_MODEL`.

One request per digest. Input is a numbered list of `title + snippet` (snippets truncated
to 400 chars). Output is a JSON array of `{index, summary, relevance}`.

Failure handling: if the key is missing, the call fails, or the response does not parse,
fall back to `ExtractiveSummarizer` (first two sentences of the snippet, `relevance` left
as `None` so re-ranking falls back to `score`). A digest is always produced.

## Error handling

- One source failing must never fail the digest. Exceptions are caught per source, the
  name is added to `Digest.failed_sources`, and it is reported in the output footer.
- Per-source HTTP timeout (default 15s) and one retry with backoff on 429/5xx.
- Individual malformed feed entries are skipped, not fatal to the feed.
- Zero results is a valid outcome: print "no articles matched" plus which sources ran.

## Testing

`pytest` + `pytest-asyncio`. Network is never touched in the default test run.

- **Fixtures:** recorded RSS XML and HN JSON committed under `tests/fixtures/`.
- **Unit:** URL canonicalization, dedup grouping, each scoring component, `--since` parsing,
  OpenRouter response parsing (including malformed responses hitting the fallback).
- **Integration:** fixture-backed sources → pipeline → stub summarizer → digest, asserting
  order and count.
- **Smoke:** one live test hitting real feeds, marked `@pytest.mark.live`, deselected by
  default.

## Configuration

`.env` (git-ignored), read via `python-dotenv`:

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | no | Enables LLM summaries. Absent → extractive fallback. Values shorter than 20 characters after stripping are treated as absent, so a blank or stub value in the shell does not cause a doomed API call on every run. |
| `OPENROUTER_MODEL` | no | Defaults to `google/gemini-2.5-flash-lite`. |
| `HTTP_TIMEOUT` | no | Per-request timeout, default 15s. |

`feeds.yaml` holds the RSS source list: name, url, weight.

## What the user must supply manually

| # | Item | Needed for | How to get it |
|---|---|---|---|
| 1 | OpenRouter API key | good summaries (phase 1) | openrouter.ai → Keys. Into `.env`, never into chat. |
| 2 | Reddit client id + secret | Reddit source (deferred) | reddit.com/prefs/apps → create "script" app |
| 3 | Public deployment target | phase 2 | any host; registry rejects localhost (ngrok works for testing) |
| 4 | Masumi node access | phase 3 | either an `ADMIN_KEY` on the hosted node at `payment.masumi.network`, or self-host via Docker (needs a Blockfrost API key) |
| 5 | Funded Cardano selling wallet | phase 3 | preprod ADA free from dispenser.masumi.network |
| 6 | Agent metadata | phase 3 | name, description, capability+version, author, contact, tags, and price **denominated in USDM** — USDM pricing is required for a Sokosumi listing |

## Verified facts (checked 2026-07-29)

- `payments.masumi.network` (plural) does not resolve. The live host is
  `payment.masumi.network`; `/api/v1/health` returns `{"status":"success","data":{"status":"ok"}}`
  and `/admin` serves the Payment Service dashboard.
- Whether Masumi issues admin keys on that hosted node to third-party developers is
  **unconfirmed**; the official docs describe self-hosting only. To be resolved in phase 3.
- Registration mints an NFT on Cardano carrying the agent metadata, and returns an
  `AGENT_IDENTIFIER` that the agent must then hold in its own config.
- Preprod registration confirmation takes roughly 5–15 minutes.

## Success criteria for phase 1

`python -m chronos "agentic payments" --since 7d` prints a ranked markdown digest of
≥5 relevant articles from at least two sources, in under 30 seconds, with the full test
suite passing offline.
