"""Slice75B RED tests for run retry / resume / cancel canonical lifecycle parity.

These tests intentionally define the target Slice75B behavior and are expected
to fail before any production implementation is added. They exercise the
contract documented in the Slice75B revised plan:

- Retry/resume of a FAILED run reuses the Slice75A strict admission helper for
  FULL runs and atomically transitions ``FAILED -> QUEUED`` through a shared
  lifecycle service (never ``RunExecutionService.execute`` or ``RunOrchestrator``
  directly).
- Cancel is a stop operation: it never invokes strict admission, providers,
  the orchestrator, or the canonical execution service; it transitions the
  run to a terminal ``CANCELLED`` status.
- Tenant scoping, leakage-safe ledger entries, and ``PipelineExecutor``
  quarantine remain enforced.
"""

from __future__ import annotations

import importlib
import json
import re
from hashlib import sha256
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from idis.api.abac import InMemoryDealAssignmentStore, set_deal_assignment_store
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    _run_steps_store,
)
from idis.persistence.repositories.runs import (
    _in_memory_runs_store,
    clear_in_memory_runs_store,
)

API_KEY = "slice75b-red-key"
OTHER_API_KEY = "slice75b-red-other-key"
TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

RUN_FAILED_FULL = "11111111-1111-1111-1111-111111111111"
RUN_FAILED_FULL_BLOCKED = "11111111-1111-1111-1111-111111111112"
RUN_FAILED_FULL_INVALID_SOURCE = "11111111-1111-1111-1111-111111111113"
RUN_FAILED_FULL_ALIAS = "11111111-1111-1111-1111-111111111114"
RUN_SUCCEEDED = "22222222-2222-2222-2222-222222222222"
RUN_QUEUED = "33333333-3333-3333-3333-333333333333"
RUN_RUNNING = "44444444-4444-4444-4444-444444444444"
RUN_CANCELLED = "55555555-5555-5555-5555-555555555555"
RUN_OTHER_TENANT = "66666666-6666-6666-6666-666666666666"

DEAL_ID = "deadbeef-dead-beef-dead-beefdeadbeef"
SECRET_DSN = "postgresql://user:secret@localhost:5432/idis"


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_A,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        OTHER_API_KEY: {
            "tenant_id": TENANT_B,
            "actor_id": "actor-b",
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


def _seed_run(
    *,
    run_id: str,
    status: str,
    tenant_id: str = TENANT_A,
    mode: str = "FULL",
    source: dict[str, Any] | None = None,
    cancel_requested_at: str | None = None,
) -> dict[str, Any]:
    run = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "deal_id": DEAL_ID,
        "mode": mode,
        "status": status,
        "started_at": "2026-05-27T00:00:00Z",
        "finished_at": None,
        "source": source,
        "created_at": "2026-05-27T00:00:00Z",
    }
    if cancel_requested_at is not None:
        run["cancel_requested_at"] = cancel_requested_at
    _in_memory_runs_store[run_id] = run
    return run


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    isolate_idempotency_store: bool = False,
    audit_sink: InMemoryAuditSink | None = None,
) -> TestClient:
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    idempotency_store = (
        SqliteIdempotencyStore(in_memory=True) if isolate_idempotency_store else None
    )
    app = create_app(
        audit_sink=audit_sink or InMemoryAuditSink(),
        idempotency_store=idempotency_store,
        service_region="me-south-1",
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_in_memory_stores() -> None:
    clear_in_memory_runs_store()
    _run_steps_store.clear()
    store = InMemoryDealAssignmentStore()
    store.add_assignment(TENANT_A, DEAL_ID, "actor-a")
    store.add_assignment(TENANT_B, DEAL_ID, "actor-b")
    set_deal_assignment_store(store)


class _UniqueRunStepsRepository:
    """Postgres-like run-step fake enforcing tenant/run/step_name uniqueness."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._steps: dict[tuple[str, str, StepName], RunStep] = {}
        self.create_attempts: list[StepName] = []

    def seed(self, step: RunStep) -> None:
        key = (step.tenant_id, step.run_id, step.step_name)
        self._steps[key] = step

    def create(self, step: RunStep) -> RunStep:
        self.create_attempts.append(step.step_name)
        key = (step.tenant_id, step.run_id, step.step_name)
        if key in self._steps:
            raise ValueError("duplicate run step violates unique constraint")
        self._steps[key] = step
        return step

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        steps = [
            step
            for (tenant_id, stored_run_id, _step_name), step in self._steps.items()
            if tenant_id == self._tenant_id and stored_run_id == run_id
        ]
        return sorted(steps, key=lambda step: step.step_order)

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        return self._steps.get((self._tenant_id, run_id, step_name))

    def update(self, step: RunStep) -> RunStep:
        key = (step.tenant_id, step.run_id, step.step_name)
        if key not in self._steps:
            raise KeyError(step.step_id)
        self._steps[key] = step
        return step


class _LifecycleRunsRepository:
    """Minimal runs repository fake for lifecycle service tests."""

    def __init__(self, initial_status: str) -> None:
        self.status = initial_status
        self.finished_at: str | None = None

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.status = status
        self.finished_at = finished_at

    def try_requeue_failed(self, run_id: str) -> bool:
        if self.status != "FAILED":
            return False
        self.status = "QUEUED"
        self.finished_at = None
        return True

    def try_cancel_active(self, run_id: str) -> bool:
        if self.status not in {"QUEUED", "RUNNING"}:
            return False
        self.status = "CANCELLED"
        return True


class _LateCancellationRunsRepository:
    """Runs repo fake that exposes a cancellation after orchestration returns."""

    def __init__(self) -> None:
        self.status = "QUEUED"
        self.finished_at: str | None = None
        self.completed_statuses: list[str] = []

    def get(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "tenant_id": TENANT_A,
            "deal_id": DEAL_ID,
            "status": self.status,
            "finished_at": self.finished_at,
        }

    def try_mark_running(self, run_id: str) -> bool:
        if self.status != "QUEUED":
            return False
        self.status = "RUNNING"
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.completed_statuses.append(status)
        self.status = status
        self.finished_at = finished_at


class _CompletionRaceRunsRepository:
    """Runs repo fake where a concurrent cancel wins AFTER the pre-complete check.

    The first cancellation check observes RUNNING; the concurrent cancel then commits,
    so guarded execution finalization must refuse to overwrite the CANCELLED status
    with SUCCEEDED/FAILED.
    """

    def __init__(self) -> None:
        self.status = "QUEUED"
        self.finished_at: str | None = None
        self.guarded_complete_calls: list[str] = []
        self.unguarded_complete_calls: list[str] = []
        self._cancel_wins_after_check = True

    def get(self, run_id: str) -> dict[str, Any]:
        observed = self.status
        if self._cancel_wins_after_check and self.status == "RUNNING":
            # The pre-complete check observes RUNNING; the concurrent cancel commits
            # immediately after, before execution finalization writes a terminal status.
            self._cancel_wins_after_check = False
            self.status = "CANCELLED"
            self.finished_at = "2026-05-27T00:01:00Z"
        return {
            "run_id": run_id,
            "tenant_id": TENANT_A,
            "deal_id": DEAL_ID,
            "status": observed,
            "finished_at": self.finished_at,
        }

    def try_mark_running(self, run_id: str) -> bool:
        if self.status != "QUEUED":
            return False
        self.status = "RUNNING"
        return True

    def try_complete_running(self, run_id: str, *, status: str, finished_at: str | None) -> bool:
        self.guarded_complete_calls.append(status)
        if self.status != "RUNNING":
            return False
        self.status = status
        self.finished_at = finished_at
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.unguarded_complete_calls.append(status)
        self.status = status
        self.finished_at = finished_at


class _PassingStrictReport:
    may_proceed = True

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": True,
            "blocker_count": 0,
            "blocking_components": [],
            "components": [],
        }


class _BlockingStrictReport:
    may_proceed = False

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": False,
            "blocker_count": 1,
            "blocking_components": ["live_llm_model_clients"],
            "components": [],
        }


def _patch_strict_admission(report: Any) -> Any:
    """Patch the shared admission helper for whichever modules import it."""
    return patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
        return_value=report,
    )


def test_retry_failed_full_run_reuses_slice75a_strict_admission_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry of a FAILED FULL run must call the Slice75A strict admission helper."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_FAILED_FULL, status="FAILED")

    strict_helper = MagicMock(return_value=_PassingStrictReport())
    with patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
        strict_helper,
    ):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL}/retry",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 202, response.text
    assert strict_helper.call_count == 1
    assert _in_memory_runs_store[RUN_FAILED_FULL]["status"] == "QUEUED"


def test_retry_failed_full_run_with_blocking_strict_stays_failed_and_logs_safe_block_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict block on retry keeps the run FAILED and writes a safe ledger entry."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_FAILED_FULL_BLOCKED, status="FAILED")

    with _patch_strict_admission(_BlockingStrictReport()):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL_BLOCKED}/retry",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert _in_memory_runs_store[RUN_FAILED_FULL_BLOCKED]["status"] == "FAILED"

    steps = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL_BLOCKED)
    assert steps, "strict block ledger entry must be persisted on retry block"
    assert steps[-1].error_code == "STRICT_FULL_LIVE_BLOCKED"
    encoded = json.dumps(steps[-1].model_dump(mode="json"), sort_keys=True)
    assert "raw_text" not in encoded
    assert "postgresql://" not in encoded.lower()
    assert "object_key" not in encoded


@pytest.mark.parametrize("action", ["retry", "resume"])
def test_retry_resume_strict_block_lifecycle_409_emits_safe_audit_event(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """Side-effecting strict-block lifecycle 409s must still be audited."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, audit_sink=audit_sink)
    _seed_run(run_id=RUN_FAILED_FULL_BLOCKED, status="FAILED")

    with _patch_strict_admission(_BlockingStrictReport()):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL_BLOCKED}/{action}",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"
    steps = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL_BLOCKED)
    lifecycle_steps = [step for step in steps if step.step_name == StepName.RUN_LIFECYCLE]
    assert lifecycle_steps
    assert lifecycle_steps[-1].error_code == "STRICT_FULL_LIVE_BLOCKED"

    events = audit_sink.events
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "deal.run.requeued"
    assert event["request"]["status_code"] == 409
    assert event["resource"]["resource_id"] == RUN_FAILED_FULL_BLOCKED
    encoded_event = json.dumps(event, sort_keys=True)
    assert API_KEY not in encoded_event
    assert "raw_text" not in encoded_event
    assert "object_key" not in encoded_event
    assert "provider_payload" not in encoded_event
    assert SECRET_DSN not in encoded_event


