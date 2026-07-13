"""Durable BYOK policy registry + legal holds (Slice98 Task 6).

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-13

The BYOK and legal-hold cores were process-local in-memory registries; a restart lost every key
policy and hold. These tables are the durable, cross-replica twins behind the store seams the
real ``ComplianceEnforcedStore`` boundary consults.

- ``byok_policies``: one policy per tenant (PRIMARY KEY tenant_id) storing POLICY METADATA only.
  The raw key alias is NEVER persisted - only its SHA-256 and length - and key material lives
  solely in the customer's KMS (locked Task 6 KMS-boundary decision; see
  docs/architecture/slice98_byok_kms_decision.md).
- ``legal_holds``: tenant-scoped hold records (composite PRIMARY KEY) storing HASH-ONLY reasons
  (the core never persists plaintext hold reasons). The partial index serves the hot
  ``has_active_hold`` deletion-gate lookup.

RLS follows the guarded 0024 form (FORCE + NULLIF on both USING and WITH CHECK). No FKs: FK
integrity checks bypass RLS.
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_TABLES = ("byok_policies", "legal_holds")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS byok_policies (
            tenant_id UUID NOT NULL,
            key_alias_sha256 TEXT NOT NULL,
            key_alias_length INTEGER NOT NULL,
            key_state TEXT NOT NULL CHECK (key_state IN ('ACTIVE', 'REVOKED')),
            created_at TIMESTAMPTZ NOT NULL,
            rotated_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id)
        );

        CREATE TABLE IF NOT EXISTS legal_holds (
            tenant_id UUID NOT NULL,
            hold_id UUID NOT NULL,
            target_type TEXT NOT NULL CHECK (target_type IN ('DEAL', 'DOCUMENT', 'ARTIFACT')),
            target_id TEXT NOT NULL,
            reason_hash TEXT NOT NULL,
            reason_length INTEGER NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL,
            applied_by TEXT NOT NULL,
            lifted_at TIMESTAMPTZ,
            lifted_by TEXT,
            PRIMARY KEY (tenant_id, hold_id)
        );

        CREATE INDEX IF NOT EXISTS idx_legal_holds_active_target
            ON legal_holds (tenant_id, target_type, target_id)
            WHERE lifted_at IS NULL;
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
    op.execute("DROP TABLE IF EXISTS legal_holds CASCADE;")
    op.execute("DROP TABLE IF EXISTS byok_policies CASCADE;")
