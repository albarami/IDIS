"""Independent RED oracle for the Task-8.1 v23 identity/hash graph.

This module deliberately does not import the v23 materializer at collection
time.  The first RED run therefore distinguishes an absent implementation
from a malformed graph instead of failing during pytest collection.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
V22_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
V22_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v22_contract_hash_v1.json"
)
V23_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
V23_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v23_contract_hash_v1.json"
)
POLICY_V1_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
POLICY_V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_V2_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
V23_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v23_preimage.py"
TASK8_VOID_EVIDENCE = (
    REPO_ROOT / "tests" / "slice100_task8" / ("_task8_void_contract_hash_evidence.py")
)

V22_ARTIFACT_SHA256 = "94edc7b19fe73ed75872ad052c84b7a9fb341d32b9741f98a1a4c048a058b507"
V22_FIXTURE_SHA256 = "4368d86b6bdd270f03d94bbc591a9ee2f6fbc6c36c9c65076cd1549d2a4da56b"
V22_CONTRACT_SHA256 = "a8294752ae392f72cbdf83ced9107df12243f3f26010347457f86603a302dc23"
VOID_V22_HASHES = (
    V22_ARTIFACT_SHA256,
    V22_FIXTURE_SHA256,
    V22_CONTRACT_SHA256,
)

V23_ARTIFACT_SCHEMA = "full-strict-v23-reviewed-preimage-v1"
V23_CONTRACT_VERSION = "full-strict-v23"
POLICY_V2_ID = "fdd-formula-policy-v2"
POLICY_V2_ARTIFACT_SCHEMA = "fdd-formula-policy-v2"
GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v1"
DASHBOARD_SCOPE = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
DASHBOARD_OUTPUT_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
DASHBOARD_ROW_NAME_TUPLE = (
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
V22_DASHBOARD_CYCLE = (
    '["full_strict","RUNTIME_VALUE","METHODOLOGY_TRUTH_DASHBOARD_ROW",'
    '"METHODOLOGY_TRUTH_DASHBOARD_ROW","native_output_sha256"]',
    '["full_strict","RUNTIME_VALUE","METHODOLOGY_TRUTH_DASHBOARD_ROW",'
    '"METHODOLOGY_TRUTH_DASHBOARD_ROW","root_projection_sha256"]',
    '["full_strict","RUNTIME_VALUE","METHODOLOGY_TRUTH_DASHBOARD_ROW",'
    '"METHODOLOGY_TRUTH_DASHBOARD_ROW","output_object_id"]',
    '["full_strict","RUNTIME_VALUE","METHODOLOGY_TRUTH_DASHBOARD_ROW",'
    '"METHODOLOGY_TRUTH_DASHBOARD_ROW","native_output_sha256"]',
)

EXPECTED_SOURCE_POINTER_TUPLE = (
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

SEMANTICALLY_INVARIANT_SECTIONS = (
    "semantic_step_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "current_ddl_registry",
    "erasure_baseline",
    "erasure_materialized",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class V23OracleError(AssertionError):
    """Stable failure carrying no operator secrets or private document data."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V23OracleError(code)


def load_json(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), f"{code}:{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V23OracleError(f"{code}_INVALID:{path.name}") from exc
    require(isinstance(value, dict), f"{code}_INVALID:{path.name}")
    return value


def load_v22() -> dict[str, Any]:
    raw = V22_ARTIFACT.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == V22_ARTIFACT_SHA256, "TASK8_V22_BYTES_DRIFT")
    return load_json(V22_ARTIFACT, "TASK8_V22_ARTIFACT_MISSING")


def load_v23() -> dict[str, Any]:
    return load_json(V23_ARTIFACT, "TASK8_V23_ARTIFACT_NOT_IMPLEMENTED")


def load_policy_v1() -> dict[str, Any]:
    return load_json(POLICY_V1_ARTIFACT, "TASK8_FDD_POLICY_V1_MISSING")


def load_policy_v2() -> dict[str, Any]:
    return load_json(POLICY_V2_ARTIFACT, "TASK8_FDD_POLICY_V2_NOT_IMPLEMENTED")


