"""Runs routes for IDIS API.

Provides POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.

SNAPSHOT mode: INGEST_CHECK -> EXTRACT -> GRADE -> CALC.
FULL mode: INGEST_CHECK -> EXTRACT -> GRADE -> CALC -> GRAPH_EVIDENCE -> RAG_EVIDENCE
           -> ENRICHMENT -> DEBATE -> ANALYSIS -> SCORING -> DELIVERABLES.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import AuditSink, AuditSinkError
from idis.models.run_source import RunSource
from idis.persistence.repositories.run_steps import get_run_steps_repository
from idis.persistence.repositories.runs import get_runs_repository
from idis.services.runs import strict_full_live as strict_full_live_module
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.lifecycle import RunLifecycleService
from idis.services.runs.steps import build_run_context
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    STRICT_FULL_LIVE_BLOCKED,
    build_strict_block_operator_safe_details,
    build_strict_full_live_admission_report,
    is_strict_full_live_required,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Runs"])


class StartRunRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/runs."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    source: RunSource | None = None


PATH_LIKE_RUN_FIELDS = frozenset(
    {
        "data_room_root_path",
        "root_path",
        "file_path",
        "folder_path",
        "local_folder",
        "local_path",
        "uri",
        "uris",
        "paths",
        "files",
    }
)


class StepErrorResponse(BaseModel):
    """Error envelope for a failed step."""

    code: str
    message: str


class RunStepResponse(BaseModel):
    """Single step in the run response."""

    step_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    error: StepErrorResponse | None = None
    retry_count: int = 0


class RunRefStepResponse(BaseModel):
    """Single step in the start-run response without observability summaries."""

    step_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: StepErrorResponse | None = None
    retry_count: int = 0


class RunRef(BaseModel):
    """Run reference returned by startRun (202)."""

    run_id: str
    status: str
    steps: list[RunRefStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


class RunStatus(BaseModel):
    """Run status response for GET /v1/runs/{runId}."""

    run_id: str
    status: str
    mode: str
    started_at: str
    finished_at: str | None = None
    source: RunSource | None = None
    steps: list[RunStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


def _validate_start_run_body(body: dict[str, Any] | None) -> StartRunRequest:
    """Validate start run request body, returning 400 for missing required fields."""
    if body is None or not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Request body is required",
        )
    if "mode" not in body:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Missing required field: mode",
            details={"missing_fields": ["mode"]},
        )
    mode = body.get("mode")
    if mode not in ("SNAPSHOT", "FULL"):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid mode; must be SNAPSHOT or FULL",
        )
    rejected_fields = _path_like_fields(body)
    if rejected_fields:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Run creation does not accept filesystem paths or raw URI/path lists",
            details={"rejected_fields": rejected_fields},
        )
    try:
        return StartRunRequest.model_validate(body)
    except ValidationError as exc:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid run request body",
            details={"errors": exc.errors()},
        ) from exc


def _path_like_fields(body: dict[str, Any]) -> list[str]:
    """Return forbidden path-like fields from a potentially nested run request."""
    rejected: set[str] = set()
    for key, value in body.items():
        if key in PATH_LIKE_RUN_FIELDS:
            rejected.add(key)
        if isinstance(value, dict):
            rejected.update(field for field in value if field in PATH_LIKE_RUN_FIELDS)
    return sorted(rejected)


@router.post("/deals/{deal_id}/runs", response_model=RunRef, status_code=202)
async def start_run(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Start an IDIS pipeline run.

    Args:
        deal_id: UUID of the deal to run pipeline for.
        request: FastAPI request for DB connection and body access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunRef with run_id and initial status.

    Raises:
        IdisHttpError: 400 if invalid/missing fields, 404 if deal not found,
            500 if audit emission fails (fail-closed).
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_start_run_body(body)

    run_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    if not runs_repo.deal_exists(deal_id):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

    preflight_corpus = _gather_preflight_corpus(request, tenant_ctx.tenant_id, deal_id)
    preflight_corpus = _apply_run_source_to_preflight_corpus(
        preflight_corpus=preflight_corpus,
        source=request_body.source,
    )
    if not preflight_corpus:
        raise IdisHttpError(
            status_code=400,
            code="NO_INGESTED_DOCUMENTS",
            message=(
                "Deal has no ingested documents; ingest at least one document before starting a run"
            ),
        )
    documents = _extraction_ready_documents_from_preflight_corpus(preflight_corpus)

    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_live_extraction_required = request_body.mode == "FULL" and is_strict_full_live_required(
        dotenv_path=strict_dotenv_path
    )
    if strict_live_extraction_required:
        strict_report = build_strict_full_live_admission_report(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            preflight_corpus=preflight_corpus,
            strict_dotenv_path=strict_dotenv_path,
        )
        if not strict_report.may_proceed:
            raise IdisHttpError(
                status_code=409,
                code=STRICT_FULL_LIVE_BLOCKED,
                message=("Strict full-live preflight blocked this FULL run before execution"),
                details=build_strict_block_operator_safe_details(strict_report),
            )

    run_data = runs_repo.create(
        run_id=run_id,
        deal_id=deal_id,
        mode=request_body.mode,
        idempotency_key=idempotency_key,
        source=request_body.source.to_storage_dict() if request_body.source is not None else None,
        created_by_actor_id=tenant_ctx.actor_id,
        created_by_actor_type=tenant_ctx.actor_type,
    )

    request.state.audit_resource_id = run_id

    extractor_configured = getattr(request.app.state, "extractor_configured", True)
    if not extractor_configured:
        raise IdisHttpError(
            status_code=503,
            code="EXTRACTOR_NOT_CONFIGURED",
            message="No claim extractor is configured. Cannot proceed.",
        )

    audit_sink = _get_audit_sink(request)
    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    execution_service = RunExecutionService(
        audit_sink=audit_sink,
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
    )

    ctx = build_run_context(
        db_conn=db_conn,
        run_id=run_id,
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        mode=request_body.mode,
        documents=documents,
        deal_metadata=_load_deal_metadata_for_run(request, tenant_ctx.tenant_id, deal_id),
        preflight_corpus=preflight_corpus,
        audit_sink=audit_sink,
        strict_live_extraction_required=strict_live_extraction_required,
        strict_live_debate_backend_required=strict_live_extraction_required,
    )

    try:
        execution_result = await asyncio.to_thread(execution_service.execute, ctx)
    except AuditSinkError as exc:
        logger.error("Audit failure aborted run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run aborted: audit event emission failed",
        ) from exc

    if not execution_result.claimed:
        raise IdisHttpError(
            status_code=409,
            code="RUN_ALREADY_CLAIMED",
            message="Run was already claimed for execution",
        )

    finished_at = execution_result.finished_at or datetime.now(UTC).isoformat().replace(
        "+00:00", "Z"
    )
    run_data["status"] = execution_result.status
    run_data["finished_at"] = finished_at
    run_data["block_reason"] = execution_result.block_reason

    try:
        _emit_run_completed_audit(request, run_id, tenant_ctx.tenant_id, run_data["status"])
    except AuditSinkError as exc:
        logger.error("Audit failure on run.completed for run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run completed but audit event emission failed",
        ) from exc

    step_responses = _build_run_ref_step_responses(execution_result.steps)

    return RunRef(
        run_id=run_data["run_id"],
        status=run_data["status"],
        steps=step_responses,
        block_reason=execution_result.block_reason,
    )


@router.get("/runs/{run_id}", response_model=RunStatus)
def get_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunStatus:
    """Get run status.

    Args:
        run_id: UUID of the run to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunStatus with run details and step ledger.

    Raises:
        IdisHttpError: 404 if run not found or belongs to different tenant.
    """
    db_conn = getattr(request.state, "db_conn", None)

    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_data = runs_repo.get(run_id)

    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")

    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    steps = run_steps_repo.get_by_run_id(run_id)
    step_responses = _build_step_responses(steps)

    return RunStatus(
        run_id=run_data["run_id"],
        status=run_data["status"],
        mode=run_data["mode"],
        started_at=run_data["started_at"],
        finished_at=run_data.get("finished_at"),
        source=_run_source_from_storage(run_data.get("source")),
        steps=step_responses,
        block_reason=run_data.get("block_reason") or _derive_block_reason(steps),
    )


@router.post("/runs/{run_id}/retry", response_model=RunRef, status_code=202)
def retry_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Requeue a failed run after validating strict admission constraints."""
    return _retry_or_resume_run(run_id=run_id, request=request, tenant_ctx=tenant_ctx)


@router.post("/runs/{run_id}/resume", response_model=RunRef, status_code=202)
def resume_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Alias retry for a failed run."""
    return _retry_or_resume_run(run_id=run_id, request=request, tenant_ctx=tenant_ctx)


@router.post("/runs/{run_id}/cancel", response_model=RunRef, status_code=202)
def cancel_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Cancel a queued or running run without invoking execution admission."""
    request.state.audit_resource_id = run_id
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    run_data = runs_repo.get(run_id)
    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")
    if str(run_data.get("status")) not in {"QUEUED", "RUNNING"}:
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_CANCELABLE",
            message="Only queued or running runs can be cancelled",
        )
    if not lifecycle.request_cancel(run_id=run_id, tenant_id=tenant_ctx.tenant_id):
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_CANCELABLE",
            message="Only queued or running runs can be cancelled",
        )
    return RunRef(run_id=run_id, status="CANCELLED", steps=[], block_reason=None)


def _retry_or_resume_run(
    *,
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    request.state.audit_resource_id = run_id
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    run_data = runs_repo.get(run_id)
    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")
    if str(run_data.get("status")) != "FAILED":
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_RETRYABLE",
            message="Only failed runs can be retried",
        )

    run_source = _run_source_from_storage(run_data.get("source"))
    strict_dotenv_path = os.getenv(IDIS_STRICT_DOTENV_PATH_ENV)
    if str(run_data.get("mode", "")).upper() == "FULL" and is_strict_full_live_required(
        dotenv_path=strict_dotenv_path
    ):
        preflight_corpus = _gather_preflight_corpus(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=str(run_data["deal_id"]),
        )
        try:
            preflight_corpus = _apply_run_source_to_preflight_corpus(
                preflight_corpus=preflight_corpus,
                source=run_source,
            )
        except IdisHttpError as exc:
            if exc.code == "INVALID_RUN_SOURCE":
                lifecycle.persist_failed_block(
                    run_id=run_id,
                    tenant_id=tenant_ctx.tenant_id,
                    reason_code="INVALID_RUN_SOURCE",
                    message="Persisted run source is invalid for strict retry",
                )
                request.state.audit_mutation_occurred_on_error = True
                raise IdisHttpError(
                    status_code=409,
                    code="INVALID_RUN_SOURCE",
                    message="Persisted run source is invalid for strict retry",
                    details=exc.details,
                ) from exc
            raise

        strict_report = strict_full_live_module.build_strict_full_live_admission_report(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            preflight_corpus=preflight_corpus,
            strict_dotenv_path=strict_dotenv_path,
        )
        if not strict_report.may_proceed:
            blocking_provenance = strict_full_live_module.build_blocking_step_provenance(
                strict_report
            )
            lifecycle.persist_failed_block(
                run_id=run_id,
                tenant_id=tenant_ctx.tenant_id,
                reason_code=STRICT_FULL_LIVE_BLOCKED,
                message="Strict full live retry admission blocked",
                provenance_items=[item.model_dump(mode="json") for item in blocking_provenance],
            )
            request.state.audit_mutation_occurred_on_error = True
            raise IdisHttpError(
                status_code=409,
                code=STRICT_FULL_LIVE_BLOCKED,
                message="Strict full live admission blocked retry",
            )

    if not lifecycle.request_retry(run_id=run_id):
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_RETRYABLE",
            message="Only failed runs can be retried",
        )
    return RunRef(
        run_id=run_id,
        status="QUEUED",
        steps=[],
        block_reason=None,
    )


