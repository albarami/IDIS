"""RED source/catalog contract for Task 5 migration 0036.

The migration is deliberately limited to the four durable methodology tables.
Task 7 output roots, receipts, and expected-consumer edges remain out of scope.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import idis.persistence.migrations as migrations_pkg
from idis.compliance import erasure_postgres
from idis.runtime import dependency_probes
from tests.slice100_task2b._support import TENANT_TABLES as REVISION_0034_TABLES

TASK5_TABLES = (
    "methodology_coverage_records",
    "methodology_questions",
    "methodology_versions",
    "run_methodology_selections",
)
EXPECTED_TENANT_TABLES = tuple(sorted((*REVISION_0034_TABLES, *TASK5_TABLES)))
MIGRATIONS_DIR = Path(migrations_pkg.__file__).parent / "versions"


def _migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0036_*.py"))
    assert len(matches) == 1, "Task 5 must add exactly one linear migration 0036"
    return matches[0]


def _load_migration() -> Any:
    path = _migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task5_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0036_is_linear_and_declares_only_the_four_task5_tables() -> None:
    module = _load_migration()

    assert module.revision == "0036"
    assert module.down_revision == "0035"
    assert tuple(sorted(module.TASK5_TABLES)) == TASK5_TABLES


def test_0036_contains_no_task7_schema_or_unapproved_setting_surface() -> None:
    source = _migration_path().read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", source).upper()

    assert normalized.count("CREATE TABLE") == 4
    for table in TASK5_TABLES:
        assert f"CREATE TABLE {table.upper()}" in normalized
    for forbidden in (
        "RUN_OUTPUT_OBJECT",
        "RUN_STEP_OUTPUT",
        "CONSUMER_RECEIPT",
        "EXPECTED_CONSUMER_EDGE",
        "RUN_INPUT_BINDING",
        "EXTRACTION_TASK",
        "EXTRACTION_ATTEMPT",
        "EXTRACTION_RESULT",
        "PROVIDER_PROVENANCE",
        "GETENV(",
        "ENVIRON[",
    ):
        assert forbidden not in normalized


def test_0036_uses_uuid_record_identity_and_preserves_the_approved_payload_shape() -> None:
    normalized = re.sub(r"\s+", " ", _migration_path().read_text(encoding="utf-8")).lower()

    for required in (
        "methodology_version_record_id uuid",
        "methodology_question_record_id uuid",
        "commercial_dd_version_record_id uuid",
        "financial_dd_version_record_id uuid",
        "source_sheet_or_section",
        "source_row_number",
        "source_anchor_cell",
        "evidence_links jsonb",
        "answer jsonb",
        "conflict_ids text[]",
        "defect_ids text[]",
    ):
        assert required in normalized


def test_four_tables_have_canonical_forced_rls_in_both_policy_directions() -> None:
    module = _load_migration()
    predicate = re.sub(r"\s+", " ", module.CANONICAL_TENANT_PREDICATE).strip().lower()

    assert "nullif(current_setting('idis.tenant_id', true), '') is not null" in predicate
    assert "tenant_id = nullif(current_setting('idis.tenant_id', true), '')::uuid" in predicate
    source = re.sub(r"\s+", " ", _migration_path().read_text(encoding="utf-8")).lower()
    for table in TASK5_TABLES:
        assert f"alter table {table} enable row level security" in source
        assert f"alter table {table} force row level security" in source
        assert f"create policy {table}_tenant_isolation" in source
    assert source.count(" with check ") >= 4
    assert source.count(" using ") >= 4


def test_strict_readiness_inventory_contains_all_46_revision_0036_tables() -> None:
    assert len(REVISION_0034_TABLES) == 42
    assert len(EXPECTED_TENANT_TABLES) == 46
    # Later approved revisions extend the global readiness census; Task 5 owns this subset.
    assert frozenset(EXPECTED_TENANT_TABLES) <= dependency_probes.TENANT_RLS_TABLES


def test_migration_exposes_lossless_downgrade_guard() -> None:
    module = _load_migration()

    assert callable(module.assert_downgrade_is_lossless)
    source = re.sub(r"\s+", " ", _migration_path().read_text(encoding="utf-8")).upper()
    assert "LOCK TABLE" in source
    assert "ACCESS EXCLUSIVE" in source
    assert "CANNOT DOWNGRADE TASK 5" in source


def test_all_four_task5_tables_have_an_explicit_erasure_disposition_and_fk_safe_order() -> None:
    expected_task5_classification = {
        "methodology_coverage_records": "erased_with_deal",
        "methodology_questions": "retained_tenant_frozen_source",
        "methodology_versions": "retained_tenant_frozen_source",
        "run_methodology_selections": "erased_with_deal",
    }
    # Later approved revisions add classifications; Task 5 still proves its exact entries.
    assert {
        table: erasure_postgres.METHODOLOGY_TABLE_ERASURE_CLASSIFICATION[table]
        for table in expected_task5_classification
    } == expected_task5_classification
    assert (
        erasure_postgres.DEAL_SCOPED_TABLE_CLASSIFICATION["methodology_coverage_records"]
        == "erased"
    )
    assert (
        erasure_postgres.DEAL_SCOPED_TABLE_CLASSIFICATION["run_methodology_selections"] == "erased"
    )
    order = erasure_postgres._DIRECT_DELETE_ORDER
    assert (
        order.index("methodology_coverage_records")
        < order.index("run_methodology_selections")
        < order.index("runs")
    )
