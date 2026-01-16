"""Claims service module for IDIS.

Provides ClaimService for CRUD operations with:
- Tenant scoping enforcement
- No-Free-Facts validation on create/update
- Sanad integrity checks
- Audit event emission
"""

from idis.services.claims.service import (
    ClaimService,
    ClaimServiceError,
    NoFreeFactsViolationError,
    TenantMismatchError,
)

__all__ = [
    "ClaimService",
    "ClaimServiceError",
    "NoFreeFactsViolationError",
    "TenantMismatchError",
]
