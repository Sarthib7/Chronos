"""Payment service client, against a mocked transport — no network."""

from dataclasses import replace

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
    source_index=None,
)


def settings_with_index(index: int | None) -> MasumiSettings:
    return replace(SETTINGS, source_index=index)


def client_with(handler, settings: MasumiSettings = SETTINGS) -> PaymentClient:
    client = PaymentClient(settings)
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


def cardano_source(network="Preprod", source_type="Web3CardanoV2"):
    return {"chain": "Cardano", "network": network, "paymentSourceType": source_type}


def registry_response(sources):
    """A GET /registry page. V1 entries advertise no supported sources at all."""
    return {"status": "success", "data": {"Assets": [{
        "agentIdentifier": SETTINGS.agent_identifier,
        "state": "RegistrationConfirmed",
        "supportedPaymentSources": sources or None,
    }]}}


def routed(sources=None, payment_response=None, capture=None):
    """Handler answering both the registry lookup and the payment POST."""
    import json as _json

    if sources is None:
        sources = [cardano_source()]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/registry" in str(request.url):
            return httpx.Response(200, json=registry_response(sources))
        if capture is not None:
            capture["json"] = _json.loads(request.content)
            capture["token"] = request.headers.get("token")
        return httpx.Response(200, json=payment_response or {"status": "success", "data": {
            "blockchainIdentifier": "abc123",
            "payByTime": "1785333571000",
            "RequestedFunds": [{"unit": "16a55b", "amount": "1000000"}],
        }})

    return handler


class TestSourceDetection:
    async def test_v2_agent_gets_the_index(self):
        captured = {}
        payment = await client_with(routed(capture=captured)).create_payment("e1b9f7", "a" * 64)
        assert captured["json"]["supportedPaymentSourceIndex"] == 0
        assert payment.blockchain_identifier == "abc123"

    async def test_v1_agent_must_not_get_the_index(self):
        # The API documents the field as forbidden for V1; sending it anyway
        # would break every job the moment an agent is registered as V1.
        captured = {}
        await client_with(routed(sources=[], capture=captured)).create_payment("e1b9f7", "a" * 64)
        assert "supportedPaymentSourceIndex" not in captured["json"]

    async def test_index_counts_sources_the_service_would_filter_out(self):
        # GET /registry keeps every advertised source in position order, and
        # that position is what POST /payment indexes. A Mainnet source listed
        # first still occupies index 0 even though it is unusable here.
        captured = {}
        sources = [cardano_source(network="Mainnet"), cardano_source()]
        await client_with(routed(sources=sources, capture=captured)).create_payment("e1b9f7", "a" * 64)
        assert captured["json"]["supportedPaymentSourceIndex"] == 1

    async def test_ambiguous_pricing_refuses_to_guess(self):
        # With two priced sources the index picks which price the buyer is
        # charged, so a default would silently bill the wrong amount.
        client = client_with(routed(sources=[cardano_source(), cardano_source()]))
        with pytest.raises(PaymentError, match="PAYMENT_SOURCE_INDEX"):
            await client.create_payment("e1b9f7", "a" * 64)

    async def test_configured_index_is_honoured(self):
        captured = {}
        client = client_with(
            routed(sources=[cardano_source(), cardano_source()], capture=captured),
            settings_with_index(1),
        )
        await client.create_payment("e1b9f7", "a" * 64)
        assert captured["json"]["supportedPaymentSourceIndex"] == 1

    async def test_configured_index_must_be_payable(self):
        client = client_with(
            routed(sources=[cardano_source()]),
            settings_with_index(3),
        )
        with pytest.raises(PaymentError, match="not a payable"):
            await client.create_payment("e1b9f7", "a" * 64)

    async def test_unknown_agent_raises_instead_of_assuming(self):
        def handler(request):
            if "/registry" in str(request.url):
                return httpx.Response(200, json={"status": "success", "data": {"Assets": []}})
            raise AssertionError("must not attempt a payment without knowing the version")

        with pytest.raises(PaymentError, match="not in this node's registry"):
            await client_with(handler).create_payment("e1b9f7", "a" * 64)

    async def test_detection_is_cached(self):
        calls = []

        def handler(request):
            if "/registry" in str(request.url):
                calls.append(1)
                return httpx.Response(200, json=registry_response([cardano_source()]))
            return httpx.Response(200, json={"status": "success", "data": {"blockchainIdentifier": "x"}})

        client = client_with(handler)
        await client.create_payment("e1b9f7", "a" * 64)
        await client.create_payment("e1b9f8", "b" * 64)
        assert len(calls) == 1


