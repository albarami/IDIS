"""Tests for Phase 2.9 Claim-Sanad link boundary service."""

from __future__ import annotations

from typing import Any

from idis.models.claim_sanad_link_boundary import (
    ClaimSanadLinkReason,
    ClaimSanadLinkStatus,
)
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
    SanadCreationResult,
    SanadCreationStatus,
    SanadCreationSummary,
)
from idis.services.claims.service import ClaimNotFoundError, ClaimService, UpdateClaimInput

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"
CLAIM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLAIM_2_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
SANAD_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SANAD_2_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbc"


class TrackingClaimService(ClaimService):
    """ClaimService test double that records update inputs."""

    def __init__(
        self,
        *,
        tenant_id: str = TENANT_ID,
        claims: dict[str, dict[str, Any]] | None = None,
        sanads: dict[str, dict[str, Any]] | None = None,
        update_failure: Exception | None = None,
        update_override: dict[str, Any] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self.claims = claims or {}
        self.sanads = sanads or {}
        self.update_failure = update_failure
        self.update_override = update_override
        self.update_calls: list[tuple[str, UpdateClaimInput]] = []

    def get(self, claim_id: str) -> dict[str, Any]:
        claim = self.claims.get(claim_id)
        if claim is None:
            raise ClaimNotFoundError(claim_id, self.tenant_id)
        return dict(claim)

    def get_sanad(self, sanad_id: str) -> dict[str, Any] | None:
        sanad = self.sanads.get(sanad_id)
        return dict(sanad) if sanad is not None else None

    def update(self, claim_id: str, input_data: UpdateClaimInput) -> dict[str, Any]:
        self.update_calls.append((claim_id, input_data))
        if self.update_failure is not None:
            raise self.update_failure
        updated = dict(self.claims[claim_id])
        updated["sanad_id"] = input_data.sanad_id
        if self.update_override:
            updated.update(self.update_override)
        self.claims[claim_id] = updated
        return dict(updated)


class FailingOnceClaimService(TrackingClaimService):
    """ClaimService test double that fails once and then applies updates."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._failed = False

    def update(self, claim_id: str, input_data: UpdateClaimInput) -> dict[str, Any]:
        if not self._failed:
            self._failed = True
            self.update_calls.append((claim_id, input_data))
            raise RuntimeError("synthetic update failure")
        return super().update(claim_id, input_data)


def _claim(
    *,
    claim_id: str = CLAIM_ID,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    sanad_id: str | None = None,
    claim_grade: str = "D",
    claim_verdict: str = "UNVERIFIED",
    claim_action: str = "VERIFY",
    ic_bound: bool = False,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "claim_text": "Synthetic claim.",
        "sanad_id": sanad_id,
        "claim_grade": claim_grade,
        "claim_verdict": claim_verdict,
        "claim_action": claim_action,
        "ic_bound": ic_bound,
    }


def _sanad(
    *,
    sanad_id: str = SANAD_ID,
    claim_id: str = CLAIM_ID,
    deal_id: str = DEAL_ID,
    tenant_id: str = TENANT_ID,
) -> dict[str, Any]:
    return {
        "sanad_id": sanad_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "claim_id": claim_id,
    }


def _mapping(
    *,
    claim_id: str = CLAIM_ID,
    sanad_id: str = SANAD_ID,
    methodology_question_id: str = QUESTION_ID,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
) -> SanadCreationMapping:
    return SanadCreationMapping(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=claim_id,
        methodology_question_id=methodology_question_id,
        source_span_ids=["span-001"],
        evidence_ids=["evidence-001"],
        primary_evidence_id="evidence-001",
        sanad_id=sanad_id,
        transmission_chain_node_count=2,
        chain_node_types=["INGEST", "EXTRACT"],
        extraction_confidence=0.91,
        dhabt_score=0.88,
    )


def _creation_result(
    mappings: list[SanadCreationMapping] | None = None,
    link_decisions: list[ClaimSanadLinkDecision] | None = None,
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
) -> SanadCreationResult:
    mapped = mappings or [_mapping()]
    links = link_decisions
    if links is None:
        links = [
            ClaimSanadLinkDecision(
                tenant_id=item.tenant_id,
                deal_id=item.deal_id,
                run_id=item.run_id,
                claim_id=item.claim_id,
                methodology_question_id=item.methodology_question_id,
                sanad_id=item.sanad_id,
            )
            for item in mapped
        ]
    return SanadCreationResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=SanadCreationStatus.COMPLETED,
        mappings=mapped,
        rejections=[],
        claim_link_decisions=links,
        summary=SanadCreationSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_readiness_decisions=len(mapped),
            selected_decision_count=len(mapped),
            created_sanad_count=len(mapped),
            rejected_decision_count=0,
            already_created_count=0,
            by_status={SanadCreationStatus.COMPLETED.value: 1},
            by_reason={},
        ),
    )


def _service() -> Any:
    from idis.services.methodology.claim_sanad_link_boundary import (
        ClaimSanadLinkBoundaryService,
    )

    return ClaimSanadLinkBoundaryService()


def test_build_claim_sanad_link_decisions_from_phase_2_8_result() -> None:
    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(),
    )

    assert result.status == ClaimSanadLinkStatus.COMPLETED
    assert result.decisions[0].claim_id == CLAIM_ID
    assert result.decisions[0].sanad_id == SANAD_ID
    assert result.decisions[0].coverage_update_status == "not_applied"
    assert result.summary.to_deterministic_json() == result.summary.to_deterministic_json()


def test_apply_claim_sanad_links_uses_injected_claim_service_update_only() -> None:
    decision_result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(),
    )
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: _claim(claim_grade="D")},
        sanads={SANAD_ID: _sanad()},
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decision_result.decisions,
        claim_service=claim_service,
        request_id="request-phase-2-9",
    )

    assert result.status == ClaimSanadLinkStatus.COMPLETED
    assert len(claim_service.update_calls) == 1
    claim_id, update_input = claim_service.update_calls[0]
    assert claim_id == CLAIM_ID
    assert update_input.sanad_id == SANAD_ID
    assert update_input.request_id == "request-phase-2-9"
    assert result.mappings[0].sanad_id == SANAD_ID
    assert result.mappings[0].ic_bound is False
    assert result.mappings[0].claim_grade == "D"
    assert result.mappings[0].claim_verdict != "VERIFIED"
    assert result.mappings[0].claim_action != "NONE"
    assert result.mappings[0].coverage_update_status == "not_applied"


def test_already_linked_claim_returns_deterministic_rejection_without_update() -> None:
    decision = (
        _service()
        .build_claim_sanad_link_decisions(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            sanad_creation_result=_creation_result(),
        )
        .decisions[0]
    )
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: _claim(sanad_id=SANAD_ID)},
        sanads={SANAD_ID: _sanad()},
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=[decision],
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.ALREADY_LINKED
    assert claim_service.update_calls == []


def test_claim_service_update_failure_fails_closed_and_batch_continues() -> None:
    creation_result = _creation_result(
        mappings=[
            _mapping(claim_id=CLAIM_ID, sanad_id=SANAD_ID),
            _mapping(claim_id=CLAIM_2_ID, sanad_id=SANAD_2_ID),
        ]
    )
    decisions = (
        _service()
        .build_claim_sanad_link_decisions(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            sanad_creation_result=creation_result,
        )
        .decisions
    )
    claim_service = FailingOnceClaimService(
        claims={
            CLAIM_ID: _claim(claim_id=CLAIM_ID),
            CLAIM_2_ID: _claim(claim_id=CLAIM_2_ID),
        },
        sanads={
            SANAD_ID: _sanad(sanad_id=SANAD_ID, claim_id=CLAIM_ID),
            SANAD_2_ID: _sanad(sanad_id=SANAD_2_ID, claim_id=CLAIM_2_ID),
        },
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.SERVICE_UPDATE_FAILED
    assert result.mappings[0].claim_id == CLAIM_2_ID
