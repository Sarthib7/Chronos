"""Tests for the Web3CardanoV2 payment workaround."""

import masumi_v2
from masumi_v2 import inject_v2_index, is_payment_request


def payment_payload(**overrides) -> dict:
    payload = {
        "agentIdentifier": "67ab0c92" + "f" * 50,
        "network": "Preprod",
        "paymentType": "Web3CardanoV1",
        "identifierFromPurchaser": "e1b9f721e80d9fb5c917b4e9e0",
        "inputHash": "a" * 64,
    }
    payload.update(overrides)
    return payload


class TestIsPaymentRequest:
    def test_recognises_a_payment_request(self):
        assert is_payment_request(payment_payload()) is True

    def test_ignores_unrelated_payloads(self):
        assert is_payment_request({"decisionHash": "abc", "identifier": "job-1"}) is False

    def test_ignores_partial_matches(self):
        assert is_payment_request({"agentIdentifier": "x"}) is False

    def test_ignores_non_dicts(self):
        assert is_payment_request(None) is False
        assert is_payment_request("string") is False
        assert is_payment_request([1, 2]) is False


class TestInjectV2Index:
    def test_adds_the_index(self):
        assert inject_v2_index(payment_payload())["supportedPaymentSourceIndex"] == 0

    def test_honours_a_custom_index(self):
        assert inject_v2_index(payment_payload(), index=2)["supportedPaymentSourceIndex"] == 2

    def test_does_not_overwrite_an_explicit_value(self):
        payload = payment_payload(supportedPaymentSourceIndex=3)
        assert inject_v2_index(payload)["supportedPaymentSourceIndex"] == 3

    def test_leaves_other_payloads_untouched(self):
        other = {"identifier": "job-1", "decisionHash": "abc"}
        assert inject_v2_index(dict(other)) == other

    def test_preserves_existing_fields(self):
        payload = inject_v2_index(payment_payload())
        assert payload["agentIdentifier"].startswith("67ab0c92")
        assert payload["identifierFromPurchaser"] == "e1b9f721e80d9fb5c917b4e9e0"


class TestApply:
    def test_patches_aiohttp_and_is_idempotent(self):
        import aiohttp

        original = aiohttp.ClientSession.post
        try:
            masumi_v2.apply()
            patched = aiohttp.ClientSession.post
            assert getattr(patched, "_chronos_v2_patch", False) is True

            masumi_v2.apply()  # second call must not re-wrap
            assert aiohttp.ClientSession.post is patched
        finally:
            aiohttp.ClientSession.post = original

    def test_patched_post_injects_into_payment_requests_only(self):
        import aiohttp

        original = aiohttp.ClientSession.post
        seen = []

        def fake_post(self, url, **kwargs):
            seen.append(kwargs.get("json"))
            return "sent"

        try:
            aiohttp.ClientSession.post = fake_post
            masumi_v2.apply()
            session = object.__new__(aiohttp.ClientSession)

            aiohttp.ClientSession.post(session, "/payment/", json=payment_payload())
            aiohttp.ClientSession.post(session, "/other", json={"identifier": "job-1"})

            assert seen[0]["supportedPaymentSourceIndex"] == 0
            assert "supportedPaymentSourceIndex" not in seen[1]
        finally:
            aiohttp.ClientSession.post = original
