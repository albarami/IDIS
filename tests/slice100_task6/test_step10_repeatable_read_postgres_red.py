"""Real-PostgreSQL RED contracts for the Step-10 completion snapshot."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.run_step import StepName
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodology_extraction import (
    PostgresMethodologyExtractionRepository,
)
from idis.services.runs.execution import (
    WorkerLeaseBoundaryCommitFailedError,
    build_postgres_step_boundary_uow,
)
from idis.services.runs.methodology_postgres_materialization_runtime import _StagedSanadWrite
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task6.test_coverage_projections_postgres_red import (
    DEAL_ID,
    LEASE_OWNER_ID,
    RUN_ID,
    RUN_STEP_ID,
    SANAD_STEP_ID,
    TENANT_ID,
    _seed_first_canonical_snapshot,
    _seed_selected_coverage,
    _seed_terminal_tasks,
    _SeededRun,
)

_REPEATABLE_READ_WRITE_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"


@dataclass
class _BoundAuditSink(PostgresAuditSink):
    """Use the boundary transaction for durable audit; retain failure audit locally."""

    standalone_failures: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event_payload: dict[str, Any]) -> None:
        self.standalone_failures.append(event_payload)


def _prepare_step10(
    database: DisposablePostgres,
) -> tuple[_SeededRun, _StagedSanadWrite, _BoundAuditSink]:
    selection_id = _seed_selected_coverage(database)
    seeded = _seed_terminal_tasks(database, selection_id, found_count=1)
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        repository = PostgresMethodologyExtractionRepository(connection, TENANT_ID)
        repository.complete_execution_step_exact_set(
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            run_step_id=RUN_STEP_ID,
            expected_task_ids=seeded.task_ids,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )
    _seed_first_canonical_snapshot(database, seeded)
    with database.app_engine.begin() as connection:
        set_tenant_local(connection, TENANT_ID)
        factual = PostgresMethodologyExtractionRepository(
            connection, TENANT_ID
        ).load_factual_output_records(deal_id=DEAL_ID, run_id=RUN_ID)
    signature = tuple(
        sorted(
            (str(row["factual_output_record_id"]), str(row["typed_output_sha256"]))
            for row in factual
        )
    )
    sink = _BoundAuditSink()
    staged = _StagedSanadWrite(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        run_step_id=SANAD_STEP_ID,
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
        audit_sink=sink,
        records=(),
        grades=(),
        defects=(),
        factual_signature=signature,
    )
    return seeded, staged, sink


def _invoke_step10_uow(
    database: DisposablePostgres,
    staged: _StagedSanadWrite,
    sink: _BoundAuditSink,
) -> None:
    uow = build_postgres_step_boundary_uow(
        audit_sink=sink,
        connection_factory=database.app_engine.begin,
        lease_seconds=3600,
    )
    uow(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
        boundary="COMPLETE",
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
        mutation=lambda **_kwargs: None,
        native_output_writes=(staged,),
    )


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


def _sqlstate(error: BaseException) -> str | None:
    pending: list[Any] = [error]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str):
                return value
        pending.extend(
            [
                getattr(candidate, "orig", None),
                getattr(candidate, "__cause__", None),
                getattr(candidate, "__context__", None),
            ]
        )
    return None


def _add_statement_capture(engine: Engine, statements: list[str]) -> Any:
    def capture(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(_normalized(statement))

    event.listen(engine, "before_cursor_execute", capture)
    return capture


def test_production_step10_uow_sets_repeatable_read_write_as_exact_first_sql() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seeded, staged, sink = _prepare_step10(database)
        statements: list[str] = []
        capture = _add_statement_capture(database.app_engine, statements)
        try:
            _invoke_step10_uow(database, staged, sink)
        finally:
            event.remove(database.app_engine, "before_cursor_execute", capture)

        assert statements[0] == _REPEATABLE_READ_WRITE_SQL
        assert statements.count(_REPEATABLE_READ_WRITE_SQL) == 1
        assert _normalized(statements[1]).startswith("SET LOCAL idis.tenant_id")
        assert not any("READ ONLY" in statement.upper() for statement in statements)


def test_step10_snapshot_conflict_is_40001_and_rolls_back_whole_boundary() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        seeded, staged, sink = _prepare_step10(database)
        with database.admin_engine.connect() as connection:
            before = (
                connection.execute(
                    text(
                        """
                    SELECT r.last_heartbeat_at, r.lease_expires_at,
                           (SELECT count(*) FROM audit_events
                            WHERE tenant_id=CAST(:tenant AS uuid)) AS audit_count,
                           (SELECT count(*) FROM methodology_coverage_records
                            WHERE tenant_id=CAST(:tenant AS uuid)
                              AND run_id=CAST(:run AS uuid)
                              AND status='answered') AS answered_count
                    FROM runs r
                    WHERE r.tenant_id=CAST(:tenant AS uuid)
                      AND r.run_id=CAST(:run AS uuid)
                    """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID},
                )
                .mappings()
                .one()
            )

        statements: list[str] = []
        snapshot_established = threading.Event()
        admin_committed = threading.Event()
        worker_finished = threading.Event()
        worker_errors: list[BaseException] = []
        barrier_errors: list[str] = []
        capture = _add_statement_capture(database.app_engine, statements)

        def pause_after_first_task6_snapshot(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            normalized = _normalized(statement).upper()
            signature = (
                "SELECT F.FACTUAL_OUTPUT_RECORD_ID, F.RESULT_ID, F.TASK_RECORD_ID, F.OUTPUT_ORDINAL"
            )
            if snapshot_established.is_set() or signature not in normalized:
                return
            snapshot_established.set()
            if not admin_committed.wait(timeout=10):
                barrier_errors.append("admin coverage update did not commit within 10 seconds")

        event.listen(database.app_engine, "after_cursor_execute", pause_after_first_task6_snapshot)

        def run_completion() -> None:
            try:
                _invoke_step10_uow(database, staged, sink)
            except BaseException as error:  # the worker boundary must preserve its cause
                worker_errors.append(error)
            finally:
                worker_finished.set()

        worker = threading.Thread(target=run_completion, name="step10-completion", daemon=True)
        worker.start()
        try:
            assert snapshot_established.wait(timeout=10), (
                "Step-10 did not establish the factual-output snapshot within 10 seconds"
            )
            try:
                with database.admin_engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
                    changed = connection.execute(
                        text(
                            """
                            UPDATE methodology_coverage_records
                            SET updated_at = updated_at + interval '1 second'
                            WHERE tenant_id=CAST(:tenant AS uuid)
                              AND run_id=CAST(:run AS uuid)
                              AND coverage_record_id=:coverage_record_id
                            RETURNING coverage_record_id
                            """
                        ),
                        {
                            "tenant": TENANT_ID,
                            "run": RUN_ID,
                            "coverage_record_id": seeded.first["coverage"],
                        },
                    ).scalar_one()
                    assert str(changed) == str(seeded.first["coverage"])
            finally:
                admin_committed.set()
            assert worker_finished.wait(timeout=15), (
                "Step-10 did not terminate within 15 seconds after the admin commit"
            )
        finally:
            admin_committed.set()
            worker.join(timeout=2)
            event.remove(
                database.app_engine,
                "after_cursor_execute",
                pause_after_first_task6_snapshot,
            )
            event.remove(database.app_engine, "before_cursor_execute", capture)

        assert not worker.is_alive()
        assert barrier_errors == []
        assert len(worker_errors) == 1
        failure = worker_errors[0]
        assert isinstance(failure, WorkerLeaseBoundaryCommitFailedError)
        assert _sqlstate(failure) == "40001"
        # One transaction declaration proves the boundary did not retry after 40001.
        assert statements.count(_REPEATABLE_READ_WRITE_SQL) == 1

        with database.admin_engine.connect() as connection:
            after = (
                connection.execute(
                    text(
                        """
                    SELECT r.last_heartbeat_at, r.lease_expires_at,
                           (SELECT count(*) FROM audit_events
                            WHERE tenant_id=CAST(:tenant AS uuid)) AS audit_count,
                           (SELECT count(*) FROM methodology_coverage_records
                            WHERE tenant_id=CAST(:tenant AS uuid)
                              AND run_id=CAST(:run AS uuid)
                              AND status='answered') AS answered_count,
                           (SELECT status FROM run_steps
                            WHERE tenant_id=CAST(:tenant AS uuid)
                              AND step_id=CAST(:step AS uuid)) AS step_status
                    FROM runs r
                    WHERE r.tenant_id=CAST(:tenant AS uuid)
                      AND r.run_id=CAST(:run AS uuid)
                    """
                    ),
                    {"tenant": TENANT_ID, "run": RUN_ID, "step": SANAD_STEP_ID},
                )
                .mappings()
                .one()
            )

        assert after["last_heartbeat_at"] == before["last_heartbeat_at"]
        assert after["lease_expires_at"] == before["lease_expires_at"]
        assert after["audit_count"] == before["audit_count"]
        assert after["answered_count"] == before["answered_count"] == 0
        assert after["step_status"] == "RUNNING"
        assert len(sink.standalone_failures) == 1
