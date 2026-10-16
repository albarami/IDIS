"""RED-first hermetic composition contracts for full-strict-v19 erasure.

The production service wished here coordinates injected durable ports, one
dedicated erasure connection, and an injected object-store seam.  The fakes
model transaction and session-lock boundaries rather than replacing a
PostgreSQL proof; real-role, ACL, API, and migration proofs live in their own
operator-backed suites.
"""

from __future__ import annotations

import copy
import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
ACTOR_ID = "slice100-admin"
RECOVERY_ACTOR_ID = "slice100-recovery-admin"

ATTEMPTED_AT = datetime(2026, 7, 17, 16, 0, tzinfo=UTC)
EXECUTED_AT = datetime(2026, 7, 17, 16, 1, 2, 345678, tzinfo=UTC)

ORDERED_URIS = (
    "idis://tenant-safe/deal-safe/object-02",
    "idis://tenant-safe/deal-safe/object-01",
)


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.compliance.task7_erasure")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.compliance.task7_erasure":
            raise
        pytest.fail(
            "full-strict-v19 erasure service module is missing: idis.compliance.task7_erasure",
            pytrace=False,
        )


class _InjectedFunctionFailure(RuntimeError):
    pass


class _InjectedPostCommitCrash(BaseException):
    """A process-death seam: normal exception recovery must not run."""


