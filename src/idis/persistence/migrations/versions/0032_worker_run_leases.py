"""Durable, fenced worker leases on pipeline runs (Slice100 Task 1).

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-15

The migration deliberately refuses to invent ownership for an already RUNNING
legacy row.  Operators must first allow or move those runs to a non-running
state, after which the four lease columns can be backfilled deterministically.
Likewise, downgrade is blocked while a worker owns any RUNNING row or any row
has nonzero fencing history.  Dropping and recreating a used token would repeat
an epoch, violating the monotonic fencing contract.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _lock_runs_against_worker_mutations() -> None:
    """Hold a migration-long lock that excludes worker claims and run writes."""
    op.get_bind().execute(sa.text("LOCK TABLE runs IN SHARE ROW EXCLUSIVE MODE"))


def _abort_if_running_rows_exist(*, action: str) -> None:
    bind = op.get_bind()
    running_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM runs WHERE status = 'RUNNING'")
    ).scalar_one()
    if int(running_count) > 0:
        raise RuntimeError(f"cannot {action} worker run leases while legacy RUNNING rows exist")


def _abort_if_fencing_history_would_be_lost() -> None:
    bind = op.get_bind()
    nonzero_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM runs WHERE lease_fencing_token <> 0")
    ).scalar_one()
    if int(nonzero_count) > 0:
        raise RuntimeError(
            "cannot downgrade worker run leases while nonzero fencing history exists"
        )


def upgrade() -> None:
    _lock_runs_against_worker_mutations()
    _abort_if_running_rows_exist(action="upgrade")

    op.add_column(
        "runs",
        sa.Column("lease_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column(
            "lease_fencing_token",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_runs_worker_lease_fencing_token_nonnegative",
        "runs",
        "lease_fencing_token >= 0",
    )
    op.create_check_constraint(
        "ck_runs_running_requires_coherent_worker_lease",
        "runs",
        "status <> 'RUNNING' OR (lease_owner_id IS NOT NULL "
        "AND lease_fencing_token > 0 AND lease_expires_at IS NOT NULL "
        "AND last_heartbeat_at IS NOT NULL)",
    )


def downgrade() -> None:
    _lock_runs_against_worker_mutations()
    _abort_if_running_rows_exist(action="downgrade")
    _abort_if_fencing_history_would_be_lost()

    op.drop_constraint(
        "ck_runs_running_requires_coherent_worker_lease",
        "runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_runs_worker_lease_fencing_token_nonnegative",
        "runs",
        type_="check",
    )
    op.drop_column("runs", "last_heartbeat_at")
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "lease_fencing_token")
    op.drop_column("runs", "lease_owner_id")
