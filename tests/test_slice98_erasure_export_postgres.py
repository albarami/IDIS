"""Slice98 Task 8 (durable) - erasure requests, full-deal removal, export against real Postgres.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the hermetic twins cannot:

- Migration 0030: ``erasure_requests`` with guarded+forced RLS and - the locked amendment - NO
  foreign key to ``deals`` (the request row must outlive the deal it erased).
- The installed Task-7 runner covers every live deal-scoped relation except the retained request,
  while the historical executor's target inventory remains internally closed.
- Public app-credential erasure and direct legacy deletion both fail closed with zero database
  mutation once the v19 dedicated authority is installed.
- The historical executor's read-only hold scan still observes a real ``legal_holds`` row and its
  lift without becoming a production erasure path.
- Request durability across store instances; the Postgres export collector returns the seeded
  inventory. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idis.audit.sink import InMemoryAuditSink
from idis.compliance import erasure_postgres as legacy_erasure_postgres
from idis.compliance.erasure import (
    ErasureStatus,
    PostgresErasureRequestStore,
    execute_erasure,
    request_erasure,
    reset_erasure_request_store,
)
from idis.compliance.erasure_postgres import (
    DEAL_SCOPED_TABLE_CLASSIFICATION,
    PostgresErasureExecutor,
    PostgresExportCollector,
)
from idis.compliance.retention import (
    HoldTarget,
    PostgresLegalHoldRegistry,
    apply_hold,
    lift_hold,
    reset_legal_hold_registry,
)
from idis.persistence.repositories.runs import RunAlreadyActiveError
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightBinding,
    StrictPreflightProofError,
)

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_REASON = "Data subject erasure request under contract clause 9.2."
_NOW = datetime.now(UTC)
_TASK7_DELETE_TARGET_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.([a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)

_PROOF_INSERT_SQL = (
    "INSERT INTO strict_preflight_proofs ("
    "proof_id, tenant_id, deal_id, corpus_hash, strict_profile_generation, "
    "strict_profile_hmac, pipeline_contract_version, pipeline_contract_hash, "
    "expected_consumer_edge_rule_hash, static_decision_set_hash, "
    "methodology_version_hashes, promoted_pointer_hash, provider_receipt_set_hash, "
    "dependency_receipt_set_hash, binding_hash, created_at, expires_at) VALUES ("
    "CAST(:p AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), :h1, 'acceptance-v1', :h2, "
    "'full-strict-v2', :h3, :h4, :h5, CAST(:methods AS jsonb), :h6, :h7, :h8, :h9, "
    ":now, :expires)"
)


def _strict_binding(tenant_id: str, deal_id: str) -> StrictPreflightBinding:
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


def _proof_insert_params(tenant_id: str, deal_id: str, proof_id: str) -> dict[str, object]:
    binding = _strict_binding(tenant_id, deal_id)
    return {
        "p": proof_id,
        "t": tenant_id,
        "d": deal_id,
        "h1": binding.corpus_hash,
        "h2": binding.strict_profile_hmac,
        "h3": binding.pipeline_contract_hash,
        "h4": binding.expected_consumer_edge_rule_hash,
        "h5": binding.static_decision_set_hash,
        "methods": json.dumps(binding.methodology_version_hashes),
        "h6": binding.promoted_pointer_hash,
        "h7": binding.provider_receipt_set_hash,
        "h8": binding.dependency_receipt_set_hash,
        "h9": binding.digest(),
        "now": _NOW,
        "expires": _NOW + timedelta(hours=1),
    }


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres erasure integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0030's erasure_requests table exists."""
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


_CLEAN_TABLES = (
    "erasure_requests",
    "legal_holds",
    "strict_preflight_proofs",
    "document_spans",
    "sanads",
    "defects",
    "evidence_items",
    "calc_sanads",
    "run_steps",
    "human_gate_actions",
    "debate_sessions",
    "deliverables",
    "data_room_package_files",
    "data_room_packages",
    "claims",
    "documents",
    "document_artifacts",
    "runs",
    "human_gates",
    "overrides",
    "deterministic_calculations",
    "vector_embeddings",
    "validated_evidence_packages",
    "evidence_trust_findings",
    "muhasabah_records",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "deal_assignments",
    "break_glass_grants",
    "deals",
    "audit_events",
)


