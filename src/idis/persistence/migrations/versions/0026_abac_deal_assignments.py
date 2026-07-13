"""Durable ABAC deal assignments and groups (Slice98 Task 1).

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-12

Backs the existing ``DealAssignmentStore`` seam (``api/abac.py``) with durable, tenant-RLS state so
production ABAC never relies on an in-memory per-process store:

- ``deal_assignments``: one row per (deal, assignee); ``assignee_type`` is ``ACTOR`` (direct
  assignment, ``assignee_id`` = actor id) or ``GROUP`` (``assignee_id`` = group id). The UNIQUE
  index makes writes idempotent (``ON CONFLICT DO NOTHING``).
- ``groups`` / ``group_memberships``: group registry and actor membership;
  ``is_actor_in_deal_group`` = member of a group that is assigned to the deal.

Group and actor ids are TEXT (actor ids are registry strings like ``analyst-1``; group ids are
app-generated uuid strings) - no uuid casts on the join path. Group identity is TENANT-SCOPED:
``groups`` has a composite ``PRIMARY KEY (tenant_id, group_id)`` (the same group_id may exist
independently under two tenants) and ``group_memberships`` carries a composite
``FOREIGN KEY (tenant_id, group_id)`` so a membership can never reference another tenant's group
(FK integrity checks bypass RLS, so a single-column FK would leak across tenants). RLS follows the
canonical 0024 guarded form: ``FORCE ROW LEVEL SECURITY`` and an explicit ``IS NOT NULL`` guard on
both ``USING`` and ``WITH CHECK``, so reads AND writes fail closed without a tenant context (no
existence oracle).
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_TABLES = ("deal_assignments", "groups", "group_memberships")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deal_assignments (
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            assignee_type TEXT NOT NULL CHECK (assignee_type IN ('ACTOR', 'GROUP')),
            assignee_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_deal_assignments_assignee
            ON deal_assignments (tenant_id, deal_id, assignee_type, assignee_id);

        CREATE TABLE IF NOT EXISTS groups (
            tenant_id UUID NOT NULL,
            group_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT groups_pk PRIMARY KEY (tenant_id, group_id)
        );

        CREATE TABLE IF NOT EXISTS group_memberships (
            tenant_id UUID NOT NULL,
            group_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT group_memberships_group_fk
                FOREIGN KEY (tenant_id, group_id)
                REFERENCES groups (tenant_id, group_id)
                ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_group_memberships_member
            ON group_memberships (tenant_id, group_id, actor_id);
        """
    )

    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

            DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};

            CREATE POLICY {table}_tenant_isolation
                ON {table}
                USING (
                    NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                    AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
                )
                WITH CHECK (
                    NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                    AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
                );
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS group_memberships CASCADE;")
    op.execute("DROP TABLE IF EXISTS groups CASCADE;")
    op.execute("DROP TABLE IF EXISTS deal_assignments CASCADE;")
