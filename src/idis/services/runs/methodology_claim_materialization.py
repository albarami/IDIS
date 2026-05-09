"""Run-scoped governed claim materialization from neutral execution outputs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from idis.models.claim import Materiality
from idis.models.claim_materialization import (
    ClaimMaterializationReason,
    ClaimMaterializationStatus,
    MaterializedClaimSourceRef,
    MaterializedClaimType,
    MaterializedClaimValueStruct,
    MethodologyOutputClaimMapping,
    MethodologyOutputClaimMaterializationRunResult,
    MethodologyOutputClaimMaterializationSummary,
    MethodologyOutputClaimRejection,
    RunScopedMaterializedClaim,
)
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionResult,
    MethodologyExtractionOutput,
)
from idis.models.value_structs import ValueStructType


class InMemoryRunMethodologyClaimMaterializationService:
    """Materialize neutral methodology outputs into run-scoped in-memory claims."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        execution_result: MethodologyExtractionExecutionResult,
    ) -> tuple[MethodologyOutputClaimMaterializationRunResult, list[RunScopedMaterializedClaim]]:
        """Convert accepted neutral execution outputs into governed claim records."""
        outputs = list(execution_result.accepted_outputs)
        mappings: list[MethodologyOutputClaimMapping] = []
        rejections: list[MethodologyOutputClaimRejection] = []
        claims: list[RunScopedMaterializedClaim] = []
        seen_output_ids: set[str] = set()

        context_reason = _context_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            execution_result=execution_result,
        )
        if context_reason is not None:
            rejections = [
                _rejection(
                    extraction_output_id=output.methodology_extraction_output_id,
                    reason=context_reason,
                )
                for output in outputs
            ] or [_rejection(extraction_output_id=None, reason=context_reason)]
            return _result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_outputs=len(outputs),
                mappings=mappings,
                rejections=rejections,
                claims=claims,
            )

        for output in sorted(
            outputs,
            key=lambda item: (
                item.methodology_extraction_output_id or "",
                item.extraction_task_id,
                item.methodology_question_id,
            ),
        ):
            output_id = output.methodology_extraction_output_id
            if output_id in seen_output_ids:
                rejections.append(
                    _rejection(
                        extraction_output_id=output_id,
                        reason=ClaimMaterializationReason.DUPLICATE_EXTRACTION_OUTPUT_ID,
                    )
                )
                continue
            seen_output_ids.add(output_id or "")

            try:
                claim = _claim_from_output(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    output=output,
                )
            except _OutputMaterializationError as exc:
                rejections.append(
                    _rejection(
                        extraction_output_id=output_id,
                        reason=exc.reason,
                    )
                )
                continue

            claims.append(claim)
            mappings.append(
                MethodologyOutputClaimMapping(
                    extraction_output_id=claim.extraction_output_id,
                    claim_id=claim.claim_id or "",
                    extraction_task_id=claim.extraction_task_id,
                    methodology_question_id=claim.methodology_question_id,
                    coverage_record_id=claim.coverage_record_id,
                    document_id=output.document_id,
                    source_span_ids=[ref.source_span_id for ref in claim.source_refs],
                )
            )

        return _result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_outputs=len(outputs),
            mappings=mappings,
            rejections=rejections,
            claims=claims,
        )


