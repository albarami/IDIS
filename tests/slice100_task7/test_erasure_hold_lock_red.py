"""RED contracts for the Task-7 tenant-scoped erasure/hold advisory lock.

The production legal-hold repository and the approved erasure runner must serialize on
one hash-bound, transaction-scoped PostgreSQL advisory lock.  The static tests in this
module stay runnable before migration 0038 exists.  The PostgreSQL tests use isolated
databases and real independent connections; without the declared PostgreSQL test URL
they follow the repository's standard skip/fail policy.
"""

from __future__ import annotations

import importlib.util
import json
import queue
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Connection, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task7._postgres_support import provision_exact_erasure_principals

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink
from idis.compliance import retention, task7_erasure
from idis.compliance.retention import (
    HoldTarget,
    LegalHold,
    PostgresLegalHoldRegistry,
    lift_hold,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_0038 = (
    _REPO_ROOT
    / "src"
    / "idis"
    / "persistence"
    / "migrations"
    / "versions"
    / "0038_full_strict_v16_receipts_and_edges.py"
)
_V7_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"

_LOCK_CONTRACT = (
    "erasure-tenant-advisory-lock-v1",
    "PostgreSQL-16",
    "pg_catalog.hashtextextended",
    "idis-task7-erasure-tenant-lock-v1:",
    0,
    "pg_catalog.pg_advisory_xact_lock",
)
_LOCK_DOMAIN = _LOCK_CONTRACT[3]
_LOCK_SQL = text(
    "SELECT pg_catalog.pg_advisory_xact_lock("
    "pg_catalog.hashtextextended("
    "'idis-task7-erasure-tenant-lock-v1:' || CAST(:tenant_id AS text), 0))"
)
_RUNNER_SQL = text(
    "SELECT public.idis_task7_execute_approved_erasure("
    "CAST(:request_id AS uuid), CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid))"
)
_HOLD_TENANT_CHANGE_CODE = "TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED"
_RUNNER_BLOCKED_BY_HOLD_CODE = "TASK7_ERASURE_BLOCKED_BY_HOLD"


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_MIGRATION_0038_ABSENT:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact_lock_contract() -> tuple[object, ...]:
    assert _V7_ARTIFACT.is_file(), "TASK7_ERASURE_LOCK_V7_ARTIFACT_ABSENT"
    artifact = json.loads(_V7_ARTIFACT.read_text(encoding="utf-8"))
    rows = artifact["erasure_execution_registry"]["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    value = rows[0]["advisory_lock_tuple"]
    assert isinstance(value, list)
    return tuple(value)


def test_runner_migration_and_legal_hold_repository_share_one_exact_lock_contract() -> None:
    """No layer may choose a lock domain, seed, hash, or acquisition primitive."""

    repository_contract = getattr(
        retention,
        "ERASURE_TENANT_ADVISORY_LOCK_CONTRACT_V1",
        None,
    )
    assert repository_contract == _LOCK_CONTRACT, "TASK7_LEGAL_HOLD_LOCK_CONTRACT_ABSENT_OR_DRIFTED"

    migration = _load_module(_MIGRATION_0038, "task7_erasure_hold_lock_migration")
    migration_contract = getattr(
        migration,
        "ERASURE_TENANT_ADVISORY_LOCK_CONTRACT_V1",
        None,
    )
    assert migration_contract == _LOCK_CONTRACT, "TASK7_RUNNER_LOCK_CONTRACT_DRIFTED"
    assert _artifact_lock_contract() == _LOCK_CONTRACT


def test_migration_0038_is_the_database_enforcement_boundary() -> None:
    """Keep the RED reason explicit even when no PostgreSQL test URL is configured."""

    migration = _load_module(_MIGRATION_0038, "task7_erasure_hold_lock_boundary")
    assert migration.revision == "0038"
    assert migration.down_revision == "0037"


@pytest.fixture(scope="module")
def task7_database() -> Iterator[DisposablePostgres]:
    assert _MIGRATION_0038.is_file()
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            provision_exact_erasure_principals(connection)
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        yield database


def _coordinate() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _seed_request(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> None:
    with database.admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) "
                "VALUES (CAST(:tenant_id AS uuid),'Task 7 hold-lock tenant')"
            ),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES ("
                "CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),"
                "'Task 7 hold-lock deal',:created_at)"
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "created_at": datetime.now(UTC),
            },
        )
        connection.execute(
            text(
                "INSERT INTO erasure_requests ("
                "tenant_id,request_id,deal_id,status,requested_by,requested_at,"
                "reason_hash,reason_length) VALUES ("
                "CAST(:tenant_id AS uuid),CAST(:request_id AS uuid),"
                "CAST(:deal_id AS uuid),'REQUESTED','task7-red',:requested_at,"
                ":reason_hash,1)"
            ),
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "deal_id": deal_id,
                "requested_at": datetime.now(UTC),
                "reason_hash": "a" * 64,
            },
        )
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, tenant_id)
        ordered_uris = tuple(
            str(uri)
            for uri in connection.execute(
                text(
                    "SELECT uri FROM document_artifacts "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND deal_id=CAST(:deal_id AS uuid) "
                    'ORDER BY uri COLLATE "C"'
                ),
                {"tenant_id": tenant_id, "deal_id": deal_id},
            ).scalars()
        )
        staged = connection.execute(
            text(
                "UPDATE erasure_requests SET counts="
                "pg_catalog.jsonb_build_object("
                "'object_rows_reconciled',CAST(:object_rows_reconciled AS bigint)) "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND deal_id=CAST(:deal_id AS uuid) "
                "AND request_id=CAST(:request_id AS uuid)"
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "request_id": request_id,
                "object_rows_reconciled": len(ordered_uris),
            },
        )
        assert staged.rowcount == 1
        lineage = task7_erasure.derive_attempt_lineage(
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
            attempt_ordinal=1,
            prior_status=None,
            prior_attempt_event_id=None,
            prior_attempt_event_hash=None,
        )
        target_sha256 = task7_erasure.object_target_set_sha256(
            tenant_id=tenant_id,
            deal_id=deal_id,
            ordered_uris=ordered_uris,
        )
        attempted = task7_erasure.build_erasure_audit_event(
            tenant_id=tenant_id,
            deal_id=deal_id,
            actor_id="task7-red",
            occurred_at=datetime(2026, 7, 25, tzinfo=UTC),
            lineage=lineage,
            event_type="task7.erasure.attempted",
            object_target_set_sha256=target_sha256,
            counts=None,
        )
        PostgresAuditSink().emit_in_tx(connection, attempted)


