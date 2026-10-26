"""RED-first PostgreSQL execution census for the changed v22 authorities.

The structural tests are hermetic.  The final test executes only when the
three already-approved database URLs are explicitly present in the process
environment; the runner must create and drop its own disposable database.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from collections import Counter
from typing import Any

import pytest

from . import _v22_oracle as oracle

_DATABASE_ENV_NAMES = (
    "IDIS_DATABASE_ADMIN_URL",
    "IDIS_DATABASE_URL",
    "IDIS_ERASURE_DATABASE_URL",
)
_AUTHORITY_FAMILIES = (
    "dashboard-root-equation-v2",
    "dashboard-partial-native-root-exact-one-v1",
    "task8-current48-dashboard-erasure-refusal-v1",
)
_DASHBOARD_TARGET_EVENTS = {
    ("methodology_truth_dashboard_rows", "INSERT"),
    ("methodology_truth_dashboard_rows", "UPDATE"),
    ("methodology_truth_dashboard_rows", "DELETE"),
    ("methodology_truth_dashboard_root_links", "INSERT"),
    ("methodology_truth_dashboard_root_links", "UPDATE"),
    ("methodology_truth_dashboard_root_links", "DELETE"),
}


def _assets() -> tuple[dict[str, Any], Any]:
    v22 = oracle.load_v22()
    oracle.validate_v22(v22)
    runner = oracle.load_module(
        oracle.SQL_CENSUS_RUNNER,
        "_task8_v22_sql_census_runner_red",
        "TASK8_V22_SQL_CENSUS_RUNNER_NOT_IMPLEMENTED",
    )
    return v22, runner


def _runner_only() -> Any:
    """Load the hermetic runner without admitting provisional contract bytes."""

    return oracle.load_module(
        oracle.SQL_CENSUS_RUNNER,
        "_task8_v22_sql_census_runner_hermetic",
        "TASK8_V22_SQL_CENSUS_RUNNER_NOT_IMPLEMENTED",
    )


def _case_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sql_authority(v22: dict[str, Any], authority_id: str) -> dict[str, Any]:
    matches = [
        row for row in v22["sql_authority_registry"]["rows"] if row["authority_id"] == authority_id
    ]
    assert len(matches) == 1
    return matches[0]


def _guard_dashboard_columns(v22: dict[str, Any]) -> list[str]:
    matches = [
        row
        for row in v22["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["authority_id"] in _AUTHORITY_FAMILIES[:2]
    ]
    assert len(matches) == 3
    return sorted(
        {
            column
            for row in matches
            for relation, columns, _source in row["direct_read_column_tuple"]
            if relation == "methodology_truth_dashboard_rows"
            for column in columns
        }
    )


def _guard_function_rows(v22: dict[str, Any], authority_id: str) -> list[dict[str, Any]]:
    matches = [
        row
        for row in v22["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["authority_id"] == authority_id
    ]
    assert matches
    return sorted(matches, key=lambda row: row["function_ordinal"])


def _installed_guard_owner_select_acl(v22: dict[str, Any]) -> list[list[object]]:
    rows = [
        row
        for row in v22["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["install_scope"] == "CURRENT_0038" or row["authority_id"] in _AUTHORITY_FAMILIES[:2]
    ]
    relations = {
        relation for row in rows for relation, _columns, _source in row["direct_read_column_tuple"]
    }
    return sorted(
        [
            [
                relation,
                sorted(
                    {
                        column
                        for row in rows
                        for row_relation, columns, _source in row["direct_read_column_tuple"]
                        if row_relation == relation
                        for column in columns
                    }
                ),
            ]
            for relation in relations
        ]
    )


def test_v22_census_executes_exact_hash_bound_authority_and_erasure_bytes() -> None:
    v22, runner = _assets()
    bundle = runner.build_hash_bound_authority_execution(v22)

    assert tuple(bundle) == (
        "schema_version",
        "sql_authorities",
        "erasure_authority",
        "expected_sha256",
    )
    assert bundle["schema_version"] == "task8-v22-hash-bound-authority-execution-v1"
    assert [row["authority_id"] for row in bundle["sql_authorities"]] == list(
        _AUTHORITY_FAMILIES[:2]
    )
    expected_statements: list[str] = []
    authority_install_statements: dict[str, list[str]] = {}
    for binding in bundle["sql_authorities"]:
        statement_offset = len(expected_statements)
        source = _sql_authority(v22, binding["authority_id"])
        assert binding["authority_sha256"] == source["expected_sha256"]
        assert binding["target_tuple"] == source["target_tuple"]
        assert binding["body_sql_utf8_hex_tuple"] == source["body_sql_utf8_hex_tuple"]
        assert binding["body_tuple_sha256"] == _canonical_sha256(
            [
                "task8-v22-authority-body-tuple-v1",
                binding["authority_id"],
                source["body_sql_utf8_hex_tuple"],
            ]
        )
        expected_statements.extend(
            bytes.fromhex(value).decode("utf-8") for value in source["body_sql_utf8_hex_tuple"]
        )
        guard_rows = _guard_function_rows(v22, binding["authority_id"])
        guard_bindings = binding["guard_function_acl_binding_tuple"]
        assert [row["function_signature"] for row in guard_rows] == [
            row["function_signature"] for row in guard_bindings
        ]
        for guard_acl, guard_binding in zip(guard_rows, guard_bindings, strict=True):
            assert guard_binding["guard_function_acl_sha256"] == guard_acl["expected_sha256"]
            assert guard_binding["owner_sql_utf8_hex"] == guard_acl["owner_sql_utf8_hex"]
            assert (
                guard_binding["function_acl_sql_utf8_hex_tuple"]
                == guard_acl["acl_sql_utf8_hex_tuple"]
            )
            expected_statements.append(
                bytes.fromhex(guard_acl["owner_sql_utf8_hex"]).decode("utf-8")
            )
            expected_statements.extend(
                bytes.fromhex(value).decode("utf-8")
                for value in guard_acl["acl_sql_utf8_hex_tuple"]
            )
            assert guard_binding["guard_select_sql_tuple"]
            expected_statements.extend(guard_binding["guard_select_sql_tuple"])
        authority_install_statements[binding["authority_id"]] = expected_statements[
            statement_offset:
        ]

    erasure_source = v22["erasure_execution_registry"]["rows"][0]
    erasure = bundle["erasure_authority"]
    assert erasure["execution_id"] == erasure_source["execution_id"]
    assert erasure["authority_sha256"] == erasure_source["expected_sha256"]
    assert (
        erasure["current_body_sql_utf8_hex_tuple"]
        == erasure_source["current_body_sql_utf8_hex_tuple"]
    )
    assert (
        erasure["current_acl_sql_utf8_hex_tuple"]
        == erasure_source["current_acl_sql_utf8_hex_tuple"]
    )
    exact_body = [
        bytes.fromhex(value).decode("utf-8")
        for value in erasure_source["current_body_sql_utf8_hex_tuple"]
    ]
    exact_acl = [
        bytes.fromhex(value).decode("utf-8")
        for value in erasure_source["current_acl_sql_utf8_hex_tuple"]
    ]
    assert sum(oracle.ERASURE_REFUSAL_CODE in statement for statement in exact_body) == 1
    exact_dashboard_acl = (
        "GRANT SELECT (tenant_id,deal_id) ON TABLE "
        "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner"
    )
    assert [
        statement for statement in exact_acl if "methodology_truth_dashboard_rows" in statement
    ] == [exact_dashboard_acl]
    expected_statements.extend((*exact_body, *exact_acl))
    partial_contract = v22["task8_fdd_contract"]["partial_exact_one_contract"]
    exact_restore = [
        bytes.fromhex(value).decode("utf-8")
        for value in partial_contract["downgrade_sql_utf8_hex_tuple"]
    ]
    expected_statements.extend(exact_restore)
    expected_statements.extend(
        authority_install_statements["dashboard-partial-native-root-exact-one-v1"]
    )

    class RecordingCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> None:
            self.statements.append(statement)

    cursor = RecordingCursor()
    binding_receipt = runner.execute_hash_bound_authority_sql(cursor, bundle)
    assert cursor.statements == expected_statements
    assert binding_receipt["bundle_sha256"] == bundle["expected_sha256"]
    assert binding_receipt["partial_restore_reinstall_statement_tuple_sha256"] == (
        _canonical_sha256(
            [
                "task8-v22-partial-restore-reinstall-v1",
                exact_restore,
                authority_install_statements["dashboard-partial-native-root-exact-one-v1"],
            ]
        )
    )
    assert binding_receipt["authority_binding_tuple"] == [
        {
            "authority_id": row["authority_id"],
            "authority_sha256": row["authority_sha256"],
            "body_tuple_sha256": row["body_tuple_sha256"],
            "target_tuple": row["target_tuple"],
            "downgrade_tuple_sha256": row["downgrade_tuple_sha256"],
            "downgrade_statement_count": len(row["downgrade_sql_utf8_hex_tuple"]),
            "guard_function_acl_binding_tuple": [
                {
                    "function_signature": guard["function_signature"],
                    "function_kind": guard["function_kind"],
                    "target_trigger_tuple": guard["target_trigger_tuple"],
                    "target_event_coordinate_tuple": guard["target_event_coordinate_tuple"],
                    "guard_select_sql_tuple": guard["guard_select_sql_tuple"],
                    "guard_function_acl_sha256": guard["guard_function_acl_sha256"],
                    "guard_select_tuple_sha256": guard["guard_select_tuple_sha256"],
                }
                for guard in row["guard_function_acl_binding_tuple"]
            ],
        }
        for row in bundle["sql_authorities"]
    ] + [
        {
            "authority_id": _AUTHORITY_FAMILIES[2],
            "authority_sha256": erasure["authority_sha256"],
            "body_tuple_sha256": erasure["body_tuple_sha256"],
            "acl_tuple_sha256": erasure["acl_tuple_sha256"],
        }
    ]
    assert binding_receipt["executed_statement_tuple_sha256"] == _canonical_sha256(
        ["task8-v22-executed-authority-sql-v1", expected_statements]
    )
    assert "task8_dashboard_truth_guard" not in inspect.getsource(runner)
    assert "task8_partial_exact_one_guard" not in inspect.getsource(runner)
    assert "task8_rls_probe_" not in inspect.getsource(runner)
    for forbidden in (
        "def _insert_parent",
        "def _insert_dashboard(",
        "found_count",
        "not_found_count",
    ):
        assert forbidden not in inspect.getsource(runner)


def test_v22_partial_extension_binds_exact_replacement_restoration_and_acl_bytes() -> None:
    v22, runner = _assets()
    partial = _sql_authority(v22, "dashboard-partial-native-root-exact-one-v1")
    contract = v22["task8_fdd_contract"]["partial_exact_one_contract"]
    bundle = runner.build_hash_bound_authority_execution(v22)
    binding = next(
        row for row in bundle["sql_authorities"] if row["authority_id"] == partial["authority_id"]
    )

    exact_install = [
        bytes.fromhex(value).decode("utf-8") for value in partial["body_sql_utf8_hex_tuple"]
    ]
    exact_restore = [
        bytes.fromhex(value).decode("utf-8") for value in contract["downgrade_sql_utf8_hex_tuple"]
    ]
    assert len(exact_install) == 3
    assert exact_install[0].startswith(
        "CREATE OR REPLACE FUNCTION public.idis_task7_current_native_hashes_v1"
    )
    assert exact_install[1].startswith(
        "CREATE OR REPLACE FUNCTION public.idis_task7_current_output_exact_one_v1"
    )
    assert "methodology_truth_dashboard_rows" in exact_install[2]
    assert len(exact_restore) == 3
    assert exact_restore[0].startswith("DROP TRIGGER")
    assert binding["downgrade_sql_utf8_hex_tuple"] == contract["downgrade_sql_utf8_hex_tuple"]
    assert binding["downgrade_tuple_sha256"] == _canonical_sha256(
        [
            "task8-v22-authority-downgrade-tuple-v1",
            partial["authority_id"],
            contract["downgrade_sql_utf8_hex_tuple"],
        ]
    )
    assert [row["function_signature"] for row in binding["guard_function_acl_binding_tuple"]] == [
        "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)",
        "public.idis_task7_current_output_exact_one_v1()",
    ]
    assert contract["current_target_triggers_preserved_unchanged"] is True
    assert contract["downgrade_restores_replaced_functions_unchanged"] is True


def test_v22_disposable_path_installs_the_exact_bundle_and_passes_its_binding() -> None:
    _v22, runner = _assets()

    entrypoint_source = inspect.getsource(runner.run_disposable_postgres_census)
    setup_source = inspect.getsource(runner._setup_disposable_schema)
    database_source = inspect.getsource(runner._run_database_execution)
    assert "build_hash_bound_authority_execution" in entrypoint_source
    # Exact SQL is installed inside the disposable setup transaction.
    # Importing migrations.env outside Alembic has an unbound context proxy;
    # the runner therefore drives the same real environment via command.upgrade.
    assert "_run_migrations_through_0038" in setup_source
    migration_source = inspect.getsource(runner._run_migrations_through_0038)
    assert "command.upgrade" in migration_source
    assert '"0038"' in migration_source
    assert "install_artifact_sql_scaffold" not in setup_source
    assert "execute_hash_bound_authority_sql" in setup_source
    assert "authority_execution_binding=" in entrypoint_source
    assert "authority_execution_binding" in database_source


def test_v22_future_table_scaffold_is_derived_from_the_reviewed_registry() -> None:
    v22, runner = _assets()

    scaffold = runner.build_future_table_scaffold(v22, "idis_application")
    assert scaffold["schema_version"] == "task8-v22-future-table-scaffold-v1"
    assert scaffold["relation_tuple"] == [
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    ]
    sql = " ".join(scaffold["statement_tuple"])
    for relation, required_columns in {
        "methodology_truth_dashboard_rows": {
            "dashboard_row_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "run_output_object_id",
            "run_output_object_class",
            "run_output_object_kind",
            "coverage_record_id",
            "root_projection_sha256",
            "row_disposition",
            "row_payload_sha256",
            "lease_owner_id",
            "lease_fencing_token",
        },
        "methodology_truth_dashboard_root_links": {
            "dashboard_root_link_id",
            "tenant_id",
            "deal_id",
            "run_id",
            "dashboard_row_id",
            "dashboard_output_object_id",
            "root_kind",
            "root_ordinal",
            "root_output_object_id",
            "root_output_object_class",
            "root_output_object_kind",
            "lease_owner_id",
            "lease_fencing_token",
        },
    }.items():
        assert f"CREATE TABLE public.{relation}" in sql
        assert all(column in sql for column in required_columns)
    assert scaffold["installed_tenant_rls_table_count"] == 69
    assert "CONSTRAINT pk_v2_methodology_truth_dashboard_rows PRIMARY KEY" in sql
    assert "CONSTRAINT pk_v3_dashboard_root_link PRIMARY KEY" in sql
    for index_name in {
        "ix_v2_methodology_truth_dashboard_rows_run",
        "ix_v2_methodology_truth_dashboard_rows_root",
        "ix_v2_dashboard_coverage",
        "ix_v3_dashboard_root_link_run",
        "ix_v3_dashboard_root_link_parent",
        "ix_v3_dashboard_root_link_root",
    }:
        assert f"INDEX {index_name}" in sql
    assert oracle.SHA256_RE.fullmatch(scaffold["expected_sha256"])

    current_grants = runner.build_application_behavior_grants(
        v22,
        "idis_application",
    )
    assert current_grants["relation_tuple"] == [
        "run_output_objects",
        "run_step_outputs",
    ]
    assert current_grants["statement_tuple"] == [
        "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE run_output_objects TO idis_application",
        "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE run_step_outputs TO idis_application",
    ]
    assert oracle.SHA256_RE.fullmatch(current_grants["expected_sha256"])


def test_v22_parent_root_bootstrap_placeholders_are_unique_until_exact_helper_refresh() -> None:
    _v22, runner = _assets()

    class RecordingCursor:
        def __init__(self) -> None:
            self.parameters: list[tuple[object, ...]] = []

        def execute(self, _statement: str, parameters: tuple[object, ...]) -> None:
            self.parameters.append(parameters)

    scope = runner._CaseScope(
        tenant_id=uuid.uuid4(),
        deal_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        lease_owner_id=uuid.uuid4(),
        parent_step_id=uuid.uuid4(),
        dashboard_step_id=uuid.uuid4(),
    )
    cursor = RecordingCursor()
    for output_id in (uuid.uuid4(), uuid.uuid4()):
        runner._insert_seed_output(
            cursor,
            scope=scope,
            output_object_id=output_id,
            output_kind="METHODOLOGY_EXTRACTION_RESULT",
            output_ordinal=len(cursor.parameters),
        )

    first, second = cursor.parameters
    assert oracle.SHA256_RE.fullmatch(first[5])
    assert oracle.SHA256_RE.fullmatch(first[6])
    assert first[5:7] != second[5:7]
    refresh_source = inspect.getsource(runner._refresh_seed_output_hashes)
    assert "idis_task7_current_native_hashes_v1" in refresh_source
    assert "UPDATE public.run_output_objects" in refresh_source


def test_v22_real_composition_paths_seed_the_literal_21_cdd_79_fdd_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner_only()

    batch_source = inspect.getsource(runner._seed_dashboard_batch)
    assert "range(coverage_row_count)" in batch_source
    assert "ALL_NOT_FOUND_GAP" in batch_source
    assert "parent_output_ordinals" in batch_source
    assert "commercial_coverage_row_count" in batch_source

    calls: list[dict[str, Any]] = []

    class Connection:
        committed = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            raise AssertionError("legal batch must not roll back")

        def close(self) -> None:
            self.closed = True

    connection = Connection()

    def record_graph(_admin_url: str, **kwargs: Any) -> int:
        calls.append(kwargs)
        return int(kwargs["dashboard_output_ordinal"])

    monkeypatch.setattr(runner, "_connect", lambda _url: connection)
    monkeypatch.setattr(runner, "_seed_dashboard_graph", record_graph)
    assert runner._seed_dashboard_batch(
        "postgresql://not-opened",
        scope=object(),
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=100,
        financial_coverage_row_count=79,
    ) == tuple(range(100))
    assert connection.committed is True
    assert connection.closed is True
    assert [row["methodology_type"] for row in calls[:21]] == ["commercial_dd"] * 21
    assert [row["methodology_question_ordinal"] for row in calls[:21]] == list(range(1, 22))
    assert [row["methodology_type"] for row in calls[21:]] == ["financial_dd"] * 79
    assert [row["methodology_question_ordinal"] for row in calls[21:]] == list(range(1, 80))
    assert calls[0]["semantic_case"] == "ALL_FOUND_ANSWERED"
    assert {row["semantic_case"] for row in calls[1:]} == {"ALL_NOT_FOUND_GAP"}
    for composition in (
        runner._execute_behavior_case,
        runner._run_rls_behavior_matrix,
        runner._run_lock_races,
    ):
        source = inspect.getsource(composition)
        assert "_seed_dashboard_batch" in source
        assert "_insert_dashboard_batch" in source


def test_v22_global_dashboard_case_catalog_and_fixture_plans_are_exact_and_hermetic() -> None:
    runner = _runner_only()

    cases = runner._global_dashboard_census_cases(_AUTHORITY_FAMILIES[0])
    assert [case["case_id"] for case in cases] == [
        "dashboard-global-missing-row",
        "dashboard-global-source-count-99",
        "dashboard-global-extra-noncoverage",
        "dashboard-global-duplicate-coverage",
    ]
    assert [case["semantic_case"] for case in cases] == [
        "GLOBAL_DASHBOARD_ROW_MISSING",
        "GLOBAL_COVERAGE_SOURCE_COUNT_99",
        "GLOBAL_EXTRA_NONCOVERAGE_ROW",
        "GLOBAL_DUPLICATE_COVERAGE_ROW",
    ]
    assert [case["expected_sqlstate"] for case in cases] == ["23514", "23514", "23503", "23505"]
    assert [case["expected_reason_code"] for case in cases] == [
        "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION",
        "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION",
        "",
        "",
    ]
    assert {case["execution_role"] for case in cases} == {"application"}
    assert {case["expected"] for case in cases} == {"REJECT_ZERO_MUTATION"}

    plans = {
        case["semantic_case"]: runner._global_dashboard_census_plan(
            case["semantic_case"],
            coverage_row_count=100,
            financial_coverage_row_count=79,
        )
        for case in cases
    }
    assert (
        plans["GLOBAL_DASHBOARD_ROW_MISSING"].seed_coverage_row_count,
        plans["GLOBAL_DASHBOARD_ROW_MISSING"].seed_financial_coverage_row_count,
        plans["GLOBAL_DASHBOARD_ROW_MISSING"].omit_focus_output,
        plans["GLOBAL_DASHBOARD_ROW_MISSING"].extra_row_disposition,
    ) == (100, 79, True, None)
    assert (
        plans["GLOBAL_COVERAGE_SOURCE_COUNT_99"].seed_coverage_row_count,
        plans["GLOBAL_COVERAGE_SOURCE_COUNT_99"].seed_financial_coverage_row_count,
        plans["GLOBAL_COVERAGE_SOURCE_COUNT_99"].omit_focus_output,
        plans["GLOBAL_COVERAGE_SOURCE_COUNT_99"].extra_row_disposition,
    ) == (99, 78, False, None)
    assert plans["GLOBAL_EXTRA_NONCOVERAGE_ROW"].extra_row_disposition == "NONCOVERAGE"
    assert plans["GLOBAL_DUPLICATE_COVERAGE_ROW"].extra_row_disposition == "DUPLICATE"


def test_v22_zero_mutation_projection_is_the_complete_reviewed_scope() -> None:
    v22, runner = _assets()

    projection = runner.build_zero_mutation_projection_manifest(v22)
    erasure = v22["erasure_execution_registry"]["rows"][0]
    assert projection["current_relation_tuple"] == erasure["current_relation_tuple"]
    assert projection["indirect_relation_tuple"] == [
        "calc_sanads",
        "human_gate_actions",
        "run_steps",
    ]
    assert projection["task8_relation_tuple"] == [
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    ]
    assert {
        "audit_events",
        "deals",
        "erasure_requests",
    }.issubset(projection["control_relation_tuple"])
    assert oracle.SHA256_RE.fullmatch(projection["expected_sha256"])


def test_v22_lock_binding_is_exactly_shared_by_dashboard_and_erasure() -> None:
    v22, runner = _assets()

    lock = runner.build_advisory_lock_binding(v22)
    assert lock["schema_version"] == "task8-v22-shared-advisory-lock-binding-v1"
    assert lock["dashboard_authority_id"] == "dashboard-root-equation-v2"
    assert lock["erasure_execution_id"] == "approved-erasure-execution-v1"
    assert lock["prefix"] == "idis-task7-erasure-tenant-lock-v1:"
    assert lock["seed"] == 0
    assert lock["dashboard_occurrence_count"] == 1
    assert lock["erasure_occurrence_count"] == 1
    assert oracle.SHA256_RE.fullmatch(lock["expected_sha256"])
    race_source = inspect.getsource(runner._run_lock_races)
    assert "lock_binding" in race_source
    assert "pg_advisory_xact_lock" in race_source
    assert "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE" in race_source
    assert "_seed_erasure_request" in race_source
    assert "_call_erasure" in race_source


def test_v22_census_manifest_covers_every_changed_authority_and_direct_sql_path() -> None:
    v22, runner = _assets()
    manifest = runner.build_census_case_manifest(v22)

    assert tuple(manifest) == (
        "schema_version",
        "setup_role",
        "behavior_role_contract",
        "dashboard_coverage_row_count",
        "dashboard_financial_coverage_row_count",
        "authorities",
        "catalog_assertions",
        "race_cases",
    )
    assert manifest["schema_version"] == "task8-v22-postgres-authority-census-v1"
    assert manifest["setup_role"] == "admin_setup_only"
    assert manifest["behavior_role_contract"] == {
        "guard_default": "application",
        "erasure_function": "erasure_executor",
        "admin_behavior_case_count": 0,
    }
    assert manifest["dashboard_coverage_row_count"] == 100
    assert manifest["dashboard_financial_coverage_row_count"] == 79
    assert tuple(row["authority_id"] for row in manifest["authorities"]) == (_AUTHORITY_FAMILIES)
    for authority in manifest["authorities"]:
        assert authority["legal_case_tuple"]
        assert authority["illegal_case_tuple"]
        assert authority["direct_sql_case_tuple"]
        assert set(authority) == {
            "authority_ordinal",
            "authority_id",
            "target_event_tuple",
            "legal_case_tuple",
            "illegal_case_tuple",
            "direct_sql_case_tuple",
        }

    dashboard = manifest["authorities"][0]
    assert {
        (target, event) for target, event in dashboard["target_event_tuple"]
    } == _DASHBOARD_TARGET_EVENTS
    dashboard_event_cases = {
        tuple(event)
        for case in (
            *dashboard["legal_case_tuple"],
            *dashboard["illegal_case_tuple"],
            *dashboard["direct_sql_case_tuple"],
        )
        for event in case["exercised_target_event_tuple"]
    }
    assert dashboard_event_cases == _DASHBOARD_TARGET_EVENTS
    partial = manifest["authorities"][1]
    partial_authority = _sql_authority(
        v22,
        "dashboard-partial-native-root-exact-one-v1",
    )
    assert {(target, event) for target, event in partial["target_event_tuple"]} == {
        (target, event)
        for target, _trigger in partial_authority["target_tuple"]
        for event in ("INSERT", "UPDATE", "DELETE")
    }
    assert len(partial["target_event_tuple"]) == 57
    assert {
        tuple(event)
        for case in (
            *partial["legal_case_tuple"],
            *partial["illegal_case_tuple"],
            *partial["direct_sql_case_tuple"],
        )
        for event in case["exercised_target_event_tuple"]
    } == {
        ("run_output_objects", "INSERT"),
        ("methodology_truth_dashboard_rows", "INSERT"),
    }
    duplicate = next(
        case
        for case in partial["illegal_case_tuple"]
        if case["case_id"] == "partial-root-duplicate"
    )
    # The artifact-compatible immediate UNIQUE is the reachable duplicate guard.
    assert duplicate["expected_sqlstate"] == "23505"
    missing = next(
        case for case in partial["illegal_case_tuple"] if case["case_id"] == "partial-root-missing"
    )
    assert missing["expected_reason_code"] == "STEP_OUTPUT_MISSING"
    global_cases = {
        case["case_id"]: case
        for case in dashboard["illegal_case_tuple"]
        if case["case_id"].startswith("dashboard-global-")
    }
    assert set(global_cases) == {
        "dashboard-global-missing-row",
        "dashboard-global-source-count-99",
        "dashboard-global-extra-noncoverage",
        "dashboard-global-duplicate-coverage",
    }
    assert global_cases["dashboard-global-missing-row"]["expected_sqlstate"] == "23514"
    assert global_cases["dashboard-global-source-count-99"]["expected_sqlstate"] == "23514"
    assert global_cases["dashboard-global-extra-noncoverage"]["expected_sqlstate"] == "23503"
    assert global_cases["dashboard-global-duplicate-coverage"]["expected_sqlstate"] == "23505"
    reasoned_dashboard_cases = [
        case
        for case in (*dashboard["illegal_case_tuple"], *dashboard["direct_sql_case_tuple"])
        if case["expected_reason_code"]
    ]
    assert all(
        case["expected_reason_code"] == "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
        for case in reasoned_dashboard_cases
    )
    erasure = manifest["authorities"][2]
    assert erasure["target_event_tuple"] == [
        ["public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)", "EXECUTE"]
    ]

    cases = _case_rows(manifest)
    case_ids = [case["case_id"] for case in cases]
    assert len(case_ids) == len(set(case_ids))
    assert all(case["execution_role"] != "admin" for case in cases)
    assert all(
        case["execution_role"] == "application"
        for case in cases
        if case["authority_id"] != "task8-current48-dashboard-erasure-refusal-v1"
    )
    assert {
        case["execution_role"]
        for case in cases
        if case["authority_id"] == "task8-current48-dashboard-erasure-refusal-v1"
    } == {"application", "erasure_executor"}
    assert any(case["expected_sqlstate"] == "23514" for case in cases)
    assert any(case.get("expected_reason_code") == oracle.ERASURE_REFUSAL_CODE for case in cases)
    runner.validate_census_case_manifest(manifest, v22)
    catalog_source = inspect.getsource(runner._catalog_projection)
    assert "ctrg_task8_partial_native_root_run_output_objects" not in catalog_source
    assert "target_event_coordinate_tuple" in catalog_source
    assert "urls.application" in catalog_source
    assert "oidvectortypes" in catalog_source


def test_v22_census_manifest_includes_truth_table_acl_rls_and_two_way_lock_proofs() -> None:
    v22, runner = _assets()
    manifest = runner.build_census_case_manifest(v22)
    catalog = manifest["catalog_assertions"]
    dashboard_guard = next(
        row
        for row in _guard_function_rows(v22, "dashboard-root-equation-v2")
        if row["function_kind"] == "TRIGGER_GUARD"
    )
    partial_guard = next(
        row
        for row in _guard_function_rows(
            v22,
            "dashboard-partial-native-root-exact-one-v1",
        )
        if row["function_kind"] == "TRIGGER_GUARD"
    )
    trigger_function_binding_tuple = sorted(
        [
            [authority, target, trigger, guard["function_signature"]]
            for authority, guard in (
                ("dashboard-root-equation-v2", dashboard_guard),
                ("dashboard-partial-native-root-exact-one-v1", partial_guard),
            )
            for target, trigger in guard["target_trigger_tuple"]
        ]
    )

    assert catalog == {
        "installed_tenant_rls_table_count": 69,
        "dashboard_enable_rls": True,
        "dashboard_force_rls": True,
        "guard_owner_superuser": False,
        "guard_owner_bypassrls": False,
        "guard_owner_role_membership_count": 0,
        "erasure_owner_superuser": False,
        "erasure_owner_bypassrls": False,
        "erasure_owner_role_membership_count": 0,
        "application_superuser": False,
        "application_bypassrls": False,
        "erasure_executor_superuser": False,
        "erasure_executor_bypassrls": False,
        "erasure_executor_role_membership_count": 0,
        "guard_owner_dashboard_select_columns": _guard_dashboard_columns(v22),
        "guard_owner_dashboard_insert": False,
        "guard_owner_dashboard_update": False,
        "guard_owner_dashboard_delete": False,
        "guard_owner_dashboard_write_privilege_count": 0,
        "erasure_owner_dashboard_select_columns": ["tenant_id", "deal_id"],
        "erasure_owner_dashboard_write_privilege_count": 0,
        "erasure_executor_dashboard_table_privilege_count": 0,
        "application_runner_execute_count": 0,
        "erasure_executor_runner_execute_count": 1,
        "erasure_executor_schema_usage": True,
        "erasure_executor_schema_create": False,
        "dashboard_policy_count": 2,
        "dashboard_policy_using_with_check_equal_count": 2,
        "dashboard_guard_function_owner_count": 3,
        "dashboard_guard_security_definer_count": 2,
        "application_guard_function_execute_count": 0,
        "guard_owner_parent_select_acl_tuple": _installed_guard_owner_select_acl(v22),
        "guard_owner_parent_write_privilege_count": 0,
        "dashboard_trigger_event_tuple": [list(row) for row in sorted(_DASHBOARD_TARGET_EVENTS)],
        "partial_trigger_event_tuple": sorted(
            [
                [target, event]
                for target, _trigger in _sql_authority(
                    v22,
                    "dashboard-partial-native-root-exact-one-v1",
                )["target_tuple"]
                for event in ("DELETE", "INSERT", "UPDATE")
            ]
        ),
        "dashboard_trigger_event_coordinate_tuple": dashboard_guard[
            "target_event_coordinate_tuple"
        ],
        "partial_trigger_event_coordinate_tuple": partial_guard["target_event_coordinate_tuple"],
        "trigger_function_binding_tuple": trigger_function_binding_tuple,
        "partial_target_application_dml_reachability_tuple": sorted(
            [
                [
                    target,
                    trigger,
                    True,
                    True,
                    True,
                    partial_guard["function_signature"],
                ]
                for target, trigger in partial_guard["target_trigger_tuple"]
            ]
        ),
        "partial_target_application_dml_reachability_count": 19,
        "trigger_catalog_application_role_match": True,
        "erasure_runner_owner_valid": True,
        "erasure_runner_security_definer": True,
        "current_erasure_relation_count": 48,
        "future_erasure_relation_count": 38,
        "complete_erasure_relation_count": 86,
    }
    assert manifest["race_cases"] == [
        {
            "case_id": "hold-erasure-lock-then-dashboard-write",
            "first_actor": "erasure_executor",
            "second_actor": "application",
            "expected": "SECOND_BLOCKS_THEN_PARENT_REVALIDATION_REJECTS",
        },
        {
            "case_id": "hold-dashboard-lock-then-erasure",
            "first_actor": "application",
            "second_actor": "erasure_executor",
            "expected": "SECOND_BLOCKS_THEN_DASHBOARD_REFUSAL",
        },
    ]
    cases = _case_rows(manifest)
    dashboard_outcomes = {
        case["semantic_case"]
        for case in cases
        if case["authority_id"] == "dashboard-root-equation-v2" and case["case_kind"] == "LEGAL"
    }
    assert {
        "ALL_NOT_FOUND_GAP",
        "MIXED_FOUND_NOT_FOUND_ANSWERED",
        "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG",
    }.issubset(dashboard_outcomes)
    assert any(
        case["semantic_case"] == "REQUIRED_FORMULA_MISSING"
        and case["expected"] == "REJECT_ZERO_MUTATION"
        for case in cases
    )
    runner.validate_census_case_manifest(manifest, v22)


def test_v22_census_runner_api_requires_disposable_admin_app_and_executor_urls() -> None:
    _v22, runner = _assets()
    signature = inspect.signature(runner.run_disposable_postgres_census)

    assert tuple(signature.parameters) == (
        "admin_database_url",
        "application_database_url",
        "erasure_database_url",
        "artifact_raw",
    )
    assert callable(runner.validate_census_receipt)
    assert "drop" in inspect.getsource(runner.run_disposable_postgres_census).lower()
    assert "application" in inspect.getsource(runner.run_disposable_postgres_census).lower()


def test_v22_rejected_cases_require_measured_pre_post_zero_mutation() -> None:
    _v22, runner = _assets()
    case_source = inspect.getsource(runner._execute_behavior_case)
    execution_source = inspect.getsource(runner._run_database_execution)

    assert "_tenant_deal_projection" in case_source
    assert "pre_projection_sha256" in case_source
    assert "post_projection_sha256" in case_source
    assert "zero_mutation" in case_source
    assert '"dashboard_refusal_zero_mutation": True' not in execution_source
    assert "rejected_case_zero_mutation_count" in execution_source
    assert "rejected_case_projection_sha256" in execution_source
    projection_source = inspect.getsource(runner._tenant_deal_projection)
    assert "projection_manifest" in projection_source
    assert "current_relation_tuple" in projection_source
    assert "calc_sanads" in projection_source
    assert "human_gate_actions" in projection_source
    assert "run_steps" in projection_source
    assert "audit_events" in projection_source
    assert "column_name::text" in projection_source
    assert "has_table_privilege" not in projection_source
    assert "current_user" in case_source
    assert "session_user" in case_source
    rls_source = inspect.getsource(runner._run_rls_behavior_matrix)
    assert "WRONG_TENANT" in rls_source
    assert "UNSET_TENANT" in rls_source
    assert "42501" in rls_source
    assert "_tenant_deal_projection" in rls_source


def test_v22_erasure_invocation_uses_the_production_positional_contract() -> None:
    _v22, runner = _assets()

    class RecordingCursor:
        def __init__(self) -> None:
            self.parameters: tuple[object, ...] | None = None

        def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
            assert "idis_task7_execute_approved_erasure" in statement
            self.parameters = parameters

    request_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    cursor = RecordingCursor()
    runner._call_erasure(cursor, tenant_id, deal_id, request_id)

    assert cursor.parameters == (request_id, tenant_id, deal_id)


def test_v22_operator_backed_census_executes_no_behavior_case_as_admin() -> None:
    missing = [name for name in _DATABASE_ENV_NAMES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("operator-backed Task-8 disposable PostgreSQL URLs are not exposed")
    v22, runner = _assets()

    receipt = runner.run_disposable_postgres_census(
        admin_database_url=os.environ["IDIS_DATABASE_ADMIN_URL"],
        application_database_url=os.environ["IDIS_DATABASE_URL"],
        erasure_database_url=os.environ["IDIS_ERASURE_DATABASE_URL"],
        artifact_raw=oracle.V22_ARTIFACT.read_bytes(),
    )
    runner.validate_census_receipt(receipt, v22)

    assert receipt["schema_version"] == "task8-v22-postgres-authority-census-receipt-v1"
    assert receipt["result"] == "PASS"
    execution = receipt["database_execution"]
    assert execution["admin_behavior_case_count"] == 0
    assert execution["application_case_execution_count"] > 0
    assert execution["erasure_executor_case_execution_count"] > 0
    manifest = runner.build_census_case_manifest(v22)
    expected_cases = _case_rows(manifest)
    expected_counts = Counter(case["case_kind"] for case in expected_cases)
    assert execution["legal_execution_count"] == expected_counts["LEGAL"]
    assert execution["illegal_rejection_count"] == expected_counts["ILLEGAL"]
    assert execution["direct_sql_rejection_count"] == expected_counts["DIRECT_SQL"]
    rejected_count = expected_counts["ILLEGAL"] + expected_counts["DIRECT_SQL"]
    assert execution["rejected_case_projection_count"] == rejected_count
    assert execution["rejected_case_zero_mutation_count"] == rejected_count
    assert oracle.SHA256_RE.fullmatch(execution["rejected_case_projection_sha256"])
    assert execution["unexpected_outcome_count"] == 0
    assert execution["behavior_role_match_count"] == len(expected_cases)
    assert oracle.SHA256_RE.fullmatch(execution["behavior_role_attestation_sha256"])
    assert execution["rls_behavior_passed"] is True
    assert oracle.SHA256_RE.fullmatch(execution["rls_behavior_sha256"])
    assert execution["dashboard_refusal_sqlstate"] == oracle.ERASURE_REFUSAL_SQLSTATE
    assert execution["dashboard_refusal_reason_code"] == oracle.ERASURE_REFUSAL_CODE
    assert execution["dashboard_refusal_zero_mutation"] is True
    assert execution["forward_lock_race_passed"] is True
    assert execution["reverse_lock_race_passed"] is True
    assert execution["installed_tenant_rls_table_count"] == 69
    assert execution["guard_owner_dashboard_select_columns"] == _guard_dashboard_columns(v22)
    assert execution["guard_owner_dashboard_write_privilege_count"] == 0
    assert execution["erasure_owner_dashboard_select_columns"] == [
        "tenant_id",
        "deal_id",
    ]
    assert execution["erasure_owner_dashboard_write_privilege_count"] == 0
    assert execution["erasure_executor_dashboard_table_privilege_count"] == 0
    assert execution["application_runner_execute_count"] == 0
    assert execution["catalog_assertion_receipt"] == {
        key: value
        for key, value in manifest["catalog_assertions"].items()
        if key
        not in {
            "current_erasure_relation_count",
            "future_erasure_relation_count",
            "complete_erasure_relation_count",
        }
    }
    assert (
        execution["authority_execution_binding"]["bundle_sha256"]
        == (runner.build_hash_bound_authority_execution(v22)["expected_sha256"])
    )
    for field in (
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "complete_execution_sha256",
    ):
        assert oracle.SHA256_RE.fullmatch(execution[field])
    assert receipt["disposable_database_dropped"] is True
