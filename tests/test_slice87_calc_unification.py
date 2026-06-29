"""Slice87 Task 2 — methodology-authoritative calculation-path unification.

RED-first behavior tests for two production changes (D-B methodology-authoritative, D-G id scheme):

  Part A — the FULL methodology deterministic-calculation path now PERSISTS its calculations and
  CalcSanads through the existing durable persistence path (get_calculations_repository().create),
  preserving the deterministic UUID5 methodology ids, and is idempotent on re-run.

  Part B — the durable CALC path dedups/merges against already-persisted (methodology-authoritative)
  records by reproducibility_hash instead of recomputing a parallel duplicate under a fresh id.

No new DB migration (0005 already provides both tables). No new formulas, no graph/RAG, no
financial-table work, no deliverable changes, no real FULL run, no Slice88.
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.models.deterministic_calculation import CalcType
from idis.persistence.repositories.calculations import (
    InMemoryCalculationsRepository,
    clear_in_memory_calculations_store,
)
from idis.services.calc.runner import CalcRunner

# CalcRunner fixtures (Part B).
from tests.test_calc_runner import (
    DEAL_ID as CR_DEAL,
)
from tests.test_calc_runner import (
    TENANT_ID as CR_TENANT,
)
from tests.test_calc_runner import (
    FakeClaimsRepository,
    FakeSanadsRepository,
    _money_claim,
    _sanad,
)

# Methodology service fixtures (Part A).
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID as M_DEAL,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    RUN_ID as M_RUN,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    TENANT_ID as M_TENANT,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    SanadGrade,
    _claim,
    _grade,
    _sanad_record,
    _task,
)


@pytest.fixture(autouse=True)
def _clear_store() -> Any:
    clear_in_memory_calculations_store()
    yield
    clear_in_memory_calculations_store()


# --- Part A: FULL methodology path persists durably (G1) ---


def _methodology_inputs() -> dict[str, Any]:
    return {
        "tenant_id": M_TENANT,
        "deal_id": M_DEAL,
        "run_id": M_RUN,
        "materialized_claims": [
            _claim("claim_mth_revenue", "revenue", "1000"),
            _claim("claim_mth_cogs", "cogs", "400"),
        ],
        "sanads": [
            _sanad_record("claim_mth_revenue"),
            _sanad_record("claim_mth_cogs", grade=SanadGrade.B),
        ],
        "sanad_grades": [
            _grade("claim_mth_revenue"),
            _grade("claim_mth_cogs", SanadGrade.B),
        ],
        "extraction_tasks": [_task()],
    }


def test_full_methodology_calc_persists_records_durably() -> None:
    from idis.api.routes.runs import _run_full_methodology_deterministic_calculation

    run_result, calculations, calc_sanads = _run_full_methodology_deterministic_calculation(
        **_methodology_inputs(),
        db_conn=None,
    )

    # The service contract is preserved: same in-memory records returned.
    assert run_result.status.value == "completed"
    assert len(calculations) == 1
    assert len(calc_sanads) == 1
    methodology_calc_id = calculations[0].calculation.calc_id

    # ...AND the calculation + CalcSanad are now durable, under the deterministic methodology id.
    durable = InMemoryCalculationsRepository(M_TENANT).list_by_deal(M_DEAL)
    assert [row["calc_id"] for row in durable] == [methodology_calc_id]
    durable_sanads = InMemoryCalculationsRepository(M_TENANT).list_calc_sanads_by_deal(M_DEAL)
    assert [row["calc_id"] for row in durable_sanads] == [methodology_calc_id]


def test_full_methodology_persistence_is_idempotent_on_rerun() -> None:
    from idis.api.routes.runs import _run_full_methodology_deterministic_calculation

    inputs = _methodology_inputs()
    _run_full_methodology_deterministic_calculation(**inputs, db_conn=None)
    _run_full_methodology_deterministic_calculation(**inputs, db_conn=None)

    # Deterministic UUID5 ids mean re-running persists no duplicate (resume-safe).
    durable = InMemoryCalculationsRepository(M_TENANT).list_by_deal(M_DEAL)
    assert len(durable) == 1


# --- Part B: durable CALC path dedups/merges against authoritative records (D-B) ---


def _calc_runner(repo: InMemoryCalculationsRepository, *, cash: str = "1000000") -> CalcRunner:
    claims = {
        "c-cash": _money_claim("c-cash", "cash_balance", cash),
        "c-burn": _money_claim("c-burn", "monthly_burn_rate", "100000"),
    }
    sanads = {"c-cash": _sanad("c-cash"), "c-burn": _sanad("c-burn")}
    return CalcRunner(
        tenant_id=CR_TENANT,
        deal_id=CR_DEAL,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=repo,
    )


def test_calc_path_dedups_against_authoritative_persisted_calc() -> None:
    repo = InMemoryCalculationsRepository(CR_TENANT)

    # First execution stands in for the methodology-authoritative persist (same deal, same inputs).
    first = _calc_runner(repo).run(
        created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY]
    )
    assert len(first["calc_ids"]) == 1
    assert first["persisted_count"] == 1
    authoritative_id = first["calc_ids"][0]

    # A second pass over identical inputs must dedup/merge, NOT persist a parallel duplicate.
    second = _calc_runner(repo).run(
        created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY]
    )
    assert second["calc_ids"] == [authoritative_id]  # reused the authoritative id (merge)
    assert second["persisted_count"] == 0  # nothing newly persisted

    persisted = repo.list_by_deal(CR_DEAL)
    assert len(persisted) == 1  # no parallel duplicate in the durable store


def test_calc_path_does_not_over_suppress_distinct_inputs() -> None:
    repo = InMemoryCalculationsRepository(CR_TENANT)

    # First RUNWAY is authoritative. A second RUNWAY over DIFFERENT cash has a different
    # reproducibility hash — a genuinely distinct calc that dedup must NOT suppress.
    _calc_runner(repo, cash="1000000").run(
        created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY]
    )
    distinct = _calc_runner(repo, cash="2000000").run(
        created_claim_ids=["c-cash", "c-burn"], calc_types=[CalcType.RUNWAY]
    )

    assert distinct["persisted_count"] == 1  # different inputs -> persisted, not deduped away
    assert len(repo.list_by_deal(CR_DEAL)) == 2


def test_calc_step_dedups_metadata_bearing_methodology_calc() -> None:
    """Cross-path dedup for metadata-bearing calc types (GROSS_MARGIN), not just metadata-free.

    The methodology path stamps output metadata (e.g. unit=percent, currency) into the
    reproducibility hash via metadata_for_calc; the CALC step must produce the SAME metadata so it
    dedups against the methodology-authoritative record instead of persisting a parallel duplicate.
    """
    from decimal import Decimal as _D

    from idis.calc.engine import CalcEngine, InputGradeInfo
    from idis.calc.formulas.core import register_core_formulas
    from idis.calc.formulas.registry import FormulaRegistry
    from idis.models.calc_sanad import SanadGrade

    repo = InMemoryCalculationsRepository(CR_TENANT)

    # Seed a methodology-authoritative GROSS_MARGIN calc WITH methodology metadata (unit+currency),
    # exactly as metadata_for_calc stamps it.
    FormulaRegistry.reset_instance()
    engine = CalcEngine(
        registry=register_core_formulas(FormulaRegistry()), enforce_extraction_gate=False
    )
    seeded = engine.run(
        tenant_id=CR_TENANT,
        deal_id=CR_DEAL,
        calc_type=CalcType.GROSS_MARGIN,
        input_values={"revenue": _D("1000"), "cogs": _D("400")},
        input_grades=[
            InputGradeInfo(claim_id="c-revenue", grade=SanadGrade.A),
            InputGradeInfo(claim_id="c-cogs", grade=SanadGrade.A),
        ],
        metadata={"unit": "percent", "currency": "USD"},
    )
    repo.create(calculation=seeded.calculation, calc_sanad=seeded.calc_sanad)
    seeded_id = seeded.calculation.calc_id

    # The CALC step over the SAME claims must reuse the methodology calc, not duplicate it.
    claims = {
        "c-revenue": _money_claim("c-revenue", "revenue", "1000"),
        "c-cogs": _money_claim("c-cogs", "cogs", "400"),
    }
    sanads = {"c-revenue": _sanad("c-revenue"), "c-cogs": _sanad("c-cogs")}
    runner = CalcRunner(
        tenant_id=CR_TENANT,
        deal_id=CR_DEAL,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=repo,
    )
    result = runner.run(
        created_claim_ids=["c-revenue", "c-cogs"], calc_types=[CalcType.GROSS_MARGIN]
    )

    assert result["calc_ids"] == [seeded_id]  # reused the methodology-authoritative id
    assert result["persisted_count"] == 0  # no parallel duplicate persisted
    assert len(repo.list_by_deal(CR_DEAL)) == 1
