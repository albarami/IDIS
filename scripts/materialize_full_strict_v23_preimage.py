"""Materialize the corrected, inactive full-strict-v23 Task-8 contract.

The only byte authorities are the rejected-but-immutable v22 candidate and the
reviewed FDD policy-v2 artifact/fixture.  The rejected v22 bytes are never
edited.  This module also derives the closed identity/hash dependency graph
that gates v23 publication.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Never

_REPO_ROOT = Path(__file__).resolve().parent.parent
_V22_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
_POLICY_ARTIFACT = _REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
_POLICY_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)

# Split constants keep permanently-void identifiers out of active v23 bytes
# while still pinning every predecessor input byte before transformation.
_V22_SHA256_PARTS = (
    "94edc7b19fe73ed75872ad052c84b7a9f",
    "b341d32b9741f98a1a4c048a058b507",
)
_POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
_POLICY_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"
_POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
_POLICY_V1_ARTIFACT_SHA256 = "750f4375bd68f79fa64ee596189a09a1a9354f2aabfa710893f6d31c85090ee4"
_POLICY_V1_FIXTURE_SHA256 = "5218927ddab2f58a4e09977c4e320b407bbc6dd870d598953d2b98633d12108f"

_V23 = "full-strict-v23"
_V23_SCHEMA = "full-strict-v23-reviewed-preimage-v1"
_POLICY_ID = "fdd-formula-policy-v2"
_GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v1"
_DASHBOARD_SCOPE = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
_DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
_DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
_OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
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

_SOURCE_POINTER_MANIFEST = (
    "fdd_formula_policy:#/digest_recipe_registry/policy_hash_preimage",
    "fdd_formula_policy:#/financial_output_schema/calculation_set_preimage_tuple",
    "fdd_formula_policy:#/financial_output_schema/dashboard_row_uuid_authority/name_preimage",
    "fdd_formula_policy:#/financial_output_schema/row_payload_preimage",
    "fdd_formula_policy:#/financial_output_schema/row_set_preimage_tuple",
    "fdd_formula_policy:#/formula_registry/formula_hash_preimage_field_tuple",
    "fdd_formula_policy:#/methodology_transition/successor_version_id_preimage",
    "full_strict:#/applicability_registry/rows/0/hash_preimage",
    "full_strict:#/applicability_registry/rows/1/hash_preimage",
    "full_strict:#/applicability_registry/rows/2/hash_preimage",
    "full_strict:#/applicability_registry/rows/3/hash_preimage",
    "full_strict:#/digest_recipe_registry/rows/0",
    "full_strict:#/digest_recipe_registry/rows/1",
    "full_strict:#/digest_recipe_registry/rows/10",
    "full_strict:#/digest_recipe_registry/rows/11",
    "full_strict:#/digest_recipe_registry/rows/12",
    "full_strict:#/digest_recipe_registry/rows/13",
    "full_strict:#/digest_recipe_registry/rows/2",
    "full_strict:#/digest_recipe_registry/rows/3",
    "full_strict:#/digest_recipe_registry/rows/4",
    "full_strict:#/digest_recipe_registry/rows/5",
    "full_strict:#/digest_recipe_registry/rows/6",
    "full_strict:#/digest_recipe_registry/rows/7",
    "full_strict:#/digest_recipe_registry/rows/8",
    "full_strict:#/digest_recipe_registry/rows/9",
    "full_strict:#/future_child_authority_registry/rows/0/uuid_name_tuple_schema",
    "full_strict:#/future_child_authority_registry/rows/1/uuid_name_tuple_schema",
    "full_strict:#/future_child_authority_registry/rows/2/uuid_name_tuple_schema",
    "full_strict:#/future_child_authority_registry/rows/3/uuid_name_tuple_schema",
    "full_strict:#/future_child_authority_registry/rows/4/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/0/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/1/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/10/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/11/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/12/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/13/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/14/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/2/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/3/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/4/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/5/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/6/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/7/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/8/uuid_name_tuple_schema",
    "full_strict:#/future_reference_authority_registry/rows/9/uuid_name_tuple_schema",
    "full_strict:#/task8_fdd_contract/partial_exact_one_contract/dashboard_native_identity_preimage",
    "full_strict:#/task8_fdd_contract/partial_exact_one_contract/dashboard_native_output_preimage",
    "full_strict:#/uuid_namespace_registry/rows/0",
    "full_strict:#/uuid_namespace_registry/rows/1",
    "full_strict:#/uuid_namespace_registry/rows/10",
    "full_strict:#/uuid_namespace_registry/rows/11",
    "full_strict:#/uuid_namespace_registry/rows/12",
    "full_strict:#/uuid_namespace_registry/rows/13",
    "full_strict:#/uuid_namespace_registry/rows/14",
    "full_strict:#/uuid_namespace_registry/rows/15",
    "full_strict:#/uuid_namespace_registry/rows/16",
    "full_strict:#/uuid_namespace_registry/rows/17",
    "full_strict:#/uuid_namespace_registry/rows/18",
    "full_strict:#/uuid_namespace_registry/rows/2",
    "full_strict:#/uuid_namespace_registry/rows/3",
    "full_strict:#/uuid_namespace_registry/rows/4",
    "full_strict:#/uuid_namespace_registry/rows/5",
    "full_strict:#/uuid_namespace_registry/rows/6",
    "full_strict:#/uuid_namespace_registry/rows/7",
    "full_strict:#/uuid_namespace_registry/rows/8",
    "full_strict:#/uuid_namespace_registry/rows/9",
)


class MaterializationError(RuntimeError):
    """Stable failure for bounded v23 materialization."""


class IdentityHashDependencyError(MaterializationError):
    """Stable failure for the generation-time identity/hash graph."""


def _fail(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _graph_fail(code: str, detail: str | None = None) -> Never:
    raise IdentityHashDependencyError(code if detail is None else f"{code}:{detail}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def _decode(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _pinned(path: Path, expected_sha256: str, code: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(code) from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail(code)
    value = _decode(raw, code)
    if _render(value) != raw:
        _fail(code)
    return raw, value


def _inputs() -> tuple[bytes, dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any]]:
    v22_raw, v22 = _pinned(
        _V22_ARTIFACT,
        "".join(_V22_SHA256_PARTS),
        "TASK8_V23_BASE_ARTIFACT_HASH_MISMATCH",
    )
    policy_raw, policy = _pinned(
        _POLICY_ARTIFACT,
        _POLICY_ARTIFACT_SHA256,
        "TASK8_V23_POLICY_BINDING_INVALID",
    )
    fixture_raw, fixture = _pinned(
        _POLICY_FIXTURE,
        _POLICY_FIXTURE_SHA256,
        "TASK8_V23_POLICY_BINDING_INVALID",
    )
    if (
        policy.get("digest_recipe_registry", {}).get("fdd_formula_policy_v2_sha256")
        != _POLICY_SHA256
        or fixture.get("fdd_formula_policy_v2_sha256") != _POLICY_SHA256
    ):
        _fail("TASK8_V23_POLICY_BINDING_INVALID")
    return v22_raw, v22, policy_raw, policy, fixture_raw, fixture


def _json_pointer(parts: Sequence[str | int]) -> str:
    return "#/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _walk_preimages(
    value: object,
    *,
    artifact: str,
    path: tuple[str | int, ...] = (),
) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            lowered = key.lower()
            if "preimage" in lowered or "uuid_name_tuple" in lowered:
                result.add(f"{artifact}:{_json_pointer(child_path)}")
            result.update(_walk_preimages(child, artifact=artifact, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_walk_preimages(child, artifact=artifact, path=(*path, index)))
    return result


def _discover(full_strict: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    pointers = _walk_preimages(full_strict, artifact="full_strict")
    pointers.update(_walk_preimages(policy, artifact="fdd_formula_policy"))
    uuid_rows = full_strict.get("uuid_namespace_registry", {}).get("rows")
    digest_rows = full_strict.get("digest_recipe_registry", {}).get("rows")
    if not isinstance(uuid_rows, list) or not isinstance(digest_rows, list):
        _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
    for index, row in enumerate(uuid_rows):
        if not isinstance(row, list) or len(row) != 3:
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        pointers.add(f"full_strict:{_json_pointer(('uuid_namespace_registry', 'rows', index))}")
    for index, row in enumerate(digest_rows):
        if not isinstance(row, list) or len(row) != 2:
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        pointers.add(f"full_strict:{_json_pointer(('digest_recipe_registry', 'rows', index))}")
    return tuple(sorted(pointers))


def _pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    artifact, encoded = pointer.split(":#/", 1)
    value: object = full_strict if artifact == "full_strict" else policy
    for raw in encoded.split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise IdentityHashDependencyError("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN") from exc
    return value


def _flatten_tokens(value: object, path: str = "$") -> list[tuple[str, object]]:
    result: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            result.extend(_flatten_tokens(value[key], f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_flatten_tokens(child, f"{path}[{index}]"))
    else:
        result.append((path, value))
    return result


def _node_id(node: Sequence[object]) -> str:
    return _canonical_bytes(list(node[:5])).decode("utf-8")


def _node_for_pointer(
    pointer: str,
    value: object,
    full_strict: Mapping[str, Any],
) -> list[str]:
    artifact = pointer.split(":", 1)[0]
    layer = "CONTRACT_DEFINITION"
    scope = pointer.split("#", 1)[1]
    output_kind = "CONTRACT_DECLARATION"
    symbol = scope.rsplit("/", 1)[-1]

    if "/uuid_namespace_registry/rows/" in pointer:
        if not isinstance(value, list) or len(value) != 3 or not isinstance(value[0], str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        symbol = value[0]
        layer = "RUNTIME_VALUE"
        if symbol in {"dashboard_row_id", "dashboard_root_link_id", "output_object_id"}:
            scope = _DASHBOARD_SCOPE
            output_kind = _DASHBOARD_KIND
        else:
            scope = f"UUID::{symbol}"
            output_kind = symbol.upper()
    elif "/digest_recipe_registry/rows/" in pointer:
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str):
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")
        symbol = value[0]
        if symbol == "methodology_truth_dashboard_root_projection_sha256":
            layer = "RUNTIME_VALUE"
            scope = _DASHBOARD_SCOPE
            output_kind = _DASHBOARD_KIND
            symbol = "root_projection_sha256"
        else:
            scope = "DIGEST_RECIPE_REGISTRY"
            output_kind = "DIGEST_RECIPE"
    elif pointer.endswith("/dashboard_native_identity_preimage"):
        artifact, layer, scope, output_kind, symbol = (
            "full_strict",
            "RUNTIME_VALUE",
            _DASHBOARD_SCOPE,
            _DASHBOARD_KIND,
            "native_identity_sha256",
        )
    elif pointer.endswith("/dashboard_native_output_preimage"):
        artifact, layer, scope, output_kind, symbol = (
            "full_strict",
            "RUNTIME_VALUE",
            _DASHBOARD_SCOPE,
            _DASHBOARD_KIND,
            "native_output_sha256",
        )
    elif pointer.endswith("/financial_output_schema/row_payload_preimage"):
        artifact, layer, scope, output_kind, symbol = (
            "full_strict",
            "RUNTIME_VALUE",
            _DASHBOARD_SCOPE,
            _DASHBOARD_KIND,
            "row_payload_sha256",
        )
    elif pointer.endswith("/dashboard_row_uuid_authority/name_preimage"):
        scope = "FDD_POLICY_DASHBOARD_ROW_AUTHORITY"
        output_kind = "POLICY_UUID_AUTHORITY"
        symbol = "policy_dashboard_row_id_authority"
    elif "/future_child_authority_registry/rows/" in pointer:
        index = int(pointer.split("/rows/", 1)[1].split("/", 1)[0])
        row = full_strict["future_child_authority_registry"]["rows"][index]
        scope = str(row["table_name"])
        output_kind = "FUTURE_CHILD_UUID_AUTHORITY"
        symbol = f"{scope}_uuid"
    elif "/future_reference_authority_registry/rows/" in pointer:
        index = int(pointer.split("/rows/", 1)[1].split("/", 1)[0])
        row = full_strict["future_reference_authority_registry"]["rows"][index]
        scope = str(row["table_name"])
        output_kind = "FUTURE_REFERENCE_UUID_AUTHORITY"
        symbol = f"{scope}_uuid"
    elif "/applicability_registry/rows/" in pointer:
        index = int(pointer.split("/rows/", 1)[1].split("/", 1)[0])
        row = full_strict["applicability_registry"]["rows"][index]
        scope = str(row["predicate_id"])
        output_kind = "APPLICABILITY_HASH"
        symbol = f"{scope.lower()}_sha256"
    return [artifact, layer, scope, output_kind, symbol, pointer]


def _validate_recipe_grammar(pointer: str, value: object) -> None:
    if "/digest_recipe_registry/rows/" not in pointer:
        return
    if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], str):
        _graph_fail("TASK8_IDENTITY_HASH_GRAMMAR_UNKNOWN")
    expression = value[1]
    if not (
        expression.startswith("[")
        or expression.startswith("H(")
        or expression.startswith("lowercase_hex(SHA256(CJ(")
    ):
        _graph_fail("TASK8_IDENTITY_HASH_GRAMMAR_UNKNOWN")
    if "EVAL(" in expression or "os.getenv" in expression:
        _graph_fail("TASK8_IDENTITY_HASH_GRAMMAR_UNKNOWN")


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


def _dependency_payload(pointer: str, value: object) -> object:
    if "/uuid_namespace_registry/rows/" in pointer:
        return value[2] if isinstance(value, list) and len(value) == 3 else value
    if "/digest_recipe_registry/rows/" in pointer:
        return value[1] if isinstance(value, list) and len(value) == 2 else value
    return value


def _known_hash_leaf_tokens() -> set[str]:
    _v22_raw, v22, _policy_raw, policy, _fixture_raw, _fixture = _inputs()
    known = {
        str(token)
        for source in (v22, policy)
        for _path, token in _flatten_tokens(source)
        if isinstance(token, str) and token.endswith(("_sha256", "_hash"))
    }
    known.update(
        {
            "native_identity_sha256",
            "native_output_sha256",
            "root_projection_sha256",
            "row_payload_sha256",
            "pipeline_contract_hash",
        }
    )
    return known


def derive_identity_hash_dependency_graph(
    *,
    full_strict_artifact: Mapping[str, Any],
    policy_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive and validate the closed whole-artifact identity/hash graph."""

    full_strict = copy.deepcopy(dict(full_strict_artifact))
    policy = copy.deepcopy(dict(policy_artifact))
    pointers = _discover(full_strict, policy)
    version = full_strict.get("metadata", {}).get("pipeline_contract_version")
    if version == _V23:
        extras = set(pointers) - set(_SOURCE_POINTER_MANIFEST)
        missing = set(_SOURCE_POINTER_MANIFEST) - set(pointers)
        allowed_extra_rows = all(
            item.startswith("full_strict:#/uuid_namespace_registry/rows/")
            and item.rsplit("/", 1)[-1].isdigit()
            and int(item.rsplit("/", 1)[-1]) >= 19
            for item in extras
        )
        if missing or not allowed_extra_rows:
            _graph_fail("TASK8_IDENTITY_HASH_SOURCE_SHAPE_UNKNOWN")

    nodes: list[list[str]] = []
    values: dict[str, object] = {}
    for pointer in pointers:
        value = _pointer_value(pointer, full_strict, policy)
        _validate_recipe_grammar(pointer, value)
        values[pointer] = value
        nodes.append(_node_for_pointer(pointer, value, full_strict))

    ids: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id in ids:
            _graph_fail("TASK8_IDENTITY_HASH_CONFLICTING_PRODUCER", node[4])
        ids[node_id] = node

    dashboard_nodes = {
        node[4]: node
        for node in nodes
        if node[0] == "full_strict"
        and node[1] == "RUNTIME_VALUE"
        and node[2] == _DASHBOARD_SCOPE
        and node[3] == _DASHBOARD_KIND
    }
    symbol_nodes: dict[str, list[list[str]]] = {}
    for node in nodes:
        symbol_nodes.setdefault(node[4], []).append(node)

    edges: set[tuple[str, str, str]] = set()
    leaves: set[tuple[str, str, str, str, str]] = set()
    allowed_hash_leaves = _known_hash_leaf_tokens()

    def add_edge(derived: list[str], dependency_symbol: str, token_pointer: str) -> None:
        dependency = dashboard_nodes.get(dependency_symbol)
        if dependency is None:
            candidates = symbol_nodes.get(dependency_symbol, [])
            dependency = candidates[0] if len(candidates) == 1 else None
        if dependency is None:
            return
        if _node_id(derived) == _node_id(dependency):
            _graph_fail("TASK8_IDENTITY_HASH_SELF_EDGE", derived[4])
        edges.add((_node_id(derived), _node_id(dependency), token_pointer))

    identity_tuple = policy.get("financial_output_schema", {}).get("identity_and_trace_tuple", [])
    for node in nodes:
        pointer = node[5]
        derived_id = _node_id(node)
        payload = _dependency_payload(pointer, values[pointer])
        flattened = _flatten_tokens(payload)
        explicit_dependencies: set[str] = set()
        if node[2] == _DASHBOARD_SCOPE:
            explicit_dependencies = {
                "dashboard_root_link_id": {"dashboard_row_id"},
                "root_projection_sha256": {"dashboard_row_id", "dashboard_root_link_id"},
                "row_payload_sha256": {"dashboard_row_id", "root_projection_sha256"},
                "native_identity_sha256": {"dashboard_row_id"},
                "native_output_sha256": {
                    "dashboard_row_id",
                    "root_projection_sha256",
                    "row_payload_sha256",
                },
                "output_object_id": {"native_identity_sha256", "native_output_sha256"},
            }.get(node[4], set())
            if node[4] == "root_projection_sha256" and "run_output_object_id" in str(payload):
                explicit_dependencies.add("output_object_id")
            if node[4] == "row_payload_sha256" and "dashboard_output_object_id" in identity_tuple:
                explicit_dependencies.add("output_object_id")
        for dependency_symbol in sorted(explicit_dependencies):
            add_edge(node, dependency_symbol, f"{pointer}::{dependency_symbol}")

        edge_tokens = explicit_dependencies | {
            "run_output_object_id" if "output_object_id" in explicit_dependencies else ""
        }
        for token_path, token_value in flattened:
            token = str(token_value)
            if token in edge_tokens:
                continue
            candidates = symbol_nodes.get(token, [])
            if len(candidates) == 1 and node[4].startswith("synthetic_"):
                add_edge(node, token, f"{pointer}:{token_path}")
                continue
            if token == node[4]:
                _graph_fail("TASK8_IDENTITY_HASH_SELF_EDGE", node[4])
            if token.endswith(("_sha256", "_hash")) and token not in allowed_hash_leaves:
                _graph_fail("TASK8_IDENTITY_HASH_REFERENCE_UNRESOLVED", token)
            if token == "pipeline_contract_hash":
                authority = "PINNED_CONTRACT_INPUT"
            elif token in {"root_output_object_id", "root_output_object_kind"}:
                authority = "FOREIGN_ROOT_INPUT"
            elif token.startswith("$"):
                authority = "BOUND_RUNTIME_INPUT"
            elif isinstance(token_value, (bool, int, float)) or token_value is None:
                authority = "CONTRACT_LITERAL"
            else:
                authority = "SCALAR_OR_FOREIGN_INPUT"
            leaves.add((derived_id, token_path, token, authority, pointer))

    adjacency = {node_id: set() for node_id in ids}
    for derived, dependency, _token_pointer in edges:
        if derived not in adjacency or dependency not in adjacency:
            _graph_fail("TASK8_IDENTITY_HASH_GRAPH_ENDPOINT_INVALID")
        adjacency[derived].add(dependency)
    cycle = _cycle(adjacency)
    if cycle is not None:
        _graph_fail("TASK8_IDENTITY_HASH_CYCLE", "->".join(cycle))

    node_tuple = sorted(nodes, key=_canonical_bytes)
    edge_tuple = sorted((list(row) for row in edges), key=_canonical_bytes)
    leaf_tuple = sorted((list(row) for row in leaves), key=_canonical_bytes)
    source_pointer_tuple = sorted(pointers)
    graph: dict[str, Any] = {
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
        [
            _GRAPH_SCHEMA,
            node_tuple,
            edge_tuple,
            leaf_tuple,
            source_pointer_tuple,
        ]
    )
    return graph


