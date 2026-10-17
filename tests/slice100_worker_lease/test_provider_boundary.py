"""Task 1 tests for provider-phase database isolation and fail-closed writes."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

import idis.pipeline.worker as worker_module
from idis.models.run_step import StepName
from idis.persistence.db import DatabaseConfigError, set_tenant_local
from idis.persistence.provider_reads import (
    ProviderReadConnection,
    UnstagedProviderPersistenceError,
)
from idis.persistence.repositories.layer1_evidence import PostgresLayer1EvidenceRepository
from idis.pipeline.worker import (
    WorkerLeaseConfig,
    _build_provider_output_guard,
    build_postgres_claimed_run_runtime,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError

_TENANT_ID = "11111111-1111-4111-8111-111111111111"


class _ConsumedResult:
    def __init__(self, value: object, connection: _ReadConnection) -> None:
        self._value = value
        self._connection = connection

    def scalar_one(self) -> object:
        assert self._connection.active is False
        return self._value


class _FrozenResult:
    def __init__(self, value: object, connection: _ReadConnection) -> None:
        self._value = value
        self._connection = connection

    def __call__(self) -> _ConsumedResult:
        return _ConsumedResult(self._value, self._connection)


class _LiveResult:
    def __init__(self, value: object, connection: _ReadConnection) -> None:
        self._value = value
        self._connection = connection

    def freeze(self) -> _FrozenResult:
        assert self._connection.active is True
        return _FrozenResult(self._value, self._connection)


class _IgnoredResult:
    pass


class _ReadConnection:
    def __init__(self, value: object) -> None:
        self.value = value
        self.active = False
        self.statements: list[str] = []

    def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> Any:
        rendered = str(statement).strip()
        self.statements.append(rendered)
        if rendered.upper().startswith("SELECT"):
            return _LiveResult(self.value, self)
        return _IgnoredResult()

    def exec_driver_sql(self, statement: str, *_args: Any, **_kwargs: Any) -> Any:
        self.statements.append(statement.strip())
        return _LiveResult(self.value, self)


class _ReadConnectionFactory:
    def __init__(self) -> None:
        self.connections: list[_ReadConnection] = []

    @contextmanager
    def __call__(self) -> Any:
        connection = _ReadConnection(value=len(self.connections) + 1)
        connection.active = True
        self.connections.append(connection)
        try:
            yield connection
        finally:
            connection.active = False


def test_provider_reads_use_one_detached_tenant_transaction_per_query() -> None:
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )

    first = connection.execute(text("SELECT 1")).scalar_one()
    second = connection.execute(text("SELECT 2")).scalar_one()

    assert (first, second) == (1, 2)
    assert len(factory.connections) == 2
    for opened in factory.connections:
        assert opened.active is False
        assert opened.statements[0] == "SET TRANSACTION READ ONLY"
        assert opened.statements[1] == f"SET LOCAL idis.tenant_id = '{_TENANT_ID}'"
        assert opened.statements[2].startswith("SELECT")


@pytest.mark.parametrize(
    "tenant", ["abcdefab-cdef-4abc-8abc-abcdefabcdef", "ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF"]
)
def test_repository_same_tenant_assertion_opens_no_connection_before_detached_read(
    tenant: str,
) -> None:
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(connection_factory=factory, tenant_id=tenant)

    PostgresLayer1EvidenceRepository(connection, tenant)
    assert factory.connections == []
    assert connection.execute(text(f"SET LOCAL idis.tenant_id = '{tenant}'")) is None
    connection.assert_no_write_attempt()
    assert connection.execute(text("SELECT 1")).scalar_one() == 1
    assert len(factory.connections) == 1
    assert factory.connections[0].active is False
    assert factory.connections[0].statements == [
        "SET TRANSACTION READ ONLY",
        f"SET LOCAL idis.tenant_id = '{tenant}'",
        "SELECT 1",
    ]


@pytest.mark.parametrize(
    "tenant", ["not-a-uuid", "", "abcdefab-cdef-4abc-8abc-abcdefabcdef; SELECT 1"]
)
def test_provider_scope_constructor_reuses_existing_uuid_format_refusal(tenant: str) -> None:
    factory = _ReadConnectionFactory()
    with pytest.raises(DatabaseConfigError):
        ProviderReadConnection(connection_factory=factory, tenant_id=tenant)
    assert factory.connections == []


class _TextClauseSubclass(TextClause):
    pass


@pytest.mark.parametrize(
    "variant",
    [
        "wrong-tenant",
        "tenant-case",
        "sql-case",
        "trailing-space",
        "semicolon",
        "extra-sql",
        "string",
        "subclass",
        "empty-parameters",
        "parameters",
        "args",
        "kwargs",
    ],
)
def test_only_exact_typed_tenant_assertion_is_accepted_and_denials_latch(variant: str) -> None:
    tenant = "abcdefab-cdef-4abc-8abc-abcdefabcdef"
    sql = f"SET LOCAL idis.tenant_id = '{tenant}'"
    statement: Any = text(sql)
    parameters: Any = None
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = {}
    if variant == "wrong-tenant":
        statement = text(f"SET LOCAL idis.tenant_id = '{_TENANT_ID}'")
    elif variant == "tenant-case":
        statement = text(f"SET LOCAL idis.tenant_id = '{tenant.upper()}'")
    elif variant == "sql-case":
        statement = text(sql.lower())
    elif variant == "trailing-space":
        statement = text(sql + " ")
    elif variant == "semicolon":
        statement = text(sql + ";")
    elif variant == "extra-sql":
        statement = text(sql + "; SELECT 1")
    elif variant == "string":
        statement = sql
    elif variant == "subclass":
        statement = _TextClauseSubclass(sql)
    elif variant == "empty-parameters":
        parameters = {}
    elif variant == "parameters":
        parameters = {"tenant": tenant}
    elif variant == "args":
        args = ({},)
    else:
        kwargs = {"execution_options": {}}
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(connection_factory=factory, tenant_id=tenant)
    with pytest.raises(UnstagedProviderPersistenceError):
        connection.execute(statement, parameters, *args, **kwargs)
    assert factory.connections == []
    with pytest.raises(UnstagedProviderPersistenceError):
        connection.assert_no_write_attempt()


def test_driver_sql_tenant_set_stays_denied_and_ordinary_connection_still_executes_set() -> None:
    factory = _ReadConnectionFactory()
    facade = ProviderReadConnection(connection_factory=factory, tenant_id=_TENANT_ID)
    with pytest.raises(UnstagedProviderPersistenceError):
        facade.exec_driver_sql(f"SET LOCAL idis.tenant_id = '{_TENANT_ID}'")
    assert factory.connections == []
    with pytest.raises(UnstagedProviderPersistenceError):
        facade.assert_no_write_attempt()
    with factory() as ordinary:
        set_tenant_local(ordinary, _TENANT_ID)
        assert ordinary.statements == [f"SET LOCAL idis.tenant_id = '{_TENANT_ID}'"]


def test_swallowed_foreign_tenant_assertion_cannot_reach_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _ReadConnectionFactory()
    facade = ProviderReadConnection(connection_factory=factory, tenant_id=_TENANT_ID)
    guard = _build_provider_output_guard(provider_reads=facade, contracts={})
    ctx = RunContext(
        run_id="run-foreign-scope-latch",
        tenant_id=_TENANT_ID,
        deal_id="deal-scope-latch",
        mode="SNAPSHOT",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        provider_output_guard_fn=guard,
    )
    orchestrator = RunOrchestrator(audit_sink=object(), run_steps_repo=object())  # type: ignore[arg-type]

    def swallowed_scope(_step: Any, _ctx: Any, _accumulated: Any) -> dict[str, bool]:
        with suppress(UnstagedProviderPersistenceError):
            set_tenant_local(facade, "22222222-2222-4222-8222-222222222222")
        return {"completed_looking": True}

    monkeypatch.setattr(orchestrator, "_dispatch_step", swallowed_scope)
    with pytest.raises(UnstagedProviderPersistenceError):
        orchestrator._dispatch_guarded_step(StepName.ENRICHMENT, ctx, {})
    assert factory.connections == []


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO claims (claim_id) VALUES ('x')",
        "UPDATE claims SET claim_text = 'late'",
        "DELETE FROM claims",
        "WITH changed AS (UPDATE claims SET claim_text = 'late' RETURNING *) SELECT * FROM changed",
        "SELECT nextval('claims_sequence')",
        "SELECT * FROM claims FOR UPDATE",
        "SELECT 1; DELETE FROM claims",
    ],
)
def test_provider_database_mutations_fail_before_a_connection_is_opened(statement: str) -> None:
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )

    with pytest.raises(UnstagedProviderPersistenceError) as exc_info:
        connection.execute(text(statement))

    assert exc_info.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert factory.connections == []
    with pytest.raises(UnstagedProviderPersistenceError):
        connection.assert_no_write_attempt()


@pytest.mark.parametrize("method_name", ["begin", "begin_nested", "commit", "rollback"])
def test_provider_cannot_own_or_finish_a_database_transaction(method_name: str) -> None:
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )

    with pytest.raises(UnstagedProviderPersistenceError):
        getattr(connection, method_name)()

    assert factory.connections == []
    with pytest.raises(UnstagedProviderPersistenceError):
        connection.assert_no_write_attempt()


def test_swallowed_provider_write_attempt_still_blocks_completion() -> None:
    factory = _ReadConnectionFactory()
    connection = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )

    with suppress(UnstagedProviderPersistenceError):
        connection.execute(text("UPDATE claims SET claim_text = 'late'"))

    with pytest.raises(UnstagedProviderPersistenceError) as exc_info:
        connection.assert_no_write_attempt()

    assert exc_info.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert factory.connections == []


@pytest.mark.parametrize(
    "step_name",
    [
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
        StepName.EXTRACT,
        StepName.GRADE,
        StepName.CALC,
        StepName.GRAPH_EVIDENCE,
        StepName.RAG_EVIDENCE,
        StepName.DELIVERABLES,
    ],
)
def test_unstaged_durable_producer_is_blocked_before_dispatch(
    step_name: StepName,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _ReadConnectionFactory()
    provider_reads = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )
    guard = _build_provider_output_guard(
        provider_reads=provider_reads,
        contracts={
            StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: object(),
            StepName.LAYER2_IC_CHALLENGE: object(),
        },
    )
    ctx = RunContext(
        run_id="run-provider-boundary",
        tenant_id=_TENANT_ID,
        deal_id="deal-provider-boundary",
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        provider_output_guard_fn=guard,
    )
    dispatched: list[StepName] = []
    orchestrator = RunOrchestrator(audit_sink=object(), run_steps_repo=object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        orchestrator,
        "_dispatch_step",
        lambda current, _ctx, _accumulated: dispatched.append(current) or {},
    )

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._dispatch_guarded_step(step_name, ctx, {})

    assert exc_info.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert dispatched == []
    assert factory.connections == []


def test_swallowed_direct_write_cannot_reach_completion_after_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _ReadConnectionFactory()
    provider_reads = ProviderReadConnection(
        connection_factory=factory,
        tenant_id=_TENANT_ID,
    )
    guard = _build_provider_output_guard(provider_reads=provider_reads, contracts={})
    ctx = RunContext(
        run_id="run-provider-latch",
        tenant_id=_TENANT_ID,
        deal_id="deal-provider-latch",
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        provider_output_guard_fn=guard,
    )
    orchestrator = RunOrchestrator(audit_sink=object(), run_steps_repo=object())  # type: ignore[arg-type]

    def swallow_write(
        _step: StepName, _ctx: RunContext, _accumulated: dict[str, Any]
    ) -> dict[str, Any]:
        with suppress(UnstagedProviderPersistenceError):
            provider_reads.execute(text("UPDATE claims SET claim_text = 'late'"))
        return {"completed_looking": True}

    monkeypatch.setattr(orchestrator, "_dispatch_step", swallow_write)

    with pytest.raises(UnstagedProviderPersistenceError):
        orchestrator._dispatch_guarded_step(StepName.ENRICHMENT, ctx, {})

    assert factory.connections == []


def test_default_claimed_runtime_releases_bootstrap_connection_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _ReadConnectionFactory()
    captured: dict[str, Any] = {}

    def build_context(**kwargs: Any) -> RunContext:
        captured.update(kwargs)
        assert kwargs["db_conn"].active is True
        return RunContext(
            run_id=str(kwargs["run_data"]["run_id"]),
            tenant_id=str(kwargs["tenant_id"]),
            deal_id=str(kwargs["run_data"]["deal_id"]),
            mode=str(kwargs["run_data"]["mode"]),
            documents=[],
            extract_fn=lambda **_kwargs: {},
            grade_fn=lambda **_kwargs: {},
            provider_output_guard_fn=kwargs["provider_output_guard_fn"],
        )

    monkeypatch.setattr(worker_module, "_default_run_context_factory", build_context)
    runtime = build_postgres_claimed_run_runtime(
        tenant_id=_TENANT_ID,
        run_data={
            "run_id": "22222222-2222-4222-8222-222222222222",
            "deal_id": "33333333-3333-4333-8333-333333333333",
            "mode": "FULL",
            "status": "RUNNING",
            "lease_owner_id": "44444444-4444-4444-8444-444444444444",
            "lease_fencing_token": 3,
        },
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
        audit_sink=object(),
        connection_factory=factory,
    )

    assert len(factory.connections) == 1
    assert factory.connections[0].active is False
    assert isinstance(captured["provider_db_conn"], ProviderReadConnection)
    assert runtime.context.provider_output_guard_fn is captured["provider_output_guard_fn"]
    assert not hasattr(runtime, "_provider_connection_manager")
