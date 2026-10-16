# ruff: noqa: F403, F405
"""Worker-loop, configuration, cancellation, and audit contracts."""

from __future__ import annotations

import os

# The shared harness deliberately exports private fixtures and probes.
from ._support import *  # noqa: F403


def test_default_run_context_factory_binds_claim_to_production_lease_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical context must compose the reviewed production builders, not no-ops."""
    polling_connection = object()
    captured: dict[str, Any] = {}
    builder_calls: dict[str, Any] = {}
    boundary_invocations: list[dict[str, Any]] = []
    renewal_invocations: list[str] = []
    heartbeat_invocations: list[dict[str, Any]] = []
    audit_sink = SimpleNamespace(name="durable-audit")
    real_stager_builder = getattr(steps_module, "build_postgres_native_output_stager", None)
    assert callable(real_stager_builder), (
        "canonical context is missing the real production native-output stager builder"
    )

    def production_boundary(**kwargs: Any) -> None:
        boundary_invocations.append(kwargs)

    def build_boundary(**kwargs: Any) -> Callable[..., None]:
        builder_calls["boundary"] = kwargs
        assert kwargs["audit_sink"] is audit_sink
        assert polling_connection not in kwargs.values()
        return production_boundary

    def renew_claim() -> bool:
        renewal_invocations.append("renew")
        return True

    def build_renewal(**kwargs: Any) -> Callable[[], bool]:
        builder_calls["renewal"] = kwargs
        assert kwargs["audit_sink"] is audit_sink
        assert kwargs["tenant_id"] == TENANT_ID
        assert kwargs["run_id"] == RUN_ID
        assert kwargs["lease_owner_id"] == SECOND_WORKER_ID
        assert kwargs["lease_fencing_token"] == 3
        assert kwargs["lease_seconds"] == LEASE_SECONDS
        assert polling_connection not in kwargs.values()
        return renew_claim

    def supervise(**kwargs: Any) -> Any:
        heartbeat_invocations.append(kwargs)
        assert kwargs["renew"] is renew_claim
        assert kwargs["heartbeat_seconds"] == 20
        assert polling_connection not in kwargs.values()
        return kwargs["operation"]()

    def build_stager(**kwargs: Any) -> Callable[..., Any]:
        builder_calls["stager"] = kwargs
        assert polling_connection not in kwargs.values()
        stager = real_stager_builder(**kwargs)
        assert callable(stager)
        assert getattr(stager, "__module__", type(stager).__module__).startswith("idis.")
        return stager

    def capture_build_run_context(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(worker_module, "_load_worker_preflight_corpus", lambda **_kwargs: [])
    monkeypatch.setattr(worker_module, "_load_worker_deal_metadata", lambda **_kwargs: None)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(
            lease_seconds=LEASE_SECONDS,
            heartbeat_seconds=20,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        execution_module,
        "build_postgres_step_boundary_uow",
        build_boundary,
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "build_worker_lease_renewal",
        build_renewal,
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "execute_with_worker_lease_heartbeat",
        supervise,
        raising=False,
    )
    monkeypatch.setattr(
        steps_module,
        "build_postgres_native_output_stager",
        build_stager,
        raising=False,
    )
    monkeypatch.setattr(steps_module, "build_run_context", capture_build_run_context)
    monkeypatch.setattr(
        steps_module,
        "extraction_ready_documents_from_preflight_corpus",
        lambda _corpus: [],
    )
    run_data = {
        "run_id": RUN_ID,
        "deal_id": DEAL_ID,
        "mode": "FULL",
        "source": {"type": "deal_documents", "document_ids": []},
        "lease_owner_id": SECOND_WORKER_ID,
        "lease_fencing_token": 3,
    }

    worker_module._default_run_context_factory(
        db_conn=polling_connection,
        tenant_id=TENANT_ID,
        run_data=run_data,
        audit_sink=audit_sink,
    )

    assert captured["db_conn"] is polling_connection
    assert captured["lease_owner_id"] == SECOND_WORKER_ID
    assert captured["lease_fencing_token"] == 3
    assert captured["step_boundary_uow_fn"] is production_boundary
    captured["step_boundary_uow_fn"](
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        boundary="START",
        lease_owner_id=SECOND_WORKER_ID,
        lease_fencing_token=3,
        mutation=lambda **_kwargs: None,
        native_output_writes=(),
    )
    assert len(boundary_invocations) == 1

    assert (
        captured["execute_with_lease_heartbeat_fn"](
            step_name=StepName.INGEST_CHECK,
            operation=lambda: "operation-result",
        )
        == "operation-result"
    )
    assert len(heartbeat_invocations) == 1

    production_stager = captured["stage_native_output_writes_fn"]
    assert callable(production_stager)
    assert isinstance(production_stager.contract_registry, dict)
    # Task 1 may compose only pre-Task7 concrete writers; missing families remain gated.
    assert set(production_stager.contract_registry) == {
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        StepName.LAYER2_IC_CHALLENGE,
    }
    assert set(production_stager.contract_registry) < set(STRICT_V2_STEPS)
    assert set(builder_calls) == {"boundary", "renewal", "stager"}
    assert callable(captured["after_step_boundary_commit_fn"])


def test_lease_contract_preserves_cancellation_and_one_active_run_index() -> None:
    """Cancellation wins over a live lease, clears ownership, and retains its token."""
    repository = _seed_queued_run()
    claimed = _claim_one(repository, owner_id=FIRST_WORKER_ID)
    assert claimed["status"] == "RUNNING"
    assert claimed["lease_fencing_token"] == 1
    retained_heartbeat = claimed["last_heartbeat_at"]
    assert repository.has_active_run(DEAL_ID) is True
    conflicting_run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with pytest.raises(RunAlreadyActiveError):
        repository.create(
            run_id=conflicting_run_id,
            deal_id=DEAL_ID,
            mode="FULL",
            source={"type": "deal_documents", "document_ids": []},
            created_by_actor_id="originating-human",
            created_by_actor_type="HUMAN",
        )
    assert conflicting_run_id not in runs_module._in_memory_runs_store
    assert repository.try_cancel_active(RUN_ID) is True
    cancelled = repository.get(RUN_ID)
    assert cancelled is not None
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancel_requested_at"] is not None
    assert cancelled["lease_owner_id"] is None
    assert cancelled["lease_expires_at"] is None
    assert cancelled["lease_fencing_token"] == 1
    assert cancelled["last_heartbeat_at"] == retained_heartbeat
    assert repository.has_active_run(DEAL_ID) is False

    reclaim = getattr(repository, "requeue_expired_worker_leases", None)
    assert callable(reclaim)
    assert reclaim(limit=1) == []
    renew = getattr(repository, "renew_worker_lease", None)
    assert callable(renew)
    assert (
        renew(
            run_id=RUN_ID,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            lease_seconds=LEASE_SECONDS,
        )
        is False
    )

    step_repository = InMemoryRunStepsRepository(TENANT_ID)
    cancelled_step = RunStep(
        step_id="88888888-8888-4888-8888-888888888888",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=StepName.INGEST_CHECK,
        step_order=STEP_ORDER[StepName.INGEST_CHECK],
        status=StepStatus.RUNNING,
    )
    _assert_worker_lease_fenced(
        lambda: step_repository.create(
            cancelled_step,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
        )
    )

    replacement_run_id = "99999999-9999-4999-8999-999999999999"
    repository.create(
        run_id=replacement_run_id,
        deal_id=DEAL_ID,
        mode="FULL",
        source={"type": "deal_documents", "document_ids": []},
        created_by_actor_id="originating-human",
        created_by_actor_type="HUMAN",
    )
    assert repository.has_active_run(DEAL_ID) is True
    visible_statuses = {
        run_id: row["status"]
        for run_id, row in runs_module._in_memory_runs_store.items()
        if row["tenant_id"] == TENANT_ID and row["deal_id"] == DEAL_ID
    }
    assert visible_statuses == {RUN_ID: "CANCELLED", replacement_run_id: "QUEUED"}


def test_postgres_cancellation_clears_owner_and_expiry_without_resetting_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection([SimpleNamespace(run_id=RUN_ID)])
    repository = PostgresRunsRepository(connection, TENANT_ID)

    assert repository.try_cancel_active(RUN_ID) is True

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    assert len(re.findall(r"\bUPDATE\s+RUNS\b", normalized)) == 1
    assert re.search(r"(?:\w+\.)?TENANT_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?RUN_ID\s*=\s*:\w+", normalized)
    values = {str(value).upper() for value in params.values()}
    assert "QUEUED" in normalized or "QUEUED" in values
    assert "RUNNING" in normalized or "RUNNING" in values
    _assert_update_assigns_value(
        sql,
        params,
        table="runs",
        column="status",
        value="CANCELLED",
    )
    assert re.search(r"LEASE_OWNER_ID\s*=\s*NULL", normalized)
    assert re.search(r"LEASE_EXPIRES_AT\s*=\s*NULL", normalized)
    database_time = r"(?:STATEMENT_TIMESTAMP\(\)|CURRENT_TIMESTAMP|NOW\(\))"
    assert re.search(rf"CANCEL_REQUESTED_AT\s*=\s*{database_time}", normalized)
    assert re.search(rf"FINISHED_AT\s*=\s*{database_time}", normalized)
    set_clause = normalized.split("SET", maxsplit=1)[1].split("WHERE", maxsplit=1)[0]
    assert "LEASE_FENCING_TOKEN" not in set_clause
    assert "LAST_HEARTBEAT_AT" not in set_clause
    assert "RETURNING" in normalized
    assert TENANT_ID in params.values()
    assert RUN_ID in params.values()


def test_canonical_worker_invokes_atomic_claim_with_limit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reclaim and claim commit with audits before exact preclaimed execution."""
    events: list[str] = []
    durable: dict[str, Any] = {
        "row": {
            "run_id": RUN_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "status": "RUNNING",
            "source": {"type": "deal_documents", "document_ids": []},
            "lease_owner_id": FIRST_WORKER_ID,
            "lease_fencing_token": 1,
            "lease_expires_at": "2000-01-01T00:00:00Z",
            "last_heartbeat_at": "2000-01-01T00:00:00Z",
        },
        "audits": [],
    }

    class Connection:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0
            self.working = copy.deepcopy(durable)

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def commit(self) -> None:
            durable.clear()
            durable.update(copy.deepcopy(self.working))
            self.commits += 1
            events.append("commit")
            self.working = copy.deepcopy(durable)

        def rollback(self) -> None:
            self.rollbacks += 1
            events.append("rollback")
            self.working = copy.deepcopy(durable)

    class Engine:
        def __init__(self) -> None:
            self.connection = Connection()

        def connect(self) -> Connection:
            return self.connection

    class RunsRepository:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            row = self.connection.working["row"]
            assert row["status"] == "RUNNING"
            assert row["lease_owner_id"] == FIRST_WORKER_ID
            assert row["lease_fencing_token"] == 1
            row.update(
                status="QUEUED",
                lease_owner_id=None,
                lease_fencing_token=2,
                lease_expires_at=None,
            )
            events.append("reclaim")
            for code in ("WORKER_LEASE_EXPIRED", "WORKER_LEASE_RECLAIMED"):
                lease_audit_emitter(
                    db_conn=self.connection,
                    code=code,
                    run_id=RUN_ID,
                    deal_id=DEAL_ID,
                    prior_lease_owner_id=FIRST_WORKER_ID,
                    prior_fencing_token=1,
                    new_fencing_token=2,
                )
            return [copy.deepcopy(row)]

        def claim_queued_runs(
            self,
            *,
            limit: int,
            lease_owner_id: str,
            lease_seconds: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert lease_owner_id == SECOND_WORKER_ID
            assert lease_seconds == LEASE_SECONDS
            row = self.connection.working["row"]
            assert row["status"] == "QUEUED"
            assert row["lease_fencing_token"] == 2
            row.update(
                status="RUNNING",
                lease_owner_id=lease_owner_id,
                lease_fencing_token=3,
                lease_expires_at="2026-07-15T12:01:00Z",
                last_heartbeat_at="2026-07-15T12:00:00Z",
            )
            events.append("claim")
            lease_audit_emitter(
                db_conn=self.connection,
                code="WORKER_LEASE_CLAIMED",
                run_id=RUN_ID,
                deal_id=DEAL_ID,
                lease_owner_id=lease_owner_id,
                new_fencing_token=3,
                lease_expires_at=row["lease_expires_at"],
            )
            return [copy.deepcopy(row)]

        def count_queued_runs(self) -> int:
            return 0

    class AuditSink:
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            assert conn is engine.connection
            code = event["payload"]["safe"]["code"]
            conn.working["audits"].append(code)
            events.append(f"audit:{code}")

        def emit(self, _event: dict[str, Any]) -> None:
            return None

    audit_sink = AuditSink()

    class ExecutionService:
        def __init__(self) -> None:
            self.audit_sink = audit_sink

        def execute(self, _ctx: Any) -> None:
            raise AssertionError("a preclaimed lease must never call the legacy claim path")

        def execute_preclaimed(self, ctx: Any) -> SimpleNamespace:
            events.append("execute-preclaimed")
            assert ctx.lease_owner_id == SECOND_WORKER_ID
            assert ctx.lease_fencing_token == 3
            assert durable["row"]["status"] == "RUNNING"
            assert durable["row"]["lease_fencing_token"] == 3
            return SimpleNamespace(status="SUCCEEDED")

    def service_factory(*, db_conn: Any, tenant_id: str, **_kwargs: Any) -> ExecutionService:
        assert db_conn is engine.connection
        assert tenant_id == TENANT_ID
        events.append("build-service")
        return ExecutionService()

    def context_factory(
        *, db_conn: Any, tenant_id: str, run_data: dict[str, Any], **_kwargs: Any
    ) -> Any:
        assert db_conn is engine.connection
        assert tenant_id == TENANT_ID
        assert run_data["lease_owner_id"] == SECOND_WORKER_ID
        assert run_data["lease_fencing_token"] == 3
        events.append("build-context")
        return SimpleNamespace(**run_data)

    engine = Engine()
    monkeypatch.setattr(worker_module, "get_app_engine", lambda: engine)
    monkeypatch.setattr(
        worker_module,
        "set_tenant_local",
        lambda conn, tenant_id: (
            conn is engine.connection and tenant_id == TENANT_ID and events.append("rls")
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "get_runs_repository",
        lambda conn, _tenant_id: RunsRepository(conn),
    )
    monkeypatch.setattr(worker_module, "_default_worker_audit_sink", lambda: audit_sink)
    monkeypatch.setattr(worker_module, "_WORKER_INSTANCE_ID", SECOND_WORKER_ID)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(lease_seconds=60, heartbeat_seconds=20),
        raising=False,
    )

    worker = worker_module.PipelineWorker(
        tenant_ids=[TENANT_ID],
        poll_interval=0,
        execution_service_factory=service_factory,
        run_context_factory=context_factory,
    )
    assert asyncio.run(worker._process_queued_runs()) == 1
    assert durable["row"]["status"] == "RUNNING"
    assert durable["row"]["lease_owner_id"] == SECOND_WORKER_ID
    assert durable["row"]["lease_fencing_token"] == 3
    assert durable["audits"] == [
        "WORKER_LEASE_EXPIRED",
        "WORKER_LEASE_RECLAIMED",
        "WORKER_LEASE_CLAIMED",
    ]
    reclaim_index = events.index("reclaim")
    reclaim_commit_index = events.index("commit", reclaim_index)
    claim_index = events.index("claim", reclaim_commit_index)
    claim_commit_index = events.index("commit", claim_index)
    context_index = events.index("build-context")
    execute_index = events.index("execute-preclaimed")
    assert reclaim_index < reclaim_commit_index < claim_index < claim_commit_index
    assert claim_commit_index < context_index < execute_index
    assert events[reclaim_index:reclaim_commit_index] == [
        "reclaim",
        "audit:WORKER_LEASE_EXPIRED",
        "audit:WORKER_LEASE_RECLAIMED",
    ]
    assert events[claim_index:claim_commit_index] == [
        "claim",
        "audit:WORKER_LEASE_CLAIMED",
    ]
    assert engine.connection.rollbacks == 0


def test_default_worker_releases_polling_connection_before_claimed_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production composition root must be the canonical worker's default execution path."""
    durable: dict[str, Any] = {
        "row": {
            "run_id": RUN_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "status": "QUEUED",
            "source": {"type": "deal_documents", "document_ids": []},
            "lease_owner_id": None,
            "lease_fencing_token": 0,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
        },
        "audits": [],
    }
    events: list[str] = []
    polling_connections: list[Any] = []
    lease_config = SimpleNamespace(lease_seconds=LEASE_SECONDS, heartbeat_seconds=20)

    class PollingConnection:
        def __init__(self, connection_id: int) -> None:
            self.connection_id = connection_id
            self.working = copy.deepcopy(durable)
            self.closed = False
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self) -> PollingConnection:
            events.append(f"poll-open:{self.connection_id}")
            return self

        def __exit__(self, *_args: Any) -> None:
            self.closed = True
            events.append(f"poll-close:{self.connection_id}")

        def commit(self) -> None:
            durable.clear()
            durable.update(copy.deepcopy(self.working))
            self.working = copy.deepcopy(durable)
            self.commits += 1
            events.append(f"poll-commit:{self.connection_id}")

        def rollback(self) -> None:
            self.working = copy.deepcopy(durable)
            self.rollbacks += 1
            events.append(f"poll-rollback:{self.connection_id}")

    class Engine:
        def connect(self) -> PollingConnection:
            connection = PollingConnection(len(polling_connections) + 1)
            polling_connections.append(connection)
            return connection

    class RunsRepository:
        def __init__(self, connection: PollingConnection) -> None:
            self.connection = connection

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert callable(lease_audit_emitter)
            return []

        def claim_queued_runs(
            self,
            *,
            limit: int,
            lease_owner_id: str | None = None,
            lease_seconds: int | None = None,
            lease_audit_emitter: Callable[..., None] | None = None,
        ) -> list[dict[str, Any]]:
            if lease_owner_id is None:
                # Permit the legacy call to continue far enough for this test to
                # reject its polling-connection execution path explicitly. The
                # separate atomic-claim tests reject this limit/signature.
                assert limit == 10
                lease_owner_id = SECOND_WORKER_ID
                lease_seconds = LEASE_SECONDS
            else:
                assert limit == 1
                assert lease_owner_id == SECOND_WORKER_ID
                assert lease_seconds == LEASE_SECONDS
                assert callable(lease_audit_emitter)
            row = self.connection.working["row"]
            assert row["status"] == "QUEUED"
            row.update(
                status="RUNNING",
                lease_owner_id=lease_owner_id,
                lease_fencing_token=1,
                lease_expires_at="2026-07-15T12:01:00Z",
                last_heartbeat_at="2026-07-15T12:00:00Z",
            )
            if lease_audit_emitter is not None:
                lease_audit_emitter(
                    db_conn=self.connection,
                    code="WORKER_LEASE_CLAIMED",
                    run_id=RUN_ID,
                    deal_id=DEAL_ID,
                    lease_owner_id=lease_owner_id,
                    new_fencing_token=1,
                    lease_expires_at=row["lease_expires_at"],
                )
            events.append("claim")
            return [copy.deepcopy(row)]

        def count_queued_runs(self) -> int:
            return 0

    class AuditSink:
        def emit_in_tx(self, conn: PollingConnection, event: dict[str, Any]) -> None:
            code = event["payload"]["safe"]["code"]
            conn.working["audits"].append(code)
            events.append(f"audit:{code}")

        def emit(self, _event: dict[str, Any]) -> None:
            return None

    audit_sink = AuditSink()
    runtime_builds: list[dict[str, Any]] = []
    runtime_executions: list[str] = []

    class ClaimedRuntime:
        def execute_preclaimed(self) -> SimpleNamespace:
            assert runtime_builds
            assert all(connection.closed for connection in polling_connections)
            runtime_executions.append("execute-preclaimed")
            return SimpleNamespace(status="SUCCEEDED")

    def build_claimed_runtime(**kwargs: Any) -> ClaimedRuntime:
        assert polling_connections
        assert all(connection.closed for connection in polling_connections), (
            "the claim transaction connection must be released before runtime composition"
        )
        assert durable["row"]["status"] == "RUNNING"
        assert durable["row"]["lease_owner_id"] == SECOND_WORKER_ID
        assert durable["row"]["lease_fencing_token"] == 1
        assert durable["audits"] == ["WORKER_LEASE_CLAIMED"]
        assert kwargs["tenant_id"] == TENANT_ID
        assert kwargs["run_data"] == durable["row"]
        assert kwargs["lease_config"] is lease_config
        assert kwargs["audit_sink"] is audit_sink
        assert "db_conn" not in kwargs
        assert not any(value in polling_connections for value in kwargs.values())
        runtime_builds.append(kwargs)
        events.append("build-claimed-runtime")
        return ClaimedRuntime()

    engine = Engine()
    monkeypatch.setattr(worker_module, "get_app_engine", lambda: engine)
    monkeypatch.setattr(
        worker_module,
        "set_tenant_local",
        lambda conn, tenant_id: (
            tenant_id == TENANT_ID and events.append(f"rls:{conn.connection_id}")
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "get_runs_repository",
        lambda conn, tenant_id: (
            RunsRepository(conn)
            if tenant_id == TENANT_ID
            else (_ for _ in ()).throw(AssertionError("wrong tenant repository"))
        ),
    )
    monkeypatch.setattr(worker_module, "_default_worker_audit_sink", lambda: audit_sink)
    monkeypatch.setattr(worker_module, "_WORKER_INSTANCE_ID", SECOND_WORKER_ID)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module, "load_worker_lease_config", lambda **_kwargs: lease_config, raising=False
    )
    monkeypatch.setattr(
        worker_module,
        "build_postgres_claimed_run_runtime",
        build_claimed_runtime,
        raising=False,
    )
    monkeypatch.setattr(worker_module, "emit_run_signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_module,
        "_default_execution_service_factory",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy polling-connection execution service was invoked")
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "_default_run_context_factory",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy polling-connection run context was invoked")
        ),
    )

    worker = worker_module.PipelineWorker(tenant_ids=[TENANT_ID], poll_interval=0)
    fallback_failures: list[str] = []
    monkeypatch.setattr(
        worker,
        "_mark_run_failed_after_exception",
        lambda *_args, **_kwargs: fallback_failures.append("legacy-failed-write"),
    )

    assert asyncio.run(worker._process_queued_runs()) == 1
    assert len(runtime_builds) == 1
    assert runtime_executions == ["execute-preclaimed"]
    assert fallback_failures == []
    assert all(connection.closed for connection in polling_connections)
    claim_index = events.index("claim")
    claim_commit_index = next(
        index
        for index, event in enumerate(events)
        if index > claim_index and event.startswith("poll-commit:")
    )
    close_index = next(
        index
        for index, event in enumerate(events)
        if index > claim_commit_index and event.startswith("poll-close:")
    )
    build_index = events.index("build-claimed-runtime")
    assert claim_index < claim_commit_index < close_index < build_index


def test_worker_crash_immediately_after_claim_commit_leaves_reclaimable_durable_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durable: dict[str, Any] = {
        "row": {
            "run_id": RUN_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "source": {"type": "deal_documents", "document_ids": []},
            "status": "QUEUED",
            "lease_owner_id": None,
            "lease_fencing_token": 0,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
        },
        "audits": [],
    }

    class Connection:
        def __init__(self) -> None:
            self.working = copy.deepcopy(durable)
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def commit(self) -> None:
            durable.clear()
            durable.update(copy.deepcopy(self.working))
            self.working = copy.deepcopy(durable)
            self.commits += 1

        def rollback(self) -> None:
            self.working = copy.deepcopy(durable)
            self.rollbacks += 1

    connection = Connection()

    class RunsRepository:
        def requeue_expired_worker_leases(self, **_kwargs: Any) -> list[dict[str, Any]]:
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
            assert lease_seconds == LEASE_SECONDS
            row = connection.working["row"]
            row.update(
                status="RUNNING",
                lease_owner_id=lease_owner_id,
                lease_fencing_token=1,
                lease_expires_at="2026-07-15T12:01:00Z",
                last_heartbeat_at="2026-07-15T12:00:00Z",
            )
            lease_audit_emitter(
                db_conn=connection,
                code="WORKER_LEASE_CLAIMED",
                run_id=RUN_ID,
                deal_id=DEAL_ID,
                lease_owner_id=lease_owner_id,
                new_fencing_token=1,
                lease_expires_at=row["lease_expires_at"],
            )
            return [copy.deepcopy(row)]

        def count_queued_runs(self) -> int:
            return 0

    class AuditSink:
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            conn.working["audits"].append(event["payload"]["safe"]["code"])

        def emit(self, _event: dict[str, Any]) -> None:
            return None

    audit_sink = AuditSink()
    service = SimpleNamespace(audit_sink=audit_sink)
    monkeypatch.setattr(
        worker_module, "get_app_engine", lambda: SimpleNamespace(connect=lambda: connection)
    )
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: RunsRepository())
    monkeypatch.setattr(worker_module, "_default_worker_audit_sink", lambda: audit_sink)
    monkeypatch.setattr(worker_module, "_WORKER_INSTANCE_ID", SECOND_WORKER_ID)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(
            lease_seconds=LEASE_SECONDS,
            heartbeat_seconds=20,
        ),
        raising=False,
    )

    def crash_context_factory(**kwargs: Any) -> Any:
        run_data = kwargs["run_data"]
        assert connection.commits >= 1
        assert durable["row"]["status"] == "RUNNING"
        assert durable["row"]["lease_owner_id"] == SECOND_WORKER_ID
        assert durable["row"]["lease_fencing_token"] == 1
        assert run_data["lease_fencing_token"] == 1
        raise _SimulatedWorkerCrash

    worker = worker_module.PipelineWorker(
        tenant_ids=[TENANT_ID],
        poll_interval=0,
        execution_service_factory=lambda **_kwargs: service,
        run_context_factory=crash_context_factory,
    )
    with pytest.raises(_SimulatedWorkerCrash):
        asyncio.run(worker._process_queued_runs())

    assert durable["row"]["status"] == "RUNNING"
    assert durable["row"]["lease_owner_id"] == SECOND_WORKER_ID
    assert durable["row"]["lease_fencing_token"] == 1
    assert durable["audits"] == ["WORKER_LEASE_CLAIMED"]
    assert connection.rollbacks == 0


def test_worker_instance_owner_uuid_is_stable_across_poll_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners: list[str] = []

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    class RunsRepository:
        def requeue_expired_worker_leases(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

        def claim_queued_runs(self, *, lease_owner_id: str, **_kwargs: Any) -> list[dict[str, Any]]:
            owners.append(lease_owner_id)
            return []

    connection = Connection()
    monkeypatch.setattr(
        worker_module, "get_app_engine", lambda: SimpleNamespace(connect=lambda: connection)
    )
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: RunsRepository())
    monkeypatch.setattr(
        worker_module,
        "_default_worker_audit_sink",
        lambda: SimpleNamespace(emit_in_tx=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(
            lease_seconds=LEASE_SECONDS,
            heartbeat_seconds=20,
        ),
        raising=False,
    )
    worker = worker_module.PipelineWorker(tenant_ids=[TENANT_ID], poll_interval=0)
    assert asyncio.run(worker._process_queued_runs()) == 0
    assert asyncio.run(worker._process_queued_runs()) == 0
    assert len(owners) == 2
    assert owners[0] == owners[1]
    assert str(uuid.UUID(owners[0])) == owners[0]


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [("reclaim", "WORKER_LEASE_RECLAIM_FAILED"), ("claim", "WORKER_LEASE_CLAIM_FAILED")],
)
def test_worker_surfaces_stable_reclaim_and_claim_failure_codes(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_code: str,
) -> None:
    class Connection:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    class Engine:
        def __init__(self) -> None:
            self.connection = Connection()

        def connect(self) -> Connection:
            return self.connection

    class RunsRepository:
        def requeue_expired_worker_leases(
            self,
            *,
            limit: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            assert callable(lease_audit_emitter)
            if operation == "reclaim":
                raise OSError("reclaim database failure")
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
            assert uuid.UUID(lease_owner_id)
            assert lease_seconds == 60
            assert callable(lease_audit_emitter)
            raise OSError("claim database failure")

    engine = Engine()
    monkeypatch.setattr(worker_module, "get_app_engine", lambda: engine)
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(worker_module, "get_runs_repository", lambda *_args: RunsRepository())
    monkeypatch.setattr(
        worker_module,
        "_default_worker_audit_sink",
        lambda: SimpleNamespace(emit_in_tx=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(lease_seconds=60, heartbeat_seconds=20),
        raising=False,
    )
    worker = worker_module.PipelineWorker(tenant_ids=[TENANT_ID], poll_interval=0)

    caught: Exception | None = None
    try:
        asyncio.run(worker._process_queued_runs())
    except Exception as exc:
        caught = exc
    assert caught is not None
    assert getattr(caught, "code", None) == expected_code
    assert engine.connection.rollbacks == 1
    assert engine.connection.commits in ({0} if operation == "reclaim" else {0, 1})


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [("reclaim", "WORKER_LEASE_RECLAIM_FAILED"), ("claim", "WORKER_LEASE_CLAIM_FAILED")],
)
def test_canonical_worker_rolls_back_when_transition_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_code: str,
) -> None:
    initial_row = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "mode": "FULL",
        "source": {"type": "deal_documents", "document_ids": []},
        "status": "RUNNING" if operation == "reclaim" else "QUEUED",
        "lease_owner_id": FIRST_WORKER_ID if operation == "reclaim" else None,
        "lease_fencing_token": 1 if operation == "reclaim" else 0,
        "lease_expires_at": "2000-01-01T00:00:00Z" if operation == "reclaim" else None,
        "last_heartbeat_at": "2000-01-01T00:00:00Z" if operation == "reclaim" else None,
    }
    durable = {"row": copy.deepcopy(initial_row), "audits": []}
    forbidden_calls: list[str] = []

    class Connection:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0
            self.working = copy.deepcopy(durable)

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def commit(self) -> None:
            durable.clear()
            durable.update(copy.deepcopy(self.working))
            self.commits += 1
            self.working = copy.deepcopy(durable)

        def rollback(self) -> None:
            self.rollbacks += 1
            self.working = copy.deepcopy(durable)

    class Engine:
        def __init__(self) -> None:
            self.connection = Connection()

        def connect(self) -> Connection:
            return self.connection

    class RunsRepository:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, Any]]:
            assert limit == 1
            if operation == "reclaim":
                self.connection.working["row"].update(
                    status="QUEUED",
                    lease_owner_id=None,
                    lease_fencing_token=2,
                    lease_expires_at=None,
                )
                lease_audit_emitter(
                    db_conn=self.connection,
                    code="WORKER_LEASE_EXPIRED",
                    run_id=RUN_ID,
                    deal_id=DEAL_ID,
                    prior_lease_owner_id=FIRST_WORKER_ID,
                    prior_fencing_token=1,
                    new_fencing_token=2,
                )
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
            assert lease_seconds == LEASE_SECONDS
            if operation == "claim":
                self.connection.working["row"].update(
                    status="RUNNING",
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=1,
                    lease_expires_at="2026-07-15T12:01:00Z",
                    last_heartbeat_at="2026-07-15T12:00:00Z",
                )
                lease_audit_emitter(
                    db_conn=self.connection,
                    code="WORKER_LEASE_CLAIMED",
                    run_id=RUN_ID,
                    deal_id=DEAL_ID,
                    lease_owner_id=lease_owner_id,
                    new_fencing_token=1,
                    lease_expires_at="2026-07-15T12:01:00Z",
                )
            return []

        def count_queued_runs(self) -> int:
            return 0

    engine = Engine()
    monkeypatch.setattr(worker_module, "get_app_engine", lambda: engine)
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(
        worker_module,
        "get_runs_repository",
        lambda conn, _tenant_id: RunsRepository(conn),
    )
    monkeypatch.setattr(worker_module, "_WORKER_INSTANCE_ID", SECOND_WORKER_ID)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(
            lease_seconds=LEASE_SECONDS,
            heartbeat_seconds=20,
        ),
        raising=False,
    )

    class FailingAuditSink:
        def emit_in_tx(self, conn: Any, _event: dict[str, Any]) -> None:
            assert conn is engine.connection
            raise AuditSinkError("injected canonical transition audit failure")

    monkeypatch.setattr(worker_module, "_default_worker_audit_sink", FailingAuditSink)
    worker = worker_module.PipelineWorker(
        tenant_ids=[TENANT_ID],
        poll_interval=0,
        execution_service_factory=lambda **_kwargs: forbidden_calls.append("service"),
        run_context_factory=lambda **_kwargs: forbidden_calls.append("context"),
    )
    monkeypatch.setattr(
        worker,
        "_mark_run_failed_after_exception",
        lambda *_args, **_kwargs: forbidden_calls.append("fallback-failed"),
    )

    caught: Exception | None = None
    try:
        asyncio.run(worker._process_queued_runs())
    except Exception as exc:
        caught = exc
    assert caught is not None
    assert getattr(caught, "code", None) == expected_code
    assert engine.connection.rollbacks == 1
    assert engine.connection.commits in ({0} if operation == "reclaim" else {0, 1})
    assert durable == {"row": initial_row, "audits": []}
    assert forbidden_calls == []


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"IDIS_WORKER_LEASE_SECONDS": "60"},
        {"IDIS_WORKER_HEARTBEAT_SECONDS": "20"},
        {"IDIS_WORKER_LEASE_SECONDS": "", "IDIS_WORKER_HEARTBEAT_SECONDS": "20"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": ""},
        {"IDIS_WORKER_LEASE_SECONDS": "abc", "IDIS_WORKER_HEARTBEAT_SECONDS": "20"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": "abc"},
        {"IDIS_WORKER_LEASE_SECONDS": "0", "IDIS_WORKER_HEARTBEAT_SECONDS": "20"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": "0"},
        {"IDIS_WORKER_LEASE_SECONDS": "-1", "IDIS_WORKER_HEARTBEAT_SECONDS": "20"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": "-1"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": "60"},
        {"IDIS_WORKER_LEASE_SECONDS": "60", "IDIS_WORKER_HEARTBEAT_SECONDS": "61"},
    ],
    ids=[
        "both-missing",
        "heartbeat-missing",
        "lease-missing",
        "lease-empty",
        "heartbeat-empty",
        "lease-non-integer",
        "heartbeat-non-integer",
        "lease-zero",
        "heartbeat-zero",
        "lease-negative",
        "heartbeat-negative",
        "heartbeat-equals-lease",
        "heartbeat-exceeds-lease",
    ],
)
def test_strict_worker_lease_config_rejects_invalid_values(env: dict[str, str]) -> None:
    loader = getattr(worker_module, "load_worker_lease_config", None)
    assert callable(loader), "strict worker is missing a typed lease-config loader"
    _require_parameters(loader, {"env", "strict"}, contract="typed worker lease config")

    caught: Exception | None = None
    try:
        loader(env=env, strict=True)
    except Exception as exc:
        caught = exc
    assert caught is not None, f"invalid strict lease config unexpectedly passed: {env}"
    assert getattr(caught, "code", None) == "WORKER_LEASE_CONFIG_INVALID"


def test_strict_worker_lease_config_returns_typed_positive_interval() -> None:
    loader = getattr(worker_module, "load_worker_lease_config", None)
    assert callable(loader), "strict worker is missing a typed lease-config loader"
    config = loader(
        env={
            "IDIS_WORKER_LEASE_SECONDS": "60",
            "IDIS_WORKER_HEARTBEAT_SECONDS": "20",
        },
        strict=True,
    )
    assert type(config.lease_seconds) is int
    assert type(config.heartbeat_seconds) is int
    assert config.lease_seconds == 60
    assert config.heartbeat_seconds == 20
    assert config.heartbeat_seconds < config.lease_seconds


def test_worker_validates_lease_config_before_opening_queue_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strict worker cannot poll or claim once typed config validation fails."""
    events: list[str] = []
    config_calls: list[dict[str, Any]] = []

    class InvalidConfig(RuntimeError):
        code = "WORKER_LEASE_CONFIG_INVALID"

    def reject_config(*args: Any, strict: bool = False, **kwargs: Any) -> _LeaseConfigProbe:
        assert args == ()
        config_calls.append({"strict": strict, **kwargs})
        if strict is not True:
            events.append("non-strict-config-bypass")
            return _LeaseConfigProbe()
        events.append("config")
        raise InvalidConfig

    monkeypatch.setattr(worker_module, "load_worker_lease_config", reject_config, raising=False)
    monkeypatch.setattr(
        worker_module,
        "get_app_engine",
        lambda: events.append("database") or (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        worker_module, "require_strict_process_role", lambda *_args, **_kwargs: None
    )
    with pytest.raises(InvalidConfig):
        worker = worker_module.PipelineWorker(tenant_ids=[TENANT_ID], poll_interval=0)
        asyncio.run(worker.start())
    assert config_calls == [{"strict": True, "env": os.environ}]
    assert events == ["config"]


def test_start_worker_does_not_publish_global_when_lease_config_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_calls: list[dict[str, Any]] = []

    class InvalidConfig(RuntimeError):
        code = "WORKER_LEASE_CONFIG_INVALID"

    def reject_config(*args: Any, strict: bool = False, **kwargs: Any) -> _LeaseConfigProbe:
        assert args == ()
        config_calls.append({"strict": strict, **kwargs})
        if strict is not True:
            return _LeaseConfigProbe()
        raise InvalidConfig

    monkeypatch.setattr(worker_module, "_worker", None)
    monkeypatch.setattr(worker_module, "get_gdbs_path", lambda: None)
    monkeypatch.setattr(
        worker_module, "require_strict_process_role", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        reject_config,
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "get_app_engine",
        lambda: (_ for _ in ()).throw(AssertionError("database opened before config")),
    )

    with pytest.raises(InvalidConfig):
        asyncio.run(worker_module.start_worker())
    assert config_calls == [{"strict": True}]
    assert worker_module._worker is None


def test_process_all_queued_validates_lease_config_before_worker_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.process_queued_runs as process_module

    class InvalidConfig(RuntimeError):
        code = "WORKER_LEASE_CONFIG_INVALID"

    factory_calls: list[str] = []
    config_calls: list[dict[str, Any]] = []

    def reject_config(*args: Any, strict: bool = False, **kwargs: Any) -> _LeaseConfigProbe:
        assert args == ()
        config_calls.append({"strict": strict, **kwargs})
        if strict is not True:
            return _LeaseConfigProbe()
        raise InvalidConfig

    monkeypatch.setattr(process_module, "require_strict_process_role", lambda *_a, **_k: None)
    monkeypatch.setattr(process_module, "get_worker_tenant_ids", lambda: [TENANT_ID])
    monkeypatch.setattr(
        process_module,
        "load_worker_lease_config",
        reject_config,
        raising=False,
    )

    with pytest.raises(InvalidConfig):
        asyncio.run(
            process_module.process_all_queued(
                worker_factory=lambda **_kwargs: factory_calls.append("factory"),
            )
        )
    assert config_calls == [{"strict": True, "env": os.environ}]
    assert factory_calls == []


@pytest.mark.parametrize("code", sorted(LEASE_AUDIT_CODES))
def test_worker_lease_audit_events_are_schema_valid_safe_and_bound(code: str) -> None:
    event = _build_lease_event(code)
    validation = validate_audit_event(event)
    assert validation.passed, [error.code for error in validation.errors]
    assert event["tenant_id"] == TENANT_ID
    assert event["event_type"] == LEASE_EVENT_TYPES[code]
    assert event["actor"] == {
        "actor_type": "SERVICE",
        "actor_id": "idis-pipeline-worker",
    }
    assert event["resource"]["deal_id"] == DEAL_ID
    assert event["request"]["request_id"] == RUN_ID

    safe = event["payload"]["safe"]
    common = {"code", "run_id"}
    expected_by_code = {
        "WORKER_LEASE_CLAIMED": common
        | {"lease_owner_id", "new_fencing_token", "lease_expires_at"},
        "WORKER_LEASE_HEARTBEAT_RENEWED": common
        | {"lease_owner_id", "current_fencing_token", "lease_expires_at"},
        "WORKER_LEASE_EXPIRED": common
        | {"prior_lease_owner_id", "prior_fencing_token", "new_fencing_token"},
        "WORKER_LEASE_RECLAIMED": common
        | {"prior_lease_owner_id", "prior_fencing_token", "new_fencing_token"},
    }
    assert set(safe) == expected_by_code[code]
    assert safe["code"] == code
    assert safe["run_id"] == RUN_ID
    serialized_safe = repr(safe).lower()
    assert not any(
        forbidden in serialized_safe
        for forbidden in ("secret", "password", "authorization", "api_key", "raw_payload")
    )


def test_boundary_failure_event_is_safe_postrollback_evidence() -> None:
    event = _build_lease_event("WORKER_LEASE_BOUNDARY_COMMIT_FAILED")
    validation = validate_audit_event(event)
    assert validation.passed, [error.code for error in validation.errors]
    assert event["event_type"] == LEASE_EVENT_TYPES["WORKER_LEASE_BOUNDARY_COMMIT_FAILED"]
    assert event["payload"]["safe"] == {
        "code": "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
        "run_id": RUN_ID,
        "step_name": StepName.INGEST_CHECK.value,
        "boundary": "COMPLETE",
    }


def test_worker_lease_audit_uses_same_transaction_connection_and_fails_closed() -> None:
    emitter = getattr(worker_module, "emit_worker_lease_audit_in_tx", None)
    assert callable(emitter), "lease audit lacks a durable in-transaction emitter"
    _require_parameters(
        emitter,
        {"db_conn", "audit_sink", "event"},
        contract="in-transaction worker lease audit",
    )
    event = _build_lease_event("WORKER_LEASE_CLAIMED")
    connection = object()
    observed: list[tuple[Any, dict[str, Any]]] = []

    class Sink:
        def emit_in_tx(self, conn: Any, emitted: dict[str, Any]) -> None:
            observed.append((conn, emitted))

    emitter(db_conn=connection, audit_sink=Sink(), event=event)
    assert observed == [(connection, event)]

    class FailingSink:
        def emit_in_tx(self, conn: Any, emitted: dict[str, Any]) -> None:
            assert conn is connection
            assert emitted == event
            raise AuditSinkError("injected durable audit failure")

    with pytest.raises(AuditSinkError, match="injected durable audit failure"):
        emitter(db_conn=connection, audit_sink=FailingSink(), event=event)