@pytest.mark.parametrize("action", ["retry", "resume"])
def test_retry_resume_strict_block_409_with_idempotency_key_replays_without_duplicate_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """Side-effecting strict-block 409s must be idempotently replayed."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, isolate_idempotency_store=True, audit_sink=audit_sink)
    _seed_run(run_id=RUN_FAILED_FULL_BLOCKED, status="FAILED")
    strict_helper = MagicMock(return_value=_BlockingStrictReport())
    headers = {
        "X-IDIS-API-Key": API_KEY,
        "Idempotency-Key": f"{action}-strict-block-409-key",
    }

    with patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
        strict_helper,
    ):
        first = client.post(f"/v1/runs/{RUN_FAILED_FULL_BLOCKED}/{action}", headers=headers)
        second = client.post(f"/v1/runs/{RUN_FAILED_FULL_BLOCKED}/{action}", headers=headers)

    assert first.status_code == 409
    assert first.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert second.status_code == 409
    assert second.json() == first.json()
    assert second.headers.get("X-IDIS-Idempotency-Replay") == "true"
    assert strict_helper.call_count == 1

    lifecycle_step = InMemoryRunStepsRepository(TENANT_A).get_step(
        RUN_FAILED_FULL_BLOCKED,
        StepName.RUN_LIFECYCLE,
    )
    assert lifecycle_step is not None
    assert len(lifecycle_step.result_summary["lifecycle_events"]) == 1
    assert len(audit_sink.events) == 1


def test_retry_does_not_call_run_execution_service_execute_or_run_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry handler must requeue only; it must never execute through the canonical service."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_FAILED_FULL, status="FAILED")

    with (
        _patch_strict_admission(_PassingStrictReport()),
        patch("idis.services.runs.execution.RunExecutionService.execute") as exec_mock,
        patch("idis.services.runs.orchestrator.RunOrchestrator.execute") as orch_mock,
    ):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL}/retry",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 202, response.text
    assert exec_mock.call_count == 0
    assert orch_mock.call_count == 0


@pytest.mark.parametrize(
    "status",
    ["SUCCEEDED", "QUEUED", "RUNNING", "CANCELLED"],
)
def test_retry_rejects_non_failed_runs_as_run_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """Retry on any non-FAILED status must return RUN_NOT_RETRYABLE and not mutate status."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    run_id = {
        "SUCCEEDED": RUN_SUCCEEDED,
        "QUEUED": RUN_QUEUED,
        "RUNNING": RUN_RUNNING,
        "CANCELLED": RUN_CANCELLED,
    }[status]
    _seed_run(run_id=run_id, status=status)

    response = client.post(
        f"/v1/runs/{run_id}/retry",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "RUN_NOT_RETRYABLE"
    assert _in_memory_runs_store[run_id]["status"] == status


def test_retry_not_retryable_409_without_lifecycle_mutation_emits_no_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected retry with no lifecycle mutation remains unaudited."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, audit_sink=audit_sink)
    _seed_run(run_id=RUN_SUCCEEDED, status="SUCCEEDED")

    response = client.post(
        f"/v1/runs/{RUN_SUCCEEDED}/retry",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "RUN_NOT_RETRYABLE"
    assert InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_SUCCEEDED) == []
    assert audit_sink.events == []


def test_retry_not_retryable_409_with_idempotency_key_is_not_replayed_or_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-mutation 409s keep the existing non-replay contract."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, isolate_idempotency_store=True, audit_sink=audit_sink)
    _seed_run(run_id=RUN_SUCCEEDED, status="SUCCEEDED")
    headers = {
        "X-IDIS-API-Key": API_KEY,
        "Idempotency-Key": "not-retryable-409-key",
    }

    first = client.post(f"/v1/runs/{RUN_SUCCEEDED}/retry", headers=headers)
    second = client.post(f"/v1/runs/{RUN_SUCCEEDED}/retry", headers=headers)

    assert first.status_code == 409
    assert second.status_code == 409
    assert first.json()["code"] == "RUN_NOT_RETRYABLE"
    assert second.json()["code"] == "RUN_NOT_RETRYABLE"
    assert second.headers.get("X-IDIS-Idempotency-Replay") is None
    assert InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_SUCCEEDED) == []
    assert audit_sink.events == []


