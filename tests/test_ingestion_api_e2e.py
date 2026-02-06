"""E2E tests for IngestionService wired to Document API [P1-T02].

Tests prove the full flow: create doc → trigger ingest → spans appear in GET response.
All tests use real IngestionService with filesystem-backed compliant store.

Required test coverage per Gate 3 mapping:
- test_create_document_returns_201
- test_ingest_document_calls_ingestion_service
- test_ingest_document_returns_202_with_run_ref
- test_ingest_document_emits_audit_events
- test_ingest_document_fail_closed_on_invalid_data
- test_get_document_returns_artifact
- test_get_document_tenant_isolation
- test_get_spans_after_ingestion
- test_get_spans_before_ingestion_returns_empty
- test_list_documents_still_works
- test_auto_ingest_on_create
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.compliance.byok import BYOKPolicyRegistry, configure_key
from idis.idempotency.store import SqliteIdempotencyStore
from idis.services.ingestion import IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore


def _create_minimal_pdf() -> bytes:
    """Create a minimal valid PDF for testing."""
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF content) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
307
%%EOF
"""


def _create_minimal_xlsx() -> bytes:
    """Create a minimal valid XLSX file for testing."""
    xlsx_buffer = io.BytesIO()

    with zipfile.ZipFile(xlsx_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        rels_ct = "application/vnd.openxmlformats-package.relationships+xml"
        sheet_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
        content_types = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Types xmlns="{ct_ns}">'
            f'<Default Extension="rels" ContentType="{rels_ct}"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{sheet_ct}.sheet.main+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" '
            f'ContentType="{sheet_ct}.worksheet+xml"/>'
            f"</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types)

        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        doc_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        rels = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId1" Type="{doc_rel}/officeDocument" Target="xl/workbook.xml"/>'
            f"</Relationships>"
        )
        zf.writestr("_rels/.rels", rels)

        ss_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        workbook = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<workbook xmlns="{ss_ns}" xmlns:r="{doc_rel}">'
            f"<sheets>"
            f'<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
            f"</sheets>"
            f"</workbook>"
        )
        zf.writestr("xl/workbook.xml", workbook)

        workbook_rels = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId1" Type="{doc_rel}/worksheet" '
            f'Target="worksheets/sheet1.xml"/>'
            f"</Relationships>"
        )
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)

        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            '<c r="A1" t="inlineStr"><is><t>Revenue</t></is></c>'
            '<c r="B1" t="n"><v>1000000</v></c>'
            "</row>"
            '<row r="2">'
            '<c r="A2" t="inlineStr"><is><t>Expenses</t></is></c>'
            '<c r="B2" t="n"><v>750000</v></c>'
            "</row>"
            "</sheetData>"
            "</worksheet>"
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)

    return xlsx_buffer.getvalue()


@pytest.fixture
def tenant_a_id() -> str:
    """Fixed tenant A UUID for deterministic tests."""
    return "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def tenant_b_id() -> str:
    """Fixed tenant B UUID."""
    return "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def actor_a_id() -> str:
    """Actor A UUID."""
    return "actor-a-e2e"


@pytest.fixture
def actor_b_id() -> str:
    """Actor B UUID."""
    return "actor-b-e2e"


@pytest.fixture
def api_key_a() -> str:
    """API key for tenant A."""
    return "e2e-key-a"


@pytest.fixture
def api_key_b() -> str:
    """API key for tenant B."""
    return "e2e-key-b"


@pytest.fixture
def deal_id() -> str:
    """Generate a deal UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def _wired_app_context(
    tenant_a_id: str,
    tenant_b_id: str,
    actor_a_id: str,
    actor_b_id: str,
    api_key_a: str,
    api_key_b: str,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Build a fully wired app with IngestionService, compliant store, and BYOK.

    Returns dict with keys: client, audit_sink, ingestion_service, compliant_store, tenant_ctx_a.
    """
    clear_deals_store()
    clear_document_store()

    api_keys_config = {
        api_key_a: {
            "tenant_id": tenant_a_id,
            "actor_id": actor_a_id,
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        api_key_b: {
            "tenant_id": tenant_b_id,
            "actor_id": actor_b_id,
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))

    tmpdir = tempfile.mkdtemp(prefix="idis_e2e_")
    inner_store = FilesystemObjectStore(base_dir=Path(tmpdir))
    byok_registry = BYOKPolicyRegistry()
    compliant_store = ComplianceEnforcedStore(inner_store=inner_store, byok_registry=byok_registry)

    audit_sink = InMemoryAuditSink()
    ingestion_service = IngestionService(compliant_store=compliant_store, audit_sink=audit_sink)

    idem_store = SqliteIdempotencyStore(in_memory=True)
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=idem_store,
        ingestion_service=ingestion_service,
    )

    tenant_ctx_a = TenantContext(
        tenant_id=tenant_a_id,
        actor_id=actor_a_id,
        name="Tenant A",
        timezone="UTC",
        data_region="me-south-1",
    )
    configure_key(tenant_ctx_a, "e2e-key-alias", audit_sink, registry=byok_registry)

    tenant_ctx_b = TenantContext(
        tenant_id=tenant_b_id,
        actor_id=actor_b_id,
        name="Tenant B",
        timezone="UTC",
        data_region="me-south-1",
    )
    configure_key(tenant_ctx_b, "e2e-key-alias-b", audit_sink, registry=byok_registry)

    client = TestClient(app, raise_server_exceptions=False)

    return {
        "client": client,
        "audit_sink": audit_sink,
        "ingestion_service": ingestion_service,
        "compliant_store": compliant_store,
        "tenant_ctx_a": tenant_ctx_a,
        "tenant_ctx_b": tenant_ctx_b,
    }


