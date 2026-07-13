"""Durable per-deal erasure requests (Slice98 Task 8).

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-13

The request -> ADMIN-execution erasure workflow needs durable, cross-replica state: a restart
must not lose a pending request, and who requested/executed an erasure is compliance evidence.
DELIBERATELY NO FOREIGN KEYS (locked amendment): the accepted erasure depth is full removal of
the ``deals`` row, so this evidence row must outlive the deal it erased - and FK integrity
checks bypass RLS anyway (slice precedent). The reason is stored hash+length only, never
plaintext. RLS follows the guarded 0024 form (FORCE + NULLIF on both USING and WITH CHECK).
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS erasure_requests (
            tenant_id UUID NOT NULL,
            request_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'EXECUTED', 'FAILED')),
            requested_by TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL,
            reason_hash TEXT NOT NULL,
            reason_length INTEGER NOT NULL,
            executed_by TEXT,
            executed_at TIMESTAMPTZ,
            counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (tenant_id, request_id)
        );

        CREATE INDEX IF NOT EXISTS idx_erasure_requests_tenant_deal
            ON erasure_requests (tenant_id, deal_id);
        """
    )

    op.execute(
        """
        ALTER TABLE erasure_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE erasure_requests FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS erasure_requests_tenant_isolation ON erasure_requests;

        CREATE POLICY erasure_requests_tenant_isolation
            ON erasure_requests
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
    op.execute("DROP TABLE IF EXISTS erasure_requests CASCADE;")
