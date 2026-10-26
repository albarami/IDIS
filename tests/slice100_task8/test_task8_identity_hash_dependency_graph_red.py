"""RED-first whole-artifact identity/hash dependency-graph admission."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

import pytest

from . import _v23_oracle as oracle


def _derive(
    materializer: Any,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=copy.deepcopy(dict(full_strict)),
        policy_artifact=copy.deepcopy(dict(policy)),
    )
    assert isinstance(graph, dict)
    return graph


def _error_type(materializer: Any) -> type[Exception]:
    error_type = getattr(materializer, "IdentityHashDependencyError", None)
    assert isinstance(error_type, type), "TASK8_IDENTITY_HASH_ERROR_TYPE_MISSING"
    return error_type


def _reverse_mapping_order(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reverse_mapping_order(child) for key, child in reversed(tuple(value.items()))}
    if isinstance(value, list):
        return [_reverse_mapping_order(child) for child in value]
    return value


def _dashboard_internal_dependencies(
    graph: Mapping[str, Any],
    symbol: str,
) -> set[str]:
    dashboard_symbols = {
        "dashboard_row_id",
        "dashboard_root_link_id",
        "root_projection_sha256",
        "row_payload_sha256",
        "native_identity_sha256",
        "native_output_sha256",
        "output_object_id",
    }
    return (
        oracle.graph_dependencies(
            graph,
            scope=oracle.DASHBOARD_SCOPE,
            output_symbol=symbol,
        )
        & dashboard_symbols
    )


def test_held_v22_reconstructs_the_exact_reviewed_cycle_before_production_admission() -> None:
    graph = oracle.held_v22_dashboard_graph()

    assert oracle.deterministic_cycle_path(graph) == oracle.V22_DASHBOARD_CYCLE


def test_general_admission_rejects_held_v22_with_the_same_deterministic_cycle_path() -> None:
    materializer = oracle.load_materializer()

    with pytest.raises(
        _error_type(materializer),
        # Node IDs are canonical JSON; quote the entire literal rather than
        # letting regex character classes reinterpret their brackets.
        match=re.escape("TASK8_IDENTITY_HASH_CYCLE:" + "->".join(oracle.V22_DASHBOARD_CYCLE)),
    ):
        _derive(materializer, oracle.load_v22(), oracle.load_policy_v1())


def test_v23_graph_has_exact_source_pointer_closure_and_closed_canonical_shape() -> None:
    materializer = oracle.load_materializer()
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()
    graph = _derive(materializer, v23, policy)

    assert tuple(graph) == (
        "schema_version",
        "node_tuple",
        "edge_tuple",
        "leaf_tuple",
        "source_pointer_tuple",
        "counts",
        "graph_sha256",
    )
    assert graph["schema_version"] == oracle.GRAPH_SCHEMA
    independently_discovered = oracle.discover_declared_source_pointers(v23, policy)
    assert independently_discovered == oracle.EXPECTED_SOURCE_POINTER_TUPLE
    assert tuple(graph["source_pointer_tuple"]) == oracle.EXPECTED_SOURCE_POINTER_TUPLE
    assert len(graph["source_pointer_tuple"]) == 66
    node_source_pointers = [node[5] for node in graph["node_tuple"]]
    assert set(node_source_pointers) == set(graph["source_pointer_tuple"])
    assert all(
        node_source_pointers.count(pointer) == 1 for pointer in graph["source_pointer_tuple"]
    )
    for field in ("node_tuple", "edge_tuple", "leaf_tuple", "source_pointer_tuple"):
        values = graph[field]
        assert values == sorted(values, key=oracle.canonical_json), (
            f"TASK8_IDENTITY_HASH_GRAPH_ORDER_INVALID:{field}"
        )
        assert len({oracle.canonical_json(value) for value in values}) == len(values), (
            f"TASK8_IDENTITY_HASH_GRAPH_DUPLICATE:{field}"
        )
    assert all(len(node) == 6 for node in graph["node_tuple"])
    assert all(len(edge) == 3 for edge in graph["edge_tuple"])
    assert all(len(leaf) == 5 for leaf in graph["leaf_tuple"])
    assert graph["counts"] == {
        "node_count": len(graph["node_tuple"]),
        "edge_count": len(graph["edge_tuple"]),
        "leaf_count": len(graph["leaf_tuple"]),
        "source_pointer_count": len(graph["source_pointer_tuple"]),
    }
    assert graph["graph_sha256"] == oracle.canonical_sha256(
        [
            oracle.GRAPH_SCHEMA,
            graph["node_tuple"],
            graph["edge_tuple"],
            graph["leaf_tuple"],
            graph["source_pointer_tuple"],
        ]
    )
    node_ids = {oracle.graph_node_id(node) for node in graph["node_tuple"]}
    adjacency = {node_id: set() for node_id in node_ids}
    for derived, dependency, _token_pointer in graph["edge_tuple"]:
        assert derived in node_ids, "TASK8_IDENTITY_HASH_GRAPH_DANGLING_DERIVED"
        assert dependency in node_ids, "TASK8_IDENTITY_HASH_GRAPH_DANGLING_DEPENDENCY"
        adjacency[derived].add(dependency)
    assert oracle.deterministic_cycle_path(adjacency) is None


def test_v23_dashboard_dependency_dag_is_exact_and_contains_no_self_edge() -> None:
    graph = _derive(oracle.load_materializer(), oracle.load_v23(), oracle.load_policy_v2())

    assert _dashboard_internal_dependencies(graph, "dashboard_row_id") == set()
    assert _dashboard_internal_dependencies(graph, "dashboard_root_link_id") == {"dashboard_row_id"}
    assert _dashboard_internal_dependencies(graph, "root_projection_sha256") == {
        "dashboard_row_id",
        "dashboard_root_link_id",
    }
    assert _dashboard_internal_dependencies(graph, "row_payload_sha256") == {
        "dashboard_row_id",
        "root_projection_sha256",
    }
    assert _dashboard_internal_dependencies(graph, "native_identity_sha256") == {"dashboard_row_id"}
    assert _dashboard_internal_dependencies(graph, "native_output_sha256") == {
        "dashboard_row_id",
        "root_projection_sha256",
        "row_payload_sha256",
    }
    assert _dashboard_internal_dependencies(graph, "output_object_id") == {
        "native_identity_sha256",
        "native_output_sha256",
    }
    assert not any(derived == dependency for derived, dependency, _ in graph["edge_tuple"])


def test_graph_derivation_is_repeatable_and_mapping_iteration_order_independent() -> None:
    materializer = oracle.load_materializer()
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()

    observations = [_derive(materializer, v23, policy) for _ in range(3)]
    observations.append(
        _derive(
            materializer,
            _reverse_mapping_order(v23),
            _reverse_mapping_order(policy),
        )
    )

    assert all(observation == observations[0] for observation in observations[1:])


def test_graph_digest_changes_when_a_governed_dashboard_uuid_input_changes() -> None:
    materializer = oracle.load_materializer()
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()
    baseline = _derive(materializer, v23, policy)
    changed = copy.deepcopy(v23)
    row = next(
        row for row in changed["uuid_namespace_registry"]["rows"] if row[0] == "dashboard_row_id"
    )
    row[2][-1] = "question_payload_hash_mutation_vector"

    mutated = _derive(materializer, changed, policy)

    assert mutated["graph_sha256"] != baseline["graph_sha256"]


def test_synthetic_non_dashboard_cycle_is_rejected_by_the_general_guard() -> None:
    materializer = oracle.load_materializer()
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()
    mutated = copy.deepcopy(v23)
    mutated["uuid_namespace_registry"]["rows"].extend(
        [
            [
                "synthetic_a_id",
                "a3a014b3-f10e-510b-b97e-35c85a040d20",
                ["synthetic-a-v1", "tenant_id", "synthetic_b_id"],
            ],
            [
                "synthetic_b_id",
                "7c48b448-35e6-5585-b90d-74fe7880b34c",
                ["synthetic-b-v1", "tenant_id", "synthetic_a_id"],
            ],
        ]
    )

    with pytest.raises(_error_type(materializer), match="TASK8_IDENTITY_HASH_CYCLE") as caught:
        _derive(materializer, mutated, policy)

    assert "synthetic_a_id" in str(caught.value)
    assert "synthetic_b_id" in str(caught.value)


def test_conflicting_producer_for_one_governed_symbol_is_rejected() -> None:
    materializer = oracle.load_materializer()
    mutated = copy.deepcopy(oracle.load_v23())
    mutated["uuid_namespace_registry"]["rows"].append(
        [
            "dashboard_row_id",
            "9a2d2565-9b42-5e55-994a-fc29a291ab68",
            list(oracle.DASHBOARD_ROW_NAME_TUPLE),
        ]
    )

    with pytest.raises(
        _error_type(materializer),
        match="TASK8_IDENTITY_HASH_CONFLICTING_PRODUCER:dashboard_row_id",
    ):
        _derive(materializer, mutated, oracle.load_policy_v2())


def test_direct_self_reference_is_rejected_before_cycle_traversal() -> None:
    materializer = oracle.load_materializer()
    mutated = copy.deepcopy(oracle.load_v23())
    row = next(
        row for row in mutated["uuid_namespace_registry"]["rows"] if row[0] == "dashboard_row_id"
    )
    row[2].append("dashboard_row_id")

    with pytest.raises(
        _error_type(materializer),
        match="TASK8_IDENTITY_HASH_SELF_EDGE:dashboard_row_id",
    ):
        _derive(materializer, mutated, oracle.load_policy_v2())


def test_unknown_hash_grammar_is_rejected_instead_of_becoming_hash_input() -> None:
    materializer = oracle.load_materializer()
    mutated = copy.deepcopy(oracle.load_v23())
    row = next(
        row
        for row in mutated["digest_recipe_registry"]["rows"]
        if row[0] == "pipeline_contract_hash"
    )
    row[1] = "EVAL(os.getenv('TASK8_MUST_NOT_BE_READ'))"

    with pytest.raises(
        _error_type(materializer),
        match="TASK8_IDENTITY_HASH_GRAMMAR_UNKNOWN",
    ):
        _derive(materializer, mutated, oracle.load_policy_v2())


def test_unresolved_governed_hash_reference_is_not_downgraded_to_scalar_leaf() -> None:
    materializer = oracle.load_materializer()
    mutated = copy.deepcopy(oracle.load_v23())
    row = next(
        row for row in mutated["uuid_namespace_registry"]["rows"] if row[0] == "dashboard_row_id"
    )
    row[2].append("undeclared_governed_sha256")

    with pytest.raises(
        _error_type(materializer),
        match="TASK8_IDENTITY_HASH_REFERENCE_UNRESOLVED:undeclared_governed_sha256",
    ):
        _derive(materializer, mutated, oracle.load_policy_v2())


def test_new_unrecognized_preimage_shape_is_rejected_by_exact_pointer_manifest() -> None:
    materializer = oracle.load_materializer()
    mutated = copy.deepcopy(oracle.load_v23())
    mutated["task8_fdd_contract"]["rogue_hash_preimage"] = [
        "rogue-v1",
        "tenant_id",
        "deal_id",
    ]

    with pytest.raises(
        _error_type(materializer),
        match="TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN",
    ):
        _derive(materializer, mutated, oracle.load_policy_v2())


def test_runtime_pipeline_contract_hash_is_a_pinned_leaf_not_an_artifact_back_edge() -> None:
    graph = _derive(oracle.load_materializer(), oracle.load_v23(), oracle.load_policy_v2())
    matches = [leaf for leaf in graph["leaf_tuple"] if leaf[2] == "pipeline_contract_hash"]

    assert matches
    assert all(leaf[3] == "PINNED_CONTRACT_INPUT" for leaf in matches)
    contract_definition_nodes = {
        oracle.graph_node_id(node)
        for node in graph["node_tuple"]
        if node[1] == "CONTRACT_DEFINITION" and node[4] == "pipeline_contract_hash"
    }
    runtime_nodes = {
        oracle.graph_node_id(node)
        for node in graph["node_tuple"]
        if node[1] == "RUNTIME_VALUE" and node[4] == "pipeline_contract_hash"
    }
    assert not any(
        derived in runtime_nodes and dependency in contract_definition_nodes
        for derived, dependency, _ in graph["edge_tuple"]
    )


def _identity_inputs() -> dict[str, Any]:
    return {
        "tenant_id": "00000000-0000-4000-8000-000000000001",
        "deal_id": "00000000-0000-4000-8000-000000000002",
        "run_id": "00000000-0000-4000-8000-000000000003",
        "pipeline_contract_version": "full-strict-v23",
        "pipeline_contract_hash": "a" * 64,
        "coverage_record_id": "mc_0123456789abcdef01234567",
        "methodology_version_record_id": "00000000-0000-4000-8000-000000000004",
        "methodology_question_record_id": "00000000-0000-4000-8000-000000000005",
        "question_payload_hash": "b" * 64,
        "row_disposition": "ANSWERED",
        "disposition_reason_code": None,
        "row_trace_prefix": [
            1,
            1,
            "P&L",
            11,
            "P&L!A11",
            "00000000-0000-4000-8000-000000000004",
            "financial_dd:version",
            "00000000-0000-4000-8000-000000000005",
            "methodology-question-id",
            "b" * 64,
        ],
        "result_records": [["result", "c" * 64]],
        "answer_values": [["answer", "d" * 64]],
        "task_gaps": [],
        "calculations": [["calculation", "e" * 64]],
        "roots": [
            {
                "root_kind": "RESULT",
                "root_ordinal": 1,
                "root_output_object_id": "00000000-0000-4000-8000-000000000006",
                "root_output_object_kind": "METHODOLOGY_RESULT",
            }
        ],
    }


def test_dashboard_identity_vector_replays_and_matches_independent_derivation() -> None:
    materializer = oracle.load_materializer()
    inputs = _identity_inputs()
    expected = oracle.expected_dashboard_identity_vector(**inputs)
    observations = [
        materializer.derive_dashboard_identity_vector(**copy.deepcopy(inputs)) for _ in range(3)
    ]

    assert observations == [expected, expected, expected]
    assert expected["output_object_id"] != inputs["roots"][0]["root_output_object_id"]


def test_row_coordinate_mutation_changes_row_identity_and_every_dependent_value() -> None:
    materializer = oracle.load_materializer()
    baseline_inputs = _identity_inputs()
    changed_inputs = copy.deepcopy(baseline_inputs)
    changed_inputs["question_payload_hash"] = "f" * 64
    changed_inputs["row_trace_prefix"][-1] = "f" * 64
    baseline = materializer.derive_dashboard_identity_vector(**baseline_inputs)
    changed = materializer.derive_dashboard_identity_vector(**changed_inputs)

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


def test_foreign_root_mutation_preserves_row_identity_but_changes_only_downstream_values() -> None:
    materializer = oracle.load_materializer()
    baseline_inputs = _identity_inputs()
    changed_inputs = copy.deepcopy(baseline_inputs)
    changed_inputs["roots"][0]["root_output_object_id"] = "00000000-0000-4000-8000-000000000007"
    baseline = materializer.derive_dashboard_identity_vector(**baseline_inputs)
    changed = materializer.derive_dashboard_identity_vector(**changed_inputs)

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
