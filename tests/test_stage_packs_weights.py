"""Stage pack weight tests — Phase 9.

Asserts exact weight mappings for PRE_SEED / SEED / SERIES_A / SERIES_B / GROWTH,
validates all 8 dimensions are covered, and weights sum to 1.0.
"""

from __future__ import annotations

import pytest

from idis.analysis.scoring.models import ALL_DIMENSIONS, ScoreDimension, Stage
from idis.analysis.scoring.stage_packs import (
    StagePackNotFoundError,
    get_stage_pack,
)

_WEIGHT_TOLERANCE = 1e-9


class TestPreSeedWeights:
    """PRE_SEED weight mapping matches VC doc verbatim."""

    def test_team_quality(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert pack.weights[ScoreDimension.TEAM_QUALITY] == pytest.approx(0.40)

    def test_market_attractiveness(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert pack.weights[ScoreDimension.MARKET_ATTRACTIVENESS] == pytest.approx(0.30)

    def test_product_defensibility(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert pack.weights[ScoreDimension.PRODUCT_DEFENSIBILITY] == pytest.approx(0.15)

    def test_traction_velocity(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert pack.weights[ScoreDimension.TRACTION_VELOCITY] == pytest.approx(0.15)

    def test_zero_weight_dimensions(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        for dim in [
            ScoreDimension.FUND_THESIS_FIT,
            ScoreDimension.CAPITAL_EFFICIENCY,
            ScoreDimension.SCALABILITY,
            ScoreDimension.RISK_PROFILE,
        ]:
            assert pack.weights[dim] == pytest.approx(0.0), f"PRE_SEED {dim.value} should be 0.0"

    def test_covers_all_8_dimensions(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert set(pack.weights.keys()) == ALL_DIMENSIONS

    def test_weights_sum_to_one(self) -> None:
        pack = get_stage_pack(Stage.PRE_SEED)
        assert sum(pack.weights.values()) == pytest.approx(1.0, abs=_WEIGHT_TOLERANCE)


class TestSeedWeights:
    """SEED weight mapping is an interim default covering all 8 dimensions."""

    def test_covers_all_8_dimensions(self) -> None:
        pack = get_stage_pack(Stage.SEED)
        assert set(pack.weights.keys()) == ALL_DIMENSIONS

    def test_weights_sum_to_one(self) -> None:
        pack = get_stage_pack(Stage.SEED)
        assert sum(pack.weights.values()) == pytest.approx(1.0, abs=_WEIGHT_TOLERANCE)

    def test_team_quality_is_largest(self) -> None:
        pack = get_stage_pack(Stage.SEED)
        max_dim = max(pack.weights, key=lambda d: pack.weights[d])
        assert max_dim == ScoreDimension.TEAM_QUALITY

    def test_all_weights_non_negative(self) -> None:
        pack = get_stage_pack(Stage.SEED)
        for dim, weight in pack.weights.items():
            assert weight >= 0.0, f"SEED {dim.value} weight is negative: {weight}"


class TestSeriesAWeights:
    """SERIES_A weight mapping matches VC doc verbatim."""

    def test_product_defensibility(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert pack.weights[ScoreDimension.PRODUCT_DEFENSIBILITY] == pytest.approx(0.30)

    def test_capital_efficiency(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert pack.weights[ScoreDimension.CAPITAL_EFFICIENCY] == pytest.approx(0.25)

    def test_team_quality(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert pack.weights[ScoreDimension.TEAM_QUALITY] == pytest.approx(0.20)

    def test_market_attractiveness(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert pack.weights[ScoreDimension.MARKET_ATTRACTIVENESS] == pytest.approx(0.15)

    def test_traction_velocity(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert pack.weights[ScoreDimension.TRACTION_VELOCITY] == pytest.approx(0.10)

    def test_zero_weight_dimensions(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        for dim in [
            ScoreDimension.FUND_THESIS_FIT,
            ScoreDimension.SCALABILITY,
            ScoreDimension.RISK_PROFILE,
        ]:
            assert pack.weights[dim] == pytest.approx(0.0), f"SERIES_A {dim.value} should be 0.0"

    def test_covers_all_8_dimensions(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert set(pack.weights.keys()) == ALL_DIMENSIONS

    def test_weights_sum_to_one(self) -> None:
        pack = get_stage_pack(Stage.SERIES_A)
        assert sum(pack.weights.values()) == pytest.approx(1.0, abs=_WEIGHT_TOLERANCE)


class TestSeriesBWeights:
    """SERIES_B weight mapping matches VC doc verbatim."""

    def test_capital_efficiency(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert pack.weights[ScoreDimension.CAPITAL_EFFICIENCY] == pytest.approx(0.25)

    def test_scalability(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert pack.weights[ScoreDimension.SCALABILITY] == pytest.approx(0.25)

    def test_team_quality(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert pack.weights[ScoreDimension.TEAM_QUALITY] == pytest.approx(0.20)

    def test_market_attractiveness(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert pack.weights[ScoreDimension.MARKET_ATTRACTIVENESS] == pytest.approx(0.15)

    def test_product_defensibility(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert pack.weights[ScoreDimension.PRODUCT_DEFENSIBILITY] == pytest.approx(0.15)

    def test_zero_weight_dimensions(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        for dim in [
            ScoreDimension.TRACTION_VELOCITY,
            ScoreDimension.FUND_THESIS_FIT,
            ScoreDimension.RISK_PROFILE,
        ]:
            assert pack.weights[dim] == pytest.approx(0.0), f"SERIES_B {dim.value} should be 0.0"

    def test_covers_all_8_dimensions(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert set(pack.weights.keys()) == ALL_DIMENSIONS

    def test_weights_sum_to_one(self) -> None:
        pack = get_stage_pack(Stage.SERIES_B)
        assert sum(pack.weights.values()) == pytest.approx(1.0, abs=_WEIGHT_TOLERANCE)


class TestGrowthWeights:
    """GROWTH weight mapping matches VC doc verbatim."""

    def test_risk_profile(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert pack.weights[ScoreDimension.RISK_PROFILE] == pytest.approx(0.30)

    def test_fund_thesis_fit(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert pack.weights[ScoreDimension.FUND_THESIS_FIT] == pytest.approx(0.25)

    def test_capital_efficiency(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert pack.weights[ScoreDimension.CAPITAL_EFFICIENCY] == pytest.approx(0.25)

    def test_market_attractiveness(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert pack.weights[ScoreDimension.MARKET_ATTRACTIVENESS] == pytest.approx(0.20)

    def test_zero_weight_dimensions(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        for dim in [
            ScoreDimension.TEAM_QUALITY,
            ScoreDimension.PRODUCT_DEFENSIBILITY,
            ScoreDimension.TRACTION_VELOCITY,
            ScoreDimension.SCALABILITY,
        ]:
            assert pack.weights[dim] == pytest.approx(0.0), f"GROWTH {dim.value} should be 0.0"

    def test_covers_all_8_dimensions(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert set(pack.weights.keys()) == ALL_DIMENSIONS

    def test_weights_sum_to_one(self) -> None:
        pack = get_stage_pack(Stage.GROWTH)
        assert sum(pack.weights.values()) == pytest.approx(1.0, abs=_WEIGHT_TOLERANCE)


class TestBandThresholdsAndRouting:
    """Band thresholds and routing rules are correct for all packs."""

    @pytest.mark.parametrize("stage", list(Stage))
    def test_high_threshold_is_75(self, stage: Stage) -> None:
        pack = get_stage_pack(stage)
        assert pack.band_thresholds["HIGH"] == 75.0

    @pytest.mark.parametrize("stage", list(Stage))
    def test_medium_threshold_is_55(self, stage: Stage) -> None:
        pack = get_stage_pack(stage)
        assert pack.band_thresholds["MEDIUM"] == 55.0

    @pytest.mark.parametrize("stage", list(Stage))
    def test_high_routes_to_invest(self, stage: Stage) -> None:
        from idis.analysis.scoring.models import RoutingAction, ScoreBand

        pack = get_stage_pack(stage)
        assert pack.routing_by_band[ScoreBand.HIGH] == RoutingAction.INVEST

    @pytest.mark.parametrize("stage", list(Stage))
    def test_medium_routes_to_hold(self, stage: Stage) -> None:
        from idis.analysis.scoring.models import RoutingAction, ScoreBand

        pack = get_stage_pack(stage)
        assert pack.routing_by_band[ScoreBand.MEDIUM] == RoutingAction.HOLD

    @pytest.mark.parametrize("stage", list(Stage))
    def test_low_routes_to_decline(self, stage: Stage) -> None:
        from idis.analysis.scoring.models import RoutingAction, ScoreBand

        pack = get_stage_pack(stage)
        assert pack.routing_by_band[ScoreBand.LOW] == RoutingAction.DECLINE


class TestGetStagePackFailClosed:
    """get_stage_pack must return valid packs or raise."""

    @pytest.mark.parametrize("stage", list(Stage))
    def test_all_stages_have_packs(self, stage: Stage) -> None:
        pack = get_stage_pack(stage)
        assert pack.stage == stage

    def test_unknown_stage_string_raises(self) -> None:
        with pytest.raises((ValueError, StagePackNotFoundError)):
            get_stage_pack("MEGA_ROUND")  # type: ignore[arg-type]
