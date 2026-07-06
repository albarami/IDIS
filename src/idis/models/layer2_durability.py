"""Durable safe-shape rows for the Layer 2 IC challenge output (Slice93).

The whitelist boundary between the in-memory Layer-2 challenge/finding records and the
durable tables added by migration 0022. Rows carry IDs, categories (finding_type/
severity), counts, and reference id lists only — claim text, transcripts, prompt text,
and raw model output never enter a row (fields are copied by name, never spread).

Id shapes (Task 1 gate): the challenge_id is a bare UUID5 (UUID column); the finding_id
is a prefixed / LLM-supplied string (``layer2-finding-…``), stored as text and keyed
compositely with tenant_id + run_id.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from idis.models.layer2_ic_challenge import (
    Layer2ICChallengeFinding,
    Layer2ICChallengeRecord,
    counter,
)


class Layer2ChallengeRow(BaseModel):
    """Durable safe row for one Layer-2 IC challenge (identity + safe aggregate summary)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    challenge_id: str = Field(..., min_length=1)
    source_debate_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    safe_summary: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: Layer2ICChallengeRecord) -> Layer2ChallengeRow:
        """Whitelist-convert the in-memory challenge record into its durable safe row."""
        # Local import breaks the models<->emphasis import cycle; the emphasis reuses the
        # scoring stage_packs read-only and never touches the scorecard.
        from idis.analysis.scoring.models import Stage
        from idis.services.runs.layer2_stage_emphasis import apply_layer2_stage_emphasis

        by_category = counter(finding.category.value for finding in record.findings)
        safe_summary = {
            "claim_ids": list(record.claim_ids),
            "calc_ids": list(record.calc_ids),
            "graph_ref_ids": list(record.graph_ref_ids),
            "rag_ref_ids": list(record.rag_ref_ids),
            "enrichment_ref_ids": list(record.enrichment_ref_ids),
            "finding_ids": [finding.finding_id for finding in record.findings],
            "finding_count": len(record.findings),
            "unresolved_question_count": record.unresolved_question_count,
            "muhasabah_passed": record.muhasabah_passed,
            "by_finding_type": counter(finding.finding_type for finding in record.findings),
            "by_severity": counter(finding.severity for finding in record.findings),
            "by_category": by_category,
            "stage_emphasis": apply_layer2_stage_emphasis(Stage.SEED, by_category),
        }
        return cls(
            tenant_id=record.tenant_id,
            deal_id=record.deal_id,
            run_id=record.run_id,
            challenge_id=record.layer2_challenge_id,
            source_debate_id=record.source_debate_id,
            status=record.status.value,
            safe_summary=safe_summary,
        )


class Layer2FindingRow(BaseModel):
    """Durable safe row for one Layer-2 IC finding (IDs, category, severity, refs only)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    challenge_id: str = Field(..., min_length=1)
    finding_id: str = Field(..., min_length=1)
    finding_type: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    supported_claim_ids: list[str] = Field(default_factory=list)
    supported_calc_ids: list[str] = Field(default_factory=list)
    graph_ref_ids: list[str] = Field(default_factory=list)
    rag_ref_ids: list[str] = Field(default_factory=list)
    enrichment_ref_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_finding(
        cls,
        finding: Layer2ICChallengeFinding,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        challenge_id: str,
    ) -> Layer2FindingRow:
        """Whitelist-convert a safe challenge finding into its durable row."""
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            challenge_id=challenge_id,
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            severity=finding.severity,
            category=finding.category.value,
            supported_claim_ids=list(finding.supported_claim_ids),
            supported_calc_ids=list(finding.supported_calc_ids),
            graph_ref_ids=list(finding.graph_ref_ids),
            rag_ref_ids=list(finding.rag_ref_ids),
            enrichment_ref_ids=list(finding.enrichment_ref_ids),
        )
