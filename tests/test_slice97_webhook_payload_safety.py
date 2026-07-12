"""Slice97 Task 1 — safe webhook event payload builder (Acceptance A2).

RED-first. A webhook event payload must carry NO raw private content or secrets. This is enforced by
composing the project's existing sanitizers, caller-side:
- value-level: ``safe_public_summary`` (the run-summary sanitizer, lifted into a shared module
  ``idis.services.webhooks.safe_payload`` and re-exported by ``routes/runs.py`` so there is ONE
  implementation), which drops paths, URIs, base64 blobs, excerpts, transcripts, exception text;
- key-level: a fail-closed check against the audit validator's ``REDACTION_BLOCKLIST`` (secret /
  password / api_key / token / private_key ...), because a short secret-like VALUE survives the
  value-level pass — the KEY must be rejected.

``build_webhook_event`` composes both and returns a versioned, JSON-safe ``WebhookEvent`` envelope.
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from idis.services.webhooks.events import (
    WEBHOOK_EVENT_SCHEMA_VERSION,
    WebhookEvent,
    WebhookPayloadError,
    build_webhook_event,
)
from idis.services.webhooks.safe_payload import safe_public_summary, safe_public_summary_dict

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_RUN = "11111111-1111-1111-1111-111111111111"

# Substrings that must never appear anywhere in a serialized webhook event.
_FORBIDDEN = (
    "secret",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
    "token",
    "bearer",
    "transcript",
    "confidential",
    "revenue was",
    "ebitda was",
    "/var/",
    "s3://",
    "://",
)


def _assert_no_forbidden(obj: Any) -> None:
    blob = json.dumps(obj, sort_keys=True, default=str).lower()
    for part in _FORBIDDEN:
        assert part not in blob, f"forbidden substring {part!r} leaked into: {blob}"


# --- one implementation (no drift) ---


def test_runs_reexports_the_single_sanitizer_implementation() -> None:
    # The sanitizer must have ONE implementation shared by routes/runs.py and the webhook builder.
    from idis.api.routes import runs as runs_module

    assert runs_module._safe_public_run_summary_dict is safe_public_summary_dict


# --- value-level sanitizer strips private content ---


def test_safe_public_summary_strips_sensitive_keys_and_values() -> None:
    unsafe = {
        "run_id": _RUN,  # safe id
        "status": "COMPLETED",  # safe enum
        "artifact_count": 3,  # safe count (allowlisted)
        "reproducibility_hashes": ["a" * 64],  # safe sha256 hex
        "transcript": "the CEO said revenue was 10m",  # sensitive KEY -> dropped
        "local_path": "/var/data/room/file.pdf",  # sensitive KEY (path) -> dropped
        "raw_excerpt": "EBITDA was 2M confidential",  # sensitive KEY -> dropped
        "note": "s3://bucket/private-object",  # sensitive VALUE (:// and /) -> dropped
    }
    safe = safe_public_summary(unsafe)
    assert isinstance(safe, dict)
    assert safe["run_id"] == _RUN
    assert safe["status"] == "COMPLETED"
    assert safe["artifact_count"] == 3
    assert safe["reproducibility_hashes"] == ["a" * 64]
    assert "transcript" not in safe
    assert "local_path" not in safe
    assert "raw_excerpt" not in safe
    assert "note" not in safe  # value dropped -> key absent
    _assert_no_forbidden(safe)


def test_safe_public_summary_dict_always_returns_dict() -> None:
    assert safe_public_summary_dict({"x": 1}) == {"x": 1}
    assert safe_public_summary_dict({}) == {}


# --- build_webhook_event: safe envelope ---


def test_build_webhook_event_produces_versioned_safe_envelope() -> None:
    event = build_webhook_event(
        event_type="run.completed",
        tenant_id=_TENANT,
        resource_type="run",
        resource_id=_RUN,
        data={"status": "COMPLETED", "artifact_count": 2, "local_path": "/var/x.pdf"},
        event_id="99999999-9999-9999-9999-999999999999",
        occurred_at="2026-07-10T00:00:00Z",
    )
    assert isinstance(event, WebhookEvent)
    assert event.schema_version == WEBHOOK_EVENT_SCHEMA_VERSION
    assert event.event_type == "run.completed"
    assert event.tenant_id == _TENANT
    assert event.resource_type == "run" and event.resource_id == _RUN
    assert event.event_id == "99999999-9999-9999-9999-999999999999"
    assert event.occurred_at == "2026-07-10T00:00:00Z"
    # data sanitized: the safe fields remain, the path is dropped
    assert event.data["status"] == "COMPLETED"
    assert event.data["artifact_count"] == 2
    assert "local_path" not in event.data
    # JSON-serializable + safe end-to-end
    payload = event.model_dump()
    json.dumps(payload)
    _assert_no_forbidden(payload)


def test_build_webhook_event_generates_id_and_timestamp_when_omitted() -> None:
    event = build_webhook_event(
        event_type="run.claimed",
        tenant_id=_TENANT,
        resource_type="run",
        resource_id=_RUN,
        data={"mode": "SNAPSHOT"},
    )
    assert event.event_id and isinstance(event.event_id, str)
    assert event.occurred_at and isinstance(event.occurred_at, str)


# --- build_webhook_event: key-level fail-closed backstop ---


@pytest.mark.parametrize(
    "bad_key", ["secret", "password", "api_key", "access_token", "private_key"]
)
def test_build_webhook_event_rejects_blocklisted_keys(bad_key: str) -> None:
    # A short secret-like VALUE survives the value-level pass; the KEY must be rejected fail-closed.
    with pytest.raises(WebhookPayloadError):
        build_webhook_event(
            event_type="run.completed",
            tenant_id=_TENANT,
            resource_type="run",
            resource_id=_RUN,
            data={bad_key: "x9", "status": "COMPLETED"},
        )


def test_build_webhook_event_rejects_nested_blocklisted_key() -> None:
    with pytest.raises(WebhookPayloadError):
        build_webhook_event(
            event_type="run.completed",
            tenant_id=_TENANT,
            resource_type="run",
            resource_id=_RUN,
            data={"outer": {"api_key": "sk-abc"}},
        )
