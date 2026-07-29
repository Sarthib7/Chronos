"""Job lifecycle: awaiting payment → running → completed, plus the payment poller."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from chronos_masumi.hashing import input_hash, output_hash
from chronos_masumi.payments import (
    REFUND_STATES,
    STATE_FUNDS_LOCKED,
    PaymentClient,
    PaymentError,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 20

AWAITING_PAYMENT = "awaiting_payment"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
REFUNDED = "refunded"

JobHandler = Callable[[str, dict], Awaitable[str]]


@dataclass
class Job:
    id: str
    identifier_from_purchaser: str
    input_data: dict
    status: str = AWAITING_PAYMENT
    blockchain_identifier: str = ""
    pay_by_time: str | None = None
    submit_result_time: str | None = None
    requested_funds: list[dict] = field(default_factory=list)
    input_hash: str = ""
    output_hash: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def public_state(self) -> dict:
        """Only what a buyer is entitled to see."""
        body = {"job_id": self.id, "status": self.status}
        if self.status == AWAITING_PAYMENT:
            body["blockchainIdentifier"] = self.blockchain_identifier
            body["payByTime"] = self.pay_by_time
            body["requestedFunds"] = self.requested_funds
        if self.status == COMPLETED:
            body["result"] = self.result
            body["input_hash"] = self.input_hash
            body["output_hash"] = self.output_hash
        if self.status == FAILED:
            body["error"] = self.error
        return body


class JobManager:
    """Holds jobs in memory and drives them forward as payments confirm.

    In-memory is a deliberate limit for now: a restart loses in-flight jobs. It
    is survivable on Preprod, but a persistent store is required before mainnet,
    since a lost job means a buyer paid and receives nothing.
    """

    def __init__(self, payments: PaymentClient, handler: JobHandler):
        self.payments = payments
        self.handler = handler
        self.jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None

    async def start_job(self, identifier_from_purchaser: str, input_data: dict) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            identifier_from_purchaser=identifier_from_purchaser,
            input_data=input_data,
        )
        job.input_hash = input_hash(input_data, identifier_from_purchaser)

        payment = await self.payments.create_payment(identifier_from_purchaser, job.input_hash)
        job.blockchain_identifier = payment.blockchain_identifier
        job.pay_by_time = payment.pay_by_time
        job.submit_result_time = payment.submit_result_time
        job.requested_funds = payment.requested_funds

        self.jobs[job.id] = job
        logger.info("job %s created, awaiting payment", job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    @property
    def active_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status in (AWAITING_PAYMENT, RUNNING))

    async def execute(self, job: Job) -> None:
        """Run the agent, publish the decision hash, then release the result.

        The hash is submitted before the result is exposed. A buyer who could
        read the output before it was committed on-chain could dispute a result
        we had not yet bound ourselves to.
        """
        job.status = RUNNING
        try:
            result = await self.handler(job.identifier_from_purchaser, job.input_data)
            job.output_hash = output_hash(result, job.identifier_from_purchaser)
            await self.payments.submit_result(job.blockchain_identifier, job.output_hash)
            job.result = result
            job.status = COMPLETED
            logger.info("job %s completed", job.id)
        except Exception as exc:
            job.status = FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            logger.exception("job %s failed", job.id)

    async def poll_once(self) -> None:
        pending = [j for j in self.jobs.values() if j.status == AWAITING_PAYMENT]
        for job in pending:
            try:
                state = await self.payments.payment_state(job.blockchain_identifier)
            except PaymentError as exc:
                logger.warning("payment lookup failed for job %s: %s", job.id, exc)
                continue
            if state == STATE_FUNDS_LOCKED:
                logger.info("job %s funded, executing", job.id)
                await self.execute(job)
            elif state in REFUND_STATES:
                job.status = REFUNDED
                job.error = f"payment state: {state}"
                logger.info("job %s refunded/disputed (%s)", job.id, state)

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception("payment poll cycle failed")
            await asyncio.sleep(POLL_SECONDS)

    def start_polling(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop_polling(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
