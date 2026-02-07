"""§4.4.6 Cross-Document Entity Co-occurrence.

Find entities mentioned across multiple documents (supports entity resolution).
Tenant isolation enforced via property match on deal node.
Deterministic ordering by doc_count DESC.
"""

from __future__ import annotations

from typing import Any

QUERY = """\
MATCH (entity:Entity)-[:MENTIONED_IN]->(span:Span)
      <-[:HAS_SPAN]-(doc:Document)
      <-[:HAS_DOCUMENT]-(deal:Deal {deal_id: $deal_id, tenant_id: $tenant_id})
WITH entity, collect(DISTINCT doc.document_id) AS docs, count(DISTINCT doc) AS doc_count
WHERE doc_count >= 2
RETURN entity.name, entity.type, doc_count, docs
ORDER BY doc_count DESC
"""

REQUIRED_PARAMS = frozenset({"deal_id", "tenant_id"})


def build_entity_cooccurrence_query(
    *,
    deal_id: str,
    tenant_id: str,
) -> tuple[str, dict[str, Any]]:
    """Build the §4.4.6 entity co-occurrence query.

    Args:
        deal_id: UUID of the deal.
        tenant_id: UUID of the tenant (isolation constraint).

    Returns:
        Tuple of (cypher_query, parameters).
    """
    return QUERY, {"deal_id": deal_id, "tenant_id": tenant_id}