def test_retry_cross_tenant_returns_404_and_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry against another tenant's run id must return 404 and never mutate the row."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_OTHER_TENANT, status="FAILED", tenant_id=TENANT_B)

    response = client.post(
        f"/v1/runs/{RUN_OTHER_TENANT}/retry",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 404
    assert _in_memory_runs_store[RUN_OTHER_TENANT]["status"] == "FAILED"


def test_retry_invalid_persisted_run_source_fails_closed_as_invalid_run_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry of a FAILED FULL with selected docs missing from corpus fails closed."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(
        run_id=RUN_FAILED_FULL_INVALID_SOURCE,
        status="FAILED",
        source={"type": "deal_documents", "document_ids": ["missing-doc"]},
    )

    response = client.post(
        f"/v1/runs/{RUN_FAILED_FULL_INVALID_SOURCE}/retry",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_RUN_SOURCE"
    assert _in_memory_runs_store[RUN_FAILED_FULL_INVALID_SOURCE]["status"] == "FAILED"


@pytest.mark.parametrize("action", ["retry", "resume"])
def test_retry_resume_invalid_source_lifecycle_409_emits_safe_audit_event(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """Side-effecting invalid-source lifecycle 409s must still be audited."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, audit_sink=audit_sink)
    _seed_run(
        run_id=RUN_FAILED_FULL_INVALID_SOURCE,
        status="FAILED",
        source={"type": "deal_documents", "document_ids": ["missing-doc"]},
    )

    response = client.post(
        f"/v1/runs/{RUN_FAILED_FULL_INVALID_SOURCE}/{action}",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_RUN_SOURCE"
    steps = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL_INVALID_SOURCE)
    lifecycle_steps = [step for step in steps if step.step_name == StepName.RUN_LIFECYCLE]
    assert lifecycle_steps
    assert lifecycle_steps[-1].error_code == "INVALID_RUN_SOURCE"

    events = audit_sink.events
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "deal.run.requeued"
    assert event["request"]["status_code"] == 409
    assert event["resource"]["resource_id"] == RUN_FAILED_FULL_INVALID_SOURCE
    encoded_event = json.dumps(event, sort_keys=True)
    assert API_KEY not in encoded_event
    assert "missing-doc" not in encoded_event
    assert "raw_text" not in encoded_event
    assert "object_key" not in encoded_event
    assert "provider_payload" not in encoded_event
    assert SECRET_DSN not in encoded_event


@pytest.mark.parametrize("action", ["retry", "resume"])
def test_retry_resume_invalid_source_409_with_idempotency_key_replays_without_duplicate_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """Side-effecting invalid-source 409s must be idempotently replayed."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, isolate_idempotency_store=True, audit_sink=audit_sink)
    _seed_run(
        run_id=RUN_FAILED_FULL_INVALID_SOURCE,
        status="FAILED",
        source={"type": "deal_documents", "document_ids": ["missing-doc"]},
    )
    headers = {
        "X-IDIS-API-Key": API_KEY,
        "Idempotency-Key": f"{action}-invalid-source-409-key",
    }

    first = client.post(
        f"/v1/runs/{RUN_FAILED_FULL_INVALID_SOURCE}/{action}",
        headers=headers,
    )
    second = client.post(
        f"/v1/runs/{RUN_FAILED_FULL_INVALID_SOURCE}/{action}",
        headers=headers,
    )

    assert first.status_code == 409
    assert first.json()["code"] == "INVALID_RUN_SOURCE"
    assert second.status_code == 409
    assert second.json() == first.json()
    assert second.headers.get("X-IDIS-Idempotency-Replay") == "true"

    lifecycle_step = InMemoryRunStepsRepository(TENANT_A).get_step(
        RUN_FAILED_FULL_INVALID_SOURCE,
        StepName.RUN_LIFECYCLE,
    )
    assert lifecycle_step is not None
    assert len(lifecycle_step.result_summary["lifecycle_events"]) == 1
    assert len(audit_sink.events) == 1


@pytest.mark.parametrize("action", ["retry", "resume"])
def test_retry_resume_lifecycle_audit_hashes_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """Audited retry/resume lifecycle 409s must not store raw idempotency keys."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    raw_idempotency_key = f"{action}-lifecycle-key-raw-secret"
    audit_sink = InMemoryAuditSink()
    client = _client(monkeypatch, isolate_idempotency_store=True, audit_sink=audit_sink)
    _seed_run(run_id=RUN_FAILED_FULL_BLOCKED, status="FAILED")

    with _patch_strict_admission(_BlockingStrictReport()):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL_BLOCKED}/{action}",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Idempotency-Key": raw_idempotency_key,
            },
        )

    assert response.status_code == 409, response.text
    events = audit_sink.events
    assert len(events) == 1
    event = events[0]
    encoded_event = json.dumps(event, sort_keys=True)
    assert raw_idempotency_key not in encoded_event
    assert "idempotency_key" not in event["request"]
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        event["request"]["idempotency_key_sha256"],
    )
    assert (
        event["request"]["idempotency_key_sha256"]
        == sha256(raw_idempotency_key.encode("utf-8")).hexdigest()
    )


def test_resume_is_alias_of_retry_and_reuses_strict_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/runs/{id}/resume must behave identically to /retry for FAILED FULL."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_FAILED_FULL_ALIAS, status="FAILED")

    strict_helper = MagicMock(return_value=_PassingStrictReport())
    with patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
        strict_helper,
    ):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL_ALIAS}/resume",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 202, response.text
    assert strict_helper.call_count == 1
    assert _in_memory_runs_store[RUN_FAILED_FULL_ALIAS]["status"] == "QUEUED"


def test_requeue_failed_clears_stale_cancel_requested_at_in_memory() -> None:
    """Retry/requeue must clear stale cancellation metadata."""
    from idis.persistence.repositories.runs import InMemoryRunsRepository

    _seed_run(
        run_id=RUN_FAILED_FULL,
        status="FAILED",
        cancel_requested_at="2026-05-27T00:01:00Z",
    )
    _in_memory_runs_store[RUN_FAILED_FULL]["finished_at"] = "2026-05-27T00:02:00Z"
    runs_repo = InMemoryRunsRepository(TENANT_A)

    assert runs_repo.try_requeue_failed(RUN_FAILED_FULL) is True

    stored = runs_repo.get(RUN_FAILED_FULL)
    assert stored is not None
    assert stored["status"] == "QUEUED"
    assert stored["finished_at"] is None
    assert stored["cancel_requested_at"] is None


def test_resume_then_worker_skips_completed_steps_via_existing_orchestrator_resume_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume requeues and worker runs through real execution/orchestrator path.

    Contract covered:
    - FAILED run with an already-COMPLETED step is resumed/requeued via /resume.
    - Next worker cycle executes through real RunExecutionService + RunOrchestrator.
    - Completed step is not rerun and not duplicated.
    - Next incomplete step is dispatched.
    - PipelineExecutor is still quarantined.
    """
    import asyncio

    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.pipeline.worker import PipelineWorker
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(
        run_id=RUN_FAILED_FULL,
        status="FAILED",
        mode="SNAPSHOT",
        source=None,
    )

    completed_extract_step = RunStep(
        step_id="00000000-0000-0000-0000-0000000000ee",
        run_id=RUN_FAILED_FULL,
        tenant_id=TENANT_A,
        step_name=StepName.EXTRACT,
        step_order=17,
        status=StepStatus.COMPLETED,
        started_at="2026-05-27T00:00:00Z",
        finished_at="2026-05-27T00:00:01Z",
        result_summary={
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        },
    )
    InMemoryRunStepsRepository(TENANT_A).create(completed_extract_step)
    step_rows_before = len(InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL))
    assert step_rows_before == 1

    with _patch_strict_admission(_PassingStrictReport()):
        resume_resp = client.post(
            f"/v1/runs/{RUN_FAILED_FULL}/resume",
            headers={"X-IDIS-API-Key": API_KEY},
        )
    assert resume_resp.status_code == 202, resume_resp.text
    assert _in_memory_runs_store[RUN_FAILED_FULL]["status"] == "QUEUED"

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")

    runs_repo = InMemoryRunsRepository(TENANT_A)
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    audit_sink = InMemoryAuditSink()

    extract_calls: list[str] = []
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def counting_extract(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        return {
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        }

    def counting_grade(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        grade_calls.append(run_id)
        return {
            "graded_count": len(created_claim_ids),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    def counting_calc(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: Any = None,
    ) -> dict[str, Any]:
        calc_calls.append(run_id)
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    def execution_service_factory(*, db_conn: Any, tenant_id: str) -> RunExecutionService:
        return RunExecutionService(
            audit_sink=audit_sink,
            runs_repo=runs_repo,
            run_steps_repo=run_steps_repo,
        )

    def run_context_factory(
        *,
        db_conn: Any,
        tenant_id: str,
        run_data: dict[str, Any],
        audit_sink: Any,
    ) -> RunContext:
        return RunContext(
            run_id=str(run_data["run_id"]),
            tenant_id=tenant_id,
            deal_id=str(run_data["deal_id"]),
            mode="SNAPSHOT",
            documents=[
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
            ],
            preflight_corpus=[
                {
                    "tenant_id": tenant_id,
                    "deal_id": str(run_data["deal_id"]),
                    "document_id": "doc-001",
                    "doc_id": "artifact-doc-001",
                    "doc_type": "PDF",
                    "parse_status": "PARSED",
                    "document_name": "test.pdf",
                    "sha256": "a" * 64,
                    "uri": "deals/test.pdf",
                    "metadata": {},
                    "source_metadata": {},
                    "spans": [
                        {
                            "span_id": "span-001",
                            "tenant_id": tenant_id,
                            "deal_id": str(run_data["deal_id"]),
                            "document_id": "doc-001",
                            "span_type": "PAGE_TEXT",
                            "locator": {"page": 1},
                            "text_excerpt": "Revenue was $5M.",
                            "content_hash": "b" * 64,
                        }
                    ],
                }
            ],
            extract_fn=counting_extract,
            grade_fn=counting_grade,
            calc_fn=counting_calc,
        )

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=execution_service_factory,
        run_context_factory=run_context_factory,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch(
            "idis.pipeline.worker.get_runs_repository",
            side_effect=lambda _conn, _tenant: runs_repo,
        ),
        patch(
            "idis.pipeline.worker.get_run_steps_repository",
            side_effect=lambda _conn, _tenant: run_steps_repo,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    # Completed EXTRACT step should be skipped (existing resume contract).
    assert extract_calls == []
    # Next incomplete steps should dispatch through the real orchestrator path.
    assert grade_calls == [RUN_FAILED_FULL]
    assert calc_calls == [RUN_FAILED_FULL]

    pipeline_module = importlib.import_module("idis.pipeline")
    assert not hasattr(pipeline_module, "PipelineExecutor")

    step_rows_after = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL)
    extract_rows = [s for s in step_rows_after if s.step_name == StepName.EXTRACT]
    assert len(extract_rows) == 1, "already-COMPLETED EXTRACT row must not be duplicated"
    assert extract_rows[0].step_id == completed_extract_step.step_id
    assert extract_rows[0].status == StepStatus.COMPLETED


def test_retry_after_stale_failed_run_lifecycle_step_can_still_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale lifecycle evidence must not poison later successful execution."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.lifecycle import RunLifecycleService
    from idis.services.runs.orchestrator import RunContext

    _seed_run(
        run_id=RUN_FAILED_FULL_BLOCKED,
        status="FAILED",
        mode="FULL",
        source=None,
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    runs_repo = InMemoryRunsRepository(TENANT_A)
    lifecycle_step = RunStep(
        step_id="00000000-0000-0000-0000-00000000f00d",
        run_id=RUN_FAILED_FULL_BLOCKED,
        tenant_id=TENANT_A,
        step_name=StepName.RUN_LIFECYCLE,
        step_order=STEP_ORDER[StepName.RUN_LIFECYCLE],
        status=StepStatus.FAILED,
        started_at="2026-05-27T00:00:00Z",
        finished_at="2026-05-27T00:00:01Z",
        result_summary={
            "reason_code": "STRICT_FULL_LIVE_BLOCKED",
            "lifecycle_events": [
                {
                    "reason_code": "STRICT_FULL_LIVE_BLOCKED",
                    "occurred_at": "2026-05-27T00:00:01Z",
                }
            ],
        },
        error_code="STRICT_FULL_LIVE_BLOCKED",
        error_message="Strict full live retry admission blocked",
    )
    run_steps_repo.create(lifecycle_step)
    lifecycle_before = run_steps_repo.get_step(
        RUN_FAILED_FULL_BLOCKED,
        StepName.RUN_LIFECYCLE,
    )
    assert lifecycle_before == lifecycle_step

    lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)
    assert lifecycle.request_retry(run_id=RUN_FAILED_FULL_BLOCKED) is True
    assert _in_memory_runs_store[RUN_FAILED_FULL_BLOCKED]["status"] == "QUEUED"

    ctx = RunContext(
        run_id=RUN_FAILED_FULL_BLOCKED,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "safe.pdf",
                "spans": [
                    {
                        "span_id": "span-001",
                        "text_excerpt": "Revenue was $5M.",
                        "locator": {"page": 1},
                        "span_type": "PAGE_TEXT",
                    }
                ],
            }
        ],
        deal_metadata={"tenant_id": TENANT_A, "company_name": "Acme Corp"},
        extract_fn=lambda **_kwargs: {
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        },
        grade_fn=lambda **kwargs: {
            "graded_count": len(kwargs["created_claim_ids"]),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        },
        calc_fn=lambda **_kwargs: {
            "calc_ids": ["calc-001"],
            "reproducibility_hashes": ["hash-aaa"],
        },
        enrich_fn=lambda **_kwargs: {
            "provider_count": 0,
            "result_count": 0,
            "blocked_count": 0,
            "enrichment_refs": {},
        },
        debate_fn=lambda **kwargs: {
            "debate_id": kwargs["run_id"],
            "stop_reason": "MAX_ROUNDS",
            "round_number": 1,
            "muhasabah_passed": True,
            "agent_output_count": 0,
        },
        layer2_ic_challenge_fn=lambda **kwargs: {
            "status": "completed",
            "layer2_challenge_ids": [f"layer2-{kwargs['run_id'][:8]}"],
            "source_debate_ids": [str(kwargs["debate_summary"]["debate_id"])],
            "claim_ids": sorted(kwargs["created_claim_ids"]),
            "calc_ids": sorted(kwargs["calc_ids"]),
            "graph_ref_ids": [],
            "rag_ref_ids": [],
            "enrichment_ref_ids": [],
            "finding_count": 0,
            "unresolved_question_count": 0,
            "muhasabah_passed": True,
        },
        analysis_fn=lambda **kwargs: {
            "agent_count": 0,
            "report_ids": [],
            "bundle_id": f"bundle-{kwargs['run_id'][:8]}",
        },
        scoring_fn=lambda **_kwargs: {
            "composite_score": 72.5,
            "band": "MEDIUM",
            "routing": "HOLD",
        },
        deliverables_fn=lambda **_kwargs: {
            "deliverable_count": 0,
            "types": [],
            "deliverable_ids": [],
        },
    )
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert _in_memory_runs_store[RUN_FAILED_FULL_BLOCKED]["status"] == "SUCCEEDED"
    assert (
        run_steps_repo.get_step(RUN_FAILED_FULL_BLOCKED, StepName.RUN_LIFECYCLE) == lifecycle_before
    )


def test_cancel_queued_marks_cancelled_without_strict_or_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel on a QUEUED run is a stop operation; no strict, no orchestrator, no execute."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_QUEUED, status="QUEUED")

    strict_helper = MagicMock(return_value=_PassingStrictReport())
    with (
        patch(
            "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
            strict_helper,
        ),
        patch("idis.services.runs.execution.RunExecutionService.execute") as exec_mock,
        patch("idis.services.runs.orchestrator.RunOrchestrator.execute") as orch_mock,
    ):
        response = client.post(
            f"/v1/runs/{RUN_QUEUED}/cancel",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 202, response.text
    assert _in_memory_runs_store[RUN_QUEUED]["status"] == "CANCELLED"
    assert strict_helper.call_count == 0
    assert exec_mock.call_count == 0
    assert orch_mock.call_count == 0


def test_cancel_succeeds_when_strict_readiness_would_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is not execution admission; it must succeed even under strict block."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_QUEUED, status="QUEUED")

    blocking_helper = MagicMock(return_value=_BlockingStrictReport())
    with patch(
        "idis.services.runs.strict_full_live.build_strict_full_live_admission_report",
        blocking_helper,
    ):
        response = client.post(
            f"/v1/runs/{RUN_QUEUED}/cancel",
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert response.status_code == 202, response.text
    assert _in_memory_runs_store[RUN_QUEUED]["status"] == "CANCELLED"
    assert blocking_helper.call_count == 0


def test_cancel_running_sets_cancel_requested_at_and_orchestrator_stops_between_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel on RUNNING records cancel_requested_at; orchestrator must consult and stop.

    The orchestrator dispatch loop must consult a cancellation signal between steps
    and stop before dispatching the next step. This test fails today because the
    orchestrator does not consult any cancellation signal, and it will continue to
    fail if a future change merely exposes ``is_cancellation_requested`` without
    wiring it into the dispatch loop.
    """
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_RUNNING, status="RUNNING", mode="SNAPSHOT")

    cancel_resp = client.post(
        f"/v1/runs/{RUN_RUNNING}/cancel",
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert cancel_resp.status_code == 202, cancel_resp.text
    stored = _in_memory_runs_store[RUN_RUNNING]
    assert stored.get("cancel_requested_at") is not None, (
        "cancel_requested_at must be set on RUNNING -> CANCELLED transition"
    )

    from idis.services.runs.orchestrator import RunContext, RunOrchestrator

    # Reset to a freshly-RUNNING row so the orchestrator can dispatch at least the
    # first step before observing the cancel signal mid-flight.
    _in_memory_runs_store[RUN_RUNNING]["status"] = "RUNNING"
    _in_memory_runs_store[RUN_RUNNING].pop("cancel_requested_at", None)

    pre_completed_steps = [
        (StepName.DATA_ROOM_INVENTORY_PACKAGE, 0),
        (StepName.DATA_ROOM_INGESTION_HANDOFF, 1),
        (StepName.INGEST_CHECK, 2),
        (StepName.DOCUMENT_PREFLIGHT, 3),
        (StepName.METHODOLOGY_COVERAGE_INIT, 4),
    ]
    steps_repo = InMemoryRunStepsRepository(TENANT_A)
    for step_name, step_order in pre_completed_steps:
        steps_repo.create(
            RunStep(
                step_id=f"00000000-0000-0000-0000-00000000{step_order:04d}",
                run_id=RUN_RUNNING,
                tenant_id=TENANT_A,
                step_name=step_name,
                step_order=step_order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary={},
            )
        )

    extract_calls: list[str] = []
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def cancelling_extract(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        # Mid-execution cancellation: update the persistent runs row so any
        # cancellation check (callable, repo poll, etc.) will observe it.
        _in_memory_runs_store[run_id]["status"] = "CANCELLED"
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return {
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        }

    def counting_grade(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        grade_calls.append(run_id)
        return {
            "graded_count": len(created_claim_ids),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    def counting_calc(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: Any = None,
    ) -> dict[str, Any]:
        calc_calls.append(run_id)
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[
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
        ],
        extract_fn=cancelling_extract,
        grade_fn=counting_grade,
        calc_fn=counting_calc,
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=steps_repo,
    )
    orchestrator.execute(ctx)

    assert extract_calls == [RUN_RUNNING], (
        "extract should dispatch exactly once before the cancellation takes effect"
    )
    assert grade_calls == [], (
        "orchestrator must consult the cancellation signal between EXTRACT and GRADE "
        "and stop before dispatching GRADE"
    )
    assert calc_calls == [], "orchestrator must not continue past the cancellation point to CALC"


def test_execution_service_preserves_cancelled_status_after_mid_execution_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical execution must not overwrite mid-execution cancellation as FAILED."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    _seed_run(run_id=RUN_RUNNING, status="QUEUED", mode="SNAPSHOT")

    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    runs_repo = InMemoryRunsRepository(TENANT_A)
    pre_completed_steps = [
        (StepName.DATA_ROOM_INVENTORY_PACKAGE, 0),
        (StepName.DATA_ROOM_INGESTION_HANDOFF, 1),
        (StepName.INGEST_CHECK, 2),
        (StepName.DOCUMENT_PREFLIGHT, 3),
        (StepName.METHODOLOGY_COVERAGE_INIT, 4),
    ]
    for step_name, step_order in pre_completed_steps:
        run_steps_repo.create(
            RunStep(
                step_id=f"10000000-0000-0000-0000-00000000{step_order:04d}",
                run_id=RUN_RUNNING,
                tenant_id=TENANT_A,
                step_name=step_name,
                step_order=step_order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary={},
            )
        )

    extract_calls: list[str] = []
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def cancelling_extract(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        _in_memory_runs_store[run_id]["status"] = "CANCELLED"
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return {
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001"],
            "chunk_count": 1,
            "unique_claim_count": 1,
            "conflict_count": 0,
        }

    def counting_grade(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        grade_calls.append(run_id)
        return {
            "graded_count": len(created_claim_ids),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    def counting_calc(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: Any = None,
    ) -> dict[str, Any]:
        calc_calls.append(run_id)
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[
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
        ],
        extract_fn=cancelling_extract,
        grade_fn=counting_grade,
        calc_fn=counting_calc,
    )
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(ctx)

    assert extract_calls == [RUN_RUNNING]
    assert grade_calls == []
    assert calc_calls == []
    assert result.status == "CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] == "CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] != "FAILED"


def test_execution_service_preserves_cancelled_status_when_step_cancels_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation observed during a failing step must not be completed as FAILED."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    _seed_run(run_id=RUN_RUNNING, status="QUEUED", mode="SNAPSHOT")
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    runs_repo = InMemoryRunsRepository(TENANT_A)
    for step_name, step_order in [
        (StepName.DATA_ROOM_INVENTORY_PACKAGE, 0),
        (StepName.DATA_ROOM_INGESTION_HANDOFF, 1),
        (StepName.INGEST_CHECK, 2),
        (StepName.DOCUMENT_PREFLIGHT, 3),
        (StepName.METHODOLOGY_COVERAGE_INIT, 4),
    ]:
        run_steps_repo.create(
            RunStep(
                step_id=f"20000000-0000-0000-0000-00000000{step_order:04d}",
                run_id=RUN_RUNNING,
                tenant_id=TENANT_A,
                step_name=step_name,
                step_order=step_order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary={},
            )
        )

    extract_calls: list[str] = []
    grade_calls: list[str] = []

    def cancelling_extract_then_raise(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        _in_memory_runs_store[run_id]["status"] = "CANCELLED"
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        raise RuntimeError("provider-like failure after cancellation")

    def counting_grade(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        grade_calls.append(run_id)
        return {"graded_count": 0, "failed_count": 0, "total_defects": 0, "all_failed": False}

    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[
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
        ],
        extract_fn=cancelling_extract_then_raise,
        grade_fn=counting_grade,
        calc_fn=lambda **_kwargs: {"calc_ids": [], "reproducibility_hashes": []},
    )
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(ctx)

    assert extract_calls == [RUN_RUNNING]
    assert grade_calls == []
    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] == "CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] != "FAILED"


def test_execution_service_preserves_cancelled_status_when_final_step_cancels_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation observed during the final step must not be completed as SUCCEEDED."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.persistence.repositories.runs import InMemoryRunsRepository
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    _seed_run(run_id=RUN_RUNNING, status="QUEUED", mode="SNAPSHOT")
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    runs_repo = InMemoryRunsRepository(TENANT_A)
    for step_name, step_order, summary in [
        (StepName.DATA_ROOM_INVENTORY_PACKAGE, 0, {}),
        (StepName.DATA_ROOM_INGESTION_HANDOFF, 1, {}),
        (StepName.INGEST_CHECK, 2, {}),
        (StepName.DOCUMENT_PREFLIGHT, 3, {}),
        (StepName.METHODOLOGY_COVERAGE_INIT, 4, {}),
        (StepName.EXTRACT, 5, {"created_claim_ids": ["claim-001"]}),
        (StepName.GRADE, 6, {"graded_count": 1, "failed_count": 0}),
    ]:
        run_steps_repo.create(
            RunStep(
                step_id=f"30000000-0000-0000-0000-00000000{step_order:04d}",
                run_id=RUN_RUNNING,
                tenant_id=TENANT_A,
                step_name=step_name,
                step_order=step_order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary=summary,
            )
        )

    calc_calls: list[str] = []

    def cancelling_calc(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: Any = None,
    ) -> dict[str, Any]:
        calc_calls.append(run_id)
        _in_memory_runs_store[run_id]["status"] = "CANCELLED"
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[
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
        ],
        extract_fn=lambda **_kwargs: {"created_claim_ids": ["claim-001"]},
        grade_fn=lambda **_kwargs: {
            "graded_count": 1,
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        },
        calc_fn=cancelling_calc,
    )
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    result = service.execute(ctx)

    assert calc_calls == [RUN_RUNNING]
    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] == "CANCELLED"
    assert _in_memory_runs_store[RUN_RUNNING]["status"] != "SUCCEEDED"


def test_execution_service_preserves_late_cancelled_status_after_successful_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late cancellation after orchestrator success must not be overwritten."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import OrchestratorResult, RunContext

    runs_repo = _LateCancellationRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )
    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )

    def successful_execute_then_late_cancel(
        _orchestrator: Any,
        _ctx: RunContext,
    ) -> OrchestratorResult:
        runs_repo.status = "CANCELLED"
        runs_repo.finished_at = "2026-05-27T00:01:00Z"
        return OrchestratorResult(status="SUCCEEDED", steps=[])

    with patch(
        "idis.services.runs.execution.RunOrchestrator.execute",
        successful_execute_then_late_cancel,
    ):
        result = service.execute(ctx)

    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert runs_repo.status == "CANCELLED"
    assert "SUCCEEDED" not in runs_repo.completed_statuses


def test_execution_service_preserves_late_cancelled_status_after_orchestrator_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late cancellation before exception finalization must not be overwritten."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    runs_repo = _LateCancellationRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )
    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )

    def raise_after_late_cancel(_orchestrator: Any, _ctx: RunContext) -> None:
        runs_repo.status = "CANCELLED"
        runs_repo.finished_at = "2026-05-27T00:01:00Z"
        raise RuntimeError("late cancellation won race before final failure write")

    with patch(
        "idis.services.runs.execution.RunOrchestrator.execute",
        raise_after_late_cancel,
    ):
        result = service.execute(ctx)

    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert runs_repo.status == "CANCELLED"
    assert "FAILED" not in runs_repo.completed_statuses


def test_execution_service_does_not_overwrite_cancel_winning_completion_race_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel committed between the pre-complete check and success completion must win."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import OrchestratorResult, RunContext

    runs_repo = _CompletionRaceRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )
    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )

    with patch(
        "idis.services.runs.execution.RunOrchestrator.execute",
        lambda _orchestrator, _ctx: OrchestratorResult(status="SUCCEEDED", steps=[]),
    ):
        result = service.execute(ctx)

    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert runs_repo.status == "CANCELLED"
    assert "SUCCEEDED" not in runs_repo.unguarded_complete_calls


def test_execution_service_does_not_overwrite_cancel_winning_completion_race_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel committed between the pre-complete check and FAILED completion must win."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import RunContext

    runs_repo = _CompletionRaceRunsRepository()
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )
    ctx = RunContext(
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
    )

    def raise_during_orchestration(_orchestrator: Any, _ctx: RunContext) -> None:
        raise RuntimeError("orchestration failed")

    with patch(
        "idis.services.runs.execution.RunOrchestrator.execute",
        raise_during_orchestration,
    ):
        result = service.execute(ctx)

    assert result.status == "CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert runs_repo.status == "CANCELLED"
    assert "FAILED" not in runs_repo.unguarded_complete_calls


def test_in_memory_try_complete_running_refuses_cancelled_run() -> None:
    """In-memory execution finalization guard must refuse to complete a CANCELLED run."""
    from idis.persistence.repositories.runs import InMemoryRunsRepository

    runs_repo = InMemoryRunsRepository(TENANT_A)
    _seed_run(run_id=RUN_RUNNING, status="RUNNING")
    assert runs_repo.try_cancel_active(RUN_RUNNING) is True
    assert _in_memory_runs_store[RUN_RUNNING]["status"] == "CANCELLED"

    completed = runs_repo.try_complete_running(
        RUN_RUNNING,
        status="SUCCEEDED",
        finished_at="2026-05-27T00:01:00Z",
    )

    assert completed is False
    assert _in_memory_runs_store[RUN_RUNNING]["status"] == "CANCELLED"


def test_cancel_cross_tenant_returns_404_and_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel against another tenant's run id must return 404 and not mutate the row."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_OTHER_TENANT, status="RUNNING", tenant_id=TENANT_B)

    response = client.post(
        f"/v1/runs/{RUN_OTHER_TENANT}/cancel",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 404
    assert _in_memory_runs_store[RUN_OTHER_TENANT]["status"] == "RUNNING"


def test_cancel_then_retry_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once cancelled, retry must be rejected as RUN_NOT_RETRYABLE."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    client = _client(monkeypatch)
    _seed_run(run_id=RUN_CANCELLED, status="CANCELLED")

    response = client.post(
        f"/v1/runs/{RUN_CANCELLED}/retry",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RUN_NOT_RETRYABLE"
    assert _in_memory_runs_store[RUN_CANCELLED]["status"] == "CANCELLED"


def test_cancel_audit_and_ledger_metadata_is_leakage_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel ledger / audit entries must not contain raw text, DSNs, or payloads."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.setenv("DATABASE_URL", SECRET_DSN)
    audit_sink = InMemoryAuditSink()
    client = _client(
        monkeypatch,
        isolate_idempotency_store=True,
        audit_sink=audit_sink,
    )
    _seed_run(
        run_id=RUN_RUNNING,
        status="RUNNING",
        source={"type": "deal_documents", "document_ids": ["doc-strict"]},
    )

    response = client.post(
        f"/v1/runs/{RUN_RUNNING}/cancel",
        headers={"X-IDIS-API-Key": API_KEY, "Idempotency-Key": "cancel-key-xyz"},
    )

    assert response.status_code == 202, response.text
    steps = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_RUNNING)
    assert steps, "cancel ledger entry must be persisted via run-step surface"
    encoded = json.dumps([s.model_dump(mode="json") for s in steps], sort_keys=True)
    assert SECRET_DSN not in encoded
    assert "postgresql://" not in encoded.lower()
    assert "raw_text" not in encoded
    assert "object_key" not in encoded
    assert "cancel-key-xyz" not in encoded
    assert "doc-strict" not in encoded
    events = audit_sink.events
    assert len(events) == 1
    encoded_event = json.dumps(events[0], sort_keys=True)
    assert "cancel-key-xyz" not in encoded_event
    assert "idempotency_key" not in events[0]["request"]
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        events[0]["request"]["idempotency_key_sha256"],
    )
    assert events[0]["request"]["idempotency_key_sha256"] == sha256(b"cancel-key-xyz").hexdigest()


def test_cancel_lifecycle_ledger_avoids_duplicate_document_preflight_step() -> None:
    """Cancel lifecycle evidence must not collide with existing DOCUMENT_PREFLIGHT."""
    from idis.services.runs.lifecycle import RunLifecycleService

    existing_step = RunStep(
        step_id="40000000-0000-0000-0000-000000000003",
        run_id=RUN_RUNNING,
        tenant_id=TENANT_A,
        step_name=StepName.DOCUMENT_PREFLIGHT,
        step_order=3,
        status=StepStatus.COMPLETED,
        started_at="2026-05-27T00:00:00Z",
        finished_at="2026-05-27T00:00:01Z",
        result_summary={
            "document_count": 1,
            "safe_existing_value": "preserve-me",
        },
    )
    runs_repo = _LifecycleRunsRepository("RUNNING")
    run_steps_repo = _UniqueRunStepsRepository(TENANT_A)
    run_steps_repo.seed(existing_step)
    service = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    assert service.request_cancel(run_id=RUN_RUNNING, tenant_id=TENANT_A) is True

    assert StepName.DOCUMENT_PREFLIGHT not in run_steps_repo.create_attempts
    assert run_steps_repo.get_step(RUN_RUNNING, StepName.DOCUMENT_PREFLIGHT) == existing_step
    steps = run_steps_repo.get_by_run_id(RUN_RUNNING)
    encoded = json.dumps([step.model_dump(mode="json") for step in steps], sort_keys=True)
    assert "RUN_CANCELLED" in encoded
    assert "preserve-me" in encoded
    assert SECRET_DSN not in encoded
    assert "postgresql://" not in encoded.lower()
    assert "raw_text" not in encoded
    assert "object_key" not in encoded
    assert "provider_payload" not in encoded


def test_retry_block_lifecycle_ledger_avoids_duplicate_document_preflight_step() -> None:
    """Retry/resume block evidence must not collide with existing DOCUMENT_PREFLIGHT."""
    from idis.services.runs.lifecycle import RunLifecycleService

    existing_step = RunStep(
        step_id="50000000-0000-0000-0000-000000000003",
        run_id=RUN_FAILED_FULL_BLOCKED,
        tenant_id=TENANT_A,
        step_name=StepName.DOCUMENT_PREFLIGHT,
        step_order=3,
        status=StepStatus.COMPLETED,
        started_at="2026-05-27T00:00:00Z",
        finished_at="2026-05-27T00:00:01Z",
        result_summary={
            "document_count": 1,
            "safe_existing_value": "preserve-me",
        },
    )
    runs_repo = _LifecycleRunsRepository("FAILED")
    run_steps_repo = _UniqueRunStepsRepository(TENANT_A)
    run_steps_repo.seed(existing_step)
    service = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    service.persist_failed_block(
        run_id=RUN_FAILED_FULL_BLOCKED,
        tenant_id=TENANT_A,
        reason_code="STRICT_FULL_LIVE_BLOCKED",
        message="Strict full live retry admission blocked",
    )

    assert StepName.DOCUMENT_PREFLIGHT not in run_steps_repo.create_attempts
    assert (
        run_steps_repo.get_step(RUN_FAILED_FULL_BLOCKED, StepName.DOCUMENT_PREFLIGHT)
        == existing_step
    )
    steps = run_steps_repo.get_by_run_id(RUN_FAILED_FULL_BLOCKED)
    encoded = json.dumps([step.model_dump(mode="json") for step in steps], sort_keys=True)
    assert "STRICT_FULL_LIVE_BLOCKED" in encoded
    assert "preserve-me" in encoded
    assert SECRET_DSN not in encoded
    assert "postgresql://" not in encoded.lower()
    assert "raw_text" not in encoded
    assert "object_key" not in encoded
    assert "provider_payload" not in encoded


def test_retry_audit_and_ledger_metadata_is_leakage_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry ledger / audit entries must not contain raw text, DSNs, or payloads."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("DATABASE_URL", SECRET_DSN)
    client = _client(monkeypatch)
    _seed_run(
        run_id=RUN_FAILED_FULL,
        status="FAILED",
        source={"type": "deal_documents", "document_ids": ["doc-strict"]},
    )

    with _patch_strict_admission(_PassingStrictReport()):
        response = client.post(
            f"/v1/runs/{RUN_FAILED_FULL}/retry",
            headers={"X-IDIS-API-Key": API_KEY, "Idempotency-Key": "retry-key-xyz"},
        )

    assert response.status_code == 202, response.text
    steps = InMemoryRunStepsRepository(TENANT_A).get_by_run_id(RUN_FAILED_FULL)
    encoded = json.dumps([s.model_dump(mode="json") for s in steps], sort_keys=True)
    assert SECRET_DSN not in encoded
    assert "postgresql://" not in encoded.lower()
    assert "raw_text" not in encoded
    assert "object_key" not in encoded
    assert "retry-key-xyz" not in encoded
    assert "doc-strict" not in encoded


def test_pipeline_executor_remains_quarantined_under_retry_resume_cancel() -> None:
    """No Slice75B lifecycle path may resurrect PipelineExecutor as a public export."""
    pipeline_module = importlib.import_module("idis.pipeline")
    assert "PipelineExecutor" not in getattr(pipeline_module, "__all__", [])
    assert not hasattr(pipeline_module, "PipelineExecutor")

    lifecycle = importlib.import_module("idis.services.runs.lifecycle")
    lifecycle_source = importlib.import_module("idis.services.runs.lifecycle").__file__
    assert lifecycle_source is not None
    with open(lifecycle_source, encoding="utf-8") as fh:
        text = fh.read()
    assert "PipelineExecutor" not in text, (
        "RunLifecycleService must not import or reference PipelineExecutor"
    )
    assert hasattr(lifecycle, "RunLifecycleService"), (
        "Slice75B must expose RunLifecycleService from idis.services.runs.lifecycle"
    )
