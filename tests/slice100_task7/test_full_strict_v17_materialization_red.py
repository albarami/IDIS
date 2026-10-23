"""RED-first admission contracts for the bounded full-strict-v17 successor."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from tests._private_artifacts import requires_private_artifact

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V16_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_V16_CENSUS_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v16_current_guard_postgres_census_receipt.json"
)
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v17_preimage.py"

_V16_ARTIFACT_BYTES = 5_875_678
_V16_ARTIFACT_SHA256 = "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
_V16_FIXTURE_SHA256 = "999dafef745feaa56448e0a84ebed3aff37729c3d85f98267ae72a19d702ab79"
_V16_CONTRACT_SHA256 = "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
_V16_CENSUS_RECEIPT_SHA256 = "b47f062aa339d8fa288e4b1eefc9ed7fea25a4a02920bc9fcc0adc964dbb41ce"

_ALLOWED_CHANGED_TOP_LEVEL_SECTIONS = {
    "metadata",
    "digest_recipe_registry",
    "sql_authority_registry",
}
_ALLOWED_CHANGED_AUTHORITY_ORDINALS = {17, 19, 22, 26}
_SEMANTIC_INVARIANT_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
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
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_v17_assets() -> tuple[dict[str, Any], ModuleType]:
    missing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_V17_ARTIFACT, _V17_MATERIALIZER)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V17_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)
    return (
        _load_json(_V17_ARTIFACT),
        _load_module(_V17_MATERIALIZER, "_task7_v17_materialization_red"),
    )


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        if set(old) != set(new):
            return {path}
        changed: set[tuple[str | int, ...]] = set()
        for key in old:
            changed.update(_changed_paths(old[key], new[key], (*path, key)))
        return changed
    if isinstance(old, list):
        if len(old) != len(new):
            return {path}
        changed = set()
        for ordinal, (old_item, new_item) in enumerate(zip(old, new, strict=True)):
            changed.update(_changed_paths(old_item, new_item, (*path, ordinal)))
        return changed
    return set() if old == new else {path}


def _authority(artifact: dict[str, Any], ordinal: int) -> dict[str, Any]:
    row = artifact["sql_authority_registry"]["rows"][ordinal]
    assert row["authority_ordinal"] == ordinal
    return row


def _decoded_statements(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        bytes.fromhex(statement).decode("utf-8") for statement in row["body_sql_utf8_hex_tuple"]
    )


def _candidate_bytes(materializer: ModuleType, artifact: dict[str, Any]) -> bytes:
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    return materializer._render(candidate)


@requires_private_artifact("task7_v16_current_guard_postgres_census_receipt.json")
def test_v16_frozen_authority_remains_byte_immutable_before_v17_materialization() -> None:
    raw = _V16_ARTIFACT.read_bytes()
    artifact = _load_json(_V16_ARTIFACT)
    fixture = _load_json(_V16_FIXTURE)

    assert len(raw) == _V16_ARTIFACT_BYTES
    assert hashlib.sha256(raw).hexdigest() == _V16_ARTIFACT_SHA256
    assert _sha256(_V16_FIXTURE) == _V16_FIXTURE_SHA256
    assert _sha256(_V16_CENSUS_RECEIPT) == _V16_CENSUS_RECEIPT_SHA256
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v16-reviewed-preimage-v1",
        "append_frozen": True,
        "pipeline_contract_version": "full-strict-v16",
    }
    assert fixture["artifact_sha256"] == _V16_ARTIFACT_SHA256
    assert fixture["FULL_STRICT_V16_CONTRACT_HASH"] == _V16_CONTRACT_SHA256


def test_v17_artifact_and_hermetic_materializer_are_required() -> None:
    artifact, materializer = _require_v17_assets()

    assert artifact["metadata"]["artifact_schema"] == ("full-strict-v17-reviewed-preimage-v1")
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v17"
    assert callable(materializer.build_v17)
    assert callable(materializer.project_v17_to_v16)


def test_v17_materializer_rebuilds_the_exact_candidate_and_projects_byte_exact_v16() -> None:
    v17, materializer = _require_v17_assets()
    v16_raw = _V16_ARTIFACT.read_bytes()
    v16 = _load_json(_V16_ARTIFACT)

    rebuilt = materializer.build_v17(v16)
    assert materializer._render(rebuilt) == _candidate_bytes(materializer, v17)

    projected = materializer.project_v17_to_v16(v17)
    assert projected == v16
    assert materializer._render(projected) == v16_raw
    assert projected["metadata"]["append_frozen"] is True


def test_v17_exact_diff_is_bounded_to_the_reviewed_source_authority_delta() -> None:
    v17, _materializer = _require_v17_assets()
    v16 = _load_json(_V16_ARTIFACT)
    changed = _changed_paths(v16, v17)

    assert changed
    assert {path[0] for path in changed} == _ALLOWED_CHANGED_TOP_LEVEL_SECTIONS
    changed_authority_ordinals = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authority_ordinals == _ALLOWED_CHANGED_AUTHORITY_ORDINALS
    assert _authority(v17, 17) != _authority(v16, 17)
    assert _authority(v17, 22) != _authority(v16, 22)

    source_sql = "\n".join(
        (
            *_decoded_statements(_authority(v17, 17)),
            *_decoded_statements(_authority(v17, 22)),
        )
    )
    assert "BACKFILL" in source_sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON public.data_room_package_files" in source_sql


def test_v17_preserves_the_complete_semantic_invariance_wall_byte_for_byte() -> None:
    v17, _materializer = _require_v17_assets()
    v16 = _load_json(_V16_ARTIFACT)

    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v17[section] == v16[section], section

    assert len(v17["semantic_step_registry"]["rows"]) == 28
    assert len(v17["edge_rule_registry"]["rows"]) == 48
    assert len(v17["usage_route_registry"]["rows"]) == 1_756
    assert len(v17["usage_extractor_registry"]["rows"]) == 19
    assert len(v17["provider_registry"]["rows"]) == 18
    assert len(v17["rights_index_policy_registry"]["rows"]) == 18
    assert len(v17["current_ddl_registry"]["rows"]) == 34
    assert len(v17["future_native_authority_registry"]["rows"]) == 18
    assert len(v17["future_reference_authority_registry"]["rows"]) == 15
    assert len(v17["future_child_authority_registry"]["rows"]) == 5
    assert v17["activation_assertions"] == {
        **v17["activation_assertions"],
        "semantic_step_count": 28,
        "edge_rule_count": 48,
        "usage_route_count": 1_756,
        "usage_extractor_count": 19,
        "current_ddl_table_count": 34,
        "future_native_table_count": 18,
        "future_reference_table_count": 15,
        "future_child_table_count": 5,
        "baseline_physical_table_count": 67,
        "materialized_physical_table_count": 72,
        "erasure_current_relation_count": 48,
        "erasure_future_relation_count": 38,
        "erasure_complete_relation_count": 86,
    }
