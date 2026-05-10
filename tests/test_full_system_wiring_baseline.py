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
    """Methodology components are detected without claiming extraction planning."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert inventory["methodology_registry_models"].status in {"WIRED", "PARTIAL"}
    assert inventory["fdd_synthetic_excel_importer"].status == "PARTIAL"
    assert inventory["commercial_dd_template"].status == "PARTIAL"
    assert inventory["methodology_coverage_service"].status == "PARTIAL"
    assert inventory["methodology_postgres_persistence"].status in {"NOT_FOUND", "DEFERRED"}
    assert inventory["methodology_run_integration"].status == "PARTIAL"


def test_full_system_report_does_not_overstate_methodology_run_wiring() -> None:
    """Wiring audit must remain truthful about deferred methodology run integration."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "methodology registry" in report.lower()
    assert "coverage" in report.lower()
    assert "methodology run integration" in report.lower()
    assert "Methodology registry is fully wired into production runs" not in report


def test_full_system_inventory_detects_phase_3_0c_coverage_init_baseline() -> None:
    """Slice 3 coverage init is visible while coverage persistence remains deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    coverage_init = inventory["methodology_coverage_init_run_integration"]
    extraction_task = inventory["extraction_task_run_integration"]

    assert coverage_init.status == "PARTIAL"
    assert any("METHODOLOGY_COVERAGE_INIT" in item for item in coverage_init.evidence)
    assert any("commercial_dd_v1.json" in item for item in coverage_init.evidence)
    assert any("run-step summary" in item for item in coverage_init.evidence)
    assert any("Coverage records remain in memory" in item for item in coverage_init.gaps)
    assert extraction_task.status == "PARTIAL"


def test_full_system_inventory_detects_phase_3_0d_task_planning_baseline() -> None:
    """Slice 4 task planning is visible without claiming persistence."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    extraction_task = inventory["extraction_task_run_integration"]
    live_execution = inventory["live_methodology_extraction_execution"]
    persistence = inventory["extraction_task_postgres_persistence"]

    assert extraction_task.status == "PARTIAL"
    assert any("METHODOLOGY_EXTRACTION_TASK_PLANNING" in item for item in extraction_task.evidence)
    assert any("methodology_extraction_tasks" in item for item in extraction_task.evidence)
    assert any("safe preflight summaries" in item for item in extraction_task.evidence)
    assert any("not persisted to Postgres" in item for item in extraction_task.gaps)
    assert any("execution remains in memory" in item for item in extraction_task.gaps)
    assert live_execution.status == "DEFERRED"
    assert persistence.status in {"DEFERRED", "PARTIAL"}


def test_full_system_inventory_detects_phase_3_0e_task_execution_baseline() -> None:
    """Slice 5 task execution is visible while downstream product layers stay deferred."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    execution = inventory["methodology_extraction_run_integration"]
    live_llm = inventory["methodology_extraction_live_llm_integration"]
    persistence = inventory["methodology_extraction_postgres_persistence"]

    assert execution.status == "PARTIAL"
    assert any("METHODOLOGY_EXTRACTION_TASK_EXECUTION" in item for item in execution.evidence)
    assert any("in-memory" in item.lower() for item in execution.evidence)
    assert any("schema-validated" in item.lower() for item in execution.evidence)
    assert any("Claims remain deferred" in item for item in execution.gaps)
    assert any("EvidenceItems remain deferred" in item for item in execution.gaps)
    assert any("Layer 1 Evidence Trust Court remains deferred" in item for item in execution.gaps)
    assert any("Layer 2 IC Decision Debate remains deferred" in item for item in execution.gaps)
    assert any("real data-room E2E remains deferred" in item for item in execution.gaps)
    assert live_llm.status == "DEFERRED"
    assert persistence.status in {"DEFERRED", "PARTIAL"}


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
        "DEFERRED",
        "PARTIAL",
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


def test_full_system_inventory_detects_phase_3_0b_document_preflight_baseline() -> None:
    """Slice 2 preflight is visible without claiming Slice 3+ run extraction wiring."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    preflight = inventory["document_preflight_run_integration"]

    assert preflight.status == "PARTIAL"
    assert any("DOCUMENT_PREFLIGHT" in item for item in preflight.evidence)
    assert any("preflight_corpus" in item for item in preflight.evidence)
    assert any("run-step summary" in item for item in preflight.evidence)
    assert any("extraction execution remains deferred" in item.lower() for item in preflight.gaps)


