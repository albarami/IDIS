"""Tests for Runs API endpoints.

Tests POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.
Covers: happy path, tenant isolation, idempotency, audit correlation, fail-closed validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import idis.api.routes.runs as runs_route
from idis.api import abac
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.persistence.db import set_tenant_local
from idis.services.runs.execution import RunExecutionResult
from tests._cir04c3_required_frozen_preview import (
    CapturingRequiredPostgresAuditSink,
    RequiredFrozenPreview,
    required_frozen_preview,
)
from tests.abac_seed import seed_deal_access

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-api-key-tenant-a"
API_KEY_TENANT_B = "test-api-key-tenant-b"


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """API keys configuration for testing."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": str(uuid.uuid4()),
            "actor_id": "actor-a",
            "name": "Tenant A Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": str(uuid.uuid4()),
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
) -> Iterator[TestClient]:
    """Create test client with in-memory stores."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.setattr(abac, "_default_store", abac.InMemoryDealAssignmentStore())
    clear_deals_store()
    clear_runs_store()
    app = create_app(audit_sink=audit_sink, service_region="us-east-1")
    app.state.deal_documents = {}
    app.state.fixture_tenant_a = api_keys_config[API_KEY_TENANT_A]["tenant_id"]
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()


@pytest.fixture
def preview_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[RequiredFrozenPreview]:
    with required_frozen_preview(monkeypatch) as target:
        yield target


@pytest.fixture
def frozen_client(
    preview_database: RequiredFrozenPreview,
    api_keys_config: dict[str, dict[str, str | list[str]]],
    audit_sink: InMemoryAuditSink,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[TestClient]:
    for config in api_keys_config.values():
        preview_database.add_tenant(str(config["tenant_id"]), str(config["data_region"]))
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    app = create_app(
        audit_sink=audit_sink,
        service_region="us-east-1",
        postgres_audit_sink=CapturingRequiredPostgresAuditSink(audit_sink),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
    )
    app.state.fixture_tenant_a = api_keys_config[API_KEY_TENANT_A]["tenant_id"]
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()


@pytest.fixture
def frozen_deal_id(
    frozen_client: TestClient,
    preview_database: RequiredFrozenPreview,
    tmp_path: Path,
) -> str:
    response = frozen_client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
    )
    assert response.status_code == 201
    deal = response.json()["deal_id"]
    tenant = frozen_client.app.state.fixture_tenant_a
    seed_deal_access(tenant, deal, "actor-a")
    frozen_client.app.state.fixture_package_id = preview_database.prepare_pdf(
        tenant,
        deal,
        tmp_path / "source-objects",
    )
    return deal


@pytest.fixture
def deal_id(client: TestClient) -> str:
    """Create an empty memory deal for validation that precedes consumption."""
    response = client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
    )
    assert response.status_code == 201
    did = response.json()["deal_id"]
    seed_deal_access(client.app.state.fixture_tenant_a, did, "actor-a")
    return did


class TestRunsAPIHappyPath:
    """Test happy path scenarios for Runs API."""

    def test_start_run_returns_202_with_run_ref(
        self, frozen_client: TestClient, frozen_deal_id: str
    ) -> None:
        """POST /v1/deals/{dealId}/runs returns 202 with RunRef."""
        client, deal_id = frozen_client, frozen_deal_id
        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        assert body["status"] == "SUCCEEDED"
        uuid.UUID(body["run_id"])

    def test_start_run_uses_canonical_execution_service(
        self,
        frozen_client: TestClient,
        frozen_deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API starts must execute through RunExecutionService."""
        client, deal_id = frozen_client, frozen_deal_id
        calls: list[str] = []

        class RecordingExecutionService:
            def __init__(self, **kwargs: object) -> None:
                calls.append("init")

            def execute(self, ctx: object) -> RunExecutionResult:
                calls.append("execute")
                return RunExecutionResult(claimed=True, status="SUCCEEDED")

        monkeypatch.setattr(
            runs_route,
            "RunExecutionService",
            RecordingExecutionService,
            raising=False,
        )

        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 202
        assert calls == ["init", "execute"]

    def test_get_run_returns_run_status(
        self, frozen_client: TestClient, frozen_deal_id: str
    ) -> None:
        """GET /v1/runs/{runId} returns RunStatus."""
        client, deal_id = frozen_client, frozen_deal_id
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
        assert body["status"] == "SUCCEEDED"
        assert "started_at" in body


