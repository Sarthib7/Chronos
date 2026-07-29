# Chronos status

Last updated: 2026-07-29

## What exists

An AI news agent. Input is a topic plus a lookback window. Output is a ranked,
deduplicated markdown digest with short summaries.

Two ways to run it:

```bash
uv run python -m chronos "AI agents" --since 7d    # local CLI, no payments
uv run python main.py                              # MIP-003 API server
```

Live: <https://chronos-production-41d6.up.railway.app> (Swagger at `/docs`)

## Verified state

Everything below was confirmed by a live call, not assumed.

| Thing | State |
|---|---|
| Deployment (Railway) | Serving. `/availability`, `/status`, `/start_job`, `/input_schema`, `/health` all 200 |
| Tests | 191 offline, 1 live smoke test, all passing |
| RSS feeds | 14 of 14 reachable |
| LLM summarisation | Working. Roughly $0.0004 per digest |
| Registration | `RegistrationConfirmed` on Cardano Preprod |
| Payment source | `Web3CardanoV2`, Standard access type |
| Payment request creation | Working end to end against the live payment service |
| Settlement | **Untested.** Nobody has paid yet |
| Sokosumi listing | **Not listed.** Confirmed against all 101 catalogue entries |

## Identifiers

| Key | Value |
|---|---|
| Agent identifier | `67ab0c92c4ac1610895a1c965ee50aba41a8f1513b15240723b3bd0b10e9eb69a6a661146740169f7ca03ab8e77b39765f68562a61c030a1de000000` |
| Selling wallet vkey | `26524d1f6f67d495087475a94dcf57b4eb4a97b95b7d131c25deacaa` |
| Payment contract | `addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g` |
| Price | 1 tUSDM (`1000000`, 6 decimals) |
| tUSDM unit | `16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d` |

Note: the agent identifier changed once already (re-registration) and will change
again on any `POST /registry/update`, which burns and re-mints the asset. Railway needs
the new value each time.

## Architecture

```
chronos/          scraper core, no knowledge of payments or HTTP
  sources/        RSS and Hacker News behind one Source protocol
  pipeline.py     dedup, scoring, source diversity
  summarize.py    OpenRouter with extractive fallback
chronos_masumi/   MIP-003 service and payment integration
  hashing.py      MIP-004 input/output hashes
  payments.py     payment service client, v1/v2 detection
  jobs.py         job lifecycle, payment polling
  app.py          MIP-003 endpoints
agent.py          translates a job into a Query and a Digest into markdown
main.py           uvicorn entry point
```

The `masumi` pip SDK is deliberately not used. See `docs/masumi-v2-findings.md`.

## Open blockers

1. **Sokosumi listing.** Requires submitting <https://tally.so/r/nPLBaV>. No API exists
   for it. Needs a human.
2. **Registry metadata is incomplete.** `Author.name` is empty, `ExampleOutputs` is
   empty, `Capability` is the placeholder `Custom Agent`. Fixing needs an Admin
   permission key. The current `PAYMENT_API_KEY` is `ReadAndPay` and returns 403 on
   `POST /registry/update`. Payload is prepared in `examples/registration-payload.json`.
3. **Settlement unproven.** Payment request creation works. Funds locked, execute,
   decision hash, unlock has never run.

## Known limits

- Jobs live in memory. A restart loses in-flight work. Acceptable on Preprod, needs a
  persistent store before mainnet, because a lost job means a buyer paid and got nothing.
- The published example output is a snapshot and will age. Masumi ties a divergent
  example output to dispute risk.
- Reddit and X sources are not implemented. Both slot in behind the existing `Source`
  protocol without touching the pipeline.

## Next actions

1. Submit the Sokosumi form using `examples/sokosumi-listing.md`.
2. Ask Sandro whether Sokosumi preprod indexes v2 payment contract agents yet.
3. Get an Admin key, then run the prepared registry update.
4. Run a real purchase to prove settlement.
