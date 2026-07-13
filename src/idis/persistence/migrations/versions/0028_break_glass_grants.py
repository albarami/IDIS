"""Durable break-glass grants: issuance record + strict single-use consumption (Slice98 Task 5).

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-13

The break-glass core mints stateless HMAC tokens; without a durable record a valid token is
replayable until expiry and issuance leaves no trace. ``break_glass_grants`` records every issued
grant (grant_id = the token's token_id) and carries the consumption mark: when
``IDIS_ENABLE_DURABLE_BREAK_GLASS`` is on, RBACMiddleware consumes the grant with one atomic
conditional UPDATE, making each grant strictly single-use across replicas. The enforcement lookup
key is the FULL SHA-256 of the raw token, unique per tenant. ``justification`` is stored plaintext
(the overrides-table precedent: an RLS-protected governance record of WHY); audit events keep
their hash+length-only posture. Identity is TENANT-SCOPED (composite primary key) and RLS follows
the guarded 0024 form (FORCE + NULLIF on both USING and WITH CHECK). No FK to deals(deal_id): FK
integrity checks bypass RLS; the issuance route verifies deal existence tenant-scoped instead.
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS break_glass_grants (
            tenant_id UUID NOT NULL,
            grant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            actor_id TEXT NOT NULL,
            justification TEXT NOT NULL,
            token_sha256 TEXT NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            consumed_request_id TEXT,
            PRIMARY KEY (tenant_id, grant_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_break_glass_grants_token
            ON break_glass_grants (tenant_id, token_sha256);
        """
    )

    op.execute(
        """
        ALTER TABLE break_glass_grants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE break_glass_grants FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS break_glass_grants_tenant_isolation ON break_glass_grants;

        CREATE POLICY break_glass_grants_tenant_isolation
            ON break_glass_grants
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
    op.execute("DROP TABLE IF EXISTS break_glass_grants CASCADE;")