@pytest.fixture
def pg_clean(_pg_schema: None) -> Generator[None, None, None]:
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_CLEAN_TABLES)} CASCADE"))

    reset_erasure_request_store()
    reset_legal_hold_registry()
    _truncate()
    yield
    _truncate()
    reset_erasure_request_store()
    reset_legal_hold_registry()


def _ctx(tenant_id: str = _TENANT_A) -> object:
    from idis.api.auth import TenantContext

    return TenantContext(
        tenant_id=tenant_id,
        actor_id="pg-erasure-admin",
        name="PG Erasure Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


def _admin_exec(sql: str, params: dict | None = None) -> None:
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        conn.execute(text(sql), params or {})


def _seed_bare_deal(tenant_id: str) -> str:
    deal_id = str(uuid.uuid4())
    _admin_exec(
        "INSERT INTO tenants (tenant_id, name) VALUES (CAST(:t AS uuid), 'T') "
        "ON CONFLICT (tenant_id) DO NOTHING",
        {"t": tenant_id},
    )
    _admin_exec(
        "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
        "VALUES (CAST(:d AS uuid), CAST(:t AS uuid), 'Proof Deal', :now)",
        {"d": deal_id, "t": tenant_id, "now": _NOW},
    )
    return deal_id


def _consume_bound_proof(
    tenant_id: str,
    deal_id: str,
    proof_id: str,
    run_id: str,
) -> dict[str, Any]:
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        repo = PostgresStrictPreflightProofsRepository(conn, tenant_id)
        run = repo.consume_and_create_run(
            proof_id=proof_id,
            expected_binding=_strict_binding(tenant_id, deal_id),
            run_id=run_id,
            deal_id=deal_id,
            mode="FULL",
            idempotency_key=f"proof-{proof_id}",
            source={"type": "deal_documents", "document_ids": []},
            created_by_actor_id="task1-pg-test",
            created_by_actor_type="SERVICE",
        )
        repo.require_consumed_for_run(proof_id=proof_id, run_id=run_id, deal_id=deal_id)
        return run


def _admin_proof_state(proof_id: str) -> tuple[datetime | None, str | None]:
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT consumed_at, consumed_by_run_id FROM strict_preflight_proofs "
                "WHERE proof_id = CAST(:p AS uuid)"
            ),
            {"p": proof_id},
        ).fetchone()
    assert row is not None
    return row.consumed_at, (
        str(row.consumed_by_run_id) if row.consumed_by_run_id is not None else None
    )


def _admin_count(table: str, tenant_id: str, deal_id: str | None = None) -> int:
    from idis.persistence.db import get_admin_engine

    where = "tenant_id = CAST(:tenant_id AS uuid)"
    params: dict = {"tenant_id": tenant_id}
    if deal_id is not None:
        where += " AND deal_id = CAST(:deal_id AS uuid)"
        params["deal_id"] = deal_id
    with get_admin_engine().begin() as conn:
        row = conn.execute(
            text(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}"), params
        ).fetchone()
    assert row is not None
    return int(row.n)


