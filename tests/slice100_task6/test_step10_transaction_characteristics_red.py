"""RED contracts for Step-10 completion transaction characteristics."""

from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest

from idis.models.run_step import StepName
from idis.services.runs.execution import (
    WorkerLeaseBoundaryCommitFailedError,
    build_postgres_step_boundary_uow,
)
from idis.services.runs.methodology_postgres_materialization_runtime import _StagedSanadWrite

TENANT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "33333333-3333-4333-8333-333333333333"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"


class _Result:
    rowcount = 1


@dataclass
class _ProbeConnection:
    events: list[str]

    def exec_driver_sql(self, statement: str) -> None:
        self.events.append(f"driver:{statement}")

    def execute(self, statement: Any, _params: dict[str, Any] | None = None) -> _Result:
        self.events.append(f"sql:{' '.join(str(statement).split())}")
        return _Result()


@contextmanager
def _transaction(connection: _ProbeConnection) -> Iterator[_ProbeConnection]:
    yield connection


class _AuditSink:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def emit_in_tx(self, _connection: _ProbeConnection, _event: dict[str, Any]) -> None:
        self._events.append("audit-in-tx")

    def emit(self, _event: dict[str, Any]) -> None:
        self._events.append("failure-audit")


def _real_staged_sanad_write() -> _StagedSanadWrite:
    return _StagedSanadWrite(
        tenant_id=TENANT_ID,
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id=RUN_ID,
        run_step_id="67676767-6767-4676-8676-676767676767",
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
        audit_sink=object(),
        records=(),
        grades=(),
        defects=(),
        factual_signature=(),
    )


def _characteristics_module() -> Any:
    return importlib.import_module("idis.services.runs.transaction_characteristics")


def _invoke_uow(
    *,
    writes: tuple[Any, ...],
    connection_factory: Any,
    events: list[str],
) -> None:
    uow = build_postgres_step_boundary_uow(
        audit_sink=_AuditSink(events),
        connection_factory=connection_factory,
    )
    uow(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
        boundary="COMPLETE",
        lease_owner_id=LEASE_OWNER_ID,
        lease_fencing_token=1,
        mutation=lambda **_kwargs: events.append("mutation"),
        native_output_writes=writes,
    )


def test_real_staged_sanad_write_exposes_immutable_typed_characteristics() -> None:
    characteristics = _characteristics_module()
    staged = _real_staged_sanad_write()

    marker = staged.transaction_characteristics
    assert isinstance(marker, characteristics.TransactionCharacteristics)
    assert marker is characteristics.REPEATABLE_READ_WRITE
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        marker.access_mode = characteristics.TransactionAccessMode.READ_ONLY
    # Frozen+slots dataclasses may surface ClassVar assignment as TypeError on Python 3.12.
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        staged.transaction_characteristics = characteristics.REPEATABLE_READ_WRITE


def test_identical_typed_markers_coalesce_to_one_static_first_statement() -> None:
    characteristics = _characteristics_module()
    events: list[str] = []
    equal_marker = characteristics.TransactionCharacteristics(
        isolation_level=characteristics.TransactionIsolationLevel.REPEATABLE_READ,
        access_mode=characteristics.TransactionAccessMode.READ_WRITE,
    )
    assert equal_marker == characteristics.REPEATABLE_READ_WRITE
    assert equal_marker is not characteristics.REPEATABLE_READ_WRITE

    @dataclass(frozen=True, slots=True)
    class _MarkedWrite:
        name: str
        transaction_characteristics: ClassVar[Any] = equal_marker

        def persist(self, **_kwargs: Any) -> None:
            events.append(self.name)

    connection = _ProbeConnection(events)
    _invoke_uow(
        writes=(_MarkedWrite("write-one"), _MarkedWrite("write-two")),
        connection_factory=lambda: _transaction(connection),
        events=events,
    )

    assert events.count("driver:SET TRANSACTION ISOLATION LEVEL REPEATABLE READ") == 1
    assert events[:2] == [
        "driver:SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        f"sql:SET LOCAL idis.tenant_id = '{TENANT_ID}'",
    ]
    assert events.index("write-one") < events.index("write-two") < events.index("mutation")
    assert not any("READ ONLY" in event for event in events)


@pytest.mark.parametrize("invalid_kind", ["malformed", "read_only", "conflicting"])
def test_invalid_or_conflicting_markers_fail_before_connection_or_writes(
    invalid_kind: str,
) -> None:
    characteristics = _characteristics_module()
    opened = 0
    invoked = 0
    events: list[str] = []

    class _MalformedWrite:
        transaction_characteristics = "REPEATABLE READ"

        def persist(self, **_kwargs: Any) -> None:
            nonlocal invoked
            invoked += 1

    class _ReadOnlyWrite:
        transaction_characteristics = characteristics.TransactionCharacteristics(
            isolation_level=characteristics.TransactionIsolationLevel.REPEATABLE_READ,
            access_mode=characteristics.TransactionAccessMode.READ_ONLY,
        )

        def persist(self, **_kwargs: Any) -> None:
            nonlocal invoked
            invoked += 1

    class _ReadWriteWrite:
        transaction_characteristics = characteristics.REPEATABLE_READ_WRITE

        def persist(self, **_kwargs: Any) -> None:
            nonlocal invoked
            invoked += 1

    def connection_factory() -> Iterator[_ProbeConnection]:
        nonlocal opened
        opened += 1
        return _transaction(_ProbeConnection(events))

    writes: tuple[Any, ...]
    if invalid_kind == "malformed":
        writes = (_MalformedWrite(),)
    elif invalid_kind == "read_only":
        writes = (_ReadOnlyWrite(),)
    else:
        writes = (_ReadWriteWrite(), _ReadOnlyWrite())

    with pytest.raises(WorkerLeaseBoundaryCommitFailedError):
        _invoke_uow(writes=writes, connection_factory=connection_factory, events=events)

    assert opened == 0
    assert invoked == 0


def test_unmarked_default_boundary_keeps_existing_read_committed_shape() -> None:
    events: list[str] = []

    def unmarked_write(**_kwargs: Any) -> None:
        events.append("unmarked-write")

    connection = _ProbeConnection(events)
    _invoke_uow(
        writes=(unmarked_write,),
        connection_factory=lambda: _transaction(connection),
        events=events,
    )

    assert not any(event.startswith("driver:") for event in events)
    assert events[0] == f"sql:SET LOCAL idis.tenant_id = '{TENANT_ID}'"
    assert events.index("unmarked-write") < events.index("mutation")
