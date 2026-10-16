"""Run-scoped document preflight models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)


class DocumentPreflightStatus(StrEnum):
    """Aggregate preflight status for a run corpus."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DocumentPreflightReason(StrEnum):
    """Stable reason code for one preflight decision."""

    READY = "READY"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    CONVERSION_REQUIRED = "CONVERSION_REQUIRED"
    OCR_REQUIRED = "OCR_REQUIRED"
    TOO_LARGE = "TOO_LARGE"
    ENCRYPTED_SOURCE = "ENCRYPTED_SOURCE"
    CORRUPTED_SOURCE = "CORRUPTED_SOURCE"
    UNKNOWN_PARSER_STATUS = "UNKNOWN_PARSER_STATUS"
    NO_USABLE_DOCUMENTS = "NO_USABLE_DOCUMENTS"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    NO_INGESTED_DOCUMENTS = "NO_INGESTED_DOCUMENTS"
    MISSING_SPANS = "MISSING_SPANS"
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED = "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"


class DocumentPreflightBaseModel(BaseModel):
    """Base model for deterministic document preflight payloads."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentPreflightSafeSummaryModel(BaseModel):
    """Exact persisted-summary model with no input coercion or extra fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class DocumentPreflightSpanReference(DocumentPreflightSafeSummaryModel):
    """Safe span reference for persisted run-step summaries."""

    span_id: StrictStr
    document_id: StrictStr
    locator: dict[StrictStr, Any]
    span_type: StrictStr
    content_hash: StrictStr | None

    @field_validator("span_id", "document_id", "span_type")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("field must be nonblank and canonical")
        return value

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_canonical(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 or null")
        return value


def document_preflight_span_order_key(
    span: DocumentPreflightSpanReference,
) -> tuple[str, str]:
    """Return the shared total order for persisted safe span references."""

    return (span.document_id, span.span_id)


_TRIAGE_BY_SUPPORT: dict[DocumentSupportStatus, frozenset[DocumentTriageStatus]] = {
    DocumentSupportStatus.SUPPORTED: frozenset({DocumentTriageStatus.READY}),
    DocumentSupportStatus.PARTIALLY_SUPPORTED: frozenset({DocumentTriageStatus.PARTIAL}),
    DocumentSupportStatus.UNSUPPORTED: frozenset({DocumentTriageStatus.UNSUPPORTED_SOURCE}),
    DocumentSupportStatus.ENCRYPTED: frozenset({DocumentTriageStatus.BLOCKED}),
    DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY: frozenset({DocumentTriageStatus.OCR_REQUIRED}),
    DocumentSupportStatus.TOO_LARGE: frozenset({DocumentTriageStatus.TOO_LARGE}),
    DocumentSupportStatus.CORRUPTED: frozenset({DocumentTriageStatus.BLOCKED}),
    DocumentSupportStatus.CONVERSION_REQUIRED: frozenset(
        {DocumentTriageStatus.CONVERSION_REQUIRED}
    ),
    DocumentSupportStatus.EXCLUDED_CONFIRMED: frozenset({DocumentTriageStatus.EXCLUDED_CONFIRMED}),
    DocumentSupportStatus.UNKNOWN: frozenset(
        {DocumentTriageStatus.UNKNOWN, DocumentTriageStatus.BLOCKED}
    ),
}

_TERMINAL_REASON_BY_SUPPORT: dict[DocumentSupportStatus, DocumentPreflightReason] = {
    DocumentSupportStatus.UNSUPPORTED: DocumentPreflightReason.UNSUPPORTED_SOURCE,
    DocumentSupportStatus.ENCRYPTED: DocumentPreflightReason.ENCRYPTED_SOURCE,
    DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY: DocumentPreflightReason.OCR_REQUIRED,
    DocumentSupportStatus.TOO_LARGE: DocumentPreflightReason.TOO_LARGE,
    DocumentSupportStatus.CORRUPTED: DocumentPreflightReason.CORRUPTED_SOURCE,
    DocumentSupportStatus.CONVERSION_REQUIRED: DocumentPreflightReason.CONVERSION_REQUIRED,
    DocumentSupportStatus.UNKNOWN: DocumentPreflightReason.UNKNOWN_PARSER_STATUS,
}


def document_preflight_reason_for_state(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    has_source_spans: bool,
) -> DocumentPreflightReason:
    """Return the sole producer reason for a normal support/triage/span state."""

    terminal_reason = _TERMINAL_REASON_BY_SUPPORT.get(support_status)
    if terminal_reason is not None:
        return terminal_reason
    if not has_source_spans:
        return DocumentPreflightReason.MISSING_SPANS
    if triage_status is DocumentTriageStatus.PARTIAL:
        return DocumentPreflightReason.PARTIAL_SUPPORT
    return DocumentPreflightReason.READY


def document_preflight_is_eligible_state(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    parser_usable_without_conversion: bool,
    has_source_spans: bool,
) -> bool:
    """Return the sole producer eligibility decision for a normal document."""

    return (
        parser_usable_without_conversion
        and support_status
        in {DocumentSupportStatus.SUPPORTED, DocumentSupportStatus.PARTIALLY_SUPPORTED}
        and triage_status in {DocumentTriageStatus.READY, DocumentTriageStatus.PARTIAL}
        and has_source_spans
    )


def _validate_document_preflight_state(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    usable_for_methodology_extraction: bool,
    reason: DocumentPreflightReason,
    reason_codes: Sequence[str],
    warning_codes: Sequence[str],
    fdd_category: FddDocumentCategory | None,
    cdd_category: CddDocumentCategory | None,
    secondary_fdd_categories: Sequence[FddDocumentCategory],
    secondary_cdd_categories: Sequence[CddDocumentCategory],
    methodology_target_areas: Sequence[str],
    source_spans: Sequence[DocumentPreflightSpanReference] | None,
) -> None:
    """Enforce the one producer state machine used before and after persistence."""

    if not reason_codes:
        raise ValueError("producer classification requires at least one reason code")

    exclusion_reason = DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    exclusion_markers = (
        support_status is DocumentSupportStatus.EXCLUDED_CONFIRMED,
        triage_status is DocumentTriageStatus.EXCLUDED_CONFIRMED,
        reason is exclusion_reason,
    )
    if any(exclusion_markers):
        if not all(exclusion_markers):
            raise ValueError("confirmed exclusion markers must be exact and complete")
        if list(reason_codes) != [exclusion_reason.value]:
            raise ValueError("confirmed exclusion reason codes must be exact")
        if warning_codes:
            raise ValueError("confirmed exclusion warning codes must be empty")
        if (
            usable_for_methodology_extraction
            or fdd_category is not None
            or cdd_category is not None
            or secondary_fdd_categories
            or secondary_cdd_categories
            or methodology_target_areas
        ):
            raise ValueError("confirmed exclusion producer state is invalid")
        if source_spans is not None and source_spans:
            raise ValueError("confirmed exclusion requires zero source spans")
        return

    if fdd_category is None or cdd_category is None:
        raise ValueError("normal producer classification requires primary categories")
    if not methodology_target_areas:
        raise ValueError("normal producer classification requires target areas")

    permitted_triage = _TRIAGE_BY_SUPPORT[support_status]
    if triage_status not in permitted_triage:
        raise ValueError("document support and triage states are inconsistent")

    expected_terminal_reason = _TERMINAL_REASON_BY_SUPPORT.get(support_status)
    if expected_terminal_reason is not None:
        if reason is not expected_terminal_reason:
            raise ValueError("document support and reason states are inconsistent")
        if usable_for_methodology_extraction:
            raise ValueError("terminal document support state cannot be usable")
    elif support_status is DocumentSupportStatus.SUPPORTED:
        if source_spans is None:
            if reason not in {DocumentPreflightReason.READY, DocumentPreflightReason.MISSING_SPANS}:
                raise ValueError("supported document reason is inconsistent")
        elif source_spans:
            if reason is not DocumentPreflightReason.READY or not usable_for_methodology_extraction:
                raise ValueError("supported document with spans must be READY and usable")
        elif (
            reason is not DocumentPreflightReason.MISSING_SPANS or usable_for_methodology_extraction
        ):
            raise ValueError("supported document without spans must be MISSING_SPANS and unusable")
    elif support_status is DocumentSupportStatus.PARTIALLY_SUPPORTED:
        if source_spans is None:
            if reason not in {
                DocumentPreflightReason.PARTIAL_SUPPORT,
                DocumentPreflightReason.MISSING_SPANS,
            }:
                raise ValueError("partially supported document reason is inconsistent")
        elif source_spans:
            if reason is not DocumentPreflightReason.PARTIAL_SUPPORT:
                raise ValueError("partially supported document with spans requires PARTIAL_SUPPORT")
        elif (
            reason is not DocumentPreflightReason.MISSING_SPANS or usable_for_methodology_extraction
        ):
            raise ValueError(
                "partially supported document without spans must be MISSING_SPANS and unusable"
            )
    else:  # pragma: no cover - all non-exclusion enum members are covered above.
        raise ValueError("document support state is not recognized")

    if source_spans is not None:
        expected_reason = document_preflight_reason_for_state(
            support_status=support_status,
            triage_status=triage_status,
            has_source_spans=bool(source_spans),
        )
        if reason is not expected_reason:
            raise ValueError("document reason contradicts support, triage, or span state")


class RunDocumentPreflightDecision(DocumentPreflightBaseModel):
    """Preflight decision for one document in one run."""

    tenant_id: str
    deal_id: str
    run_id: str
    document_id: str
    classification_id: str
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    usable_for_methodology_extraction: bool
    reason: DocumentPreflightReason
    reason_codes: list[str]
    warning_codes: list[str] = Field(default_factory=list)
    source_spans: list[DocumentPreflightSpanReference] = Field(default_factory=list)
    fdd_category: FddDocumentCategory | None = None
    cdd_category: CddDocumentCategory | None = None
    secondary_fdd_categories: list[FddDocumentCategory]
    secondary_cdd_categories: list[CddDocumentCategory]
    methodology_target_areas: list[str] = Field(default_factory=list)

    @field_validator("tenant_id", "deal_id", "run_id", "document_id", "classification_id")
    @classmethod
    def _ids_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value.strip()

    @field_validator("reason_codes", "warning_codes", "methodology_target_areas")
    @classmethod
    def _string_lists_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @field_validator("secondary_fdd_categories", "secondary_cdd_categories")
    @classmethod
    def _secondary_categories_are_canonical(cls, value: list[Any]) -> list[Any]:
        canonical_values = [item.value for item in value]
        if canonical_values != sorted(set(canonical_values)):
            raise ValueError("secondary categories must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _matches_producer_state_machine(self) -> RunDocumentPreflightDecision:
        _validate_document_preflight_state(
            support_status=self.support_status,
            triage_status=self.triage_status,
            usable_for_methodology_extraction=self.usable_for_methodology_extraction,
            reason=self.reason,
            reason_codes=self.reason_codes,
            warning_codes=self.warning_codes,
            fdd_category=self.fdd_category,
            cdd_category=self.cdd_category,
            secondary_fdd_categories=self.secondary_fdd_categories,
            secondary_cdd_categories=self.secondary_cdd_categories,
            methodology_target_areas=self.methodology_target_areas,
            source_spans=self.source_spans,
        )
        return self


class DocumentPreflightClassificationSummaryRecord(DocumentPreflightSafeSummaryModel):
    """Exact safe classification record shared by producer and strict consumer."""

    classification_id: StrictStr
    document_id: StrictStr
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    reason: DocumentPreflightReason
    reason_codes: list[StrictStr]
    warning_codes: list[StrictStr]
    fdd_category: FddDocumentCategory | None
    cdd_category: CddDocumentCategory | None
    secondary_fdd_categories: list[FddDocumentCategory]
    secondary_cdd_categories: list[CddDocumentCategory]
    methodology_target_areas: list[StrictStr]
    usable_for_methodology_extraction: StrictBool

    @field_validator("support_status", "triage_status", "reason", mode="before")
    @classmethod
    def _required_enum_is_exact_string(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("safe-summary enum must be an exact string")
        return value

    @field_validator("fdd_category", "cdd_category", mode="before")
    @classmethod
    def _optional_enum_is_exact_string_or_null(cls, value: object) -> object:
        if value is not None and type(value) is not str:
            raise ValueError("safe-summary category must be an exact string or null")
        return value

    @field_validator("classification_id", "document_id")
    @classmethod
    def _identifier_is_canonical(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("identifier must be nonblank and canonical")
        return value

    @field_validator(
        "reason_codes",
        "warning_codes",
        "methodology_target_areas",
        mode="before",
    )
    @classmethod
    def _requires_exact_string_list(cls, value: object) -> object:
        if type(value) is not list:
            raise ValueError("safe-summary collection must be a list")
        if any(type(item) is not str for item in value):
            raise ValueError("safe-summary list items must be exact strings")
        return value

    @field_validator(
        "secondary_fdd_categories",
        "secondary_cdd_categories",
        mode="before",
    )
    @classmethod
    def _requires_exact_enum_string_list(cls, value: object) -> object:
        if type(value) is not list:
            raise ValueError("safe-summary category collection must be a list")
        if any(type(item) is not str for item in value):
            raise ValueError("safe-summary categories must be exact strings")
        return value

    @field_validator("reason_codes", "warning_codes", "methodology_target_areas")
    @classmethod
    def _string_list_is_canonical(cls, value: list[str]) -> list[str]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("safe-summary strings must be nonblank and canonical")
        if value != sorted(set(value)):
            raise ValueError("safe-summary strings must be sorted and unique")
        return value

    @field_validator("secondary_fdd_categories", "secondary_cdd_categories")
    @classmethod
    def _category_list_is_canonical(cls, value: list[Any]) -> list[Any]:
        serialized = [item.value for item in value]
        if serialized != sorted(set(serialized)):
            raise ValueError("secondary categories must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _matches_a_producer_permitted_state(
        self,
    ) -> DocumentPreflightClassificationSummaryRecord:
        _validate_document_preflight_state(
            support_status=self.support_status,
            triage_status=self.triage_status,
            usable_for_methodology_extraction=self.usable_for_methodology_extraction,
            reason=self.reason,
            reason_codes=self.reason_codes,
            warning_codes=self.warning_codes,
            fdd_category=self.fdd_category,
            cdd_category=self.cdd_category,
            secondary_fdd_categories=self.secondary_fdd_categories,
            secondary_cdd_categories=self.secondary_cdd_categories,
            methodology_target_areas=self.methodology_target_areas,
            source_spans=None,
        )
        return self

    @classmethod
    def from_decision(
        cls,
        decision: RunDocumentPreflightDecision,
    ) -> DocumentPreflightClassificationSummaryRecord:
        """Project an internal decision into its canonical persisted-safe record."""

        return cls.model_validate(
            {
                "classification_id": decision.classification_id,
                "document_id": decision.document_id,
                "support_status": decision.support_status.value,
                "triage_status": decision.triage_status.value,
                "reason": decision.reason.value,
                "reason_codes": sorted(set(decision.reason_codes)),
                "warning_codes": sorted(set(decision.warning_codes)),
                "fdd_category": decision.fdd_category.value if decision.fdd_category else None,
                "cdd_category": decision.cdd_category.value if decision.cdd_category else None,
                "secondary_fdd_categories": [
                    category.value for category in decision.secondary_fdd_categories
                ],
                "secondary_cdd_categories": [
                    category.value for category in decision.secondary_cdd_categories
                ],
                "methodology_target_areas": sorted(set(decision.methodology_target_areas)),
                "usable_for_methodology_extraction": (decision.usable_for_methodology_extraction),
            }
        )


class DocumentPreflightSummary(DocumentPreflightSafeSummaryModel):
    """Aggregate preflight counts for a run."""

    tenant_id: StrictStr
    deal_id: StrictStr
    run_id: StrictStr
    total_documents: StrictInt = Field(ge=0)
    usable_documents: StrictInt = Field(ge=0)
    excluded_confirmed_documents: StrictInt = Field(ge=0)
    blocked_documents: StrictInt = Field(ge=0)
    by_support_status: dict[StrictStr, StrictInt]
    by_triage_status: dict[StrictStr, StrictInt]
    by_reason: dict[StrictStr, StrictInt]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _scope_identifier_is_canonical(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("summary scope identifier must be nonblank and canonical")
        return value

    @field_validator("by_support_status", "by_triage_status", "by_reason", mode="before")
    @classmethod
    def _count_map_is_exact_and_canonical(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("summary count map must be an exact dictionary")
        typed_value = value
        if any(type(key) is not str or not key or key != key.strip() for key in typed_value):
            raise ValueError("summary count-map keys must be canonical strings")
        if any(type(count) is not int or count < 0 for count in typed_value.values()):
            raise ValueError("summary count-map values must be nonnegative exact integers")
        return dict(sorted(typed_value.items()))


def _summary_from_records(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    records: Sequence[RunDocumentPreflightDecision | DocumentPreflightClassificationSummaryRecord],
) -> DocumentPreflightSummary:
    exclusion_reason = DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    return DocumentPreflightSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_documents=len(records),
        usable_documents=sum(1 for record in records if record.usable_for_methodology_extraction),
        excluded_confirmed_documents=sum(
            1 for record in records if record.reason is exclusion_reason
        ),
        blocked_documents=sum(
            1
            for record in records
            if not record.usable_for_methodology_extraction
            and record.reason is not exclusion_reason
        ),
        by_support_status=_counter(record.support_status.value for record in records),
        by_triage_status=_counter(record.triage_status.value for record in records),
        by_reason=_counter(record.reason.value for record in records),
    )


def document_preflight_status_for_decisions(
    decisions: Sequence[
        RunDocumentPreflightDecision | DocumentPreflightClassificationSummaryRecord
    ],
) -> DocumentPreflightStatus:
    """Derive the sole valid aggregate status from fully validated decisions."""

    if not decisions:
        return DocumentPreflightStatus.FAILED
    usable_count = sum(1 for decision in decisions if decision.usable_for_methodology_extraction)
    if usable_count == 0:
        return DocumentPreflightStatus.FAILED
    exclusion_reason = DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    if any(
        not decision.usable_for_methodology_extraction and decision.reason is not exclusion_reason
        for decision in decisions
    ):
        return DocumentPreflightStatus.PARTIAL
    if any(decision.reason is DocumentPreflightReason.PARTIAL_SUPPORT for decision in decisions):
        return DocumentPreflightStatus.PARTIAL
    return DocumentPreflightStatus.COMPLETED


class DocumentPreflightRunStepSummary(DocumentPreflightSafeSummaryModel):
    """Complete structural envelope shared by legacy and strict run composition."""

    status: DocumentPreflightStatus
    summary: DocumentPreflightSummary
    eligible_document_ids: list[StrictStr]
    excluded_confirmed_document_ids: list[StrictStr]
    blocked_document_ids: list[StrictStr]
    classifications: list[DocumentPreflightClassificationSummaryRecord]
    source_spans_by_document_id: dict[StrictStr, list[DocumentPreflightSpanReference]]

    @field_validator("status", mode="before")
    @classmethod
    def _status_is_exact_string(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("preflight status must be an exact string")
        return value

    @field_validator("summary", mode="before")
    @classmethod
    def _summary_is_an_exact_dictionary(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("preflight aggregate summary must be an exact dictionary")
        return value

    @field_validator(
        "eligible_document_ids",
        "excluded_confirmed_document_ids",
        "blocked_document_ids",
        mode="before",
    )
    @classmethod
    def _partition_is_exact_string_list(cls, value: object) -> object:
        if type(value) is not list:
            raise ValueError("preflight document partition must be an exact list")
        if any(type(item) is not str for item in value):
            raise ValueError("preflight document partition items must be exact strings")
        return value

    @field_validator(
        "eligible_document_ids",
        "excluded_confirmed_document_ids",
        "blocked_document_ids",
    )
    @classmethod
    def _partition_is_canonical(cls, value: list[str]) -> list[str]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("preflight document partition IDs must be canonical")
        if value != sorted(set(value)):
            raise ValueError("preflight document partition must be sorted and unique")
        return value

    @field_validator("classifications", mode="before")
    @classmethod
    def _classifications_are_an_exact_list(cls, value: object) -> object:
        if type(value) is not list:
            raise ValueError("preflight classifications must be an exact list")
        if any(type(item) is not dict for item in value):
            raise ValueError("preflight classification items must be exact dictionaries")
        return value

    @field_validator("source_spans_by_document_id", mode="before")
    @classmethod
    def _source_map_is_exact_and_canonical(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("preflight source map must be an exact dictionary")
        typed_value = value
        if any(
            type(document_id) is not str or not document_id or document_id != document_id.strip()
            for document_id in typed_value
        ):
            raise ValueError("preflight source-map document IDs must be canonical strings")
        if any(type(spans) is not list for spans in typed_value.values()):
            raise ValueError("preflight source-map values must be exact lists")
        if any(type(span) is not dict for spans in typed_value.values() for span in spans):
            raise ValueError("preflight source-span items must be exact dictionaries")
        return {document_id: typed_value[document_id] for document_id in sorted(typed_value)}

    @model_validator(mode="after")
    def _validate_complete_envelope(self) -> DocumentPreflightRunStepSummary:
        identities = [
            (record.document_id, record.classification_id) for record in self.classifications
        ]
        document_ids = [record.document_id for record in self.classifications]
        classification_ids = [record.classification_id for record in self.classifications]
        if identities != sorted(identities):
            raise ValueError("preflight classifications must be canonically ordered")
        if len(document_ids) != len(set(document_ids)) or len(classification_ids) != len(
            set(classification_ids)
        ):
            raise ValueError("preflight classification identities must be unique")
        if set(self.source_spans_by_document_id) != set(document_ids):
            raise ValueError("preflight source-map keys must equal classification document IDs")

        record_by_document_id = {record.document_id: record for record in self.classifications}
        for document_id in sorted(self.source_spans_by_document_id):
            spans = self.source_spans_by_document_id[document_id]
            if any(span.document_id != document_id for span in spans):
                raise ValueError("source span document_id mismatch")
            span_ids = [span.span_id for span in spans]
            if len(span_ids) != len(set(span_ids)):
                raise ValueError("preflight source span IDs must be unique")
            if spans != sorted(spans, key=document_preflight_span_order_key):
                raise ValueError("preflight source spans must be canonically ordered")
            record = record_by_document_id[document_id]
            _validate_document_preflight_state(
                support_status=record.support_status,
                triage_status=record.triage_status,
                usable_for_methodology_extraction=record.usable_for_methodology_extraction,
                reason=record.reason,
                reason_codes=record.reason_codes,
                warning_codes=record.warning_codes,
                fdd_category=record.fdd_category,
                cdd_category=record.cdd_category,
                secondary_fdd_categories=record.secondary_fdd_categories,
                secondary_cdd_categories=record.secondary_cdd_categories,
                methodology_target_areas=record.methodology_target_areas,
                source_spans=spans,
            )

        partition_sets = (
            set(self.eligible_document_ids),
            set(self.excluded_confirmed_document_ids),
            set(self.blocked_document_ids),
        )
        if any(
            partition_sets[left] & partition_sets[right] for left, right in ((0, 1), (0, 2), (1, 2))
        ):
            raise ValueError("preflight document partitions must be disjoint")
        if set().union(*partition_sets) != set(document_ids):
            raise ValueError("preflight document partitions must cover every classification")

        expected_eligible = sorted(
            record.document_id
            for record in self.classifications
            if record.usable_for_methodology_extraction
        )
        exclusion_reason = DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        expected_excluded = sorted(
            record.document_id
            for record in self.classifications
            if record.reason is exclusion_reason
        )
        expected_blocked = sorted(
            record.document_id
            for record in self.classifications
            if not record.usable_for_methodology_extraction
            and record.reason is not exclusion_reason
        )
        if (
            self.eligible_document_ids != expected_eligible
            or self.excluded_confirmed_document_ids != expected_excluded
            or self.blocked_document_ids != expected_blocked
        ):
            raise ValueError("preflight document partitions contradict classification states")

        expected_summary = _summary_from_records(
            tenant_id=self.summary.tenant_id,
            deal_id=self.summary.deal_id,
            run_id=self.summary.run_id,
            records=self.classifications,
        )
        if self.summary != expected_summary:
            raise ValueError("preflight aggregate summary contradicts classifications")
        expected_status = document_preflight_status_for_decisions(self.classifications)
        if self.status is not expected_status:
            raise ValueError("preflight status contradicts classifications")
        return self

    def require_scope(self, *, tenant_id: str, deal_id: str, run_id: str) -> None:
        """Require the nested persisted scope to equal the requested durable run."""

        if (self.summary.tenant_id, self.summary.deal_id, self.summary.run_id) != (
            tenant_id,
            deal_id,
            run_id,
        ):
            raise ValueError("document preflight run-step summary scope mismatch")

    @classmethod
    def from_result(cls, result: DocumentPreflightResult) -> DocumentPreflightRunStepSummary:
        """Build and revalidate the exact persisted envelope from a producer result."""

        for decision in result.decisions:
            if (decision.tenant_id, decision.deal_id, decision.run_id) != (
                result.tenant_id,
                result.deal_id,
                result.run_id,
            ):
                raise ValueError("preflight decision scope mismatch")
        expected_summary = _summary_from_records(
            tenant_id=result.tenant_id,
            deal_id=result.deal_id,
            run_id=result.run_id,
            records=result.decisions,
        )
        if result.summary != expected_summary:
            raise ValueError("caller-supplied preflight summary is inconsistent")
        ordered_decisions = sorted(
            result.decisions,
            key=lambda decision: (decision.document_id, decision.classification_id),
        )
        return cls.model_validate(
            {
                "status": result.status.value,
                "summary": expected_summary.model_dump(mode="json"),
                "eligible_document_ids": result.eligible_document_ids,
                "excluded_confirmed_document_ids": result.excluded_confirmed_document_ids,
                "blocked_document_ids": result.blocked_document_ids,
                "classifications": [
                    DocumentPreflightClassificationSummaryRecord.from_decision(decision).model_dump(
                        mode="json"
                    )
                    for decision in ordered_decisions
                ],
                "source_spans_by_document_id": {
                    decision.document_id: [
                        span.model_dump(mode="json")
                        for span in sorted(
                            decision.source_spans,
                            key=document_preflight_span_order_key,
                        )
                    ]
                    for decision in ordered_decisions
                },
            }
        )


class DocumentPreflightResult(DocumentPreflightBaseModel):
    """Run-scoped document preflight result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: DocumentPreflightStatus
    decisions: list[RunDocumentPreflightDecision]
    summary: DocumentPreflightSummary | None = None

    @model_validator(mode="after")
    def _ensure_summary(self) -> DocumentPreflightResult:
        document_ids = [decision.document_id for decision in self.decisions]
        classification_ids = [decision.classification_id for decision in self.decisions]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("preflight decision document IDs must be unique")
        if len(classification_ids) != len(set(classification_ids)):
            raise ValueError("preflight classification IDs must be unique")
        for decision in self.decisions:
            if (decision.tenant_id, decision.deal_id, decision.run_id) != (
                self.tenant_id,
                self.deal_id,
                self.run_id,
            ):
                raise ValueError("preflight decision scope mismatch")
            span_ids = [span.span_id for span in decision.source_spans]
            if len(span_ids) != len(set(span_ids)):
                raise ValueError("preflight source span IDs must be unique per document")
            if any(span.document_id != decision.document_id for span in decision.source_spans):
                raise ValueError("preflight source span document ID mismatch")
        expected_summary = _summary_from_records(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            records=self.decisions,
        )
        if self.summary is None:
            self.summary = expected_summary
        elif self.summary != expected_summary:
            raise ValueError("caller-supplied preflight summary is inconsistent")
        expected_status = document_preflight_status_for_decisions(self.decisions)
        if self.status is not expected_status:
            raise ValueError("caller-supplied preflight status is inconsistent")
        return self

    @property
    def eligible_document_ids(self) -> list[str]:
        """Document IDs eligible for downstream extraction."""
        return sorted(
            decision.document_id
            for decision in self.decisions
            if decision.usable_for_methodology_extraction
        )

    @property
    def blocked_document_ids(self) -> list[str]:
        """Document IDs blocked from downstream extraction."""
        return sorted(
            decision.document_id
            for decision in self.decisions
            if not decision.usable_for_methodology_extraction
            and decision.reason != DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        )

    @property
    def excluded_confirmed_document_ids(self) -> list[str]:
        """Document IDs durably excluded by a current signed attestation."""
        return sorted(
            decision.document_id
            for decision in self.decisions
            if decision.reason == DocumentPreflightReason.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        )

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for tests and audit stability."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def to_run_step_summary(self) -> dict[str, Any]:
        """Return a safe run-step summary with no raw span text."""
        return DocumentPreflightRunStepSummary.from_result(self).model_dump(mode="json")


def _counter(items: list[str] | Any) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
