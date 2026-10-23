"""RED vectors for deterministic post-0038 Task 7 UUIDv5 identities."""

from __future__ import annotations

import hashlib
import importlib
import json
import unicodedata
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid5

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)

_CONTRACT_HASH = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"

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


def _uuid_module() -> ModuleType:
    try:
        return importlib.import_module("idis.pipeline.task7_uuid")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.pipeline.task7_uuid":
            raise
        pytest.fail(
            "Task 7 deterministic UUID module is missing: idis.pipeline.task7_uuid",
            pytrace=False,
        )


def _artifact_uuid_rows() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    artifact = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    return tuple(
        (row[0], row[1], tuple(row[2])) for row in artifact["uuid_namespace_registry"]["rows"]
    )


def _fixture_contract_hash() -> str:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return str(fixture["FULL_STRICT_V20_CONTRACT_HASH"])


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


def _values() -> dict[str, object]:
    return {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "deal_id": "22222222-2222-4222-8222-222222222222",
        "run_id": "33333333-3333-4333-8333-333333333333",
        "pipeline_contract_version": "full-strict-v20",
        "pipeline_contract_hash": _CONTRACT_HASH,
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


def test_literal_contract_hash_matches_canonical_v20_fixture() -> None:
    assert _fixture_contract_hash() == _CONTRACT_HASH


def test_literal_vectors_rederive_independently_from_canonical_v20_bytes() -> None:
    values = _values()
    artifact_rows = _artifact_uuid_rows()

    assert tuple(_VECTORS) == tuple(row[0] for row in artifact_rows)
    for id_kind, namespace, name_tuple_schema in artifact_rows:
        name_tuple = [
            name_tuple_schema[0],
            *(values[field] for field in name_tuple_schema[1:]),
        ]
        name_hash = hashlib.sha256(_canonical_json_bytes(name_tuple)).hexdigest()
        identifier = str(uuid5(UUID(namespace), name_hash))

        assert _VECTORS[id_kind] == (name_hash, identifier)


@pytest.mark.parametrize("id_kind", tuple(_VECTORS))
def test_every_frozen_uuid_recipe_matches_literal_vector(id_kind: str) -> None:
    module = _uuid_module()
    expected_name_hash, expected_uuid = _VECTORS[id_kind]

    derived = module.derive_task7_uuid(id_kind=id_kind, values=_values())

    assert derived.name_hash == expected_name_hash
    assert str(derived.value) == expected_uuid
    assert isinstance(derived.value, UUID)
    assert derived.value.version == 5


def test_runtime_namespace_registry_equals_frozen_artifact_order() -> None:
    module = _uuid_module()

    actual = tuple(
        (row.id_kind, str(row.namespace_uuid), row.name_tuple_schema)
        for row in module.TASK7_UUID_NAMESPACE_REGISTRY
    )

    assert actual == _artifact_uuid_rows()
    assert tuple(_VECTORS) == tuple(row[0] for row in actual)


def test_uuid_derivation_is_retry_stable_and_mapping_order_independent() -> None:
    module = _uuid_module()
    values = _values()
    reverse_order = dict(reversed(tuple(values.items())))

    first = module.derive_task7_uuid(id_kind="expected_edge_id", values=values)
    retry = module.derive_task7_uuid(id_kind="expected_edge_id", values=reverse_order)

    assert retry == first


def test_native_primary_key_prevents_same_payload_from_collapsing_distinct_rows() -> None:
    module = _uuid_module()
    first_values = _values()
    second_values = dict(first_values)
    second_values["native_identity_sha256"] = "a" * 64
    assert second_values["native_output_sha256"] == first_values["native_output_sha256"]

    first = module.derive_task7_uuid(id_kind="output_object_id", values=first_values)
    second = module.derive_task7_uuid(id_kind="output_object_id", values=second_values)

    assert first.name_hash != second.name_hash
    assert first.value != second.value


def test_collision_guard_rejects_one_uuid_bound_to_two_name_hashes() -> None:
    module = _uuid_module()
    collision = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    bindings = (
        module.Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="1" * 64,
            value=collision,
            row_sha256="a" * 64,
        ),
        module.Task7UuidBinding(
            id_kind="output_object_id",
            name_hash="2" * 64,
            value=collision,
            row_sha256="a" * 64,
        ),
    )

    with pytest.raises(module.Task7UuidError) as caught:
        module.validate_task7_uuid_bindings(bindings)

    assert caught.value.code == "TASK7_UUID_COLLISION"


def test_exact_retry_id_with_different_row_bytes_fails_closed() -> None:
    module = _uuid_module()
    identifier = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    bindings = (
        module.Task7UuidBinding(
            id_kind="receipt_id",
            name_hash="3" * 64,
            value=identifier,
            row_sha256="a" * 64,
        ),
        module.Task7UuidBinding(
            id_kind="receipt_id",
            name_hash="3" * 64,
            value=identifier,
            row_sha256="b" * 64,
        ),
    )

    with pytest.raises(module.Task7UuidError) as caught:
        module.validate_task7_uuid_bindings(bindings)

    assert caught.value.code == "TASK7_UUID_ROW_MISMATCH"


def test_legacy_contracts_keep_opaque_step_ids_outside_uuidv5_admission() -> None:
    registry = importlib.import_module("idis.pipeline.contract_registry")
    module = _uuid_module()
    opaque_ids = (
        "00000000-0000-4000-8000-000000000001",
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
    )

    assert registry.PIPELINE_CONTRACTS["snapshot-v1"].deterministic_step_ids is False
    assert registry.PIPELINE_CONTRACTS["full-v1"].deterministic_step_ids is False
    assert module.preserve_legacy_step_ids(opaque_ids) == opaque_ids

    with pytest.raises(module.Task7UuidError) as caught:
        module.derive_step_id_for_admission(
            pipeline_contract_version="full-v1",
            values=_values(),
        )
    assert caught.value.code == "TASK7_UUID_STRICT_ADMISSION_REQUIRED"