class TestRunsAPITenantIsolation:
    """Test tenant isolation for Runs API."""

    def test_cross_tenant_get_run_returns_404(
        self, frozen_client: TestClient, frozen_deal_id: str
    ) -> None:
        """GET /v1/runs/{runId} returns 404 for cross-tenant access."""
        client, deal_id = frozen_client, frozen_deal_id
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
        self, frozen_client: TestClient, frozen_deal_id: str, audit_sink: InMemoryAuditSink
    ) -> None:
        """POST /v1/deals/{dealId}/runs emits audit event with correct resource_id."""
        client, deal_id = frozen_client, frozen_deal_id
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
        self, frozen_client: TestClient, frozen_deal_id: str
    ) -> None:
        """Same Idempotency-Key + same payload returns identical result."""
        client, deal_id = frozen_client, frozen_deal_id
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
        self, frozen_client: TestClient, frozen_deal_id: str
    ) -> None:
        """Same Idempotency-Key + different payload returns 409."""
        client, deal_id = frozen_client, frozen_deal_id
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
        seed_deal_access(client.app.state.fixture_tenant_a, empty_deal_id, "actor-a")

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
        seed_deal_access(client.app.state.fixture_tenant_a, empty_deal_id, "actor-a")

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
        preview_database: RequiredFrozenPreview,
        tmp_path: Path,
        request: pytest.FixtureRequest,
    ) -> None:
        """When audit sink raises on deal.run.completed, endpoint returns 500."""
        tenant = str(api_keys_config[API_KEY_TENANT_A]["tenant_id"])
        preview_database.add_tenant(tenant, "us-east-1")
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
        request.addfinalizer(client.close)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Audit Fail Deal", "company_name": "AuditCo"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        seed_deal_access(tenant, deal_id, "actor-a")
        package = preview_database.prepare_pdf(tenant, deal_id, tmp_path / "source-objects")
        source_before = preview_database.package_rows(tenant, deal_id, package)
        from idis.persistence.repositories.webhook_outbox import PostgresWebhookOutboxRepository
        from tests.test_slice97_webhook_outbox_postgres import _create_subscription

        _create_subscription(tenant, ["run.claimed", "run.completed"])
        original_enqueue = PostgresWebhookOutboxRepository.enqueue
        enqueued_claims: list[dict[str, int]] = []

        def observe_enqueue(repo: PostgresWebhookOutboxRepository, **kwargs: Any) -> bool:
            inserted = original_enqueue(repo, **kwargs)
            if kwargs["event_type"] == "run.claimed":
                connection = kwargs["conn"]
                assert inserted and connection is not None
                from sqlalchemy import text

                enqueued_claims.append(
                    {
                        table: connection.execute(
                            text(f"SELECT count(*) FROM {table}")
                        ).scalar_one()
                        for table in ("runs", "webhook_delivery_attempts")
                    }
                )
            return inserted

        monkeypatch.setattr(PostgresWebhookOutboxRepository, "enqueue", observe_enqueue)

        response = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "AUDIT_FAILURE"
        assert call_count > 0
        assert enqueued_claims == [{"runs": 1, "webhook_delivery_attempts": 1}]
        assert any(str(event["event_type"]).startswith("deal.run.step.") for event in sink.events)
        assert preview_database.package_rows(tenant, deal_id, package) == source_before
        from sqlalchemy import text

        with preview_database.engine.begin() as conn:
            set_tenant_local(conn, tenant)
            for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts"):
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_required_preview_database_is_mandatory_without_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests._cir04c3_required_frozen_preview import (
        URL_ENV,
        RequiredFrozenPreviewDatabaseError,
        qualify_required_database,
    )

    monkeypatch.delenv(URL_ENV, raising=False)
    with pytest.raises(
        RequiredFrozenPreviewDatabaseError, match="^REQUIRED_FROZEN_PREVIEW_DATABASE_UNQUALIFIED$"
    ):
        qualify_required_database(os.environ)


@pytest.mark.parametrize(
    "mismatch",
    [
        "missing-receipt",
        "hash",
        "owner",
        "absence",
        "database",
        "role",
        "revision",
        "default-reuse",
    ],
)
def test_required_preview_database_rejects_unowned_receipt_before_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    import tests._cir04c3_required_frozen_preview as helper

    receipt = {
        "schema_version": helper.RECEIPT_VERSION,
        "owner": "CIR-04 required-caller disposable fixture",
        "absent_before_create": True,
        "service_container_id": "synthetic-unit-receipt-not-a-service",
        "host": "127.0.0.1",
        "port": 64838,
        "database": "idis_c3_callers_synthetic",
        "database_oid": 1,
        "app_role": "fixture",
        "revision": "0038",
        "role_flags": {"rolsuper": False, "rolbypassrls": False},
    }
    if mismatch == "owner":
        receipt["owner"] = "not-owned"
    elif mismatch == "absence":
        receipt["absent_before_create"] = False
    elif mismatch == "database":
        receipt["database"] = "ordinary_user_database"
    elif mismatch == "role":
        receipt["role_flags"] = {"rolsuper": False, "rolbypassrls": True}
    elif mismatch == "revision":
        receipt["revision"] = "0037"
    raw = json.dumps(receipt).encode()
    path = tmp_path / "synthetic-qualification-unit.json"
    if mismatch != "missing-receipt":
        path.write_bytes(raw)
    url = "postgresql+psycopg2://fixture@127.0.0.1:64838/idis_c3_callers_synthetic"
    env = {
        helper.URL_ENV: url,
        helper.RECEIPT_ENV: str(path),
        helper.RECEIPT_HASH_ENV: "0" * 64
        if mismatch == "hash"
        else hashlib.sha256(raw).hexdigest(),
    }
    if mismatch == "default-reuse":
        env["IDIS_DATABASE_URL"] = url
    opened = []

    def unexpected_connection(*args: object, **kwargs: object) -> None:
        opened.append(True)
        pytest.fail("Unowned receipt reached a database connection")

    monkeypatch.setattr(helper, "create_engine", unexpected_connection)
    with pytest.raises(
        helper.RequiredFrozenPreviewDatabaseError,
        match="^REQUIRED_FROZEN_PREVIEW_DATABASE_UNQUALIFIED$",
    ):
        helper.qualify_required_database(env)
    assert opened == []


