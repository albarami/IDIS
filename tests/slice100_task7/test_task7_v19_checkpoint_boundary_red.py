"""Authority for Task 7's honest frozen-v20 -> Task 11 proof boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V20_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_V20_ARTIFACT_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_V20_FIXTURE_SHA256 = "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
_V20_CONTRACT_HASH = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
_EXPECTED_TASK11_RULE_IDS = (
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


def _load_v20() -> dict[str, Any]:
    value = json.loads(_V20_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rows(artifact: Mapping[str, Any], section: str) -> tuple[Mapping[str, Any], ...]:
    container = artifact[section]
    assert isinstance(container, Mapping)
    rows = container["rows"]
    assert isinstance(rows, list)
    assert all(isinstance(row, Mapping) for row in rows)
    return tuple(cast(Mapping[str, Any], row) for row in rows)


def _future_relations(artifact: Mapping[str, Any]) -> frozenset[str]:
    native = tuple(
        cast(str, row["planned_table"])
        for row in _rows(artifact, "future_native_authority_registry")
    )
    references = tuple(
        cast(str, row["table_name"])
        for row in _rows(artifact, "future_reference_authority_registry")
    )
    children = tuple(
        cast(str, row["table_name"]) for row in _rows(artifact, "future_child_authority_registry")
    )
    assert len(native) == 18
    assert len(references) == 15
    assert len(children) == 5
    assert len(set(native) | set(references) | set(children)) == 38
    return frozenset((*native, *references, *children))


def _instance_relations(instance: Mapping[str, Any]) -> tuple[str, ...]:
    concrete = instance["selector_ast"]
    assert isinstance(concrete, list) and len(concrete) == 3
    assert concrete[0] == "concrete-producer-selector-v2"
    spec = concrete[1]
    assert isinstance(spec, list) and len(spec) == 9
    assert spec[0] == "producer-selector-spec-v2"
    relations = spec[4]
    assert isinstance(relations, list)
    assert relations and all(isinstance(relation, str) for relation in relations)
    return tuple(cast(list[str], relations))


def _derive_task11_rule_ids(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    future = _future_relations(artifact)
    blocked: list[str] = []
    for row in _rows(artifact, "producer_selector_registry"):
        instances = row["instances"]
        assert isinstance(instances, list) and instances
        relations = {
            relation
            for instance in instances
            for relation in _instance_relations(cast(Mapping[str, Any], instance))
        }
        if relations & future:
            blocked.append(cast(str, row["rule_id"]))
    return tuple(blocked)


def _boundary_module() -> ModuleType:
    try:
        return importlib.import_module("idis.pipeline.task7_checkpoint_boundary")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "TASK7_V20_CHECKPOINT_BOUNDARY_MODULE_ABSENT:idis.pipeline.task7_checkpoint_boundary",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc


def test_frozen_v20_artifact_and_fixture_remain_byte_identical() -> None:
    artifact_bytes = _V20_ARTIFACT.read_bytes()
    fixture_bytes = _V20_FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)

    assert hashlib.sha256(artifact_bytes).hexdigest() == _V20_ARTIFACT_SHA256
    assert hashlib.sha256(fixture_bytes).hexdigest() == _V20_FIXTURE_SHA256
    assert fixture["FULL_STRICT_V20_CONTRACT_HASH"] == _V20_CONTRACT_HASH


def test_future_relation_partition_is_exact_complete_and_disjoint() -> None:
    artifact = _load_v20()

    assert len(_future_relations(artifact)) == 38


def test_task11_rule_inventory_is_derived_from_v20_selector_relation_intersection() -> None:
    artifact = _load_v20()
    edge_rows = _rows(artifact, "edge_rule_registry")
    selector_rows = _rows(artifact, "producer_selector_registry")
    all_rule_ids = tuple(cast(str, row["rule_id"]) for row in edge_rows)

    assert len(edge_rows) == 48
    assert len(selector_rows) == 48
    assert tuple(cast(str, row["rule_id"]) for row in selector_rows) == all_rule_ids
    assert _derive_task11_rule_ids(artifact) == _EXPECTED_TASK11_RULE_IDS
    assert set(_EXPECTED_TASK11_RULE_IDS) < set(all_rule_ids)


def test_v20_boundary_module_assigns_27_cut_all_48_equality_only_to_task11() -> None:
    artifact = _load_v20()
    module = _boundary_module()

    assert tuple(
        module.derive_task11_producer_blocked_rule_ids(artifact)
    ) == _derive_task11_rule_ids(artifact)
    assert tuple(module.TASK11_PRODUCER_BLOCKED_RULE_IDS) == _EXPECTED_TASK11_RULE_IDS
    assert module.TASK11_REQUIRED_RESTART_CUTS == 27
    assert module.TASK11_REQUIRED_EDGE_RULE_COUNT == 48
    assert module.TASK11_REQUIRES_THREE_WAY_EQUALITY is True
    assert module.TASK7_CLAIMS_COMPLETE_RESTART_EQUALITY is False
