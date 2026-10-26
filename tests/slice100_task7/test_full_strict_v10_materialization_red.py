"""RED-first admission contracts for the full-strict-v10 successor bytes."""

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
from tests.slice100_task7._void_contract_hash_evidence import (
    VOID_V9_ARTIFACT_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V9_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v9_reviewed_preimage_v1.json"
_V10_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v10_reviewed_preimage_v1.json"
_V10_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v10_preimage.py"
_V10_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v10_contract_fixture.py"
_V10_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v10_contract_hash_v1.json"
)

_V9_SHA512 = (
    "5b51205be91b013c6804f4f186a5f5413764a33c243457ff293617680772d6fa"
    "3a02e2ca1a455171ab9f2e8d08ebbc7ea0c08dd354eb93ed03a76d7de0e25878"
)
_OLD_MULTI_ARRAY = (
    "FROM pg_catalog.unnest("
    "f.accepted_source_span_ids,f.accepted_locator_sha256s"
    ") zipped(span_id,locator_sha256)"
)
_ROWS_FROM_REPLACEMENT = (
    "FROM ROWS FROM ("
    "pg_catalog.unnest(f.accepted_source_span_ids), "
    "pg_catalog.unnest(f.accepted_locator_sha256s)"
    ") AS zipped(span_id,locator_sha256)"
)

