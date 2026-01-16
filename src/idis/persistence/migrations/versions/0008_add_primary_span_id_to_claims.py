"""Add primary_span_id column to claims table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-01-15

Adds primary_span_id (UUID, nullable) to claims table for evidence span reference.
RLS policies remain unchanged as tenant_id is the isolation boundary.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add primary_span_id column to claims table."""
    op.execute(
        """
        ALTER TABLE claims
        ADD COLUMN IF NOT EXISTS primary_span_id UUID
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_claims_primary_span_id
        ON claims (primary_span_id)
        WHERE primary_span_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Remove primary_span_id column from claims table."""
    op.execute("DROP INDEX IF EXISTS ix_claims_primary_span_id")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS primary_span_id")
