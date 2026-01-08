"""Tests for webhook HMAC-SHA256 signing.

Per Traceability Matrix WH-001: tests/test_webhook_signing.py::test_hmac_correct
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from idis.services.webhooks.signing import (
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    WebhookSignature,
    compute_hmac_signature,
    sign_webhook_payload,
    verify_webhook_signature,
)


class TestComputeHmacSignature:
    """Tests for compute_hmac_signature function."""

    def test_deterministic_signature(self) -> None:
        """Same inputs always produce same signature."""
        secret = "test-secret-key"
        timestamp = 1704067200
        payload = b'{"event":"deal.created","deal_id":"123"}'

        sig1 = compute_hmac_signature(secret, timestamp, payload)
        sig2 = compute_hmac_signature(secret, timestamp, payload)

        assert sig1 == sig2

    def test_canonical_string_format(self) -> None:
        """Verify canonical string is "{timestamp}.{raw_body}"."""
        secret = "my-secret"
        timestamp = 1704067200
        payload = b'{"test":true}'

        expected_canonical = f"{timestamp}.".encode() + payload
        expected_sig = hmac.new(
            key=secret.encode("utf-8"),
            msg=expected_canonical,
            digestmod=hashlib.sha256,
        ).hexdigest()

        actual_sig = compute_hmac_signature(secret, timestamp, payload)

        assert actual_sig == expected_sig

    def test_different_secret_different_signature(self) -> None:
        """Different secrets produce different signatures."""
        timestamp = 1704067200
        payload = b'{"event":"test"}'

        sig1 = compute_hmac_signature("secret-1", timestamp, payload)
        sig2 = compute_hmac_signature("secret-2", timestamp, payload)

        assert sig1 != sig2

    def test_different_timestamp_different_signature(self) -> None:
        """Different timestamps produce different signatures."""
        secret = "test-secret"
        payload = b'{"event":"test"}'

        sig1 = compute_hmac_signature(secret, 1704067200, payload)
        sig2 = compute_hmac_signature(secret, 1704067201, payload)

        assert sig1 != sig2

    def test_different_payload_different_signature(self) -> None:
        """Different payloads produce different signatures."""
        secret = "test-secret"
        timestamp = 1704067200

        sig1 = compute_hmac_signature(secret, timestamp, b'{"a":1}')
        sig2 = compute_hmac_signature(secret, timestamp, b'{"a":2}')

        assert sig1 != sig2

    def test_empty_payload(self) -> None:
        """Empty payload produces valid signature."""
        sig = compute_hmac_signature("secret", 1704067200, b"")

        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_hex_digest_format(self) -> None:
        """Signature is valid hex string of expected length."""
        sig = compute_hmac_signature("secret", 1704067200, b"payload")

        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


class TestSignWebhookPayload:
    """Tests for sign_webhook_payload function."""

    def test_hmac_correct(self) -> None:
        """Required test per Traceability Matrix WH-001."""
        secret = "webhook-secret-abc123"
        timestamp = 1704067200
        payload = b'{"event_type":"deal.created","deal_id":"550e8400-e29b-41d4-a716-446655440000"}'

        result = sign_webhook_payload(secret, timestamp, payload)

        assert isinstance(result, WebhookSignature)
        assert result.timestamp == timestamp
        assert len(result.signature) == 64

        expected_canonical = f"{timestamp}.".encode() + payload
        expected_sig = hmac.new(
            key=secret.encode("utf-8"),
            msg=expected_canonical,
            digestmod=hashlib.sha256,
        ).hexdigest()
        assert result.signature == expected_sig

    def test_returns_correct_headers(self) -> None:
        """Headers dict contains timestamp and signature."""
        result = sign_webhook_payload("secret", 1704067200, b'{"test":1}')

        assert HEADER_TIMESTAMP in result.headers
        assert HEADER_SIGNATURE in result.headers
        assert result.headers[HEADER_TIMESTAMP] == "1704067200"
        assert result.headers[HEADER_SIGNATURE].startswith("sha256=")

    def test_signature_header_format(self) -> None:
        """Signature header has format 'sha256=<hex>'."""
        result = sign_webhook_payload("secret", 1704067200, b"body")

        sig_header = result.headers[HEADER_SIGNATURE]
        assert sig_header.startswith("sha256=")
        hex_part = sig_header[7:]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_headers_dict_has_expected_keys(self) -> None:
        """Headers dict has exactly the expected keys."""
        result = sign_webhook_payload("secret", 1704067200, b"body")

        assert set(result.headers.keys()) == {HEADER_TIMESTAMP, HEADER_SIGNATURE}

    def test_frozen_dataclass(self) -> None:
        """WebhookSignature is immutable."""
        result = sign_webhook_payload("secret", 1704067200, b"body")

        with pytest.raises(AttributeError):
            result.timestamp = 9999999999  # type: ignore[misc]


class TestVerifyWebhookSignature:
    """Tests for verify_webhook_signature function."""

    def test_valid_signature_verifies(self) -> None:
        """Valid signature returns True."""
        secret = "test-secret"
        timestamp = 1704067200
        payload = b'{"event":"test"}'

        result = sign_webhook_payload(secret, timestamp, payload)

        assert verify_webhook_signature(secret, timestamp, payload, result.signature)

    def test_valid_signature_with_prefix_verifies(self) -> None:
        """Valid signature with sha256= prefix returns True."""
        secret = "test-secret"
        timestamp = 1704067200
        payload = b'{"event":"test"}'

        result = sign_webhook_payload(secret, timestamp, payload)

        assert verify_webhook_signature(secret, timestamp, payload, f"sha256={result.signature}")

    def test_invalid_signature_fails(self) -> None:
        """Invalid signature returns False."""
        secret = "test-secret"
        timestamp = 1704067200
        payload = b'{"event":"test"}'

        assert not verify_webhook_signature(secret, timestamp, payload, "invalid-signature")

    def test_wrong_secret_fails(self) -> None:
        """Wrong secret produces verification failure."""
        timestamp = 1704067200
        payload = b'{"event":"test"}'

        result = sign_webhook_payload("correct-secret", timestamp, payload)

        assert not verify_webhook_signature("wrong-secret", timestamp, payload, result.signature)

    def test_tampered_payload_fails(self) -> None:
        """Tampered payload produces verification failure."""
        secret = "test-secret"
        timestamp = 1704067200
        original_payload = b'{"amount":100}'
        tampered_payload = b'{"amount":1000}'

        result = sign_webhook_payload(secret, timestamp, original_payload)

        assert not verify_webhook_signature(secret, timestamp, tampered_payload, result.signature)

    def test_wrong_timestamp_fails(self) -> None:
        """Wrong timestamp produces verification failure."""
        secret = "test-secret"
        payload = b'{"event":"test"}'

        result = sign_webhook_payload(secret, 1704067200, payload)

        assert not verify_webhook_signature(secret, 1704067201, payload, result.signature)


class TestSecurityProperties:
    """Tests for security-critical properties."""

    def test_timing_safe_comparison(self) -> None:
        """Verification uses constant-time comparison (hmac.compare_digest)."""
        import hmac as hmac_module

        original_compare = hmac_module.compare_digest

        called_with_compare_digest = [False]

        def tracking_compare(a: str, b: str) -> bool:
            called_with_compare_digest[0] = True
            return original_compare(a, b)

        hmac_module.compare_digest = tracking_compare  # type: ignore[assignment]
        try:
            verify_webhook_signature("secret", 1704067200, b"body", "fakesig")
            assert called_with_compare_digest[0], "Should use hmac.compare_digest"
        finally:
            hmac_module.compare_digest = original_compare

    def test_unicode_secret_handled(self) -> None:
        """Unicode characters in secret are properly encoded."""
        secret = "secret-with-émojis-🔐"
        result = sign_webhook_payload(secret, 1704067200, b"body")

        assert verify_webhook_signature(secret, 1704067200, b"body", result.signature)

    def test_binary_payload_preserved(self) -> None:
        """Binary payload bytes are preserved exactly."""
        secret = "secret"
        timestamp = 1704067200
        payload = bytes([0x00, 0xFF, 0x7F, 0x80])

        result = sign_webhook_payload(secret, timestamp, payload)

        assert verify_webhook_signature(secret, timestamp, payload, result.signature)
