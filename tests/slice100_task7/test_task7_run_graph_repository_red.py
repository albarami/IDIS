"""RED-first contracts for the seven-table Task 7 run-graph repository."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"

TENANT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
LEASE_OWNER_ID = "44444444-4444-4444-8444-444444444444"
CONTRACT_HASH = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"

_TABLES = (
    "run_input_bindings",
    "run_output_objects",
    "run_step_outputs",
    "run_applicability_decisions",
    "run_expected_consumer_edges",
    "run_consumer_receipts",
    "deliverable_manifest_references",
)
_ROW_TYPES = {
    "run_input_bindings": "RunInputBindingRecord",
    "run_output_objects": "RunOutputObjectRecord",
    "run_step_outputs": "RunStepOutputRecord",
    "run_applicability_decisions": "RunApplicabilityDecisionRecord",
    "run_expected_consumer_edges": "RunExpectedConsumerEdgeRecord",
    "run_consumer_receipts": "RunConsumerReceiptRecord",
    "deliverable_manifest_references": "DeliverableManifestReferenceRecord",
}
_INSERT_METHODS = {table: f"insert_{table[:-1]}" for table in _TABLES}
_INSERT_METHODS["run_applicability_decisions"] = "insert_run_applicability_decision"
_INSERT_METHODS["run_expected_consumer_edges"] = "insert_run_expected_consumer_edge"
_INSERT_METHODS["deliverable_manifest_references"] = "insert_deliverable_manifest_reference"
_LIST_METHODS = {table: f"list_{table}" for table in _TABLES}
_TOTAL_ORDERS = {
    "run_input_bindings": ("source_object_kind, source_lineage_object_id, binding_id"),
    "run_output_objects": ("output_kind, native_identity_sha256, output_object_id"),
    "run_step_outputs": ("step_name, subrole, output_kind, output_ordinal, step_output_id"),
    "run_applicability_decisions": (
        "decision_phase, decision_kind, subject_kind, subject_key, "
        "contract_rule_id, contract_rule_version, applicability_decision_id"
    ),
    "run_expected_consumer_edges": (
        "consumer_step_name, consumer_subrole, usage_kind, contract_rule_id, "
        "contract_rule_version, expected_edge_id"
    ),
    "run_consumer_receipts": ("consumer_step_name, consumer_subrole, expected_edge_id, receipt_id"),
    "deliverable_manifest_references": (
        "manifest_deliverable_id, member_kind, catalog_ordinal NULLS LAST, manifest_reference_id"
    ),
}
_LEASE_TABLES = frozenset(_TABLES) - {"run_input_bindings"}


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.persistence.repositories.task7_run_graph")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.persistence.repositories.task7_run_graph":
            raise
        pytest.fail(
            "Task 7 run-graph repository module is missing",
            pytrace=False,
        )


def _artifact_write_columns() -> dict[str, tuple[str, ...]]:
    artifact = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    rows = {
        row["table_name"]: row
        for row in artifact["current_ddl_registry"]["rows"]
        if row["table_name"] in _TABLES
    }
    result: dict[str, tuple[str, ...]] = {}
    for table in _TABLES:
        columns: list[str] = []
        for definition in rows[table]["column_tuple"]:
            column_name = definition.split(None, 1)[0]
            if " GENERATED ALWAYS " in definition or column_name == "created_at":
                continue
            columns.append(column_name)
        result[table] = tuple(columns)
    return result


class _Row:
    def __init__(self, values: dict[str, Any]) -> None:
        self._mapping = dict(values)


class _Result:
    def __init__(
        self,
        *,
        rows: list[_Row] | None = None,
        scalar: object | None = None,
    ) -> None:
        self._rows = list(rows or [])
        self._scalar = scalar
        self.rowcount = 1

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def one_or_none(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[_Row]:
        return list(self._rows)


class _Connection:
    def __init__(
        self,
        rows_by_table: dict[str, dict[str, Any]] | None = None,
        *,
        run_scope_result: int | None = 1,
        fail_insert: bool = False,
    ) -> None:
        self.rows_by_table = dict(rows_by_table or {})
        self.run_scope_result = run_scope_result
        self.fail_insert = fail_insert
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: object,
        params: dict[str, Any] | None = None,
    ) -> _Result:
        sql = " ".join(str(statement).split())
        values = dict(params or {})
        self.calls.append((sql, values))
        if self.fail_insert and sql.startswith("INSERT INTO "):
            raise SQLAlchemyError("simulated durable insert failure")
        if "SELECT 1 FROM runs" in sql:
            return _Result(scalar=self.run_scope_result)
        if sql.startswith("SELECT "):
            for table, row in self.rows_by_table.items():
                if f"FROM {table}" in sql:
                    return _Result(rows=[_Row(row)])
        return _Result()


def _record_payloads(module: ModuleType) -> dict[str, object]:
    consumed_at = datetime(2026, 7, 23, 10, 30, tzinfo=UTC)
    return {
        "run_input_bindings": module.RunInputBindingRecord(
            binding_id="00000000-0000-4000-8000-000000000001",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            source_lineage_object_id="00000000-0000-4000-8000-000000000002",
            source_object_kind="SPAN",
            source_disposition="EVIDENCE_BEARING",
            binding_purpose="EVIDENCE",
            corpus_receipt_id="00000000-0000-4000-8000-000000000003",
            selected_source_version="v1",
            selected_identity_sha256="a" * 64,
            selected_content_sha256="b" * 64,
            corpus_receipt_sha256="c" * 64,
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=CONTRACT_HASH,
            binding_sha256="d" * 64,
        ),
        "run_output_objects": module.RunOutputObjectRecord(
            output_object_id="00000000-0000-4000-8000-000000000004",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            output_class="STEP_RECEIPT",
            output_kind="EXTRACT_RECEIPT_V1",
            output_schema_version="1",
            native_identity_sha256="a" * 64,
            native_output_sha256="b" * 64,
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=CONTRACT_HASH,
            write_origin="RUNTIME",
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        ),
        "run_step_outputs": module.RunStepOutputRecord(
            step_output_id="00000000-0000-4000-8000-000000000005",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            step_id="00000000-0000-4000-8000-000000000006",
            output_object_id="00000000-0000-4000-8000-000000000004",
            step_name="EXTRACT",
            subrole="__step__",
            output_kind="EXTRACT_RECEIPT_V1",
            output_ordinal=0,
            semantic_rule_version="2",
            semantic_rule_hash="e" * 64,
            summary_sha256="f" * 64,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        ),
        "run_applicability_decisions": module.RunApplicabilityDecisionRecord(
            applicability_decision_id="00000000-0000-4000-8000-000000000007",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            decision_phase="STEP_16",
            decision_kind="PROVIDER_APPLICABILITY",
            subject_kind="PROVIDER",
            subject_key="finnhub",
            outcome="NOT_APPLICABLE",
            upstream_input_binding_id=("00000000-0000-4000-8000-000000000001"),
            upstream_output_object_id=None,
            deciding_step_output_id=("00000000-0000-4000-8000-000000000005"),
            contract_rule_id="E029",
            contract_rule_version="2",
            contract_rule_hash="a" * 64,
            evidence_sha256="b" * 64,
            decision_sha256="c" * 64,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        ),
        "run_expected_consumer_edges": module.RunExpectedConsumerEdgeRecord(
            expected_edge_id="00000000-0000-4000-8000-000000000008",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            producer_input_binding_id=("00000000-0000-4000-8000-000000000001"),
            producer_output_object_id=None,
            consumer_step_name="ANALYSIS",
            consumer_subrole="financial_agent",
            usage_kind="EXTERNAL_CONTEXT",
            applicability_decision_id=("00000000-0000-4000-8000-000000000007"),
            contract_rule_id="E029",
            contract_rule_version="2",
            contract_rule_hash="a" * 64,
            edge_tuple_sha256="d" * 64,
            materialization_origin="WORKER",
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        ),
        "run_consumer_receipts": module.RunConsumerReceiptRecord(
            receipt_id="00000000-0000-4000-8000-000000000009",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            expected_edge_id="00000000-0000-4000-8000-000000000008",
            consumer_step_output_id=("00000000-0000-4000-8000-00000000000a"),
            consumer_output_object_id=("00000000-0000-4000-8000-00000000000b"),
            consumer_step_name="ANALYSIS",
            consumer_subrole="financial_agent",
            usage_evidence_sha256="e" * 64,
            consumed_at=consumed_at,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=7,
        ),
        "deliverable_manifest_references": (
            module.DeliverableManifestReferenceRecord(
                manifest_reference_id=("00000000-0000-4000-8000-00000000000c"),
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                manifest_deliverable_id=("00000000-0000-4000-8000-00000000000d"),
                manifest_output_object_id=("00000000-0000-4000-8000-00000000000e"),
                run_output_object_id=("00000000-0000-4000-8000-00000000000f"),
                member_kind="ARTIFACT",
                catalog_ordinal=3,
                member_step_output_id=("00000000-0000-4000-8000-000000000010"),
                member_output_object_id=("00000000-0000-4000-8000-000000000011"),
                member_identity_sha256="a" * 64,
                member_value_sha256="b" * 64,
                member_content_sha256="c" * 64,
                reference_sha256="d" * 64,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
        ),
    }


def _repository(
    module: ModuleType,
    rows: dict[str, object],
    **connection_options: object,
) -> tuple[object, _Connection]:
    connection = _Connection(
        {table: asdict(row) for table, row in rows.items()},
        **connection_options,
    )
    repository = module.PostgresTask7RunGraphRepository(
        connection,
        TENANT_ID,
    )
    return repository, connection


def _code(error: BaseException) -> str | None:
    return getattr(error, "code", None)


def test_row_types_and_write_columns_equal_the_reviewed_v5_artifact() -> None:
    module = _module()
    expected_columns = _artifact_write_columns()

    assert tuple(module.TASK7_RUN_GRAPH_SPECS) == _TABLES
    for table in _TABLES:
        spec = module.TASK7_RUN_GRAPH_SPECS[table]
        row_type = getattr(module, _ROW_TYPES[table])
        assert spec.row_type is row_type
        assert spec.write_columns == expected_columns[table]
        assert tuple(field.name for field in fields(row_type)) == expected_columns[table]


def test_repository_has_no_in_memory_update_delete_or_dynamic_table_api() -> None:
    module = _module()
    rows = _record_payloads(module)
    repository, _connection = _repository(module, rows)

    assert "InMemory" not in inspect.getsource(module)
    assert not hasattr(module, "get_task7_run_graph_repository")
    public_methods = {
        name
        for name in dir(repository)
        if not name.startswith("_") and callable(getattr(repository, name))
    }
    assert not {
        name
        for name in public_methods
        if name.startswith(("update", "delete", "upsert", "execute"))
    }
    assert set(_INSERT_METHODS.values()) <= public_methods
    assert set(_LIST_METHODS.values()) <= public_methods


@pytest.mark.parametrize("table", _TABLES)
def test_each_collection_read_is_scope_bound_and_totally_ordered(table: str) -> None:
    module = _module()
    rows = _record_payloads(module)
    repository, connection = _repository(module, rows)

    result = getattr(repository, _LIST_METHODS[table])(
        deal_id=DEAL_ID,
        run_id=RUN_ID,
    )

    assert result == (rows[table],)
    sql, params = connection.calls[-1]
    assert f"FROM {table}" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "deal_id = CAST(:deal_id AS uuid)" in sql
    assert "run_id = CAST(:run_id AS uuid)" in sql
    assert f"ORDER BY {_TOTAL_ORDERS[table]}" in sql
    assert params == {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
    }


@pytest.mark.parametrize("table", _TABLES)
def test_each_insert_is_append_only_scope_bound_and_exactly_reloaded(
    table: str,
) -> None:
    module = _module()
    rows = _record_payloads(module)
    repository, connection = _repository(module, rows)
    record = rows[table]

    persisted = getattr(repository, _INSERT_METHODS[table])(record)

    assert persisted == record
    statements = [sql for sql, _params in connection.calls]
    insert = next(sql for sql in statements if sql.startswith(f"INSERT INTO {table}"))
    assert "ON CONFLICT DO NOTHING" in insert
    assert all(not sql.startswith(("UPDATE ", "DELETE ")) for sql in statements)
    reload_sql, reload_params = connection.calls[-1]
    primary_key = module.TASK7_RUN_GRAPH_SPECS[table].primary_key
    assert f"FROM {table}" in reload_sql
    assert f"ORDER BY {primary_key}" in reload_sql
    assert reload_params["tenant_id"] == TENANT_ID
    assert reload_params["deal_id"] == DEAL_ID
    assert reload_params["run_id"] == RUN_ID
    if table in _LEASE_TABLES:
        assert any("lease_expires_at > statement_timestamp()" in sql for sql in statements)
    for column in _artifact_write_columns()[table]:
        assert column in insert


def test_admission_edge_uses_zero_token_without_forging_worker_context() -> None:
    module = _module()
    rows = _record_payloads(module)
    rows["run_expected_consumer_edges"] = replace(
        rows["run_expected_consumer_edges"],
        materialization_origin="ADMISSION",
        lease_owner_id=None,
        lease_fencing_token=0,
    )
    repository, connection = _repository(module, rows)

    persisted = repository.insert_run_expected_consumer_edge(rows["run_expected_consumer_edges"])

    assert persisted == rows["run_expected_consumer_edges"]
    statements = [sql for sql, _params in connection.calls]
    assert any("SELECT 1 FROM runs" in sql for sql in statements)
    assert all("idis.worker_lease_owner_id" not in sql for sql in statements)


def test_cross_tenant_record_is_rejected_before_insert() -> None:
    module = _module()
    rows = _record_payloads(module)
    repository, connection = _repository(module, rows)
    cross_tenant = replace(
        rows["run_input_bindings"],
        tenant_id=OTHER_TENANT_ID,
    )

    with pytest.raises(module.Task7RunGraphRepositoryError) as caught:
        repository.insert_run_input_binding(cross_tenant)

    assert _code(caught.value) == "TASK7_RUN_GRAPH_SCOPE_MISMATCH"
    assert all(not sql.startswith("INSERT INTO ") for sql, _params in connection.calls)


def test_invalid_or_expired_worker_fence_fails_closed_before_insert() -> None:
    module = _module()
    rows = _record_payloads(module)
    invalid = replace(
        rows["run_step_outputs"],
        lease_fencing_token=0,
    )
    repository, connection = _repository(module, rows)

    with pytest.raises(module.Task7RunGraphRepositoryError) as caught:
        repository.insert_run_step_output(invalid)

    assert _code(caught.value) == "WORKER_LEASE_FENCED"
    assert all(not sql.startswith("INSERT INTO ") for sql, _params in connection.calls)

    repository, connection = _repository(
        module,
        rows,
        run_scope_result=None,
    )
    with pytest.raises(module.Task7RunGraphRepositoryError) as caught:
        repository.insert_run_step_output(rows["run_step_outputs"])
    assert _code(caught.value) == "WORKER_LEASE_FENCED"
    assert all(not sql.startswith("INSERT INTO ") for sql, _params in connection.calls)


@pytest.mark.parametrize("table", _TABLES)
def test_reload_drift_for_every_table_fails_with_one_stable_code(table: str) -> None:
    module = _module()
    rows = _record_payloads(module)
    stored_rows = dict(rows)
    primary_key = module.TASK7_RUN_GRAPH_SPECS[table].primary_key
    stored_rows[table] = replace(
        rows[table],
        **{primary_key: "ffffffff-ffff-4fff-8fff-ffffffffffff"},
    )
    repository, _connection = _repository(module, stored_rows)

    with pytest.raises(module.Task7RunGraphRepositoryError) as caught:
        getattr(repository, _INSERT_METHODS[table])(rows[table])

    assert _code(caught.value) == "TASK7_RUN_GRAPH_ROW_MISMATCH"


def test_durable_sql_failure_is_mapped_to_stable_fail_closed_code() -> None:
    module = _module()
    rows = _record_payloads(module)
    repository, connection = _repository(
        module,
        rows,
        fail_insert=True,
    )

    with pytest.raises(module.Task7RunGraphRepositoryError) as caught:
        repository.insert_run_input_binding(rows["run_input_bindings"])

    assert _code(caught.value) == "TASK7_RUN_GRAPH_PERSISTENCE_REJECTED"
    assert any(
        sql.startswith("INSERT INTO run_input_bindings") for sql, _params in connection.calls
    )