def _gather_snapshot_documents(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Gather ingested document spans for SNAPSHOT extraction.

    Loads from the request-scoped Postgres corpus first when DB is configured.
    Test-only in-memory documents are used only when no DB connection exists.

    Args:
        request: FastAPI request.
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.

    Returns:
        List of document dicts with doc_type, document_id, spans.
    """
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.services.runs.steps import load_documents_for_deal

        return load_documents_for_deal(
            db_conn=db_conn,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )

    test_docs: list[dict[str, Any]] = getattr(
        request.state,
        "snapshot_documents",
        [],
    )
    if test_docs:
        return test_docs

    deal_documents: dict[str, list[dict[str, Any]]] = getattr(
        request.app.state,
        "deal_documents",
        {},
    )
    if deal_id in deal_documents:
        return deal_documents[deal_id]

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return []

    documents: list[dict[str, Any]] = []
    for _key, doc in ingestion_service._documents.items():
        if str(doc.deal_id) != deal_id:
            continue
        if str(doc.tenant_id) != tenant_id:
            continue
        spans = ingestion_service.get_spans(doc.tenant_id, doc.document_id)
        if not spans:
            continue
        span_dicts = [
            {
                "span_id": str(s.span_id),
                "text_excerpt": s.text_excerpt,
                "locator": s.locator if isinstance(s.locator, dict) else {},
                "span_type": (
                    s.span_type.value if hasattr(s.span_type, "value") else str(s.span_type)
                ),
            }
            for s in spans
        ]
        documents.append(
            {
                "document_id": str(doc.document_id),
                "doc_type": (
                    doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
                ),
                "document_name": str(doc.document_id),
                "spans": span_dicts,
            }
        )

    return documents


def _gather_preflight_corpus(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Gather the full persisted corpus for DOCUMENT_PREFLIGHT."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.services.runs.steps import load_document_preflight_corpus_for_deal

        return load_document_preflight_corpus_for_deal(
            db_conn=db_conn,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )

    test_docs: list[dict[str, Any]] = getattr(
        request.state,
        "snapshot_documents",
        [],
    )
    if test_docs:
        return test_docs

    deal_documents: dict[str, list[dict[str, Any]]] = getattr(
        request.app.state,
        "deal_documents",
        {},
    )
    if deal_id in deal_documents:
        return deal_documents[deal_id]

    ingestion_corpus = _gather_ingestion_service_preflight_corpus(
        request,
        tenant_id=tenant_id,
        deal_id=deal_id,
    )
    if ingestion_corpus:
        return ingestion_corpus

    return _gather_snapshot_documents(request, tenant_id, deal_id)


def _gather_ingestion_service_preflight_corpus(
    request: Request,
    *,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Load full no-DB upload corpus from the app ingestion service state."""
    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return []

    corpus: list[dict[str, Any]] = []
    for _key, doc in ingestion_service._documents.items():
        if str(doc.deal_id) != deal_id:
            continue
        if str(doc.tenant_id) != tenant_id:
            continue
        artifact = ingestion_service.get_artifact(doc.tenant_id, doc.doc_id)
        spans = ingestion_service.get_spans(doc.tenant_id, doc.document_id)
        corpus.append(
            {
                "tenant_id": str(doc.tenant_id),
                "deal_id": str(doc.deal_id),
                "document_id": str(doc.document_id),
                "doc_id": str(doc.doc_id),
                "doc_type": doc.doc_type.value
                if hasattr(doc.doc_type, "value")
                else str(doc.doc_type),
                "parse_status": (
                    doc.parse_status.value
                    if hasattr(doc.parse_status, "value")
                    else str(doc.parse_status)
                ),
                "document_name": (
                    str(artifact.title)
                    if artifact is not None and getattr(artifact, "title", None)
                    else str(doc.document_id)
                ),
                "sha256": artifact.sha256 if artifact is not None else None,
                "uri": artifact.uri if artifact is not None else None,
                "metadata": dict(getattr(doc, "metadata", {}) or {}),
                "source_metadata": (
                    dict(getattr(artifact, "metadata", {}) or {}) if artifact is not None else {}
                ),
                "spans": [_ingestion_span_for_preflight(span) for span in spans],
            }
        )
    return corpus


def _ingestion_span_for_preflight(span: Any) -> dict[str, Any]:
    return {
        "span_id": str(span.span_id),
        "tenant_id": str(span.tenant_id),
        "deal_id": str(span.deal_id) if getattr(span, "deal_id", None) is not None else None,
        "document_id": str(span.document_id),
        "span_type": span.span_type.value
        if hasattr(span.span_type, "value")
        else str(span.span_type),
        "locator": span.locator if isinstance(span.locator, dict) else {},
        "text_excerpt": span.text_excerpt,
        "content_hash": span.content_hash,
    }


def _apply_run_source_to_preflight_corpus(
    *,
    preflight_corpus: list[dict[str, Any]],
    source: RunSource | None,
) -> list[dict[str, Any]]:
    """Validate and apply a durable deal-documents run source."""
    if source is None:
        return preflight_corpus

    from idis.services.runs.steps import (
        filter_preflight_corpus_by_run_source,
        missing_document_ids_for_run_source,
    )

    missing_ids = missing_document_ids_for_run_source(preflight_corpus, source)
    if missing_ids:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_RUN_SOURCE",
            message="Selected run-source documents are not present in this deal corpus",
            details={"missing_document_ids": missing_ids},
        )
    return filter_preflight_corpus_by_run_source(preflight_corpus, source)


