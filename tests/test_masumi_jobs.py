"""Job lifecycle: payment detection, execution order, and failure handling."""

import pytest

from chronos_masumi.jobs import (
    AWAITING_PAYMENT,
    COMPLETED,
    FAILED,
    REFUNDED,
    JobManager,
)
from chronos_masumi.payments import PaymentError, PaymentRequest

BUYER = "e1b9f721e80d9fb5c917b4e9e0"


class FakePayments:
    def __init__(self, state=None):
        self.state = state
        self.submitted: list[tuple[str, str]] = []
        self.created: list[tuple[str, str]] = []

    async def create_payment(self, identifier, input_hash):
        self.created.append((identifier, input_hash))
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
        return self.state

    async def submit_result(self, blockchain_identifier, output_hash):
        self.submitted.append((blockchain_identifier, output_hash))
        return {"ok": True}


async def handler(identifier, input_data):
    return f"# digest for {input_data.get('topic')}"


class TestStartJob:
    async def test_creates_payment_and_awaits_it(self):
        payments = FakePayments()
        manager = JobManager(payments, handler)
        job = await manager.start_job(BUYER, {"topic": "AI agents"})

        assert job.status == AWAITING_PAYMENT
        assert job.blockchain_identifier == "chain-1"
        assert job.requested_funds[0]["amount"] == "1000000"
        assert manager.get(job.id) is job

    async def test_input_hash_is_computed_before_payment(self):
        # The payment request carries the input hash, binding the buyer's input
        # to the payment. Computing it later would defeat decision logging.
        payments = FakePayments()
        job = await JobManager(payments, handler).start_job(BUYER, {"topic": "x"})
        assert payments.created[0][1] == job.input_hash
        assert len(job.input_hash) == 64

    async def test_work_does_not_start_before_payment(self):
        ran = []

        async def tracking_handler(identifier, input_data):
            ran.append(1)
            return "out"

        await JobManager(FakePayments(), tracking_handler).start_job(BUYER, {"topic": "x"})
        assert ran == []


class TestPolling:
    async def test_funded_payment_triggers_execution(self):
        payments = FakePayments(state="FundsLocked")
        manager = JobManager(payments, handler)
        job = await manager.start_job(BUYER, {"topic": "AI agents"})

        await manager.poll_once()

        assert job.status == COMPLETED
        assert job.result == "# digest for AI agents"
        assert job.output_hash and len(job.output_hash) == 64

    async def test_unfunded_payment_leaves_job_waiting(self):
        manager = JobManager(FakePayments(state=None), handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        await manager.poll_once()
        assert job.status == AWAITING_PAYMENT

    async def test_refund_state_marks_job_refunded(self):
        manager = JobManager(FakePayments(state="RefundRequested"), handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        await manager.poll_once()
        assert job.status == REFUNDED

    async def test_lookup_failure_does_not_lose_the_job(self):
        payments = FakePayments()

        async def failing_state(blockchain_identifier):
            raise PaymentError("503: service unavailable")

        manager = JobManager(payments, handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        payments.payment_state = failing_state

        await manager.poll_once()
        assert job.status == AWAITING_PAYMENT  # retried next cycle, not dropped


class TestExecution:
    async def test_hash_is_submitted_before_result_is_exposed(self):
        """The buyer must not be able to read output we have not committed to."""
        payments = FakePayments(state="FundsLocked")
        observed = []

        async def submit(blockchain_identifier, output_hash):
            observed.append(("submit", None))
            return {}

        manager = JobManager(payments, handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        payments.submit_result = submit

        await manager.execute(job)
        assert observed == [("submit", None)]
        assert job.result is not None and job.status == COMPLETED

    async def test_handler_failure_marks_job_failed_without_submitting(self):
        payments = FakePayments(state="FundsLocked")

        async def boom(identifier, input_data):
            raise ValueError("no articles matched")

        manager = JobManager(payments, boom)
        job = await manager.start_job(BUYER, {"topic": "cardano"})
        await manager.execute(job)

        assert job.status == FAILED
        assert "no articles matched" in job.error
        assert payments.submitted == []  # never claim work we did not deliver

    async def test_submit_failure_marks_job_failed(self):
        payments = FakePayments(state="FundsLocked")

        async def failing_submit(blockchain_identifier, output_hash):
            raise PaymentError("400: bad hash")

        manager = JobManager(payments, handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        payments.submit_result = failing_submit

        await manager.execute(job)
        assert job.status == FAILED
        assert job.result is None


class TestPublicState:
    async def test_awaiting_payment_exposes_payment_details_only(self):
        manager = JobManager(FakePayments(), handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        body = job.public_state()
        assert body["status"] == AWAITING_PAYMENT
        assert "blockchainIdentifier" in body
        assert "result" not in body

    async def test_completed_exposes_result_and_hashes(self):
        manager = JobManager(FakePayments(state="FundsLocked"), handler)
        job = await manager.start_job(BUYER, {"topic": "x"})
        await manager.poll_once()
        body = job.public_state()
        assert body["result"] and body["input_hash"] and body["output_hash"]

    async def test_active_count_tracks_open_jobs(self):
        manager = JobManager(FakePayments(), handler)
        await manager.start_job(BUYER, {"topic": "a"})
        await manager.start_job(BUYER, {"topic": "b"})
        assert manager.active_count == 2
