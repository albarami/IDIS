"""Admission gate for the full-strict-v11 pre-freeze PostgreSQL census."""

from __future__ import annotations

import base64
import copy
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.slice100_task7._v11_current_guard_census_support import (
    BASE_SCHEMA_REVISION,
    CASE_KINDS,
    EXPECTED_ORDERED_TARGETS,
    HARNESS_IDENTITY,
    PRODUCTION_CONSTRUCTOR_SPECS,
    TASK6_OUTPUT_ROOT_UPDATE_TARGETS,
    TASK6_WRITE_CONTEXT_GUCS,
    artifact_and_raw,
    assert_complete_census_result,
    census_result,
    expected_case_coordinates,
    expected_trigger_event_inventory,
    expected_trigger_projection,
    runner,
)


def test_v11_artifact_declares_exact_73_target_and_192_event_inventory() -> None:
    """The reviewed trigger DDL is the sole event-inventory authority."""

    assert len(EXPECTED_ORDERED_TARGETS) == 73
    inventory = expected_trigger_event_inventory()
    assert len(inventory) == 192
    assert len(set(inventory)) == 192


def test_v11_artifact_trigger_statement_cardinality_preserves_retained_guards() -> None:
    """Rows 17-25 own target DDL; retained Task-5/Task-6 rows own no trigger DDL."""

    artifact, _raw = artifact_and_raw()
    rows = artifact["sql_authority_registry"]["rows"][17:]
    for row in rows:
        statements = [
            bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
        ]
        retained = row["authority_id"] in {
            "current-task5-coverage-replacement-v1",
            "current-task6-immutable-replacement-v1",
        }
        for table_name, guard_name in row["target_tuple"]:
            matches = [
                statement
                for statement in statements
                if f"TRIGGER {guard_name} " in statement
                and f" ON public.{table_name} " in statement
            ]
            assert len(matches) == (0 if retained else 1)


def test_v11_runner_rejects_unknown_other_target_and_generated_fixture_fields() -> None:
    """Fixture fields cannot disappear through the historical relaxed insert seam."""

    module = runner()
    columns = {
        "alpha": {
            "alpha_id": ("uuid", False),
            "derived_hash": ("text", True),
        },
        "beta": {"beta_only": ("text", False)},
    }
    validate = module.validate_fixture_values
    assert validate(columns, "alpha", {"alpha_id": "11111111-1111-1111-1111-111111111111"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V11_CENSUS_FIXTURE_UNKNOWN_FIELD:alpha\.unknown$",
    ):
        validate(columns, "alpha", {"unknown": "not-a-column"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V11_CENSUS_FIXTURE_OTHER_TARGET_FIELD:alpha\.beta_only$",
    ):
        validate(columns, "alpha", {"beta_only": "wrong-target"})
    with pytest.raises(
        module.CensusError,
        match=r"^TASK7_V11_CENSUS_FIXTURE_GENERATED_FIELD:alpha\.derived_hash$",
    ):
        validate(columns, "alpha", {"derived_hash": "computed-by-postgres"})


def test_v11_pre_fixture_census_uses_only_0037_artifact_sql() -> None:
    result = census_result()
    assert result["base_schema_revision"] == BASE_SCHEMA_REVISION
    assert result["harness_identity"] == HARNESS_IDENTITY
    assert "migration_revision" not in result


def test_v11_census_executes_219_outcomes_covering_all_192_events() -> None:
    result = census_result()
    assert_complete_census_result(result)
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
        == expected_case_coordinates()
    )
    assert tuple(CASE_KINDS) == ("LEGAL", "ILLEGAL", "DIRECT_SQL")
    assert tuple(tuple(row) for row in result["pg_trigger_projection_tuple"]) == (
        expected_trigger_projection()
    )


def test_v11_census_exercises_the_named_corrected_composition_branches() -> None:
    result = census_result()
    outcomes = result["case_outcome_tuple"]

    def scenario(authority: str, table: str, case: str) -> str:
        return next(
            str(row["scenario_id"])
            for row in outcomes
            if row["authority_id"] == authority
            and row["table_name"] == table
            and row["case_kind"] == case
        )

    assert (
        scenario(
            "current-append-only-lease-v1",
            "run_expected_consumer_edges",
            "LEGAL",
        )
        == "EXPECTED_EDGE_QUEUED_ADMISSION"
    )
    assert (
        scenario("current-source-bound-v1", "data_room_packages", "LEGAL")
        == "PACKAGE_OPEN_TO_SEALED_BINDING"
    )
    assert {
        scenario(
            "current-task6-immutable-replacement-v1",
            table,
            "LEGAL",
        )
        for table in TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    } == {"TASK6_OUTPUT_ROOT_ATTACH_UPDATE"}
    assert (
        scenario(
            "current-task6-immutable-replacement-v1",
            "methodology_extraction_task_spans",
            "ILLEGAL",
        )
        == "TASK6_SPAN_IMMUTABLE_UPDATE"
    )
    assert tuple(result["task6_write_context_guc_tuple"]) == TASK6_WRITE_CONTEXT_GUCS
    assert result["strict_input_binding_queued_admission_count"] == 1
    assert result["methodology_commercial_question_count"] == 21
    assert result["methodology_financial_question_count"] == 79
    assert result["methodology_coverage_count"] == 100


def test_v11_census_retains_four_production_constructor_roundtrips() -> None:
    result = census_result()
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
        == PRODUCTION_CONSTRUCTOR_SPECS
    )
    assert all(
        proof["original_payload_sha256"] == proof["reloaded_payload_sha256"]
        for proof in result["constructor_proof_tuple"]
    )


