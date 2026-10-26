"""Independent literals and helpers for the full-strict-v25 RED suite.

This test-owned module does not import the v25 materializer.  It derives the
approved policy-v2 requirement tuple from the frozen policy bytes and keeps
the parent/version/hash authorities literal so the production generator
cannot affirm its own output.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
V21_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
V22_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
V23_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
V25_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v25_reviewed_preimage_v1.json"
V25_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v25_contract_hash_v1.json"
)
V25_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v25_preimage.py"
POLICY_V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_V2_REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)

V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
POLICY_V2_METHODOLOGY_VERSION_ID = (
    f"financial_dd:{WORKBOOK_SHA256}:fdd-formula-policy-v2:{POLICY_V2_SHA256}"
)
V25_VERSION = "full-strict-v25"
V25_SCHEMA = "full-strict-v25-reviewed-preimage-v1"
V25_STRUCTURAL_SCHEMA = "task8-v25-structural-admission-v1"
V25_CONSERVATION_SCHEMA = "task8-v25-approved-semantics-conservation-v1"
V25_REQUIREMENT_CONTRACT_SCHEMA = "task8-dashboard-requirement-binding-v3"
V25_REQUIREMENT_LITERAL_DOMAIN = "task8-v25-dashboard-policy-v2-requirement-literal-v1"
V25_STABLE_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
V25_GRAPH_PRODUCER_SHA256 = "67a838da50a8f1d81af68c3fad800bd42151f687d5f73e86bfbb9490f755fd64"
V25_GRAPH_EXTERNAL_INPUT_SHA256 = "d5caf7232507ee99d051e355bd994e008162d8770beab117cb47926f10a0d4fa"
V25_CONSERVATION_SHA256 = "a16b12a71640b40bc472deb23d6d4b3318ad22d35ce471c957099b8a9962dc60"

POLICY_KEYS = frozenset({"formula_ordinal", "formula_type", "required", "scope"})
LEGACY_KEYS = frozenset({"calc_type", "required"})
EXPECTED_POLICY_REQUIREMENT_TUPLE = (
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


class V25OracleError(AssertionError):
    """Stable, operator-safe independent-oracle failure."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V25OracleError(code)


def _private_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_bytes(value)


def _private_fingerprint(value: object) -> tuple[str, int]:
    raw = _private_bytes(value)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def require_private_equal(observed: object, expected: object, code: str) -> None:
    """Compare exactly while exposing only digests and byte sizes on failure."""

    if observed == expected:
        return
    observed_sha256, observed_size = _private_fingerprint(observed)
    expected_sha256, expected_size = _private_fingerprint(expected)
    raise V25OracleError(
        f"{code}:observed_sha256={observed_sha256}:observed_size={observed_size}:"
        f"expected_sha256={expected_sha256}:expected_size={expected_size}"
    )


def require_private_contains(needle: str | bytes, haystack: str | bytes, code: str) -> None:
    """Assert exact containment without rendering either value on failure."""

    same_type_contains = (
        isinstance(needle, str) and isinstance(haystack, str) and needle in haystack
    ) or (isinstance(needle, bytes) and isinstance(haystack, bytes) and needle in haystack)
    if same_type_contains:
        return
    haystack_sha256, haystack_size = _private_fingerprint(haystack)
    needle_sha256, needle_size = _private_fingerprint(needle)
    raise V25OracleError(
        f"{code}:observed_sha256={haystack_sha256}:observed_size={haystack_size}:"
        f"expected_sha256={needle_sha256}:expected_size={needle_size}"
    )


