"""RED-first wiring tests for the signed no-substantive-content terminal path."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from idis.api.routes.documents import _document_summary_from_durable_row
from idis.audit.sink import InMemoryAuditSink
from idis.parsers import ocr as ocr_module
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.runtime.governance_envelopes import NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    VerifiedDocumentNoSubstantiveContentAttestation,
)
from idis.services.ingestion.service import IngestionContext, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEAL_ID = UUID("22222222-2222-2222-2222-222222222222")
SAFE_ALIAS = "company-002-document-00053.pdf"
SOURCE_TREE_FINGERPRINT = "a" * 64
DOCUMENT_BYTES = b"%PDF-1.4\nproject-owned-red-fixture\n%%EOF\n"
CONTENT_SHA256 = hashlib.sha256(DOCUMENT_BYTES).hexdigest()
POLICY_SHA256 = ocr_module.OCR_RESCUE_POLICY_SHA256
NOW = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)
_TEST_PAGE_RECEIPT = ocr_module._no_consensus_page_execution_receipt(
    page_number=1,
    candidate_receipt_sha256=hashlib.sha256(b"fictional-candidate-receipt").hexdigest(),
)
_TEST_FILE_PROOF = ocr_module._ocr_no_consensus_file_proof((_TEST_PAGE_RECEIPT,))
OCR_RESULT_SHA256 = _TEST_FILE_PROOF.ocr_result_sha256


def _verified_attestation(
    *,
    tenant_id: str = str(TENANT_ID),
    safe_alias: str = SAFE_ALIAS,
) -> VerifiedDocumentNoSubstantiveContentAttestation:
    return VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version=NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
        signer_id="slice100-reviewer",
        tenant_id=tenant_id,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=safe_alias,
        content_sha256=CONTENT_SHA256,
        disposition=DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        variant="watermark_only",
        attestation_text_sha256="e" * 64,
        reviewer_id="operator-reviewer",
        approved_at_text="2020-01-01T00:00:00Z",
        expires_at_text="2099-01-01T00:00:00Z",
        approved_at=datetime(2020, 1, 1, tzinfo=UTC),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        payload_sha256="f" * 64,
        envelope_sha256="1" * 64,
        public_key_sha256="2" * 64,
    )


def _complete_no_consensus_result() -> ParseResult:
    page_count = 1
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata={
            "page_count": page_count,
            "span_count": 0,
            "total_text_length": 0,
            "ocr_performed": True,
            "ocr_page_count": page_count,
            "ocr_required_page_count": page_count,
            "native_text_page_count": 0,
            "blank_page_count": page_count,
            "primary_completed_page_count": page_count,
            "rescue_completed_page_count": page_count,
            "rescue_candidate_attempt_count": page_count * 24,
            "rescue_candidate_completed_count": page_count * 24,
            "rescue_accepted_clique_page_count": 0,
            "accepted_text_page_count": 0,
            "parser_runtime": "tesseract",
            "parser_runtime_status": "completed_no_extractable_text",
            "ocr_rescue_policy_version": "pdf-ocr-rescue-v1",
            "ocr_rescue_policy_sha256": POLICY_SHA256,
            "ocr_no_consensus_page_receipts": [_TEST_PAGE_RECEIPT.to_payload()],
            "ocr_result_sha256": OCR_RESULT_SHA256,
        },
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
                message="Complete OCR rescue found no corroborated text",
                details={
                    "page_count": page_count,
                    "attempted_candidate_count": page_count * 24,
                    "completed_candidate_count": page_count * 24,
                    "no_consensus_page_count": page_count,
                },
            )
        ],
        warnings=[],
    )


def _service(
    tmp_path: Path,
    *,
    attestation: VerifiedDocumentNoSubstantiveContentAttestation | None,
) -> tuple[IngestionService, InMemoryAuditSink]:
    audit = InMemoryAuditSink()
    service = IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
        ),
        audit_sink=audit,
        no_substantive_content_attestation=attestation,
    )
    service._parse_document = lambda *_args, **_kwargs: _complete_no_consensus_result()  # type: ignore[method-assign]
    return service, audit


def _ingest(service: IngestionService):
    return service.ingest_bytes(
        IngestionContext(
            tenant_id=TENANT_ID,
            actor_id="slice100-operator",
            request_id="request-003",
        ),
        DEAL_ID,
        filename=SAFE_ALIAS,
        media_type="application/octet-stream",
        data=DOCUMENT_BYTES,
        metadata={
            "upload_filename": SAFE_ALIAS,
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        },
    )


def test_exact_attestation_persists_failed_zero_span_receipt_and_two_truthful_audits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: NOW)}),
    )
    service, audit = _service(tmp_path, attestation=_verified_attestation())

    result = _ingest(service)

    assert result.success is False
    assert result.terminal_disposition == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    assert result.parse_status is not None and result.parse_status.value == "FAILED"
    assert result.span_count == 0
    assert result.document_id is not None
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    receipt = document.metadata["document_no_substantive_content_receipt"]
    assert receipt["disposition"] == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    assert receipt["safe_alias"] == SAFE_ALIAS
    assert receipt["source_tree_fingerprint"] == SOURCE_TREE_FINGERPRINT
    assert receipt["content_sha256"] == CONTENT_SHA256
    assert receipt["span_count"] == 0
    assert service.get_spans(TENANT_ID, result.document_id) == []

    event_types = [event["event_type"] for event in audit.events]
    assert event_types == [
        "document.created",
        "document.ingestion.failed",
        "document.no_substantive_content.confirmed",
    ]
    assert "document.ingestion.completed" not in event_types


def test_no_attestation_cannot_promote_complete_no_consensus_failure(tmp_path: Path) -> None:
    service, audit = _service(tmp_path, attestation=None)

    result = _ingest(service)

    assert result.success is False
    assert result.terminal_disposition is None
    assert result.document_id is not None
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    assert "document_no_substantive_content_receipt" not in document.metadata
    assert [event["event_type"] for event in audit.events] == [
        "document.created",
        "document.ingestion.failed",
    ]


def test_mismatched_attestation_fails_before_any_document_or_audit_is_materialized(
    tmp_path: Path,
) -> None:
    service, audit = _service(
        tmp_path,
        attestation=_verified_attestation(safe_alias="company-002-document-00054.pdf"),
    )

    result = _ingest(service)

    assert result.success is False
    assert result.artifact_id is None
    assert result.document_id is None
    assert result.terminal_disposition is None
    assert result.errors[0].details == {
        "attestation_error_code": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH"
    }
    assert audit.events == []


def test_durable_summary_exposes_only_the_exact_safe_receipt_including_content_binding() -> None:
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "safe_alias": SAFE_ALIAS,
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "attestation_text_sha256": "e" * 64,
        "reviewer_id": "operator-reviewer",
        "approved_at": "2020-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "signer_id": "slice100-reviewer",
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
        "ocr_result_sha256": OCR_RESULT_SHA256,
    }
    row = {
        "doc_id": "33333333-3333-3333-3333-333333333333",
        "document_id": "44444444-4444-4444-4444-444444444444",
        "deal_id": str(DEAL_ID),
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "document_name": SAFE_ALIAS,
        "created_at": "2035-01-02T03:04:05Z",
        "parse_status": "FAILED",
        "sha256": CONTENT_SHA256,
        "uri": "tenant-safe/object.pdf",
        "source_metadata": {"source_system": "slice100", "raw_text": "drop-me"},
        "metadata": {
            "document_no_substantive_content_receipt": receipt,
            "content_sha256": "drop-outside-the-receipt",
        },
    }

    summary = _document_summary_from_durable_row(row)

    assert summary.terminal_disposition == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    assert summary.metadata == {"document_no_substantive_content_receipt": receipt}
    assert summary.source_metadata == {"source_system": "slice100"}


def test_malformed_receipt_cannot_create_a_public_terminal_disposition() -> None:
    receipt = {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "raw_text": "must-never-cross",
    }
    row = {
        "doc_id": "33333333-3333-3333-3333-333333333333",
        "document_id": "44444444-4444-4444-4444-444444444444",
        "deal_id": str(DEAL_ID),
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "parse_status": "FAILED",
        "metadata": {"document_no_substantive_content_receipt": receipt},
        "source_metadata": {},
    }

    summary = _document_summary_from_durable_row(row)

    assert summary.terminal_disposition is None
    assert summary.metadata == {}
