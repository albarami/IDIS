"""Slice 9 run-scoped deterministic calculation models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.calc_sanad import CalcSanad, SanadGrade
from idis.models.deterministic_calculation import CalcType, DeterministicCalculation

CALC_NAMESPACE = UUID("6cfd65ff-7d24-54d4-9ce4-df4a907ec88e")
CALC_SANAD_NAMESPACE = UUID("a31db11d-f364-5df4-948d-894cc4f95ed9")
CALC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MethodologyCalculationStatus(StrEnum):
    """Aggregate Slice 9 calculation status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MethodologyCalculationReason(StrEnum):
    """Machine-readable Slice 9 calculation reasons."""

    MISSING_MATERIALIZED_CLAIMS = "missing_materialized_claims"
    MISSING_SANAD_GRADES = "missing_sanad_grades"
    MISSING_EXTRACTION_TASKS = "missing_extraction_tasks"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    NO_REQUESTED_CALCULATIONS = "no_requested_calculations"
    UNSUPPORTED_CALC_TYPE = "unsupported_calc_type"
    MISSING_REQUIRED_CLAIM = "missing_required_claim"
    MISSING_SANAD_GRADE = "missing_sanad_grade"
    MISSING_SOURCE_METADATA = "missing_source_metadata"
    BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    BELOW_DHABT_THRESHOLD = "below_dhabt_threshold"
    CALCULATION_FAILED = "calculation_failed"


