"""RED-first admission for the bounded full-strict-v22 successor contract."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from . import _v22_oracle as oracle

_ALLOWED_CHANGED_SECTIONS = {
    "metadata",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "schema_registry",
    "future_native_authority_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
    "erasure_execution_registry",
    "activation_assertions",
    "task8_fdd_contract",
}


def _assets() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Any,
]:
    v21 = oracle.load_v21()
    policy = oracle.load_policy()
    v22 = oracle.load_v22()
    materializer = oracle.load_module(
        oracle.V22_MATERIALIZER,
        "_task8_v22_materializer_red",
        "TASK8_V22_MATERIALIZER_NOT_IMPLEMENTED",
    )
    return v21, policy, v22, materializer


def _candidate(v22: Mapping[str, Any]) -> dict[str, Any]:
    return oracle.candidate_from_frozen(v22)


def _built_v22(
    v21: dict[str, Any],
    policy: dict[str, Any],
    materializer: Any,
) -> dict[str, Any]:
    return materializer.build_v22(
        copy.deepcopy(v21),
        copy.deepcopy(policy),
        policy_artifact_sha256=hashlib.sha256(oracle.POLICY_ARTIFACT.read_bytes()).hexdigest(),
        policy_fixture_sha256=hashlib.sha256(oracle.POLICY_FIXTURE.read_bytes()).hexdigest(),
    )


def _authority(
    artifact: Mapping[str, Any],
    authority_id: str,
) -> Mapping[str, Any]:
    rows = oracle.authority_rows(artifact)
    assert authority_id in rows
    return rows[authority_id]


def _schema_target(contract: Mapping[str, Any], target_name: str) -> dict[str, Any]:
    snapshot = contract["normalized_schema_snapshot"]
    targets = {str(name): schema for name, schema in snapshot["targets"]}
    assert target_name in targets
    target = copy.deepcopy(targets[target_name])
    target.setdefault("$schema", "https://json-schema.org/draft/2020-12/schema")
    if "$defs" not in target and "$defs" in snapshot:
        target["$defs"] = copy.deepcopy(snapshot["$defs"])
    Draft202012Validator.check_schema(target)
    return target


def _assert_schema_accepts(schema: Mapping[str, Any], payload: object) -> None:
    errors = tuple(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)
    )
    assert errors == ()


def _assert_schema_rejects(schema: Mapping[str, Any], payload: object) -> None:
    assert tuple(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))


def _legal_fdd_factual_output() -> dict[str, Any]:
    return {
        "subject": "company",
        "predicate": "fdd.reported_gross_margin_ratio",
        "claim_class": "FINANCIAL_FACT",
        "value": {
            "type": "signed_ratio",
            "value": "-0.2501",
            "as_of": None,
            "time_window": {
                "label": "FY2025",
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
            },
        },
        "period": {
            "kind": "range",
            "date": None,
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
        },
        "unit": "ratio",
        "claim_text": "governed factual output",
        "accepted_source_span_ids": ["00000000-0000-4000-8000-000000000010"],
        "accepted_locator_sha256s": ["a" * 64],
        "methodology_question_record_id": "00000000-0000-4000-8000-000000000011",
        "factual_output_id": None,
    }


def _legal_fdd_calculation() -> dict[str, Any]:
    return {
        "calc_type": "GROSS_MARGIN",
        "inputs": {
            "schema_version": "fdd-calc-inputs-v1",
            "formula_policy_id": "fdd-formula-policy-v1",
            "formula_policy_sha256": (
                "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2"
            ),
            "formula_sha256": "b" * 64,
            "coordinate": {
                "period_kind": "range",
                "period_start": "2025-01-01",
                "period_end": "2025-12-31",
                "as_of_date": None,
                "base_currency": "USD",
                "monetary_scale_unit": "units",
            },
            "bindings": [
                {
                    "binding_kind": "NUMERIC",
                    "ordinal": 0,
                    "role": "recurring_revenue",
                    "source_decimal": "100",
                    "normalized_decimal": "100",
                    "sign_transform": "POSITIVE_MAGNITUDE",
                    "source_sheet": "P&L",
                    "source_row": 4,
                    "predicate": "fdd.recurring_revenue",
                    "coverage_record_id": "mc_000000000000000000000001",
                    "methodology_version_record_id": "00000000-0000-4000-8000-000000000002",
                    "methodology_question_record_id": "00000000-0000-4000-8000-000000000003",
                    "factual_output_record_id": "00000000-0000-4000-8000-000000000004",
                    "claim_id": "00000000-0000-4000-8000-000000000005",
                    "evidence_ids": ["00000000-0000-4000-8000-000000000006"],
                    "sanad_ids": ["00000000-0000-4000-8000-000000000007"],
                    "period": {
                        "kind": "range",
                        "date": None,
                        "start_date": "2025-01-01",
                        "end_date": "2025-12-31",
                    },
                    "currency": "USD",
                    "unit": "units",
                    "value_sha256": "c" * 64,
                }
            ],
            "parent_calculations": [],
            "assumptions": [],
        },
        "output": {
            "primary_name": "gross_margin",
            "primary_decimal": "0.25",
            "primary_scale": 4,
            "secondary_values": [],
            "unit": "ratio",
            "currency": None,
            "formula_disposition": "IN_BALANCE",
        },
    }


def test_v22_assets_rebuild_from_frozen_v21_and_inverse_is_byte_exact() -> None:
    v21, policy, v22, materializer = _assets()

    oracle.validate_v22(v22)
    rebuilt = materializer.build_v22(
        copy.deepcopy(v21),
        copy.deepcopy(policy),
        policy_artifact_sha256=hashlib.sha256(oracle.POLICY_ARTIFACT.read_bytes()).hexdigest(),
        policy_fixture_sha256=hashlib.sha256(oracle.POLICY_FIXTURE.read_bytes()).hexdigest(),
    )
    assert rebuilt["metadata"]["append_frozen"] is False
    assert materializer._render(rebuilt) == materializer._render(_candidate(v22))
    assert (
        materializer._render(materializer.project_v22_to_v21(copy.deepcopy(v22)))
        == oracle.V21_ARTIFACT.read_bytes()
    )
    assert (
        materializer._render(materializer.project_v22_to_v21(copy.deepcopy(rebuilt)))
        == oracle.V21_ARTIFACT.read_bytes()
    )
    materializer.validate_candidate_artifact(
        rebuilt,
        policy_artifact_raw=oracle.POLICY_ARTIFACT.read_bytes(),
        policy_fixture_raw=oracle.POLICY_FIXTURE.read_bytes(),
    )


def test_v22_exact_diff_has_only_the_approved_successor_surfaces() -> None:
    v21, _policy, v22, materializer = _assets()
    changed = oracle.changed_paths(v21, v22)

    assert {path[0] for path in changed} == _ALLOWED_CHANGED_SECTIONS
    for section in oracle.BYTE_IDENTICAL_SECTIONS:
        assert v22[section] == v21[section], section

    # The complete semantic and routing universe is conserved byte-for-byte.
    assert len(v22["semantic_step_registry"]["rows"]) == 28
    assert len(v22["edge_rule_registry"]["rows"]) == 48
    assert len(v22["usage_route_registry"]["rows"]) == 1_756
    assert len(v22["usage_extractor_registry"]["rows"]) == 19
    assert len(v22["provider_registry"]["rows"]) == 18
    assert v22["rights_index_policy_registry"] == v21["rights_index_policy_registry"]

    # Existing relation definitions and erasure ordering remain the same
    # universe. Only the two already-reviewed dashboard definitions may be
    # retargeted to the successor guards.
    old_native = v21["future_native_authority_registry"]["rows"]
    new_native = v22["future_native_authority_registry"]["rows"]
    assert len(new_native) == len(old_native) == 18
    assert new_native[1:] == old_native[1:]
    assert new_native[0]["planned_table"] == "methodology_truth_dashboard_rows"
    old_children = v21["future_child_authority_registry"]["rows"]
    new_children = v22["future_child_authority_registry"]["rows"]
    assert len(new_children) == len(old_children) == 5
    assert new_children[:-1] == old_children[:-1]
    assert new_children[-1]["table_name"] == "methodology_truth_dashboard_root_links"
    assert v22["erasure_baseline"] == v21["erasure_baseline"]
    assert v22["erasure_materialized"] == v21["erasure_materialized"]

    materializer.validate_exact_diff(v21, _candidate(v22))


def test_v22_appends_separate_fdd_schemas_and_never_widens_v21_models() -> None:
    v21, _policy, v22, _materializer = _assets()
    old_rows = v21["schema_registry"]["rows"]
    new_rows = v22["schema_registry"]["rows"]

    assert len(old_rows) == 66
    assert new_rows[:66] == old_rows
    assert len(new_rows) == 68
    assert (
        tuple((row["schema_id"], row["schema_version"]) for row in new_rows[66:])
        == oracle.FDD_SCHEMA_ROWS
    )

    contracts = v22["task8_fdd_contract"]["fdd_schema_contracts"]
    for schema_row, contract in zip(new_rows[66:], contracts, strict=True):
        assert schema_row["normalized_schema_snapshot"] == contract["normalized_schema_snapshot"]
        assert schema_row["expected_schema_sha256"] == contract["expected_schema_sha256"]

    legacy = {(row["schema_id"], row["schema_version"]): row for row in old_rows}
    assert (
        new_rows[
            next(
                index
                for index, row in enumerate(new_rows)
                if row["schema_id"] == "deterministic-calculation"
            )
        ]
        == legacy[("deterministic-calculation", "task7-deterministic-calculation-v1")]
    )
    assert "FddSignedRatioValueV1" not in str(
        legacy[("methodology-factual-output", "task7-methodology-factual-output-v1")]
    )
    assert (
        tuple(tuple(row) for row in v22["task8_fdd_contract"]["selector_contracts"])
        == oracle.SELECTOR_CONTRACTS
    )


def test_v22_fdd_factual_schema_executes_the_frozen_legacy_union_plus_exact_signed_ratio() -> None:
    _v21, _policy, v22, _materializer = _assets()
    factual_contract = v22["task8_fdd_contract"]["fdd_schema_contracts"][0]
    schema = _schema_target(factual_contract, "FddMethodologyFactualOutputV1")

    legal = _legal_fdd_factual_output()
    _assert_schema_accepts(schema, legal)

    # The successor is a strict append to the complete frozen factual-value
    # union, not an open ``object`` branch that overlaps signed_ratio.
    legacy = copy.deepcopy(legal)
    legacy["predicate"] = "fdd.recurring_revenue"
    legacy["value"] = {
        "type": "monetary",
        "amount": "100",
        "currency": "USD",
        "as_of": None,
        "time_window": {
            "label": "FY2025",
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
        },
    }
    legacy["unit"] = "units"
    _assert_schema_accepts(schema, legacy)


@pytest.mark.parametrize(
    ("mutation",),
    (
        ("negative-zero",),
        ("binary-float",),
        ("exponent",),
        ("trailing-zero",),
        ("both-period-forms",),
        ("neither-period-form",),
        ("wrong-unit",),
        ("extra-field",),
    ),
)
def test_v22_signed_ratio_schema_rejects_every_noncanonical_or_ambiguous_shape(
    mutation: str,
) -> None:
    _v21, _policy, v22, _materializer = _assets()
    factual_contract = v22["task8_fdd_contract"]["fdd_schema_contracts"][0]
    schema = _schema_target(factual_contract, "FddMethodologyFactualOutputV1")
    payload = _legal_fdd_factual_output()

    if mutation == "negative-zero":
        payload["value"]["value"] = "-0"
    elif mutation == "binary-float":
        payload["value"]["value"] = -0.25
    elif mutation == "exponent":
        payload["value"]["value"] = "-2.5e-1"
    elif mutation == "trailing-zero":
        payload["value"]["value"] = "-0.2500"
    elif mutation == "both-period-forms":
        payload["value"]["as_of"] = "2025-12-31"
    elif mutation == "neither-period-form":
        payload["value"]["time_window"] = None
    elif mutation == "wrong-unit":
        payload["unit"] = "percentage_points"
    else:
        payload["value"]["unexpected"] = "forbidden"
    _assert_schema_rejects(schema, payload)


def test_v22_formula_schema_executes_a_complete_closed_legal_constructor() -> None:
    _v21, _policy, v22, _materializer = _assets()
    formula_contract = v22["task8_fdd_contract"]["fdd_schema_contracts"][1]
    schema = _schema_target(formula_contract, "FddDeterministicCalculationV1")
    _assert_schema_accepts(schema, _legal_fdd_calculation())


@pytest.mark.parametrize(
    ("mutation",),
    (
        ("null-input-fields",),
        ("binary-float",),
        ("negative-zero",),
        ("bad-coordinate-nullability",),
        ("bad-binding-discriminator",),
        ("false-completeness",),
        ("nonempty-assumptions",),
        ("malformed-hash",),
        ("noncanonical-uuid",),
        ("unknown-field",),
        ("missing-field",),
    ),
)
def test_v22_formula_schema_rejects_null_open_or_noncanonical_constructor_shapes(
    mutation: str,
) -> None:
    _v21, _policy, v22, _materializer = _assets()
    formula_contract = v22["task8_fdd_contract"]["fdd_schema_contracts"][1]
    schema = _schema_target(formula_contract, "FddDeterministicCalculationV1")
    payload = _legal_fdd_calculation()
    numeric = payload["inputs"]["bindings"][0]

    if mutation == "null-input-fields":
        payload["inputs"] = dict.fromkeys(payload["inputs"])
    elif mutation == "binary-float":
        numeric["normalized_decimal"] = 100.0
    elif mutation == "negative-zero":
        payload["output"]["primary_decimal"] = "-0"
    elif mutation == "bad-coordinate-nullability":
        payload["inputs"]["coordinate"]["as_of_date"] = "2025-12-31"
    elif mutation == "bad-binding-discriminator":
        numeric["binding_kind"] = "SET_COMPLETENESS"
    elif mutation == "false-completeness":
        payload["inputs"]["bindings"] = [
            {
                "binding_kind": "SET_COMPLETENESS",
                "ordinal": 0,
                "role": "reconciling_set",
                "confirmed": False,
                "source_sheet": "P&L",
                "source_row": 30,
                "predicate": "fdd.tax_reconciling_item_set_complete",
                "coverage_record_id": "mc_000000000000000000000001",
                "methodology_version_record_id": "00000000-0000-4000-8000-000000000002",
                "methodology_question_record_id": "00000000-0000-4000-8000-000000000003",
                "factual_output_record_id": "00000000-0000-4000-8000-000000000004",
                "claim_id": "00000000-0000-4000-8000-000000000005",
                "evidence_ids": ["00000000-0000-4000-8000-000000000006"],
                "sanad_ids": ["00000000-0000-4000-8000-000000000007"],
                "period": numeric["period"],
                "value_sha256": "c" * 64,
            }
        ]
    elif mutation == "nonempty-assumptions":
        payload["inputs"]["assumptions"] = [{"assumption_id": "x", "value": "1"}]
    elif mutation == "malformed-hash":
        payload["inputs"]["formula_sha256"] = "ABC"
    elif mutation == "noncanonical-uuid":
        numeric["claim_id"] = "00000000-0000-4000-8000-00000000000A"
    elif mutation == "unknown-field":
        payload["output"]["extension"] = 1
    else:
        del numeric["predicate"]
    _assert_schema_rejects(schema, payload)


def test_v22_builder_fails_before_publication_when_a_schema_snapshot_is_not_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v21, policy, _v22, materializer = _assets()
    invalid = materializer._formula_schema_contract()
    invalid["normalized_schema_snapshot"]["targets"][0][1]["type"] = "not-a-json-type"
    monkeypatch.setattr(materializer, "_formula_schema_contract", lambda: invalid)

    with pytest.raises(
        materializer.MaterializationError,
        match="TASK8_V22_SCHEMA_SNAPSHOT_INVALID",
    ):
        materializer.build_v22(
            copy.deepcopy(v21),
            copy.deepcopy(policy),
            policy_artifact_sha256=hashlib.sha256(oracle.POLICY_ARTIFACT.read_bytes()).hexdigest(),
            policy_fixture_sha256=hashlib.sha256(oracle.POLICY_FIXTURE.read_bytes()).hexdigest(),
        )


def test_v22_replaces_dashboard_v1_adds_partial_guard_and_preserves_complete_guard() -> None:
    v21, policy, _canonical_v22, materializer = _assets()
    v22 = _built_v22(v21, policy, materializer)
    old_authorities = oracle.authority_rows(v21)
    new_authorities = oracle.authority_rows(v22)

    assert "dashboard-root-equation-v1" in old_authorities
    assert "dashboard-root-equation-v1" not in new_authorities
    assert "dashboard-root-equation-v2" in new_authorities
    assert "dashboard-partial-native-root-exact-one-v1" in new_authorities
    assert (
        new_authorities["native-root-exact-one-v1"] == old_authorities["native-root-exact-one-v1"]
    )
    assert len(v22["sql_authority_registry"]["rows"]) == (
        len(v21["sql_authority_registry"]["rows"]) + 1
    )

    dashboard = _authority(v22, "dashboard-root-equation-v2")
    assert dashboard["target_tuple"] == [
        ["methodology_truth_dashboard_rows", "ctrg_v2_dashboard_root_equation"],
        [
            "methodology_truth_dashboard_root_links",
            "ctrg_v3_dashboard_root_equation",
        ],
    ]
    assert dashboard["legal_red_case_tuple"]
    assert dashboard["illegal_red_case_tuple"]
    assert dashboard["direct_sql_red_case_tuple"]
    decoded_dashboard = "\n".join(oracle.decode_sql_tuple(dashboard, "body_sql_utf8_hex_tuple"))
    for literal in (
        "EVIDENCE_FOUND",
        "EVIDENCE_NOT_FOUND",
        "ANSWERED",
        "GAP",
        "RED_FLAG",
        "ADVERSE_CONTRADICTION",
        "CALCULATION",
    ):
        assert literal in decoded_dashboard
    assert "PARTIAL" not in decoded_dashboard
    assert dashboard["declared_parent_relation_tuple"] == [
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_extraction_tasks",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
        "deterministic_calculations",
        "run_output_objects",
    ]

    # The successor preserves v21's complete two-way projection proof while
    # changing only the mixed-result disposition semantics.
    for root_kind, parent in (
        ("RESULT", "methodology_extraction_results"),
        ("EVIDENCE", "methodology_result_evidence_links"),
        ("SANAD", "methodology_result_sanad_links"),
        ("CALCULATION", "deterministic_calculations"),
        ("ADVERSE_CONTRADICTION", "methodology_result_defect_links"),
    ):
        assert f"'{root_kind}'::text" in decoded_dashboard
        assert f"public.{parent}" in decoded_dashboard
    assert "expected_raw(root_kind,root_output_object_id,root_output_object_kind)" in (
        decoded_dashboard
    )
    assert "missing_roots AS" in decoded_dashboard
    assert "extra_roots AS" in decoded_dashboard
    assert decoded_dashboard.count(" EXCEPT SELECT ") >= 2
    assert "root_projection_sha256" in decoded_dashboard
    assert "methodology-truth-dashboard-root-projection-v1" in decoded_dashboard

    # Required calculation types are exact: every required=true type exists,
    # and calculations outside that declared set are rejected.
    assert "jsonb_array_elements(dashboard_row.question_payload->'required_calculations')" in (
        decoded_dashboard
    )
    assert "(item->>'required')::boolean" in decoded_dashboard
    assert "item->>'calc_type'=c.calc_type" in decoded_dashboard
    assert "SELECT count(*) FROM public.deterministic_calculations c" in (decoded_dashboard)
    assert ")<>1" in decoded_dashboard
    assert "o.output_schema_version='task8-fdd-deterministic-calculation-v1'" in (decoded_dashboard)
    required_missing = decoded_dashboard.index(
        "(SELECT count(*) FROM public.deterministic_calculations c"
    )
    undeclared = decoded_dashboard.index(
        "EXISTS (SELECT 1 FROM public.deterministic_calculations c",
        required_missing + 1,
    )
    assert required_missing < undeclared

    # A per-row truth proof is insufficient: once any dashboard row exists,
    # the deferred authority must prove the exact selected 100-row run set.
    assert {
        "COVERAGE_CENSUS_NOT_100",
        "DASHBOARD_ROW_CENSUS_NOT_100",
        "COVERAGE_SET_MISSING",
        "COVERAGE_SET_EXTRA",
    }.issubset(set(dashboard["illegal_red_case_tuple"]))
    assert "WITH dashboard_scopes AS" in decoded_dashboard
    assert "incomplete_scopes AS" in decoded_dashboard
    assert decoded_dashboard.count("<>100") >= 2
    assert decoded_dashboard.count(" EXCEPT SELECT ") >= 4

    partial = _authority(v22, "dashboard-partial-native-root-exact-one-v1")
    current = _authority(v21, "current-output-exact-one-v1")
    assert partial["target_tuple"] == [
        *current["target_tuple"],
        [
            "methodology_truth_dashboard_rows",
            "ctrg_task8_partial_native_root_dashboard_rows",
        ],
    ]
    assert partial["legal_red_case_tuple"]
    assert partial["illegal_red_case_tuple"]
    assert partial["direct_sql_red_case_tuple"]

    dashboard_native = v22["future_native_authority_registry"]["rows"][0]
    assert dashboard_native["sql_authority_id_tuple"] == [
        "dashboard-root-equation-v2",
        "approved-erasure-append-only-v1",
        "native-root-exact-one-v1",
        "dashboard-partial-native-root-exact-one-v1",
    ]
    dashboard_child = v22["future_child_authority_registry"]["rows"][-1]
    assert "idis_task8_dashboard_root_equation_v2" in dashboard_child["projection_guard"]
    assert dashboard_child["sql_authority_id_tuple"] == [
        "dashboard-root-equation-v2",
        "approved-erasure-append-only-v1",
    ]


def test_v22_partial_exact_one_replaces_the_colliding_0038_targets_and_restores_them() -> None:
    v21, policy, _v22, materializer = _assets()
    v22 = _built_v22(v21, policy, materializer)
    current = _authority(v21, "current-output-exact-one-v1")
    partial = _authority(v22, "dashboard-partial-native-root-exact-one-v1")
    current_statements = oracle.decode_sql_tuple(current, "body_sql_utf8_hex_tuple")
    partial_statements = oracle.decode_sql_tuple(partial, "body_sql_utf8_hex_tuple")

    old_function = next(
        statement
        for statement in current_statements
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_output_exact_one_v1()")
    )
    old_helper = next(
        statement
        for statement in current_statements
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_native_hashes_v1(")
    )
    assert len(partial_statements) == 3
    new_helper, new_function, dashboard_trigger = partial_statements
    assert new_helper.startswith(
        "CREATE OR REPLACE FUNCTION public.idis_task7_current_native_hashes_v1("
    )
    assert new_function.startswith(
        "CREATE OR REPLACE FUNCTION public.idis_task7_current_output_exact_one_v1()"
    )
    assert dashboard_trigger == (
        "CREATE CONSTRAINT TRIGGER ctrg_task8_partial_native_root_dashboard_rows "
        "AFTER INSERT OR UPDATE OR DELETE ON public.methodology_truth_dashboard_rows "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "public.idis_task7_current_output_exact_one_v1()"
    )

    # This is the generation-time reproduction of the operator-census blocker:
    # the installed 0038 function scans every NATIVE_ROW but has neither the
    # dashboard child in its census nor a dashboard projection in its helper.
    assert "public.methodology_truth_dashboard_rows" not in old_function
    assert "ro.output_kind<>'METHODOLOGY_TRUTH_DASHBOARD_ROW'" not in old_function
    assert "public.methodology_truth_dashboard_rows" in new_function
    assert "public.methodology_truth_dashboard_rows" in new_helper
    assert "ro.output_kind<>'METHODOLOGY_TRUTH_DASHBOARD_ROW'" not in new_function
    assert "public.idis_task7_current_native_hashes_v1" in new_function
    assert "STEP_OUTPUT_MISSING" in new_function
    assert "pg_catalog.jsonb_build_array(d.dashboard_row_id::text)" in new_helper
    assert (
        "pg_catalog.jsonb_build_array(d.coverage_record_id,"
        "d.root_projection_sha256,d.row_disposition,d.row_payload_sha256)"
    ) in new_helper
    helper_extension = (
        " UNION ALL SELECT d.run_output_object_kind,"
        "pg_catalog.jsonb_build_array(d.dashboard_row_id::text),"
        "pg_catalog.jsonb_build_array(d.coverage_record_id,"
        "d.root_projection_sha256,d.row_disposition,d.row_payload_sha256) "
        "FROM public.methodology_truth_dashboard_rows d WHERE "
        "d.tenant_id=p_tenant_id AND d.deal_id=p_deal_id AND "
        "d.run_id=p_run_id AND d.run_output_object_id=p_output_object_id"
    )
    assert (
        new_helper.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1).replace(
            helper_extension, "", 1
        )
        == old_helper
    )
    old_function_marker = (
        " FROM public.deliverables WHERE run_output_object_id IS NOT NULL), scoped_natives AS"
    )
    new_function_marker = (
        " FROM public.deliverables WHERE run_output_object_id IS NOT NULL "
        "UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
        "run_output_object_kind FROM public.methodology_truth_dashboard_rows "
        "WHERE run_output_object_id IS NOT NULL), scoped_natives AS"
    )
    assert (
        new_function.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1).replace(
            new_function_marker, old_function_marker, 1
        )
        == old_function
    )

    partial_contract = v22["task8_fdd_contract"]["partial_exact_one_contract"]
    assert partial_contract["replaced_current_authority_id"] == ("current-output-exact-one-v1")
    assert partial_contract["replaced_current_function_signature"] == (
        "public.idis_task7_current_output_exact_one_v1()"
    )
    assert partial_contract["replaced_current_helper_signature"] == (
        "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
    )
    assert partial_contract["replaced_current_target_tuple"] == current["target_tuple"]
    assert partial_contract["current_target_triggers_preserved_unchanged"] is True
    assert partial_contract["downgrade_restores_replaced_functions_unchanged"] is True
    downgrade = oracle.decode_sql_tuple(
        partial_contract,
        "downgrade_sql_utf8_hex_tuple",
    )
    dashboard_drop = (
        "DROP TRIGGER ctrg_task8_partial_native_root_dashboard_rows "
        "ON public.methodology_truth_dashboard_rows"
    )
    assert downgrade == (
        dashboard_drop,
        old_helper.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
        old_function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    )


def test_v22_partial_exact_one_generation_rejects_unrecognized_0038_guard_bytes() -> None:
    v21, _policy, _v22, materializer = _assets()
    current = copy.deepcopy(_authority(v21, "current-output-exact-one-v1"))
    statements = list(oracle.decode_sql_tuple(current, "body_sql_utf8_hex_tuple"))
    function_ordinal = next(
        ordinal
        for ordinal, statement in enumerate(statements)
        if statement.startswith("CREATE FUNCTION public.idis_task7_current_output_exact_one_v1()")
    )
    statements[function_ordinal] = statements[function_ordinal].replace(
        "STEP_OUTPUT_MISSING",
        "UNREVIEWED_DRIFT",
        1,
    )
    current["body_sql_utf8_hex_tuple"] = [
        statement.encode("utf-8").hex() for statement in statements
    ]

    try:

        def operation() -> object:
            return materializer._partial_exact_one_authority(28, current)

        with pytest.raises(
            materializer.MaterializationError,
            match="TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID",
        ):
            operation()
    except TypeError:
        pytest.fail("partial guard generation does not close over the installed 0038 bytes")


def test_v22_dashboard_acl_row_binds_the_exact_successor_body_and_read_closure() -> None:
    v21, policy, _canonical_v22, materializer = _assets()
    v22 = _built_v22(v21, policy, materializer)
    authority = _authority(v22, "dashboard-root-equation-v2")
    statements = oracle.decode_sql_tuple(authority, "body_sql_utf8_hex_tuple")
    function_statement = next(
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
    )
    function_body = function_statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    assert function_body
    body_sha256 = hashlib.sha256(function_body.encode("utf-8")).hexdigest()

    acl = v22["sql_authority_registry"]["guard_function_acl_registry"]
    dashboard_rows = [
        row for row in acl["function_rows"] if row["authority_id"] == "dashboard-root-equation-v2"
    ]
    assert len(dashboard_rows) == 1
    row = dashboard_rows[0]
    assert row["function_signature"] == ("public.idis_task8_dashboard_root_equation_v2()")
    assert row["definition_locator"] == (
        "sql_authority_registry.rows[0].body_sql_utf8_hex_tuple[1].function[0]"
    )
    assert (
        row["sql_authority_parent_relation_tuple"] == (authority["declared_parent_relation_tuple"])
    )
    assert row["declared_parent_relation_tuple"] == (authority["declared_parent_relation_tuple"])
    assert row["select_relation_tuple"] == authority["declared_parent_relation_tuple"]
    for _relation, _columns, evidence in row["direct_read_column_tuple"]:
        assert evidence == ["SQL_BODY_SHA256", body_sha256]
    assert all(
        evidence == [["SQL_BODY_SHA256", body_sha256]]
        for _relation, _columns, evidence in row["transitive_read_column_tuple"]
    )
    assert "idis_task8_dashboard_root_equation_v2" in bytes.fromhex(
        row["owner_sql_utf8_hex"]
    ).decode("utf-8")
    assert all(
        "idis_task8_dashboard_root_equation_v2" in bytes.fromhex(value).decode("utf-8")
        for value in row["acl_sql_utf8_hex_tuple"]
    )
    assert not any(
        row["authority_id"] == "dashboard-root-equation-v1" for row in acl["function_rows"]
    )

    # Column-limited SECURITY DEFINER grants must cover the exact v2 body.
    # SELECT d.* would either require a table-wide grant or fail at runtime.
    assert "SELECT d.*" not in function_body
    direct_columns = {
        relation: columns for relation, columns, _evidence in row["direct_read_column_tuple"]
    }
    assert direct_columns["methodology_truth_dashboard_rows"] == [
        "coverage_record_id",
        "dashboard_row_id",
        "deal_id",
        "root_projection_sha256",
        "row_disposition",
        "run_id",
        "run_output_object_id",
        "tenant_id",
    ]
    assert direct_columns["methodology_truth_dashboard_root_links"] == [
        "dashboard_root_link_id",
        "dashboard_row_id",
        "deal_id",
        "root_kind",
        "root_ordinal",
        "root_output_object_id",
        "root_output_object_kind",
        "run_id",
        "tenant_id",
    ]
    assert "output_schema_version" in direct_columns["run_output_objects"]
    assert (
        "pg_catalog.pg_advisory_xact_lock( pg_catalog.hashtextextended( "
        "'idis-task7-erasure-tenant-lock-v1:'||scope_tenant::text,0 ) )"
    ) in function_body
    assert function_body.index("pg_catalog.pg_advisory_xact_lock") < (
        function_body.index("IF EXISTS")
    )


def test_v22_partial_exact_one_acl_row_is_hash_bound_and_future_inactive() -> None:
    v21, _policy, v22, _materializer = _assets()
    authority = _authority(v22, "dashboard-partial-native-root-exact-one-v1")
    statements = oracle.decode_sql_tuple(authority, "body_sql_utf8_hex_tuple")
    helper_statement, function_statement, _dashboard_trigger = statements
    helper_body = helper_statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    function_body = function_statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    helper_body_sha256 = hashlib.sha256(helper_body.encode("utf-8")).hexdigest()
    function_body_sha256 = hashlib.sha256(function_body.encode("utf-8")).hexdigest()

    acl = v22["sql_authority_registry"]["guard_function_acl_registry"]
    matches = {
        row["function_signature"]: row
        for row in acl["function_rows"]
        if row["authority_id"] == "dashboard-partial-native-root-exact-one-v1"
    }
    assert set(matches) == {
        "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)",
        "public.idis_task7_current_output_exact_one_v1()",
    }
    helper = matches["public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"]
    function = matches["public.idis_task7_current_output_exact_one_v1()"]
    assert helper["definition_locator"] == (
        "sql_authority_registry.rows[28].body_sql_utf8_hex_tuple[0].function[0]"
    )
    assert function["definition_locator"] == (
        "sql_authority_registry.rows[28].body_sql_utf8_hex_tuple[1].function[0]"
    )
    expected_parents = [
        *_authority(v21, "current-output-exact-one-v1")["declared_parent_relation_tuple"],
        "methodology_truth_dashboard_rows",
    ]
    for row in (helper, function):
        assert row["install_scope"] == "FUTURE_INACTIVE"
        assert row["owner_role"] == "idis_task7_guard_function_owner"
        assert row["public_execute"] is False
        assert row["application_role_execute"] is False
        assert row["declared_parent_relation_tuple"] == expected_parents
        dashboard_reads = [
            item
            for item in row["direct_read_column_tuple"]
            if item[0] == "methodology_truth_dashboard_rows"
        ]
        assert len(dashboard_reads) == 1
    assert helper["security_mode"] == "INVOKER"
    assert function["security_mode"] == "DEFINER"
    assert helper["direct_read_column_tuple"][-1] == [
        "methodology_truth_dashboard_rows",
        [
            "coverage_record_id",
            "dashboard_row_id",
            "deal_id",
            "root_projection_sha256",
            "row_disposition",
            "row_payload_sha256",
            "run_id",
            "run_output_object_id",
            "run_output_object_kind",
            "tenant_id",
        ],
        ["SQL_BODY_SHA256", helper_body_sha256],
    ]
    assert function["direct_read_column_tuple"][-1] == [
        "methodology_truth_dashboard_rows",
        ["deal_id", "run_id", "run_output_object_id", "run_output_object_kind", "tenant_id"],
        ["SQL_BODY_SHA256", function_body_sha256],
    ]
    assert function["target_trigger_tuple"] == authority["target_tuple"]
    assert len(function["target_event_coordinate_tuple"]) == 57
    assert {
        (coordinate[0], coordinate[1], coordinate[2], coordinate[3], coordinate[4])
        for coordinate in function["target_event_coordinate_tuple"]
    } == {
        (
            table,
            trigger,
            "AFTER",
            event,
            "ROW",
        )
        for table, trigger in authority["target_tuple"]
        for event in ("INSERT", "UPDATE", "DELETE")
    }


def test_v22_current48_refuses_dashboard_erasure_with_exact_acl_and_zero_scope_drift() -> None:
    v21, _policy, v22, _materializer = _assets()
    old = v21["erasure_execution_registry"]["rows"][0]
    new = v22["erasure_execution_registry"]["rows"][0]

    for field in ("current_relation_tuple", "future_relation_tuple", "complete_relation_tuple"):
        assert new[field] == old[field]
    assert (len(new["current_relation_tuple"]), len(new["future_relation_tuple"])) == (48, 38)
    assert set(new["current_relation_tuple"]).isdisjoint(new["future_relation_tuple"])
    assert set(new["current_relation_tuple"]) | set(new["future_relation_tuple"]) == set(
        new["complete_relation_tuple"]
    )
    assert "methodology_truth_dashboard_rows" not in new["current_relation_tuple"]
    assert "methodology_truth_dashboard_rows" in new["future_relation_tuple"]
    assert new["declared_read_relation_tuple"] == [
        *old["declared_read_relation_tuple"],
        "methodology_truth_dashboard_rows",
    ]
    assert new["advisory_lock_tuple"] == old["advisory_lock_tuple"]

    body = oracle.decode_sql_tuple(new, "current_body_sql_utf8_hex_tuple")
    body_text = "\n".join(body)
    assert oracle.ERASURE_REFUSAL_CODE in body_text
    assert "methodology_truth_dashboard_rows" in body_text
    assert "tenant_id" in body_text and "deal_id" in body_text
    assert body_text.index("pg_advisory_xact_lock") < body_text.index(
        "methodology_truth_dashboard_rows"
    )
    first_delete = min(
        (body_text.find(token) for token in ("DELETE FROM", "UPDATE ") if token in body_text),
        default=-1,
    )
    assert first_delete > body_text.index(oracle.ERASURE_REFUSAL_CODE)

    acl = oracle.decode_sql_tuple(new, "current_acl_sql_utf8_hex_tuple")
    dashboard_acl = tuple(
        statement for statement in acl if "methodology_truth_dashboard_rows" in statement
    )
    assert dashboard_acl == (
        "GRANT SELECT (tenant_id,deal_id) ON TABLE "
        "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner",
    )
    assert not any(
        privilege in dashboard_acl[0] for privilege in ("INSERT", "UPDATE", "DELETE", " ALL ")
    )


def test_v22_erasure_refusal_normalizes_exact_0039_to_0038_restoration() -> None:
    v21, policy, _canonical_v22, materializer = _assets()
    v22 = _built_v22(v21, policy, materializer)
    old = v21["erasure_execution_registry"]["rows"][0]
    refusal = v22["task8_fdd_contract"]["erasure_refusal_contract"]

    old_body = oracle.decode_sql_tuple(old, "current_body_sql_utf8_hex_tuple")
    old_acl = oracle.decode_sql_tuple(old, "current_acl_sql_utf8_hex_tuple")
    assert len(old_body) == 1
    expected_body = old_body[0].replace(
        "CREATE FUNCTION public.idis_task7_execute_approved_erasure(",
        "CREATE OR REPLACE FUNCTION public.idis_task7_execute_approved_erasure(",
        1,
    )
    expected_revoke = (
        "REVOKE SELECT (tenant_id,deal_id) ON TABLE "
        "public.methodology_truth_dashboard_rows FROM idis_erasure_function_owner"
    )
    assert oracle.decode_sql_tuple(refusal, "downgrade_sql_utf8_hex_tuple") == (
        expected_body,
        *old_acl,
        expected_revoke,
    )


def test_v22_installed_partition_inactive_boundary_and_task11_debt_are_literal() -> None:
    _v21, _policy, v22, _materializer = _assets()
    oracle.validate_task8_section(v22)

    assert v22["metadata"]["activation_status"] == "INACTIVE"
    assert v22["task8_fdd_contract"]["installed_partition"]["prior_tenant_rls_table_count"] == 67
    assert (
        v22["task8_fdd_contract"]["installed_partition"]["successor_tenant_rls_table_count"] == 69
    )
    obligation = v22["task8_fdd_contract"]["task11_exit_obligation"]
    assert obligation["restart_cut_count"] == 27
    assert tuple(obligation["undischarged_rule_tuple"]) == (oracle.TASK11_UNDISCHARGED_RULES)
    assert obligation["discharged_by_task8"] is False

    lock = oracle.load_json(oracle.CONTRACT_LOCK, "TASK8_CONTRACT_LOCK_MISSING")
    assert "contracts/full_strict_v21_reviewed_preimage_v1.json" in lock["files"]
    assert "contracts/full_strict_v22_reviewed_preimage_v1.json" not in lock["files"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("policy-hash", "TASK8_V22_POLICY_BINDING_INVALID"),
        ("selector", "TASK8_V22_SELECTOR_CONTRACT_INVALID"),
        ("dashboard", "TASK8_V22_DASHBOARD_TRUTH_TABLE_INVALID"),
        ("partial", "TASK8_V22_PARTIAL_EXACT_ONE_INVALID"),
        ("erasure", "TASK8_V22_ERASURE_REFUSAL_CODE_INVALID"),
        ("task11", "TASK8_V22_TASK11_OBLIGATION_INVALID"),
    ),
)
def test_v22_admission_rejects_corruption_in_every_new_semantic_family(
    mutation: str,
    code: str,
) -> None:
    _v21, _policy, v22, _materializer = _assets()
    corrupt = copy.deepcopy(v22)
    section = corrupt["task8_fdd_contract"]
    if mutation == "policy-hash":
        section["policy_binding"]["policy_sha256"] = "0" * 64
    elif mutation == "selector":
        section["selector_contracts"][0][4] = "legacy-schema"
    elif mutation == "dashboard":
        section["dashboard_contract"]["truth_table"][0][2] = "ONE_OR_MORE_DEFECTS"
    elif mutation == "partial":
        section["partial_exact_one_contract"]["complete_future_authority_preserved"] = False
    elif mutation == "erasure":
        section["erasure_refusal_contract"]["reason_code"] = "WEAKENED"
    else:
        section["task11_exit_obligation"]["discharged_by_task8"] = True

    with pytest.raises(oracle.V22OracleError, match=code):
        oracle.validate_task8_section(corrupt)
