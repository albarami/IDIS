"""Models for legacy draft and Slice 6 neutral-output claim materialization."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.claim import Materiality
from idis.models.value_structs import ValueStruct, ValueStructType, parse_value_struct

_UNSAFE_LOCATOR_KEYS = frozenset(
    {"text", "raw_text", "text_excerpt", "document_name", "path", "uri"}
)
_UNSAFE_URI_PREFIXES = ("file://", "s3://", "http://", "https://")


class ClaimMaterializationStatus(StrEnum):
    """Aggregate materialization status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimMaterializationReason(StrEnum):
    """Machine-readable materialization rejection reasons."""

    STALE_OR_INVALID_DRAFT_ID = "stale_or_invalid_draft_id"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    SOURCE_SPAN_METADATA_MISMATCH = "source_span_metadata_mismatch"
    CLAIM_SERVICE_CREATE_FAILED = "claim_service_create_failed"
    BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    BELOW_DHABT_THRESHOLD = "below_dhabt_threshold"
    MISSING_GATE_METADATA = "missing_gate_metadata"
    MISSING_SOURCE_SPAN = "missing_source_span"
    MISSING_METHODOLOGY_LINKAGE = "missing_methodology_linkage"
    MALFORMED_CLAIM_DRAFT = "malformed_claim_draft"
    DUPLICATE_DRAFT_ID = "duplicate_draft_id"
    MISSING_CLAIM_TYPE = "missing_claim_type"
    UNSUPPORTED_ANSWER_TYPE = "unsupported_answer_type"
    MALFORMED_EXTRACTION_OUTPUT = "malformed_extraction_output"
    MISSING_VALUE_STRUCT = "missing_value_struct"
    DUPLICATE_EXTRACTION_OUTPUT_ID = "duplicate_extraction_output_id"
    NO_ACCEPTED_EXECUTION_OUTPUTS = "no_accepted_execution_outputs"


class MaterializedClaimType(StrEnum):
    """Domain-semantic claim type for Slice 6 run-scoped claims.

    This intentionally does not reuse `ClaimType`, which is a lifecycle/source
    enum (`primary`/`derived`) used by the calc loop guardrail.
    """

    FINANCIAL_METRIC = "FINANCIAL_METRIC"
    MARKET_SIZE = "MARKET_SIZE"
    COMPETITION = "COMPETITION"
    TEAM = "TEAM"
    TRACTION = "TRACTION"
    LEGAL = "LEGAL"
    TECH = "TECH"


