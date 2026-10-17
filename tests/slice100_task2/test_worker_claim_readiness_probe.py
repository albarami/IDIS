"""Task 2 RED/green contracts for operational worker-claim admission."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _valid_worker_env() -> dict[str, str]:
    tenant_id = "10000000-0000-4000-8000-000000000001"
    return {
        "IDIS_ACCEPTANCE_TENANT_ID": tenant_id,
        "IDIS_WORKER_TENANT_IDS": tenant_id,
        "IDIS_PROCESS_ROLE": "worker",
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "10",
    }


def _passing_facts(module: Any, **overrides: object) -> object:
    values: dict[str, object] = {
        "backend": "postgres",
        "queued_row_ok": True,
        "claim_ok": True,
        "fencing_ok": True,
        "claim_audit_ok": True,
        "heartbeat_ok": True,
        "reclaim_ok": True,
        "fresh_claim_ok": True,
        "stale_write_rejected": True,
        "boundary_rollback_ok": True,
        "no_residue": True,
    }
    values.update(overrides)
    return module.WorkerProbeFacts(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "memory"},
        {"queued_row_ok": False},
        {"claim_ok": False},
        {"fencing_ok": False},
        {"claim_audit_ok": False},
        {"heartbeat_ok": False},
        {"reclaim_ok": False},
        {"fresh_claim_ok": False},
        {"stale_write_rejected": False},
        {"boundary_rollback_ok": False},
        {"no_residue": False},
    ],
)
def test_worker_readiness_requires_an_operational_rollback_safe_claim(
    overrides: dict[str, object],
) -> None:
    import idis.runtime.dependency_readiness as module

    check = module.check_worker_readiness(
        env=_valid_worker_env(),
        expected_role="worker",
        probe=lambda: _passing_facts(module, **overrides),
    )

    assert check.component == "worker"
    assert check.may_proceed is False
    assert check.code == "WORKER_CLAIM_NOT_PROVEN"


def test_worker_readiness_fails_closed_when_claim_probe_raises() -> None:
    import idis.runtime.dependency_readiness as module

    def unavailable() -> object:
        raise RuntimeError("connection detail must not escape")

    check = module.check_worker_readiness(
        env=_valid_worker_env(),
        expected_role="worker",
        probe=unavailable,
    )

    assert check.component == "worker"
    assert check.may_proceed is False
    assert check.code == "WORKER_CLAIM_NOT_PROVEN"


def test_worker_readiness_accepts_a_complete_operational_claim_probe() -> None:
    import idis.runtime.dependency_readiness as module

    check = module.check_worker_readiness(
        env=_valid_worker_env(),
        expected_role="worker",
        probe=lambda: _passing_facts(module),
    )

    assert check.component == "worker"
    assert check.may_proceed is True
    assert check.code is None


@pytest.mark.parametrize(
    "audit_origin,expected_claim_audit_ok",
    [
        ("canonical-worker", True),
        ("api-inline-equal-request-id", False),
        ("extra-api-inline-equal-request-id", True),
        ("wrong-actor", False),
        ("missing-actor", False),
        ("wrong-path", False),
        ("wrong-method", False),
        ("wrong-status", False),
        ("unknown-origin", False),
    ],
)
@pytest.mark.parametrize(
    (
        "heartbeat_advances",
        "boundary_probe_result",
        "expected_heartbeat_ok",
        "expected_boundary_rollback_ok",
    ),
    [
        pytest.param(True, True, True, True, id="persisted-heartbeat-and-boundary-proven"),
        pytest.param(
            False,
            True,
            False,
            True,
            id="renew-reports-success-and-audits-but-row-is-unchanged",
        ),
        pytest.param(
            True,
            False,
            True,
            False,
            id="outer-rollback-is-not-a-step-boundary-rollback-proof",
        ),
    ],
)
def test_worker_claim_collector_proves_persisted_heartbeat_and_rolls_back(
    heartbeat_advances: bool,
    boundary_probe_result: bool,
    expected_heartbeat_ok: bool,
    expected_boundary_rollback_ok: bool,
    audit_origin: str,
    expected_claim_audit_ok: bool,
) -> None:
    import idis.pipeline.worker as worker_module
    import idis.runtime.dependency_probes as module
    from idis.audit.postgres_sink import PostgresAuditSink

    rollbacks: list[int] = []
    tenant_scopes: list[str] = []
    repositories: list[object] = []
    statements: list[str] = []
    audit_inserts: list[dict[str, object]] = []
    initial_heartbeat = datetime(2099, 1, 1, tzinfo=UTC)
    initial_expiry = initial_heartbeat + timedelta(minutes=1)
    lease_state = {
        "last_heartbeat_at": initial_heartbeat,
        "lease_expires_at": initial_expiry,
        "observed_at": initial_heartbeat + timedelta(seconds=30),
    }
    boundary_probe_calls: list[dict[str, object]] = []

    class Transaction:
        def rollback(self) -> None:
            rollbacks.append(1)

    class Result:
        def __init__(self, value: object = None, rows: list[object] | None = None) -> None:
            self.value = value
            self.rows = rows or []

        def scalar_one(self) -> object:
            return self.value

        def mappings(self) -> Result:
            return self

        def all(self) -> list[object]:
            return self.rows

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Transaction:
            return Transaction()

        def execute(self, statement: object, params: object = None) -> Result:
            rendered = str(statement)
            statements.append(rendered)
            if "INSERT INTO audit_events" in rendered:
                assert isinstance(params, dict)
                audit_inserts.append(dict(params))
                return Result()
            if "FROM audit_events" in rendered and "EXISTS" not in rendered:
                rows: list[object] = []
                for inserted in audit_inserts:
                    event = json.loads(str(inserted["event"]))
                    assert event["request"]["request_id"] == event["payload"]["safe"]["run_id"]
                    if audit_origin in {
                        "api-inline-equal-request-id",
                        "extra-api-inline-equal-request-id",
                    }:
                        if audit_origin == "extra-api-inline-equal-request-id":
                            rows.append(dict(inserted))
                        event["actor"] = {
                            "actor_type": "SERVICE",
                            "actor_id": "idis-api-inline-executor",
                        }
                        event["request"]["path"] = "/v1/deals/test-deal/runs"
                        event["request"]["status_code"] = 202
                        event["payload"]["safe"]["execution_origin"] = "API_INLINE"
                    elif audit_origin == "wrong-actor":
                        event["actor"]["actor_id"] = "another-component"
                    elif audit_origin == "missing-actor":
                        event.pop("actor")
                    elif audit_origin == "wrong-path":
                        event["request"]["path"] = "/v1/other"
                    elif audit_origin == "wrong-method":
                        event["request"]["method"] = "GET"
                    elif audit_origin == "wrong-status":
                        event["request"]["status_code"] = 202
                    elif audit_origin == "unknown-origin":
                        event["payload"]["safe"]["execution_origin"] = "UNKNOWN_COMPONENT"
                    rows.append({**inserted, "event": event})
                return Result(rows=rows)
            if "SELECT last_heartbeat_at, lease_expires_at" in rendered:
                return Result(rows=[dict(lease_state)])
            if "UPDATE runs" in rendered and "lease_expires_at" in rendered:
                return Result(rows=[{"run_id": "probe"}])
            return Result(False if "EXISTS" in rendered else None)

    class RunsRepository:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection
            self.created: dict[str, object] = {}
            self.fencing_token = 0
            self.lease_owner_id: str | None = None

        def create(self, **kwargs: object) -> dict[str, object]:
            self.created = dict(kwargs)
            return {
                **kwargs,
                "tenant_id": tenant_scopes[-1],
                "status": "QUEUED",
            }

        def claim_queued_runs(
            self,
            *,
            limit: int,
            lease_owner_id: str,
            lease_seconds: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, object]]:
            assert limit == 1
            assert lease_seconds == 60
            self.fencing_token += 1
            self.lease_owner_id = lease_owner_id
            lease_audit_emitter(
                db_conn=self.connection,
                code="WORKER_LEASE_CLAIMED",
                run_id=self.created["run_id"],
                deal_id=self.created["deal_id"],
                lease_owner_id=lease_owner_id,
                new_fencing_token=self.fencing_token,
                lease_expires_at="2099-01-01T00:01:00Z",
            )
            return [
                {
                    "status": "RUNNING",
                    "lease_owner_id": lease_owner_id,
                    "lease_fencing_token": self.fencing_token,
                    "lease_expires_at": "2099-01-01T00:01:00Z",
                    "last_heartbeat_at": "2099-01-01T00:00:00Z",
                }
            ]

        def renew_worker_lease(
            self,
            *,
            run_id: str,
            lease_owner_id: str,
            lease_fencing_token: int,
            lease_seconds: int,
            lease_audit_emitter: Callable[..., None],
        ) -> bool:
            assert run_id == self.created["run_id"]
            assert lease_owner_id == self.lease_owner_id
            assert lease_fencing_token == self.fencing_token == 1
            assert lease_seconds == 60
            if heartbeat_advances:
                lease_state["last_heartbeat_at"] = initial_heartbeat + timedelta(seconds=10)
                lease_state["lease_expires_at"] = initial_expiry + timedelta(minutes=1)
            lease_audit_emitter(
                db_conn=self.connection,
                code="WORKER_LEASE_HEARTBEAT_RENEWED",
                run_id=run_id,
                lease_owner_id=lease_owner_id,
                current_fencing_token=lease_fencing_token,
                lease_expires_at="2099-01-01T00:02:00Z",
            )
            return True

        def requeue_expired_worker_leases(
            self,
            *,
            limit: int,
            lease_audit_emitter: Callable[..., None],
        ) -> list[dict[str, object]]:
            assert limit == 1
            prior_owner = self.lease_owner_id
            prior_token = self.fencing_token
            self.fencing_token += 1
            self.lease_owner_id = None
            for code in ("WORKER_LEASE_EXPIRED", "WORKER_LEASE_RECLAIMED"):
                lease_audit_emitter(
                    db_conn=self.connection,
                    code=code,
                    run_id=self.created["run_id"],
                    deal_id=self.created["deal_id"],
                    prior_lease_owner_id=prior_owner,
                    prior_fencing_token=prior_token,
                    new_fencing_token=self.fencing_token,
                )
            return [
                {
                    "status": "QUEUED",
                    "lease_owner_id": None,
                    "lease_fencing_token": self.fencing_token,
                }
            ]

    class RunStepsRepository:
        def create(self, *_args: object, **_kwargs: object) -> None:
            raise worker_module.WorkerLeaseFencedError("stale probe token")

    def repository_factory(connection: object, _tenant_id: str) -> RunsRepository:
        assert isinstance(connection, Connection)
        repo = RunsRepository(connection)
        repositories.append(repo)
        return repo

    uuid_values = iter(SimpleNamespace(hex=str(index) * 32) for index in range(1, 8))

    facts = module.collect_worker_claim_facts(
        connection_factory=Connection,
        boundary_transaction_factory=Connection,
        tenant_setter=lambda _connection, tenant_id: tenant_scopes.append(tenant_id),
        runs_repository_factory=repository_factory,
        run_steps_repository_factory=lambda _connection, _tenant: RunStepsRepository(),
        run_step_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        worker_lease_fenced_error=worker_module.WorkerLeaseFencedError,
        audit_sink_factory=PostgresAuditSink,
        lease_audit_emitter_factory=worker_module._lease_transition_audit_emitter,
        lease_seconds=60,
        boundary_rollback_probe=lambda **kwargs: (
            boundary_probe_calls.append(dict(kwargs)) is None and boundary_probe_result,
            True,
        ),
        uuid_factory=lambda: next(uuid_values),
    )

    assert facts == module.WorkerProbeFacts(
        backend="postgres",
        queued_row_ok=True,
        claim_ok=True,
        fencing_ok=True,
        claim_audit_ok=expected_claim_audit_ok,
        heartbeat_ok=expected_heartbeat_ok,
        reclaim_ok=True,
        fresh_claim_ok=True,
        stale_write_rejected=True,
        boundary_rollback_ok=expected_boundary_rollback_ok,
        no_residue=True,
    )
    assert len(boundary_probe_calls) == 1
    assert len(repositories) == 1
    assert len(audit_inserts) == 5
    assert len(rollbacks) == 2
    assert len(tenant_scopes) == 2
    assert tenant_scopes[0] == tenant_scopes[1]
    assert any("INSERT INTO deals" in statement for statement in statements)
    assert any("INSERT INTO audit_events" in statement for statement in statements)
    assert sum("SELECT last_heartbeat_at, lease_expires_at" in item for item in statements) == 2
    assert any("FROM audit_events" in statement for statement in statements)
    assert any("EXISTS" in statement for statement in statements)


def _require_disposable_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "worker boundary readiness proof requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES", "0") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def test_worker_claim_collector_proves_real_boundary_rollback_on_disposable_postgres() -> None:
    """The typed admission fact must come from the production fenced boundary UoW."""
    _require_disposable_postgres()

    from alembic import command
    from alembic.config import Config

    import idis.pipeline.worker as worker_module
    import idis.runtime.dependency_probes as module
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.models.run_step import RunStep
    from idis.persistence import db
    from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
    from idis.persistence.repositories.runs import PostgresRunsRepository

    from .test_durable_governance_migration import (
        _create_disposable_engine,
        _run_alembic,
    )

    engine, cleanup = _create_disposable_engine()
    config = Config(
        str(
            Path(__file__).resolve().parents[2]
            / "src"
            / "idis"
            / "persistence"
            / "migrations"
            / "alembic.ini"
        )
    )
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "src" / "idis" / "persistence" / "migrations"),
    )
    try:
        _run_alembic(config, engine, command.upgrade, "0033")

        facts = module.collect_worker_claim_facts(
            connection_factory=engine.connect,
            boundary_transaction_factory=engine.begin,
            tenant_setter=db.set_tenant_local,
            runs_repository_factory=PostgresRunsRepository,
            run_steps_repository_factory=PostgresRunStepsRepository,
            run_step_factory=RunStep,
            worker_lease_fenced_error=worker_module.WorkerLeaseFencedError,
            audit_sink_factory=PostgresAuditSink,
            lease_audit_emitter_factory=worker_module._lease_transition_audit_emitter,
            lease_seconds=60,
        )

        assert facts == module.WorkerProbeFacts(
            backend="postgres",
            queued_row_ok=True,
            claim_ok=True,
            fencing_ok=True,
            claim_audit_ok=True,
            heartbeat_ok=True,
            reclaim_ok=True,
            fresh_claim_ok=True,
            stale_write_rejected=True,
            boundary_rollback_ok=True,
            no_residue=True,
        )
    finally:
        cleanup()
