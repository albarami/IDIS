"""IDIS idempotency support module.

Provides tenant-scoped idempotency key storage and lookup for safe API retries.
"""

from idis.idempotency.store import (
    IdempotencyKeyConflictError,
    IdempotencyRecord,
    IdempotencyStoreError,
    ScopeKey,
    SqliteIdempotencyStore,
)

__all__ = [
    "IdempotencyKeyConflictError",
    "IdempotencyRecord",
    "IdempotencyStoreError",
    "ScopeKey",
    "SqliteIdempotencyStore",
]
