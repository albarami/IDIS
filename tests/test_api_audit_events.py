"""Tests for Audit Events API (GET /v1/audit/events).

Tests the file-sink (JSONL) path for audit event queries with:
- Tenant isolation verification (cross-tenant returns empty)
- Pagination stability (deterministic ordering)
- Cursor-based pagination (fail-closed on invalid cursor)
- RBAC enforcement (only AUDITOR/ADMIN can access)

Run with: pytest -q tests/test_api_audit_events.py
"""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.sink import AUDIT_LOG_PATH_ENV, JsonlFileAuditSink

TENANT_A_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

API_KEY_TENANT_A_ADMIN = "test-key-tenant-a-admin"
API_KEY_TENANT_A_ANALYST = "test-key-tenant-a-analyst"
API_KEY_TENANT_B_ADMIN = "test-key-tenant-b-admin"
API_KEY_TENANT_A_AUDITOR = "test-key-tenant-a-auditor"


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """API keys configuration for testing."""
    return {
        API_KEY_TENANT_A_ADMIN: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-a-admin",
            "name": "Tenant A Admin",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN"],
        },
        API_KEY_TENANT_A_ANALYST: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-a-analyst",
            "name": "Tenant A Analyst",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
        API_KEY_TENANT_B_ADMIN: {
            "tenant_id": TENANT_B_ID,
            "actor_id": "actor-b-admin",
            "name": "Tenant B Admin",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN"],
        },
        API_KEY_TENANT_A_AUDITOR: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-a-auditor",
            "name": "Tenant A Auditor",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["AUDITOR"],
        },
    }


@pytest.fixture
def temp_audit_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for audit log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audit_log_path(temp_audit_dir: Path) -> Path:
    """Return path for the audit log file."""
    return temp_audit_dir / "audit_events.jsonl"


@pytest.fixture
def client_with_audit_sink(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    audit_log_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a test client with JSONL audit sink configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    monkeypatch.setenv(AUDIT_LOG_PATH_ENV, str(audit_log_path))

    sink = JsonlFileAuditSink(str(audit_log_path))
    app = create_app(audit_sink=sink)
    return TestClient(app)


def _write_synthetic_audit_event(
    file_path: Path,
    tenant_id: str,
    event_id: str,
    event_type: str,
    occurred_at: str,
    deal_id: str | None = None,
) -> None:
    """Write a synthetic audit event to JSONL file."""
    event = {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "request": {
            "request_id": f"req-{event_id[:8]}",
            "method": "POST",
            "path": "/v1/deals",
        },
    }
    if deal_id:
        event["request"]["deal_id"] = deal_id

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


class TestAuditEventsAPIBasic:
    """Basic functionality tests for GET /v1/audit/events."""

    def test_list_audit_events_returns_200_with_items(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """GET /v1/audit/events returns 200 with items list."""
        event_id = str(uuid.uuid4())
        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_A_ID,
            event_id,
            "deal.created",
            datetime.now(UTC).isoformat(),
        )

        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        body = response.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) >= 1

        found = any(item["event_id"] == event_id for item in body["items"])
        assert found, f"Expected event {event_id} not found in response"

    def test_list_audit_events_returns_empty_when_no_events(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """GET /v1/audit/events returns empty items when no events exist."""
        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["next_cursor"] is None


class TestAuditEventsTenantIsolation:
    """Tenant isolation tests for GET /v1/audit/events."""

    def test_cross_tenant_query_returns_empty(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """Events from tenant A are not visible to tenant B (no leakage)."""
        event_id_a = str(uuid.uuid4())
        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_A_ID,
            event_id_a,
            "deal.created",
            datetime.now(UTC).isoformat(),
        )

        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B_ADMIN},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == [], "Tenant B should see no events from tenant A"

    def test_tenant_sees_only_own_events(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """Each tenant sees only their own audit events."""
        event_id_a = str(uuid.uuid4())
        event_id_b = str(uuid.uuid4())
        now = datetime.now(UTC)

        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_A_ID,
            event_id_a,
            "deal.created",
            now.isoformat(),
        )
        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_B_ID,
            event_id_b,
            "claim.created",
            (now + timedelta(seconds=1)).isoformat(),
        )

        response_a = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )
        assert response_a.status_code == 200
        items_a = response_a.json()["items"]
        ids_a = {item["event_id"] for item in items_a}
        assert event_id_a in ids_a
        assert event_id_b not in ids_a

        response_b = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B_ADMIN},
        )
        assert response_b.status_code == 200
        items_b = response_b.json()["items"]
        ids_b = {item["event_id"] for item in items_b}
        assert event_id_b in ids_b
        assert event_id_a not in ids_b


