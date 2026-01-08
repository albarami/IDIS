"""Harden RLS policies to handle empty tenant context gracefully.

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-09

Uses NULLIF to convert empty string to NULL before UUID cast.
When tenant context is unset or empty:
- SELECT returns 0 rows (fail closed)
- INSERT/UPDATE blocked by WITH CHECK (RLS violation)
This prevents DataError from invalid UUID cast on empty string.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Update RLS policies to use NULLIF for empty tenant context handling."""

    op.execute("DROP POLICY IF EXISTS audit_events_tenant_isolation ON audit_events")
    op.execute(
        """
        CREATE POLICY audit_events_tenant_isolation ON audit_events
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )

    op.execute("DROP POLICY IF EXISTS idempotency_records_tenant_isolation ON idempotency_records")
    op.execute(
        """
        CREATE POLICY idempotency_records_tenant_isolation ON idempotency_records
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )

    op.execute("DROP POLICY IF EXISTS deals_tenant_isolation ON deals")
    op.execute(
        """
        CREATE POLICY deals_tenant_isolation ON deals
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    """Revert to original RLS policies without NULLIF handling."""

    op.execute("DROP POLICY IF EXISTS audit_events_tenant_isolation ON audit_events")
    op.execute(
        """
        CREATE POLICY audit_events_tenant_isolation ON audit_events
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute("DROP POLICY IF EXISTS idempotency_records_tenant_isolation ON idempotency_records")
    op.execute(
        """
        CREATE POLICY idempotency_records_tenant_isolation ON idempotency_records
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute("DROP POLICY IF EXISTS deals_tenant_isolation ON deals")
    op.execute(
        """
        CREATE POLICY deals_tenant_isolation ON deals
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )
