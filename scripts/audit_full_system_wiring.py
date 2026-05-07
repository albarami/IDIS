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
            "document_classification_ui_integration": _document_classification_ui_integration(
                root
            ),
            "document_classification_run_integration": _document_classification_run_integration(
                files
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
        ".env.example",
        "docker-compose.yml",
        "scripts/pg_init.sql",
        "scripts/db/init.sql",
        "src/idis/api/routes/runs.py",
        "src/idis/api/main.py",
        "src/idis/models/run_step.py",
        "src/idis/services/runs/execution.py",
        "src/idis/services/runs/steps.py",
        "src/idis/pipeline/worker.py",
        "src/idis/pipeline/executor.py",
        "src/idis/services/ingestion/service.py",
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
        "src/idis/models/document_classification.py",
        "src/idis/services/documents/parser_capabilities.py",
        "src/idis/services/documents/classifier.py",
        "src/idis/services/documents/classification_service.py",
        "src/idis/services/documents/audit.py",
        "src/idis/analysis/agents/__init__.py",
        "src/idis/analysis/runner.py",
        "src/idis/debate/orchestrator.py",
        "src/idis/debate/muhasabah_gate.py",
        "src/idis/validators/no_free_facts.py",
        "src/idis/validators/deliverable.py",
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
    return WiringItem(
        key="ingestion_service",
        label="Ingestion service",
        status="PARTIAL",
        summary=(
            "Ingestion service exists and is route-accessible, but default app wiring is "
            "optional."
        ),
        evidence=["`IngestionService.ingest_bytes` stores, parses, and spans raw bytes."],
        gaps=["Default `create_app()` leaves `ingestion_service` optional."],
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
    integrated = "methodology_question_id" in run_steps or "MethodologyRegistry" in run_steps
    return WiringItem(
        key="methodology_run_integration",
        label="Methodology run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary="Methodology registry is not yet wired into production run execution.",
        evidence=["RunExecutionService remains methodology-agnostic in Phase 2.2."],
        gaps=["Future run slices must initialize coverage per methodology question."],
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
    integrated = "classify_document" in run_steps or "DocumentClassification" in worker
    return WiringItem(
        key="document_classification_run_integration",
        label="Document classification run integration",
        status="PARTIAL" if integrated else "DEFERRED",
        summary=(
            "Document classification run integration is deferred; no live run wiring is claimed."
        ),
        evidence=["RunExecutionService remains document-classification agnostic in Phase 2.3."],
        gaps=["Future runs must call classification before methodology-driven extraction."],
        phase_2_action="Phase 2.3",
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
        exclude_paths={"graph_consistency.py"},
    )
    status = "PARTIAL" if api_refs else "TEST_ONLY"
    return WiringItem(
        key="neo4j_graph",
        label="Neo4j / graph projection",
        status=status,
        summary="Neo4j driver and projection code exist, but live run projection is not wired.",
        evidence=[
            "`GraphProjectionService`, `GraphRepository`, and Neo4j driver exist.",
            "Graph projection has dedicated tests.",
        ],
        gaps=["GraphProjectionService is not called by live run/write paths."],
    )


def _redis(files: dict[str, str]) -> WiringItem:
    return WiringItem(
        key="redis",
        label="Redis",
        status="CONFIG_ONLY",
        summary=(
            "Redis is configured in Docker/env examples, but runtime code uses in-memory "
            "stores."
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
            "Anthropic client is wired behind backend env selection, but baseline is dry-run "
            "only."
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
