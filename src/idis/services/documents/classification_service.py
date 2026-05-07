"""In-memory document classification service."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from idis.models.document_classification import (
    DocumentClassification,
    DocumentClassificationBlockerSummary,
    DocumentClassificationSummary,
    DocumentSupportStatus,
)
from idis.parsers.base import ParseResult
from idis.services.documents.classifier import DocumentDescriptor, classify_document


class InMemoryDocumentClassificationService:
    """Classify documents and keep deterministic in-memory results."""

    persistence_backend = "in_memory"
    external_calls_enabled = False

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], DocumentClassification] = {}

    def classify_one(
        self,
        descriptor: DocumentDescriptor,
        *,
        parse_result: ParseResult | None = None,
    ) -> DocumentClassification:
        """Classify one document descriptor."""
        classification = classify_document(descriptor, parse_result=parse_result)
        key = (classification.tenant_id, classification.deal_id, classification.document_id)
        self._records[key] = classification
        return classification

    def classify_batch(
        self,
        descriptors: list[DocumentDescriptor],
        *,
        parse_results: dict[str, ParseResult] | None = None,
    ) -> list[DocumentClassification]:
        """Classify a batch of document descriptors in input order."""
        parse_results = parse_results or {}
        return [
            self.classify_one(
                descriptor,
                parse_result=parse_results.get(descriptor.document_id),
            )
            for descriptor in descriptors
        ]

    def summarize(self, *, tenant_id: str, deal_id: str) -> DocumentClassificationSummary:
        """Summarize records for one tenant/deal scope."""
        records = self._records_for(tenant_id=tenant_id, deal_id=deal_id)
        return DocumentClassificationSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            total_documents=len(records),
            by_fdd_category=_counter(record.fdd_category.value for record in records),
            by_cdd_category=_counter(record.cdd_category.value for record in records),
            by_support_status=_counter(record.support_status.value for record in records),
            by_triage_status=_counter(record.triage_status.value for record in records),
        )

    def blocker_summary(
        self,
        *,
        tenant_id: str,
        deal_id: str,
    ) -> DocumentClassificationBlockerSummary:
        """Summarize blocked and conversion-required records by reason code."""
        blocked_statuses = {
            DocumentSupportStatus.UNSUPPORTED,
            DocumentSupportStatus.ENCRYPTED,
            DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
            DocumentSupportStatus.TOO_LARGE,
            DocumentSupportStatus.CORRUPTED,
            DocumentSupportStatus.CONVERSION_REQUIRED,
            DocumentSupportStatus.UNKNOWN,
        }
        records = [
            record
            for record in self._records_for(tenant_id=tenant_id, deal_id=deal_id)
            if record.support_status in blocked_statuses
        ]
        return DocumentClassificationBlockerSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            total_blocked=len(records),
            by_reason_code=_counter(
                reason_code
                for record in records
                for reason_code in record.reason_codes
                if reason_code
            ),
        )

    def _records_for(self, *, tenant_id: str, deal_id: str) -> list[DocumentClassification]:
        return [
            record
            for key, record in sorted(self._records.items())
            if key[0] == tenant_id and key[1] == deal_id
        ]


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
