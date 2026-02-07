"""Tests for Runs API endpoints.

Tests POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.
Covers: happy path, tenant isolation, idempotency, audit correlation, fail-closed validation.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import AuditSinkError, InMemoryAuditSink

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-api-key-tenant-a"
API_KEY_TENANT_B = "test-api-key-tenant-b"


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """API keys configuration for testing."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-a",
            "name": "Tenant A Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": "actor-b",
            "name": "Tenant B Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
    }


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide in-memory audit sink for test verification."""
    return InMemoryAuditSink()


@pytest.fixture
def client(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    audit_sink: InMemoryAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with in-memory stores."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    clear_deals_store()
    clear_runs_store()
    app = create_app(audit_sink=audit_sink, service_region="us-east-1")
    app.state.deal_documents = {}
    return TestClient(app)


@pytest.fixture
def deal_id(client: TestClient) -> str:
    """Create a deal and seed a minimal document so SNAPSHOT runs pass."""
    response = client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
    )
    assert response.status_code == 201
    did = response.json()["deal_id"]
    client.app.state.deal_documents[did] = [
        {
            "document_id": "doc-test-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": "span-test-001",
                    "text_excerpt": "Revenue was $5M in 2024.",
                    "locator": {"page": 1, "line": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]
    return did


class TestRunsAPIHappyPath:
    """Test happy path scenarios for Runs API."""

    def test_start_run_returns_202_with_run_ref(self, client: TestClient, deal_id: str) -> None:
        """POST /v1/deals/{dealId}/runs returns 202 with RunRef."""
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        assert body["status"] == "COMPLETED"
        uuid.UUID(body["run_id"])

    def test_get_run_returns_run_status(self, client: TestClient, deal_id: str) -> None:
        """GET /v1/runs/{runId} returns RunStatus."""
        create_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        run_id = create_resp.json()["run_id"]

        response = client.get(
            f"/v1/runs/{run_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["status"] == "COMPLETED"
        assert "started_at" in body


class TestRunsAPITenantIsolation:
    """Test tenant isolation for Runs API."""

    def test_cross_tenant_get_run_returns_404(self, client: TestClient, deal_id: str) -> None:
        """GET /v1/runs/{runId} returns 404 for cross-tenant access."""
        create_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        run_id = create_resp.json()["run_id"]

        response = client.get(
            f"/v1/runs/{run_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 404


class TestRunsAPIValidation:
    """Test validation scenarios for Runs API."""

    def test_invalid_mode_returns_422(self, client: TestClient, deal_id: str) -> None:
        """POST with invalid mode returns 422 (schema mismatch)."""
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "INVALID"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"

    def test_missing_mode_returns_400(self, client: TestClient, deal_id: str) -> None:
        """POST with missing mode field returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"
        assert "request_id" in body

    def test_nonexistent_run_returns_404(self, client: TestClient) -> None:
        """GET /v1/runs/{runId} returns 404 for nonexistent run."""
        fake_run_id = str(uuid.uuid4())
        response = client.get(
            f"/v1/runs/{fake_run_id}",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 404


class TestRunsAPIAuditCorrelation:
    """Test audit event correlation for Runs API."""

    def test_start_run_emits_audit_event(
        self, client: TestClient, deal_id: str, audit_sink: InMemoryAuditSink
    ) -> None:
        """POST /v1/deals/{dealId}/runs emits audit event with correct resource_id."""
        request_id = str(uuid.uuid4())
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "X-Request-ID": request_id,
            },
        )

        assert response.status_code == 202
        run_id = response.json()["run_id"]

        events = audit_sink.events
        assert len(events) >= 1

        run_event = next(
            (e for e in events if e.get("event_type") == "deal.run.started"),
            None,
        )
        assert run_event is not None
        assert run_event["resource"]["resource_id"] == run_id
        assert run_event["request"]["request_id"] == request_id


class TestRunsAPIIdempotency:
    """Test idempotency for Runs API."""

    def test_same_idempotency_key_same_payload_returns_same_result(
        self, client: TestClient, deal_id: str
    ) -> None:
        """Same Idempotency-Key + same payload returns identical result."""
        idem_key = str(uuid.uuid4())
        payload = {"mode": "SNAPSHOT"}

        resp1 = client.post(
            f"/v1/deals/{deal_id}/runs",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/runs",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 202
        assert resp2.status_code == 202
        assert resp1.json()["run_id"] == resp2.json()["run_id"]

    def test_same_idempotency_key_different_payload_returns_409(
        self, client: TestClient, deal_id: str
    ) -> None:
        """Same Idempotency-Key + different payload returns 409."""
        idem_key = str(uuid.uuid4())

        resp1 = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "FULL"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 202
        assert resp2.status_code == 409


class TestNoIngestedDocumentsReturns400:
    """Regression: deal with zero ingested docs must return 400 before orchestration."""

    def test_no_ingested_docs_returns_400(self, client: TestClient) -> None:
        """POST /v1/deals/{dealId}/runs with no docs returns 400 NO_INGESTED_DOCUMENTS."""
        create_resp = client.post(
            "/v1/deals",
            json={"name": "Empty Deal", "company_name": "NoDocs Inc"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert create_resp.status_code == 201
        empty_deal_id = create_resp.json()["deal_id"]

        response = client.post(
            f"/v1/deals/{empty_deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "NO_INGESTED_DOCUMENTS"

    def test_no_ingested_docs_does_not_create_run(self, client: TestClient) -> None:
        """No run record should exist after NO_INGESTED_DOCUMENTS rejection."""
        create_resp = client.post(
            "/v1/deals",
            json={"name": "Empty Deal 2", "company_name": "NoDocs LLC"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert create_resp.status_code == 201
        empty_deal_id = create_resp.json()["deal_id"]

        response = client.post(
            f"/v1/deals/{empty_deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response.status_code == 400

        from idis.persistence.repositories.runs import _in_memory_runs_store

        for run_data in _in_memory_runs_store.values():
            assert run_data["deal_id"] != empty_deal_id, (
                "Run record must not be created for deal with no ingested documents"
            )


class TestAuditFailureOnRunCompletedReturns500:
    """Regression: AuditSinkError on deal.run.completed must return 500 AUDIT_FAILURE."""

    def test_audit_sink_failure_on_run_completed_returns_500(
        self,
        api_keys_config: dict[str, dict[str, str | list[str]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When audit sink raises on deal.run.completed, endpoint returns 500."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
        clear_deals_store()
        clear_runs_store()

        call_count = 0

        class FailOnRunCompletedSink:
            """Audit sink that fails only on deal.run.completed events."""

            def __init__(self) -> None:
                self.events: list[dict] = []

            def emit(self, event: dict) -> None:
                nonlocal call_count
                call_count += 1
                if event.get("event_type") == "deal.run.completed":
                    raise AuditSinkError("Disk full on run completed")
                self.events.append(event)

        sink = FailOnRunCompletedSink()
        app = create_app(audit_sink=sink, service_region="us-east-1")
        app.state.deal_documents = {}
        client = TestClient(app, raise_server_exceptions=False)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Audit Fail Deal", "company_name": "AuditCo"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]

        app.state.deal_documents[deal_id] = [
            {
                "document_id": "doc-audit-001",
                "doc_type": "PDF",
                "document_name": "audit_test.pdf",
                "spans": [
                    {
                        "span_id": "span-audit-001",
                        "text_excerpt": "Revenue $10M.",
                        "locator": {"page": 1},
                        "span_type": "PAGE_TEXT",
                    }
                ],
            }
        ]

        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "AUDIT_FAILURE"