class _Transaction:
    def __init__(self, connection: _DedicatedConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _DedicatedConnection:
        assert self.connection.open
        assert self.connection.locked
        assert not self.connection.in_transaction
        self.connection.in_transaction = True
        self.connection.events.append("transaction:begin")
        return self.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        _ = (exc, traceback)
        assert self.connection.in_transaction
        if exc_type is None:
            callbacks = tuple(self.connection._commit_callbacks)
            self.connection._commit_callbacks.clear()
            for callback in callbacks:
                callback()
            self.connection.events.append("transaction:commit")
        else:
            self.connection._commit_callbacks.clear()
            self.connection.events.append("transaction:rollback")
        self.connection.in_transaction = False
        return False


class _DedicatedConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.open = False
        self.locked = False
        self.in_transaction = False
        self._commit_callbacks: list[Callable[[], None]] = []

    def __enter__(self) -> _DedicatedConnection:
        assert not self.open
        self.open = True
        self.events.append("connection:open")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        _ = (exc_type, exc, traceback)
        assert not self.in_transaction
        assert not self.locked
        self.open = False
        self.events.append("connection:close")
        return False

    def acquire_tenant_session_lock(self, tenant_id: str) -> None:
        assert self.open
        assert not self.in_transaction
        assert not self.locked
        assert tenant_id == TENANT_ID
        self.locked = True
        self.events.append(f"session-lock:acquire:{tenant_id}")

    def release_tenant_session_lock(self, tenant_id: str) -> None:
        assert self.open
        assert not self.in_transaction
        assert self.locked
        assert tenant_id == TENANT_ID
        self.locked = False
        self.events.append(f"session-lock:release:{tenant_id}")

    def begin(self) -> _Transaction:
        return _Transaction(self)

    def stage_on_commit(self, callback: Callable[[], None]) -> None:
        assert self.in_transaction
        self._commit_callbacks.append(callback)


class _ConnectionFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.connections: list[_DedicatedConnection] = []

    def __call__(self) -> _DedicatedConnection:
        connection = _DedicatedConnection(self.events)
        self.connections.append(connection)
        return connection

    @property
    def active(self) -> _DedicatedConnection | None:
        for connection in reversed(self.connections):
            if connection.open:
                return connection
        return None


class _ObjectStore:
    def __init__(
        self,
        *,
        events: list[str],
        factory: _ConnectionFactory,
        port: _Port,
    ) -> None:
        self.events = events
        self.factory = factory
        self.port = port
        self.present = {ORDERED_URIS[0]}
        self.calls: list[str] = []

    def reconcile_absent(self, uri: str) -> str:
        connection = self.factory.active
        assert connection is not None
        assert connection.locked
        assert not connection.in_transaction, (
            "external object I/O must never run inside a database transaction"
        )
        assert any(
            event["event_type"] == "task7.erasure.attempted" for event in self.port.lineage_events
        ), "attempted lineage must be durable before object mutation"
        assert uri in ORDERED_URIS
        self.calls.append(uri)
        if uri in self.present:
            self.present.remove(uri)
            disposition = "DELETED"
        else:
            disposition = "ALREADY_ABSENT"
        self.events.append(f"object:{disposition}:{uri.rsplit('/', 1)[-1]}")
        return disposition


class _Port:
    """Stateful durable-port double; it does not substitute for a real-role proof."""

    def __init__(
        self,
        *,
        module: ModuleType,
        events: list[str],
        fail_function_attempts: int = 0,
        fail_succeeded_persist_attempts: int = 0,
        blocked_hold_target: str | None = None,
    ) -> None:
        self.module = module
        self.events = events
        self.request_state: dict[str, Any] = {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "request_id": REQUEST_ID,
            "status": "REQUESTED",
            "executed_by": None,
            "executed_at": None,
            "counts": {},
        }
        self.lineage_events: list[dict[str, Any]] = []
        self.fail_function_attempts = fail_function_attempts
        self.fail_succeeded_persist_attempts = fail_succeeded_persist_attempts
        self.blocked_hold_target = blocked_hold_target
        self.hold_scan_calls = 0
        self.target_enumeration_calls = 0
        self.stage_calls = 0
        self.function_calls = 0
        self.lineage_persist_calls: list[str] = []
        self.last_function_result: dict[str, object] | None = None

    def load_request_state(self, *, command: object) -> Mapping[str, object]:
        self._assert_command(command)
        self.events.append("app:load-request")
        return copy.deepcopy(self.request_state)

    def load_lineage_events(self, *, command: object) -> Sequence[Mapping[str, object]]:
        self._assert_command(command)
        self.events.append("app:load-lineage")
        return tuple(copy.deepcopy(self.lineage_events))

    def list_object_targets(
        self,
        *,
        connection: _DedicatedConnection,
        command: object,
    ) -> tuple[str, ...]:
        self._assert_command(command)
        assert connection.locked
        assert not connection.in_transaction
        self.target_enumeration_calls += 1
        self.events.append("app:list-object-targets")
        return ORDERED_URIS

    def assert_no_active_holds(
        self,
        *,
        connection: _DedicatedConnection,
        command: object,
    ) -> None:
        from idis.api.errors import IdisHttpError

        self._assert_command(command)
        assert connection.locked
        assert not connection.in_transaction
        self.hold_scan_calls += 1
        self.events.append("app:check-all-holds")
        if self.blocked_hold_target is not None:
            self.events.append(f"app:hold-blocked:{self.blocked_hold_target}")
            raise IdisHttpError(
                status_code=403,
                code="DELETION_BLOCKED_BY_HOLD",
                message="Access denied.",
            )

    def persist_lineage_event(
        self,
        *,
        event: Mapping[str, object],
        request_status: str | None = None,
    ) -> str:
        event_copy = copy.deepcopy(dict(event))
        event_type = str(event_copy["event_type"])
        self.lineage_persist_calls.append(event_type)
        self.events.append(f"app:persist:{event_type}")

        if event_type == "task7.erasure.succeeded" and self.fail_succeeded_persist_attempts > 0:
            self.fail_succeeded_persist_attempts -= 1
            raise _InjectedPostCommitCrash("crash after function commit")

        digest = self.module.canonical_event_sha256(event_copy)
        existing = next(
            (
                stored
                for stored in self.lineage_events
                if stored["event_id"] == event_copy["event_id"]
            ),
            None,
        )
        if existing is not None:
            if self.module.canonical_event_sha256(existing) != digest:
                raise AssertionError("deterministic audit event identity conflict")
            return digest

        self.lineage_events.append(event_copy)
        if request_status is not None:
            assert request_status == "FAILED"
            self.request_state.update(
                {
                    "status": request_status,
                    "executed_by": ACTOR_ID,
                    "executed_at": event_copy["occurred_at"],
                }
            )
        return digest

    def stage_object_rows_reconciled(
        self,
        *,
        command: object,
        object_rows_reconciled: int,
    ) -> None:
        self._assert_command(command)
        assert type(object_rows_reconciled) is int and object_rows_reconciled >= 0
        self.stage_calls += 1
        self.events.append(f"app:stage-object-rows:{object_rows_reconciled}")
        self.request_state["counts"] = {"object_rows_reconciled": object_rows_reconciled}

    def invoke_approved_erasure(
        self,
        *,
        connection: _DedicatedConnection,
        command: object,
        attempt_id: str,
    ) -> Mapping[str, object]:
        self._assert_command(command)
        assert connection.in_transaction
        assert connection.locked
        assert any(
            event["event_type"] == "task7.erasure.attempted"
            and event["payload"]["safe"]["attempt_id"] == attempt_id
            for event in self.lineage_events
        ), "dedicated callable must observe a durable open attempted event"
        self.function_calls += 1
        self.events.append("dedicated:invoke-approved-erasure")
        if self.fail_function_attempts > 0:
            self.fail_function_attempts -= 1
            raise _InjectedFunctionFailure("injected function failure")

        staged = dict(self.request_state["counts"])
        assert staged == {"object_rows_reconciled": len(ORDERED_URIS)}
        exact_counts = {
            "document_artifacts": 2,
            "runs": 1,
            **staged,
        }

        def commit() -> None:
            self.request_state.update(
                {
                    "status": "EXECUTED",
                    "executed_by": ACTOR_ID,
                    "executed_at": EXECUTED_AT,
                    "counts": exact_counts,
                }
            )

        connection.stage_on_commit(commit)
        self.last_function_result = {
            "status": "EXECUTED",
            "executed_by": ACTOR_ID,
            "executed_at": EXECUTED_AT,
            "counts": exact_counts,
        }
        return self.last_function_result

    @staticmethod
    def _assert_command(command: Any) -> None:
        assert command.tenant_id == TENANT_ID
        assert command.deal_id == DEAL_ID
        assert command.request_id == REQUEST_ID
        assert command.actor_id in {ACTOR_ID, RECOVERY_ACTOR_ID}


def _command(module: ModuleType, *, actor_id: str = ACTOR_ID) -> Any:
    return module.Task7ErasureExecutionCommand(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        request_id=REQUEST_ID,
        actor_id=actor_id,
    )


def _service(
    module: ModuleType,
    *,
    fail_function_attempts: int = 0,
    fail_succeeded_persist_attempts: int = 0,
    hold_checker: Callable[[str, str], None] | None = None,
    blocked_hold_target: str | None = None,
    actor_ip: str | None = None,
    actor_user_agent: str | None = None,
) -> tuple[
    Any,
    _Port,
    _ObjectStore,
    _ConnectionFactory,
    list[str],
]:
    events: list[str] = []
    port = _Port(
        module=module,
        events=events,
        fail_function_attempts=fail_function_attempts,
        fail_succeeded_persist_attempts=fail_succeeded_persist_attempts,
        blocked_hold_target=blocked_hold_target,
    )
    factory = _ConnectionFactory(events)
    objects = _ObjectStore(events=events, factory=factory, port=port)

    def default_hold_checker(tenant_id: str, deal_id: str) -> None:
        assert tenant_id == TENANT_ID
        assert deal_id == DEAL_ID
        connection = factory.active
        assert connection is not None and connection.locked
        assert not connection.in_transaction
        events.append("hold-check:clear")

    actor_kwargs: dict[str, object] = {}
    if actor_ip is not None:
        actor_kwargs["ip"] = actor_ip
    if actor_user_agent is not None:
        actor_kwargs["user_agent"] = actor_user_agent
    service = module.Task7ErasureExecutionService(
        connection_factory=factory,
        port=port,
        object_store=objects,
        clock=lambda: ATTEMPTED_AT,
        hold_checker=hold_checker or default_hold_checker,
        actor_identity=module.Task7ErasureActorIdentity(
            actor_type="HUMAN",
            actor_id=ACTOR_ID,
            roles=("ADMIN",),
            **actor_kwargs,
        ),
    )
    return service, port, objects, factory, events


def _event_types(port: _Port) -> list[str]:
    return [str(event["event_type"]) for event in port.lineage_events]


def _events_of_type(port: _Port, event_type: str) -> list[dict[str, Any]]:
    return [event for event in port.lineage_events if event["event_type"] == event_type]


def _seed_open_attempt(module: ModuleType, port: _Port) -> dict[str, Any]:
    lineage = module.derive_attempt_lineage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        request_id=REQUEST_ID,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    target_sha256 = module.object_target_set_sha256(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        ordered_uris=ORDERED_URIS,
    )
    event = module.build_erasure_audit_event(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        actor_id=ACTOR_ID,
        occurred_at=ATTEMPTED_AT,
        lineage=lineage,
        event_type="task7.erasure.attempted",
        object_target_set_sha256=target_sha256,
        counts=None,
    )
    port.lineage_events.append(copy.deepcopy(event))
    return event


def test_public_service_contract_is_typed_and_dependency_injected() -> None:
    module = _module()

    assert tuple(field.name for field in fields(module.Task7ErasureExecutionCommand)) == (
        "tenant_id",
        "deal_id",
        "request_id",
        "actor_id",
    )
    assert tuple(field.name for field in fields(module.Task7ErasureExecutionResult)) == (
        "request_id",
        "status",
        "executed_at",
        "counts",
        "terminal_event_id",
        "terminal_event_sha256",
    )
    constructor = inspect.signature(module.Task7ErasureExecutionService).parameters
    assert {
        "connection_factory",
        "port",
        "object_store",
        "clock",
        "hold_checker",
        "actor_identity",
    } <= set(constructor)
    assert callable(module.Task7ErasureExecutionService.execute)


def test_success_orders_lock_read_attempt_object_stage_function_and_terminal_evidence() -> None:
    module = _module()
    service, port, objects, factory, events = _service(module)

    result = service.execute(_command(module))

    assert result == module.Task7ErasureExecutionResult(
        request_id=REQUEST_ID,
        status="EXECUTED",
        executed_at=EXECUTED_AT,
        counts={
            "document_artifacts": 2,
            "runs": 1,
            "object_rows_reconciled": 2,
        },
        terminal_event_id=_events_of_type(port, "task7.erasure.succeeded")[0]["event_id"],
        terminal_event_sha256=module.canonical_event_sha256(
            _events_of_type(port, "task7.erasure.succeeded")[0]
        ),
    )
    assert len(factory.connections) == 1
    assert _event_types(port) == [
        "task7.erasure.attempted",
        "task7.erasure.succeeded",
    ]
    assert objects.calls == list(ORDERED_URIS)
    assert port.target_enumeration_calls == 1
    assert port.stage_calls == 1
    assert port.function_calls == 1

    acquire = events.index(f"session-lock:acquire:{TENANT_ID}")
    post_lock_lineage = events.index("app:load-lineage", acquire + 1)
    hold_check = events.index("hold-check:clear")
    durable_hold_check = events.index("app:check-all-holds")
    enumerate_targets = events.index("app:list-object-targets")
    attempted = events.index("app:persist:task7.erasure.attempted")
    first_object = next(index for index, event in enumerate(events) if event.startswith("object:"))
    staged = events.index("app:stage-object-rows:2")
    invoked = events.index("dedicated:invoke-approved-erasure")
    succeeded = events.index("app:persist:task7.erasure.succeeded")
    release = events.index(f"session-lock:release:{TENANT_ID}")
    close = events.index("connection:close")
    assert acquire < post_lock_lineage < hold_check < durable_hold_check
    assert durable_hold_check < enumerate_targets
    assert enumerate_targets < attempted < first_object < staged < invoked
    assert invoked < succeeded < release < close
    assert events.count("transaction:begin") == 1
    assert events.count("transaction:commit") == 1
    assert "transaction:rollback" not in events


def test_dedicated_function_result_accepts_exact_frozen_v19_artifact_shape() -> None:
    """The function returns four fields; request scope is bound at invocation."""

    module = _module()
    service, port, _objects, _factory, _events = _service(module)

    result = service.execute(_command(module))

    assert result.status == "EXECUTED"
    assert port.last_function_result is not None
    assert set(port.last_function_result) == {
        "status",
        "executed_by",
        "executed_at",
        "counts",
    }
    assert "request_id" not in port.last_function_result


def test_present_and_already_absent_objects_both_count_as_reconciled_not_deleted() -> None:
    module = _module()
    service, port, objects, _factory, _events = _service(module)

    result = service.execute(_command(module))

    assert objects.present == set()
    assert objects.calls == list(ORDERED_URIS)
    assert result.counts["object_rows_reconciled"] == 2
    assert result.counts["document_artifacts"] == 2
    assert "objects_deleted" not in result.counts
    succeeded = _events_of_type(port, "task7.erasure.succeeded")[0]
    assert succeeded["payload"]["safe"]["counts"] == result.counts
    assert "objects_deleted" not in succeeded["payload"]["safe"]["counts"]


def test_existing_open_attempt_is_reused_without_a_second_attempted_row() -> None:
    module = _module()
    service, port, _objects, _factory, _events = _service(module)
    original = _seed_open_attempt(module, port)

    result = service.execute(_command(module))

    attempted = _events_of_type(port, "task7.erasure.attempted")
    succeeded = _events_of_type(port, "task7.erasure.succeeded")
    assert attempted == [original]
    assert len(succeeded) == 1
    assert (
        succeeded[0]["payload"]["safe"]["attempt_id"] == (original["payload"]["safe"]["attempt_id"])
    )
    assert result.terminal_event_id == succeeded[0]["event_id"]


def test_failed_attempt_is_terminal_and_retry_points_to_its_exact_event_hash() -> None:
    module = _module()
    service, port, _objects, _factory, events = _service(
        module,
        fail_function_attempts=1,
    )

    with pytest.raises(module.Task7ErasureExecutionError) as caught:
        service.execute(_command(module))

    assert caught.value.code == "ERASURE_EXECUTION_FAILED"
    assert port.request_state["status"] == "FAILED"
    assert _event_types(port) == [
        "task7.erasure.attempted",
        "task7.erasure.failed",
    ]
    failed = _events_of_type(port, "task7.erasure.failed")[0]
    first_attempted = _events_of_type(port, "task7.erasure.attempted")[0]
    assert failed["payload"]["safe"]["outcome"] == "FAILED"
    assert (
        failed["payload"]["safe"]["attempt_id"]
        == (first_attempted["payload"]["safe"]["attempt_id"])
    )
    assert "transaction:rollback" in events
    assert events[-2:] == [
        f"session-lock:release:{TENANT_ID}",
        "connection:close",
    ]

    result = service.execute(_command(module))

    attempted = _events_of_type(port, "task7.erasure.attempted")
    assert len(attempted) == 2
    second_safe = attempted[1]["payload"]["safe"]
    assert second_safe["attempt_ordinal"] == 2
    assert second_safe["prior_status"] == "FAILED"
    assert second_safe["prior_attempt_event_id"] == failed["event_id"]
    assert second_safe["prior_attempt_event_hash"] == (module.canonical_event_sha256(failed))
    assert second_safe["attempt_id"] != first_attempted["payload"]["safe"]["attempt_id"]
    assert result.status == "EXECUTED"
    assert _event_types(port)[-1] == "task7.erasure.succeeded"


def test_post_function_commit_crash_reconstructs_same_succeeded_event_without_work() -> None:
    module = _module()
    service, port, objects, factory, _events = _service(
        module,
        fail_succeeded_persist_attempts=1,
        actor_ip="203.0.113.40",
        actor_user_agent="slice100-v19-original-client",
    )

    with pytest.raises(_InjectedPostCommitCrash):
        service.execute(_command(module))

    assert port.request_state == {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "request_id": REQUEST_ID,
        "status": "EXECUTED",
        "executed_by": ACTOR_ID,
        "executed_at": EXECUTED_AT,
        "counts": {
            "document_artifacts": 2,
            "runs": 1,
            "object_rows_reconciled": 2,
        },
    }
    assert _event_types(port) == ["task7.erasure.attempted"]
    attempted = port.lineage_events[0]
    connections_after_crash = len(factory.connections)
    work_after_crash = (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    )

    result = service.execute(_command(module))

    # Terminal reconstruction still serializes through one fresh session
    # lock, but performs no object, enumeration, staging, or function work.
    assert len(factory.connections) == connections_after_crash + 1
    assert (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    ) == work_after_crash
    assert _event_types(port) == [
        "task7.erasure.attempted",
        "task7.erasure.succeeded",
    ]
    succeeded = port.lineage_events[1]
    assert succeeded["event_id"] == module.derive_event_identity(
        attempt_id=attempted["payload"]["safe"]["attempt_id"],
        event_type="task7.erasure.succeeded",
        outcome="SUCCEEDED",
    )
    assert succeeded["occurred_at"] == "2026-07-17T16:01:02.345678Z"
    assert succeeded["payload"]["safe"]["counts"] == port.request_state["counts"]
    assert result.terminal_event_id == succeeded["event_id"]
    assert result.terminal_event_sha256 == module.canonical_event_sha256(succeeded)


def test_cross_admin_post_commit_recovery_preserves_persisted_attempt_actor() -> None:
    """Crash recovery remains deterministic when a different ADMIN retries."""

    module = _module()
    service, port, objects, factory, _events = _service(
        module,
        fail_succeeded_persist_attempts=1,
        actor_ip="203.0.113.40",
        actor_user_agent="slice100-v19-original-client",
    )

    with pytest.raises(_InjectedPostCommitCrash):
        service.execute(_command(module))

    attempted = _events_of_type(port, "task7.erasure.attempted")[0]
    connections_after_crash = len(factory.connections)
    work_after_crash = (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    )
    recovery = module.Task7ErasureExecutionService(
        connection_factory=factory,
        port=port,
        object_store=objects,
        clock=lambda: EXECUTED_AT,
        hold_checker=lambda _tenant_id, _deal_id: None,
        actor_identity=module.Task7ErasureActorIdentity(
            actor_type="HUMAN",
            actor_id=RECOVERY_ACTOR_ID,
            roles=("ADMIN",),
            ip="198.51.100.27",
            user_agent="slice100-v19-recovery-client",
        ),
    )

    result = recovery.execute(_command(module, actor_id=RECOVERY_ACTOR_ID))

    assert len(factory.connections) == connections_after_crash + 1
    assert (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    ) == work_after_crash
    succeeded = _events_of_type(port, "task7.erasure.succeeded")[0]
    assert succeeded["actor"] == attempted["actor"]
    assert succeeded["actor"]["actor_id"] == ACTOR_ID
    assert succeeded["actor"]["actor_id"] != RECOVERY_ACTOR_ID
    assert succeeded["actor"]["ip"] == "203.0.113.40"
    assert succeeded["actor"]["user_agent"] == "slice100-v19-original-client"
    assert result.terminal_event_id == succeeded["event_id"]


def test_terminal_authority_is_never_derived_from_an_unlocked_split_snapshot() -> None:
    """A stale state/new lineage interleave cannot fail before lock acquisition."""

    module = _module()
    events: list[str] = []

    class _InterleavingPort(_Port):
        factory: _ConnectionFactory

        def __init__(self) -> None:
            super().__init__(module=module, events=events)
            self.unlocked_state_seen = False
            self.interleaved = False

        def _lock_is_held(self) -> bool:
            return bool(self.factory.connections and self.factory.connections[-1].locked)

        def load_request_state(self, *, command: object) -> Mapping[str, object]:
            if not self._lock_is_held():
                self._assert_command(command)
                self.unlocked_state_seen = True
                self.events.append("app:load-request:stale-before-lock")
                return copy.deepcopy(self.request_state)
            return super().load_request_state(command=command)

        def load_lineage_events(
            self,
            *,
            command: object,
        ) -> Sequence[Mapping[str, object]]:
            if self.unlocked_state_seen and not self.interleaved:
                self._assert_command(command)
                self.interleaved = True
                attempted = _seed_open_attempt(module, self)
                counts = {
                    "document_artifacts": 2,
                    "runs": 1,
                    "object_rows_reconciled": 2,
                }
                succeeded = module.build_erasure_audit_event(
                    tenant_id=TENANT_ID,
                    deal_id=DEAL_ID,
                    actor_id=ACTOR_ID,
                    occurred_at=EXECUTED_AT,
                    lineage=module.ErasureAttemptLineage(
                        **{
                            key: attempted["payload"]["safe"][key]
                            for key in (
                                "request_id",
                                "attempt_id",
                                "attempt_ordinal",
                                "prior_status",
                                "prior_attempt_event_id",
                                "prior_attempt_event_hash",
                            )
                        }
                    ),
                    event_type="task7.erasure.succeeded",
                    object_target_set_sha256=module.object_target_set_sha256(
                        tenant_id=TENANT_ID,
                        deal_id=DEAL_ID,
                        ordered_uris=ORDERED_URIS,
                    ),
                    counts=counts,
                )
                self.lineage_events.append(succeeded)
                self.request_state.update(
                    {
                        "status": "EXECUTED",
                        "executed_by": ACTOR_ID,
                        "executed_at": EXECUTED_AT,
                        "counts": counts,
                    }
                )
                self.events.append("other-worker:committed-between-split-reads")
                return copy.deepcopy(self.lineage_events)
            return super().load_lineage_events(command=command)

    port = _InterleavingPort()
    factory = _ConnectionFactory(events)
    port.factory = factory
    objects = _ObjectStore(events=events, factory=factory, port=port)
    service = module.Task7ErasureExecutionService(
        connection_factory=factory,
        port=port,
        object_store=objects,
        clock=lambda: ATTEMPTED_AT,
        hold_checker=lambda _tenant_id, _deal_id: None,
        actor_identity=module.Task7ErasureActorIdentity(
            actor_type="HUMAN",
            actor_id=ACTOR_ID,
            roles=("ADMIN",),
        ),
    )

    result = service.execute(_command(module))

    assert result.status == "EXECUTED"
    assert port.unlocked_state_seen is False
    acquired = events.index(f"session-lock:acquire:{TENANT_ID}")
    first_read = min(
        events.index("app:load-request"),
        events.index("app:load-lineage"),
    )
    assert acquired < first_read


def test_waiter_reloads_under_session_lock_and_reconstructs_concurrent_execution() -> None:
    """A pre-lock REQUESTED snapshot can never authorize post-wait object/database work."""

    module = _module()
    events: list[str] = []
    port = _Port(module=module, events=events)
    ordinary_factory = _ConnectionFactory(events)
    objects = _ObjectStore(events=events, factory=ordinary_factory, port=port)
    interleaved = False

    class _WaitingConnection(_DedicatedConnection):
        def acquire_tenant_session_lock(self, tenant_id: str) -> None:
            nonlocal interleaved
            super().acquire_tenant_session_lock(tenant_id)
            assert not interleaved
            interleaved = True
            _seed_open_attempt(module, port)
            port.request_state.update(
                {
                    "status": "EXECUTED",
                    "executed_by": ACTOR_ID,
                    "executed_at": EXECUTED_AT,
                    "counts": {
                        "document_artifacts": 2,
                        "runs": 1,
                        "object_rows_reconciled": 2,
                    },
                }
            )
            events.append("other-worker:committed-executed")

    class _WaitingFactory(_ConnectionFactory):
        def __call__(self) -> _DedicatedConnection:
            connection = _WaitingConnection(self.events)
            self.connections.append(connection)
            # The object-store double discovers the live connection through the
            # ordinary factory, so both doubles share the same active list.
            ordinary_factory.connections.append(connection)
            return connection

    waiting_factory = _WaitingFactory(events)
    service = module.Task7ErasureExecutionService(
        connection_factory=waiting_factory,
        port=port,
        object_store=objects,
        clock=lambda: ATTEMPTED_AT,
        hold_checker=lambda _tenant_id, _deal_id: events.append("hold-check:must-not-run"),
        actor_identity=module.Task7ErasureActorIdentity(
            actor_type="HUMAN",
            actor_id=ACTOR_ID,
            roles=("ADMIN",),
        ),
    )

    result = service.execute(_command(module))

    assert interleaved is True
    assert result.status == "EXECUTED"
    assert _event_types(port) == [
        "task7.erasure.attempted",
        "task7.erasure.succeeded",
    ]
    assert objects.calls == []
    assert port.target_enumeration_calls == 0
    assert port.hold_scan_calls == 0
    assert port.stage_calls == 0
    assert port.function_calls == 0
    assert "hold-check:must-not-run" not in events
    assert events.count("app:load-request") == 1
    assert events.count("app:load-lineage") == 1
    acquired = events.index(f"session-lock:acquire:{TENANT_ID}")
    concurrent_commit = events.index("other-worker:committed-executed")
    post_lock_request = events.index("app:load-request", acquired + 1)
    post_lock_lineage = events.index("app:load-lineage", post_lock_request + 1)
    succeeded = events.index("app:persist:task7.erasure.succeeded")
    released = events.index(f"session-lock:release:{TENANT_ID}")
    assert (
        acquired < concurrent_commit < post_lock_request < post_lock_lineage < succeeded < released
    )


def test_preexisting_hold_blocks_after_locked_reload_before_any_erasure_side_effect() -> None:
    """The shared hold lock covers the check through target/object reconciliation."""

    from idis.api.errors import IdisHttpError

    module = _module()
    hold_calls: list[tuple[str, str]] = []
    factory_ref: _ConnectionFactory | None = None

    def blocked_hold_checker(tenant_id: str, deal_id: str) -> None:
        assert factory_ref is not None
        connection = factory_ref.active
        assert connection is not None and connection.locked
        assert not connection.in_transaction
        hold_calls.append((tenant_id, deal_id))
        connection.events.append("hold-check:blocked")
        raise IdisHttpError(
            status_code=403,
            code="DELETION_BLOCKED_BY_HOLD",
            message="Access denied.",
        )

    service, port, objects, factory, events = _service(
        module,
        hold_checker=blocked_hold_checker,
    )
    factory_ref = factory

    with pytest.raises(IdisHttpError) as caught:
        service.execute(_command(module))

    assert caught.value.code == "DELETION_BLOCKED_BY_HOLD"
    assert hold_calls == [(TENANT_ID, DEAL_ID)]
    assert port.lineage_events == []
    assert objects.calls == []
    assert port.target_enumeration_calls == 0
    assert port.hold_scan_calls == 0
    assert port.stage_calls == 0
    assert port.function_calls == 0
    acquired = events.index(f"session-lock:acquire:{TENANT_ID}")
    post_lock_request = events.index("app:load-request", acquired + 1)
    post_lock_lineage = events.index("app:load-lineage", post_lock_request + 1)
    blocked = events.index("hold-check:blocked")
    released = events.index(f"session-lock:release:{TENANT_ID}")
    closed = events.index("connection:close")
    assert acquired < post_lock_request < post_lock_lineage < blocked < released < closed


@pytest.mark.parametrize("blocked_target", ("DOCUMENT", "ARTIFACT"))
def test_durable_full_scope_hold_scan_blocks_before_target_or_object_work(
    blocked_target: str,
) -> None:
    """Non-deal holds are checked under the held lock before object deletion."""

    from idis.api.errors import IdisHttpError

    module = _module()
    service, port, objects, _factory, events = _service(
        module,
        blocked_hold_target=blocked_target,
    )

    with pytest.raises(IdisHttpError) as caught:
        service.execute(_command(module))

    assert caught.value.code == "DELETION_BLOCKED_BY_HOLD"
    assert port.hold_scan_calls == 1
    assert port.lineage_events == []
    assert objects.calls == []
    assert port.target_enumeration_calls == 0
    assert port.stage_calls == 0
    assert port.function_calls == 0
    route_check = events.index("hold-check:clear")
    durable_check = events.index("app:check-all-holds")
    blocked = events.index(f"app:hold-blocked:{blocked_target}")
    released = events.index(f"session-lock:release:{TENANT_ID}")
    assert route_check < durable_check < blocked < released


def test_existing_succeeded_lineage_is_idempotent_and_performs_no_second_work() -> None:
    module = _module()
    service, port, objects, factory, _events = _service(module)
    first = service.execute(_command(module))
    lineage_after_first = copy.deepcopy(port.lineage_events)
    connections_after_first = len(factory.connections)
    work_after_first = (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    )

    second = service.execute(_command(module))

    assert second == first
    assert port.lineage_events == lineage_after_first
    assert len(factory.connections) == connections_after_first + 1
    assert (
        len(objects.calls),
        port.target_enumeration_calls,
        port.stage_calls,
        port.function_calls,
    ) == work_after_first
