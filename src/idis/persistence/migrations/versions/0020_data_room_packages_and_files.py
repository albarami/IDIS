"""Add data_room_packages and data_room_package_files tables (Slice77).

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-30

Tables created:
- data_room_packages: durable tenant/deal-scoped data-room package header
  (keyed by package_id only; no user-supplied name). Aggregate counts are
  derived from the file rows, not stored.
- data_room_package_files: per-file ledger. A file's location is stored as a
  path_hash plus a safe extension only -- no raw paths or filenames.

Both tables have a tenant_id UUID column for RLS and the canonical
tenant_isolation policy copied from 0010_run_steps_evidence_items.py.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- data_room_packages table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_room_packages (
            package_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            deal_id UUID NOT NULL REFERENCES deals(deal_id),
            status VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN', 'SEALED')),
            created_by_actor_id VARCHAR(255),
            created_by_actor_type VARCHAR(20)
                CHECK (
                    created_by_actor_type IS NULL
                    OR created_by_actor_type IN ('HUMAN', 'SERVICE')
                ),
            manifest_uri TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_room_packages_tenant ON data_room_packages(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_room_packages_tenant_deal "
        "ON data_room_packages(tenant_id, deal_id)"
    )
    op.execute("ALTER TABLE data_room_packages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE data_room_packages FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_data_room_packages ON data_room_packages")
    op.execute(
        """
        CREATE POLICY tenant_isolation_data_room_packages ON data_room_packages
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )

    # --- data_room_package_files table ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_room_package_files (
            file_entry_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            package_id UUID NOT NULL
                REFERENCES data_room_packages(package_id) ON DELETE CASCADE,
            deal_id UUID NOT NULL,
            sequence INTEGER NOT NULL,
            path_hash VARCHAR(64) NOT NULL,
            extension VARCHAR(16),
            sha256 VARCHAR(64),
            file_status VARCHAR(20) NOT NULL,
            support_status VARCHAR(40) NOT NULL,
            triage_status VARCHAR(40) NOT NULL,
            parse_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            error_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            doc_id UUID,
            document_id UUID,
            storage_uri TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, package_id, path_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_room_package_files_tenant_package "
        "ON data_room_package_files(tenant_id, package_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_room_package_files_tenant_deal "
        "ON data_room_package_files(tenant_id, deal_id)"
    )
    op.execute("ALTER TABLE data_room_package_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE data_room_package_files FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_data_room_package_files ON data_room_package_files"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_data_room_package_files ON data_room_package_files
        USING (
            NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
            AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_room_package_files CASCADE")
    op.execute("DROP TABLE IF EXISTS data_room_packages CASCADE")
