"""Slice 60 calculation visibility and proof tests."""

from __future__ import annotations

import json
from pathlib import Path

from idis.analysis.models import AnalysisCalcReference, AnalysisContext
from idis.api.routes.runs import _safe_public_run_summary_dict
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import _TIMESTAMP, _make_context, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
    _make_deliverables_bundle,
)

VALID_REPRO_HASH = "a" * 64
VALID_FORMULA_HASH = "b" * 64


def _context_with_calc_package() -> AnalysisContext:
    """Return an analysis context with one structured calculation reference."""
    calc_ref = AnalysisCalcReference(
        calc_id="calc-margin",
        calc_type="GROSS_MARGIN",
        output_summary="60.0000 %",
        input_claim_ids=["claim-cogs", "claim-revenue"],
        source_summary="GROSS_MARGIN from 2 input claims",
        reproducibility_hash=VALID_REPRO_HASH,
        calc_sanad_id="calc-sanad-margin",
        formula_hash=VALID_FORMULA_HASH,
        code_version="0.1.0",
        output={"primary_value": "60.0000", "unit": "percent", "currency": None},
        assumptions={
            "formula": "(revenue - cogs) / revenue * 100",
            "inputs": {"revenue": "1000", "cogs": "400"},
        },
        calc_grade="A",
        input_min_sanad_grade="A",
    )
    return _make_context().model_copy(
        update={
            "calc_ids": frozenset({"calc-margin"}),
            "calc_registry": {"calc-margin": calc_ref},
        }
    )


def _export_bundle(tmp_path: Path, analysis_context: AnalysisContext) -> FilesystemObjectStore:
    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )
    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=analysis_context,
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )
    return object_store


def _stored_json(object_store: FilesystemObjectStore, filename: str) -> dict[str, object]:
    stored = object_store.get(
        tenant_id=TENANT_ID,
        key=f"runs/{RUN_ID}/product_bundle/{filename}",
    )
    return json.loads(stored.body.decode("utf-8"))


def test_financial_diligence_json_includes_structured_calc_package(tmp_path: Path) -> None:
    """Financial diligence must surface durable calculations with reproducibility proof."""
    object_store = _export_bundle(tmp_path, _context_with_calc_package())

    financial = _stored_json(object_store, "financial_diligence.json")

    package = financial["calculation_package"]
    assert package["status"] == "calculations_available"
    assert package["calc_count"] == 1
    assert package["calc_sanad_count"] == 1
    assert package["calculations"] == [
        {
            "calc_id": "calc-margin",
            "calc_sanad_id": "calc-sanad-margin",
            "calc_type": "GROSS_MARGIN",
            "input_claim_ids": ["claim-cogs", "claim-revenue"],
            "assumptions": {
                "formula": "(revenue - cogs) / revenue * 100",
                "inputs": {"cogs": "400", "revenue": "1000"},
            },
            "output": {"currency": None, "primary_value": "60.0000", "unit": "percent"},
            "formula_hash": VALID_FORMULA_HASH,
            "code_version": "0.1.0",
            "reproducibility_hash": VALID_REPRO_HASH,
            "calc_grade": "A",
            "input_min_sanad_grade": "A",
        }
    ]


def test_evidence_index_json_includes_calc_entries_with_calc_sanad_links(
    tmp_path: Path,
) -> None:
    """Evidence index must expose calc evidence without raw source text."""
    object_store = _export_bundle(tmp_path, _context_with_calc_package())

    evidence_index = _stored_json(object_store, "evidence_index.json")

    assert evidence_index["calc_entries"] == [
        {
            "calc_id": "calc-margin",
            "calc_sanad_id": "calc-sanad-margin",
            "source_claim_ids": ["claim-cogs", "claim-revenue"],
            "reproducibility_hash": VALID_REPRO_HASH,
        }
    ]
    encoded = json.dumps(evidence_index, sort_keys=True)
    assert "raw_text" not in encoded
    assert ".local_reports" not in encoded
    assert "C:\\Projects" not in encoded


def test_run_summary_json_includes_calc_counts_ids_and_hashes(tmp_path: Path) -> None:
    """Run summary artifact must include safe calc counts, IDs, Sanad IDs, and hashes."""
    object_store = _export_bundle(tmp_path, _context_with_calc_package())

    run_summary = _stored_json(object_store, "run_summary.json")

    assert run_summary["calculation_status"] == "calculations_available"
    assert run_summary["calc_count"] == 1
    assert run_summary["calc_ids"] == ["calc-margin"]
    assert run_summary["calc_sanad_count"] == 1
    assert run_summary["calc_sanad_ids"] == ["calc-sanad-margin"]
    assert run_summary["reproducibility_hashes"] == [VALID_REPRO_HASH]


