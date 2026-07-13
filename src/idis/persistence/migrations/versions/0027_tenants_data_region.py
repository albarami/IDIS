"""Add durable data_region to the tenants registry (residency source of truth).

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-13

Slice98 Task 3: residency has historically pinned each request by comparing the request claim /
API-key region against the service region. This adds the DURABLE, cross-replica source of truth -
a nullable ``tenants.data_region`` column - which the residency layer consults when
``IDIS_ENABLE_DURABLE_RESIDENCY`` is enabled. Nullable by design: existing tenants are unprovisioned
until an operator sets their region, and the residency layer fails closed (deny) on a NULL/unset
region. The ``tenants`` registry is the FK parent and stays without RLS; the durable read is
filtered explicitly by tenant_id.
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable data_region column to the tenants registry (idempotent)."""
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS data_region text")


def downgrade() -> None:
    """Remove the data_region column from the tenants registry."""
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS data_region")