class TestAuditEventsPagination:
    """Pagination tests for GET /v1/audit/events."""

    def test_pagination_is_stable_and_deterministic(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """Calling twice yields identical first page ordering."""
        now = datetime.now(UTC)
        event_ids = []
        for i in range(5):
            event_id = str(uuid.uuid4())
            event_ids.append(event_id)
            _write_synthetic_audit_event(
                audit_log_path,
                TENANT_A_ID,
                event_id,
                f"event.type.{i}",
                (now + timedelta(seconds=i)).isoformat(),
            )

        response1 = client_with_audit_sink.get(
            "/v1/audit/events?limit=3",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )
        response2 = client_with_audit_sink.get(
            "/v1/audit/events?limit=3",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response1.status_code == 200
        assert response2.status_code == 200

        items1 = response1.json()["items"]
        items2 = response2.json()["items"]

        assert len(items1) == len(items2) == 3
        for i in range(3):
            assert items1[i]["event_id"] == items2[i]["event_id"]

    def test_cursor_advances_deterministically(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """Cursor-based pagination advances through all events."""
        now = datetime.now(UTC)
        all_event_ids = []
        for i in range(6):
            event_id = str(uuid.uuid4())
            all_event_ids.append(event_id)
            _write_synthetic_audit_event(
                audit_log_path,
                TENANT_A_ID,
                event_id,
                f"event.type.{i}",
                (now + timedelta(seconds=i)).isoformat(),
            )

        collected_ids: list[str] = []
        cursor = None

        for _ in range(10):
            url = "/v1/audit/events?limit=2"
            if cursor:
                url += f"&cursor={cursor}"

            response = client_with_audit_sink.get(
                url,
                headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
            )
            assert response.status_code == 200
            body = response.json()

            for item in body["items"]:
                collected_ids.append(item["event_id"])

            cursor = body.get("next_cursor")
            if cursor is None:
                break

        assert set(collected_ids) == set(all_event_ids)

    def test_invalid_cursor_returns_400(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """Invalid cursor returns 400 error (fail-closed)."""
        response = client_with_audit_sink.get(
            "/v1/audit/events?cursor=not-a-valid-cursor",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_CURSOR"

    def test_limit_out_of_range_returns_400(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """Limit > 200 returns 400 error (fail-closed)."""
        response = client_with_audit_sink.get(
            "/v1/audit/events?limit=500",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_LIMIT"
        assert "request_id" in body

    def test_limit_zero_returns_400(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """Limit = 0 returns 400 error (fail-closed)."""
        response = client_with_audit_sink.get(
            "/v1/audit/events?limit=0",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_LIMIT"
        assert "request_id" in body


class TestAuditEventsRBAC:
    """RBAC enforcement tests for GET /v1/audit/events."""

    def test_admin_can_access_audit_events(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """ADMIN role can access audit events."""
        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )
        assert response.status_code == 200

    def test_auditor_can_access_audit_events(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """AUDITOR role can access audit events."""
        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_AUDITOR},
        )
        assert response.status_code == 200

    def test_analyst_cannot_access_audit_events(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """ANALYST role cannot access audit events (RBAC denied)."""
        response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ANALYST},
        )
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "RBAC_DENIED"


class TestAuditEventsFilters:
    """Filter parameter tests for GET /v1/audit/events."""

    def test_filter_by_event_type(
        self,
        client_with_audit_sink: TestClient,
        audit_log_path: Path,
    ) -> None:
        """eventType filter returns only matching events."""
        now = datetime.now(UTC)
        event_id_deal = str(uuid.uuid4())
        event_id_claim = str(uuid.uuid4())

        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_A_ID,
            event_id_deal,
            "deal.created",
            now.isoformat(),
        )
        _write_synthetic_audit_event(
            audit_log_path,
            TENANT_A_ID,
            event_id_claim,
            "claim.created",
            (now + timedelta(seconds=1)).isoformat(),
        )

        response = client_with_audit_sink.get(
            "/v1/audit/events?eventType=deal.created",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["event_id"] == event_id_deal
        assert items[0]["event_type"] == "deal.created"


class TestAuditEventsIntegration:
    """Integration tests that generate events via real API mutations."""

    def test_api_mutations_generate_audit_events(
        self,
        client_with_audit_sink: TestClient,
    ) -> None:
        """Creating a deal generates an audit event retrievable via API."""
        create_response = client_with_audit_sink.post(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
            json={
                "name": "Audit Test Deal",
                "company_name": "Audit Test Corp",
            },
        )

        assert create_response.status_code == 201, f"Deal creation failed: {create_response.text}"

        list_response = client_with_audit_sink.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A_ADMIN},
        )

        assert list_response.status_code == 200
        items = list_response.json()["items"]

        deal_events = [item for item in items if "deal" in item.get("event_type", "").lower()]
        assert len(deal_events) >= 1, "Expected at least one deal-related audit event"
