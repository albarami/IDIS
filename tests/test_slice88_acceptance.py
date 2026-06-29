"""Slice88 Task 5 — acceptance proof for the master-plan Neo4j-projection acceptance.

Acceptance: a FULL strict run writes graph projections OR blocks safely.

  - When Neo4j is healthy, the FULL strict graph step writes the in-schema projection set: claims,
    evidence, the Sanad transmission chain, defects, and calculations (deliverables are deferred —
    no Deliverable node in the locked schema, so they are never projected).
  - When Neo4j is missing/unhealthy or projection fails, the FULL strict graph step blocks safely
    (RunStepBlockedError) rather than writing partial/unsafe graph state.

Proven with injected fake graph health/projection/retrieval services — no real Neo4j run.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import pytest

from idis.api.routes.runs import _run_full_graph_evidence
from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.graph_consistency import GraphProjectionService
from idis.persistence.neo4j_driver import Neo4jHealthCheck, NodeLabel
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.persistence.repositories.claims import (
    InMemoryDefectsRepository,
    InMemorySanadsRepository,
    clear_defects_in_memory_store,
    clear_sanad_in_memory_store,
)
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FailingProjectionService,
    FakeGraphRepository,
    RecordingProjectionService,
)


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()
    clear_in_memory_calculations_store()
    yield
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()
    clear_in_memory_calculations_store()


def _seed_full_claim_graph() -> str:
    """Persist a claim's Sanad transmission chain, a defect, and a calculation."""
    InMemorySanadsRepository(TENANT_ID).create(
        sanad_id="sanad-001",
        claim_id="claim-001",
        deal_id=DEAL_ID,
        primary_evidence_id="ev-001",
        transmission_chain=[
            {
                "node_id": "tn-1",
                "timestamp": "2026-06-29T00:00:00Z",
                # Production-shaped (entity-keyed) input_refs, as the Sanad service persists them.
                "input_refs": [{"evidence_id": "ev-001"}],
            }
        ],
    )
    InMemoryDefectsRepository(TENANT_ID).create(
        defect_id="def-1",
        claim_id="claim-001",
        deal_id=DEAL_ID,
        defect_type="CONTRADICTION",
        severity="MAJOR",
        description="seeded",
        cure_protocol="review",
    )
    FormulaRegistry.reset_instance()
    engine = CalcEngine(
        registry=register_core_formulas(FormulaRegistry()), enforce_extraction_gate=False
    )
    result = engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": Decimal("1000"), "cogs": Decimal("400")},
        input_grades=[
            InputGradeInfo(claim_id="claim-001", grade=SanadGrade.A),
            InputGradeInfo(claim_id="claim-002", grade=SanadGrade.A),
        ],
    )
    InMemoryCalculationsRepository(TENANT_ID).create(
        calculation=result.calculation, calc_sanad=result.calc_sanad
    )
    return result.calculation.calc_id


def _run_strict_graph(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "documents": [],
        "created_claim_ids": ["claim-001"],
        "calc_ids": [],
        "strict_full_live": True,
        "neo4j_health_checker": lambda _env: Neo4jHealthCheck.healthy(),
        "retrieval_service": GraphRetrievalService(graph_repo=FakeGraphRepository()),
    }
    kwargs.update(overrides)
    return _run_full_graph_evidence(**kwargs)


# --- Acceptance 1: strict run WRITES the full in-schema projection set when healthy ---


def test_full_strict_graph_writes_full_in_schema_projection_set() -> None:
    calc_id = _seed_full_claim_graph()
    projection_service = RecordingProjectionService()

    summary = _run_strict_graph(calc_ids=[calc_id], projection_service=projection_service)

    assert summary["graph_status"] == "available"  # wrote, did not block
    claim_projection = projection_service.claim_projection_kwargs[0]
    # The full in-schema set is fed to project_claim_sanad.
    assert [tn["node_id"] for tn in claim_projection["transmission_nodes"]] == [
        "tn-1"
    ]  # Sanad chain
    assert [d["defect_id"] for d in claim_projection["defects"]] == ["def-1"]  # defects
    assert [c["calc_id"] for c in claim_projection["calculations"]] == [calc_id]  # calculations
    assert "evidence_items" in claim_projection  # evidence seam present

    projection = summary["graph_projection"]
    assert projection["status"] == "projected"
    assert projection["projected_claim_count"] == 1
    assert projection["projected_sanad_step_count"] == 1
    assert projection["projected_defect_count"] == 1
    assert projection["projected_calculation_count"] == 1

    # Deliverables are deferred — never projected.
    assert "deliverables" not in claim_projection


# --- Acceptance 2: strict run BLOCKS safely on missing/unhealthy/failed graph ---


def test_full_strict_graph_blocks_safely_when_neo4j_unhealthy() -> None:
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_strict_graph(
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.missing(
                missing_env_vars=["NEO4J_URI"]
            ),
            projection_service=RecordingProjectionService(),
        )
    assert exc_info.value.code == "GRAPH_HEALTH_BLOCKED"


def test_full_strict_graph_blocks_safely_when_projection_fails() -> None:
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_strict_graph(projection_service=FailingProjectionService())
    assert exc_info.value.code == "GRAPH_PROJECTION_BLOCKED"


# --- Acceptance 3: deliverables remain deferred (no projection seam, no node) ---


def test_deliverables_remain_deferred_no_seam_no_node() -> None:
    sig = inspect.signature(GraphProjectionService.project_claim_sanad)
    assert "deliverables" not in sig.parameters  # no deliverables seam on the service
    assert not hasattr(GraphProjectionService, "project_deliverable")
    assert "Deliverable" not in {label.value for label in NodeLabel}  # not in the locked schema
