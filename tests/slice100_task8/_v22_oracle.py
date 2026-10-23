"""Independent wished contract for Task-8.1 full-strict-v22 materialization.

The oracle deliberately imports no Task-8 materializer, generator, publisher,
runtime model, or migration.  It encodes only the successor-contract deltas
approved in the Task-8 design gate, with frozen v21 as the negative control.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
V21_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
V22_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
POLICY_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
POLICY_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v1_hash_v1.json"
)
V22_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v22_contract_hash_v1.json"
)
V22_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v22_preimage.py"
FIXTURE_GENERATOR = REPO_ROOT / "scripts" / "generate_task8_contract_fixtures.py"
GATE_PUBLISHER = REPO_ROOT / "scripts" / "publish_task8_contract_artifact_gate.py"
SQL_CENSUS_RUNNER = REPO_ROOT / "scripts" / "run_task8_v22_sql_authority_census.py"
CONTRACT_LOCK = REPO_ROOT / "contracts" / "contract_lock.json"

V21_FROZEN_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
V22_ARTIFACT_SCHEMA = "full-strict-v22-reviewed-preimage-v1"
V22_CONTRACT_VERSION = "full-strict-v22"
TASK8_SECTION_SCHEMA = "task8-fdd-successor-contract-v1"
POLICY_ID = "fdd-formula-policy-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

V21_TOP_LEVEL_KEYS = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
    "erasure_execution_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
    "activation_assertions",
)
V22_TOP_LEVEL_KEYS = (*V21_TOP_LEVEL_KEYS, "task8_fdd_contract")

# Sections whose literal bytes cannot change under the approved successor.
BYTE_IDENTICAL_SECTIONS = (
    "canonicalization_contract",
    "uuid_namespace_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_reference_authority_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
)

FDD_CALC_TYPES = (
    "GROSS_MARGIN",
    "FDD_EBITDA_BRIDGE",
    "FDD_EBIT_BRIDGE",
    "FDD_TAX_RECONCILIATION",
    "FDD_NET_INCOME_BRIDGE",
    "FDD_WORKING_CAPITAL_MOVEMENT",
    "FDD_FREE_CASH_FLOW_CASH_BRIDGE",
    "FDD_DEBT_ROLL_FORWARD",
    "FDD_ASSET_TIE_OUT",
    "FDD_LIABILITY_TIE_OUT",
)
FDD_SCHEMA_ROWS = (
    (
        "fdd-methodology-factual-output",
        "task8-fdd-methodology-factual-output-v1",
    ),
    (
        "fdd-deterministic-calculation",
        "task8-fdd-deterministic-calculation-v1",
    ),
)

EXACT_MODEL_FIELD_SETS: dict[str, tuple[str, ...]] = {
    "FddSignedRatioValueV1": ("type", "value", "as_of", "time_window"),
    "FddCalcInputsV1": (
        "schema_version",
        "formula_policy_id",
        "formula_policy_sha256",
        "formula_sha256",
        "coordinate",
        "bindings",
        "parent_calculations",
        "assumptions",
    ),
    "FddEconomicCoordinateV1": (
        "period_kind",
        "period_start",
        "period_end",
        "as_of_date",
        "base_currency",
        "monetary_scale_unit",
    ),
    "FddNumericBindingV1": (
        "binding_kind",
        "ordinal",
        "role",
        "source_decimal",
        "normalized_decimal",
        "sign_transform",
        "source_sheet",
        "source_row",
        "predicate",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "factual_output_record_id",
        "claim_id",
        "evidence_ids",
        "sanad_ids",
        "period",
        "currency",
        "unit",
        "value_sha256",
    ),
    "FddSetCompletenessBindingV1": (
        "binding_kind",
        "ordinal",
        "role",
        "confirmed",
        "source_sheet",
        "source_row",
        "predicate",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "factual_output_record_id",
        "claim_id",
        "evidence_ids",
        "sanad_ids",
        "period",
        "value_sha256",
    ),
    "FddParentCalculationV1": (
        "ordinal",
        "calc_type",
        "calc_id",
        "economic_coordinate_sha256",
        "formula_sha256",
        "reproducibility_sha256",
    ),
    "FddGovernedAssumptionV1": ("assumption_id", "value"),
    "FddCalcOutputV1": (
        "primary_name",
        "primary_decimal",
        "primary_scale",
        "secondary_values",
        "unit",
        "currency",
        "formula_disposition",
    ),
    "FddSecondaryOutputV1": ("name", "decimal", "scale", "unit", "currency"),
}

SELECTOR_CONTRACTS = (
    (
        0,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "FINANCIAL_DD",
        "METHODOLOGY_FACTUAL_OUTPUT",
        "fdd-methodology-factual-output",
        "task8-fdd-methodology-factual-output-v1",
    ),
    (
        1,
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "FINANCIAL_DD",
        "METHODOLOGY_CALCULATION",
        "fdd-deterministic-calculation",
        "task8-fdd-deterministic-calculation-v1",
    ),
    (
        2,
        "CALC",
        "GENERIC",
        "CALCULATION",
        "deterministic-calculation",
        "task7-deterministic-calculation-v1",
    ),
)

DASHBOARD_TRUTH_TABLE = (
    (
        0,
        "ALL_GOVERNED_NOT_FOUND",
        "NO_DEFECTS",
        "GAP",
        ("RESULT",),
        ("EVIDENCE", "SANAD", "CALCULATION", "ADVERSE_CONTRADICTION"),
    ),
    (
        1,
        "AT_LEAST_ONE_FOUND_REMAINDER_GOVERNED_FOUND_OR_NOT_FOUND",
        "NO_DEFECTS",
        "ANSWERED",
        ("RESULT", "EVIDENCE", "SANAD", "REQUIRED_CALCULATION"),
        ("ADVERSE_CONTRADICTION",),
    ),
    (
        2,
        "AT_LEAST_ONE_FOUND_REMAINDER_GOVERNED_FOUND_OR_NOT_FOUND",
        "ONE_OR_MORE_GOVERNED_DEFECTS",
        "RED_FLAG",
        (
            "RESULT",
            "EVIDENCE",
            "SANAD",
            "REQUIRED_CALCULATION",
            "ADVERSE_CONTRADICTION",
        ),
        (),
    ),
)

TASK11_UNDISCHARGED_RULES = (
    "E028_PROVIDER_OUTCOME_COMMON",
    "E029_PROVIDER_FACT_SEMANTIC",
    "E030_PROVIDER_FACT_DELIVERABLES",
    "E031_GRAPH_INTERNAL_DOWNSTREAM",
    "E032_RAG_INTERNAL_DOWNSTREAM",
    "E033_GRAPH_EXTERNAL_DOWNSTREAM",
    "E034_RAG_EXTERNAL_DOWNSTREAM",
    "E035_CONFLICT_INTERNAL_DOWNSTREAM",
    "E036_CONFLICT_EXTERNAL_DOWNSTREAM",
    "E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
)

TASK8_SQL_AUTHORITY_IDS = (
    "dashboard-root-equation-v2",
    "dashboard-partial-native-root-exact-one-v1",
)
ERASURE_REFUSAL_CODE = "TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED"
ERASURE_REFUSAL_SQLSTATE = "23514"


class V22OracleError(AssertionError):
    """Stable, private-data-free contract failure."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V22OracleError(code)


