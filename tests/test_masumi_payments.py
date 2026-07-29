"""Payment service client, against a mocked transport — no network."""

import httpx
import pytest

from chronos_masumi.config import MasumiSettings
from chronos_masumi.payments import PaymentClient, PaymentError

SETTINGS = MasumiSettings(
    payment_api_key="masumi-payment-testkey",
    payment_service_url="https://payment.example/api/v1",
    network="Preprod",
    agent_identifier="67ab0c92" + "f" * 50,
    seller_vkey="26524d1f",
    source_index=0,
)


def client_with(handler) -> PaymentClient:
    client = PaymentClient(SETTINGS)
    transport = httpx.MockTransport(handler)

    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Patched
    client._restore = lambda: setattr(httpx, "AsyncClient", original)
    return client


@pytest.fixture(autouse=True)
def restore_httpx():
    original = httpx.AsyncClient
    yield
    httpx.AsyncClient = original


class TestCreatePayment:
    async def test_sends_the_v2_source_index(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["json"] = __import__("json").loads(request.content)
            captured["token"] = request.headers.get("token")
            return httpx.Response(200, json={"status": "success", "data": {
                "blockchainIdentifier": "abc123",
                "payByTime": "1785333571000",
                "RequestedFunds": [{"unit": "16a55b", "amount": "1000000"}],
            }})

        payment = await client_with(handler).create_payment("e1b9f7", "a" * 64)
        assert captured["json"]["supportedPaymentSourceIndex"] == 0
        assert captured["token"] == "masumi-payment-testkey"
        assert payment.blockchain_identifier == "abc123"
        assert payment.requested_funds == [{"unit": "16a55b", "amount": "1000000"}]

    async def test_does_not_declare_a_payment_source_type(self):
        # The service derives V1/V2 from the registry and rejects a mismatch,
        # so asserting a version can only ever break us.
        captured = {}

        def handler(request):
            captured["json"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"status": "success", "data": {"blockchainIdentifier": "x"}})

        await client_with(handler).create_payment("e1b9f7", "a" * 64)
        assert "paymentSourceType" not in captured["json"]
        assert "paymentType" not in captured["json"]

    async def test_includes_required_fields(self):
        captured = {}

        def handler(request):
            captured["json"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"status": "success", "data": {"blockchainIdentifier": "x"}})

        await client_with(handler).create_payment("e1b9f7", "b" * 64)
        for field in ("agentIdentifier", "network", "identifierFromPurchaser",
                      "inputHash", "payByTime", "submitResultTime"):
            assert field in captured["json"], field

    async def test_error_body_raises_payment_error(self):
        def handler(request):
            return httpx.Response(400, json={"status": "error", "error": {
                "message": "V2 Cardano payments require supportedPaymentSourceIndex"}})

        with pytest.raises(PaymentError, match="supportedPaymentSourceIndex"):
            await client_with(handler).create_payment("e1b9f7", "a" * 64)

    async def test_non_json_response_raises(self):
        def handler(request):
            return httpx.Response(502, text="<html>gateway</html>")

        with pytest.raises(PaymentError, match="non-JSON"):
            await client_with(handler).create_payment("e1b9f7", "a" * 64)


class TestPaymentState:
    async def test_finds_matching_payment(self):
        def handler(request):
            return httpx.Response(200, json={"status": "success", "data": {"Payments": [
                {"blockchainIdentifier": "other", "onChainState": "FundsLocked"},
                {"blockchainIdentifier": "mine", "onChainState": "ResultSubmitted"},
            ]}})

        assert await client_with(handler).payment_state("mine") == "ResultSubmitted"

    async def test_absent_payment_gives_none(self):
        def handler(request):
            return httpx.Response(200, json={"status": "success", "data": {"Payments": []}})

        assert await client_with(handler).payment_state("mine") is None


class TestSubmitResult:
    async def test_posts_hash_and_identifier(self):
        captured = {}

        def handler(request):
            captured["json"] = __import__("json").loads(request.content)
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

        await client_with(handler).submit_result("chain-id", "d" * 64)
        assert captured["json"] == {
            "network": "Preprod",
            "blockchainIdentifier": "chain-id",
            "submitResultHash": "d" * 64,
        }
        assert captured["url"].endswith("/payment/submit-result")
