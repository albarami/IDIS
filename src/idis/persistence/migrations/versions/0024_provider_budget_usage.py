"""Add provider_budget_usage table for the DEC-C per-tenant/provider spend hard cap.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-08

Tenant-scoped durable accounting for the provider budget hard cap (Slice96 DEC-C): one row per
(tenant_id, provider) holding cumulative live-provider-call usage. A durable, race-safe store lets
the hard cap hold across replicas and survive restarts (unlike a per-process in-memory counter).

Table has:
- tenant_id UUID for RLS
- RLS policy restricting access to the current tenant (identical pattern to other tenant tables)
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_budget_usage (
            tenant_id UUID NOT NULL,
            provider TEXT NOT NULL,
            used BIGINT NOT NULL DEFAULT 0 CHECK (used >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT provider_budget_usage_pk PRIMARY KEY (tenant_id, provider)
        );

        CREATE INDEX IF NOT EXISTS idx_provider_budget_usage_tenant
            ON provider_budget_usage (tenant_id);
        """
    )

    # RLS matching the project convention: FORCE (the table owner is not exempt) + explicit USING
    # and WITH CHECK on the same NULLIF-guarded predicate, so reads AND writes fail closed when no
    # tenant context is set (a mismatched or absent tenant cannot write a usage row).
    op.execute(
        """
        ALTER TABLE provider_budget_usage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE provider_budget_usage FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS provider_budget_usage_tenant_isolation
            ON provider_budget_usage;

        CREATE POLICY provider_budget_usage_tenant_isolation
            ON provider_budget_usage
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
    op.execute("DROP TABLE IF EXISTS provider_budget_usage CASCADE;")
