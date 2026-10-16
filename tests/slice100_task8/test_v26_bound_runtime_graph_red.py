"""RED-first closure for full-strict-v26 bound runtime graph leaves."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import pytest

from . import _v26_oracle as oracle

BOUND_RUNTIME_TOKENS = (
    "$identity_and_trace_without_self_output",
    "$disposition",
    "$disposition_reason_code",
    "$result_records",
    "$answer_values",
    "$task_gaps",
    "$calculations",
)
ROW_PAYLOAD_POINTER = "fdd_formula_policy:#/financial_output_schema/row_payload_preimage"


def _assets() -> tuple[dict[str, Any], dict[str, Any], Any]:
    policy = oracle.load_policy_v2()
    artifact, materializer = oracle.build_candidate()
    return artifact, policy, materializer


def _row_payload(policy: Mapping[str, Any]) -> list[object]:
    value = policy["financial_output_schema"]["row_payload_preimage"]
    oracle.require(isinstance(value, list), "TASK8_V26_ROW_PAYLOAD_NOT_A_LIST")
    return list(value)


def test_v26_retains_exact_seven_prefixed_bound_runtime_inputs_atomically() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    external_rows = [
        row
        for row in contract["allowed_external_input_tuple"]
        if row[3] == "BOUND_RUNTIME_INPUT"
    ]
    oracle.require_private_equal(
        external_rows,
        [
            [ROW_PAYLOAD_POINTER, f"$[{ordinal}]", token, "BOUND_RUNTIME_INPUT"]
            for ordinal, token in enumerate(BOUND_RUNTIME_TOKENS, start=1)
        ],
        "TASK8_V26_BOUND_RUNTIME_EXTERNAL_ROWS_INVALID",
    )

    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    leaves = [
        row
        for row in graph["leaf_tuple"]
        if row[3] == "BOUND_RUNTIME_INPUT"
    ]
    oracle.require_private_equal(
        [(row[1], row[2], row[3]) for row in leaves],
        [
            (f"$[{ordinal}]", token, "BOUND_RUNTIME_INPUT")
            for ordinal, token in enumerate(BOUND_RUNTIME_TOKENS, start=1)
        ],
        "TASK8_V26_BOUND_RUNTIME_GRAPH_LEAVES_INVALID",
    )
    oracle.require_private_equal(
        graph["counts"],
        {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V26_GRAPH_COUNTS_DRIFT",
    )


def test_v26_global_dollar_source_contract_is_exactly_nine_closed_locations() -> None:
    artifact, policy, materializer = _assets()
    pointers = materializer._discover_sources(artifact, policy)
    observed: list[tuple[str, str, str, str]] = []
    for pointer in pointers:
        producer = materializer._producer_authority_row(pointer, artifact, policy)
        value = materializer._graph_pointer_value(pointer, artifact, policy)
        payload = materializer._relative_pointer_value(value, producer[6])
        observed.extend(
            (pointer, token_path, producer[2], scalar)
            for token_path, scalar in materializer._flatten(payload)
            if isinstance(scalar, str) and "$" in scalar
        )
    oracle.require_private_equal(
        observed,
        [
            (ROW_PAYLOAD_POINTER, f"$[{ordinal}]", "RUNTIME_VALUE", token)
            for ordinal, token in enumerate(
                (*BOUND_RUNTIME_TOKENS, "$root_projection_sha256"),
                start=1,
            )
        ]
        + [(
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "CONTRACT_DEFINITION",
            "$FDD_FORMULA_POLICY_V2_SHA256",
        )],
        "TASK8_V26_DOLLAR_SOURCE_CENSUS_DRIFT",
    )
    oracle.require(
        materializer._leaf_authority("$unexpected") == "SCALAR_OR_FOREIGN_INPUT",
        "TASK8_V26_ARBITRARY_DOLLAR_LEAF_CLASSIFIED_BOUND",
    )


def test_v26_bound_runtime_tokens_bypass_unprefixed_producer_capture() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    producer_symbols = {row[5] for row in contract["producer_authority_tuple"]}
    for ordinal, token in enumerate(BOUND_RUNTIME_TOKENS, start=1):
        references = materializer._governed_scalar_references(
            token,
            source_pointer=ROW_PAYLOAD_POINTER,
            token_path=f"$[{ordinal}]",
            source_layer="RUNTIME_VALUE",
            producer_symbols=producer_symbols,
        )
        oracle.require_private_equal(
            references,
            ((token, "BOUND_RUNTIME_INPUT"),),
            "TASK8_V26_BOUND_RUNTIME_REFERENCE_INVALID",
        )
        oracle.require(
            token != token.removeprefix("$"),
            "TASK8_V26_BOUND_RUNTIME_PREFIX_LOST",
        )
        oracle.require(
            token.removeprefix("$") not in {symbol for symbol, _authority in references},
            "TASK8_V26_BOUND_RUNTIME_PRODUCER_CAPTURED",
        )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    row_payload_derived_id = next(
        row[0]
        for row in graph["leaf_tuple"]
        if row[4] == ROW_PAYLOAD_POINTER and row[3] == "BOUND_RUNTIME_INPUT"
    )
    row_payload_dependencies = {
        row[1]
        for row in graph["edge_tuple"]
        if row[0] == row_payload_derived_id
    }
    for token in BOUND_RUNTIME_TOKENS:
        oracle.require(
            all(
                token.removeprefix("$") not in dependency
                for dependency in row_payload_dependencies
            ),
            "TASK8_V26_BOUND_RUNTIME_PRODUCER_CAPTURED",
        )


def test_v26_preserves_root_projection_edge_and_contract_placeholder_authority() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    producer_symbols = {row[5] for row in contract["producer_authority_tuple"]}
    oracle.require_private_equal(
        materializer._governed_scalar_references(
            "$root_projection_sha256",
            source_pointer=ROW_PAYLOAD_POINTER,
            token_path="$[8]",
            source_layer="RUNTIME_VALUE",
            producer_symbols=producer_symbols,
        ),
        (("root_projection_sha256", "SCALAR_OR_FOREIGN_INPUT"),),
        "TASK8_V26_ROOT_PROJECTION_REFERENCE_CLASSIFICATION_DRIFT",
    )
    oracle.require_private_equal(
        materializer._governed_scalar_references(
            "$FDD_FORMULA_POLICY_V2_SHA256",
            source_pointer=(
                "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage"
            ),
            token_path="$[3]",
            source_layer="CONTRACT_DEFINITION",
            producer_symbols=producer_symbols,
        ),
        (("FDD_FORMULA_POLICY_V2_SHA256", "SCALAR_OR_FOREIGN_INPUT"),),
        "TASK8_V26_POLICY_PLACEHOLDER_CLASSIFICATION_DRIFT",
    )
    external_matches = [
        row
        for row in contract["allowed_external_input_tuple"]
        if row[2] == "FDD_FORMULA_POLICY_V2_SHA256"
    ]
    oracle.require_private_equal(
        external_matches,
        [[
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "FDD_FORMULA_POLICY_V2_SHA256",
            "SCALAR_OR_FOREIGN_INPUT",
        ]],
        "TASK8_V26_POLICY_PLACEHOLDER_EXTERNAL_ROW_DRIFT",
    )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    root_projection_edges = [
        row
        for row in graph["edge_tuple"]
        if row[2].endswith("#root_projection_sha256")
    ]
    oracle.require(
        len(root_projection_edges) == 3,
        "TASK8_V26_ROOT_PROJECTION_EDGE_COUNT_DRIFT",
    )
    placeholder_leaves = [
        row
        for row in graph["leaf_tuple"]
        if row[2] == "FDD_FORMULA_POLICY_V2_SHA256"
    ]
    oracle.require_private_equal(
        [(row[1], row[2], row[3], row[4]) for row in placeholder_leaves],
        [(
            "$[3]#FDD_FORMULA_POLICY_V2_SHA256",
            "FDD_FORMULA_POLICY_V2_SHA256",
            "SCALAR_OR_FOREIGN_INPUT",
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
        )],
        "TASK8_V26_POLICY_PLACEHOLDER_LEAF_DRIFT",
    )


@pytest.mark.parametrize(
    "invalid",
    (
        "$",
        "$$calculations",
        "$calculations.more",
        "$calculations-more",
        "$calculations ",
        "$9calculations",
    ),
)
def test_v26_rejects_malformed_bound_runtime_tokens_with_stable_code(invalid: str) -> None:
    _artifact, _policy, materializer = _assets()
    producer_symbols: set[str] = set()
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID$",
    ):
        materializer._governed_scalar_references(
            invalid,
            source_pointer=ROW_PAYLOAD_POINTER,
            token_path="$[7]",
            source_layer="RUNTIME_VALUE",
            producer_symbols=producer_symbols,
        )


@pytest.mark.parametrize(
    "capturing_symbol",
    ("calculations", "FDD_FORMULA_POLICY_V2_SHA256"),
)
def test_v26_recognized_source_capture_rejects_reserved_producer_before_graph_build(
    capturing_symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    digest_row = next(
        row
        for row in mutated["digest_recipe_registry"]["rows"]
        if row[0] == "pipeline_contract_hash"
    )
    digest_row[0] = capturing_symbol
    monkeypatch.setattr(materializer, "_V26_GRAPH_PRODUCER_SHA256", "")
    monkeypatch.setattr(materializer, "_V26_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID$",
    ):
        materializer._build_identity_hash_dependency_contract(mutated, policy)


@pytest.mark.parametrize(
    ("pointer", "token_path", "replacement", "expected_code"),
    (
        (
            ROW_PAYLOAD_POINTER,
            "$[1]",
            "$disposition",
            "TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID",
        ),
        (
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "$calculations",
            "TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID",
        ),
        (
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "$$FDD_FORMULA_POLICY_V2_SHA256",
            "TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID",
        ),
        (
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "$",
            "TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID",
        ),
        (
            "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
            "$[3]",
            "$FDD_FORMULA_POLICY_V2_SHA256.more",
            "TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID",
        ),
        (
            "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
            "$[0]",
            "$unexpected",
            "TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID",
        ),
    ),
)
def test_v26_rejects_cross_position_cross_layer_and_malformed_dollar_sources(
    pointer: str,
    token_path: str,
    replacement: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    mutated_policy = copy.deepcopy(policy)
    source = materializer._graph_pointer_value(pointer, artifact, mutated_policy)
    oracle.require(isinstance(source, list), "TASK8_V26_DOLLAR_SOURCE_TEST_SHAPE_INVALID")
    source[int(token_path[2:-1])] = replacement
    monkeypatch.setattr(materializer, "_V26_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match=rf"^{expected_code}$",
    ):
        materializer._build_identity_hash_dependency_contract(artifact, mutated_policy)


@pytest.mark.parametrize(
    ("pointer", "token_path", "replacement"),
    (
        (ROW_PAYLOAD_POINTER, "$[0]", "prefix$unexpected"),
        (ROW_PAYLOAD_POINTER, "$[0]", "calculations$"),
        (ROW_PAYLOAD_POINTER, "$[0]", "x$$y"),
        (
            "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
            "$[0]",
            "prefix$unexpected",
        ),
        (
            "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
            "$[0]",
            "calculations$",
        ),
        (
            "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
            "$[0]",
            "x$$y",
        ),
    ),
)
def test_v26_rejects_embedded_suffix_and_multiple_dollars_in_every_source_layer(
    pointer: str,
    token_path: str,
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    mutated_policy = copy.deepcopy(policy)
    source = materializer._graph_pointer_value(pointer, artifact, mutated_policy)
    oracle.require(isinstance(source, list), "TASK8_V26_DOLLAR_SOURCE_TEST_SHAPE_INVALID")
    source[int(token_path[2:-1])] = replacement
    monkeypatch.setattr(materializer, "_V26_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID$",
    ):
        materializer._build_identity_hash_dependency_contract(artifact, mutated_policy)


def test_v26_rejects_self_declared_hidden_dependency_payload_selector() -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    rows = mutated["task8_structural_admission"]["identity_hash_dependency_contract"][
        "producer_authority_tuple"
    ]
    row_payload_producer = next(row for row in rows if row[0] == ROW_PAYLOAD_POINTER)
    row_payload_producer[6] = "#/0"
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_DRIFT$",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=mutated,
            policy_artifact=policy,
        )


@pytest.mark.parametrize(
    "replacement",
    (
        "calculations",
        "$",
        "$$calculations",
        "$calculations.more",
        "$calculations-more",
        "$calculations ",
        "$9calculations",
    ),
)
def test_v26_rejects_frozen_row_payload_binding_drift(
    replacement: str,
) -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(policy)
    payload = _row_payload(mutated)
    payload[-2] = replacement
    mutated["financial_output_schema"]["row_payload_preimage"] = payload
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=mutated,
        )


def test_v26_rejects_bound_runtime_authority_relabel() -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    contract = mutated["task8_structural_admission"]["identity_hash_dependency_contract"]
    target = next(
        row for row in contract["allowed_external_input_tuple"] if row[2] == "$calculations"
    )
    target[3] = "SCALAR_OR_FOREIGN_INPUT"
    contract["allowed_external_input_sha256"] = materializer._canonical_sha256(
        [materializer._GRAPH_EXTERNAL_DOMAIN, contract["allowed_external_input_tuple"]]
    )
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_EXTERNAL_INPUT_DRIFT$",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=mutated,
            policy_artifact=policy,
        )


def test_v26_rejects_unconsumed_external_authority_row_as_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    contract = mutated["task8_structural_admission"]["identity_hash_dependency_contract"]
    contract["allowed_external_input_tuple"].append(
        [
            ROW_PAYLOAD_POINTER,
            "$[99]",
            "orphaned_external_input",
            "SCALAR_OR_FOREIGN_INPUT",
        ]
    )
    contract["allowed_external_input_sha256"] = materializer._canonical_sha256(
        [materializer._GRAPH_EXTERNAL_DOMAIN, contract["allowed_external_input_tuple"]]
    )
    monkeypatch.setattr(materializer, "_V26_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_EXTERNAL_INPUT_ORPHANED$",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=mutated,
            policy_artifact=policy,
        )


def test_v26_keeps_frozen_digest_operator_and_reserved_producer_closure() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    rows = contract["producer_authority_tuple"]
    oracle.require(
        sum(tuple(row) == materializer._FROZEN_DIGEST_OPERATOR_PRODUCER_ROW for row in rows) == 1,
        "TASK8_V26_FROZEN_DIGEST_OPERATOR_CARDINALITY_INVALID",
    )
    producer_symbols = {row[5] for row in rows}
    oracle.require("hex" not in producer_symbols, "TASK8_V26_HEX_PRODUCER_ADMITTED")
    oracle.require(
        "float8send" not in producer_symbols,
        "TASK8_V26_FLOAT8SEND_PRODUCER_ADMITTED",
    )


def test_v26_graph_contract_and_graph_hashes_are_literal_and_rederived() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    oracle.require(
        contract["producer_authority_sha256"] == oracle.V26_GRAPH_PRODUCER_SHA256,
        "TASK8_V26_GRAPH_PRODUCER_HASH_INVALID",
    )
    oracle.require(
        contract["allowed_external_input_sha256"] == oracle.V26_GRAPH_EXTERNAL_INPUT_SHA256,
        "TASK8_V26_GRAPH_EXTERNAL_HASH_INVALID",
    )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require(
        graph["graph_sha256"] == oracle.V26_GRAPH_SHA256,
        "TASK8_V26_GRAPH_HASH_INVALID",
    )
