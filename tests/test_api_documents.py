"""Tests for IDIS Documents API endpoints.

Tests cover:
A) Happy path: createDealDocument -> listDealDocuments -> ingestDocument
B) Tenant isolation: documents in tenant A not visible to tenant B
C) Idempotency: same key returns same response, payload mismatch returns 409
D) Pagination: limit=1 yields next_cursor, second page returns remaining
E) Ingest failure cases: missing uri, unsupported scheme, sha256 mismatch
"""

import json
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from tests.abac_seed import seed_deal_access

DOCUMENT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ARTIFACT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def tenant_a_id() -> str:
    """Generate tenant A UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def tenant_b_id() -> str:
    """Generate tenant B UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def api_key_a() -> str:
    """Generate API key for tenant A."""
    return f"key-a-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def api_key_b() -> str:
    """Generate API key for tenant B."""
    return f"key-b-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def actor_a_id() -> str:
    """Generate actor A UUID."""
    return f"actor-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def actor_b_id() -> str:
    """Generate actor B UUID."""
    return f"actor-b-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def deal_id(tenant_a_id: str, actor_a_id: str) -> str:
    """Generate a deal UUID and seed tenant A's actor as an authorized assignee (Task 2.6).

    Nearly every test drives this deal as authorized tenant A (api_key_a). Deal-scoped ops
    (createDealDocument / listDealDocuments / getDealDocumentSummary / uploadDealDocument) are now
    ABAC deny-by-default, so the operating actor must hold an assignment. Seeding here through the
    app's default store is the direct analog of the reference test_api_runs.py deal_id fixture.
    Tenant B is never seeded, so cross-tenant tests still (correctly) get 403.
    """
    did = str(uuid.uuid4())
    seed_deal_access(tenant_a_id, did, actor_a_id)
    return did


@pytest.fixture
def api_keys_config_single(
    tenant_a_id: str, actor_a_id: str, api_key_a: str
) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration with single tenant."""
    return {
        api_key_a: {
            "tenant_id": tenant_a_id,
            "actor_id": actor_a_id,
            "name": "Tenant A",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


@pytest.fixture
def api_keys_config_multi(
    tenant_a_id: str,
    tenant_b_id: str,
    actor_a_id: str,
    actor_b_id: str,
    api_key_a: str,
    api_key_b: str,
) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration with two tenants."""
    return {
        api_key_a: {
            "tenant_id": tenant_a_id,
            "actor_id": actor_a_id,
            "name": "Tenant A",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        api_key_b: {
            "tenant_id": tenant_b_id,
            "actor_id": actor_b_id,
            "name": "Tenant B",
            "timezone": "America/New_York",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


@pytest.fixture
def client_single_tenant(
    api_keys_config_single: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with single tenant configured."""
    clear_deals_store()
    clear_document_store()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)

    app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
    return TestClient(app)


@pytest.fixture
def client_multi_tenant(
    api_keys_config_multi: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with two tenants configured."""
    clear_deals_store()
    clear_document_store()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_multi))

    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)

    app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
    return TestClient(app)


def _client_with_fake_db(
    *,
    api_keys_config: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    row: MagicMock | list[MagicMock],
) -> TestClient:
    """Create a test client with a request-scoped fake document DB row."""
    clear_deals_store()
    clear_document_store()
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))

    app = create_app(
        audit_sink=InMemoryAuditSink(),
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
    )
    fake_conn = _FakeDocumentDbConn(row)

    @app.middleware("http")
    async def _fake_db_conn_middleware(request: Any, call_next: Any) -> Any:
        request.state.db_conn = fake_conn
        return await call_next(request)

    return TestClient(app)


