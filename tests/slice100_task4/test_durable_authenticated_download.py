from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.policy import POLICY_RULES
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation import real_example_gate
from idis.idempotency.store import SqliteIdempotencyStore
from idis.rate_limit.limiter import InMemoryRateLimitStore, RateLimitConfig, TenantRateLimiter
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
DEAL_A = "33333333-3333-3333-3333-333333333333"
ARTIFACT_ID = "44444444-4444-4444-4444-444444444444"
API_KEY_A = "task4-durable-download-a"
API_KEY_B = "task4-durable-download-b"
STORAGE_KEY = "deals/safe/artifact"
DURABLE_BYTES = b"durable owner authenticated download bytes"
CONTENT_SHA256 = hashlib.sha256(DURABLE_BYTES).hexdigest()
INGESTED_AT = datetime(2026, 7, 16, tzinfo=UTC)


class _RowsResult:
    def __init__(self, rows: list[MagicMock]) -> None:
        self._rows = rows

    def fetchone(self) -> MagicMock | None:
        return self._rows[0] if self._rows else None


class _DurableDownloadConnection:
    """Small RLS-aware SQL boundary containing one durable artifact row."""

    def __init__(
        self,
        *,
        expected_sha256: str = CONTENT_SHA256,
        expected_size_bytes: int = len(DURABLE_BYTES),
    ) -> None:
        self.tenant_id: str | None = None
        self.sql: list[str] = []
        self.artifact_row = MagicMock(
            _mapping={
                "doc_id": ARTIFACT_ID,
                "tenant_id": TENANT_A,
                "deal_id": DEAL_A,
                "doc_type": "DATA_ROOM_FILE",
                "title": "company-001-document-00001.txt",
                "source_system": "api-upload",
                "version_id": expected_sha256[:12],
                "ingested_at": INGESTED_AT,
                "sha256": expected_sha256,
                "uri": STORAGE_KEY,
                "metadata": {
                    "size_bytes": expected_size_bytes,
                    "upload_filename": "company-001-document-00001.txt",
                    "streaming_contract_version": "bounded-streaming-v1",
                },
            }
        )

    def execute(self, statement: object, params: Mapping[str, Any] | None = None) -> _RowsResult:
        sql = str(statement)
        self.sql.append(sql)
        if "SET LOCAL idis.tenant_id" in sql:
            matched = re.search(r"idis\.tenant_id = '([0-9a-f-]+)'", sql)
            assert matched is not None
            self.tenant_id = matched.group(1)
            return _RowsResult([])
        if "FROM document_artifacts" in sql:
            query_params = params or {}
            visible = (
                self.tenant_id == TENANT_A
                and query_params.get("doc_id") == ARTIFACT_ID
                and query_params.get("tenant_id", TENANT_A) == TENANT_A
            )
            return _RowsResult([self.artifact_row] if visible else [])
        raise AssertionError(f"unexpected SQL: {sql}")


class _RestartedIngestionService:
    """Only the persistent compliant store survives process reconstruction."""

    def __init__(self, compliant_store: ComplianceEnforcedStore) -> None:
        self._compliant_store = compliant_store


def _configure_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY_A: {
                    "tenant_id": TENANT_A,
                    "actor_id": "task4-reader-a",
                    "name": "Task4 Reader A",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                },
                API_KEY_B: {
                    "tenant_id": TENANT_B,
                    "actor_id": "task4-reader-b",
                    "name": "Task4 Reader B",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                },
            }
        ),
    )


def _build_client(
    *,
    inner_store: FilesystemObjectStore,
    connection: _DurableDownloadConnection,
) -> TestClient:
    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=_RestartedIngestionService(
            ComplianceEnforcedStore(inner_store=inner_store)
        ),
        rate_limiter=TenantRateLimiter(
            RateLimitConfig(user_rpm=600, integration_rpm=1200, burst_multiplier=2),
            store=InMemoryRateLimitStore(),
        ),
        service_region="me-south-1",
    )

    @app.middleware("http")
    async def _attach_restarted_connection(request: Any, call_next: Any) -> Any:
        request.state.db_conn = connection
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _isolate_memory_store() -> Any:
    clear_document_store()
    yield
    clear_document_store()


@pytest.fixture
def persistent_store(tmp_path: Path) -> FilesystemObjectStore:
    store = FilesystemObjectStore(base_dir=tmp_path / "persistent-objects")
    store.put(TENANT_A, STORAGE_KEY, DURABLE_BYTES, content_type="text/plain")
    return store


