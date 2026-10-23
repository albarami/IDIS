"""Standalone checks for the inactive full-strict-v28 candidate UUID APIs."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterator, Sequence
from typing import overload
from uuid import UUID, uuid5

import pytest

from idis.pipeline import task7_uuid
from idis.pipeline.task7_uuid import (
    Task7DerivedUuid,
    Task7UuidBinding,
    Task7UuidNamespaceSpec,
)

_V28_VERSION = "full-strict-v28"
_V28_CONTRACT_HASH = "7f" * 32
_STAGE_ID_KIND = "stage_admission_binding_id"
_STAGE_NAMESPACE = UUID("591d30bc-4dce-479b-9164-5f4020c311ae")
_STEP_ID_NAMESPACE = UUID("d26b136c-a25e-53b7-acb4-84798eaec9d8")

_V20_VERSION = "full-strict-v20"
_V20_CONTRACT_HASH = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"

_VECTORS: dict[str, tuple[str, str]] = {
    "step_id": (
        "5efb21de5383224590edf96d1eea1edfebf59744a95f06d276bbc60ef245149b",
        "70b44d47-79ec-52eb-9027-732632072684",
    ),
    "source_lineage_object_id": (
        "b8b11d26e0552080670ff19d9f3ec4ddafc13fcf622a13d0fecefb88a7fe194e",
        "efa921c3-8b08-516e-999a-b5024674511e",
    ),
    "parse_attempt_id": (
        "2205c42c50a662a645d802b8bc263a138dd4ad15f79db3e4f50e192ba5d7c2a8",
        "01fd73d8-2cf1-5b08-95a8-facecc6e730c",
    ),
    "binding_id": (
        "fdff50673c88c37eb325ce6b96398d59aede755960b07774c53e3aabc37135ad",
        "6e4da7f8-daa7-5034-936b-1a8dd11c7926",
    ),
    "output_object_id": (
        "1307fb803aa67622484b95ae1c557efacd3f883111180aaf03d8b49a724bfe5d",
        "66f431b8-d69f-5dae-b235-92e3c71a7015",
    ),
    "step_output_id": (
        "498db2cdf1a11ecece598c469761b55cc7ce1c565b6cccaf0762c14be3f037d5",
        "5b8eaf2b-dd27-52eb-af67-21524587b253",
    ),
    "applicability_decision_id": (
        "167e7c6cc70956aae0ce3a8f8f3ad75361029af04eb9aa7ac03681ab49ba5f64",
        "55664ef3-fe38-5ab1-b6aa-f0c88b6e5f94",
    ),
    "expected_edge_id": (
        "6c778f389dd7b4ae4d5a655da2a689c507b53c1ba6b0fffb7e902a9765c59a9e",
        "c3a6c287-c5c9-5de4-bf62-0c747f0bab7b",
    ),
    "receipt_id": (
        "0f18e50fd4bff331ff2528383c5b76bf8e28be0c0caacfe7f5703f4df40b8ec2",
        "2241c3f2-a7df-5b20-9225-3876185aff8a",
    ),
    "future_input_reference_id": (
        "9e0e5d0946aeb32fb7d24ecbe7f94b46990cc63ecec93dae00f387860ed2c2a7",
        "7aa67a69-83cd-54fc-ba8b-b93fb809b89e",
    ),
    "manifest_reference_id": (
        "3306edd4c1c7676d3dea2074e0288ac0f426ba5d43870238b14459101c312085",
        "65560ee2-f32d-55d7-a799-54993f90df94",
    ),
    "identity_identifier_id": (
        "0482ede82ca356e14ad7b29ca4c110e812e013153b9e051a54ca418119476fc5",
        "7e054a2f-d0e7-5b93-b65c-9d0920c4d81b",
    ),
    "scoring_dimension_id": (
        "512da1c767209adc1b7696459df3ce8e0ddf54ac456c26574bc5c712dd35893e",
        "5eeca5e7-afde-5c6b-af16-9211a1fac3ea",
    ),
    "generic_claim_link_id": (
        "658bd5a4ab9d4fc997b3b74b9c297e33defc0fa599a4ab829fafe9877aace996",
        "cce7ea8b-dfd4-558a-bb68-1b554443cbe6",
    ),
    "generic_evidence_link_id": (
        "4b32ec4aedf8dbe0292b803851c74aa7a438834b6a63688a48c339bd1abf4ecc",
        "fb44d691-a97e-5515-97bb-d8965494ed63",
    ),
    "dashboard_root_link_id": (
        "9caa69a221813b5b0be2889b4e3f792384d8da144596508e5170e3551a195380",
        "8d79111d-8a3e-5242-9abf-f1c626cd163a",
    ),
    "erasure_attempt_id": (
        "0cc39a51ca69d91e881d5edfbb257ba6f27c303726f4fd72bbd1f0778195c261",
        "fc7474f0-263e-508f-bb95-0db39d47c8dc",
    ),
    "erasure_audit_event_id": (
        "978fc06631a55778d67f4f2ef43c40805f2d45982f0de9d019205582663a2bfb",
        "04c5f23f-1c84-5bea-8cc8-9d76dc26b042",
    ),
}


def _normalize_json_value(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _normalize_json_value(item)
            for key, item in value.items()
        }
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _normalize_json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _oracle_name_hash(name_tuple: list[object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(name_tuple)).hexdigest()


def _fictional_values(contract_version: str, contract_hash: str) -> dict[str, object]:
    return {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "deal_id": "22222222-2222-4222-8222-222222222222",
        "run_id": "33333333-3333-4333-8333-333333333333",
        "pipeline_contract_version": contract_version,
        "pipeline_contract_hash": contract_hash,
        "step_name": "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
        "step_order": 19,
        "corpus_receipt_id": "44444444-4444-4444-8444-444444444444",
        "object_kind": "DOCUMENT",
        "native_scoped_id": "55555555-5555-4555-8555-555555555555",
        "source_version": "v1",
        "source_disposition": "PARSED",
        "object_identity_sha256": "1" * 64,
        "content_sha256": "2" * 64,
        "corpus_receipt_sha256": "3" * 64,
        "document_id": "66666666-6666-4666-8666-666666666666",
        "attempt_ordinal": 2,
        "parser_name": "pdf",
        "parser_version": "pdf-v1",
        "input_sha256": "4" * 64,
        "receipt_sha256": "5" * 64,
        "status": "COMPLETED",
        "source_lineage_object_id": "77777777-7777-4777-8777-777777777777",
        "source_object_kind": "DOCUMENT",
        "binding_purpose": "EVIDENCE",
        "selected_source_version": "v1",
        "selected_identity_sha256": "6" * 64,
        "selected_content_sha256": "7" * 64,
        "output_class": "NATIVE_ROW",
        "output_kind": "CONFLICT_FINDING",
        "native_identity_sha256": "8" * 64,
        "native_output_sha256": "9" * 64,
        "step_id": "88888888-8888-4888-8888-888888888888",
        "subrole": "__step__",
        "output_ordinal": 0,
        "output_object_id": "99999999-9999-4999-8999-999999999999",
        "decision_phase": "STEP_16",
        "decision_kind": "PROVIDER_APPLICABILITY",
        "subject_kind": "PROVIDER",
        "subject_key": "finnhub",
        "outcome": "NOT_APPLICABLE",
        "upstream_type": "OUTPUT_OBJECT",
        "upstream_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "deciding_step_output_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "contract_rule_id": "rule-001",
        "contract_rule_version": "2",
        "contract_rule_hash": "a" * 64,
        "evidence_sha256": "b" * 64,
        "producer_type": "OUTPUT_OBJECT",
        "producer_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "consumer_step": "ANALYSIS",
        "consumer_subrole": "financial_agent",
        "usage_kind": "EXTERNAL_CONTEXT",
        "decision_id_or_null": None,
        "rule_id": "rule-001",
        "rule_version": "2",
        "rule_hash": "c" * 64,
        "expected_edge_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "consumer_step_output_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "consumer_output_object_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "usage_evidence_sha256": "d" * 64,
        "reference_table": "analysis_agent_input_references",
        "consumer_id": "10101010-1010-4010-8010-101010101010",
        "reference_kind": "EVIDENCE",
        "reference_ordinal": 3,
        "producer_identity_sha256": "e" * 64,
        "producer_value_sha256": "f" * 64,
        "manifest_deliverable_id": "12121212-1212-4212-8212-121212121212",
        "member_kind": "DELIVERABLE",
        "catalog_ordinal_or_null": 4,
        "member_step_output_id": "13131313-1313-4313-8313-131313131313",
        "member_output_object_id": "14141414-1414-4414-8414-141414141414",
        "reference_sha256": "0" * 64,
        "identity_output_id": "15151515-1515-4515-8515-151515151515",
        "identifier_kind": "company_number",
        "identifier_hash": "1" * 64,
        "verification_provenance_sha256": "2" * 64,
        "scoring_output_id": "16161616-1616-4616-8616-161616161616",
        "dimension_ordinal": 5,
        "dimension_key": "financial_quality",
        "binary64(score)": {"float8_be": "3ff0000000000000"},
        "binary64(confidence)": {"float8_be": "3fe0000000000000"},
        "rationale_sha256": "3" * 64,
        "confidence_justification_sha256": "4" * 64,
        "support_projection_sha256": "5" * 64,
        "muhasabah_projection_sha256": "6" * 64,
        "generic_output_id": "17171717-1717-4717-8717-171717171717",
        "claim_ordinal": 7,
        "claim_id": "18181818-1818-4818-8818-181818181818",
        "canonical_key_sha256": "7" * 64,
        "reused_existing_claim": False,
        "evidence_ordinal": 8,
        "generic_claim_link_id": "19191919-1919-4919-8919-191919191919",
        "evidence_id": "20202020-2020-4020-8020-202020202020",
        "source_span_id": "21212121-2121-4121-8121-212121212121",
        "locator_sha256": "8" * 64,
        "dashboard_row_id": "22222222-3333-4333-8333-222222222222",
        "root_kind": "EVIDENCE",
        "root_ordinal": 1,
        "root_output_object_id": "24242424-2424-4424-8424-242424242424",
        "request_id": "25252525-2525-4525-8525-252525252525",
        "prior_status": "REQUESTED",
        "prior_event_id_or_null": None,
        "prior_event_hash_or_null": None,
        "attempt_id": "26262626-2626-4626-8626-262626262626",
        "event_type": "task7.erasure.attempted",
    }


def _recipe_values(
    name_tuple_schema: tuple[str, ...],
    values: dict[str, object],
) -> dict[str, object]:
    return {field: values[field] for field in name_tuple_schema[1:]}


def _spec(id_kind: str) -> Task7UuidNamespaceSpec:
    return next(row for row in task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY if row.id_kind == id_kind)


def _schema(id_kind: str) -> tuple[str, ...]:
    return _spec(id_kind).name_tuple_schema


def _stage_values() -> dict[str, object]:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    return {
        "tenant_id": values["tenant_id"],
        "deal_id": values["deal_id"],
        "run_id": values["run_id"],
        "pipeline_contract_version": _V28_VERSION,
        "pipeline_contract_hash": _V28_CONTRACT_HASH,
        "corpus_receipt_id": None,
        "source_lineage_object_id": None,
        "source_object_kind": "RUN_STAGE_ADMISSION",
        "source_disposition": "ADMITTED_METADATA",
        "binding_purpose": "STAGE_ADMISSION",
        "selected_source_version": "run-stage-admission-v1",
        "selected_identity_sha256": "1a" * 32,
        "selected_content_sha256": "2b" * 32,
        "corpus_receipt_sha256": None,
    }


def _oracle_tuple(
    name_tuple_schema: tuple[str, ...],
    values: dict[str, object],
) -> list[object]:
    return [
        name_tuple_schema[0],
        *(values[field] for field in name_tuple_schema[1:]),
    ]


def _candidate_registry() -> tuple[Task7UuidNamespaceSpec, ...]:
    return task7_uuid.task7_v28_candidate_namespace_registry(
        candidate_contract_version=_V28_VERSION
    )


def _candidate_derived(id_kind: str, values: dict[str, object]) -> Task7DerivedUuid:
    return task7_uuid.derive_task7_v28_candidate_uuid(
        candidate_contract_version=_V28_VERSION,
        candidate_contract_hash=_V28_CONTRACT_HASH,
        id_kind=id_kind,
        values=values,
    )


def _binding_for(derived: Task7DerivedUuid) -> Task7UuidBinding:
    return Task7UuidBinding(
        id_kind=derived.id_kind,
        name_hash=derived.name_hash,
        value=derived.value,
        row_sha256="cd" * 32,
    )


class _SneakyStr(str):
    """A str subclass instance that must fail the candidate proper-type gate."""


class _EqualsNone(str):
    """A str subclass equal to None under == while never being None under is.

    JSON-canonicalizable (the empty string): if the production identity
    guard weakened to equality, canonicalization would succeed and the
    negative would fail instead of being masked by a later serialization
    refusal."""

    def __eq__(self, other: object) -> bool:
        return other is None

    def __ne__(self, other: object) -> bool:
        return other is not None


class _CountingBindings(Sequence[Task7UuidBinding]):
    """Finite trapping iterable recording each materialization attempt."""

    def __init__(self, rows: Sequence[Task7UuidBinding]) -> None:
        self._rows = tuple(rows)
        self.iterations = 0

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[Task7UuidBinding]:
        self.iterations += 1
        return iter(self._rows)

    @overload
    def __getitem__(self, index: int) -> Task7UuidBinding: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Task7UuidBinding, ...]: ...

    def __getitem__(self, index: int | slice) -> Task7UuidBinding | tuple[Task7UuidBinding, ...]:
        return self._rows[index]


# F01 — exact19 candidate registry and old map/API conservation.


def test_candidate_registry_appends_one_stage_kind_after_parent18() -> None:
    candidate = _candidate_registry()

    assert len(candidate) == 19
    assert len({row.id_kind for row in candidate}) == 19
    assert tuple(candidate[:18]) == tuple(task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY)
    assert all(
        candidate[index] is task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY[index] for index in range(18)
    )
    stage = candidate[18]
    binding_spec = _spec("binding_id")
    assert stage.id_kind == _STAGE_ID_KIND
    assert stage.namespace_uuid == _STAGE_NAMESPACE
    assert stage.name_tuple_schema == binding_spec.name_tuple_schema
    assert stage.name_tuple_schema[0] == "run-input-binding-v2"
    assert len(stage.name_tuple_schema) == 15


def test_candidate_registry_rejects_non_v28_versions() -> None:
    for bad_version in ("", "full-strict-v21", "FULL-STRICT-V28", "full-strict-v20"):
        with pytest.raises(task7_uuid.Task7UuidError) as caught:
            task7_uuid.task7_v28_candidate_namespace_registry(
                candidate_contract_version=bad_version
            )
        assert caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    for improper in (123, _SneakyStr(_V28_VERSION), None):
        improper_caught = pytest.raises(
            task7_uuid.Task7UuidError,
            task7_uuid.task7_v28_candidate_namespace_registry,
            candidate_contract_version=improper,
        )
        assert improper_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"


def test_candidate_calls_leave_legacy_tables_unchanged() -> None:
    before_registry = tuple(task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY)
    before_map = dict(task7_uuid._NAMESPACE_BY_KIND)

    _candidate_registry()
    _candidate_derived(_STAGE_ID_KIND, _stage_values())

    assert tuple(task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY) == before_registry
    assert dict(task7_uuid._NAMESPACE_BY_KIND) == before_map
    assert _STAGE_ID_KIND not in task7_uuid._NAMESPACE_BY_KIND


def test_legacy_apis_reject_stage_kind() -> None:
    stage_binding = Task7UuidBinding(
        id_kind=_STAGE_ID_KIND,
        name_hash="a" * 64,
        value=uuid5(_STAGE_NAMESPACE, "a" * 64),
        row_sha256="b" * 64,
    )

    with pytest.raises(task7_uuid.Task7UuidError) as derive_caught:
        task7_uuid.derive_task7_uuid(id_kind=_STAGE_ID_KIND, values=_stage_values())
    assert derive_caught.value.code == "TASK7_UUID_RECIPE_UNAVAILABLE"

    with pytest.raises(task7_uuid.Task7UuidError) as validate_caught:
        task7_uuid.validate_task7_uuid_bindings((stage_binding,))
    assert validate_caught.value.code == "TASK7_UUID_BINDING_INVALID"


def test_v21_admission_remains_fail_closed_for_v28() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)

    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.derive_step_id_for_admission(
            pipeline_contract_version=_V28_VERSION,
            values=values,
        )
    assert caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    with pytest.raises(task7_uuid.Task7UuidError) as values_caught:
        task7_uuid.derive_step_id_for_admission(
            pipeline_contract_version="full-strict-v21",
            values=values,
        )
    assert values_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"


# F02 — legacy 18 literal vectors, collision/retry codes, legacy admission.


@pytest.mark.parametrize("id_kind", tuple(_VECTORS))
def test_legacy_literal_vectors_match_fictional_v20_values(id_kind: str) -> None:
    expected_name_hash, expected_uuid = _VECTORS[id_kind]
    values = _fictional_values(_V20_VERSION, _V20_CONTRACT_HASH)

    derived = task7_uuid.derive_task7_uuid(id_kind=id_kind, values=values)

    assert derived.name_hash == expected_name_hash
    assert str(derived.value) == expected_uuid
    assert derived.value.version == 5

    recipe = _spec(id_kind)
    name_tuple = _oracle_tuple(recipe.name_tuple_schema, values)
    assert _oracle_name_hash(name_tuple) == expected_name_hash
    assert uuid5(recipe.namespace_uuid, expected_name_hash) == derived.value


def test_legacy_collision_and_retry_codes_unchanged() -> None:
    collision = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    conflicting = (
        Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="1" * 64,
            value=collision,
            row_sha256="a" * 64,
        ),
        Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="2" * 64,
            value=collision,
            row_sha256="a" * 64,
        ),
    )
    identifier = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    retried = (
        Task7UuidBinding(
            id_kind="receipt_id",
            name_hash="3" * 64,
            value=identifier,
            row_sha256="a" * 64,
        ),
        Task7UuidBinding(
            id_kind="receipt_id",
            name_hash="3" * 64,
            value=identifier,
            row_sha256="b" * 64,
        ),
    )

    with pytest.raises(task7_uuid.Task7UuidError) as collision_caught:
        task7_uuid.validate_task7_uuid_bindings(conflicting)
    assert collision_caught.value.code == "TASK7_UUID_COLLISION"

    with pytest.raises(task7_uuid.Task7UuidError) as retry_caught:
        task7_uuid.validate_task7_uuid_bindings(retried)
    assert retry_caught.value.code == "TASK7_UUID_ROW_MISMATCH"

    assert task7_uuid.validate_task7_uuid_bindings(()) == ()
    assert task7_uuid.preserve_legacy_step_ids(("a", "b")) == ("a", "b")


# F03 — identical ordinary values under v28 context, strict membership.


@pytest.mark.parametrize("id_kind", tuple(_VECTORS))
def test_candidate_ordinary_recipes_equal_legacy_and_oracle(id_kind: str) -> None:
    recipe = _spec(id_kind)
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    projected = _recipe_values(recipe.name_tuple_schema, values)

    candidate = _candidate_derived(id_kind, projected)
    legacy = task7_uuid.derive_task7_uuid(id_kind=id_kind, values=projected)

    name_tuple = _oracle_tuple(recipe.name_tuple_schema, projected)
    expected_name_hash = _oracle_name_hash(name_tuple)
    assert candidate == legacy
    assert candidate.name_hash == expected_name_hash
    assert candidate.value == uuid5(recipe.namespace_uuid, expected_name_hash)


def test_candidate_membership_strict_where_legacy_permits_extras() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)

    with pytest.raises(task7_uuid.Task7UuidError) as extra_caught:
        _candidate_derived("step_id", values)
    assert extra_caught.value.code == "TASK7_UUID_NAME_TUPLE_INVALID"

    projected = _recipe_values(_schema("step_id"), values)
    missing = dict(projected)
    del missing["step_name"]
    with pytest.raises(task7_uuid.Task7UuidError) as missing_caught:
        _candidate_derived("step_id", missing)
    assert missing_caught.value.code == "TASK7_UUID_NAME_TUPLE_INVALID"

    legacy = task7_uuid.derive_task7_uuid(id_kind="step_id", values=values)
    assert legacy.name_hash == _oracle_name_hash(_oracle_tuple(_schema("step_id"), projected))


def test_values_must_be_mapping_for_strict_membership() -> None:
    keys_only = frozenset(_schema("step_id")[1:])
    same_key_iterables = (keys_only, set(keys_only), sorted(keys_only))
    for non_mapping in same_key_iterables:
        caught = pytest.raises(
            task7_uuid.Task7UuidError,
            task7_uuid.derive_task7_v28_candidate_uuid,
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            id_kind="step_id",
            values=non_mapping,
        )
        assert caught.value.code == "TASK7_UUID_NAME_TUPLE_INVALID"


def test_context_free_recipes_ignore_context_members() -> None:
    recipe = _spec("erasure_audit_event_id")
    assert "pipeline_contract_version" not in recipe.name_tuple_schema
    assert "pipeline_contract_hash" not in recipe.name_tuple_schema
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    projected = _recipe_values(recipe.name_tuple_schema, values)

    candidate = _candidate_derived("erasure_audit_event_id", projected)

    name_tuple = _oracle_tuple(recipe.name_tuple_schema, projected)
    assert candidate.name_hash == _oracle_name_hash(name_tuple)
    assert candidate.value == uuid5(recipe.namespace_uuid, _oracle_name_hash(name_tuple))


# F04 — stage recipe tuple, namespace, fixed/None/digest member rules.


def test_stage_recipe_derives_under_591d_namespace() -> None:
    stage_values = _stage_values()

    derived = _candidate_derived(_STAGE_ID_KIND, stage_values)

    name_tuple = _oracle_tuple(_schema("binding_id"), stage_values)
    expected_name_hash = _oracle_name_hash(name_tuple)
    assert derived.id_kind == _STAGE_ID_KIND
    assert derived.name_hash == expected_name_hash
    assert derived.value == uuid5(_STAGE_NAMESPACE, expected_name_hash)
    assert derived.value.version == 5


def test_stage_and_ordinary_binding_share_name_hash_not_uuid() -> None:
    stage_values = _stage_values()

    stage = _candidate_derived(_STAGE_ID_KIND, stage_values)
    ordinary = _candidate_derived("binding_id", stage_values)

    assert stage.name_hash == ordinary.name_hash
    assert stage.value != ordinary.value
    assert ordinary.value == uuid5(UUID("bb31eeac-26b0-50a1-a3b0-978a7b3b4095"), stage.name_hash)


def test_stage_fixed_and_digest_members_refused_individually() -> None:
    stage_values = _stage_values()
    mutations = (
        ("source_object_kind", "DOCUMENT"),
        ("source_object_kind", None),
        ("source_object_kind", _SneakyStr("RUN_STAGE_ADMISSION")),
        ("source_disposition", "PARSED"),
        ("source_disposition", _SneakyStr("ADMITTED_METADATA")),
        ("binding_purpose", "EVIDENCE"),
        ("binding_purpose", _SneakyStr("STAGE_ADMISSION")),
        ("selected_source_version", "v1"),
        ("selected_source_version", _SneakyStr("run-stage-admission-v1")),
        ("source_lineage_object_id", "77777777-7777-4777-8777-777777777777"),
        ("source_lineage_object_id", _EqualsNone()),
        ("corpus_receipt_id", "44444444-4444-4444-8444-444444444444"),
        ("corpus_receipt_id", _EqualsNone()),
        ("corpus_receipt_sha256", "3" * 64),
        ("corpus_receipt_sha256", _EqualsNone()),
        ("selected_identity_sha256", None),
        ("selected_identity_sha256", "G" * 64),
        ("selected_identity_sha256", "A" * 64),
        ("selected_identity_sha256", "1" * 63),
        ("selected_identity_sha256", _SneakyStr("1a" * 32)),
        ("selected_content_sha256", None),
        ("selected_content_sha256", "not-hex"),
        ("selected_content_sha256", 7),
        ("selected_content_sha256", _SneakyStr("2b" * 32)),
    )
    for field, wrong in mutations:
        mutated = dict(stage_values)
        mutated[field] = wrong
        with pytest.raises(task7_uuid.Task7UuidError) as caught:
            _candidate_derived(_STAGE_ID_KIND, mutated)
        assert caught.value.code == "TASK7_UUID_NAME_TUPLE_INVALID"

    assert stage_values["selected_identity_sha256"] != (stage_values["selected_content_sha256"])


# F05 — context/member/stage negatives and truthful error precedence.


def test_derive_rejects_bad_candidate_context() -> None:
    projected = _recipe_values(
        _schema("step_id"),
        _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH),
    )
    for bad_version in ("", "full-strict-v21", "FULL-STRICT-V28"):
        with pytest.raises(task7_uuid.Task7UuidError) as version_caught:
            task7_uuid.derive_task7_v28_candidate_uuid(
                candidate_contract_version=bad_version,
                candidate_contract_hash=_V28_CONTRACT_HASH,
                id_kind="step_id",
                values=projected,
            )
        assert version_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    for bad_hash in ("", "G" * 64, "A" * 64, "1" * 63):
        with pytest.raises(task7_uuid.Task7UuidError) as hash_caught:
            task7_uuid.derive_task7_v28_candidate_uuid(
                candidate_contract_version=_V28_VERSION,
                candidate_contract_hash=bad_hash,
                id_kind="step_id",
                values=projected,
            )
        assert hash_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"


def test_candidate_context_requires_proper_str_types() -> None:
    projected = _recipe_values(
        _schema("step_id"),
        _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH),
    )
    improper_versions = (123, _SneakyStr(_V28_VERSION), None, b"full-strict-v28")
    for bad_version in improper_versions:
        version_caught = pytest.raises(
            task7_uuid.Task7UuidError,
            task7_uuid.derive_task7_v28_candidate_uuid,
            candidate_contract_version=bad_version,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            id_kind="step_id",
            values=projected,
        )
        assert version_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    improper_hashes = (17, _SneakyStr(_V28_CONTRACT_HASH), None, b"7f" * 32)
    for bad_hash in improper_hashes:
        hash_caught = pytest.raises(
            task7_uuid.Task7UuidError,
            task7_uuid.derive_task7_v28_candidate_uuid,
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=bad_hash,
            id_kind="step_id",
            values=projected,
        )
        assert hash_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"


def test_context_checked_before_recipe_membership() -> None:
    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.derive_task7_v28_candidate_uuid(
            candidate_contract_version="full-strict-v21",
            candidate_contract_hash="not-a-hash",
            id_kind="unknown_kind",
            values={},
        )
    assert caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    with pytest.raises(task7_uuid.Task7UuidError) as kind_caught:
        task7_uuid.derive_task7_v28_candidate_uuid(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            id_kind="unknown_kind",
            values={},
        )
    assert kind_caught.value.code == "TASK7_UUID_RECIPE_UNAVAILABLE"


def test_mismatched_context_members_refused_where_present() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    projected_step = _recipe_values(_schema("step_id"), values)

    mismatched_version = dict(projected_step)
    mismatched_version["pipeline_contract_version"] = "full-strict-v21"
    with pytest.raises(task7_uuid.Task7UuidError) as version_caught:
        _candidate_derived("step_id", mismatched_version)
    assert version_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    projected_binding = _recipe_values(_schema("binding_id"), values)
    mismatched_hash = dict(projected_binding)
    mismatched_hash["pipeline_contract_hash"] = "0" * 64
    with pytest.raises(task7_uuid.Task7UuidError) as hash_caught:
        _candidate_derived("binding_id", mismatched_hash)
    assert hash_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"

    hash_only_schema = _schema("step_output_id")
    assert "pipeline_contract_version" not in hash_only_schema
    projected_step_output = _recipe_values(hash_only_schema, values)
    mismatched_hash_only = dict(projected_step_output)
    mismatched_hash_only["pipeline_contract_hash"] = "0" * 64
    with pytest.raises(task7_uuid.Task7UuidError) as step_output_caught:
        _candidate_derived("step_output_id", mismatched_hash_only)
    assert step_output_caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"


# F06 — candidate binding validation: context, order, namespace sweep.


def test_correct_namespace_bindings_accepted_including_stage() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    stage_values = _stage_values()
    candidate = _candidate_registry()
    assert len(candidate) == 19

    bindings = []
    for spec in candidate:
        recipe_values = (
            stage_values
            if spec.id_kind == _STAGE_ID_KIND
            else _recipe_values(spec.name_tuple_schema, values)
        )
        derived = _candidate_derived(spec.id_kind, recipe_values)
        oracle_name_hash = _oracle_name_hash(_oracle_tuple(spec.name_tuple_schema, recipe_values))
        assert derived.name_hash == oracle_name_hash
        assert derived.value == uuid5(spec.namespace_uuid, oracle_name_hash)
        bindings.append(_binding_for(derived))

    result = task7_uuid.validate_task7_v28_candidate_uuid_bindings(
        candidate_contract_version=_V28_VERSION,
        candidate_contract_hash=_V28_CONTRACT_HASH,
        bindings=tuple(bindings),
    )

    assert result == tuple(bindings)


def test_wrong_namespace_singleton_rejected_by_sweep() -> None:
    name_hash = "ab" * 32
    wrong_value = uuid5(_STEP_ID_NAMESPACE, name_hash)
    oracle_value = uuid5(UUID("bb31eeac-26b0-50a1-a3b0-978a7b3b4095"), name_hash)
    assert wrong_value != oracle_value
    binding = Task7UuidBinding(
        id_kind="binding_id",
        name_hash=name_hash,
        value=wrong_value,
        row_sha256="cd" * 32,
    )

    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=(binding,),
        )
    assert caught.value.code == "TASK7_UUID_BINDING_INVALID"


def test_malformed_binding_fields_rejected() -> None:
    good = _binding_for(_candidate_derived(_STAGE_ID_KIND, _stage_values()))
    malformed = (
        Task7UuidBinding(
            id_kind="unknown_kind",
            name_hash=good.name_hash,
            value=good.value,
            row_sha256=good.row_sha256,
        ),
        Task7UuidBinding(
            id_kind=good.id_kind,
            name_hash="xyz",
            value=good.value,
            row_sha256=good.row_sha256,
        ),
        Task7UuidBinding(
            id_kind=good.id_kind,
            name_hash=good.name_hash,
            value=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            row_sha256=good.row_sha256,
        ),
        Task7UuidBinding(
            id_kind=good.id_kind,
            name_hash=good.name_hash,
            value=good.value,
            row_sha256="!!",
        ),
    )
    for binding in malformed:
        with pytest.raises(task7_uuid.Task7UuidError) as caught:
            task7_uuid.validate_task7_v28_candidate_uuid_bindings(
                candidate_contract_version=_V28_VERSION,
                candidate_contract_hash=_V28_CONTRACT_HASH,
                bindings=(binding,),
            )
        assert caught.value.code == "TASK7_UUID_BINDING_INVALID"


def test_conflicting_declarations_hit_collision_before_sweep() -> None:
    arbitrary = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    bindings = (
        Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="1" * 64,
            value=arbitrary,
            row_sha256="a" * 64,
        ),
        Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="2" * 64,
            value=arbitrary,
            row_sha256="a" * 64,
        ),
        Task7UuidBinding(
            id_kind="receipt_id",
            name_hash="1" * 64,
            value=UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb"),
            row_sha256="b" * 64,
        ),
    )

    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=bindings,
        )
    assert caught.value.code == "TASK7_UUID_COLLISION"


def test_namespace_coherent_retry_drift_hits_row_mismatch() -> None:
    first = _binding_for(_candidate_derived(_STAGE_ID_KIND, _stage_values()))
    drifted = Task7UuidBinding(
        id_kind=first.id_kind,
        name_hash=first.name_hash,
        value=first.value,
        row_sha256="ef" * 32,
    )

    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=(first, drifted),
        )
    assert caught.value.code == "TASK7_UUID_ROW_MISMATCH"


def test_legacy_accepts_candidate_rejects_wrong_namespace_singleton() -> None:
    name_hash = "f1" * 32
    wrong_value = uuid5(_STEP_ID_NAMESPACE, name_hash)
    binding_namespace = UUID("bb31eeac-26b0-50a1-a3b0-978a7b3b4095")
    assert wrong_value != uuid5(binding_namespace, name_hash)
    binding = Task7UuidBinding(
        id_kind="binding_id",
        name_hash=name_hash,
        value=wrong_value,
        row_sha256="cd" * 32,
    )

    assert task7_uuid.validate_task7_uuid_bindings((binding,)) == (binding,)

    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=(binding,),
        )
    assert caught.value.code == "TASK7_UUID_BINDING_INVALID"


def test_binding_validation_gated_by_context_and_materialization() -> None:
    assert (
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version=_V28_VERSION,
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=(),
        )
        == ()
    )

    binding = _binding_for(_candidate_derived(_STAGE_ID_KIND, _stage_values()))
    trapped = _CountingBindings((binding,))
    with pytest.raises(task7_uuid.Task7UuidError) as caught:
        task7_uuid.validate_task7_v28_candidate_uuid_bindings(
            candidate_contract_version="full-strict-v21",
            candidate_contract_hash=_V28_CONTRACT_HASH,
            bindings=trapped,
        )
    assert caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"
    assert trapped.iterations == 0

    admitted = _CountingBindings((binding,))
    result = task7_uuid.validate_task7_v28_candidate_uuid_bindings(
        candidate_contract_version=_V28_VERSION,
        candidate_contract_hash=_V28_CONTRACT_HASH,
        bindings=admitted,
    )
    assert result == (binding,)
    assert admitted.iterations == 1


# F07 — data sensitivity: reorder, NFC, perturbation, restored, tables.


def test_mapping_reorder_and_nfc_equivalence_derive_identically() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    projected = _recipe_values(_schema("step_id"), values)
    reordered = dict(reversed(tuple(projected.items())))

    baseline = _candidate_derived("step_id", projected)
    assert _candidate_derived("step_id", reordered) == baseline

    nfc = dict(projected)
    nfd = dict(projected)
    nfc["step_name"] = unicodedata.normalize("NFC", "METHODOLOGY_É_STEP")
    nfd["step_name"] = unicodedata.normalize("NFD", "METHODOLOGY_É_STEP")
    assert nfc["step_name"] != nfd["step_name"]
    derived_nfc = _candidate_derived("step_id", nfc)
    derived_nfd = _candidate_derived("step_id", nfd)
    assert derived_nfc == derived_nfd
    assert derived_nfc != baseline


def test_perturbed_member_changes_identity_and_restore_matches() -> None:
    values = _fictional_values(_V28_VERSION, _V28_CONTRACT_HASH)
    projected = _recipe_values(_schema("step_id"), values)

    baseline = _candidate_derived("step_id", projected)
    perturbed = dict(projected)
    perturbed["step_order"] = 20
    changed = _candidate_derived("step_id", perturbed)
    assert changed.name_hash != baseline.name_hash
    assert changed.value != baseline.value
    assert _candidate_derived("step_id", projected) == baseline


def test_namespace_tables_immutable_across_calls() -> None:
    before = tuple(task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY)
    before_map = dict(task7_uuid._NAMESPACE_BY_KIND)
    before_candidate = _candidate_registry()

    _candidate_derived(_STAGE_ID_KIND, _stage_values())
    task7_uuid.validate_task7_v28_candidate_uuid_bindings(
        candidate_contract_version=_V28_VERSION,
        candidate_contract_hash=_V28_CONTRACT_HASH,
        bindings=(),
    )

    assert tuple(task7_uuid.TASK7_UUID_NAMESPACE_REGISTRY) == before
    assert dict(task7_uuid._NAMESPACE_BY_KIND) == before_map
    assert _candidate_registry() == before_candidate
