"""RED contract: signed no-substantive-content rows are never parsed evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from idis.models.document_preflight import DocumentPreflightStatus
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from tests.test_run_document_preflight_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    _document,
    _span,
)


def _receipt() -> dict[str, Any]:
    return {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "safe_alias": "company-001-document-00002.pdf",
        "content_sha256": "c" * 64,
        "source_tree_fingerprint": "d" * 64,
        "attestation_text_sha256": "e" * 64,
        "reviewer_id": "reviewer-fictional-001",
        "approved_at": "2026-07-17T00:00:00Z",
        "expires_at": "2026-07-18T00:00:00Z",
        "signer_id": "fictional-governance-signer",
        "payload_sha256": "f" * 64,
        "envelope_sha256": "1" * 64,
        "public_key_sha256": "2" * 64,
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 1,
        "ocr_required_page_count": 1,
        "primary_completed_page_count": 1,
        "rescue_completed_page_count": 1,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": "3" * 64,
    }


def _confirmed_document() -> dict[str, object]:
    return _document(
        document_id="doc-confirmed-illegible",
        parse_status="FAILED",
        metadata={
            "parser_doc_type": "PDF",
            "detected_format": "PDF",
            "parser_runtime": "tesseract",
            "parser_runtime_status": "completed_no_extractable_text",
            "span_count": 0,
            "parse_error_codes": ["ocr_rescue_no_consensus"],
            "parse_warning_codes": [],
            "document_no_substantive_content_receipt": _receipt(),
        },
        spans=[],
    )


def _ready_document() -> dict[str, object]:
    return _document(
        document_id="doc-ready",
        doc_type="DOCX",
        metadata={
            "parser_doc_type": "DOCX",
            "detected_format": "DOCX",
            "parser_runtime_status": "completed",
            "span_count": 1,
            "parse_error_codes": [],
            "parse_warning_codes": [],
        },
        spans=[
            {
                **_span(text="Fictional recurring revenue evidence"),
                "document_id": "doc-ready",
            }
        ],
    )


def test_current_confirmed_no_substantive_content_receipt_is_nonblocking_but_nonconsumable() -> (
    None
):
    verified: list[str] = []

    def verify(document: Mapping[str, Any]) -> bool:
        verified.append(str(document["document_id"]))
        return True

    service = InMemoryRunDocumentPreflightService(
        no_substantive_content_receipt_verifier=verify,
    )

    result, eligible = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[_ready_document(), _confirmed_document()],
    )

    assert verified == ["doc-confirmed-illegible"]
    assert result.status == DocumentPreflightStatus.COMPLETED
    assert [row["document_id"] for row in eligible] == ["doc-ready"]
    assert result.eligible_document_ids == ["doc-ready"]
    assert result.excluded_confirmed_document_ids == ["doc-confirmed-illegible"]
    assert result.blocked_document_ids == []
    confirmed = result.decisions[1]
    assert confirmed.reason.value == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert confirmed.support_status.value == "excluded_confirmed"
    assert confirmed.triage_status.value == "excluded_confirmed"
    assert confirmed.usable_for_methodology_extraction is False
    assert confirmed.source_spans == []
    assert result.summary is not None
    assert result.summary.total_documents == 2
    assert result.summary.usable_documents == 1
    assert result.summary.excluded_confirmed_documents == 1
    assert result.summary.blocked_documents == 0


def test_unverifiable_receipt_remains_a_blocker() -> None:
    service = InMemoryRunDocumentPreflightService(
        no_substantive_content_receipt_verifier=lambda _document: False,
    )

    result, eligible = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        corpus=[_ready_document(), _confirmed_document()],
    )

    assert [row["document_id"] for row in eligible] == ["doc-ready"]
    # Existing mixed-corpus semantics classify one ready plus one blocker as PARTIAL;
    # PARTIAL remains a hard semantic run failure and is not an accepted exclusion.
    assert result.status == DocumentPreflightStatus.PARTIAL
    assert result.excluded_confirmed_document_ids == []
    assert result.blocked_document_ids == ["doc-confirmed-illegible"]
