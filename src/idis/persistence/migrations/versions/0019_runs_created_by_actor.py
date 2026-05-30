"""Persist the originating authenticated actor on runs.

Adds nullable created_by_actor_id / created_by_actor_type columns so strict-run
audit events can attribute the authenticated actor that created a run instead of
an unauthenticated fallback. Nullable to preserve pre-existing rows; actor_type is
constrained to HUMAN/SERVICE.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable originating-actor columns to runs."""
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS created_by_actor_id VARCHAR(255)")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS created_by_actor_type VARCHAR(20)")
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_created_by_actor_type_check")
    op.execute(
        """
        ALTER TABLE runs
        ADD CONSTRAINT runs_created_by_actor_type_check
        CHECK (created_by_actor_type IS NULL OR created_by_actor_type IN ('HUMAN', 'SERVICE'))
        """
    )


def downgrade() -> None:
    """Remove originating-actor columns from runs."""
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_created_by_actor_type_check")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS created_by_actor_type")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS created_by_actor_id")
