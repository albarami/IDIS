"""Typed contracts for durable, task-aware methodology extraction.

The models in this module are deliberately provider-neutral at the persistence
boundary.  They preserve the complete Anthropic proof needed by the strict path,
while keeping hermetic test transport receipts explicitly non-live.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date as Date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.methodology_pricing import compute_anthropic_usd
from idis.models.value_structs import (
    BooleanValue,
    CountValue,
    DateValue,
    MonetaryValue,
    PercentageValue,
    RangeValue,
    TableRowValue,
    TextValue,
    ValueStruct,
    parse_value_struct,
)

METHODOLOGY_FACTUAL_OUTPUT_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://idis.local/task6/methodology-factual-output/v1"
)
METHODOLOGY_CLAIM_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://idis.local/task6/canonical-methodology-claim/v1"
)
METHODOLOGY_RECORD_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://idis.local/task6/methodology-record/v1"
)
METHODOLOGY_RESULT_LINK_NAMESPACE = uuid.UUID("6c0ed11d-42cf-5746-aada-f54543224172")

METHODOLOGY_TABLE_CONTRACT_VERSION = "methodology-table-contract-v1"
METHODOLOGY_TABLE_COLUMNS = ("row_label", "metric", "value", "unit", "period")
METHODOLOGY_SEARCH_SCOPE_CONTRACT_VERSION = "methodology-searched-scope-v1"
CLAIM_NORMALIZATION_RULE_VERSION: Literal["claim-normalization-v1"] = "claim-normalization-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_8_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_COVERAGE_RECORD_ID_RE = re.compile(r"^mc_[0-9a-f]{24}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def _require_non_blank(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("field must not be blank")
    return normalized


def _require_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("field must be a lowercase SHA-256 digest")
    return value


def validate_methodology_source_text_sha256(*, text: object, content_sha256: object) -> None:
    """Authorize exact UTF-8 source text against its durable content digest."""

    if (
        type(text) is not str
        or not text.strip()
        or type(content_sha256) is not str
        or _SHA256_RE.fullmatch(content_sha256) is None
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != content_sha256
    ):
        raise ValueError("source text does not match content SHA-256")


def _require_uuid(value: str) -> str:
    parsed = uuid.UUID(str(value))
    return str(parsed)


def _require_coverage_record_id(value: str) -> str:
    if not _COVERAGE_RECORD_ID_RE.fullmatch(value):
        raise ValueError("field must be a Task-5 methodology coverage record ID")
    return value


def _normalize_text(value: str, *, casefold: bool = False) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise ValueError("normalized text must not be blank")
    return normalized.casefold() if casefold else normalized


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("numeric values must be finite")
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        return "0"
    return normalized


def _canonical_methodology_unit(*, value: ValueStruct, declared_unit: str) -> str:
    """Derive claim units from the typed value under claim-normalization-v1."""

    if isinstance(value, MonetaryValue):
        return value.currency.value.upper()
    if isinstance(value, PercentageValue):
        return "ratio"
    if isinstance(value, DateValue):
        return "date"
    if isinstance(value, BooleanValue):
        return "boolean"
    if isinstance(value, TableRowValue):
        return "table_row"
    if isinstance(value, TextValue):
        return "text"
    if isinstance(value, CountValue):
        return _normalize_text(value.unit or declared_unit, casefold=True)
    if isinstance(value, RangeValue):
        return _normalize_text(value.unit, casefold=True)
    raise TypeError("unsupported methodology typed value")


def _canonical_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_data(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, Date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _canonical_data(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_data(item) for item in value]
    if isinstance(value, str):
        return _normalize_text(value)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a typed value with the Task-6 pinned canonical JSON form."""

    return json.dumps(
        _canonical_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _exact_json_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _exact_json_data(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, Date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _exact_json_data(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_exact_json_data(item) for item in value]
    return value


def exact_canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON for wire/proof domains without altering string bytes."""

    return json.dumps(
        _exact_json_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class MethodologyExtractionAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class MethodologyCandidateSpan(_StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    document_id: str
    span_id: str
    ordinal: int = Field(ge=1)
    content_sha256: str
    locator_sha256: str
    text: str

    _uuid_fields = field_validator("document_id", "span_id")(_require_uuid)
    _sha_fields = field_validator("content_sha256", "locator_sha256")(_require_sha256)

    @field_validator("text")
    @classmethod
    def preserve_non_blank_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("candidate span text must not be blank")
        return value

    @model_validator(mode="after")
    def exact_text_matches_content_sha256(self) -> MethodologyCandidateSpan:
        validate_methodology_source_text_sha256(
            text=self.text,
            content_sha256=self.content_sha256,
        )
        return self


def compute_methodology_searched_scope_sha256(
    candidate_spans: Sequence[MethodologyCandidateSpan],
) -> str:
    """Bind the complete admitted candidate identity, excluding raw private text."""

    if not candidate_spans:
        raise ValueError("searched scope requires at least one admitted span")
    ordered = tuple(sorted(candidate_spans, key=lambda span: (span.ordinal, span.span_id)))
    ordinals = tuple(span.ordinal for span in ordered)
    span_ids = tuple(span.span_id for span in ordered)
    source_locator_pairs = tuple((span.span_id, span.locator_sha256) for span in ordered)
    if (
        len(ordinals) != len(set(ordinals))
        or len(span_ids) != len(set(span_ids))
        or len(source_locator_pairs) != len(set(source_locator_pairs))
    ):
        raise ValueError("searched scope candidate identities must be unique")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_version": METHODOLOGY_SEARCH_SCOPE_CONTRACT_VERSION,
                "candidate_spans": [
                    {
                        "document_id": span.document_id,
                        "span_id": span.span_id,
                        "ordinal": span.ordinal,
                        "content_sha256": span.content_sha256,
                        "locator_sha256": span.locator_sha256,
                    }
                    for span in ordered
                ],
            }
        )
    ).hexdigest()


class CDDMethodologySourceTrace(_StrictModel):
    """Immutable checked-in commercial methodology section trace."""

    kind: Literal["cdd_section"]
    source_section: str
    source_sha256: str

    _section = field_validator("source_section")(_require_non_blank)
    _source_hash = field_validator("source_sha256")(_require_sha256)


class FDDMethodologySourceTrace(_StrictModel):
    """Immutable operator-workbook physical row trace."""

    kind: Literal["fdd_workbook_row"]
    sheet_name: str
    row_number: int = Field(ge=1)
    anchor_cell: str
    source_sha256: str

    _sheet = field_validator("sheet_name")(_require_non_blank)
    _source_hash = field_validator("source_sha256")(_require_sha256)

    @field_validator("anchor_cell")
    @classmethod
    def valid_anchor(cls, value: str) -> str:
        cleaned = value.strip()
        if re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cleaned) is None:
            raise ValueError("FDD anchor_cell must be an uppercase A1 coordinate")
        return cleaned


MethodologyExtractorSourceTrace = Annotated[
    CDDMethodologySourceTrace | FDDMethodologySourceTrace,
    Field(discriminator="kind"),
]


class MethodologyTaskExtractorRequest(_StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    tenant_id: str
    deal_id: str
    run_id: str
    extraction_task_id: str
    task_input_sha256: str
    methodology_type: Literal["commercial_dd", "financial_dd"]
    methodology_version_id: str
    methodology_version_sha256: str
    methodology_question_id: str
    methodology_question_record_id: str
    methodology_question_sha256: str
    source_trace: MethodologyExtractorSourceTrace
    question_text: str
    expected_answer_schema: dict[str, Any]
    required_evidence: tuple[dict[str, Any], ...]
    candidate_spans: tuple[MethodologyCandidateSpan, ...]
    predicates: tuple[str, ...]
    validation_requirements: tuple[str, ...]
    required_calculations: tuple[dict[str, Any], ...]

    _uuid_fields = field_validator(
        "tenant_id", "deal_id", "run_id", "methodology_question_record_id"
    )(_require_uuid)
    _sha_fields = field_validator(
        "task_input_sha256", "methodology_version_sha256", "methodology_question_sha256"
    )(_require_sha256)
    _text_fields = field_validator(
        "extraction_task_id",
        "methodology_version_id",
        "methodology_question_id",
    )(_require_non_blank)

    @field_validator("question_text")
    @classmethod
    def exact_non_blank_question(cls, value: str) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("question_text must contain non-whitespace content")
        return value

    @field_validator(
        "required_evidence",
        "candidate_spans",
        "predicates",
        "validation_requirements",
    )
    @classmethod
    def non_empty_tuple(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("task-aware extractor collections must not be empty")
        return value

    @field_validator("expected_answer_schema")
    @classmethod
    def non_empty_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("expected_answer_schema must not be empty")
        return value

    @field_validator("predicates", "validation_requirements")
    @classmethod
    def non_blank_strings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_non_blank(item) for item in value)

    @model_validator(mode="after")
    def unique_candidate_identity(self) -> MethodologyTaskExtractorRequest:
        span_ids = [span.span_id for span in self.candidate_spans]
        ordinals = [span.ordinal for span in self.candidate_spans]
        source_locator_pairs = [
            (span.span_id, span.locator_sha256) for span in self.candidate_spans
        ]
        if (
            len(span_ids) != len(set(span_ids))
            or len(ordinals) != len(set(ordinals))
            or len(source_locator_pairs) != len(set(source_locator_pairs))
        ):
            raise ValueError("candidate span identities must be unique")
        if self.methodology_type == "commercial_dd" and not isinstance(
            self.source_trace, CDDMethodologySourceTrace
        ):
            raise ValueError("commercial_dd requires a CDD section trace")
        if self.methodology_type == "financial_dd" and not isinstance(
            self.source_trace, FDDMethodologySourceTrace
        ):
            raise ValueError("financial_dd requires an FDD workbook-row trace")
        return self

    @property
    def source_sheet_or_section(self) -> str:
        if isinstance(self.source_trace, CDDMethodologySourceTrace):
            return self.source_trace.source_section
        return self.source_trace.sheet_name

    @property
    def source_row_number(self) -> int | None:
        if isinstance(self.source_trace, FDDMethodologySourceTrace):
            return self.source_trace.row_number
        return None

    @property
    def source_anchor_cell(self) -> str | None:
        if isinstance(self.source_trace, FDDMethodologySourceTrace):
            return self.source_trace.anchor_cell
        return None


class MethodologyPeriod(_StrictModel):
    kind: Literal["none", "as_of", "range"]
    date: Date | None = None
    start_date: Date | None = None
    end_date: Date | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> MethodologyPeriod:
        if self.kind == "none":
            if self.date is not None or self.start_date is not None or self.end_date is not None:
                raise ValueError("none periods carry no date fields")
        elif self.kind == "as_of":
            if self.date is None or self.start_date is not None or self.end_date is not None:
                raise ValueError("as_of periods require only date")
        elif self.date is not None or self.start_date is None or self.end_date is None:
            raise ValueError("range periods require only start_date and end_date")
        elif self.start_date > self.end_date:
            raise ValueError("period start_date cannot exceed end_date")
        return self

    def normalized(self) -> str:
        if self.kind == "none":
            return "none"
        if self.kind == "as_of":
            return f"as_of:{self.date.isoformat()}"  # type: ignore[union-attr]
        return f"range:{self.start_date.isoformat()}/{self.end_date.isoformat()}"  # type: ignore[union-attr]


class MethodologyGapFinding(_StrictModel):
    code: str
    finding: str

    _non_blank_fields = field_validator("code", "finding")(_require_non_blank)


class MethodologyFactualOutput(_StrictModel):
    factual_output_id: str | None = None
    subject: str
    methodology_question_record_id: str | None = None
    predicate: str
    claim_class: str
    value: ValueStruct
    period: MethodologyPeriod
    unit: str
    claim_text: str
    accepted_source_span_ids: tuple[str, ...]
    accepted_locator_sha256s: tuple[str, ...]

    _uuid_fields = field_validator("methodology_question_record_id")(
        lambda value: _require_uuid(value) if value is not None else None
    )
    _sha_fields = field_validator("accepted_locator_sha256s")(
        lambda values: tuple(_require_sha256(value) for value in values)
    )
    _text_fields = field_validator("subject", "predicate", "claim_class", "unit", "claim_text")(
        _require_non_blank
    )

    @field_validator("accepted_source_span_ids")
    @classmethod
    def valid_source_span_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("accepted_source_span_ids must not be empty")
        validated = tuple(_require_uuid(value) for value in values)
        if len(validated) != len(set(validated)):
            raise ValueError("accepted_source_span_ids must be unique")
        return validated

    @field_validator("accepted_locator_sha256s")
    @classmethod
    def valid_locator_set(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("accepted_locator_sha256s must not be empty")
        return values

    @model_validator(mode="after")
    def deterministic_factual_output_id(self) -> MethodologyFactualOutput:
        if len(self.accepted_source_span_ids) != len(self.accepted_locator_sha256s):
            raise ValueError("accepted span and locator sets must have equal cardinality")
        paired_locators = tuple(
            sorted(
                zip(self.accepted_source_span_ids, self.accepted_locator_sha256s, strict=True),
                key=lambda pair: pair[0],
            )
        )
        if len(paired_locators) != len(set(paired_locators)):
            raise ValueError("accepted source/locator pairs must be unique")
        object.__setattr__(
            self,
            "accepted_source_span_ids",
            tuple(span_id for span_id, _locator_sha256 in paired_locators),
        )
        object.__setattr__(
            self,
            "accepted_locator_sha256s",
            tuple(locator_sha256 for _span_id, locator_sha256 in paired_locators),
        )
        question_id = self.methodology_question_record_id
        if question_id is None:
            raise ValueError("methodology_question_record_id is required")
        canonical_unit = _canonical_methodology_unit(value=self.value, declared_unit=self.unit)
        object.__setattr__(self, "unit", canonical_unit)
        seed = {
            "methodology_question_record_id": question_id,
            "subject": _normalize_text(self.subject, casefold=True),
            "predicate": _normalize_text(self.predicate, casefold=True),
            "claim_class": _normalize_text(self.claim_class),
            "value": self.value,
            "period": self.period,
            "unit": canonical_unit,
            "claim_text": _normalize_text(self.claim_text),
            "accepted_source_locator_pairs": list(
                zip(
                    self.accepted_source_span_ids,
                    self.accepted_locator_sha256s,
                    strict=True,
                )
            ),
        }
        generated = str(
            uuid.uuid5(
                METHODOLOGY_FACTUAL_OUTPUT_NAMESPACE,
                canonical_json_bytes(seed).decode("utf-8"),
            )
        )
        if (
            self.factual_output_id is not None
            and _require_uuid(self.factual_output_id) != generated
        ):
            raise ValueError("factual_output_id does not match canonical output")
        object.__setattr__(self, "factual_output_id", generated)
        return self


def _duplicate_table_row_keys(
    factual_outputs: tuple[MethodologyFactualOutput, ...],
) -> tuple[str, ...]:
    keys: list[str] = []
    for output in factual_outputs:
        if not isinstance(output.value, TableRowValue):
            continue
        keys.append(
            hashlib.sha256(
                canonical_json_bytes(
                    {
                        "methodology_question_record_id": output.methodology_question_record_id,
                        "table_row": output.value,
                    }
                )
            ).hexdigest()
        )
    return tuple(keys)


class MethodologyProviderReceipt(_StrictModel):
    provider: Literal["anthropic"]
    backend: Literal["live", "hermetic"]
    configured_model: str
    observed_model: str
    prompt_id: Literal["METHODOLOGY_QUESTION_EXTRACTION_V1"]
    prompt_version: str
    prompt_content_sha256: str
    prompt_input_schema_sha256: str
    prompt_output_schema_sha256: str
    request_payload_sha256: str
    provider_request_id: str
    provider_response_sha256: str
    real_call: bool
    configured_max_tokens: int = Field(gt=0)
    configured_temperature: Literal["0"]
    configured_json_mode: Literal[True]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    stop_reason: str
    budget_policy_id: str
    budget_component: Literal["anthropic"]
    budget_reservation_id: str
    billable_payload_sha256: str
    billable_payload_utf8_bytes: int = Field(gt=0)
    price_catalog_version: str
    price_catalog_row_sha256: str
    input_usd_per_million_tokens: str
    output_usd_per_million_tokens: str
    reserved_calls: int = Field(ge=0)
    reserved_input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    reserved_embedding_tokens: Literal[0]
    reserved_usd: str
    budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"]
    receipt_sha256: str

    _sha_fields = field_validator(
        "prompt_content_sha256",
        "prompt_input_schema_sha256",
        "prompt_output_schema_sha256",
        "request_payload_sha256",
        "provider_response_sha256",
        "billable_payload_sha256",
        "price_catalog_row_sha256",
        "receipt_sha256",
    )(_require_sha256)
    _non_blank_fields = field_validator(
        "configured_model",
        "observed_model",
        "prompt_version",
        "provider_request_id",
        "stop_reason",
        "budget_policy_id",
        "price_catalog_version",
    )(_require_non_blank)
    _reservation_uuid = field_validator("budget_reservation_id")(_require_uuid)

    @field_validator(
        "input_usd_per_million_tokens", "output_usd_per_million_tokens", "reserved_usd"
    )
    @classmethod
    def decimal_8(cls, value: str) -> str:
        if not _DECIMAL_8_RE.fullmatch(value) or Decimal(value) < 0:
            raise ValueError("USD fields require non-negative fixed 8-decimal values")
        return value

    @model_validator(mode="after")
    def validate_provider_proof(self) -> MethodologyProviderReceipt:
        if self.configured_model != self.observed_model:
            raise ValueError("configured and observed model must match")
        if self.backend == "live":
            if not self.real_call:
                raise ValueError("live backend requires real_call=true")
            if self.reserved_calls != 1 or self.budget_terminal_state != "CONSUMED":
                raise ValueError("live calls require one consumed reservation")
            if self.billable_payload_utf8_bytes != self.reserved_input_tokens:
                raise ValueError("input reservation must cover complete billable payload")
            if self.input_tokens > self.reserved_input_tokens:
                raise ValueError("observed input tokens exceed reservation")
            if self.output_tokens > self.reserved_output_tokens:
                raise ValueError("observed output tokens exceed reservation")
        else:
            if self.real_call:
                raise ValueError("hermetic backend cannot claim a real call")
            if self.budget_terminal_state != "NOT_BILLABLE":
                raise ValueError("hermetic transport must be non-billable")
            if (
                any(
                    (
                        self.reserved_calls,
                        self.reserved_input_tokens,
                        self.reserved_output_tokens,
                    )
                )
                or Decimal(self.reserved_usd) != 0
            ):
                raise ValueError("hermetic transport cannot reserve provider budget")
        return self


METHODOLOGY_PROVIDER_RECEIPT_HASH_FIELDS: frozenset[str] = frozenset(
    MethodologyProviderReceipt.model_fields
) - {"receipt_sha256"}


def compute_methodology_provider_receipt_sha256(
    *, receipt_fields: Mapping[str, object], validated_result_sha256: str
) -> str:
    """Hash the exact final-receipt field set without a proof-hash dependency."""

    if frozenset(receipt_fields) != METHODOLOGY_PROVIDER_RECEIPT_HASH_FIELDS:
        raise ValueError("prospective receipt fields differ from the governed contract")
    validated_hash = _require_sha256(validated_result_sha256)
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "receipt_fields": dict(receipt_fields),
                "validated_result_sha256": validated_hash,
            }
        )
    ).hexdigest()


class MethodologyTaskExtractorResponse(_StrictModel):
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"]
    searched_document_ids: tuple[str, ...]
    searched_span_ids: tuple[str, ...]
    searched_scope_sha256: str
    provider_receipt: MethodologyProviderReceipt
    factual_outputs: tuple[MethodologyFactualOutput, ...] = ()
    gap_finding: MethodologyGapFinding | None = None

    _sha_field = field_validator("searched_scope_sha256")(_require_sha256)
    _document_ids = field_validator("searched_document_ids")(
        lambda values: tuple(sorted(_require_uuid(value) for value in values))
    )
    _span_ids = field_validator("searched_span_ids")(
        lambda values: tuple(sorted(_require_uuid(value) for value in values))
    )

    @model_validator(mode="after")
    def validate_terminal_semantics(self) -> MethodologyTaskExtractorResponse:
        if not self.searched_document_ids or not self.searched_span_ids:
            raise ValueError("terminal methodology result requires positive searched scope")
        if self.outcome == "EVIDENCE_FOUND":
            if not self.factual_outputs or self.gap_finding is not None:
                raise ValueError("EVIDENCE_FOUND requires facts and no gap finding")
            output_ids = [output.factual_output_id for output in self.factual_outputs]
            if len(output_ids) != len(set(output_ids)):
                raise ValueError("duplicate factual output")
            table_row_keys = _duplicate_table_row_keys(self.factual_outputs)
            if len(table_row_keys) != len(set(table_row_keys)):
                raise ValueError("duplicate methodology table row")
        elif self.factual_outputs or self.gap_finding is None:
            raise ValueError("EVIDENCE_NOT_FOUND requires a governed gap and zero facts")
        return self


class MethodologyPromptArtifact(_StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    prompt_id: Literal["METHODOLOGY_QUESTION_EXTRACTION_V1"]
    version: str
    status: Literal["DRAFT", "STAGING", "PROD"]
    content: str
    content_sha256: str
    input_schema: dict[str, Any]
    input_schema_sha256: str
    output_schema: dict[str, Any]
    output_schema_sha256: str

    _version = field_validator("version")(_require_non_blank)
    _sha_fields = field_validator("content_sha256", "input_schema_sha256", "output_schema_sha256")(
        _require_sha256
    )

    @field_validator("content")
    @classmethod
    def preserve_non_blank_content(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("prompt content must not be blank")
        return value

    @model_validator(mode="after")
    def hashes_bind_exact_artifact(self) -> MethodologyPromptArtifact:
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("prompt content hash does not bind the exact UTF-8 bytes")
        if (
            hashlib.sha256(exact_canonical_json_bytes(self.input_schema)).hexdigest()
            != self.input_schema_sha256
            or hashlib.sha256(exact_canonical_json_bytes(self.output_schema)).hexdigest()
            != self.output_schema_sha256
        ):
            raise ValueError("prompt schema hash does not bind the canonical schema")
        return self


class AnthropicMethodologyTool(_StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    name: Literal["methodology_result"]
    description: str
    input_schema: dict[str, Any]
    strict: Literal[True]

    @field_validator("description")
    @classmethod
    def non_blank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool description must not be blank")
        return value


class AnthropicMethodologyToolChoice(_StrictModel):
    type: Literal["tool"]
    name: Literal["methodology_result"]
    disable_parallel_tool_use: Literal[True]


class AnthropicMethodologyWireRequest(_StrictModel):
    """The sole request object used for reservation hashing and SDK dispatch."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    model: str
    max_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=0.0)
    system: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[AnthropicMethodologyTool, ...]
    tool_choice: AnthropicMethodologyToolChoice

    @model_validator(mode="after")
    def strict_single_tool_request(self) -> AnthropicMethodologyWireRequest:
        if not self.model.strip() or not self.system.strip() or not self.messages:
            raise ValueError("Anthropic wire request fields must not be blank")
        if len(self.tools) != 1 or self.tools[0].name != self.tool_choice.name:
            raise ValueError("Anthropic wire request requires exactly one forced strict tool")
        return self

    def exact_payload(self) -> bytes:
        return exact_canonical_json_bytes(self)

    def as_sdk_kwargs(self) -> dict[str, Any]:
        decoded: object = json.loads(self.exact_payload().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Anthropic wire request must serialize as an object")
        return {str(key): value for key, value in decoded.items()}


class MethodologyBudgetReservation(_StrictModel):
    budget_policy_id: str
    model_id: str
    billable_payload_sha256: str
    billable_payload_utf8_bytes: int = Field(gt=0)
    reserved_input_tokens: int = Field(gt=0)
    reserved_output_tokens: int = Field(gt=0)
    price_catalog_version: str
    price_catalog_mode: str
    price_catalog_row_sha256: str
    input_usd_per_million_tokens: str
    output_usd_per_million_tokens: str
    worst_case_usd: str

    @classmethod
    def from_anthropic_billable_payload(
        cls,
        *,
        payload: bytes,
        budget_policy_id: str,
        model_id: str,
        output_token_ceiling: int,
        context_window_limit: int,
        price_catalog_version: str,
        price_catalog_mode: str,
        price_catalog_row_sha256: str,
        input_usd_per_million_tokens: str,
        output_usd_per_million_tokens: str,
    ) -> MethodologyBudgetReservation:
        if not payload:
            raise ValueError("billable payload must not be empty")
        if output_token_ceiling <= 0:
            raise ValueError("output token ceiling must be positive")
        input_ceiling = len(payload)
        if input_ceiling + output_token_ceiling > context_window_limit:
            raise ValueError("conservative request token ceiling exceeds model context")
        input_price = Decimal(input_usd_per_million_tokens)
        output_price = Decimal(output_usd_per_million_tokens)
        return cls(
            budget_policy_id=budget_policy_id,
            model_id=model_id,
            billable_payload_sha256=hashlib.sha256(payload).hexdigest(),
            billable_payload_utf8_bytes=input_ceiling,
            reserved_input_tokens=input_ceiling,
            reserved_output_tokens=output_token_ceiling,
            price_catalog_version=price_catalog_version,
            price_catalog_mode=price_catalog_mode,
            price_catalog_row_sha256=price_catalog_row_sha256,
            input_usd_per_million_tokens=f"{input_price:.8f}",
            output_usd_per_million_tokens=f"{output_price:.8f}",
            worst_case_usd=compute_anthropic_usd(
                input_tokens=input_ceiling,
                output_tokens=output_token_ceiling,
                input_usd_per_million_tokens=f"{input_price:.8f}",
                output_usd_per_million_tokens=f"{output_price:.8f}",
            ),
        )

    _text_fields = field_validator(
        "budget_policy_id", "model_id", "price_catalog_version", "price_catalog_mode"
    )(_require_non_blank)
    _sha_fields = field_validator("billable_payload_sha256", "price_catalog_row_sha256")(
        _require_sha256
    )


class PreparedMethodologyCall(_StrictModel):
    """Complete zero-I/O call preparation persisted before provider dispatch."""

    request: MethodologyTaskExtractorRequest
    prompt_artifact: MethodologyPromptArtifact
    wire_request: AnthropicMethodologyWireRequest
    budget_policy_id: str
    billable_payload: bytes = Field(min_length=1)
    reservation: MethodologyBudgetReservation
    budget_reservation_id: str
    prepared_call_sha256: str

    _policy = field_validator("budget_policy_id")(_require_non_blank)
    _budget_uuid = field_validator("budget_reservation_id")(_require_uuid)
    _prepared_hash = field_validator("prepared_call_sha256")(_require_sha256)

    @property
    def model(self) -> str:
        return self.wire_request.model

    @property
    def max_tokens(self) -> int:
        return self.wire_request.max_tokens

    @property
    def temperature(self) -> Literal["0"]:
        return "0"

    @classmethod
    def from_components(
        cls,
        *,
        request: MethodologyTaskExtractorRequest,
        prompt_artifact: MethodologyPromptArtifact,
        wire_request: AnthropicMethodologyWireRequest,
        budget_policy_id: str,
        billable_payload: bytes,
        reservation: MethodologyBudgetReservation,
        budget_reservation_id: str,
    ) -> PreparedMethodologyCall:
        draft = cls.model_construct(
            request=request,
            prompt_artifact=prompt_artifact,
            wire_request=wire_request,
            budget_policy_id=budget_policy_id,
            billable_payload=billable_payload,
            reservation=reservation,
            budget_reservation_id=budget_reservation_id,
            prepared_call_sha256="0" * 64,
        )
        return cls(
            request=request,
            prompt_artifact=prompt_artifact,
            wire_request=wire_request,
            budget_policy_id=budget_policy_id,
            billable_payload=billable_payload,
            reservation=reservation,
            budget_reservation_id=budget_reservation_id,
            prepared_call_sha256=draft.expected_prepared_call_sha256(),
        )

    def expected_prepared_call_sha256(self) -> str:
        seed = {
            "request": self.request,
            "prompt_id": self.prompt_artifact.prompt_id,
            "prompt_version": self.prompt_artifact.version,
            "prompt_content_sha256": self.prompt_artifact.content_sha256,
            "prompt_input_schema_sha256": self.prompt_artifact.input_schema_sha256,
            "prompt_output_schema_sha256": self.prompt_artifact.output_schema_sha256,
            "wire_request": self.wire_request,
            "wire_request_sha256": hashlib.sha256(self.wire_request.exact_payload()).hexdigest(),
            "budget_policy_id": self.budget_policy_id,
            "billable_payload_sha256": hashlib.sha256(self.billable_payload).hexdigest(),
            "reservation": self.reservation,
            "budget_reservation_id": self.budget_reservation_id,
        }
        return hashlib.sha256(exact_canonical_json_bytes(seed)).hexdigest()

    @model_validator(mode="after")
    def validate_prepared_identity(self) -> PreparedMethodologyCall:
        if self.billable_payload != self.wire_request.exact_payload():
            raise ValueError("billable payload is not the exact Anthropic wire request")
        if (
            hashlib.sha256(self.billable_payload).hexdigest()
            != self.reservation.billable_payload_sha256
            or self.budget_policy_id != self.reservation.budget_policy_id
            or self.model != self.reservation.model_id
        ):
            raise ValueError("billable payload and reservation hashes differ")
        if self.expected_prepared_call_sha256() != self.prepared_call_sha256:
            raise ValueError("prepared_call_sha256 does not match prepared call")
        return self

    def transport_kwargs(self) -> dict[str, Any]:
        return {**self.wire_request.as_sdk_kwargs(), "retries": 0, "batch_mode": False}


class MethodologyCallAdmissionProof(_StrictModel):
    """Repository-issued proof that STARTED and budget reserve committed."""

    backend: Literal["postgresql", "hermetic"]
    committed: Literal[True]
    attempt_id: str
    budget_reservation_id: str
    prepared_call_sha256: str
    task_input_sha256: str
    price_catalog_row_sha256: str
    worker_fencing_token: int = Field(gt=0)
    budget_policy_id: str = "hermetic"

    _uuid_fields = field_validator("attempt_id", "budget_reservation_id")(_require_uuid)
    _sha_fields = field_validator(
        "prepared_call_sha256", "task_input_sha256", "price_catalog_row_sha256"
    )(_require_sha256)
    _policy = field_validator("budget_policy_id")(_require_non_blank)


class DispatchedMethodologyCall(_StrictModel):
    """One provider response before any terminal budget/result claim exists."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    prepared_call: PreparedMethodologyCall
    admission: MethodologyCallAdmissionProof
    backend: Literal["live", "hermetic"]
    provider_request_id: Any
    observed_model: Any
    content_blocks: tuple[Any, ...]
    input_tokens: Any
    output_tokens: Any
    latency_ms: int = Field(ge=0)
    stop_reason: Any
    provider_response_sha256: str
    budget_state: Literal["RESERVED"] = "RESERVED"
    dispatched_call_sha256: str

    _sha_fields = field_validator("provider_response_sha256", "dispatched_call_sha256")(
        _require_sha256
    )

    @classmethod
    def from_provider_response(
        cls,
        *,
        prepared_call: PreparedMethodologyCall,
        admission: MethodologyCallAdmissionProof,
        backend: Literal["live", "hermetic"],
        provider_request_id: Any,
        observed_model: Any,
        content_blocks: Sequence[Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        input_tokens: Any,
        output_tokens: Any,
        latency_ms: int,
        stop_reason: Any,
        provider_response_sha256: str,
    ) -> DispatchedMethodologyCall:
        if content_blocks is None:
            if output_payload is None:
                raise ValueError("provider response content blocks are required")
            content_blocks = (
                {
                    "type": "tool_use",
                    "name": "methodology_result",
                    "input": output_payload,
                },
            )
        elif output_payload is not None:
            raise ValueError("provider response cannot supply two content representations")
        captured_blocks = tuple(content_blocks)
        draft = cls.model_construct(
            prepared_call=prepared_call,
            admission=admission,
            backend=backend,
            provider_request_id=provider_request_id,
            observed_model=observed_model,
            content_blocks=captured_blocks,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            provider_response_sha256=provider_response_sha256,
            budget_state="RESERVED",
            dispatched_call_sha256="0" * 64,
        )
        return cls(
            prepared_call=prepared_call,
            admission=admission,
            backend=backend,
            provider_request_id=provider_request_id,
            observed_model=observed_model,
            content_blocks=captured_blocks,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            provider_response_sha256=provider_response_sha256,
            budget_state="RESERVED",
            dispatched_call_sha256=draft.expected_dispatched_call_sha256(),
        )

    def expected_dispatched_call_sha256(self) -> str:
        return hashlib.sha256(
            exact_canonical_json_bytes(
                {
                    "prepared_call_sha256": self.prepared_call.prepared_call_sha256,
                    "attempt_id": self.admission.attempt_id,
                    "budget_reservation_id": self.admission.budget_reservation_id,
                    "backend": self.backend,
                    "provider_request_id": self.provider_request_id,
                    "observed_model": self.observed_model,
                    "content_blocks": self.content_blocks,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "latency_ms": self.latency_ms,
                    "stop_reason": self.stop_reason,
                    "provider_response_sha256": self.provider_response_sha256,
                    "budget_state": self.budget_state,
                }
            )
        ).hexdigest()

    @model_validator(mode="after")
    def content_bound_dispatch(self) -> DispatchedMethodologyCall:
        if self.expected_dispatched_call_sha256() != self.dispatched_call_sha256:
            raise ValueError("dispatched_call_sha256 does not match provider response")
        return self


class ValidatedDispatchedMethodologyResult(_StrictModel):
    """Typed terminal payload validated before the fenced terminal transaction."""

    dispatched_call_sha256: str
    task_input_sha256: str
    outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"]
    searched_document_ids: tuple[str, ...]
    searched_span_ids: tuple[str, ...]
    searched_scope_sha256: str
    factual_outputs: tuple[MethodologyFactualOutput, ...] = ()
    gap_finding: MethodologyGapFinding | None = None
    budget_state: Literal["RESERVED"] = "RESERVED"
    validated_result_sha256: str

    _sha_fields = field_validator(
        "dispatched_call_sha256",
        "task_input_sha256",
        "searched_scope_sha256",
        "validated_result_sha256",
    )(_require_sha256)
    _document_ids = field_validator("searched_document_ids")(
        lambda values: tuple(sorted(_require_uuid(value) for value in values))
    )
    _span_ids = field_validator("searched_span_ids")(
        lambda values: tuple(sorted(_require_uuid(value) for value in values))
    )

    @classmethod
    def from_terminal_payload(
        cls,
        *,
        dispatched_call_sha256: str,
        task_input_sha256: str,
        outcome: Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"],
        searched_document_ids: tuple[str, ...],
        searched_span_ids: tuple[str, ...],
        searched_scope_sha256: str,
        factual_outputs: tuple[MethodologyFactualOutput, ...],
        gap_finding: MethodologyGapFinding | None,
    ) -> ValidatedDispatchedMethodologyResult:
        draft = cls.model_construct(
            dispatched_call_sha256=dispatched_call_sha256,
            task_input_sha256=task_input_sha256,
            outcome=outcome,
            searched_document_ids=searched_document_ids,
            searched_span_ids=searched_span_ids,
            searched_scope_sha256=searched_scope_sha256,
            factual_outputs=factual_outputs,
            gap_finding=gap_finding,
            budget_state="RESERVED",
            validated_result_sha256="0" * 64,
        )
        return cls(
            dispatched_call_sha256=dispatched_call_sha256,
            task_input_sha256=task_input_sha256,
            outcome=outcome,
            searched_document_ids=searched_document_ids,
            searched_span_ids=searched_span_ids,
            searched_scope_sha256=searched_scope_sha256,
            factual_outputs=factual_outputs,
            gap_finding=gap_finding,
            budget_state="RESERVED",
            validated_result_sha256=draft.expected_validated_result_sha256(),
        )

    def expected_validated_result_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "dispatched_call_sha256": self.dispatched_call_sha256,
                    "task_input_sha256": self.task_input_sha256,
                    "outcome": self.outcome,
                    "searched_document_ids": self.searched_document_ids,
                    "searched_span_ids": self.searched_span_ids,
                    "searched_scope_sha256": self.searched_scope_sha256,
                    "factual_outputs": self.factual_outputs,
                    "gap_finding": self.gap_finding,
                    "budget_state": self.budget_state,
                }
            )
        ).hexdigest()

    @model_validator(mode="after")
    def typed_preterminal_semantics(self) -> ValidatedDispatchedMethodologyResult:
        if not self.searched_document_ids or not self.searched_span_ids:
            raise ValueError("validated result requires positive searched scope")
        if self.outcome == "EVIDENCE_FOUND":
            if not self.factual_outputs or self.gap_finding is not None:
                raise ValueError("EVIDENCE_FOUND requires facts and no gap finding")
            output_ids = [output.factual_output_id for output in self.factual_outputs]
            if len(output_ids) != len(set(output_ids)):
                raise ValueError("duplicate factual output")
            table_row_keys = _duplicate_table_row_keys(self.factual_outputs)
            if len(table_row_keys) != len(set(table_row_keys)):
                raise ValueError("duplicate methodology table row")
        elif self.factual_outputs or self.gap_finding is None:
            raise ValueError("EVIDENCE_NOT_FOUND requires a governed gap and zero facts")
        if self.expected_validated_result_sha256() != self.validated_result_sha256:
            raise ValueError("validated_result_sha256 does not match terminal payload")
        return self


class MethodologyCallTerminalizationProof(_StrictModel):
    """Content-bound proof emitted only after the fenced terminal transaction commits."""

    backend: Literal["postgresql", "hermetic"]
    committed: Literal[True]
    attempt_id: str
    budget_reservation_id: str
    prepared_call_sha256: str
    dispatched_call_sha256: str
    validated_result_sha256: str
    prospective_receipt_sha256: str
    task_input_sha256: str
    price_catalog_row_sha256: str
    worker_fencing_token: int = Field(gt=0)
    budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"]
    budget_policy_id: str
    consumed_calls: int = Field(ge=0)
    consumed_input_tokens: int = Field(ge=0)
    consumed_output_tokens: int = Field(ge=0)
    consumed_usd: str
    terminalization_proof_sha256: str

    _uuid_fields = field_validator("attempt_id", "budget_reservation_id")(_require_uuid)
    _sha_fields = field_validator(
        "prepared_call_sha256",
        "dispatched_call_sha256",
        "validated_result_sha256",
        "prospective_receipt_sha256",
        "task_input_sha256",
        "price_catalog_row_sha256",
        "terminalization_proof_sha256",
    )(_require_sha256)
    _policy = field_validator("budget_policy_id")(_require_non_blank)

    @field_validator("consumed_usd")
    @classmethod
    def consumed_usd_format(cls, value: str) -> str:
        if not _DECIMAL_8_RE.fullmatch(value) or Decimal(value) < 0:
            raise ValueError("consumed_usd requires a non-negative fixed 8-decimal value")
        return value

    @classmethod
    def from_validated_result(
        cls,
        *,
        dispatched_call: DispatchedMethodologyCall,
        validated_result: ValidatedDispatchedMethodologyResult,
        admission: MethodologyCallAdmissionProof,
        backend: Literal["postgresql", "hermetic"],
        budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"],
        budget_policy_id: str,
        prospective_receipt_sha256: str,
    ) -> MethodologyCallTerminalizationProof:
        if (
            validated_result.dispatched_call_sha256 != dispatched_call.dispatched_call_sha256
            or admission != dispatched_call.admission
            or admission.budget_policy_id != dispatched_call.prepared_call.budget_policy_id
            or budget_policy_id != admission.budget_policy_id
            or admission.price_catalog_row_sha256
            != dispatched_call.prepared_call.reservation.price_catalog_row_sha256
        ):
            raise ValueError("validated result, policy, and admission must bind the exact dispatch")
        if backend == "hermetic":
            consumed_calls = consumed_input_tokens = consumed_output_tokens = 0
            consumed_usd = "0.00000000"
        else:
            consumed_calls = 1
            consumed_input_tokens = dispatched_call.input_tokens
            consumed_output_tokens = dispatched_call.output_tokens
            reservation = dispatched_call.prepared_call.reservation
            consumed_usd = compute_anthropic_usd(
                input_tokens=consumed_input_tokens,
                output_tokens=consumed_output_tokens,
                input_usd_per_million_tokens=reservation.input_usd_per_million_tokens,
                output_usd_per_million_tokens=reservation.output_usd_per_million_tokens,
            )
        draft = cls.model_construct(
            backend=backend,
            committed=True,
            attempt_id=admission.attempt_id,
            budget_reservation_id=admission.budget_reservation_id,
            prepared_call_sha256=dispatched_call.prepared_call.prepared_call_sha256,
            dispatched_call_sha256=dispatched_call.dispatched_call_sha256,
            validated_result_sha256=validated_result.validated_result_sha256,
            prospective_receipt_sha256=prospective_receipt_sha256,
            task_input_sha256=dispatched_call.prepared_call.request.task_input_sha256,
            price_catalog_row_sha256=(
                dispatched_call.prepared_call.reservation.price_catalog_row_sha256
            ),
            worker_fencing_token=admission.worker_fencing_token,
            budget_terminal_state=budget_terminal_state,
            budget_policy_id=budget_policy_id,
            consumed_calls=consumed_calls,
            consumed_input_tokens=consumed_input_tokens,
            consumed_output_tokens=consumed_output_tokens,
            consumed_usd=consumed_usd,
            terminalization_proof_sha256="0" * 64,
        )
        return cls(
            backend=backend,
            committed=True,
            attempt_id=admission.attempt_id,
            budget_reservation_id=admission.budget_reservation_id,
            prepared_call_sha256=dispatched_call.prepared_call.prepared_call_sha256,
            dispatched_call_sha256=dispatched_call.dispatched_call_sha256,
            validated_result_sha256=validated_result.validated_result_sha256,
            prospective_receipt_sha256=prospective_receipt_sha256,
            task_input_sha256=dispatched_call.prepared_call.request.task_input_sha256,
            price_catalog_row_sha256=(
                dispatched_call.prepared_call.reservation.price_catalog_row_sha256
            ),
            worker_fencing_token=admission.worker_fencing_token,
            budget_terminal_state=budget_terminal_state,
            budget_policy_id=budget_policy_id,
            consumed_calls=consumed_calls,
            consumed_input_tokens=consumed_input_tokens,
            consumed_output_tokens=consumed_output_tokens,
            consumed_usd=consumed_usd,
            terminalization_proof_sha256=draft.expected_terminalization_proof_sha256(),
        )

    def expected_terminalization_proof_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "backend": self.backend,
                    "committed": self.committed,
                    "attempt_id": self.attempt_id,
                    "budget_reservation_id": self.budget_reservation_id,
                    "prepared_call_sha256": self.prepared_call_sha256,
                    "dispatched_call_sha256": self.dispatched_call_sha256,
                    "validated_result_sha256": self.validated_result_sha256,
                    "prospective_receipt_sha256": self.prospective_receipt_sha256,
                    "task_input_sha256": self.task_input_sha256,
                    "price_catalog_row_sha256": self.price_catalog_row_sha256,
                    "worker_fencing_token": self.worker_fencing_token,
                    "budget_terminal_state": self.budget_terminal_state,
                    "budget_policy_id": self.budget_policy_id,
                    "consumed_calls": self.consumed_calls,
                    "consumed_input_tokens": self.consumed_input_tokens,
                    "consumed_output_tokens": self.consumed_output_tokens,
                    "consumed_usd": self.consumed_usd,
                }
            )
        ).hexdigest()

    @model_validator(mode="after")
    def honest_terminalization(self) -> MethodologyCallTerminalizationProof:
        if self.expected_terminalization_proof_sha256() != self.terminalization_proof_sha256:
            raise ValueError("terminalization proof hash mismatch")
        if self.backend == "hermetic":
            if self.budget_terminal_state != "NOT_BILLABLE" or any(
                (
                    self.consumed_calls,
                    self.consumed_input_tokens,
                    self.consumed_output_tokens,
                    Decimal(self.consumed_usd),
                )
            ):
                raise ValueError("hermetic terminalization must be honestly non-billable")
        elif self.budget_terminal_state != "CONSUMED" or self.consumed_calls != 1:
            raise ValueError("PostgreSQL live terminalization must prove one consumed call")
        return self


class DurableMethodologyExtractionExecutionResult(_StrictModel):
    """Restart-stable Step-7 value loaded from durable terminal result rows."""

    tenant_id: str
    deal_id: str
    run_id: str
    selected_question_ids: tuple[str, ...]
    planned_task_question_ids: dict[str, str]
    attempt_task_ids: tuple[str, ...]
    result_task_ids: tuple[str, ...]
    terminal_results_by_task_id: dict[str, MethodologyTaskExtractorResponse]

    _scope_uuids = field_validator("tenant_id", "deal_id", "run_id")(_require_uuid)

    @model_validator(mode="after")
    def exact_terminal_equations(self) -> DurableMethodologyExtractionExecutionResult:
        selected = tuple(sorted(set(self.selected_question_ids)))
        planned_tasks = tuple(sorted(self.planned_task_question_ids))
        planned_questions = tuple(sorted(set(self.planned_task_question_ids.values())))
        attempts = tuple(sorted(set(self.attempt_task_ids)))
        result_ids = tuple(sorted(set(self.result_task_ids)))
        terminal_ids = tuple(sorted(self.terminal_results_by_task_id))
        if (
            len(selected) != len(self.selected_question_ids)
            or selected != planned_questions
            or len(attempts) != len(self.attempt_task_ids)
            or len(result_ids) != len(self.result_task_ids)
            or planned_tasks != attempts
            or planned_tasks != result_ids
            or planned_tasks != terminal_ids
        ):
            raise ValueError("Step-7 durable terminal task equations differ")
        object.__setattr__(self, "selected_question_ids", selected)
        object.__setattr__(self, "attempt_task_ids", attempts)
        object.__setattr__(self, "result_task_ids", result_ids)
        return self

    @property
    def planned_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.planned_task_question_ids))

    def to_run_step_summary(self) -> dict[str, Any]:
        outcomes = Counter(
            response.outcome for response in self.terminal_results_by_task_id.values()
        )
        return {
            "status": "COMPLETED",
            "selected_question_ids": list(self.selected_question_ids),
            "task_ids": list(self.planned_task_ids),
            "task_count": len(self.planned_task_ids),
            "outcomes": dict(sorted(outcomes.items())),
        }


class CanonicalMethodologyClaimIdentity(_StrictModel):
    claim_id: str
    normalization_rule_version: Literal["claim-normalization-v1"]
    normalized_predicate: str
    normalized_subject: str
    normalized_period: str
    normalized_unit: str
    value_sha256: str
    canonical_key_sha256: str

    @classmethod
    def from_factual_output(
        cls,
        *,
        tenant_id: str,
        deal_id: str,
        factual_output: MethodologyFactualOutput,
    ) -> CanonicalMethodologyClaimIdentity:
        identity, _canonical_value = cls.recompute(
            tenant_id=tenant_id,
            deal_id=deal_id,
            normalization_rule_version=CLAIM_NORMALIZATION_RULE_VERSION,
            normalized_predicate=factual_output.predicate,
            normalized_subject=factual_output.subject,
            normalized_period=factual_output.period.normalized(),
            normalized_unit=factual_output.unit,
            value=factual_output.value,
        )
        return identity

    @classmethod
    def recompute(
        cls,
        *,
        tenant_id: str,
        deal_id: str,
        normalization_rule_version: str,
        normalized_predicate: str,
        normalized_subject: str,
        normalized_period: str,
        normalized_unit: str,
        value: object,
    ) -> tuple[CanonicalMethodologyClaimIdentity, ValueStruct]:
        """Recompute the governed typed-value hash, canonical key, and UUIDv5."""
        if normalization_rule_version != CLAIM_NORMALIZATION_RULE_VERSION:
            raise ValueError("unsupported claim normalization rule version")
        if isinstance(value, Mapping):
            value_payload = dict(value)
        elif hasattr(value, "model_dump"):
            value_payload = value.model_dump(mode="json")
        else:
            raise ValueError("canonical methodology claim value must be typed")
        canonical_value = parse_value_struct(value_payload)
        tenant = _require_uuid(tenant_id)
        deal = _require_uuid(deal_id)
        predicate = _normalize_text(normalized_predicate, casefold=True)
        subject = _normalize_text(normalized_subject, casefold=True)
        period = _normalize_text(normalized_period)
        unit = _normalize_text(normalized_unit)
        value_sha256 = hashlib.sha256(canonical_json_bytes(canonical_value)).hexdigest()
        key = {
            "tenant_id": tenant,
            "deal_id": deal,
            "normalization_rule_version": CLAIM_NORMALIZATION_RULE_VERSION,
            "normalized_predicate": predicate,
            "normalized_subject": subject,
            "normalized_period": period,
            "normalized_unit": unit,
            "value_sha256": value_sha256,
        }
        key_bytes = canonical_json_bytes(key)
        return (
            cls(
                claim_id=str(uuid.uuid5(METHODOLOGY_CLAIM_NAMESPACE, key_bytes.decode("utf-8"))),
                normalization_rule_version=CLAIM_NORMALIZATION_RULE_VERSION,
                normalized_predicate=predicate,
                normalized_subject=subject,
                normalized_period=period,
                normalized_unit=unit,
                value_sha256=value_sha256,
                canonical_key_sha256=hashlib.sha256(key_bytes).hexdigest(),
            ),
            canonical_value,
        )

    _uuid_field = field_validator("claim_id")(_require_uuid)
    _sha_fields = field_validator("value_sha256", "canonical_key_sha256")(_require_sha256)


class MethodologyResultFactualOutputRecord(_StrictModel):
    factual_output_record_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    result_id: str
    factual_output_id: str
    factual_output: MethodologyFactualOutput
    factual_output_sha256: str

    @classmethod
    def from_factual_output(
        cls,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        result_id: str,
        factual_output: MethodologyFactualOutput,
    ) -> MethodologyResultFactualOutputRecord:
        output_id = _require_uuid(str(factual_output.factual_output_id))
        identity = "|".join(
            (
                _require_uuid(tenant_id),
                _require_uuid(deal_id),
                _require_uuid(run_id),
                _require_uuid(result_id),
                output_id,
            )
        )
        return cls(
            factual_output_record_id=str(uuid.uuid5(METHODOLOGY_RECORD_NAMESPACE, identity)),
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            result_id=result_id,
            factual_output_id=output_id,
            factual_output=factual_output,
            factual_output_sha256=hashlib.sha256(canonical_json_bytes(factual_output)).hexdigest(),
        )

    _uuid_fields = field_validator(
        "factual_output_record_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_id",
    )(_require_uuid)
    _sha_field = field_validator("factual_output_sha256")(_require_sha256)


class _MethodologyResultLink(_StrictModel):
    tenant_id: str
    deal_id: str
    run_id: str
    result_id: str
    factual_output_record_id: str
    claim_link_id: str
    factual_output_id: str

    _scope_uuids = field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "claim_link_id",
        "factual_output_id",
    )(_require_uuid)


class MethodologyResultClaimLink(_StrictModel):
    claim_link_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    result_id: str
    factual_output_record_id: str
    coverage_record_id: str
    factual_output_id: str
    claim_id: str
    canonical_key_sha256: str
    reused_existing_claim: bool

    _uuid_fields = field_validator(
        "claim_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "factual_output_id",
        "claim_id",
    )(_require_uuid)
    _coverage_record_id = field_validator("coverage_record_id")(_require_coverage_record_id)
    _sha_field = field_validator("canonical_key_sha256")(_require_sha256)


class MethodologyResultEvidenceLink(_MethodologyResultLink):
    evidence_link_id: str
    evidence_id: str
    document_id: str
    source_span_id: str

    _record_uuids = field_validator(
        "evidence_link_id", "evidence_id", "document_id", "source_span_id"
    )(_require_uuid)


class MethodologyResultSanadLink(_MethodologyResultLink):
    sanad_link_id: str
    sanad_id: str
    primary_evidence_id: str
    grade: str
    defect_ids: tuple[str, ...]

    _record_uuids = field_validator("sanad_link_id", "sanad_id", "primary_evidence_id")(
        _require_uuid
    )
    _grade = field_validator("grade")(_require_non_blank)
    _defect_uuids = field_validator("defect_ids")(
        lambda values: tuple(sorted(_require_uuid(value) for value in values))
    )


class MethodologyResultDefectLink(_StrictModel):
    defect_link_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    result_id: str
    factual_output_record_id: str
    sanad_link_id: str
    defect_id: str

    _record_uuids = field_validator(
        "defect_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "sanad_link_id",
        "defect_id",
    )(_require_uuid)


MethodologyResultLinkTable = Literal[
    "methodology_result_claim_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
]

_METHODOLOGY_RESULT_LINK_IDENTITY_CONTRACTS: dict[
    MethodologyResultLinkTable,
    tuple[frozenset[str], frozenset[str], frozenset[str]],
] = {
    "methodology_result_claim_links": (
        frozenset(MethodologyResultClaimLink.model_fields) - {"claim_link_id"},
        frozenset(
            {
                "selection_id",
                "methodology_version_record_id",
                "methodology_question_record_id",
            }
        ),
        frozenset({"factual_output_id"}),
    ),
    "methodology_result_evidence_links": (
        frozenset(MethodologyResultEvidenceLink.model_fields) - {"evidence_link_id"},
        frozenset({"locator_sha256"}),
        frozenset({"factual_output_id"}),
    ),
    "methodology_result_sanad_links": (
        frozenset(MethodologyResultSanadLink.model_fields) - {"sanad_link_id"},
        frozenset(),
        frozenset({"factual_output_id", "grade", "defect_ids"}),
    ),
    "methodology_result_defect_links": (
        frozenset(MethodologyResultDefectLink.model_fields) - {"defect_link_id"},
        frozenset(),
        frozenset(),
    ),
}


def generate_methodology_result_link_id(
    *,
    table: MethodologyResultLinkTable,
    logical_record: Mapping[str, Any],
    derived_fields: Mapping[str, Any] | None = None,
) -> str:
    """Generate the existing durable UUIDv5 identity from one exact logical link."""

    try:
        logical_fields, required_derived_fields, non_durable_fields = (
            _METHODOLOGY_RESULT_LINK_IDENTITY_CONTRACTS[table]
        )
    except KeyError as exc:
        raise ValueError("unsupported methodology result link table") from exc
    logical_payload = dict(logical_record)
    derived_payload = dict(derived_fields or {})
    if set(logical_payload) != logical_fields:
        raise ValueError("methodology result link logical fields do not match the contract")
    if set(derived_payload) != required_derived_fields:
        raise ValueError("methodology result link derived fields do not match the contract")
    identity_payload = {
        key: value for key, value in logical_payload.items() if key not in non_durable_fields
    }
    identity_payload.update(derived_payload)
    digest = hashlib.sha256(exact_canonical_json_bytes(identity_payload)).hexdigest()
    return str(uuid.uuid5(METHODOLOGY_RESULT_LINK_NAMESPACE, f"{table}:{digest}"))
