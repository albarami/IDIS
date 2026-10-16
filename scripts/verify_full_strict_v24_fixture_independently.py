"""Independent stdlib verification for the inactive full-strict-v24 fixture.

The verifier accepts only supplied bytes.  It has its own canonicalization,
component hashing, dependency-graph discovery, cycle detection, conservation
receipt, and dashboard identity implementation.
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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Never

_PIPELINE_VERSION = "full-strict-v24"
_COMPONENT_DOMAIN = "full-strict-v24-component-v1"
_PIPELINE_DOMAIN = "full-strict-v24-pipeline-contract-v1"
_GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
_CONSERVATION_SCHEMA = "task8-v24-approved-semantics-conservation-v1"
_STRUCTURAL_SCHEMA = "task8-v24-structural-admission-v1"
_DASHBOARD_SCOPE = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
_DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
_OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECIPE_OPERATOR_TOKENS = frozenset(
    {"CJ", "H", "ORDERED", "SCALAR", "SHA256", "TUPLE", "lowercase_hex"}
)
_POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_V2_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
_POLICY_V2_REVIEWER_MAP_RELATIVE_PATH = (
    "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
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
    "parent_artifact_sha256": _V21_ARTIFACT_SHA256,
    "parent_pipeline_contract_hash": _V21_CONTRACT_SHA256,
    "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
}
_APPROVED_DELTA_MANIFEST = (
    (1, "ADD_SEPARATE_FDD_DETERMINISTIC_CALCULATION_SCHEMA", "SEMANTIC_V22"),
    (2, "ADD_FDD_SIGNED_RATIO_FACTUAL_SCHEMA", "SEMANTIC_V22"),
    (3, "BIND_VERSION_AWARE_FDD_SELECTORS", "SEMANTIC_V22"),
    (4, "REPLACE_DASHBOARD_MIXED_RESULT_TRUTH_TABLE", "SEMANTIC_V22"),
    (5, "ADD_PARTIAL_NATIVE_ROOT_AND_ERASURE_REFUSAL", "SEMANTIC_V22"),
    (6, "BIND_EXACT_POLICY_ARTIFACT_OUTPUT_HASH_CONTRACTS", "SEMANTIC_V22"),
    (7, "ADD_ACYCLIC_DASHBOARD_IDENTITY_CONTRACT", "SECOND_GATE_SEMANTIC"),
    (8, "DERIVE_DASHBOARD_SQL_FROM_FROZEN_RECIPES", "MECHANICAL_CLOSURE"),
    (9, "ENFORCE_STRICT_PERIOD_ORDERING", "MECHANICAL_CORRECTION"),
    (10, "ENFORCE_POSITIVE_MAGNITUDE_NONNEGATIVE", "MECHANICAL_CORRECTION"),
    (11, "PRESERVE_NUMERIC_SOURCE_NORMALIZATION", "MECHANICAL_CORRECTION"),
    (12, "BIND_FDD_FORMULA_POLICY_V2", "POLICY_BINDING"),
)
_APPROVED_CONSERVATION_BASELINES = (
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
        ("SQL_AUTHORITY", "dashboard-root-equation-v2"),
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
            _POLICY_V2_ARTIFACT_SHA256,
            _POLICY_V2_SHA256,
            _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            _POLICY_V2_REVIEWER_MAP_SHA256,
        ),
    ),
)
_APPROVED_CONSERVATION_RECEIPT_SELECTORS = {
    "STRICT_PERIOD_ORDERING": (
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
    "POSITIVE_MAGNITUDE_NONNEGATIVE": (
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "$defs/FddNumericBindingV1/x-idis-cross-field-constraint-tuple/1"
            ),
            "CANONICAL_JSON_SHA256",
        ),
    ),
    "SOURCE_NORMALIZATION_PRESERVED": (
        (
            "full_strict",
            (
                "#/task8_fdd_contract/fdd_schema_contracts/1/normalized_schema_snapshot/"
                "$defs/FddNumericBindingV1/x-idis-cross-field-constraint-tuple/0"
            ),
            "CANONICAL_JSON_SHA256",
        ),
    ),
    "ERASURE_0039_TO_0038_EXACT_RESTORATION": (
        (
            "full_strict",
            "#/task8_fdd_contract/erasure_refusal_contract",
            "CANONICAL_JSON_SHA256",
        ),
    ),
    "DASHBOARD_100_OF_100_CENSUS_GUARD": (
        (
            "full_strict",
            "#/sql_authority_registry/rows/0",
            "CANONICAL_JSON_SHA256",
        ),
    ),
    "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT": (
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
    "FDD_FORMULA_POLICY_V2_BINDING": (
        (
            "full_strict",
            "#/task8_fdd_contract/policy_binding",
            "CANONICAL_JSON_SHA256",
        ),
        ("fdd_formula_policy", "#", "CANONICAL_JSON_SHA256"),
        (
            "reviewer_map",
            _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            "RAW_FILE_SHA256",
        ),
    ),
}

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


class IndependentVerificationError(RuntimeError):
    """Fail-closed error exposing only a stable contract code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Never:
    raise IndependentVerificationError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def _normalize_canonical_value(value: object) -> object:
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("TASK8_V24_INDEPENDENT_BINARY64_NONFINITE")
        return 0 if value == 0.0 else value
    if isinstance(value, str):
        _require(
            unicodedata.normalize("NFC", value) == value,
            "TASK8_V24_INDEPENDENT_UNICODE_NOT_NFC",
        )
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, list | tuple):
        return [_normalize_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            _require(isinstance(key, str), "TASK8_V24_INDEPENDENT_NON_STRING_KEY")
            normalized_key = _normalize_canonical_value(key)
            _require(isinstance(normalized_key, str), "TASK8_V24_INDEPENDENT_NON_STRING_KEY")
            result[normalized_key] = _normalize_canonical_value(item)
        return result
    _fail("TASK8_V24_INDEPENDENT_VALUE_TYPE_INVALID")


def _canonical_json(value: object) -> bytes:
    normalized = _normalize_canonical_value(value)
    return json.dumps(
        normalized,
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
    return value


def _validate_frozen_policy(policy_artifact_raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(policy_artifact_raw).hexdigest() != _POLICY_V2_ARTIFACT_SHA256:
        _fail("TASK8_V24_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH")
    policy = _decode(
        policy_artifact_raw,
        "TASK8_V24_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH",
    )
    digest = policy.get("digest_recipe_registry")
    valid = (
        _render(policy) == policy_artifact_raw
        and isinstance(digest, Mapping)
        and digest.get("fdd_formula_policy_v2_sha256") == _POLICY_V2_SHA256
        and policy.get("metadata", {}).get("policy_id") == "fdd-formula-policy-v2"
    )
    _require(valid, "TASK8_V24_INDEPENDENT_POLICY_FROZEN_IDENTITY_MISMATCH")
    return policy


def _validate_structural_admission(artifact: Mapping[str, Any]) -> None:
    _require(
        tuple(artifact) == _EXPECTED_TOP_LEVEL_SECTIONS,
        "TASK8_V24_INDEPENDENT_TOP_LEVEL_SECTION_CLOSURE_INVALID",
    )
    structural = artifact.get("task8_structural_admission")
    _require(
        isinstance(structural, Mapping)
        and set(structural)
        == {
            "section_schema",
            "parent_provenance",
            "approved_delta_manifest",
            "approved_semantics_conservation_manifest",
            "approved_semantics_conservation_sha256",
            "identity_hash_dependency_contract",
            "dashboard_sql_recipe_contract",
            "expected_sha256",
        }
        and structural.get("section_schema") == _STRUCTURAL_SCHEMA,
        "TASK8_V24_INDEPENDENT_STRUCTURAL_HASH_MISMATCH",
    )
    _require(
        structural.get("parent_provenance") == _EXPECTED_PARENT_PROVENANCE,
        "TASK8_V24_INDEPENDENT_PARENT_PROVENANCE_INVALID",
    )
    _require(
        structural.get("approved_delta_manifest")
        == [list(row) for row in _APPROVED_DELTA_MANIFEST],
        "TASK8_V24_INDEPENDENT_APPROVED_DELTA_MANIFEST_INVALID",
    )
    payload = {key: value for key, value in structural.items() if key != "expected_sha256"}
    expected = _canonical_sha256([_STRUCTURAL_SCHEMA, payload])
    _require(
        structural.get("expected_sha256") == expected,
        "TASK8_V24_INDEPENDENT_STRUCTURAL_HASH_MISMATCH",
    )


def _artifact_policy_binding(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    task8 = artifact.get("task8_fdd_contract")
    binding = task8.get("policy_binding") if isinstance(task8, Mapping) else None
    _require(
        isinstance(binding, Mapping),
        "TASK8_V24_INDEPENDENT_POLICY_BINDING_INVALID",
    )
    expected_values = (
        "fdd-formula-policy-v2",
        _POLICY_V2_ARTIFACT_SHA256,
        _POLICY_V2_SHA256,
        _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
        _POLICY_V2_REVIEWER_MAP_SHA256,
    )
    observed_values = (
        binding.get("policy_id"),
        binding.get("artifact_sha256"),
        binding.get("policy_sha256"),
        binding.get("reviewer_map_relative_path"),
        binding.get("reviewer_map_sha256"),
    )
    expected_binding_hash = _canonical_sha256(
        ["task8-v24-policy-binding-v1", list(expected_values)]
    )
    _require(
        set(binding)
        == {
            "policy_id",
            "artifact_relative_path",
            "artifact_sha256",
            "policy_sha256",
            "fixture_relative_path",
            "fixture_sha256",
            "fixture_policy_sha256",
            "reviewer_map_relative_path",
            "reviewer_map_sha256",
            "binding_sha256",
        }
        and binding.get("artifact_relative_path") == "contracts/fdd_formula_policy_v2.json"
        and binding.get("fixture_relative_path")
        == "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json"
        and binding.get("fixture_sha256") == _POLICY_V2_FIXTURE_SHA256
        and binding.get("fixture_policy_sha256") == _POLICY_V2_SHA256
        and observed_values == expected_values
        and binding.get("binding_sha256") == expected_binding_hash,
        "TASK8_V24_INDEPENDENT_POLICY_BINDING_INVALID",
    )
    return binding


def _validate_reviewer_map_binding(
    artifact: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> str:
    digest = hashlib.sha256(reviewer_map_raw).hexdigest()
    _require(
        digest == _POLICY_V2_REVIEWER_MAP_SHA256,
        "TASK8_V24_INDEPENDENT_REVIEWER_MAP_FROZEN_IDENTITY_MISMATCH",
    )
    _artifact_policy_binding(artifact)
    return digest


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
            result.update(
                _walk_preimages(
                    child,
                    artifact_name=artifact_name,
                    path=child_path,
                )
            )
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
        _require(isinstance(rows, list), "TASK8_V24_INDEPENDENT_GRAPH_SOURCE_INVALID")
        for ordinal, row in enumerate(rows):
            _require(
                isinstance(row, list) and len(row) == width,
                "TASK8_V24_INDEPENDENT_GRAPH_SOURCE_INVALID",
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
        value: object = full_strict if artifact_name == "full_strict" else policy
        for raw in encoded.split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
        return value
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndependentVerificationError("TASK8_V24_INDEPENDENT_GRAPH_SOURCE_INVALID") from exc


def _relative_payload(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    if pointer.startswith("#/") and pointer[2:].isdigit() and isinstance(value, list):
        ordinal = int(pointer[2:])
        if 0 <= ordinal < len(value):
            return value[ordinal]
    _fail("TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID")


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
    _require(len(node) == 6, "TASK8_V24_INDEPENDENT_GRAPH_NODE_INVALID")
    return _canonical_json(list(node[:5])).decode("utf-8")


def _recipe_identifiers(value: object) -> set[str]:
    if not isinstance(value, str):
        return {str(value)}
    return set(_IDENTIFIER.findall(value)) - _RECIPE_OPERATOR_TOKENS


class _RecipeParser:
    """Closed, full-consumption parser for the hash-bound recipe language."""

    def __init__(self, expression: str) -> None:
        self.expression = expression
        self.offset = 0

    def parse(self) -> None:
        self._expression()
        self._whitespace()
        self._expect(self.offset == len(self.expression))

    def _expect(self, condition: bool) -> None:
        _require(condition, "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID")

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
        assert match is not None
        self.offset = match.end()
        return match.group(0)

    def _json_string(self) -> None:
        self._whitespace()
        try:
            value, consumed = json.JSONDecoder().raw_decode(self.expression[self.offset :])
        except json.JSONDecodeError as exc:
            raise IndependentVerificationError(
                "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID"
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
    _require(
        isinstance(value, str),
        "TASK8_V24_INDEPENDENT_RECIPE_GRAMMAR_INVALID",
    )
    _RecipeParser(value).parse()


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


def _derive_graph(
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    pointers = _discover_source_pointers(full_strict, policy)
    structural = full_strict.get("task8_structural_admission")
    contract = (
        structural.get("identity_hash_dependency_contract")
        if isinstance(structural, Mapping)
        else None
    )
    _require(
        isinstance(contract, Mapping) and contract.get("schema_version") == _GRAPH_SCHEMA,
        "TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )
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
            "v23_non_dashboard_cycle_disposition",
        )
        and contract.get("source_discovery") == "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS"
        and contract.get("recipe_grammar_tuple") == ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"]
        and contract.get("dashboard_special_case_allowed") is False
        and contract.get("synthetic_symbol_special_case_allowed") is False
        and contract.get("v23_non_dashboard_cycle_disposition")
        == "SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY"
        and contract.get("producer_authority_schema") == producer_schema
        and isinstance(producer_rows, list)
        and contract.get("producer_authority_sha256")
        == _canonical_sha256(["task8-v24-graph-producer-authority-v1", producer_rows]),
        "TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )
    _require(
        contract.get("allowed_external_input_schema") == external_schema
        and isinstance(external_rows, list)
        and contract.get("allowed_external_input_sha256")
        == _canonical_sha256(["task8-v24-graph-allowed-external-input-v1", external_rows]),
        "TASK8_V24_INDEPENDENT_EXTERNAL_INPUT_INVALID",
    )

    producer_by_pointer: dict[str, list[str]] = {}
    nodes: list[list[str]] = []
    payload_pointers: dict[str, str] = {}
    for raw_row in producer_rows:
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 7
            and all(isinstance(value, str) and value for value in raw_row),
            "TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
        )
        pointer, artifact_name, layer, scope, output_kind, symbol, payload_pointer = raw_row
        _require(
            pointer not in producer_by_pointer
            and (
                payload_pointer == "#"
                or (payload_pointer.startswith("#/") and payload_pointer[2:].isdigit())
            ),
            "TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
        )
        producer_by_pointer[pointer] = raw_row
        payload_pointers[pointer] = payload_pointer
        nodes.append([artifact_name, layer, scope, output_kind, symbol, pointer])
    _require(
        set(producer_by_pointer) == set(pointers),
        "TASK8_V24_INDEPENDENT_PRODUCER_AUTHORITY_INVALID",
    )

    symbol_nodes: dict[str, list[list[str]]] = {}
    nodes_by_id: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _node_id(node)
        _require(
            node_id not in nodes_by_id,
            "TASK8_V24_INDEPENDENT_DUPLICATE_PRODUCER",
        )
        nodes_by_id[node_id] = node
        symbol_nodes.setdefault(node[4], []).append(node)
    _require(
        all(len(candidates) == 1 for candidates in symbol_nodes.values()),
        "TASK8_V24_INDEPENDENT_DUPLICATE_PRODUCER",
    )

    allowed_external: dict[tuple[str, str, str], str] = {}
    for raw_row in external_rows:
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 4
            and all(isinstance(value, str) and value for value in raw_row),
            "TASK8_V24_INDEPENDENT_EXTERNAL_INPUT_INVALID",
        )
        pointer, scalar_path, symbol, authority = raw_row
        key = (pointer, scalar_path, symbol)
        _require(
            pointer in producer_by_pointer
            and key not in allowed_external
            and authority in _ALLOWED_LEAF_AUTHORITIES,
            "TASK8_V24_INDEPENDENT_EXTERNAL_INPUT_INVALID",
        )
        allowed_external[key] = authority

    values: dict[str, object] = {}
    for pointer in pointers:
        value = _pointer_value(pointer, full_strict, policy)
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
                authority = _leaf_authority(token_value)
                leaves.add((node_id, token_path, str(token_value), authority, pointer))
                continue
            for identifier in identifiers:
                candidates = symbol_nodes.get(identifier, [])
                if len(candidates) == 1:
                    dependency_id = _node_id(candidates[0])
                    if dependency_id == node_id:
                        _fail("TASK8_V24_INDEPENDENT_IDENTITY_HASH_CYCLE")
                    edges.add(
                        (
                            node_id,
                            dependency_id,
                            f"{pointer}:{token_path}#{identifier}",
                        )
                    )
                    continue
                key = (pointer, token_path, identifier)
                authority = allowed_external.get(key)
                if authority is None:
                    _fail("TASK8_V24_INDEPENDENT_GOVERNED_REFERENCE_UNRESOLVED")
                consumed_external.add(key)
                leaves.add(
                    (
                        node_id,
                        f"{token_path}#{identifier}",
                        identifier,
                        authority,
                        pointer,
                    )
                )
    _require(
        consumed_external == set(allowed_external),
        "TASK8_V24_INDEPENDENT_EXTERNAL_INPUT_ORPHANED",
    )

    adjacency = {node_id: set() for node_id in nodes_by_id}
    for derived, dependency, _receipt in edges:
        _require(
            derived in adjacency and dependency in adjacency,
            "TASK8_V24_INDEPENDENT_GRAPH_ENDPOINT_INVALID",
        )
        adjacency[derived].add(dependency)
    if _cycle(adjacency) is not None:
        _fail("TASK8_V24_INDEPENDENT_IDENTITY_HASH_CYCLE")

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


def _component_and_contract_hashes(
    artifact: Mapping[str, Any],
    artifact_raw: bytes,
) -> tuple[dict[str, str], str]:
    components = {
        f"{section}_hash": _canonical_sha256([_COMPONENT_DOMAIN, section, copy.deepcopy(value)])
        for section, value in artifact.items()
    }
    task8 = artifact.get("task8_fdd_contract")
    _require(
        isinstance(task8, Mapping)
        and isinstance(task8.get("expected_sha256"), str)
        and bool(_SHA256.fullmatch(str(task8["expected_sha256"]))),
        "TASK8_V24_INDEPENDENT_TASK8_COMPONENT_INVALID",
    )
    components["task8_fdd_contract_hash"] = str(task8["expected_sha256"])
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


def _derive_counts(artifact: Mapping[str, Any]) -> dict[str, int]:
    erasure_registry = artifact.get("erasure_execution_registry")
    erasure_rows = erasure_registry.get("rows") if isinstance(erasure_registry, Mapping) else None
    _require(
        isinstance(erasure_rows, list)
        and len(erasure_rows) >= 1
        and isinstance(erasure_rows[0], Mapping),
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    erasure = erasure_rows[0]
    task8 = artifact.get("task8_fdd_contract")
    _require(isinstance(task8, Mapping), "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID")
    installed = task8.get("installed_partition")
    task11 = task8.get("task11_exit_obligation")
    _require(
        isinstance(installed, Mapping) and isinstance(task11, Mapping),
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )

    def rows_in(section_name: str) -> list[Any]:
        section = artifact.get(section_name)
        rows = section.get("rows") if isinstance(section, Mapping) else None
        _require(isinstance(rows, list), "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID")
        return rows

    current_relations = erasure.get("current_relation_tuple")
    future_relations = erasure.get("future_relation_tuple")
    complete_relations = erasure.get("complete_relation_tuple")
    undischarged = task11.get("undischarged_rule_tuple")
    _require(
        all(
            isinstance(value, list)
            for value in (
                current_relations,
                future_relations,
                complete_relations,
                undischarged,
            )
        ),
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
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
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    return counts


def _resolve_json_pointer(document: object, pointer: str) -> object:
    if pointer == "#":
        return document
    _require(
        pointer.startswith("#/"),
        "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
    )
    value = document
    try:
        for raw_token in pointer[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndependentVerificationError(
            "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID"
        ) from exc
    return value


def _validate_conservation_receipts(
    manifest: Sequence[object],
    *,
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> None:
    reviewer_receipt_count = 0
    for expected_ordinal, raw_row in enumerate(manifest, start=1):
        baseline = _APPROVED_CONSERVATION_BASELINES[expected_ordinal - 1]
        _require(
            isinstance(raw_row, list)
            and len(raw_row) == 5
            and raw_row[0] == expected_ordinal
            and isinstance(raw_row[1], str)
            and bool(raw_row[1])
            and isinstance(raw_row[2], list)
            and bool(raw_row[2])
            and isinstance(raw_row[4], list)
            and bool(raw_row[4]),
            "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
        )
        _require(
            _canonical_json(raw_row[:4]) == _canonical_json(list(baseline)),
            "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
        )
        expected_selectors = _APPROVED_CONSERVATION_RECEIPT_SELECTORS.get(raw_row[1])
        actual_selectors = tuple(
            tuple(raw_receipt[:3])
            for raw_receipt in raw_row[4]
            if isinstance(raw_receipt, list) and len(raw_receipt) == 4
        )
        _require(
            expected_selectors is not None and actual_selectors == expected_selectors,
            "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
        )
        for raw_receipt in raw_row[4]:
            _require(
                isinstance(raw_receipt, list)
                and len(raw_receipt) == 4
                and all(isinstance(item, str) and item for item in raw_receipt),
                "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
            )
            source_name, locator, digest_kind, expected_digest = raw_receipt
            _require(
                bool(_SHA256.fullmatch(expected_digest)),
                "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
            )
            if source_name == "full_strict" and digest_kind == "CANONICAL_JSON_SHA256":
                actual = _canonical_sha256(_resolve_json_pointer(artifact, locator))
            elif source_name == "fdd_formula_policy" and digest_kind == "CANONICAL_JSON_SHA256":
                actual = _canonical_sha256(_resolve_json_pointer(policy, locator))
            elif (
                source_name == "reviewer_map"
                and digest_kind == "RAW_FILE_SHA256"
                and locator == _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH
            ):
                reviewer_receipt_count += 1
                actual = hashlib.sha256(reviewer_map_raw).hexdigest()
            else:
                _fail("TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID")
            _require(
                actual == expected_digest,
                "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_MISMATCH",
            )
    _require(
        reviewer_receipt_count == 1,
        "TASK8_V24_INDEPENDENT_CONSERVATION_RECEIPT_INVALID",
    )


def _conservation_hash(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> str:
    structural = artifact.get("task8_structural_admission")
    _require(
        isinstance(structural, Mapping) and structural.get("section_schema") == _STRUCTURAL_SCHEMA,
        "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_MISSING",
    )
    manifest = structural.get("approved_semantics_conservation_manifest")
    _require(
        isinstance(manifest, list) and len(manifest) == 7,
        "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
    )
    _validate_conservation_receipts(
        manifest,
        artifact=artifact,
        policy=policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    digest = _canonical_sha256([_CONSERVATION_SCHEMA, manifest])
    _require(
        structural.get("approved_semantics_conservation_sha256") == digest,
        "TASK8_V24_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
    )
    return digest


def _validate_dashboard_recipe_fields(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    uuid_section = artifact.get("uuid_namespace_registry")
    uuid_rows = uuid_section.get("rows") if isinstance(uuid_section, Mapping) else None
    _require(isinstance(uuid_rows, list), "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING")
    dashboard_rows = [
        row
        for row in uuid_rows
        if isinstance(row, list) and len(row) == 3 and row[0] == "dashboard_row_id"
    ]
    _require(
        len(dashboard_rows) == 1 and tuple(dashboard_rows[0][2]) == _DASHBOARD_ROW_NAME_TUPLE,
        "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING",
    )
    financial = policy.get("financial_output_schema")
    row_preimage = financial.get("row_payload_preimage") if isinstance(financial, Mapping) else None
    _require(
        isinstance(row_preimage, list)
        and "dashboard_output_object_id" not in str(row_preimage)
        and "root_projection_sha256" in str(row_preimage),
        "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING",
    )
    digest_section = artifact.get("digest_recipe_registry")
    digest_rows = digest_section.get("rows") if isinstance(digest_section, Mapping) else None
    _require(isinstance(digest_rows, list), "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING")
    projection = [
        row[1]
        for row in digest_rows
        if isinstance(row, list)
        and len(row) == 2
        and row[0] == "methodology_truth_dashboard_root_projection_sha256"
    ]
    _require(
        len(projection) == 1
        and "methodology-truth-dashboard-root-projection-v2" in projection[0]
        and "run_output_object_id" not in projection[0],
        "TASK8_V24_INDEPENDENT_IDENTITY_FIELD_MISSING",
    )


def _fixture_graph_hash(graph: Mapping[str, Any]) -> tuple[str, dict[str, int]]:
    _require(
        graph.get("schema_version") == _GRAPH_SCHEMA,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    node_tuple = graph.get("node_tuple")
    edge_tuple = graph.get("edge_tuple")
    leaf_tuple = graph.get("leaf_tuple")
    source_pointer_tuple = graph.get("source_pointer_tuple")
    _require(
        all(
            isinstance(value, list)
            for value in (node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple)
        ),
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    counts = {
        "node_count": len(node_tuple),
        "edge_count": len(edge_tuple),
        "leaf_count": len(leaf_tuple),
        "source_pointer_count": len(source_pointer_tuple),
    }
    _require(
        graph.get("counts") == counts,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    digest = _canonical_sha256(
        [_GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    _require(
        graph.get("graph_sha256") == digest,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    return digest, counts


def derive_fixture(
    *,
    artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, object]:
    """Derive the complete inactive fixture through the independent path."""

    artifact = _decode(artifact_raw, "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID")
    policy = _validate_frozen_policy(policy_artifact_raw)
    _require(
        _render(artifact) == artifact_raw,
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    metadata = artifact.get("metadata")
    _require(
        isinstance(metadata, Mapping)
        and metadata.get("pipeline_contract_version") == _PIPELINE_VERSION
        and metadata.get("activation_status") == "INACTIVE",
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    _validate_structural_admission(artifact)
    reviewer_map_sha256 = _validate_reviewer_map_binding(artifact, reviewer_map_raw)
    components, contract_hash = _component_and_contract_hashes(artifact, artifact_raw)
    _validate_dashboard_recipe_fields(artifact, policy)
    conservation_hash = _conservation_hash(artifact, policy, reviewer_map_raw)
    graph = _derive_graph(artifact, policy)

    policy_binding = _artifact_policy_binding(artifact)
    counts = _derive_counts(artifact)
    return {
        "artifact_schema": "full-strict-v24-contract-hash-fixture-v1",
        "pipeline_contract_version": _PIPELINE_VERSION,
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "FULL_STRICT_V24_CONTRACT_HASH": contract_hash,
        "component_hashes": components,
        "identity_hash_dependency_graph": graph,
        "approved_semantics_conservation": {
            "schema_version": _CONSERVATION_SCHEMA,
            "entry_count": 7,
            "sha256": conservation_hash,
        },
        "policy_binding": {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_sha256": _POLICY_V2_ARTIFACT_SHA256,
            "policy_sha256": _POLICY_V2_SHA256,
            "reviewer_map_relative_path": _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            "reviewer_map_sha256": reviewer_map_sha256,
            "binding_sha256": policy_binding["binding_sha256"],
        },
        "counts": counts,
        "activation_status": "INACTIVE",
    }


def verify_fixture(
    *,
    artifact_raw: bytes,
    policy_artifact_raw: bytes,
    fixture_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, object]:
    """Verify v24 fixture evidence through the independent implementation."""

    artifact = _decode(artifact_raw, "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID")
    policy = _validate_frozen_policy(policy_artifact_raw)
    fixture = _decode(fixture_raw, "TASK8_V24_INDEPENDENT_FIXTURE_INVALID")
    _require(
        _render(artifact) == artifact_raw,
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    _require(
        _render(fixture) == fixture_raw,
        "TASK8_V24_INDEPENDENT_FIXTURE_INVALID",
    )
    metadata = artifact.get("metadata")
    _require(
        isinstance(metadata, Mapping)
        and metadata.get("pipeline_contract_version") == _PIPELINE_VERSION,
        "TASK8_V24_INDEPENDENT_ARTIFACT_INVALID",
    )
    _validate_structural_admission(artifact)
    reviewer_map_sha256 = _validate_reviewer_map_binding(artifact, reviewer_map_raw)
    _require(
        tuple(fixture)
        == (
            "artifact_schema",
            "pipeline_contract_version",
            "artifact_sha256",
            "FULL_STRICT_V24_CONTRACT_HASH",
            "component_hashes",
            "identity_hash_dependency_graph",
            "approved_semantics_conservation",
            "policy_binding",
            "counts",
            "activation_status",
        )
        and fixture.get("artifact_schema") == "full-strict-v24-contract-hash-fixture-v1"
        and fixture.get("pipeline_contract_version") == _PIPELINE_VERSION
        and fixture.get("activation_status") == "INACTIVE",
        "TASK8_V24_INDEPENDENT_FIXTURE_INVALID",
    )

    expected_components, expected_contract = _component_and_contract_hashes(
        artifact,
        artifact_raw,
    )
    _require(
        fixture.get("component_hashes") == expected_components,
        "TASK8_V24_INDEPENDENT_COMPONENT_HASH_MISMATCH",
    )
    _require(
        fixture.get("FULL_STRICT_V24_CONTRACT_HASH") == expected_contract,
        "TASK8_V24_INDEPENDENT_CONTRACT_HASH_MISMATCH",
    )
    artifact_sha256 = hashlib.sha256(artifact_raw).hexdigest()
    _require(
        fixture.get("artifact_sha256") == artifact_sha256,
        "TASK8_V24_INDEPENDENT_ARTIFACT_HASH_MISMATCH",
    )

    _validate_dashboard_recipe_fields(artifact, policy)
    conservation_hash = _conservation_hash(artifact, policy, reviewer_map_raw)
    conservation = fixture.get("approved_semantics_conservation")
    _require(
        conservation
        == {
            "schema_version": _CONSERVATION_SCHEMA,
            "entry_count": 7,
            "sha256": conservation_hash,
        },
        "TASK8_V24_INDEPENDENT_CONSERVATION_HASH_MISMATCH",
    )
    _require(
        fixture.get("counts") == _derive_counts(artifact),
        "TASK8_V24_INDEPENDENT_COUNT_MISMATCH",
    )
    independently_derived_graph = _derive_graph(artifact, policy)
    fixture_graph = fixture.get("identity_hash_dependency_graph")
    _require(
        isinstance(fixture_graph, Mapping),
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    fixture_graph_hash, fixture_graph_counts = _fixture_graph_hash(fixture_graph)
    _require(
        dict(fixture_graph) == independently_derived_graph,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )

    policy_binding = _artifact_policy_binding(artifact)
    expected_fixture_binding = {
        "policy_id": "fdd-formula-policy-v2",
        "artifact_sha256": _POLICY_V2_ARTIFACT_SHA256,
        "policy_sha256": _POLICY_V2_SHA256,
        "reviewer_map_relative_path": _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
        "reviewer_map_sha256": reviewer_map_sha256,
        "binding_sha256": policy_binding["binding_sha256"],
    }
    _require(
        fixture.get("policy_binding") == expected_fixture_binding,
        "TASK8_V24_INDEPENDENT_POLICY_BINDING_INVALID",
    )

    return {
        "verification_path": "TASK8_V24_INDEPENDENT_STDLIB_V1",
        "artifact_sha256": artifact_sha256,
        "component_hashes": expected_components,
        "contract_hash": expected_contract,
        "identity_graph_sha256": fixture_graph_hash,
        "identity_graph_counts": fixture_graph_counts,
        "conservation_sha256": conservation_hash,
        "policy_binding_sha256": policy_binding["binding_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--reviewer-map", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(None if argv is None else list(argv))
    try:
        artifact_raw = args.artifact.read_bytes()
        policy_raw = args.policy.read_bytes()
        fixture_raw = args.fixture.read_bytes()
        reviewer_map_raw = args.reviewer_map.read_bytes()
        report = verify_fixture(
            artifact_raw=artifact_raw,
            policy_artifact_raw=policy_raw,
            fixture_raw=fixture_raw,
            reviewer_map_raw=reviewer_map_raw,
        )
        fixture = _decode(fixture_raw, "TASK8_V24_INDEPENDENT_FIXTURE_INVALID")
        graph = fixture["identity_hash_dependency_graph"]
        counts = {**fixture["counts"], **graph["counts"]}
    except IndependentVerificationError as exc:
        print(json.dumps({"reason_code": exc.code, "result": "FAILED"}, sort_keys=True))
        return 2
    except (OSError, KeyError, TypeError) as exc:
        code = "TASK8_V24_INDEPENDENT_INPUT_MISSING"
        if not isinstance(exc, OSError):
            code = "TASK8_V24_INDEPENDENT_FIXTURE_INVALID"
        print(json.dumps({"reason_code": code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "activation_status": "INACTIVE",
                "artifact_sha256": report["artifact_sha256"],
                "conservation_sha256": report["conservation_sha256"],
                "contract_hash": report["contract_hash"],
                "counts": counts,
                "graph_sha256": report["identity_graph_sha256"],
                "policy_binding_sha256": report["policy_binding_sha256"],
                "result": "VERIFIED",
                "verification_path": report["verification_path"],
            },
            sort_keys=True,
        )
    )
    return 0


def _uuid5(namespace: str, value: object) -> str:
    name = hashlib.sha256(_canonical_json(value)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), name))


def derive_dashboard_identity_vector(**inputs: Any) -> dict[str, object]:
    """Recompute the approved five-stage dashboard identity chain."""

    required = {
        "tenant_id",
        "deal_id",
        "run_id",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "question_payload_hash",
        "row_disposition",
        "disposition_reason_code",
        "row_trace_prefix",
        "result_records",
        "answer_values",
        "task_gaps",
        "calculations",
        "roots",
    }
    _require(set(inputs) == required, "TASK8_V24_INDEPENDENT_IDENTITY_INPUT_INVALID")
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

    roots = inputs["roots"]
    _require(isinstance(roots, list), "TASK8_V24_INDEPENDENT_IDENTITY_INPUT_INVALID")
    root_links: list[list[object]] = []
    for root in sorted(
        roots,
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
    identity_and_trace = [*inputs["row_trace_prefix"], coverage_record_id, row_id]
    row_payload_hash = _canonical_sha256(
        [
            "financial-diligence-row-v2",
            identity_and_trace,
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
            _DASHBOARD_KIND,
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
            _DASHBOARD_KIND,
            [row_id],
            [
                coverage_record_id,
                projection_hash,
                inputs["row_disposition"],
                row_payload_hash,
            ],
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
            _DASHBOARD_KIND,
            native_identity_hash,
            native_output_hash,
        ],
    )
    return {
        "dashboard_row_id": row_id,
        "root_link_tuple": root_links,
        "root_projection_sha256": projection_hash,
        "row_payload_sha256": row_payload_hash,
        "native_identity_sha256": native_identity_hash,
        "native_output_sha256": native_output_hash,
        "output_object_id": output_object_id,
    }


__all__ = [
    "IndependentVerificationError",
    "derive_fixture",
    "derive_dashboard_identity_vector",
    "main",
    "verify_fixture",
]


if __name__ == "__main__":
    raise SystemExit(main())
