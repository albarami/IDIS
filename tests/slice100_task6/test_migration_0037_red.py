"""RED source/catalog boundary for Task 6 migration 0037."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import idis.persistence.migrations as migrations_pkg
from idis.compliance import erasure_postgres
from idis.runtime import dependency_probes
from tests.slice100_task5.test_methodology_migration_0036_red import (
    EXPECTED_TENANT_TABLES as REVISION_0036_TABLES,
)

TASK6_TABLES = (
    "methodology_extraction_attempts",
    "methodology_extraction_results",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_result_claim_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_factual_outputs",
    "methodology_result_sanad_links",
)
EXPECTED_TENANT_TABLES = tuple(sorted((*REVISION_0036_TABLES, *TASK6_TABLES)))
MIGRATIONS_DIR = Path(migrations_pkg.__file__).parent / "versions"


def migration_path() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("0037_*.py"))
    assert len(matches) == 1, "Task 6 must add exactly one linear migration 0037"
    return matches[0]


def load_migration() -> Any:
    path = migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task6_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_source() -> str:
    return re.sub(r"\s+", " ", migration_path().read_text(encoding="utf-8")).lower()


def test_0037_is_linear_and_declares_exactly_the_nine_task6_tables() -> None:
    module = load_migration()

    assert module.revision == "0037"
    assert module.down_revision == "0036"
    assert tuple(sorted(module.TASK6_TABLES)) == TASK6_TABLES
    source = normalized_source()
    assert source.count("create table") == 9
    for table in TASK6_TABLES:
        assert f"create table {table}" in source


def test_0037_contains_no_task7_or_setting_surface() -> None:
    source = normalized_source()

    for forbidden in (
        "run_output_objects",
        "source_lineage_objects",
        "run_input_bindings",
        "run_step_outputs",
        "run_expected_consumer_edges",
        "run_consumer_receipts",
        "run_applicability_decisions",
        "provider_enrichment_facts",
        "getenv(",
        "environ[",
    ):
        assert forbidden not in source


def test_0037_declares_attempt_idempotency_terminal_results_and_typed_links() -> None:
    source = normalized_source()

    for required in (
        "task_input_sha256",
        "lease_fencing_token",
        "provider_request_id",
        "provider_response_sha256",
        "dispatched_call_sha256",
        "prepared_call_sha256",
        "real_call",
        "transport_kind",
        "price_catalog_version",
        "price_catalog_row_sha256",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_usd",
        "outcome_unknown",
        "evidence_found",
        "evidence_not_found",
        "searched_scope_sha256",
        "canonical_key_sha256",
        "factual_output_id",
        "factual_output_record_id",
        "claim_id",
        "evidence_id",
        "sanad_id",
        "defect_id",
    ):
        assert required in source
    assert "unique (tenant_id, run_id, task_record_id, task_input_sha256)" in source
    assert (
        source.count(
            "foreign key (tenant_id, deal_id, run_id, result_id, factual_output_record_id)"
        )
        >= 4
    )
    assert "trg_methodology_result_factual_outputs_immutable" in source


def test_0037_attempt_trigger_uses_wall_clock_statement_timestamp() -> None:
    source = normalized_source()

    assert "lease_expires_at > statement_timestamp()" in source
    assert "lease_expires_at > now()" not in source


def test_0037_enforces_run_global_defect_ownership_and_indexes_child_fk() -> None:
    source = normalized_source()

    assert (
        "constraint uq_methodology_result_defect_links_defect unique "
        "( tenant_id, run_id, defect_id )"
    ) in source
    assert (
        "constraint uq_methodology_result_defect_links_defect unique "
        "( tenant_id, run_id, factual_output_record_id, defect_id )"
    ) not in source
    assert (
        "create index ix_methodology_result_defect_links_defect_scope "
        "on methodology_result_defect_links (tenant_id, deal_id, defect_id)"
    ) in source


def test_0037_failed_consumed_constraint_preserves_malformed_response_truth() -> None:
    source = normalized_source()

    # A complete returned JSON response is billable even when individual metadata
    # members are malformed. The raw hash remains mandatory; only malformed members
    # become NULL under one of the two reviewed stable metadata-failure codes.
    assert "methodology_provider_provenance_invalid" in source
    assert "methodology_provider_usage_invalid" in source
    failed_branch = source.split("(status = 'failed'", 1)[1].split(
        "(status = 'outcome_unknown'", 1
    )[0]
    assert "provider_response_sha256 ~ '^[0-9a-f]{64}$'" in failed_branch
    assert "dispatched_call_sha256 ~ '^[0-9a-f]{64}$'" in failed_branch
    assert "observed_model is null" in failed_branch
    assert "provider_request_id is null" in failed_branch
    assert "stop_reason is null" in failed_branch
    assert "input_tokens is null" in failed_branch
    assert "output_tokens is null" in failed_branch
    succeeded_branch = source.split("(status = 'succeeded'", 1)[1].split("(status = 'failed'", 1)[0]
    assert "observed_model = configured_model" in succeeded_branch
    assert "input_tokens <= reserved_input_tokens" in succeeded_branch
    assert "output_tokens <= reserved_output_tokens" in succeeded_branch


def test_claim_links_bind_the_exact_factual_output_task_lineage() -> None:
    source = normalized_source()
    claim_links = source.split("create table methodology_result_claim_links", 1)[1].split(
        "create table methodology_result_evidence_links", 1
    )[0]

    for required_column in (
        "selection_id uuid not null",
        "coverage_record_id text not null",
        "methodology_version_record_id uuid not null",
        "methodology_question_record_id uuid not null",
    ):
        assert required_column in claim_links
    assert (
        "foreign key ( tenant_id, deal_id, run_id, selection_id, "
        "coverage_record_id, methodology_version_record_id, "
        "methodology_question_record_id ) references methodology_coverage_records ( "
        "tenant_id, deal_id, run_id, selection_id, coverage_record_id, "
        "methodology_version_record_id, methodology_question_record_id )" in claim_links
    )
    lineage_guard = source.split("create or replace function idis_task6_link_lineage_guard()", 1)[
        1
    ].split("create or replace function idis_task6_validate_result_outputs()", 1)[0]
    assert "tg_table_name = 'methodology_result_claim_links'" in lineage_guard
    assert "from methodology_result_factual_outputs f" in lineage_guard
    assert "join methodology_extraction_tasks t" in lineage_guard
    for exact_match in (
        "t.selection_id = new.selection_id",
        "t.coverage_record_id = new.coverage_record_id",
        "t.methodology_version_record_id = new.methodology_version_record_id",
        "t.methodology_question_record_id = new.methodology_question_record_id",
    ):
        assert exact_match in lineage_guard
    assert '"methodology_result_claim_links",' in lineage_guard


def test_0037_adds_only_the_approved_canonical_extensions() -> None:
    source = normalized_source()

    for column in (
        "normalization_rule_version",
        "normalized_predicate",
        "normalized_subject",
        "normalized_period",
        "normalized_unit",
        "value_sha256",
        "source_system",
        "upstream_origin_id",
        "retrieval_timestamp",
        "verification_status",
        "source_rank_subgrade",
        "rationale",
        "updated_at",
        "primary_evidence_id",
    ):
        assert column in source
    assert "claim-normalization-v1" in source
    assert "primary_evidence_id uuid" in source
    assert "foreign key (tenant_id, deal_id, primary_evidence_id)" in source


def test_all_nine_tables_have_canonical_forced_rls_in_both_directions() -> None:
    module = load_migration()
    predicate = re.sub(r"\s+", " ", module.CANONICAL_TENANT_PREDICATE).strip().lower()
    source = normalized_source()

    assert "nullif(current_setting('idis.tenant_id', true), '') is not null" in predicate
    assert "tenant_id = nullif(current_setting('idis.tenant_id', true), '')::uuid" in predicate
    for table in TASK6_TABLES:
        assert f"alter table {table} enable row level security" in source
        assert f"alter table {table} force row level security" in source
        assert f"create policy {table}_tenant_isolation" in source
    assert source.count(" with check ") >= 9
    assert source.count(" using ") >= 9


def test_revision_0037_inventory_expands_from_46_to_55_and_remains_admitted() -> None:
    assert len(REVISION_0036_TABLES) == 46
    assert len(EXPECTED_TENANT_TABLES) == 55
    # Historical migration evidence is a subset contract once later reviewed migrations add tables.
    assert frozenset(EXPECTED_TENANT_TABLES) < dependency_probes.TENANT_RLS_TABLES


def test_task6_tables_are_erased_with_deal_in_child_first_order() -> None:
    classification = erasure_postgres.DEAL_SCOPED_TABLE_CLASSIFICATION
    for table in TASK6_TABLES:
        assert classification[table] == "erased"

    order = erasure_postgres._DIRECT_DELETE_ORDER
    expected_child_first = (
        "methodology_result_defect_links",
        "methodology_result_sanad_links",
        "methodology_result_evidence_links",
        "methodology_result_claim_links",
        "methodology_result_factual_outputs",
        "methodology_extraction_results",
        "methodology_extraction_attempts",
        "methodology_extraction_task_spans",
        "methodology_extraction_tasks",
        "methodology_coverage_records",
        "run_methodology_selections",
        "runs",
    )
    assert [order.index(table) for table in expected_child_first] == sorted(
        order.index(table) for table in expected_child_first
    )


def test_0037_has_lossless_populated_downgrade_guard() -> None:
    module = load_migration()
    source = normalized_source()

    assert callable(module.assert_downgrade_is_lossless)
    assert "lock table" in source
    assert "access exclusive" in source
    assert "cannot downgrade task 6" in source
