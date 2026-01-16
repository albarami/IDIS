"""Add runs, debate_sessions, deliverables, human_gates, overrides tables.

Revision ID: 0009
Revises: 0008_add_primary_span_id_to_claims
Create Date: 2026-01-16

Tables created:
- runs: Pipeline run tracking (QUEUED, RUNNING, SUCCEEDED, FAILED)
- debate_sessions: LangGraph debate sessions with transcript
- deliverables: Generated deliverable records with object store refs
- human_gates: Human verification gates for deals
- human_gate_actions: Immutable actions on human gates
- overrides: Partner overrides for IC export with caveats

All tables have:
- tenant_id UUID column for RLS
- RLS policy restricting access to current tenant
"""

from alembic import op

revision = "0009"
down_revision = "0008_add_primary_span_id_to_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- runs table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            mode VARCHAR(20) NOT NULL CHECK (mode IN ('SNAPSHOT', 'FULL')),
            status VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_runs_tenant_id ON runs(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_runs_deal_id ON runs(deal_id)")
    op.execute(
        """
        ALTER TABLE runs ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_runs ON runs
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_runs ON runs
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- debate_sessions table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS debate_sessions (
            debate_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            run_id UUID REFERENCES runs(run_id),
            protocol_version VARCHAR(20) NOT NULL DEFAULT 'v1',
            max_rounds INTEGER NOT NULL DEFAULT 5,
            rounds JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_debate_sessions_tenant_id ON debate_sessions(tenant_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_debate_sessions_deal_id ON debate_sessions(deal_id)")
    op.execute(
        """
        ALTER TABLE debate_sessions ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_debate_sessions ON debate_sessions
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_debate_sessions ON debate_sessions
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- deliverables table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deliverables (
            deliverable_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            run_id UUID REFERENCES runs(run_id),
            deliverable_type VARCHAR(50) NOT NULL,
            format VARCHAR(20) NOT NULL DEFAULT 'PDF' CHECK (format IN ('PDF', 'DOCX', 'JSON')),
            status VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED', 'GENERATING', 'COMPLETED', 'FAILED')),
            uri TEXT,
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_tenant_id ON deliverables(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_deal_id ON deliverables(deal_id)")
    op.execute(
        """
        ALTER TABLE deliverables ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_deliverables ON deliverables
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_deliverables ON deliverables
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- human_gates table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS human_gates (
            gate_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            gate_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CORRECTED')),
            context JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_human_gates_tenant_id ON human_gates(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_human_gates_deal_id ON human_gates(deal_id)")
    op.execute(
        """
        ALTER TABLE human_gates ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_human_gates ON human_gates
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_human_gates ON human_gates
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- human_gate_actions table (immutable) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS human_gate_actions (
            action_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            gate_id UUID NOT NULL REFERENCES human_gates(gate_id),
            action VARCHAR(20) NOT NULL CHECK (action IN ('APPROVE', 'REJECT', 'CORRECT')),
            actor_id VARCHAR(255) NOT NULL,
            notes TEXT,
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_human_gate_actions_tenant_id "
        "ON human_gate_actions(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_human_gate_actions_gate_id ON human_gate_actions(gate_id)"
    )
    op.execute(
        """
        ALTER TABLE human_gate_actions ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_human_gate_actions ON human_gate_actions
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_human_gate_actions ON human_gate_actions
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- overrides table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS overrides (
            override_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            override_type VARCHAR(50) NOT NULL,
            justification TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVOKED')),
            actor_id VARCHAR(255) NOT NULL,
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_overrides_tenant_id ON overrides(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_overrides_deal_id ON overrides(deal_id)")
    op.execute(
        """
        ALTER TABLE overrides ENABLE ROW LEVEL SECURITY
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_isolation_overrides ON overrides
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_overrides ON overrides
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS overrides CASCADE")
    op.execute("DROP TABLE IF EXISTS human_gate_actions CASCADE")
    op.execute("DROP TABLE IF EXISTS human_gates CASCADE")
    op.execute("DROP TABLE IF EXISTS deliverables CASCADE")
    op.execute("DROP TABLE IF EXISTS debate_sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS runs CASCADE")
