"""Slice76 Cluster 1: originating actor persistence on runs.

Verifies the runs repository persists the originating authenticated actor
(created_by_actor_id / created_by_actor_type), that retry/requeue preserves it,
and that the Postgres SQL surfaces and migration 0019 carry the new columns.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
    clear_in_memory_runs_store,
)

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DEAL_ID = "deadbeef-dead-beef-dead-beefdeadbeef"
RUN_ID = "11111111-1111-1111-1111-111111111111"


def test_runs_persist_originating_actor() -> None:
    """In-memory create must persist and return the originating actor identity."""
    clear_in_memory_runs_store()
    repo = InMemoryRunsRepository(TENANT_A)

    created = repo.create(
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        created_by_actor_id="actor-a",
        created_by_actor_type="HUMAN",
    )

    assert created["created_by_actor_id"] == "actor-a"
    assert created["created_by_actor_type"] == "HUMAN"

    fetched = repo.get(RUN_ID)
    assert fetched is not None
    assert fetched["created_by_actor_id"] == "actor-a"
    assert fetched["created_by_actor_type"] == "HUMAN"


def test_retry_requeue_preserves_originating_actor() -> None:
    """Requeue (retry/resume) must not overwrite the original run creator."""
    clear_in_memory_runs_store()
    repo = InMemoryRunsRepository(TENANT_A)
    repo.create(
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        created_by_actor_id="actor-a",
        created_by_actor_type="HUMAN",
    )
    repo.update_status(RUN_ID, status="FAILED", finished_at="2026-05-30T00:00:00Z")

    assert repo.try_requeue_failed(RUN_ID) is True

    fetched = repo.get(RUN_ID)
    assert fetched is not None
    assert fetched["status"] == "QUEUED"
    assert fetched["created_by_actor_id"] == "actor-a"
    assert fetched["created_by_actor_type"] == "HUMAN"


def test_postgres_runs_create_and_select_sql_include_created_by_actor() -> None:
    """Postgres INSERT and SELECT must carry created_by_actor_id/type columns."""

    class _FakeResult:
        def fetchone(self) -> None:
            return None

        def fetchall(self) -> list[Any]:
            return []

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed_sql: list[str] = []

        def execute(self, statement: object, params: dict[str, Any] | None = None) -> _FakeResult:
            self.executed_sql.append(str(statement))
            return _FakeResult()

    conn = _FakeConnection()
    # Bypass real SET LOCAL; PostgresRunsRepository.__init__ calls set_tenant_local.
    runs_module = importlib.import_module("idis.persistence.repositories.runs")
    original = runs_module.set_tenant_local
    runs_module.set_tenant_local = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    try:
        repo = PostgresRunsRepository(conn, TENANT_A)
        created = repo.create(
            run_id=RUN_ID,
            deal_id=DEAL_ID,
            mode="FULL",
            created_by_actor_id="actor-a",
            created_by_actor_type="HUMAN",
        )
        repo.get(RUN_ID)
    finally:
        runs_module.set_tenant_local = original  # type: ignore[assignment]

    assert created["created_by_actor_id"] == "actor-a"
    assert created["created_by_actor_type"] == "HUMAN"

    joined_sql = "\n".join(conn.executed_sql)
    assert "INSERT INTO runs" in joined_sql
    assert joined_sql.count("created_by_actor_id") >= 2  # present in INSERT and SELECT
    assert "created_by_actor_type" in joined_sql


def test_migration_0019_adds_created_by_actor_columns() -> None:
    """Migration 0019 must add created_by_actor columns and revise after 0018."""
    migration = importlib.import_module(
        "idis.persistence.migrations.versions.0019_runs_created_by_actor"
    )
    assert migration.revision == "0019"
    assert migration.down_revision == "0018"

    upgrade_sql = inspect.getsource(migration.upgrade)
    assert "created_by_actor_id" in upgrade_sql
    assert "created_by_actor_type" in upgrade_sql
    assert "ADD COLUMN IF NOT EXISTS" in upgrade_sql

    downgrade_sql = inspect.getsource(migration.downgrade)
    assert "DROP COLUMN IF EXISTS created_by_actor_id" in downgrade_sql
    assert "DROP COLUMN IF EXISTS created_by_actor_type" in downgrade_sql


# --- Cluster 2: consume originating actor in strict run-step/worker audit events ---


def _orch_documents() -> list[dict[str, Any]]:
    return [
        {
            "document_id": "doc-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]


def _orch_extract(**_kwargs: Any) -> dict[str, Any]:
    return {"status": "COMPLETED", "created_claim_ids": ["claim-001"]}


def _orch_grade(**_kwargs: Any) -> dict[str, Any]:
    return {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}


def _orch_calc(**_kwargs: Any) -> dict[str, Any]:
    return {"calc_ids": ["calc-001"], "reproducibility_hashes": []}


def _run_snapshot_orchestrator(
    audit_sink: Any,
    *,
    created_by_actor_id: str | None,
    created_by_actor_type: str | None,
) -> Any:
    import uuid

    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
    from idis.services.runs.orchestrator import RunContext, RunOrchestrator

    orchestrator = RunOrchestrator(
        audit_sink=audit_sink,
        run_steps_repo=InMemoryRunStepsRepository(TENANT_A),
    )
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        tenant_id=TENANT_A,
        deal_id=str(uuid.uuid4()),
        mode="SNAPSHOT",
        documents=_orch_documents(),
        extract_fn=_orch_extract,
        grade_fn=_orch_grade,
        calc_fn=_orch_calc,
        created_by_actor_id=created_by_actor_id,
        created_by_actor_type=created_by_actor_type,
    )
    return orchestrator.execute(ctx)


def _run_step_audit_events(audit_sink: Any) -> list[dict[str, Any]]:
    return [event for event in audit_sink.events if "run.step" in str(event.get("event_type", ""))]


def test_worker_claimed_run_carries_originating_actor() -> None:
    """claim_queued_runs must return the originating actor to the worker path."""
    clear_in_memory_runs_store()
    repo = InMemoryRunsRepository(TENANT_A)
    repo.create(
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        created_by_actor_id="actor-a",
        created_by_actor_type="HUMAN",
    )

    claimed = repo.claim_queued_runs(limit=10)
    assert len(claimed) == 1
    assert claimed[0]["created_by_actor_id"] == "actor-a"
    assert claimed[0]["created_by_actor_type"] == "HUMAN"


def test_orchestrator_run_step_audit_event_has_originating_actor() -> None:
    """Strict run-step audit events must carry the originating authenticated actor."""
    from idis.audit.sink import InMemoryAuditSink

    audit_sink = InMemoryAuditSink()
    _run_snapshot_orchestrator(
        audit_sink,
        created_by_actor_id="actor-a",
        created_by_actor_type="HUMAN",
    )

    events = _run_step_audit_events(audit_sink)
    assert events
    for event in events:
        assert event["actor"]["actor_id"] == "actor-a"
        assert event["actor"]["actor_type"] == "HUMAN"
        assert event["actor"]["actor_id"] != "unknown"


def test_orchestrator_run_step_audit_event_uses_service_actor_for_system_run() -> None:
    """A run with no originating actor must use the defined service principal, not 'unknown'."""
    from idis.audit.sink import InMemoryAuditSink

    audit_sink = InMemoryAuditSink()
    _run_snapshot_orchestrator(
        audit_sink,
        created_by_actor_id=None,
        created_by_actor_type=None,
    )

    events = _run_step_audit_events(audit_sink)
    assert events
    for event in events:
        assert event["actor"]["actor_type"] == "SERVICE"
        assert event["actor"]["actor_id"] == "idis-worker"
        assert event["actor"]["actor_id"] != "unknown"


def test_orchestrator_run_step_audit_event_validates_against_audit_validator() -> None:
    """Run-step audit events must satisfy the existing audit-event validator."""
    from idis.audit.sink import InMemoryAuditSink
    from idis.validators.audit_event_validator import validate_audit_event

    audit_sink = InMemoryAuditSink()
    _run_snapshot_orchestrator(
        audit_sink,
        created_by_actor_id="actor-a",
        created_by_actor_type="HUMAN",
    )

    events = _run_step_audit_events(audit_sink)
    assert events
    for event in events:
        result = validate_audit_event(event)
        assert result.passed, result.errors


# --- Cluster 3: runtime validation of run-step audit events before emit ---


class _RecordingAuditSink:
    """Audit sink that records emitted events without validation."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_orchestrator_run_step_audit_event_calls_validator_before_emit() -> None:
    """Each run-step audit event must be validated via validate_audit_event before emit."""
    from idis.audit.sink import InMemoryAuditSink
    from idis.validators.audit_event_validator import validate_audit_event as real_validate

    audit_sink = InMemoryAuditSink()
    spy = MagicMock(side_effect=real_validate)
    with patch("idis.services.runs.orchestrator.validate_audit_event", spy, create=True):
        _run_snapshot_orchestrator(
            audit_sink,
            created_by_actor_id="actor-a",
            created_by_actor_type="HUMAN",
        )

    assert spy.called
    assert spy.call_count == len(audit_sink.events)


