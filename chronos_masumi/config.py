"""Masumi-side configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from chronos.config import read_env

DEFAULT_PAYMENT_SERVICE_URL = "https://payment.masumi.network/api/v1"
DEFAULT_NETWORK = "Preprod"

# Selects the priced source by position in the agent's supported_payment_sources.
# Required for Web3CardanoV2 agents and forbidden for V1. Left unset it is
# derived from the registry, which only works when the agent advertises exactly
# one priced source; with several, the index picks the price, so it has to be
# chosen rather than guessed.


@dataclass(frozen=True)
class MasumiSettings:
    payment_api_key: str
    payment_service_url: str
    network: str
    agent_identifier: str
    seller_vkey: str
    source_index: int | None

    @property
    def ready(self) -> bool:
        """Whether paid jobs can actually be created."""
        return bool(self.payment_api_key and self.agent_identifier)

    def missing(self) -> list[str]:
        absent = []
        if not self.payment_api_key:
            absent.append("PAYMENT_API_KEY")
        if not self.agent_identifier:
            absent.append("AGENT_IDENTIFIER")
        return absent


def load_masumi_settings() -> MasumiSettings:
    configured_index = read_env("PAYMENT_SOURCE_INDEX")
    return MasumiSettings(
        payment_api_key=read_env("PAYMENT_API_KEY"),
        payment_service_url=read_env("PAYMENT_SERVICE_URL") or DEFAULT_PAYMENT_SERVICE_URL,
        network=read_env("NETWORK") or DEFAULT_NETWORK,
        agent_identifier=read_env("AGENT_IDENTIFIER"),
        seller_vkey=read_env("SELLER_VKEY"),
        source_index=int(configured_index) if configured_index else None,
    )


def server_port() -> int:
    return int(os.getenv("PORT") or 8080)
