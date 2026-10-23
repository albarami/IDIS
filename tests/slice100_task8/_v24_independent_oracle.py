"""Independent Task-8 v24 fixture and dashboard-identity test oracle.

This module is deliberately test-owned.  It does not import a Task-8
materializer, fixture generator, publisher, census runner, or IDIS runtime
module.  Literal contract domains and the hand-written identity vector are the
second implementation required by the v24 fixture-independence ruling.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unicodedata
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
V24_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v24_contract_hash_v1.json"
)
POLICY_V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_V2_REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
INDEPENDENT_VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v24_fixture_independently.py"
GOLDEN_VECTOR = (
    REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "full_strict_v24_dashboard_identity_golden_v1.json"
)

PIPELINE_VERSION = "full-strict-v24"
COMPONENT_DOMAIN = "full-strict-v24-component-v1"
PIPELINE_DOMAIN = "full-strict-v24-pipeline-contract-v1"
GRAPH_SCHEMA = "task8-identity-hash-dependency-graph-v3"
CONSERVATION_SCHEMA = "task8-v24-approved-semantics-conservation-v1"
DASHBOARD_ROW_NAMESPACE = "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
DASHBOARD_ROOT_LINK_NAMESPACE = "58a9370f-69f9-5873-b6b7-508272aee80e"
OUTPUT_OBJECT_NAMESPACE = "b3ef55a1-2eef-5712-836c-4870bc3ef6d4"
DASHBOARD_OUTPUT_KIND = "METHODOLOGY_TRUTH_DASHBOARD_ROW"

_FORBIDDEN_VERIFIER_IMPORTS = (
    "idis",
    "importlib",
    "runpy",
    "subprocess",
    "scripts.generate_task8_contract_fixtures",
    "scripts.materialize",
    "tests.slice100_task8",
)
_FORBIDDEN_VERIFIER_SOURCE_MARKERS = (
    "materialize_full_strict",
    "generate_task8_contract_fixtures",
    "publish_task8_contract_artifact_gate",
    "_task8_v24_materializer",
    "_v24_independent_oracle",
    "__import__(",
)


class V24IndependentOracleError(AssertionError):
    """Safe assertion with a stable code and no operator data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise V24IndependentOracleError(code)


