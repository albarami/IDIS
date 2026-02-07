"""§4.4.4 Weakest Link in Transmission Chain.

Identify the lowest-grade node in a Sanad chain (determines base grade).
Tenant isolation enforced via property match on claim node.
Deterministic ordering by grade_rank DESC, LIMIT 1.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH (claim:Claim {claim_id: $claim_id, tenant_id: $tenant_id})
      -[:HAS_SANAD_STEP]->(tn:TransmissionNode)
      -[:INPUT]->(ev:EvidenceItem)
WITH tn, ev,
     CASE ev.source_grade
       WHEN 'A' THEN 0 WHEN 'B' THEN 1
       WHEN 'C' THEN 2 WHEN 'D' THEN 3
     END AS grade_rank
ORDER BY grade_rank DESC
LIMIT 1
RETURN tn.node_id AS weakest_node,
       ev.evidence_id AS weakest_evidence,
       ev.source_grade AS min_grade,
       ev.source_system AS source_system
"""

REQUIRED_PARAMS = frozenset({"claim_id", "tenant_id"})


def build_weakest_link_query(
    *,
    claim_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.4 weakest link query.

    Args:
        claim_id: UUID of the claim.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"claim_id": claim_id, "tenant_id": tenant_id}
