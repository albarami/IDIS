"""Tests for truthful CALC runner behavior."""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.deterministic_calculation import CalcType
from idis.services.calc.runner import CalcRunner

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


class FakeClaimsRepository:
    """Claim repository test double."""

    def __init__(self, claims: dict[str, dict[str, Any]]) -> None:
        self._claims = claims

    def get(self, claim_id: str) -> dict[str, Any] | None:
        return self._claims.get(claim_id)


class FakeSanadsRepository:
    """Sanad repository test double."""

    def __init__(self, sanads: dict[str, dict[str, Any]]) -> None:
        self._sanads = sanads

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        return self._sanads.get(claim_id)


class FakeCalculationsRepository:
    """Calculation repository test double."""

    def __init__(self, *, fail_on_create: bool = False) -> None:
        self.fail_on_create = fail_on_create
        self.created: list[tuple[object, object]] = []

    def create(self, *, calculation: object, calc_sanad: object) -> None:
        if self.fail_on_create:
            raise RuntimeError("database unavailable")
        self.created.append((calculation, calc_sanad))


def _money_claim(claim_id: str, predicate: str, amount: str) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "claim_class": "FINANCIAL",
        "claim_text": f"{predicate} was {amount}",
        "predicate": predicate,
        "value": {
            "type": "monetary",
            "amount": amount,
            "currency": "USD",
        },
        "claim_grade": "A",
        "materiality": "HIGH",
    }


def _sanad(
    claim_id: str,
    *,
    confidence: str = "0.99",
    dhabt: str = "0.95",
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "computed": {
            "extraction_confidence": confidence,
            "dhabt_score": dhabt,
        },
    }


def _runner(
    *,
    claims: dict[str, dict[str, Any]],
    sanads: dict[str, dict[str, Any]],
    calculations_repo: FakeCalculationsRepository | None = None,
) -> CalcRunner:
    return CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=calculations_repo or FakeCalculationsRepository(),
    )


def test_calc_runner_persists_eligible_deterministic_calculation_and_calc_sanad() -> None:
    """Eligible evidence-backed inputs produce durable calc and CalcSanad records."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert len(result["calc_ids"]) == 1
    assert len(result["reproducibility_hashes"]) == 1
    assert result["blocked_candidates"] == []
    assert len(calculations_repo.created) == 1
    calculation, calc_sanad = calculations_repo.created[0]
    assert calculation.calc_type == CalcType.GROSS_MARGIN
    assert calculation.output.primary_value == 60
    assert calc_sanad.calc_id == calculation.calc_id


def test_calc_runner_maps_real_extraction_claim_shape_to_formula_inputs() -> None:
    """Claims without explicit predicates can still feed formulas via claim_class/text."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": {
            **_money_claim("claim-revenue", "unused", "1000"),
            "predicate": None,
            "claim_class": "REVENUE",
            "value": {"type": "monetary", "value": "1000", "currency": "USD"},
        },
        "claim-cogs": {
            **_money_claim("claim-cogs", "unused", "400"),
            "predicate": None,
            "claim_class": "COST_OF_GOODS_SOLD",
            "value": {"type": "monetary", "value": "400", "currency": "USD"},
        },
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []


def test_calc_runner_completes_truthfully_when_no_inputs_are_eligible() -> None:
    """No eligible inputs is a truthful completed CALC summary, not fake calc IDs."""
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }

    result = _runner(claims=claims, sanads={}).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 0
    assert result["calc_ids"] == []
    assert result["reproducibility_hashes"] == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "missing_source_metadata",
            "claim_ids": ["claim-cogs", "claim-revenue"],
        }
    ]


def test_calc_runner_persists_eligible_candidates_and_reports_blocked_candidates() -> None:
    """Mixed eligible/ineligible candidates persist real calcs and report skipped ones."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN, CalcType.RUNWAY],
    )

    assert result["persisted_count"] == 1
    assert len(result["calc_ids"]) == 1
    assert result["blocked_candidates"] == [
        {
            "calc_type": "RUNWAY",
            "reason": "missing_required_claim",
            "missing_inputs": ["cash_balance", "monthly_burn_rate"],
        }
    ]


def test_calc_runner_fails_closed_on_persistence_errors() -> None:
    """System and DB errors must fail closed rather than becoming skipped candidates."""
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }
    runner = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=FakeCalculationsRepository(fail_on_create=True),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        runner.run(
            created_claim_ids=list(claims),
            calc_types=[CalcType.GROSS_MARGIN],
        )
