"""MIP-003 HTTP surface, driven through FastAPI's test client."""

import pytest
from fastapi.testclient import TestClient

from chronos_masumi.app import create_app
from chronos_masumi.config import MasumiSettings
from chronos_masumi.jobs import JobManager
from chronos_masumi.payments import PaymentError, PaymentRequest

BUYER = "e1b9f721e80d9fb5c917b4e9e0"


def settings(ready: bool = True) -> MasumiSettings:
    return MasumiSettings(
        payment_api_key="masumi-payment-testkey" if ready else "",
        payment_service_url="https://payment.example/api/v1",
        network="Preprod",
        agent_identifier="67ab0c92" + "f" * 50 if ready else "",
        seller_vkey="26524d1f",
        source_index=0,
    )


class FakePayments:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail

    async def create_payment(self, identifier, input_hash):
        if self.fail:
            raise self.fail
        return PaymentRequest(
            blockchain_identifier="chain-1",
            pay_by_time=1785333571000,
            submit_result_time=1785340000000,
            unlock_time=1785361600000,
            external_dispute_unlock_time=1785383200000,
            agent_identifier="67ab0c92" + "f" * 50,
            seller_vkey="26524d1f",
            requested_funds=[{"unit": "16a55b", "amount": "1000000"}],
        )

    async def payment_state(self, blockchain_identifier):
        return None

    async def submit_result(self, blockchain_identifier, output_hash):
        return {}


async def handler(identifier, input_data):
    return "# digest"


def build(ready=True, payments=None) -> TestClient:
    manager = JobManager(payments or FakePayments(), handler)
    return TestClient(create_app(settings(ready), manager))


class TestAvailability:
    def test_reports_available_when_configured(self):
        with build() as client:
            body = client.get("/availability").json()
            assert body["status"] == "available"
            assert body["type"] == "masumi-agent"

    def test_reports_unavailable_when_payment_config_missing(self):
        with build(ready=False) as client:
            body = client.get("/availability").json()
            assert body["status"] == "unavailable"
            assert "AGENT_IDENTIFIER" in body["message"]


class TestInputSchema:
    def test_returns_mip003_shape(self):
        with build() as client:
            body = client.get("/input_schema").json()
            ids = [f["id"] for f in body["input_data"]]
            assert ids == ["topic", "timeframe", "limit"]

    def test_every_field_declares_type_and_name(self):
        with build() as client:
            for field in client.get("/input_schema").json()["input_data"]:
                assert field["type"] and field["name"]


class TestStartJob:
    def test_creates_a_job_awaiting_payment(self):
        with build() as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents"},
            })
            assert r.status_code == 200
            body = r.json()
            assert body["blockchainIdentifier"] == "chain-1"
            assert len(body["input_hash"]) == 64
            # The job state lives on /status; MIP-003 keeps it out of this body.
            state = client.get("/status", params={"job_id": body["id"]}).json()
            assert state["status"] == "awaiting_payment"

    def test_returns_every_field_mip003_requires(self):
        # A buyer builds POST /purchase from this response alone. They have no
        # key for our payment node, so anything omitted here is unrecoverable
        # and the agent becomes unpayable by anyone outside our own account.
        with build() as client:
            body = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents"},
            }).json()

        for name in ("id", "blockchainIdentifier", "agentIdentifier", "sellerVKey",
                     "identifierFromPurchaser", "input_hash"):
            assert body.get(name), f"missing or empty: {name}"
        for name in ("payByTime", "submitResultTime", "unlockTime",
                     "externalDisputeUnlockTime"):
            # Unix milliseconds as integers. Seconds would be the wrong value:
            # the seller signature in blockchainIdentifier covers milliseconds.
            assert isinstance(body.get(name), int), f"{name} must be an int"
            assert body[name] > 1_700_000_000_000, f"{name} looks like seconds"

        assert body["identifierFromPurchaser"] == BUYER

    def test_returns_nothing_beyond_the_standard(self):
        # A consumer validating against a strict schema rejects the entire body
        # over one unrecognised key, so an extra field is as fatal as a missing
        # one. identifier_from_seller in particular is not in the standard.
        with build() as client:
            body = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents"},
            }).json()

        assert set(body) == {
            "id", "blockchainIdentifier", "payByTime", "submitResultTime",
            "unlockTime", "externalDisputeUnlockTime", "agentIdentifier",
            "sellerVKey", "identifierFromPurchaser", "input_hash", "inputHash",
        }

    def test_carries_the_hash_under_both_spellings(self):
        # MIP-003's table says input_hash; the masumi SDK ships inputHash and
        # notes that Sokosumi expects camelCase. Answer to both.
        with build() as client:
            body = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents"},
            }).json()
        assert body["inputHash"] == body["input_hash"]
        assert len(body["inputHash"]) == 64

    def test_rejects_non_hex_identifier_with_a_usable_message(self):
        with build() as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": "not-hex-at-all",
                "input_data": {"topic": "AI agents"},
            })
            assert r.status_code == 400
            assert "hex" in r.json()["detail"]

    def test_rejects_when_payments_are_not_configured(self):
        with build(ready=False) as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER, "input_data": {"topic": "x"}})
            assert r.status_code == 503

    def test_payment_service_error_surfaces_as_502_not_500(self):
        payments = FakePayments(fail=PaymentError("400: V2 requires supportedPaymentSourceIndex"))
        with build(payments=payments) as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER, "input_data": {"topic": "x"}})
            assert r.status_code == 502
            assert "supportedPaymentSourceIndex" in r.json()["detail"]

    def test_malformed_body_is_rejected(self):
        with build() as client:
            assert client.post("/start_job", json={"input_data": {}}).status_code == 422


