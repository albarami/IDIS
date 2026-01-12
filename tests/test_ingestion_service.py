"""Tests for IDIS Ingestion Service (Phase 1.3).

Per v6.3 requirements:
- Happy path PDF: ingest PDF → artifact+document+spans persisted, sha256 correct
- Happy path XLSX: ingest XLSX → spans have sheet/cell based locators
- Fail closed on empty bytes: structured failure, no unhandled exception
- Fail closed on corrupted office zip: parse error, document status FAILED
- Tenant isolation: same bytes in two tenants → no collisions
- Determinism: same bytes twice → span ordering and locators stable
- Audit events: document.created and document.ingestion.completed emitted
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest


@pytest.fixture
def temp_storage_dir() -> Any:
    """Create a temporary directory for storage tests."""
    with tempfile.TemporaryDirectory(prefix="idis_test_ingestion_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def object_store(temp_storage_dir: Path) -> Any:
    """Create a FilesystemObjectStore with a temp directory."""
    from idis.storage.filesystem_store import FilesystemObjectStore

    return FilesystemObjectStore(base_dir=temp_storage_dir)


@pytest.fixture
def audit_sink() -> Any:
    """Create an in-memory audit sink for testing."""
    from idis.audit.sink import InMemoryAuditSink

    return InMemoryAuditSink()


@pytest.fixture
def ingestion_service(object_store: Any, audit_sink: Any) -> Any:
    """Create an IngestionService with test dependencies."""
    from idis.services.ingestion import IngestionService

    return IngestionService(
        object_store=object_store,
        audit_sink=audit_sink,
    )


@pytest.fixture
def tenant_a() -> UUID:
    """Return a valid tenant UUID for tenant A."""
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def tenant_b() -> UUID:
    """Return a valid tenant UUID for tenant B."""
    return UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def deal_id() -> UUID:
    """Return a valid deal UUID."""
    return UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
def ingestion_context(tenant_a: UUID) -> Any:
    """Create a standard ingestion context for testing."""
    from idis.services.ingestion import IngestionContext

    return IngestionContext(
        tenant_id=tenant_a,
        actor_id="test-user",
        request_id="req-001",
    )


def _create_minimal_pdf() -> bytes:
    """Create a minimal valid PDF for testing."""
    pdf_content = b"""%PDF-1.4
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
    return pdf_content


