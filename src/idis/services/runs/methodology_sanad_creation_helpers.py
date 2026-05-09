"""Helpers for Slice 8 run-scoped Sanad materialization."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.defect import CureProtocol, Defect, DefectSeverity, DefectStatus, DefectType
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.sanad import CorroborationStatus
from idis.models.sanad_materialization import (
    MethodologySanadMapping,
    MethodologySanadMaterializationSummary,
    MethodologySanadReason,
    MethodologySanadRejection,
    RunScopedSanadDefectRecord,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    counter,
    deterministic_sanad_defect_id,
    deterministic_sanad_node_id,
    deterministic_sanad_timestamp,
)
from idis.models.transmission_node import ActorType, NodeType, TransmissionNode, VerificationMethod
from idis.services.sanad.grader import DefectSummary

ADAPTER_ACTOR_ID = "slice_8_sanad_adapter"
DEFAULT_CONFIDENCE = 0.9

_GRADER_CODE_TO_DEFECT_TYPE: dict[str, DefectType] = {
    "ILAL_VERSION_DRIFT": DefectType.INCONSISTENCY,
    "ILAL_CHAIN_BREAK": DefectType.BROKEN_CHAIN,
    "ILAL_CHAIN_GRAFTING": DefectType.CHAIN_GRAFTING,
    "ILAL_CHRONOLOGY_IMPOSSIBLE": DefectType.CHRONO_IMPOSSIBLE,
    "SHUDHUDH_ANOMALY": DefectType.ANOMALY_VS_STRONGER_SOURCES,
    "SHUDHUDH_UNIT_MISMATCH": DefectType.UNIT_MISMATCH,
    "SHUDHUDH_TIME_WINDOW": DefectType.TIME_WINDOW_MISMATCH,
    "COI_HIGH_UNDISCLOSED": DefectType.CONCEALMENT,
    "COI_HIGH_UNCURED": DefectType.INCONSISTENCY,
    "COI_DISCLOSURE_MISSING": DefectType.SCOPE_DRIFT,
}
_CURE_BY_DEFECT_TYPE: dict[DefectType, CureProtocol] = {
    DefectType.BROKEN_CHAIN: CureProtocol.RECONSTRUCT_CHAIN,
    DefectType.MISSING_LINK: CureProtocol.RECONSTRUCT_CHAIN,
    DefectType.UNKNOWN_SOURCE: CureProtocol.REQUEST_SOURCE,
    DefectType.CONCEALMENT: CureProtocol.HUMAN_ARBITRATION,
    DefectType.INCONSISTENCY: CureProtocol.HUMAN_ARBITRATION,
    DefectType.ANOMALY_VS_STRONGER_SOURCES: CureProtocol.HUMAN_ARBITRATION,
    DefectType.CHRONO_IMPOSSIBLE: CureProtocol.DISCARD_CLAIM,
    DefectType.CHAIN_GRAFTING: CureProtocol.RECONSTRUCT_CHAIN,
    DefectType.CIRCULARITY: CureProtocol.RECONSTRUCT_CHAIN,
    DefectType.STALENESS: CureProtocol.REQUEST_SOURCE,
    DefectType.UNIT_MISMATCH: CureProtocol.HUMAN_ARBITRATION,
    DefectType.TIME_WINDOW_MISMATCH: CureProtocol.HUMAN_ARBITRATION,
    DefectType.SCOPE_DRIFT: CureProtocol.HUMAN_ARBITRATION,
    DefectType.IMPLAUSIBILITY: CureProtocol.HUMAN_ARBITRATION,
}


class SanadMaterializationError(Exception):
    """Fail-closed internal Slice 8 materialization error."""

    def __init__(self, reason: MethodologySanadReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def group_evidence_by_claim(
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
) -> dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]]:
    """Group evidence by claim ID in deterministic order."""
    grouped: dict[str, list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell]] = (
        defaultdict(list)
    )
    for evidence in sorted(evidence_items, key=lambda item: (item.claim_id, evidence_id(item))):
        grouped[evidence.claim_id].append(evidence)
    return grouped


def claim_sort_key(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> tuple[str, str, str, str]:
    """Return deterministic claim processing order key."""
    return (
        claim.claim_id or "",
        claim.extraction_output_id,
        claim.extraction_task_id,
        claim.methodology_question_id,
    )


def claim_scope_matches(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    """Return whether a claim belongs to the run scope."""
    return claim.tenant_id == tenant_id and claim.deal_id == deal_id and claim.run_id == run_id


def evidence_scope_matches(
    evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    """Return whether an evidence item belongs to the run scope."""
    return (
        evidence.tenant_id == tenant_id
        and evidence.deal_id == deal_id
        and evidence.run_id == run_id
    )


def evidence_id(evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell) -> str:
    """Return evidence ID from a full record or safe shell."""
    if isinstance(evidence, RunScopedEvidenceItemShell):
        return evidence.evidence_id
    return evidence.evidence_item.evidence_id


def evidence_source_span_id(
    evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell,
) -> str:
    """Return original safe source span ID from evidence record or shell."""
    if isinstance(evidence, RunScopedEvidenceItemShell):
        return evidence.source_span_id
    return evidence.source_ref.source_span_id


def source_provenance_keys(
    source_provenance: list[RunScopedEvidenceProvenanceRef],
) -> set[tuple[str, str]]:
    """Return safe document/span keys present in Slice 7 provenance."""
    return {(item.document_id, item.source_span_id) for item in source_provenance}


def evidence_source_provenance_key(
    evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell,
) -> tuple[str, str]:
    """Return the document/span provenance key for an evidence item."""
    if isinstance(evidence, RunScopedEvidenceItemShell):
        return evidence.document_id, evidence.source_span_id
    return evidence.source_ref.document_id, evidence.source_ref.source_span_id


def source_for_grader(
    evidence: RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell,
) -> dict[str, Any]:
    """Build safe structured grader source input."""
    if isinstance(evidence, RunScopedEvidenceItemShell):
        return {
            "evidence_id": evidence.evidence_id,
            "source_span_id": evidence.source_span_id,
            "source_grade": "D",
            "verification_status": "UNVERIFIED",
        }
    item = evidence.evidence_item
    return {
        "evidence_id": item.evidence_id,
        "source_span_id": evidence.source_ref.source_span_id,
        "source_grade": item.source_grade.value,
        "verification_status": item.verification_status.value,
        "source_system": item.source_system,
        "upstream_origin_id": item.upstream_origin_id,
    }


def claim_for_grader(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> dict[str, Any]:
    """Build safe structured grader claim input."""
    materiality = getattr(claim, "materiality", None)
    claim_payload: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "materiality": materiality.value if materiality is not None else "MEDIUM",
    }
    value_struct = getattr(claim, "value_struct", None)
    if value_struct is not None:
        claim_payload["value_struct"] = value_struct.model_dump(mode="json")
    return claim_payload


def build_transmission_chain(
    *,
    sanad_id: str,
    claim_id: str,
    evidence_ids: list[str],
    source_span_ids: list[str],
) -> list[TransmissionNode]:
    """Build deterministic INGEST -> EXTRACT chain with ID-only refs."""
    ingest_input_refs = [{"source_span_id": span_id} for span_id in source_span_ids]
    ingest_output_refs = [{"evidence_id": item} for item in evidence_ids]
    extract_input_refs = [{"evidence_id": item} for item in evidence_ids]
    extract_output_refs = [{"claim_id": claim_id}, {"sanad_id": sanad_id}]
    return [
        _node(
            sanad_id=sanad_id,
            node_type=NodeType.INGEST,
            ordinal=0,
            input_refs=ingest_input_refs,
            output_refs=ingest_output_refs,
        ),
        _node(
            sanad_id=sanad_id,
            node_type=NodeType.EXTRACT,
            ordinal=1,
            input_refs=extract_input_refs,
            output_refs=extract_output_refs,
        ),
    ]


def materialize_defects(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    sanad_id: str,
    claim_id: str,
    evidence_ids: list[str],
    grader_defects: list[DefectSummary],
) -> list[RunScopedSanadDefectRecord]:
    """Map grader defect summaries to existing Defect records."""
    records: list[RunScopedSanadDefectRecord] = []
    for ordinal, summary in enumerate(grader_defects, start=2):
        defect_type = _map_defect_code(summary.code)
        if defect_type is None:
            raise SanadMaterializationError(
                MethodologySanadReason.DEFECT_MATERIALIZATION_FAILED,
                "grader defect code could not be safely mapped",
            )
        severity = DefectSeverity(summary.severity)
        cure_protocol = _CURE_BY_DEFECT_TYPE[defect_type]
        defect = Defect(
            defect_id=deterministic_sanad_defect_id(
                sanad_id=sanad_id,
                claim_id=claim_id,
                defect_type=defect_type.value,
                severity=severity.value,
                cure_protocol=cure_protocol.value,
                evidence_ids=evidence_ids,
            ),
            tenant_id=tenant_id,
            deal_id=deal_id,
            defect_type=defect_type,
            severity=severity,
            detected_by=ADAPTER_ACTOR_ID,
            description=f"deterministic {defect_type.value} defect",
            evidence_refs=[{"evidence_id": item} for item in evidence_ids],
            cure_protocol=cure_protocol,
            status=DefectStatus.OPEN,
            affected_claim_ids=[claim_id],
            timestamp=deterministic_sanad_timestamp(ordinal),
        )
        records.append(
            RunScopedSanadDefectRecord(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                claim_id=claim_id,
                sanad_id=sanad_id,
                defect=defect,
            )
        )
    return records


def grade_reason_codes(grade_result: Any) -> list[str]:
    """Return safe lowercase snake_case grade reason codes."""
    codes = {f"grade_{str(grade_result.grade).lower()}"}
    explanation = getattr(grade_result, "explanation", None)
    if explanation is not None:
        base_grade = getattr(explanation, "base_grade", None)
        source_tier = getattr(explanation, "source_tier", None)
        if base_grade:
            codes.add(f"base_grade_{str(base_grade).lower()}")
        if source_tier:
            codes.add(f"source_tier_{str(source_tier).lower()}")
    for defect in getattr(grade_result, "all_defects", []):
        codes.add(str(defect.code).lower())
    return sorted(codes)


def corroboration_status(evidence_count: int) -> CorroborationStatus:
    """Return existing Sanad corroboration enum from evidence count."""
    if evidence_count >= 3:
        return CorroborationStatus.MUTAWATIR
    if evidence_count >= 2:
        return CorroborationStatus.AHAD_2
    return CorroborationStatus.AHAD_1


def summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claims: int,
    total_evidence_items: int,
    mappings: list[MethodologySanadMapping],
    links: list[RunScopedSanadLinkRecord],
    grades: list[RunScopedSanadGradeRecord],
    defects: list[RunScopedSanadDefectRecord],
    rejections: list[MethodologySanadRejection],
) -> MethodologySanadMaterializationSummary:
    """Build safe aggregate summary."""
    return MethodologySanadMaterializationSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claims=total_claims,
        total_evidence_items=total_evidence_items,
        created_sanad_count=len(mappings),
        linked_claim_count=len(links),
        graded_sanad_count=len(grades),
        defect_count=len(defects),
        rejected_count=len(rejections),
        by_status=counter(
            ["created_linked_graded" for _mapping in mappings]
            + ["rejected" for _rejection in rejections]
        ),
        by_reason=counter([rejection.reason.value for rejection in rejections]),
        by_grade=counter([grade.sanad_grade.value for grade in grades]),
        by_defect_severity=counter([defect.defect.severity.value for defect in defects]),
    )


def rejection(
    *,
    reason: MethodologySanadReason,
    message: str,
    claim_id: str | None = None,
) -> MethodologySanadRejection:
    """Build a stable reason-coded rejection."""
    return MethodologySanadRejection(
        claim_id=claim_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def _node(
    *,
    sanad_id: str,
    node_type: NodeType,
    ordinal: int,
    input_refs: list[dict[str, str]],
    output_refs: list[dict[str, str]],
) -> TransmissionNode:
    return TransmissionNode(
        node_id=deterministic_sanad_node_id(
            sanad_id=sanad_id,
            node_type=node_type.value,
            ordinal=ordinal,
            input_refs=input_refs,
            output_refs=output_refs,
        ),
        node_type=node_type,
        actor_type=ActorType.SYSTEM,
        actor_id=ADAPTER_ACTOR_ID,
        input_refs=input_refs,
        output_refs=output_refs,
        timestamp=deterministic_sanad_timestamp(ordinal),
        confidence=DEFAULT_CONFIDENCE,
        dhabt_score=DEFAULT_CONFIDENCE,
        verification_method=VerificationMethod.AUTO,
    )


def _map_defect_code(code: str) -> DefectType | None:
    if code in DefectType.__members__:
        return DefectType[code]
    return _GRADER_CODE_TO_DEFECT_TYPE.get(code)
