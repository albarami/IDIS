"""Helpers for Slice 11 Evidence Trust Court service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from idis.debate.orchestrator import DebateOrchestrator, RoleRunners
from idis.debate.roles.base import (
    RoleResult,
    deterministic_id,
    deterministic_position_hash,
    deterministic_timestamp,
)
from idis.models.calc_materialization import (
    RunScopedCalcSanadRecord,
    RunScopedCalcSanadShell,
    RunScopedCalculationShell,
    RunScopedDeterministicCalculationRecord,
)
from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.debate import AgentOutput, DebateRole, DebateState, MuhasabahRecord
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.evidence_trust_court_aliases import EvidenceTrustAliasMaps
from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    EvidenceTrustFindingType,
    MethodologyEvidenceTrustCourtReason,
    MethodologyEvidenceTrustCourtRejection,
    RunScopedClaimTrustAssessment,
    RunScopedEvidenceTrustCourtFinding,
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtRoleSummary,
    RunScopedEvidenceTrustCourtSummary,
    counter,
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
    RunScopedTruthDashboardRecord,
    TruthDashboardVerdict,
)


@dataclass(frozen=True)
class CourtInputBundle:
    """Normalized Slice 11 inputs scoped to one run."""

    tenant_id: str
    deal_id: str
    run_id: str
    materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell]
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]
    source_provenance: list[RunScopedEvidenceProvenanceRef]
    sanads: list[RunScopedSanadRecord | RunScopedSanadShell]
    sanad_grades: list[RunScopedSanadGradeRecord]
    sanad_defects: list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell]
    calculations: list[RunScopedDeterministicCalculationRecord | RunScopedCalculationShell]
    calc_sanads: list[RunScopedCalcSanadRecord | RunScopedCalcSanadShell]
    truth_dashboard: RunScopedTruthDashboardRecord


class Layer1CourtRoleRunner:
    """Deterministic injected role runner for the Layer 1 court adapter."""

    def __init__(
        self,
        *,
        role: DebateRole,
        alias_maps: EvidenceTrustAliasMaps,
        critical_defect_detected: bool,
    ) -> None:
        self._role = role
        self._alias_maps = alias_maps
        self._critical_defect_detected = critical_defect_detected
        self._call_count = 0

    @property
    def role(self) -> DebateRole:
        """Return the debate role."""
        return self._role

    @property
    def agent_id(self) -> str:
        """Return the deterministic Layer 1 agent ID."""
        return f"{self._role.value}-layer1"

    def run(self, state: DebateState) -> RoleResult:
        """Produce one Muhasabah-gated Layer 1 role output."""
        self._call_count += 1
        output_id = deterministic_id(
            "out",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=self._role.value,
            round_number=state.round_number,
            step=self._call_count,
            extra="evidence_trust_court",
        )
        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=self._role,
            output_type="layer1_evidence_trust",
            content={
                "sections": [
                    {
                        "text": "Layer 1 evidence trust court assertion",
                        "is_factual": True,
                        "is_subjective": False,
                    }
                ],
                "critical_defect_detected": self._critical_defect_detected,
            },
            muhasabah=_muhasabah_for_output(
                state=state,
                role=self._role,
                agent_id=self.agent_id,
                output_id=output_id,
                call_count=self._call_count,
                alias_maps=self._alias_maps,
            ),
            round_number=state.round_number,
            timestamp=deterministic_timestamp(state.round_number, self._call_count),
        )
        return RoleResult(
            outputs=[output],
            position_hash=deterministic_position_hash(
                self._role.value,
                state.round_number,
                "layer1_evidence_trust",
            ),
        )


def run_layer1_debate(
    *,
    bundle: CourtInputBundle,
    alias_maps: EvidenceTrustAliasMaps,
    critical_defect_detected: bool,
) -> DebateState:
    """Run the existing debate orchestrator with deterministic Layer 1 role runners."""
    role_runners = RoleRunners(
        advocate=_runner(DebateRole.ADVOCATE, alias_maps, critical_defect_detected),
        sanad_breaker=_runner(DebateRole.SANAD_BREAKER, alias_maps, critical_defect_detected),
        contradiction_finder=_runner(
            DebateRole.CONTRADICTION_FINDER,
            alias_maps,
            critical_defect_detected,
        ),
        risk_officer=_runner(DebateRole.RISK_OFFICER, alias_maps, critical_defect_detected),
        arbiter=_runner(DebateRole.ARBITER, alias_maps, critical_defect_detected),
    )
    initial_state = DebateState(
        tenant_id=bundle.tenant_id,
        deal_id=bundle.deal_id,
        claim_registry_ref=f"run:{bundle.run_id}:claims",
        sanad_graph_ref=f"run:{bundle.run_id}:sanads",
    )
    return DebateOrchestrator(role_runners=role_runners).run(initial_state)


def assess_claims(
    *,
    court_id: str,
    bundle: CourtInputBundle,
) -> tuple[list[RunScopedClaimTrustAssessment], list[RunScopedEvidenceTrustCourtFinding]]:
    """Build claim assessments and safe findings from run-scoped inputs."""
    evidence_by_claim = group_by_claim(bundle.evidence_items)
    grades_by_claim = {grade.claim_id: grade for grade in bundle.sanad_grades}
    dashboard_verdicts = {
        mapping.claim_id: mapping.verdict for mapping in bundle.truth_dashboard.row_mappings
    }
    calc_ids_by_claim = group_calc_ids_by_claim(bundle.calculations)

    assessments: list[RunScopedClaimTrustAssessment] = []
    findings: list[RunScopedEvidenceTrustCourtFinding] = []
    for claim in sorted(bundle.materialized_claims, key=claim_id):
        current_claim_id = claim_id(claim)
        grade = grades_by_claim[current_claim_id]
        evidence_records = evidence_by_claim.get(current_claim_id, [])
        verdict = dashboard_verdicts.get(current_claim_id, TruthDashboardVerdict.UNVERIFIED)
        evidence_ids = [evidence_id(record) for record in evidence_records]
        disposition, reason_codes = disposition_for(
            grade=grade,
            dashboard_verdict=verdict,
            has_evidence_linkage=bool(evidence_ids),
        )
        calc_ids = calc_ids_by_claim.get(current_claim_id, [])
        assessments.append(
            RunScopedClaimTrustAssessment(
                claim_id=current_claim_id,
                disposition=disposition,
                evidence_ids=evidence_ids,
                source_span_ids=[source_span_id(record) for record in evidence_records],
                sanad_id=grade.sanad_id,
                sanad_grade=grade.sanad_grade,
                dashboard_verdict=verdict,
                calc_ids=calc_ids,
                defect_ids=list(grade.defect_ids),
                reason_codes=reason_codes,
            )
        )
        if disposition in {EvidenceTrustDisposition.DISPUTED, EvidenceTrustDisposition.REJECTED}:
            findings.append(
                finding(
                    court_id=court_id,
                    bundle=bundle,
                    claim_id=current_claim_id,
                    grade=grade,
                    verdict=verdict,
                    evidence_ids=evidence_ids,
                    calc_ids=calc_ids,
                    reason_codes=reason_codes,
                )
            )
    return assessments, findings


def disposition_for(
    *,
    grade: RunScopedSanadGradeRecord,
    dashboard_verdict: TruthDashboardVerdict,
    has_evidence_linkage: bool,
) -> tuple[EvidenceTrustDisposition, list[str]]:
    """Determine Layer 1 claim trust disposition."""
    reason_codes = [f"sanad_grade_{grade.sanad_grade.value.lower()}"]
    if not has_evidence_linkage:
        return EvidenceTrustDisposition.REJECTED, [
            MethodologyEvidenceTrustCourtReason.MISSING_EVIDENCE_LINKAGE.value
        ]
    if dashboard_verdict == TruthDashboardVerdict.REFUTED:
        return EvidenceTrustDisposition.REJECTED, [
            *reason_codes,
            MethodologyEvidenceTrustCourtReason.DASHBOARD_REFUTED.value,
        ]
    if grade.fatal_defect_count > 0 or grade.sanad_grade == SanadGrade.D:
        return EvidenceTrustDisposition.REJECTED, [*reason_codes, "fatal_or_d_grade"]
    if grade.major_defect_count > 0 or dashboard_verdict == TruthDashboardVerdict.DISPUTED:
        return EvidenceTrustDisposition.DISPUTED, [*reason_codes, "material_dispute"]
    if grade.sanad_grade == SanadGrade.C or dashboard_verdict == TruthDashboardVerdict.UNVERIFIED:
        return EvidenceTrustDisposition.UNVERIFIED, [*reason_codes, "insufficient_layer1_trust"]
    return EvidenceTrustDisposition.TRUSTED, [*reason_codes, "trusted_a_or_b_sanad"]


def role_summaries(
    final_state: DebateState,
    alias_maps: EvidenceTrustAliasMaps,
) -> list[RunScopedEvidenceTrustCourtRoleSummary]:
    """Translate alias-based agent outputs back to safe run-scoped IDs."""
    summaries: list[RunScopedEvidenceTrustCourtRoleSummary] = []
    for output in final_state.agent_outputs:
        summaries.append(
            RunScopedEvidenceTrustCourtRoleSummary(
                output_id=output.output_id,
                agent_id=output.agent_id,
                role=output.role,
                output_type=output.output_type,
                supported_claim_ids=[
                    resolved[1]
                    for alias in output.muhasabah.supported_claim_ids
                    if (resolved := alias_maps.resolve(alias)) is not None
                ],
                supported_calc_ids=[
                    resolved[1]
                    for alias in output.muhasabah.supported_calc_ids
                    if (resolved := alias_maps.resolve(alias)) is not None
                ],
                confidence=output.muhasabah.confidence,
                reason_codes=["muhasabah_gate_passed"],
            )
        )
    return summaries


def summary_from_court(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claims: int,
    court: RunScopedEvidenceTrustCourtRecord,
    rejections: list[MethodologyEvidenceTrustCourtRejection],
) -> RunScopedEvidenceTrustCourtSummary:
    """Build a safe aggregate court summary."""
    return RunScopedEvidenceTrustCourtSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claims=total_claims,
        assessed_claim_count=len(court.claim_assessments),
        finding_count=len(court.findings),
        rejected_count=len(rejections),
        by_disposition=counter(
            assessment.disposition.value for assessment in court.claim_assessments
        ),
        by_reason=counter(
            reason_code
            for assessment in court.claim_assessments
            for reason_code in assessment.reason_codes
        ),
        by_grade=counter(assessment.sanad_grade.value for assessment in court.claim_assessments),
        by_dashboard_verdict=counter(
            assessment.dashboard_verdict.value for assessment in court.claim_assessments
        ),
    )


def scope_mismatch(item: Any, *, tenant_id: str, deal_id: str, run_id: str) -> bool:
    """Return true when a scoped input belongs to another tenant, deal, or run."""
    return (
        getattr(item, "tenant_id", tenant_id) != tenant_id
        or getattr(item, "deal_id", deal_id) != deal_id
        or getattr(item, "run_id", run_id) != run_id
    )


def claim_id(item: Any) -> str:
    """Return a run-scoped claim ID from any Slice 6-11 input."""
    return str(getattr(item, "claim_id", ""))


def evidence_id(item: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> str:
    """Return an evidence ID from a record or shell."""
    evidence_item = getattr(item, "evidence_item", None)
    if evidence_item is not None:
        return str(evidence_item.evidence_id)
    return str(getattr(item, "evidence_id", ""))


def source_span_id(item: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> str:
    """Return a source span ID from a record or shell."""
    source_ref = getattr(item, "source_ref", None)
    if source_ref is not None:
        return str(source_ref.source_span_id)
    evidence_item = getattr(item, "evidence_item", None)
    if evidence_item is not None and evidence_item.source_span_id is not None:
        return str(evidence_item.source_span_id)
    return str(getattr(item, "source_span_id", ""))


def document_id(item: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> str:
    """Return a source document ID from a record or shell."""
    source_ref = getattr(item, "source_ref", None)
    if source_ref is not None:
        return str(source_ref.document_id)
    evidence_item = getattr(item, "evidence_item", None)
    if evidence_item is not None and getattr(evidence_item, "document_id", None) is not None:
        return str(evidence_item.document_id)
    return str(getattr(item, "document_id", ""))


def source_key(item: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> tuple[str, str]:
    """Return the document/span provenance key for an evidence item."""
    return document_id(item), source_span_id(item)


def calc_id(item: RunScopedDeterministicCalculationRecord | RunScopedCalculationShell) -> str:
    """Return a calculation ID from a record or shell."""
    calculation = getattr(item, "calculation", None)
    if calculation is not None:
        return str(calculation.calc_id)
    return str(getattr(item, "calc_id", ""))


def calc_input_claim_ids(
    item: RunScopedDeterministicCalculationRecord | RunScopedCalculationShell,
) -> list[str]:
    """Return calculation input claim IDs."""
    return list(getattr(item, "input_claim_ids", []))


def group_by_claim(
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
) -> dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]]:
    """Group evidence records by claim ID."""
    grouped: dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]] = {}
    for record in evidence_items:
        grouped.setdefault(claim_id(record), []).append(record)
    return grouped


def group_calc_ids_by_claim(
    calculations: list[RunScopedDeterministicCalculationRecord | RunScopedCalculationShell],
) -> dict[str, list[str]]:
    """Group calculation IDs by their input claim IDs."""
    grouped: dict[str, list[str]] = {}
    for calculation in calculations:
        for current_claim_id in calc_input_claim_ids(calculation):
            grouped.setdefault(current_claim_id, []).append(calc_id(calculation))
    return {
        current_claim_id: sorted(set(calc_ids)) for current_claim_id, calc_ids in grouped.items()
    }


def finding(
    *,
    court_id: str,
    bundle: CourtInputBundle,
    claim_id: str,
    grade: RunScopedSanadGradeRecord,
    verdict: TruthDashboardVerdict,
    evidence_ids: list[str],
    calc_ids: list[str],
    reason_codes: list[str],
) -> RunScopedEvidenceTrustCourtFinding:
    """Build one deterministic safe court finding."""
    return RunScopedEvidenceTrustCourtFinding(
        finding_id=deterministic_id(
            "finding",
            tenant_id=bundle.tenant_id,
            deal_id=bundle.deal_id,
            role="evidence_trust_court",
            round_number=1,
            extra=f"{court_id}:{claim_id}:{':'.join(reason_codes)}",
        ),
        finding_type=(
            EvidenceTrustFindingType.DASHBOARD_CONSISTENCY
            if verdict in {TruthDashboardVerdict.DISPUTED, TruthDashboardVerdict.REFUTED}
            else EvidenceTrustFindingType.PROVENANCE
            if MethodologyEvidenceTrustCourtReason.MISSING_EVIDENCE_LINKAGE.value in reason_codes
            else EvidenceTrustFindingType.SANAD_DEFECT
        ),
        claim_id=claim_id,
        evidence_ids=evidence_ids,
        sanad_id=grade.sanad_id,
        calc_ids=calc_ids,
        defect_ids=list(grade.defect_ids),
        reason_codes=reason_codes,
    )


def _runner(
    role: DebateRole,
    alias_maps: EvidenceTrustAliasMaps,
    critical_defect_detected: bool,
) -> Layer1CourtRoleRunner:
    return Layer1CourtRoleRunner(
        role=role,
        alias_maps=alias_maps,
        critical_defect_detected=critical_defect_detected,
    )


def _muhasabah_for_output(
    *,
    state: DebateState,
    role: DebateRole,
    agent_id: str,
    output_id: str,
    call_count: int,
    alias_maps: EvidenceTrustAliasMaps,
) -> MuhasabahRecord:
    return MuhasabahRecord(
        record_id=deterministic_id(
            "muh",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=role.value,
            round_number=state.round_number,
            step=call_count,
            extra="evidence_trust_court",
        ),
        agent_id=agent_id,
        output_id=output_id,
        supported_claim_ids=sorted(alias_maps.claim_aliases.values()),
        supported_calc_ids=sorted(alias_maps.calc_aliases.values()),
        falsifiability_tests=[
            {
                "test_description": "Check evidence provenance and Sanad trust state",
                "required_evidence": "run-scoped court input bundle",
                "pass_fail_rule": "all factual assertions are backed by aliases",
            }
        ],
        uncertainties=[
            {
                "uncertainty": "Layer 1 only",
                "impact": "LOW",
                "mitigation": "Validated Evidence Package remains Slice 12",
            }
        ],
        confidence=0.8,
        failure_modes=["missing_source_provenance", "tenant_or_run_mismatch"],
        timestamp=deterministic_timestamp(state.round_number, call_count),
        is_subjective=False,
    )
