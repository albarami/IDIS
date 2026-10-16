"""Independent stdlib verification for the inactive full-strict-v25 fixture.

All authorities arrive as caller-supplied bytes.  This module owns its
canonicalization, requirement-contract model, dependency-graph derivation,
conservation receipt verification, component hashing, and dashboard identity
chain.  It never writes a contract or fixture path.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Never, cast

_PIPELINE_VERSION = "full-strict-v25"
_ARTIFACT_SCHEMA = "full-strict-v25-reviewed-preimage-v1"
_FIXTURE_SCHEMA = "full-strict-v25-contract-hash-fixture-v1"
_COMPONENT_DOMAIN = "full-strict-v25-component-v1"
_PIPELINE_DOMAIN = "full-strict-v25-pipeline-contract-v1"
_GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
_STRUCTURAL_SCHEMA = "task8-v25-structural-admission-v1"
_CONSERVATION_SCHEMA = "task8-v25-approved-semantics-conservation-v1"
_REQUIREMENT_SCHEMA = "task8-dashboard-requirement-binding-v3"
_REQUIREMENT_LITERAL_DOMAIN = "task8-v25-dashboard-policy-v2-requirement-literal-v1"
_POLICY_BINDING_DOMAIN = "task8-v25-policy-binding-v1"
_GRAPH_PRODUCER_DOMAIN = "task8-v25-graph-producer-authority-v1"
_GRAPH_EXTERNAL_DOMAIN = "task8-v25-graph-allowed-external-input-v1"

_PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_PARENT_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
_POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
_REVIEWER_MAP_PATH = "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_EXPECTED_ARTIFACT_SHA256 = "008dcf51b015e8191c93c607832b3e44d8e364f0b2902b05e6fc44bacebf86a1"
_EXPECTED_CONTRACT_SHA256 = "48cca9fa4a50a1a456493e36c696ddb888669fa023d25426bacc19b8fa0620ed"
_EXPECTED_GRAPH_SHA256 = "a8f27747ec5b13d0962f2202b21a37718f59c3a0e44a3f1672f6071ce2aa9bd0"
_EXPECTED_CONSERVATION_SHA256 = "a16b12a71640b40bc472deb23d6d4b3318ad22d35ce471c957099b8a9962dc60"
_EXPECTED_POLICY_BINDING_SHA256 = "8915fc53da0eda52e256ad8da6fcfbe614cc066eea57c60a85f69bfb3bd357a2"
_EXPECTED_FIXTURE_SHA256 = "3eab3b34412e9ba4f83584ce19c999c782ab9f85444c29e33379d22678a4ee0f"
_POLICY_VERSION_ID = (
    "financial_dd:a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352:"
    "fdd-formula-policy-v2:2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
)
_STABLE_DASHBOARD_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECIPE_OPERATOR_TOKENS = frozenset(
    {"CJ", "H", "ORDERED", "SCALAR", "SHA256", "TUPLE", "lowercase_hex"}
)
_PRODUCER_RESERVED_TOKENS = _RECIPE_OPERATOR_TOKENS | frozenset({"float8send", "hex"})
_ALLOWED_LEAF_AUTHORITIES = frozenset(
    {
        "BOUND_RUNTIME_INPUT",
        "CONTRACT_LITERAL",
        "FOREIGN_ROOT_INPUT",
        "PINNED_CONTRACT_INPUT",
        "SCALAR_OR_FOREIGN_INPUT",
        "VERSIONED_CONTRACT_INPUT",
    }
)

_EXPECTED_TOP_LEVEL_SECTIONS = (
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
    "task8_fdd_contract",
    "task8_structural_admission",
)
_EXPECTED_PARENT_PROVENANCE = {
    "parent_pipeline_contract_version": "full-strict-v21",
    "parent_artifact_sha256": _PARENT_ARTIFACT_SHA256,
    "parent_pipeline_contract_hash": _PARENT_CONTRACT_SHA256,
    "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
}
_APPROVED_DELTA_MANIFEST = (
    (1, "ADD_SEPARATE_FDD_DETERMINISTIC_CALCULATION_SCHEMA", "SEMANTIC_TASK8_APPROVED"),
    (2, "ADD_FDD_SIGNED_RATIO_FACTUAL_SCHEMA", "SEMANTIC_TASK8_APPROVED"),
    (3, "BIND_VERSION_AWARE_FDD_SELECTORS", "SEMANTIC_TASK8_APPROVED"),
    (4, "REPLACE_DASHBOARD_MIXED_RESULT_TRUTH_TABLE", "SEMANTIC_TASK8_APPROVED"),
    (5, "ADD_PARTIAL_NATIVE_ROOT_AND_ERASURE_REFUSAL", "SEMANTIC_TASK8_APPROVED"),
    (6, "BIND_EXACT_POLICY_ARTIFACT_OUTPUT_HASH_CONTRACTS", "SEMANTIC_TASK8_APPROVED"),
    (7, "ADD_ACYCLIC_DASHBOARD_IDENTITY_CONTRACT", "SECOND_GATE_SEMANTIC"),
    (8, "DERIVE_DASHBOARD_SQL_FROM_FROZEN_RECIPES", "MECHANICAL_CLOSURE"),
    (9, "ENFORCE_STRICT_PERIOD_ORDERING", "MECHANICAL_CORRECTION"),
    (10, "ENFORCE_POSITIVE_MAGNITUDE_NONNEGATIVE", "MECHANICAL_CORRECTION"),
    (11, "PRESERVE_NUMERIC_SOURCE_NORMALIZATION", "MECHANICAL_CORRECTION"),
    (12, "BIND_FDD_FORMULA_POLICY_V2", "POLICY_BINDING"),
    (
        13,
        "REPLACE_DASHBOARD_REQUIREMENT_BINDING_WITH_VERSION_DISCRIMINATED_V3",
        "SEMANTIC_V25",
    ),
)

_REQUIREMENT_TUPLE = (
    (1, "GROSS_MARGIN", "line_item"),
    (2, "FDD_EBITDA_BRIDGE", "p_and_l_control"),
    (3, "FDD_EBIT_BRIDGE", "p_and_l_control"),
    (4, "FDD_TAX_RECONCILIATION", "p_and_l_control"),
    (5, "FDD_NET_INCOME_BRIDGE", "p_and_l_control"),
    (6, "FDD_WORKING_CAPITAL_MOVEMENT", "cash_flow_control"),
    (7, "FDD_FREE_CASH_FLOW_CASH_BRIDGE", "cash_flow_control"),
    (8, "FDD_DEBT_ROLL_FORWARD", "debt_control"),
    (9, "FDD_ASSET_TIE_OUT", "sheet_control"),
    (10, "FDD_LIABILITY_TIE_OUT", "sheet_control"),
)
_POLICY_KEYS = ("formula_ordinal", "formula_type", "required", "scope")
_LEGACY_KEYS = ("calc_type", "required")
_NEGATIVE_CASES = (
    "LEGACY_FOUR_FIELD_REQUIREMENT",
    "POLICY_V2_TWO_FIELD_REQUIREMENT",
    "POLICY_V2_FORMULA_TYPE_UNKNOWN",
    "POLICY_V2_FORMULA_ORDINAL_DRIFT",
    "POLICY_V2_SCOPE_DRIFT",
    "UNDECLARED_FDD_CALCULATION_ROOT",
    "MIXED_REQUIREMENT_SHAPES",
    "CROSS_VERSION_METHODOLOGY_JOIN",
    "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
)

_DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
_DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
_OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
_DASHBOARD_OUTPUT_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_ROW_NAME_TUPLE = (
    "methodology-truth-dashboard-row-v2",
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "coverage_record_id",
    "methodology_version_record_id",
    "methodology_question_record_id",
    "question_payload_hash",
)
_PROJECTION_RECIPE = (
    'H(["methodology-truth-dashboard-root-projection-v2",tenant_id,deal_id,run_id,'
    "dashboard_row_id,coverage_record_id,row_disposition,count,ORDERED[(root_kind,"
    "root_ordinal,root_output_object_id,root_output_object_kind,dashboard_root_link_id)]])"
)
_ROW_PAYLOAD_RECIPE = (
    "financial-diligence-row-v2",
    "$identity_and_trace_without_self_output",
    "$disposition",
    "$disposition_reason_code",
    "$result_records",
    "$answer_values",
    "$task_gaps",
    "$calculations",
    "$root_projection_sha256",
)

_FROZEN_DIGEST_OPERATOR_PRODUCER_ROW = (
    "full_strict:#/digest_recipe_registry/rows/0",
    "full_strict",
    "CONTRACT_DEFINITION",
    "DIGEST_RECIPE_REGISTRY",
    "DIGEST_RECIPE",
    "H",
    "#/1",
)
_FROZEN_DIGEST_OPERATOR_VALUE = ("H", "lowercase_hex(SHA256(CJ(value)))")

_CONSERVATION_BASELINES = (
    (
        1,
        "STRICT_PERIOD_ORDERING",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
        ),
        "period_start<period_end AND as_of_date XOR (period_start,period_end)",
    ),
    (
        2,
        "POSITIVE_MAGNITUDE_NONNEGATIVE",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
        ),
        (
            "sign_transform='POSITIVE_MAGNITUDE' => decimal(source_decimal)>=0 AND "
            "decimal(normalized_decimal)>=0"
        ),
    ),
    (
        3,
        "SOURCE_NORMALIZATION_PRESERVED",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
        ),
        "normalized_decimal=source_decimal",
    ),
    (
        4,
        "ERASURE_0039_TO_0038_EXACT_RESTORATION",
        (
            "DERIVED_RESTORATION_TUPLE",
            "task8_fdd_contract",
            "erasure_refusal_contract",
            "downgrade_sql_utf8_hex_tuple",
        ),
        "EXACT_V21_ERASURE_BODY_PLUS_ACL_PLUS_DASHBOARD_REVOKE",
    ),
    (
        5,
        "DASHBOARD_100_OF_100_CENSUS_GUARD",
        ("SQL_AUTHORITY", "dashboard-root-equation-v3"),
        (
            "COVERAGE_CENSUS_NOT_100",
            "DASHBOARD_ROW_CENSUS_NOT_100",
            "COVERAGE_SET_MISSING",
            "COVERAGE_SET_EXTRA",
            "WITH dashboard_scopes AS",
            "incomplete_scopes AS",
            "TWO_LITERAL_100_COMPARISONS",
            "FOUR_SET_DIFFERENCE_COMPARISONS",
        ),
    ),
    (
        6,
        "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT",
        ("IDENTITY_CHAIN", "methodology-truth-dashboard-row-v2"),
        (
            "dashboard_row_id",
            "dashboard_root_link_ids",
            "root_projection_sha256",
            "row_payload_sha256",
            "native_identity_sha256_and_native_output_sha256",
            "output_object_id",
        ),
    ),
    (
        7,
        "FDD_FORMULA_POLICY_V2_BINDING",
        ("POLICY_BINDING", "task8_fdd_contract", "policy_binding"),
        (
            "fdd-formula-policy-v2",
            _POLICY_ARTIFACT_SHA256,
            _POLICY_SHA256,
            _REVIEWER_MAP_PATH,
            _REVIEWER_MAP_SHA256,
        ),
    ),
    (
        8,
        "DASHBOARD_REQUIREMENT_VERSION_DISCRIMINATION",
        ("SQL_AUTHORITY", "dashboard-root-equation-v3"),
        (
            "POLICY_V2_EXACT_FOUR_FIELD_TEN_LITERAL_TUPLES",
            "LEGACY_EXACT_TWO_FIELD_REQUIREMENTS",
            "FORMULA_TYPE_EQUALS_CALCULATION_COLUMN",
            "CROSS_VERSION_REJECTED",
        ),
    ),
)
_CONSERVATION_SELECTORS = (
    (
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "$defs/FddCalcInputsV1/x-idis-cross-field-constraint-tuple/2"
            ),
            "CANONICAL_JSON_SHA256",
        ),
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "targets/1/1/x-idis-cross-field-constraint-tuple/2"
            ),
            "CANONICAL_JSON_SHA256",
        ),
    ),
    (
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "$defs/FddNumericBindingV1/x-idis-cross-field-constraint-tuple/1"
            ),
            "CANONICAL_JSON_SHA256",
        ),
    ),
    (
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "$defs/FddNumericBindingV1/x-idis-cross-field-constraint-tuple/0"
            ),
            "CANONICAL_JSON_SHA256",
        ),
    ),
    (
        (
            "full_strict",
            "#/task8_fdd_contract/erasure_refusal_contract",
            "CANONICAL_JSON_SHA256",
        ),
    ),
    (("full_strict", "#/sql_authority_registry/rows/0", "CANONICAL_JSON_SHA256"),),
    (
        ("full_strict", "#/uuid_namespace_registry/rows/18", "CANONICAL_JSON_SHA256"),
        ("full_strict", "#/uuid_namespace_registry/rows/15", "CANONICAL_JSON_SHA256"),
        ("full_strict", "#/uuid_namespace_registry/rows/4", "CANONICAL_JSON_SHA256"),
        ("full_strict", "#/digest_recipe_registry/rows/8", "CANONICAL_JSON_SHA256"),
        (
            "full_strict",
            "#/task8_fdd_contract/partial_exact_one_contract",
            "CANONICAL_JSON_SHA256",
        ),
        (
            "full_strict",
            "#/task8_structural_admission/identity_hash_dependency_contract",
            "CANONICAL_JSON_SHA256",
        ),
        (
            "full_strict",
            "#/task8_structural_admission/dashboard_sql_recipe_contract",
            "CANONICAL_JSON_SHA256",
        ),
        (
            "fdd_formula_policy",
            "#/financial_output_schema/dashboard_row_uuid_authority",
            "CANONICAL_JSON_SHA256",
        ),
        (
            "fdd_formula_policy",
            "#/financial_output_schema/row_payload_preimage",
            "CANONICAL_JSON_SHA256",
        ),
    ),
    (
        ("full_strict", "#/task8_fdd_contract/policy_binding", "CANONICAL_JSON_SHA256"),
        ("fdd_formula_policy", "#", "CANONICAL_JSON_SHA256"),
        ("reviewer_map", _REVIEWER_MAP_PATH, "RAW_FILE_SHA256"),
    ),
    (
        ("full_strict", "#/sql_authority_registry/rows/0", "CANONICAL_JSON_SHA256"),
        (
            "full_strict",
            "#/task8_fdd_contract/dashboard_contract/requirement_binding_contract",
            "CANONICAL_JSON_SHA256",
        ),
    ),
)


class IndependentVerificationError(RuntimeError):
    """Fail-closed error containing only a stable contract code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise IndependentVerificationError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def _normalize(value: object) -> object:
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("TASK8_V25_INDEPENDENT_BINARY64_NONFINITE")
        return 0 if value == 0.0 else value
    if isinstance(value, str):
        _require(
            unicodedata.normalize("NFC", value) == value,
            "TASK8_V25_INDEPENDENT_UNICODE_NOT_NFC",
        )
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            _require(isinstance(key, str), "TASK8_V25_INDEPENDENT_NON_STRING_KEY")
            result[key] = _normalize(item)
        return result
    _fail("TASK8_V25_INDEPENDENT_VALUE_TYPE_INVALID")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _decode(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(code) from exc
    _require(isinstance(value, dict), code)
    return cast(dict[str, Any], value)


def _without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(item) for key, item in value.items() if key != "expected_sha256"}


