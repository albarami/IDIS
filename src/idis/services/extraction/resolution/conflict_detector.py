"""ConflictDetector — detects value conflicts between claims per spec §5.

Conflict rules per spec §5.2–5.3:
- Same claim_class + same metric key with values differing >5% → conflict
- Conflict records include both claim identities and resolution status
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from idis.services.extraction.resolution.deduplicator import (
    DeduplicatedClaim,
    _extract_numeric_value,
)

logger = logging.getLogger(__name__)

CONFLICT_THRESHOLD = Decimal("0.05")


@dataclass(frozen=True)
class ConflictRecord:
    """Structured conflict between two claims.

    Attributes:
        conflict_id: Unique UUID for this conflict.
        claim_identity_a: Identity hash of first claim.
        claim_identity_b: Identity hash of second claim.
        conflict_type: Type of conflict (VALUE_MISMATCH).
        resolution_status: Current status (PENDING, RESOLVED).
        pct_difference: Percentage difference between values.
        details: Additional context about the conflict.
    """

    conflict_id: str
    claim_identity_a: str
    claim_identity_b: str
    conflict_type: str
    resolution_status: str = "PENDING"
    pct_difference: str = "0"
    details: str = ""


@dataclass
class ConflictDetectionResult:
    """Result of conflict detection across claims."""

    conflicts: list[ConflictRecord] = field(default_factory=list)
    conflict_count: int = 0


class ConflictDetector:
    """Detects value conflicts between deduplicated claims.

    Groups claims by claim_class and compares numeric values.
    Conflicts are flagged when values differ by more than 5%.
    """

    def detect(
        self,
        claims: list[DeduplicatedClaim],
    ) -> ConflictDetectionResult:
        """Detect conflicts among deduplicated claims.

        Args:
            claims: List of deduplicated claims to check.

        Returns:
            ConflictDetectionResult with any detected conflicts.
        """
        conflicts: list[ConflictRecord] = []

        by_class: dict[str, list[DeduplicatedClaim]] = {}
        for claim in claims:
            by_class.setdefault(claim.claim_class, []).append(claim)

        for class_claims in by_class.values():
            conflicts.extend(self._detect_in_group(class_claims))

        return ConflictDetectionResult(
            conflicts=conflicts,
            conflict_count=len(conflicts),
        )

    def _detect_in_group(
        self,
        group: list[DeduplicatedClaim],
    ) -> list[ConflictRecord]:
        """Detect conflicts within a same-class group.

        Args:
            group: Claims of the same class.

        Returns:
            List of conflict records for this group.
        """
        conflicts: list[ConflictRecord] = []

        for i, a in enumerate(group):
            val_a = _extract_numeric_value({"value": a.value})
            if val_a is None:
                continue

            for b in group[i + 1 :]:
                val_b = _extract_numeric_value({"value": b.value})
                if val_b is None:
                    continue

                denominator = max(abs(val_a), abs(val_b))
                if denominator == Decimal("0"):
                    continue

                pct_diff = abs(val_a - val_b) / denominator

                if pct_diff > CONFLICT_THRESHOLD:
                    conflicts.append(
                        ConflictRecord(
                            conflict_id=str(uuid.uuid4()),
                            claim_identity_a=a.identity_hash,
                            claim_identity_b=b.identity_hash,
                            conflict_type="VALUE_MISMATCH",
                            resolution_status="PENDING",
                            pct_difference=str(pct_diff),
                            details=(
                                f"{a.claim_class}: "
                                f"'{a.claim_text[:80]}' ({val_a}) vs "
                                f"'{b.claim_text[:80]}' ({val_b})"
                            ),
                        )
                    )

        return conflicts
