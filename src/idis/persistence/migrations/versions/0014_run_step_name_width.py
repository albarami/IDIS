"""Widen run_steps.step_name for methodology step names.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow canonical FULL methodology step names to persist in Postgres."""
    op.execute(
        """
        ALTER TABLE run_steps
        ALTER COLUMN step_name TYPE VARCHAR(100)
        """
    )


def downgrade() -> None:
    """Narrow only when doing so cannot truncate existing persisted rows."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM run_steps
                WHERE length(step_name) > 50
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade run_steps.step_name to VARCHAR(50): long values exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE run_steps
        ALTER COLUMN step_name TYPE VARCHAR(50)
        """
    )