def _load_deal_metadata_for_run(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> dict[str, Any] | None:
    """Load explicit deal metadata for safe identity-boundary steps."""
    from idis.persistence.repositories.deals import DealsRepository, InMemoryDealsRepository

    db_conn = getattr(request.state, "db_conn", None)
    repo = (
        DealsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryDealsRepository(tenant_id)
    )
    return repo.get(deal_id)


def _extraction_ready_documents_from_preflight_corpus(
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive parsed extraction-ready documents from the full preflight corpus."""
    from idis.services.runs.steps import extraction_ready_documents_from_preflight_corpus

    return extraction_ready_documents_from_preflight_corpus(corpus)


def _get_audit_sink(request: Request) -> AuditSink:
    """Get the audit sink from app state, falling back to in-memory.

    Args:
        request: FastAPI request.

    Returns:
        AuditSink instance.
    """
    from idis.audit.sink import InMemoryAuditSink

    sink: AuditSink | None = getattr(request.app.state, "audit_sink", None)
    if sink is None:
        return InMemoryAuditSink()
    return sink


SAFE_PUBLIC_STEP_ERROR_MESSAGE = "Run step failed; see error code for details."
SENSITIVE_SUMMARY_KEY_PARTS = frozenset(
    {
        "base64",
        "bytes",
        "content_b64",
        "artifact",
        "excerpt",
        "file",
        "file_content",
        "filename",
        "hash",
        "header",
        "html",
        "local_path",
        "path",
        "raw",
        "sha",
        "span",
        "text",
        "transcript",
        "uri",
    }
)
SENSITIVE_SUMMARY_VALUE_PARTS = frozenset(
    {
        "content_b64",
        "confidential",
        "raw bytes",
        "raw_bytes",
        "raw text",
        "raw_text",
        "parsed text",
        "parsed_text",
        "text_excerpt",
        "_marker",
        "revenue was 10m",
        "ebitda was 2m",
    }
)
SAFE_PUBLIC_SUMMARY_KEYS = frozenset({"artifact_count", "manifest_uri"})


def _build_step_responses(steps: list[Any]) -> list[RunStepResponse]:
    """Convert RunStep models to API response format.

    Args:
        steps: List of RunStep instances (already ordered by step_order).

    Returns:
        List of RunStepResponse dicts for JSON serialization.
    """
    from idis.models.run_step import StepStatus

    responses: list[RunStepResponse] = []
    for step in steps:
        error = None
        if step.status in (StepStatus.FAILED, StepStatus.BLOCKED) and step.error_code:
            error = StepErrorResponse(
                code=step.error_code,
                message=SAFE_PUBLIC_STEP_ERROR_MESSAGE,
            )
        responses.append(
            RunStepResponse(
                step_name=step.step_name.value
                if hasattr(step.step_name, "value")
                else step.step_name,
                status=step.status.value if hasattr(step.status, "value") else step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                summary=_safe_public_run_summary_dict(step.result_summary),
                error=error,
                retry_count=step.retry_count,
            )
        )
    return responses


def _build_run_ref_step_responses(steps: list[Any]) -> list[RunRefStepResponse]:
    """Convert RunStep models to the start-run response without summaries."""
    responses: list[RunRefStepResponse] = []
    for step in steps:
        error = None
        if step.status in ("FAILED", "BLOCKED") and step.error_code:
            error = StepErrorResponse(
                code=step.error_code,
                message=SAFE_PUBLIC_STEP_ERROR_MESSAGE,
            )
        responses.append(
            RunRefStepResponse(
                step_name=step.step_name.value
                if hasattr(step.step_name, "value")
                else step.step_name,
                status=step.status.value if hasattr(step.status, "value") else step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                error=error,
                retry_count=step.retry_count,
            )
        )
    return responses


def _derive_block_reason(steps: list[Any]) -> str | None:
    """Derive a stable run-level block reason from the durable step ledger."""
    from idis.models.run_step import StepStatus

    for step in steps:
        if step.status in (StepStatus.FAILED, StepStatus.BLOCKED) and step.error_code:
            return str(step.error_code)
    return None


def _run_source_from_storage(source: object) -> RunSource | None:
    """Return only the public persisted run-source contract."""
    if source is None:
        return None
    if isinstance(source, RunSource):
        return source
    if isinstance(source, dict):
        public_source = {
            "type": source.get("type"),
            "document_ids": source.get("document_ids"),
        }
        try:
            return RunSource.model_validate(public_source)
        except ValidationError:
            return None
    return None


def _safe_public_run_summary_dict(value: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized public summary object."""
    safe = _safe_public_run_summary(value)
    return safe if isinstance(safe, dict) else {}


def _safe_public_run_summary(value: object) -> object:
    """Sanitize persisted step summaries before exposing them publicly."""
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("_"):
                continue
            if key_text == "blocked_candidates":
                continue
            if key_text == "manifest_uri":
                if isinstance(item, str):
                    from idis.persistence.repositories.deliverables import (
                        safe_public_deliverable_uri,
                    )

                    if safe_uri := safe_public_deliverable_uri(item):
                        sanitized[key_text] = safe_uri
                continue
            if key_text == "artifact_count":
                if isinstance(item, int | float) and not isinstance(item, bool):
                    sanitized[key_text] = item
                continue
            if key_text == "reproducibility_hashes":
                if isinstance(item, list):
                    hashes = [value for value in item if _is_sha256_hex(value)]
                    if hashes:
                        sanitized[key_text] = hashes
                    elif item == []:
                        sanitized[key_text] = []
                continue
            if key_text == "blocked_candidate_reason_counts":
                if isinstance(item, dict):
                    reason_counts = _safe_reason_counts(item)
                    if reason_counts:
                        sanitized[key_text] = reason_counts
                    elif item == {}:
                        sanitized[key_text] = {}
                continue
            if key_text not in SAFE_PUBLIC_SUMMARY_KEYS and _is_sensitive_summary_key(key_text):
                continue
            safe_item = _safe_public_run_summary(item)
            if safe_item is not None:
                sanitized[key_text] = safe_item
        return sanitized
    if isinstance(value, list):
        return [
            safe_item for item in value if (safe_item := _safe_public_run_summary(item)) is not None
        ]
    if isinstance(value, str):
        return value if _is_safe_public_summary_string(value) else None
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


def _is_sensitive_summary_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_SUMMARY_KEY_PARTS)


def _is_safe_public_summary_string(value: str) -> bool:
    normalized = value.lower()
    if any(part in normalized for part in SENSITIVE_SUMMARY_VALUE_PARTS):
        return False
    if _looks_like_base64_blob(value):
        return False
    if "://" in value or "\\" in value or "/" in value:
        return False
    if len(value) > 512:
        return False
    if (
        "error:" in normalized
        or "exception:" in normalized
        or normalized.startswith(("valueerror", "runtimeerror", "traceback"))
    ):
        return False
    return not (len(value) > 1 and value[1] == ":")


def _is_sha256_hex(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _safe_reason_counts(value: dict[object, object]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, item in value.items():
        key_text = str(key)
        if not key_text.replace("_", "").isalnum():
            continue
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            safe[key_text] = item
    return safe


def _looks_like_base64_blob(value: str) -> bool:
    """Return True for likely opaque base64 payloads."""
    import base64
    import binascii

    stripped = value.strip()
    if len(stripped) < 16 or len(stripped) % 4 != 0:
        return False
    try:
        base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError):
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    return all(char in allowed for char in stripped)


def _emit_run_completed_audit(
    request: Request,
    run_id: str,
    tenant_id: str,
    status: str,
) -> None:
    """Emit deal.run.completed audit event after pipeline finishes.

    Args:
        request: FastAPI request for audit sink access.
        run_id: Pipeline run UUID.
        tenant_id: Tenant UUID.
        status: Final run status.
    """
    from idis.audit.sink import InMemoryAuditSink

    audit_sink = getattr(request.app.state, "audit_sink", None)
    if audit_sink is None:
        audit_sink = InMemoryAuditSink()

    event = {
        "event_type": "deal.run.completed",
        "tenant_id": tenant_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "details": {
            "run_id": run_id,
            "status": status,
        },
        "resource": {
            "resource_type": "deal",
            "resource_id": run_id,
        },
    }
    audit_sink.emit(event)


def _retrieve_claims_for_debate(
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any = None,
) -> list[dict[str, Any]]:
    """Look up full claim data for debate context.

    Uses Postgres repositories when db_conn is available, otherwise
    falls back to in-memory stores.

    Maps repository field names to DebateContext expected fields:
      claim_grade → sanad_grade
      primary_span_id → source_doc
      extraction_confidence (from sanad) → confidence

    Args:
        tenant_id: Tenant UUID for scoped lookups.
        created_claim_ids: Claim IDs produced by extraction/grading.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        List of claim dicts with keys matching DebateContext serialization:
        claim_id, claim_text, claim_class, sanad_grade, source_doc, confidence.
    """
    from idis.persistence.repositories.claims import (
        ClaimsRepository,
        InMemoryClaimsRepository,
        InMemorySanadsRepository,
        SanadsRepository,
    )

    if db_conn is not None:
        claims_repo: ClaimsRepository | InMemoryClaimsRepository = ClaimsRepository(
            db_conn, tenant_id
        )
        sanads_repo: SanadsRepository | InMemorySanadsRepository = SanadsRepository(
            db_conn, tenant_id
        )
    else:
        claims_repo = InMemoryClaimsRepository(tenant_id)
        sanads_repo = InMemorySanadsRepository(tenant_id)

    debate_claims: list[dict[str, Any]] = []
    for cid in created_claim_ids:
        claim = claims_repo.get(cid)
        if claim is None:
            logger.warning("Claim %s not found in store for debate context", cid)
            debate_claims.append(
                {
                    "claim_id": cid,
                    "claim_text": "",
                    "claim_class": "",
                    "sanad_grade": "",
                    "source_doc": "",
                    "confidence": 0.0,
                }
            )
            continue

        sanad = sanads_repo.get_by_claim(cid)
        confidence = 0.0
        if sanad is not None:
            computed = sanad.get("computed", {})
            confidence = computed.get(
                "extraction_confidence",
                sanad.get("extraction_confidence", 0.0),
            )

        debate_claims.append(
            {
                "claim_id": cid,
                "claim_text": claim.get("claim_text", ""),
                "claim_class": claim.get("claim_class", ""),
                "sanad_grade": claim.get("claim_grade", ""),
                "source_doc": claim.get("primary_span_id", "") or "",
                "confidence": float(confidence),
            }
        )

    return debate_claims


def _run_full_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    db_conn: Any = None,
    debate_role_runners_factory: DebateRoleRunnersFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> dict[str, Any]:
    """Run DebateOrchestrator for a FULL pipeline run.

    Constructs a DebateState from pipeline context, executes the debate,
    and converts the returned DebateState to a dict summary for the step ledger.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with debate_id, stop_reason, round_number, muhasabah_passed,
        and agent_output_count.
    """
    from idis.debate.orchestrator import DebateOrchestrator
    from idis.debate.roles.llm_role_runner import DebateContext
    from idis.models.debate import DebateConfig, DebateState

    debate_claims = _retrieve_claims_for_debate(tenant_id, created_claim_ids, db_conn=db_conn)

    context = DebateContext(
        deal_name=deal_id,
        deal_sector="Unknown",
        deal_stage="Unknown",
        deal_summary="",
        claims=debate_claims,
        calc_results=[
            {
                "calc_id": cid,
                "calc_name": "",
                "result_value": "",
                "input_claim_ids": [],
            }
            for cid in calc_ids
        ],
        conflicts=[],
    )

    state = DebateState(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_registry_ref=f"claims://{run_id}",
        sanad_graph_ref=f"sanad://{run_id}",
        round_number=1,
    )

    role_runners = _build_debate_role_runners(
        context=context,
        debate_role_runners_factory=debate_role_runners_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
    )
    orchestrator = DebateOrchestrator(config=DebateConfig(), role_runners=role_runners)
    final_state = orchestrator.run(state)

    gate_failure = orchestrator.get_gate_failure()
    muhasabah_passed = gate_failure is None

    return {
        "debate_id": run_id,
        "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
        "round_number": final_state.round_number,
        "muhasabah_passed": muhasabah_passed,
        "agent_output_count": len(final_state.agent_outputs),
        "debate_provenance": _build_debate_provenance(
            selection=_resolve_debate_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            role_runners=role_runners,
        ),
        "debate_observability": _build_debate_observability(final_state),
    }


def _run_full_enrichment(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    db_conn: Any = None,
) -> dict[str, Any]:
    """Run enrichment for all configured providers in a FULL pipeline run.

    Iterates over registered providers, calls EnrichmentService.enrich() for each,
    and aggregates results. Gracefully handles zero results (step COMPLETED with
    result_count=0).

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        db_conn: Optional database connection for durable BYOL credential repository.

    Returns:
        Dict with provider_count, result_count, blocked_count, enrichment_refs.
    """
    from idis.audit.sink import InMemoryAuditSink
    from idis.persistence.repositories.enrichment_credentials import (
        get_enrichment_credentials_repository,
    )
    from idis.services.enrichment.models import (
        EnrichmentPurpose,
        EnrichmentQuery,
        EnrichmentRequest,
        EnrichmentStatus,
        EntityType,
    )
    from idis.services.enrichment.service import create_default_enrichment_service

    audit_sink = InMemoryAuditSink()
    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_full_live = is_strict_full_live_required(dotenv_path=strict_dotenv_path)
    service = create_default_enrichment_service(
        audit_sink=audit_sink,
        credential_repo=get_enrichment_credentials_repository(db_conn, tenant_id),
        strict_full_live=strict_full_live,
        tenant_id=tenant_id,
        strict_dotenv_path=strict_dotenv_path,
    )
    providers = service.list_providers()

    result_count = 0
    blocked_count = 0
    enrichment_refs: dict[str, dict[str, str]] = {}

    request = EnrichmentRequest(
        tenant_id=tenant_id,
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name=deal_id),
        purpose=EnrichmentPurpose.DUE_DILIGENCE,
    )

    for provider_info in providers:
        provider_id = provider_info["provider_id"]
        try:
            result = service.enrich(provider_id=provider_id, request=request)
        except Exception as exc:
            if strict_full_live:
                raise RuntimeError(f"Strict enrichment provider failed: {provider_id}") from exc
            logger.warning(
                "Enrichment provider %s failed for deal %s in run %s",
                provider_id,
                deal_id,
                run_id,
                exc_info=True,
            )
            continue

        if result.status == EnrichmentStatus.HIT:
            result_count += 1
            if result.provenance:
                ref_id = f"enrich-{provider_id}-{run_id[:8]}"
                enrichment_refs[ref_id] = {
                    "ref_id": ref_id,
                    "provider_id": result.provenance.provider_id,
                    "source_id": result.provenance.source_id,
                }
        elif result.status in (
            EnrichmentStatus.BLOCKED_RIGHTS,
            EnrichmentStatus.BLOCKED_MISSING_BYOL,
        ):
            if strict_full_live:
                raise RuntimeError(f"Strict enrichment provider blocked: {provider_id}")
            blocked_count += 1
        elif result.status == EnrichmentStatus.ERROR and strict_full_live:
            raise RuntimeError(f"Strict enrichment provider failed: {provider_id}")

    return {
        "provider_count": len(providers),
        "result_count": result_count,
        "blocked_count": blocked_count,
        "enrichment_refs": enrichment_refs,
    }


def _run_full_layer2_ic_challenge(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    debate_summary: dict[str, Any],
    created_claim_ids: list[str],
    calc_ids: list[str],
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    enrichment_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the distinct Layer 2 IC challenge for a FULL pipeline run."""
    from idis.services.runs.layer2_ic_challenge import (
        RunLayer2ICChallengeService,
        build_live_layer2_ic_runners,
    )

    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_full_live = is_strict_full_live_required(dotenv_path=strict_dotenv_path)
    challenger_runner = None
    arbiter_runner = None
    if strict_full_live:
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        default_model = os.environ.get(
            "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
            "claude-sonnet-4-20250514",
        )
        arbiter_model = os.environ.get(
            "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
            "claude-opus-4-20250514",
        )
        challenger_runner, arbiter_runner = build_live_layer2_ic_runners(
            challenger_client=AnthropicLLMClient(model=default_model, max_tokens=8192),
            arbiter_client=AnthropicLLMClient(model=arbiter_model, max_tokens=8192),
        )
    service = RunLayer2ICChallengeService(
        strict_full_live=strict_full_live,
        env=os.environ,
        challenger_runner=challenger_runner,
        arbiter_runner=arbiter_runner,
    )
    return service.run(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        debate_summary=debate_summary,
        created_claim_ids=created_claim_ids,
        calc_ids=calc_ids,
        graph_evidence=graph_evidence,
        rag_evidence=rag_evidence,
        enrichment_refs=enrichment_refs,
    )


def _run_full_analysis(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    enrichment_refs: dict[str, Any],
    deal_metadata: dict[str, Any] | None = None,
    db_conn: Any = None,
    analysis_client_factory: AnalysisClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> dict[str, Any]:
    """Run analysis agents for a FULL pipeline run.

    Builds LLM client (using IDIS_DEBATE_BACKEND), registers all 8 specialist agents,
    constructs AnalysisContext from pipeline data, and runs AnalysisEngine.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        enrichment_refs: Enrichment references from enrichment step.

    Returns:
        Dict with agent_count, report_ids, bundle_id, and internal state
        (_analysis_bundle, _analysis_context) for downstream steps.
    """
    from idis.analysis.agents import build_default_specialist_agents
    from idis.analysis.models import AnalysisContext, EnrichmentRef
    from idis.analysis.registry import AnalysisAgentRegistry
    from idis.analysis.runner import AnalysisEngine
    from idis.audit.sink import InMemoryAuditSink

    llm_client = _build_analysis_llm_client(
        analysis_client_factory=analysis_client_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
    )
    agents = build_default_specialist_agents(llm_client=llm_client)

    registry = AnalysisAgentRegistry()
    for agent in agents:
        registry.register(agent)

    enrichment_ref_models: dict[str, EnrichmentRef] = {}
    for ref_id, ref_data in enrichment_refs.items():
        if isinstance(ref_data, dict):
            enrichment_ref_models[ref_id] = EnrichmentRef(
                ref_id=ref_data.get("ref_id", ref_id),
                provider_id=ref_data.get("provider_id", "unknown"),
                source_id=ref_data.get("source_id", "unknown"),
            )

    analysis_ctx = AnalysisContext(
        deal_id=deal_id,
        tenant_id=tenant_id,
        run_id=run_id,
        claim_ids=frozenset(created_claim_ids),
        calc_ids=frozenset(calc_ids),
        enrichment_refs=enrichment_ref_models,
        claim_registry=_build_analysis_claim_registry(
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        ),
        calc_registry=_build_analysis_calc_registry(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_ids=calc_ids,
            db_conn=db_conn,
        ),
        company_name=str((deal_metadata or {}).get("company_name") or ""),
        stage=str((deal_metadata or {}).get("stage") or ""),
        sector=str((deal_metadata or {}).get("sector") or ""),
    )

    audit_sink = InMemoryAuditSink()
    engine = AnalysisEngine(registry=registry, audit_sink=audit_sink)
    agent_ids = [a.agent_id for a in registry.list_agents()]
    bundle = engine.run(analysis_ctx, agent_ids)

    return {
        "agent_count": len(bundle.reports),
        "report_ids": [r.agent_id for r in bundle.reports],
        "bundle_id": f"bundle-{run_id[:8]}",
        "analysis_provenance": _build_analysis_provenance(
            selection=_resolve_analysis_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            client=llm_client,
        ),
        "_analysis_bundle": bundle,
        "_analysis_context": analysis_ctx,
    }


def _build_analysis_claim_registry(
    *,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    db_conn: Any = None,
) -> dict[str, Any]:
    """Load readable claim summaries for Layer 2 analysis context."""
    from idis.analysis.models import AnalysisClaimReference
    from idis.persistence.repositories.claims import ClaimsRepository, InMemoryClaimsRepository

    claims_repo = (
        ClaimsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryClaimsRepository(tenant_id)
    )
    registry: dict[str, AnalysisClaimReference] = {}
    for claim_id in sorted(set(created_claim_ids)):
        claim = claims_repo.get(claim_id)
        if not claim or str(claim.get("deal_id")) != deal_id:
            continue
        source_span_id = claim.get("primary_span_id")
        claim_class = str(claim.get("claim_class") or "")
        claim_text = str(claim.get("claim_text") or "")
        registry[claim_id] = AnalysisClaimReference(
            claim_id=claim_id,
            claim_text=claim_text,
            claim_class=claim_class,
            source_summary=_claim_source_summary(claim_class, source_span_id),
            sanad_grade=claim.get("claim_grade"),
            materiality=claim.get("materiality"),
            claim_verdict=claim.get("claim_verdict"),
            source_span_id=str(source_span_id) if source_span_id else None,
        )
    return registry


def _claim_source_summary(claim_class: str, source_span_id: Any) -> str:
    """Build a concise claim source summary for evidence appendices."""
    span_summary = f"span {source_span_id}" if source_span_id else None
    parts = [part for part in (claim_class, span_summary) if part]
    return ", ".join(parts) if parts else "Extracted claim"


def _build_analysis_calc_registry(
    *,
    tenant_id: str,
    deal_id: str,
    calc_ids: list[str],
    db_conn: Any = None,
) -> dict[str, Any]:
    """Load readable calculation summaries for Layer 2 analysis context."""
    from idis.analysis.models import AnalysisCalcReference
    from idis.persistence.repositories.calculations import get_calculations_repository

    requested_ids = set(calc_ids)
    if not requested_ids:
        return {}
    repo = get_calculations_repository(db_conn, tenant_id)
    registry: dict[str, AnalysisCalcReference] = {}
    calc_sanads_by_calc_id = {
        str(calc_sanad.get("calc_id") or ""): calc_sanad
        for calc_sanad in repo.list_calc_sanads_by_deal(deal_id)
    }
    for calc in repo.list_by_deal(deal_id):
        calc_id = str(calc.get("calc_id") or "")
        if calc_id not in requested_ids:
            continue
        output = calc.get("output")
        input_claim_ids = _calc_input_claim_ids(calc.get("inputs"))
        calc_sanad = calc_sanads_by_calc_id.get(calc_id, {})
        registry[calc_id] = AnalysisCalcReference(
            calc_id=calc_id,
            calc_type=str(calc.get("calc_type") or ""),
            output_summary=_calc_output_summary(output),
            input_claim_ids=input_claim_ids,
            source_summary=(
                f"{calc.get('calc_type') or 'calculation'} from {len(input_claim_ids)} input claims"
            ),
            reproducibility_hash=calc.get("reproducibility_hash"),
            calc_sanad_id=calc_sanad.get("calc_sanad_id"),
            formula_hash=calc.get("formula_hash"),
            code_version=calc.get("code_version"),
            output=_calc_output_payload(output),
            assumptions=_calc_assumptions(calc.get("inputs")),
            calc_grade=calc_sanad.get("calc_grade"),
            input_min_sanad_grade=calc_sanad.get("input_min_sanad_grade"),
        )
    return registry


def _calc_output_summary(output: Any) -> str:
    """Render a calculation output to a short deterministic summary."""
    if isinstance(output, dict):
        value = output.get("primary_value") or output.get("value") or output.get("amount")
        unit = output.get("unit")
        currency = output.get("currency")
        parts = [str(item) for item in (value, currency, unit) if item not in (None, "")]
        return " ".join(parts) if parts else str(output)
    return str(output or "")


def _calc_output_payload(output: Any) -> dict[str, Any]:
    """Return the safe structured output fields used by product exports."""
    if not isinstance(output, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in ("primary_value", "value", "amount", "unit", "currency"):
        if key in output:
            payload[key] = output[key]
    secondary_values = output.get("secondary_values")
    if isinstance(secondary_values, dict):
        payload["secondary_values"] = secondary_values
    return payload


def _calc_assumptions(inputs: Any) -> dict[str, Any]:
    """Expose calculation inputs/metadata as deterministic assumptions."""
    if not isinstance(inputs, dict):
        return {}
    assumptions: dict[str, Any] = {}
    values = inputs.get("values")
    metadata = inputs.get("metadata")
    if isinstance(values, dict):
        assumptions["inputs"] = {str(key): value for key, value in sorted(values.items())}
    if isinstance(metadata, dict) and metadata:
        assumptions["metadata"] = {
            str(key): value for key, value in sorted(metadata.items()) if value not in (None, "")
        }
    return assumptions


def _calc_input_claim_ids(inputs: Any) -> list[str]:
    """Extract input claim IDs from calculation repository payloads."""
    if not isinstance(inputs, dict):
        return []
    raw_claim_ids = inputs.get("input_claim_ids") or inputs.get("claim_ids") or []
    if isinstance(raw_claim_ids, list):
        return sorted(str(item) for item in raw_claim_ids if str(item).strip())
    return []


def _run_full_scoring(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scoring_client_factory: ScoringClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> dict[str, Any]:
    """Run scoring engine for a FULL pipeline run.

    Builds LLM client (using IDIS_DEBATE_BACKEND), constructs ScoringEngine,
    and scores the analysis bundle. Defaults to SEED stage.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        analysis_bundle: AnalysisBundle from analysis step.
        analysis_context: AnalysisContext from analysis step.

    Returns:
        Dict with composite_score, band, routing, and internal state
        (_scorecard) for downstream steps.
    """
    from idis.analysis.scoring.engine import ScoringEngine
    from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner
    from idis.analysis.scoring.models import Stage
    from idis.audit.sink import InMemoryAuditSink

    llm_client = _build_scoring_llm_client(
        scoring_client_factory=scoring_client_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
    )
    runner = LLMScorecardRunner(llm_client=llm_client)
    audit_sink = InMemoryAuditSink()
    engine = ScoringEngine(runner=runner, audit_sink=audit_sink)

    stage = Stage.SEED
    scorecard = engine.score(analysis_context, analysis_bundle, stage)

    return {
        "composite_score": scorecard.composite_score,
        "band": scorecard.score_band.value,
        "routing": scorecard.routing.value,
        "scoring_provenance": _build_scoring_provenance(
            selection=_resolve_scoring_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            client=llm_client,
        ),
        "_scorecard": scorecard,
    }


def _run_full_deliverables(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scorecard: Any,
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    layer2_evidence: dict[str, Any] | None = None,
    db_conn: Any = None,
    object_store: Any = None,
) -> dict[str, Any]:
    """Run deliverables generation for a FULL pipeline run.

    Uses DeliverablesGenerator to produce the full bundle of deliverables
    from analysis reports and scorecard.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        analysis_bundle: AnalysisBundle from analysis step.
        analysis_context: AnalysisContext from analysis step.
        scorecard: Scorecard from scoring step.

    Returns:
        Dict with deliverable_count, types, deliverable_ids.
    """
    from idis.audit.sink import InMemoryAuditSink
    from idis.deliverables.generator import DeliverablesGenerator

    audit_sink = InMemoryAuditSink()
    generator = DeliverablesGenerator(audit_sink=audit_sink)

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    bundle = generator.generate(
        ctx=analysis_context,
        bundle=analysis_bundle,
        scorecard=scorecard,
        deal_name=deal_id,
        generated_at=generated_at,
        deliverable_id_prefix=f"del-{run_id[:8]}",
    )

    types: list[str] = [
        bundle.screening_snapshot.deliverable_type,
        bundle.ic_memo.deliverable_type,
        bundle.truth_dashboard.deliverable_type,
        bundle.qa_brief.deliverable_type,
    ]
    deliverable_ids: list[str] = [
        bundle.screening_snapshot.deliverable_id,
        bundle.ic_memo.deliverable_id,
        bundle.truth_dashboard.deliverable_id,
        bundle.qa_brief.deliverable_id,
    ]

    if bundle.decline_letter is not None:
        types.append(bundle.decline_letter.deliverable_type)
        deliverable_ids.append(bundle.decline_letter.deliverable_id)

    if db_conn is not None and object_store is not None:
        from idis.deliverables.product_bundle import ProductBundleExporter
        from idis.persistence.repositories.deliverables import PostgresDeliverablesRepository

        exporter = ProductBundleExporter(
            deliverables_repo=PostgresDeliverablesRepository(db_conn, tenant_id),
            object_store=object_store,
            object_store_backend=object_store.backend_name,
        )
        export_summary = exporter.export_bundle(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            bundle=bundle,
            analysis_context=analysis_context,
            scorecard=scorecard,
            export_timestamp=generated_at,
            graph_evidence=graph_evidence,
            rag_evidence=rag_evidence,
            layer2_evidence=layer2_evidence,
        )
        export_summary["durable_export"] = True
        return export_summary

    return {
        "deliverable_count": len(types),
        "types": sorted(types),
        "deliverable_ids": sorted(deliverable_ids),
        "durable_export": False,
    }


def _run_full_graph_evidence(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    created_claim_ids: list[str],
    calc_ids: list[str],
    db_conn: Any = None,
    strict_full_live: bool | None = None,
    neo4j_health_checker: Any = None,
    projection_service: Any = None,
    retrieval_service: Any = None,
) -> dict[str, Any]:
    """Project and retrieve Neo4j graph evidence for product visibility."""
    from idis.persistence.neo4j_driver import Neo4jHealthStatus, check_neo4j_health
    from idis.services.runs.orchestrator import RunStepBlockedError

    health = (
        neo4j_health_checker(os.environ)
        if neo4j_health_checker is not None
        else check_neo4j_health()
    )
    strict = (
        strict_full_live
        if strict_full_live is not None
        else is_strict_full_live_required(
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
        )
    )
    if health.status != Neo4jHealthStatus.HEALTHY:
        summary = {
            "graph_status": "skipped",
            "graph_projection": {"status": "skipped"},
            "graph_retrieval": {"status": "skipped"},
            "neo4j_health_status": health.status.value,
            "missing_env_vars": health.missing_env_vars,
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_HEALTH_BLOCKED",
                "Neo4j graph evidence is not health-check ready",
                result_summary=summary,
            )
        return summary

    projection_summary = _project_graph_evidence(
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=documents,
        created_claim_ids=created_claim_ids,
        db_conn=db_conn,
        projection_service=projection_service,
    )
    projection_status = projection_summary["status"]
    if projection_status != "projected":
        graph_status = "skipped" if projection_status == "skipped" else "blocked"
        summary = {
            "graph_status": graph_status,
            "graph_projection": projection_summary,
            "graph_retrieval": {"status": "not_attempted"},
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_PROJECTION_BLOCKED",
                "Neo4j graph projection failed",
                result_summary=summary,
            )
        return summary

    retrieval_summary = _retrieve_graph_evidence(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_ids=created_claim_ids,
        retrieval_service=retrieval_service,
    )
    retrieval_status = retrieval_summary["status"]
    if retrieval_status != "retrieved":
        graph_status = "skipped" if retrieval_status == "skipped" else "blocked"
        summary = {
            "graph_status": graph_status,
            "graph_projection": projection_summary,
            "graph_retrieval": retrieval_summary,
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_RETRIEVAL_BLOCKED",
                "Neo4j graph retrieval failed",
                result_summary=summary,
            )
        return summary

    return {
        "graph_status": "available",
        "graph_projection": projection_summary,
        "graph_retrieval": retrieval_summary,
    }


def _run_full_rag_evidence(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    db_conn: Any = None,
    strict_full_live: bool | None = None,
    pgvector_health_checker: Any = None,
    embedding_health_checker: Any = None,
    indexing_service: Any = None,
    retrieval_service: Any = None,
    vector_repository_factory: Any = None,
) -> dict[str, Any]:
    """Index persisted spans and run bounded probe retrieval for product visibility."""
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
        is_vector_search_enabled,
    )
    from idis.services.rag.indexing import (
        VectorEmbeddingsRepository,
        build_postgres_vector_repository,
        create_openai_embed_batch,
        index_document_spans_for_deal,
    )
    from idis.services.rag.pgvector_health import PgvectorHealthStatus, check_pgvector_health
    from idis.services.rag.retrieval import retrieve_rag_probe_evidence
    from idis.services.runs.orchestrator import RunStepBlockedError

    env = os.environ
    strict = (
        strict_full_live
        if strict_full_live is not None
        else is_strict_full_live_required(
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
        )
    )
    skipped_summary = {
        "rag_status": "skipped",
        "rag_indexing": {"status": "skipped", "indexed_span_count": 0, "skipped_span_count": 0},
        "rag_retrieval": {
            "status": "skipped",
            "retrieval_mode": "probe",
            "probe_count": 0,
            "match_count": 0,
            "matches": [],
        },
    }

    if not is_vector_search_enabled(env):
        if strict:
            raise RunStepBlockedError(
                "RAG_CONFIG_BLOCKED",
                "Vector search is disabled for strict FULL runs",
                result_summary=skipped_summary,
            )
        return skipped_summary

    pgvector_health = (
        pgvector_health_checker(env)
        if pgvector_health_checker is not None
        else check_pgvector_health(env=env)
    )
    embedding_health = (
        embedding_health_checker(env)
        if embedding_health_checker is not None
        else check_embedding_health(env=env)
    )

    if (
        pgvector_health.status != PgvectorHealthStatus.HEALTHY
        or embedding_health.status != EmbeddingHealthStatus.HEALTHY
    ):
        summary = {
            **skipped_summary,
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
            "missing_env_vars": sorted(
                set(pgvector_health.missing_env_vars) | set(embedding_health.missing_env_vars)
            ),
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_HEALTH_BLOCKED",
                "pgvector/RAG evidence is not health-check ready",
                result_summary=summary,
            )
        return summary

    if db_conn is None:
        if strict:
            raise RunStepBlockedError(
                "RAG_DATABASE_BLOCKED",
                "pgvector indexing requires Postgres run persistence",
                result_summary=skipped_summary,
            )
        return skipped_summary

    repository_factory = vector_repository_factory or build_postgres_vector_repository
    repository = cast(
        VectorEmbeddingsRepository,
        repository_factory(db_conn, tenant_id),
    )
    embedding_model = embedding_health.model or "text-embedding-3-small"
    embedding_dimensions = embedding_health.dimensions or 1536

    if indexing_service is not None:
        indexing_summary, probe_embeddings = indexing_service(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            documents=documents,
            repository=repository,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
    else:
        indexing_summary, probe_embeddings = index_document_spans_for_deal(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            documents=documents,
            repository=repository,
            embed_batch=create_openai_embed_batch(env=env),
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )

    indexing_status = indexing_summary["status"]
    if indexing_status != "indexed":
        summary = {
            "rag_status": "skipped" if indexing_status == "skipped" else "blocked",
            "rag_indexing": indexing_summary,
            "rag_retrieval": {
                "status": "not_attempted",
                "retrieval_mode": "probe",
                "probe_count": 0,
                "match_count": 0,
                "matches": [],
            },
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_INDEXING_BLOCKED",
                "pgvector span indexing did not complete",
                result_summary=summary,
            )
        return summary

    if retrieval_service is not None:
        retrieval_summary = retrieval_service(
            deal_id=deal_id,
            probe_embeddings=probe_embeddings,
            repository=repository,
        )
    else:
        retrieval_summary = retrieve_rag_probe_evidence(
            deal_id=deal_id,
            probe_embeddings=probe_embeddings,
            repository=repository,
        )

    retrieval_status = retrieval_summary["status"]
    if retrieval_status != "probed":
        summary = {
            "rag_status": "blocked",
            "rag_indexing": indexing_summary,
            "rag_retrieval": retrieval_summary,
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_PROBE_RETRIEVAL_BLOCKED",
                "pgvector probe retrieval did not complete",
                result_summary=summary,
            )
        return summary

    return {
        "rag_status": "available",
        "rag_indexing": indexing_summary,
        "rag_retrieval": retrieval_summary,
        "pgvector_health_status": pgvector_health.status.value,
        "embedding_health_status": embedding_health.status.value,
    }


def _project_graph_evidence(
    *,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    created_claim_ids: list[str],
    db_conn: Any,
    projection_service: Any = None,
) -> dict[str, Any]:
    from idis.persistence.graph_consistency import GraphProjectionService, ProjectionStatus

    service = projection_service or GraphProjectionService()
    safe_documents = _graph_documents(documents)
    safe_spans = _graph_spans(documents)
    try:
        deal_result = service.project_deal(
            tenant_id=tenant_id,
            deal_id=deal_id,
            documents=safe_documents,
            spans=safe_spans,
        )
    except Exception:
        return {
            "status": "failed",
            "projected_document_count": 0,
            "projected_span_count": 0,
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    if deal_result.status == ProjectionStatus.SKIPPED:
        return {
            "status": "skipped",
            "projected_document_count": 0,
            "projected_span_count": 0,
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    if deal_result.status != ProjectionStatus.SUCCESS:
        return {"status": "failed", "projected_document_count": 0, "projected_span_count": 0}

    try:
        claims = _graph_claims(
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        )
        evidence_by_claim = _graph_evidence_by_claim(
            tenant_id=tenant_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        )
    except Exception:
        return {
            "status": "failed",
            "projected_document_count": len(safe_documents),
            "projected_span_count": len(safe_spans),
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    projected_claim_count = 0
    for claim in claims:
        try:
            result = service.project_claim_sanad(
                tenant_id=tenant_id,
                claim=claim,
                evidence_items=evidence_by_claim.get(claim["claim_id"], []),
                transmission_nodes=[],
                calculations=[],
            )
        except Exception:
            return {
                "status": "failed",
                "projected_document_count": len(safe_documents),
                "projected_span_count": len(safe_spans),
                "projected_claim_count": projected_claim_count,
                "projected_calculation_count": 0,
            }
        if result.status != ProjectionStatus.SUCCESS:
            return {
                "status": "failed",
                "projected_document_count": len(safe_documents),
                "projected_span_count": len(safe_spans),
                "projected_claim_count": projected_claim_count,
                "projected_calculation_count": 0,
            }
        projected_claim_count += 1

    return {
        "status": "projected",
        "projected_document_count": len(safe_documents),
        "projected_span_count": len(safe_spans),
        "projected_claim_count": projected_claim_count,
        "projected_calculation_count": 0,
    }


def _retrieve_graph_evidence(
    *,
    tenant_id: str,
    deal_id: str,
    claim_ids: list[str],
    retrieval_service: Any = None,
) -> dict[str, Any]:
    from idis.services.graph.retrieval import GraphRetrievalService

    service = retrieval_service or GraphRetrievalService()
    try:
        return service.retrieve_deal_graph_summary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_ids=claim_ids,
        )
    except Exception:
        return {"status": "failed", "retrieval_count": 0, "query_summaries": []}


def _graph_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(document["document_id"]),
            "doc_type": str(document.get("doc_type") or ""),
        }
        for document in documents
        if document.get("document_id")
    ]


def _graph_spans(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for document in documents:
        document_id = str(document.get("document_id") or "")
        if not document_id:
            continue
        for span in document.get("spans") or []:
            if not isinstance(span, dict) or not span.get("span_id"):
                continue
            spans.append(
                {
                    "span_id": str(span["span_id"]),
                    "document_id": document_id,
                    "span_type": str(span.get("span_type") or ""),
                }
            )
    return spans


def _graph_claims(
    *,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> list[dict[str, Any]]:
    from idis.persistence.repositories.claims import ClaimsRepository, InMemoryClaimsRepository

    repo = (
        ClaimsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryClaimsRepository(tenant_id)
    )
    claims: list[dict[str, Any]] = []
    for claim_id in sorted(set(created_claim_ids)):
        claim = repo.get(claim_id)
        claims.append(
            {
                "claim_id": claim_id,
                "claim_text": str((claim or {}).get("claim_text") or ""),
                "claim_grade": str((claim or {}).get("claim_grade") or "D"),
                "claim_verdict": str((claim or {}).get("claim_verdict") or "UNVERIFIED"),
                "materiality": str((claim or {}).get("materiality") or "MEDIUM"),
                "claim_class": str((claim or {}).get("claim_class") or "OTHER"),
                "deal_id": deal_id,
            }
        )
    return claims


def _graph_evidence_by_claim(
    *,
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> dict[str, list[dict[str, Any]]]:
    from idis.persistence.repositories.evidence import get_evidence_repository

    repo = get_evidence_repository(db_conn, tenant_id)
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for claim_id in sorted(set(created_claim_ids)):
        evidence_items = []
        for item in repo.get_by_claim(claim_id):
            evidence_items.append(
                {
                    "evidence_id": str(item.get("evidence_id") or ""),
                    "source_grade": str(item.get("source_grade") or "D"),
                    "source_system": "idis",
                    "upstream_origin_id": str(item.get("source_span_id") or ""),
                }
            )
        evidence_by_claim[claim_id] = [item for item in evidence_items if item["evidence_id"]]
    return evidence_by_claim


_ANALYSIS_MAX_TOKENS = 8192
_SCORING_MAX_TOKENS = 16384
_DEBATE_MAX_TOKENS = 8192
_DEFAULT_DEBATE_MODEL = "claude-sonnet-4-20250514"
_DEFAULT_DEBATE_ARBITER_MODEL = "claude-opus-4-20250514"


@dataclass(frozen=True)
class AnalysisClientSelection:
    """Safe selection context for an injected analysis-client factory (no API key)."""

    backend: str
    model: str | None
    max_tokens: int


@dataclass(frozen=True)
class ScoringClientSelection:
    """Safe selection context for an injected scoring-client factory (no API key)."""

    backend: str
    model: str | None
    max_tokens: int


@dataclass(frozen=True)
class DebateRoleRunnerSelection:
    """Safe selection context for an injected debate role-runners factory (no API key)."""

    backend: str
    default_model: str | None
    arbiter_model: str | None
    max_tokens: int


AnalysisClientFactory = Callable[[AnalysisClientSelection], Any]
ScoringClientFactory = Callable[[ScoringClientSelection], Any]
DebateRoleRunnersFactory = Callable[[DebateRoleRunnerSelection], Any]


def _resolve_analysis_selection() -> AnalysisClientSelection:
    """Resolve the analysis backend/model selection from env (no client construction)."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    model = (
        os.environ.get("IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", _DEFAULT_DEBATE_MODEL)
        if backend == "anthropic"
        else None
    )
    return AnalysisClientSelection(backend=backend, model=model, max_tokens=_ANALYSIS_MAX_TOKENS)


def _resolve_scoring_selection() -> ScoringClientSelection:
    """Resolve the scoring backend/model selection from env (no client construction)."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    model = (
        os.environ.get("IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", _DEFAULT_DEBATE_MODEL)
        if backend == "anthropic"
        else None
    )
    return ScoringClientSelection(backend=backend, model=model, max_tokens=_SCORING_MAX_TOKENS)


def _resolve_debate_selection() -> DebateRoleRunnerSelection:
    """Resolve the debate backend/models selection from env (no runner construction)."""
    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")
    if backend == "anthropic":
        default_model = os.environ.get("IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", _DEFAULT_DEBATE_MODEL)
        arbiter_model = os.environ.get(
            "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER", _DEFAULT_DEBATE_ARBITER_MODEL
        )
    else:
        default_model = None
        arbiter_model = None
    return DebateRoleRunnerSelection(
        backend=backend,
        default_model=default_model,
        arbiter_model=arbiter_model,
        max_tokens=_DEBATE_MAX_TOKENS,
    )


# --- Slice84: execution-time strict-live enforcement for analysis / debate L1 / scoring ---
# A single shared flag ``strict_live_debate_backend_required`` (threaded only from the strict
# FULL execution path) forbids the deterministic analysis client / debate RoleRunners / scoring
# client. Surfaced only as fixed, role-specific codes — never with the API key, prompt,
# response, provider payload, or a raw underlying exception message.
STRICT_LIVE_ANALYSIS_REQUIRED = "STRICT_LIVE_ANALYSIS_REQUIRED"
STRICT_LIVE_ANALYSIS_PROVIDER_FAILED = "STRICT_LIVE_ANALYSIS_PROVIDER_FAILED"
STRICT_LIVE_DEBATE_REQUIRED = "STRICT_LIVE_DEBATE_REQUIRED"
STRICT_LIVE_DEBATE_PROVIDER_FAILED = "STRICT_LIVE_DEBATE_PROVIDER_FAILED"
STRICT_LIVE_SCORING_REQUIRED = "STRICT_LIVE_SCORING_REQUIRED"
STRICT_LIVE_SCORING_PROVIDER_FAILED = "STRICT_LIVE_SCORING_PROVIDER_FAILED"


class StrictLiveRoleError(Exception):
    """A strict FULL analysis/debate/scoring role could not use an approved live backend.

    Carries only a safe, fixed ``code`` + ``message`` — never the API key, prompt, response,
    provider payload, or a raw underlying exception message. The ``code`` is role-specific (one
    of the ``STRICT_LIVE_{ANALYSIS,DEBATE,SCORING}_{REQUIRED,PROVIDER_FAILED}`` constants).
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _build_strict_anthropic_role_client(
    selection: Any,
    factory: Callable[[Any], Any] | None,
    *,
    role: str,
    provider_failed_code: str,
) -> Any:
    """Build the live (Anthropic) analysis/scoring client for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as the role's ``..._PROVIDER_FAILED`` code with a
    fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if factory is not None:
            return factory(selection)
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(model=selection.model, max_tokens=selection.max_tokens)
    except StrictLiveRoleError:
        raise
    except Exception as exc:
        raise StrictLiveRoleError(
            code=provider_failed_code,
            message=f"Strict live {role} provider construction or call failed.",
        ) from exc


def _build_scoring_llm_client(
    *,
    scoring_client_factory: ScoringClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> Any:
    """Build the LLM client for scoring based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic). When ``scoring_client_factory`` is
    supplied it is called with the resolved ``ScoringClientSelection`` and its return value is
    used (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required``
    is True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_SCORING_REQUIRED`` (deterministic scoring is forbidden) and an
    anthropic provider failure surfaces ``STRICT_LIVE_SCORING_PROVIDER_FAILED``. The non-strict
    default path is unchanged.

    Returns:
        An LLMClient implementation instance.
    """
    selection = _resolve_scoring_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_anthropic_role_client(
                selection,
                scoring_client_factory,
                role="scoring",
                provider_failed_code=STRICT_LIVE_SCORING_PROVIDER_FAILED,
            )
        if scoring_client_factory is not None:
            return scoring_client_factory(selection)
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(model=selection.model, max_tokens=selection.max_tokens)

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_SCORING_REQUIRED,
            message=(
                "Strict live scoring requires the anthropic backend; "
                "deterministic scoring is forbidden."
            ),
        )
    if scoring_client_factory is not None:
        return scoring_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicScoringLLMClient

    return DeterministicScoringLLMClient()


def _build_analysis_llm_client(
    *,
    analysis_client_factory: AnalysisClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> Any:
    """Build the LLM client for analysis agents based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic). When ``analysis_client_factory`` is
    supplied it is called with the resolved ``AnalysisClientSelection`` and its return value is
    used (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required``
    is True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_ANALYSIS_REQUIRED`` (deterministic analysis is forbidden) and an
    anthropic provider failure surfaces ``STRICT_LIVE_ANALYSIS_PROVIDER_FAILED``. The non-strict
    default path is unchanged.

    Returns:
        An LLMClient implementation instance.
    """
    selection = _resolve_analysis_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_anthropic_role_client(
                selection,
                analysis_client_factory,
                role="analysis",
                provider_failed_code=STRICT_LIVE_ANALYSIS_PROVIDER_FAILED,
            )
        if analysis_client_factory is not None:
            return analysis_client_factory(selection)
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(model=selection.model, max_tokens=selection.max_tokens)

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_ANALYSIS_REQUIRED,
            message=(
                "Strict live analysis requires the anthropic backend; "
                "deterministic analysis is forbidden."
            ),
        )
    if analysis_client_factory is not None:
        return analysis_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicAnalysisLLMClient

    return DeterministicAnalysisLLMClient()


def _run_snapshot_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
    db_conn: Any = None,
) -> dict[str, Any]:
    """Run deterministic calculations for claims produced by extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        calc_types: Optional list of CalcType to run. None means run all.
        db_conn: SQLAlchemy connection for Postgres persistence.

    Returns:
        Dict with calc_ids and reproducibility_hashes.
    """
    from idis.models.deterministic_calculation import CalcType
    from idis.persistence.repositories.calculations import get_calculations_repository
    from idis.persistence.repositories.claims import (
        ClaimsRepository,
        InMemoryClaimsRepository,
        InMemorySanadsRepository,
        SanadsRepository,
    )
    from idis.services.calc.runner import CalcRunner

    typed_calc_types = (
        [
            calc_type if isinstance(calc_type, CalcType) else CalcType(str(calc_type))
            for calc_type in calc_types
        ]
        if calc_types
        else None
    )

    claims_repo: Any
    sanads_repo: Any
    if db_conn is not None:
        claims_repo = ClaimsRepository(db_conn, tenant_id)
        sanads_repo = SanadsRepository(db_conn, tenant_id)
    else:
        claims_repo = InMemoryClaimsRepository(tenant_id)
        sanads_repo = InMemorySanadsRepository(tenant_id)

    runner = CalcRunner(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claims_repo=claims_repo,
        sanads_repo=sanads_repo,
        calculations_repo=get_calculations_repository(db_conn, tenant_id),
    )
    result = runner.run(
        created_claim_ids=created_claim_ids,
        calc_types=typed_calc_types,
    )
    result["claim_count"] = len(created_claim_ids)
    return result


def _run_snapshot_auto_grade(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    audit_sink: AuditSink,
    db_conn: Any = None,
) -> dict[str, Any]:
    """Run Sanad auto-grading for all claims produced by SNAPSHOT extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        audit_sink: App-level audit sink (required).
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with grading summary stats.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from idis.services.runs.orchestrator import RunStepBlockedError
    from idis.services.sanad.auto_grade import auto_grade_claims_for_run

    if not created_claim_ids:
        return {
            "graded_count": 0,
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    try:
        if db_conn is not None:
            with db_conn.begin_nested():
                grade_result = auto_grade_claims_for_run(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    created_claim_ids=created_claim_ids,
                    audit_sink=audit_sink,
                    db_conn=db_conn,
                )
        else:
            grade_result = auto_grade_claims_for_run(
                run_id=run_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
                created_claim_ids=created_claim_ids,
                audit_sink=audit_sink,
                db_conn=db_conn,
            )
    except SQLAlchemyError as exc:
        logger.warning("Sanad auto-grade blocked by persistence error", exc_info=True)
        raise RunStepBlockedError(
            "SANAD_AUTO_GRADE_PERSISTENCE_BLOCKED",
            "Sanad auto-grade is blocked by downstream persistence schema",
            result_summary={
                "graded_count": 0,
                "failed_count": len(created_claim_ids),
                "total_defects": 0,
                "all_failed": True,
                "blocker": "defects_schema_query",
            },
        ) from exc

    return {
        "graded_count": grade_result.graded_count,
        "failed_count": grade_result.failed_count,
        "total_defects": grade_result.total_defects,
        "all_failed": grade_result.all_failed,
    }


def _run_snapshot_extraction(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    db_conn: Any = None,
    extractor_client_factory: ExtractorClientFactory | None = None,
    strict_live_extraction_required: bool = False,
) -> dict[str, Any]:
    """Execute SNAPSHOT extraction pipeline synchronously.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        documents: List of document dicts with doc_type, document_id, spans.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with pipeline result status and stats.
    """
    from idis.audit.sink import InMemoryAuditSink
    from idis.persistence.repositories.evidence import get_evidence_repository
    from idis.services.claims.service import ClaimService
    from idis.services.extraction.chunking.service import ChunkingService
    from idis.services.extraction.confidence.scorer import ConfidenceScorer
    from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.extraction.resolution.conflict_detector import ConflictDetector
    from idis.services.extraction.resolution.deduplicator import Deduplicator

    prompt_text = _get_extraction_prompt()
    output_schema = _get_extraction_output_schema()

    selection = _resolve_extraction_selection()
    llm_client = _build_extraction_llm_client(
        extractor_client_factory=extractor_client_factory,
        strict_live_extraction_required=strict_live_extraction_required,
    )
    scorer = ConfidenceScorer()
    extractor = LLMClaimExtractor(
        llm_client=llm_client,
        prompt_text=prompt_text,
        output_schema=output_schema,
        confidence_scorer=scorer,
    )

    audit_sink = InMemoryAuditSink()
    claim_service = ClaimService(
        tenant_id=tenant_id,
        db_conn=db_conn,
        audit_sink=audit_sink,
    )
    evidence_repo = get_evidence_repository(db_conn, tenant_id)

    pipeline = ExtractionPipeline(
        chunking_service=ChunkingService(),
        claim_extractor=extractor,
        deduplicator=Deduplicator(),
        conflict_detector=ConflictDetector(),
        claim_service=claim_service,
        evidence_repo=evidence_repo,
        audit_sink=audit_sink,
    )

    result = pipeline.run(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=documents,
    )

    return {
        "status": result.status,
        "created_claim_ids": result.created_claim_ids,
        "chunk_count": result.chunk_count,
        "unique_claim_count": result.unique_claim_count,
        "conflict_count": result.conflict_count,
        "extraction_provenance": _build_extraction_provenance(
            selection=selection,
            strict_live_extraction_required=strict_live_extraction_required,
            client=llm_client,
        ),
    }


def _find_project_root() -> Path:
    """Walk up from this file to find the directory containing pyproject.toml.

    Returns:
        Path to the project root.

    Raises:
        FileNotFoundError: If pyproject.toml cannot be found.
    """
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    msg = "Cannot locate project root (no pyproject.toml found above %s)"
    raise FileNotFoundError(msg % Path(__file__).resolve())


def _get_extraction_prompt() -> str:
    """Load EXTRACT_CLAIMS_V1 prompt text from disk.

    Fail-closed: raises FileNotFoundError if prompt file is missing.

    Returns:
        Prompt template string.
    """
    root = _find_project_root()
    prompt_path = root / "prompts" / "extract_claims" / "1.0.0" / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _get_extraction_output_schema() -> dict[str, Any]:
    """Load EXTRACT_CLAIMS_V1 output schema from disk.

    Fail-closed: raises FileNotFoundError if schema file is missing.

    Returns:
        JSON schema dict.
    """
    import json

    root = _find_project_root()
    schema_path = root / "schemas" / "extraction" / "extract_claims_output.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


_EXTRACTION_MAX_TOKENS = 4096

# Safe, fixed strict-live-extraction outcome codes (value == name, mirroring
# STRICT_FULL_LIVE_BLOCKED). Surfaced only as codes — never with secrets or raw messages.
STRICT_LIVE_EXTRACTION_REQUIRED = "STRICT_LIVE_EXTRACTION_REQUIRED"
STRICT_LIVE_EXTRACTION_PROVIDER_FAILED = "STRICT_LIVE_EXTRACTION_PROVIDER_FAILED"


class StrictLiveExtractionError(Exception):
    """Strict FULL extraction could not use an approved live extractor backend.

    Carries only a safe, fixed ``code`` + ``message`` — never the API key, prompt, response,
    provider payload, or a raw underlying exception message.
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ExtractorClientSelection:
    """Safe selection context handed to an injected extractor-client factory.

    Carries only the resolved backend + model + max_tokens — never the API key or any other
    secret. The factory (test seam / future strict wiring) returns the extraction client.
    """

    backend: str
    model: str | None
    max_tokens: int


ExtractorClientFactory = Callable[[ExtractorClientSelection], Any]

_DEFAULT_ANTHROPIC_EXTRACT_MODEL = "claude-sonnet-4-20250514"
_EXTRACTION_PROMPT_ID = "EXTRACT_CLAIMS_V1"


def _resolve_extraction_selection() -> ExtractorClientSelection:
    """Resolve the extraction backend/model selection from env (no client construction)."""
    import os

    backend = os.environ.get("IDIS_EXTRACT_BACKEND", "deterministic")
    model = (
        os.environ.get("IDIS_ANTHROPIC_MODEL_EXTRACT", _DEFAULT_ANTHROPIC_EXTRACT_MODEL)
        if backend == "anthropic"
        else None
    )
    return ExtractorClientSelection(backend=backend, model=model, max_tokens=_EXTRACTION_MAX_TOKENS)


def _build_strict_anthropic_extractor(
    selection: ExtractorClientSelection,
    extractor_client_factory: ExtractorClientFactory | None,
) -> Any:
    """Build the live (Anthropic) extractor for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as ``STRICT_LIVE_EXTRACTION_PROVIDER_FAILED``
    with a fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if extractor_client_factory is not None:
            return extractor_client_factory(selection)
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(model=selection.model, max_tokens=selection.max_tokens)
    except StrictLiveExtractionError:
        raise
    except Exception as exc:
        raise StrictLiveExtractionError(
            code=STRICT_LIVE_EXTRACTION_PROVIDER_FAILED,
            message="Strict live extraction provider construction or call failed.",
        ) from exc


def _build_extraction_llm_client(
    *,
    extractor_client_factory: ExtractorClientFactory | None = None,
    strict_live_extraction_required: bool = False,
) -> Any:
    """Build the LLM client for extraction based on env configuration.

    Reads IDIS_EXTRACT_BACKEND (default: deterministic).
    Fail-closed: raises ValueError if anthropic backend selected but key missing.

    When ``extractor_client_factory`` is supplied it is called with the resolved
    ``ExtractorClientSelection`` and its return value is used as the extraction client
    (injection seam for tests / strict wiring). When ``strict_live_extraction_required`` is
    True (threaded only from the strict FULL execution path) a non-anthropic backend fails
    closed with ``STRICT_LIVE_EXTRACTION_REQUIRED`` (deterministic extraction is forbidden)
    and an anthropic provider failure surfaces ``STRICT_LIVE_EXTRACTION_PROVIDER_FAILED``.
    The non-strict default path is unchanged.

    Returns:
        An LLMClient implementation instance.

    Raises:
        ValueError: If IDIS_EXTRACT_BACKEND=anthropic but ANTHROPIC_API_KEY is unset
            (non-strict default path only; an injected factory builds its own client).
        StrictLiveExtractionError: If strict-live extraction is required but a live approved
            backend is not available/usable.
    """
    selection = _resolve_extraction_selection()

    if selection.backend == "anthropic":
        if strict_live_extraction_required:
            return _build_strict_anthropic_extractor(selection, extractor_client_factory)
        if extractor_client_factory is not None:
            return extractor_client_factory(selection)
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(model=selection.model, max_tokens=selection.max_tokens)

    # Non-anthropic backend (deterministic / unset / other).
    if strict_live_extraction_required:
        raise StrictLiveExtractionError(
            code=STRICT_LIVE_EXTRACTION_REQUIRED,
            message=(
                "Strict live extraction requires the anthropic backend; "
                "deterministic extraction is forbidden."
            ),
        )
    if extractor_client_factory is not None:
        return extractor_client_factory(selection)

    from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

    return DeterministicLLMClient()


def _safe_client_request_id(client: Any) -> str | None:
    """Return a safe provider request id if the client exposes one, else None."""
    value = getattr(client, "provider_request_id", None)
    return value if isinstance(value, str) and value.strip() else None


def _extraction_prompt_version() -> str | None:
    """Return the extraction prompt's registry version from prompts/registry.yaml (safe)."""
    return _prompt_registry_version(_EXTRACTION_PROMPT_ID)


def _build_extraction_provenance(
    *,
    selection: ExtractorClientSelection,
    strict_live_extraction_required: bool,
    client: Any,
) -> dict[str, Any]:
    """Build a safe, additive extraction-provenance block for the step summary.

    Records only provider/backend + safe model name + prompt id/version + the strict flag +
    a sanitized provider request id (only if the client safely exposes one) — never the API
    key, prompt body, response text, raw provider payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    provider = "anthropic" if selection.backend == "anthropic" else "deterministic"
    return {
        "provider": provider,
        "backend": selection.backend,
        "model": selection.model,
        "prompt_id": _EXTRACTION_PROMPT_ID,
        "prompt_version": _extraction_prompt_version(),
        "strict_live_extraction_required": bool(strict_live_extraction_required),
        "provider_request_id": _sanitize_request_id(_safe_client_request_id(client)),
    }


# --- Slice84 Task 4: safe, additive provenance + debate observability ---
_SCORING_PROMPT_ID = "scoring_agent"  # on-disk prompt family: prompts/scoring_agent/1.0.0/
_SCORING_PROMPT_VERSION = "1.0.0"
_DEBATE_PROMPT_IDS = (
    "DEBATE_ADVOCATE_V1",
    "DEBATE_SANAD_BREAKER_V1",
    "DEBATE_CONTRADICTION_FINDER_V1",
    "DEBATE_RISK_OFFICER_V1",
    "DEBATE_ARBITER_V1",
)
_DEBATE_PROMPT_VERSION_ID = "DEBATE_ARBITER_V1"


def _prompt_registry_version(prompt_id: str) -> str | None:
    """Return a prompt's registry version from prompts/registry.yaml (safe), else None."""
    import yaml

    try:
        registry_path = _find_project_root() / "prompts" / "registry.yaml"
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    prompt = (data.get("prompts") or {}).get(prompt_id) or {}
    version = prompt.get("version")
    return version.strip() if isinstance(version, str) and version.strip() else None


def _provider_label(backend: str) -> str:
    """Map a resolved backend to a safe provider label."""
    return "anthropic" if backend == "anthropic" else "deterministic"


def _build_role_client_provenance(
    *,
    selection: Any,
    prompt_id: str | None,
    prompt_version: str | None,
    strict_live_debate_backend_required: bool,
    client: Any,
) -> dict[str, Any]:
    """Build a safe, additive provenance block for a single-client role (analysis/scoring).

    Records only provider/backend + safe model name + prompt id/version + the strict flag + a
    sanitized provider request id (only if the client safely exposes one) — never the API key,
    prompt body, response text, raw provider payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    return {
        "provider": _provider_label(selection.backend),
        "backend": selection.backend,
        "model": selection.model,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "strict_live_debate_backend_required": bool(strict_live_debate_backend_required),
        "provider_request_id": _sanitize_request_id(_safe_client_request_id(client)),
    }


def _build_analysis_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, client: Any
) -> dict[str, Any]:
    """Safe analysis provenance.

    Analysis runs 8 embedded specialist prompts (no single registry prompt id), so
    ``prompt_id``/``prompt_version`` are null by design; the rest mirrors the shared block.
    """
    return _build_role_client_provenance(
        selection=selection,
        prompt_id=None,
        prompt_version=None,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        client=client,
    )


def _build_scoring_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, client: Any
) -> dict[str, Any]:
    """Safe scoring provenance (on-disk prompt family scoring_agent/1.0.0)."""
    return _build_role_client_provenance(
        selection=selection,
        prompt_id=_SCORING_PROMPT_ID,
        prompt_version=_SCORING_PROMPT_VERSION,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        client=client,
    )


def _safe_runner_request_id(runner: Any) -> str | None:
    """Return a safe provider request id from a role runner's client, if any."""
    client = getattr(runner, "llm_client", None)
    return _safe_client_request_id(client) if client is not None else None


def _build_debate_provenance(
    *, selection: Any, strict_live_debate_backend_required: bool, role_runners: Any
) -> dict[str, Any]:
    """Build safe, additive debate provenance.

    Records default + arbiter model names, the 5 registry debate prompt ids + their version, the
    strict flag, and sanitized default/arbiter provider request ids (only if safely exposed) —
    never the API key, prompt body, response, raw payload, exception message, or a path.
    """
    from idis.services.llm_model_health import _sanitize_request_id

    return {
        "provider": _provider_label(selection.backend),
        "backend": selection.backend,
        "default_model": selection.default_model,
        "arbiter_model": selection.arbiter_model,
        "prompt_ids": list(_DEBATE_PROMPT_IDS),
        "prompt_version": _prompt_registry_version(_DEBATE_PROMPT_VERSION_ID),
        "strict_live_debate_backend_required": bool(strict_live_debate_backend_required),
        "default_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(getattr(role_runners, "advocate", None))
        ),
        "arbiter_provider_request_id": _sanitize_request_id(
            _safe_runner_request_id(getattr(role_runners, "arbiter", None))
        ),
    }


def _build_debate_observability(final_state: Any) -> dict[str, Any]:
    """Build safe, additive debate observability for the step summary.

    Surfaces only bounded counts/booleans + safe source-reference ids + a FIXED arbiter rationale
    summary — never the raw arbiter rationale (which may contain private model text), agent output
    content, the API key, response, raw payload, or a path.
    """
    decisions = list(getattr(final_state, "arbiter_decisions", []) or [])
    return {
        "round_number": final_state.round_number,
        "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
        "agent_output_count": len(final_state.agent_outputs),
        "arbiter_decision_count": len(decisions),
        "dissent_preserved": any(bool(d.dissent_preserved) for d in decisions),
        "challenges_validated_count": sum(len(d.challenges_validated) for d in decisions),
        "arbiter_rationale_summary": (
            "arbiter_decision_recorded" if decisions else "no_arbiter_decision"
        ),
        "source_reference_ids": [
            final_state.claim_registry_ref,
            final_state.sanad_graph_ref,
        ],
    }


def _build_debate_role_runners(
    context: Any = None,
    *,
    debate_role_runners_factory: DebateRoleRunnersFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> Any:
    """Build role runners for debate based on env configuration.

    Reads IDIS_DEBATE_BACKEND (default: deterministic).
    Fail-closed: raises ValueError if anthropic backend selected but key missing.

    When ``debate_role_runners_factory`` is supplied it is called with the resolved
    ``DebateRoleRunnerSelection`` and its return value (a complete ``RoleRunners``) is used
    (injection seam for tests / strict wiring). When ``strict_live_debate_backend_required`` is
    True (threaded only from the strict FULL execution path) a non-anthropic backend fails closed
    with ``STRICT_LIVE_DEBATE_REQUIRED`` (deterministic debate is forbidden) and an anthropic
    provider failure surfaces ``STRICT_LIVE_DEBATE_PROVIDER_FAILED``. The non-strict default path
    is unchanged.

    Args:
        context: Optional DebateContext with rich pipeline data for LLM agents.

    Returns:
        RoleRunners instance (deterministic or LLM-backed).

    Raises:
        ValueError: If IDIS_DEBATE_BACKEND=anthropic but ANTHROPIC_API_KEY is unset
            (default path only; an injected factory builds its own runners).
        StrictLiveRoleError: If strict-live debate is required but a live approved backend is
            not available/usable.
    """
    from idis.debate.orchestrator import RoleRunners

    selection = _resolve_debate_selection()

    if selection.backend == "anthropic":
        if strict_live_debate_backend_required:
            return _build_strict_debate_role_runners(
                selection, context, debate_role_runners_factory
            )
        if debate_role_runners_factory is not None:
            return debate_role_runners_factory(selection)
        return _build_live_debate_role_runners(selection, context)

    if strict_live_debate_backend_required:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_DEBATE_REQUIRED,
            message=(
                "Strict live debate requires the anthropic backend; "
                "deterministic debate is forbidden."
            ),
        )
    if debate_role_runners_factory is not None:
        return debate_role_runners_factory(selection)
    return RoleRunners()


def _build_strict_debate_role_runners(
    selection: DebateRoleRunnerSelection,
    context: Any,
    debate_role_runners_factory: DebateRoleRunnersFactory | None,
) -> Any:
    """Build the live debate RoleRunners for strict FULL, failing closed safely.

    Any construction/call failure is surfaced as ``STRICT_LIVE_DEBATE_PROVIDER_FAILED`` with a
    fixed message — never the raw exception message, key, prompt, or provider payload.
    """
    try:
        if debate_role_runners_factory is not None:
            return debate_role_runners_factory(selection)
        return _build_live_debate_role_runners(selection, context)
    except StrictLiveRoleError:
        raise
    except Exception as exc:
        raise StrictLiveRoleError(
            code=STRICT_LIVE_DEBATE_PROVIDER_FAILED,
            message="Strict live debate provider construction or call failed.",
        ) from exc


def _build_live_debate_role_runners(selection: DebateRoleRunnerSelection, context: Any) -> Any:
    """Construct the live (Anthropic) debate RoleRunners (5 LLM role runners)."""
    from idis.debate.orchestrator import RoleRunners
    from idis.debate.roles.llm_role_runner import LLMRoleRunner
    from idis.models.debate import DebateRole
    from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

    prompts = _load_debate_prompts()

    default_client = AnthropicLLMClient(
        model=selection.default_model, max_tokens=selection.max_tokens
    )
    arbiter_client = AnthropicLLMClient(
        model=selection.arbiter_model, max_tokens=selection.max_tokens
    )

    return RoleRunners(
        advocate=LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=default_client,
            system_prompt=prompts["advocate"],
            context=context,
        ),
        sanad_breaker=LLMRoleRunner(
            role=DebateRole.SANAD_BREAKER,
            llm_client=default_client,
            system_prompt=prompts["sanad_breaker"],
            context=context,
        ),
        contradiction_finder=LLMRoleRunner(
            role=DebateRole.CONTRADICTION_FINDER,
            llm_client=default_client,
            system_prompt=prompts["contradiction_finder"],
            context=context,
        ),
        risk_officer=LLMRoleRunner(
            role=DebateRole.RISK_OFFICER,
            llm_client=default_client,
            system_prompt=prompts["risk_officer"],
            context=context,
        ),
        arbiter=LLMRoleRunner(
            role=DebateRole.ARBITER,
            llm_client=arbiter_client,
            system_prompt=prompts["arbiter"],
            context=context,
        ),
    )


def _load_debate_prompts() -> dict[str, str]:
    """Load debate role prompt texts from disk.

    Reads from prompts/<role>/1.0.0/prompt.md for each role.
    Fail-closed: raises FileNotFoundError if any prompt is missing.

    Returns:
        Dict mapping role name to prompt text.
    """
    root = _find_project_root()
    role_dirs = {
        "advocate": "debate_advocate",
        "sanad_breaker": "debate_sanad_breaker",
        "contradiction_finder": "debate_contradiction_finder",
        "risk_officer": "debate_risk_officer",
        "arbiter": "debate_arbiter",
    }

    prompts: dict[str, str] = {}
    for role_key, dir_name in role_dirs.items():
        prompt_path = root / "prompts" / dir_name / "1.0.0" / "prompt.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Debate prompt file not found: {prompt_path}")
        prompts[role_key] = prompt_path.read_text(encoding="utf-8")

    return prompts


def clear_runs_store() -> None:
    """Clear the in-memory runs store. For testing only."""
    from idis.persistence.repositories.runs import clear_in_memory_runs_store

    clear_in_memory_runs_store()
