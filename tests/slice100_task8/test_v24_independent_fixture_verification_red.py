"""RED contracts for Task-8 v24 independent fixture verification.

The verifier under test is a second implementation.  It may consume candidate
artifact, policy, and fixture bytes, but it may not import or execute the
materializer whose output it verifies.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from . import _v24_independent_oracle as oracle


def _candidate_bytes() -> tuple[bytes, bytes, bytes]:
    oracle.require(
        oracle.V24_ARTIFACT.is_file(),
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )
    oracle.require(
        oracle.V24_FIXTURE.is_file(),
        "TASK8_V24_CONTRACT_FIXTURE_NOT_IMPLEMENTED",
    )
    oracle.require(
        oracle.POLICY_V2_ARTIFACT.is_file(),
        "TASK8_V24_POLICY_V2_MISSING",
    )
    return (
        oracle.V24_ARTIFACT.read_bytes(),
        oracle.POLICY_V2_ARTIFACT.read_bytes(),
        oracle.V24_FIXTURE.read_bytes(),
    )


def _verify(
    module: Any,
    *,
    artifact_raw: bytes,
    policy_raw: bytes,
    fixture_raw: bytes,
    reviewer_map_raw: bytes | None = None,
) -> Mapping[str, Any]:
    verifier = getattr(module, "verify_fixture", None)
    oracle.require(callable(verifier), "TASK8_V24_INDEPENDENT_VERIFY_API_MISSING")
    result = verifier(
        artifact_raw=artifact_raw,
        policy_artifact_raw=policy_raw,
        fixture_raw=fixture_raw,
        reviewer_map_raw=(
            oracle.POLICY_V2_REVIEWER_MAP.read_bytes()
            if reviewer_map_raw is None
            else reviewer_map_raw
        ),
    )
    oracle.require(
        isinstance(result, Mapping),
        "TASK8_V24_INDEPENDENT_VERIFY_RESULT_INVALID",
    )
    return result


def _expect_verifier_error(
    module: Any,
    code: str,
    *,
    artifact_raw: bytes,
    policy_raw: bytes,
    fixture_raw: bytes,
    reviewer_map_raw: bytes | None = None,
) -> None:
    error_type = getattr(module, "IndependentVerificationError", None)
    oracle.require(
        isinstance(error_type, type) and issubclass(error_type, Exception),
        "TASK8_V24_INDEPENDENT_ERROR_TYPE_MISSING",
    )
    with pytest.raises(error_type) as caught:
        _verify(
            module,
            artifact_raw=artifact_raw,
            policy_raw=policy_raw,
            fixture_raw=fixture_raw,
            reviewer_map_raw=reviewer_map_raw,
        )
    assert oracle.verifier_error_code(caught.value) == code


def _load_candidate_objects() -> tuple[Any, dict[str, Any], dict[str, Any], bytes]:
    module = oracle.load_verifier()
    artifact_raw, policy_raw, fixture_raw = _candidate_bytes()
    artifact = oracle.load_json_bytes(
        artifact_raw,
        "TASK8_V24_CONTRACT_ARTIFACT_INVALID",
    )
    fixture = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )
    return module, artifact, fixture, policy_raw


def _uuid_rows(artifact: Mapping[str, Any]) -> dict[str, list[Any]]:
    return {row[0]: row for row in artifact["uuid_namespace_registry"]["rows"]}


def _synthetic_graph_artifact(
    *,
    uuid_rows: list[list[Any]],
    producer_rows: list[list[str]],
    digest_rows: list[list[Any]] | None = None,
) -> dict[str, Any]:
    producer_hash = oracle.canonical_sha256(
        ["task8-v24-graph-producer-authority-v1", producer_rows]
    )
    external_rows: list[list[str]] = []
    external_hash = oracle.canonical_sha256(
        ["task8-v24-graph-allowed-external-input-v1", external_rows]
    )
    return {
        "uuid_namespace_registry": {"rows": uuid_rows},
        "digest_recipe_registry": {"rows": [] if digest_rows is None else digest_rows},
        "task8_structural_admission": {
            "identity_hash_dependency_contract": {
                "schema_version": oracle.GRAPH_SCHEMA,
                "source_discovery": "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS",
                "recipe_grammar_tuple": ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"],
                "producer_authority_schema": [
                    "source_pointer",
                    "artifact_name",
                    "layer",
                    "scope",
                    "output_kind",
                    "symbol",
                    "dependency_payload_pointer",
                ],
                "producer_authority_tuple": producer_rows,
                "producer_authority_sha256": producer_hash,
                "allowed_external_input_schema": [
                    "source_pointer",
                    "token_path",
                    "symbol",
                    "authority",
                ],
                "allowed_external_input_tuple": external_rows,
                "allowed_external_input_sha256": external_hash,
                "dashboard_special_case_allowed": False,
                "synthetic_symbol_special_case_allowed": False,
                "v23_non_dashboard_cycle_disposition": ("SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY"),
            }
        },
    }


def test_hand_written_dashboard_identity_golden_replays_in_test_oracle() -> None:
    """The literal vector is useful evidence before either production path exists."""

    vector = oracle.load_golden_vector()
    assert oracle.derive_dashboard_identity_vector(**vector["inputs"]) == vector["expected"]


def test_independent_verifier_source_cannot_reach_materializer_or_runtime_derivation() -> None:
    oracle.require(
        oracle.INDEPENDENT_VERIFIER.is_file(),
        "TASK8_V24_INDEPENDENT_VERIFIER_NOT_IMPLEMENTED",
    )
    oracle.assert_verifier_source_is_independent(oracle.INDEPENDENT_VERIFIER.read_bytes())


def test_independent_verifier_recomputes_every_fixture_hash_from_candidate_bytes() -> None:
    module = oracle.load_verifier()
    artifact_raw, policy_raw, fixture_raw = _candidate_bytes()
    artifact = oracle.load_json_bytes(
        artifact_raw,
        "TASK8_V24_CONTRACT_ARTIFACT_INVALID",
    )
    fixture = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )
    expected_components, expected_contract = oracle.independent_contract_hash(
        artifact,
        artifact_raw,
    )
    expected_graph, expected_graph_counts = oracle.independent_fixture_graph_hash(fixture)
    expected_conservation = oracle.independent_conservation_hash(artifact)

    result = _verify(
        module,
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=fixture_raw,
    )

    assert result == {
        "verification_path": "TASK8_V24_INDEPENDENT_STDLIB_V1",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "component_hashes": expected_components,
        "contract_hash": expected_contract,
        "identity_graph_sha256": expected_graph,
        "identity_graph_counts": expected_graph_counts,
        "conservation_sha256": expected_conservation,
        "policy_binding_sha256": fixture["policy_binding"]["binding_sha256"],
    }
    assert fixture["component_hashes"] == expected_components
    assert fixture["FULL_STRICT_V24_CONTRACT_HASH"] == expected_contract
    assert fixture["identity_hash_dependency_graph"]["graph_sha256"] == expected_graph
    assert fixture["approved_semantics_conservation"] == {
        "schema_version": oracle.CONSERVATION_SCHEMA,
        "entry_count": 7,
        "sha256": expected_conservation,
    }


@pytest.mark.parametrize(
    ("fixture_path", "corrupt_value", "expected_code"),
    [
        (
            ("component_hashes", "metadata_hash"),
            "0" * 64,
            "TASK8_V24_INDEPENDENT_COMPONENT_HASH_MISMATCH",
        ),
        (
            ("FULL_STRICT_V24_CONTRACT_HASH",),
            "1" * 64,
            "TASK8_V24_INDEPENDENT_CONTRACT_HASH_MISMATCH",
        ),
        (
            ("identity_hash_dependency_graph", "graph_sha256"),
            "2" * 64,
            "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
        ),
        (
            ("approved_semantics_conservation", "sha256"),
            "3" * 64,
            "TASK8_V24_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
        ),
    ],
)
def test_each_stale_fixture_hash_family_fails_with_a_distinct_code(
    fixture_path: tuple[str, ...],
    corrupt_value: str,
    expected_code: str,
) -> None:
    module = oracle.load_verifier()
    artifact_raw, policy_raw, fixture_raw = _candidate_bytes()
    fixture = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )
    target: dict[str, Any] = fixture
    for segment in fixture_path[:-1]:
        target = target[segment]
    target[fixture_path[-1]] = corrupt_value

    _expect_verifier_error(
        module,
        expected_code,
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=oracle.render_artifact(fixture),
    )


def test_fixture_count_drift_is_independently_rejected() -> None:
    module = oracle.load_verifier()
    artifact_raw, policy_raw, fixture_raw = _candidate_bytes()
    fixture = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )
    fixture["counts"]["provider_count"] += 1

    _expect_verifier_error(
        module,
        "TASK8_V24_INDEPENDENT_COUNT_MISMATCH",
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=oracle.render_artifact(fixture),
    )


def test_non_dashboard_artifact_dependency_cannot_hide_behind_a_stale_graph_fixture() -> None:
    module, artifact, fixture, policy_raw = _load_candidate_objects()
    rows = _uuid_rows(artifact)
    rows["step_id"][2].append("source_lineage_object_id")
    artifact_raw, fixture_raw = oracle.refreshed_fixture_hash_bindings(artifact, fixture)

    _expect_verifier_error(
        module,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=fixture_raw,
    )


def test_general_graph_rejects_the_named_synthetic_non_dashboard_cycle() -> None:
    """This is the guard-bypass vector; it is not a cycle in unmodified v23 bytes."""

    module, artifact, fixture, policy_raw = _load_candidate_objects()
    rows = _uuid_rows(artifact)
    rows["step_id"][2].append("source_lineage_object_id")
    rows["source_lineage_object_id"][2].append("step_id")
    artifact_raw, fixture_raw = oracle.refreshed_fixture_hash_bindings(artifact, fixture)

    _expect_verifier_error(
        module,
        "TASK8_V24_INDEPENDENT_IDENTITY_HASH_CYCLE",
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=fixture_raw,
    )


def test_generic_graph_rejects_misspelled_governed_reference_instead_of_leafing_it() -> None:
    module = oracle.load_verifier()
    pointer = "full_strict:#/uuid_namespace_registry/rows/0"
    artifact = _synthetic_graph_artifact(
        uuid_rows=[
            [
                "step_id",
                "00000000-0000-5000-8000-000000000001",
                ["step_iid"],
            ]
        ],
        producer_rows=[
            [
                pointer,
                "full_strict",
                "RUNTIME_VALUE",
                "RUN_STEP",
                "RUN_STEP",
                "step_id",
                "#/2",
            ]
        ],
    )

    with pytest.raises(module.IndependentVerificationError) as caught:
        module._derive_graph(artifact, {})  # noqa: SLF001 - independent graph seam

    assert caught.value.code == "TASK8_V24_INDEPENDENT_GOVERNED_REFERENCE_UNRESOLVED"


def test_generic_graph_rejects_duplicate_producer_symbols_before_token_resolution() -> None:
    module = oracle.load_verifier()
    first = "full_strict:#/uuid_namespace_registry/rows/0"
    second = "full_strict:#/uuid_namespace_registry/rows/1"
    artifact = _synthetic_graph_artifact(
        uuid_rows=[
            ["first_id", "00000000-0000-5000-8000-000000000001", []],
            ["second_id", "00000000-0000-5000-8000-000000000002", []],
        ],
        producer_rows=[
            [
                first,
                "full_strict",
                "RUNTIME_VALUE",
                "FIRST",
                "FIRST",
                "duplicate_id",
                "#/2",
            ],
            [
                second,
                "full_strict",
                "RUNTIME_VALUE",
                "SECOND",
                "SECOND",
                "duplicate_id",
                "#/2",
            ],
        ],
    )

    with pytest.raises(module.IndependentVerificationError) as caught:
        module._derive_graph(artifact, {})  # noqa: SLF001 - independent graph seam

    assert caught.value.code == "TASK8_V24_INDEPENDENT_DUPLICATE_PRODUCER"


def test_generic_graph_output_is_independent_of_producer_manifest_row_order() -> None:
    module = oracle.load_verifier()
    first = "full_strict:#/uuid_namespace_registry/rows/0"
    second = "full_strict:#/uuid_namespace_registry/rows/1"
    producer_rows = [
        [
            first,
            "full_strict",
            "RUNTIME_VALUE",
            "FIRST",
            "FIRST",
            "first_id",
            "#/2",
        ],
        [
            second,
            "full_strict",
            "RUNTIME_VALUE",
            "SECOND",
            "SECOND",
            "second_id",
            "#/2",
        ],
    ]
    artifact = _synthetic_graph_artifact(
        uuid_rows=[
            ["first_id", "00000000-0000-5000-8000-000000000001", []],
            ["second_id", "00000000-0000-5000-8000-000000000002", []],
        ],
        producer_rows=producer_rows,
    )
    reordered = copy.deepcopy(artifact)
    contract = reordered["task8_structural_admission"]["identity_hash_dependency_contract"]
    contract["producer_authority_tuple"].reverse()
    contract["producer_authority_sha256"] = oracle.canonical_sha256(
        ["task8-v24-graph-producer-authority-v1", contract["producer_authority_tuple"]]
    )

    assert module._derive_graph(artifact, {}) == module._derive_graph(  # noqa: SLF001
        reordered, {}
    )


def test_repeated_symbol_in_one_recipe_has_one_deterministic_edge_receipt() -> None:
    module = oracle.load_verifier()
    first = "full_strict:#/digest_recipe_registry/rows/0"
    second = "full_strict:#/digest_recipe_registry/rows/1"
    artifact = _synthetic_graph_artifact(
        uuid_rows=[],
        digest_rows=[
            ["base_sha256", "[]"],
            ["derived_sha256", "[base_sha256,base_sha256]"],
        ],
        producer_rows=[
            [
                first,
                "full_strict",
                "CONTRACT_RECIPE",
                "BASE",
                "BASE",
                "base_sha256",
                "#/1",
            ],
            [
                second,
                "full_strict",
                "CONTRACT_RECIPE",
                "DERIVED",
                "DERIVED",
                "derived_sha256",
                "#/1",
            ],
        ],
    )

    graph = module._derive_graph(artifact, {})  # noqa: SLF001 - independent graph seam

    assert graph["counts"]["edge_count"] == 1
    assert graph["edge_tuple"][0][2] == f"{second}:$#base_sha256"


def test_recipe_grammar_operators_are_not_misclassified_as_dependency_symbols() -> None:
    module = oracle.load_verifier()
    identifiers = module._recipe_identifiers(  # noqa: SLF001 - independent graph seam
        "H(CJ(ORDERED(TUPLE(SCALAR(schema_sha256)))))"
    )

    assert identifiers == {"schema_sha256"}


def test_recipe_grammar_is_classified_by_declared_payload_pointer_not_source_path() -> None:
    module = oracle.load_verifier()
    pointer = "full_strict:#/custom_preimage"
    artifact = _synthetic_graph_artifact(
        uuid_rows=[],
        producer_rows=[
            [
                pointer,
                "full_strict",
                "CONTRACT_RECIPE",
                "CUSTOM",
                "CUSTOM",
                "custom_sha256",
                "#/1",
            ]
        ],
    )
    artifact["custom_preimage"] = ["custom_sha256", "EVAL(unreviewed_runtime_expression)"]

    with pytest.raises(module.IndependentVerificationError) as caught:
        module._derive_graph(artifact, {})  # noqa: SLF001 - independent graph seam

    assert caught.value.code == "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID"


def test_every_frozen_digest_recipe_is_fully_consumed_by_closed_grammar() -> None:
    module = oracle.load_verifier()
    artifact = oracle.load_json_file(
        oracle.V24_ARTIFACT,
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )

    for _symbol, expression in artifact["digest_recipe_registry"]["rows"]:
        module._validate_recipe_grammar("#/1", expression)  # noqa: SLF001


@pytest.mark.parametrize(
    "expression",
    [
        "H(value)",
        "CJ([value])",
        "SCALAR(value)",
        "TUPLE(value,other)",
        "ORDERED[(value,other)]",
        "[]",
        '(value,"literal")',
        " lowercase_hex ( SHA256 ( CJ ( value ) ) ) ",
        "hex(float8send(score))",
    ],
)
def test_closed_recipe_grammar_accepts_only_the_reviewed_compositions(
    expression: str,
) -> None:
    module = oracle.load_verifier()

    module._validate_recipe_grammar("#/1", expression)  # noqa: SLF001


@pytest.mark.parametrize(
    "expression",
    [
        (
            '["typed-schema-definition-v1",schema_id,schema_version,authority_locator,'
            "normalized_schema,collection_paths];H(H)"
        ),
        "UNKNOWN(value)",
        "0",
        "-1",
        "1.5",
        "H([value])TRAILING",
        "H([value]",
        "H()",
        "H(value,other)",
        "CJ()",
        "SCALAR(value,other)",
        "TUPLE()",
        "ORDERED[]",
        "ORDERED[value,other]",
        "lowercase_hex(SHA256(CJ(value)),extra)",
        "lowercase_hex(value)",
        "SHA256(value)",
        "SHA256",
        "hex(value)",
        "float8send(score)",
        "float8send",
        "hex(float8send(H(score)))",
        "hex(float8send(H))",
        "hex(float8send(hex))",
    ],
)
def test_closed_recipe_grammar_rejects_unknown_trailing_nesting_and_arity(
    expression: str,
) -> None:
    module = oracle.load_verifier()

    with pytest.raises(module.IndependentVerificationError) as caught:
        module._validate_recipe_grammar("#/1", expression)  # noqa: SLF001

    assert caught.value.code == "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID"


def test_exact_audit_trailing_recipe_mutation_fails_through_independent_derivation() -> None:
    module = oracle.load_verifier()
    artifact = oracle.load_json_file(
        oracle.V24_ARTIFACT,
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )
    row = artifact["digest_recipe_registry"]["rows"][1]
    assert row[0] == "schema_sha256"
    row[1] += ";H(H)"

    with pytest.raises(module.IndependentVerificationError) as caught:
        module.derive_fixture(
            artifact_raw=oracle.render_artifact(artifact),
            policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
        )

    assert caught.value.code == "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID"


def test_independent_canonical_binary64_is_finite_and_normalizes_negative_zero() -> None:
    module = oracle.load_verifier()

    assert module._canonical_json({"value": 0.25}) == b'{"value":0.25}'  # noqa: SLF001
    assert module._canonical_json({"value": -0.0}) == b'{"value":0}'  # noqa: SLF001
    for illegal in (math.nan, math.inf, -math.inf):
        with pytest.raises(module.IndependentVerificationError) as caught:
            module._canonical_json({"value": illegal})  # noqa: SLF001
        assert caught.value.code == "TASK8_V24_INDEPENDENT_BINARY64_NONFINITE"


def test_independent_path_pins_exact_frozen_policy_v2_bytes_and_digest() -> None:
    module = oracle.load_verifier()
    validate = getattr(module, "_validate_frozen_policy", None)
    oracle.require(callable(validate), "TASK8_V24_INDEPENDENT_POLICY_PIN_API_MISSING")
    policy_raw = oracle.POLICY_V2_ARTIFACT.read_bytes()

    policy = validate(policy_raw)
    assert hashlib.sha256(policy_raw).hexdigest() == (
        "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
    )
    assert policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"] == (
        "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
    )

    mutated = copy.deepcopy(policy)
    mutated["metadata"]["formula_count"] += 1
    with pytest.raises(module.IndependentVerificationError) as caught:
        validate(oracle.render_artifact(mutated))
    assert caught.value.code == "TASK8_V24_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH"


def test_independent_admission_pins_parent_top_level_delta_and_structural_hash() -> None:
    module = oracle.load_verifier()
    validate = getattr(module, "_validate_structural_admission", None)
    oracle.require(callable(validate), "TASK8_V24_INDEPENDENT_STRUCTURAL_API_MISSING")
    artifact = oracle.load_json_file(
        oracle.V24_ARTIFACT,
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )

    validate(artifact)
    mutations: list[tuple[dict[str, Any], str]] = []
    wrong_parent = copy.deepcopy(artifact)
    wrong_parent["task8_structural_admission"]["parent_provenance"][
        "parent_pipeline_contract_version"
    ] = "full-strict-v23"
    mutations.append((wrong_parent, "TASK8_V24_INDEPENDENT_PARENT_PROVENANCE_INVALID"))
    extra = copy.deepcopy(artifact)
    extra["unapproved_delta"] = {"must_never_be_admitted": True}
    mutations.append((extra, "TASK8_V24_INDEPENDENT_TOP_LEVEL_SECTION_CLOSURE_INVALID"))
    missing_delta = copy.deepcopy(artifact)
    missing_delta["task8_structural_admission"]["approved_delta_manifest"].pop()
    mutations.append((missing_delta, "TASK8_V24_INDEPENDENT_APPROVED_DELTA_MANIFEST_INVALID"))
    bad_hash = copy.deepcopy(artifact)
    bad_hash["task8_structural_admission"]["expected_sha256"] = "0" * 64
    mutations.append((bad_hash, "TASK8_V24_INDEPENDENT_STRUCTURAL_HASH_MISMATCH"))
    for candidate, code in mutations:
        with pytest.raises(module.IndependentVerificationError) as caught:
            validate(candidate)
        assert caught.value.code == code


def test_reviewer_map_bytes_are_pinned_without_decoding_private_prose() -> None:
    module = oracle.load_verifier()
    validate = getattr(module, "_validate_reviewer_map_binding", None)
    oracle.require(callable(validate), "TASK8_V24_INDEPENDENT_REVIEWER_MAP_API_MISSING")
    artifact = oracle.load_json_file(
        oracle.V24_ARTIFACT,
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )
    raw = oracle.POLICY_V2_REVIEWER_MAP.read_bytes()

    assert validate(artifact, raw) == (
        "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
    )
    with pytest.raises(module.IndependentVerificationError) as caught:
        validate(artifact, raw + b"\nPRIVATE_REVIEWER_CANARY")
    assert caught.value.code == "TASK8_V24_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH"


def test_independent_conservation_rejects_missing_or_mutated_byte_receipts() -> None:
    module = oracle.load_verifier()
    artifact = oracle.load_json_file(
        oracle.V24_ARTIFACT,
        "TASK8_V24_CONTRACT_ARTIFACT_NOT_IMPLEMENTED",
    )
    policy = oracle.load_json_file(
        oracle.POLICY_V2_ARTIFACT,
        "TASK8_V24_POLICY_V2_MISSING",
    )
    raw_map = oracle.POLICY_V2_REVIEWER_MAP.read_bytes()
    manifest = artifact["task8_structural_admission"]["approved_semantics_conservation_manifest"]

    missing = copy.deepcopy(manifest)
    missing[5][4].pop()
    with pytest.raises(module.IndependentVerificationError) as caught:
        module._validate_conservation_receipts(  # noqa: SLF001 - independent seam
            missing,
            artifact=artifact,
            policy=policy,
            reviewer_map_raw=raw_map,
        )
    assert caught.value.code == "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID"

    mutated = copy.deepcopy(manifest)
    mutated[0][4][0][3] = "0" * 64
    with pytest.raises(module.IndependentVerificationError) as caught:
        module._validate_conservation_receipts(  # noqa: SLF001 - independent seam
            mutated,
            artifact=artifact,
            policy=policy,
            reviewer_map_raw=raw_map,
        )
    assert caught.value.code == "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_MISMATCH"


def test_dashboard_identity_field_omission_is_named_before_fixture_comparison() -> None:
    module, artifact, fixture, policy_raw = _load_candidate_objects()
    row = _uuid_rows(artifact)["dashboard_row_id"]
    row[2].remove("coverage_record_id")
    artifact_raw, fixture_raw = oracle.refreshed_fixture_hash_bindings(artifact, fixture)

    _expect_verifier_error(
        module,
        "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING",
        artifact_raw=artifact_raw,
        policy_raw=policy_raw,
        fixture_raw=fixture_raw,
    )


def test_independent_verifier_replays_hand_written_five_stage_identity_chain() -> None:
    module = oracle.load_verifier()
    derive = getattr(module, "derive_dashboard_identity_vector", None)
    oracle.require(callable(derive), "TASK8_V24_INDEPENDENT_IDENTITY_API_MISSING")
    vector = oracle.load_golden_vector()

    assert derive(**copy.deepcopy(vector["inputs"])) == vector["expected"]


def test_dashboard_root_input_order_does_not_change_any_identity_output() -> None:
    module = oracle.load_verifier()
    derive = getattr(module, "derive_dashboard_identity_vector", None)
    oracle.require(callable(derive), "TASK8_V24_INDEPENDENT_IDENTITY_API_MISSING")
    vector = oracle.load_golden_vector()
    reversed_inputs = copy.deepcopy(vector["inputs"])
    reversed_inputs["roots"].reverse()

    assert derive(**reversed_inputs) == vector["expected"]


def test_dashboard_identity_mutation_propagates_through_every_dependent_stage() -> None:
    module = oracle.load_verifier()
    derive = getattr(module, "derive_dashboard_identity_vector", None)
    oracle.require(callable(derive), "TASK8_V24_INDEPENDENT_IDENTITY_API_MISSING")
    vector = oracle.load_golden_vector()
    baseline = derive(**copy.deepcopy(vector["inputs"]))
    changed_inputs = copy.deepcopy(vector["inputs"])
    changed_inputs["question_payload_hash"] = "f" * 64
    changed_inputs["row_trace_prefix"][-1] = "f" * 64
    changed = derive(**changed_inputs)

    for field in (
        "dashboard_row_id",
        "root_link_tuple",
        "root_projection_sha256",
        "row_payload_sha256",
        "native_identity_sha256",
        "native_output_sha256",
        "output_object_id",
    ):
        assert changed[field] != baseline[field], field


def test_root_assignment_mutation_preserves_row_and_native_identity_only() -> None:
    module = oracle.load_verifier()
    derive = getattr(module, "derive_dashboard_identity_vector", None)
    oracle.require(callable(derive), "TASK8_V24_INDEPENDENT_IDENTITY_API_MISSING")
    vector = oracle.load_golden_vector()
    baseline = derive(**copy.deepcopy(vector["inputs"]))
    changed_inputs = copy.deepcopy(vector["inputs"])
    changed_inputs["roots"][0]["root_output_object_id"] = "88888888-8888-4888-8888-888888888888"
    changed = derive(**changed_inputs)

    assert changed["dashboard_row_id"] == baseline["dashboard_row_id"]
    assert changed["native_identity_sha256"] == baseline["native_identity_sha256"]
    for field in (
        "root_link_tuple",
        "root_projection_sha256",
        "row_payload_sha256",
        "native_output_sha256",
        "output_object_id",
    ):
        assert changed[field] != baseline[field], field


def test_independent_derive_fixture_emits_the_complete_inactive_evidence_shape() -> None:
    module = oracle.load_verifier()
    derive = getattr(module, "derive_fixture", None)
    oracle.require(callable(derive), "TASK8_V24_INDEPENDENT_DERIVE_FIXTURE_API_MISSING")
    artifact_raw, policy_raw, fixture_raw = _candidate_bytes()
    artifact = oracle.load_json_bytes(
        artifact_raw,
        "TASK8_V24_CONTRACT_ARTIFACT_INVALID",
    )
    expected = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )

    observed = derive(
        artifact_raw=artifact_raw,
        policy_artifact_raw=policy_raw,
        reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
    )

    assert observed == expected
    assert tuple(observed) == (
        "artifact_schema",
        "pipeline_contract_version",
        "artifact_sha256",
        "FULL_STRICT_V24_CONTRACT_HASH",
        "component_hashes",
        "identity_hash_dependency_graph",
        "approved_semantics_conservation",
        "policy_binding",
        "counts",
        "activation_status",
    )
    assert observed["activation_status"] == "INACTIVE"
    assert observed["counts"] == {
        "semantic_step_count": len(artifact["semantic_step_registry"]["rows"]),
        "edge_rule_count": len(artifact["edge_rule_registry"]["rows"]),
        "usage_route_count": len(artifact["usage_route_registry"]["rows"]),
        "usage_extractor_count": len(artifact["usage_extractor_registry"]["rows"]),
        "provider_count": len(artifact["provider_registry"]["rows"]),
        "schema_count": len(artifact["schema_registry"]["rows"]),
        "current_ddl_table_count": len(artifact["current_ddl_registry"]["rows"]),
        "installed_tenant_rls_table_count": artifact["task8_fdd_contract"]["installed_partition"][
            "successor_tenant_rls_table_count"
        ],
        "erasure_current_relation_count": 48,
        "erasure_future_relation_count": 38,
        "erasure_complete_relation_count": 86,
        "task11_restart_cut_count": 27,
        "task11_undischarged_rule_count": 10,
    }


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(oracle.INDEPENDENT_VERIFIER), *arguments],
        cwd=oracle.REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_independent_verifier_cli_fails_with_only_a_stable_code(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "private-name-must-never-echo.json"
    bad.write_text("private-source-value", encoding="utf-8")

    result = _run_cli(
        "--artifact",
        str(bad),
        "--policy",
        str(bad),
        "--fixture",
        str(bad),
        "--reviewer-map",
        str(bad),
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "reason_code": "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
        "result": "FAILED",
    }
    assert "private-name" not in result.stdout
    assert "private-source-value" not in result.stdout


def test_independent_verifier_cli_success_is_hashes_and_counts_only() -> None:
    artifact_raw, _policy_raw, fixture_raw = _candidate_bytes()
    fixture = oracle.load_json_bytes(
        fixture_raw,
        "TASK8_V24_CONTRACT_FIXTURE_INVALID",
    )
    result = _run_cli(
        "--artifact",
        str(oracle.V24_ARTIFACT),
        "--policy",
        str(oracle.POLICY_V2_ARTIFACT),
        "--fixture",
        str(oracle.V24_FIXTURE),
        "--reviewer-map",
        str(oracle.POLICY_V2_REVIEWER_MAP),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "activation_status": "INACTIVE",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "conservation_sha256": fixture["approved_semantics_conservation"]["sha256"],
        "contract_hash": fixture["FULL_STRICT_V24_CONTRACT_HASH"],
        "counts": {
            **fixture["counts"],
            **fixture["identity_hash_dependency_graph"]["counts"],
        },
        "graph_sha256": fixture["identity_hash_dependency_graph"]["graph_sha256"],
        "policy_binding_sha256": fixture["policy_binding"]["binding_sha256"],
        "result": "VERIFIED",
        "verification_path": "TASK8_V24_INDEPENDENT_STDLIB_V1",
    }


def test_independent_cli_rejects_mutated_reviewer_map_without_echoing_canary(
    tmp_path: Path,
) -> None:
    canary = "PRIVATE_REVIEWER_PROSE_MUST_NOT_LEAK"
    mutated = tmp_path / "reviewer-map-mutated.md"
    mutated.write_bytes(oracle.POLICY_V2_REVIEWER_MAP.read_bytes() + canary.encode("utf-8"))

    result = _run_cli(
        "--artifact",
        str(oracle.V24_ARTIFACT),
        "--policy",
        str(oracle.POLICY_V2_ARTIFACT),
        "--fixture",
        str(oracle.V24_FIXTURE),
        "--reviewer-map",
        str(mutated),
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "reason_code": "TASK8_V24_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH",
        "result": "FAILED",
    }
    assert canary not in result.stdout
