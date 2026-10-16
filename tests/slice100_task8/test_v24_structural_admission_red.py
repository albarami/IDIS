"""RED-first structural admission for the reviewed full-strict-v24 draft."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from . import _v24_structural_oracle as oracle

_REQUIRED_PROJECTION_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v2","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)
_OLD_PROJECTION_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v1","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.run_output_object_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)


def _assets() -> tuple[dict[str, Any], dict[str, Any], Any]:
    # Artifact load is deliberately first: the initial RED result must name
    # the missing reviewed v24 bytes, not cascade through an import failure.
    artifact = oracle.load_v24()
    policy = oracle.load_policy_v2()
    materializer = oracle.load_materializer()
    return artifact, policy, materializer


def _structural(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return oracle.structural_section(artifact)


def _graph(materializer: Any, artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=copy.deepcopy(dict(artifact)),
        policy_artifact=copy.deepcopy(oracle.load_policy_v2()),
    )


def _graph_receipt(graph: Mapping[str, Any]) -> str:
    return oracle.canonical_sha256(
        [
            oracle.GRAPH_SCHEMA,
            graph["node_tuple"],
            graph["edge_tuple"],
            graph["leaf_tuple"],
            graph["source_pointer_tuple"],
        ]
    )


def _replace_dashboard_sql(artifact: dict[str, Any], old: str, new: str) -> None:
    row = next(
        item
        for item in artifact["sql_authority_registry"]["rows"]
        if item["authority_id"] == "dashboard-root-equation-v2"
    )
    statements = [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]
    function_ordinal = next(
        ordinal
        for ordinal, value in enumerate(statements)
        if value.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
        or value.startswith(
            "CREATE OR REPLACE FUNCTION public.idis_task8_dashboard_root_equation_v2()"
        )
    )
    statement = statements[function_ordinal]
    projection_start = statement.index("preimage:=")
    # Target the governed projection expression, not an earlier SQL WHERE use.
    old_at = statement.index(old, projection_start)
    statements[function_ordinal] = statement[:old_at] + new + statement[old_at + len(old) :]
    row["body_sql_utf8_hex_tuple"] = [value.encode("utf-8").hex() for value in statements]


def _policy_projection_recipe(artifact: Mapping[str, Any]) -> str:
    matches = [
        row[1]
        for row in artifact["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    ]
    assert len(matches) == 1, "TASK8_V24_PROJECTION_RECIPE_CARDINALITY_INVALID"
    return matches[0]


def _expected_sql_recipe_binding(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[list[str]]:
    return [
        [
            "full_strict",
            "methodology_truth_dashboard_root_projection_sha256",
            oracle.canonical_sha256(_policy_projection_recipe(artifact)),
        ],
        [
            "fdd_formula_policy",
            "#/financial_output_schema/row_payload_preimage",
            oracle.canonical_sha256(policy["financial_output_schema"]["row_payload_preimage"]),
        ],
    ]


def test_v24_derives_from_exact_frozen_v21_parent_and_records_that_provenance() -> None:
    artifact, _policy, materializer = _assets()

    assert artifact["metadata"]["artifact_schema"] == oracle.V24_SCHEMA
    assert artifact["metadata"]["pipeline_contract_version"] == oracle.V24_VERSION
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["activation_status"] == "INACTIVE"
    section = _structural(artifact)
    assert section["section_schema"] == oracle.STRUCTURAL_SCHEMA
    assert section["parent_provenance"] == oracle.expected_parent_provenance()

    # The public parent validator is the build gate.  It accepts only the
    # exact frozen v21 bytes; a valid-but-rejected v23 document is not a base.
    validated = materializer.validate_frozen_parent_bytes(oracle.V21_ARTIFACT.read_bytes())
    assert validated["metadata"]["pipeline_contract_version"] == "full-strict-v21"
    for rejected in (
        oracle.V23_DRAFT_ARTIFACT.read_bytes(),
        # Preserve valid JSON while changing bytes; replacing LF with LF was a no-op.
        oracle.V21_ARTIFACT.read_bytes()[:-1] + b" \n",
    ):
        with pytest.raises(
            materializer.MaterializationError,
            match="TASK8_V24_PARENT_MUST_BE_FROZEN_V21",
        ):
            materializer.validate_frozen_parent_bytes(rejected)


def test_v24_rebuild_uses_parent_bytes_not_a_rejected_intermediate() -> None:
    artifact, _policy, materializer = _assets()
    rebuilt = materializer.build_v24_from_parent_raw(
        parent_artifact_raw=oracle.V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    assert oracle.canonical_bytes(rebuilt) == oracle.canonical_bytes(artifact)

    with pytest.raises(
        materializer.MaterializationError,
        match="TASK8_V24_PARENT_MUST_BE_FROZEN_V21",
    ):
        materializer.build_v24_from_parent_raw(
            parent_artifact_raw=oracle.V23_DRAFT_ARTIFACT.read_bytes(),
            policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
        )


def test_v24_policy_binding_includes_and_validates_the_frozen_reviewer_map() -> None:
    artifact, _policy, materializer = _assets()
    binding = artifact["task8_fdd_contract"]["policy_binding"]
    assert binding["reviewer_map_relative_path"] == (
        "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
    )
    assert binding["reviewer_map_sha256"] == oracle.POLICY_V2_REVIEWER_MAP_SHA256

    with pytest.raises(
        materializer.MaterializationError,
        match="TASK8_V24_REVIEWER_MAP_BYTES_DRIFT",
    ):
        materializer.build_v24_from_parent_raw(
            parent_artifact_raw=oracle.V21_ARTIFACT.read_bytes(),
            policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes() + b" ",
        )


def test_v24_approved_delta_manifest_is_literal_complete_and_hash_bound() -> None:
    artifact, _policy, _materializer = _assets()
    section = _structural(artifact)

    assert section["approved_delta_manifest"] == [
        list(row) for row in oracle.APPROVED_DELTA_MANIFEST
    ]
    manifest = section["approved_semantics_conservation_manifest"]
    assert [row[:4] for row in manifest] == json.loads(
        oracle.canonical_bytes(oracle.APPROVED_SEMANTICS_CONSERVATION_MANIFEST)
    )
    assert section["approved_semantics_conservation_sha256"] == oracle.expected_conservation_sha256(
        manifest
    )
    assert section["expected_sha256"] == oracle.expected_structural_sha256(section)


def test_whole_artifact_graph_has_closed_token_receipts_and_no_special_case_escape() -> None:
    artifact, policy, materializer = _assets()
    contract = _structural(artifact)["identity_hash_dependency_contract"]
    assert contract["schema_version"] == oracle.GRAPH_SCHEMA
    assert contract["source_discovery"] == ("WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS")
    assert contract["recipe_grammar_tuple"] == ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"]
    assert contract["producer_authority_schema"] == [
        "source_pointer",
        "artifact_name",
        "layer",
        "scope",
        "output_kind",
        "symbol",
        "dependency_payload_pointer",
    ]
    assert contract["allowed_external_input_schema"] == [
        "source_pointer",
        "token_path",
        "symbol",
        "authority",
    ]
    assert contract["producer_authority_sha256"] == oracle.canonical_sha256(
        [oracle.GRAPH_PRODUCER_AUTHORITY_DOMAIN, contract["producer_authority_tuple"]]
    )
    assert contract["allowed_external_input_sha256"] == oracle.canonical_sha256(
        [oracle.GRAPH_EXTERNAL_INPUT_DOMAIN, contract["allowed_external_input_tuple"]]
    )
    assert contract["dashboard_special_case_allowed"] is False
    assert contract["synthetic_symbol_special_case_allowed"] is False
    assert contract["v23_non_dashboard_cycle_disposition"] == (
        "SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY"
    )

    graph = _graph(materializer, artifact)
    independent_sources = oracle.discover_source_pointers(artifact, policy)
    assert graph["schema_version"] == oracle.GRAPH_SCHEMA
    assert tuple(graph["source_pointer_tuple"]) == independent_sources
    assert graph["counts"]["source_pointer_count"] == len(independent_sources)
    assert oracle.graph_token_receipts(graph) == oracle.independently_expected_token_receipts(
        artifact,
        policy,
    )
    assert oracle.deterministic_cycle_path(oracle.graph_adjacency(graph)) is None
    assert graph["graph_sha256"] == _graph_receipt(graph)


def test_general_graph_rejects_an_unresolved_governed_reference() -> None:
    artifact, _policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    step_output = next(
        row for row in mutated["uuid_namespace_registry"]["rows"] if row[0] == "step_output_id"
    )
    step_output[2][step_output[2].index("step_id")] = "step_iid"

    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_GOVERNED_REFERENCE_UNRESOLVED",
    ):
        _graph(materializer, mutated)


def test_general_graph_rejects_duplicate_producer_symbols_before_edge_derivation() -> None:
    artifact, _policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    rows = mutated["task8_structural_admission"]["identity_hash_dependency_contract"][
        "producer_authority_tuple"
    ]
    assert len(rows) >= 2
    rows[1][5] = rows[0][5]

    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_DUPLICATE_PRODUCER",
    ):
        _graph(materializer, mutated)


def test_v23_non_dashboard_cycle_was_synthetic_and_general_guard_rejects_it() -> None:
    _artifact, _policy, materializer = _assets()
    pristine = oracle.load_v23_draft()
    uuid_rows = {row[0]: row for row in pristine["uuid_namespace_registry"]["rows"]}

    # This explicitly settles the audit ambiguity.  These two dependencies do
    # not occur in the rejected v23 bytes; the prior RED injected them solely
    # to demonstrate that the dashboard-specific guard could be bypassed.
    assert "source_lineage_object_id" not in uuid_rows["step_id"][2]
    assert "step_id" not in uuid_rows["source_lineage_object_id"][2]
    # v23 predates the hash-bound authority manifest; graph execution therefore
    # uses v24 while the two assertions above prove the mutation is absent in v23.
    graph_candidate = oracle.load_v24()
    pristine_graph = _graph(materializer, graph_candidate)
    assert oracle.deterministic_cycle_path(oracle.graph_adjacency(pristine_graph)) is None

    mutated = copy.deepcopy(graph_candidate)
    mutated_rows = {row[0]: row for row in mutated["uuid_namespace_registry"]["rows"]}
    mutated_rows["step_id"][2].append("source_lineage_object_id")
    mutated_rows["source_lineage_object_id"][2].append("step_id")
    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_CYCLE",
    ):
        _graph(materializer, mutated)


def test_non_dashboard_dependency_changes_edge_count_and_graph_receipt() -> None:
    artifact, _policy, materializer = _assets()
    baseline = _graph(materializer, artifact)
    mutated = copy.deepcopy(artifact)
    step = next(row for row in mutated["uuid_namespace_registry"]["rows"] if row[0] == "step_id")
    assert "source_lineage_object_id" not in step[2]
    step[2].append("source_lineage_object_id")

    changed = _graph(materializer, mutated)
    assert changed["counts"]["edge_count"] == baseline["counts"]["edge_count"] + 1
    assert changed["graph_sha256"] != baseline["graph_sha256"]


def test_general_graph_rejects_an_unknown_recipe_form_before_fixture_derivation() -> None:
    artifact, _policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    recipe = next(
        row
        for row in mutated["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    )
    recipe[1] = "EVAL(unreviewed_dynamic_recipe)"

    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID",
    ):
        _graph(materializer, mutated)


def test_all_15_frozen_digest_recipes_pass_the_closed_grammar() -> None:
    artifact, _policy, materializer = _assets()
    recipes = artifact["digest_recipe_registry"]["rows"]
    producers = {
        row[0]: row
        for row in _structural(artifact)["identity_hash_dependency_contract"][
            "producer_authority_tuple"
        ]
    }
    assert len(recipes) == 15
    for ordinal, recipe in enumerate(recipes):
        pointer = f"full_strict:#/digest_recipe_registry/rows/{ordinal}"
        materializer._validate_recipe_grammar(producers[pointer], recipe)  # noqa: SLF001


@pytest.mark.parametrize(
    "expression",
    (
        "H(value)",
        "CJ([value])",
        "SCALAR(value)",
        "TUPLE(value,other)",
        "ORDERED[(value,other)]",
        "[]",
        '(value,"literal")',
        " lowercase_hex ( SHA256 ( CJ ( value ) ) ) ",
        "hex(float8send(score))",
    ),
)
def test_closed_recipe_grammar_accepts_each_reviewed_composition(expression: str) -> None:
    _artifact, _policy, materializer = _assets()
    producer = ("", "", "", "", "", "", "#/1")
    materializer._validate_recipe_grammar(producer, [None, expression])  # noqa: SLF001


@pytest.mark.parametrize(
    "mutation",
    (
        "exact-trailing-call",
        "unknown-function",
        "unbalanced-nesting",
        "missing-separator",
        "excess-h-arity",
        "zero-h-arity",
        "empty-ordered",
    ),
)
def test_digest_recipe_closed_grammar_consumes_the_complete_input(
    mutation: str,
) -> None:
    artifact, _policy, materializer = _assets()
    mutated = copy.deepcopy(artifact)
    recipes = {row[0]: row for row in mutated["digest_recipe_registry"]["rows"]}
    if mutation == "exact-trailing-call":
        recipes["schema_sha256"][1] += ";H(H)"
    elif mutation == "unknown-function":
        recipes["task8_fdd_successor_contract_sha256"][1] = (
            'H(["full-strict-v24-task8-fdd-contract-v2",MYSTERY(contract)])'
        )
    elif mutation == "unbalanced-nesting":
        recipes["task8_fdd_successor_contract_sha256"][1] = (
            'H(["full-strict-v24-task8-fdd-contract-v2",contract)]'
        )
    elif mutation == "missing-separator":
        recipes["task8_fdd_successor_contract_sha256"][1] = (
            'H(["full-strict-v24-task8-fdd-contract-v2" contract])'
        )
    elif mutation == "excess-h-arity":
        recipes["task8_fdd_successor_contract_sha256"][1] = (
            'H(["full-strict-v24-task8-fdd-contract-v2"],contract)'
        )
    elif mutation == "zero-h-arity":
        recipes["task8_fdd_successor_contract_sha256"][1] = "H()"
    else:
        recipes["usage_path_route_hash"][1] = '["usage-path-route-v2",ORDERED[]]'

    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID",
    ):
        _graph(materializer, mutated)


def test_dashboard_sql_is_derived_equal_to_both_frozen_v2_recipe_sources() -> None:
    artifact, policy, materializer = _assets()
    contract = _structural(artifact)["dashboard_sql_recipe_contract"]
    assert contract["authority_id"] == "dashboard-root-equation-v2"
    assert contract["projection_recipe_id"] == (
        "methodology_truth_dashboard_root_projection_sha256"
    )
    assert contract["projection_recipe_version"] == (
        "methodology-truth-dashboard-root-projection-v2"
    )
    assert contract["row_payload_policy_pointer"] == (
        "#/financial_output_schema/row_payload_preimage"
    )
    assert contract["row_payload_recipe_version"] == "financial-diligence-row-v2"
    assert contract["forbidden_self_field_tuple"] == [
        "run_output_object_id",
        "dashboard_output_object_id",
    ]
    assert contract["derivation_order_tuple"] == list(oracle.ACYCLIC_DERIVATION_ORDER)
    assert contract["source_recipe_binding_tuple"] == _expected_sql_recipe_binding(
        artifact,
        policy,
    )

    materializer.validate_dashboard_sql_recipe_derivation(artifact, policy)
    body = oracle.dashboard_function_body(artifact)
    assert _REQUIRED_PROJECTION_PREFIX in body
    assert _OLD_PROJECTION_PREFIX not in body
    projection_sql = body[body.index("preimage:=") : body.index("actual_hash:=")]
    assert (
        contract["governed_projection_sql_sha256"]
        == hashlib.sha256(projection_sql.encode("utf-8")).hexdigest()
    )
    assert "dashboard_row.run_output_object_id" not in projection_sql
    assert "dashboard_output_object_id" not in projection_sql


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("projection-v1", "TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT"),
        ("wrong-projection-field", "TASK8_V24_DASHBOARD_SQL_RECIPE_DRIFT"),
        ("self-output-field", "TASK8_V24_SUPERSEDED_RECIPE_FIELD"),
        ("policy-row-self-field", "TASK8_V24_SUPERSEDED_RECIPE_FIELD"),
    ),
)
def test_dashboard_sql_recipe_admission_rejects_superseded_recipe_fields(
    mutation: str,
    expected_code: str,
) -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    candidate_policy = copy.deepcopy(policy)
    if mutation == "projection-v1":
        _replace_dashboard_sql(
            candidate,
            "methodology-truth-dashboard-root-projection-v2",
            "methodology-truth-dashboard-root-projection-v1",
        )
    elif mutation == "wrong-projection-field":
        _replace_dashboard_sql(
            candidate,
            "dashboard_row.row_disposition",
            "dashboard_row.coverage_record_id",
        )
    elif mutation == "self-output-field":
        _replace_dashboard_sql(
            candidate,
            "dashboard_row.coverage_record_id",
            "dashboard_row.run_output_object_id||dashboard_row.coverage_record_id",
        )
    else:
        candidate_policy["financial_output_schema"]["row_payload_preimage"].insert(
            1, "dashboard_output_object_id"
        )

    with pytest.raises(materializer.MaterializationError, match=expected_code):
        materializer.validate_dashboard_sql_recipe_derivation(candidate, candidate_policy)


def test_every_approved_semantic_is_bound_to_current_artifact_bytes() -> None:
    artifact, policy, materializer = _assets()
    section = _structural(artifact)
    manifest = section["approved_semantics_conservation_manifest"]
    assert [row[1] for row in manifest] == [
        row[1] for row in oracle.APPROVED_SEMANTICS_CONSERVATION_MANIFEST
    ]
    for _ordinal, semantic_id, _selector, _expected, implementing_receipts in manifest:
        assert implementing_receipts
        assert all(
            len(receipt) == 4 and receipt[2] in {"CANONICAL_JSON_SHA256", "RAW_FILE_SHA256"}
            for receipt in implementing_receipts
        )
        oracle.assert_semantic_binding(artifact, policy, semantic_id)
    materializer.validate_approved_semantics_conservation(
        artifact,
        policy,
        reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
    )


@pytest.mark.parametrize(
    "semantic_id",
    [row[1] for row in oracle.APPROVED_SEMANTICS_CONSERVATION_MANIFEST],
)
def test_regeneration_cannot_drop_an_approved_semantic_manifest_entry(
    semantic_id: str,
) -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    section = candidate["task8_structural_admission"]
    section["approved_semantics_conservation_manifest"] = [
        row for row in section["approved_semantics_conservation_manifest"] if row[1] != semantic_id
    ]
    section["approved_semantics_conservation_sha256"] = oracle.canonical_sha256(
        [
            oracle.CONSERVATION_DOMAIN,
            section["approved_semantics_conservation_manifest"],
        ]
    )

    with pytest.raises(
        materializer.MaterializationError,
        match=f"TASK8_V24_APPROVED_SEMANTIC_MISSING:{semantic_id}",
    ):
        materializer.validate_approved_semantics_conservation(
            candidate,
            policy,
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
        )


@pytest.mark.parametrize(
    "semantic_id",
    [row[1] for row in oracle.APPROVED_SEMANTICS_CONSERVATION_MANIFEST],
)
def test_regeneration_cannot_retain_a_manifest_label_while_drifting_its_bytes(
    semantic_id: str,
) -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    candidate_policy = copy.deepcopy(policy)
    if semantic_id == "STRICT_PERIOD_ORDERING":
        oracle.replace_schema_constraint(
            candidate,
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
            "period_start<=period_end AND as_of_date XOR (period_start,period_end)",
        )
    elif semantic_id == "POSITIVE_MAGNITUDE_NONNEGATIVE":
        oracle.replace_schema_constraint(
            candidate,
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
            "decimal(normalized_decimal)>=0",
        )
    elif semantic_id == "SOURCE_NORMALIZATION_PRESERVED":
        oracle.replace_schema_constraint(
            candidate,
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
            "normalized_decimal=abs(source_decimal)",
        )
    elif semantic_id == "ERASURE_0039_TO_0038_EXACT_RESTORATION":
        candidate["task8_fdd_contract"]["erasure_refusal_contract"][
            "downgrade_sql_utf8_hex_tuple"
        ].pop()
    elif semantic_id == "DASHBOARD_100_OF_100_CENSUS_GUARD":
        dashboard = next(
            row
            for row in candidate["sql_authority_registry"]["rows"]
            if row["authority_id"] == "dashboard-root-equation-v2"
        )
        dashboard["illegal_red_case_tuple"].remove("COVERAGE_CENSUS_NOT_100")
    elif semantic_id == "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT":
        candidate["task8_structural_admission"]["dashboard_sql_recipe_contract"][
            "derivation_order_tuple"
        ] = list(reversed(oracle.ACYCLIC_DERIVATION_ORDER))
    else:
        candidate["task8_fdd_contract"]["policy_binding"]["policy_id"] = "fdd-formula-policy-v1"

    with pytest.raises(
        materializer.MaterializationError,
        match=f"TASK8_V24_APPROVED_SEMANTIC_DRIFT:{semantic_id}",
    ):
        materializer.validate_approved_semantics_conservation(
            candidate,
            candidate_policy,
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
        )


@pytest.mark.parametrize(
    "semantic_id",
    [row[1] for row in oracle.APPROVED_SEMANTICS_CONSERVATION_MANIFEST],
)
def test_each_conserved_semantic_detects_a_one_byte_implementation_drift(
    semantic_id: str,
) -> None:
    artifact, policy, materializer = _assets()
    candidate = copy.deepcopy(artifact)
    candidate_policy = copy.deepcopy(policy)
    if semantic_id in {
        "STRICT_PERIOD_ORDERING",
        "POSITIVE_MAGNITUDE_NONNEGATIVE",
        "SOURCE_NORMALIZATION_PRESERVED",
    }:
        contract = next(
            row
            for row in candidate["task8_fdd_contract"]["fdd_schema_contracts"]
            if row["schema_id"] == "fdd-deterministic-calculation"
        )
        model_name, constraint_id = {
            "STRICT_PERIOD_ORDERING": (
                "FddCalcInputsV1",
                "economic-coordinate-date-order-v1",
            ),
            "POSITIVE_MAGNITUDE_NONNEGATIVE": (
                "FddNumericBindingV1",
                "positive-magnitude-source-nonnegative-v1",
            ),
            "SOURCE_NORMALIZATION_PRESERVED": (
                "FddNumericBindingV1",
                "numeric-binding-source-normalization-v1",
            ),
        }[semantic_id]
        constraint_row = next(
            row
            for row in contract["normalized_schema_snapshot"]["$defs"][model_name][
                "x-idis-cross-field-constraint-tuple"
            ]
            if row[0] == constraint_id
        )
        constraint_row.append(" ")
    elif semantic_id == "ERASURE_0039_TO_0038_EXACT_RESTORATION":
        candidate["task8_fdd_contract"]["erasure_refusal_contract"]["reason_code"] += " "
    elif semantic_id == "DASHBOARD_100_OF_100_CENSUS_GUARD":
        dashboard = next(
            row
            for row in candidate["sql_authority_registry"]["rows"]
            if row["authority_id"] == "dashboard-root-equation-v2"
        )
        dashboard["authority_version"] += " "
    elif semantic_id == "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT":
        row = next(
            row
            for row in candidate["uuid_namespace_registry"]["rows"]
            if row[0] == "dashboard_row_id"
        )
        row[1] += " "
    else:
        candidate["task8_fdd_contract"]["policy_binding"]["artifact_relative_path"] += " "

    with pytest.raises(
        materializer.MaterializationError,
        match=f"TASK8_V24_APPROVED_SEMANTIC_BYTE_DRIFT:{semantic_id}",
    ):
        materializer.validate_approved_semantics_conservation(
            candidate,
            candidate_policy,
            reviewer_map_raw=oracle.POLICY_V2_REVIEWER_MAP.read_bytes(),
        )


def test_v24_structural_oracle_has_no_self_affirming_hash_inputs() -> None:
    # The structural oracle is intentionally literal.  This guards against a
    # future cleanup importing the production materializer into the RED oracle
    # and thereby making the admission tests self-affirming.
    source = Path(__file__).with_name("_v24_structural_oracle.py").read_text(encoding="utf-8")
    assert "materialize_full_strict_v24_preimage" in source  # path only, lazy import
    assert "from scripts" not in source
    assert "import scripts" not in source
    assert hashlib.sha256(oracle.V21_ARTIFACT.read_bytes()).hexdigest() == (
        oracle.V21_ARTIFACT_SHA256
    )
