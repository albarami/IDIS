"""Slice87 Task 4 — typed deterministic calc-derived financial-table builder + additive feeds.

A typed FinancialTable is derived from the deterministic calculations already present in the
analysis context (same eligibility gate as the existing calculation_package: a CalcSanad id plus a
valid reproducibility hash). It is then fed two ways, both additively:

  - memo financials: one calc-derived fact per row, carrying claim_refs (the calc's input claims,
    for No-Free-Facts) and calc_refs — alongside the existing LLM financial_agent facts.
  - VC bundle: a sanitized `financial_table` block inside the financial_diligence artifact, next to
    the preserved `calculation_package`.

Task 4 is narrow: no graph/RAG, no Task 5, no Slice88.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.analysis.models import AnalysisCalcReference, AnalysisContext
from idis.deliverables.financial_table import build_financial_table
from idis.models.deliverables import FinancialTable, FinancialTableRow
from idis.storage.filesystem_store import FilesystemObjectStore

# Shared deliverables fixtures.
from tests.test_deliverables_generator import _TIMESTAMP, _make_bundle, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
    _make_deliverables_bundle,
)

_HASH_A = "a" * 64
_HASH_F = "f" * 64


def _calc_ref(
    calc_id: str,
    calc_type: str,
    *,
    repro: str | None = _HASH_A,
    sanad: str | None = "sanad-x",
    primary: str = "60.0000",
    unit: str | None = "%",
    claims: tuple[str, ...] = ("claim-001",),
) -> AnalysisCalcReference:
    return AnalysisCalcReference(
        calc_id=calc_id,
        calc_type=calc_type,
        output_summary=f"{primary} {unit}".strip(),
        input_claim_ids=list(claims),
        reproducibility_hash=repro,
        calc_sanad_id=sanad,
        formula_hash=_HASH_F,
        code_version="test-1.0.0",
        output={"primary_value": primary, "unit": unit, "currency": None},
        calc_grade="B",
        input_min_sanad_grade="B",
    )


# --- builder ---


def test_build_financial_table_produces_typed_rows_for_eligible_calcs() -> None:
    registry = {
        "calc-2": _calc_ref("calc-2", "RUNWAY", primary="12.0000", unit="months"),
        "calc-1": _calc_ref("calc-1", "GROSS_MARGIN", primary="60.0000", unit="%"),
    }
    table = build_financial_table(registry)
    assert isinstance(table, FinancialTable)
    assert table.row_count == 2
    assert [r.calc_id for r in table.rows] == ["calc-1", "calc-2"]  # sorted by calc_id

    row = table.rows[0]
    assert isinstance(row, FinancialTableRow)
    assert row.calc_type == "GROSS_MARGIN"
    assert row.label == "Gross Margin"
    assert row.primary_value == "60.0000"
    assert row.unit == "%"
    assert row.reproducibility_hash == _HASH_A
    assert row.calc_sanad_id == "sanad-x"
    assert row.formula_hash == _HASH_F
    assert row.code_version == "test-1.0.0"
    assert row.calc_grade == "B"
    assert row.input_claim_ids == ["claim-001"]


def test_build_financial_table_filters_ineligible_calcs() -> None:
    registry = {
        "calc-ok": _calc_ref("calc-ok", "MOIC"),
        "calc-badhash": _calc_ref("calc-badhash", "RUNWAY", repro="not-a-sha256"),
        "calc-nosanad": _calc_ref("calc-nosanad", "LTV", sanad=None),
    }
    table = build_financial_table(registry)
    assert [r.calc_id for r in table.rows] == ["calc-ok"]


def test_build_financial_table_labels_known_types_and_falls_back() -> None:
    registry = {
        "c-moic": _calc_ref("c-moic", "MOIC"),
        "c-nrr": _calc_ref("c-nrr", "NRR"),
        "c-cac": _calc_ref("c-cac", "CAC_PAYBACK"),
        "c-mystery": _calc_ref("c-mystery", "MYSTERY_METRIC"),
    }
    labels = {r.calc_type: r.label for r in build_financial_table(registry).rows}
    assert labels["MOIC"] == "MOIC"
    assert labels["NRR"] == "Net Revenue Retention"
    assert labels["CAC_PAYBACK"] == "CAC Payback"
    assert labels["MYSTERY_METRIC"] == "MYSTERY_METRIC"  # unknown → raw calc_type


# --- additive feeds ---


def _context_with_eligible_calc() -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        claim_ids=frozenset({"claim-001", "claim-002"}),
        calc_ids=frozenset({"calc-001", "calc-fin-001"}),
        company_name="Acme Corp",
        stage="SERIES_A",
        sector="Fintech",
        calc_registry={
            "calc-fin-001": _calc_ref(
                "calc-fin-001", "GROSS_MARGIN", primary="60.0000", unit="%", claims=("claim-001",)
            )
        },
    )


def test_memo_financials_fed_additively_from_calcs_preserving_llm_bridge() -> None:
    from idis.audit.sink import InMemoryAuditSink
    from idis.deliverables.generator import DeliverablesGenerator

    result = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_context_with_eligible_calc(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-task4",
    )
    financials = result.ic_memo.financials.facts

    # The calc-derived fact is present, carrying both claim_refs (No-Free-Facts) and calc_refs.
    calc_facts = [f for f in financials if "calc-fin-001" in f.calc_refs]
    assert len(calc_facts) == 1
    assert calc_facts[0].claim_refs  # non-empty → satisfies No-Free-Facts
    assert "60.0000" in calc_facts[0].text

    # The existing LLM financial_agent bridge is preserved (its claim-001 facts remain).
    llm_facts = [f for f in financials if "calc-fin-001" not in f.calc_refs]
    assert llm_facts  # agent-bridged financial facts still present


def test_vc_bundle_has_financial_table_block_and_preserves_calc_package(tmp_path: Path) -> None:
    from idis.deliverables.product_bundle import ProductBundleExporter

    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    ctx = _context_with_eligible_calc()
    # Align fixture ids with the export call's tenant/deal/run scoping.
    ctx = ctx.model_copy(update={"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID})

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=ctx,
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )

    body = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/financial_diligence.json",
        ).body.decode("utf-8")
    )
    # The existing calculation_package is preserved.
    assert "calculation_package" in body
    # The new sanitized financial_table block is present with the eligible calc row.
    assert "financial_table" in body
    ft: dict[str, Any] = body["financial_table"]
    assert ft["row_count"] == 1
    assert ft["rows"][0]["calc_id"] == "calc-fin-001"
    assert ft["rows"][0]["label"] == "Gross Margin"
    assert ft["rows"][0]["reproducibility_hash"] == _HASH_A
