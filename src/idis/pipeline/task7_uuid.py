"""Deterministic UUIDv5 identities for the reviewed Task 7 contract."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final
from uuid import UUID, uuid5

TASK7_UUID_COLLISION: Final = "TASK7_UUID_COLLISION"
TASK7_UUID_ROW_MISMATCH: Final = "TASK7_UUID_ROW_MISMATCH"
TASK7_UUID_STRICT_ADMISSION_REQUIRED: Final = "TASK7_UUID_STRICT_ADMISSION_REQUIRED"
TASK7_UUID_RECIPE_UNAVAILABLE: Final = "TASK7_UUID_RECIPE_UNAVAILABLE"
TASK7_UUID_NAME_TUPLE_INVALID: Final = "TASK7_UUID_NAME_TUPLE_INVALID"
TASK7_UUID_BINDING_INVALID: Final = "TASK7_UUID_BINDING_INVALID"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_FULL_STRICT_V21_CONTRACT_HASH: Final = (
    "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


class Task7UuidError(RuntimeError):
    """Stable fail-closed Task 7 identity error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Task7UuidNamespaceSpec:
    """One reviewed UUID namespace and its exact ordered name tuple."""

    id_kind: str
    namespace_uuid: UUID
    name_tuple_schema: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Task7DerivedUuid:
    """Deterministic identity plus the hash used as its UUIDv5 name."""

    id_kind: str
    name_hash: str
    value: UUID


@dataclass(frozen=True, slots=True)
class Task7UuidBinding:
    """Persisted row binding used to reject collisions and retry drift."""

    id_kind: str
    name_hash: str
    value: UUID
    row_sha256: str


