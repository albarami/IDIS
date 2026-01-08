"""Webhook HMAC-SHA256 signing for IDIS.

Implements deterministic webhook payload signing per IDIS v6.3:
- API Contracts §6.1: HMAC signature with shared secret
- Canonical string: "{timestamp}.{raw_body}"
- Signature: hex digest of HMAC-SHA256(secret, canonical_string)

Headers produced:
- X-IDIS-Webhook-Timestamp: <timestamp>
- X-IDIS-Webhook-Signature: sha256=<hex>

SECURITY: Never log secrets or full signed headers containing secrets.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

HEADER_TIMESTAMP = "X-IDIS-Webhook-Timestamp"
HEADER_SIGNATURE = "X-IDIS-Webhook-Signature"


@dataclass(frozen=True)
class WebhookSignature:
    """Result of signing a webhook payload.

    Attributes:
        timestamp: Integer seconds (Unix epoch) used in signature.
        signature: Hex digest of HMAC-SHA256 signature.
        headers: Dict of headers to include with webhook delivery.
    """

    timestamp: int
    signature: str
    headers: dict[str, str]


def compute_hmac_signature(secret: str, timestamp: int, payload: bytes) -> str:
    """Compute HMAC-SHA256 signature for webhook payload.

    Canonical string format: "{timestamp}.{raw_body}"

    Args:
        secret: Shared secret for HMAC computation.
        timestamp: Integer seconds (Unix epoch).
        payload: Raw bytes of JSON payload body.

    Returns:
        Hex digest of HMAC-SHA256 signature.
    """
    canonical = f"{timestamp}.".encode() + payload
    signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=canonical,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return signature


def sign_webhook_payload(
    secret: str,
    timestamp: int,
    payload: bytes,
) -> WebhookSignature:
    """Sign a webhook payload with HMAC-SHA256.

    Creates a deterministic signature using the canonical string format
    "{timestamp}.{raw_body}" and returns headers for webhook delivery.

    Args:
        secret: Shared secret for HMAC computation.
        timestamp: Integer seconds (Unix epoch).
        payload: Raw bytes of JSON payload body.

    Returns:
        WebhookSignature with timestamp, signature, and headers dict.

    Example:
        >>> sig = sign_webhook_payload("my-secret", 1704067200, b'{"event":"test"}')
        >>> sig.headers
        {'X-IDIS-Webhook-Timestamp': '1704067200', 'X-IDIS-Webhook-Signature': 'sha256=...'}
    """
    signature = compute_hmac_signature(secret, timestamp, payload)

    headers = {
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_SIGNATURE: f"sha256={signature}",
    }

    return WebhookSignature(
        timestamp=timestamp,
        signature=signature,
        headers=headers,
    )


def verify_webhook_signature(
    secret: str,
    timestamp: int,
    payload: bytes,
    expected_signature: str,
) -> bool:
    """Verify a webhook signature.

    Used by webhook consumers to validate incoming webhook requests.

    Args:
        secret: Shared secret for HMAC computation.
        timestamp: Integer seconds from X-IDIS-Webhook-Timestamp header.
        payload: Raw bytes of request body.
        expected_signature: Signature from X-IDIS-Webhook-Signature header
                           (with or without "sha256=" prefix).

    Returns:
        True if signature matches, False otherwise.
    """
    if expected_signature.startswith("sha256="):
        expected_signature = expected_signature[7:]

    computed = compute_hmac_signature(secret, timestamp, payload)
    return hmac.compare_digest(computed, expected_signature)
