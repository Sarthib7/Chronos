"""Environment and feed-list configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import dotenv_values, load_dotenv

load_dotenv()

PACKAGE_DIR = Path(__file__).parent
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def clean_value(value: str | None) -> str:
    """Strip control characters and surrounding whitespace.

    A shell profile can export a variable holding nothing but an escape
    character (a paste artifact). str.strip() leaves control characters intact,
    so such a value looks set, is truthy, and shadows the real one.
    """
    return _CONTROL_CHARS.sub("", value or "").strip()


def read_env(name: str) -> str:
    """Read a setting, letting .env fill in for a blank shell variable.

    load_dotenv() never overrides an already-set variable, so a junk
    OPENROUTER_API_KEY exported by a shell profile silently shadows the real key
    in .env. A blank export carries no intent, so it falls through to the file;
    a genuine exported value still wins, which is what deployments depend on.
    """
    return clean_value(os.getenv(name)) or clean_value(dotenv_values().get(name))


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
    # A stub value must not count as a key, otherwise every run pays a doomed
    # API call before falling back to extractive summaries.
    raw_key = read_env("OPENROUTER_API_KEY")
    key = raw_key if len(raw_key) >= MIN_KEY_LENGTH else None
    return Settings(
        openrouter_api_key=key,
        openrouter_model=read_env("OPENROUTER_MODEL") or DEFAULT_MODEL,
        http_timeout=float(read_env("HTTP_TIMEOUT") or 15),
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
