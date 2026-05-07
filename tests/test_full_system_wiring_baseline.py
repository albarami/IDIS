"""Phase 2.0 full-system wiring baseline tests.

These tests intentionally use static/synthetic evidence only. They do not
ingest real data rooms, call external APIs, or require live infrastructure.
"""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory, render_report

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_full_system_inventory_marks_core_run_wiring_truthfully() -> None:
    """The baseline must distinguish wired run steps from missing future layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["api_run_path"].status == "WIRED"
    assert inventory["worker_executor_path"].status == "WIRED"
    assert inventory["snapshot_steps"].status == "WIRED"
    assert inventory["full_steps"].status == "PARTIAL"
    assert inventory["calc_step"].status == "WIRED"
    assert inventory["debate_layer_1"].status == "WIRED"
    assert inventory["debate_layer_2"].status == "NOT_FOUND"


def test_full_system_inventory_includes_report_required_sections() -> None:
    """Generated report data must cover the required Phase 2.0 report sections."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    report_sections = {section.heading for section in inventory.report_sections}

    assert {
        "Executive Summary",
        "Full-System Wiring Matrix",
        "Infrastructure Matrix",
        "API Run Path vs Worker Path Comparison",
        "SNAPSHOT Step Outputs",
        "FULL Step Outputs",
        "Stubbed and Config-Only Components",
        "Missing Components",
        "Existing Components Not Wired",
        "Risk Ranking",
        "Recommended Phase 2.1 Starting Point",
        "Exact Evidence From Tests and Script",
        "Validation Commands and Results",
    }.issubset(report_sections)


def test_full_system_report_renders_phase_2_1_canonical_truthfulness() -> None:
    """Rendered report text must not retain stale Phase 2.0 split/stub claims."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "worker.py` polls queued runs and calls `PipelineExecutor`" not in report
    assert "CALC step can complete without durable deterministic calculations" not in report
    assert "RunExecutionService" in report
    assert "CalcRunner" in report


def test_full_system_inventory_detects_phase_2_2_methodology_foundation() -> None:
    """Phase 2.2 methodology components are detected without claiming run integration."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_registry_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["fdd_synthetic_excel_importer"].status == "PARTIAL"
    assert inventory["commercial_dd_template"].status == "PARTIAL"
    assert inventory["methodology_coverage_service"].status == "PARTIAL"
    assert inventory["methodology_postgres_persistence"].status in {"NOT_FOUND", "DEFERRED"}
    assert inventory["methodology_run_integration"].status in {"NOT_FOUND", "DEFERRED"}


def test_full_system_report_does_not_overstate_methodology_run_wiring() -> None:
    """Wiring audit must remain truthful about deferred methodology run integration."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "methodology registry" in report.lower()
    assert "coverage" in report.lower()
    assert "methodology run integration" in report.lower()
    assert "Methodology registry is fully wired into production runs" not in report
