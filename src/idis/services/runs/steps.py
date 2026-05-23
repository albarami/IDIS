"""Shared run step wiring for canonical execution."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from idis.audit.sink import AuditSink
from idis.methodology.models import MethodologyRegistry
from idis.models.claim_materialization import (
    MethodologyOutputClaimMaterializationRunResult,
    RunScopedMaterializedClaim,
)
from idis.models.evidence_item_materialization import (
    MethodologyEvidenceItemMaterializationRunResult,
    RunScopedEvidenceItemRecord,
)
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionRunResult,
)
from idis.models.extraction_task import ExtractionTask, ExtractionTaskPlanningRunResult
from idis.models.methodology_coverage import (
    MethodologyCoverageInitializationResult,
    MethodologyCoverageRecord,
)
from idis.models.run_source import RunSource
from idis.models.sanad_materialization import (
    MethodologySanadMaterializationRunResult,
    RunScopedSanadDefectRecord,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
)
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.runs.methodology_coverage_init import load_default_methodology_registry
from idis.services.runs.orchestrator import RunContext

CoverageInitFn = Callable[
    ...,
    tuple[MethodologyCoverageInitializationResult, list[MethodologyCoverageRecord]],
]
TaskPlanningFn = Callable[
    ...,
    tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]],
]
TaskExecutionFn = Callable[
    ...,
    tuple[MethodologyExtractionExecutionRunResult, MethodologyExtractionExecutionResult],
]
ClaimMaterializationFn = Callable[
    ...,
    tuple[MethodologyOutputClaimMaterializationRunResult, list[RunScopedMaterializedClaim]],
]
EvidenceItemMaterializationFn = Callable[
    ...,
    tuple[MethodologyEvidenceItemMaterializationRunResult, list[RunScopedEvidenceItemRecord]],
]
SanadCreationLinkingGradingFn = Callable[
    ...,
    tuple[
        MethodologySanadMaterializationRunResult,
        list[RunScopedSanadRecord],
        list[RunScopedSanadLinkRecord],
        list[RunScopedSanadGradeRecord],
        list[RunScopedSanadDefectRecord],
    ],
]


def build_run_context(
    *,
    db_conn: Any,
    tenant_id: str,
    run_id: str,
    deal_id: str,
    mode: str,
    documents: list[dict[str, Any]],
    deal_metadata: dict[str, Any] | None = None,
    data_room_root_path: str | None = None,
    preflight_corpus: list[dict[str, Any]] | None = None,
    audit_sink: AuditSink,
    methodology_registry: MethodologyRegistry | None = None,
    methodology_registry_loader_fn: Callable[[], MethodologyRegistry] | None = None,
    methodology_coverage_init_fn: CoverageInitFn | None = None,
    methodology_extraction_task_planning_fn: TaskPlanningFn | None = None,
    methodology_extraction_task_execution_fn: TaskExecutionFn | None = None,
    methodology_claim_materialization_fn: ClaimMaterializationFn | None = None,
    methodology_evidence_item_materialization_fn: EvidenceItemMaterializationFn | None = None,
    methodology_sanad_creation_linking_grading_fn: SanadCreationLinkingGradingFn | None = None,
) -> RunContext:
    """Build a RunContext with the canonical step callables.

    The callables still delegate to the existing route helper implementations
    until Phase 2.1 finishes extracting all route-local wiring.
    """
    from idis.api.routes.runs import (
        _run_full_analysis,
        _run_full_debate,
        _run_full_deliverables,
        _run_full_enrichment,
        _run_full_scoring,
        _run_snapshot_auto_grade,
        _run_snapshot_calc,
        _run_snapshot_extraction,
    )

    _ = audit_sink
    is_full = mode == "FULL"
    return RunContext(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode=mode,
        documents=documents,
        deal_metadata=deal_metadata,
        data_room_root_path=data_room_root_path,
        preflight_corpus=preflight_corpus or documents,
        methodology_registry=methodology_registry,
        methodology_registry_loader_fn=methodology_registry_loader_fn
        or load_default_methodology_registry,
        methodology_coverage_init_fn=methodology_coverage_init_fn,
        methodology_extraction_task_planning_fn=methodology_extraction_task_planning_fn,
        methodology_extraction_task_execution_fn=methodology_extraction_task_execution_fn,
        methodology_claim_materialization_fn=methodology_claim_materialization_fn,
        methodology_evidence_item_materialization_fn=methodology_evidence_item_materialization_fn,
        methodology_sanad_creation_linking_grading_fn=methodology_sanad_creation_linking_grading_fn,
        extract_fn=partial(_run_snapshot_extraction, db_conn=db_conn),
        grade_fn=partial(_run_snapshot_auto_grade, db_conn=db_conn),
        calc_fn=partial(_run_snapshot_calc, db_conn=db_conn),
        enrich_fn=partial(_run_full_enrichment, db_conn=db_conn) if is_full else None,
        debate_fn=partial(_run_full_debate, db_conn=db_conn) if is_full else None,
        analysis_fn=(
            partial(_run_full_analysis, db_conn=db_conn, deal_metadata=deal_metadata)
            if is_full
            else None
        ),
        scoring_fn=_run_full_scoring if is_full else None,
        deliverables_fn=_run_full_deliverables if is_full else None,
    )


def load_documents_for_deal(
    *,
    db_conn: Any,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load parsed documents and spans for a deal from Postgres.

    The tenant-scoped repository sets RLS context before querying the corpus.
    """
    if db_conn is None:
        return []

    repo = PostgresDocumentsRepository(db_conn, tenant_id)
    run_documents: list[dict[str, Any]] = []
    for document in repo.list_documents_by_deal(deal_id, parsed_only=True):
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=document["document_id"],
        )
        if not spans:
            continue
        run_documents.append(_document_for_run(document, spans))
    return run_documents


