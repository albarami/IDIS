"""Postgres/RLS integration coverage for Slice75B run lifecycle behavior."""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.services.runs.lifecycle import RunLifecycleService

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"


def test_migration_0018_only_targets_named_runs_status_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration 0018 must not dynamically drop arbitrary status CHECK constraints."""
    migration = importlib.import_module(
        "idis.persistence.migrations.versions.0018_runs_cancel_state"
    )
    executed_sql: list[str] = []

    def capture_execute(sql: str) -> None:
        executed_sql.append(sql)

    monkeypatch.setattr(migration.op, "execute", capture_execute)

    migration.upgrade()
    migration.downgrade()

    joined_sql = "\n".join(executed_sql)
    assert "pg_constraint" not in joined_sql
    assert "EXECUTE format" not in joined_sql
    assert "DROP CONSTRAINT IF EXISTS %I" not in joined_sql
    assert "ALTER TABLE runs ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ" in joined_sql
    assert "ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check" in joined_sql
    assert "CANCELLED" in joined_sql
    assert "UPDATE runs SET status = 'FAILED' WHERE status = 'CANCELLED'" in joined_sql
    assert "CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'))" in joined_sql
    assert "ALTER TABLE runs DROP COLUMN IF EXISTS cancel_requested_at" in joined_sql


def test_postgres_try_requeue_failed_sql_clears_cancel_requested_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres retry/requeue SQL must clear stale cancellation metadata."""
    from idis.persistence.repositories import runs as runs_repo_module

    class _FakeResult:
        def fetchone(self) -> tuple[str]:
            return ("run-id",)

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed_sql: list[str] = []

        def execute(self, statement: object, params: dict[str, str] | None = None) -> _FakeResult:
            self.executed_sql.append(str(statement))
            return _FakeResult()

    conn = _FakeConnection()
    monkeypatch.setattr(runs_repo_module, "set_tenant_local", lambda *_args: None)
    runs_repo = PostgresRunsRepository(conn, TENANT_A_ID)

    assert runs_repo.try_requeue_failed(str(uuid.uuid4())) is True

    joined_sql = "\n".join(conn.executed_sql)
    assert "UPDATE runs" in joined_sql
    assert "cancel_requested_at = NULL" in joined_sql


def test_postgres_try_complete_running_sql_only_completes_running_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution finalization SQL must only complete runs still in RUNNING."""
    from idis.persistence.repositories import runs as runs_repo_module

    class _FakeResult:
        def fetchone(self) -> tuple[str]:
            return ("run-id",)

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed_sql: list[str] = []

        def execute(self, statement: object, params: dict[str, str] | None = None) -> _FakeResult:
            self.executed_sql.append(str(statement))
            return _FakeResult()

    conn = _FakeConnection()
    monkeypatch.setattr(runs_repo_module, "set_tenant_local", lambda *_args: None)
    runs_repo = PostgresRunsRepository(conn, TENANT_A_ID)

    assert (
        runs_repo.try_complete_running(
            str(uuid.uuid4()),
            status="SUCCEEDED",
            finished_at="2026-05-27T00:01:00Z",
        )
        is True
    )

    joined_sql = "\n".join(conn.executed_sql)
    assert "UPDATE runs" in joined_sql
    assert "WHERE run_id = :run_id AND status = 'RUNNING'" in joined_sql


def test_postgres_try_complete_active_sql_completes_queued_or_running_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight-fail guard must only complete runs still QUEUED or RUNNING."""
    from idis.persistence.repositories import runs as runs_repo_module

    class _FakeResult:
        def fetchone(self) -> tuple[str]:
            return ("run-id",)

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed_sql: list[str] = []

        def execute(self, statement: object, params: dict[str, str] | None = None) -> _FakeResult:
            self.executed_sql.append(str(statement))
            return _FakeResult()

    conn = _FakeConnection()
    monkeypatch.setattr(runs_repo_module, "set_tenant_local", lambda *_args: None)
    runs_repo = PostgresRunsRepository(conn, TENANT_A_ID)

    assert (
        runs_repo.try_complete_active(
            str(uuid.uuid4()),
            status="FAILED",
            finished_at="2026-05-27T00:01:00Z",
        )
        is True
    )

    joined_sql = "\n".join(conn.executed_sql)
    assert "UPDATE runs" in joined_sql
    assert "WHERE run_id = :run_id AND status IN ('QUEUED', 'RUNNING')" in joined_sql
    assert "RETURNING run_id" in joined_sql


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"

    if not admin_url or not app_url:
        msg = f"PostgreSQL integration tests require {ADMIN_URL_ENV} and {APP_URL_ENV} env vars"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def admin_engine() -> Generator[Engine, None, None]:
    """Create admin engine for migrations and cleanup."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    """Create app engine for RLS-scoped repository operations."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_app_engine, reset_engines

    engine = get_app_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def migrated_db(admin_engine: Engine) -> Generator[None, None, None]:
    """Run Alembic migrations to head for direct Slice75B schema validation."""
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_tables(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    """Clean Slice75B tables around each Postgres integration test."""
    _truncate_lifecycle_tables(admin_engine)
    yield
    _truncate_lifecycle_tables(admin_engine)


def _truncate_lifecycle_tables(admin_engine: Engine) -> None:
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE run_steps, runs, deals CASCADE"))


def _insert_deal(conn: object, *, tenant_id: str, deal_id: str) -> None:
    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO deals (deal_id, tenant_id, name, created_at)
            VALUES (:deal_id, :tenant_id, :name, :created_at)
            """
        ),
        {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "name": f"Deal {deal_id[:8]}",
            "created_at": now,
        },
    )


def _insert_run(
    conn: object,
    *,
    tenant_id: str,
    run_id: str,
    deal_id: str,
    status: str,
) -> None:
    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO runs
                (run_id, tenant_id, deal_id, mode, status, started_at, created_at)
            VALUES
                (:run_id, :tenant_id, :deal_id, 'FULL', :status, :started_at, :created_at)
            """
        ),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "status": status,
            "started_at": now,
            "created_at": now,
        },
    )


