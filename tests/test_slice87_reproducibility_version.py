"""Slice87 Task 6 — reproducibility/version polish.

Two additive, preserving changes:

  - formula_version is surfaced where calc metadata is already surfaced (the analysis calc registry
    and the VC bundle calculation_package). It is resolved by registry lookup with a formula_hash
    guard, so it is never invented and the reproducibility hash is untouched. No model field, no DB
    column, no migration. The financial table is intentionally not changed.

  - verify_reproducibility is exercised through an acceptance proof: a real calc verifies clean,
    re-running identical inputs is deterministic, and a tampered calc is rejected.

Existing formula_hash/code_version/reproducibility_hash fields are preserved. No graph/RAG changes,
no financial-table changes, no new formulas, no Slice88.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from idis.analysis.models import AnalysisCalcReference, AnalysisContext
from idis.api.routes.runs import _build_analysis_calc_registry
from idis.calc.engine import CalcEngine, CalcIntegrityError, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas, resolve_formula_version
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
_HASH = "a" * 64


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_in_memory_calculations_store()
    yield
    clear_in_memory_calculations_store()


def _engine() -> CalcEngine:
    FormulaRegistry.reset_instance()
    return CalcEngine(
        registry=register_core_formulas(FormulaRegistry()), enforce_extraction_gate=False
    )


def _seed_gross_margin_calc() -> str:
    result = _engine().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": Decimal("1000"), "cogs": Decimal("400")},
        input_grades=[
            InputGradeInfo(claim_id="claim-revenue", grade=SanadGrade.A),
            InputGradeInfo(claim_id="claim-cogs", grade=SanadGrade.A),
        ],
    )
    InMemoryCalculationsRepository(TENANT_ID).create(
        calculation=result.calculation, calc_sanad=result.calc_sanad
    )
    return result.calculation.calc_id


# --- resolve_formula_version: registry lookup with formula_hash guard ---


def test_resolve_formula_version_matches_and_guards() -> None:
    registry = register_core_formulas(FormulaRegistry())
    gm_hash = registry.get_or_raise(CalcType.GROSS_MARGIN).formula_hash

    assert resolve_formula_version(CalcType.GROSS_MARGIN, gm_hash) == "1.0.0"
    # hash mismatch (e.g. a different/older formula version) → not surfaced
    assert resolve_formula_version(CalcType.GROSS_MARGIN, "deadbeef") is None
    # unknown calc type → not surfaced
    assert resolve_formula_version("NOT_A_CALC_TYPE", gm_hash) is None


# --- surfacing through the analysis calc registry ---


def test_build_analysis_calc_registry_surfaces_formula_version() -> None:
    calc_id = _seed_gross_margin_calc()

    registry = _build_analysis_calc_registry(
        tenant_id=TENANT_ID, deal_id=DEAL_ID, calc_ids=[calc_id], db_conn=None
    )

    ref = registry[calc_id]
    assert ref.formula_version == "1.0.0"
    # existing fields preserved
    assert ref.formula_hash
    assert ref.code_version
    assert ref.reproducibility_hash


# --- surfacing in the VC bundle calculation_package ---


def test_calc_package_emits_formula_version(tmp_path: Path) -> None:
    from idis.deliverables.product_bundle import ProductBundleExporter

    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=FilesystemObjectStore(base_dir=tmp_path / "objects"),
        object_store_backend="filesystem",
    )
    ctx = AnalysisContext(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id="run-001",
        claim_ids=frozenset({"claim-001"}),
        calc_ids=frozenset({"calc-001"}),
        calc_registry={
            "calc-001": AnalysisCalcReference(
                calc_id="calc-001",
                calc_type="GROSS_MARGIN",
                input_claim_ids=["claim-001"],
                reproducibility_hash=_HASH,
                calc_sanad_id="sanad-001",
                formula_hash="f" * 64,
                code_version="test-1.0.0",
                formula_version="1.0.0",
                output={"primary_value": "60.0000"},
                calc_grade="A",
            )
        },
    )

    package = exporter._calc_package(ctx)
    item = package["calculations"][0]
    assert item["formula_version"] == "1.0.0"
    # existing fields preserved
    assert item["formula_hash"] == "f" * 64
    assert item["code_version"] == "test-1.0.0"
    assert item["reproducibility_hash"] == _HASH


# --- verify_reproducibility acceptance proof ---


def test_verify_reproducibility_pass_determinism_and_tamper() -> None:
    engine = _engine()
    inputs: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "calc_type": CalcType.GROSS_MARGIN,
        "input_values": {"revenue": Decimal("1000"), "cogs": Decimal("400")},
        "input_grades": [InputGradeInfo(claim_id="claim-revenue", grade=SanadGrade.A)],
    }
    result = engine.run(**inputs)

    # A genuine calc verifies clean.
    engine.verify_reproducibility(result.calculation)

    # Identical inputs are deterministic (same reproducibility hash).
    again = engine.run(**inputs)
    assert again.calculation.reproducibility_hash == result.calculation.reproducibility_hash

    # A tampered output is rejected.
    tampered = result.calculation.model_copy(
        update={
            "output": result.calculation.output.model_copy(
                update={"primary_value": Decimal("999.9999")}
            )
        }
    )
    with pytest.raises(CalcIntegrityError):
        engine.verify_reproducibility(tampered)
