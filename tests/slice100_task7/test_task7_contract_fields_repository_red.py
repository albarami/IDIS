"""RED contracts for Task-7 run/run-step contract-bearing persistence."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from idis.models.run_step import RunStep
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
    clear_in_memory_runs_store,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
STEP_ID = "44444444-4444-4444-8444-444444444444"
CONTRACT_HASH = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
SEMANTIC_HASH = "ee473f158a9e48dff845c7e498e8bd3eadde20ea68eeb698e7c3482c158182f6"


class _Result:
    def __init__(self, *, row: object | None = None, rows: list[object] | None = None) -> None:
        self.rowcount = 1
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self) -> object | None:
        return self._row

    def fetchall(self) -> list[object]:
        return list(self._rows)


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[_Result] = []

    def execute(
        self,
        statement: object,
        params: dict[str, Any] | None = None,
    ) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        if self.results:
            return self.results.pop(0)
        return _Result()

    def begin_nested(self) -> nullcontext[None]:
        return nullcontext()


def _strict_step() -> RunStep:
    return RunStep(
        step_id=STEP_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        step_name="DATA_ROOM_INVENTORY_PACKAGE",
        step_order=0,
        pipeline_contract_version="full-strict-v20",
        pipeline_contract_hash=CONTRACT_HASH,
        semantic_rule_version="2",
        semantic_rule_hash=SEMANTIC_HASH,
    )


def test_run_step_model_carries_all_four_immutable_contract_fields() -> None:
    step = _strict_step()

    assert step.pipeline_contract_version == "full-strict-v20"
    assert step.pipeline_contract_hash == CONTRACT_HASH
    assert step.semantic_rule_version == "2"
    assert step.semantic_rule_hash == SEMANTIC_HASH


def test_postgres_run_step_create_writes_all_four_contract_fields() -> None:
    connection = _RecordingConnection()
    repository = PostgresRunStepsRepository(connection, TENANT_ID)  # type: ignore[arg-type]

    repository.create(_strict_step())

    statement, params = connection.calls[-1]
    for column in (
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "semantic_rule_version",
        "semantic_rule_hash",
    ):
        assert column in statement
        assert params[column] == getattr(_strict_step(), column)


def test_postgres_run_step_read_round_trips_contract_fields_and_total_order() -> None:
    connection = _RecordingConnection()
    repository = PostgresRunStepsRepository(connection, TENANT_ID)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    connection.results.append(
        _Result(
            rows=[
                SimpleNamespace(
                    step_id=STEP_ID,
                    tenant_id=TENANT_ID,
                    run_id=RUN_ID,
                    step_name="DATA_ROOM_INVENTORY_PACKAGE",
                    step_order=0,
                    status="PENDING",
                    started_at=None,
                    finished_at=None,
                    retry_count=0,
                    result_summary={},
                    error_code=None,
                    error_message=None,
                    pipeline_contract_version="full-strict-v20",
                    pipeline_contract_hash=CONTRACT_HASH,
                    semantic_rule_version="2",
                    semantic_rule_hash=SEMANTIC_HASH,
                    created_at=now,
                )
            ]
        )
    )

    steps = repository.get_by_run_id(RUN_ID)

    statement, _params = connection.calls[-1]
    assert "ORDER BY step_order, step_id" in " ".join(statement.split())
    assert len(steps) == 1
    assert steps[0].pipeline_contract_hash == CONTRACT_HASH
    assert steps[0].semantic_rule_hash == SEMANTIC_HASH


def test_postgres_run_create_requires_and_persists_contract_identity() -> None:
    connection = _RecordingConnection()
    repository = PostgresRunsRepository(connection, TENANT_ID)  # type: ignore[arg-type]

    created = repository.create(
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        pipeline_contract_version="full-strict-v20",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        pipeline_resume_error_code=None,
    )

    statement, params = connection.calls[-1]
    for column in (
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "pipeline_resume_state",
        "pipeline_resume_error_code",
    ):
        assert column in statement
        assert params[column] == created[column]


def test_postgres_run_row_round_trips_contract_identity() -> None:
    connection = _RecordingConnection()
    repository = PostgresRunsRepository(connection, TENANT_ID)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    row = SimpleNamespace(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        status="QUEUED",
        started_at=now,
        finished_at=None,
        source=None,
        created_at=now,
        cancel_requested_at=None,
        created_by_actor_id=None,
        created_by_actor_type=None,
        strict_preflight_proof_id=None,
        lease_owner_id=None,
        lease_fencing_token=0,
        lease_expires_at=None,
        last_heartbeat_at=None,
        pipeline_contract_version="full-strict-v20",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        pipeline_resume_error_code=None,
    )

    result = repository._row_to_dict(row)

    assert result["pipeline_contract_version"] == "full-strict-v20"
    assert result["pipeline_contract_hash"] == CONTRACT_HASH
    assert result["pipeline_resume_state"] == "RESUMABLE"
    assert result["pipeline_resume_error_code"] is None


def test_in_memory_run_create_uses_the_same_explicit_contract_fields() -> None:
    clear_in_memory_runs_store()
    repository = InMemoryRunsRepository(TENANT_ID)

    created = repository.create(
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        pipeline_contract_version="full-strict-v20",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        pipeline_resume_error_code=None,
    )

    assert created["pipeline_contract_version"] == "full-strict-v20"
    assert created["pipeline_contract_hash"] == CONTRACT_HASH
    assert created["pipeline_resume_state"] == "RESUMABLE"
