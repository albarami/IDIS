"""Guardrail tests for Phase 2.9 Claim-Sanad link boundary."""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.claim_sanad_link_boundary import ClaimSanadLinkReason
from idis.models.sanad_creation_boundary import SanadCreationMapping
from tests.test_claim_sanad_link_boundary_service import (
    CLAIM_ID,
    DEAL_ID,
    OTHER_TENANT_ID,
    RUN_ID,
    SANAD_2_ID,
    SANAD_ID,
    TENANT_ID,
    TrackingClaimService,
    _claim,
    _creation_result,
    _mapping,
    _sanad,
    _service,
)


class FailingGetSanadClaimService(TrackingClaimService):
    """ClaimService test double whose Sanad lookup fails."""

    def get_sanad(self, sanad_id: str) -> dict[str, Any] | None:
        raise RuntimeError(f"synthetic get_sanad failure for {sanad_id}")


def _decisions_from_mapping(mapping: SanadCreationMapping) -> list[Any]:
    return _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(mappings=[mapping]),
    ).decisions


def test_missing_claim_id_fails_closed() -> None:
    mapping = _mapping().model_copy(update={"claim_id": ""})

    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(mappings=[mapping], link_decisions=[]),
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.MISSING_CLAIM_ID


def test_missing_sanad_id_fails_closed() -> None:
    mapping = _mapping().model_copy(update={"sanad_id": ""})

    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(mappings=[mapping], link_decisions=[]),
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.MISSING_SANAD_ID


def test_wrong_tenant_fails_closed() -> None:
    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(tenant_id=OTHER_TENANT_ID),
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.TENANT_OR_RUN_MISMATCH


def test_wrong_claim_service_tenant_fails_closed() -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_service = TrackingClaimService(
        tenant_id=OTHER_TENANT_ID,
        claims={CLAIM_ID: _claim()},
        sanads={SANAD_ID: _sanad()},
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.TENANT_OR_SERVICE_MISMATCH
    assert claim_service.update_calls == []


def test_stale_mapping_without_matching_phase_2_8_link_decision_fails_closed() -> None:
    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(mappings=[_mapping()], link_decisions=[]),
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.STALE_MAPPING


def test_mixed_duplicate_batch_keeps_non_conflicting_mapping() -> None:
    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(
            mappings=[
                _mapping(claim_id=CLAIM_ID, sanad_id=SANAD_ID),
                _mapping(claim_id=CLAIM_ID, sanad_id=SANAD_2_ID),
                _mapping(claim_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac"),
            ]
        ),
    )

    assert len(result.rejections) == 2
    assert len(result.decisions) == 1
    assert result.decisions[0].claim_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac"


def test_exact_duplicate_mapping_is_rejected_deterministically() -> None:
    result = _service().build_claim_sanad_link_decisions(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        sanad_creation_result=_creation_result(
            mappings=[
                _mapping(claim_id=CLAIM_ID, sanad_id=SANAD_ID),
                _mapping(claim_id=CLAIM_ID, sanad_id=SANAD_ID),
            ]
        ),
    )

    assert result.decisions == []
    assert len(result.rejections) == 2
    assert {
        rejection.reason for rejection in result.rejections
    } == {ClaimSanadLinkReason.EXISTING_CONFLICTING_SANAD}


def test_existing_conflicting_sanad_id_fails_closed() -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: _claim(sanad_id=SANAD_2_ID)},
        sanads={SANAD_ID: _sanad()},
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.EXISTING_CONFLICTING_SANAD
    assert claim_service.update_calls == []


@pytest.mark.parametrize(
    "claim_override",
    [
        {"tenant_id": OTHER_TENANT_ID},
        {"deal_id": "22222222-2222-2222-2222-222222222299"},
        {"claim_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa99"},
    ],
)
def test_pre_update_claim_scope_mismatch_fails_before_update(
    claim_override: dict[str, Any],
) -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_data = _claim()
    claim_data.update(claim_override)
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: claim_data},
        sanads={SANAD_ID: _sanad()},
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH
    assert claim_service.update_calls == []


def test_missing_sanad_fails_closed() -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_service = TrackingClaimService(claims={CLAIM_ID: _claim()}, sanads={})

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.SANAD_NOT_FOUND
    assert claim_service.update_calls == []


def test_get_sanad_failure_becomes_deterministic_rejection() -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_service = FailingGetSanadClaimService(claims={CLAIM_ID: _claim()})

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == ClaimSanadLinkReason.SANAD_NOT_FOUND
    assert claim_service.update_calls == []


@pytest.mark.parametrize(
    ("update_override", "expected_reason"),
    [
        ({"tenant_id": OTHER_TENANT_ID}, ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH),
        (
            {"deal_id": "22222222-2222-2222-2222-222222222299"},
            ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH,
        ),
        (
            {"claim_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa99"},
            ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH,
        ),
        ({"sanad_id": SANAD_2_ID}, ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT),
        ({"ic_bound": True}, ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT),
        ({"claim_verdict": "VERIFIED"}, ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT),
        ({"claim_action": "NONE"}, ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT),
        ({"claim_grade": "A"}, ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT),
    ],
)
def test_post_update_protected_field_drift_fails_closed(
    update_override: dict[str, Any],
    expected_reason: ClaimSanadLinkReason,
) -> None:
    decisions = _decisions_from_mapping(_mapping())
    claim_service = TrackingClaimService(
        claims={CLAIM_ID: _claim(claim_grade="D")},
        sanads={SANAD_ID: _sanad()},
        update_override=update_override,
    )

    result = _service().apply_claim_sanad_links(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        link_decisions=decisions,
        claim_service=claim_service,
    )

    assert result.rejections[0].reason == expected_reason
    assert result.mappings == []