def test_required_preview_database_restores_environment_engines_and_assignment_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api import abac
    from idis.persistence import db
    from idis.persistence.repositories import webhook_outbox
    from idis.persistence.repositories.webhook_outbox import (
        InMemoryWebhookOutboxRepository,
        PostgresWebhookOutboxRepository,
    )

    names = (
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_REQUIRE_POSTGRES",
    )
    before_env = {name: os.environ.get(name) for name in names}
    before_engines = (db._app_engine, db._admin_engine, db._erasure_engine)
    before_store = abac._default_store
    stale_outbox = InMemoryWebhookOutboxRepository()
    monkeypatch.setattr(webhook_outbox, "_DEFAULT_OUTBOX", stale_outbox)
    with required_frozen_preview(monkeypatch) as target:
        assert db.get_app_engine() is target.engine
        assert db.is_postgres_configured()
        assert abac._default_store is None
        assert abac.get_deal_assignment_store().__class__ is abac.PostgresDealAssignmentStore
        assert isinstance(webhook_outbox.default_webhook_outbox(), PostgresWebhookOutboxRepository)
    assert {name: os.environ.get(name) for name in names} == before_env
    assert (db._app_engine, db._admin_engine, db._erasure_engine) == before_engines
    assert abac._default_store is before_store
    assert webhook_outbox._DEFAULT_OUTBOX is stale_outbox


def test_required_preview_drops_stale_webhook_outbox_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope the cached outbox through the real helper without qualifying a database."""
    import tests._cir04c3_required_frozen_preview as helper
    from idis.persistence.repositories import webhook_outbox
    from idis.persistence.repositories.webhook_outbox import (
        InMemoryWebhookOutboxRepository,
        PostgresWebhookOutboxRepository,
    )

    class _DisposableEngine:
        def dispose(self) -> None:
            return None

    monkeypatch.setattr(helper, "qualify_required_database", lambda _env: _DisposableEngine())
    monkeypatch.setenv(
        helper.URL_ENV,
        "postgresql+psycopg2://fixture@127.0.0.1:9/idis_scope_unit",
    )
    stale_outbox = InMemoryWebhookOutboxRepository()
    monkeypatch.setattr(webhook_outbox, "_DEFAULT_OUTBOX", stale_outbox)
    with helper.required_frozen_preview(monkeypatch):
        assert isinstance(webhook_outbox.default_webhook_outbox(), PostgresWebhookOutboxRepository)
    assert webhook_outbox._DEFAULT_OUTBOX is stale_outbox


@pytest.mark.parametrize(
    "connection",
    [
        "not-a-url",
        "postgresql+cir04c3_missing_driver://fixture@127.0.0.1:64838/idis_c3_callers_synthetic",
    ],
)
def test_required_preview_database_sanitizes_malformed_or_unsupported_driver_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    connection: str,
) -> None:
    from sqlalchemy.pool import Pool

    import tests._cir04c3_required_frozen_preview as helper

    receipt = {
        "schema_version": helper.RECEIPT_VERSION,
        "owner": "CIR-04 required-caller disposable fixture",
        "absent_before_create": True,
        "service_container_id": "synthetic-unit-receipt-not-a-service",
        "host": "127.0.0.1",
        "port": 64838,
        "database": "idis_c3_callers_synthetic",
        "database_oid": 1,
        "app_role": "fixture",
        "revision": "0038",
        "role_flags": {"rolsuper": False, "rolbypassrls": False},
    }
    raw = json.dumps(receipt).encode()
    path = tmp_path / "synthetic-url-qualification-unit.json"
    path.write_bytes(raw)
    env = {
        helper.URL_ENV: connection,
        helper.RECEIPT_ENV: str(path),
        helper.RECEIPT_HASH_ENV: hashlib.sha256(raw).hexdigest(),
    }
    pool_connections = []

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        pool_connections.append(True)
        pytest.fail("Invalid fixture configuration reached a DBAPI connection")

    monkeypatch.setattr(Pool, "connect", unexpected_connect)
    with pytest.raises(
        helper.RequiredFrozenPreviewDatabaseError,
        match="^REQUIRED_FROZEN_PREVIEW_DATABASE_UNQUALIFIED$",
    ) as raised:
        helper.qualify_required_database(env)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert pool_connections == []
