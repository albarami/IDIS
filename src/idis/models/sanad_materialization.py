"""Slice 8 in-memory Sanad creation, linking, grading, and defect models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.claim_sanad_link_boundary import ClaimPromotionStatus
from idis.models.defect import CureProtocol, Defect, DefectSeverity, DefectStatus, DefectType
from idis.models.sanad import Sanad, SanadGrade

SANAD_NAMESPACE = UUID("3aa02f17-bfb5-5a69-b601-2ad9a8d2c155")
SANAD_NODE_NAMESPACE = UUID("9ca8ad4a-6f41-5a17-9fa6-bc86edb97054")
SANAD_DEFECT_NAMESPACE = UUID("d05bc4dd-c50c-5a4b-9b26-754a78307b70")
SANAD_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MethodologySanadMaterializationStatus(StrEnum):
    """Aggregate Slice 8 Sanad materialization status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MethodologySanadReason(StrEnum):
    """Machine-readable Slice 8 Sanad materialization reasons."""

    MISSING_MATERIALIZED_CLAIMS = "missing_materialized_claims"
    MISSING_EVIDENCE_ITEMS = "missing_evidence_items"
    MISSING_SOURCE_PROVENANCE = "missing_source_provenance"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    MISSING_CLAIM_ID = "missing_claim_id"
    MISSING_CLAIM_EVIDENCE = "missing_claim_evidence"
    MALFORMED_EVIDENCE_ITEM = "malformed_evidence_item"
    DUPLICATE_CLAIM_SANAD_INPUT = "duplicate_claim_sanad_input"
    CHAIN_BUILD_FAILED = "chain_build_failed"
    SANAD_VALIDATION_FAILED = "sanad_validation_failed"
    GRADING_FAILED = "grading_failed"
    DEFECT_MATERIALIZATION_FAILED = "defect_materialization_failed"
    CLAIM_LINK_FAILED = "claim_link_failed"


class SanadMaterializationBaseModel(BaseModel):
    """Base model for deterministic Slice 8 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedSanadShell(SanadMaterializationBaseModel):
    """Safe resume shell for a run-scoped Sanad."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad_id: str
    primary_evidence_id: str
    evidence_ids: list[str]
    source_span_ids: list[str]
    sanad_grade: SanadGrade
    defect_ids: list[str] = Field(default_factory=list)
    transmission_chain_node_count: int = Field(..., ge=1)
    chain_node_types: list[str]
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "sanad_id",
        "primary_evidence_id",
        "methodology_question_id",
        "coverage_record_id",
        "extraction_task_id",
        "extraction_output_id",
        "status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("evidence_ids", "source_span_ids", "defect_ids", "chain_node_types")
    @classmethod
    def _string_list_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedSanadRecord(SanadMaterializationBaseModel):
    """In-memory governed Sanad boundary for Slice 8."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad: Sanad
    evidence_ids: list[str]
    source_span_ids: list[str]
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "coverage_record_id",
        "extraction_task_id",
        "extraction_output_id",
        "status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("evidence_ids", "source_span_ids")
    @classmethod
    def _ids_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if not cleaned or any(not item for item in cleaned):
            raise ValueError("ids must not be empty or blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _sanad_scope_matches_record(self) -> RunScopedSanadRecord:
        if self.sanad.tenant_id != self.tenant_id:
            raise ValueError("sanad tenant_id must match record tenant_id")
        if self.sanad.deal_id != self.deal_id:
            raise ValueError("sanad deal_id must match record deal_id")
        if self.sanad.claim_id != self.claim_id:
            raise ValueError("sanad claim_id must match record claim_id")
        return self

    def to_shell(self, *, defect_ids: list[str] | None = None) -> RunScopedSanadShell:
        """Build a safe shell without chain refs, descriptions, or explanations."""
        return RunScopedSanadShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            claim_id=self.claim_id,
            sanad_id=self.sanad.sanad_id,
            primary_evidence_id=self.sanad.primary_evidence_id,
            evidence_ids=list(self.evidence_ids),
            source_span_ids=list(self.source_span_ids),
            sanad_grade=self.sanad.sanad_grade,
            defect_ids=list(defect_ids or []),
            transmission_chain_node_count=len(self.sanad.transmission_chain),
            chain_node_types=[node.node_type.value for node in self.sanad.transmission_chain],
            methodology_question_id=self.methodology_question_id,
            coverage_record_id=self.coverage_record_id,
            extraction_task_id=self.extraction_task_id,
            extraction_output_id=self.extraction_output_id,
            status=self.status,
        )


class RunScopedSanadLinkRecord(SanadMaterializationBaseModel):
    """Run-scoped claim-to-Sanad link that does not promote IC readiness."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad_id: str
    evidence_ids: list[str]
    source_span_ids: list[str]
    claim_link_status: str
    claim_promotion_status: ClaimPromotionStatus = ClaimPromotionStatus.SANAD_LINKED_NOT_IC_READY


