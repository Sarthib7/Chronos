"""Client for the Masumi Payment Service HTTP API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from chronos_masumi.config import MasumiSettings

logger = logging.getLogger(__name__)

PAY_BY_MINUTES = 60 * 12
SUBMIT_RESULT_MINUTES = 60 * 24

# On-chain states that matter to a seller. The full set is larger; these are the
# ones that change what we do next.
SOURCE_TYPE_V1 = "Web3CardanoV1"
SOURCE_TYPE_V2 = "Web3CardanoV2"

STATE_FUNDS_LOCKED = "FundsLocked"
STATE_RESULT_SUBMITTED = "ResultSubmitted"

# Every state from which a job can no longer be delivered and paid. Taken from
# the service's own onChainState enum: FundsLocked, FundsOrDatumInvalid,
# ResultSubmitted, RefundRequested, Disputed, WithdrawAuthorized,
# RefundAuthorized, Withdrawn, RefundWithdrawn, DisputedWithdrawn.
TERMINAL_FAILURE_STATES = {
    "FundsOrDatumInvalid",
    "RefundRequested",
    "Disputed",
    "RefundAuthorized",
    "RefundWithdrawn",
    "DisputedWithdrawn",
}


class PaymentError(Exception):
    """The payment service rejected a request."""


@dataclass
class PaymentRequest:
    """Everything a buyer needs to pay for a job.

    MIP-003 requires the seller to hand back the unlock times, the agent
    identifier and the seller key. A buyer has no credentials on the seller's
    payment node, so anything withheld here cannot be recovered and the buyer
    cannot build POST /purchase at all.
    """

    blockchain_identifier: str
    pay_by_time: int | None
    submit_result_time: int | None
    unlock_time: int | None
    external_dispute_unlock_time: int | None
    agent_identifier: str
    seller_vkey: str
    requested_funds: list[dict]


def _timestamp(minutes: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _millis(value) -> int | None:
    """The service sends unix milliseconds as strings; MIP-003 wants integers.

    Kept in milliseconds rather than converted to seconds: the seller signature
    inside the blockchainIdentifier covers the millisecond values, so a buyer
    echoing anything else fails the signature check.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("payment service sent an unparseable timestamp: %r", value)
        return None


