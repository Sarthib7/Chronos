# Sokosumi listing — submission sheet

Form: https://tally.so/r/nPLBaV (linked from Masumi's "List Your Agent on Sokosumi" guide)
There is no listing API. Submission is manual and human-reviewed.

## Agent details

| Field | Value |
|---|---|
| Agent name | Chronos |
| Agent identifier | `67ab0c92c4ac1610895a1c965ee50aba41a8f1513b15240723b3bd0b1025ff95b4e4749198c17614dc54653a0a66a3e297f71215d0a3646c2e000000` |
| Network | Preprod |
| API base URL | https://chronos-production-41d6.up.railway.app |
| Price | 1 tUSDM (`1000000`, unit `16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d`) |
| Author | sarthi — sarthi.borkar@nmkr.io |
| Repository | https://github.com/Sarthib7/Chronos |
| Example output | https://raw.githubusercontent.com/Sarthib7/Chronos/main/examples/example-digest.md |

## Short description

Ranked, deduplicated AI news digests on demand.

## Long description

Chronos turns a topic and a time window into a ranked news digest. It searches curated
AI and machine-learning press feeds plus Hacker News, collapses duplicate and syndicated
coverage of the same story, scores each article for relevance to the requested topic, and
returns a summarised digest in markdown.

It is deliberately strict about relevance: if nothing genuinely matches the topic, the job
fails rather than returning unrelated articles. Padding a paid result with filler is worse
than returning nothing.

## Input

| Field | Type | Notes |
|---|---|---|
| `topic` | text, required | Words from the topic must appear in an article for it to be included |
| `timeframe` | option | 24h, 3d, 7d, 14d, 30d — defaults to 7d |
| `limit` | number, optional | 3–25, defaults to 10 |

## Output

Markdown digest: ranked articles with title, source, publication date, relevance score,
one-to-two sentence summary, and link. Footer discloses which summarizer ran and any
source that was unreachable.

Typical execution time: 5–15 seconds.

## Before submitting — fix the on-chain metadata

The registry entry a reviewer will look at is currently incomplete. Registration was done
through the dashboard with several fields left at defaults:

| Field | On-chain now | Should be |
|---|---|---|
| `Author.name` | `""` (empty) | `sarthi` |
| `ExampleOutputs` | `[]` (empty) | The raw.githubusercontent URL above, `text/markdown` |
| `Capability` | `Custom Agent` / `1.0.0` | `news-digest` / `0.1.0` |
| `description` | "AI news digests on demand" | The long description above |

Masumi's own guidance ties a missing or divergent example output to dispute risk, and an
empty author is the first thing that looks unfinished on a marketplace card.

Fix via `POST /registry/update`, which issues an UpdateAction on the V2 registry mint
contract — it burns the current asset and mints a new one with an incremented version
segment. Costs ADA; needs a key with write permission (the current `ReadAndPay` key is
not sufficient).

## Status of the agent itself

- Deployed and serving: `/availability`, `/status`, `/start_job`, `/input_schema`, `/health`
- Registered on Cardano Preprod, registration confirmed
- Payment request creation verified end to end against the live payment service
- **Not yet verified:** settlement. No purchase has been made, so funds-locked →
  execute → decision-hash → unlock is untested.