class _OutputMaterializationError(ValueError):
    def __init__(self, reason: ClaimMaterializationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _context_rejection(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    execution_result: MethodologyExtractionExecutionResult,
) -> ClaimMaterializationReason | None:
    if (
        execution_result.tenant_id != tenant_id
        or execution_result.deal_id != deal_id
        or execution_result.run_id != run_id
    ):
        return ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH
    return None


def _claim_from_output(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    output: MethodologyExtractionOutput,
) -> RunScopedMaterializedClaim:
    if output.tenant_id != tenant_id or output.deal_id != deal_id or output.run_id != run_id:
        raise _OutputMaterializationError(ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH)
    if not output.coverage_record_id:
        raise _OutputMaterializationError(ClaimMaterializationReason.MISSING_METHODOLOGY_LINKAGE)
    if not output.source_span_ids:
        raise _OutputMaterializationError(ClaimMaterializationReason.MISSING_SOURCE_SPAN)

    claim_type = _claim_type(output.answer)
    claim_text, value_struct = _claim_text_and_value(output)
    try:
        source_refs = [
            MaterializedClaimSourceRef(
                document_id=output.document_id,
                source_span_id=span_id,
                locator=None,
            )
            for span_id in output.source_span_ids
        ]
        return RunScopedMaterializedClaim(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_text=claim_text,
            claim_type=claim_type,
            value_struct=value_struct,
            materiality=Materiality.MEDIUM,
            source_refs=source_refs,
            methodology_id=output.methodology_id,
            methodology_version_id=output.methodology_version_id,
            methodology_question_id=output.methodology_question_id,
            coverage_record_id=output.coverage_record_id,
            extraction_task_id=output.extraction_task_id,
            extraction_output_id=output.methodology_extraction_output_id or "",
            status="materialized_unverified",
        )
    except ValidationError as exc:
        raise _OutputMaterializationError(
            ClaimMaterializationReason.MALFORMED_EXTRACTION_OUTPUT
        ) from exc


def _claim_type(answer: dict[str, Any]) -> MaterializedClaimType:
    raw_claim_type = answer.get("claim_type")
    if not isinstance(raw_claim_type, str) or not raw_claim_type.strip():
        raise _OutputMaterializationError(ClaimMaterializationReason.MISSING_CLAIM_TYPE)
    try:
        return MaterializedClaimType(raw_claim_type.strip())
    except ValueError as exc:
        raise _OutputMaterializationError(ClaimMaterializationReason.MISSING_CLAIM_TYPE) from exc


def _claim_text_and_value(
    output: MethodologyExtractionOutput,
) -> tuple[str, MaterializedClaimValueStruct]:
    answer_type = output.answer_type.lower().strip()
    if answer_type == "narrative":
        text = output.answer.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _OutputMaterializationError(
                ClaimMaterializationReason.MALFORMED_EXTRACTION_OUTPUT
            )
        return text.strip(), MaterializedClaimValueStruct(
            type=ValueStructType.TEXT,
            value=text.strip(),
            source_answer_type=answer_type,
        )
    if answer_type == "numeric":
        return _numeric_claim_text_and_value(output.answer, answer_type)
    raise _OutputMaterializationError(ClaimMaterializationReason.UNSUPPORTED_ANSWER_TYPE)


def _numeric_claim_text_and_value(
    answer: dict[str, Any],
    answer_type: str,
) -> tuple[str, MaterializedClaimValueStruct]:
    label = answer.get("label") or answer.get("predicate")
    raw_value = answer.get("value")
    unit = answer.get("unit")
    currency = answer.get("currency")
    time_window = answer.get("time_window")
    if (
        not isinstance(label, str)
        or not label.strip()
        or isinstance(raw_value, bool)
        or not isinstance(raw_value, int | float | Decimal)
        or not isinstance(unit, str)
        or not unit.strip()
        or not isinstance(currency, str)
        or not currency.strip()
        or not isinstance(time_window, str)
        or not time_window.strip()
    ):
        raise _OutputMaterializationError(ClaimMaterializationReason.MISSING_VALUE_STRUCT)

    value = Decimal(str(raw_value))
    canonical_value = _canonical_decimal(value)
    value_struct = MaterializedClaimValueStruct(
        type=ValueStructType.MONETARY,
        value=value,
        unit=unit.strip(),
        currency=currency.strip(),
        time_window=time_window.strip(),
        source_answer_type=answer_type,
    )
    return f"{label.strip()}: {canonical_value} {currency.strip()}", value_struct


def _canonical_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _rejection(
    *,
    extraction_output_id: str | None,
    reason: ClaimMaterializationReason,
) -> MethodologyOutputClaimRejection:
    return MethodologyOutputClaimRejection(
        extraction_output_id=extraction_output_id,
        reason=reason,
        reason_codes=[reason.value],
        message=reason.value,
    )


def _result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_outputs: int,
    mappings: list[MethodologyOutputClaimMapping],
    rejections: list[MethodologyOutputClaimRejection],
    claims: list[RunScopedMaterializedClaim],
) -> tuple[MethodologyOutputClaimMaterializationRunResult, list[RunScopedMaterializedClaim]]:
    summary = MethodologyOutputClaimMaterializationSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_outputs=total_outputs,
        created_claim_count=len(mappings),
        rejected_output_count=len(rejections),
        by_status=_counter(["completed"] * len(mappings) + ["rejected"] * len(rejections)),
        by_reason=_counter(rejection.reason.value for rejection in rejections),
    )
    return (
        MethodologyOutputClaimMaterializationRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=_aggregate_status(
                mappings=mappings,
                rejections=rejections,
                total_outputs=total_outputs,
            ),
            output_claim_mappings=mappings,
            rejected_outputs=rejections,
            summary=summary,
        ),
        claims,
    )


def _aggregate_status(
    *,
    mappings: list[MethodologyOutputClaimMapping],
    rejections: list[MethodologyOutputClaimRejection],
    total_outputs: int,
) -> ClaimMaterializationStatus:
    if mappings and rejections:
        return ClaimMaterializationStatus.PARTIAL
    if rejections:
        return ClaimMaterializationStatus.FAILED
    if total_outputs == 0 or mappings:
        return ClaimMaterializationStatus.COMPLETED
    return ClaimMaterializationStatus.FAILED


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
