"""Independent structural oracle for the full-strict-v24 RED gate.

The oracle deliberately imports neither the v23 nor the future v24
materializer.  It defines the reviewed successor boundary as literal test
data and inspects artifact bytes through public JSON shapes only.
"""

from __future__ import annotations

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
V21_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
V23_DRAFT_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
POLICY_V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_V2_REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
V24_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v24_preimage.py"

V21_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
V21_CONTRACT_SHA256 = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
V23_DRAFT_ARTIFACT_SHA256 = "ddbe234bfbac246e65b2b1c0dd30eae018e88754446d713f1defdb4d70232dfc"
POLICY_V2_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_V2_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
POLICY_V2_REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"

V24_VERSION = "full-strict-v24"
V24_SCHEMA = "full-strict-v24-reviewed-preimage-v1"
STRUCTURAL_SCHEMA = "task8-v24-structural-admission-v1"
GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
GRAPH_PRODUCER_AUTHORITY_DOMAIN = "task8-v24-graph-producer-authority-v1"
GRAPH_EXTERNAL_INPUT_DOMAIN = "task8-v24-graph-allowed-external-input-v1"
POLICY_BINDING_DOMAIN = "task8-v24-policy-binding-v1"
CONSERVATION_DOMAIN = "task8-v24-approved-semantics-conservation-v1"
STRUCTURAL_DOMAIN = "task8-v24-structural-admission-v1"

