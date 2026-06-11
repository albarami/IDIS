"""Source-grade mapping for enrichment providers (Slice86, decision D-E).

Maps a provider's rights class (plus whether tenant BYOL credentials backed the call) onto the
evidence ``SourceGrade`` scale at summary/export time. The grade is NEVER persisted into
``EnrichmentProvenance`` and grade ``A`` is reserved for primary/audited documents — enrichment
sources can never earn it.

Rule (locked): GREEN -> B, YELLOW -> C, RED with BYOL -> C, RED without BYOL -> D.
"""

from __future__ import annotations

from idis.models.evidence_item import SourceGrade
from idis.services.enrichment.models import RightsClass


def map_rights_to_source_grade(
    rights_class: RightsClass | str,
    *,
    has_byol: bool,
) -> SourceGrade:
    """Map a provider rights class to an enrichment source grade.

    Args:
        rights_class: Provider rights classification (enum or its string value).
        has_byol: Whether tenant BYOL credentials backed the provider call.

    Returns:
        SourceGrade B/C/D per the locked Slice86 rule (never A).
    """
    rights = RightsClass(rights_class)
    if rights == RightsClass.GREEN:
        return SourceGrade.B
    if rights == RightsClass.YELLOW:
        return SourceGrade.C
    return SourceGrade.C if has_byol else SourceGrade.D
