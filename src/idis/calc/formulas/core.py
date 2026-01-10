"""Core formulas for deterministic calculations.

Phase 4.1: Minimal formula set for tests (runway, gross_margin).
All formulas use Decimal arithmetic exclusively.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from idis.calc.formulas.registry import FormulaRegistry, FormulaSpec
from idis.models.deterministic_calculation import CalcType


def _runway_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate runway in months.

    Formula: runway_months = cash_balance / monthly_burn_rate

    Required inputs:
        - cash_balance: Current cash balance (Decimal)
        - monthly_burn_rate: Monthly cash burn rate (Decimal, must be > 0)

    Returns:
        Runway in months (Decimal).

    Raises:
        ValueError: If monthly_burn_rate is zero or negative.
    """
    cash_balance = inputs["cash_balance"]
    monthly_burn_rate = inputs["monthly_burn_rate"]

    if monthly_burn_rate <= Decimal("0"):
        raise ValueError("monthly_burn_rate must be positive")

    runway = cash_balance / monthly_burn_rate
    return runway.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _gross_margin_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate gross margin percentage.

    Formula: gross_margin = (revenue - cogs) / revenue * 100

    Required inputs:
        - revenue: Total revenue (Decimal, must be > 0)
        - cogs: Cost of goods sold (Decimal)

    Returns:
        Gross margin as percentage (Decimal, e.g., 65.5000 for 65.5%).

    Raises:
        ValueError: If revenue is zero or negative.
    """
    revenue = inputs["revenue"]
    cogs = inputs["cogs"]

    if revenue <= Decimal("0"):
        raise ValueError("revenue must be positive")

    gross_margin = ((revenue - cogs) / revenue) * Decimal("100")
    return gross_margin.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _burn_rate_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate monthly burn rate.

    Formula: burn_rate = (starting_cash - ending_cash) / months

    Required inputs:
        - starting_cash: Cash at period start (Decimal)
        - ending_cash: Cash at period end (Decimal)
        - months: Number of months in period (Decimal, must be > 0)

    Returns:
        Monthly burn rate (Decimal).

    Raises:
        ValueError: If months is zero or negative.
    """
    starting_cash = inputs["starting_cash"]
    ending_cash = inputs["ending_cash"]
    months = inputs["months"]

    if months <= Decimal("0"):
        raise ValueError("months must be positive")

    burn_rate = (starting_cash - ending_cash) / months
    return burn_rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _ltv_cac_ratio_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate LTV/CAC ratio.

    Formula: ltv_cac_ratio = ltv / cac

    Required inputs:
        - ltv: Lifetime value (Decimal)
        - cac: Customer acquisition cost (Decimal, must be > 0)

    Returns:
        LTV/CAC ratio (Decimal).

    Raises:
        ValueError: If cac is zero or negative.
    """
    ltv = inputs["ltv"]
    cac = inputs["cac"]

    if cac <= Decimal("0"):
        raise ValueError("cac must be positive")

    ratio = ltv / cac
    return ratio.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


RUNWAY_SPEC = FormulaSpec(
    calc_type=CalcType.RUNWAY,
    version="1.0.0",
    expression_id="runway_cash_burn_v1",
    fn=_runway_formula,
    required_inputs=("cash_balance", "monthly_burn_rate"),
    output_precision=4,
)

GROSS_MARGIN_SPEC = FormulaSpec(
    calc_type=CalcType.GROSS_MARGIN,
    version="1.0.0",
    expression_id="gross_margin_revenue_cogs_v1",
    fn=_gross_margin_formula,
    required_inputs=("revenue", "cogs"),
    output_precision=4,
)

BURN_RATE_SPEC = FormulaSpec(
    calc_type=CalcType.BURN_RATE,
    version="1.0.0",
    expression_id="burn_rate_cash_delta_v1",
    fn=_burn_rate_formula,
    required_inputs=("starting_cash", "ending_cash", "months"),
    output_precision=4,
)

LTV_CAC_RATIO_SPEC = FormulaSpec(
    calc_type=CalcType.LTV_CAC_RATIO,
    version="1.0.0",
    expression_id="ltv_cac_ratio_v1",
    fn=_ltv_cac_ratio_formula,
    required_inputs=("ltv", "cac"),
    output_precision=4,
)


def register_core_formulas(registry: FormulaRegistry | None = None) -> FormulaRegistry:
    """Register all core formulas with the registry.

    Args:
        registry: Optional registry to use. If None, uses the singleton.

    Returns:
        The registry with core formulas registered.
    """
    if registry is None:
        registry = FormulaRegistry()

    for spec in [RUNWAY_SPEC, GROSS_MARGIN_SPEC, BURN_RATE_SPEC, LTV_CAC_RATIO_SPEC]:
        if registry.get(spec.calc_type) is None:
            registry.register(spec)

    return registry
