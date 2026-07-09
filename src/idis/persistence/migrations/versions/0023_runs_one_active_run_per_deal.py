"""Enforce at most one active run per (tenant, deal) (Slice96 / DEC-D).

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-07

Adds a partial UNIQUE index on runs(tenant_id, deal_id) WHERE status IN ('QUEUED', 'RUNNING')
so two concurrent startRun inserts for the same deal cannot both create an active run — the
loser hits a unique violation. The runs repository fail-fast pre-check surfaces the common
"second startRun while active" case as a safe RUN_ALREADY_ACTIVE (409); this index is the
race-safe backstop. Terminal runs (SUCCEEDED/FAILED/CANCELLED) are unconstrained, so a deal can
be re-run once its active run finishes.
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_one_active_per_deal "
        "ON runs (tenant_id, deal_id) "
        "WHERE status IN ('QUEUED', 'RUNNING')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_runs_one_active_per_deal")
