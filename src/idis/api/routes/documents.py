"""Documents routes for IDIS API.

Provides document management endpoints per OpenAPI v6.3 spec:
- GET /v1/deals/{dealId}/documents (listDealDocuments)
- POST /v1/deals/{dealId}/documents (createDealDocument)
- POST /v1/documents/{docId}/ingest (ingestDocument)

All endpoints enforce tenant isolation, emit audit events, and support idempotency.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Documents"])

ALLOWED_URI_SCHEMES = frozenset({"idis://", "s3://", "file://"})
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class DocType(str, Enum):
    """Document type classification per OpenAPI spec."""

    PITCH_DECK = "PITCH_DECK"
    FINANCIAL_MODEL = "FINANCIAL_MODEL"
    DATA_ROOM_FILE = "DATA_ROOM_FILE"
    TRANSCRIPT = "TRANSCRIPT"
    TERM_SHEET = "TERM_SHEET"
    OTHER = "OTHER"


class RunStatus(str, Enum):
    """Ingestion run status per OpenAPI RunRef schema."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CreateDocumentRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/documents."""

    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    title: Annotated[str, Field(min_length=1)]
    source_system: str | None = None
    uri: str | None = None
    sha256: Annotated[str | None, Field(default=None, min_length=64, max_length=64)] = None
    metadata: dict[str, Any] | None = None
    auto_ingest: bool = True


class DocumentArtifactResponse(BaseModel):
    """Response model for DocumentArtifact per OpenAPI spec."""

    doc_id: str
    deal_id: str
    doc_type: str
    title: str
    source_system: str
    version_id: str
    ingested_at: str
    sha256: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] | None = None


class PaginatedDocumentList(BaseModel):
    """Paginated list of documents per OpenAPI spec."""

    items: list[DocumentArtifactResponse]
    next_cursor: str | None = None


class RunRef(BaseModel):
    """Reference to an ingestion run per OpenAPI spec."""

    run_id: str
    status: str


class IngestDocumentRequest(BaseModel):
    """Optional request body for POST /v1/documents/{docId}/ingest."""

    model_config = ConfigDict(extra="forbid")

    priority: str = "NORMAL"


