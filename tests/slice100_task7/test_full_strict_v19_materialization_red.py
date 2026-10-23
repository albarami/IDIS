"""RED-first admission contract for the bounded full-strict-v19 successor.

V18 is immutable historical evidence. V19 may change only the reviewed
erasure-composition authority, its closed audit-lineage/UUID declarations,
version-bearing recipes, and dependent hashes. This module intentionally
loads v19 assets lazily so the pre-implementation state is a clean RED
(`TASK7_V19_MATERIALIZATION_NOT_IMPLEMENTED`), not an import error.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)
_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V19_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v19_preimage.py"
_V19_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v19_contract_fixture.py"
_SUCCESSOR_HISTORY = _REPO_ROOT / ".local_reports" / "slice100" / "task7_v17_contract_history.jsonl"

_V18_ARTIFACT_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V18_FIXTURE_SHA256 = "9efef72429745ac8ccce91f4aea541a1bb958638e83bf08c9eccbc5eabd03a0c"
_V18_CONTRACT_SHA256 = "ad8e0a8fd012af34f34d8fd4b66643d2d31a8a4f436dd3a8021af9a188da378c"

_APPROVED_CHANGED_SECTIONS = {
    "metadata",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    # V19 cannot remain runtime-selectable under V18's literal version predicates.
    "sql_authority_registry",
    "erasure_execution_registry",
}
_SEMANTIC_INVARIANCE_SECTIONS = (
    "canonicalization_contract",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
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


def _render_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _candidate_view(artifact: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    return candidate


def _require_v19_assets() -> tuple[dict[str, Any], ModuleType, ModuleType]:
    missing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_V19_ARTIFACT, _V19_MATERIALIZER, _V19_GENERATOR)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V19_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)

    artifact = _load_json(_V19_ARTIFACT)
    assert artifact["metadata"]["artifact_schema"] == "full-strict-v19-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v19"
    assert isinstance(artifact["metadata"]["append_frozen"], bool)
    return (
        _candidate_view(artifact),
        _load_module(_V19_MATERIALIZER, "_task7_v19_materializer_red"),
        _load_module(_V19_GENERATOR, "_task7_v19_generator_red"),
    )


def _changed_top_level_sections(old: dict[str, Any], new: dict[str, Any]) -> set[str]:
    assert tuple(old) == tuple(new)
    return {key for key in old if old[key] != new[key]}


def _history_rows() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in _SUCCESSOR_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all(isinstance(row, dict) for row in rows)
    return rows


def test_v18_is_byte_immutable_and_superseded_not_void() -> None:
    artifact = _load_json(_V18_ARTIFACT)
    fixture = _load_json(_V18_FIXTURE)

    assert hashlib.sha256(_V18_ARTIFACT.read_bytes()).hexdigest() == _V18_ARTIFACT_SHA256
    assert hashlib.sha256(_V18_FIXTURE.read_bytes()).hexdigest() == _V18_FIXTURE_SHA256
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v18"
    assert fixture["FULL_STRICT_V18_CONTRACT_HASH"] == _V18_CONTRACT_SHA256

    rows = [
        row
        for row in _history_rows()
        if row.get("event") == "V18_FROZEN_CONTRACT_SUPERSEDED_BY_V19"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact_sha256"] == _V18_ARTIFACT_SHA256
    assert row["fixture_sha256"] == _V18_FIXTURE_SHA256
    assert row["contract_sha256"] == _V18_CONTRACT_SHA256
    assert row["append_frozen"] is True
    assert row["runtime_state"] == "INACTIVE"
    assert row["runtime_selectable"] is False
    assert row["superseded_by"] == "full-strict-v19"
    assert row["disposition"].endswith("SUPERSEDED_NEVER_SELECTABLE_NOT_VOID")


def test_v19_artifact_materializer_and_hermetic_generator_are_required() -> None:
    artifact, materializer, generator = _require_v19_assets()

    assert artifact["metadata"]["append_frozen"] is False
    assert callable(materializer.build_v19)
    assert callable(materializer.project_v19_to_v18)
    assert callable(materializer._validate_exact_diff)
    assert callable(generator.validate_candidate_artifact)
    assert callable(generator.validate_erasure_execution_registry)


def test_v19_build_is_deterministic_and_inverse_is_byte_exact_v18() -> None:
    v19, materializer, generator = _require_v19_assets()
    v18 = _load_json(_V18_ARTIFACT)
    v18_raw = _V18_ARTIFACT.read_bytes()

    rebuilt = materializer.build_v19(v18)
    assert materializer._render(rebuilt) == materializer._render(v19)
    assert materializer._render(materializer.build_v19(v18)) == materializer._render(rebuilt)

    projected = materializer.project_v19_to_v18(v19)
    assert projected == v18
    assert materializer._render(projected) == v18_raw
    assert hashlib.sha256(materializer._render(projected)).hexdigest() == _V18_ARTIFACT_SHA256
    assert (
        generator.validate_candidate_artifact(
            v19,
            raw=materializer._render(v19),
        )
        == v19
    )


def test_v18_to_v19_top_level_delta_is_exactly_the_reviewed_boundary() -> None:
    v19, materializer, _generator = _require_v19_assets()
    v18 = _load_json(_V18_ARTIFACT)
    candidate_v18 = _candidate_view(v18)

    assert _changed_top_level_sections(candidate_v18, v19) == _APPROVED_CHANGED_SECTIONS
    changed_authorities = {
        path[2]
        for path in materializer._changed_paths(v18, v19)
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authorities == {19, 26}
    materializer._validate_exact_diff(v18, v19)

    corrupted = copy.deepcopy(v19)
    corrupted["provider_registry"]["rows"][0][1] = "unreviewed-provider"
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V19_(?:INVARIANT_DRIFT|EXACT_DIFF_INVALID)",
    ):
        materializer._validate_exact_diff(v18, corrupted)


def test_v19_preserves_the_complete_semantic_invariance_wall() -> None:
    v19, _materializer, _generator = _require_v19_assets()
    v18 = _load_json(_V18_ARTIFACT)

    for section in _SEMANTIC_INVARIANCE_SECTIONS:
        assert v19[section] == v18[section], section

    assert len(v19["semantic_step_registry"]["rows"]) == 28
    assert len(v19["edge_rule_registry"]["rows"]) == 48
    assert len(v19["usage_route_registry"]["rows"]) == 1_756
    assert len(v19["usage_extractor_registry"]["rows"]) == 19
    assert len(v19["provider_registry"]["rows"]) == 18
    assert len(v19["rights_index_policy_registry"]["rows"]) == 18
    assert v19["erasure_materialized"]["physical_table_count"] == 72
    assert v19["activation_assertions"] == v18["activation_assertions"]


def test_v19_tuple_and_uuid_additions_are_append_only_over_v18() -> None:
    v19, _materializer, _generator = _require_v19_assets()
    v18 = _load_json(_V18_ARTIFACT)

    v18_schemas = v18["tuple_schema_registry"]["rows"]
    v19_schemas = v19["tuple_schema_registry"]["rows"]
    assert [row for row in v19_schemas if row in v18_schemas] == v18_schemas
    assert {row[0] for row in v19_schemas} - {row[0] for row in v18_schemas} == {
        "ErasureExecutionSpecV2",
        "ErasureAuditLineageSpecV1",
    }

    v18_namespaces = v18["uuid_namespace_registry"]["rows"]
    v19_namespaces = v19["uuid_namespace_registry"]["rows"]
    assert v19_namespaces[: len(v18_namespaces)] == v18_namespaces
    assert len(v19_namespaces) == len(v18_namespaces) + 2
    assert {row[0] for row in v19_namespaces[len(v18_namespaces) :]} == {
        "erasure_attempt_id",
        "erasure_audit_event_id",
    }


def test_v19_hash_derivation_rejects_corruption_in_each_new_authority_section() -> None:
    v19, materializer, generator = _require_v19_assets()

    for section in (
        "tuple_schema_registry",
        "uuid_namespace_registry",
        "erasure_execution_registry",
    ):
        corrupted = copy.deepcopy(v19)
        raw = bytearray(_render_json(corrupted))
        marker = f'"{section}"'.encode()
        offset = raw.index(marker) + len(marker)
        raw[offset] ^= 1
        with pytest.raises(
            (generator.ContractArtifactError, json.JSONDecodeError, UnicodeDecodeError),
            match="TASK7_V19|Expecting|codec",
        ):
            parsed = json.loads(bytes(raw))
            assert isinstance(parsed, dict)
            generator.validate_candidate_artifact(parsed, raw=bytes(raw))

    # The materializer's inverse remains the independent exact-diff authority.
    corrupted = copy.deepcopy(v19)
    corrupted["erasure_execution_registry"]["rows"][0]["activation_state"] = "CORRUPTED"
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V19_(?:INVARIANT_DRIFT|EXACT_DIFF_INVALID)",
    ):
        materializer.project_v19_to_v18(corrupted)
