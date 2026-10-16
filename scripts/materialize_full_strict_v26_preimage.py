"""Build the bounded, inactive full-strict-v26 candidate without publishing it.

The candidate is derived from exact frozen full-strict-v21 bytes plus the
complete approved Task-8 delta implementation.  Rejected candidate artifacts
are never read or accepted as parents.  Canonical artifact and fixture paths
are publication commit points and this module refuses to write them; callers
may request only an explicit noncanonical staging output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Never

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V21_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_POLICY_V2_ARTIFACT = _REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
_POLICY_V2_REVIEWER_MAP = (
    _REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_POLICY_V2_REVIEWER_MAP_RELATIVE_PATH = (
    "docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_APPROVED_DELTA_HELPER = _SCRIPT_DIR / "materialize_full_strict_v22_preimage.py"
_CANONICAL_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
_CANONICAL_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)

_V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
_V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
_V26_ARTIFACT_SHA256 = "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27"
_POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
_APPROVED_DELTA_HELPER_SHA256 = "78add79548bd4ef47f26290f63325f4b0744f659cfcc0f8c47deb30ebc282c29"
_POLICY_V2_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
_SUPERSEDED_POLICY_SHA256 = "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2"
_WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
_POLICY_V2_METHODOLOGY_VERSION_ID = (
    f"financial_dd:{_WORKBOOK_SHA256}:fdd-formula-policy-v2:{_POLICY_V2_SHA256}"
)

_V26 = "full-strict-v26"
_V26_SCHEMA = "full-strict-v26-reviewed-preimage-v1"
_STRUCTURAL_SCHEMA = "task8-v26-structural-admission-v1"
_STRUCTURAL_DOMAIN = "task8-v26-structural-admission-v1"
_CONSERVATION_DOMAIN = "task8-v26-approved-semantics-conservation-v1"
_REQUIREMENT_SCHEMA = "task8-dashboard-requirement-binding-v3"
_REQUIREMENT_LITERAL_DOMAIN = "task8-v26-dashboard-policy-v2-requirement-literal-v1"
_POLICY_BINDING_DOMAIN = "task8-v26-policy-binding-v1"
_GRAPH_PRODUCER_DOMAIN = "task8-v26-graph-producer-authority-v1"
_GRAPH_EXTERNAL_DOMAIN = "task8-v26-graph-allowed-external-input-v1"
_GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
_STABLE_DASHBOARD_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"

# Literal generation-time authorities.  Admission recomputes each value from
# the supplied frozen inputs; candidate bytes cannot affirm their own census.
_V26_GRAPH_PRODUCER_SHA256 = "2cd98224e13fedb8163115eb8b36f0bbcd89a8d2349e2862dfc2a1ce51799bad"
_V26_GRAPH_EXTERNAL_INPUT_SHA256 = (
    "73184db5100e400407713e370795956bfdd7fd51c34ca745ee486d925116af25"
)
_V26_CONSERVATION_SHA256 = "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235"

_SQL_ROW_DOMAIN = "full-strict-v22-sql-authority-row-v1"
_SQL_REGISTRY_DOMAIN = "full-strict-v22-sql-authority-registry-v1"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_ROLE_HASH_DOMAIN = b"full-strict-v12-guard-owner-role-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"

_POLICY_KEYS = ("formula_ordinal", "formula_type", "required", "scope")
_LEGACY_KEYS = ("calc_type", "required")
_POLICY_REQUIREMENT_TUPLE = (
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
_APPROVED_EQUATION_V3_DELTA = (
    13,
    "REPLACE_DASHBOARD_REQUIREMENT_BINDING_WITH_VERSION_DISCRIMINATED_V3",
    "SEMANTIC_REVIEWED_EQUATION_V3",
)
_APPROVED_BOUND_RUNTIME_GRAPH_DELTA = (
    14,
    "CLASSIFY_NONPRODUCER_RUNTIME_BINDINGS_AS_BOUND_INPUTS",
    "MECHANICAL_EVIDENCE_CLOSURE",
)
_REQUIREMENT_NEGATIVE_CASES = (
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
    _APPROVED_EQUATION_V3_DELTA,
    _APPROVED_BOUND_RUNTIME_GRAPH_DELTA,
)
_DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
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
_ACYCLIC_DERIVATION_ORDER = (
    "dashboard_row_id",
    "dashboard_root_link_ids",
    "root_projection_sha256",
    "row_payload_sha256",
    "native_identity_sha256_and_native_output_sha256",
    "output_object_id",
)
_PROJECTION_RECIPE = (
    'H(["methodology-truth-dashboard-root-projection-v2",tenant_id,deal_id,'
    "run_id,dashboard_row_id,coverage_record_id,row_disposition,count,"
    "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
    "root_output_object_kind,dashboard_root_link_id)]])"
)
_ROW_PAYLOAD_RECIPE = [
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
_POLICY_ROW_PAYLOAD_POINTER = (
    "fdd_formula_policy:#/financial_output_schema/row_payload_preimage"
)
_POLICY_SUCCESSOR_VERSION_POINTER = (
    "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage"
)
_BOUND_RUNTIME_INPUT_BY_LOCATION = {
    (_POLICY_ROW_PAYLOAD_POINTER, f"$[{ordinal}]"): token
    for ordinal, token in enumerate(_ROW_PAYLOAD_RECIPE[1:8], start=1)
}
_ROOT_PROJECTION_DOLLAR_LOCATION = (_POLICY_ROW_PAYLOAD_POINTER, "$[8]")
_ROOT_PROJECTION_DOLLAR_TOKEN = "$root_projection_sha256"
_POLICY_SHA_PLACEHOLDER_LOCATION = (_POLICY_SUCCESSOR_VERSION_POINTER, "$[3]")
_POLICY_SHA_PLACEHOLDER_TOKEN = "$FDD_FORMULA_POLICY_V2_SHA256"
_RESERVED_DOLLAR_PRODUCER_SYMBOLS = {
    *(token[1:] for token in _BOUND_RUNTIME_INPUT_BY_LOCATION.values()),
    _POLICY_SHA_PLACEHOLDER_TOKEN[1:],
}
_STRUCTURAL_FIELDS = (
    "section_schema",
    "parent_provenance",
    "approved_delta_manifest",
    "approved_semantics_conservation_manifest",
    "approved_semantics_conservation_sha256",
    "identity_hash_dependency_contract",
    "dashboard_sql_recipe_contract",
    "expected_sha256",
)
_CONSERVATION_BASE = (
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
        _ACYCLIC_DERIVATION_ORDER,
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
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BOUND_RUNTIME_INPUT_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_GRAMMAR_OPERATOR_TOKENS = {
    "CJ",
    "H",
    "ORDERED",
    "SCALAR",
    "SHA256",
    "TUPLE",
    "lowercase_hex",
}
_PRODUCER_RESERVED_TOKENS = _GRAMMAR_OPERATOR_TOKENS | {"float8send", "hex"}
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


class _RecipeGrammarError(ValueError):
    """Internal sentinel for a recipe outside the reviewed closed grammar."""


class _RecipeParser:
    """Fully consuming parser for the reviewed digest expression grammar."""

    def __init__(self, recipe: str) -> None:
        self._recipe = recipe
        self._position = 0

    def parse(self) -> None:
        self._expression()
        self._whitespace()
        if self._position != len(self._recipe):
            raise _RecipeGrammarError

    def _whitespace(self) -> None:
        while self._position < len(self._recipe) and self._recipe[self._position] in " \t\r\n":
            self._position += 1

    def _peek(self) -> str | None:
        self._whitespace()
        if self._position == len(self._recipe):
            return None
        return self._recipe[self._position]

    def _consume(self, expected: str) -> None:
        self._whitespace()
        if not self._recipe.startswith(expected, self._position):
            raise _RecipeGrammarError
        self._position += len(expected)

    def _identifier(self) -> str:
        self._whitespace()
        match = _IDENTIFIER_RE.match(self._recipe, self._position)
        if match is None:
            raise _RecipeGrammarError
        self._position = match.end()
        return match.group(0)

    def _json_string(self) -> None:
        self._whitespace()
        try:
            value, end = json.JSONDecoder().raw_decode(self._recipe, self._position)
        except json.JSONDecodeError as exc:
            raise _RecipeGrammarError from exc
        if not isinstance(value, str):
            raise _RecipeGrammarError
        self._position = end

    def _sequence(self, opening: str, closing: str, *, allow_empty: bool) -> None:
        self._consume(opening)
        if self._peek() == closing:
            if not allow_empty:
                raise _RecipeGrammarError
            self._consume(closing)
            return
        self._expression()
        while self._peek() == ",":
            self._consume(",")
            if self._peek() == closing:
                raise _RecipeGrammarError
            self._expression()
        self._consume(closing)

    def _unary_call(self) -> None:
        self._consume("(")
        if self._peek() == ")":
            raise _RecipeGrammarError
        self._expression()
        self._consume(")")

    def _lowercase_hex_chain(self) -> None:
        self._consume("(")
        if self._identifier() != "SHA256":
            raise _RecipeGrammarError
        self._consume("(")
        if self._identifier() != "CJ":
            raise _RecipeGrammarError
        self._unary_call()
        self._consume(")")
        self._consume(")")

    def _float8send_chain(self) -> None:
        self._consume("(")
        if self._identifier() != "float8send":
            raise _RecipeGrammarError
        self._consume("(")
        argument = self._identifier()
        if argument in _GRAMMAR_OPERATOR_TOKENS or argument in {"hex", "float8send"}:
            raise _RecipeGrammarError
        self._consume(")")
        self._consume(")")

    def _named_expression(self) -> None:
        name = self._identifier()
        next_token = self._peek()
        if name == "ORDERED":
            self._consume("[")
            if self._peek() == "]":
                raise _RecipeGrammarError
            self._expression()
            self._consume("]")
            return
        if name in {"H", "CJ", "SCALAR"}:
            self._unary_call()
            return
        if name == "TUPLE":
            self._sequence("(", ")", allow_empty=False)
            return
        if name == "lowercase_hex":
            self._lowercase_hex_chain()
            return
        if name == "hex":
            self._float8send_chain()
            return
        if name in {"SHA256", "float8send"} or next_token in {"(", "["}:
            raise _RecipeGrammarError

    def _expression(self) -> None:
        token = self._peek()
        if token is None:
            raise _RecipeGrammarError
        if token == '"':
            self._json_string()
            return
        if token == "[":
            self._sequence("[", "]", allow_empty=True)
            return
        if token == "(":
            self._sequence("(", ")", allow_empty=False)
            return
        if token.isalpha() or token == "_":
            self._named_expression()
            return
        raise _RecipeGrammarError


class MaterializationError(RuntimeError):
    """Stable, operator-safe candidate construction failure."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(code if detail is None else f"{code}:{detail}")


class IdentityHashDependencyError(MaterializationError):
    """Stable failure for the whole-artifact identity/hash graph."""


def _fail(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code, detail)