class TestCreatePayment:
    async def test_sends_key_and_returns_payment_details(self):
        captured = {}
        payment = await client_with(routed(capture=captured)).create_payment("e1b9f7", "a" * 64)
        assert captured["token"] == "masumi-payment-testkey"
        assert payment.blockchain_identifier == "abc123"
        assert payment.requested_funds == [{"unit": "16a55b", "amount": "1000000"}]

    async def test_does_not_declare_a_payment_source_type(self):
        # The service derives V1/V2 from the registry and rejects a mismatch,
        # so asserting a version can only ever break us.
        captured = {}
        await client_with(routed(capture=captured)).create_payment("e1b9f7", "a" * 64)
        assert "paymentSourceType" not in captured["json"]
        assert "paymentType" not in captured["json"]

    async def test_includes_required_fields(self):
        captured = {}
        await client_with(routed(capture=captured)).create_payment("e1b9f7", "b" * 64)
        for field in ("agentIdentifier", "network", "identifierFromPurchaser",
                      "inputHash", "payByTime", "submitResultTime"):
            assert field in captured["json"], field

    async def test_keeps_the_fields_a_buyer_cannot_look_up(self):
        response = {"status": "success", "data": {
            "blockchainIdentifier": "abc123",
            "payByTime": "1785333571000",
            "submitResultTime": "1785340000000",
            "unlockTime": "1785361600000",
            "externalDisputeUnlockTime": "1785383200000",
            "agentIdentifier": "67ab0c92" + "e" * 50,
            "SmartContractWallet": {"walletVkey": "26524d1fdeadbeef"},
            "RequestedFunds": [{"unit": "16a55b", "amount": "1000000"}],
        }}
        payment = await client_with(routed(payment_response=response)).create_payment("e1b9f7", "a" * 64)
        assert payment.unlock_time == 1785361600000
        assert payment.external_dispute_unlock_time == 1785383200000
        assert payment.seller_vkey == "26524d1fdeadbeef"
        assert payment.agent_identifier.startswith("67ab0c92")

    async def test_falls_back_to_configured_identity_when_absent(self):
        response = {"status": "success", "data": {"blockchainIdentifier": "abc123"}}
        payment = await client_with(routed(payment_response=response)).create_payment("e1b9f7", "a" * 64)
        assert payment.seller_vkey == SETTINGS.seller_vkey
        assert payment.agent_identifier == SETTINGS.agent_identifier
        assert payment.unlock_time is None

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
    async def test_resolves_by_identifier_rather_than_listing(self):
        # GET /payment answers with Web3CardanoV1 only unless it is filtered,
        # so a V2 payment never appears there and the seller waits forever on
        # funds that are already locked.
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["json"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"status": "success", "data": {
                "onChainState": "FundsLocked", "NextAction": {"requestedAction": "None"},
            }})

        assert await client_with(handler).payment_state("mine") == "FundsLocked"
        assert captured["method"] == "POST"
        assert captured["url"].endswith("/payment/resolve-blockchain-identifier")
        assert captured["json"] == {"network": "Preprod", "blockchainIdentifier": "mine"}

    async def test_absent_payment_gives_none(self):
        def handler(request):
            return httpx.Response(404, json={"status": "error", "error": {"message": "Payment not found"}})

        assert await client_with(handler).payment_state("mine") is None

    async def test_other_errors_still_raise(self):
        def handler(request):
            return httpx.Response(401, json={"status": "error", "error": {"message": "Unauthorized"}})

        with pytest.raises(PaymentError, match="Unauthorized"):
            await client_with(handler).payment_state("mine")


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
