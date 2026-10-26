"""Admission gate for the full-strict-v10 pre-freeze PostgreSQL census."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.slice100_task7._v10_current_guard_census_support import (
    BASE_SCHEMA_REVISION,
    CASE_KINDS,
    EXPECTED_AUTHORITY_TARGETS,
    EXPECTED_ORDERED_TARGETS,
    HARNESS_IDENTITY,
    INSTALLATION_MODE,
    PRODUCTION_CONSTRUCTOR_SPECS,
    _runner,
    artifact_and_raw,
    assert_complete_census_result,
    assert_reviewed_target_and_locator_closure,
    census_result,
    current_authority_rows,
    expected_case_coordinates,
    expected_locator_nodes,
)


def test_v10_scaffold_rejects_historical_source_drift_before_import() -> None:
    """The signed census cannot silently execute changed scaffold mechanics."""

    scaffold = importlib.import_module("scripts._task7_v10_census_scaffold")
    scaffold_file = getattr(scaffold, "__file__", None)
    assert isinstance(scaffold_file, str)
    historical = Path(scaffold_file).with_name("_task7_v9_census_scaffold.py").read_bytes()
    scaffold.verify_historical_scaffold_bytes(historical)
    with pytest.raises(
        RuntimeError,
        match="^TASK7_V10_HISTORICAL_CENSUS_SCAFFOLD_DRIFT$",
    ):
        scaffold.verify_historical_scaffold_bytes(historical + b"\n# tampered")


def test_v10_artifact_names_exactly_33_collectable_historical_nodes() -> None:
    """The reviewed historical locators remain callable for current v10 SQL."""

    assert_reviewed_target_and_locator_closure()
    module = importlib.import_module(
        "tests.slice100_task7.test_full_strict_v8_current_guard_postgres_census_red"
    )
    for locator in expected_locator_nodes():
        _path, separator, node_id = locator.partition("::")
        assert separator == "::"
        assert "[" not in node_id
        assert callable(getattr(module, node_id, None)), locator


def test_v10_current_guard_target_inventory_is_exact_and_disjoint() -> None:
    """The eleven authorities cover each reviewed current target once."""

    rows = current_authority_rows()
    assert tuple(row["authority_id"] for row in rows) == tuple(EXPECTED_AUTHORITY_TARGETS)
    assert len(EXPECTED_ORDERED_TARGETS) == 73
    assert len(set(EXPECTED_ORDERED_TARGETS)) == 73
    assert (
        tuple(
            (row["authority_id"], target[0], target[1])
            for row in rows
            for target in row["target_tuple"]
        )
        == EXPECTED_ORDERED_TARGETS
    )


def test_v10_pre_fixture_census_uses_0037_artifact_sql_harness() -> None:
    """The census is pre-migration evidence and never claims 0038 success."""

    result = census_result()
    assert result["harness_identity"] == HARNESS_IDENTITY
    assert result["base_schema_revision"] == BASE_SCHEMA_REVISION
    assert result["installation_mode"] == INSTALLATION_MODE
    assert "migration_revision" not in result


def test_v10_pre_fixture_census_executes_complete_73_by_3_matrix() -> None:
    """All 73 legal, illegal, and direct-SQL cases execute exactly once."""

    result = census_result()
    assert_complete_census_result(result)
    assert (
        tuple(
            (
                row["case_kind"],
                row["authority_id"],
                row["table_name"],
                row["guard_name"],
            )
            for row in result["case_outcome_tuple"]
        )
        == expected_case_coordinates()
    )
    assert tuple(CASE_KINDS) == ("LEGAL", "ILLEGAL", "DIRECT_SQL")
    assert result["legal_execution_count"] == 73
    assert result["illegal_rejection_count"] == 73
    assert result["direct_sql_rejection_count"] == 73
    assert result["unexpected_outcome_count"] == 0


def test_v10_pre_fixture_census_proves_four_production_constructor_roundtrips() -> None:
    """The four closed schemas use real constructors and durable tables."""

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
    assert result["constructor_schema_proof_count"] == 4
    assert all(
        proof["original_payload_sha256"] == proof["reloaded_payload_sha256"]
        for proof in result["constructor_proof_tuple"]
    )


def test_v10_runner_issues_and_verifies_artifact_bound_signed_receipt() -> None:
    """The runner signs v10 evidence that the v10 generator verifies."""

    artifact, artifact_raw = artifact_and_raw()
    result = census_result()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    runner = _runner()
    receipt = runner.build_and_verify_signed_receipt(
        signer_id="task7-v10-test-census-signer-v1",
        artifact_raw=artifact_raw,
        artifact=artifact,
        result=result,
        private_key=private_key,
        trusted_public_key_raw=public_key_raw,
    )
    rendered = (json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    payload = runner.verify_signed_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_key_raw,
    )
    assert payload["pipeline_contract_version"] == "full-strict-v10"
    assert payload["candidate_artifact_sha256"] == result["candidate_artifact_sha256"]
    assert (
        payload["prospective_frozen_artifact_sha256"]
        == result["prospective_frozen_artifact_sha256"]
    )
    assert payload["database_execution"]["current_guard_target_count"] == 73
    assert "v9" not in json.dumps(receipt, sort_keys=True).lower()
