"""Webhooks foundation: webhooks table with RLS + optional delivery outbox.

Revision ID: 0003
Revises: 0002
Create Date: 2026-01-09

Creates tenant-scoped webhook storage per IDIS v6.3:
- API Contracts §6 (Webhooks)
- Traceability Matrix WH-001

Tables:
- webhooks: Webhook subscriptions with tenant isolation
- webhook_delivery_attempts: Retry state persistence (optional outbox)

RLS:
- NULLIF hardening consistent with Phase 2.10.8
- Fail-closed: SELECT returns 0 rows when tenant unset
- Fail-closed: INSERT/UPDATE blocked by WITH CHECK

Security:
- secret column is TEXT (sensitive; never returned/logged)
- App role has INSERT/SELECT only (no BYPASSRLS)
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create webhooks and webhook_delivery_attempts tables with RLS."""

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhooks (
            webhook_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            url TEXT NOT NULL,
            events TEXT[] NOT NULL,
            secret TEXT,
            active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhooks_tenant_id
        ON webhooks (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhooks_active
        ON webhooks (tenant_id, active) WHERE active = true
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_delivery_attempts (
            attempt_id UUID PRIMARY KEY,
            webhook_id UUID NOT NULL REFERENCES webhooks(webhook_id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL,
            event_id UUID NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ,
            last_attempt_at TIMESTAMPTZ,
            last_error TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT valid_status CHECK (
                status IN ('pending', 'succeeded', 'failed', 'exhausted')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_delivery_attempts_tenant_id
        ON webhook_delivery_attempts (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_delivery_attempts_next_retry
        ON webhook_delivery_attempts (next_attempt_at)
        WHERE status = 'pending' AND next_attempt_at IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_delivery_attempts_webhook_id
        ON webhook_delivery_attempts (webhook_id)
        """
    )

    op.execute("ALTER TABLE webhooks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webhooks FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE webhook_delivery_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webhook_delivery_attempts FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY webhooks_tenant_isolation ON webhooks
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY webhook_delivery_attempts_tenant_isolation ON webhook_delivery_attempts
        USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    """Drop webhooks and webhook_delivery_attempts tables."""

    op.execute(
        "DROP POLICY IF EXISTS webhook_delivery_attempts_tenant_isolation "
        "ON webhook_delivery_attempts"
    )
    op.execute("DROP POLICY IF EXISTS webhooks_tenant_isolation ON webhooks")

    op.execute("DROP TABLE IF EXISTS webhook_delivery_attempts")
    op.execute("DROP TABLE IF EXISTS webhooks")
