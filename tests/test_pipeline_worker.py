"""Regression tests for pipeline worker canonical execution setup.

Module-wide harness ``_hermetic_worker_env`` sets ``IDIS_REQUIRE_FULL_LIVE=0``
and replaces ``asyncio.run`` and ``asyncio.to_thread`` with synchronous inline
drivers for every test in this module. A coroutine that suspends fails the
driver. The empty-corpus ``NO_INGESTED_DOCUMENTS`` case and the frozen FAILED
``NO_USABLE_DOCUMENTS`` case are component injections: they supply states the
production freeze path cannot reach yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    clear_in_memory_runs_store,
)
from idis.pipeline.worker import PipelineWorker, _default_run_context_factory
from idis.services.runs.execution import RunExecutionService

TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
LEASE_EXPIRES_AT = "2099-01-01T00:01:00Z"
LAST_HEARTBEAT_AT = "2099-01-01T00:00:00Z"


class TransactionalInMemoryAuditSink(InMemoryAuditSink):
    """In-memory sink exposing the durable sink's in-transaction seam."""

    def emit_in_tx(self, _conn: object, event: dict[str, Any]) -> None:
        self.emit(event)


class FakeRunsRepository:
    """Tenant-scoped queued-run repository for worker tests."""

    def __init__(self) -> None:
        self.claim_queued_runs_calls: list[int] = []
        self.requeue_expired_worker_leases_calls: list[int] = []
        self.completed: list[tuple[str, str, str | None]] = []
        self.status = "QUEUED"
        self.lease_owner_id: str | None = None
        self.lease_fencing_token = 0

    def requeue_expired_worker_leases(
        self,
        *,
        limit: int = 1,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        """Model the canonical expired-lease sweep with no expired rows."""
        assert lease_audit_emitter is not None
        self.requeue_expired_worker_leases_calls.append(limit)
        return []

    def claim_queued_runs(
        self,
        *,
        limit: int,
        lease_owner_id: str,
        lease_seconds: int,
        lease_audit_emitter: Callable[..., None],
    ) -> list[dict[str, Any]]:
        """Atomically model QUEUED -> RUNNING with a fenced worker lease."""
        assert limit == 1
        assert lease_owner_id
        assert lease_seconds > 0
        self.claim_queued_runs_calls.append(limit)
        self.status = "RUNNING"
        self.lease_owner_id = lease_owner_id
        self.lease_fencing_token += 1
        run = {
            "run_id": "run-1",
            "deal_id": "deal-1",
            "mode": "SNAPSHOT",
            "status": self.status,
            "tenant_id": TENANT_ID,
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": self.lease_fencing_token,
            "lease_expires_at": LEASE_EXPIRES_AT,
            "last_heartbeat_at": LAST_HEARTBEAT_AT,
        }
        lease_audit_emitter(
            db_conn=None,
            code="WORKER_LEASE_CLAIMED",
            run_id=run["run_id"],
            deal_id=run["deal_id"],
            lease_owner_id=lease_owner_id,
            new_fencing_token=self.lease_fencing_token,
            lease_expires_at=LEASE_EXPIRES_AT,
        )
        return [run]

    def count_queued_runs(self) -> int:
        return int(self.status == "QUEUED")

    def try_mark_running(self, run_id: str) -> bool:
        """Allow canonical execution service to claim the fake run."""
        return run_id == "run-1"

    def get(self, run_id: str) -> dict[str, Any] | None:
        if run_id != "run-1":
            return None
        return {
            "run_id": run_id,
            "tenant_id": TENANT_ID,
            "deal_id": "deal-1",
            "status": self.status,
        }

    def try_complete_running(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> bool:
        """Accept terminal writes only from the current fenced owner/token."""
        if self.status != "RUNNING":
            return False
        if lease_owner_id != self.lease_owner_id:
            return False
        if lease_fencing_token != self.lease_fencing_token:
            return False
        self.status = status
        self.completed.append((run_id, status, finished_at))
        return True

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        """Record failure recovery status updates."""
        self.completed.append((run_id, status, finished_at))


DOC_1_ID = "00000000-0000-4000-8000-000000000001"
DOC_2_ID = "00000000-0000-4000-8000-000000000002"
DOC_FAILED_ID = "00000000-0000-4000-8000-000000000099"
SNAPSHOT_CONTRACT_HASH = "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"


@pytest.fixture(autouse=True)
def _hermetic_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate unit worker tests from ambient .env strict flags and loop self-pipe sockets."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")

    async def _inline_to_thread(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    def _run_coroutine_sync(coro: Any) -> Any:
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        finally:
            coro.close()
        raise RuntimeError("Worker test coroutine yielded unexpectedly")

    monkeypatch.setattr(asyncio, "to_thread", _inline_to_thread)
    monkeypatch.setattr(asyncio, "run", _run_coroutine_sync)


def _preclaimed_default_run_context_factory(
    *,
    db_conn: Any,
    tenant_id: str,
    run_data: dict[str, Any],
    audit_sink: Any,
) -> Any:
    """Build the legacy hermetic context and attach its atomically claimed lease."""
    unfenced_run_data = dict(run_data)
    unfenced_run_data.setdefault("pipeline_contract_version", "snapshot-v1")
    unfenced_run_data.setdefault("pipeline_contract_hash", SNAPSHOT_CONTRACT_HASH)
    unfenced_run_data.setdefault("pipeline_resume_state", "NON_RESUMABLE")
    lease_owner_id = str(unfenced_run_data.pop("lease_owner_id"))
    lease_fencing_token = int(unfenced_run_data.pop("lease_fencing_token"))
    ctx = _default_run_context_factory(
        db_conn=db_conn,
        tenant_id=tenant_id,
        run_data=unfenced_run_data,
        audit_sink=audit_sink,
    )
    ctx.lease_owner_id = lease_owner_id
    ctx.lease_fencing_token = lease_fencing_token
    return ctx


@pytest.fixture
def seeded_in_memory_runs_repository() -> Iterator[InMemoryRunsRepository]:
    """Keep the run row and fenced step writes on one persistence twin."""
    clear_in_memory_runs_store()
    clear_run_steps_store()
    repository = InMemoryRunsRepository(TENANT_ID)
    repository.create(
        run_id="run-1",
        deal_id="deal-1",
        mode="SNAPSHOT",
    )
    try:
        yield repository
    finally:
        clear_run_steps_store()
        clear_in_memory_runs_store()


def test_worker_uses_run_execution_service_not_pipeline_executor() -> None:
    """Production worker runs must go through RunExecutionService only."""
    conn = MagicMock()

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = FakeRunsRepository()
    execution_service = MagicMock()
    run_context = MagicMock()
    run_context.run_id = "run-1"
    run_context.tenant_id = TENANT_ID

    def context_factory(**kwargs: Any) -> MagicMock:
        run_context.lease_owner_id = kwargs["run_data"]["lease_owner_id"]
        run_context.lease_fencing_token = kwargs["run_data"]["lease_fencing_token"]
        return run_context

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: execution_service,
        run_context_factory=context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.PipelineExecutor",
            side_effect=AssertionError("worker must not instantiate PipelineExecutor"),
            create=True,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True) as set_tenant_local,
    ):
        asyncio.run(worker._process_queued_runs())

    assert set_tenant_local.call_count == 2
    assert all(item.args == (conn, TENANT_ID) for item in set_tenant_local.call_args_list)
    assert runs_repo.requeue_expired_worker_leases_calls == [1]
    assert runs_repo.claim_queued_runs_calls == [1]
    execution_service.execute_preclaimed.assert_called_once_with(run_context)


