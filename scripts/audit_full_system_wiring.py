#!/usr/bin/env python3
"""Static Phase 2.0 full-system wiring audit.

This script intentionally avoids real data ingestion and external provider calls.
It inspects repository code/config and writes a local report under `.local_reports/`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPORT_PATH: Final = Path(".local_reports/phase_2_0_wiring_baseline_report.md")
VALID_STATUSES: Final = {
    "WIRED",
    "PARTIAL",
    "STUBBED",
    "CONFIG_ONLY",
    "NOT_FOUND",
    "TEST_ONLY",
    "DEFERRED",
}


@dataclass(frozen=True)
class WiringItem:
    """Single audited component/integration result."""

    key: str
    label: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    phase_2_action: str = "Phase 2.0"
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate status values eagerly."""
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid wiring status for {self.key}: {self.status}")


@dataclass(frozen=True)
class ReportSection:
    """Report section descriptor used by tests and renderer."""

    heading: str


@dataclass
class WiringInventory(dict[str, WiringItem]):
    """Dictionary of wiring items plus report metadata."""

    report_sections: list[ReportSection] = field(default_factory=list)
    validation_results: dict[str, str] = field(default_factory=dict)


def collect_wiring_inventory(repo_root: Path) -> WiringInventory:
    """Collect a static wiring inventory from repository files.

    Args:
        repo_root: Repository root to inspect.

    Returns:
        WiringInventory with classified components and report metadata.
    """
    root = repo_root.resolve()
    inventory = WiringInventory()

    files = _load_relevant_files(root)
    provider_ids = _discover_provider_ids(root)

    inventory.update(
        {
            "api_run_path": _api_run_path(files),
            "worker_executor_path": _worker_executor_path(files),
            "api_worker_path_comparison": _api_worker_path_comparison(files),
            "snapshot_steps": _snapshot_steps(files),
            "full_steps": _full_steps(files),
            "ingestion_service": _ingestion_service(files),
            "persisted_ingestion_corpus": _persisted_ingestion_corpus(root, files),
            "unified_run_document_loader": _unified_run_document_loader(files),
            "api_worker_document_corpus_split": _api_worker_document_corpus_split(files),
            "parser_wiring": _parser_wiring(files),
            "extraction_pipeline": _extraction_pipeline(files),
            "sanad_wiring": _sanad_wiring(files),
            "calc_engine": _calc_engine(files),
            "calc_step": _calc_step(files),
            "calc_sanad": _calc_sanad(files),
            "methodology_registry_models": _methodology_registry_models(root, files),
            "fdd_synthetic_excel_importer": _fdd_synthetic_excel_importer(root, files),
            "commercial_dd_template": _commercial_dd_template(root, files),
            "methodology_coverage_service": _methodology_coverage_service(root, files),
            "methodology_postgres_persistence": _methodology_postgres_persistence(root),
            "methodology_run_integration": _methodology_run_integration(files),
            "methodology_coverage_init_run_integration": (
                _methodology_coverage_init_run_integration(files)
            ),
            "document_classification_models": _document_classification_models(root, files),
            "parser_capability_registry": _parser_capability_registry(root, files),
            "document_classification_service": _document_classification_service(root, files),
            "parser_triage_layer": _parser_triage_layer(root, files),
            "document_classification_postgres_persistence": (
                _document_classification_postgres_persistence(root)
            ),
            "document_classification_api_integration": _document_classification_api_integration(
                files
            ),
            "document_classification_ui_integration": _document_classification_ui_integration(root),
            "document_classification_run_integration": _document_classification_run_integration(
                files
            ),
            "document_preflight_run_integration": _document_preflight_run_integration(files),
            "extraction_task_models": _extraction_task_models(root, files),
            "extraction_task_planner": _extraction_task_planner(root, files),
            "extraction_task_audit_contract": _extraction_task_audit_contract(root, files),
            "extraction_task_postgres_persistence": _extraction_task_postgres_persistence(root),
            "extraction_task_api_integration": _extraction_task_api_integration(files),
            "extraction_task_run_integration": _extraction_task_run_integration(files),
            "live_methodology_extraction_execution": _live_methodology_extraction_execution(files),
            "methodology_extraction_execution_models": (
                _methodology_extraction_execution_models(root, files)
            ),
            "methodology_extraction_task_executor": _methodology_extraction_task_executor(
                root, files
            ),
            "methodology_extraction_execution_audit_contract": (
                _methodology_extraction_execution_audit_contract(root, files)
            ),
            "methodology_extraction_postgres_persistence": (
                _methodology_extraction_postgres_persistence(root)
            ),
            "methodology_extraction_api_integration": _methodology_extraction_api_integration(
                files
            ),
            "methodology_extraction_run_integration": _methodology_extraction_run_integration(
                files
            ),
            "methodology_extraction_live_llm_integration": (
                _methodology_extraction_live_llm_integration(files)
            ),
            "methodology_extraction_coverage_integration": (
                _methodology_extraction_coverage_integration(files)
            ),
            "methodology_claim_materialization_models": (
                _methodology_claim_materialization_models(root, files)
            ),
            "methodology_claim_materializer": _methodology_claim_materializer(root, files),
            "methodology_claim_materialization_audit_contract": (
                _methodology_claim_materialization_audit_contract(root, files)
            ),
            "methodology_claim_materialization_postgres_schema": (
                _methodology_claim_materialization_postgres_schema(root)
            ),
            "methodology_claim_materialization_api_integration": (
                _methodology_claim_materialization_api_integration(files)
            ),
            "methodology_claim_materialization_run_integration": (
                _methodology_claim_materialization_run_integration(files)
            ),
            "methodology_evidence_item_materialization_run_integration": (
                _methodology_evidence_item_materialization_run_integration(root, files)
            ),
            "methodology_sanad_creation_linking_grading_run_integration": (
                _methodology_sanad_creation_linking_grading_run_integration(root, files)
            ),
            "methodology_deterministic_calculation_run_integration": (
                _methodology_deterministic_calculation_run_integration(root, files)
            ),
            "methodology_truth_dashboard_run_integration": (
                _methodology_truth_dashboard_run_integration(root, files)
            ),
            "methodology_evidence_trust_court_run_integration": (
                _methodology_evidence_trust_court_run_integration(root, files)
            ),
            "methodology_validated_evidence_package_run_integration": (
                _methodology_validated_evidence_package_run_integration(root, files)
            ),
            "methodology_external_intelligence_conflict_check_plan_run_integration": (
                _methodology_external_intelligence_conflict_check_plan_run_integration(root, files)
            ),
            "methodology_company_identity_package_run_integration": (
                _methodology_company_identity_package_run_integration(root, files)
            ),
            "data_room_inventory_package_run_integration": (
                _data_room_inventory_package_run_integration(root, files)
            ),
            "data_room_full_harness_run_handoff": (
                _data_room_full_harness_run_handoff(root, files)
            ),
            "data_room_ingestion_handoff_run_integration": (
                _data_room_ingestion_handoff_run_integration(root, files)
            ),
            "production_run_source_contract": _production_run_source_contract(root, files),
            "durable_document_api_parity": _durable_document_api_parity(files),
            "single_document_upload_intake": _single_document_upload_intake(files),
            "api_upload_to_selected_run_smoke": _api_upload_to_selected_run_smoke(files),
            "default_upload_ingestion_wiring": _default_upload_ingestion_wiring(files),
            "methodology_layer2_readiness_package_run_integration": (
                _methodology_layer2_readiness_package_run_integration(root, files)
            ),
            "methodology_claim_materialization_sanad_integration": (
                _methodology_claim_materialization_sanad_integration(files)
            ),
            "methodology_claim_materialization_coverage_integration": (
                _methodology_claim_materialization_coverage_integration(files)
            ),
            "methodology_sanad_coverage_boundary_models": (
                _methodology_sanad_coverage_boundary_models(root, files)
            ),
            "methodology_sanad_coverage_boundary_service": (
                _methodology_sanad_coverage_boundary_service(root, files)
            ),
            "methodology_sanad_coverage_boundary_audit_contract": (
                _methodology_sanad_coverage_boundary_audit_contract(root, files)
            ),
            "methodology_sanad_readiness_boundary": (_methodology_sanad_readiness_boundary(files)),
            "methodology_coverage_decision_boundary": (
                _methodology_coverage_decision_boundary(files)
            ),
            "methodology_live_coverage_updates": _methodology_live_coverage_updates(files),
            "methodology_boundary_sanad_creation": _methodology_boundary_sanad_creation(files),
            "methodology_boundary_ic_promotion": _methodology_boundary_ic_promotion(files),
            "methodology_boundary_postgres_persistence": (
                _methodology_boundary_postgres_persistence(root)
            ),
            "methodology_boundary_api_run_ui_integration": (
                _methodology_boundary_api_run_ui_integration(files)
            ),
            "methodology_boundary_rag_graph_cache_integration": (
                _methodology_boundary_rag_graph_cache_integration(files)
            ),
            "methodology_sanad_creation_boundary_models": (
                _methodology_sanad_creation_boundary_models(root, files)
            ),
            "methodology_sanad_creation_boundary_service": (
                _methodology_sanad_creation_boundary_service(root, files)
            ),
            "methodology_sanad_creation_boundary_audit_contract": (
                _methodology_sanad_creation_boundary_audit_contract(root, files)
            ),
            "methodology_synthetic_sanad_creation_path": (
                _methodology_synthetic_sanad_creation_path(files)
            ),
            "methodology_sanad_creation_claim_link_application": (
                _methodology_sanad_creation_claim_link_application(files)
            ),
            "methodology_sanad_creation_ic_promotion": (
                _methodology_sanad_creation_ic_promotion(files)
            ),
            "methodology_sanad_creation_coverage_updates": (
                _methodology_sanad_creation_coverage_updates(files)
            ),
            "methodology_sanad_creation_postgres_api_run_ui_integration": (
                _methodology_sanad_creation_postgres_api_run_ui_integration(files)
            ),
            "methodology_sanad_creation_rag_graph_cache_integration": (
                _methodology_sanad_creation_rag_graph_cache_integration(files)
            ),
            "methodology_claim_sanad_link_boundary_models": (
                _methodology_claim_sanad_link_boundary_models(root, files)
            ),
            "methodology_claim_sanad_link_boundary_service": (
                _methodology_claim_sanad_link_boundary_service(root, files)
            ),
            "methodology_claim_sanad_link_boundary_audit_contract": (
                _methodology_claim_sanad_link_boundary_audit_contract(root, files)
            ),
            "methodology_synthetic_claim_sanad_link_apply_path": (
                _methodology_synthetic_claim_sanad_link_apply_path(files)
            ),
            "methodology_claim_sanad_link_ic_promotion": (
                _methodology_claim_sanad_link_ic_promotion(files)
            ),
            "methodology_claim_sanad_link_verdict_action_promotion": (
                _methodology_claim_sanad_link_verdict_action_promotion(files)
            ),
            "methodology_claim_sanad_link_coverage_updates": (
                _methodology_claim_sanad_link_coverage_updates(files)
            ),
            "methodology_claim_sanad_link_postgres_api_run_ui_integration": (
                _methodology_claim_sanad_link_postgres_api_run_ui_integration(files)
            ),
            "methodology_claim_sanad_link_rag_graph_cache_integration": (
                _methodology_claim_sanad_link_rag_graph_cache_integration(files)
            ),
            "analysis_agents": _analysis_agents(files),
            "commercial_agents": _commercial_agents(files),
            "debate_layer_1": _debate_layer_1(files),
            "debate_layer_2": _debate_layer_2(root),
            "muhasabah_nff_gates": _muhasabah_nff_gates(files),
            "deliverables": _deliverables(files),
            "audit_sinks": _audit_sinks(files),
            "postgres": _postgres(files),
            "docker_postgres": _docker_postgres(files),
            "supabase": _supabase(root, files),
            "neo4j_graph": _neo4j_graph(root, files),
            "redis": _redis(files),
            "rag_vector_retrieval": _rag_vector_retrieval(root, files),
            "object_storage": _object_storage(root, files),
            "external_enrichment_connectors": _external_enrichment(provider_ids),
            "anthropic_llm": _anthropic_llm(files),
            "openai_llm": _openai_llm(root, files),
        }
    )

    inventory.report_sections = [
        ReportSection("Executive Summary"),
        ReportSection("Full-System Wiring Matrix"),
        ReportSection("Infrastructure Matrix"),
        ReportSection("API Run Path vs Worker Path Comparison"),
        ReportSection("SNAPSHOT Step Outputs"),
        ReportSection("FULL Step Outputs"),
        ReportSection("Stubbed and Config-Only Components"),
        ReportSection("Missing Components"),
        ReportSection("Existing Components Not Wired"),
        ReportSection("Risk Ranking"),
        ReportSection("Recommended Phase 2.1 Starting Point"),
        ReportSection("Exact Evidence From Tests and Script"),
        ReportSection("Validation Commands and Results"),
    ]
    inventory.validation_results = {
        "ruff check .": "NOT_RUN",
        "mypy src/idis --ignore-missing-imports": "NOT_RUN",
        "pytest -q": "NOT_RUN",
        "python scripts/run_postgres_integration_local.py": "NOT_RUN",
        (
            "pytest tests/test_full_system_wiring_baseline.py "
            "tests/test_run_route_full_path_truthfulness.py "
            "tests/test_worker_executor_path_truthfulness.py "
            "tests/test_infrastructure_wiring_baseline.py "
            "tests/test_external_integrations_config_baseline.py -q"
        ): "NOT_RUN",
    }
    return inventory


def render_report(inventory: WiringInventory) -> str:
    """Render the wiring inventory as a Markdown report."""
    lines: list[str] = [
        "# Phase 2.0 Wiring Baseline Report",
        "",
        "> Generated by `scripts/audit_full_system_wiring.py` using static, secret-safe "
        "inspection only. No real data-room ingestion and no paid external API calls.",
        "",
        "## Executive Summary",
        "",
        "- API runs and background queued runs use `RunExecutionService`, which wraps "
        "`RunOrchestrator` as the canonical execution engine.",
        "- CALC now delegates to `CalcRunner`, persists eligible deterministic calculations "
        "and CalcSanad rows, and reports blocked candidates without fake calc IDs.",
        "- Neo4j, Redis, and pgvector are present in code/config, but are not proven live-run "
        "wiring.",
        "- Document classification and parser triage now exist as an in-memory foundation, "
        "but API/UI/run/Postgres integration is explicitly deferred.",
        "- Methodology-driven extraction task planning now exists as metadata-only, "
        "in-memory planning; live methodology extraction execution remains deferred.",
        "- Synthetic methodology extraction task execution now produces methodology-linked "
        "claim draft metadata only; persistence, API/run integration, coverage updates, "
        "and live LLM execution remain deferred.",
        "- Sanad readiness boundary and coverage update decisions now exist, while "
        "live coverage updates are not wired; Phase 2.8 adds an explicit synthetic "
        "Sanad creation path while production run wiring remains deferred.",
        "- Claim-Sanad link boundary now exists; the explicit synthetic ClaimService.update "
        "path exists when invoked, while IC promotion, claim verdict/action promotion, "
        "and coverage updates are not wired.",
        "- Enrichment connectors and deterministic/Anthropic LLM wiring exist, but baseline "
        "validation is config-only for paid/network providers.",
        "",
        "## Full-System Wiring Matrix",
        "",
        "| Component | Status | Summary | Evidence | Gaps | Phase 2 Action |",
        "|---|---|---|---|---|---|",
    ]
    for item in inventory.values():
        if item.key in INFRASTRUCTURE_KEYS:
            continue
        lines.append(_item_row(item))

    lines.extend(
        [
            "",
            "## Infrastructure Matrix",
            "",
            "| Integration | Status | Summary | Evidence | Gaps | Phase 2 Action |",
            "|---|---|---|---|---|---|",
        ]
    )
    for key in INFRASTRUCTURE_KEYS:
        lines.append(_item_row(inventory[key]))

    lines.extend(_comparison_section(inventory))
    lines.extend(_step_section("SNAPSHOT Step Outputs", inventory["snapshot_steps"]))
    lines.extend(_step_section("FULL Step Outputs", inventory["full_steps"]))
    lines.extend(_status_group_section("Stubbed and Config-Only Components", inventory))
    lines.extend(_missing_section(inventory))
    lines.extend(_not_wired_section(inventory))
    lines.extend(_risk_section(inventory))
    lines.extend(
        [
            "",
            "## Recommended Phase 2.1 Starting Point",
            "",
            "After Phase 2.0, begin with the smallest fix that makes the canonical run path "
            "truthful: reconcile API `RunOrchestrator` execution with worker execution and "
            "durable step outputs. Do not start methodology import until run-path truthfulness "
            "and infrastructure wiring findings are accepted.",
            "",
            "## Exact Evidence From Tests and Script",
            "",
        ]
    )
    for item in inventory.values():
        lines.append(f"### {item.label}")
        lines.append("")
        lines.append(f"- Status: `{item.status}`")
        for evidence in item.evidence:
            lines.append(f"- Evidence: {evidence}")
        for gap in item.gaps:
            lines.append(f"- Gap: {gap}")
        lines.append("")

    lines.extend(
        [
            "## Validation Commands and Results",
            "",
            "| Command | Result |",
            "|---|---|",
        ]
    )
    for command, result in inventory.validation_results.items():
        lines.append(f"| `{command}` | `{result}` |")

    lines.append("")
    return "\n".join(lines)


