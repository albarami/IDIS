"""Persistence repositories for IDIS.

Provides tenant-scoped data access with Postgres persistence
and in-memory fallback for development/testing.
"""

from idis.persistence.repositories.claims import (
    ClaimNotFoundError,
    ClaimsRepository,
    DefectsRepository,
    InMemoryClaimsRepository,
    InMemoryDefectsRepository,
    InMemorySanadsRepository,
    SanadsRepository,
    clear_all_claims_stores,
    clear_claims_in_memory_store,
    clear_defects_in_memory_store,
    clear_sanad_in_memory_store,
    seed_claim_in_memory,
    seed_defect_in_memory,
    seed_sanad_in_memory,
)
from idis.persistence.repositories.deals import (
    DealNotFoundError,
    DealsRepository,
    InMemoryDealsRepository,
    clear_in_memory_store,
    get_deals_repository,
)

__all__ = [
    "ClaimNotFoundError",
    "ClaimsRepository",
    "DealNotFoundError",
    "DealsRepository",
    "DefectsRepository",
    "InMemoryClaimsRepository",
    "InMemoryDealsRepository",
    "InMemoryDefectsRepository",
    "InMemorySanadsRepository",
    "SanadsRepository",
    "clear_all_claims_stores",
    "clear_claims_in_memory_store",
    "clear_defects_in_memory_store",
    "clear_in_memory_store",
    "clear_sanad_in_memory_store",
    "get_deals_repository",
    "seed_claim_in_memory",
    "seed_defect_in_memory",
    "seed_sanad_in_memory",
]
