"""Add durable Layer-1 evidence trust court output tables (Slice92).

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-04

Tables created:
- validated_evidence_packages: durable safe VEP candidate rows (IDs, status, and a
  safe_summary JSONB of dispositions/grades/finding aggregates — no claim text).
- evidence_trust_findings: per-finding Layer-1 court rows (finding/claim/sanad/calc/
  defect IDs, finding type, reason codes — no descriptions or payloads). finding_id
  ("finding-<hex>" from debate/roles/base.py deterministic_id) and claim_id
  ("claim_mth_<hex>" from claim_materialization) are production-shaped strings, so
  they are stored as VARCHAR; sanad_id is a verified bare UUID5 and stays UUID.
- muhasabah_records: court-scoped Muhasabah self-accounting rows (agent/output ids,
  confidence, subjectivity, supported ids, structured uncertainty triples — no
  falsifiability narrative or failure-mode prose).

Deterministic keys make writes idempotent via ON CONFLICT upserts: package and
Muhasabah record ids are full UUID5 primary keys; finding ids are the court's
prefixed deterministic strings ("finding-<hex>", only 48 bits of entropy), so
the findings table uses a composite PRIMARY KEY (tenant_id, run_id, finding_id)
because a bare global finding_id key could collide across tenants/runs. All
tables carry tenant_id UUID for RLS with the canonical tenant_isolation policy
copied from 0020_data_room_packages_and_files.py.
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- validated_evidence_packages table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS validated_evidence_packages (
            package_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            court_id UUID NOT NULL,
            dashboard_id UUID NOT NULL,
            status VARCHAR(20) NOT NULL
                CHECK (status IN ('completed', 'partial', 'failed')),
            safe_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_validated_evidence_packages_tenant "
        "ON validated_evidence_packages(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_validated_evidence_packages_tenant_run "
        "ON validated_evidence_packages(tenant_id, run_id)"
    )
    op.execute("ALTER TABLE validated_evidence_packages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE validated_evidence_packages FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_validated_evidence_packages "
        "ON validated_evidence_packages"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_validated_evidence_packages ON validated_evidence_packages
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- evidence_trust_findings table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_trust_findings (
            finding_id VARCHAR(255) NOT NULL,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            court_id UUID NOT NULL,
            finding_type VARCHAR(40) NOT NULL,
            claim_id VARCHAR(255) NOT NULL,
            evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            sanad_id UUID,
            calc_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            defect_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, run_id, finding_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_trust_findings_tenant "
        "ON evidence_trust_findings(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_trust_findings_tenant_run "
        "ON evidence_trust_findings(tenant_id, run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_trust_findings_tenant_court "
        "ON evidence_trust_findings(tenant_id, court_id)"
    )
    op.execute("ALTER TABLE evidence_trust_findings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evidence_trust_findings FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_evidence_trust_findings ON evidence_trust_findings"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_evidence_trust_findings ON evidence_trust_findings
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- muhasabah_records table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS muhasabah_records (
            record_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL,
            run_id UUID NOT NULL,
            source_step VARCHAR(50) NOT NULL,
            agent_id VARCHAR(255) NOT NULL,
            output_id VARCHAR(255) NOT NULL,
            confidence DOUBLE PRECISION NOT NULL
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            is_subjective BOOLEAN NOT NULL DEFAULT FALSE,
            supported_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            supported_calc_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            uncertainties JSONB NOT NULL DEFAULT '[]'::jsonb,
            record_timestamp VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, run_id, source_step, agent_id, output_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_muhasabah_records_tenant ON muhasabah_records(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_muhasabah_records_tenant_run "
        "ON muhasabah_records(tenant_id, run_id)"
    )
    op.execute("ALTER TABLE muhasabah_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE muhasabah_records FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_muhasabah_records ON muhasabah_records")
    op.execute(
        """
        CREATE POLICY tenant_isolation_muhasabah_records ON muhasabah_records
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS muhasabah_records CASCADE")
    op.execute("DROP TABLE IF EXISTS evidence_trust_findings CASCADE")
    op.execute("DROP TABLE IF EXISTS validated_evidence_packages CASCADE")
