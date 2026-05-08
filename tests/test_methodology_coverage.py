"""Tests for methodology coverage ledger models and service."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    ReportMapping,
    RequiredEvidence,
)
from idis.models.methodology_coverage import (
    MethodologyAnswer,
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
    MethodologyEvidenceLink,
)
from idis.services.methodology.coverage import (
    InMemoryMethodologyCoverageService,
    InvalidCoverageTransitionError,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _question(question_id: str, section: str) -> MethodologyQuestion:
    return MethodologyQuestion(
        methodology_id="commercial_dd",
        methodology_version_id="commercial_dd:v1",
        methodology_question_id=question_id,
        methodology_type=MethodologyType.COMMERCIAL_DD,
        section=section,
        sheet_or_source_section=section,
        source_row_number=None,
        term="commercial",
        nature="Commercial",
        line_item="market",
        question_text=f"What is the evidence for {section}?",
        required_evidence=[
            RequiredEvidence(
                evidence_type="management_deck",
                description="Management-provided support",
                min_count=1,
            )
        ],
        target_document_categories=["management_presentation"],
        required_calculations=[],
        assigned_agents=[AssignedAgent(role="market_agent", responsibility="Assess evidence")],
        red_flag_rules=[],
        report_mapping=ReportMapping(report_section=section),
        validation_requirements=["requires_claim_or_evidence"],
        source_trace=MethodologySourceTrace(
            source_type="template",
            source_name="commercial_dd_v1.json",
            source_hash="b" * 64,
            sheet_or_section=section,
        ),
    )


def _registry() -> MethodologyRegistry:
    version = MethodologyVersion(
        methodology_id="commercial_dd",
        methodology_version_id="commercial_dd:v1",
        methodology_type=MethodologyType.COMMERCIAL_DD,
        version_label="v1",
        source_hash="b" * 64,
        questions=[
            _question("mq_commercial_dd_market_0001", "Market"),
            _question("mq_commercial_dd_customers_0001", "Customers"),
        ],
    )
    return MethodologyRegistry(
        methodology_id="commercial_dd",
        methodology_type=MethodologyType.COMMERCIAL_DD,
        versions=[version],
    )


def test_initialize_one_record_per_methodology_question() -> None:
    """Every run/deal can initialize a record per methodology question."""
    service = InMemoryMethodologyCoverageService()

    records = service.initialize_coverage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
    )

    assert len(records) == 2
    assert {record.status for record in records} == {MethodologyCoverageStatus.NOT_STARTED}


def test_aggregates_by_methodology_type_and_section() -> None:
    """Coverage summaries are deterministic by type, section, and status."""
    service = InMemoryMethodologyCoverageService()
    service.initialize_coverage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
    )

    summary = service.summarize(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)

    assert summary.total_questions == 2
    assert summary.by_methodology_type == {"commercial_dd": 2}
    assert summary.by_section == {"Customers": 1, "Market": 1}
    assert summary.by_status == {"not_started": 2}


def test_valid_transition_to_evidence_missing_requires_reason() -> None:
    """Reason-coded blocked states are required for auditability."""
    service = InMemoryMethodologyCoverageService()
    record = service.initialize_coverage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
    )[0]

    updated = service.update_status(
        record.coverage_record_id,
        MethodologyCoverageStatus.EVIDENCE_MISSING,
        reason_code="missing_management_deck",
    )

    assert updated.status == MethodologyCoverageStatus.EVIDENCE_MISSING
    assert updated.reason_code == "missing_management_deck"


def test_invalid_transition_fails_closed() -> None:
    """Invalid transitions must not silently mutate coverage state."""
    service = InMemoryMethodologyCoverageService()
    record = service.initialize_coverage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
    )[0]

    service.update_status(
        record.coverage_record_id,
        MethodologyCoverageStatus.BLOCKED,
        reason_code="unsupported_format",
    )

    with pytest.raises(InvalidCoverageTransitionError):
        service.update_status(
            record.coverage_record_id,
            MethodologyCoverageStatus.ANSWERED,
            evidence_links=[MethodologyEvidenceLink(evidence_id="evidence-1", claim_id="claim-1")],
        )


def test_answered_without_evidence_fails_validation() -> None:
    """Answered coverage requires at least one source claim or evidence link."""
    with pytest.raises(ValidationError):
        MethodologyCoverageRecord(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_id="commercial_dd",
            methodology_version_id="commercial_dd:v1",
            methodology_question_id="mq_commercial_dd_market_0001",
            methodology_type=MethodologyType.COMMERCIAL_DD,
            section="Market",
            status=MethodologyCoverageStatus.ANSWERED,
        )


def test_answered_with_claim_or_evidence_works() -> None:
    """Claim/evidence-backed answers can move to answered."""
    service = InMemoryMethodologyCoverageService()
    record = service.initialize_coverage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
    )[0]

    updated = service.update_status(
        record.coverage_record_id,
        MethodologyCoverageStatus.ANSWERED,
        answer=MethodologyAnswer(answer_text="Market is growing", claim_ids=["claim-1"]),
        evidence_links=[MethodologyEvidenceLink(evidence_id="evidence-1", claim_id="claim-1")],
    )

    assert updated.status == MethodologyCoverageStatus.ANSWERED
    assert updated.answer is not None


def test_calculation_backed_answer_requires_calc_id() -> None:
    """Calculation-backed answers cannot omit calc provenance."""
    with pytest.raises(ValidationError):
        MethodologyAnswer(answer_text="Gross margin is 60%", requires_calculation=True)


def test_blocked_and_evidence_missing_require_reason_codes() -> None:
    """Reason-coded statuses must carry machine-readable reasons."""
    for status in (
        MethodologyCoverageStatus.BLOCKED,
        MethodologyCoverageStatus.EVIDENCE_MISSING,
        MethodologyCoverageStatus.UNSUPPORTED_SOURCE,
    ):
        with pytest.raises(ValidationError):
            MethodologyCoverageRecord(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                methodology_id="commercial_dd",
                methodology_version_id="commercial_dd:v1",
                methodology_question_id="mq_commercial_dd_market_0001",
                methodology_type=MethodologyType.COMMERCIAL_DD,
                section="Market",
                status=status,
            )


def test_reason_codes_and_provenance_references_reject_blank_strings() -> None:
    """Whitespace-only reason/provenance values do not satisfy coverage invariants."""
    with pytest.raises(ValidationError):
        MethodologyCoverageRecord(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_id="commercial_dd",
            methodology_version_id="commercial_dd:v1",
            methodology_question_id="mq_commercial_dd_market_0001",
            methodology_type=MethodologyType.COMMERCIAL_DD,
            section="Market",
            status=MethodologyCoverageStatus.BLOCKED,
            reason_code="   ",
        )

    with pytest.raises(ValidationError):
        MethodologyAnswer(answer_text="Market is growing", claim_ids=["   "])

    with pytest.raises(ValidationError):
        MethodologyEvidenceLink(evidence_id="   ")
