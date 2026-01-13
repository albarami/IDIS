"""Persistence repositories for IDIS.

Provides tenant-scoped data access with Postgres persistence
and in-memory fallback for development/testing.
"""

from idis.persistence.repositories.deals import (
    DealNotFoundError,
    DealsRepository,
    InMemoryDealsRepository,
    clear_in_memory_store,
    get_deals_repository,
)

__all__ = [
    "DealNotFoundError",
    "DealsRepository",
    "InMemoryDealsRepository",
    "clear_in_memory_store",
    "get_deals_repository",
]