def test_full_system_inventory_detects_phase_3_0k_evidence_trust_court_boundary() -> None:
    """Slice 11 Evidence Trust Court must be visible without overstating downstream layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    court = inventory["methodology_evidence_trust_court_run_integration"]

    assert court.status == "PARTIAL"
    assert any("METHODOLOGY_EVIDENCE_TRUST_COURT" in item for item in court.evidence)
    assert any("Layer 1 Evidence Trust Court boundary exists" in item for item in court.evidence)
    assert not any(
        "Validated Evidence Package remains deferred to Slice 12" in gap for gap in court.gaps
    )
    assert any("Layer 2 IC debate" in gap for gap in court.gaps)


def test_full_system_inventory_detects_phase_3_0l_validated_evidence_package_boundary() -> None:
    """Slice 12 VEP must be visible without overstating downstream IC layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    vep = inventory["methodology_validated_evidence_package_run_integration"]

    assert vep.status == "PARTIAL"
    assert any("METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE" in item for item in vep.evidence)
    assert any(
        "Layer 1 Validated Evidence Package boundary exists" in item for item in vep.evidence
    )
    assert any("Layer 2 IC debate" in gap for gap in vep.gaps)
    assert any("enrichment/API checks" in gap for gap in vep.gaps)
    assert any("GO/CONDITIONAL/NO-GO" in gap for gap in vep.gaps)
    assert any("deliverables, API/UI/OpenAPI" in gap for gap in vep.gaps)
    assert any("durable Validated Evidence Package persistence" in gap for gap in vep.gaps)
    assert any("real E2E" in gap for gap in vep.gaps)


def test_full_system_inventory_detects_phase_3_0m_external_intelligence_plan_boundary() -> None:
    """Slice 13 must be a plan boundary, not executed external conflict checks."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    plan = inventory["methodology_external_intelligence_conflict_check_plan_run_integration"]

    assert plan.status == "PARTIAL"
    assert any(
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN" in item for item in plan.evidence
    )
    assert any("plan boundary" in item for item in plan.evidence)
    assert not any("external conflict checks executed" in item.lower() for item in plan.evidence)
    assert plan.metadata["live_calls_performed"] is False
    assert any("Layer 2 IC debate" in gap for gap in plan.gaps)
    assert any("GO/CONDITIONAL/NO-GO" in gap for gap in plan.gaps)
    assert any("deliverables, API/UI/OpenAPI" in gap for gap in plan.gaps)
    assert any("real provider calls" in gap for gap in plan.gaps)


def test_full_system_inventory_detects_slice_14_layer2_readiness_boundary() -> None:
    """Slice 14 must report readiness only, not execute downstream Layer 2."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    readiness = inventory["methodology_layer2_readiness_package_run_integration"]

    assert readiness.status == "PARTIAL"
    assert any("METHODOLOGY_LAYER2_READINESS_PACKAGE" in item for item in readiness.evidence)
    assert any("readiness/input-boundary" in item for item in readiness.evidence)
    assert any("construction_status" in item for item in readiness.evidence)
    assert any("readiness_status" in item for item in readiness.evidence)
    assert not any("IC debate executed" in item for item in readiness.evidence)
    assert readiness.metadata["layer2_execution_performed"] is False
    assert readiness.metadata["ready_expected_for_current_slice13_inputs"] is False
    assert any("IC debate remains deferred" in gap for gap in readiness.gaps)
    assert any("GO/CONDITIONAL/NO-GO" in gap for gap in readiness.gaps)
    assert any("INVEST/HOLD/DECLINE" in gap for gap in readiness.gaps)
    assert any("scorecard execution remains deferred" in gap for gap in readiness.gaps)
    assert any("deliverables, API/UI/OpenAPI" in gap for gap in readiness.gaps)
    assert any("live provider calls remain deferred" in gap for gap in readiness.gaps)


