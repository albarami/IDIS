"""Tests for the config-gated PDF OCR adapter boundary."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.document import ParseStatus
from idis.parsers.base import ParseErrorCode
from idis.parsers.ocr import OcrConfig, OcrPageText, OcrTimeoutError
from idis.parsers.pdf import parse_pdf
from idis.parsers.registry import parse_bytes
from idis.services.documents.parser_capabilities import triage_document
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.ingestion.service import UploadIngestionPhaseRecorder
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEAL_ID = UUID("33333333-3333-3333-3333-333333333333")


class RecordingOcrAdapter:
    """Test OCR adapter that records calls and returns configured pages."""

    def __init__(
        self, pages: list[OcrPageText] | None = None, error: Exception | None = None
    ) -> None:
        self.pages = pages or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        self.calls.append(
            {
                "byte_count": len(data),
                "max_pages": max_pages,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.pages

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        self.calls.append(
            {
                "byte_count": len(data),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.pages


class AdapterSpecificFailure(Exception):
    """Adapter-specific test failure that must not escape parsing."""


def test_ocr_disabled_keeps_no_text_pdf_as_no_text_extracted() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="OCR SHOULD NOT RUN")])

    result = parse_pdf(
        _create_image_only_pdf(),
        ocr_config=OcrConfig(enabled=False, adapter=adapter),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.NO_TEXT_EXTRACTED]
    assert adapter.calls == []


def test_ocr_enabled_adapter_success_creates_deterministic_page_text_spans() -> None:
    adapter = RecordingOcrAdapter(
        [
            OcrPageText(page_number=1, text="OCR revenue was 10M\nOCR margin was 40%"),
            OcrPageText(page_number=2, text="OCR cash runway was 18 months"),
        ]
    )

    first = parse_pdf(
        _create_image_only_pdf(num_pages=2),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=3, timeout_seconds=2.5),
    )
    second = parse_pdf(
        _create_image_only_pdf(num_pages=2),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=3, timeout_seconds=2.5),
    )

    assert first.success is True
    assert first.errors == []
    assert first.metadata["ocr_performed"] is True
    assert first.metadata["ocr_page_count"] == 2
    assert "pdf_diagnostic_reason" not in first.metadata
    assert first.private_diagnostics["pdf_diagnostic_reason"] == "parsed_ocr"
    public_result = json.dumps(first.to_dict(), sort_keys=True)
    for forbidden in (
        "private_diagnostics",
        "pdf_diagnostic_reason",
        "parsed_text",
        "parsed_empty_password_encrypted",
        "parsed_ocr",
    ):
        assert forbidden not in public_result
    assert [span.span_type for span in first.spans] == ["PAGE_TEXT", "PAGE_TEXT", "PAGE_TEXT"]
    assert [span.locator for span in first.spans] == [
        {"page": 1, "line": 1, "source": "ocr"},
        {"page": 1, "line": 2, "source": "ocr"},
        {"page": 2, "line": 1, "source": "ocr"},
    ]
    assert [span.content_hash for span in first.spans] == [
        span.content_hash for span in second.spans
    ]
    assert adapter.calls[0] == {
        "byte_count": len(_create_image_only_pdf(num_pages=2)),
        "max_pages": 3,
        "timeout_seconds": 2.5,
    }


def test_ocr_enabled_without_adapter_fails_safely() -> None:
    result = parse_pdf(
        _create_image_only_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=None),
    )

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_UNAVAILABLE]
    assert result.errors[0].details == {}
    capability = triage_document(parse_result=result)
    assert capability.requires_ocr is True
    assert capability.reason_codes == ["ocr_unavailable"]


def test_ocr_timeout_is_structured_and_does_not_leak_content() -> None:
    confidential_marker = "CONFIDENTIAL OCR TEXT SHOULD NOT LEAK"
    adapter = RecordingOcrAdapter(error=OcrTimeoutError(confidential_marker))

    result = parse_pdf(
        _create_image_only_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, timeout_seconds=0.01),
    )
    encoded = str(result.to_dict())

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_TIMEOUT]
    assert result.errors[0].details == {}
    assert confidential_marker not in encoded
    capability = triage_document(parse_result=result)
    assert capability.requires_ocr is True
    assert capability.reason_codes == ["ocr_timeout"]


def test_ocr_unexpected_adapter_failure_is_structured_and_safe() -> None:
    confidential_marker = "UNEXPECTED OCR FAILURE SHOULD NOT LEAK"
    adapter = RecordingOcrAdapter(error=AdapterSpecificFailure(confidential_marker))

    result = parse_pdf(
        _create_image_only_pdf(),
        ocr_config=OcrConfig(enabled=True, adapter=adapter),
    )
    encoded = str(result.to_dict())

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]
    assert result.errors[0].details == {}
    assert confidential_marker not in encoded


def test_ocr_adapter_page_results_are_validated_before_span_creation() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=2, text="Out of bounds OCR text")])

    result = parse_pdf(
        _create_image_only_pdf(num_pages=1),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]
    assert result.errors[0].details == {}


def test_ocr_adapter_page_number_must_be_within_configured_page_window() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=2, text="Outside OCR page window")])

    result = parse_pdf(
        _create_image_only_pdf(num_pages=3),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]
    assert result.errors[0].details == {}


def test_existing_text_and_locked_pdf_behavior_do_not_invoke_ocr() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="unused")])
    locked_value = "locked-fixture-value"

    text_result = parse_pdf(
        _create_text_pdf(["Revenue text already exists"]),
        ocr_config=OcrConfig(enabled=True, adapter=adapter),
    )
    locked_result = parse_pdf(
        _create_encrypted_pdf(["Locked text"], locked_value=locked_value),
        ocr_config=OcrConfig(enabled=True, adapter=adapter),
    )

    assert text_result.success is True
    assert [error.code for error in locked_result.errors] == [ParseErrorCode.ENCRYPTED_PDF]
    assert adapter.calls == []


def test_parse_bytes_accepts_explicit_ocr_config() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="Registry OCR text")])

    result = parse_bytes(
        _create_image_only_pdf(),
        filename="scan.pdf",
        ocr_config=OcrConfig(enabled=True, adapter=adapter),
    )

    assert result.success is True
    assert [span.text_excerpt for span in result.spans] == ["Registry OCR text"]


def test_ingestion_ocr_opt_in_creates_spans_without_changing_default_behavior() -> None:
    disabled = _ingestion_service()
    enabled = _ingestion_service(
        ocr_config=OcrConfig(
            enabled=True,
            adapter=RecordingOcrAdapter([OcrPageText(page_number=1, text="Ingested OCR text")]),
        )
    )
    ctx = IngestionContext(tenant_id=TENANT_ID, actor_id="test-user", request_id="req-ocr")
    pdf_bytes = _create_image_only_pdf()

    disabled_result = disabled.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="scan.pdf",
        media_type="application/pdf",
        data=pdf_bytes,
    )
    enabled_result = enabled.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="scan.pdf",
        media_type="application/pdf",
        data=pdf_bytes,
    )

    assert disabled_result.success is False
    assert disabled_result.parse_status == ParseStatus.FAILED
    assert enabled_result.success is True
    assert enabled_result.parse_status == ParseStatus.PARSED
    assert enabled_result.span_count == 1
    assert enabled_result.to_dict()["span_count"] == 1
    assert "Ingested OCR text" not in str(enabled_result.to_dict())


def test_ingestion_records_pdf_ocr_no_text_reason_without_content_leakage() -> None:
    service = _ingestion_service(
        ocr_config=OcrConfig(
            enabled=True,
            adapter=RecordingOcrAdapter([]),
        )
    )
    recorder = UploadIngestionPhaseRecorder()
    ctx = IngestionContext(tenant_id=TENANT_ID, actor_id="test-user", request_id="req-ocr-empty")

    result = service.ingest_bytes(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="private-empty-ocr-scan.pdf",
        media_type="application/pdf",
        data=_create_image_only_pdf(),
        phase_recorder=recorder,
    )

    assert result.success is False
    assert result.parse_status == ParseStatus.FAILED
    assert result.errors[0].code.value == "parse_failed"
    pdf_diagnostics = recorder.to_summary()["parser_diagnostics"]["pdf_diagnostics"]
    assert pdf_diagnostics["counts_by_outcome_reason"] == {"failed_ocr_no_text": 1}
    assert pdf_diagnostics["parse_elapsed_by_outcome_reason"] == {
        "failed_ocr_no_text": {"under_1s": 1}
    }

    encoded = str(pdf_diagnostics)
    assert "private-empty-ocr-scan" not in encoded
    assert "Ingested OCR text" not in encoded
    assert "PAGE_TEXT" not in encoded


def _ingestion_service(*, ocr_config: OcrConfig | None = None) -> IngestionService:
    temp_dir = tempfile.TemporaryDirectory(prefix="idis_test_ocr_")
    store = FilesystemObjectStore(base_dir=Path(temp_dir.name))
    service = IngestionService(
        compliant_store=ComplianceEnforcedStore(inner_store=store),
        audit_sink=InMemoryAuditSink(),
        ocr_config=ocr_config,
    )
    service._slice32_temp_dir = temp_dir  # type: ignore[attr-defined]
    return service


def _create_image_only_pdf(*, num_pages: int = 1) -> bytes:
    if not REPORTLAB_AVAILABLE:
        pytest.skip("reportlab not installed")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    for page_index in range(num_pages):
        c.rect(72, 650, 144, 72, stroke=1, fill=0)
        if page_index < num_pages - 1:
            c.showPage()
    c.save()
    return buffer.getvalue()


def _create_text_pdf(lines: list[str]) -> bytes:
    if not REPORTLAB_AVAILABLE:
        pytest.skip("reportlab not installed")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    y_position = 750
    for line in lines:
        c.drawString(72, y_position, line)
        y_position -= 14
    c.save()
    return buffer.getvalue()


def _create_encrypted_pdf(text_lines: list[str], *, locked_value: str) -> bytes:
    reader = PdfReader(io.BytesIO(_create_text_pdf(text_lines)))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    owner_value = "slice32-owner-value"
    writer.encrypt(user_password=locked_value, owner_password=owner_value)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
