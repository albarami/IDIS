"""Typed deterministic calc-derived financial-table builder.

Derives a typed FinancialTable from the deterministic calculations in an analysis context's
calc_registry, using the same eligibility gate as the product-bundle calculation_package: a row is
included only when it has a CalcSanad id and a valid reproducibility hash. The table is consumed
additively by the memo financials section and the VC bundle's financial_table block; it never
replaces the existing LLM financial_agent bridge or the calculation_package.
"""

from __future__ import annotations

import re
from typing import Any

from idis.analysis.models import AnalysisCalcReference
from idis.models.deliverables import FinancialTable, FinancialTableRow

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Deterministic human-readable labels per CalcType (falls back to the raw calc_type).
_CALC_TYPE_LABELS: dict[str, str] = {
    "RUNWAY": "Runway",
    "GROSS_MARGIN": "Gross Margin",
    "BURN_RATE": "Burn Rate",
    "LTV_CAC_RATIO": "LTV/CAC Ratio",
    "MOIC": "MOIC",
    "VALUATION_MULTIPLE": "Valuation Multiple",
    "NRR": "Net Revenue Retention",
    "CAC_PAYBACK": "CAC Payback",
    "LTV": "Lifetime Value",
    "IRR": "IRR",
}


def _is_sha256_hex(value: str | None) -> bool:
    return bool(value) and bool(_SHA256_RE.match(str(value)))


def _label_for(calc_type: str) -> str:
    return _CALC_TYPE_LABELS.get(calc_type, calc_type)


def _output_field(output: Any, key: str) -> Any:
    if isinstance(output, dict):
        return output.get(key)
    return None


def build_financial_table(calc_registry: dict[str, AnalysisCalcReference]) -> FinancialTable:
    """Build a typed FinancialTable from eligible deterministic calculations.

    Eligibility matches the calculation_package gate: a CalcSanad id plus a valid reproducibility
    hash. Rows are ordered deterministically by calc_id.
    """
    rows: list[FinancialTableRow] = []
    for calc_id, calc in sorted(calc_registry.items()):
        if not calc.calc_sanad_id or not _is_sha256_hex(calc.reproducibility_hash):
            continue
        primary = _output_field(calc.output, "primary_value")
        rows.append(
            FinancialTableRow(
                calc_id=calc_id,
                calc_type=calc.calc_type,
                label=_label_for(calc.calc_type),
                primary_value=str(primary) if primary is not None else None,
                unit=_output_field(calc.output, "unit"),
                currency=_output_field(calc.output, "currency"),
                output_summary=calc.output_summary,
                calc_sanad_id=calc.calc_sanad_id,
                formula_hash=calc.formula_hash,
                code_version=calc.code_version,
                reproducibility_hash=calc.reproducibility_hash,
                calc_grade=calc.calc_grade,
                input_min_sanad_grade=calc.input_min_sanad_grade,
                input_claim_ids=list(calc.input_claim_ids),
            )
        )
    return FinancialTable(rows=rows, row_count=len(rows))
