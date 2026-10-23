"""RED-first admission for the bounded full-strict-v21 ACL successor.

Frozen v20 is the immutable negative control.  V21 changes only the four
authoritative erasure ACL copies so that every explicitly executable function
grantee also receives direct schema USAGE, plus dependent version/hash bytes.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V20_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V21_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_V21_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v21_preimage.py"
_V21_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v21_contract_fixture.py"
_V21_FROZEN_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"

_EXECUTOR_USAGE = "GRANT USAGE ON SCHEMA public TO idis_erasure_executor"
_EXECUTOR_REVOKE = "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor"
_EXECUTOR_EXECUTE = (
    "GRANT EXECUTE ON FUNCTION "
    "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) "
    "TO idis_erasure_executor"
)
_RUNNER_DROP = "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ACL_FIELDS = (
    "current_acl_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
)
_EXPECTED_COUNTS = {
    "current_acl_sql_utf8_hex_tuple": (208, 209),
    "future_acl_sql_utf8_hex_tuple": (360, 361),
    "future_activation_sql_utf8_hex_tuple": (362, 363),
    "future_restoration_sql_utf8_hex_tuple": (210, 211),
}
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
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_artifact() -> dict[str, Any]:
    frozen_raw = _V21_ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _V21_FROZEN_SHA256
    artifact = _load_json(_V21_ARTIFACT)
    assert artifact["metadata"]["append_frozen"] is True
    artifact["metadata"]["append_frozen"] = False
    return artifact


def _require_v21_assets() -> tuple[dict[str, Any], ModuleType, ModuleType]:
    missing = [
        path.name
        for path in (_V21_ARTIFACT, _V21_MATERIALIZER, _V21_GENERATOR)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V21_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)
    artifact = _candidate_artifact()
    return (
        artifact,
        _load_module(_V21_MATERIALIZER, "_task7_v21_materializer_red"),
        _load_module(_V21_GENERATOR, "_task7_v21_generator_red"),
    )


def _row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    return artifact["erasure_execution_registry"]["rows"][0]


def _decode(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    values = row[field]
    assert isinstance(values, list)
    return tuple(bytes.fromhex(str(value)).decode("utf-8") for value in values)


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        result: set[tuple[str | int, ...]] = set()
        for key in old.keys() | new.keys():
            if key not in old or key not in new:
                result.add((*path, key))
            else:
                result.update(_changed_paths(old[key], new[key], (*path, key)))
        return result
    if isinstance(old, list):
        result = set()
        for index in range(max(len(old), len(new))):
            if index >= len(old) or index >= len(new):
                result.add((*path, index))
            else:
                result.update(_changed_paths(old[index], new[index], (*path, index)))
        return result
    return set() if old == new else {path}


def test_v21_assets_are_required_and_frozen_artifact_reconstructs_candidate() -> None:
    artifact, materializer, generator = _require_v21_assets()

    assert artifact["metadata"]["artifact_schema"] == "full-strict-v21-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v21"
    assert artifact["metadata"]["append_frozen"] is False
    assert callable(materializer.build_v21)
    assert callable(materializer.project_v21_to_v20)
    assert callable(materializer.validate_executable_call_acl_closure)
    assert callable(generator.validate_candidate_artifact)


def test_real_frozen_v20_is_the_exact_negative_control() -> None:
    _artifact, materializer, _generator = _require_v21_assets()
    v20 = _load_json(_V20_ARTIFACT)

    assert v20["metadata"]["append_frozen"] is True
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
    ):
        materializer.validate_executable_call_acl_closure(v20)


def test_v21_build_is_deterministic_and_inverse_is_byte_exact_v20() -> None:
    v21, materializer, generator = _require_v21_assets()
    v20 = _load_json(_V20_ARTIFACT)

    rebuilt = materializer.build_v21(v20)
    assert materializer._render(rebuilt) == materializer._render(v21)
    assert materializer._render(materializer.build_v21(v20)) == materializer._render(rebuilt)
    assert materializer._render(materializer.project_v21_to_v20(v21)) == _V20_ARTIFACT.read_bytes()
    assert (
        generator.validate_candidate_artifact(
            v21,
            raw=materializer._render(v21),
        )
        == v21
    )


def test_v20_to_v21_delta_adds_one_executor_schema_grant_to_each_acl_copy() -> None:
    v21, materializer, _generator = _require_v21_assets()
    v20 = _load_json(_V20_ARTIFACT)
    old_row = _row(v20)
    new_row = _row(v21)

    for field in _ACL_FIELDS:
        old = _decode(old_row, field)
        new = _decode(new_row, field)
        old_count, new_count = _EXPECTED_COUNTS[field]
        assert (len(old), len(new)) == (old_count, new_count)
        assert new.count(_EXECUTOR_USAGE) == 1
        assert old.count(_EXECUTOR_USAGE) == 0
        assert tuple(item for item in new if item != _EXECUTOR_USAGE) == old
        assert (
            new.index(_EXECUTOR_REVOKE) < new.index(_EXECUTOR_USAGE) < new.index(_EXECUTOR_EXECUTE)
        )

    assert _decode(new_row, "future_activation_sql_utf8_hex_tuple")[0] == _RUNNER_DROP
    assert _decode(new_row, "future_restoration_sql_utf8_hex_tuple")[0] == _RUNNER_DROP
    assert _decode(new_row, "future_activation_sql_utf8_hex_tuple")[1:] == (
        *_decode(new_row, "future_body_sql_utf8_hex_tuple"),
        *_decode(new_row, "future_acl_sql_utf8_hex_tuple"),
    )
    assert _decode(new_row, "future_restoration_sql_utf8_hex_tuple")[1:] == (
        *_decode(new_row, "current_body_sql_utf8_hex_tuple"),
        *_decode(new_row, "current_acl_sql_utf8_hex_tuple"),
    )
    materializer.validate_executable_call_acl_closure(v21)


def test_v21_exact_diff_and_complete_invariance_wall() -> None:
    v21, materializer, _generator = _require_v21_assets()
    v20 = _load_json(_V20_ARTIFACT)
    changed = _changed_paths(v20, v21)

    assert {path[0] for path in changed} == {
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
        "erasure_execution_registry",
    }
    changed_authorities = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authorities == {19, 26}
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v21[section] == v20[section], section
    assert len(v21["semantic_step_registry"]["rows"]) == 28
    assert len(v21["edge_rule_registry"]["rows"]) == 48
    assert len(v21["usage_route_registry"]["rows"]) == 1_756
    assert len(v21["usage_extractor_registry"]["rows"]) == 19
    assert len(v21["provider_registry"]["rows"]) == 18
    materializer._validate_exact_diff(v20, v21)


@pytest.mark.parametrize("field", _ACL_FIELDS)
@pytest.mark.parametrize(
    "mutation",
    ("drop-usage", "drop-revoke", "drop-execute", "duplicate-usage", "reorder-usage"),
)
def test_v21_closure_rejects_drift_in_every_authoritative_copy(
    field: str,
    mutation: str,
) -> None:
    v21, materializer, _generator = _require_v21_assets()
    corrupted = copy.deepcopy(v21)
    row = corrupted["erasure_execution_registry"]["rows"][0]
    statements = list(_decode(row, field))

    if mutation == "drop-usage":
        statements.remove(_EXECUTOR_USAGE)
    elif mutation == "drop-revoke":
        statements.remove(_EXECUTOR_REVOKE)
    elif mutation == "drop-execute":
        statements.remove(_EXECUTOR_EXECUTE)
    elif mutation == "duplicate-usage":
        statements.insert(statements.index(_EXECUTOR_USAGE), _EXECUTOR_USAGE)
    else:
        statements.remove(_EXECUTOR_USAGE)
        statements.insert(statements.index(_EXECUTOR_REVOKE), _EXECUTOR_USAGE)
    row[field] = [statement.encode("utf-8").hex() for statement in statements]

    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
    ):
        materializer.validate_executable_call_acl_closure(corrupted)


def test_v21_candidate_hash_is_stable_for_checkpoint_reporting() -> None:
    v21, materializer, _generator = _require_v21_assets()
    rendered = materializer._render(v21)

    assert hashlib.sha256(rendered).hexdigest() == materializer.V21_CANDIDATE_SHA256
    assert hashlib.sha256(_V21_ARTIFACT.read_bytes()).hexdigest() == _V21_FROZEN_SHA256