class ClaimMaterializationBaseModel(BaseModel):
    """Base model for deterministic materialization data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _is_path_or_uri_like(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered.startswith(_UNSAFE_URI_PREFIXES):
        return True
    if stripped.startswith(("/", "\\")):
        return True
    return len(stripped) >= 2 and stripped[0].isalpha() and stripped[1] == ":"


def _safe_reference_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("field must not be blank")
    if _is_path_or_uri_like(cleaned):
        raise ValueError("field must not be path or URI-like")
    return cleaned


def _validate_safe_locator(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if isinstance(key, str) and key.strip().lower() in _UNSAFE_LOCATOR_KEYS:
                raise ValueError("locator must not contain raw text or location metadata")
            _validate_safe_locator(nested_value)
        return
    if isinstance(value, list):
        for item in value:
            _validate_safe_locator(item)
        return
    if isinstance(value, str) and _is_path_or_uri_like(value):
        raise ValueError("locator values must not be path or URI-like")


class MaterializedClaimSourceRef(ClaimMaterializationBaseModel):
    """Safe source reference for an in-memory materialized claim."""

    document_id: str
    source_span_id: str
    locator: dict[str, Any] | None = None

    @field_validator("document_id", "source_span_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _safe_reference_id(value)

    @field_validator("locator")
    @classmethod
    def _locator_safe(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        _validate_safe_locator(value)
        return value


class MaterializedClaimValueStruct(ClaimMaterializationBaseModel):
    """Typed Slice 6 claim value with source answer type metadata."""

    type: ValueStructType
    value: Decimal | int | str | bool
    unit: str | None = None
    currency: str | None = None
    time_window: str | None = None
    source_answer_type: str

    @field_validator("unit", "currency", "time_window")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("field must not be blank when provided")
        return value.strip() if value is not None else None

    @field_validator("source_answer_type")
    @classmethod
    def _source_answer_type_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_answer_type must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _required_financial_fields(self) -> MaterializedClaimValueStruct:
        if self.type == ValueStructType.MONETARY and (
            self.currency is None or self.unit is None or self.time_window is None
        ):
            raise ValueError("monetary values require currency, unit, and time_window")
        return self

    def to_value_struct(self) -> ValueStruct:
        """Convert to the existing typed `ValueStruct` hierarchy."""
        if self.type == ValueStructType.MONETARY:
            return parse_value_struct(
                {
                    "type": ValueStructType.MONETARY.value,
                    "amount": self.value,
                    "currency": self.currency,
                    "time_window": {"label": self.time_window},
                }
            )
        if self.type == ValueStructType.PERCENTAGE:
            return parse_value_struct(
                {
                    "type": ValueStructType.PERCENTAGE.value,
                    "value": self.value,
                    "time_window": (
                        {"label": self.time_window} if self.time_window is not None else None
                    ),
                }
            )
        if self.type == ValueStructType.COUNT:
            return parse_value_struct(
                {
                    "type": ValueStructType.COUNT.value,
                    "value": self.value,
                    "unit": self.unit,
                }
            )
        if self.type == ValueStructType.TEXT:
            return parse_value_struct(
                {
                    "type": ValueStructType.TEXT.value,
                    "value": str(self.value),
                }
            )
        raise ValueError(f"Unsupported materialized value type: {self.type.value}")


class RunScopedMaterializedClaim(ClaimMaterializationBaseModel):
    """In-memory governed claim boundary produced from neutral execution outputs."""

    claim_id: str | None = None
    tenant_id: str
    deal_id: str
    run_id: str
    claim_text: str
    claim_type: MaterializedClaimType
    value_struct: MaterializedClaimValueStruct
    materiality: Materiality = Materiality.MEDIUM
    source_refs: list[MaterializedClaimSourceRef]
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_text",
        "methodology_id",
        "methodology_version_id",
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

    @field_validator("claim_id")
    @classmethod
    def _claim_id_format(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("claim_mth_"):
            raise ValueError("claim_id must start with claim_mth_")
        return value

    @field_validator("source_refs")
    @classmethod
    def _source_refs_not_empty(
        cls, value: list[MaterializedClaimSourceRef]
    ) -> list[MaterializedClaimSourceRef]:
        if not value:
            raise ValueError("source_refs must not be empty")
        return value

    @model_validator(mode="after")
    def _set_deterministic_claim_id(self) -> RunScopedMaterializedClaim:
        if self.claim_id is None:
            self.claim_id = generate_methodology_materialized_claim_id(
                tenant_id=self.tenant_id,
                deal_id=self.deal_id,
                run_id=self.run_id,
                extraction_output_id=self.extraction_output_id,
                extraction_task_id=self.extraction_task_id,
                methodology_question_id=self.methodology_question_id,
                coverage_record_id=self.coverage_record_id,
                source_refs=self.source_refs,
                value_struct=self.value_struct,
            )
        return self


class RunScopedMaterializedClaimShell(ClaimMaterializationBaseModel):
    """Safe resume shell for an in-memory materialized claim."""

    claim_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    source_refs: list[MaterializedClaimSourceRef]
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "claim_id",
        "tenant_id",
        "deal_id",
        "run_id",
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

    @field_validator("source_refs")
    @classmethod
    def _source_refs_not_empty(
        cls, value: list[MaterializedClaimSourceRef]
    ) -> list[MaterializedClaimSourceRef]:
        if not value:
            raise ValueError("source_refs must not be empty")
        return value


def generate_methodology_materialized_claim_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    extraction_output_id: str,
    extraction_task_id: str,
    methodology_question_id: str,
    coverage_record_id: str,
    source_refs: list[MaterializedClaimSourceRef],
    value_struct: MaterializedClaimValueStruct,
) -> str:
    """Generate a deterministic run-scoped methodology claim ID."""
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "extraction_output_id": extraction_output_id,
        "extraction_task_id": extraction_task_id,
        "methodology_question_id": methodology_question_id,
        "coverage_record_id": coverage_record_id,
        "source_refs": sorted(
            (ref.model_dump(mode="json") for ref in source_refs),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        ),
        "value_struct": value_struct.model_dump(mode="json"),
    }
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return f"claim_mth_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


class MethodologyOutputClaimMapping(ClaimMaterializationBaseModel):
    """Mapping from neutral execution output to run-scoped claim."""

    extraction_output_id: str
    claim_id: str
    extraction_task_id: str
    methodology_question_id: str
    coverage_record_id: str
    document_id: str
    source_span_ids: list[str]

    @field_validator(
        "extraction_output_id",
        "claim_id",
        "extraction_task_id",
        "methodology_question_id",
        "coverage_record_id",
        "document_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("document_id")
    @classmethod
    def _document_id_safe(cls, value: str) -> str:
        return _safe_reference_id(value)

    @field_validator("source_span_ids")
    @classmethod
    def _source_span_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_span_ids must not be empty")
        cleaned = [_safe_reference_id(item) for item in value]
        return sorted(set(cleaned))


class MethodologyOutputClaimRejection(ClaimMaterializationBaseModel):
    """Rejected neutral execution output with machine-readable reason."""

    extraction_output_id: str | None = None
    reason: ClaimMaterializationReason
    reason_codes: list[str]
    message: str

    @field_validator("extraction_output_id")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("field must not be blank")
        return value.strip() if value is not None else None

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reason_codes must not contain blank values")
        return sorted(set(cleaned))

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _reason_code_contains_reason(self) -> MethodologyOutputClaimRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyOutputClaimMaterializationSummary(ClaimMaterializationBaseModel):
    """Safe aggregate summary for neutral-output claim materialization."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_outputs: int
    created_claim_count: int
    rejected_output_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_deterministic_json(self) -> str:
        """Serialize summary deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyOutputClaimMaterializationRunResult(ClaimMaterializationBaseModel):
    """Run-step-safe result for Slice 6 materialization."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: ClaimMaterializationStatus
    output_claim_mappings: list[MethodologyOutputClaimMapping] = Field(default_factory=list)
    rejected_outputs: list[MethodologyOutputClaimRejection] = Field(default_factory=list)
    summary: MethodologyOutputClaimMaterializationSummary

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe run-step summary without extracted answers or raw text."""
        return {
            "status": status or self.status.value,
            "claim_ids": [mapping.claim_id for mapping in self.output_claim_mappings],
            "output_claim_mappings": [
                mapping.model_dump(mode="json") for mapping in self.output_claim_mappings
            ],
            "rejected_outputs": [
                rejection.model_dump(mode="json") for rejection in self.rejected_outputs
            ],
            "summary": {
                "total_outputs": self.summary.total_outputs,
                "created_claim_count": self.summary.created_claim_count,
                "rejected_output_count": self.summary.rejected_output_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
            },
        }


class DraftClaimMapping(ClaimMaterializationBaseModel):
    """Mapping from a methodology claim draft to a persisted claim."""

    methodology_claim_draft_id: str
    claim_id: str
    extraction_task_id: str
    methodology_question_id: str
    document_id: str
    source_span_ids: list[str]

    @field_validator(
        "methodology_claim_draft_id",
        "claim_id",
        "extraction_task_id",
        "methodology_question_id",
        "document_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids")
    @classmethod
    def _source_span_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_span_ids must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source_span_ids must not contain blank values")
        return sorted(set(cleaned))


class ClaimMaterializationDraftRejection(ClaimMaterializationBaseModel):
    """Rejected draft with machine-readable reason."""

    methodology_claim_draft_id: str | None = None
    reason: ClaimMaterializationReason
    reason_codes: list[str]
    message: str

    @field_validator("methodology_claim_draft_id")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reason_codes must not contain blank values")
        return sorted(set(cleaned))

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _reason_code_contains_reason(self) -> ClaimMaterializationDraftRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyClaimMaterializationSummary(ClaimMaterializationBaseModel):
    """Deterministic summary of materialization output."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_drafts: int
    created_claim_count: int
    rejected_draft_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_deterministic_json(self) -> str:
        """Serialize summary deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class ClaimMaterializationResult(ClaimMaterializationBaseModel):
    """Top-level methodology claim materialization result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: ClaimMaterializationStatus
    draft_claim_mappings: list[DraftClaimMapping] = Field(default_factory=list)
    rejected_drafts: list[ClaimMaterializationDraftRejection] = Field(default_factory=list)
    summary: MethodologyClaimMaterializationSummary

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_deterministic_json(self) -> str:
        """Serialize result deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


def rejection(
    *,
    methodology_claim_draft_id: str | None,
    reason: ClaimMaterializationReason,
    message: str,
) -> ClaimMaterializationDraftRejection:
    """Build a standardized draft rejection."""
    return ClaimMaterializationDraftRejection(
        methodology_claim_draft_id=methodology_claim_draft_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