_UNCHANGED_SECTIONS = (
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

_EXPECTED_CHANGED_PATHS = {
    ("metadata", "artifact_schema"),
    ("metadata", "pipeline_contract_version"),
    ("metadata", "reviewed_design_sha256"),
    ("metadata", "reviewed_design_utf8_hex"),
    ("digest_recipe_registry", "rows", 5, 1),
    ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
    ("sql_authority_registry", "rows", 18, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 18, "expected_sha256"),
    ("sql_authority_registry", "rows", 19, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 19, "expected_sha256"),
    ("sql_authority_registry", "rows", 26, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 26, "expected_sha256"),
    ("sql_authority_registry", "expected_sha256"),
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required v10 module is absent: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        if tuple(old) != tuple(new):
            return {path}
        changed: set[tuple[str | int, ...]] = set()
        for key in old:
            changed.update(_changed_paths(old[key], new[key], (*path, key)))
        return changed
    if isinstance(old, list):
        if len(old) != len(new):
            return {path}
        changed = set()
        for ordinal, (old_child, new_child) in enumerate(zip(old, new, strict=True)):
            changed.update(
                _changed_paths(
                    old_child,
                    new_child,
                    (*path, ordinal),
                )
            )
        return changed
    return set() if old == new else {path}


def _decode_body(artifact: dict[str, Any], row: int, statement: int) -> str:
    encoded = artifact["sql_authority_registry"]["rows"][row]["body_sql_utf8_hex_tuple"][statement]
    return bytes.fromhex(encoded).decode("utf-8")


def test_v10_successor_assets_exist_unfrozen_without_fixture() -> None:
    assert _V10_MATERIALIZER.is_file()
    assert _V10_GENERATOR.is_file()
    assert _V10_ARTIFACT.is_file()
    assert not _V10_FIXTURE.exists()

    artifact = _load_json(_V10_ARTIFACT)
    assert artifact["metadata"]["artifact_schema"] == ("full-strict-v10-reviewed-preimage-v1")
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v10"
    assert artifact["metadata"]["append_frozen"] is False


def test_v10_exact_diff_is_only_rows_from_plus_dependent_version_hashes() -> None:
    v9 = _load_json(_V9_ARTIFACT)
    v10 = _load_json(_V10_ARTIFACT)

    assert _changed_paths(v9, v10) == _EXPECTED_CHANGED_PATHS
    for section in _UNCHANGED_SECTIONS:
        assert v10[section] == v9[section], section

    old_output = _decode_body(v9, 18, 2)
    new_output = _decode_body(v10, 18, 2)
    assert old_output.count(_OLD_MULTI_ARRAY) == 1
    assert _ROWS_FROM_REPLACEMENT not in old_output
    assert new_output == old_output.replace(
        _OLD_MULTI_ARRAY,
        _ROWS_FROM_REPLACEMENT,
        1,
    )
    assert _OLD_MULTI_ARRAY not in new_output
    assert new_output.count(_ROWS_FROM_REPLACEMENT) == 1

    for row, statement in ((19, 2), (26, 0)):
        old_sql = _decode_body(v9, row, statement)
        new_sql = _decode_body(v10, row, statement)
        assert old_sql.count("full-strict-v9") == 1
        assert new_sql == old_sql.replace("full-strict-v9", "full-strict-v10")


def test_v10_reviewed_design_and_recipe_change_only_the_version() -> None:
    v9 = _load_json(_V9_ARTIFACT)
    v10 = _load_json(_V10_ARTIFACT)

    old_design = json.loads(bytes.fromhex(v9["metadata"]["reviewed_design_utf8_hex"]))
    new_design = json.loads(bytes.fromhex(v10["metadata"]["reviewed_design_utf8_hex"]))
    expected_design = copy.deepcopy(old_design)
    expected_design[0] = "full-strict-v10-reviewed-design-literal-v1"
    expected_design[1] = ["pipeline_contract_version", "full-strict-v10"]
    assert new_design == expected_design

    assert v10["digest_recipe_registry"]["rows"][5][1] == (
        v9["digest_recipe_registry"]["rows"][5][1]
        .replace("pipeline-contract-v9", "pipeline-contract-v10")
        .replace("full-strict-v9", "full-strict-v10")
    )


def test_v10_materializer_authenticates_v9_by_sha512_only() -> None:
    source = _V10_MATERIALIZER.read_text(encoding="utf-8")
    assert VOID_V9_ARTIFACT_SHA256 not in source

    materializer = _load_module(
        _V10_MATERIALIZER,
        "_task7_v10_materializer_auth_red",
    )
    assert materializer._V9_SOURCE_SHA512 == _V9_SHA512
    v9 = materializer._load_v9(_V9_ARTIFACT)
    assert materializer._render(materializer.build_v10(v9)) == (_V10_ARTIFACT.read_bytes())


def test_v10_materializer_is_deterministic_exclusive_and_fail_closed(
    tmp_path: Path,
) -> None:
    materializer = _load_module(
        _V10_MATERIALIZER,
        "_task7_v10_materializer_determinism_red",
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert materializer.main(["--base-artifact", str(_V9_ARTIFACT), "--output", str(first)]) == 0
    assert materializer.main(["--base-artifact", str(_V9_ARTIFACT), "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes() == _V10_ARTIFACT.read_bytes()
    assert materializer.main(["--base-artifact", str(_V9_ARTIFACT), "--output", str(first)]) == 2

    corrupted = tmp_path / "void-v9-corrupted.json"
    raw = bytearray(_V9_ARTIFACT.read_bytes())
    raw[-2] ^= 1
    corrupted.write_bytes(bytes(raw))
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V10_BASE_ARTIFACT_HASH_MISMATCH",
    ):
        materializer._load_v9(corrupted)


def test_v10_generator_validates_all_invariants_without_publishing() -> None:
    generator_source = _V10_GENERATOR.read_text(encoding="utf-8")
    assert "os.environ" not in generator_source
    assert "from idis" not in generator_source
    assert "import idis" not in generator_source
    assert not _V10_FIXTURE.exists()

    generator = _load_module(
        _V10_GENERATOR,
        "_task7_v10_generator_invariants_red",
    )
    raw = _V10_ARTIFACT.read_bytes()
    artifact = generator._decode_artifact(raw)
    generator._validate_shape(raw, artifact)

    assert generator.invariant_vector(artifact) == {
        "semantic_step_count": 28,
        "edge_rule_count": 48,
        "usage_route_count": 1756,
        "usage_extractor_count": 19,
        "provider_count": 18,
        "current_ddl_count": 34,
        "materialized_physical_table_count": 72,
        "erasure_phase_count": 66,
    }
    fixture = generator.derive_contract_bytes(raw)
    assert fixture["pipeline_contract_version"] == "full-strict-v10"
    assert fixture["fixture_schema"] == ("full-strict-v10-contract-hash-fixture-v1")
    assert fixture["artifact_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(fixture["FULL_STRICT_V10_CONTRACT_HASH"]) == 64
    assert callable(generator.admit_candidate_atomically)
    assert not _V10_FIXTURE.exists()


def test_v10_prospective_freeze_changes_only_append_frozen_in_memory() -> None:
    generator = _load_module(
        _V10_GENERATOR,
        "_task7_v10_generator_freeze_projection_red",
    )
    candidate_raw = _V10_ARTIFACT.read_bytes()
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    candidate = generator._decode_artifact(candidate_raw)
    frozen = generator._decode_artifact(frozen_raw)

    candidate["metadata"]["append_frozen"] = True
    assert frozen == candidate
    assert _V10_ARTIFACT.read_bytes() == candidate_raw
    assert not _V10_FIXTURE.exists()
