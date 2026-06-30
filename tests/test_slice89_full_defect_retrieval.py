"""Slice89 Task 4 — wire projected defect IDs into the FULL graph retrieval path.

Slice88 projects defects into the graph; Task 2 taught the retrieval service to derive defect-impact
conclusions from defect IDs. Task 4 connects them: the FULL graph step now passes the projected
defect IDs into retrieval, so defect-impact conclusions flow end-to-end. Behavior is preserved when
no defects are projected.
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.api.routes.runs import _run_full_graph_evidence
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.persistence.repositories.claims import (
    InMemoryDefectsRepository,
    clear_defects_in_memory_store,
)
from idis.services.graph.retrieval import GraphRetrievalService
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingProjectionService,
)
from tests.test_slice89_graph_conclusions import _ConclusionsFakeRepo


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_defects_in_memory_store()
    yield
    clear_defects_in_memory_store()


class _RecordingRetrieval:
    """Retrieval fake that records the defect_ids it is called with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def retrieve_deal_graph_summary(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        claim_ids: list[str],
        defect_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"claim_ids": claim_ids, "defect_ids": defect_ids})
        return {
            "status": "retrieved",
            "query_summaries": [],
            "graph_conclusions": {
                "claims": [],
                "defect_impacts": [],
                "co_occurring_entity_count": 0,
            },
        }


def _seed_defect(defect_id: str, claim_id: str = "claim-001") -> None:
    InMemoryDefectsRepository(TENANT_ID).create(
        defect_id=defect_id,
        claim_id=claim_id,
        deal_id=DEAL_ID,
        defect_type="CONTRADICTION",
        severity="MAJOR",
        description="seeded",
        cure_protocol="review",
    )


def _run(retrieval_service: Any) -> dict[str, Any]:
    return _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RecordingProjectionService(),
        retrieval_service=retrieval_service,
    )


def test_full_retrieval_receives_projected_defect_ids() -> None:
    _seed_defect("def-1")
    retrieval = _RecordingRetrieval()
    _run(retrieval)
    # The projected defect id now flows into the retrieval call (omitted before Task 4).
    assert retrieval.calls[0]["defect_ids"] == ["def-1"]


def test_full_retrieval_produces_defect_impact_conclusions_when_defects_projected() -> None:
    _seed_defect("def-1")
    summary = _run(GraphRetrievalService(graph_repo=_ConclusionsFakeRepo()))
    defect_impacts = summary["graph_retrieval"]["graph_conclusions"]["defect_impacts"]
    # get_defect_impact ran in FULL because a defect was projected.
    assert [impact["defect_id"] for impact in defect_impacts] == ["def-1"]


def test_full_retrieval_no_defects_preserves_behavior() -> None:
    # No projected defects → no defect-impact conclusions (preserved behavior).
    summary = _run(GraphRetrievalService(graph_repo=_ConclusionsFakeRepo()))
    assert summary["graph_retrieval"]["graph_conclusions"]["defect_impacts"] == []
