"""Deduplicator — identifies duplicate claims via deterministic UUIDv5 identity.

Deduplication rules per spec §5.2:
- Exact match: claim_text identical (normalized) → merge, keep highest confidence
- Value match: same claim_class, same numeric value, same time_window → merge
- Near match: same metric, values within 5% → flag for reconciliation
- Conflict: same metric, values differ > 5% → create conflict record

Claim identity is computed as UUIDv5(namespace=deal_id, name=normalized_claim_text).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

DEDUP_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


@dataclass
class DeduplicatedClaim:
    """Result of deduplication for a single claim."""

    claim_text: str
    claim_class: str
    extraction_confidence: Decimal
    span_ids: list[str]
    identity_hash: str
    predicate: str | None = None
    value: dict[str, Any] | None = None
    merged_count: int = 1


@dataclass
class DeduplicationResult:
    """Result of deduplication across all claims."""

    unique_claims: list[DeduplicatedClaim] = field(default_factory=list)
    merged_count: int = 0
    near_matches: list[tuple[str, str]] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    """Normalize claim text for comparison.

    Args:
        text: Raw claim text.

    Returns:
        Lowercased, whitespace-normalized, stripped text.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def _compute_identity(deal_id: str, normalized_text: str) -> str:
    """Compute deterministic claim identity via UUIDv5.

    Args:
        deal_id: Deal UUID string for namespace.
        normalized_text: Normalized claim text.

    Returns:
        UUIDv5 string as identity hash.
    """
    namespace = uuid.uuid5(DEDUP_NAMESPACE, deal_id)
    return str(uuid.uuid5(namespace, normalized_text))


def _extract_numeric_value(claim: dict[str, Any]) -> Decimal | None:
    """Extract numeric value from claim's value struct if present.

    Args:
        claim: Claim dict with optional value field.

    Returns:
        Decimal value or None if not numeric.
    """
    value_struct = claim.get("value")
    if not value_struct or not isinstance(value_struct, dict):
        return None
    raw_value = value_struct.get("value")
    if raw_value is None:
        return None
    try:
        return Decimal(str(raw_value))
    except Exception:
        return None


def _extract_time_window(claim: dict[str, Any]) -> str | None:
    """Extract time_window from claim's value struct if present.

    Args:
        claim: Claim dict with optional value field.

    Returns:
        Time window string or None.
    """
    value_struct = claim.get("value")
    if not value_struct or not isinstance(value_struct, dict):
        return None
    tw = value_struct.get("time_window")
    return str(tw) if tw is not None else None


def _value_merge_key(claim: dict[str, Any]) -> tuple[str, str, str] | None:
    """Build a merge key for value-match dedup: (claim_class, value, time_window).

    Returns None if the claim lacks a numeric value or time_window.

    Args:
        claim: Claim dict with claim_class, value struct.

    Returns:
        Tuple key or None if not eligible for value-merge.
    """
    numeric = _extract_numeric_value(claim)
    if numeric is None:
        return None
    tw = _extract_time_window(claim)
    if tw is None:
        return None
    claim_class = claim.get("claim_class", "")
    return (claim_class, str(numeric), tw)


