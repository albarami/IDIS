"""Tests for truthful CALC runner behavior."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from idis.calc.engine import CalcEngine, CalcEngineResult, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import CalcSanad
from idis.models.deterministic_calculation import CalcType, DeterministicCalculation
from idis.services.calc.runner import CalcRunner

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


class FakeClaimsRepository:
    """Claim repository test double."""

    def __init__(self, claims: dict[str, dict[str, Any]]) -> None:
        self._claims = claims

    def get(self, claim_id: str) -> dict[str, Any] | None:
        return self._claims.get(claim_id)


class FakeSanadsRepository:
    """Sanad repository test double."""

    def __init__(self, sanads: dict[str, dict[str, Any]]) -> None:
        self._sanads = sanads

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        return self._sanads.get(claim_id)


class FakeCalculationsRepository:
    """Calculation repository test double."""

    def __init__(self, *, fail_on_create: bool = False) -> None:
        self.fail_on_create = fail_on_create
        self.created: list[tuple[object, object]] = []

    def create(self, *, calculation: object, calc_sanad: object) -> None:
        if self.fail_on_create:
            raise RuntimeError("database unavailable")
        self.created.append((calculation, calc_sanad))

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        return [
            {"calc_id": calc.calc_id, "reproducibility_hash": calc.reproducibility_hash}
            for calc, _ in self.created
        ]


def _money_claim(
    claim_id: str,
    predicate: str,
    amount: str,
    *,
    currency: str | None = "USD",
    time_window: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "monetary",
        "amount": amount,
    }
    if currency is not None:
        value["currency"] = currency
    if time_window is not None:
        value["time_window"] = time_window
    return {
        "claim_id": claim_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "claim_class": "FINANCIAL",
        "claim_text": f"{predicate} was {amount}",
        "predicate": predicate,
        "value": value,
        "claim_grade": "A",
        "materiality": "HIGH",
    }


def _sanad(
    claim_id: str,
    *,
    confidence: str = "0.99",
    dhabt: str = "0.95",
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "computed": {
            "extraction_confidence": confidence,
            "dhabt_score": dhabt,
        },
    }


def _runner(
    *,
    claims: dict[str, dict[str, Any]],
    sanads: dict[str, dict[str, Any]],
    calculations_repo: FakeCalculationsRepository | None = None,
) -> CalcRunner:
    return CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=calculations_repo or FakeCalculationsRepository(),
    )


class _CountingCalcEngine(CalcEngine):
    """Genuine CalcEngine that additionally records run invocations.

    Bounded test spy through the existing ``CalcRunner(engine=...)`` seam;
    delegates every call so refusing paths can assert zero engine calls.
    """

    def __init__(self) -> None:
        super().__init__(registry=register_core_formulas(FormulaRegistry()))
        self.run_calls = 0

    def run(
        self,
        tenant_id: str,
        deal_id: str,
        calc_type: CalcType,
        input_values: dict[str, Decimal],
        input_grades: list[InputGradeInfo],
        metadata: dict[str, str] | None = None,
    ) -> CalcEngineResult:
        self.run_calls += 1
        return super().run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=calc_type,
            input_values=input_values,
            input_grades=input_grades,
            metadata=metadata,
        )


def _runner_with_engine_spy(
    *,
    claims: dict[str, dict[str, Any]],
    sanads: dict[str, dict[str, Any]],
    calculations_repo: FakeCalculationsRepository | None = None,
) -> tuple[CalcRunner, _CountingCalcEngine, FakeCalculationsRepository]:
    """Runner wired to a counting engine and a recording calculations repo."""
    repo = calculations_repo or FakeCalculationsRepository()
    engine = _CountingCalcEngine()
    runner = CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=FakeClaimsRepository(claims),
        sanads_repo=FakeSanadsRepository(sanads),
        calculations_repo=repo,
        engine=engine,
    )
    return runner, engine, repo


def test_calc_runner_persists_eligible_deterministic_calculation_and_calc_sanad() -> None:
    """Eligible evidence-backed inputs produce durable calc and CalcSanad records."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert len(result["calc_ids"]) == 1
    assert len(result["reproducibility_hashes"]) == 1
    assert result["blocked_candidates"] == []
    assert len(calculations_repo.created) == 1
    calculation, calc_sanad = calculations_repo.created[0]
    assert calculation.calc_type == CalcType.GROSS_MARGIN
    assert calculation.output.primary_value == 60
    assert calc_sanad.calc_id == calculation.calc_id