def test_orchestrator_run_step_audit_event_fails_closed_when_validator_rejects() -> None:
    """A rejected run-step audit event must fail closed (AuditSinkError)."""
    from idis.audit.sink import AuditSinkError, InMemoryAuditSink

    audit_sink = InMemoryAuditSink()
    rejecting = MagicMock(return_value=MagicMock(passed=False, errors=[]))
    with (
        patch("idis.services.runs.orchestrator.validate_audit_event", rejecting, create=True),
        pytest.raises(AuditSinkError),
    ):
        _run_snapshot_orchestrator(
            audit_sink,
            created_by_actor_id="actor-a",
            created_by_actor_type="HUMAN",
        )


def test_orchestrator_run_step_audit_event_does_not_emit_invalid_event() -> None:
    """An invalid run-step audit event must never reach the sink."""
    from idis.audit.sink import AuditSinkError

    sink = _RecordingAuditSink()
    rejecting = MagicMock(return_value=MagicMock(passed=False, errors=[]))
    with (
        patch("idis.services.runs.orchestrator.validate_audit_event", rejecting, create=True),
        pytest.raises(AuditSinkError),
    ):
        _run_snapshot_orchestrator(
            sink,
            created_by_actor_id=None,
            created_by_actor_type=None,
        )

    assert sink.events == []
