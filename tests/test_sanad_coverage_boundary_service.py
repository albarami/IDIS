"""Tests for Phase 2.7 Sanad and coverage boundary service."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from idis.methodology.models import MethodologyType
from idis.models.claim_materialization import (
    ClaimMaterializationResult,
    ClaimMaterializationStatus,
    DraftClaimMapping,
    MethodologyClaimMaterializationSummary,
)
from idis.models.methodology_coverage import (
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
)
from idis.models.sanad_coverage_boundary import (
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryStatus,
)
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService
from idis.services.methodology.sanad_coverage_boundary import (
    InvalidCoverageDecisionScopeError,
    SanadCoverageBoundaryService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"


class TrackingCoverageService(InMemoryMethodologyCoverageService):
    """Coverage service that records update calls."""

    def __init__(self, records: list[MethodologyCoverageRecord]) -> None:
        super().__init__()
        self.update_calls: list[dict[str, Any]] = []
        self._records = {record.coverage_record_id: record for record in records}

    def update_status(self, *args: Any, **kwargs: Any) -> MethodologyCoverageRecord:
        self.update_calls.append({"args": args, "kwargs": kwargs})
        return super().update_status(*args, **kwargs)


def _mapping(
    *,
    claim_id: str = "claim-001",
    methodology_question_id: str = QUESTION_ID,
    source_span_ids: list[str] | None = None,
) -> DraftClaimMapping:
    return DraftClaimMapping(
        methodology_claim_draft_id=f"mcd_{claim_id}",
        claim_id=claim_id,
        extraction_task_id="et_revenue_quality",
        methodology_question_id=methodology_question_id,
        document_id="doc-financial-model",
        source_span_ids=source_span_ids or ["span-001"],
    )


def _materialization_result(
    mappings: list[DraftClaimMapping] | None = None,
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
) -> ClaimMaterializationResult:
    mappings = mappings or [_mapping()]
    return ClaimMaterializationResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=ClaimMaterializationStatus.COMPLETED,
        draft_claim_mappings=mappings,
        rejected_drafts=[],
        summary=MethodologyClaimMaterializationSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_drafts=len(mappings),
            created_claim_count=len(mappings),
            rejected_draft_count=0,
            by_status={"completed": len(mappings)},
            by_reason={},
        ),
    )


def _coverage_record(
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    methodology_question_id: str = QUESTION_ID,
) -> MethodologyCoverageRecord:
    return MethodologyCoverageRecord(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id=methodology_question_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        section="Revenue Quality",
    )


def _evidence_ref(
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    claim_id: str = "claim-001",
    methodology_question_id: str = QUESTION_ID,
    source_span_id: str = "span-001",
    evidence_id: str = "evidence-001",
    target_status: MethodologyCoverageStatus = MethodologyCoverageStatus.EXTRACTED,
    answer_text: str | None = None,
    calc_ids: list[str] | None = None,
    conflict_ids: list[str] | None = None,
    defect_ids: list[str] | None = None,
    sanad_id: str | None = None,
    sanad_status: str = "deferred",
) -> MethodologyClaimEvidenceReference:
    return MethodologyClaimEvidenceReference(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_span_id=source_span_id,
        target_status=target_status,
        answer_text=answer_text,
        calc_ids=calc_ids or [],
        conflict_ids=conflict_ids or [],
        defect_ids=defect_ids or [],
        sanad_id=sanad_id,
        sanad_status=sanad_status,
    )


def _service() -> SanadCoverageBoundaryService:
    return SanadCoverageBoundaryService()


def test_happy_path_creates_readiness_and_coverage_decisions() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref()],
    )

    assert result.status == SanadCoverageBoundaryStatus.COMPLETED
    readiness = result.readiness_decisions[0]
    coverage = result.coverage_decisions[0]
    assert readiness.ready_for_future_sanad is True
    assert readiness.claim_id == "claim-001"
    assert readiness.evidence_ids == ["evidence-001"]
    assert readiness.source_span_ids == ["span-001"]
    assert readiness.methodology_question_id == QUESTION_ID
    assert readiness.ic_promotion_status == "deferred_until_sanad"
    assert coverage.claim_ids == ["claim-001"]
    assert coverage.evidence_ids == ["evidence-001"]
    assert coverage.source_span_ids == ["span-001"]
    assert coverage.methodology_question_id == QUESTION_ID
    assert coverage.ic_promotion_status == "deferred_until_sanad"


def test_answered_decision_uses_deferred_sanad_status_without_ic_promotion() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(
                target_status=MethodologyCoverageStatus.ANSWERED,
                answer_text="Revenue quality has supporting synthetic evidence.",
            )
        ],
    )

    coverage = result.coverage_decisions[0]
    assert coverage.target_status == MethodologyCoverageStatus.ANSWERED
    assert coverage.sanad_status == "deferred"
    assert coverage.sanad_id is None
    assert coverage.ic_promotion_status == "deferred_until_sanad"


def test_missing_evidence_creates_evidence_missing_decision_and_no_sanad() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[],
    )

    readiness = result.readiness_decisions[0]
    coverage = result.coverage_decisions[0]
    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert readiness.ready_for_future_sanad is False
    assert readiness.reason == SanadCoverageBoundaryReason.EVIDENCE_MISSING
    assert readiness.sanad_id is None
    assert coverage.target_status == MethodologyCoverageStatus.EVIDENCE_MISSING
    assert coverage.reason == SanadCoverageBoundaryReason.EVIDENCE_MISSING


def test_tenant_deal_run_mismatch_fails_closed() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(tenant_id=OTHER_TENANT_ID),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref()],
    )

    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert result.coverage_decisions[0].target_status == MethodologyCoverageStatus.BLOCKED
    assert result.coverage_decisions[0].reason == SanadCoverageBoundaryReason.TENANT_OR_RUN_MISMATCH


def test_source_span_mismatch_fails_closed() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref(source_span_id="span-999")],
    )

    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert result.readiness_decisions[0].reason == SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH
    assert result.coverage_decisions[0].target_status == MethodologyCoverageStatus.BLOCKED


def test_any_scoped_source_span_mismatch_for_same_claim_question_fails_closed() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(evidence_id="evidence-valid", source_span_id="span-001"),
            _evidence_ref(evidence_id="evidence-invalid", source_span_id="span-999"),
        ],
    )

    readiness = result.readiness_decisions[0]
    coverage = result.coverage_decisions[0]
    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert readiness.ready_for_future_sanad is False
    assert readiness.reason == SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH
    assert coverage.target_status == MethodologyCoverageStatus.BLOCKED
    assert coverage.reason == SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH


def test_missing_methodology_linkage_fails_closed() -> None:
    mapping = _mapping(methodology_question_id="mq_missing")
    bad_mapping = mapping.model_copy(update={"methodology_question_id": ""})

    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result([bad_mapping]),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref()],
    )

    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert result.coverage_decisions[0].reason == (
        SanadCoverageBoundaryReason.MISSING_METHODOLOGY_LINKAGE
    )


def test_duplicate_conflicting_mappings_fail_closed() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(
            [
                _mapping(claim_id="claim-001", source_span_ids=["span-001"]),
                _mapping(claim_id="claim-002", source_span_ids=["span-002"]),
            ]
        ),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref()],
    )

    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert result.coverage_decisions[0].target_status == MethodologyCoverageStatus.BLOCKED
    assert set(result.coverage_decisions[0].claim_ids) == {"claim-001", "claim-002"}
    assert set(result.coverage_decisions[0].source_span_ids) == {"span-001", "span-002"}
    assert result.coverage_decisions[0].reason == (
        SanadCoverageBoundaryReason.DUPLICATE_CONFLICTING_MAPPING
    )


def test_status_decisions_include_partial_answered_and_contradicted() -> None:
    partial = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(target_status=MethodologyCoverageStatus.PARTIALLY_ANSWERED)
        ],
    )
    contradicted = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(
                target_status=MethodologyCoverageStatus.CONTRADICTED,
                conflict_ids=["conflict-001"],
            )
        ],
    )

    assert partial.coverage_decisions[0].target_status == (
        MethodologyCoverageStatus.PARTIALLY_ANSWERED
    )
    assert contradicted.coverage_decisions[0].target_status == (
        MethodologyCoverageStatus.CONTRADICTED
    )
    assert contradicted.coverage_decisions[0].conflict_ids == ["conflict-001"]


def test_malformed_contradicted_reference_does_not_crash_service() -> None:
    malformed = MethodologyClaimEvidenceReference.model_construct(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id="claim-001",
        evidence_id="evidence-001",
        source_span_id="span-001",
        target_status=MethodologyCoverageStatus.CONTRADICTED,
        conflict_ids=[],
        defect_ids=[],
        calc_ids=[],
        sanad_status="deferred",
    )

    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[malformed],
    )

    assert result.status == SanadCoverageBoundaryStatus.FAILED
    assert result.readiness_decisions[0].ready_for_future_sanad is False
    assert result.coverage_decisions[0].target_status == MethodologyCoverageStatus.BLOCKED
    assert result.coverage_decisions[0].reason == SanadCoverageBoundaryReason.BLOCKED


def test_available_sanad_reference_is_preserved_without_ic_promotion() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref(sanad_id="sanad-001", sanad_status="available")],
    )

    readiness = result.readiness_decisions[0]
    coverage = result.coverage_decisions[0]
    assert readiness.sanad_id == "sanad-001"
    assert readiness.sanad_status == "available"
    assert readiness.ic_promotion_status == "deferred_until_sanad"
    assert coverage.sanad_id == "sanad-001"
    assert coverage.sanad_status == "available"
    assert coverage.ic_promotion_status == "deferred_until_sanad"


def test_blocked_and_evidence_missing_reference_statuses_are_not_sanad_ready() -> None:
    blocked = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[_evidence_ref(target_status=MethodologyCoverageStatus.BLOCKED)],
    )
    evidence_missing = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(target_status=MethodologyCoverageStatus.EVIDENCE_MISSING)
        ],
    )

    assert blocked.readiness_decisions[0].ready_for_future_sanad is False
    assert blocked.coverage_decisions[0].target_status == MethodologyCoverageStatus.BLOCKED
    assert evidence_missing.readiness_decisions[0].ready_for_future_sanad is False
    assert evidence_missing.coverage_decisions[0].target_status == (
        MethodologyCoverageStatus.EVIDENCE_MISSING
    )


def test_mixed_evidence_missing_and_extracted_refs_are_not_sanad_ready() -> None:
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[_coverage_record()],
        evidence_references=[
            _evidence_ref(
                target_status=MethodologyCoverageStatus.EXTRACTED,
                evidence_id="evidence-001",
            ),
            _evidence_ref(
                target_status=MethodologyCoverageStatus.EVIDENCE_MISSING,
                evidence_id="evidence-002",
            ),
        ],
    )

    assert result.readiness_decisions[0].ready_for_future_sanad is False
    assert result.coverage_decisions[0].target_status == (
        MethodologyCoverageStatus.EVIDENCE_MISSING
    )


def test_default_boundary_flow_does_not_mutate_coverage_records_or_call_update() -> None:
    record = _coverage_record()
    coverage_service = TrackingCoverageService([record])

    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[record],
        evidence_references=[_evidence_ref()],
    )

    assert result.coverage_decisions
    assert record.status == MethodologyCoverageStatus.NOT_STARTED
    assert coverage_service.update_calls == []


def test_apply_decisions_in_memory_is_separate_and_explicitly_injected() -> None:
    record = _coverage_record()
    service = TrackingCoverageService([record])
    result = _service().build_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialization_result=_materialization_result(),
        coverage_records=[record],
        evidence_references=[_evidence_ref()],
    )

    updated = _service().apply_decisions_in_memory(
        coverage_service=service,
        coverage_records=[record],
        decisions=result.coverage_decisions,
    )

    assert len(service.update_calls) == 1
    assert updated[0].status == MethodologyCoverageStatus.EXTRACTED


def test_apply_decisions_in_memory_matches_full_tenant_deal_run_scope() -> None:
    tenant_a_record = _coverage_record()
    tenant_b_record = _coverage_record(tenant_id=OTHER_TENANT_ID)
    service = TrackingCoverageService([tenant_b_record])
    decision = (
        _service()
        .build_decisions(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            materialization_result=_materialization_result(),
            coverage_records=[tenant_a_record],
            evidence_references=[_evidence_ref()],
        )
        .coverage_decisions[0]
    )

    with pytest.raises(InvalidCoverageDecisionScopeError):
        _service().apply_decisions_in_memory(
            coverage_service=service,
            coverage_records=[tenant_b_record],
            decisions=[decision],
        )

    assert service.update_calls == []
    assert tenant_b_record.status == MethodologyCoverageStatus.NOT_STARTED


def test_boundary_service_does_not_import_or_call_forbidden_integrations() -> None:
    import idis.services.methodology.sanad_coverage_boundary as boundary

    source = inspect.getsource(boundary)
    forbidden = [
        "SanadService",
        "auto_grade_claims_for_run",
        "ClaimsRepository",
        "EvidenceRepo",
        "ClaimService",
        "update(",
        "ic_bound=True",
        "sqlalchemy",
        "FastAPI",
        "APIRouter",
        "neo4j",
        "redis",
        "pgvector",
        "requests",
        "httpx",
    ]

    for token in forbidden:
        assert token not in source
