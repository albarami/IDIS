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


def test_full_system_inventory_detects_phase_2_3_document_classification_foundation() -> None:
    """Phase 2.3 classification components are detected without claiming live wiring."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["document_classification_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["parser_capability_registry"].status == "PARTIAL"
    assert inventory["document_classification_service"].status == "PARTIAL"
    assert inventory["parser_triage_layer"].status == "PARTIAL"
    assert inventory["document_classification_postgres_persistence"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["document_classification_api_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["document_classification_ui_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["document_classification_run_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_inventory_detects_phase_3_0a_persisted_corpus_baseline() -> None:
    """Phase 3.0A persistence and loader work must be detected without overstating E2E."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["persisted_ingestion_corpus"].status == "PARTIAL"
    assert inventory["unified_run_document_loader"].status == "PARTIAL"
    assert inventory["api_worker_document_corpus_split"].status == "PARTIAL"
    assert any(
        "Methodology/CALC/enrichment/RAG/Neo4j/debate/agents/deliverables" in gap
        for gap in inventory["unified_run_document_loader"].gaps
    )


def test_full_system_report_does_not_overstate_phase_3_0a_full_run_wiring() -> None:
    """The audit must not present Slice 1 as a complete FULL-run integration."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "persisted ingestion corpus" in report.lower()
    assert "unified run document loader" in report.lower()
    assert "methodology/calc/enrichment/rag/neo4j/debate/agents/deliverables" in report.lower()
    assert "real end-to-end FULL diligence flow is wired" not in report


def test_full_system_report_does_not_overstate_document_classification_wiring() -> None:
    """Wiring audit must not claim live document-classification run integration."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "document classification" in report.lower()
    assert "parser triage" in report.lower()
    assert "document classification run integration" in report.lower()
    assert "Document classification is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_2_4_extraction_task_planning_foundation() -> None:
    """Phase 2.4 extraction task planning is detected without claiming live extraction."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["extraction_task_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["extraction_task_planner"].status == "PARTIAL"
    assert inventory["extraction_task_audit_contract"].status == "PARTIAL"
    assert inventory["extraction_task_postgres_persistence"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["extraction_task_api_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["extraction_task_run_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["live_methodology_extraction_execution"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_extraction_task_planning_wiring() -> None:
    """Wiring audit must not claim methodology-driven extraction is live."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "extraction task" in report.lower()
    assert "live methodology extraction execution" in report.lower()
    assert "Methodology-driven extraction is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_2_5_execution_foundation() -> None:
    """Phase 2.5 synthetic execution exists while live integration remains deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_extraction_execution_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["methodology_extraction_task_executor"].status == "PARTIAL"
    assert inventory["methodology_extraction_execution_audit_contract"].status == "PARTIAL"
    assert inventory["methodology_extraction_postgres_persistence"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_extraction_api_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_extraction_run_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_extraction_live_llm_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_extraction_coverage_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_phase_2_5_execution_wiring() -> None:
    """Wiring audit must not claim synthetic task execution is production extraction."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "methodology extraction task executor" in report.lower()
    assert "methodology extraction run integration" in report.lower()
    assert "Methodology extraction execution is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_2_6_claim_materialization_foundation() -> None:
    """Phase 2.6 claim materialization exists while integrations remain deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_claim_materialization_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["methodology_claim_materializer"].status == "PARTIAL"
    assert inventory["methodology_claim_materialization_audit_contract"].status == "PARTIAL"
    assert inventory["methodology_claim_materialization_postgres_schema"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_materialization_api_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_materialization_run_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_materialization_sanad_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_materialization_coverage_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_claim_materialization_wiring() -> None:
    """Wiring audit must not claim claim materialization is production-wired."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "methodology claim materializer" in report.lower()
    assert "claim materialization run integration" in report.lower()
    assert "Methodology claim materialization is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_2_7_sanad_coverage_boundary() -> None:
    """Phase 2.7 boundary exists while live integrations remain deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_sanad_coverage_boundary_models"].status in {
        "WIRED",
        "PARTIAL",
    }
    assert inventory["methodology_sanad_coverage_boundary_service"].status == "PARTIAL"
    assert inventory["methodology_sanad_coverage_boundary_audit_contract"].status == "PARTIAL"
    assert inventory["methodology_sanad_readiness_boundary"].status == "PARTIAL"
    assert inventory["methodology_coverage_decision_boundary"].status == "PARTIAL"
    assert inventory["methodology_live_coverage_updates"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_boundary_sanad_creation"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_boundary_ic_promotion"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_boundary_postgres_persistence"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_boundary_api_run_ui_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_boundary_rag_graph_cache_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_phase_2_7_wiring() -> None:
    """Wiring audit must not claim boundary decisions are live updates."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "sanad readiness boundary" in report.lower()
    assert "coverage update decisions" in report.lower()
    assert "live coverage updates are not wired" in report.lower()
    assert "Sanad creation is wired for methodology boundary decisions" not in report
    assert "Coverage updates are live in production" not in report
    assert "IC-bound promotion is wired" not in report


def test_full_system_inventory_detects_phase_2_8_sanad_creation_boundary() -> None:
    """Phase 2.8 Sanad creation boundary exists while live integrations stay deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_sanad_creation_boundary_models"].status in {
        "WIRED",
        "PARTIAL",
    }
    assert inventory["methodology_sanad_creation_boundary_service"].status == "PARTIAL"
    assert inventory["methodology_sanad_creation_boundary_audit_contract"].status == "PARTIAL"
    assert inventory["methodology_synthetic_sanad_creation_path"].status == "PARTIAL"
    assert inventory["methodology_sanad_creation_claim_link_application"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_sanad_creation_ic_promotion"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_sanad_creation_coverage_updates"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_sanad_creation_postgres_api_run_ui_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_sanad_creation_rag_graph_cache_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_phase_2_8_wiring() -> None:
    """Wiring audit must not claim Phase 2.8 creation is production wired."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "sanad creation boundary" in report.lower()
    assert "synthetic sanad creation path exists when explicitly invoked" in report.lower()
    assert "claim link application is not wired" in report.lower()
    assert "Coverage updates are live in production" not in report
    assert "IC-bound promotion is wired" not in report
    assert "Sanad creation boundary is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_2_9_claim_sanad_link_boundary() -> None:
    """Phase 2.9 Claim-Sanad link boundary exists while promotion stays deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_claim_sanad_link_boundary_models"].status in {
        "WIRED",
        "PARTIAL",
    }
    assert inventory["methodology_claim_sanad_link_boundary_service"].status == "PARTIAL"
    assert inventory["methodology_claim_sanad_link_boundary_audit_contract"].status == "PARTIAL"
    assert inventory["methodology_synthetic_claim_sanad_link_apply_path"].status == "PARTIAL"
    assert inventory["methodology_claim_sanad_link_ic_promotion"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_sanad_link_verdict_action_promotion"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_sanad_link_coverage_updates"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_sanad_link_postgres_api_run_ui_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }
    assert inventory["methodology_claim_sanad_link_rag_graph_cache_integration"].status in {
        "NOT_FOUND",
        "DEFERRED",
    }


def test_full_system_report_does_not_overstate_phase_2_9_wiring() -> None:
    """Wiring audit must not claim Phase 2.9 links are IC promotion."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "claim-sanad link boundary" in report.lower()
    assert "explicit synthetic claimservice.update path exists when invoked" in report.lower()
    assert "ic promotion is not wired" in report.lower()
    assert "claim verdict/action promotion is not wired" in report.lower()
    assert "coverage updates are not wired" in report.lower()
    assert "claim-sanad link boundary is fully wired into production runs" not in report
