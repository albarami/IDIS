# ruff: noqa: F401
"""Shared harness for Slice100 Task 1 durable worker-lease contracts.

These tests intentionally describe the approved lease amendment before production
implementation exists.  They are hermetic and synchronous so the declared ``dev``
dependency set is sufficient to collect and run them.

The contract is deliberately narrow:

* one atomic QUEUED -> RUNNING claim writes owner, fencing token, heartbeat, and expiry;
* an expired RUNNING lease is first requeued, then claimed by another worker;
* every step write carries the current fencing token and rejects a zombie token;
* renewal is checked at step boundaries and ownership loss stops before the next step;
* all 27 FULL-run restart cut points retain strict resume/rehydration behavior; and
* cancellation and the one-active-run index keep their existing semantics.

No live provider or network is used. The disposable-PostgreSQL migration proof is
opt-in and skips unless the repository's existing integration-test environment is set.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import gc
import hashlib
import importlib
import importlib.util
import inspect
import json
import re
import sys
import textwrap
import threading
import uuid
import weakref
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import idis.persistence.repositories.run_steps as run_steps_module
import idis.persistence.repositories.runs as runs_module
import idis.pipeline.worker as worker_module
import idis.services.runs.execution as execution_module
import idis.services.runs.orchestrator as orchestrator_module
import idis.services.runs.steps as steps_module
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.models.layer1_durability import ValidatedEvidencePackageRow
from idis.models.run_step import FULL_STEPS, STEP_ORDER, RunStep, StepName, StepStatus
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    PostgresRunStepsRepository,
)
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    PostgresRunsRepository,
    RunAlreadyActiveError,
)
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import OrchestratorResult, RunContext, RunOrchestrator
from idis.validators.audit_event_validator import validate_audit_event

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
FIRST_WORKER_ID = "44444444-4444-4444-8444-444444444444"
SECOND_WORKER_ID = "55555555-5555-4555-8555-555555555555"
LEASE_SECONDS = 60
MIGRATIONS_DIR = Path(runs_module.__file__).parents[1] / "migrations" / "versions"
LEASE_CONTEXT_FIELDS = {
    "lease_owner_id",
    "lease_fencing_token",
    "step_boundary_uow_fn",
    "after_step_boundary_commit_fn",
    "execute_with_lease_heartbeat_fn",
    "stage_native_output_writes_fn",
}

LEASE_AUDIT_CODES = {
    "WORKER_LEASE_CLAIMED",
    "WORKER_LEASE_HEARTBEAT_RENEWED",
    "WORKER_LEASE_EXPIRED",
    "WORKER_LEASE_RECLAIMED",
}

LEASE_FAILURE_CODES = {
    "WORKER_LEASE_FENCED",
    "WORKER_LEASE_CLAIM_FAILED",
    "WORKER_LEASE_HEARTBEAT_FAILED",
    "WORKER_LEASE_RECLAIM_FAILED",
    "WORKER_LEASE_CONFIG_INVALID",
    "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
}

LEASE_EVENT_TYPES = {
    "WORKER_LEASE_CLAIMED": "worker.lease.claimed",
    "WORKER_LEASE_HEARTBEAT_RENEWED": "worker.lease.heartbeat_renewed",
    "WORKER_LEASE_EXPIRED": "worker.lease.expired",
    "WORKER_LEASE_RECLAIMED": "worker.lease.reclaimed",
    "WORKER_LEASE_BOUNDARY_COMMIT_FAILED": "worker.lease.boundary_commit_failed",
}

REHYDRATORS_BY_STEP = {
    StepName.DATA_ROOM_INVENTORY_PACKAGE: "_rehydrate_data_room_inventory_package",
    StepName.METHODOLOGY_COVERAGE_INIT: "_rehydrate_methodology_coverage_records",
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: "_rehydrate_methodology_extraction_tasks",
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION: ("_rehydrate_methodology_extraction_execution"),
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION: "_rehydrate_methodology_materialized_claims",
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: ("_rehydrate_methodology_evidence_items"),
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: "_rehydrate_methodology_sanads",
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION: "_rehydrate_methodology_calculations",
    StepName.METHODOLOGY_TRUTH_DASHBOARD: "_rehydrate_methodology_truth_dashboard",
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT: "_rehydrate_methodology_evidence_trust_court",
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: (
        "_rehydrate_methodology_validated_evidence_package"
    ),
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN: (
        "_rehydrate_methodology_external_intelligence_conflict_check_plan"
    ),
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE: (
        "_rehydrate_methodology_company_identity_package"
    ),
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE: (
        "_rehydrate_methodology_layer2_readiness_package"
    ),
}

# Slice100 admission resolves this versioned order from the run's bound
# ``full-strict-v2`` contract.  ``FULL_STEPS`` remains the legacy ``full-v1``
# compatibility order and must not drive the acceptance restart proof.
STRICT_V2_STEPS: tuple[StepName, ...] = (
    StepName.DATA_ROOM_INVENTORY_PACKAGE,
    StepName.DATA_ROOM_INGESTION_HANDOFF,
    StepName.INGEST_CHECK,
    StepName.DOCUMENT_PREFLIGHT,
    StepName.METHODOLOGY_COVERAGE_INIT,
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION,
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION,
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION,
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
    StepName.METHODOLOGY_TRUTH_DASHBOARD,
    StepName.EXTRACT,
    StepName.GRADE,
    StepName.CALC,
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE,
    StepName.ENRICHMENT,
    StepName.GRAPH_EVIDENCE,
    StepName.RAG_EVIDENCE,
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
    StepName.DEBATE,
    StepName.ANALYSIS,
    StepName.SCORING,
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE,
    StepName.LAYER2_IC_CHALLENGE,
    StepName.DELIVERABLES,
)

# Reviewed logical persistence targets from plan section 8. These are contract IDs,
# not authority to add tables; each registry spec must bind the ID to an existing or
# separately approved concrete model/repository implementation.
EXPECTED_NATIVE_OUTPUT_CONTRACTS: dict[StepName, tuple[str, str]] = {
    StepName.DATA_ROOM_INVENTORY_PACKAGE: (
        "data_room_inventory_package_output",
        "postgres:package+file_ledger|object_store:source_objects",
    ),
    StepName.DATA_ROOM_INGESTION_HANDOFF: (
        "data_room_ingestion_handoff_output",
        "postgres:ingestion_receipt+documents+artifacts+parse_attempts+spans|object_store",
    ),
    StepName.INGEST_CHECK: (
        "ingest_check_output",
        "postgres:ingest_reconciliation",
    ),
    StepName.DOCUMENT_PREFLIGHT: (
        "document_preflight_output",
        "postgres:document_classifications",
    ),
    StepName.METHODOLOGY_COVERAGE_INIT: (
        "methodology_coverage_init_output",
        "postgres:methodology_versions+run_selections+coverage_rows",
    ),
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: (
        "methodology_extraction_task_planning_output",
        "postgres:extraction_tasks+dependencies+candidate_spans",
    ),
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION: (
        "methodology_extraction_task_execution_output",
        "postgres:extraction_attempts+results+provider_provenance",
    ),
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION: (
        "methodology_claim_materialization_output",
        "postgres:canonical_claims+methodology_row_links",
    ),
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: (
        "methodology_evidence_item_materialization_output",
        "postgres:evidence_items+document_span_links",
    ),
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: (
        "methodology_sanad_creation_linking_grading_output",
        "postgres:sanads+links+grades+defects",
    ),
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION: (
        "methodology_deterministic_calculation_output",
        "postgres:calculations+calc_sanads",
    ),
    StepName.METHODOLOGY_TRUTH_DASHBOARD: (
        "methodology_truth_dashboard_output",
        "postgres:row_level_truth_dashboard",
    ),
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT: (
        "methodology_evidence_trust_court_output",
        "postgres:layer1_findings+muhasabah",
    ),
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: (
        "methodology_validated_evidence_package_output",
        "postgres:validated_evidence_package+content_refs",
    ),
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN: (
        "methodology_external_intelligence_conflict_check_output",
        "postgres:conflict_check_plan+executions+findings",
    ),
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE: (
        "methodology_company_identity_package_output",
        "postgres:company_identity+version+identifiers",
    ),
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE: (
        "methodology_layer2_readiness_package_output",
        "postgres:layer2_readiness_package",
    ),
    StepName.EXTRACT: (
        "extract_output",
        "postgres:preextract_retrieval+claims+evidence+provider_provenance",
    ),
    StepName.GRADE: (
        "grade_output",
        "postgres:claims+sanads+defects",
    ),
    StepName.CALC: (
        "calc_output",
        "postgres:calculations+calc_sanads",
    ),
    StepName.GRAPH_EVIDENCE: (
        "graph_evidence_output",
        "neo4j:projection|postgres:graph_provenance",
    ),
    StepName.RAG_EVIDENCE: (
        "rag_evidence_output",
        "postgres:embeddings+retrieval_queries+retrieval_results",
    ),
    StepName.ENRICHMENT: (
        "enrichment_output",
        "postgres:provider_outcomes+normalized_facts+provenance+rights_decisions",
    ),
    StepName.DEBATE: (
        "debate_output",
        "postgres:debate_rounds+role_outputs+dissent+muhasabah",
    ),
    StepName.LAYER2_IC_CHALLENGE: (
        "layer2_ic_challenge_output",
        "postgres:layer2_role_outputs+challenges+findings+dissent+muhasabah",
    ),
    StepName.ANALYSIS: (
        "analysis_output",
        "postgres:agent_outputs+analysis_report_bundle",
    ),
    StepName.SCORING: (
        "scoring_output",
        "postgres:validated_scorecard",
    ),
    StepName.DELIVERABLES: (
        "deliverables_output",
        "postgres:product_rows|object_store:deliverable_objects",
    ),
}


@dataclass(frozen=True)
class _ReviewedPersistenceBinding:
    """A source-grounded binding that exists before the Task 7 schema design."""

    row_type_qname: str | None
    repository_type_qname: str
    persist_method: str | None
    load_methods: tuple[str, ...]
    sql_targets: frozenset[str] = frozenset()
    non_sql_methods: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _ReviewedStepOutputBinding:
    """Known concrete pieces plus gaps that only the gated Task 7 design may fill."""

    aggregate_type_qname: str | None
    unresolved_requirements: tuple[str, ...]
    produced_write_bindings: tuple[_ReviewedPersistenceBinding, ...] = ()
    read_dependencies: tuple[_ReviewedPersistenceBinding, ...] = ()

    @property
    def all_source_grounded_bindings(self) -> tuple[_ReviewedPersistenceBinding, ...]:
        return self.produced_write_bindings + self.read_dependencies


def _binding(
    row_type_qname: str | None,
    repository_type_qname: str,
    persist_method: str | None,
    *load_methods: str,
    sql_targets: tuple[str, ...] = (),
    non_sql_methods: tuple[str, ...] = (),
) -> _ReviewedPersistenceBinding:
    return _ReviewedPersistenceBinding(
        row_type_qname=row_type_qname,
        repository_type_qname=repository_type_qname,
        persist_method=persist_method,
        load_methods=load_methods,
        sql_targets=frozenset(sql_targets),
        non_sql_methods=frozenset(non_sql_methods),
    )


def _task7_gaps(*specific: str) -> tuple[str, ...]:
    return (
        *specific,
        "Task 7 schema-review gate: first-class output root plus exact run-scoped "
        "load_output/rehydrate_context contract",
    )


# This inventory is intentionally independent of any production registry.  It pins
# only model/repository/method/table targets that exist and were source-reviewed.
# A plan-approved target that has no concrete schema yet stays an explicit RED
# blocker; inventing a plausible ``idis.*`` class or table cannot satisfy it.
REVIEWED_STEP_OUTPUT_BINDINGS: dict[StepName, _ReviewedStepOutputBinding] = {
    StepName.DATA_ROOM_INVENTORY_PACKAGE: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.data_room_inventory_package_materialization."
            "DataRoomInventoryPackageRunResult"
        ),
        read_dependencies=(
            _binding(
                "idis.models.data_room_package.DataRoomPackage",
                "idis.persistence.repositories.data_room_packages."
                "PostgresDataRoomPackagesRepository",
                None,
                "get_package",
                sql_targets=("data_room_packages",),
            ),
            _binding(
                "idis.models.data_room_package.DataRoomPackageFile",
                "idis.persistence.repositories.data_room_packages."
                "PostgresDataRoomPackagesRepository",
                None,
                "list_files_by_package",
                sql_targets=("data_room_package_files",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "approved persistent-source-object target has no reviewed namespace binding",
            "existing package loaders do not reconstruct the exact run-scoped aggregate",
        ),
    ),
    StepName.DATA_ROOM_INGESTION_HANDOFF: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.data_room_ingestion_handoff.DataRoomIngestionHandoffRunResult"
        ),
        read_dependencies=(
            _binding(
                "idis.models.document.Document",
                "idis.persistence.repositories.documents.PostgresDocumentsRepository",
                None,
                "list_documents_by_deal",
                sql_targets=("documents", "document_artifacts"),
            ),
            _binding(
                "idis.models.document_span.DocumentSpan",
                "idis.persistence.repositories.documents.PostgresDocumentsRepository",
                None,
                "list_spans_by_document",
                sql_targets=("document_spans",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "immutable ingestion receipt and parse-attempt bindings are missing",
            "approved ingestion object-store target has no reviewed namespace binding",
            "artifact/run-scoped aggregate loader is missing",
        ),
    ),
    StepName.INGEST_CHECK: _ReviewedStepOutputBinding(
        aggregate_type_qname=None,
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "ingest reconciliation has no concrete native model/repository/table",
        ),
    ),
    StepName.DOCUMENT_PREFLIGHT: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.document_preflight.DocumentPreflightResult",
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "document classifications have no concrete repository/table",
        ),
    ),
    StepName.METHODOLOGY_COVERAGE_INIT: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.methodology_coverage.MethodologyCoverageInitializationResult"
        ),
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "methodology versions, run selections, and coverage rows have no repository/table",
        ),
    ),
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.extraction_task.ExtractionTaskPlanningRunResult",
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "extraction tasks, dependencies, and candidate spans have no repository/table",
        ),
    ),
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.extraction_execution.MethodologyExtractionExecutionRunResult"
        ),
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "attempts, results, and provider provenance have no repository/table",
        ),
    ),
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.claim_materialization.MethodologyOutputClaimMaterializationRunResult"
        ),
        produced_write_bindings=(
            _binding(
                "idis.models.claim.Claim",
                "idis.persistence.repositories.claims.ClaimsRepository",
                "create",
                "list_by_deal",
                sql_targets=("claims",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "claims are deal-scoped and cannot load an exact run output",
            "methodology-row links are missing",
        ),
    ),
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.evidence_item_materialization."
            "MethodologyEvidenceItemMaterializationRunResult"
        ),
        produced_write_bindings=(
            _binding(
                "idis.models.evidence_item.EvidenceItem",
                "idis.persistence.repositories.evidence.PostgresEvidenceRepository",
                "create",
                "get_by_claim",
                sql_targets=("evidence_items",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "evidence rows have no exact run-scoped loader or document-span link binding",
        ),
    ),
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.sanad_materialization.MethodologySanadMaterializationRunResult"
        ),
        produced_write_bindings=(
            _binding(
                "idis.models.sanad.Sanad",
                "idis.persistence.repositories.claims.SanadsRepository",
                "create",
                "list_by_deal",
                sql_targets=("sanads",),
            ),
            _binding(
                "idis.models.defect.Defect",
                "idis.persistence.repositories.claims.DefectsRepository",
                "create",
                "list_by_deal",
                sql_targets=("defects",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "run-scoped Sanad links and grade bindings are missing",
            "deal-scoped rows cannot reconstruct the exact run aggregate",
        ),
    ),
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.calc_materialization.MethodologyCalculationRunResult",
        produced_write_bindings=(
            _binding(
                "idis.models.deterministic_calculation.DeterministicCalculation",
                "idis.persistence.repositories.calculations.PostgresCalculationsRepository",
                "create",
                "list_by_deal",
                "list_calc_sanads_by_deal",
                sql_targets=("deterministic_calculations", "calc_sanads"),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "calculation rows are deal-scoped and have no exact run aggregate loader",
        ),
    ),
    StepName.METHODOLOGY_TRUTH_DASHBOARD: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.truth_dashboard_materialization.MethodologyTruthDashboardRunResult"
        ),
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "row-level truth dashboard has no concrete repository/table",
        ),
    ),
    StepName.EXTRACT: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.services.extraction.pipeline.PipelineRunResult",
        produced_write_bindings=(
            _binding(
                "idis.models.claim.Claim",
                "idis.persistence.repositories.claims.ClaimsRepository",
                "create",
                "list_by_deal",
                sql_targets=("claims",),
            ),
            _binding(
                "idis.models.evidence_item.EvidenceItem",
                "idis.persistence.repositories.evidence.PostgresEvidenceRepository",
                "create",
                "get_by_claim",
                sql_targets=("evidence_items",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "pre-extract retrieval/query and provider-provenance outputs are missing",
            "claim/evidence rows cannot load the exact run aggregate",
        ),
    ),
    StepName.GRADE: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.services.sanad.auto_grade.AutoGradeRunResult",
        produced_write_bindings=(
            _binding(
                "idis.models.claim.Claim",
                "idis.persistence.repositories.claims.ClaimsRepository",
                "update_grade",
                "list_by_deal",
                sql_targets=("claims",),
            ),
            _binding(
                "idis.models.sanad.Sanad",
                "idis.persistence.repositories.claims.SanadsRepository",
                "create",
                "list_by_deal",
                sql_targets=("sanads",),
            ),
            _binding(
                "idis.models.defect.Defect",
                "idis.persistence.repositories.claims.DefectsRepository",
                "create",
                "list_by_deal",
                sql_targets=("defects",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "grade rows have no exact run-scoped aggregate loader",
        ),
    ),
    StepName.CALC: _ReviewedStepOutputBinding(
        aggregate_type_qname=None,
        produced_write_bindings=(
            _binding(
                "idis.models.deterministic_calculation.DeterministicCalculation",
                "idis.persistence.repositories.calculations.PostgresCalculationsRepository",
                "create",
                "list_by_deal",
                "list_calc_sanads_by_deal",
                sql_targets=("deterministic_calculations", "calc_sanads"),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "unified calculation has no typed run aggregate or exact run loader",
        ),
    ),
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.company_identity_package_materialization."
            "MethodologyCompanyIdentityPackageRunResult"
        ),
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "company identity/version/identifier rows have no repository/table",
        ),
    ),
    StepName.ENRICHMENT: _ReviewedStepOutputBinding(
        aggregate_type_qname=None,
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "EnrichmentResult is per-provider, not a typed 15-provider run aggregate",
            "provider outcomes, facts, provenance, and rights decisions have no repository/table",
        ),
    ),
    StepName.GRAPH_EVIDENCE: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.persistence.graph_consistency.ProjectionResult",
        produced_write_bindings=(
            _binding(
                "idis.persistence.graph_consistency.ProjectionResult",
                "idis.persistence.graph_repo.GraphRepository",
                "upsert_deal_graph_projection",
                "get_deal_claims_with_grades",
                non_sql_methods=(
                    "upsert_deal_graph_projection",
                    "get_deal_claims_with_grades",
                ),
            ),
            _binding(
                "idis.persistence.graph_consistency.ProjectionResult",
                "idis.persistence.graph_repo.GraphRepository",
                "upsert_claim_sanad_projection",
                "get_claim_sanad_chain",
                "get_independence_clusters",
                "get_weakest_link",
                "get_defect_impact",
                "get_entity_cooccurrence",
                non_sql_methods=(
                    "upsert_claim_sanad_projection",
                    "get_claim_sanad_chain",
                    "get_independence_clusters",
                    "get_weakest_link",
                    "get_defect_impact",
                    "get_entity_cooccurrence",
                ),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "PostgreSQL graph provenance and a typed complete graph output are missing",
        ),
    ),
    StepName.RAG_EVIDENCE: _ReviewedStepOutputBinding(
        aggregate_type_qname=None,
        produced_write_bindings=(
            _binding(
                None,
                "idis.persistence.repositories.vector_embeddings."
                "PostgresVectorEmbeddingsRepository",
                "upsert_embedding",
                "similarity_search",
                sql_targets=("vector_embeddings",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "RAG has no typed run aggregate or durable retrieval-query/result rows",
        ),
    ),
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN: (
        _ReviewedStepOutputBinding(
            aggregate_type_qname=(
                "idis.models.external_intelligence_conflict_check_plan_materialization."
                "MethodologyExternalIntelligenceConflictCheckPlanRunResult"
            ),
            produced_write_bindings=(),
            unresolved_requirements=_task7_gaps(
                "conflict plans, executions, and findings have no repository/table",
            ),
        )
    ),
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.evidence_trust_court_materialization."
            "MethodologyEvidenceTrustCourtRunResult"
        ),
        produced_write_bindings=(
            _binding(
                "idis.models.layer1_durability.EvidenceTrustFindingRow",
                "idis.persistence.repositories.layer1_evidence.PostgresLayer1EvidenceRepository",
                "upsert_evidence_trust_finding",
                "list_evidence_trust_findings",
                sql_targets=("evidence_trust_findings",),
            ),
            _binding(
                "idis.models.layer1_durability.MuhasabahRecordRow",
                "idis.persistence.repositories.layer1_evidence.PostgresLayer1EvidenceRepository",
                "upsert_muhasabah_record",
                "list_muhasabah_records",
                sql_targets=("muhasabah_records",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "existing row loaders do not reconstruct the typed Court aggregate",
        ),
    ),
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.validated_evidence_package_materialization."
            "MethodologyValidatedEvidencePackageRunResult"
        ),
        produced_write_bindings=(
            _binding(
                "idis.models.layer1_durability.ValidatedEvidencePackageRow",
                "idis.persistence.repositories.layer1_evidence.PostgresLayer1EvidenceRepository",
                "upsert_validated_evidence_package",
                "list_validated_evidence_packages",
                sql_targets=("validated_evidence_packages",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "existing row loader does not reconstruct the typed VEP aggregate/content refs",
        ),
    ),
    StepName.DEBATE: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.debate.DebateState",
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "debate route SQL is not a worker repository binding",
            "rounds, role outputs, dissent, and Muhasabah persistence are missing",
        ),
    ),
    StepName.ANALYSIS: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.analysis.models.AnalysisBundle",
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "agent outputs and analysis report bundle have no repository/table",
        ),
    ),
    StepName.SCORING: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.analysis.scoring.models.Scorecard",
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "validated scorecard has no repository/table",
        ),
    ),
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE: _ReviewedStepOutputBinding(
        aggregate_type_qname=(
            "idis.models.layer2_readiness_package_materialization."
            "MethodologyLayer2ReadinessPackageRunResult"
        ),
        produced_write_bindings=(),
        unresolved_requirements=_task7_gaps(
            "Layer-2 readiness package has no repository/table",
        ),
    ),
    StepName.LAYER2_IC_CHALLENGE: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.layer2_ic_challenge.Layer2ICChallengeRecord",
        produced_write_bindings=(
            _binding(
                "idis.models.layer2_durability.Layer2ChallengeRow",
                "idis.persistence.repositories.layer2_challenge.PostgresLayer2ChallengeRepository",
                "upsert_challenge",
                "list_challenges",
                sql_targets=("layer2_ic_challenges",),
            ),
            _binding(
                "idis.models.layer2_durability.Layer2FindingRow",
                "idis.persistence.repositories.layer2_challenge.PostgresLayer2ChallengeRepository",
                "upsert_finding",
                "list_findings",
                sql_targets=("layer2_ic_findings",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "Layer-2 role outputs, dissent, and Muhasabah persistence are missing",
            "existing row loaders do not reconstruct the complete typed aggregate",
        ),
    ),
    StepName.DELIVERABLES: _ReviewedStepOutputBinding(
        aggregate_type_qname="idis.models.deliverables.DeliverablesBundle",
        produced_write_bindings=(
            _binding(
                None,
                "idis.persistence.repositories.deliverables.PostgresDeliverablesRepository",
                "create_completed",
                "list_by_deal",
                "get_by_id",
                sql_targets=("deliverables",),
            ),
        ),
        unresolved_requirements=_task7_gaps(
            "repository has no exact list_by_run aggregate loader",
            "stored artifact is private and the object-store namespace/put/head/get binding "
            "is not yet reviewed",
        ),
    ),
}


def _qualified_name(value: Any) -> str:
    target = value if inspect.isclass(value) else type(value)
    return f"{target.__module__}.{target.__qualname__}"


def _resolve_qualified_name(qualified_name: str) -> Any:
    module_name, attribute_name = qualified_name.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attribute_name)


def _assert_no_unreviewed_task7_binding(step_name: StepName) -> None:
    expected = REVIEWED_STEP_OUTPUT_BINDINGS[step_name]
    assert not expected.unresolved_requirements, (
        f"{step_name.value} cannot be claimed as a concrete restart binding before the "
        "Task 7 receipt/edge schema-review gate resolves: "
        + "; ".join(expected.unresolved_requirements)
    )


def _contract_binding_fingerprint(binding: Any) -> tuple[Any, ...]:
    return (
        _qualified_name(binding.row_model_type)
        if getattr(binding, "row_model_type", None) is not None
        else None,
        _qualified_name(binding.repository_type),
        binding.persist_method,
        tuple(binding.load_methods),
        frozenset(binding.sql_targets),
        frozenset(binding.non_sql_methods),
    )


def _reviewed_binding_fingerprint(
    binding: _ReviewedPersistenceBinding,
) -> tuple[Any, ...]:
    return (
        binding.row_type_qname,
        binding.repository_type_qname,
        binding.persist_method,
        binding.load_methods,
        binding.sql_targets,
        binding.non_sql_methods,
    )


def _assert_contract_matches_reviewed_binding(step_name: StepName, contract: Any) -> None:
    expected = REVIEWED_STEP_OUTPUT_BINDINGS[step_name]
    _assert_no_unreviewed_task7_binding(step_name)
    assert expected.aggregate_type_qname is not None
    assert _qualified_name(contract.aggregate_output_type) == expected.aggregate_type_qname
    actual_bindings = tuple(contract.write_bindings)
    assert tuple(_contract_binding_fingerprint(item) for item in actual_bindings) == tuple(
        _reviewed_binding_fingerprint(item) for item in expected.produced_write_bindings
    )
    _require_parameters(
        contract.load_output,
        {"db_conn", "tenant_id", "deal_id", "run_id"},
        contract=f"{step_name.value} typed output loader",
    )
    load_parameters = set(inspect.signature(contract.load_output).parameters)
    assert not load_parameters & {"result_summary", "run_step", "step"}, (
        f"{step_name.value} load_output may not receive ledger-summary state"
    )
    _require_parameters(
        contract.rehydrate_context,
        {"ctx", "output"},
        contract=f"{step_name.value} typed context rehydrator",
    )
    rehydrate_parameters = set(inspect.signature(contract.rehydrate_context).parameters)
    assert "result_summary" not in rehydrate_parameters
    _require_parameters(
        contract.summary_from_output,
        {"output"},
        contract=f"{step_name.value} canonical summary builder",
    )


def _require_parameters(
    callable_object: Callable[..., Any],
    expected: set[str],
    *,
    contract: str,
) -> None:
    parameters = set(inspect.signature(callable_object).parameters)
    missing = expected - parameters
    assert not missing, (
        f"{contract} is missing approved parameters {sorted(missing)}; "
        f"current signature has {sorted(parameters)}"
    )


def _require_run_context_lease_fields() -> None:
    fields = set(RunContext.__dataclass_fields__)
    missing = LEASE_CONTEXT_FIELDS - fields
    assert not missing, (
        "RunContext cannot propagate the claiming lease to step boundaries; "
        f"missing approved fields {sorted(missing)}"
    )


def _context(
    *,
    lease_owner_id: str = SECOND_WORKER_ID,
    lease_fencing_token: int = 3,
    step_boundary_uow_fn: Callable[..., None] | None = None,
    after_step_boundary_commit_fn: Callable[..., None] | None = None,
    execute_with_lease_heartbeat_fn: Callable[..., Any] | None = None,
    stage_native_output_writes_fn: Callable[..., tuple[Callable[..., None], ...]] | None = None,
) -> RunContext:
    _require_run_context_lease_fields()
    boundary_uow = step_boundary_uow_fn or (lambda *, mutation, **_kwargs: mutation())
    after_commit = after_step_boundary_commit_fn or (lambda **_kwargs: None)
    with_heartbeat = execute_with_lease_heartbeat_fn or (
        lambda *, operation, **_kwargs: operation()
    )
    stage_outputs = stage_native_output_writes_fn or _stage_summary_native_output_writes
    return RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        lease_owner_id=lease_owner_id,
        lease_fencing_token=lease_fencing_token,
        step_boundary_uow_fn=boundary_uow,
        after_step_boundary_commit_fn=after_commit,
        execute_with_lease_heartbeat_fn=with_heartbeat,
        stage_native_output_writes_fn=stage_outputs,
    )


@pytest.fixture(autouse=True)
def _clear_in_memory_lease_stores() -> None:
    _clear_all_idis_in_memory_stores()
    yield
    _clear_all_idis_in_memory_stores()


def _clear_all_idis_in_memory_stores() -> None:
    """Clear every loaded IDIS repository store, including lazy imports from FULL dispatch."""
    _clear_loaded_idis_in_memory_stores(preserve_ids=set())


def _clear_loaded_idis_in_memory_stores(*, preserve_ids: set[int]) -> None:
    """Simulate process loss while optionally retaining explicitly durable test state."""
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("idis.") or module is None:
            continue
        for attribute_name, value in vars(module).items():
            if (
                attribute_name.endswith("_store")
                and isinstance(value, dict)
                and id(value) not in preserve_ids
            ):
                value.clear()


def _clear_worker_a_non_durable_stores() -> None:
    """Drop process-local outputs after crash but retain the durable run-row test twin."""
    _clear_loaded_idis_in_memory_stores(
        preserve_ids={id(runs_module._in_memory_runs_store)},
    )


def _seed_queued_run(
    *,
    run_id: str = RUN_ID,
    deal_id: str = DEAL_ID,
) -> InMemoryRunsRepository:
    runs_module.clear_in_memory_runs_store()
    repository = InMemoryRunsRepository(TENANT_ID)
    repository.create(
        run_id=run_id,
        deal_id=deal_id,
        mode="FULL",
        source={"type": "deal_documents", "document_ids": []},
        created_by_actor_id="originating-human",
        created_by_actor_type="HUMAN",
    )
    return repository


class _Result:
    def __init__(self, row: Any | None = None, *, rowcount: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> Any | None:
        return self._row

    def fetchall(self) -> list[Any]:
        return [] if self._row is None else [self._row]

    def one(self) -> Any:
        if self._row is None:
            raise AssertionError("SQLAlchemy Result.one() received no row")
        return self._row

    def one_or_none(self) -> Any | None:
        return self._row

    def scalar_one_or_none(self) -> Any | None:
        return self._row


class _CapturingConnection:
    def __init__(
        self,
        rows: list[Any | None] | None = None,
        *,
        rowcounts: list[int] | None = None,
    ) -> None:
        self.rows = list(rows or [])
        self.rowcounts = list(rowcounts or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        row = self.rows.pop(0) if self.rows else None
        rowcount = self.rowcounts.pop(0) if self.rowcounts else 1
        return _Result(row, rowcount=rowcount)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _leased_row(
    *,
    status: str,
    owner_id: str | None,
    fencing_token: int,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        status=status,
        started_at=now,
        finished_at=None,
        source={"type": "deal_documents", "document_ids": []},
        created_at=now,
        cancel_requested_at=None,
        created_by_actor_id="originating-human",
        created_by_actor_type="HUMAN",
        strict_preflight_proof_id=None,
        lease_owner_id=owner_id,
        lease_fencing_token=fencing_token,
        lease_expires_at=now,
        last_heartbeat_at=now,
        prior_lease_owner_id=FIRST_WORKER_ID,
        prior_fencing_token=max(fencing_token - 1, 0),
    )


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.upper().split())


_UNSUPPORTED_CTE_RELATION_TARGET = "__unsupported_cte_scope__"


def _sql_cte_aliases(tokens: list[str]) -> frozenset[str]:
    """Identify unqualified CTE aliases so they cannot masquerade as durable tables."""
    aliases: set[str] = set()
    identifier = re.compile(r'"(?:[^"]|"")*"|[A-Z_][A-Z0-9_$]*')
    for with_index, token in enumerate(tokens):
        if token != "WITH":
            continue
        index = with_index + 1
        if index < len(tokens) and tokens[index] == "RECURSIVE":
            index += 1
        while index < len(tokens) and identifier.fullmatch(tokens[index]):
            alias = tokens[index].replace('"', "").lower()
            index += 1
            if index < len(tokens) and tokens[index] == "(":
                depth = 1
                index += 1
                while index < len(tokens) and depth:
                    depth += tokens[index] == "("
                    depth -= tokens[index] == ")"
                    index += 1
            if index >= len(tokens) or tokens[index] != "AS":
                break
            index += 1
            while index < len(tokens) and tokens[index] in {
                "MATERIALIZED",
                "NOT",
            }:
                index += 1
            if index >= len(tokens) or tokens[index] != "(":
                break
            aliases.add(alias)
            depth = 1
            index += 1
            while index < len(tokens) and depth:
                depth += tokens[index] == "("
                depth -= tokens[index] == ")"
                index += 1
            if index >= len(tokens) or tokens[index] != ",":
                break
            index += 1
    return frozenset(aliases)


def _sql_relation_targets(statement: str) -> tuple[str, ...]:
    """Return every concrete FROM/JOIN relation, including comma joins."""
    normalized = _normalized_sql(statement)
    tokens = re.findall(r'"(?:[^"]|"")*"|[A-Z_][A-Z0-9_$]*|[().,;]', normalized)
    cte_aliases = _sql_cte_aliases(tokens)
    clause_terminators = {
        "EXCEPT",
        "FETCH",
        "FOR",
        "GROUP",
        "HAVING",
        "INTERSECT",
        "LIMIT",
        "OFFSET",
        "ORDER",
        "QUALIFY",
        "RETURNING",
        "UNION",
        "WHERE",
        "WINDOW",
    }
    targets: list[str] = []
    active_from_depths: set[int] = set()
    expect_relation_depths: set[int] = set()
    depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        keyword = token.replace('"', "").upper()
        is_keyword = not token.startswith('"')

        if depth in expect_relation_depths:
            if is_keyword and keyword in {"LATERAL", "ONLY"}:
                index += 1
                continue
            if token == "(":
                expect_relation_depths.discard(depth)
                depth += 1
                index += 1
                continue
            assert re.fullmatch(r'"(?:[^"]|"")*"|[A-Z_][A-Z0-9_$]*', token), (
                f"unsupported SQL relation syntax after FROM/JOIN: {normalized}"
            )
            relation_parts = [token]
            while (
                index + 2 < len(tokens)
                and tokens[index + 1] == "."
                and re.fullmatch(
                    r'"(?:[^"]|"")*"|[A-Z_][A-Z0-9_$]*',
                    tokens[index + 2],
                )
            ):
                relation_parts.append(tokens[index + 2])
                index += 2
            target = relation_parts[-1].replace('"', "").lower()
            is_cte_reference = len(relation_parts) == 1 and target in cte_aliases
            if target and not is_cte_reference and target not in targets:
                targets.append(target)
            expect_relation_depths.discard(depth)
            index += 1
            continue

        if token == "(":
            depth += 1
        elif token == ")":
            active_from_depths.discard(depth)
            expect_relation_depths.discard(depth)
            depth = max(depth - 1, 0)
        elif token == ";" or (is_keyword and keyword in clause_terminators):
            active_from_depths.discard(depth)
            expect_relation_depths.discard(depth)
        elif token in {"FROM", "JOIN"} or (
            token == "TABLE"
            and (index == 0 or tokens[index - 1] in {"(", "EXCEPT", "INTERSECT", "UNION"})
        ):
            active_from_depths.add(depth)
            expect_relation_depths.add(depth)
        elif token == "," and depth in active_from_depths:
            expect_relation_depths.add(depth)
        index += 1

    assert not expect_relation_depths, f"unterminated FROM/JOIN relation in SQL: {normalized}"
    if cte_aliases:
        # This harness deliberately does not pretend to be a scope-aware SQL parser.
        # Any CTE is therefore an explicit unreviewed target and fails closed, even
        # when a nested alias shadows the name of an outer physical relation.
        targets.append(_UNSUPPORTED_CTE_RELATION_TARGET)
    return tuple(targets)


def _method_sql_relation_targets(method: Callable[..., Any]) -> frozenset[str]:
    """Extract SQL relation targets from string literals in a repository method."""
    source = textwrap.dedent(inspect.getsource(method))
    tree = ast.parse(source)
    targets: set[str] = set()
    for node in ast.walk(tree):
        sql_literal: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql_literal = node.value
        elif isinstance(node, ast.JoinedStr):
            sql_literal = " ".join(
                part.value if isinstance(part, ast.Constant) else "INTERPOLATED_VALUE"
                for part in node.values
            )
        if sql_literal is not None and re.search(r"\bSELECT\b", sql_literal, re.IGNORECASE):
            targets.update(_sql_relation_targets(sql_literal))
    return frozenset(targets)


def _assert_sql_column_equals_value(
    sql: str,
    params: dict[str, Any],
    *,
    column: str,
    value: str,
) -> None:
    """Accept either a safe bound value or an equivalent SQL literal."""
    normalized = _normalized_sql(sql)
    normalized_params = {str(name).upper(): item for name, item in params.items()}
    qualified = rf"(?:\w+\.)?{re.escape(column.upper())}"
    if re.search(rf"{qualified}\s*=\s*'{re.escape(value.upper())}'", normalized):
        return
    matches = re.findall(rf"{qualified}\s*=\s*:(\w+)", normalized)
    assert any(str(normalized_params.get(name)).upper() == value.upper() for name in matches), (
        f"{column} is not safely constrained to {value}"
    )


def _update_set_clause(sql: str, *, table: str = "runs") -> str:
    """Return the target UPDATE SET clause without prescribing aliases or bind names."""
    normalized = _normalized_sql(sql)
    match = re.search(
        rf"\bUPDATE\s+(?:(?:\w+|\"\w+\")\.)?[\"]?{re.escape(table.upper())}[\"]?"
        r"(?:\s+(?:AS\s+)?\w+)?\s+SET\s+(?P<set_clause>.*?)\s+WHERE\s+",
        normalized,
    )
    assert match is not None, f"could not identify the {table} UPDATE SET clause"
    return match.group("set_clause")


def _assert_update_assigns_value(
    sql: str,
    params: dict[str, Any],
    *,
    table: str,
    column: str,
    value: str,
) -> None:
    """Require the transition target in SET, not merely a value in a CTE predicate."""
    set_clause = _update_set_clause(sql, table=table)
    _assert_sql_column_equals_value(set_clause, params, column=column, value=value)


def _assert_worker_lease_fenced(operation: Callable[[], Any]) -> None:
    caught: Exception | None = None
    try:
        operation()
    except Exception as exc:  # implementation owns the concrete typed exception
        caught = exc
    assert caught is not None, "stale worker write unexpectedly succeeded"
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"


class _WorkerLeaseFenced(RuntimeError):
    code = "WORKER_LEASE_FENCED"


class _WorkerLeaseBoundaryCommitFailed(RuntimeError):
    code = "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"


@dataclass
class _AtomicBoundaryState:
    run_id: str
    lease_owner_id: str
    lease_fencing_token: int
    status: str = "RUNNING"
    steps: dict[StepName, RunStep] = field(default_factory=dict)
    native_outputs: dict[StepName, dict[str, Any]] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    heartbeat_count: int = 0


class _TransactionalRunStepsRepository:
    """Fresh repository views see committed state only; writes require an active UoW."""

    def __init__(self, state: _AtomicBoundaryState) -> None:
        self.state = state
        self._working: _AtomicBoundaryState | None = None

    def bind_transaction(self, working: _AtomicBoundaryState | None) -> None:
        self._working = working

    def _writable(
        self,
        *,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> _AtomicBoundaryState:
        working = self._working
        if (
            working is None
            or working.status != "RUNNING"
            or working.lease_owner_id != lease_owner_id
            or working.lease_fencing_token != lease_fencing_token
        ):
            raise _WorkerLeaseFenced
        return working

    def create(
        self,
        step: RunStep,
        *,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> RunStep:
        working = self._writable(
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        assert step.run_id == working.run_id
        working.steps[step.step_name] = step.model_copy(deep=True)
        return step

    def update(
        self,
        step: RunStep,
        *,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> RunStep:
        working = self._writable(
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        assert step.step_name in working.steps
        working.steps[step.step_name] = step.model_copy(deep=True)
        return step

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        assert run_id == self.state.run_id
        step = self.state.steps.get(step_name)
        return step.model_copy(deep=True) if step is not None else None

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        assert run_id == self.state.run_id
        return [
            step.model_copy(deep=True)
            for step in sorted(self.state.steps.values(), key=lambda item: item.step_order)
        ]


class _TransactionalAuditSink:
    def __init__(self, state: _AtomicBoundaryState) -> None:
        self.state = state
        self._working: _AtomicBoundaryState | None = None

    def bind_transaction(self, working: _AtomicBoundaryState | None) -> None:
        self._working = working

    def emit(self, event: dict[str, Any]) -> None:
        target = self._working if self._working is not None else self.state
        target.audits.append(copy.deepcopy(event))


class _AtomicStepBoundaryUoW:
    """Transaction-aware test double for the reviewed single-owner boundary seam."""

    def __init__(
        self,
        state: _AtomicBoundaryState,
        repository: _TransactionalRunStepsRepository,
        audit_sink: _TransactionalAuditSink,
        *,
        fail_boundary: tuple[StepName, str] | None = None,
        fail_point: str | None = None,
    ) -> None:
        self.state = state
        self.repository = repository
        self.audit_sink = audit_sink
        self.fail_boundary = fail_boundary
        self.fail_point = fail_point
        self.commits: list[tuple[StepName, str, str, int]] = []

    def __call__(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_name: StepName,
        boundary: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        mutation: Callable[[], None],
        native_output_writes: tuple[Callable[..., None], ...] = (),
    ) -> None:
        assert run_id == self.state.run_id
        assert tenant_id == TENANT_ID
        if (
            self.state.status != "RUNNING"
            or self.state.lease_owner_id != lease_owner_id
            or self.state.lease_fencing_token != lease_fencing_token
        ):
            raise _WorkerLeaseFenced

        working = copy.deepcopy(self.state)
        working.heartbeat_count += 1
        self.repository.bind_transaction(working)
        self.audit_sink.bind_transaction(working)
        should_fail = self.fail_boundary == (step_name, boundary)
        try:
            if should_fail and self.fail_point == "write":
                working.native_outputs[step_name] = {"partial": True}
                raise OSError("injected boundary write failure")
            if boundary == "START":
                assert native_output_writes == (), "START must not persist completion output"
            else:
                assert native_output_writes, (
                    "COMPLETE omitted the staged native-output write set; a RunStep summary "
                    "is not an output"
                )
                for write in native_output_writes:
                    write(
                        transaction_state=working,
                        lease_owner_id=lease_owner_id,
                        lease_fencing_token=lease_fencing_token,
                    )
            mutation()
            if boundary == "COMPLETE":
                assert step_name in working.native_outputs, (
                    "completion mutation omitted its centralized native-output write set"
                )
            if should_fail and self.fail_point == "audit":
                raise AuditSinkError("injected boundary audit failure")
            self.audit_sink.emit(
                {
                    "event_type": "worker.lease.heartbeat_renewed",
                    "payload": {
                        "safe": {
                            "code": "WORKER_LEASE_HEARTBEAT_RENEWED",
                            "run_id": run_id,
                            "step_name": step_name.value,
                            "boundary": boundary,
                            "lease_owner_id": lease_owner_id,
                            "current_fencing_token": lease_fencing_token,
                        }
                    },
                }
            )
            if should_fail and self.fail_point == "commit":
                raise OSError("injected boundary commit failure")
        except Exception as exc:
            self.repository.bind_transaction(None)
            self.audit_sink.bind_transaction(None)
            self.state.audits.append(
                {
                    "event_type": "worker.lease.boundary_commit_failed",
                    "payload": {
                        "safe": {
                            "code": "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
                            "run_id": run_id,
                            "step_name": step_name.value,
                            "boundary": boundary,
                        }
                    },
                }
            )
            raise _WorkerLeaseBoundaryCommitFailed from exc

        self.state.steps = working.steps
        self.state.native_outputs = working.native_outputs
        self.state.audits = working.audits
        self.state.heartbeat_count = working.heartbeat_count
        self.repository.bind_transaction(None)
        self.audit_sink.bind_transaction(None)
        self.commits.append((step_name, boundary, lease_owner_id, lease_fencing_token))


def _stage_summary_native_output_writes(
    *,
    ctx: RunContext,
    step_name: StepName,
    result_summary: dict[str, Any],
) -> tuple[Callable[..., None], ...]:
    """Stage output separately; only the completion UoW is allowed to apply it."""
    assert ctx.run_id
    staged_summary = copy.deepcopy(result_summary)
    content_hash = hashlib.sha256(
        json.dumps(staged_summary, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    def persist(
        *,
        transaction_state: _AtomicBoundaryState,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        assert step_name not in transaction_state.native_outputs
        transaction_state.native_outputs[step_name] = {
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": lease_fencing_token,
            "result_summary": copy.deepcopy(staged_summary),
            "content_hash": content_hash,
        }

    return (persist,)


@dataclass
class _BoundaryProbeDurableState:
    events: list[tuple[str, int]] = field(default_factory=list)


class _BoundaryProbeConnection:
    """Transactional connection probe used against the production UoW builder."""

    def __init__(
        self,
        connection_id: int,
        durable: _BoundaryProbeDurableState,
        *,
        fail_commit: bool = False,
        lease_update_rowcount: int = 1,
        fail_lease_update: bool = False,
    ) -> None:
        self.connection_id = connection_id
        self.durable = durable
        self.fail_commit = fail_commit
        self.lease_update_rowcount = lease_update_rowcount
        self.fail_lease_update = fail_lease_update
        self.pending: list[tuple[str, int]] = []
        self.sql_params: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def record(self, event: str) -> None:
        self.pending.append((event, self.connection_id))

    def execute(self, statement: Any, _params: dict[str, Any] | None = None) -> _Result:
        normalized = _normalized_sql(str(statement))
        self.sql_params.append(dict(_params or {}))
        self.record(f"sql:{normalized}")
        if "UPDATE" in normalized and "RUNS" in normalized:
            if self.fail_lease_update:
                raise OSError("injected lease-renewal database failure")
            if self.lease_update_rowcount == 0:
                return _Result(None, rowcount=0)
            return _Result(
                _leased_row(
                    status="RUNNING",
                    owner_id=FIRST_WORKER_ID,
                    fencing_token=1,
                ),
                rowcount=self.lease_update_rowcount,
            )
        return _Result(SimpleNamespace(ok=True))

    def commit(self) -> None:
        if self.fail_commit:
            raise OSError("injected boundary commit failure")
        self.durable.events.extend(self.pending)
        self.pending.clear()
        self.commits += 1

    def rollback(self) -> None:
        self.pending.clear()
        self.rollbacks += 1


class _BoundaryProbeTransaction:
    def __init__(self, connection: _BoundaryProbeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _BoundaryProbeConnection:
        return self.connection

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> bool:
        try:
            if exc_type is None:
                try:
                    self.connection.commit()
                except Exception:
                    self.connection.rollback()
                    raise
            else:
                self.connection.rollback()
        finally:
            self.connection.closed = True
        return False


def _lease_vep_record() -> RunScopedValidatedEvidencePackageRecord:
    return RunScopedValidatedEvidencePackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_id="55555555-5555-4555-8555-555555555555",
        court_id="44444444-4444-4444-8444-444444444444",
        dashboard_id="66666666-6666-4666-8666-666666666666",
        claim_ids_by_disposition={"TRUSTED": ["claim_mth_0123456789abcdef01234567"]},
        evidence_ids=["evi-1"],
        source_span_ids=["span-1"],
        sanad_ids=["77777777-7777-4777-8777-777777777777"],
        defect_ids=[],
        calc_ids=["calc-1"],
        finding_ids=["finding-c41828c98cf5"],
        finding_types=["contradiction"],
        role_names=["advocate"],
        reason_codes=["GRADE_B"],
        by_disposition={"TRUSTED": 1},
        by_grade={"B": 1},
        by_dashboard_verdict={"SUPPORTED": 1},
        by_finding_type={"contradiction": 1},
        by_reason={"GRADE_B": 1},
        status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
    )


def _migration_function_source(path: Path, function_name: str) -> tuple[str, ast.FunctionDef]:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ),
        None,
    )
    assert function is not None, f"{path.name} is missing {function_name}()"
    return ast.get_source_segment(source, function) or "", function


def _reachable_migration_functions(
    path: Path,
    function_name: str,
) -> tuple[str, tuple[ast.FunctionDef, ...]]:
    """Return a migration entry point and every top-level helper it can call."""
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    assert function_name in functions, f"{path.name} is missing {function_name}()"

    reachable: list[ast.FunctionDef] = []
    pending = [function_name]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited or name not in functions:
            continue
        visited.add(name)
        function = functions[name]
        reachable.append(function)
        pending.extend(
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
        )
    return source, tuple(reachable)


def _reachable_migration_source(path: Path, function_name: str) -> str:
    source, functions = _reachable_migration_functions(path, function_name)
    return "\n".join(ast.get_source_segment(source, function) or "" for function in functions)


def _worker_lease_migration_path() -> Path:
    columns = {
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    candidates: list[Path] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        upgrade_source = _reachable_migration_source(path, "upgrade")
        if all(name in upgrade_source for name in columns):
            candidates.append(path)
    assert len(candidates) == 1, (
        "exactly one reviewed migration must own all four runs lease columns; "
        f"found {[path.name for path in candidates]}"
    )
    return candidates[0]


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _function_assignments(function: ast.FunctionDef) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    assignments[target.id] = value
    return assignments


def _resolve_assigned_node(node: ast.AST, assignments: dict[str, ast.AST]) -> ast.AST:
    visited: set[str] = set()
    while isinstance(node, ast.Name) and node.id in assignments and node.id not in visited:
        visited.add(node.id)
        node = assignments[node.id]
    return node


def _batch_table_bindings(function: ast.FunctionDef) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            context = item.context_expr
            target = item.optional_vars
            if (
                isinstance(context, ast.Call)
                and isinstance(context.func, ast.Attribute)
                and context.func.attr == "batch_alter_table"
                and context.args
                and isinstance(target, ast.Name)
            ):
                table_name = _constant_string(context.args[0])
                if table_name is not None:
                    bindings[target.id] = table_name
    return bindings


def _column_name_from_declaration(column: ast.AST) -> str | None:
    if not isinstance(column, ast.Call):
        return None
    if column.args:
        name = _constant_string(column.args[0])
        if name is not None:
            return name
    return next(
        (
            value
            for keyword in column.keywords
            if keyword.arg == "name" and (value := _constant_string(keyword.value)) is not None
        ),
        None,
    )


def _alembic_column_calls(
    path: Path,
    function_name: str,
    method: str,
) -> dict[tuple[str, str], str]:
    """Collect reachable Alembic column operations and their declaration source."""
    source, functions = _reachable_migration_functions(path, function_name)
    calls: dict[tuple[str, str], str] = {}
    for function in functions:
        assignments = _function_assignments(function)
        batch_bindings = _batch_table_bindings(function)
        for node in ast.walk(function):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != method
                or not node.args
            ):
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in batch_bindings:
                table_name = batch_bindings[receiver.id]
                column_arg = node.args[0]
            elif isinstance(receiver, ast.Name) and receiver.id == "op" and len(node.args) >= 2:
                table_name = _constant_string(node.args[0])
                column_arg = node.args[1]
                if table_name is None:
                    continue
            else:
                continue

            column_arg = _resolve_assigned_node(column_arg, assignments)
            if method == "add_column":
                column_name = _column_name_from_declaration(column_arg)
            else:
                column_name = _constant_string(column_arg)
            if column_name is None:
                continue
            declaration = ast.get_source_segment(source, column_arg) or ""
            calls[(table_name, column_name)] = declaration
    return calls


def _static_string_expression(
    node: ast.AST,
    assignments: dict[str, ast.AST],
) -> str | None:
    node = _resolve_assigned_node(node, assignments)
    value = _constant_string(node)
    if value is not None:
        return value
    if isinstance(node, ast.Call) and node.args:
        return _static_string_expression(node.args[0], assignments)
    return None


def _alembic_check_constraints(path: Path, function_name: str) -> list[tuple[str, str]]:
    constraints: list[tuple[str, str]] = []
    _source, functions = _reachable_migration_functions(path, function_name)
    for function in functions:
        assignments = _function_assignments(function)
        batch_bindings = _batch_table_bindings(function)
        for node in ast.walk(function):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "create_check_constraint"
            ):
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in batch_bindings:
                if len(node.args) < 2:
                    continue
                table_name = batch_bindings[receiver.id]
                condition = node.args[1]
            elif isinstance(receiver, ast.Name) and receiver.id == "op":
                if len(node.args) < 3:
                    continue
                table_name = _constant_string(node.args[1])
                condition = node.args[2]
                if table_name is None:
                    continue
            else:
                continue
            sql = _static_string_expression(condition, assignments)
            if sql is not None:
                constraints.append((table_name, sql))
    return constraints


def _is_coherent_running_lease_constraint(sql: str) -> bool:
    normalized = _normalized_sql(sql)
    # PostgreSQL reflection adds equivalent ::text casts; normalize them instead of distorting DDL.
    normalized = re.sub(r"\(([A-Z_][A-Z0-9_]*)\)::(?:TEXT|CHARACTER VARYING)", r"\1", normalized)
    normalized = re.sub(r"\b([A-Z_][A-Z0-9_]*)::(?:TEXT|CHARACTER VARYING)\b", r"\1", normalized)
    normalized = re.sub(r"(['\"]RUNNING['\"])::(?:TEXT|CHARACTER VARYING)", r"\1", normalized)
    reflected = re.sub(r"[()]", " ", normalized)
    reflected = re.sub(r"\s+", " ", reflected).strip()
    implication_body: str | None = None

    non_running_implication = re.search(
        r"(?:STATUS\s*(?:<>|!=)\s*['\"]RUNNING['\"]|"
        r"NOT\s*\(\s*STATUS\s*=\s*['\"]RUNNING['\"]\s*\))"
        r"\s+OR\s+(?P<body>.+)",
        reflected,
    )
    if non_running_implication is not None:
        implication_body = non_running_implication.group("body")
    else:
        case_implication = re.search(
            r"CASE\s+WHEN\s+STATUS\s*=\s*['\"]RUNNING['\"]\s+THEN\s+"
            r"\(?(?P<body>.+?)\)?\s+ELSE\s+(?:TRUE|1\s*=\s*1)\s+END",
            normalized,
        )
        if case_implication is not None:
            implication_body = case_implication.group("body")

    if implication_body is None or " OR " in implication_body:
        return False

    required_atoms = (
        r"LEASE_OWNER_ID\s+IS\s+NOT\s+NULL",
        r"LEASE_FENCING_TOKEN\s*>\s*0",
        r"LEASE_EXPIRES_AT\s+IS\s+NOT\s+NULL",
        r"LAST_HEARTBEAT_AT\s+IS\s+NOT\s+NULL",
    )
    atom_spans = [re.search(atom, implication_body) for atom in required_atoms]
    if not all(atom_spans):
        return False

    # Presence alone is insufficient: all four predicates must belong to one
    # conjunction.  Three AND operators are the minimum connective count for
    # four required atoms, and any OR inside the implication body is rejected.
    return len(re.findall(r"\bAND\b", implication_body)) >= len(required_atoms) - 1


def _enforces_nonnegative_lease_fencing_token(sql: str) -> bool:
    return bool(
        re.search(
            r"LEASE_FENCING_TOKEN\s*>=\s*0",
            _normalized_sql(sql),
        )
    )


def _function_or_called_local_helper_raises(path: Path, function_name: str) -> bool:
    _source, functions = _reachable_migration_functions(path, function_name)
    return any(isinstance(node, ast.Raise) for function in functions for node in ast.walk(function))


class _MigrationProbeResult:
    def __init__(self, running_count: int) -> None:
        self.running_count = running_count
        self.rowcount = running_count

    def scalar(self) -> int:
        return self.running_count

    def scalar_one(self) -> int:
        return self.running_count

    def scalar_one_or_none(self) -> int:
        return self.running_count

    def fetchone(self) -> tuple[int] | None:
        return (self.running_count,) if self.running_count else None


class _MigrationProbeBind:
    def __init__(self, *, running_count: int, nonzero_token_count: int = 0) -> None:
        self.running_count = running_count
        self.nonzero_token_count = nonzero_token_count
        self.statements: list[str] = []

    def execute(
        self, statement: Any, _params: dict[str, Any] | None = None
    ) -> _MigrationProbeResult:
        self.statements.append(str(statement))
        normalized = _normalized_sql(str(statement))
        if "RUNNING" in normalized and ("SELECT" in normalized or "EXISTS" in normalized):
            return _MigrationProbeResult(self.running_count)
        if "LEASE_FENCING_TOKEN" in normalized and "SELECT" in normalized:
            return _MigrationProbeResult(self.nonzero_token_count)
        return _MigrationProbeResult(0)


class _MigrationBatchProbe:
    def __init__(self, op_probe: _MigrationOpProbe, table_name: str) -> None:
        self.op_probe = op_probe
        self.table_name = table_name

    def __enter__(self) -> _MigrationBatchProbe:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def add_column(self, column: Any) -> None:
        self.op_probe.operations.append(("add_column", self.table_name, str(column.name)))

    def drop_column(self, column_name: str) -> None:
        self.op_probe.operations.append(("drop_column", self.table_name, str(column_name)))

    def __getattr__(self, name: str) -> Callable[..., None]:
        def record(*args: Any, **_kwargs: Any) -> None:
            self.op_probe.operations.append((name, self.table_name, repr(args)))

        return record


class _MigrationOpProbe:
    def __init__(self, bind: _MigrationProbeBind) -> None:
        self.bind = bind
        self.operations: list[tuple[str, str, str]] = []

    def get_bind(self) -> _MigrationProbeBind:
        return self.bind

    def add_column(self, table_name: str, column: Any) -> None:
        self.operations.append(("add_column", table_name, str(column.name)))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.operations.append(("drop_column", table_name, str(column_name)))

    def batch_alter_table(self, table_name: str, **_kwargs: Any) -> _MigrationBatchProbe:
        return _MigrationBatchProbe(self, table_name)

    def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _MigrationProbeResult:
        return self.bind.execute(statement, params)

    def __getattr__(self, name: str) -> Callable[..., None]:
        def record(*args: Any, **_kwargs: Any) -> None:
            table_name = str(args[1] if name.startswith("drop_") and len(args) > 1 else args[0])
            self.operations.append((name, table_name, repr(args)))

        return record


def _load_worker_lease_migration_module() -> Any:
    path = _worker_lease_migration_path()
    spec = importlib.util.spec_from_file_location("slice100_worker_lease_migration_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skip_or_fail_if_no_disposable_postgres() -> None:
    import os

    admin_url_env = "IDIS_DATABASE_ADMIN_URL"
    app_url_env = "IDIS_DATABASE_URL"
    require_postgres_env = "IDIS_REQUIRE_POSTGRES"
    if os.environ.get(admin_url_env) and os.environ.get(app_url_env):
        return
    message = (
        "Task1 migration round trip requires disposable PostgreSQL via "
        f"{admin_url_env} and {app_url_env}"
    )
    if os.environ.get(require_postgres_env, "0") == "1":
        pytest.fail(f"REQUIRED: {message} ({require_postgres_env}=1)")
    pytest.skip(message)


def _create_disposable_migration_engine() -> tuple[Any, Callable[[], None]]:
    """Create an isolated database so lease history from other tests cannot be erased."""
    import os

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    configured = os.environ["IDIS_DATABASE_ADMIN_URL"]
    configured_url = make_url(configured)
    database_name = f"idis_slice100_lease_udu_{uuid.uuid4().hex}"
    assert re.fullmatch(r"idis_slice100_lease_udu_[0-9a-f]{32}", database_name)
    maintenance_database = "postgres" if configured_url.database != "postgres" else "template1"
    maintenance = create_engine(
        configured_url.set(database=maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    with maintenance.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" TEMPLATE template0')
    engine = create_engine(configured_url.set(database=database_name))

    def cleanup() -> None:
        engine.dispose()
        try:
            with maintenance.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            maintenance.dispose()

    return engine, cleanup


def _run_alembic_action(
    config: Any,
    engine: Any,
    action: Callable[[Any, str], None],
    target: str,
) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        action(config, target)


def _postgres_run_columns(engine: Any) -> dict[str, dict[str, Any]]:
    from sqlalchemy import inspect as sqlalchemy_inspect

    with engine.connect() as connection:
        return {
            column["name"]: column for column in sqlalchemy_inspect(connection).get_columns("runs")
        }


def _assert_postgres_worker_lease_schema(engine: Any) -> None:
    from sqlalchemy import BigInteger, DateTime
    from sqlalchemy import inspect as sqlalchemy_inspect
    from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

    columns = _postgres_run_columns(engine)
    assert isinstance(columns["lease_owner_id"]["type"], PostgreSQLUUID)
    token_column = columns["lease_fencing_token"]
    assert isinstance(token_column["type"], BigInteger)
    assert token_column["nullable"] is False
    assert "0" in str(token_column["default"])
    for name in ("lease_expires_at", "last_heartbeat_at"):
        column_type = columns[name]["type"]
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True, f"runs.{name} must be TIMESTAMPTZ"

    with engine.connect() as connection:
        constraints = sqlalchemy_inspect(connection).get_check_constraints("runs")
    assert any(
        _is_coherent_running_lease_constraint(str(constraint.get("sqltext") or ""))
        for constraint in constraints
    ), "PostgreSQL must enforce the coherent RUNNING lease CHECK"
    assert any(
        _enforces_nonnegative_lease_fencing_token(str(constraint.get("sqltext") or ""))
        for constraint in constraints
    ), "PostgreSQL must reject negative lease fencing tokens"


def _postgres_seeded_run_snapshot(engine: Any, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    from sqlalchemy import text

    lease_columns = {
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    column_names = sorted(set(_postgres_run_columns(engine)) - lease_columns)
    projection = ", ".join(f'"{name}"' for name in column_names)
    snapshot: dict[str, dict[str, Any]] = {}
    with engine.connect() as connection:
        for run_id in run_ids:
            row = (
                connection.execute(
                    text(f"SELECT {projection} FROM runs WHERE run_id = CAST(:run_id AS UUID)"),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
            snapshot[run_id] = dict(row)
    return snapshot


def _assert_postgres_seeded_lease_backfill(engine: Any, run_ids: list[str]) -> None:
    from sqlalchemy import text

    with engine.connect() as connection:
        for run_id in run_ids:
            row = (
                connection.execute(
                    text(
                        "SELECT lease_owner_id, lease_fencing_token, lease_expires_at, "
                        "last_heartbeat_at FROM runs WHERE run_id = CAST(:run_id AS UUID)"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
            assert dict(row) == {
                "lease_owner_id": None,
                "lease_fencing_token": 0,
                "lease_expires_at": None,
                "last_heartbeat_at": None,
            }


def _assert_postgres_running_constraint_behavior(engine: Any, run_id: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    valid = {
        "run_id": run_id,
        "owner_id": SECOND_WORKER_ID,
        "token": 1,
        "lease_expires_at": datetime(2030, 1, 1, tzinfo=UTC),
        "last_heartbeat_at": datetime(2029, 12, 31, tzinfo=UTC),
    }
    invalid_cases = {
        "owner": {**valid, "owner_id": None},
        "positive-token": {**valid, "token": 0},
        "expiry": {**valid, "lease_expires_at": None},
        "heartbeat": {**valid, "last_heartbeat_at": None},
    }
    statement = text(
        """
        UPDATE runs
        SET status = 'RUNNING',
            lease_owner_id = CAST(:owner_id AS UUID),
            lease_fencing_token = :token,
            lease_expires_at = :lease_expires_at,
            last_heartbeat_at = :last_heartbeat_at
        WHERE run_id = CAST(:run_id AS UUID)
        """
    )
    for missing_field, params in invalid_cases.items():
        with pytest.raises(IntegrityError), engine.begin() as connection:
            result = connection.execute(statement, params)
            assert result.rowcount == 1, f"seeded run disappeared for {missing_field} probe"

    # Prove the CHECK is an implication, not an unconditional ban on RUNNING.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            result = connection.execute(statement, valid)
            assert result.rowcount == 1
            persisted = (
                connection.execute(
                    text(
                        "SELECT status, lease_owner_id, lease_fencing_token, "
                        "lease_expires_at, last_heartbeat_at FROM runs "
                        "WHERE run_id = CAST(:run_id AS UUID)"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
            assert persisted["status"] == "RUNNING"
            assert str(persisted["lease_owner_id"]) == SECOND_WORKER_ID
            assert persisted["lease_fencing_token"] == 1
            assert persisted["lease_expires_at"] is not None
            assert persisted["last_heartbeat_at"] is not None
        finally:
            transaction.rollback()


class _SimulatedWorkerCrash(BaseException):
    """Abrupt process death after a committed step boundary."""


def _full_documents() -> list[dict[str, Any]]:
    return [
        {
            "document_id": "doc-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]


def _full_lease_context(
    *,
    run_id: str = RUN_ID,
    owner_id: str,
    token: int,
    boundary_uow: Callable[..., None],
    after_boundary_commit: Callable[..., None],
    with_heartbeat: Callable[..., Any] | None = None,
    stage_native_outputs: Callable[..., tuple[Callable[..., None], ...]] = (
        _stage_summary_native_output_writes
    ),
) -> RunContext:
    """Build the same deterministic FULL seam used by the orchestrator regression tests."""
    _require_run_context_lease_fields()
    return RunContext(
        run_id=run_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=_full_documents(),
        deal_metadata={"tenant_id": TENANT_ID, "company_name": "Acme Corp"},
        extract_fn=lambda **_kwargs: {
            "status": "COMPLETED",
            "created_claim_ids": ["claim-001", "claim-002"],
            "chunk_count": 1,
            "unique_claim_count": 2,
            "conflict_count": 0,
        },
        grade_fn=lambda **kwargs: {
            "graded_count": len(kwargs["created_claim_ids"]),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        },
        calc_fn=lambda **_kwargs: {
            "calc_ids": ["calc-001", "calc-002"],
            "reproducibility_hashes": ["hash-aaa", "hash-bbb"],
        },
        enrich_fn=lambda **_kwargs: {
            "provider_count": 0,
            "result_count": 0,
            "blocked_count": 0,
            "enrichment_refs": {},
        },
        debate_fn=lambda **kwargs: {
            "debate_id": kwargs["run_id"],
            "stop_reason": "MAX_ROUNDS",
            "round_number": 5,
            "muhasabah_passed": True,
            "agent_output_count": 10,
        },
        layer2_ic_challenge_fn=lambda **kwargs: {
            "status": "completed",
            "layer2_challenge_ids": ["layer2-001"],
            "source_debate_ids": [str(kwargs["debate_summary"]["debate_id"])],
            "claim_ids": sorted(kwargs["created_claim_ids"]),
            "calc_ids": sorted(kwargs["calc_ids"]),
            "finding_count": 1,
            "unresolved_question_count": 1,
            "muhasabah_passed": True,
        },
        analysis_fn=lambda **kwargs: {
            "agent_count": 8,
            "report_ids": ["report-001"],
            "bundle_id": f"bundle-{kwargs['run_id'][:8]}",
        },
        scoring_fn=lambda **_kwargs: {
            "composite_score": 72.5,
            "band": "MEDIUM",
            "routing": "HOLD",
        },
        deliverables_fn=lambda **_kwargs: {
            "deliverable_count": 4,
            "types": ["IC_MEMO", "QA_BRIEF", "SCREENING_SNAPSHOT", "TRUTH_DASHBOARD"],
            "deliverable_ids": ["del-001", "del-002", "del-003", "del-004"],
        },
        lease_owner_id=owner_id,
        lease_fencing_token=token,
        step_boundary_uow_fn=boundary_uow,
        after_step_boundary_commit_fn=after_boundary_commit,
        execute_with_lease_heartbeat_fn=with_heartbeat
        or (lambda *, operation, **_kwargs: operation()),
        stage_native_output_writes_fn=stage_native_outputs,
        is_cancellation_requested_fn=lambda _run_id, _tenant_id: False,
    )


def _claim_one(
    repository: InMemoryRunsRepository,
    *,
    owner_id: str,
    lease_seconds: int = LEASE_SECONDS,
) -> dict[str, Any]:
    _require_parameters(
        repository.claim_queued_runs,
        {"limit", "lease_owner_id", "lease_seconds"},
        contract="restart lease claim",
    )
    claimed = repository.claim_queued_runs(
        limit=1,
        lease_owner_id=owner_id,
        lease_seconds=lease_seconds,
    )
    assert len(claimed) == 1
    return claimed[0]


def _summary_by_step(steps: list[RunStep]) -> dict[StepName, dict[str, Any]]:
    return {step.step_name: step.result_summary for step in steps}


class _ReopenableResult:
    def __init__(self, rows: list[Any] | None = None, *, rowcount: int | None = None) -> None:
        self.rows = list(rows or [])
        self.rowcount = len(self.rows) if rowcount is None else rowcount

    def fetchone(self) -> Any | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[Any]:
        return list(self.rows)

    def one_or_none(self) -> Any | None:
        assert len(self.rows) <= 1
        return self.fetchone()

    def one(self) -> Any:
        assert len(self.rows) == 1
        return self.rows[0]

    def scalar_one_or_none(self) -> Any | None:
        return self.fetchone()

    def scalar_one(self) -> Any:
        row = self.one()
        if isinstance(row, (str, int, float, bool)):
            return row
        mapping = vars(row) if hasattr(row, "__dict__") else None
        if isinstance(mapping, dict) and len(mapping) == 1:
            return next(iter(mapping.values()))
        return row


@dataclass(frozen=True)
class _LeaseConfigProbe:
    lease_seconds: int = LEASE_SECONDS
    heartbeat_seconds: int = 20


@dataclass
class _TypedResumeTrace:
    active_step: StepName | None = None
    active_loader_contract: Any = None
    load_attempts: list[StepName] = field(default_factory=list)
    load_successes: list[StepName] = field(default_factory=list)
    summary_calls: list[StepName] = field(default_factory=list)
    rehydrate_calls: list[StepName] = field(default_factory=list)
    native_reads: list[tuple[StepName, str]] = field(default_factory=list)
    loaded_outputs: dict[StepName, Any] = field(default_factory=dict)
    canonical_summaries: dict[StepName, dict[str, Any]] = field(default_factory=dict)
    rehydrated_fields: dict[StepName, frozenset[str]] = field(default_factory=dict)
    rehydrated_output_tokens: dict[StepName, frozenset[tuple[str, Any]]] = field(
        default_factory=dict
    )
    consumer_inputs: dict[StepName, dict[str, Any]] = field(default_factory=dict)


_ACTIVE_TYPED_RESUME_TRACE: ContextVar[_TypedResumeTrace | None] = ContextVar(
    "slice100_active_typed_resume_trace",
    default=None,
)
_ACTIVE_DISPATCH_CONSUMER_TRACE: ContextVar[_TypedResumeTrace | None] = ContextVar(
    "slice100_active_dispatch_consumer_trace",
    default=None,
)


REVIEWED_REHYDRATION_CONTEXT_FIELDS: dict[StepName, frozenset[str]] = {
    step_name: frozenset() for step_name in STRICT_V2_STEPS
}
REVIEWED_REHYDRATION_CONTEXT_FIELDS.update(
    {
        StepName.DATA_ROOM_INVENTORY_PACKAGE: frozenset(
            {"data_room_inventory_package", "documents", "preflight_corpus"}
        ),
        StepName.DATA_ROOM_INGESTION_HANDOFF: frozenset({"documents", "preflight_corpus"}),
        StepName.DOCUMENT_PREFLIGHT: frozenset({"documents"}),
        StepName.METHODOLOGY_COVERAGE_INIT: frozenset({"methodology_coverage_records"}),
        StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: frozenset(
            {"methodology_extraction_tasks", "methodology_registry"}
        ),
        StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION: frozenset(
            {"methodology_extraction_execution_result"}
        ),
        StepName.METHODOLOGY_CLAIM_MATERIALIZATION: frozenset({"methodology_materialized_claims"}),
        StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: frozenset(
            {
                "methodology_evidence_items",
                "methodology_evidence_source_provenance",
            }
        ),
        StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: frozenset(
            {
                "methodology_sanad_defects",
                "methodology_sanad_grades",
                "methodology_sanad_links",
                "methodology_sanads",
            }
        ),
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION: frozenset(
            {"methodology_calc_sanads", "methodology_calculations"}
        ),
        StepName.METHODOLOGY_TRUTH_DASHBOARD: frozenset({"methodology_truth_dashboard"}),
        StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE: frozenset(
            {"methodology_company_identity_package"}
        ),
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN: frozenset(
            {"methodology_external_intelligence_conflict_check_plan"}
        ),
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT: frozenset({"methodology_evidence_trust_court"}),
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: frozenset(
            {"methodology_validated_evidence_package"}
        ),
        StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE: frozenset(
            {"methodology_layer2_readiness_package"}
        ),
    }
)

_CONTEXT_BUSINESS_INPUT_FIELDS = frozenset(
    {
        "calc_types",
        "created_by_actor_id",
        "created_by_actor_type",
        "data_room_root_path",
        "deal_id",
        "deal_metadata",
        "documents",
        "mode",
        "preflight_corpus",
        "run_id",
        "strict_preflight_proof_id",
        "tenant_id",
    }
)
_CONTEXT_BUSINESS_FIELDS = _CONTEXT_BUSINESS_INPUT_FIELDS | frozenset().union(
    *REVIEWED_REHYDRATION_CONTEXT_FIELDS.values()
)


def _semantic_json(value: Any) -> str:
    def json_default(item: Any) -> Any:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if hasattr(item, "__dataclass_fields__"):
            return {
                name: getattr(item, name)
                for name in item.__dataclass_fields__
                if not callable(getattr(item, name))
            }
        if isinstance(item, Path):
            return str(item)
        return {"type": _qualified_name(item), "value": str(item)}

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def _context_field_snapshot(
    ctx: RunContext, field_names: set[str] | frozenset[str]
) -> dict[str, str]:
    return {
        name: _semantic_json(getattr(ctx, name))
        for name in sorted(field_names)
        if hasattr(ctx, name)
    }


def _context_resume_snapshot(ctx: RunContext) -> dict[str, str]:
    """Normalize only reviewed business/consumer state, never lease/runtime identity."""
    assert set(REVIEWED_REHYDRATION_CONTEXT_FIELDS) == set(STRICT_V2_STEPS)
    assert set(RunContext.__dataclass_fields__) >= _CONTEXT_BUSINESS_FIELDS
    return _context_field_snapshot(ctx, _CONTEXT_BUSINESS_FIELDS)


def _context_mutation_snapshot(ctx: RunContext) -> dict[str, str]:
    """Include dynamic and lease fields so rehydration cannot hide marker mutations."""
    return {name: _semantic_json(value) for name, value in sorted(vars(ctx).items())}


def _semantic_scalar_tokens(value: Any, *, _seen: set[int] | None = None) -> set[tuple[str, Any]]:
    if value is None or isinstance(value, bool) or callable(value):
        return set()
    if isinstance(value, (str, int, float)):
        return {(type(value).__name__, value)}
    if isinstance(value, Path):
        return {("str", str(value))}
    seen = _seen if _seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return set()
    seen.add(value_id)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _semantic_scalar_tokens(model_dump(mode="json"), _seen=seen)
    if isinstance(value, dict):
        return set().union(
            *(_semantic_scalar_tokens(item, _seen=seen) for item in value.values()),
            set(),
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return set().union(
            *(_semantic_scalar_tokens(item, _seen=seen) for item in value),
            set(),
        )
    if hasattr(value, "__dataclass_fields__"):
        return set().union(
            *(
                _semantic_scalar_tokens(getattr(value, name), _seen=seen)
                for name in value.__dataclass_fields__
            ),
            set(),
        )
    return set()


def _context_semantic_tokens(
    ctx: RunContext,
    *,
    field_names: set[str] | frozenset[str],
) -> set[tuple[str, Any]]:
    return set().union(
        *(
            _semantic_scalar_tokens(getattr(ctx, name))
            for name in field_names
            if hasattr(ctx, name)
        ),
        set(),
    )


def _install_dispatch_consumer_spy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture the exact context/accumulator consumed by every executed step."""
    original_dispatch = RunOrchestrator._dispatch_step

    def tracked_dispatch(
        orchestrator: RunOrchestrator,
        step_name: StepName,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        trace = _ACTIVE_DISPATCH_CONSUMER_TRACE.get()
        if trace is not None:
            assert step_name not in trace.consumer_inputs
            trace.consumer_inputs[step_name] = {
                "context": _context_resume_snapshot(ctx),
                "accumulated": _semantic_json(accumulated),
            }
        return original_dispatch(orchestrator, step_name, ctx, accumulated)

    monkeypatch.setattr(RunOrchestrator, "_dispatch_step", tracked_dispatch)


def _execute_with_consumer_trace(runtime: Any, trace: _TypedResumeTrace) -> Any:
    token = _ACTIVE_DISPATCH_CONSUMER_TRACE.set(trace)
    try:
        return runtime.execute_preclaimed()
    finally:
        _ACTIVE_DISPATCH_CONSUMER_TRACE.reset(token)


def _install_contract_resume_spies(
    monkeypatch: pytest.MonkeyPatch,
    contract_registry: dict[StepName, Any],
) -> None:
    """Record the real contract methods without trusting a self-reported trace."""
    by_type: dict[type[Any], Any] = {}
    for contract in contract_registry.values():
        by_type.setdefault(type(contract), contract)

    for contract_type, exemplar in by_type.items():
        if getattr(contract_type, "_slice100_typed_resume_spy_installed", False):
            continue
        for method_name in ("load_output", "summary_from_output", "rehydrate_context"):
            method = getattr(contract_type, method_name, None)
            assert callable(method), (
                f"{exemplar.step_name.value} contract type is missing {method_name}"
            )

        original_load = contract_type.load_output
        original_summary = contract_type.summary_from_output
        original_rehydrate = contract_type.rehydrate_context

        def tracked_load_output(
            contract_self: Any,
            *args: Any,
            __original: Callable[..., Any] = original_load,
            **kwargs: Any,
        ) -> Any:
            trace = _ACTIVE_TYPED_RESUME_TRACE.get()
            assert trace is not None and trace.active_step == contract_self.step_name
            assert trace.active_loader_contract is None
            trace.load_attempts.append(contract_self.step_name)
            trace.active_loader_contract = contract_self
            try:
                output = __original(contract_self, *args, **kwargs)
            finally:
                trace.active_loader_contract = None
            expected = REVIEWED_STEP_OUTPUT_BINDINGS[contract_self.step_name]
            assert expected.aggregate_type_qname is not None
            assert _qualified_name(output) == expected.aggregate_type_qname
            trace.load_successes.append(contract_self.step_name)
            trace.loaded_outputs[contract_self.step_name] = output
            return output

        def tracked_summary_from_output(
            contract_self: Any,
            *args: Any,
            __original: Callable[..., Any] = original_summary,
            **kwargs: Any,
        ) -> Any:
            trace = _ACTIVE_TYPED_RESUME_TRACE.get()
            assert trace is not None and trace.active_step == contract_self.step_name
            bound = inspect.signature(__original).bind(contract_self, *args, **kwargs)
            output = bound.arguments["output"]
            assert output is trace.loaded_outputs[contract_self.step_name]
            summary = __original(contract_self, *args, **kwargs)
            assert isinstance(summary, dict)
            trace.summary_calls.append(contract_self.step_name)
            trace.canonical_summaries[contract_self.step_name] = copy.deepcopy(summary)
            return summary

        def tracked_rehydrate_context(
            contract_self: Any,
            *args: Any,
            __original: Callable[..., Any] = original_rehydrate,
            **kwargs: Any,
        ) -> Any:
            trace = _ACTIVE_TYPED_RESUME_TRACE.get()
            assert trace is not None and trace.active_step == contract_self.step_name
            bound = inspect.signature(__original).bind(contract_self, *args, **kwargs)
            assert bound.arguments["output"] is trace.loaded_outputs[contract_self.step_name]
            ctx = bound.arguments["ctx"]
            assert ctx.tenant_id == TENANT_ID
            assert ctx.deal_id == DEAL_ID
            assert ctx.run_id == RUN_ID
            before_state = _context_mutation_snapshot(ctx)
            before_tokens = _context_semantic_tokens(
                ctx,
                field_names=_CONTEXT_BUSINESS_FIELDS,
            )
            output_tokens = _semantic_scalar_tokens(bound.arguments["output"])
            result = __original(contract_self, *args, **kwargs)
            after_state = _context_mutation_snapshot(ctx)
            after_tokens = _context_semantic_tokens(
                ctx,
                field_names=_CONTEXT_BUSINESS_FIELDS,
            )
            changed_fields = frozenset(
                name
                for name in before_state.keys() | after_state.keys()
                if before_state.get(name) != after_state.get(name)
            )
            reviewed_fields = REVIEWED_REHYDRATION_CONTEXT_FIELDS[contract_self.step_name]
            assert changed_fields <= reviewed_fields, (
                f"{contract_self.step_name.value} rehydrate_context mutated unreviewed "
                f"context fields {sorted(changed_fields - reviewed_fields)}; arbitrary "
                "marker, lease, and runtime fields cannot satisfy semantic rehydration"
            )
            transferred_output_tokens = frozenset((after_tokens - before_tokens) & output_tokens)
            if changed_fields:
                assert transferred_output_tokens, (
                    f"{contract_self.step_name.value} rehydrate_context changed reviewed "
                    "business state but transferred no semantic value from the loaded typed "
                    "output"
                )
            trace.rehydrate_calls.append(contract_self.step_name)
            trace.rehydrated_fields[contract_self.step_name] = changed_fields
            trace.rehydrated_output_tokens[contract_self.step_name] = transferred_output_tokens
            return result

        monkeypatch.setattr(contract_type, "load_output", tracked_load_output)
        monkeypatch.setattr(contract_type, "summary_from_output", tracked_summary_from_output)
        monkeypatch.setattr(contract_type, "rehydrate_context", tracked_rehydrate_context)
        monkeypatch.setattr(
            contract_type,
            "_slice100_typed_resume_spy_installed",
            True,
            raising=False,
        )


def _read_reopenable_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_reopenable_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":"), default=str),
        encoding="utf-8",
    )