def test_migration_0018_allows_cancelled_status_and_cancel_requested_at(
    app_engine: Engine,
    clean_tables: None,
) -> None:
    """Alembic head includes 0018, CANCELLED status, and cancel_requested_at."""
    from idis.persistence.db import set_tenant_local

    deal_id = str(uuid.uuid4())
    cancelled_run_id = str(uuid.uuid4())
    invalid_run_id = str(uuid.uuid4())

    with app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A_ID)
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0018"

        cancel_column_count = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_name = 'runs'
                  AND column_name = 'cancel_requested_at'
                """
            )
        ).scalar_one()
        assert cancel_column_count == 1

        _insert_deal(conn, tenant_id=TENANT_A_ID, deal_id=deal_id)
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=cancelled_run_id,
            deal_id=deal_id,
            status="CANCELLED",
        )
        assert (
            conn.execute(
                text("SELECT status FROM runs WHERE run_id = :run_id"),
                {"run_id": cancelled_run_id},
            ).scalar_one()
            == "CANCELLED"
        )

    with pytest.raises((DBAPIError, IntegrityError)), app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A_ID)
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=invalid_run_id,
            deal_id=deal_id,
            status="NOT_A_STATUS",
        )


def test_postgres_runs_repository_cancel_requeue_and_queue_scope(
    app_engine: Engine,
    clean_tables: None,
) -> None:
    """Postgres run transitions honor CANCELLED semantics and queue filtering."""
    from idis.persistence.db import set_tenant_local

    deal_id = str(uuid.uuid4())
    queued_run_id = str(uuid.uuid4())
    running_run_id = str(uuid.uuid4())
    failed_run_id = str(uuid.uuid4())

    with app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A_ID)
        _insert_deal(conn, tenant_id=TENANT_A_ID, deal_id=deal_id)
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=queued_run_id,
            deal_id=deal_id,
            status="QUEUED",
        )
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=running_run_id,
            deal_id=deal_id,
            status="RUNNING",
        )
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=failed_run_id,
            deal_id=deal_id,
            status="FAILED",
        )

        repo = PostgresRunsRepository(conn, TENANT_A_ID)
        assert repo.try_cancel_active(queued_run_id) is True
        assert repo.try_cancel_active(running_run_id) is True

        cancelled_rows = {
            row.run_id: row.cancel_requested_at
            for row in conn.execute(
                text(
                    """
                    SELECT run_id, cancel_requested_at
                    FROM runs
                    WHERE run_id IN (:queued_run_id, :running_run_id)
                    """
                ),
                {
                    "queued_run_id": queued_run_id,
                    "running_run_id": running_run_id,
                },
            )
        }
        assert cancelled_rows[uuid.UUID(queued_run_id)] is not None
        assert cancelled_rows[uuid.UUID(running_run_id)] is not None

        queued_ids = {str(row["run_id"]) for row in repo.claim_queued_runs(limit=10)}
        assert queued_run_id not in queued_ids
        assert running_run_id not in queued_ids

        assert repo.try_requeue_failed(queued_run_id) is False
        assert repo.try_requeue_failed(running_run_id) is False
        assert repo.try_requeue_failed(failed_run_id) is True
        assert repo.get(failed_run_id)["status"] == "QUEUED"


def test_postgres_lifecycle_cancel_uses_run_lifecycle_without_preflight_collision(
    app_engine: Engine,
    clean_tables: None,
) -> None:
    """RUN_LIFECYCLE evidence coexists with existing DOCUMENT_PREFLIGHT in Postgres."""
    from idis.persistence.db import set_tenant_local

    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    preflight_step_id = str(uuid.uuid4())

    with app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A_ID)
        _insert_deal(conn, tenant_id=TENANT_A_ID, deal_id=deal_id)
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=run_id,
            deal_id=deal_id,
            status="RUNNING",
        )

        runs_repo = PostgresRunsRepository(conn, TENANT_A_ID)
        steps_repo = PostgresRunStepsRepository(conn, TENANT_A_ID)
        existing_step = RunStep(
            step_id=preflight_step_id,
            run_id=run_id,
            tenant_id=TENANT_A_ID,
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=3,
            status=StepStatus.COMPLETED,
            started_at="2026-05-27T00:00:00Z",
            finished_at="2026-05-27T00:00:01Z",
            result_summary={"safe_existing_value": "preserve-me"},
        )
        steps_repo.create(existing_step)

        lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=steps_repo)
        assert lifecycle.request_cancel(run_id=run_id, tenant_id=TENANT_A_ID) is True

        preflight_after = steps_repo.get_step(run_id, StepName.DOCUMENT_PREFLIGHT)
        lifecycle_after = steps_repo.get_step(run_id, StepName.RUN_LIFECYCLE)
        assert preflight_after is not None
        assert preflight_after.step_id == preflight_step_id
        assert preflight_after.result_summary == {"safe_existing_value": "preserve-me"}
        assert lifecycle_after is not None
        assert lifecycle_after.error_code == "RUN_CANCELLED"
        assert lifecycle_after.result_summary["reason_code"] == "RUN_CANCELLED"


def test_postgres_lifecycle_transitions_are_tenant_scoped(
    app_engine: Engine,
    clean_tables: None,
) -> None:
    """Tenant B repositories cannot retry or cancel tenant A runs."""
    from idis.persistence.db import set_tenant_local

    deal_id = str(uuid.uuid4())
    failed_run_id = str(uuid.uuid4())
    queued_run_id = str(uuid.uuid4())

    with app_engine.begin() as conn:
        set_tenant_local(conn, TENANT_A_ID)
        _insert_deal(conn, tenant_id=TENANT_A_ID, deal_id=deal_id)
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=failed_run_id,
            deal_id=deal_id,
            status="FAILED",
        )
        _insert_run(
            conn,
            tenant_id=TENANT_A_ID,
            run_id=queued_run_id,
            deal_id=deal_id,
            status="QUEUED",
        )

        tenant_b_repo = PostgresRunsRepository(conn, TENANT_B_ID)
        assert tenant_b_repo.try_requeue_failed(failed_run_id) is False
        assert tenant_b_repo.try_cancel_active(queued_run_id) is False
        assert tenant_b_repo.get(failed_run_id) is None
        assert tenant_b_repo.get(queued_run_id) is None

        tenant_a_repo = PostgresRunsRepository(conn, TENANT_A_ID)
        assert tenant_a_repo.get(failed_run_id)["status"] == "FAILED"
        assert tenant_a_repo.get(queued_run_id)["status"] == "QUEUED"
