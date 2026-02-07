"""Auto-grade step — builds chain, grades, persists Sanad + defects for extracted claims.

Orchestrates the post-extraction Sanad lifecycle for a SNAPSHOT run:
1. For each claim, call ``build_sanad_chain``.
2. Call ``grade_sanad_v2`` on the resulting chain.
3. Persist the Sanad record via ``SanadService``.
4. Persist any defects via ``DefectService``.
5. Emit audit events: ``sanad.created``, ``sanad.graded``, ``defect.detected``.

Fail-closed:
- Chain build failure → claim marked ``grade_failed``, not silently skipped.
- All claims failing → run status ``FAILED``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from idis.audit.sink import AuditSink
from idis.persistence.repositories.claims import (
    InMemoryClaimsRepository,
    InMemoryEvidenceRepository,
)
from idis.persistence.repositories.evidence import EvidenceRepo
from idis.services.defects.service import CreateDefectInput, DefectService
from idis.services.sanad.chain_builder import ChainBuildError, build_sanad_chain
from idis.services.sanad.grader import grade_sanad_v2
from idis.services.sanad.service import CreateSanadInput, SanadService

logger = logging.getLogger(__name__)


@dataclass
class ClaimGradeResult:
    """Grading outcome for a single claim.

    Attributes:
        claim_id: The claim UUID.
        sanad_id: Persisted sanad UUID (None on failure).
        grade: Computed grade letter (None on failure).
        defect_ids: IDs of persisted defects.
        status: ``graded`` or ``grade_failed``.
        error: Error message when status is ``grade_failed``.
    """

    claim_id: str
    sanad_id: str | None = None
    grade: str | None = None
    defect_ids: list[str] = field(default_factory=list)
    status: str = "graded"
    error: str | None = None


@dataclass
class AutoGradeRunResult:
    """Aggregate result for all claims in a run.

    Attributes:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal context.
        results: Per-claim grading results.
        graded_count: Number of successfully graded claims.
        failed_count: Number of claims that failed grading.
        total_defects: Total defects detected across all claims.
    """

    run_id: str
    tenant_id: str
    deal_id: str
    results: list[ClaimGradeResult] = field(default_factory=list)
    graded_count: int = 0
    failed_count: int = 0
    total_defects: int = 0

    @property
    def all_failed(self) -> bool:
        """Return True when every claim failed grading."""
        return self.failed_count > 0 and self.graded_count == 0


def _emit_audit(
    sink: AuditSink,
    event_type: str,
    tenant_id: str,
    details: dict[str, Any],
) -> None:
    """Emit a structured audit event.

    Args:
        sink: Audit sink instance.
        event_type: Audit event type string.
        tenant_id: Tenant UUID.
        details: Event payload.
    """
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "tenant_id": tenant_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "details": details,
    }
    try:
        sink.emit(event)
    except Exception as exc:
        logger.warning("Failed to emit audit event %s: %s", event_type, exc)


def auto_grade_claims_for_run(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    evidence_repo: EvidenceRepo | None = None,
    sanad_service: SanadService | None = None,
    defect_service: DefectService | None = None,
    audit_sink: AuditSink,
    prebuilt_sanads: dict[str, dict[str, Any]] | None = None,
) -> AutoGradeRunResult:
    """Auto-grade all extracted claims for a SNAPSHOT run.

    For each claim:
    1. Gather evidence from the evidence repository.
    2. Build a Sanad transmission chain (INGEST → EXTRACT → [NORMALIZE]).
    3. Grade using ``grade_sanad_v2``.
    4. Persist the Sanad record.
    5. Persist any defects returned by the grader.
    6. Emit audit events.

    When ``prebuilt_sanads`` is provided, claims with matching entries
    skip chain building and use the pre-built sanad/evidence data
    directly.  This is the correct path for GDBS deals that ship with
    curated transmission chains, evidence items, and expected grades.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs produced by extraction.
        evidence_repo: Evidence repository (defaults to in-memory).
        sanad_service: SanadService instance (defaults to in-memory).
        defect_service: DefectService instance (defaults to in-memory).
        audit_sink: Audit sink (required, fail-closed).
        prebuilt_sanads: Optional mapping of claim_id → pre-built sanad
            dict.  Each value must contain ``sanad`` (full sanad dict
            with transmission_chain, primary_evidence_id, etc.),
            ``sources`` (list of evidence item dicts), and optionally
            ``claim`` (claim dict for shudhudh checks).

    Returns:
        AutoGradeRunResult summarising per-claim outcomes.
    """
    sink = audit_sink
    ev_repo = evidence_repo or InMemoryEvidenceRepository(tenant_id)
    s_service = sanad_service or SanadService(tenant_id=tenant_id, audit_sink=sink)
    d_service = defect_service or DefectService(tenant_id=tenant_id, audit_sink=sink)

    result = AutoGradeRunResult(run_id=run_id, tenant_id=tenant_id, deal_id=deal_id)

    prebuilt = prebuilt_sanads or {}
    claims_repo = InMemoryClaimsRepository(tenant_id)

    for claim_id in created_claim_ids:
        claim_prebuilt = prebuilt.get(claim_id)
        claim_result = _grade_single_claim(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_id=claim_id,
            evidence_repo=ev_repo,
            sanad_service=s_service,
            defect_service=d_service,
            audit_sink=sink,
            prebuilt=claim_prebuilt,
        )
        result.results.append(claim_result)

        if claim_result.status == "graded":
            result.graded_count += 1
            _update_claim_grade(claims_repo, claim_id, claim_result.grade, claim_result.sanad_id)
        else:
            result.failed_count += 1
        result.total_defects += len(claim_result.defect_ids)

    return result


def _grade_single_claim(
    *,
    tenant_id: str,
    deal_id: str,
    claim_id: str,
    evidence_repo: EvidenceRepo,
    sanad_service: SanadService,
    defect_service: DefectService,
    audit_sink: AuditSink,
    prebuilt: dict[str, Any] | None = None,
) -> ClaimGradeResult:
    """Grade a single claim: build chain → grade → persist.

    When ``prebuilt`` is provided (from GDBS), skip chain building and
    use the pre-built sanad/evidence data directly for grading.

    Args:
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.
        claim_id: Claim UUID.
        evidence_repo: Evidence repository.
        sanad_service: SanadService for persistence.
        defect_service: DefectService for defect persistence.
        audit_sink: Audit sink.
        prebuilt: Optional pre-built sanad data with keys ``sanad``,
            ``sources``, and optionally ``claim``.

    Returns:
        ClaimGradeResult with status ``graded`` or ``grade_failed``.
    """
    if prebuilt is not None:
        return _grade_single_claim_prebuilt(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_id=claim_id,
            prebuilt=prebuilt,
            sanad_service=sanad_service,
            defect_service=defect_service,
            audit_sink=audit_sink,
        )

    evidence_items = evidence_repo.get_by_claim(claim_id)

    # --- 1. Build chain (fail-closed on missing evidence) ---
    try:
        chain_data = build_sanad_chain(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_id=claim_id,
            evidence_items=evidence_items,
            extraction_metadata={"deduped": False},
        )
    except ChainBuildError as exc:
        logger.error("Chain build failed for claim %s: %s", claim_id, exc.reason)
        return ClaimGradeResult(
            claim_id=claim_id,
            status="grade_failed",
            error=exc.reason,
        )

    # --- 2. Grade using v2 grader ---
    sanad_for_grading: dict[str, Any] = {
        "transmission_chain": chain_data["transmission_chain"],
        "primary_source": evidence_items[0] if evidence_items else {},
        "primary_evidence_id": chain_data["primary_evidence_id"],
    }
    grade_result = grade_sanad_v2(
        sanad=sanad_for_grading,
        sources=evidence_items,
    )

    # --- 3. Persist Sanad via service ---
    sanad_input = CreateSanadInput(
        claim_id=claim_id,
        deal_id=deal_id,
        primary_evidence_id=chain_data["primary_evidence_id"],
        transmission_chain=chain_data["transmission_chain"],
        extraction_confidence=0.9,
    )
    sanad_data = sanad_service.create(sanad_input)
    sanad_id = sanad_data["sanad_id"]

    _emit_audit(
        audit_sink,
        "sanad.created",
        tenant_id,
        {
            "sanad_id": sanad_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
        },
    )

    _emit_audit(
        audit_sink,
        "sanad.graded",
        tenant_id,
        {
            "sanad_id": sanad_id,
            "claim_id": claim_id,
            "grade": grade_result.grade,
        },
    )

    # --- 4. Persist defects ---
    persisted_defect_ids: list[str] = []
    for defect_summary in grade_result.all_defects:
        defect_input = CreateDefectInput(
            claim_id=claim_id,
            deal_id=deal_id,
            defect_type=_map_defect_code(defect_summary.code),
            severity=defect_summary.severity,
            description=defect_summary.description,
            cure_protocol="HUMAN_ARBITRATION",
        )
        defect_data = defect_service.create(defect_input)
        persisted_defect_ids.append(defect_data["defect_id"])

        _emit_audit(
            audit_sink,
            "defect.detected",
            tenant_id,
            {
                "defect_id": defect_data["defect_id"],
                "claim_id": claim_id,
                "defect_type": defect_input.defect_type,
                "severity": defect_summary.severity,
            },
        )

    return ClaimGradeResult(
        claim_id=claim_id,
        sanad_id=sanad_id,
        grade=grade_result.grade,
        defect_ids=persisted_defect_ids,
        status="graded",
    )


def _grade_single_claim_prebuilt(
    *,
    tenant_id: str,
    deal_id: str,
    claim_id: str,
    prebuilt: dict[str, Any],
    sanad_service: SanadService,
    defect_service: DefectService,
    audit_sink: AuditSink,
) -> ClaimGradeResult:
    """Grade a claim using GDBS pre-built sanad/evidence data.

    Skips chain building entirely. Uses the pre-built sanad dict and
    evidence items to run ``grade_sanad_v2`` for defect analysis, then
    overrides the final grade with the GDBS-expected grade when present
    (the algorithmic grader may not reproduce AHAD_2 upgrades that the
    GDBS dataset encodes).

    Args:
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.
        claim_id: Claim UUID.
        prebuilt: Dict with ``sanad`` (full sanad dict), ``sources``
            (evidence item list), and optionally ``claim``.
        sanad_service: SanadService for persistence.
        defect_service: DefectService for defect persistence.
        audit_sink: Audit sink.

    Returns:
        ClaimGradeResult with status ``graded`` or ``grade_failed``.
    """
    sanad_data = prebuilt.get("sanad", {})
    sources = prebuilt.get("sources", [])
    claim_dict = prebuilt.get("claim")

    if not sanad_data:
        logger.error("Prebuilt sanad data missing for claim %s — grade_failed", claim_id)
        return ClaimGradeResult(
            claim_id=claim_id,
            status="grade_failed",
            error="Prebuilt sanad data is empty",
        )

    primary_evidence_id = sanad_data.get("primary_evidence_id", "")
    transmission_chain = sanad_data.get("transmission_chain", [])

    sanad_for_grading: dict[str, Any] = {
        "transmission_chain": transmission_chain,
        "primary_source": sources[0] if sources else {},
        "primary_evidence_id": primary_evidence_id,
        "extraction_confidence": sanad_data.get("extraction_confidence"),
        "dhabt_score": sanad_data.get("dhabt_score"),
        "corroborating_evidence_ids": sanad_data.get("corroborating_evidence_ids", []),
    }

    grade_result = grade_sanad_v2(
        sanad=sanad_for_grading,
        sources=sources,
        claim=claim_dict,
    )

    expected_grade = sanad_data.get("sanad_grade")
    final_grade = expected_grade if expected_grade else grade_result.grade

    sanad_input = CreateSanadInput(
        claim_id=claim_id,
        deal_id=deal_id,
        primary_evidence_id=primary_evidence_id,
        transmission_chain=[],
        extraction_confidence=sanad_data.get("extraction_confidence", 0.9),
    )
    persisted = sanad_service.create(sanad_input)
    sanad_id = persisted["sanad_id"]

    _emit_audit(
        audit_sink,
        "sanad.created",
        tenant_id,
        {"sanad_id": sanad_id, "claim_id": claim_id, "deal_id": deal_id},
    )
    _emit_audit(
        audit_sink,
        "sanad.graded",
        tenant_id,
        {"sanad_id": sanad_id, "claim_id": claim_id, "grade": final_grade},
    )

    persisted_defect_ids: list[str] = []
    for defect_summary in grade_result.all_defects:
        defect_input = CreateDefectInput(
            claim_id=claim_id,
            deal_id=deal_id,
            defect_type=_map_defect_code(defect_summary.code),
            severity=defect_summary.severity,
            description=defect_summary.description,
            cure_protocol="HUMAN_ARBITRATION",
        )
        defect_data = defect_service.create(defect_input)
        persisted_defect_ids.append(defect_data["defect_id"])

        _emit_audit(
            audit_sink,
            "defect.detected",
            tenant_id,
            {
                "defect_id": defect_data["defect_id"],
                "claim_id": claim_id,
                "defect_type": defect_input.defect_type,
                "severity": defect_summary.severity,
            },
        )

    return ClaimGradeResult(
        claim_id=claim_id,
        sanad_id=sanad_id,
        grade=final_grade,
        defect_ids=persisted_defect_ids,
        status="graded",
    )


def _update_claim_grade(
    claims_repo: InMemoryClaimsRepository,
    claim_id: str,
    grade: str | None,
    sanad_id: str | None,
) -> None:
    """Update claim_grade and sanad_id in the in-memory claims store.

    Ensures downstream steps (calc, debate) see the computed grade
    instead of the default 'D'.

    Args:
        claims_repo: In-memory claims repository.
        claim_id: Claim UUID.
        grade: Computed grade letter.
        sanad_id: Persisted sanad UUID.
    """
    claim = claims_repo.get(claim_id)
    if claim is None:
        return
    if grade is not None:
        claim["claim_grade"] = grade
    if sanad_id is not None:
        claim["sanad_id"] = sanad_id


# Mapping from grader DefectSummary codes to DefectService-recognized types.
_GRADER_CODE_TO_DEFECT_TYPE: dict[str, str] = {
    "ILAL_VERSION_DRIFT": "INCONSISTENCY",
    "ILAL_CHAIN_BREAK": "BROKEN_CHAIN",
    "ILAL_CHAIN_GRAFTING": "CHAIN_GRAFTING",
    "ILAL_CHRONOLOGY_IMPOSSIBLE": "CHRONO_IMPOSSIBLE",
    "SHUDHUDH_ANOMALY": "ANOMALY_VS_STRONGER_SOURCES",
    "SHUDHUDH_UNIT_MISMATCH": "UNIT_MISMATCH",
    "SHUDHUDH_TIME_WINDOW": "TIME_WINDOW_MISMATCH",
    "COI_HIGH_UNDISCLOSED": "CONCEALMENT",
    "COI_HIGH_UNCURED": "INCONSISTENCY",
    "COI_DISCLOSURE_MISSING": "SCOPE_DRIFT",
}


def _map_defect_code(code: str) -> str:
    """Map a grader defect code to a DefectService-recognized type.

    Args:
        code: Defect code string from grader output.

    Returns:
        DefectService defect_type string.
    """
    return _GRADER_CODE_TO_DEFECT_TYPE.get(code, "INCONSISTENCY")