def _validate_parent(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _PARENT_ARTIFACT_SHA256:
        _fail("TASK8_V25_INDEPENDENT_PARENT_FROZEN_IDENTITY_MISMATCH")
    parent = _decode(raw, "TASK8_V25_INDEPENDENT_PARENT_FROZEN_IDENTITY_MISMATCH")
    metadata = parent.get("metadata")
    _require(
        _render(parent) == raw
        and isinstance(metadata, Mapping)
        and metadata.get("artifact_schema") == "full-strict-v21-reviewed-preimage-v1"
        and metadata.get("pipeline_contract_version") == "full-strict-v21"
        and metadata.get("append_frozen") is True,
        "TASK8_V25_INDEPENDENT_PARENT_FROZEN_IDENTITY_MISMATCH",
    )
    return parent


def _validate_policy(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _POLICY_ARTIFACT_SHA256:
        _fail("TASK8_V25_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH")
    policy = _decode(raw, "TASK8_V25_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH")
    digest = policy.get("digest_recipe_registry")
    transition = policy.get("methodology_transition")
    rows = transition.get("successor_required_map") if isinstance(transition, Mapping) else None
    observed_rows = []
    if isinstance(rows, list):
        try:
            observed_rows = [
                [row["formula_ordinal"], row["formula_type"], row["scope"], row["required"]]
                for row in rows
            ]
        except (KeyError, TypeError):
            observed_rows = []
    _require(
        _render(policy) == raw
        and isinstance(digest, Mapping)
        and digest.get("fdd_formula_policy_v2_sha256") == _POLICY_SHA256
        and policy.get("metadata", {}).get("policy_id") == "fdd-formula-policy-v2"
        and observed_rows == [[*row, True] for row in _REQUIREMENT_TUPLE],
        "TASK8_V25_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH",
    )
    return policy


def _validate_reviewer_map(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    _require(
        digest == _REVIEWER_MAP_SHA256,
        "TASK8_V25_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH",
    )
    return digest


def _artifact_policy_binding(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    task8 = artifact.get("task8_fdd_contract")
    binding = task8.get("policy_binding") if isinstance(task8, Mapping) else None
    expected_values = [
        "fdd-formula-policy-v2",
        _POLICY_ARTIFACT_SHA256,
        _POLICY_SHA256,
        _REVIEWER_MAP_PATH,
        _REVIEWER_MAP_SHA256,
    ]
    expected = {
        "policy_id": "fdd-formula-policy-v2",
        "artifact_relative_path": "contracts/fdd_formula_policy_v2.json",
        "artifact_sha256": _POLICY_ARTIFACT_SHA256,
        "policy_sha256": _POLICY_SHA256,
        "fixture_relative_path": "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json",
        "fixture_sha256": _POLICY_FIXTURE_SHA256,
        "fixture_policy_sha256": _POLICY_SHA256,
        "reviewer_map_relative_path": _REVIEWER_MAP_PATH,
        "reviewer_map_sha256": _REVIEWER_MAP_SHA256,
        "binding_sha256": _canonical_sha256([_POLICY_BINDING_DOMAIN, expected_values]),
    }
    _require(
        isinstance(binding, Mapping)
        and dict(binding) == expected
        and binding.get("binding_sha256") == _EXPECTED_POLICY_BINDING_SHA256,
        "TASK8_V25_INDEPENDENT_POLICY_BINDING_INVALID",
    )
    return cast(Mapping[str, Any], binding)


def _expected_requirement_contract() -> dict[str, Any]:
    rows = [list(row) for row in _REQUIREMENT_TUPLE]
    return {
        "schema_version": _REQUIREMENT_SCHEMA,
        "discriminator_field": "methodology_version_id",
        "policy_v2_methodology_version_id": _POLICY_VERSION_ID,
        "policy_id": "fdd-formula-policy-v2",
        "policy_sha256": _POLICY_SHA256,
        "policy_v2_requirement_key_tuple": list(_POLICY_KEYS),
        "legacy_requirement_key_tuple": list(_LEGACY_KEYS),
        "policy_v2_requirement_literal_tuple": rows,
        "policy_v2_requirement_literal_sha256": _canonical_sha256(
            [_REQUIREMENT_LITERAL_DOMAIN, _POLICY_SHA256, rows]
        ),
        "calculation_column": "deterministic_calculations.calc_type",
        "policy_v2_match_field": "formula_type",
        "legacy_match_field": "calc_type",
        "stable_rejection_code": _STABLE_DASHBOARD_CODE,
        "negative_case_tuple": list(_NEGATIVE_CASES),
        "normative_source_mode": "HASH_BOUND_ARTIFACT_LITERAL_ONLY",
        "runtime_policy_read_allowed": False,
        "field_alias_allowed": False,
        "adapter_allowed": False,
        "coercion_allowed": False,
    }


def _sql_literal(value: str) -> str:
    _require(
        bool(value) and "'" not in value and "\\" not in value,
        "TASK8_V25_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID",
    )
    return f"'{value}'"


def _render_requirement_guard(contract: Mapping[str, Any]) -> str:
    _require(
        dict(contract) == _expected_requirement_contract(),
        "TASK8_V25_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID",
    )
    values = ",".join(
        f"({ordinal},{_sql_literal(formula_type)},{_sql_literal(scope)})"
        for ordinal, formula_type, scope in _REQUIREMENT_TUPLE
    )
    version = _sql_literal(_POLICY_VERSION_ID)
    return (
        "IF dashboard_row.methodology_version_id IS DISTINCT FROM "
        "dashboard_row.coverage_methodology_version_id OR jsonb_typeof("
        "dashboard_row.question_payload->'required_calculations') IS DISTINCT FROM 'array' "
        "OR EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "jsonb_typeof(item) IS DISTINCT FROM 'object' OR ("
        f"dashboard_row.methodology_version_id={version} AND ("
        "CASE WHEN jsonb_typeof(item)='object' THEN (SELECT count(*) FROM "
        "jsonb_object_keys(item)) ELSE -1 END<>4 OR NOT (item ?& "
        "ARRAY['formula_ordinal','formula_type','required','scope']) OR "
        "jsonb_typeof(item->'formula_ordinal') IS DISTINCT FROM 'number' OR "
        "jsonb_typeof(item->'formula_type') IS DISTINCT FROM 'string' OR "
        "jsonb_typeof(item->'required') IS DISTINCT FROM 'boolean' OR "
        "jsonb_typeof(item->'scope') IS DISTINCT FROM 'string' OR NOT EXISTS "
        f"(SELECT 1 FROM (VALUES {values}) AS approved(formula_ordinal,formula_type,scope) "
        "WHERE item->>'formula_ordinal'=approved.formula_ordinal::text AND "
        "item->>'formula_type'=approved.formula_type AND item->>'scope'=approved.scope))) "
        f"OR (dashboard_row.methodology_version_id<>{version} AND ("
        "CASE WHEN jsonb_typeof(item)='object' THEN (SELECT count(*) FROM "
        "jsonb_object_keys(item)) ELSE -1 END<>2 OR NOT (item ?& "
        "ARRAY['calc_type','required']) OR jsonb_typeof(item->'calc_type') IS DISTINCT "
        "FROM 'string' OR item->>'calc_type'='' OR jsonb_typeof(item->'required') IS "
        "DISTINCT FROM 'boolean'))) OR ("
        f"dashboard_row.methodology_version_id={version} AND EXISTS ("
        "SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item GROUP BY "
        "item->>'formula_ordinal',item->>'formula_type',item->>'scope' HAVING "
        "count(*)<>1)) THEN RAISE EXCEPTION "
        f"'{_STABLE_DASHBOARD_CODE}' USING ERRCODE='23514'; END IF;"
    )


def _dashboard_body(artifact: Mapping[str, Any]) -> str:
    registry = artifact.get("sql_authority_registry")
    rows = registry.get("rows") if isinstance(registry, Mapping) else None
    matches = [
        row
        for row in rows or []
        if isinstance(row, Mapping) and row.get("authority_id") == "dashboard-root-equation-v3"
    ]
    _require(len(matches) == 1, "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH")
    try:
        statements = [
            bytes.fromhex(str(value)).decode("utf-8")
            for value in matches[0]["body_sql_utf8_hex_tuple"]
        ]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH"
        ) from exc
    functions = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    _require(len(functions) == 1, "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH")
    body = functions[0].partition(" AS $$")[2].rsplit("$$", 1)[0]
    _require(bool(body), "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH")
    return body


def _validate_requirement_contract_and_sql(artifact: Mapping[str, Any]) -> None:
    try:
        contract = artifact["task8_fdd_contract"]["dashboard_contract"][
            "requirement_binding_contract"
        ]
    except (KeyError, TypeError) as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID"
        ) from exc
    _require(
        isinstance(contract, Mapping) and dict(contract) == _expected_requirement_contract(),
        "TASK8_V25_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID",
    )
    guard = _render_requirement_guard(contract)
    _require(
        hashlib.sha256(guard.encode("utf-8")).hexdigest()
        == "9daf2e4a062ce56bc4f3d6a30746b3da99b6581e6c2b251e694703db1e38c5d1",
        "TASK8_V25_INDEPENDENT_REQUIREMENT_CONTRACT_INVALID",
    )
    body = _dashboard_body(artifact)
    _require(body.count(guard) == 1, "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH")
    try:
        guard_precedes_preimage = body.index(guard) < body.index("preimage:=")
    except ValueError as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH"
        ) from exc
    checks = (
        guard_precedes_preimage,
        body.count("item->>'formula_type'=c.calc_type") == 3,
        body.count("item->>'calc_type'=c.calc_type") == 3,
        body.count("c.calc_type") == 6,
        body.count("q.methodology_version_id=c.methodology_version_id") == 2,
        "COALESCE" not in body,
        "fdd_formula_policy_v2.json" not in body,
        "formula_type')::" not in body,
        "calc_type')::" not in body,
    )
    _require(all(checks), "TASK8_V25_INDEPENDENT_REQUIREMENT_SQL_MISMATCH")


def validate_requirement_vector(
    *,
    methodology_version_id: str,
    joined_methodology_version_id: str,
    required_calculations: object,
    calculation_types: Sequence[str],
) -> None:
    """Executable independent reading of the permanent requirement matrix."""

    if methodology_version_id != joined_methodology_version_id or not isinstance(
        required_calculations, list
    ):
        _fail(_STABLE_DASHBOARD_CODE)
    required_types: list[str] = []
    policy_v2 = methodology_version_id == _POLICY_VERSION_ID
    approved = set(_REQUIREMENT_TUPLE)
    observed: set[tuple[object, object, object]] = set()
    for value in required_calculations:
        if not isinstance(value, dict):
            _fail(_STABLE_DASHBOARD_CODE)
        if policy_v2:
            if tuple(value) != _POLICY_KEYS and set(value) != set(_POLICY_KEYS):
                _fail(_STABLE_DASHBOARD_CODE)
            row = (value.get("formula_ordinal"), value.get("formula_type"), value.get("scope"))
            if (
                type(value.get("formula_ordinal")) is not int
                or type(value.get("formula_type")) is not str
                or type(value.get("required")) is not bool
                or type(value.get("scope")) is not str
                or row not in approved
                or row in observed
            ):
                _fail(_STABLE_DASHBOARD_CODE)
            observed.add(row)
            if value["required"]:
                required_types.append(str(value["formula_type"]))
        else:
            if set(value) != set(_LEGACY_KEYS):
                _fail(_STABLE_DASHBOARD_CODE)
            if (
                type(value.get("calc_type")) is not str
                or not str(value["calc_type"]).strip()
                or type(value.get("required")) is not bool
            ):
                _fail(_STABLE_DASHBOARD_CODE)
            if value["required"]:
                required_types.append(str(value["calc_type"]))
    if Counter(calculation_types) != Counter(required_types):
        _fail(_STABLE_DASHBOARD_CODE)


def _json_pointer(parts: Sequence[str | int]) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "#/" + "/".join(encoded)


def _walk_preimages(
    value: object,
    *,
    artifact_name: str,
    path: tuple[str | int, ...] = (),
) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, key)
            lowered = key.lower()
            if "preimage" in lowered or "uuid_name_tuple" in lowered:
                result.add(f"{artifact_name}:{_json_pointer(child_path)}")
            result.update(_walk_preimages(child, artifact_name=artifact_name, path=child_path))
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            result.update(
                _walk_preimages(
                    child,
                    artifact_name=artifact_name,
                    path=(*path, ordinal),
                )
            )
    return result


def _discover_source_pointers(
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    pointers = _walk_preimages(full_strict, artifact_name="full_strict")
    pointers.update(_walk_preimages(policy, artifact_name="fdd_formula_policy"))
    for section_name, width in (
        ("uuid_namespace_registry", 3),
        ("digest_recipe_registry", 2),
    ):
        section = full_strict.get(section_name)
        rows = section.get("rows") if isinstance(section, Mapping) else None
        _require(isinstance(rows, list), "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID")
        for ordinal, row in enumerate(cast(list[Any], rows)):
            _require(
                isinstance(row, list) and len(row) == width,
                "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
            )
            pointers.add(f"full_strict:{_json_pointer((section_name, 'rows', ordinal))}")
    return tuple(sorted(pointers))


def _pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    try:
        artifact_name, encoded = pointer.split(":#/", 1)
        _require(
            artifact_name in {"full_strict", "fdd_formula_policy"},
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        value: object = full_strict if artifact_name == "full_strict" else policy
        for raw in encoded.split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
        return value
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndependentVerificationError("TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID") from exc


def _relative_payload(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    _require(
        pointer.startswith("#/") and len(pointer) > 2,
        "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )
    current = value
    try:
        for raw in pointer[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID"
        ) from exc
    return current


def _producer_authority_row(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[str]:
    """Classify one governed source solely from its closed source shape."""

    value = _pointer_value(pointer, full_strict, policy)
    if pointer.startswith("full_strict:#/digest_recipe_registry/rows/"):
        _require(
            isinstance(value, list)
            and len(value) == 2
            and isinstance(value[0], str)
            and isinstance(value[1], str),
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        value = cast(list[Any], value)
        symbol = (
            "root_projection_sha256"
            if value[0] == "methodology_truth_dashboard_root_projection_sha256"
            else value[0]
        )
        if symbol == "root_projection_sha256":
            return [
                pointer,
                "full_strict",
                "RUNTIME_VALUE",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                symbol,
                "#/1",
            ]
        return [
            pointer,
            "full_strict",
            "CONTRACT_DEFINITION",
            "DIGEST_RECIPE_REGISTRY",
            "DIGEST_RECIPE",
            symbol,
            "#/1",
        ]
    if pointer.startswith("full_strict:#/uuid_namespace_registry/rows/"):
        _require(
            isinstance(value, list)
            and len(value) == 3
            and isinstance(value[0], str)
            and isinstance(value[1], str)
            and isinstance(value[2], list),
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        value = cast(list[Any], value)
        symbol = value[0]
        dashboard = symbol in {"output_object_id", "dashboard_root_link_id", "dashboard_row_id"}
        return [
            pointer,
            "full_strict",
            "RUNTIME_VALUE",
            "METHODOLOGY_TRUTH_DASHBOARD_ROW" if dashboard else f"UUID::{symbol}",
            "METHODOLOGY_TRUTH_DASHBOARD_ROW" if dashboard else symbol.upper(),
            symbol,
            "#/2",
        ]
    if pointer.startswith("full_strict:#/applicability_registry/rows/") and pointer.endswith(
        "/hash_preimage"
    ):
        row_pointer = pointer[: -len("/hash_preimage")]
        parent = _pointer_value(row_pointer, full_strict, policy)
        _require(
            isinstance(parent, Mapping) and isinstance(parent.get("predicate_id"), str),
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        parent = cast(Mapping[str, Any], parent)
        predicate = cast(str, parent["predicate_id"])
        return [
            pointer,
            "full_strict",
            "CONTRACT_DEFINITION",
            predicate,
            "APPLICABILITY_HASH",
            f"{predicate.lower()}_sha256",
            "#",
        ]
    future_registry: tuple[str, str] | None = None
    if "/future_child_authority_registry/" in pointer and pointer.endswith(
        "/uuid_name_tuple_schema"
    ):
        future_registry = ("future_child_authority_registry", "FUTURE_CHILD_UUID_AUTHORITY")
    elif "/future_reference_authority_registry/" in pointer and pointer.endswith(
        "/uuid_name_tuple_schema"
    ):
        future_registry = (
            "future_reference_authority_registry",
            "FUTURE_REFERENCE_UUID_AUTHORITY",
        )
    if future_registry is not None:
        registry_name, output_kind = future_registry
        row_pointer = pointer[: -len("/uuid_name_tuple_schema")]
        parent = _pointer_value(row_pointer, full_strict, policy)
        _require(
            isinstance(parent, Mapping) and isinstance(parent.get("table_name"), str),
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        parent = cast(Mapping[str, Any], parent)
        table_name = cast(str, parent["table_name"])
        _require(
            f"/{registry_name}/" in pointer,
            "TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID",
        )
        return [
            pointer,
            "full_strict",
            "CONTRACT_DEFINITION",
            table_name,
            output_kind,
            f"{table_name}_uuid",
            "#",
        ]
    policy_rows = {
        "fdd_formula_policy:#/digest_recipe_registry/policy_hash_preimage": (
            "CONTRACT_DEFINITION",
            "/digest_recipe_registry/policy_hash_preimage",
            "CONTRACT_DECLARATION",
            "policy_hash_preimage",
        ),
        "fdd_formula_policy:#/financial_output_schema/calculation_set_preimage_tuple": (
            "CONTRACT_DEFINITION",
            "/financial_output_schema/calculation_set_preimage_tuple",
            "CONTRACT_DECLARATION",
            "calculation_set_preimage_tuple",
        ),
        "fdd_formula_policy:#/financial_output_schema/dashboard_row_uuid_authority/name_preimage": (
            "CONTRACT_DEFINITION",
            "FDD_POLICY_DASHBOARD_ROW_AUTHORITY",
            "POLICY_UUID_AUTHORITY",
            "policy_dashboard_row_id_authority",
        ),
        "fdd_formula_policy:#/financial_output_schema/row_payload_preimage": (
            "RUNTIME_VALUE",
            "METHODOLOGY_TRUTH_DASHBOARD_ROW",
            "METHODOLOGY_TRUTH_DASHBOARD_ROW",
            "row_payload_sha256",
        ),
        "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple": (
            "CONTRACT_DEFINITION",
            "/financial_output_schema/row_set_preimage_tuple",
            "CONTRACT_DECLARATION",
            "row_set_preimage_tuple",
        ),
        "fdd_formula_policy:#/formula_registry/formula_hash_preimage_field_tuple": (
            "CONTRACT_DEFINITION",
            "/formula_registry/formula_hash_preimage_field_tuple",
            "CONTRACT_DECLARATION",
            "formula_hash_preimage_field_tuple",
        ),
        "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage": (
            "CONTRACT_DEFINITION",
            "/methodology_transition/successor_version_id_preimage",
            "CONTRACT_DECLARATION",
            "successor_version_id_preimage",
        ),
    }
    if pointer in policy_rows:
        layer, scope, output_kind, symbol = policy_rows[pointer]
        return [pointer, "fdd_formula_policy", layer, scope, output_kind, symbol, "#"]
    partial_rows = {
        "dashboard_native_identity_preimage": "native_identity_sha256",
        "dashboard_native_output_preimage": "native_output_sha256",
    }
    for suffix, symbol in partial_rows.items():
        if pointer.endswith(f"/{suffix}"):
            return [
                pointer,
                "full_strict",
                "RUNTIME_VALUE",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                "METHODOLOGY_TRUTH_DASHBOARD_ROW",
                symbol,
                "#",
            ]
    _fail("TASK8_V25_INDEPENDENT_GRAPH_SOURCE_INVALID")


def _validate_producer_symbols(
    producer_rows: Sequence[Sequence[str]],
    *,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    """Apply the ASCII symbol closure and the single frozen H declaration exception."""

    frozen_rows = [
        row for row in producer_rows if row[0] == _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW[0]
    ]
    frozen_value = _pointer_value(
        _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW[0],
        full_strict,
        policy,
    )
    _require(
        len(frozen_rows) == 1
        and tuple(frozen_rows[0]) == _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW
        and frozen_value == list(_FROZEN_DIGEST_OPERATOR_VALUE),
        "TASK8_V25_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
    )
    for row in producer_rows:
        _require(len(row) == 7, "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID")
        if tuple(row) == _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW:
            continue
        symbol = row[5]
        _require(
            symbol not in _PRODUCER_RESERVED_TOKENS and _IDENTIFIER.fullmatch(symbol) is not None,
            "TASK8_V25_INDEPENDENT_PRODUCER_SYMBOL_INVALID",
        )


def _derive_external_input_rows(
    *,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
    producer_rows: Sequence[Sequence[str]],
) -> list[list[str]]:
    producer_symbols = {row[5] for row in producer_rows}
    rows: set[tuple[str, str, str, str]] = set()
    for row in producer_rows:
        pointer = row[0]
        value = _pointer_value(pointer, full_strict, policy)
        payload = _relative_payload(value, row[6])
        _validate_recipe_grammar(row[6], payload)
        for token_path, scalar in _flatten(payload):
            if not isinstance(scalar, str):
                continue
            for identifier in _recipe_identifiers(scalar):
                if identifier not in producer_symbols:
                    rows.add((pointer, token_path, identifier, _leaf_authority(identifier)))
    return sorted((list(row) for row in rows), key=_canonical_json)


def _flatten(value: object, path: str = "$") -> list[tuple[str, object]]:
    if isinstance(value, Mapping):
        return [item for key in sorted(value) for item in _flatten(value[key], f"{path}.{key}")]
    if isinstance(value, list):
        return [
            item
            for ordinal, child in enumerate(value)
            for item in _flatten(child, f"{path}[{ordinal}]")
        ]
    return [(path, value)]


def _node_id(node: Sequence[object]) -> str:
    _require(len(node) == 6, "TASK8_V25_INDEPENDENT_GRAPH_NODE_INVALID")
    return _canonical_json(list(node[:5])).decode("utf-8")


def _recipe_identifiers(value: object) -> set[str]:
    if not isinstance(value, str):
        return {str(value)}
    return set(_IDENTIFIER.findall(value)) - _RECIPE_OPERATOR_TOKENS


class _RecipeParser:
    """Closed and full-consumption parser for the governed recipe language."""

    def __init__(self, expression: str) -> None:
        self.expression = expression
        self.offset = 0

    def parse(self) -> None:
        self._expression()
        self._whitespace()
        self._expect(self.offset == len(self.expression))

    def _expect(self, condition: bool) -> None:
        _require(condition, "TASK8_V25_INDEPENDENT_RECIPE_GRAMMAR_INVALID")

    def _whitespace(self) -> None:
        while self.offset < len(self.expression) and self.expression[self.offset] in " \t\r\n":
            self.offset += 1

    def _peek(self) -> str:
        self._whitespace()
        return self.expression[self.offset] if self.offset < len(self.expression) else ""

    def _literal(self, expected: str) -> None:
        self._whitespace()
        self._expect(self.expression.startswith(expected, self.offset))
        self.offset += len(expected)

    def _identifier(self) -> str:
        self._whitespace()
        match = _IDENTIFIER.match(self.expression, self.offset)
        self._expect(match is not None)
        if match is None:
            _fail("TASK8_V25_INDEPENDENT_RECIPE_GRAMMAR_INVALID")
        self.offset = match.end()
        return match.group(0)

    def _json_string(self) -> None:
        self._whitespace()
        try:
            value, consumed = json.JSONDecoder().raw_decode(self.expression[self.offset :])
        except json.JSONDecodeError as exc:
            raise IndependentVerificationError(
                "TASK8_V25_INDEPENDENT_RECIPE_GRAMMAR_INVALID"
            ) from exc
        self._expect(isinstance(value, str) and consumed > 0)
        self.offset += consumed

    def _sequence(self, closing: str, *, empty_allowed: bool) -> None:
        if self._peek() == closing:
            self._expect(empty_allowed)
            self._literal(closing)
            return
        self._expression()
        while self._peek() == ",":
            self._literal(",")
            self._expect(self._peek() != closing)
            self._expression()
        self._literal(closing)

    def _unary_call(self) -> None:
        self._literal("(")
        self._expect(self._peek() != ")")
        self._expression()
        self._literal(")")

    def _ordered(self) -> None:
        self._literal("[")
        self._expect(self._peek() != "]")
        self._expression()
        self._literal("]")

    def _lowercase_hex_chain(self) -> None:
        self._literal("(")
        self._expect(self._identifier() == "SHA256")
        self._literal("(")
        self._expect(self._identifier() == "CJ")
        self._literal("(")
        self._expect(self._peek() != ")")
        self._expression()
        self._literal(")")
        self._literal(")")
        self._literal(")")

    def _binary64_terminal(self) -> None:
        self._literal("(")
        self._expect(self._identifier() == "float8send")
        self._literal("(")
        argument = self._identifier()
        self._expect(
            argument not in _RECIPE_OPERATOR_TOKENS and argument not in {"hex", "float8send"}
        )
        self._expect(self._peek() == ")")
        self._literal(")")
        self._literal(")")

    def _expression(self) -> None:
        token = self._peek()
        self._expect(bool(token))
        if token == '"':
            self._json_string()
            return
        if token == "[":
            self._literal("[")
            self._sequence("]", empty_allowed=True)
            return
        if token == "(":
            self._literal("(")
            self._sequence(")", empty_allowed=False)
            return
        name = self._identifier()
        next_token = self._peek()
        if name in {"H", "CJ", "SCALAR"}:
            self._expect(next_token == "(")
            self._unary_call()
            return
        if name == "TUPLE":
            self._expect(next_token == "(")
            self._literal("(")
            self._sequence(")", empty_allowed=False)
            return
        if name == "ORDERED":
            self._expect(next_token == "[")
            self._ordered()
            return
        if name == "lowercase_hex":
            self._expect(next_token == "(")
            self._lowercase_hex_chain()
            return
        if name == "hex":
            self._expect(next_token == "(")
            self._binary64_terminal()
            return
        self._expect(name not in {"SHA256", "float8send"})
        self._expect(next_token not in {"(", "["})


def _validate_recipe_grammar(payload_pointer: str, value: object) -> None:
    if payload_pointer != "#/1":
        return
    _require(isinstance(value, str), "TASK8_V25_INDEPENDENT_RECIPE_GRAMMAR_INVALID")
    _RecipeParser(cast(str, value)).parse()


def _cycle(adjacency: Mapping[str, set[str]]) -> tuple[str, ...] | None:
    complete: set[str] = set()
    active: list[str] = []
    active_index: dict[str, int] = {}

    def visit(node: str) -> tuple[str, ...] | None:
        if node in active_index:
            return (*active[active_index[node] :], node)
        if node in complete:
            return None
        active_index[node] = len(active)
        active.append(node)
        for dependency in sorted(adjacency.get(node, set())):
            found = visit(dependency)
            if found is not None:
                return found
        active.pop()
        active_index.pop(node)
        complete.add(node)
        return None

    for node in sorted(adjacency):
        found = visit(node)
        if found is not None:
            return found
    return None


def _leaf_authority(token: object) -> str:
    rendered = str(token)
    if rendered == "pipeline_contract_hash":
        return "PINNED_CONTRACT_INPUT"
    if rendered == "pipeline_contract_version":
        return "VERSIONED_CONTRACT_INPUT"
    if rendered in {"root_output_object_id", "root_output_object_kind"}:
        return "FOREIGN_ROOT_INPUT"
    if rendered.startswith("$"):
        return "BOUND_RUNTIME_INPUT"
    if token is None or isinstance(token, (bool, int)):
        return "CONTRACT_LITERAL"
    return "SCALAR_OR_FOREIGN_INPUT"


def derive_identity_hash_dependency_graph(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the whole-artifact graph from declarations and closed grammar."""

    pointers = _discover_source_pointers(full_strict_artifact, policy_artifact)
    structural = full_strict_artifact.get("task8_structural_admission")
    contract = (
        structural.get("identity_hash_dependency_contract")
        if isinstance(structural, Mapping)
        else None
    )
    _require(
        isinstance(contract, Mapping) and contract.get("schema_version") == _GRAPH_SCHEMA,
        "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )
    contract = cast(Mapping[str, Any], contract)
    producer_schema = [
        "source_pointer",
        "artifact_name",
        "layer",
        "scope",
        "output_kind",
        "symbol",
        "dependency_payload_pointer",
    ]
    external_schema = ["source_pointer", "token_path", "symbol", "authority"]
    producer_rows = contract.get("producer_authority_tuple")
    external_rows = contract.get("allowed_external_input_tuple")
    _require(
        tuple(contract)
        == (
            "schema_version",
            "source_discovery",
            "recipe_grammar_tuple",
            "producer_authority_schema",
            "producer_authority_tuple",
            "producer_authority_sha256",
            "allowed_external_input_schema",
            "allowed_external_input_tuple",
            "allowed_external_input_sha256",
            "dashboard_special_case_allowed",
            "synthetic_symbol_special_case_allowed",
            "rejected_non_dashboard_cycle_disposition",
        )
        and contract.get("source_discovery") == "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS"
        and contract.get("recipe_grammar_tuple") == ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"]
        and contract.get("dashboard_special_case_allowed") is False
        and contract.get("synthetic_symbol_special_case_allowed") is False
        and contract.get("rejected_non_dashboard_cycle_disposition")
        == "SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY"
        and contract.get("producer_authority_schema") == producer_schema
        and isinstance(producer_rows, list)
        and contract.get("producer_authority_sha256")
        == _canonical_sha256([_GRAPH_PRODUCER_DOMAIN, producer_rows]),
        "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )
    _require(
        contract.get("allowed_external_input_schema") == external_schema
        and isinstance(external_rows, list)
        and contract.get("allowed_external_input_sha256")
        == _canonical_sha256([_GRAPH_EXTERNAL_DOMAIN, external_rows]),
        "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_INVALID",
    )
    raw_producer_rows = cast(list[Any], producer_rows)
    raw_external_rows = cast(list[Any], external_rows)

    supplied_producer_rows: list[list[str]] = []
    supplied_by_pointer: dict[str, list[str]] = {}
    for raw_row in raw_producer_rows:
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 7
            and all(isinstance(value, str) and value for value in raw_row),
            "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
        )
        row = cast(list[str], raw_row)
        pointer = row[0]
        _require(
            pointer not in supplied_by_pointer,
            "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
        )
        supplied_producer_rows.append(row)
        supplied_by_pointer[pointer] = row
    _validate_producer_symbols(
        supplied_producer_rows,
        full_strict=full_strict_artifact,
        policy=policy_artifact,
    )
    supplied_symbols = [row[5] for row in supplied_producer_rows]
    _require(
        len(set(supplied_symbols)) == len(supplied_symbols),
        "TASK8_V25_INDEPENDENT_DUPLICATE_PRODUCER",
    )

    expected_producer_rows = [
        _producer_authority_row(pointer, full_strict_artifact, policy_artifact)
        for pointer in pointers
    ]
    _validate_producer_symbols(
        expected_producer_rows,
        full_strict=full_strict_artifact,
        policy=policy_artifact,
    )
    expected_by_pointer = {row[0]: row for row in expected_producer_rows}
    _require(
        set(supplied_by_pointer) == set(expected_by_pointer)
        and all(
            supplied_by_pointer[pointer] == row for pointer, row in expected_by_pointer.items()
        ),
        "TASK8_V25_INDEPENDENT_PRODUCER_AUTHORITY_DRIFT",
    )

    nodes = [[row[1], row[2], row[3], row[4], row[5], row[0]] for row in expected_producer_rows]
    symbol_nodes: dict[str, list[list[str]]] = {}
    nodes_by_id: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _node_id(node)
        _require(node_id not in nodes_by_id, "TASK8_V25_INDEPENDENT_DUPLICATE_PRODUCER")
        nodes_by_id[node_id] = node
        symbol_nodes.setdefault(node[4], []).append(node)
    _require(
        all(len(candidates) == 1 for candidates in symbol_nodes.values()),
        "TASK8_V25_INDEPENDENT_DUPLICATE_PRODUCER",
    )

    supplied_external: dict[tuple[str, str, str], str] = {}
    for raw_row in raw_external_rows:
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 4
            and all(isinstance(value, str) and value for value in raw_row),
            "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_INVALID",
        )
        pointer, scalar_path, symbol, authority = raw_row
        key = (pointer, scalar_path, symbol)
        _require(
            pointer in expected_by_pointer
            and key not in supplied_external
            and authority in _ALLOWED_LEAF_AUTHORITIES,
            "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_INVALID",
        )
        supplied_external[key] = authority

    expected_external_rows = _derive_external_input_rows(
        full_strict=full_strict_artifact,
        policy=policy_artifact,
        producer_rows=expected_producer_rows,
    )
    expected_external = {(row[0], row[1], row[2]): row[3] for row in expected_external_rows}
    supplied_keys = set(supplied_external)
    expected_keys = set(expected_external)
    _require(
        supplied_keys <= expected_keys,
        "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_ORPHANED",
    )
    _require(
        expected_keys <= supplied_keys,
        "TASK8_V25_INDEPENDENT_GOVERNED_REFERENCE_UNRESOLVED",
    )
    _require(
        supplied_external == expected_external,
        "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_DRIFT",
    )

    values: dict[str, object] = {}
    payload_pointers = {row[0]: row[6] for row in expected_producer_rows}
    for pointer in pointers:
        value = _pointer_value(pointer, full_strict_artifact, policy_artifact)
        _validate_recipe_grammar(
            payload_pointers[pointer],
            _relative_payload(value, payload_pointers[pointer]),
        )
        values[pointer] = value

    edges: set[tuple[str, str, str]] = set()
    leaves: set[tuple[str, str, str, str, str]] = set()
    consumed_external: set[tuple[str, str, str]] = set()
    for node in nodes:
        node_id = _node_id(node)
        pointer = node[5]
        payload = _relative_payload(values[pointer], payload_pointers[pointer])
        for token_path, token_value in _flatten(payload):
            identifiers = (
                sorted(_recipe_identifiers(token_value)) if isinstance(token_value, str) else []
            )
            if not identifiers:
                leaves.add(
                    (node_id, token_path, str(token_value), _leaf_authority(token_value), pointer)
                )
                continue
            for identifier in identifiers:
                candidates = symbol_nodes.get(identifier, [])
                if len(candidates) == 1:
                    dependency_id = _node_id(candidates[0])
                    if dependency_id == node_id:
                        _fail("TASK8_V25_INDEPENDENT_IDENTITY_HASH_CYCLE")
                    edges.add((node_id, dependency_id, f"{pointer}:{token_path}#{identifier}"))
                    continue
                key = (pointer, token_path, identifier)
                authority = expected_external.get(key)
                if authority is None:
                    _fail("TASK8_V25_INDEPENDENT_GOVERNED_REFERENCE_UNRESOLVED")
                consumed_external.add(key)
                leaves.add((node_id, f"{token_path}#{identifier}", identifier, authority, pointer))
    _require(
        consumed_external == set(expected_external),
        "TASK8_V25_INDEPENDENT_EXTERNAL_INPUT_ORPHANED",
    )

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    for derived, dependency, _receipt in edges:
        _require(
            derived in adjacency and dependency in adjacency,
            "TASK8_V25_INDEPENDENT_GRAPH_ENDPOINT_INVALID",
        )
        adjacency[derived].add(dependency)
    if _cycle(adjacency) is not None:
        _fail("TASK8_V25_INDEPENDENT_IDENTITY_HASH_CYCLE")

    node_tuple = sorted(nodes, key=_canonical_json)
    edge_tuple = sorted((list(row) for row in edges), key=_canonical_json)
    leaf_tuple = sorted((list(row) for row in leaves), key=_canonical_json)
    source_pointer_tuple = list(pointers)
    counts = {
        "node_count": len(node_tuple),
        "edge_count": len(edge_tuple),
        "leaf_count": len(leaf_tuple),
        "source_pointer_count": len(source_pointer_tuple),
    }
    graph: dict[str, Any] = {
        "schema_version": _GRAPH_SCHEMA,
        "node_tuple": node_tuple,
        "edge_tuple": edge_tuple,
        "leaf_tuple": leaf_tuple,
        "source_pointer_tuple": source_pointer_tuple,
        "counts": counts,
    }
    graph["graph_sha256"] = _canonical_sha256(
        [_GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    return graph


def _expected_projection_sql() -> str:
    match = re.fullmatch(
        r'H\(\["(?P<version>[a-z0-9-]+)",'
        r"(?P<row_fields>[a-z0-9_,]+),"
        r"ORDERED\[\((?P<root_fields>[a-z0-9_,]+)\)\]\]\)",
        _PROJECTION_RECIPE,
    )
    _require(match is not None, "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID")
    if match is None:
        _fail("TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID")
    row_fields = tuple(match.group("row_fields").split(","))
    root_fields = tuple(match.group("root_fields").split(","))
    _require(
        row_fields
        == (
            "tenant_id",
            "deal_id",
            "run_id",
            "dashboard_row_id",
            "coverage_record_id",
            "row_disposition",
            "count",
        )
        and root_fields
        == (
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
            "root_output_object_kind",
            "dashboard_root_link_id",
        ),
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )
    tenant, deal, run, dashboard_row, coverage, disposition, _count = row_fields
    root_kind, root_ordinal, root_object, root_object_kind, root_link = root_fields
    order_fields = tuple(field for field in root_fields if field != root_object_kind)
    return (
        f'preimage:=\'["{match.group("version")}","\''
        f"||dashboard_row.{tenant}::text||'\",\"'"
        f"||dashboard_row.{deal}::text||'\",\"'"
        f"||dashboard_row.{run}::text||'\",\"'"
        f"||dashboard_row.{dashboard_row}::text||'\",\"'"
        f"||dashboard_row.{coverage}||'\",\"'"
        f"||dashboard_row.{disposition}||'\",'"
        "||link_count::text||',['; first_link:=true; FOR root_link IN SELECT "
        f"{','.join(root_fields)} FROM public.methodology_truth_dashboard_root_links "
        f"WHERE {tenant}=dashboard_row.{tenant} AND {deal}=dashboard_row.{deal} "
        f"AND {run}=dashboard_row.{run} AND {dashboard_row}=dashboard_row.{dashboard_row} "
        f"ORDER BY {','.join(order_fields)} LOOP IF NOT first_link THEN "
        "preimage:=preimage||','; END IF; first_link:=false; "
        f"preimage:=preimage||'[\"'||root_link.{root_kind}||'\",'"
        f"||root_link.{root_ordinal}::text||',\"'"
        f"||root_link.{root_object}::text||'\",\"'"
        f"||root_link.{root_object_kind}||'\",\"'"
        f"||root_link.{root_link}::text||'\"]'; END LOOP; "
        "preimage:=preimage||']]'; "
    )


def _validate_dashboard_recipes(artifact: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    uuid_section = artifact.get("uuid_namespace_registry")
    uuid_rows = uuid_section.get("rows") if isinstance(uuid_section, Mapping) else None
    _require(isinstance(uuid_rows, list), "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID")
    dashboard_rows = [
        row
        for row in cast(list[Any], uuid_rows)
        if isinstance(row, list) and len(row) == 3 and row[0] == "dashboard_row_id"
    ]
    _require(
        len(dashboard_rows) == 1
        and dashboard_rows[0][1] == _DASHBOARD_ROW_NAMESPACE
        and tuple(dashboard_rows[0][2]) == _DASHBOARD_ROW_NAME_TUPLE,
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )
    financial = policy.get("financial_output_schema")
    row_payload = financial.get("row_payload_preimage") if isinstance(financial, Mapping) else None
    dashboard_authority = (
        financial.get("dashboard_row_uuid_authority") if isinstance(financial, Mapping) else None
    )
    _require(
        isinstance(row_payload, list)
        and tuple(row_payload) == _ROW_PAYLOAD_RECIPE
        and isinstance(dashboard_authority, Mapping)
        and dashboard_authority.get("namespace_uuid") == _DASHBOARD_ROW_NAMESPACE
        and tuple(dashboard_authority.get("name_preimage", ())) == _DASHBOARD_ROW_NAME_TUPLE,
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )
    digest_section = artifact.get("digest_recipe_registry")
    digest_rows = digest_section.get("rows") if isinstance(digest_section, Mapping) else None
    projection = [
        row[1]
        for row in digest_rows or []
        if isinstance(row, list)
        and len(row) == 2
        and row[0] == "methodology_truth_dashboard_root_projection_sha256"
    ]
    _require(
        projection == [_PROJECTION_RECIPE]
        and "run_output_object_id" not in _PROJECTION_RECIPE
        and "dashboard_output_object_id" not in _PROJECTION_RECIPE
        and "run_output_object_id" not in str(row_payload)
        and "dashboard_output_object_id" not in str(row_payload),
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )
    expected_projection = _expected_projection_sql()
    body = _dashboard_body(artifact)
    try:
        observed_projection = body[body.index("preimage:=") : body.index("actual_hash:=")]
    except ValueError as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID"
        ) from exc
    _require(
        observed_projection == expected_projection,
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )
    requirement = _expected_requirement_contract()
    expected_contract = {
        "authority_id": "dashboard-root-equation-v3",
        "projection_recipe_id": "methodology_truth_dashboard_root_projection_sha256",
        "projection_recipe_version": "methodology-truth-dashboard-root-projection-v2",
        "row_payload_policy_pointer": "#/financial_output_schema/row_payload_preimage",
        "row_payload_recipe_version": "financial-diligence-row-v2",
        "forbidden_self_field_tuple": ["run_output_object_id", "dashboard_output_object_id"],
        "derivation_order_tuple": [
            "dashboard_row_id",
            "dashboard_root_link_ids",
            "root_projection_sha256",
            "row_payload_sha256",
            "native_identity_sha256_and_native_output_sha256",
            "output_object_id",
        ],
        "source_recipe_binding_tuple": [
            [
                "full_strict",
                "methodology_truth_dashboard_root_projection_sha256",
                _canonical_sha256(_PROJECTION_RECIPE),
            ],
            [
                "fdd_formula_policy",
                "#/financial_output_schema/row_payload_preimage",
                _canonical_sha256(list(_ROW_PAYLOAD_RECIPE)),
            ],
        ],
        "governed_projection_sql_sha256": hashlib.sha256(
            expected_projection.encode("utf-8")
        ).hexdigest(),
        "requirement_binding_contract_pointer": (
            "#/task8_fdd_contract/dashboard_contract/requirement_binding_contract"
        ),
        "policy_v2_requirement_literal_sha256": requirement["policy_v2_requirement_literal_sha256"],
        "governed_requirement_guard_sql_sha256": hashlib.sha256(
            _render_requirement_guard(requirement).encode("utf-8")
        ).hexdigest(),
        "runtime_normative_policy_read_allowed": False,
    }
    structural = artifact.get("task8_structural_admission")
    observed_contract = (
        structural.get("dashboard_sql_recipe_contract") if isinstance(structural, Mapping) else None
    )
    _require(
        isinstance(observed_contract, Mapping) and dict(observed_contract) == expected_contract,
        "TASK8_V25_INDEPENDENT_DASHBOARD_RECIPE_INVALID",
    )


def _resolve_json_pointer(document: object, pointer: str) -> object:
    if pointer == "#":
        return document
    _require(
        pointer.startswith("#/"),
        "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
    )
    value = document
    try:
        for raw_token in pointer[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndependentVerificationError(
            "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID"
        ) from exc
    return value


def _validate_conservation(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> str:
    structural = artifact.get("task8_structural_admission")
    manifest = (
        structural.get("approved_semantics_conservation_manifest")
        if isinstance(structural, Mapping)
        else None
    )
    _require(
        isinstance(manifest, list) and len(manifest) == len(_CONSERVATION_BASELINES) == 8,
        "TASK8_V25_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
    )
    manifest = cast(list[Any], manifest)
    reviewer_count = 0
    for ordinal, raw_row in enumerate(manifest):
        baseline = _CONSERVATION_BASELINES[ordinal]
        selectors = _CONSERVATION_SELECTORS[ordinal]
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 5
            and _canonical_json(raw_row[:4]) == _canonical_json(list(baseline))
            and isinstance(raw_row[4], list),
            "TASK8_V25_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
        )
        actual_selectors = tuple(
            tuple(receipt[:3])
            for receipt in raw_row[4]
            if isinstance(receipt, list) and len(receipt) == 4
        )
        _require(
            actual_selectors == selectors,
            "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
        )
        for receipt in raw_row[4]:
            _require(
                isinstance(receipt, list)
                and len(receipt) == 4
                and all(isinstance(item, str) and item for item in receipt),
                "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
            )
            source, locator, kind, expected_digest = receipt
            _require(
                bool(_SHA256.fullmatch(expected_digest)),
                "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
            )
            if source == "full_strict" and kind == "CANONICAL_JSON_SHA256":
                actual = _canonical_sha256(_resolve_json_pointer(artifact, locator))
            elif source == "fdd_formula_policy" and kind == "CANONICAL_JSON_SHA256":
                actual = _canonical_sha256(_resolve_json_pointer(policy, locator))
            elif (
                source == "reviewer_map"
                and kind == "RAW_FILE_SHA256"
                and locator == _REVIEWER_MAP_PATH
            ):
                reviewer_count += 1
                actual = hashlib.sha256(reviewer_map_raw).hexdigest()
            else:
                _fail("TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID")
            _require(
                actual == expected_digest,
                "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_MISMATCH",
            )
    _require(reviewer_count == 1, "TASK8_V25_INDEPENDENT_CONSERVATION_RECEIPT_INVALID")
    digest = _canonical_sha256([_CONSERVATION_SCHEMA, manifest])
    _require(
        isinstance(structural, Mapping)
        and structural.get("approved_semantics_conservation_sha256") == digest
        and digest == _EXPECTED_CONSERVATION_SHA256,
        "TASK8_V25_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
    )
    return digest


def _derive_counts(artifact: Mapping[str, Any]) -> dict[str, int]:
    erasure_registry = artifact.get("erasure_execution_registry")
    erasure_rows = erasure_registry.get("rows") if isinstance(erasure_registry, Mapping) else None
    _require(
        isinstance(erasure_rows, list)
        and bool(erasure_rows)
        and isinstance(erasure_rows[0], Mapping),
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    erasure = cast(Mapping[str, Any], cast(list[Any], erasure_rows)[0])
    task8 = artifact.get("task8_fdd_contract")
    installed = task8.get("installed_partition") if isinstance(task8, Mapping) else None
    task11 = task8.get("task11_exit_obligation") if isinstance(task8, Mapping) else None
    _require(
        isinstance(installed, Mapping) and isinstance(task11, Mapping),
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    installed = cast(Mapping[str, Any], installed)
    task11 = cast(Mapping[str, Any], task11)

    def rows_in(section_name: str) -> list[Any]:
        section = artifact.get(section_name)
        rows = section.get("rows") if isinstance(section, Mapping) else None
        _require(isinstance(rows, list), "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID")
        return cast(list[Any], rows)

    current_relations = erasure.get("current_relation_tuple")
    future_relations = erasure.get("future_relation_tuple")
    complete_relations = erasure.get("complete_relation_tuple")
    undischarged = task11.get("undischarged_rule_tuple")
    _require(
        all(
            isinstance(value, list)
            for value in (current_relations, future_relations, complete_relations, undischarged)
        ),
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    current_relations = cast(list[Any], current_relations)
    future_relations = cast(list[Any], future_relations)
    complete_relations = cast(list[Any], complete_relations)
    undischarged = cast(list[Any], undischarged)
    counts = {
        "semantic_step_count": len(rows_in("semantic_step_registry")),
        "edge_rule_count": len(rows_in("edge_rule_registry")),
        "usage_route_count": len(rows_in("usage_route_registry")),
        "usage_extractor_count": len(rows_in("usage_extractor_registry")),
        "provider_count": len(rows_in("provider_registry")),
        "schema_count": len(rows_in("schema_registry")),
        "current_ddl_table_count": len(rows_in("current_ddl_registry")),
        "installed_tenant_rls_table_count": installed.get("successor_tenant_rls_table_count"),
        "erasure_current_relation_count": len(current_relations),
        "erasure_future_relation_count": len(future_relations),
        "erasure_complete_relation_count": len(complete_relations),
        "task11_restart_cut_count": task11.get("restart_cut_count"),
        "task11_undischarged_rule_count": len(undischarged),
    }
    _require(
        all(isinstance(value, int) and value >= 0 for value in counts.values()),
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    return cast(dict[str, int], counts)


def _component_and_contract_hashes(
    artifact: Mapping[str, Any],
    artifact_raw: bytes,
) -> tuple[dict[str, str], str]:
    task8 = artifact.get("task8_fdd_contract")
    _require(isinstance(task8, Mapping), "TASK8_V25_INDEPENDENT_TASK8_COMPONENT_INVALID")
    task8 = cast(Mapping[str, Any], task8)
    expected_task8 = _canonical_sha256(
        ["full-strict-v25-task8-fdd-contract-v2", _without_hash(task8)]
    )
    _require(
        task8.get("expected_sha256") == expected_task8,
        "TASK8_V25_INDEPENDENT_TASK8_COMPONENT_INVALID",
    )
    components = {
        f"{section}_hash": _canonical_sha256([_COMPONENT_DOMAIN, section, copy.deepcopy(value)])
        for section, value in artifact.items()
    }
    components["task8_fdd_contract_hash"] = expected_task8
    ordered = [[name, digest] for name, digest in components.items()]
    contract_hash = _canonical_sha256(
        [
            _PIPELINE_DOMAIN,
            _PIPELINE_VERSION,
            hashlib.sha256(artifact_raw).hexdigest(),
            ordered,
        ]
    )
    components["pipeline_contract_hash"] = contract_hash
    return components, contract_hash


def _fixture_graph_hash(graph: Mapping[str, Any]) -> tuple[str, dict[str, int]]:
    node_tuple = graph.get("node_tuple")
    edge_tuple = graph.get("edge_tuple")
    leaf_tuple = graph.get("leaf_tuple")
    source_pointer_tuple = graph.get("source_pointer_tuple")
    _require(
        graph.get("schema_version") == _GRAPH_SCHEMA
        and all(
            isinstance(value, list)
            for value in (node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple)
        ),
        "TASK8_V25_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    node_tuple = cast(list[Any], node_tuple)
    edge_tuple = cast(list[Any], edge_tuple)
    leaf_tuple = cast(list[Any], leaf_tuple)
    source_pointer_tuple = cast(list[Any], source_pointer_tuple)
    counts = {
        "node_count": len(node_tuple),
        "edge_count": len(edge_tuple),
        "leaf_count": len(leaf_tuple),
        "source_pointer_count": len(source_pointer_tuple),
    }
    digest = _canonical_sha256(
        [_GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    _require(
        graph.get("counts") == counts and graph.get("graph_sha256") == digest,
        "TASK8_V25_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    return digest, counts


def _validate_artifact(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v25_artifact_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str, dict[str, str], str]:
    _validate_parent(parent_artifact_raw)
    policy = _validate_policy(policy_artifact_raw)
    _validate_reviewer_map(reviewer_map_raw)
    artifact = _decode(v25_artifact_raw, "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID")
    _require(
        _render(artifact) == v25_artifact_raw,
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    _require(
        tuple(artifact) == _EXPECTED_TOP_LEVEL_SECTIONS,
        "TASK8_V25_INDEPENDENT_TOP_LEVEL_SECTION_CLOSURE_INVALID",
    )
    metadata = artifact.get("metadata")
    _require(
        isinstance(metadata, Mapping)
        and metadata.get("artifact_schema") == _ARTIFACT_SCHEMA
        and metadata.get("pipeline_contract_version") == _PIPELINE_VERSION
        and metadata.get("append_frozen") is True
        and metadata.get("activation_status") == "INACTIVE",
        "TASK8_V25_INDEPENDENT_ARTIFACT_INVALID",
    )
    structural = artifact.get("task8_structural_admission")
    _require(
        isinstance(structural, Mapping)
        and tuple(structural)
        == (
            "section_schema",
            "parent_provenance",
            "approved_delta_manifest",
            "identity_hash_dependency_contract",
            "dashboard_sql_recipe_contract",
            "approved_semantics_conservation_manifest",
            "approved_semantics_conservation_sha256",
            "expected_sha256",
        )
        and structural.get("section_schema") == _STRUCTURAL_SCHEMA,
        "TASK8_V25_INDEPENDENT_STRUCTURAL_HASH_MISMATCH",
    )
    structural = cast(Mapping[str, Any], structural)
    _require(
        structural.get("parent_provenance") == _EXPECTED_PARENT_PROVENANCE,
        "TASK8_V25_INDEPENDENT_PARENT_PROVENANCE_INVALID",
    )
    _require(
        structural.get("approved_delta_manifest")
        == [list(row) for row in _APPROVED_DELTA_MANIFEST],
        "TASK8_V25_INDEPENDENT_APPROVED_DELTA_MANIFEST_INVALID",
    )
    binding = _artifact_policy_binding(artifact)
    _validate_requirement_contract_and_sql(artifact)
    _validate_dashboard_recipes(artifact, policy)
    graph = derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    _require(
        graph.get("graph_sha256") == _EXPECTED_GRAPH_SHA256
        and graph.get("counts")
        == {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V25_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    conservation_hash = _validate_conservation(artifact, policy, reviewer_map_raw)
    _require(
        structural.get("expected_sha256")
        == _canonical_sha256([_STRUCTURAL_SCHEMA, _without_hash(structural)]),
        "TASK8_V25_INDEPENDENT_STRUCTURAL_HASH_MISMATCH",
    )
    components, contract_hash = _component_and_contract_hashes(artifact, v25_artifact_raw)
    artifact_sha256 = hashlib.sha256(v25_artifact_raw).hexdigest()
    _require(
        artifact_sha256 == _EXPECTED_ARTIFACT_SHA256,
        "TASK8_V25_INDEPENDENT_ARTIFACT_HASH_MISMATCH",
    )
    _require(
        contract_hash == _EXPECTED_CONTRACT_SHA256,
        "TASK8_V25_INDEPENDENT_CONTRACT_HASH_MISMATCH",
    )
    return (
        artifact,
        policy,
        graph,
        conservation_hash,
        str(binding["binding_sha256"]),
        components,
        contract_hash,
    )


def derive_fixture(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v25_artifact_raw: bytes,
) -> dict[str, object]:
    """Derive the complete inactive fixture without writing any path."""

    artifact, _policy, graph, conservation_hash, binding_hash, components, contract_hash = (
        _validate_artifact(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=reviewer_map_raw,
            v25_artifact_raw=v25_artifact_raw,
        )
    )
    fixture: dict[str, object] = {
        "artifact_schema": _FIXTURE_SCHEMA,
        "pipeline_contract_version": _PIPELINE_VERSION,
        "artifact_sha256": hashlib.sha256(v25_artifact_raw).hexdigest(),
        "FULL_STRICT_V25_CONTRACT_HASH": contract_hash,
        "component_hashes": components,
        "identity_hash_dependency_graph": graph,
        "approved_semantics_conservation": {
            "schema_version": _CONSERVATION_SCHEMA,
            "entry_count": 8,
            "sha256": conservation_hash,
        },
        "policy_binding": {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_sha256": _POLICY_ARTIFACT_SHA256,
            "policy_sha256": _POLICY_SHA256,
            "reviewer_map_relative_path": _REVIEWER_MAP_PATH,
            "reviewer_map_sha256": _REVIEWER_MAP_SHA256,
            "binding_sha256": binding_hash,
        },
        "counts": _derive_counts(artifact),
        "activation_status": "INACTIVE",
    }
    _require(
        hashlib.sha256(_render(fixture)).hexdigest() == _EXPECTED_FIXTURE_SHA256,
        "TASK8_V25_INDEPENDENT_FIXTURE_HASH_MISMATCH",
    )
    return fixture


def derive_fixture_bytes(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v25_artifact_raw: bytes,
) -> bytes:
    """Return canonical fixture bytes in memory only."""

    return _render(
        derive_fixture(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=reviewer_map_raw,
            v25_artifact_raw=v25_artifact_raw,
        )
    )


def verify_fixture(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
    v25_artifact_raw: bytes,
    fixture_raw: bytes,
) -> dict[str, object]:
    """Verify supplied fixture bytes and return hashes and counts only."""

    artifact, _policy, graph, conservation_hash, binding_hash, components, contract_hash = (
        _validate_artifact(
            parent_artifact_raw=parent_artifact_raw,
            policy_artifact_raw=policy_artifact_raw,
            reviewer_map_raw=reviewer_map_raw,
            v25_artifact_raw=v25_artifact_raw,
        )
    )
    fixture = _decode(fixture_raw, "TASK8_V25_INDEPENDENT_FIXTURE_INVALID")
    _require(
        _render(fixture) == fixture_raw
        and tuple(fixture)
        == (
            "artifact_schema",
            "pipeline_contract_version",
            "artifact_sha256",
            "FULL_STRICT_V25_CONTRACT_HASH",
            "component_hashes",
            "identity_hash_dependency_graph",
            "approved_semantics_conservation",
            "policy_binding",
            "counts",
            "activation_status",
        )
        and fixture.get("artifact_schema") == _FIXTURE_SCHEMA
        and fixture.get("pipeline_contract_version") == _PIPELINE_VERSION
        and fixture.get("activation_status") == "INACTIVE",
        "TASK8_V25_INDEPENDENT_FIXTURE_INVALID",
    )
    artifact_sha256 = hashlib.sha256(v25_artifact_raw).hexdigest()
    _require(
        fixture.get("artifact_sha256") == artifact_sha256,
        "TASK8_V25_INDEPENDENT_ARTIFACT_HASH_MISMATCH",
    )
    _require(
        fixture.get("component_hashes") == components,
        "TASK8_V25_INDEPENDENT_COMPONENT_HASH_MISMATCH",
    )
    _require(
        fixture.get("FULL_STRICT_V25_CONTRACT_HASH") == contract_hash,
        "TASK8_V25_INDEPENDENT_CONTRACT_HASH_MISMATCH",
    )
    fixture_graph = fixture.get("identity_hash_dependency_graph")
    _require(
        isinstance(fixture_graph, Mapping),
        "TASK8_V25_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    fixture_graph = cast(Mapping[str, Any], fixture_graph)
    graph_hash, graph_counts = _fixture_graph_hash(fixture_graph)
    _require(
        dict(fixture_graph) == graph,
        "TASK8_V25_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    _require(
        fixture.get("approved_semantics_conservation")
        == {
            "schema_version": _CONSERVATION_SCHEMA,
            "entry_count": 8,
            "sha256": conservation_hash,
        },
        "TASK8_V25_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
    )
    _require(
        fixture.get("policy_binding")
        == {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_sha256": _POLICY_ARTIFACT_SHA256,
            "policy_sha256": _POLICY_SHA256,
            "reviewer_map_relative_path": _REVIEWER_MAP_PATH,
            "reviewer_map_sha256": _REVIEWER_MAP_SHA256,
            "binding_sha256": binding_hash,
        },
        "TASK8_V25_INDEPENDENT_POLICY_BINDING_INVALID",
    )
    counts = _derive_counts(artifact)
    _require(
        fixture.get("counts") == counts,
        "TASK8_V25_INDEPENDENT_COUNT_MISMATCH",
    )
    fixture_sha256 = hashlib.sha256(fixture_raw).hexdigest()
    _require(
        fixture_sha256 == _EXPECTED_FIXTURE_SHA256,
        "TASK8_V25_INDEPENDENT_FIXTURE_HASH_MISMATCH",
    )
    return {
        "verification_path": "TASK8_V25_INDEPENDENT_STDLIB_V1",
        "parent_artifact_sha256": _PARENT_ARTIFACT_SHA256,
        "artifact_sha256": artifact_sha256,
        "fixture_sha256": fixture_sha256,
        "component_hashes": components,
        "contract_hash": contract_hash,
        "identity_graph_sha256": graph_hash,
        "identity_graph_counts": graph_counts,
        "conservation_sha256": conservation_hash,
        "policy_binding_sha256": binding_hash,
        "counts": counts,
    }


def _uuid5(namespace: str, value: object) -> str:
    canonical_name_sha256 = hashlib.sha256(_canonical_json(value)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), canonical_name_sha256))


def derive_dashboard_identity_vector(**inputs: Any) -> dict[str, object]:
    """Independent implementation of the approved acyclic five-stage chain."""

    tenant_id = str(inputs["tenant_id"])
    deal_id = str(inputs["deal_id"])
    run_id = str(inputs["run_id"])
    pipeline_version = str(inputs["pipeline_contract_version"])
    pipeline_hash = str(inputs["pipeline_contract_hash"])
    coverage_record_id = str(inputs["coverage_record_id"])
    row_id = _uuid5(
        _DASHBOARD_ROW_NAMESPACE,
        [
            "methodology-truth-dashboard-row-v2",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            coverage_record_id,
            inputs["methodology_version_record_id"],
            inputs["methodology_question_record_id"],
            inputs["question_payload_hash"],
        ],
    )
    root_links: list[list[object]] = []
    for root in sorted(
        inputs["roots"],
        key=lambda item: (
            str(item["root_kind"]),
            int(item["root_ordinal"]),
            str(item["root_output_object_id"]),
        ),
    ):
        link_id = _uuid5(
            _DASHBOARD_ROOT_LINK_NAMESPACE,
            [
                "methodology-truth-dashboard-root-link-v1",
                tenant_id,
                deal_id,
                run_id,
                row_id,
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
            ],
        )
        root_links.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )
    projection_hash = _canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v2",
            tenant_id,
            deal_id,
            run_id,
            row_id,
            coverage_record_id,
            inputs["row_disposition"],
            len(root_links),
            root_links,
        ]
    )
    payload_hash = _canonical_sha256(
        [
            "financial-diligence-row-v2",
            [*inputs["row_trace_prefix"], coverage_record_id, row_id],
            inputs["row_disposition"],
            inputs["disposition_reason_code"],
            inputs["result_records"],
            inputs["answer_values"],
            inputs["task_gaps"],
            inputs["calculations"],
            projection_hash,
        ]
    )
    native_identity_hash = _canonical_sha256(
        [
            "native-identity-v1",
            tenant_id,
            deal_id,
            run_id,
            _DASHBOARD_OUTPUT_KIND,
            [row_id],
        ]
    )
    native_output_hash = _canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            _DASHBOARD_OUTPUT_KIND,
            [row_id],
            [coverage_record_id, projection_hash, inputs["row_disposition"], payload_hash],
        ]
    )
    output_object_id = _uuid5(
        _OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            "NATIVE_ROW",
            _DASHBOARD_OUTPUT_KIND,
            native_identity_hash,
            native_output_hash,
        ],
    )
    return {
        "dashboard_row_id": row_id,
        "root_link_tuple": root_links,
        "root_projection_sha256": projection_hash,
        "row_payload_sha256": payload_hash,
        "native_identity_sha256": native_identity_hash,
        "native_output_sha256": native_output_hash,
        "output_object_id": output_object_id,
    }


class _CodeOnlyParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        _fail("TASK8_V25_INDEPENDENT_CLI_ARGUMENT_INVALID")


def _parser() -> argparse.ArgumentParser:
    parser = _CodeOnlyParser(add_help=True)
    parser.add_argument("--mode", choices=("derive", "verify"), required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--reviewer-map", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    return parser


def _reject_canonical_input(paths: Sequence[Path]) -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = {
        (root / "contracts" / "full_strict_v25_reviewed_preimage_v1.json").resolve(),
        (
            root / "tests" / "slice100_task8" / "fixtures" / "full_strict_v25_contract_hash_v1.json"
        ).resolve(),
    }
    try:
        observed = {path.resolve() for path in paths}
    except OSError as exc:
        raise IndependentVerificationError("TASK8_V25_INDEPENDENT_INPUT_MISSING") from exc
    _require(
        forbidden.isdisjoint(observed),
        "TASK8_V25_INDEPENDENT_CANONICAL_INPUT_FORBIDDEN",
    )


def _cli_report(fixture: Mapping[str, Any], result: str) -> dict[str, object]:
    graph = fixture["identity_hash_dependency_graph"]
    counts = {**fixture["counts"], **graph["counts"]}
    fixture_sha256 = hashlib.sha256(_render(fixture)).hexdigest()
    _require(
        fixture_sha256 == _EXPECTED_FIXTURE_SHA256,
        "TASK8_V25_INDEPENDENT_FIXTURE_HASH_MISMATCH",
    )
    return {
        "activation_status": "INACTIVE",
        "artifact_sha256": fixture["artifact_sha256"],
        "conservation_sha256": fixture["approved_semantics_conservation"]["sha256"],
        "contract_hash": fixture["FULL_STRICT_V25_CONTRACT_HASH"],
        "counts": counts,
        "graph_sha256": graph["graph_sha256"],
        "fixture_sha256": fixture_sha256,
        "policy_binding_sha256": fixture["policy_binding"]["binding_sha256"],
        "result": result,
        "verification_path": "TASK8_V25_INDEPENDENT_STDLIB_V1",
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(None if argv is None else list(argv))
        if args.mode == "verify" and args.fixture is None:
            _fail("TASK8_V25_INDEPENDENT_CLI_ARGUMENT_INVALID")
        input_paths = [args.parent, args.policy, args.reviewer_map, args.artifact]
        if args.fixture is not None:
            input_paths.append(args.fixture)
        _reject_canonical_input(input_paths)
        parent_raw = args.parent.read_bytes()
        policy_raw = args.policy.read_bytes()
        reviewer_map_raw = args.reviewer_map.read_bytes()
        artifact_raw = args.artifact.read_bytes()
        if args.mode == "derive":
            fixture = derive_fixture(
                parent_artifact_raw=parent_raw,
                policy_artifact_raw=policy_raw,
                reviewer_map_raw=reviewer_map_raw,
                v25_artifact_raw=artifact_raw,
            )
            output = _cli_report(fixture, "DERIVED")
        else:
            fixture_raw = args.fixture.read_bytes()
            report = verify_fixture(
                parent_artifact_raw=parent_raw,
                policy_artifact_raw=policy_raw,
                reviewer_map_raw=reviewer_map_raw,
                v25_artifact_raw=artifact_raw,
                fixture_raw=fixture_raw,
            )
            fixture = _decode(fixture_raw, "TASK8_V25_INDEPENDENT_FIXTURE_INVALID")
            output = _cli_report(fixture, "VERIFIED")
            _require(
                output["artifact_sha256"] == report["artifact_sha256"],
                "TASK8_V25_INDEPENDENT_FIXTURE_INVALID",
            )
    except IndependentVerificationError as exc:
        print(json.dumps({"reason_code": exc.code, "result": "FAILED"}, sort_keys=True))
        return 2
    except OSError:
        print(
            json.dumps(
                {"reason_code": "TASK8_V25_INDEPENDENT_INPUT_MISSING", "result": "FAILED"},
                sort_keys=True,
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {"reason_code": "TASK8_V25_INDEPENDENT_UNEXPECTED_FAILURE", "result": "FAILED"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IndependentVerificationError",
    "derive_dashboard_identity_vector",
    "derive_fixture",
    "derive_fixture_bytes",
    "derive_identity_hash_dependency_graph",
    "main",
    "validate_requirement_vector",
    "verify_fixture",
]
