"""RED-first admission contracts for the bounded full-strict-v12 successor."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from sqlalchemy import text
from tests.slice100_task2b._support import disposable_postgres, migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V11_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v11_reviewed_preimage_v1.json"
_V12_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v12_reviewed_preimage_v1.json"
_V12_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v12_preimage.py"
_V12_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v12_contract_fixture.py"
_V12_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v12_contract_hash_v1.json"
)

_V11_SOURCE_SHA512 = (
    "fd9d97c10de4bd4b5f571c32b633d620a811d75d8474d2fa20128d27156178ff"
    "ce5d588c7428a372d1639544b4c6042eaff0e7ce6a84d4604e860ebce2e82aed"
)

_GUARD_OWNER = "idis_task7_guard_function_owner"
_APP_ROLE = "AUTHENTICATED_APPLICATION_ROLE"
_ERASURE_OWNER = "idis_erasure_function_owner"
_EXPECTED_ROLE_POSTURE = {
    "login": False,
    "superuser": False,
    "createdb": False,
    "createrole": False,
    "inherit": False,
    "bypassrls": False,
}
_FOUR_ACL_CORRECTION_SIGNATURES = {
    "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)",
    "public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid)",
    "public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)",
    "public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)",
}
_EXTERNAL_HELPER_SIGNATURES = {
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
    "public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
}
_RETAINED_0037_SIGNATURES = {
    "public.idis_task5_coverage_scope_guard()",
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
    "public.idis_task6_immutable_guard()",
}
_RETAINED_0037_FUNCTION_BASELINE = {
    "public.idis_task5_coverage_scope_guard()": (
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        "679e84f8a9d4378178a4f076bbabddef40a2bbcb7ddcd11a0e9e7b4073e8d1e04"
        "fa2b2cb665097248bf9cd0fbf68d13b326291cbe393d355abcfce3edf891904",
        2716,
        "3d4767d2bf5678f8a9a003c3168d79e3050e16eaf0849bc49b8d79e6668b5fbf",
        2589,
        "ba593849e358a5c772a6af3531d32b193fd01d314432e71b872522c3127cd3ab",
    ),
    "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)": (
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        "de166216d565872b88c84d7d5abaff721b157e358e7c12110b0cb95d1468ec4b"
        "811371bd955ea584df754b4f3d4452b9fb2c6370f74cf2d83a9d0c2d61aab232",
        1883,
        "e83e6e6d60c3ea5bcdf65b010dc26b91a0abfe48513a8fabf3176308022dfda2",
        1677,
        "cde8f9e3b33a469ce43ec9b7751465d588267fbb1166ed4e8cd155557fb3b499",
    ),
    "public.idis_task6_immutable_guard()": (
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        "de166216d565872b88c84d7d5abaff721b157e358e7c12110b0cb95d1468ec4b"
        "811371bd955ea584df754b4f3d4452b9fb2c6370f74cf2d83a9d0c2d61aab232",
        761,
        "136aa75e5bb1f8a26288f532ef81eab0e22d7143759ebb2b7c89dc754db942d1",
        639,
        "97db59522cf42dd91797850e5f9ae438d4d8aa9914d24058bbb4943e7fa3114a",
    ),
}
_CHANGED_BODY_ORDINALS = {
    17: {0},
    18: {2, 6},
    19: {2},
    20: {0},
    21: {0},
    22: {0},
    23: {0},
    24: {0},
    25: {0},
    26: {0},
    27: {0},
}
_UNCHANGED_SECTIONS = {
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
}


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _decode_sql(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(bytes.fromhex(item).decode("utf-8") for item in row["body_sql_utf8_hex_tuple"])


def _decode_hex_tuple(values: list[str]) -> tuple[str, ...]:
    return tuple(bytes.fromhex(value).decode("utf-8") for value in values)


def _required_v12_assets() -> None:
    for path in (_V12_MATERIALIZER, _V12_GENERATOR, _V12_ARTIFACT):
        assert path.is_file(), f"required v12 asset is absent: {path}"


def _acl(artifact: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        artifact["sql_authority_registry"]["guard_function_acl_registry"],
    )


def test_v12_assets_are_unfrozen_and_only_v11_sha512_authenticator_is_active() -> None:
    _required_v12_assets()
    materializer = _load_module(_V12_MATERIALIZER, "_task7_v12_assets_materializer_red")
    artifact = _load_json(_V12_ARTIFACT)

    assert artifact["metadata"] | {} == artifact["metadata"]
    assert artifact["metadata"]["artifact_schema"] == "full-strict-v12-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v12"
    assert artifact["metadata"]["append_frozen"] is False
    assert not _V12_FIXTURE.exists()
    assert materializer._V11_SOURCE_SHA512 == _V11_SOURCE_SHA512
    assert not hasattr(materializer, "_V11_SOURCE_SHA256")


def test_v12_exact_successor_changes_only_acl_authority_and_dependent_bytes() -> None:
    _required_v12_assets()
    materializer = _load_module(_V12_MATERIALIZER, "_task7_v12_exact_diff_red")
    v11 = _load_json(_V11_ARTIFACT)
    v12 = _load_json(_V12_ARTIFACT)

    assert materializer.project_v12_to_v11(v12) == v11
    for section in _UNCHANGED_SECTIONS:
        assert v12[section] == v11[section], section
    assert tuple(v12) == tuple(v11)
    assert tuple(v12["sql_authority_registry"]) == (
        "rows",
        "guard_function_acl_registry",
        "expected_sha256",
    )

    old_rows = v11["sql_authority_registry"]["rows"]
    new_rows = v12["sql_authority_registry"]["rows"]
    assert len(old_rows) == len(new_rows) == 28
    for row_ordinal, (old_row, new_row) in enumerate(zip(old_rows, new_rows, strict=True)):
        changed_body_ordinals = {
            ordinal
            for ordinal, (old, new) in enumerate(
                zip(_decode_sql(old_row), _decode_sql(new_row), strict=True)
            )
            if old != new
        }
        assert changed_body_ordinals == _CHANGED_BODY_ORDINALS.get(row_ordinal, set())
        projected = copy.deepcopy(new_row)
        projected["body_sql_utf8_hex_tuple"] = old_row["body_sql_utf8_hex_tuple"]
        projected["expected_sha256"] = old_row["expected_sha256"]
        assert projected == old_row

    assert materializer._render(materializer.build_v12(v11)) == _V12_ARTIFACT.read_bytes()


def test_v12_binds_one_least_privilege_owner_and_complete_function_census() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    role = acl["guard_owner_role"]
    rows = acl["function_rows"]

    assert role["role_name"] == _GUARD_OWNER
    assert role["provisioning_mode"] == "OPERATOR_BOOTSTRAP_REQUIRED"
    assert role["posture"] == _EXPECTED_ROLE_POSTURE
    assert role["role_membership_tuple"] == []
    assert role["table_ownership_tuple"] == []
    assert role["schema_usage_tuple"] == ["public"]
    assert len(role["select_relation_column_tuple"]) == 36
    assert sum(len(columns) for _relation, columns in role["select_relation_column_tuple"]) == 366
    assert role["insert_relation_tuple"] == []
    assert role["update_relation_tuple"] == []
    assert role["delete_relation_tuple"] == []
    assert role["truncate_relation_tuple"] == []
    assert role["references_relation_tuple"] == []
    assert role["trigger_relation_tuple"] == []
    assert role["rls_contract_tuple"] == [
        "FORCE_RLS",
        "TRANSACTION_LOCAL_TENANT_CONTEXT",
        "idis.tenant_id",
        "canonical-null-guarded-using-and-with-check-v1",
    ]

    assert len(rows) == 34
    assert [row["function_ordinal"] for row in rows] == list(range(34))
    signatures = [row["function_signature"] for row in rows]
    assert len(signatures) == len(set(signatures))
    assert {row["function_kind"] for row in rows} == {"TRIGGER_GUARD", "HELPER"}
    assert sum(row["function_kind"] == "TRIGGER_GUARD" for row in rows) == 25
    assert sum(row["function_kind"] == "HELPER" for row in rows) == 9
    assert sum(row["install_scope"] == "CURRENT_0038" for row in rows) == 19
    assert sum(row["install_scope"] == "FUTURE_INACTIVE" for row in rows) == 14
    assert sum(row["install_scope"] == "EXTERNAL_DEPENDENCY" for row in rows) == 1
    assert {
        row["function_signature"] for row in rows if row["install_scope"] == "CURRENT_0038"
    } == set(acl["current_install_function_signature_tuple"])
    assert len(role["select_relation_tuple"]) == 36
    assert {
        row["function_signature"] for row in rows if row["definition_origin"] == "EXTERNAL"
    } == (_EXTERNAL_HELPER_SIGNATURES)

    assert {row["function_signature"] for row in rows if row["acl_correction_id"] is not None} == (
        _FOUR_ACL_CORRECTION_SIGNATURES
    )
    for row in rows:
        assert row["public_execute"] is False
        assert row["application_role"] == _APP_ROLE
        assert row["application_role_binding"] == "RUNTIME_SESSION_USER"
        assert row["application_role_execute"] is False
        assert row["search_path_tuple"] == ["pg_catalog", "pg_temp"]
        assert row["owner_role"] in {_GUARD_OWNER, _ERASURE_OWNER}
        assert row["insert_relation_tuple"] == []
        assert row["update_relation_tuple"] == []
        assert row["delete_relation_tuple"] == []
        assert row["truncate_relation_tuple"] == []
        assert row["references_relation_tuple"] == []
        assert row["trigger_relation_tuple"] == []
        assert len(row["select_relation_tuple"]) == len(set(row["select_relation_tuple"]))
        assert "direct_read_column_tuple" in row
        assert "transitive_read_column_tuple" in row
        assert all(columns for _relation, columns, _evidence in row["direct_read_column_tuple"])
        assert bytes.fromhex(row["owner_sql_utf8_hex"]).decode("utf-8") == (
            f"ALTER FUNCTION {row['function_signature']} OWNER TO {row['owner_role']}"
        )
        decoded_acl = tuple(
            bytes.fromhex(value).decode("utf-8") for value in row["acl_sql_utf8_hex_tuple"]
        )
        assert f"REVOKE ALL ON FUNCTION {row['function_signature']} FROM PUBLIC" in decoded_acl
        assert all(
            "idis_app" not in statement and "AUTHENTICATED_APPLICATION_ROLE" not in statement
            for statement in decoded_acl
        )

    assert {
        row["function_signature"]
        for row in rows
        if row["explicit_execute_grantee_tuple"] == [_GUARD_OWNER]
    } == {"public.idis_task7_approved_erasure_context_v1(uuid,uuid)"}
    assert {
        relation
        for row in rows
        if row["install_scope"] == "CURRENT_0038"
        for relation in row["select_relation_tuple"]
    } == set(role["select_relation_tuple"])
    assert [relation for relation, _columns in role["select_relation_column_tuple"]] == role[
        "select_relation_tuple"
    ]


def test_v12_installation_is_column_scoped_and_binds_all_366_reads() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    role = acl["guard_owner_role"]
    grants = _decode_hex_tuple(acl["current_column_grant_sql_utf8_hex_tuple"])
    revokes = _decode_hex_tuple(acl["current_column_revoke_sql_utf8_hex_tuple"])
    installation = _decode_hex_tuple(acl["installation_sql_utf8_hex_tuple"])

    assert len(grants) == len(revokes) == 36
    assert all(statement.startswith("GRANT SELECT (") for statement in grants)
    assert all(statement.startswith("REVOKE SELECT (") for statement in revokes)
    assert all(" ON TABLE public." in statement for statement in (*grants, *revokes))
    assert all(f" {_GUARD_OWNER}" in statement for statement in (*grants, *revokes))
    assert not any("GRANT SELECT ON TABLE" in statement for statement in installation)
    assert all(statement in installation for statement in (*grants, *revokes))

    expected_grants = tuple(
        f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{relation} TO {_GUARD_OWNER}"
        for relation, columns in role["select_relation_column_tuple"]
    )
    expected_revokes = tuple(
        f"REVOKE SELECT ({','.join(columns)}) ON TABLE public.{relation} FROM {_GUARD_OWNER}"
        for relation, columns in role["select_relation_column_tuple"]
    )
    assert grants == expected_grants
    assert revokes == expected_revokes
    assert sum(statement.count(",") + 1 for statement in grants) == 366


def test_v12_installation_refuses_a_nonclean_guard_owner_before_mutation() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    role = acl["guard_owner_role"]
    installation = _decode_hex_tuple(acl["installation_sql_utf8_hex_tuple"])
    preflight = bytes.fromhex(role["clean_role_preflight_sql_utf8_hex"]).decode("utf-8")
    retained_preflight = bytes.fromhex(acl["retained_0037_catalog_preflight_sql_utf8_hex"]).decode(
        "utf-8"
    )

    assert installation[0] == preflight
    assert installation[2] == retained_preflight
    assert preflight.startswith("DO $$DECLARE guard_oid oid; BEGIN")
    for catalog in (
        "pg_catalog.pg_roles",
        "pg_catalog.pg_auth_members",
        "pg_catalog.pg_database",
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_class",
        "pg_catalog.pg_attribute",
        "pg_catalog.pg_proc",
        "pg_catalog.pg_default_acl",
    ):
        assert catalog in preflight
    assert "datdba=guard_oid" in preflight
    assert "nspowner=guard_oid" in preflight
    assert "relowner=guard_oid" in preflight
    assert "proowner=guard_oid" in preflight
    assert "aclexplode" in preflight
    assert "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN" in preflight
    for catalog_field in ("prosrc", "prosecdef", "proconfig", "proacl"):
        assert catalog_field in retained_preflight
    assert "pg_catalog.to_regprocedure(expected.function_signature)" in retained_preflight
    assert "expected.function_signature::pg_catalog.regprocedure" not in retained_preflight
    assert "current_user" in retained_preflight
    assert "TASK7_RETAINED_0037_CATALOG_BASELINE_INVALID" in retained_preflight
    assert len(acl["retained_0037_catalog_projection_tuple"]) == 3
    assert {
        row[0] for row in acl["retained_0037_catalog_projection_tuple"]
    } == _RETAINED_0037_SIGNATURES
    assert not any(
        statement.startswith(("ALTER ", "REVOKE ", "GRANT ", "CREATE ", "DROP "))
        for statement in installation[:3]
    )


def test_v12_retained_0037_baseline_uses_authenticated_exact_migration_bytes() -> None:
    _required_v12_assets()
    materializer = _load_module(_V12_MATERIALIZER, "_task7_v12_exact_0037_source_red")
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)

    projection = acl["retained_0037_catalog_projection_tuple"]
    assert projection == materializer._authenticated_retained_0037_catalog_projection(_REPO_ROOT)
    assert len(projection) == 3
    for row in projection:
        (
            signature,
            function_sql_hex,
            prosrc_hex,
            source_path,
            source_sha512,
            owner_binding,
            security_definer,
            proconfig,
            proacl,
        ) = row
        (
            expected_path,
            expected_source_sha512,
            expected_sql_length,
            expected_sql_sha256,
            expected_prosrc_length,
            expected_prosrc_sha256,
        ) = _RETAINED_0037_FUNCTION_BASELINE[signature]
        function_sql = bytes.fromhex(function_sql_hex).decode("utf-8")
        prosrc = bytes.fromhex(prosrc_hex).decode("utf-8")

        assert source_path == expected_path
        assert source_sha512 == expected_source_sha512
        assert len(function_sql) == expected_sql_length
        assert hashlib.sha256(function_sql.encode("utf-8")).hexdigest() == expected_sql_sha256
        assert len(prosrc) == expected_prosrc_length
        assert hashlib.sha256(prosrc.encode("utf-8")).hexdigest() == expected_prosrc_sha256
        assert materializer._function_prosrc(function_sql) == prosrc
        assert owner_binding == "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER"
        assert security_definer is False
        assert proconfig is None
        assert proacl is None

    downgrade = acl["downgrade_authority"]
    restores = _decode_hex_tuple(downgrade["retained_function_restore_sql_utf8_hex_tuple"])
    assert restores == tuple(bytes.fromhex(row[1]).decode("utf-8") for row in projection)


def test_v12_retained_0037_trigger_catalog_is_source_authenticated_and_complete() -> None:
    _required_v12_assets()
    materializer = _load_module(_V12_MATERIALIZER, "_task7_v12_exact_0037_triggers_red")
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    projection = acl["retained_0037_trigger_catalog_projection_tuple"]

    assert projection == materializer._authenticated_retained_0037_trigger_catalog_projection(
        _REPO_ROOT
    )
    assert len(projection) == 9
    assert len({(row[1], row[2]) for row in projection}) == 9
    assert {row[0] for row in projection} == {
        "public.idis_task5_coverage_scope_guard()",
        "public.idis_task6_immutable_guard()",
    }
    assert all(
        row[3:10] == ["BEFORE", ["INSERT", "UPDATE", "DELETE"], "ROW", "O", False, False, False]
        for row in projection
    )

    retained_coordinates = {
        (
            row["function_signature"],
            coordinate[0],
            coordinate[1],
            coordinate[2],
            coordinate[3],
            coordinate[4],
            coordinate[5],
            coordinate[6],
            coordinate[7],
            coordinate[8],
        )
        for row in acl["function_rows"]
        if row["function_signature"] in _RETAINED_0037_SIGNATURES
        for coordinate in row["target_event_coordinate_tuple"]
    }
    projected_coordinates = {
        (
            signature,
            relation,
            trigger,
            timing,
            event,
            level,
            enabled,
            is_constraint,
            deferrable,
            initially_deferred,
        )
        for (
            signature,
            relation,
            trigger,
            timing,
            events,
            level,
            enabled,
            is_constraint,
            deferrable,
            initially_deferred,
            _source_path,
            _source_sha512,
        ) in projection
        for event in events
    }
    assert projected_coordinates == retained_coordinates


def test_v12_retained_0037_projection_matches_disposable_postgres_catalog() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    function_projection = acl["retained_0037_catalog_projection_tuple"]
    trigger_projection = acl["retained_0037_trigger_catalog_projection_tuple"]

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.connect() as connection:
            for row in function_projection:
                (
                    signature,
                    _function_sql_hex,
                    prosrc_hex,
                    _source_path,
                    _source_sha512,
                    _owner_binding,
                    security_definer,
                    proconfig,
                    proacl,
                ) = row
                actual = connection.execute(
                    text(
                        """
                        SELECT p.prosrc,
                               owner.rolname,
                               current_user,
                               p.prosecdef,
                               p.proconfig,
                               p.proacl
                        FROM pg_catalog.pg_proc AS p
                        JOIN pg_catalog.pg_roles AS owner ON owner.oid = p.proowner
                        WHERE p.oid = pg_catalog.to_regprocedure(:signature)
                        """
                    ),
                    {"signature": signature},
                ).one()
                assert actual.prosrc == bytes.fromhex(prosrc_hex).decode("utf-8")
                assert actual.rolname == actual.current_user
                assert actual.prosecdef is security_definer
                assert actual.proconfig == proconfig
                assert actual.proacl == proacl

            for row in trigger_projection:
                (
                    function_signature,
                    relation,
                    trigger,
                    timing,
                    events,
                    level,
                    enabled,
                    is_constraint,
                    deferrable,
                    initially_deferred,
                    _source_path,
                    _source_sha512,
                ) = row
                actual = connection.execute(
                    text(
                        """
                        SELECT t.tgtype,
                               t.tgenabled,
                               t.tgconstraint <> 0 AS is_constraint,
                               t.tgdeferrable,
                               t.tginitdeferred,
                               p.oid::pg_catalog.regprocedure::text AS function_signature
                        FROM pg_catalog.pg_trigger AS t
                        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
                        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
                        WHERE n.nspname = 'public'
                          AND c.relname = :relation
                          AND t.tgname = :trigger
                          AND NOT t.tgisinternal
                        """
                    ),
                    {"relation": relation, "trigger": trigger},
                ).one()
                assert ("BEFORE" if actual.tgtype & 2 else "AFTER") == timing
                assert [
                    event
                    for event, mask in (("INSERT", 4), ("UPDATE", 16), ("DELETE", 8))
                    if actual.tgtype & mask
                ] == events
                assert ("ROW" if actual.tgtype & 1 else "STATEMENT") == level
                assert actual.tgenabled == enabled
                assert actual.is_constraint is is_constraint
                assert actual.tgdeferrable is deferrable
                assert actual.tginitdeferred is initially_deferred
                assert actual.function_signature == function_signature.removeprefix("public.")


def test_v12_downgrade_restores_0037_and_leaves_guard_owner_without_db_acl() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    downgrade = acl["downgrade_authority"]
    rows = acl["function_rows"]

    new_trigger_drops = _decode_hex_tuple(downgrade["new_trigger_drop_sql_utf8_hex_tuple"])
    retained_trigger_drops = _decode_hex_tuple(
        downgrade["retained_trigger_drop_sql_utf8_hex_tuple"]
    )
    function_drops = _decode_hex_tuple(downgrade["current_function_drop_sql_utf8_hex_tuple"])
    restores = _decode_hex_tuple(downgrade["retained_function_restore_sql_utf8_hex_tuple"])
    trigger_restores = _decode_hex_tuple(downgrade["retained_trigger_restore_sql_utf8_hex_tuple"])
    ordered = _decode_hex_tuple(downgrade["ordered_sql_utf8_hex_tuple"])

    assert len(new_trigger_drops) == 64
    assert len(retained_trigger_drops) == len(trigger_restores) == 9
    assert len(function_drops) == 19
    assert len(restores) == 3
    assert set(downgrade["retained_function_signature_tuple"]) == (_RETAINED_0037_SIGNATURES)
    assert all(statement.startswith("DROP TRIGGER ") for statement in new_trigger_drops)
    assert all(statement.startswith("DROP TRIGGER ") for statement in retained_trigger_drops)
    assert all(statement.startswith("DROP FUNCTION public.") for statement in function_drops)
    assert all("CREATE OR REPLACE FUNCTION" in statement for statement in restores)
    assert all("CREATE TRIGGER " in statement for statement in trigger_restores)
    assert _decode_hex_tuple(downgrade["external_helper_execute_revoke_sql_utf8_hex_tuple"]) == (
        "REVOKE EXECUTE ON FUNCTION "
        "public.idis_task7_approved_erasure_context_v1(uuid,uuid) "
        f"FROM {_GUARD_OWNER}",
    )
    assert _decode_hex_tuple(downgrade["column_select_revoke_sql_utf8_hex_tuple"]) == (
        _decode_hex_tuple(acl["current_column_revoke_sql_utf8_hex_tuple"])
    )
    assert _decode_hex_tuple(downgrade["schema_usage_revoke_sql_utf8_hex_tuple"]) == (
        f"REVOKE USAGE ON SCHEMA public FROM {_GUARD_OWNER}",
    )
    assert "pg_catalog.pg_attribute" in bytes.fromhex(
        downgrade["zero_privilege_assertion_sql_utf8_hex"]
    ).decode("utf-8")
    downgrade_zero = bytes.fromhex(downgrade["zero_privilege_assertion_sql_utf8_hex"]).decode(
        "utf-8"
    )
    assert "attacl" in downgrade_zero
    assert downgrade_zero.replace(
        "TASK7_GUARD_FUNCTION_OWNER_DOWNGRADE_ACL_INVALID",
        "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN",
    ) == bytes.fromhex(acl["guard_owner_role"]["clean_role_preflight_sql_utf8_hex"]).decode("utf-8")

    by_signature = {row["function_signature"]: row for row in rows}
    function_order = list(downgrade["current_function_drop_signature_tuple"])
    assert set(function_order) == {
        row["function_signature"] for row in rows if row["install_scope"] == "CURRENT_0038"
    }
    position = {signature: ordinal for ordinal, signature in enumerate(function_order)}
    for signature in function_order:
        for callee in by_signature[signature]["helper_call_signature_tuple"]:
            if callee in position:
                assert position[signature] < position[callee]

    assert ordered[:64] == new_trigger_drops
    assert ordered[64:73] == retained_trigger_drops
    postrestore = bytes.fromhex(
        downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
    ).decode("utf-8")
    assert ordered[-13:-10] == restores
    assert ordered[-10:-1] == trigger_restores
    assert ordered[-1] == postrestore
    assert postrestore == bytes.fromhex(acl["retained_0037_catalog_preflight_sql_utf8_hex"]).decode(
        "utf-8"
    )
    assert downgrade["restored_catalog_contract"] == {
        "owner_binding": "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
        "prosecdef": False,
        "proconfig": None,
        "proacl": None,
    }
    expected_restores = tuple(
        bytes.fromhex(row[1]).decode("utf-8")
        for row in acl["retained_0037_catalog_projection_tuple"]
    )
    assert restores == expected_restores
    assert all(" SECURITY DEFINER " not in statement for statement in restores)
    assert all(" SET search_path" not in statement for statement in restores)
    assert all(
        ordered.index(function_drop) < ordered.index(expected_restore)
        for function_drop, expected_restore in zip(
            (
                statement
                for signature, statement in zip(
                    function_order,
                    function_drops,
                    strict=True,
                )
                if signature in _RETAINED_0037_SIGNATURES
            ),
            expected_restores,
            strict=True,
        )
    )


def test_v12_all_trigger_guards_are_definer_and_four_helpers_are_explicit_invoker() -> None:
    _required_v12_assets()
    artifact = _load_json(_V12_ARTIFACT)
    rows = _acl(artifact)["function_rows"]
    sql_rows = artifact["sql_authority_registry"]["rows"]
    all_sql = "\n".join(statement for row in sql_rows for statement in _decode_sql(row))

    for row in rows:
        signature = row["function_signature"]
        name = signature.partition("(")[0]
        if row["function_kind"] == "TRIGGER_GUARD":
            assert row["security_mode"] == "DEFINER"
            assert f"FUNCTION {name}" in all_sql
        elif signature in _FOUR_ACL_CORRECTION_SIGNATURES:
            assert row["security_mode"] == "INVOKER"
            assert row["acl_correction_id"].startswith("V11_42501_")

    trigger_definitions = [
        statement
        for row in sql_rows
        for statement in _decode_sql(row)
        if " RETURNS trigger " in statement
    ]
    assert len(trigger_definitions) == 25
    assert all(
        " SECURITY DEFINER SET search_path=pg_catalog,pg_temp " in sql
        for sql in trigger_definitions
    )
    for signature in _FOUR_ACL_CORRECTION_SIGNATURES:
        name = signature.partition("(")[0]
        definition = next(
            statement
            for row in sql_rows
            for statement in _decode_sql(row)
            if f"FUNCTION {name}(" in statement
        )
        assert " SECURITY INVOKER SET search_path=pg_catalog,pg_temp " in definition


def test_v12_acl_registry_has_no_orphan_function_target_helper_owner_or_grant() -> None:
    _required_v12_assets()
    generator = _load_module(_V12_GENERATOR, "_task7_v12_acl_closure_red")
    artifact = _load_json(_V12_ARTIFACT)

    generator._validate_guard_function_acl_registry(artifact)
    assert len(generator.guard_function_authority(artifact)) == 34
    assert len(generator.guard_target_authority(artifact)) > 73
    assert len(generator.guard_helper_call_authority(artifact)) > 0
    rows = _acl(artifact)["function_rows"]
    by_signature = {row["function_signature"]: row for row in rows}
    for row in rows:
        assert [item[0] for item in row["direct_read_column_tuple"]] == row["select_relation_tuple"]
        transitive_relations = {
            item[0]: set(item[1]) for item in row["transitive_read_column_tuple"]
        }
        for relation, columns, _evidence in row["direct_read_column_tuple"]:
            assert set(columns) <= transitive_relations[relation]
        for callee in row["helper_call_signature_tuple"]:
            for relation, columns, _evidence in by_signature[callee][
                "transitive_read_column_tuple"
            ]:
                assert set(columns) <= transitive_relations[relation]

    duplicate = copy.deepcopy(artifact)
    duplicate_acl = _acl(duplicate)
    duplicate_acl["function_rows"].append(copy.deepcopy(duplicate_acl["function_rows"][0]))
    with pytest.raises(generator.ContractArtifactError) as duplicate_error:
        generator._validate_guard_function_acl_registry(duplicate)
    assert duplicate_error.value.code == "TASK7_V12_ACL_FUNCTION_CENSUS_INVALID"

    orphan = copy.deepcopy(artifact)
    _acl(orphan)["function_rows"][0]["authority_id"] = "orphan-authority-v1"
    with pytest.raises(generator.ContractArtifactError) as orphan_error:
        generator._validate_guard_function_acl_registry(orphan)
    assert orphan_error.value.code == "TASK7_V12_ACL_AUTHORITY_ORPHAN"


def test_v12_trigger_targets_bind_all_event_coordinates_bidirectionally() -> None:
    _required_v12_assets()
    generator = _load_module(_V12_GENERATOR, "_task7_v12_target_events_red")
    artifact = _load_json(_V12_ARTIFACT)
    rows = _acl(artifact)["function_rows"]
    current = [row for row in rows if row["install_scope"] == "CURRENT_0038"]
    future = [row for row in rows if row["install_scope"] == "FUTURE_INACTIVE"]

    assert sum(len(row["target_trigger_tuple"]) for row in current) == 73
    assert sum(len(row["target_event_coordinate_tuple"]) for row in current) == 192
    assert sum(len(row["target_trigger_tuple"]) for row in future) == 81
    assert sum(len(row["target_event_coordinate_tuple"]) for row in future) == 242
    assert len(generator.guard_target_event_authority(artifact)) == 434
    for row in (*current, *future):
        targets = {tuple(value) for value in row["target_trigger_tuple"]}
        coordinate_targets = {
            (value[0], value[1]) for value in row["target_event_coordinate_tuple"]
        }
        assert coordinate_targets == targets
        assert all(
            value[2] in {"BEFORE", "AFTER"}
            and value[3] in {"INSERT", "UPDATE", "DELETE"}
            and value[4] == "ROW"
            and value[5] == "O"
            and isinstance(value[6], bool)
            and isinstance(value[7], bool)
            and isinstance(value[8], bool)
            for value in row["target_event_coordinate_tuple"]
        )


def test_v12_generator_keeps_semantic_vectors_and_refuses_unfrozen_publication() -> None:
    _required_v12_assets()
    generator = _load_module(_V12_GENERATOR, "_task7_v12_generator_red")
    v11 = _load_json(_V11_ARTIFACT)
    raw = _V12_ARTIFACT.read_bytes()
    artifact = generator._decode_artifact(raw)

    generator._validate_shape(raw, artifact)
    assert generator.invariant_vector(artifact) == generator._V11.invariant_vector(v11)
    # The canonical vector intentionally retains its full census, not a five-key test projection.
    assert generator.invariant_vector(artifact) | {} == {
        "semantic_step_count": 28,
        "edge_rule_count": 48,
        "usage_route_count": 1756,
        "usage_extractor_count": 19,
        "provider_count": 18,
        "current_ddl_count": 34,
        "materialized_physical_table_count": 72,
        "erasure_phase_count": 66,
    }
    prospective = generator.derive_prospective_frozen_bytes(raw)
    assert json.loads(prospective)["metadata"]["append_frozen"] is True
    prospective_contract = generator.derive_contract_bytes(prospective)
    assert (
        prospective_contract["component_hashes"]["sql_authority_registry_hash"]
        == (json.loads(prospective)["sql_authority_registry"]["expected_sha256"])
    )
    assert _V12_ARTIFACT.read_bytes() == raw

    completed = subprocess.run(
        [sys.executable, str(_V12_GENERATOR), "--artifact", str(_V12_ARTIFACT)],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.strip() == "TASK7_V12_FIXTURE_PUBLICATION_REFUSED_UNFROZEN"
    assert not _V12_FIXTURE.exists()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("owner_posture", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("function_owner", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("select_grant", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("public_execute", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("body_security", "PIPELINE_CONTRACT_HASH_MISMATCH"),
        ("downgrade", "PIPELINE_CONTRACT_HASH_MISMATCH"),
    ],
)
def test_v12_corruption_of_each_acl_surface_fails_admission(
    mutation: str,
    expected_code: str,
) -> None:
    _required_v12_assets()
    generator = _load_module(
        _V12_GENERATOR,
        f"_task7_v12_corruption_{mutation}_red",
    )
    artifact = _load_json(_V12_ARTIFACT)
    acl = _acl(artifact)
    if mutation == "owner_posture":
        acl["guard_owner_role"]["posture"]["bypassrls"] = True
    elif mutation == "function_owner":
        acl["function_rows"][0]["owner_role"] = _APP_ROLE
    elif mutation == "select_grant":
        acl["guard_owner_role"]["select_relation_tuple"].append("private_unreviewed_table")
    elif mutation == "public_execute":
        acl["function_rows"][0]["public_execute"] = True
    elif mutation == "body_security":
        sql_row = artifact["sql_authority_registry"]["rows"][17]
        body = _decode_sql(sql_row)[0].replace(" SECURITY DEFINER ", " ")
        sql_row["body_sql_utf8_hex_tuple"][0] = body.encode("utf-8").hex()
    else:
        acl["downgrade_authority"]["ordered_sql_utf8_hex_tuple"].pop()
    corrupted = (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )

    with pytest.raises(generator.ContractArtifactError) as error:
        generator._validate_shape(corrupted, artifact)
    assert error.value.code == expected_code


def test_v12_materializer_authenticates_v11_and_is_deterministic(tmp_path: Path) -> None:
    _required_v12_assets()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        completed = subprocess.run(
            [
                sys.executable,
                str(_V12_MATERIALIZER),
                "--base-artifact",
                str(_V11_ARTIFACT),
                "--output",
                str(output),
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == ""
        assert completed.stdout.startswith("TASK7_V12_CANDIDATE_MATERIALIZED:")
        assert output.read_bytes() == _V12_ARTIFACT.read_bytes()
    assert (
        hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    )

    duplicate = subprocess.run(
        [
            sys.executable,
            str(_V12_MATERIALIZER),
            "--base-artifact",
            str(_V11_ARTIFACT),
            "--output",
            str(first),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert duplicate.returncode == 2
    assert duplicate.stderr.strip() == "TASK7_V12_OUTPUT_ALREADY_EXISTS"

    tampered = tmp_path / "tampered-v11.json"
    raw = bytearray(_V11_ARTIFACT.read_bytes())
    raw[-2] ^= 1
    tampered.write_bytes(raw)
    rejected = subprocess.run(
        [
            sys.executable,
            str(_V12_MATERIALIZER),
            "--base-artifact",
            str(tampered),
            "--output",
            str(tmp_path / "rejected.json"),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert rejected.returncode == 2
    assert rejected.stderr.strip() == "TASK7_V12_BASE_ARTIFACT_HASH_MISMATCH"