def test_worker_without_tenant_scope_does_not_scan_global_queued_runs() -> None:
    """Worker must fail safe instead of globally polling queued rows without RLS scope."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    worker = PipelineWorker(poll_interval=0, tenant_ids=[])

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.set_tenant_local", create=True) as set_tenant_local,
    ):
        asyncio.run(worker._process_queued_runs())

    set_tenant_local.assert_not_called()
    conn.execute.assert_not_called()


def test_worker_persists_failed_status_after_execution_exception() -> None:
    """A crashed preclaimed execution remains leased RUNNING for explicit reclaim."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = FakeRunsRepository()
    execution_service = MagicMock()
    execution_service.audit_sink = InMemoryAuditSink()
    execution_service.execute_preclaimed.side_effect = RuntimeError("orchestration failed")
    run_context = MagicMock()

    def context_factory(**kwargs: Any) -> MagicMock:
        run_context.lease_owner_id = kwargs["run_data"]["lease_owner_id"]
        run_context.lease_fencing_token = kwargs["run_data"]["lease_fencing_token"]
        return run_context

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: execution_service,
        run_context_factory=context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        pytest.raises(RuntimeError, match="orchestration failed"),
    ):
        asyncio.run(worker._process_queued_runs())

    assert conn.commit.call_count == 2
    conn.rollback.assert_not_called()
    assert runs_repo.status == "RUNNING"
    assert runs_repo.completed == []


