"""RED contracts for strict worker methodology composition and resume."""

from __future__ import annotations

import uuid
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

import idis.pipeline.worker as worker_module
from idis.methodology.models import MethodologyType
from idis.models.methodology_coverage import (
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
)
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task5.test_durable_selection_coverage_red import (
    _MethodologyRepository,
    _registry,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
LEASE_FENCING_TOKEN = 7


def _coverage_records() -> list[MethodologyCoverageRecord]:
    records: list[MethodologyCoverageRecord] = []
    for methodology_type, count in (
        (MethodologyType.COMMERCIAL_DD, 21),
        (MethodologyType.FINANCIAL_DD, 79),
    ):
        for ordinal in range(1, count + 1):
            records.append(
                MethodologyCoverageRecord(
                    tenant_id=TENANT_ID,
                    deal_id=DEAL_ID,
                    run_id=RUN_ID,
                    methodology_id=methodology_type.value,
                    methodology_version_id=f"{methodology_type.value}:frozen-v1",
                    methodology_question_id=f"mq_{methodology_type.value}_{ordinal:04d}",
                    methodology_type=methodology_type,
                    section=(
                        "Fictional commercial section"
                        if methodology_type is MethodologyType.COMMERCIAL_DD
                        else "P&L"
                    ),
                    status=MethodologyCoverageStatus.NOT_STARTED,
                )
            )
    return records


def _context(**overrides: Any) -> RunContext:
    values: dict[str, Any] = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "mode": "FULL",
        "documents": [],
        "extract_fn": lambda **_kwargs: {},
        "grade_fn": lambda **_kwargs: {},
        "strict_resume_validation_required": True,
    }
    values.update(overrides)
    return RunContext(**values)