def _hold(*, tenant_id: str, deal_id: str) -> LegalHold:
    return LegalHold(
        hold_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        target_type=HoldTarget.DEAL,
        target_id=deal_id,
        reason_hash="b" * 64,
        reason_length=1,
        applied_at=datetime.now(UTC),
        applied_by="task7-red",
    )


@contextmanager
def _app_transaction(database: DisposablePostgres) -> Iterator[Connection]:
    with database.app_engine.begin() as connection:
        yield connection


def _patch_repository_database(
    monkeypatch: pytest.MonkeyPatch,
    database: DisposablePostgres,
    *,
    transaction_factory: Callable[[], Any] | None = None,
) -> None:
    from idis.persistence import db as persistence_db

    factory = transaction_factory or (lambda: _app_transaction(database))
    monkeypatch.setattr(persistence_db, "begin_app_conn", factory)


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.lower().replace('"', "").split())


def _sqlstate(error: BaseException) -> str | None:
    return getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)


def _primary_error_message(error: BaseException) -> str:
    return str(error).splitlines()[0].strip()


def _compiled_lock_sql(engine: Engine) -> str:
    return str(_LOCK_SQL.compile(dialect=engine.dialect))


def _bound_tenant(parameters: object) -> str | None:
    if isinstance(parameters, dict):
        value = parameters.get("tenant_id")
        return str(value) if value is not None else None
    return None


