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
        return PaymentRequest("chain-1", "1785333571000", "1785340000000",
                              [{"unit": "16a55b", "amount": "1000000"}])

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
            assert body["status"] == "awaiting_payment"
            assert body["blockchainIdentifier"] == "chain-1"
            assert len(body["input_hash"]) == 64

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
            }).json()["job_id"]

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