class _RecoveryRunsRepository:
    """Runs repo fake exposing guarded and unconditional completion for recovery tests."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.guarded_calls: list[str] = []
        self.unconditional_calls: list[str] = []

    def try_complete_running(self, run_id: str, *, status: str, finished_at: str | None) -> bool:
        self.guarded_calls.append(status)
        if self.status != "RUNNING":
            return False
        self.status = status
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.unconditional_calls.append(status)
        self.status = status


def test_worker_exception_recovery_does_not_overwrite_cancelled_run() -> None:
    """Post-exception recovery must not overwrite a CANCELLED run with FAILED."""
    conn = MagicMock()
    runs_repo = _RecoveryRunsRepository(status="CANCELLED")

    worker = PipelineWorker(poll_interval=0, tenant_ids=[TENANT_ID])

    with (
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        worker._mark_run_failed_after_exception(conn, TENANT_ID, "run-1")

    assert runs_repo.status == "CANCELLED"
    assert runs_repo.guarded_calls == ["FAILED"]
    assert runs_repo.unconditional_calls == []


def test_worker_exception_recovery_marks_running_run_failed() -> None:
    """Post-exception recovery must still mark a still-RUNNING run FAILED via the guard."""
    conn = MagicMock()
    runs_repo = _RecoveryRunsRepository(status="RUNNING")

    worker = PipelineWorker(poll_interval=0, tenant_ids=[TENANT_ID])

    with (
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        worker._mark_run_failed_after_exception(conn, TENANT_ID, "run-1")

    assert runs_repo.status == "FAILED"
    assert runs_repo.guarded_calls == ["FAILED"]
    assert runs_repo.unconditional_calls == []


class _PreflightBlockRunsRepository:
    """Runs repo fake exposing guarded active-completion for preflight-block tests."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.active_complete_calls: list[str] = []
        self.unconditional_calls: list[str] = []

    def try_complete_active(self, run_id: str, *, status: str, finished_at: str | None) -> bool:
        self.active_complete_calls.append(status)
        if self.status not in ("QUEUED", "RUNNING"):
            return False
        self.status = status
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.unconditional_calls.append(status)
        self.status = status


class _RecordingRunStepsRepository:
    """Run-steps repo fake recording created ledger steps."""

    def __init__(self) -> None:
        self.created: list[object] = []

    def create(self, step: object) -> None:
        self.created.append(step)


