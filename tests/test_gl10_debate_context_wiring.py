"""GL-10 slice 1: FULL debate context uses persisted calculations and deal metadata."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

import idis.api.routes.runs as runs_mod
from idis.api.routes.runs import _run_full_debate
from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
MISSING_CALC_ID = "44444444-4444-4444-4444-444444444444"
CLAIM_REVENUE = "claim-revenue"
CLAIM_COGS = "claim-cogs"


class _StopAfterContext(Exception):
    """Halt once the built debate context has been captured."""


@pytest.fixture(autouse=True)
def _clear_calculations() -> Any:
    clear_in_memory_calculations_store()
    yield
    clear_in_memory_calculations_store()


def _capture_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _capture(context: Any = None, **_kwargs: Any) -> Any:
        captured["context"] = context
        raise _StopAfterContext

    monkeypatch.setattr(runs_mod, "_build_debate_role_runners", _capture)
    return captured


def _seed_gross_margin() -> Any:
    FormulaRegistry.reset_instance()
    engine = CalcEngine(
        registry=register_core_formulas(FormulaRegistry()),
        enforce_extraction_gate=False,
    )
    result = engine.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": Decimal("1000"), "cogs": Decimal("400")},
        input_grades=[
            InputGradeInfo(claim_id=CLAIM_REVENUE, grade=SanadGrade.A),
            InputGradeInfo(claim_id=CLAIM_COGS, grade=SanadGrade.B),
        ],
    )
    InMemoryCalculationsRepository(TENANT_ID).create(
        calculation=result.calculation,
        calc_sanad=result.calc_sanad,
    )
    return result


def _context_for(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    captured = _capture_context(monkeypatch)
    with pytest.raises(_StopAfterContext):
        _run_full_debate(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[],
            db_conn=None,
            **kwargs,
        )
    return captured["context"]


def test_full_debate_context_uses_persisted_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted calculation supplies name, value, claims, grade, and version."""
    result = _seed_gross_margin()
    calc_id = result.calculation.calc_id

    context = _context_for(monkeypatch, calc_ids=[calc_id])

    assert context.calc_results == [
        {
            "calc_id": calc_id,
            "calc_name": "GROSS_MARGIN",
            "result_value": "60.0000",
            "input_claim_ids": [CLAIM_COGS, CLAIM_REVENUE],
            "calc_grade": result.calc_sanad.calc_grade.value,
            "formula_version": "1.0.0",
        }
    ]
    assert context.conflicts == []


def test_requested_calc_without_persisted_row_is_marked_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested calculation id with no persisted row stays, marked missing."""
    result = _seed_gross_margin()
    present_id = result.calculation.calc_id

    context = _context_for(monkeypatch, calc_ids=[MISSING_CALC_ID, present_id])

    assert context.calc_results[0] == {
        "calc_id": MISSING_CALC_ID,
        "calc_name": "",
        "result_value": "",
        "input_claim_ids": [],
        "missing": True,
    }
    assert context.calc_results[1]["calc_id"] == present_id
    assert context.calc_results[1]["calc_name"] == "GROSS_MARGIN"
    assert "missing" not in context.calc_results[1]


def test_deal_fields_come_from_metadata_or_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deal identity comes from metadata when supplied, otherwise the prior literals."""
    described = _context_for(
        monkeypatch,
        calc_ids=[],
        deal_metadata={
            "company_name": "Northwind Analytics",
            "sector": "Software",
            "stage": "Series B",
            "description": "Revenue quality review",
            "summary": "Unused when description is present",
        },
    )
    assert described.deal_name == "Northwind Analytics"
    assert described.deal_sector == "Software"
    assert described.deal_stage == "Series B"
    assert described.deal_summary == "Revenue quality review"

    summarized = _context_for(
        monkeypatch,
        calc_ids=[],
        deal_metadata={
            "company_name": "Harbor Health",
            "sector": "Health",
            "stage": "Seed",
            "description": "",
            "summary": "Care delivery",
        },
    )
    assert summarized.deal_name == "Harbor Health"
    assert summarized.deal_sector == "Health"
    assert summarized.deal_stage == "Seed"
    assert summarized.deal_summary == "Care delivery"

    empty = _context_for(monkeypatch, calc_ids=[], deal_metadata={})
    assert empty.deal_name == DEAL_ID
    assert empty.deal_sector == "Unknown"
    assert empty.deal_stage == "Unknown"
    assert empty.deal_summary == ""

    name_only = _context_for(
        monkeypatch,
        calc_ids=[],
        deal_metadata={"company_name": "Cedar Logistics"},
    )
    assert name_only.deal_name == "Cedar Logistics"
    assert name_only.deal_sector == "Unknown"
    assert name_only.deal_stage == "Unknown"
    assert name_only.deal_summary == ""

    literals = _context_for(monkeypatch, calc_ids=[])
    assert literals.deal_name == DEAL_ID
    assert literals.deal_sector == "Unknown"
    assert literals.deal_stage == "Unknown"
    assert literals.deal_summary == ""


def test_debate_partial_carries_deal_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FULL debate partial carries deal metadata the way the analysis partial does."""
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.runs.steps import build_run_context

    metadata = {
        "company_name": "Northwind Analytics",
        "sector": "Software",
        "stage": "Series B",
        "summary": "Pipeline note",
    }
    context_obj = build_run_context(
        db_conn=None,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        deal_metadata=metadata,
        audit_sink=InMemoryAuditSink(),
    )
    assert context_obj.debate_fn is not None
    assert context_obj.debate_fn.keywords.get("deal_metadata") == metadata

    captured = _capture_context(monkeypatch)
    with pytest.raises(_StopAfterContext):
        context_obj.debate_fn(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[],
            calc_ids=[],
            rag_retrieval=None,
        )
    built = captured["context"]
    assert built.deal_name == "Northwind Analytics"
    assert built.deal_sector == "Software"
    assert built.deal_stage == "Series B"
    assert built.deal_summary == "Pipeline note"
    assert built.conflicts == []
