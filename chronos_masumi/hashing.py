"""MIP-004 decision logging hashes.

A buyer independently recomputes these from the input they sent and the output
they received, and requests a refund if they disagree. So this must match the
reference implementation byte for byte — the algorithm here is deliberately a
faithful reimplementation of masumi's helper functions, not an improvement on
them.

Pre-image is `f"{identifier_from_purchaser};{payload}"`, SHA-256, hex.
The semicolon prevents concatenation ambiguity: without it, ("ab", "c") and
("a", "bc") would hash identically.
"""

from __future__ import annotations

import hashlib
import json

import canonicaljson


def hash_payload(payload: str, identifier_from_purchaser: str) -> str:
    pre_image = f"{identifier_from_purchaser};{payload}"
    return hashlib.sha256(pre_image.encode("utf-8")).hexdigest()


def input_hash(input_data: dict, identifier_from_purchaser: str) -> str:
    """Hash the job input. Serialised with JCS (RFC 8785) so key order cannot vary."""
    canonical = canonicaljson.encode_canonical_json(input_data).decode("utf-8")
    return hash_payload(canonical, identifier_from_purchaser)


def output_hash(output: str, identifier_from_purchaser: str) -> str:
    """Hash the job output.

    The output is JSON-escaped and then stripped of its surrounding quotes, which
    is what the reference implementation does. Skipping the escape would produce
    a different hash for any output containing a quote or newline — i.e. every
    markdown digest — and every such job would look tampered with.
    """
    if not isinstance(output, str):
        raise TypeError("output must be a string")
    escaped = json.dumps(output, ensure_ascii=False)[1:-1]
    return hash_payload(escaped, identifier_from_purchaser)
