"""Tests for Phase 2.8 synthetic Sanad creation boundary service."""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.methodology_coverage import MethodologyCoverageStatus
from idis.models.sanad_coverage_boundary import (
    ICPromotionStatus,
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryResult,
    SanadCoverageBoundaryStatus,
    SanadCoverageBoundarySummary,
    SanadReadinessDecision,
)
from idis.models.sanad_creation_boundary import (
    SanadCreationReason,
    SanadCreationStatus,
)
from idis.services.sanad.chain_builder import ChainBuildError
from idis.services.sanad.service import CreateSanadInput, SanadService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"
QUESTION_2_ID = "mq_financial_dd_revenue_quality_0002"
CLAIM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLAIM_2_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
EVIDENCE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
EVIDENCE_2_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbc"


class TrackingSanadService(SanadService):
    """SanadService that records create calls."""

    def __init__(self, *, tenant_id: str = TENANT_ID) -> None:
        super().__init__(tenant_id=tenant_id, db_conn=None)
        self.create_calls: list[CreateSanadInput] = []

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        self.create_calls.append(input_data)
        return super().create(input_data)


class FailingOnceSanadService(TrackingSanadService):
    """SanadService that fails once and then delegates to the real service."""

    def __init__(self, *, tenant_id: str = TENANT_ID) -> None:
        super().__init__(tenant_id=tenant_id)
        self._failure_count = 0

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        if self._failure_count == 0:
            self._failure_count += 1
            self.create_calls.append(input_data)
            raise ValueError("synthetic create failure")
        return super().create(input_data)


def _readiness(
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    claim_id: str | None = CLAIM_ID,
    methodology_question_id: str = QUESTION_ID,
    source_span_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    ready_for_future_sanad: bool = True,
    sanad_id: str | None = None,
) -> SanadReadinessDecision:
    return SanadReadinessDecision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        source_span_ids=source_span_ids or ["span-001"],
        evidence_ids=evidence_ids or [EVIDENCE_ID],
        calc_ids=["calc-001"],
        sanad_id=sanad_id,
        ready_for_future_sanad=ready_for_future_sanad,
        reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
        reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
    )


def _coverage_result(
    readiness_decisions: list[SanadReadinessDecision] | None = None,
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
) -> SanadCoverageBoundaryResult:
    decisions = readiness_decisions or [_readiness()]
    return SanadCoverageBoundaryResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=SanadCoverageBoundaryStatus.COMPLETED,
        readiness_decisions=decisions,
        coverage_decisions=[],
        summary=SanadCoverageBoundarySummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
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
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    claim_id: str = CLAIM_ID,
    methodology_question_id: str = QUESTION_ID,
    evidence_id: str = EVIDENCE_ID,
    source_span_id: str = "span-001",
    calc_ids: list[str] | None = None,
    target_status: MethodologyCoverageStatus = MethodologyCoverageStatus.EXTRACTED,
    conflict_ids: list[str] | None = None,
    defect_ids: list[str] | None = None,
) -> MethodologyClaimEvidenceReference:
    return MethodologyClaimEvidenceReference(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_span_id=source_span_id,
        calc_ids=calc_ids or ["calc-001"],
        target_status=target_status,
        conflict_ids=conflict_ids or [],
        defect_ids=defect_ids or [],
    )


def _service() -> Any:
    from idis.services.methodology.sanad_creation_boundary import (
        SanadCreationBoundaryService,
    )

    return SanadCreationBoundaryService()


def test_ready_decision_builds_chain_and_creates_sanad_through_injected_service() -> None:
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(
            [
                _readiness(
                    evidence_ids=[EVIDENCE_2_ID, EVIDENCE_ID],
                    source_span_ids=["span-001", "span-002"],
                )
            ]
        ),
        evidence_references=[
            _evidence_ref(evidence_id=EVIDENCE_2_ID, source_span_id="span-002"),
            _evidence_ref(evidence_id=EVIDENCE_ID, source_span_id="span-001"),
        ],
        sanad_service=sanad_service,
        extraction_confidence=0.91,
        dhabt_score=0.88,
    )

    assert result.status == SanadCreationStatus.COMPLETED
    assert len(sanad_service.create_calls) == 1
    create_input = sanad_service.create_calls[0]
    assert create_input.transmission_chain
    assert create_input.primary_evidence_id == EVIDENCE_ID
    assert create_input.corroborating_evidence_ids == [EVIDENCE_2_ID]
    assert create_input.extraction_confidence == 0.91
    assert create_input.dhabt_score == 0.88

    mapping = result.mappings[0]
    assert mapping.claim_id == CLAIM_ID
    assert mapping.methodology_question_id == QUESTION_ID
    assert mapping.source_span_ids == ["span-001", "span-002"]
    assert mapping.evidence_ids == [EVIDENCE_ID, EVIDENCE_2_ID]
    assert mapping.primary_evidence_id == EVIDENCE_ID
    assert mapping.corroborating_evidence_ids == [EVIDENCE_2_ID]
    assert mapping.sanad_id
    assert sanad_service.get(mapping.sanad_id)["sanad_id"] == mapping.sanad_id
    assert mapping.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD
    assert mapping.coverage_update_status == "not_applied"
    assert result.claim_link_decisions[0].coverage_status == "deferred"
    assert result.summary.to_deterministic_json() == result.summary.to_deterministic_json()