def test_worker_preflight_block_does_not_overwrite_cancelled_run() -> None:
    """Strict preflight block must not overwrite a run cancelled after batch locks released."""
    conn = MagicMock()
    runs_repo = _PreflightBlockRunsRepository(status="CANCELLED")
    steps_repo = _RecordingRunStepsRepository()
    worker = PipelineWorker(poll_interval=0, tenant_ids=[TENANT_ID])

    with (
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=steps_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        worker._persist_worker_preflight_block(
            conn=conn,
            tenant_id=TENANT_ID,
            run_id="run-1",
            reason_code="STRICT_FULL_LIVE_BLOCKED",
            message="blocked",
        )

    assert runs_repo.status == "CANCELLED"
    assert runs_repo.unconditional_calls == []
    assert steps_repo.created == []


def test_worker_preflight_block_marks_queued_run_failed_with_ledger() -> None:
    """Strict preflight block must still mark a QUEUED run FAILED and write the ledger step."""
    from idis.models.run_step import StepName

    conn = MagicMock()
    runs_repo = _PreflightBlockRunsRepository(status="QUEUED")
    steps_repo = _RecordingRunStepsRepository()
    worker = PipelineWorker(poll_interval=0, tenant_ids=[TENANT_ID])

    with (
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=steps_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        worker._persist_worker_preflight_block(
            conn=conn,
            tenant_id=TENANT_ID,
            run_id="run-1",
            reason_code="INVALID_RUN_SOURCE",
            message="invalid source",
        )

    assert runs_repo.status == "FAILED"
    assert runs_repo.active_complete_calls == ["FAILED"]
    assert runs_repo.unconditional_calls == []
    assert len(steps_repo.created) == 1
    assert steps_repo.created[0].step_name == StepName.DOCUMENT_PREFLIGHT
    assert steps_repo.created[0].error_code == "INVALID_RUN_SOURCE"


def test_worker_commits_running_claim_before_orchestration(monkeypatch) -> None:
    """Worker must release the RUNNING row before long-running orchestration."""
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.orchestrator import OrchestratorResult, RunContext

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    events: list[str] = []
    cancel_results: list[bool] = []

    class RecordingConnection:
        def commit(self) -> None:
            events.append("commit")

        def rollback(self) -> None:
            events.append("rollback")

    class RecordingRunsRepository(InMemoryRunsRepository):
        def __init__(self) -> None:
            super().__init__(TENANT_ID)
            self.status = "QUEUED"
            self.lease_owner_id: str | None = None
            self.lease_fencing_token = 0

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int = 1,
            lease_audit_emitter: Callable[..., None] | None = None,
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert lease_audit_emitter is not None
            events.append("requeue_expired_worker_leases")
            return []

        def claim_queued_runs(
            self,
            *,
            limit: int,
            lease_owner_id: str,
            lease_seconds: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert lease_seconds > 0
            events.append("claim_queued_runs")
            self.status = "RUNNING"
            run = {
                "run_id": "run-1",
                "deal_id": "deal-1",
                "mode": "SNAPSHOT",
                "status": self.status,
                "tenant_id": TENANT_ID,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": 1,
                "lease_expires_at": LEASE_EXPIRES_AT,
                "last_heartbeat_at": LAST_HEARTBEAT_AT,
            }
            lease_audit_emitter(
                db_conn=conn,
                code="WORKER_LEASE_CLAIMED",
                run_id=run["run_id"],
                deal_id=run["deal_id"],
                lease_owner_id=lease_owner_id,
                new_fencing_token=1,
                lease_expires_at=LEASE_EXPIRES_AT,
            )
            return [run]

        def try_mark_running(self, run_id: str) -> bool:
            events.append("mark_running")
            if self.status != "QUEUED":
                return False
            self.status = "RUNNING"
            return True

        def get(self, run_id: str) -> dict[str, str]:
            return {
                "run_id": run_id,
                "tenant_id": TENANT_ID,
                "deal_id": "deal-1",
                "status": self.status,
            }

        def try_cancel_active(self, run_id: str) -> bool:
            events.append("cancel_during_orchestration")
            claim_index = events.index("claim_queued_runs")
            if self.status != "RUNNING" or "commit" not in events[claim_index + 1 :]:
                return False
            self.status = "CANCELLED"
            return True

        def complete(
            self,
            run_id: str,
            *,
            status: str,
            finished_at: str | None,
        ) -> None:
            events.append(f"complete_{status}")
            self.status = status

    conn = RecordingConnection()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = RecordingRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)

    def context_factory(
        *,
        db_conn: object,
        tenant_id: str,
        run_data: dict[str, str],
        audit_sink: object,
    ) -> RunContext:
        return RunContext(
            run_id=run_data["run_id"],
            tenant_id=tenant_id,
            deal_id=run_data["deal_id"],
            mode=run_data["mode"],
            lease_owner_id=str(run_data["lease_owner_id"]),
            lease_fencing_token=int(run_data["lease_fencing_token"]),
            documents=[],
            extract_fn=lambda **_kwargs: {},
            grade_fn=lambda **_kwargs: {},
        )

    def orchestrator_execute(ctx: RunContext) -> OrchestratorResult:
        events.append("orchestrator_execute")
        cancel_results.append(runs_repo.try_cancel_active(ctx.run_id))
        status = "CANCELLED" if cancel_results[-1] else "SUCCEEDED"
        return OrchestratorResult(status=status, steps=[])

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        run_context_factory=context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
        patch("idis.services.runs.execution.RunOrchestrator") as orch_class_mock,
    ):
        orch_instance = MagicMock()
        orch_instance.execute.side_effect = orchestrator_execute
        orch_class_mock.return_value = orch_instance
        processed = asyncio.run(worker._process_queued_runs())

    assert processed == 1
    assert cancel_results == [True]
    claim_index = events.index("claim_queued_runs")
    claim_commit_index = events.index("commit", claim_index)
    assert claim_index < claim_commit_index < events.index("orchestrator_execute")
    assert orch_class_mock.call_count == 1


def test_worker_default_execution_restores_tenant_context_after_claim_commit(monkeypatch) -> None:
    """Worker must re-establish RLS tenant context after the claim commit.

    set_tenant_local() uses SET LOCAL idis.tenant_id, which is transaction-scoped.
    The claim commit (added to release the RUNNING row lock for cancellation) clears
    it, so orchestration must not run without tenant RLS context on Postgres.
    """
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.orchestrator import OrchestratorResult, RunContext

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    tenant_context_at_orchestration: list[bool] = []

    class RecordingConnection:
        def __init__(self) -> None:
            self.tenant_context_active = True

        def commit(self) -> None:
            self.tenant_context_active = False

        def rollback(self) -> None:
            self.tenant_context_active = False

    class SimpleRunsRepository(InMemoryRunsRepository):
        def __init__(self) -> None:
            super().__init__(TENANT_ID)
            self.status = "QUEUED"
            self.lease_owner_id: str | None = None
            self.lease_fencing_token = 0

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int = 1,
            lease_audit_emitter: Callable[..., None] | None = None,
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert lease_audit_emitter is not None
            return []

        def claim_queued_runs(
            self,
            *,
            limit: int,
            lease_owner_id: str,
            lease_seconds: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert lease_seconds > 0
            self.status = "RUNNING"
            self.lease_owner_id = lease_owner_id
            self.lease_fencing_token += 1
            run = {
                "run_id": "run-1",
                "deal_id": "deal-1",
                "mode": "SNAPSHOT",
                "status": self.status,
                "tenant_id": TENANT_ID,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": self.lease_fencing_token,
                "lease_expires_at": LEASE_EXPIRES_AT,
                "last_heartbeat_at": LAST_HEARTBEAT_AT,
            }
            lease_audit_emitter(
                db_conn=conn,
                code="WORKER_LEASE_CLAIMED",
                run_id=run["run_id"],
                deal_id=run["deal_id"],
                lease_owner_id=lease_owner_id,
                new_fencing_token=self.lease_fencing_token,
                lease_expires_at=LEASE_EXPIRES_AT,
            )
            return [run]

        def try_mark_running(self, run_id: str) -> bool:
            if self.status != "QUEUED":
                return False
            self.status = "RUNNING"
            return True

        def get(self, run_id: str) -> dict[str, str]:
            return {
                "run_id": run_id,
                "tenant_id": TENANT_ID,
                "deal_id": "deal-1",
                "status": self.status,
            }

        def try_complete_running(
            self,
            run_id: str,
            *,
            status: str,
            finished_at: str | None,
            lease_owner_id: str | None = None,
            lease_fencing_token: int | None = None,
        ) -> bool:
            _ = (run_id, finished_at)
            if self.status != "RUNNING":
                return False
            if lease_owner_id != self.lease_owner_id:
                return False
            if lease_fencing_token != self.lease_fencing_token:
                return False
            self.status = status
            return True

        def complete(
            self,
            run_id: str,
            *,
            status: str,
            finished_at: str | None,
        ) -> None:
            self.status = status

    conn = RecordingConnection()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = SimpleRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)

    def context_factory(
        *,
        db_conn: object,
        tenant_id: str,
        run_data: dict[str, str],
        audit_sink: object,
    ) -> RunContext:
        fake_set_tenant_local(db_conn, tenant_id)
        return RunContext(
            run_id=run_data["run_id"],
            tenant_id=tenant_id,
            deal_id=run_data["deal_id"],
            mode=run_data["mode"],
            lease_owner_id=str(run_data["lease_owner_id"]),
            lease_fencing_token=int(run_data["lease_fencing_token"]),
            documents=[],
            extract_fn=lambda **_kwargs: {},
            grade_fn=lambda **_kwargs: {},
        )

    def fake_set_tenant_local(db_conn: RecordingConnection, tenant_id: str) -> None:
        db_conn.tenant_context_active = True

    def orchestrator_execute(ctx: RunContext) -> OrchestratorResult:
        tenant_context_at_orchestration.append(conn.tenant_context_active)
        return OrchestratorResult(status="SUCCEEDED", steps=[])

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        run_context_factory=context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
        patch(
            "idis.pipeline.worker.set_tenant_local",
            side_effect=fake_set_tenant_local,
            create=True,
        ),
        patch("idis.services.runs.execution.RunOrchestrator") as orch_class_mock,
    ):
        orch_instance = MagicMock()
        orch_instance.execute.side_effect = orchestrator_execute
        orch_class_mock.return_value = orch_instance
        processed = asyncio.run(worker._process_queued_runs())

    assert processed == 1
    assert tenant_context_at_orchestration == [True]


