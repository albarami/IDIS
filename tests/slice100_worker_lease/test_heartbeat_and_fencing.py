# ruff: noqa: F403, F405
"""Worker-lease heartbeat, boundary, and fencing contracts."""

from __future__ import annotations

# The shared harness deliberately exports private fixtures and probes.
from ._support import *  # noqa: F403


def test_production_boundary_uow_uses_fresh_rls_transactions_and_staged_native_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the production builder; injected orchestration doubles are not persistence proof."""
    builder = getattr(execution_module, "build_postgres_step_boundary_uow", None)
    assert callable(builder), "execution runtime is missing the production Postgres boundary UoW"
    _require_parameters(
        builder,
        {"audit_sink", "connection_factory"},
        contract="production Postgres boundary UoW builder",
    )
    durable = _BoundaryProbeDurableState()
    connections: list[_BoundaryProbeConnection] = []

    def connection_factory() -> _BoundaryProbeTransaction:
        connection = _BoundaryProbeConnection(len(connections) + 1, durable)
        connections.append(connection)
        return _BoundaryProbeTransaction(connection)

    rls_connections: list[_BoundaryProbeConnection] = []

    def establish_rls(connection: _BoundaryProbeConnection, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        rls_connections.append(connection)
        connection.record("rls")

    monkeypatch.setattr(execution_module, "set_tenant_local", establish_rls, raising=False)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)

    class AuditSink:
        def emit_in_tx(self, conn: _BoundaryProbeConnection, event: dict[str, Any]) -> None:
            assert event["payload"]["safe"]["code"] == "WORKER_LEASE_HEARTBEAT_RENEWED"
            conn.record("heartbeat-audit")

    uow = builder(audit_sink=AuditSink(), connection_factory=connection_factory)
    _require_parameters(
        uow,
        {
            "run_id",
            "tenant_id",
            "step_name",
            "boundary",
            "lease_owner_id",
            "lease_fencing_token",
            "mutation",
            "native_output_writes",
        },
        contract="production boundary UoW callback",
    )

    def start_mutation(*, db_conn: _BoundaryProbeConnection, **_kwargs: Any) -> None:
        db_conn.record("step-running")

    uow(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        boundary="START",
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
        mutation=start_mutation,
        native_output_writes=(),
    )
    assert all(connection.closed for connection in connections)
    assert not any(not connection.closed for connection in connections)
    durable.events.append(("provider-with-no-boundary-transaction", 0))

    def staged_native_write(*, db_conn: _BoundaryProbeConnection, **_kwargs: Any) -> None:
        db_conn.record("native-output")

    def complete_mutation(*, db_conn: _BoundaryProbeConnection, **_kwargs: Any) -> None:
        db_conn.record("step-completed")

    uow(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        boundary="COMPLETE",
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
        mutation=complete_mutation,
        native_output_writes=(staged_native_write,),
    )

    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert rls_connections == connections
    assert all(connection.closed for connection in connections)
    assert all(connection.commits == 1 and connection.rollbacks == 0 for connection in connections)
    assert ("provider-with-no-boundary-transaction", 0) in durable.events
    for connection in connections:
        assert len(connection.sql_params) == 1
        bound_values = set(connection.sql_params[0].values())
        assert {TENANT_ID, RUN_ID, FIRST_WORKER_ID, 1, LEASE_SECONDS} <= bound_values
        events = [
            event
            for event, connection_id in durable.events
            if connection_id == connection.connection_id
        ]
        assert events[0] == "rls"
        lease_updates = [
            event for event in events if event.startswith("sql:") and "UPDATE" in event
        ]
        assert len(lease_updates) == 1
        lease_sql = lease_updates[0]
        for predicate in (
            "TENANT_ID",
            "RUN_ID",
            "STATUS",
            "LEASE_OWNER_ID",
            "LEASE_FENCING_TOKEN",
            "LEASE_EXPIRES_AT",
        ):
            assert predicate in lease_sql
        assert "RUNNING" in lease_sql or "RUNNING" in {
            str(value).upper() for value in connection.sql_params[0].values()
        }
        assert re.search(
            r"LEASE_EXPIRES_AT\s*>\s*(?:STATEMENT_TIMESTAMP\(\)|NOW\(\)|CURRENT_TIMESTAMP)",
            lease_sql,
        )
        assert events[-1] == "heartbeat-audit"
    completion_events = [event for event, connection_id in durable.events if connection_id == 2]
    assert "native-output" in completion_events
    assert "step-completed" in completion_events


def test_production_boundary_uow_zero_row_renewal_fences_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = getattr(execution_module, "build_postgres_step_boundary_uow", None)
    assert callable(builder), "execution runtime is missing the production Postgres boundary UoW"
    durable = _BoundaryProbeDurableState()
    connection = _BoundaryProbeConnection(1, durable, lease_update_rowcount=0)
    monkeypatch.setattr(
        execution_module,
        "set_tenant_local",
        lambda conn, tenant_id: tenant_id == TENANT_ID and conn.record("rls"),
        raising=False,
    )
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)

    class AuditSink:
        def emit_in_tx(self, _conn: Any, _event: dict[str, Any]) -> None:
            raise AssertionError("a zero-row lease CAS must not emit a success audit")

        def emit(self, _event: dict[str, Any]) -> None:
            raise AssertionError("fencing is not a boundary-commit failure audit")

    write_calls: list[str] = []
    uow = builder(
        audit_sink=AuditSink(),
        connection_factory=lambda: _BoundaryProbeTransaction(connection),
    )
    caught: Exception | None = None
    try:
        uow(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            step_name=StepName.INGEST_CHECK,
            boundary="COMPLETE",
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=3,
            mutation=lambda **_kwargs: write_calls.append("step"),
            native_output_writes=(lambda **_kwargs: write_calls.append("native"),),
        )
    except Exception as exc:
        caught = exc

    assert caught is not None
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"
    assert write_calls == []
    assert durable.events == []
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True
    assert len(connection.sql_params) == 1
    assert {TENANT_ID, RUN_ID, SECOND_WORKER_ID, 3, LEASE_SECONDS} <= set(
        connection.sql_params[0].values()
    )


@pytest.mark.parametrize(
    ("failure_point", "expected_code"),
    [
        ("heartbeat_database", "WORKER_LEASE_HEARTBEAT_FAILED"),
        ("heartbeat_audit", "WORKER_LEASE_HEARTBEAT_FAILED"),
        ("native", "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"),
        ("step", "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"),
        ("completion_audit", "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"),
        ("commit", "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"),
    ],
)
def test_production_boundary_uow_rolls_back_the_complete_write_set(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    expected_code: str,
) -> None:
    builder = getattr(execution_module, "build_postgres_step_boundary_uow", None)
    assert callable(builder), "execution runtime is missing the production Postgres boundary UoW"
    durable = _BoundaryProbeDurableState()
    connection = _BoundaryProbeConnection(
        1,
        durable,
        fail_commit=failure_point == "commit",
        fail_lease_update=failure_point == "heartbeat_database",
    )

    def establish_rls(conn: _BoundaryProbeConnection, _tenant_id: str) -> None:
        conn.record("rls")

    monkeypatch.setattr(execution_module, "set_tenant_local", establish_rls, raising=False)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)

    postrollback_events: list[dict[str, Any]] = []

    class AuditSink:
        def emit_in_tx(self, conn: _BoundaryProbeConnection, _event: dict[str, Any]) -> None:
            conn.record("heartbeat-audit")
            if failure_point == "heartbeat_audit":
                raise AuditSinkError("injected boundary audit failure")

        def emit(self, event: dict[str, Any]) -> None:
            postrollback_events.append(event)

    uow = builder(
        audit_sink=AuditSink(),
        connection_factory=lambda: _BoundaryProbeTransaction(connection),
    )

    def native_write(*, db_conn: _BoundaryProbeConnection, **_kwargs: Any) -> None:
        db_conn.record("native-output")
        if failure_point == "native":
            raise OSError("injected native write failure")

    def step_write(*, db_conn: _BoundaryProbeConnection, **_kwargs: Any) -> None:
        db_conn.record("step-completed")
        if failure_point == "step":
            raise OSError("injected step write failure")
        if failure_point == "completion_audit":
            db_conn.record("completion-boundary-audit")
            raise AuditSinkError("injected completion-boundary audit failure")

    caught: Exception | None = None
    try:
        uow(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            step_name=StepName.INGEST_CHECK,
            boundary="COMPLETE",
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            mutation=step_write,
            native_output_writes=(native_write,),
        )
    except Exception as exc:
        caught = exc
    assert caught is not None
    assert getattr(caught, "code", None) == expected_code
    assert durable.events == []
    assert connection.closed is True
    assert connection.commits == 0
    assert connection.rollbacks == 1
    if expected_code == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED":
        assert len(postrollback_events) == 1
        assert (
            postrollback_events[0]["payload"]["safe"]["code"]
            == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
        )
    else:
        assert postrollback_events == []


def test_zombie_step_write_is_rejected_with_stable_fencing_code() -> None:
    """A late writer carrying an old claim token must never mutate the step ledger."""
    step_repository = InMemoryRunStepsRepository(TENANT_ID)
    _require_parameters(
        step_repository.create,
        {"step", "lease_owner_id", "lease_fencing_token"},
        contract="fenced step create write",
    )
    runs_repository = _seed_queued_run()
    _require_parameters(
        runs_repository.claim_queued_runs,
        {"limit", "lease_owner_id", "lease_seconds"},
        contract="zombie fencing setup claim",
    )
    runs_repository.claim_queued_runs(
        limit=1,
        lease_owner_id=SECOND_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
    )
    _require_parameters(
        step_repository.update,
        {"step", "lease_owner_id", "lease_fencing_token"},
        contract="fenced step completion write",
    )
    step = RunStep(
        step_id="66666666-6666-4666-8666-666666666666",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.DATA_ROOM_INVENTORY_PACKAGE,
        step_order=STEP_ORDER[StepName.DATA_ROOM_INVENTORY_PACKAGE],
        status=StepStatus.RUNNING,
    )

    step_repository.create(
        step,
        lease_owner_id=SECOND_WORKER_ID,
        lease_fencing_token=1,
    )
    zombie_completion = step.model_copy(deep=True)
    zombie_completion.status = StepStatus.COMPLETED
    zombie_completion.finished_at = "2026-07-15T00:00:01Z"

    caught: Exception | None = None
    try:
        step_repository.update(
            zombie_completion,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=0,
        )
    except Exception as exc:  # the implementation owns the concrete typed exception
        caught = exc

    assert caught is not None, "a zombie step write unexpectedly succeeded"
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"
    persisted = step_repository.get_step(RUN_ID, step.step_name)
    assert persisted is not None
    assert persisted.status == StepStatus.RUNNING


def test_step_update_cannot_use_one_run_lease_to_mutate_another_run() -> None:
    """The persisted target row, not only the supplied model, must match the leased run."""
    runs_module.clear_in_memory_runs_store()
    run_steps_module.clear_run_steps_store()
    runs_repository = InMemoryRunsRepository(TENANT_ID)
    run_b = "77777777-7777-4777-8777-777777777777"
    run_a = "88888888-8888-4888-8888-888888888888"
    runs_repository.create(
        run_id=run_b,
        deal_id="99999999-9999-4999-8999-999999999999",
        mode="FULL",
    )
    runs_repository.claim_queued_runs(
        limit=1,
        lease_owner_id=SECOND_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
    )
    runs_repository.create(
        run_id=run_a,
        deal_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        mode="FULL",
    )
    step_repository = InMemoryRunStepsRepository(TENANT_ID)
    step = RunStep(
        step_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        run_id=run_a,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=STEP_ORDER[StepName.INGEST_CHECK],
        status=StepStatus.RUNNING,
    )
    step_repository.create(step)
    forged = step.model_copy(deep=True)
    forged.run_id = run_b
    forged.status = StepStatus.COMPLETED

    with pytest.raises(runs_module.WorkerLeaseFencedError) as caught:
        step_repository.update(
            forged,
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=1,
        )

    assert caught.value.code == "WORKER_LEASE_FENCED"
    persisted = step_repository.get_step(run_a, StepName.INGEST_CHECK)
    assert persisted is not None
    assert persisted.run_id == run_a
    assert persisted.status == StepStatus.RUNNING


def test_postgres_fenced_step_update_binds_the_outer_target_to_the_exact_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target-row identity is the persistence contract, independent of SQL shape."""
    monkeypatch.setattr(run_steps_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection()
    repository = PostgresRunStepsRepository(connection, TENANT_ID)
    step = RunStep(
        step_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=STEP_ORDER[StepName.INGEST_CHECK],
        status=StepStatus.COMPLETED,
    )

    repository.update(
        step,
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
    )

    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    assert "FOR UPDATE" in normalized
    assert re.search(r"RUN_STEPS\.STEP_ID\s*=\s*:\w+", normalized)
    assert re.search(r"RUN_STEPS\.TENANT_ID\s*=\s*:\w+", normalized)
    assert re.search(
        r"RUN_STEPS\.RUN_ID\s*=\s*ELIGIBLE_RUN\.RUN_ID",
        normalized,
    )
    assert params["run_id"] == RUN_ID
    assert params["tenant_id"] == TENANT_ID


def test_ownerless_step_writes_allow_nonrunning_rows_but_reject_a_live_lease() -> None:
    runs_repository = _seed_queued_run()
    step_repository = InMemoryRunStepsRepository(TENANT_ID)
    step = RunStep(
        step_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=STEP_ORDER[StepName.INGEST_CHECK],
        status=StepStatus.PENDING,
    )
    step_repository.create(step)
    queued_update = step.model_copy(deep=True)
    queued_update.status = StepStatus.RUNNING
    step_repository.update(queued_update)
    runs_repository.claim_queued_runs(
        limit=1,
        lease_owner_id=FIRST_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
    )
    before = step_repository.get_step(RUN_ID, StepName.INGEST_CHECK)
    assert before is not None
    ownerless_completion = before.model_copy(deep=True)
    ownerless_completion.status = StepStatus.COMPLETED

    with pytest.raises(runs_module.WorkerLeaseFencedError) as update_error:
        step_repository.update(ownerless_completion)
    with pytest.raises(runs_module.WorkerLeaseFencedError) as create_error:
        step_repository.create(
            RunStep(
                step_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                run_id=RUN_ID,
                tenant_id=TENANT_ID,
                step_name=StepName.DOCUMENT_PREFLIGHT,
                step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
                status=StepStatus.PENDING,
            )
        )

    assert update_error.value.code == "WORKER_LEASE_FENCED"
    assert create_error.value.code == "WORKER_LEASE_FENCED"
    assert step_repository.get_step(RUN_ID, StepName.INGEST_CHECK) == before
    assert step_repository.get_step(RUN_ID, StepName.DOCUMENT_PREFLIGHT) is None


@pytest.mark.parametrize("operation", ["create", "update"])
def test_postgres_ownerless_step_write_zero_row_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.setattr(run_steps_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(rowcounts=[0])
    repository = PostgresRunStepsRepository(connection, TENANT_ID)
    step = RunStep(
        step_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=STEP_ORDER[StepName.INGEST_CHECK],
        status=StepStatus.RUNNING,
    )

    with pytest.raises(runs_module.WorkerLeaseFencedError) as caught:
        getattr(repository, operation)(step)

    assert caught.value.code == "WORKER_LEASE_FENCED"
    normalized = _normalized_sql(connection.calls[0][0])
    assert "FOR UPDATE" in normalized
    assert "STATUS <> 'RUNNING'" in normalized


def test_failed_boundary_cas_fences_before_the_next_step_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-row start-boundary CAS fences before dispatching the next step."""
    _require_run_context_lease_fields()
    state = _AtomicBoundaryState(RUN_ID, FIRST_WORKER_ID, 1)
    repository = _TransactionalRunStepsRepository(state)
    audit_sink = _TransactionalAuditSink(state)
    uow = _AtomicStepBoundaryUoW(state, repository, audit_sink)
    after_commits: list[tuple[StepName, str]] = []
    dispatched: list[StepName] = []

    def fence_before_second_start(**kwargs: Any) -> None:
        if kwargs["step_name"] == StepName.EXTRACT and kwargs["boundary"] == "START":
            state.lease_owner_id = SECOND_WORKER_ID
            state.lease_fencing_token = 2
        uow(**kwargs)

    context = _context(
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
        step_boundary_uow_fn=fence_before_second_start,
        after_step_boundary_commit_fn=lambda **kwargs: after_commits.append(
            (kwargs["step_name"], kwargs["boundary"])
        ),
        execute_with_lease_heartbeat_fn=lambda *, operation, **_kwargs: operation(),
    )
    context.mode = "SNAPSHOT"
    monkeypatch.setattr(
        orchestrator_module,
        "SNAPSHOT_STEPS",
        [StepName.INGEST_CHECK, StepName.EXTRACT],
    )
    monkeypatch.setattr(
        RunOrchestrator,
        "_dispatch_step",
        lambda _self, step_name, _ctx, _accumulated: dispatched.append(step_name) or {},
    )

    caught: Exception | None = None
    try:
        RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repository).execute(context)
    except Exception as exc:
        caught = exc

    assert caught is not None
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"
    assert dispatched == [StepName.INGEST_CHECK]
    assert [(step, boundary) for step, boundary, _owner, _token in uow.commits] == [
        (StepName.INGEST_CHECK, "START"),
        (StepName.INGEST_CHECK, "COMPLETE"),
    ]
    assert after_commits == [
        (StepName.INGEST_CHECK, "START"),
        (StepName.INGEST_CHECK, "COMPLETE"),
    ]
    assert StepName.EXTRACT not in state.steps
    assert StepName.EXTRACT not in state.native_outputs


@pytest.mark.parametrize("boundary", ["START", "COMPLETE"])
@pytest.mark.parametrize("fail_point", ["write", "audit", "commit"])
def test_boundary_uow_failure_rolls_back_step_output_heartbeat_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    fail_point: str,
) -> None:
    """Write, durable-audit, and commit faults expose no partial boundary state."""
    state = _AtomicBoundaryState(RUN_ID, FIRST_WORKER_ID, 1)
    repository = _TransactionalRunStepsRepository(state)
    audit_sink = _TransactionalAuditSink(state)
    uow = _AtomicStepBoundaryUoW(
        state,
        repository,
        audit_sink,
        fail_boundary=(StepName.INGEST_CHECK, boundary),
        fail_point=fail_point,
    )
    after_commits: list[tuple[StepName, str]] = []
    context = _context(
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
        step_boundary_uow_fn=uow,
        after_step_boundary_commit_fn=lambda **kwargs: after_commits.append(
            (kwargs["step_name"], kwargs["boundary"])
        ),
        execute_with_lease_heartbeat_fn=lambda *, operation, **_kwargs: operation(),
    )
    context.mode = "SNAPSHOT"
    monkeypatch.setattr(orchestrator_module, "SNAPSHOT_STEPS", [StepName.INGEST_CHECK])

    caught: Exception | None = None
    try:
        RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repository).execute(context)
    except Exception as exc:
        caught = exc

    assert caught is not None
    assert getattr(caught, "code", None) == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    visible = _TransactionalRunStepsRepository(state).get_step(RUN_ID, StepName.INGEST_CHECK)
    if boundary == "START":
        assert visible is None
        assert state.heartbeat_count == 0
        assert after_commits == []
    else:
        assert visible is not None
        assert visible.status == StepStatus.RUNNING
        assert visible.result_summary == {}
        assert state.heartbeat_count == 1
        assert after_commits == [(StepName.INGEST_CHECK, "START")]
    assert StepName.INGEST_CHECK not in state.native_outputs
    assert not any(event.get("event_type") == "run.step.completed" for event in state.audits)
    committed_heartbeat_events = [
        event
        for event in state.audits
        if event.get("payload", {}).get("safe", {}).get("code") == "WORKER_LEASE_HEARTBEAT_RENEWED"
    ]
    assert len(committed_heartbeat_events) == (0 if boundary == "START" else 1)
    failure_events = [
        event
        for event in state.audits
        if event.get("payload", {}).get("safe", {}).get("code")
        == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    ]
    assert len(failure_events) == 1


def test_preclaimed_execution_does_not_mark_running_twice_or_write_failed_after_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preclaimed service propagates fencing without fallback terminal writes."""
    _require_run_context_lease_fields()
    terminal_writes: list[str] = []

    class RunsRepository:
        def try_mark_running(self, _run_id: str) -> bool:
            raise AssertionError("atomic lease claim must replace the old second mark-running step")

        def try_complete_running(self, _run_id: str, **_kwargs: Any) -> bool:
            terminal_writes.append("guarded")
            return True

        def complete(self, _run_id: str, **_kwargs: Any) -> None:
            terminal_writes.append("fallback")

        def get(self, _run_id: str) -> dict[str, Any]:
            return {
                "status": "QUEUED",
                "lease_owner_id": None,
                "lease_fencing_token": 2,
            }

    class WorkerLeaseFencedError(RuntimeError):
        code = "WORKER_LEASE_FENCED"

    class FencedOrchestrator:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, _ctx: RunContext) -> OrchestratorResult:
            raise WorkerLeaseFencedError("worker lease was fenced")

    monkeypatch.setattr(execution_module, "RunOrchestrator", FencedOrchestrator)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=RunsRepository(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(WorkerLeaseFencedError, match="worker lease was fenced"):
        service.execute(_context())

    assert terminal_writes == [], "a fenced zombie must not persist FAILED or terminal state"


def test_zombie_terminal_completion_carries_token_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale worker cannot complete the RUNNING row now owned by token 3."""
    _require_run_context_lease_fields()
    attempted_leases: list[tuple[str, int]] = []
    fallback_writes: list[str] = []

    class RunsRepository:
        def try_mark_running(self, _run_id: str) -> bool:
            raise AssertionError("preclaimed execution must not mark RUNNING a second time")

        def try_complete_running(
            self,
            _run_id: str,
            *,
            status: str,
            finished_at: str,
            lease_owner_id: str,
            lease_fencing_token: int,
        ) -> bool:
            assert status == "SUCCEEDED"
            assert finished_at
            attempted_leases.append((lease_owner_id, lease_fencing_token))
            return False

        def complete(self, _run_id: str, **_kwargs: Any) -> None:
            fallback_writes.append("unfenced-terminal-fallback")

        def get(self, _run_id: str) -> dict[str, Any]:
            return {
                "status": "RUNNING",
                "lease_owner_id": SECOND_WORKER_ID,
                "lease_fencing_token": 3,
            }

    class SuccessfulOrchestrator:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, _ctx: RunContext) -> OrchestratorResult:
            return OrchestratorResult(status="SUCCEEDED")

    monkeypatch.setattr(execution_module, "RunOrchestrator", SuccessfulOrchestrator)
    monkeypatch.setattr(execution_module, "_notify_run_webhook", lambda *_args, **_kwargs: None)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=RunsRepository(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    caught: Exception | None = None
    try:
        service.execute(
            _context(
                lease_owner_id=FIRST_WORKER_ID,
                lease_fencing_token=1,
            )
        )
    except Exception as exc:  # the implementation owns the concrete typed exception
        caught = exc

    assert caught is not None, "stale terminal completion did not fail closed"
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"
    assert attempted_leases == [(FIRST_WORKER_ID, 1)]
    assert fallback_writes == []


@pytest.mark.parametrize("terminal_status", ["SUCCEEDED", "FAILED"])
def test_terminal_transition_clears_owner_and_expiry_but_retains_fencing_history(
    terminal_status: str,
) -> None:
    repository = _seed_queued_run()
    claimed = _claim_one(repository, owner_id=FIRST_WORKER_ID)
    retained_heartbeat = claimed["last_heartbeat_at"]
    complete = repository.try_complete_running
    _require_parameters(
        complete,
        {"run_id", "status", "finished_at", "lease_owner_id", "lease_fencing_token"},
        contract="owner/token-fenced terminal transition",
    )

    assert complete(
        RUN_ID,
        status=terminal_status,
        finished_at="2026-07-15T00:00:02Z",
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
    )
    terminal = repository.get(RUN_ID)
    assert terminal is not None
    assert terminal["status"] == terminal_status
    assert terminal["lease_owner_id"] is None
    assert terminal["lease_expires_at"] is None
    assert terminal["lease_fencing_token"] == 1
    assert terminal["last_heartbeat_at"] == retained_heartbeat


def test_unfenced_terminal_surfaces_cannot_overwrite_a_live_lease() -> None:
    repository = _seed_queued_run()
    _claim_one(repository, owner_id=SECOND_WORKER_ID)
    before = dict(repository.get(RUN_ID) or {})

    assert (
        repository.try_complete_running(
            RUN_ID,
            status="FAILED",
            finished_at="2026-07-15T00:00:02Z",
        )
        is False
    )
    assert (
        repository.try_complete_active(
            RUN_ID,
            status="FAILED",
            finished_at="2026-07-15T00:00:02Z",
        )
        is False
    )
    with pytest.raises(runs_module.WorkerLeaseFencedError) as complete_error:
        repository.complete(
            RUN_ID,
            status="FAILED",
            finished_at="2026-07-15T00:00:02Z",
        )
    with pytest.raises(runs_module.WorkerLeaseFencedError) as update_error:
        repository.update_status(
            RUN_ID,
            status="FAILED",
            finished_at="2026-07-15T00:00:02Z",
        )

    assert complete_error.value.code == "WORKER_LEASE_FENCED"
    assert update_error.value.code == "WORKER_LEASE_FENCED"
    assert repository.get(RUN_ID) == before


def test_postgres_terminal_transition_is_owner_token_and_expiry_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(
        [_leased_row(status="SUCCEEDED", owner_id=None, fencing_token=1)]
    )
    repository = PostgresRunsRepository(connection, TENANT_ID)
    _require_parameters(
        repository.try_complete_running,
        {"run_id", "status", "finished_at", "lease_owner_id", "lease_fencing_token"},
        contract="Postgres fenced terminal transition",
    )

    assert repository.try_complete_running(
        RUN_ID,
        status="SUCCEEDED",
        finished_at="2026-07-15T00:00:02Z",
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
    )
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    _assert_update_assigns_value(
        sql,
        params,
        table="runs",
        column="status",
        value="SUCCEEDED",
    )
    assert "RUNNING" in normalized or "RUNNING" in {str(value).upper() for value in params.values()}
    assert re.search(r"(?:\w+\.)?TENANT_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?RUN_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?LEASE_OWNER_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?LEASE_FENCING_TOKEN\s*=\s*:\w+", normalized)
    assert re.search(
        r"LEASE_EXPIRES_AT\s*>\s*(?:STATEMENT_TIMESTAMP\(\)|CURRENT_TIMESTAMP|NOW\(\))",
        normalized,
    )
    set_clause = _update_set_clause(sql)
    assert re.search(r"LEASE_OWNER_ID\s*=\s*NULL", set_clause)
    assert re.search(r"LEASE_EXPIRES_AT\s*=\s*NULL", set_clause)
    assert "LEASE_FENCING_TOKEN" not in set_clause
    assert "LAST_HEARTBEAT_AT" not in set_clause


def test_bound_heartbeat_renewal_uses_a_fresh_rls_transaction_for_every_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = getattr(worker_module, "build_worker_lease_renewal", None)
    assert callable(builder), "canonical worker is missing its bound fresh-transaction renewal"
    _require_parameters(
        builder,
        {
            "connection_factory",
            "audit_sink",
            "tenant_id",
            "run_id",
            "lease_owner_id",
            "lease_fencing_token",
            "lease_seconds",
        },
        contract="bound worker heartbeat renewal builder",
    )
    durable = _BoundaryProbeDurableState()
    connections: list[_BoundaryProbeConnection] = []

    def connection_factory() -> _BoundaryProbeTransaction:
        connection = _BoundaryProbeConnection(len(connections) + 1, durable)
        connections.append(connection)
        return _BoundaryProbeTransaction(connection)

    def establish_rls(connection: _BoundaryProbeConnection, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        connection.record("rls")

    monkeypatch.setattr(worker_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)

    class AuditSink:
        def emit_in_tx(self, conn: _BoundaryProbeConnection, event: dict[str, Any]) -> None:
            assert event["payload"]["safe"]["code"] == "WORKER_LEASE_HEARTBEAT_RENEWED"
            conn.record("heartbeat-audit")

    renew = builder(
        connection_factory=connection_factory,
        audit_sink=AuditSink(),
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        lease_owner_id=SECOND_WORKER_ID,
        lease_fencing_token=3,
        lease_seconds=LEASE_SECONDS,
    )
    assert callable(renew)
    assert renew() is True
    assert renew() is True

    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert all(connection.closed for connection in connections)
    assert all(connection.commits == 1 and connection.rollbacks == 0 for connection in connections)
    for connection in connections:
        values = {value for params in connection.sql_params for value in params.values()}
        assert TENANT_ID in values
        assert RUN_ID in values
        assert SECOND_WORKER_ID in values
        assert 3 in values
        assert LEASE_SECONDS in values
        events = [
            event
            for event, connection_id in durable.events
            if connection_id == connection.connection_id
        ]
        assert events[0] == "rls"
        assert events[-1] == "heartbeat-audit"


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_audit_calls"),
    [
        ("cas_zero", "WORKER_LEASE_FENCED", 0),
        ("database", "WORKER_LEASE_HEARTBEAT_FAILED", 0),
        ("audit", "WORKER_LEASE_HEARTBEAT_FAILED", 1),
    ],
)
def test_real_bound_renewal_maps_loss_and_suppresses_inflight_output(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: str,
    expected_audit_calls: int,
) -> None:
    """Failure semantics must come from the real renewal builder, not a test callback."""
    builder = getattr(worker_module, "build_worker_lease_renewal", None)
    supervisor = getattr(worker_module, "execute_with_worker_lease_heartbeat", None)
    assert callable(builder), "canonical worker is missing its bound fresh-transaction renewal"
    assert callable(supervisor), "worker is missing its in-flight heartbeat supervisor"

    durable = _BoundaryProbeDurableState()
    connections: list[_BoundaryProbeConnection] = []
    operation_started = threading.Event()
    allow_finish = threading.Event()
    renewal_attempted = threading.Event()
    results: list[dict[str, str]] = []
    errors: list[BaseException] = []
    audit_calls: list[str] = []

    class RenewalConnection(_BoundaryProbeConnection):
        def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
            if "UPDATE" in _normalized_sql(str(statement)) and "RUNS" in _normalized_sql(
                str(statement)
            ):
                renewal_attempted.set()
            return super().execute(statement, params)

    def connection_factory() -> _BoundaryProbeTransaction:
        connection = RenewalConnection(
            len(connections) + 1,
            durable,
            lease_update_rowcount=0 if failure == "cas_zero" else 1,
            fail_lease_update=failure == "database",
        )
        connections.append(connection)
        return _BoundaryProbeTransaction(connection)

    def establish_rls(connection: _BoundaryProbeConnection, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        connection.record("rls")

    monkeypatch.setattr(worker_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)

    class AuditSink:
        def emit_in_tx(self, conn: _BoundaryProbeConnection, event: dict[str, Any]) -> None:
            assert event["payload"]["safe"]["code"] == "WORKER_LEASE_HEARTBEAT_RENEWED"
            audit_calls.append("heartbeat-audit")
            conn.record("heartbeat-audit")
            if failure == "audit":
                raise AuditSinkError("injected durable heartbeat audit failure")

    renew = builder(
        connection_factory=connection_factory,
        audit_sink=AuditSink(),
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        lease_owner_id=SECOND_WORKER_ID,
        lease_fencing_token=3,
        lease_seconds=LEASE_SECONDS,
    )

    def operation() -> dict[str, str]:
        operation_started.set()
        assert allow_finish.wait(timeout=2)
        return {"output": "must-not-reach-the-completion-boundary"}

    def run() -> None:
        try:
            results.append(
                supervisor(
                    operation=operation,
                    renew=renew,
                    heartbeat_seconds=0.01,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert operation_started.wait(timeout=2)
    assert renewal_attempted.wait(timeout=2)
    allow_finish.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert results == []
    assert len(errors) == 1
    assert getattr(errors[0], "code", None) == expected_code
    assert audit_calls == ["heartbeat-audit"] * expected_audit_calls
    assert len(connections) == 1
    assert connections[0].commits == 0
    assert connections[0].rollbacks == 1
    assert connections[0].closed is True
    assert durable.events == []


def test_long_step_renews_periodically_until_output_is_returned() -> None:
    supervisor = getattr(worker_module, "execute_with_worker_lease_heartbeat", None)
    assert callable(supervisor), "worker is missing its in-flight heartbeat supervisor"
    _require_parameters(
        supervisor,
        {"operation", "renew", "heartbeat_seconds"},
        contract="in-flight heartbeat supervisor",
    )
    operation_started = threading.Event()
    allow_finish = threading.Event()
    two_heartbeats = threading.Event()
    renewals: list[object] = []
    result: list[dict[str, str]] = []
    errors: list[BaseException] = []

    def operation() -> dict[str, str]:
        operation_started.set()
        assert allow_finish.wait(timeout=2)
        return {"output": "persist-only-after-supervisor-success"}

    def renew() -> bool:
        assert operation_started.is_set()
        renewals.append(object())  # each call represents a fresh short transaction
        if len(renewals) >= 2:
            two_heartbeats.set()
        return True

    def run() -> None:
        try:
            result.append(supervisor(operation=operation, renew=renew, heartbeat_seconds=0.01))
        except BaseException as exc:  # pragma: no cover - assertion reports it below
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert operation_started.wait(timeout=2)
    assert two_heartbeats.wait(timeout=2), "long step did not renew at the configured cadence"
    allow_finish.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == []
    assert len(renewals) >= 2
    assert result == [{"output": "persist-only-after-supervisor-success"}]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [("cas_zero", "WORKER_LEASE_FENCED"), ("backend", "WORKER_LEASE_HEARTBEAT_FAILED")],
)
def test_inflight_heartbeat_loss_suppresses_completed_operation_output(
    failure: str,
    expected_code: str,
) -> None:
    supervisor = getattr(worker_module, "execute_with_worker_lease_heartbeat", None)
    assert callable(supervisor), "worker is missing its in-flight heartbeat supervisor"
    operation_started = threading.Event()
    allow_finish = threading.Event()
    renewal_attempted = threading.Event()
    result: list[dict[str, str]] = []
    errors: list[BaseException] = []

    def operation() -> dict[str, str]:
        operation_started.set()
        assert allow_finish.wait(timeout=2)
        return {"output": "zombie-result-must-be-discarded"}

    def renew() -> bool:
        assert operation_started.is_set()
        renewal_attempted.set()
        if failure == "backend":
            raise OSError("heartbeat database unavailable")
        return False

    def run() -> None:
        try:
            result.append(supervisor(operation=operation, renew=renew, heartbeat_seconds=0.01))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert operation_started.wait(timeout=2)
    assert renewal_attempted.wait(timeout=2)
    allow_finish.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == []
    assert len(errors) == 1
    assert getattr(errors[0], "code", None) == expected_code


def test_heartbeat_loss_waits_for_foreground_provider_cleanup_before_raising() -> None:
    supervisor = worker_module.execute_with_worker_lease_heartbeat
    operation_started = threading.Event()
    renewal_failed = threading.Event()
    allow_provider_cleanup = threading.Event()
    provider_cleaned_up = threading.Event()
    supervisor_thread_ids: list[int] = []
    operation_thread_ids: list[int] = []
    heartbeat_thread_ids: list[int] = []
    results: list[object] = []
    errors: list[BaseException] = []

    def operation() -> object:
        operation_thread_ids.append(threading.get_ident())
        operation_started.set()
        try:
            assert allow_provider_cleanup.wait(timeout=2)
            return object()
        finally:
            provider_cleaned_up.set()

    def renew() -> bool:
        heartbeat_thread_ids.append(threading.get_ident())
        renewal_failed.set()
        return False

    def run_supervisor() -> None:
        supervisor_thread_ids.append(threading.get_ident())
        try:
            results.append(supervisor(operation=operation, renew=renew, heartbeat_seconds=0.01))
        except BaseException as exc:
            errors.append(exc)

    supervisor_thread = threading.Thread(target=run_supervisor)
    supervisor_thread.start()
    assert operation_started.wait(timeout=2)
    assert renewal_failed.wait(timeout=2)

    assert supervisor_thread.is_alive(), "lease loss returned while provider work was still alive"
    assert provider_cleaned_up.is_set() is False
    assert errors == []
    allow_provider_cleanup.set()
    supervisor_thread.join(timeout=2)

    assert supervisor_thread.is_alive() is False
    assert provider_cleaned_up.is_set()
    assert results == []
    assert len(errors) == 1
    assert getattr(errors[0], "code", None) == "WORKER_LEASE_FENCED"
    assert operation_thread_ids == supervisor_thread_ids
    assert heartbeat_thread_ids
    assert set(heartbeat_thread_ids).isdisjoint(supervisor_thread_ids)
