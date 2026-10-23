"""RED-first admission contracts for the bounded full-strict-v18 correction."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v17_contract_hash_v1.json"
)
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v18_preimage.py"
_V18_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v18_contract_fixture.py"
_V18_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)

_V17_ARTIFACT_BYTES = 6_315_305
_V17_ARTIFACT_SHA256 = "d6218f7a48f2170099b461ee152410852de9e0f29cfe78fb71627b968c1465e1"
_V17_ARTIFACT_SHA512 = (
    "08663ffe16e6e6515e0bebff161bb30a1dfc496573fda8cd4cfcb556b2acfeae"
    "5c611f7141b83e0264a5871844f11d85724dc289a7a5d8adab2a4bcf5b4a6d8a"
)
_V18_CANDIDATE_SHA256 = "2fd34ce570e582e07a2fc88e1be016464f1972c0907b1900ca085ed807532038"
_V18_FROZEN_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V18_FIXTURE_SHA256 = "9efef72429745ac8ccce91f4aea541a1bb958638e83bf08c9eccbc5eabd03a0c"
_FRESH_0037_STRICT_PREFLIGHT_BYTES = 4_287
_FRESH_0037_STRICT_PREFLIGHT_SHA256 = (
    "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"
)
_BAD_V17_STRICT_PREFLIGHT_BYTES = 4_261
_BAD_V17_STRICT_PREFLIGHT_SHA256 = (
    "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484"
)
_CORRECTED_RETAINED_ORDINAL = 0
_CORRECTED_ORDERED_SQL_ORDINAL = 172
_VERSION_AUTHORITY_ORDINALS = {19, 26}
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
_LANGUAGE_CLAUSE = re.compile(r"(?i)\bLANGUAGE\s+[a-z][a-z0-9_]*\b")


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


def _require_v18_assets() -> tuple[dict[str, Any], ModuleType, ModuleType]:
    missing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_V18_ARTIFACT, _V18_MATERIALIZER, _V18_GENERATOR)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V18_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)
    frozen_raw = _V18_ARTIFACT.read_bytes()
    frozen = _load_json(_V18_ARTIFACT)
    assert hashlib.sha256(frozen_raw).hexdigest() == _V18_FROZEN_SHA256
    assert frozen["metadata"]["append_frozen"] is True
    candidate = copy.deepcopy(frozen)
    candidate["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(candidate, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(candidate_raw).hexdigest() == _V18_CANDIDATE_SHA256
    return (
        candidate,
        _load_module(_V18_MATERIALIZER, "_task7_v18_materialization_red"),
        _load_module(_V18_GENERATOR, "_task7_v18_generator_red"),
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


def _expected_v18_changed_paths() -> set[tuple[str | int, ...]]:
    expected: set[tuple[str | int, ...]] = {
        ("metadata", "append_frozen"),
        ("metadata", "artifact_schema"),
        ("metadata", "pipeline_contract_version"),
        ("metadata", "reviewed_design_sha256"),
        ("metadata", "reviewed_design_utf8_hex"),
        ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
        ("digest_recipe_registry", "rows", 5, 1),
        ("sql_authority_registry", "expected_sha256"),
        ("sql_authority_registry", "rows", 19, "body_sql_utf8_hex_tuple", 2),
        ("sql_authority_registry", "rows", 19, "expected_sha256"),
        ("sql_authority_registry", "rows", 26, "body_sql_utf8_hex_tuple", 0),
        ("sql_authority_registry", "rows", 26, "expected_sha256"),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "expected_sha256",
        ),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "downgrade_authority",
            "expected_sha256",
        ),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "downgrade_authority",
            "retained_function_restore_sql_utf8_hex_tuple",
            0,
        ),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "downgrade_authority",
            "ordered_sql_utf8_hex_tuple",
            172,
        ),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "function_rows",
            25,
            "expected_sha256",
        ),
        (
            "sql_authority_registry",
            "guard_function_acl_registry",
            "function_rows",
            34,
            "expected_sha256",
        ),
    }
    for read_ordinal in range(8):
        expected.add(
            (
                "sql_authority_registry",
                "guard_function_acl_registry",
                "function_rows",
                25,
                "direct_read_column_tuple",
                read_ordinal,
                2,
                1,
            )
        )
        expected.add(
            (
                "sql_authority_registry",
                "guard_function_acl_registry",
                "function_rows",
                25,
                "transitive_read_column_tuple",
                read_ordinal,
                2,
                0,
                1,
            )
        )
    for read_ordinal in range(3):
        expected.add(
            (
                "sql_authority_registry",
                "guard_function_acl_registry",
                "function_rows",
                34,
                "direct_read_column_tuple",
                read_ordinal,
                2,
                1,
            )
        )
    for read_ordinal in range(1, 4):
        expected.add(
            (
                "sql_authority_registry",
                "guard_function_acl_registry",
                "function_rows",
                34,
                "transitive_read_column_tuple",
                read_ordinal,
                2,
                0,
                1,
            )
        )
    assert len(expected) == 40
    return expected


def _downgrade(artifact: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        artifact["sql_authority_registry"]["guard_function_acl_registry"]["downgrade_authority"],
    )


def _render_candidate(materializer: ModuleType, artifact: dict[str, Any]) -> bytes:
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    return bytes(materializer._render(candidate))


def test_frozen_v17_is_preserved_as_superseded_history() -> None:
    raw = _V17_ARTIFACT.read_bytes()
    artifact = _load_json(_V17_ARTIFACT)
    fixture = _load_json(_V17_FIXTURE)

    assert len(raw) == _V17_ARTIFACT_BYTES
    assert hashlib.sha256(raw).hexdigest() == _V17_ARTIFACT_SHA256
    assert hashlib.sha512(raw).hexdigest() == _V17_ARTIFACT_SHA512
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v17"
    assert fixture["pipeline_contract_version"] == "full-strict-v17"

    bad = bytes.fromhex(
        _downgrade(artifact)["retained_function_restore_sql_utf8_hex_tuple"][
            _CORRECTED_RETAINED_ORDINAL
        ]
    )
    assert len(bad) == _BAD_V17_STRICT_PREFLIGHT_BYTES
    assert hashlib.sha256(bad).hexdigest() == _BAD_V17_STRICT_PREFLIGHT_SHA256
    assert not _LANGUAGE_CLAUSE.search(bad.decode("utf-8"))


def test_v18_artifact_materializer_and_admission_generator_are_required() -> None:
    artifact, materializer, generator = _require_v18_assets()

    assert artifact["metadata"]["artifact_schema"] == ("full-strict-v18-reviewed-preimage-v1")
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v18"
    assert artifact["metadata"]["append_frozen"] is False
    assert callable(materializer.build_v18)
    assert callable(materializer.project_v18_to_v17)
    assert callable(materializer.validate_retained_restore_language)
    assert callable(generator.validate_candidate_artifact)


def test_v18_materializer_is_deterministic_and_projects_byte_exact_frozen_v17() -> None:
    v18, materializer, generator = _require_v18_assets()
    v17_raw = _V17_ARTIFACT.read_bytes()
    v17 = _load_json(_V17_ARTIFACT)

    rebuilt = materializer.build_v18(v17)
    assert materializer._render(rebuilt) == _render_candidate(materializer, v18)

    projected = materializer.project_v18_to_v17(v18)
    assert projected == v17
    assert materializer._render(projected) == v17_raw
    assert projected["metadata"]["append_frozen"] is True
    assert (
        generator.validate_candidate_artifact(
            v18,
            raw=materializer._render(v18),
        )
        == v18
    )


def test_v18_binds_exact_fresh_0037_catalog_definition_in_both_restore_sites() -> None:
    v18, _materializer, _generator = _require_v18_assets()
    downgrade = _downgrade(v18)
    restore_hex = downgrade["retained_function_restore_sql_utf8_hex_tuple"][
        _CORRECTED_RETAINED_ORDINAL
    ]
    ordered_hex = downgrade["ordered_sql_utf8_hex_tuple"][_CORRECTED_ORDERED_SQL_ORDINAL]
    definition = bytes.fromhex(restore_hex)

    assert ordered_hex == restore_hex
    assert len(definition) == _FRESH_0037_STRICT_PREFLIGHT_BYTES
    assert hashlib.sha256(definition).hexdigest() == _FRESH_0037_STRICT_PREFLIGHT_SHA256
    assert definition.startswith(
        b"CREATE OR REPLACE FUNCTION public.idis_prevent_strict_preflight_proof_mutation()"
    )
    assert definition.endswith(b"$function$\n")
    assert _LANGUAGE_CLAUSE.search(definition.decode("utf-8"))


def test_v18_class_guard_requires_language_in_every_retained_definition() -> None:
    v18, materializer, _generator = _require_v18_assets()
    retained_hex = _downgrade(v18)["retained_function_restore_sql_utf8_hex_tuple"]
    assert len(retained_hex) == 8
    assert all(
        _LANGUAGE_CLAUSE.search(bytes.fromhex(value).decode("utf-8")) for value in retained_hex
    )
    materializer.validate_retained_restore_language(v18)

    corrupted = copy.deepcopy(v18)
    sql = bytes.fromhex(
        _downgrade(corrupted)["retained_function_restore_sql_utf8_hex_tuple"][1]
    ).decode("utf-8")
    sql = _LANGUAGE_CLAUSE.sub("", sql, count=1)
    _downgrade(corrupted)["retained_function_restore_sql_utf8_hex_tuple"][1] = sql.encode(
        "utf-8"
    ).hex()
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V18_RETAINED_FUNCTION_LANGUAGE_REQUIRED",
    ):
        materializer.validate_retained_restore_language(corrupted)


def test_v18_exact_diff_is_bounded_and_rejects_unreviewed_changes() -> None:
    v18, materializer, _generator = _require_v18_assets()
    v17 = _load_json(_V17_ARTIFACT)
    changed = _changed_paths(v17, v18)

    assert changed
    assert changed == _expected_v18_changed_paths()
    assert {path[0] for path in changed} == {
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
    }
    changed_authority_ordinals = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authority_ordinals == _VERSION_AUTHORITY_ORDINALS
    materializer._validate_exact_diff(v17, v18)

    corrupted = copy.deepcopy(v18)
    corrupted["provider_registry"]["rows"][0][1] = "unreviewed-provider"
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V18_INVARIANT_DRIFT",
    ):
        materializer._validate_exact_diff(v17, corrupted)

    corrupted = copy.deepcopy(v18)
    _downgrade(corrupted)["ordered_sql_utf8_hex_tuple"][171] = _downgrade(corrupted)[
        "ordered_sql_utf8_hex_tuple"
    ][172]
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V18_EXACT_DIFF_INVALID",
    ):
        materializer._validate_exact_diff(v17, corrupted)


def test_v18_preserves_the_complete_semantic_invariance_wall() -> None:
    v18, _materializer, _generator = _require_v18_assets()
    v17 = _load_json(_V17_ARTIFACT)

    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v18[section] == v17[section], section

    assert len(v18["semantic_step_registry"]["rows"]) == 28
    assert len(v18["edge_rule_registry"]["rows"]) == 48
    assert len(v18["usage_route_registry"]["rows"]) == 1_756
    assert len(v18["usage_extractor_registry"]["rows"]) == 19
    assert len(v18["provider_registry"]["rows"]) == 18
    assert len(v18["rights_index_policy_registry"]["rows"]) == 18
    assert v18["activation_assertions"] == v17["activation_assertions"]


def test_v18_is_frozen_while_the_admission_generator_remains_nonpublishing() -> None:
    v18, _materializer, generator = _require_v18_assets()

    assert v18["metadata"]["append_frozen"] is False
    assert _load_json(_V18_ARTIFACT)["metadata"]["append_frozen"] is True
    assert hashlib.sha256(_V18_FIXTURE.read_bytes()).hexdigest() == _V18_FIXTURE_SHA256
    assert not hasattr(generator, "authorize_fixture_publication")
    assert not hasattr(generator, "derive_contract_bytes")
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V18_APPEND_FREEZE_NOT_AUTHORIZED",
    ):
        generator.reject_fixture_generation()
