"""API regression tests for webhook trust-boundary behavior."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import JsonlFileAuditSink


def _admin_api_keys_json(tenant_id: str, api_key: str = "test-admin-key") -> str:
    """Create an ADMIN API-key registry for webhook API tests."""
    return json.dumps(
        {
            api_key: {
                "tenant_id": tenant_id,
                "actor_id": "admin-actor",
                "name": "Admin Actor",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ADMIN"],
            }
        }
    )


def test_create_webhook_emits_audit_with_created_webhook_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful createWebhook responses include the created webhook as audit resource."""
    tenant_id = str(uuid.uuid4())
    audit_log_path = tmp_path / "webhook_audit.jsonl"
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _admin_api_keys_json(tenant_id))
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))

    sink = JsonlFileAuditSink(str(audit_log_path))
    app = create_app(audit_sink=sink, service_region="us-east-1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/webhooks",
        headers={"X-IDIS-API-Key": "test-admin-key"},
        json={
            "url": "https://example.com/webhook",
            "events": ["deal.created"],
            "secret": "super-secret",
            "active": True,
        },
    )

    assert response.status_code == 201
    webhook_id = response.json()["webhook_id"]

    event = json.loads(audit_log_path.read_text().strip())
    assert event["event_type"] == "webhook.created"
    assert event["resource"]["resource_type"] == "webhook"
    assert event["resource"]["resource_id"] == webhook_id
