"""Helper functions for Slice 9 deterministic calculation materialization."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, NamedTuple

from idis.methodology.models import RequiredCalculation
from idis.models.calc_materialization import (
    MethodologyCalculationReason,
    MethodologyCalculationRejection,
)
from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.deterministic_calculation import CalcType
from idis.models.extraction_task import (
    ExtractionTask,
    FddPolicyV2ExtractionTask,
    PlannedExtractionTask,
    decode_extraction_task,
)
from idis.models.sanad_materialization import (
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.services.calc.runner import (
    _claim_input_key,
    _extract_decimal_value,
    _operand_claim_id,
)
from idis.validators.extraction_gate import ExtractionGateBlockedError


def requested_calculations(
    extraction_tasks: list[PlannedExtractionTask],
) -> list[tuple[ExtractionTask, RequiredCalculation]]:
    """Return calculation requirements from each task's expected answer schema."""
    admitted: list[ExtractionTask] = []
    for value in extraction_tasks:
        task = decode_extraction_task(value)
        if isinstance(task, FddPolicyV2ExtractionTask):
            raise ValueError("METHODOLOGY_FDD_POLICY_V2_CALCULATION_UNSUPPORTED")
        admitted.append(task)
    return [
        (task, calc)
        for task in admitted
        for calc in task.expected_answer_schema.required_calculations
    ]


def scope_claims(
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell]:
    """Return claims scoped to the current run."""
    return [
        claim
        for claim in claims
        if claim.tenant_id == tenant_id and claim.deal_id == deal_id and claim.run_id == run_id
    ]


def scope_grades(
    grades: list[RunScopedSanadGradeRecord],
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> list[RunScopedSanadGradeRecord]:
    """Return Sanad grade records scoped to the current run."""
    return [
        grade
        for grade in grades
        if grade.tenant_id == tenant_id and grade.deal_id == deal_id and grade.run_id == run_id
    ]


def scope_sanads(
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell],
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> list[RunScopedSanadRecord | RunScopedSanadShell]:
    """Return Sanad records/shells scoped to the current run."""
    return [
        sanad
        for sanad in sanads
        if sanad.tenant_id == tenant_id and sanad.deal_id == deal_id and sanad.run_id == run_id
    ]


def claims_grouped_by_input_key(
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
) -> dict[str, list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell]]:
    """Group task-scoped claims by existing CalcRunner input key."""
    by_key: dict[str, list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell]] = {}
    for claim in claims:
        input_key = _claim_input_key(claim_payload(claim))
        if input_key is not None:
            by_key.setdefault(input_key, []).append(claim)
    return by_key


def claims_for_task(
    claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
    task: PlannedExtractionTask,
) -> list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell]:
    """Return only claims produced for the same task/question/coverage tuple."""
    return [
        claim
        for claim in claims
        if claim.extraction_task_id == task.extraction_task_id
        and claim.methodology_question_id == task.methodology_question_id
        and claim.coverage_record_id == task.coverage_record_id
    ]


def claim_payload(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> dict[str, Any]:
    """Build the minimal payload shape consumed by CalcRunner helper functions."""
    payload: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "claim_class": getattr(claim, "claim_type", ""),
        "claim_text": getattr(claim, "claim_text", ""),
        "predicate": predicate_from_claim(claim),
        "materiality": getattr(claim, "materiality", "MEDIUM"),
    }
    value_struct = getattr(claim, "value_struct", None)
    if value_struct is not None:
        payload["value"] = value_struct.model_dump(mode="json")
    return payload


