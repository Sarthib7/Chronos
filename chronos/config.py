"""Environment and feed-list configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

PACKAGE_DIR = Path(__file__).parent
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str | None
    openrouter_model: str
    http_timeout: float

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


MIN_KEY_LENGTH = 20


def load_settings() -> Settings:
    # A blank or stub value in the shell environment must not count as a key,
    # otherwise every run pays a doomed API call before falling back.
    raw_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    key = raw_key if len(raw_key) >= MIN_KEY_LENGTH else None
    return Settings(
        openrouter_api_key=key,
        openrouter_model=os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL,
        http_timeout=float(os.getenv("HTTP_TIMEOUT") or 15),
    )


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    weight: float = 1.0


def load_feeds(path: Path | None = None) -> list[FeedConfig]:
    """Read the curated RSS list. Entries missing a name or url are skipped."""
    path = path or PACKAGE_DIR / "feeds.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    feeds = []
    for entry in raw.get("feeds", []):
        if not entry.get("name") or not entry.get("url"):
            continue
        feeds.append(
            FeedConfig(
                name=entry["name"],
                url=entry["url"],
                weight=float(entry.get("weight", 1.0)),
            )
        )
    return feeds