def load_document_preflight_corpus_for_deal(
    *,
    db_conn: Any,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load the full persisted corpus for document preflight.

    This includes parsed and failed document rows. Failed/no-span rows are needed
    so parser triage can reconstruct blockers from safe metadata.
    """
    if db_conn is None:
        return []

    repo = PostgresDocumentsRepository(db_conn, tenant_id)
    corpus: list[dict[str, Any]] = []
    for document in repo.list_documents_by_deal(deal_id, parsed_only=False):
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=document["document_id"],
        )
        corpus.append(_document_for_preflight(document, spans))
    return corpus


def extraction_ready_documents_from_preflight_corpus(
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return parsed documents with spans in the legacy extraction-ready shape."""
    documents: list[dict[str, Any]] = []
    for document in corpus:
        spans = list(document.get("spans") or [])
        if str(document.get("parse_status", "PARSED")) != "PARSED":
            continue
        if not spans:
            continue
        documents.append(_document_for_run(document, spans))
    return documents


def filter_preflight_corpus_by_run_source(
    corpus: list[dict[str, Any]],
    source: dict[str, Any] | RunSource | None,
) -> list[dict[str, Any]]:
    """Apply a persisted run-source contract to a deal preflight corpus."""
    if source is None:
        return list(corpus)
    run_source = source if isinstance(source, RunSource) else RunSource.model_validate(source)
    requested_ids = set(run_source.document_ids)
    return [document for document in corpus if str(document.get("document_id")) in requested_ids]


def missing_document_ids_for_run_source(
    corpus: list[dict[str, Any]],
    source: RunSource,
) -> list[str]:
    """Return selected document IDs absent from the loaded deal corpus."""
    present_ids = {str(document.get("document_id")) for document in corpus}
    return [document_id for document_id in source.document_ids if document_id not in present_ids]


def _span_for_run(span: Any) -> dict[str, Any]:
    """Convert a repository span row to the run document span shape."""
    span_dict = (
        dict(span)
        if isinstance(span, dict)
        else {
            "span_id": str(span.span_id),
            "text_excerpt": span.text_excerpt,
            "locator": span.locator if isinstance(span.locator, dict) else {},
            "span_type": str(span.span_type),
            "content_hash": getattr(span, "content_hash", None),
        }
    )
    result: dict[str, Any] = {
        "span_id": str(span_dict["span_id"]),
        "text_excerpt": span_dict.get("text_excerpt"),
        "locator": span_dict.get("locator") if isinstance(span_dict.get("locator"), dict) else {},
        "span_type": str(span_dict["span_type"]),
    }
    if span_dict.get("content_hash"):
        result["content_hash"] = span_dict["content_hash"]
    return result


def _document_for_run(document: dict[str, Any], spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a repository document and spans to the run document shape."""
    result = {
        "document_id": document["document_id"],
        "doc_type": document["doc_type"],
        "document_name": document["document_name"],
        "spans": [_span_for_run(span) for span in spans],
    }
    metadata = dict(document.get("metadata") or {})
    if "parser_mode" in metadata:
        result["metadata"] = metadata
    return result


def _span_for_preflight(span: dict[str, Any]) -> dict[str, Any]:
    """Convert a repository span row to full preflight span shape."""
    return {
        "span_id": str(span["span_id"]),
        "tenant_id": str(span.get("tenant_id")) if span.get("tenant_id") is not None else None,
        "deal_id": str(span.get("deal_id")) if span.get("deal_id") is not None else None,
        "document_id": str(span["document_id"]),
        "span_type": str(span["span_type"]),
        "locator": span.get("locator") if isinstance(span.get("locator"), dict) else {},
        "text_excerpt": span.get("text_excerpt"),
        "content_hash": span.get("content_hash"),
    }


def _document_for_preflight(
    document: dict[str, Any],
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert repository document and spans to full preflight corpus shape."""
    return {
        "tenant_id": str(document["tenant_id"]),
        "deal_id": str(document["deal_id"]),
        "document_id": str(document["document_id"]),
        "doc_id": str(document["doc_id"]),
        "doc_type": str(document["doc_type"]),
        "parse_status": str(document["parse_status"]),
        "document_name": str(document.get("document_name") or document["document_id"]),
        "sha256": document.get("sha256"),
        "uri": document.get("uri"),
        "metadata": dict(document.get("metadata") or {}),
        "source_metadata": dict(
            document.get("source_metadata") or document.get("artifact_metadata") or {}
        ),
        "spans": [_span_for_preflight(span) for span in spans],
    }
