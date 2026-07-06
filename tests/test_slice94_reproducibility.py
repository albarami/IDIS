"""Slice94 Task 4 — reproducibility acceptance (frozen assumptions), the remaining pin.

Task 3 proved the financial *tables* reproduce byte-identically; it asserted `financial_table`
rows (which carry no assumptions) plus a whole-blob identity. This pins the precise remaining
acceptance — "financial tables and **assumptions** are reproducible" — on the calc package:

  - the calc's frozen ``assumptions`` are surfaced faithfully (non-empty) in the export,
  - they are byte-identical across independent re-runs,
  - and each exported calc carries the full reproducibility contract (output hash + formula
    identity + engine version + input-claim lineage + assumptions).

Injected fakes only — no real Anthropic; filesystem object store; no database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.analysis.models import AnalysisCalcReference, AnalysisContext
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import _TIMESTAMP, _make_bundle, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
)

_REPRO_HASH = "b" * 64  # valid sha256-hex
_FORMULA_HASH = "f" * 64
_ASSUMPTIONS = {"arr_growth_rate": "0.40", "gross_margin": "0.60", "churn_monthly": "0.02"}


def _calc_with_assumptions() -> AnalysisCalcReference:
    return AnalysisCalcReference(
        calc_id="calc-fin-001",
        calc_type="GROSS_MARGIN",
        output_summary="60.0000 %",
        input_claim_ids=["claim-001"],
        reproducibility_hash=_REPRO_HASH,
        calc_sanad_id="sanad-x",
        formula_hash=_FORMULA_HASH,
        code_version="calc-engine-1.2.3",
        formula_version="gross-margin-v1",
        output={"primary_value": "60.0000", "unit": "%", "currency": None},
        assumptions=_ASSUMPTIONS,
        calc_grade="B",
        input_min_sanad_grade="B",
    )


def _context() -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        claim_ids=frozenset({"claim-001", "claim-002"}),
        calc_ids=frozenset({"calc-001", "calc-fin-001"}),
        company_name="Acme Corp",
        stage="SERIES_A",
        sector="Fintech",
        calc_registry={"calc-fin-001": _calc_with_assumptions()},
    )


def _export_calc_package(tmp_path: Path) -> dict[str, Any]:
    ctx = _context()
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=ctx,
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-repro",
    )
    store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=store,
        object_store_backend="filesystem",
    ).export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=bundle,
        analysis_context=ctx,
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )
    obj = store.get(
        tenant_id=TENANT_ID, key=f"runs/{RUN_ID}/product_bundle/financial_diligence.json"
    )
    return json.loads(obj.body.decode("utf-8"))["calculation_package"]


# --- Frozen assumptions are surfaced faithfully and reproduce across re-runs ---


def test_calc_assumptions_surfaced_and_frozen_across_reruns(tmp_path: Path) -> None:
    calc_a = _export_calc_package(tmp_path / "a")["calculations"][0]
    calc_b = _export_calc_package(tmp_path / "b")["calculations"][0]

    # Surfaced faithfully and non-empty (never an empty dict).
    assert calc_a["assumptions"] == _ASSUMPTIONS
    assert calc_a["assumptions"]
    # Frozen: byte-identical assumptions across independent exports.
    assert calc_a["assumptions"] == calc_b["assumptions"]
    # The reproducibility hash binding the deterministic output is stable across re-runs.
    assert calc_a["reproducibility_hash"] == calc_b["reproducibility_hash"] == _REPRO_HASH


# --- Each exported calc carries the full reproducibility contract ---


def test_calc_carries_full_reproducibility_contract(tmp_path: Path) -> None:
    calc = _export_calc_package(tmp_path)["calculations"][0]
    for field in (
        "reproducibility_hash",
        "formula_hash",
        "formula_version",
        "code_version",
        "input_claim_ids",
        "assumptions",
    ):
        assert calc[field], f"missing reproducibility field: {field}"
    assert len(calc["reproducibility_hash"]) == 64
    assert calc["input_claim_ids"] == ["claim-001"]
    assert calc["assumptions"] == _ASSUMPTIONS