def test_full_system_inventory_detects_slice_15_company_identity_boundary() -> None:
    """Slice 15 must report identity input readiness only, not enrichment execution."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    identity = inventory["methodology_company_identity_package_run_integration"]

    assert identity.status == "PARTIAL"
    assert any("METHODOLOGY_COMPANY_IDENTITY_PACKAGE" in item for item in identity.evidence)
    assert any("company identity input boundary" in item for item in identity.evidence)
    assert not any("company_name" in item for item in identity.evidence)
    assert identity.metadata["enrichment_execution_performed"] is False
    assert identity.metadata["layer2_execution_performed"] is False
    assert any("enrichment execution remains deferred" in gap for gap in identity.gaps)
    assert any("connector fetch remains deferred" in gap for gap in identity.gaps)
    assert any("facts remain deferred" in gap for gap in identity.gaps)
    assert any("executed provider checks remain deferred" in gap for gap in identity.gaps)


def test_full_system_inventory_detects_slice_16_data_room_inventory_boundary() -> None:
    """Slice 16 must report inventory intake only, not OCR/media/API/Layer 2 execution."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    data_room = inventory["data_room_inventory_package_run_integration"]

    assert data_room.status == "PARTIAL"
    assert any("DATA_ROOM_INVENTORY_PACKAGE" in item for item in data_room.evidence)
    assert any("recursive" in item.lower() for item in data_room.evidence)
    assert any("inventory/intake boundary" in item for item in data_room.evidence)
    assert data_room.metadata["ocr_performed"] is False
    assert data_room.metadata["media_transcription_performed"] is False
    assert data_room.metadata["api_or_ui_changed"] is False
    assert data_room.metadata["layer2_execution_performed"] is False
    assert any("OCR remains deferred" in gap for gap in data_room.gaps)
    assert any("video/audio transcription remains deferred" in gap for gap in data_room.gaps)
    assert any("API/OpenAPI/UI remains deferred" in gap for gap in data_room.gaps)
    assert any("Layer 2 execution remains deferred" in gap for gap in data_room.gaps)


def test_full_system_inventory_detects_slice_17_local_data_room_harness_boundary() -> None:
    """Slice 17 must report local harness handoff only, not API/persistence/Layer 2."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    harness = inventory["data_room_full_harness_run_handoff"]

    assert harness.status == "PARTIAL"
    assert any("local data-room FULL harness" in item for item in harness.evidence)
    assert any("RunContext" in item for item in harness.evidence)
    assert harness.metadata["api_or_ui_changed"] is False
    assert harness.metadata["persistent_data_room_package"] is False
    assert harness.metadata["live_enrichment_expanded"] is False
    assert harness.metadata["layer2_execution_performed"] is False
    assert any("API/OpenAPI/UI remains deferred" in gap for gap in harness.gaps)
    assert any("persistence/S3 remains deferred" in gap for gap in harness.gaps)
    assert any("Layer 2 remains deferred" in gap for gap in harness.gaps)


def test_full_system_inventory_detects_slice_18_durable_handoff_boundary() -> None:
    """Slice 18 must report durable ingestion handoff only, not API/UI/media/Layer 2."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    handoff = inventory["data_room_ingestion_handoff_run_integration"]

    assert handoff.status == "PARTIAL"
    assert any("DATA_ROOM_INGESTION_HANDOFF" in item for item in handoff.evidence)
    assert any("IngestionService" in item for item in handoff.evidence)
    assert handoff.metadata["api_or_ui_changed"] is False
    assert handoff.metadata["s3_or_supabase_storage_added"] is False
    assert handoff.metadata["unsupported_files_create_documents"] is False
    assert handoff.metadata["ocr_performed"] is False
    assert handoff.metadata["media_transcription_performed"] is False
    assert handoff.metadata["layer2_execution_performed"] is False
    assert any("API/OpenAPI/UI remains deferred" in gap for gap in handoff.gaps)
    assert any("S3/Supabase storage remains deferred" in gap for gap in handoff.gaps)
    assert any("unsupported/deferred files remain summaries only" in gap for gap in handoff.gaps)
    assert any("Layer 2 remains deferred" in gap for gap in handoff.gaps)


def test_full_system_report_keeps_slice_12_deferred_boundaries_explicit() -> None:
    """Rendered audit wording must keep Slice 12 separate from downstream IC layers."""
    report = render_report(collect_wiring_inventory(REPO_ROOT))

    assert "in-memory run-scoped Layer 1 Evidence Trust Court boundary exists" in report
    assert "data-room inventory package boundary exists" in report
    assert "local data-room FULL harness boundary exists" in report
    assert "durable data-room ingestion handoff boundary exists" in report
    assert "in-memory run-scoped Layer 1 Validated Evidence Package boundary exists" in report
    assert "external intelligence conflict-check plan boundary exists" in report
    assert "company identity package boundary exists" in report
    assert "Layer 2 readiness package boundary exists" in report
    assert "Validated Evidence Package remains deferred to Slice 12" not in report
    assert "external conflict checks executed" not in report.lower()
    assert "enrichment execution performed" not in report.lower()
    assert "media transcription performed" not in report.lower()
    assert "IC debate executed" not in report
    assert "enrichment/API check execution remains deferred" in report
    assert "Layer 2 IC debate" in report
    assert "GO/CONDITIONAL/NO-GO" in report
    assert "deliverables, API/UI/OpenAPI, and real E2E remain deferred" in report
    assert "Validated Evidence Package produces IC recommendations" not in report


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
    assert inventory["extraction_task_run_integration"].status == "PARTIAL"
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
        "DEFERRED",
        "PARTIAL",
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
        "PARTIAL",
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


