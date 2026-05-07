"""Tests for in-memory document classification service."""

from __future__ import annotations

from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.services.documents.classification_service import InMemoryDocumentClassificationService
from idis.services.documents.classifier import DocumentDescriptor

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


def _descriptor(document_id: str, filename: str) -> DocumentDescriptor:
    return DocumentDescriptor(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=document_id,
        filename=filename,
        file_size_bytes=1024,
    )


def test_classify_single_document() -> None:
    service = InMemoryDocumentClassificationService()

    result = service.classify_one(_descriptor("doc-1", "synthetic_financial_model.xlsx"))

    assert result.fdd_category == FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL
    assert result.tenant_id == TENANT_ID
    assert result.deal_id == DEAL_ID


def test_classify_batch() -> None:
    service = InMemoryDocumentClassificationService()

    results = service.classify_batch(
        [
            _descriptor("doc-1", "synthetic_financial_model.xlsx"),
            _descriptor("doc-2", "synthetic_market_research.pdf"),
        ]
    )

    assert len(results) == 2
    assert {result.document_id for result in results} == {"doc-1", "doc-2"}


def test_summary_by_fdd_and_cdd_category() -> None:
    service = InMemoryDocumentClassificationService()
    service.classify_batch(
        [
            _descriptor("doc-1", "synthetic_pl_schedule.xlsx"),
            _descriptor("doc-2", "synthetic_market_research.pdf"),
        ]
    )

    summary = service.summarize(tenant_id=TENANT_ID, deal_id=DEAL_ID)

    assert summary.by_fdd_category[FddDocumentCategory.PL_SUPPORT.value] == 1
    assert summary.by_cdd_category[CddDocumentCategory.MARKET_RESEARCH.value] == 1


def test_summary_by_parser_and_triage_status() -> None:
    service = InMemoryDocumentClassificationService()
    service.classify_batch(
        [
            _descriptor("doc-1", "synthetic_video.mp4"),
            _descriptor("doc-2", "synthetic_market_research.pdf"),
        ]
    )

    summary = service.summarize(tenant_id=TENANT_ID, deal_id=DEAL_ID)

    assert summary.by_support_status[DocumentSupportStatus.CONVERSION_REQUIRED.value] == 1
    assert summary.by_triage_status[DocumentTriageStatus.CONVERSION_REQUIRED.value] == 1


def test_blocker_summary() -> None:
    service = InMemoryDocumentClassificationService()
    service.classify_batch(
        [
            _descriptor("doc-1", "synthetic_video.mp4"),
            _descriptor("doc-2", "synthetic_notes.one"),
            _descriptor("doc-3", "synthetic_financial_model.xlsx"),
        ]
    )

    blockers = service.blocker_summary(tenant_id=TENANT_ID, deal_id=DEAL_ID)

    assert blockers.total_blocked == 2
    assert blockers.by_reason_code["conversion_required"] == 2


def test_results_are_deterministic() -> None:
    service = InMemoryDocumentClassificationService()
    descriptor = _descriptor("doc-1", "synthetic_financial_model.xlsx")

    first = service.classify_one(descriptor).to_deterministic_json()
    second = service.classify_one(descriptor).to_deterministic_json()

    assert first == second


def test_service_does_not_persist_or_call_external_services() -> None:
    service = InMemoryDocumentClassificationService()

    assert service.persistence_backend == "in_memory"
    assert service.external_calls_enabled is False