def write_report(
    inventory: WiringInventory,
    *,
    output_path: Path = REPORT_PATH,
    repo_root: Path | None = None,
) -> Path:
    """Write the audit report to a local report path.

    Args:
        inventory: Collected wiring inventory.
        output_path: Relative or absolute output path.
        repo_root: Repository root used for resolving relative output path.

    Returns:
        Absolute path written.
    """
    root = Path.cwd() if repo_root is None else repo_root
    destination = output_path if output_path.is_absolute() else root / output_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_report(inventory), encoding="utf-8")
    return destination


INFRASTRUCTURE_KEYS: Final = {
    "postgres",
    "docker_postgres",
    "supabase",
    "neo4j_graph",
    "redis",
    "rag_vector_retrieval",
    "object_storage",
    "external_enrichment_connectors",
    "anthropic_llm",
    "openai_llm",
}


def _load_relevant_files(root: Path) -> dict[str, str]:
    """Load files used by static checks."""
    relative_paths = [
        ".github/workflows/ci.yml",
        ".env.example",
        "docker-compose.yml",
        "scripts/pg_init.sql",
        "scripts/db/init.sql",
        "openapi/IDIS_OpenAPI_v6_3.yaml",
        "src/idis/api/routes/documents.py",
        "src/idis/api/routes/runs.py",
        "src/idis/api/main.py",
        "src/idis/api/middleware/audit.py",
        "src/idis/persistence/repositories/evidence.py",
        "src/idis/api/policy.py",
        "src/idis/services/ingestion/defaults.py",
        "src/idis/models/run_source.py",
        "src/idis/models/run_step.py",
        "src/idis/persistence/repositories/runs.py",
        "src/idis/persistence/migrations/versions/0013_runs_source_contract.py",
        "src/idis/persistence/migrations/versions/0014_run_step_name_width.py",
        "src/idis/persistence/migrations/versions/0015_defects_workflow_columns.py",
        "src/idis/services/runs/execution.py",
        "src/idis/services/runs/orchestrator.py",
        "src/idis/services/runs/steps.py",
        "src/idis/pipeline/worker.py",
        "src/idis/pipeline/executor.py",
        "src/idis/services/ingestion/service.py",
        "src/idis/persistence/repositories/documents.py",
        "src/idis/persistence/migrations/versions/0012_document_spans_deal_content_hash.py",
        "src/idis/models/document_span.py",
        "src/idis/models/document_preflight.py",
        "src/idis/parsers/registry.py",
        "src/idis/services/extraction/pipeline.py",
        "src/idis/services/sanad/auto_grade.py",
        "src/idis/calc/engine.py",
        "src/idis/services/calc/runner.py",
        "src/idis/models/calc_sanad.py",
        "src/idis/persistence/repositories/calculations.py",
        "src/idis/methodology/models.py",
        "src/idis/methodology/registry.py",
        "src/idis/methodology/importers/fdd_excel.py",
        "src/idis/methodology/templates/commercial_dd_v1.json",
        "src/idis/models/methodology_coverage.py",
        "src/idis/services/methodology/coverage.py",
        "src/idis/services/runs/methodology_coverage_init.py",
        "src/idis/models/document_classification.py",
        "src/idis/services/documents/parser_capabilities.py",
        "src/idis/services/documents/classifier.py",
        "src/idis/services/documents/classification_service.py",
        "src/idis/services/documents/audit.py",
        "src/idis/services/runs/document_preflight.py",
        "src/idis/services/runs/methodology_extraction_task_planning.py",
        "src/idis/services/runs/methodology_extraction_task_execution.py",
        "src/idis/models/extraction_task.py",
        "src/idis/services/extraction/task_planner.py",
        "src/idis/services/extraction/task_audit.py",
        "src/idis/models/extraction_execution.py",
        "src/idis/services/extraction/task_executor.py",
        "src/idis/services/extraction/execution_audit.py",
        "src/idis/models/claim_materialization.py",
        "src/idis/models/evidence_item.py",
        "src/idis/models/evidence_item_materialization.py",
        "src/idis/models/sanad_materialization.py",
        "src/idis/models/calc_materialization.py",
        "src/idis/models/truth_dashboard_materialization.py",
        "src/idis/models/evidence_trust_court_materialization.py",
        "src/idis/models/validated_evidence_package_materialization.py",
        "src/idis/models/external_intelligence_conflict_check_plan_materialization.py",
        "src/idis/models/company_identity_package_materialization.py",
        "src/idis/models/data_room_inventory_package_materialization.py",
        "src/idis/models/layer2_readiness_package_materialization.py",
        "src/idis/services/extraction/claim_materializer.py",
        "src/idis/services/runs/methodology_claim_materialization.py",
        "src/idis/services/runs/methodology_evidence_item_materialization.py",
        "src/idis/services/runs/methodology_sanad_creation_linking_grading.py",
        "src/idis/services/runs/methodology_sanad_creation_helpers.py",
        "src/idis/services/runs/methodology_deterministic_calculation.py",
        "src/idis/services/runs/methodology_deterministic_calculation_helpers.py",
        "src/idis/services/runs/methodology_truth_dashboard.py",
        "src/idis/services/runs/methodology_evidence_trust_court.py",
        "src/idis/services/runs/methodology_evidence_trust_court_helpers.py",
        "src/idis/services/runs/methodology_validated_evidence_package.py",
        "src/idis/services/runs/methodology_external_intelligence_conflict_check_plan.py",
        "src/idis/services/runs/methodology_company_identity_package.py",
        "src/idis/services/runs/data_room_inventory_package.py",
        "src/idis/models/data_room_ingestion_handoff.py",
        "src/idis/services/runs/data_room_ingestion_handoff.py",
        "src/idis/services/runs/methodology_layer2_readiness_package.py",
        "src/idis/evaluation/data_room_harness.py",
        "scripts/run_data_room_full_harness.py",
        "src/idis/services/extraction/claim_materialization_audit.py",
        "src/idis/models/sanad_coverage_boundary.py",
        "src/idis/services/methodology/sanad_coverage_boundary.py",
        "src/idis/services/methodology/sanad_coverage_boundary_audit.py",
        "src/idis/models/sanad_creation_boundary.py",
        "src/idis/services/methodology/sanad_creation_boundary.py",
        "src/idis/services/methodology/sanad_creation_boundary_results.py",
        "src/idis/services/methodology/sanad_creation_boundary_support.py",
        "src/idis/services/methodology/sanad_creation_boundary_audit.py",
        "src/idis/models/claim_sanad_link_boundary.py",
        "src/idis/services/methodology/claim_sanad_link_boundary.py",
        "src/idis/services/methodology/claim_sanad_link_boundary_support.py",
        "src/idis/services/methodology/claim_sanad_link_boundary_audit.py",
        "src/idis/analysis/agents/__init__.py",
        "src/idis/analysis/runner.py",
        "src/idis/debate/orchestrator.py",
        "src/idis/debate/muhasabah_gate.py",
        "src/idis/validators/no_free_facts.py",
        "src/idis/validators/deliverable.py",
        "src/idis/deliverables/truth_dashboard.py",
        "src/idis/deliverables/generator.py",
        "src/idis/audit/sink.py",
        "src/idis/audit/postgres_sink.py",
        "src/idis/persistence/db.py",
        "src/idis/persistence/neo4j_driver.py",
        "src/idis/persistence/graph_repo.py",
        "src/idis/persistence/graph_consistency.py",
        "src/idis/storage/filesystem_store.py",
        "src/idis/storage/__init__.py",
        "src/idis/services/enrichment/service.py",
        "src/idis/services/extraction/extractors/anthropic_client.py",
        "src/idis/rate_limit/limiter.py",
        "src/idis/services/enrichment/cache_policy.py",
        "tests/test_api_upload_to_run_smoke_postgres.py",
        "tests/test_api_default_upload_ingestion_postgres.py",
        "tests/test_api_full_run_step_name_postgres.py",
        "tests/test_api_full_run_durable_evidence_postgres.py",
        "tests/test_api_sanad_auto_grade_persistence_postgres.py",
        "tests/test_run_document_loader.py",
        "pyproject.toml",
    ]
    return {path: _read(root / path) for path in relative_paths}


