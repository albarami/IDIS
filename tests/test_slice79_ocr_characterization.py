"""Slice79 Task 1 — characterization tests pinning CURRENT OCR/image behavior.

RED-as-discovery: these tests document existing behavior so later tasks change it
deliberately. They use the mocked OCR adapter boundary (deterministic, no real
tesseract/poppler) per decision R-F. **No production code is changed by Task 1.**

Each test maps to a numbered Task-1 question (see the Slice79 plan §2.3 / approval):
  1. Scanned PDF OCR -> spans + extraction-eligible/chunkable.
  2. Image OCR -> parser spans.
  3. Image OCR spans are persisted by ingestion (same unconditional path as PDF).
  4. Image OCR success is still downstream-blocked with explicit ``ocr_required``.
  5. Canonical ingestion path receives OCR config when ``IDIS_OCR_ENABLED=1``.
  6. PDF chunker handles OCR ``PAGE_TEXT`` spans with ``source: "ocr"``.

Safety: OCR output is document content — assertions verify no OCR text leaks into
result payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.models.extraction_task import ExtractionTaskBlockerReason
from idis.parsers.image import parse_image
from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.parsers.pdf import parse_pdf
from idis.services.documents.parser_capabilities import triage_document
from idis.services.extraction.chunking.service import ChunkingService
from idis.services.extraction.task_planner import (
    _READY_SUPPORT_STATUSES,
    _READY_TRIAGE_STATUSES,
    _SUPPORT_BLOCKERS,
)
from idis.services.ingestion import IngestionContext
from idis.services.ingestion.defaults import (
    build_default_ingestion_service,
    build_default_ocr_config,
)
from tests.test_pdf_ocr_adapter import (
    DEAL_ID,
    TENANT_ID,
    RecordingOcrAdapter,
    _create_image_only_pdf,
    _ingestion_service,
)

# Filename routes to parse_image (is_image_source); mock adapter ignores the bytes.
_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes-ignored-by-mock-adapter"
_DOC_ID = "11111111-1111-1111-1111-111111111111"


def _ocr_span(span_id: str, *, page: int, line: int, text: str) -> dict[str, Any]:
    return {
        "span_id": span_id,
        "span_type": "PAGE_TEXT",
        "locator": {"page": page, "line": line, "source": "ocr"},
        "text_excerpt": text,
        "content_hash": f"hash-{span_id}",
    }


def test_scanned_pdf_ocr_produces_spans_and_is_extraction_eligible() -> None:
    """1. Scanned PDF OCR -> PAGE_TEXT spans; triage -> PARTIALLY_SUPPORTED/PARTIAL (eligible)."""
    adapter = RecordingOcrAdapter(
        [OcrPageText(page_number=1, text="OCR revenue 10M\nOCR margin 40%")]
    )

    result = parse_pdf(
        _create_image_only_pdf(), ocr_config=OcrConfig(enabled=True, adapter=adapter)
    )

    assert result.success is True
    assert result.doc_type == "PDF"
    assert [s.span_type for s in result.spans] == ["PAGE_TEXT", "PAGE_TEXT"]
    assert all(s.locator.get("source") == "ocr" for s in result.spans)

    capability = triage_document(parse_result=result, filename="scan.pdf")
    assert capability.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED
    assert capability.triage_status == DocumentTriageStatus.PARTIAL
    # Consumed by preflight/task_planner ready-sets -> eligible; doc_type PDF is chunkable.
    assert capability.support_status in _READY_SUPPORT_STATUSES
    assert capability.triage_status in _READY_TRIAGE_STATUSES


def test_image_ocr_produces_parser_spans() -> None:
    """2. Image OCR -> PAGE_TEXT spans with image locators."""
    adapter = RecordingOcrAdapter(
        [OcrPageText(page_number=1, text="image line one\nimage line two")]
    )

    result = parse_image(_PNG_BYTES, ocr_config=OcrConfig(enabled=True, adapter=adapter))

    assert result.success is True
    assert result.doc_type == "IMAGE"
    assert [s.span_type for s in result.spans] == ["PAGE_TEXT", "PAGE_TEXT"]
    assert all(s.locator.get("source") == "ocr_image" for s in result.spans)
    assert result.metadata["ocr_performed"] is True


def test_image_ocr_spans_are_persisted_by_ingestion_like_pdf() -> None:
    """3. Ingestion builds/persists image OCR spans (gated on parse success, not triage)."""
    confidential = "INGESTED IMAGE OCR TEXT MUST NOT LEAK"
    service = _ingestion_service(
        ocr_config=OcrConfig(
            enabled=True,
            adapter=RecordingOcrAdapter([OcrPageText(page_number=1, text=confidential)]),
        )
    )
    ctx = IngestionContext(tenant_id=TENANT_ID, actor_id="test-user", request_id="req-img-ocr")

    result = service.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="scan.png",
        media_type="image/png",
        data=_PNG_BYTES,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    assert result.doc_type == "IMAGE"
    assert result.span_count == 1
    assert confidential not in str(result.to_dict())


def test_image_ocr_success_remains_downstream_blocked_ocr_required() -> None:
    """4. NARROW: image OCR succeeds at parser, but capability re-derives ocr_required."""
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="image text")])

    result = parse_image(_PNG_BYTES, ocr_config=OcrConfig(enabled=True, adapter=adapter))
    assert result.success is True  # parser produced spans...

    capability = triage_document(parse_result=result, filename="scan.png")
    # ...but capability is re-derived from the .png extension (ignores successful OCR).
    assert capability.support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    assert capability.triage_status == DocumentTriageStatus.OCR_REQUIRED
    assert capability.reason_codes == ["ocr_required"]
    assert capability.requires_ocr is True
    # Downstream consumes this -> blocked, NOT eligible.
    assert capability.support_status not in _READY_SUPPORT_STATUSES
    assert _SUPPORT_BLOCKERS[capability.support_status] == ExtractionTaskBlockerReason.OCR_REQUIRED


def test_canonical_ingestion_path_receives_ocr_config_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """5. Off by default; ``IDIS_OCR_ENABLED=1`` wires OCR into the canonical builder."""
    assert build_default_ocr_config(env={}) is None
    enabled_cfg = build_default_ocr_config(env={"IDIS_OCR_ENABLED": "1"})
    assert enabled_cfg is not None
    assert enabled_cfg.enabled is True
    assert enabled_cfg.adapter is not None

    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("IDIS_OCR_ENABLED", "1")
    service = build_default_ingestion_service()
    assert service._ocr_config is not None
    assert service._ocr_config.enabled is True


def test_pdf_chunker_handles_ocr_page_text_spans() -> None:
    """6. PdfChunker groups OCR PAGE_TEXT spans (source: "ocr") by page; provenance kept."""
    spans = [
        _ocr_span("s1", page=1, line=1, text="Revenue was 10M"),
        _ocr_span("s2", page=1, line=2, text="Margin was 40%"),
        _ocr_span("s3", page=2, line=1, text="Runway 18 months"),
    ]

    chunks = ChunkingService().chunk_spans(spans, document_id=_DOC_ID, doc_type="PDF")

    assert len(chunks) == 2  # one chunk per page (under the token limit)
    assert {c.doc_type for c in chunks} == {"PDF"}
    combined = " ".join(c.content for c in chunks)
    assert "Revenue was 10M" in combined
    assert "Runway 18 months" in combined
    all_span_ids = {span_id for c in chunks for span_id in c.span_ids}
    assert {"s1", "s2", "s3"}.issubset(all_span_ids)