def _app_graph_snapshot(tenant_id: str, deal_id: str) -> dict[str, int]:
    """Read the seeded graph through the RLS-bound application credential."""

    from idis.persistence.db import begin_app_conn, set_tenant_local

    params = {"tenant_id": tenant_id, "deal_id": deal_id}
    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_id)
        row = (
            conn.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM deals
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS deals,
                      (SELECT COUNT(*) FROM document_artifacts
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS document_artifacts,
                      (SELECT COUNT(*) FROM documents
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS documents,
                      (SELECT COUNT(*) FROM claims
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS claims,
                      (SELECT COUNT(*) FROM strict_preflight_proofs
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS strict_preflight_proofs,
                      (SELECT COUNT(*) FROM runs
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS runs,
                      (SELECT COUNT(*) FROM run_steps s
                       JOIN runs r ON r.run_id = s.run_id AND r.tenant_id = s.tenant_id
                       WHERE r.tenant_id = CAST(:tenant_id AS uuid)
                         AND r.deal_id = CAST(:deal_id AS uuid)) AS run_steps,
                      (SELECT COUNT(*) FROM human_gates
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS human_gates,
                      (SELECT COUNT(*) FROM deterministic_calculations
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS deterministic_calculations,
                      (SELECT COUNT(*) FROM deal_assignments
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS deal_assignments,
                      (SELECT COUNT(*) FROM erasure_requests
                       WHERE tenant_id = CAST(:tenant_id AS uuid)
                         AND deal_id = CAST(:deal_id AS uuid)) AS erasure_requests,
                      (SELECT COUNT(*) FROM audit_events
                       WHERE tenant_id = CAST(:tenant_id AS uuid)) AS audit_events
                    """
                ),
                params,
            )
            .mappings()
            .one()
        )
    return {key: int(value) for key, value in row.items()}


def _seed_deal_graph(tenant_id: str) -> str:
    """Seed one deal with a row in each major deal-scoped table (+ children)."""
    deal_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    proof_id = str(uuid.uuid4())
    gate_id = str(uuid.uuid4())
    calc_id = str(uuid.uuid4())

    _admin_exec(
        "INSERT INTO tenants (tenant_id, name) VALUES (CAST(:t AS uuid), 'T') "
        "ON CONFLICT (tenant_id) DO NOTHING",
        {"t": tenant_id},
    )
    _admin_exec(
        "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
        "VALUES (CAST(:d AS uuid), CAST(:t AS uuid), 'Erasure Deal', :now)",
        {"d": deal_id, "t": tenant_id, "now": _NOW},
    )
    _admin_exec(
        "INSERT INTO document_artifacts (doc_id, tenant_id, deal_id, doc_type, title, "
        "source_system, version_id, uri) VALUES (CAST(:doc AS uuid), CAST(:t AS uuid), "
        "CAST(:d AS uuid), 'PITCH_DECK', 'Deck', 'upload', 'v1', :uri)",
        {"doc": doc_id, "t": tenant_id, "d": deal_id, "uri": f"idis://erasure/{deal_id}.pdf"},
    )
    _admin_exec(
        "INSERT INTO documents (document_id, tenant_id, deal_id, doc_id, doc_type) VALUES "
        "(CAST(:did AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), CAST(:doc AS uuid), 'PDF')",
        {"did": document_id, "t": tenant_id, "d": deal_id, "doc": doc_id},
    )
    _admin_exec(
        "INSERT INTO claims (claim_id, tenant_id, deal_id, claim_class, claim_text, created_at) "
        "VALUES (CAST(:c AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), 'FINANCIAL', 'rev', :now)",
        {"c": claim_id, "t": tenant_id, "d": deal_id, "now": _NOW},
    )
    _admin_exec(_PROOF_INSERT_SQL, _proof_insert_params(tenant_id, deal_id, proof_id))
    _admin_exec(
        "INSERT INTO runs (run_id, tenant_id, deal_id, mode, strict_preflight_proof_id) VALUES "
        "(CAST(:r AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), 'FULL', CAST(:p AS uuid))",
        {"r": run_id, "t": tenant_id, "d": deal_id, "p": proof_id},
    )
    _admin_exec(
        "UPDATE strict_preflight_proofs SET consumed_at = :now, "
        "consumed_by_run_id = CAST(:r AS uuid) WHERE tenant_id = CAST(:t AS uuid) "
        "AND proof_id = CAST(:p AS uuid)",
        {"now": _NOW, "r": run_id, "t": tenant_id, "p": proof_id},
    )
    _admin_exec(
        "INSERT INTO run_steps (step_id, tenant_id, run_id, step_name, step_order) "
        "VALUES (CAST(:s AS uuid), CAST(:t AS uuid), CAST(:r AS uuid), 'EXTRACT', 1)",
        {"s": str(uuid.uuid4()), "t": tenant_id, "r": run_id},
    )
    _admin_exec(
        "INSERT INTO human_gates (gate_id, tenant_id, deal_id, gate_type) VALUES "
        "(CAST(:g AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), 'IC_READY')",
        {"g": gate_id, "t": tenant_id, "d": deal_id},
    )
    _admin_exec(
        "INSERT INTO deterministic_calculations (calc_id, tenant_id, deal_id, calc_type, "
        "inputs, formula_hash, code_version, output, reproducibility_hash) VALUES "
        "(CAST(:c AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), 'ARR', '{}'::jsonb, 'fh', "
        "'v1', '{}'::jsonb, 'rh')",
        {"c": calc_id, "t": tenant_id, "d": deal_id},
    )
    _admin_exec(
        "INSERT INTO deal_assignments (tenant_id, deal_id, assignee_type, assignee_id) VALUES "
        "(CAST(:t AS uuid), CAST(:d AS uuid), 'ACTOR', 'analyst-1')",
        {"t": tenant_id, "d": deal_id},
    )
    _admin_exec(
        "INSERT INTO audit_events (event_id, tenant_id, occurred_at, event_type, event) "
        "VALUES (CAST(:e AS uuid), CAST(:t AS uuid), :now, 'deal.created', "
        "CAST(:event AS jsonb))",
        {
            "e": str(uuid.uuid4()),
            "t": tenant_id,
            "now": _NOW,
            "event": json.dumps({"summary": f"deal {deal_id} created"}),
        },
    )
    return deal_id


def test_0030_schema_rls_and_no_fk_to_deals(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        columns = {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'erasure_requests'"
                )
            )
        }
        assert {
            "tenant_id",
            "request_id",
            "deal_id",
            "status",
            "requested_by",
            "requested_at",
            "reason_hash",
            "reason_length",
            "executed_by",
            "executed_at",
            "counts",
        } <= columns
        assert "reason" not in columns  # plaintext reasons are NEVER persisted

        rls = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'erasure_requests'"
            )
        ).fetchone()
        assert rls is not None and rls.relrowsecurity and rls.relforcerowsecurity

        # Amendment pin: NO foreign key from erasure_requests to anything - the request row
        # is durable evidence that must outlive the erased deal.
        fk_count = conn.execute(
            text(
                "SELECT COUNT(*) AS n FROM information_schema.table_constraints "
                "WHERE table_name = 'erasure_requests' AND constraint_type = 'FOREIGN KEY'"
            )
        ).fetchone()
        assert fk_count is not None and int(fk_count.n) == 0


def test_0031_proof_schema_uses_lifecycle_guard_without_reverse_fks(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        rls = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'strict_preflight_proofs'"
            )
        ).fetchone()
        assert rls is not None and rls.relrowsecurity and rls.relforcerowsecurity

        reverse_fk_count = conn.execute(
            text(
                "SELECT COUNT(*) AS n FROM information_schema.table_constraints "
                "WHERE table_name = 'strict_preflight_proofs' "
                "AND constraint_type = 'FOREIGN KEY'"
            )
        ).fetchone()
        assert reverse_fk_count is not None and int(reverse_fk_count.n) == 0

        run_fk_count = conn.execute(
            text(
                "SELECT COUNT(*) AS n FROM information_schema.table_constraints "
                "WHERE table_name = 'runs' AND constraint_type = 'FOREIGN KEY' "
                "AND constraint_name = 'fk_runs_strict_preflight_proof_tenant'"
            )
        ).fetchone()
        assert run_fk_count is not None and int(run_fk_count.n) == 1

        trigger_events = {
            row.event_manipulation
            for row in conn.execute(
                text(
                    "SELECT event_manipulation FROM information_schema.triggers "
                    "WHERE event_object_table = 'strict_preflight_proofs' "
                    "AND trigger_name = 'strict_preflight_proofs_immutability'"
                )
            )
        }
        assert trigger_events == {"INSERT", "UPDATE", "DELETE"}


def test_installed_task7_runner_covers_live_deal_scope_and_legacy_targets(
    pg_clean: None,
) -> None:
    """The installed v19 runner owns the live census; the legacy inventory remains closed."""

    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        function_definition = conn.execute(
            text(
                "SELECT pg_get_functiondef("
                "'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)'::regprocedure)"
            )
        ).scalar_one()
        app_can_execute = conn.execute(
            text(
                "SELECT has_function_privilege("
                "current_user, "
                "'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)', "
                "'EXECUTE')"
            )
        ).scalar_one()
        live_tables = {
            row.table_name
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE column_name = 'deal_id' AND table_schema = 'public' "
                    "ORDER BY table_name"
                )
            )
        }

    installed_order = tuple(_TASK7_DELETE_TARGET_RE.findall(str(function_definition)))
    installed_targets = set(installed_order)
    assert installed_order
    assert len(installed_order) == len(installed_targets)
    assert app_can_execute is False

    child_targets = {relation for relation, _sql in legacy_erasure_postgres._CHILD_TABLE_DELETES}
    legacy_targets = (
        set(legacy_erasure_postgres._DIRECT_DELETE_ORDER)
        | child_targets
        | {"deals", "strict_preflight_proofs"}
    )
    legacy_erased = {
        relation
        for relation, disposition in DEAL_SCOPED_TABLE_CLASSIFICATION.items()
        if disposition == "erased"
    }
    legacy_retained = {
        relation
        for relation, disposition in DEAL_SCOPED_TABLE_CLASSIFICATION.items()
        if disposition == "retained"
    }

    assert legacy_retained == {"erasure_requests"}
    assert legacy_targets == legacy_erased | child_targets
    assert set(DEAL_SCOPED_TABLE_CLASSIFICATION) == legacy_erased | legacy_retained
    assert legacy_targets < installed_targets
    assert installed_targets & live_tables == live_tables - legacy_retained


def test_public_app_credential_erasure_is_unreachable_with_zero_mutation(
    pg_clean: None,
) -> None:
    deal_a = _seed_deal_graph(_TENANT_A)
    deal_b = _seed_deal_graph(_TENANT_B)

    sink = InMemoryAuditSink()
    request = request_erasure(_ctx(), deal_a, _REASON, sink, PostgresErasureRequestStore())
    before_a = _app_graph_snapshot(_TENANT_A, deal_a)
    before_b = _app_graph_snapshot(_TENANT_B, deal_b)
    before_sink = sink.events

    with pytest.raises(
        RuntimeError,
        match="^TASK7_ERASURE_API_COMPOSITION_REQUIRED$",
    ):
        execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=PostgresErasureExecutor(),
            hold_checker=lambda _tenant_id, _deal_id: None,
            store=PostgresErasureRequestStore(),
        )

    assert _app_graph_snapshot(_TENANT_A, deal_a) == before_a
    assert _app_graph_snapshot(_TENANT_B, deal_b) == before_b
    assert sink.events == before_sink
    stored = PostgresErasureRequestStore().get(_TENANT_A, request.request_id)
    assert stored is not None
    assert stored.status == ErasureStatus.REQUESTED
    assert stored.executed_at is None
    assert stored.executed_by is None
    assert stored.counts == {}


def test_app_role_proof_consumption_is_atomic_durable_and_one_use(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    deal_id = _seed_bare_deal(_TENANT_A)
    proof_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    _admin_exec(_PROOF_INSERT_SQL, _proof_insert_params(_TENANT_A, deal_id, proof_id))

    run = _consume_bound_proof(_TENANT_A, deal_id, proof_id, run_id)
    assert run["run_id"] == run_id
    assert run["strict_preflight_proof_id"] == proof_id
    consumed_at, consumed_by = _admin_proof_state(proof_id)
    assert consumed_at is not None and consumed_by == run_id

    # A fresh application-role transaction proves the reciprocal link survived commit.
    with begin_app_conn() as conn:
        repo = PostgresStrictPreflightProofsRepository(conn, _TENANT_A)
        repo.require_consumed_for_run(proof_id=proof_id, run_id=run_id, deal_id=deal_id)

    with begin_app_conn() as conn:
        repo = PostgresStrictPreflightProofsRepository(conn, _TENANT_A)
        with pytest.raises(StrictPreflightProofError) as exc_info:
            repo.consume_and_create_run(
                proof_id=proof_id,
                expected_binding=_strict_binding(_TENANT_A, deal_id),
                run_id=str(uuid.uuid4()),
                deal_id=deal_id,
                mode="FULL",
                idempotency_key="proof-reuse",
                source={"type": "deal_documents", "document_ids": []},
                created_by_actor_id="task1-pg-test",
                created_by_actor_type="SERVICE",
            )
    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_ALREADY_CONSUMED"
    assert _admin_count("runs", _TENANT_A, deal_id) == 1


def test_app_role_cross_tenant_proof_is_invisible(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    deal_a = _seed_bare_deal(_TENANT_A)
    _seed_bare_deal(_TENANT_B)
    proof_id = str(uuid.uuid4())
    _admin_exec(_PROOF_INSERT_SQL, _proof_insert_params(_TENANT_A, deal_a, proof_id))

    with begin_app_conn() as conn:
        repo = PostgresStrictPreflightProofsRepository(conn, _TENANT_B)
        with pytest.raises(StrictPreflightProofError) as exc_info:
            repo.consume_and_create_run(
                proof_id=proof_id,
                expected_binding=_strict_binding(_TENANT_B, deal_a),
                run_id=str(uuid.uuid4()),
                deal_id=deal_a,
                mode="FULL",
                idempotency_key="cross-tenant-proof",
                source={"type": "deal_documents", "document_ids": []},
                created_by_actor_id="task1-pg-test",
                created_by_actor_type="SERVICE",
            )

    assert exc_info.value.code == "STRICT_PREFLIGHT_PROOF_INVALID"
    assert _admin_count("runs", _TENANT_B, deal_a) == 0
    assert _admin_proof_state(proof_id) == (None, None)


def test_active_run_conflict_leaves_second_proof_unconsumed(pg_clean: None) -> None:
    from idis.persistence.db import begin_app_conn

    deal_id = _seed_bare_deal(_TENANT_A)
    first_proof = str(uuid.uuid4())
    second_proof = str(uuid.uuid4())
    _admin_exec(_PROOF_INSERT_SQL, _proof_insert_params(_TENANT_A, deal_id, first_proof))
    _admin_exec(_PROOF_INSERT_SQL, _proof_insert_params(_TENANT_A, deal_id, second_proof))
    _consume_bound_proof(_TENANT_A, deal_id, first_proof, str(uuid.uuid4()))

    with begin_app_conn() as conn:
        repo = PostgresStrictPreflightProofsRepository(conn, _TENANT_A)
        with pytest.raises(RunAlreadyActiveError):
            repo.consume_and_create_run(
                proof_id=second_proof,
                expected_binding=_strict_binding(_TENANT_A, deal_id),
                run_id=str(uuid.uuid4()),
                deal_id=deal_id,
                mode="FULL",
                idempotency_key="second-active-run",
                source={"type": "deal_documents", "document_ids": []},
                created_by_actor_id="task1-pg-test",
                created_by_actor_type="SERVICE",
            )

    assert _admin_proof_state(second_proof) == (None, None)
    assert _admin_count("runs", _TENANT_A, deal_id) == 1


def test_live_deal_proof_delete_is_rejected_without_partial_deletion(pg_clean: None) -> None:
    from sqlalchemy.exc import DBAPIError

    deal_a = _seed_deal_graph(_TENANT_A)

    with pytest.raises(DBAPIError, match="cannot be deleted while its same-tenant deal exists"):
        _admin_exec(
            "DELETE FROM strict_preflight_proofs WHERE tenant_id = CAST(:t AS uuid) "
            "AND deal_id = CAST(:d AS uuid)",
            {"t": _TENANT_A, "d": deal_a},
        )

    assert _admin_count("deals", _TENANT_A, deal_a) == 1
    assert _admin_count("runs", _TENANT_A, deal_a) == 1
    assert _admin_count("strict_preflight_proofs", _TENANT_A, deal_a) == 1


@pytest.mark.parametrize(
    "invalid_methods",
    [
        json.dumps(["not-a-sha256"]),
        json.dumps([None]),
        f"[{'1' * 64}]",  # hash-shaped JSON number is not a JSON string
    ],
)
def test_methodology_hash_array_rejects_non_hash_elements(
    pg_clean: None,
    invalid_methods: str,
) -> None:
    from sqlalchemy.exc import IntegrityError

    deal_id = str(uuid.uuid4())
    _admin_exec(
        "INSERT INTO tenants (tenant_id, name) VALUES (CAST(:t AS uuid), 'T') "
        "ON CONFLICT (tenant_id) DO NOTHING",
        {"t": _TENANT_A},
    )
    _admin_exec(
        "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
        "VALUES (CAST(:d AS uuid), CAST(:t AS uuid), 'Hash Check Deal', :now)",
        {"d": deal_id, "t": _TENANT_A, "now": _NOW},
    )
    params = _proof_insert_params(_TENANT_A, deal_id, str(uuid.uuid4()))
    params["methods"] = invalid_methods

    with pytest.raises(IntegrityError):
        _admin_exec(_PROOF_INSERT_SQL, params)

    assert _admin_count("strict_preflight_proofs", _TENANT_A, deal_id) == 0


def test_proof_insert_locks_deal_against_concurrent_erasure(pg_clean: None) -> None:
    from sqlalchemy.exc import DBAPIError

    from idis.persistence.db import get_admin_engine

    deal_id = str(uuid.uuid4())
    proof_id = str(uuid.uuid4())
    _admin_exec(
        "INSERT INTO tenants (tenant_id, name) VALUES (CAST(:t AS uuid), 'T') "
        "ON CONFLICT (tenant_id) DO NOTHING",
        {"t": _TENANT_A},
    )
    _admin_exec(
        "INSERT INTO deals (deal_id, tenant_id, name, created_at) "
        "VALUES (CAST(:d AS uuid), CAST(:t AS uuid), 'Lock Deal', :now)",
        {"d": deal_id, "t": _TENANT_A, "now": _NOW},
    )

    engine = get_admin_engine()
    with engine.connect() as issuing_conn, engine.connect() as erasing_conn:
        issuing_tx = issuing_conn.begin()
        issuing_conn.execute(
            text(_PROOF_INSERT_SQL),
            _proof_insert_params(_TENANT_A, deal_id, proof_id),
        )

        erasing_tx = erasing_conn.begin()
        erasing_conn.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(DBAPIError, match="lock timeout|canceling statement"):
            erasing_conn.execute(
                text(
                    "DELETE FROM deals WHERE tenant_id = CAST(:t AS uuid) "
                    "AND deal_id = CAST(:d AS uuid)"
                ),
                {"t": _TENANT_A, "d": deal_id},
            )
        erasing_tx.rollback()
        issuing_tx.rollback()

    assert _admin_count("deals", _TENANT_A, deal_id) == 1
    assert _admin_count("strict_preflight_proofs", _TENANT_A, deal_id) == 0


def test_direct_legacy_app_executor_is_fenced_with_zero_database_mutation(
    pg_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.services.ingestion import defaults as ingestion_defaults
    from idis.storage.errors import ObjectNotFoundError

    class _AbsentObjectStore:
        def delete(self, *_args: object, **_kwargs: object) -> None:
            raise ObjectNotFoundError()

    deal_a = _seed_deal_graph(_TENANT_A)
    before = _app_graph_snapshot(_TENANT_A, deal_a)
    monkeypatch.setattr(
        ingestion_defaults,
        "build_default_compliance_store",
        lambda: _AbsentObjectStore(),
    )

    with pytest.raises(DBAPIError) as caught:
        PostgresErasureExecutor().erase_deal(_TENANT_A, deal_a)

    original = caught.value.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    assert sqlstate == "23514"
    assert "STEP_REHYDRATION_FAILED" in str(original)
    assert _app_graph_snapshot(_TENANT_A, deal_a) == before


def test_legacy_hold_scan_observes_hold_and_lift_without_erasure(pg_clean: None) -> None:
    deal_a = _seed_deal_graph(_TENANT_A)
    sink = InMemoryAuditSink()
    hold = apply_hold(_ctx(), HoldTarget.DEAL, deal_a, _REASON, sink, PostgresLegalHoldRegistry())
    before = _app_graph_snapshot(_TENANT_A, deal_a)
    executor = PostgresErasureExecutor()

    from idis.api.errors import IdisHttpError

    with pytest.raises(IdisHttpError) as exc_info:
        executor.scan_holds(_TENANT_A, deal_a)
    assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"
    assert _app_graph_snapshot(_TENANT_A, deal_a) == before

    lift_hold(_ctx(), hold.hold_id, sink, PostgresLegalHoldRegistry())
    executor.scan_holds(_TENANT_A, deal_a)
    after_lift = _app_graph_snapshot(_TENANT_A, deal_a)
    assert {key: value for key, value in after_lift.items() if key != "audit_events"} == {
        key: value for key, value in before.items() if key != "audit_events"
    }
    assert after_lift["audit_events"] == before["audit_events"] + 1


def test_request_durable_across_store_instances(pg_clean: None) -> None:
    deal_a = _seed_deal_graph(_TENANT_A)
    request = request_erasure(
        _ctx(), deal_a, _REASON, InMemoryAuditSink(), PostgresErasureRequestStore()
    )
    # a second instance stands in for a fresh replica/restart
    loaded = PostgresErasureRequestStore().get(_TENANT_A, request.request_id)
    assert loaded is not None
    assert loaded.status == ErasureStatus.REQUESTED
    assert loaded.reason_hash == request.reason_hash
    assert PostgresErasureRequestStore().get(_TENANT_B, request.request_id) is None  # RLS


def test_postgres_export_collector_returns_seeded_inventory(pg_clean: None) -> None:
    deal_a = _seed_deal_graph(_TENANT_A)
    _seed_deal_graph(_TENANT_B)

    inventory = PostgresExportCollector().collect(_TENANT_A)
    assert [d["deal_id"] for d in inventory["deals"]] == [deal_a]
    assert len(inventory["documents"]) == 1
    assert inventory["documents"][0]["deal_id"] == deal_a
    assert len(inventory["claims"]) == 1
    payload = json.dumps(inventory)
    assert _TENANT_B not in payload  # strictly tenant-scoped
