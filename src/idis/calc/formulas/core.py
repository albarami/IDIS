"""Core formulas for deterministic calculations.

Covers 9 of the 10 CalcTypes: RUNWAY, GROSS_MARGIN, BURN_RATE, LTV_CAC_RATIO, MOIC,
VALUATION_MULTIPLE, NET_REVENUE_RETENTION, CAC_PAYBACK, and LTV. IRR is deferred until a
cash-flow-series input model exists (formulas take a flat dict[str, Decimal] of scalars).
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


def _moic_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate Multiple on Invested Capital (MOIC).

    Formula: moic = total_value / invested_capital

    Required inputs:
        - total_value: Current/realized value of the investment (Decimal)
        - invested_capital: Capital invested (Decimal, must be > 0)

    Returns:
        MOIC as a multiple (Decimal, e.g., 3.0000 for 3x).

    Raises:
        ValueError: If invested_capital is zero or negative.
    """
    total_value = inputs["total_value"]
    invested_capital = inputs["invested_capital"]

    if invested_capital <= Decimal("0"):
        raise ValueError("invested_capital must be positive")

    moic = total_value / invested_capital
    return moic.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _valuation_multiple_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate a revenue valuation multiple.

    Formula: valuation_multiple = valuation / revenue

    Required inputs:
        - valuation: Company valuation / enterprise value (Decimal)
        - revenue: Revenue basis for the multiple (Decimal, must be > 0)

    Returns:
        Valuation multiple (Decimal, e.g., 5.0000 for 5x revenue).

    Raises:
        ValueError: If revenue is zero or negative.
    """
    valuation = inputs["valuation"]
    revenue = inputs["revenue"]

    if revenue <= Decimal("0"):
        raise ValueError("revenue must be positive")

    multiple = valuation / revenue
    return multiple.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _net_revenue_retention_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate Net Revenue Retention (NRR) as a percentage.

    Formula: nrr = (starting_arr + expansion - contraction - churn) / starting_arr * 100

    Required inputs:
        - starting_arr: ARR at period start (Decimal, must be > 0)
        - expansion: Expansion ARR from existing customers (Decimal)
        - contraction: Contraction ARR from existing customers (Decimal)
        - churn: Churned ARR from existing customers (Decimal)

    Returns:
        NRR as a percentage (Decimal, e.g., 105.0000 for 105%).

    Raises:
        ValueError: If starting_arr is zero or negative.
    """
    starting_arr = inputs["starting_arr"]
    expansion = inputs["expansion"]
    contraction = inputs["contraction"]
    churn = inputs["churn"]

    if starting_arr <= Decimal("0"):
        raise ValueError("starting_arr must be positive")

    retained = starting_arr + expansion - contraction - churn
    nrr = (retained / starting_arr) * Decimal("100")
    return nrr.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _cac_payback_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate CAC payback period in months.

    Formula: cac_payback_months = cac / monthly_gross_profit

    Required inputs:
        - cac: Customer acquisition cost (Decimal)
        - monthly_gross_profit: Monthly gross profit per customer (Decimal, must be > 0)

    Returns:
        CAC payback period in months (Decimal).

    Raises:
        ValueError: If monthly_gross_profit is zero or negative.
    """
    cac = inputs["cac"]
    monthly_gross_profit = inputs["monthly_gross_profit"]

    if monthly_gross_profit <= Decimal("0"):
        raise ValueError("monthly_gross_profit must be positive")

    payback = cac / monthly_gross_profit
    return payback.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _ltv_formula(inputs: dict[str, Decimal]) -> Decimal:
    """Calculate customer Lifetime Value (LTV).

    Formula: ltv = (arpa * gross_margin_rate) / churn_rate

    Required inputs:
        - arpa: Average revenue per account per period (Decimal)
        - gross_margin_rate: Gross margin as a fraction, e.g. 0.8 (Decimal)
        - churn_rate: Customer churn rate per period as a fraction (Decimal, must be > 0)

    Returns:
        Lifetime value (Decimal).

    Raises:
        ValueError: If churn_rate is zero or negative.
    """
    arpa = inputs["arpa"]
    gross_margin_rate = inputs["gross_margin_rate"]
    churn_rate = inputs["churn_rate"]

    if churn_rate <= Decimal("0"):
        raise ValueError("churn_rate must be positive")

    ltv = (arpa * gross_margin_rate) / churn_rate
    return ltv.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


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

MOIC_SPEC = FormulaSpec(
    calc_type=CalcType.MOIC,
    version="1.0.0",
    expression_id="moic_total_value_invested_v1",
    fn=_moic_formula,
    required_inputs=("total_value", "invested_capital"),
    output_precision=4,
)

VALUATION_MULTIPLE_SPEC = FormulaSpec(
    calc_type=CalcType.VALUATION_MULTIPLE,
    version="1.0.0",
    expression_id="valuation_multiple_revenue_v1",
    fn=_valuation_multiple_formula,
    required_inputs=("valuation", "revenue"),
    output_precision=4,
)

NET_REVENUE_RETENTION_SPEC = FormulaSpec(
    calc_type=CalcType.NET_REVENUE_RETENTION,
    version="1.0.0",
    expression_id="nrr_expansion_contraction_churn_v1",
    fn=_net_revenue_retention_formula,
    required_inputs=("starting_arr", "expansion", "contraction", "churn"),
    output_precision=4,
)

CAC_PAYBACK_SPEC = FormulaSpec(
    calc_type=CalcType.CAC_PAYBACK,
    version="1.0.0",
    expression_id="cac_payback_months_v1",
    fn=_cac_payback_formula,
    required_inputs=("cac", "monthly_gross_profit"),
    output_precision=4,
)

LTV_SPEC = FormulaSpec(
    calc_type=CalcType.LTV,
    version="1.0.0",
    expression_id="ltv_arpa_margin_churn_v1",
    fn=_ltv_formula,
    required_inputs=("arpa", "gross_margin_rate", "churn_rate"),
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

    for spec in [
        RUNWAY_SPEC,
        GROSS_MARGIN_SPEC,
        BURN_RATE_SPEC,
        LTV_CAC_RATIO_SPEC,
        MOIC_SPEC,
        VALUATION_MULTIPLE_SPEC,
        NET_REVENUE_RETENTION_SPEC,
        CAC_PAYBACK_SPEC,
        LTV_SPEC,
    ]:
        if registry.get(spec.calc_type) is None:
            registry.register(spec)

    return registry