def test_full_system_inventory_detects_phase_3_0f_claim_materialization_boundary() -> None:
    """Slice 6 materializes in-memory claims without overstating persistence."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    run_integration = inventory["methodology_claim_materialization_run_integration"]
    postgres_schema = inventory["methodology_claim_materialization_postgres_schema"]
    sanad_integration = inventory["methodology_claim_materialization_sanad_integration"]
    coverage_integration = inventory["methodology_claim_materialization_coverage_integration"]

    assert run_integration.status == "PARTIAL"
    assert any("METHODOLOGY_CLAIM_MATERIALIZATION" in item for item in run_integration.evidence)
    assert any("MethodologyExtractionOutput" in item for item in run_integration.evidence)
    durable_deferral = (
        "in-memory governed claim boundary exists; durable Claim Registry persistence "
        "remains deferred"
    )
    assert any(
        durable_deferral in item
        for item in [run_integration.summary, *run_integration.evidence, *run_integration.gaps]
    )
    assert any("EvidenceItems remain deferred" in item for item in run_integration.gaps)
    assert any("Sanads remain deferred" in item for item in run_integration.gaps)
    assert any("Truth Dashboard remains deferred" in item for item in run_integration.gaps)
    assert any(
        "Layer 1 Evidence Trust Court remains deferred" in item for item in run_integration.gaps
    )
    assert any(
        "Layer 2 IC Decision Debate remains deferred" in item for item in run_integration.gaps
    )
    assert postgres_schema.status in {"DEFERRED", "PARTIAL"}
    assert sanad_integration.status == "DEFERRED"
    assert coverage_integration.status == "DEFERRED"


def test_full_system_inventory_detects_phase_3_0g_evidence_item_boundary() -> None:
    """Slice 7 creates in-memory EvidenceItems without overstating persistence."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    evidence_boundary = inventory["methodology_evidence_item_materialization_run_integration"]

    assert evidence_boundary.status == "PARTIAL"
    assert any(
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION" in item for item in evidence_boundary.evidence
    )
    assert any(
        "in-memory governed EvidenceItem/source-provenance boundary exists" in item
        for item in [
            evidence_boundary.summary,
            *evidence_boundary.evidence,
            *evidence_boundary.gaps,
        ]
    )
    assert any(
        "Durable Postgres evidence persistence remains deferred" in item
        for item in evidence_boundary.gaps
    )
    assert any(
        "Sanad creation/linking/grading remains deferred" in item for item in evidence_boundary.gaps
    )
    assert any("Truth Dashboard remains deferred" in item for item in evidence_boundary.gaps)
    assert any("CALC remains deferred" in item for item in evidence_boundary.gaps)
    assert any("enrichment/API checks remain deferred" in item for item in evidence_boundary.gaps)
    assert any(
        "Layer 1 Evidence Trust Court remains deferred" in item for item in evidence_boundary.gaps
    )
    assert any("Layer 2 IC Debate remains deferred" in item for item in evidence_boundary.gaps)
    assert any("deliverables remain deferred" in item for item in evidence_boundary.gaps)
    assert any("real data-room E2E remains deferred" in item for item in evidence_boundary.gaps)


