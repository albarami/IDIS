"""RED catalog contract for the reviewed Task-7 migration 0038.

This module intentionally describes the approved database boundary before the
migration exists.  It reads the frozen reviewed preimage only to cross-check
that the hand-auditable migration constants still name the same authorities.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from tests.slice100_task6.test_migration_0037_red import (
    EXPECTED_TENANT_TABLES as REVISION_0037_TABLES,
)

import idis.persistence.migrations as migrations_pkg
from idis.runtime import dependency_probes

TASK7_TABLES = (
    "deliverable_manifest_references",
    "document_parse_attempts",
    "run_applicability_decisions",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "run_input_bindings",
    "run_output_objects",
    "run_step_outputs",
    "source_lineage_objects",
)
EXPECTED_TENANT_TABLES = tuple(sorted((*REVISION_0037_TABLES, *TASK7_TABLES)))
SOURCE_NATIVE_TABLES = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
)
ROOT_BEARING_CURRENT_TABLES = (
    "methodology_coverage_records",
    "methodology_extraction_tasks",
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
    "methodology_result_claim_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
    "deterministic_calculations",
    "vector_embeddings",
    "evidence_trust_findings",
    "muhasabah_records",
    "validated_evidence_packages",
    "debate_sessions",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "deliverables",
)
ALTERED_CURRENT_TABLES = tuple(
    sorted(
        (
            *ROOT_BEARING_CURRENT_TABLES,
            "calc_sanads",
            "data_room_package_files",
            "data_room_packages",
            "document_artifacts",
            "document_spans",
            "documents",
            "run_steps",
            "runs",
        )
    )
)

MIGRATIONS_DIR = Path(migrations_pkg.__file__).parent / "versions"
ARTIFACT_PATH = Path("contracts/full_strict_v21_reviewed_preimage_v1.json")
FIXTURE_PATH = Path("tests/slice100_task7/fixtures/full_strict_v21_contract_hash_v1.json")


def migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0038_*.py"))
    assert len(matches) == 1, "Task 7 requires exactly one linear migration 0038"
    return matches[0]


def load_migration() -> Any:
    path = migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task7_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_source() -> str:
    return re.sub(r"\s+", " ", migration_path().read_text(encoding="utf-8")).lower()


def _reviewed_current_ddl_tables() -> tuple[str, ...]:
    artifact = json.loads(ARTIFACT_PATH.read_bytes())
    return tuple(row["table_name"] for row in artifact["current_ddl_registry"]["rows"])


def _reviewed_task7_named_ddl() -> dict[str, frozenset[str]]:
    artifact = json.loads(ARTIFACT_PATH.read_bytes())
    result: dict[str, frozenset[str]] = {}
    for row in artifact["current_ddl_registry"]["rows"]:
        table = row["table_name"]
        if table not in TASK7_TABLES:
            continue
        names: set[str] = set()
        for tuple_name in (
            "check_tuple",
            "foreign_key_tuple",
            "unique_tuple",
            "index_tuple",
        ):
            for authority in row[tuple_name]:
                match = re.match(r"((?:pk|ck|fk|uq|ux|ix)_task7_[a-z0-9_]+)", authority)
                if match is not None:
                    names.add(match.group(1))
        result[table] = frozenset(names)
    return result


def test_0038_is_linear_and_declares_exactly_the_nine_reviewed_tables() -> None:
    module = load_migration()

    assert module.revision == "0038"
    assert module.down_revision == "0037"
    assert tuple(sorted(module.TASK7_TABLES_V1)) == TASK7_TABLES
    source = normalized_source()
    assert source.count("create table") == 9
    for table in TASK7_TABLES:
        assert f"create table {table}" in source


def test_0038_embeds_the_frozen_contract_and_reviewed_ddl_registry() -> None:
    module = load_migration()
    fixture = json.loads(FIXTURE_PATH.read_bytes())

    assert fixture["FULL_STRICT_V21_CONTRACT_HASH"] == module.FULL_STRICT_V21_CONTRACT_HASH
    assert fixture["component_hashes"]["task7_ddl_registry_hash"] == module.TASK7_DDL_REGISTRY_HASH
    assert tuple(module.REVIEWED_CURRENT_DDL_TABLES_V1) == _reviewed_current_ddl_tables()


def test_0038_consumes_every_frozen_v21_current_ddl_tuple_exactly() -> None:
    module = load_migration()
    artifact = json.loads(ARTIFACT_PATH.read_bytes())

    assert set(module._DDL_ROWS) == {
        str(row["table_name"]) for row in artifact["current_ddl_registry"]["rows"]
    }
    for row in artifact["current_ddl_registry"]["rows"]:
        installed = module._DDL_ROWS[str(row["table_name"])]
        for tuple_name in (
            "column_tuple",
            "check_tuple",
            "foreign_key_tuple",
            "unique_tuple",
            "index_tuple",
            "rls_tuple",
        ):
            assert tuple(row[tuple_name]) == tuple(installed[tuple_name])
        assert row["erasure_ordinal"] == installed["erasure_ordinal"]


def test_0038_uses_literal_auditable_sql_not_a_runtime_decoded_artifact_blob() -> None:
    """The reviewed artifact is translated offline; migration execution never interprets it."""
    source = normalized_source()

    for forbidden in (
        "_reviewed_migration_bytes_b85",
        "b85decode",
        "zlib.decompress",
        "runtime_artifact",
    ):
        assert forbidden not in source


def test_current_ddl_census_is_exactly_nine_new_plus_twenty_five_altered_tables() -> None:
    migration_path()
    reviewed = frozenset(_reviewed_current_ddl_tables())

    assert len(TASK7_TABLES) == 9
    assert len(ALTERED_CURRENT_TABLES) == 25
    assert reviewed == frozenset((*TASK7_TABLES, *ALTERED_CURRENT_TABLES))


def test_each_new_table_materializes_every_reviewed_constraint_and_index_name() -> None:
    source = normalized_source()
    reviewed = _reviewed_task7_named_ddl()

    assert frozenset(reviewed) == frozenset(TASK7_TABLES)
    assert all(reviewed.values())
    for table, authority_names in reviewed.items():
        for name in authority_names:
            assert name in source, f"0038 omits reviewed {table} authority {name}"


def test_source_tables_have_complete_binding_columns_and_post_backfill_not_null_state() -> None:
    source = normalized_source()

    for table in SOURCE_NATIVE_TABLES:
        assert f"alter table {table}" in source
        assert "source_binding_state" in source
        assert f"ck_task7_{table}_binding_state" in source
    assert source.count("alter column source_binding_state set not null") == len(
        SOURCE_NATIVE_TABLES
    )
    for required in (
        "source_lineage_object_id",
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
        "parse_attempt_id",
        "write_origin",
        "backfill",
        "runtime",
    ):
        assert required in source


def test_source_membership_replaces_only_the_two_reviewed_cascades() -> None:
    source = normalized_source()

    for required in (
        "fk_task7_package_files_package",
        "fk_task7_package_files_document",
        "fk_task7_documents_artifact",
        "fk_task7_spans_document",
        "fk_task7_spans_deal",
        "fk_task7_spans_parse_attempt",
        "ux_task7_package_files_doc_id_bound",
        "ux_task7_package_files_document_id_bound",
    ):
        assert required in source
    assert "on delete no action" in source
    assert source.count("on delete cascade") == 2
    assert "data_room_package_files_package_id_fkey" in source
    assert "document_spans_document_id_fkey" in source


def test_all_current_native_authorities_receive_exact_root_shape_and_supporting_index() -> None:
    source = normalized_source()

    for table in ROOT_BEARING_CURRENT_TABLES:
        assert f"ck_task7_{table}_" in source
        assert f"fk_task7_{table}_output_root" in source
        assert f"ix_task7_{table}_output_root_scope" in source
    for required in (
        "run_output_object_id",
        "run_output_object_class",
        "run_output_object_kind",
        "native_row",
        "ux_vector_embeddings_reusable",
        "ux_vector_embeddings_strict_run",
        "ix_task7_calc_sanads_calculation_fk",
        "ix_task7_deterministic_calculations_run_fk",
        "ix_task7_vector_embeddings_run_fk",
    ):
        assert required in source


def test_component_scope_and_deliverable_durable_columns_are_fully_relational() -> None:
    source = normalized_source()

    for required in (
        "alter table calc_sanads",
        "deal_id",
        "run_id",
        "ck_task7_calc_sanads_scope",
        "ix_task7_calc_sanads_calculation_fk",
        "content_type",
        "size_bytes",
        "content_sha256",
        "uri_sha256",
        "object_version_hash",
        "download_proof_hash",
        "catalog_ordinal",
        "ck_task7_deliverables_terminal_root",
    ):
        assert required in source


def test_runs_and_steps_gain_exact_contract_fields_without_narrowing_step_name() -> None:
    source = normalized_source()

    for required in (
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "pipeline_resume_state",
        "pipeline_resume_error_code",
        "semantic_rule_version",
        "semantic_rule_hash",
        "legacy_run_resume_unsafe",
        "historical_terminal",
        "non_resumable",
        "fk_task7_runs_deal",
        "fk_task7_run_steps_run",
        "uq_task7_run_steps_order",
    ):
        assert required in source
    assert not re.search(r"alter table run_steps\s+alter (?:column )?step_name", source), (
        "0038 must leave the revision-0014 VARCHAR(100) step_name type untouched"
    )


def test_0038_expands_the_forced_rls_inventory_from_55_to_exactly_64() -> None:
    module = load_migration()
    source = normalized_source()

    assert len(REVISION_0037_TABLES) == 55
    assert len(EXPECTED_TENANT_TABLES) == 64
    assert tuple(sorted(module.TASK7_TABLES_V1)) == TASK7_TABLES
    assert frozenset(EXPECTED_TENANT_TABLES) == dependency_probes.TENANT_RLS_TABLES
    for table in TASK7_TABLES:
        assert f"alter table {table} enable row level security" in source
        assert f"alter table {table} force row level security" in source
        assert f"create policy {table}_tenant_isolation" in source
    assert source.count(" with check ") >= len(TASK7_TABLES)
    assert source.count(" using ") >= len(TASK7_TABLES)


def test_upgrade_contains_migration_local_census_and_fail_closed_backfill() -> None:
    source = normalized_source()

    for required in (
        "pg_advisory_xact_lock",
        "pg_temp.task7_backfill_census",
        "task7-migration-backfill-census-v1",
        "legacy-open-freeze-v1",
        "source-package-manifest-v1",
        "task7_parse_attempt_lineage_unreconstructible",
        "task7_source_backfill_unreconstructible",
        "lock table",
        "access exclusive",
    ):
        assert required in source
    assert "create table task7_backfill_census" not in source


def test_downgrade_is_guarded_lossless_transaction_without_cascade_cleanup() -> None:
    module = load_migration()
    source = normalized_source()

    assert callable(module.assert_downgrade_is_lossless)
    for required in (
        "cannot downgrade task 7",
        "full-strict-v21",
        "write_origin = 'runtime'",
        "source_binding_state = 'unbound'",
        "restore",
        "idis_task5_coverage_scope_guard",
        "idis_task6_immutable_guard",
    ):
        assert required in source
    downgrade_source = source.split("def downgrade", 1)[1]
    assert "drop table if exists run_output_objects cascade" not in downgrade_source
    assert "disable trigger all" not in downgrade_source
