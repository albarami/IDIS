"""Source Tiers — Six-level reliability hierarchy (Jarḥ wa Taʿdīl adaptation).

Implements deterministic source tier assignment for evidence items.
All tier assignments are fail-closed: unknown sources default to lowest tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SourceTier(Enum):
    """Six-level source reliability hierarchy.

    Tiers 1-4 are PRIMARY (can serve as primary evidence).
    Tiers 5-6 are SUPPORT_ONLY (cannot be primary for HIGH/CRITICAL claims).
    """

    ATHBAT_AL_NAS = "ATHBAT_AL_NAS"
    THIQAH_THABIT = "THIQAH_THABIT"
    THIQAH = "THIQAH"
    SADUQ = "SADUQ"
    SHAYKH = "SHAYKH"
    MAQBUL = "MAQBUL"


class TierUsage(Enum):
    """Admissibility classification for source tiers."""

    PRIMARY = "PRIMARY"
    SUPPORT_ONLY = "SUPPORT_ONLY"


@dataclass(frozen=True)
class TierInfo:
    """Metadata for a source tier."""

    tier: SourceTier
    numeric_weight: float
    usage: TierUsage
    description: str


@dataclass(frozen=True)
class ConflictInfo:
    """Information about a COI or conflict affecting source reliability."""

    coi_present: bool = False
    coi_severity: str | None = None
    coi_disclosed: bool = False
    coi_type: str | None = None
    coi_description: str | None = None


TIER_METADATA: dict[SourceTier, TierInfo] = {
    SourceTier.ATHBAT_AL_NAS: TierInfo(
        tier=SourceTier.ATHBAT_AL_NAS,
        numeric_weight=1.00,
        usage=TierUsage.PRIMARY,
        description="Highest reliability — audited financials, verified regulatory filings",
    ),
    SourceTier.THIQAH_THABIT: TierInfo(
        tier=SourceTier.THIQAH_THABIT,
        numeric_weight=0.90,
        usage=TierUsage.PRIMARY,
        description="Highly reliable — bank statements, signed contracts",
    ),
    SourceTier.THIQAH: TierInfo(
        tier=SourceTier.THIQAH,
        numeric_weight=0.80,
        usage=TierUsage.PRIMARY,
        description="Reliable — internal financial models with version control",
    ),
    SourceTier.SADUQ: TierInfo(
        tier=SourceTier.SADUQ,
        numeric_weight=0.65,
        usage=TierUsage.PRIMARY,
        description="Truthful but may err — founder statements, pitch decks",
    ),
    SourceTier.SHAYKH: TierInfo(
        tier=SourceTier.SHAYKH,
        numeric_weight=0.50,
        usage=TierUsage.SUPPORT_ONLY,
        description="Known but unverified — third-party estimates, press releases",
    ),
    SourceTier.MAQBUL: TierInfo(
        tier=SourceTier.MAQBUL,
        numeric_weight=0.40,
        usage=TierUsage.SUPPORT_ONLY,
        description="Minimally acceptable — analyst guesses, forum posts",
    ),
}

SOURCE_TYPE_TO_TIER: dict[str, SourceTier] = {
    "AUDITED_FINANCIAL": SourceTier.ATHBAT_AL_NAS,
    "REGULATORY_FILING": SourceTier.ATHBAT_AL_NAS,
    "SEC_FILING": SourceTier.ATHBAT_AL_NAS,
    "BANK_STATEMENT": SourceTier.THIQAH_THABIT,
    "SIGNED_CONTRACT": SourceTier.THIQAH_THABIT,
    "NOTARIZED_DOCUMENT": SourceTier.THIQAH_THABIT,
    "FINANCIAL_MODEL": SourceTier.THIQAH,
    "INTERNAL_REPORT": SourceTier.THIQAH,
    "VERSION_CONTROLLED_DOC": SourceTier.THIQAH,
    "PITCH_DECK": SourceTier.SADUQ,
    "FOUNDER_STATEMENT": SourceTier.SADUQ,
    "EXEC_MEMO": SourceTier.SADUQ,
    "EMAIL": SourceTier.SADUQ,
    "PRESS_RELEASE": SourceTier.SHAYKH,
    "THIRD_PARTY_ESTIMATE": SourceTier.SHAYKH,
    "NEWS_ARTICLE": SourceTier.SHAYKH,
    "ANALYST_ESTIMATE": SourceTier.MAQBUL,
    "FORUM_POST": SourceTier.MAQBUL,
    "UNKNOWN": SourceTier.MAQBUL,
}


def get_tier_weight(tier: SourceTier) -> float:
    """Get numeric weight for a source tier.

    Args:
        tier: Source tier enum value

    Returns:
        Numeric weight in range [0.40, 1.00]
    """
    return TIER_METADATA[tier].numeric_weight


def get_tier_usage(tier: SourceTier) -> TierUsage:
    """Get admissibility classification for a source tier.

    Args:
        tier: Source tier enum value

    Returns:
        TierUsage.PRIMARY or TierUsage.SUPPORT_ONLY
    """
    return TIER_METADATA[tier].usage


def assign_source_tier(
    source: dict[str, Any] | None,
    *,
    source_type_field: str = "source_type",
) -> SourceTier:
    """Assign source tier based on source metadata.

    FAIL-CLOSED: Unknown or missing source types default to MAQBUL (lowest tier).

    Args:
        source: Evidence item dictionary with source metadata
        source_type_field: Field name containing source type

    Returns:
        SourceTier enum value
    """
    if source is None:
        return SourceTier.MAQBUL

    source_type = source.get(source_type_field)
    if source_type is None:
        return SourceTier.MAQBUL

    source_type_upper = str(source_type).upper().strip()
    return SOURCE_TYPE_TO_TIER.get(source_type_upper, SourceTier.MAQBUL)


def tier_to_base_grade(tier: SourceTier) -> str:
    """Convert source tier to base Sanad grade.

    Args:
        tier: Source tier

    Returns:
        Base grade (A, B, C, or D)
    """
    tier_to_grade = {
        SourceTier.ATHBAT_AL_NAS: "A",
        SourceTier.THIQAH_THABIT: "A",
        SourceTier.THIQAH: "B",
        SourceTier.SADUQ: "B",
        SourceTier.SHAYKH: "C",
        SourceTier.MAQBUL: "C",
    }
    return tier_to_grade.get(tier, "C")


def is_primary_eligible(tier: SourceTier) -> bool:
    """Check if tier can serve as primary evidence.

    Args:
        tier: Source tier

    Returns:
        True if tier is PRIMARY eligible
    """
    return get_tier_usage(tier) == TierUsage.PRIMARY


def check_tier_admissibility(
    tier: SourceTier,
    materiality: str,
) -> tuple[bool, str | None]:
    """Check if source tier is admissible for given materiality level.

    RULE ADM-002: SUPPORT_ONLY sources cannot be primary for HIGH/CRITICAL claims.

    Args:
        tier: Source tier
        materiality: Claim materiality (LOW, MEDIUM, HIGH, CRITICAL)

    Returns:
        Tuple of (is_admissible, reason_if_not)
    """
    usage = get_tier_usage(tier)
    materiality_upper = materiality.upper() if materiality else "UNKNOWN"

    if usage == TierUsage.SUPPORT_ONLY and materiality_upper in {"HIGH", "CRITICAL"}:
        return (
            False,
            f"SUPPORT_ONLY tier {tier.value} cannot be primary for {materiality_upper} claim",
        )

    return (True, None)
