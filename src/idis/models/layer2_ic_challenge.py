"""Run-scoped Layer 2 IC challenge models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.analysis.scoring.models import Stage

LAYER2_IC_CHALLENGE_NAMESPACE = UUID("0794fcbf-556b-5067-91d3-ea88dd749bc8")


class Layer2ICChallengeStatus(StrEnum):
    """Execution status for the Layer 2 IC challenge."""

    COMPLETED = "completed"
    BLOCKED = "blocked"


class Layer2ChallengeCategory(StrEnum):
    """Bounded IC challenge categories (Slice93 Task 6, DEC-D).

    The first eight map 1:1 to the scorecard dimensions (enabling scorecard-safe stage
    weighting); ``GENERAL`` is the catch-all for unmapped / free-text challenges.
    """

    MARKET_RISK = "market_risk"
    TEAM_RISK = "team_risk"
    PRODUCT_RISK = "product_risk"
    TRACTION_RISK = "traction_risk"
    THESIS_FIT_RISK = "thesis_fit_risk"
    CAPITAL_EFFICIENCY_RISK = "capital_efficiency_risk"
    SCALABILITY_RISK = "scalability_risk"
    EXECUTION_RISK = "execution_risk"
    GENERAL = "general"


class Layer2ICChallengeFinding(BaseModel):
    """Safe, reference-bound finding emitted by the IC challenge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    finding_id: str
    finding_type: str
    severity: str
    category: Layer2ChallengeCategory = Field(
        default=Layer2ChallengeCategory.GENERAL,
        description="Bounded challenge category (defaults to the GENERAL catch-all)",
    )
    supported_claim_ids: list[str] = Field(default_factory=list)
    supported_calc_ids: list[str] = Field(default_factory=list)
    graph_ref_ids: list[str] = Field(default_factory=list)
    rag_ref_ids: list[str] = Field(default_factory=list)
    enrichment_ref_ids: list[str] = Field(default_factory=list)

    @field_validator("finding_id", "finding_type", "severity")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "supported_claim_ids",
        "supported_calc_ids",
        "graph_ref_ids",
        "rag_ref_ids",
        "enrichment_ref_ids",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @model_validator(mode="after")
    def _requires_claim_or_calc_support(self) -> Layer2ICChallengeFinding:
        if not self.supported_claim_ids and not self.supported_calc_ids:
            raise ValueError("Layer 2 findings must reference at least one claim or calculation")
        return self


class Layer2ICChallengeShell(BaseModel):
    """Resume-safe shell for a Layer 2 IC challenge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str
    deal_id: str
    run_id: str
    layer2_challenge_id: str
    source_debate_id: str
    status: Layer2ICChallengeStatus
    claim_ids: list[str]
    calc_ids: list[str]
    graph_ref_ids: list[str]
    rag_ref_ids: list[str]
    enrichment_ref_ids: list[str]
    finding_ids: list[str]
    finding_count: int = Field(..., ge=0)
    unresolved_question_count: int = Field(..., ge=0)
    muhasabah_passed: bool

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "layer2_challenge_id",
        "source_debate_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "calc_ids",
        "graph_ref_ids",
        "rag_ref_ids",
        "enrichment_ref_ids",
        "finding_ids",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)


class Layer2ICChallengeRecord(BaseModel):
    """Safe run-scoped Layer 2 IC challenge record."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str
    deal_id: str
    run_id: str
    layer2_challenge_id: str
    source_debate_id: str
    status: Layer2ICChallengeStatus
    claim_ids: list[str]
    calc_ids: list[str]
    graph_ref_ids: list[str] = Field(default_factory=list)
    rag_ref_ids: list[str] = Field(default_factory=list)
    enrichment_ref_ids: list[str] = Field(default_factory=list)
    findings: list[Layer2ICChallengeFinding] = Field(default_factory=list)
    unresolved_question_count: int = Field(..., ge=0)
    muhasabah_passed: bool

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "layer2_challenge_id",
        "source_debate_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "calc_ids",
        "graph_ref_ids",
        "rag_ref_ids",
        "enrichment_ref_ids",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @model_validator(mode="after")
    def _requires_evidence_refs(self) -> Layer2ICChallengeRecord:
        if not self.claim_ids and not self.calc_ids:
            raise ValueError("Layer 2 challenge must reference claims or calculations")
        return self

    def to_shell(self) -> Layer2ICChallengeShell:
        """Return a resume-safe shell without private content or prompt transcript."""
        return Layer2ICChallengeShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            layer2_challenge_id=self.layer2_challenge_id,
            source_debate_id=self.source_debate_id,
            status=self.status,
            claim_ids=list(self.claim_ids),
            calc_ids=list(self.calc_ids),
            graph_ref_ids=list(self.graph_ref_ids),
            rag_ref_ids=list(self.rag_ref_ids),
            enrichment_ref_ids=list(self.enrichment_ref_ids),
            finding_ids=[finding.finding_id for finding in self.findings],
            finding_count=len(self.findings),
            unresolved_question_count=self.unresolved_question_count,
            muhasabah_passed=self.muhasabah_passed,
        )

    def to_run_step_summary(self, stage: Stage = Stage.SEED) -> dict[str, Any]:
        """Return safe Layer 2 run-step visibility.

        ``stage`` selects the stage-weighted category emphasis (DEC-E); it defaults to
        ``Stage.SEED`` to mirror the scoring step's default stage.
        """
        # Lazy import breaks the models<->emphasis import cycle (emphasis imports the
        # category enum defined above); the emphasis reuses stage_packs read-only.
        from idis.services.runs.layer2_stage_emphasis import apply_layer2_stage_emphasis

        shell = self.to_shell()
        by_severity = counter(finding.severity for finding in self.findings)
        by_type = counter(finding.finding_type for finding in self.findings)
        by_category = counter(finding.category.value for finding in self.findings)
        return {
            "status": self.status.value,
            "layer2_challenge_ids": [self.layer2_challenge_id],
            "source_debate_ids": [self.source_debate_id],
            "claim_ids": shell.claim_ids,
            "calc_ids": shell.calc_ids,
            "graph_ref_ids": shell.graph_ref_ids,
            "rag_ref_ids": shell.rag_ref_ids,
            "enrichment_ref_ids": shell.enrichment_ref_ids,
            "finding_ids": shell.finding_ids,
            "finding_count": shell.finding_count,
            "unresolved_question_count": shell.unresolved_question_count,
            "muhasabah_passed": shell.muhasabah_passed,
            "by_finding_type": by_type,
            "by_severity": by_severity,
            "by_category": by_category,
            "stage_emphasis": apply_layer2_stage_emphasis(stage, by_category),
            "challenge_shells": [shell.model_dump(mode="json")],
        }


def deterministic_layer2_ic_challenge_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    debate_id: str,
    claim_ids: list[str],
    calc_ids: list[str],
) -> str:
    """Generate a stable Layer 2 IC challenge ID."""
    return str(
        uuid5(
            LAYER2_IC_CHALLENGE_NAMESPACE,
            json.dumps(
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "debate_id": debate_id,
                    "claim_ids": sorted(claim_ids),
                    "calc_ids": sorted(calc_ids),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _sorted_strings(value: list[str]) -> list[str]:
    cleaned = [str(item).strip() for item in value]
    if any(not item for item in cleaned):
        raise ValueError("list values must not be blank")
    return sorted(set(cleaned))
