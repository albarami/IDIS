"""Materialize the bounded, inactive full-strict-v22 reviewed successor.

The builder is hermetic: its only authorities are the frozen full-strict-v21
artifact, the frozen FDD formula-policy artifact, and that policy's frozen hash
fixture.  It imports neither runtime code nor the independent Task-8 tests.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Never

from jsonschema import Draft202012Validator, SchemaError

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V21_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_POLICY_ARTIFACT = _REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
_POLICY_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v1_hash_v1.json"
)

_V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_POLICY_ARTIFACT_SHA256 = "750f4375bd68f79fa64ee596189a09a1a9354f2aabfa710893f6d31c85090ee4"
_POLICY_FIXTURE_SHA256 = "5218927ddab2f58a4e09977c4e320b407bbc6dd870d598953d2b98633d12108f"
_POLICY_SHA256 = "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2"

_V21 = "full-strict-v21"
_V22 = "full-strict-v22"
_V22_ARTIFACT_SCHEMA = "full-strict-v22-reviewed-preimage-v1"
_TASK8_SECTION_SCHEMA = "task8-fdd-successor-contract-v1"
_POLICY_ID = "fdd-formula-policy-v1"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_CURRENT_OUTPUT_EXACT_ONE_V1_SHA256 = (
    "92bf381c009c0dca37544811da4c73a77e5437b34f30f1310e51fe8a0b6f87ae"
)
_CURRENT_NATIVE_HASHES_SIGNATURE = "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
_CURRENT_OUTPUT_EXACT_ONE_SIGNATURE = "public.idis_task7_current_output_exact_one_v1()"
_PARTIAL_EXACT_ONE_AUTHORITY_ID = "dashboard-partial-native-root-exact-one-v1"

_BYTE_IDENTICAL_SECTIONS = (
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
_APPROVED_CHANGED_SECTIONS = {
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

_FDD_CALC_TYPES = (
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
_MODEL_FIELD_SETS = (
    ("FddSignedRatioValueV1", ("type", "value", "as_of", "time_window")),
    (
        "FddCalcInputsV1",
        (
            "schema_version",
            "formula_policy_id",
            "formula_policy_sha256",
            "formula_sha256",
            "coordinate",
            "bindings",
            "parent_calculations",
            "assumptions",
        ),
    ),
    (
        "FddEconomicCoordinateV1",
        (
            "period_kind",
            "period_start",
            "period_end",
            "as_of_date",
            "base_currency",
            "monetary_scale_unit",
        ),
    ),
    (
        "FddNumericBindingV1",
        (
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
    ),
    (
        "FddSetCompletenessBindingV1",
        (
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
    ),
    (
        "FddParentCalculationV1",
        (
            "ordinal",
            "calc_type",
            "calc_id",
            "economic_coordinate_sha256",
            "formula_sha256",
            "reproducibility_sha256",
        ),
    ),
    ("FddGovernedAssumptionV1", ("assumption_id", "value")),
    (
        "FddCalcOutputV1",
        (
            "primary_name",
            "primary_decimal",
            "primary_scale",
            "secondary_values",
            "unit",
            "currency",
            "formula_disposition",
        ),
    ),
    ("FddSecondaryOutputV1", ("name", "decimal", "scale", "unit", "currency")),
)
_SELECTOR_CONTRACTS = (
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
_DASHBOARD_TRUTH_TABLE = (
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
_TASK11_RULES = (
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

_ERASURE_REASON_CODE = "TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED"
_ERASURE_SQLSTATE = "23514"
_ERASURE_LOCK_MARKER = (
    "PERFORM pg_catalog.pg_advisory_xact_lock( "
    "pg_catalog.hashtextextended( "
    "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0 ) );"
)
_DASHBOARD_LOCK_SQL = (
    "PERFORM pg_catalog.pg_advisory_xact_lock( pg_catalog.hashtextextended( "
    "'idis-task7-erasure-tenant-lock-v1:'||scope_tenant::text,0 ) );"
)
_ERASURE_REFUSAL_SQL = (
    " IF EXISTS ( SELECT 1 FROM public.methodology_truth_dashboard_rows d "
    "WHERE d.tenant_id=$2 AND d.deal_id=$3 ) THEN RAISE EXCEPTION "
    f"'{_ERASURE_REASON_CODE}' USING ERRCODE='{_ERASURE_SQLSTATE}'; END IF;"
)
_DASHBOARD_SELECT_GRANT = (
    "GRANT SELECT (tenant_id,deal_id) ON TABLE "
    "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner"
)
_DASHBOARD_SELECT_REVOKE = (
    "REVOKE SELECT (tenant_id,deal_id) ON TABLE "
    "public.methodology_truth_dashboard_rows FROM idis_erasure_function_owner"
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v22 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _h(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _render(artifact: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(artifact),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _decode_json(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(code) from exc
    if not isinstance(value, dict):
        _error(code)
    return value


def _load_pinned(path: Path, sha256: str, code: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(code) from exc
    if hashlib.sha256(raw).hexdigest() != sha256:
        _error(code)
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(code)
    value = _decode_json(raw, code)
    if _render(value) != raw:
        _error(code)
    return raw, value


def _pinned_inputs() -> tuple[bytes, dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any]]:
    v21_raw, v21 = _load_pinned(
        _V21_ARTIFACT,
        _V21_ARTIFACT_SHA256,
        "TASK8_V22_BASE_ARTIFACT_HASH_MISMATCH",
    )
    policy_raw, policy = _load_pinned(
        _POLICY_ARTIFACT,
        _POLICY_ARTIFACT_SHA256,
        "TASK8_V22_POLICY_BINDING_INVALID",
    )
    fixture_raw, fixture = _load_pinned(
        _POLICY_FIXTURE,
        _POLICY_FIXTURE_SHA256,
        "TASK8_V22_POLICY_BINDING_INVALID",
    )
    return v21_raw, v21, policy_raw, policy, fixture_raw, fixture


_CANONICAL_DECIMAL_PATTERN = r"^(?:0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$"
_NONNEGATIVE_CANONICAL_DECIMAL_PATTERN = r"^(?:0|(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$"
_CANONICAL_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_COVERAGE_RECORD_ID_PATTERN = r"^mc_[0-9a-f]{24}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CURRENCY_CODES = (
    "USD",
    "EUR",
    "GBP",
    "CHF",
    "JPY",
    "CNY",
    "AED",
    "SAR",
    "QAR",
    "SGD",
    "HKD",
    "INR",
    "BRL",
    "CAD",
    "AUD",
)


def _closed_object(
    fields: tuple[str, ...],
    properties: Mapping[str, Any],
    *,
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if set(fields) != set(properties):
        _error("TASK8_V22_SCHEMA_PROPERTY_SET_INVALID")
    schema: dict[str, Any] = {
        "additionalProperties": False,
        "properties": copy.deepcopy(dict(properties)),
        "required": list(fields),
        "type": "object",
    }
    if conditions:
        schema["allOf"] = copy.deepcopy(conditions)
    return schema


def _nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"anyOf": [copy.deepcopy(dict(schema)), {"type": "null"}]}


def _nonblank_string() -> dict[str, Any]:
    return {"minLength": 1, "pattern": r".*\S.*", "type": "string"}


def _canonical_decimal() -> dict[str, Any]:
    return {"pattern": _CANONICAL_DECIMAL_PATTERN, "type": "string"}


def _canonical_uuid() -> dict[str, Any]:
    return {"pattern": _CANONICAL_UUID_PATTERN, "type": "string"}


def _sha256() -> dict[str, Any]:
    return {"pattern": _SHA256_PATTERN, "type": "string"}


def _currency() -> dict[str, Any]:
    return {"enum": list(_CURRENCY_CODES), "type": "string"}


def _fdd_period_schema() -> dict[str, Any]:
    fields = ("kind", "date", "start_date", "end_date")
    date = {"format": "date", "type": "string"}
    return _closed_object(
        fields,
        {
            "kind": {"enum": ["as_of", "range"], "type": "string"},
            "date": _nullable(date),
            "start_date": _nullable(date),
            "end_date": _nullable(date),
        },
        conditions=[
            {
                "oneOf": [
                    {
                        "properties": {
                            "kind": {"const": "as_of"},
                            "date": date,
                            "start_date": {"type": "null"},
                            "end_date": {"type": "null"},
                        }
                    },
                    {
                        "properties": {
                            "kind": {"const": "range"},
                            "date": {"type": "null"},
                            "start_date": date,
                            "end_date": date,
                        }
                    },
                ]
            }
        ],
    )


def _signed_ratio_schema() -> dict[str, Any]:
    date = {"format": "date", "type": "string"}
    time_window = _closed_object(
        ("label", "start_date", "end_date"),
        {
            "label": _nonblank_string(),
            "start_date": date,
            "end_date": date,
        },
    )
    return _closed_object(
        _MODEL_FIELD_SETS[0][1],
        {
            "type": {"const": "signed_ratio", "type": "string"},
            "value": _canonical_decimal(),
            "as_of": _nullable(date),
            "time_window": _nullable(time_window),
        },
        conditions=[
            {
                "oneOf": [
                    {
                        "properties": {
                            "as_of": date,
                            "time_window": {"type": "null"},
                        }
                    },
                    {
                        "properties": {
                            "as_of": {"type": "null"},
                            "time_window": time_window,
                        }
                    },
                ]
            }
        ],
    )


def _v21_factual_output_schema(v21: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        row
        for row in v21["schema_registry"]["rows"]
        if row.get("schema_id") == "methodology-factual-output"
        and row.get("schema_version") == "task7-methodology-factual-output-v1"
    ]
    if len(matches) != 1:
        _error("TASK8_V22_FACTUAL_SCHEMA_BASE_INVALID")
    targets = matches[0]["normalized_schema_snapshot"].get("targets")
    if not isinstance(targets, list):
        _error("TASK8_V22_FACTUAL_SCHEMA_BASE_INVALID")
    target_matches = [
        schema
        for name, schema in targets
        if name == "idis.models.methodology_extraction:MethodologyFactualOutput"
    ]
    if len(target_matches) != 1 or not isinstance(target_matches[0], dict):
        _error("TASK8_V22_FACTUAL_SCHEMA_BASE_INVALID")
    result = copy.deepcopy(target_matches[0])
    # V21 canonicalizes binary64 JSON-schema bounds as tagged bytes so its
    # contract hash never depends on a host JSON float renderer.  The new FDD
    # snapshot must itself be executable Draft 2020-12 JSON Schema.  The only
    # tagged bound in the frozen factual schema is exact positive zero, whose
    # JSON-Schema-native integer spelling is semantically identical.
    percentage_minimum = (
        result.get("$defs", {})
        .get("PercentageValue", {})
        .get("properties", {})
        .get("value", {})
        .get("anyOf", [{}])[0]
        .get("minimum")
    )
    if percentage_minimum != {"float8_be": "0000000000000000"}:
        _error("TASK8_V22_FACTUAL_SCHEMA_FLOAT_BOUND_INVALID")
    result["$defs"]["PercentageValue"]["properties"]["value"]["anyOf"][0]["minimum"] = 0
    return result


def _factual_schema_contract(v21: Mapping[str, Any]) -> dict[str, Any]:
    signed_ratio = _signed_ratio_schema()
    factual_output = _v21_factual_output_schema(v21)
    definitions = factual_output.get("$defs")
    value = factual_output.get("properties", {}).get("value")
    if (
        not isinstance(definitions, dict)
        or "FddSignedRatioValueV1" in definitions
        or not isinstance(value, dict)
        or not isinstance(value.get("oneOf"), list)
        or not isinstance(value.get("discriminator"), dict)
        or not isinstance(value["discriminator"].get("mapping"), dict)
        or "signed_ratio" in value["discriminator"]["mapping"]
    ):
        _error("TASK8_V22_FACTUAL_SCHEMA_BASE_INVALID")
    definitions["FddSignedRatioValueV1"] = copy.deepcopy(signed_ratio)
    value["discriminator"]["mapping"]["signed_ratio"] = "#/$defs/FddSignedRatioValueV1"
    value["oneOf"].append({"$ref": "#/$defs/FddSignedRatioValueV1"})
    factual_output["properties"]["methodology_question_record_id"] = _canonical_uuid()
    factual_output["properties"]["factual_output_id"] = _nullable(_canonical_uuid())
    factual_output["properties"]["accepted_source_span_ids"] = {
        "items": _canonical_uuid(),
        "minItems": 1,
        "type": "array",
        "uniqueItems": True,
    }
    factual_output["properties"]["accepted_locator_sha256s"] = {
        "items": _sha256(),
        "minItems": 1,
        "type": "array",
    }
    for required_field in ("methodology_question_record_id", "factual_output_id"):
        if required_field not in factual_output["required"]:
            factual_output["required"].append(required_field)
    factual_output["x-idis-cross-field-constraint-tuple"] = [
        [
            "accepted-source-locator-cardinality-v1",
            "len(accepted_source_span_ids)=len(accepted_locator_sha256s)",
        ],
        [
            "methodology-factual-output-uuid5-v1",
            "factual_output_id=NULL_OR_UUID5(canonical_factual_output_preimage)",
        ],
        [
            "signed-ratio-period-coordinate-equality-v1",
            "value.as_of=period.date OR "
            "value.time_window.(start_date,end_date)=period.(start_date,end_date)",
        ],
    ]
    date = {"format": "date", "type": "string"}
    factual_output.setdefault("allOf", []).append(
        {
            "if": {
                "properties": {
                    "value": {
                        "properties": {"type": {"const": "signed_ratio"}},
                        "required": ["type"],
                    }
                }
            },
            "then": {
                "properties": {"unit": {"const": "ratio"}},
                "oneOf": [
                    {
                        "properties": {
                            "value": {
                                "properties": {
                                    "as_of": date,
                                    "time_window": {"type": "null"},
                                }
                            },
                            "period": {
                                "properties": {
                                    "kind": {"const": "as_of"},
                                    "date": date,
                                    "start_date": {"type": "null"},
                                    "end_date": {"type": "null"},
                                }
                            },
                        }
                    },
                    {
                        "properties": {
                            "value": {
                                "properties": {
                                    "as_of": {"type": "null"},
                                    "time_window": {"type": "object"},
                                }
                            },
                            "period": {
                                "properties": {
                                    "kind": {"const": "range"},
                                    "date": {"type": "null"},
                                    "start_date": date,
                                    "end_date": date,
                                }
                            },
                        }
                    },
                ],
            },
        }
    )
    snapshot = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "targets": [
            ["FddSignedRatioValueV1", signed_ratio],
            ["FddMethodologyFactualOutputV1", factual_output],
        ],
    }
    return _schema_contract(
        schema_id="fdd-methodology-factual-output",
        schema_version="task8-fdd-methodology-factual-output-v1",
        snapshot=snapshot,
        field_sets=(("FddSignedRatioValueV1", _MODEL_FIELD_SETS[0][1]),),
        legal_cases=(
            [
                "negative-ratio-range",
                {
                    "type": "signed_ratio",
                    "value": "-0.2501",
                    "as_of": None,
                    "time_window": {
                        "label": "FY2025",
                        "start_date": "2025-01-01",
                        "end_date": "2025-12-31",
                    },
                },
            ],
            [
                "positive-ratio-as-of",
                {
                    "type": "signed_ratio",
                    "value": "0.4",
                    "as_of": "2025-12-31",
                    "time_window": None,
                },
            ],
        ),
        illegal_cases=(
            ["binary-float", ["value", 0.25], "TASK8_FDD_BINARY_FLOAT_FORBIDDEN"],
            ["boolean", ["value", True], "TASK8_FDD_DECIMAL_INVALID"],
            ["nan", ["value", "NaN"], "TASK8_FDD_DECIMAL_INVALID"],
            ["infinity", ["value", "Infinity"], "TASK8_FDD_DECIMAL_INVALID"],
            ["negative-zero", ["value", "-0"], "TASK8_FDD_NEGATIVE_ZERO_INVALID"],
            ["both-periods", ["as_of", "time_window"], "TASK8_FDD_PERIOD_INVALID"],
            ["extra-field", ["unexpected"], "TASK8_FDD_SCHEMA_EXTRA_FIELD"],
        ),
    )


def _economic_coordinate_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[2][1]
    date = {"format": "date", "type": "string"}
    return _closed_object(
        fields,
        {
            "period_kind": {"enum": ["as_of", "range"], "type": "string"},
            "period_start": _nullable(date),
            "period_end": _nullable(date),
            "as_of_date": _nullable(date),
            "base_currency": _currency(),
            "monetary_scale_unit": _nonblank_string(),
        },
        conditions=[
            {
                "oneOf": [
                    {
                        "properties": {
                            "period_kind": {"const": "as_of"},
                            "period_start": {"type": "null"},
                            "period_end": {"type": "null"},
                            "as_of_date": date,
                        }
                    },
                    {
                        "properties": {
                            "period_kind": {"const": "range"},
                            "period_start": date,
                            "period_end": date,
                            "as_of_date": {"type": "null"},
                        }
                    },
                ]
            }
        ],
    )


def _lineage_id_array() -> dict[str, Any]:
    return {
        "items": _canonical_uuid(),
        "minItems": 1,
        "type": "array",
        "uniqueItems": True,
    }


def _numeric_binding_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[3][1]
    schema = _closed_object(
        fields,
        {
            "binding_kind": {"const": "NUMERIC", "type": "string"},
            "ordinal": {"minimum": 0, "type": "integer"},
            "role": _nonblank_string(),
            "source_decimal": _canonical_decimal(),
            "normalized_decimal": _canonical_decimal(),
            "sign_transform": {
                "enum": ["POSITIVE_MAGNITUDE", "DIRECT_SIGNED"],
                "type": "string",
            },
            "source_sheet": _nonblank_string(),
            "source_row": {"minimum": 1, "type": "integer"},
            "predicate": _nonblank_string(),
            "coverage_record_id": {
                "pattern": _COVERAGE_RECORD_ID_PATTERN,
                "type": "string",
            },
            "methodology_version_record_id": _canonical_uuid(),
            "methodology_question_record_id": _canonical_uuid(),
            "factual_output_record_id": _canonical_uuid(),
            "claim_id": _canonical_uuid(),
            "evidence_ids": _lineage_id_array(),
            "sanad_ids": _lineage_id_array(),
            "period": {"$ref": "#/$defs/FddMethodologyPeriodV1"},
            "currency": _nullable(_currency()),
            "unit": _nonblank_string(),
            "value_sha256": _sha256(),
        },
        conditions=[
            {
                "if": {
                    "properties": {
                        "sign_transform": {
                            "const": "POSITIVE_MAGNITUDE",
                            "type": "string",
                        }
                    },
                    "required": ["sign_transform"],
                },
                "then": {
                    "properties": {
                        "source_decimal": {
                            "pattern": _NONNEGATIVE_CANONICAL_DECIMAL_PATTERN,
                            "type": "string",
                        },
                        "normalized_decimal": {
                            "pattern": _NONNEGATIVE_CANONICAL_DECIMAL_PATTERN,
                            "type": "string",
                        },
                    }
                },
            }
        ],
    )
    schema["x-idis-cross-field-constraint-tuple"] = [
        [
            "numeric-binding-source-normalization-v1",
            "normalized_decimal=source_decimal",
        ],
        [
            "positive-magnitude-source-nonnegative-v1",
            (
                "sign_transform='POSITIVE_MAGNITUDE' => "
                "decimal(source_decimal)>=0 AND decimal(normalized_decimal)>=0"
            ),
        ],
    ]
    return schema


def _set_completeness_binding_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[4][1]
    return _closed_object(
        fields,
        {
            "binding_kind": {"const": "SET_COMPLETENESS", "type": "string"},
            "ordinal": {"minimum": 0, "type": "integer"},
            "role": _nonblank_string(),
            "confirmed": {"const": True, "type": "boolean"},
            "source_sheet": _nonblank_string(),
            "source_row": {"minimum": 1, "type": "integer"},
            "predicate": _nonblank_string(),
            "coverage_record_id": {
                "pattern": _COVERAGE_RECORD_ID_PATTERN,
                "type": "string",
            },
            "methodology_version_record_id": _canonical_uuid(),
            "methodology_question_record_id": _canonical_uuid(),
            "factual_output_record_id": _canonical_uuid(),
            "claim_id": _canonical_uuid(),
            "evidence_ids": _lineage_id_array(),
            "sanad_ids": _lineage_id_array(),
            "period": {"$ref": "#/$defs/FddMethodologyPeriodV1"},
            "value_sha256": _sha256(),
        },
    )


def _parent_calculation_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[5][1]
    return _closed_object(
        fields,
        {
            "ordinal": {"minimum": 0, "type": "integer"},
            "calc_type": {"$ref": "#/$defs/FddCalcTypeV1"},
            "calc_id": _canonical_uuid(),
            "economic_coordinate_sha256": _sha256(),
            "formula_sha256": _sha256(),
            "reproducibility_sha256": _sha256(),
        },
    )


def _assumption_schema() -> dict[str, Any]:
    return _closed_object(
        _MODEL_FIELD_SETS[6][1],
        {"assumption_id": _nonblank_string(), "value": _canonical_decimal()},
    )


def _secondary_output_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[8][1]
    schema = _closed_object(
        fields,
        {
            "name": _nonblank_string(),
            "decimal": _canonical_decimal(),
            "scale": {"enum": [2, 4], "type": "integer"},
            "unit": _nonblank_string(),
            "currency": _nullable(_currency()),
        },
    )
    schema["oneOf"] = [
        {
            "properties": {
                "scale": {"const": 4},
                "unit": {"const": "ratio"},
                "currency": {"type": "null"},
            }
        },
        {
            "properties": {
                "scale": {"const": 2},
                "unit": {"not": {"const": "ratio"}},
                "currency": _currency(),
            }
        },
    ]
    return schema


def _calc_output_schema() -> dict[str, Any]:
    fields = _MODEL_FIELD_SETS[7][1]
    schema = _closed_object(
        fields,
        {
            "primary_name": _nonblank_string(),
            "primary_decimal": _canonical_decimal(),
            "primary_scale": {"enum": [2, 4], "type": "integer"},
            "secondary_values": {
                "items": {"$ref": "#/$defs/FddSecondaryOutputV1"},
                "type": "array",
            },
            "unit": _nonblank_string(),
            "currency": _nullable(_currency()),
            "formula_disposition": {
                "enum": ["CALCULATED", "IN_BALANCE", "OUT_OF_BALANCE"],
                "type": "string",
            },
        },
    )
    schema["oneOf"] = [
        {
            "properties": {
                "primary_scale": {"const": 4},
                "unit": {"const": "ratio"},
                "currency": {"type": "null"},
            }
        },
        {
            "properties": {
                "primary_scale": {"const": 2},
                "unit": {"not": {"const": "ratio"}},
                "currency": _currency(),
            }
        },
    ]
    return schema


def _formula_schema_contract() -> dict[str, Any]:
    field_sets = _MODEL_FIELD_SETS[1:]
    definitions: dict[str, Any] = {
        "FddCalcTypeV1": {"enum": list(_FDD_CALC_TYPES), "type": "string"},
        "FddMethodologyPeriodV1": _fdd_period_schema(),
        "FddEconomicCoordinateV1": _economic_coordinate_schema(),
        "FddNumericBindingV1": _numeric_binding_schema(),
        "FddSetCompletenessBindingV1": _set_completeness_binding_schema(),
        "FddParentCalculationV1": _parent_calculation_schema(),
        "FddGovernedAssumptionV1": _assumption_schema(),
        "FddSecondaryOutputV1": _secondary_output_schema(),
        "FddCalcOutputV1": _calc_output_schema(),
    }
    definitions["FddCalcInputsV1"] = _closed_object(
        _MODEL_FIELD_SETS[1][1],
        {
            "schema_version": {"const": "fdd-calc-inputs-v1", "type": "string"},
            "formula_policy_id": {"const": _POLICY_ID, "type": "string"},
            "formula_policy_sha256": {"const": _POLICY_SHA256, "type": "string"},
            "formula_sha256": _sha256(),
            "coordinate": {"$ref": "#/$defs/FddEconomicCoordinateV1"},
            "bindings": {
                "items": {
                    "discriminator": {
                        "mapping": {
                            "NUMERIC": "#/$defs/FddNumericBindingV1",
                            "SET_COMPLETENESS": "#/$defs/FddSetCompletenessBindingV1",
                        },
                        "propertyName": "binding_kind",
                    },
                    "oneOf": [
                        {"$ref": "#/$defs/FddNumericBindingV1"},
                        {"$ref": "#/$defs/FddSetCompletenessBindingV1"},
                    ],
                },
                "minItems": 1,
                "type": "array",
            },
            "parent_calculations": {
                "items": {"$ref": "#/$defs/FddParentCalculationV1"},
                "type": "array",
            },
            "assumptions": {
                "items": {"$ref": "#/$defs/FddGovernedAssumptionV1"},
                "maxItems": 0,
                "type": "array",
            },
        },
    )
    definitions["FddCalcInputsV1"]["x-idis-cross-field-constraint-tuple"] = [
        ["binding-ordinal-total-order-v1", "bindings.ordinal=0..N-1"],
        [
            "parent-calculation-ordinal-total-order-v1",
            "parent_calculations.ordinal=0..N-1",
        ],
        [
            "economic-coordinate-date-order-v1",
            "period_start<period_end AND as_of_date XOR (period_start,period_end)",
        ],
    ]
    calculation = _closed_object(
        ("calc_type", "inputs", "output"),
        {
            "calc_type": {"$ref": "#/$defs/FddCalcTypeV1"},
            "inputs": {"$ref": "#/$defs/FddCalcInputsV1"},
            "output": {"$ref": "#/$defs/FddCalcOutputV1"},
        },
        conditions=[
            {
                "if": {"properties": {"calc_type": {"const": "FDD_ASSET_TIE_OUT"}}},
                "then": {
                    "properties": {
                        "output": {"properties": {"formula_disposition": {"const": "CALCULATED"}}}
                    }
                },
            }
        ],
    )
    definitions["FddDeterministicCalculationV1"] = calculation
    snapshot = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        "targets": [
            ["FddDeterministicCalculationV1", calculation],
            ["FddCalcInputsV1", definitions["FddCalcInputsV1"]],
            ["FddCalcOutputV1", definitions["FddCalcOutputV1"]],
        ],
    }
    return _schema_contract(
        schema_id="fdd-deterministic-calculation",
        schema_version="task8-fdd-deterministic-calculation-v1",
        snapshot=snapshot,
        field_sets=field_sets,
        legal_cases=(
            [
                "out-of-balance",
                [
                    "FDD_DEBT_ROLL_FORWARD",
                    "OUT_OF_BALANCE",
                    "DIRECT_SIGNED",
                    "EMPTY_ASSUMPTION_TUPLE",
                ],
            ],
            ["negative-gross-margin", ["GROSS_MARGIN", "-0.2501", "ratio", 4]],
            ["complete-empty-component-set", ["confirmed", True, "component_count", 0]],
        ),
        illegal_cases=(
            ["legacy-alias", ["calc_type", "CUSTOM"], "TASK8_FDD_CALC_TYPE_INVALID"],
            ["false-completeness", ["confirmed", False], "TASK8_FDD_SET_INCOMPLETE"],
            ["default-zero", ["missing_binding", "0"], "TASK8_FDD_DEFAULT_ZERO_FORBIDDEN"],
            ["unknown-field", ["extension"], "TASK8_FDD_SCHEMA_EXTRA_FIELD"],
            ["nan", ["primary_decimal", "NaN"], "TASK8_FDD_DECIMAL_INVALID"],
            ["negative-zero", ["primary_decimal", "-0"], "TASK8_FDD_NEGATIVE_ZERO_INVALID"],
        ),
    )


def _schema_contract(
    *,
    schema_id: str,
    schema_version: str,
    snapshot: dict[str, Any],
    field_sets: tuple[tuple[str, tuple[str, ...]], ...],
    legal_cases: tuple[list[Any], ...],
    illegal_cases: tuple[list[Any], ...],
) -> dict[str, Any]:
    authority_locator = f"task8-literal:{schema_id}:{schema_version}"
    collection_paths = [["$", schema_id, schema_version]]
    expected = _h(
        [
            "typed-schema-definition-v1",
            schema_id,
            schema_version,
            authority_locator,
            snapshot,
            collection_paths,
        ]
    )
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "normalized_schema_snapshot": snapshot,
        "model_field_set_tuple": [[model, list(fields)] for model, fields in field_sets],
        "legal_case_tuple": list(legal_cases),
        "illegal_case_tuple": list(illegal_cases),
        "expected_schema_sha256": expected,
    }


def _validate_executable_schema_contracts(
    contracts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    for contract in contracts:
        snapshot = contract.get("normalized_schema_snapshot")
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("targets"), list):
            _error("TASK8_V22_SCHEMA_SNAPSHOT_INVALID")
        definitions = snapshot.get("$defs", {})
        for target in snapshot["targets"]:
            if (
                not isinstance(target, list)
                or len(target) != 2
                or not isinstance(target[0], str)
                or not isinstance(target[1], dict)
            ):
                _error("TASK8_V22_SCHEMA_SNAPSHOT_INVALID")
            schema = copy.deepcopy(target[1])
            schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            schema.setdefault("$defs", copy.deepcopy(definitions))
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise MaterializationError("TASK8_V22_SCHEMA_SNAPSHOT_INVALID") from exc


def _schema_registry_row(contract: Mapping[str, Any]) -> dict[str, Any]:
    schema_id = str(contract["schema_id"])
    schema_version = str(contract["schema_version"])
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "authority_locator": f"task8-literal:{schema_id}:{schema_version}",
        "normalized_schema_snapshot": copy.deepcopy(contract["normalized_schema_snapshot"]),
        "collection_paths": [["$", schema_id, schema_version]],
        "origin": "TASK8_APPEND_FROZEN_LITERAL",
        "expected_schema_sha256": contract["expected_schema_sha256"],
    }


def _sql_row_hash(row: Mapping[str, Any]) -> str:
    return _h(
        [
            "full-strict-v22-sql-authority-row-v1",
            {key: value for key, value in row.items() if key != "expected_sha256"},
        ]
    )


def _encoded(statements: tuple[str, ...]) -> list[str]:
    if any(statement != " ".join(statement.split()) for statement in statements):
        _error("TASK8_V22_SQL_NOT_CANONICAL")
    return [statement.encode("utf-8").hex() for statement in statements]


def _dashboard_mixed_result_guard() -> str:
    return (
        "IF dashboard_row.row_disposition IN ('ANSWERED','RED_FLAG') AND "
        "(NOT EXISTS (SELECT 1 FROM public.methodology_extraction_tasks t "
        "JOIN public.methodology_extraction_results r ON "
        "r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id "
        "AND r.task_record_id=t.task_record_id WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id "
        "AND t.coverage_record_id=dashboard_row.coverage_record_id "
        "AND r.outcome='EVIDENCE_FOUND') OR EXISTS (SELECT 1 FROM "
        "public.methodology_extraction_tasks t LEFT JOIN "
        "public.methodology_extraction_results r ON r.tenant_id=t.tenant_id "
        "AND r.deal_id=t.deal_id AND r.run_id=t.run_id "
        "AND r.task_record_id=t.task_record_id WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id "
        "AND t.coverage_record_id=dashboard_row.coverage_record_id AND "
        "(r.result_id IS NULL OR r.outcome NOT IN "
        "('EVIDENCE_FOUND','EVIDENCE_NOT_FOUND') OR "
        "r.result_payload='{}'::jsonb OR (r.outcome='EVIDENCE_FOUND' AND "
        "(NOT EXISTS (SELECT 1 FROM public.methodology_result_factual_outputs f "
        "WHERE f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id "
        "AND f.run_id=r.run_id AND f.result_id=r.result_id) OR EXISTS "
        "(SELECT 1 FROM public.methodology_result_factual_outputs f WHERE "
        "f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id AND f.run_id=r.run_id "
        "AND f.result_id=r.result_id AND (NOT EXISTS (SELECT 1 FROM "
        "public.methodology_result_evidence_links e WHERE "
        "e.tenant_id=f.tenant_id AND e.deal_id=f.deal_id AND e.run_id=f.run_id "
        "AND e.result_id=f.result_id AND "
        "e.factual_output_record_id=f.factual_output_record_id) OR NOT EXISTS "
        "(SELECT 1 FROM public.methodology_result_sanad_links s WHERE "
        "s.tenant_id=f.tenant_id AND s.deal_id=f.deal_id AND s.run_id=f.run_id "
        "AND s.result_id=f.result_id AND "
        "s.factual_output_record_id=f.factual_output_record_id))))) OR "
        "(r.outcome='EVIDENCE_NOT_FOUND' AND (r.gap_finding IS NULL OR "
        "r.gap_finding='{}'::jsonb OR cardinality(r.searched_document_ids)<=0 "
        "OR cardinality(r.searched_span_ids)<=0 OR "
        "r.searched_scope_sha256!~'^[0-9a-f]{64}$' OR "
        "r.provider_provenance_sha256!~'^[0-9a-f]{64}$' OR EXISTS "
        "(SELECT 1 FROM public.methodology_result_factual_outputs f WHERE "
        "f.tenant_id=r.tenant_id AND f.deal_id=r.deal_id AND f.run_id=r.run_id "
        "AND f.result_id=r.result_id)))))) THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF;"
    )


def _dashboard_task_result_census_guard() -> str:
    return (
        "IF NOT EXISTS (SELECT 1 FROM public.methodology_extraction_tasks t "
        "WHERE t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id "
        "AND t.coverage_record_id=dashboard_row.coverage_record_id) OR EXISTS "
        "(SELECT 1 FROM public.methodology_extraction_tasks t WHERE "
        "t.tenant_id=dashboard_row.tenant_id AND "
        "t.deal_id=dashboard_row.deal_id AND t.run_id=dashboard_row.run_id "
        "AND t.coverage_record_id=dashboard_row.coverage_record_id AND "
        "(SELECT count(*) FROM public.methodology_extraction_results r WHERE "
        "r.tenant_id=t.tenant_id AND r.deal_id=t.deal_id AND r.run_id=t.run_id "
        "AND r.task_record_id=t.task_record_id AND r.outcome IN "
        "('EVIDENCE_FOUND','EVIDENCE_NOT_FOUND'))<>1) THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF; "
    )


def _dashboard_scope_census_guard() -> str:
    return (
        "IF EXISTS (WITH dashboard_scopes AS (SELECT scope_tenant AS tenant_id,"
        "scope_deal AS deal_id,scope_run AS run_id),incomplete_scopes AS "
        "(SELECT 1 FROM dashboard_scopes s WHERE (SELECT count(*) FROM "
        "public.methodology_coverage_records c WHERE c.tenant_id=s.tenant_id "
        "AND c.deal_id=s.deal_id AND c.run_id=s.run_id)<>100 OR "
        "(SELECT count(*) FROM public.methodology_truth_dashboard_rows d WHERE "
        "d.tenant_id=s.tenant_id AND d.deal_id=s.deal_id AND "
        "d.run_id=s.run_id)<>100 OR EXISTS ((SELECT c.coverage_record_id FROM "
        "public.methodology_coverage_records c WHERE c.tenant_id=s.tenant_id "
        "AND c.deal_id=s.deal_id AND c.run_id=s.run_id EXCEPT SELECT "
        "d.coverage_record_id FROM public.methodology_truth_dashboard_rows d "
        "WHERE d.tenant_id=s.tenant_id AND d.deal_id=s.deal_id AND "
        "d.run_id=s.run_id) UNION ALL (SELECT d.coverage_record_id FROM "
        "public.methodology_truth_dashboard_rows d WHERE "
        "d.tenant_id=s.tenant_id AND d.deal_id=s.deal_id AND "
        "d.run_id=s.run_id EXCEPT SELECT c.coverage_record_id FROM "
        "public.methodology_coverage_records c WHERE c.tenant_id=s.tenant_id "
        "AND c.deal_id=s.deal_id AND c.run_id=s.run_id))) SELECT 1 FROM "
        "incomplete_scopes) THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF; "
    )


def _dashboard_exact_calculation_guard() -> str:
    return (
        "IF EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "(item->>'required')::boolean AND (SELECT count(*) FROM "
        "public.deterministic_calculations c JOIN public.run_output_objects o "
        "ON o.tenant_id=c.tenant_id AND o.deal_id=c.deal_id AND "
        "o.run_id=c.run_id AND o.output_object_id=c.run_output_object_id "
        "AND o.output_class=c.run_output_object_class AND "
        "o.output_kind=c.run_output_object_kind WHERE "
        "c.tenant_id=dashboard_row.tenant_id AND "
        "c.deal_id=dashboard_row.deal_id AND c.run_id=dashboard_row.run_id "
        "AND c.coverage_record_id=dashboard_row.coverage_record_id AND "
        "c.methodology_question_record_id="
        "dashboard_row.methodology_question_record_id AND "
        "c.producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' AND "
        "item->>'calc_type'=c.calc_type AND "
        "o.output_schema_version='task8-fdd-deterministic-calculation-v1')<>1) "
        "OR EXISTS (SELECT 1 FROM public.deterministic_calculations c JOIN "
        "public.run_output_objects o ON o.tenant_id=c.tenant_id AND "
        "o.deal_id=c.deal_id AND o.run_id=c.run_id AND "
        "o.output_object_id=c.run_output_object_id AND "
        "o.output_class=c.run_output_object_class AND "
        "o.output_kind=c.run_output_object_kind WHERE "
        "c.tenant_id=dashboard_row.tenant_id AND "
        "c.deal_id=dashboard_row.deal_id AND c.run_id=dashboard_row.run_id "
        "AND c.coverage_record_id=dashboard_row.coverage_record_id AND "
        "c.methodology_question_record_id="
        "dashboard_row.methodology_question_record_id AND "
        "c.producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' AND "
        "(o.output_schema_version<>"
        "'task8-fdd-deterministic-calculation-v1' OR NOT EXISTS (SELECT 1 "
        "FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "(item->>'required')::boolean AND item->>'calc_type'=c.calc_type))) "
        "THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF;"
    )


def _dashboard_authority(
    base: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if base.get("authority_id") != "dashboard-root-equation-v1":
        _error("TASK8_V22_DASHBOARD_AUTHORITY_BASE_INVALID")
    try:
        base_statements = [
            bytes.fromhex(str(value)).decode("utf-8") for value in base["body_sql_utf8_hex_tuple"]
        ]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V22_DASHBOARD_AUTHORITY_BASE_INVALID") from exc
    if len(base_statements) != 4 or not base_statements[0].startswith(
        "CREATE FUNCTION public.idis_task7_dashboard_root_equation_v1()"
    ):
        _error("TASK8_V22_DASHBOARD_AUTHORITY_BASE_INVALID")

    function = base_statements[0].replace(
        "public.idis_task7_dashboard_root_equation_v1",
        "public.idis_task8_dashboard_root_equation_v2",
    )
    if function.count("DECLARE dashboard_row RECORD;") != 1:
        _error("TASK8_V22_DASHBOARD_LOCK_BASE_INVALID")
    function = function.replace(
        "DECLARE dashboard_row RECORD;",
        "DECLARE scope_tenant UUID; scope_deal UUID; scope_run UUID; dashboard_row RECORD;",
    )
    lock_insertion = "BEGIN IF EXISTS"
    if function.count(lock_insertion) != 1:
        _error("TASK8_V22_DASHBOARD_LOCK_BASE_INVALID")
    function = function.replace(
        lock_insertion,
        "BEGIN IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; "
        "scope_deal:=OLD.deal_id; scope_run:=OLD.run_id; ELSE "
        "scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; "
        "scope_run:=NEW.run_id; END IF; "
        f"{_DASHBOARD_LOCK_SQL} {_dashboard_scope_census_guard()}IF EXISTS",
    )
    inherited_projection = (
        "FOR dashboard_row IN SELECT d.*,q.question_payload,q.methodology_question_record_id FROM"
    )
    exact_projection = (
        "FOR dashboard_row IN SELECT d.tenant_id,d.deal_id,d.run_id,"
        "d.coverage_record_id,d.dashboard_row_id,d.run_output_object_id,"
        "d.root_projection_sha256,d.row_disposition,q.question_payload,"
        "q.methodology_question_record_id FROM"
    )
    if function.count(inherited_projection) != 1:
        _error("TASK8_V22_DASHBOARD_PROJECTION_BASE_INVALID")
    function = function.replace(inherited_projection, exact_projection)
    validation_marker = (
        "IF EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE"
    )
    if function.count(validation_marker) != 1:
        _error("TASK8_V22_DASHBOARD_CALCULATION_GUARD_BASE_INVALID")
    function = function.replace(
        validation_marker,
        _dashboard_task_result_census_guard() + validation_marker,
    )
    old_calculation_start = (
        "IF EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "(item->>'required')::boolean AND NOT EXISTS (SELECT 1 FROM "
        "public.deterministic_calculations c"
    )
    calculation_start = function.find(old_calculation_start)
    if calculation_start < 0:
        _error("TASK8_V22_DASHBOARD_CALCULATION_GUARD_BASE_INVALID")
    calculation_end_marker = (
        "THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF;"
    )
    calculation_end = function.find(calculation_end_marker, calculation_start)
    if calculation_end < 0:
        _error("TASK8_V22_DASHBOARD_CALCULATION_GUARD_BASE_INVALID")
    calculation_end += len(calculation_end_marker)
    function = (
        function[:calculation_start]
        + _dashboard_exact_calculation_guard()
        + function[calculation_end:]
    )
    calculation_root_marker = "AND c.run_output_object_kind='METHODOLOGY_CALCULATION'"
    if function.count(calculation_root_marker) != 1:
        _error("TASK8_V22_DASHBOARD_CALCULATION_ROOT_BASE_INVALID")
    function = function.replace(
        calculation_root_marker,
        calculation_root_marker + " AND o.output_schema_version="
        "'task8-fdd-deterministic-calculation-v1'",
    )
    old_start = "IF dashboard_row.row_disposition IN ('ANSWERED','RED_FLAG') AND ("
    old_end = (
        "THEN RAISE EXCEPTION "
        "'TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION' "
        "USING ERRCODE='23514'; END IF;"
    )
    start = function.find(old_start)
    end = function.find(old_end, start)
    if start < 0 or end < 0:
        _error("TASK8_V22_DASHBOARD_MIXED_RESULT_BASE_INVALID")
    end += len(old_end)
    function = function[:start] + _dashboard_mixed_result_guard() + function[end:]
    statements = (
        "DROP FUNCTION IF EXISTS public.idis_task8_dashboard_root_equation_v2() CASCADE",
        function,
        *(
            statement.replace(
                "public.idis_task7_dashboard_root_equation_v1",
                "public.idis_task8_dashboard_root_equation_v2",
            )
            for statement in base_statements[1:]
        ),
    )
    if any(statement != " ".join(statement.split()) for statement in statements):
        _error("TASK8_V22_SQL_NOT_CANONICAL")
    function_body = function.partition(" AS $$")[2].rsplit("$$", 1)[0]
    if not function_body:
        _error("TASK8_V22_DASHBOARD_FUNCTION_BODY_INVALID")
    body_sha256 = hashlib.sha256(function_body.encode("utf-8")).hexdigest()
    row: dict[str, Any] = {
        "authority_ordinal": 0,
        "authority_id": "dashboard-root-equation-v2",
        "authority_version": "2",
        "authority_kind": "DEFERRED_CONSTRAINT_TRIGGER",
        "target_tuple": [
            ["methodology_truth_dashboard_rows", "ctrg_v2_dashboard_root_equation"],
            [
                "methodology_truth_dashboard_root_links",
                "ctrg_v3_dashboard_root_equation",
            ],
        ],
        "declared_parent_relation_tuple": copy.deepcopy(base["declared_parent_relation_tuple"]),
        "body_sql_utf8_hex_tuple": _encoded(statements),
        "legal_red_case_tuple": [
            "ALL_NOT_FOUND_GAP",
            "ALL_FOUND_ANSWERED",
            "MIXED_FOUND_NOT_FOUND_ANSWERED",
            "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG",
        ],
        "illegal_red_case_tuple": [
            "RESULT_MISSING",
            "RESULT_NONTERMINAL",
            "COVERAGE_CENSUS_NOT_100",
            "DASHBOARD_ROW_CENSUS_NOT_100",
            "COVERAGE_SET_MISSING",
            "COVERAGE_SET_EXTRA",
            "ROOT_MISSING",
            "ROOT_DUPLICATE",
            "REQUIRED_FORMULA_MISSING",
            "REQUIRED_FORMULA_TYPE_MISMATCH",
            "UNDECLARED_FORMULA",
            "ROOT_PROJECTION_HASH_MISMATCH",
            "GAP_WITH_EVIDENCE",
            "ANSWERED_WITH_ADVERSE",
            "RED_FLAG_WITHOUT_ADVERSE",
        ],
        "direct_sql_red_case_tuple": [
            "DIRECT_PARENT_INSERT",
            "DIRECT_CHILD_INSERT",
            "DIRECT_CHILD_UPDATE",
            "DIRECT_CHILD_DELETE",
        ],
    }
    row["expected_sha256"] = _sql_row_hash(row)
    return row, body_sha256


def _partial_exact_one_authority(
    ordinal: int,
    current: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, tuple[str, ...]]:
    if (
        current.get("authority_id") != "current-output-exact-one-v1"
        or current.get("expected_sha256") != _CURRENT_OUTPUT_EXACT_ONE_V1_SHA256
    ):
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    try:
        base_statements = tuple(
            bytes.fromhex(str(value)).decode("utf-8")
            for value in current["body_sql_utf8_hex_tuple"]
        )
        current_targets = copy.deepcopy(current["target_tuple"])
        current_parents = copy.deepcopy(current["declared_parent_relation_tuple"])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID") from exc
    if len(base_statements) != 26 or len(current_targets) != 18:
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    helper = base_statements[2]
    function = base_statements[6]
    if (
        not helper.startswith("CREATE FUNCTION public.idis_task7_current_native_hashes_v1(")
        or not function.startswith(
            "CREATE FUNCTION public.idis_task7_current_output_exact_one_v1()"
        )
        or function.count("STEP_OUTPUT_MISSING") != 2
        or "public.methodology_truth_dashboard_rows" in helper
        or "public.methodology_truth_dashboard_rows" in function
    ):
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    expected_trigger_statements = tuple(
        "CREATE CONSTRAINT TRIGGER "
        f"{trigger} AFTER INSERT OR UPDATE OR DELETE ON public.{table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        f"{_CURRENT_OUTPUT_EXACT_ONE_SIGNATURE}"
        for table, trigger in current_targets
    )
    if base_statements[7:25] != expected_trigger_statements:
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")

    helper_marker = "),resolved AS ("
    if helper.count(helper_marker) != 1:
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    helper_extension = (
        " UNION ALL SELECT d.run_output_object_kind,"
        "pg_catalog.jsonb_build_array(d.dashboard_row_id::text),"
        "pg_catalog.jsonb_build_array(d.coverage_record_id,"
        "d.root_projection_sha256,d.row_disposition,d.row_payload_sha256) "
        "FROM public.methodology_truth_dashboard_rows d WHERE "
        "d.tenant_id=p_tenant_id AND d.deal_id=p_deal_id AND "
        "d.run_id=p_run_id AND d.run_output_object_id=p_output_object_id"
    )
    extended_helper = helper.replace(
        helper_marker,
        helper_extension + helper_marker,
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)

    function_marker = (
        " FROM public.deliverables WHERE run_output_object_id IS NOT NULL), scoped_natives AS"
    )
    if function.count(function_marker) != 1:
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    function_extension = (
        " FROM public.deliverables WHERE run_output_object_id IS NOT NULL "
        "UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,"
        "run_output_object_kind FROM public.methodology_truth_dashboard_rows "
        "WHERE run_output_object_id IS NOT NULL), scoped_natives AS"
    )
    extended_function = function.replace(
        function_marker,
        function_extension,
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    dashboard_trigger = (
        "CREATE CONSTRAINT TRIGGER ctrg_task8_partial_native_root_dashboard_rows "
        "AFTER INSERT OR UPDATE OR DELETE ON public.methodology_truth_dashboard_rows "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        f"{_CURRENT_OUTPUT_EXACT_ONE_SIGNATURE}"
    )
    statements = (extended_helper, extended_function, dashboard_trigger)
    dashboard_target = [
        "methodology_truth_dashboard_rows",
        "ctrg_task8_partial_native_root_dashboard_rows",
    ]
    row: dict[str, Any] = {
        "authority_ordinal": ordinal,
        "authority_id": _PARTIAL_EXACT_ONE_AUTHORITY_ID,
        "authority_version": "1",
        "authority_kind": "DEFERRED_CONSTRAINT_TRIGGER",
        "target_tuple": [*current_targets, dashboard_target],
        "declared_parent_relation_tuple": [
            *current_parents,
            "methodology_truth_dashboard_rows",
        ],
        "body_sql_utf8_hex_tuple": _encoded(statements),
        "legal_red_case_tuple": [
            "EXACT_DASHBOARD_PARENT_CHILD",
            "EVERY_LEGACY_CURRENT_NATIVE_BRANCH_UNCHANGED",
        ],
        "illegal_red_case_tuple": [
            "DASHBOARD_PARENT_WITH_ZERO_CHILD",
            "DASHBOARD_PARENT_WITH_DUPLICATE_CHILD",
            "DASHBOARD_CHILD_WITH_WRONG_PARENT_KIND",
            "DASHBOARD_NATIVE_IDENTITY_HASH_MISMATCH",
            "DASHBOARD_NATIVE_OUTPUT_HASH_MISMATCH",
        ],
        "direct_sql_red_case_tuple": [
            "DIRECT_PARENT_INSERT",
            "DIRECT_CHILD_INSERT",
            "DIRECT_PARENT_UPDATE",
            "DIRECT_CHILD_DELETE",
        ],
    }
    row["expected_sha256"] = _sql_row_hash(row)
    helper_body = extended_helper.partition(" AS $$")[2].rsplit("$$", 1)[0]
    function_body = extended_function.partition(" AS $$")[2].rsplit("$$", 1)[0]
    if not helper_body or not function_body:
        _error("TASK8_V22_PARTIAL_FUNCTION_BODY_INVALID")
    downgrade = (
        "DROP TRIGGER ctrg_task8_partial_native_root_dashboard_rows "
        "ON public.methodology_truth_dashboard_rows",
        helper.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
        function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    )
    return (
        row,
        hashlib.sha256(helper_body.encode("utf-8")).hexdigest(),
        hashlib.sha256(function_body.encode("utf-8")).hexdigest(),
        downgrade,
    )


def _relation_hash(domain: str, row: Mapping[str, Any], hash_field: str) -> str:
    return _h([domain, {key: value for key, value in row.items() if key != hash_field}])


def _transform_metadata(artifact: dict[str, Any], policy_sha256: str) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK8_V22_REVIEWED_DESIGN_INVALID") from exc
    if not isinstance(design, list) or design[1] != ["pipeline_contract_version", _V21]:
        _error("TASK8_V22_REVIEWED_DESIGN_INVALID")
    design[0] = "full-strict-v22-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", _V22]
    design.append(
        [
            "task8_fdd_successor",
            [
                "policy_id",
                _POLICY_ID,
                "policy_sha256",
                policy_sha256,
                "schema_count_delta",
                2,
                "installed_rls_delta",
                [67, 69],
                "activation_status",
                "INACTIVE",
            ],
        ]
    )
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = _V22_ARTIFACT_SCHEMA
    metadata["pipeline_contract_version"] = _V22
    metadata["append_frozen"] = False
    metadata["reviewed_design_sha256"] = hashlib.sha256(rendered).hexdigest()
    metadata["reviewed_design_utf8_hex"] = rendered.hex()
    metadata["activation_status"] = "INACTIVE"


def _transform_tuple_registry(artifact: dict[str, Any]) -> None:
    artifact["tuple_schema_registry"]["rows"].append(
        [
            "Task8FddSuccessorContractV1",
            [
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
            ],
        ]
    )


def _transform_digest_recipe(artifact: dict[str, Any]) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    rows[5] = [
        "pipeline_contract_hash",
        '["pipeline-contract-v22","full-strict-v22",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    rows.extend(
        [
            [
                "fdd_formula_policy_v1_sha256",
                'H(["fdd-formula-policy-v1",ORDERED[component_hashes]])',
            ],
            [
                "task8_fdd_successor_contract_sha256",
                'H(["full-strict-v22-task8-fdd-contract-v1",contract])',
            ],
        ]
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _transform_schema_registry(
    artifact: dict[str, Any],
    contracts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    section = artifact["schema_registry"]
    if len(section["rows"]) != 66:
        _error("TASK8_V22_SCHEMA_BASE_INVALID")
    section["rows"].extend(_schema_registry_row(contract) for contract in contracts)
    section["expected_sha256"] = _h(["full-strict-v22-schema-registry-v1", section["rows"]])


def _transform_dashboard_relations(artifact: dict[str, Any]) -> None:
    native = artifact["future_native_authority_registry"]
    dashboard = native["rows"][0]
    if dashboard["planned_table"] != "methodology_truth_dashboard_rows":
        _error("TASK8_V22_DASHBOARD_RELATION_BASE_INVALID")
    dashboard["sql_authority_id_tuple"] = [
        "dashboard-root-equation-v2",
        "approved-erasure-append-only-v1",
        "native-root-exact-one-v1",
        "dashboard-partial-native-root-exact-one-v1",
    ]
    dashboard["schema_sha256"] = _relation_hash(
        "full-strict-v22-future-native-authority-row-v1",
        dashboard,
        "schema_sha256",
    )
    native["expected_sha256"] = _h(
        ["full-strict-v22-future-native-authority-registry-v1", native["rows"]]
    )

    children = artifact["future_child_authority_registry"]
    links = children["rows"][-1]
    if links["table_name"] != "methodology_truth_dashboard_root_links":
        _error("TASK8_V22_DASHBOARD_RELATION_BASE_INVALID")
    links["projection_guard"] = (
        "CREATE CONSTRAINT TRIGGER ctrg_v3_dashboard_root_equation "
        "AFTER INSERT OR UPDATE OR DELETE ON "
        "public.methodology_truth_dashboard_root_links DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION public.idis_task8_dashboard_root_equation_v2()"
    )
    links["sql_authority_id_tuple"] = [
        "dashboard-root-equation-v2",
        "approved-erasure-append-only-v1",
    ]
    links["schema_sha256"] = _relation_hash(
        "full-strict-v22-future-child-authority-row-v1",
        links,
        "schema_sha256",
    )
    children["expected_sha256"] = _h(
        ["full-strict-v22-future-child-authority-registry-v1", children["rows"]]
    )


def _rehash_acl_function_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _rehash_acl_registry(registry: dict[str, Any]) -> None:
    registry["expected_sha256"] = _h(
        [
            _ACL_REGISTRY_HASH_DOMAIN,
            registry["guard_owner_role"],
            registry["function_rows"],
            registry["current_install_function_signature_tuple"],
            registry["current_column_grant_sql_utf8_hex_tuple"],
            registry["current_column_revoke_sql_utf8_hex_tuple"],
            registry["retained_0037_catalog_projection_tuple"],
            registry["retained_0037_trigger_catalog_projection_tuple"],
            registry["retained_0037_catalog_preflight_sql_utf8_hex"],
            registry["installation_sql_utf8_hex_tuple"],
            registry["downgrade_authority"],
        ]
    )


def _replace_signature_in_hex(value: str) -> str:
    try:
        statement = bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V22_DASHBOARD_ACL_BASE_INVALID") from exc
    old = "public.idis_task7_dashboard_root_equation_v1()"
    if old not in statement:
        _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
    return (
        statement.replace(
            old,
            "public.idis_task8_dashboard_root_equation_v2()",
        )
        .encode("utf-8")
        .hex()
    )


def _transform_dashboard_acl(
    section: dict[str, Any],
    authority: Mapping[str, Any],
    body_sha256: str,
) -> None:
    registry = section.get("guard_function_acl_registry")
    if not isinstance(registry, dict):
        _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
    matches = [
        row
        for row in registry["function_rows"]
        if row["authority_id"] == "dashboard-root-equation-v1"
    ]
    if len(matches) != 1:
        _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
    row = matches[0]
    if (
        row["function_signature"] != "public.idis_task7_dashboard_root_equation_v1()"
        or row["install_scope"] != "FUTURE_INACTIVE"
        or row["helper_call_signature_tuple"]
    ):
        _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")

    row["authority_id"] = "dashboard-root-equation-v2"
    row["function_signature"] = "public.idis_task8_dashboard_root_equation_v2()"
    row["definition_locator"] = (
        "sql_authority_registry.rows[0].body_sql_utf8_hex_tuple[1].function[0]"
    )
    parents = copy.deepcopy(authority["declared_parent_relation_tuple"])
    row["sql_authority_parent_relation_tuple"] = parents
    row["declared_parent_relation_tuple"] = copy.deepcopy(parents)
    row["select_relation_tuple"] = copy.deepcopy(parents)
    if [item[0] for item in row["direct_read_column_tuple"]] != parents:
        _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
    exact_direct_columns = {
        "methodology_truth_dashboard_rows": [
            "coverage_record_id",
            "dashboard_row_id",
            "deal_id",
            "root_projection_sha256",
            "row_disposition",
            "run_id",
            "run_output_object_id",
            "tenant_id",
        ],
        "methodology_truth_dashboard_root_links": [
            "dashboard_root_link_id",
            "dashboard_row_id",
            "deal_id",
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
            "root_output_object_kind",
            "run_id",
            "tenant_id",
        ],
        "run_output_objects": [
            "deal_id",
            "output_class",
            "output_kind",
            "output_object_id",
            "output_schema_version",
            "run_id",
            "tenant_id",
        ],
    }
    for relation, columns, evidence in row["direct_read_column_tuple"]:
        if relation in exact_direct_columns:
            columns[:] = exact_direct_columns[relation]
        if not isinstance(evidence, list) or evidence[0] != "SQL_BODY_SHA256":
            _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
        evidence[1] = body_sha256
    for relation, columns, evidence_rows in row["transitive_read_column_tuple"]:
        if relation in exact_direct_columns:
            columns[:] = exact_direct_columns[relation]
        if not isinstance(evidence_rows, list):
            _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
        for evidence in evidence_rows:
            if not isinstance(evidence, list) or evidence[0] != "SQL_BODY_SHA256":
                _error("TASK8_V22_DASHBOARD_ACL_BASE_INVALID")
            evidence[1] = body_sha256
    row["owner_sql_utf8_hex"] = _replace_signature_in_hex(row["owner_sql_utf8_hex"])
    row["acl_sql_utf8_hex_tuple"] = [
        _replace_signature_in_hex(value) for value in row["acl_sql_utf8_hex_tuple"]
    ]
    _rehash_acl_function_row(row)
    _rehash_acl_registry(registry)


def _transform_partial_exact_one_acl(
    section: dict[str, Any],
    authority: Mapping[str, Any],
    helper_body_sha256: str,
    function_body_sha256: str,
) -> None:
    registry = section["guard_function_acl_registry"]
    rows = registry["function_rows"]
    matches = {
        row["function_signature"]: row
        for row in rows
        if row["function_signature"]
        in (_CURRENT_NATIVE_HASHES_SIGNATURE, _CURRENT_OUTPUT_EXACT_ONE_SIGNATURE)
    }
    if set(matches) != {
        _CURRENT_NATIVE_HASHES_SIGNATURE,
        _CURRENT_OUTPUT_EXACT_ONE_SIGNATURE,
    }:
        _error("TASK8_V22_PARTIAL_ACL_BASE_INVALID")
    helper = matches[_CURRENT_NATIVE_HASHES_SIGNATURE]
    function = matches[_CURRENT_OUTPUT_EXACT_ONE_SIGNATURE]
    if (
        helper["authority_id"] != "current-output-exact-one-v1"
        or function["authority_id"] != "current-output-exact-one-v1"
        or helper["install_scope"] != "CURRENT_0038"
        or function["install_scope"] != "CURRENT_0038"
        or helper["expected_sha256"]
        != "ba3a18326b1e11e916afb9976adf9fb4aa1c1debf9038d324f021dd4cf354628"
        or function["expected_sha256"]
        != "7ceeb28120cba1fcaecb0977d9b224eea8ebda68fb57ef09428a2a0cb1e59dcd"
    ):
        _error("TASK8_V22_PARTIAL_ACL_BASE_INVALID")

    old_helper_hashes = {
        evidence[1]
        for _relation, _columns, evidence in helper["direct_read_column_tuple"]
        if evidence[0] == "SQL_BODY_SHA256"
    }
    old_function_hashes = {
        evidence[1]
        for _relation, _columns, evidence in function["direct_read_column_tuple"]
        if evidence[0] == "SQL_BODY_SHA256"
    }
    if len(old_helper_hashes) != 1 or len(old_function_hashes) != 1:
        _error("TASK8_V22_PARTIAL_ACL_BASE_INVALID")
    old_helper_hash = next(iter(old_helper_hashes))
    old_function_hash = next(iter(old_function_hashes))

    helper_dashboard_read = [
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
    function_dashboard_read = [
        "methodology_truth_dashboard_rows",
        ["deal_id", "run_id", "run_output_object_id", "run_output_object_kind", "tenant_id"],
        ["SQL_BODY_SHA256", function_body_sha256],
    ]
    for row, direct_read in (
        (helper, helper_dashboard_read),
        (function, function_dashboard_read),
    ):
        row["authority_id"] = _PARTIAL_EXACT_ONE_AUTHORITY_ID
        row["install_scope"] = "FUTURE_INACTIVE"
        row["sql_authority_parent_relation_tuple"] = copy.deepcopy(
            authority["declared_parent_relation_tuple"]
        )
        row["declared_parent_relation_tuple"] = copy.deepcopy(
            authority["declared_parent_relation_tuple"]
        )
        row["select_relation_tuple"] = copy.deepcopy(authority["declared_parent_relation_tuple"])
        for _relation, _columns, evidence in row["direct_read_column_tuple"]:
            if evidence == ["SQL_BODY_SHA256", old_helper_hash]:
                evidence[1] = helper_body_sha256
            elif evidence == ["SQL_BODY_SHA256", old_function_hash]:
                evidence[1] = function_body_sha256
            else:
                _error("TASK8_V22_PARTIAL_ACL_BASE_INVALID")
        row["direct_read_column_tuple"].append(direct_read)
        for _relation, _columns, evidence_rows in row["transitive_read_column_tuple"]:
            for evidence in evidence_rows:
                if evidence == ["SQL_BODY_SHA256", old_helper_hash]:
                    evidence[1] = helper_body_sha256
                elif evidence == ["SQL_BODY_SHA256", old_function_hash]:
                    evidence[1] = function_body_sha256
                else:
                    _error("TASK8_V22_PARTIAL_ACL_BASE_INVALID")

    helper["definition_locator"] = (
        "sql_authority_registry.rows[28].body_sql_utf8_hex_tuple[0].function[0]"
    )
    function["definition_locator"] = (
        "sql_authority_registry.rows[28].body_sql_utf8_hex_tuple[1].function[0]"
    )
    helper["transitive_read_column_tuple"].append(
        [
            helper_dashboard_read[0],
            copy.deepcopy(helper_dashboard_read[1]),
            [["SQL_BODY_SHA256", helper_body_sha256]],
        ],
    )
    function["transitive_read_column_tuple"].append(
        [
            helper_dashboard_read[0],
            copy.deepcopy(helper_dashboard_read[1]),
            [
                ["SQL_BODY_SHA256", function_body_sha256],
                ["SQL_BODY_SHA256", helper_body_sha256],
            ],
        ]
    )
    helper["transitive_read_column_tuple"].sort(key=lambda item: item[0])
    function["transitive_read_column_tuple"].sort(key=lambda item: item[0])
    function["target_trigger_tuple"] = copy.deepcopy(authority["target_tuple"])
    function["target_event_coordinate_tuple"] = [
        [
            table,
            trigger,
            "AFTER",
            event,
            "ROW",
            "O",
            True,
            True,
            True,
        ]
        for table, trigger in authority["target_tuple"]
        for event in ("INSERT", "UPDATE", "DELETE")
    ]
    _rehash_acl_function_row(helper)
    _rehash_acl_function_row(function)
    _rehash_acl_registry(registry)


def _transform_sql_registry(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    if len(rows) != 28 or rows[0]["authority_id"] != "dashboard-root-equation-v1":
        _error("TASK8_V22_SQL_AUTHORITY_BASE_INVALID")
    dashboard, body_sha256 = _dashboard_authority(rows[0])
    rows[0] = dashboard
    current_matches = [row for row in rows if row["authority_id"] == "current-output-exact-one-v1"]
    if len(current_matches) != 1:
        _error("TASK8_V22_CURRENT_OUTPUT_EXTENSION_BASE_INVALID")
    partial, helper_body_sha256, function_body_sha256, _downgrade = _partial_exact_one_authority(
        len(rows), current_matches[0]
    )
    rows.append(partial)
    _transform_dashboard_acl(section, dashboard, body_sha256)
    _transform_partial_exact_one_acl(
        section,
        partial,
        helper_body_sha256,
        function_body_sha256,
    )
    section["expected_sha256"] = _h(
        [
            "full-strict-v22-sql-authority-registry-v1",
            rows,
            section["guard_function_acl_registry"],
        ]
    )


def _decode_hex_tuple(row: Mapping[str, Any], field: str) -> list[str]:
    try:
        values = row[field]
        if not isinstance(values, list) or not values:
            _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
        return [bytes.fromhex(str(value)).decode("utf-8") for value in values]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V22_ERASURE_REFUSAL_INVALID") from exc


def _transform_erasure_registry(artifact: dict[str, Any]) -> tuple[str, ...]:
    section = artifact["erasure_execution_registry"]
    row = section["rows"][0]
    if (
        len(row["current_relation_tuple"]) != 48
        or len(row["future_relation_tuple"]) != 38
        or len(row["complete_relation_tuple"]) != 86
        or "methodology_truth_dashboard_rows" in row["current_relation_tuple"]
        or "methodology_truth_dashboard_rows" not in row["future_relation_tuple"]
    ):
        _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
    if "methodology_truth_dashboard_rows" in row["declared_read_relation_tuple"]:
        _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
    row["execution_version"] = "3"
    row["declared_read_relation_tuple"].append("methodology_truth_dashboard_rows")
    current_body = _decode_hex_tuple(row, "current_body_sql_utf8_hex_tuple")
    if len(current_body) != 1 or current_body[0].count(_ERASURE_LOCK_MARKER) != 1:
        _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
    restored_body = current_body[0].replace(
        "CREATE FUNCTION public.idis_task7_execute_approved_erasure(",
        "CREATE OR REPLACE FUNCTION public.idis_task7_execute_approved_erasure(",
        1,
    )
    if restored_body == current_body[0]:
        _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
    current_body[0] = current_body[0].replace(
        _ERASURE_LOCK_MARKER,
        _ERASURE_LOCK_MARKER + _ERASURE_REFUSAL_SQL,
    )
    row["current_body_sql_utf8_hex_tuple"] = _encoded(tuple(current_body))
    current_acl = _decode_hex_tuple(row, "current_acl_sql_utf8_hex_tuple")
    if any("methodology_truth_dashboard_rows" in statement for statement in current_acl):
        _error("TASK8_V22_ERASURE_REFUSAL_INVALID")
    downgrade = (restored_body, *current_acl, _DASHBOARD_SELECT_REVOKE)
    current_acl.append(_DASHBOARD_SELECT_GRANT)
    row["current_acl_sql_utf8_hex_tuple"] = _encoded(tuple(current_acl))
    drop = "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
    row["future_restoration_sql_utf8_hex_tuple"] = _encoded((drop, *current_body, *current_acl))
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _h(["full-strict-v22-erasure-execution-row-v1", payload])
    section["expected_sha256"] = _h(
        ["full-strict-v22-erasure-execution-registry-v1", section["rows"]]
    )
    return downgrade


def _task8_section(
    *,
    policy: Mapping[str, Any],
    policy_artifact_sha256: str,
    policy_fixture: Mapping[str, Any],
    policy_fixture_sha256: str,
    schema_contracts: tuple[dict[str, Any], dict[str, Any]],
    current_output_downgrade_sql: tuple[str, ...],
    current_output_target_tuple: list[list[str]],
    erasure_refusal_downgrade_sql: tuple[str, ...],
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "section_schema": _TASK8_SECTION_SCHEMA,
        "policy_binding": {
            "policy_id": _POLICY_ID,
            "artifact_relative_path": "contracts/fdd_formula_policy_v1.json",
            "artifact_sha256": policy_artifact_sha256,
            "policy_sha256": policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"],
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v1_hash_v1.json"
            ),
            "fixture_sha256": policy_fixture_sha256,
            "fixture_policy_sha256": policy_fixture["fdd_formula_policy_v1_sha256"],
        },
        "fdd_schema_contracts": copy.deepcopy(list(schema_contracts)),
        "selector_contracts": [list(row) for row in _SELECTOR_CONTRACTS],
        "dashboard_contract": {
            "authority_id": "dashboard-root-equation-v2",
            "truth_table": [
                [ordinal, result_set, defect_set, disposition, list(required), list(forbidden)]
                for ordinal, result_set, defect_set, disposition, required, forbidden in (
                    _DASHBOARD_TRUTH_TABLE
                )
            ],
            "coverage_row_count": 100,
            "financial_row_count": 79,
            "mixed_found_not_found_disposition_tuple": ["ANSWERED", "RED_FLAG"],
            "required_formula_gap_bypass_allowed": False,
        },
        "partial_exact_one_contract": {
            "authority_id": _PARTIAL_EXACT_ONE_AUTHORITY_ID,
            "monotonic_extension_table": "methodology_truth_dashboard_rows",
            "replaced_current_authority_id": "current-output-exact-one-v1",
            "replaced_current_helper_signature": _CURRENT_NATIVE_HASHES_SIGNATURE,
            "replaced_current_function_signature": _CURRENT_OUTPUT_EXACT_ONE_SIGNATURE,
            "replaced_current_target_tuple": copy.deepcopy(current_output_target_tuple),
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
            "downgrade_sql_utf8_hex_tuple": _encoded(current_output_downgrade_sql),
            "downgrade_restores_replaced_functions_unchanged": True,
            "complete_future_authority_id": "native-root-exact-one-v1",
            "complete_future_authority_preserved": True,
            "runtime_conditional_allowed": False,
        },
        "erasure_refusal_contract": {
            "reason_code": _ERASURE_REASON_CODE,
            "sqlstate": _ERASURE_SQLSTATE,
            "checked_relation": "methodology_truth_dashboard_rows",
            "checked_columns": ["tenant_id", "deal_id"],
            "owner_select_columns": ["tenant_id", "deal_id"],
            "owner_insert": False,
            "owner_update": False,
            "owner_delete": False,
            "executor_table_privilege_count": 0,
            "application_runner_execute_count": 0,
            "advisory_lock_contract": "task7-tenant-deal-erasure-advisory-lock-v1",
            "zero_mutation_on_refusal": True,
            "downgrade_sql_utf8_hex_tuple": _encoded(erasure_refusal_downgrade_sql),
        },
        "installed_partition": {
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
        "task11_exit_obligation": {
            "restart_cut_count": 27,
            "undischarged_rule_tuple": list(_TASK11_RULES),
            "three_way_equality_required": True,
            "discharged_by_task8": False,
        },
        "activation_assertions": {
            "activation_status": "INACTIVE",
            "runtime_selectable": False,
            "migration_consumable_before_second_review": False,
            "v21_runtime_lock_unchanged": True,
        },
    }
    section["expected_sha256"] = _h(["full-strict-v22-task8-fdd-contract-v1", section])
    return section


def _transform_activation(artifact: dict[str, Any]) -> None:
    assertions = artifact["activation_assertions"]
    assertions["installed_tenant_rls_table_count"] = 69
    assertions["activation_status"] = "INACTIVE"
    assertions["runtime_selectable"] = False
    assertions["task11_restart_cut_count"] = 27
    assertions["task11_undischarged_rule_count"] = 10


def _build_v22_core(
    v21: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    policy_artifact_sha256: str,
    policy_fixture: Mapping[str, Any],
    policy_fixture_sha256: str,
) -> dict[str, Any]:
    artifact = copy.deepcopy(dict(v21))
    schema_contracts = (_factual_schema_contract(v21), _formula_schema_contract())
    _validate_executable_schema_contracts(schema_contracts)
    _transform_metadata(
        artifact,
        str(policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"]),
    )
    _transform_tuple_registry(artifact)
    _transform_digest_recipe(artifact)
    _transform_schema_registry(artifact, schema_contracts)
    _transform_dashboard_relations(artifact)
    _transform_sql_registry(artifact)
    erasure_refusal_downgrade_sql = _transform_erasure_registry(artifact)
    _transform_activation(artifact)
    current = next(
        row
        for row in v21["sql_authority_registry"]["rows"]
        if row["authority_id"] == "current-output-exact-one-v1"
    )
    _partial, _helper_hash, _function_hash, downgrade_sql = _partial_exact_one_authority(
        len(v21["sql_authority_registry"]["rows"]), current
    )
    artifact["task8_fdd_contract"] = _task8_section(
        policy=policy,
        policy_artifact_sha256=policy_artifact_sha256,
        policy_fixture=policy_fixture,
        policy_fixture_sha256=policy_fixture_sha256,
        schema_contracts=schema_contracts,
        current_output_downgrade_sql=downgrade_sql,
        current_output_target_tuple=current["target_tuple"],
        erasure_refusal_downgrade_sql=erasure_refusal_downgrade_sql,
    )
    return artifact


def _changed_paths(
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
                result.update(_changed_paths(old[key], new[key], (*path, key)))
        return result
    if isinstance(old, list):
        result = set()
        for index in range(max(len(old), len(new))):
            if index >= len(old) or index >= len(new):
                result.add((*path, index))
            else:
                result.update(_changed_paths(old[index], new[index], (*path, index)))
        return result
    return set() if old == new else {path}


def _validate_task8_section(
    artifact: Mapping[str, Any],
    *,
    policy_raw: bytes,
    policy_fixture_raw: bytes,
) -> None:
    section = artifact.get("task8_fdd_contract")
    if not isinstance(section, dict) or tuple(section) != (
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
    ):
        _error("TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    expected_hash = _h(
        [
            "full-strict-v22-task8-fdd-contract-v1",
            {key: value for key, value in section.items() if key != "expected_sha256"},
        ]
    )
    if section["expected_sha256"] != expected_hash:
        _error("TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    policy = _decode_json(policy_raw, "TASK8_V22_POLICY_BINDING_INVALID")
    policy_fixture = _decode_json(
        policy_fixture_raw,
        "TASK8_V22_POLICY_BINDING_INVALID",
    )
    binding = section["policy_binding"]
    if (
        binding["artifact_sha256"] != hashlib.sha256(policy_raw).hexdigest()
        or binding["fixture_sha256"] != hashlib.sha256(policy_fixture_raw).hexdigest()
        or binding["policy_sha256"]
        != policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"]
        or binding["fixture_policy_sha256"] != policy_fixture["fdd_formula_policy_v1_sha256"]
    ):
        _error("TASK8_V22_POLICY_BINDING_INVALID")


def validate_exact_diff(v21: Mapping[str, Any], v22: Mapping[str, Any]) -> None:
    """Prove that v22 changes only the approved successor surfaces."""

    v21_raw, pinned_v21, policy_raw, policy, fixture_raw, fixture = _pinned_inputs()
    if _render(v21) != v21_raw or _render(pinned_v21) != v21_raw:
        _error("TASK8_V22_BASE_ARTIFACT_HASH_MISMATCH")
    candidate = copy.deepcopy(dict(v22))
    if candidate.get("metadata", {}).get("append_frozen") is True:
        candidate["metadata"]["append_frozen"] = False
    if {path[0] for path in _changed_paths(v21, candidate)} != (_APPROVED_CHANGED_SECTIONS):
        _error("TASK8_V22_EXACT_DIFF_INVALID")
    for section in _BYTE_IDENTICAL_SECTIONS:
        if candidate[section] != v21[section]:
            _error("TASK8_V22_INVARIANT_DRIFT", section)
    expected = _build_v22_core(
        pinned_v21,
        policy,
        policy_artifact_sha256=hashlib.sha256(policy_raw).hexdigest(),
        policy_fixture=fixture,
        policy_fixture_sha256=hashlib.sha256(fixture_raw).hexdigest(),
    )
    if _render(candidate) != _render(expected):
        _error("TASK8_V22_EXACT_DIFF_INVALID")


def build_v22(
    v21: dict[str, Any],
    policy: dict[str, Any],
    *,
    policy_artifact_sha256: str,
    policy_fixture_sha256: str,
) -> dict[str, Any]:
    """Build the deterministic, unfrozen v22 candidate."""

    v21_raw, pinned_v21, policy_raw, pinned_policy, fixture_raw, fixture = _pinned_inputs()
    if _render(v21) != v21_raw or _render(v21) != _render(pinned_v21):
        _error("TASK8_V22_BASE_ARTIFACT_HASH_MISMATCH")
    if _render(policy) != policy_raw or _render(policy) != _render(pinned_policy):
        _error("TASK8_V22_POLICY_BINDING_INVALID")
    if policy_artifact_sha256 != hashlib.sha256(policy_raw).hexdigest():
        _error("TASK8_V22_POLICY_BINDING_INVALID")
    if policy_fixture_sha256 != hashlib.sha256(fixture_raw).hexdigest():
        _error("TASK8_V22_POLICY_BINDING_INVALID")
    artifact = _build_v22_core(
        pinned_v21,
        pinned_policy,
        policy_artifact_sha256=policy_artifact_sha256,
        policy_fixture=fixture,
        policy_fixture_sha256=policy_fixture_sha256,
    )
    validate_exact_diff(pinned_v21, artifact)
    return artifact


def project_v22_to_v21(v22: dict[str, Any]) -> dict[str, Any]:
    """Return exact frozen v21 after proving the supplied v22 candidate."""

    _v21_raw, v21, policy_raw, _policy, fixture_raw, _fixture = _pinned_inputs()
    candidate = copy.deepcopy(v22)
    candidate["metadata"]["append_frozen"] = False
    _validate_task8_section(
        candidate,
        policy_raw=policy_raw,
        policy_fixture_raw=fixture_raw,
    )
    validate_exact_diff(v21, candidate)
    return copy.deepcopy(v21)


def validate_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
) -> dict[str, Any]:
    """Validate the exact unfrozen candidate and its policy binding."""

    _v21_raw, v21, pinned_policy_raw, _policy, pinned_fixture_raw, _fixture = _pinned_inputs()
    if policy_artifact_raw != pinned_policy_raw or policy_fixture_raw != pinned_fixture_raw:
        _error("TASK8_V22_POLICY_BINDING_INVALID")
    candidate = copy.deepcopy(dict(artifact))
    if (
        candidate.get("metadata", {}).get("artifact_schema") != _V22_ARTIFACT_SCHEMA
        or candidate.get("metadata", {}).get("pipeline_contract_version") != _V22
        or candidate.get("metadata", {}).get("append_frozen") is not False
        or candidate.get("metadata", {}).get("activation_status") != "INACTIVE"
    ):
        _error("TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    _validate_task8_section(
        candidate,
        policy_raw=policy_artifact_raw,
        policy_fixture_raw=policy_fixture_raw,
    )
    validate_exact_diff(v21, candidate)
    return candidate


def validate_frozen_artifact(
    artifact_raw: bytes,
    *,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
) -> dict[str, Any]:
    """Validate exact frozen bytes without making them runtime-selectable."""

    artifact = _decode_json(artifact_raw, "TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    if _render(artifact) != artifact_raw:
        _error("TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    if artifact.get("metadata", {}).get("append_frozen") is not True:
        _error("TASK8_V22_CONTRACT_ARTIFACT_INVALID")
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    validate_candidate_artifact(
        candidate,
        policy_artifact_raw=policy_artifact_raw,
        policy_fixture_raw=policy_fixture_raw,
    )
    return artifact


def frozen_bytes(candidate: Mapping[str, Any]) -> bytes:
    """Return the append-frozen form after exact candidate validation."""

    _v21_raw, _v21, policy_raw, _policy, fixture_raw, _fixture = _pinned_inputs()
    validated = validate_candidate_artifact(
        candidate,
        policy_artifact_raw=policy_raw,
        policy_fixture_raw=fixture_raw,
    )
    frozen = copy.deepcopy(validated)
    frozen["metadata"]["append_frozen"] = True
    return _render(frozen)


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK8_V22_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK8_V22_OUTPUT_ALREADY_EXISTS") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V21_ARTIFACT)
    parser.add_argument("--policy-artifact", type=Path, default=_POLICY_ARTIFACT)
    parser.add_argument("--policy-fixture", type=Path, default=_POLICY_FIXTURE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        v21_raw = args.base_artifact.read_bytes()
        policy_raw = args.policy_artifact.read_bytes()
        fixture_raw = args.policy_fixture.read_bytes()
        v21 = _decode_json(v21_raw, "TASK8_V22_BASE_ARTIFACT_HASH_MISMATCH")
        policy = _decode_json(policy_raw, "TASK8_V22_POLICY_BINDING_INVALID")
        candidate = build_v22(
            v21,
            policy,
            policy_artifact_sha256=hashlib.sha256(policy_raw).hexdigest(),
            policy_fixture_sha256=hashlib.sha256(fixture_raw).hexdigest(),
        )
        output = frozen_bytes(candidate) if args.freeze else _render(candidate)
        _write_exclusive(args.output, output)
    except (MaterializationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "activation_status": "INACTIVE",
                "artifact_sha256": hashlib.sha256(output).hexdigest(),
                "append_frozen": bool(args.freeze),
                "pipeline_contract_version": _V22,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaterializationError",
    "_render",
    "build_v22",
    "frozen_bytes",
    "main",
    "project_v22_to_v21",
    "validate_candidate_artifact",
    "validate_exact_diff",
    "validate_frozen_artifact",
]