def _owner_get(client: TestClient, api_key: str = API_KEY_A) -> Any:
    return client.get(
        f"/v1/documents/{ARTIFACT_ID}",
        headers={"X-IDIS-API-Key": api_key},
    )


def _owner_stream_get(client: TestClient, api_key: str = API_KEY_A) -> Any:
    return client.get(
        f"/v1/documents/{ARTIFACT_ID}/content",
        headers={"X-IDIS-API-Key": api_key},
    )


def test_authenticated_raw_content_stream_is_safe_exact_and_non_materializing(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection()
    object_version = persistent_store.head(TENANT_A, STORAGE_KEY).version_id

    def forbidden_get(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stream endpoint called materializing ObjectStore.get")

    monkeypatch.setattr(persistent_store, "get", forbidden_get)
    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_stream_get(client)

    assert response.status_code == 200
    assert response.content == DURABLE_BYTES
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(len(DURABLE_BYTES))
    assert response.headers["x-idis-content-size"] == str(len(DURABLE_BYTES))
    assert response.headers["x-idis-content-sha256"] == CONTENT_SHA256
    assert response.headers["x-idis-object-version"] == object_version
    assert response.headers["x-idis-stream-contract"] == "durable-object-stream-v1"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers
    rendered_headers = json.dumps(dict(response.headers), sort_keys=True)
    assert STORAGE_KEY not in rendered_headers
    assert "company-001-document-00001.txt" not in rendered_headers


def test_gate_accepts_real_rate_limit_headers_on_authenticated_durable_stream(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    """RED: the gate must admit the real middleware's safe cross-cutting headers."""

    _configure_auth(monkeypatch)
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_REDIS_URL", raising=False)
    connection = _DurableDownloadConnection()
    object_version = persistent_store.head(TENANT_A, STORAGE_KEY).version_id
    source = SimpleNamespace(
        sequence=0,
        size_bytes=len(DURABLE_BYTES),
        content_sha256=CONTENT_SHA256,
    )
    companies = tuple(
        SimpleNamespace(company_alias=f"company-{index:03d}", files=(source,)) for index in (1, 2)
    )
    terminal_rows = [
        {
            "company_alias": company.company_alias,
            "source_sequence": source.sequence,
            "doc_id": ARTIFACT_ID,
            "deal_id": DEAL_A,
            "durable_object_integrity": {
                "schema_version": "durable-object-integrity-v1",
                "content_sha256": CONTENT_SHA256,
                "size_bytes": len(DURABLE_BYTES),
                "version_id": object_version,
                "streaming_verified": True,
            },
        }
        for company in companies
    ]

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        with client.stream(
            "GET",
            f"/v1/documents/{ARTIFACT_ID}/content",
            headers={"X-IDIS-API-Key": API_KEY_A},
        ) as response:
            assert response.status_code == 200
            observed_idis_headers = {
                name.lower() for name in response.headers if name.lower().startswith("x-idis-")
            }
            assert observed_idis_headers == {
                "x-idis-content-sha256",
                "x-idis-content-size",
                "x-idis-object-version",
                "x-idis-stream-contract",
                "x-idis-ratelimit-limit",
                "x-idis-ratelimit-remaining",
            }
            assert int(response.headers["X-IDIS-RateLimit-Limit"]) > 0
            assert int(response.headers["x-idis-ratelimit-remaining"]) >= 0
            assert b"".join(response.iter_bytes()) == DURABLE_BYTES

        proof = real_example_gate._verify_authenticated_downloads(
            api_client=client,
            api_key=API_KEY_A,
            acceptance_tenant_id=TENANT_A,
            source_companies=companies,
            terminal_rows=terminal_rows,
        )

    assert proof["company_count"] == 2
    assert len(proof["receipts_sha256"]) == 64


def test_authenticated_raw_content_stream_masks_cross_tenant_row(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection()

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_stream_get(client, API_KEY_B)

    assert response.status_code == 404
    assert response.json()["code"] == "DOCUMENT_NOT_FOUND"


@pytest.mark.parametrize(
    ("expected_sha256", "expected_size_bytes"),
    [
        (hashlib.sha256(b"different durable bytes").hexdigest(), len(DURABLE_BYTES)),
        (CONTENT_SHA256, len(DURABLE_BYTES) + 1),
    ],
)
def test_authenticated_raw_content_stream_fails_before_body_on_row_object_drift(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection(
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    )

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_stream_get(client)

    assert response.status_code == 409
    assert response.json()["code"] == "DOCUMENT_CONTENT_INTEGRITY_FAILED"


def test_raw_content_stream_openapi_and_policy_contract_are_locked() -> None:
    spec_path = Path(__file__).resolve().parents[2] / "openapi" / "IDIS_OpenAPI_v6_3.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    operation = spec["paths"]["/v1/documents/{docId}/content"]["get"]

    assert operation["operationId"] == "streamDocumentContent"
    assert POLICY_RULES["streamDocumentContent"].is_mutation is False
    response = operation["responses"]["200"]
    assert response["content"]["application/octet-stream"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    assert set(response["headers"]) == {
        "Content-Length",
        "X-IDIS-Content-SHA256",
        "X-IDIS-Content-Size",
        "X-IDIS-Object-Version",
        "X-IDIS-Stream-Contract",
        "Cache-Control",
        "X-Content-Type-Options",
    }


def test_postgres_owner_download_does_not_require_memory_warmup(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection()

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_get(client)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "doc_id": ARTIFACT_ID,
        "tenant_id": TENANT_A,
        "deal_id": DEAL_A,
        "doc_type": "DATA_ROOM_FILE",
        "title": "company-001-document-00001.txt",
        "source_system": "api-upload",
        "version_id": CONTENT_SHA256[:12],
        "ingested_at": "2026-07-16T00:00:00Z",
        "sha256": CONTENT_SHA256,
        "uri": STORAGE_KEY,
        "metadata": {
            "size_bytes": len(DURABLE_BYTES),
            "upload_filename": "company-001-document-00001.txt",
            "streaming_contract_version": "bounded-streaming-v1",
        },
        "content_b64": base64.b64encode(DURABLE_BYTES).decode("ascii"),
        "content_sha256": CONTENT_SHA256,
    }
    assert any("FROM document_artifacts" in sql for sql in connection.sql)


def test_download_survives_fresh_app_and_repository_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    _configure_auth(monkeypatch)
    first_connection = _DurableDownloadConnection()
    with _build_client(inner_store=persistent_store, connection=first_connection) as first_client:
        first_response = _owner_get(first_client)
    assert first_response.status_code == 200

    clear_document_store()
    restarted_connection = _DurableDownloadConnection()
    with _build_client(
        inner_store=persistent_store,
        connection=restarted_connection,
    ) as restarted_client:
        restarted_response = _owner_get(restarted_client)

    assert restarted_response.status_code == 200
    assert restarted_response.json() == first_response.json()
    assert restarted_connection is not first_connection
    assert any("FROM document_artifacts" in sql for sql in restarted_connection.sql)


def test_durable_download_masks_cross_tenant_row_as_not_found(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection()

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_get(client, API_KEY_B)

    assert response.status_code == 404
    assert response.json()["code"] == "DOCUMENT_NOT_FOUND"


def test_durable_delete_cross_tenant_404_is_postgres_rls_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
) -> None:
    """The Task-4 delete canary must reach durable tenant visibility, not empty memory."""
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection()

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = client.delete(
            f"/v1/documents/{ARTIFACT_ID}",
            headers={"X-IDIS-API-Key": API_KEY_B},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "DOCUMENT_NOT_FOUND"
    assert connection.tenant_id == TENANT_B
    assert any("FROM document_artifacts" in sql for sql in connection.sql), (
        "DELETE 404 must follow a durable document_artifacts visibility query"
    )


@pytest.mark.parametrize(
    ("expected_sha256", "expected_size_bytes"),
    [
        (hashlib.sha256(b"different durable bytes").hexdigest(), len(DURABLE_BYTES)),
        (CONTENT_SHA256, len(DURABLE_BYTES) + 1),
    ],
)
def test_durable_download_fails_closed_on_row_object_integrity_drift(
    monkeypatch: pytest.MonkeyPatch,
    persistent_store: FilesystemObjectStore,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    _configure_auth(monkeypatch)
    connection = _DurableDownloadConnection(
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    )

    with _build_client(inner_store=persistent_store, connection=connection) as client:
        response = _owner_get(client)

    assert response.status_code == 409
    assert response.json()["code"] == "DOCUMENT_CONTENT_INTEGRITY_FAILED"
