"""Neo4j-backed graph repository for IDIS Sanad graph projection.

Provides tenant-scoped graph operations for the §4.4 query patterns
and projection upserts. Postgres remains the source of truth.

All methods require tenant_id for tenant isolation enforcement.
"""

from __future__ import annotations

import logging
from typing import Any

from idis.persistence.cypher.q_4_4_1_full_chain import build_full_chain_query
from idis.persistence.cypher.q_4_4_2_deal_claims_grades import build_deal_claims_grades_query
from idis.persistence.cypher.q_4_4_3_independence_clusters import (
    build_independence_clusters_query,
)
from idis.persistence.cypher.q_4_4_4_weakest_link import build_weakest_link_query
from idis.persistence.cypher.q_4_4_5_defect_impact import build_defect_impact_query
from idis.persistence.cypher.q_4_4_6_entity_cooccurrence import build_entity_cooccurrence_query
from idis.persistence.neo4j_driver import (
    EdgeType,
    NodeLabel,
    execute_read,
    execute_write,
)

logger = logging.getLogger(__name__)


class GraphProjectionError(Exception):
    """Raised when a graph projection operation fails.

    Fail-closed: the caller must handle this as a structured failure,
    not silently skip the projection.
    """


class GraphRepository:
    """Neo4j-backed repository for Sanad graph projection and queries.

    All operations are tenant-scoped. No cross-tenant traversal is possible
    because every node carries tenant_id and every query filters on it.
    """

    def upsert_deal_graph_projection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
        spans: list[dict[str, Any]],
        entities: list[dict[str, Any]] | None = None,
    ) -> None:
        """Project deal structure (Deal→Document→Span) into Neo4j.

        Uses MERGE to be idempotent. Creates Deal, Document, and Span
        nodes with HAS_DOCUMENT and HAS_SPAN edges.

        Args:
            tenant_id: Tenant UUID for isolation.
            deal_id: Deal UUID.
            documents: List of document dicts with document_id, doc_type.
            spans: List of span dicts with span_id, document_id, span_type.
            entities: Optional list of entity dicts with entity_id, name, type.

        Raises:
            GraphProjectionError: If projection fails.
        """
        try:
            query = f"""\
MERGE (deal:{NodeLabel.DEAL} {{deal_id: $deal_id, tenant_id: $tenant_id}})
SET deal.updated_at = datetime()
"""
            execute_write(query, {"deal_id": deal_id, "tenant_id": tenant_id})

            for doc in documents:
                query = f"""\
MERGE (doc:{NodeLabel.DOCUMENT} {{document_id: $document_id, tenant_id: $tenant_id}})
SET doc.doc_type = $doc_type, doc.updated_at = datetime()
WITH doc
MATCH (deal:{NodeLabel.DEAL} {{deal_id: $deal_id, tenant_id: $tenant_id}})
MERGE (deal)-[:{EdgeType.HAS_DOCUMENT}]->(doc)
"""
                execute_write(query, {
                    "document_id": doc["document_id"],
                    "doc_type": doc.get("doc_type", ""),
                    "deal_id": deal_id,
                    "tenant_id": tenant_id,
                })

            for span in spans:
                query = f"""\
MERGE (span:{NodeLabel.SPAN} {{span_id: $span_id, tenant_id: $tenant_id}})
SET span.span_type = $span_type, span.updated_at = datetime()
WITH span
MATCH (doc:{NodeLabel.DOCUMENT} {{document_id: $document_id, tenant_id: $tenant_id}})
MERGE (doc)-[:{EdgeType.HAS_SPAN}]->(span)
"""
                execute_write(query, {
                    "span_id": span["span_id"],
                    "span_type": span.get("span_type", ""),
                    "document_id": span["document_id"],
                    "tenant_id": tenant_id,
                })

            for entity in entities or []:
                query = f"""\
MERGE (e:{NodeLabel.ENTITY} {{entity_id: $entity_id, tenant_id: $tenant_id}})
SET e.name = $name, e.type = $entity_type, e.updated_at = datetime()
"""
                execute_write(query, {
                    "entity_id": entity["entity_id"],
                    "name": entity.get("name", ""),
                    "entity_type": entity.get("type", ""),
                    "tenant_id": tenant_id,
                })

                for span_id in entity.get("span_ids", []):
                    link_query = f"""\
MATCH (e:{NodeLabel.ENTITY} {{entity_id: $entity_id, tenant_id: $tenant_id}})
MATCH (span:{NodeLabel.SPAN} {{span_id: $span_id, tenant_id: $tenant_id}})
MERGE (e)-[:{EdgeType.MENTIONED_IN}]->(span)
"""
                    execute_write(link_query, {
                        "entity_id": entity["entity_id"],
                        "span_id": span_id,
                        "tenant_id": tenant_id,
                    })

            logger.info(
                "Deal graph projection complete: deal=%s docs=%d spans=%d",
                deal_id,
                len(documents),
                len(spans),
            )

        except Exception as exc:
            raise GraphProjectionError(
                f"Failed to project deal {deal_id} into graph: {exc}"
            ) from exc

    def upsert_claim_sanad_projection(
        self,
        *,
        tenant_id: str,
        claim: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        transmission_nodes: list[dict[str, Any]],
        defects: list[dict[str, Any]] | None = None,
        calculations: list[dict[str, Any]] | None = None,
    ) -> None:
        """Project claim Sanad chain into Neo4j.

        Creates Claim, EvidenceItem, TransmissionNode nodes and their
        edges (SUPPORTED_BY, HAS_SANAD_STEP, INPUT, OUTPUT, HAS_DEFECT,
        DERIVED_FROM).

        Args:
            tenant_id: Tenant UUID for isolation.
            claim: Claim dict with claim_id, claim_text, claim_grade, etc.
            evidence_items: List of evidence dicts with evidence_id, source_grade, etc.
            transmission_nodes: List of TN dicts with node_id, timestamp, input_refs.
            defects: Optional list of defect dicts with defect_id, defect_type, severity.
            calculations: Optional list of calc dicts with calc_id, calc_type.

        Raises:
            GraphProjectionError: If projection fails.
        """
        try:
            claim_id = claim["claim_id"]

            claim_query = f"""\
MERGE (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
SET c.claim_text = $claim_text,
    c.claim_grade = $claim_grade,
    c.claim_verdict = $claim_verdict,
    c.materiality = $materiality,
    c.claim_class = $claim_class,
    c.updated_at = datetime()
"""
            execute_write(claim_query, {
                "claim_id": claim_id,
                "claim_text": claim.get("claim_text", ""),
                "claim_grade": claim.get("claim_grade", "D"),
                "claim_verdict": claim.get("claim_verdict", "UNVERIFIED"),
                "materiality": claim.get("materiality", "MEDIUM"),
                "claim_class": claim.get("claim_class", "OTHER"),
                "tenant_id": tenant_id,
            })

            for ev in evidence_items:
                ev_query = f"""\
MERGE (ev:{NodeLabel.EVIDENCE_ITEM} {{evidence_id: $evidence_id, tenant_id: $tenant_id}})
SET ev.source_grade = $source_grade,
    ev.source_system = $source_system,
    ev.upstream_origin_id = $upstream_origin_id,
    ev.updated_at = datetime()
WITH ev
MATCH (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
MERGE (c)-[:{EdgeType.SUPPORTED_BY}]->(ev)
"""
                execute_write(ev_query, {
                    "evidence_id": ev["evidence_id"],
                    "source_grade": ev.get("source_grade", "D"),
                    "source_system": ev.get("source_system", ""),
                    "upstream_origin_id": ev.get("upstream_origin_id", ""),
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                })

            for tn in transmission_nodes:
                tn_query = f"""\
MERGE (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
SET tn.timestamp = $timestamp,
    tn.updated_at = datetime()
WITH tn
MATCH (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
MERGE (c)-[:{EdgeType.HAS_SANAD_STEP}]->(tn)
"""
                execute_write(tn_query, {
                    "node_id": tn["node_id"],
                    "timestamp": tn.get("timestamp", ""),
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                })

                tn_query_output = f"""\
MATCH (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
MATCH (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
MERGE (tn)-[:{EdgeType.OUTPUT}]->(c)
"""
                execute_write(tn_query_output, {
                    "node_id": tn["node_id"],
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                })

                for input_ref in tn.get("input_refs", []):
                    ref_type = input_ref.get("type", "")
                    ref_id = input_ref.get("id", "")
                    if ref_type == "span":
                        input_q = f"""\
MATCH (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
MATCH (s:{NodeLabel.SPAN} {{span_id: $ref_id, tenant_id: $tenant_id}})
MERGE (tn)-[:{EdgeType.INPUT}]->(s)
"""
                    elif ref_type == "evidence":
                        input_q = f"""\
MATCH (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
MATCH (ev:{NodeLabel.EVIDENCE_ITEM} {{evidence_id: $ref_id, tenant_id: $tenant_id}})
MERGE (tn)-[:{EdgeType.INPUT}]->(ev)
"""
                    elif ref_type == "claim":
                        input_q = f"""\
MATCH (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
MATCH (c2:{NodeLabel.CLAIM} {{claim_id: $ref_id, tenant_id: $tenant_id}})
MERGE (tn)-[:{EdgeType.INPUT}]->(c2)
"""
                    elif ref_type == "calculation":
                        input_q = f"""\
MATCH (tn:{NodeLabel.TRANSMISSION_NODE} {{node_id: $node_id, tenant_id: $tenant_id}})
MATCH (calc:{NodeLabel.CALCULATION} {{calc_id: $ref_id, tenant_id: $tenant_id}})
MERGE (tn)-[:{EdgeType.INPUT}]->(calc)
"""
                    else:
                        continue

                    execute_write(input_q, {
                        "node_id": tn["node_id"],
                        "ref_id": ref_id,
                        "tenant_id": tenant_id,
                    })

            for defect in defects or []:
                defect_query = f"""\
MERGE (d:{NodeLabel.DEFECT} {{defect_id: $defect_id, tenant_id: $tenant_id}})
SET d.defect_type = $defect_type,
    d.severity = $severity,
    d.updated_at = datetime()
WITH d
MATCH (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
MERGE (c)-[:{EdgeType.HAS_DEFECT}]->(d)
"""
                execute_write(defect_query, {
                    "defect_id": defect["defect_id"],
                    "defect_type": defect.get("defect_type", ""),
                    "severity": defect.get("severity", "MINOR"),
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                })

            for calc in calculations or []:
                calc_query = f"""\
MERGE (calc:{NodeLabel.CALCULATION} {{calc_id: $calc_id, tenant_id: $tenant_id}})
SET calc.calc_type = $calc_type,
    calc.updated_at = datetime()
WITH calc
MATCH (c:{NodeLabel.CLAIM} {{claim_id: $claim_id, tenant_id: $tenant_id}})
MERGE (calc)-[:{EdgeType.DERIVED_FROM}]->(c)
"""
                execute_write(calc_query, {
                    "calc_id": calc["calc_id"],
                    "calc_type": calc.get("calc_type", ""),
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                })

            logger.info(
                "Claim Sanad projection complete: claim=%s evidence=%d tns=%d",
                claim_id,
                len(evidence_items),
                len(transmission_nodes),
            )

        except GraphProjectionError:
            raise
        except Exception as exc:
            raise GraphProjectionError(
                f"Failed to project claim {claim.get('claim_id', '?')} Sanad chain: {exc}"
            ) from exc

    def get_claim_sanad_chain(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.1: Full Sanad chain for a claim.

        Args:
            tenant_id: Tenant UUID.
            claim_id: Claim UUID.

        Returns:
            List of chain records ordered by transmission node timestamp ASC.
        """
        query, params = build_full_chain_query(
            claim_id=claim_id, tenant_id=tenant_id
        )
        return execute_read(query, params)

    def get_deal_claims_with_grades(
        self,
        *,
        tenant_id: str,
        deal_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.2: All claims for a deal with Sanad grades.

        Args:
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.

        Returns:
            List of claim records ordered by materiality DESC, grade ASC.
        """
        query, params = build_deal_claims_grades_query(
            deal_id=deal_id, tenant_id=tenant_id
        )
        return execute_read(query, params)

    def get_independence_clusters(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.3: Independence clusters for corroboration assessment.

        Args:
            tenant_id: Tenant UUID.
            claim_id: Claim UUID.

        Returns:
            List with corroboration status and cluster details.
        """
        query, params = build_independence_clusters_query(
            claim_id=claim_id, tenant_id=tenant_id
        )
        return execute_read(query, params)

    def get_weakest_link(
        self,
        *,
        tenant_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.4: Weakest link in transmission chain.

        Args:
            tenant_id: Tenant UUID.
            claim_id: Claim UUID.

        Returns:
            Single-record list with weakest node details, or empty.
        """
        query, params = build_weakest_link_query(
            claim_id=claim_id, tenant_id=tenant_id
        )
        return execute_read(query, params)

    def get_defect_impact(
        self,
        *,
        tenant_id: str,
        defect_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.5: Defect impact analysis.

        Args:
            tenant_id: Tenant UUID.
            defect_id: Defect UUID.

        Returns:
            List with affected claims and downstream calculations.
        """
        query, params = build_defect_impact_query(
            defect_id=defect_id, tenant_id=tenant_id
        )
        return execute_read(query, params)

    def get_entity_cooccurrence(
        self,
        *,
        tenant_id: str,
        deal_id: str,
    ) -> list[dict[str, Any]]:
        """§4.4.6: Cross-document entity co-occurrence.

        Args:
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.

        Returns:
            List of entities appearing in 2+ documents, ordered by doc_count DESC.
        """
        query, params = build_entity_cooccurrence_query(
            deal_id=deal_id, tenant_id=tenant_id
        )
        return execute_read(query, params)
