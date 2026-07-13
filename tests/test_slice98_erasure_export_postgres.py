"""Slice98 Task 8 (durable) - erasure requests, full-deal removal, export against real Postgres.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the hermetic twins cannot:

- Migration 0030: ``erasure_requests`` with guarded+forced RLS and - the locked amendment - NO
  foreign key to ``deals`` (the request row must outlive the deal it erased).
- The CLASSIFICATION TRIPWIRE: every table carrying a deal_id column in the live schema must be
  classified in ``DEAL_SCOPED_TABLE_CLASSIFICATION`` (erased / retained / out_of_scope). A future
  deal-scoped table added without classification FAILS this test.
- Full-deal removal across the classified surface: seeded rows for tenant A's deal disappear
  from EVERY erased table (deals row included) while tenant B's identical data and the
  audit_events rows survive, verified through a separate admin connection.
- A real ``legal_holds`` row (DEAL target) blocks execution entirely until lifted.
- Request durability across store instances; the Postgres export collector returns the seeded
  inventory. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from idis.audit.sink import InMemoryAuditSink
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

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_REASON = "Data subject erasure request under contract clause 9.2."
_NOW = datetime.now(UTC)


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


def _seed_deal_graph(tenant_id: str) -> str:
    """Seed one deal with a row in each major deal-scoped table (+ children)."""
    deal_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
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
    _admin_exec(
        "INSERT INTO runs (run_id, tenant_id, deal_id, mode) VALUES "
        "(CAST(:r AS uuid), CAST(:t AS uuid), CAST(:d AS uuid), 'SNAPSHOT')",
        {"r": run_id, "t": tenant_id, "d": deal_id},
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


def test_every_deal_scoped_table_is_classified_tripwire(pg_clean: None) -> None:
    """A new deal-scoped table MUST be classified (erased/retained/out_of_scope) or this fails."""
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        live_tables = {
            row.table_name
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE column_name = 'deal_id' AND table_schema = 'public'"
                )
            )
        }
    classified = set(DEAL_SCOPED_TABLE_CLASSIFICATION)
    unclassified = live_tables - classified
    assert unclassified == set(), (
        f"deal-scoped tables missing an erasure classification: {sorted(unclassified)} - "
        "add each to DEAL_SCOPED_TABLE_CLASSIFICATION as erased, retained (with reason), "
        "or out_of_scope"
    )
    stale = classified - live_tables
    assert stale == set(), f"classification lists tables absent from the schema: {sorted(stale)}"
    assert DEAL_SCOPED_TABLE_CLASSIFICATION["erasure_requests"] == "retained"
    assert DEAL_SCOPED_TABLE_CLASSIFICATION["deals"] == "erased"


def test_full_deal_erasure_across_classified_surface(pg_clean: None) -> None:
    deal_a = _seed_deal_graph(_TENANT_A)
    deal_b = _seed_deal_graph(_TENANT_B)

    sink = InMemoryAuditSink()
    request = request_erasure(_ctx(), deal_a, _REASON, sink, PostgresErasureRequestStore())
    executed = execute_erasure(
        _ctx(),
        request.request_id,
        sink,
        executor=PostgresErasureExecutor(),
        hold_checker=lambda tid, did: None,
        store=PostgresErasureRequestStore(),
    )
    assert executed.status == ErasureStatus.EXECUTED
    assert executed.counts["rows_deleted"] >= 9  # the seeded graph incl. children + deals row

    erased_tables = [
        table for table, kind in DEAL_SCOPED_TABLE_CLASSIFICATION.items() if kind == "erased"
    ]
    for table in erased_tables:
        assert _admin_count(table, _TENANT_A, deal_a) == 0, f"{table} still holds erased rows"
    # tenant B's identical graph is fully intact
    assert _admin_count("deals", _TENANT_B, deal_b) == 1
    assert _admin_count("claims", _TENANT_B, deal_b) == 1
    assert _admin_count("document_artifacts", _TENANT_B, deal_b) == 1
    # audit events survive erasure (immutable trail with deal references)
    assert _admin_count("audit_events", _TENANT_A) >= 1
    # and the request row itself remains as durable evidence (retained classification)
    stored = PostgresErasureRequestStore().get(_TENANT_A, request.request_id)
    assert stored is not None and stored.status == ErasureStatus.EXECUTED


def test_real_deal_hold_blocks_erasure_until_lifted(pg_clean: None) -> None:
    from idis.compliance.retention import block_deletion_if_held

    deal_a = _seed_deal_graph(_TENANT_A)
    sink = InMemoryAuditSink()
    hold = apply_hold(_ctx(), HoldTarget.DEAL, deal_a, _REASON, sink, PostgresLegalHoldRegistry())
    request = request_erasure(_ctx(), deal_a, _REASON, sink, PostgresErasureRequestStore())

    def _hold_checker(tenant_id: str, deal_id: str) -> None:
        block_deletion_if_held(_ctx(), HoldTarget.DEAL, deal_id, PostgresLegalHoldRegistry())

    from idis.api.errors import IdisHttpError

    with pytest.raises(IdisHttpError) as exc_info:
        execute_erasure(
            _ctx(),
            request.request_id,
            sink,
            executor=PostgresErasureExecutor(),
            hold_checker=_hold_checker,
            store=PostgresErasureRequestStore(),
        )
    assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"
    assert _admin_count("deals", _TENANT_A, deal_a) == 1  # nothing deleted

    lift_hold(_ctx(), hold.hold_id, sink, PostgresLegalHoldRegistry())
    executed = execute_erasure(
        _ctx(),
        request.request_id,
        sink,
        executor=PostgresErasureExecutor(),
        hold_checker=_hold_checker,
        store=PostgresErasureRequestStore(),
    )
    assert executed.status == ErasureStatus.EXECUTED
    assert _admin_count("deals", _TENANT_A, deal_a) == 0


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