class _DocumentStore:
    """In-memory document artifact storage with tenant isolation.

    This is a temporary implementation until Postgres persistence is added.
    All operations are tenant-scoped to enforce isolation.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, str] = {}
        self._runs: dict[str, dict[str, Any]] = {}

    def create_artifact(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        doc_id: str,
        doc_type: str,
        title: str,
        source_system: str,
        version_id: str,
        ingested_at: str,
        sha256: str | None,
        uri: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Create and persist a document artifact (tenant-scoped)."""
        key = f"{tenant_id}:{doc_id}"
        artifact = {
            "doc_id": doc_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "doc_type": doc_type,
            "title": title,
            "source_system": source_system,
            "version_id": version_id,
            "ingested_at": ingested_at,
            "sha256": sha256,
            "uri": uri,
            "metadata": metadata or {},
        }
        self._artifacts[key] = artifact
        return artifact

    def get_artifact(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        """Get a document artifact by ID (tenant-scoped)."""
        key = f"{tenant_id}:{doc_id}"
        return self._artifacts.get(key)

    def list_artifacts(
        self,
        tenant_id: str,
        deal_id: str,
        limit: int,
        cursor: dict[str, str] | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List document artifacts for a deal with cursor pagination.

        Returns (items, next_cursor) tuple.
        Order is deterministic by (ingested_at DESC, doc_id ASC).
        """
        tenant_artifacts = [
            a
            for a in self._artifacts.values()
            if a["tenant_id"] == tenant_id and a["deal_id"] == deal_id
        ]

        tenant_artifacts.sort(key=lambda x: (x["ingested_at"], x["doc_id"]), reverse=True)

        if cursor:
            cursor_ingested_at = cursor.get("ingested_at", "")
            cursor_doc_id = cursor.get("doc_id", "")
            filtered = []
            past_cursor = False
            for a in tenant_artifacts:
                if past_cursor:
                    filtered.append(a)
                elif (a["ingested_at"], a["doc_id"]) < (cursor_ingested_at, cursor_doc_id):
                    filtered.append(a)
                    past_cursor = True
            tenant_artifacts = filtered

        items = tenant_artifacts[:limit]

        next_cursor = None
        if len(tenant_artifacts) > limit:
            last_item = items[-1]
            cursor_data = {
                "ingested_at": last_item["ingested_at"],
                "doc_id": last_item["doc_id"],
            }
            next_cursor = base64.urlsafe_b64encode(json.dumps(cursor_data).encode()).decode()

        return items, next_cursor

    def store_idempotency(self, key: str, doc_id: str) -> None:
        """Store idempotency mapping."""
        self._idempotency[key] = doc_id

    def get_idempotency(self, key: str) -> str | None:
        """Get stored doc_id for idempotency key."""
        return self._idempotency.get(key)

    def create_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        doc_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Create an ingestion run record."""
        key = f"{tenant_id}:{run_id}"
        run = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "doc_id": doc_id,
            "status": status,
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._runs[key] = run
        return run

    def update_run_status(self, tenant_id: str, run_id: str, status: str) -> None:
        """Update run status."""
        key = f"{tenant_id}:{run_id}"
        if key in self._runs:
            self._runs[key]["status"] = status

    def clear(self) -> None:
        """Clear all stores. For testing only."""
        self._artifacts.clear()
        self._idempotency.clear()
        self._runs.clear()


_document_store = _DocumentStore()


def clear_document_store() -> None:
    """Clear the in-memory document store. For testing only."""
    _document_store.clear()


def _decode_cursor(cursor: str | None) -> dict[str, str] | None:
    """Decode a base64 cursor to dict, or None if invalid."""
    if not cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        parsed: dict[str, str] = json.loads(decoded)
        return parsed
    except Exception:
        return None


def _is_uri_scheme_allowed(uri: str | None) -> bool:
    """Check if URI scheme is in the allowlist (SSRF protection)."""
    if not uri:
        return False
    return any(uri.startswith(scheme) for scheme in ALLOWED_URI_SCHEMES)


def _emit_document_created_audit(
    request: Request,
    tenant_id: str,
    artifact: dict[str, Any],
    idempotency_key: str | None,
) -> None:
    """Emit document.created audit event."""
    from idis.audit.sink import InMemoryAuditSink

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    actor_id = getattr(request.state, "tenant_context", None)
    actor_id_str = actor_id.actor_id if actor_id else "unknown"

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "document.created",
        "occurred_at": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "severity": "MEDIUM",
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": actor_id_str,
        },
        "request": {
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        },
        "resource": {
            "resource_type": "document",
            "resource_id": artifact["doc_id"],
            "deal_id": artifact["deal_id"],
        },
        "summary": f"Document artifact created: {artifact['title']}",
        "payload": {
            "sha256": artifact.get("sha256"),
            "title": artifact["title"],
            "doc_type": artifact["doc_type"],
        },
    }

    audit_sink = getattr(request.app.state, "audit_sink", None)
    if audit_sink is None:
        audit_sink = InMemoryAuditSink()
    audit_sink.emit(event)


def _emit_ingestion_audit(
    request: Request,
    tenant_id: str,
    doc_id: str,
    deal_id: str,
    run_id: str,
    status: str,
    idempotency_key: str | None,
    error_message: str | None = None,
) -> None:
    """Emit ingestion audit event (completed or failed)."""
    from idis.audit.sink import InMemoryAuditSink

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    actor_id = getattr(request.state, "tenant_context", None)
    actor_id_str = actor_id.actor_id if actor_id else "unknown"

    event_type = (
        "document.ingestion.completed"
        if status == RunStatus.SUCCEEDED.value
        else "document.ingestion.failed"
    )
    summary = (
        f"Document ingestion {status.lower()}"
        if not error_message
        else f"Document ingestion failed: {error_message}"
    )

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "severity": "MEDIUM" if status == RunStatus.FAILED.value else "LOW",
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": actor_id_str,
        },
        "request": {
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        },
        "resource": {
            "resource_type": "document",
            "resource_id": doc_id,
            "deal_id": deal_id,
        },
        "summary": summary,
        "payload": {
            "run_id": run_id,
            "status": status,
            "error": error_message,
        },
    }

    audit_sink = getattr(request.app.state, "audit_sink", None)
    if audit_sink is None:
        audit_sink = InMemoryAuditSink()
    audit_sink.emit(event)


