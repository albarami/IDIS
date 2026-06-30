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

    def get_defect_impact(
        self,
        *,
        tenant_id: str,
        defect_id: str,
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
        defect_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a tenant-scoped graph retrieval summary with safe conclusions.

        Preserves the existing counts-only ``query_summaries`` and adds ``graph_conclusions``
        derived deterministically from safe fields only (ids, grades, statuses, counts) — never
        raw spans/text/paths/source names. The defect-impact query is run only when ``defect_ids``
        are supplied.
        """
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
        claim_conclusions: list[dict[str, Any]] = []
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
            claim_conclusions.append(
                _claim_conclusion(claim_id, chain=chain, clusters=clusters, weakest=weakest)
            )

        safe_defect_ids = sorted({defect_id for defect_id in (defect_ids or []) if defect_id})
        defect_impacts: list[dict[str, Any]] = []
        for defect_id in safe_defect_ids:
            impact = self._graph_repo.get_defect_impact(
                tenant_id=tenant_id,
                defect_id=defect_id,
            )
            query_summaries.append(
                {
                    "query": "defect_impact",
                    "defect_id": defect_id,
                    "record_count": len(impact),
                }
            )
            defect_impacts.append(_defect_conclusion(defect_id, records=impact))

        return {
            "status": "retrieved",
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_ids": safe_claim_ids,
            "retrieval_count": len(query_summaries),
            "query_summaries": query_summaries,
            "graph_conclusions": {
                "claims": claim_conclusions,
                "defect_impacts": defect_impacts,
                "co_occurring_entity_count": len(entity_cooccurrence),
            },
        }


def _claim_conclusion(
    claim_id: str,
    *,
    chain: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    weakest: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive a per-claim graph conclusion from safe fields only (no raw spans/text/paths)."""
    cluster = clusters[0] if clusters else {}
    weak = weakest[0] if weakest else {}
    chain_depth = max((int(record.get("chain_depth", 0) or 0) for record in chain), default=0)
    weakest_grade = weak.get("min_grade")
    corroboration_status = cluster.get("corroboration_status")
    return {
        "claim_id": claim_id,
        "chain_depth": chain_depth,
        "weakest_grade": str(weakest_grade) if weakest_grade else None,
        "corroboration_status": str(corroboration_status) if corroboration_status else None,
        "independent_source_count": int(cluster.get("independent_source_count", 0) or 0),
    }


def _defect_conclusion(defect_id: str, *, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive a defect-impact conclusion (ids/types/severities only — never raw claim text)."""
    record = records[0] if records else {}
    affected = record.get("affected_claims") or []
    affected_claim_ids = sorted(
        {
            str(item.get("claim_id"))
            for item in affected
            if isinstance(item, dict) and item.get("claim_id")
        }
    )
    affected_calc_ids = sorted(
        {str(calc_id) for calc_id in (record.get("affected_calculations") or []) if calc_id}
    )
    return {
        "defect_id": defect_id,
        "defect_type": str(record.get("defect_type") or ""),
        "severity": str(record.get("severity") or ""),
        "affected_claim_ids": affected_claim_ids,
        "affected_calc_ids": affected_calc_ids,
    }