class CalculationMaterializationBaseModel(BaseModel):
    """Base model for Slice 9 deterministic data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedCalculationShell(CalculationMaterializationBaseModel):
    """Safe resume shell for a run-scoped deterministic calculation."""

    tenant_id: str
    deal_id: str
    run_id: str
    calc_id: str
    calc_type: CalcType
    input_claim_ids: list[str]
    input_sanad_ids: list[str]
    formula_hash: str
    reproducibility_hash: str
    output_primary_value: str
    output_unit: str | None = None
    output_currency: str | None = None
    methodology_question_id: str
    extraction_task_id: str
    coverage_record_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "calc_id",
        "formula_hash",
        "reproducibility_hash",
        "output_primary_value",
        "methodology_question_id",
        "extraction_task_id",
        "coverage_record_id",
        "status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("input_claim_ids", "input_sanad_ids")
    @classmethod
    def _string_list_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedDeterministicCalculationRecord(CalculationMaterializationBaseModel):
    """In-memory governed deterministic calculation boundary for Slice 9."""

    tenant_id: str
    deal_id: str
    run_id: str
    calculation: DeterministicCalculation
    input_claim_ids: list[str]
    input_sanad_ids: list[str]
    methodology_question_id: str
    extraction_task_id: str
    coverage_record_id: str
    status: str

    @model_validator(mode="after")
    def _calculation_scope_matches_record(self) -> RunScopedDeterministicCalculationRecord:
        if self.calculation.tenant_id != self.tenant_id:
            raise ValueError("calculation tenant_id must match record tenant_id")
        if self.calculation.deal_id != self.deal_id:
            raise ValueError("calculation deal_id must match record deal_id")
        return self

    def to_shell(self) -> RunScopedCalculationShell:
        """Build a safe shell without raw input values."""
        return RunScopedCalculationShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            calc_id=self.calculation.calc_id,
            calc_type=self.calculation.calc_type,
            input_claim_ids=list(self.input_claim_ids),
            input_sanad_ids=list(self.input_sanad_ids),
            formula_hash=self.calculation.formula_hash,
            reproducibility_hash=self.calculation.reproducibility_hash,
            output_primary_value=str(self.calculation.output.primary_value),
            output_unit=self.calculation.output.unit,
            output_currency=self.calculation.output.currency,
            methodology_question_id=self.methodology_question_id,
            extraction_task_id=self.extraction_task_id,
            coverage_record_id=self.coverage_record_id,
            status=self.status,
        )


class RunScopedCalcSanadShell(CalculationMaterializationBaseModel):
    """Safe resume shell for a run-scoped CalcSanad."""

    tenant_id: str
    deal_id: str
    run_id: str
    calc_id: str
    calc_sanad_id: str
    input_claim_ids: list[str]
    input_min_sanad_grade: SanadGrade
    calc_grade: SanadGrade
    methodology_question_id: str
    extraction_task_id: str
    coverage_record_id: str
    status: str


class RunScopedCalcSanadRecord(CalculationMaterializationBaseModel):
    """In-memory governed CalcSanad wrapper for Slice 9."""

    tenant_id: str
    deal_id: str
    run_id: str
    calc_id: str
    calc_sanad: CalcSanad
    methodology_question_id: str
    extraction_task_id: str
    coverage_record_id: str
    status: str

    @model_validator(mode="after")
    def _calc_sanad_scope_matches_record(self) -> RunScopedCalcSanadRecord:
        if self.calc_sanad.tenant_id != self.tenant_id:
            raise ValueError("calc_sanad tenant_id must match record tenant_id")
        if self.calc_sanad.calc_id != self.calc_id:
            raise ValueError("calc_sanad calc_id must match record calc_id")
        return self

    def to_shell(self) -> RunScopedCalcSanadShell:
        """Build a safe shell without verbose grade explanation."""
        return RunScopedCalcSanadShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            calc_id=self.calc_id,
            calc_sanad_id=self.calc_sanad.calc_sanad_id,
            input_claim_ids=list(self.calc_sanad.input_claim_ids),
            input_min_sanad_grade=self.calc_sanad.input_min_sanad_grade,
            calc_grade=self.calc_sanad.calc_grade,
            methodology_question_id=self.methodology_question_id,
            extraction_task_id=self.extraction_task_id,
            coverage_record_id=self.coverage_record_id,
            status=self.status,
        )


class MethodologyCalculationMapping(CalculationMaterializationBaseModel):
    """Summary-safe calculation mapping."""

    calc_id: str
    calc_sanad_id: str
    calc_type: CalcType
    input_claim_ids: list[str]
    input_sanad_ids: list[str]
    methodology_question_id: str
    extraction_task_id: str
    coverage_record_id: str
    formula_hash: str
    reproducibility_hash: str
    output_primary_value: str
    output_unit: str | None = None
    output_currency: str | None = None
    calc_grade: SanadGrade
    status: str

    @classmethod
    def from_records(
        cls,
        calculation_record: RunScopedDeterministicCalculationRecord,
        calc_sanad_record: RunScopedCalcSanadRecord,
    ) -> MethodologyCalculationMapping:
        """Build a summary-safe mapping from run-scoped records."""
        calculation = calculation_record.calculation
        return cls(
            calc_id=calculation.calc_id,
            calc_sanad_id=calc_sanad_record.calc_sanad.calc_sanad_id,
            calc_type=calculation.calc_type,
            input_claim_ids=list(calculation_record.input_claim_ids),
            input_sanad_ids=list(calculation_record.input_sanad_ids),
            methodology_question_id=calculation_record.methodology_question_id,
            extraction_task_id=calculation_record.extraction_task_id,
            coverage_record_id=calculation_record.coverage_record_id,
            formula_hash=calculation.formula_hash,
            reproducibility_hash=calculation.reproducibility_hash,
            output_primary_value=str(calculation.output.primary_value),
            output_unit=calculation.output.unit,
            output_currency=calculation.output.currency,
            calc_grade=calc_sanad_record.calc_sanad.calc_grade,
            status=calculation_record.status,
        )


class MethodologyCalculationRejection(CalculationMaterializationBaseModel):
    """Stable reason-coded Slice 9 rejection/blocker."""

    calc_type: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    methodology_question_id: str | None = None
    extraction_task_id: str | None = None
    coverage_record_id: str | None = None
    reason: MethodologyCalculationReason
    reason_codes: list[str]
    message: str
    required: bool = True

    @model_validator(mode="after")
    def _reason_codes_include_reason(self) -> MethodologyCalculationRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyCalculationSummary(CalculationMaterializationBaseModel):
    """Safe aggregate summary for Slice 9."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_requested_calculations: int
    created_calculation_count: int
    blocked_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    by_calc_type: dict[str, int]
    by_grade: dict[str, int]