class PaymentClient:
    def __init__(self, settings: MasumiSettings, timeout: float = 30.0):
        self.settings = settings
        self.timeout = timeout
        self._detected: tuple[str, int | None] | None = None

    @property
    def _headers(self) -> dict:
        return {"token": self.settings.payment_api_key}

    async def _request(self, method: str, path: str, missing_ok: bool = False, **kwargs) -> dict | None:
        url = f"{self.settings.payment_service_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(method, url, headers=self._headers, **kwargs)
        if response.status_code == 404 and missing_ok:
            return None
        try:
            body = response.json()
        except ValueError:
            raise PaymentError(f"{response.status_code}: non-JSON response") from None
        if response.status_code >= 400 or body.get("status") == "error":
            message = body.get("error", {}).get("message", str(body)[:200])
            raise PaymentError(f"{response.status_code}: {message}")
        return body.get("data", {})

    async def detect_source(self) -> tuple[str, int | None]:
        """Read the agent's own registry entry to learn its payment source type.

        The index is required for Web3CardanoV2 and *forbidden* for V1, so
        guessing wrong breaks every job. Rather than hardcode a version, ask the
        registry what this agent actually is. Cached after the first lookup.

        This deliberately uses GET /registry and not GET /registry/agent-identifier.
        The latter runs its sources through filterValidSupportedPaymentSources(),
        which drops entries for other networks, so a position in its array is not
        the position POST /payment indexes into. GET /registry serialises the
        persisted sources sorted by position and never removes a row.

        Nothing here falls back to a guess: an index sent to a V1 agent, or the
        wrong index sent to a V2 one, is rejected or bills the wrong price.
        """
        if self._detected is not None:
            return self._detected

        data = await self._request(
            "GET",
            "/registry",
            params={
                "network": self.settings.network,
                "filterAgentIdentifier": self.settings.agent_identifier,
                "limit": 1,
            },
        )
        entries = data.get("Assets") or []
        if not entries:
            raise PaymentError(
                f"agent {self.settings.agent_identifier} is not in this node's registry "
                f"on {self.settings.network}; cannot tell V1 from V2"
            )

        sources = entries[0].get("supportedPaymentSources") or []
        if not sources:
            # V1 registrations carry no supported sources; pricing lives in AgentPricing.
            logger.info("agent registered as %s (no source index sent)", SOURCE_TYPE_V1)
            self._detected = (SOURCE_TYPE_V1, None)
            return self._detected

        payable = [
            position
            for position, source in enumerate(sources)
            if source.get("chain") == "Cardano"
            and source.get("network") == self.settings.network
            and source.get("paymentSourceType") == SOURCE_TYPE_V2
        ]
        self._detected = (SOURCE_TYPE_V2, self._choose_index(payable))
        logger.info("agent registered as %s (source index %s)", *self._detected)
        return self._detected

    def _choose_index(self, payable: list[int]) -> int:
        """Pick which priced source to sell through.

        For V2 the index selects the price, so an ambiguous choice is a wrong
        bill rather than a technicality.
        """
        configured = self.settings.source_index
        if configured is not None:
            if configured not in payable:
                raise PaymentError(
                    f"PAYMENT_SOURCE_INDEX={configured} is not a payable "
                    f"{SOURCE_TYPE_V2} source on {self.settings.network}; "
                    f"advertised indexes: {payable or 'none'}"
                )
            return configured
        if not payable:
            raise PaymentError(
                f"agent advertises no {SOURCE_TYPE_V2} Cardano source on "
                f"{self.settings.network}"
            )
        if len(payable) > 1:
            raise PaymentError(
                f"agent advertises {len(payable)} priced sources at indexes {payable}; "
                "set PAYMENT_SOURCE_INDEX to choose one"
            )
        return payable[0]

    async def create_payment(self, identifier_from_purchaser: str, input_hash: str) -> PaymentRequest:
        """Reserve payment for a job.

        paymentSourceType is deliberately never sent: the service derives it from
        the agent's registry entry and rejects any value that disagrees with it.

        supportedPaymentSourceIndex is sent only for V2 agents, because the API
        documents it as "required for V2 Cardano payments and forbidden for V1".
        The masumi SDK never sends it at all, which is why it cannot pay a V2
        agent; sending it unconditionally would just break V1 agents instead.
        """
        source_type, source_index = await self.detect_source()

        payload = {
            "agentIdentifier": self.settings.agent_identifier,
            "network": self.settings.network,
            "identifierFromPurchaser": identifier_from_purchaser,
            "inputHash": input_hash,
            "payByTime": _timestamp(PAY_BY_MINUTES),
            "submitResultTime": _timestamp(SUBMIT_RESULT_MINUTES),
        }
        if source_index is not None:
            payload["supportedPaymentSourceIndex"] = source_index

        data = await self._request("POST", "/payment", json=payload)
        wallet = data.get("SmartContractWallet") or {}
        return PaymentRequest(
            blockchain_identifier=data.get("blockchainIdentifier", ""),
            pay_by_time=_millis(data.get("payByTime")),
            submit_result_time=_millis(data.get("submitResultTime")),
            unlock_time=_millis(data.get("unlockTime")),
            external_dispute_unlock_time=_millis(data.get("externalDisputeUnlockTime")),
            agent_identifier=data.get("agentIdentifier") or self.settings.agent_identifier,
            seller_vkey=wallet.get("walletVkey") or self.settings.seller_vkey,
            requested_funds=data.get("RequestedFunds") or data.get("Amounts") or [],
        )

    async def payment_state(self, blockchain_identifier: str) -> str | None:
        """Current on-chain state, or None if the payment is not visible yet.

        Resolved by identifier rather than by listing. GET /payment defaults to
        Web3CardanoV1 when given neither filterPaymentSourceType nor
        filterSmartContractAddress, so a V2 payment is simply absent from that
        list and the seller waits forever on funds that are already locked.
        This endpoint takes the identifier directly and is version-agnostic.
        """
        data = await self._request(
            "POST",
            "/payment/resolve-blockchain-identifier",
            missing_ok=True,
            json={
                "network": self.settings.network,
                "blockchainIdentifier": blockchain_identifier,
            },
        )
        if data is None:
            return None

        action = data.get("NextAction") or {}
        if action.get("errorNote"):
            logger.warning(
                "payment %s needs %s: %s",
                blockchain_identifier[:16],
                action.get("requestedAction"),
                action["errorNote"],
            )
        return data.get("onChainState")

    async def submit_result(self, blockchain_identifier: str, output_hash: str) -> dict:
        """Publish the decision hash on-chain, which starts the dispute window."""
        return await self._request(
            "POST",
            "/payment/submit-result",
            json={
                "network": self.settings.network,
                "blockchainIdentifier": blockchain_identifier,
                "submitResultHash": output_hash,
            },
        )