APPROVED_DELTA_MANIFEST: tuple[tuple[int, str, str], ...] = (
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

STRICT_PERIOD_EXPRESSION = "period_start<period_end AND as_of_date XOR (period_start,period_end)"
SOURCE_NORMALIZATION_EXPRESSION = "normalized_decimal=source_decimal"
POSITIVE_MAGNITUDE_EXPRESSION = (
    "sign_transform='POSITIVE_MAGNITUDE' => decimal(source_decimal)>=0 AND "
    "decimal(normalized_decimal)>=0"
)
DASHBOARD_CENSUS_ILLEGAL_CASES = (
    "COVERAGE_CENSUS_NOT_100",
    "DASHBOARD_ROW_CENSUS_NOT_100",
    "COVERAGE_SET_MISSING",
    "COVERAGE_SET_EXTRA",
)
ACYCLIC_DERIVATION_ORDER = (
    "dashboard_row_id",
    "dashboard_root_link_ids",
    "root_projection_sha256",
    "row_payload_sha256",
    "native_identity_sha256_and_native_output_sha256",
    "output_object_id",
)

APPROVED_SEMANTICS_CONSERVATION_MANIFEST: tuple[tuple[int, str, tuple[str, ...], object], ...] = (
    (
        1,
        "STRICT_PERIOD_ORDERING",
        (
            "SCHEMA_CONSTRAINT",
            "fdd-deterministic-calculation",
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
        ),
        STRICT_PERIOD_EXPRESSION,
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
        POSITIVE_MAGNITUDE_EXPRESSION,
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
        SOURCE_NORMALIZATION_EXPRESSION,
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
            *DASHBOARD_CENSUS_ILLEGAL_CASES,
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
        ACYCLIC_DERIVATION_ORDER,
    ),
    (
        7,
        "FDD_FORMULA_POLICY_V2_BINDING",
        ("POLICY_BINDING", "task8_fdd_contract", "policy_binding"),
        (
            "fdd-formula-policy-v2",
            POLICY_V2_ARTIFACT_SHA256,
            POLICY_V2_SHA256,
            ("docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"),
            POLICY_V2_REVIEWER_MAP_SHA256,
        ),
    ),
)

ALLOWED_LEAF_AUTHORITIES = frozenset(
    {
        "BOUND_RUNTIME_INPUT",
        "CONTRACT_LITERAL",
        "FOREIGN_ROOT_INPUT",
        "PINNED_CONTRACT_INPUT",
        "SCALAR_OR_FOREIGN_INPUT",
        "VERSIONED_CONTRACT_INPUT",
    }
)


class V24StructuralOracleError(AssertionError):
    """Stable, secret-free structural-admission failure."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V24StructuralOracleError(code)


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


def load_json(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), f"{code}:{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V24StructuralOracleError(f"{code}_INVALID:{path.name}") from exc
    require(isinstance(value, dict), f"{code}_INVALID:{path.name}")
    return value


def load_v21() -> dict[str, Any]:
    raw = V21_ARTIFACT.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == V21_ARTIFACT_SHA256, "TASK8_V21_BYTES_DRIFT")
    return load_json(V21_ARTIFACT, "TASK8_V21_ARTIFACT_MISSING")


def load_v23_draft() -> dict[str, Any]:
    raw = V23_DRAFT_ARTIFACT.read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == V23_DRAFT_ARTIFACT_SHA256,
        "TASK8_V23_REJECTED_DRAFT_BYTES_DRIFT",
    )
    return load_json(V23_DRAFT_ARTIFACT, "TASK8_V23_REJECTED_DRAFT_MISSING")


def load_v24() -> dict[str, Any]:
    return load_json(V24_ARTIFACT, "TASK8_V24_ARTIFACT_NOT_IMPLEMENTED")


def load_policy_v2() -> dict[str, Any]:
    raw = POLICY_V2_ARTIFACT.read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == POLICY_V2_ARTIFACT_SHA256,
        "TASK8_FDD_POLICY_V2_BYTES_DRIFT",
    )
    return load_json(POLICY_V2_ARTIFACT, "TASK8_FDD_POLICY_V2_MISSING")


def load_materializer() -> ModuleType:
    require(V24_MATERIALIZER.is_file(), "TASK8_V24_MATERIALIZER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location("_task8_v24_structural_red", V24_MATERIALIZER)
    require(spec is not None and spec.loader is not None, "TASK8_V24_MATERIALIZER_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def structural_section(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    section = artifact.get("task8_structural_admission")
    require(isinstance(section, dict), "TASK8_V24_STRUCTURAL_ADMISSION_MISSING")
    return section


def expected_parent_provenance() -> dict[str, str]:
    return {
        "parent_pipeline_contract_version": "full-strict-v21",
        "parent_artifact_sha256": V21_ARTIFACT_SHA256,
        "parent_pipeline_contract_hash": V21_CONTRACT_SHA256,
        "derivation_mode": "FROZEN_V21_PLUS_ENUMERATED_APPROVED_DELTA_ONLY",
    }


def expected_conservation_sha256(manifest: Sequence[object]) -> str:
    return canonical_sha256([CONSERVATION_DOMAIN, list(manifest)])


def expected_structural_sha256(section: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in section.items() if key != "expected_sha256"}
    return canonical_sha256([STRUCTURAL_DOMAIN, payload])


def _json_pointer(parts: Sequence[str | int]) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "#/" + "/".join(encoded)


def _walk_preimage_declarations(
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
            result.update(
                _walk_preimage_declarations(
                    child,
                    artifact_name=artifact_name,
                    path=child_path,
                )
            )
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            result.update(
                _walk_preimage_declarations(
                    child,
                    artifact_name=artifact_name,
                    path=(*path, ordinal),
                )
            )
    return result


def discover_source_pointers(
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    """Independently enumerate every UUID tuple and hash/preimage declaration."""

    pointers = _walk_preimage_declarations(full_strict, artifact_name="full_strict")
    pointers.update(_walk_preimage_declarations(policy, artifact_name="fdd_formula_policy"))
    for section_name in ("uuid_namespace_registry", "digest_recipe_registry"):
        rows = full_strict.get(section_name, {}).get("rows", [])
        require(isinstance(rows, list), "TASK8_V24_GRAPH_REGISTRY_INVALID")
        for ordinal, row in enumerate(rows):
            require(isinstance(row, list), "TASK8_V24_GRAPH_REGISTRY_ROW_INVALID")
            pointers.add(f"full_strict:#{'/' + section_name}/rows/{ordinal}")
    return tuple(sorted(pointers))


def pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    artifact_name, encoded = pointer.split(":#/", 1)
    value: object = full_strict if artifact_name == "full_strict" else policy
    for raw in encoded.split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    return value


def relative_pointer_value(value: object, pointer: str) -> object:
    if pointer == "#":
        return value
    require(pointer.startswith("#/"), "TASK8_V24_GRAPH_PAYLOAD_POINTER_INVALID")
    current = value
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
    return current


def flatten_scalars(value: object, path: str = "$") -> tuple[tuple[str, object], ...]:
    if isinstance(value, dict):
        return tuple(
            item for key in sorted(value) for item in flatten_scalars(value[key], f"{path}.{key}")
        )
    if isinstance(value, list):
        return tuple(
            item
            for ordinal, child in enumerate(value)
            for item in flatten_scalars(child, f"{path}[{ordinal}]")
        )
    return ((path, value),)


def graph_node_id(node: Sequence[object]) -> str:
    require(len(node) == 6, "TASK8_V24_GRAPH_NODE_SHAPE_INVALID")
    return canonical_bytes(list(node[:5])).decode("utf-8")


def graph_adjacency(graph: Mapping[str, Any]) -> dict[str, set[str]]:
    nodes = graph.get("node_tuple")
    edges = graph.get("edge_tuple")
    require(isinstance(nodes, list) and isinstance(edges, list), "TASK8_V24_GRAPH_SHAPE_INVALID")
    adjacency = {graph_node_id(node): set() for node in nodes}
    for row in edges:
        require(isinstance(row, list) and len(row) == 3, "TASK8_V24_GRAPH_EDGE_SHAPE_INVALID")
        derived, dependency, _receipt = row
        require(
            derived in adjacency and dependency in adjacency,
            "TASK8_V24_GRAPH_EDGE_ENDPOINT_INVALID",
        )
        adjacency[derived].add(dependency)
    return adjacency


def deterministic_cycle_path(graph: Mapping[str, set[str]]) -> tuple[str, ...] | None:
    complete: set[str] = set()
    active: list[str] = []
    active_index: dict[str, int] = {}

    def visit(node: str) -> tuple[str, ...] | None:
        if node in active_index:
            start = active_index[node]
            return (*active[start:], node)
        if node in complete:
            return None
        active_index[node] = len(active)
        active.append(node)
        for dependency in sorted(graph.get(node, set())):
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        active.pop()
        active_index.pop(node)
        complete.add(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle is not None:
            return cycle
    return None


def graph_token_receipts(graph: Mapping[str, Any]) -> set[tuple[str, str]]:
    receipts: set[tuple[str, str]] = set()
    for _derived, _dependency, token_pointer in graph["edge_tuple"]:
        require(":$" in token_pointer, "TASK8_V24_GRAPH_EDGE_TOKEN_RECEIPT_INVALID")
        pointer, path = token_pointer.rsplit(":$", 1)
        receipts.add((pointer, "$" + path))
    for leaf in graph["leaf_tuple"]:
        require(len(leaf) == 5, "TASK8_V24_GRAPH_LEAF_SHAPE_INVALID")
        require(leaf[3] in ALLOWED_LEAF_AUTHORITIES, "TASK8_V24_GRAPH_LEAF_AUTHORITY_INVALID")
        receipts.add((leaf[4], leaf[1]))
    return receipts


def independently_expected_token_receipts(
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> set[tuple[str, str]]:
    contract = structural_section(full_strict)["identity_hash_dependency_contract"]
    producer_rows = contract["producer_authority_tuple"]
    operator_tokens = {"CJ", "H", "ORDERED", "SCALAR", "SHA256", "TUPLE", "lowercase_hex"}
    identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    receipts: set[tuple[str, str]] = set()
    for row in producer_rows:
        pointer = row[0]
        payload = relative_pointer_value(
            pointer_value(pointer, full_strict, policy),
            row[6],
        )
        for token_path, value in flatten_scalars(payload):
            identifiers = (
                set(identifier.findall(value)) - operator_tokens
                if isinstance(value, str)
                else set()
            )
            if identifiers:
                receipts.update((pointer, f"{token_path}#{symbol}") for symbol in identifiers)
            else:
                receipts.add((pointer, token_path))
    return receipts


def authority(artifact: Mapping[str, Any], authority_id: str) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == authority_id
    ]
    require(len(matches) == 1, f"TASK8_V24_SQL_AUTHORITY_CARDINALITY:{authority_id}")
    return matches[0]


def decoded_sql(authority_row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    try:
        return tuple(bytes.fromhex(value).decode("utf-8") for value in authority_row[field])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise V24StructuralOracleError("TASK8_V24_SQL_HEX_INVALID") from exc


def dashboard_function_body(artifact: Mapping[str, Any]) -> str:
    statements = decoded_sql(
        authority(artifact, "dashboard-root-equation-v2"), "body_sql_utf8_hex_tuple"
    )
    matches = [
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
        or statement.startswith(
            "CREATE OR REPLACE FUNCTION public.idis_task8_dashboard_root_equation_v2()"
        )
    ]
    require(len(matches) == 1, "TASK8_V24_DASHBOARD_SQL_FUNCTION_CARDINALITY_INVALID")
    body = matches[0].partition(" AS $$")[2].rsplit("$$", 1)[0]
    require(bool(body), "TASK8_V24_DASHBOARD_SQL_BODY_INVALID")
    return body


def schema_constraint(
    artifact: Mapping[str, Any],
    model_name: str,
    constraint_id: str,
) -> str:
    contracts = artifact["task8_fdd_contract"]["fdd_schema_contracts"]
    matches = [row for row in contracts if row["schema_id"] == "fdd-deterministic-calculation"]
    require(len(matches) == 1, "TASK8_V24_FDD_SCHEMA_CONTRACT_CARDINALITY_INVALID")
    snapshot = matches[0]["normalized_schema_snapshot"]
    # The production snapshot's targets authority is an ordered pair-list, not a map.
    target_map = dict(snapshot["targets"])
    locations = [snapshot["$defs"][model_name]]
    if model_name in target_map:
        locations.append(target_map[model_name])
    found: list[str] = []
    for location in locations:
        rows = location.get("x-idis-cross-field-constraint-tuple", [])
        row_matches = [row[1] for row in rows if row[0] == constraint_id]
        require(
            len(row_matches) == 1,
            f"TASK8_V24_SCHEMA_CONSTRAINT_CARDINALITY:{constraint_id}",
        )
        found.append(row_matches[0])
    require(
        len(set(found)) == 1,
        f"TASK8_V24_SCHEMA_CONSTRAINT_TARGET_DRIFT:{constraint_id}",
    )
    return found[0]


def expected_erasure_downgrade_tuple(v21: Mapping[str, Any]) -> tuple[str, ...]:
    row = v21["erasure_execution_registry"]["rows"][0]
    old_body = decoded_sql(row, "current_body_sql_utf8_hex_tuple")
    old_acl = decoded_sql(row, "current_acl_sql_utf8_hex_tuple")
    require(len(old_body) == 1, "TASK8_V24_V21_ERASURE_BODY_CARDINALITY_INVALID")
    body = old_body[0].replace(
        "CREATE FUNCTION public.idis_task7_execute_approved_erasure(",
        "CREATE OR REPLACE FUNCTION public.idis_task7_execute_approved_erasure(",
        1,
    )
    revoke = (
        "REVOKE SELECT (tenant_id,deal_id) ON TABLE "
        "public.methodology_truth_dashboard_rows FROM idis_erasure_function_owner"
    )
    return (body, *old_acl, revoke)


def assert_semantic_binding(
    artifact: Mapping[str, Any],
    policy: Mapping[str, Any],
    semantic_id: str,
) -> None:
    if semantic_id == "STRICT_PERIOD_ORDERING":
        actual = schema_constraint(
            artifact,
            "FddCalcInputsV1",
            "economic-coordinate-date-order-v1",
        )
        require(
            actual == STRICT_PERIOD_EXPRESSION, "TASK8_V24_SEMANTIC_DRIFT:STRICT_PERIOD_ORDERING"
        )
        return
    if semantic_id == "POSITIVE_MAGNITUDE_NONNEGATIVE":
        actual = schema_constraint(
            artifact,
            "FddNumericBindingV1",
            "positive-magnitude-source-nonnegative-v1",
        )
        require(
            actual == POSITIVE_MAGNITUDE_EXPRESSION,
            "TASK8_V24_SEMANTIC_DRIFT:POSITIVE_MAGNITUDE_NONNEGATIVE",
        )
        return
    if semantic_id == "SOURCE_NORMALIZATION_PRESERVED":
        actual = schema_constraint(
            artifact,
            "FddNumericBindingV1",
            "numeric-binding-source-normalization-v1",
        )
        require(
            actual == SOURCE_NORMALIZATION_EXPRESSION,
            "TASK8_V24_SEMANTIC_DRIFT:SOURCE_NORMALIZATION_PRESERVED",
        )
        return
    if semantic_id == "ERASURE_0039_TO_0038_EXACT_RESTORATION":
        refusal = artifact["task8_fdd_contract"]["erasure_refusal_contract"]
        actual = decoded_sql(refusal, "downgrade_sql_utf8_hex_tuple")
        require(
            actual == expected_erasure_downgrade_tuple(load_v21()),
            "TASK8_V24_SEMANTIC_DRIFT:ERASURE_0039_TO_0038_EXACT_RESTORATION",
        )
        return
    if semantic_id == "DASHBOARD_100_OF_100_CENSUS_GUARD":
        row = authority(artifact, "dashboard-root-equation-v2")
        cases = set(row["illegal_red_case_tuple"])
        body = dashboard_function_body(artifact)
        valid = (
            set(DASHBOARD_CENSUS_ILLEGAL_CASES).issubset(cases)
            and "WITH dashboard_scopes AS" in body
            and "incomplete_scopes AS" in body
            and body.count("<>100") >= 2
            and body.count(" EXCEPT SELECT ") >= 4
        )
        require(valid, "TASK8_V24_SEMANTIC_DRIFT:DASHBOARD_100_OF_100_CENSUS_GUARD")
        return
    if semantic_id == "ACYCLIC_DASHBOARD_IDENTITY_CONTRACT":
        section = structural_section(artifact)
        actual = tuple(section["dashboard_sql_recipe_contract"]["derivation_order_tuple"])
        require(
            actual == ACYCLIC_DERIVATION_ORDER,
            "TASK8_V24_SEMANTIC_DRIFT:ACYCLIC_DASHBOARD_IDENTITY_CONTRACT",
        )
        return
    if semantic_id == "FDD_FORMULA_POLICY_V2_BINDING":
        binding = artifact["task8_fdd_contract"]["policy_binding"]
        binding_sha256 = canonical_sha256(
            [
                POLICY_BINDING_DOMAIN,
                [
                    "fdd-formula-policy-v2",
                    POLICY_V2_ARTIFACT_SHA256,
                    POLICY_V2_SHA256,
                    ("docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"),
                    POLICY_V2_REVIEWER_MAP_SHA256,
                ],
            ]
        )
        actual = (
            binding["policy_id"],
            binding["artifact_sha256"],
            binding["policy_sha256"],
            binding["reviewer_map_relative_path"],
            binding["reviewer_map_sha256"],
            binding["binding_sha256"],
        )
        policy_hash = policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"]
        valid = (
            actual
            == (
                "fdd-formula-policy-v2",
                POLICY_V2_ARTIFACT_SHA256,
                POLICY_V2_SHA256,
                ("docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"),
                POLICY_V2_REVIEWER_MAP_SHA256,
                binding_sha256,
            )
            and policy_hash == POLICY_V2_SHA256
        )
        require(valid, "TASK8_V24_SEMANTIC_DRIFT:FDD_FORMULA_POLICY_V2_BINDING")
        return
    raise V24StructuralOracleError(f"TASK8_V24_SEMANTIC_UNKNOWN:{semantic_id}")


def replace_schema_constraint(
    artifact: dict[str, Any],
    model_name: str,
    constraint_id: str,
    replacement: str,
) -> None:
    contract = next(
        row
        for row in artifact["task8_fdd_contract"]["fdd_schema_contracts"]
        if row["schema_id"] == "fdd-deterministic-calculation"
    )
    snapshot = contract["normalized_schema_snapshot"]
    target_map = dict(snapshot["targets"])
    locations = [snapshot["$defs"][model_name]]
    if model_name in target_map:
        locations.append(target_map[model_name])
    for location in locations:
        row = next(
            item
            for item in location["x-idis-cross-field-constraint-tuple"]
            if item[0] == constraint_id
        )
        row[1] = replacement


def materializer_graph(module: ModuleType, artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return module.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=load_policy_v2(),
    )


__all__ = [
    "ACYCLIC_DERIVATION_ORDER",
    "APPROVED_DELTA_MANIFEST",
    "APPROVED_SEMANTICS_CONSERVATION_MANIFEST",
    "CONSERVATION_DOMAIN",
    "GRAPH_SCHEMA",
    "POLICY_V2_ARTIFACT_SHA256",
    "POLICY_V2_SHA256",
    "STRUCTURAL_SCHEMA",
    "V21_ARTIFACT",
    "V21_ARTIFACT_SHA256",
    "V21_CONTRACT_SHA256",
    "V23_DRAFT_ARTIFACT",
    "V23_DRAFT_ARTIFACT_SHA256",
    "V24_ARTIFACT",
    "V24_SCHEMA",
    "V24_VERSION",
    "V24StructuralOracleError",
    "assert_semantic_binding",
    "canonical_bytes",
    "canonical_sha256",
    "dashboard_function_body",
    "deterministic_cycle_path",
    "discover_source_pointers",
    "expected_conservation_sha256",
    "expected_parent_provenance",
    "expected_structural_sha256",
    "flatten_scalars",
    "graph_adjacency",
    "graph_token_receipts",
    "independently_expected_token_receipts",
    "load_materializer",
    "load_policy_v2",
    "load_v21",
    "load_v23_draft",
    "load_v24",
    "materializer_graph",
    "replace_schema_constraint",
    "schema_constraint",
    "structural_section",
]