def require_private_not_contains(
    needle: str | bytes,
    haystack: str | bytes,
    code: str,
) -> None:
    """Assert exact non-containment without rendering either value on failure."""

    same_type_contains = (
        isinstance(needle, str) and isinstance(haystack, str) and needle in haystack
    ) or (isinstance(needle, bytes) and isinstance(haystack, bytes) and needle in haystack)
    if not same_type_contains:
        return
    haystack_sha256, haystack_size = _private_fingerprint(haystack)
    needle_sha256, needle_size = _private_fingerprint(needle)
    raise V25OracleError(
        f"{code}:observed_sha256={haystack_sha256}:observed_size={haystack_size}:"
        f"forbidden_sha256={needle_sha256}:forbidden_size={needle_size}"
    )


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def render_artifact(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V25OracleError(code) from exc
    require(isinstance(value, dict), code)
    return cast(dict[str, Any], value)


def load_v21() -> dict[str, Any]:
    return load_json(V21_ARTIFACT, "TASK8_V25_FROZEN_V21_MISSING")


def load_v24() -> dict[str, Any]:
    return load_json(V24_ARTIFACT, "TASK8_V25_VOID_V24_MISSING")


def load_policy_v2() -> dict[str, Any]:
    return load_json(POLICY_V2_ARTIFACT, "TASK8_V25_POLICY_V2_MISSING")


def load_materializer() -> ModuleType:
    require(V25_MATERIALIZER.is_file(), "TASK8_V25_MATERIALIZER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location("_task8_v25_materializer_red", V25_MATERIALIZER)
    if spec is None or spec.loader is None:
        raise V25OracleError("TASK8_V25_MATERIALIZER_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise V25OracleError("TASK8_V25_MATERIALIZER_IMPORT_INVALID") from exc
    return module


def build_candidate() -> tuple[dict[str, Any], ModuleType]:
    materializer = load_materializer()
    candidate = materializer.build_v25_from_parent_raw(
        parent_artifact_raw=V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    require(isinstance(candidate, dict), "TASK8_V25_CANDIDATE_INVALID")
    return cast(dict[str, Any], candidate), materializer


def policy_requirement_tuple(policy: Mapping[str, Any]) -> tuple[tuple[int, str, str], ...]:
    rows = policy["methodology_transition"]["successor_required_map"]
    return tuple(
        (int(row["formula_ordinal"]), str(row["formula_type"]), str(row["scope"])) for row in rows
    )


def expected_requirement_literal_sha256() -> str:
    return canonical_sha256(
        [V25_REQUIREMENT_LITERAL_DOMAIN, POLICY_V2_SHA256, EXPECTED_POLICY_REQUIREMENT_TUPLE]
    )


def dashboard_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    ]
    require(len(matches) == 1, "TASK8_V25_DASHBOARD_AUTHORITY_CARDINALITY_INVALID")
    return cast(Mapping[str, Any], matches[0])


def decoded_sql(row: Mapping[str, Any], field: str = "body_sql_utf8_hex_tuple") -> tuple[str, ...]:
    try:
        return tuple(bytes.fromhex(value).decode("utf-8") for value in row[field])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise V25OracleError("TASK8_V25_SQL_HEX_INVALID") from exc


def dashboard_function_body(artifact: Mapping[str, Any]) -> str:
    statements = decoded_sql(dashboard_authority(artifact))
    matches = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    require(len(matches) == 1, "TASK8_V25_DASHBOARD_FUNCTION_CARDINALITY_INVALID")
    body = matches[0].partition(" AS $$")[2].rsplit("$$", 1)[0]
    require(bool(body), "TASK8_V25_DASHBOARD_FUNCTION_BODY_INVALID")
    return body


def admit_requirement_vector(
    *,
    methodology_version_id: str,
    joined_methodology_version_id: str,
    required_calculations: object,
    calculation_types: Sequence[str],
) -> None:
    """Independent executable reading of the approved v3 negative matrix."""

    require(
        methodology_version_id == joined_methodology_version_id,
        V25_STABLE_CODE,
    )
    require(isinstance(required_calculations, list), V25_STABLE_CODE)
    calculation_rows = cast(list[object], required_calculations)
    expected = set(EXPECTED_POLICY_REQUIREMENT_TUPLE)
    required_types: list[str] = []
    policy_v2 = methodology_version_id == POLICY_V2_METHODOLOGY_VERSION_ID
    observed_policy_keys: set[tuple[object, object, object]] = set()
    for value in calculation_rows:
        require(isinstance(value, dict), V25_STABLE_CODE)
        requirement = cast(dict[str, object], value)
        if policy_v2:
            require(set(requirement) == POLICY_KEYS, V25_STABLE_CODE)
            require(type(requirement["formula_ordinal"]) is int, V25_STABLE_CODE)
            require(type(requirement["formula_type"]) is str, V25_STABLE_CODE)
            require(type(requirement["required"]) is bool, V25_STABLE_CODE)
            require(type(requirement["scope"]) is str, V25_STABLE_CODE)
            row = (
                requirement["formula_ordinal"],
                requirement["formula_type"],
                requirement["scope"],
            )
            require(row in expected, V25_STABLE_CODE)
            require(row not in observed_policy_keys, V25_STABLE_CODE)
            observed_policy_keys.add(row)
            if requirement["required"]:
                required_types.append(cast(str, requirement["formula_type"]))
        else:
            require(set(requirement) == LEGACY_KEYS, V25_STABLE_CODE)
            require(
                type(requirement["calc_type"]) is str and bool(requirement["calc_type"].strip()),
                V25_STABLE_CODE,
            )
            require(type(requirement["required"]) is bool, V25_STABLE_CODE)
            if requirement["required"]:
                required_types.append(cast(str, requirement["calc_type"]))
    require(Counter(calculation_types) == Counter(required_types), V25_STABLE_CODE)


def normalized_non_equation_sections(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize unavoidable version/hash identities for semantic invariance checks."""

    result = copy.deepcopy(dict(value))
    result.pop("metadata", None)
    result.pop("task8_structural_admission", None)
    result.pop("task8_fdd_contract", None)
    result.pop("sql_authority_registry", None)
    result.pop("schema_registry", None)
    result.pop("digest_recipe_registry", None)
    result.pop("tuple_schema_registry", None)
    result.pop("uuid_namespace_registry", None)

    # References from otherwise invariant registries must follow the one
    # approved equation authority/function rename.  Normalize only those two
    # identifiers so this comparison continues to reject every other drift.
    def normalize_equation_identity(child: object) -> object:
        if isinstance(child, dict):
            return {key: normalize_equation_identity(item) for key, item in child.items()}
        if isinstance(child, list):
            return [normalize_equation_identity(item) for item in child]
        if isinstance(child, str):
            return child.replace(
                "dashboard-root-equation-v3", "dashboard-root-equation-v2"
            ).replace(
                "idis_task8_dashboard_root_equation_v3",
                "idis_task8_dashboard_root_equation_v2",
            )
        return child

    normalized = normalize_equation_identity(result)
    require(isinstance(normalized, dict), "TASK8_V25_NORMALIZATION_INVALID")
    return cast(dict[str, Any], normalized)


__all__ = [
    "EXPECTED_POLICY_REQUIREMENT_TUPLE",
    "LEGACY_KEYS",
    "POLICY_KEYS",
    "POLICY_V2_ARTIFACT",
    "POLICY_V2_ARTIFACT_SHA256",
    "POLICY_V2_METHODOLOGY_VERSION_ID",
    "POLICY_V2_REVIEWER_MAP",
    "POLICY_V2_REVIEWER_MAP_SHA256",
    "POLICY_V2_SHA256",
    "V21_ARTIFACT",
    "V21_ARTIFACT_SHA256",
    "V21_CONTRACT_SHA256",
    "V22_ARTIFACT",
    "V23_ARTIFACT",
    "V24_ARTIFACT",
    "V25_ARTIFACT",
    "V25_CONSERVATION_SCHEMA",
    "V25_FIXTURE",
    "V25_GRAPH_EXTERNAL_INPUT_SHA256",
    "V25_GRAPH_PRODUCER_SHA256",
    "V25_MATERIALIZER",
    "V25_REQUIREMENT_CONTRACT_SCHEMA",
    "V25_CONSERVATION_SHA256",
    "V25_SCHEMA",
    "V25_STABLE_CODE",
    "V25_STRUCTURAL_SCHEMA",
    "V25_VERSION",
    "V25OracleError",
    "admit_requirement_vector",
    "canonical_bytes",
    "canonical_sha256",
    "dashboard_authority",
    "dashboard_function_body",
    "decoded_sql",
    "expected_requirement_literal_sha256",
    "load_materializer",
    "load_policy_v2",
    "load_v21",
    "load_v24",
    "build_candidate",
    "normalized_non_equation_sections",
    "policy_requirement_tuple",
    "require",
    "require_private_contains",
    "require_private_equal",
    "require_private_not_contains",
    "render_artifact",
]
