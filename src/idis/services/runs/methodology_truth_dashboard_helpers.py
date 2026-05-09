"""Helper functions for Slice 10 Truth Dashboard materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from idis.models.calc_materialization import (
    RunScopedCalculationShell,
    RunScopedDeterministicCalculationRecord,
)
from idis.models.claim_materialization import RunScopedMaterializedClaim
from idis.models.defect import DefectSeverity
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
)
from idis.models.sanad import SanadGrade
from idis.models.sanad_materialization import (
    RunScopedSanadDefectRecord,
    RunScopedSanadDefectShell,
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.models.truth_dashboard_materialization import (
    MethodologyTruthDashboardMapping,
    MethodologyTruthDashboardReason,
    MethodologyTruthDashboardRejection,
    MethodologyTruthDashboardRunResult,
    MethodologyTruthDashboardSummary,
    TruthDashboardVerdict,
    aggregate_status,
    counter,
)

_ScopedInput = TypeVar("_ScopedInput")


@dataclass(frozen=True)
class RowCandidate:
    """Validated ingredients for one Truth Dashboard row."""

    claim: RunScopedMaterializedClaim
    evidence_ids: list[str]
    sanad_id: str
    calc_ids: list[str]
    defect_ids: list[str]
    sanad_grade: SanadGrade
    verdict: TruthDashboardVerdict


def candidate_for_claim(
    *,
    claim: RunScopedMaterializedClaim,
    evidence_by_claim: dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]],
    provenance_keys: set[tuple[str, str]],
    sanad_by_claim: dict[str, RunScopedSanadRecord | RunScopedSanadShell],
    grade_by_claim: dict[str, RunScopedSanadGradeRecord],
    defects_by_claim: dict[str, list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell]],
    calc_ids_by_claim: dict[str, list[str]],
) -> RowCandidate | MethodologyTruthDashboardRejection:
    """Return a row candidate or fail-closed rejection for one claim."""
    claim_id = claim.claim_id or ""
    evidence_items = evidence_by_claim.get(claim_id, [])
    if not evidence_items:
        return rejection(
            claim_id=claim_id,
            reason=MethodologyTruthDashboardReason.MISSING_EVIDENCE_LINKAGE,
            message="Claim has no linked EvidenceItems",
        )
    if not evidence_has_provenance(evidence_items, provenance_keys):
        return rejection(
            claim_id=claim_id,
            reason=MethodologyTruthDashboardReason.MISSING_SOURCE_PROVENANCE,
            message="Evidence source span is not backed by Slice 7 provenance",
        )
    sanad = sanad_by_claim.get(claim_id)
    if sanad is None:
        return rejection(
            claim_id=claim_id,
            reason=MethodologyTruthDashboardReason.MISSING_SANAD,
            message="Claim has no linked Sanad",
        )
    grade = grade_by_claim.get(claim_id)
    if grade is None:
        return rejection(
            claim_id=claim_id,
            reason=MethodologyTruthDashboardReason.MISSING_SANAD_GRADE,
            message="Claim has no Sanad grade",
        )

    defect_ids = sorted(
        set(grade.defect_ids)
        | {defect_id(defect) for defect in defects_by_claim.get(claim_id, []) if defect_id(defect)}
    )
    fatal_count = grade.fatal_defect_count + sum(
        1
        for defect in defects_by_claim.get(claim_id, [])
        if defect_severity(defect) == DefectSeverity.FATAL
    )
    major_count = grade.major_defect_count + sum(
        1
        for defect in defects_by_claim.get(claim_id, [])
        if defect_severity(defect) == DefectSeverity.MAJOR
    )
    evidence_ids = sorted({evidence_id(evidence) for evidence in evidence_items})
    return RowCandidate(
        claim=claim,
        evidence_ids=evidence_ids,
        sanad_id=sanad_id(sanad),
        calc_ids=calc_ids_by_claim.get(claim_id, []),
        defect_ids=defect_ids,
        sanad_grade=grade.sanad_grade,
        verdict=verdict(
            grade=grade.sanad_grade,
            evidence_ids=evidence_ids,
            fatal_defect_count=fatal_count,
            major_defect_count=major_count,
        ),
    )


def evidence_by_claim(
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
) -> dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]]:
    """Group EvidenceItems by claim ID."""
    grouped: dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]] = {}
    for evidence in evidence_items:
        grouped.setdefault(evidence.claim_id, []).append(evidence)
    return grouped


def filter_scoped(
    *,
    records: list[_ScopedInput],
    tenant_id: str,
    deal_id: str,
    run_id: str,
    rejections: list[MethodologyTruthDashboardRejection],
) -> list[_ScopedInput]:
    """Return scoped inputs and record fatal scope mismatch rejections."""
    scoped: list[_ScopedInput] = []
    for record in records:
        if (
            getattr(record, "tenant_id", tenant_id) == tenant_id
            and getattr(record, "deal_id", deal_id) == deal_id
            and getattr(record, "run_id", run_id) == run_id
        ):
            scoped.append(record)
            continue
        rejections.append(
            rejection(
                claim_id=record_claim_id(record),
                reason=MethodologyTruthDashboardReason.TENANT_OR_RUN_MISMATCH,
                message="Input scope does not match Truth Dashboard run scope",
            )
        )
    return scoped


def has_scope_mismatch(rejections: list[MethodologyTruthDashboardRejection]) -> bool:
    """Return True when any fatal tenant/deal/run mismatch was observed."""
    return any(
        rejection.reason == MethodologyTruthDashboardReason.TENANT_OR_RUN_MISMATCH
        for rejection in rejections
    )


def sanad_by_claim(
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell],
) -> dict[str, RunScopedSanadRecord | RunScopedSanadShell]:
    """Map Sanads by claim ID."""
    return {sanad.claim_id: sanad for sanad in sanads}


def defects_by_claim(
    defects: list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell],
) -> dict[str, list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell]]:
    """Group defect records and shells by claim ID."""
    grouped: dict[str, list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell]] = {}
    for defect in defects:
        grouped.setdefault(defect.claim_id, []).append(defect)
    return grouped


def calc_ids_by_claim(
    calculations: list[RunScopedDeterministicCalculationRecord | RunScopedCalculationShell],
) -> dict[str, list[str]]:
    """Group calculation IDs by input claim ID."""
    grouped: dict[str, set[str]] = {}
    for calculation in calculations:
        calc_id = (
            calculation.calculation.calc_id
            if isinstance(calculation, RunScopedDeterministicCalculationRecord)
            else calculation.calc_id
        )
        for claim_id in calculation.input_claim_ids:
            grouped.setdefault(claim_id, set()).add(calc_id)
    return {claim_id: sorted(calc_ids) for claim_id, calc_ids in grouped.items()}


def evidence_has_provenance(
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
    provenance_keys: set[tuple[str, str]],
) -> bool:
    """Return True when every evidence source span is backed by provenance."""
    for evidence in evidence_items:
        if isinstance(evidence, RunScopedEvidenceItemRecord):
            key = (evidence.source_ref.document_id, evidence.source_ref.source_span_id)
        else:
            key = (evidence.document_id, evidence.source_span_id)
        if key not in provenance_keys:
            return False
    return True


def evidence_id(evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> str:
    """Return a safe evidence ID from either full record or shell."""
    if isinstance(evidence, RunScopedEvidenceItemRecord):
        return evidence.evidence_item.evidence_id
    return evidence.evidence_id


def sanad_id(sanad: RunScopedSanadRecord | RunScopedSanadShell) -> str:
    """Return a safe Sanad ID from either full record or shell."""
    if isinstance(sanad, RunScopedSanadRecord):
        return sanad.sanad.sanad_id
    return sanad.sanad_id


def sanad_confidence(sanad: RunScopedSanadRecord | RunScopedSanadShell | None) -> float | None:
    """Return in-memory extraction confidence when a full Sanad is present."""
    if isinstance(sanad, RunScopedSanadRecord):
        return sanad.sanad.extraction_confidence
    return None


def defect_id(defect: RunScopedSanadDefectRecord | RunScopedSanadDefectShell) -> str:
    """Return a safe defect ID from either full record or shell."""
    if isinstance(defect, RunScopedSanadDefectRecord):
        return defect.defect.defect_id
    return defect.defect_id


def defect_severity(
    defect: RunScopedSanadDefectRecord | RunScopedSanadDefectShell,
) -> DefectSeverity:
    """Return defect severity from either full record or shell."""
    if isinstance(defect, RunScopedSanadDefectRecord):
        return defect.defect.severity
    return defect.severity


def record_claim_id(record: object) -> str | None:
    """Return claim_id from a scoped input when present."""
    value = getattr(record, "claim_id", None)
    return value if isinstance(value, str) and value.strip() else None


def verdict(
    *,
    grade: SanadGrade,
    evidence_ids: list[str],
    fatal_defect_count: int,
    major_defect_count: int,
) -> TruthDashboardVerdict:
    """Map Sanad grade, evidence linkage, and defects to a row verdict."""
    if fatal_defect_count > 0 or grade == SanadGrade.D:
        return TruthDashboardVerdict.REFUTED
    if major_defect_count > 0:
        return TruthDashboardVerdict.DISPUTED
    if grade == SanadGrade.C or not evidence_ids:
        return TruthDashboardVerdict.UNVERIFIED
    return TruthDashboardVerdict.CONFIRMED


def run_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claims: int,
    mappings: list[MethodologyTruthDashboardMapping],
    rejections: list[MethodologyTruthDashboardRejection],
    shells: list,
) -> MethodologyTruthDashboardRunResult:
    """Build the run-step-safe Slice 10 result."""
    status = aggregate_status(mappings=mappings, rejections=rejections)
    return MethodologyTruthDashboardRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=status,
        dashboard_mappings=mappings,
        dashboard_shells=shells,
        rejections=rejections,
        summary=MethodologyTruthDashboardSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claims=total_claims,
            created_row_count=len(mappings),
            rejected_count=len(rejections),
            by_status=counter(mapping.status for mapping in mappings),
            by_reason=counter(rejection.reason.value for rejection in rejections),
            by_verdict=counter(mapping.verdict.value for mapping in mappings),
            by_grade=counter(mapping.sanad_grade.value for mapping in mappings),
        ),
    )


def rejection(
    *,
    claim_id: str | None,
    reason: MethodologyTruthDashboardReason,
    message: str,
) -> MethodologyTruthDashboardRejection:
    """Build a stable reason-coded Slice 10 rejection."""
    return MethodologyTruthDashboardRejection(
        claim_id=claim_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