def test_worker_empty_persisted_corpus_preserves_no_ingested_documents_code(
    seeded_in_memory_runs_repository: InMemoryRunsRepository,
) -> None:
    """Component injection: an empty corpus still fails as NO_INGESTED_DOCUMENTS."""
    from idis.pipeline.worker import (
        InvalidRunSourceMetadataError,
        _load_worker_preflight_corpus,
    )

    conn = MagicMock()

    def execute(statement: object, params: dict[str, str] | None = None) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "FROM deals" in sql:
            result.fetchone.return_value = _deal_row()
            return result
        if "FROM documents" in sql:
            result.fetchall.return_value = []
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")

    conn.execute.side_effect = execute
    with pytest.raises(
        InvalidRunSourceMetadataError,
        match="run source must pin an exact document set",
    ):
        _load_worker_preflight_corpus(
            db_conn=conn,
            tenant_id=TENANT_ID,
            run_data={
                "run_id": "run-1",
                "deal_id": "deal-1",
                "mode": "SNAPSHOT",
                "source": None,
            },
        )

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = seeded_in_memory_runs_repository
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: RunExecutionService(
            audit_sink=InMemoryAuditSink(),
            runs_repo=runs_repo,
            run_steps_repo=run_steps_repo,
        ),
        run_context_factory=_preclaimed_default_run_context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
        patch("idis.pipeline.worker._load_worker_preflight_corpus", return_value=[]),
    ):
        processed = asyncio.run(worker._process_queued_runs())

    run_record = runs_repo.get("run-1")
    assert run_record is not None
    assert run_record["status"] == "FAILED"
    steps = run_steps_repo.get_by_run_id("run-1")
    assert processed == 1
    assert len(steps) == 3
    assert [step.error_code for step in steps] == [None, None, "NO_INGESTED_DOCUMENTS"]
    assert steps[-1].error_code != "VALUEERROR"


