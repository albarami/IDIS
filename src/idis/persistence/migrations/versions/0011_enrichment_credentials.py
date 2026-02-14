"""Add enrichment_credentials table for BYOL credential persistence.

Revision ID: 0011
Revises: 0010
Create Date: 2026-02-08

Tables created:
- enrichment_credentials: Tenant-scoped encrypted BYOL credentials

All tables have:
- tenant_id UUID column for RLS
- RLS policy restricting access to current tenant
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment_credentials (
            tenant_id UUID NOT NULL,
            connector_id TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            rotated_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            CONSTRAINT enrichment_credentials_pk PRIMARY KEY (tenant_id, connector_id)
        );

        CREATE INDEX IF NOT EXISTS idx_enrichment_credentials_tenant
            ON enrichment_credentials (tenant_id);
        """
    )

    # RLS policy identical to other Phase 7.A tables
    op.execute(
        """
        ALTER TABLE enrichment_credentials ENABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS enrichment_credentials_tenant_isolation
            ON enrichment_credentials;

        CREATE POLICY enrichment_credentials_tenant_isolation
            ON enrichment_credentials
            USING (
                NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
                AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
            );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS enrichment_credentials CASCADE;")