class MethodologyCalculationRunResult(CalculationMaterializationBaseModel):
    """Run-step-safe Slice 9 result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyCalculationStatus
    calculation_mappings: list[MethodologyCalculationMapping] = Field(default_factory=list)
    calculation_shells: list[RunScopedCalculationShell] = Field(default_factory=list)
    calc_sanad_shells: list[RunScopedCalcSanadShell] = Field(default_factory=list)
    rejections: list[MethodologyCalculationRejection] = Field(default_factory=list)
    summary: MethodologyCalculationSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without raw claim values or verbose explanations."""
        return {
            "status": status or self.status.value,
            "calc_ids": [mapping.calc_id for mapping in self.calculation_mappings],
            "calc_sanad_ids": [mapping.calc_sanad_id for mapping in self.calculation_mappings],
            "input_claim_ids": sorted(
                {
                    claim_id
                    for mapping in self.calculation_mappings
                    for claim_id in mapping.input_claim_ids
                }
            ),
            "input_sanad_ids": sorted(
                {
                    sanad_id
                    for mapping in self.calculation_mappings
                    for sanad_id in mapping.input_sanad_ids
                }
            ),
            "calculation_mappings": [
                mapping.model_dump(mode="json") for mapping in self.calculation_mappings
            ],
            "calculation_shells": [
                shell.model_dump(mode="json") for shell in self.calculation_shells
            ],
            "calc_sanad_shells": [
                shell.model_dump(mode="json") for shell in self.calc_sanad_shells
            ],
            "rejections": [rejection.model_dump(mode="json") for rejection in self.rejections],
            "summary": {
                "total_requested_calculations": self.summary.total_requested_calculations,
                "created_calculation_count": self.summary.created_calculation_count,
                "blocked_count": self.summary.blocked_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
                "by_calc_type": dict(self.summary.by_calc_type),
                "by_grade": dict(self.summary.by_grade),
            },
        }


def deterministic_calc_timestamp(ordinal: int) -> datetime:
    """Return a deterministic synthetic timestamp for a calculation ordinal."""
    return CALC_EPOCH + timedelta(seconds=ordinal)


def deterministic_calc_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    calc_type: str,
    input_claim_ids: list[str],
    formula_hash: str,
    methodology_question_id: str,
    extraction_task_id: str,
    coverage_record_id: str,
) -> str:
    """Generate a deterministic UUID v5 calculation ID."""
    seed: dict[str, object] = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "calc_type": calc_type,
        "input_claim_ids": sorted(input_claim_ids),
        "formula_hash": formula_hash,
        "methodology_question_id": methodology_question_id,
        "extraction_task_id": extraction_task_id,
        "coverage_record_id": coverage_record_id,
    }
    return _uuid5(CALC_NAMESPACE, seed)


def deterministic_calc_sanad_id(*, calc_id: str) -> str:
    """Generate a deterministic UUID v5 CalcSanad ID."""
    return _uuid5(CALC_SANAD_NAMESPACE, {"calc_id": calc_id})


def aggregate_status(
    *,
    mappings: list[MethodologyCalculationMapping],
    rejections: list[MethodologyCalculationRejection],
) -> MethodologyCalculationStatus:
    """Return aggregate Slice 9 status."""
    required_failures = [rejection for rejection in rejections if rejection.required]
    if required_failures:
        return MethodologyCalculationStatus.FAILED
    if mappings and rejections:
        return MethodologyCalculationStatus.PARTIAL
    return MethodologyCalculationStatus.COMPLETED


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
