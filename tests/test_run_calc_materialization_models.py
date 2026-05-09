"""Tests for Slice 9 run-scoped deterministic calculation models."""

from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal
from uuid import UUID

from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_materialization import (
    MethodologyCalculationMapping,
    MethodologyCalculationRunResult,
    MethodologyCalculationStatus,
    MethodologyCalculationSummary,
    RunScopedCalcSanadRecord,
    RunScopedDeterministicCalculationRecord,
    deterministic_calc_id,
    deterministic_calc_sanad_id,
    deterministic_calc_timestamp,
)
from idis.models.calc_sanad import CalcSanad, SanadGrade
from idis.models.deterministic_calculation import CalcType, DeterministicCalculation

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _engine_result() -> tuple[DeterministicCalculation, CalcSanad]:
    FormulaRegistry.reset_instance()
    registry = register_core_formulas()
    result = CalcEngine(registry=registry, enforce_extraction_gate=False).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": Decimal("1000"), "cogs": Decimal("400")},
        input_grades=[
            InputGradeInfo(claim_id="claim_mth_revenue", grade=SanadGrade.A),
            InputGradeInfo(claim_id="claim_mth_cogs", grade=SanadGrade.B),
        ],
        metadata={"unit": "percent", "currency": "USD", "time_window": "FY2024"},
    )
    calc_id = deterministic_calc_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calc_type=CalcType.GROSS_MARGIN.value,
        input_claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
        formula_hash=result.calculation.formula_hash,
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
    )
    calc_sanad_id = deterministic_calc_sanad_id(calc_id=calc_id)
    calculation = result.calculation.model_copy(
        update={
            "calc_id": calc_id,
            "created_at": deterministic_calc_timestamp(0),
            "updated_at": deterministic_calc_timestamp(0),
        }
    )
    calc_sanad = result.calc_sanad.model_copy(
        update={
            "calc_sanad_id": calc_sanad_id,
            "calc_id": calc_id,
            "created_at": deterministic_calc_timestamp(1),
            "updated_at": deterministic_calc_timestamp(1),
        }
    )
    return calculation, calc_sanad


def test_deterministic_calc_ids_and_timestamps_are_stable() -> None:
    calc_id = deterministic_calc_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calc_type=CalcType.GROSS_MARGIN.value,
        input_claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
        formula_hash="formula-hash",
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
    )
    changed = deterministic_calc_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calc_type=CalcType.RUNWAY.value,
        input_claim_ids=["claim_mth_cash", "claim_mth_burn"],
        formula_hash="formula-hash",
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
    )

    assert UUID(calc_id).version == 5
    assert UUID(deterministic_calc_sanad_id(calc_id=calc_id)).version == 5
    assert calc_id != changed
    assert deterministic_calc_timestamp(1).tzinfo == UTC
    assert deterministic_calc_timestamp(1).isoformat() == "1970-01-01T00:00:01+00:00"


def test_run_scoped_calc_records_reuse_canonical_models_and_safe_shells() -> None:
    calculation, calc_sanad = _engine_result()
    record = RunScopedDeterministicCalculationRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calculation=calculation,
        input_claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
        input_sanad_ids=["sanad-revenue", "sanad-cogs"],
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
        status="created",
    )
    sanad_record = RunScopedCalcSanadRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calc_id=calculation.calc_id,
        calc_sanad=calc_sanad,
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
        status="created",
    )

    assert isinstance(record.calculation, DeterministicCalculation)
    assert isinstance(sanad_record.calc_sanad, CalcSanad)
    assert record.to_shell().calc_id == calculation.calc_id
    assert sanad_record.to_shell().calc_sanad_id == calc_sanad.calc_sanad_id
    assert "explanation" not in sanad_record.to_shell().model_dump(mode="json")


def test_run_step_summary_contains_safe_ids_counts_hashes_and_no_raw_payloads() -> None:
    calculation, calc_sanad = _engine_result()
    mapping = MethodologyCalculationMapping(
        calc_id=calculation.calc_id,
        calc_sanad_id=calc_sanad.calc_sanad_id,
        calc_type=CalcType.GROSS_MARGIN,
        input_claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
        input_sanad_ids=["sanad-revenue", "sanad-cogs"],
        methodology_question_id="mq_unit_economics",
        extraction_task_id="et_unit_economics",
        coverage_record_id="mcr_unit_economics",
        formula_hash=calculation.formula_hash,
        reproducibility_hash=calculation.reproducibility_hash,
        output_primary_value="60.0000",
        output_unit="percent",
        output_currency="USD",
        calc_grade=SanadGrade.B,
        status="created",
    )
    run_result = MethodologyCalculationRunResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=MethodologyCalculationStatus.COMPLETED,
        calculation_mappings=[mapping],
        calculation_shells=[],
        calc_sanad_shells=[],
        rejections=[],
        summary=MethodologyCalculationSummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_requested_calculations=1,
            created_calculation_count=1,
            blocked_count=0,
            by_status={"created": 1},
            by_reason={},
            by_calc_type={"GROSS_MARGIN": 1},
            by_grade={"B": 1},
        ),
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert calculation.calc_id in serialized
    assert calculation.reproducibility_hash in serialized
    assert "60.0000" in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "locator" not in serialized
    assert "document_name" not in serialized
    assert "C:/secret" not in serialized
    assert "explanation" not in serialized
