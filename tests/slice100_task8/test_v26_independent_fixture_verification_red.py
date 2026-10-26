"""RED-first contract for the independent full-strict-v26 fixture verifier."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from . import _v26_independent_oracle as oracle

_ROW_PAYLOAD_POINTER = "fdd_formula_policy:#/financial_output_schema/row_payload_preimage"
_BOUND_RUNTIME_TOKENS = (
    "$identity_and_trace_without_self_output",
    "$disposition",
    "$disposition_reason_code",
    "$result_records",
    "$answer_values",
    "$task_gaps",
    "$calculations",
)
_POLICY_PLACEHOLDER_POINTER = (
    "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage"
)
_DOLLAR_SEMANTIC_TUPLE = (
    *(
        (
            _ROW_PAYLOAD_POINTER,
            f"$[{ordinal}]",
            token,
            "RUNTIME_VALUE",
            ((token, "BOUND_RUNTIME_INPUT"),),
        )
        for ordinal, token in enumerate(_BOUND_RUNTIME_TOKENS, start=1)
    ),
    (
        _ROW_PAYLOAD_POINTER,
        "$[8]",
        "$root_projection_sha256",
        "RUNTIME_VALUE",
        (("root_projection_sha256", "SCALAR_OR_FOREIGN_INPUT"),),
    ),
    (
        _POLICY_PLACEHOLDER_POINTER,
        "$[3]",
        "$FDD_FORMULA_POLICY_V2_SHA256",
        "CONTRACT_DEFINITION",
        (("FDD_FORMULA_POLICY_V2_SHA256", "SCALAR_OR_FOREIGN_INPUT"),),
    ),
)


def _raw_inputs() -> tuple[bytes, bytes, bytes, bytes]:
    return oracle.candidate_bytes()


def _decode(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    oracle.require(isinstance(value, dict), "TASK8_V26_TEST_JSON_INVALID")
    return cast(dict[str, Any], value)


def _derive(
    *,
    parent_raw: bytes | None = None,
    policy_raw: bytes | None = None,
    reviewer_map_raw: bytes | None = None,
    artifact_raw: bytes | None = None,
) -> tuple[Any, dict[str, Any], bytes]:
    parent, policy, reviewer, artifact = _raw_inputs()
    verifier = oracle.load_verifier()
    fixture = verifier.derive_fixture(
        parent_artifact_raw=parent if parent_raw is None else parent_raw,
        policy_artifact_raw=policy if policy_raw is None else policy_raw,
        reviewer_map_raw=reviewer if reviewer_map_raw is None else reviewer_map_raw,
        v26_artifact_raw=artifact if artifact_raw is None else artifact_raw,
    )
    oracle.require(isinstance(fixture, dict), "TASK8_V26_TEST_FIXTURE_INVALID")
    fixture_raw = verifier.derive_fixture_bytes(
        parent_artifact_raw=parent if parent_raw is None else parent_raw,
        policy_artifact_raw=policy if policy_raw is None else policy_raw,
        reviewer_map_raw=reviewer if reviewer_map_raw is None else reviewer_map_raw,
        v26_artifact_raw=artifact if artifact_raw is None else artifact_raw,
    )
    oracle.require(fixture_raw == oracle.render(fixture), "TASK8_V26_TEST_FIXTURE_BYTES_DRIFT")
    return verifier, fixture, fixture_raw


def _verify(
    fixture_raw: bytes,
    *,
    parent_raw: bytes | None = None,
    policy_raw: bytes | None = None,
    reviewer_map_raw: bytes | None = None,
    artifact_raw: bytes | None = None,
) -> dict[str, Any]:
    parent, policy, reviewer, artifact = _raw_inputs()
    verifier = oracle.load_verifier()
    report = verifier.verify_fixture(
        parent_artifact_raw=parent if parent_raw is None else parent_raw,
        policy_artifact_raw=policy if policy_raw is None else policy_raw,
        reviewer_map_raw=reviewer if reviewer_map_raw is None else reviewer_map_raw,
        v26_artifact_raw=artifact if artifact_raw is None else artifact_raw,
        fixture_raw=fixture_raw,
    )
    oracle.require(isinstance(report, dict), "TASK8_V26_TEST_REPORT_INVALID")
    return cast(dict[str, Any], report)


def _expect_code(expected: str, operation: Callable[[], object]) -> None:
    with pytest.raises(Exception) as captured:
        operation()
    oracle.require(
        oracle.verifier_error_code(captured.value) == expected,
        "TASK8_V26_TEST_ERROR_CODE_DRIFT",
    )


def _dashboard_function_statement(artifact: Mapping[str, Any]) -> tuple[dict[str, Any], int, str]:
    row = next(
        item
        for item in artifact["sql_authority_registry"]["rows"]
        if item["authority_id"] == "dashboard-root-equation-v3"
    )
    statements = [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]
    ordinal = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    )
    return row, ordinal, statements[ordinal]


def _replace_dashboard_sql(artifact: dict[str, Any], old: str, new: str) -> None:
    row, ordinal, statement = _dashboard_function_statement(artifact)
    oracle.require(statement.count(old) >= 1, "TASK8_V26_TEST_SQL_MUTATION_BASE_INVALID")
    statements = [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]
    statements[ordinal] = statement.replace(old, new, 1)
    row["body_sql_utf8_hex_tuple"] = [value.encode("utf-8").hex() for value in statements]


def test_independent_verifier_exists_and_sources_are_lf_pinned() -> None:
    oracle.require(
        oracle.INDEPENDENT_VERIFIER.is_file(),
        "TASK8_V26_INDEPENDENT_VERIFIER_NOT_IMPLEMENTED",
    )
    existing_paths = (
        "scripts/verify_full_strict_v26_fixture_independently.py",
        "tests/slice100_task8/_v26_independent_oracle.py",
        "tests/slice100_task8/test_v26_independent_fixture_verification_red.py",
        "tests/slice100_task8/fixtures/full_strict_v26_independent_golden_v1.json",
    )
    pinned_paths = (
        *existing_paths,
        "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md",
        "contracts/full_strict_v26_reviewed_preimage_v1.json",
        "tests/slice100_task8/fixtures/full_strict_v26_contract_hash_v1.json",
    )
    attributes = (oracle.REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for relative_path in pinned_paths:
        oracle.require(
            f"{relative_path} text eol=lf" in attributes,
            "TASK8_V26_INDEPENDENT_SOURCE_LF_ATTRIBUTE_MISSING",
        )
    for relative_path in existing_paths:
        oracle.require(
            b"\r\n" not in (oracle.REPO_ROOT / relative_path).read_bytes(),
            "TASK8_V26_INDEPENDENT_SOURCE_NOT_LF",
        )


def test_independent_verifier_source_cannot_reach_production_or_predecessor_code() -> None:
    oracle.assert_verifier_source_is_independent(oracle.INDEPENDENT_VERIFIER.read_bytes())


def test_independent_privacy_ast_includes_the_production_verifier() -> None:
    paths = (
        oracle.INDEPENDENT_VERIFIER,
        Path(__file__).with_name("_v26_independent_oracle.py"),
        Path(__file__),
    )
    offenders = [
        (path.name, node.lineno)
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assert)
    ]
    oracle.require(not offenders, "TASK8_V26_INDEPENDENT_PRIVACY_BARE_ASSERT_FORBIDDEN")


def test_canonical_v26_publication_targets_remain_absent() -> None:
    oracle.require(
        not oracle.CANONICAL_ARTIFACT.exists(),
        "TASK8_V26_INDEPENDENT_CANONICAL_ARTIFACT_PREPUBLISHED",
    )
    oracle.require(
        not oracle.CANONICAL_FIXTURE.exists(),
        "TASK8_V26_INDEPENDENT_CANONICAL_FIXTURE_PREPUBLISHED",
    )


def test_independent_fixture_derivation_pins_all_stable_candidate_identities() -> None:
    parent_raw, _policy_raw, _reviewer_raw, artifact_raw = _raw_inputs()
    _verifier, fixture, fixture_raw = _derive()
    graph_hash, graph_counts = oracle.independent_graph_hash(
        fixture["identity_hash_dependency_graph"]
    )
    oracle.require(
        hashlib.sha256(parent_raw).hexdigest() == oracle.PARENT_ARTIFACT_SHA256,
        "TASK8_V26_TEST_PARENT_IDENTITY_DRIFT",
    )
    oracle.require(
        hashlib.sha256(artifact_raw).hexdigest() == oracle.ARTIFACT_SHA256,
        "TASK8_V26_TEST_ARTIFACT_IDENTITY_DRIFT",
    )
    oracle.require(
        fixture["artifact_sha256"] == oracle.ARTIFACT_SHA256,
        "TASK8_V26_TEST_FIXTURE_ARTIFACT_IDENTITY_DRIFT",
    )
    oracle.require(
        fixture["FULL_STRICT_V26_CONTRACT_HASH"] == oracle.CONTRACT_SHA256,
        "TASK8_V26_TEST_CONTRACT_IDENTITY_DRIFT",
    )
    oracle.require(graph_hash == oracle.GRAPH_SHA256, "TASK8_V26_TEST_GRAPH_IDENTITY_DRIFT")
    oracle.require(
        graph_counts
        == {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V26_TEST_GRAPH_COUNT_DRIFT",
    )
    oracle.require(
        fixture["approved_semantics_conservation"]
        == {
            "schema_version": "task8-v26-approved-semantics-conservation-v1",
            "entry_count": 8,
            "sha256": oracle.CONSERVATION_SHA256,
        },
        "TASK8_V26_TEST_CONSERVATION_IDENTITY_DRIFT",
    )
    oracle.require(
        fixture["policy_binding"]["binding_sha256"] == oracle.POLICY_BINDING_SHA256,
        "TASK8_V26_TEST_POLICY_BINDING_IDENTITY_DRIFT",
    )
    oracle.require(
        hashlib.sha256(fixture_raw).hexdigest() == oracle.FIXTURE_SHA256,
        "TASK8_V26_TEST_FIXTURE_IDENTITY_DRIFT",
    )
    oracle.require(
        not oracle.CANONICAL_FIXTURE.exists(),
        "TASK8_V26_INDEPENDENT_CANONICAL_FIXTURE_PREPUBLISHED",
    )


def test_independent_fixture_component_and_contract_hashes_match_test_oracle() -> None:
    _parent, _policy, _reviewer, artifact_raw = _raw_inputs()
    artifact = _decode(artifact_raw)
    _verifier, fixture, _fixture_raw = _derive()
    expected_components, expected_contract = oracle.independent_contract_hash(
        artifact, artifact_raw
    )
    oracle.require(
        fixture["component_hashes"] == expected_components,
        "TASK8_V26_TEST_COMPONENT_HASH_DRIFT",
    )
    oracle.require(
        expected_contract == oracle.CONTRACT_SHA256,
        "TASK8_V26_TEST_CONTRACT_HASH_DRIFT",
    )


def test_verify_fixture_returns_hash_and_count_only_report() -> None:
    _verifier, fixture, fixture_raw = _derive()
    report = _verify(fixture_raw)
    oracle.require(
        set(report)
        == {
            "verification_path",
            "parent_artifact_sha256",
            "artifact_sha256",
            "fixture_sha256",
            "component_hashes",
            "contract_hash",
            "identity_graph_sha256",
            "identity_graph_counts",
            "conservation_sha256",
            "policy_binding_sha256",
            "counts",
        },
        "TASK8_V26_TEST_REPORT_FIELD_CLOSURE_INVALID",
    )
    encoded = json.dumps(report, sort_keys=True)
    artifact = _decode(_raw_inputs()[3])
    for forbidden in (
        "reviewed_design_utf8_hex",
        "body_sql_utf8_hex_tuple",
        "approved_delta_manifest",
        str(artifact["metadata"].get("reviewed_design_utf8_hex", "")),
    ):
        oracle.require(forbidden not in encoded, "TASK8_V26_TEST_REPORT_PRIVATE_BYTES_LEAKED")
    oracle.require(
        report["artifact_sha256"] == oracle.ARTIFACT_SHA256
        and report["fixture_sha256"] == oracle.FIXTURE_SHA256
        and report["contract_hash"] == oracle.CONTRACT_SHA256
        and report["identity_graph_sha256"] == oracle.GRAPH_SHA256
        and report["conservation_sha256"] == oracle.CONSERVATION_SHA256
        and report["policy_binding_sha256"] == oracle.POLICY_BINDING_SHA256,
        "TASK8_V26_TEST_REPORT_IDENTITY_DRIFT",
    )
    oracle.require(report["counts"] == fixture["counts"], "TASK8_V26_TEST_REPORT_COUNT_DRIFT")


def test_exact_frozen_parent_bytes_and_recorded_provenance_are_independently_required() -> None:
    parent, _policy, _reviewer, artifact_raw = _raw_inputs()
    mutated = bytearray(parent)
    mutated[-2] = 32 if mutated[-2] != 32 else 9
    _expect_code(
        "TASK8_V26_INDEPENDENT_PARENT_FROZEN_IDENTITY_MISMATCH",
        lambda: _derive(parent_raw=bytes(mutated)),
    )
    artifact = _decode(artifact_raw)
    artifact["task8_structural_admission"]["parent_provenance"]["parent_artifact_sha256"] = "0" * 64
    _expect_code(
        "TASK8_V26_INDEPENDENT_PARENT_PROVENANCE_INVALID",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


def test_top_level_and_complete_approved_delta_closure_are_exact() -> None:
    artifact = _decode(_raw_inputs()[3])
    reordered = {key: artifact[key] for key in reversed(tuple(artifact))}
    _expect_code(
        "TASK8_V26_INDEPENDENT_TOP_LEVEL_SECTION_CLOSURE_INVALID",
        lambda: _derive(artifact_raw=oracle.render(reordered)),
    )
    artifact = _decode(_raw_inputs()[3])
    artifact["task8_structural_admission"]["approved_delta_manifest"].pop()
    _expect_code(
        "TASK8_V26_INDEPENDENT_APPROVED_DELTA_MANIFEST_INVALID",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


def test_complete_top_level_digest_inventory_is_literal_and_independent() -> None:
    artifact = _decode(_raw_inputs()[3])
    verifier = oracle.load_verifier()
    oracle.require(
        len(oracle.TOP_LEVEL_DIGEST_INVENTORY) == 29,
        "TASK8_V26_TEST_TOP_LEVEL_DIGEST_COUNT_DRIFT",
    )
    oracle.require(
        tuple(row[0] for row in oracle.TOP_LEVEL_DIGEST_INVENTORY)
        == tuple(sorted(f"#/{key}" for key in artifact)),
        "TASK8_V26_TEST_TOP_LEVEL_DIGEST_POINTER_DRIFT",
    )
    oracle.require(
        oracle.top_level_digest_inventory(artifact) == oracle.TOP_LEVEL_DIGEST_INVENTORY,
        "TASK8_V26_TEST_TOP_LEVEL_DIGEST_ORACLE_DRIFT",
    )
    oracle.require(
        verifier._top_level_digest_inventory(artifact) == oracle.TOP_LEVEL_DIGEST_INVENTORY,
        "TASK8_V26_TEST_TOP_LEVEL_DIGEST_VERIFIER_DRIFT",
    )


def test_complete_artifact_invariance_rejects_deep_otherwise_valid_drift() -> None:
    artifact = _decode(_raw_inputs()[3])
    artifact["metadata"]["reviewed_design_utf8_hex"] += "20"
    oracle.require(
        oracle.top_level_digest_inventory(artifact) != oracle.TOP_LEVEL_DIGEST_INVENTORY,
        "TASK8_V26_TEST_COMPLETE_ARTIFACT_CANARY_STALLED",
    )
    _expect_code(
        "TASK8_V26_INDEPENDENT_COMPLETE_ARTIFACT_INVARIANCE_DRIFT",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


def test_void_history_is_test_owned_and_excluded_from_active_and_verifier_bytes() -> None:
    oracle.require(
        oracle.VOID_IDENTITIES is oracle.void_evidence.TASK8_VOID_CONTRACT_HASHES,
        "TASK8_V26_TEST_VOID_EVIDENCE_AUTHORITY_DRIFT",
    )
    artifact_raw = _raw_inputs()[3]
    verifier_source = oracle.INDEPENDENT_VERIFIER.read_bytes()
    for marker in (
        b"full-strict-v22",
        b"full-strict-v23",
        b"full-strict-v24",
        b"full-strict-v25",
    ):
        oracle.require(marker not in artifact_raw, "TASK8_V26_TEST_VOID_LABEL_PRESENT")
        oracle.require(marker not in verifier_source, "TASK8_V26_TEST_VOID_LABEL_IN_VERIFIER")
    for digest in oracle.VOID_IDENTITIES:
        oracle.require(digest.encode("ascii") not in artifact_raw, "TASK8_V26_TEST_VOID_ID_PRESENT")
        oracle.require(
            digest.encode("ascii") not in verifier_source,
            "TASK8_V26_TEST_VOID_ID_IN_VERIFIER",
        )
    artifact = _decode(artifact_raw)
    artifact["metadata"]["void_canary"] = oracle.VOID_IDENTITIES[-1]
    _expect_code(
        "TASK8_V26_INDEPENDENT_COMPLETE_ARTIFACT_INVARIANCE_DRIFT",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


def test_policy_and_reviewer_map_are_exact_frozen_inputs() -> None:
    _parent, policy, reviewer, _artifact = _raw_inputs()
    _expect_code(
        "TASK8_V26_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH",
        lambda: _derive(policy_raw=policy + b"\n"),
    )
    _expect_code(
        "TASK8_V26_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH",
        lambda: _derive(reviewer_map_raw=reviewer + b"private-canary"),
    )


def test_exact_ten_requirement_literals_and_binding_are_independently_validated() -> None:
    artifact = _decode(_raw_inputs()[3])
    contract = artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"]
    oracle.require(
        tuple(tuple(row) for row in contract["policy_v2_requirement_literal_tuple"])
        == oracle.EXPECTED_REQUIREMENT_TUPLE,
        "TASK8_V26_TEST_REQUIREMENT_LITERAL_DRIFT",
    )
    oracle.require(
        contract["policy_sha256"] == oracle.POLICY_SHA256,
        "TASK8_V26_TEST_REQUIREMENT_POLICY_DRIFT",
    )
    artifact["task8_fdd_contract"]["dashboard_contract"]["requirement_binding_contract"][
        "policy_v2_requirement_literal_tuple"
    ][0][2] = "sheet_control"
    _expect_code(
        "TASK8_V26_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("HAVING count(*)<>1", "HAVING count(*)<0"),
        ("q.methodology_version_id=c.methodology_version_id", "TRUE"),
        ("item->>'formula_type'=c.calc_type", "item->>'calc_type'=c.calc_type"),
    ),
)
def test_full_governed_requirement_sql_rejects_discrimination_or_duplicate_guard_drift(
    old: str,
    new: str,
) -> None:
    artifact = _decode(_raw_inputs()[3])
    _replace_dashboard_sql(artifact, old, new)
    _expect_code(
        "TASK8_V26_INDEPENDENT_REQUIREMENT_SQL_MISMATCH",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


@pytest.mark.parametrize(
    ("name", "accepted", "vector"),
    oracle.requirement_vectors(),
    ids=[row[0] for row in oracle.requirement_vectors()],
)
def test_hand_written_requirement_vectors(
    name: str,
    accepted: bool,
    vector: dict[str, object],
) -> None:
    del name
    verifier = oracle.load_verifier()
    if accepted:
        verifier.validate_requirement_vector(**vector)
    else:
        _expect_code(
            oracle.STABLE_DASHBOARD_CODE,
            lambda: verifier.validate_requirement_vector(**vector),
        )


def test_requirement_golden_names_are_complete_and_literal() -> None:
    golden = oracle.load_golden_vector()
    authority = golden["requirement_vector_authority"]
    oracle.require(
        authority["legal_vector_tuple"]
        == ["legacy_empty", "legacy_singleton", "policy_empty", "policy_singleton"],
        "TASK8_V26_TEST_LEGAL_VECTOR_AUTHORITY_DRIFT",
    )
    oracle.require(
        authority["negative_vector_tuple"]
        == [row[0] for row in oracle.requirement_vectors() if not row[1]],
        "TASK8_V26_TEST_NEGATIVE_VECTOR_AUTHORITY_DRIFT",
    )


def test_hand_written_golden_names_all_nine_governed_dollar_scalars() -> None:
    governed = oracle.load_golden_vector()["governed_dollar_scalar_contract"]
    oracle.require(
        tuple(governed["bound_runtime_token_tuple"]) == _BOUND_RUNTIME_TOKENS,
        "TASK8_V26_GOLDEN_BOUND_RUNTIME_TUPLE_DRIFT",
    )
    oracle.require(
        governed["producer_edge_reference"] == "$root_projection_sha256"
        and governed["contract_definition_placeholder"] == "$FDD_FORMULA_POLICY_V2_SHA256"
        and len(governed["bound_runtime_token_tuple"]) + 2 == 9,
        "TASK8_V26_GOLDEN_GOVERNED_DOLLAR_COUNT_DRIFT",
    )
    expected_source_tuple = tuple((*row[:4], *row[4][0]) for row in _DOLLAR_SEMANTIC_TUPLE)
    oracle.require(
        tuple(tuple(row) for row in governed["source_contract_tuple"]) == expected_source_tuple,
        "TASK8_V26_GOLDEN_DOLLAR_SOURCE_CONTRACT_DRIFT",
    )


def test_hand_written_golden_names_every_permanent_graph_regression() -> None:
    authority = oracle.load_golden_vector()["regression_vector_authority"]
    oracle.require(
        authority["permanent_vector_tuple"]
        == [
            "V23_NON_DASHBOARD_CYCLE",
            "V25_STRIPPED_PREFIX",
            "SELF_DECLARED_PAYLOAD_SELECTOR",
            "EXTERNAL_AUTHORITY_RELABEL",
            "FROZEN_H_OPERATOR_DECLARATION",
            "RESERVED_PRODUCER_SYMBOL",
            "UNKNOWN_RECIPE_GRAMMAR",
            "BOUND_RUNTIME_PRODUCER_CAPTURE",
            "DASHBOARD_IDENTITY_CHAIN",
        ],
        "TASK8_V26_GOLDEN_REGRESSION_AUTHORITY_DRIFT",
    )


@pytest.mark.parametrize("semantic_ordinal", range(8))
def test_all_eight_conservation_receipts_are_rederived_from_implementing_bytes(
    semantic_ordinal: int,
) -> None:
    artifact = _decode(_raw_inputs()[3])
    row = artifact["task8_structural_admission"]["approved_semantics_conservation_manifest"][
        semantic_ordinal
    ]
    row[4][0][3] = "0" * 64
    _expect_code(
        "TASK8_V26_INDEPENDENT_CONSERVATION_RECEIPT_MISMATCH",
        lambda: _derive(artifact_raw=oracle.render(artifact)),
    )


def test_general_graph_is_independently_equal_to_literal_hash_and_counts() -> None:
    parent, policy_raw, reviewer, artifact_raw = _raw_inputs()
    del parent, reviewer
    verifier, fixture, _fixture_raw = _derive()
    graph = verifier.derive_identity_hash_dependency_graph(
        full_strict_artifact=_decode(artifact_raw),
        policy_artifact=_decode(policy_raw),
    )
    oracle.require(
        graph == fixture["identity_hash_dependency_graph"],
        "TASK8_V26_TEST_GRAPH_PARITY_DRIFT",
    )
    graph_hash, counts = oracle.independent_graph_hash(graph)
    oracle.require(
        graph_hash == oracle.GRAPH_SHA256
        and counts
        == {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V26_TEST_GRAPH_LITERAL_DRIFT",
    )


def test_general_graph_retains_seven_atomic_unsuffixed_bound_runtime_leaves() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    external_rows = [
        row
        for row in contract["allowed_external_input_tuple"]
        if row[0] == _ROW_PAYLOAD_POINTER and row[3] == "BOUND_RUNTIME_INPUT"
    ]
    oracle.require(
        external_rows
        == [
            [_ROW_PAYLOAD_POINTER, f"$[{ordinal}]", token, "BOUND_RUNTIME_INPUT"]
            for ordinal, token in enumerate(_BOUND_RUNTIME_TOKENS, start=1)
        ],
        "TASK8_V26_INDEPENDENT_BOUND_RUNTIME_EXTERNAL_ROWS_INVALID",
    )
    graph = verifier.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    leaf_rows = [
        row
        for row in graph["leaf_tuple"]
        if row[4] == _ROW_PAYLOAD_POINTER and row[3] == "BOUND_RUNTIME_INPUT"
    ]
    oracle.require(
        [(row[1], row[2], row[3]) for row in leaf_rows]
        == [
            (f"$[{ordinal}]", token, "BOUND_RUNTIME_INPUT")
            for ordinal, token in enumerate(_BOUND_RUNTIME_TOKENS, start=1)
        ],
        "TASK8_V26_INDEPENDENT_BOUND_RUNTIME_LEAVES_INVALID",
    )


def test_general_graph_distinguishes_root_edge_and_contract_definition_placeholder() -> None:
    verifier, artifact, policy = _graph_assets()
    producer_symbols = {
        row[5]
        for row in artifact["task8_structural_admission"]["identity_hash_dependency_contract"][
            "producer_authority_tuple"
        ]
    }
    for ordinal, token in enumerate(_BOUND_RUNTIME_TOKENS, start=1):
        oracle.require(
            verifier._governed_scalar_references(
                token,
                source_pointer=_ROW_PAYLOAD_POINTER,
                token_path=f"$[{ordinal}]",
                source_layer="RUNTIME_VALUE",
                producer_symbols=producer_symbols,
            )
            == ((token, "BOUND_RUNTIME_INPUT"),),
            "TASK8_V26_INDEPENDENT_BOUND_RUNTIME_TOKEN_NOT_ATOMIC",
        )
    oracle.require(
        verifier._governed_scalar_references(
            "$root_projection_sha256",
            source_pointer=_ROW_PAYLOAD_POINTER,
            token_path="$[8]",
            source_layer="RUNTIME_VALUE",
            producer_symbols=producer_symbols,
        )
        == (("root_projection_sha256", "SCALAR_OR_FOREIGN_INPUT"),),
        "TASK8_V26_INDEPENDENT_ROOT_PROJECTION_REFERENCE_DRIFT",
    )
    oracle.require(
        verifier._governed_scalar_references(
            "$FDD_FORMULA_POLICY_V2_SHA256",
            source_pointer=_POLICY_PLACEHOLDER_POINTER,
            token_path="$[3]",
            source_layer="CONTRACT_DEFINITION",
            producer_symbols=producer_symbols,
        )
        == (("FDD_FORMULA_POLICY_V2_SHA256", "SCALAR_OR_FOREIGN_INPUT"),),
        "TASK8_V26_INDEPENDENT_POLICY_PLACEHOLDER_DRIFT",
    )
    graph = verifier.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require(
        len([row for row in graph["edge_tuple"] if row[2].endswith("#root_projection_sha256")])
        == 3,
        "TASK8_V26_INDEPENDENT_ROOT_PROJECTION_EDGE_COUNT_DRIFT",
    )
    oracle.require(
        [
            (row[1], row[2], row[3], row[4])
            for row in graph["leaf_tuple"]
            if row[2] == "FDD_FORMULA_POLICY_V2_SHA256"
        ]
        == [
            (
                "$[3]#FDD_FORMULA_POLICY_V2_SHA256",
                "FDD_FORMULA_POLICY_V2_SHA256",
                "SCALAR_OR_FOREIGN_INPUT",
                ("fdd_formula_policy:#/methodology_transition/successor_version_id_preimage"),
            )
        ],
        "TASK8_V26_INDEPENDENT_POLICY_PLACEHOLDER_LEAF_DRIFT",
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
def test_general_graph_never_classifies_malformed_binding_as_bound(invalid: str) -> None:
    verifier = oracle.load_verifier()
    _expect_code(
        "TASK8_V26_INDEPENDENT_DOLLAR_REFERENCE_INVALID",
        lambda: verifier._governed_scalar_references(
            invalid,
            source_pointer=_ROW_PAYLOAD_POINTER,
            token_path="$[7]",
            source_layer="RUNTIME_VALUE",
            producer_symbols=set(),
        ),
    )


def test_general_graph_rejects_v25_stripped_prefix_external_manifest() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    for row in contract["allowed_external_input_tuple"]:
        if row[0] == _ROW_PAYLOAD_POINTER and row[3] == "BOUND_RUNTIME_INPUT":
            row[2] = row[2].removeprefix("$")
            row[3] = "SCALAR_OR_FOREIGN_INPUT"
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_BOUND_RUNTIME_CENSUS_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_unprefixed_producer_capture_before_edges() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    row = next(row for row in contract["producer_authority_tuple"] if row[5] == "step_id")
    row[5] = "calculations"
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def _graph_assets() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    _parent, policy_raw, _reviewer, artifact_raw = _raw_inputs()
    return oracle.load_verifier(), _decode(artifact_raw), _decode(policy_raw)


def _rehash_graph_manifests(contract: dict[str, Any]) -> None:
    contract["producer_authority_sha256"] = oracle.canonical_sha256(
        ["task8-v26-graph-producer-authority-v1", contract["producer_authority_tuple"]]
    )
    contract["allowed_external_input_sha256"] = oracle.canonical_sha256(
        ["task8-v26-graph-allowed-external-input-v1", contract["allowed_external_input_tuple"]]
    )


def _rebuild_graph_manifests(
    verifier: Any,
    artifact: dict[str, Any],
    policy: dict[str, Any],
) -> None:
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    pointers = verifier._discover_source_pointers(artifact, policy)
    producer_rows = [
        verifier._producer_authority_row(pointer, artifact, policy) for pointer in pointers
    ]
    contract["producer_authority_tuple"] = producer_rows
    contract["allowed_external_input_tuple"] = verifier._derive_external_input_rows(
        full_strict=artifact,
        policy=policy,
        producer_rows=producer_rows,
    )
    _rehash_graph_manifests(contract)


@pytest.mark.parametrize(
    ("source_pointer", "token_path", "raw_token", "source_layer", "expected"),
    _DOLLAR_SEMANTIC_TUPLE,
)
def test_general_graph_classifies_only_the_exact_nine_dollar_semantics(
    source_pointer: str,
    token_path: str,
    raw_token: str,
    source_layer: str,
    expected: tuple[tuple[str, str], ...],
) -> None:
    verifier, artifact, _policy = _graph_assets()
    producer_symbols = {
        row[5]
        for row in artifact["task8_structural_admission"]["identity_hash_dependency_contract"][
            "producer_authority_tuple"
        ]
    }
    oracle.require(
        verifier._governed_scalar_references(
            raw_token,
            source_pointer=source_pointer,
            token_path=token_path,
            source_layer=source_layer,
            producer_symbols=producer_symbols,
        )
        == expected,
        "TASK8_V26_INDEPENDENT_DOLLAR_SEMANTIC_DRIFT",
    )


@pytest.mark.parametrize(
    ("source_pointer", "token_path", "raw_token", "source_layer"),
    (
        (_ROW_PAYLOAD_POINTER, "$[7]", "$$calculations", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[7]", "$calculations.more", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[7]", "$calculations$", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[7]", "calculations$", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[7]", "x$$y", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[6]", "$calculations", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[7]", "$calculations", "CONTRACT_DEFINITION"),
        (_POLICY_PLACEHOLDER_POINTER, "$[3]", "$calculations", "CONTRACT_DEFINITION"),
        (_ROW_PAYLOAD_POINTER, "$[8]", "$FDD_FORMULA_POLICY_V2_SHA256", "RUNTIME_VALUE"),
        (_ROW_PAYLOAD_POINTER, "$[8]", "prefix$root_projection_sha256", "RUNTIME_VALUE"),
        (
            "full_strict:#/uuid_namespace_registry/rows/1",
            "$[0]",
            "$unreviewed_runtime_input",
            "RUNTIME_VALUE",
        ),
    ),
)
def test_general_graph_rejects_unknown_malformed_or_cross_layer_dollar_semantics(
    source_pointer: str,
    token_path: str,
    raw_token: str,
    source_layer: str,
) -> None:
    verifier = oracle.load_verifier()
    _expect_code(
        "TASK8_V26_INDEPENDENT_DOLLAR_REFERENCE_INVALID",
        lambda: verifier._governed_scalar_references(
            raw_token,
            source_pointer=source_pointer,
            token_path=token_path,
            source_layer=source_layer,
            producer_symbols={"root_projection_sha256"},
        ),
    )


def test_leaf_authority_has_no_generic_dollar_prefix_fallback() -> None:
    verifier = oracle.load_verifier()
    oracle.require(
        verifier._leaf_authority("$not_a_governed_context") == "SCALAR_OR_FOREIGN_INPUT",
        "TASK8_V26_INDEPENDENT_GENERIC_DOLLAR_FALLBACK_PRESENT",
    )


def test_global_bound_runtime_census_rejects_an_eighth_row() -> None:
    verifier = oracle.load_verifier()
    expected_rows = [
        [_ROW_PAYLOAD_POINTER, f"$[{ordinal}]", token, "BOUND_RUNTIME_INPUT"]
        for ordinal, token in enumerate(_BOUND_RUNTIME_TOKENS, start=1)
    ]
    _expect_code(
        "TASK8_V26_INDEPENDENT_BOUND_RUNTIME_CENSUS_INVALID",
        lambda: verifier._validate_bound_runtime_census(
            [
                *expected_rows,
                [
                    "full_strict:#/uuid_namespace_registry/rows/1",
                    "$[0]",
                    "$eighth_bound",
                    "BOUND_RUNTIME_INPUT",
                ],
            ]
        ),
    )


@pytest.mark.parametrize(
    "protected_symbol",
    ("calculations", "FDD_FORMULA_POLICY_V2_SHA256"),
)
def test_general_graph_rejects_coherent_source_shaped_protected_producer_capture(
    protected_symbol: str,
) -> None:
    verifier, artifact, policy = _graph_assets()
    artifact["uuid_namespace_registry"]["rows"].append(
        [
            protected_symbol,
            "d91c1b6b-d677-5c11-9168-60506273367e",
            ["capture_seed"],
        ]
    )
    _rebuild_graph_manifests(verifier, artifact, policy)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_non_dashboard_cycles() -> None:
    verifier, artifact, policy = _graph_assets()
    rows = {row[0]: row for row in artifact["uuid_namespace_registry"]["rows"]}
    rows["step_id"][2].append("source_lineage_object_id")
    rows["source_lineage_object_id"][2].append("step_id")
    _expect_code(
        "TASK8_V26_INDEPENDENT_IDENTITY_HASH_CYCLE",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_derives_full_payload_and_rejects_self_declared_selector_bypass() -> None:
    verifier, artifact, policy = _graph_assets()
    namespace_rows = artifact["uuid_namespace_registry"]["rows"]
    by_symbol = {row[0]: (ordinal, row) for ordinal, row in enumerate(namespace_rows)}
    step_ordinal, step_row = by_symbol["step_id"]
    lineage_ordinal, lineage_row = by_symbol["source_lineage_object_id"]
    step_row[2].append("source_lineage_object_id")
    lineage_row[2].append("step_id")

    step_pointer = f"full_strict:#/uuid_namespace_registry/rows/{step_ordinal}"
    lineage_pointer = f"full_strict:#/uuid_namespace_registry/rows/{lineage_ordinal}"
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    producer_by_pointer = {row[0]: row for row in contract["producer_authority_tuple"]}
    producer_by_pointer[step_pointer][5:] = ["hidden_step_id", "#/0"]
    producer_by_pointer[lineage_pointer][5:] = ["hidden_source_lineage_object_id", "#/0"]
    contract["allowed_external_input_tuple"] = [
        row
        for row in contract["allowed_external_input_tuple"]
        if row[0] not in {step_pointer, lineage_pointer}
    ]
    contract["allowed_external_input_tuple"].extend(
        [
            [step_pointer, "$", "step_id", "SCALAR_OR_FOREIGN_INPUT"],
            [lineage_pointer, "$", "source_lineage_object_id", "SCALAR_OR_FOREIGN_INPUT"],
        ]
    )
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_AUTHORITY_DRIFT",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_nonidentifier_symbol_that_hides_a_cycle() -> None:
    verifier, artifact, policy = _graph_assets()
    namespace_rows = artifact["uuid_namespace_registry"]["rows"]
    first_ordinal = len(namespace_rows)
    namespace_rows.extend(
        [
            ["cycle-a", "4aa65cc8-7f21-5cf6-ac44-1a35862f5c90", ["cycle_b"]],
            ["cycle_b", "4aa65cc8-7f21-5cf6-ac44-1a35862f5c91", ["cycle-a"]],
        ]
    )
    first_pointer = f"full_strict:#/uuid_namespace_registry/rows/{first_ordinal}"
    second_pointer = f"full_strict:#/uuid_namespace_registry/rows/{first_ordinal + 1}"
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    contract["producer_authority_tuple"].extend(
        [
            [
                first_pointer,
                "full_strict",
                "RUNTIME_VALUE",
                "UUID::cycle-a",
                "CYCLE-A",
                "cycle-a",
                "#/2",
            ],
            [
                second_pointer,
                "full_strict",
                "RUNTIME_VALUE",
                "UUID::cycle_b",
                "CYCLE_B",
                "cycle_b",
                "#/2",
            ],
        ]
    )
    contract["allowed_external_input_tuple"].extend(
        [
            [second_pointer, "$[0]", "a", "SCALAR_OR_FOREIGN_INPUT"],
            [second_pointer, "$[0]", "cycle", "SCALAR_OR_FOREIGN_INPUT"],
        ]
    )
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_external_authority_relabel() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    row = contract["allowed_external_input_tuple"][0]
    row[3] = "CONTRACT_LITERAL" if row[3] != "CONTRACT_LITERAL" else "FOREIGN_ROOT_INPUT"
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_EXTERNAL_INPUT_DRIFT",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_allows_only_the_exact_frozen_h_operator_declaration() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    expected_row = [
        "full_strict:#/digest_recipe_registry/rows/0",
        "full_strict",
        "CONTRACT_DEFINITION",
        "DIGEST_RECIPE_REGISTRY",
        "DIGEST_RECIPE",
        "H",
        "#/1",
    ]
    h_row = next(row for row in contract["producer_authority_tuple"] if row[5] == "H")
    oracle.require(
        h_row == expected_row
        and artifact["digest_recipe_registry"]["rows"][0]
        == ["H", "lowercase_hex(SHA256(CJ(value)))"],
        "TASK8_V26_TEST_FROZEN_H_AUTHORITY_DRIFT",
    )
    injected = copy.deepcopy(artifact)
    injected_contract = injected["task8_structural_admission"]["identity_hash_dependency_contract"]
    next(row for row in injected_contract["producer_authority_tuple"] if row[5] != "H")[5] = "H"
    _rehash_graph_manifests(injected_contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=injected,
            policy_artifact=policy,
        ),
    )
    artifact["digest_recipe_registry"]["rows"][0][0] = "MUTATED_H"
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )
    h_row[5] = "MUTATED_H"
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


@pytest.mark.parametrize("reserved_symbol", ("hex", "float8send"))
def test_general_graph_rejects_parser_reserved_producer_symbols(reserved_symbol: str) -> None:
    verifier, artifact, policy = _graph_assets()
    namespace_rows = artifact["uuid_namespace_registry"]["rows"]
    ordinal = len(namespace_rows)
    namespace_rows.append(
        [reserved_symbol, "5fa25c73-0463-51f8-a749-87cd0f6d2a6d", ["reserved_test_leaf"]]
    )
    pointer = f"full_strict:#/uuid_namespace_registry/rows/{ordinal}"
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    contract["producer_authority_tuple"].append(
        [
            pointer,
            "full_strict",
            "RUNTIME_VALUE",
            f"UUID::{reserved_symbol}",
            reserved_symbol.upper(),
            reserved_symbol,
            "#/2",
        ]
    )
    contract["allowed_external_input_tuple"] = [
        row for row in contract["allowed_external_input_tuple"] if row[2] != reserved_symbol
    ]
    contract["allowed_external_input_tuple"].append(
        [pointer, "$[0]", "reserved_test_leaf", "SCALAR_OR_FOREIGN_INPUT"]
    )
    _rehash_graph_manifests(contract)
    _expect_code(
        "TASK8_V26_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_unresolved_governed_symbols() -> None:
    verifier, artifact, policy = _graph_assets()
    row = next(
        row for row in artifact["uuid_namespace_registry"]["rows"] if row[0] == "step_output_id"
    )
    row[2].append("undeclared_governed_sha256")
    _expect_code(
        "TASK8_V26_INDEPENDENT_GOVERNED_REFERENCE_UNRESOLVED",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_duplicate_producer_symbols() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    rows = contract["producer_authority_tuple"]
    rows[1][5] = rows[0][5]
    contract["producer_authority_sha256"] = oracle.canonical_sha256(
        ["task8-v26-graph-producer-authority-v1", rows]
    )
    _expect_code(
        "TASK8_V26_INDEPENDENT_DUPLICATE_PRODUCER",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_orphaned_external_authority_rows() -> None:
    verifier, artifact, policy = _graph_assets()
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    rows = contract["allowed_external_input_tuple"]
    extra = copy.deepcopy(rows[0])
    extra[1] = "$[999999]"
    rows.append(extra)
    contract["allowed_external_input_sha256"] = oracle.canonical_sha256(
        ["task8-v26-graph-allowed-external-input-v1", rows]
    )
    _expect_code(
        "TASK8_V26_INDEPENDENT_EXTERNAL_INPUT_ORPHANED",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_rejects_unknown_recipe_grammar() -> None:
    verifier, artifact, policy = _graph_assets()
    row = next(
        row
        for row in artifact["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    )
    row[1] = "EVAL(unreviewed_dynamic_recipe)"
    _expect_code(
        "TASK8_V26_INDEPENDENT_RECIPE_GRAMMAR_INVALID",
        lambda: verifier.derive_identity_hash_dependency_graph(
            full_strict_artifact=artifact,
            policy_artifact=policy,
        ),
    )


def test_general_graph_output_is_independent_of_producer_manifest_order() -> None:
    verifier, artifact, policy = _graph_assets()
    baseline = verifier.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    contract = artifact["task8_structural_admission"]["identity_hash_dependency_contract"]
    rows = contract["producer_authority_tuple"]
    rows.reverse()
    contract["producer_authority_sha256"] = oracle.canonical_sha256(
        ["task8-v26-graph-producer-authority-v1", rows]
    )
    reordered = verifier.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    oracle.require(reordered == baseline, "TASK8_V26_TEST_GRAPH_ORDER_SENSITIVITY_INVALID")


def test_hand_written_dashboard_identity_vector_replays_through_two_implementations() -> None:
    verifier = oracle.load_verifier()
    golden = oracle.load_golden_vector()["dashboard_identity"]
    expected = golden["expected"]
    oracle.require(
        oracle.derive_dashboard_identity_vector(**golden["inputs"]) == expected,
        "TASK8_V26_TEST_IDENTITY_ORACLE_DRIFT",
    )
    oracle.require(
        verifier.derive_dashboard_identity_vector(**golden["inputs"]) == expected,
        "TASK8_V26_TEST_IDENTITY_VERIFIER_DRIFT",
    )


def test_dashboard_root_order_is_invariant_but_identity_inputs_propagate() -> None:
    verifier = oracle.load_verifier()
    golden = oracle.load_golden_vector()["dashboard_identity"]
    inputs = copy.deepcopy(golden["inputs"])
    baseline = verifier.derive_dashboard_identity_vector(**inputs)
    inputs["roots"].reverse()
    oracle.require(
        verifier.derive_dashboard_identity_vector(**inputs) == baseline,
        "TASK8_V26_TEST_ROOT_ORDER_CHANGED_IDENTITY",
    )
    inputs = copy.deepcopy(golden["inputs"])
    inputs["question_payload_hash"] = "a" * 64
    mutated = verifier.derive_dashboard_identity_vector(**inputs)
    for field in (
        "dashboard_row_id",
        "root_link_tuple",
        "root_projection_sha256",
        "row_payload_sha256",
        "native_identity_sha256",
        "native_output_sha256",
        "output_object_id",
    ):
        oracle.require(
            mutated[field] != baseline[field], "TASK8_V26_TEST_IDENTITY_MUTATION_STALLED"
        )


@pytest.mark.parametrize(
    ("mutator", "code"),
    (
        (
            lambda fixture: fixture.__setitem__("artifact_sha256", "0" * 64),
            "TASK8_V26_INDEPENDENT_ARTIFACT_HASH_MISMATCH",
        ),
        (
            lambda fixture: fixture["component_hashes"].__setitem__("metadata_hash", "0" * 64),
            "TASK8_V26_INDEPENDENT_COMPONENT_HASH_MISMATCH",
        ),
        (
            lambda fixture: fixture.__setitem__("FULL_STRICT_V26_CONTRACT_HASH", "0" * 64),
            "TASK8_V26_INDEPENDENT_CONTRACT_HASH_MISMATCH",
        ),
        (
            lambda fixture: fixture["identity_hash_dependency_graph"].__setitem__(
                "graph_sha256", "0" * 64
            ),
            "TASK8_V26_INDEPENDENT_GRAPH_HASH_MISMATCH",
        ),
        (
            lambda fixture: fixture["approved_semantics_conservation"].__setitem__(
                "sha256", "0" * 64
            ),
            "TASK8_V26_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
        ),
        (
            lambda fixture: fixture["policy_binding"].__setitem__("binding_sha256", "0" * 64),
            "TASK8_V26_INDEPENDENT_POLICY_BINDING_INVALID",
        ),
        (
            lambda fixture: fixture["counts"].__setitem__("schema_count", 0),
            "TASK8_V26_INDEPENDENT_COUNT_MISMATCH",
        ),
    ),
)
def test_each_stale_fixture_hash_family_has_a_stable_rejection_code(
    mutator: Callable[[dict[str, Any]], object],
    code: str,
) -> None:
    _verifier, fixture, _fixture_raw = _derive()
    mutated = copy.deepcopy(fixture)
    mutator(mutated)
    _expect_code(code, lambda: _verify(oracle.render(mutated)))


def test_fixture_identity_pin_rejects_independent_expected_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = oracle.load_verifier()
    monkeypatch.setattr(verifier, "_EXPECTED_FIXTURE_SHA256", "0" * 64)
    parent, policy, reviewer, artifact = _raw_inputs()
    _expect_code(
        "TASK8_V26_INDEPENDENT_FIXTURE_HASH_MISMATCH",
        lambda: verifier.derive_fixture(
            parent_artifact_raw=parent,
            policy_artifact_raw=policy,
            reviewer_map_raw=reviewer,
            v26_artifact_raw=artifact,
        ),
    )


def test_direct_api_nonfinite_values_fail_with_stable_code() -> None:
    verifier = oracle.load_verifier()
    _expect_code(
        "TASK8_V26_INDEPENDENT_BINARY64_NONFINITE",
        lambda: verifier._render({"private": float("nan")}),
    )
    parent, policy, reviewer, _artifact = _raw_inputs()
    _expect_code(
        "TASK8_V26_INDEPENDENT_BINARY64_NONFINITE",
        lambda: verifier.derive_fixture(
            parent_artifact_raw=parent,
            policy_artifact_raw=policy,
            reviewer_map_raw=reviewer,
            v26_artifact_raw=b'{"metadata":NaN}\n',
        ),
    )


@pytest.mark.parametrize(
    ("artifact_raw", "expected_code"),
    (
        pytest.param(
            b'{"oversized_integer":' + (b"9" * 5000) + b"}\n",
            "TASK8_V26_INDEPENDENT_ARTIFACT_INVALID",
            id="oversized-integer",
        ),
        pytest.param(
            b'{"lone_surrogate":"\\ud800"}\n',
            "TASK8_V26_INDEPENDENT_CANONICAL_VALUE_INVALID",
            id="lone-surrogate",
        ),
    ),
)
def test_direct_api_malformed_canonical_values_fail_with_stable_codes(
    artifact_raw: bytes,
    expected_code: str,
) -> None:
    verifier = oracle.load_verifier()
    parent, policy, reviewer, _artifact = _raw_inputs()
    _expect_code(
        expected_code,
        lambda: verifier.derive_fixture(
            parent_artifact_raw=parent,
            policy_artifact_raw=policy,
            reviewer_map_raw=reviewer,
            v26_artifact_raw=artifact_raw,
        ),
    )


@pytest.mark.parametrize(
    ("fixture_raw", "expected_code"),
    (
        pytest.param(
            b'{"oversized_integer":' + (b"9" * 5000) + b"}\n",
            "TASK8_V26_INDEPENDENT_FIXTURE_INVALID",
            id="oversized-integer",
        ),
        pytest.param(
            b'{"lone_surrogate":"\\ud800"}\n',
            "TASK8_V26_INDEPENDENT_CANONICAL_VALUE_INVALID",
            id="lone-surrogate",
        ),
    ),
)
def test_verify_api_malformed_canonical_values_fail_with_stable_codes(
    fixture_raw: bytes,
    expected_code: str,
) -> None:
    _expect_code(expected_code, lambda: _verify(fixture_raw))


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(oracle.INDEPENDENT_VERIFIER), *arguments],
        cwd=oracle.REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _write_cli_inputs(tmp_path: Path, *, reviewer_raw: bytes | None = None) -> tuple[Path, ...]:
    parent, policy, reviewer, artifact = _raw_inputs()
    _verifier, _fixture, fixture = _derive()
    values = {
        "parent.json": parent,
        "policy.json": policy,
        "reviewer-map.md": reviewer if reviewer_raw is None else reviewer_raw,
        "candidate.json": artifact,
        "fixture.json": fixture,
    }
    paths: list[Path] = []
    for name, raw in values.items():
        path = tmp_path / name
        path.write_bytes(raw)
        paths.append(path)
    return tuple(paths)


def test_cli_success_is_hashes_and_counts_only(tmp_path: Path) -> None:
    parent, policy, reviewer, artifact, fixture = _write_cli_inputs(tmp_path)
    completed = _run_cli(
        "--mode",
        "verify",
        "--parent",
        str(parent),
        "--policy",
        str(policy),
        "--reviewer-map",
        str(reviewer),
        "--artifact",
        str(artifact),
        "--fixture",
        str(fixture),
    )
    oracle.require(completed.returncode == 0, "TASK8_V26_TEST_CLI_SUCCESS_EXIT_INVALID")
    report = json.loads(completed.stdout)
    oracle.require(report["result"] == "VERIFIED", "TASK8_V26_TEST_CLI_RESULT_INVALID")
    oracle.require(
        report["artifact_sha256"] == oracle.ARTIFACT_SHA256
        and report["contract_hash"] == oracle.CONTRACT_SHA256
        and report["graph_sha256"] == oracle.GRAPH_SHA256
        and report["fixture_sha256"] == oracle.FIXTURE_SHA256,
        "TASK8_V26_TEST_CLI_IDENTITY_DRIFT",
    )
    oracle.require(completed.stderr == "", "TASK8_V26_TEST_CLI_STDERR_NOT_EMPTY")
    oracle.require(
        "body_sql_utf8_hex_tuple" not in completed.stdout
        and "reviewed_design_utf8_hex" not in completed.stdout,
        "TASK8_V26_TEST_CLI_PRIVATE_BYTES_LEAKED",
    )


def test_cli_failure_emits_only_stable_code_without_canary(tmp_path: Path) -> None:
    canary = b"private-reviewer-map-canary"
    parent, policy, reviewer, artifact, fixture = _write_cli_inputs(
        tmp_path,
        reviewer_raw=canary,
    )
    completed = _run_cli(
        "--mode",
        "verify",
        "--parent",
        str(parent),
        "--policy",
        str(policy),
        "--reviewer-map",
        str(reviewer),
        "--artifact",
        str(artifact),
        "--fixture",
        str(fixture),
    )
    oracle.require(completed.returncode == 2, "TASK8_V26_TEST_CLI_FAILURE_EXIT_INVALID")
    output = completed.stdout + completed.stderr
    oracle.require("private-reviewer-map-canary" not in output, "TASK8_V26_TEST_CLI_CANARY_LEAKED")
    oracle.require(str(reviewer) not in output, "TASK8_V26_TEST_CLI_PATH_LEAKED")
    oracle.require(
        json.loads(completed.stdout)
        == {
            "reason_code": "TASK8_V26_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH",
            "result": "FAILED",
        },
        "TASK8_V26_TEST_CLI_FAILURE_SHAPE_INVALID",
    )


def test_cli_nonfinite_and_argument_failures_emit_codes_without_prose(tmp_path: Path) -> None:
    parent, policy, reviewer, artifact, _fixture = _write_cli_inputs(tmp_path)
    artifact.write_bytes(b'{"metadata":NaN}\n')
    completed = _run_cli(
        "--mode",
        "derive",
        "--parent",
        str(parent),
        "--policy",
        str(policy),
        "--reviewer-map",
        str(reviewer),
        "--artifact",
        str(artifact),
    )
    oracle.require(completed.returncode == 2, "TASK8_V26_TEST_CLI_NONFINITE_EXIT_INVALID")
    oracle.require(completed.stderr == "", "TASK8_V26_TEST_CLI_NONFINITE_STDERR_NOT_EMPTY")
    oracle.require(
        json.loads(completed.stdout)
        == {
            "reason_code": "TASK8_V26_INDEPENDENT_BINARY64_NONFINITE",
            "result": "FAILED",
        },
        "TASK8_V26_TEST_CLI_NONFINITE_FAILURE_SHAPE_INVALID",
    )

    completed = _run_cli("--not-an-argument", "private-argparse-canary")
    oracle.require(completed.returncode == 2, "TASK8_V26_TEST_CLI_ARGUMENT_EXIT_INVALID")
    output = completed.stdout + completed.stderr
    oracle.require("private-argparse-canary" not in output, "TASK8_V26_TEST_CLI_ARGUMENT_LEAKED")
    oracle.require(completed.stderr == "", "TASK8_V26_TEST_CLI_ARGUMENT_STDERR_NOT_EMPTY")
    oracle.require(
        json.loads(completed.stdout)
        == {
            "reason_code": "TASK8_V26_INDEPENDENT_CLI_ARGUMENT_INVALID",
            "result": "FAILED",
        },
        "TASK8_V26_TEST_CLI_ARGUMENT_FAILURE_SHAPE_INVALID",
    )


@pytest.mark.parametrize(
    ("artifact_raw", "expected_code"),
    (
        pytest.param(
            b'{"private_oversized_integer":' + (b"9" * 5000) + b"}\n",
            "TASK8_V26_INDEPENDENT_ARTIFACT_INVALID",
            id="oversized-integer",
        ),
        pytest.param(
            b'{"private_lone_surrogate":"\\ud800"}\n',
            "TASK8_V26_INDEPENDENT_CANONICAL_VALUE_INVALID",
            id="lone-surrogate",
        ),
    ),
)
def test_cli_malformed_canonical_values_emit_only_stable_codes(
    tmp_path: Path,
    artifact_raw: bytes,
    expected_code: str,
) -> None:
    parent, policy, reviewer, artifact, _fixture = _write_cli_inputs(tmp_path)
    artifact.write_bytes(artifact_raw)
    completed = _run_cli(
        "--mode",
        "derive",
        "--parent",
        str(parent),
        "--policy",
        str(policy),
        "--reviewer-map",
        str(reviewer),
        "--artifact",
        str(artifact),
    )
    oracle.require(completed.returncode == 2, "TASK8_V26_TEST_CLI_MALFORMED_EXIT_INVALID")
    oracle.require(completed.stderr == "", "TASK8_V26_TEST_CLI_MALFORMED_STDERR_NOT_EMPTY")
    output = completed.stdout + completed.stderr
    oracle.require("private_" not in output, "TASK8_V26_TEST_CLI_MALFORMED_PROSE_LEAKED")
    oracle.require(
        json.loads(completed.stdout) == {"reason_code": expected_code, "result": "FAILED"},
        "TASK8_V26_TEST_CLI_MALFORMED_FAILURE_SHAPE_INVALID",
    )


def test_cli_derive_mode_never_writes_canonical_outputs(tmp_path: Path) -> None:
    parent, policy, reviewer, artifact, _fixture = _write_cli_inputs(tmp_path)
    completed = _run_cli(
        "--mode",
        "derive",
        "--parent",
        str(parent),
        "--policy",
        str(policy),
        "--reviewer-map",
        str(reviewer),
        "--artifact",
        str(artifact),
    )
    oracle.require(completed.returncode == 0, "TASK8_V26_TEST_CLI_DERIVE_EXIT_INVALID")
    report = json.loads(completed.stdout)
    oracle.require(report["result"] == "DERIVED", "TASK8_V26_TEST_CLI_DERIVE_RESULT_INVALID")
    oracle.require(
        report["fixture_sha256"] == oracle.FIXTURE_SHA256,
        "TASK8_V26_TEST_CLI_FIXTURE_IDENTITY_DRIFT",
    )
    oracle.require(
        not oracle.CANONICAL_ARTIFACT.exists() and not oracle.CANONICAL_FIXTURE.exists(),
        "TASK8_V26_INDEPENDENT_CANONICAL_OUTPUT_CREATED",
    )
