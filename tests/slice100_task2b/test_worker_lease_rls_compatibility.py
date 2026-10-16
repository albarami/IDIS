"""Worker-lease compatibility proof for the canonical migration-0034 RLS policy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
)
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import (
    PostgresRunsRepository,
    WorkerLeaseFencedError,
)
from idis.services.runs.execution import build_postgres_step_boundary_uow
from idis.services.runs.orchestrator import RunContext
from idis.services.runs.steps import build_postgres_native_output_stager
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)


def _seed_queued_run(
    database: DisposablePostgres,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> None:
    now = datetime.now(UTC)
    with database.admin_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants (tenant_id, name) VALUES (CAST(:tenant_id AS uuid), :name)"),
            {"tenant_id": tenant_id, "name": "Slice100 Task2b lease tenant"},
        )
        connection.execute(
            text(
                "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
                "VALUES (CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid), :name, :now)"
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Slice100 Task2b lease deal",
                "now": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status, started_at,
                    idempotency_key, created_at, source
                ) VALUES (
                    CAST(:run_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), 'FULL', 'QUEUED', :now,
                    :idempotency_key, :now, CAST(:source AS jsonb)
                )
                """
            ),
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "now": now,
                "idempotency_key": f"task2b-lease-{run_id}",
                "source": '{"type":"deal_documents","document_ids":[]}',
            },
        )


