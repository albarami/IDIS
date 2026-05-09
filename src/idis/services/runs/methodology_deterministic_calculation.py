"""Slice 9 in-memory deterministic calculation run service."""

from __future__ import annotations

from decimal import Decimal

from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_materialization import (
    MethodologyCalculationMapping,
    MethodologyCalculationReason,
    MethodologyCalculationRejection,
    MethodologyCalculationRunResult,
    MethodologyCalculationSummary,
    RunScopedCalcSanadRecord,
    RunScopedDeterministicCalculationRecord,
    aggregate_status,
    counter,
    deterministic_calc_id,
    deterministic_calc_sanad_id,
    deterministic_calc_timestamp,
)
from idis.models.calc_sanad import SanadGrade as CalcSanadGrade
from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.deterministic_calculation import CalcType
from idis.models.extraction_task import ExtractionTask
from idis.models.sanad_materialization import (
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.services.runs.methodology_deterministic_calculation_helpers import (
    claims_by_input_key,
    claims_for_task,
    decimal_value_from_claim,
    gate_rejection,
    input_key_from_claim,
    metadata_for_calc,
    rejection,
    requested_calculations,
    scope_claims,
    scope_grades,
    scope_sanads,
)
from idis.validators.extraction_gate import ExtractionGateBlockedError


class InMemoryRunMethodologyDeterministicCalculationService:
    """Run deterministic calculations over Slice 6/8 in-memory boundaries."""

    def __init__(self, *, registry: FormulaRegistry | None = None) -> None:
        """Initialize the service with the existing formula registry."""
        self._registry = register_core_formulas(registry)
        self._engine = CalcEngine(registry=self._registry)

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
        sanads: list[RunScopedSanadRecord | RunScopedSanadShell],
        sanad_grades: list[RunScopedSanadGradeRecord],
        extraction_tasks: list[ExtractionTask],
    ) -> tuple[
        MethodologyCalculationRunResult,
        list[RunScopedDeterministicCalculationRecord],
        list[RunScopedCalcSanadRecord],
    ]:
        """Run Slice 9 deterministic calculations in memory."""
        records: list[RunScopedDeterministicCalculationRecord] = []
        calc_sanads: list[RunScopedCalcSanadRecord] = []
        rejections: list[MethodologyCalculationRejection] = []

        requested = requested_calculations(extraction_tasks)
        if not materialized_claims and not requested:
            return self._result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_requested=0,
                records=[],
                calc_sanads=[],
                rejections=[],
            )
        if not materialized_claims:
            return self._result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_requested=len(requested),
                records=[],
                calc_sanads=[],
                rejections=[],
            )

        claims = scope_claims(
            materialized_claims,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        grades = scope_grades(
            sanad_grades,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        scoped_sanads = scope_sanads(
            sanads,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        grades_by_claim = {grade.claim_id: grade for grade in grades}
        sanads_by_claim = {
            sanad.claim_id: sanad
            for sanad in scoped_sanads
            if isinstance(sanad, RunScopedSanadRecord)
        }
        seen: set[tuple[str, tuple[str, ...], str, str]] = set()

        for task, required_calc in requested:
            task_claims = claims_for_task(claims, task)
            claims_by_input = claims_by_input_key(task_claims)
            calc_type_text = required_calc.calc_type
            try:
                calc_type = CalcType(calc_type_text)
            except ValueError:
                rejections.append(
                    rejection(
                        reason=MethodologyCalculationReason.UNSUPPORTED_CALC_TYPE,
                        message="methodology calculation type is not recognized",
                        calc_type=calc_type_text,
                        task=task,
                        required=required_calc.required,
                    )
                )
                continue

            spec = self._registry.get(calc_type)
            if spec is None:
                rejections.append(
                    rejection(
                        reason=MethodologyCalculationReason.UNSUPPORTED_CALC_TYPE,
                        message="methodology calculation type is not registered",
                        calc_type=calc_type.value,
                        task=task,
                        required=required_calc.required,
                    )
                )
                continue

            missing_inputs = [
                input_key for input_key in spec.required_inputs if input_key not in claims_by_input
            ]
            if missing_inputs:
                rejections.append(
                    rejection(
                        reason=MethodologyCalculationReason.MISSING_REQUIRED_CLAIM,
                        message="required formula inputs are missing",
                        calc_type=calc_type.value,
                        task=task,
                        required=required_calc.required,
                        reason_codes=[MethodologyCalculationReason.MISSING_REQUIRED_CLAIM.value],
                        claim_ids=[],
                    )
                )
                continue

            input_claims = [claims_by_input[input_key] for input_key in spec.required_inputs]
            input_claim_ids = sorted(
                str(claim.claim_id) for claim in input_claims if claim.claim_id
            )
            dedupe_key = (
                calc_type.value,
                tuple(input_claim_ids),
                task.methodology_question_id,
                task.extraction_task_id or "",
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            candidate = self._build_inputs(
                calc_type=calc_type,
                input_claims=input_claims,
                grades_by_claim=grades_by_claim,
                sanads_by_claim=sanads_by_claim,
                required=required_calc.required,
                task=task,
            )
            if isinstance(candidate, MethodologyCalculationRejection):
                rejections.append(candidate)
                continue
            input_values, input_grades, input_sanad_ids = candidate

            try:
                engine_result = self._engine.run(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    calc_type=calc_type,
                    input_values=input_values,
                    input_grades=input_grades,
                    metadata=metadata_for_calc(calc_type, input_claims),
                )
            except ExtractionGateBlockedError as exc:
                rejections.append(gate_rejection(calc_type, exc, task, required_calc.required))
                continue
            except (ArithmeticError, TypeError, ValueError):
                rejections.append(
                    rejection(
                        reason=MethodologyCalculationReason.CALCULATION_FAILED,
                        message="deterministic calculation engine failed",
                        calc_type=calc_type.value,
                        task=task,
                        required=required_calc.required,
                        claim_ids=input_claim_ids,
                    )
                )
                continue

            calc_id = deterministic_calc_id(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                calc_type=calc_type.value,
                input_claim_ids=input_claim_ids,
                formula_hash=engine_result.calculation.formula_hash,
                methodology_question_id=task.methodology_question_id,
                extraction_task_id=task.extraction_task_id or "",
                coverage_record_id=task.coverage_record_id or "",
            )
            calc_sanad_id = deterministic_calc_sanad_id(calc_id=calc_id)
            calculation = engine_result.calculation.model_copy(
                update={
                    "calc_id": calc_id,
                    "created_at": deterministic_calc_timestamp(0),
                    "updated_at": deterministic_calc_timestamp(0),
                }
            )
            calc_sanad = engine_result.calc_sanad.model_copy(
                update={
                    "calc_sanad_id": calc_sanad_id,
                    "calc_id": calc_id,
                    "created_at": deterministic_calc_timestamp(1),
                    "updated_at": deterministic_calc_timestamp(1),
                }
            )
            records.append(
                RunScopedDeterministicCalculationRecord(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    calculation=calculation,
                    input_claim_ids=input_claim_ids,
                    input_sanad_ids=input_sanad_ids,
                    methodology_question_id=task.methodology_question_id,
                    extraction_task_id=task.extraction_task_id or "",
                    coverage_record_id=task.coverage_record_id or "",
                    status="created",
                )
            )
            calc_sanads.append(
                RunScopedCalcSanadRecord(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    calc_id=calc_id,
                    calc_sanad=calc_sanad,
                    methodology_question_id=task.methodology_question_id,
                    extraction_task_id=task.extraction_task_id or "",
                    coverage_record_id=task.coverage_record_id or "",
                    status="created",
                )
            )

        return self._result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_requested=len(requested),
            records=records,
            calc_sanads=calc_sanads,
            rejections=rejections,
        )

    def _build_inputs(
        self,
        *,
        calc_type: CalcType,
        input_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
        grades_by_claim: dict[str, RunScopedSanadGradeRecord],
        sanads_by_claim: dict[str, RunScopedSanadRecord],
        required: bool,
        task: ExtractionTask,
    ) -> (
        tuple[dict[str, Decimal], list[InputGradeInfo], list[str]] | MethodologyCalculationRejection
    ):
        input_values: dict[str, Decimal] = {}
        input_grades: list[InputGradeInfo] = []
        input_sanad_ids: list[str] = []
        claim_ids = sorted(str(claim.claim_id) for claim in input_claims if claim.claim_id)
        for claim in input_claims:
            input_key = input_key_from_claim(claim)
            value = decimal_value_from_claim(claim)
            if input_key is None or value is None:
                return rejection(
                    reason=MethodologyCalculationReason.MISSING_SOURCE_METADATA,
                    message="claim value metadata cannot feed deterministic calculation",
                    calc_type=calc_type.value,
                    task=task,
                    required=required,
                    claim_ids=claim_ids,
                )
            claim_id = str(claim.claim_id)
            grade = grades_by_claim.get(claim_id)
            if grade is None:
                return rejection(
                    reason=MethodologyCalculationReason.MISSING_SANAD_GRADE,
                    message="claim is missing Slice 8 Sanad grade",
                    calc_type=calc_type.value,
                    task=task,
                    required=required,
                    claim_ids=claim_ids,
                )
            sanad = sanads_by_claim.get(claim_id)
            if sanad is None or sanad.sanad.dhabt_score is None:
                return rejection(
                    reason=MethodologyCalculationReason.MISSING_SOURCE_METADATA,
                    message="claim is missing Slice 8 Sanad extraction confidence metadata",
                    calc_type=calc_type.value,
                    task=task,
                    required=required,
                    claim_ids=claim_ids,
                )
            input_values[input_key] = value
            input_sanad_ids.append(grade.sanad_id)
            input_grades.append(
                InputGradeInfo(
                    claim_id=claim_id,
                    grade=CalcSanadGrade(grade.sanad_grade.value),
                    is_material=True,
                    extraction_confidence=Decimal(str(sanad.sanad.extraction_confidence)),
                    dhabt_score=Decimal(str(sanad.sanad.dhabt_score)),
                )
            )
        return input_values, input_grades, sorted(input_sanad_ids)

    def _result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        total_requested: int,
        records: list[RunScopedDeterministicCalculationRecord],
        calc_sanads: list[RunScopedCalcSanadRecord],
        rejections: list[MethodologyCalculationRejection],
    ) -> tuple[
        MethodologyCalculationRunResult,
        list[RunScopedDeterministicCalculationRecord],
        list[RunScopedCalcSanadRecord],
    ]:
        mappings = [
            MethodologyCalculationMapping.from_records(record, calc_sanad)
            for record, calc_sanad in zip(records, calc_sanads, strict=True)
        ]
        run_result = MethodologyCalculationRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=aggregate_status(mappings=mappings, rejections=rejections),
            calculation_mappings=mappings,
            calculation_shells=[record.to_shell() for record in records],
            calc_sanad_shells=[record.to_shell() for record in calc_sanads],
            rejections=rejections,
            summary=MethodologyCalculationSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_requested_calculations=total_requested,
                created_calculation_count=len(records),
                blocked_count=len(rejections),
                by_status=counter(
                    ["created" for _record in records] + ["blocked" for _rejection in rejections]
                ),
                by_reason=counter([rejection.reason.value for rejection in rejections]),
                by_calc_type=counter([mapping.calc_type.value for mapping in mappings]),
                by_grade=counter([mapping.calc_grade.value for mapping in mappings]),
            ),
        )
        return run_result, records, calc_sanads
