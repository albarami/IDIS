"""§4.4.5 Defect Impact Analysis.

Find all claims affected by a specific defect and their downstream calculations.
Tenant isolation enforced via property match on defect node.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH (defect:Defect {defect_id: $defect_id, tenant_id: $tenant_id})
      <-[:HAS_DEFECT]-(claim:Claim)
OPTIONAL MATCH (calc:Calculation)-[:DERIVED_FROM]->(claim)
RETURN defect.defect_type, defect.severity,
       collect(DISTINCT {
         claim_id: claim.claim_id,
         claim_text: claim.claim_text,
         materiality: claim.materiality,
         grade: claim.claim_grade
       }) AS affected_claims,
       collect(DISTINCT calc.calc_id) AS affected_calculations
"""

REQUIRED_PARAMS = frozenset({"defect_id", "tenant_id"})


def build_defect_impact_query(
    *,
    defect_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.5 defect impact analysis query.

    Args:
        defect_id: UUID of the defect.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"defect_id": defect_id, "tenant_id": tenant_id}
