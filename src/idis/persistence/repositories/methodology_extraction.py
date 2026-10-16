"""Durable, fenced persistence for Task-6 methodology extraction.

The repository never owns or commits a transaction.  Callers construct it
inside a short transaction, so lease validation, budget/audit writes performed
by sibling stores, and each method below commit or roll back together.  It has
no process-local fallback and stores no source text, prompt text, credentials,
or raw provider responses.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Never

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from idis.models.extraction_task import (
    ExtractionTaskStatus,
    PlannedExtractionTask,
    SourceSpanReference,
    decode_extraction_task,
)
from idis.models.methodology_extraction import (
    CanonicalMethodologyClaimIdentity,
    MethodologyCandidateSpan,
    MethodologyProviderReceipt,
    MethodologyResultClaimLink,
    MethodologyResultDefectLink,
    MethodologyResultEvidenceLink,
    MethodologyResultSanadLink,
    ValidatedDispatchedMethodologyResult,
    compute_methodology_provider_receipt_sha256,
    compute_methodology_searched_scope_sha256,
    generate_methodology_result_link_id,
    validate_methodology_source_text_sha256,
)
from idis.models.methodology_pricing import (
    MethodologyPriceCatalogError,
    compute_anthropic_usd,
    resolve_anthropic_standard_price,
)
from idis.models.run_step import STEP_ORDER, StepName
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories._task7_native_roots import (
    validate_durable_run_output_object_ids,
    validate_run_output_object_ids,
)
from idis.persistence.repositories.provider_budget_policies import (
    VerifiedActiveProviderBudgetPolicy,
)
from idis.services.extraction.methodology_coverage_transitions import (
    CoverageProjection,
    MethodologyCoverageProjectionError,
    project_step7_coverage,
    project_step10_coverage,
)

WORKER_LEASE_FENCED = "WORKER_LEASE_FENCED"
METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN = "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
METHODOLOGY_BUDGET_LINEAGE_REJECTED = "METHODOLOGY_BUDGET_LINEAGE_REJECTED"
METHODOLOGY_PROVIDER_PROVENANCE_INVALID = "METHODOLOGY_PROVIDER_PROVENANCE_INVALID"
METHODOLOGY_PROVIDER_USAGE_INVALID = "METHODOLOGY_PROVIDER_USAGE_INVALID"
METHODOLOGY_SEARCH_SCOPE_MISMATCH = "METHODOLOGY_SEARCH_SCOPE_MISMATCH"
METHODOLOGY_SOURCE_LOCATOR_REJECTED = "METHODOLOGY_SOURCE_LOCATOR_REJECTED"
METHODOLOGY_TASK_COVERAGE_INCOMPLETE = "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"
METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED = "METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED"

METHODOLOGY_TASK_RECORD_NAMESPACE = uuid.UUID("0f7459a6-0f5e-55a8-b543-c1b47896e25e")
METHODOLOGY_ATTEMPT_NAMESPACE = uuid.UUID("61839ac4-6de1-54db-aa64-0f3f9cb042be")
METHODOLOGY_RESULT_NAMESPACE = uuid.UUID("671535ce-d8bb-586d-a7c2-4eac6691c52d")
METHODOLOGY_FACTUAL_RECORD_NAMESPACE = uuid.UUID("6c0ed11d-42cf-5746-aada-f54543224172")
METHODOLOGY_PLANNER_CONTRACT_VERSION = "selected-pair-planner-v2"
METHODOLOGY_TASK_CONTRACT_VERSION = "methodology-extraction-task-v2"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_POSTGRES_BIGINT_MAX = (1 << 63) - 1
_STEP7_NAME = StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION
_STEP7_ORDER = STEP_ORDER[_STEP7_NAME]

_METHODOLOGY_RESULT_LINK_COLUMNS: dict[str, tuple[str, ...]] = {
    "methodology_result_claim_links": (
        "claim_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "selection_id",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "claim_id",
        "canonical_key_sha256",
        "reused_existing_claim",
    ),
    "methodology_result_evidence_links": (
        "evidence_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "claim_link_id",
        "evidence_id",
        "document_id",
        "source_span_id",
        "locator_sha256",
    ),
    "methodology_result_sanad_links": (
        "sanad_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "claim_link_id",
        "sanad_id",
        "primary_evidence_id",
    ),
    "methodology_result_defect_links": (
        "defect_link_id",
        "tenant_id",
        "deal_id",
        "run_id",
        "result_id",
        "factual_output_record_id",
        "sanad_link_id",
        "defect_id",
    ),
}

_METHODOLOGY_RESULT_LINK_ID_COLUMNS = {
    "methodology_result_claim_links": "claim_link_id",
    "methodology_result_evidence_links": "evidence_link_id",
    "methodology_result_sanad_links": "sanad_link_id",
    "methodology_result_defect_links": "defect_link_id",
}


class MethodologyExtractionRepositoryError(RuntimeError):
    """Fail-closed persistence error exposing only a reviewed stable code."""

    __slots__ = ("code",)

    def __init__(self, code: str = METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str = METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED) -> Never:
    raise MethodologyExtractionRepositoryError(code)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, Decimal, uuid.UUID)):
        return str(value)
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, bool)):
        return enum_value
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _fixed_decimal_8(value: object) -> str:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        _fail()
    return f"{number.quantize(Decimal('0.00000001')):.8f}"


def _as_payload(value: object) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        payload = vars(value)
    if not isinstance(payload, dict):
        _fail()
    return payload


def _validated_canonical_claim_identity(
    *,
    tenant_id: str,
    deal_id: str,
    payload: Mapping[str, Any],
) -> tuple[CanonicalMethodologyClaimIdentity, dict[str, Any]]:
    """Recompute all identity proofs and reject caller- or storage-owned drift."""
    try:
        identity, canonical_value = CanonicalMethodologyClaimIdentity.recompute(
            tenant_id=tenant_id,
            deal_id=deal_id,
            normalization_rule_version=payload["normalization_rule_version"],
            normalized_predicate=payload["normalized_predicate"],
            normalized_subject=payload["normalized_subject"],
            normalized_period=payload["normalized_period"],
            normalized_unit=payload["normalized_unit"],
            value=payload["value"],
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        _fail()
    expected = identity.model_dump(mode="json")
    if any(payload.get(field) != value for field, value in expected.items()):
        _fail()
    return identity, canonical_value.model_dump(mode="json")


def _canonicalize_planned_task(value: object) -> PlannedExtractionTask:
    """Bind task hashing and durable span ordinals to one exact span order."""
    try:
        task = decode_extraction_task(value if isinstance(value, BaseModel) else _as_payload(value))
        payload = task.model_dump(mode="json")
        spans = payload.get("source_spans")
        if not isinstance(spans, list):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        payload["source_spans"] = sorted(
            spans,
            key=lambda span: (str(span.get("document_id", "")), str(span.get("span_id", ""))),
        )
        return decode_extraction_task(payload)
    except (AttributeError, TypeError, ValueError):
        _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)


def _value(value: object) -> object:
    return getattr(value, "value", value)


def _row_dict(row: object) -> dict[str, Any]:
    mapping = getattr(row, "_mapping", None)
    if mapping is None:
        _fail()
    return {
        str(key): (
            value.isoformat().replace("+00:00", "Z")
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, (uuid.UUID, Decimal))
            else value
        )
        for key, value in mapping.items()
    }


def _validate_run_global_defect_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    """Reject a second durable owner for any defect UUID without collapsing rows."""
    seen: set[str] = set()
    for row in rows:
        try:
            defect_id = str(uuid.UUID(str(row["defect_id"])))
        except (KeyError, TypeError, ValueError):
            _fail()
        if defect_id in seen:
            _fail()
        seen.add(defect_id)


def _coverage_row_is_pristine(row: Any, status: str) -> bool:
    return (
        str(row.status) == status
        and row.reason_code is None
        and _json_value(row.evidence_links) == []
        and row.answer is None
        and tuple(row.conflict_ids or ()) == ()
        and tuple(row.defect_ids or ()) == ()
    )


def _coverage_row_matches_projection(row: Any, expected: CoverageProjection) -> bool:
    return (
        str(row.coverage_record_id) == expected.coverage_record_id
        and str(row.methodology_question_record_id) == expected.methodology_question_record_id
        and str(row.status) == expected.status
        and row.reason_code == expected.reason_code
        and _json_value(row.evidence_links) == expected.evidence_payload()
        and _json_value(row.answer) == expected.answer_payload()
        and tuple(str(item) for item in (row.conflict_ids or ())) == expected.conflict_ids
        and tuple(str(item) for item in (row.defect_ids or ())) == expected.defect_ids
    )


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _validate_task6_evidence_required_fields(payload: Mapping[str, Any]) -> None:
    """Reject incomplete provenance on EvidenceItems admitted to Task 6."""
    for field in ("source_system", "upstream_origin_id", "verification_status"):
        value = _value(payload.get(field))
        if not isinstance(value, str) or not value.strip():
            _fail()
    if any(
        payload.get(field) is None for field in ("retrieval_timestamp", "rationale", "updated_at")
    ):
        _fail()
    if not isinstance(_json_value(payload["rationale"]), Mapping):
        _fail()


def _question_requires_calculation(value: Any) -> bool:
    payload = _json_value(value)
    if not isinstance(payload, Mapping):
        _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
    calculations = payload.get("required_calculations", [])
    if not isinstance(calculations, list):
        _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
    required = False
    for calculation in calculations:
        if not isinstance(calculation, Mapping):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        value = calculation.get("required", True)
        if not isinstance(value, bool):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        required = required or value
    return required


@dataclass(frozen=True, slots=True)
class DurableMethodologyExtractionTaskRecord:
    """Internal durable identity paired with the validated executable task."""

    task_record_id: str
    selection_id: str
    coverage_record_id: str
    methodology_version_record_id: str
    methodology_question_record_id: str
    task_input_sha256: str
    candidate_span_set_sha256: str
    task: PlannedExtractionTask


@dataclass(frozen=True, slots=True)
class _PlannedSpanSpec:
    task_span_record_id: str
    document_id: str
    span_id: str
    span_ordinal: int
    content_sha256: str
    locator_sha256: str


@dataclass(frozen=True, slots=True)
class _PlannedTaskSpec:
    record: DurableMethodologyExtractionTaskRecord
    classification_sha256: str
    payload: dict[str, Any]
    spans: tuple[_PlannedSpanSpec, ...]


def _planned_graph_signature(specs: Sequence[_PlannedTaskSpec]) -> tuple[str, ...]:
    return tuple(
        _canonical_json(
            {
                "record": {
                    "task_record_id": spec.record.task_record_id,
                    "selection_id": spec.record.selection_id,
                    "coverage_record_id": spec.record.coverage_record_id,
                    "methodology_version_record_id": (spec.record.methodology_version_record_id),
                    "methodology_question_record_id": (spec.record.methodology_question_record_id),
                    "task_input_sha256": spec.record.task_input_sha256,
                    "candidate_span_set_sha256": spec.record.candidate_span_set_sha256,
                    "task": spec.payload,
                },
                "classification_sha256": spec.classification_sha256,
                "spans": [
                    {
                        "task_span_record_id": span.task_span_record_id,
                        "document_id": span.document_id,
                        "span_id": span.span_id,
                        "span_ordinal": span.span_ordinal,
                        "content_sha256": span.content_sha256,
                        "locator_sha256": span.locator_sha256,
                    }
                    for span in spec.spans
                ],
            }
        )
        for spec in specs
    )


@dataclass(frozen=True, slots=True)
class DurableMethodologyTerminalizationProof:
    """Content-bound proof safe to expose only after the caller commits."""

    attempt_id: str
    result_id: str
    result_sha256: str
    prepared_call_sha256: str
    dispatched_call_sha256: str
    provider_response_sha256: str
    validated_result_sha256: str
    prospective_receipt_sha256: str
    task_input_sha256: str
    price_catalog_row_sha256: str
    worker_fencing_token: int
    budget_reservation_id: str
    budget_terminal_state: str
    budget_policy_id: str
    consumed_calls: int
    consumed_input_tokens: int
    consumed_output_tokens: int
    consumed_usd: str
    receipt_sha256: str
    proof_sha256: str


@dataclass(frozen=True, slots=True)
class DurableCanonicalMaterializationSnapshot:
    """Deterministically ordered, tenant-scoped restart materialization state."""

    factual_outputs: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    evidence_items: tuple[dict[str, Any], ...]
    sanads: tuple[dict[str, Any], ...]
    defects: tuple[dict[str, Any], ...]


class PostgresMethodologyExtractionRepository:
    """Caller-transaction-owned Task-6 repository; PostgreSQL is mandatory."""

    is_durable = True
    backend = "postgresql"

    def __init__(self, connection: Connection, tenant_id: str) -> None:
        if connection is None or not tenant_id:
            _fail()
        self._connection = connection
        self._tenant_id = str(tenant_id)
        set_tenant_local(connection, self._tenant_id)

    def load_strict_planning_source_spans(
        self,
        *,
        deal_id: str,
        source_span_references_by_document_id: Mapping[str, Sequence[SourceSpanReference]],
    ) -> dict[str, list[SourceSpanReference]]:
        """Reload exact tenant/deal source text for safe preflight span identities."""
        if not deal_id or not source_span_references_by_document_id:
            _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
        expected_by_span_id: dict[str, SourceSpanReference] = {}
        expected_document_ids = sorted(source_span_references_by_document_id)
        for document_id in expected_document_ids:
            references = source_span_references_by_document_id[document_id]
            if not document_id or not references:
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            for reference in references:
                if (
                    not isinstance(reference, SourceSpanReference)
                    or reference.document_id != document_id
                    or reference.span_id in expected_by_span_id
                    or reference.evidence_tags
                    or reference.text_excerpt is not None
                    or reference.content_hash is None
                    or _SHA256_RE.fullmatch(reference.content_hash) is None
                    or reference.span_type is None
                    or not reference.locator
                ):
                    _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
                expected_by_span_id[reference.span_id] = reference

        rows = (
            self._connection.execute(
                text(
                    """
                    SELECT document_id, span_id, span_type, locator,
                           text_excerpt, content_hash
                    FROM document_spans
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND document_id = ANY(CAST(:document_ids AS uuid[]))
                      AND span_id = ANY(CAST(:span_ids AS uuid[]))
                    ORDER BY document_id, span_id
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "document_ids": expected_document_ids,
                    "span_ids": sorted(expected_by_span_id),
                },
            )
            .mappings()
            .all()
        )
        if len(rows) != len(expected_by_span_id):
            _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)

        loaded: dict[str, list[SourceSpanReference]] = {
            document_id: [] for document_id in expected_document_ids
        }
        seen_span_ids: set[str] = set()
        for row in rows:
            document_id = str(row["document_id"])
            span_id = str(row["span_id"])
            expected = expected_by_span_id.get(span_id)
            try:
                validate_methodology_source_text_sha256(
                    text=row["text_excerpt"],
                    content_sha256=row["content_hash"],
                )
            except ValueError:
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            if (
                expected is None
                or span_id in seen_span_ids
                or expected.document_id != document_id
                or expected.content_hash != str(row["content_hash"])
                or expected.span_type != str(row["span_type"])
                or expected.locator != row["locator"]
            ):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            loaded[document_id].append(
                SourceSpanReference(
                    document_id=document_id,
                    span_id=span_id,
                    evidence_tags=[],
                    locator=dict(row["locator"]),
                    span_type=str(row["span_type"]),
                    content_hash=str(row["content_hash"]),
                    text_excerpt=str(row["text_excerpt"]),
                )
            )
            seen_span_ids.add(span_id)

        if seen_span_ids != set(expected_by_span_id) or any(not spans for spans in loaded.values()):
            _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
        return {
            document_id: sorted(spans, key=lambda span: span.span_id)
            for document_id, spans in loaded.items()
        }

    def persist_planned_tasks(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        tasks: Sequence[PlannedExtractionTask],
        lease_owner_id: str,
        lease_fencing_token: int,
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> list[DurableMethodologyExtractionTaskRecord]:
        """Persist one complete selected-pair READY plan in the caller transaction."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        if not tasks:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        validated = [_canonicalize_planned_task(task) for task in tasks]
        if any(
            task.tenant_id != self._tenant_id
            or task.deal_id != deal_id
            or task.run_id != run_id
            or task.status is not ExtractionTaskStatus.READY
            or task.coverage_record_id is None
            or task.document_id is None
            or task.classification_id is None
            or not task.source_spans
            for task in validated
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        selected_id, coverage = self._load_exact_selected_coverage(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
        )
        if selected_id != selection_id:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        expected = self._build_planned_task_specs(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
            tasks=validated,
            coverage=coverage,
        )
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(spec.record.task_record_id for spec in expected),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except ValueError:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        task_count, span_count = self._planned_graph_counts(deal_id=deal_id, run_id=run_id)
        if task_count or span_count:
            if not task_count or not span_count:
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            return self._validate_exact_planned_graph(
                deal_id=deal_id,
                run_id=run_id,
                selection_id=selection_id,
                coverage=coverage,
                expected=expected,
                expected_root_bindings=root_bindings,
            )

        for spec in expected:
            self._insert_planned_task_spec(
                deal_id=deal_id,
                run_id=run_id,
                selection_id=selection_id,
                spec=spec,
                run_output_object_id=(
                    None if root_bindings is None else root_bindings[spec.record.task_record_id]
                ),
            )
        return self._validate_exact_planned_graph(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
            coverage=coverage,
            expected=expected,
            expected_root_bindings=root_bindings,
        )

    def _load_exact_selected_coverage(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        selection = self._connection.execute(
            text(
                """
                SELECT s.selection_id, s.selected_question_count,
                       s.commercial_dd_version_record_id,
                       s.commercial_methodology_version_id,
                       s.financial_dd_version_record_id,
                       s.financial_methodology_version_id,
                       commercial.status AS commercial_status,
                       commercial.question_count AS commercial_question_count,
                       financial.status AS financial_status,
                       financial.question_count AS financial_question_count
                FROM run_methodology_selections s
                JOIN methodology_versions commercial
                  ON commercial.tenant_id=s.tenant_id
                 AND commercial.methodology_version_record_id=
                     s.commercial_dd_version_record_id
                 AND commercial.methodology_version_id=
                     s.commercial_methodology_version_id
                JOIN methodology_versions financial
                  ON financial.tenant_id=s.tenant_id
                 AND financial.methodology_version_record_id=
                     s.financial_dd_version_record_id
                 AND financial.methodology_version_id=
                     s.financial_methodology_version_id
                WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                  AND s.deal_id=CAST(:deal_id AS uuid)
                  AND s.run_id=CAST(:run_id AS uuid)
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).one_or_none()
        if (
            selection is None
            or (selection_id is not None and str(selection.selection_id) != selection_id)
            or int(selection.selected_question_count) != 100
            or str(selection.commercial_status) != "FROZEN"
            or str(selection.financial_status) != "FROZEN"
            or int(selection.commercial_question_count) != 21
            or int(selection.financial_question_count) != 79
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

        coverage_rows = self._connection.execute(
            text(
                """
                SELECT c.coverage_record_id, c.methodology_version_record_id,
                       c.methodology_version_id, c.methodology_question_record_id,
                       c.methodology_question_id, c.methodology_type, c.status
                FROM methodology_coverage_records c
                WHERE c.tenant_id=CAST(:tenant_id AS uuid)
                  AND c.deal_id=CAST(:deal_id AS uuid)
                  AND c.run_id=CAST(:run_id AS uuid)
                  AND c.selection_id=CAST(:selection_id AS uuid)
                ORDER BY c.methodology_question_record_id, c.coverage_record_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "selection_id": str(selection.selection_id),
            },
        ).fetchall()
        coverage = {str(row.coverage_record_id): row for row in coverage_rows}
        if len(coverage_rows) != 100 or len(coverage) != 100:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        commercial_record_id = str(selection.commercial_dd_version_record_id)
        financial_record_id = str(selection.financial_dd_version_record_id)
        commercial_version_id = str(selection.commercial_methodology_version_id)
        financial_version_id = str(selection.financial_methodology_version_id)
        commercial_count = 0
        financial_count = 0
        for row in coverage_rows:
            methodology_type = str(row.methodology_type)
            if methodology_type == "commercial_dd":
                commercial_count += 1
                valid = (
                    str(row.methodology_version_record_id) == commercial_record_id
                    and str(row.methodology_version_id) == commercial_version_id
                )
            elif methodology_type == "financial_dd":
                financial_count += 1
                valid = (
                    str(row.methodology_version_record_id) == financial_record_id
                    and str(row.methodology_version_id) == financial_version_id
                )
            else:
                valid = False
            if not valid:
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        if commercial_count != 21 or financial_count != 79:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        return str(selection.selection_id), coverage

    def _build_planned_task_specs(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        tasks: Sequence[PlannedExtractionTask],
        coverage: Mapping[str, Any],
    ) -> tuple[_PlannedTaskSpec, ...]:
        ordered_tasks = sorted(
            tasks,
            key=lambda item: (
                item.methodology_question_id,
                item.document_id or "",
                item.extraction_task_id or "",
            ),
        )
        extraction_task_ids = [task.extraction_task_id for task in ordered_tasks]
        if None in extraction_task_ids or len(extraction_task_ids) != len(set(extraction_task_ids)):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        planned_coverage = {str(task.coverage_record_id) for task in ordered_tasks}
        if set(coverage) != planned_coverage:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

        specs: list[_PlannedTaskSpec] = []
        for task in ordered_tasks:
            coverage_row = coverage.get(str(task.coverage_record_id))
            if coverage_row is None or (
                task.tenant_id != self._tenant_id
                or task.deal_id != deal_id
                or task.run_id != run_id
                or task.status is not ExtractionTaskStatus.READY
                or task.document_id is None
                or task.classification_id is None
                or task.methodology_version_id != str(coverage_row.methodology_version_id)
                or task.methodology_question_id != str(coverage_row.methodology_question_id)
                or str(_value(task.methodology_type)) != str(coverage_row.methodology_type)
                or task.methodology_id != str(coverage_row.methodology_type)
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            payload = task.model_dump(mode="json")
            canonical_task = _canonicalize_planned_task(payload)
            if canonical_task.model_dump(mode="json") != payload:
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            identity_payload = dict(payload)
            identity_payload["extraction_task_id"] = None
            if (
                _canonicalize_planned_task(identity_payload).extraction_task_id
                != task.extraction_task_id
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            task_input_sha256 = _sha256(payload)
            classification_sha256 = _sha256(
                {
                    "classification_id": task.classification_id,
                    "document_id": task.document_id,
                    "target_cdd_category": _value(task.target_cdd_category),
                    "target_fdd_category": _value(task.target_fdd_category),
                }
            )
            span_ids = [span.span_id for span in task.source_spans]
            if len(span_ids) != len(set(span_ids)) or any(
                span.document_id != task.document_id
                or span.content_hash is None
                or _SHA256_RE.fullmatch(span.content_hash) is None
                for span in task.source_spans
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            span_payloads = [
                {
                    "document_id": span.document_id,
                    "span_id": span.span_id,
                    "ordinal": ordinal,
                    "content_sha256": span.content_hash,
                    "locator_sha256": _sha256(span.locator),
                }
                for ordinal, span in enumerate(task.source_spans, start=1)
            ]
            candidate_set_sha256 = _sha256(span_payloads)
            task_record_id = str(
                uuid.uuid5(
                    METHODOLOGY_TASK_RECORD_NAMESPACE,
                    f"{self._tenant_id}:{run_id}:{task.extraction_task_id}:{task_input_sha256}",
                )
            )
            span_specs = tuple(
                _PlannedSpanSpec(
                    task_span_record_id=str(
                        uuid.uuid5(
                            METHODOLOGY_TASK_RECORD_NAMESPACE,
                            f"{task_record_id}:{span['span_id']}:{span['ordinal']}",
                        )
                    ),
                    document_id=str(span["document_id"]),
                    span_id=str(span["span_id"]),
                    span_ordinal=int(str(span["ordinal"])),
                    content_sha256=str(span["content_sha256"]),
                    locator_sha256=str(span["locator_sha256"]),
                )
                for span in span_payloads
            )
            specs.append(
                _PlannedTaskSpec(
                    record=DurableMethodologyExtractionTaskRecord(
                        task_record_id=task_record_id,
                        selection_id=selection_id,
                        coverage_record_id=str(task.coverage_record_id),
                        methodology_version_record_id=str(
                            coverage_row.methodology_version_record_id
                        ),
                        methodology_question_record_id=str(
                            coverage_row.methodology_question_record_id
                        ),
                        task_input_sha256=task_input_sha256,
                        candidate_span_set_sha256=candidate_set_sha256,
                        task=task,
                    ),
                    classification_sha256=classification_sha256,
                    payload=payload,
                    spans=span_specs,
                )
            )
        return tuple(specs)

    def _planned_graph_counts(self, *, deal_id: str, run_id: str) -> tuple[int, int]:
        row = self._connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM methodology_extraction_tasks
                     WHERE tenant_id=CAST(:tenant_id AS uuid)
                       AND deal_id=CAST(:deal_id AS uuid)
                       AND run_id=CAST(:run_id AS uuid)) AS task_count,
                    (SELECT count(*) FROM methodology_extraction_task_spans
                     WHERE tenant_id=CAST(:tenant_id AS uuid)
                       AND deal_id=CAST(:deal_id AS uuid)
                       AND run_id=CAST(:run_id AS uuid)) AS span_count
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).one()
        return int(row.task_count), int(row.span_count)

    def _insert_planned_task_spec(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        spec: _PlannedTaskSpec,
        run_output_object_id: str | None = None,
    ) -> None:
        task = spec.record.task
        params = {
            "task_record_id": spec.record.task_record_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "selection_id": selection_id,
            "coverage_record_id": spec.record.coverage_record_id,
            "methodology_version_record_id": spec.record.methodology_version_record_id,
            "methodology_version_id": task.methodology_version_id,
            "methodology_question_record_id": spec.record.methodology_question_record_id,
            "methodology_question_id": task.methodology_question_id,
            "methodology_type": str(_value(task.methodology_type)),
            "extraction_task_id": task.extraction_task_id,
            "document_id": task.document_id,
            "classification_id": task.classification_id,
            "classification_sha256": spec.classification_sha256,
            "planner_contract_version": METHODOLOGY_PLANNER_CONTRACT_VERSION,
            "task_contract_version": METHODOLOGY_TASK_CONTRACT_VERSION,
            "task_payload": _canonical_json(spec.payload),
            "task_input_sha256": spec.record.task_input_sha256,
            "candidate_set_sha256": spec.record.candidate_span_set_sha256,
        }
        if run_output_object_id is None:
            statement = """
                INSERT INTO methodology_extraction_tasks (
                    task_record_id, tenant_id, deal_id, run_id, selection_id,
                    coverage_record_id, methodology_version_record_id,
                    methodology_version_id, methodology_question_record_id,
                    methodology_question_id, methodology_type, extraction_task_id,
                    document_id, document_classification_id,
                    document_classification_sha256, planner_contract_version,
                    task_contract_version, task_payload, task_input_sha256,
                    candidate_span_set_sha256, status
                ) VALUES (
                    CAST(:task_record_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:methodology_version_record_id AS uuid),
                    :methodology_version_id,
                    CAST(:methodology_question_record_id AS uuid),
                    :methodology_question_id, :methodology_type,
                    :extraction_task_id, CAST(:document_id AS uuid),
                    :classification_id, :classification_sha256,
                    :planner_contract_version, :task_contract_version,
                    CAST(:task_payload AS jsonb), :task_input_sha256,
                    :candidate_set_sha256, 'READY'
                )
                """
        else:
            statement = """
                INSERT INTO methodology_extraction_tasks (
                    task_record_id, tenant_id, deal_id, run_id, selection_id,
                    coverage_record_id, methodology_version_record_id,
                    methodology_version_id, methodology_question_record_id,
                    methodology_question_id, methodology_type, extraction_task_id,
                    document_id, document_classification_id,
                    document_classification_sha256, planner_contract_version,
                    task_contract_version, task_payload, task_input_sha256,
                    candidate_span_set_sha256, status, run_output_object_id
                ) VALUES (
                    CAST(:task_record_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:selection_id AS uuid), :coverage_record_id,
                    CAST(:methodology_version_record_id AS uuid),
                    :methodology_version_id,
                    CAST(:methodology_question_record_id AS uuid),
                    :methodology_question_id, :methodology_type,
                    :extraction_task_id, CAST(:document_id AS uuid),
                    :classification_id, :classification_sha256,
                    :planner_contract_version, :task_contract_version,
                    CAST(:task_payload AS jsonb), :task_input_sha256,
                    :candidate_set_sha256, 'READY', CAST(:run_output_object_id AS uuid)
                )
                """
            params["run_output_object_id"] = run_output_object_id
        self._connection.execute(
            text(statement),
            params,
        )
        for span in spec.spans:
            self._connection.execute(
                text(
                    """
                    INSERT INTO methodology_extraction_task_spans (
                        task_span_record_id, tenant_id, deal_id, run_id,
                        task_record_id, document_id, span_id, span_ordinal,
                        content_sha256, locator_sha256
                    ) VALUES (
                        CAST(:task_span_record_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                        CAST(:task_record_id AS uuid), CAST(:document_id AS uuid),
                        CAST(:span_id AS uuid), :span_ordinal,
                        :content_sha256, :locator_sha256
                    )
                    """
                ),
                {
                    "task_span_record_id": span.task_span_record_id,
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "task_record_id": spec.record.task_record_id,
                    "document_id": span.document_id,
                    "span_id": span.span_id,
                    "span_ordinal": span.span_ordinal,
                    "content_sha256": span.content_sha256,
                    "locator_sha256": span.locator_sha256,
                },
            )

    def _require_planned_task_root_bindings(
        self,
        *,
        deal_id: str,
        run_id: str,
        expected_root_bindings: Mapping[str, str],
    ) -> None:
        rows = (
            self._connection.execute(
                text(
                    """
                    SELECT task_record_id, run_output_object_id
                    FROM methodology_extraction_tasks
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                    ORDER BY task_record_id
                    """
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            )
            .mappings()
            .all()
        )
        try:
            validate_durable_run_output_object_ids(
                rows=rows,
                stable_native_id_column="task_record_id",
                expected_run_output_object_ids=expected_root_bindings,
            )
        except ValueError:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

    def _validate_exact_planned_graph(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        coverage: Mapping[str, Any],
        expected: Sequence[_PlannedTaskSpec] | None = None,
        expected_root_bindings: Mapping[str, str] | None = None,
    ) -> list[DurableMethodologyExtractionTaskRecord]:
        task_rows = (
            self._connection.execute(
                text(
                    """
                SELECT task_record_id, selection_id, coverage_record_id,
                       methodology_version_record_id, methodology_version_id,
                       methodology_question_record_id, methodology_question_id,
                       methodology_type, extraction_task_id, document_id,
                       document_classification_id,
                       document_classification_sha256, planner_contract_version,
                       task_contract_version, task_payload, task_input_sha256,
                       candidate_span_set_sha256, status
                FROM methodology_extraction_tasks
                WHERE tenant_id=CAST(:tenant_id AS uuid)
                  AND deal_id=CAST(:deal_id AS uuid)
                  AND run_id=CAST(:run_id AS uuid)
                ORDER BY methodology_question_id, document_id, extraction_task_id
                """
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            )
            .mappings()
            .all()
        )
        if not task_rows:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        if expected_root_bindings is not None:
            self._require_planned_task_root_bindings(
                deal_id=deal_id,
                run_id=run_id,
                expected_root_bindings=expected_root_bindings,
            )
        durable_tasks: list[PlannedExtractionTask] = []
        for row in task_rows:
            raw_payload = _json_value(row["task_payload"])
            if not isinstance(raw_payload, Mapping):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            task = _canonicalize_planned_task(dict(raw_payload))
            if _canonical_json(raw_payload) != _canonical_json(task.model_dump(mode="json")):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            durable_tasks.append(task)
        durable_specs = self._build_planned_task_specs(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
            tasks=durable_tasks,
            coverage=coverage,
        )
        if len(task_rows) != len(durable_specs) or (
            expected is not None
            and _planned_graph_signature(durable_specs) != _planned_graph_signature(expected)
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        for row, spec in zip(task_rows, durable_specs, strict=True):
            if not self._task_row_matches_planned_spec(row=row, spec=spec):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

        span_rows = (
            self._connection.execute(
                text(
                    """
                SELECT s.task_span_record_id, s.task_record_id, s.document_id,
                       s.span_id, s.span_ordinal, s.content_sha256,
                       s.locator_sha256, live.document_id AS live_document_id,
                       live.content_hash AS live_content_sha256,
                       live.locator AS live_locator,
                       live.text_excerpt AS live_text_excerpt
                FROM methodology_extraction_task_spans s
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id=s.tenant_id
                 AND t.deal_id=s.deal_id
                 AND t.run_id=s.run_id
                 AND t.task_record_id=s.task_record_id
                LEFT JOIN document_spans live
                  ON live.tenant_id=s.tenant_id
                 AND live.deal_id=s.deal_id
                 AND live.document_id=s.document_id
                 AND live.span_id=s.span_id
                WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                  AND s.deal_id=CAST(:deal_id AS uuid)
                  AND s.run_id=CAST(:run_id AS uuid)
                ORDER BY t.methodology_question_id, t.document_id,
                         t.extraction_task_id, s.span_ordinal,
                         s.task_span_record_id
                """
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            )
            .mappings()
            .all()
        )
        expected_spans = [
            (spec, span, spec.record.task.source_spans[span.span_ordinal - 1])
            for spec in durable_specs
            for span in spec.spans
        ]
        if len(span_rows) != len(expected_spans):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        for row, (task_spec, span_spec, _source_span) in zip(
            span_rows, expected_spans, strict=True
        ):
            if not self._span_row_matches_planned_spec(
                row=row,
                task_spec=task_spec,
                span_spec=span_spec,
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        for row, (_task_spec, span_spec, source_span) in zip(
            span_rows, expected_spans, strict=True
        ):
            if not self._live_span_matches_admitted_identity(
                row=row,
                span_spec=span_spec,
                source_span=source_span,
            ):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
        return [spec.record for spec in durable_specs]

    @staticmethod
    def _task_row_matches_planned_spec(*, row: Any, spec: _PlannedTaskSpec) -> bool:
        task = spec.record.task
        return (
            str(row["task_record_id"]) == spec.record.task_record_id
            and str(row["selection_id"]) == spec.record.selection_id
            and str(row["coverage_record_id"]) == spec.record.coverage_record_id
            and str(row["methodology_version_record_id"])
            == spec.record.methodology_version_record_id
            and str(row["methodology_version_id"]) == task.methodology_version_id
            and str(row["methodology_question_record_id"])
            == spec.record.methodology_question_record_id
            and str(row["methodology_question_id"]) == task.methodology_question_id
            and str(row["methodology_type"]) == str(_value(task.methodology_type))
            and str(row["extraction_task_id"]) == task.extraction_task_id
            and str(row["document_id"]) == task.document_id
            and str(row["document_classification_id"]) == task.classification_id
            and str(row["document_classification_sha256"]) == spec.classification_sha256
            and str(row["planner_contract_version"]) == METHODOLOGY_PLANNER_CONTRACT_VERSION
            and str(row["task_contract_version"]) == METHODOLOGY_TASK_CONTRACT_VERSION
            and _canonical_json(_json_value(row["task_payload"])) == _canonical_json(spec.payload)
            and str(row["task_input_sha256"]) == spec.record.task_input_sha256
            and str(row["candidate_span_set_sha256"]) == spec.record.candidate_span_set_sha256
            and str(row["status"]) == "READY"
        )

    @staticmethod
    def _span_row_matches_planned_spec(
        *,
        row: Any,
        task_spec: _PlannedTaskSpec,
        span_spec: _PlannedSpanSpec,
    ) -> bool:
        return (
            str(row["task_span_record_id"]) == span_spec.task_span_record_id
            and str(row["task_record_id"]) == task_spec.record.task_record_id
            and str(row["document_id"]) == span_spec.document_id
            and str(row["span_id"]) == span_spec.span_id
            and int(row["span_ordinal"]) == span_spec.span_ordinal
            and str(row["content_sha256"]) == span_spec.content_sha256
            and str(row["locator_sha256"]) == span_spec.locator_sha256
        )

    @staticmethod
    def _live_span_matches_admitted_identity(
        *,
        row: Any,
        span_spec: _PlannedSpanSpec,
        source_span: Any,
    ) -> bool:
        try:
            validate_methodology_source_text_sha256(
                text=row["live_text_excerpt"],
                content_sha256=row["live_content_sha256"],
            )
            if source_span.text_excerpt is not None and (
                source_span.text_excerpt != row["live_text_excerpt"]
            ):
                return False
            live_locator_sha256 = _sha256(row["live_locator"])
        except (TypeError, ValueError):
            return False
        return (
            str(row["live_document_id"]) == span_spec.document_id
            and str(row["live_content_sha256"]) == span_spec.content_sha256
            and live_locator_sha256 == span_spec.locator_sha256
        )

    def insert_ready_task_batch(
        self,
        *,
        tasks: list[PlannedExtractionTask],
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> list[PlannedExtractionTask]:
        """Compatibility seam used by the durable planning service."""
        if not tasks:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        first = tasks[0]
        selection_id = self._connection.execute(
            text(
                """
                SELECT selection_id FROM run_methodology_selections
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": first.deal_id,
                "run_id": first.run_id,
            },
        ).scalar_one_or_none()
        if selection_id is None:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        records = self.persist_planned_tasks(
            deal_id=first.deal_id,
            run_id=first.run_id,
            selection_id=str(selection_id),
            tasks=tasks,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        return [record.task for record in records]

    def load_planned_task_records(
        self, *, deal_id: str, run_id: str
    ) -> list[DurableMethodologyExtractionTaskRecord]:
        selection_id, coverage = self._load_exact_selected_coverage(
            deal_id=deal_id,
            run_id=run_id,
        )
        return self._validate_exact_planned_graph(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
            coverage=coverage,
        )

    def validate_recomputed_planned_tasks(
        self,
        *,
        deal_id: str,
        run_id: str,
        selection_id: str,
        tasks: Sequence[PlannedExtractionTask],
    ) -> list[DurableMethodologyExtractionTaskRecord]:
        """Compare a freshly recomputed plan with the exact durable task graph.

        This path is intentionally read-only.  Restart callers must rederive the
        plan from frozen methodologies, coverage, and persisted preflight state;
        an absent or corrupt graph is rejected rather than inserted or repaired.
        """
        if not tasks:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        validated = [_canonicalize_planned_task(task) for task in tasks]
        selected_id, coverage = self._load_exact_selected_coverage(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selection_id,
        )
        expected = self._build_planned_task_specs(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selected_id,
            tasks=validated,
            coverage=coverage,
        )
        return self._validate_exact_planned_graph(
            deal_id=deal_id,
            run_id=run_id,
            selection_id=selected_id,
            coverage=coverage,
            expected=expected,
        )

    def load_planned_tasks(self, *, deal_id: str, run_id: str) -> list[PlannedExtractionTask]:
        return [
            record.task for record in self.load_planned_task_records(deal_id=deal_id, run_id=run_id)
        ]

    def validate_live_source_span_identity(self, *, task_record_id: str) -> None:
        """Recheck admitted span document/content/locator identity at each boundary."""
        rows = self._connection.execute(
            text(
                """
                SELECT admitted.document_id, admitted.span_id,
                       admitted.content_sha256, admitted.locator_sha256,
                       live.document_id AS live_document_id,
                       live.content_hash AS live_content_sha256,
                       live.locator AS live_locator,
                       live.text_excerpt AS live_text_excerpt
                FROM methodology_extraction_task_spans admitted
                JOIN document_spans live
                  ON live.tenant_id = admitted.tenant_id
                 AND live.deal_id = admitted.deal_id
                 AND live.document_id = admitted.document_id
                 AND live.span_id = admitted.span_id
                WHERE admitted.tenant_id = CAST(:tenant_id AS uuid)
                  AND admitted.task_record_id = CAST(:task_record_id AS uuid)
                ORDER BY admitted.span_ordinal
                """
            ),
            {"tenant_id": self._tenant_id, "task_record_id": task_record_id},
        ).fetchall()
        expected = self._connection.execute(
            text(
                """
                SELECT count(*) FROM methodology_extraction_task_spans
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND task_record_id = CAST(:task_record_id AS uuid)
                """
            ),
            {"tenant_id": self._tenant_id, "task_record_id": task_record_id},
        ).scalar_one()
        if not rows or len(rows) != int(expected):
            _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
        for row in rows:
            try:
                validate_methodology_source_text_sha256(
                    text=row.live_text_excerpt,
                    content_sha256=row.live_content_sha256,
                )
            except ValueError:
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            if (
                str(row.document_id) != str(row.live_document_id)
                or row.content_sha256 != row.live_content_sha256
                or row.locator_sha256 != _sha256(row.live_locator)
            ):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)

    def _require_fence(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        try:
            uuid.UUID(str(lease_owner_id))
        except (AttributeError, TypeError, ValueError):
            _fail(WORKER_LEASE_FENCED)
        if (
            type(lease_fencing_token) is not int
            or lease_fencing_token <= 0
            or lease_fencing_token > _POSTGRES_BIGINT_MAX
        ):
            _fail(WORKER_LEASE_FENCED)
        valid = self._connection.execute(
            text(
                """
                SELECT 1 FROM runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'RUNNING'
                  AND lease_owner_id = CAST(:lease_owner_id AS uuid)
                  AND lease_fencing_token = :lease_fencing_token
                  AND lease_expires_at > statement_timestamp()
                FOR UPDATE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        ).scalar_one_or_none()
        if valid != 1:
            _fail(WORKER_LEASE_FENCED)
        self._connection.execute(
            text(
                """
                SELECT
                    set_config('idis.worker_lease_tenant_id', :tenant_id, true),
                    set_config('idis.worker_lease_deal_id', :deal_id, true),
                    set_config('idis.worker_lease_run_id', :run_id, true),
                    set_config('idis.worker_lease_owner_id', :lease_owner_id, true),
                    set_config(
                        'idis.worker_lease_fencing_token',
                        CAST(:lease_fencing_token AS text),
                        true
                    )
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        )

    def _lock_running_canonical_step7(self, *, run_id: str, run_step_id: str) -> None:
        """Lock the exact human Step 7 after the run lease and before child rows."""
        step_id = self._connection.execute(
            text(
                """
                SELECT step_id
                FROM run_steps
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND step_id = CAST(:run_step_id AS uuid)
                  AND step_name = :step_name
                  AND step_order = :step_order
                  AND status = 'RUNNING'
                FOR UPDATE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "run_id": run_id,
                "run_step_id": run_step_id,
                "step_name": _STEP7_NAME.value,
                "step_order": _STEP7_ORDER,
            },
        ).scalar_one_or_none()
        if step_id is None:
            _fail(WORKER_LEASE_FENCED)

    def _validate_terminal_source_scope(
        self,
        *,
        task_record_id: str,
        result: ValidatedDispatchedMethodologyResult,
    ) -> None:
        rows = self._connection.execute(
            text(
                """
                SELECT t.methodology_question_record_id,
                       s.document_id, s.span_id, s.span_ordinal,
                       s.content_sha256, s.locator_sha256,
                       live.text_excerpt AS live_text_excerpt
                FROM methodology_extraction_tasks t
                JOIN methodology_extraction_task_spans s
                  ON s.tenant_id = t.tenant_id AND s.deal_id = t.deal_id
                 AND s.run_id = t.run_id AND s.task_record_id = t.task_record_id
                JOIN document_spans live
                  ON live.tenant_id = s.tenant_id
                 AND live.deal_id = s.deal_id
                 AND live.document_id = s.document_id
                 AND live.span_id = s.span_id
                WHERE t.tenant_id = CAST(:tenant_id AS uuid)
                  AND t.task_record_id = CAST(:task_record_id AS uuid)
                ORDER BY s.span_ordinal, s.span_id
                """
            ),
            {"tenant_id": self._tenant_id, "task_record_id": task_record_id},
        ).fetchall()
        if not rows:
            _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
        admitted = {
            str(row.span_id): (str(row.document_id), str(row.locator_sha256)) for row in rows
        }
        expected_searched_span_ids = tuple(sorted(admitted))
        expected_searched_document_ids = tuple(
            sorted({document_id for document_id, _locator in admitted.values()})
        )
        try:
            expected_searched_scope_sha256 = compute_methodology_searched_scope_sha256(
                tuple(
                    MethodologyCandidateSpan(
                        document_id=str(row.document_id),
                        span_id=str(row.span_id),
                        ordinal=int(row.span_ordinal),
                        content_sha256=str(row.content_sha256),
                        locator_sha256=str(row.locator_sha256),
                        text=str(row.live_text_excerpt),
                    )
                    for row in rows
                )
            )
        except ValueError:
            _fail(METHODOLOGY_SEARCH_SCOPE_MISMATCH)
        if (
            result.searched_span_ids != expected_searched_span_ids
            or result.searched_document_ids != expected_searched_document_ids
            or result.searched_scope_sha256 != expected_searched_scope_sha256
        ):
            _fail(METHODOLOGY_SEARCH_SCOPE_MISMATCH)
        searched_spans = set(result.searched_span_ids)
        question_record_id = str(rows[0].methodology_question_record_id)
        for output in result.factual_outputs:
            accepted_pairs = tuple(
                zip(
                    output.accepted_source_span_ids,
                    output.accepted_locator_sha256s,
                    strict=True,
                )
            )
            if output.methodology_question_record_id != question_record_id or any(
                span_id not in searched_spans
                or span_id not in admitted
                or admitted[span_id][1] != locator_sha256
                for span_id, locator_sha256 in accepted_pairs
            ):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)

    def admit_attempt(
        self,
        *,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        task_record_id: str,
        task_input_sha256: str,
        prepared_call_sha256: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        provider: str,
        configured_model: str,
        prompt_id: str,
        prompt_content_sha256: str,
        budget_reservation_id: str,
        backend: str = "live",
        transport_kind: str = "LIVE",
        prompt_version: str = "1.0.0",
        prompt_input_schema_sha256: str = "",
        prompt_output_schema_sha256: str = "",
        request_payload_sha256: str = "",
        billable_payload_sha256: str = "",
        billable_payload_utf8_bytes: int = 0,
        configured_max_tokens: int = 0,
        configured_temperature: Decimal | str = Decimal("0"),
        configured_json_mode: bool = True,
        budget_policy_id: str = "",
        verified_budget_policy: VerifiedActiveProviderBudgetPolicy | None = None,
        budget_component: str = "anthropic",
        price_catalog_version: str = "",
        price_catalog_row_sha256: str = "",
        input_usd_per_million_tokens: Decimal | str = Decimal("0"),
        output_usd_per_million_tokens: Decimal | str = Decimal("0"),
        reserved_calls: int = 1,
        reserved_input_tokens: int = 0,
        reserved_output_tokens: int = 0,
        reserved_usd: Decimal | str = Decimal("0"),
    ) -> dict[str, Any]:
        """Admit exactly one paid-call identity while the Step-7 row stays RUNNING."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        self.validate_live_source_span_identity(task_record_id=task_record_id)
        admission_hashes = (
            task_input_sha256,
            prompt_content_sha256,
            prompt_input_schema_sha256,
            prompt_output_schema_sha256,
            request_payload_sha256,
            prepared_call_sha256,
            billable_payload_sha256,
        )
        if (
            not all(
                (
                    prompt_input_schema_sha256,
                    prompt_output_schema_sha256,
                    request_payload_sha256,
                    prepared_call_sha256,
                    billable_payload_sha256,
                    budget_policy_id,
                    price_catalog_version,
                )
            )
            or any(_SHA256_RE.fullmatch(value) is None for value in admission_hashes)
            or billable_payload_utf8_bytes <= 0
            or configured_max_tokens <= 0
        ):
            _fail()
        real_call = transport_kind == "LIVE"
        if (backend == "hermetic") != (transport_kind == "HERMETIC_TEST"):
            _fail()
        if real_call:
            try:
                price = resolve_anthropic_standard_price(model_id=configured_model)
                input_price = _fixed_decimal_8(input_usd_per_million_tokens)
                output_price = _fixed_decimal_8(output_usd_per_million_tokens)
                reserved_usd_fixed = _fixed_decimal_8(reserved_usd)
                expected_reserved_usd = compute_anthropic_usd(
                    input_tokens=reserved_input_tokens,
                    output_tokens=reserved_output_tokens,
                    input_usd_per_million_tokens=price.input_usd_per_million_tokens,
                    output_usd_per_million_tokens=price.output_usd_per_million_tokens,
                )
                policy = verified_budget_policy
                component = (
                    policy.components.get("anthropic")
                    if isinstance(policy, VerifiedActiveProviderBudgetPolicy)
                    else None
                )
                if not isinstance(component, Mapping):
                    _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
                max_calls = component.get("max_calls")
                max_input_tokens = component.get("max_input_tokens")
                max_output_tokens = component.get("max_output_tokens")
                max_usd = Decimal(str(component.get("max_usd")))
                if (
                    provider != "anthropic"
                    or budget_component != "anthropic"
                    or policy is None
                    or policy.signature_verified is not True
                    or policy.tenant_id != self._tenant_id
                    or policy.policy_hash != budget_policy_id
                    or policy.payload_hash != budget_policy_id
                    or policy.currency != "USD"
                    or not policy.total_max_usd.is_finite()
                    or type(billable_payload_utf8_bytes) is not int
                    or type(configured_max_tokens) is not int
                    or Decimal(str(configured_temperature)) != 0
                    or configured_json_mode is not True
                    or type(reserved_calls) is not int
                    or type(reserved_input_tokens) is not int
                    or type(reserved_output_tokens) is not int
                    or type(max_calls) is not int
                    or max_calls < reserved_calls
                    or type(max_input_tokens) is not int
                    or max_input_tokens < reserved_input_tokens
                    or type(max_output_tokens) is not int
                    or max_output_tokens < reserved_output_tokens
                    or not max_usd.is_finite()
                    or max_usd < Decimal(reserved_usd_fixed)
                    or policy.total_max_usd < Decimal(reserved_usd_fixed)
                    or price.catalog_version != price_catalog_version
                    or _SHA256_RE.fullmatch(price_catalog_row_sha256) is None
                    or price.row_sha256 != price_catalog_row_sha256
                    or Decimal(str(input_usd_per_million_tokens))
                    != Decimal(price.input_usd_per_million_tokens)
                    or Decimal(str(output_usd_per_million_tokens))
                    != Decimal(price.output_usd_per_million_tokens)
                    or input_price != price.input_usd_per_million_tokens
                    or output_price != price.output_usd_per_million_tokens
                    or reserved_calls != 1
                    or reserved_input_tokens != billable_payload_utf8_bytes
                    or reserved_output_tokens != configured_max_tokens
                    or reserved_input_tokens + reserved_output_tokens > price.context_window_limit
                    or reserved_usd_fixed != expected_reserved_usd
                    or request_payload_sha256 != billable_payload_sha256
                ):
                    _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
            except (
                InvalidOperation,
                KeyError,
                MethodologyPriceCatalogError,
                TypeError,
                ValueError,
            ):
                _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        else:
            try:
                hermetic_reserved_usd = Decimal(str(reserved_usd))
            except InvalidOperation:
                _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
            if verified_budget_policy is not None or any(
                (
                    reserved_calls,
                    reserved_input_tokens,
                    reserved_output_tokens,
                    hermetic_reserved_usd,
                )
            ):
                _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        attempt_id = str(
            uuid.uuid5(
                METHODOLOGY_ATTEMPT_NAMESPACE,
                f"{self._tenant_id}:{run_id}:{task_record_id}:{task_input_sha256}",
            )
        )
        params = {
            "attempt_id": attempt_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "run_step_id": run_step_id,
            "task_record_id": task_record_id,
            "task_input_sha256": task_input_sha256,
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": lease_fencing_token,
            "provider": provider,
            "backend": backend,
            "transport_kind": transport_kind,
            "configured_model": configured_model,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "prompt_content_sha256": prompt_content_sha256,
            "prompt_input_schema_sha256": prompt_input_schema_sha256,
            "prompt_output_schema_sha256": prompt_output_schema_sha256,
            "request_payload_sha256": request_payload_sha256,
            "prepared_call_sha256": prepared_call_sha256,
            "billable_payload_sha256": billable_payload_sha256,
            "billable_payload_utf8_bytes": billable_payload_utf8_bytes,
            "real_call": real_call,
            "configured_max_tokens": configured_max_tokens,
            "configured_temperature": str(configured_temperature),
            "configured_json_mode": configured_json_mode,
            "budget_policy_id": budget_policy_id,
            "budget_component": budget_component,
            "budget_reservation_id": budget_reservation_id,
            "price_catalog_version": price_catalog_version,
            "price_catalog_row_sha256": price_catalog_row_sha256,
            "input_price": _fixed_decimal_8(input_usd_per_million_tokens),
            "output_price": _fixed_decimal_8(output_usd_per_million_tokens),
            "reserved_calls": reserved_calls,
            "reserved_input_tokens": reserved_input_tokens,
            "reserved_output_tokens": reserved_output_tokens,
            "reserved_usd": _fixed_decimal_8(reserved_usd),
            "budget_state": "RESERVED" if real_call else "NOT_BILLABLE",
        }
        try:
            self._connection.execute(
                text(
                    """
                    INSERT INTO methodology_extraction_attempts (
                        attempt_id, tenant_id, deal_id, run_id, run_step_id,
                        task_record_id, task_input_sha256, lease_owner_id,
                        lease_fencing_token, status, provider, backend,
                        transport_kind, configured_model, prompt_id,
                        prompt_version, prompt_content_sha256,
                        prompt_input_schema_sha256, prompt_output_schema_sha256,
                        request_payload_sha256, billable_payload_sha256,
                        prepared_call_sha256,
                        billable_payload_utf8_bytes, real_call,
                        configured_max_tokens, configured_temperature,
                        configured_json_mode, budget_policy_id, budget_component,
                        budget_reservation_id, price_catalog_version,
                        price_catalog_row_sha256,
                        input_usd_per_million_tokens,
                        output_usd_per_million_tokens, reserved_calls,
                        reserved_input_tokens, reserved_output_tokens,
                        reserved_embedding_tokens, reserved_usd,
                        budget_terminal_state
                    ) VALUES (
                        CAST(:attempt_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                        CAST(:run_step_id AS uuid), CAST(:task_record_id AS uuid),
                        :task_input_sha256, CAST(:lease_owner_id AS uuid),
                        :lease_fencing_token, 'STARTED', :provider, :backend,
                        :transport_kind, :configured_model, :prompt_id,
                        :prompt_version, :prompt_content_sha256,
                        :prompt_input_schema_sha256, :prompt_output_schema_sha256,
                        :request_payload_sha256, :billable_payload_sha256,
                        :prepared_call_sha256,
                        :billable_payload_utf8_bytes, :real_call,
                        :configured_max_tokens, :configured_temperature,
                        :configured_json_mode, :budget_policy_id,
                        :budget_component, CAST(:budget_reservation_id AS uuid),
                        :price_catalog_version, :price_catalog_row_sha256,
                        :input_price, :output_price,
                        :reserved_calls, :reserved_input_tokens,
                        :reserved_output_tokens, 0, :reserved_usd, :budget_state
                    )
                    """
                ),
                params,
            )
        except IntegrityError:
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        return self._load_attempt(attempt_id=attempt_id)

    def finalize_attempt_success(
        self,
        *,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        attempt_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        observed_model: str,
        provider_request_id: str,
        provider_response_sha256: str,
        dispatched_call_sha256: str,
        validated_result_sha256: str,
        prospective_receipt_sha256: str,
        budget_policy_id: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str,
        result: object,
        factual_outputs: Sequence[object] | None = None,
    ) -> DurableMethodologyTerminalizationProof:
        """Terminalize one task atomically while the RunStep remains RUNNING."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        attempt = self._load_attempt(attempt_id=attempt_id, for_update=True)
        if attempt["budget_policy_id"] != budget_policy_id:
            _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        if (
            attempt["status"] != "STARTED"
            or attempt["run_step_id"] != run_step_id
            or observed_model != attempt["configured_model"]
            or _SHA256_RE.fullmatch(dispatched_call_sha256) is None
            or _SHA256_RE.fullmatch(provider_response_sha256) is None
            or _SHA256_RE.fullmatch(validated_result_sha256) is None
            or _SHA256_RE.fullmatch(prospective_receipt_sha256) is None
        ):
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        self.validate_live_source_span_identity(task_record_id=attempt["task_record_id"])
        budget_state = "CONSUMED" if attempt["real_call"] else "NOT_BILLABLE"
        try:
            validated_result = ValidatedDispatchedMethodologyResult.model_validate(result)
        except ValueError:
            _fail()
        result_sha256 = validated_result.validated_result_sha256
        if (
            result_sha256 != validated_result_sha256
            or validated_result.dispatched_call_sha256 != dispatched_call_sha256
            or validated_result.task_input_sha256 != attempt["task_input_sha256"]
        ):
            _fail()
        self._validate_terminal_source_scope(
            task_record_id=attempt["task_record_id"],
            result=validated_result,
        )
        if attempt["real_call"]:
            try:
                price = resolve_anthropic_standard_price(model_id=str(attempt["configured_model"]))
                stored_input_price = _fixed_decimal_8(attempt["input_usd_per_million_tokens"])
                stored_output_price = _fixed_decimal_8(attempt["output_usd_per_million_tokens"])
                if (
                    type(input_tokens) is not int
                    or input_tokens < 0
                    or input_tokens > int(attempt["reserved_input_tokens"])
                    or type(output_tokens) is not int
                    or output_tokens < 0
                    or output_tokens > int(attempt["reserved_output_tokens"])
                    or attempt["price_catalog_version"] != price.catalog_version
                    or attempt["price_catalog_row_sha256"] != price.row_sha256
                    or stored_input_price != price.input_usd_per_million_tokens
                    or stored_output_price != price.output_usd_per_million_tokens
                ):
                    _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
                consumed_usd = compute_anthropic_usd(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    input_usd_per_million_tokens=price.input_usd_per_million_tokens,
                    output_usd_per_million_tokens=price.output_usd_per_million_tokens,
                )
                if Decimal(consumed_usd) > Decimal(str(attempt["reserved_usd"])):
                    _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
            except (
                InvalidOperation,
                MethodologyPriceCatalogError,
                TypeError,
                ValueError,
            ):
                _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
            consumed_calls = 1
            consumed_input_tokens = input_tokens
            consumed_output_tokens = output_tokens
        else:
            consumed_usd = "0.00000000"
            consumed_calls = 0
            consumed_input_tokens = 0
            consumed_output_tokens = 0
        validated_outputs = [
            output.model_dump(mode="json") for output in validated_result.factual_outputs
        ]
        if (
            factual_outputs is not None
            and [_as_payload(output) for output in factual_outputs] != validated_outputs
        ):
            _fail()
        result_id = str(
            uuid.uuid5(
                METHODOLOGY_RESULT_NAMESPACE,
                f"{attempt['attempt_id']}:{result_sha256}",
            )
        )
        receipt_fields = {
            "provider": str(attempt["provider"]),
            "backend": str(attempt["backend"]),
            "configured_model": str(attempt["configured_model"]),
            "observed_model": observed_model,
            "prompt_id": str(attempt["prompt_id"]),
            "prompt_version": str(attempt["prompt_version"]),
            "prompt_content_sha256": str(attempt["prompt_content_sha256"]),
            "prompt_input_schema_sha256": str(attempt["prompt_input_schema_sha256"]),
            "prompt_output_schema_sha256": str(attempt["prompt_output_schema_sha256"]),
            "request_payload_sha256": str(attempt["billable_payload_sha256"]),
            "provider_request_id": provider_request_id,
            "provider_response_sha256": provider_response_sha256,
            "real_call": bool(attempt["real_call"]),
            "configured_max_tokens": int(attempt["configured_max_tokens"]),
            "configured_temperature": "0",
            "configured_json_mode": bool(attempt["configured_json_mode"]),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "budget_policy_id": str(attempt["budget_policy_id"]),
            "budget_component": str(attempt["budget_component"]),
            "budget_reservation_id": str(attempt["budget_reservation_id"]),
            "billable_payload_sha256": str(attempt["billable_payload_sha256"]),
            "billable_payload_utf8_bytes": int(attempt["billable_payload_utf8_bytes"]),
            "price_catalog_version": str(attempt["price_catalog_version"]),
            "price_catalog_row_sha256": str(attempt["price_catalog_row_sha256"]),
            "input_usd_per_million_tokens": _fixed_decimal_8(
                attempt["input_usd_per_million_tokens"]
            ),
            "output_usd_per_million_tokens": _fixed_decimal_8(
                attempt["output_usd_per_million_tokens"]
            ),
            "reserved_calls": int(attempt["reserved_calls"]),
            "reserved_input_tokens": int(attempt["reserved_input_tokens"]),
            "reserved_output_tokens": int(attempt["reserved_output_tokens"]),
            "reserved_embedding_tokens": int(attempt["reserved_embedding_tokens"]),
            "reserved_usd": _fixed_decimal_8(attempt["reserved_usd"]),
            "budget_terminal_state": budget_state,
        }
        try:
            computed_prospective_receipt_sha256 = compute_methodology_provider_receipt_sha256(
                receipt_fields=receipt_fields,
                validated_result_sha256=validated_result_sha256,
            )
            if computed_prospective_receipt_sha256 != prospective_receipt_sha256:
                _fail()
            MethodologyProviderReceipt.model_validate(
                {**receipt_fields, "receipt_sha256": prospective_receipt_sha256}
            )
        except ValueError:
            _fail()
        updated = self._connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status = 'SUCCEEDED', observed_model = :observed_model,
                    provider_request_id = :provider_request_id,
                    provider_response_sha256 = :provider_response_sha256,
                    dispatched_call_sha256 = :dispatched_call_sha256,
                    completed_at = now(), latency_ms = :latency_ms,
                    input_tokens = :input_tokens, output_tokens = :output_tokens,
                    stop_reason = :stop_reason, receipt_sha256 = :receipt_sha256,
                    budget_terminal_state = :budget_state
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND attempt_id = CAST(:attempt_id AS uuid)
                  AND status = 'STARTED'
                RETURNING attempt_id
                """
            ),
            {
                "observed_model": observed_model,
                "provider_request_id": provider_request_id,
                "provider_response_sha256": provider_response_sha256,
                "dispatched_call_sha256": dispatched_call_sha256,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "stop_reason": stop_reason,
                "receipt_sha256": prospective_receipt_sha256,
                "budget_state": budget_state,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
            },
        ).scalar_one_or_none()
        if updated is None:
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        persisted = self._persist_terminal_result(
            deal_id=deal_id,
            run_id=run_id,
            attempt=attempt,
            result=validated_result,
            factual_outputs=validated_result.factual_outputs,
            provider_provenance_sha256=prospective_receipt_sha256,
            expected_result_id=result_id,
            expected_result_sha256=result_sha256,
        )
        proof_payload = {
            "attempt_id": attempt_id,
            "result_id": persisted["result_id"],
            "result_sha256": persisted["result_sha256"],
            "prepared_call_sha256": attempt["prepared_call_sha256"],
            "dispatched_call_sha256": dispatched_call_sha256,
            "provider_response_sha256": provider_response_sha256,
            "validated_result_sha256": validated_result_sha256,
            "prospective_receipt_sha256": prospective_receipt_sha256,
            "task_input_sha256": attempt["task_input_sha256"],
            "price_catalog_row_sha256": attempt["price_catalog_row_sha256"],
            "worker_fencing_token": int(attempt["lease_fencing_token"]),
            "budget_reservation_id": attempt["budget_reservation_id"],
            "budget_terminal_state": budget_state,
            "budget_policy_id": attempt["budget_policy_id"],
            "consumed_calls": consumed_calls,
            "consumed_input_tokens": consumed_input_tokens,
            "consumed_output_tokens": consumed_output_tokens,
            "consumed_usd": consumed_usd,
            "receipt_sha256": prospective_receipt_sha256,
        }
        return DurableMethodologyTerminalizationProof(
            **proof_payload,
            proof_sha256=_sha256(proof_payload),
        )

    def finalize_attempt_failure(
        self,
        *,
        deal_id: str,
        run_id: str,
        attempt_id: str,
        run_step_id: str,
        budget_reservation_id: str,
        budget_policy_id: str,
        safe_failure_code: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Refund proof and FAILED step transition share this caller transaction."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        attempt = self._load_attempt(attempt_id=attempt_id, for_update=True)
        if attempt["budget_policy_id"] != budget_policy_id:
            _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        if (
            attempt["status"] != "STARTED"
            or attempt["run_step_id"] != run_step_id
            or attempt["budget_reservation_id"] != budget_reservation_id
        ):
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        budget_state = "REFUNDED" if attempt["real_call"] else "NOT_BILLABLE"
        self._connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status = 'FAILED', completed_at = now(),
                    budget_terminal_state = :budget_state,
                    safe_failure_code = :safe_failure_code
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND attempt_id = CAST(:attempt_id AS uuid)
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "attempt_id": attempt_id,
                "budget_state": budget_state,
                "safe_failure_code": safe_failure_code,
            },
        )
        self._fail_running_step(
            run_id=run_id,
            run_step_id=run_step_id,
            safe_failure_code=safe_failure_code,
        )

    def finalize_received_response_failure(
        self,
        *,
        deal_id: str,
        run_id: str,
        attempt_id: str,
        run_step_id: str,
        budget_reservation_id: str,
        budget_policy_id: str,
        safe_failure_code: str,
        observed_model: str | None,
        provider_request_id: str | None,
        provider_response_sha256: str,
        dispatched_call_sha256: str,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        stop_reason: str | None,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Persist a billable received response that failed result validation."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        attempt = self._load_attempt(attempt_id=attempt_id, for_update=True)
        if (
            attempt["budget_policy_id"] != budget_policy_id
            or attempt["budget_reservation_id"] != budget_reservation_id
        ):
            _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        if (
            attempt["status"] != "STARTED"
            or attempt["run_step_id"] != run_step_id
            or not attempt["real_call"]
            or _SHA256_RE.fullmatch(provider_response_sha256) is None
            or _SHA256_RE.fullmatch(dispatched_call_sha256) is None
            or not isinstance(safe_failure_code, str)
            or not safe_failure_code.strip()
            or type(latency_ms) is not int
            or latency_ms < 0
        ):
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)

        returned_text_values = (observed_model, provider_request_id, stop_reason)
        if any(
            value is not None
            and (type(value) is not str or not value or value != value.strip() or "\x00" in value)
            for value in returned_text_values
        ):
            _fail(METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED)
        returned_usage_values = (input_tokens, output_tokens)
        if any(
            value is not None
            and (type(value) is not int or value < 0 or value > _POSTGRES_BIGINT_MAX)
            for value in returned_usage_values
        ):
            _fail(METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED)
        try:
            price = resolve_anthropic_standard_price(model_id=str(attempt["configured_model"]))
            stored_input_price = _fixed_decimal_8(attempt["input_usd_per_million_tokens"])
            stored_output_price = _fixed_decimal_8(attempt["output_usd_per_million_tokens"])
            if (
                attempt["price_catalog_version"] != price.catalog_version
                or attempt["price_catalog_row_sha256"] != price.row_sha256
                or stored_input_price != price.input_usd_per_million_tokens
                or stored_output_price != price.output_usd_per_million_tokens
            ):
                _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
            observed_usd = (
                Decimal(
                    compute_anthropic_usd(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        input_usd_per_million_tokens=(price.input_usd_per_million_tokens),
                        output_usd_per_million_tokens=(price.output_usd_per_million_tokens),
                    )
                )
                if input_tokens is not None and output_tokens is not None
                else None
            )
        except (
            InvalidOperation,
            MethodologyPriceCatalogError,
            TypeError,
            ValueError,
        ):
            _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)

        provenance_complete = (
            observed_model == attempt["configured_model"]
            and provider_request_id is not None
            and stop_reason is not None
        )
        usage_within_reservation = (
            input_tokens is not None
            and output_tokens is not None
            and input_tokens <= int(attempt["reserved_input_tokens"])
            and output_tokens <= int(attempt["reserved_output_tokens"])
            and observed_usd is not None
            and observed_usd <= Decimal(str(attempt["reserved_usd"]))
        )
        if safe_failure_code == METHODOLOGY_PROVIDER_USAGE_INVALID:
            if not provenance_complete or usage_within_reservation:
                _fail(METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED)
        elif safe_failure_code != METHODOLOGY_PROVIDER_PROVENANCE_INVALID and (
            not provenance_complete or not usage_within_reservation
        ):
            _fail(METHODOLOGY_BUDGET_LINEAGE_REJECTED)
        updated = self._connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status = 'FAILED', completed_at = now(),
                    observed_model = :observed_model,
                    provider_request_id = :provider_request_id,
                    provider_response_sha256 = :provider_response_sha256,
                    dispatched_call_sha256 = :dispatched_call_sha256,
                    latency_ms = :latency_ms, input_tokens = :input_tokens,
                    output_tokens = :output_tokens, stop_reason = :stop_reason,
                    budget_terminal_state = 'CONSUMED',
                    safe_failure_code = :safe_failure_code
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND attempt_id = CAST(:attempt_id AS uuid)
                  AND status = 'STARTED' AND real_call
                RETURNING attempt_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "observed_model": observed_model,
                "provider_request_id": provider_request_id,
                "provider_response_sha256": provider_response_sha256,
                "dispatched_call_sha256": dispatched_call_sha256,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "stop_reason": stop_reason,
                "safe_failure_code": safe_failure_code,
            },
        ).scalar_one_or_none()
        if updated is None:
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        self._fail_running_step(
            run_id=run_id,
            run_step_id=run_step_id,
            safe_failure_code=safe_failure_code,
        )

    def mark_attempt_outcome_unknown(
        self,
        *,
        deal_id: str,
        run_id: str,
        attempt_id: str,
        run_step_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Fence a crash-ambiguous admitted request; it is never called again."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        attempt = self._load_attempt(attempt_id=attempt_id, for_update=True)
        if attempt["status"] != "STARTED" or attempt["run_step_id"] != run_step_id:
            _fail(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)
        self._connection.execute(
            text(
                """
                UPDATE methodology_extraction_attempts
                SET status = 'OUTCOME_UNKNOWN', completed_at = now(),
                    safe_failure_code = 'METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN'
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND attempt_id = CAST(:attempt_id AS uuid)
                """
            ),
            {"tenant_id": self._tenant_id, "attempt_id": attempt_id},
        )
        self._fail_running_step(
            run_id=run_id,
            run_step_id=run_step_id,
            safe_failure_code=METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN,
        )

    def _persist_terminal_result(
        self,
        *,
        deal_id: str,
        run_id: str,
        attempt: Mapping[str, Any],
        result: object,
        factual_outputs: Sequence[object] | None,
        provider_provenance_sha256: str,
        expected_result_id: str,
        expected_result_sha256: str,
    ) -> dict[str, Any]:
        payload = _as_payload(result)
        outcome = str(_value(payload.get("outcome")))
        if outcome not in {"EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"}:
            _fail()
        result_sha256 = str(payload.get("validated_result_sha256") or "")
        result_id = str(
            uuid.uuid5(
                METHODOLOGY_RESULT_NAMESPACE,
                f"{attempt['attempt_id']}:{result_sha256}",
            )
        )
        if result_id != expected_result_id or result_sha256 != expected_result_sha256:
            _fail()
        searched_documents = [str(item) for item in payload.get("searched_document_ids", [])]
        searched_spans = [str(item) for item in payload.get("searched_span_ids", [])]
        searched_scope_sha256 = str(payload.get("searched_scope_sha256") or "")
        gap_finding = payload.get("gap_finding")
        outputs = list(
            factual_outputs if factual_outputs is not None else payload.get("factual_outputs", [])
        )
        if (
            not searched_documents
            or not searched_spans
            or not searched_scope_sha256
            or (outcome == "EVIDENCE_FOUND") != bool(outputs)
            or (outcome == "EVIDENCE_NOT_FOUND") != bool(gap_finding)
        ):
            _fail()
        self._connection.execute(
            text(
                """
                INSERT INTO methodology_extraction_results (
                    result_id, tenant_id, deal_id, run_id, task_record_id,
                    attempt_id, task_input_sha256, outcome, result_payload,
                    result_sha256, searched_document_ids, searched_span_ids,
                    searched_scope_sha256, provider_provenance_sha256, gap_finding
                ) VALUES (
                    CAST(:result_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                    CAST(:task_record_id AS uuid), CAST(:attempt_id AS uuid),
                    :task_input_sha256, :outcome, CAST(:result_payload AS jsonb),
                    :result_sha256, CAST(:searched_document_ids AS uuid[]),
                    CAST(:searched_span_ids AS uuid[]), :searched_scope_sha256,
                    :provider_provenance_sha256, CAST(:gap_finding AS jsonb)
                )
                """
            ),
            {
                "result_id": result_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "task_record_id": attempt["task_record_id"],
                "attempt_id": attempt["attempt_id"],
                "task_input_sha256": attempt["task_input_sha256"],
                "outcome": outcome,
                "result_payload": _canonical_json(payload),
                "result_sha256": result_sha256,
                "searched_document_ids": searched_documents,
                "searched_span_ids": searched_spans,
                "searched_scope_sha256": searched_scope_sha256,
                "provider_provenance_sha256": provider_provenance_sha256,
                "gap_finding": _canonical_json(gap_finding) if gap_finding else None,
            },
        )
        for ordinal, output in enumerate(outputs, start=1):
            output_payload = _as_payload(output)
            typed_sha256 = _sha256(output_payload)
            factual_output_id = str(output_payload.get("factual_output_id") or "")
            try:
                factual_output_id = str(uuid.UUID(factual_output_id))
            except (TypeError, ValueError, AttributeError):
                _fail()
            spans = [str(item) for item in output_payload.get("accepted_source_span_ids", [])]
            locators = [str(item) for item in output_payload.get("accepted_locator_sha256s", [])]
            if not spans or len(spans) != len(locators):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            factual_record_id = str(
                uuid.uuid5(
                    METHODOLOGY_FACTUAL_RECORD_NAMESPACE,
                    f"{result_id}:{factual_output_id}:{typed_sha256}",
                )
            )
            self._connection.execute(
                text(
                    """
                    INSERT INTO methodology_result_factual_outputs (
                        factual_output_record_id, tenant_id, deal_id, run_id,
                        result_id, task_record_id, output_ordinal,
                        factual_output_id, typed_output_payload,
                        typed_output_sha256, accepted_source_span_ids,
                        accepted_locator_sha256s, accepted_locator_set_sha256
                    ) VALUES (
                        CAST(:record_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                        CAST(:result_id AS uuid), CAST(:task_record_id AS uuid),
                        :ordinal, CAST(:factual_output_id AS uuid),
                        CAST(:payload AS jsonb), :payload_sha256,
                        CAST(:span_ids AS uuid[]), CAST(:locator_hashes AS text[]),
                        :locator_set_sha256
                    )
                    """
                ),
                {
                    "record_id": factual_record_id,
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "result_id": result_id,
                    "task_record_id": attempt["task_record_id"],
                    "ordinal": ordinal,
                    "factual_output_id": factual_output_id,
                    "payload": _canonical_json(output_payload),
                    "payload_sha256": typed_sha256,
                    "span_ids": spans,
                    "locator_hashes": locators,
                    "locator_set_sha256": _sha256(sorted(zip(spans, locators, strict=True))),
                },
            )
        return self._load_result(result_id=result_id)

    def complete_execution_step_exact_set(
        self,
        *,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        expected_task_ids: Iterable[str],
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> dict[str, Any]:
        """Stage Step-7 completion only after the selected task equation is proven.

        The caller passes this returned summary to the generic orchestrator
        completion boundary in the same transaction.  This repository never
        performs an independent RunStep state transition.
        """
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self._lock_running_canonical_step7(run_id=run_id, run_step_id=run_step_id)
        expected_values = tuple(str(item) for item in expected_task_ids)
        expected = set(expected_values)
        if len(expected) != len(expected_values):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        rows = self._connection.execute(
            text(
                """
                SELECT t.extraction_task_id, t.coverage_record_id,
                       t.methodology_question_record_id,
                       a.status AS attempt_status, a.transport_kind, a.real_call,
                       r.result_id, r.outcome
                FROM methodology_extraction_tasks t
                LEFT JOIN methodology_extraction_attempts a
                  ON a.tenant_id = t.tenant_id AND a.deal_id = t.deal_id
                 AND a.run_id = t.run_id
                 AND a.task_record_id = t.task_record_id
                 AND a.task_input_sha256 = t.task_input_sha256
                LEFT JOIN methodology_extraction_results r
                  ON r.tenant_id = a.tenant_id AND r.deal_id = a.deal_id
                 AND r.run_id = a.run_id AND r.attempt_id = a.attempt_id
                 AND r.task_record_id = a.task_record_id
                 AND r.task_input_sha256 = a.task_input_sha256
                WHERE t.tenant_id = CAST(:tenant_id AS uuid)
                  AND t.deal_id = CAST(:deal_id AS uuid)
                  AND t.run_id = CAST(:run_id AS uuid)
                ORDER BY t.extraction_task_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        planned = {str(row.extraction_task_id) for row in rows}
        terminal = {
            str(row.extraction_task_id)
            for row in rows
            if row.attempt_status == "SUCCEEDED"
            and row.transport_kind == "LIVE"
            and row.real_call is True
            and row.result_id is not None
            and row.outcome in {"EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"}
        }
        coverage_rows = self._lock_coverage_rows(deal_id=deal_id, run_id=run_id)
        coverage_questions = {str(row.methodology_question_record_id) for row in coverage_rows}
        planned_questions = {str(row.methodology_question_record_id) for row in rows}
        if (
            not expected
            or len(rows) != len(expected)
            or planned != expected
            or terminal != expected
            or len(coverage_questions) != 100
            or planned_questions != coverage_questions
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        try:
            projection = project_step7_coverage(
                [_row_dict(row) for row in rows], expected_coverage_count=100
            )
        except MethodologyCoverageProjectionError:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        self._transition_coverage_exact_set(
            deal_id=deal_id,
            run_id=run_id,
            locked_rows=coverage_rows,
            projection=projection,
            allowed_initial_status="not_started",
        )
        summary = {
            "status": "COMPLETED",
            "task_count": len(expected),
            "task_set_sha256": _sha256(sorted(expected)),
        }
        return summary

    def finalize_canonical_coverage_exact_set(
        self,
        *,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> tuple[CoverageProjection, ...]:
        """Finalize Step-10 coverage from the exact committed canonical graph."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        locked_rows = self._lock_coverage_rows(deal_id=deal_id, run_id=run_id)
        planned_rows = self._read_projection_planned_rows(deal_id=deal_id, run_id=run_id)
        terminal_rows = self.load_terminal_execution_records(deal_id=deal_id, run_id=run_id)
        self._require_projection_source_equation(
            coverage_rows=locked_rows,
            planned_rows=planned_rows,
            terminal_rows=terminal_rows,
        )
        snapshot = self.load_canonical_materialization_snapshot(deal_id=deal_id, run_id=run_id)
        required_calculations = tuple(
            str(row.methodology_question_record_id)
            for row in locked_rows
            if _question_requires_calculation(row.question_payload)
        )
        try:
            predecessor = project_step7_coverage(terminal_rows, expected_coverage_count=100)
            projection = project_step10_coverage(
                terminal_rows,
                factual_outputs=snapshot.factual_outputs,
                claims=snapshot.claims,
                evidence_items=snapshot.evidence_items,
                sanads=snapshot.sanads,
                defects=snapshot.defects,
                required_calculation_question_ids=required_calculations,
                expected_coverage_count=100,
            )
        except MethodologyCoverageProjectionError:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        self._transition_coverage_exact_set(
            deal_id=deal_id,
            run_id=run_id,
            locked_rows=locked_rows,
            projection=projection,
            allowed_initial_projection=predecessor,
        )
        running_step = self._connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM run_steps
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND step_id = CAST(:run_step_id AS uuid)
                      AND status = 'RUNNING'
                )
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "run_id": run_id,
                "run_step_id": run_step_id,
            },
        ).scalar_one()
        if running_step is not True:
            _fail(WORKER_LEASE_FENCED)
        return projection

    def require_coverage_projection(
        self,
        *,
        deal_id: str,
        run_id: str,
        stage: str,
    ) -> tuple[CoverageProjection, ...]:
        """Recompute and require the exact committed Step-7 or Step-10 state."""
        if stage not in {"step7", "step10"}:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        coverage_rows = self._read_coverage_rows(deal_id=deal_id, run_id=run_id)
        planned_rows = self._read_projection_planned_rows(deal_id=deal_id, run_id=run_id)
        terminal_rows = self.load_terminal_execution_records(deal_id=deal_id, run_id=run_id)
        self._require_projection_source_equation(
            coverage_rows=coverage_rows,
            planned_rows=planned_rows,
            terminal_rows=terminal_rows,
        )
        try:
            projection = project_step7_coverage(terminal_rows, expected_coverage_count=100)
            if stage == "step10":
                snapshot = self.load_canonical_materialization_snapshot(
                    deal_id=deal_id, run_id=run_id
                )
                required_calculations = tuple(
                    str(row.methodology_question_record_id)
                    for row in coverage_rows
                    if _question_requires_calculation(row.question_payload)
                )
                projection = project_step10_coverage(
                    terminal_rows,
                    factual_outputs=snapshot.factual_outputs,
                    claims=snapshot.claims,
                    evidence_items=snapshot.evidence_items,
                    sanads=snapshot.sanads,
                    defects=snapshot.defects,
                    required_calculation_question_ids=required_calculations,
                    expected_coverage_count=100,
                )
        except MethodologyCoverageProjectionError:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        expected = {item.coverage_record_id: item for item in projection}
        if len(expected) != 100 or any(
            str(row.coverage_record_id) not in expected
            or not _coverage_row_matches_projection(row, expected[str(row.coverage_record_id)])
            for row in coverage_rows
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        return projection

    def _require_projection_source_equation(
        self,
        *,
        coverage_rows: Sequence[Any],
        planned_rows: Sequence[Mapping[str, Any]],
        terminal_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        """Require an exact planned-task = terminal-result projection source."""
        coverage_by_id = {str(row.coverage_record_id): row for row in coverage_rows}
        question_to_coverage = {
            str(row.methodology_question_record_id): str(row.coverage_record_id)
            for row in coverage_rows
        }
        if (
            len(coverage_rows) != 100
            or len(coverage_by_id) != 100
            or len(question_to_coverage) != 100
            or not planned_rows
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

        planned_by_task: dict[str, Mapping[str, Any]] = {}
        planned_extraction_ids: set[str] = set()
        planned_coverage_ids: set[str] = set()
        planned_question_ids: set[str] = set()
        for row in planned_rows:
            task_record_id = str(row.get("task_record_id"))
            extraction_task_id_value = row.get("extraction_task_id")
            extraction_task_id = (
                extraction_task_id_value.strip()
                if isinstance(extraction_task_id_value, str)
                else ""
            )
            coverage_record_id = str(row.get("coverage_record_id"))
            question_record_id = str(row.get("methodology_question_record_id"))
            if (
                task_record_id in planned_by_task
                or not extraction_task_id
                or extraction_task_id in planned_extraction_ids
                or row.get("status") != "READY"
                or coverage_record_id not in coverage_by_id
                or question_to_coverage.get(question_record_id) != coverage_record_id
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            planned_by_task[task_record_id] = row
            planned_extraction_ids.add(extraction_task_id)
            planned_coverage_ids.add(coverage_record_id)
            planned_question_ids.add(question_record_id)

        if planned_coverage_ids != set(coverage_by_id) or planned_question_ids != set(
            question_to_coverage
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

        terminal_task_ids: set[str] = set()
        for row in terminal_rows:
            task_record_id = str(row.get("task_record_id"))
            planned = planned_by_task.get(task_record_id)
            if (
                planned is None
                or task_record_id in terminal_task_ids
                or row.get("status") != "SUCCEEDED"
                or row.get("transport_kind") != "LIVE"
                or row.get("real_call") is not True
                or row.get("outcome") not in {"EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"}
                or str(row.get("extraction_task_id")) != str(planned.get("extraction_task_id"))
                or str(row.get("coverage_record_id")) != str(planned.get("coverage_record_id"))
                or str(row.get("methodology_question_record_id"))
                != str(planned.get("methodology_question_record_id"))
                or str(row.get("selection_id")) != str(planned.get("selection_id"))
                or str(row.get("methodology_version_record_id"))
                != str(planned.get("methodology_version_record_id"))
                or str(row.get("task_input_sha256")) != str(planned.get("task_input_sha256"))
            ):
                _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
            terminal_task_ids.add(task_record_id)

        if terminal_task_ids != set(planned_by_task) or len(terminal_rows) != len(planned_rows):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

    def _read_projection_planned_rows(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            text(
                """
                SELECT task_record_id, selection_id, coverage_record_id,
                       methodology_version_record_id,
                       methodology_question_record_id, extraction_task_id,
                       task_input_sha256, status
                FROM methodology_extraction_tasks
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                ORDER BY task_record_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def _lock_coverage_rows(self, *, deal_id: str, run_id: str) -> list[Any]:
        return list(
            self._connection.execute(
                text(
                    """
                    SELECT c.coverage_record_id, c.methodology_question_record_id,
                           c.status, c.reason_code, c.evidence_links, c.answer,
                           c.conflict_ids, c.defect_ids, c.updated_at,
                           q.question_payload
                    FROM methodology_coverage_records c
                    JOIN methodology_questions q
                      ON q.tenant_id = c.tenant_id
                     AND q.methodology_question_record_id =
                         c.methodology_question_record_id
                    WHERE c.tenant_id = CAST(:tenant_id AS uuid)
                      AND c.deal_id = CAST(:deal_id AS uuid)
                      AND c.run_id = CAST(:run_id AS uuid)
                    ORDER BY c.coverage_record_id
                    FOR UPDATE OF c
                    """
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            ).fetchall()
        )

    def _read_coverage_rows(self, *, deal_id: str, run_id: str) -> list[Any]:
        return list(
            self._connection.execute(
                text(
                    """
                    SELECT c.coverage_record_id, c.methodology_question_record_id,
                           c.status, c.reason_code, c.evidence_links, c.answer,
                           c.conflict_ids, c.defect_ids, c.updated_at,
                           q.question_payload
                    FROM methodology_coverage_records c
                    JOIN methodology_questions q
                      ON q.tenant_id = c.tenant_id
                     AND q.methodology_question_record_id =
                         c.methodology_question_record_id
                    WHERE c.tenant_id = CAST(:tenant_id AS uuid)
                      AND c.deal_id = CAST(:deal_id AS uuid)
                      AND c.run_id = CAST(:run_id AS uuid)
                    ORDER BY c.coverage_record_id
                    """
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            ).fetchall()
        )

    def _transition_coverage_exact_set(
        self,
        *,
        deal_id: str,
        run_id: str,
        locked_rows: Sequence[Any],
        projection: Sequence[CoverageProjection],
        allowed_initial_status: str | None = None,
        allowed_initial_projection: Sequence[CoverageProjection] | None = None,
    ) -> None:
        if len(locked_rows) != 100 or len(projection) != 100:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        current = {str(row.coverage_record_id): row for row in locked_rows}
        expected = {item.coverage_record_id: item for item in projection}
        if set(current) != set(expected):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        exact_replay = all(
            _coverage_row_matches_projection(current[coverage_id], item)
            for coverage_id, item in expected.items()
        )
        if exact_replay:
            return
        if (allowed_initial_status is None) == (allowed_initial_projection is None):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        if allowed_initial_status is not None:
            valid_predecessor = all(
                _coverage_row_is_pristine(current[coverage_id], allowed_initial_status)
                for coverage_id in expected
            )
        else:
            predecessors = {
                item.coverage_record_id: item for item in (allowed_initial_projection or ())
            }
            valid_predecessor = set(predecessors) == set(current) and all(
                _coverage_row_matches_projection(current[coverage_id], item)
                for coverage_id, item in predecessors.items()
            )
        if not valid_predecessor:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        updates = [
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "coverage_record_id": item.coverage_record_id,
                "question_record_id": item.methodology_question_record_id,
                "status": item.status,
                "reason_code": item.reason_code,
                "evidence_links": _canonical_json(item.evidence_payload()),
                "answer": (
                    None
                    if item.answer_payload() is None
                    else _canonical_json(item.answer_payload())
                ),
                "conflict_ids": list(item.conflict_ids),
                "defect_ids": list(item.defect_ids),
            }
            for item in projection
        ]
        result = self._connection.execute(
            text(
                """
                UPDATE methodology_coverage_records
                SET status = :status,
                    reason_code = :reason_code,
                    evidence_links = CAST(:evidence_links AS jsonb),
                    answer = CAST(:answer AS jsonb),
                    conflict_ids = CAST(:conflict_ids AS text[]),
                    defect_ids = CAST(:defect_ids AS text[]),
                    updated_at = statement_timestamp()
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND coverage_record_id = :coverage_record_id
                  AND methodology_question_record_id = CAST(:question_record_id AS uuid)
                """
            ),
            updates,
        )
        if result.rowcount != 100:
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        reloaded = self._lock_coverage_rows(deal_id=deal_id, run_id=run_id)
        if any(
            not _coverage_row_matches_projection(row, expected[str(row.coverage_record_id)])
            for row in reloaded
        ):
            _fail(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)

    def load_terminal_results(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            text(
                """
                SELECT r.*, COALESCE(
                    jsonb_agg(f.typed_output_payload ORDER BY f.output_ordinal)
                        FILTER (WHERE f.factual_output_record_id IS NOT NULL),
                    '[]'::jsonb
                ) AS factual_outputs
                FROM methodology_extraction_results r
                JOIN methodology_extraction_attempts a
                  ON a.tenant_id = r.tenant_id AND a.deal_id = r.deal_id
                 AND a.run_id = r.run_id AND a.attempt_id = r.attempt_id
                 AND a.task_record_id = r.task_record_id
                 AND a.task_input_sha256 = r.task_input_sha256
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = a.tenant_id AND t.deal_id = a.deal_id
                 AND t.run_id = a.run_id AND t.task_record_id = a.task_record_id
                 AND t.task_input_sha256 = a.task_input_sha256
                LEFT JOIN methodology_result_factual_outputs f
                  ON f.tenant_id = r.tenant_id AND f.deal_id = r.deal_id
                 AND f.run_id = r.run_id AND f.result_id = r.result_id
                WHERE r.tenant_id = CAST(:tenant_id AS uuid)
                  AND r.deal_id = CAST(:deal_id AS uuid)
                  AND r.run_id = CAST(:run_id AS uuid)
                  AND a.status = 'SUCCEEDED'
                GROUP BY r.result_id
                ORDER BY r.task_record_id, r.result_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def load_terminal_execution_records(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        """Load committed terminal provenance needed for typed restart rehydration."""
        rows = self._connection.execute(
            text(
                """
                SELECT a.attempt_id, a.run_step_id, a.task_record_id,
                       a.task_input_sha256, a.lease_owner_id,
                       a.lease_fencing_token, a.status, a.provider, a.backend,
                       a.transport_kind, a.configured_model, a.observed_model,
                       a.prompt_id, a.prompt_version, a.prompt_content_sha256,
                       a.prompt_input_schema_sha256,
                       a.prompt_output_schema_sha256,
                       a.request_payload_sha256, a.prepared_call_sha256,
                       a.billable_payload_sha256,
                       a.billable_payload_utf8_bytes, a.provider_request_id,
                       a.provider_response_sha256, a.dispatched_call_sha256,
                       a.real_call, a.started_at, a.completed_at, a.latency_ms,
                       a.configured_max_tokens, a.configured_temperature,
                       a.configured_json_mode, a.input_tokens, a.output_tokens,
                       a.stop_reason, a.budget_policy_id, a.budget_component,
                       a.budget_reservation_id, a.price_catalog_version,
                       a.price_catalog_row_sha256,
                       a.input_usd_per_million_tokens,
                       a.output_usd_per_million_tokens, a.reserved_calls,
                       a.reserved_input_tokens, a.reserved_output_tokens,
                       a.reserved_embedding_tokens, a.reserved_usd,
                       a.budget_terminal_state, a.receipt_sha256,
                       r.result_id, r.outcome, r.result_payload, r.result_sha256,
                       r.searched_document_ids, r.searched_span_ids,
                       r.searched_scope_sha256, r.provider_provenance_sha256,
                       r.gap_finding, r.created_at AS result_created_at,
                       t.selection_id, t.coverage_record_id,
                       t.methodology_version_record_id, t.methodology_version_id,
                       t.methodology_question_record_id, t.methodology_question_id,
                       t.methodology_type, t.extraction_task_id, t.document_id,
                       t.document_classification_id,
                       t.document_classification_sha256,
                       t.candidate_span_set_sha256
                FROM methodology_extraction_attempts a
                JOIN methodology_extraction_results r
                  ON r.tenant_id = a.tenant_id AND r.deal_id = a.deal_id
                 AND r.run_id = a.run_id AND r.attempt_id = a.attempt_id
                 AND r.task_record_id = a.task_record_id
                 AND r.task_input_sha256 = a.task_input_sha256
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = a.tenant_id AND t.deal_id = a.deal_id
                 AND t.run_id = a.run_id AND t.task_record_id = a.task_record_id
                 AND t.task_input_sha256 = a.task_input_sha256
                WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                  AND a.deal_id = CAST(:deal_id AS uuid)
                  AND a.run_id = CAST(:run_id AS uuid)
                  AND a.status = 'SUCCEEDED'
                ORDER BY a.task_record_id, a.attempt_id, r.result_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def load_factual_output_records(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        """Load durable factual-output IDs and complete methodology lineage."""
        rows = self._connection.execute(
            text(
                """
                SELECT f.factual_output_record_id, f.result_id, f.task_record_id,
                       f.output_ordinal, f.factual_output_id,
                       f.typed_output_payload, f.typed_output_sha256,
                       f.accepted_source_span_ids, f.accepted_locator_sha256s,
                       f.accepted_locator_set_sha256, f.created_at,
                       t.selection_id, t.coverage_record_id,
                       t.methodology_version_record_id, t.methodology_version_id,
                       t.methodology_question_record_id, t.methodology_question_id,
                       t.methodology_type, t.extraction_task_id,
                       t.document_id, t.document_classification_id,
                       t.document_classification_sha256, t.task_input_sha256,
                       t.candidate_span_set_sha256
                FROM methodology_result_factual_outputs f
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                 AND t.run_id = f.run_id AND t.task_record_id = f.task_record_id
                WHERE f.tenant_id = CAST(:tenant_id AS uuid)
                  AND f.deal_id = CAST(:deal_id AS uuid)
                  AND f.run_id = CAST(:run_id AS uuid)
                ORDER BY f.task_record_id, f.output_ordinal,
                         f.factual_output_record_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def load_canonical_materialization_snapshot(
        self, *, deal_id: str, run_id: str
    ) -> DurableCanonicalMaterializationSnapshot:
        """Load canonical rows joined to typed links and methodology lineage."""
        params = {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id}
        claim_rows = self._connection.execute(
            text(
                """
                SELECT l.claim_link_id, l.result_id, l.factual_output_record_id,
                       l.selection_id AS link_selection_id,
                       l.coverage_record_id AS link_coverage_record_id,
                       l.methodology_version_record_id AS link_methodology_version_record_id,
                       l.methodology_question_record_id AS link_methodology_question_record_id,
                       l.canonical_key_sha256,
                       l.reused_existing_claim, f.task_record_id,
                       t.selection_id AS task_selection_id,
                       t.coverage_record_id AS task_coverage_record_id,
                       t.methodology_version_record_id AS task_methodology_version_record_id,
                       t.methodology_question_record_id AS task_methodology_question_record_id,
                       t.methodology_question_id, t.methodology_version_id,
                       t.methodology_type, t.document_id,
                       c.claim_id, c.claim_class, c.claim_text, c.predicate, c.value,
                       c.claim_grade, c.corroboration, c.claim_verdict,
                       c.claim_action, c.defect_ids, c.materiality, c.ic_bound,
                       c.normalization_rule_version, c.normalized_predicate,
                       c.normalized_subject, c.normalized_period,
                       c.normalized_unit, c.value_sha256,
                       c.created_at, c.updated_at
                FROM methodology_result_claim_links l
                JOIN methodology_result_factual_outputs f
                  ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                 AND f.run_id = l.run_id AND f.result_id = l.result_id
                 AND f.factual_output_record_id = l.factual_output_record_id
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                 AND t.run_id = f.run_id AND t.task_record_id = f.task_record_id
                JOIN claims c ON c.tenant_id = l.tenant_id
                 AND c.deal_id = l.deal_id AND c.claim_id = l.claim_id
                WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                  AND l.deal_id = CAST(:deal_id AS uuid)
                  AND l.run_id = CAST(:run_id AS uuid)
                ORDER BY f.task_record_id, l.factual_output_record_id, l.claim_link_id
                """
            ),
            params,
        ).fetchall()
        evidence_rows = self._connection.execute(
            text(
                """
                SELECT l.evidence_link_id, l.result_id,
                       l.factual_output_record_id, l.claim_link_id,
                       l.document_id, l.source_span_id, l.locator_sha256,
                       f.task_record_id, t.coverage_record_id,
                       t.methodology_question_record_id, t.methodology_question_id,
                       t.methodology_version_record_id, t.methodology_version_id,
                       t.methodology_type, t.selection_id,
                       e.evidence_id, e.claim_id,
                       cl.claim_id AS lineage_claim_id,
                       e.source_span_id AS canonical_source_span_id,
                       admitted.span_id AS admitted_source_span_id,
                       admitted.locator_sha256 AS admitted_locator_sha256,
                       e.source_grade, e.source_system, e.upstream_origin_id,
                       e.retrieval_timestamp, e.verification_status,
                       e.source_rank_subgrade, e.rationale,
                       e.created_at, e.updated_at
                FROM methodology_result_evidence_links l
                JOIN methodology_result_factual_outputs f
                  ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                 AND f.run_id = l.run_id AND f.result_id = l.result_id
                 AND f.factual_output_record_id = l.factual_output_record_id
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                 AND t.run_id = f.run_id AND t.task_record_id = f.task_record_id
                LEFT JOIN methodology_extraction_task_spans admitted
                  ON admitted.tenant_id = t.tenant_id
                 AND admitted.deal_id = t.deal_id
                 AND admitted.run_id = t.run_id
                 AND admitted.task_record_id = t.task_record_id
                 AND admitted.document_id = l.document_id
                 AND admitted.span_id = l.source_span_id
                JOIN evidence_items e ON e.tenant_id = l.tenant_id
                 AND e.deal_id = l.deal_id AND e.evidence_id = l.evidence_id
                JOIN methodology_result_claim_links cl
                  ON cl.tenant_id = l.tenant_id AND cl.deal_id = l.deal_id
                 AND cl.run_id = l.run_id AND cl.result_id = l.result_id
                 AND cl.factual_output_record_id = l.factual_output_record_id
                 AND cl.claim_link_id = l.claim_link_id
                WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                  AND l.deal_id = CAST(:deal_id AS uuid)
                  AND l.run_id = CAST(:run_id AS uuid)
                ORDER BY f.task_record_id, l.factual_output_record_id,
                         l.evidence_link_id
                """
            ),
            params,
        ).fetchall()
        sanad_rows = self._connection.execute(
            text(
                """
                SELECT l.sanad_link_id, l.result_id,
                       l.factual_output_record_id, l.claim_link_id,
                       l.primary_evidence_id, f.task_record_id,
                       t.coverage_record_id, t.methodology_question_record_id,
                       t.methodology_question_id, t.methodology_version_record_id,
                       t.methodology_version_id, t.methodology_type,
                       t.selection_id, t.document_id,
                       s.sanad_id, s.claim_id,
                       s.primary_evidence_id AS canonical_primary_evidence_id,
                       cl.claim_id AS lineage_claim_id,
                       el.evidence_id AS lineage_primary_evidence_id,
                       s.corroborating_evidence_ids,
                       s.transmission_chain, s.computed,
                       s.created_at, s.updated_at
                FROM methodology_result_sanad_links l
                JOIN methodology_result_factual_outputs f
                  ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                 AND f.run_id = l.run_id AND f.result_id = l.result_id
                 AND f.factual_output_record_id = l.factual_output_record_id
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                 AND t.run_id = f.run_id AND t.task_record_id = f.task_record_id
                JOIN sanads s ON s.tenant_id = l.tenant_id
                 AND s.deal_id = l.deal_id AND s.sanad_id = l.sanad_id
                JOIN methodology_result_claim_links cl
                  ON cl.tenant_id = l.tenant_id AND cl.deal_id = l.deal_id
                 AND cl.run_id = l.run_id AND cl.result_id = l.result_id
                 AND cl.factual_output_record_id = l.factual_output_record_id
                 AND cl.claim_link_id = l.claim_link_id
                LEFT JOIN methodology_result_evidence_links el
                  ON el.tenant_id = l.tenant_id AND el.deal_id = l.deal_id
                 AND el.run_id = l.run_id AND el.result_id = l.result_id
                 AND el.factual_output_record_id = l.factual_output_record_id
                 AND el.claim_link_id = l.claim_link_id
                 AND el.evidence_id = l.primary_evidence_id
                WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                  AND l.deal_id = CAST(:deal_id AS uuid)
                  AND l.run_id = CAST(:run_id AS uuid)
                ORDER BY f.task_record_id, l.factual_output_record_id, l.sanad_link_id
                """
            ),
            params,
        ).fetchall()
        defect_rows = self._connection.execute(
            text(
                """
                SELECT l.defect_link_id, l.result_id,
                       l.factual_output_record_id, l.sanad_link_id,
                       f.task_record_id, t.coverage_record_id,
                       t.methodology_question_record_id, t.methodology_question_id,
                       t.methodology_version_record_id, t.methodology_version_id,
                       t.methodology_type, t.selection_id, t.document_id,
                       d.defect_id, d.claim_id,
                       s.claim_id AS sanad_claim_id,
                       cl.claim_id AS lineage_claim_id,
                       d.defect_type, d.severity,
                       d.description, d.cure_protocol, d.waived,
                       d.waived_by, d.waived_at, d.created_at, d.updated_at
                FROM methodology_result_defect_links l
                JOIN methodology_result_factual_outputs f
                  ON f.tenant_id = l.tenant_id AND f.deal_id = l.deal_id
                 AND f.run_id = l.run_id AND f.result_id = l.result_id
                 AND f.factual_output_record_id = l.factual_output_record_id
                JOIN methodology_extraction_tasks t
                  ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                 AND t.run_id = f.run_id AND t.task_record_id = f.task_record_id
                JOIN defects d ON d.tenant_id = l.tenant_id
                 AND d.deal_id = l.deal_id AND d.defect_id = l.defect_id
                JOIN methodology_result_sanad_links sl
                  ON sl.tenant_id = l.tenant_id AND sl.deal_id = l.deal_id
                 AND sl.run_id = l.run_id AND sl.result_id = l.result_id
                 AND sl.factual_output_record_id = l.factual_output_record_id
                 AND sl.sanad_link_id = l.sanad_link_id
                JOIN sanads s ON s.tenant_id = sl.tenant_id
                 AND s.deal_id = sl.deal_id AND s.sanad_id = sl.sanad_id
                JOIN methodology_result_claim_links cl
                  ON cl.tenant_id = sl.tenant_id AND cl.deal_id = sl.deal_id
                 AND cl.run_id = sl.run_id AND cl.result_id = sl.result_id
                 AND cl.factual_output_record_id = sl.factual_output_record_id
                 AND cl.claim_link_id = sl.claim_link_id
                WHERE l.tenant_id = CAST(:tenant_id AS uuid)
                  AND l.deal_id = CAST(:deal_id AS uuid)
                  AND l.run_id = CAST(:run_id AS uuid)
                ORDER BY f.task_record_id, l.factual_output_record_id, l.defect_link_id
                """
            ),
            params,
        ).fetchall()
        claim_payloads = [_row_dict(row) for row in claim_rows]
        for claim_payload in claim_payloads:
            _validated_canonical_claim_identity(
                tenant_id=self._tenant_id,
                deal_id=deal_id,
                payload=claim_payload,
            )
        evidence_payloads = [_row_dict(row) for row in evidence_rows]
        sanad_payloads = [_row_dict(row) for row in sanad_rows]
        defect_payloads = [_row_dict(row) for row in defect_rows]
        self._validate_canonical_snapshot_lineage(
            claims=claim_payloads,
            evidence_items=evidence_payloads,
            sanads=sanad_payloads,
            defects=defect_payloads,
        )
        return DurableCanonicalMaterializationSnapshot(
            factual_outputs=tuple(self.load_factual_output_records(deal_id=deal_id, run_id=run_id)),
            claims=tuple(claim_payloads),
            evidence_items=tuple(evidence_payloads),
            sanads=tuple(sanad_payloads),
            defects=tuple(defect_payloads),
        )

    @staticmethod
    def _validate_canonical_snapshot_lineage(
        *,
        claims: Sequence[dict[str, Any]],
        evidence_items: Sequence[dict[str, Any]],
        sanads: Sequence[dict[str, Any]],
        defects: Sequence[dict[str, Any]],
    ) -> None:
        """Re-prove typed link semantics during restart reconstruction."""
        _validate_run_global_defect_rows(defects)
        lineage_fields = (
            "selection_id",
            "coverage_record_id",
            "methodology_version_record_id",
            "methodology_question_record_id",
        )
        for row in claims:
            if any(
                str(row[f"link_{field}"]) != str(row[f"task_{field}"]) for field in lineage_fields
            ):
                _fail()
        for row in evidence_items:
            _validate_task6_evidence_required_fields(row)
            if (
                str(row["claim_id"]) != str(row.pop("lineage_claim_id"))
                or str(row["source_span_id"]) != str(row["canonical_source_span_id"])
                or str(row["source_span_id"]) != str(row["admitted_source_span_id"])
                or str(row["locator_sha256"]) != str(row["admitted_locator_sha256"])
            ):
                _fail()
        for row in sanads:
            if (
                str(row["claim_id"]) != str(row.pop("lineage_claim_id"))
                or str(row["primary_evidence_id"]) != str(row.pop("canonical_primary_evidence_id"))
                or str(row["primary_evidence_id"]) != str(row.pop("lineage_primary_evidence_id"))
            ):
                _fail()
        for row in defects:
            if str(row["claim_id"]) != str(row.pop("sanad_claim_id")) or str(
                row["claim_id"]
            ) != str(row.pop("lineage_claim_id")):
                _fail()

    def load_started_attempts(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        """Load crash-ambiguous admissions for fenced OUTCOME_UNKNOWN recovery."""
        rows = self._connection.execute(
            text(
                """
                SELECT * FROM methodology_extraction_attempts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'STARTED'
                ORDER BY task_record_id, attempt_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def load_non_success_attempts(self, *, deal_id: str, run_id: str) -> list[dict[str, Any]]:
        """Load terminal non-success attempts so restart can never redispatch them."""
        rows = self._connection.execute(
            text(
                """
                SELECT attempt_id, run_step_id, task_record_id,
                       task_input_sha256, prepared_call_sha256,
                       lease_owner_id, lease_fencing_token, status,
                       backend, transport_kind, real_call,
                       observed_model, provider_request_id,
                       provider_response_sha256, dispatched_call_sha256,
                       latency_ms, input_tokens, output_tokens, stop_reason,
                       budget_policy_id, budget_component,
                       budget_reservation_id, budget_terminal_state,
                       safe_failure_code, started_at, completed_at
                FROM methodology_extraction_attempts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status IN ('FAILED', 'OUTCOME_UNKNOWN')
                ORDER BY task_record_id, attempt_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def load_canonical_result_links(
        self, *, deal_id: str, run_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for kind, table, order in (
            ("claims", "methodology_result_claim_links", "factual_output_record_id"),
            ("evidence", "methodology_result_evidence_links", "evidence_id"),
            ("sanads", "methodology_result_sanad_links", "factual_output_record_id"),
            ("defects", "methodology_result_defect_links", "defect_id, defect_link_id"),
        ):
            rows = self._connection.execute(
                text(
                    f"SELECT * FROM {table} "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND deal_id = CAST(:deal_id AS uuid) "
                    "AND run_id = CAST(:run_id AS uuid) "
                    f"ORDER BY {order}"
                ),
                {"tenant_id": self._tenant_id, "deal_id": deal_id, "run_id": run_id},
            ).fetchall()
            payloads = [_row_dict(row) for row in rows]
            if kind == "defects":
                _validate_run_global_defect_rows(payloads)
            result[kind] = payloads
        return result

    def upsert_canonical_claim(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        claim: object,
    ) -> tuple[dict[str, Any], bool]:
        """Conflict-safely converge an exact normalized claim and reload its winner."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payload = _as_payload(claim)
        required = {
            "claim_id",
            "claim_class",
            "claim_text",
            "predicate",
            "value",
            "normalization_rule_version",
            "normalized_predicate",
            "normalized_subject",
            "normalized_period",
            "normalized_unit",
            "value_sha256",
            "canonical_key_sha256",
        }
        if not required.issubset(payload):
            _fail()
        identity, canonical_value = _validated_canonical_claim_identity(
            tenant_id=self._tenant_id,
            deal_id=deal_id,
            payload=payload,
        )
        payload["value"] = canonical_value
        params = {
            **payload,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "value": _canonical_json(canonical_value),
            "corroboration": _canonical_json(
                payload.get("corroboration", {"level": "AHAD", "independent_chain_count": 1})
            ),
            "defect_ids": _canonical_json(payload.get("defect_ids", [])),
            "claim_grade": payload.get("claim_grade", "D"),
            "claim_verdict": payload.get("claim_verdict", "UNVERIFIED"),
            "claim_action": payload.get("claim_action", "VERIFY"),
            "materiality": payload.get("materiality", "MEDIUM"),
            "ic_bound": bool(payload.get("ic_bound", False)),
        }
        inserted = self._connection.execute(
            text(
                """
                INSERT INTO claims (
                    claim_id, tenant_id, deal_id, claim_class, claim_text,
                    predicate, value, claim_grade, corroboration, claim_verdict,
                    claim_action, defect_ids, materiality, ic_bound, created_at,
                    updated_at, normalization_rule_version,
                    normalized_predicate, normalized_subject, normalized_period,
                    normalized_unit, value_sha256
                ) VALUES (
                    CAST(:claim_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), :claim_class, :claim_text,
                    :predicate, CAST(:value AS jsonb), :claim_grade,
                    CAST(:corroboration AS jsonb), :claim_verdict, :claim_action,
                    CAST(:defect_ids AS jsonb), :materiality, :ic_bound,
                    now(), now(), :normalization_rule_version,
                    :normalized_predicate, :normalized_subject,
                    :normalized_period, :normalized_unit, :value_sha256
                ) ON CONFLICT (
                    tenant_id, deal_id, normalized_predicate, normalized_subject,
                    normalized_period, normalized_unit, value_sha256
                ) WHERE normalization_rule_version IS NOT NULL
                  AND normalized_predicate IS NOT NULL
                  AND normalized_subject IS NOT NULL
                  AND normalized_period IS NOT NULL
                  AND normalized_unit IS NOT NULL
                  AND value_sha256 IS NOT NULL
                DO NOTHING RETURNING claim_id
                """
            ),
            params,
        ).scalar_one_or_none()
        row = self._connection.execute(
            text(
                """
                SELECT * FROM claims
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND deal_id = CAST(:deal_id AS uuid)
                  AND normalized_predicate = :normalized_predicate
                  AND normalized_subject = :normalized_subject
                  AND normalized_period = :normalized_period
                  AND normalized_unit = :normalized_unit
                  AND value_sha256 = :value_sha256
                FOR SHARE
                """
            ),
            params,
        ).one_or_none()
        if row is None:
            _fail()
        winner = _row_dict(row)
        winner_identity, winner_value = _validated_canonical_claim_identity(
            tenant_id=self._tenant_id,
            deal_id=deal_id,
            payload={**winner, "canonical_key_sha256": identity.canonical_key_sha256},
        )
        if (
            winner_identity != identity
            or winner["normalization_rule_version"] != payload["normalization_rule_version"]
            or winner["claim_class"] != payload["claim_class"]
            or winner_value != canonical_value
            or any(
                winner[field] != payload[field]
                for field in (
                    "normalized_predicate",
                    "normalized_subject",
                    "normalized_period",
                    "normalized_unit",
                    "value_sha256",
                )
            )
        ):
            _fail()
        return winner, inserted is None

    def insert_canonical_evidence_item(
        self,
        *,
        deal_id: str,
        run_id: str,
        task_record_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        evidence_item: object,
        claim_id: str,
    ) -> dict[str, Any]:
        """Insert one fully-provenanced EvidenceItem after a live span recheck."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        self.validate_live_source_span_identity(task_record_id=task_record_id)
        payload = _as_payload(evidence_item)
        required = {
            "evidence_id",
            "source_span_id",
            "source_system",
            "upstream_origin_id",
            "retrieval_timestamp",
            "verification_status",
            "source_grade",
            "rationale",
            "updated_at",
        }
        if not required.issubset(payload) or any(payload[key] is None for key in required):
            _fail()
        _validate_task6_evidence_required_fields(payload)
        row = self._connection.execute(
            text(
                """
                INSERT INTO evidence_items (
                    evidence_id, tenant_id, deal_id, claim_id, source_span_id,
                    source_grade, source_system, upstream_origin_id,
                    retrieval_timestamp, verification_status,
                    source_rank_subgrade, rationale, created_at, updated_at
                ) VALUES (
                    CAST(:evidence_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                    CAST(:source_span_id AS uuid), :source_grade, :source_system,
                    :upstream_origin_id, :retrieval_timestamp,
                    :verification_status, :source_rank_subgrade,
                    CAST(:rationale AS jsonb), COALESCE(:created_at, now()),
                    :updated_at
                ) RETURNING *
                """
            ),
            {
                **payload,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "claim_id": claim_id,
                "verification_status": str(_value(payload["verification_status"])),
                "source_grade": str(_value(payload["source_grade"])),
                "source_rank_subgrade": (
                    str(_value(payload["source_rank_subgrade"]))
                    if payload.get("source_rank_subgrade") is not None
                    else None
                ),
                "rationale": _canonical_json(payload["rationale"]),
                "created_at": payload.get("created_at"),
            },
        ).one()
        return _row_dict(row)

    def insert_canonical_sanad(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        sanad: object,
    ) -> dict[str, Any]:
        """Insert a same-scope Sanad whose primary EvidenceItem is a UUID FK."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payload = _as_payload(sanad)
        if not {"sanad_id", "claim_id", "primary_evidence_id"}.issubset(payload):
            _fail()
        row = self._connection.execute(
            text(
                """
                INSERT INTO sanads (
                    sanad_id, tenant_id, claim_id, deal_id,
                    primary_evidence_id, corroborating_evidence_ids,
                    transmission_chain, computed, created_at, updated_at
                ) VALUES (
                    CAST(:sanad_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:claim_id AS uuid), CAST(:deal_id AS uuid),
                    CAST(:primary_evidence_id AS uuid),
                    CAST(:corroborating_evidence_ids AS jsonb),
                    CAST(:transmission_chain AS jsonb), CAST(:computed AS jsonb),
                    COALESCE(:created_at, now()), COALESCE(:updated_at, now())
                ) RETURNING *
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "sanad_id": payload["sanad_id"],
                "claim_id": payload["claim_id"],
                "primary_evidence_id": payload["primary_evidence_id"],
                "corroborating_evidence_ids": _canonical_json(
                    payload.get("corroborating_evidence_ids", [])
                ),
                "transmission_chain": _canonical_json(payload.get("transmission_chain", [])),
                "computed": _canonical_json(payload.get("computed", {})),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
            },
        ).one()
        return _row_dict(row)

    def insert_canonical_defect(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        defect: object,
    ) -> dict[str, Any]:
        """Insert one governed same-scope defect for normalized link persistence."""
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payload = _as_payload(defect)
        if not {"defect_id", "claim_id", "defect_type", "severity"}.issubset(payload):
            _fail()
        row = self._connection.execute(
            text(
                """
                INSERT INTO defects (
                    defect_id, tenant_id, deal_id, claim_id, defect_type,
                    severity, description, cure_protocol, waived, status,
                    created_at, updated_at
                ) VALUES (
                    CAST(:defect_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                    :defect_type, :severity, :description, :cure_protocol,
                    :waived, :status, COALESCE(:created_at, now()),
                    COALESCE(:updated_at, now())
                ) RETURNING *
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "defect_id": payload["defect_id"],
                "claim_id": payload["claim_id"],
                "defect_type": str(_value(payload["defect_type"])),
                "severity": str(_value(payload["severity"])),
                "description": payload.get("description"),
                "cure_protocol": payload.get("cure_protocol"),
                "waived": bool(payload.get("waived", False)),
                "status": str(_value(payload.get("status", "OPEN"))),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
            },
        ).one()
        return _row_dict(row)

    def persist_claim_links(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        records: Sequence[object],
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> None:
        try:
            typed_records = tuple(MethodologyResultClaimLink.model_validate(row) for row in records)
        except (TypeError, ValueError):
            _fail()
        if not typed_records:
            _fail()
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(record.claim_link_id for record in typed_records),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except ValueError:
            _fail()
        for record in typed_records:
            if (
                record.tenant_id != self._tenant_id
                or record.deal_id != deal_id
                or record.run_id != run_id
            ):
                _fail()
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payloads: list[dict[str, Any]] = []
        for record in typed_records:
            claim_row = self._connection.execute(
                text(
                    """
                    SELECT claim_id, normalization_rule_version,
                           normalized_predicate, normalized_subject,
                           normalized_period, normalized_unit,
                           value, value_sha256
                    FROM claims
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND claim_id = CAST(:claim_id AS uuid)
                    FOR SHARE
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "claim_id": record.claim_id,
                },
            ).one_or_none()
            if claim_row is None:
                _fail()
            _validated_canonical_claim_identity(
                tenant_id=self._tenant_id,
                deal_id=deal_id,
                payload={
                    **_row_dict(claim_row),
                    "canonical_key_sha256": record.canonical_key_sha256,
                },
            )
            lineage_row = self._connection.execute(
                text(
                    """
                    SELECT f.factual_output_id, t.selection_id, t.coverage_record_id,
                           t.methodology_version_record_id,
                           t.methodology_question_record_id
                    FROM methodology_result_factual_outputs AS f
                    JOIN methodology_extraction_tasks AS t
                      ON t.tenant_id = f.tenant_id AND t.deal_id = f.deal_id
                     AND t.run_id = f.run_id
                     AND t.task_record_id = f.task_record_id
                    WHERE f.tenant_id = CAST(:tenant_id AS uuid)
                      AND f.deal_id = CAST(:deal_id AS uuid)
                      AND f.run_id = CAST(:run_id AS uuid)
                      AND f.result_id = CAST(:result_id AS uuid)
                      AND f.factual_output_record_id =
                          CAST(:factual_output_record_id AS uuid)
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "result_id": record.result_id,
                    "factual_output_record_id": record.factual_output_record_id,
                },
            ).one_or_none()
            if lineage_row is None:
                _fail()
            lineage = _row_dict(lineage_row)
            if record.factual_output_id != str(
                lineage["factual_output_id"]
            ) or record.coverage_record_id != str(lineage["coverage_record_id"]):
                _fail()
            derived_fields = {
                "selection_id": str(lineage["selection_id"]),
                "methodology_version_record_id": str(lineage["methodology_version_record_id"]),
                "methodology_question_record_id": str(lineage["methodology_question_record_id"]),
            }
            logical_record = record.model_dump(mode="json", exclude={"claim_link_id"})
            if record.claim_link_id != generate_methodology_result_link_id(
                table="methodology_result_claim_links",
                logical_record=logical_record,
                derived_fields=derived_fields,
            ):
                _fail()
            payload = record.model_dump(mode="json", exclude={"factual_output_id"})
            payload.update(derived_fields)
            payloads.append(payload)
        self._persist_exact_links(
            table="methodology_result_claim_links",
            deal_id=deal_id,
            run_id=run_id,
            records=payloads,
            run_output_object_ids=root_bindings,
        )

    def persist_evidence_links(
        self,
        *,
        deal_id: str,
        run_id: str,
        task_record_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        records: Sequence[object],
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> None:
        try:
            typed_records = tuple(
                MethodologyResultEvidenceLink.model_validate(row) for row in records
            )
        except (TypeError, ValueError):
            _fail()
        if not typed_records:
            _fail()
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(record.evidence_link_id for record in typed_records),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except ValueError:
            _fail()
        for record in typed_records:
            if (
                record.tenant_id != self._tenant_id
                or record.deal_id != deal_id
                or record.run_id != run_id
            ):
                _fail()
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payloads: list[dict[str, Any]] = []
        durable_task_record_ids: set[str] = set()
        for record in typed_records:
            admitted_row = self._connection.execute(
                text(
                    """
                    SELECT f.task_record_id, f.factual_output_id,
                           admitted.locator_sha256
                    FROM methodology_result_factual_outputs f
                    JOIN methodology_extraction_task_spans admitted
                      ON admitted.tenant_id = f.tenant_id
                     AND admitted.deal_id = f.deal_id
                     AND admitted.run_id = f.run_id
                     AND admitted.task_record_id = f.task_record_id
                     AND admitted.document_id = CAST(:document_id AS uuid)
                     AND admitted.span_id = CAST(:source_span_id AS uuid)
                    WHERE f.tenant_id = CAST(:tenant_id AS uuid)
                      AND f.deal_id = CAST(:deal_id AS uuid)
                      AND f.run_id = CAST(:run_id AS uuid)
                      AND f.result_id = CAST(:result_id AS uuid)
                      AND f.factual_output_record_id =
                          CAST(:factual_output_record_id AS uuid)
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "result_id": record.result_id,
                    "factual_output_record_id": record.factual_output_record_id,
                    "document_id": record.document_id,
                    "source_span_id": record.source_span_id,
                },
            ).one_or_none()
            if admitted_row is None:
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            durable_task_record_id = str(admitted_row.task_record_id)
            if durable_task_record_id != str(task_record_id):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            if record.factual_output_id != str(admitted_row.factual_output_id):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            durable_locator_sha256 = str(admitted_row.locator_sha256)
            logical_record = record.model_dump(mode="json", exclude={"evidence_link_id"})
            if record.evidence_link_id != generate_methodology_result_link_id(
                table="methodology_result_evidence_links",
                logical_record=logical_record,
                derived_fields={"locator_sha256": durable_locator_sha256},
            ):
                _fail(METHODOLOGY_SOURCE_LOCATOR_REJECTED)
            payload = record.model_dump(mode="json", exclude={"factual_output_id"})
            payload["locator_sha256"] = durable_locator_sha256
            payloads.append(payload)
            durable_task_record_ids.add(durable_task_record_id)
        for durable_task_record_id in sorted(durable_task_record_ids):
            self.validate_live_source_span_identity(task_record_id=durable_task_record_id)
        self._persist_exact_links(
            table="methodology_result_evidence_links",
            deal_id=deal_id,
            run_id=run_id,
            records=payloads,
            run_output_object_ids=root_bindings,
        )

    def persist_sanad_links(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        records: Sequence[object],
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> None:
        try:
            typed_records = tuple(MethodologyResultSanadLink.model_validate(row) for row in records)
        except (TypeError, ValueError):
            _fail()
        if not typed_records:
            _fail()
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(record.sanad_link_id for record in typed_records),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except ValueError:
            _fail()
        for record in typed_records:
            if (
                record.tenant_id != self._tenant_id
                or record.deal_id != deal_id
                or record.run_id != run_id
            ):
                _fail()
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payloads: list[dict[str, Any]] = []
        for record in typed_records:
            durable_row = self._connection.execute(
                text(
                    """
                    SELECT f.factual_output_id, s.computed
                    FROM methodology_result_factual_outputs AS f
                    JOIN methodology_result_claim_links AS c
                      ON c.tenant_id = f.tenant_id AND c.deal_id = f.deal_id
                     AND c.run_id = f.run_id AND c.result_id = f.result_id
                     AND c.factual_output_record_id = f.factual_output_record_id
                    JOIN sanads AS s
                      ON s.tenant_id = c.tenant_id AND s.deal_id = c.deal_id
                     AND s.claim_id = c.claim_id
                     AND s.sanad_id = CAST(:sanad_id AS uuid)
                     AND s.primary_evidence_id = CAST(:primary_evidence_id AS uuid)
                    WHERE f.tenant_id = CAST(:tenant_id AS uuid)
                      AND f.deal_id = CAST(:deal_id AS uuid)
                      AND f.run_id = CAST(:run_id AS uuid)
                      AND f.result_id = CAST(:result_id AS uuid)
                      AND f.factual_output_record_id =
                          CAST(:factual_output_record_id AS uuid)
                      AND c.claim_link_id = CAST(:claim_link_id AS uuid)
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "result_id": record.result_id,
                    "factual_output_record_id": record.factual_output_record_id,
                    "claim_link_id": record.claim_link_id,
                    "sanad_id": record.sanad_id,
                    "primary_evidence_id": record.primary_evidence_id,
                },
            ).one_or_none()
            if durable_row is None or record.factual_output_id != str(
                durable_row.factual_output_id
            ):
                _fail()
            computed = _json_value(durable_row.computed)
            if not isinstance(computed, Mapping):
                _fail()
            durable_defect_ids = computed.get("defect_ids")
            if (
                str(computed.get("sanad_grade")) != record.grade
                or not isinstance(durable_defect_ids, list)
                or tuple(sorted(str(item) for item in durable_defect_ids)) != record.defect_ids
            ):
                _fail()
            logical_record = record.model_dump(mode="json", exclude={"sanad_link_id"})
            if record.sanad_link_id != generate_methodology_result_link_id(
                table="methodology_result_sanad_links",
                logical_record=logical_record,
            ):
                _fail()
            payloads.append(
                record.model_dump(
                    mode="json",
                    exclude={"factual_output_id", "grade", "defect_ids"},
                )
            )
        self._persist_exact_links(
            table="methodology_result_sanad_links",
            deal_id=deal_id,
            run_id=run_id,
            records=payloads,
            run_output_object_ids=root_bindings,
        )

    def persist_defect_links(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        records: Sequence[object],
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> None:
        try:
            typed_records = tuple(
                MethodologyResultDefectLink.model_validate(row) for row in records
            )
        except (TypeError, ValueError):
            _fail()
        if not typed_records:
            _fail()
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(record.defect_link_id for record in typed_records),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except ValueError:
            _fail()
        defect_ids: set[str] = set()
        for record in typed_records:
            if (
                record.tenant_id != self._tenant_id
                or record.deal_id != deal_id
                or record.run_id != run_id
                or record.defect_id in defect_ids
            ):
                _fail()
            defect_ids.add(record.defect_id)
        self._require_fence(
            deal_id=deal_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        payloads: list[dict[str, Any]] = []
        for record in typed_records:
            durable_row = self._connection.execute(
                text(
                    """
                    SELECT f.factual_output_id
                    FROM methodology_result_factual_outputs AS f
                    JOIN methodology_result_sanad_links AS s
                      ON s.tenant_id = f.tenant_id AND s.deal_id = f.deal_id
                     AND s.run_id = f.run_id AND s.result_id = f.result_id
                     AND s.factual_output_record_id = f.factual_output_record_id
                    JOIN sanads AS canonical_sanad
                      ON canonical_sanad.tenant_id = s.tenant_id
                     AND canonical_sanad.deal_id = s.deal_id
                     AND canonical_sanad.sanad_id = s.sanad_id
                    JOIN defects AS d
                      ON d.tenant_id = s.tenant_id AND d.deal_id = s.deal_id
                     AND d.claim_id = canonical_sanad.claim_id
                     AND d.defect_id = CAST(:defect_id AS uuid)
                    WHERE f.tenant_id = CAST(:tenant_id AS uuid)
                      AND f.deal_id = CAST(:deal_id AS uuid)
                      AND f.run_id = CAST(:run_id AS uuid)
                      AND f.result_id = CAST(:result_id AS uuid)
                      AND f.factual_output_record_id =
                          CAST(:factual_output_record_id AS uuid)
                      AND s.sanad_link_id = CAST(:sanad_link_id AS uuid)
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "result_id": record.result_id,
                    "factual_output_record_id": record.factual_output_record_id,
                    "sanad_link_id": record.sanad_link_id,
                    "defect_id": record.defect_id,
                },
            ).one_or_none()
            if durable_row is None:
                _fail()
            logical_record = record.model_dump(mode="json", exclude={"defect_link_id"})
            if record.defect_link_id != generate_methodology_result_link_id(
                table="methodology_result_defect_links",
                logical_record=logical_record,
            ):
                _fail()
            existing = self._connection.execute(
                text(
                    """
                    SELECT 1
                    FROM methodology_result_defect_links
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND defect_id = CAST(:defect_id AS uuid)
                    FOR SHARE
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "defect_id": record.defect_id,
                },
            ).scalar_one_or_none()
            if existing is not None:
                _fail()
            payloads.append(record.model_dump(mode="json"))
        self._persist_exact_links(
            table="methodology_result_defect_links",
            deal_id=deal_id,
            run_id=run_id,
            records=payloads,
            run_output_object_ids=root_bindings,
        )

    def _persist_exact_links(
        self,
        *,
        table: str,
        deal_id: str,
        run_id: str,
        records: Sequence[Mapping[str, Any]],
        run_output_object_ids: Mapping[str, str] | None = None,
    ) -> None:
        expected_columns = _METHODOLOGY_RESULT_LINK_COLUMNS.get(table)
        native_id_column = _METHODOLOGY_RESULT_LINK_ID_COLUMNS.get(table)
        if expected_columns is None or native_id_column is None or not records:
            _fail()
        try:
            root_bindings = validate_run_output_object_ids(
                stable_native_ids=(str(record[native_id_column]) for record in records),
                run_output_object_ids=run_output_object_ids,
                native_ids_are_uuids=True,
            )
        except (KeyError, ValueError):
            _fail()
        for raw in records:
            record = dict(raw)
            if set(record) != set(expected_columns):
                _fail()
            if (
                record["tenant_id"] != self._tenant_id
                or record["deal_id"] != deal_id
                or record["run_id"] != run_id
            ):
                _fail()
            columns = expected_columns
            if root_bindings is not None:
                columns = (*columns, "run_output_object_id")
                record["run_output_object_id"] = root_bindings[str(record[native_id_column])]
            placeholders = ", ".join(f":{column}" for column in columns)
            self._connection.execute(
                text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"),
                record,
            )

    def _load_attempt(self, *, attempt_id: str, for_update: bool = False) -> dict[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            text(
                "SELECT * FROM methodology_extraction_attempts "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND attempt_id = CAST(:attempt_id AS uuid)" + suffix
            ),
            {"tenant_id": self._tenant_id, "attempt_id": attempt_id},
        ).one_or_none()
        if row is None:
            _fail()
        return _row_dict(row)

    def _load_result(self, *, result_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            text(
                """
                SELECT * FROM methodology_extraction_results
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND result_id = CAST(:result_id AS uuid)
                """
            ),
            {"tenant_id": self._tenant_id, "result_id": result_id},
        ).one_or_none()
        if row is None:
            _fail()
        return _row_dict(row)

    def _fail_running_step(
        self,
        *,
        run_id: str,
        run_step_id: str,
        safe_failure_code: str,
    ) -> None:
        updated = self._connection.execute(
            text(
                """
                UPDATE run_steps
                SET status = 'FAILED', finished_at = now(),
                    error_code = :safe_failure_code,
                    error_message = 'Methodology extraction failed closed'
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND step_id = CAST(:run_step_id AS uuid)
                  AND step_name = :step_name
                  AND step_order = :step_order
                  AND status = 'RUNNING'
                RETURNING step_id
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "run_id": run_id,
                "run_step_id": run_step_id,
                "step_name": _STEP7_NAME.value,
                "step_order": _STEP7_ORDER,
                "safe_failure_code": safe_failure_code,
            },
        ).scalar_one_or_none()
        if updated is None:
            _fail(WORKER_LEASE_FENCED)


__all__ = [
    "DurableCanonicalMaterializationSnapshot",
    "DurableMethodologyExtractionTaskRecord",
    "DurableMethodologyTerminalizationProof",
    "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN",
    "METHODOLOGY_EXTRACTION_PERSISTENCE_REJECTED",
    "METHODOLOGY_SOURCE_LOCATOR_REJECTED",
    "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
    "MethodologyExtractionRepositoryError",
    "PostgresMethodologyExtractionRepository",
    "WORKER_LEASE_FENCED",
]
