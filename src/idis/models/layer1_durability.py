"""Durable safe-shape rows for the Layer 1 Evidence Trust Court output (Slice92).

These models are the whitelist boundary between the in-memory Layer-1 records
(evidence trust court, validated evidence package, Muḥāsabah records from the
court's governed debate) and the durable tables added by migration 0021.

Safe-shape rules (DEC-A/DEC-C): rows carry IDs, dispositions, grades, finding
types, reason codes, aggregate counts, and structured uncertainty triples only.
Claim text, debate transcripts, falsifiability narrative, failure-mode prose,
and recommendations never enter a row — fields are copied by name, never spread.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from idis.models.debate import MuhasabahRecord as DebateMuhasabahRecord
from idis.models.evidence_trust_court_materialization import (
    RunScopedEvidenceTrustCourtFinding,
)
from idis.models.muhasabah_record import MuhasabahRecordCanonical
from idis.models.validated_evidence_package_materialization import (
    RunScopedValidatedEvidencePackageRecord,
)

_VEP_SAFE_SUMMARY_FIELDS = (
    "claim_ids_by_disposition",
    "evidence_ids",
    "source_span_ids",
    "sanad_ids",
    "defect_ids",
    "calc_ids",
    "finding_ids",
    "finding_types",
    "role_names",
    "reason_codes",
    "by_disposition",
    "by_grade",
    "by_dashboard_verdict",
    "by_finding_type",
    "by_reason",
)


def deterministic_muhasabah_record_id(
    *,
    tenant_id: str,
    run_id: str,
    source_step: str,
    agent_id: str,
    output_id: str,
) -> str:
    """Deterministic UUID for a persisted Muḥāsabah record (idempotent upserts)."""
    seed = f"idis:muhasabah-record:{tenant_id}:{run_id}:{source_step}:{agent_id}:{output_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


class ValidatedEvidencePackageRow(BaseModel):
    """Durable safe row for one Layer-1 Validated Evidence Package candidate."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    package_id: str = Field(..., min_length=1)
    court_id: str = Field(..., min_length=1)
    dashboard_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    safe_summary: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_record(
        cls, record: RunScopedValidatedEvidencePackageRecord
    ) -> ValidatedEvidencePackageRow:
        """Whitelist-convert the in-memory VEP record into its durable safe row."""
        safe_summary = {field: getattr(record, field) for field in _VEP_SAFE_SUMMARY_FIELDS}
        return cls(
            tenant_id=record.tenant_id,
            deal_id=record.deal_id,
            run_id=record.run_id,
            package_id=record.package_id,
            court_id=record.court_id,
            dashboard_id=record.dashboard_id,
            status=record.status.value,
            safe_summary=safe_summary,
        )


class EvidenceTrustFindingRow(BaseModel):
    """Durable safe row for one Layer-1 court finding (IDs and reason codes only)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    court_id: str = Field(..., min_length=1)
    finding_id: str = Field(..., min_length=1)
    finding_type: str = Field(..., min_length=1)
    claim_id: str = Field(..., min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    sanad_id: str | None = None
    calc_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @classmethod
    def from_finding(
        cls,
        finding: RunScopedEvidenceTrustCourtFinding,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        court_id: str,
    ) -> EvidenceTrustFindingRow:
        """Whitelist-convert a summary-safe court finding into its durable row."""
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            court_id=court_id,
            finding_id=finding.finding_id,
            finding_type=finding.finding_type.value,
            claim_id=finding.claim_id,
            evidence_ids=list(finding.evidence_ids),
            sanad_id=finding.sanad_id,
            calc_ids=list(finding.calc_ids),
            defect_ids=list(finding.defect_ids),
            reason_codes=list(finding.reason_codes),
        )


class MuhasabahRecordRow(BaseModel):
    """Durable safe row for one court-scoped Muḥāsabah record (DEC-C safe set).

    Keeps the structured self-accounting fields — agent/output identity,
    confidence, subjectivity, supported ids, and uncertainty/impact/mitigation
    triples (the "unresolved uncertainties" the master plan requires). The
    falsifiability narrative and failure-mode prose are deliberately dropped.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    record_id: str = Field(..., min_length=1)
    source_step: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    output_id: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_subjective: bool = False
    supported_claim_ids: list[str] = Field(default_factory=list)
    supported_calc_ids: list[str] = Field(default_factory=list)
    uncertainties: list[dict[str, str]] = Field(default_factory=list)
    record_timestamp: str = Field(..., min_length=1)

    @classmethod
    def from_debate_record(
        cls,
        record: DebateMuhasabahRecord,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        source_step: str,
    ) -> MuhasabahRecordRow:
        """Whitelist-convert a debate-model Muḥāsabah record into its durable row.

        Debate records carry uncertainties as loose dicts and a datetime
        timestamp; only string uncertainty/impact/mitigation values are copied
        (malformed entries are skipped, never repr-stringified).
        """
        uncertainties: list[dict[str, str]] = []
        for item in record.uncertainties:
            if not isinstance(item, dict):
                continue
            triple = {
                key: value
                for key, value in (
                    ("uncertainty", item.get("uncertainty")),
                    ("impact", item.get("impact")),
                    ("mitigation", item.get("mitigation")),
                )
                if isinstance(value, str) and value
            }
            if triple:
                uncertainties.append(triple)
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            record_id=deterministic_muhasabah_record_id(
                tenant_id=tenant_id,
                run_id=run_id,
                source_step=source_step,
                agent_id=record.agent_id,
                output_id=record.output_id,
            ),
            source_step=source_step,
            agent_id=record.agent_id,
            output_id=record.output_id,
            confidence=record.confidence,
            is_subjective=record.is_subjective,
            supported_claim_ids=list(record.supported_claim_ids),
            supported_calc_ids=list(record.supported_calc_ids),
            uncertainties=uncertainties,
            record_timestamp=record.timestamp.isoformat(),
        )

    @classmethod
    def from_canonical(
        cls,
        record: MuhasabahRecordCanonical,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        source_step: str,
    ) -> MuhasabahRecordRow:
        """Whitelist-convert a canonical Muḥāsabah record into its durable safe row."""
        uncertainties = [
            {
                "uncertainty": item.uncertainty,
                "impact": item.impact,
                "mitigation": item.mitigation,
            }
            for item in record.uncertainties
        ]
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            record_id=deterministic_muhasabah_record_id(
                tenant_id=tenant_id,
                run_id=run_id,
                source_step=source_step,
                agent_id=record.agent_id,
                output_id=record.output_id,
            ),
            source_step=source_step,
            agent_id=record.agent_id,
            output_id=record.output_id,
            confidence=record.confidence,
            is_subjective=record.is_subjective,
            supported_claim_ids=list(record.supported_claim_ids),
            supported_calc_ids=list(record.supported_calc_ids),
            uncertainties=uncertainties,
            record_timestamp=record.timestamp,
        )
