"""Phase 4.1: Add deterministic_calculations and calc_sanads tables with RLS.

Revision ID: 0005
Revises: 0004
Create Date: 2026-01-10

Tables:
- deterministic_calculations: Reproducible numeric computations
- calc_sanads: Provenance records linking calcs to input claims

RLS policies use NULLIF hardening for fail-closed tenant isolation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create deterministic_calculations and calc_sanads tables with RLS."""
    conn = op.get_bind()

    conn.execute(
        sa.text("""
        CREATE TABLE deterministic_calculations (
            calc_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            deal_id uuid NOT NULL,
            calc_type text NOT NULL,
            inputs jsonb NOT NULL,
            formula_hash text NOT NULL,
            code_version text NOT NULL,
            output jsonb NOT NULL,
            reproducibility_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE calc_sanads (
            calc_sanad_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            calc_id uuid NOT NULL REFERENCES deterministic_calculations(calc_id),
            input_claim_ids jsonb NOT NULL,
            input_min_sanad_grade text NOT NULL,
            calc_grade text NOT NULL,
            explanation jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, calc_id)
        )
        """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX idx_deterministic_calculations_tenant_deal
        ON deterministic_calculations(tenant_id, deal_id)
        """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX idx_deterministic_calculations_tenant_calc_type
        ON deterministic_calculations(tenant_id, calc_type)
        """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX idx_calc_sanads_tenant_calc
        ON calc_sanads(tenant_id, calc_id)
        """)
    )

    conn.execute(sa.text("ALTER TABLE deterministic_calculations ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text("ALTER TABLE calc_sanads ENABLE ROW LEVEL SECURITY"))

    conn.execute(
        sa.text("""
        CREATE POLICY deterministic_calculations_tenant_isolation
        ON deterministic_calculations
        FOR ALL
        USING (
            tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """)
    )

    conn.execute(
        sa.text("""
        CREATE POLICY calc_sanads_tenant_isolation
        ON calc_sanads
        FOR ALL
        USING (
            tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """)
    )

    conn.execute(
        sa.text("""
        COMMENT ON TABLE deterministic_calculations IS
        'Phase 4.1: Reproducible numeric computations with formula_hash and reproducibility_hash'
        """)
    )

    conn.execute(
        sa.text("""
        COMMENT ON TABLE calc_sanads IS
        'Phase 4.1: Provenance records linking calculations to input claims with grade derivation'
        """)
    )


def downgrade() -> None:
    """Drop deterministic_calculations and calc_sanads tables."""
    conn = op.get_bind()

    conn.execute(sa.text("DROP POLICY IF EXISTS calc_sanads_tenant_isolation ON calc_sanads"))
    conn.execute(
        sa.text(
            "DROP POLICY IF EXISTS deterministic_calculations_tenant_isolation "
            "ON deterministic_calculations"
        )
    )

    conn.execute(sa.text("DROP TABLE IF EXISTS calc_sanads CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS deterministic_calculations CASCADE"))
