"""Real-Postgres atomicity proof for the Slice100 worker claim boundary.

These tests use the application role and production ``PostgresRunsRepository`` /
``PostgresAuditSink`` path.  Without configured disposable PostgreSQL they skip,
unless ``IDIS_REQUIRE_POSTGRES=1`` makes the integration gate mandatory.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightBinding,
)
from idis.pipeline.worker import _lease_transition_audit_emitter

if TYPE_CHECKING:
    from sqlalchemy import Engine


ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"


@dataclass(frozen=True)
class _SeededStrictRun:
    tenant_id: str
    deal_id: str
    proof_id: str
    run_id: str


def _skip_or_fail_if_no_postgres() -> None:
    configured = os.environ.get(ADMIN_URL_ENV) and os.environ.get(APP_URL_ENV)
    if configured:
        return
    message = f"Task1 worker integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
    if os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1":
        pytest.fail(f"REQUIRED: {message} ({REQUIRE_POSTGRES_ENV}=1)")
    pytest.skip(message)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    _skip_or_fail_if_no_postgres()

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_admin_engine, reset_engines

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with get_admin_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    yield
    reset_engines()


def _binding(tenant_id: str, deal_id: str) -> StrictPreflightBinding:
    return StrictPreflightBinding(
        tenant_id=tenant_id,
        deal_id=deal_id,
        corpus_hash="1" * 64,
        strict_profile_generation="acceptance-v1",
        strict_profile_hmac="2" * 64,
        pipeline_contract_version="full-strict-v2",
        pipeline_contract_hash="3" * 64,
        expected_consumer_edge_rule_hash="4" * 64,
        static_decision_set_hash="5" * 64,
        methodology_version_hashes=("6" * 64,),
        promoted_pointer_hash="7" * 64,
        provider_receipt_set_hash="8" * 64,
        dependency_receipt_set_hash="9" * 64,
    )


def _seed_consumed_strict_run() -> _SeededStrictRun:
    from idis.persistence.db import begin_app_conn, get_admin_engine

    seeded = _SeededStrictRun(
        tenant_id=str(uuid.uuid4()),
        deal_id=str(uuid.uuid4()),
        proof_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
    )
    binding = _binding(seeded.tenant_id, seeded.deal_id)
    now = datetime.now(UTC)

    with get_admin_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name) "
                "VALUES (CAST(:tenant_id AS uuid), 'Slice100 Task1')"
            ),
            {"tenant_id": seeded.tenant_id},
        )
        conn.execute(
            text(
                "INSERT INTO deals (deal_id, tenant_id, name, created_at) VALUES "
                "(CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid), "
                "'Task1 Worker Claim', :now)"
            ),
            {"deal_id": seeded.deal_id, "tenant_id": seeded.tenant_id, "now": now},
        )
        conn.execute(
            text(
                """
                INSERT INTO strict_preflight_proofs (
                    proof_id, tenant_id, deal_id, corpus_hash,
                    strict_profile_generation, strict_profile_hmac,
                    pipeline_contract_version, pipeline_contract_hash,
                    expected_consumer_edge_rule_hash, static_decision_set_hash,
                    methodology_version_hashes, promoted_pointer_hash,
                    provider_receipt_set_hash, dependency_receipt_set_hash,
                    binding_hash, created_at, expires_at
                ) VALUES (
                    CAST(:proof_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), :corpus_hash,
                    :strict_profile_generation, :strict_profile_hmac,
                    :pipeline_contract_version, :pipeline_contract_hash,
                    :expected_consumer_edge_rule_hash, :static_decision_set_hash,
                    CAST(:methodology_version_hashes AS jsonb), :promoted_pointer_hash,
                    :provider_receipt_set_hash, :dependency_receipt_set_hash,
                    :binding_hash, :created_at, :expires_at
                )
                """
            ),
            {
                "proof_id": seeded.proof_id,
                "tenant_id": seeded.tenant_id,
                "deal_id": seeded.deal_id,
                "corpus_hash": binding.corpus_hash,
                "strict_profile_generation": binding.strict_profile_generation,
                "strict_profile_hmac": binding.strict_profile_hmac,
                "pipeline_contract_version": binding.pipeline_contract_version,
                "pipeline_contract_hash": binding.pipeline_contract_hash,
                "expected_consumer_edge_rule_hash": binding.expected_consumer_edge_rule_hash,
                "static_decision_set_hash": binding.static_decision_set_hash,
                "methodology_version_hashes": json.dumps(binding.methodology_version_hashes),
                "promoted_pointer_hash": binding.promoted_pointer_hash,
                "provider_receipt_set_hash": binding.provider_receipt_set_hash,
                "dependency_receipt_set_hash": binding.dependency_receipt_set_hash,
                "binding_hash": binding.digest(),
                "created_at": now,
                "expires_at": now + timedelta(hours=1),
            },
        )

    with begin_app_conn() as conn:
        repository = PostgresStrictPreflightProofsRepository(conn, seeded.tenant_id)
        repository.consume_and_create_run(
            proof_id=seeded.proof_id,
            expected_binding=binding,
            run_id=seeded.run_id,
            deal_id=seeded.deal_id,
            mode="FULL",
            idempotency_key=f"task1-{seeded.proof_id}",
            source={"type": "deal_documents", "document_ids": []},
            created_by_actor_id="originating-human",
            created_by_actor_type="HUMAN",
        )
        repository.require_consumed_for_run(
            proof_id=seeded.proof_id,
            run_id=seeded.run_id,
            deal_id=seeded.deal_id,
        )

    return seeded


def _formal_claim_row(admin_engine: Engine, seeded: _SeededStrictRun) -> Any:
    with admin_engine.begin() as conn:
        return conn.execute(
            text(
                """
                SELECT r.status,
                       r.xmin::text AS run_transaction_id,
                       a.xmin::text AS audit_transaction_id,
                       a.event #>> '{actor,actor_type}' AS actor_type,
                       a.event #>> '{actor,actor_id}' AS actor_id,
                       r.strict_preflight_proof_id::text AS proof_id,
                       a.event #>> '{payload,safe,lease_owner_id}' AS lease_owner_id,
                       a.event #>> '{payload,safe,new_fencing_token}' AS fencing_token,
                       a.event #>> '{payload,safe,lease_expires_at}' AS lease_expires_at
                FROM runs AS r
                JOIN audit_events AS a
                  ON a.tenant_id = r.tenant_id
                 AND a.request_id = CAST(r.run_id AS text)
                 AND a.event_type = 'worker.lease.claimed'
                WHERE r.tenant_id = CAST(:tenant_id AS uuid)
                  AND r.run_id = CAST(:run_id AS uuid)
                """
            ),
            {"tenant_id": seeded.tenant_id, "run_id": seeded.run_id},
        ).one_or_none()


def _formal_claim_count(admin_engine: Engine, seeded: _SeededStrictRun) -> int:
    with admin_engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND request_id = :run_id AND event_type = 'worker.lease.claimed'"
                ),
                {"tenant_id": seeded.tenant_id, "run_id": seeded.run_id},
            ).scalar_one()
        )


def _run_status(admin_engine: Engine, seeded: _SeededStrictRun) -> str:
    with admin_engine.begin() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT status FROM runs WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND run_id = CAST(:run_id AS uuid)"
                ),
                {"tenant_id": seeded.tenant_id, "run_id": seeded.run_id},
            ).scalar_one()
        )


def test_claim_transition_and_formal_audit_commit_atomically(
    _pg_schema: None,
) -> None:
    from idis.persistence.db import get_admin_engine, get_app_engine, set_tenant_local

    seeded = _seed_consumed_strict_run()
    admin_engine = get_admin_engine()
    lease_owner_id = str(uuid.uuid4())

    with get_app_engine().connect() as conn:
        set_tenant_local(conn, seeded.tenant_id)
        audit_sink = PostgresAuditSink()
        repository = PostgresRunsRepository(conn, seeded.tenant_id)
        claimed = repository.claim_queued_runs(
            limit=1,
            lease_owner_id=lease_owner_id,
            lease_seconds=60,
            lease_audit_emitter=_lease_transition_audit_emitter(
                audit_sink=audit_sink,
                tenant_id=seeded.tenant_id,
            ),
        )
        assert len(claimed) == 1
        assert claimed[0]["run_id"] == seeded.run_id
        assert claimed[0]["lease_owner_id"] == lease_owner_id
        assert claimed[0]["lease_fencing_token"] == 1
        assert claimed[0]["lease_expires_at"] is not None
        conn.commit()

    assert _run_status(admin_engine, seeded) == "RUNNING"
    assert _formal_claim_count(admin_engine, seeded) == 1
    row = _formal_claim_row(admin_engine, seeded)
    assert row is not None
    assert row.status == "RUNNING"
    assert row.run_transaction_id == row.audit_transaction_id
    assert row.actor_type == "SERVICE"
    assert row.actor_id == "idis-pipeline-worker"
    assert row.proof_id == seeded.proof_id
    assert row.lease_owner_id == lease_owner_id
    assert int(row.fencing_token) == 1
    assert row.lease_expires_at

    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE runs SET status = 'CANCELLED', lease_owner_id = NULL, "
                "lease_expires_at = NULL WHERE run_id = CAST(:run_id AS uuid)"
            ),
            {"run_id": seeded.run_id},
        )


def test_claim_audit_failure_rolls_back_transition_and_never_orchestrates(
    _pg_schema: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.persistence.db import get_admin_engine, get_app_engine, set_tenant_local

    seeded = _seed_consumed_strict_run()
    admin_engine = get_admin_engine()

    def fail_formal_claim(
        _self: PostgresAuditSink,
        _conn: Any,
        _event: dict[str, Any],
    ) -> None:
        raise AuditSinkError("INJECTED_CLAIM_AUDIT_FAILURE")

    monkeypatch.setattr(PostgresAuditSink, "emit_in_tx", fail_formal_claim)

    with (
        pytest.raises(AuditSinkError, match="INJECTED_CLAIM_AUDIT_FAILURE"),
        get_app_engine().begin() as conn,
    ):
        # Let the exception escape the real SQLAlchemy transaction context so it
        # rolls back the QUEUED -> RUNNING update just as the worker's handler does.
        set_tenant_local(conn, seeded.tenant_id)
        audit_sink = PostgresAuditSink()
        repository = PostgresRunsRepository(conn, seeded.tenant_id)
        repository.claim_queued_runs(
            limit=1,
            lease_owner_id=str(uuid.uuid4()),
            lease_seconds=60,
            lease_audit_emitter=_lease_transition_audit_emitter(
                audit_sink=audit_sink,
                tenant_id=seeded.tenant_id,
            ),
        )

    assert _run_status(admin_engine, seeded) == "QUEUED"
    assert _formal_claim_count(admin_engine, seeded) == 0
