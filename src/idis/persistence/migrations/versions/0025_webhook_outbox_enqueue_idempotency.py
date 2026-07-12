"""Idempotent webhook outbox enqueue + hardened RLS on webhook_delivery_attempts.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-10

Slice97 Task 2. Two changes to the durable delivery outbox ``webhook_delivery_attempts`` (created
in migration 0003):

1. A UNIQUE index on ``(webhook_id, event_id)`` so enqueue is idempotent even under a race
   (``INSERT ... ON CONFLICT DO NOTHING`` — at most one delivery attempt per subscription per
   logical event). A full (not partial) unique index is correct: ``event_id`` is ``NOT NULL`` and
   we want at-most-one row per (webhook, event) regardless of delivery status.

2. Replace the original 0003 tenant-isolation policy with the canonical 0024 guarded RLS form:
   ``FORCE ROW LEVEL SECURITY`` plus an explicit ``NULLIF(current_setting('idis.tenant_id', true),
   '') IS NOT NULL`` guard on both ``USING`` and ``WITH CHECK`` — reads AND writes fail closed when
   no tenant context is set. Matches ``provider_budget_usage`` (0024); required by the Slice97 plan.
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_webhook_delivery_attempts_event
        ON webhook_delivery_attempts (webhook_id, event_id)
        """
    )

    op.execute(
        """
        ALTER TABLE webhook_delivery_attempts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE webhook_delivery_attempts FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS webhook_delivery_attempts_tenant_isolation
            ON webhook_delivery_attempts;

        CREATE POLICY webhook_delivery_attempts_tenant_isolation
            ON webhook_delivery_attempts
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
    # Restore the original 0003 policy form and drop the unique index.
    op.execute(
        """
        DROP POLICY IF EXISTS webhook_delivery_attempts_tenant_isolation
            ON webhook_delivery_attempts;

        CREATE POLICY webhook_delivery_attempts_tenant_isolation
            ON webhook_delivery_attempts
            USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid);
        """
    )
    op.execute("DROP INDEX IF EXISTS ux_webhook_delivery_attempts_event")
