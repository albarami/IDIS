"""§4.4.1 Full Sanad Chain for a Claim.

Retrieve the complete provenance chain from source document through to claim.
Tenant isolation enforced via tenant_id constraint in WHERE clause.
Deterministic ordering by transmission node timestamp ASC.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH path = (doc:Document)-[:HAS_SPAN]->(span:Span)
              <-[:INPUT]-(tn:TransmissionNode)-[:OUTPUT]->(claim:Claim)
WHERE claim.claim_id = $claim_id AND claim.tenant_id = $tenant_id
RETURN doc, span, tn, claim,
       [node IN nodes(path) | labels(node)] AS node_types,
       length(path) AS chain_depth
ORDER BY tn.timestamp ASC
"""

REQUIRED_PARAMS = frozenset({"claim_id", "tenant_id"})


def build_full_chain_query(
    *,
    claim_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.1 full Sanad chain query.

    Args:
        claim_id: UUID of the claim to trace.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"claim_id": claim_id, "tenant_id": tenant_id}
