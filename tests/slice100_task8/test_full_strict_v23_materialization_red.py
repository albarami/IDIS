"""RED-first admission for the approved Task-8.1 v23 correction."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from typing import Any

from . import _v23_oracle as oracle


def _dashboard_uuid_row(v23: dict[str, Any]) -> list[Any]:
    rows = [row for row in v23["uuid_namespace_registry"]["rows"] if row[0] == "dashboard_row_id"]
    assert len(rows) == 1, "TASK8_V23_DASHBOARD_ROW_UUID_AUTHORITY_INVALID"
    return rows[0]


def test_rejected_v22_identifiers_are_pinned_before_successor_materialization() -> None:
    assert hashlib.sha256(oracle.V22_ARTIFACT.read_bytes()).hexdigest() == (
        oracle.V22_ARTIFACT_SHA256
    )
    if oracle.V22_FIXTURE.is_file():
        assert hashlib.sha256(oracle.V22_FIXTURE.read_bytes()).hexdigest() == (
            oracle.V22_FIXTURE_SHA256
        )
        fixture = oracle.load_json(oracle.V22_FIXTURE, "TASK8_V22_FIXTURE_INVALID")
        assert fixture["FULL_STRICT_V22_CONTRACT_HASH"] == oracle.V22_CONTRACT_SHA256


def test_rejected_v22_fixture_is_removed_and_all_three_identifiers_are_blocklisted() -> None:
    assert not oracle.V22_FIXTURE.exists(), "TASK8_VOID_V22_FIXTURE_STILL_PUBLISHED"
    assert oracle.TASK8_VOID_EVIDENCE.is_file(), "TASK8_VOID_V22_BLOCKLIST_NOT_IMPLEMENTED"
    spec = importlib.util.spec_from_file_location(
        "_task8_void_contract_hash_evidence_red",
        oracle.TASK8_VOID_EVIDENCE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert tuple(module.TASK8_VOID_CONTRACT_HASHES) == oracle.VOID_V22_HASHES


def test_v23_materializer_and_frozen_artifact_use_the_approved_successor_identities() -> None:
    materializer = oracle.load_materializer()
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()

    assert v23["metadata"]["artifact_schema"] == oracle.V23_ARTIFACT_SCHEMA
    assert v23["metadata"]["pipeline_contract_version"] == oracle.V23_CONTRACT_VERSION
    assert v23["metadata"]["append_frozen"] is True
    assert v23["metadata"]["activation_status"] == "INACTIVE"
    assert policy["metadata"]["artifact_schema"] == oracle.POLICY_V2_ARTIFACT_SCHEMA
    assert policy["metadata"]["policy_id"] == oracle.POLICY_V2_ID

    assert hasattr(materializer, "build_v23")
    assert hasattr(materializer, "validate_frozen_artifact")
    validated = materializer.validate_frozen_artifact(
        oracle.V23_ARTIFACT.read_bytes(),
        policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
        policy_fixture_raw=oracle.POLICY_V2_FIXTURE.read_bytes(),
    )
    assert validated == v23


def test_v23_binds_the_exact_dashboard_row_uuid_and_acyclic_hash_preimages() -> None:
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()

    _kind, namespace, name_tuple = _dashboard_uuid_row(v23)
    assert namespace == oracle.DASHBOARD_ROW_NAMESPACE
    assert tuple(name_tuple) == oracle.DASHBOARD_ROW_NAME_TUPLE

    recipes = {row[0]: row[1] for row in v23["digest_recipe_registry"]["rows"]}
    assert recipes["methodology_truth_dashboard_root_projection_sha256"] == (
        'H(["methodology-truth-dashboard-root-projection-v2",tenant_id,deal_id,'
        "run_id,dashboard_row_id,coverage_record_id,row_disposition,count,"
        "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
        "root_output_object_kind,dashboard_root_link_id)]])"
    )
    financial = policy["financial_output_schema"]
    assert financial["row_payload_preimage"] == [
        "financial-diligence-row-v2",
        "$identity_and_trace_without_self_output",
        "$disposition",
        "$disposition_reason_code",
        "$result_records",
        "$answer_values",
        "$task_gaps",
        "$calculations",
        "$root_projection_sha256",
    ]
    assert financial["identity_and_trace_tuple"] == [
        "row_ordinal",
        "sheet_ordinal",
        "source_sheet",
        "source_row_number",
        "source_anchor_cell",
        "methodology_version_record_id",
        "methodology_version_id",
        "methodology_question_record_id",
        "methodology_question_id",
        "question_payload_hash",
        "coverage_record_id",
        "dashboard_row_id",
    ]
    assert "dashboard_output_object_id" not in financial["identity_and_trace_tuple"]


def test_v23_policy_binding_retargets_every_identity_bearing_reference_to_v2() -> None:
    v23 = oracle.load_v23()
    policy = oracle.load_policy_v2()
    binding = v23["task8_fdd_contract"]["policy_binding"]

    assert binding["policy_id"] == oracle.POLICY_V2_ID
    assert binding["artifact_relative_path"] == "contracts/fdd_formula_policy_v2.json"
    assert (
        binding["artifact_sha256"]
        == hashlib.sha256(oracle.POLICY_V2_ARTIFACT.read_bytes()).hexdigest()
    )
    assert (
        binding["policy_sha256"] == policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"]
    )
    assert "fdd-formula-policy-v1" not in str(v23["task8_fdd_contract"])


def test_v22_to_v23_preserves_the_full_semantic_invariance_wall() -> None:
    v22 = oracle.load_v22()
    v23 = oracle.load_v23()

    for section in oracle.SEMANTICALLY_INVARIANT_SECTIONS:
        assert v23[section] == v22[section], f"TASK8_V23_SEMANTIC_DRIFT:{section}"
    assert len(v23["semantic_step_registry"]["rows"]) == 28
    assert len(v23["edge_rule_registry"]["rows"]) == 48
    assert len(v23["usage_route_registry"]["rows"]) == 1_756
    assert len(v23["usage_extractor_registry"]["rows"]) == 19
    assert len(v23["provider_registry"]["rows"]) == 18
    assert len(v23["schema_registry"]["rows"]) == len(v22["schema_registry"]["rows"])
    old_erasure = v22["erasure_execution_registry"]["rows"][0]
    new_erasure = v23["erasure_execution_registry"]["rows"][0]
    for field in (
        "current_relation_tuple",
        "future_relation_tuple",
        "complete_relation_tuple",
        # Erasure ordering is frozen in the executable bodies; the reviewed
        # erasure row has never declared a synthetic delete_order_tuple field.
        "current_body_sql_utf8_hex_tuple",
        "future_body_sql_utf8_hex_tuple",
    ):
        assert new_erasure[field] == old_erasure[field], f"TASK8_V23_ERASURE_DRIFT:{field}"


def test_v23_fixture_binds_the_general_graph_digest_and_counts() -> None:
    fixture = oracle.load_json(oracle.V23_FIXTURE, "TASK8_V23_FIXTURE_NOT_IMPLEMENTED")
    graph = oracle.fixture_graph_contract(fixture)

    assert graph["schema_version"] == oracle.GRAPH_SCHEMA
    assert oracle.SHA256_RE.fullmatch(graph["graph_sha256"])
    assert graph["counts"] == {
        "node_count": len(graph["node_tuple"]),
        "edge_count": len(graph["edge_tuple"]),
        "leaf_count": len(graph["leaf_tuple"]),
        "source_pointer_count": len(graph["source_pointer_tuple"]),
    }
    expected_digest = oracle.canonical_sha256(
        [
            oracle.GRAPH_SCHEMA,
            graph["node_tuple"],
            graph["edge_tuple"],
            graph["leaf_tuple"],
            graph["source_pointer_tuple"],
        ]
    )
    assert graph["graph_sha256"] == expected_digest


def test_void_v22_hashes_are_absent_from_v23_active_admission_surfaces() -> None:
    active_paths = (
        oracle.V23_ARTIFACT,
        oracle.V23_FIXTURE,
        oracle.V23_MATERIALIZER,
        oracle.REPO_ROOT / "scripts" / "generate_task8_contract_fixtures.py",
        oracle.REPO_ROOT / "scripts" / "publish_task8_contract_artifact_gate.py",
        oracle.REPO_ROOT / "contracts" / "contract_lock.json",
    )
    for path in active_paths:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        for value in oracle.VOID_V22_HASHES:
            assert value.encode("ascii") not in raw, f"TASK8_VOID_V22_ACTIVE:{path.name}"
