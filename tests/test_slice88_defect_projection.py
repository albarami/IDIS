"""Slice88 Task 3 — feed persisted defects into the FULL graph projection.

Defects are durably persisted per claim (DefectsRepository). The FULL graph step previously called
project_claim_sanad without `defects=`, so no Defect / HAS_DEFECT edges were written. Task 3 loads
the persisted defects per claim and feeds them into the existing project_claim_sanad(defects=...)
seam (existing schema only — Defect node already exists; no schema change; no deliverables/saga).
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
    FakeGraphRepository,
    RecordingProjectionService,
)


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_defects_in_memory_store()
    yield
    clear_defects_in_memory_store()


def _seed_defect(claim_id: str, *, defect_id: str, defect_type: str, severity: str) -> None:
    InMemoryDefectsRepository(TENANT_ID).create(
        defect_id=defect_id,
        claim_id=claim_id,
        deal_id=DEAL_ID,
        defect_type=defect_type,
        severity=severity,
        description="seeded defect",
        cure_protocol="review",
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


def test_full_graph_step_feeds_persisted_defects() -> None:
    _seed_defect("claim-001", defect_id="def-1", defect_type="CONTRADICTION", severity="MAJOR")
    projection_service = RecordingProjectionService()
    summary = _project(projection_service)
    assert summary["graph_projection"]["status"] == "projected"

    claim_projection = projection_service.claim_projection_kwargs[0]
    defects = claim_projection["defects"]
    assert [d["defect_id"] for d in defects] == ["def-1"]  # fed, not omitted
    assert defects[0]["defect_type"] == "CONTRADICTION"
    assert defects[0]["severity"] == "MAJOR"
    assert summary["graph_projection"]["projected_defect_count"] == 1


def test_full_graph_step_passes_empty_defects_when_none() -> None:
    # No seeded defect → defects passed as [] (not omitted/None), preserved behavior.
    projection_service = RecordingProjectionService()
    summary = _project(projection_service)

    claim_projection = projection_service.claim_projection_kwargs[0]
    assert claim_projection["defects"] == []
    assert summary["graph_projection"]["projected_defect_count"] == 0
