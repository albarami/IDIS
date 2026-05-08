"""Tests for run document classification and parser triage preflight."""

from __future__ import annotations

import pytest

from idis.models.document_preflight import DocumentPreflightReason, DocumentPreflightStatus
from idis.parsers.base import ParseErrorCode
from idis.services.runs.document_preflight import (
    InMemoryRunDocumentPreflightService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _span(*, text: str = "Revenue was $5M.") -> dict[str, object]:
    return {
        "span_id": "span-1",
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "document_id": "doc-1",
        "span_type": "PAGE_TEXT",
        "locator": {"page": 1},
        "text_excerpt": text,
        "content_hash": "b" * 64,
    }


def _document(
    *,
    document_id: str = "doc-1",
    doc_type: str = "PDF",
    parse_status: str = "PARSED",
    metadata: dict[str, object] | None = None,
    spans: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "document_id": document_id,
        "doc_id": f"artifact-{document_id}",
        "doc_type": doc_type,
        "parse_status": parse_status,
        "document_name": f"{document_id.lower()}.{doc_type.lower()}",
        "sha256": "a" * 64,
        "uri": f"deals/{document_id.lower()}.{doc_type.lower()}",
        "metadata": metadata or {},
        "source_metadata": {},
        "spans": spans if spans is not None else [_span()],
    }


def test_ready_document_is_eligible_and_kept_for_extraction() -> None:
    service = InMemoryRunDocumentPreflightService()

    result, eligible_documents = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[_document(doc_type="DOCX", spans=[_span(text="ARR was $5M.")])],
    )

    assert result.status == DocumentPreflightStatus.COMPLETED
    assert result.eligible_document_ids == ["doc-1"]
    assert eligible_documents[0]["document_id"] == "doc-1"


@pytest.mark.parametrize(
    "error_code",
    [
        ParseErrorCode.MAX_SIZE_EXCEEDED,
        ParseErrorCode.MAX_PAGES_EXCEEDED,
        ParseErrorCode.MAX_SHEETS_EXCEEDED,
        ParseErrorCode.MAX_CELLS_EXCEEDED,
    ],
)
def test_parser_limit_errors_map_to_too_large(error_code: ParseErrorCode) -> None:
    service = InMemoryRunDocumentPreflightService()

    result, eligible_documents = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[
            _document(
                parse_status="FAILED",
                metadata={
                    "parse_error_codes": [error_code.value],
                    "parser_doc_type": "XLSX",
                    "detected_format": "XLSX",
                },
                spans=[],
            )
        ],
    )

    assert eligible_documents == []
    assert result.decisions[0].reason == DocumentPreflightReason.TOO_LARGE
    assert result.decisions[0].usable_for_methodology_extraction is False


@pytest.mark.parametrize(
    ("error_code", "expected_reason"),
    [
        (ParseErrorCode.ENCRYPTED_PDF, DocumentPreflightReason.ENCRYPTED_SOURCE),
        (ParseErrorCode.SCANNED_PDF_UNSUPPORTED, DocumentPreflightReason.OCR_REQUIRED),
        (ParseErrorCode.CORRUPTED_FILE, DocumentPreflightReason.CORRUPTED_SOURCE),
        (ParseErrorCode.UNSUPPORTED_FORMAT, DocumentPreflightReason.UNSUPPORTED_SOURCE),
    ],
)
def test_failed_documents_are_triaged_from_persisted_metadata(
    error_code: ParseErrorCode,
    expected_reason: DocumentPreflightReason,
) -> None:
    service = InMemoryRunDocumentPreflightService()

    result, eligible_documents = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[
            _document(
                parse_status="FAILED",
                metadata={
                    "parse_error_codes": [error_code.value],
                    "parser_doc_type": "PDF",
                    "detected_format": "PDF",
                },
                spans=[],
            )
        ],
    )

    assert eligible_documents == []
    assert result.decisions[0].reason == expected_reason


def test_run_step_summary_does_not_include_raw_span_text() -> None:
    service = InMemoryRunDocumentPreflightService()

    result, _eligible_documents = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[
            _document(
                doc_type="DOCX",
                spans=[_span(text="Highly sensitive raw revenue sentence")],
            )
        ],
    )

    summary = result.to_run_step_summary()

    assert "Highly sensitive raw revenue sentence" not in str(summary)
    assert "text_excerpt" not in str(summary)
    assert summary["source_spans_by_document_id"]["doc-1"][0]["content_hash"] == "b" * 64
