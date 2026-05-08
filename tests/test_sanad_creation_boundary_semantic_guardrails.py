"""Semantic guardrails for Phase 2.8 Sanad creation boundary."""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.methodology_coverage import MethodologyCoverageStatus
from idis.models.sanad_coverage_boundary import (
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryResult,
    SanadCoverageBoundaryStatus,
    SanadCoverageBoundarySummary,
    SanadReadinessDecision,
)
from idis.models.sanad_creation_boundary import SanadCreationReason, SanadCreationStatus
from idis.services.methodology.sanad_creation_boundary import SanadCreationBoundaryService
from idis.services.sanad.service import CreateSanadInput, SanadService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"
QUESTION_2_ID = "mq_financial_dd_revenue_quality_0002"
CLAIM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLAIM_2_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
EVIDENCE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
EVIDENCE_2_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbc"
EVIDENCE_3_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbd"


class TrackingSanadService(SanadService):
    """SanadService that records create calls."""

    def __init__(self) -> None:
        super().__init__(tenant_id=TENANT_ID, db_conn=None)
        self.create_calls: list[CreateSanadInput] = []

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        self.create_calls.append(input_data)
        return super().create(input_data)


class RuntimeFailingOnceSanadService(TrackingSanadService):
    """SanadService that raises an unexpected exception once."""

    def __init__(self) -> None:
        super().__init__()
        self._failure_count = 0

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        if self._failure_count == 0:
            self._failure_count += 1
            self.create_calls.append(input_data)
            raise RuntimeError("unexpected synthetic create failure")
        return super().create(input_data)


def _readiness(
    *,
    claim_id: str | None = CLAIM_ID,
    methodology_question_id: str = QUESTION_ID,
    source_span_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    ready_for_future_sanad: bool = True,
    reason: SanadCoverageBoundaryReason = (
        SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD
    ),
) -> SanadReadinessDecision:
    return SanadReadinessDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        source_span_ids=source_span_ids or ["span-001"],
        evidence_ids=evidence_ids or [EVIDENCE_ID],
        ready_for_future_sanad=ready_for_future_sanad,
        reason=reason,
        reason_codes=[reason.value],
    )


def _coverage_result(decisions: list[SanadReadinessDecision]) -> SanadCoverageBoundaryResult:
    return SanadCoverageBoundaryResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=SanadCoverageBoundaryStatus.COMPLETED,
        readiness_decisions=decisions,
        coverage_decisions=[],
        summary=SanadCoverageBoundarySummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claim_mappings=len(decisions),
            ready_for_future_sanad_count=sum(
                1 for decision in decisions if decision.ready_for_future_sanad
            ),
            coverage_decision_count=0,
            blocked_decision_count=0,
            by_status={"completed": len(decisions)},
            by_reason={},
            by_coverage_status={},
        ),
    )


def _evidence_ref(
    *,
    claim_id: str = CLAIM_ID,
    methodology_question_id: str = QUESTION_ID,
    evidence_id: str = EVIDENCE_ID,
    source_span_id: str = "span-001",
    target_status: MethodologyCoverageStatus = MethodologyCoverageStatus.EXTRACTED,
    conflict_ids: list[str] | None = None,
    defect_ids: list[str] | None = None,
) -> MethodologyClaimEvidenceReference:
    return MethodologyClaimEvidenceReference(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_span_id=source_span_id,
        target_status=target_status,
        conflict_ids=conflict_ids or [],
        defect_ids=defect_ids or [],
    )


def _run(
    *,
    decisions: list[SanadReadinessDecision],
    evidence_references: list[MethodologyClaimEvidenceReference],
    sanad_service: SanadService | None = None,
) -> Any:
    return SanadCreationBoundaryService().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(decisions),
        evidence_references=evidence_references,
        sanad_service=sanad_service or TrackingSanadService(),
    )


