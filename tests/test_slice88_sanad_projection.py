"""Slice88 Task 2 — feed the persisted Sanad transmission chain into the FULL graph projection.

The Sanad chain is durably persisted on each sanad as `transmission_chain` (SanadsRepository).
The FULL graph step previously passed `transmission_nodes=[]`, so no TransmissionNode /
HAS_SANAD_STEP / INPUT / OUTPUT edges were written. Task 2 loads the persisted chain per claim and
feeds it into the existing `project_claim_sanad(transmission_nodes=...)` seam (existing schema only;
no dedicated Sanad node; no defects/deliverables/saga).
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.api.routes.runs import _normalize_input_ref, _run_full_graph_evidence
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.persistence.repositories.claims import (
    InMemorySanadsRepository,
    clear_sanad_in_memory_store,
)
from idis.services.graph.retrieval import GraphRetrievalService
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FakeGraphRepository,
    RecordingProjectionService,
)


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_sanad_in_memory_store()
    yield
    clear_sanad_in_memory_store()


def _seed_sanad_with_chain(claim_id: str, transmission_chain: list[dict[str, Any]]) -> None:
    InMemorySanadsRepository(TENANT_ID).create(
        sanad_id=f"sanad-{claim_id}",
        claim_id=claim_id,
        deal_id=DEAL_ID,
        primary_evidence_id=f"ev-{claim_id}",
        transmission_chain=transmission_chain,
    )


def _project(projection_service: RecordingProjectionService) -> dict[str, Any]:
    return _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=projection_service,
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )


def test_full_graph_step_feeds_persisted_sanad_transmission_chain() -> None:
    # Production-shaped input_refs (entity-keyed), exactly as the Sanad service persists them.
    _seed_sanad_with_chain(
        "claim-001",
        [
            {
                "node_id": "tn-1",
                "timestamp": "2026-06-29T00:00:00Z",
                "input_refs": [{"evidence_id": "ev-1"}],
            }
        ],
    )
    projection_service = RecordingProjectionService()
    summary = _project(projection_service)
    assert summary["graph_projection"]["status"] == "projected"

    claim_projection = projection_service.claim_projection_kwargs[0]
    nodes = claim_projection["transmission_nodes"]
    assert [tn["node_id"] for tn in nodes] == ["tn-1"]  # fed, not []
    # The production-shaped ref is normalized to the graph-repo {type,id} shape that routes the
    # INPUT edge (graph_repo routes on ref["type"]; an unmapped ref writes no INPUT edge).
    assert nodes[0]["input_refs"] == [{"type": "evidence", "id": "ev-1"}]
    assert summary["graph_projection"]["projected_sanad_step_count"] == 1


def test_full_graph_step_preserves_empty_chain_when_no_sanad() -> None:
    # No seeded sanad → the chain is empty (preserved behavior), not an error.
    projection_service = RecordingProjectionService()
    summary = _project(projection_service)

    claim_projection = projection_service.claim_projection_kwargs[0]
    assert claim_projection["transmission_nodes"] == []
    assert summary["graph_projection"]["projected_sanad_step_count"] == 0


# --- I-1 fix: input_ref normalization (production entity-keyed -> graph-repo {type,id}) ---


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ({"evidence_id": "ev-1"}, {"type": "evidence", "id": "ev-1"}),
        ({"span_id": "sp-1"}, {"type": "span", "id": "sp-1"}),
        ({"source_span_id": "sp-2"}, {"type": "span", "id": "sp-2"}),
        ({"claim_id": "cl-1"}, {"type": "claim", "id": "cl-1"}),
        ({"calc_id": "ca-1"}, {"type": "calculation", "id": "ca-1"}),
        ({"calculation_id": "ca-2"}, {"type": "calculation", "id": "ca-2"}),
        # already-normalized refs are preserved verbatim
        ({"type": "evidence", "id": "ev-9"}, {"type": "evidence", "id": "ev-9"}),
    ],
)
def test_normalize_input_ref_maps_production_and_preserves_normalized(
    ref: dict[str, Any], expected: dict[str, str]
) -> None:
    assert _normalize_input_ref(ref) == expected


@pytest.mark.parametrize(
    "ref",
    [
        {},  # empty
        {"unknown_key": "x"},  # unmapped entity key
        {"type": "", "id": ""},  # empty type/id
        {"type": "span"},  # missing id
        {"id": "x"},  # missing type
    ],
)
def test_normalize_input_ref_drops_malformed(ref: dict[str, Any]) -> None:
    assert _normalize_input_ref(ref) is None


def test_production_shaped_chain_produces_input_edges_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a production-shaped (entity-keyed) input_ref yields an INPUT edge.

    Exercises the REAL GraphProjectionService + GraphRepository with `execute_write` recorded
    (no real Neo4j), proving production-shaped refs route through to an INPUT-edge MERGE.
    """
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "idis.persistence.graph_repo.execute_write",
        lambda query, params: recorded.append((query, params)),
    )
    monkeypatch.setattr("idis.persistence.graph_consistency.is_neo4j_configured", lambda: True)

    _seed_sanad_with_chain(
        "claim-001",
        [
            {
                "node_id": "tn-1",
                "timestamp": "2026-06-29T00:00:00Z",
                "input_refs": [{"evidence_id": "ev-1"}],  # production-shaped
            }
        ],
    )
    _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
        # projection_service omitted -> real GraphProjectionService()
    )

    input_edge_writes = [
        (query, params)
        for query, params in recorded
        if "INPUT" in query and params.get("ref_id") == "ev-1"
    ]
    assert input_edge_writes, "production-shaped input_ref produced no INPUT edge"
