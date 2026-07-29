# Masumi v2 payment contract: what we hit building Chronos

Date: 2026-07-29. All findings below came from live calls against
`https://payment.masumi.network/api/v1` on Preprod.

Context from Sandro Schaier (Masumi): "v2" here means the **v2 payment contract
registration**, and the feature is still being implemented. Chronos is a `Standard`
access-type agent registered against that v2 payment contract. That single fact explains
most of what follows: the agent is registered against a contract whose surrounding
tooling is not finished yet.

## 1. The Python SDK cannot pay v2 agents

`POST /start_job` failed with:

```
V2 Cardano payments require supportedPaymentSourceIndex to select a priced Cardano source
```

masumi 1.2.0 (latest on PyPI, and the current state of `pip-masumi` main) hardcodes
`payment_type = "Web3CardanoV1"` and never sends `supportedPaymentSourceIndex`.
`create_payment_request` builds its payload inline, so there is no parameter, subclass
hook, or config to add it.

Four requests to `POST /payment`, identical except the two fields under test:

| # | `paymentSourceType` | `supportedPaymentSourceIndex` | Result |
|---|---|---|---|
| A | `Web3CardanoV1` | absent | 400 `paymentSourceType does not match the agent registry source` |
| B | absent | absent | 400 `V2 Cardano payments require supportedPaymentSourceIndex` |
| C | `Web3CardanoV1` | `0` | 400 `paymentSourceType does not match the agent registry source` |
| D | absent | `0` | **200 OK**, blockchainIdentifier returned |

Case B is what the SDK sends today.

Conclusions:

1. The service derives v1 versus v2 from the **agent's registry entry**, not from the
   request. A and C fail for declaring v1 against a v2 registration.
2. The only missing piece is the index. D succeeds with no version field at all.
3. The SDK's hardcoded `Web3CardanoV1` is harmless only by accident: it is sent under
   the key `paymentType`, while the spec documents `paymentSourceType`, so the service
   ignores it. "Fixing" the field name while keeping the hardcoded value would make
   every v2 agent fail case A, which is worse than today.

Chronos implements the protocol directly instead of using the SDK, and reads its own
registry entry to decide whether to send the index. The field is required for v2 and
forbidden for v1, so hardcoding either way breaks half the agents.

## 2. Registry discovery hides v2 agents by default

From the API's own parameter description:

> When omitted with no V2-aware filters, registry list/count endpoints default to
> `Web3CardanoV1` for backwards compatibility.

Measured on Preprod:

| Query | Result |
|---|---|
| default, no filters | 5 entries, all v1, no Chronos |
| `searchQuery=chronos` | 0 entries |
| `filterAgentIdentifier=<Chronos>` | 1 entry, `RegistrationConfirmed` |
| `filterPaymentSourceType=Web3CardanoV2` | 5 entries, Chronos included |

A correctly registered, confirmed v2 agent is invisible to any consumer using default
parameters, including the registry's own name search. This does not affect payments,
which resolve by `agentIdentifier`.

## 3. The dashboard registers v2 by default

Both registrations performed through the payment service admin dashboard came out
`Web3CardanoV2`. No v1 option was apparent. Combined with items 1 and 2, the default
registration path produces an agent that the default tooling can neither pay nor find.

Registering against the v1 contract is possible through the API by setting
`supportedPaymentSources[].paymentSourceType` explicitly on `POST /registry`.

## 4. Sokosumi does not list it, and that is a separate problem

Checked, and this one is **not** a v2 issue as far as the evidence goes:

- Full catalogue enumerated by cursor: 101 agents, zero Chronos.
- The public agent API exposes no `agentIdentifier`, no `paymentSourceType`, no vkey.
  Nothing links a catalogue entry to a registry entry.
- Catalogue entries carry `credits`, `categories`, `icon`, `image`, `riskClassification`
  and `summary`, none of which exist in on-chain metadata.
- All 231 documented Sokosumi API endpoints were inventoried. The `agents` namespace is
  read-only: list, detail, input-schema, jobs, ratings, reviews. There is no endpoint
  that creates a listing.

So the catalogue is populated by a human onboarding step, and the documented entry point
is the form at <https://tally.so/r/nPLBaV>.

Whether Sokosumi also excludes v2 agents is unknown and untestable from outside, because
the API exposes no payment source data. Worth asking Masumi directly rather than
inferring.

## Suggested fixes for Masumi

1. Let `Payment` accept a source index and send it. Either omit the version field or
   send `paymentSourceType` matching the registry entry rather than a constant.
2. Consider defaulting `supportedPaymentSourceIndex` when an agent has exactly one
   priced source. An explicit selector only earns its keep with more than one.
3. Surface the service error better. It is accurate but reaches developers wrapped in a
   generic 500 from their own framework, so it reads as "my agent is broken".
