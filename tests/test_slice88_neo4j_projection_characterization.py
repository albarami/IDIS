"""Slice88 Task 1 — characterization pinning the CURRENT Neo4j-projection truth.

The Neo4j machinery is almost entirely built (GraphProjectionService, GraphRepository, fail-closed
driver, tenant isolation, the dual-write saga, a locked 12-node/11-edge schema) and the FULL graph
step is wired (since Slice61) and strict fail-closed. This pins the in-scope gaps Slice88 will close
(per locked decisions D-A..D-D):

  1. (Task 2 closed G1) The FULL graph step now FEEDS the persisted Sanad transmission chain into
     project_claim_sanad; it no longer passes transmission_nodes=[].
  2. (Task 3 closed G2) The FULL graph step now FEEDS persisted defects into project_claim_sanad.
  3. Deliverables projection conflicts with the locked schema — no Deliverable node, len(NodeLabel)
     == 12, no project_deliverable method (D-A: deferred, schema not extended).
  4. The dual-write saga EXISTS but is NOT wired into the FULL projection (D-B: leave unwired).
  5. (Task 4) The strict readiness doc is reconciled to the wired reality (FULL calls
     GraphProjectionService; Sanad chain + defects fed) — G5 closed.
  6. Existing strict fail-closed graph behavior is intact (GRAPH_HEALTH_BLOCKED).

GREEN-on-arrival expected. No production/schema/implementation changes. Any RED → STOP + report.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from idis.api.routes.runs import _project_graph_evidence, _run_full_graph_evidence
from idis.persistence.graph_consistency import GraphProjectionService
from idis.persistence.neo4j_driver import Neo4jHealthCheck, NodeLabel
from idis.persistence.saga import DualWriteSagaExecutor
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FakeGraphRepository,
    RecordingProjectionService,
)

_SRC = Path("src/idis")


def _project_full_graph(projection_service: RecordingProjectionService) -> dict[str, object]:
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


# --- G1 + G2 fed (Tasks 2-3): the FULL step now feeds the Sanad chain and defects ---


def test_full_graph_step_feeds_sanads_and_defects() -> None:
    projection_service = RecordingProjectionService()
    summary = _project_full_graph(projection_service)
    assert summary["graph_projection"]["status"] == "projected"

    claim_projection = projection_service.claim_projection_kwargs[0]
    assert "evidence_items" in claim_projection
    # G1 + G2 closed: both the Sanad chain and defects are now fed (empty here only because none
    # are seeded; the real feeds are proven in the sanad/defect projection suites).
    assert claim_projection["transmission_nodes"] == []
    assert claim_projection["defects"] == []

    src = inspect.getsource(_project_graph_evidence)
    assert "transmission_nodes=claim_transmission_nodes" in src  # G1 closed (Task 2)
    assert "transmission_nodes=[]" not in src
    assert "defects=claim_defects" in src  # G2 closed (Task 3)


# --- D-A: deliverables projection conflicts with the locked schema (deferred this slice) ---


def test_deliverables_projection_conflicts_with_locked_schema() -> None:
    labels = {label.value for label in NodeLabel}
    assert "Deliverable" not in labels  # no Deliverable node in the canonical schema
    assert len(NodeLabel) == 12  # schema locked at 12 (contract-tested) — no room added
    assert not hasattr(GraphProjectionService, "project_deliverable")


# --- D-B: the dual-write saga exists but is not wired into the FULL projection ---


def test_consistency_saga_exists_but_not_wired_into_full_projection() -> None:
    assert callable(DualWriteSagaExecutor)  # the saga executor is built
    src = inspect.getsource(_project_graph_evidence)
    assert "saga" not in src.lower()  # FULL projection calls project_* directly, no saga


# --- G5 closed (Task 4): readiness doc reconciled to the wired reality ---


def test_strict_readiness_doc_reconciled_graph_wording() -> None:
    doc = Path("docs/architecture/strict_full_live_readiness.md").read_text(encoding="utf-8")
    # The stale "not wired" wording is gone...
    assert "FULL does not call `GraphProjectionService`" not in doc
    # ...replaced by the wired reality, including the Sanad-chain + defect feeds.
    assert "FULL calls `GraphProjectionService`" in doc
    assert "Sanad transmission chain" in doc
    assert "defects" in doc


# --- G6 boundary: existing strict fail-closed graph behavior is intact ---


def test_strict_fail_closed_graph_behavior_intact() -> None:
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_graph_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            created_claim_ids=["claim-001"],
            calc_ids=[],
            strict_full_live=True,
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.missing(
                missing_env_vars=["NEO4J_URI"]
            ),
        )
    assert exc_info.value.code == "GRAPH_HEALTH_BLOCKED"
