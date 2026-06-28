"""Slice87 Task 3 — formula coverage for MOIC, VALUATION_MULTIPLE, NRR, CAC_PAYBACK, LTV.

RED-first: the production formula registry implements only 4 of the 10 CalcTypes. Task 3 adds the
5 approved deterministic formulas through the existing FormulaSpec/registry idiom, taking the
registry to 9/10 (only IRR remains deferred — there is no cash-flow-series input model and one must
not be invented here). Each formula is Decimal-safe, deterministic, reproducibility-hash compatible,
and surfaces formula_hash/version through the existing DeterministicCalculation model path. Missing
required inputs and zero-denominators preserve fail-closed behavior.

Formula-only: no graph/RAG, no financial tables, no deliverable changes, no DB migration, no real
FULL run, no Slice88.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from idis.calc.engine import CalcEngine, CalcMissingInputError, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"

# (calc_type, valid_inputs, expected_primary, missing_key, zero_denominator_inputs)
NEW_FORMULAS: list[tuple[CalcType, dict[str, str], str, str, dict[str, str]]] = [
    (
        CalcType.MOIC,
        {"total_value": "300", "invested_capital": "100"},
        "3.0000",
        "invested_capital",
        {"total_value": "300", "invested_capital": "0"},
    ),
    (
        CalcType.VALUATION_MULTIPLE,
        {"valuation": "1000", "revenue": "200"},
        "5.0000",
        "revenue",
        {"valuation": "1000", "revenue": "0"},
    ),
    (
        CalcType.NET_REVENUE_RETENTION,
        {"starting_arr": "1000", "expansion": "200", "contraction": "50", "churn": "100"},
        "105.0000",
        "starting_arr",
        {"starting_arr": "0", "expansion": "200", "contraction": "50", "churn": "100"},
    ),
    (
        CalcType.CAC_PAYBACK,
        {"cac": "12000", "monthly_gross_profit": "1000"},
        "12.0000",
        "monthly_gross_profit",
        {"cac": "12000", "monthly_gross_profit": "0"},
    ),
    (
        CalcType.LTV,
        {"arpa": "100", "gross_margin_rate": "0.8", "churn_rate": "0.05"},
        "1600.0000",
        "churn_rate",
        {"arpa": "100", "gross_margin_rate": "0.8", "churn_rate": "0"},
    ),
]

_PARAMS = [pytest.param(row, id=row[0].value) for row in NEW_FORMULAS]


def _decimals(values: dict[str, str]) -> dict[str, Decimal]:
    return {key: Decimal(raw) for key, raw in values.items()}


@pytest.fixture
def engine() -> CalcEngine:
    FormulaRegistry.reset_instance()
    registry = register_core_formulas(FormulaRegistry())
    return CalcEngine(registry=registry, code_version="test-1.0.0", enforce_extraction_gate=False)


# --- registry coverage: 9 of 10, only IRR deferred ---


def test_registry_implements_nine_only_irr_deferred() -> None:
    FormulaRegistry.reset_instance()
    registered = set(register_core_formulas(FormulaRegistry()).list_registered())
    assert registered == set(CalcType) - {CalcType.IRR}
    assert len(registered) == 9
    assert CalcType.IRR not in registered  # deferred: no cash-flow-series input model exists


# --- per-formula behavior: valid, missing-input blocked, zero-denominator blocked ---


@pytest.mark.parametrize("row", _PARAMS)
def test_new_formula_valid_calculation(
    engine: CalcEngine, row: tuple[CalcType, dict[str, str], str, str, dict[str, str]]
) -> None:
    calc_type, valid_inputs, expected_primary, _missing, _zero = row
    result = engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=calc_type,
        input_values=_decimals(valid_inputs),
        input_grades=[InputGradeInfo(claim_id=f"claim-{calc_type.value}", grade=SanadGrade.A)],
    )
    assert result.calculation.calc_type == calc_type
    assert result.calculation.output.primary_value == Decimal(expected_primary)


@pytest.mark.parametrize("row", _PARAMS)
def test_new_formula_missing_required_input_blocked(
    engine: CalcEngine, row: tuple[CalcType, dict[str, str], str, str, dict[str, str]]
) -> None:
    calc_type, valid_inputs, _expected, missing_key, _zero = row
    incomplete = {k: v for k, v in valid_inputs.items() if k != missing_key}
    with pytest.raises(CalcMissingInputError):
        engine.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            calc_type=calc_type,
            input_values=_decimals(incomplete),
            input_grades=[InputGradeInfo(claim_id="claim-x", grade=SanadGrade.A)],
        )


@pytest.mark.parametrize("row", _PARAMS)
def test_new_formula_zero_denominator_blocked(
    engine: CalcEngine, row: tuple[CalcType, dict[str, str], str, str, dict[str, str]]
) -> None:
    calc_type, _valid, _expected, _missing, zero_inputs = row
    with pytest.raises(ValueError):
        engine.run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            calc_type=calc_type,
            input_values=_decimals(zero_inputs),
            input_grades=[InputGradeInfo(claim_id="claim-x", grade=SanadGrade.A)],
        )


@pytest.mark.parametrize("row", _PARAMS)
def test_new_formula_surfaces_version_hash_through_model_path(
    engine: CalcEngine, row: tuple[CalcType, dict[str, str], str, str, dict[str, str]]
) -> None:
    calc_type, valid_inputs, _expected, _missing, _zero = row
    result = engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=calc_type,
        input_values=_decimals(valid_inputs),
        input_grades=[InputGradeInfo(claim_id="claim-x", grade=SanadGrade.A)],
    )
    calc = result.calculation
    # formula_hash + code_version + reproducibility_hash flow through the existing model path.
    assert len(calc.formula_hash) == 64
    assert calc.code_version == "test-1.0.0"
    assert len(calc.reproducibility_hash) == 64
    # formula_hash is unique to this calc_type's spec (calc_type/version/expression_id).
    spec = engine._registry.get_or_raise(calc_type)
    assert calc.formula_hash == spec.formula_hash


def test_new_formula_hashes_are_distinct_across_types(engine: CalcEngine) -> None:
    hashes = {
        calc_type: engine._registry.get_or_raise(calc_type).formula_hash
        for calc_type, *_ in NEW_FORMULAS
    }
    assert len(set(hashes.values())) == len(hashes)  # no hash collisions across new formulas
