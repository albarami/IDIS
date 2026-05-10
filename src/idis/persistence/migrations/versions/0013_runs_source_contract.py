"""Add persisted source contract to runs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-10
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable JSONB run-source payload."""
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS source JSONB")


def downgrade() -> None:
    """Remove nullable JSONB run-source payload."""
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS source")
