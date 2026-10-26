"""RED-first admission contracts for the bounded full-strict-v11 successor."""

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
    VOID_CONTRACT_HASHES,
    VOID_V10_ARTIFACT_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V10_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v10_reviewed_preimage_v1.json"
_V11_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v11_reviewed_preimage_v1.json"
_V11_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v11_preimage.py"
_V11_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v11_contract_fixture.py"
_V11_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v11_contract_hash_v1.json"
)

_V10_SHA512 = (
    "d466d70824af9befabb46ecc78f1bce0c91120c47de7b5dfef8f801fb84a49f61"
    "93e7b5632370db835222a572d597b33bf3606f1dc08cc72287adfa68d414edf"
)
_CHANGED_AUTHORITY_COORDINATES = (
    (19, 2, "current-receipt-equality-v1"),
    (21, 0, "current-append-only-lease-v1"),
    (22, 0, "current-source-bound-v1"),
    (27, 0, "current-task6-immutable-replacement-v1"),
)
_EXPECTED_CHANGED_PATHS = {
    ("metadata", "artifact_schema"),
    ("metadata", "pipeline_contract_version"),
    ("metadata", "reviewed_design_sha256"),
    ("metadata", "reviewed_design_utf8_hex"),
    ("digest_recipe_registry", "rows", 5, 1),
    ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
    ("sql_authority_registry", "rows", 19, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 19, "expected_sha256"),
    ("sql_authority_registry", "rows", 21, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 21, "expected_sha256"),
    ("sql_authority_registry", "rows", 22, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 22, "expected_sha256"),
    ("sql_authority_registry", "rows", 26, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 26, "expected_sha256"),
    ("sql_authority_registry", "rows", 27, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 27, "expected_sha256"),
    ("sql_authority_registry", "expected_sha256"),
    ("erasure_execution_registry", "rows", 0, "legacy_guard_replacement_tuple"),
    (
        "erasure_execution_registry",
        "rows",
        0,
        "legacy_guard_upgrade_sql_utf8_hex_tuple",
    ),
    (
        "erasure_execution_registry",
        "rows",
        0,
        "legacy_guard_downgrade_sql_utf8_hex_tuple",
    ),
    ("erasure_execution_registry", "rows", 0, "expected_sha256"),
    ("erasure_execution_registry", "expected_sha256"),
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
    assert path.is_file(), f"required v11 module is absent: {path}"
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
            changed.update(_changed_paths(old_child, new_child, (*path, ordinal)))
        return changed
    return set() if old == new else {path}


def _decode_body(artifact: dict[str, Any], row: int, statement: int) -> str:
    encoded = artifact["sql_authority_registry"]["rows"][row]["body_sql_utf8_hex_tuple"][statement]
    return bytes.fromhex(encoded).decode("utf-8")


def _decode_sql_tuple(row: dict[str, Any], field: str) -> tuple[str, ...]:
    return tuple(bytes.fromhex(value).decode("utf-8") for value in row[field])


def _rehash_authority(artifact: dict[str, Any], row_ordinal: int) -> None:
    materializer = _load_module(
        _V11_MATERIALIZER,
        f"_task7_v11_test_rehash_{row_ordinal}",
    )
    row = artifact["sql_authority_registry"]["rows"][row_ordinal]
    materializer._rehash_authority_row(row)
    artifact["sql_authority_registry"]["expected_sha256"] = materializer._h(
        [materializer._SQL_REGISTRY_HASH_DOMAIN, artifact["sql_authority_registry"]["rows"]]
    )


def test_v11_assets_exist_unfrozen_without_fixture() -> None:
    assert _V11_MATERIALIZER.is_file()
    assert _V11_GENERATOR.is_file()
    assert _V11_ARTIFACT.is_file()
    assert not _V11_FIXTURE.exists()

    artifact = _load_json(_V11_ARTIFACT)
    assert artifact["metadata"] == {
        **_load_json(_V10_ARTIFACT)["metadata"],
        "artifact_schema": "full-strict-v11-reviewed-preimage-v1",
        "append_frozen": False,
        "pipeline_contract_version": "full-strict-v11",
        "reviewed_design_sha256": artifact["metadata"]["reviewed_design_sha256"],
        "reviewed_design_utf8_hex": artifact["metadata"]["reviewed_design_utf8_hex"],
    }


def test_v11_exact_diff_is_bounded_to_reviewed_authorities_and_lease_restoration() -> None:
    v10 = _load_json(_V10_ARTIFACT)
    v11 = _load_json(_V11_ARTIFACT)

    assert _changed_paths(v10, v11) == _EXPECTED_CHANGED_PATHS
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v11[section] == v10[section], section

    for row_ordinal, statement_ordinal, authority_id in _CHANGED_AUTHORITY_COORDINATES:
        assert v11["sql_authority_registry"]["rows"][row_ordinal]["authority_id"] == authority_id
        assert _decode_body(v11, row_ordinal, statement_ordinal) != _decode_body(
            v10,
            row_ordinal,
            statement_ordinal,
        )

    for row_ordinal in set(range(28)) - {19, 21, 22, 26, 27}:
        assert (
            v11["sql_authority_registry"]["rows"][row_ordinal]
            == v10["sql_authority_registry"]["rows"][row_ordinal]
        )


def test_v11_exact_diff_array_and_version_replacements_are_single_purpose() -> None:
    v10 = _load_json(_V10_ARTIFACT)
    v11 = _load_json(_V11_ARTIFACT)
    old_execution = v10["erasure_execution_registry"]["rows"][0]
    new_execution = v11["erasure_execution_registry"]["rows"][0]
    fields = (
        "legacy_guard_replacement_tuple",
        "legacy_guard_upgrade_sql_utf8_hex_tuple",
        "legacy_guard_downgrade_sql_utf8_hex_tuple",
    )
    for field in fields:
        old = old_execution[field]
        new = new_execution[field]
        assert len(new) == len(old) + 1
        additions = [item for item in new if item not in old]
        assert len(additions) == 1
        assert [item for item in new if item != additions[0]] == old

    old_task5 = _decode_body(v10, 26, 0)
    new_task5 = _decode_body(v11, 26, 0)
    assert new_task5.replace("full-strict-v11", "full-strict-v10", 1) == old_task5
    assert new_task5.count("full-strict-v11") == 1

    old_design = json.loads(bytes.fromhex(v10["metadata"]["reviewed_design_utf8_hex"]))
    new_design = json.loads(bytes.fromhex(v11["metadata"]["reviewed_design_utf8_hex"]))
    projected_design = copy.deepcopy(new_design)
    projected_design[0] = projected_design[0].replace("full-strict-v11", "full-strict-v10")
    projected_design[1][1] = "full-strict-v10"
    assert projected_design == old_design

    old_recipes = v10["digest_recipe_registry"]["rows"]
    new_recipes = v11["digest_recipe_registry"]["rows"]
    projected_recipes = copy.deepcopy(new_recipes)
    projected_recipes[5][1] = (
        projected_recipes[5][1]
        .replace("pipeline-contract-v11", "pipeline-contract-v10")
        .replace("full-strict-v11", "full-strict-v10")
    )
    assert projected_recipes == old_recipes


def test_v11_four_authority_bodies_are_target_safe_and_schema_qualified() -> None:
    artifact = _load_json(_V11_ARTIFACT)
    receipt = _decode_body(artifact, 19, 2)
    append_only = _decode_body(artifact, 21, 0)
    source_bound = _decode_body(artifact, 22, 0)
    task6 = _decode_body(artifact, 27, 0)

    assert "NEW.deal_id" not in receipt and "OLD.deal_id" not in receipt
    assert "FROM public.runs r" in receipt
    assert "r.tenant_id=scope_tenant" in receipt
    assert "r.run_id=scope_run" in receipt
    assert "r.pipeline_contract_version='full-strict-v11'" in receipt

    admission_only_fields = (
        "materialization_origin",
        "contract_rule_id",
        "usage_kind",
        "producer_input_binding_id",
        "producer_output_object_id",
    )
    assert "idis_task7_expected_edge_admission_is_valid_v1" in append_only
    assert all(f"NEW.{field}" not in append_only for field in admission_only_fields)
    assert "FROM public.runs r" in append_only

    assert "idis_task7_package_source_transition_is_valid_v1" in source_bound
    assert "NEW.status" not in source_bound
    assert "OLD.status" not in source_bound

    output_root_fields = (
        "run_output_object_id",
        "run_output_object_class",
        "run_output_object_kind",
    )
    assert "idis_task6_output_root_attach_is_valid_v1" in task6
    assert all(f"NEW.{field}" not in task6 for field in output_root_fields)
    assert all(f"OLD.{field}" not in task6 for field in output_root_fields)
    assert "FROM public.run_steps s" in task6
    assert "FROM public.run_output_objects r" in task6


def test_v11_hash_binds_exact_task6_lease_upgrade_and_0037_restoration() -> None:
    artifact = _load_json(_V11_ARTIFACT)
    execution = artifact["erasure_execution_registry"]["rows"][0]
    replacements = execution["legacy_guard_replacement_tuple"]
    lease = [
        item
        for item in replacements
        if item[1] == "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)"
    ]
    assert len(lease) == 1
    assert lease[0][:6] == [
        "0037",
        "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
        "PRESERVE_PRE_0038_OWNER",
        ["search_path=pg_catalog, pg_temp"],
        [],
        [],
    ]

    upgrades = _decode_sql_tuple(execution, "legacy_guard_upgrade_sql_utf8_hex_tuple")
    upgrade = next(
        statement
        for statement in upgrades
        if "public.idis_task6_current_lease_is_valid(" in statement
    )
    compact_upgrade = "".join(upgrade.lower().split())
    assert "setsearch_path=pg_catalog,pg_temp" in compact_upgrade
    assert "frompublic.runsr" in compact_upgrade
    assert "fromrunsr" not in compact_upgrade
    for setting_name in (
        "idis.worker_lease_tenant_id",
        "idis.worker_lease_deal_id",
        "idis.worker_lease_run_id",
        "idis.worker_lease_owner_id",
        "idis.worker_lease_fencing_token",
    ):
        assert f"pg_catalog.current_setting('{setting_name}',true)" in compact_upgrade
    assert "pg_catalog.statement_timestamp()" in compact_upgrade

    downgrades = _decode_sql_tuple(execution, "legacy_guard_downgrade_sql_utf8_hex_tuple")
    downgrade = next(
        statement for statement in downgrades if "idis_task6_current_lease_is_valid(" in statement
    )
    assert hashlib.sha256(downgrade.encode("utf-8")).hexdigest() == lease[0][6]
    assert "SET search_path" not in downgrade
    assert "FROM runs r" in downgrade


def test_v11_materializer_authenticates_exact_v10_and_is_deterministic(
    tmp_path: Path,
) -> None:
    materializer = _load_module(
        _V11_MATERIALIZER,
        "_task7_v11_materializer_red",
    )
    assert materializer._V10_SOURCE_SHA512 == _V10_SHA512
    assert hashlib.sha256(_V10_ARTIFACT.read_bytes()).hexdigest() == VOID_V10_ARTIFACT_SHA256
    assert VOID_V10_ARTIFACT_SHA256 in VOID_CONTRACT_HASHES
    v10 = materializer._load_v10(_V10_ARTIFACT)
    assert materializer._render(materializer.build_v11(v10)) == _V11_ARTIFACT.read_bytes()

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert materializer.main(["--base-artifact", str(_V10_ARTIFACT), "--output", str(first)]) == 0
    assert materializer.main(["--base-artifact", str(_V10_ARTIFACT), "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes() == _V11_ARTIFACT.read_bytes()
    assert materializer.main(["--base-artifact", str(_V10_ARTIFACT), "--output", str(first)]) == 2

    corrupt = tmp_path / "v10-corrupt.json"
    raw = bytearray(_V10_ARTIFACT.read_bytes())
    raw[-2] ^= 1
    corrupt.write_bytes(raw)
    with pytest.raises(
        materializer.MaterializationError,
        match="TASK7_V11_BASE_ARTIFACT_HASH_MISMATCH",
    ):
        materializer._load_v10(corrupt)


def test_v11_generator_enforces_invariants_and_prospective_freeze_only() -> None:
    source = _V11_GENERATOR.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "from idis" not in source
    assert "import idis" not in source
    assert not _V11_FIXTURE.exists()

    generator = _load_module(_V11_GENERATOR, "_task7_v11_generator_red")
    raw = _V11_ARTIFACT.read_bytes()
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
    assert len(generator.target_trigger_authority(artifact)) == 73
    assert len(generator.target_event_authority(artifact)) == 192

    frozen_raw = generator.derive_prospective_frozen_bytes(raw)
    frozen = generator._decode_artifact(frozen_raw)
    artifact["metadata"]["append_frozen"] = True
    assert frozen == artifact
    assert _V11_ARTIFACT.read_bytes() == raw
    assert not _V11_FIXTURE.exists()


def test_v11_field_closure_rejects_tg_table_missing_and_unqualified_helper() -> None:
    generator = _load_module(
        _V11_GENERATOR,
        "_task7_v11_field_closure_red",
    )
    artifact = _load_json(_V11_ARTIFACT)
    generator._validate_current_guard_target_field_closure(artifact)
    generator._validate_locked_search_path_call_closure(artifact)

    missing = copy.deepcopy(artifact)
    body = _decode_body(missing, 19, 2).replace(
        "BEGIN IF TG_OP='DELETE'",
        "BEGIN IF TG_TABLE_NAME='run_steps' AND NEW.missing IS NULL THEN NULL; END IF; "
        "IF TG_OP='DELETE'",
        1,
    )
    missing["sql_authority_registry"]["rows"][19]["body_sql_utf8_hex_tuple"][2] = body.encode(
        "utf-8"
    ).hex()
    _rehash_authority(missing, 19)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V11_CURRENT_GUARD_TARGET_FIELD_MISMATCH",
    ):
        generator._validate_current_guard_target_field_closure(missing)

    unqualified_lease = copy.deepcopy(artifact)
    execution = unqualified_lease["erasure_execution_registry"]["rows"][0]
    upgrade_ordinal = next(
        ordinal
        for ordinal, encoded in enumerate(execution["legacy_guard_upgrade_sql_utf8_hex_tuple"])
        if "idis_task6_current_lease_is_valid(" in bytes.fromhex(encoded).decode("utf-8")
    )
    lease_body = bytes.fromhex(
        execution["legacy_guard_upgrade_sql_utf8_hex_tuple"][upgrade_ordinal]
    ).decode("utf-8")
    assert lease_body.count("FROM public.runs r") == 1
    execution["legacy_guard_upgrade_sql_utf8_hex_tuple"][upgrade_ordinal] = (
        lease_body.replace("FROM public.runs r", "FROM runs r", 1).encode("utf-8").hex()
    )
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V11_LOCKED_SEARCH_PATH_RELATION_UNQUALIFIED",
    ):
        generator._validate_locked_search_path_call_closure(unqualified_lease)

    unqualified_task6 = copy.deepcopy(artifact)
    task6_body = _decode_body(unqualified_task6, 27, 0)
    assert task6_body.count("FROM public.run_steps s") == 1
    task6_body = task6_body.replace(
        "FROM public.run_steps s",
        "FROM run_steps s",
        1,
    )
    unqualified_task6["sql_authority_registry"]["rows"][27]["body_sql_utf8_hex_tuple"][0] = (
        task6_body.encode("utf-8").hex()
    )
    _rehash_authority(unqualified_task6, 27)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V11_LOCKED_SEARCH_PATH_RELATION_UNQUALIFIED",
    ):
        generator._validate_locked_search_path_call_closure(unqualified_task6)

    unqualified_retained = copy.deepcopy(artifact)
    retained = _decode_body(unqualified_retained, 18, 2)
    assert retained.count("FROM public.run_output_objects r") == 1
    retained = retained.replace(
        "FROM public.run_output_objects r",
        "FROM run_output_objects r",
        1,
    )
    unqualified_retained["sql_authority_registry"]["rows"][18]["body_sql_utf8_hex_tuple"][2] = (
        retained.encode("utf-8").hex()
    )
    _rehash_authority(unqualified_retained, 18)
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V11_LOCKED_SEARCH_PATH_RELATION_UNQUALIFIED",
    ):
        generator._validate_locked_search_path_call_closure(unqualified_retained)


def test_v11_corruption_of_each_changed_contract_section_fails_admission() -> None:
    generator = _load_module(
        _V11_GENERATOR,
        "_task7_v11_corruption_red",
    )
    original = _V11_ARTIFACT.read_bytes()
    for path in (
        ("metadata", "reviewed_design_utf8_hex"),
        ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
        ("sql_authority_registry", "expected_sha256"),
        ("erasure_execution_registry", "expected_sha256"),
    ):
        artifact = generator._decode_artifact(original)
        parent: Any = artifact
        for coordinate in path[:-1]:
            parent = parent[coordinate]
        value = parent[path[-1]]
        assert isinstance(value, str) and value
        parent[path[-1]] = ("0" if value[0] != "0" else "1") + value[1:]
        corrupted = generator._render_artifact(artifact)
        with pytest.raises(generator.ContractArtifactError):
            generator._validate_shape(corrupted, artifact)