def _admin_run_state(database: DisposablePostgres, run_id: str) -> dict[str, Any]:
    with database.admin_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                SELECT status, lease_owner_id, lease_fencing_token,
                       lease_expires_at, last_heartbeat_at,
                       cancel_requested_at, finished_at
                FROM runs
                WHERE run_id = CAST(:run_id AS uuid)
                """
                ),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def test_worker_lease_transitions_and_cancellation_survive_canonical_rls() -> None:
    """Exercise every lease write through the unprivileged production repository path."""
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0034")
        grant_application_access(database)

        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        first_owner_id = str(uuid.uuid4())
        second_owner_id = str(uuid.uuid4())
        current_step_id = str(uuid.uuid4())
        step_id = str(uuid.uuid4())
        package_id = str(uuid.uuid4())
        _seed_queued_run(
            database,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )

        initial = _admin_run_state(database, run_id)
        assert initial["status"] == "QUEUED"
        assert initial["lease_owner_id"] is None
        assert initial["lease_fencing_token"] == 0
        assert initial["lease_expires_at"] is None
        assert initial["last_heartbeat_at"] is None

        with database.app_engine.begin() as connection:
            role = connection.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            assert tuple(role) == (database.role_name, False, False)

            repository = PostgresRunsRepository(connection, tenant_id)
            claimed = repository.claim_queued_runs(
                limit=1,
                lease_owner_id=first_owner_id,
                lease_seconds=60,
            )
            assert len(claimed) == 1
            assert claimed[0]["run_id"] == run_id
            assert claimed[0]["status"] == "RUNNING"
            assert claimed[0]["lease_owner_id"] == first_owner_id
            assert claimed[0]["lease_fencing_token"] == 1

        first_claim = _admin_run_state(database, run_id)
        assert first_claim["status"] == "RUNNING"
        assert str(first_claim["lease_owner_id"]) == first_owner_id
        assert first_claim["lease_fencing_token"] == 1
        assert first_claim["lease_expires_at"] is not None
        assert first_claim["last_heartbeat_at"] is not None

        with database.app_engine.begin() as connection:
            repository = PostgresRunsRepository(connection, tenant_id)
            assert (
                repository.renew_worker_lease(
                    run_id=run_id,
                    lease_owner_id=first_owner_id,
                    lease_fencing_token=1,
                    lease_seconds=180,
                )
                is True
            )

        heartbeat = _admin_run_state(database, run_id)
        assert heartbeat["status"] == "RUNNING"
        assert str(heartbeat["lease_owner_id"]) == first_owner_id
        assert heartbeat["lease_fencing_token"] == 1
        assert heartbeat["lease_expires_at"] > first_claim["lease_expires_at"]
        assert heartbeat["last_heartbeat_at"] >= first_claim["last_heartbeat_at"]

        # Process death is represented by expiry written by migration authority; recovery
        # itself still runs through the unprivileged production repository and RLS policy.
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE runs SET lease_expires_at = "
                    "statement_timestamp() - interval '1 second' "
                    "WHERE run_id = CAST(:run_id AS uuid)"
                ),
                {"run_id": run_id},
            )

        with database.app_engine.begin() as connection:
            repository = PostgresRunsRepository(connection, tenant_id)
            requeued = repository.requeue_expired_worker_leases(limit=1)
            assert len(requeued) == 1
            assert requeued[0]["run_id"] == run_id
            assert requeued[0]["status"] == "QUEUED"
            assert requeued[0]["lease_owner_id"] is None
            assert requeued[0]["lease_fencing_token"] == 2
            assert requeued[0]["lease_expires_at"] is None

        after_requeue = _admin_run_state(database, run_id)
        assert after_requeue["status"] == "QUEUED"
        assert after_requeue["lease_owner_id"] is None
        assert after_requeue["lease_fencing_token"] == 2
        assert after_requeue["lease_expires_at"] is None

        with database.app_engine.begin() as connection:
            repository = PostgresRunsRepository(connection, tenant_id)
            claimed = repository.claim_queued_runs(
                limit=1,
                lease_owner_id=second_owner_id,
                lease_seconds=60,
            )
            assert len(claimed) == 1
            assert claimed[0]["status"] == "RUNNING"
            assert claimed[0]["lease_owner_id"] == second_owner_id
            assert claimed[0]["lease_fencing_token"] == 3
            assert claimed[0]["lease_fencing_token"] > 1

        started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        current_step = RunStep(
            step_id=current_step_id,
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
            step_order=STEP_ORDER[StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE],
            status=StepStatus.RUNNING,
            started_at=started_at,
        )
        completed_step = current_step.model_copy(
            update={
                "status": StepStatus.COMPLETED,
                "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "result_summary": {"package_count": 1, "status": "completed"},
            }
        )
        package = RunScopedValidatedEvidencePackageRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            package_id=package_id,
            court_id=str(uuid.uuid4()),
            dashboard_id=str(uuid.uuid4()),
            claim_ids_by_disposition={},
            evidence_ids=[],
            source_span_ids=[],
            sanad_ids=[],
            defect_ids=[],
            calc_ids=[],
            finding_ids=[],
            finding_types=[],
            role_names=[],
            reason_codes=[],
            by_disposition={},
            by_grade={},
            by_dashboard_verdict={},
            by_finding_type={},
            by_reason={},
            status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
        )
        context = RunContext(
            run_id=run_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            mode="FULL",
            documents=[],
            extract_fn=lambda **_kwargs: {},
            grade_fn=lambda **_kwargs: {},
            methodology_validated_evidence_package=package,
        )
        native_output_writes = build_postgres_native_output_stager()(
            ctx=context,
            step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
            result_summary=completed_step.result_summary,
        )
        assert len(native_output_writes) == 1

        boundary_uow = build_postgres_step_boundary_uow(
            audit_sink=PostgresAuditSink(),
            connection_factory=database.app_engine.begin,
            lease_seconds=180,
        )

        def create_current_step(
            *,
            db_conn: Any,
            lease_owner_id: str,
            lease_fencing_token: int,
        ) -> None:
            PostgresRunStepsRepository(db_conn, tenant_id).create(
                current_step,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        boundary_uow(
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
            boundary="START",
            lease_owner_id=second_owner_id,
            lease_fencing_token=3,
            mutation=create_current_step,
            native_output_writes=(),
        )

        def complete_current_step(
            *,
            db_conn: Any,
            lease_owner_id: str,
            lease_fencing_token: int,
        ) -> None:
            PostgresRunStepsRepository(db_conn, tenant_id).update(
                completed_step,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        boundary_uow(
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
            boundary="COMPLETE",
            lease_owner_id=second_owner_id,
            lease_fencing_token=3,
            mutation=complete_current_step,
            native_output_writes=native_output_writes,
        )

        with database.admin_engine.connect() as connection:
            persisted_step = connection.execute(
                text(
                    "SELECT status, result_summary FROM run_steps "
                    "WHERE step_id = CAST(:step_id AS uuid)"
                ),
                {"step_id": current_step_id},
            ).one()
            persisted_package = connection.execute(
                text(
                    "SELECT status, safe_summary FROM validated_evidence_packages "
                    "WHERE package_id = CAST(:package_id AS uuid)"
                ),
                {"package_id": package_id},
            ).one()
        assert persisted_step.status == "COMPLETED"
        assert persisted_step.result_summary == {"package_count": 1, "status": "completed"}
        assert persisted_package.status == "completed"
        assert persisted_package.safe_summary["by_reason"] == {}

        stale_step = RunStep(
            step_id=step_id,
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.INGEST_CHECK,
            step_order=STEP_ORDER[StepName.INGEST_CHECK],
            status=StepStatus.RUNNING,
        )
        with database.app_engine.begin() as connection:
            step_repository = PostgresRunStepsRepository(connection, tenant_id)
            with pytest.raises(WorkerLeaseFencedError) as fenced:
                step_repository.create(
                    stale_step,
                    lease_owner_id=first_owner_id,
                    lease_fencing_token=1,
                )
            assert fenced.value.code == "WORKER_LEASE_FENCED"
            assert step_repository.get_step(run_id, StepName.INGEST_CHECK) is None

        with database.admin_engine.connect() as connection:
            persisted_step_count = connection.execute(
                text("SELECT count(*) FROM run_steps WHERE step_id = CAST(:step_id AS uuid)"),
                {"step_id": step_id},
            ).scalar_one()
        assert persisted_step_count == 0

        # Cooperative cancellation intentionally clears a live owner without changing the
        # row tenant, so the canonical runs WITH CHECK must permit this existing lifecycle.
        with database.app_engine.begin() as connection:
            repository = PostgresRunsRepository(connection, tenant_id)
            assert repository.try_cancel_active(run_id) is True
            cancelled = repository.get(run_id)
            assert cancelled is not None
            assert cancelled["status"] == "CANCELLED"
            assert cancelled["lease_owner_id"] is None
            assert cancelled["lease_fencing_token"] == 3
            assert cancelled["lease_expires_at"] is None
            assert cancelled["cancel_requested_at"] is not None

        final = _admin_run_state(database, run_id)
        assert final["status"] == "CANCELLED"
        assert final["lease_owner_id"] is None
        assert final["lease_fencing_token"] == 3
        assert final["lease_expires_at"] is None
        assert final["cancel_requested_at"] is not None
        assert final["finished_at"] is not None