def test_mixed_valid_and_invalid_same_scope_refs_fail_before_chain_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fail_if_called(**_: Any) -> dict[str, Any]:
        raise AssertionError("chain builder should not be called")

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fail_if_called)
    sanad_service = TrackingSanadService()

    result = _run(
        decisions=[_readiness(evidence_ids=[EVIDENCE_ID])],
        evidence_references=[
            _evidence_ref(evidence_id=EVIDENCE_ID, source_span_id="span-001"),
            _evidence_ref(evidence_id=EVIDENCE_2_ID, source_span_id="span-999"),
        ],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.SOURCE_SPAN_MISMATCH
    assert sanad_service.create_calls == []


def test_ready_true_with_blocked_reason_does_not_create_sanad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fail_if_called(**_: Any) -> dict[str, Any]:
        raise AssertionError("chain builder should not be called")

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fail_if_called)
    sanad_service = TrackingSanadService()

    result = _run(
        decisions=[
            _readiness(
                ready_for_future_sanad=True,
                reason=SanadCoverageBoundaryReason.BLOCKED,
            )
        ],
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.BLOCKED
    assert sanad_service.create_calls == []


def test_same_claim_question_span_with_different_evidence_sets_is_blocked() -> None:
    sanad_service = TrackingSanadService()

    result = _run(
        decisions=[
            _readiness(evidence_ids=[EVIDENCE_ID]),
            _readiness(evidence_ids=[EVIDENCE_2_ID]),
        ],
        evidence_references=[
            _evidence_ref(evidence_id=EVIDENCE_ID),
            _evidence_ref(evidence_id=EVIDENCE_2_ID),
        ],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == (
        SanadCreationReason.DUPLICATE_CONFLICTING_READINESS_DECISION
    )
    assert sanad_service.create_calls == []


def test_runtime_error_from_sanad_create_becomes_rejection_and_batch_continues() -> None:
    sanad_service = RuntimeFailingOnceSanadService()

    result = _run(
        decisions=[
            _readiness(evidence_ids=[EVIDENCE_ID]),
            _readiness(
                claim_id=CLAIM_2_ID,
                methodology_question_id=QUESTION_2_ID,
                source_span_ids=["span-002"],
                evidence_ids=[EVIDENCE_2_ID],
            ),
        ],
        evidence_references=[
            _evidence_ref(evidence_id=EVIDENCE_ID),
            _evidence_ref(
                claim_id=CLAIM_2_ID,
                methodology_question_id=QUESTION_2_ID,
                evidence_id=EVIDENCE_2_ID,
                source_span_id="span-002",
            ),
        ],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.PARTIAL
    assert result.rejections[0].reason == SanadCreationReason.SANAD_CREATION_FAILED
    assert len(result.mappings) == 1
    assert len(sanad_service.create_calls) == 2


@pytest.mark.parametrize(
    ("evidence_id", "conflict_ids", "defect_ids"),
    [
        (EVIDENCE_ID, ["conflict-001"], None),
        (EVIDENCE_3_ID, None, ["defect-001"]),
    ],
)
def test_contradicted_evidence_never_creates_sanad(
    evidence_id: str,
    conflict_ids: list[str] | None,
    defect_ids: list[str] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fail_if_called(**_: Any) -> dict[str, Any]:
        raise AssertionError("chain builder should not be called")

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fail_if_called)
    sanad_service = TrackingSanadService()

    result = _run(
        decisions=[_readiness(evidence_ids=[evidence_id])],
        evidence_references=[
            _evidence_ref(
                evidence_id=evidence_id,
                target_status=MethodologyCoverageStatus.CONTRADICTED,
                conflict_ids=conflict_ids,
                defect_ids=defect_ids,
            )
        ],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.CONTRADICTED_EVIDENCE
    assert sanad_service.create_calls == []


def test_synthetic_evidence_defaults_to_source_grade_d() -> None:
    from idis.models.evidence_item import SourceGrade
    from idis.services.methodology.sanad_creation_boundary_support import (
        evidence_items_from_references,
    )

    evidence_items = evidence_items_from_references([_evidence_ref()])

    assert evidence_items[0].source_grade == SourceGrade.D