class _ReopenablePostgresConnection:
    """A transaction-shaped, file-backed substrate; no Python state crosses a restart."""

    def __init__(
        self,
        *,
        path: Path,
        connection_id: str,
        forbidden_connection_ids: set[str],
    ) -> None:
        assert connection_id not in forbidden_connection_ids
        self.path = path
        self.connection_id = connection_id
        self.working = _read_reopenable_state(path)
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.rls_tenants: list[str] = []
        self.resume_reads: list[dict[str, Any]] = []
        self.active_native_write: dict[str, Any] | None = None

    def _record_bound_resume_read(
        self,
        *,
        table: str,
        sql: str,
        values: dict[str, Any],
    ) -> None:
        assert self.rls_tenants and self.rls_tenants[-1] == TENANT_ID, (
            f"reopened {table} read did not establish tenant-local RLS"
        )
        upper_params = {str(name).upper(): value for name, value in values.items()}

        def require_exact_predicate(column: str, expected: str) -> None:
            persisted_column = (
                "REQUEST_ID" if table == "audit_events" and column == "RUN_ID" else column
            )
            forward = re.search(rf"\b{persisted_column}\s*=\s*:([A-Z0-9_]+)", sql)
            reverse = re.search(
                rf":([A-Z0-9_]+)\s*=\s*\b{persisted_column}\b",
                sql,
            )
            predicate = forward or reverse
            # Tenant-local RLS is the reviewed binding; redundant tenant predicates are optional.
            if column == "TENANT_ID" and predicate is None:
                assert self.rls_tenants[-1] == expected
                return
            assert predicate is not None, (
                f"reopened {table} read lacks an exact {column.lower()} bind predicate"
            )
            parameter_name = predicate.group(1).upper()
            assert parameter_name in upper_params
            assert str(upper_params[parameter_name]).upper() == expected.upper()

        require_exact_predicate("TENANT_ID", TENANT_ID)
        require_exact_predicate("RUN_ID", RUN_ID)
        trace = _ACTIVE_TYPED_RESUME_TRACE.get()
        if table == "run_steps":
            assert trace is None or trace.active_loader_contract is None, (
                "typed load_output attempted to reconstruct state from RunStep.result_summary"
            )
        elif table in {"runs", "audit_events"}:
            # Lifecycle/cancellation reads are lease control, not Task 7 output loading.
            pass
        else:
            assert trace is not None and trace.active_step is not None, (
                f"reopened {table} read occurred outside typed contract load_output"
            )
            assert trace.active_loader_contract is not None
            assert trace.active_loader_contract.step_name == trace.active_step
            candidates = [
                binding
                for binding in REVIEWED_STEP_OUTPUT_BINDINGS[
                    trace.active_step
                ].all_source_grounded_bindings
                if table in binding.sql_targets
            ]
            assert candidates, (
                f"{trace.active_step.value} load_output read unreviewed target {table!r}"
            )
            stack = inspect.stack()[1:]
            assert any(
                frame.function in binding.load_methods
                and frame.frame.f_locals.get("self") is not None
                and _qualified_name(frame.frame.f_locals["self"]) == binding.repository_type_qname
                and frame.frame.f_code
                is inspect.unwrap(
                    getattr(
                        _resolve_qualified_name(binding.repository_type_qname),
                        frame.function,
                    )
                ).__code__
                for binding in candidates
                for frame in stack
            ), (
                f"{trace.active_step.value} read {table!r} outside its exact reviewed "
                "repository loader"
            )
            trace.native_reads.append((trace.active_step, table))
        self.resume_reads.append(
            {
                "table": table,
                "tenant_id": TENANT_ID,
                "run_id": RUN_ID,
                "params": copy.deepcopy(values),
            }
        )

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _ReopenableResult:
        sql = _normalized_sql(str(statement))
        values = dict(params or {})
        upper_values = {str(value).upper() for value in values.values()}
        run = self.working["run"]

        if sql.startswith("SET LOCAL IDIS.TENANT_ID"):
            tenant_match = re.search(
                r"SET LOCAL IDIS\.TENANT_ID\s*=\s*['\"]([^'\"]+)['\"]",
                sql,
            )
            assert tenant_match is not None
            assert tenant_match.group(1).lower() == TENANT_ID
            self.rls_tenants.append(TENANT_ID)
            return _ReopenableResult(rowcount=1)

        # Guarded run_steps DML mentions runs in EXISTS; classify only the UPDATE target.
        if re.search(r"\bUPDATE\s+RUNS\b", sql) and "LEASE_FENCING_TOKEN" in sql:
            terminal_status = next(
                (
                    value
                    for value in values.values()
                    if str(value).upper() in {"SUCCEEDED", "FAILED", "CANCELLED"}
                ),
                None,
            )
            if "LEASE_OWNER_ID = NULL" in sql and terminal_status is None:
                if (
                    run["status"] != "RUNNING"
                    or TENANT_ID.upper() not in upper_values
                    or not str(run["lease_expires_at"] or "").startswith("2000-")
                ):
                    return _ReopenableResult(rowcount=0)
                prior_owner = run["lease_owner_id"]
                prior_token = int(run["lease_fencing_token"])
                run.update(
                    status="QUEUED",
                    lease_owner_id=None,
                    lease_fencing_token=prior_token + 1,
                    lease_expires_at=None,
                )
                return _ReopenableResult(
                    [
                        SimpleNamespace(
                            **run,
                            prior_lease_owner_id=prior_owner,
                            prior_fencing_token=prior_token,
                        )
                    ],
                    rowcount=1,
                )

            target_owner = next(
                (
                    value
                    for name, value in values.items()
                    if "OWNER" in str(name).upper() and value is not None
                ),
                None,
            )
            if target_owner is not None and "QUEUED" in sql:
                if run["status"] != "QUEUED" or TENANT_ID.upper() not in upper_values:
                    return _ReopenableResult(rowcount=0)
                run.update(
                    status="RUNNING",
                    lease_owner_id=str(target_owner),
                    lease_fencing_token=int(run["lease_fencing_token"]) + 1,
                    lease_expires_at="2026-07-15T12:03:00Z",
                    last_heartbeat_at="2026-07-15T12:02:00Z",
                    started_at=run.get("started_at") or "2026-07-15T12:02:00Z",
                )
                return _ReopenableResult([SimpleNamespace(**run)], rowcount=1)

            exact_lease = {
                str(run["tenant_id"]).upper(),
                str(run["run_id"]).upper(),
                str(run["lease_owner_id"]).upper(),
                str(run["lease_fencing_token"]).upper(),
            }
            if run["status"] != "RUNNING" or not exact_lease <= upper_values:
                return _ReopenableResult(rowcount=0)
            if terminal_status is not None:
                run.update(
                    status=str(terminal_status).upper(),
                    lease_owner_id=None,
                    lease_expires_at=None,
                    finished_at="2026-07-15T12:04:00Z",
                )
                return _ReopenableResult([SimpleNamespace(**run)], rowcount=1)
            run["last_heartbeat_at"] = "2026-07-15T12:00:00Z"
            run["lease_expires_at"] = "2026-07-15T12:01:00Z"
            return _ReopenableResult([SimpleNamespace(**run)], rowcount=1)

        if "INSERT INTO RUN_STEPS" in sql:
            row = dict(values)
            summary = row.get("result_summary", {})
            row["result_summary"] = json.loads(summary) if isinstance(summary, str) else summary
            self.working["steps"][str(row["step_id"])] = row
            return _ReopenableResult(rowcount=1)

        if "UPDATE RUN_STEPS" in sql:
            row = self.working["steps"][str(values["step_id"])]
            row.update(values)
            summary = row.get("result_summary", {})
            row["result_summary"] = json.loads(summary) if isinstance(summary, str) else summary
            return _ReopenableResult(rowcount=1)

        if "SELECT" in sql and "FROM RUN_STEPS" in sql:
            self._record_bound_resume_read(table="run_steps", sql=sql, values=values)
            rows = list(self.working["steps"].values())
            rows = [
                row
                for row in rows
                if str(row["tenant_id"]) == TENANT_ID and str(row["run_id"]) == RUN_ID
            ]
            if "step_name" in values:
                rows = [row for row in rows if row["step_name"] == values["step_name"]]
            rows.sort(key=lambda row: int(row["step_order"]))
            return _ReopenableResult([SimpleNamespace(**row) for row in rows])

        if "SELECT" in sql and "FROM RUNS" in sql:
            self._record_bound_resume_read(table="runs", sql=sql, values=values)
            if str(run["run_id"]) != str(values.get("run_id", run["run_id"])):
                return _ReopenableResult()
            return _ReopenableResult([SimpleNamespace(**run)])

        if "SELECT" in sql and "FROM AUDIT_EVENTS" in sql:
            self._record_bound_resume_read(table="audit_events", sql=sql, values=values)
            matches: list[dict[str, Any]] = []
            for event in self.working["audits"]:
                payload = event.get("payload")
                safe = payload.get("safe") if isinstance(payload, dict) else None
                if not isinstance(safe, dict):
                    continue
                if (
                    event.get("tenant_id") == values.get("tenant_id")
                    and event.get("event_type") == values.get("event_type")
                    and safe.get("run_id") == values.get("run_id")
                    and safe.get("step_id") == values.get("step_id")
                    and safe.get("step_name") == values.get("step_name")
                ):
                    matches.append(event)
            matches.sort(
                key=lambda event: (
                    str(event.get("occurred_at") or ""),
                    str(event.get("event_id") or ""),
                ),
                reverse=True,
            )
            rows = [SimpleNamespace(event=copy.deepcopy(event)) for event in matches[:1]]
            return _ReopenableResult(rows)

        if "SELECT" in sql and " FROM " in sql:
            relation_targets = _sql_relation_targets(sql)
            assert relation_targets, f"SELECT has no concrete FROM/JOIN target: {sql}"
            for relation_target in relation_targets:
                self._record_bound_resume_read(
                    table=relation_target,
                    sql=sql,
                    values=values,
                )
            table = relation_targets[0]
            if table:
                native_rows: list[Any] = []
                for write in self.working["native_writes"]:
                    write_sql = write["sql"]
                    writes_table = re.search(
                        r"\b(?:INTO|UPDATE)\s+([A-Z0-9_\".]+)",
                        write_sql,
                    )
                    if (
                        writes_table is not None
                        and writes_table.group(1).replace('"', "").split(".")[-1].lower() == table
                        and str(write["params"].get("tenant_id")) == TENANT_ID
                        and str(write["params"].get("run_id")) == RUN_ID
                    ):
                        native_rows.append(SimpleNamespace(**write["params"]))
                return _ReopenableResult(native_rows)

        if "INSERT" in sql or "UPDATE" in sql or "DELETE" in sql:
            assert self.active_native_write is not None, (
                f"untyped/unscoped native mutation reached the restart substrate: {sql}"
            )
            assert "DELETE" not in sql, "native completion output may not be represented by DELETE"
            contract = self.active_native_write["contract"]
            binding = self.active_native_write["binding"]
            expected_step = REVIEWED_STEP_OUTPUT_BINDINGS[contract.step_name]
            expected_fingerprints = {
                _reviewed_binding_fingerprint(item)
                for item in expected_step.produced_write_bindings
            }
            assert _contract_binding_fingerprint(binding) in expected_fingerprints, (
                f"{contract.step_name.value} used an unreviewed native-output binding"
            )
            assert binding.persist_method is not None
            reviewed_method = inspect.unwrap(
                getattr(binding.repository_type, binding.persist_method)
            )
            repository_frames = [
                frame
                for frame in inspect.stack()[1:]
                if frame.function == binding.persist_method
                and type(frame.frame.f_locals.get("self")) is binding.repository_type
                and frame.frame.f_code is reviewed_method.__code__
            ]
            assert repository_frames, (
                f"{contract.step_name.value} output bypassed its declared production repository "
                f"{binding.repository_type.__name__}.{binding.persist_method}"
            )
            assert TENANT_ID.upper() in upper_values
            assert DEAL_ID.upper() in upper_values
            assert RUN_ID.upper() in upper_values
            target_match = re.search(r"\b(?:INTO|UPDATE)\s+([A-Z0-9_\".]+)", sql)
            assert target_match is not None, f"native output SQL has no concrete target: {sql}"
            target = target_match.group(1).replace('"', "").split(".")[-1].lower()
            assert target in binding.sql_targets, (
                f"{contract.step_name.value} emitted SQL to unreviewed target {target!r}; "
                f"binding permits only {sorted(binding.sql_targets)}"
            )
            self.working["native_writes"].append(
                {
                    "sql": sql,
                    "params": json.loads(json.dumps(values, default=str)),
                    "step_name": contract.step_name.value,
                    "output_type_id": contract.output_type_id,
                    "persistence_target": contract.persistence_target,
                    "row_model_type": (
                        _qualified_name(binding.row_model_type)
                        if binding.row_model_type is not None
                        else None
                    ),
                    "repository_type": _qualified_name(binding.repository_type),
                    "persist_method": binding.persist_method,
                    "sql_target": target,
                    "output_payload": copy.deepcopy(self.active_native_write["output_payload"]),
                }
            )
            return _ReopenableResult(rowcount=1)

        return _ReopenableResult()

    def commit(self) -> None:
        _write_reopenable_state(self.path, self.working)
        self.commits += 1

    def rollback(self) -> None:
        self.working = _read_reopenable_state(self.path)
        self.rollbacks += 1


