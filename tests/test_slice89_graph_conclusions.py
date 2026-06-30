"""Slice89 Task 2 — graph-derived conclusions foundation (deterministic, tenant-safe).

The retrieval service surfaces a `graph_conclusions` structure derived from the existing query
records — using ONLY safe fields (ids, grades, statuses, counts), never raw spans/text/paths/source
names — while preserving the existing counts `query_summaries`. The previously-unwired defect-impact
query is wired in (called only when defect_ids are supplied). Derivation is pure and deterministic.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from idis.services.graph.retrieval import GraphRetrievalService, _defect_conclusion
from tests.test_slice61_graph_visibility import DEAL_ID, TENANT_ID


class _ConclusionsFakeRepo:
    """Repo fake returning realistic records (safe + raw fields) for conclusion derivation."""

    def get_deal_claims_with_grades(self, *, tenant_id: str, deal_id: str) -> list[dict[str, Any]]:
        return [{"claim.claim_id": "claim-001", "claim.claim_text": "RAW must not leak"}]

    def get_entity_cooccurrence(self, *, tenant_id: str, deal_id: str) -> list[dict[str, Any]]:
        return [
            {"entity.name": "ACME Corp", "doc_count": 2},
            {"entity.name": "Beta", "doc_count": 3},
        ]

    def get_claim_sanad_chain(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        return [{"chain_depth": 4, "doc": {"uri": "/private/secret.pdf"}}]

    def get_independence_clusters(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        return [{"independent_source_count": 3, "corroboration_status": "MUTAWATIR"}]

    def get_weakest_link(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        return [{"min_grade": "B", "source_system": "SECRET_SYS"}]

    def get_defect_impact(self, *, tenant_id: str, defect_id: str) -> list[dict[str, Any]]:
        # Mirrors aliased q_4_4_5 output: affected_claims is the full map literal
        # {claim_id, claim_text, materiality, grade}.
        return [
            {
                "defect_type": "CONTRADICTION",
                "severity": "MAJOR",
                "affected_claims": [
                    {"claim_id": "claim-001", "claim_text": "RAW", "materiality": 0.9, "grade": "B"}
                ],
                "affected_calculations": ["calc-001"],
            }
        ]


def _summary(
    *, claim_ids: list[str] | None = None, defect_ids: list[str] | None = None
) -> dict[str, Any]:
    service = GraphRetrievalService(graph_repo=_ConclusionsFakeRepo())
    return service.retrieve_deal_graph_summary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claim_ids=claim_ids if claim_ids is not None else ["claim-001"],
        defect_ids=defect_ids,
    )


def test_retrieval_surfaces_graph_conclusions_alongside_counts() -> None:
    summary = _summary(defect_ids=["def-1"])
    # Counts preserved.
    assert any(qs["query"] == "weakest_link" for qs in summary["query_summaries"])
    # Per-claim conclusions derived from safe fields only.
    gc = summary["graph_conclusions"]
    assert gc["claims"] == [
        {
            "claim_id": "claim-001",
            "chain_depth": 4,
            "weakest_grade": "B",
            "corroboration_status": "MUTAWATIR",
            "independent_source_count": 3,
        }
    ]
    assert gc["co_occurring_entity_count"] == 2


def test_defect_impact_wired_into_retrieval() -> None:
    gc = _summary(defect_ids=["def-1"])["graph_conclusions"]
    assert gc["defect_impacts"] == [
        {
            "defect_id": "def-1",
            "defect_type": "CONTRADICTION",
            "severity": "MAJOR",
            "affected_claim_ids": ["claim-001"],
            "affected_calc_ids": ["calc-001"],
        }
    ]
    assert "get_defect_impact" in inspect.getsource(GraphRetrievalService)  # G2 wired


def test_no_defect_ids_means_no_defect_conclusions() -> None:
    gc = _summary(defect_ids=None)["graph_conclusions"]
    assert gc["defect_impacts"] == []


def test_defect_impact_query_aliases_scalars_and_conclusion_populates() -> None:
    # The Neo4j driver keys an unaliased `RETURN n.prop` as "n.prop"; `_defect_conclusion` reads the
    # bare "defect_type"/"severity", so the query MUST alias these scalars (as every other q_4_4_*
    # query aliases its scalars). Without the alias, defect_type/severity are empty in production.
    from idis.persistence.cypher.q_4_4_5_defect_impact import QUERY

    assert "AS defect_type" in QUERY
    assert "AS severity" in QUERY

    # A production-shaped (aliased) record populates defect_type/severity; raw claim fields drop.
    record = {
        "defect_type": "CONTRADICTION",
        "severity": "MAJOR",
        "affected_claims": [
            {"claim_id": "claim-001", "claim_text": "RAW", "materiality": 0.9, "grade": "B"}
        ],
        "affected_calculations": ["calc-001"],
    }
    conclusion = _defect_conclusion("def-1", records=[record])
    assert conclusion["defect_type"] == "CONTRADICTION"
    assert conclusion["severity"] == "MAJOR"
    assert conclusion["affected_claim_ids"] == ["claim-001"]
    assert conclusion["affected_calc_ids"] == ["calc-001"]


def test_graph_conclusions_omit_raw_private_fields() -> None:
    encoded = json.dumps(_summary(defect_ids=["def-1"])["graph_conclusions"], sort_keys=True)
    for leak in ("RAW", "/private", "SECRET_SYS", "ACME", "Beta"):
        assert leak not in encoded


def test_graph_conclusions_deterministic_and_sorted() -> None:
    service = GraphRetrievalService(graph_repo=_ConclusionsFakeRepo())
    kwargs: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "claim_ids": ["claim-002", "claim-001"],
        "defect_ids": ["def-2", "def-1"],
    }
    first = service.retrieve_deal_graph_summary(**kwargs)
    second = service.retrieve_deal_graph_summary(**kwargs)
    assert first["graph_conclusions"] == second["graph_conclusions"]  # stable
    claim_ids = [c["claim_id"] for c in first["graph_conclusions"]["claims"]]
    assert claim_ids == sorted(claim_ids)  # sorted
    defect_ids = [d["defect_id"] for d in first["graph_conclusions"]["defect_impacts"]]
    assert defect_ids == sorted(defect_ids)  # sorted