def _graph_fail(code: str, detail: str | None = None) -> Never:
    raise IdentityHashDependencyError(code, detail)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MaterializationError("TASK8_V26_CANONICAL_VALUE_INVALID") from exc


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _render(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MaterializationError("TASK8_V26_CANONICAL_VALUE_INVALID") from exc


def _decode(raw: bytes, code: str) -> dict[str, Any]:
    def reject_nonfinite(_value: str) -> Never:
        raise ValueError("NONFINITE_JSON")

    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=reject_nonfinite)
    except (UnicodeError, ValueError) as exc:
        raise MaterializationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _without_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "expected_sha256"}


def _read_exact(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MaterializationError(code) from exc


def validate_frozen_parent_bytes(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _V21_ARTIFACT_SHA256:
        _fail("TASK8_V26_PARENT_MUST_BE_FROZEN_V21")
    value = _decode(raw, "TASK8_V26_PARENT_MUST_BE_FROZEN_V21")
    if (
        value.get("metadata", {}).get("pipeline_contract_version") != "full-strict-v21"
        or value.get("metadata", {}).get("append_frozen") is not True
    ):
        _fail("TASK8_V26_PARENT_MUST_BE_FROZEN_V21")
    return value


def _validate_policy_bytes(raw: bytes) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != _POLICY_V2_ARTIFACT_SHA256:
        _fail("TASK8_V26_POLICY_MUST_BE_FROZEN_V2")
    value = _decode(raw, "TASK8_V26_POLICY_MUST_BE_FROZEN_V2")
    if (
        value.get("metadata", {}).get("policy_id") != "fdd-formula-policy-v2"
        or value.get("digest_recipe_registry", {}).get("fdd_formula_policy_v2_sha256")
        != _POLICY_V2_SHA256
    ):
        _fail("TASK8_V26_POLICY_MUST_BE_FROZEN_V2")
    return value


def _validate_reviewer_map(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != _POLICY_V2_REVIEWER_MAP_SHA256:
        _fail("TASK8_V26_REVIEWER_MAP_BYTES_DRIFT")
    return raw


def _load_approved_delta_helper() -> ModuleType:
    raw = _read_exact(_APPROVED_DELTA_HELPER, "TASK8_V26_APPROVED_DELTA_HELPER_MISSING")
    if hashlib.sha256(raw).hexdigest() != _APPROVED_DELTA_HELPER_SHA256:
        _fail("TASK8_V26_APPROVED_DELTA_HELPER_DRIFT")
    spec = importlib.util.spec_from_file_location(
        "_task8_v26_approved_delta_helper", _APPROVED_DELTA_HELPER
    )
    if spec is None or spec.loader is None:
        _fail("TASK8_V26_APPROVED_DELTA_HELPER_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise MaterializationError("TASK8_V26_APPROVED_DELTA_HELPER_INVALID") from exc
    return module


def _replace_tree(value: object, replacements: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        return {
            str(_replace_tree(key, replacements)): _replace_tree(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_tree(child, replacements) for child in value]
    if isinstance(value, str):
        result = value
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result
    return value


def _retarget_candidate_tree(baseline: Mapping[str, Any]) -> dict[str, Any]:
    result = _replace_tree(
        copy.deepcopy(dict(baseline)),
        {
            "full-strict-v22": _V26,
            "full_strict_v22": "full_strict_v26",
            "pipeline-contract-v22": "pipeline-contract-v26",
            "task8-v22": "task8-v26",
            "task8_v22": "task8_v26",
            "Task8V22": "Task8V26",
            "fdd-formula-policy-v1": "fdd-formula-policy-v2",
            "fdd_formula_policy_v1": "fdd_formula_policy_v2",
            _SUPERSEDED_POLICY_SHA256: _POLICY_V2_SHA256,
            "dashboard-root-equation-v2": "dashboard-root-equation-v3",
            "idis_task8_dashboard_root_equation_v2": "idis_task8_dashboard_root_equation_v3",
            "SEMANTIC_V22": "SEMANTIC_TASK8_APPROVED",
        },
    )
    if not isinstance(result, dict):
        _fail("TASK8_V26_APPROVED_DELTA_INVALID")
    return result


def _retarget_metadata(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK8_V26_REVIEWED_DESIGN_INVALID") from exc
    replaced = _replace_tree(
        design,
        {
            "full-strict-v22": _V26,
            "full_strict_v22": "full_strict_v26",
            "task8-v22": "task8-v26",
            "task8_v22": "task8_v26",
            "fdd-formula-policy-v1": "fdd-formula-policy-v2",
            "fdd_formula_policy_v1": "fdd_formula_policy_v2",
            _SUPERSEDED_POLICY_SHA256: _POLICY_V2_SHA256,
            "SEMANTIC_V22": "SEMANTIC_TASK8_APPROVED",
        },
    )
    if not isinstance(replaced, list):
        _fail("TASK8_V26_REVIEWED_DESIGN_INVALID")
    replaced.append(
        [
            "v26_parent_provenance",
            [
                "parent_pipeline_contract_version",
                "full-strict-v21",
                "parent_artifact_sha256",
                _V21_ARTIFACT_SHA256,
                "parent_pipeline_contract_hash",
                _V21_CONTRACT_SHA256,
            ],
        ]
    )
    replaced.append(
        ["v26_approved_delta_manifest", [list(row) for row in _APPROVED_DELTA_MANIFEST]]
    )
    rendered = _canonical_bytes(replaced)
    metadata["artifact_schema"] = _V26_SCHEMA
    metadata["pipeline_contract_version"] = _V26
    metadata["append_frozen"] = True
    metadata["activation_status"] = "INACTIVE"
    metadata["reviewed_design_sha256"] = hashlib.sha256(rendered).hexdigest()
    metadata["reviewed_design_utf8_hex"] = rendered.hex()
    for name in ("task8_fdd_contract", "task8_structural_admission"):
        if name not in metadata["section_order"]:
            metadata["section_order"].append(name)


def _policy_requirement_rows(policy: Mapping[str, Any]) -> list[list[object]]:
    try:
        raw_rows = policy["methodology_transition"]["successor_required_map"]
        rows = [[row["formula_ordinal"], row["formula_type"], row["scope"]] for row in raw_rows]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK8_V26_REQUIREMENT_LITERAL_DRIFT") from exc
    if rows != [list(row) for row in _POLICY_REQUIREMENT_TUPLE] or any(
        row.get("required") is not True for row in raw_rows
    ):
        _fail("TASK8_V26_REQUIREMENT_LITERAL_DRIFT")
    return rows


def _requirement_contract(policy: Mapping[str, Any]) -> dict[str, Any]:
    rows = _policy_requirement_rows(policy)
    return {
        "schema_version": _REQUIREMENT_SCHEMA,
        "discriminator_field": "methodology_version_id",
        "policy_v2_methodology_version_id": _POLICY_V2_METHODOLOGY_VERSION_ID,
        "policy_id": "fdd-formula-policy-v2",
        "policy_sha256": _POLICY_V2_SHA256,
        "policy_v2_requirement_key_tuple": list(_POLICY_KEYS),
        "legacy_requirement_key_tuple": list(_LEGACY_KEYS),
        "policy_v2_requirement_literal_tuple": rows,
        "policy_v2_requirement_literal_sha256": _canonical_sha256(
            [_REQUIREMENT_LITERAL_DOMAIN, _POLICY_V2_SHA256, rows]
        ),
        "calculation_column": "deterministic_calculations.calc_type",
        "policy_v2_match_field": "formula_type",
        "legacy_match_field": "calc_type",
        "stable_rejection_code": _STABLE_DASHBOARD_CODE,
        "negative_case_tuple": list(_REQUIREMENT_NEGATIVE_CASES),
        "normative_source_mode": "HASH_BOUND_ARTIFACT_LITERAL_ONLY",
        "runtime_policy_read_allowed": False,
        "field_alias_allowed": False,
        "adapter_allowed": False,
        "coercion_allowed": False,
    }


def _sql_literal(value: str) -> str:
    if not value or "'" in value or "\\" in value:
        _fail("TASK8_V26_REQUIREMENT_LITERAL_DRIFT")
    return f"'{value}'"


def _values_sql(contract: Mapping[str, Any]) -> str:
    rows = contract.get("policy_v2_requirement_literal_tuple")
    if rows != [list(row) for row in _POLICY_REQUIREMENT_TUPLE]:
        _fail("TASK8_V26_REQUIREMENT_LITERAL_DRIFT")
    return ",".join(
        f"({ordinal},{_sql_literal(formula_type)},{_sql_literal(scope)})"
        for ordinal, formula_type, scope in rows
    )


def render_dashboard_requirement_guard(contract: Mapping[str, Any]) -> str:
    """Render the governed shape/tuple guard from artifact literals only."""

    version = contract.get("policy_v2_methodology_version_id")
    if version != _POLICY_V2_METHODOLOGY_VERSION_ID:
        _fail("TASK8_V26_REQUIREMENT_LITERAL_DRIFT")
    values = _values_sql(contract)
    code = contract.get("stable_rejection_code")
    if code != _STABLE_DASHBOARD_CODE:
        _fail("TASK8_V26_REQUIREMENT_CONTRACT_DRIFT")
    return (
        "IF dashboard_row.methodology_version_id IS DISTINCT FROM "
        "dashboard_row.coverage_methodology_version_id OR jsonb_typeof("
        "dashboard_row.question_payload->'required_calculations') IS DISTINCT FROM 'array' "
        "OR EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "jsonb_typeof(item) IS DISTINCT FROM 'object' OR ("
        f"dashboard_row.methodology_version_id={_sql_literal(str(version))} AND ("
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
        f"OR (dashboard_row.methodology_version_id<>{_sql_literal(str(version))} AND ("
        "CASE WHEN jsonb_typeof(item)='object' THEN (SELECT count(*) FROM "
        "jsonb_object_keys(item)) ELSE -1 END<>2 OR NOT (item ?& "
        "ARRAY['calc_type','required']) OR jsonb_typeof(item->'calc_type') IS DISTINCT "
        "FROM 'string' OR item->>'calc_type'='' OR jsonb_typeof(item->'required') IS "
        "DISTINCT FROM 'boolean'))) OR ("
        f"dashboard_row.methodology_version_id={_sql_literal(str(version))} AND EXISTS ("
        "SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item GROUP BY "
        "item->>'formula_ordinal',item->>'formula_type',item->>'scope' HAVING "
        "count(*)<>1)) THEN RAISE EXCEPTION "
        f"'{code}' USING ERRCODE='23514'; END IF;"
    )


def _requirement_type_match() -> str:
    version = _sql_literal(_POLICY_V2_METHODOLOGY_VERSION_ID)
    return (
        f"((dashboard_row.methodology_version_id={version} AND "
        "item->>'formula_type'=c.calc_type) OR (dashboard_row.methodology_version_id<>"
        f"{version} AND item->>'calc_type'=c.calc_type))"
    )


def _exact_calculation_guard() -> str:
    match = _requirement_type_match()
    return (
        "IF EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "item->'required'='true'::jsonb AND (SELECT count(*) FROM "
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
        f"{match} AND "
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
        f"item->'required'='true'::jsonb AND {match}))) "
        "THEN RAISE EXCEPTION "
        f"'{_STABLE_DASHBOARD_CODE}' "
        "USING ERRCODE='23514'; END IF;"
    )


def _decode_sql_tuple(row: Mapping[str, Any], field: str) -> list[str]:
    try:
        values = row[field]
        if not isinstance(values, list):
            _fail("TASK8_V26_SQL_HEX_INVALID")
        return [bytes.fromhex(str(value)).decode("utf-8") for value in values]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK8_V26_SQL_HEX_INVALID") from exc


def _function_body(statement: str) -> str:
    body = statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    if not body:
        _fail("TASK8_V26_DASHBOARD_FUNCTION_BODY_INVALID")
    return body


def _replace_once(value: str, old: str, new: str, code: str) -> str:
    if value.count(old) != 1:
        _fail(code)
    return value.replace(old, new, 1)


def _replace_segment(value: str, start: str, end: str, replacement: str, code: str) -> str:
    start_at = value.find(start)
    end_at = value.find(end, start_at)
    if start_at < 0 or end_at < 0:
        _fail(code)
    end_at += len(end)
    return value[:start_at] + replacement + value[end_at:]


def _rehash_acl_function(row: dict[str, Any]) -> None:
    row["expected_sha256"] = hashlib.sha256(
        _ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(_without_hash(row))
    ).hexdigest()


def _rehash_acl_role(role: dict[str, Any]) -> None:
    role["expected_sha256"] = hashlib.sha256(
        _ACL_ROLE_HASH_DOMAIN + _canonical_bytes(_without_hash(role))
    ).hexdigest()


def _rehash_acl_registry(registry: dict[str, Any]) -> None:
    registry["expected_sha256"] = _canonical_sha256(
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


def _rehash_acl_downgrade(row: dict[str, Any]) -> None:
    row["expected_sha256"] = _canonical_sha256(
        [
            "task7-guard-function-downgrade-authority-v1",
            _without_hash(row),
        ]
    )


def _retarget_dashboard_function_acl_hex(value: object) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and len(child) % 2 == 0:
                try:
                    decoded = bytes.fromhex(child).decode("utf-8")
                except (ValueError, UnicodeError):
                    pass
                else:
                    replaced = decoded.replace(
                        "idis_task8_dashboard_root_equation_v2",
                        "idis_task8_dashboard_root_equation_v3",
                    )
                    if replaced != decoded:
                        value[key] = replaced.encode("utf-8").hex()
                        changed += 1
                        continue
            changed += _retarget_dashboard_function_acl_hex(child)
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            if isinstance(child, str) and len(child) % 2 == 0:
                try:
                    decoded = bytes.fromhex(child).decode("utf-8")
                except (ValueError, UnicodeError):
                    pass
                else:
                    replaced = decoded.replace(
                        "idis_task8_dashboard_root_equation_v2",
                        "idis_task8_dashboard_root_equation_v3",
                    )
                    if replaced != decoded:
                        value[ordinal] = replaced.encode("utf-8").hex()
                        changed += 1
                        continue
            changed += _retarget_dashboard_function_acl_hex(child)
    return changed


def _question_payload_acl(statement: str) -> str:
    marker = " ON TABLE public.methodology_questions "
    if marker not in statement or not statement.startswith(("GRANT SELECT (", "REVOKE SELECT (")):
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    prefix, suffix = statement.split(marker, 1)
    opening = prefix.index("(")
    closing = prefix.rindex(")")
    columns = prefix[opening + 1 : closing].split(",")
    if "question_payload" not in columns:
        columns.append("question_payload")
    rendered = prefix[: opening + 1] + ",".join(sorted(columns)) + prefix[closing:]
    return rendered + marker + suffix


def _transform_question_acl_hex(values: list[str]) -> int:
    changed = 0
    for ordinal, encoded in enumerate(values):
        try:
            statement = bytes.fromhex(encoded).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise MaterializationError("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID") from exc
        if " ON TABLE public.methodology_questions " not in statement or not statement.startswith(
            ("GRANT SELECT (", "REVOKE SELECT (")
        ):
            continue
        replacement = _question_payload_acl(statement)
        if replacement != statement:
            values[ordinal] = replacement.encode("utf-8").hex()
            changed += 1
    return changed


def _expected_dashboard_projection_sql(projection_recipe: object) -> str:
    """Render the governed projection SQL from the frozen v2 recipe."""

    if not isinstance(projection_recipe, str):
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    match = re.fullmatch(
        r'H\(\["(?P<version>[a-z0-9-]+)",'
        r"(?P<row_fields>[a-z0-9_,]+),"
        r"ORDERED\[\((?P<root_fields>[a-z0-9_,]+)\)\]\]\)",
        projection_recipe,
    )
    if match is None:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    row_fields = tuple(match.group("row_fields").split(","))
    root_fields = tuple(match.group("root_fields").split(","))
    if row_fields != (
        "tenant_id",
        "deal_id",
        "run_id",
        "dashboard_row_id",
        "coverage_record_id",
        "row_disposition",
        "count",
    ) or root_fields != (
        "root_kind",
        "root_ordinal",
        "root_output_object_id",
        "root_output_object_kind",
        "dashboard_root_link_id",
    ):
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
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


def _install_projection_v2_sql(artifact: dict[str, Any]) -> None:
    registry = artifact["sql_authority_registry"]
    rows = [row for row in registry["rows"] if row["authority_id"] == "dashboard-root-equation-v3"]
    if len(rows) != 1:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    row = rows[0]
    statements = _decode_sql_tuple(row, "body_sql_utf8_hex_tuple")
    ordinals = [
        ordinal
        for ordinal, statement in enumerate(statements)
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
    ]
    if ordinals != [1]:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    ordinal = ordinals[0]
    statement = statements[ordinal]
    old_body = _function_body(statement)
    try:
        start = statement.index("preimage:=")
        end = statement.index("actual_hash:=", start)
    except ValueError as exc:
        raise MaterializationError("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT") from exc
    if "methodology-truth-dashboard-root-projection-v1" not in statement[start:end]:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    statements[ordinal] = (
        statement[:start] + _expected_dashboard_projection_sql(_PROJECTION_RECIPE) + statement[end:]
    )
    new_body = _function_body(statements[ordinal])
    old_hash = hashlib.sha256(old_body.encode("utf-8")).hexdigest()
    new_hash = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    row["body_sql_utf8_hex_tuple"] = [value.encode("utf-8").hex() for value in statements]
    row["expected_sha256"] = _canonical_sha256([_SQL_ROW_DOMAIN, _without_hash(row)])

    acl_registry = registry["guard_function_acl_registry"]
    acl_rows = [
        item
        for item in acl_registry["function_rows"]
        if item["authority_id"] == "dashboard-root-equation-v3"
    ]
    if len(acl_rows) != 1:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    replacements = 0
    for field in ("direct_read_column_tuple", "transitive_read_column_tuple"):
        for _relation, _columns, evidence_rows in acl_rows[0][field]:
            for evidence in evidence_rows:
                if evidence == ["SQL_BODY_SHA256", old_hash]:
                    evidence[1] = new_hash
                    replacements += 1
    if replacements == 0:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    _rehash_acl_function(acl_rows[0])
    _rehash_acl_registry(acl_registry)
    registry["expected_sha256"] = _canonical_sha256(
        [_SQL_REGISTRY_DOMAIN, registry["rows"], acl_registry]
    )


def _install_dashboard_equation_v3(
    artifact: dict[str, Any],
    contract: Mapping[str, Any],
    helper: ModuleType,
) -> None:
    registry = artifact["sql_authority_registry"]
    matches = [
        row for row in registry["rows"] if row["authority_id"] == "dashboard-root-equation-v3"
    ]
    if len(matches) != 1:
        _fail("TASK8_V26_DASHBOARD_AUTHORITY_CARDINALITY_INVALID")
    row = matches[0]
    statements = _decode_sql_tuple(row, "body_sql_utf8_hex_tuple")
    statements = [
        statement.replace(
            "idis_task8_dashboard_root_equation_v2",
            "idis_task8_dashboard_root_equation_v3",
        )
        for statement in statements
    ]
    function_ordinals = [
        ordinal
        for ordinal, statement in enumerate(statements)
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    if function_ordinals != [1]:
        _fail("TASK8_V26_DASHBOARD_FUNCTION_CARDINALITY_INVALID")
    function_ordinal = function_ordinals[0]
    statement = statements[function_ordinal]

    join = (
        "q.methodology_version_record_id=c.methodology_version_record_id AND "
        "q.methodology_question_record_id=c.methodology_question_record_id"
    )
    joined = join + " AND q.methodology_version_id=c.methodology_version_id"
    if statement.count(join) != 2:
        _fail("TASK8_V26_CROSS_VERSION_JOIN_BASE_INVALID")
    statement = statement.replace(join, joined)
    statement = _replace_once(
        statement,
        "q.question_payload,q.methodology_question_record_id FROM",
        (
            "q.question_payload,q.methodology_question_record_id,q.methodology_version_id,"
            "c.methodology_version_id AS coverage_methodology_version_id FROM"
        ),
        "TASK8_V26_CROSS_VERSION_JOIN_BASE_INVALID",
    )

    shape_starts = (
        (
            "IF jsonb_typeof(dashboard_row.question_payload->'required_calculations') "
            "IS DISTINCT FROM 'array'"
        ),
        ("IF jsonb_typeof(dashboard_row.question_payload->'required_calculations')<>'array'"),
    )
    shape_matches = [value for value in shape_starts if value in statement]
    if len(shape_matches) != 1:
        _fail("TASK8_V26_REQUIREMENT_SHAPE_BASE_INVALID")
    actual_shape_start = shape_matches[0]
    rejection_end = (
        f"THEN RAISE EXCEPTION '{_STABLE_DASHBOARD_CODE}' USING ERRCODE='23514'; END IF;"
    )
    statement = _replace_segment(
        statement,
        actual_shape_start,
        rejection_end,
        render_dashboard_requirement_guard(contract),
        "TASK8_V26_REQUIREMENT_SHAPE_BASE_INVALID",
    )

    calculation_required_start = "SELECT COALESCE(bool_or((item->>'required')::boolean),false)"
    start_at = statement.find(calculation_required_start)
    end_at = statement.find(";", start_at)
    if start_at < 0 or end_at < 0:
        _fail("TASK8_V26_CALCULATION_REQUIRED_BASE_INVALID")
    calculation_required = (
        "SELECT EXISTS (SELECT 1 FROM jsonb_array_elements("
        "dashboard_row.question_payload->'required_calculations') item WHERE "
        "item->'required'='true'::jsonb) INTO calculation_required;"
    )
    statement = statement[:start_at] + calculation_required + statement[end_at + 1 :]

    old_exact_guard = helper._dashboard_exact_calculation_guard()  # noqa: SLF001
    statement = _replace_once(
        statement,
        old_exact_guard,
        _exact_calculation_guard(),
        "TASK8_V26_CALCULATION_GUARD_BASE_INVALID",
    )
    old_root_join = (
        "JOIN jsonb_array_elements(dashboard_row.question_payload->'required_calculations') "
        "item ON (item->>'required')::boolean AND item->>'calc_type'=c.calc_type"
    )
    new_root_join = (
        "JOIN jsonb_array_elements(dashboard_row.question_payload->'required_calculations') "
        f"item ON item->'required'='true'::jsonb AND {_requirement_type_match()}"
    )
    statement = _replace_once(
        statement,
        old_root_join,
        new_root_join,
        "TASK8_V26_CALCULATION_ROOT_BASE_INVALID",
    )
    if statement != " ".join(statement.split()):
        _fail("TASK8_V26_SQL_NOT_CANONICAL")
    statements[function_ordinal] = statement
    row["body_sql_utf8_hex_tuple"] = [value.encode("utf-8").hex() for value in statements]
    row["authority_version"] = "3"
    for case_id in _REQUIREMENT_NEGATIVE_CASES:
        if case_id not in row["illegal_red_case_tuple"]:
            row["illegal_red_case_tuple"].append(case_id)
    row["expected_sha256"] = _canonical_sha256([_SQL_ROW_DOMAIN, _without_hash(row)])

    body_sha256 = hashlib.sha256(_function_body(statement).encode("utf-8")).hexdigest()
    acl = registry["guard_function_acl_registry"]
    if _retarget_dashboard_function_acl_hex(acl) == 0:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    acl_rows = [
        item
        for item in acl["function_rows"]
        if item["authority_id"] == "dashboard-root-equation-v3"
    ]
    if len(acl_rows) != 1:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    function_acl = acl_rows[0]
    for field in ("direct_read_column_tuple", "transitive_read_column_tuple"):
        for relation, columns, evidence in function_acl[field]:
            if relation == "methodology_questions":
                columns[:] = sorted({*columns, "methodology_version_id", "question_payload"})
            if field == "direct_read_column_tuple":
                if not isinstance(evidence, list) or evidence[:1] != ["SQL_BODY_SHA256"]:
                    _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
                evidence[1] = body_sha256
            else:
                for receipt in evidence:
                    if not isinstance(receipt, list) or receipt[:1] != ["SQL_BODY_SHA256"]:
                        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
                    receipt[1] = body_sha256
    _rehash_acl_function(function_acl)

    role = acl["guard_owner_role"]
    relation_columns = dict(role["select_relation_column_tuple"])
    if "methodology_questions" not in relation_columns:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID")
    relation_columns["methodology_questions"] = sorted(
        {*relation_columns["methodology_questions"], "question_payload"}
    )
    role["select_relation_column_tuple"] = [
        [relation, relation_columns[relation]]
        for relation, _columns in role["select_relation_column_tuple"]
    ]

    changed = 0
    changed += _transform_question_acl_hex(role["acl_sql_utf8_hex_tuple"])
    changed += _transform_question_acl_hex(acl["current_column_grant_sql_utf8_hex_tuple"])
    changed += _transform_question_acl_hex(acl["current_column_revoke_sql_utf8_hex_tuple"])
    changed += _transform_question_acl_hex(acl["installation_sql_utf8_hex_tuple"])
    changed += _transform_question_acl_hex(
        acl["downgrade_authority"]["column_select_revoke_sql_utf8_hex_tuple"]
    )
    changed += _transform_question_acl_hex(acl["downgrade_authority"]["ordered_sql_utf8_hex_tuple"])
    if changed != 8:
        _fail("TASK8_V26_DASHBOARD_ACL_CLOSURE_INVALID", str(changed))
    _rehash_acl_role(role)
    _rehash_acl_downgrade(acl["downgrade_authority"])
    _rehash_acl_registry(acl)
    registry["expected_sha256"] = _canonical_sha256([_SQL_REGISTRY_DOMAIN, registry["rows"], acl])


def _rehash_policy_binding(artifact: dict[str, Any]) -> None:
    binding = artifact["task8_fdd_contract"]["policy_binding"]
    ordered = [
        binding.get("policy_id"),
        binding.get("artifact_sha256"),
        binding.get("policy_sha256"),
        binding.get("reviewer_map_relative_path"),
        binding.get("reviewer_map_sha256"),
    ]
    if any(not isinstance(value, str) or not value for value in ordered):
        _fail("TASK8_V26_POLICY_BINDING_INVALID")
    binding["binding_sha256"] = _canonical_sha256([_POLICY_BINDING_DOMAIN, ordered])


def _rehash_fdd_schemas(artifact: dict[str, Any], helper: ModuleType) -> None:
    contracts = artifact["task8_fdd_contract"]["fdd_schema_contracts"]
    helper._validate_executable_schema_contracts(tuple(contracts))  # noqa: SLF001
    rows = artifact["schema_registry"]["rows"]
    rows_by_key = {(row["schema_id"], row["schema_version"]): row for row in rows}
    for contract in contracts:
        schema_id = contract["schema_id"]
        schema_version = contract["schema_version"]
        locator = f"task8-literal:{schema_id}:{schema_version}"
        paths = [["$", schema_id, schema_version]]
        digest = _canonical_sha256(
            [
                "typed-schema-definition-v1",
                schema_id,
                schema_version,
                locator,
                contract["normalized_schema_snapshot"],
                paths,
            ]
        )
        contract["expected_schema_sha256"] = digest
        registry_row = rows_by_key[(schema_id, schema_version)]
        registry_row["normalized_schema_snapshot"] = copy.deepcopy(
            contract["normalized_schema_snapshot"]
        )
        registry_row["expected_schema_sha256"] = digest
    artifact["schema_registry"]["expected_sha256"] = _canonical_sha256(
        ["full-strict-v26-schema-registry-v1", rows]
    )


def _retarget_digest_and_registry_hashes(artifact: dict[str, Any], helper: ModuleType) -> None:
    digest_rows = artifact["digest_recipe_registry"]["rows"]
    by_name = {row[0]: row for row in digest_rows}
    required = {
        "pipeline_contract_hash",
        "methodology_truth_dashboard_root_projection_sha256",
        "fdd_formula_policy_v2_sha256",
        "task8_fdd_successor_contract_sha256",
    }
    if not required.issubset(by_name):
        _fail("TASK8_V26_DIGEST_AUTHORITY_INVALID")
    by_name["pipeline_contract_hash"][1] = (
        '["pipeline-contract-v26","full-strict-v26",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    by_name["methodology_truth_dashboard_root_projection_sha256"][1] = _PROJECTION_RECIPE
    by_name["fdd_formula_policy_v2_sha256"][1] = (
        'H(["fdd-formula-policy-v2",ORDERED[component_hashes]])'
    )
    by_name["task8_fdd_successor_contract_sha256"][1] = (
        'H(["full-strict-v26-task8-fdd-contract-v2",contract])'
    )
    conservation_id = "task8_v26_approved_semantics_conservation_sha256"
    if conservation_id in by_name:
        _fail("TASK8_V26_DIGEST_AUTHORITY_INVALID")
    digest_rows.append(
        [
            conservation_id,
            'H(["task8-v26-approved-semantics-conservation-v1",manifest])',
        ]
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        digest_rows
    ).hex()

    uuid_rows = artifact["uuid_namespace_registry"]["rows"]
    if any(row[0] == "dashboard_row_id" for row in uuid_rows):
        _fail("TASK8_V26_DASHBOARD_ROW_UUID_AUTHORITY_INVALID")
    uuid_rows.append(
        ["dashboard_row_id", _DASHBOARD_ROW_NAMESPACE, list(_DASHBOARD_ROW_NAME_TUPLE)]
    )
    artifact["uuid_namespace_registry"]["expected_sha256"] = _canonical_sha256(
        ["task7-uuid-namespace-registry-v1", uuid_rows]
    )

    binding = artifact["task8_fdd_contract"]["policy_binding"]
    binding.update(
        {
            "policy_id": "fdd-formula-policy-v2",
            "artifact_relative_path": "contracts/fdd_formula_policy_v2.json",
            "artifact_sha256": _POLICY_V2_ARTIFACT_SHA256,
            "policy_sha256": _POLICY_V2_SHA256,
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json"
            ),
            "fixture_sha256": _POLICY_V2_FIXTURE_SHA256,
            "fixture_policy_sha256": _POLICY_V2_SHA256,
            "reviewer_map_relative_path": _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
            "reviewer_map_sha256": _POLICY_V2_REVIEWER_MAP_SHA256,
        }
    )
    _rehash_policy_binding(artifact)
    artifact["task8_fdd_contract"]["activation_assertions"][
        "migration_consumable_before_second_review"
    ] = False

    tuple_rows = artifact["tuple_schema_registry"]["rows"]
    structural_row = ["Task8V26StructuralAdmissionV1", list(_STRUCTURAL_FIELDS)]
    if structural_row in tuple_rows or any(row[0] == structural_row[0] for row in tuple_rows):
        _fail("TASK8_V26_TUPLE_SCHEMA_INVALID")
    tuple_rows.append(structural_row)
    _rehash_fdd_schemas(artifact, helper)


def _rehash_task8(artifact: dict[str, Any]) -> None:
    section = artifact["task8_fdd_contract"]
    section["expected_sha256"] = _canonical_sha256(
        [
            "full-strict-v26-task8-fdd-contract-v2",
            {key: value for key, value in section.items() if key != "expected_sha256"},
        ]
    )


def _json_pointer(parts: Sequence[str | int]) -> str:
    return "#/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _walk_declarations(
    value: object,
    *,
    artifact_name: str,
    path: tuple[str | int, ...] = (),
) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            lowered = key.lower()
            if "preimage" in lowered or "uuid_name_tuple" in lowered:
                result.add(f"{artifact_name}:{_json_pointer(child_path)}")
            result.update(_walk_declarations(child, artifact_name=artifact_name, path=child_path))
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            result.update(
                _walk_declarations(
                    child,
                    artifact_name=artifact_name,
                    path=(*path, ordinal),
                )
            )
    return result


def _discover_sources(full_strict: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    pointers = _walk_declarations(full_strict, artifact_name="full_strict")
    pointers.update(_walk_declarations(policy, artifact_name="fdd_formula_policy"))
    for section_name, width in (
        ("uuid_namespace_registry", 3),
        ("digest_recipe_registry", 2),
    ):
        rows = full_strict.get(section_name, {}).get("rows", [])
        if not isinstance(rows, list):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        for ordinal, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != width:
                _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
            pointers.add(f"full_strict:#/{section_name}/rows/{ordinal}")
    return tuple(sorted(pointers))


def _graph_pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    try:
        artifact_name, encoded = pointer.split(":#/", 1)
    except ValueError as exc:
        raise IdentityHashDependencyError("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID") from exc
    if artifact_name not in {"full_strict", "fdd_formula_policy"}:
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    value: object = full_strict if artifact_name == "full_strict" else policy
    try:
        for raw in encoded.split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IdentityHashDependencyError("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID") from exc
    return value


def _relative_pointer_value(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    if not pointer.startswith("#/"):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    current = value
    try:
        for raw in pointer[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IdentityHashDependencyError("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID") from exc
    return current


def _validate_recipe_grammar(producer_row: Sequence[str], value: object) -> None:
    if producer_row[6] != "#/1":
        return
    recipe = _relative_pointer_value(value, producer_row[6])
    if not isinstance(recipe, str):
        _graph_fail("TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID")
    try:
        _RecipeParser(recipe).parse()
    except _RecipeGrammarError:
        _graph_fail("TASK8_IDENTITY_HASH_RECIPE_GRAMMAR_INVALID")


def _producer_authority_row(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[str]:
    value = _graph_pointer_value(pointer, full_strict, policy)
    if pointer.startswith("full_strict:#/digest_recipe_registry/rows/"):
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
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
        if not isinstance(value, list) or len(value) != 3 or not isinstance(value[0], str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
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
    if pointer.startswith("full_strict:#/applicability_registry/rows/"):
        # The pointer selects hash_preimage, so classify it through its
        # containing applicability row.
        row_pointer = pointer.rsplit("/hash_preimage", 1)[0]
        parent = _graph_pointer_value(row_pointer, full_strict, policy)
        if not isinstance(parent, dict) or not isinstance(parent.get("predicate_id"), str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        predicate = parent["predicate_id"]
        return [
            pointer,
            "full_strict",
            "CONTRACT_DEFINITION",
            predicate,
            "APPLICABILITY_HASH",
            f"{predicate.lower()}_sha256",
            "#",
        ]
    if (
        "/future_child_authority_registry/" in pointer
        or "/future_reference_authority_registry/" in pointer
    ):
        section = (
            "future_child_authority_registry"
            if "/future_child_authority_registry/" in pointer
            else "future_reference_authority_registry"
        )
        row_pointer = pointer.rsplit("/uuid_name_tuple_schema", 1)[0]
        parent = _graph_pointer_value(row_pointer, full_strict, policy)
        if not isinstance(parent, dict) or not isinstance(parent.get("table_name"), str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        table = parent["table_name"]
        return [
            pointer,
            "full_strict",
            "CONTRACT_DEFINITION",
            table,
            (
                "FUTURE_CHILD_UUID_AUTHORITY"
                if section == "future_child_authority_registry"
                else "FUTURE_REFERENCE_UUID_AUTHORITY"
            ),
            f"{table}_uuid",
            "#",
        ]
    policy_symbols = {
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
    if pointer in policy_symbols:
        layer, scope, output_kind, symbol = policy_symbols[pointer]
        return [pointer, "fdd_formula_policy", layer, scope, output_kind, symbol, "#"]
    partial_symbols = {
        "dashboard_native_identity_preimage": "native_identity_sha256",
        "dashboard_native_output_preimage": "native_output_sha256",
    }
    for suffix, symbol in partial_symbols.items():
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
    _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN", pointer)


def _producer_node(row: Sequence[str]) -> list[str]:
    return [row[1], row[2], row[3], row[4], row[5], row[0]]


def _validate_producer_symbols(
    producer_rows: Sequence[Sequence[str]],
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
) -> None:
    try:
        frozen_operator_value = _graph_pointer_value(
            _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW[0],
            full_strict_artifact,
            policy_artifact,
        )
    except IdentityHashDependencyError:
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID")
    if frozen_operator_value != list(_FROZEN_DIGEST_OPERATOR_VALUE) or not any(
        tuple(row) == _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW for row in producer_rows
    ):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID")
    for row in producer_rows:
        if len(row) != 7:
            _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
        symbol = row[5]
        if symbol in _RESERVED_DOLLAR_PRODUCER_SYMBOLS:
            _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID")
        if symbol in _PRODUCER_RESERVED_TOKENS:
            if tuple(row) != _FROZEN_DIGEST_OPERATOR_PRODUCER_ROW:
                _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID")
            continue
        if _IDENTIFIER_RE.fullmatch(symbol) is None:
            _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_SYMBOL_INVALID")


def _recipe_identifiers(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(_IDENTIFIER_RE.findall(value)) - _GRAMMAR_OPERATOR_TOKENS


def _governed_scalar_references(
    value: object,
    *,
    source_pointer: str,
    token_path: str,
    source_layer: str,
    producer_symbols: set[str],
) -> tuple[tuple[str, str], ...]:
    """Classify the closed nine-dollar-source contract after producer closure."""

    location = (source_pointer, token_path)
    expected_bound = _BOUND_RUNTIME_INPUT_BY_LOCATION.get(location)
    expected_token = expected_bound
    if location == _ROOT_PROJECTION_DOLLAR_LOCATION:
        expected_token = _ROOT_PROJECTION_DOLLAR_TOKEN
    elif location == _POLICY_SHA_PLACEHOLDER_LOCATION:
        expected_token = _POLICY_SHA_PLACEHOLDER_TOKEN
    if expected_token is not None and value != expected_token:
        if (
            isinstance(value, str)
            and "$" in value
            and _BOUND_RUNTIME_INPUT_RE.fullmatch(value) is None
        ):
            _graph_fail("TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID")
        _graph_fail("TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID")
    if not isinstance(value, str):
        return ()
    if "$" in value and _BOUND_RUNTIME_INPUT_RE.fullmatch(value) is None:
        _graph_fail("TASK8_IDENTITY_HASH_BOUND_RUNTIME_INPUT_INVALID")
    if expected_bound is not None:
        if source_layer != "RUNTIME_VALUE":
            _graph_fail("TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID")
        return ((expected_bound, "BOUND_RUNTIME_INPUT"),)
    if location == _ROOT_PROJECTION_DOLLAR_LOCATION:
        bare_symbol = _ROOT_PROJECTION_DOLLAR_TOKEN[1:]
        if source_layer != "RUNTIME_VALUE" or bare_symbol not in producer_symbols:
            _graph_fail("TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID")
        return ((bare_symbol, _leaf_authority(bare_symbol)),)
    if location == _POLICY_SHA_PLACEHOLDER_LOCATION:
        bare_symbol = _POLICY_SHA_PLACEHOLDER_TOKEN[1:]
        if source_layer != "CONTRACT_DEFINITION":
            _graph_fail("TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID")
        return ((bare_symbol, _leaf_authority(bare_symbol)),)
    if "$" in value:
        _graph_fail("TASK8_IDENTITY_HASH_DOLLAR_SOURCE_CONTRACT_INVALID")
    return tuple(
        (identifier, _leaf_authority(identifier))
        for identifier in sorted(_recipe_identifiers(value))
    )


def _graph_node_id(node: Sequence[object]) -> str:
    return _canonical_bytes(list(node[:5])).decode("utf-8")


def _flatten(value: object, path: str = "$") -> tuple[tuple[str, object], ...]:
    if isinstance(value, dict):
        return tuple(
            item for key in sorted(value) for item in _flatten(value[key], f"{path}.{key}")
        )
    if isinstance(value, list):
        return tuple(
            item
            for ordinal, child in enumerate(value)
            for item in _flatten(child, f"{path}[{ordinal}]")
        )
    return ((path, value),)


def _leaf_authority(token: object) -> str:
    rendered = str(token)
    if rendered == "pipeline_contract_hash":
        return "PINNED_CONTRACT_INPUT"
    if rendered == "pipeline_contract_version":
        return "VERSIONED_CONTRACT_INPUT"
    if rendered in {"root_output_object_id", "root_output_object_kind"}:
        return "FOREIGN_ROOT_INPUT"
    if token is None or isinstance(token, (bool, int)):
        return "CONTRACT_LITERAL"
    return "SCALAR_OR_FOREIGN_INPUT"


def _cycle(adjacency: Mapping[str, set[str]]) -> tuple[str, ...] | None:
    complete: set[str] = set()
    active: list[str] = []
    positions: dict[str, int] = {}

    def visit(node: str) -> tuple[str, ...] | None:
        if node in positions:
            start = positions[node]
            return (*active[start:], node)
        if node in complete:
            return None
        positions[node] = len(active)
        active.append(node)
        for dependency in sorted(adjacency.get(node, set())):
            result = visit(dependency)
            if result is not None:
                return result
        active.pop()
        positions.pop(node)
        complete.add(node)
        return None

    for node in sorted(adjacency):
        result = visit(node)
        if result is not None:
            return result
    return None


def _derive_allowed_external_input_tuple(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
    producer_rows: Sequence[Sequence[str]],
) -> list[list[str]]:
    _validate_producer_symbols(
        producer_rows,
        full_strict_artifact=full_strict_artifact,
        policy_artifact=policy_artifact,
    )
    producer_symbols = {row[5] for row in producer_rows}
    rows: set[tuple[str, str, str, str]] = set()
    for row in producer_rows:
        pointer = row[0]
        value = _graph_pointer_value(pointer, full_strict_artifact, policy_artifact)
        _validate_recipe_grammar(row, value)
        payload = _relative_pointer_value(value, row[6])
        for token_path, scalar in _flatten(payload):
            for identifier, authority in _governed_scalar_references(
                scalar,
                source_pointer=pointer,
                token_path=token_path,
                source_layer=row[2],
                producer_symbols=producer_symbols,
            ):
                if identifier not in producer_symbols:
                    rows.add((pointer, token_path, identifier, authority))
    return sorted((list(row) for row in rows), key=_canonical_bytes)


def _build_identity_hash_dependency_contract(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    pointers = _discover_sources(artifact, policy)
    producer_rows = [_producer_authority_row(pointer, artifact, policy) for pointer in pointers]
    _validate_producer_symbols(
        producer_rows,
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    producer_sha256 = _canonical_sha256([_GRAPH_PRODUCER_DOMAIN, producer_rows])
    if _V26_GRAPH_PRODUCER_SHA256 and producer_sha256 != _V26_GRAPH_PRODUCER_SHA256:
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_DRIFT")
    external_rows = _derive_allowed_external_input_tuple(
        full_strict_artifact=artifact,
        policy_artifact=policy,
        producer_rows=producer_rows,
    )
    external_sha256 = _canonical_sha256([_GRAPH_EXTERNAL_DOMAIN, external_rows])
    if _V26_GRAPH_EXTERNAL_INPUT_SHA256 and external_sha256 != _V26_GRAPH_EXTERNAL_INPUT_SHA256:
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_DRIFT")
    return {
        "schema_version": _GRAPH_SCHEMA,
        "source_discovery": "WHOLE_ARTIFACT_UUID_AND_HASH_PREIMAGE_DECLARATIONS",
        "recipe_grammar_tuple": ["H", "CJ", "ORDERED", "TUPLE", "SCALAR"],
        "producer_authority_schema": [
            "source_pointer",
            "artifact_name",
            "layer",
            "scope",
            "output_kind",
            "symbol",
            "dependency_payload_pointer",
        ],
        "producer_authority_tuple": producer_rows,
        "producer_authority_sha256": producer_sha256,
        "allowed_external_input_schema": [
            "source_pointer",
            "token_path",
            "symbol",
            "authority",
        ],
        "allowed_external_input_tuple": external_rows,
        "allowed_external_input_sha256": external_sha256,
        "dashboard_special_case_allowed": False,
        "synthetic_symbol_special_case_allowed": False,
        "rejected_non_dashboard_cycle_disposition": ("SYNTHETIC_GUARD_BYPASS_MUTATION_ONLY"),
    }


def derive_identity_hash_dependency_graph(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive every UUID-name/hash-preimage edge without domain exceptions."""

    pointers = _discover_sources(full_strict_artifact, policy_artifact)
    section = full_strict_artifact.get("task8_structural_admission")
    contract = (
        section.get("identity_hash_dependency_contract") if isinstance(section, Mapping) else None
    )
    if not isinstance(contract, Mapping) or contract.get("schema_version") != _GRAPH_SCHEMA:
        _graph_fail("TASK8_IDENTITY_HASH_AUTHORITY_MANIFEST_MISSING")
    raw_producer_rows = contract.get("producer_authority_tuple")
    if not isinstance(raw_producer_rows, list):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    producer_rows: list[list[str]] = []
    for row in raw_producer_rows:
        if (
            not isinstance(row, list)
            or len(row) != 7
            or any(not isinstance(value, str) for value in row)
            or any(not value for ordinal, value in enumerate(row) if ordinal != 5)
        ):
            _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
        producer_rows.append(row)
    _validate_producer_symbols(
        producer_rows,
        full_strict_artifact=full_strict_artifact,
        policy_artifact=policy_artifact,
    )
    manifest_pointers = [row[0] for row in producer_rows]
    if len(set(manifest_pointers)) != len(manifest_pointers):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_INVALID")
    symbols = [row[5] for row in producer_rows]
    if len(set(symbols)) != len(symbols):
        _graph_fail("TASK8_IDENTITY_HASH_DUPLICATE_PRODUCER")
    if tuple(sorted(manifest_pointers)) != pointers:
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_CENSUS_DRIFT")
    expected_producers = [
        _producer_authority_row(pointer, full_strict_artifact, policy_artifact)
        for pointer in pointers
    ]
    _validate_producer_symbols(
        expected_producers,
        full_strict_artifact=full_strict_artifact,
        policy_artifact=policy_artifact,
    )
    producer_sha256 = _canonical_sha256([_GRAPH_PRODUCER_DOMAIN, producer_rows])
    if (
        producer_rows != expected_producers
        or contract.get("producer_authority_sha256") != producer_sha256
        or (_V26_GRAPH_PRODUCER_SHA256 and producer_sha256 != _V26_GRAPH_PRODUCER_SHA256)
    ):
        _graph_fail("TASK8_IDENTITY_HASH_PRODUCER_AUTHORITY_DRIFT")

    raw_external_rows = contract.get("allowed_external_input_tuple")
    if not isinstance(raw_external_rows, list):
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_INVALID")
    external_rows: list[tuple[str, str, str, str]] = []
    for row in raw_external_rows:
        if (
            not isinstance(row, list)
            or len(row) != 4
            or any(not isinstance(value, str) or not value for value in row)
        ):
            _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_INVALID")
        external_rows.append((row[0], row[1], row[2], row[3]))
    external_sha256 = _canonical_sha256(
        [_GRAPH_EXTERNAL_DOMAIN, [list(row) for row in external_rows]]
    )
    if (
        len(set(external_rows)) != len(external_rows)
        or contract.get("allowed_external_input_sha256") != external_sha256
        or (
            _V26_GRAPH_EXTERNAL_INPUT_SHA256 and external_sha256 != _V26_GRAPH_EXTERNAL_INPUT_SHA256
        )
    ):
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_DRIFT")
    allowed_external = set(external_rows)

    nodes = [_producer_node(row) for row in producer_rows]
    values: dict[str, object] = {}
    rows_by_pointer = {row[0]: row for row in producer_rows}
    for pointer in pointers:
        value = _graph_pointer_value(pointer, full_strict_artifact, policy_artifact)
        _validate_recipe_grammar(rows_by_pointer[pointer], value)
        values[pointer] = value

    by_id: dict[str, list[str]] = {}
    producers: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _graph_node_id(node)
        if node_id in by_id:
            _graph_fail("TASK8_IDENTITY_HASH_CONFLICTING_PRODUCER", node[4])
        by_id[node_id] = node
        producers[node[4]] = node
    producer_symbols = set(producers)

    edges: set[tuple[str, str, str]] = set()
    leaves: set[tuple[str, str, str, str, str]] = set()
    consumed_external: set[tuple[str, str, str, str]] = set()
    for producer_row, node in zip(producer_rows, nodes, strict=True):
        derived_id = _graph_node_id(node)
        pointer = node[5]
        payload = _relative_pointer_value(values[pointer], producer_row[6])
        for token_path, scalar in _flatten(payload):
            references = _governed_scalar_references(
                scalar,
                source_pointer=pointer,
                token_path=token_path,
                source_layer=producer_row[2],
                producer_symbols=producer_symbols,
            )
            if not references:
                leaves.add((derived_id, token_path, str(scalar), _leaf_authority(scalar), pointer))
                continue
            for identifier, authority in references:
                receipt = f"{pointer}:{token_path}#{identifier}"
                dependency = (
                    None
                    if authority == "BOUND_RUNTIME_INPUT"
                    else producers.get(identifier)
                )
                if dependency is not None:
                    dependency_id = _graph_node_id(dependency)
                    if dependency_id == derived_id:
                        _graph_fail("TASK8_IDENTITY_HASH_CYCLE", node[4])
                    edges.add((derived_id, dependency_id, receipt))
                    continue
                external = (pointer, token_path, identifier, authority)
                if external not in allowed_external:
                    _graph_fail(
                        "TASK8_IDENTITY_HASH_GOVERNED_REFERENCE_UNRESOLVED",
                        identifier,
                    )
                consumed_external.add(external)
                leaves.add(
                    (
                        derived_id,
                        (
                            token_path
                            if authority == "BOUND_RUNTIME_INPUT"
                            else f"{token_path}#{identifier}"
                        ),
                        identifier,
                        external[3],
                        pointer,
                    )
                )
    if consumed_external != allowed_external:
        _graph_fail("TASK8_IDENTITY_HASH_EXTERNAL_INPUT_ORPHANED")

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for derived, dependency_node_id, _receipt in edges:
        adjacency[derived].add(dependency_node_id)
    found_cycle = _cycle(adjacency)
    if found_cycle is not None:
        _graph_fail("TASK8_IDENTITY_HASH_CYCLE", "->".join(found_cycle))

    node_tuple = sorted(nodes, key=_canonical_bytes)
    edge_tuple = sorted((list(row) for row in edges), key=_canonical_bytes)
    leaf_tuple = sorted((list(row) for row in leaves), key=_canonical_bytes)
    source_pointer_tuple = list(pointers)
    graph = {
        "schema_version": _GRAPH_SCHEMA,
        "node_tuple": node_tuple,
        "edge_tuple": edge_tuple,
        "leaf_tuple": leaf_tuple,
        "source_pointer_tuple": source_pointer_tuple,
        "counts": {
            "node_count": len(node_tuple),
            "edge_count": len(edge_tuple),
            "leaf_count": len(leaf_tuple),
            "source_pointer_count": len(source_pointer_tuple),
        },
    }
    graph["graph_sha256"] = _canonical_sha256(
        [_GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    return graph


def _pointer_value(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    if not pointer.startswith("#/"):
        _fail("TASK8_V26_CONSERVATION_SELECTOR_INVALID")
    current = value
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise MaterializationError("TASK8_V26_CONSERVATION_SELECTOR_INVALID") from exc
    return current


def _refresh_conservation_manifest(
    artifact: dict[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> list[list[Any]]:
    def row_ordinal(
        rows: Sequence[Mapping[str, Any] | Sequence[Any]],
        *,
        field: int | str,
        value: object,
    ) -> int:
        matches = [
            ordinal
            for ordinal, row in enumerate(rows)
            if (
                isinstance(field, int)
                and isinstance(row, Sequence)
                and not isinstance(row, (str, bytes))
                and len(row) > field
                and row[field] == value
            )
            or (isinstance(field, str) and isinstance(row, Mapping) and row.get(field) == value)
        ]
        if len(matches) != 1:
            _fail("TASK8_V26_CONSERVATION_SELECTOR_INVALID", str(value))
        return matches[0]

    schema_ordinal = next(
        ordinal
        for ordinal, row in enumerate(artifact["task8_fdd_contract"]["fdd_schema_contracts"])
        if row["schema_id"] == "fdd-deterministic-calculation"
    )
    sql_ordinal = next(
        ordinal
        for ordinal, row in enumerate(artifact["sql_authority_registry"]["rows"])
        if row["authority_id"] == "dashboard-root-equation-v3"
    )
    uuid_rows = artifact["uuid_namespace_registry"]["rows"]
    uuid_ordinals = {
        name: row_ordinal(uuid_rows, field=0, value=name)
        for name in ("dashboard_row_id", "dashboard_root_link_id", "output_object_id")
    }
    projection_ordinal = next(
        ordinal
        for ordinal, row in enumerate(artifact["digest_recipe_registry"]["rows"])
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    )
    snapshot = artifact["task8_fdd_contract"]["fdd_schema_contracts"][schema_ordinal][
        "normalized_schema_snapshot"
    ]

    def full(locator: str) -> list[str]:
        return [
            "full_strict",
            locator,
            "CANONICAL_JSON_SHA256",
            _canonical_sha256(_pointer_value(artifact, locator)),
        ]

    def policy_receipt(locator: str) -> list[str]:
        return [
            "fdd_formula_policy",
            locator,
            "CANONICAL_JSON_SHA256",
            _canonical_sha256(_pointer_value(policy, locator)),
        ]

    def constraint_receipts(model_name: str, constraint_id: str) -> list[list[str]]:
        definition_rows = snapshot["$defs"][model_name]["x-idis-cross-field-constraint-tuple"]
        definition_ordinal = row_ordinal(
            definition_rows,
            field=0,
            value=constraint_id,
        )
        base = (
            f"#/task8_fdd_contract/fdd_schema_contracts/{schema_ordinal}/normalized_schema_snapshot"
        )
        locators = [
            (f"{base}/$defs/{model_name}/x-idis-cross-field-constraint-tuple/{definition_ordinal}")
        ]
        target_matches = [
            ordinal
            for ordinal, row in enumerate(snapshot["targets"])
            if isinstance(row, list) and len(row) == 2 and row[0] == model_name
        ]
        if len(target_matches) > 1:
            _fail("TASK8_V26_CONSERVATION_SELECTOR_INVALID", model_name)
        if target_matches:
            target_ordinal = target_matches[0]
            target_rows = snapshot["targets"][target_ordinal][1][
                "x-idis-cross-field-constraint-tuple"
            ]
            target_constraint_ordinal = row_ordinal(
                target_rows,
                field=0,
                value=constraint_id,
            )
            locators.append(
                f"{base}/targets/{target_ordinal}/1/"
                "x-idis-cross-field-constraint-tuple/"
                f"{target_constraint_ordinal}"
            )
        return [full(locator) for locator in locators]

    sql_locator = f"#/sql_authority_registry/rows/{sql_ordinal}"
    base = json.loads(_canonical_bytes(_CONSERVATION_BASE))
    manifest = [
        [
            *base[0],
            constraint_receipts(
                "FddCalcInputsV1",
                "economic-coordinate-date-order-v1",
            ),
        ],
        [
            *base[1],
            constraint_receipts(
                "FddNumericBindingV1",
                "positive-magnitude-source-nonnegative-v1",
            ),
        ],
        [
            *base[2],
            constraint_receipts(
                "FddNumericBindingV1",
                "numeric-binding-source-normalization-v1",
            ),
        ],
        [
            *base[3],
            [full("#/task8_fdd_contract/erasure_refusal_contract")],
        ],
        [*base[4], [full(sql_locator)]],
        [
            *base[5],
            [
                *[
                    full(f"#/uuid_namespace_registry/rows/{uuid_ordinals[name]}")
                    for name in (
                        "dashboard_row_id",
                        "dashboard_root_link_id",
                        "output_object_id",
                    )
                ],
                full(f"#/digest_recipe_registry/rows/{projection_ordinal}"),
                full("#/task8_fdd_contract/partial_exact_one_contract"),
                full("#/task8_structural_admission/identity_hash_dependency_contract"),
                full("#/task8_structural_admission/dashboard_sql_recipe_contract"),
                policy_receipt("#/financial_output_schema/dashboard_row_uuid_authority"),
                policy_receipt("#/financial_output_schema/row_payload_preimage"),
            ],
        ],
        [
            *base[6],
            [
                full("#/task8_fdd_contract/policy_binding"),
                policy_receipt("#"),
                [
                    "reviewer_map",
                    _POLICY_V2_REVIEWER_MAP_RELATIVE_PATH,
                    "RAW_FILE_SHA256",
                    hashlib.sha256(reviewer_map_raw).hexdigest(),
                ],
            ],
        ],
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
            [
                full(sql_locator),
                full("#/task8_fdd_contract/dashboard_contract/requirement_binding_contract"),
            ],
        ],
    ]
    return manifest


def _dashboard_sql_contract(
    policy: Mapping[str, Any],
    requirement: Mapping[str, Any],
) -> dict[str, Any]:
    guard = render_dashboard_requirement_guard(requirement)
    return {
        "authority_id": "dashboard-root-equation-v3",
        "projection_recipe_id": "methodology_truth_dashboard_root_projection_sha256",
        "projection_recipe_version": "methodology-truth-dashboard-root-projection-v2",
        "row_payload_policy_pointer": "#/financial_output_schema/row_payload_preimage",
        "row_payload_recipe_version": "financial-diligence-row-v2",
        "forbidden_self_field_tuple": [
            "run_output_object_id",
            "dashboard_output_object_id",
        ],
        "derivation_order_tuple": list(_ACYCLIC_DERIVATION_ORDER),
        "source_recipe_binding_tuple": [
            [
                "full_strict",
                "methodology_truth_dashboard_root_projection_sha256",
                _canonical_sha256(_PROJECTION_RECIPE),
            ],
            [
                "fdd_formula_policy",
                "#/financial_output_schema/row_payload_preimage",
                _canonical_sha256(policy["financial_output_schema"]["row_payload_preimage"]),
            ],
        ],
        "governed_projection_sql_sha256": hashlib.sha256(
            _expected_dashboard_projection_sql(_PROJECTION_RECIPE).encode("utf-8")
        ).hexdigest(),
        "requirement_binding_contract_pointer": (
            "#/task8_fdd_contract/dashboard_contract/requirement_binding_contract"
        ),
        "policy_v2_requirement_literal_sha256": requirement["policy_v2_requirement_literal_sha256"],
        "governed_requirement_guard_sql_sha256": hashlib.sha256(guard.encode("utf-8")).hexdigest(),
        "runtime_normative_policy_read_allowed": False,
    }


def _build_structural_section(
    artifact: dict[str, Any],
    policy: Mapping[str, Any],
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    structural: dict[str, Any] = {
        "section_schema": _STRUCTURAL_SCHEMA,
        "parent_provenance": {
            "parent_pipeline_contract_version": "full-strict-v21",
            "parent_artifact_sha256": _V21_ARTIFACT_SHA256,
            "parent_pipeline_contract_hash": _V21_CONTRACT_SHA256,
            "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
        },
        "approved_delta_manifest": [list(row) for row in _APPROVED_DELTA_MANIFEST],
    }
    requirement = artifact["task8_fdd_contract"]["dashboard_contract"][
        "requirement_binding_contract"
    ]
    structural["identity_hash_dependency_contract"] = _build_identity_hash_dependency_contract(
        artifact, policy
    )
    structural["dashboard_sql_recipe_contract"] = _dashboard_sql_contract(policy, requirement)
    artifact["task8_structural_admission"] = structural
    manifest = _refresh_conservation_manifest(artifact, policy, reviewer_map_raw)
    structural["approved_semantics_conservation_manifest"] = manifest
    conservation_sha256 = _canonical_sha256([_CONSERVATION_DOMAIN, manifest])
    if _V26_CONSERVATION_SHA256 and conservation_sha256 != _V26_CONSERVATION_SHA256:
        _fail("TASK8_V26_CONSERVATION_MANIFEST_DRIFT")
    structural["approved_semantics_conservation_sha256"] = conservation_sha256
    structural["expected_sha256"] = _canonical_sha256(
        [_STRUCTURAL_DOMAIN, _without_hash(structural)]
    )
    derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    return structural


def validate_dashboard_requirement_vector(
    *,
    methodology_version_id: str,
    joined_methodology_version_id: str,
    required_calculations: object,
    calculation_types: Sequence[str],
) -> None:
    """Executable generation-time model for the permanent negative vectors."""

    if methodology_version_id != joined_methodology_version_id or not isinstance(
        required_calculations, list
    ):
        _fail(_STABLE_DASHBOARD_CODE)
    required_types: list[str] = []
    policy_v2 = methodology_version_id == _POLICY_V2_METHODOLOGY_VERSION_ID
    approved = set(_POLICY_REQUIREMENT_TUPLE)
    observed_policy_keys: set[tuple[object, object, object]] = set()
    for value in required_calculations:
        if not isinstance(value, dict):
            _fail(_STABLE_DASHBOARD_CODE)
        if policy_v2:
            if set(value) != set(_POLICY_KEYS):
                _fail(_STABLE_DASHBOARD_CODE)
            requirement_key = (
                value["formula_ordinal"],
                value["formula_type"],
                value["scope"],
            )
            if (
                type(value["formula_ordinal"]) is not int
                or type(value["formula_type"]) is not str
                or type(value["required"]) is not bool
                or type(value["scope"]) is not str
                or requirement_key not in approved
                or requirement_key in observed_policy_keys
            ):
                _fail(_STABLE_DASHBOARD_CODE)
            observed_policy_keys.add(requirement_key)
            if value["required"]:
                required_types.append(value["formula_type"])
        else:
            if set(value) != set(_LEGACY_KEYS):
                _fail(_STABLE_DASHBOARD_CODE)
            if (
                type(value["calc_type"]) is not str
                or not value["calc_type"].strip()
                or type(value["required"]) is not bool
            ):
                _fail(_STABLE_DASHBOARD_CODE)
            if value["required"]:
                required_types.append(value["calc_type"])
    if Counter(calculation_types) != Counter(required_types):
        _fail(_STABLE_DASHBOARD_CODE)


def _dashboard_body(artifact: Mapping[str, Any]) -> str:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    ]
    if len(matches) != 1:
        _fail("TASK8_V26_DASHBOARD_AUTHORITY_CARDINALITY_INVALID")
    statements = _decode_sql_tuple(matches[0], "body_sql_utf8_hex_tuple")
    functions = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    if len(functions) != 1:
        _fail("TASK8_V26_DASHBOARD_FUNCTION_CARDINALITY_INVALID")
    return _function_body(functions[0])


def validate_dashboard_sql_recipe_derivation(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    """Prove SQL is derived-equal to both frozen v2 identity recipes."""

    projection_rows = [
        row
        for row in artifact.get("digest_recipe_registry", {}).get("rows", [])
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    ]
    row_payload = policy.get("financial_output_schema", {}).get("row_payload_preimage")
    if len(projection_rows) != 1:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    projection = projection_rows[0][1]
    if any(
        forbidden in str(value)
        for forbidden in ("run_output_object_id", "dashboard_output_object_id")
        for value in (projection, row_payload)
    ):
        _fail("TASK8_V26_SUPERSEDED_RECIPE_FIELD")
    if projection != _PROJECTION_RECIPE or row_payload != _ROW_PAYLOAD_RECIPE:
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    body = _dashboard_body(artifact)
    try:
        projection_sql = body[body.index("preimage:=") : body.index("actual_hash:=")]
    except ValueError as exc:
        raise MaterializationError("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT") from exc
    if any(
        forbidden in projection_sql
        for forbidden in ("run_output_object_id", "dashboard_output_object_id")
    ):
        _fail("TASK8_V26_SUPERSEDED_RECIPE_FIELD")
    if projection_sql != _expected_dashboard_projection_sql(projection):
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")
    try:
        requirement = artifact["task8_fdd_contract"]["dashboard_contract"][
            "requirement_binding_contract"
        ]
        observed = artifact["task8_structural_admission"]["dashboard_sql_recipe_contract"]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT") from exc
    if observed != _dashboard_sql_contract(policy, requirement):
        _fail("TASK8_V26_DASHBOARD_SQL_RECIPE_DRIFT")


def validate_approved_semantics_conservation(
    artifact: dict[str, Any],
    policy: Mapping[str, Any],
    *,
    reviewer_map_raw: bytes,
) -> None:
    """Bind each reviewer-approved semantic label to current implementation bytes."""

    try:
        section = artifact["task8_structural_admission"]
        observed = section["approved_semantics_conservation_manifest"]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK8_V26_CONSERVATION_MANIFEST_DRIFT") from exc
    if not isinstance(observed, list):
        _fail("TASK8_V26_CONSERVATION_MANIFEST_DRIFT")
    expected_ids = [row[1] for row in _CONSERVATION_BASE] + [
        "DASHBOARD_REQUIREMENT_VERSION_DISCRIMINATION"
    ]
    observed_ids = [row[1] for row in observed if isinstance(row, list) and len(row) >= 2]
    for semantic_id in expected_ids:
        if semantic_id not in observed_ids:
            _fail("TASK8_V26_APPROVED_SEMANTIC_MISSING", semantic_id)
    if observed_ids != expected_ids:
        _fail("TASK8_V26_CONSERVATION_MANIFEST_DRIFT")
    expected = _refresh_conservation_manifest(artifact, policy, reviewer_map_raw)
    if observed != expected:
        for expected_row, observed_row in zip(expected, observed, strict=False):
            if expected_row != observed_row:
                semantic_id = str(expected_row[1])
                _fail("TASK8_V26_APPROVED_SEMANTIC_DRIFT", semantic_id)
        _fail("TASK8_V26_CONSERVATION_MANIFEST_DRIFT")
    digest = _canonical_sha256([_CONSERVATION_DOMAIN, observed])
    if (
        digest != _V26_CONSERVATION_SHA256
        or section.get("approved_semantics_conservation_sha256") != digest
    ):
        _fail("TASK8_V26_CONSERVATION_MANIFEST_DRIFT")


def validate_structural_admission(
    artifact: dict[str, Any],
    policy: Mapping[str, Any],
    *,
    reviewer_map_raw: bytes,
) -> None:
    try:
        section = artifact["task8_structural_admission"]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK8_V26_STRUCTURAL_ADMISSION_INVALID") from exc
    if (
        section.get("section_schema") != _STRUCTURAL_SCHEMA
        or section.get("parent_provenance")
        != {
            "parent_pipeline_contract_version": "full-strict-v21",
            "parent_artifact_sha256": _V21_ARTIFACT_SHA256,
            "parent_pipeline_contract_hash": _V21_CONTRACT_SHA256,
            "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
        }
        or section.get("approved_delta_manifest") != [list(row) for row in _APPROVED_DELTA_MANIFEST]
    ):
        _fail("TASK8_V26_STRUCTURAL_ADMISSION_INVALID")
    validate_dashboard_sql_recipe_derivation(artifact, policy)
    validate_approved_semantics_conservation(
        artifact,
        policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    if section.get("expected_sha256") != _canonical_sha256(
        [_STRUCTURAL_DOMAIN, _without_hash(section)]
    ):
        _fail("TASK8_V26_STRUCTURAL_ADMISSION_INVALID")
    if hashlib.sha256(_render(artifact)).hexdigest() != _V26_ARTIFACT_SHA256:
        _fail("TASK8_V26_COMPLETE_ARTIFACT_INVARIANCE_DRIFT")


def validate_dashboard_requirement_contract(
    artifact: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    try:
        contract = artifact["task8_fdd_contract"]["dashboard_contract"][
            "requirement_binding_contract"
        ]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK8_V26_REQUIREMENT_CONTRACT_DRIFT") from exc
    if contract.get("runtime_policy_read_allowed") is not False:
        _fail("TASK8_V26_RUNTIME_POLICY_DISCOVERY_FORBIDDEN")
    expected = _requirement_contract(policy)
    if contract != expected:
        _fail("TASK8_V26_REQUIREMENT_LITERAL_DRIFT")
    body = _dashboard_body(artifact)
    guard = render_dashboard_requirement_guard(contract)
    if (
        body.count(guard) != 1
        or body.index(guard) > body.index("preimage:=")
        or body.count("item->>'formula_type'=c.calc_type") != 3
        or body.count("item->>'calc_type'=c.calc_type") != 3
        or body.count("c.calc_type") != 6
        or body.count("q.methodology_version_id=c.methodology_version_id") != 2
        or "COALESCE" in body
        or "fdd_formula_policy_v2.json" in body
    ):
        _fail("TASK8_V26_REQUIREMENT_SQL_DERIVATION_DRIFT")


def _assert_no_rejected_identifiers(artifact: Mapping[str, Any]) -> None:
    raw = _canonical_bytes(artifact)
    for version in range(22, 26):
        marker = f"full-strict-v{version}".encode("ascii")
        if marker in raw:
            _fail("TASK8_V26_REJECTED_CANDIDATE_DEPENDENCY")


def _assert_invariance(parent: Mapping[str, Any], artifact: Mapping[str, Any]) -> None:
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
        if artifact[section] != parent[section]:
            _fail("TASK8_V26_INVARIANCE_DRIFT", section)
    counts = (
        len(artifact["semantic_step_registry"]["rows"]),
        len(artifact["edge_rule_registry"]["rows"]),
        len(artifact["usage_route_registry"]["rows"]),
        len(artifact["usage_extractor_registry"]["rows"]),
        len(artifact["provider_registry"]["rows"]),
    )
    if counts != (28, 48, 1_756, 19, 18):
        _fail("TASK8_V26_INVARIANCE_DRIFT", "COUNTS")


def build_v26_from_parent_raw(
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    parent = validate_frozen_parent_bytes(parent_artifact_raw)
    policy = _validate_policy_bytes(policy_artifact_raw)
    reviewer_map_raw = _validate_reviewer_map(reviewer_map_raw)
    helper = _load_approved_delta_helper()
    # Execute the source-pinned, pure six-delta builder directly over the
    # caller-supplied exact v21 object.  The policy alias exists only because
    # that reviewed helper predates the v2 policy field name; no predecessor
    # candidate artifact participates in this derivation.
    policy_alias = copy.deepcopy(policy)
    policy_alias["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"] = _POLICY_V2_SHA256
    baseline = helper._build_v22_core(  # noqa: SLF001 - source-pinned pure helper
        copy.deepcopy(parent),
        policy_alias,
        policy_artifact_sha256=_POLICY_V2_ARTIFACT_SHA256,
        policy_fixture={"fdd_formula_policy_v1_sha256": _POLICY_V2_SHA256},
        policy_fixture_sha256=_POLICY_V2_FIXTURE_SHA256,
    )
    artifact = _retarget_candidate_tree(baseline)
    _retarget_metadata(artifact)
    _retarget_digest_and_registry_hashes(artifact, helper)
    _install_projection_v2_sql(artifact)

    dashboard = artifact["task8_fdd_contract"]["dashboard_contract"]
    dashboard["requirement_binding_contract"] = _requirement_contract(policy)
    _install_dashboard_equation_v3(
        artifact,
        dashboard["requirement_binding_contract"],
        helper,
    )
    _rehash_task8(artifact)
    _build_structural_section(artifact, policy, reviewer_map_raw)
    _assert_invariance(parent, artifact)
    validate_dashboard_requirement_contract(artifact, policy)
    validate_structural_admission(
        artifact,
        policy,
        reviewer_map_raw=reviewer_map_raw,
    )
    _assert_no_rejected_identifiers(artifact)
    return artifact


def frozen_bytes(candidate: Mapping[str, Any]) -> bytes:
    metadata = candidate.get("metadata", {})
    if (
        metadata.get("pipeline_contract_version") != _V26
        or metadata.get("artifact_schema") != _V26_SCHEMA
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V26_CONTRACT_ARTIFACT_INVALID")
    return _render(candidate)


def validate_frozen_artifact(
    artifact_raw: bytes,
    *,
    parent_artifact_raw: bytes,
    policy_artifact_raw: bytes,
    reviewer_map_raw: bytes,
) -> dict[str, Any]:
    artifact = _decode(artifact_raw, "TASK8_V26_CONTRACT_ARTIFACT_INVALID")
    if _render(artifact) != artifact_raw:
        _fail("TASK8_V26_CONTRACT_ARTIFACT_INVALID")
    expected = build_v26_from_parent_raw(
        parent_artifact_raw=parent_artifact_raw,
        policy_artifact_raw=policy_artifact_raw,
        reviewer_map_raw=reviewer_map_raw,
    )
    if artifact != expected:
        _fail("TASK8_V26_EXACT_DIFF_INVALID")
    return artifact


def candidate_receipt(candidate: Mapping[str, Any]) -> dict[str, Any]:
    raw = frozen_bytes(candidate)
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    components = {
        f"{name}_hash": _canonical_sha256(
            ["full-strict-v26-component-v1", name, copy.deepcopy(value)]
        )
        for name, value in candidate.items()
    }
    task8 = candidate.get("task8_fdd_contract", {})
    if not isinstance(task8, Mapping) or not isinstance(task8.get("expected_sha256"), str):
        _fail("TASK8_V26_CANDIDATE_RECEIPT_INVALID")
    components["task8_fdd_contract_hash"] = str(task8["expected_sha256"])
    ordered = [[name, digest] for name, digest in components.items()]
    contract_sha256 = _canonical_sha256(
        [
            "full-strict-v26-pipeline-contract-v1",
            _V26,
            artifact_sha256,
            ordered,
        ]
    )
    return {
        "receipt_schema": "full-strict-v26-candidate-receipt-v1",
        "pipeline_contract_version": _V26,
        "artifact_sha256": artifact_sha256,
        "candidate_contract_sha256": contract_sha256,
        "component_hashes": components,
        "activation_status": "INACTIVE",
        "canonical_artifact_published": False,
        "canonical_fixture_published": False,
    }


def _write_staging(path: Path, raw: bytes) -> None:
    try:
        resolved = path.resolve()
        canonical_targets = {
            _CANONICAL_ARTIFACT.resolve(),
            _CANONICAL_FIXTURE.resolve(),
        }
    except OSError:
        _fail("TASK8_V26_STAGING_IO_FAILURE")
    if resolved in canonical_targets:
        _fail("TASK8_V26_CANONICAL_PUBLICATION_PATH_FORBIDDEN")
    try:
        with path.open("xb") as stream:
            if stream.write(raw) != len(raw):
                _fail("TASK8_V26_STAGING_IO_FAILURE")
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError:
            _fail("TASK8_V26_STAGING_IO_FAILURE")
        if existing != raw:
            _fail("TASK8_V26_STAGING_NO_CLOBBER")
    except MaterializationError:
        raise
    except OSError:
        _fail("TASK8_V26_STAGING_IO_FAILURE")


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise MaterializationError("TASK8_V26_CLI_ARGUMENT_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _StableArgumentParser(description=__doc__)
    parser.add_argument("--staging-output", type=Path)
    try:
        args = parser.parse_args(argv)
        candidate = build_v26_from_parent_raw(
            parent_artifact_raw=_read_exact(_V21_ARTIFACT, "TASK8_V26_PARENT_MUST_BE_FROZEN_V21"),
            policy_artifact_raw=_read_exact(
                _POLICY_V2_ARTIFACT, "TASK8_V26_POLICY_MUST_BE_FROZEN_V2"
            ),
            reviewer_map_raw=_read_exact(
                _POLICY_V2_REVIEWER_MAP, "TASK8_V26_REVIEWER_MAP_BYTES_DRIFT"
            ),
        )
        raw = frozen_bytes(candidate)
        if args.staging_output is not None:
            _write_staging(args.staging_output, raw)
        receipt = candidate_receipt(candidate)
    except MaterializationError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except OSError:
        print("TASK8_V26_IO_FAILURE", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - final CLI privacy boundary; BaseException propagates
        print("TASK8_V26_UNEXPECTED_FAILURE", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IdentityHashDependencyError",
    "MaterializationError",
    "build_v26_from_parent_raw",
    "candidate_receipt",
    "derive_identity_hash_dependency_graph",
    "frozen_bytes",
    "main",
    "render_dashboard_requirement_guard",
    "validate_approved_semantics_conservation",
    "validate_dashboard_requirement_contract",
    "validate_dashboard_requirement_vector",
    "validate_dashboard_sql_recipe_derivation",
    "validate_frozen_artifact",
    "validate_frozen_parent_bytes",
    "validate_structural_admission",
]
