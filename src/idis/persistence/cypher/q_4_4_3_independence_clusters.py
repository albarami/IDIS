"""§4.4.3 Independence Clusters (Corroboration Assessment).

Find independent evidence groups for Tawatur classification.
Tenant isolation enforced via property match on claim node.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH (claim:Claim {claim_id: $claim_id, tenant_id: $tenant_id})
      -[:SUPPORTED_BY]->(ev:EvidenceItem)
WITH claim, ev,
     ev.source_system + '|' + coalesce(ev.upstream_origin_id, ev.evidence_id) AS independence_key
WITH claim, independence_key, collect(ev) AS group_members, count(ev) AS group_size
RETURN claim.claim_id,
       count(independence_key) AS independent_source_count,
       CASE
         WHEN count(independence_key) >= 3 THEN 'MUTAWATIR'
         WHEN count(independence_key) = 2 THEN 'AHAD_2'
         WHEN count(independence_key) = 1 THEN 'AHAD_1'
         ELSE 'NONE'
       END AS corroboration_status,
       collect({key: independence_key, count: group_size}) AS clusters
"""

REQUIRED_PARAMS = frozenset({"claim_id", "tenant_id"})


def build_independence_clusters_query(
    *,
    claim_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.3 independence clusters query.

    Args:
        claim_id: UUID of the claim.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"claim_id": claim_id, "tenant_id": tenant_id}
