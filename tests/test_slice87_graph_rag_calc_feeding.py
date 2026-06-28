"""Slice87 Task 5 — minimal additive graph/RAG calc feeding through existing FULL-step seams.

Graph: _project_graph_evidence now feeds the deal's PERSISTED deterministic calculations into the
existing GraphProjectionService.project_claim_sanad(calculations=...) seam (previously always given
an empty list), grouped by the calc's input claims, and reports projected_calculation_count. It only
projects
real persisted calcs — it never invents calc edges.

RAG: the rag FULL-step seam now receives calc_ids (threaded by the orchestrator) and additively
surfaces a rag_calc_evidence block. No pgvector calc-embedding is added (that would be new infra);
this is an additive evidence reflection only.

Both preserve existing behavior when no calc data exists. No new infrastructure. No formula or
financial-table changes. No Slice88.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from idis.api.routes.runs import _run_full_graph_evidence, _run_full_rag_evidence
from idis.audit.sink import InMemoryAuditSink
from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FakeGraphRepository,
    RecordingProjectionService,
)


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_in_memory_calculations_store()
    yield
    clear_in_memory_calculations_store()


def _seed_persisted_calc(*, claim_ids: list[str]) -> str:
    FormulaRegistry.reset_instance()
    engine = CalcEngine(registry=register_core_formulas(), enforce_extraction_gate=False)
    result = engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": Decimal("1000"), "cogs": Decimal("400")},
        input_grades=[InputGradeInfo(claim_id=cid, grade=SanadGrade.A) for cid in claim_ids],
    )
    InMemoryCalculationsRepository(TENANT_ID).create(
        calculation=result.calculation, calc_sanad=result.calc_sanad
    )
    return result.calculation.calc_id


# --- graph: feed persisted calcs through the existing project_claim_sanad seam ---


def test_graph_projection_feeds_persisted_calcs() -> None:
    calc_id = _seed_persisted_calc(claim_ids=["claim-001"])
    projection_service = RecordingProjectionService()

    summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[calc_id],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=projection_service,
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )

    assert summary["graph_projection"]["projected_calculation_count"] == 1
    claim_projection = projection_service.claim_projection_kwargs[0]
    assert [c["calc_id"] for c in claim_projection["calculations"]] == [calc_id]


def test_graph_projection_preserves_behavior_without_calc() -> None:
    projection_service = RecordingProjectionService()

    summary = _run_full_graph_evidence(
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

    assert summary["graph_projection"]["projected_calculation_count"] == 0
    assert projection_service.claim_projection_kwargs[0]["calculations"] == []


# --- RAG: additively surface calc evidence through the threaded seam ---


def test_rag_step_surfaces_calc_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDIS_ENABLE_VECTOR_SEARCH", raising=False)

    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        calc_ids=["calc-b", "calc-a"],
        strict_full_live=False,
    )

    block = summary["rag_calc_evidence"]
    assert block["status"] == "calc_evidence_available"
    assert block["calc_count"] == 2
    assert block["calc_ids"] == ["calc-a", "calc-b"]  # sorted


def test_rag_step_preserves_behavior_without_calc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDIS_ENABLE_VECTOR_SEARCH", raising=False)

    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        calc_ids=[],
        strict_full_live=False,
    )

    assert summary["rag_status"] == "skipped"  # existing behavior preserved
    assert summary["rag_calc_evidence"]["status"] == "no_calc_evidence"
    assert summary["rag_calc_evidence"]["calc_count"] == 0


# --- orchestrator threads accumulated calc_ids into the rag seam ---


def test_orchestrator_threads_calc_ids_to_rag() -> None:
    recorded: dict[str, Any] = {}

    def recording_rag_fn(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"rag_status": "skipped"}

    ctx = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_k: {},
        grade_fn=lambda **_k: {},
        rag_fn=recording_rag_fn,
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    orchestrator._execute_rag_evidence(
        ctx, {"calc_ids": ["calc-001"], "created_claim_ids": ["claim-001"]}
    )

    assert recorded.get("calc_ids") == ["calc-001"]