class TestStatus:
    def test_returns_job_state(self):
        with build() as client:
            job_id = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents"},
            }).json()["id"]

            body = client.get("/status", params={"job_id": job_id}).json()
            assert body["job_id"] == job_id
            assert body["status"] == "awaiting_payment"
            assert "result" not in body

    def test_unknown_job_is_404(self):
        with build() as client:
            assert client.get("/status", params={"job_id": "nope"}).status_code == 404

    def test_missing_job_id_is_422(self):
        with build() as client:
            assert client.get("/status").status_code == 422


class TestHealth:
    def test_reports_configuration_state(self):
        with build() as client:
            body = client.get("/health").json()
            assert body["status"] == "ok" and body["configured"] is True


class TestInputDataShapes:
    """MIP-003 documents input_data as key/value pairs; marketplace clients send that."""

    def test_accepts_mip003_key_value_array(self):
        with build() as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": [
                    {"key": "topic", "value": "AI agents"},
                    {"key": "timeframe", "value": "7d"},
                    {"key": "limit", "value": 5},
                ],
            })
            assert r.status_code == 200
            assert len(r.json()["input_hash"]) == 64

    def test_object_and_array_forms_hash_identically(self):
        # Same logical input must bind to the same payment, or decision logging
        # would depend on which wire shape the buyer happened to use.
        with build() as client:
            as_object = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": {"topic": "AI agents", "timeframe": "7d"},
            }).json()["input_hash"]
            as_array = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER,
                "input_data": [
                    {"key": "topic", "value": "AI agents"},
                    {"key": "timeframe", "value": "7d"},
                ],
            }).json()["input_hash"]
            assert as_object == as_array

    def test_rejects_a_shape_it_cannot_interpret(self):
        with build() as client:
            r = client.post("/start_job", json={
                "identifier_from_purchaser": BUYER, "input_data": "just a string"})
            assert r.status_code == 422


class TestDocsUsability:
    def test_openapi_ships_a_valid_hex_example(self):
        import re

        with build() as client:
            schema = client.get("/openapi.json").json()
            examples = schema["components"]["schemas"]["StartJobRequest"]["examples"]
            identifier = examples[0]["identifier_from_purchaser"]
            # The default FastAPI placeholder is "string", which is not hex and
            # makes the first click in Swagger fail.
            assert re.match(r"^[0-9a-fA-F]+$", identifier)

    def test_examples_cover_both_input_shapes(self):
        with build() as client:
            examples = client.get("/openapi.json").json()["components"]["schemas"]["StartJobRequest"]["examples"]
            shapes = {type(e["input_data"]).__name__ for e in examples}
            assert shapes == {"dict", "list"}
