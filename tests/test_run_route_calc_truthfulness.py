"""Run-route CALC truthfulness tests."""

from __future__ import annotations

from idis.api.routes.runs import _run_snapshot_calc
from idis.models.deterministic_calculation import CalcType
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.persistence.repositories.claims import (
    InMemoryClaimsRepository,
    InMemorySanadsRepository,
    clear_all_claims_stores,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


def test_run_snapshot_calc_persists_real_calc_and_calc_sanad_for_eligible_inputs() -> None:
    """API CALC hook must invoke the real runner and persist durable results."""
    clear_all_claims_stores()
    clear_in_memory_calculations_store()
    claims_repo = InMemoryClaimsRepository(TENANT_ID)
    sanads_repo = InMemorySanadsRepository(TENANT_ID)

    claims_repo.create(
        claim_id="claim-revenue",
        deal_id=DEAL_ID,
        claim_class="FINANCIAL",
        claim_text="Revenue was 1000",
        predicate="revenue",
        value={"type": "monetary", "amount": "1000", "currency": "USD"},
        claim_grade="A",
        materiality="HIGH",
    )
    claims_repo.create(
        claim_id="claim-cogs",
        deal_id=DEAL_ID,
        claim_class="FINANCIAL",
        claim_text="COGS was 400",
        predicate="cogs",
        value={"type": "monetary", "amount": "400", "currency": "USD"},
        claim_grade="A",
        materiality="HIGH",
    )
    for claim_id in ["claim-revenue", "claim-cogs"]:
        sanads_repo.create(
            sanad_id=f"sanad-{claim_id}",
            claim_id=claim_id,
            deal_id=DEAL_ID,
            primary_evidence_id=f"evidence-{claim_id}",
            computed={"extraction_confidence": "0.99", "dhabt_score": "0.95"},
        )

    result = _run_snapshot_calc(
        run_id="run-1",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
        db_conn=None,
    )

    calculations_repo = InMemoryCalculationsRepository(TENANT_ID)
    assert result["persisted_count"] == 1
    assert len(result["calc_ids"]) == 1
    assert result["blocked_candidates"] == []
    assert len(calculations_repo.list_by_deal(DEAL_ID)) == 1
    assert len(calculations_repo.list_calc_sanads_by_deal(DEAL_ID)) == 1


def test_run_snapshot_calc_returns_blocked_summary_without_fake_calc_ids() -> None:
    """No eligible inputs must be explicit and must not fabricate calc IDs."""
    clear_all_claims_stores()
    clear_in_memory_calculations_store()

    result = _run_snapshot_calc(
        run_id="run-1",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        created_claim_ids=[],
        calc_types=[CalcType.GROSS_MARGIN],
        db_conn=None,
    )

    assert result["persisted_count"] == 0
    assert result["calc_ids"] == []
    assert result["reproducibility_hashes"] == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "missing_required_claim",
            "missing_inputs": ["revenue", "cogs"],
        }
    ]
