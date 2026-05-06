"""Phase 2.0 full-system wiring baseline tests.

These tests intentionally use static/synthetic evidence only. They do not
ingest real data rooms, call external APIs, or require live infrastructure.
"""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_full_system_inventory_marks_core_run_wiring_truthfully() -> None:
    """The baseline must distinguish wired run steps from stubs and missing layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["api_run_path"].status == "WIRED"
    assert inventory["worker_executor_path"].status == "PARTIAL"
    assert inventory["snapshot_steps"].status == "WIRED"
    assert inventory["full_steps"].status == "PARTIAL"
    assert inventory["calc_step"].status == "STUBBED"
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
