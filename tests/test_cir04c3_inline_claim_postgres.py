"""Real app-role claim/transaction protocol, not frozen-preview acceptance.

Storage-only runs deliberately have no source corpus and are never executed.
The caller composes the canonical audit builder and durable sink with a synthetic
service actor here to isolate the repository claim protocol; production runs.py
wires the request-bound API audit hook and rebinds to the human requester.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from idis.audit.canonical import build_canonical_audit_event
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.persistence.db import get_app_engine, is_postgres_configured, set_tenant_local
from idis.persistence.repositories.runs import (
    PostgresRunsRepository,
    WorkerLeaseFencedError,
    _resolve_run_contract_identity,
)
from idis.validators.audit_event_validator import validate_audit_event
from tests.test_slice98_erasure_export_postgres import _PROOF_INSERT_SQL, _proof_insert_params


@pytest.fixture
def app_engine() -> Engine:
    if not is_postgres_configured():
        if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
            pytest.fail("required owned PostgreSQL application environment unavailable")
        pytest.skip("PostgreSQL application environment not configured")
    engine = get_app_engine()
    with engine.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ).one()
        assert tuple(role) == (False, False)
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0038"
    return engine


def _seed_run(
    engine: Engine,
    *,
    tenant_id: str | None = None,
    mode: str = "SNAPSHOT",
    mismatched_hash: bool = False,
    contract_identity: dict[str, Any] | None = None,
    historical_proof_linked: bool = False,
) -> dict[str, Any]:
    tenant = tenant_id or str(uuid.uuid4())
    with engine.begin() as conn:
        set_tenant_local(conn, tenant)
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES "
                "(CAST(:tenant AS uuid),'C3 protocol-only tenant') "
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"tenant": tenant},
        )
        deal_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO deals (deal_id,tenant_id,name,created_at) VALUES "
                "(CAST(:deal AS uuid),CAST(:tenant AS uuid),'C3 protocol-only deal',now())"
            ),
            {"tenant": tenant, "deal": deal_id},
        )
        identity = (
            {
                "pipeline_contract_version": "snapshot-v1",
                "pipeline_contract_hash": "0" * 64,
                "pipeline_resume_state": "NON_RESUMABLE",
                "pipeline_resume_error_code": "LEGACY_RUN_RESUME_UNSAFE",
            }
            if mismatched_hash
            else {}
        )
        if contract_identity is not None:
            identity = contract_identity
        if historical_proof_linked:
            # Synthetic storage-only historical backfill, using the existing C2 SQL pattern.
            # This proof has real FK parents, but is not signed operator authority, a frozen
            # corpus, runtime admission, or permission to execute a strict/native pipeline.
            assert mode == "FULL" and not identity
            proof_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute(text(_PROOF_INSERT_SQL), _proof_insert_params(tenant, deal_id, proof_id))
            version, contract_hash, resume_state, resume_error = _resolve_run_contract_identity(
                mode=mode,
                pipeline_contract_version=None,
                pipeline_contract_hash=None,
                pipeline_resume_state=None,
                pipeline_resume_error_code=None,
            )
            conn.execute(
                text(
                    "INSERT INTO runs (run_id,tenant_id,deal_id,mode,status,"
                    "strict_preflight_proof_id,pipeline_contract_version,pipeline_contract_hash,"
                    "pipeline_resume_state,pipeline_resume_error_code) VALUES "
                    "(CAST(:run AS uuid),CAST(:tenant AS uuid),CAST(:deal AS uuid),'FULL',"
                    "'QUEUED',CAST(:proof AS uuid),:version,:hash,:state,:error)"
                ),
                {
                    "run": run_id,
                    "tenant": tenant,
                    "deal": deal_id,
                    "proof": proof_id,
                    "version": version,
                    "hash": contract_hash,
                    "state": resume_state,
                    "error": resume_error,
                },
            )
            run = PostgresRunsRepository(conn, tenant).get(run_id)
            assert run is not None
            return run
        return PostgresRunsRepository(conn, tenant).create(
            run_id=str(uuid.uuid4()), deal_id=deal_id, mode=mode, **identity
        )


def _rows(engine: Engine, tenant_id: str) -> dict[str, list[str]]:
    with engine.begin() as conn:
        set_tenant_local(conn, tenant_id)
        return {
            table: list(
                conn.execute(
                    text(
                        f"SELECT to_jsonb(t)::text FROM {table} t "
                        "WHERE tenant_id=CAST(:tenant AS uuid) ORDER BY to_jsonb(t)::text"
                    ),
                    {"tenant": tenant_id},
                ).scalars()
            )
            for table in (
                "tenants",
                "deals",
                "strict_preflight_proofs",
                "runs",
                "run_steps",
                "audit_events",
            )
        }


def _claim(
    conn: Connection,
    run: dict[str, Any],
    *,
    owner: str,
    seconds: int = 60,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    repo = PostgresRunsRepository(conn, tenant_id or str(run["tenant_id"]))
    method = getattr(repo, "claim_queued_run_for_execution", None)
    assert callable(method), "PostgreSQL exact-row inline lease claim is not implemented"
    result = method(run_id=run["run_id"], lease_owner_id=owner, lease_seconds=seconds)
    assert result is None or isinstance(result, dict)
    return result


def _audit(conn: Connection, claimed: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    """Compose existing audit primitives; never substitute for API wiring coverage."""
    event = build_canonical_audit_event(
        tenant_id=str(claimed["tenant_id"]),
        event_type="worker.lease.claimed",
        severity="MEDIUM",
        summary="API-inline storage protocol lease claimed",
        actor_type="SERVICE",
        actor_id="idis-api-inline-executor",
        request_id=request_id,
        method="POST",
        path=f"/v1/deals/{claimed['deal_id']}/runs",
        status_code=202,
        resource_type="deal",
        resource_id=str(claimed["deal_id"]),
        deal_id=str(claimed["deal_id"]),
        safe={
            "code": "WORKER_LEASE_CLAIMED",
            "execution_origin": "API_INLINE",
            "run_id": str(claimed["run_id"]),
            "lease_owner_id": str(claimed["lease_owner_id"]),
            "new_fencing_token": int(claimed["lease_fencing_token"]),
            "lease_expires_at": str(claimed["lease_expires_at"]),
        },
    )
    assert validate_audit_event(event).passed
    PostgresAuditSink().emit_in_tx(conn, event)
    return event


@pytest.mark.parametrize("mode", ["SNAPSHOT", "FULL"])
def test_exact_run_claim_does_not_take_an_older_queued_run(app_engine: Engine, mode: str) -> None:
    older = _seed_run(app_engine)
    target = _seed_run(app_engine, tenant_id=older["tenant_id"], mode=mode)
    owner = str(uuid.uuid4())
    with app_engine.begin() as conn:
        claimed = _claim(conn, target, owner=owner)
        assert claimed is not None
        assert claimed["run_id"] == target["run_id"]
        assert claimed["status"] == "RUNNING"
        assert claimed["lease_owner_id"] == owner
        assert claimed["lease_fencing_token"] == 1
        assert claimed["lease_expires_at"] is not None
        assert claimed["last_heartbeat_at"] is not None
        _audit(conn, claimed, request_id=str(uuid.uuid4()))
        other = PostgresRunsRepository(conn, target["tenant_id"]).get(older["run_id"])
        assert other is not None
        assert (other["status"], other["lease_owner_id"], other["lease_fencing_token"]) == (
            "QUEUED",
            None,
            0,
        )


def test_exact_run_claim_and_api_identity_audit_share_transaction(app_engine: Engine) -> None:
    run = _seed_run(app_engine)
    request_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        claimed = _claim(conn, run, owner=str(uuid.uuid4()))
        assert claimed is not None
        event = _audit(conn, claimed, request_id=request_id)
    with app_engine.begin() as conn:
        set_tenant_local(conn, run["tenant_id"])
        row = conn.execute(
            text(
                "SELECT r.xmin::text,a.xmin::text,a.event FROM runs r "
                "JOIN audit_events a ON a.tenant_id=r.tenant_id "
                "WHERE r.run_id=CAST(:run AS uuid) AND a.event_id=CAST(:event AS uuid)"
            ),
            {"run": run["run_id"], "event": event["event_id"]},
        ).one()
    assert row[0] == row[1]
    assert row[2]["request"]["request_id"] == request_id
    assert row[2]["actor"]["actor_id"] == "idis-api-inline-executor"
    assert row[2]["actor"]["actor_id"] != "idis-pipeline-worker"
    assert row[2]["payload"]["safe"]["execution_origin"] == "API_INLINE"


def test_second_exact_claim_is_noop_without_second_audit(app_engine: Engine) -> None:
    run = _seed_run(app_engine)
    with app_engine.begin() as conn:
        first = _claim(conn, run, owner=str(uuid.uuid4()))
        assert first is not None
        _audit(conn, first, request_id=str(uuid.uuid4()))
    before = _rows(app_engine, run["tenant_id"])
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


def test_inline_claim_rejects_non_closed_snapshot_identity_without_mutation(
    app_engine: Engine,
) -> None:
    run = _seed_run(app_engine, mismatched_hash=True)
    before = _rows(app_engine, run["tenant_id"])
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


def test_inline_claim_rejects_proof_linked_historical_full_without_mutation(
    app_engine: Engine,
) -> None:
    run = _seed_run(app_engine, mode="FULL", historical_proof_linked=True)
    assert run["pipeline_contract_version"] == "full-v1"
    assert run["pipeline_resume_state"] == "NON_RESUMABLE"
    assert run["pipeline_resume_error_code"] == "LEGACY_RUN_RESUME_UNSAFE"
    assert run["strict_preflight_proof_id"] is not None
    before = _rows(app_engine, run["tenant_id"])
    assert len(before["strict_preflight_proofs"]) == len(before["runs"]) == 1
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


@pytest.mark.parametrize(
    "mode,version,resume_state,error_code",
    [
        ("SNAPSHOT", "full-v1", "NON_RESUMABLE", "LEGACY_RUN_RESUME_UNSAFE"),
        ("FULL", "snapshot-v1", "NON_RESUMABLE", "LEGACY_RUN_RESUME_UNSAFE"),
        ("SNAPSHOT", "unknown-version", "NON_RESUMABLE", "LEGACY_RUN_RESUME_UNSAFE"),
        ("SNAPSHOT", "snapshot-v1", "RESUMABLE", None),
        ("FULL", "full-v1", "HISTORICAL_TERMINAL", None),
    ],
)
def test_inline_claim_rejects_other_identity_tuples_without_mutation(
    app_engine: Engine, mode: str, version: str, resume_state: str, error_code: str | None
) -> None:
    run = _seed_run(
        app_engine,
        mode=mode,
        contract_identity={
            "pipeline_contract_version": version,
            "pipeline_contract_hash": (
                "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
            ),
            "pipeline_resume_state": resume_state,
            "pipeline_resume_error_code": error_code,
        },
    )
    before = _rows(app_engine, run["tenant_id"])
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


@pytest.mark.parametrize("status", ["CANCELLED", "FAILED", "SUCCEEDED"])
def test_inline_claim_does_not_reopen_terminal_rows(app_engine: Engine, status: str) -> None:
    run = _seed_run(app_engine)
    with app_engine.begin() as conn:
        PostgresRunsRepository(conn, run["tenant_id"]).update_status(run["run_id"], status=status)
    before = _rows(app_engine, run["tenant_id"])
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5])
def test_inline_claim_rejects_invalid_duration_before_mutation(
    app_engine: Engine, seconds: Any
) -> None:
    run = _seed_run(app_engine)
    before = _rows(app_engine, run["tenant_id"])
    with pytest.raises(ValueError, match="lease_seconds"), app_engine.begin() as conn:
        _claim(conn, run, owner=str(uuid.uuid4()), seconds=seconds)
    assert _rows(app_engine, run["tenant_id"]) == before


@pytest.mark.parametrize("owner", ["", "not-a-uuid"])
def test_inline_claim_rejects_invalid_owner_before_mutation(app_engine: Engine, owner: str) -> None:
    run = _seed_run(app_engine)
    before = _rows(app_engine, run["tenant_id"])
    with pytest.raises(ValueError, match="lease_owner_id"), app_engine.begin() as conn:
        _claim(conn, run, owner=owner)
    assert _rows(app_engine, run["tenant_id"]) == before


def test_foreign_tenant_exact_claim_has_no_write_or_audit(app_engine: Engine) -> None:
    run = _seed_run(app_engine)
    before = _rows(app_engine, run["tenant_id"])
    with app_engine.begin() as conn:
        assert _claim(conn, run, owner=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())) is None
    assert _rows(app_engine, run["tenant_id"]) == before


def test_audit_failure_after_actual_insert_rolls_back_claim_and_event(app_engine: Engine) -> None:
    run = _seed_run(app_engine)
    before = _rows(app_engine, run["tenant_id"])
    with (
        pytest.raises(AuditSinkError, match="C3_INJECTED_AFTER_DURABLE_AUDIT_INSERT"),
        app_engine.begin() as conn,
    ):
        claimed = _claim(conn, run, owner=str(uuid.uuid4()))
        assert claimed is not None
        _audit(conn, claimed, request_id=str(uuid.uuid4()))
        raise AuditSinkError("C3_INJECTED_AFTER_DURABLE_AUDIT_INSERT")
    assert _rows(app_engine, run["tenant_id"]) == before


def test_statement_clock_expiry_fences_completion_and_rolls_back_transaction(
    app_engine: Engine,
) -> None:
    run = _seed_run(app_engine)
    before = _rows(app_engine, run["tenant_id"])
    owner = str(uuid.uuid4())
    with (
        pytest.raises(WorkerLeaseFencedError, match="C3_EXPIRED_INLINE_LEASE"),
        app_engine.begin() as conn,
    ):
        claimed = _claim(conn, run, owner=owner, seconds=1)
        assert claimed is not None
        _audit(conn, claimed, request_id=str(uuid.uuid4()))
        conn.execute(text("SELECT pg_sleep(1.1)"))
        assert not PostgresRunsRepository(conn, run["tenant_id"]).try_complete_running(
            run["run_id"],
            status="FAILED",
            finished_at=None,
            lease_owner_id=owner,
            lease_fencing_token=int(claimed["lease_fencing_token"]),
        )
        raise WorkerLeaseFencedError("C3_EXPIRED_INLINE_LEASE")
    assert _rows(app_engine, run["tenant_id"]) == before


def test_two_real_app_transactions_have_one_exact_claim_winner(app_engine: Engine) -> None:
    run = _seed_run(app_engine)
    # Fail at the missing feature before starting threads, not at a barrier timeout.
    assert callable(getattr(PostgresRunsRepository, "claim_queued_run_for_execution", None)), (
        "PostgreSQL exact-row inline lease claim is not implemented"
    )
    barrier = Barrier(2)

    def contender() -> dict[str, Any] | None:
        with app_engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='3s'"))
            barrier.wait(timeout=5)
            claimed = _claim(conn, run, owner=str(uuid.uuid4()))
            if claimed is not None:
                _audit(conn, claimed, request_id=str(uuid.uuid4()))
            return claimed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
    final = _rows(app_engine, run["tenant_id"])
    assert len(final["runs"]) == 1
    assert len(final["audit_events"]) == 1
