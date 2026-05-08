"""Strict UpdateClaimInput contract tests for Phase 2.9 link application."""

from __future__ import annotations

from tests.test_claim_sanad_link_boundary_service import (
    CLAIM_ID,
    DEAL_ID,
    RUN_ID,
    SANAD_ID,
    TENANT_ID,
    TrackingClaimService,
    _claim,
    _creation_result,
    _sanad,
    _service,
)


def test_apply_passes_only_sanad_id_and_request_id_to_claim_service_update() -> None:
    decision_result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(),
    )
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: _claim()},
        sanads={SANAD_ID: _sanad()},
    )

    _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decision_result.decisions,
        claim_service=claim_service,
        request_id="phase-2-9-request",
    )

    _, update_input = claim_service.update_calls[0]
    assert update_input.model_dump(exclude_none=True) == {
        "sanad_id": SANAD_ID,
        "request_id": "phase-2-9-request",
    }
    assert update_input.ic_bound is None
    assert update_input.claim_verdict is None
    assert update_input.claim_action is None
    assert update_input.claim_grade is None