def test_calc_runner_maps_real_extraction_claim_shape_to_formula_inputs() -> None:
    """Claims without explicit predicates can still feed formulas via claim_class/text."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": {
            **_money_claim("claim-revenue", "unused", "1000"),
            "predicate": None,
            "claim_class": "REVENUE",
            "value": {"type": "monetary", "value": "1000", "currency": "USD"},
        },
        "claim-cogs": {
            **_money_claim("claim-cogs", "unused", "400"),
            "predicate": None,
            "claim_class": "COST_OF_GOODS_SOLD",
            "value": {"type": "monetary", "value": "400", "currency": "USD"},
        },
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []


def test_calc_runner_completes_truthfully_when_no_inputs_are_eligible() -> None:
    """No eligible inputs is a truthful completed CALC summary, not fake calc IDs."""
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }

    result = _runner(claims=claims, sanads={}).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 0
    assert result["calc_ids"] == []
    assert result["reproducibility_hashes"] == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "missing_source_metadata",
            "claim_ids": ["claim-cogs", "claim-revenue"],
        }
    ]


def test_calc_runner_persists_eligible_candidates_and_reports_blocked_candidates() -> None:
    """Mixed eligible/ineligible candidates persist real calcs and report skipped ones."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN, CalcType.RUNWAY],
    )

    assert result["persisted_count"] == 1
    assert len(result["calc_ids"]) == 1
    assert result["blocked_candidates"] == [
        {
            "calc_type": "RUNWAY",
            "reason": "missing_required_claim",
            "missing_inputs": ["cash_balance", "monthly_burn_rate"],
        }
    ]


def test_calc_runner_fails_closed_on_persistence_errors() -> None:
    """System and DB errors must fail closed rather than becoming skipped candidates."""
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }
    runner = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=FakeCalculationsRepository(fail_on_create=True),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        runner.run(
            created_claim_ids=list(claims),
            calc_types=[CalcType.GROSS_MARGIN],
        )


class SequentialClaimsRepository:
    """Serves the recorded payload for the Nth successive ``get`` call.

    Narrow double for exercising repeated appearances of one claim id whose
    record content differs between reads; a plain dict repository cannot
    express two records under one key.
    """

    def __init__(self, records: list[dict[str, Any] | None]) -> None:
        self._records = records
        self._calls = 0

    def get(self, claim_id: str) -> dict[str, Any] | None:
        record = self._records[min(self._calls, len(self._records) - 1)]
        self._calls += 1
        return record


def _three_input_sanads() -> dict[str, dict[str, Any]]:
    return {
        "claim-revenue-a": _sanad("claim-revenue-a"),
        "claim-revenue-b": _sanad("claim-revenue-b"),
        "claim-cogs": _sanad("claim-cogs"),
    }


