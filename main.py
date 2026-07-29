#!/usr/bin/env python3
"""Chronos as a Masumi agentic service.

    uv run python main.py          # MIP-003 API server
    uv run python -m chronos "..."  # local CLI, no payments

Implements MIP-003 and the payment flow directly against the payment service's
HTTP API. The masumi SDK is not used: it cannot create payments for agents
registered as Web3CardanoV2.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402

from chronos_masumi.app import create_app  # noqa: E402
from chronos_masumi.config import server_port  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=server_port())
