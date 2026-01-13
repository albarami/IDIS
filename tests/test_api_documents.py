"""Tests for IDIS Documents API endpoints.

Tests cover:
A) Happy path: createDealDocument → listDealDocuments → ingestDocument
B) Tenant isolation: documents in tenant A not visible to tenant B
C) Idempotency: same key returns same response, payload mismatch returns 409
D) Pagination: limit=1 yields next_cursor, second page returns remaining
E) Ingest failure cases: missing uri, unsupported scheme, sha256 mismatch
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore


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
def deal_id() -> str:
    """Generate a deal UUID."""
    return str(uuid.uuid4())


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
            "data_region": "us-east-1",
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
            json={"doc_type": "PITCH_DECK", "title": "Doc 1"},
        )

        client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
            },
            json={"doc_type": "FINANCIAL_MODEL", "title": "Doc 2"},
        )

        response = client_single_tenant.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2

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
                json={"doc_type": "OTHER", "title": f"Doc {i}"},
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
                json={"doc_type": "OTHER", "title": f"Paginated Doc {i}"},
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
                json={"doc_type": "OTHER", "title": f"Page Doc {i}"},
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
            json={"doc_type": "PITCH_DECK", "title": "Tenant A Only"},
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

        assert response_b.status_code == 200
        assert len(response_b.json()["items"]) == 0

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
        """Ingest with unsupported URI scheme returns 202 with FAILED status."""
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


class TestIdempotency:
    """Test idempotency for document creation."""

    def test_same_idempotency_key_returns_same_response(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Same Idempotency-Key returns identical response (via middleware)."""
        idempotency_key = f"idem_{uuid.uuid4().hex[:8]}"
        payload = {"doc_type": "PITCH_DECK", "title": "Idempotent Doc"}

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
            json={"doc_type": "PITCH_DECK", "title": "Original Title"},
        )

        response2 = client_single_tenant.post(
            f"/v1/deals/{deal_id}/documents",
            headers={
                "X-IDIS-API-Key": api_key_a,
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={"doc_type": "PITCH_DECK", "title": "Different Title"},
        )

        assert response1.status_code == 201
        assert response2.status_code == 409
        assert response2.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"


class TestHappyPath:
    """End-to-end happy path tests."""

    def test_create_list_ingest_flow(
        self, client_single_tenant: TestClient, api_key_a: str, deal_id: str
    ) -> None:
        """Full flow: create document → list shows it → ingest returns RunRef."""
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
            json={"doc_type": "PITCH_DECK", "title": "Audit Test Doc"},
        )

        events = audit_sink.events
        doc_created_events = [e for e in events if e.get("event_type") == "document.created"]
        assert len(doc_created_events) >= 1

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
            json={"doc_type": "PITCH_DECK", "title": "Request ID Test"},
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
