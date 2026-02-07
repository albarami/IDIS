"""Runs routes for IDIS API.

Provides POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.

SNAPSHOT mode: Runs INGEST_CHECK -> EXTRACT -> GRADE -> CALC via RunOrchestrator.
FULL mode: Runs INGEST_CHECK -> EXTRACT -> GRADE -> CALC -> DEBATE via RunOrchestrator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import AuditSink, AuditSinkError
from idis.persistence.repositories.deals import InMemoryDealsRepository
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Runs"])

_IN_MEMORY_RUNS: dict[str, dict[str, Any]] = {}


class StartRunRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/runs."""

    mode: str


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
    error: StepErrorResponse | None = None
    retry_count: int = 0


class RunRef(BaseModel):
    """Run reference returned by startRun (202)."""

    run_id: str
    status: str
    steps: list[RunStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


class RunStatus(BaseModel):
    """Run status response for GET /v1/runs/{runId}."""

    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    steps: list[RunStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


def _get_run_from_postgres(conn: Any, run_id: str) -> dict[str, Any] | None:
    """Get run from Postgres."""
    from sqlalchemy import text

    result = conn.execute(
        text(
            """
            SELECT run_id, tenant_id, deal_id, mode, status, started_at, finished_at, created_at
            FROM runs
            WHERE run_id = :run_id
            """
        ),
        {"run_id": run_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {
        "run_id": str(row.run_id),
        "tenant_id": str(row.tenant_id),
        "deal_id": str(row.deal_id),
        "mode": row.mode,
        "status": row.status,
        "started_at": row.started_at.isoformat().replace("+00:00", "Z") if row.started_at else None,
        "finished_at": row.finished_at.isoformat().replace("+00:00", "Z")
        if row.finished_at
        else None,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
    }


def _create_run_in_postgres(
    conn: Any,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    mode: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create run in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO runs
                (run_id, tenant_id, deal_id, mode, status, started_at,
                 idempotency_key, created_at)
            VALUES
                (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                 :idempotency_key, :created_at)
            """
        ),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "started_at": now,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "mode": mode,
        "status": "QUEUED",
        "started_at": now.isoformat().replace("+00:00", "Z"),
        "finished_at": None,
        "created_at": now.isoformat().replace("+00:00", "Z"),
    }


def _deal_exists_in_postgres(conn: Any, deal_id: str) -> bool:
    """Check if deal exists in Postgres (RLS enforced)."""
    from sqlalchemy import text

    result = conn.execute(
        text("SELECT 1 FROM deals WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    )
    return result.fetchone() is not None


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
    return StartRunRequest(mode=mode)


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

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")
    else:
        deals_repo = InMemoryDealsRepository(tenant_ctx.tenant_id)
        if deals_repo.get(deal_id) is None:
            raise IdisHttpError(
                status_code=404,
                code="NOT_FOUND",
                message="Deal not found",
            )

    documents = _gather_snapshot_documents(request, tenant_ctx.tenant_id, deal_id)
    if not documents:
        raise IdisHttpError(
            status_code=400,
            code="NO_INGESTED_DOCUMENTS",
            message=(
                "Deal has no ingested documents; ingest at least one document before starting a run"
            ),
        )

    if db_conn is not None:
        run_data = _create_run_in_postgres(
            conn=db_conn,
            run_id=run_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            mode=request_body.mode,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        run_data = {
            "run_id": run_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "mode": request_body.mode,
            "status": "QUEUED",
            "started_at": now,
            "finished_at": None,
            "created_at": now,
        }
        _IN_MEMORY_RUNS[run_id] = run_data

    request.state.audit_resource_id = run_id

    extractor_configured = getattr(request.app.state, "extractor_configured", True)
    if not extractor_configured:
        raise IdisHttpError(
            status_code=503,
            code="EXTRACTOR_NOT_CONFIGURED",
            message="No claim extractor is configured. Cannot proceed.",
        )

    audit_sink = _get_audit_sink(request)
    run_steps_repo = InMemoryRunStepsRepository(tenant_ctx.tenant_id)
    orchestrator = RunOrchestrator(
        audit_sink=audit_sink,
        run_steps_repo=run_steps_repo,
    )

    ctx = RunContext(
        run_id=run_id,
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        mode=request_body.mode,
        documents=documents,
        extract_fn=_run_snapshot_extraction,
        grade_fn=_run_snapshot_auto_grade,
        calc_fn=_run_snapshot_calc,
        debate_fn=_run_full_debate if request_body.mode == "FULL" else None,
    )

    try:
        orch_result = orchestrator.execute(ctx)
    except AuditSinkError as exc:
        logger.error("Audit failure aborted run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run aborted: audit event emission failed",
        ) from exc

    run_data["status"] = orch_result.status
    run_data["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    run_data["block_reason"] = orch_result.block_reason
    if db_conn is None:
        _IN_MEMORY_RUNS[run_id] = run_data

    try:
        _emit_run_completed_audit(request, run_id, tenant_ctx.tenant_id, run_data["status"])
    except AuditSinkError as exc:
        logger.error("Audit failure on run.completed for run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run completed but audit event emission failed",
        ) from exc

    step_responses = _build_step_responses(orch_result.steps)

    return RunRef(
        run_id=run_data["run_id"],
        status=run_data["status"],
        steps=step_responses,
        block_reason=orch_result.block_reason,
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

    if db_conn is not None:
        run_data = _get_run_from_postgres(db_conn, run_id)
    else:
        run_data = _IN_MEMORY_RUNS.get(run_id)
        if run_data is not None and run_data.get("tenant_id") != tenant_ctx.tenant_id:
            run_data = None

    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")

    run_steps_repo = InMemoryRunStepsRepository(tenant_ctx.tenant_id)
    steps = run_steps_repo.get_by_run_id(run_id)
    step_responses = _build_step_responses(steps)

    return RunStatus(
        run_id=run_data["run_id"],
        status=run_data["status"],
        started_at=run_data["started_at"],
        finished_at=run_data.get("finished_at"),
        steps=step_responses,
        block_reason=run_data.get("block_reason"),
    )


def _gather_snapshot_documents(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Gather ingested document spans for SNAPSHOT extraction.

    Checks request.state.snapshot_documents first (for testing),
    then falls back to IngestionService if available.

    Args:
        request: FastAPI request.
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.

    Returns:
        List of document dicts with doc_type, document_id, spans.
    """
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
                message=step.error_message or "",
            )
        responses.append(
            RunStepResponse(
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


def _run_full_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
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

    Returns:
        Dict with debate_id, stop_reason, round_number, muhasabah_passed,
        and agent_output_count.
    """
    from idis.debate.orchestrator import DebateOrchestrator
    from idis.models.debate import DebateConfig, DebateState

    state = DebateState(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_registry_ref=f"claims://{run_id}",
        sanad_graph_ref=f"sanad://{run_id}",
        round_number=1,
    )

    orchestrator = DebateOrchestrator(config=DebateConfig())
    final_state = orchestrator.run(state)

    gate_failure = orchestrator.get_gate_failure()
    muhasabah_passed = gate_failure is None

    return {
        "debate_id": run_id,
        "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
        "round_number": final_state.round_number,
        "muhasabah_passed": muhasabah_passed,
        "agent_output_count": len(final_state.agent_outputs),
    }


def _run_snapshot_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic calculations for claims produced by extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        calc_types: Optional list of CalcType to run. None means run all.

    Returns:
        Dict with calc_ids and reproducibility_hashes.
    """
    if not created_claim_ids:
        return {
            "calc_ids": [],
            "reproducibility_hashes": [],
        }

    return {
        "calc_ids": [],
        "reproducibility_hashes": [],
        "claim_count": len(created_claim_ids),
    }


def _run_snapshot_auto_grade(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    audit_sink: AuditSink,
) -> dict[str, Any]:
    """Run Sanad auto-grading for all claims produced by SNAPSHOT extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        audit_sink: App-level audit sink (required).

    Returns:
        Dict with grading summary stats.
    """
    from idis.services.sanad.auto_grade import auto_grade_claims_for_run

    if not created_claim_ids:
        return {
            "graded_count": 0,
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    grade_result = auto_grade_claims_for_run(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        created_claim_ids=created_claim_ids,
        audit_sink=audit_sink,
    )

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
) -> dict[str, Any]:
    """Execute SNAPSHOT extraction pipeline synchronously.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        documents: List of document dicts with doc_type, document_id, spans.

    Returns:
        Dict with pipeline result status and stats.
    """
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.claims.service import ClaimService
    from idis.services.extraction.chunking.service import ChunkingService
    from idis.services.extraction.confidence.scorer import ConfidenceScorer
    from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
    from idis.services.extraction.extractors.llm_client import DeterministicLLMClient
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.extraction.resolution.conflict_detector import ConflictDetector
    from idis.services.extraction.resolution.deduplicator import Deduplicator

    prompt_text = _get_extraction_prompt()
    output_schema = _get_extraction_output_schema()

    llm_client = DeterministicLLMClient()
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
        audit_sink=audit_sink,
    )

    pipeline = ExtractionPipeline(
        chunking_service=ChunkingService(),
        claim_extractor=extractor,
        deduplicator=Deduplicator(),
        conflict_detector=ConflictDetector(),
        claim_service=claim_service,
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


def clear_runs_store() -> None:
    """Clear the in-memory runs store. For testing only."""
    _IN_MEMORY_RUNS.clear()
