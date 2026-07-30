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
from chronos_masumi.schema import INPUT_SCHEMA, normalize_input_data

logger = logging.getLogger(__name__)

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
MAX_ACTIVE_JOBS = 20


EXAMPLE_IDENTIFIER = "a1b2c3d4e5f60718293a4b5c6d"
EXAMPLE_INPUT = {"topic": "AI agents", "timeframe": "7d", "limit": 5}


class StartJobRequest(BaseModel):
    identifier_from_purchaser: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "A unique nonce identifying this purchase. Must be a hex string "
            "(0-9, a-f) — the payment service rejects anything else."
        ),
        examples=[EXAMPLE_IDENTIFIER],
    )
    input_data: dict | list = Field(
        ...,
        description=(
            "Job input, either as an object or as MIP-003 key/value pairs: "
            '[{"key": "topic", "value": "AI agents"}]'
        ),
        examples=[EXAMPLE_INPUT],
    )

    # Without a worked example, Swagger's "Try it out" prefills
    # identifier_from_purchaser with "string", which is not hex, so the first
    # thing anyone reading the docs does is get a 400.
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"identifier_from_purchaser": EXAMPLE_IDENTIFIER, "input_data": EXAMPLE_INPUT},
                {
                    "identifier_from_purchaser": EXAMPLE_IDENTIFIER,
                    "input_data": [
                        {"key": "topic", "value": "AI agents"},
                        {"key": "timeframe", "value": "7d"},
                        {"key": "limit", "value": 5},
                    ],
                },
            ]
        }
    }


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
            input_data = normalize_input_data(request.input_data)
        except TypeError as exc:
            raise HTTPException(400, str(exc)) from exc

        try:
            job = await manager.start_job(request.identifier_from_purchaser, input_data)
        except PaymentError as exc:
            logger.error("payment creation failed: %s", exc)
            raise HTTPException(502, f"Payment service rejected the request: {exc}") from exc

        # Exactly the ten fields MIP-003 defines, and nothing else. A buyer
        # builds POST /purchase straight from these and holds no credentials on
        # our payment node, so a missing field is one they cannot look up
        # anywhere else. Extra keys are equally unsafe: a consumer validating
        # this response against a strict schema rejects the whole body over one
        # unrecognised name, so convenience fields do not belong here.
        return {
            "id": job.id,
            "blockchainIdentifier": job.blockchain_identifier,
            "payByTime": job.pay_by_time,
            "submitResultTime": job.submit_result_time,
            "unlockTime": job.unlock_time,
            "externalDisputeUnlockTime": job.external_dispute_unlock_time,
            "agentIdentifier": job.agent_identifier,
            "sellerVKey": job.seller_vkey,
            "identifierFromPurchaser": job.identifier_from_purchaser,
            "input_hash": job.input_hash,
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