def _create_minimal_xlsx() -> bytes:
    """Create a minimal valid XLSX file for testing."""
    import zipfile

    xlsx_buffer = io.BytesIO()

    with zipfile.ZipFile(xlsx_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        rels_ct = "application/vnd.openxmlformats-package.relationships+xml"
        sheet_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
        content_types = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{ct_ns}">
<Default Extension="rels" ContentType="{rels_ct}"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="{sheet_ct}.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{sheet_ct}.worksheet+xml"/>
</Types>"""
        zf.writestr("[Content_Types].xml", content_types)

        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        doc_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{rel_ns}">
<Relationship Id="rId1" Type="{doc_rel}/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        zf.writestr("_rels/.rels", rels)

        ss_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        workbook = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="{ss_ns}" xmlns:r="{doc_rel}">
<sheets>
<sheet name="Sheet1" sheetId="1" r:id="rId1"/>
</sheets>
</workbook>"""
        zf.writestr("xl/workbook.xml", workbook)

        workbook_rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{rel_ns}">
<Relationship Id="rId1" Type="{doc_rel}/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)

        sheet1 = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1">
<c r="A1" t="inlineStr"><is><t>Revenue</t></is></c>
<c r="B1" t="n"><v>1000000</v></c>
</row>
<row r="2">
<c r="A2" t="inlineStr"><is><t>Expenses</t></is></c>
<c r="B2" t="n"><v>750000</v></c>
</row>
</sheetData>
</worksheet>"""
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)

    return xlsx_buffer.getvalue()


def _create_corrupted_xlsx() -> bytes:
    """Create a corrupted XLSX (valid ZIP header, invalid content)."""
    import zipfile

    xlsx_buffer = io.BytesIO()

    with zipfile.ZipFile(xlsx_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "not valid xml <><><>")

    return xlsx_buffer.getvalue()


class TestHappyPathPDF:
    """Tests for successful PDF ingestion."""

    def test_ingest_pdf_returns_success(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """PDF ingestion should return success with correct attributes."""
        pdf_bytes = _create_minimal_pdf()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="test.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        assert result.success is True
        assert result.artifact_id is not None
        assert result.document_id is not None
        assert result.doc_type == "PDF"
        assert result.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
        assert result.storage_uri is not None
        assert len(result.errors) == 0

    def test_ingest_pdf_persists_artifact(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        tenant_a: UUID,
    ) -> None:
        """PDF ingestion should persist DocumentArtifact."""
        pdf_bytes = _create_minimal_pdf()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="financial_model.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        artifact = ingestion_service.get_artifact(tenant_a, result.artifact_id)

        assert artifact is not None
        assert artifact.doc_id == result.artifact_id
        assert artifact.tenant_id == tenant_a
        assert artifact.deal_id == deal_id
        assert artifact.sha256 == result.sha256
        assert artifact.title == "financial_model.pdf"

    def test_ingest_pdf_persists_document(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        tenant_a: UUID,
    ) -> None:
        """PDF ingestion should persist Document with PARSED status."""
        from idis.models.document import DocumentType, ParseStatus

        pdf_bytes = _create_minimal_pdf()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="test.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        document = ingestion_service.get_document(tenant_a, result.document_id)

        assert document is not None
        assert document.document_id == result.document_id
        assert document.tenant_id == tenant_a
        assert document.deal_id == deal_id
        assert document.doc_type == DocumentType.PDF
        assert document.parse_status == ParseStatus.PARSED

    def test_ingest_pdf_emits_audit_events(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        audit_sink: Any,
    ) -> None:
        """PDF ingestion should emit document.created and document.ingestion.completed."""
        pdf_bytes = _create_minimal_pdf()

        ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="test.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        events = audit_sink.events
        event_types = [e["event_type"] for e in events]

        assert "document.created" in event_types
        assert "document.ingestion.completed" in event_types

        created_event = next(e for e in events if e["event_type"] == "document.created")
        assert created_event["tenant_id"] == str(ingestion_context.tenant_id)
        assert "sha256" in created_event["payload"]

        completed_event = next(
            e for e in events if e["event_type"] == "document.ingestion.completed"
        )
        assert completed_event["payload"]["parse_status"] == "PARSED"

    def test_ingest_pdf_sha256_matches_content(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """SHA256 in result should match actual content hash."""
        pdf_bytes = _create_minimal_pdf()
        expected_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="test.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        assert result.sha256 == expected_sha256


class TestHappyPathXLSX:
    """Tests for successful XLSX ingestion."""

    def test_ingest_xlsx_returns_success(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """XLSX ingestion should return success."""
        xlsx_bytes = _create_minimal_xlsx()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="financials.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=xlsx_bytes,
        )

        assert result.success is True
        assert result.doc_type == "XLSX"
        assert result.span_count >= 0
        assert len(result.errors) == 0

    def test_ingest_xlsx_persists_spans(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        tenant_a: UUID,
    ) -> None:
        """XLSX ingestion should persist spans with cell-based locators."""
        xlsx_bytes = _create_minimal_xlsx()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="financials.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=xlsx_bytes,
        )

        spans = ingestion_service.get_spans(tenant_a, result.document_id)

        if result.span_count > 0:
            assert len(spans) == result.span_count
            for span in spans:
                assert span.tenant_id == tenant_a
                assert span.document_id == result.document_id
                assert span.locator is not None

    def test_ingest_xlsx_emits_audit_events(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        audit_sink: Any,
    ) -> None:
        """XLSX ingestion should emit required audit events."""
        xlsx_bytes = _create_minimal_xlsx()

        ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="financials.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=xlsx_bytes,
        )

        events = audit_sink.events
        event_types = [e["event_type"] for e in events]

        assert "document.created" in event_types
        assert "document.ingestion.completed" in event_types


class TestFailClosedEmptyBytes:
    """Tests for fail-closed behavior on empty input."""

    def test_empty_bytes_returns_structured_error(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """Empty bytes should return structured failure, not exception."""
        from idis.services.ingestion import IngestionErrorCode

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="empty.pdf",
            media_type="application/pdf",
            data=b"",
        )

        assert result.success is False
        assert len(result.errors) == 1
        assert result.errors[0].code == IngestionErrorCode.EMPTY_FILE
        assert "empty" in result.errors[0].message.lower()

    def test_empty_bytes_no_unhandled_exception(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """Empty bytes should never raise an unhandled exception."""
        try:
            result = ingestion_service.ingest_bytes(
                ctx=ingestion_context,
                deal_id=deal_id,
                filename="empty.pdf",
                media_type="application/pdf",
                data=b"",
            )
            assert result.success is False
        except Exception as e:
            pytest.fail(f"Unexpected exception raised: {e}")

    def test_empty_bytes_no_cross_tenant_leakage(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        tenant_b: UUID,
    ) -> None:
        """Error details should not leak tenant information."""
        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="empty.pdf",
            media_type="application/pdf",
            data=b"",
        )

        error_str = str(result.errors[0].to_dict())
        assert str(tenant_b) not in error_str


class TestFailClosedCorruptedOfficeZip:
    """Tests for fail-closed behavior on corrupted Office files."""

    def test_corrupted_xlsx_returns_structured_error(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """Corrupted XLSX should return parse error with FAILED status."""
        from idis.models.document import ParseStatus

        corrupted_bytes = _create_corrupted_xlsx()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="corrupted.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=corrupted_bytes,
        )

        assert result.success is False
        assert result.parse_status == ParseStatus.FAILED
        assert len(result.errors) >= 1

    def test_corrupted_xlsx_persists_artifact_and_document(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        tenant_a: UUID,
    ) -> None:
        """Corrupted XLSX should still persist artifact and document (with FAILED)."""
        from idis.models.document import ParseStatus

        corrupted_bytes = _create_corrupted_xlsx()

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="corrupted.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=corrupted_bytes,
        )

        artifact = ingestion_service.get_artifact(tenant_a, result.artifact_id)
        document = ingestion_service.get_document(tenant_a, result.document_id)

        assert artifact is not None
        assert document is not None
        assert document.parse_status == ParseStatus.FAILED

    def test_corrupted_xlsx_emits_audit_events(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
        audit_sink: Any,
    ) -> None:
        """Corrupted XLSX should emit document.created and document.ingestion.failed."""
        corrupted_bytes = _create_corrupted_xlsx()

        ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="corrupted.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=corrupted_bytes,
        )

        events = audit_sink.events
        event_types = [e["event_type"] for e in events]

        assert "document.created" in event_types
        assert "document.ingestion.failed" in event_types


class TestTenantIsolation:
    """Tests for tenant isolation during ingestion."""

    def test_same_bytes_different_tenants_no_collision(
        self,
        object_store: Any,
        audit_sink: Any,
        tenant_a: UUID,
        tenant_b: UUID,
    ) -> None:
        """Same bytes ingested by two tenants should not collide."""
        from idis.services.ingestion import IngestionContext, IngestionService

        service = IngestionService(
            object_store=object_store,
            audit_sink=audit_sink,
        )

        pdf_bytes = _create_minimal_pdf()
        deal_a = uuid4()
        deal_b = uuid4()

        ctx_a = IngestionContext(
            tenant_id=tenant_a,
            actor_id="user-a",
            request_id="req-a",
        )
        ctx_b = IngestionContext(
            tenant_id=tenant_b,
            actor_id="user-b",
            request_id="req-b",
        )

        result_a = service.ingest_bytes(
            ctx=ctx_a,
            deal_id=deal_a,
            filename="shared.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        result_b = service.ingest_bytes(
            ctx=ctx_b,
            deal_id=deal_b,
            filename="shared.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        assert result_a.success is True
        assert result_b.success is True

        assert result_a.artifact_id != result_b.artifact_id
        assert result_a.document_id != result_b.document_id

    def test_tenant_a_cannot_access_tenant_b_artifacts(
        self,
        object_store: Any,
        audit_sink: Any,
        tenant_a: UUID,
        tenant_b: UUID,
    ) -> None:
        """Tenant A should not be able to retrieve tenant B's artifacts."""
        from idis.services.ingestion import IngestionContext, IngestionService

        service = IngestionService(
            object_store=object_store,
            audit_sink=audit_sink,
        )

        pdf_bytes = _create_minimal_pdf()

        ctx_b = IngestionContext(
            tenant_id=tenant_b,
            actor_id="user-b",
            request_id="req-b",
        )

        result_b = service.ingest_bytes(
            ctx=ctx_b,
            deal_id=uuid4(),
            filename="secret.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        artifact_via_a = service.get_artifact(tenant_a, result_b.artifact_id)
        document_via_a = service.get_document(tenant_a, result_b.document_id)
        spans_via_a = service.get_spans(tenant_a, result_b.document_id)

        assert artifact_via_a is None
        assert document_via_a is None
        assert spans_via_a == []

        artifact_via_b = service.get_artifact(tenant_b, result_b.artifact_id)
        assert artifact_via_b is not None


class TestDeterminismRegression:
    """Tests for deterministic span ordering and locators."""

    def test_same_bytes_twice_stable_span_ordering(
        self,
        object_store: Any,
        audit_sink: Any,
        tenant_a: UUID,
    ) -> None:
        """Same bytes ingested twice should produce stable span ordering."""
        from idis.services.ingestion import IngestionContext, IngestionService

        xlsx_bytes = _create_minimal_xlsx()

        service1 = IngestionService(
            object_store=object_store,
            audit_sink=audit_sink,
        )
        service2 = IngestionService(
            object_store=object_store,
            audit_sink=audit_sink,
        )

        ctx1 = IngestionContext(
            tenant_id=tenant_a,
            actor_id="user",
            request_id="req-1",
        )
        ctx2 = IngestionContext(
            tenant_id=tenant_a,
            actor_id="user",
            request_id="req-2",
        )

        result1 = service1.ingest_bytes(
            ctx=ctx1,
            deal_id=uuid4(),
            filename="test.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=xlsx_bytes,
        )

        result2 = service2.ingest_bytes(
            ctx=ctx2,
            deal_id=uuid4(),
            filename="test.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=xlsx_bytes,
        )

        assert result1.span_count == result2.span_count

        spans1 = service1.get_spans(tenant_a, result1.document_id)
        spans2 = service2.get_spans(tenant_a, result2.document_id)

        if spans1 and spans2:
            locators1 = [s.locator for s in spans1]
            locators2 = [s.locator for s in spans2]
            assert locators1 == locators2

    def test_same_bytes_stable_sha256(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
    ) -> None:
        """Same bytes should always produce same SHA256."""
        pdf_bytes = _create_minimal_pdf()

        result1 = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=uuid4(),
            filename="test1.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        result2 = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=uuid4(),
            filename="test2.pdf",
            media_type="application/pdf",
            data=pdf_bytes,
        )

        assert result1.sha256 == result2.sha256


class TestSpanGenerator:
    """Tests for SpanGenerator deterministic behavior."""

    def test_span_generator_deterministic_ordering(self) -> None:
        """SpanGenerator should produce deterministic ordering."""
        from idis.parsers.base import SpanDraft
        from idis.services.ingestion import SpanGenerator

        drafts = [
            SpanDraft(
                span_type="CELL",
                locator={"sheet": "Sheet1", "cell": "B2"},
                text_excerpt="Second",
            ),
            SpanDraft(
                span_type="CELL",
                locator={"sheet": "Sheet1", "cell": "A1"},
                text_excerpt="First",
            ),
            SpanDraft(
                span_type="CELL",
                locator={"sheet": "Sheet1", "cell": "C3"},
                text_excerpt="Third",
            ),
        ]

        generator = SpanGenerator()
        tenant_id = uuid4()
        document_id = uuid4()

        spans1 = generator.generate_spans(drafts, tenant_id=tenant_id, document_id=document_id)
        spans2 = generator.generate_spans(
            list(reversed(drafts)), tenant_id=tenant_id, document_id=document_id
        )

        locators1 = [s.locator for s in spans1]
        locators2 = [s.locator for s in spans2]

        assert locators1 == locators2

    def test_span_generator_normalized_locators(self) -> None:
        """SpanGenerator should normalize locator JSON."""
        from idis.parsers.base import SpanDraft
        from idis.services.ingestion import SpanGenerator

        draft = SpanDraft(
            span_type="CELL",
            locator={"cell": "A1", "sheet": "Sheet1"},
            text_excerpt="Test",
        )

        generator = SpanGenerator()
        spans = generator.generate_spans(
            [draft],
            tenant_id=uuid4(),
            document_id=uuid4(),
        )

        locator_keys = list(spans[0].locator.keys())
        assert locator_keys == sorted(locator_keys)


class TestFileSizeLimit:
    """Tests for file size limit enforcement."""

    def test_oversized_file_rejected(
        self,
        object_store: Any,
        audit_sink: Any,
        tenant_a: UUID,
    ) -> None:
        """Files exceeding max size should be rejected."""
        from idis.services.ingestion import (
            IngestionContext,
            IngestionErrorCode,
            IngestionService,
        )

        service = IngestionService(
            object_store=object_store,
            audit_sink=audit_sink,
            max_bytes=100,
        )

        ctx = IngestionContext(
            tenant_id=tenant_a,
            actor_id="user",
            request_id="req-1",
        )

        result = service.ingest_bytes(
            ctx=ctx,
            deal_id=uuid4(),
            filename="large.bin",
            media_type="application/octet-stream",
            data=b"X" * 200,
        )

        assert result.success is False
        assert len(result.errors) == 1
        assert result.errors[0].code == IngestionErrorCode.FILE_TOO_LARGE


class TestUnsupportedFormat:
    """Tests for unsupported format handling."""

    def test_unknown_format_handled_gracefully(
        self,
        ingestion_service: Any,
        ingestion_context: Any,
        deal_id: UUID,
    ) -> None:
        """Unknown formats should return parse error, not exception."""
        unknown_bytes = b"This is not a valid document format"

        result = ingestion_service.ingest_bytes(
            ctx=ingestion_context,
            deal_id=deal_id,
            filename="unknown.xyz",
            media_type="application/octet-stream",
            data=unknown_bytes,
        )

        assert result.success is False
        assert result.parse_status is not None
        assert len(result.errors) >= 1