def test_calc_runner_refuses_competing_operands_for_required_input() -> None:
    """Distinct claim identities feeding one required key refuse pre-computation."""
    claims = {
        "claim-revenue-a": _money_claim("claim-revenue-a", "revenue", "1000"),
        "claim-revenue-b": _money_claim("claim-revenue-b", "revenue", "1500"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = _three_input_sanads()

    for created_claim_ids in (
        ["claim-revenue-a", "claim-revenue-b", "claim-cogs"],
        ["claim-revenue-b", "claim-revenue-a", "claim-cogs"],
    ):
        calculations_repo = FakeCalculationsRepository()
        result = _runner(
            claims=claims,
            sanads=sanads,
            calculations_repo=calculations_repo,
        ).run(
            created_claim_ids=created_claim_ids,
            calc_types=[CalcType.GROSS_MARGIN],
        )

        assert result["persisted_count"] == 0
        assert result["calc_ids"] == []
        assert result["reproducibility_hashes"] == []
        assert calculations_repo.created == []
        assert result["blocked_candidates"] == [
            {
                "calc_type": "GROSS_MARGIN",
                "reason": "calculation_failed",
                "claim_ids": ["claim-revenue-a", "claim-revenue-b"],
            }
        ]


def test_calc_runner_refuses_competing_operands_at_equal_amounts() -> None:
    """Equal amounts do not collapse two distinct eligible claim identities."""
    claims = {
        "claim-revenue-a": _money_claim("claim-revenue-a", "revenue", "1000"),
        "claim-revenue-b": _money_claim("claim-revenue-b", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }

    calculations_repo = FakeCalculationsRepository()
    result = _runner(
        claims=claims,
        sanads=_three_input_sanads(),
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 0
    assert calculations_repo.created == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "calculation_failed",
            "claim_ids": ["claim-revenue-a", "claim-revenue-b"],
        }
    ]


def test_calc_runner_treats_identical_repeated_claim_as_one_observation() -> None:
    """Re-reading the same immutable claim record is not competition."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
    }
    sanads = {
        "claim-revenue": _sanad("claim-revenue"),
        "claim-cogs": _sanad("claim-cogs"),
    }

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=["claim-revenue", "claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert len(result["reproducibility_hashes"]) == 1
    assert len(calculations_repo.created) == 1


def test_calc_runner_refuses_conflicting_content_for_same_claim_id() -> None:
    """One claim id reappearing with different record content refuses."""
    claims_repo = SequentialClaimsRepository(
        [
            _money_claim("claim-revenue", "revenue", "1000"),
            _money_claim("claim-revenue", "revenue", "9999"),
            _money_claim("claim-cogs", "cogs", "400"),
        ]
    )
    calculations_repo = FakeCalculationsRepository()

    result = CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=claims_repo,
        sanads_repo=FakeSanadsRepository(
            {
                "claim-revenue": _sanad("claim-revenue"),
                "claim-cogs": _sanad("claim-cogs"),
            }
        ),
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=["claim-revenue", "claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 0
    assert result["calc_ids"] == []
    assert calculations_repo.created == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "calculation_failed",
            "claim_ids": ["claim-revenue"],
        }
    ]

    # The same conflicting identity is refused when its snapshots project to
    # two different required keys — per-key singletons must not escape.
    cross_key_records: tuple[list[dict[str, Any] | None], ...] = (
        [
            _money_claim("claim-shared", "revenue", "1000"),
            _money_claim("claim-shared", "cogs", "400"),
        ],
        [
            _money_claim("claim-shared", "cogs", "400"),
            _money_claim("claim-shared", "revenue", "1000"),
        ],
    )
    for records in cross_key_records:
        calculations_repo = FakeCalculationsRepository()
        result = CalcRunner(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            claims_repo=SequentialClaimsRepository(records),
            sanads_repo=FakeSanadsRepository({"claim-shared": _sanad("claim-shared")}),
            calculations_repo=calculations_repo,
        ).run(
            created_claim_ids=["claim-shared", "claim-shared"],
            calc_types=[CalcType.GROSS_MARGIN],
        )

        assert result["persisted_count"] == 0
        assert result["calc_ids"] == []
        assert calculations_repo.created == []
        assert result["blocked_candidates"] == [
            {
                "calc_type": "GROSS_MARGIN",
                "reason": "calculation_failed",
                "claim_ids": ["claim-shared"],
            }
        ]


def test_calc_runner_ignores_duplicate_claims_on_unrequested_input_keys() -> None:
    """Duplicate eligible claims on keys this calculation never requests do not block."""
    calculations_repo = FakeCalculationsRepository()
    claims = {
        "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
        "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
        "claim-cash-a": _money_claim("claim-cash-a", "cash_balance", "500"),
        "claim-cash-b": _money_claim("claim-cash-b", "cash_balance", "700"),
    }
    sanads = {claim_id: _sanad(claim_id) for claim_id in claims}

    result = _runner(
        claims=claims,
        sanads=sanads,
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=list(claims),
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert len(result["reproducibility_hashes"]) == 1
    assert len(calculations_repo.created) == 1

    # Conflicting content under one id that only spans an unrequested key is
    # equally out of scope for this calculation.
    calculations_repo = FakeCalculationsRepository()
    result = CalcRunner(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claims_repo=SequentialClaimsRepository(
            [
                _money_claim("claim-shared", "revenue", "1000"),
                _money_claim("claim-shared", "cash_balance", "9999"),
                _money_claim("claim-cogs", "cogs", "400"),
            ]
        ),
        sanads_repo=FakeSanadsRepository(
            {
                "claim-shared": _sanad("claim-shared"),
                "claim-cogs": _sanad("claim-cogs"),
            }
        ),
        calculations_repo=calculations_repo,
    ).run(
        created_claim_ids=["claim-shared", "claim-shared", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert len(calculations_repo.created) == 1


def test_calc_runner_refuses_mismatched_selected_pair_currency() -> None:
    """Different present currencies on the selected pair block before the engine."""
    for created_claim_ids in (
        ["claim-revenue", "claim-cogs"],
        ["claim-cogs", "claim-revenue"],
    ):
        runner, engine, calculations_repo = _runner_with_engine_spy(
            claims={
                "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
                "claim-cogs": _money_claim("claim-cogs", "cogs", "400", currency="EUR"),
            },
            sanads={
                "claim-revenue": _sanad("claim-revenue"),
                "claim-cogs": _sanad("claim-cogs"),
            },
        )

        result = runner.run(
            created_claim_ids=created_claim_ids,
            calc_types=[CalcType.GROSS_MARGIN],
        )

        assert result["persisted_count"] == 0
        assert result["calc_ids"] == []
        assert result["reproducibility_hashes"] == []
        assert engine.run_calls == 0
        assert calculations_repo.created == []
        assert result["blocked_candidates"] == [
            {
                "calc_type": "GROSS_MARGIN",
                "reason": "calculation_failed",
                "claim_ids": ["claim-cogs", "claim-revenue"],
            }
        ]


def test_calc_runner_refuses_mismatched_selected_pair_time_window() -> None:
    """Different present periods on the selected pair block before the engine."""
    runner, engine, calculations_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000", time_window="FY2024"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400", time_window="FY2023"),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )

    result = runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 0
    assert result["calc_ids"] == []
    assert result["reproducibility_hashes"] == []
    assert engine.run_calls == 0
    assert calculations_repo.created == []
    assert result["blocked_candidates"] == [
        {
            "calc_type": "GROSS_MARGIN",
            "reason": "calculation_failed",
            "claim_ids": ["claim-cogs", "claim-revenue"],
        }
    ]


def test_calc_runner_refuses_one_sided_coordinate_absence() -> None:
    """A coordinate carried by only one operand is incomplete, never borrowed."""
    coordinate_cases: list[tuple[dict[str, str | None], dict[str, str | None]]] = [
        ({"currency": "USD"}, {"currency": None}),
        ({"currency": None}, {"currency": "USD"}),
        ({"time_window": "FY2024"}, {}),
        ({}, {"time_window": "FY2024"}),
    ]
    for revenue_value, cogs_value in coordinate_cases:
        runner, engine, calculations_repo = _runner_with_engine_spy(
            claims={
                "claim-revenue": _money_claim("claim-revenue", "revenue", "1000", **revenue_value),
                "claim-cogs": _money_claim("claim-cogs", "cogs", "400", **cogs_value),
            },
            sanads={
                "claim-revenue": _sanad("claim-revenue"),
                "claim-cogs": _sanad("claim-cogs"),
            },
        )

        result = runner.run(
            created_claim_ids=["claim-revenue", "claim-cogs"],
            calc_types=[CalcType.GROSS_MARGIN],
        )

        assert result["persisted_count"] == 0
        assert result["calc_ids"] == []
        assert result["reproducibility_hashes"] == []
        assert engine.run_calls == 0
        assert calculations_repo.created == []
        assert result["blocked_candidates"] == [
            {
                "calc_type": "GROSS_MARGIN",
                "reason": "calculation_failed",
                "claim_ids": ["claim-cogs", "claim-revenue"],
            }
        ]


def test_calc_runner_allows_both_coordinates_absent() -> None:
    """Optional coordinates absent on both operands preserve existing persistence."""
    runner, engine, calculations_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000", currency=None),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400", currency=None),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )

    result = runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert engine.run_calls == 1
    assert len(result["reproducibility_hashes"]) == 1
    assert len(calculations_repo.created) == 1
    calculation, calc_sanad = calculations_repo.created[0]
    assert isinstance(calculation, DeterministicCalculation)
    assert isinstance(calc_sanad, CalcSanad)
    assert calculation.output.primary_value == 60
    assert calc_sanad.calc_id == calculation.calc_id


def test_calc_runner_matching_present_period_preserves_positive() -> None:
    """Equal explicit periods on both operands persist as before."""
    runner, engine, calculations_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000", time_window="FY2024"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400", time_window="FY2024"),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )

    result = runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert engine.run_calls == 1
    assert len(result["reproducibility_hashes"]) == 1
    assert len(calculations_repo.created) == 1
    calculation, _calc_sanad = calculations_repo.created[0]
    assert isinstance(calculation, DeterministicCalculation)
    assert calculation.output.primary_value == 60


def test_calc_runner_restored_coordinate_reproduces_genuine_result() -> None:
    """Restoring only the changed coordinate reproduces the coherent positive."""
    refused_runner, refused_engine, refused_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400", currency="EUR"),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )
    refused = refused_runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )
    assert refused["persisted_count"] == 0
    assert refused_engine.run_calls == 0
    assert refused_repo.created == []

    restored_runner, restored_engine, restored_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )
    restored = restored_runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )
    assert restored["persisted_count"] == 1
    assert restored["blocked_candidates"] == []
    assert restored_engine.run_calls == 1
    assert len(restored_repo.created) == 1
    calculation, calc_sanad = restored_repo.created[0]
    assert isinstance(calculation, DeterministicCalculation)
    assert isinstance(calc_sanad, CalcSanad)
    assert calculation.output.primary_value == 60
    assert calc_sanad.calc_id == calculation.calc_id

    positive_runner, _positive_engine, _positive_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
        },
    )
    positive = positive_runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs"],
        calc_types=[CalcType.GROSS_MARGIN],
    )
    assert restored["reproducibility_hashes"] == positive["reproducibility_hashes"]


def test_calc_runner_unrequested_mismatching_observation_does_not_block() -> None:
    """A mismatching coordinate on an unrequested key stays out of the pair."""
    runner, engine, calculations_repo = _runner_with_engine_spy(
        claims={
            "claim-revenue": _money_claim("claim-revenue", "revenue", "1000"),
            "claim-cogs": _money_claim("claim-cogs", "cogs", "400"),
            "claim-cash": _money_claim(
                "claim-cash",
                "cash_balance",
                "500",
                currency="EUR",
                time_window="FY2023",
            ),
        },
        sanads={
            "claim-revenue": _sanad("claim-revenue"),
            "claim-cogs": _sanad("claim-cogs"),
            "claim-cash": _sanad("claim-cash"),
        },
    )

    result = runner.run(
        created_claim_ids=["claim-revenue", "claim-cogs", "claim-cash"],
        calc_types=[CalcType.GROSS_MARGIN],
    )

    assert result["persisted_count"] == 1
    assert result["blocked_candidates"] == []
    assert engine.run_calls == 1
    assert len(result["reproducibility_hashes"]) == 1
    assert len(calculations_repo.created) == 1
