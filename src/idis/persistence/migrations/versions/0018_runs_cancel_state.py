"""Add CANCELLED run status and cancellation timestamp support.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add cancellable run state while preserving tenant/RLS semantics."""
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check")
    op.execute(
        """
        ALTER TABLE runs
        ADD CONSTRAINT runs_status_check
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'))
        """
    )


def downgrade() -> None:
    """Revert cancellable run state additions."""
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check")
    op.execute("UPDATE runs SET status = 'FAILED' WHERE status = 'CANCELLED'")
    op.execute(
        """
        ALTER TABLE runs
        ADD CONSTRAINT runs_status_check
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'))
        """
    )
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS cancel_requested_at")
