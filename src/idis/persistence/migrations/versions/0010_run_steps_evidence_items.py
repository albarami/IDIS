"""Add run_steps and evidence_items tables.

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-07

Tables created:
- run_steps: Pipeline step ledger per run (step_order, retry_count, result_summary)
- evidence_items: Evidence items linking claims to source spans

All tables have:
- tenant_id UUID column for RLS
- RLS policy restricting access to current tenant
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- run_steps table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_steps (
            step_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            run_id UUID NOT NULL REFERENCES runs(run_id),
            step_name VARCHAR(50) NOT NULL,
            step_order INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'BLOCKED')),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            retry_count INTEGER NOT NULL DEFAULT 0,
            result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_code VARCHAR(100),
            error_message TEXT,
            UNIQUE (tenant_id, run_id, step_name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_steps_tenant_run ON run_steps(tenant_id, run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_steps_tenant_run_order "
        "ON run_steps(tenant_id, run_id, step_order)"
    )
    op.execute(
        """
        ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_run_steps ON run_steps
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_run_steps ON run_steps
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- evidence_items table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_items (
            evidence_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            claim_id UUID NOT NULL,
            source_span_id UUID NOT NULL,
            source_grade VARCHAR(10) NOT NULL DEFAULT 'D',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_items_tenant_deal "
        "ON evidence_items(tenant_id, deal_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_items_tenant_claim "
        "ON evidence_items(tenant_id, claim_id)"
    )
    op.execute(
        """
        ALTER TABLE evidence_items ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_evidence_items ON evidence_items
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_evidence_items ON evidence_items
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_items CASCADE")
    op.execute("DROP TABLE IF EXISTS run_steps CASCADE")
