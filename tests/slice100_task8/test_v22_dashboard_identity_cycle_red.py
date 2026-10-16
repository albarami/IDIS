"""RED admission for an acyclic, deterministic dashboard native root."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
V21 = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
V22 = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
POLICY = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _has_cycle(graph: Mapping[str, set[str]]) -> bool:
    active: set[str] = set()
    complete: set[str] = set()

    def visit(node: str) -> bool:
        if node in active:
            return True
        if node in complete:
            return False
        active.add(node)
        if any(visit(child) for child in graph.get(node, set())):
            return True
        active.remove(node)
        complete.add(node)
        return False

    return any(visit(node) for node in graph)


def test_dashboard_native_identity_and_hash_dependency_graph_is_acyclic() -> None:
    v21 = _load(V21)
    v22 = _load(V22)
    policy = _load(POLICY)

    output_namespace = next(
        row for row in v21["uuid_namespace_registry"]["rows"] if row[0] == "output_object_id"
    )
    native = v22["task8_fdd_contract"]["partial_exact_one_contract"]
    projection = next(
        row[1]
        for row in v21["digest_recipe_registry"]["rows"]
        if row[0] == "methodology_truth_dashboard_root_projection_sha256"
    )
    financial = policy["financial_output_schema"]

    graph: dict[str, set[str]] = {
        "output_object_id": set(),
        "native_output_sha256": set(),
        "root_projection_sha256": set(),
        "row_payload_sha256": set(),
    }
    if "native_output_sha256" in output_namespace[2]:
        graph["output_object_id"].add("native_output_sha256")
    native_payload = native["dashboard_native_output_preimage"][-1]
    if "root_projection_sha256" in native_payload:
        graph["native_output_sha256"].add("root_projection_sha256")
    if "row_payload_sha256" in native_payload:
        graph["native_output_sha256"].add("row_payload_sha256")
    if "run_output_object_id" in projection:
        graph["root_projection_sha256"].add("output_object_id")
    if "dashboard_output_object_id" in financial["identity_and_trace_tuple"]:
        graph["row_payload_sha256"].add("output_object_id")

    assert not _has_cycle(graph), "TASK8_DASHBOARD_NATIVE_DIGEST_CYCLE"


def test_dashboard_row_id_has_a_frozen_deterministic_namespace_and_name_tuple() -> None:
    v22 = _load(V22)
    matches = [
        row for row in v22["uuid_namespace_registry"]["rows"] if row[0] == "dashboard_row_id"
    ]
    assert len(matches) == 1, "TASK8_DASHBOARD_ROW_UUID_AUTHORITY_MISSING"
    _kind, namespace, name_tuple = matches[0]
    assert namespace == "6aef4159-c49a-560a-9f92-6c6089d7f7c3"
    assert name_tuple == [
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
    ]