def load_materializer() -> ModuleType:
    require(V23_MATERIALIZER.is_file(), "TASK8_V23_MATERIALIZER_NOT_IMPLEMENTED")
    spec = importlib.util.spec_from_file_location("_task8_v23_materializer_red", V23_MATERIALIZER)
    require(spec is not None and spec.loader is not None, "TASK8_V23_MATERIALIZER_IMPORT_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def json_pointer(parts: Sequence[str | int]) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "#/" + "/".join(encoded)


def _walk_preimages(
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
                result.add(f"{artifact_name}:{json_pointer(child_path)}")
            result.update(_walk_preimages(child, artifact_name=artifact_name, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_walk_preimages(child, artifact_name=artifact_name, path=(*path, index)))
    return result


def discover_declared_source_pointers(
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    """Discover every governed UUID tuple and explicit hash/preimage declaration.

    The independent discovery intentionally understands only the artifact's
    public declaration shapes.  A new declaration shape must update this
    oracle and the production closed grammar together; silent discovery drift
    is not admissible.
    """

    pointers = _walk_preimages(full_strict, artifact_name="full_strict")
    pointers.update(_walk_preimages(policy, artifact_name="fdd_formula_policy"))

    uuid_rows = full_strict.get("uuid_namespace_registry", {}).get("rows", [])
    require(isinstance(uuid_rows, list), "TASK8_IDENTITY_HASH_UUID_REGISTRY_INVALID")
    for index, row in enumerate(uuid_rows):
        require(
            isinstance(row, list) and len(row) == 3,
            "TASK8_IDENTITY_HASH_UUID_REGISTRY_ROW_INVALID",
        )
        pointers.add(f"full_strict:{json_pointer(('uuid_namespace_registry', 'rows', index))}")

    digest_rows = full_strict.get("digest_recipe_registry", {}).get("rows", [])
    require(isinstance(digest_rows, list), "TASK8_IDENTITY_HASH_DIGEST_REGISTRY_INVALID")
    for index, row in enumerate(digest_rows):
        require(
            isinstance(row, list) and len(row) == 2,
            "TASK8_IDENTITY_HASH_DIGEST_REGISTRY_ROW_INVALID",
        )
        pointers.add(f"full_strict:{json_pointer(('digest_recipe_registry', 'rows', index))}")

    return tuple(sorted(pointers))


def graph_node_id(node: Sequence[object]) -> str:
    require(len(node) == 6, "TASK8_IDENTITY_HASH_GRAPH_NODE_SHAPE_INVALID")
    return canonical_json(list(node[:5])).decode("utf-8")


def graph_dependencies(
    graph: Mapping[str, Any],
    *,
    scope: str,
    output_symbol: str,
    artifact: str = "full_strict",
    output_kind: str = DASHBOARD_OUTPUT_KIND,
) -> set[str]:
    nodes = graph["node_tuple"]
    by_id = {graph_node_id(node): node for node in nodes}
    matches = [
        graph_node_id(node)
        for node in nodes
        if node[0] == artifact
        and node[1] == "RUNTIME_VALUE"
        and node[2] == scope
        and node[3] == output_kind
        and node[4] == output_symbol
    ]
    require(len(matches) == 1, f"TASK8_IDENTITY_HASH_GRAPH_NODE_MISSING:{output_symbol}")
    dependencies: set[str] = set()
    for derived, dependency, _token_pointer in graph["edge_tuple"]:
        if derived == matches[0]:
            require(dependency in by_id, "TASK8_IDENTITY_HASH_GRAPH_EDGE_TARGET_INVALID")
            dependencies.add(str(by_id[dependency][4]))
    return dependencies


def deterministic_cycle_path(graph: Mapping[str, set[str]]) -> tuple[str, ...] | None:
    """Return the lexicographically first closed DFS cycle, if one exists."""

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


def held_v22_dashboard_graph() -> dict[str, set[str]]:
    """Reconstruct the rejected v22 dashboard cycle from literal authorities."""

    v22 = load_v22()
    policy = load_policy_v1()
    uuid_rows = v22["uuid_namespace_registry"]["rows"]
    output_tuple = next(row[2] for row in uuid_rows if row[0] == "output_object_id")
    recipes = {row[0]: row[1] for row in v22["digest_recipe_registry"]["rows"]}
    partial = v22["task8_fdd_contract"]["partial_exact_one_contract"]
    financial = policy["financial_output_schema"]

    names = {
        symbol: canonical_json(
            [
                "full_strict",
                "RUNTIME_VALUE",
                DASHBOARD_SCOPE,
                DASHBOARD_OUTPUT_KIND,
                symbol,
            ]
        ).decode("utf-8")
        for symbol in (
            "output_object_id",
            "native_output_sha256",
            "root_projection_sha256",
            "row_payload_sha256",
        )
    }
    graph = {node: set() for node in names.values()}
    if "native_output_sha256" in output_tuple:
        graph[names["output_object_id"]].add(names["native_output_sha256"])
    native_payload = partial["dashboard_native_output_preimage"][-1]
    if "root_projection_sha256" in native_payload:
        graph[names["native_output_sha256"]].add(names["root_projection_sha256"])
    if "row_payload_sha256" in native_payload:
        graph[names["native_output_sha256"]].add(names["row_payload_sha256"])
    projection = recipes["methodology_truth_dashboard_root_projection_sha256"]
    if "run_output_object_id" in projection:
        graph[names["root_projection_sha256"]].add(names["output_object_id"])
    if "dashboard_output_object_id" in financial["identity_and_trace_tuple"]:
        graph[names["row_payload_sha256"]].add(names["output_object_id"])
    return graph


def fixture_graph_contract(fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    graph = fixture.get("identity_hash_dependency_graph")
    require(isinstance(graph, dict), "TASK8_V23_FIXTURE_GRAPH_BINDING_MISSING")
    return graph


def uuid5_from_canonical_name(namespace: str, name_tuple: object) -> str:
    """Derive the reviewed UUIDv5 name from lowercase canonical SHA-256."""

    name = hashlib.sha256(canonical_json(name_tuple)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), name))


def expected_dashboard_identity_vector(
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
    """Independent executable vector for the approved five-step derivation."""

    row_id = uuid5_from_canonical_name(
        DASHBOARD_ROW_NAMESPACE,
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
        link_id = uuid5_from_canonical_name(
            DASHBOARD_ROOT_LINK_NAMESPACE,
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
    projection = canonical_sha256(
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
    identity_and_trace = [*row_trace_prefix, coverage_record_id, row_id]
    row_payload = canonical_sha256(
        [
            "financial-diligence-row-v2",
            identity_and_trace,
            row_disposition,
            disposition_reason_code,
            result_records,
            answer_values,
            task_gaps,
            calculations,
            projection,
        ]
    )
    native_identity = canonical_sha256(
        [
            "native-identity-v1",
            tenant_id,
            deal_id,
            run_id,
            DASHBOARD_OUTPUT_KIND,
            [row_id],
        ]
    )
    native_output = canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_contract_version,
            pipeline_contract_hash,
            DASHBOARD_OUTPUT_KIND,
            [row_id],
            [coverage_record_id, projection, row_disposition, row_payload],
        ]
    )
    output_object_id = uuid5_from_canonical_name(
        OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_contract_version,
            pipeline_contract_hash,
            "NATIVE_ROW",
            DASHBOARD_OUTPUT_KIND,
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