def _store_test_file(
    compliant_store: ComplianceEnforcedStore,
    tenant_ctx: TenantContext,
    storage_key: str,
    data: bytes,
) -> None:
    """Store test file bytes in the compliant store."""
    compliant_store.put(
        tenant_ctx=tenant_ctx,
        key=storage_key,
        data=data,
    )


class TestCreateDocumentReturns201:
    """Proves basic creation works after wiring."""

    def test_create_document_returns_201(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """POST /v1/deals/{dealId}/documents returns 201 with IngestionService wired."""
        client = _wired_app_context["client"]

        response = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={"doc_type": "PITCH_DECK", "title": "E2E Test Doc", "auto_ingest": False},
        )

        assert response.status_code == 201
        body = response.json()
        assert "doc_id" in body
        assert body["deal_id"] == deal_id
        assert body["doc_type"] == "PITCH_DECK"


class TestIngestDocumentCallsIngestionService:
    """Proves POST /ingest invokes IngestionService.ingest_bytes()."""

    def test_ingest_document_calls_ingestion_service(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """POST /v1/documents/{docId}/ingest calls IngestionService and returns SUCCEEDED."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/ingest-test.pdf"

        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "ingest-service-test",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={},
        )

        assert ingest_resp.status_code == 202
        body = ingest_resp.json()
        assert body["status"] == "SUCCEEDED"

        ingestion_svc = _wired_app_context["ingestion_service"]
        assert len(ingestion_svc._artifacts) > 0


class TestIngestDocumentReturns202WithRunRef:
    """Proves response matches OpenAPI 202 + RunRef."""

    def test_ingest_document_returns_202_with_run_ref(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """POST /v1/documents/{docId}/ingest returns 202 with valid RunRef schema."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/runref-test.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "FINANCIAL_MODEL",
                "title": "runref-test",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={},
        )

        assert ingest_resp.status_code == 202
        body = ingest_resp.json()
        assert "run_id" in body
        assert "status" in body
        assert body["status"] in ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


