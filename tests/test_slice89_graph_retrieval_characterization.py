"""Slice89 Task 1 — characterization pinning the CURRENT graph-retrieval truth.

The Neo4j read machinery is built and FULL already INVOKES retrieval and strict-blocks on failure,
but retrieval surfaces only COUNTS — the graph-derived conclusions never reach the consumers or the
VC package. This pins the in-scope gaps Slice89 will close (per locked decisions DEC-1..DEC-5,
acceptance-first scope):

  1. (G1 closed, Task 2) `retrieve_deal_graph_summary` surfaces `graph_conclusions` alongside the
     counts `query_summaries`.
  2. (G2 closed, Task 2) `GraphRepository.get_defect_impact` is now called by the retrieval service
     (and declared on the Protocol).
  3. FULL invokes retrieval and strict-blocks on failure (`GRAPH_RETRIEVAL_BLOCKED`).
  4. (G6) Layer 2 consumes ref-ids/counts only, never graph-derived conclusions.
  5. (G7 closed, Task 3) The deliverables generator renders graph_conclusions as VC facts.
  6. (G3/G4/G5) analysis + scoring payloads carry no graph data; debate's conflicts list is empty.
  7. (G8 closed, Task 6) the strict readiness doc is reconciled: retrieval is wired into FULL and
     feeds the VC package; analysis/debate/scoring/Layer 2 consumer feeds are a later follow-on.

GREEN-on-arrival expected (characterization pins current truth). Any RED → STOP + report.
No production changes.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import idis.deliverables.generator as generator_module
import idis.services.runs.layer2_ic_challenge as layer2_module
from idis.analysis.agents.llm_specialist_agent import (
    _build_context_payload as analysis_build_payload,
)
from idis.analysis.scoring.llm_scorecard_runner import (
    _build_context_payload as scoring_build_payload,
)
from idis.api.routes.runs import _run_full_debate, _run_full_graph_evidence
from idis.persistence.graph_repo import GraphRepository
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.services.graph.retrieval import GraphRepositoryProtocol, GraphRetrievalService
from idis.services.runs.layer2_ic_challenge import _graph_ref_ids
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FakeGraphRepository,
    RecordingProjectionService,
)

_READINESS_DOC = Path("docs/architecture/strict_full_live_readiness.md")


class _FailingRetrievalService:
    """Retrieval fake that reports failure (drives the strict block)."""

    def retrieve_deal_graph_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "failed", "retrieval_count": 0, "query_summaries": []}


# --- G1 closed (Task 2): retrieval surfaces conclusions alongside the counts ---


def test_retrieval_surfaces_conclusions_alongside_counts() -> None:
    service = GraphRetrievalService(graph_repo=FakeGraphRepository())
    summary = service.retrieve_deal_graph_summary(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, claim_ids=["claim-001"]
    )
    assert summary["status"] == "retrieved"
    # Counts preserved...
    assert all("record_count" in qs for qs in summary["query_summaries"])
    # ...and graph-derived conclusions are now surfaced (G1 closed, Task 2).
    assert set(summary["graph_conclusions"]) == {
        "claims",
        "defect_impacts",
        "co_occurring_entity_count",
    }


# --- G2 closed (Task 2): defect-impact query wired into retrieval ---


def test_get_defect_impact_wired_into_retrieval() -> None:
    assert hasattr(GraphRepository, "get_defect_impact")  # the repo method is built...
    # ...and retrieval now calls it, with the Protocol declaring it (G2 closed, Task 2).
    assert "get_defect_impact" in inspect.getsource(GraphRetrievalService)
    assert "get_defect_impact" in inspect.getsource(GraphRepositoryProtocol)


# --- FULL invokes retrieval and strict-blocks on failure ---


def test_full_invokes_retrieval_and_strict_blocks_on_failure() -> None:
    assert "_retrieve_graph_evidence" in inspect.getsource(_run_full_graph_evidence)
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_graph_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            created_claim_ids=["claim-001"],
            calc_ids=[],
            strict_full_live=True,
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
            projection_service=RecordingProjectionService(),
            retrieval_service=_FailingRetrievalService(),
        )
    assert exc_info.value.code == "GRAPH_RETRIEVAL_BLOCKED"


# --- G6: Layer 2 consumes ref-ids/counts only ---


def test_layer2_consumes_ref_ids_and_counts_only() -> None:
    # _graph_ref_ids extracts explicit ref-ids when present...
    assert _graph_ref_ids({"graph_retrieval": {"graph_ref_ids": ["claim-001"]}}) == ["claim-001"]
    # ...but for the real counts summary (query_summaries only) it currently returns [] — the
    # query_summaries fallback is unreachable since `raw = ... or []` is always a list. Either way,
    # Layer 2 consumes ref-ids/counts only, never graph-derived conclusions (G6).
    assert (
        _graph_ref_ids({"graph_retrieval": {"query_summaries": [{"claim_id": "claim-001"}]}}) == []
    )
    assert "graph_conclusions" not in inspect.getsource(layer2_module)


# --- G7 closed (Task 3): VC package renders graph-derived conclusions with provenance ---


def test_vc_package_generator_renders_graph_derived_conclusions() -> None:
    gen_src = inspect.getsource(generator_module)
    # The deliverables generator now renders graph_conclusions as facts (G7 closed, Task 3).
    assert "graph_conclusions" in gen_src
    assert "Graph-derived" in gen_src


# --- G3/G4/G5: analysis/scoring/debate do not consume graph conclusions ---


def test_analysis_debate_scoring_payloads_ignore_graph() -> None:
    assert "graph" not in inspect.getsource(analysis_build_payload)  # G3
    assert "graph" not in inspect.getsource(scoring_build_payload)  # G5
    # Debate passes an empty conflicts list — graph-derived contradictions are not populated (G4).
    assert "conflicts=[]" in inspect.getsource(_run_full_debate)


# --- G8 closed (Task 6): readiness doc reconciled to the wired reality ---


def test_readiness_doc_reconciled_graph_retrieval_wording() -> None:
    doc = _READINESS_DOC.read_text(encoding="utf-8")
    # The stale "later slice / not wired into FULL" wording is gone...
    assert "Graph retrieval into analysis/debate is a later slice" not in doc
    assert "Wire Neo4j graph retrieval into FULL" not in doc
    # ...replaced by the wired reality: conclusions feed the VC package; consumers deferred.
    assert "graph-derived conclusions" in doc
    assert "consumer feeds are a later follow-on" in doc