def test_v11_runner_signs_and_verifies_event_complete_receipt() -> None:
    artifact, artifact_raw = artifact_and_raw()
    result = census_result()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    module = runner()
    receipt = module.build_and_verify_signed_receipt(
        signer_id="task7-v11-test-census-signer-v1",
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
    assert payload["pipeline_contract_version"] == "full-strict-v11"
    assert execution["current_guard_target_count"] == 73
    assert execution["current_guard_target_event_count"] == 192
    assert execution["case_outcome_count"] == 219
    assert execution["legal_execution_count"] == 73
    assert execution["illegal_rejection_count"] == 73
    assert execution["direct_sql_rejection_count"] == 73
    assert execution["expected_edge_queued_admission_count"] == 1
    assert execution["package_open_to_sealed_binding_count"] == 1
    assert execution["task6_output_root_update_acceptance_count"] == 2
    assert execution["task6_task_span_update_rejection_count"] == 1
    assert execution["pg_trigger_inventory_sha256"] == result["pg_trigger_inventory_sha256"]
    assert execution["trigger_event_coverage_sha256"] == result["trigger_event_coverage_sha256"]
    assert execution["seed_proof_sha256"] == result["seed_proof_sha256"]
    assert execution["complete_execution_sha256"] == result["complete_execution_sha256"]
    assert "v10" not in json.dumps(receipt, sort_keys=True).lower()

    forged = copy.deepcopy(receipt)
    forged["payload"]["database_execution"]["current_guard_target_event_count"] = 191
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
        match="^TASK7_V11_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=forged_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )

    with pytest.raises(
        module.CensusError,
        match="^TASK7_V11_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw + b" ",
            trusted_public_key_raw=public_key_raw,
        )

    wrong_private_key = Ed25519PrivateKey.generate()
    wrong_public_key_raw = wrong_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(
        module.CensusError,
        match="^TASK7_V11_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=rendered,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=wrong_public_key_raw,
        )

    signature_tamper = bytearray(rendered)
    signature_tamper[-10] ^= 1
    with pytest.raises(
        module.CensusError,
        match="^TASK7_V11_CENSUS_RECEIPT_INVALID$",
    ):
        module.verify_signed_receipt(
            receipt_raw=bytes(signature_tamper),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )
