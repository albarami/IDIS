# ruff: noqa: F403, F405
"""Worker-lease claim, reclaim, and migration contracts."""

from __future__ import annotations

# The shared harness deliberately exports private fixtures and probes.
from ._support import *  # noqa: F403


def test_claim_is_one_atomic_update_that_sets_all_durable_lease_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker must not SELECT candidates and later mark one RUNNING."""
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(
        [_leased_row(status="RUNNING", owner_id=FIRST_WORKER_ID, fencing_token=1)]
    )
    repository = PostgresRunsRepository(connection, TENANT_ID)
    audited: list[dict[str, Any]] = []
    _require_parameters(
        repository.claim_queued_runs,
        {"limit", "lease_owner_id", "lease_seconds", "lease_audit_emitter"},
        contract="atomic worker claim",
    )

    claimed = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=FIRST_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
        lease_audit_emitter=lambda **event: audited.append(event),
    )

    assert len(connection.calls) == 1, "claim state transition must be exactly one SQL statement"
    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    assert len(re.findall(r"\bUPDATE\s+RUNS\b", normalized)) == 1
    assert re.search(r"\bINSERT\b|\bDELETE\b", normalized) is None
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert re.search(r"LIMIT\s+:\w+", normalized)
    assert re.search(r"(?:\w+\.)?TENANT_ID\s*=\s*:\w+", normalized)
    _assert_update_assigns_value(
        sql,
        params,
        table="runs",
        column="status",
        value="RUNNING",
    )
    assert "QUEUED" in normalized or "QUEUED" in {str(value).upper() for value in params.values()}
    assert re.search(r"(?:\w+\.)?LEASE_OWNER_ID\s*=\s*:\w+", normalized)
    assert re.search(
        r"LEASE_FENCING_TOKEN\s*=\s*(?:\w+\.)?LEASE_FENCING_TOKEN\s*\+\s*1",
        normalized,
    )
    assert re.search(r"LAST_HEARTBEAT_AT\s*=\s*STATEMENT_TIMESTAMP\(\)", normalized)
    assert re.search(r"LEASE_EXPIRES_AT\s*=\s*STATEMENT_TIMESTAMP\(\)\s*\+", normalized)
    assert LEASE_SECONDS in params.values()
    assert "STARTED_AT" in normalized
    assert "COALESCE" in normalized or "CASE" in normalized
    assert "RETURNING" in normalized
    for returned in (
        "RUN_ID",
        "TENANT_ID",
        "STATUS",
        "LEASE_OWNER_ID",
        "LEASE_FENCING_TOKEN",
        "LEASE_EXPIRES_AT",
        "LAST_HEARTBEAT_AT",
    ):
        assert returned in normalized.split("RETURNING", maxsplit=1)[1]
    assert TENANT_ID in params.values()
    assert FIRST_WORKER_ID in params.values()
    assert LEASE_SECONDS in params.values()
    assert 1 in params.values(), "canonical worker must claim exactly one run"
    assert len(claimed) == 1
    assert claimed[0]["status"] == "RUNNING"
    assert claimed[0]["lease_owner_id"] == FIRST_WORKER_ID
    assert claimed[0]["lease_fencing_token"] == 1
    assert len(audited) == 1
    assert audited[0]["db_conn"] is connection
    assert audited[0]["code"] == "WORKER_LEASE_CLAIMED"
    assert audited[0]["lease_owner_id"] == FIRST_WORKER_ID
    assert audited[0]["new_fencing_token"] == 1
    assert audited[0]["lease_expires_at"] is not None


@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_legacy_try_mark_running_is_retired_with_stable_fencing_error(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    if backend == "memory":
        repository: Any = _seed_queued_run()
        before = dict(repository.get(RUN_ID) or {})
        connection = None
    else:
        monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
        connection = _CapturingConnection()
        repository = PostgresRunsRepository(connection, TENANT_ID)
        before = None

    with pytest.raises(runs_module.WorkerLeaseFencedError) as caught:
        repository.try_mark_running(RUN_ID)

    assert caught.value.code == "WORKER_LEASE_FENCED"
    if backend == "memory":
        assert repository.get(RUN_ID) == before
    else:
        assert connection is not None
        assert connection.calls == []


def test_one_migration_structurally_owns_the_complete_worker_lease_lifecycle() -> None:
    """Do not allow comments or fragments in unrelated revisions to satisfy the schema RED."""
    assert _is_coherent_running_lease_constraint(
        "status <> 'RUNNING' OR (lease_owner_id IS NOT NULL "
        "AND lease_fencing_token > 0 AND lease_expires_at IS NOT NULL "
        "AND last_heartbeat_at IS NOT NULL)"
    )
    assert not _is_coherent_running_lease_constraint(
        "status <> 'RUNNING' OR (lease_owner_id IS NOT NULL "
        "OR lease_fencing_token > 0 OR lease_expires_at IS NOT NULL "
        "OR last_heartbeat_at IS NOT NULL)"
    )
    assert not _is_coherent_running_lease_constraint(
        "status <> 'RUNNING' OR (lease_owner_id IS NOT NULL "
        "AND lease_fencing_token > 0 AND (lease_expires_at IS NOT NULL "
        "OR last_heartbeat_at IS NOT NULL))"
    )
    columns = {
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    path = _worker_lease_migration_path()
    downgrade_source = _reachable_migration_source(path, "downgrade")
    upgrade_calls = _alembic_column_calls(path, "upgrade", "add_column")
    downgrade_calls = _alembic_column_calls(path, "downgrade", "drop_column")
    for name in columns:
        assert ("runs", name) in upgrade_calls
        assert ("runs", name) in downgrade_calls

    owner_declaration = _normalized_sql(upgrade_calls[("runs", "lease_owner_id")])
    token_declaration = _normalized_sql(upgrade_calls[("runs", "lease_fencing_token")])
    expiry_declaration = _normalized_sql(upgrade_calls[("runs", "lease_expires_at")])
    heartbeat_declaration = _normalized_sql(upgrade_calls[("runs", "last_heartbeat_at")])
    assert re.search(r"\bUUID\s*\(", owner_declaration), (
        "runs.lease_owner_id must use PostgreSQL UUID semantics"
    )
    assert "BIGINT" in token_declaration or "BIGINTEGER" in token_declaration
    assert re.search(r"NULLABLE\s*=\s*FALSE", token_declaration)
    for column_name, declaration in (
        ("lease_expires_at", expiry_declaration),
        ("last_heartbeat_at", heartbeat_declaration),
    ):
        assert "TIMESTAMPTZ" in declaration or re.search(
            r"(?:DATETIME|TIMESTAMP)\s*\([^)]*TIMEZONE\s*=\s*TRUE",
            declaration,
        ), f"runs.{column_name} must be timezone-aware TIMESTAMPTZ"

    constraints = [
        sql
        for table_name, sql in _alembic_check_constraints(path, "upgrade")
        if table_name == "runs"
    ]
    assert any(_is_coherent_running_lease_constraint(sql) for sql in constraints), (
        "one runs CHECK constraint must make RUNNING imply a non-null owner, "
        "positive fencing token, expiry, and heartbeat"
    )
    assert any(_enforces_nonnegative_lease_fencing_token(sql) for sql in constraints), (
        "runs.lease_fencing_token must reject negative persisted fencing epochs"
    )
    assert _function_or_called_local_helper_raises(path, "upgrade"), (
        "upgrade must abort rather than fabricate a lease for a legacy RUNNING row"
    )
    assert re.search(r"SERVER_DEFAULT\s*=.*?['\"]0['\"]", token_declaration)
    assert "RUNNING" in _normalized_sql(downgrade_source)
    assert _function_or_called_local_helper_raises(path, "downgrade"), (
        "downgrade must abort while any RUNNING row exists"
    )


def test_worker_lease_migration_executes_safe_upgrade_downgrade_upgrade_paths() -> None:
    """Static fragments are insufficient; execute both functions before the real PG U-D-U."""
    module = _load_worker_lease_migration_module()
    bind = _MigrationProbeBind(running_count=0)
    op_probe = _MigrationOpProbe(bind)
    module.op = op_probe

    module.upgrade()
    module.downgrade()
    module.upgrade()

    lease_columns = {
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    added = {
        column
        for operation, table, column in op_probe.operations
        if operation == "add_column" and table == "runs"
    }
    dropped = {
        column
        for operation, table, column in op_probe.operations
        if operation == "drop_column" and table == "runs"
    }
    assert lease_columns <= added
    assert lease_columns <= dropped
    assert any("RUNNING" in _normalized_sql(statement) for statement in bind.statements)


@pytest.mark.parametrize("function_name", ["upgrade", "downgrade"])
def test_worker_lease_migration_aborts_when_legacy_running_rows_exist(
    function_name: str,
) -> None:
    module = _load_worker_lease_migration_module()
    bind = _MigrationProbeBind(running_count=1)
    module.op = _MigrationOpProbe(bind)

    with pytest.raises(RuntimeError):
        getattr(module, function_name)()
    assert any("RUNNING" in _normalized_sql(statement) for statement in bind.statements)


@pytest.mark.parametrize("function_name", ["upgrade", "downgrade"])
def test_worker_lease_migration_locks_runs_before_safety_checks(function_name: str) -> None:
    module = _load_worker_lease_migration_module()
    bind = _MigrationProbeBind(running_count=0)
    module.op = _MigrationOpProbe(bind)

    getattr(module, function_name)()

    statements = [_normalized_sql(statement) for statement in bind.statements]
    lock_index = next(
        index
        for index, statement in enumerate(statements)
        if "LOCK TABLE RUNS IN SHARE ROW EXCLUSIVE MODE" in statement
    )
    check_index = next(
        index
        for index, statement in enumerate(statements)
        if "SELECT COUNT(*) FROM RUNS" in statement
    )
    assert lock_index < check_index, "migration safety checks must hold the claim-excluding lock"


def test_worker_lease_migration_refuses_to_drop_nonzero_fencing_history() -> None:
    module = _load_worker_lease_migration_module()
    bind = _MigrationProbeBind(running_count=0, nonzero_token_count=1)
    op_probe = _MigrationOpProbe(bind)
    module.op = op_probe

    with pytest.raises(RuntimeError, match="nonzero fencing history"):
        module.downgrade()

    assert not any(operation == "drop_column" for operation, *_rest in op_probe.operations)


def test_worker_lease_migration_upgrade_downgrade_upgrade_on_disposable_postgres() -> None:
    """Prove legacy rows survive and backfill across a real PostgreSQL U-D-U cycle."""
    _skip_or_fail_if_no_disposable_postgres()

    import os

    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    import idis.persistence.migrations as migrations_pkg

    module = _load_worker_lease_migration_module()
    revision = str(module.revision)
    down_revision = module.down_revision
    assert down_revision is None or isinstance(down_revision, str), (
        "worker lease migration must have one linear down_revision"
    )
    downgrade_target = down_revision or "base"

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    # Task 2 legitimately adds a linear successor; the Task 1 lease revision must remain on the
    # one-head ancestry chain instead of remaining the repository's permanent current head.
    assert head is not None and revision in {
        item.revision for item in script.walk_revisions("base", head)
    }, "worker lease migration must remain on the single current Alembic lineage"

    engine, cleanup_database = _create_disposable_migration_engine()
    seeded_run_ids: list[str] = []
    lease_columns = {
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    try:
        _run_alembic_action(config, engine, command.upgrade, "head")
        with engine.connect() as connection:
            running_count = connection.execute(
                text("SELECT COUNT(*) FROM runs WHERE status = 'RUNNING'")
            ).scalar_one()
        assert running_count == 0, (
            "the disposable migration database must be isolated with no RUNNING rows; "
            "the test will not delete unrelated run data"
        )
        _run_alembic_action(config, engine, command.downgrade, downgrade_target)
        assert lease_columns.isdisjoint(_postgres_run_columns(engine))
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == down_revision

        seed_tenant_id = str(uuid.uuid4())
        statuses = ("QUEUED", "SUCCEEDED", "FAILED", "CANCELLED")
        with engine.begin() as connection:
            for index, status in enumerate(statuses):
                deal_id = str(uuid.uuid4())
                run_id = str(uuid.uuid4())
                seeded_run_ids.append(run_id)
                connection.execute(
                    text(
                        "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
                        "VALUES (CAST(:deal_id AS UUID), CAST(:tenant_id AS UUID), "
                        ":name, :created_at)"
                    ),
                    {
                        "deal_id": deal_id,
                        "tenant_id": seed_tenant_id,
                        "name": f"slice100-worker-lease-migration-{status.lower()}",
                        "created_at": datetime(2026, 7, 15, 8, index, tzinfo=UTC),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO runs "
                        "(run_id, tenant_id, deal_id, mode, status, started_at, "
                        "finished_at, idempotency_key, created_at, source) "
                        "VALUES (CAST(:run_id AS UUID), CAST(:tenant_id AS UUID), "
                        "CAST(:deal_id AS UUID), :mode, :status, :started_at, "
                        ":finished_at, :idempotency_key, :created_at, "
                        "CAST(:source AS JSONB))"
                    ),
                    {
                        "run_id": run_id,
                        "tenant_id": seed_tenant_id,
                        "deal_id": deal_id,
                        "mode": "FULL" if index % 2 else "SNAPSHOT",
                        "status": status,
                        "started_at": datetime(2026, 7, 15, 9, index, tzinfo=UTC),
                        "finished_at": (
                            None
                            if status == "QUEUED"
                            else datetime(2026, 7, 15, 10, index, tzinfo=UTC)
                        ),
                        "idempotency_key": f"slice100-lease-migration-{run_id}",
                        "created_at": datetime(2026, 7, 15, 8, index, tzinfo=UTC),
                        "source": json.dumps(
                            {"type": "deal_documents", "document_ids": [f"doc-{index}"]}
                        ),
                    },
                )

        legacy_snapshot = _postgres_seeded_run_snapshot(engine, seeded_run_ids)

        _run_alembic_action(config, engine, command.upgrade, revision)
        _assert_postgres_worker_lease_schema(engine)
        assert _postgres_seeded_run_snapshot(engine, seeded_run_ids) == legacy_snapshot
        _assert_postgres_seeded_lease_backfill(engine, seeded_run_ids)
        _assert_postgres_running_constraint_behavior(engine, seeded_run_ids[0])
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE runs SET lease_fencing_token = 2 WHERE run_id = CAST(:run_id AS UUID)"
                ),
                {"run_id": seeded_run_ids[0]},
            )
        with pytest.raises(RuntimeError, match="nonzero fencing history"):
            _run_alembic_action(config, engine, command.downgrade, downgrade_target)
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == revision
            retained_token = connection.execute(
                text("SELECT lease_fencing_token FROM runs WHERE run_id = CAST(:run_id AS UUID)"),
                {"run_id": seeded_run_ids[0]},
            ).scalar_one()
            assert retained_token == 2
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE runs SET lease_fencing_token = 0 WHERE run_id = CAST(:run_id AS UUID)"
                ),
                {"run_id": seeded_run_ids[0]},
            )
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == revision

        _run_alembic_action(config, engine, command.downgrade, downgrade_target)
        assert lease_columns.isdisjoint(_postgres_run_columns(engine))
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == down_revision
        assert _postgres_seeded_run_snapshot(engine, seeded_run_ids) == legacy_snapshot

        _run_alembic_action(config, engine, command.upgrade, revision)
        _assert_postgres_worker_lease_schema(engine)
        assert _postgres_seeded_run_snapshot(engine, seeded_run_ids) == legacy_snapshot
        _assert_postgres_seeded_lease_backfill(engine, seeded_run_ids)
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == revision
    finally:
        cleanup_database()


def test_crashed_running_worker_is_requeued_then_second_worker_claims_higher_token() -> None:
    """A crash leaves RUNNING durable; expiry requeues before a distinct new claim."""
    repository = _seed_queued_run()
    _require_parameters(
        repository.claim_queued_runs,
        {"limit", "lease_owner_id", "lease_seconds"},
        contract="in-memory atomic worker claim twin",
    )
    reclaim = getattr(repository, "requeue_expired_worker_leases", None)
    assert callable(reclaim), (
        "crash recovery is missing requeue_expired_worker_leases; RUNNING would remain stuck"
    )
    _require_parameters(reclaim, {"limit"}, contract="expired lease requeue")

    first = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=FIRST_WORKER_ID,
        lease_seconds=1,
    )
    assert first[0]["status"] == "RUNNING"
    assert first[0]["lease_fencing_token"] == 1

    # Simulate process death after the RUNNING claim transaction committed.
    runs_module._in_memory_runs_store[RUN_ID]["lease_expires_at"] = "2000-01-01T00:00:00Z"
    requeued = reclaim(limit=1)
    assert len(requeued) == 1
    assert requeued[0]["run_id"] == RUN_ID
    assert requeued[0]["status"] == "QUEUED"
    assert requeued[0]["lease_fencing_token"] == 2
    assert repository.has_active_run(DEAL_ID) is True

    second = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=SECOND_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
    )
    assert len(second) == 1
    assert second[0]["run_id"] == RUN_ID
    assert second[0]["status"] == "RUNNING"
    assert second[0]["lease_owner_id"] == SECOND_WORKER_ID
    assert second[0]["lease_fencing_token"] == 3
    assert len(runs_module._in_memory_runs_store) == 1, "reclaim must not create a replacement run"


def test_expired_postgres_reclaim_is_one_atomic_running_to_queued_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(
        [_leased_row(status="QUEUED", owner_id=None, fencing_token=2)]
    )
    repository = PostgresRunsRepository(connection, TENANT_ID)
    reclaim = getattr(repository, "requeue_expired_worker_leases", None)
    assert callable(reclaim), "Postgres crash recovery is missing expired-lease requeue"
    _require_parameters(
        reclaim,
        {"limit", "lease_audit_emitter"},
        contract="atomic expired Postgres lease requeue",
    )
    audited: list[dict[str, Any]] = []

    requeued = reclaim(limit=1, lease_audit_emitter=lambda **event: audited.append(event))

    assert len(connection.calls) == 1, "reclaim must be exactly one database statement"
    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    assert len(re.findall(r"\bUPDATE\s+RUNS\b", normalized)) == 1
    assert re.search(r"\bINSERT\b|\bDELETE\b", normalized) is None
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert re.search(r"LIMIT\s+:\w+", normalized)
    assert re.search(r"(?:\w+\.)?TENANT_ID\s*=\s*:\w+", normalized)
    _assert_update_assigns_value(
        sql,
        params,
        table="runs",
        column="status",
        value="QUEUED",
    )
    assert "RUNNING" in normalized or "RUNNING" in {str(value).upper() for value in params.values()}
    assert re.search(
        r"LEASE_EXPIRES_AT\s*<=\s*(?:STATEMENT_TIMESTAMP\(\)|CURRENT_TIMESTAMP)",
        normalized,
    )
    assert re.search(r"LEASE_OWNER_ID\s*=\s*NULL", normalized)
    assert re.search(r"LEASE_EXPIRES_AT\s*=\s*NULL", normalized)
    assert re.search(
        r"LEASE_FENCING_TOKEN\s*=\s*(?:\w+\.)?LEASE_FENCING_TOKEN\s*\+\s*1",
        normalized,
    )
    set_clause = normalized.split("SET", maxsplit=1)[1].split("WHERE", maxsplit=1)[0]
    assert "LAST_HEARTBEAT_AT" not in set_clause, "reclaim must retain heartbeat history"
    assert "RETURNING" in normalized
    for returned in (
        "RUN_ID",
        "TENANT_ID",
        "STATUS",
        "LEASE_OWNER_ID",
        "LEASE_FENCING_TOKEN",
        "LEASE_EXPIRES_AT",
        "LAST_HEARTBEAT_AT",
    ):
        assert returned in normalized.split("RETURNING", maxsplit=1)[1]
    assert TENANT_ID in params.values()
    assert 1 in params.values()
    assert len(requeued) == 1
    assert requeued[0]["status"] == "QUEUED"
    assert requeued[0]["lease_owner_id"] is None
    assert requeued[0]["lease_fencing_token"] == 2
    assert [event["code"] for event in audited] == [
        "WORKER_LEASE_EXPIRED",
        "WORKER_LEASE_RECLAIMED",
    ]
    assert all(event["db_conn"] is connection for event in audited)
    assert all(event["prior_lease_owner_id"] == FIRST_WORKER_ID for event in audited)
    assert all(event["prior_fencing_token"] == 1 for event in audited)
    assert all(event["new_fencing_token"] == 2 for event in audited)


def test_heartbeat_renews_only_the_current_unexpired_owner_and_token() -> None:
    repository = _seed_queued_run()
    _require_parameters(
        repository.claim_queued_runs,
        {"limit", "lease_owner_id", "lease_seconds"},
        contract="heartbeat setup claim",
    )
    renew = getattr(repository, "renew_worker_lease", None)
    assert callable(renew), "worker lease repository is missing renew_worker_lease"
    _require_parameters(
        renew,
        {"run_id", "lease_owner_id", "lease_fencing_token", "lease_seconds"},
        contract="worker heartbeat renewal",
    )

    claim = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=FIRST_WORKER_ID,
        lease_seconds=LEASE_SECONDS,
    )[0]
    first_expiry = claim["lease_expires_at"]

    assert (
        renew(
            run_id=RUN_ID,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            lease_seconds=LEASE_SECONDS * 2,
        )
        is True
    )
    renewed = repository.get(RUN_ID)
    assert renewed is not None
    assert renewed["last_heartbeat_at"] is not None
    assert renewed["lease_expires_at"] > first_expiry
    assert (
        renew(
            run_id=RUN_ID,
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=1,
            lease_seconds=LEASE_SECONDS,
        )
        is False
    )

    # Even the exact owner/token is fenced after database-time expiry.
    runs_module._in_memory_runs_store[RUN_ID]["lease_expires_at"] = "2000-01-01T00:00:00Z"
    expired_snapshot = dict(runs_module._in_memory_runs_store[RUN_ID])
    assert (
        renew(
            run_id=RUN_ID,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            lease_seconds=LEASE_SECONDS,
        )
        is False
    )
    assert runs_module._in_memory_runs_store[RUN_ID] == expired_snapshot
    assert (
        renew(
            run_id=RUN_ID,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=0,
            lease_seconds=LEASE_SECONDS,
        )
        is False
    )


def test_postgres_heartbeat_is_one_atomic_fenced_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(
        [_leased_row(status="RUNNING", owner_id=FIRST_WORKER_ID, fencing_token=1)]
    )
    repository = PostgresRunsRepository(connection, TENANT_ID)
    renew = getattr(repository, "renew_worker_lease", None)
    assert callable(renew), "Postgres worker lease repository is missing heartbeat renewal"
    _require_parameters(
        renew,
        {
            "run_id",
            "lease_owner_id",
            "lease_fencing_token",
            "lease_seconds",
            "lease_audit_emitter",
        },
        contract="atomic fenced Postgres heartbeat",
    )
    audited: list[dict[str, Any]] = []

    renewed = renew(
        run_id=RUN_ID,
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=1,
        lease_seconds=LEASE_SECONDS,
        lease_audit_emitter=lambda **event: audited.append(event),
    )

    assert len(connection.calls) == 1, "heartbeat must be exactly one database statement"
    sql, params = connection.calls[0]
    normalized = _normalized_sql(sql)
    assert len(re.findall(r"\bUPDATE\s+RUNS\b", normalized)) == 1
    assert re.search(r"\bINSERT\b|\bDELETE\b", normalized) is None
    assert re.search(r"(?:\w+\.)?TENANT_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?RUN_ID\s*=\s*:\w+", normalized)
    _assert_sql_column_equals_value(sql, params, column="status", value="RUNNING")
    assert re.search(r"(?:\w+\.)?LEASE_OWNER_ID\s*=\s*:\w+", normalized)
    assert re.search(r"(?:\w+\.)?LEASE_FENCING_TOKEN\s*=\s*:\w+", normalized)
    assert re.search(
        r"LEASE_EXPIRES_AT\s*>\s*(?:STATEMENT_TIMESTAMP\(\)|CURRENT_TIMESTAMP)",
        normalized,
    )
    assert re.search(r"LAST_HEARTBEAT_AT\s*=\s*STATEMENT_TIMESTAMP\(\)", normalized)
    assert re.search(r"LEASE_EXPIRES_AT\s*=\s*STATEMENT_TIMESTAMP\(\)\s*\+", normalized)
    assert LEASE_SECONDS in params.values()
    set_clause = normalized.split("SET", maxsplit=1)[1].split("WHERE", maxsplit=1)[0]
    assert "LEASE_FENCING_TOKEN" not in set_clause
    assert "LEASE_OWNER_ID" not in set_clause
    assert "RETURNING" in normalized
    assert "LEASE_EXPIRES_AT" in normalized.split("RETURNING", maxsplit=1)[1]
    assert TENANT_ID in params.values()
    assert RUN_ID in params.values()
    assert FIRST_WORKER_ID in params.values()
    assert 1 in params.values()
    assert LEASE_SECONDS in params.values()
    assert renewed is True
    assert len(audited) == 1
    assert audited[0]["db_conn"] is connection
    assert audited[0]["code"] == "WORKER_LEASE_HEARTBEAT_RENEWED"
    assert audited[0]["lease_owner_id"] == FIRST_WORKER_ID
    assert audited[0]["current_fencing_token"] == 1
    assert audited[0]["lease_expires_at"] is not None


@pytest.mark.parametrize("operation", ["claim", "heartbeat", "reclaim"])
def test_guarded_lease_transition_emits_no_audit_when_cas_matches_no_row(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection([None])
    repository = PostgresRunsRepository(connection, TENANT_ID)
    audited: list[dict[str, Any]] = []

    def emitter(**event: Any) -> None:
        audited.append(event)

    if operation == "claim":
        method = repository.claim_queued_runs
        _require_parameters(method, {"lease_audit_emitter"}, contract="claim audit CAS")
        result = method(
            limit=1,
            lease_owner_id=FIRST_WORKER_ID,
            lease_seconds=LEASE_SECONDS,
            lease_audit_emitter=emitter,
        )
        assert result == []
    elif operation == "heartbeat":
        method = getattr(repository, "renew_worker_lease", None)
        assert callable(method)
        result = method(
            run_id=RUN_ID,
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            lease_seconds=LEASE_SECONDS,
            lease_audit_emitter=emitter,
        )
        assert result is False
    else:
        method = getattr(repository, "requeue_expired_worker_leases", None)
        assert callable(method)
        result = method(limit=1, lease_audit_emitter=emitter)
        assert result == []

    assert audited == []


@pytest.mark.parametrize("operation", ["claim", "heartbeat", "reclaim"])
def test_lease_transition_audit_failure_aborts_before_caller_can_commit(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """The repository does not swallow durable-audit failure or own the outer commit."""
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    row = _leased_row(
        status="QUEUED" if operation == "reclaim" else "RUNNING",
        owner_id=None if operation == "reclaim" else FIRST_WORKER_ID,
        fencing_token=2 if operation == "reclaim" else 1,
    )
    connection = _CapturingConnection([row])
    repository = PostgresRunsRepository(connection, TENANT_ID)

    def fail_audit(**event: Any) -> None:
        assert event["db_conn"] is connection
        raise AuditSinkError("injected lease audit failure")

    with pytest.raises(AuditSinkError, match="injected lease audit failure"):
        if operation == "claim":
            repository.claim_queued_runs(
                limit=1,
                lease_owner_id=FIRST_WORKER_ID,
                lease_seconds=LEASE_SECONDS,
                lease_audit_emitter=fail_audit,
            )
        elif operation == "heartbeat":
            renew = getattr(repository, "renew_worker_lease", None)
            assert callable(renew)
            renew(
                run_id=RUN_ID,
                lease_owner_id=FIRST_WORKER_ID,
                lease_fencing_token=1,
                lease_seconds=LEASE_SECONDS,
                lease_audit_emitter=fail_audit,
            )
        else:
            reclaim = getattr(repository, "requeue_expired_worker_leases", None)
            assert callable(reclaim)
            reclaim(limit=1, lease_audit_emitter=fail_audit)

    assert connection.commits == 0
    assert connection.rollbacks == 0


def test_reclaim_second_audit_failure_leaves_both_events_uncommitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "set_tenant_local", lambda *_args: None)
    connection = _CapturingConnection(
        [_leased_row(status="QUEUED", owner_id=None, fencing_token=2)]
    )
    repository = PostgresRunsRepository(connection, TENANT_ID)
    reclaim = getattr(repository, "requeue_expired_worker_leases", None)
    assert callable(reclaim)
    observed: list[str] = []

    def fail_second(**event: Any) -> None:
        assert event["db_conn"] is connection
        observed.append(event["code"])
        if event["code"] == "WORKER_LEASE_RECLAIMED":
            raise AuditSinkError("injected second reclaim audit failure")

    with pytest.raises(AuditSinkError, match="injected second reclaim audit failure"):
        reclaim(limit=1, lease_audit_emitter=fail_second)
    assert observed == ["WORKER_LEASE_EXPIRED", "WORKER_LEASE_RECLAIMED"]
    assert connection.commits == 0
    assert connection.rollbacks == 0
