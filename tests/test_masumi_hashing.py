"""MIP-004 hashing. A buyer recomputes these independently, so behaviour is contractual."""

import hashlib
import json

import pytest

from chronos_masumi.hashing import hash_payload, input_hash, output_hash

BUYER = "e1b9f721e80d9fb5c917b4e9e0"


class TestHashPayload:
    def test_uses_semicolon_delimited_preimage(self):
        expected = hashlib.sha256(f"{BUYER};payload".encode()).hexdigest()
        assert hash_payload("payload", BUYER) == expected

    def test_delimiter_prevents_concatenation_ambiguity(self):
        # Without the delimiter ("ab","c") and ("a","bc") would collide.
        assert hash_payload("c", "ab") != hash_payload("bc", "a")

    def test_is_hex_sha256(self):
        digest = hash_payload("x", BUYER)
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


class TestInputHash:
    def test_key_order_does_not_change_the_hash(self):
        # JCS canonicalisation is the whole point: buyer and seller serialise
        # independently and must still agree.
        a = {"topic": "AI agents", "limit": 5, "timeframe": "7d"}
        b = {"limit": 5, "timeframe": "7d", "topic": "AI agents"}
        assert input_hash(a, BUYER) == input_hash(b, BUYER)

    def test_different_input_changes_the_hash(self):
        assert input_hash({"topic": "a"}, BUYER) != input_hash({"topic": "b"}, BUYER)

    def test_different_buyer_changes_the_hash(self):
        payload = {"topic": "AI agents"}
        assert input_hash(payload, BUYER) != input_hash(payload, "ff00")

    def test_nested_values_are_canonicalised(self):
        a = {"a": {"x": 1, "y": 2}}
        b = {"a": {"y": 2, "x": 1}}
        assert input_hash(a, BUYER) == input_hash(b, BUYER)


class TestOutputHash:
    def test_output_is_json_escaped_before_hashing(self):
        text = 'line one\n"quoted"'
        escaped = json.dumps(text, ensure_ascii=False)[1:-1]
        assert output_hash(text, BUYER) == hash_payload(escaped, BUYER)

    def test_escaping_matters_for_markdown(self):
        # Every digest contains newlines; hashing raw would disagree with the
        # reference implementation on literally every job.
        text = "# Digest\n\nBody"
        assert output_hash(text, BUYER) != hash_payload(text, BUYER)

    def test_non_string_is_rejected(self):
        with pytest.raises(TypeError):
            output_hash({"not": "a string"}, BUYER)

    def test_empty_output_is_hashable(self):
        assert len(output_hash("", BUYER)) == 64
