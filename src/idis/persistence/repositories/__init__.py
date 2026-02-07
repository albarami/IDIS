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
    InMemoryEvidenceRepository,
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
from idis.persistence.repositories.evidence import (
    EvidenceRepo,
    PostgresEvidenceRepository,
    get_evidence_repository,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    PostgresRunStepsRepository,
    RunStepsRepo,
    clear_run_steps_store,
    get_run_steps_repository,
)
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
    clear_in_memory_runs_store,
    get_runs_repository,
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
    "InMemoryEvidenceRepository",
    "InMemoryRunsRepository",
    "InMemoryRunStepsRepository",
    "InMemorySanadsRepository",
    "EvidenceRepo",
    "PostgresEvidenceRepository",
    "PostgresRunStepsRepository",
    "RunStepsRepo",
    "PostgresRunsRepository",
    "SanadsRepository",
    "clear_all_claims_stores",
    "clear_claims_in_memory_store",
    "clear_defects_in_memory_store",
    "clear_in_memory_runs_store",
    "clear_in_memory_store",
    "clear_run_steps_store",
    "clear_sanad_in_memory_store",
    "get_deals_repository",
    "get_evidence_repository",
    "get_run_steps_repository",
    "get_runs_repository",
    "seed_claim_in_memory",
    "seed_defect_in_memory",
    "seed_sanad_in_memory",
]
