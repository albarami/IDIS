"""Slice87 Task 7 — acceptance proof for both master-plan bullets.

Bullet 1: financial claims produce calc IDs and CalcSanads.
Bullet 2: calc outputs feed analysis, debate, graph, RAG, and the VC package.

Proven with existing production seams and synthetic/injected fixtures only — no real FULL run.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from idis.analysis.models import AnalysisContext
from idis.api.routes.runs import (
    _build_analysis_calc_registry,
    _run_full_graph_evidence,
    _run_full_rag_evidence,
)
from idis.audit.sink import InMemoryAuditSink
from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.calc.runner import CalcRunner
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_calc_runner import (
    FakeClaimsRepository,
    FakeSanadsRepository,
    _money_claim,
    _sanad,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository
from tests.test_slice61_graph_visibility import FakeGraphRepository, RecordingProjectionService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_in_memory_calculations_store()
    yield
    clear_in_memory_calculations_store()


def _seed_financial_calc(*, claim_ids: tuple[str, str]) -> str:
    """Persist a real deterministic calc derived from financial input claims."""
    FormulaRegistry.reset_instance()
    engine = CalcEngine(
        registry=register_core_formulas(FormulaRegistry()), enforce_extraction_gate=False
    )
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


# --- Bullet 1: financial claims produce calc IDs and CalcSanads ---


def test_financial_claims_produce_calc_ids_and_calc_sanads() -> None:
    claims = {
        "c-cash": _money_claim("c-cash", "cash_balance", "1000000"),
        "c-burn": _money_claim("c-burn", "monthly_burn_rate", "100000"),
    }
    sanads = {"c-cash": _sanad("c-cash"), "c-burn": _sanad("c-burn")}
    calc_repo = InMemoryCalculationsRepository(TENANT_ID)
    runner = CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=calc_repo,
    )

    result = runner.run(created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY])

    # A calc ID is produced from the financial claims.
    assert len(result["calc_ids"]) == 1
    calc_id = result["calc_ids"][0]

    # ...and a durable CalcSanad links that calc to its source financial claims.
    sanad_rows = calc_repo.list_calc_sanads_by_deal(DEAL_ID)
    assert [row["calc_id"] for row in sanad_rows] == [calc_id]
    assert sorted(sanad_rows[0]["input_claim_ids"]) == ["c-burn", "c-cash"]
    assert sanad_rows[0]["calc_grade"]  # graded


# --- Bullet 2a: the calc OUTPUT is surfaced to analysis, graph, RAG, and the VC package ---


def test_calc_output_surfaced_to_analysis_graph_rag_and_vc_package(tmp_path: Path) -> None:
    calc_id = _seed_financial_calc(claim_ids=("claim-revenue", "claim-cogs"))

    # analysis: the calc (with output) is loaded into the analysis calc registry.
    registry = _build_analysis_calc_registry(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, calc_ids=[calc_id], db_conn=None
    )
    assert calc_id in registry
    assert registry[calc_id].output.get("primary_value") == "60.0000"

    # graph: the persisted calc is projected into the graph evidence.
    graph_summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_ids=[calc_id],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RecordingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )
    assert graph_summary["graph_projection"]["projected_calculation_count"] == 1

    # RAG: the calc evidence is surfaced through the RAG step.
    rag_summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        calc_ids=[calc_id],
        strict_full_live=False,
    )
    assert rag_summary["rag_calc_evidence"]["calc_ids"] == [calc_id]

    # VC package: the calc appears in the bundle calculation_package with full provenance.
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=FilesystemObjectStore(base_dir=tmp_path / "objects"),
        object_store_backend="filesystem",
    )
    ctx = AnalysisContext(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        claim_ids=frozenset({"claim-revenue", "claim-cogs"}),
        calc_ids=frozenset({calc_id}),
        calc_registry=registry,
    )
    package = exporter._calc_package(ctx)
    assert package["calc_count"] == 1
    item = package["calculations"][0]
    assert item["calc_id"] == calc_id
    assert item["reproducibility_hash"]
    assert item["calc_sanad_id"]


# --- Bullet 2b: calc_ids are fed to all five consumers via the orchestrator seams ---


def _ctx(**fns: Any) -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_k: {},
        grade_fn=lambda **_k: {},
        **fns,
    )


def _recorder(sink: dict[str, Any], base: dict[str, Any]) -> Any:
    def fn(**kwargs: Any) -> dict[str, Any]:
        sink.clear()
        sink.update(kwargs)
        return dict(base)

    return fn


def test_calc_ids_feed_analysis_debate_graph_rag_and_vc_package() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    accumulated = {"created_claim_ids": ["claim-fin"], "calc_ids": ["calc-fin-1"]}

    # analysis
    rec: dict[str, Any] = {}
    orchestrator._execute_analysis(
        _ctx(analysis_fn=_recorder(rec, {"_analysis_bundle": {}, "_analysis_context": {}})),
        accumulated,
    )
    assert rec["calc_ids"] == ["calc-fin-1"]

    # debate
    orchestrator._execute_debate(
        _ctx(debate_fn=_recorder(rec, {"debate_id": RUN_ID, "muhasabah_passed": True})),
        accumulated,
    )
    assert rec["calc_ids"] == ["calc-fin-1"]

    # graph
    orchestrator._execute_graph_evidence(
        _ctx(graph_fn=_recorder(rec, {"graph_status": "skipped"})), accumulated
    )
    assert rec["calc_ids"] == ["calc-fin-1"]

    # RAG
    orchestrator._execute_rag_evidence(
        _ctx(rag_fn=_recorder(rec, {"rag_status": "skipped"})), accumulated
    )
    assert rec["calc_ids"] == ["calc-fin-1"]

    # VC package (deliverables): calc_ids reach the deliverables step via layer2 evidence.
    deliverables_accumulated = {
        **accumulated,
        "_analysis_bundle": {},
        "_analysis_context": {},
        "_scorecard": {},
    }
    orchestrator._execute_deliverables(
        _ctx(deliverables_fn=_recorder(rec, {"deliverable_count": 1})),
        deliverables_accumulated,
    )
    assert rec["layer2_evidence"]["calc_ids"] == ["calc-fin-1"]