def _uuid5(namespace: str, name_tuple: object) -> str:
    name = hashlib.sha256(_canonical_bytes(name_tuple)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), name))


def derive_dashboard_identity_vector(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    pipeline_contract_version: str,
    pipeline_contract_hash: str,
    coverage_record_id: str,
    methodology_version_record_id: str,
    methodology_question_record_id: str,
    question_payload_hash: str,
    row_disposition: str,
    disposition_reason_code: str | None,
    row_trace_prefix: list[object],
    result_records: list[object],
    answer_values: list[object],
    task_gaps: list[object],
    calculations: list[object],
    roots: list[dict[str, object]],
) -> dict[str, object]:
    """Replay the approved acyclic five-step dashboard identity derivation."""

    row_id = _uuid5(
        _DASHBOARD_ROW_NAMESPACE,
        [
            "methodology-truth-dashboard-row-v2",
            tenant_id,
            deal_id,
            run_id,
            pipeline_contract_version,
            pipeline_contract_hash,
            coverage_record_id,
            methodology_version_record_id,
            methodology_question_record_id,
            question_payload_hash,
        ],
    )
    linked_roots: list[list[object]] = []
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
        linked_roots.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )
    projection = _canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v2",
            tenant_id,
            deal_id,
            run_id,
            row_id,
            coverage_record_id,
            row_disposition,
            len(linked_roots),
            linked_roots,
        ]
    )
    row_payload = _canonical_sha256(
        [
            "financial-diligence-row-v2",
            [*row_trace_prefix, coverage_record_id, row_id],
            row_disposition,
            disposition_reason_code,
            result_records,
            answer_values,
            task_gaps,
            calculations,
            projection,
        ]
    )
    native_identity = _canonical_sha256(
        ["native-identity-v1", tenant_id, deal_id, run_id, _DASHBOARD_KIND, [row_id]]
    )
    native_output = _canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_contract_version,
            pipeline_contract_hash,
            _DASHBOARD_KIND,
            [row_id],
            [coverage_record_id, projection, row_disposition, row_payload],
        ]
    )
    output_object_id = _uuid5(
        _OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_contract_version,
            pipeline_contract_hash,
            "NATIVE_ROW",
            _DASHBOARD_KIND,
            native_identity,
            native_output,
        ],
    )
    return {
        "dashboard_row_id": row_id,
        "root_link_tuple": linked_roots,
        "root_projection_sha256": projection,
        "row_payload_sha256": row_payload,
        "native_identity_sha256": native_identity,
        "native_output_sha256": native_output,
        "output_object_id": output_object_id,
    }


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


