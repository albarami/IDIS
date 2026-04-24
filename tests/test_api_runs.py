"""Tests for Runs API endpoints.

Tests POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.
Covers: happy path, tenant isolation, idempotency, audit correlation, fail-closed validation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import InMemoryAuditSink
from tests._postgres_support import (
    admin_engine_generator,
    migrated_db_generator,
    postgres_configured,
    truncate_all,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-api-key-tenant-a"
API_KEY_TENANT_B = "test-api-key-tenant-b"


@pytest.fixture(scope="module")
def _pg_admin_engine() -> Generator[Engine, None, None]:
    yield from admin_engine_generator()


@pytest.fixture(scope="module")
def _pg_migrated(_pg_admin_engine: Engine) -> Generator[None, None, None]:
    yield from migrated_db_generator(_pg_admin_engine)


@pytest.fixture(autouse=True)
def _pg_clean_state(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """TRUNCATE all relevant tables between tests when Postgres is enabled.

    Also requests the module-scope migrations fixture lazily so it only
    runs in Postgres mode.
    """
    if postgres_configured():
        admin_engine = request.getfixturevalue("_pg_admin_engine")
        request.getfixturevalue("_pg_migrated")
        truncate_all(admin_engine)
        yield
        truncate_all(admin_engine)
    else:
        yield


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


def _seed_snapshot_docs_pg(
    *, tenant_id: str, deal_id: str, doc_count: int = 1
) -> None:
    """Seed durable document_artifacts + documents + document_spans rows so
    runs.py `_gather_snapshot_documents` reads through the Postgres
    repositories. Called only in Postgres mode.
    """
    from idis.persistence.db import get_app_engine, set_tenant_local
    from idis.persistence.repositories.documents import (
        DocumentArtifactsRepository,
        DocumentSpansRepository,
        DocumentsRepository,
    )

    with get_app_engine().begin() as conn:
        set_tenant_local(conn, tenant_id)
        arts = DocumentArtifactsRepository(conn, tenant_id)
        docs = DocumentsRepository(conn, tenant_id)
        spans = DocumentSpansRepository(conn, tenant_id)
        for _ in range(doc_count):
            art_id = str(uuid.uuid4())
            document_id = str(uuid.uuid4())
            arts.create(
                doc_id=art_id,
                deal_id=deal_id,
                doc_type="DATA_ROOM_FILE",
                title="snapshot-seed",
                source_system="test",
                version_id="v1",
            )
            docs.create(
                document_id=document_id,
                deal_id=deal_id,
                doc_id=art_id,
                doc_type="PDF",
                parse_status="PARSED",
            )
            spans.create_many(
                [
                    {
                        "span_id": str(uuid.uuid4()),
                        "document_id": document_id,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1, "line": 1},
                        "text_excerpt": "Revenue was $5M in 2024.",
                    }
                ]
            )


@pytest.fixture
def deal_id(client: TestClient) -> str:
    """Create a deal and seed a minimal document so SNAPSHOT runs pass.

    In Postgres mode, seed real document_artifacts + documents +
    document_spans rows so `_gather_snapshot_documents` reads through
    the durable repositories — the main path.

    In no-DB mode, fall back to the `app.state.deal_documents` override
    (that code path in runs.py exists precisely so the service can run
    without Postgres in dev/test).
    """
    response = client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
    )
    assert response.status_code == 201
    did = response.json()["deal_id"]

    if postgres_configured():
        _seed_snapshot_docs_pg(tenant_id=TENANT_A_ID, deal_id=did)
        return did

    client.app.state.deal_documents[did] = [
        {
            "document_id": str(uuid.uuid4()),
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": str(uuid.uuid4()),
                    "text_excerpt": "Revenue was $5M in 2024.",
                    "locator": {"page": 1, "line": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]
    return did


@pytest.mark.skipif(
    not postgres_configured(),
    reason="Durable SNAPSHOT gather proof is Postgres-only",
)
class TestSnapshotGatherDurablePath:
    """Task 7 completion: under Postgres, POST /runs must drive SNAPSHOT
    extraction from real document_artifacts / documents / document_spans
    rows, not from an app.state.deal_documents override.
    """

    def test_snapshot_gather_requires_durable_rows(
        self, client: TestClient
    ) -> None:
        """Seed a deal + real docs/spans; assert the run succeeds. Then
        prove negative: a different deal with *no* durable rows (and no
        deal_documents override) must return NO_INGESTED_DOCUMENTS —
        confirming the main path reads from Postgres, not a lingering
        override.
        """
        # Positive: deal with durable rows -> run succeeds.
        create = client.post(
            "/v1/deals",
            json={"name": "Snapshot Durable", "company_name": "Durable Co"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        did_with_docs = create.json()["deal_id"]
        _seed_snapshot_docs_pg(tenant_id=TENANT_A_ID, deal_id=did_with_docs)

        ok = client.post(
            f"/v1/deals/{did_with_docs}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert ok.status_code == 202, ok.text

        # Negative: deal without durable rows and without any
        # deal_documents override must be rejected. If the handler were
        # leaking through a stale override we'd get 202 here.
        create2 = client.post(
            "/v1/deals",
            json={"name": "Snapshot Empty", "company_name": "Empty Co"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        did_empty = create2.json()["deal_id"]

        empty = client.post(
            f"/v1/deals/{did_empty}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert empty.status_code == 400, empty.text
        assert empty.json()["code"] == "NO_INGESTED_DOCUMENTS"


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
        # Sprint 2 Task 11: API is enqueue-only. Clients see QUEUED here;
        # the worker advances to SUCCEEDED/FAILED asynchronously.
        assert body["status"] == "QUEUED"
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
        # Task 11: GET immediately after POST reflects the durable QUEUED
        # state. Advancing the run is the worker's responsibility; this
        # test only verifies the read-after-enqueue contract.
        assert body["status"] == "QUEUED"
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
        """POST /v1/deals/{dealId}/runs emits audit event with correct resource_id.

        In Postgres mode the audit middleware writes via PostgresAuditSink
        (in-transaction), so the in-memory `audit_sink` never sees the
        event. Query the durable `audit_events` table instead — that is
        the main path in production.
        """
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

        if postgres_configured():
            from sqlalchemy import text as _text

            from idis.persistence.db import get_admin_engine

            with get_admin_engine().begin() as conn:
                row = conn.execute(
                    _text(
                        """
                        SELECT event_id, event_type, request_id, event
                        FROM audit_events
                        WHERE event_type = 'deal.run.started'
                          AND event->'resource'->>'resource_id' = :rid
                        ORDER BY occurred_at DESC
                        LIMIT 1
                        """
                    ),
                    {"rid": run_id},
                ).fetchone()
            assert row is not None, "deal.run.started audit row must be persisted"
            assert row.event["resource"]["resource_id"] == run_id
            assert row.event["request"]["request_id"] == request_id
            return

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


# TestAuditFailureOnRunCompletedReturns500 was removed in Sprint 2 Task 11.
# The API no longer emits deal.run.completed inline — that audit event
# is now the worker's responsibility. A worker-side equivalent belongs
# in tests/test_worker_orchestrator_postgres.py (future work). The
# middleware's deal.run.started audit still fires on POST /runs and is
# covered by TestRunsAPIAuditCorrelation.