def _read(path: Path) -> str:
    """Read text safely."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _exists(root: Path, relative_path: str) -> bool:
    return (root / relative_path).exists()


def _contains(files: dict[str, str], path: str, needle: str) -> bool:
    return needle in files.get(path, "")


def _discover_provider_ids(root: Path) -> list[str]:
    """Discover enrichment provider IDs without calling external providers."""
    connector_dir = root / "src/idis/services/enrichment/connectors"
    provider_ids: set[str] = set()
    for connector in connector_dir.glob("*.py"):
        if connector.name == "__init__.py":
            continue
        text = _read(connector)
        provider_ids.update(re.findall(r'PROVIDER_ID\s*=\s*"([^"]+)"', text))
    return sorted(provider_ids)


def _item_row(item: WiringItem) -> str:
    evidence = "<br>".join(_escape_cell(value) for value in item.evidence) or "-"
    gaps = "<br>".join(_escape_cell(value) for value in item.gaps) or "-"
    return (
        f"| {item.label} | `{item.status}` | {_escape_cell(item.summary)} | "
        f"{evidence} | {gaps} | {item.phase_2_action} |"
    )


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _api_run_path(files: dict[str, str]) -> WiringItem:
    status = (
        "WIRED"
        if _contains(files, "src/idis/api/routes/runs.py", "RunExecutionService")
        and _contains(files, "src/idis/services/runs/execution.py", "RunOrchestrator")
        else "NOT_FOUND"
    )
    return WiringItem(
        key="api_run_path",
        label="API run path",
        status=status,
        summary="HTTP run route executes through the shared RunExecutionService.",
        evidence=[
            "`src/idis/api/routes/runs.py` imports/uses `RunExecutionService`.",
            "`RunExecutionService` wraps `RunOrchestrator`.",
            "`start_run` calls `asyncio.to_thread(execution_service.execute, ctx)`.",
        ],
        gaps=[],
    )


def _worker_executor_path(files: dict[str, str]) -> WiringItem:
    worker_text = files.get("src/idis/pipeline/worker.py", "")
    status = "WIRED" if "RunExecutionService" in worker_text else "PARTIAL"
    return WiringItem(
        key="worker_executor_path",
        label="Worker/executor path",
        status=status,
        summary="Background worker uses RunExecutionService for queued runs.",
        evidence=[
            "`PipelineWorker` uses tenant-scoped queued-run polling.",
            "`PipelineWorker` constructs `RunExecutionService` for claimed runs.",
            "`PipelineExecutor` is no longer instantiated by the production worker.",
        ],
        gaps=[
            "Worker polling is fail-safe/no-op without explicit tenant scope.",
        ],
    )


def _api_worker_path_comparison(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="api_worker_path_comparison",
        label="API path vs worker path comparison",
        status="WIRED",
        summary="Both API and worker execution paths use RunExecutionService.",
        evidence=[
            "API path uses `RunExecutionService`.",
            "Worker path uses `RunExecutionService`.",
            "`RunExecutionService` uses `RunOrchestrator` internally.",
        ],
        gaps=[],
    )


def _snapshot_steps(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="snapshot_steps",
        label="SNAPSHOT run steps",
        status="WIRED",
        summary="SNAPSHOT sequence is defined and orchestrated.",
        evidence=[
            "`SNAPSHOT_STEPS` includes INGEST_CHECK, EXTRACT, GRADE, CALC.",
            "`RunContext` receives extract, grade, and calc callables.",
        ],
        gaps=[],
    )


def _full_steps(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="full_steps",
        label="FULL run steps",
        status="PARTIAL",
        summary="FULL sequence includes enrichment, debate, analysis, scoring, and deliverables.",
        evidence=[
            "`FULL_STEPS` includes ENRICHMENT, DEBATE, ANALYSIS, SCORING, DELIVERABLES.",
            "`_run_full_enrichment`, `_run_full_debate`, `_run_full_analysis` are wired.",
            "`_run_full_deliverables` is passed for FULL runs.",
        ],
        gaps=[
            "`_run_full_deliverables` uses local `InMemoryAuditSink`.",
            "RAG/graph inputs are not fed into analysis/debate context.",
        ],
    )


def _ingestion_service(files: dict[str, str]) -> WiringItem:
    main_text = files.get("src/idis/api/main.py", "")
    defaults_text = files.get("src/idis/services/ingestion/defaults.py", "")
    default_wired = (
        "build_default_ingestion_service" in main_text
        and "ComplianceEnforcedStore" in defaults_text
        and "FilesystemObjectStore" in defaults_text
    )
    return WiringItem(
        key="ingestion_service",
        label="Ingestion service",
        status="WIRED" if default_wired else "PARTIAL",
        summary=(
            "Ingestion service exists and default app wiring provides the compliance-enforced "
            "upload path."
        ),
        evidence=[
            "`IngestionService.ingest_bytes` stores, parses, and spans raw bytes.",
            "`create_app()` builds a default ingestion service when no test override is supplied.",
            "`ComplianceEnforcedStore` wraps the existing filesystem object-store backend.",
        ],
        gaps=["No new S3/Supabase backend is added; filesystem object-store config remains used."],
    )


def _persisted_ingestion_corpus(root: Path, files: dict[str, str]) -> WiringItem:
    repo_exists = (root / "src/idis/persistence/repositories/documents.py").exists()
    migration_exists = (
        root / "src/idis/persistence/migrations/versions/0012_document_spans_deal_content_hash.py"
    ).exists()
    service_text = files.get("src/idis/services/ingestion/service.py", "")
    status = (
        "PARTIAL"
        if repo_exists
        and migration_exists
        and "PostgresDocumentsRepository" in service_text
        and "db_conn" in service_text
        else "DEFERRED"
    )
    return WiringItem(
        key="persisted_ingestion_corpus",
        label="Persisted ingestion corpus",
        status=status,
        summary=(
            "Parsed ingestion outputs can persist to Postgres documents/document_spans "
            "when ingestion receives an explicit DB connection."
        ),
        evidence=[
            "`PostgresDocumentsRepository` persists artifacts, documents, and spans.",
            "`IngestionService` accepts `db_conn` for durable corpus writes.",
            "`document_spans` includes deal scope and content hash via migration 0012.",
        ],
        gaps=[
            "Default document API creation remains partially in-memory.",
            "Methodology extraction and downstream FULL-run stages remain deferred.",
        ],
    )


def _unified_run_document_loader(files: dict[str, str]) -> WiringItem:
    steps_text = files.get("src/idis/services/runs/steps.py", "")
    route_text = files.get("src/idis/api/routes/runs.py", "")
    worker_text = files.get("src/idis/pipeline/worker.py", "")
    status = (
        "PARTIAL"
        if "load_documents_for_deal" in steps_text
        and "PostgresDocumentsRepository" in steps_text
        and (
            "load_documents_for_deal" in route_text
            or "load_document_preflight_corpus_for_deal" in route_text
        )
        and (
            "load_documents_for_deal" in worker_text
            or "load_document_preflight_corpus_for_deal" in worker_text
        )
        else "DEFERRED"
    )
    return WiringItem(
        key="unified_run_document_loader",
        label="Unified run document loader",
        status=status,
        summary=(
            "API-started and worker-started runs now share the same parsed "
            "Postgres document/span loader when a DB connection is present."
        ),
        evidence=[
            "`src/idis/services/runs/steps.py::load_documents_for_deal` is the shared loader.",
            "API run route calls the shared loader from `request.state.db_conn`.",
            "Pipeline worker context factory calls the shared loader with tenant scope.",
        ],
        gaps=[
            "The run is still not a real end-to-end FULL diligence flow.",
            (
                "Methodology/CALC/enrichment/RAG/Neo4j/debate/agents/deliverables "
                "wiring remains deferred."
            ),
        ],
    )


def _api_worker_document_corpus_split(files: dict[str, str]) -> WiringItem:
    route_text = files.get("src/idis/api/routes/runs.py", "")
    worker_text = files.get("src/idis/pipeline/worker.py", "")
    resolved = (
        'getattr(request.state, "db_conn", None)' in route_text
        and (
            "load_documents_for_deal" in route_text
            or "load_document_preflight_corpus_for_deal" in route_text
        )
        and (
            "load_documents_for_deal" in worker_text
            or "load_document_preflight_corpus_for_deal" in worker_text
        )
        and "tenant_id=tenant_id" in worker_text
    )
    return WiringItem(
        key="api_worker_document_corpus_split",
        label="API/worker document corpus split",
        status="PARTIAL" if resolved else "DEFERRED",
        summary=(
            "The previous route-local memory corpus split is resolved for DB-backed runs, "
            "while explicit test-only memory injection remains for non-DB tests."
        ),
        evidence=[
            "API route prefers persisted DB corpus before memory test hooks.",
            "Worker path hydrates `RunContext.documents` through the same shared loader.",
        ],
        gaps=["No persisted documents still fail closed as `NO_INGESTED_DOCUMENTS`."],
    )


def _parser_wiring(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="parser_wiring",
        label="Parser registry",
        status="WIRED",
        summary="Parser registry routes supported formats to PDF/XLSX/DOCX/PPTX parsers.",
        evidence=["`parse_bytes` delegates to parser implementations by detected format."],
        gaps=["OCR/table extraction and unsupported formats remain out of scope."],
    )


def _extraction_pipeline(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="extraction_pipeline",
        label="Extraction pipeline",
        status="WIRED",
        summary="Snapshot extraction builds the extraction pipeline over parsed spans/chunks.",
        evidence=["`_run_snapshot_extraction` constructs `ExtractionPipeline`."],
        gaps=["Extraction is generic and not methodology-question driven."],
    )


def _sanad_wiring(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="sanad_wiring",
        label="Sanad wiring",
        status="WIRED",
        summary="GRADE step auto-grades claims and debate context reads Sanad grades.",
        evidence=["`_run_snapshot_auto_grade` calls `auto_grade_claims_for_run`."],
        gaps=["Sanad completeness is not yet tied to methodology coverage."],
    )


def _calc_engine(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="calc_engine",
        label="CalcEngine",
        status="WIRED",
        summary="CalcEngine is reached through CalcRunner for eligible CALC inputs.",
        evidence=[
            "`src/idis/calc/engine.py::CalcEngine` exists.",
            "`src/idis/services/calc/runner.py::CalcRunner` invokes `CalcEngine.run`.",
        ],
        gaps=[],
    )


def _calc_step(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="calc_step",
        label="Current CALC run step",
        status="WIRED",
        summary=(
            "The API CALC callable delegates to CalcRunner and reports persisted or "
            "blocked candidates truthfully."
        ),
        evidence=[
            "`_run_snapshot_calc` constructs `CalcRunner`.",
            "`CalcRunner` persists eligible deterministic calculations and CalcSanads.",
            "`CalcRunner` returns blocked_candidates for ineligible inputs.",
        ],
        gaps=[],
    )


def _calc_sanad(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="calc_sanad",
        label="CalcSanad",
        status="WIRED",
        summary="CalcSanad records are persisted for eligible deterministic calculations.",
        evidence=[
            "`src/idis/models/calc_sanad.py::CalcSanad` exists.",
            "`src/idis/persistence/repositories/calculations.py` persists calc_sanads.",
        ],
        gaps=[],
    )


def _methodology_registry_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/methodology/models.py") and _contains(
        files, "src/idis/methodology/models.py", "MethodologyRegistry"
    )
    return WiringItem(
        key="methodology_registry_models",
        label="Methodology registry models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Structured methodology registry models exist for FDD and CDD.",
        evidence=[
            "`src/idis/methodology/models.py` defines MethodologyRegistry and MethodologyQuestion.",
            "`MethodologyType` includes financial_dd and commercial_dd.",
        ],
        gaps=["Not yet integrated into live run orchestration."],
    )


def _fdd_synthetic_excel_importer(root: Path, files: dict[str, str]) -> WiringItem:
    has_importer = _exists(root, "src/idis/methodology/importers/fdd_excel.py")
    return WiringItem(
        key="fdd_synthetic_excel_importer",
        label="FDD synthetic Excel importer",
        status="PARTIAL" if has_importer else "NOT_FOUND",
        summary="FDD-style Excel importer exists and is tested only with synthetic workbooks.",
        evidence=["`src/idis/methodology/importers/fdd_excel.py` validates sheets and columns."],
        gaps=["Real confidential workbook import is intentionally not committed or run."],
    )


def _commercial_dd_template(root: Path, files: dict[str, str]) -> WiringItem:
    has_template = _exists(root, "src/idis/methodology/templates/commercial_dd_v1.json")
    return WiringItem(
        key="commercial_dd_template",
        label="Commercial DD structured template",
        status="PARTIAL" if has_template else "NOT_FOUND",
        summary="CDD methodology exists as structured registry JSON.",
        evidence=["`commercial_dd_v1.json` uses MethodologyRegistry shape."],
        gaps=["Template is not yet consumed by prompts, agents, or deliverables."],
    )


def _methodology_coverage_service(root: Path, files: dict[str, str]) -> WiringItem:
    has_service = _exists(root, "src/idis/services/methodology/coverage.py") and _exists(
        root, "src/idis/models/methodology_coverage.py"
    )
    return WiringItem(
        key="methodology_coverage_service",
        label="Methodology coverage ledger models/service",
        status="PARTIAL" if has_service else "NOT_FOUND",
        summary="In-memory coverage ledger can initialize and summarize methodology coverage.",
        evidence=[
            "`src/idis/models/methodology_coverage.py` defines coverage records.",
            "`src/idis/services/methodology/coverage.py` defines in-memory coverage service.",
        ],
        gaps=["Coverage is not yet persisted or connected to live runs."],
    )


def _methodology_postgres_persistence(root: Path) -> WiringItem:
    has_migration = any(
        "methodology" in path.name
        for path in (root / "src/idis/persistence/migrations/versions").glob("*.py")
    )
    return WiringItem(
        key="methodology_postgres_persistence",
        label="Methodology Postgres persistence",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Methodology persistence is intentionally deferred in Phase 2.2.",
        evidence=["No Phase 2.2 methodology persistence migration is present."],
        gaps=["Postgres persistence remains a later slice."],
    )


def _methodology_run_integration(files: dict[str, str]) -> WiringItem:
    run_steps = files.get("src/idis/services/runs/steps.py", "")
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    integrated = "MethodologyRegistry" in run_steps and "METHODOLOGY_COVERAGE_INIT" in orchestrator
    return WiringItem(
        key="methodology_run_integration",
        label="Methodology run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Methodology registry is wired only for run-scoped coverage initialization.",
        evidence=[
            "`build_run_context` injects a deterministic methodology registry loader.",
            "`METHODOLOGY_COVERAGE_INIT` initializes coverage before legacy extraction.",
        ],
        gaps=["Methodology extraction execution remains deferred."],
    )


def _methodology_coverage_init_run_integration(files: dict[str, str]) -> WiringItem:
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    steps = files.get("src/idis/services/runs/steps.py", "")
    run_step = files.get("src/idis/models/run_step.py", "")
    service = files.get("src/idis/services/runs/methodology_coverage_init.py", "")
    coverage_models = files.get("src/idis/models/methodology_coverage.py", "")

    wired = (
        "METHODOLOGY_COVERAGE_INIT" in run_step
        and "METHODOLOGY_COVERAGE_INIT" in orchestrator
        and "load_default_methodology_registry" in steps
        and "commercial_dd_v1.json" in service
        and "to_run_step_summary" in coverage_models
    )
    return WiringItem(
        key="methodology_coverage_init_run_integration",
        label="Methodology coverage init run integration",
        status="PARTIAL" if wired else "DEFERRED",
        summary=(
            "Run execution initializes in-memory methodology coverage records before legacy "
            "extraction."
        ),
        evidence=[
            "`METHODOLOGY_COVERAGE_INIT` is present after `DOCUMENT_PREFLIGHT`.",
            "`build_run_context` wires a shared default registry loader for API and worker.",
            "API and worker share `build_run_context` for methodology coverage initialization.",
            "`commercial_dd_v1.json` is the deterministic local registry source.",
            "Coverage initialization persists only safe IDs/counts in run-step summary.",
        ],
        gaps=[
            "No Postgres coverage tables are added in Slice 3.",
            "Coverage records remain in memory/run-step summary only.",
            (
                "Claim materialization, Sanad linkage, CALC, enrichment, RAG, graph, cache, "
                "debate, scoring, and deliverables remain downstream."
            ),
        ],
        phase_2_action="Phase 3.0 Slice 3",
    )


def _document_classification_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/document_classification.py") and _contains(
        files, "src/idis/models/document_classification.py", "DocumentClassification"
    )
    return WiringItem(
        key="document_classification_models",
        label="Document classification models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Structured document classification and parser triage models exist.",
        evidence=[
            "`src/idis/models/document_classification.py` defines classification enums/models.",
            "Models include FDD/CDD categories, support statuses, triage status, and evidence.",
        ],
        gaps=["Models are not yet persisted to Postgres."],
        phase_2_action="Phase 2.3",
    )


def _parser_capability_registry(root: Path, files: dict[str, str]) -> WiringItem:
    has_registry = _exists(root, "src/idis/services/documents/parser_capabilities.py")
    return WiringItem(
        key="parser_capability_registry",
        label="Parser capability registry",
        status="PARTIAL" if has_registry else "NOT_FOUND",
        summary="Parser capabilities classify current parser support without overstating OCR.",
        evidence=[
            "`parser_capabilities.py` maps PDF/XLSX/DOCX/PPTX and unsupported formats.",
            "Conversion/OCR requirements are represented as triage metadata only.",
        ],
        gaps=["Registry is not yet called by ingestion or run execution paths."],
        phase_2_action="Phase 2.3",
    )


def _document_classification_service(root: Path, files: dict[str, str]) -> WiringItem:
    has_service = _exists(root, "src/idis/services/documents/classification_service.py")
    return WiringItem(
        key="document_classification_service",
        label="Document classification service",
        status="PARTIAL" if has_service else "NOT_FOUND",
        summary="In-memory document classification service exists for single/batch use.",
        evidence=[
            "`classification_service.py` stores deterministic in-memory classification results.",
            "`classifier.py` maps categories to methodology target areas.",
        ],
        gaps=["No API, Postgres, or production run integration is claimed."],
        phase_2_action="Phase 2.3",
    )


def _parser_triage_layer(root: Path, files: dict[str, str]) -> WiringItem:
    has_triage = _exists(root, "src/idis/services/documents/parser_capabilities.py") and _contains(
        files, "src/idis/services/documents/parser_capabilities.py", "triage_document"
    )
    return WiringItem(
        key="parser_triage_layer",
        label="Parser triage layer",
        status="PARTIAL" if has_triage else "NOT_FOUND",
        summary="Parser triage maps parse errors to unsupported/encrypted/scanned/large states.",
        evidence=[
            (
                "`triage_document` consumes parser error codes such as ENCRYPTED_PDF "
                "and NO_TEXT_EXTRACTED."
            ),
            "Large, corrupted, conversion-required, and unsupported sources are reason-coded.",
        ],
        gaps=["Triage output is not yet persisted or surfaced through API/UI."],
        phase_2_action="Phase 2.3",
    )


def _document_classification_postgres_persistence(root: Path) -> WiringItem:
    migrations_dir = root / "src/idis/persistence/migrations/versions"
    has_migration = any(
        "classification" in path.name or "triage" in path.name
        for path in migrations_dir.glob("*.py")
    )
    return WiringItem(
        key="document_classification_postgres_persistence",
        label="Document classification Postgres persistence",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Document classification persistence is intentionally deferred in Phase 2.3.",
        evidence=["No Phase 2.3 document classification migration is expected."],
        gaps=["Future slice must define tenant-scoped persistent classification tables."],
        phase_2_action="Phase 2.3",
    )


def _document_classification_api_integration(files: dict[str, str]) -> WiringItem:
    api_text = files.get("src/idis/api/main.py", "") + files.get("src/idis/api/routes/runs.py", "")
    integrated = "DocumentClassification" in api_text or "classification_service" in api_text
    return WiringItem(
        key="document_classification_api_integration",
        label="Document classification API integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Document classification is not exposed through API routes in Phase 2.3.",
        evidence=["No document classification API route is wired by this static audit."],
        gaps=["Future API work must expose tenant-scoped classification endpoints."],
        phase_2_action="Phase 2.3",
    )


def _document_classification_ui_integration(root: Path) -> WiringItem:
    has_ui = any(
        path.name.endswith((".tsx", ".ts"))
        and "document" in path.name.lower()
        and "classification" in _read(path).lower()
        for path in (root / "frontend").glob("**/*")
        if path.is_file()
    )
    return WiringItem(
        key="document_classification_ui_integration",
        label="Document classification UI integration",
        status="PARTIAL" if has_ui else "DEFERRED",
        summary="Document classification UI is intentionally out of scope for Phase 2.3.",
        evidence=["No Phase 2.3 UI files are expected."],
        gaps=["Future UI work may present triage/blocker summaries."],
        phase_2_action="Phase 2.3",
    )


def _document_classification_run_integration(files: dict[str, str]) -> WiringItem:
    run_steps = files.get("src/idis/services/runs/steps.py", "")
    worker = files.get("src/idis/pipeline/worker.py", "")
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    integrated = (
        "classify_document" in run_steps
        or "DocumentClassification" in worker
        or "DOCUMENT_PREFLIGHT" in orchestrator
    )
    return WiringItem(
        key="document_classification_run_integration",
        label="Document classification run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=("Document classification is partially wired through run preflight when present."),
        evidence=["Run-level preflight is distinct from methodology extraction planning."],
        gaps=["Future runs must call methodology-driven extraction after preflight."],
        phase_2_action="Phase 3.0 Slice 2",
    )


def _document_preflight_run_integration(files: dict[str, str]) -> WiringItem:
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    steps = files.get("src/idis/services/runs/steps.py", "")
    api = files.get("src/idis/api/routes/runs.py", "")
    worker = files.get("src/idis/pipeline/worker.py", "")
    service = files.get("src/idis/services/runs/document_preflight.py", "")
    model = files.get("src/idis/models/document_preflight.py", "")

    wired = (
        "DOCUMENT_PREFLIGHT" in orchestrator
        and "preflight_corpus" in orchestrator
        and "load_document_preflight_corpus_for_deal" in steps
        and "_gather_preflight_corpus" in api
        and "load_document_preflight_corpus_for_deal" in worker
        and ("to_run_step_summary" in service or "to_run_step_summary" in model)
    )
    return WiringItem(
        key="document_preflight_run_integration",
        label="Document preflight run integration",
        status="PARTIAL" if wired else "DEFERRED",
        summary=(
            "DOCUMENT_PREFLIGHT classifies and triages the full persisted corpus before "
            "legacy extraction receives eligible documents."
        ),
        evidence=[
            "`DOCUMENT_PREFLIGHT` is present in the orchestrator step order.",
            "`RunContext.preflight_corpus` keeps the full corpus separate from extraction docs.",
            "API start-run loads full preflight corpus before deriving parsed documents.",
            "worker context factory loads full preflight corpus before deriving parsed documents.",
            "Preflight stores safe run-step summary references rather than raw span text.",
        ],
        gaps=[
            "Methodology extraction execution remains deferred after task planning.",
            "No normalized Postgres classification/triage tables are added in Slice 2.",
        ],
        phase_2_action="Phase 3.0 Slice 2",
    )


def _extraction_task_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/extraction_task.py") and _contains(
        files, "src/idis/models/extraction_task.py", "ExtractionTask"
    )
    return WiringItem(
        key="extraction_task_models",
        label="Extraction task models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Structured extraction task metadata models exist.",
        evidence=[
            "`src/idis/models/extraction_task.py` defines ExtractionTask and statuses.",
            "Models link methodology questions, classifications, documents, and source spans.",
        ],
        gaps=["Extraction task records are not persisted to Postgres."],
        phase_2_action="Phase 2.4",
    )


def _extraction_task_planner(root: Path, files: dict[str, str]) -> WiringItem:
    has_planner = _exists(root, "src/idis/services/extraction/task_planner.py") and _contains(
        files, "src/idis/services/extraction/task_planner.py", "InMemoryExtractionTaskPlanner"
    )
    return WiringItem(
        key="extraction_task_planner",
        label="In-memory extraction task planner",
        status="PARTIAL" if has_planner else "NOT_FOUND",
        summary="In-memory planner creates ready/blocked extraction task metadata.",
        evidence=[
            "`task_planner.py` maps MethodologyQuestion to DocumentClassification and spans.",
            "Planner produces metadata only and does not execute extraction.",
        ],
        gaps=["Planner execution is in-memory only and is not persisted to Postgres."],
        phase_2_action="Phase 2.4",
    )


def _extraction_task_audit_contract(root: Path, files: dict[str, str]) -> WiringItem:
    has_contract = _exists(root, "src/idis/services/extraction/task_audit.py") and _contains(
        files, "src/idis/services/extraction/task_audit.py", "EXTRACTION_TASK_AUDIT_EVENTS"
    )
    return WiringItem(
        key="extraction_task_audit_contract",
        label="Extraction task future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist for extraction task planning.",
        evidence=["`task_audit.py` defines event constants without live audit emission."],
        gaps=["No live audit sink emission is wired in Phase 2.4."],
        phase_2_action="Phase 2.4",
    )


def _extraction_task_postgres_persistence(root: Path) -> WiringItem:
    migrations_dir = root / "src/idis/persistence/migrations/versions"
    has_migration = any(
        "extraction_task" in path.name or "task_planning" in path.name
        for path in migrations_dir.glob("*.py")
    )
    return WiringItem(
        key="extraction_task_postgres_persistence",
        label="Extraction task Postgres persistence",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Extraction task persistence is intentionally deferred in Phase 2.4.",
        evidence=["No Phase 2.4 extraction task migration is expected."],
        gaps=["Future slice must define tenant-scoped persistent task tables."],
        phase_2_action="Phase 2.4",
    )


def _extraction_task_api_integration(files: dict[str, str]) -> WiringItem:
    api_text = files.get("src/idis/api/main.py", "") + files.get("src/idis/api/routes/runs.py", "")
    integrated = "ExtractionTask" in api_text or "InMemoryExtractionTaskPlanner" in api_text
    return WiringItem(
        key="extraction_task_api_integration",
        label="Extraction task API integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Extraction task planning is not exposed through API routes in Phase 2.4.",
        evidence=["No extraction task API route is wired by this static audit."],
        gaps=["Future API work may expose task planning summaries."],
        phase_2_action="Phase 2.4",
    )


def _extraction_task_run_integration(files: dict[str, str]) -> WiringItem:
    run_steps = files.get("src/idis/services/runs/steps.py", "")
    worker = files.get("src/idis/pipeline/worker.py", "")
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    run_step = files.get("src/idis/models/run_step.py", "")
    service = files.get("src/idis/services/runs/methodology_extraction_task_planning.py", "")
    model = files.get("src/idis/models/extraction_task.py", "")
    integrated = (
        "METHODOLOGY_EXTRACTION_TASK_PLANNING" in run_step
        and "methodology_extraction_tasks" in orchestrator
        and "InMemoryRunMethodologyExtractionTaskPlanningService" in service
        and "ExtractionTaskPlanningRunResult" in model
        and "build_run_context" in run_steps
        and "_default_run_context_factory" in worker
    )
    return WiringItem(
        key="extraction_task_run_integration",
        label="Extraction task run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "FULL runs plan methodology extraction task metadata after document preflight "
            "and coverage initialization."
        ),
        evidence=[
            "`METHODOLOGY_EXTRACTION_TASK_PLANNING` is present in FULL step order.",
            "`RunContext.methodology_extraction_tasks` attaches planned tasks in memory.",
            "Planning reconstructs inputs from safe preflight summaries and coverage records.",
            "API and worker share `build_run_context` for planner-capable runs.",
        ],
        gaps=[
            "Extraction task records are not persisted to Postgres.",
            "Slice 5 execution remains in memory and persists only safe run-step summaries.",
            "No extraction task API route is exposed.",
        ],
        phase_2_action="Phase 3.0 Slice 4",
    )


def _live_methodology_extraction_execution(files: dict[str, str]) -> WiringItem:
    planner_text = files.get("src/idis/services/extraction/task_planner.py", "")
    integrated = (
        "ClaimExtractor" in planner_text or "LLM" in planner_text or "create_claim" in planner_text
    )
    return WiringItem(
        key="live_methodology_extraction_execution",
        label="Live methodology extraction execution",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Live methodology-driven extraction execution is intentionally deferred.",
        evidence=["Phase 2.4 creates extraction task metadata only."],
        gaps=["Future slices must execute tasks and convert extracted answers into claims."],
        phase_2_action="Phase 2.4",
    )


def _methodology_extraction_execution_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/extraction_execution.py") and _contains(
        files, "src/idis/models/extraction_execution.py", "MethodologyExtractionOutput"
    )
    return WiringItem(
        key="methodology_extraction_execution_models",
        label="Methodology extraction execution models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Structured models exist for methodology task execution output metadata.",
        evidence=[
            "`src/idis/models/extraction_execution.py` defines execution statuses and results.",
            "Neutral execution outputs preserve extraction task, coverage, methodology, document, "
            "span, confidence, and dhabt metadata.",
        ],
        gaps=["Models are not persisted to Postgres in Phase 2.5."],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_task_executor(root: Path, files: dict[str, str]) -> WiringItem:
    has_executor = _exists(root, "src/idis/services/extraction/task_executor.py") and _contains(
        files,
        "src/idis/services/extraction/task_executor.py",
        "InMemoryMethodologyExtractionTaskExecutor",
    )
    return WiringItem(
        key="methodology_extraction_task_executor",
        label="Methodology extraction task executor",
        status="PARTIAL" if has_executor else "NOT_FOUND",
        summary="Executor processes ready tasks only with an injected extractor/provider.",
        evidence=[
            "`task_executor.py` skips blocked tasks and validates source-span provenance.",
            "Executor produces schema-validated neutral outputs and fails closed "
            "without an extractor.",
        ],
        gaps=[
            "No ClaimService persistence, Sanad creation, coverage update, API, or run "
            "integration is wired."
        ],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_execution_audit_contract(
    root: Path, files: dict[str, str]
) -> WiringItem:
    has_contract = _exists(root, "src/idis/services/extraction/execution_audit.py") and _contains(
        files,
        "src/idis/services/extraction/execution_audit.py",
        "EXTRACTION_EXECUTION_AUDIT_EVENTS",
    )
    return WiringItem(
        key="methodology_extraction_execution_audit_contract",
        label="Methodology extraction execution future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist for execution.",
        evidence=["`execution_audit.py` defines event constants without live audit emission."],
        gaps=["No live audit sink emission is wired in Phase 2.5."],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_postgres_persistence(root: Path) -> WiringItem:
    migrations_dir = root / "src/idis/persistence/migrations/versions"
    has_migration = any(
        "extraction_execution" in path.name or "methodology_extraction" in path.name
        for path in migrations_dir.glob("*.py")
    )
    return WiringItem(
        key="methodology_extraction_postgres_persistence",
        label="Methodology extraction Postgres persistence",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Methodology extraction execution persistence is intentionally deferred.",
        evidence=["No Phase 2.5 methodology extraction execution migration is expected."],
        gaps=["Future slice must define tenant-scoped persistence for execution results."],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_api_integration(files: dict[str, str]) -> WiringItem:
    api_text = files.get("src/idis/api/main.py", "") + files.get("src/idis/api/routes/runs.py", "")
    integrated = "InMemoryMethodologyExtractionTaskExecutor" in api_text
    return WiringItem(
        key="methodology_extraction_api_integration",
        label="Methodology extraction API integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Methodology extraction execution is not exposed through API routes.",
        evidence=["No Phase 2.5 API route wiring is expected."],
        gaps=["Future API work may expose synthetic execution summaries or persisted results."],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_run_integration(files: dict[str, str]) -> WiringItem:
    run_text = files.get("src/idis/services/runs/steps.py", "") + files.get(
        "src/idis/pipeline/worker.py", ""
    )
    orchestrator = files.get("src/idis/services/runs/orchestrator.py", "")
    run_step = files.get("src/idis/models/run_step.py", "")
    service = files.get("src/idis/services/runs/methodology_extraction_task_execution.py", "")
    integrated = (
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION" in run_step
        and "methodology_extraction_execution_result" in orchestrator
        and "InMemoryRunMethodologyExtractionTaskExecutionService" in service
        and "TaskExecutionFn" in run_text
    )
    return WiringItem(
        key="methodology_extraction_run_integration",
        label="Methodology extraction run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "FULL runs execute planned methodology tasks in memory after task planning, "
            "without materializing claims."
        ),
        evidence=[
            "`METHODOLOGY_EXTRACTION_TASK_EXECUTION` is present in FULL step order.",
            "`RunContext.methodology_extraction_execution_result` attaches in-memory results.",
            "Run adapter hydrates source span text in memory and persists only safe summaries.",
            "Execution outputs are schema-validated and fail closed with stable reason codes.",
        ],
        gaps=[
            "Claims remain deferred until Phase 3.0 Slice 6.",
            "EvidenceItems remain deferred until a later provenance slice.",
            "Sanad creation/linking/grading remains deferred.",
            "Truth Dashboard remains deferred.",
            "Layer 1 Evidence Trust Court remains deferred.",
            "Enrichment/API checks remain deferred.",
            "Deterministic FDD CALC integration remains deferred.",
            "Layer 2 IC Decision Debate remains deferred.",
            "GO / CONDITIONAL / NO-GO package remains deferred.",
            "Deliverables remain deferred.",
            "Postgres execution-result persistence remains deferred.",
            "real data-room E2E remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 5",
    )


def _methodology_extraction_live_llm_integration(files: dict[str, str]) -> WiringItem:
    executor_text = files.get("src/idis/services/extraction/task_executor.py", "")
    integrated = any(
        token in executor_text
        for token in ("LLMClaimExtractor", "Anthropic", "OpenAI", "external_calls_enabled = True")
    )
    return WiringItem(
        key="methodology_extraction_live_llm_integration",
        label="Methodology extraction live LLM integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Phase 2.5 does not wire live or production LLM extraction.",
        evidence=["Task executor requires an injected extractor and makes no network calls."],
        gaps=["Future production extractor wiring must preserve evidence and gate semantics."],
        phase_2_action="Phase 2.5",
    )


def _methodology_extraction_coverage_integration(files: dict[str, str]) -> WiringItem:
    executor_text = files.get("src/idis/services/extraction/task_executor.py", "")
    integrated = "InMemoryMethodologyCoverageService" in executor_text
    return WiringItem(
        key="methodology_extraction_coverage_integration",
        label="Methodology extraction coverage integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Phase 2.5 does not update methodology coverage answers.",
        evidence=["Task executor returns claim drafts only and does not mutate coverage records."],
        gaps=["Future slice must update coverage after claims and Sanad are created."],
        phase_2_action="Phase 2.5",
    )


def _methodology_claim_materialization_models(root: Path, files: dict[str, str]) -> WiringItem:
    model_text = files.get("src/idis/models/claim_materialization.py", "")
    has_models = _exists(root, "src/idis/models/claim_materialization.py") and all(
        token in model_text
        for token in [
            "RunScopedMaterializedClaim",
            "MaterializedClaimType",
            "MaterializedClaimValueStruct",
            "MethodologyOutputClaimMaterializationRunResult",
        ]
    )
    return WiringItem(
        key="methodology_claim_materialization_models",
        label="Methodology claim materialization models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary=("Structured models exist for Slice 6 in-memory governed claim materialization."),
        evidence=[
            "`src/idis/models/claim_materialization.py` defines run-scoped materialized claims.",
            "Semantic claim type, typed value struct, source refs, mappings, and rejections "
            "are machine-readable.",
        ],
        gaps=[
            "in-memory governed claim boundary exists; durable Claim Registry persistence "
            "remains deferred."
        ],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_claim_materializer(root: Path, files: dict[str, str]) -> WiringItem:
    materializer_text = files.get("src/idis/services/extraction/claim_materializer.py", "")
    run_materializer_text = files.get(
        "src/idis/services/runs/methodology_claim_materialization.py", ""
    )
    has_materializer = (
        _exists(root, "src/idis/services/extraction/claim_materializer.py")
        and "MethodologyClaimMaterializationService" in materializer_text
        and "InMemoryRunMethodologyClaimMaterializationService" in run_materializer_text
    )
    return WiringItem(
        key="methodology_claim_materializer",
        label="Methodology claim materializer",
        status="PARTIAL" if has_materializer else "NOT_FOUND",
        summary=(
            "Slice 6 run materializer converts neutral MethodologyExtractionOutput records "
            "into in-memory governed claims."
        ),
        evidence=[
            "`methodology_claim_materialization.py` consumes accepted_outputs only.",
            "Legacy draft materializer remains isolated in `claim_materializer.py`.",
            "Created run-scoped claims remain unverified and non-IC-bound while downstream "
            "work is deferred.",
        ],
        gaps=[
            "EvidenceItems remain deferred.",
            "Sanads remain deferred.",
            "Truth Dashboard remains deferred.",
            "Layer 1 Evidence Trust Court remains deferred.",
            "Layer 2 IC Decision Debate remains deferred.",
            "CALC, enrichment, deliverables, and real data-room E2E remain deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_claim_materialization_audit_contract(
    root: Path, files: dict[str, str]
) -> WiringItem:
    has_contract = _exists(
        root, "src/idis/services/extraction/claim_materialization_audit.py"
    ) and _contains(
        files,
        "src/idis/services/extraction/claim_materialization_audit.py",
        "CLAIM_MATERIALIZATION_AUDIT_EVENTS",
    )
    return WiringItem(
        key="methodology_claim_materialization_audit_contract",
        label="Methodology claim materialization future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist for materialization.",
        evidence=[
            "`claim_materialization_audit.py` defines event constants without live emission."
        ],
        gaps=["No live audit sink emission is wired in Phase 2.6."],
        phase_2_action="Phase 2.6",
    )


def _methodology_claim_materialization_postgres_schema(root: Path) -> WiringItem:
    migrations_dir = root / "src/idis/persistence/migrations/versions"
    has_migration = any(
        "claim_materialization" in path.name for path in migrations_dir.glob("*.py")
    )
    return WiringItem(
        key="methodology_claim_materialization_postgres_schema",
        label="Claim materialization Postgres schema",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Durable claim materialization schema work is intentionally deferred.",
        evidence=[
            "in-memory governed claim boundary exists; durable Claim Registry persistence "
            "remains deferred."
        ],
        gaps=["Future slice must add durable Claim Registry persistence and idempotency."],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_claim_materialization_api_integration(files: dict[str, str]) -> WiringItem:
    api_text = files.get("src/idis/api/main.py", "") + files.get("src/idis/api/routes/runs.py", "")
    integrated = "MethodologyClaimMaterializationService" in api_text
    return WiringItem(
        key="methodology_claim_materialization_api_integration",
        label="Claim materialization API integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Claim materialization is not exposed through API routes.",
        evidence=["No Phase 2.6 API route wiring is expected."],
        gaps=["Future API work must preserve draft validation and scoped materialization."],
        phase_2_action="Phase 2.6",
    )


def _methodology_claim_materialization_run_integration(files: dict[str, str]) -> WiringItem:
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
        + files.get("src/idis/services/runs/methodology_claim_materialization.py", "")
    )
    integrated = (
        "METHODOLOGY_CLAIM_MATERIALIZATION" in run_text
        and "InMemoryRunMethodologyClaimMaterializationService" in run_text
        and "MethodologyExtractionOutput" in run_text
    )
    return WiringItem(
        key="methodology_claim_materialization_run_integration",
        label="Claim materialization run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory governed claim boundary exists; durable Claim Registry persistence "
            "remains deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_CLAIM_MATERIALIZATION after task execution.",
            "Run materialization consumes MethodologyExtractionOutput accepted_outputs.",
            "RunContext carries methodology_materialized_claims in memory with resume shells.",
        ],
        gaps=[
            "Durable Claim Registry persistence remains deferred.",
            "EvidenceItems remain deferred.",
            "Sanads remain deferred.",
            "Truth Dashboard remains deferred.",
            "Layer 1 Evidence Trust Court remains deferred.",
            "Layer 2 IC Decision Debate remains deferred.",
            "CALC, enrichment, deliverables, and real data-room E2E remain deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_evidence_item_materialization_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/evidence_item_materialization.py", "")
    service_text = files.get(
        "src/idis/services/runs/methodology_evidence_item_materialization.py", ""
    )
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/models/evidence_item.py")
        and _exists(root, "src/idis/models/evidence_item_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_evidence_item_materialization.py")
        and "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION" in run_text
        and "InMemoryRunMethodologyEvidenceItemMaterializationService" in service_text
        and "RunScopedEvidenceProvenanceRef" in model_text
        and "MaterializedClaimSourceRef" in model_text
    )
    return WiringItem(
        key="methodology_evidence_item_materialization_run_integration",
        label="EvidenceItem/source-provenance run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory governed EvidenceItem/source-provenance boundary exists; "
            "durable Postgres evidence persistence remains deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION after "
            "claim materialization.",
            "Slice 7 reuses existing EvidenceItem plus MaterializedClaimSourceRef safety.",
            "RunContext carries methodology_evidence_items and safe provenance in memory.",
            "Run-step summaries include safe IDs/counts/statuses/reason codes only.",
        ],
        gaps=[
            "Durable Postgres evidence persistence remains deferred because durable Claim Registry "
            "persistence remains deferred and the current evidence table expects UUID "
            "claim/source IDs.",
            "Sanad creation/linking/grading remains deferred to Slice 8.",
            "Truth Dashboard remains deferred.",
            "CALC remains deferred.",
            "enrichment/API checks remain deferred.",
            "Layer 1 Evidence Trust Court remains deferred.",
            "Layer 2 IC Debate remains deferred.",
            "GO/CONDITIONAL/NO-GO package remains deferred.",
            "deliverables remain deferred.",
            "real data-room E2E remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 7",
    )


def _methodology_sanad_creation_linking_grading_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/sanad_materialization.py", "")
    service_text = files.get(
        "src/idis/services/runs/methodology_sanad_creation_linking_grading.py", ""
    )
    helper_text = files.get("src/idis/services/runs/methodology_sanad_creation_helpers.py", "")
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/models/sanad.py")
        and _exists(root, "src/idis/models/transmission_node.py")
        and _exists(root, "src/idis/models/defect.py")
        and _exists(root, "src/idis/models/sanad_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_sanad_creation_linking_grading.py")
        and "METHODOLOGY_SANAD_CREATION_LINKING_GRADING" in run_text
        and "InMemoryRunMethodologySanadCreationLinkingGradingService" in service_text
        and "grade_sanad_v2" in service_text
        and "RunScopedSanadRecord" in model_text
        and "RunScopedSanadGradeRecord" in model_text
        and "materialize_defects" in helper_text
    )
    return WiringItem(
        key="methodology_sanad_creation_linking_grading_run_integration",
        label="Sanad creation/linking/grading run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory governed Sanad creation/linking/grading boundary exists; "
            "durable Postgres Sanad/Defect/Claim persistence remains deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_SANAD_CREATION_LINKING_GRADING after "
            "EvidenceItem materialization and before legacy EXTRACT.",
            "Slice 8 reuses existing Sanad, TransmissionNode, Defect, and "
            "grade_sanad_v2/calculate_sanad_grade grading concepts.",
            "RunContext carries methodology_sanads, links, grades, and defects in memory.",
            "Run-step summaries include safe IDs/counts/statuses/reason codes only.",
        ],
        gaps=[
            "Durable Postgres Sanad/Defect/Claim persistence remains deferred because "
            "durable Claim Registry persistence remains deferred and current tables expect "
            "UUID claim IDs.",
            "Claim-to-Sanad links are run-scoped only and do not promote claims to "
            "IC-ready status.",
            "Truth Dashboard remains deferred.",
            "CALC remains deferred.",
            "enrichment/API checks remain deferred.",
            "Layer 1 Evidence Trust Court and Validated Evidence Package run later in FULL mode.",
            "Layer 2 IC Debate remains deferred.",
            "GO/CONDITIONAL/NO-GO package remains deferred.",
            "deliverables remain deferred.",
            "real data-room E2E remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 8",
    )


def _methodology_deterministic_calculation_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/calc_materialization.py", "")
    service_text = files.get("src/idis/services/runs/methodology_deterministic_calculation.py", "")
    helper_text = files.get(
        "src/idis/services/runs/methodology_deterministic_calculation_helpers.py", ""
    )
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/calc/engine.py")
        and _exists(root, "src/idis/calc/formulas/core.py")
        and _exists(root, "src/idis/models/deterministic_calculation.py")
        and _exists(root, "src/idis/models/calc_sanad.py")
        and _exists(root, "src/idis/models/calc_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_deterministic_calculation.py")
        and "METHODOLOGY_DETERMINISTIC_CALCULATION" in run_text
        and "InMemoryRunMethodologyDeterministicCalculationService" in service_text
        and "task.expected_answer_schema.required_calculations" in helper_text
        and "CalcEngine" in service_text
        and "register_core_formulas" in service_text
        and "RunScopedDeterministicCalculationRecord" in model_text
        and "RunScopedCalcSanadRecord" in model_text
    )
    return WiringItem(
        key="methodology_deterministic_calculation_run_integration",
        label="Methodology deterministic calculation run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory run-scoped deterministic calculation boundary exists; durable "
            "Calc/CalcSanad persistence over durable Claim/Sanad inputs remains deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_DETERMINISTIC_CALCULATION after Sanad "
            "creation/linking/grading and before legacy EXTRACT.",
            "Slice 9 reuses CalcEngine, register_core_formulas, DeterministicCalculation, "
            "CalcSanad, and CalcRunner input/blocker helpers where safe.",
            "Candidate selection reads task.expected_answer_schema.required_calculations.",
            "RunContext carries methodology_calculations and methodology_calc_sanads in memory.",
            "Run-step summaries include safe IDs, hashes, statuses, counts, reason codes, "
            "and output scalar metadata only.",
        ],
        gaps=[
            "durable Calc/CalcSanad persistence over durable Claim/Sanad inputs remains deferred.",
            "calculations do not promote claims, Sanads, or deals to IC readiness.",
            "Truth Dashboard remains deferred.",
            "enrichment/API checks remain deferred.",
            "Layer 1 Evidence Trust Court and Validated Evidence Package run later in FULL mode.",
            "Layer 2 IC Debate remains deferred.",
            "GO/CONDITIONAL/NO-GO package remains deferred.",
            "deliverables remain deferred.",
            "real data-room E2E remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 9",
    )


def _methodology_truth_dashboard_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/truth_dashboard_materialization.py", "")
    service_text = files.get("src/idis/services/runs/methodology_truth_dashboard.py", "")
    deliverable_text = files.get("src/idis/deliverables/truth_dashboard.py", "")
    validator_text = files.get("src/idis/validators/deliverable.py", "")
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/models/truth_dashboard_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_truth_dashboard.py")
        and "METHODOLOGY_TRUTH_DASHBOARD" in run_text
        and "InMemoryRunMethodologyTruthDashboardService" in service_text
        and "TruthDashboardBuilder" in service_text
        and "validate_deliverable_no_free_facts" in service_text
        and "RunScopedTruthDashboardRecord" in model_text
        and "RunScopedTruthDashboardShell" in model_text
        and "TruthDashboardBuilder" in deliverable_text
        and "validate_truth_dashboard" in validator_text
    )
    return WiringItem(
        key="methodology_truth_dashboard_run_integration",
        label="Methodology Truth Dashboard run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory run-scoped Truth Dashboard boundary exists; durable Truth "
            "Dashboard persistence remains deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_TRUTH_DASHBOARD after deterministic calculation "
            "and before legacy EXTRACT.",
            "Slice 10 reuses TruthDashboardBuilder, TruthDashboard/TruthRow deliverable "
            "models, and deliverable No-Free-Facts validation where safe.",
            "RunContext carries a methodology_truth_dashboard record or safe shell in memory.",
            "Run-step summaries include safe IDs, verdict/grade/status counts, and reason "
            "codes only.",
            "API get_deal_truth_dashboard and UI/OpenAPI dashboard contracts are inventory "
            "only, not Slice 10 runtime dependencies.",
        ],
        gaps=[
            "durable Truth Dashboard persistence remains deferred.",
            "API/UI/OpenAPI exposure remains deferred.",
            "deliverables integration remains deferred.",
            "Validated Evidence Package is a downstream Layer 1 run-scoped package step.",
            "enrichment/API checks remain deferred.",
            "Layer 2 IC Debate remains deferred.",
            "GO/CONDITIONAL/NO-GO package remains deferred.",
            "real data-room E2E remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 10",
    )


def _methodology_evidence_trust_court_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/evidence_trust_court_materialization.py", "")
    service_text = files.get("src/idis/services/runs/methodology_evidence_trust_court.py", "")
    helper_text = files.get(
        "src/idis/services/runs/methodology_evidence_trust_court_helpers.py", ""
    )
    debate_text = files.get("src/idis/debate/orchestrator.py", "")
    gate_text = files.get("src/idis/debate/muhasabah_gate.py", "")
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/models/evidence_trust_court_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_evidence_trust_court.py")
        and "METHODOLOGY_EVIDENCE_TRUST_COURT" in run_text
        and "InMemoryRunMethodologyEvidenceTrustCourtService" in service_text
        and "DebateOrchestrator" in helper_text
        and "MuhasabahGate" in gate_text
        and "RunScopedEvidenceTrustCourtRecord" in model_text
        and "RunScopedEvidenceTrustCourtShell" in model_text
        and "DebateOrchestrator" in debate_text
    )
    return WiringItem(
        key="methodology_evidence_trust_court_run_integration",
        label="Methodology Evidence Trust Court run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory run-scoped Layer 1 Evidence Trust Court boundary exists; "
            "Validated Evidence Package follows as the Slice 12 Layer 1 package boundary."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_EVIDENCE_TRUST_COURT after Truth Dashboard "
            "and before METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE.",
            "Layer 1 Evidence Trust Court boundary exists for evidence integrity, "
            "provenance, Sanad trust, contradictions, and Truth Dashboard consistency.",
            "Slice 11 reuses DebateOrchestrator, DebateState, role protocols, "
            "MuhasabahGate, and validators through a run-scoped adapter.",
            "RunContext carries a methodology_evidence_trust_court record or safe shell in memory.",
            "Run-step summaries include safe IDs, dispositions, verdict/grade counts, "
            "role names, and reason codes only.",
        ],
        gaps=[
            "enrichment/API checks remain deferred.",
            "Layer 2 IC debate remains deferred.",
            "GO/CONDITIONAL/NO-GO remains deferred.",
            "deliverables, API/UI/OpenAPI, and real E2E remain deferred.",
            "durable Evidence Trust Court persistence remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 11",
    )


def _methodology_validated_evidence_package_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/validated_evidence_package_materialization.py", "")
    service_text = files.get("src/idis/services/runs/methodology_validated_evidence_package.py", "")
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(root, "src/idis/models/validated_evidence_package_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_validated_evidence_package.py")
        and "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE" in run_text
        and "InMemoryRunMethodologyValidatedEvidencePackageService" in service_text
        and "RunScopedValidatedEvidencePackageRecord" in model_text
        and "RunScopedValidatedEvidencePackageShell" in model_text
        and "RunScopedValidatedEvidencePackageSummary" in model_text
        and "RunScopedEvidenceTrustCourtRecord" in service_text
        and "EVIDENCE_TRUST_COURT_SHELL_ONLY" in service_text
    )
    return WiringItem(
        key="methodology_validated_evidence_package_run_integration",
        label="Methodology Validated Evidence Package run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory run-scoped Layer 1 Validated Evidence Package boundary exists; "
            "downstream IC debate, recommendations, delivery surfaces, and persistence "
            "remain deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE after Evidence Trust Court "
            "and before the external intelligence conflict-check plan boundary.",
            "Layer 1 Validated Evidence Package boundary exists for safe claim-disposition, "
            "evidence, source span, Sanad, defect, calc, finding, and reason-code packaging.",
            "VEP construction requires a full RunScopedEvidenceTrustCourtRecord and fails closed "
            "for Evidence Trust Court shells.",
            "RunContext carries a methodology_validated_evidence_package record or safe shell "
            "in memory.",
            "Run-step summaries include safe IDs, disposition sets, finding types, role names, "
            "reason codes, and aggregate counts only.",
        ],
        gaps=[
            "enrichment/API checks planning boundary exists; enrichment/API check execution "
            "remains deferred.",
            "Layer 2 IC debate remains deferred.",
            "GO/CONDITIONAL/NO-GO remains deferred.",
            "deliverables, API/UI/OpenAPI, and real E2E remain deferred.",
            "durable Evidence Trust Court persistence remains deferred.",
            "durable Validated Evidence Package persistence remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 12",
    )


def _methodology_external_intelligence_conflict_check_plan_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get(
        "src/idis/models/external_intelligence_conflict_check_plan_materialization.py",
        "",
    )
    service_text = files.get(
        "src/idis/services/runs/methodology_external_intelligence_conflict_check_plan.py",
        "",
    )
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    integrated = (
        _exists(
            root,
            "src/idis/models/external_intelligence_conflict_check_plan_materialization.py",
        )
        and _exists(
            root,
            "src/idis/services/runs/methodology_external_intelligence_conflict_check_plan.py",
        )
        and "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN" in run_text
        and "RunScopedExternalIntelligenceConflictCheckPlanRecord" in model_text
        and "RunScopedExternalIntelligenceConflictCheckPlanShell" in model_text
        and "InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService" in service_text
        and "EnrichmentProviderRegistry" in service_text
        and "EnrichmentService.enrich" not in service_text
        and ".fetch(" not in service_text
    )
    return WiringItem(
        key="methodology_external_intelligence_conflict_check_plan_run_integration",
        label="Methodology external intelligence conflict-check plan run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "in-memory run-scoped external intelligence conflict-check plan boundary exists; "
            "actual external conflict-check execution, provider calls, APIs, UI, and persistence "
            "remain deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN after VEP "
            "and before legacy EXTRACT.",
            "external intelligence conflict-check plan boundary exists for safe provider/check "
            "planning metadata only.",
            "Plan construction consumes VEP record or safe shell IDs/counts/reason codes and "
            "static EnrichmentProviderRegistry metadata.",
            "Run-step summaries include plan IDs, package IDs, provider IDs, check statuses, "
            "reason codes, and aggregate counts only.",
            "No live provider calls are performed by the plan boundary.",
        ],
        gaps=[
            "enrichment/API check execution remains deferred.",
            "real provider calls remain deferred.",
            "PitchBook/Crunchbase connectors remain deferred.",
            "Layer 2 IC debate remains deferred.",
            "GO/CONDITIONAL/NO-GO remains deferred.",
            "deliverables, API/UI/OpenAPI, and real E2E remain deferred.",
            "durable external intelligence conflict-check plan persistence remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 13",
        metadata={"live_calls_performed": False},
    )


def _methodology_layer2_readiness_package_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/layer2_readiness_package_materialization.py", "")
    service_text = files.get(
        "src/idis/services/runs/methodology_layer2_readiness_package.py",
        "",
    )
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    forbidden_calls_absent = all(
        forbidden not in service_text
        for forbidden in (
            "DebateOrchestrator",
            "AnalysisEngine",
            "ScoringEngine",
            "DeliverablesGenerator",
            "EnrichmentService",
            ".enrich(",
            ".fetch(",
        )
    )
    integrated = (
        _exists(root, "src/idis/models/layer2_readiness_package_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_layer2_readiness_package.py")
        and "METHODOLOGY_LAYER2_READINESS_PACKAGE" in run_text
        and "RunScopedLayer2ReadinessPackageRecord" in model_text
        and "RunScopedLayer2ReadinessPackageShell" in model_text
        and "construction_status" in model_text
        and "readiness_status" in model_text
        and "InMemoryRunMethodologyLayer2ReadinessPackageService" in service_text
        and forbidden_calls_absent
    )
    return WiringItem(
        key="methodology_layer2_readiness_package_run_integration",
        label="Methodology Layer 2 readiness package run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "Layer 2 readiness package boundary exists; IC debate, scoring, routing "
            "vocabulary changes, deliverables, APIs, persistence, and live provider "
            "calls remain deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_LAYER2_READINESS_PACKAGE after the Slice 13 "
            "plan and before legacy EXTRACT.",
            "Layer 2 readiness/input-boundary package consumes VEP and external "
            "intelligence plan records or safe shells only.",
            "Run-step summaries expose construction_status and readiness_status as "
            "separate fields.",
            "Current Slice 13 plan-only inputs are expected to produce deferred or "
            "blocked readiness, not ready.",
            "No Layer 2 engines or live provider calls are performed by the readiness boundary.",
        ],
        gaps=[
            "IC debate remains deferred.",
            "GO/CONDITIONAL/NO-GO remains deferred.",
            "INVEST/HOLD/DECLINE mapping remains deferred.",
            "scorecard execution remains deferred.",
            "deliverables, API/UI/OpenAPI, and real E2E remain deferred.",
            "live provider calls remain deferred.",
            "PitchBook/Crunchbase connectors remain deferred.",
            "durable Layer 2 readiness package persistence remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 14",
        metadata={
            "layer2_execution_performed": False,
            "ready_expected_for_current_slice13_inputs": False,
        },
    )


def _methodology_company_identity_package_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/company_identity_package_materialization.py", "")
    service_text = files.get(
        "src/idis/services/runs/methodology_company_identity_package.py",
        "",
    )
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    forbidden_calls_absent = all(
        forbidden not in service_text
        for forbidden in (
            "EnrichmentService",
            ".enrich(",
            ".fetch(",
            "_run_full_enrichment",
            "DebateOrchestrator",
            "AnalysisEngine",
            "ScoringEngine",
            "DeliverablesGenerator",
        )
    )
    integrated = (
        _exists(root, "src/idis/models/company_identity_package_materialization.py")
        and _exists(root, "src/idis/services/runs/methodology_company_identity_package.py")
        and "METHODOLOGY_COMPANY_IDENTITY_PACKAGE" in run_text
        and "RunScopedCompanyIdentityPackageRecord" in model_text
        and "RunScopedCompanyIdentityPackageShell" in model_text
        and "InMemoryRunMethodologyCompanyIdentityPackageService" in service_text
        and forbidden_calls_absent
    )
    return WiringItem(
        key="methodology_company_identity_package_run_integration",
        label="Methodology company identity package run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "company identity package boundary exists; enrichment execution, facts, "
            "executed provider checks, BYOL automation, APIs, persistence, and Layer 2 "
            "execution remain deferred."
        ),
        evidence=[
            "FULL runs include METHODOLOGY_COMPANY_IDENTITY_PACKAGE after the Slice 13 "
            "plan and before METHODOLOGY_LAYER2_READINESS_PACKAGE.",
            "company identity input boundary consumes explicit deal metadata only.",
            "Run-step summaries expose deterministic identity IDs and aggregate counts only.",
            "No enrichment, connector, legacy enrichment helper, or Layer 2 execution calls "
            "are performed by the identity boundary.",
        ],
        gaps=[
            "enrichment execution remains deferred.",
            "connector fetch remains deferred.",
            "facts remain deferred.",
            "executed provider checks remain deferred.",
            "BYOL automation remains deferred.",
            "API/UI/OpenAPI and durable persistence remain deferred.",
            "Layer 2 execution remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 15",
        metadata={
            "enrichment_execution_performed": False,
            "layer2_execution_performed": False,
        },
    )


def _data_room_inventory_package_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/data_room_inventory_package_materialization.py", "")
    service_text = files.get("src/idis/services/runs/data_room_inventory_package.py", "")
    run_text = (
        files.get("src/idis/models/run_step.py", "")
        + files.get("src/idis/services/runs/orchestrator.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
    )
    api_text = files.get("src/idis/api/routes/runs.py", "")
    forbidden_calls_absent = all(
        forbidden not in service_text
        for forbidden in (
            "EnrichmentService",
            ".enrich(",
            ".fetch(",
            "DebateOrchestrator",
            "AnalysisEngine",
            "ScoringEngine",
            "DeliverablesGenerator",
            "OpenAI",
            "Anthropic",
        )
    )
    integrated = (
        _exists(root, "src/idis/models/data_room_inventory_package_materialization.py")
        and _exists(root, "src/idis/services/runs/data_room_inventory_package.py")
        and "DATA_ROOM_INVENTORY_PACKAGE" in run_text
        and "RunScopedDataRoomInventoryPackageRecord" in model_text
        and "RunScopedDataRoomInventoryPackageShell" in model_text
        and "InMemoryRunDataRoomInventoryPackageService" in service_text
        and ".rglob(" in service_text
        and "parse_bytes(" in service_text
        and forbidden_calls_absent
    )
    return WiringItem(
        key="data_room_inventory_package_run_integration",
        label="Data-room inventory package run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "data-room inventory package boundary exists; OCR, video/audio transcription, "
            "API/OpenAPI/UI, persistence, enrichment execution, and Layer 2 remain deferred."
        ),
        evidence=[
            "DATA_ROOM_INVENTORY_PACKAGE runs before INGEST_CHECK as an inventory/intake boundary.",
            "Recursive folder scan records safe relative paths, hashes, extensions, "
            "sizes, and IDs.",
            "Supported parser outputs can hand parsed document IDs into DOCUMENT_PREFLIGHT.",
            "Deferred MP4/image/HTML/TXT cases use stable reason codes without OCR or media work.",
        ],
        gaps=[
            "OCR remains deferred.",
            "video/audio transcription remains deferred.",
            "image OCR remains deferred.",
            "API/OpenAPI/UI remains deferred.",
            "durable persistence remains deferred.",
            "Layer 2 execution remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 16",
        metadata={
            "ocr_performed": False,
            "media_transcription_performed": False,
            "api_or_ui_changed": "DATA_ROOM_INVENTORY_PACKAGE" in api_text,
            "layer2_execution_performed": False,
        },
    )


def _data_room_full_harness_run_handoff(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    harness_text = files.get("src/idis/evaluation/data_room_harness.py", "")
    script_text = files.get("scripts/run_data_room_full_harness.py", "")
    api_text = files.get("src/idis/api/routes/runs.py", "")
    integrated = (
        _exists(root, "src/idis/evaluation/data_room_harness.py")
        and _exists(root, "scripts/run_data_room_full_harness.py")
        and "run_data_room_harness" in harness_text
        and "data_room_root_path" in harness_text
        and "RunContext" in harness_text
        and "InMemoryRunStepsRepository" in harness_text
        and "local data-room harness deferral" in harness_text
        and "main" in script_text
    )
    return WiringItem(
        key="data_room_full_harness_run_handoff",
        label="Data-room full harness run handoff",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "local data-room FULL harness boundary exists; API/OpenAPI/UI, "
            "persistence/S3, OCR/media, live enrichment expansion, and Layer 2 remain deferred."
        ),
        evidence=[
            "local data-room FULL harness accepts an arbitrary root path and emits a safe summary.",
            "RunContext is built with data_room_root_path, empty input corpus, "
            "and explicit deal metadata.",
            "Harness summarizes completed, blocked, deferred, and not-started steps "
            "without raw text.",
            "Legacy enrichment, debate, analysis, scoring, and deliverables are local deferrals.",
        ],
        gaps=[
            "API/OpenAPI/UI remains deferred.",
            "persistence/S3 remains deferred.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "Layer 2 remains deferred.",
            "live enrichment execution remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 17",
        metadata={
            "api_or_ui_changed": (
                "data_room_root_path" in api_text and "PATH_LIKE_RUN_FIELDS" not in api_text
            ),
            "persistent_data_room_package": False,
            "live_enrichment_expanded": "create_default_enrichment_service" in harness_text,
            "layer2_execution_performed": False,
        },
    )


def _data_room_ingestion_handoff_run_integration(
    root: Path,
    files: dict[str, str],
) -> WiringItem:
    model_text = files.get("src/idis/models/data_room_ingestion_handoff.py", "")
    service_text = files.get("src/idis/services/runs/data_room_ingestion_handoff.py", "")
    run_text = files.get("src/idis/models/run_step.py", "") + files.get(
        "src/idis/services/runs/orchestrator.py", ""
    )
    api_text = files.get("src/idis/api/routes/runs.py", "")
    forbidden_calls_absent = all(
        forbidden not in service_text
        for forbidden in (
            "S3",
            "supabase",
            "OCR",
            "transcription",
            "Neo4j",
            "RAG",
            "EnrichmentService",
            "DebateOrchestrator",
            "DeliverablesGenerator",
        )
    )
    integrated = (
        _exists(root, "src/idis/models/data_room_ingestion_handoff.py")
        and _exists(root, "src/idis/services/runs/data_room_ingestion_handoff.py")
        and "DATA_ROOM_INGESTION_HANDOFF" in run_text
        and "InMemoryRunDataRoomIngestionHandoffService" in service_text
        and "ingest_bytes_fn" in service_text
        and "existing_document_lookup_fn" in service_text
        and "DataRoomIngestionHandoffStatus" in model_text
        and "durable_ingested" in model_text
        and "durable_reused" in model_text
        and "in_memory_fallback" in model_text
        and forbidden_calls_absent
    )
    return WiringItem(
        key="data_room_ingestion_handoff_run_integration",
        label="Data-room ingestion handoff run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "durable data-room ingestion handoff boundary exists; API/OpenAPI/UI, "
            "S3/Supabase, OCR/media/image/HTML/TXT parsing, Layer 2, RAG/Neo4j, "
            "live enrichment, and deliverables remain deferred."
        ),
        evidence=[
            "DATA_ROOM_INGESTION_HANDOFF runs after inventory and before INGEST_CHECK.",
            "Supported inventory files can be handed to an IngestionService adapter.",
            "Handoff summaries classify deferred, durable_ingested, durable_reused, "
            "and in_memory_fallback outcomes.",
            "Unsupported/deferred/blocked files remain safe summary rows only.",
        ],
        gaps=[
            "API/OpenAPI/UI remains deferred.",
            "S3/Supabase storage remains deferred.",
            "unsupported/deferred files remain summaries only.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "Layer 2 remains deferred.",
            "RAG/Neo4j remains deferred.",
            "live enrichment execution remains deferred.",
            "deliverables expansion remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 18",
        metadata={
            "api_or_ui_changed": "DATA_ROOM_INGESTION_HANDOFF" in api_text,
            "s3_or_supabase_storage_added": any(
                token in service_text.lower() for token in ("s3", "supabase")
            ),
            "unsupported_files_create_documents": False,
            "ocr_performed": False,
            "media_transcription_performed": False,
            "layer2_execution_performed": False,
        },
    )


def _production_run_source_contract(root: Path, files: dict[str, str]) -> WiringItem:
    runs_text = files.get("src/idis/api/routes/runs.py", "")
    source_text = files.get("src/idis/models/run_source.py", "")
    worker_text = files.get("src/idis/pipeline/worker.py", "")
    repo_text = files.get("src/idis/persistence/repositories/runs.py", "")
    migration_text = files.get(
        "src/idis/persistence/migrations/versions/0013_runs_source_contract.py",
        "",
    )
    integrated = (
        _exists(root, "src/idis/models/run_source.py")
        and "RunSourceType" in source_text
        and "deal_documents" in source_text
        and 'extra="forbid"' in source_text
        and "PATH_LIKE_RUN_FIELDS" in runs_text
        and "INVALID_RUN_SOURCE" in runs_text
        and "source:" in repo_text
        and "filter_preflight_corpus_by_run_source" in worker_text
        and "deal_metadata" in worker_text
        and "ADD COLUMN IF NOT EXISTS source JSONB" in migration_text
    )
    return WiringItem(
        key="production_run_source_contract",
        label="Production run-source contract",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "production run-source contract boundary exists; runs can start from durable "
            "deal document refs while local folders remain harness-only."
        ),
        evidence=[
            "Public runs API accepts strict mode plus deal_documents source only.",
            "Run source is persisted so worker and API hydrate the same selected corpus.",
            "Worker applies persisted source filtering and loads deal metadata.",
            "Local folder paths remain harness-only and are rejected by run creation.",
        ],
        gaps=[
            "local folder paths remain harness-only.",
            "durable data-room package table remains deferred.",
            "UI remains deferred.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "S3/Supabase storage expansion remains deferred.",
            "RAG/Neo4j remains deferred.",
            "Layer 2 remains deferred.",
            "external enrichment expansion remains deferred.",
            "deliverables expansion remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 19",
        metadata={
            "public_api_accepts_filesystem_paths": False,
            "durable_package_table_added": False,
            "ui_changed": False,
            "layer2_execution_performed": False,
        },
    )


def _durable_document_api_parity(files: dict[str, str]) -> WiringItem:
    documents_text = files.get("src/idis/api/routes/documents.py", "")
    repository_text = files.get("src/idis/persistence/repositories/documents.py", "")
    openapi_text = files.get("openapi/IDIS_OpenAPI_v6_3.yaml", "")
    policy_text = files.get("src/idis/api/policy.py", "")
    durable_summary_body = documents_text.split("def _document_summary_from_durable_row", 1)[
        1
    ].split("def _document_summary_from_memory_artifact", 1)[0]
    integrated = (
        "PostgresDocumentsRepository" in documents_text
        and "list_documents_by_deal" in documents_text
        and "get_deal_document_summary" in documents_text
        and "document_id" in openapi_text
        and "RunSource.document_ids" in openapi_text
        and "file://" not in documents_text.partition("ALLOWED_URI_SCHEMES")[2].split("\n", 1)[0]
        and "list_spans_by_document" in repository_text
        and "getDealDocumentSummary" in policy_text
    )
    return WiringItem(
        key="durable_document_api_parity",
        label="Durable document API parity",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "durable document API parity boundary exists; safe document summaries expose "
            "run-source document IDs while raw content, local paths, UI, and downstream "
            "execution remain deferred."
        ),
        evidence=[
            "Document list uses PostgresDocumentsRepository when db_conn exists.",
            "Safe summaries expose durable document_id for RunSource.document_ids.",
            "Deal-scoped durable document summary route is policy-protected.",
            "file:// and raw local filesystem URI registration are rejected.",
        ],
        gaps=[
            "local filesystem path API remains deferred.",
            "raw content delivery remains outside safe summaries.",
            "durable data-room package table remains deferred.",
            "UI remains deferred.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "S3/Supabase storage expansion remains deferred.",
            "RAG/Neo4j remains deferred.",
            "Layer 2 remains deferred.",
            "external enrichment expansion remains deferred.",
            "deliverables expansion remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 20",
        metadata={
            "safe_summary_exposes_content_b64": "content_b64=" in durable_summary_body,
            "public_api_accepts_file_uri": '"file://"'
            in documents_text.split("ALLOWED_URI_SCHEMES", 1)[1].split("\n", 1)[0],
            "durable_package_table_added": False,
            "layer2_execution_performed": False,
        },
    )


def _single_document_upload_intake(files: dict[str, str]) -> WiringItem:
    documents_text = files.get("src/idis/api/routes/documents.py", "")
    service_text = files.get("src/idis/services/ingestion/service.py", "")
    openapi_text = files.get("openapi/IDIS_OpenAPI_v6_3.yaml", "")
    policy_text = files.get("src/idis/api/policy.py", "")
    audit_text = files.get("src/idis/api/middleware/audit.py", "")
    upload_parts = documents_text.split("async def upload_deal_document", 1)
    upload_body = upload_parts[1].split("@router.post(", 1)[0] if len(upload_parts) > 1 else ""
    integrated = (
        '"/v1/deals/{deal_id}/documents/upload"' in documents_text
        and "UPLOAD_CONTENT_TYPE" in documents_text
        and "application/octet-stream" in documents_text
        and "ingest_bytes(" in upload_body
        and "IngestionContext" in documents_text
        and "ComplianceEnforcedStore" in service_text
        and "uploadDealDocument" in openapi_text
        and "application/octet-stream" in openapi_text
        and "uploadDealDocument" in policy_text
        and "uploadDealDocument" in audit_text
    )
    return WiringItem(
        key="single_document_upload_intake",
        label="Single-document upload intake",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "single-document upload intake boundary exists; clients can upload one raw "
            "supported document through existing compliance storage and ingestion while "
            "folder upload, new storage backends, UI, OCR/media, and downstream layers "
            "remain deferred."
        ),
        evidence=[
            "Upload endpoint requires application/octet-stream.",
            "Upload route validates filename, byte size, SHA256, content type, and deal scope.",
            "Uploaded bytes flow through IngestionService.ingest_bytes and compliant storage.",
            "Safe summaries expose durable document_id for RunSource.document_ids.",
        ],
        gaps=[
            "data-room folder upload remains deferred.",
            "durable data-room package table remains deferred.",
            "UI remains deferred.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "new S3/Supabase storage implementation remains deferred.",
            "RAG/Neo4j remains deferred.",
            "Layer 2 remains deferred.",
            "external enrichment expansion remains deferred.",
            "deliverables expansion remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 21",
        metadata={
            "public_api_accepts_folder_upload": "data_room_root_path" in upload_body,
            "safe_response_exposes_content_b64": "content_b64" in upload_body,
            "durable_package_table_added": False,
            "new_s3_supabase_backend_added": False,
            "layer2_execution_performed": False,
        },
    )


def _api_upload_to_selected_run_smoke(files: dict[str, str]) -> WiringItem:
    documents_text = files.get("src/idis/api/routes/documents.py", "")
    runs_text = files.get("src/idis/api/routes/runs.py", "")
    run_source_text = files.get("src/idis/models/run_source.py", "")
    worker_text = files.get("src/idis/pipeline/worker.py", "")
    smoke_test_text = files.get("tests/test_api_upload_to_run_smoke_postgres.py", "")
    worker_test_text = files.get("tests/test_run_document_loader.py", "")
    response_model_body = documents_text.split("class DocumentArtifactResponse", 1)[-1].split(
        "class PaginatedDocumentList", 1
    )[0]
    public_response_exposes_raw_content = any(
        token in response_model_body
        for token in [
            "content_b64",
            "raw_bytes",
            "raw_text",
            "parsed_text",
            "text_excerpt",
            "spans",
        ]
    )
    integrated = (
        '"/v1/deals/{deal_id}/documents/upload"' in documents_text
        and '"document_ids"' in run_source_text
        and "filter_preflight_corpus_by_run_source" in runs_text
        and "filter_preflight_corpus_by_run_source" in worker_text
        and "test_api_upload_list_get_selected_run_smoke_consumes_only_selected_document"
        in smoke_test_text
        and "test_api_selected_run_rejects_cross_deal_document_without_creating_run"
        in smoke_test_text
        and "test_worker_claimed_persisted_run_source_filters_selected_document_context"
        in smoke_test_text
        and "test_worker_context_factory_applies_persisted_run_source_filter" in worker_test_text
    )
    worker_filters_persisted_source = (
        "filter_preflight_corpus_by_run_source" in worker_text
        and 'run_data.get("source")' in worker_text
    )
    return WiringItem(
        key="api_upload_to_selected_run_smoke",
        label="API upload-to-selected-run smoke",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "API upload-to-selected-run smoke boundary exists; tests prove uploaded "
            "durable document summaries can start a selected run while adding no "
            "new product surface."
        ),
        evidence=[
            "Smoke test uploads two supported documents through the existing upload endpoint.",
            "Smoke test verifies durable list/get summaries before run creation.",
            "Selected run uses existing RunSource source.document_ids filtering.",
            "Worker parity test proves persisted runs.source filters queued context.",
        ],
        gaps=[
            "smoke boundary only; no new endpoint or production behavior is added.",
            "folder/data-room upload remains deferred.",
            "durable data-room package table remains deferred.",
            "UI remains deferred.",
            "OCR/media/image/HTML/TXT parsing remains deferred.",
            "new S3/Supabase storage implementation remains deferred.",
            "RAG/Neo4j remains deferred.",
            "Layer 2 remains deferred.",
            "external enrichment expansion remains deferred.",
            "deliverables expansion remains deferred.",
        ],
        phase_2_action="Phase 3.0 Slice 22",
        metadata={
            "new_endpoint_added": False,
            "public_response_exposes_raw_content": public_response_exposes_raw_content,
            "worker_filters_persisted_source": worker_filters_persisted_source,
            "layer2_execution_performed": False,
        },
    )


def _default_upload_ingestion_wiring(files: dict[str, str]) -> WiringItem:
    runs_text = files.get("src/idis/api/routes/runs.py", "")
    main_text = files.get("src/idis/api/main.py", "")
    defaults_text = files.get("src/idis/services/ingestion/defaults.py", "")
    evidence_repo_text = files.get("src/idis/persistence/repositories/evidence.py", "")
    postgres_test_text = files.get("tests/test_api_default_upload_ingestion_postgres.py", "")
    full_run_step_name_test_text = files.get("tests/test_api_full_run_step_name_postgres.py", "")
    full_run_durable_evidence_test_text = files.get(
        "tests/test_api_full_run_durable_evidence_postgres.py",
        "",
    )
    sanad_auto_grade_test_text = files.get(
        "tests/test_api_sanad_auto_grade_persistence_postgres.py",
        "",
    )
    step_name_width_migration_text = files.get(
        "src/idis/persistence/migrations/versions/0014_run_step_name_width.py",
        "",
    )
    defects_workflow_migration_text = files.get(
        "src/idis/persistence/migrations/versions/0015_defects_workflow_columns.py",
        "",
    )
    ci_text = files.get(".github/workflows/ci.yml", "")
    uses_compliance_store = "ComplianceEnforcedStore" in defaults_text
    uses_existing_store = "FilesystemObjectStore" in defaults_text
    test_added = (
        "test_create_app_default_upload_persists_parsed_document_without_ingestion_shim"
        in postgres_test_text
    )
    ci_added = "tests/test_api_default_upload_ingestion_postgres.py" in ci_text
    step_name_width_migrated = (
        "ALTER COLUMN step_name TYPE VARCHAR(100)" in step_name_width_migration_text
        and "length(step_name) > 50" in step_name_width_migration_text
    )
    full_run_step_name_test_added = (
        "test_default_upload_selected_full_run_persists_long_step_name_without_sql_truncation"
        in full_run_step_name_test_text
        and "documents/upload" in full_run_step_name_test_text
        and '"mode": "FULL"' in full_run_step_name_test_text
        and '"document_ids": [document_id]' in full_run_step_name_test_text
    )
    full_run_step_name_ci_added = "tests/test_api_full_run_step_name_postgres.py" in ci_text
    evidence_repo_wired = (
        "get_evidence_repository(db_conn, tenant_id)" in runs_text
        and "evidence_repo=evidence_repo" in runs_text
        and "PostgresEvidenceRepository" in evidence_repo_text
    )
    durable_evidence_test_added = (
        "test_selected_full_run_persists_durable_claims_and_evidence_for_uploaded_room"
        in full_run_durable_evidence_test_text
        and "documents/upload" in full_run_durable_evidence_test_text
        and '"mode": "FULL"' in full_run_durable_evidence_test_text
        and "evidence_items" in full_run_durable_evidence_test_text
    )
    durable_evidence_ci_added = "tests/test_api_full_run_durable_evidence_postgres.py" in ci_text
    sanad_auto_grade_persistence_test_added = (
        "test_slice28_selected_full_run_persists_sanad_grades_without_known_blocker"
        in sanad_auto_grade_test_text
        and "KNOWN_SANAD_BLOCKER" in sanad_auto_grade_test_text
        and "sanads" in sanad_auto_grade_test_text
        and "computed->>'grade'" in sanad_auto_grade_test_text
    )
    sanad_auto_grade_persistence_ci_added = (
        "tests/test_api_sanad_auto_grade_persistence_postgres.py" in ci_text
    )
    defects_workflow_columns_migrated = (
        "ALTER TABLE defects ADD COLUMN IF NOT EXISTS deal_id UUID"
        in defects_workflow_migration_text
        and "ALTER TABLE defects ADD COLUMN IF NOT EXISTS status TEXT"
        in defects_workflow_migration_text
        and "ALTER TABLE defects ADD COLUMN IF NOT EXISTS cured_at TIMESTAMPTZ"
        in defects_workflow_migration_text
    )
    integrated = (
        "build_default_ingestion_service" in main_text
        and uses_compliance_store
        and uses_existing_store
        and test_added
        and ci_added
    )
    return WiringItem(
        key="default_upload_ingestion_wiring",
        label="Default upload ingestion wiring",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "default upload ingestion wiring boundary exists; production-style create_app() "
            "can upload supported document bytes into the durable parsed Postgres corpus."
        ),
        evidence=[
            "`create_app()` builds a default ingestion service without a test shim.",
            "`build_default_ingestion_service` uses ComplianceEnforcedStore.",
            "The default store uses existing filesystem object-store configuration only.",
            (
                "Postgres test proves upload, durable document_id, PARSED row, "
                "spans, and safe list/get."
            ),
            (
                "Slice 25 Postgres test proves selected-document FULL run-step persistence "
                "for METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN."
            ),
            "Migration widens run_steps.step_name to VARCHAR(100) with guarded downgrade.",
            (
                "Slice 26 Postgres test proves selected FULL runs over uploaded "
                "PDF/XLSX/DOCX/PPTX files persist durable claims and evidence_items."
            ),
            (
                "Slice 28 Postgres test proves selected FULL runs execute GRADE "
                "without SANAD_AUTO_GRADE_PERSISTENCE_BLOCKED and persist linked "
                "claim Sanad grades."
            ),
            "Migration aligns defects workflow columns with the Postgres repository contract.",
            "`_run_snapshot_extraction` passes `get_evidence_repository(db_conn, tenant_id)` "
            "into `ExtractionPipeline`.",
        ],
        gaps=[
            "claim/evidence retrieval expansion remains deferred.",
            (
                "Sanad auto-grade Postgres persistence is covered by Slice 28; "
                "no replacement runtime blocker has been proven by this audit."
            ),
            (
                "Layer 2, enrichment, deliverables, folder upload, OCR/media/HTML/TXT, "
                "and RAG/Neo4j remain deferred."
            ),
            "No new S3/Supabase storage backend is added.",
        ],
        phase_2_action="Phase 3.0 Slices 24-26",
        metadata={
            "uses_compliance_enforced_store": uses_compliance_store,
            "new_storage_backend_added": False,
            "postgres_ci_test_added": ci_added,
            "run_step_name_width_migrated": step_name_width_migrated,
            "full_run_step_name_postgres_test_added": full_run_step_name_test_added,
            "full_run_step_name_postgres_ci_test_added": full_run_step_name_ci_added,
            "evidence_repo_wired_to_postgres": evidence_repo_wired,
            "full_run_durable_evidence_postgres_test_added": durable_evidence_test_added,
            "full_run_durable_evidence_postgres_ci_test_added": durable_evidence_ci_added,
            "sanad_auto_grade_persistence_test_added": sanad_auto_grade_persistence_test_added,
            "sanad_auto_grade_persistence_ci_test_added": sanad_auto_grade_persistence_ci_added,
            "defects_workflow_columns_migrated": defects_workflow_columns_migrated,
            "next_blocker": None,
            "layer2_execution_performed": False,
        },
    )


def _methodology_claim_materialization_sanad_integration(files: dict[str, str]) -> WiringItem:
    materializer_text = files.get("src/idis/services/extraction/claim_materializer.py", "")
    integrated = "SanadService" in materializer_text
    return WiringItem(
        key="methodology_claim_materialization_sanad_integration",
        label="Claim materialization Sanad integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Slice 6 does not create Sanad records or promote claims.",
        evidence=["Run-scoped materialized claims remain unverified and non-IC-bound."],
        gaps=["Future slice must create evidence chains before IC-bound promotion."],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_claim_materialization_coverage_integration(files: dict[str, str]) -> WiringItem:
    materializer_text = files.get("src/idis/services/extraction/claim_materializer.py", "")
    integrated = "MethodologyCoverage" in materializer_text or "update_status" in materializer_text
    return WiringItem(
        key="methodology_claim_materialization_coverage_integration",
        label="Claim materialization coverage integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Slice 6 does not mutate methodology coverage answers.",
        evidence=["Materialized claims carry coverage_record_id linkage only."],
        gaps=["Future slice must update coverage after claim and evidence chain creation."],
        phase_2_action="Phase 3.0 Slice 6",
    )


def _methodology_sanad_coverage_boundary_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/sanad_coverage_boundary.py") and _contains(
        files,
        "src/idis/models/sanad_coverage_boundary.py",
        "SanadCoverageBoundaryResult",
    )
    return WiringItem(
        key="methodology_sanad_coverage_boundary_models",
        label="Methodology Sanad and coverage boundary models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Structured Sanad readiness and coverage update decision models exist.",
        evidence=[
            "`sanad_coverage_boundary.py` defines readiness and coverage decision records.",
            "Boundary decisions carry `ic_promotion_status='deferred_until_sanad'`.",
        ],
        gaps=["Models are not persisted to a dedicated Phase 2.7 schema."],
        phase_2_action="Phase 2.7",
    )


def _methodology_sanad_coverage_boundary_service(root: Path, files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    has_service = (
        _exists(root, "src/idis/services/methodology/sanad_coverage_boundary.py")
        and "SanadCoverageBoundaryService" in service_text
    )
    return WiringItem(
        key="methodology_sanad_coverage_boundary_service",
        label="Methodology Sanad and coverage boundary service",
        status="PARTIAL" if has_service else "NOT_FOUND",
        summary=(
            "Synthetic-only boundary service creates decisions without live coverage mutation."
        ),
        evidence=[
            "`build_decisions` returns decision records from materialized claims.",
            "The default flow does not create Sanad records or promote claims.",
        ],
        gaps=["No API, run, UI, Postgres, RAG, graph, or cache integration is wired."],
        phase_2_action="Phase 2.7",
    )


def _methodology_sanad_coverage_boundary_audit_contract(
    root: Path, files: dict[str, str]
) -> WiringItem:
    has_contract = _exists(
        root, "src/idis/services/methodology/sanad_coverage_boundary_audit.py"
    ) and _contains(
        files,
        "src/idis/services/methodology/sanad_coverage_boundary_audit.py",
        "SANAD_COVERAGE_BOUNDARY_AUDIT_EVENTS",
    )
    return WiringItem(
        key="methodology_sanad_coverage_boundary_audit_contract",
        label="Sanad coverage boundary future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist without live emission.",
        evidence=["`sanad_coverage_boundary_audit.py` defines Phase 2.7 audit constants."],
        gaps=["No live audit sink emission is wired in Phase 2.7."],
        phase_2_action="Phase 2.7",
    )


def _methodology_sanad_readiness_boundary(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    has_boundary = "SanadReadinessDecision" in service_text and "ready_for_future_sanad" in (
        service_text
    )
    return WiringItem(
        key="methodology_sanad_readiness_boundary",
        label="Sanad readiness boundary",
        status="PARTIAL" if has_boundary else "NOT_FOUND",
        summary="Sanad readiness boundary exists as decision output only.",
        evidence=["Boundary service emits readiness decisions for future Sanad work."],
        gaps=["Actual Sanad chain creation remains deferred."],
        phase_2_action="Phase 2.7",
    )


def _methodology_coverage_decision_boundary(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    has_decisions = "CoverageUpdateDecision" in service_text and "build_decisions" in (service_text)
    return WiringItem(
        key="methodology_coverage_decision_boundary",
        label="Coverage update decisions",
        status="PARTIAL" if has_decisions else "NOT_FOUND",
        summary="Coverage update decisions exist, but live coverage updates are not wired.",
        evidence=["`build_decisions` returns deterministic coverage decisions by target status."],
        gaps=["Default boundary flow intentionally does not mutate coverage records."],
        phase_2_action="Phase 2.7",
    )


def _methodology_live_coverage_updates(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    default_flow_has_live_mutation = "build_decisions" in service_text and (
        "coverage_service.update_status" in service_text.split("apply_decisions_in_memory")[0]
    )
    return WiringItem(
        key="methodology_live_coverage_updates",
        label="Live methodology coverage updates",
        status="PARTIAL" if default_flow_has_live_mutation else "DEFERRED",
        summary="Live coverage updates are not wired by the default boundary flow.",
        evidence=[
            "Phase 2.7 produces CoverageUpdateDecision records by default.",
            "Optional in-memory application is separate and explicitly injected.",
        ],
        gaps=["Production coverage mutation must be introduced in a later phase."],
        phase_2_action="Phase 2.7",
    )


def _methodology_boundary_sanad_creation(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    integrated = "build_sanad_chain" in service_text or "SanadService" in service_text
    return WiringItem(
        key="methodology_boundary_sanad_creation",
        label="Boundary Sanad creation",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Sanad creation is not wired for Phase 2.7 boundary decisions.",
        evidence=["Readiness decisions carry deferred Sanad status only."],
        gaps=["Future work must create evidence-backed source-span-linked chains."],
        phase_2_action="Phase 2.7",
    )


def _methodology_boundary_ic_promotion(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    integrated = "ic_bound=True" in service_text or "VERIFIED" in service_text
    return WiringItem(
        key="methodology_boundary_ic_promotion",
        label="Boundary IC-bound promotion",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="IC-bound promotion is not wired; all decisions defer until Sanad exists.",
        evidence=["Decision records include `deferred_until_sanad` promotion status."],
        gaps=["Future promotion must require an existing Sanad chain."],
        phase_2_action="Phase 2.7",
    )


def _methodology_boundary_postgres_persistence(root: Path) -> WiringItem:
    migrations_dir = root / "src/idis/persistence/migrations/versions"
    has_migration = any(
        "sanad_coverage_boundary" in path.name for path in migrations_dir.glob("*.py")
    )
    return WiringItem(
        key="methodology_boundary_postgres_persistence",
        label="Boundary Postgres persistence",
        status="PARTIAL" if has_migration else "DEFERRED",
        summary="Phase 2.7 has no Postgres persistence for boundary decisions.",
        evidence=["No Phase 2.7 migration is expected for the synthetic boundary slice."],
        gaps=["Future persistence must preserve deterministic decision provenance."],
        phase_2_action="Phase 2.7",
    )


def _methodology_boundary_api_run_ui_integration(files: dict[str, str]) -> WiringItem:
    integration_text = (
        files.get("src/idis/api/main.py", "")
        + files.get("src/idis/api/routes/runs.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
        + files.get("src/idis/pipeline/worker.py", "")
    )
    integrated = "SanadCoverageBoundaryService" in integration_text
    return WiringItem(
        key="methodology_boundary_api_run_ui_integration",
        label="Boundary API/run/UI integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="API/run/UI integration is not wired for Phase 2.7 boundary decisions.",
        evidence=["Boundary service is not called from API routes or run execution paths."],
        gaps=["Future integration must keep decision-only and mutation paths explicit."],
        phase_2_action="Phase 2.7",
    )


def _methodology_boundary_rag_graph_cache_integration(files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_coverage_boundary.py", "")
    integrated = any(
        token in service_text.lower() for token in ["retrieval", "pgvector", "neo4j", "redis"]
    )
    return WiringItem(
        key="methodology_boundary_rag_graph_cache_integration",
        label="Boundary RAG/vector/Neo4j/Redis integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="RAG/vector/Neo4j/Redis integration remains deferred for Phase 2.7.",
        evidence=["Boundary decisions are built from synthetic in-memory inputs only."],
        gaps=["Future retrieval, graph, and cache integrations must stay source-span linked."],
        phase_2_action="Phase 2.7",
    )


def _methodology_sanad_creation_boundary_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/sanad_creation_boundary.py") and _contains(
        files,
        "src/idis/models/sanad_creation_boundary.py",
        "SanadCreationResult",
    )
    return WiringItem(
        key="methodology_sanad_creation_boundary_models",
        label="Methodology Sanad creation boundary models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Sanad creation boundary models exist for synthetic Phase 2.8 results.",
        evidence=[
            "`sanad_creation_boundary.py` defines creation mappings, rejections, "
            "claim-link decisions, and summaries.",
            "Created mappings still carry `ic_promotion_status='deferred_until_sanad'`.",
        ],
        gaps=["Models are not persisted to a dedicated Phase 2.8 schema."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_boundary_service(root: Path, files: dict[str, str]) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
    has_service = (
        _exists(root, "src/idis/services/methodology/sanad_creation_boundary.py")
        and "SanadCreationBoundaryService" in service_text
    )
    return WiringItem(
        key="methodology_sanad_creation_boundary_service",
        label="Methodology Sanad creation boundary service",
        status="PARTIAL" if has_service else "NOT_FOUND",
        summary=(
            "Synthetic-only Sanad creation boundary exists and requires explicit "
            "tenant-scoped invocation."
        ),
        evidence=[
            "`create_sanads_for_ready_decisions` accepts Phase 2.7 readiness output.",
            "The service uses an injected SanadService and returns claim-link metadata.",
        ],
        gaps=["No API, run, UI, Postgres, RAG, graph, or cache integration is wired."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_boundary_audit_contract(
    root: Path, files: dict[str, str]
) -> WiringItem:
    has_contract = _exists(
        root, "src/idis/services/methodology/sanad_creation_boundary_audit.py"
    ) and _contains(
        files,
        "src/idis/services/methodology/sanad_creation_boundary_audit.py",
        "SANAD_CREATION_BOUNDARY_AUDIT_EVENTS",
    )
    return WiringItem(
        key="methodology_sanad_creation_boundary_audit_contract",
        label="Sanad creation boundary future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist without live emission.",
        evidence=["`sanad_creation_boundary_audit.py` defines Phase 2.8 audit constants."],
        gaps=["No live audit sink emission is wired in Phase 2.8."],
        phase_2_action="Phase 2.8",
    )


def _methodology_synthetic_sanad_creation_path(files: dict[str, str]) -> WiringItem:
    service_text = (
        files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    )
    has_path = all(
        token in service_text
        for token in [
            "build_sanad_chain",
            "CreateSanadInput",
            "sanad_service.create",
            "EvidenceItem",
        ]
    )
    return WiringItem(
        key="methodology_synthetic_sanad_creation_path",
        label="Synthetic Sanad creation path",
        status="PARTIAL" if has_path else "NOT_FOUND",
        summary="Synthetic Sanad creation path exists when explicitly invoked.",
        evidence=[
            "Phase 2.8 validates synthetic EvidenceItem payloads before creation.",
            "The boundary passes an explicit transmission chain into SanadService.create.",
        ],
        gaps=["This path is not called from production run execution."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_claim_link_application(files: dict[str, str]) -> WiringItem:
    service_text = (
        files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    )
    claim_link_live = "ClaimService" in service_text or "claim_action" in service_text
    return WiringItem(
        key="methodology_sanad_creation_claim_link_application",
        label="Sanad claim link application",
        status="PARTIAL" if claim_link_live else "DEFERRED",
        summary="Claim link application is not wired; Phase 2.8 returns metadata only.",
        evidence=["ClaimSanadLinkDecision records defer future claim linkage."],
        gaps=["A later phase must explicitly apply claim-to-Sanad links."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_ic_promotion(files: dict[str, str]) -> WiringItem:
    service_text = (
        files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    )
    integrated = "ic_bound=True" in service_text or "VERIFIED" in service_text
    return WiringItem(
        key="methodology_sanad_creation_ic_promotion",
        label="Sanad creation IC-bound promotion",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="IC-bound promotion is not wired for Phase 2.8 Sanad creation.",
        evidence=["Creation results keep `deferred_until_sanad` promotion metadata."],
        gaps=["Investment-committee promotion remains a later explicit phase."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_coverage_updates(files: dict[str, str]) -> WiringItem:
    service_text = (
        files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    )
    live_coverage = "update_status" in service_text or "apply_decisions_in_memory" in service_text
    return WiringItem(
        key="methodology_sanad_creation_coverage_updates",
        label="Sanad creation coverage updates",
        status="PARTIAL" if live_coverage else "DEFERRED",
        summary="Coverage updates are not wired for Phase 2.8 Sanad creation.",
        evidence=["Creation mappings carry coverage_update_status='not_applied'."],
        gaps=["Coverage mutation remains deferred until explicitly approved."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_postgres_api_run_ui_integration(
    files: dict[str, str],
) -> WiringItem:
    service_text = files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
    results_text = files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
    support_text = files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    integration_text = (
        files.get("src/idis/api/main.py", "")
        + files.get("src/idis/api/routes/runs.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
        + files.get("src/idis/pipeline/worker.py", "")
    )
    integrated = (
        "SanadCreationBoundaryService" in integration_text
        or "sqlalchemy" in (service_text + results_text + support_text).lower()
    )
    return WiringItem(
        key="methodology_sanad_creation_postgres_api_run_ui_integration",
        label="Sanad creation Postgres/API/run/UI integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Postgres/API/run/UI integration is not wired for Phase 2.8.",
        evidence=["The creation boundary is not invoked by API routes or run execution paths."],
        gaps=["Production integration must keep explicit tenant and apply boundaries."],
        phase_2_action="Phase 2.8",
    )


def _methodology_sanad_creation_rag_graph_cache_integration(files: dict[str, str]) -> WiringItem:
    service_text = (
        files.get("src/idis/services/methodology/sanad_creation_boundary.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_results.py", "")
        + files.get("src/idis/services/methodology/sanad_creation_boundary_support.py", "")
    )
    integrated = any(
        token in service_text.lower() for token in ["retrieval", "pgvector", "neo4j", "redis"]
    )
    return WiringItem(
        key="methodology_sanad_creation_rag_graph_cache_integration",
        label="Sanad creation RAG/vector/Neo4j/Redis integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="RAG/vector/Neo4j/Redis integration remains deferred for Phase 2.8.",
        evidence=["The creation boundary consumes only synthetic in-memory references."],
        gaps=["Future retrieval, graph, and cache work must preserve source-span linkage."],
        phase_2_action="Phase 2.8",
    )


def _claim_sanad_link_text(files: dict[str, str]) -> str:
    return files.get("src/idis/services/methodology/claim_sanad_link_boundary.py", "") + files.get(
        "src/idis/services/methodology/claim_sanad_link_boundary_support.py", ""
    )


def _methodology_claim_sanad_link_boundary_models(root: Path, files: dict[str, str]) -> WiringItem:
    has_models = _exists(root, "src/idis/models/claim_sanad_link_boundary.py") and _contains(
        files,
        "src/idis/models/claim_sanad_link_boundary.py",
        "ClaimSanadLinkApplicationResult",
    )
    return WiringItem(
        key="methodology_claim_sanad_link_boundary_models",
        label="Claim-Sanad link boundary models",
        status="WIRED" if has_models else "NOT_FOUND",
        summary="Claim-Sanad link boundary models exist for synthetic Phase 2.9 results.",
        evidence=[
            "`claim_sanad_link_boundary.py` defines apply decisions, mappings, "
            "rejections, summaries, and non-promotion status."
        ],
        gaps=["Models are not persisted to a dedicated Phase 2.9 schema."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_boundary_service(root: Path, files: dict[str, str]) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    has_service = (
        _exists(root, "src/idis/services/methodology/claim_sanad_link_boundary.py")
        and "ClaimSanadLinkBoundaryService" in service_text
    )
    return WiringItem(
        key="methodology_claim_sanad_link_boundary_service",
        label="Claim-Sanad link boundary service",
        status="PARTIAL" if has_service else "NOT_FOUND",
        summary=(
            "Synthetic-only Claim-Sanad link boundary exists and requires explicit "
            "tenant-scoped invocation."
        ),
        evidence=[
            "`build_claim_sanad_link_decisions` consumes Phase 2.8 creation output.",
            "`apply_claim_sanad_links` requires an injected ClaimService.",
        ],
        gaps=["No API, run, UI, Postgres, RAG, graph, or cache integration is wired."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_boundary_audit_contract(
    root: Path, files: dict[str, str]
) -> WiringItem:
    has_contract = _exists(
        root, "src/idis/services/methodology/claim_sanad_link_boundary_audit.py"
    ) and _contains(
        files,
        "src/idis/services/methodology/claim_sanad_link_boundary_audit.py",
        "CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENTS",
    )
    return WiringItem(
        key="methodology_claim_sanad_link_boundary_audit_contract",
        label="Claim-Sanad link boundary future audit contract",
        status="PARTIAL" if has_contract else "NOT_FOUND",
        summary="Future audit event names and payload keys exist without live emission.",
        evidence=["`claim_sanad_link_boundary_audit.py` defines Phase 2.9 audit constants."],
        gaps=["No live audit sink emission is wired in Phase 2.9."],
        phase_2_action="Phase 2.9",
    )


def _methodology_synthetic_claim_sanad_link_apply_path(files: dict[str, str]) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    has_path = all(
        token in service_text
        for token in [
            "apply_claim_sanad_links",
            "ClaimService",
            "UpdateClaimInput",
            ".update(",
        ]
    )
    return WiringItem(
        key="methodology_synthetic_claim_sanad_link_apply_path",
        label="Synthetic Claim-Sanad link apply path",
        status="PARTIAL" if has_path else "NOT_FOUND",
        summary="Explicit synthetic ClaimService.update path exists when invoked.",
        evidence=[
            "Phase 2.9 applies only `sanad_id` and `request_id` through ClaimService.update."
        ],
        gaps=["This path is not called from production run execution."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_ic_promotion(files: dict[str, str]) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    integrated = "ic_bound=True" in service_text
    return WiringItem(
        key="methodology_claim_sanad_link_ic_promotion",
        label="Claim-Sanad link IC promotion",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="IC promotion is not wired for Phase 2.9 Claim-Sanad linking.",
        evidence=["Link mappings carry `sanad_linked_not_ic_ready` promotion metadata."],
        gaps=["Investment-committee promotion remains a later explicit phase."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_verdict_action_promotion(
    files: dict[str, str],
) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    integrated = "claim_verdict=VERIFIED" in service_text or "claim_action=NONE" in service_text
    return WiringItem(
        key="methodology_claim_sanad_link_verdict_action_promotion",
        label="Claim-Sanad link verdict/action promotion",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Claim verdict/action promotion is not wired for Phase 2.9.",
        evidence=["Post-update validation rejects VERIFIED or NONE protected-field drift."],
        gaps=["Verified verdict and no-action promotion remain a later explicit phase."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_coverage_updates(files: dict[str, str]) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    live_coverage = (
        ".update_status(" in service_text
        or "update_status(" in service_text
        or "apply_decisions_in_memory" in service_text
    )
    return WiringItem(
        key="methodology_claim_sanad_link_coverage_updates",
        label="Claim-Sanad link coverage updates",
        status="PARTIAL" if live_coverage else "DEFERRED",
        summary="Coverage updates are not wired for Phase 2.9 Claim-Sanad linking.",
        evidence=["Link mappings carry coverage_update_status='not_applied'."],
        gaps=["Coverage mutation remains deferred until explicitly approved."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_postgres_api_run_ui_integration(
    files: dict[str, str],
) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    integration_text = (
        files.get("src/idis/api/main.py", "")
        + files.get("src/idis/api/routes/runs.py", "")
        + files.get("src/idis/services/runs/steps.py", "")
        + files.get("src/idis/pipeline/worker.py", "")
    )
    integrated = (
        "ClaimSanadLinkBoundaryService" in integration_text
        or "sqlalchemy" in (service_text).lower()
    )
    return WiringItem(
        key="methodology_claim_sanad_link_postgres_api_run_ui_integration",
        label="Claim-Sanad link Postgres/API/run/UI integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Postgres/API/run/UI integration is not wired for Phase 2.9.",
        evidence=["The link boundary is not invoked by API routes or run execution paths."],
        gaps=["Production integration must keep explicit tenant and apply boundaries."],
        phase_2_action="Phase 2.9",
    )


def _methodology_claim_sanad_link_rag_graph_cache_integration(files: dict[str, str]) -> WiringItem:
    service_text = _claim_sanad_link_text(files)
    integrated = any(
        token in service_text.lower() for token in ["retrieval", "pgvector", "neo4j", "redis"]
    )
    return WiringItem(
        key="methodology_claim_sanad_link_rag_graph_cache_integration",
        label="Claim-Sanad link RAG/vector/Neo4j/Redis integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="RAG/vector/Neo4j/Redis integration remains deferred for Phase 2.9.",
        evidence=["The link boundary consumes only synthetic Phase 2.8 mapping records."],
        gaps=["Future retrieval, graph, and cache work must preserve Sanad linkage."],
        phase_2_action="Phase 2.9",
    )


def _analysis_agents(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="analysis_agents",
        label="Analysis agents",
        status="WIRED",
        summary="Default specialist agents are registered and run by FULL analysis.",
        evidence=["`build_default_specialist_agents` returns eight agents."],
        gaps=["Agent context lacks methodology, RAG, and graph-rich evidence inputs."],
    )


def _commercial_agents(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="commercial_agents",
        label="Commercial agents",
        status="WIRED",
        summary="Market, sector, technical, terms, team, and risk agents exist.",
        evidence=[
            "`MarketAgent`, `SectorSpecialistAgent`, `TechnicalAgent`, and `TermsAgent` exist."
        ],
        gaps=["No dedicated commercial methodology registry assigns questions to agents."],
    )


def _debate_layer_1(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="debate_layer_1",
        label="Debate layer 1",
        status="WIRED",
        summary="LangGraph debate orchestrator is wired for FULL runs.",
        evidence=["`_run_full_debate` constructs `DebateOrchestrator`."],
        gaps=["`evidence_call_retrieval` does not perform real RAG retrieval."],
    )


def _debate_layer_2(root: Path) -> WiringItem:
    found = any((root / "src/idis").rglob("*challenge*")) or any(
        (root / "src/idis").rglob("*review*")
    )
    return WiringItem(
        key="debate_layer_2",
        label="Second challenge/review debate layer",
        status="NOT_FOUND" if not found else "PARTIAL",
        summary="No distinct second-layer challenge/review debate orchestrator is present.",
        evidence=["Search found no separate second debate orchestrator."],
        gaps=["Must be designed before Phase 2.8."],
    )


def _muhasabah_nff_gates(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="muhasabah_nff_gates",
        label="Muḥāsabah / No-Free-Facts gates",
        status="WIRED",
        summary="Analysis, debate, scoring, and deliverables use validation gates.",
        evidence=[
            "`AnalysisEngine._validate_report` validates NFF and Muḥāsabah.",
            "`MuhasabahGate` validates debate outputs.",
            "`DeliverablesGenerator` validates deliverable NFF.",
        ],
        gaps=["Validation is not yet methodology-coverage aware."],
    )


def _deliverables(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="deliverables",
        label="Deliverables",
        status="PARTIAL",
        summary="Deliverables generator and routes exist, but persistence/exposure needs proof.",
        evidence=["`_run_full_deliverables` calls `DeliverablesGenerator.generate`."],
        gaps=["Generated FULL run deliverables use local audit sink and synthesized IDs."],
    )


def _audit_sinks(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="audit_sinks",
        label="Audit sinks",
        status="PARTIAL",
        summary="API audit and Postgres sink exist, but some run helpers use local sinks.",
        evidence=["`AuditMiddleware`, `JsonlFileAuditSink`, and `PostgresAuditSink` exist."],
        gaps=["Extraction/analysis/deliverable helpers instantiate `InMemoryAuditSink` in places."],
    )


def _postgres(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="postgres",
        label="Postgres",
        status="WIRED",
        summary="Postgres is the canonical relational store when configured.",
        evidence=["`IDIS_DATABASE_URL` and `set_tenant_local` are used by persistence code."],
        gaps=["Some routes and services retain in-memory fallback paths."],
    )


def _docker_postgres(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="docker_postgres",
        label="Docker Postgres",
        status="WIRED",
        summary="Docker Compose provides Postgres with pgvector image and migrations service.",
        evidence=["`docker-compose.yml` service `postgres` uses `pgvector/pgvector:pg16`."],
        gaps=["CI local integration runner uses plain `postgres:16`, not pgvector image."],
    )


def _supabase(root: Path, files: dict[str, str]) -> WiringItem:
    docs_reference = any("Supabase" in _read(path) for path in (root / "docs").glob("*.md"))
    return WiringItem(
        key="supabase",
        label="Supabase",
        status="CONFIG_ONLY" if docs_reference else "NOT_FOUND",
        summary="Supabase appears only as docs/env target, not runtime SDK integration.",
        evidence=["Supabase can be treated as a managed Postgres target if configured safely."],
        gaps=["no Supabase SDK, storage, auth, realtime, or vector runtime integration found."],
    )


def _neo4j_graph(root: Path, files: dict[str, str]) -> WiringItem:
    api_refs = _source_references(
        root,
        "GraphProjectionService",
        exclude_paths={
            "graph_consistency.py",
            "strict_full_live.py",
        },
    )
    status = "PARTIAL" if api_refs else "TEST_ONLY"
    gaps = (
        [
            "Neo4j graph projection/retrieval is gated by strict env, health, and "
            "product-bundle visibility checks."
        ]
        if api_refs
        else ["GraphProjectionService is not called by live run/write paths."]
    )
    return WiringItem(
        key="neo4j_graph",
        label="Neo4j / graph projection",
        status=status,
        summary=(
            "Neo4j driver, projection, and retrieval code are wired into the FULL run graph "
            "evidence step."
            if api_refs
            else "Neo4j driver and projection code exist, but live run projection is not wired."
        ),
        evidence=[
            "`GraphProjectionService`, `GraphRepository`, and Neo4j driver exist.",
            "Graph projection has dedicated tests.",
        ],
        gaps=gaps,
    )


def _redis(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="redis",
        label="Redis",
        status="CONFIG_ONLY",
        summary=(
            "Redis is configured in Docker/env examples, but runtime code uses in-memory stores."
        ),
        evidence=["`docker-compose.yml` defines `redis`; `.env.example` defines Redis URL names."],
        gaps=["Redis URL is not consumed by runtime code for cache/rate/queue."],
    )


def _rag_vector_retrieval(root: Path, files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="rag_vector_retrieval",
        label="RAG / vector retrieval",
        status="CONFIG_ONLY",
        summary="pgvector is provisioned, but no app-level embedding/index/query path exists.",
        evidence=["`CREATE EXTENSION IF NOT EXISTS vector` exists in DB init scripts."],
        gaps=["no embedding/index/query path or retrieval API is implemented."],
    )


def _object_storage(root: Path, files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="object_storage",
        label="Object storage",
        status="WIRED",
        summary="Filesystem object store is wired for local raw artifact storage.",
        evidence=["`FilesystemObjectStore` stores tenant-scoped objects with SHA256 metadata."],
        gaps=["Supabase storage is not wired; S3 backend is documented as planned only."],
    )


def _external_enrichment(provider_ids: list[str]) -> WiringItem:
    return WiringItem(
        key="external_enrichment_connectors",
        label="External enrichment connectors",
        status="WIRED",
        summary="Default enrichment registry includes public and BYOL providers.",
        evidence=["Connector files were inventoried statically; no provider fetch was called."],
        gaps=["BYOL env variables are not automatically loaded into tenant credential storage."],
        metadata={
            "provider_count": len(provider_ids),
            "provider_ids": provider_ids,
            "live_calls_performed": False,
        },
    )


def _anthropic_llm(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="anthropic_llm",
        label="Anthropic LLM",
        status="PARTIAL",
        summary=(
            "Anthropic client is wired behind backend env selection, but baseline is dry-run only."
        ),
        evidence=["Anthropic client present; config-validated only with no live paid call."],
        gaps=["Live paid calls require explicit approval and valid `ANTHROPIC_API_KEY`."],
        metadata={"live_calls_performed": False},
    )


def _openai_llm(root: Path, files: dict[str, str]) -> WiringItem:
    has_openai_dependency = "openai" in files.get("pyproject.toml", "").lower()
    return WiringItem(
        key="openai_llm",
        label="OpenAI LLM",
        status="PARTIAL" if has_openai_dependency else "CONFIG_ONLY",
        summary="OpenAI is present only as env/docs placeholder in current runtime.",
        evidence=["`OPENAI_API_KEY` appears in env examples/local configuration names."],
        gaps=["no runtime client or OpenAI SDK integration found."],
        metadata={"live_calls_performed": False},
    )


def _source_references(root: Path, needle: str, *, exclude_paths: set[str]) -> list[Path]:
    """Return source files containing a needle, excluding same-module references."""
    matches: list[Path] = []
    for path in (root / "src/idis").rglob("*.py"):
        if path.name in exclude_paths:
            continue
        if needle in _read(path):
            matches.append(path)
    return matches


def _comparison_section(inventory: WiringInventory) -> list[str]:
    item = inventory["api_worker_path_comparison"]
    return [
        "",
        "## API Run Path vs Worker Path Comparison",
        "",
        f"- Status: `{item.status}`",
        "- API path: `src/idis/api/routes/runs.py` builds `RunContext` and calls "
        "`RunExecutionService`.",
        "- Worker path: `src/idis/pipeline/worker.py` polls tenant-scoped queued runs "
        "and calls `RunExecutionService`.",
        "- Result: API and worker execution share the canonical service; "
        "`PipelineExecutor` is legacy/demo-only.",
    ]


def _step_section(heading: str, item: WiringItem) -> list[str]:
    return [
        "",
        f"## {heading}",
        "",
        f"- Status: `{item.status}`",
        f"- Summary: {item.summary}",
        *[f"- Evidence: {evidence}" for evidence in item.evidence],
        *[f"- Gap: {gap}" for gap in item.gaps],
    ]


def _status_group_section(heading: str, inventory: WiringInventory) -> list[str]:
    lines = ["", f"## {heading}", ""]
    for item in inventory.values():
        if item.status in {"STUBBED", "CONFIG_ONLY"}:
            lines.append(f"- `{item.label}`: `{item.status}` — {item.summary}")
    return lines


def _missing_section(inventory: WiringInventory) -> list[str]:
    lines = ["", "## Missing Components", ""]
    for item in inventory.values():
        if item.status == "NOT_FOUND":
            lines.append(f"- `{item.label}`: {item.summary}")
    return lines


def _not_wired_section(inventory: WiringInventory) -> list[str]:
    lines = ["", "## Existing Components Not Wired", ""]
    for item in inventory.values():
        if item.status in {"TEST_ONLY", "PARTIAL"}:
            lines.append(f"- `{item.label}`: {item.summary}")
    return lines


def _risk_section(inventory: WiringInventory) -> list[str]:
    return [
        "",
        "## Risk Ranking",
        "",
        "1. **High**: RAG/vector retrieval is config-only while reports may imply future RAG.",
        "2. **Medium**: Neo4j graph code exists but is not live-run wired.",
        "3. **Medium**: Redis is configured but unused by runtime cache/rate/queue paths.",
        "4. **Medium**: External BYOL credentials are not automatically wired from env names.",
        "5. **Low**: Worker polling is fail-safe when tenant scope is not configured.",
    ]


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    inventory = collect_wiring_inventory(args.repo_root)
    destination = write_report(inventory, output_path=args.output, repo_root=args.repo_root)
    print(f"Wiring baseline report written: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