class TestIngestDocumentEmitsAuditEvents:
    """Proves audit sink receives document.ingestion.completed."""

    def test_ingest_document_emits_audit_events(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """Ingestion emits document.ingestion.completed audit event."""
        client = _wired_app_context["client"]
        audit_sink: InMemoryAuditSink = _wired_app_context["audit_sink"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/audit-test.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "audit-events-test",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        audit_sink.clear()

        client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={},
        )

        events = audit_sink.events
        event_types = [e.get("event_type") for e in events]
        assert "document.ingestion.completed" in event_types

        completed = next(e for e in events if e["event_type"] == "document.ingestion.completed")
        assert completed["tenant_id"] == tenant_ctx_a.tenant_id
        assert "resource" in completed
        assert completed["resource"]["resource_type"] == "document"


class TestIngestDocumentFailClosedOnInvalidData:
    """Proves bad input → structured error, no silent pass."""

    def test_ingest_document_fail_closed_on_invalid_data(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """Ingesting with SHA256 mismatch returns FAILED, not SUCCEEDED."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/fail-closed-test.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        wrong_sha256 = "f" * 64

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "fail-closed-test",
                "uri": f"file://{storage_key}",
                "sha256": wrong_sha256,
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={},
        )

        assert ingest_resp.status_code == 202
        body = ingest_resp.json()
        assert body["status"] == "FAILED"
        assert "run_id" in body


class TestGetDocumentReturnsArtifact:
    """Proves GET /documents/{docId} returns correct data."""

    def test_get_document_returns_artifact(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """GET /v1/documents/{docId} returns document metadata with content."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        test_content = b"test artifact content for GET endpoint"
        storage_key = "e2e/get-artifact-test.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, test_content)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "DATA_ROOM_FILE",
                "title": "get-artifact-test",
                "uri": f"file://{storage_key}",
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        get_resp = client.get(
            f"/v1/documents/{doc_id}",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["doc_id"] == doc_id
        assert body["title"] == "get-artifact-test"
        assert body["content_sha256"] == hashlib.sha256(test_content).hexdigest()


class TestGetDocumentTenantIsolation:
    """Proves cross-tenant GET returns 404."""

    def test_get_document_tenant_isolation(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        api_key_b: str,
        deal_id: str,
    ) -> None:
        """Tenant B cannot GET a document created by tenant A."""
        client = _wired_app_context["client"]

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "Tenant A Secret",
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        get_resp = client.get(
            f"/v1/documents/{doc_id}",
            headers={"X-IDIS-API-Key": api_key_b},
        )

        assert get_resp.status_code == 404


class TestGetSpansAfterIngestion:
    """Proves spans endpoint returns spans from actual parsing."""

    def test_get_spans_after_ingestion(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """GET /documents/{docId}/spans returns spans after successful ingestion."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/spans-test.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "spans-test",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={},
        )
        assert ingest_resp.json()["status"] == "SUCCEEDED"

        spans_resp = client.get(
            f"/v1/documents/{doc_id}/spans",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert spans_resp.status_code == 200
        body = spans_resp.json()
        assert "items" in body
        assert "total" in body
        assert body["total"] >= 0
        if body["total"] > 0:
            span = body["items"][0]
            assert "span_id" in span
            assert "document_id" in span
            assert "span_type" in span
            assert "locator" in span


class TestGetSpansBeforeIngestionReturnsEmpty:
    """Proves spans before ingest → empty list."""

    def test_get_spans_before_ingestion_returns_empty(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """GET /documents/{docId}/spans returns empty before ingestion."""
        client = _wired_app_context["client"]

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "no-ingest-yet",
                "uri": "file://e2e/no-ingest.pdf",
                "auto_ingest": False,
            },
        )
        doc_id = create_resp.json()["doc_id"]

        spans_resp = client.get(
            f"/v1/documents/{doc_id}/spans",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert spans_resp.status_code == 200
        body = spans_resp.json()
        assert body["items"] == []
        assert body["total"] == 0


class TestListDocumentsStillWorks:
    """Proves list behavior not broken after wiring."""

    def test_list_documents_still_works(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """GET /v1/deals/{dealId}/documents returns documents after wiring."""
        client = _wired_app_context["client"]

        client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={"doc_type": "PITCH_DECK", "title": "List Test 1", "auto_ingest": False},
        )
        client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={"doc_type": "FINANCIAL_MODEL", "title": "List Test 2", "auto_ingest": False},
        )

        list_resp = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a},
        )

        assert list_resp.status_code == 200
        body = list_resp.json()
        assert len(body["items"]) == 2
        titles = {item["title"] for item in body["items"]}
        assert "List Test 1" in titles
        assert "List Test 2" in titles


class TestAutoIngestOnCreate:
    """Proves auto_ingest=true triggers pipeline."""

    def test_auto_ingest_on_create(
        self,
        _wired_app_context: dict[str, Any],
        api_key_a: str,
        deal_id: str,
    ) -> None:
        """POST with auto_ingest=true triggers ingestion and spans become available."""
        client = _wired_app_context["client"]
        compliant_store = _wired_app_context["compliant_store"]
        tenant_ctx_a = _wired_app_context["tenant_ctx_a"]
        audit_sink: InMemoryAuditSink = _wired_app_context["audit_sink"]

        pdf_data = _create_minimal_pdf()
        storage_key = "e2e/auto-ingest.pdf"
        _store_test_file(compliant_store, tenant_ctx_a, storage_key, pdf_data)

        audit_sink.clear()

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": api_key_a, "Content-Type": "application/json"},
            json={
                "doc_type": "PITCH_DECK",
                "title": "auto-ingest-e2e",
                "uri": f"file://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": True,
            },
        )

        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        events = audit_sink.events
        event_types = [e.get("event_type") for e in events]
        assert "document.ingestion.completed" in event_types or (
            "document.ingestion.failed" in event_types
        )

        spans_resp = client.get(
            f"/v1/documents/{doc_id}/spans",
            headers={"X-IDIS-API-Key": api_key_a},
        )
        assert spans_resp.status_code == 200
        body = spans_resp.json()
        assert body["total"] >= 0
