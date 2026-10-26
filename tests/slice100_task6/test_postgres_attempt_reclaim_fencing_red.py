"""Disposable-PostgreSQL RED contracts for Task-6 attempt lease recovery."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
    set_tenant_context,
)
from tests.slice100_task6._support import TASK6_SCHEMA_HEAD
from tests.slice100_task6.test_postgres_0037_red import (
    ATTEMPT_ID,
    DEAL_ID,
    HASH_A,
    HASH_B,
    LEASE_OWNER_ID,
    RUN_ID,
    STEP_ID,
    TENANT_A,
    _insert_task6_graph,
    _seed_task6_dependencies,
)

SUCCESSOR_LEASE_OWNER_ID = "67676767-6767-4767-8767-676767676767"
MUTATED_LEASE_OWNER_ID = "68686868-6868-4868-8868-686868686868"


def _prepare_started_attempt(database: DisposablePostgres) -> None:
    migrate(database.admin_engine, TASK6_SCHEMA_HEAD)
    grant_application_access(database)
    _seed_task6_dependencies(database)
    _insert_task6_graph(database, started_attempt_only=True)


def _activate_successor_lease(database: DisposablePostgres) -> None:
    with database.admin_engine.begin() as connection:
        updated = connection.execute(
            text(
                """
                UPDATE runs
                SET lease_owner_id = CAST(:owner_id AS uuid),
                    lease_fencing_token = 9,
                    lease_expires_at = statement_timestamp() + interval '1 hour',
                    last_heartbeat_at = statement_timestamp()
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND run_id = CAST(:run_id AS uuid)
                  AND status = 'RUNNING'
                """
            ),
            {
                "owner_id": SUCCESSOR_LEASE_OWNER_ID,
                "tenant_id": TENANT_A,
                "run_id": RUN_ID,
            },
        )
        assert updated.rowcount == 1


def _attempt_fence(database: DisposablePostgres) -> tuple[str, int, str]:
    with database.admin_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT lease_owner_id, lease_fencing_token, status
                FROM methodology_extraction_attempts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND attempt_id = CAST(:attempt_id AS uuid)
                """
            ),
            {"tenant_id": TENANT_A, "attempt_id": ATTEMPT_ID},
        ).one()
    return str(row.lease_owner_id), int(row.lease_fencing_token), str(row.status)


def test_higher_token_successor_marks_old_started_attempt_outcome_unknown() -> None:
    """A reclaimed run closes, but never rewrites, the old admission epoch."""
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_started_attempt(database)
        _activate_successor_lease(database)

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            repository.mark_attempt_outcome_unknown(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                attempt_id=ATTEMPT_ID,
                run_step_id=STEP_ID,
                lease_owner_id=SUCCESSOR_LEASE_OWNER_ID,
                lease_fencing_token=9,
            )

        assert _attempt_fence(database) == (LEASE_OWNER_ID, 7, "OUTCOME_UNKNOWN")


def test_same_admission_lease_marks_started_attempt_outcome_unknown() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_started_attempt(database)

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            repository.mark_attempt_outcome_unknown(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                attempt_id=ATTEMPT_ID,
                run_step_id=STEP_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )

        assert _attempt_fence(database) == (LEASE_OWNER_ID, 7, "OUTCOME_UNKNOWN")


