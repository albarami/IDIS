"""Resolution module — deduplication and conflict detection for extracted claims.

Provides:
- Deduplicator: Identifies duplicate claims via UUIDv5 identity
- ConflictDetector: Detects value conflicts between claims
- ConflictRecord: Structured conflict data
"""

from idis.services.extraction.resolution.conflict_detector import (
    ConflictDetector,
    ConflictRecord,
)
from idis.services.extraction.resolution.deduplicator import Deduplicator

__all__ = [
    "ConflictDetector",
    "ConflictRecord",
    "Deduplicator",
]
