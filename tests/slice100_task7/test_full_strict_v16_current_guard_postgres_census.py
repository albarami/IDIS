"""Admission gate for the full-strict-v16 application-role PostgreSQL census."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
import uuid
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT_PATH = _REPO_ROOT / "tests" / "slice100_task7" / "_v16_current_guard_census_support.py"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
_SCAFFOLD_PATH = _REPO_ROOT / "scripts" / "_task7_v16_census_scaffold.py"


@cache
def _support() -> ModuleType:
    assert _SUPPORT_PATH.is_file(), "TASK7_V16_APP_ROLE_CENSUS_SUPPORT_MISSING"
    spec = importlib.util.spec_from_file_location(
        "_task7_v16_current_guard_census_test_support",
        _SUPPORT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v16_app_role_census_lane_exists_as_new_versioned_files() -> None:
    assert _SUPPORT_PATH.is_file(), "TASK7_V16_APP_ROLE_CENSUS_SUPPORT_MISSING"
    assert _RUNNER_PATH.is_file(), "TASK7_V16_APP_ROLE_CENSUS_RUNNER_MISSING"
    assert _SCAFFOLD_PATH.is_file(), "TASK7_V16_APP_ROLE_CENSUS_SCAFFOLD_MISSING"
    runner_source = _RUNNER_PATH.read_text(encoding="utf-8")
    scaffold_source = _SCAFFOLD_PATH.read_text(encoding="utf-8")
    assert "run_task7_v14_current_guard_census" not in runner_source
    assert "run_task7_v15_current_guard_census" not in runner_source
    assert "_task7_v14_census_scaffold" not in scaffold_source
    assert "_task7_v15_census_scaffold" not in scaffold_source
    assert '"run_task7_v11_current_guard_census.py"' in runner_source
    assert '"_task7_v11_census_scaffold.py"' in scaffold_source
    assert "def _capture_cluster_safety_snapshot(" in runner_source
    # The combined context order makes the residue boundary enter before and
    # exit after the disposable database/role lifecycle.
    assert runner_source.index(
        "with _cluster_safety_boundary(), disposable_postgres() as database:"
    ) > runner_source.index("def _capture_cluster_safety_snapshot(")
    assert "CREATE ROLE {GUARD_OWNER_ROLE}" not in scaffold_source
    task6_source = (
        _REPO_ROOT
        / "tests"
        / "slice100_task7"
        / "test_full_strict_v16_task6_before_generated_postgres_red.py"
    ).read_text(encoding="utf-8")
    assert "runner._decode_artifact(_ARTIFACT_PATH)" in task6_source
    assert task6_source.index("runner._decode_artifact(_ARTIFACT_PATH)") < task6_source.index(
        "with runner._cluster_safety_boundary(), disposable_postgres() as database:"
    )
    assert "json.loads(_ARTIFACT_PATH.read_bytes())" not in task6_source


def test_v16_artifact_declares_exact_guard_acl_authority() -> None:
    support = _support()
    module = support.runner()
    artifact, raw = support.artifact_and_raw()
    assert hashlib.sha512(raw).hexdigest() == support.EXPECTED_ARTIFACT_SHA512
    assert hashlib.sha256(raw).hexdigest() == (
        "dd7b2585ff01909765342549bf60b4ba32d18cec398c8b002a77c55d0f92ce74"
    )
    frozen_raw = module._derive_prospective_frozen_bytes(raw)
    assert hashlib.sha256(frozen_raw).hexdigest() == (
        "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"
    )
    fixture = support._generator().derive_contract_bytes(frozen_raw)
    assert fixture["FULL_STRICT_V16_CONTRACT_HASH"] == (
        "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
    )
    assert len(module._CLOSED_RESULT_FIELDS) == 66
    historical_raw = (_REPO_ROOT / "scripts" / "run_task7_v11_current_guard_census.py").read_bytes()
    adapted_source = module._adapt_historical_execution_source(historical_raw)
    assert adapted_source.count('"full-strict-v16"') == 13
    assert '"full-strict-v11"' not in adapted_source
    for rejected_version in ("v14", "v15", "v17"):
        poisoned = historical_raw.replace(
            b'"full-strict-v11"',
            f'"full-strict-{rejected_version}"'.encode("ascii"),
            1,
        )
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID$",
        ):
            module._adapt_historical_execution_source(poisoned)
    module.validate_historical_fixture_values(
        "runs",
        {
            "run_id": uuid.uuid4(),
            "pipeline_contract_version": "full-strict-v16",
        },
    )
    for invalid_values in (
        {"pipeline_contract_version": "full-strict-v11"},
        {"pipeline_contract_version": "full-strict-v14"},
        {"pipeline_contract_version": "full-strict-v15"},
        {"pipeline_contract_version": "full-strict-v17"},
        {"unexpected_version": "full-strict-v16"},
    ):
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID$",
        ):
            module.validate_historical_fixture_values("runs", invalid_values)
    for rejected_token in (
        "V11",
        "task7-v14-catalog",
        "_task7_v15_internal",
    ):
        for poisoned in (
            {"outer": [{"weakly_constrained_catalog_field": rejected_token}]},
            {f"weakly_constrained_{rejected_token}_key": "ordinary-value"},
        ):
            with pytest.raises(
                module.CensusError,
                match=r"^TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID$",
            ):
                module._validate_serialized_provenance(
                    poisoned,
                    error_code="TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID",
                )
    module._validate_serialized_provenance(
        {
            "v110_is_safe": "task7-v140-is-safe",
            "_task7_v150_is_safe": ["ordinary"],
        },
        error_code="TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID",
    )
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    for invalid_serialized_value in (
        cyclic,
        {"non_json": object()},
        {1: "non-string-key"},
    ):
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID$",
        ):
            module._validate_serialized_provenance(
                invalid_serialized_value,
                error_code="TASK7_V16_CENSUS_RESULT_PROVENANCE_INVALID",
            )
    expected_external = module._external_erasure_helper_contract(artifact)
    assert expected_external == {
        "function_signature": "public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
        "function_owner": "idis_erasure_function_owner",
        "security_definer": True,
        "search_path_tuple": ("pg_catalog", "pg_temp"),
        "execute_grantee_tuple": (
            "idis_erasure_function_owner",
            support.GUARD_OWNER_ROLE,
        ),
        "application_role_execute": False,
        "public_execute": False,
    }
    valid_external_proof = {
        "function_signature": expected_external["function_signature"],
        "guard_owner_execute": True,
        "application_direct_execute": False,
        "public_direct_execute": False,
        "application_direct_call_denied": True,
        "ordinary_guard_call_result": False,
        "function_owner": expected_external["function_owner"],
        "security_definer": True,
        "search_path_tuple": ["pg_catalog", "pg_temp"],
        "raw_execute_acl_tuple": [
            {
                "grantee": grantee,
                "grantor": expected_external["function_owner"],
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
            for grantee in expected_external["execute_grantee_tuple"]
        ],
    }
    module._validate_external_erasure_helper_proof(
        valid_external_proof,
        artifact=artifact,
        error_code="TEST_INVALID_EXTERNAL_HELPER_PROOF",
    )
    extra_acl_field = copy.deepcopy(valid_external_proof)
    extra_acl_field["raw_execute_acl_tuple"][0]["unknown_nested_acl_field"] = "MASKED"
    with pytest.raises(
        module.CensusError,
        match=r"^TEST_INVALID_EXTERNAL_HELPER_PROOF$",
    ):
        module._validate_external_erasure_helper_proof(
            extra_acl_field,
            artifact=artifact,
            error_code="TEST_INVALID_EXTERNAL_HELPER_PROOF",
        )
    assert module._normalize_locked_search_path(
        ["search_path=pg_catalog, pg_temp"],
        expected=("pg_catalog", "pg_temp"),
        error_code="TEST_INVALID_PROCONFIG",
    ) == ("pg_catalog", "pg_temp")
    for invalid_proconfig in (
        "search_path=pg_catalog, pg_temp",
        ["search_path"],
        ["search_path=pg_catalog, pg_temp", "search_path=pg_catalog"],
        ["search_path=pg_catalog, pg_temp", "statement_timeout=1s"],
    ):
        with pytest.raises(
            module.CensusError,
            match=r"^TEST_INVALID_PROCONFIG$",
        ):
            module._normalize_locked_search_path(
                invalid_proconfig,
                expected=("pg_catalog", "pg_temp"),
                error_code="TEST_INVALID_PROCONFIG",
            )
    stable_snapshot = {
        "generated_database_tuple": ("idis_t2b_db_existing",),
        "generated_app_role_tuple": ("idis_t2b_app_existing",),
    }
    module._assert_cluster_residue_free(stable_snapshot, dict(stable_snapshot))
    changed_snapshot = {
        **stable_snapshot,
        "generated_app_role_tuple": (
            "idis_t2b_app_existing",
            "idis_t2b_app_residue",
        ),
    }
    with pytest.raises(
        module.CensusError,
        match=(
            r"^TASK7_V16_CLUSTER_RESIDUE_DETECTED:"
            r"generated_app_role_tuple$"
        ),
    ):
        module._assert_cluster_residue_free(stable_snapshot, changed_snapshot)
    assert module._validate_signer_id("task7-v16-signer") == "task7-v16-signer"
    for invalid_signer_id in (None, 17, "", " \t"):
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
        ):
            module._validate_signer_id(invalid_signer_id)
    for rejected_version in ("v14", "v15"):
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED$",
        ):
            module._decode_artifact(
                _REPO_ROOT
                / "contracts"
                / f"full_strict_{rejected_version}_reviewed_preimage_v1.json"
            )
    registry = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    assert registry["guard_owner_role"]["role_name"] == support.GUARD_OWNER_ROLE
    assert registry["guard_owner_role"]["posture"] == {
        "login": False,
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "inherit": False,
        "bypassrls": False,
    }
    current = tuple(
        row for row in registry["function_rows"] if row["install_scope"] == "CURRENT_0038"
    )
    assert len(current) == 19
    assert tuple(row["function_signature"] for row in current) == tuple(
        registry["current_install_function_signature_tuple"]
    )
    # The ruling applies SECURITY DEFINER to trigger guards; pure helpers stay
    # SECURITY INVOKER while retaining the same owner/ACL/search-path closure.
    assert all(
        row["security_mode"] == "DEFINER"
        for row in current
        if row["function_kind"] == "TRIGGER_GUARD"
    )
    assert all(row["search_path_tuple"] == ["pg_catalog", "pg_temp"] for row in current)
    assert all(row["owner_role"] == support.GUARD_OWNER_ROLE for row in current)
    assert all(row["application_role_execute"] is False for row in current)
    assert all(row["public_execute"] is False for row in current)
    assert len(registry["guard_owner_role"]["select_relation_column_tuple"]) == 36
    assert (
        sum(
            len(columns)
            for _relation, columns in registry["guard_owner_role"]["select_relation_column_tuple"]
        )
        == 366
    )


def test_v16_trigger_inventory_is_exactly_73_targets_and_192_events() -> None:
    support = _support()
    assert len(support.EXPECTED_ORDERED_TARGETS) == 73
    inventory = support.expected_trigger_event_inventory()
    assert len(inventory) == 192
    assert len(set(inventory)) == 192


def test_v16_disposable_scaffold_constraint_scope_covers_every_guard_target() -> None:
    support = _support()
    module = support.runner()
    artifact, _raw = support.artifact_and_raw()
    relation_names = module.case_scaffold_relation_names(
        artifact,
        support.EXPECTED_ORDERED_TARGETS,
    )
    assert {
        table_name for _authority_id, table_name, _guard_name in support.EXPECTED_ORDERED_TARGETS
    }.issubset(set(relation_names))
    assert {
        "run_methodology_selections",
        "methodology_questions",
    }.issubset(set(relation_names))


def test_v16_census_executes_every_case_as_generated_application_role() -> None:
    support = _support()
    result = support.census_result()
    support.assert_complete_census_result(result)
    assert result["database_actor"]["role_name"] == result["application_role_name"]
    assert result["database_actor"]["is_superuser"] is False
    assert result["database_actor"]["bypass_rls"] is False
    assert result["database_actor"]["case_execution_count"] == 219
    assert result["admin_case_execution_count"] == 0
    assert result["application_case_execution_count"] == 219
    assert all(
        row["executed_as_role"] == result["application_role_name"]
        for row in result["case_outcome_tuple"]
    )
    assert all(
        row["observed_session_user"] == result["application_role_name"]
        and row["observed_is_superuser"] is False
        and row["observed_bypass_rls"] is False
        for row in result["case_outcome_tuple"]
    )
    assert all(row["sqlstate"] != "42501" for row in result["case_outcome_tuple"])
    assert (
        tuple(
            (
                row["case_kind"],
                row["authority_id"],
                row["table_name"],
                row["guard_name"],
                row["trigger_event"],
            )
            for row in result["case_outcome_tuple"]
        )
        == support.expected_case_coordinates()
    )
    assert {tuple(row) for row in result["observed_trigger_contract_coordinate_tuple"]} == set(
        support.expected_trigger_contract_coordinates()
    )
    assert len(result["observed_trigger_contract_coordinate_tuple"]) == 192


def test_v16_retains_all_six_source_bound_transition_and_rejection_paths() -> None:
    support = _support()
    result = support.census_result()
    expected_targets = tuple(
        (table_name, guard_name)
        for authority_id, table_name, guard_name in support.EXPECTED_ORDERED_TARGETS
        if authority_id == "current-source-bound-v1"
    )
    assert len(expected_targets) == 6
    rows = tuple(
        row
        for row in result["case_outcome_tuple"]
        if row["authority_id"] == "current-source-bound-v1"
    )
    assert len(rows) == 18
    for case_kind, event, outcome in (
        ("LEGAL", "UPDATE", "ACCEPTED"),
        ("ILLEGAL", "UPDATE", "REJECTED"),
        ("DIRECT_SQL", "DELETE", "REJECTED"),
    ):
        case_rows = tuple(row for row in rows if row["case_kind"] == case_kind)
        assert tuple((row["table_name"], row["guard_name"]) for row in case_rows) == (
            expected_targets
        )
        assert all(row["trigger_event"] == event and row["outcome"] == outcome for row in case_rows)
        if case_kind == "LEGAL":
            assert all(
                row["sqlstate"] is None and row["authority_rejection_marker"] is None
                for row in case_rows
            )
        else:
            assert all(
                (
                    row["sqlstate"],
                    row["authority_rejection_marker"],
                )
                == support.expected_rejection(
                    authority_id="current-source-bound-v1",
                    table_name=row["table_name"],
                    case_kind=case_kind,
                )
                for row in case_rows
            )


def test_v16_catalog_proves_exact_current_routine_and_acl_projection() -> None:
    support = _support()
    result = support.census_result()
    routine_rows = result["current_routine_catalog_tuple"]
    assert len(routine_rows) == 19
    assert all(row["owner"] == support.GUARD_OWNER_ROLE for row in routine_rows)
    assert all(
        row["security_definer"] == (row["function_kind"] == "TRIGGER_GUARD") for row in routine_rows
    )
    assert all(row["search_path"] == "pg_catalog, pg_temp" for row in routine_rows)
    assert all(
        len(row["raw_execute_acl_tuple"]) == 1
        and row["raw_execute_acl_tuple"][0]["grantee"] == support.GUARD_OWNER_ROLE
        and row["raw_execute_acl_tuple"][0]["grantor"] == support.GUARD_OWNER_ROLE
        and row["raw_execute_acl_tuple"][0]["privilege_type"] == "EXECUTE"
        and row["raw_execute_acl_tuple"][0]["is_grantable"] is False
        for row in routine_rows
    )
    assert result["guard_owner_column_acl_relation_count"] == 36
    assert result["guard_owner_column_acl_count"] == 366
    assert len(result["guard_owner_raw_attribute_acl_tuple"]) == 366
    assert result["application_direct_helper_execute_count"] == 0
    assert result["public_direct_helper_execute_count"] == 0
    assert len(result["guard_owner_raw_schema_acl_tuple"]) == 1
    assert {
        key: result["guard_owner_raw_schema_acl_tuple"][0][key]
        for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
    } == {
        "schema_name": "public",
        "grantee": support.GUARD_OWNER_ROLE,
        "privilege_type": "USAGE",
        "is_grantable": False,
    }


def test_v16_guard_owner_is_force_rls_bound_and_strictly_read_only() -> None:
    support = _support()
    result = support.census_result()
    posture = result["guard_owner_posture"]
    assert posture == {
        "role_name": support.GUARD_OWNER_ROLE,
        "can_login": False,
        "is_superuser": False,
        "can_create_db": False,
        "can_create_role": False,
        "inherits": False,
        "bypass_rls": False,
        "membership_count": 0,
        "owned_relation_count": 0,
        "owned_routine_count": 19,
        "schema_usage": True,
        "schema_create": False,
        "table_select_privilege_count": 0,
        "table_insert_privilege_count": 0,
        "table_update_privilege_count": 0,
        "table_delete_privilege_count": 0,
        "table_truncate_privilege_count": 0,
        "table_references_privilege_count": 0,
        "table_trigger_privilege_count": 0,
    }
    assert result["force_rls_parent_relation_count"] == 36
    proof = result["security_definer_guard_owner_rls_context_proof"]
    assert proof["proof_scope"] == "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS"
    assert proof["executed_as_role"] == support.GUARD_OWNER_ROLE
    assert proof["unset_tenant_guard_denial_count"] >= 1
    assert proof["cross_tenant_guard_denial_count"] >= 1
    assert proof["same_tenant_guard_acceptance_count"] >= 1


def test_v16_external_erasure_helper_edge_is_not_executable_by_app_or_public() -> None:
    support = _support()
    result = support.census_result()
    proof = result["external_erasure_helper_proof"]
    assert set(proof) == {
        "function_signature",
        "guard_owner_execute",
        "application_direct_execute",
        "public_direct_execute",
        "application_direct_call_denied",
        "ordinary_guard_call_result",
        "function_owner",
        "security_definer",
        "search_path_tuple",
        "raw_execute_acl_tuple",
    }
    assert proof["function_signature"] == "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
    assert proof["guard_owner_execute"] is True
    assert proof["application_direct_execute"] is False
    assert proof["public_direct_execute"] is False
    assert proof["application_direct_call_denied"] is True
    assert proof["ordinary_guard_call_result"] is False
    assert proof["function_owner"] == "idis_erasure_function_owner"
    assert proof["security_definer"] is True
    assert proof["search_path_tuple"] == ["pg_catalog", "pg_temp"]
    assert all(
        set(row)
        == {
            "grantee",
            "grantor",
            "privilege_type",
            "is_grantable",
        }
        for row in proof["raw_execute_acl_tuple"]
    )
    assert {
        (
            row["grantee"],
            row["grantor"],
            row["privilege_type"],
            row["is_grantable"],
        )
        for row in proof["raw_execute_acl_tuple"]
    } == {
        (
            proof["function_owner"],
            proof["function_owner"],
            "EXECUTE",
            False,
        ),
        (
            support.GUARD_OWNER_ROLE,
            proof["function_owner"],
            "EXECUTE",
            False,
        ),
    }


def test_v16_retains_four_production_constructor_roundtrips() -> None:
    support = _support()
    result = support.census_result()
    assert (
        tuple(
            (
                proof["schema_id"],
                proof["schema_version"],
                proof["constructor_qualname"],
                proof["durable_table"],
            )
            for proof in result["constructor_proof_tuple"]
        )
        == support.PRODUCTION_CONSTRUCTOR_SPECS
    )
    assert all(
        proof["original_payload_sha256"] == proof["reloaded_payload_sha256"]
        for proof in result["constructor_proof_tuple"]
    )


def test_v16_manifest_parity_and_admin_fixture_hash_calls_are_derived() -> None:
    support = _support()
    result = support.census_result()
    parity = result["manifest_parity_projection"]
    projections = tuple(
        tuple(tuple(row) for row in parity[field])
        for field in (
            "artifact_authority_target_tuple",
            "requested_authority_target_tuple",
            "live_catalog_authority_target_tuple",
            "measured_case_authority_target_tuple",
        )
    )
    assert len(set(projections)) == 1
    assert result["manifest_parity_sha256"] == support.digest(
        ["task7-v16-manifest-parity-v1", parity]
    )

    helper_calls = result["admin_fixture_helper_projection_tuple"]
    assert result["admin_fixture_helper_call_count"] == len(helper_calls)
    assert len(helper_calls) == 36
    assert all(
        row["phase"] == "COMMITTED_FIXTURE_SETUP"
        and row["function_signature"]
        == "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
        and row["case_kind"] in {"LEGAL", "DIRECT_SQL"}
        for row in helper_calls
    )
    assert {
        case_kind: sum(row["case_kind"] == case_kind for row in helper_calls)
        for case_kind in ("LEGAL", "DIRECT_SQL")
    } == {"LEGAL": 18, "DIRECT_SQL": 18}
    assert (
        len(
            {
                (
                    row["authority_id"],
                    row["table_name"],
                    row["guard_name"],
                    row["case_kind"],
                )
                for row in helper_calls
            }
        )
        == 36
    )


def test_v16_fixture_fields_fail_closed_instead_of_being_dropped() -> None:
    support = _support()
    module = support.runner()
    columns = {
        "alpha": {
            "alpha_id": ("uuid", False),
            "derived_hash": ("text", True),
        },
        "beta": {"beta_only": ("text", False)},
    }
    assert module.validate_fixture_values(
        columns,
        "alpha",
        {"alpha_id": "11111111-1111-1111-1111-111111111111"},
    )
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_FIXTURE_UNKNOWN_FIELD:alpha\.unknown$",
    ):
        module.validate_fixture_values(columns, "alpha", {"unknown": "not-a-column"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_FIXTURE_OTHER_TARGET_FIELD:alpha\.beta_only$",
    ):
        module.validate_fixture_values(columns, "alpha", {"beta_only": "wrong-target"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_FIXTURE_GENERATED_FIELD:alpha\.derived_hash$",
    ):
        module.validate_fixture_values(
            columns,
            "alpha",
            {"derived_hash": "computed-by-postgres"},
        )

    insert_events: list[tuple[str, str]] = []

    def record_insert(
        _connection: object,
        _column_map: object,
        table_name: str,
        values: dict[str, object],
        *,
        returning: tuple[str, ...] = (),
    ) -> dict[str, object]:
        insert_events.append((table_name, str(values["pipeline_contract_version"])))
        return {"returning": returning}

    for branch in ("ORDINARY", "TASK5_DEFERRED"):
        insert_events.clear()
        inserted = module._validated_historical_insert(
            branch=branch,
            original_insert=record_insert,
            connection=object(),
            column_map={},
            table_name="runs",
            values={"pipeline_contract_version": "full-strict-v16"},
            returning=("run_id",),
        )
        assert inserted == {"returning": ("run_id",)}
        assert insert_events == [("runs", "full-strict-v16")]

        insert_events.clear()
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_HISTORICAL_FIXTURE_VERSION_INVALID$",
        ):
            module._validated_historical_insert(
                branch=branch,
                original_insert=record_insert,
                connection=object(),
                column_map={},
                table_name="runs",
                values={"pipeline_contract_version": "full-strict-v11"},
                returning=(),
            )
        assert insert_events == []

    rejection_metrics = {
        "admin_fixture_helper_projection_tuple": (
            {
                "authority_id": "current-output-exact-one-v1",
                "table_name": "methodology_coverage_records",
                "guard_name": "ctrg_output",
                "case_kind": "DIRECT_SQL",
            },
        ),
    }
    assert module._validated_rejection_metrics(rejection_metrics) == rejection_metrics


def test_v16_package_seed_corrections_never_write_generated_columns() -> None:
    module = _support().runner()
    native = module.corrected_native_source_values(
        table_name="data_room_packages",
        tenant_id="11111111-1111-1111-1111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        root_id="33333333-3333-4333-8333-333333333333",
        corpus_id="44444444-4444-4444-8444-444444444444",
    )
    assert native["package_id"] == "44444444-4444-4444-8444-444444444444"
    assert native["status"] == "SEALED"
    assert "source_corpus_receipt_id" not in native

    package_file = module.corrected_native_source_values(
        table_name="data_room_package_files",
        tenant_id="11111111-1111-1111-1111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        root_id="33333333-3333-4333-8333-333333333333",
        corpus_id="44444444-4444-4444-8444-444444444444",
    )
    assert package_file["package_id"] == "44444444-4444-4444-8444-444444444444"
    assert package_file["sequence"] == 1
    assert "source_corpus_receipt_id" not in package_file

    unbound = module.corrected_unbound_source_values(table_name="data_room_packages")
    assert isinstance(unbound["package_id"], str)
    assert unbound["status"] == "OPEN"
    assert "source_corpus_receipt_id" not in unbound

    unbound_file = module.corrected_unbound_source_values(table_name="data_room_package_files")
    assert isinstance(unbound_file["file_entry_id"], str)
    assert isinstance(unbound_file["package_id"], str)
    assert unbound_file["sequence"] == 1
    assert unbound_file["file_status"] == "PENDING"
    assert unbound_file["support_status"] == "SUPPORTED"
    assert unbound_file["triage_status"] == "READY"
    assert "source_corpus_receipt_id" not in unbound_file

    graph = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "deal_id": "22222222-2222-4222-8222-222222222222",
        "run_id": "33333333-3333-4333-8333-333333333333",
        "output_object_id": "44444444-4444-4444-8444-444444444444",
    }
    artifact, _raw = _support().artifact_and_raw()
    discriminators = module.artifact_native_output_discriminators(artifact)
    assert discriminators == {
        "deterministic_calculations": "METHODOLOGY_CALCULATION",
        "muhasabah_records": "COURT_MUHASABAH",
        "vector_embeddings": "EXTRACT_RAG_EMBEDDING",
    }
    assert (
        module.corrected_native_output_values(
            table_name="deterministic_calculations",
            graph=graph,
            discriminator_by_table=discriminators,
        )["run_output_object_kind"]
        == "METHODOLOGY_CALCULATION"
    )
    assert (
        module.corrected_native_output_values(
            table_name="muhasabah_records",
            graph=graph,
            discriminator_by_table=discriminators,
        )["run_output_object_kind"]
        == "COURT_MUHASABAH"
    )
    assert (
        module.corrected_native_output_values(
            table_name="vector_embeddings",
            graph=graph,
            discriminator_by_table=discriminators,
        )["run_output_object_kind"]
        == "EXTRACT_RAG_EMBEDDING"
    )


def test_v16_component_digest_preimages_are_normalized_to_json_lists() -> None:
    module = _support().runner()
    raw_projection = (
        ("authority", "table", "trigger", "INSERT"),
        ("authority", "table", "trigger", "UPDATE"),
    )
    normalized = module._json_safe(raw_projection)
    assert normalized == [
        ["authority", "table", "trigger", "INSERT"],
        ["authority", "table", "trigger", "UPDATE"],
    ]
    assert module._digest(["task7-v16-trigger-event-coverage-v1", normalized]) == module._digest(
        [
            "task7-v16-trigger-event-coverage-v1",
            [
                ["authority", "table", "trigger", "INSERT"],
                ["authority", "table", "trigger", "UPDATE"],
            ],
        ]
    )


def test_v16_runner_signs_and_verifies_app_role_complete_receipt() -> None:
    support = _support()
    artifact, artifact_raw = support.artifact_and_raw()
    result = support.census_result()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    module = support.runner()
    synthetic_result = {
        **module._database_execution(result),
        "prospective_frozen_artifact_sha256": result["prospective_frozen_artifact_sha256"],
    }
    synthetic_result["complete_execution_sha256"] = module._digest(
        [
            "task7-v16-complete-execution-v1",
            {
                key: value
                for key, value in synthetic_result.items()
                if key != "complete_execution_sha256"
            },
        ]
    )
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.build_and_verify_signed_receipt(
            signer_id="task7-v16-test-census-signer-v1",
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=synthetic_result,
            private_key=private_key,
            trusted_public_key_raw=public_key_raw,
        )

    receipt = module.build_and_verify_signed_receipt(
        signer_id="task7-v16-test-census-signer-v1",
        artifact_raw=artifact_raw,
        artifact=artifact,
        result=result,
        private_key=private_key,
        trusted_public_key_raw=public_key_raw,
    )
    assert tuple(receipt) == (
        "schema_version",
        "signer_id",
        "payload",
        "payload_sha256",
        "public_key_sha256",
        "signature_base64",
    )
    assert tuple(receipt["payload"]) == (
        "schema_version",
        "result",
        "artifact_schema",
        "pipeline_contract_version",
        "candidate_artifact_sha256",
        "prospective_frozen_artifact_sha256",
        "database_execution",
        "census_result",
    )
    rendered = (json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    payload = module.verify_signed_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_key_raw,
    )
    execution = payload["database_execution"]
    assert payload["pipeline_contract_version"] == "full-strict-v16"
    assert execution["application_case_execution_count"] == 219
    assert execution["admin_case_execution_count"] == 0
    assert execution["current_routine_catalog_count"] == 19
    assert execution["guard_owner_column_acl_count"] == 366
    assert execution["unexpected_outcome_count"] == 0

    def recompute_result_digests(census_result: dict[str, object]) -> None:
        census_result["guard_acl_projection_sha256"] = module._digest(
            [
                "task7-v16-guard-acl-projection-v1",
                census_result["current_routine_catalog_tuple"],
                census_result["guard_owner_raw_attribute_acl_tuple"],
                census_result["guard_owner_raw_schema_acl_tuple"],
                census_result["guard_owner_posture"],
                census_result["external_erasure_helper_proof"],
            ]
        )
        census_result["complete_execution_sha256"] = module._digest(
            [
                "task7-v16-complete-execution-v1",
                {
                    key: value
                    for key, value in census_result.items()
                    if key != "complete_execution_sha256"
                },
            ]
        )

    def resign(tampered: dict[str, object]) -> bytes:
        payload = tampered["payload"]
        assert isinstance(payload, dict)
        census_result = payload["census_result"]
        assert isinstance(census_result, dict)
        recompute_result_digests(census_result)
        payload["database_execution"] = module._database_execution(census_result)
        tampered["payload_sha256"] = module._digest(payload)
        tampered["signature_base64"] = base64.b64encode(
            private_key.sign(
                module._canonical_bytes(
                    [
                        module._CENSUS_SIGNING_DOMAIN,
                        tampered["signer_id"],
                        tampered["payload_sha256"],
                        tampered["public_key_sha256"],
                        payload,
                    ]
                )
            )
        ).decode("ascii")
        return (json.dumps(tampered, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )

    builder_poison = copy.deepcopy(result)
    builder_poison["guard_owner_raw_attribute_acl_tuple"][0]["raw_attacl"] = (
        "_task7_v15_builder_poison"
    )
    recompute_result_digests(builder_poison)
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.build_and_verify_signed_receipt(
            signer_id="task7-v16-test-census-signer-v1",
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=builder_poison,
            private_key=private_key,
            trusted_public_key_raw=public_key_raw,
        )

    for rejected_token in (
        "V11",
        "task7-v14-receipt",
        "_task7_v15_receipt",
    ):
        provenance_tamper = copy.deepcopy(receipt)
        tampered_result = provenance_tamper["payload"]["census_result"]
        tampered_result["guard_owner_raw_attribute_acl_tuple"][0]["raw_attacl"] = rejected_token
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
        ):
            module.verify_signed_receipt(
                receipt_raw=resign(provenance_tamper),
                artifact_raw=artifact_raw,
                trusted_public_key_raw=public_key_raw,
            )

    owner_tamper = copy.deepcopy(receipt)
    owner_proof = owner_tamper["payload"]["census_result"]["external_erasure_helper_proof"]
    original_owner = owner_proof["function_owner"]
    owner_proof["function_owner"] = "task7_fabricated_erasure_owner"
    for acl_row in owner_proof["raw_execute_acl_tuple"]:
        if acl_row["grantee"] == original_owner:
            acl_row["grantee"] = owner_proof["function_owner"]
        if acl_row["grantor"] == original_owner:
            acl_row["grantor"] = owner_proof["function_owner"]
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=resign(owner_tamper),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    for field, invalid_value in (
        ("security_definer", False),
        ("search_path_tuple", ["pg_catalog"]),
        ("application_direct_call_denied", False),
    ):
        helper_tamper = copy.deepcopy(receipt)
        helper_tamper["payload"]["census_result"]["external_erasure_helper_proof"][field] = (
            invalid_value
        )
        with pytest.raises(
            module.CensusError,
            match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
        ):
            module.verify_signed_receipt(
                receipt_raw=resign(helper_tamper),
                artifact_raw=artifact_raw,
                trusted_public_key_raw=public_key_raw,
            )

    unknown_acl_field = copy.deepcopy(receipt)
    unknown_acl_field["payload"]["census_result"]["external_erasure_helper_proof"][
        "raw_execute_acl_tuple"
    ][0]["unknown_nested_acl_field"] = "MASKED"
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=resign(unknown_acl_field),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    fabricated_nested = copy.deepcopy(receipt)
    fabricated_result = fabricated_nested["payload"]["census_result"]
    rejected = next(
        row for row in fabricated_result["case_outcome_tuple"] if row["case_kind"] == "ILLEGAL"
    )
    rejected["authority_rejection_marker"] = "FABRICATED_AUTHORITY_MARKER"
    fabricated_result["case_result_sha256"] = module._digest(
        [
            "task7-v16-current-guard-case-results-v1",
            fabricated_result["case_outcome_tuple"],
        ]
    )
    fabricated_result["complete_execution_sha256"] = module._digest(
        [
            "task7-v16-complete-execution-v1",
            {
                key: value
                for key, value in fabricated_result.items()
                if key != "complete_execution_sha256"
            },
        ]
    )
    fabricated_nested["payload"]["database_execution"] = module._database_execution(
        fabricated_result
    )
    fabricated_nested["payload_sha256"] = module._digest(fabricated_nested["payload"])
    fabricated_nested["signature_base64"] = base64.b64encode(
        private_key.sign(
            module._canonical_bytes(
                [
                    module._CENSUS_SIGNING_DOMAIN,
                    fabricated_nested["signer_id"],
                    fabricated_nested["payload_sha256"],
                    fabricated_nested["public_key_sha256"],
                    fabricated_nested["payload"],
                ]
            )
        )
    ).decode("ascii")
    fabricated_nested_raw = (
        json.dumps(
            fabricated_nested,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=fabricated_nested_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    forged = copy.deepcopy(receipt)
    forged["payload"]["database_execution"]["application_case_execution_count"] = 218
    forged["payload_sha256"] = module._digest(forged["payload"])
    forged["signature_base64"] = base64.b64encode(
        private_key.sign(
            module._canonical_bytes(
                [
                    module._CENSUS_SIGNING_DOMAIN,
                    forged["signer_id"],
                    forged["payload_sha256"],
                    forged["public_key_sha256"],
                    forged["payload"],
                ]
            )
        )
    ).decode("ascii")
    forged_raw = (json.dumps(forged, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=forged_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    dummy_hashes = copy.deepcopy(receipt)
    for key in tuple(dummy_hashes["payload"]["database_execution"]):
        if key.endswith("_sha256"):
            dummy_hashes["payload"]["database_execution"][key] = "0" * 64
    dummy_hashes["payload_sha256"] = module._digest(dummy_hashes["payload"])
    dummy_hashes["signature_base64"] = base64.b64encode(
        private_key.sign(
            module._canonical_bytes(
                [
                    module._CENSUS_SIGNING_DOMAIN,
                    dummy_hashes["signer_id"],
                    dummy_hashes["payload_sha256"],
                    dummy_hashes["public_key_sha256"],
                    dummy_hashes["payload"],
                ]
            )
        )
    ).decode("ascii")
    dummy_hashes_raw = (
        json.dumps(
            dummy_hashes,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=dummy_hashes_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw + b" ",
            trusted_public_key_raw=public_key_raw,
        )

    wrong_public_key_raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=wrong_public_key_raw,
        )

    signature_tamper = copy.deepcopy(receipt)
    signature = bytearray(base64.b64decode(signature_tamper["signature_base64"]))
    signature[0] ^= 1
    signature_tamper["signature_base64"] = base64.b64encode(signature).decode("ascii")
    signature_tamper_raw = (
        json.dumps(
            signature_tamper,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=signature_tamper_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    blank_signer = copy.deepcopy(receipt)
    blank_signer["signer_id"] = " "
    blank_signer["signature_base64"] = base64.b64encode(
        private_key.sign(
            module._canonical_bytes(
                [
                    module._CENSUS_SIGNING_DOMAIN,
                    blank_signer["signer_id"],
                    blank_signer["payload_sha256"],
                    blank_signer["public_key_sha256"],
                    blank_signer["payload"],
                ]
            )
        )
    ).decode("ascii")
    blank_signer_raw = (
        json.dumps(blank_signer, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V16_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=blank_signer_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )
