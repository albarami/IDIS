"""§4.4.2 All Claims for a Deal with Sanad Grades.

Retrieve all claims linked to a deal via document/span chain, with
optional defect information. Deterministic ordering by materiality DESC,
claim_grade ASC.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH (deal:Deal {deal_id: $deal_id, tenant_id: $tenant_id})
      -[:HAS_DOCUMENT]->(doc:Document)
      -[:HAS_SPAN]->(span:Span)
      <-[:SUPPORTED_BY]-(claim:Claim)
OPTIONAL MATCH (claim)-[:HAS_DEFECT]->(defect:Defect)
RETURN claim.claim_id, claim.claim_text, claim.claim_grade,
       claim.claim_verdict, claim.materiality,
       collect(DISTINCT doc.document_id) AS source_docs,
       collect(DISTINCT {type: defect.defect_type, severity: defect.severity}) AS defects
ORDER BY claim.materiality DESC, claim.claim_grade ASC
"""

REQUIRED_PARAMS = frozenset({"deal_id", "tenant_id"})


def build_deal_claims_grades_query(
    *,
    deal_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.2 deal claims with grades query.

    Args:
        deal_id: UUID of the deal.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"deal_id": deal_id, "tenant_id": tenant_id}
