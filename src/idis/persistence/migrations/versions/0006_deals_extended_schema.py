"""Extend deals table with additional columns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-01-13

Adds missing columns to deals table:
- company_name: Required company name
- status: Deal status (NEW, ACTIVE, etc.)
- stage: Optional deal stage
- tags: JSONB array of tags
- updated_at: Last update timestamp
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add extended columns to deals table."""

    op.execute(
        """
        ALTER TABLE deals
        ADD COLUMN IF NOT EXISTS company_name TEXT
        """
    )

    op.execute(
        """
        ALTER TABLE deals
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'NEW'
        """
    )

    op.execute(
        """
        ALTER TABLE deals
        ADD COLUMN IF NOT EXISTS stage TEXT
        """
    )

    op.execute(
        """
        ALTER TABLE deals
        ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb
        """
    )

    op.execute(
        """
        ALTER TABLE deals
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deals_status
        ON deals (status)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deals_created_at
        ON deals (created_at)
        """
    )


def downgrade() -> None:
    """Remove extended columns from deals table."""

    op.execute("DROP INDEX IF EXISTS ix_deals_created_at")
    op.execute("DROP INDEX IF EXISTS ix_deals_status")

    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS tags")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS stage")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS company_name")
