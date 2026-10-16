"""Admission gate for the full-strict-v9 current-guard PostgreSQL census."""

from __future__ import annotations

import importlib

from tests.slice100_task7._v9_current_guard_census_support import (
    BASE_SCHEMA_REVISION,
    CASE_KINDS,
    EXPECTED_AUTHORITY_TARGETS,
    EXPECTED_ORDERED_TARGETS,
    HARNESS_IDENTITY,
    INSTALLATION_MODE,
    PRODUCTION_CONSTRUCTOR_SPECS,
    assert_complete_census_result,
    assert_reviewed_target_and_locator_closure,
    census_result,
    current_authority_rows,
    expected_locator_nodes,
)


def test_v9_artifact_names_exactly_33_collectable_historical_nodes() -> None:
    """The frozen v8 path remains executable for current v9 SQL evidence."""

    assert_reviewed_target_and_locator_closure()
    module = importlib.import_module(
        "tests.slice100_task7.test_full_strict_v8_current_guard_postgres_census_red"
    )
    for locator in expected_locator_nodes():
        _path, separator, node_id = locator.partition("::")
        assert separator == "::"
        assert "[" not in node_id
        assert callable(getattr(module, node_id, None)), locator


def test_v9_current_guard_target_inventory_is_exact_and_disjoint() -> None:
    """The eleven authorities cover each reviewed current target once."""

    rows = current_authority_rows()
    assert tuple(row["authority_id"] for row in rows) == tuple(EXPECTED_AUTHORITY_TARGETS)
    assert len(EXPECTED_ORDERED_TARGETS) == 73
    assert len(set(EXPECTED_ORDERED_TARGETS)) == 73
    assert (
        tuple(
            (
                row["authority_id"],
                target[0],
                target[1],
            )
            for row in rows
            for target in row["target_tuple"]
        )
        == EXPECTED_ORDERED_TARGETS
    )


def test_v9_pre_fixture_census_uses_0037_artifact_sql_harness() -> None:
    """The census is pre-migration evidence and never claims 0038 success."""

    result = census_result()
    assert result["harness_identity"] == HARNESS_IDENTITY
    assert result["base_schema_revision"] == BASE_SCHEMA_REVISION
    assert result["installation_mode"] == INSTALLATION_MODE
    assert "migration_revision" not in result


def test_v9_pre_fixture_census_executes_every_target_in_all_three_modes() -> None:
    """All 73 legal, illegal, and direct-SQL cases execute exactly once."""

    result = census_result()
    assert_complete_census_result(result)
    assert tuple(
        (kind, authority_id, table_name, guard_name)
        for kind in CASE_KINDS
        for authority_id, table_name, guard_name in EXPECTED_ORDERED_TARGETS
    ) == tuple(
        (
            row["case_kind"],
            row["authority_id"],
            row["table_name"],
            row["guard_name"],
        )
        for row in result["case_outcome_tuple"]
    )
    assert result["legal_execution_count"] == 73
    assert result["illegal_rejection_count"] == 73
    assert result["direct_sql_rejection_count"] == 73
    assert result["unexpected_outcome_count"] == 0


def test_v9_pre_fixture_census_proves_four_production_constructor_roundtrips() -> None:
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


def test_v9_pre_fixture_census_binds_candidate_and_prospective_frozen_bytes() -> None:
    """The signed evidence cannot be replayed for different v9 bytes."""

    result = census_result()
    assert result["candidate_append_frozen"] is False
    assert result["candidate_artifact_sha256"] != result["prospective_frozen_artifact_sha256"]
    assert result["catalog_projection_sha256"]
    assert result["case_result_sha256"]
    assert result["constructor_roundtrip_sha256"]
    assert result["manifest_parity_sha256"]
