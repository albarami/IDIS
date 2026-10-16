"""Task 4 RED contract for evidence-backed readiness of successful parsers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
)
from idis.models.document_preflight import (
    DocumentPreflightReason,
    DocumentPreflightResult,
    DocumentPreflightStatus,
)
from idis.parsers.base import ParseLimits, ParseResult
from idis.parsers.xlsx import parse_xlsx
from idis.services.documents.parser_capabilities import triage_document
from idis.services.ingestion.service import IngestionService
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from tests.test_xlsx_parser import create_test_xlsx

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_DEAL_ID = "22222222-2222-2222-2222-222222222222"
_RUN_ID = "33333333-3333-3333-3333-333333333333"
_NATIVE_COMPLETION_REASONS = {
    "PDF": "pdf_parse_completed",
    "XLSX": "xlsx_parse_completed",
    "DOCX": "docx_parse_completed",
    "HTML": "text_parse_completed",
    "PPTX": "pptx_parse_completed",
    "TEXT": "text_parse_completed",
}


def _durable_native_document(
    doc_type: str,
    *,
    warning_codes: list[str] | None = None,
    error_codes: list[str] | None = None,
    declared_span_count: int = 1,
    actual_span_count: int = 1,
) -> dict[str, object]:
    document_id = f"document-{doc_type.lower()}"
    span_type = "CELL" if doc_type == "XLSX" else "PAGE_TEXT"
    spans = [
        {
            "span_id": f"span-{doc_type.lower()}-{index}",
            "tenant_id": _TENANT_ID,
            "deal_id": _DEAL_ID,
            "document_id": document_id,
            "span_type": span_type,
            "locator": {"ordinal": index},
            "text_excerpt": "fictional durable evidence",
            "content_hash": "a" * 64,
        }
        for index in range(actual_span_count)
    ]
    return {
        "tenant_id": _TENANT_ID,
        "deal_id": _DEAL_ID,
        "document_id": document_id,
        "doc_id": f"artifact-{doc_type.lower()}",
        "doc_type": doc_type,
        "parse_status": "PARSED" if not error_codes else "FAILED",
        "document_name": f"fictional.{doc_type.lower()}",
        "metadata": {
            "parser_doc_type": doc_type,
            "detected_format": doc_type,
            "parser_runtime_status": "completed" if not error_codes else "failed",
            "span_count": declared_span_count,
            "parse_warning_codes": list(warning_codes or []),
            "parse_error_codes": list(error_codes or []),
        },
        "source_metadata": {"size_bytes": 1024},
        "spans": spans,
    }


def _run_durable_preflight(
    document: dict[str, object],
) -> tuple[DocumentPreflightResult, list[dict[str, Any]]]:
    return InMemoryRunDocumentPreflightService().run(
        tenant_id=_TENANT_ID,
        deal_id=_DEAL_ID,
        run_id=_RUN_ID,
        corpus=[document],
    )


@pytest.mark.parametrize(
    ("doc_type", "filename", "reason"),
    [
        ("PDF", "fictional.pdf", "pdf_parse_completed"),
        ("XLSX", "fictional.xlsx", "xlsx_parse_completed"),
        ("DOCX", "fictional.docx", "docx_parse_completed"),
        ("PPTX", "fictional.pptx", "pptx_parse_completed"),
        ("HTML", "fictional.html", "text_parse_completed"),
        ("TEXT", "fictional.txt", "text_parse_completed"),
    ],
)
def test_nonempty_error_free_parse_is_ingestion_ready(
    doc_type: str,
    filename: str,
    reason: str,
) -> None:
    result = ParseResult(  # type: ignore[arg-type]
        doc_type=doc_type,
        success=True,
        metadata={"span_count": 1},
    )

    capability = triage_document(filename=filename, parse_result=result)

    assert capability.support_status == DocumentSupportStatus.SUPPORTED
    assert capability.triage_status == DocumentTriageStatus.READY
    assert capability.reason_codes == [reason]
    assert capability.usable_without_conversion is True


def test_success_flag_without_a_nonempty_span_proof_is_not_promoted() -> None:
    result = ParseResult(
        doc_type="PDF",
        success=True,
        metadata={"span_count": 0},
    )

    capability = triage_document(filename="fictional.pdf", parse_result=result)

    assert capability.support_status != DocumentSupportStatus.SUPPORTED
    assert capability.triage_status != DocumentTriageStatus.READY


@pytest.mark.parametrize("doc_type", ["PDF", "XLSX", "IMAGE", "MEDIA"])
def test_warning_bearing_parse_cannot_receive_a_completion_disposition(
    doc_type: str,
) -> None:
    metadata: dict[str, object] = {"span_count": 1}
    if doc_type == "IMAGE":
        metadata["ocr_performed"] = True
    if doc_type == "MEDIA":
        metadata["media_transcription_performed"] = True
    private_warning = "fictional-private-locator: incomplete extraction"
    result = ParseResult(  # type: ignore[arg-type]
        doc_type=doc_type,
        success=True,
        metadata=metadata,
        warnings=[private_warning],
    )

    capability = triage_document(filename="fictional-input", parse_result=result)
    service = IngestionService(compliant_store=object())  # type: ignore[arg-type]
    persisted = service._parse_metadata_for_persistence(  # noqa: SLF001
        result,
        document_id=uuid4(),
        artifact_id=uuid4(),
    )

    assert capability.support_status != DocumentSupportStatus.SUPPORTED
    assert capability.triage_status != DocumentTriageStatus.READY
    assert not any(code.endswith("_completed") for code in capability.reason_codes)
    assert persisted["parse_warning_codes"] == ["parser_incomplete_warning"]
    assert private_warning not in str(persisted)


def test_xlsx_truncation_warning_is_a_safe_code_without_sheet_name() -> None:
    private_sheet_name = "FICTIONAL_PRIVATE_SHEET_CANARY"
    data = create_test_xlsx({private_sheet_name: [["one", "two"]]})

    result = parse_xlsx(data, limits=ParseLimits(max_cells_per_sheet=1))

    assert result.success is True
    assert result.warnings == ["xlsx_sheet_cell_limit_truncated"]
    assert private_sheet_name not in str(result.warnings)


@pytest.mark.parametrize(
    ("doc_type", "completion_reason"),
    sorted(_NATIVE_COMPLETION_REASONS.items()),
)
def test_durable_native_success_reconstructs_exact_ready_evidence(
    doc_type: str,
    completion_reason: str,
) -> None:
    result, eligible_documents = _run_durable_preflight(_durable_native_document(doc_type))

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.COMPLETED
    assert decision.support_status == DocumentSupportStatus.SUPPORTED
    assert decision.triage_status == DocumentTriageStatus.READY
    assert decision.reason == DocumentPreflightReason.READY
    assert completion_reason in decision.reason_codes
    assert [row["document_id"] for row in eligible_documents] == [f"document-{doc_type.lower()}"]


@pytest.mark.parametrize(
    ("parser_doc_type", "persisted_doc_type", "success_flag", "completion_reason"),
    [
        ("IMAGE", "IMAGE", "ocr_performed", "image_ocr_completed"),
    ],
)
def test_durable_runtime_success_reconstructs_only_from_canonical_type_binding(
    parser_doc_type: str,
    persisted_doc_type: str,
    success_flag: str,
    completion_reason: str,
) -> None:
    document = _durable_native_document(parser_doc_type)
    document["doc_type"] = persisted_doc_type
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    metadata[success_flag] = True

    result, eligible_documents = _run_durable_preflight(document)

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.COMPLETED
    assert decision.triage_status == DocumentTriageStatus.READY
    assert completion_reason in decision.reason_codes
    assert len(eligible_documents) == 1


# Durable MEDIA success is intentionally covered by test_media_dual_track_preflight;
# the former one-boolean STT fixture is now a required fail-closed case.


@pytest.mark.parametrize(
    ("parser_doc_type", "wrong_persisted_doc_type", "success_flag"),
    [
        ("PDF", "HTML", None),
        ("XLSX", "PDF", None),
        ("DOCX", "PDF", None),
        ("PPTX", "PDF", None),
        ("HTML", "PDF", None),
        ("TEXT", "PDF", None),
        ("IMAGE", "PDF", "ocr_performed"),
        ("MEDIA", "PDF", "media_transcription_performed"),
    ],
)
def test_durable_parser_identity_mismatch_is_corrupted_and_ineligible(
    parser_doc_type: str,
    wrong_persisted_doc_type: str,
    success_flag: str | None,
) -> None:
    document = _durable_native_document(parser_doc_type)
    document["doc_type"] = wrong_persisted_doc_type
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    if success_flag is not None:
        metadata[success_flag] = True

    result, eligible_documents = _run_durable_preflight(document)

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize("doc_type", sorted(_NATIVE_COMPLETION_REASONS))
def test_durable_native_warning_never_falls_back_to_ready(doc_type: str) -> None:
    result, eligible_documents = _run_durable_preflight(
        _durable_native_document(
            doc_type,
            warning_codes=["parser_incomplete_warning"],
        )
    )

    decision = result.decisions[0]
    assert result.status != DocumentPreflightStatus.COMPLETED
    assert decision.triage_status != DocumentTriageStatus.READY
    assert _NATIVE_COMPLETION_REASONS[doc_type] not in decision.reason_codes
    assert eligible_documents == []


@pytest.mark.parametrize("doc_type", sorted(_NATIVE_COMPLETION_REASONS))
def test_durable_native_error_never_falls_back_to_ready(doc_type: str) -> None:
    result, eligible_documents = _run_durable_preflight(
        _durable_native_document(
            doc_type,
            error_codes=["corrupted_file"],
            declared_span_count=0,
            actual_span_count=0,
        )
    )

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize("doc_type", sorted(_NATIVE_COMPLETION_REASONS))
def test_unknown_durable_native_error_never_falls_back_to_ready(doc_type: str) -> None:
    result, eligible_documents = _run_durable_preflight(
        _durable_native_document(
            doc_type,
            error_codes=["unrecognized_persisted_parser_error"],
            declared_span_count=0,
            actual_span_count=0,
        )
    )

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize("doc_type", sorted(_NATIVE_COMPLETION_REASONS))
@pytest.mark.parametrize("missing_key", ["parse_error_codes", "parse_warning_codes"])
def test_durable_native_success_requires_explicit_empty_code_lists(
    doc_type: str,
    missing_key: str,
) -> None:
    document = _durable_native_document(doc_type)
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    metadata.pop(missing_key)

    result, eligible_documents = _run_durable_preflight(document)

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize("doc_type", sorted(_NATIVE_COMPLETION_REASONS))
def test_durable_native_span_count_mismatch_never_falls_back_to_ready(
    doc_type: str,
) -> None:
    result, eligible_documents = _run_durable_preflight(
        _durable_native_document(
            doc_type,
            declared_span_count=2,
            actual_span_count=1,
        )
    )

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize(
    ("doc_type", "success_flag"),
    [
        ("IMAGE", "ocr_performed"),
        ("MEDIA", "media_transcription_performed"),
    ],
)
def test_runtime_success_flag_cannot_override_failed_durable_status(
    doc_type: str,
    success_flag: str,
) -> None:
    document = _durable_native_document(doc_type)
    document["parse_status"] = "FAILED"
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    metadata["parser_runtime_status"] = "failed"
    metadata[success_flag] = True

    result, eligible_documents = _run_durable_preflight(document)

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


@pytest.mark.parametrize(
    ("parser_doc_type", "persisted_doc_type", "filename"),
    [
        ("PDF", "PDF", "fictional.pdf"),
        ("XLSX", "XLSX", "fictional.xlsx"),
        ("DOCX", "DOCX", "fictional.docx"),
        ("PPTX", "PPTX", "fictional.pptx"),
        ("HTML", "HTML", "fictional.html"),
        ("TEXT", "TEXT", "fictional.txt"),
        ("IMAGE", "IMAGE", "fictional.png"),
        ("MEDIA", "VIDEO", "fictional.mp4"),
    ],
)
def test_durable_row_without_any_parser_metadata_never_uses_filename_fallback(
    parser_doc_type: str,
    persisted_doc_type: str,
    filename: str,
) -> None:
    document = _durable_native_document(parser_doc_type)
    document["doc_type"] = persisted_doc_type
    document["document_name"] = filename
    document["metadata"] = {}

    result, eligible_documents = _run_durable_preflight(document)

    decision = result.decisions[0]
    assert result.status == DocumentPreflightStatus.FAILED
    assert decision.triage_status == DocumentTriageStatus.BLOCKED
    assert decision.reason == DocumentPreflightReason.CORRUPTED_SOURCE
    assert eligible_documents == []


def test_nonpersisted_capability_lookup_keeps_filename_behavior() -> None:
    capability = triage_document(filename="fictional.pdf", parse_result=None)

    assert capability.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED
    assert capability.triage_status == DocumentTriageStatus.PARTIAL
    assert capability.usable_without_conversion is True


def test_nonpersisted_orchestrator_document_keeps_legacy_span_inference() -> None:
    """Only rows without durable parse_status may use the legacy inference path."""
    document = {
        "document_id": "fictional-in-memory-document",
        "doc_type": "DOCX",
        "document_name": "fictional.docx",
        "spans": [
            {
                "span_id": "fictional-in-memory-span",
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "fictional evidence",
            }
        ],
    }

    result, eligible_documents = _run_durable_preflight(document)

    assert result.status == DocumentPreflightStatus.COMPLETED
    assert [row["document_id"] for row in eligible_documents] == ["fictional-in-memory-document"]
