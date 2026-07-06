"""Add durable Layer-2 IC challenge output tables (Slice93).

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-05

Tables created:
- layer2_ic_challenges: durable safe challenge rows (identity + a safe_summary JSONB of
  ref-id lists, finding ids, counts, and finding_type/severity histograms — no claim
  text, transcripts, prompt text, or raw model output). challenge_id is a bare UUID5.
- layer2_ic_findings: per-finding rows (finding/challenge ids, finding_type, severity,
  the bounded challenge category, and reference id lists). finding_id is a prefixed /
  LLM-supplied string
  ("layer2-finding-<hex>" or the model's own id), stored as VARCHAR and keyed compositely
  with (tenant_id, run_id, finding_id) so a low-entropy or attacker-influenced finding id
  can never collide across tenants/runs.

Both tables carry tenant_id UUID for RLS with the canonical tenant_isolation policy copied
from 0021_layer1_evidence_durability.py; deterministic ids make ON CONFLICT upserts idempotent.
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- layer2_ic_challenges table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS layer2_ic_challenges (
            challenge_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            source_debate_id VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL
                CHECK (status IN ('completed', 'blocked')),
            safe_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_layer2_ic_challenges_tenant "
        "ON layer2_ic_challenges(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_layer2_ic_challenges_tenant_run "
        "ON layer2_ic_challenges(tenant_id, run_id)"
    )
    op.execute("ALTER TABLE layer2_ic_challenges ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE layer2_ic_challenges FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_layer2_ic_challenges ON layer2_ic_challenges"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_layer2_ic_challenges ON layer2_ic_challenges
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- layer2_ic_findings table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS layer2_ic_findings (
            finding_id VARCHAR(255) NOT NULL,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            challenge_id UUID NOT NULL,
            finding_type VARCHAR(80) NOT NULL,
            severity VARCHAR(40) NOT NULL,
            category VARCHAR(40) NOT NULL DEFAULT 'general',
            supported_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            supported_calc_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            graph_ref_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            rag_ref_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            enrichment_ref_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, run_id, finding_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_layer2_ic_findings_tenant ON layer2_ic_findings(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_layer2_ic_findings_tenant_run "
        "ON layer2_ic_findings(tenant_id, run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_layer2_ic_findings_tenant_challenge "
        "ON layer2_ic_findings(tenant_id, challenge_id)"
    )
    op.execute("ALTER TABLE layer2_ic_findings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE layer2_ic_findings FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_layer2_ic_findings ON layer2_ic_findings")
    op.execute(
        """
        CREATE POLICY tenant_isolation_layer2_ic_findings ON layer2_ic_findings
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS layer2_ic_findings CASCADE")
    op.execute("DROP TABLE IF EXISTS layer2_ic_challenges CASCADE")
