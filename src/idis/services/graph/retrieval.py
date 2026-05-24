"""Safe graph retrieval summaries built on the existing GraphRepository."""

from __future__ import annotations

from typing import Any, Protocol

from idis.persistence.graph_repo import GraphRepository


class GraphRepositoryProtocol(Protocol):
    """Graph repository methods used by Slice61 retrieval visibility."""

    def get_deal_claims_with_grades(
        self,
        *,
        tenant_id: str,
        deal_id: str,
    ) -> list[dict[str, Any]]: ...

    def get_entity_cooccurrence(
        self,
        *,
        tenant_id: str,
        deal_id: str,
    ) -> list[dict[str, Any]]: ...

    def get_claim_sanad_chain(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]: ...

    def get_independence_clusters(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]: ...

    def get_weakest_link(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]: ...


class GraphRetrievalService:
    """Run existing Neo4j Cypher retrievals and return safe counts only."""

    def __init__(self, *, graph_repo: GraphRepositoryProtocol | None = None) -> None:
        """Initialize the retrieval service."""
        self._graph_repo = graph_repo or GraphRepository()

    def retrieve_deal_graph_summary(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        claim_ids: list[str],
    ) -> dict[str, Any]:
        """Return a tenant-scoped graph retrieval summary without raw records."""
        query_summaries: list[dict[str, Any]] = []

        deal_claims = self._graph_repo.get_deal_claims_with_grades(
            tenant_id=tenant_id,
            deal_id=deal_id,
        )
        query_summaries.append(
            {"query": "deal_claims_with_grades", "record_count": len(deal_claims)}
        )

        entity_cooccurrence = self._graph_repo.get_entity_cooccurrence(
            tenant_id=tenant_id,
            deal_id=deal_id,
        )
        query_summaries.append(
            {"query": "entity_cooccurrence", "record_count": len(entity_cooccurrence)}
        )

        safe_claim_ids = sorted({claim_id for claim_id in claim_ids if claim_id})
        for claim_id in safe_claim_ids:
            chain = self._graph_repo.get_claim_sanad_chain(
                tenant_id=tenant_id,
                claim_id=claim_id,
            )
            query_summaries.append(
                {
                    "query": "claim_sanad_chain",
                    "claim_id": claim_id,
                    "record_count": len(chain),
                }
            )
            clusters = self._graph_repo.get_independence_clusters(
                tenant_id=tenant_id,
                claim_id=claim_id,
            )
            query_summaries.append(
                {
                    "query": "independence_clusters",
                    "claim_id": claim_id,
                    "record_count": len(clusters),
                }
            )
            weakest = self._graph_repo.get_weakest_link(
                tenant_id=tenant_id,
                claim_id=claim_id,
            )
            query_summaries.append(
                {
                    "query": "weakest_link",
                    "claim_id": claim_id,
                    "record_count": len(weakest),
                }
            )

        return {
            "status": "retrieved",
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_ids": safe_claim_ids,
            "retrieval_count": len(query_summaries),
            "query_summaries": query_summaries,
        }