def test_strict_worker_composition_injects_durable_init_and_rehydrate_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    opened: list[object] = []
    closed: list[object] = []
    repository = _MethodologyRepository(
        (
            _registry(MethodologyType.COMMERCIAL_DD, 21),
            _registry(MethodologyType.FINANCIAL_DD, 79),
        )
    )
    repository.list_coverage_records = (  # type: ignore[attr-defined,method-assign]
        lambda *, deal_id, run_id: list(repository.durable_rows)
    )

    @contextmanager
    def connection_factory():  # type: ignore[no-untyped-def]
        connection = object()
        opened.append(connection)
        try:
            yield connection
        finally:
            closed.append(connection)

    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: True)
    monkeypatch.setattr(worker_module, "set_tenant_local", lambda *_args: None)
    monkeypatch.setattr(
        worker_module,
        "PostgresRunsRepository",
        lambda _connection, _tenant_id: SimpleNamespace(
            is_durable=True,
            require_run_scope=lambda **_kwargs: None,
            require_active_worker_lease=lambda **_kwargs: None,
        ),
    )
    monkeypatch.setattr(
        "idis.persistence.repositories.methodologies.get_methodologies_repository",
        lambda _connection, _tenant_id: repository,
    )
    monkeypatch.setattr(worker_module, "_load_worker_preflight_corpus", lambda **_kwargs: [])
    monkeypatch.setattr(worker_module, "_load_worker_deal_metadata", lambda **_kwargs: {})
    monkeypatch.setattr(
        "idis.services.runs.steps.extraction_ready_documents_from_preflight_corpus",
        lambda _corpus: [],
    )

    def capture_context(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr("idis.services.runs.steps.build_run_context", capture_context)

    from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH

    worker_module._default_run_context_factory(
        db_conn=object(),
        tenant_id=TENANT_ID,
        run_data={
            "run_id": RUN_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "status": "RUNNING",
            "pipeline_contract_version": "full-strict-v21",
            "pipeline_contract_hash": FULL_STRICT_V21_CONTRACT_HASH,
            "pipeline_resume_state": "RESUMABLE",
            "lease_owner_id": LEASE_OWNER_ID,
            "lease_fencing_token": LEASE_FENCING_TOKEN,
        },
        audit_sink=object(),
        lease_config=worker_module.WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
        connection_factory=connection_factory,
        strict_profile=SimpleNamespace(values={}),  # type: ignore[arg-type]
    )

    assert callable(captured.get("methodology_coverage_init_fn"))
    assert callable(captured.get("methodology_coverage_rehydrate_fn"))
    assert (
        captured["methodology_coverage_init_fn"]
        is not captured["methodology_coverage_rehydrate_fn"]
    )
    result, initialized = captured["methodology_coverage_init_fn"](
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=object(),
    )
    rehydrated = captured["methodology_coverage_rehydrate_fn"](
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
    )

    assert result.summary.total_questions == 100
    assert len(initialized) == len(rehydrated) == 100
    assert len(opened) == len(closed) == 2
    assert opened == closed
    assert opened[0] is not opened[1]


def test_production_callbacks_use_fresh_real_postgres_transactions_for_init_and_rehydrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdd = _registry(MethodologyType.COMMERCIAL_DD, 21)
    fdd = _registry(MethodologyType.FINANCIAL_DD, 79)

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        grant_application_access(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants (tenant_id, name) VALUES (CAST(:id AS uuid), 'Task5')"),
                {"id": TENANT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
                    "VALUES (CAST(:deal AS uuid), CAST(:tenant AS uuid), 'Task5 deal', now())"
                ),
                {"deal": DEAL_ID, "tenant": TENANT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO runs "
                    "(run_id, tenant_id, deal_id, mode, status, started_at, created_at) "
                    "VALUES (CAST(:run AS uuid), CAST(:tenant AS uuid), "
                    "CAST(:deal AS uuid), 'FULL', 'QUEUED', now(), now())"
                ),
                {"run": RUN_ID, "tenant": TENANT_ID, "deal": DEAL_ID},
            )

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologiesRepository(connection, TENANT_ID)
            frozen_records = []
            for registry in (cdd, fdd):
                imported = repository.import_registry(
                    registry=registry,
                    source_type=registry.current_version.questions[0].source_trace.source_type,
                    source_alias="configured-source",
                    source_byte_count=1,
                    source_contract_hash="c" * 64,
                    source_trace_digest=methodology_source_trace_digest(registry),
                    imported_by_actor_id="task5-composition-test",
                    import_audit_event_id=str(uuid.uuid4()),
                )
                frozen_records.append(
                    repository.freeze_version(
                        methodology_version_record_id=(imported.methodology_version_record_id),
                        expected_source_sha256=registry.current_version.source_hash,
                        frozen_by_actor_id="task5-composition-test",
                        freeze_audit_event_id=str(uuid.uuid4()),
                        expected_registry=registry,
                    )
                )
            repository.create_run_selection(
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                commercial_dd_version_record_id=(frozen_records[0].methodology_version_record_id),
                financial_dd_version_record_id=(frozen_records[1].methodology_version_record_id),
                created_by_actor_id="task5-composition-test",
                selection_audit_event_id=str(uuid.uuid4()),
            )
            claimed = PostgresRunsRepository(connection, TENANT_ID).claim_queued_runs(
                limit=1,
                lease_owner_id=LEASE_OWNER_ID,
                lease_seconds=60,
            )
            assert len(claimed) == 1
            assert int(claimed[0]["lease_fencing_token"]) == 1

        callback_connections: list[Any] = []

        @contextmanager
        def fresh_transaction():  # type: ignore[no-untyped-def]
            with database.app_engine.begin() as connection:
                callback_connections.append(connection)
                yield connection

        def forbidden_in_memory(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("strict production callback constructed in-memory coverage")

        monkeypatch.setattr(InMemoryMethodologyCoverageService, "__init__", forbidden_in_memory)
        initialize, rehydrate = worker_module.build_postgres_methodology_coverage_callbacks(
            connection_factory=fresh_transaction,
            tenant_id=TENANT_ID,
            lease_owner_id=LEASE_OWNER_ID,
            lease_fencing_token=1,
        )

        result, initialized = initialize(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            registry=object(),
        )
        resumed = rehydrate(tenant_id=TENANT_ID, deal_id=DEAL_ID, run_id=RUN_ID)

        assert result.summary.total_questions == 100
        assert len(initialized) == len(resumed) == 100
        assert len(callback_connections) == 2
        assert callback_connections[0] is not callback_connections[1]
        with database.app_engine.begin() as connection:
            repository = PostgresMethodologiesRepository(connection, TENANT_ID)
            assert len(repository.list_coverage_records(deal_id=DEAL_ID, run_id=RUN_ID)) == 100


def test_strict_rehydrate_uses_persisted_exact_100_without_in_memory_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _coverage_records()
    calls: list[tuple[str, str, str]] = []

    def rehydrate(*, tenant_id: str, deal_id: str, run_id: str) -> list[Any]:
        calls.append((tenant_id, deal_id, run_id))
        return list(records)

    def forbidden_in_memory(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict worker constructed the in-memory coverage service")

    monkeypatch.setattr(InMemoryMethodologyCoverageService, "__init__", forbidden_in_memory)
    context = _context(methodology_coverage_rehydrate_fn=rehydrate)

    RunOrchestrator._rehydrate_methodology_coverage_records(object(), context)  # type: ignore[arg-type]

    assert calls == [(TENANT_ID, DEAL_ID, RUN_ID)]
    assert context.methodology_coverage_records == sorted(
        records, key=lambda record: record.coverage_record_id
    )
    assert Counter(record.methodology_type for record in context.methodology_coverage_records) == {
        MethodologyType.COMMERCIAL_DD: 21,
        MethodologyType.FINANCIAL_DD: 79,
    }


@pytest.mark.parametrize("count", [0, 99, 101])
def test_strict_rehydrate_rejects_incomplete_persisted_coverage(count: int) -> None:
    records = _coverage_records()[:count]
    if count == 101:
        records.append(
            records[0].model_copy(update={"methodology_question_id": "mq_commercial_dd_extra_0022"})
        )
    context = _context(
        methodology_coverage_rehydrate_fn=lambda **_kwargs: list(records),
    )

    with pytest.raises(RunStepBlockedError) as raised:
        RunOrchestrator._rehydrate_methodology_coverage_records(  # type: ignore[arg-type]
            object(), context
        )

    assert raised.value.code == "METHODOLOGY_COVERAGE_INCOMPLETE"


def test_strict_rehydrate_missing_durable_reader_never_falls_back_to_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def forbidden_in_memory(*_args: Any, **_kwargs: Any) -> None:
        nonlocal constructed
        constructed = True
        raise AssertionError("strict worker constructed the in-memory coverage service")

    monkeypatch.setattr(InMemoryMethodologyCoverageService, "__init__", forbidden_in_memory)
    context = _context()

    with pytest.raises(RunStepBlockedError) as raised:
        RunOrchestrator._rehydrate_methodology_coverage_records(  # type: ignore[arg-type]
            object(), context
        )

    assert raised.value.code == "METHODOLOGY_COVERAGE_INCOMPLETE"
    assert constructed is False
