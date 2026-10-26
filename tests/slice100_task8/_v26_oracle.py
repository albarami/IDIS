"""Independent literals and helpers for the full-strict-v26 RED suite.

This test-owned module does not import the v26 materializer.  It derives the
approved policy-v2 requirement tuple from the frozen policy bytes and keeps
the parent/version/hash authorities literal so the production generator
cannot affirm its own output.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from . import _task8_void_contract_hash_evidence as void_evidence

REPO_ROOT = Path(__file__).resolve().parents[2]
V21_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
V22_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
V23_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
V26_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
V26_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)
V26_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v26_preimage.py"
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
V26_VERSION = "full-strict-v26"
V26_SCHEMA = "full-strict-v26-reviewed-preimage-v1"
V26_STRUCTURAL_SCHEMA = "task8-v26-structural-admission-v1"
V26_CONSERVATION_SCHEMA = "task8-v26-approved-semantics-conservation-v1"
V26_REQUIREMENT_CONTRACT_SCHEMA = "task8-dashboard-requirement-binding-v3"
V26_REQUIREMENT_LITERAL_DOMAIN = "task8-v26-dashboard-policy-v2-requirement-literal-v1"
V26_STABLE_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
V26_GRAPH_PRODUCER_SHA256 = "2cd98224e13fedb8163115eb8b36f0bbcd89a8d2349e2862dfc2a1ce51799bad"
V26_GRAPH_EXTERNAL_INPUT_SHA256 = "73184db5100e400407713e370795956bfdd7fd51c34ca745ee486d925116af25"
V26_GRAPH_SHA256 = "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece"
V26_CONSERVATION_SHA256 = "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235"
V26_ARTIFACT_SHA256 = "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27"
V26_TOP_LEVEL_DIGEST_INVENTORY = (
    ("#/activation_assertions", "13d4a67fe2e956889fd8ba28c89a4e2753a513d6c04658976a4c2f9c89620b46"),
    (
        "#/applicability_registry",
        "b480564d5c75cb7a48ab00eea42eeaa89e2bc17f4fe5a9443236a0d3623dabb6",
    ),
    (
        "#/baseline_parent_authority_registry",
        "36320b2bdd6ffe165a8af2ff6da8ca9a7884f634a60aaabc9a85ab7fa05ad792",
    ),
    (
        "#/canonicalization_contract",
        "c610c8b3bf4b8e19ffdf4db4230a5351271e822f316fd1897d13d29835040d6c",
    ),
    (
        "#/consumer_selector_registry",
        "679b68ecec2d8cc645d966c2b6f57920a994c2f17a13c9d87c61353befb3082b",
    ),
    (
        "#/current_ddl_registry",
        "68df8944cd9bdda6294832675ec08bf98c38f1a2a1189758cc2f6b7d31b3be5b",
    ),
    (
        "#/deliverable_registry",
        "8e058e325eef60fa89639dcb898a003ba42d69335eac2d0d9ed97e985e092a75",
    ),
    (
        "#/digest_recipe_registry",
        "5b8fea641aba109f234c4cc4997d061071502f645af8b97e4339850d49d88e78",
    ),
    (
        "#/edge_rule_registry",
        "9d8acc4ef18a4e928a7108ce84dbe1ae30599fa0a56054d8b7880f9a8d985884",
    ),
    ("#/erasure_baseline", "067532baa954e12abbe630ebf1f260a737048da6d4776fb2a5cae5485618d44d"),
    (
        "#/erasure_execution_registry",
        "580968ff8ce62b2940da32604e5fec9a8f34e29d81292ef204315c75d6dfbdff",
    ),
    (
        "#/erasure_materialized",
        "60a385781e3eeac0b1dfd505632975e9529a7f8f7120246fc99ea01f7537cfa4",
    ),
    (
        "#/future_child_authority_registry",
        "0cfc9124c4707777ed587fb5a520484c50d2c02addfc51255ac237f213488acc",
    ),
    (
        "#/future_native_authority_registry",
        "7b26c5e2e69911191b7948957112acf58e855e42a4a87868120ad0e02a4feaaf",
    ),
    (
        "#/future_reference_authority_registry",
        "c11abd81be74988d26cdee57bbe8fca2e5665ba7e1fee05833c17ea068d5e245",
    ),
    ("#/metadata", "08555a0120bd16768beb83f66d115c8f9f0cd79c450c8f2f092a2bfb5f84ea11"),
    (
        "#/producer_selector_registry",
        "bf87e29d1560fa7355131afae0443c48e93b59982259f7b3e10dac659a8011a2",
    ),
    (
        "#/provider_registry",
        "43371023cdc4b39dd60d45a4d38d3100cc7fab1ef73b7cdca9462fb046eda086",
    ),
    (
        "#/rights_index_policy_registry",
        "9c3b759b3f7d1b7ccca4568de18c12dba754214675154bc0b97f5fa598106d39",
    ),
    ("#/role_registry", "0e11e8758dda0846d9dfe8db6a67f00ec30a0cd146d5ba9ccf3567376ebc0934"),
    (
        "#/schema_registry",
        "2b236f69ef4d4b61beb8391da1c35aaba9b65019fc01cca1351de694aab88933",
    ),
    (
        "#/semantic_step_registry",
        "c3f88439d006dbf86bd53e93be509f01f65c255574993d65fcba467a516f68f0",
    ),
    (
        "#/sql_authority_registry",
        "55a3b3ce956ceae5439ab4322dd301525ab24c22e74a56fea2653236b0fadf54",
    ),
    (
        "#/task8_fdd_contract",
        "76c29938b157290d717809dde13649e9762f3668dae26d188c1fb62a6aebab47",
    ),
    (
        "#/task8_structural_admission",
        "d1cb0cdf6e2b9c0040710fb5e114774393b8c7e18f9ffa80ee7f77242b6da2ea",
    ),
    (
        "#/tuple_schema_registry",
        "9fe8cfc7e625d105c587418c25272a98ae05356e3da7e94d8bcb56b5c0270afd",
    ),
    (
        "#/usage_extractor_registry",
        "78c515afec20a0b790a48a90fbbbd9c71a5c9db523599d9912abc6b8b0c65176",
    ),
    (
        "#/usage_route_registry",
        "c97e0903e546cfe800520a25f40c11556f5bd25aa9d88e23a6c0279b4e152899",
    ),
    (
        "#/uuid_namespace_registry",
        "db692ff203ede78a3e7fecc85af680c7a02cc7b86be8713133d125813b89260c",
    ),
)

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


class V26OracleError(AssertionError):
    """Stable, operator-safe independent-oracle failure."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V26OracleError(code)


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
    raise V26OracleError(
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
    raise V26OracleError(
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
    raise V26OracleError(
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
        raise V26OracleError(code) from exc
    require(isinstance(value, dict), code)
    return cast(dict[str, Any], value)


def load_v21() -> dict[str, Any]:
    return load_json(V21_ARTIFACT, "TASK8_V26_FROZEN_V21_MISSING")


def load_v24() -> dict[str, Any]:
    try:
        raw = V24_ARTIFACT.read_bytes()
    except OSError as exc:
        raise V26OracleError("TASK8_V26_VOID_V24_MISSING") from exc
    require(
        hashlib.sha256(raw).hexdigest() == void_evidence.VOID_V24_ARTIFACT_SHA256,
        "TASK8_V26_VOID_V24_RAW_HASH_DRIFT",
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise V26OracleError("TASK8_V26_VOID_V24_INVALID") from exc
    require(isinstance(value, dict), "TASK8_V26_VOID_V24_INVALID")
    return cast(dict[str, Any], value)


def load_policy_v2() -> dict[str, Any]:
    return load_json(POLICY_V2_ARTIFACT, "TASK8_V26_POLICY_V2_MISSING")


def load_materializer() -> ModuleType:
    require(V26_MATERIALIZER.is_file(), "TASK8_V26_MATERIALIZER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location("_task8_v26_materializer_red", V26_MATERIALIZER)
    if spec is None or spec.loader is None:
        raise V26OracleError("TASK8_V26_MATERIALIZER_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise V26OracleError("TASK8_V26_MATERIALIZER_IMPORT_INVALID") from exc
    return module


def build_candidate() -> tuple[dict[str, Any], ModuleType]:
    materializer = load_materializer()
    candidate = materializer.build_v26_from_parent_raw(
        parent_artifact_raw=V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=POLICY_V2_REVIEWER_MAP.read_bytes(),
    )
    require(isinstance(candidate, dict), "TASK8_V26_CANDIDATE_INVALID")
    return cast(dict[str, Any], candidate), materializer


def policy_requirement_tuple(policy: Mapping[str, Any]) -> tuple[tuple[int, str, str], ...]:
    rows = policy["methodology_transition"]["successor_required_map"]
    return tuple(
        (int(row["formula_ordinal"]), str(row["formula_type"]), str(row["scope"])) for row in rows
    )


def expected_requirement_literal_sha256() -> str:
    return canonical_sha256(
        [V26_REQUIREMENT_LITERAL_DOMAIN, POLICY_V2_SHA256, EXPECTED_POLICY_REQUIREMENT_TUPLE]
    )


def dashboard_authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    ]
    require(len(matches) == 1, "TASK8_V26_DASHBOARD_AUTHORITY_CARDINALITY_INVALID")
    return cast(Mapping[str, Any], matches[0])


def decoded_sql(row: Mapping[str, Any], field: str = "body_sql_utf8_hex_tuple") -> tuple[str, ...]:
    try:
        return tuple(bytes.fromhex(value).decode("utf-8") for value in row[field])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise V26OracleError("TASK8_V26_SQL_HEX_INVALID") from exc


def dashboard_function_body(artifact: Mapping[str, Any]) -> str:
    statements = decoded_sql(dashboard_authority(artifact))
    matches = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    ]
    require(len(matches) == 1, "TASK8_V26_DASHBOARD_FUNCTION_CARDINALITY_INVALID")
    body = matches[0].partition(" AS $$")[2].rsplit("$$", 1)[0]
    require(bool(body), "TASK8_V26_DASHBOARD_FUNCTION_BODY_INVALID")
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
        V26_STABLE_CODE,
    )
    require(isinstance(required_calculations, list), V26_STABLE_CODE)
    calculation_rows = cast(list[object], required_calculations)
    expected = set(EXPECTED_POLICY_REQUIREMENT_TUPLE)
    required_types: list[str] = []
    policy_v2 = methodology_version_id == POLICY_V2_METHODOLOGY_VERSION_ID
    observed_policy_keys: set[tuple[object, object, object]] = set()
    for value in calculation_rows:
        require(isinstance(value, dict), V26_STABLE_CODE)
        requirement = cast(dict[str, object], value)
        if policy_v2:
            require(set(requirement) == POLICY_KEYS, V26_STABLE_CODE)
            require(type(requirement["formula_ordinal"]) is int, V26_STABLE_CODE)
            require(type(requirement["formula_type"]) is str, V26_STABLE_CODE)
            require(type(requirement["required"]) is bool, V26_STABLE_CODE)
            require(type(requirement["scope"]) is str, V26_STABLE_CODE)
            row = (
                requirement["formula_ordinal"],
                requirement["formula_type"],
                requirement["scope"],
            )
            require(row in expected, V26_STABLE_CODE)
            require(row not in observed_policy_keys, V26_STABLE_CODE)
            observed_policy_keys.add(row)
            if requirement["required"]:
                required_types.append(cast(str, requirement["formula_type"]))
        else:
            require(set(requirement) == LEGACY_KEYS, V26_STABLE_CODE)
            require(
                type(requirement["calc_type"]) is str and bool(requirement["calc_type"].strip()),
                V26_STABLE_CODE,
            )
            require(type(requirement["required"]) is bool, V26_STABLE_CODE)
            if requirement["required"]:
                required_types.append(cast(str, requirement["calc_type"]))
    require(Counter(calculation_types) == Counter(required_types), V26_STABLE_CODE)


def top_level_path_digest_inventory(
    value: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Digest every complete top-level subtree without excluding semantic surfaces."""

    return tuple((f"#/{key}", canonical_sha256(value[key])) for key in sorted(value))


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
    "V26_ARTIFACT",
    "V26_ARTIFACT_SHA256",
    "V26_CONSERVATION_SCHEMA",
    "V26_FIXTURE",
    "V26_GRAPH_EXTERNAL_INPUT_SHA256",
    "V26_GRAPH_PRODUCER_SHA256",
    "V26_GRAPH_SHA256",
    "V26_MATERIALIZER",
    "V26_REQUIREMENT_CONTRACT_SCHEMA",
    "V26_CONSERVATION_SHA256",
    "V26_SCHEMA",
    "V26_STABLE_CODE",
    "V26_STRUCTURAL_SCHEMA",
    "V26_TOP_LEVEL_DIGEST_INVENTORY",
    "V26_VERSION",
    "V26OracleError",
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
    "policy_requirement_tuple",
    "require",
    "require_private_contains",
    "require_private_equal",
    "require_private_not_contains",
    "render_artifact",
    "top_level_path_digest_inventory",
]