def test_durable_hold_add_and_update_lock_before_each_mutation(
    task7_database: DisposablePostgres,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    _patch_repository_database(monkeypatch, task7_database)

    observed: list[tuple[object | None, str, object]] = []

    def capture(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        observed.append((connection.get_transaction(), statement, _parameters))

    event.listen(task7_database.app_engine, "before_cursor_execute", capture)
    try:
        store = PostgresLegalHoldRegistry()
        hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
        store.add(hold)
        store.update(
            replace(
                hold,
                lifted_at=datetime.now(UTC),
                lifted_by="task7-red",
            )
        )
    finally:
        event.remove(task7_database.app_engine, "before_cursor_execute", capture)

    mutation_indexes = [
        index
        for index, (_transaction_id, statement, _parameters) in enumerate(observed)
        if _normalized_sql(statement).startswith(("insert into legal_holds", "update legal_holds"))
    ]
    expected_lock_sql = _normalized_sql(_compiled_lock_sql(task7_database.app_engine))
    advisory_lock_indexes = [
        index
        for index, (_transaction_id, statement, _parameters) in enumerate(observed)
        if "pg_advisory" in _normalized_sql(statement)
    ]
    lock_indexes = [
        index
        for index, (_transaction_id, statement, parameters) in enumerate(observed)
        if _normalized_sql(statement) == expected_lock_sql
        and _bound_tenant(parameters) == tenant_id
    ]
    assert advisory_lock_indexes == lock_indexes
    assert len(mutation_indexes) == len(lock_indexes) == 2
    assert lock_indexes[0] < mutation_indexes[0] < lock_indexes[1] < mutation_indexes[1]
    transaction_pairs = [
        (observed[lock_index][0], observed[mutation_index][0])
        for lock_index, mutation_index in zip(lock_indexes, mutation_indexes, strict=True)
    ]
    assert all(
        lock_transaction is mutation_transaction
        for lock_transaction, mutation_transaction in transaction_pairs
    )
    assert all(
        lock_transaction is not None
        for lock_transaction, _mutation_transaction in transaction_pairs
    )
    assert transaction_pairs[0][0] is not transaction_pairs[1][0]


def test_lift_composition_locks_before_read_and_updates_in_the_same_transaction(
    task7_database: DisposablePostgres,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
    with task7_database.admin_engine.begin() as connection:
        set_tenant_context(connection, tenant_id)
        _acquire_tenant_lock(connection, tenant_id)
        _insert_hold_sql(connection, hold)

    transaction_serial = 0

    @contextmanager
    def tracked_transaction() -> Iterator[Connection]:
        nonlocal transaction_serial
        transaction_serial += 1
        with task7_database.app_engine.begin() as connection:
            connection.info["task7_hold_transaction"] = transaction_serial
            try:
                yield connection
            finally:
                connection.info.pop("task7_hold_transaction", None)

    _patch_repository_database(
        monkeypatch,
        task7_database,
        transaction_factory=tracked_transaction,
    )
    observed: list[tuple[int | None, str, object]] = []

    def capture(
        connection: Connection,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        observed.append((connection.info.get("task7_hold_transaction"), statement, parameters))

    event.listen(task7_database.app_engine, "before_cursor_execute", capture)
    try:
        lifted = lift_hold(
            TenantContext(
                tenant_id=tenant_id,
                actor_id="task7-red",
                name="Task 7 hold-lock tenant",
                timezone="UTC",
                data_region=None,
            ),
            hold.hold_id,
            InMemoryAuditSink(),
            PostgresLegalHoldRegistry(),
        )
    finally:
        event.remove(task7_database.app_engine, "before_cursor_execute", capture)

    assert lifted.lifted_at is not None
    expected_lock_sql = _normalized_sql(_compiled_lock_sql(task7_database.app_engine))
    advisory_statements = [
        (transaction_id, statement, parameters)
        for transaction_id, statement, parameters in observed
        if "pg_advisory" in _normalized_sql(statement)
    ]
    assert len(advisory_statements) == 1
    assert _normalized_sql(advisory_statements[0][1]) == expected_lock_sql
    assert _bound_tenant(advisory_statements[0][2]) == tenant_id
    relevant = [
        (transaction_id, statement, parameters)
        for transaction_id, statement, parameters in observed
        if "legal_holds" in _normalized_sql(statement)
        or _normalized_sql(statement) == expected_lock_sql
    ]
    select_index = next(
        index
        for index, (_transaction_id, statement, _parameters) in enumerate(relevant)
        if _normalized_sql(statement).startswith("select hold_id")
    )
    update_index = next(
        index
        for index, (_transaction_id, statement, _parameters) in enumerate(relevant)
        if _normalized_sql(statement).startswith("update legal_holds")
    )
    lock_indexes = [
        index
        for index, (_transaction_id, statement, parameters) in enumerate(relevant)
        if _normalized_sql(statement) == expected_lock_sql
        and _bound_tenant(parameters) == tenant_id
    ]
    transaction_ids = {relevant[index][0] for index in (*lock_indexes, select_index, update_index)}
    assert len(lock_indexes) == 1
    assert lock_indexes[0] < select_index < update_index
    assert len(transaction_ids) == 1 and None not in transaction_ids
    assert transaction_serial == 1
    with task7_database.admin_engine.connect() as connection:
        persisted_lift = connection.execute(
            text(
                "SELECT lifted_at,lifted_by FROM legal_holds "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND hold_id=CAST(:hold_id AS uuid)"
            ),
            {"tenant_id": tenant_id, "hold_id": hold.hold_id},
        ).one()
    assert persisted_lift.lifted_at == lifted.lifted_at
    assert persisted_lift.lifted_by == "task7-red"


def _insert_hold_sql(connection: Connection, hold: LegalHold) -> None:
    connection.execute(
        text(
            "INSERT INTO legal_holds ("
            "tenant_id,hold_id,target_type,target_id,reason_hash,reason_length,"
            "applied_at,applied_by,lifted_at,lifted_by) VALUES ("
            "CAST(:tenant_id AS uuid),CAST(:hold_id AS uuid),:target_type,:target_id,"
            ":reason_hash,:reason_length,:applied_at,:applied_by,:lifted_at,:lifted_by)"
        ),
        {
            "tenant_id": hold.tenant_id,
            "hold_id": hold.hold_id,
            "target_type": hold.target_type.value,
            "target_id": hold.target_id,
            "reason_hash": hold.reason_hash,
            "reason_length": hold.reason_length,
            "applied_at": hold.applied_at,
            "applied_by": hold.applied_by,
            "lifted_at": hold.lifted_at,
            "lifted_by": hold.lifted_by,
        },
    )


def _acquire_tenant_lock(connection: Connection, tenant_id: str) -> None:
    connection.execute(_LOCK_SQL, {"tenant_id": tenant_id})


def _hold_count(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    hold_id: str,
) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM legal_holds "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "hold_id": hold_id},
            ).scalar_one()
        )


def _seed_hold_directly(database: DisposablePostgres, hold: LegalHold) -> None:
    """Use the genuine trigger path without a repository-side pre-lock."""

    with database.admin_engine.begin() as connection:
        set_tenant_context(connection, hold.tenant_id)
        _insert_hold_sql(connection, hold)


def _acquire_decoy_lock(connection: Connection, *, kind: str, tenant_id: str) -> None:
    if kind == "wrong_tenant":
        _acquire_tenant_lock(connection, str(uuid.uuid4()))
        return
    assert kind == "wrong_domain"
    connection.execute(
        text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended("
            "'idis-task7-wrong-lock-domain-v1:' || CAST(:tenant_id AS text), 0))"
        ),
        {"tenant_id": tenant_id},
    )


def _execute_direct_hold_mutation(
    database: DisposablePostgres,
    *,
    mutation: str,
    hold: LegalHold,
    backend_pid: queue.Queue[int],
    decoy_lock: str | None = None,
) -> int:
    with database.app_engine.begin() as connection:
        set_tenant_context(connection, hold.tenant_id)
        backend_pid.put(int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()))
        if decoy_lock is not None:
            _acquire_decoy_lock(connection, kind=decoy_lock, tenant_id=hold.tenant_id)
        if mutation == "INSERT":
            result = connection.execute(
                text(
                    "INSERT INTO legal_holds ("
                    "tenant_id,hold_id,target_type,target_id,reason_hash,reason_length,"
                    "applied_at,applied_by,lifted_at,lifted_by) VALUES ("
                    "CAST(:tenant_id AS uuid),CAST(:hold_id AS uuid),:target_type,:target_id,"
                    ":reason_hash,:reason_length,:applied_at,:applied_by,NULL,NULL)"
                ),
                {
                    "tenant_id": hold.tenant_id,
                    "hold_id": hold.hold_id,
                    "target_type": hold.target_type.value,
                    "target_id": hold.target_id,
                    "reason_hash": hold.reason_hash,
                    "reason_length": hold.reason_length,
                    "applied_at": hold.applied_at,
                    "applied_by": hold.applied_by,
                },
            )
        elif mutation == "UPDATE":
            result = connection.execute(
                text(
                    "UPDATE legal_holds SET lifted_at=:lifted_at,lifted_by='task7-red' "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {
                    "tenant_id": hold.tenant_id,
                    "hold_id": hold.hold_id,
                    "lifted_at": datetime.now(UTC),
                },
            )
        else:
            assert mutation == "DELETE"
            result = connection.execute(
                text(
                    "DELETE FROM legal_holds "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {"tenant_id": hold.tenant_id, "hold_id": hold.hold_id},
            )
        return int(result.rowcount)


def _assert_canonical_lock_is_available(
    database: DisposablePostgres,
    *,
    tenant_id: str,
) -> None:
    """Behaviorally prove the trigger retained no session-scoped lock after commit."""

    with database.admin_engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout='250ms'")
        _acquire_tenant_lock(connection, tenant_id)


def _run_direct_mutation_behind_blocker(
    database: DisposablePostgres,
    *,
    mutation: str,
    hold: LegalHold,
    decoy_lock: str | None = None,
) -> int:
    backend_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    executor = ThreadPoolExecutor(max_workers=1)
    future: Future[int] | None = None
    try:
        with database.admin_engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            _acquire_tenant_lock(blocker, hold.tenant_id)
            try:
                future = executor.submit(
                    _execute_direct_hold_mutation,
                    database,
                    mutation=mutation,
                    hold=hold,
                    backend_pid=backend_pid,
                    decoy_lock=decoy_lock,
                )
                _wait_for_advisory_lock_wait(
                    database.admin_engine,
                    backend_pid.get(timeout=5),
                )
                assert not future.done(), "TASK7_DIRECT_HOLD_WRITE_DID_NOT_BLOCK"
            finally:
                blocker_transaction.rollback()
        assert future is not None
        return future.result(timeout=5)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("mutation", ["INSERT", "UPDATE", "DELETE"])
def test_direct_hold_mutation_auto_acquires_canonical_transaction_lock(
    task7_database: DisposablePostgres,
    mutation: str,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
    if mutation != "INSERT":
        _seed_hold_directly(task7_database, hold)

    assert (
        _run_direct_mutation_behind_blocker(
            task7_database,
            mutation=mutation,
            hold=hold,
        )
        == 1
    )
    if mutation == "DELETE":
        assert (
            _hold_count(
                task7_database,
                tenant_id=tenant_id,
                hold_id=hold.hold_id,
            )
            == 0
        )
    else:
        with task7_database.admin_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT lifted_at FROM legal_holds "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "hold_id": hold.hold_id},
            ).one()
        assert (row.lifted_at is not None) is (mutation == "UPDATE")
    _assert_canonical_lock_is_available(task7_database, tenant_id=tenant_id)


@pytest.mark.parametrize("decoy_lock", ["wrong_tenant", "wrong_domain"])
def test_decoy_lock_cannot_bypass_canonical_trigger_lock(
    task7_database: DisposablePostgres,
    decoy_lock: str,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)

    assert (
        _run_direct_mutation_behind_blocker(
            task7_database,
            mutation="INSERT",
            hold=hold,
            decoy_lock=decoy_lock,
        )
        == 1
    )
    assert (
        _hold_count(
            task7_database,
            tenant_id=tenant_id,
            hold_id=hold.hold_id,
        )
        == 1
    )
    _assert_canonical_lock_is_available(task7_database, tenant_id=tenant_id)


def test_hold_tenant_change_is_rejected_even_when_both_tenant_locks_are_held(
    task7_database: DisposablePostgres,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
    _seed_hold_directly(task7_database, hold)

    replacement_tenant_id = str(uuid.uuid4())
    with (
        pytest.raises(DBAPIError) as error,
        task7_database.admin_engine.begin() as connection,
    ):
        set_tenant_context(connection, tenant_id)
        _acquire_tenant_lock(connection, tenant_id)
        _acquire_tenant_lock(connection, replacement_tenant_id)
        connection.execute(
            text(
                "UPDATE legal_holds SET tenant_id=CAST(:replacement_tenant_id AS uuid) "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND hold_id=CAST(:hold_id AS uuid)"
            ),
            {
                "replacement_tenant_id": replacement_tenant_id,
                "tenant_id": tenant_id,
                "hold_id": hold.hold_id,
            },
        )
    assert _primary_error_message(error.value.orig) == _HOLD_TENANT_CHANGE_CODE
    assert _sqlstate(error.value.orig) == "23514"
    assert (
        _hold_count(
            task7_database,
            tenant_id=tenant_id,
            hold_id=hold.hold_id,
        )
        == 1
    )
    assert (
        _hold_count(
            task7_database,
            tenant_id=replacement_tenant_id,
            hold_id=hold.hold_id,
        )
        == 0
    )


def _runner_parameters(*, tenant_id: str, deal_id: str, request_id: str) -> dict[str, str]:
    return {
        "request_id": request_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
    }


def _open_executor_transaction(
    database: DisposablePostgres,
) -> tuple[Connection, Any]:
    connection = database.admin_engine.connect()
    transaction = connection.begin()
    connection.exec_driver_sql("SET SESSION AUTHORIZATION idis_erasure_executor")
    identity = connection.exec_driver_sql("SELECT session_user,current_user").one()
    assert tuple(identity) == ("idis_erasure_executor", "idis_erasure_executor")
    return connection, transaction


def _close_executor_connection(connection: Connection, transaction: Any) -> None:
    try:
        if transaction.is_active:
            # Backend-termination RED deliberately makes rollback network-fatal.
            with suppress(DBAPIError):
                transaction.rollback()
        with suppress(DBAPIError):
            connection.invalidate()
    finally:
        connection.close()


def _invoke_runner(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
    backend_pid: queue.Queue[int] | None = None,
) -> tuple[str, object]:
    connection, transaction = _open_executor_transaction(database)
    try:
        if backend_pid is not None:
            backend_pid.put(int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()))
        try:
            value = connection.execute(
                _RUNNER_SQL,
                _runner_parameters(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    request_id=request_id,
                ),
            ).scalar_one()
        except DBAPIError as error:
            if transaction.is_active:
                with suppress(DBAPIError):
                    transaction.rollback()
            return "rejected", (
                _primary_error_message(error.orig),
                _sqlstate(error.orig),
            )
        transaction.commit()
        return "committed", value
    finally:
        _close_executor_connection(connection, transaction)


def _wait_for_advisory_lock_wait(engine: Engine, backend_pid: int) -> None:
    deadline = time.monotonic() + 5.0
    observed: tuple[object, object] | None = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT wait_event_type,wait_event FROM pg_catalog.pg_stat_activity "
                    "WHERE pid=:backend_pid"
                ),
                {"backend_pid": backend_pid},
            ).one_or_none()
        if row is not None:
            observed = (row.wait_event_type, row.wait_event)
            if row.wait_event_type == "Lock" and "advisory" in str(row.wait_event).lower():
                return
        time.sleep(0.02)
    pytest.fail(f"TASK7_EXPECTED_ADVISORY_LOCK_WAIT_NOT_OBSERVED:{observed!r}")


def _deal_count(database: DisposablePostgres, *, tenant_id: str, deal_id: str) -> int:
    with database.admin_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM deals WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND deal_id=CAST(:deal_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "deal_id": deal_id},
            ).scalar_one()
        )