class _FakeDocumentDbConn:
    """Minimal DB connection for PostgresDocumentsRepository document reads."""

    def __init__(self, row: MagicMock | list[MagicMock]) -> None:
        self._rows = row if isinstance(row, list) else [row]

    def execute(self, statement: object, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "WHERE documents.document_id = :document_id" in sql:
            matching_row = next(
                (
                    row
                    for row in self._rows
                    if row._mapping["document_id"] == (params or {}).get("document_id")
                ),
                None,
            )
            result.fetchone.return_value = matching_row
            return result
        if "WHERE documents.deal_id = :deal_id" in sql:
            result.fetchall.return_value = [
                row
                for row in self._rows
                if row._mapping["deal_id"] == (params or {}).get("deal_id")
            ]
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")


def _document_row(
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str = DOCUMENT_ID,
    artifact_id: str = ARTIFACT_ID,
    metadata: dict[str, Any] | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a joined durable document row with parser metadata only."""
    return MagicMock(
        _mapping={
            "document_id": document_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "doc_id": artifact_id,
            "doc_type": "PDF",
            "artifact_doc_type": "DATA_ROOM_FILE",
            "parse_status": "PARSED",
            "document_metadata": metadata
            or {"name": "durable-source.pdf", "detected_format": "PDF"},
            "artifact_metadata": source_metadata or {"source_system": "api"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "document_name": "durable-source.pdf",
            "sha256": "a" * 64,
            "uri": "deals/durable-source.pdf",
        },
    )


class TestCreateDealDocument:
    """Test POST /v1/deals/{dealId}/documents endpoint."""

    def test_create_document_returns_201(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """POST /v1/deals/{dealId}/documents returns 201 with valid payload."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Q4 Pitch Deck",
                "auto_ingest": False,
            },
        )

        assert response.status_code == 201

    def test_create_document_returns_document_artifact(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Created document contains required fields per OpenAPI spec."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "FINANCIAL_MODEL",
                "title": "Financial Model v2",
                "source_system": "DocSend",
                "auto_ingest": False,
            },
        )

        body = response.json()
        assert "doc_id" in body
        assert body["deal_id"] == deal_id
        assert body["doc_type"] == "FINANCIAL_MODEL"
        assert body["title"] == "Financial Model v2"
        assert body["source_system"] == "DocSend"
        assert "version_id" in body
        assert "ingested_at" in body

    def test_create_document_with_all_fields(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Document creation with all optional fields succeeds."""
        sha256 = "a" * 64
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "DATA_ROOM_FILE",
                "title": "Term Sheet Draft",
                "source_system": "Google Drive",
                "uri": "idis://bucket/path/file.pdf",
                "sha256": sha256,
                "metadata": {"author": "John Doe", "version": "1.0"},
                "auto_ingest": False,
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["sha256"] == sha256
        assert body["uri"] == "idis://bucket/path/file.pdf"
        assert body["metadata"]["author"] == "John Doe"

    def test_create_document_unsupported_uri_returns_400(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """auto_ingest=true with unsupported URI scheme returns 400."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "External Doc",
                "uri": "https://external.example.com/file.pdf",
                "auto_ingest": True,
            },
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "BAD_REQUEST"
        assert "Unsupported URI scheme" in body["message"]

    def test_create_document_rejects_file_uri_even_without_auto_ingest(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Public document registration must not accept server-local file URIs."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "DATA_ROOM_FILE",
                "title": "Local Path",
                "uri": "file://C:/unsafe/data-room/model.xlsx",
                "auto_ingest": False,
            },
        )

        assert response.status_code == 400
        assert response.json()["code"] == "BAD_REQUEST"

    @pytest.mark.parametrize(
        "uri",
        [
            "idis://C:/unsafe/data-room/model.xlsx",
            "idis:///tmp/data-room/model.xlsx",
            "idis://../secret/model.xlsx",
            "idis://folder/../../secret/model.xlsx",
            "idis://\\\\server\\share\\model.xlsx",
        ],
    )
    def test_create_document_rejects_path_like_object_store_uri(
        self,
        client_single_tenant: TestClient,
        api_key_a: str,
        deal_id: str,
        uri: str,
    ) -> None:
        """Accepted URI schemes still cannot smuggle server-local path semantics."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "DATA_ROOM_FILE",
                "title": "Path-Like Object Key",
                "uri": uri,
                "auto_ingest": False,
            },
        )

        assert response.status_code == 400
        assert response.json()["code"] == "BAD_REQUEST"

    def test_create_document_without_auth_returns_401(
        self, client_single_tenant: TestClient, deal_id: str
    ) -> None:
        """POST without API key returns 401."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "No Auth Doc",
            },
        )

        assert response.status_code == 401


