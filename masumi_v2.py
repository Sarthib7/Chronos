"""Workaround: make the masumi SDK speak Web3CardanoV2.

Chronos is registered with `paymentSourceType: "Web3CardanoV2"`. The payment
service rejects a V2 payment request unless it carries
`supportedPaymentSourceIndex`, which selects the priced source by position in
the agent's `supported_payment_sources`:

    "V2 Cardano payments require supportedPaymentSourceIndex to select a
     priced Cardano source"

masumi 1.2.0 — the latest release on PyPI, and the current state of
`pip-masumi` main — hardcodes `payment_type = "Web3CardanoV1"` and never sends
that field. `create_payment_request` builds its payload inline with no
extension point, so there is nothing to subclass or configure.

Verified directly against the node: the same request succeeds with
`supportedPaymentSourceIndex: 0` and fails without it.

This patch injects the field into outgoing payment-request payloads and
nothing else. Delete it once the SDK supports V2 natively.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_INDEX = 0

# Fields that together identify a payment-request payload, so the patch cannot
# accidentally rewrite an unrelated request the SDK makes.
_PAYMENT_REQUEST_FIELDS = ("agentIdentifier", "identifierFromPurchaser")


def is_payment_request(payload: object) -> bool:
    return isinstance(payload, dict) and all(f in payload for f in _PAYMENT_REQUEST_FIELDS)


def inject_v2_index(payload: object, index: int = DEFAULT_SOURCE_INDEX) -> object:
    """Add supportedPaymentSourceIndex to a payment request, leaving anything else alone.

    An explicit value already present wins: the caller knows better than we do.
    """
    if is_payment_request(payload) and "supportedPaymentSourceIndex" not in payload:
        payload["supportedPaymentSourceIndex"] = index
    return payload


def apply(index: int = DEFAULT_SOURCE_INDEX) -> None:
    """Patch aiohttp so SDK payment requests carry the V2 source index."""
    import aiohttp

    if getattr(aiohttp.ClientSession.post, "_chronos_v2_patch", False):
        return

    original_post = aiohttp.ClientSession.post

    def post(self, url, **kwargs):
        if is_payment_request(kwargs.get("json")):
            inject_v2_index(kwargs["json"], index)
            logger.info("Added supportedPaymentSourceIndex=%d to payment request", index)
        return original_post(self, url, **kwargs)

    post._chronos_v2_patch = True
    aiohttp.ClientSession.post = post
    logger.info("Applied Web3CardanoV2 payment patch (index=%d)", index)