def test_worker_no_usable_persisted_corpus_preserves_no_usable_documents_code(
    seeded_in_memory_runs_repository: InMemoryRunsRepository,
) -> None:
    """Component injection: a frozen FAILED document fails as NO_USABLE_DOCUMENTS."""
    conn = MagicMock()
    failed_row = MagicMock(
        _mapping={
            "document_id": DOC_FAILED_ID,
            "tenant_id": TENANT_ID,
            "deal_id": "deal-1",
            "doc_id": "artifact-failed",
            "doc_type": "PDF",
            "parse_status": "FAILED",
            "document_metadata": {
                "name": "encrypted.pdf",
                "parse_error_codes": ["encrypted_pdf"],
                "parse_warning_codes": [],
                "detected_format": "PDF",
                "parser_doc_type": "PDF",
            },
            "artifact_metadata": {},
            "document_name": "encrypted.pdf",
            "sha256": "a" * 64,
            "uri": "deals/encrypted.pdf",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )

    def execute(statement: object, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "FROM deals" in sql:
            result.fetchone.return_value = _deal_row()
            return result
        if "JOIN source_lineage_objects root" in sql:
            requested = (params or {}).get("document_ids") or []
            result.scalars.return_value.all.return_value = [
                doc_id for doc_id in sorted(requested) if doc_id == DOC_FAILED_ID
            ]
            return result
        if "WHERE documents.document_id = :document_id" in sql:
            doc_id = str((params or {}).get("document_id") or "")
            result.fetchone.return_value = failed_row if doc_id == DOC_FAILED_ID else None
            return result
        if "FROM documents" in sql:
            result.fetchall.return_value = [failed_row]
            return result
        if "FROM document_spans" in sql:
            result.fetchall.return_value = []
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")

    conn.execute.side_effect = execute
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    clear_in_memory_runs_store()
    runs_repo = seeded_in_memory_runs_repository
    runs_repo.create(
        run_id="run-1",
        deal_id="deal-1",
        mode="SNAPSHOT",
        source={"type": "deal_documents", "document_ids": [DOC_FAILED_ID]},
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_ID)

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: RunExecutionService(
            audit_sink=InMemoryAuditSink(),
            runs_repo=runs_repo,
            run_steps_repo=run_steps_repo,
        ),
        run_context_factory=_preclaimed_default_run_context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._default_worker_audit_sink",
            return_value=TransactionalInMemoryAuditSink(),
        ),
    ):
        processed = asyncio.run(worker._process_queued_runs())

    steps = run_steps_repo.get_by_run_id("run-1")
    assert processed == 1
    assert [step.error_code for step in steps] == [None, None, None, "NO_USABLE_DOCUMENTS"]
    assert steps[-1].error_code != "RUNTIMEERROR"