def _runner_state(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> tuple[int, tuple[object, ...]]:
    with database.admin_engine.connect() as connection:
        request = connection.execute(
            text(
                "SELECT status,executed_by,executed_at,counts FROM erasure_requests "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                "AND deal_id=CAST(:deal_id AS uuid) "
                "AND request_id=CAST(:request_id AS uuid)"
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "request_id": request_id,
            },
        ).one()
    return (
        _deal_count(database, tenant_id=tenant_id, deal_id=deal_id),
        tuple(request),
    )


def test_committed_hold_wins_race_and_waiting_erasure_rechecks_before_deleting(
    task7_database: DisposablePostgres,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
    hold_mutated = threading.Event()
    permit_hold_commit = threading.Event()

    @contextmanager
    def paused_hold_transaction() -> Iterator[Connection]:
        with task7_database.app_engine.begin() as connection:
            yield connection
            hold_mutated.set()
            assert permit_hold_commit.wait(timeout=5), "TASK7_HOLD_COMMIT_RELEASE_TIMEOUT"

    _patch_repository_database(
        monkeypatch,
        task7_database,
        transaction_factory=paused_hold_transaction,
    )

    runner_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        hold_future = executor.submit(PostgresLegalHoldRegistry().add, hold)
        assert hold_mutated.wait(timeout=5), "TASK7_HOLD_WRITE_DID_NOT_REACH_PRECOMMIT"
        runner_future = executor.submit(
            _invoke_runner,
            task7_database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
            backend_pid=runner_pid,
        )
        _wait_for_advisory_lock_wait(task7_database.admin_engine, runner_pid.get(timeout=5))
        permit_hold_commit.set()
        hold_future.result(timeout=5)
        outcome, detail = runner_future.result(timeout=5)

    assert outcome == "rejected"
    assert isinstance(detail, tuple) and len(detail) == 2
    message, sqlstate = detail
    assert message == _RUNNER_BLOCKED_BY_HOLD_CODE
    assert sqlstate == "23514"
    assert _deal_count(task7_database, tenant_id=tenant_id, deal_id=deal_id) == 1


@pytest.mark.parametrize("boundary", ["commit", "rollback"])
def test_erasure_lock_blocks_hold_write_until_transaction_boundary(
    task7_database: DisposablePostgres,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)
    hold_pid: queue.Queue[int] = queue.Queue(maxsize=1)

    @contextmanager
    def traced_hold_transaction() -> Iterator[Connection]:
        with task7_database.app_engine.begin() as connection:
            hold_pid.put(int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()))
            yield connection

    _patch_repository_database(
        monkeypatch,
        task7_database,
        transaction_factory=traced_hold_transaction,
    )

    runner_connection, runner_transaction = _open_executor_transaction(task7_database)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        runner_connection.execute(
            _RUNNER_SQL,
            _runner_parameters(
                tenant_id=tenant_id,
                deal_id=deal_id,
                request_id=request_id,
            ),
        ).scalar_one()
        future: Future[None] = executor.submit(PostgresLegalHoldRegistry().add, hold)
        _wait_for_advisory_lock_wait(task7_database.admin_engine, hold_pid.get(timeout=5))
        if boundary == "commit":
            runner_transaction.commit()
        else:
            runner_transaction.rollback()
        future.result(timeout=5)
    finally:
        _close_executor_connection(runner_connection, runner_transaction)
        executor.shutdown(wait=True, cancel_futures=True)

    expected_deals = 0 if boundary == "commit" else 1
    assert _deal_count(task7_database, tenant_id=tenant_id, deal_id=deal_id) == expected_deals
    with task7_database.admin_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM legal_holds "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "hold_id": hold.hold_id},
            ).scalar_one()
            == 1
        )


