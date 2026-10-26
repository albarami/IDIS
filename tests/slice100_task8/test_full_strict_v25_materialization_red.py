"""RED-first materialization contract for the approved full-strict-v25 candidate."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from . import _task8_void_contract_hash_evidence as void_evidence
from . import _v25_oracle as oracle


def _assets() -> tuple[dict[str, Any], dict[str, Any], Any]:
    policy = oracle.load_policy_v2()
    artifact, materializer = oracle.build_candidate()
    return artifact, policy, materializer


def test_v25_local_privacy_ast_forbids_bare_assertions() -> None:
    paths = (
        Path(__file__),
        Path(__file__).with_name("test_v25_dashboard_requirement_matrix_red.py"),
        Path(__file__).with_name("_v25_oracle.py"),
    )
    offenders = [
        (path.name, node.lineno)
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assert)
    ]
    oracle.require(not offenders, "TASK8_V25_PRIVACY_BARE_ASSERT_FORBIDDEN")


def test_v25_private_comparison_reports_only_sha_and_size() -> None:
    canary = "private-prose-canary"
    with pytest.raises(
        oracle.V25OracleError,
        match="^TASK8_V25_PRIVATE_EQUALITY_TEST:",
    ) as captured:
        oracle.require_private_equal(
            canary, "different-private-value", "TASK8_V25_PRIVATE_EQUALITY_TEST"
        )
    message = str(captured.value)
    oracle.require(canary not in message, "TASK8_V25_PRIVATE_HELPER_LEAKED_OBSERVED")
    oracle.require(
        "different-private-value" not in message,
        "TASK8_V25_PRIVATE_HELPER_LEAKED_EXPECTED",
    )
    oracle.require(
        message.count("sha256=") == 2 and message.count("size=") == 2,
        "TASK8_V25_PRIVATE_HELPER_FINGERPRINT_INVALID",
    )


def test_v25_materializer_sources_are_lf_pinned_under_autocrlf() -> None:
    relative_paths = (
        "scripts/materialize_full_strict_v22_preimage.py",
        "scripts/materialize_full_strict_v25_preimage.py",
    )
    attributes = (oracle.REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for relative_path in relative_paths:
        oracle.require(
            f"{relative_path} text eol=lf" in attributes,
            "TASK8_V25_SOURCE_LF_ATTRIBUTE_MISSING",
        )
        oracle.require(
            b"\r\n" not in (oracle.REPO_ROOT / relative_path).read_bytes(),
            "TASK8_V25_SOURCE_CHECKOUT_NOT_LF",
        )
    observed = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=true",
            "check-attr",
            "text",
            "eol",
            "--",
            *relative_paths,
        ],
        cwd=oracle.REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for relative_path in relative_paths:
        oracle.require(
            f"{relative_path}: text: set" in observed,
            "TASK8_V25_SOURCE_TEXT_ATTRIBUTE_INVALID",
        )
        oracle.require(
            f"{relative_path}: eol: lf" in observed,
            "TASK8_V25_SOURCE_EOL_ATTRIBUTE_INVALID",
        )
    oracle.require(
        hashlib.sha256((oracle.REPO_ROOT / relative_paths[0]).read_bytes()).hexdigest()
        == "78add79548bd4ef47f26290f63325f4b0744f659cfcc0f8c47deb30ebc282c29",
        "TASK8_V25_APPROVED_DELTA_HELPER_RAW_HASH_DRIFT",
    )


def test_v25_materializer_keeps_canonical_publication_targets_absent() -> None:
    oracle.require(oracle.V25_MATERIALIZER.is_file(), "TASK8_V25_MATERIALIZER_NOT_IMPLEMENTED")
    oracle.require(
        not oracle.V25_ARTIFACT.exists(),
        "TASK8_V25_CANONICAL_ARTIFACT_PREPUBLISHED",
    )
    oracle.require(
        not oracle.V25_FIXTURE.exists(),
        "TASK8_V25_CANONICAL_FIXTURE_PREPUBLISHED",
    )
    oracle.require(
        oracle.V25_ARTIFACT != oracle.V24_ARTIFACT,
        "TASK8_V25_CANONICAL_PATH_COLLISION",
    )


def test_v25_derives_only_from_exact_frozen_v21_and_records_parent_provenance() -> None:
    artifact, _policy, materializer = _assets()
    metadata = artifact["metadata"]
    structural = artifact["task8_structural_admission"]
    oracle.require(
        metadata["artifact_schema"] == oracle.V25_SCHEMA,
        "TASK8_V25_ARTIFACT_SCHEMA_INVALID",
    )
    oracle.require(
        metadata["pipeline_contract_version"] == oracle.V25_VERSION,
        "TASK8_V25_VERSION_INVALID",
    )
    oracle.require(metadata["append_frozen"] is True, "TASK8_V25_APPEND_FROZEN_INVALID")
    oracle.require(
        metadata["activation_status"] == "INACTIVE",
        "TASK8_V25_ACTIVATION_STATUS_INVALID",
    )
    oracle.require(
        structural["section_schema"] == oracle.V25_STRUCTURAL_SCHEMA,
        "TASK8_V25_STRUCTURAL_SCHEMA_INVALID",
    )
    oracle.require_private_equal(
        structural["parent_provenance"],
        {
            "parent_pipeline_contract_version": "full-strict-v21",
            "parent_artifact_sha256": oracle.V21_ARTIFACT_SHA256,
            "parent_pipeline_contract_hash": oracle.V21_CONTRACT_SHA256,
            "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
        },
        "TASK8_V25_PARENT_PROVENANCE_INVALID",
    )

    validated = materializer.validate_frozen_parent_bytes(oracle.V21_ARTIFACT.read_bytes())
    oracle.require(
        validated["metadata"]["pipeline_contract_version"] == "full-strict-v21",
        "TASK8_V25_PARENT_VERSION_INVALID",
    )
    for rejected in (
        oracle.V22_ARTIFACT,
        oracle.V23_ARTIFACT,
        oracle.V24_ARTIFACT,
    ):
        with pytest.raises(
            materializer.MaterializationError,
            match="TASK8_V25_PARENT_MUST_BE_FROZEN_V21",
        ):
            materializer.validate_frozen_parent_bytes(rejected.read_bytes())


def test_v25_rebuild_is_exact_and_does_not_read_void_candidate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, _policy, materializer = _assets()
    original = Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path.resolve() in {
            oracle.V22_ARTIFACT.resolve(),
            oracle.V23_ARTIFACT.resolve(),
            oracle.V24_ARTIFACT.resolve(),
        }:
            raise AssertionError("TASK8_V25_REJECTED_PARENT_READ")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    rebuilt = materializer.build_v25_from_parent_raw(
        parent_artifact_raw=original(oracle.V21_ARTIFACT),
        policy_artifact_raw=original(oracle.POLICY_V2_ARTIFACT),
        reviewer_map_raw=original(oracle.POLICY_V2_REVIEWER_MAP),
    )
    oracle.require_private_equal(
        oracle.render_artifact(rebuilt),
        oracle.render_artifact(artifact),
        "TASK8_V25_REBUILD_RENDER_DRIFT",
    )
    oracle.require_private_equal(rebuilt, artifact, "TASK8_V25_REBUILD_OBJECT_DRIFT")


def test_v25_requirement_literal_is_policy_derived_hash_bound_and_not_runtime_discovered() -> None:
    artifact, policy, materializer = _assets()
    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    oracle.require(
        contract["schema_version"] == oracle.V25_REQUIREMENT_CONTRACT_SCHEMA,
        "TASK8_V25_REQUIREMENT_SCHEMA_INVALID",
    )
    oracle.require(
        contract["discriminator_field"] == "methodology_version_id",
        "TASK8_V25_REQUIREMENT_DISCRIMINATOR_INVALID",
    )
    oracle.require(
        contract["policy_v2_methodology_version_id"] == oracle.POLICY_V2_METHODOLOGY_VERSION_ID,
        "TASK8_V25_REQUIREMENT_VERSION_ID_INVALID",
    )
    oracle.require(
        contract["policy_id"] == "fdd-formula-policy-v2",
        "TASK8_V25_REQUIREMENT_POLICY_ID_INVALID",
    )
    oracle.require(
        contract["policy_sha256"] == oracle.POLICY_V2_SHA256,
        "TASK8_V25_REQUIREMENT_POLICY_HASH_INVALID",
    )
    oracle.require_private_equal(
        contract["policy_v2_requirement_key_tuple"],
        sorted(oracle.POLICY_KEYS),
        "TASK8_V25_POLICY_KEY_TUPLE_INVALID",
    )
    oracle.require_private_equal(
        contract["legacy_requirement_key_tuple"],
        sorted(oracle.LEGACY_KEYS),
        "TASK8_V25_LEGACY_KEY_TUPLE_INVALID",
    )
    oracle.require_private_equal(
        contract["policy_v2_requirement_literal_tuple"],
        [list(row) for row in oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE],
        "TASK8_V25_REQUIREMENT_LITERAL_TUPLE_INVALID",
    )
    oracle.require_private_equal(
        oracle.policy_requirement_tuple(policy),
        oracle.EXPECTED_POLICY_REQUIREMENT_TUPLE,
        "TASK8_V25_POLICY_REQUIREMENT_TUPLE_INVALID",
    )
    oracle.require(
        contract["policy_v2_requirement_literal_sha256"]
        == oracle.expected_requirement_literal_sha256(),
        "TASK8_V25_REQUIREMENT_LITERAL_HASH_INVALID",
    )
    oracle.require(
        contract["normative_source_mode"] == "HASH_BOUND_ARTIFACT_LITERAL_ONLY",
        "TASK8_V25_NORMATIVE_SOURCE_MODE_INVALID",
    )
    oracle.require(
        contract["runtime_policy_read_allowed"] is False,
        "TASK8_V25_RUNTIME_POLICY_READ_INVALID",
    )
    binding = artifact["task8_fdd_contract"]["policy_binding"]
    oracle.require_private_equal(
        binding,
        {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_relative_path": "contracts/fdd_formula_policy_v2.json",
            "artifact_sha256": oracle.POLICY_V2_ARTIFACT_SHA256,
            "policy_sha256": oracle.POLICY_V2_SHA256,
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json"
            ),
            "fixture_sha256": ("b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"),
            "fixture_policy_sha256": oracle.POLICY_V2_SHA256,
            "reviewer_map_relative_path": (
                "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
            ),
            "reviewer_map_sha256": oracle.POLICY_V2_REVIEWER_MAP_SHA256,
            "binding_sha256": ("8915fc53da0eda52e256ad8da6fcfbe614cc066eea57c60a85f69bfb3bd357a2"),
        },
        "TASK8_V25_POLICY_BINDING_INVALID",
    )
    reviewed_design = bytes.fromhex(artifact["metadata"]["reviewed_design_utf8_hex"]).decode(
        "utf-8"
    )
    oracle.require_private_contains(
        "fdd-formula-policy-v2",
        reviewed_design,
        "TASK8_V25_REVIEWED_DESIGN_POLICY_V2_MISSING",
    )
    oracle.require_private_contains(
        oracle.POLICY_V2_SHA256,
        reviewed_design,
        "TASK8_V25_REVIEWED_DESIGN_POLICY_HASH_MISSING",
    )
    oracle.require_private_not_contains(
        "fdd-formula-policy-v1",
        reviewed_design,
        "TASK8_V25_REVIEWED_DESIGN_POLICY_V1_RETAINED",
    )
    materializer.validate_dashboard_requirement_contract(artifact, policy)


def test_v25_equation_v3_is_the_only_semantic_delta_and_other_governed_bodies_are_exact() -> None:
    v25, _policy, materializer = _assets()
    v24 = oracle.load_v24()
    structural = v25["task8_structural_admission"]
    oracle.require_private_equal(
        structural["approved_delta_manifest"][-1],
        [
            13,
            "REPLACE_DASHBOARD_REQUIREMENT_BINDING_WITH_VERSION_DISCRIMINATED_V3",
            "SEMANTIC_V25",
        ],
        "TASK8_V25_APPROVED_DELTA_INVALID",
    )
    v24_manifest = v24["task8_structural_admission"]["approved_semantics_conservation_manifest"]
    v25_manifest = structural["approved_semantics_conservation_manifest"]
    oracle.require(len(v24_manifest) == 7, "TASK8_V25_BASE_MANIFEST_COUNT_INVALID")
    oracle.require(len(v25_manifest) == 8, "TASK8_V25_MANIFEST_COUNT_INVALID")

    def normalized_semantic_prefix(row: list[Any]) -> list[Any]:
        result = copy.deepcopy(row[:4])
        if result[1] == "DASHBOARD_100_OF_100_CENSUS_GUARD":
            result[2] = ["SQL_AUTHORITY", "dashboard-root-equation-v3"]
        return result

    oracle.require_private_equal(
        [normalized_semantic_prefix(row) for row in v24_manifest],
        [row[:4] for row in v25_manifest[:7]],
        "TASK8_V25_SEMANTIC_PREFIX_DRIFT",
    )

    def receipt_topology(row: list[Any]) -> list[list[str]]:
        return [receipt[:3] for receipt in row[4]]

    oracle.require_private_equal(
        [receipt_topology(row) for row in v24_manifest],
        [receipt_topology(row) for row in v25_manifest[:7]],
        "TASK8_V25_RECEIPT_TOPOLOGY_DRIFT",
    )
    oracle.require_private_equal(
        v25_manifest[7][:4],
        [
            8,
            "DASHBOARD_REQUIREMENT_VERSION_DISCRIMINATION",
            ["SQL_AUTHORITY", "dashboard-root-equation-v3"],
            [
                "POLICY_V2_EXACT_FOUR_FIELD_TEN_LITERAL_TUPLES",
                "LEGACY_EXACT_TWO_FIELD_REQUIREMENTS",
                "FORMULA_TYPE_EQUALS_CALCULATION_COLUMN",
                "CROSS_VERSION_REJECTED",
            ],
        ],
        "TASK8_V25_REQUIREMENT_CONSERVATION_ROW_INVALID",
    )

    rendered = oracle.canonical_bytes(v25)
    for label in (b"full-strict-v22", b"full-strict-v23", b"full-strict-v24"):
        oracle.require_private_not_contains(
            label,
            rendered,
            "TASK8_V25_VOID_LABEL_IN_CANDIDATE",
        )
    for identifier in void_evidence.TASK8_VOID_CONTRACT_HASHES:
        oracle.require_private_not_contains(
            identifier.encode("ascii"),
            rendered,
            "TASK8_V25_VOID_HASH_IN_CANDIDATE",
        )

    decoded_string_leaves: list[str] = []

    def collect_decoded_string_leaves(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                collect_decoded_string_leaves(child)
        elif isinstance(value, list):
            for child in value:
                collect_decoded_string_leaves(child)
        elif isinstance(value, str) and len(value) % 2 == 0:
            with suppress(ValueError, UnicodeError):
                decoded_string_leaves.append(bytes.fromhex(value).decode("utf-8"))

    collect_decoded_string_leaves(v25)
    decoded = "\n".join(decoded_string_leaves)
    for marker in (
        "full-strict-v22",
        "full-strict-v23",
        "full-strict-v24",
        "fdd-formula-policy-v1",
        "fdd_formula_policy_v1",
        *void_evidence.TASK8_VOID_CONTRACT_HASHES,
    ):
        oracle.require_private_not_contains(
            marker,
            decoded,
            "TASK8_V25_VOID_MARKER_IN_DECODED_CANDIDATE",
        )

    v24_by_id = {row["authority_id"]: row for row in v24["sql_authority_registry"]["rows"]}
    v25_by_id = {row["authority_id"]: row for row in v25["sql_authority_registry"]["rows"]}
    oracle.require(
        "dashboard-root-equation-v2" not in v25_by_id,
        "TASK8_V25_OLD_EQUATION_RETAINED",
    )
    oracle.require(
        "dashboard-root-equation-v3" in v25_by_id,
        "TASK8_V25_EQUATION_V3_MISSING",
    )
    for authority_id in (
        "current-output-exact-one-v1",
        "dashboard-partial-native-root-exact-one-v1",
    ):
        oracle.require_private_equal(
            v25_by_id[authority_id]["body_sql_utf8_hex_tuple"],
            v24_by_id[authority_id]["body_sql_utf8_hex_tuple"],
            "TASK8_V25_UNCHANGED_SQL_BODY_DRIFT",
        )
    v24_rows = v24["sql_authority_registry"]["rows"]
    v25_rows = v25["sql_authority_registry"]["rows"]
    oracle.require(
        len(v24_rows) == len(v25_rows) == 29,
        "TASK8_V25_SQL_AUTHORITY_COUNT_DRIFT",
    )
    oracle.require_private_equal(
        [
            ordinal
            for ordinal, (before, after) in enumerate(zip(v24_rows, v25_rows, strict=True))
            if before["body_sql_utf8_hex_tuple"] != after["body_sql_utf8_hex_tuple"]
        ],
        [0],
        "TASK8_V25_SQL_BODY_CHANGE_SET_DRIFT",
    )
    oracle.require(
        v24_rows[18]["authority_id"] == "current-output-exact-one-v1",
        "TASK8_V25_CURRENT_OUTPUT_BODY_PIN_DRIFT",
    )
    oracle.require(
        v24_rows[28]["authority_id"] == "dashboard-partial-native-root-exact-one-v1",
        "TASK8_V25_PARTIAL_NATIVE_BODY_PIN_DRIFT",
    )
    oracle.require_private_equal(
        v25["task8_fdd_contract"]["partial_exact_one_contract"]["downgrade_sql_utf8_hex_tuple"],
        v24["task8_fdd_contract"]["partial_exact_one_contract"]["downgrade_sql_utf8_hex_tuple"],
        "TASK8_V25_PARTIAL_DOWNGRADE_BODY_DRIFT",
    )
    oracle.require_private_equal(
        oracle.normalized_non_equation_sections(v25),
        oracle.normalized_non_equation_sections(v24),
        "TASK8_V25_NON_EQUATION_SECTION_DRIFT",
    )


def test_v25_general_graph_authorities_are_literal_and_candidate_independent() -> None:
    artifact, policy, materializer = _assets()
    structural = artifact["task8_structural_admission"]
    contract = structural["identity_hash_dependency_contract"]
    oracle.require(
        contract["source_discovery"] == "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS",
        "TASK8_V25_GRAPH_SOURCE_DISCOVERY_INVALID",
    )
    oracle.require(
        contract["producer_authority_sha256"] == oracle.V25_GRAPH_PRODUCER_SHA256,
        "TASK8_V25_GRAPH_PRODUCER_AUTHORITY_INVALID",
    )
    oracle.require(
        contract["allowed_external_input_sha256"] == oracle.V25_GRAPH_EXTERNAL_INPUT_SHA256,
        "TASK8_V25_GRAPH_EXTERNAL_AUTHORITY_INVALID",
    )
    oracle.require(
        structural["approved_semantics_conservation_sha256"] == oracle.V25_CONSERVATION_SHA256,
        "TASK8_V25_CONSERVATION_HASH_INVALID",
    )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require_private_equal(
        graph["counts"],
        {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V25_GRAPH_COUNTS_INVALID",
    )


def test_v25_general_graph_rejects_synthetic_non_dashboard_cycle() -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    rows = {row[0]: row for row in candidate["uuid_namespace_registry"]["rows"]}
    rows["step_id"][2].append("source_lineage_object_id")
    rows["source_lineage_object_id"][2].append("step_id")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_CYCLE",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=candidate,
            policy_artifact=policy,
        )


def test_v25_general_graph_rejects_unresolved_governed_reference() -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    step_output = next(
        row for row in candidate["uuid_namespace_registry"]["rows"] if row[0] == "step_output_id"
    )
    step_output[2].append("undeclared_governed_sha256")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_GOVERNED_REFERENCE_UNRESOLVED",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=candidate,
            policy_artifact=policy,
        )


def test_v25_general_graph_rejects_duplicate_producer_before_edges() -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    rows = candidate["task8_structural_admission"]["identity_hash_dependency_contract"][
        "producer_authority_tuple"
    ]
    rows[1][5] = rows[0][5]
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_DUPLICATE_PRODUCER",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=candidate,
            policy_artifact=policy,
        )


@pytest.mark.parametrize(
    "invalid_symbol",
    ("cycle-a", "123", "", "H", "hex", "float8send"),
)
def test_v25_general_graph_rejects_non_identifier_or_reserved_producer_symbols(
    invalid_symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    future_candidate = copy.deepcopy(artifact)
    digest_row = next(
        row
        for row in future_candidate["digest_recipe_registry"]["rows"]
        if row[0] == "pipeline_contract_hash"
    )
    digest_row[0] = invalid_symbol
    monkeypatch.setattr(materializer, "_V25_GRAPH_PRODUCER_SHA256", "")
    monkeypatch.setattr(materializer, "_V25_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID$",
    ):
        materializer._build_identity_hash_dependency_contract(
            future_candidate,
            policy,
        )

    manifest_candidate = copy.deepcopy(artifact)
    producer_row = next(
        row
        for row in manifest_candidate["task8_structural_admission"][
            "identity_hash_dependency_contract"
        ]["producer_authority_tuple"]
        if row[5] == "pipeline_contract_hash"
    )
    producer_row[5] = invalid_symbol
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID$",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=manifest_candidate,
            policy_artifact=policy,
        )


def test_v25_general_graph_requires_exact_frozen_digest_operator_after_valid_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, policy, materializer = _assets()
    future_candidate = copy.deepcopy(artifact)
    future_candidate["digest_recipe_registry"]["rows"][0][0] = "digest_operator"
    monkeypatch.setattr(materializer, "_V25_GRAPH_PRODUCER_SHA256", "")
    monkeypatch.setattr(materializer, "_V25_GRAPH_EXTERNAL_INPUT_SHA256", "")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID$",
    ):
        materializer._build_identity_hash_dependency_contract(
            future_candidate,
            policy,
        )


def test_v25_general_graph_allows_only_exact_frozen_digest_operator_declaration() -> None:
    artifact, policy, materializer = _assets()
    producer_rows = artifact["task8_structural_admission"]["identity_hash_dependency_contract"][
        "producer_authority_tuple"
    ]
    operator_rows = [row for row in producer_rows if row[5] == "H"]
    oracle.require_private_equal(
        operator_rows,
        [
            [
                "full_strict:#/digest_recipe_registry/rows/0",
                "full_strict",
                "CONTRACT_DEFINITION",
                "DIGEST_RECIPE_REGISTRY",
                "DIGEST_RECIPE",
                "H",
                "#/1",
            ]
        ],
        "TASK8_V25_FROZEN_DIGEST_OPERATOR_AUTHORITY_DRIFT",
    )
    oracle.require_private_equal(
        artifact["digest_recipe_registry"]["rows"][0],
        ["H", "lowercase_hex(SHA256(CJ(value)))"],
        "TASK8_V25_FROZEN_DIGEST_OPERATOR_VALUE_DRIFT",
    )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require(
        all(not edge[2].endswith("#H") for edge in graph["edge_tuple"]),
        "TASK8_V25_FROZEN_DIGEST_OPERATOR_BECAME_REFERENCABLE",
    )
    external_rows = artifact["task8_structural_admission"]["identity_hash_dependency_contract"][
        "allowed_external_input_tuple"
    ]
    oracle.require_private_equal(
        [row for row in external_rows if row[2] in {"hex", "float8send"}],
        [
            [
                "full_strict:#/digest_recipe_registry/rows/7",
                "$",
                "float8send",
                "SCALAR_OR_FOREIGN_INPUT",
            ],
            [
                "full_strict:#/digest_recipe_registry/rows/7",
                "$",
                "hex",
                "SCALAR_OR_FOREIGN_INPUT",
            ],
        ],
        "TASK8_V25_PARSER_BUILTIN_EXTERNAL_LEAF_DRIFT",
    )


@pytest.mark.parametrize("mutation", ("authority", "value"))
def test_v25_general_graph_rejects_frozen_digest_operator_mutation(
    mutation: str,
) -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    if mutation == "authority":
        operator_row = next(
            row
            for row in candidate["task8_structural_admission"]["identity_hash_dependency_contract"][
                "producer_authority_tuple"
            ]
            if row[5] == "H"
        )
        operator_row[3] = "MUTATED_DIGEST_SCOPE"
    else:
        candidate["digest_recipe_registry"]["rows"][0][1] = "lowercase_hex(SHA256(value))"
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="^TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID$",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=candidate,
            policy_artifact=policy,
        )


def test_v25_general_graph_rejects_unknown_recipe_grammar() -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    projection = next(
        row
        for row in candidate["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    )
    projection[1] = "EVAL(unreviewed_dynamic_recipe)"
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID",
    ):
        materializer.derive_identity_hash_dependency_graph(
            full_strict_artifact=candidate,
            policy_artifact=policy,
        )


def test_v25_no_clobber_and_frozen_validation_are_exact(tmp_path: Path) -> None:
    artifact, _policy, materializer = _assets()
    raw = oracle.render_artifact(artifact)
    validated = materializer.validate_frozen_artifact(
        raw,
        parent_artifact_raw=oracle.V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    oracle.require_private_equal(validated, artifact, "TASK8_V25_FROZEN_VALIDATION_DRIFT")
    oracle.require(
        hashlib.sha256(materializer.frozen_bytes(validated)).hexdigest()
        == hashlib.sha256(raw).hexdigest(),
        "TASK8_V25_FROZEN_BYTES_HASH_DRIFT",
    )

    output = tmp_path / "full_strict_v25_reviewed_preimage_v1.staging.json"
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 0,
        "TASK8_V25_STAGING_CREATE_FAILED",
    )
    first = output.read_bytes()
    oracle.require_private_equal(first, raw, "TASK8_V25_STAGING_BYTES_DRIFT")
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 0,
        "TASK8_V25_STAGING_IDEMPOTENCE_FAILED",
    )
    oracle.require_private_equal(
        output.read_bytes(),
        first,
        "TASK8_V25_STAGING_IDEMPOTENCE_BYTES_DRIFT",
    )
    output.write_text(json.dumps({"wrong": True}), encoding="utf-8")
    oracle.require(
        materializer.main(["--staging-output", str(output)]) == 2,
        "TASK8_V25_STAGING_NO_CLOBBER_EXIT_INVALID",
    )

    oracle.require(
        materializer.main(["--staging-output", str(oracle.V25_ARTIFACT)]) == 2,
        "TASK8_V25_CANONICAL_WRITE_EXIT_INVALID",
    )
    oracle.require(
        not oracle.V25_ARTIFACT.exists(),
        "TASK8_V25_CANONICAL_ARTIFACT_PREPUBLISHED",
    )


def test_v25_staging_exclusive_create_rejects_race_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, _policy, materializer = _assets()
    raw = oracle.render_artifact(artifact)
    output = tmp_path / "exclusive-race.staging.json"
    racer = b"race-winner-private-canary"
    original_open = Path.open
    raced = False

    def racing_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        nonlocal raced
        if path == output and mode == "xb" and not raced:
            raced = True
            with original_open(path, "xb") as stream:
                stream.write(racer)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(
        materializer.MaterializationError,
        match="^TASK8_V25_STAGING_NO_CLOBBER$",
    ):
        materializer._write_staging(output, raw)
    oracle.require_private_equal(
        output.read_bytes(),
        racer,
        "TASK8_V25_STAGING_RACE_OVERWROTE_WINNER",
    )


def test_v25_cli_failures_emit_stable_code_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    materializer = oracle.load_materializer()
    missing = tmp_path / "missing-parent-private-canary.json"
    monkeypatch.setattr(materializer, "_V21_ARTIFACT", missing)
    oracle.require(materializer.main([]) == 2, "TASK8_V25_MISSING_PARENT_EXIT_INVALID")
    captured = capsys.readouterr()
    oracle.require(captured.out == "", "TASK8_V25_FAILURE_STDOUT_NOT_EMPTY")
    oracle.require(
        captured.err == "TASK8_V25_PARENT_MUST_BE_FROZEN_V21\n",
        "TASK8_V25_MISSING_PARENT_STDERR_INVALID",
    )
    oracle.require(
        "private-canary" not in captured.err,
        "TASK8_V25_MISSING_PARENT_PATH_LEAKED",
    )

    monkeypatch.setattr(materializer, "_V21_ARTIFACT", oracle.V21_ARTIFACT)

    def fail_with_detail(**_kwargs: Any) -> dict[str, Any]:
        raise materializer.MaterializationError(
            "TASK8_V25_CLI_TEST_FAILURE",
            "private-detail-canary",
        )

    monkeypatch.setattr(materializer, "build_v25_from_parent_raw", fail_with_detail)
    oracle.require(materializer.main([]) == 2, "TASK8_V25_DETAIL_FAILURE_EXIT_INVALID")
    captured = capsys.readouterr()
    oracle.require(captured.out == "", "TASK8_V25_FAILURE_STDOUT_NOT_EMPTY")
    oracle.require(
        captured.err == "TASK8_V25_CLI_TEST_FAILURE\n",
        "TASK8_V25_DETAIL_FAILURE_STDERR_INVALID",
    )
    oracle.require(
        "private-detail-canary" not in captured.err,
        "TASK8_V25_DETAIL_FAILURE_LEAKED",
    )

    def fail_with_oserror(**_kwargs: Any) -> dict[str, Any]:
        raise OSError("private-oserror-canary")

    monkeypatch.setattr(materializer, "build_v25_from_parent_raw", fail_with_oserror)
    oracle.require(materializer.main([]) == 2, "TASK8_V25_OSERROR_EXIT_INVALID")
    captured = capsys.readouterr()
    oracle.require(captured.out == "", "TASK8_V25_FAILURE_STDOUT_NOT_EMPTY")
    oracle.require(
        captured.err == "TASK8_V25_IO_FAILURE\n",
        "TASK8_V25_OSERROR_STDERR_INVALID",
    )
    oracle.require(
        "private-oserror-canary" not in captured.err,
        "TASK8_V25_OSERROR_LEAKED",
    )

    oracle.require(
        materializer.main(["--private-argument-canary"]) == 2,
        "TASK8_V25_ARGUMENT_FAILURE_EXIT_INVALID",
    )
    captured = capsys.readouterr()
    oracle.require(captured.out == "", "TASK8_V25_FAILURE_STDOUT_NOT_EMPTY")
    oracle.require(
        captured.err == "TASK8_V25_CLI_ARGUMENT_INVALID\n",
        "TASK8_V25_ARGUMENT_FAILURE_STDERR_INVALID",
    )
    oracle.require(
        "private-argument-canary" not in captured.err,
        "TASK8_V25_ARGUMENT_FAILURE_LEAKED",
    )

    def fail_unexpected(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("private-unexpected-canary")

    monkeypatch.setattr(materializer, "build_v25_from_parent_raw", fail_unexpected)
    oracle.require(
        materializer.main([]) == 2,
        "TASK8_V25_UNEXPECTED_FAILURE_EXIT_INVALID",
    )
    captured = capsys.readouterr()
    oracle.require(captured.out == "", "TASK8_V25_FAILURE_STDOUT_NOT_EMPTY")
    oracle.require(
        captured.err == "TASK8_V25_UNEXPECTED_FAILURE\n",
        "TASK8_V25_UNEXPECTED_FAILURE_STDERR_INVALID",
    )
    oracle.require(
        "private-unexpected-canary" not in captured.err,
        "TASK8_V25_UNEXPECTED_FAILURE_LEAKED",
    )


@pytest.mark.parametrize("error", (KeyboardInterrupt(), SystemExit(7)))
def test_v25_cli_privacy_boundary_preserves_base_exceptions(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = oracle.load_materializer()

    def fail_with_base_exception(**_kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(
        materializer,
        "build_v25_from_parent_raw",
        fail_with_base_exception,
    )
    with pytest.raises(type(error)):
        materializer.main([])


def test_v25_candidate_receipt_is_hash_bound_without_prepublishing_fixture() -> None:
    artifact, _policy, materializer = _assets()
    raw = oracle.render_artifact(artifact)
    receipt = materializer.candidate_receipt(artifact)
    oracle.require(
        receipt["receipt_schema"] == "full-strict-v25-candidate-receipt-v1",
        "TASK8_V25_CANDIDATE_RECEIPT_SCHEMA_INVALID",
    )
    oracle.require(
        receipt["pipeline_contract_version"] == oracle.V25_VERSION,
        "TASK8_V25_CANDIDATE_RECEIPT_VERSION_INVALID",
    )
    oracle.require(
        receipt["artifact_sha256"] == hashlib.sha256(raw).hexdigest(),
        "TASK8_V25_CANDIDATE_RECEIPT_ARTIFACT_HASH_INVALID",
    )
    oracle.require(
        receipt["activation_status"] == "INACTIVE",
        "TASK8_V25_CANDIDATE_RECEIPT_ACTIVATION_INVALID",
    )
    oracle.require(
        len(receipt["candidate_contract_sha256"]) == 64,
        "TASK8_V25_CANDIDATE_RECEIPT_CONTRACT_HASH_INVALID",
    )
    oracle.require(
        not oracle.V25_FIXTURE.exists(),
        "TASK8_V25_CANONICAL_FIXTURE_PREPUBLISHED",
    )


def test_v25_source_contains_no_void_artifact_base_or_runtime_policy_read() -> None:
    source = oracle.V25_MATERIALIZER.read_text(encoding="utf-8")
    for forbidden in (
        "materialize_full_strict_v24",
        "build_v24",
        "validate_v24",
        "full_strict_v24_reviewed_preimage_v1.json",
        "full_strict_v24_contract_hash_v1.json",
    ):
        oracle.require_private_not_contains(
            forbidden,
            source,
            "TASK8_V25_VOID_SOURCE_DEPENDENCY",
        )
    candidate, _materializer = oracle.build_candidate()
    oracle.require_private_not_contains(
        "COALESCE",
        oracle.dashboard_function_body(candidate),
        "TASK8_V25_SQL_COALESCE_FORBIDDEN",
    )