def test_run_summary_json_reports_no_eligible_calculations_when_empty(tmp_path: Path) -> None:
    """Empty calc registry must be honest, not pretend calculation success."""
    object_store = _export_bundle(tmp_path, _make_context())

    run_summary = _stored_json(object_store, "run_summary.json")

    assert run_summary["calculation_status"] == "no_eligible_calculations"
    assert run_summary["calc_count"] == 0
    assert run_summary["calc_ids"] == []
    assert run_summary["calc_sanad_count"] == 0
    assert run_summary["calc_sanad_ids"] == []
    assert run_summary["reproducibility_hashes"] == []


def test_incomplete_calc_references_are_not_reported_as_product_visible(
    tmp_path: Path,
) -> None:
    """Calc refs without CalcSanad and hash proof must not count as available calcs."""
    incomplete_calc = AnalysisCalcReference(
        calc_id="calc-incomplete",
        calc_type="RUNWAY",
        output_summary="12 months",
        input_claim_ids=["claim-cash"],
        reproducibility_hash="not-a-sha",
    )
    context = _make_context().model_copy(
        update={
            "calc_ids": frozenset({"calc-incomplete"}),
            "calc_registry": {"calc-incomplete": incomplete_calc},
        }
    )
    object_store = _export_bundle(tmp_path, context)

    financial = _stored_json(object_store, "financial_diligence.json")
    evidence_index = _stored_json(object_store, "evidence_index.json")
    run_summary = _stored_json(object_store, "run_summary.json")

    assert financial["calculation_package"]["status"] == "no_eligible_calculations"
    assert financial["calculation_package"]["calc_count"] == 0
    assert financial["calculation_package"]["calculations"] == []
    assert evidence_index["calc_entries"] == []
    assert run_summary["calculation_status"] == "no_eligible_calculations"
    assert run_summary["calc_count"] == 0
    assert run_summary["calc_ids"] == []
    assert run_summary["calc_sanad_count"] == 0
    assert run_summary["calc_sanad_ids"] == []
    assert run_summary["reproducibility_hashes"] == []


def test_public_run_step_summary_exposes_only_safe_calc_summary_fields() -> None:
    """Public run summaries must keep hashes only when they are valid SHA-256 hex."""
    public = _safe_public_run_summary_dict(
        {
            "calc_count": 1,
            "calc_ids": ["calc-margin"],
            "calc_sanad_count": 1,
            "calc_sanad_ids": ["calc-sanad-margin"],
            "reproducibility_hashes": [
                VALID_REPRO_HASH,
                "not-a-sha",
                "C:\\Projects\\private\\hash.txt",
            ],
            "blocked_candidate_count": 1,
            "blocked_candidate_reason_counts": {"missing_required_claim": 1},
            "blocked_candidates": [
                {
                    "calc_type": "RUNWAY",
                    "reason": "missing_required_claim",
                    "claim_ids": ["claim-private"],
                    "missing_inputs": ["cash_balance", "monthly_burn_rate"],
                }
            ],
        }
    )

    assert public == {
        "calc_count": 1,
        "calc_ids": ["calc-margin"],
        "calc_sanad_count": 1,
        "calc_sanad_ids": ["calc-sanad-margin"],
        "reproducibility_hashes": [VALID_REPRO_HASH],
        "blocked_candidate_count": 1,
        "blocked_candidate_reason_counts": {"missing_required_claim": 1},
    }


def test_strict_readiness_clears_calculation_visibility_only_with_db_and_product_export(
    tmp_path: Path,
) -> None:
    """Calculation engine and CalcSanad strict rows clear only when output-visible."""
    blocked = build_strict_full_live_readiness_report(env={})
    blocked_inventory = {item.component_name: item for item in blocked.component_inventory}

    assert blocked_inventory["calculation engine"].output_visible is False
    assert blocked_inventory["CalcSanad"].output_visible is False
    assert "calculation engine" in blocked.blocking_components
    assert "CalcSanad" in blocked.blocking_components

    configured = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://app@db/idis",
            "IDIS_API_KEYS": "configured",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
        }
    )
    inventory = {item.component_name: item for item in configured.component_inventory}

    assert inventory["calculation engine"].full_wired is True
    assert inventory["calculation engine"].output_visible is True
    assert inventory["CalcSanad"].full_wired is True
    assert inventory["CalcSanad"].output_visible is True
    assert "calculation engine" not in configured.blocking_components
    assert "CalcSanad" not in configured.blocking_components