def test_current_lease_context_is_set_only_after_successful_fence_validation() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        WORKER_LEASE_FENCED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_started_attempt(database)

        with database.app_engine.begin() as connection:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository._require_fence(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    lease_owner_id=MUTATED_LEASE_OWNER_ID,
                    lease_fencing_token=8,
                )
            assert exc_info.value.code == WORKER_LEASE_FENCED
            assert connection.execute(
                text("SELECT current_setting('idis.worker_lease_owner_id', true)")
            ).scalar_one_or_none() in (None, "")
            assert connection.execute(
                text("SELECT current_setting('idis.worker_lease_fencing_token', true)")
            ).scalar_one_or_none() in (None, "")

            repository._require_fence(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
            assert (
                connection.execute(
                    text("SELECT current_setting('idis.worker_lease_owner_id', true)")
                ).scalar_one()
                == LEASE_OWNER_ID
            )
            assert (
                connection.execute(
                    text("SELECT current_setting('idis.worker_lease_fencing_token', true)")
                ).scalar_one()
                == "7"
            )


_ZOMBIE_TERMINAL_UPDATES = {
    "SUCCEEDED": """
        status = 'SUCCEEDED', observed_model = 'fixture-model',
        provider_request_id = 'zombie-request', provider_response_sha256 = :hash_a,
        dispatched_call_sha256 = :hash_b, completed_at = now(), latency_ms = 1,
        input_tokens = 0, output_tokens = 0, stop_reason = 'end_turn',
        receipt_sha256 = :hash_b
    """,
    "FAILED": """
        status = 'FAILED', completed_at = now(),
        safe_failure_code = 'METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED'
    """,
    "OUTCOME_UNKNOWN": """
        status = 'OUTCOME_UNKNOWN', completed_at = now(),
        safe_failure_code = 'METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN'
    """,
}


@pytest.mark.parametrize("terminal_status", tuple(_ZOMBIE_TERMINAL_UPDATES))
def test_zombie_worker_cannot_write_any_terminal_attempt_state(terminal_status: str) -> None:
    from idis.persistence.repositories.methodology_extraction import (
        WORKER_LEASE_FENCED,
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_started_attempt(database)
        _activate_successor_lease(database)

        connection = database.app_engine.connect()
        transaction = connection.begin()
        try:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            with pytest.raises(MethodologyExtractionRepositoryError) as exc_info:
                repository._require_fence(
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    lease_owner_id=LEASE_OWNER_ID,
                    lease_fencing_token=7,
                )
            assert exc_info.value.code == WORKER_LEASE_FENCED

            with pytest.raises(DBAPIError) as trigger_exc:
                connection.execute(
                    text(
                        "UPDATE methodology_extraction_attempts SET "
                        + _ZOMBIE_TERMINAL_UPDATES[terminal_status]
                        + " WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "AND attempt_id = CAST(:attempt_id AS uuid)"
                    ),
                    {
                        "tenant_id": TENANT_A,
                        "attempt_id": ATTEMPT_ID,
                        "hash_a": HASH_A,
                        "hash_b": HASH_B,
                    },
                )
            assert "WORKER_LEASE_FENCED" in str(trigger_exc.value)
        finally:
            transaction.rollback()
            connection.close()

        assert _attempt_fence(database) == (LEASE_OWNER_ID, 7, "STARTED")


def test_attempt_admission_owner_and_token_are_immutable_provenance() -> None:
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    with disposable_postgres() as database:
        _prepare_started_attempt(database)

        connection = database.app_engine.connect()
        transaction = connection.begin()
        try:
            set_tenant_context(connection, TENANT_A)
            repository = PostgresMethodologyExtractionRepository(connection, TENANT_A)
            repository._require_fence(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                lease_owner_id=LEASE_OWNER_ID,
                lease_fencing_token=7,
            )
            with pytest.raises(DBAPIError) as trigger_exc:
                connection.execute(
                    text(
                        """
                        UPDATE methodology_extraction_attempts
                        SET lease_owner_id = CAST(:mutated_owner AS uuid),
                            lease_fencing_token = 8
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND attempt_id = CAST(:attempt_id AS uuid)
                        """
                    ),
                    {
                        "mutated_owner": MUTATED_LEASE_OWNER_ID,
                        "tenant_id": TENANT_A,
                        "attempt_id": ATTEMPT_ID,
                    },
                )
            assert "METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE" in str(trigger_exc.value)
        finally:
            transaction.rollback()
            connection.close()

        assert _attempt_fence(database) == (LEASE_OWNER_ID, 7, "STARTED")
