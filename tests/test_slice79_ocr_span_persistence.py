"""Slice79 Task 5 — durable OCR span persistence (Q1 confirm/repair).

Proves scanned-PDF and image OCR spans are durably persisted through the ingestion
path via the documents repository abstraction (recording repo), that persisted
metadata carries safe OCR diagnostics/confidence (no raw OCR text), and that image
OCR spans persist even though the image is not extraction-eligible (NARROW: durable
spans, not image-to-claims).

True Postgres row proof is a CI/postgres-integration follow-up; this uses the local
documents-repository abstraction that ``_persist_spans`` drives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from idis.audit.sink import InMemoryAuditSink
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus
from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.services.ingestion import IngestionContext, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_pdf_ocr_adapter import RecordingOcrAdapter, _create_image_only_pdf

_TENANT = UUID("11111111-1111-4111-8111-111111111111")
_DEAL = UUID("33333333-3333-4333-8333-333333333333")
_CTX = IngestionContext(tenant_id=_TENANT, actor_id="tester", request_id="req-slice79-t5")
_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes-ignored-by-mock-adapter"


class _RecordingDocumentsRepo:
    """Records repository persistence calls (stand-in for Postgres documents repo)."""

    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []

    def create_artifact(self, **kwargs: Any) -> None:
        self.artifacts.append(kwargs)

    def create_document(self, **kwargs: Any) -> None:
        self.documents.append(kwargs)

    def create_document_span(self, **kwargs: Any) -> None:
        self.spans.append(kwargs)


class _RecordingRepoIngestionService(IngestionService):
    """IngestionService whose persistence always routes through a recording repo."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.recording_repo = _RecordingDocumentsRepo()

    def _documents_repo(self, tenant_id: UUID, *, db_conn: Any | None = None) -> Any:
        return self.recording_repo


def _service(
    tmp_path: Path, *, ocr_config: OcrConfig
) -> tuple[_RecordingRepoIngestionService, InMemoryAuditSink]:
    store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    audit = InMemoryAuditSink()
    service = _RecordingRepoIngestionService(
        compliant_store=ComplianceEnforcedStore(inner_store=store),
        audit_sink=audit,
        ocr_config=ocr_config,
    )
    return service, audit


def test_scanned_pdf_ocr_persists_page_text_spans(tmp_path: Path) -> None:
    adapter = RecordingOcrAdapter(
        [
            OcrPageText(page_number=1, text="Revenue 10M", confidence=0.9),
            OcrPageText(page_number=2, text="Margin 40%", confidence=0.8),
        ]
    )
    service, _ = _service(
        tmp_path, ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=3)
    )

    result = service.ingest_bytes(
        ctx=_CTX,
        deal_id=_DEAL,
        filename="scan.pdf",
        media_type="application/pdf",
        data=_create_image_only_pdf(num_pages=2),
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    spans = service.recording_repo.spans
    assert spans, "scanned-PDF OCR spans must be persisted via the documents repository"
    assert all(row["span_type"] == "PAGE_TEXT" for row in spans)
    assert all(row["locator"]["source"] == "ocr" for row in spans)
    assert {row["locator"]["page"] for row in spans} == {1, 2}


def test_image_ocr_persists_page_text_spans(tmp_path: Path) -> None:
    adapter = RecordingOcrAdapter(
        [OcrPageText(page_number=1, text="scanned image line", confidence=0.7)]
    )
    service, _ = _service(tmp_path, ocr_config=OcrConfig(enabled=True, adapter=adapter))

    result = service.ingest_bytes(
        ctx=_CTX,
        deal_id=_DEAL,
        filename="scan.png",
        media_type="image/png",
        data=_PNG_BYTES,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    spans = service.recording_repo.spans
    assert spans, "image OCR spans must be persisted via the documents repository"
    assert all(row["span_type"] == "PAGE_TEXT" for row in spans)
    assert all(row["locator"]["source"] == "ocr_image" for row in spans)


def test_persisted_ocr_metadata_is_safe_and_includes_confidence(tmp_path: Path) -> None:
    confidential = "CONFIDENTIAL_PERSISTED_OCR_TEXT_MARKER"
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text=confidential, confidence=0.91)])
    service, _ = _service(
        tmp_path, ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=2)
    )

    result = service.ingest_bytes(
        ctx=_CTX,
        deal_id=_DEAL,
        filename="scan.pdf",
        media_type="application/pdf",
        data=_create_image_only_pdf(num_pages=1),
    )

    assert result.success is True
    metadata = service.recording_repo.documents[0]["metadata"]
    assert metadata["ocr_performed"] is True
    assert metadata["ocr_page_count"] == 1
    assert metadata["ocr_mean_confidence"] == 0.91
    assert metadata["parser_mode"] == "ocr"
    # Persisted metadata is text-free; raw OCR text lives only in span text_excerpt.
    assert confidential not in json.dumps(metadata, default=str)


def test_image_ocr_spans_persist_even_though_image_not_extraction_eligible(tmp_path: Path) -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="image text", confidence=0.6)])
    service, _ = _service(tmp_path, ocr_config=OcrConfig(enabled=True, adapter=adapter))

    result = service.ingest_bytes(
        ctx=_CTX,
        deal_id=_DEAL,
        filename="scan.png",
        media_type="image/png",
        data=_PNG_BYTES,
    )

    assert result.success is True
    metadata = service.recording_repo.documents[0]["metadata"]
    # OCR ran, but the image is NOT extraction-eligible (NARROW: durable spans, not claims).
    assert metadata["parser_mode"] == "ocr"
    assert metadata["parser_support_status"] not in {
        DocumentSupportStatus.SUPPORTED.value,
        DocumentSupportStatus.PARTIALLY_SUPPORTED.value,
    }
    # ...yet the OCR spans are still durably persisted.
    spans = service.recording_repo.spans
    assert spans
    assert all(row["locator"]["source"] == "ocr_image" for row in spans)


def test_ocr_text_only_persists_in_spans_not_in_summaries(tmp_path: Path) -> None:
    confidential = "CONFIDENTIAL_LEAK_PROBE_OCR_TEXT_MARKER"
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text=confidential, confidence=0.9)])
    service, audit = _service(
        tmp_path, ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=2)
    )

    result = service.ingest_bytes(
        ctx=_CTX,
        deal_id=_DEAL,
        filename="scan.pdf",
        media_type="application/pdf",
        data=_create_image_only_pdf(num_pages=1),
    )

    assert result.success is True
    # Intended: persisted span text_excerpt carries the OCR text (durable provenance).
    assert any(confidential in row["text_excerpt"] for row in service.recording_repo.spans)
    # Not leaked anywhere else: result, persisted document metadata, or audit events.
    assert confidential not in json.dumps(result.to_dict(), default=str)
    assert confidential not in json.dumps(service.recording_repo.documents, default=str)
    assert confidential not in json.dumps(audit.events, default=str)
