"""MIP-003 Agentic Service API for Chronos."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from agent import NoResultsError, process_job
from chronos_masumi.config import MasumiSettings, load_masumi_settings
from chronos_masumi.jobs import JobManager
from chronos_masumi.payments import PaymentClient, PaymentError
from chronos_masumi.schema import INPUT_SCHEMA

logger = logging.getLogger(__name__)

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
MAX_ACTIVE_JOBS = 20


class StartJobRequest(BaseModel):
    identifier_from_purchaser: str = Field(..., min_length=1, max_length=64)
    input_data: dict


def create_app(settings: MasumiSettings | None = None, manager: JobManager | None = None) -> FastAPI:
    settings = settings or load_masumi_settings()
    manager = manager or JobManager(PaymentClient(settings), process_job)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.ready:
            manager.start_polling()
            logger.info("agent %s ready on %s", settings.agent_identifier[:24], settings.network)
        else:
            # Serving without payment config is useful for probing the schema, but
            # must never look healthy enough to accept paid work.
            logger.warning("payment config incomplete: %s", ", ".join(settings.missing()))
        yield
        await manager.stop_polling()

    app = FastAPI(title="Chronos — MIP-003 Agentic Service", version="0.1.0", lifespan=lifespan)

    @app.get("/availability")
    async def availability():
        if not settings.ready:
            return {
                "status": "unavailable",
                "type": "masumi-agent",
                "message": "Payment configuration incomplete: " + ", ".join(settings.missing()),
            }
        if manager.active_count >= MAX_ACTIVE_JOBS:
            return {
                "status": "unavailable",
                "type": "masumi-agent",
                "message": "At capacity, try again shortly",
            }
        return {"status": "available", "type": "masumi-agent", "message": "Server operational"}

    @app.get("/input_schema")
    async def input_schema():
        return INPUT_SCHEMA

    @app.post("/start_job")
    async def start_job(request: StartJobRequest):
        if not settings.ready:
            raise HTTPException(503, "Agent not configured for payments")
        # The payment service requires a hex nonce. Rejecting it here gives the
        # buyer a usable message instead of a 500 wrapping the service's error.
        if not HEX_RE.match(request.identifier_from_purchaser):
            raise HTTPException(400, "identifier_from_purchaser must be a hex string")
        if manager.active_count >= MAX_ACTIVE_JOBS:
            raise HTTPException(503, "At capacity, try again shortly")

        try:
            job = await manager.start_job(request.identifier_from_purchaser, request.input_data)
        except PaymentError as exc:
            logger.error("payment creation failed: %s", exc)
            raise HTTPException(502, f"Payment service rejected the request: {exc}") from exc

        return {
            "job_id": job.id,
            "identifier_from_seller": job.id,
            "blockchainIdentifier": job.blockchain_identifier,
            "payByTime": job.pay_by_time,
            "submitResultTime": job.submit_result_time,
            "requestedFunds": job.requested_funds,
            "input_hash": job.input_hash,
            "status": job.status,
        }

    @app.get("/status")
    async def status(job_id: str = Query(...)):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, f"No job with id {job_id}")
        return job.public_state()

    @app.get("/health")
    async def health():
        return {"status": "ok", "configured": settings.ready, "active_jobs": manager.active_count}

    return app
