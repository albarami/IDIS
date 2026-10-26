"""Admission gate for the full-strict-v13 application-role PostgreSQL census."""

from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT_PATH = _REPO_ROOT / "tests" / "slice100_task7" / "_v13_current_guard_census_support.py"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v13_current_guard_census.py"
_SCAFFOLD_PATH = _REPO_ROOT / "scripts" / "_task7_v13_census_scaffold.py"


@cache
def _support() -> ModuleType:
    assert _SUPPORT_PATH.is_file(), "TASK7_V13_APP_ROLE_CENSUS_SUPPORT_MISSING"
    spec = importlib.util.spec_from_file_location(
        "_task7_v13_current_guard_census_test_support",
        _SUPPORT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v13_app_role_census_lane_exists_as_new_versioned_files() -> None:
    assert _SUPPORT_PATH.is_file(), "TASK7_V13_APP_ROLE_CENSUS_SUPPORT_MISSING"
    assert _RUNNER_PATH.is_file(), "TASK7_V13_APP_ROLE_CENSUS_RUNNER_MISSING"
    assert _SCAFFOLD_PATH.is_file(), "TASK7_V13_APP_ROLE_CENSUS_SCAFFOLD_MISSING"


def test_v13_artifact_declares_exact_guard_acl_authority() -> None:
    support = _support()
    artifact, _raw = support.artifact_and_raw()
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


def test_v13_trigger_inventory_is_exactly_73_targets_and_192_events() -> None:
    support = _support()
    assert len(support.EXPECTED_ORDERED_TARGETS) == 73
    inventory = support.expected_trigger_event_inventory()
    assert len(inventory) == 192
    assert len(set(inventory)) == 192


def test_v13_census_executes_every_case_as_generated_application_role() -> None:
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


def test_v13_catalog_proves_exact_current_routine_and_acl_projection() -> None:
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


def test_v13_guard_owner_is_force_rls_bound_and_strictly_read_only() -> None:
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


def test_v13_external_erasure_helper_edge_is_not_executable_by_app_or_public() -> None:
    support = _support()
    result = support.census_result()
    proof = result["external_erasure_helper_proof"]
    assert proof["function_signature"] == "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
    assert proof["guard_owner_execute"] is True
    assert proof["application_direct_execute"] is False
    assert proof["public_direct_execute"] is False
    assert proof["ordinary_guard_call_result"] is False
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


def test_v13_retains_four_production_constructor_roundtrips() -> None:
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


def test_v13_manifest_parity_and_admin_fixture_hash_calls_are_derived() -> None:
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
        ["task7-v13-manifest-parity-v1", parity]
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


def test_v13_fixture_fields_fail_closed_instead_of_being_dropped() -> None:
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
        match=r"^TASK7_V13_CENSUS_FIXTURE_UNKNOWN_FIELD:alpha\.unknown$",
    ):
        module.validate_fixture_values(columns, "alpha", {"unknown": "not-a-column"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V13_CENSUS_FIXTURE_OTHER_TARGET_FIELD:alpha\.beta_only$",
    ):
        module.validate_fixture_values(columns, "alpha", {"beta_only": "wrong-target"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V13_CENSUS_FIXTURE_GENERATED_FIELD:alpha\.derived_hash$",
    ):
        module.validate_fixture_values(
            columns,
            "alpha",
            {"derived_hash": "computed-by-postgres"},
        )


def test_v13_package_seed_corrections_never_write_generated_columns() -> None:
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


def test_v13_component_digest_preimages_are_normalized_to_json_lists() -> None:
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
    assert module._digest(["task7-v13-trigger-event-coverage-v1", normalized]) == module._digest(
        [
            "task7-v13-trigger-event-coverage-v1",
            [
                ["authority", "table", "trigger", "INSERT"],
                ["authority", "table", "trigger", "UPDATE"],
            ],
        ]
    )


def test_v13_runner_signs_and_verifies_app_role_complete_receipt() -> None:
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
            "task7-v13-complete-execution-v1",
            {
                key: value
                for key, value in synthetic_result.items()
                if key != "complete_execution_sha256"
            },
        ]
    )
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
    ):
        module.build_and_verify_signed_receipt(
            signer_id="task7-v13-test-census-signer-v1",
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=synthetic_result,
            private_key=private_key,
            trusted_public_key_raw=public_key_raw,
        )

    receipt = module.build_and_verify_signed_receipt(
        signer_id="task7-v13-test-census-signer-v1",
        artifact_raw=artifact_raw,
        artifact=artifact,
        result=result,
        private_key=private_key,
        trusted_public_key_raw=public_key_raw,
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
    assert payload["pipeline_contract_version"] == "full-strict-v13"
    assert execution["application_case_execution_count"] == 219
    assert execution["admin_case_execution_count"] == 0
    assert execution["current_routine_catalog_count"] == 19
    assert execution["guard_owner_column_acl_count"] == 366
    assert execution["unexpected_outcome_count"] == 0

    fabricated_nested = copy.deepcopy(receipt)
    fabricated_result = fabricated_nested["payload"]["census_result"]
    rejected = next(
        row for row in fabricated_result["case_outcome_tuple"] if row["case_kind"] == "ILLEGAL"
    )
    rejected["authority_rejection_marker"] = "FABRICATED_AUTHORITY_MARKER"
    fabricated_result["case_result_sha256"] = module._digest(
        [
            "task7-v13-current-guard-case-results-v1",
            fabricated_result["case_outcome_tuple"],
        ]
    )
    fabricated_result["complete_execution_sha256"] = module._digest(
        [
            "task7-v13-complete-execution-v1",
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
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
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
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
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
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=dummy_hashes_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
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
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
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
        match=r"^TASK7_V13_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=signature_tamper_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )
