"""Slice87 Task 1 — characterization pinning the CURRENT calculation-path truth.

GREEN-on-arrival expected: one production CalcEngine/CalcSanad core already exists (no stub
calculators), but FULL runs it via TWO executions, and several master-plan scope items are not
yet wired. This pins exactly what is already built (so we don't duplicate it) and the gaps
Tasks 2-7 will close (so later tasks change behavior deliberately). No production change, no real
FULL run, no DB migration, no implementation, no Slice88.

Pins (per the locked decisions D-A..D-G):
  1. CalcType has 10 members; Task 3 took the formula registry to 9 implemented (only IRR
     deferred — no cash-flow-series input model).
  2. The durable CALC path (CalcRunner) persists DeterministicCalculation + CalcSanad and returns
     {calc_ids, reproducibility_hashes, persisted_count, blocked_candidates} with random UUIDs.
  3. The methodology SERVICE stays pure (returns run-scoped in-memory records, deterministic
     UUID5 ids); Task 2 made the FULL methodology PATH persist them durably via a wired wrapper,
     so G1 is closed (behavioral proof lives in test_slice87_calc_unification.py).
  4. Reproducibility hash + formula_hash + code_version are model fields and are bundle-visible in
     _calc_package; per-calc formula_version is NOT surfaced (G5).
  5. CalcEngine.verify_reproducibility exists but is never invoked in production (G5).
  6. Deliverables: memo financials/scenario builders exist but nothing feeds them; no FinancialTable
     model exists (G3).
  7. Graph and RAG do not consume calc outputs (G4).
  8. Migration 0005 already provides both calc tables — no new migration expected.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from idis.calc.engine import CalcEngine
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_materialization import RunScopedDeterministicCalculationRecord
from idis.models.deterministic_calculation import CalcType, DeterministicCalculation

TENANT_ID = "tenant-slice87"
DEAL_ID = "deal-slice87"

# Reuse the established CalcRunner fixtures rather than rebuilding them.
from tests.test_calc_runner import (  # noqa: E402
    FakeCalculationsRepository,
    _money_claim,
    _runner,
    _sanad,
)

_SRC = Path("src/idis")


# --- 1. CalcType breadth vs implemented formulas (G2: Task 3 closed all but IRR) ---


def test_calc_type_has_ten_members_registry_implements_nine() -> None:
    assert {t.value for t in CalcType} == {
        "IRR",
        "MOIC",
        "GROSS_MARGIN",
        "NRR",
        "CAC_PAYBACK",
        "VALUATION_MULTIPLE",
        "RUNWAY",
        "BURN_RATE",
        "LTV",
        "LTV_CAC_RATIO",
    }
    registered = set(register_core_formulas(FormulaRegistry()).list_registered())
    # Task 3 added MOIC, VALUATION_MULTIPLE, NRR, CAC_PAYBACK, LTV → 9 of 10 implemented.
    assert registered == set(CalcType) - {CalcType.IRR}
    missing = set(CalcType) - registered
    # Only IRR remains deferred: there is no cash-flow-series input model (formulas take scalars).
    assert missing == {CalcType.IRR}


# --- 2. durable CALC path: CalcRunner persists + returns the known shape, random UUIDs ---


def test_calc_runner_persists_and_returns_durable_shape() -> None:
    claims = {
        "c-cash": _money_claim("c-cash", "cash_balance", "1000000"),
        "c-burn": _money_claim("c-burn", "monthly_burn_rate", "100000"),
    }
    sanads = {"c-cash": _sanad("c-cash"), "c-burn": _sanad("c-burn")}
    repo = FakeCalculationsRepository()
    runner = _runner(claims=claims, sanads=sanads, calculations_repo=repo)

    result = runner.run(created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY])
    assert set(result) == {
        "calc_ids",
        "reproducibility_hashes",
        "persisted_count",
        "blocked_candidates",
    }
    assert len(result["calc_ids"]) == 1
    assert result["persisted_count"] == 1
    # Persisted both a DeterministicCalculation and a CalcSanad (durable path).
    (created_calc, created_sanad) = repo.created[0]
    assert isinstance(created_calc, DeterministicCalculation)
    assert created_calc.calc_id == result["calc_ids"][0]
    assert created_sanad.calc_id == created_calc.calc_id
    # CALC-path ids are random UUIDs (not the methodology deterministic UUID5 scheme).
    assert "-" in created_calc.calc_id and len(created_calc.calc_id) == 36
    # reproducibility_hash + formula_hash + code_version are present on the persisted record.
    assert len(created_calc.reproducibility_hash) == 64
    assert created_calc.formula_hash and created_calc.code_version


def test_calc_runner_registers_all_implemented_formulas() -> None:
    runner = _runner(claims={}, sanads={})
    # CalcRunner registers via register_core_formulas → all 9 implemented CalcTypes (IRR deferred).
    assert set(runner._registry.list_registered()) == set(CalcType) - {CalcType.IRR}


# --- 3. methodology SERVICE stays pure; Task 2 persists via a wired wrapper (G1 closed) ---


def test_methodology_service_stays_pure_persistence_is_in_wrapper() -> None:
    from idis.services.runs import methodology_deterministic_calculation as methodology

    service_cls = methodology.InMemoryRunMethodologyDeterministicCalculationService
    run_sig = inspect.signature(service_cls.run)
    assert "run" in dir(service_cls)
    # The service stays pure: run() returns run-scoped in-memory records, not a repo write.
    assert run_sig.return_annotation is not inspect.Signature.empty
    assert "RunScopedDeterministicCalculationRecord" in str(run_sig.return_annotation)

    methodology_src = inspect.getsource(methodology)
    # Purity invariant: the service module itself never persists (Task 2 deliberately kept it pure).
    for token in (
        "get_calculations_repository",
        "PostgresCalculationsRepository",
        "calculations_repo",
    ):
        assert token not in methodology_src, token

    # Task 2 (G1 closed): durable persistence is the FULL-path wrapper's job, wired into the run.
    from idis.api.routes import runs as runs_routes
    from idis.services.runs import steps as run_steps

    assert hasattr(runs_routes, "_run_full_methodology_deterministic_calculation")
    assert "get_calculations_repository" in inspect.getsource(
        runs_routes._run_full_methodology_deterministic_calculation
    )
    assert "methodology_deterministic_calculation_fn" in inspect.getsource(
        run_steps.build_run_context
    )

    # The run-scoped record WRAPS a DeterministicCalculation (calc_id nested at .calculation),
    # and the methodology path mints its ids via the deterministic UUID5 helper (D-G).
    from idis.models.calc_materialization import deterministic_calc_id

    record_fields = RunScopedDeterministicCalculationRecord.model_fields
    assert "calculation" in record_fields
    assert record_fields["calculation"].annotation is DeterministicCalculation
    assert "calc_id" not in record_fields  # not a top-level field; lives on .calculation
    assert callable(deterministic_calc_id)


def test_methodology_and_calc_are_two_full_steps() -> None:
    from idis.models.run_step import FULL_STEPS, StepName

    assert StepName.METHODOLOGY_DETERMINISTIC_CALCULATION in FULL_STEPS
    assert StepName.CALC in FULL_STEPS
    # Methodology calc runs well before the durable CALC step in FULL ordering.
    assert FULL_STEPS.index(StepName.METHODOLOGY_DETERMINISTIC_CALCULATION) < FULL_STEPS.index(
        StepName.CALC
    )


# --- 4. repro/version fields: present + bundle-visible; formula_version NOT surfaced (G5) ---


def test_calc_model_carries_repro_and_version_fields() -> None:
    fields = set(DeterministicCalculation.model_fields)
    assert {"reproducibility_hash", "formula_hash", "code_version"} <= fields
    assert "formula_version" not in fields  # per-calc formula_version not a model field


def test_calc_package_surfaces_hashes_but_not_formula_version() -> None:
    bundle_src = (_SRC / "deliverables" / "product_bundle.py").read_text(encoding="utf-8")
    calc_pkg = bundle_src[bundle_src.index("def _calc_package") :].split("def _evidence_index")[0]
    for field in ('"reproducibility_hash"', '"formula_hash"', '"code_version"', '"calc_grade"'):
        assert field in calc_pkg
    assert '"formula_version"' not in calc_pkg  # G5: formula_version not surfaced per calc


# --- 5. verify_reproducibility exists but is never invoked in production (G5) ---


def test_verify_reproducibility_defined_but_uninvoked() -> None:
    assert callable(CalcEngine.verify_reproducibility)
    invocations = [
        path
        for path in _SRC.rglob("*.py")
        if "verify_reproducibility(" in path.read_text(encoding="utf-8")
    ]
    # Only the definition site (engine.py) references it; no production caller.
    assert invocations == [_SRC / "calc" / "engine.py"]


# --- 6. deliverables: financials are LLM-agent-driven, no calc FinancialTable (G3) ---


def test_memo_financials_are_agent_driven_no_calc_financial_table() -> None:
    memo_src = (_SRC / "deliverables" / "memo.py").read_text(encoding="utf-8")
    assert "def add_financials_fact" in memo_src
    assert "def add_scenario_fact" in memo_src

    # Today the memo "financials" section is bridged from the LLM financial_agent report, NOT
    # from deterministic CalcEngine outputs, and the scenario builder is never fed.
    generator_src = (_SRC / "deliverables" / "generator.py").read_text(encoding="utf-8")
    assert '"financial_agent": "financials"' in generator_src
    assert "add_scenario_fact" not in generator_src  # scenario facts unfed (G3)

    # No deterministic FinancialTable model/builder exists anywhere yet (the Slice87 G3 gap).
    hits = [
        path for path in _SRC.rglob("*.py") if "FinancialTable" in path.read_text(encoding="utf-8")
    ]
    assert hits == []


# --- 7. graph and RAG do not consume calc outputs (G4) ---


def test_graph_and_rag_do_not_consume_calc_outputs() -> None:
    graph_retrieval = (_SRC / "services" / "graph" / "retrieval.py").read_text(encoding="utf-8")
    assert "calc" not in graph_retrieval.lower()

    rag_dir = _SRC / "services" / "rag"
    rag_calc_refs = [
        path
        for path in rag_dir.rglob("*.py")
        if "calc_id" in path.read_text(encoding="utf-8")
        or "calculation" in path.read_text(encoding="utf-8").lower()
    ]
    assert rag_calc_refs == []


# --- 8. migration 0005 already provides both tables — no new migration expected ---


def test_migration_0005_provides_calc_tables() -> None:
    migration = (
        _SRC
        / "persistence"
        / "migrations"
        / "versions"
        / "0005_deterministic_calculations_and_calc_sanads.py"
    )
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    assert "deterministic_calculations" in text
    assert "calc_sanads" in text
    assert "POLICY" in text.upper()  # RLS tenant isolation
