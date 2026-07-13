"""Durable tenant ``data_region`` store: residency source of truth (Slice98 Task 3).

The residency middleware pins each request to the service's deployed region. Historically the
tenant's region came only from the request claim / API-key config; this module provides the
DURABLE, cross-replica source of truth (the ``tenants.data_region`` column, migration 0027) behind
a seam mirroring the ABAC assignment-store pattern:

- ``TenantRegionStore`` Protocol: read-only ``get_data_region(tenant_id) -> str | None``.
- ``InMemoryTenantRegionStore``: hermetic twin for tests and non-Postgres deployments.
- ``PostgresTenantRegionStore``: reads the durable column on a per-call app-role connection; a DB
  error propagates so the residency layer fails closed (deny), never a silent allow.
- Seam ``get_/set_/reset_/build_default_tenant_region_store``: Postgres when configured, else
  in-memory.

The cutover is gated by ``IDIS_ENABLE_DURABLE_RESIDENCY`` (default off) so it is deliberate. When
off, residency keeps using the request claim region and this store is never consulted.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

IDIS_DURABLE_RESIDENCY_ENV = "IDIS_ENABLE_DURABLE_RESIDENCY"
_TRUTHY = {"1", "true", "yes", "on"}


def is_durable_residency_enabled() -> bool:
    """True when the durable-residency cutover is explicitly enabled (default off)."""
    return os.environ.get(IDIS_DURABLE_RESIDENCY_ENV, "").strip().lower() in _TRUTHY


@runtime_checkable
class TenantRegionStore(Protocol):
    """Read seam for a tenant's durable data_region."""

    def get_data_region(self, tenant_id: str) -> str | None:
        """Return the tenant's durable data_region, or None if unset/absent.

        Implementations MUST raise on backend failure (never return None on error) so the residency
        layer can distinguish "no region provisioned" (deny as unset) from "cannot resolve" (deny as
        resolution failure) and fail closed in both cases.
        """
        ...


class InMemoryTenantRegionStore:
    """Process-local twin. Not durable; for tests and non-Postgres deployments."""

    def __init__(self) -> None:
        self._regions: dict[str, str | None] = {}

    def set_region(self, tenant_id: str, region: str | None) -> None:
        """Seed/overwrite a tenant's region (test/provisioning helper)."""
        self._regions[tenant_id] = region

    def get_data_region(self, tenant_id: str) -> str | None:
        return self._regions.get(tenant_id)


class PostgresTenantRegionStore:
    """Durable twin: reads ``tenants.data_region`` on a per-call app-role connection.

    The ``tenants`` registry is not RLS-scoped (it is the FK parent), so the read is filtered
    explicitly by ``tenant_id`` and only ever asked for the current authenticated tenant. Both a
    missing row and a NULL column return None (the residency layer denies as "region unset"); a
    backend error propagates so the residency layer denies as "resolution failed".
    """

    def get_data_region(self, tenant_id: str) -> str | None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn

        with begin_app_conn() as conn:
            row = conn.execute(
                text("SELECT data_region FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
                {"tid": tenant_id},
            ).fetchone()
        if row is None:
            return None
        value = row[0]
        return value if isinstance(value, str) else None


_store: TenantRegionStore | None = None


def build_default_tenant_region_store() -> TenantRegionStore:
    """Select the durable Postgres store when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresTenantRegionStore()
    return InMemoryTenantRegionStore()


def get_tenant_region_store() -> TenantRegionStore:
    """Return the process-wide tenant-region store, building the default on first use."""
    global _store
    if _store is None:
        _store = build_default_tenant_region_store()
    return _store


def set_tenant_region_store(store: TenantRegionStore) -> None:
    """Override the process-wide store (tests / explicit wiring)."""
    global _store
    _store = store


def reset_tenant_region_store() -> None:
    """Clear the process-wide store so the next access rebuilds the default."""
    global _store
    _store = None