def canonical_json(value: object) -> bytes:
    """Render the reviewed canonical JSON subset without production imports."""

    normalized = _normalize_canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize_canonical_value(value: object) -> object:
    if isinstance(value, float):
        require(math.isfinite(value), "TASK8_V24_INDEPENDENT_BINARY64_NONFINITE")
        return 0 if value == 0.0 else value
    if isinstance(value, str):
        require(
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
            require(isinstance(key, str), "TASK8_V24_INDEPENDENT_NON_STRING_KEY")
            normalized_key = _normalize_canonical_value(key)
            require(isinstance(normalized_key, str), "TASK8_V24_INDEPENDENT_NON_STRING_KEY")
            result[normalized_key] = _normalize_canonical_value(item)
        return result
    raise V24IndependentOracleError("TASK8_V24_INDEPENDENT_VALUE_TYPE_INVALID")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def render_artifact(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def load_json_bytes(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise V24IndependentOracleError(code) from exc
    require(isinstance(value, dict), code)
    return value


def load_json_file(path: Path, code: str) -> dict[str, Any]:
    require(path.is_file(), code)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise V24IndependentOracleError(code) from exc
    return load_json_bytes(raw, code)


def load_golden_vector() -> dict[str, Any]:
    value = load_json_file(GOLDEN_VECTOR, "TASK8_V24_GOLDEN_VECTOR_MISSING")
    require(
        value.get("schema_version") == "task8-v24-dashboard-identity-golden-v1",
        "TASK8_V24_GOLDEN_VECTOR_SCHEMA_INVALID",
    )
    require(
        value.get("derivation_authority") == "HAND_WRITTEN_INDEPENDENT_VECTOR",
        "TASK8_V24_GOLDEN_VECTOR_AUTHORITY_INVALID",
    )
    return value


def load_verifier() -> ModuleType:
    require(
        INDEPENDENT_VERIFIER.is_file(),
        "TASK8_V24_INDEPENDENT_VERIFIER_NOT_IMPLEMENTED",
    )
    spec = importlib.util.spec_from_file_location(
        "_task8_v24_independent_verifier_red",
        INDEPENDENT_VERIFIER,
    )
    require(
        spec is not None and spec.loader is not None,
        "TASK8_V24_INDEPENDENT_VERIFIER_IMPORT_INVALID",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise V24IndependentOracleError("TASK8_V24_INDEPENDENT_VERIFIER_IMPORT_INVALID") from exc
    return module


def assert_verifier_source_is_independent(raw: bytes) -> None:
    """Reject direct and reflective access to the production derivation path."""

    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source)
    except (UnicodeError, SyntaxError) as exc:
        raise V24IndependentOracleError("TASK8_V24_INDEPENDENT_VERIFIER_SOURCE_INVALID") from exc

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            require(
                node.func.id != "exec" and node.func.id != "eval",
                "TASK8_V24_INDEPENDENT_VERIFIER_COUPLED",
            )
    for module_name in imported:
        require(
            not any(
                module_name == forbidden or module_name.startswith(f"{forbidden}.")
                for forbidden in _FORBIDDEN_VERIFIER_IMPORTS
            ),
            "TASK8_V24_INDEPENDENT_VERIFIER_COUPLED",
        )
    lowered = source.lower()
    for marker in _FORBIDDEN_VERIFIER_SOURCE_MARKERS:
        require(
            marker.lower() not in lowered,
            "TASK8_V24_INDEPENDENT_VERIFIER_COUPLED",
        )


def independent_component_hashes(
    artifact: Mapping[str, Any],
) -> dict[str, str]:
    """Recompute v24 components from literal artifact sections."""

    components = {
        f"{section}_hash": canonical_sha256([COMPONENT_DOMAIN, section, copy.deepcopy(value)])
        for section, value in artifact.items()
    }
    task8 = artifact.get("task8_fdd_contract")
    require(
        isinstance(task8, Mapping) and isinstance(task8.get("expected_sha256"), str),
        "TASK8_V24_INDEPENDENT_TASK8_COMPONENT_INVALID",
    )
    components["task8_fdd_contract_hash"] = str(task8["expected_sha256"])
    return components


def independent_contract_hash(
    artifact: Mapping[str, Any],
    artifact_raw: bytes,
) -> tuple[dict[str, str], str]:
    components = independent_component_hashes(artifact)
    ordered = [[name, digest] for name, digest in components.items()]
    contract_hash = canonical_sha256(
        [
            PIPELINE_DOMAIN,
            PIPELINE_VERSION,
            hashlib.sha256(artifact_raw).hexdigest(),
            ordered,
        ]
    )
    result = dict(components)
    result["pipeline_contract_hash"] = contract_hash
    return result, contract_hash


def independent_conservation_hash(artifact: Mapping[str, Any]) -> str:
    admission = artifact.get("task8_structural_admission")
    require(
        isinstance(admission, Mapping),
        "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_MISSING",
    )
    manifest = admission.get("approved_semantics_conservation_manifest")
    require(
        isinstance(manifest, list) and len(manifest) == 7,
        "TASK8_V24_INDEPENDENT_CONSERVATION_MANIFEST_INVALID",
    )
    return canonical_sha256([CONSERVATION_SCHEMA, manifest])


def independent_fixture_graph_hash(
    fixture: Mapping[str, Any],
) -> tuple[str, dict[str, int]]:
    graph = fixture.get("identity_hash_dependency_graph")
    require(
        isinstance(graph, Mapping) and graph.get("schema_version") == GRAPH_SCHEMA,
        "TASK8_V24_INDEPENDENT_GRAPH_MISSING",
    )
    node_tuple = graph.get("node_tuple")
    edge_tuple = graph.get("edge_tuple")
    leaf_tuple = graph.get("leaf_tuple")
    source_pointer_tuple = graph.get("source_pointer_tuple")
    require(
        all(
            isinstance(value, list)
            for value in (node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple)
        ),
        "TASK8_V24_INDEPENDENT_GRAPH_SHAPE_INVALID",
    )
    counts = {
        "node_count": len(node_tuple),
        "edge_count": len(edge_tuple),
        "leaf_count": len(leaf_tuple),
        "source_pointer_count": len(source_pointer_tuple),
    }
    require(
        graph.get("counts") == counts,
        "TASK8_V24_INDEPENDENT_GRAPH_COUNT_MISMATCH",
    )
    digest = canonical_sha256(
        [GRAPH_SCHEMA, node_tuple, edge_tuple, leaf_tuple, source_pointer_tuple]
    )
    require(
        graph.get("graph_sha256") == digest,
        "TASK8_V24_INDEPENDENT_GRAPH_HASH_MISMATCH",
    )
    return digest, counts


def refreshed_fixture_hash_bindings(
    artifact: Mapping[str, Any],
    fixture: Mapping[str, Any],
) -> tuple[bytes, bytes]:
    """Refresh only static hashes so graph-specific RED vectors reach the graph."""

    artifact_raw = render_artifact(artifact)
    components, contract_hash = independent_contract_hash(artifact, artifact_raw)
    refreshed = copy.deepcopy(dict(fixture))
    refreshed["artifact_sha256"] = hashlib.sha256(artifact_raw).hexdigest()
    refreshed["component_hashes"] = components
    refreshed["FULL_STRICT_V24_CONTRACT_HASH"] = contract_hash
    conservation = refreshed.get("approved_semantics_conservation")
    if isinstance(conservation, dict):
        conservation["sha256"] = independent_conservation_hash(artifact)
    return artifact_raw, render_artifact(refreshed)


def uuid5_from_canonical_name(namespace: str, value: object) -> str:
    canonical_name_sha256 = hashlib.sha256(canonical_json(value)).hexdigest()
    return str(uuid.uuid5(uuid.UUID(namespace), canonical_name_sha256))


def derive_dashboard_identity_vector(**inputs: Any) -> dict[str, object]:
    """Independent implementation of the approved acyclic five-stage chain."""

    tenant_id = str(inputs["tenant_id"])
    deal_id = str(inputs["deal_id"])
    run_id = str(inputs["run_id"])
    pipeline_version = str(inputs["pipeline_contract_version"])
    pipeline_hash = str(inputs["pipeline_contract_hash"])
    coverage_record_id = str(inputs["coverage_record_id"])
    row_id = uuid5_from_canonical_name(
        DASHBOARD_ROW_NAMESPACE,
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
        root_links.append(
            [
                root["root_kind"],
                root["root_ordinal"],
                root["root_output_object_id"],
                root["root_output_object_kind"],
                link_id,
            ]
        )

    projection_hash = canonical_sha256(
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
    payload_hash = canonical_sha256(
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
    native_identity_hash = canonical_sha256(
        [
            "native-identity-v1",
            tenant_id,
            deal_id,
            run_id,
            DASHBOARD_OUTPUT_KIND,
            [row_id],
        ]
    )
    native_output_hash = canonical_sha256(
        [
            "native-output-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            DASHBOARD_OUTPUT_KIND,
            [row_id],
            [
                coverage_record_id,
                projection_hash,
                inputs["row_disposition"],
                payload_hash,
            ],
        ]
    )
    output_object_id = uuid5_from_canonical_name(
        OUTPUT_OBJECT_NAMESPACE,
        [
            "run-output-object-v1",
            tenant_id,
            deal_id,
            run_id,
            pipeline_version,
            pipeline_hash,
            "NATIVE_ROW",
            DASHBOARD_OUTPUT_KIND,
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


def verifier_error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    require(isinstance(code, str), "TASK8_V24_INDEPENDENT_ERROR_CODE_MISSING")
    return code


__all__ = [
    "CONSERVATION_SCHEMA",
    "GOLDEN_VECTOR",
    "GRAPH_SCHEMA",
    "INDEPENDENT_VERIFIER",
    "POLICY_V2_ARTIFACT",
    "POLICY_V2_REVIEWER_MAP",
    "V24_ARTIFACT",
    "V24_FIXTURE",
    "V24IndependentOracleError",
    "assert_verifier_source_is_independent",
    "canonical_sha256",
    "derive_dashboard_identity_vector",
    "independent_component_hashes",
    "independent_conservation_hash",
    "independent_contract_hash",
    "independent_fixture_graph_hash",
    "load_golden_vector",
    "load_json_bytes",
    "load_json_file",
    "load_verifier",
    "refreshed_fixture_hash_bindings",
    "render_artifact",
    "require",
    "verifier_error_code",
]