def test_result_uses_persisted_sanad_service_id_not_transient_builder_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fake_build_sanad_chain(**_: Any) -> dict[str, Any]:
        return {
            "sanad_id": "transient-builder-id",
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "claim_id": CLAIM_ID,
            "primary_evidence_id": EVIDENCE_ID,
            "transmission_chain": [
                {
                    "node_id": "55555555-5555-5555-5555-555555555555",
                    "node_type": "INGEST",
                    "actor_type": "SYSTEM",
                    "actor_id": "idis_ingestion",
                    "prev_node_id": None,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "input_refs": [{"evidence_id": EVIDENCE_ID}],
                    "output_refs": [{"claim_id": CLAIM_ID}],
                }
            ],
            "created_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fake_build_sanad_chain)
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.mappings[0].sanad_id
    assert result.mappings[0].sanad_id != "transient-builder-id"
    assert sanad_service.get(result.mappings[0].sanad_id)["sanad_id"] == (
        result.mappings[0].sanad_id
    )


def test_injected_sanad_service_tenant_mismatch_fails_closed() -> None:
    sanad_service = TrackingSanadService(tenant_id=OTHER_TENANT_ID)

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.TENANT_OR_SERVICE_MISMATCH
    assert sanad_service.create_calls == []


def test_missing_evidence_rejects_before_sanad_create() -> None:
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.EVIDENCE_MISSING
    assert sanad_service.create_calls == []


def test_source_span_mismatch_rejects_before_chain_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fail_if_called(**_: Any) -> dict[str, Any]:
        raise AssertionError("chain builder should not be called")

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fail_if_called)
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[_evidence_ref(source_span_id="span-999")],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.SOURCE_SPAN_MISMATCH
    assert sanad_service.create_calls == []


def test_missing_claim_linkage_fails_closed() -> None:
    sanad_service = TrackingSanadService()
    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result([_readiness(claim_id=None)]),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.MISSING_CLAIM_LINKAGE
    assert sanad_service.create_calls == []


def test_tenant_deal_run_mismatch_fails_closed() -> None:
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(tenant_id=OTHER_TENANT_ID),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.TENANT_OR_RUN_MISMATCH
    assert sanad_service.create_calls == []


def test_chain_build_error_becomes_deterministic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support

    def fail_build(**_: Any) -> dict[str, Any]:
        raise ChainBuildError(CLAIM_ID, "synthetic malformed chain")

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", fail_build)
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.CHAIN_BUILD_FAILED
    assert sanad_service.create_calls == []


def test_sanad_create_failure_becomes_rejection_and_batch_continues() -> None:
    sanad_service = FailingOnceSanadService()
    decisions = [
        _readiness(),
        _readiness(
            claim_id=CLAIM_2_ID,
            methodology_question_id=QUESTION_2_ID,
            evidence_ids=[EVIDENCE_2_ID],
            source_span_ids=["span-002"],
        ),
    ]

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(decisions),
        evidence_references=[
            _evidence_ref(),
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


def test_malformed_contradicted_evidence_is_rejected_deterministically() -> None:
    malformed = MethodologyClaimEvidenceReference.model_construct(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id=CLAIM_ID,
        evidence_id=EVIDENCE_ID,
        source_span_id="span-001",
        target_status=MethodologyCoverageStatus.CONTRADICTED,
        conflict_ids=[],
        defect_ids=[],
        calc_ids=[],
        sanad_status="deferred",
    )
    sanad_service = TrackingSanadService()

    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(),
        evidence_references=[malformed],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == SanadCreationReason.CONTRADICTED_EVIDENCE
    assert sanad_service.create_calls == []


def test_duplicate_conflicting_readiness_decisions_fail_closed() -> None:
    sanad_service = TrackingSanadService()
    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(
            [
                _readiness(claim_id=CLAIM_ID, source_span_ids=["span-001"]),
                _readiness(claim_id=CLAIM_2_ID, source_span_ids=["span-002"]),
            ]
        ),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.FAILED
    assert result.rejections[0].reason == (
        SanadCreationReason.DUPLICATE_CONFLICTING_READINESS_DECISION
    )
    assert sanad_service.create_calls == []


def test_existing_sanad_id_is_reported_as_already_created_and_skipped() -> None:
    sanad_service = TrackingSanadService()
    result = _service().create_sanads_for_ready_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_coverage_result=_coverage_result(
            [_readiness(sanad_id="44444444-4444-4444-4444-444444444444")]
        ),
        evidence_references=[_evidence_ref()],
        sanad_service=sanad_service,
    )

    assert result.status == SanadCreationStatus.COMPLETED
    assert result.rejections[0].reason == SanadCreationReason.ALREADY_CREATED
    assert result.summary.already_created_count == 1
    assert sanad_service.create_calls == []