class Deduplicator:
    """Identifies and merges duplicate claims using deterministic identity.

    Uses UUIDv5(deal_id, normalized_text) for exact match detection.
    Near matches (within 5%) and conflicts (>5% diff) are flagged separately.
    """

    def deduplicate(
        self,
        claims: list[dict[str, Any]],
        *,
        deal_id: str,
    ) -> DeduplicationResult:
        """Deduplicate a list of claim dicts.

        Args:
            claims: List of claim dicts with claim_text, claim_class, etc.
            deal_id: Deal UUID for identity namespace.

        Returns:
            DeduplicationResult with unique claims and merge stats.
        """
        identity_map: dict[str, DeduplicatedClaim] = {}
        merged_count = 0

        for claim in claims:
            text = claim.get("claim_text", "")
            if not text:
                continue

            normalized = _normalize_text(text)
            identity = _compute_identity(deal_id, normalized)
            confidence = Decimal(str(claim.get("extraction_confidence", "0.5")))
            span_id = claim.get("span_id", "")

            if identity in identity_map:
                existing = identity_map[identity]
                if confidence > existing.extraction_confidence:
                    existing.extraction_confidence = confidence
                if span_id and span_id not in existing.span_ids:
                    existing.span_ids.append(span_id)
                existing.merged_count += 1
                merged_count += 1
            else:
                identity_map[identity] = DeduplicatedClaim(
                    claim_text=text,
                    claim_class=claim.get("claim_class", "OTHER"),
                    extraction_confidence=confidence,
                    span_ids=[span_id] if span_id else [],
                    identity_hash=identity,
                    predicate=claim.get("predicate"),
                    value=claim.get("value"),
                )

        identity_map, value_merges = self._value_merge(
            identity_map,
            deal_id=deal_id,
        )
        merged_count += value_merges

        unique_claims = sorted(identity_map.values(), key=lambda c: c.identity_hash)

        near_matches = self._find_near_matches(unique_claims)

        return DeduplicationResult(
            unique_claims=unique_claims,
            merged_count=merged_count,
            near_matches=near_matches,
        )

    def _value_merge(
        self,
        identity_map: dict[str, DeduplicatedClaim],
        *,
        deal_id: str,
    ) -> tuple[dict[str, DeduplicatedClaim], int]:
        """Merge claims with same claim_class + numeric value + time_window.

        Per spec §5.2 rule 2: if two drafts share the same claim_class,
        numeric value, and time_window but have different claim_text,
        merge them — keep higher confidence, link both span_ids.

        Args:
            identity_map: Current identity→claim map.
            deal_id: Deal UUID for context.

        Returns:
            Updated identity_map and count of value-merges performed.
        """
        value_groups: dict[tuple[str, str, str], list[str]] = {}
        for identity, claim in identity_map.items():
            key = _value_merge_key(
                {
                    "claim_class": claim.claim_class,
                    "value": claim.value,
                }
            )
            if key is not None:
                value_groups.setdefault(key, []).append(identity)

        merge_count = 0
        for _key, identities in value_groups.items():
            if len(identities) < 2:
                continue
            primary_id = identities[0]
            primary = identity_map[primary_id]
            for secondary_id in identities[1:]:
                secondary = identity_map.pop(secondary_id, None)
                if secondary is None:
                    continue
                if secondary.extraction_confidence > primary.extraction_confidence:
                    primary.extraction_confidence = secondary.extraction_confidence
                for sid in secondary.span_ids:
                    if sid and sid not in primary.span_ids:
                        primary.span_ids.append(sid)
                primary.merged_count += secondary.merged_count
                merge_count += 1
                logger.debug(
                    "Value-merged claim %s into %s (class=%s)",
                    secondary_id,
                    primary_id,
                    primary.claim_class,
                )

        return identity_map, merge_count

    def _find_near_matches(
        self,
        claims: list[DeduplicatedClaim],
    ) -> list[tuple[str, str]]:
        """Find near matches: same claim_class with values within 5%.

        Values that differ by ≤5% are near-matches (flagged for reconciliation).
        Values that differ by >5% are conflicts (handled by ConflictDetector).

        Args:
            claims: Deduplicated claims.

        Returns:
            List of (identity_hash_a, identity_hash_b) pairs that are near matches.
        """
        near: list[tuple[str, str]] = []
        by_class: dict[str, list[DeduplicatedClaim]] = {}
        for claim in claims:
            by_class.setdefault(claim.claim_class, []).append(claim)

        for class_claims in by_class.values():
            for i, a in enumerate(class_claims):
                val_a = _extract_numeric_value({"value": a.value})
                if val_a is None:
                    continue
                for b in class_claims[i + 1 :]:
                    val_b = _extract_numeric_value({"value": b.value})
                    if val_b is None:
                        continue
                    if val_a == Decimal("0") and val_b == Decimal("0"):
                        continue
                    denominator = max(abs(val_a), abs(val_b))
                    if denominator == Decimal("0"):
                        continue
                    pct_diff = abs(val_a - val_b) / denominator
                    if pct_diff <= Decimal("0.05"):
                        near.append((a.identity_hash, b.identity_hash))

        return near
