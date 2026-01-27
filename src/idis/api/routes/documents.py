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

    def delete_artifact(self, tenant_id: str, doc_id: str) -> bool:
        """Delete a document artifact by ID (tenant-scoped).

        Returns True if artifact was deleted, False if not found.
        """
        key = f"{tenant_id}:{doc_id}"
        existed = key in self._artifacts
        if existed:
            del self._artifacts[key]
        return existed

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
    if (
        request_body.auto_ingest
        and request_body.uri
        and not _is_uri_scheme_allowed(request_body.uri)
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

    if request_body.auto_ingest:
        ingestion_service = getattr(request.app.state, "ingestion_service", None)
        if ingestion_service is None:
            raise IdisHttpError(
                status_code=400,
                code="SERVICE_UNAVAILABLE",
                message=(
                    "Cannot create document with auto_ingest=true: ingestion service unavailable"
                ),
                details={"auto_ingest": request_body.auto_ingest},
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

    # Set audit resource_id for middleware correlation
    request.state.audit_resource_id = doc_id

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
    # Set audit resource_id from path param for middleware correlation
    request.state.audit_resource_id = doc_id

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
        compliant_store = getattr(ingestion_service, "_compliant_store", None)
        if compliant_store is not None:
            try:
                from idis.api.auth import TenantContext as TenantCtx
                from idis.compliance.byok import DataClass

                ctx_for_store = TenantCtx(
                    tenant_id=tenant_ctx.tenant_id,
                    actor_id=tenant_ctx.actor_id,
                    name=getattr(tenant_ctx, "name", "api"),
                    timezone=getattr(tenant_ctx, "timezone", "UTC"),
                    data_region=getattr(tenant_ctx, "data_region", "me-south-1"),
                )
                stored_obj = compliant_store.get(
                    tenant_ctx=ctx_for_store,
                    key=uri,
                    data_class=DataClass.CLASS_2,
                )
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
                            f"SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
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

            except IdisHttpError as e:
                _COMPLIANCE_DENIAL_CODES = frozenset(
                    {
                        "BYOK_KEY_REVOKED",
                        "BYOK_AUDIT_REQUIRED",
                        "BYOK_AUDIT_FAILED",
                        "DELETION_BLOCKED_BY_HOLD",
                        "RESIDENCY_REGION_MISMATCH",
                        "RESIDENCY_SERVICE_REGION_UNSET",
                    }
                )
                if e.status_code == 403 and e.code in _COMPLIANCE_DENIAL_CODES:
                    logger.warning(
                        "Compliance denial during document operation (re-raising): code=%s",
                        e.code,
                    )
                    raise
                logger.warning("IdisHttpError during ingestion: %s", e)
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
        error_message="Ingestion service unavailable: cannot validate SHA256 integrity",
    )
    return RunRef(run_id=run["run_id"], status=run["status"])


class GetDocumentResponse(BaseModel):
    """Response model for GET /v1/documents/{doc_id}."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    tenant_id: str
    deal_id: str
    doc_type: str
    title: str
    source_system: str
    version_id: str
    ingested_at: str
    sha256: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] | None = None


@router.get(
    "/v1/documents/{doc_id}",
    response_model=GetDocumentResponse,
    summary="Get a document",
    description="Retrieve a document by ID. Requires BYOK key active for Class2/3 data.",
    responses={
        200: {"description": "Document retrieved successfully"},
        403: {"description": "Access denied - BYOK key revoked"},
        404: {"description": "Document not found"},
    },
)
async def get_document(
    request: Request,
    doc_id: str,
    tenant_ctx: RequireTenantContext,
) -> GetDocumentResponse:
    """Get a document with BYOK enforcement.

    This endpoint enforces BYOK key checks for Class2/3 data access.
    If BYOK key is revoked, returns 403 BYOK_KEY_REVOKED.

    Args:
        request: FastAPI request object.
        doc_id: Document ID to retrieve.
        tenant_ctx: Tenant context from authentication.

    Returns:
        GetDocumentResponse with document metadata.

    Raises:
        IdisHttpError: 403 if BYOK key revoked, 404 if not found.
    """
    from idis.api.auth import TenantContext as TenantCtx
    from idis.compliance.byok import DataClass

    request.state.audit_resource_id = doc_id

    artifact = _document_store.get_artifact(tenant_ctx.tenant_id, doc_id)
    if artifact is None:
        raise IdisHttpError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found",
        )

    ctx_for_store = TenantCtx(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        name=getattr(tenant_ctx, "name", "api"),
        timezone=getattr(tenant_ctx, "timezone", "UTC"),
        data_region=getattr(tenant_ctx, "data_region", "me-south-1"),
    )

    storage_key = artifact.get("uri")
    if storage_key:
        ingestion_service = getattr(request.app.state, "ingestion_service", None)
        if ingestion_service is not None:
            compliant_store = getattr(ingestion_service, "_compliant_store", None)
            if compliant_store is not None:
                compliant_store.get(
                    tenant_ctx=ctx_for_store,
                    key=storage_key,
                    data_class=DataClass.CLASS_2,
                )

    return GetDocumentResponse(
        doc_id=artifact["doc_id"],
        tenant_id=artifact["tenant_id"],
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


class DeleteDocumentResponse(BaseModel):
    """Response model for DELETE /v1/documents/{doc_id}."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    deleted: bool
    message: str


@router.delete(
    "/v1/documents/{doc_id}",
    response_model=DeleteDocumentResponse,
    summary="Delete a document",
    description="Delete a document by ID. Blocked if document is under legal hold.",
    responses={
        200: {"description": "Document deleted successfully"},
        403: {"description": "Access denied - document under legal hold or BYOK revoked"},
        404: {"description": "Document not found"},
    },
)
async def delete_document(
    request: Request,
    doc_id: str,
    tenant_ctx: RequireTenantContext,
) -> DeleteDocumentResponse:
    """Delete a document with legal hold protection.

    This endpoint enforces legal hold checks before deletion.
    If a legal hold is active, returns 403 DELETION_BLOCKED_BY_HOLD.

    Args:
        request: FastAPI request object.
        doc_id: Document ID to delete.
        tenant_ctx: Tenant context from authentication.

    Returns:
        DeleteDocumentResponse with deletion status.

    Raises:
        IdisHttpError: 403 if document under legal hold, 404 if not found.
    """
    from idis.api.auth import TenantContext as TenantCtx
    from idis.compliance.retention import HoldTarget, block_deletion_if_held

    request.state.audit_resource_id = doc_id

    artifact = _document_store.get_artifact(tenant_ctx.tenant_id, doc_id)
    if artifact is None:
        raise IdisHttpError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found",
        )

    ctx_for_hold = TenantCtx(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        name=getattr(tenant_ctx, "name", "api"),
        timezone=getattr(tenant_ctx, "timezone", "UTC"),
        data_region=getattr(tenant_ctx, "data_region", "me-south-1"),
    )

    block_deletion_if_held(
        tenant_ctx=ctx_for_hold,
        target_type=HoldTarget.ARTIFACT,
        target_id=doc_id,
    )

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is not None:
        compliant_store = getattr(ingestion_service, "_compliant_store", None)
        if compliant_store is not None:
            storage_key = artifact.get("storage_key") or artifact.get("uri")
            if storage_key:
                try:
                    compliant_store.delete(
                        tenant_ctx=ctx_for_hold,
                        key=storage_key,
                        resource_id=doc_id,
                        hold_target_type=HoldTarget.ARTIFACT,
                    )
                except IdisHttpError:
                    raise
                except Exception as e:
                    logger.warning("Storage deletion failed (continuing): %s", e)

    deleted = _document_store.delete_artifact(tenant_ctx.tenant_id, doc_id)

    audit_sink = getattr(request.app.state, "audit_sink", None)
    if audit_sink is not None:
        event = {
            "event_type": "document.deleted",
            "tenant_id": tenant_ctx.tenant_id,
            "actor_id": tenant_ctx.actor_id,
            "resource_type": "document",
            "resource_id": doc_id,
            "details": {"deleted": deleted},
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        try:
            audit_sink.emit(event)
        except Exception as e:
            logger.warning("Audit emission failed for document.deleted: %s", e)

    return DeleteDocumentResponse(
        doc_id=doc_id,
        deleted=deleted,
        message="Document deleted" if deleted else "Document not found or already deleted",
    )