class RunScopedSanadGradeRecord(SanadMaterializationBaseModel):
    """Safe grade record for one run-scoped Sanad."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad_id: str
    sanad_grade: SanadGrade
    grade_reason_codes: list[str]
    defect_ids: list[str]
    fatal_defect_count: int = Field(..., ge=0)
    major_defect_count: int = Field(..., ge=0)
    minor_defect_count: int = Field(..., ge=0)


class RunScopedSanadDefectShell(SanadMaterializationBaseModel):
    """Safe defect resume shell without description or evidence refs."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad_id: str
    defect_id: str
    defect_type: DefectType
    severity: DefectSeverity
    cure_protocol: CureProtocol
    status: DefectStatus


class RunScopedSanadDefectRecord(SanadMaterializationBaseModel):
    """In-memory governed defect wrapper for Slice 8."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    sanad_id: str
    defect: Defect

    def to_shell(self) -> RunScopedSanadDefectShell:
        """Build a safe defect shell without description or refs."""
        return RunScopedSanadDefectShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            claim_id=self.claim_id,
            sanad_id=self.sanad_id,
            defect_id=self.defect.defect_id,
            defect_type=self.defect.defect_type,
            severity=self.defect.severity,
            cure_protocol=self.defect.cure_protocol,
            status=self.defect.status,
        )


class MethodologySanadMapping(SanadMaterializationBaseModel):
    """Summary-safe claim/evidence/Sanad mapping."""

    claim_id: str
    sanad_id: str
    primary_evidence_id: str
    evidence_ids: list[str]
    source_span_ids: list[str]
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    sanad_grade: SanadGrade
    defect_ids: list[str] = Field(default_factory=list)
    chain_node_types: list[str]
    transmission_chain_node_count: int = Field(..., ge=1)

    @classmethod
    def from_record(
        cls,
        record: RunScopedSanadRecord,
        *,
        defect_ids: list[str] | None = None,
    ) -> MethodologySanadMapping:
        """Build a summary-safe mapping from a run-scoped Sanad record."""
        return cls(
            claim_id=record.claim_id,
            sanad_id=record.sanad.sanad_id,
            primary_evidence_id=record.sanad.primary_evidence_id,
            evidence_ids=list(record.evidence_ids),
            source_span_ids=list(record.source_span_ids),
            methodology_question_id=record.methodology_question_id,
            coverage_record_id=record.coverage_record_id,
            extraction_task_id=record.extraction_task_id,
            extraction_output_id=record.extraction_output_id,
            sanad_grade=record.sanad.sanad_grade,
            defect_ids=list(defect_ids or []),
            chain_node_types=[node.node_type.value for node in record.sanad.transmission_chain],
            transmission_chain_node_count=len(record.sanad.transmission_chain),
        )


class MethodologySanadRejection(SanadMaterializationBaseModel):
    """Stable reason-coded Slice 8 rejection."""

    claim_id: str | None = None
    reason: MethodologySanadReason
    reason_codes: list[str]
    message: str

    @model_validator(mode="after")
    def _reason_codes_include_reason(self) -> MethodologySanadRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologySanadMaterializationSummary(SanadMaterializationBaseModel):
    """Safe aggregate summary for Slice 8."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_claims: int
    total_evidence_items: int
    created_sanad_count: int
    linked_claim_count: int
    graded_sanad_count: int
    defect_count: int
    rejected_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    by_grade: dict[str, int]
    by_defect_severity: dict[str, int]


