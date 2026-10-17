"""Durable ingestion boundary for the page-level image-only contract."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult, SpanDraft
from idis.services.ingestion.image_only_page import ImageOnlyPageContractError
from idis.services.ingestion.service import IngestionContext, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from idis.storage.models import StoredObjectMetadata
from tests.slice100_task4.test_image_only_page_red import (
    CONTENT_SHA256,
    _expected_evaluation,
    _receipt,
)

TENANT_ID = UUID("81000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("82000000-0000-4000-8000-000000000001")


def _service(tmp_path: Path) -> IngestionService:
    return IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
        ),
        audit_sink=InMemoryAuditSink(),
    )


def _materialize(
    service: IngestionService,
    parse_result: ParseResult,
):
    ctx = IngestionContext(
        tenant_id=TENANT_ID,
        actor_id="fictional-image-only-worker",
        request_id="fictional-image-only-request",
    )
    storage_key = "tenant-safe/fictional-image-only.pdf"
    stored_object = service._store_raw_bytes(
        ctx=ctx,
        storage_key=storage_key,
        data=b"fictional-parent-pdf",
        content_type="application/pdf",
    )
    assert isinstance(stored_object, StoredObjectMetadata)
    return service._materialize_parse_result(
        ctx=ctx,
        deal_id=DEAL_ID,
        filename="company-002-document-00105.pdf",
        metadata={"upload_filename": "company-002-document-00105.pdf"},
        sha256=CONTENT_SHA256,
        storage_key=storage_key,
        parse_result=parse_result,
        stored_object=stored_object,
        parser_started_at=datetime(2026, 10, 17, 0, 0, tzinfo=UTC),
        parser_finished_at=datetime(2026, 10, 17, 0, 0, 1, tzinfo=UTC),
        phase_recorder=None,
        db_conn=None,
    )


def _mixed_result() -> ParseResult:
    receipts = [_receipt(page_number=2, occurrence_scope="REVIEWER_CONFIRMED")]
    evaluation = _expected_evaluation(
        receipts,
        page_count=2,
        substantive_span_count=1,
    )
    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=[
            SpanDraft(
                span_type="PAGE_TEXT",
                locator={"page": 1, "source": "native_text"},
                text_excerpt="Fictional substantive evidence.",
            )
        ],
        metadata={
            "page_count": 2,
            "image_only_page_receipts": receipts,
            "image_only_page_evaluation": evaluation,
        },
    )


def test_mixed_image_only_page_metadata_is_projected_and_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = _materialize(service, _mixed_result())

    assert result.success is True
    assert result.span_count == 1
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    receipts = document.metadata["image_only_page_receipts"]
    evaluation = document.metadata["image_only_page_evaluation"]
    assert receipts[0]["classification"] == "image_only_page"
    assert receipts[0]["emitted_span_count"] == 0
    assert evaluation["parse_status"] == "PARSED"
    assert evaluation["substantive_span_count"] == 1
    assert not ({"text", "text_excerpt", "spans", "raw_content"} & set(receipts[0]))


def test_all_image_only_document_remains_failed_and_persists_review_blocker(
    tmp_path: Path,
) -> None:
    receipts = [
        _receipt(page_number=1, occurrence_scope="REVIEWER_CONFIRMED"),
        _receipt(page_number=2, occurrence_scope="REVIEWER_CONFIRMED"),
    ]
    parse_result = ParseResult(
        doc_type="PDF",
        success=False,
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                message="No substantive text was extracted",
                details={"terminal_code": "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"},
            )
        ],
        metadata={
            "page_count": 2,
            "image_only_page_receipts": receipts,
            "image_only_page_evaluation": _expected_evaluation(
                receipts,
                page_count=2,
                substantive_span_count=0,
            ),
        },
    )
    service = _service(tmp_path)

    result = _materialize(service, parse_result)

    assert result.success is False
    assert result.terminal_disposition is None
    assert result.span_count == 0
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    assert document.metadata["image_only_page_evaluation"]["terminal_code"] == (
        "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
    )


def test_forged_image_only_span_count_fails_before_durable_materialization(
    tmp_path: Path,
) -> None:
    parse_result = _mixed_result()
    parse_result.metadata = copy.deepcopy(parse_result.metadata)
    parse_result.metadata["image_only_page_evaluation"]["substantive_span_count"] = 2
    service = _service(tmp_path)

    with pytest.raises(ImageOnlyPageContractError):
        _materialize(service, parse_result)

    assert service._documents == {}
    assert service._spans == {}