def decimal_value_from_claim(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> Decimal | None:
    """Extract a Decimal value through existing CalcRunner logic."""
    return _extract_decimal_value(claim_payload(claim).get("value"))


def input_key_from_claim(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> str | None:
    """Extract a formula input key through existing CalcRunner logic."""
    return _claim_input_key(claim_payload(claim))


def predicate_from_claim(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> str | None:
    """Derive the conservative structured predicate prefix from claim text."""
    claim_text = getattr(claim, "claim_text", "")
    if not isinstance(claim_text, str):
        return None
    prefix = claim_text.split(":", 1)[0].strip().lower()
    return prefix or None


def metadata_for_calc(
    calc_type: CalcType,
    claims: list[Any],
) -> dict[str, str]:
    """Build safe scalar metadata for the canonical CalcOutput.

    Shared by the methodology path (run-scoped claim objects) and the CALC step (`CalcRunner`,
    repository dict claims) so both stamp identical output metadata into the reproducibility hash.
    """
    currency = common_attr(claims, "currency")
    metadata: dict[str, str] = {}
    if calc_type in {CalcType.GROSS_MARGIN, CalcType.LTV_CAC_RATIO}:
        metadata["unit"] = "percent" if calc_type == CalcType.GROSS_MARGIN else "ratio"
    if currency:
        metadata["currency"] = currency
    time_window = common_attr(claims, "time_window")
    if time_window:
        metadata["time_window"] = time_window
    return metadata


def _claim_value_attr(claim: Any, attr: str) -> Any:
    """Read a value-struct attribute from a claim object or repository dict."""
    value_struct = getattr(claim, "value_struct", None)
    if value_struct is not None:
        return getattr(value_struct, attr, None)
    if isinstance(claim, dict):
        value = claim.get("value")
        return value.get(attr) if isinstance(value, dict) else None
    return None


def common_attr(
    claims: list[Any],
    attr: str,
) -> str | None:
    """Return a value-struct attribute only when all inputs agree.

    Reads `claim.value_struct.<attr>` for run-scoped claim objects, or `claim["value"][<attr>]`
    for repository dict claims, so the methodology and CALC paths share one metadata source.
    """
    values: set[str] = set()
    for claim in claims:
        raw = _claim_value_attr(claim, attr)
        if raw is not None:
            values.add(str(raw))
    if len(values) == 1:
        return next(iter(values))
    return None


class PairCoordinateDefect(NamedTuple):
    """Deliberate pre-computation refusal detail for a selected operand pair."""

    coordinate: str
    claim_ids: list[str]
    message: str


def selected_pair_coordinate_defect(
    calc_type: CalcType,
    claims: list[Any],
) -> PairCoordinateDefect | None:
    """Refuse incoherent currency/time_window on the selected GROSS_MARGIN pair.

    Shared by the methodology service and CalcRunner at the existing metadata
    seam, over the already-selected required operands only. For each coordinate
    independently: equal present values are coherent, distinct present values
    refuse, exactly one absent refuses (a present value must not be borrowed
    for the whole result), and both absent preserve the existing optional
    repository-dict behavior. Absence is never reported as disagreement.
    """
    if calc_type != CalcType.GROSS_MARGIN:
        return None
    for coordinate in ("currency", "time_window"):
        raws = [_claim_value_attr(claim, coordinate) for claim in claims]
        present = [raw for raw in raws if raw is not None]
        if not present:
            continue
        if len(present) == len(raws) and len({str(raw) for raw in present}) == 1:
            continue
        detail = (
            f"selected operands disagree on {coordinate}"
            if len(present) == len(raws)
            else f"selected operands have incomplete {coordinate} metadata"
        )
        claim_ids = sorted(
            claim_id for claim in claims if (claim_id := _operand_claim_id(claim)) is not None
        )
        return PairCoordinateDefect(
            coordinate=coordinate,
            claim_ids=claim_ids,
            message=f"pre-computation operand coordinate refusal: {detail}",
        )
    return None


def gate_rejection(
    calc_type: CalcType,
    exc: ExtractionGateBlockedError,
    task: PlannedExtractionTask,
    required: bool,
) -> MethodologyCalculationRejection:
    """Convert extraction gate failures into stable Slice 9 reason codes."""
    reason = MethodologyCalculationReason.MISSING_SOURCE_METADATA
    claim_ids: list[str] = []
    for decision in exc.blocked_inputs:
        claim_ids.append(decision.claim_id)
        if decision.reason is None:
            continue
        if decision.reason.value == "LOW_CONFIDENCE":
            reason = MethodologyCalculationReason.BELOW_CONFIDENCE_THRESHOLD
        elif decision.reason.value == "LOW_DHABT":
            reason = MethodologyCalculationReason.BELOW_DHABT_THRESHOLD
    return rejection(
        reason=reason,
        message="calculation input failed extraction confidence gate",
        calc_type=calc_type.value,
        task=task,
        required=required,
        claim_ids=claim_ids,
    )


def rejection(
    *,
    reason: MethodologyCalculationReason,
    message: str,
    calc_type: str,
    task: PlannedExtractionTask,
    required: bool,
    reason_codes: list[str] | None = None,
    claim_ids: list[str] | None = None,
) -> MethodologyCalculationRejection:
    """Build a stable reason-coded calculation rejection."""
    return MethodologyCalculationRejection(
        calc_type=calc_type,
        claim_ids=sorted(claim_ids or []),
        methodology_question_id=task.methodology_question_id,
        extraction_task_id=task.extraction_task_id,
        coverage_record_id=task.coverage_record_id,
        reason=reason,
        reason_codes=reason_codes or [reason.value],
        message=message,
        required=required,
    )