def test_full_system_inventory_detects_phase_3_0h_sanad_boundary() -> None:
    """Slice 8 creates in-memory Sanads without overstating downstream layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    sanad_boundary = inventory["methodology_sanad_creation_linking_grading_run_integration"]

    assert sanad_boundary.status == "PARTIAL"
    assert any(
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING" in item for item in sanad_boundary.evidence
    )
    assert any(
        "in-memory governed Sanad creation/linking/grading boundary exists" in item
        for item in [sanad_boundary.summary, *sanad_boundary.evidence, *sanad_boundary.gaps]
    )
    assert any(
        "Durable Postgres Sanad/Defect/Claim persistence remains deferred" in item
        for item in sanad_boundary.gaps
    )
    assert any("Claim-to-Sanad links are run-scoped only" in item for item in sanad_boundary.gaps)
    assert any("Truth Dashboard remains deferred" in item for item in sanad_boundary.gaps)
    assert any("CALC remains deferred" in item for item in sanad_boundary.gaps)
    assert any("enrichment/API checks remain deferred" in item for item in sanad_boundary.gaps)
    assert any(
        "Layer 1 Evidence Trust Court and Validated Evidence Package run later in FULL mode" in item
        for item in sanad_boundary.gaps
    )
    assert any("Layer 2 IC Debate remains deferred" in item for item in sanad_boundary.gaps)
    assert any(
        "GO/CONDITIONAL/NO-GO package remains deferred" in item for item in sanad_boundary.gaps
    )
    assert any("deliverables remain deferred" in item for item in sanad_boundary.gaps)
    assert any("real data-room E2E remains deferred" in item for item in sanad_boundary.gaps)


def test_full_system_inventory_detects_phase_3_0i_deterministic_calc_boundary() -> None:
    """Slice 9 creates run-scoped calcs without overstating downstream layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    calc_boundary = inventory["methodology_deterministic_calculation_run_integration"]

    assert calc_boundary.status == "PARTIAL"
    assert any("METHODOLOGY_DETERMINISTIC_CALCULATION" in item for item in calc_boundary.evidence)
    assert any(
        "in-memory run-scoped deterministic calculation boundary exists" in item
        for item in [calc_boundary.summary, *calc_boundary.evidence, *calc_boundary.gaps]
    )
    assert any(
        "durable Calc/CalcSanad persistence over durable Claim/Sanad inputs remains deferred"
        in item
        for item in calc_boundary.gaps
    )
    assert any(
        "calculations do not promote claims, Sanads, or deals to IC readiness" in item
        for item in calc_boundary.gaps
    )
    assert any("Truth Dashboard remains deferred" in item for item in calc_boundary.gaps)
    assert any("enrichment/API checks remain deferred" in item for item in calc_boundary.gaps)
    assert any(
        "Layer 1 Evidence Trust Court and Validated Evidence Package run later in FULL mode" in item
        for item in calc_boundary.gaps
    )
    assert any("Layer 2 IC Debate remains deferred" in item for item in calc_boundary.gaps)
    assert any(
        "GO/CONDITIONAL/NO-GO package remains deferred" in item for item in calc_boundary.gaps
    )
    assert any("deliverables remain deferred" in item for item in calc_boundary.gaps)
    assert any("real data-room E2E remains deferred" in item for item in calc_boundary.gaps)


def test_full_system_inventory_detects_phase_3_0j_truth_dashboard_boundary() -> None:
    """Slice 10 creates a run-scoped dashboard without overstating downstream layers."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    truth_boundary = inventory["methodology_truth_dashboard_run_integration"]

    assert truth_boundary.status == "PARTIAL"
    assert any("METHODOLOGY_TRUTH_DASHBOARD" in item for item in truth_boundary.evidence)
    assert any(
        "in-memory run-scoped Truth Dashboard boundary exists" in item
        for item in [truth_boundary.summary, *truth_boundary.evidence, *truth_boundary.gaps]
    )
    assert any(
        "durable Truth Dashboard persistence remains deferred" in item
        for item in truth_boundary.gaps
    )
    assert any("API/UI/OpenAPI exposure remains deferred" in item for item in truth_boundary.gaps)
    assert any("deliverables integration remains deferred" in item for item in truth_boundary.gaps)
    assert not any(
        "Layer 1 Evidence Trust Court remains deferred" in item for item in truth_boundary.gaps
    )
    assert not any(
        "Validated Evidence Package remains deferred" in item for item in truth_boundary.gaps
    )
    assert any(
        "Validated Evidence Package is a downstream Layer 1 run-scoped package step" in item
        for item in truth_boundary.gaps
    )
    assert any("Layer 2 IC Debate remains deferred" in item for item in truth_boundary.gaps)
    assert any(
        "GO/CONDITIONAL/NO-GO package remains deferred" in item for item in truth_boundary.gaps
    )
    assert any("real data-room E2E remains deferred" in item for item in truth_boundary.gaps)


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
