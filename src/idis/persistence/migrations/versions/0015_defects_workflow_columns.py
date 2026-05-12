"""Align defects table with workflow repository columns.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add columns required by the durable DefectsRepository contract."""
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS deal_id UUID")
    op.execute(
        """
        UPDATE defects
        SET deal_id = claims.deal_id
        FROM claims
        WHERE defects.claim_id = claims.claim_id
          AND defects.deal_id IS NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'defects_deal_id_fkey'
            ) THEN
                ALTER TABLE defects
                ADD CONSTRAINT defects_deal_id_fkey
                FOREIGN KEY (deal_id) REFERENCES deals(deal_id);
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS status TEXT")
    op.execute("UPDATE defects SET status = 'OPEN' WHERE status IS NULL")
    op.execute("ALTER TABLE defects ALTER COLUMN status SET DEFAULT 'OPEN'")
    op.execute("ALTER TABLE defects ALTER COLUMN status SET NOT NULL")
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS waiver_reason TEXT")
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS cured_by TEXT")
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS cured_reason TEXT")
    op.execute("ALTER TABLE defects ADD COLUMN IF NOT EXISTS cured_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_defects_deal_id
        ON defects (deal_id)
        WHERE deal_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Remove defect workflow columns added in this revision."""
    op.execute("DROP INDEX IF EXISTS ix_defects_deal_id")
    op.execute(
        """
        ALTER TABLE defects
        DROP CONSTRAINT IF EXISTS defects_deal_id_fkey
        """
    )
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS cured_at")
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS cured_reason")
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS cured_by")
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS waiver_reason")
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE defects DROP COLUMN IF EXISTS deal_id")