class _ReopenablePostgresTransaction:
    def __init__(self, connection: _ReopenablePostgresConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _ReopenablePostgresConnection:
        return self.connection

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> bool:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.closed = True
        return False


class _ReopenableConnectionFactory:
    def __init__(
        self,
        *,
        path: Path,
        process_id: str,
        forbidden_connection_ids: set[str] | None = None,
    ) -> None:
        self.path = path
        self.process_id = process_id
        self.forbidden_connection_ids = set(forbidden_connection_ids or set())
        self.connections: list[_ReopenablePostgresConnection] = []

    def __call__(self) -> _ReopenablePostgresTransaction:
        connection = _ReopenablePostgresConnection(
            path=self.path,
            connection_id=f"{self.process_id}-{len(self.connections) + 1}",
            forbidden_connection_ids=self.forbidden_connection_ids,
        )
        self.connections.append(connection)
        return _ReopenablePostgresTransaction(connection)


class _ReopenableAuditSink:
    def __init__(self) -> None:
        self.out_of_transaction_events: list[dict[str, Any]] = []

    def emit_in_tx(
        self,
        conn: _ReopenablePostgresConnection,
        event: dict[str, Any],
    ) -> None:
        conn.working["audits"].append(copy.deepcopy(event))

    def emit(self, event: dict[str, Any]) -> None:
        self.out_of_transaction_events.append(copy.deepcopy(event))


def _typed_output_payload(output: Any) -> dict[str, Any]:
    model_dump = getattr(output, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
    elif hasattr(output, "__dataclass_fields__"):
        payload = {
            name: copy.deepcopy(getattr(output, name)) for name in output.__dataclass_fields__
        }
    else:
        raise AssertionError(
            f"native output {type(output)!r} is not a concrete typed model/dataclass"
        )
    assert isinstance(payload, dict)
    return json.loads(json.dumps(payload, default=str))


def _claimed_runtime_provider_context_factory(
    *,
    validation_calls: list[StepName],
    resume_trace: _TypedResumeTrace,
    crash_after_step: StepName | None,
) -> Callable[..., RunContext]:
    def build(**components: Any) -> RunContext:
        required = {
            "lease_owner_id",
            "lease_fencing_token",
            "step_boundary_uow_fn",
            "stage_native_output_writes_fn",
            "completed_step_resume_validator",
            "execute_with_lease_heartbeat_fn",
        }
        assert required <= set(components)
        production_validator = components["completed_step_resume_validator"]
        production_stager = components["stage_native_output_writes_fn"]
        contract_registry = getattr(production_stager, "contract_registry", None)
        assert isinstance(contract_registry, dict)
        assert set(contract_registry) == set(STRICT_V2_STEPS)

        def track_production_validation(ctx: RunContext, step: RunStep) -> Any:
            validation_calls.append(step.step_name)
            assert resume_trace.active_step is None
            before = (
                len(resume_trace.load_attempts),
                len(resume_trace.load_successes),
                len(resume_trace.summary_calls),
                len(resume_trace.rehydrate_calls),
                len(resume_trace.native_reads),
            )
            resume_trace.active_step = step.step_name
            token = _ACTIVE_TYPED_RESUME_TRACE.set(resume_trace)
            try:
                result = production_validator(ctx, step)
            finally:
                _ACTIVE_TYPED_RESUME_TRACE.reset(token)
                resume_trace.active_step = None
            assert resume_trace.load_attempts[before[0] :] == [step.step_name]
            assert resume_trace.load_successes[before[1] :] == [step.step_name]
            assert resume_trace.summary_calls[before[2] :] == [step.step_name]
            assert resume_trace.rehydrate_calls[before[3] :] == [step.step_name]
            native_reads = resume_trace.native_reads[before[4] :]
            assert native_reads and {read_step for read_step, _table in native_reads} == {
                step.step_name
            }, f"{step.step_name.value} load_output performed no bound native-row read"
            assert resume_trace.canonical_summaries[step.step_name] == step.result_summary, (
                f"{step.step_name.value} durable typed output does not reproduce the "
                "persisted canonical summary"
            )
            return result

        def crash_only_after_committed_boundary(**boundary: Any) -> None:
            if (
                crash_after_step is not None
                and boundary["boundary"] == "COMPLETE"
                and boundary["step_name"] == crash_after_step
            ):
                raise _SimulatedWorkerCrash

        def tracked_production_stager(**stage: Any) -> tuple[Callable[..., None], ...]:
            step_name = stage["step_name"]
            contract = contract_registry[step_name]
            expected_output_type, expected_target = EXPECTED_NATIVE_OUTPUT_CONTRACTS[step_name]
            assert contract.step_name == step_name
            assert contract.output_type_id == expected_output_type
            assert contract.persistence_target == expected_target
            _assert_contract_matches_reviewed_binding(step_name, contract)
            expected_binding_fingerprints = {
                _reviewed_binding_fingerprint(item)
                for item in REVIEWED_STEP_OUTPUT_BINDINGS[step_name].produced_write_bindings
            }
            production_writes = tuple(production_stager(**stage))
            assert production_writes, f"{step_name.value} returned no native-output write set"
            staged_binding_fingerprints: set[tuple[Any, ...]] = set()
            for write in production_writes:
                assert not inspect.isfunction(write), (
                    f"{step_name.value} returned an untyped raw closure instead of a staged write"
                )
                assert write.contract is contract
                assert write.binding in contract.write_bindings
                binding_fingerprint = _contract_binding_fingerprint(write.binding)
                assert binding_fingerprint in expected_binding_fingerprints
                staged_binding_fingerprints.add(binding_fingerprint)
                if write.binding.row_model_type is not None:
                    assert isinstance(write.output, write.binding.row_model_type)
                output_payload = _typed_output_payload(write.output)
                assert str(output_payload.get("tenant_id")) == TENANT_ID
                assert str(output_payload.get("deal_id")) == DEAL_ID
                assert str(output_payload.get("run_id")) == RUN_ID
                assert callable(write.persist)
            assert staged_binding_fingerprints == expected_binding_fingerprints, (
                f"{step_name.value} did not stage the exact reviewed binding set"
            )

            def persist_production_write_set(
                *,
                db_conn: _ReopenablePostgresConnection,
                lease_owner_id: str,
                lease_fencing_token: int,
                **write_context: Any,
            ) -> None:
                before = len(db_conn.working["native_writes"])
                for write in production_writes:
                    write_before = len(db_conn.working["native_writes"])
                    assert db_conn.active_native_write is None
                    db_conn.active_native_write = {
                        "contract": contract,
                        "binding": write.binding,
                        "output_payload": _typed_output_payload(write.output),
                    }
                    try:
                        write.persist(
                            db_conn=db_conn,
                            lease_owner_id=lease_owner_id,
                            lease_fencing_token=lease_fencing_token,
                            **write_context,
                        )
                    finally:
                        db_conn.active_native_write = None
                    assert len(db_conn.working["native_writes"]) > write_before, (
                        f"{step_name.value} typed writer emitted no repository-backed DML"
                    )
                after = len(db_conn.working["native_writes"])
                assert after > before, (
                    f"{step_name.value} staged writers produced no durable native mutation"
                )
                for persisted in db_conn.working["native_writes"][before:after]:
                    assert persisted["step_name"] == step_name.value
                    assert persisted["output_type_id"] == expected_output_type
                    assert persisted["persistence_target"] == expected_target
                persisted_slice = db_conn.working["native_writes"][before:after]
                for expected_binding in REVIEWED_STEP_OUTPUT_BINDINGS[
                    step_name
                ].produced_write_bindings:
                    matching = [
                        item
                        for item in persisted_slice
                        if item["row_model_type"] == expected_binding.row_type_qname
                        and item["repository_type"] == expected_binding.repository_type_qname
                        and item["persist_method"] == expected_binding.persist_method
                    ]
                    assert {item["sql_target"] for item in matching} == set(
                        expected_binding.sql_targets
                    ), (
                        f"{step_name.value} did not emit the exact reviewed SQL targets for "
                        f"{expected_binding.repository_type_qname}."
                        f"{expected_binding.persist_method}"
                    )
                    assert not expected_binding.non_sql_methods, (
                        f"{step_name.value} non-SQL methods require the still-gated exact "
                        "object-store/graph recording substrate"
                    )
                db_conn.working["native_writes_by_step"][step_name.value] = list(
                    range(before, after)
                )

            return (persist_production_write_set,)

        tracked_production_stager.production_stager = production_stager  # type: ignore[attr-defined]
        tracked_production_stager.contract_registry = contract_registry  # type: ignore[attr-defined]

        context = _full_lease_context(
            owner_id=components["lease_owner_id"],
            token=components["lease_fencing_token"],
            boundary_uow=components["step_boundary_uow_fn"],
            after_boundary_commit=crash_only_after_committed_boundary,
            with_heartbeat=components["execute_with_lease_heartbeat_fn"],
            stage_native_outputs=tracked_production_stager,
        )
        context.strict_resume_validation_required = True
        context.completed_step_resume_validator = track_production_validation
        return context

    return build


def _initial_reopenable_claimed_state(*, owner_id: str, token: int) -> dict[str, Any]:
    return {
        "run": {
            "run_id": RUN_ID,
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "mode": "FULL",
            "status": "RUNNING",
            "source": {"type": "deal_documents", "document_ids": []},
            "started_at": "2026-07-15T12:00:00Z",
            "finished_at": None,
            "created_at": "2026-07-15T11:59:00Z",
            "cancel_requested_at": None,
            "created_by_actor_id": "originating-human",
            "created_by_actor_type": "HUMAN",
            "strict_preflight_proof_id": None,
            "lease_owner_id": owner_id,
            "lease_fencing_token": token,
            "lease_expires_at": "2026-07-15T12:01:00Z",
            "last_heartbeat_at": "2026-07-15T12:00:00Z",
        },
        "steps": {},
        "native_writes": [],
        "native_writes_by_step": {},
        "audits": [],
    }


def _poison_semantic_value(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, str):
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            if re.fullmatch(r"[0-9a-fA-F]{32,128}", value):
                replacement = "0" if value[0].lower() != "0" else "1"
                return replacement + value[1:]
            return f"{value}-semantic-poison"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slice100-poison:{parsed}"))
    if isinstance(value, list):
        return [*value, "slice100-semantic-poison"]
    if isinstance(value, dict):
        return {**value, "_slice100_semantic_poison": True}
    if value is None:
        return "slice100-semantic-poison"
    raise AssertionError(f"no shape-preserving semantic poison for {type(value)!r}")


def _poison_bound_native_content(
    state: dict[str, Any],
    step_name: StepName,
) -> tuple[int, str, Any, Any]:
    excluded = {
        "tenant_id",
        "deal_id",
        "run_id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "lease_owner_id",
        "lease_fencing_token",
        "lease_expires_at",
        "last_heartbeat_at",
    }
    indices = tuple(state["native_writes_by_step"][step_name.value])
    assert indices
    for index in indices:
        write = state["native_writes"][index]
        params = write["params"]
        output_payload = write["output_payload"]
        shared_keys = [
            key
            for key in params.keys() & output_payload.keys()
            if key not in excluded and params[key] == output_payload[key]
        ]
        shared_keys.sort(key=lambda key: (key.endswith("_id"), key))
        for key in shared_keys:
            try:
                poisoned = _poison_semantic_value(params[key])
            except AssertionError:
                continue
            original = copy.deepcopy(params[key])
            assert poisoned != original
            params[key] = copy.deepcopy(poisoned)
            output_payload[key] = copy.deepcopy(poisoned)
            assert params["tenant_id"] == TENANT_ID
            assert params["deal_id"] == DEAL_ID
            assert params["run_id"] == RUN_ID
            return index, key, original, poisoned
    raise AssertionError(
        f"{step_name.value} has no bound native field available for semantic poison"
    )


def _step_semantics(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(state["steps"].values(), key=lambda row: row["step_order"])
    return [
        {
            "step_name": row["step_name"],
            "step_order": row["step_order"],
            "status": row["status"],
            "retry_count": row["retry_count"],
            "result_summary": row["result_summary"],
            "error_code": row.get("error_code"),
        }
        for row in rows
    ]


def _native_write_semantics(state: dict[str, Any]) -> list[dict[str, Any]]:
    volatile_keys = {
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "last_heartbeat_at",
        "lease_expires_at",
        "lease_fencing_token",
        "lease_owner_id",
        "event_id",
    }
    return [
        {
            "sql": write["sql"],
            "step_name": write["step_name"],
            "output_type_id": write["output_type_id"],
            "persistence_target": write["persistence_target"],
            "row_model_type": write["row_model_type"],
            "repository_type": write["repository_type"],
            "persist_method": write["persist_method"],
            "sql_target": write["sql_target"],
            "output_payload": {
                key: value
                for key, value in write["output_payload"].items()
                if key.lower() not in volatile_keys
            },
            "params": {
                key: value
                for key, value in write["params"].items()
                if key.lower() not in volatile_keys
                and not (
                    "lease" in key.lower()
                    and any(
                        fragment in key.lower()
                        for fragment in ("owner", "token", "expires", "heartbeat")
                    )
                )
            },
        }
        for write in state["native_writes"]
    ]


def _build_lease_event(code: str) -> dict[str, Any]:
    builder = getattr(worker_module, "build_worker_lease_audit_event", None)
    assert callable(builder), "worker is missing its schema-safe lease audit builder"
    _require_parameters(
        builder,
        {
            "code",
            "tenant_id",
            "deal_id",
            "run_id",
            "lease_owner_id",
            "prior_fencing_token",
            "new_fencing_token",
            "lease_expires_at",
            "step_name",
            "boundary",
        },
        contract="worker lease audit builder",
    )
    return builder(
        code=code,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        lease_owner_id=FIRST_WORKER_ID,
        prior_fencing_token=1,
        new_fencing_token=2,
        lease_expires_at="2026-07-15T12:01:00Z",
        step_name=StepName.INGEST_CHECK.value,
        boundary="COMPLETE",
    )


# Private names are intentionally shared across the thematic contract modules.
__all__ = [name for name in globals() if not name.startswith("__")]
