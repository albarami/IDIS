"""Tests for deterministic calculation repository serialization."""

from __future__ import annotations

from decimal import Decimal

from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


def _calc_result() -> object:
    FormulaRegistry.reset_instance()
    registry = register_core_formulas()
    engine = CalcEngine(registry=registry, enforce_extraction_gate=False)
    return engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={
            "revenue": Decimal("1000"),
            "cogs": Decimal("400"),
        },
        input_grades=[
            InputGradeInfo(claim_id="claim-revenue", grade=SanadGrade.A),
            InputGradeInfo(claim_id="claim-cogs", grade=SanadGrade.A),
        ],
    )


def test_in_memory_calculations_repository_persists_calculation_and_calc_sanad() -> None:
    """Repository stores real calculation rows and linked CalcSanad rows."""
    clear_in_memory_calculations_store()
    repo = InMemoryCalculationsRepository(TENANT_ID)
    result = _calc_result()

    repo.create(calculation=result.calculation, calc_sanad=result.calc_sanad)

    calculations = repo.list_by_deal(DEAL_ID)
    sanads = repo.list_calc_sanads_by_deal(DEAL_ID)

    assert [item["calc_id"] for item in calculations] == [result.calculation.calc_id]
    assert calculations[0]["reproducibility_hash"] == result.calculation.reproducibility_hash
    assert [item["calc_id"] for item in sanads] == [result.calculation.calc_id]
    assert sanads[0]["calc_grade"] == "A"
