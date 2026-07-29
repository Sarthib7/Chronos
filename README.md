# Chronos

An AI news agent. Give it a topic and a time window; it returns a ranked, deduplicated
digest with short summaries.

Phase 1 is the scraper core plus a CLI. Phase 2 (done) exposes it as a Masumi agentic
service. Phase 3 registers it on Masumi and lists it on Sokosumi. See
`docs/superpowers/specs/2026-07-29-chronos-news-scraper-design.md`.

## As a Masumi agent

```bash
uv run python main.py          # MIP-003 API server on $PORT (default 8080)
```

Live: <https://chronos-production-41d6.up.railway.app> — `/docs` for Swagger.

`chronos_masumi/` implements MIP-003 and the payment flow directly against the payment
service's HTTP API. The `masumi` SDK is deliberately **not** used: it cannot create
payments for agents registered as `Web3CardanoV2`, because it never sends
`supportedPaymentSourceIndex` and builds its request payload inline with no extension
point. See `chronos_masumi/payments.py`.

| Module | Responsibility |
|---|---|
| `hashing.py` | MIP-004 input/output hashes |
| `payments.py` | Payment service client, V1/V2 detection |
| `jobs.py` | Job lifecycle and payment polling |
| `app.py` | MIP-003 endpoints |
| `schema.py` | Buyer-facing input schema |

Three behaviours are contractual rather than stylistic:

- **Hashing is a faithful reimplementation.** Buyers recompute these and dispute on
  mismatch, so input uses JCS canonical JSON and output is JSON-escaped before hashing.
- **`paymentSourceType` is never sent.** The service derives it from the registry entry
  and rejects any value that disagrees.
- **The decision hash is submitted before the result is exposed**, and a failed job
  submits nothing — never claim work that was not delivered.

`input_data` accepts both a plain object and MIP-003 key/value pairs
(`[{"key": "topic", "value": "AI agents"}]`); both produce the same input hash.

A job with no matching articles raises `NoResultsError` and fails rather than returning
an empty digest — billing for zero results invites a dispute.

Known limit: jobs are held in memory, so a restart loses in-flight work. Acceptable on
Preprod; needs a persistent store before mainnet.

Additional `.env` values for API mode:

```
PAYMENT_API_KEY=...      # from the Masumi Payment Service admin dashboard
PAYMENT_SERVICE_URL=https://payment.masumi.network/api/v1
NETWORK=Preprod
AGENT_IDENTIFIER=...     # issued after on-chain registration
SELLER_VKEY=...          # from your selling wallet
```

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