class TestListDealDocuments:
    """Test GET /v1/deals/{dealId}/documents endpoint."""

    def test_list_documents_returns_empty_initially(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """GET /v1/deals/{dealId}/documents returns empty list initially."""
        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert len(body["items"]) == 0
        assert body["next_cursor"] is None

    def test_list_documents_returns_created_documents(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Created documents appear in list response."""
        client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "PITCH_DECK", "title": "Doc 1", "auto_ingest": False},
        )

        client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "FINANCIAL_MODEL", "title": "Doc 2", "auto_ingest": False},
        )

        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2

    def test_list_documents_uses_durable_repository_when_db_conn_exists(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        tenant_a_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Production listing should expose durable document_id safe summaries."""
        client = _client_with_fake_db(
            api_keys_config=api_keys_config_single,
            monkeypatch=monkeypatch,
            row=_document_row(tenant_id=tenant_a_id, deal_id=deal_id),
        )

        response = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["document_id"] == DOCUMENT_ID
        assert body["items"][0]["doc_id"] == ARTIFACT_ID
        assert body["items"][0]["doc_type"] == "DATA_ROOM_FILE"
        assert body["items"][0]["parse_status"] == "PARSED"
        assert "content_b64" not in body["items"][0]
        assert "text_excerpt" not in body["items"][0]
        assert "spans" not in body["items"][0]

    def test_list_documents_sanitizes_durable_metadata_and_preserves_pagination(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        tenant_a_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Durable list summaries redact raw-content metadata and keep cursors usable."""
        first_row = _document_row(
            tenant_id=tenant_a_id,
            deal_id=deal_id,
            document_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
            artifact_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
            metadata={
                "name": "first.pdf",
                "content_b64": "unsafe",
                "nested": {"text_excerpt": "unsafe", "safe": "kept"},
            },
            source_metadata={
                "source_system": "api",
                "raw_text": "unsafe",
                "nested": {"spans": ["unsafe"], "safe": "kept"},
            },
        )
        second_row = _document_row(
            tenant_id=tenant_a_id,
            deal_id=deal_id,
            document_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2",
            artifact_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
        )
        client = _client_with_fake_db(
            api_keys_config=api_keys_config_single,
            monkeypatch=monkeypatch,
            row=[first_row, second_row],
        )

        first_page = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 1},
        )
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert first_body["items"][0]["metadata"] == {
            "name": "first.pdf",
            "nested": {"safe": "kept"},
        }
        assert first_body["items"][0]["source_metadata"] == {
            "source_system": "api",
            "nested": {"safe": "kept"},
        }
        assert first_body["next_cursor"] is not None

        second_page = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 1, "cursor": first_body["next_cursor"]},
        )
        assert second_page.status_code == 200
        assert second_page.json()["items"][0]["document_id"] == (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"
        )
        assert second_page.json()["next_cursor"] is None

    def test_get_deal_document_summary_is_durable_and_safe(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        tenant_a_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deal-scoped durable get returns summary metadata without raw content."""
        client = _client_with_fake_db(
            api_keys_config=api_keys_config_single,
            monkeypatch=monkeypatch,
            row=_document_row(tenant_id=tenant_a_id, deal_id=deal_id),
        )

        response = client.get(
            f"/v1/deals/{deal_id}/documents/{DOCUMENT_ID}",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["document_id"] == DOCUMENT_ID
        assert body["deal_id"] == deal_id
        assert body["title"] == "durable-source.pdf"
        assert "content_b64" not in body
        assert "content_sha256" not in body
        assert "text_excerpt" not in body
        assert "spans" not in body

    def test_get_deal_document_summary_rejects_cross_deal_access(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        tenant_a_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Durable document summaries must be deal-scoped, not just tenant-scoped."""
        other_deal_id = str(uuid.uuid4())
        client = _client_with_fake_db(
            api_keys_config=api_keys_config_single,
            monkeypatch=monkeypatch,
            row=_document_row(tenant_id=tenant_a_id, deal_id=other_deal_id),
        )

        response = client.get(
            f"/v1/deals/{deal_id}/documents/{DOCUMENT_ID}",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 404

    def test_list_documents_respects_limit(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Limit parameter restricts number of returned items."""
        for i in range(5):
            client_single_tenant.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={"doc_type": "OTHER", "title": f"Doc {i}", "auto_ingest": False},
            )

        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 2},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2


class TestPagination:
    """Test cursor-based pagination for document listing."""

    def test_pagination_returns_next_cursor(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """When more items exist, next_cursor is returned."""
        for i in range(3):
            client_single_tenant.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={"doc_type": "OTHER", "title": f"Paginated Doc {i}", "auto_ingest": False},
            )

        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["next_cursor"] is not None

    def test_pagination_second_page_returns_remaining(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Using next_cursor returns remaining items."""
        doc_ids = []
        for i in range(3):
            resp = client_single_tenant.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={"doc_type": "OTHER", "title": f"Page Doc {i}", "auto_ingest": False},
            )
            doc_ids.append(resp.json()["doc_id"])

        page1 = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 2},
        )

        assert page1.status_code == 200
        body1 = page1.json()
        assert len(body1["items"]) == 2
        next_cursor = body1["next_cursor"]

        page2 = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
            params={"limit": 2, "cursor": next_cursor},
        )

        assert page2.status_code == 200
        body2 = page2.json()
        assert len(body2["items"]) >= 1

        all_doc_ids = {item["doc_id"] for item in body1["items"]}
        all_doc_ids.update(item["doc_id"] for item in body2["items"])
        assert len(all_doc_ids) == 3


class TestTenantIsolation:
    """Test tenant isolation for document operations."""

    def test_tenant_a_documents_not_visible_to_tenant_b(
        self,
        client_multi_tenant: TestClient,
        api_key_a: str,
        api_key_b: str,
        deal_id: str,
    ) -> None:
        """Documents created by tenant A are not visible to tenant B."""
        client_multi_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "PITCH_DECK", "title": "Tenant A Only", "auto_ingest": False},
        )

        response_a = client_multi_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        response_b = client_multi_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_b},
        )

        assert response_a.status_code == 200
        assert len(response_a.json()["items"]) == 1

        # Task 2.6: tenant B holds no assignment on this deal, so ABAC denies before the route
        # (previously 200 with an empty list under the pre-2.5 route-level bypass). Cross-tenant
        # isolation is now enforced deny-by-default at the ABAC layer.
        assert response_b.status_code == 403
        assert response_b.json()["code"] == "ABAC_DENIED_NO_ASSIGNMENT"

    def test_ingest_document_from_other_tenant_returns_404(
        self,
        client_multi_tenant: TestClient,
        api_key_a: str,
        api_key_b: str,
        deal_id: str,
    ) -> None:
        """Tenant B cannot ingest document created by tenant A."""
        create_response = client_multi_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Tenant A Doc",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )

        doc_id = create_response.json()["doc_id"]

        ingest_response = client_multi_tenant.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_b,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_response.status_code == 404
        assert ingest_response.json()["code"] == "NOT_FOUND"


class TestIngestDocument:
    """Test POST /v1/documents/{docId}/ingest endpoint."""

    def test_ingest_document_returns_202(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Ingest request returns 202 with RunRef."""
        create_response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Ingestable Doc",
                "uri": "idis://bucket/ingest.pdf",
                "auto_ingest": False,
            },
        )

        doc_id = create_response.json()["doc_id"]

        ingest_response = client_single_tenant.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_response.status_code == 202
        body = ingest_response.json()
        assert "run_id" in body
        assert "status" in body

    def test_ingest_nonexistent_document_returns_404(
        self, client_single_tenant: TestClient, api_key_a: str
    ) -> None:
        """Ingest request for nonexistent document returns 404."""
        fake_doc_id = str(uuid.uuid4())

        response = client_single_tenant.post(
            f"/v1/documents/{fake_doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    def test_ingest_missing_uri_returns_failed_status(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Ingest with missing URI returns 202 with FAILED status."""
        create_response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "No URI Doc",
                "auto_ingest": False,
            },
        )

        doc_id = create_response.json()["doc_id"]

        ingest_response = client_single_tenant.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_response.status_code == 202
        body = ingest_response.json()
        assert body["status"] == "FAILED"

    def test_ingest_unsupported_uri_scheme_returns_failed_status(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Unsupported URI schemes are rejected before ingestion can be queued."""
        create_response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "External URI Doc",
                "uri": "https://example.com/file.pdf",
                "auto_ingest": False,
            },
        )

        assert create_response.status_code == 400
        assert create_response.json()["code"] == "BAD_REQUEST"


class TestIdempotency:
    """Test idempotency for document creation."""

    def test_same_idempotency_key_returns_same_response(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Same Idempotency-Key returns identical response (via middleware)."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"doc_type": "PITCH_DECK", "title": "Idempotent Doc", "auto_ingest": False}

        response1 = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        response2 = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response1.status_code == 201
        assert response2.status_code == 201
        assert response1.json()["doc_id"] == response2.json()["doc_id"]

    def test_different_payload_same_key_returns_409(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Same Idempotency-Key with different payload returns 409."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"

        response1 = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"doc_type": "PITCH_DECK", "title": "Original Title", "auto_ingest": False},
        )

        response2 = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"doc_type": "PITCH_DECK", "title": "Different Title", "auto_ingest": False},
        )

        assert response1.status_code == 201
        assert response2.status_code == 409
        assert response2.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"


class TestHappyPath:
    """End-to-end happy path tests."""

    def test_create_list_ingest_flow(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Full flow: create document -> list shows it -> ingest returns RunRef."""
        create_response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "FINANCIAL_MODEL",
                "title": "Q4 Model",
                "uri": "idis://bucket/q4-model.xlsx",
                "auto_ingest": False,
            },
        )

        assert create_response.status_code == 201
        doc_id = create_response.json()["doc_id"]

        list_response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert list_response.status_code == 200
        items = list_response.json()["items"]
        assert any(item["doc_id"] == doc_id for item in items)

        ingest_response = client_single_tenant.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_response.status_code == 202
        run_ref = ingest_response.json()
        assert "run_id" in run_ref
        assert run_ref["status"] in ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


