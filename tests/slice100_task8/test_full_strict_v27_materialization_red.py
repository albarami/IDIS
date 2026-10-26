"""RED-first materialization contract for the inactive full-strict-v27 candidate."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from . import _task8_void_contract_hash_evidence as void_evidence
from . import _v27_oracle as oracle


def _assets() -> tuple[dict[str, Any], dict[str, Any], ModuleType]:
    policy = oracle.load_json(oracle.POLICY_V2_ARTIFACT, "TASK8_V27_POLICY_V2_MISSING")
    artifact, materializer = oracle.build_candidate()
    return artifact, policy, materializer


def test_v27_red_requires_new_materializer_and_candidate() -> None:
    artifact, _policy, materializer = _assets()
    oracle.require(
        getattr(materializer, "build_v27_from_parent_raw", None) is not None,
        "TASK8_V27_MATERIALIZER_NOT_IMPLEMENTED",
    )
    oracle.require(
        artifact["metadata"]["pipeline_contract_version"] == oracle.V27_VERSION,
        "TASK8_V27_VERSION_INVALID",
    )


def test_v27_materializer_uses_only_frozen_v21_artifact_parent() -> None:
    source = oracle.V27_MATERIALIZER.read_text(encoding="utf-8")
    oracle.require(
        "full_strict_v21_reviewed_preimage_v1.json" in source,
        "TASK8_V27_FROZEN_V21_PARENT_MISSING",
    )
    for version in range(22, 27):
        oracle.require(
            f"full_strict_v{version}_reviewed_preimage_v1.json" not in source,
            "TASK8_V27_REJECTED_ARTIFACT_SOURCE_DEPENDENCY",
        )
        oracle.require(
            f"full_strict_v{version}_contract_hash_v1.json" not in source,
            "TASK8_V27_REJECTED_FIXTURE_SOURCE_DEPENDENCY",
        )
    oracle.require(
        "materialize_full_strict_v22_preimage.py" in source,
        "TASK8_V27_SOURCE_PINNED_DELTA_HELPER_MISSING",
    )
    for version in range(23, 27):
        oracle.require(
            f"materialize_full_strict_v{version}_preimage.py" not in source,
            "TASK8_V27_REJECTED_MATERIALIZER_SOURCE_DEPENDENCY",
        )


def test_v27_normalized_source_diff_is_confined_to_count_closure_and_void_boundary() -> None:
    retired = oracle.REPO_ROOT / "scripts" / "materialize_full_strict_v26_preimage.py"
    frozen_sources = dict(void_evidence.VOID_V26_FROZEN_FILE_SHA256_BY_PATH)
    oracle.require(
        hashlib.sha256(retired.read_bytes()).hexdigest()
        == frozen_sources["scripts/materialize_full_strict_v26_preimage.py"],
        "TASK8_V27_VOID_PREDECESSOR_SOURCE_DRIFT",
    )

    def declarations(path: Path) -> dict[str, str]:
        module = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }

    predecessor = declarations(retired)
    successor = {
        name.replace("v27", "v26"): value.replace("V27", "V26").replace("v27", "v26")
        for name, value in declarations(oracle.V27_MATERIALIZER).items()
    }
    oracle.require(
        set(successor) - set(predecessor)
        == {
            "_apply_installed_rls_count_closure",
            "_reviewed_design_named_value",
            "validate_installed_rls_count_closure",
        },
        "TASK8_V27_NORMALIZED_SOURCE_ADDITION_DRIFT",
    )
    oracle.require(
        not (set(predecessor) - set(successor)),
        "TASK8_V27_NORMALIZED_SOURCE_REMOVAL_DRIFT",
    )
    oracle.require(
        {
            name
            for name in set(predecessor).intersection(successor)
            if predecessor[name] != successor[name]
        }
        == {
            "_assert_no_rejected_identifiers",
            "_retarget_metadata",
            "build_v26_from_parent_raw",
            "validate_structural_admission",
        },
        "TASK8_V27_NORMALIZED_SOURCE_CHANGE_DRIFT",
    )

    def assignments(path: Path) -> dict[str, str]:
        module = ast.parse(path.read_text(encoding="utf-8"))
        result: dict[str, str] = {}
        for node in module.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            else:
                names = []
            for name in names:
                result[name] = ast.dump(node, include_attributes=False)
        return result

    predecessor_assignments = assignments(retired)
    successor_assignments = {
        name.replace("V27", "V26").replace("v27", "v26"): value.replace("V27", "V26").replace(
            "v27", "v26"
        )
        for name, value in assignments(oracle.V27_MATERIALIZER).items()
    }
    oracle.require(
        set(successor_assignments) - set(predecessor_assignments)
        == {"_APPROVED_INSTALLED_RLS_COUNT_CLOSURE_DELTA"},
        "TASK8_V27_NORMALIZED_AUTHORITY_ADDITION_DRIFT",
    )
    oracle.require(
        not (set(predecessor_assignments) - set(successor_assignments)),
        "TASK8_V27_NORMALIZED_AUTHORITY_REMOVAL_DRIFT",
    )
    oracle.require(
        {
            name
            for name in set(predecessor_assignments).intersection(successor_assignments)
            if predecessor_assignments[name] != successor_assignments[name]
        }
        == {
            "_APPROVED_DELTA_MANIFEST",
            "_V26_ARTIFACT_SHA256",
            "_V26_CONSERVATION_SHA256",
            "_V26_GRAPH_EXTERNAL_INPUT_SHA256",
            "_V26_GRAPH_PRODUCER_SHA256",
            "__all__",
        },
        "TASK8_V27_NORMALIZED_AUTHORITY_CHANGE_DRIFT",
    )


def test_v27_parent_and_policy_inputs_are_raw_hash_pinned() -> None:
    oracle.require(
        hashlib.sha256(oracle.V21_ARTIFACT.read_bytes()).hexdigest() == oracle.V21_ARTIFACT_SHA256,
        "TASK8_V27_FROZEN_V21_RAW_HASH_DRIFT",
    )
    oracle.require(
        hashlib.sha256(oracle.POLICY_V2_ARTIFACT.read_bytes()).hexdigest()
        == oracle.POLICY_V2_ARTIFACT_SHA256,
        "TASK8_V27_POLICY_V2_RAW_HASH_DRIFT",
    )
    oracle.require(
        hashlib.sha256(oracle.POLICY_V2_REVIEWER_MAP.read_bytes()).hexdigest()
        == oracle.POLICY_V2_REVIEWER_MAP_SHA256,
        "TASK8_V27_REVIEWER_MAP_RAW_HASH_DRIFT",
    )


def test_v27_metadata_and_parent_provenance_are_exact_and_inactive() -> None:
    artifact, _policy, _materializer = _assets()
    metadata = artifact["metadata"]
    oracle.require(
        metadata["artifact_schema"] == oracle.V27_SCHEMA
        and metadata["pipeline_contract_version"] == oracle.V27_VERSION
        and metadata["append_frozen"] is True
        and metadata["activation_status"] == "INACTIVE",
        "TASK8_V27_METADATA_INVALID",
    )
    structural = artifact["task8_structural_admission"]
    oracle.require(
        structural["section_schema"] == oracle.V27_STRUCTURAL_SCHEMA,
        "TASK8_V27_STRUCTURAL_SCHEMA_INVALID",
    )
    oracle.require(
        structural["parent_provenance"]
        == {
            "parent_pipeline_contract_version": "full-strict-v21",
            "parent_artifact_sha256": oracle.V21_ARTIFACT_SHA256,
            "parent_pipeline_contract_hash": oracle.V21_CONTRACT_SHA256,
            "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
        },
        "TASK8_V27_PARENT_PROVENANCE_INVALID",
    )
    oracle.require(
        tuple(tuple(row) for row in structural["approved_delta_manifest"])
        == oracle.EXPECTED_APPROVED_DELTA_MANIFEST,
        "TASK8_V27_APPROVED_DELTA_MANIFEST_DRIFT",
    )


def test_v27_active_bytes_contain_no_rejected_version_or_identity() -> None:
    artifact, _policy, _materializer = _assets()
    raw = oracle.render_artifact(artifact)
    for version in range(22, 27):
        oracle.require(
            f"full-strict-v{version}".encode("ascii") not in raw,
            "TASK8_V27_REJECTED_VERSION_IDENTIFIER_PRESENT",
        )
    for identity in oracle.VOID_V26_ACTIVE_IDENTITY_BLOCKLIST:
        oracle.require(
            identity.encode("ascii") not in raw,
            "TASK8_V27_VOID_V26_IDENTITY_PRESENT",
        )


def test_v27_preserves_all_parent_invariant_semantic_sections() -> None:
    artifact, _policy, _materializer = _assets()
    parent = oracle.load_json(oracle.V21_ARTIFACT, "TASK8_V27_FROZEN_V21_MISSING")
    for section in (
        "semantic_step_registry",
        "edge_rule_registry",
        "usage_route_registry",
        "usage_extractor_registry",
        "provider_registry",
        "rights_index_policy_registry",
        "erasure_baseline",
        "erasure_materialized",
    ):
        oracle.require(
            artifact[section] == parent[section],
            "TASK8_V27_PARENT_SEMANTIC_SECTION_DRIFT",
        )
    for section, expected_sha256 in oracle.INVARIANT_SECTION_SHA256.items():
        oracle.require(
            oracle.canonical_sha256(artifact[section]) == expected_sha256,
            "TASK8_V27_INVARIANT_SECTION_HASH_DRIFT",
        )


def test_v27_preserves_all_68_frozen_schema_snapshots() -> None:
    artifact, _policy, _materializer = _assets()
    rows = [
        [
            row["schema_id"],
            row["schema_version"],
            row["normalized_schema_snapshot"],
            row["expected_schema_sha256"],
        ]
        for row in artifact["schema_registry"]["rows"]
    ]
    oracle.require(len(rows) == 68, "TASK8_V27_SCHEMA_SNAPSHOT_COUNT_DRIFT")
    oracle.require(
        oracle.canonical_sha256(["task8-v27-frozen-schema-snapshot-inventory-v1", rows])
        == oracle.FROZEN_SCHEMA_SNAPSHOT_INVENTORY_SHA256,
        "TASK8_V27_SCHEMA_SNAPSHOT_BYTES_DRIFT",
    )


def test_v27_reuses_equation_v3_row_and_body_bytes_exactly() -> None:
    artifact, _policy, _materializer = _assets()
    row = oracle.dashboard_authority(artifact)
    oracle.require(
        row["expected_sha256"] == oracle.EQUATION_V3_ROW_SHA256,
        "TASK8_V27_EQUATION_V3_ROW_HASH_DRIFT",
    )
    oracle.require(
        hashlib.sha256(oracle.dashboard_function_body(artifact).encode("utf-8")).hexdigest()
        == oracle.EQUATION_V3_BODY_SHA256,
        "TASK8_V27_EQUATION_V3_BODY_HASH_DRIFT",
    )


def test_v27_preserves_exact_ten_policy_requirement_literals() -> None:
    artifact, _policy, _materializer = _assets()
    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    oracle.require(
        tuple(tuple(row) for row in contract["policy_v2_requirement_literal_tuple"])
        == oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE,
        "TASK8_V27_REQUIREMENT_LITERAL_TUPLE_DRIFT",
    )
    oracle.require(
        contract["policy_v2_requirement_literal_sha256"] == oracle.V27_REQUIREMENT_LITERAL_SHA256,
        "TASK8_V27_REQUIREMENT_LITERAL_HASH_DRIFT",
    )
    oracle.require(
        contract["runtime_policy_read_allowed"] is False,
        "TASK8_V27_RUNTIME_POLICY_DISCOVERY_FORBIDDEN",
    )


def test_v27_conservation_manifest_keeps_all_eight_semantic_vectors() -> None:
    artifact, _policy, _materializer = _assets()
    rows = artifact["task8_structural_admission"]["approved_semantics_conservation_manifest"]
    oracle.require(
        tuple(row[1] for row in rows)
        == (
            "STRICT_PERIOD_ORDERING",
            "POSITIVE_MAGNITUDE_NONNEGATIVE",
            "SOURCE_NORMALIZATION_PRESERVED",
            "ERASURE_0039_TO_0038_EXACT_RESTORATION",
            "DASHBOARD_100_OF_100_CENSUS_GUARD",
            "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT",
            "FDD_FORMULA_POLICY_V2_BINDING",
            "DASHBOARD_REQUIREMENT_VERSION_DISCRIMINATION",
        ),
        "TASK8_V27_CONSERVATION_SEMANTIC_VECTOR_DRIFT",
    )


def test_v27_validators_reject_installed_count_drift() -> None:
    artifact, policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    mutated["task8_fdd_contract"]["installed_partition"]["successor_tenant_rls_table_count"] = 67
    with pytest.raises(
        materializer.MaterializationError,
        match="^TASK8_V27_INSTALLED_RLS_COUNT_DRIFT$",
    ):
        materializer.validate_installed_rls_count_closure(mutated)
    materializer.validate_installed_rls_count_closure(artifact)
    materializer.validate_dashboard_requirement_contract(artifact, policy)


def test_v27_frozen_bytes_and_exact_validator_round_trip() -> None:
    artifact, _policy, materializer = _assets()
    raw = materializer.frozen_bytes(artifact)
    oracle.require(raw == oracle.render_artifact(artifact), "TASK8_V27_RENDER_DRIFT")
    validated = materializer.validate_frozen_artifact(
        raw,
        parent_artifact_raw=oracle.V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    oracle.require(validated == artifact, "TASK8_V27_EXACT_VALIDATION_DRIFT")


def test_v27_cli_stages_noncanonical_bytes_with_no_clobber(tmp_path: Path) -> None:
    _artifact, _policy, materializer = _assets()
    output = tmp_path / "candidate.json"
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 0, "TASK8_V27_CLI_FAILED"
    )
    first = output.read_bytes()
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 0, "TASK8_V27_CLI_FAILED"
    )
    oracle.require(output.read_bytes() == first, "TASK8_V27_STAGING_IDEMPOTENCE_DRIFT")
    output.write_bytes(b"different")
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 2,
        "TASK8_V27_STAGING_NO_CLOBBER_BYPASS",
    )


def test_v27_canonical_publication_paths_remain_absent() -> None:
    oracle.require(
        not oracle.V27_CANONICAL_ARTIFACT.exists(),
        "TASK8_V27_CANONICAL_ARTIFACT_PREMATURE",
    )
    oracle.require(
        not oracle.V27_CANONICAL_FIXTURE.exists(),
        "TASK8_V27_CANONICAL_FIXTURE_PREMATURE",
    )


def test_v27_lf_contract_pins_are_exact_and_existing_bytes_are_lf_only() -> None:
    expected = {
        "scripts/materialize_full_strict_v27_preimage.py text eol=lf",
        "tests/slice100_task8/_v27_oracle.py text eol=lf",
        "tests/slice100_task8/test_full_strict_v27_materialization_red.py text eol=lf",
        "tests/slice100_task8/test_v27_installed_rls_count_red.py text eol=lf",
        "contracts/full_strict_v27_reviewed_preimage_v1.json text eol=lf",
        "tests/slice100_task8/fixtures/full_strict_v27_contract_hash_v1.json text eol=lf",
    }
    observed = set((oracle.REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines())
    oracle.require(expected <= observed, "TASK8_V27_LF_ATTRIBUTE_PIN_MISSING")
    for path in (
        oracle.V27_MATERIALIZER,
        Path(__file__),
        Path(__file__).with_name("test_v27_installed_rls_count_red.py"),
        Path(__file__).with_name("_v27_oracle.py"),
        oracle.V27_STAGED_ARTIFACT,
    ):
        raw = path.read_bytes()
        oracle.require(
            b"\r" not in raw and raw.endswith(b"\n"),
            "TASK8_V27_LF_BYTES_DRIFT",
        )


def test_v27_privacy_ast_forbids_bare_assertions() -> None:
    paths = (
        Path(__file__),
        Path(__file__).with_name("test_v27_installed_rls_count_red.py"),
        Path(__file__).with_name("_v27_oracle.py"),
        oracle.V27_MATERIALIZER,
    )
    offenders = [
        (path.name, node.lineno)
        for path in paths
        if path.is_file()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assert)
    ]
    oracle.require(not offenders, "TASK8_V27_PRIVACY_BARE_ASSERT_FORBIDDEN")


def test_v27_receipt_is_self_consistent_and_nonpublished() -> None:
    artifact, _policy, materializer = _assets()
    receipt = materializer.candidate_receipt(artifact)
    oracle.require(
        receipt["pipeline_contract_version"] == oracle.V27_VERSION
        and receipt["artifact_sha256"]
        == hashlib.sha256(oracle.render_artifact(artifact)).hexdigest()
        and receipt["activation_status"] == "INACTIVE"
        and receipt["canonical_artifact_published"] is False
        and receipt["canonical_fixture_published"] is False,
        "TASK8_V27_CANDIDATE_RECEIPT_INVALID",
    )
    json.dumps(receipt, ensure_ascii=False, allow_nan=False, sort_keys=True)


def test_v27_all_outward_candidate_identities_are_literal_and_rederived() -> None:
    artifact, policy, materializer = _assets()
    raw = materializer.frozen_bytes(artifact)
    receipt = materializer.candidate_receipt(artifact)
    structural = artifact["task8_structural_admission"]
    graph_contract = structural["identity_hash_dependency_contract"]
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require(
        len(raw) == oracle.V27_ARTIFACT_SIZE_BYTES
        and hashlib.sha256(raw).hexdigest() == oracle.V27_ARTIFACT_SHA256,
        "TASK8_V27_ARTIFACT_IDENTITY_DRIFT",
    )
    oracle.require(
        receipt["candidate_contract_sha256"] == oracle.V27_CANDIDATE_CONTRACT_SHA256,
        "TASK8_V27_CANDIDATE_CONTRACT_IDENTITY_DRIFT",
    )
    oracle.require(
        artifact["task8_fdd_contract"]["expected_sha256"] == oracle.V27_TASK8_CONTRACT_SHA256,
        "TASK8_V27_TASK8_CONTRACT_IDENTITY_DRIFT",
    )
    oracle.require(
        structural["expected_sha256"] == oracle.V27_STRUCTURAL_SHA256,
        "TASK8_V27_STRUCTURAL_IDENTITY_DRIFT",
    )
    oracle.require(
        graph_contract["producer_authority_sha256"] == oracle.V27_GRAPH_PRODUCER_SHA256
        and graph_contract["allowed_external_input_sha256"] == oracle.V27_GRAPH_EXTERNAL_SHA256
        and graph["graph_sha256"] == oracle.V27_GRAPH_SHA256,
        "TASK8_V27_GRAPH_IDENTITY_DRIFT",
    )
    oracle.require(
        structural["approved_semantics_conservation_sha256"] == oracle.V27_CONSERVATION_SHA256,
        "TASK8_V27_CONSERVATION_IDENTITY_DRIFT",
    )
    oracle.require(
        artifact["task8_fdd_contract"]["policy_binding"]["binding_sha256"]
        == oracle.V27_POLICY_BINDING_SHA256,
        "TASK8_V27_POLICY_BINDING_IDENTITY_DRIFT",
    )
    oracle.require(
        artifact["metadata"]["reviewed_design_sha256"] == oracle.V27_REVIEWED_DESIGN_SHA256,
        "TASK8_V27_REVIEWED_DESIGN_IDENTITY_DRIFT",
    )
