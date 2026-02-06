"""Tests for Deduplicator and ConflictDetector.

10 tests covering:
- Exact text match deduplication
- UUIDv5 identity is deterministic
- Highest confidence kept on merge
- All span_ids preserved on merge
- Near match detection (within 1%)
- Conflict detection (>5% difference)
- No conflicts for identical values
- Empty input produces empty output
- Different claim classes not compared for conflicts
- Merged count tracking
"""

from __future__ import annotations

from decimal import Decimal

from idis.services.extraction.resolution.conflict_detector import (
    ConflictDetector,
)
from idis.services.extraction.resolution.deduplicator import (
    Deduplicator,
    _compute_identity,
    _normalize_text,
)

DEAL_ID = "deal00001-0000-0000-0000-000000000001"


def _make_claim(
    text: str,
    claim_class: str = "FINANCIAL",
    confidence: str = "0.85",
    span_id: str = "span-001",
    value: dict | None = None,
) -> dict:
    """Helper to build a claim dict for testing."""
    claim: dict = {
        "claim_text": text,
        "claim_class": claim_class,
        "extraction_confidence": confidence,
        "span_id": span_id,
    }
    if value is not None:
        claim["value"] = value
    return claim


class TestDeduplicator:
    """Tests for Deduplicator."""

    def test_exact_text_match_deduplication(self) -> None:
        """Identical text produces one unique claim."""
        dedup = Deduplicator()
        claims = [
            _make_claim("Revenue was $5M.", span_id="s1"),
            _make_claim("Revenue was $5M.", span_id="s2"),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        assert len(result.unique_claims) == 1
        assert result.merged_count == 1
        assert "s1" in result.unique_claims[0].span_ids
        assert "s2" in result.unique_claims[0].span_ids

    def test_uuid5_identity_deterministic(self) -> None:
        """Same deal_id + same text always produces same identity."""
        id_a = _compute_identity(DEAL_ID, _normalize_text("Revenue was $5M."))
        id_b = _compute_identity(DEAL_ID, _normalize_text("Revenue was $5M."))
        id_c = _compute_identity(DEAL_ID, _normalize_text("Different claim."))

        assert id_a == id_b
        assert id_a != id_c

    def test_highest_confidence_kept(self) -> None:
        """On merge, the higher confidence value is retained."""
        dedup = Deduplicator()
        claims = [
            _make_claim("Revenue was $5M.", confidence="0.70", span_id="s1"),
            _make_claim("Revenue was $5M.", confidence="0.95", span_id="s2"),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        assert len(result.unique_claims) == 1
        assert result.unique_claims[0].extraction_confidence == Decimal("0.95")

    def test_all_span_ids_preserved(self) -> None:
        """All source span IDs are kept after merge."""
        dedup = Deduplicator()
        claims = [
            _make_claim("Revenue was $5M.", span_id="s1"),
            _make_claim("Revenue was $5M.", span_id="s2"),
            _make_claim("Revenue was $5M.", span_id="s3"),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        assert len(result.unique_claims) == 1
        assert set(result.unique_claims[0].span_ids) == {"s1", "s2", "s3"}

    def test_near_match_detection(self) -> None:
        """Values within 1% are flagged as near matches."""
        dedup = Deduplicator()
        claims = [
            _make_claim("ARR claim A", span_id="s1", value={"value": 5000000}),
            _make_claim("ARR claim B", span_id="s2", value={"value": 5040000}),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        assert len(result.near_matches) >= 1

    def test_empty_input_empty_output(self) -> None:
        """Empty claim list produces empty result."""
        dedup = Deduplicator()
        result = dedup.deduplicate([], deal_id=DEAL_ID)

        assert len(result.unique_claims) == 0
        assert result.merged_count == 0

    def test_merged_count_tracking(self) -> None:
        """Merged count reflects number of duplicate claims removed."""
        dedup = Deduplicator()
        claims = [
            _make_claim("Claim A.", span_id="s1"),
            _make_claim("Claim A.", span_id="s2"),
            _make_claim("Claim B.", span_id="s3"),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        assert len(result.unique_claims) == 2
        assert result.merged_count == 1


class TestConflictDetector:
    """Tests for ConflictDetector."""

    def test_conflict_detected_above_threshold(self) -> None:
        """Values differing >5% create a conflict record."""
        dedup = Deduplicator()
        claims = [
            _make_claim("ARR is $5M", span_id="s1", value={"value": 5000000}),
            _make_claim("ARR is $6M", span_id="s2", value={"value": 6000000}),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        detector = ConflictDetector()
        conflicts = detector.detect(result.unique_claims)

        assert conflicts.conflict_count >= 1
        assert conflicts.conflicts[0].conflict_type == "VALUE_MISMATCH"
        assert conflicts.conflicts[0].resolution_status == "PENDING"

    def test_no_conflict_for_identical_values(self) -> None:
        """Identical numeric values produce no conflict."""
        dedup = Deduplicator()
        claims = [
            _make_claim("ARR claim A", span_id="s1", value={"value": 5000000}),
            _make_claim("ARR claim B", span_id="s2", value={"value": 5000000}),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        detector = ConflictDetector()
        conflicts = detector.detect(result.unique_claims)

        assert conflicts.conflict_count == 0

    def test_different_classes_not_compared(self) -> None:
        """Claims of different classes are not compared for conflicts."""
        dedup = Deduplicator()
        claims = [
            _make_claim(
                "ARR $5M",
                claim_class="FINANCIAL",
                span_id="s1",
                value={"value": 5000000},
            ),
            _make_claim(
                "100K users",
                claim_class="TRACTION",
                span_id="s2",
                value={"value": 100000},
            ),
        ]
        result = dedup.deduplicate(claims, deal_id=DEAL_ID)

        detector = ConflictDetector()
        conflicts = detector.detect(result.unique_claims)

        assert conflicts.conflict_count == 0