def load_json(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), f"{code}:{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V22OracleError(f"{code}_INVALID") from exc
    require(isinstance(value, dict), f"{code}_INVALID")
    return value


def load_v21() -> dict[str, Any]:
    raw = V21_ARTIFACT.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == V21_FROZEN_SHA256, "TASK8_V21_DRIFT")
    value = load_json(V21_ARTIFACT, "TASK8_V21_MISSING")
    require(tuple(value) == V21_TOP_LEVEL_KEYS, "TASK8_V21_TOP_LEVEL_DRIFT")
    require(value["metadata"]["append_frozen"] is True, "TASK8_V21_NOT_FROZEN")
    return value


def load_v22() -> dict[str, Any]:
    return load_json(V22_ARTIFACT, "TASK8_V22_ARTIFACT_NOT_IMPLEMENTED")


def load_policy() -> dict[str, Any]:
    return load_json(POLICY_ARTIFACT, "TASK8_FDD_POLICY_ARTIFACT_NOT_IMPLEMENTED")


def load_policy_fixture() -> dict[str, Any]:
    return load_json(POLICY_FIXTURE, "TASK8_FDD_POLICY_FIXTURE_NOT_IMPLEMENTED")


def load_v22_fixture() -> dict[str, Any]:
    return load_json(V22_FIXTURE, "TASK8_V22_FIXTURE_NOT_IMPLEMENTED")


