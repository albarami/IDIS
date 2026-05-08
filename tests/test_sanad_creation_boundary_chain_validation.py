"""Chain-output validation tests for Phase 2.8 Sanad creation boundary."""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.sanad_coverage_boundary import (
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryResult,
    SanadCoverageBoundaryStatus,
    SanadCoverageBoundarySummary,
    SanadReadinessDecision,
)
from idis.models.sanad_creation_boundary import SanadCreationReason, SanadCreationStatus
from idis.services.sanad.service import CreateSanadInput, SanadService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"
CLAIM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
EVIDENCE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TrackingSanadService(SanadService):
    """SanadService that records create calls."""

    def __init__(self) -> None:
        super().__init__(tenant_id=TENANT_ID, db_conn=None)
        self.create_calls: list[CreateSanadInput] = []

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        self.create_calls.append(input_data)
        return super().create(input_data)


def _readiness() -> SanadReadinessDecision:
    return SanadReadinessDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id=CLAIM_ID,
        source_span_ids=["span-001"],
        evidence_ids=[EVIDENCE_ID],
        calc_ids=["calc-001"],
        ready_for_future_sanad=True,
        reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
        reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
    )


def _coverage_result() -> SanadCoverageBoundaryResult:
    decision = _readiness()
    return SanadCoverageBoundaryResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=SanadCoverageBoundaryStatus.COMPLETED,
        readiness_decisions=[decision],
        coverage_decisions=[],
        summary=SanadCoverageBoundarySummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claim_mappings=1,
            ready_for_future_sanad_count=1,
            coverage_decision_count=0,
            blocked_decision_count=0,
            by_status={"completed": 1},
            by_reason={},
            by_coverage_status={},
        ),
    )


def _evidence_ref() -> Any:
    from idis.models.sanad_coverage_boundary import MethodologyClaimEvidenceReference

    return MethodologyClaimEvidenceReference(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id=CLAIM_ID,
        evidence_id=EVIDENCE_ID,
        source_span_id="span-001",
        calc_ids=["calc-001"],
    )


def test_empty_builder_chain_rejects_before_sanad_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support
    from idis.services.methodology.sanad_creation_boundary import (
        SanadCreationBoundaryService,
    )

    def empty_chain(**_: Any) -> dict[str, Any]:
        return {
            "sanad_id": "transient-builder-id",
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "claim_id": CLAIM_ID,
            "primary_evidence_id": EVIDENCE_ID,
            "transmission_chain": [],
            "created_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", empty_chain)
    sanad_service = TrackingSanadService()

    result = SanadCreationBoundaryService().create_sanads_for_ready_decisions(
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


def test_malformed_builder_node_rejects_before_sanad_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.methodology.sanad_creation_boundary_support as support
    from idis.services.methodology.sanad_creation_boundary import (
        SanadCreationBoundaryService,
    )

    def malformed_chain(**_: Any) -> dict[str, Any]:
        return {
            "sanad_id": "transient-builder-id",
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "claim_id": CLAIM_ID,
            "primary_evidence_id": EVIDENCE_ID,
            "transmission_chain": [{"node_type": "BOGUS"}],
            "created_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(support.chain_builder, "build_sanad_chain", malformed_chain)
    sanad_service = TrackingSanadService()

    result = SanadCreationBoundaryService().create_sanads_for_ready_decisions(
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