class TestAuditEvents:
    """Test that audit events are emitted for document operations."""

    def test_create_document_emits_audit_event(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document creation emits document.created audit event."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "PITCH_DECK", "title": "Audit Test Doc", "auto_ingest": False},
        )

        events = audit_sink.events
        doc_created_events = [e for e in events if e.get("event_type") == "document.created"]
        assert len(doc_created_events) >= 1

    def test_document_audit_events_hash_idempotency_key(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document route audit events must not contain raw idempotency keys."""
        clear_deals_store()
        clear_document_store()
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))
        raw_idempotency_key = "document-create-raw-idempotency-key"
        audit_sink = InMemoryAuditSink()
        app = create_app(
            audit_sink=audit_sink,
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
        )
        client = TestClient(app)

        response = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": raw_idempotency_key,
            },
            json={"doc_type": "PITCH_DECK", "title": "Audit Test Doc", "auto_ingest": False},
        )

        assert response.status_code == 201
        document_events = [
            event for event in audit_sink.events if event["resource"]["resource_type"] == "document"
        ]
        assert document_events
        encoded_events = json.dumps(document_events, sort_keys=True)
        assert raw_idempotency_key not in encoded_events
        for event in document_events:
            assert "idempotency_key" not in event["request"]
            assert (
                event["request"]["idempotency_key_sha256"]
                == sha256(raw_idempotency_key.encode("utf-8")).hexdigest()
            )

    def test_ingest_document_emits_audit_event(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document ingestion emits ingestion audit event."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Ingest Audit Doc",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )

        doc_id = create_resp.json()["doc_id"]

        client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        events = audit_sink.events
        ingestion_events = [
            e for e in events if e.get("event_type", "").startswith("document.ingestion")
        ]
        assert len(ingestion_events) >= 1

    def test_ingestion_service_audit_events_hash_idempotency_key(self) -> None:
        """Ingestion service audit events must not contain raw idempotency keys."""
        from idis.models.document import Document, DocumentType, ParseStatus
        from idis.models.document_artifact import DocType, DocumentArtifact
        from idis.parsers.base import ParseError, ParseErrorCode
        from idis.services.ingestion import IngestionContext, IngestionService

        audit_sink = InMemoryAuditSink()
        service = IngestionService(compliant_store=MagicMock(), audit_sink=audit_sink)
        tenant_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        document_id = uuid.uuid4()
        now = datetime.now(UTC)
        raw_idempotency_key = "ingestion-service-raw-idempotency-key"
        ctx = IngestionContext(
            tenant_id=tenant_id,
            actor_id="actor-a",
            request_id="req-ingestion",
            idempotency_key=raw_idempotency_key,
        )
        artifact = DocumentArtifact(
            doc_id=artifact_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            doc_type=DocType.PITCH_DECK,
            title="Pitch Deck",
            source_system="api",
            version_id="version-1",
            ingested_at=now,
            sha256="a" * 64,
            uri="idis://bucket/pitch.pdf",
            metadata={},
            created_at=now,
            updated_at=now,
        )
        document = Document(
            document_id=document_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            doc_id=artifact_id,
            doc_type=DocumentType.PDF,
            parse_status=ParseStatus.PARSED,
            metadata={},
            created_at=now,
            updated_at=now,
        )

        service._emit_document_created(ctx, artifact, "a" * 64)
        service._emit_ingestion_completed(ctx, document, span_count=1, sha256="a" * 64)
        document.parse_status = ParseStatus.FAILED
        service._emit_ingestion_failed(
            ctx,
            document,
            [ParseError(code=ParseErrorCode.INTERNAL_ERROR, message="parse failed")],
            "a" * 64,
        )

        assert len(audit_sink.events) == 3
        encoded_events = json.dumps(audit_sink.events, sort_keys=True)
        assert raw_idempotency_key not in encoded_events
        for event in audit_sink.events:
            assert "idempotency_key" not in event["request"]
            assert (
                event["request"]["idempotency_key_sha256"]
                == sha256(raw_idempotency_key.encode("utf-8")).hexdigest()
            )


class TestRequestIdHeader:
    """Test that X-Request-Id header is included in responses."""

    def test_create_document_includes_request_id(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """POST response includes X-Request-Id header."""
        response = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "PITCH_DECK", "title": "Request ID Test", "auto_ingest": False},
        )

        assert "X-Request-Id" in response.headers
        assert len(response.headers["X-Request-Id"]) > 0

    def test_list_documents_includes_request_id(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """GET response includes X-Request-Id header."""
        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert "X-Request-Id" in response.headers

    def test_ingest_document_includes_request_id(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Ingest response includes X-Request-Id header."""
        create_resp = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Ingest Req ID",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )

        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client_single_tenant.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert "X-Request-Id" in ingest_resp.headers


class TestFailClosedIngestion:
    """Test fail-closed behavior when ingestion_service is unavailable.

    These tests verify that:
    - Ingestion never reports SUCCEEDED without SHA256 validation
    - auto_ingest=true returns 400 when ingestion_service is unset
    - ingest endpoint returns 202 with FAILED status when service unavailable
    """

    def test_ingest_fails_closed_when_service_unavailable(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /v1/documents/{docId}/ingest returns 202 FAILED without ingestion_service."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Fail Closed Test",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_resp.status_code == 202
        run_ref = ingest_resp.json()
        assert run_ref["status"] == "FAILED"
        assert "run_id" in run_ref

    def test_ingest_emits_failed_audit_when_service_unavailable(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ingestion emits document.ingestion.failed when service unavailable."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Audit Fail Test",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        events = audit_sink.events
        ingestion_events = [e for e in events if e.get("event_type") == "document.ingestion.failed"]
        assert len(ingestion_events) >= 1

        event = ingestion_events[0]
        assert "error" in event.get("payload", {})

    def test_auto_ingest_returns_400_when_service_unavailable(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST with auto_ingest=true returns 400 when ingestion_service unset."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        app.state.ingestion_service = None
        client = TestClient(app)

        response = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "Auto Ingest Fail",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": True,
            },
        )

        assert response.status_code == 400
        error = response.json()
        assert error.get("code") == "SERVICE_UNAVAILABLE"
        assert "ingestion service unavailable" in error.get("message", "").lower()

    def test_auto_ingest_false_succeeds_without_ingestion_service(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST with auto_ingest=false succeeds even without ingestion_service."""
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        response = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "No Auto Ingest",
                "uri": "idis://bucket/file.pdf",
                "auto_ingest": False,
            },
        )

        assert response.status_code == 201

    def test_no_succeeded_without_sha256_validation(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify ingestion never reports SUCCEEDED without SHA256 validation.

        This is a regression test for the fail-closed invariant: no "SUCCEEDED"
        status can be returned unless bytes were actually ingested and integrity
        was validated via server-computed SHA256.
        """
        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "PITCH_DECK",
                "title": "SHA256 Regression Test",
                "uri": "idis://bucket/file.pdf",
                "sha256": "a" * 64,
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert ingest_resp.status_code == 202
        run_ref = ingest_resp.json()
        assert run_ref["status"] != "SUCCEEDED"


class TestBYOKRevokeRealPath:
    """Real-path integration tests for BYOK revoke enforcement.

    These tests verify that BYOK revoke denial surfaces as HTTP 403
    with code BYOK_KEY_REVOKED, not swallowed into 202 run failed.
    """

    def test_ingestion_denied_when_byok_key_revoked_real_path(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        tenant_a_id: str,
        actor_a_id: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BYOK revoke must return 403 BYOK_KEY_REVOKED on ingestion, not 202.

        This test configures BYOK, revokes the key, then attempts ingestion.
        The route must return 403 with code BYOK_KEY_REVOKED, not swallow
        the error into a 202 run-failed response.
        """
        import tempfile
        from pathlib import Path

        from idis.api.auth import TenantContext
        from idis.audit.sink import InMemoryAuditSink
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            configure_key,
            revoke_key,
        )
        from idis.idempotency.store import SqliteIdempotencyStore
        from idis.services.ingestion import IngestionService
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        byok_registry = BYOKPolicyRegistry()
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        tenant_ctx = TenantContext(
            tenant_id=tenant_a_id,
            actor_id=actor_a_id,
            name="Test Tenant",
            timezone="UTC",
            data_region="me-south-1",
        )

        configure_key(tenant_ctx, "test-key-alias-123", audit_sink, registry=byok_registry)
        revoke_key(tenant_ctx, audit_sink, registry=byok_registry)

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )
            ingestion_service = IngestionService(
                compliant_store=compliant_store,
                audit_sink=audit_sink,
            )

            app = create_app(
                audit_sink=audit_sink,
                idempotency_store=idem_store,
                ingestion_service=ingestion_service,
            )
            client = TestClient(app, raise_server_exceptions=False)

            create_resp = client.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={
                    "doc_type": "PITCH_DECK",
                    "title": "BYOK Revoke Test Doc",
                    "uri": "idis://bucket/revoke-test.pdf",
                    "auto_ingest": False,
                },
            )
            assert create_resp.status_code == 201
            doc_id = create_resp.json()["doc_id"]

            ingest_resp = client.post(
                f"/v1/documents/{doc_id}/ingest",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={},
            )

            assert ingest_resp.status_code == 403, (
                f"Expected 403 for BYOK revoke, got {ingest_resp.status_code}: {ingest_resp.text}"
            )
            body = ingest_resp.json()
            assert body["code"] == "BYOK_KEY_REVOKED"
            assert body["message"] == "Access denied."


class TestLegalHoldDeleteRealPath:
    """Real-path integration tests for legal hold delete protection.

    These tests verify that delete with active legal hold returns HTTP 403
    with code DELETION_BLOCKED_BY_HOLD.
    """

    def test_document_delete_blocked_when_legal_hold_active_real_path(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        tenant_a_id: str,
        actor_a_id: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legal hold must return 403 DELETION_BLOCKED_BY_HOLD on delete.

        This test creates a document, applies a legal hold, then attempts
        deletion. The route must return 403 with DELETION_BLOCKED_BY_HOLD.
        """
        from idis.api.auth import TenantContext
        from idis.audit.sink import InMemoryAuditSink
        from idis.compliance.retention import (
            HoldTarget,
            LegalHoldRegistry,
            apply_hold,
        )
        from idis.idempotency.store import SqliteIdempotencyStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        hold_registry = LegalHoldRegistry()
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app, raise_server_exceptions=False)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "TERM_SHEET",
                "title": "Legal Hold Test Doc",
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        tenant_ctx = TenantContext(
            tenant_id=tenant_a_id,
            actor_id=actor_a_id,
            name="Test Tenant",
            timezone="UTC",
            data_region="me-south-1",
        )

        apply_hold(
            tenant_ctx=tenant_ctx,
            target_type=HoldTarget.ARTIFACT,
            target_id=doc_id,
            reason="Litigation hold for compliance test",
            audit_sink=audit_sink,
            registry=hold_registry,
        )

        from idis.compliance.retention import (
            reset_legal_hold_registry,
            set_legal_hold_registry,
        )

        set_legal_hold_registry(hold_registry)  # the seam the delete route consults (Task 6)

        try:
            delete_resp = client.delete(
                f"/v1/documents/{doc_id}",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                },
            )

            assert delete_resp.status_code == 403, (
                f"Expected 403 for legal hold, got {delete_resp.status_code}: {delete_resp.text}"
            )
            body = delete_resp.json()
            assert body["code"] == "DELETION_BLOCKED_BY_HOLD"
            assert body["message"] == "Access denied."
        finally:
            reset_legal_hold_registry()

    def test_document_delete_succeeds_without_legal_hold(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document delete succeeds when no legal hold is active."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.idempotency.store import SqliteIdempotencyStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app, raise_server_exceptions=False)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={
                "doc_type": "OTHER",
                "title": "Delete Test Doc",
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        delete_resp = client.delete(
            f"/v1/documents/{doc_id}",
            headers={
                "X-IDIS-API-Key": api_key_a,
            },
        )

        assert delete_resp.status_code == 200
        body = delete_resp.json()
        assert body["doc_id"] == doc_id
        assert body["deleted"] is True

    def test_document_delete_returns_404_for_nonexistent(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document delete returns 404 for non-existent document."""
        from idis.audit.sink import InMemoryAuditSink
        from idis.idempotency.store import SqliteIdempotencyStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
        client = TestClient(app, raise_server_exceptions=False)

        fake_doc_id = str(uuid.uuid4())

        delete_resp = client.delete(
            f"/v1/documents/{fake_doc_id}",
            headers={
                "X-IDIS-API-Key": api_key_a,
            },
        )

        assert delete_resp.status_code == 404
        body = delete_resp.json()
        assert body["code"] == "DOCUMENT_NOT_FOUND"


class TestBYOKRevokeGetRealPath:
    """Real-path integration tests for BYOK revoke enforcement on GET.

    These tests verify that BYOK revoke denial surfaces as HTTP 403
    with code BYOK_KEY_REVOKED on document GET.
    """

    def test_documents_get_denied_when_byok_key_revoked_real_path(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        tenant_a_id: str,
        actor_a_id: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BYOK revoke must return 403 BYOK_KEY_REVOKED on GET.

        This test creates a document, then revokes the BYOK key and attempts
        GET. The route must return 403 with code BYOK_KEY_REVOKED and
        message 'Access denied.'
        """
        import tempfile
        from pathlib import Path

        from idis.api.auth import TenantContext
        from idis.audit.sink import InMemoryAuditSink
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            configure_key,
            revoke_key,
        )
        from idis.idempotency.store import SqliteIdempotencyStore
        from idis.services.ingestion import IngestionService
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        byok_registry = BYOKPolicyRegistry()
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        tenant_ctx = TenantContext(
            tenant_id=tenant_a_id,
            actor_id=actor_a_id,
            name="Test Tenant",
            timezone="UTC",
            data_region="me-south-1",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )
            ingestion_service = IngestionService(
                compliant_store=compliant_store,
                audit_sink=audit_sink,
            )

            app = create_app(
                audit_sink=audit_sink,
                idempotency_store=idem_store,
                ingestion_service=ingestion_service,
            )
            client = TestClient(app, raise_server_exceptions=False)

            create_resp = client.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={
                    "doc_type": "PITCH_DECK",
                    "title": "BYOK Revoke GET Test Doc",
                    "uri": "idis://documents/revoke-get-test.pdf",
                    "auto_ingest": False,
                },
            )
            assert create_resp.status_code == 201
            doc_id = create_resp.json()["doc_id"]

            configure_key(tenant_ctx, "test-key-alias-456", audit_sink, registry=byok_registry)

            storage_key = "documents/revoke-get-test.pdf"
            compliant_store.put(
                tenant_ctx=tenant_ctx,
                key=storage_key,
                data=b"test document content for BYOK revoke test",
            )

            revoke_key(tenant_ctx, audit_sink, registry=byok_registry)

            get_resp = client.get(
                f"/v1/documents/{doc_id}",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                },
            )

            assert get_resp.status_code == 403, (
                f"Expected 403 for BYOK revoke on GET, got {get_resp.status_code}: {get_resp.text}"
            )
            body = get_resp.json()
            assert body["code"] == "BYOK_KEY_REVOKED"
            assert body["message"] == "Access denied."

    def test_documents_get_succeeds_when_byok_key_active(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        tenant_a_id: str,
        actor_a_id: str,
        deal_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document GET succeeds when BYOK key is active (positive control)."""
        import tempfile
        from pathlib import Path

        from idis.api.auth import TenantContext
        from idis.audit.sink import InMemoryAuditSink
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            configure_key,
        )
        from idis.idempotency.store import SqliteIdempotencyStore
        from idis.services.ingestion import IngestionService
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        clear_deals_store()
        clear_document_store()

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        byok_registry = BYOKPolicyRegistry()
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)

        tenant_ctx = TenantContext(
            tenant_id=tenant_a_id,
            actor_id=actor_a_id,
            name="Test Tenant",
            timezone="UTC",
            data_region="me-south-1",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )
            ingestion_service = IngestionService(
                compliant_store=compliant_store,
                audit_sink=audit_sink,
            )

            app = create_app(
                audit_sink=audit_sink,
                idempotency_store=idem_store,
                ingestion_service=ingestion_service,
            )
            client = TestClient(app, raise_server_exceptions=False)

            create_resp = client.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={
                    "doc_type": "PITCH_DECK",
                    "title": "BYOK Active GET Test Doc",
                    "uri": "idis://documents/active-get-test.pdf",
                    "auto_ingest": False,
                },
            )
            assert create_resp.status_code == 201
            doc_id = create_resp.json()["doc_id"]

            configure_key(tenant_ctx, "test-key-alias-789", audit_sink, registry=byok_registry)

            storage_key = "documents/active-get-test.pdf"
            test_content = b"real document content for BYOK active GET test"
            compliant_store.put(
                tenant_ctx=tenant_ctx,
                key=storage_key,
                data=test_content,
            )

            get_resp = client.get(
                f"/v1/documents/{doc_id}",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                },
            )

            assert get_resp.status_code == 200, (
                f"Expected 200 for BYOK active on GET, got {get_resp.status_code}: {get_resp.text}"
            )
            body = get_resp.json()
            assert body["doc_id"] == doc_id
            assert body["title"] == "BYOK Active GET Test Doc"
            assert body["uri"] == f"idis://{storage_key}"

            import base64
            import hashlib

            assert "content_b64" in body, "Response must include content_b64 from storage"
            assert "content_sha256" in body, "Response must include content_sha256 from storage"

            decoded_content = base64.b64decode(body["content_b64"])
            assert decoded_content == test_content, (
                f"content_b64 must decode to exact stored bytes. "
                f"Got {decoded_content!r}, expected {test_content!r}"
            )

            expected_sha256 = hashlib.sha256(test_content).hexdigest()
            assert body["content_sha256"] == expected_sha256, (
                f"content_sha256 must match SHA256 of stored bytes. "
                f"Got {body['content_sha256']}, expected {expected_sha256}"
            )

    def test_documents_get_returns_404_when_storage_content_missing(
        self,
        api_keys_config_single: dict[str, dict[str, str]],
        api_key_a: str,
        tenant_a_id: str,
        actor_a_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET returns 404 when document metadata exists but storage content is missing.

        This test ensures the route depends on actual storage retrieval.
        If the storage call is removed and response is built from in-memory metadata,
        this test will fail because the route would return 200 instead of 404.
        """
        import tempfile
        from pathlib import Path

        from idis.api.auth import TenantContext
        from idis.api.main import create_app
        from idis.compliance.byok import BYOKPolicyRegistry, configure_key
        from idis.idempotency.store import SqliteIdempotencyStore
        from idis.services.ingestion import IngestionService
        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config_single))

        deal_id = str(uuid.uuid4())
        # Task 2.6: this test uses a local deal_id (not the seeded fixture). It creates the document
        # as authorized tenant A via the deal-scoped createDealDocument, so seed that assignment;
        # the asserted 404 is the route-level DOCUMENT_CONTENT_NOT_FOUND (storage bytes missing).
        seed_deal_access(tenant_a_id, deal_id, actor_a_id)
        audit_sink = InMemoryAuditSink()
        idem_store = SqliteIdempotencyStore(in_memory=True)
        byok_registry = BYOKPolicyRegistry()

        tenant_ctx = TenantContext(
            tenant_id=tenant_a_id,
            actor_id=actor_a_id,
            name="test-tenant",
            timezone="UTC",
            data_region="me-south-1",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
            compliant_store = ComplianceEnforcedStore(
                inner_store=inner_store,
                byok_registry=byok_registry,
            )
            ingestion_service = IngestionService(
                compliant_store=compliant_store,
                audit_sink=audit_sink,
            )

            app = create_app(
                audit_sink=audit_sink,
                idempotency_store=idem_store,
                ingestion_service=ingestion_service,
            )
            client = TestClient(app, raise_server_exceptions=False)

            configure_key(tenant_ctx, "test-key-alias-missing", audit_sink, registry=byok_registry)

            storage_key = "documents/missing-content-test.pdf"
            create_resp = client.post(
                f"/v1/deals/{deal_id}/documents",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                    "Content-Type": "application/json",
                },
                json={
                    "doc_type": "PITCH_DECK",
                    "title": "Missing Content Test Doc",
                    "uri": f"idis://{storage_key}",
                    "auto_ingest": False,
                },
            )
            assert create_resp.status_code == 201
            doc_id = create_resp.json()["doc_id"]

            get_resp = client.get(
                f"/v1/documents/{doc_id}",
                headers={
                    "X-IDIS-API-Key": api_key_a,
                },
            )

            assert get_resp.status_code == 404, (
                f"Expected 404 when storage content missing, got {get_resp.status_code}"
            )
            body = get_resp.json()
            assert body["code"] == "DOCUMENT_CONTENT_NOT_FOUND"