def test_default_worker_context_factory_hydrates_persisted_documents() -> None:
    """Default worker context must not claim real runs with an empty document list."""
    conn = MagicMock()
    doc_row = MagicMock(
        _mapping={
            "document_id": DOC_1_ID,
            "tenant_id": TENANT_ID,
            "deal_id": "deal-1",
            "doc_id": "artifact-1",
            "doc_type": "PDF",
            "parse_status": "PARSED",
            "document_metadata": {"name": "source.pdf"},
            "artifact_metadata": {},
            "document_name": "source.pdf",
            "sha256": "a" * 64,
            "uri": "deals/source.pdf",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )

    def execute(statement: object, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "FROM deals" in sql:
            result.fetchone.return_value = _deal_row()
            return result
        if "JOIN source_lineage_objects root" in sql:
            requested = (params or {}).get("document_ids") or []
            result.scalars.return_value.all.return_value = [
                doc_id for doc_id in sorted(requested) if doc_id == DOC_1_ID
            ]
            return result
        if "WHERE documents.document_id = :document_id" in sql:
            doc_id = str((params or {}).get("document_id") or "")
            result.fetchone.return_value = doc_row if doc_id == DOC_1_ID else None
            return result
        if "FROM documents" in sql:
            result.fetchall.return_value = [doc_row]
            return result
        if "FROM document_spans" in sql:
            result.fetchall.return_value = [
                MagicMock(
                    _mapping={
                        "span_id": "span-1",
                        "tenant_id": TENANT_ID,
                        "deal_id": "deal-1",
                        "document_id": DOC_1_ID,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1},
                        "text_excerpt": "Revenue was $5M.",
                        "content_hash": None,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                )
            ]
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")

    conn.execute.side_effect = execute

    ctx = _default_run_context_factory(
        db_conn=conn,
        tenant_id=TENANT_ID,
        run_data={
            "run_id": "run-1",
            "deal_id": "deal-1",
            "mode": "SNAPSHOT",
            "source": {"type": "deal_documents", "document_ids": [DOC_1_ID]},
            "pipeline_contract_version": "snapshot-v1",
            "pipeline_contract_hash": SNAPSHOT_CONTRACT_HASH,
            "pipeline_resume_state": "NON_RESUMABLE",
        },
        audit_sink=InMemoryAuditSink(),
    )

    assert ctx.documents == [
        {
            "document_id": DOC_1_ID,
            "doc_type": "PDF",
            "document_name": "source.pdf",
            "spans": [
                {
                    "span_id": "span-1",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]
    assert [doc["document_id"] for doc in ctx.preflight_corpus] == [DOC_1_ID]
    assert ctx.methodology_registry_loader_fn is not None
    assert ctx.methodology_coverage_records == []
    assert ctx.methodology_extraction_task_planning_fn is None
    assert ctx.methodology_extraction_tasks == []
    assert ctx.methodology_extraction_task_execution_fn is None
    assert ctx.methodology_extraction_execution_result is None


def test_default_worker_context_factory_applies_run_source_and_deal_metadata() -> None:
    """Worker context must match API source filtering and deal metadata behavior."""
    conn = MagicMock()
    rows_by_id = {
        DOC_1_ID: _document_row(DOC_1_ID, "artifact-1", "source-1.pdf"),
        DOC_2_ID: _document_row(DOC_2_ID, "artifact-2", "source-2.pdf"),
    }

    def execute(statement: object, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "FROM deals" in sql:
            result.fetchone.return_value = _deal_row(company_name="WorkerCo")
            return result
        if "JOIN source_lineage_objects root" in sql:
            requested = (params or {}).get("document_ids") or []
            result.scalars.return_value.all.return_value = [
                doc_id for doc_id in sorted(requested) if doc_id in rows_by_id
            ]
            return result
        if "WHERE documents.document_id = :document_id" in sql:
            doc_id = str((params or {}).get("document_id") or "")
            result.fetchone.return_value = rows_by_id.get(doc_id)
            return result
        if "FROM documents" in sql:
            result.fetchall.return_value = list(rows_by_id.values())
            return result
        if "FROM document_spans" in sql:
            document_id = (params or {}).get("document_id")
            result.fetchall.return_value = [_span_row(str(document_id))]
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")

    conn.execute.side_effect = execute

    ctx = _default_run_context_factory(
        db_conn=conn,
        tenant_id=TENANT_ID,
        run_data={
            "run_id": "run-1",
            "deal_id": "deal-1",
            "mode": "SNAPSHOT",
            "source": {"type": "deal_documents", "document_ids": [DOC_2_ID]},
            "pipeline_contract_version": "snapshot-v1",
            "pipeline_contract_hash": SNAPSHOT_CONTRACT_HASH,
            "pipeline_resume_state": "NON_RESUMABLE",
        },
        audit_sink=InMemoryAuditSink(),
    )

    assert [doc["document_id"] for doc in ctx.preflight_corpus] == [DOC_2_ID]
    assert [doc["document_id"] for doc in ctx.documents] == [DOC_2_ID]
    assert ctx.deal_metadata is not None
    assert ctx.deal_metadata["company_name"] == "WorkerCo"


def _document_row(document_id: str, artifact_id: str, name: str) -> MagicMock:
    return MagicMock(
        _mapping={
            "document_id": document_id,
            "tenant_id": TENANT_ID,
            "deal_id": "deal-1",
            "doc_id": artifact_id,
            "doc_type": "PDF",
            "parse_status": "PARSED",
            "document_metadata": {"name": name},
            "artifact_metadata": {},
            "document_name": name,
            "sha256": "a" * 64,
            "uri": f"deals/{name}",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )


def _deal_row(company_name: str = "WorkerCo") -> MagicMock:
    return MagicMock(
        deal_id="deal-1",
        tenant_id=TENANT_ID,
        name="Worker Source Deal",
        company_name=company_name,
        status="ACTIVE",
        stage="DILIGENCE",
        tags=[],
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
    )


def _span_row(document_id: str) -> MagicMock:
    return MagicMock(
        _mapping={
            "span_id": f"span-{document_id}",
            "tenant_id": TENANT_ID,
            "deal_id": "deal-1",
            "document_id": document_id,
            "span_type": "PAGE_TEXT",
            "locator": {"page": 1},
            "text_excerpt": f"Revenue from {document_id}.",
            "content_hash": "b" * 64,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