class MethodologySanadMaterializationRunResult(SanadMaterializationBaseModel):
    """Run-step-safe Slice 8 result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologySanadMaterializationStatus
    sanad_mappings: list[MethodologySanadMapping] = Field(default_factory=list)
    claim_sanad_links: list[RunScopedSanadLinkRecord] = Field(default_factory=list)
    grade_records: list[RunScopedSanadGradeRecord] = Field(default_factory=list)
    defect_shells: list[RunScopedSanadDefectShell] = Field(default_factory=list)
    rejections: list[MethodologySanadRejection] = Field(default_factory=list)
    summary: MethodologySanadMaterializationSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without raw payloads, full chain refs, or descriptions."""
        return {
            "status": status or self.status.value,
            "sanad_ids": [mapping.sanad_id for mapping in self.sanad_mappings],
            "claim_ids": sorted({mapping.claim_id for mapping in self.sanad_mappings}),
            "evidence_ids": sorted(
                {
                    evidence_id
                    for mapping in self.sanad_mappings
                    for evidence_id in mapping.evidence_ids
                }
            ),
            "source_span_ids": sorted(
                {
                    source_span_id
                    for mapping in self.sanad_mappings
                    for source_span_id in mapping.source_span_ids
                }
            ),
            "sanad_mappings": [mapping.model_dump(mode="json") for mapping in self.sanad_mappings],
            "claim_sanad_links": [link.model_dump(mode="json") for link in self.claim_sanad_links],
            "grade_records": [grade.model_dump(mode="json") for grade in self.grade_records],
            "defect_shells": [
                defect_shell.model_dump(mode="json") for defect_shell in self.defect_shells
            ],
            "rejections": [rejection.model_dump(mode="json") for rejection in self.rejections],
            "summary": {
                "total_claims": self.summary.total_claims,
                "total_evidence_items": self.summary.total_evidence_items,
                "created_sanad_count": self.summary.created_sanad_count,
                "linked_claim_count": self.summary.linked_claim_count,
                "graded_sanad_count": self.summary.graded_sanad_count,
                "defect_count": self.summary.defect_count,
                "rejected_count": self.summary.rejected_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
                "by_grade": dict(self.summary.by_grade),
                "by_defect_severity": dict(self.summary.by_defect_severity),
            },
        }


def deterministic_sanad_timestamp(ordinal: int) -> datetime:
    """Return a deterministic synthetic timestamp for a chain/defect ordinal."""
    return SANAD_EPOCH + timedelta(seconds=ordinal)


def deterministic_sanad_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim_id: str,
    evidence_ids: list[str],
    source_span_ids: list[str],
    extraction_output_id: str,
    extraction_task_id: str,
    methodology_question_id: str,
    coverage_record_id: str,
) -> str:
    """Generate a deterministic UUID v5 Sanad ID."""
    seed: dict[str, object] = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "claim_id": claim_id,
        "evidence_ids": sorted(evidence_ids),
        "source_span_ids": sorted(source_span_ids),
        "extraction_output_id": extraction_output_id,
        "extraction_task_id": extraction_task_id,
        "methodology_question_id": methodology_question_id,
        "coverage_record_id": coverage_record_id,
    }
    return _uuid5(SANAD_NAMESPACE, seed)


def deterministic_sanad_node_id(
    *,
    sanad_id: str,
    node_type: str,
    ordinal: int,
    input_refs: list[dict[str, str]],
    output_refs: list[dict[str, str]],
) -> str:
    """Generate a deterministic UUID v5 transmission node ID."""
    seed: dict[str, object] = {
        "sanad_id": sanad_id,
        "node_type": node_type,
        "ordinal": ordinal,
        "input_refs": sorted(input_refs, key=_canonical_json),
        "output_refs": sorted(output_refs, key=_canonical_json),
    }
    return _uuid5(SANAD_NODE_NAMESPACE, seed)


def deterministic_sanad_defect_id(
    *,
    sanad_id: str,
    claim_id: str,
    defect_type: str,
    severity: str,
    cure_protocol: str,
    evidence_ids: list[str],
) -> str:
    """Generate a deterministic UUID v5 defect ID."""
    seed: dict[str, object] = {
        "sanad_id": sanad_id,
        "claim_id": claim_id,
        "defect_type": defect_type,
        "severity": severity,
        "cure_protocol": cure_protocol,
        "evidence_ids": sorted(evidence_ids),
    }
    return _uuid5(SANAD_DEFECT_NAMESPACE, seed)


def aggregate_status(
    *,
    mappings: list[MethodologySanadMapping],
    rejections: list[MethodologySanadRejection],
) -> MethodologySanadMaterializationStatus:
    """Return aggregate materialization status."""
    if mappings and rejections:
        return MethodologySanadMaterializationStatus.PARTIAL
    if rejections:
        return MethodologySanadMaterializationStatus.FAILED
    return MethodologySanadMaterializationStatus.COMPLETED


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
