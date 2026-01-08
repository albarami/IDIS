"""PostgreSQL Foundation: audit_events, idempotency_records, deals tables with RLS.

Revision ID: 0001
Revises:
Create Date: 2026-01-08

Creates the foundational tables for IDIS:
- audit_events: Append-only audit log with immutability trigger
- idempotency_records: Tenant-scoped idempotency storage
- deals: Basic deal storage

All tables use Row-Level Security (RLS) with FORCE enabled.
Tenant isolation enforced via current_setting('idis.tenant_id', true).
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply migration: create tables, RLS policies, and immutability trigger."""

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            event_type TEXT NOT NULL,
            request_id TEXT,
            idempotency_key TEXT,
            event JSONB NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_id
        ON audit_events (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at
        ON audit_events (occurred_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_audit_events_event_type
        ON audit_events (event_type)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_records (
            tenant_id UUID NOT NULL,
            actor_id TEXT NOT NULL,
            method TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            body_bytes BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, actor_id, method, operation_id, idempotency_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deals (
            deal_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deals_tenant_id
        ON deals (tenant_id)
        """
    )

    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE deals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deals FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY audit_events_tenant_isolation ON audit_events
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY idempotency_records_tenant_isolation ON idempotency_records
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY deals_tenant_isolation ON deals
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION idis_reject_audit_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit events are immutable: UPDATE and DELETE are not allowed';
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE TRIGGER audit_events_immutability
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION idis_reject_audit_mutation()
        """
    )


def downgrade() -> None:
    """Revert migration: drop tables, policies, and trigger."""

    op.execute("DROP TRIGGER IF EXISTS audit_events_immutability ON audit_events")

    op.execute("DROP FUNCTION IF EXISTS idis_reject_audit_mutation()")

    op.execute("DROP POLICY IF EXISTS deals_tenant_isolation ON deals")
    op.execute("DROP POLICY IF EXISTS idempotency_records_tenant_isolation ON idempotency_records")
    op.execute("DROP POLICY IF EXISTS audit_events_tenant_isolation ON audit_events")

    op.execute("DROP TABLE IF EXISTS deals")
    op.execute("DROP TABLE IF EXISTS idempotency_records")
    op.execute("DROP TABLE IF EXISTS audit_events")
