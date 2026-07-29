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
STATE_FUNDS_LOCKED = "FundsLocked"
STATE_RESULT_SUBMITTED = "ResultSubmitted"
REFUND_STATES = {"RefundRequested", "Refunded", "FundsOrDatumInvalid", "Disputed"}


class PaymentError(Exception):
    """The payment service rejected a request."""


@dataclass
class PaymentRequest:
    blockchain_identifier: str
    pay_by_time: str | None
    submit_result_time: str | None
    requested_funds: list[dict]


def _timestamp(minutes: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class PaymentClient:
    def __init__(self, settings: MasumiSettings, timeout: float = 30.0):
        self.settings = settings
        self.timeout = timeout

    @property
    def _headers(self) -> dict:
        return {"token": self.settings.payment_api_key}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.settings.payment_service_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(method, url, headers=self._headers, **kwargs)
        try:
            body = response.json()
        except ValueError:
            raise PaymentError(f"{response.status_code}: non-JSON response") from None
        if response.status_code >= 400 or body.get("status") == "error":
            message = body.get("error", {}).get("message", str(body)[:200])
            raise PaymentError(f"{response.status_code}: {message}")
        return body.get("data", {})

    async def create_payment(self, identifier_from_purchaser: str, input_hash: str) -> PaymentRequest:
        """Reserve payment for a job.

        supportedPaymentSourceIndex is what the masumi SDK omits; without it a
        Web3CardanoV2 agent cannot be paid at all. paymentSourceType is
        deliberately not sent: the service derives it from the agent's registry
        entry and rejects any value that disagrees with it.
        """
        payload = {
            "agentIdentifier": self.settings.agent_identifier,
            "network": self.settings.network,
            "identifierFromPurchaser": identifier_from_purchaser,
            "inputHash": input_hash,
            "payByTime": _timestamp(PAY_BY_MINUTES),
            "submitResultTime": _timestamp(SUBMIT_RESULT_MINUTES),
            "supportedPaymentSourceIndex": self.settings.source_index,
        }
        data = await self._request("POST", "/payment", json=payload)
        return PaymentRequest(
            blockchain_identifier=data.get("blockchainIdentifier", ""),
            pay_by_time=data.get("payByTime"),
            submit_result_time=data.get("submitResultTime"),
            requested_funds=data.get("RequestedFunds") or data.get("Amounts") or [],
        )

    async def payment_state(self, blockchain_identifier: str) -> str | None:
        """Current on-chain state, or None if the payment is not visible yet.

        The service exposes no lookup by blockchainIdentifier, so this pages the
        recent payments and matches locally.
        """
        data = await self._request(
            "GET", "/payment", params={"network": self.settings.network, "limit": 100}
        )
        for payment in data.get("Payments", []) or []:
            if payment.get("blockchainIdentifier") == blockchain_identifier:
                return payment.get("onChainState")
        return None

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
