"""RED contracts proving restart validates durable coverage, not summaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from idis.services.runs import methodology_postgres_materialization_runtime as materialization
from idis.services.runs import methodology_postgres_runtime as execution


@contextmanager
def _connection_factory() -> Iterator[object]:
    yield object()


def test_step7_rehydration_requires_the_recomputed_durable_projection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, str]] = []

    class _Repository:
        def __init__(self, _connection: object, _tenant_id: str) -> None:
            pass

        def require_coverage_projection(self, *, deal_id: str, run_id: str, stage: str) -> None:
            calls.append((deal_id, run_id, stage))

        def load_planned_task_records(self, **_kwargs: Any) -> list[Any]:
            return [
                SimpleNamespace(
                    task=SimpleNamespace(extraction_task_id="task-a"),
                    methodology_question_record_id="question-a",
                )
            ]

        def load_non_success_attempts(self, **_kwargs: Any) -> list[Any]:
            return []

        def load_terminal_execution_records(self, **_kwargs: Any) -> list[Any]:
            return [{"terminal": True}]

    sentinel = object()
    monkeypatch.setattr(execution, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(execution, "PostgresMethodologyExtractionRepository", _Repository)
    monkeypatch.setattr(
        execution,
        "_responses_from_terminal_rows",
        lambda _rows: {"task-a": object()},
    )
    monkeypatch.setattr(
        execution,
        "DurableMethodologyExtractionExecutionResult",
        lambda **_kwargs: sentinel,
    )

    result = execution.rehydrate_postgres_methodology_execution(
        connection_factory=_connection_factory,
        tenant_id="tenant-a",
        deal_id="deal-a",
        run_id="run-a",
    )

    assert result == (sentinel, sentinel)
    assert calls == [("deal-a", "run-a", "step7")]


def test_step10_rehydration_requires_the_recomputed_final_projection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, str]] = []
    snapshot = object()

    class _Boundary:
        _tenant_id = "tenant-a"

        def load_validated_final_snapshot(self, **values: Any) -> object:
            calls.append((str(values["deal_id"]), str(values["run_id"]), "step10"))
            return snapshot

    boundary = _Boundary()
    monkeypatch.setattr(
        materialization,
        "_rehydration_boundary_and_values",
        lambda _kwargs: (
            boundary,
            {"tenant_id": "tenant-a", "deal_id": "deal-a", "run_id": "run-a"},
        ),
    )
    monkeypatch.setattr(
        materialization,
        "_validate_complete_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        materialization,
        "_sanad_shells_from_snapshot",
        lambda *_args: (("sanad",), ("link",), ("grade",), ("defect",)),
    )
    monkeypatch.setattr(
        materialization,
        "_sanad_result_from_shells",
        lambda *_args: "result",
    )

    result = materialization.rehydrate_postgres_methodology_sanads(
        connection_factory=_connection_factory,
        tenant_id="tenant-a",
        deal_id="deal-a",
        run_id="run-a",
    )

    assert result == (
        "result",
        ("sanad",),
        ("link",),
        ("grade",),
        ("defect",),
    )
    assert calls == [("deal-a", "run-a", "step10")]


def test_step10_rehydration_uses_one_repeatable_read_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    acquisitions: list[object] = []
    transaction_statements: list[str] = []
    repository_calls: list[tuple[str, object]] = []
    snapshot = object()

    class _Connection:
        def exec_driver_sql(self, statement: str) -> None:
            transaction_statements.append(statement)

    @contextmanager
    def one_snapshot_connection_factory() -> Iterator[object]:
        if acquisitions:
            raise AssertionError("Step-10 rehydration opened a second PostgreSQL snapshot")
        connection = _Connection()
        acquisitions.append(connection)
        yield connection

    class _Repository:
        def __init__(self, connection: object, _tenant_id: str) -> None:
            self.connection = connection

        def require_coverage_projection(self, *, deal_id: str, run_id: str, stage: str) -> None:
            assert (deal_id, run_id, stage) == ("deal-a", "run-a", "step10")
            repository_calls.append(("coverage", self.connection))

        def load_canonical_materialization_snapshot(self, *, deal_id: str, run_id: str) -> object:
            assert (deal_id, run_id) == ("deal-a", "run-a")
            repository_calls.append(("snapshot", self.connection))
            return snapshot

    def validate_snapshot(actual: object, *, rejected_count: int) -> None:
        assert actual is snapshot
        assert rejected_count == 0

    monkeypatch.setattr(materialization, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(
        materialization,
        "PostgresMethodologyExtractionRepository",
        _Repository,
    )
    monkeypatch.setattr(
        materialization,
        "_validate_complete_snapshot",
        validate_snapshot,
    )
    monkeypatch.setattr(
        materialization,
        "_sanad_shells_from_snapshot",
        lambda *_args: (("sanad",), ("link",), ("grade",), ("defect",)),
    )
    monkeypatch.setattr(
        materialization,
        "_sanad_result_from_shells",
        lambda *_args: "result",
    )

    result = materialization.rehydrate_postgres_methodology_sanads(
        connection_factory=one_snapshot_connection_factory,
        tenant_id="tenant-a",
        deal_id="deal-a",
        run_id="run-a",
    )

    assert result == (
        "result",
        ("sanad",),
        ("link",),
        ("grade",),
        ("defect",),
    )
    assert len(acquisitions) == 1
    assert repository_calls == [
        ("coverage", acquisitions[0]),
        ("snapshot", acquisitions[0]),
    ]
    assert transaction_statements == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"]