def test_lock_timeout_leaves_zero_hold_mutation(
    task7_database: DisposablePostgres,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    hold = _hold(tenant_id=tenant_id, deal_id=deal_id)

    @contextmanager
    def short_timeout_transaction() -> Iterator[Connection]:
        with task7_database.app_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout='100ms'")
            yield connection

    _patch_repository_database(
        monkeypatch,
        task7_database,
        transaction_factory=short_timeout_transaction,
    )

    with task7_database.admin_engine.connect() as blocker:
        transaction = blocker.begin()
        _acquire_tenant_lock(blocker, tenant_id)
        try:
            with pytest.raises(IdisHttpError) as error:
                PostgresLegalHoldRegistry().add(hold)
            assert error.value.code == "LEGAL_HOLD_WRITE_FAILED"
        finally:
            transaction.rollback()

    with task7_database.admin_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM legal_holds "
                    "WHERE tenant_id=CAST(:tenant_id AS uuid) "
                    "AND hold_id=CAST(:hold_id AS uuid)"
                ),
                {"tenant_id": tenant_id, "hold_id": hold.hold_id},
            ).scalar_one()
            == 0
        )


def test_runner_advisory_lock_timeout_has_exact_sqlstate_and_zero_deletion(
    task7_database: DisposablePostgres,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    before = _runner_state(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )

    with task7_database.admin_engine.connect() as blocker:
        blocker_transaction = blocker.begin()
        _acquire_tenant_lock(blocker, tenant_id)
        try:
            runner_connection, runner_transaction = _open_executor_transaction(task7_database)
            try:
                runner_connection.exec_driver_sql("SET LOCAL statement_timeout='100ms'")
                with pytest.raises(DBAPIError) as error:
                    runner_connection.execute(
                        _RUNNER_SQL,
                        _runner_parameters(
                            tenant_id=tenant_id,
                            deal_id=deal_id,
                            request_id=request_id,
                        ),
                    ).scalar_one()
                assert _sqlstate(error.value.orig) == "57014"
            finally:
                _close_executor_connection(runner_connection, runner_transaction)
        finally:
            blocker_transaction.rollback()

    assert (
        _runner_state(
            task7_database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        == before
    )
    _assert_canonical_lock_is_available(task7_database, tenant_id=tenant_id)


def test_runner_backend_termination_while_waiting_has_exact_terminal_signal_and_zero_deletion(
    task7_database: DisposablePostgres,
) -> None:
    tenant_id, deal_id, request_id = _coordinate()
    _seed_request(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    before = _runner_state(
        task7_database,
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
    )
    backend_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    executor = ThreadPoolExecutor(max_workers=1)
    future: Future[tuple[str, object]] | None = None
    try:
        with task7_database.admin_engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            _acquire_tenant_lock(blocker, tenant_id)
            try:
                future = executor.submit(
                    _invoke_runner,
                    task7_database,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    request_id=request_id,
                    backend_pid=backend_pid,
                )
                waiting_pid = backend_pid.get(timeout=5)
                _wait_for_advisory_lock_wait(task7_database.admin_engine, waiting_pid)
                with task7_database.admin_engine.begin() as terminator:
                    assert (
                        terminator.execute(
                            text("SELECT pg_catalog.pg_terminate_backend(:backend_pid)"),
                            {"backend_pid": waiting_pid},
                        ).scalar_one()
                        is True
                    )
                outcome, detail = future.result(timeout=5)
            finally:
                blocker_transaction.rollback()
        assert future is not None
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    assert outcome == "rejected"
    assert isinstance(detail, tuple) and len(detail) == 2
    message, sqlstate = detail
    # psycopg may surface administrator termination as 57P01 or an exact socket-close terminal.
    assert sqlstate == "57P01" or (
        sqlstate is None and message == "server closed the connection unexpectedly"
    ), detail
    assert (
        _runner_state(
            task7_database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            request_id=request_id,
        )
        == before
    )
    _assert_canonical_lock_is_available(task7_database, tenant_id=tenant_id)