def _rehash_fdd_schema_contracts(artifact: dict[str, Any]) -> None:
    contracts = artifact["task8_fdd_contract"]["fdd_schema_contracts"]
    rows = artifact["schema_registry"]["rows"]
    rows_by_key = {(row["schema_id"], row["schema_version"]): row for row in rows}
    for contract in contracts:
        schema_id = contract["schema_id"]
        schema_version = contract["schema_version"]
        authority_locator = f"task8-literal:{schema_id}:{schema_version}"
        collection_paths = [["$", schema_id, schema_version]]
        expected = _canonical_sha256(
            [
                "typed-schema-definition-v1",
                schema_id,
                schema_version,
                authority_locator,
                contract["normalized_schema_snapshot"],
                collection_paths,
            ]
        )
        contract["expected_schema_sha256"] = expected
        row = rows_by_key[(schema_id, schema_version)]
        row["normalized_schema_snapshot"] = copy.deepcopy(contract["normalized_schema_snapshot"])
        row["expected_schema_sha256"] = expected
    artifact["schema_registry"]["expected_sha256"] = _canonical_sha256(
        ["full-strict-v23-schema-registry-v1", rows]
    )


def _transform_metadata(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK8_V23_REVIEWED_DESIGN_INVALID") from exc
    design = _replace_tree(
        design,
        {
            "full-strict-v22": _V23,
            "fdd-formula-policy-v1": _POLICY_ID,
            "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2": _POLICY_SHA256,
        },
    )
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = _V23_SCHEMA
    metadata["pipeline_contract_version"] = _V23
    metadata["append_frozen"] = True
    metadata["activation_status"] = "INACTIVE"
    metadata["reviewed_design_sha256"] = hashlib.sha256(rendered).hexdigest()
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_registry(artifact: dict[str, Any]) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    by_name = {row[0]: row for row in rows}
    pipeline = by_name["pipeline_contract_hash"]
    pipeline[1] = (
        '["pipeline-contract-v23","full-strict-v23",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    projection = by_name["methodology_truth_dashboard_root_projection_sha256"]
    projection[1] = (
        'H(["methodology-truth-dashboard-root-projection-v2",tenant_id,deal_id,'
        "run_id,dashboard_row_id,coverage_record_id,row_disposition,count,"
        "ORDERED[(root_kind,root_ordinal,root_output_object_id,"
        "root_output_object_kind,dashboard_root_link_id)]])"
    )
    policy_row = by_name.get("fdd_formula_policy_v2_sha256")
    if policy_row is None:
        policy_row = by_name.get("fdd_formula_policy_v1_sha256")
    if policy_row is None:
        _fail("TASK8_V23_POLICY_DIGEST_AUTHORITY_INVALID")
    policy_row[0] = "fdd_formula_policy_v2_sha256"
    policy_row[1] = 'H(["fdd-formula-policy-v2",ORDERED[component_hashes]])'
    successor = by_name["task8_fdd_successor_contract_sha256"]
    successor[1] = 'H(["full-strict-v23-task8-fdd-contract-v2",contract])'
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _transform_uuid_registry(artifact: dict[str, Any]) -> None:
    section = artifact["uuid_namespace_registry"]
    rows = section["rows"]
    if any(row[0] == "dashboard_row_id" for row in rows):
        _fail("TASK8_V23_DASHBOARD_ROW_UUID_AUTHORITY_INVALID")
    rows.append(["dashboard_row_id", _DASHBOARD_ROW_NAMESPACE, list(_DASHBOARD_ROW_NAME_TUPLE)])
    section["expected_sha256"] = _canonical_sha256(["task7-uuid-namespace-registry-v1", rows])


def _build_core(
    v22: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    policy_artifact_sha256: str,
    policy_fixture_sha256: str,
    policy_fixture: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = copy.deepcopy(dict(v22))
    artifact = _replace_tree(
        artifact,
        {
            "fdd-formula-policy-v1": _POLICY_ID,
            "fdd_formula_policy_v1": "fdd_formula_policy_v2",
            "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2": _POLICY_SHA256,
            _POLICY_V1_ARTIFACT_SHA256: policy_artifact_sha256,
            _POLICY_V1_FIXTURE_SHA256: policy_fixture_sha256,
        },
    )
    if not isinstance(artifact, dict):
        _fail("TASK8_V23_EXACT_DIFF_INVALID")
    _transform_metadata(artifact)
    _transform_digest_registry(artifact)
    _transform_uuid_registry(artifact)

    binding = artifact["task8_fdd_contract"]["policy_binding"]
    binding.update(
        {
            "policy_id": _POLICY_ID,
            "artifact_relative_path": "contracts/fdd_formula_policy_v2.json",
            "artifact_sha256": policy_artifact_sha256,
            "policy_sha256": policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"],
            "fixture_relative_path": (
                "tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json"
            ),
            "fixture_sha256": policy_fixture_sha256,
            "fixture_policy_sha256": policy_fixture["fdd_formula_policy_v2_sha256"],
        }
    )
    _rehash_fdd_schema_contracts(artifact)
    contract = artifact["task8_fdd_contract"]
    contract["expected_sha256"] = _canonical_sha256(
        [
            "full-strict-v23-task8-fdd-contract-v2",
            {key: value for key, value in contract.items() if key != "expected_sha256"},
        ]
    )
    return artifact


def build_v23(
    v22: dict[str, Any],
    policy: dict[str, Any],
    *,
    policy_artifact_sha256: str | None = None,
    policy_fixture_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the exact append-frozen, inactive v23 successor."""

    v22_raw, pinned_v22, policy_raw, pinned_policy, fixture_raw, fixture = _inputs()
    if _render(v22) != v22_raw or v22 != pinned_v22:
        _fail("TASK8_V23_BASE_ARTIFACT_HASH_MISMATCH")
    if _render(policy) != policy_raw or policy != pinned_policy:
        _fail("TASK8_V23_POLICY_BINDING_INVALID")
    policy_artifact_sha256 = policy_artifact_sha256 or hashlib.sha256(policy_raw).hexdigest()
    policy_fixture_sha256 = policy_fixture_sha256 or hashlib.sha256(fixture_raw).hexdigest()
    if (
        policy_artifact_sha256 != hashlib.sha256(policy_raw).hexdigest()
        or policy_fixture_sha256 != hashlib.sha256(fixture_raw).hexdigest()
    ):
        _fail("TASK8_V23_POLICY_BINDING_INVALID")
    artifact = _build_core(
        pinned_v22,
        pinned_policy,
        policy_artifact_sha256=policy_artifact_sha256,
        policy_fixture_sha256=policy_fixture_sha256,
        policy_fixture=fixture,
    )
    derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=pinned_policy,
    )
    return artifact


def validate_frozen_artifact(
    artifact_raw: bytes,
    *,
    policy_artifact_raw: bytes,
    policy_fixture_raw: bytes,
) -> dict[str, Any]:
    """Validate exact frozen bytes against the bounded deterministic build."""

    _v22_raw, v22, pinned_policy_raw, policy, pinned_fixture_raw, _fixture = _inputs()
    if policy_artifact_raw != pinned_policy_raw or policy_fixture_raw != pinned_fixture_raw:
        _fail("TASK8_V23_POLICY_BINDING_INVALID")
    artifact = _decode(artifact_raw, "TASK8_V23_CONTRACT_ARTIFACT_INVALID")
    if _render(artifact) != artifact_raw:
        _fail("TASK8_V23_CONTRACT_ARTIFACT_INVALID")
    expected = build_v23(copy.deepcopy(v22), copy.deepcopy(policy))
    if artifact != expected:
        _fail("TASK8_V23_EXACT_DIFF_INVALID")
    if (
        artifact.get("metadata", {}).get("append_frozen") is not True
        or artifact.get("metadata", {}).get("activation_status") != "INACTIVE"
        or artifact.get("activation_assertions", {}).get("runtime_selectable") is not False
    ):
        _fail("TASK8_V23_CONTRACT_ARTIFACT_INVALID")
    return artifact


def derive_v23_fixture(
    *,
    artifact: Mapping[str, Any],
    artifact_raw: bytes,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the inactive v23 fixture, including the complete graph tuples."""

    components = {
        f"{section}_hash": _canonical_sha256(["full-strict-v23-component-v1", section, value])
        for section, value in artifact.items()
    }
    components["task8_fdd_contract_hash"] = str(artifact["task8_fdd_contract"]["expected_sha256"])
    ordered = [[name, digest] for name, digest in components.items()]
    pipeline_hash = _canonical_sha256(
        [
            "full-strict-v23-pipeline-contract-v1",
            _V23,
            hashlib.sha256(artifact_raw).hexdigest(),
            ordered,
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    erasure = artifact["erasure_execution_registry"]["rows"][0]
    task11 = artifact["task8_fdd_contract"]["task11_exit_obligation"]
    graph = derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=policy,
    )
    return {
        "artifact_schema": "full-strict-v23-contract-hash-fixture-v1",
        "pipeline_contract_version": _V23,
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "FULL_STRICT_V23_CONTRACT_HASH": pipeline_hash,
        "component_hashes": components,
        "identity_hash_dependency_graph": graph,
        "policy_binding": {
            "policy_id": _POLICY_ID,
            "artifact_sha256": hashlib.sha256(_render(policy)).hexdigest(),
            "policy_sha256": policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"],
        },
        "counts": {
            "semantic_step_count": len(artifact["semantic_step_registry"]["rows"]),
            "edge_rule_count": len(artifact["edge_rule_registry"]["rows"]),
            "usage_route_count": len(artifact["usage_route_registry"]["rows"]),
            "usage_extractor_count": len(artifact["usage_extractor_registry"]["rows"]),
            "provider_count": len(artifact["provider_registry"]["rows"]),
            "schema_count": len(artifact["schema_registry"]["rows"]),
            "current_ddl_table_count": len(artifact["current_ddl_registry"]["rows"]),
            "installed_tenant_rls_table_count": artifact["task8_fdd_contract"][
                "installed_partition"
            ]["successor_tenant_rls_table_count"],
            "erasure_current_relation_count": len(erasure["current_relation_tuple"]),
            "erasure_future_relation_count": len(erasure["future_relation_tuple"]),
            "erasure_complete_relation_count": len(erasure["complete_relation_tuple"]),
            "task11_restart_cut_count": task11["restart_cut_count"],
            "task11_undischarged_rule_count": len(task11["undischarged_rule_tuple"]),
        },
        "activation_status": "INACTIVE",
    }


def _write_exclusive(path: Path, raw: bytes) -> None:
    if not path.parent.is_dir():
        _fail("TASK8_V23_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
    except FileExistsError as exc:
        raise MaterializationError("TASK8_V23_OUTPUT_ALREADY_EXISTS") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture-output", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _v22_raw, v22, _policy_raw, policy, _fixture_raw, _fixture = _inputs()
        artifact = build_v23(v22, policy)
        raw = _render(artifact)
        _write_exclusive(args.output, raw)
        if args.fixture_output is not None:
            fixture = derive_v23_fixture(artifact=artifact, artifact_raw=raw, policy=policy)
            _write_exclusive(args.fixture_output, _render(fixture))
    except (OSError, MaterializationError) as exc:
        code = str(exc) if isinstance(exc, MaterializationError) else "TASK8_V23_IO_ERROR"
        print(json.dumps({"reason_code": code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(json.dumps({"activation_status": "INACTIVE", "result": "MATERIALIZED"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IdentityHashDependencyError",
    "MaterializationError",
    "build_v23",
    "derive_dashboard_identity_vector",
    "derive_identity_hash_dependency_graph",
    "derive_v23_fixture",
    "main",
    "validate_frozen_artifact",
]
