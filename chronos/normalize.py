"""URL and text normalization shared by sources and the pipeline."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "mc_", "pk_")
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mkt_tok",
    "ref",
    "referrer",
    "source",
    "cmpid",
    "guccounter",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def canonical_url(url: str) -> str:
    """Strip tracking params, fragments and trailing slashes so equal articles compare equal."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if (scheme == "https" and netloc.endswith(":443")) or (
        scheme == "http" and netloc.endswith(":80")
    ):
        netloc = netloc.rsplit(":", 1)[0]

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_KEYS
        and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def article_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()


def clean_text(raw: str | None, limit: int = 600) -> str:
    """Turn feed HTML into a plain-text snippet."""
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def first_sentences(text: str, count: int = 2) -> str:
    """Extractive fallback summary."""
    if not text:
        return ""
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    return " ".join(sentences[:count]) if sentences else text