def load_module(path: Path, name: str, code: str) -> ModuleType:
    require(path.is_file(), f"{code}:{path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"{code}_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def render(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def candidate_from_frozen(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result["metadata"]["append_frozen"] = False
    return result


def changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        result: set[tuple[str | int, ...]] = set()
        for key in old.keys() | new.keys():
            if key not in old or key not in new:
                result.add((*path, key))
            else:
                result.update(changed_paths(old[key], new[key], (*path, key)))
        return result
    if isinstance(old, list):
        result = set()
        for index in range(max(len(old), len(new))):
            if index >= len(old) or index >= len(new):
                result.add((*path, index))
            else:
                result.update(changed_paths(old[index], new[index], (*path, index)))
        return result
    return set() if old == new else {path}


def authority_rows(artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["authority_id"]): row for row in artifact["sql_authority_registry"]["rows"]}


def decode_sql_tuple(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    values = row[field]
    require(isinstance(values, list), "TASK8_V22_SQL_TUPLE_INVALID")
    try:
        return tuple(bytes.fromhex(str(value)).decode("utf-8") for value in values)
    except (ValueError, UnicodeError) as exc:
        raise V22OracleError("TASK8_V22_SQL_HEX_INVALID") from exc


def _validate_hash(value: object, code: str) -> None:
    require(isinstance(value, str) and SHA256_RE.fullmatch(value) is not None, code)


def validate_task8_section(v22: Mapping[str, Any]) -> None:
    section = v22["task8_fdd_contract"]
    require(
        tuple(section)
        == (
            "section_schema",
            "policy_binding",
            "fdd_schema_contracts",
            "selector_contracts",
            "dashboard_contract",
            "partial_exact_one_contract",
            "erasure_refusal_contract",
            "installed_partition",
            "task11_exit_obligation",
            "activation_assertions",
            "expected_sha256",
        ),
        "TASK8_V22_SECTION_SHAPE_INVALID",
    )
    require(section["section_schema"] == TASK8_SECTION_SCHEMA, "TASK8_V22_SECTION_INVALID")

    policy_raw = POLICY_ARTIFACT.read_bytes()
    policy = load_policy()
    policy_fixture = load_policy_fixture()
    binding = section["policy_binding"]
    require(
        binding
        == {
            "policy_id": POLICY_ID,
            "artifact_relative_path": "contracts/fdd_formula_policy_v1.json",
            "artifact_sha256": hashlib.sha256(policy_raw).hexdigest(),
            "policy_sha256": policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"],
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v1_hash_v1.json"
            ),
            "fixture_sha256": hashlib.sha256(POLICY_FIXTURE.read_bytes()).hexdigest(),
            "fixture_policy_sha256": policy_fixture["fdd_formula_policy_v1_sha256"],
        },
        "TASK8_V22_POLICY_BINDING_INVALID",
    )

    schema_contracts = section["fdd_schema_contracts"]
    require(len(schema_contracts) == 2, "TASK8_V22_SCHEMA_CONTRACT_COUNT_INVALID")
    require(
        tuple((row["schema_id"], row["schema_version"]) for row in schema_contracts)
        == FDD_SCHEMA_ROWS,
        "TASK8_V22_SCHEMA_CONTRACT_ID_INVALID",
    )
    observed_field_sets: dict[str, tuple[str, ...]] = {}
    for row in schema_contracts:
        require(row["normalized_schema_snapshot"], "TASK8_V22_SCHEMA_SNAPSHOT_EMPTY")
        require(row["legal_case_tuple"], "TASK8_V22_SCHEMA_LEGAL_CASES_EMPTY")
        require(row["illegal_case_tuple"], "TASK8_V22_SCHEMA_ILLEGAL_CASES_EMPTY")
        _validate_hash(row["expected_schema_sha256"], "TASK8_V22_SCHEMA_HASH_INVALID")
        for model_name, fields in row["model_field_set_tuple"]:
            observed_field_sets[str(model_name)] = tuple(fields)
    for model_name, expected_fields in EXACT_MODEL_FIELD_SETS.items():
        require(
            observed_field_sets.get(model_name) == expected_fields,
            f"TASK8_V22_MODEL_FIELD_SET_INVALID:{model_name}",
        )
    require(
        tuple(tuple(row) for row in section["selector_contracts"]) == SELECTOR_CONTRACTS,
        "TASK8_V22_SELECTOR_CONTRACT_INVALID",
    )

    dashboard = section["dashboard_contract"]
    require(
        dashboard["authority_id"] == "dashboard-root-equation-v2",
        "TASK8_V22_DASHBOARD_AUTHORITY_INVALID",
    )
    require(
        tuple(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                tuple(row[4]),
                tuple(row[5]),
            )
            for row in dashboard["truth_table"]
        )
        == DASHBOARD_TRUTH_TABLE,
        "TASK8_V22_DASHBOARD_TRUTH_TABLE_INVALID",
    )
    require(
        dashboard["coverage_row_count"] == 100 and dashboard["financial_row_count"] == 79,
        "TASK8_V22_DASHBOARD_CARDINALITY_INVALID",
    )
    require(
        dashboard["mixed_found_not_found_disposition_tuple"] == ["ANSWERED", "RED_FLAG"],
        "TASK8_V22_MIXED_RESULT_INVALID",
    )
    require(
        dashboard["required_formula_gap_bypass_allowed"] is False,
        "TASK8_V22_FORMULA_GAP_BYPASS_INVALID",
    )

    v21 = load_v21()
    current = next(
        row
        for row in v21["sql_authority_registry"]["rows"]
        if row["authority_id"] == "current-output-exact-one-v1"
    )
    current_statements = decode_sql_tuple(current, "body_sql_utf8_hex_tuple")
    expected_downgrade = [
        (
            b"DROP TRIGGER ctrg_task8_partial_native_root_dashboard_rows "
            b"ON public.methodology_truth_dashboard_rows"
        ).hex(),
        current_statements[2]
        .replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        .encode("utf-8")
        .hex(),
        current_statements[6]
        .replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        .encode("utf-8")
        .hex(),
    ]
    partial = section["partial_exact_one_contract"]
    require(
        partial
        == {
            "authority_id": "dashboard-partial-native-root-exact-one-v1",
            "monotonic_extension_table": "methodology_truth_dashboard_rows",
            "replaced_current_authority_id": "current-output-exact-one-v1",
            "replaced_current_helper_signature": (
                "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
            ),
            "replaced_current_function_signature": (
                "public.idis_task7_current_output_exact_one_v1()"
            ),
            "replaced_current_target_tuple": current["target_tuple"],
            "current_target_triggers_preserved_unchanged": True,
            "dashboard_native_identity_preimage": [
                "native-identity-v1",
                "tenant_id",
                "deal_id",
                "run_id",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                ["dashboard_row_id"],
            ],
            "dashboard_native_output_preimage": [
                "native-output-v1",
                "tenant_id",
                "deal_id",
                "run_id",
                "pipeline_contract_version",
                "pipeline_contract_hash",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                ["dashboard_row_id"],
                [
                    "coverage_record_id",
                    "root_projection_sha256",
                    "row_disposition",
                    "row_payload_sha256",
                ],
            ],
            "downgrade_sql_utf8_hex_tuple": expected_downgrade,
            "downgrade_restores_replaced_functions_unchanged": True,
            "complete_future_authority_id": "native-root-exact-one-v1",
            "complete_future_authority_preserved": True,
            "runtime_conditional_allowed": False,
        },
        "TASK8_V22_PARTIAL_EXACT_ONE_INVALID",
    )

    refusal = section["erasure_refusal_contract"]
    require(
        refusal["reason_code"] == ERASURE_REFUSAL_CODE
        and refusal["sqlstate"] == ERASURE_REFUSAL_SQLSTATE,
        "TASK8_V22_ERASURE_REFUSAL_CODE_INVALID",
    )
    require(
        refusal["checked_relation"] == "methodology_truth_dashboard_rows"
        and refusal["checked_columns"] == ["tenant_id", "deal_id"],
        "TASK8_V22_ERASURE_REFUSAL_SCOPE_INVALID",
    )
    require(
        refusal["owner_select_columns"] == ["tenant_id", "deal_id"]
        and refusal["owner_insert"] is False
        and refusal["owner_update"] is False
        and refusal["owner_delete"] is False
        and refusal["executor_table_privilege_count"] == 0
        and refusal["application_runner_execute_count"] == 0,
        "TASK8_V22_ERASURE_REFUSAL_ACL_INVALID",
    )
    require(
        refusal["advisory_lock_contract"] == "task7-tenant-deal-erasure-advisory-lock-v1"
        and refusal["zero_mutation_on_refusal"] is True,
        "TASK8_V22_ERASURE_REFUSAL_ATOMICITY_INVALID",
    )
    current_erasure = v21["erasure_execution_registry"]["rows"][0]
    current_erasure_body = decode_sql_tuple(current_erasure, "current_body_sql_utf8_hex_tuple")
    require(len(current_erasure_body) == 1, "TASK8_V22_ERASURE_DOWNGRADE_INVALID")
    expected_erasure_downgrade = [
        current_erasure_body[0]
        .replace(
            "CREATE FUNCTION public.idis_task7_execute_approved_erasure(",
            "CREATE OR REPLACE FUNCTION public.idis_task7_execute_approved_erasure(",
            1,
        )
        .encode("utf-8")
        .hex(),
        *current_erasure["current_acl_sql_utf8_hex_tuple"],
        (
            b"REVOKE SELECT (tenant_id,deal_id) ON TABLE "
            b"public.methodology_truth_dashboard_rows FROM idis_erasure_function_owner"
        ).hex(),
    ]
    require(
        refusal["downgrade_sql_utf8_hex_tuple"] == expected_erasure_downgrade,
        "TASK8_V22_ERASURE_DOWNGRADE_INVALID",
    )

    partition = section["installed_partition"]
    require(
        partition
        == {
            "prior_tenant_rls_table_count": 67,
            "successor_tenant_rls_table_count": 69,
            "added_table_tuple": [
                "methodology_truth_dashboard_rows",
                "methodology_truth_dashboard_root_links",
            ],
            "complete_erasure_current_relation_count": 48,
            "complete_erasure_future_relation_count": 38,
            "complete_erasure_relation_count": 86,
        },
        "TASK8_V22_INSTALLED_PARTITION_INVALID",
    )
    obligation = section["task11_exit_obligation"]
    require(
        obligation["restart_cut_count"] == 27
        and tuple(obligation["undischarged_rule_tuple"]) == TASK11_UNDISCHARGED_RULES
        and obligation["three_way_equality_required"] is True
        and obligation["discharged_by_task8"] is False,
        "TASK8_V22_TASK11_OBLIGATION_INVALID",
    )
    require(
        section["activation_assertions"]
        == {
            "activation_status": "INACTIVE",
            "runtime_selectable": False,
            "migration_consumable_before_second_review": False,
            "v21_runtime_lock_unchanged": True,
        },
        "TASK8_V22_ACTIVATION_INVALID",
    )
    expected_hash = canonical_sha256(
        [
            "full-strict-v22-task8-fdd-contract-v1",
            {key: value for key, value in section.items() if key != "expected_sha256"},
        ]
    )
    require(
        section["expected_sha256"] == expected_hash,
        "TASK8_V22_SECTION_HASH_INVALID",
    )


def validate_v22(v22: Mapping[str, Any]) -> None:
    require(tuple(v22) == V22_TOP_LEVEL_KEYS, "TASK8_V22_TOP_LEVEL_INVALID")
    metadata = v22["metadata"]
    require(metadata["artifact_schema"] == V22_ARTIFACT_SCHEMA, "TASK8_V22_SCHEMA_INVALID")
    require(
        metadata["pipeline_contract_version"] == V22_CONTRACT_VERSION,
        "TASK8_V22_VERSION_INVALID",
    )
    require(metadata["append_frozen"] is True, "TASK8_V22_NOT_FROZEN")
    require(metadata["activation_status"] == "INACTIVE", "TASK8_V22_NOT_INACTIVE")
    validate_task8_section(v22)


def assert_sequence_equal(actual: Sequence[Any], expected: Sequence[Any], code: str) -> None:
    require(tuple(actual) == tuple(expected), code)
