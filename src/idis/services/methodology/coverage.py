"""In-memory methodology coverage service."""

from __future__ import annotations

from collections import Counter

from idis.methodology.models import MethodologyRegistry
from idis.models.methodology_coverage import (
    MethodologyAnswer,
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
    MethodologyCoverageSummary,
    MethodologyEvidenceLink,
)


class InvalidCoverageTransitionError(ValueError):
    """Raised when a coverage status transition is not allowed."""


_ALLOWED_TRANSITIONS: dict[MethodologyCoverageStatus, set[MethodologyCoverageStatus]] = {
    MethodologyCoverageStatus.NOT_STARTED: {
        MethodologyCoverageStatus.EVIDENCE_MISSING,
        MethodologyCoverageStatus.UNSUPPORTED_SOURCE,
        MethodologyCoverageStatus.EXTRACTED,
        MethodologyCoverageStatus.PARTIALLY_ANSWERED,
        MethodologyCoverageStatus.ANSWERED,
        MethodologyCoverageStatus.NOT_APPLICABLE,
        MethodologyCoverageStatus.BLOCKED,
    },
    MethodologyCoverageStatus.EXTRACTED: {
        MethodologyCoverageStatus.PARTIALLY_ANSWERED,
        MethodologyCoverageStatus.ANSWERED,
        MethodologyCoverageStatus.CONTRADICTED,
        MethodologyCoverageStatus.BLOCKED,
    },
    MethodologyCoverageStatus.PARTIALLY_ANSWERED: {
        MethodologyCoverageStatus.ANSWERED,
        MethodologyCoverageStatus.CONTRADICTED,
        MethodologyCoverageStatus.BLOCKED,
    },
    MethodologyCoverageStatus.EVIDENCE_MISSING: {
        MethodologyCoverageStatus.EXTRACTED,
        MethodologyCoverageStatus.PARTIALLY_ANSWERED,
        MethodologyCoverageStatus.BLOCKED,
    },
    MethodologyCoverageStatus.UNSUPPORTED_SOURCE: {MethodologyCoverageStatus.BLOCKED},
    MethodologyCoverageStatus.ANSWERED: {MethodologyCoverageStatus.CONTRADICTED},
    MethodologyCoverageStatus.CONTRADICTED: {MethodologyCoverageStatus.BLOCKED},
    MethodologyCoverageStatus.NOT_APPLICABLE: set(),
    MethodologyCoverageStatus.BLOCKED: set(),
}


class InMemoryMethodologyCoverageService:
    """Tenant/deal/run scoped in-memory coverage ledger."""

    def __init__(self) -> None:
        """Initialize an empty in-memory coverage store."""
        self._records: dict[str, MethodologyCoverageRecord] = {}

    def initialize_coverage(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        registry: MethodologyRegistry,
    ) -> list[MethodologyCoverageRecord]:
        """Initialize one coverage record per methodology question."""
        records: list[MethodologyCoverageRecord] = []
        version = registry.current_version
        for question in version.questions:
            record = MethodologyCoverageRecord(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                methodology_id=registry.methodology_id,
                methodology_version_id=version.methodology_version_id,
                methodology_question_id=question.methodology_question_id,
                methodology_type=question.methodology_type,
                section=question.section,
            )
            self._records[record.coverage_record_id] = record
            records.append(record)
        return sorted(records, key=lambda record: record.methodology_question_id)

    def update_status(
        self,
        coverage_record_id: str,
        status: MethodologyCoverageStatus,
        *,
        reason_code: str | None = None,
        evidence_links: list[MethodologyEvidenceLink] | None = None,
        answer: MethodologyAnswer | None = None,
        conflict_ids: list[str] | None = None,
        defect_ids: list[str] | None = None,
    ) -> MethodologyCoverageRecord:
        """Update a coverage record with deterministic transition validation."""
        current = self._records[coverage_record_id]
        allowed = _ALLOWED_TRANSITIONS[current.status]
        if status not in allowed and status != current.status:
            raise InvalidCoverageTransitionError(
                f"invalid coverage transition: {current.status.value} -> {status.value}"
            )

        updated = current.model_copy(
            update={
                "status": status,
                "reason_code": reason_code,
                "evidence_links": evidence_links or current.evidence_links,
                "answer": answer if answer is not None else current.answer,
                "conflict_ids": conflict_ids or current.conflict_ids,
                "defect_ids": defect_ids or current.defect_ids,
            }
        )
        updated = MethodologyCoverageRecord.model_validate(updated.model_dump(mode="json"))
        self._records[coverage_record_id] = updated
        return updated

    def summarize(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
    ) -> MethodologyCoverageSummary:
        """Aggregate coverage by status, methodology type, and section."""
        records = sorted(
            [
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and record.deal_id == deal_id
                and record.run_id == run_id
            ],
            key=lambda record: record.methodology_question_id,
        )
        by_status = Counter(record.status.value for record in records)
        by_type = Counter(record.methodology_type.value for record in records)
        by_section = Counter(record.section for record in records)
        return MethodologyCoverageSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_questions=len(records),
            by_status=dict(sorted(by_status.items())),
            by_methodology_type=dict(sorted(by_type.items())),
            by_section=dict(sorted(by_section.items())),
        )
