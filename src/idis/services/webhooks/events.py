"""Slice97 — versioned, safe webhook event envelope + builder (acceptance A2).

``build_webhook_event`` composes the project's sanitizers so a webhook event carries no raw private
content or secrets:

1. value-level: the event ``data`` is projected through the shared ``safe_public_summary`` sanitizer
   (drops paths, URIs, base64 blobs, excerpts, transcripts, exception text);
2. key-level (fail-closed backstop): the sanitized ``data`` is checked against the audit validator's
   ``REDACTION_BLOCKLIST`` — a short secret-like VALUE survives the value-level pass, so a
   blocklisted KEY (``secret`` / ``password`` / ``api_key`` / ``token`` / ``private_key`` ...) must
   be rejected. Unsafe data raises ``WebhookPayloadError`` (an unsafe event is never built).

``build_webhook_event`` is the redaction boundary; no thinner wrapper should be treated as one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from idis.services.webhooks.safe_payload import safe_public_summary_dict
from idis.validators.audit_event_validator import REDACTION_BLOCKLIST

WEBHOOK_EVENT_SCHEMA_VERSION = "1.0"


class WebhookPayloadError(ValueError):
    """Raised when a webhook event payload would carry a blocklisted (secret-like) key."""


class WebhookEvent(BaseModel):
    """Versioned, safe webhook event envelope (the delivered/persisted payload shape)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    event_id: str
    event_type: str
    occurred_at: str
    tenant_id: str
    resource_type: str
    resource_id: str
    data: dict[str, Any]


def _reject_blocklisted_keys(obj: object, path: str = "data") -> None:
    """Fail-closed on any blocklisted (secret-like) key, mirroring the audit validator's fatal rule.

    Exact-match against ``REDACTION_BLOCKLIST`` (case-insensitive), like ``_check_redaction``'s
    REDACTION_VIOLATION — so legitimate counts such as ``tokens_used`` are not false-positives.
    """
    if isinstance(obj, Mapping):
        for key, item in obj.items():
            if str(key).lower() in REDACTION_BLOCKLIST:
                raise WebhookPayloadError(
                    f"webhook payload key at {path}.{key} matches the redaction blocklist"
                )
            _reject_blocklisted_keys(item, f"{path}.{key}")
    elif isinstance(obj, Sequence) and not isinstance(obj, str | bytes):
        for index, item in enumerate(obj):
            _reject_blocklisted_keys(item, f"{path}[{index}]")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_webhook_event(
    *,
    event_type: str,
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    data: Mapping[str, Any] | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> WebhookEvent:
    """Build a versioned webhook event with a sanitized, secret-free ``data`` payload.

    Raises ``WebhookPayloadError`` if the (post-sanitization) data carries a blocklisted key.
    """
    safe_data = safe_public_summary_dict(dict(data or {}))
    _reject_blocklisted_keys(safe_data)
    return WebhookEvent(
        schema_version=WEBHOOK_EVENT_SCHEMA_VERSION,
        event_id=event_id or str(uuid4()),
        event_type=event_type,
        occurred_at=occurred_at or _now_iso(),
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        data=safe_data,
    )
