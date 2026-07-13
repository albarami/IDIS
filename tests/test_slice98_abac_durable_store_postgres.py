"""Slice98 Task 1 (durable) - Postgres ABAC assignment store: RLS, durability, real decision path.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the in-memory twin cannot:
assignments/groups survive across store instances (restart/replica durability), duplicate writes
are idempotent (unique indexes), rows are invisible cross-tenant and unwritable without a tenant
context (guarded RLS, no existence oracle), and the REAL ABAC decision functions used by
RBACMiddleware allow/deny from the durable default store. PYTHONPATH pinned to this worktree's
src.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.api.abac import (
    AbacDecisionCode,
    PostgresDealAssignmentStore,
    check_deal_access,
    check_deal_access_with_break_glass,
    get_deal_assignment_store,
    reset_deal_assignment_store,
)

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_ABAC_TABLES = ("group_memberships", "groups", "deal_assignments")


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres ABAC-store integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0026's tables exist."""
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


@pytest.fixture
def abac_tenant(_pg_schema: None) -> Generator[str, None, None]:
    """A unique tenant per test; truncate ABAC tables (admin bypasses RLS); reset the seam."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_ABAC_TABLES)} CASCADE"))

    reset_deal_assignment_store()
    _truncate()
    yield str(uuid.uuid4())
    _truncate()
    reset_deal_assignment_store()


def test_app_role_is_non_superuser_and_nobypassrls(_pg_schema: None) -> None:
    # RLS proofs below are only meaningful under a real non-superuser app role.
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS name, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).fetchone()
    assert row is not None
    assert not row.rolsuper, f"current_user={row.name!r} is SUPERUSER"
    assert not row.rolbypassrls, f"current_user={row.name!r} has BYPASSRLS"


def test_direct_assignment_is_durable_across_store_instances(abac_tenant: str) -> None:
    deal_id = str(uuid.uuid4())
    writer = PostgresDealAssignmentStore()
    writer.add_assignment(abac_tenant, deal_id, "analyst-1")

    reader = PostgresDealAssignmentStore()  # a second instance = restart/replica stand-in
    assert reader.is_actor_assigned(abac_tenant, deal_id, "analyst-1") is True
    assert reader.is_actor_assigned(abac_tenant, deal_id, "analyst-2") is False

    writer.remove_assignment(abac_tenant, deal_id, "analyst-1")
    assert reader.is_actor_assigned(abac_tenant, deal_id, "analyst-1") is False


def test_duplicate_assignment_is_idempotent(abac_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    deal_id = str(uuid.uuid4())
    store = PostgresDealAssignmentStore()
    store.add_assignment(abac_tenant, deal_id, "analyst-1")
    store.add_assignment(abac_tenant, deal_id, "analyst-1")  # duplicate -> no error, no dup row

    with begin_app_conn() as conn:
        set_tenant_local(conn, abac_tenant)
        row = conn.execute(text("SELECT count(*) AS n FROM deal_assignments")).fetchone()
    assert row is not None and int(row.n) == 1


def test_group_membership_grants_deal_access(abac_tenant: str) -> None:
    deal_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    store = PostgresDealAssignmentStore()
    store.create_group(abac_tenant, group_id, name="deal-team")
    store.add_group_member(abac_tenant, group_id, "analyst-7")
    store.assign_group_to_deal(abac_tenant, deal_id, group_id)

    reader = PostgresDealAssignmentStore()
    assert reader.is_actor_in_deal_group(abac_tenant, deal_id, "analyst-7") is True
    assert reader.is_actor_in_deal_group(abac_tenant, deal_id, "analyst-8") is False  # not member
    other_deal = str(uuid.uuid4())
    assert reader.is_actor_in_deal_group(abac_tenant, other_deal, "analyst-7") is False

    store.remove_group_member(abac_tenant, group_id, "analyst-7")
    assert reader.is_actor_in_deal_group(abac_tenant, deal_id, "analyst-7") is False


def test_cross_tenant_rows_are_invisible(abac_tenant: str) -> None:
    deal_id = str(uuid.uuid4())
    store = PostgresDealAssignmentStore()
    store.add_assignment(abac_tenant, deal_id, "analyst-1")

    other_tenant = str(uuid.uuid4())
    # RLS: the same deal/actor under another tenant context is simply absent (no existence leak).
    assert store.is_actor_assigned(other_tenant, deal_id, "analyst-1") is False


def test_no_tenant_context_write_is_blocked(abac_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn

    # A write with an EMPTY tenant context must be rejected by the guarded WITH CHECK.
    with pytest.raises(SQLAlchemyError), begin_app_conn() as conn:
        conn.execute(text("SET LOCAL idis.tenant_id = ''"))
        conn.execute(
            text(
                "INSERT INTO deal_assignments "
                "(tenant_id, deal_id, assignee_type, assignee_id, created_at) VALUES "
                "(CAST(:t AS uuid), CAST(:d AS uuid), 'ACTOR', :a, now())"
            ),
            {"t": abac_tenant, "d": str(uuid.uuid4()), "a": "analyst-1"},
        )


def test_abac_rls_policies_have_is_not_null_guard(_pg_schema: None) -> None:
    # Deterministic proof the 0026 policies use the guarded 0024 form on ALL three tables.
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        for table in _ABAC_TABLES:
            row = conn.execute(
                text(
                    "SELECT qual, with_check FROM pg_policies "
                    "WHERE tablename = :t AND policyname = :p"
                ),
                {"t": table, "p": f"{table}_tenant_isolation"},
            ).fetchone()
            assert row is not None, f"missing RLS policy on {table}"
            assert "IS NOT NULL" in row.qual, f"{table} USING lacks the guard"
            assert row.with_check is not None and "IS NOT NULL" in row.with_check, (
                f"{table} WITH CHECK lacks the guard"
            )


def test_group_ids_are_tenant_scoped_not_global(abac_tenant: str) -> None:
    # Groups are tenant-scoped identities: the SAME group_id must exist independently under two
    # tenants (composite (tenant_id, group_id) key), each with its own name/membership/assignment.
    # RED on the global-PK schema: tenant B's create_group is silently swallowed by tenant A's row.
    from idis.persistence.db import begin_app_conn, set_tenant_local

    tenant_b = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    deal_a, deal_b = str(uuid.uuid4()), str(uuid.uuid4())
    store = PostgresDealAssignmentStore()

    store.create_group(abac_tenant, group_id, name="team-a")
    store.create_group(tenant_b, group_id, name="team-b")  # independent row, not a silent no-op
    store.add_group_member(abac_tenant, group_id, "actor-a")
    store.add_group_member(tenant_b, group_id, "actor-b")
    store.assign_group_to_deal(abac_tenant, deal_a, group_id)
    store.assign_group_to_deal(tenant_b, deal_b, group_id)

    assert store.is_actor_in_deal_group(abac_tenant, deal_a, "actor-a") is True
    assert store.is_actor_in_deal_group(abac_tenant, deal_a, "actor-b") is False
    assert store.is_actor_in_deal_group(tenant_b, deal_b, "actor-b") is True
    assert store.is_actor_in_deal_group(tenant_b, deal_b, "actor-a") is False

    # tenant B genuinely OWNS its groups row (the create was not swallowed cross-tenant)
    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_b)
        row = conn.execute(
            text("SELECT name FROM groups WHERE group_id = :g"), {"g": group_id}
        ).fetchone()
    assert row is not None and row.name == "team-b"


def test_membership_for_other_tenants_group_fails(abac_tenant: str) -> None:
    # A membership must reference a SAME-TENANT group. FK integrity checks bypass RLS, so a
    # single-column FK would let tenant B attach a membership to tenant A's group - the composite
    # (tenant_id, group_id) FK must reject it instead.
    tenant_b = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    store = PostgresDealAssignmentStore()
    store.create_group(abac_tenant, group_id, name="team-a")  # exists ONLY under tenant A

    with pytest.raises(SQLAlchemyError):
        store.add_group_member(tenant_b, group_id, "actor-x")


def test_group_membership_fk_includes_tenant_id(_pg_schema: None) -> None:
    # Schema-level proof: the group_memberships FK is composite over (tenant_id, group_id).
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                "WHERE conrelid = 'group_memberships'::regclass AND contype = 'f'"
            )
        ).fetchone()
    assert row is not None, "group_memberships must have a foreign key"
    definition = row.def_ if hasattr(row, "def_") else row[0]
    assert "FOREIGN KEY (tenant_id, group_id)" in definition
    assert "REFERENCES groups(tenant_id, group_id)" in definition


def test_real_decision_path_uses_durable_default_store(abac_tenant: str) -> None:
    # The exact functions RBACMiddleware calls, resolving the DEFAULT store seam (which must be
    # the Postgres store here since IDIS_DATABASE_URL is configured in this env-gated test).
    deal_id = str(uuid.uuid4())
    assert isinstance(get_deal_assignment_store(), PostgresDealAssignmentStore)

    PostgresDealAssignmentStore().add_assignment(abac_tenant, deal_id, "analyst-1")

    allowed = check_deal_access(
        tenant_id=abac_tenant,
        actor_id="analyst-1",
        roles={"ANALYST"},
        deal_id=deal_id,
        is_mutation=True,
    )
    assert allowed.allow is True and allowed.code == AbacDecisionCode.ALLOWED

    denied = check_deal_access(
        tenant_id=abac_tenant,
        actor_id="analyst-9",
        roles={"ANALYST"},
        deal_id=deal_id,
        is_mutation=True,
    )
    assert denied.allow is False and denied.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT

    admin_unassigned = check_deal_access(
        tenant_id=abac_tenant,
        actor_id="admin-1",
        roles={"ADMIN"},
        deal_id=deal_id,
        is_mutation=False,
    )
    assert admin_unassigned.requires_break_glass is True

    break_glass = check_deal_access_with_break_glass(
        tenant_id=abac_tenant,
        actor_id="admin-1",
        roles={"ADMIN"},
        deal_id=deal_id,
        is_mutation=False,
        break_glass_valid=True,
    )
    assert break_glass.allow is True
