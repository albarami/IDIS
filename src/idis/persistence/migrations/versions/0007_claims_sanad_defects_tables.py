"""Create claims, sanads, and defects tables with RLS.

Revision ID: 0007
Revises: 0006
Create Date: 2026-01-13

Creates tables for Phase 3 Sanad Trust Framework:
- claims: Claim storage with tenant isolation
- sanads: Sanad chain storage
- defects: Defect storage

All tables use Row-Level Security (RLS) with FORCE enabled.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create claims, sanads, and defects tables with RLS."""

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            claim_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            claim_class TEXT NOT NULL,
            claim_text TEXT NOT NULL,
            predicate TEXT,
            value JSONB,
            sanad_id UUID,
            claim_grade TEXT NOT NULL DEFAULT 'D',
            corroboration JSONB DEFAULT '{"level": "AHAD", "independent_chain_count": 1}'::jsonb,
            claim_verdict TEXT NOT NULL DEFAULT 'UNVERIFIED',
            claim_action TEXT NOT NULL DEFAULT 'VERIFY',
            defect_ids JSONB DEFAULT '[]'::jsonb,
            materiality TEXT DEFAULT 'MEDIUM',
            ic_bound BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_claims_tenant_id
        ON claims (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_claims_deal_id
        ON claims (deal_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_claims_claim_grade
        ON claims (claim_grade)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sanads (
            sanad_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            claim_id UUID NOT NULL REFERENCES claims(claim_id),
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            primary_evidence_id TEXT NOT NULL,
            corroborating_evidence_ids JSONB DEFAULT '[]'::jsonb,
            transmission_chain JSONB DEFAULT '[]'::jsonb,
            computed JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sanads_tenant_id
        ON sanads (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sanads_claim_id
        ON sanads (claim_id)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS defects (
            defect_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            claim_id UUID REFERENCES claims(claim_id),
            defect_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'MINOR',
            description TEXT,
            cure_protocol TEXT,
            waived BOOLEAN DEFAULT FALSE,
            waived_by TEXT,
            waived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_defects_tenant_id
        ON defects (tenant_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_defects_claim_id
        ON defects (claim_id)
        """
    )

    op.execute("ALTER TABLE claims ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE claims FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE sanads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sanads FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE defects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE defects FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY claims_tenant_isolation ON claims
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY sanads_tenant_isolation ON sanads
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY defects_tenant_isolation ON defects
        USING (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('idis.tenant_id', true)::uuid)
        """
    )


def downgrade() -> None:
    """Drop claims, sanads, and defects tables."""

    op.execute("DROP POLICY IF EXISTS defects_tenant_isolation ON defects")
    op.execute("DROP POLICY IF EXISTS sanads_tenant_isolation ON sanads")
    op.execute("DROP POLICY IF EXISTS claims_tenant_isolation ON claims")

    op.execute("DROP TABLE IF EXISTS defects")
    op.execute("DROP TABLE IF EXISTS sanads")
    op.execute("DROP TABLE IF EXISTS claims")