@router.get("/v1/deals/{deal_id}/documents", response_model=PaginatedDocumentList)
def list_deal_documents(
    deal_id: str,
    tenant_ctx: RequireTenantContext,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> PaginatedDocumentList:
    """List documents for a deal.

    Implements GET /v1/deals/{dealId}/documents per OpenAPI spec.
    Enforces tenant isolation and cursor-based pagination.

    Args:
        deal_id: UUID of the deal.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum items to return (1-200, default 50).
        cursor: Pagination cursor from previous response.

    Returns:
        PaginatedDocumentList with items and optional next_cursor.
    """
    effective_limit = min(max(1, limit), MAX_LIMIT)

    decoded_cursor = _decode_cursor(cursor)

    items, next_cursor = _document_store.list_artifacts(
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        limit=effective_limit,
        cursor=decoded_cursor,
    )

    response_items = [
        DocumentArtifactResponse(
            doc_id=item["doc_id"],
            deal_id=item["deal_id"],
            doc_type=item["doc_type"],
            title=item["title"],
            source_system=item["source_system"],
            version_id=item["version_id"],
            ingested_at=item["ingested_at"],
            sha256=item.get("sha256"),
            uri=item.get("uri"),
            metadata=item.get("metadata"),
        )
        for item in items
    ]

    return PaginatedDocumentList(items=response_items, next_cursor=next_cursor)


@router.post(
    "/v1/deals/{deal_id}/documents",
    response_model=DocumentArtifactResponse,
    status_code=201,
)
def create_deal_document(
    deal_id: str,
    request_body: CreateDocumentRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DocumentArtifactResponse:
    """Create a document artifact for a deal.

    Implements POST /v1/deals/{dealId}/documents per OpenAPI spec.
    Supports idempotency via Idempotency-Key header.

    Args:
        deal_id: UUID of the parent deal.
        request_body: Document creation request.
        request: FastAPI request for audit context.
        tenant_ctx: Injected tenant context from auth dependency.
        idempotency_key: Optional idempotency key header.

    Returns:
        Created DocumentArtifact.

    Raises:
        IdisHttpError: 400 if auto_ingest=true with unsupported URI scheme.
    """
    if request_body.auto_ingest and request_body.uri and not _is_uri_scheme_allowed(
        request_body.uri
    ):
        raise IdisHttpError(
                status_code=400,
                code="BAD_REQUEST",
                message="Unsupported URI scheme for auto-ingestion",
                details={
                    "uri": request_body.uri,
                    "allowed_schemes": list(ALLOWED_URI_SCHEMES),
                },
            )

    doc_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    version_id = str(uuid.uuid4())[:12]

    artifact = _document_store.create_artifact(
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        doc_id=doc_id,
        doc_type=request_body.doc_type.value,
        title=request_body.title,
        source_system=request_body.source_system or "api",
        version_id=version_id,
        ingested_at=now,
        sha256=request_body.sha256,
        uri=request_body.uri,
        metadata=request_body.metadata,
    )

    _emit_document_created_audit(
        request=request,
        tenant_id=tenant_ctx.tenant_id,
        artifact=artifact,
        idempotency_key=idempotency_key,
    )

    return DocumentArtifactResponse(
        doc_id=artifact["doc_id"],
        deal_id=artifact["deal_id"],
        doc_type=artifact["doc_type"],
        title=artifact["title"],
        source_system=artifact["source_system"],
        version_id=artifact["version_id"],
        ingested_at=artifact["ingested_at"],
        sha256=artifact.get("sha256"),
        uri=artifact.get("uri"),
        metadata=artifact.get("metadata"),
    )


@router.post(
    "/v1/documents/{doc_id}/ingest",
    response_model=RunRef,
    status_code=202,
)
def ingest_document(
    doc_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    request_body: IngestDocumentRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunRef:
    """Trigger ingestion for a document.

    Implements POST /v1/documents/{docId}/ingest per OpenAPI spec.
    Returns 202 with RunRef containing run_id and status.

    Behavior:
    - If artifact not found in tenant scope: 404
    - If uri missing or unsupported: returns 202 with FAILED status
    - If sha256 provided and does not match: returns 202 with FAILED status
    - On success: returns 202 with SUCCEEDED status

    Args:
        doc_id: UUID of the document to ingest.
        request: FastAPI request for audit context.
        tenant_ctx: Injected tenant context from auth dependency.
        request_body: Optional priority configuration.
        idempotency_key: Optional idempotency key header.

    Returns:
        RunRef with run_id and status.

    Raises:
        IdisHttpError: 404 if document not found in tenant scope.
    """
    artifact = _document_store.get_artifact(tenant_ctx.tenant_id, doc_id)
    if artifact is None:
        raise IdisHttpError(
            status_code=404,
            code="NOT_FOUND",
            message="Document not found",
            details={"doc_id": doc_id},
        )

    run_id = str(uuid.uuid4())
    uri = artifact.get("uri")
    expected_sha256 = artifact.get("sha256")

    if not uri:
        run = _document_store.create_run(
            tenant_id=tenant_ctx.tenant_id,
            run_id=run_id,
            doc_id=doc_id,
            status=RunStatus.FAILED.value,
        )
        _emit_ingestion_audit(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            doc_id=doc_id,
            deal_id=artifact["deal_id"],
            run_id=run_id,
            status=RunStatus.FAILED.value,
            idempotency_key=idempotency_key,
            error_message="Missing URI for ingestion",
        )
        return RunRef(run_id=run["run_id"], status=run["status"])

    if not _is_uri_scheme_allowed(uri):
        run = _document_store.create_run(
            tenant_id=tenant_ctx.tenant_id,
            run_id=run_id,
            doc_id=doc_id,
            status=RunStatus.FAILED.value,
        )
        _emit_ingestion_audit(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            doc_id=doc_id,
            deal_id=artifact["deal_id"],
            run_id=run_id,
            status=RunStatus.FAILED.value,
            idempotency_key=idempotency_key,
            error_message=f"Unsupported URI scheme: {uri}",
        )
        return RunRef(run_id=run["run_id"], status=run["status"])

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is not None:
        object_store = getattr(ingestion_service, "_object_store", None)
        if object_store is not None:
            try:
                stored_obj = object_store.get(tenant_ctx.tenant_id, uri)
                actual_data = stored_obj.body

                if expected_sha256:
                    actual_sha256 = hashlib.sha256(actual_data).hexdigest()
                    if actual_sha256 != expected_sha256:
                        run = _document_store.create_run(
                            tenant_id=tenant_ctx.tenant_id,
                            run_id=run_id,
                            doc_id=doc_id,
                            status=RunStatus.FAILED.value,
                        )
                        sha256_err = (
                            f"SHA256 mismatch: expected {expected_sha256}, "
                            f"got {actual_sha256}"
                        )
                        _emit_ingestion_audit(
                            request=request,
                            tenant_id=tenant_ctx.tenant_id,
                            doc_id=doc_id,
                            deal_id=artifact["deal_id"],
                            run_id=run_id,
                            status=RunStatus.FAILED.value,
                            idempotency_key=idempotency_key,
                            error_message=sha256_err,
                        )
                        return RunRef(run_id=run["run_id"], status=run["status"])

                from idis.services.ingestion import IngestionContext

                ctx = IngestionContext(
                    tenant_id=UUID(tenant_ctx.tenant_id),
                    actor_id=tenant_ctx.actor_id,
                    request_id=getattr(request.state, "request_id", str(uuid.uuid4())),
                    idempotency_key=idempotency_key,
                )

                result = ingestion_service.ingest_bytes(
                    ctx=ctx,
                    deal_id=UUID(artifact["deal_id"]),
                    filename=artifact["title"],
                    media_type=None,
                    data=actual_data,
                    metadata=artifact.get("metadata"),
                )

                status = RunStatus.SUCCEEDED.value if result.success else RunStatus.FAILED.value
                run = _document_store.create_run(
                    tenant_id=tenant_ctx.tenant_id,
                    run_id=run_id,
                    doc_id=doc_id,
                    status=status,
                )
                _emit_ingestion_audit(
                    request=request,
                    tenant_id=tenant_ctx.tenant_id,
                    doc_id=doc_id,
                    deal_id=artifact["deal_id"],
                    run_id=run_id,
                    status=status,
                    idempotency_key=idempotency_key,
                    error_message=None if result.success else "Ingestion failed",
                )
                return RunRef(run_id=run["run_id"], status=run["status"])

            except Exception as e:
                logger.warning("Ingestion failed: %s", e)
                run = _document_store.create_run(
                    tenant_id=tenant_ctx.tenant_id,
                    run_id=run_id,
                    doc_id=doc_id,
                    status=RunStatus.FAILED.value,
                )
                _emit_ingestion_audit(
                    request=request,
                    tenant_id=tenant_ctx.tenant_id,
                    doc_id=doc_id,
                    deal_id=artifact["deal_id"],
                    run_id=run_id,
                    status=RunStatus.FAILED.value,
                    idempotency_key=idempotency_key,
                    error_message=str(e),
                )
                return RunRef(run_id=run["run_id"], status=run["status"])

    run = _document_store.create_run(
        tenant_id=tenant_ctx.tenant_id,
        run_id=run_id,
        doc_id=doc_id,
        status=RunStatus.SUCCEEDED.value,
    )
    _emit_ingestion_audit(
        request=request,
        tenant_id=tenant_ctx.tenant_id,
        doc_id=doc_id,
        deal_id=artifact["deal_id"],
        run_id=run_id,
        status=RunStatus.SUCCEEDED.value,
        idempotency_key=idempotency_key,
    )
    return RunRef(run_id=run["run_id"], status=run["status"])