TASK7_UUID_NAMESPACE_REGISTRY: Final[tuple[Task7UuidNamespaceSpec, ...]] = (
    Task7UuidNamespaceSpec(
        "step_id",
        UUID("d26b136c-a25e-53b7-acb4-84798eaec9d8"),
        (
            "run-step-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "step_name",
            "step_order",
        ),
    ),
    Task7UuidNamespaceSpec(
        "source_lineage_object_id",
        UUID("90a5de51-2753-5547-9375-ac5312629947"),
        (
            "source-lineage-object-v2",
            "tenant_id",
            "deal_id",
            "corpus_receipt_id",
            "object_kind",
            "native_scoped_id",
            "source_version",
            "source_disposition",
            "object_identity_sha256",
            "content_sha256",
            "corpus_receipt_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "parse_attempt_id",
        UUID("6f591e1c-c7b1-5f0f-8ec8-c93380c6f6b1"),
        (
            "document-parse-attempt-v1",
            "tenant_id",
            "deal_id",
            "document_id",
            "attempt_ordinal",
            "parser_name",
            "parser_version",
            "input_sha256",
            "receipt_sha256",
            "status",
        ),
    ),
    Task7UuidNamespaceSpec(
        "binding_id",
        UUID("bb31eeac-26b0-50a1-a3b0-978a7b3b4095"),
        (
            "run-input-binding-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "corpus_receipt_id",
            "source_lineage_object_id",
            "source_object_kind",
            "source_disposition",
            "binding_purpose",
            "selected_source_version",
            "selected_identity_sha256",
            "selected_content_sha256",
            "corpus_receipt_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "output_object_id",
        UUID("b3ef55a1-2eef-5712-836c-4870bc3ef6d4"),
        (
            "run-output-object-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "output_class",
            "output_kind",
            "native_identity_sha256",
            "native_output_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "step_output_id",
        UUID("1b789979-1603-5f0e-bb46-0a79a623a2b7"),
        (
            "run-step-output-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_hash",
            "step_id",
            "step_name",
            "subrole",
            "output_kind",
            "output_ordinal",
            "output_object_id",
        ),
    ),
    Task7UuidNamespaceSpec(
        "applicability_decision_id",
        UUID("642e7080-3b0d-54b9-a69b-c82df3fa6c7e"),
        (
            "run-applicability-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_hash",
            "decision_phase",
            "decision_kind",
            "subject_kind",
            "subject_key",
            "outcome",
            "upstream_type",
            "upstream_id",
            "deciding_step_output_id",
            "contract_rule_id",
            "contract_rule_version",
            "contract_rule_hash",
            "evidence_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "expected_edge_id",
        UUID("2b5951cb-9894-5a8e-9907-425f2a9d5bc2"),
        (
            "expected-edge-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "producer_type",
            "producer_id",
            "consumer_step",
            "consumer_subrole",
            "usage_kind",
            "decision_id_or_null",
            "rule_id",
            "rule_version",
            "rule_hash",
        ),
    ),
    Task7UuidNamespaceSpec(
        "receipt_id",
        UUID("8385d33d-8f2f-5bbf-bc31-308a4e994f6d"),
        (
            "consumer-receipt-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_hash",
            "expected_edge_id",
            "consumer_step_output_id",
            "consumer_output_object_id",
            "usage_evidence_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "future_input_reference_id",
        UUID("8caa2d0a-4588-40f1-96ba-60401462ae91"),
        (
            "future-input-reference-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "reference_table",
            "consumer_id",
            "consumer_output_object_id",
            "producer_type",
            "producer_id",
            "reference_kind",
            "reference_ordinal",
            "producer_identity_sha256",
            "producer_value_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "manifest_reference_id",
        UUID("5292cdb1-d81b-4a97-9d07-9af726fec352"),
        (
            "deliverable-manifest-reference-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "manifest_deliverable_id",
            "member_kind",
            "catalog_ordinal_or_null",
            "member_step_output_id",
            "member_output_object_id",
            "reference_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "identity_identifier_id",
        UUID("1a5be9e5-5cc5-5a16-8d3f-6f9fc4eeda53"),
        (
            "company-identity-identifier-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "identity_output_id",
            "identifier_kind",
            "identifier_hash",
            "verification_provenance_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "scoring_dimension_id",
        UUID("dc0541cd-f3de-5bf4-8b9a-09fd2d6647d6"),
        (
            "scoring-output-dimension-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "scoring_output_id",
            "dimension_ordinal",
            "dimension_key",
            "binary64(score)",
            "binary64(confidence)",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "generic_claim_link_id",
        UUID("a66eb885-f319-5498-87d4-a408d5364b59"),
        (
            "generic-extraction-claim-link-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "generic_output_id",
            "claim_ordinal",
            "claim_id",
            "canonical_key_sha256",
            "reused_existing_claim",
        ),
    ),
    Task7UuidNamespaceSpec(
        "generic_evidence_link_id",
        UUID("fbf8b180-1b76-56b8-bef4-e619548b13dc"),
        (
            "generic-extraction-evidence-link-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "generic_output_id",
            "evidence_ordinal",
            "generic_claim_link_id",
            "claim_id",
            "evidence_id",
            "document_id",
            "source_span_id",
            "locator_sha256",
        ),
    ),
    Task7UuidNamespaceSpec(
        "dashboard_root_link_id",
        UUID("58a9370f-69f9-5873-b6b7-508272aee80e"),
        (
            "methodology-truth-dashboard-root-link-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "dashboard_row_id",
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
        ),
    ),
    Task7UuidNamespaceSpec(
        "erasure_attempt_id",
        UUID("4520d8ed-e929-5486-821c-82f8901ae7fa"),
        (
            "task7-erasure-attempt-v1",
            "tenant_id",
            "deal_id",
            "request_id",
            "attempt_ordinal",
            "prior_status",
            "prior_event_id_or_null",
            "prior_event_hash_or_null",
        ),
    ),
    Task7UuidNamespaceSpec(
        "erasure_audit_event_id",
        UUID("dd76cee1-28d5-55f8-b21a-eb64bc2e4be4"),
        (
            "task7-erasure-audit-event-v1",
            "attempt_id",
            "event_type",
            "outcome",
        ),
    ),
)

_NAMESPACE_BY_KIND: Final[Mapping[str, Task7UuidNamespaceSpec]] = MappingProxyType(
    {row.id_kind: row for row in TASK7_UUID_NAMESPACE_REGISTRY}
)


def _normalize_json_value(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("mapping keys collide after NFC normalization")
            normalized[normalized_key] = _normalize_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    normalized = _normalize_json_value(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def derive_task7_uuid(
    *,
    id_kind: str,
    values: Mapping[str, object],
) -> Task7DerivedUuid:
    """Derive one reviewed Task 7 UUID from its exact ordered tuple."""

    try:
        recipe = _NAMESPACE_BY_KIND[id_kind]
    except KeyError as exc:
        raise Task7UuidError(
            TASK7_UUID_RECIPE_UNAVAILABLE,
            f"Task 7 UUID recipe is unavailable: {id_kind}",
        ) from exc

    try:
        name_tuple = [
            recipe.name_tuple_schema[0],
            *(values[field] for field in recipe.name_tuple_schema[1:]),
        ]
        name_hash = hashlib.sha256(_canonical_json_bytes(name_tuple)).hexdigest()
    except (KeyError, TypeError, ValueError) as exc:
        raise Task7UuidError(
            TASK7_UUID_NAME_TUPLE_INVALID,
            f"Task 7 UUID name tuple is invalid: {id_kind}",
        ) from exc

    return Task7DerivedUuid(
        id_kind=id_kind,
        name_hash=name_hash,
        value=uuid5(recipe.namespace_uuid, name_hash),
    )


def derive_step_id_for_admission(
    *,
    pipeline_contract_version: str,
    values: Mapping[str, object],
) -> Task7DerivedUuid:
    """Derive a step ID only for newly admitted full-strict-v21 runs."""

    if pipeline_contract_version != _FULL_STRICT_V21_VERSION:
        raise Task7UuidError(
            TASK7_UUID_STRICT_ADMISSION_REQUIRED,
            "deterministic step IDs require full-strict-v21 admission",
        )
    if (
        values.get("pipeline_contract_version") != _FULL_STRICT_V21_VERSION
        or values.get("pipeline_contract_hash") != _FULL_STRICT_V21_CONTRACT_HASH
    ):
        raise Task7UuidError(
            TASK7_UUID_STRICT_ADMISSION_REQUIRED,
            "step ID values are not bound to the reviewed full-strict-v21 contract",
        )
    return derive_task7_uuid(id_kind="step_id", values=values)


def preserve_legacy_step_ids(opaque_ids: Sequence[str]) -> tuple[str, ...]:
    """Preserve legacy step primary keys byte-for-byte without UUIDv5 claims."""

    return tuple(opaque_ids)


def validate_task7_uuid_bindings(
    bindings: Sequence[Task7UuidBinding],
) -> tuple[Task7UuidBinding, ...]:
    """Reject UUID collisions and same-identity retry row drift."""

    rows = tuple(bindings)
    by_identifier: dict[UUID, Task7UuidBinding] = {}
    by_name: dict[tuple[str, str], UUID] = {}
    for binding in rows:
        if (
            binding.id_kind not in _NAMESPACE_BY_KIND
            or _SHA256_RE.fullmatch(binding.name_hash) is None
            or _SHA256_RE.fullmatch(binding.row_sha256) is None
            or binding.value.version != 5
        ):
            raise Task7UuidError(
                TASK7_UUID_BINDING_INVALID,
                f"Task 7 UUID binding is malformed: {binding.id_kind}",
            )

        previous = by_identifier.get(binding.value)
        if previous is not None:
            if previous.id_kind != binding.id_kind or previous.name_hash != binding.name_hash:
                raise Task7UuidError(
                    TASK7_UUID_COLLISION,
                    "one Task 7 UUID is bound to different names",
                )
            if previous.row_sha256 != binding.row_sha256:
                raise Task7UuidError(
                    TASK7_UUID_ROW_MISMATCH,
                    "a retried Task 7 UUID is bound to different row bytes",
                )

        name_key = (binding.id_kind, binding.name_hash)
        previous_identifier = by_name.get(name_key)
        if previous_identifier is not None and previous_identifier != binding.value:
            raise Task7UuidError(
                TASK7_UUID_COLLISION,
                "one Task 7 UUID name is bound to different identifiers",
            )
        by_identifier[binding.value] = binding
        by_name[name_key] = binding.value
    return rows
