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
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import AuditSinkError
from idis.parsers.registry import detect_format, is_image_source, is_media_source
from idis.services.ingestion import IngestionContext
from idis.services.ingestion.service import (
    DEFAULT_MAX_BYTES,
    RouteValidatedSha256,
    UploadIngestionPhase,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Documents"])

ALLOWED_URI_SCHEMES = frozenset({"idis://", "s3://"})
UPLOAD_CONTENT_TYPE = "application/octet-stream"
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
FORBIDDEN_SUMMARY_METADATA_KEYS = frozenset(
    {
        "base64",
        "bytes",
        "content",
        "content_b64",
        "content_sha256",
        "excerpt",
        "parsed_text",
        "raw_bytes",
        "raw_content",
        "raw_text",
        "span",
        "spans",
        "text",
        "text_excerpt",
    }
)


class DocType(StrEnum):
    """Document type classification per OpenAPI spec."""

    PITCH_DECK = "PITCH_DECK"
    FINANCIAL_MODEL = "FINANCIAL_MODEL"
    DATA_ROOM_FILE = "DATA_ROOM_FILE"
    TRANSCRIPT = "TRANSCRIPT"
    TERM_SHEET = "TERM_SHEET"
    OTHER = "OTHER"


class RunStatus(StrEnum):
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
    document_id: str | None = None
    parse_status: str | None = None
    sha256: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] | None = None
    source_metadata: dict[str, Any] | None = None


class PaginatedDocumentList(BaseModel):
    """Paginated list of documents per OpenAPI spec."""

    items: list[DocumentArtifactResponse]
    next_cursor: str | None = None


class RunRef(BaseModel):
    """Reference to an ingestion run per OpenAPI spec."""

    run_id: str
    status: str
    document_id: str | None = None


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
        self._ingestion_doc_ids: dict[str, str] = {}

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

    def set_ingestion_document_id(
        self, tenant_id: str, doc_id: str, ingestion_document_id: str
    ) -> None:
        """Map a route doc_id to the IngestionService's internal document_id."""
        key = f"{tenant_id}:{doc_id}"
        self._ingestion_doc_ids[key] = ingestion_document_id
        artifact = self._artifacts.get(key)
        if artifact is not None:
            artifact["document_id"] = ingestion_document_id
            artifact["parse_status"] = "PARSED"

    def get_ingestion_document_id(self, tenant_id: str, doc_id: str) -> str | None:
        """Get the IngestionService document_id for a route doc_id."""
        key = f"{tenant_id}:{doc_id}"
        return self._ingestion_doc_ids.get(key)

    def clear(self) -> None:
        """Clear all stores. For testing only."""
        self._artifacts.clear()
        self._idempotency.clear()
        self._runs.clear()
        self._ingestion_doc_ids.clear()


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


def _decode_offset_cursor(cursor: str | None) -> int:
    """Decode an offset-style cursor used by durable repository listing."""
    decoded = _decode_cursor(cursor)
    if decoded is None:
        return 0
    try:
        return max(0, int(decoded.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _encode_offset_cursor(offset: int) -> str:
    """Encode an offset-style cursor."""
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode()).decode()


def _is_uri_scheme_allowed(uri: str) -> bool:
    """Check if URI scheme is in the allowlist (SSRF protection)."""
    if not uri:
        return False
    return any(uri.startswith(scheme) for scheme in ALLOWED_URI_SCHEMES)


def _reject_unsafe_document_uri(uri: str | None) -> None:
    """Reject public document URIs that imply local files or unsupported sources."""
    if not uri:
        return
    if not _is_uri_scheme_allowed(uri):
        raise IdisHttpError(
            status_code=400,
            code="BAD_REQUEST",
            message="Unsupported URI scheme for document registration",
            details={
                "uri": uri,
                "allowed_schemes": sorted(ALLOWED_URI_SCHEMES),
            },
        )
    object_key = _strip_uri_scheme(uri)
    normalized_key = object_key.replace("\\", "/")
    path_parts = [part for part in normalized_key.split("/") if part]
    first_part = path_parts[0] if path_parts else ""
    is_path_like = (
        object_key.startswith(("/", "\\"))
        or normalized_key.startswith("../")
        or "/../" in normalized_key
        or normalized_key.endswith("/..")
        or (len(first_part) == 2 and first_part[1] == ":")
    )
    if is_path_like:
        raise IdisHttpError(
            status_code=400,
            code="BAD_REQUEST",
            message="Document URI must be an object key, not a local filesystem path",
            details={"uri": uri},
        )


def _reject_unsafe_upload_filename(filename: str) -> None:
    """Reject upload filenames that imply a client-controlled filesystem path."""
    normalized = filename.strip().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    first_part = parts[0] if parts else ""
    is_path_like = (
        not normalized
        or "/" in normalized
        or normalized in {".", ".."}
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
        or normalized.startswith("~")
        or (len(first_part) == 2 and first_part[1] == ":")
    )
    if is_path_like:
        raise IdisHttpError(
            status_code=400,
            code="BAD_REQUEST",
            message="Upload filename must be a filename, not a local filesystem path",
            details={"filename": filename},
        )


def _max_upload_bytes(request: Request) -> int:
    """Return the active ingestion byte limit for route-level fail-fast checks."""
    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return DEFAULT_MAX_BYTES
    max_bytes = getattr(ingestion_service, "_max_bytes", DEFAULT_MAX_BYTES)
    return int(max_bytes)


def _reject_invalid_upload_content_type(request: Request) -> None:
    """Require raw octet-stream uploads for this narrow intake boundary."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != UPLOAD_CONTENT_TYPE:
        raise IdisHttpError(
            status_code=415,
            code="UNSUPPORTED_MEDIA_TYPE",
            message="Document upload requires application/octet-stream",
            details={"content_type": content_type or None},
        )


def _reject_oversized_content_length(request: Request, max_bytes: int) -> None:
    """Reject oversized uploads before reading the body when Content-Length is present."""
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        size_bytes = int(content_length)
    except ValueError:
        return
    if size_bytes > max_bytes:
        raise IdisHttpError(
            status_code=413,
            code="FILE_TOO_LARGE",
            message=f"File exceeds maximum size of {max_bytes} bytes",
            details={"size_bytes": size_bytes, "max_bytes": max_bytes},
        )


def _reject_invalid_upload_body(data: bytes, max_bytes: int) -> None:
    """Reject empty or oversized upload bodies before ingestion."""
    if not data:
        raise IdisHttpError(
            status_code=400,
            code="EMPTY_FILE",
            message="Upload body must not be empty",
        )
    if len(data) > max_bytes:
        raise IdisHttpError(
            status_code=413,
            code="FILE_TOO_LARGE",
            message=f"File exceeds maximum size of {max_bytes} bytes",
            details={"size_bytes": len(data), "max_bytes": max_bytes},
        )


def _reject_sha256_mismatch(
    data: bytes,
    expected_sha256: str | None,
) -> RouteValidatedSha256:
    """Validate optional caller-supplied SHA256 and return the actual digest."""
    try:
        return RouteValidatedSha256.from_bytes(
            data=data,
            expected_sha256=expected_sha256,
        )
    except ValueError as error:
        actual_sha256 = hashlib.sha256(data).hexdigest()
        raise IdisHttpError(
            status_code=400,
            code="SHA256_MISMATCH",
            message="Uploaded bytes do not match the supplied SHA256",
            details={"expected_sha256": expected_sha256, "actual_sha256": actual_sha256},
        ) from error


def _upload_phase_recorder(request: Request) -> object | None:
    return getattr(request.app.state, "upload_ingestion_phase_recorder", None)


def _record_upload_phase(
    phase_recorder: object | None,
    phase: UploadIngestionPhase,
    started_at: float,
) -> None:
    if phase_recorder is None:
        return
    try:
        record_phase = getattr(phase_recorder, "record_phase", None)
    except Exception as error:
        logger.warning(
            "Private upload phase recorder lookup failed: phase=%s exception_type=%s",
            phase.value,
            type(error).__name__,
        )
        return
    if not callable(record_phase):
        return
    try:
        record_phase(phase, time.monotonic() - started_at)
    except Exception as error:
        logger.warning(
            "Private upload phase recorder failed: phase=%s exception_type=%s",
            phase.value,
            type(error).__name__,
        )


def _reject_unsupported_upload_format(data: bytes, filename: str) -> None:
    """Reject unsupported magic bytes before ingestion persists storage or corpus rows."""
    detected_format = detect_format(data)
    if detected_format is not None:
        return
    if is_image_source(filename=filename, mime_type=None) or is_media_source(
        filename=filename,
        mime_type=None,
    ):
        return
    raise IdisHttpError(
        status_code=400,
        code="UNSUPPORTED_FORMAT",
        message="Uploaded document format is not supported",
        details={"filename": filename},
    )


def _safe_ingestion_error_details(errors: list[Any]) -> list[dict[str, str]]:
    """Return ingestion error details without parser internals or byte-derived fields."""
    safe_errors: list[dict[str, str]] = []
    for error in errors:
        code = getattr(getattr(error, "code", None), "value", str(getattr(error, "code", "")))
        message = str(getattr(error, "message", "Ingestion failed"))
        safe_errors.append({"code": code, "message": message})
    return safe_errors


def _validate_durable_deal_scope(request: Request, tenant_id: str, deal_id: str) -> None:
    """Validate deal existence through durable repositories when a DB connection exists."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is None:
        return
    from idis.persistence.repositories.deals import get_deals_repository

    if get_deals_repository(db_conn, tenant_id).get(deal_id) is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")


def _strip_uri_scheme(uri: str) -> str:
    """Strip the URI scheme prefix to produce a storage-safe key.

    Converts logical URIs (file://path, idis://bucket/key, s3://bucket/key)
    to plain storage keys by removing the scheme prefix.

    Args:
        uri: Logical URI with scheme prefix.

    Returns:
        Storage-safe key without scheme.
    """
    for scheme in ALLOWED_URI_SCHEMES:
        if uri.startswith(scheme):
            return uri[len(scheme) :]
    return uri


def _audit_request_metadata(
    *,
    request_id: str,
    idempotency_key: str | None,
) -> dict[str, str]:
    request_metadata = {"request_id": request_id}
    if idempotency_key:
        request_metadata["idempotency_key_sha256"] = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
    return request_metadata


def _emit_document_created_audit(
    request: Request,
    tenant_id: str,
    artifact: dict[str, Any],
    idempotency_key: str | None,
) -> None:
    """Emit document.created audit event."""
    from idis.audit.sink import get_audit_sink

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
        "request": _audit_request_metadata(
            request_id=request_id,
            idempotency_key=idempotency_key,
        ),
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
        audit_sink = get_audit_sink()
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
    from idis.audit.sink import get_audit_sink

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
        "request": _audit_request_metadata(
            request_id=request_id,
            idempotency_key=idempotency_key,
        ),
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
        audit_sink = get_audit_sink()
    audit_sink.emit(event)


def _safe_summary_metadata(value: Any) -> Any:
    """Return JSON-like metadata with raw content/excerpt keys removed."""
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, nested_value in value.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_SUMMARY_METADATA_KEYS:
                continue
            safe_value = _safe_summary_metadata(nested_value)
            if safe_value is not None:
                safe[str(key)] = safe_value
        return safe
    if isinstance(value, list):
        safe_list = [
            safe_item for item in value if (safe_item := _safe_summary_metadata(item)) is not None
        ]
        return safe_list
    return value


def _document_summary_from_durable_row(document: dict[str, Any]) -> DocumentArtifactResponse:
    """Convert a durable document row to a safe public summary."""
    source_metadata = document.get("source_metadata") or {}
    return DocumentArtifactResponse(
        doc_id=str(document["doc_id"]),
        document_id=str(document["document_id"]),
        deal_id=str(document["deal_id"]),
        doc_type=str(document.get("artifact_doc_type") or document["doc_type"]),
        title=str(document.get("document_name") or document["document_id"]),
        source_system=str(source_metadata.get("source_system") or "ingestion"),
        version_id=str(document.get("sha256") or document["doc_id"])[:12],
        ingested_at=str(document.get("created_at") or document.get("updated_at") or ""),
        parse_status=str(document.get("parse_status") or ""),
        sha256=document.get("sha256"),
        uri=document.get("uri"),
        metadata=_safe_summary_metadata(document.get("metadata") or {}),
        source_metadata=_safe_summary_metadata(source_metadata),
    )


def _document_summary_from_memory_artifact(artifact: dict[str, Any]) -> DocumentArtifactResponse:
    """Convert an in-memory artifact to the legacy public summary shape."""
    return DocumentArtifactResponse(
        doc_id=artifact["doc_id"],
        document_id=artifact.get("document_id"),
        deal_id=artifact["deal_id"],
        doc_type=artifact["doc_type"],
        title=artifact["title"],
        source_system=artifact["source_system"],
        version_id=artifact["version_id"],
        ingested_at=artifact["ingested_at"],
        parse_status=artifact.get("parse_status"),
        sha256=artifact.get("sha256"),
        uri=artifact.get("uri"),
        metadata=artifact.get("metadata"),
        source_metadata=None,
    )


def _trigger_auto_ingest(
    *,
    request: Request,
    tenant_ctx: RequireTenantContext,
    artifact: dict[str, Any],
    doc_id: str,
    idempotency_key: str | None,
) -> None:
    """Trigger ingestion via IngestionService during auto_ingest on create.

    Retrieves document bytes from compliant store and calls ingest_bytes().
    On success, stores the ingestion document_id mapping for span retrieval.
    On failure, logs and emits audit but does not fail the create operation.
    Audit emission failures propagate to fail closed.
    """
    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return

    compliant_store = getattr(ingestion_service, "_compliant_store", None)
    if compliant_store is None:
        return

    uri = artifact.get("uri")
    if not uri:
        return

    try:
        from idis.api.auth import TenantContext as TenantCtx
        from idis.compliance.byok import DataClass
        from idis.services.ingestion import IngestionContext

        ctx_for_store = TenantCtx(
            tenant_id=tenant_ctx.tenant_id,
            actor_id=tenant_ctx.actor_id,
            name=getattr(tenant_ctx, "name", "api"),
            timezone=getattr(tenant_ctx, "timezone", "UTC"),
            data_region=getattr(tenant_ctx, "data_region", "me-south-1"),
        )

        storage_key = _strip_uri_scheme(uri)
        stored_obj = compliant_store.get(
            tenant_ctx=ctx_for_store,
            key=storage_key,
            data_class=DataClass.CLASS_2,
        )
        actual_data = stored_obj.body

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
            db_conn=getattr(request.state, "db_conn", None),
        )

        if result.success and result.document_id is not None:
            _document_store.set_ingestion_document_id(
                tenant_ctx.tenant_id, doc_id, str(result.document_id)
            )

        run_id = str(uuid.uuid4())
        status = RunStatus.SUCCEEDED.value if result.success else RunStatus.FAILED.value
        _document_store.create_run(
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
            error_message=None if result.success else "Auto-ingestion failed",
        )
    except AuditSinkError:
        raise
    except Exception as e:
        logger.warning("Auto-ingestion failed for doc_id=%s: %s", doc_id, e)


@router.get("/v1/deals/{deal_id}/documents", response_model=PaginatedDocumentList)
def list_deal_documents(
    request: Request,
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
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.persistence.repositories.documents import PostgresDocumentsRepository

        offset = _decode_offset_cursor(cursor)
        documents = PostgresDocumentsRepository(
            db_conn,
            tenant_ctx.tenant_id,
        ).list_documents_by_deal(deal_id, parsed_only=False)
        page_documents = documents[offset : offset + effective_limit]
        response_items = [
            _document_summary_from_durable_row(document) for document in page_documents
        ]
        next_offset = offset + len(page_documents)
        next_cursor = _encode_offset_cursor(next_offset) if next_offset < len(documents) else None
        return PaginatedDocumentList(items=response_items, next_cursor=next_cursor)

    decoded_cursor = _decode_cursor(cursor)

    items, next_cursor = _document_store.list_artifacts(
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        limit=effective_limit,
        cursor=decoded_cursor,
    )

    response_items = [_document_summary_from_memory_artifact(item) for item in items]

    return PaginatedDocumentList(items=response_items, next_cursor=next_cursor)


@router.get(
    "/v1/deals/{deal_id}/documents/{document_id}",
    response_model=DocumentArtifactResponse,
    summary="Get durable document summary",
    description="Retrieve a safe deal-scoped durable document summary for run-source use.",
    responses={
        200: {"description": "Document summary retrieved successfully"},
        404: {"description": "Document not found"},
    },
)
def get_deal_document_summary(
    deal_id: str,
    document_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> DocumentArtifactResponse:
    """Get a safe durable document summary for one deal/document."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.persistence.repositories.documents import PostgresDocumentsRepository

        document = PostgresDocumentsRepository(db_conn, tenant_ctx.tenant_id).get_document(
            document_id
        )
        if document is None or str(document.get("deal_id")) != deal_id:
            raise IdisHttpError(
                status_code=404,
                code="NOT_FOUND",
                message="Document not found",
            )
        return _document_summary_from_durable_row(document)

    for artifact in _document_store._artifacts.values():
        if artifact.get("tenant_id") != tenant_ctx.tenant_id:
            continue
        if artifact.get("deal_id") != deal_id:
            continue
        if artifact.get("document_id") == document_id or artifact.get("doc_id") == document_id:
            return _document_summary_from_memory_artifact(artifact)

    raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Document not found")


@router.post(
    "/v1/deals/{deal_id}/documents/upload",
    response_model=DocumentArtifactResponse,
    status_code=201,
    summary="Upload and ingest one document",
    description=(
        "Upload raw bytes for a single supported document and ingest them through the "
        "compliance-enforced storage and ingestion pipeline."
    ),
)
async def upload_deal_document(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    filename: Annotated[str, Query(min_length=1, max_length=128)],
    doc_type: Annotated[DocType, Query()],
    sha256: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
    source_system: Annotated[str, Query(min_length=1, max_length=64)] = "api-upload",
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DocumentArtifactResponse:
    """Upload raw bytes for one deal document and return a safe durable summary."""
    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        raise IdisHttpError(
            status_code=400,
            code="SERVICE_UNAVAILABLE",
            message="Cannot upload document: ingestion service unavailable",
        )

    _validate_durable_deal_scope(request, tenant_ctx.tenant_id, deal_id)
    _reject_invalid_upload_content_type(request)
    _reject_unsafe_upload_filename(filename)

    max_bytes = _max_upload_bytes(request)
    _reject_oversized_content_length(request, max_bytes)
    phase_recorder = _upload_phase_recorder(request)
    phase_started_at = time.monotonic()
    try:
        data = await request.body()
        _reject_invalid_upload_body(data, max_bytes)
        actual_sha256 = _reject_sha256_mismatch(data, sha256)
        _reject_unsupported_upload_format(data, filename)
    finally:
        _record_upload_phase(
            phase_recorder,
            UploadIngestionPhase.ROUTE_BODY_READ_HASH_VALIDATION,
            phase_started_at,
        )

    metadata = {
        "doc_type": doc_type.value,
        "source_system": source_system,
        "upload_filename": filename,
        "upload_intake": "single_document_upload",
    }
    ctx = IngestionContext(
        tenant_id=UUID(tenant_ctx.tenant_id),
        actor_id=tenant_ctx.actor_id,
        request_id=getattr(request.state, "request_id", str(uuid.uuid4())),
        idempotency_key=idempotency_key,
    )
    result = ingestion_service.ingest_bytes(
        ctx=ctx,
        deal_id=UUID(deal_id),
        filename=filename,
        media_type=UPLOAD_CONTENT_TYPE,
        data=data,
        metadata=metadata,
        validated_sha256=actual_sha256,
        phase_recorder=phase_recorder,
        db_conn=getattr(request.state, "db_conn", None),
    )
    if result.artifact_id is None or result.document_id is None:
        raise IdisHttpError(
            status_code=400,
            code="DOCUMENT_UPLOAD_FAILED",
            message="Document upload ingestion failed",
            details={"errors": _safe_ingestion_error_details(result.errors)},
        )

    request.state.audit_resource_id = str(result.artifact_id)
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.persistence.repositories.documents import PostgresDocumentsRepository

        document = PostgresDocumentsRepository(db_conn, tenant_ctx.tenant_id).get_document(
            str(result.document_id)
        )
        if document is not None and str(document.get("deal_id")) == deal_id:
            return _document_summary_from_durable_row(document)

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    artifact = _document_store.create_artifact(
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        doc_id=str(result.artifact_id),
        doc_type=doc_type.value,
        title=filename,
        source_system=source_system,
        version_id=actual_sha256.value[:12],
        ingested_at=now,
        sha256=actual_sha256.value,
        uri=result.storage_uri,
        metadata=metadata,
    )
    artifact["parse_status"] = result.parse_status.value if result.parse_status else None
    artifact["document_id"] = str(result.document_id)
    return _document_summary_from_memory_artifact(artifact)


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
    _reject_unsafe_document_uri(request_body.uri)

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

    if request_body.auto_ingest and request_body.uri:
        _trigger_auto_ingest(
            request=request,
            tenant_ctx=tenant_ctx,
            artifact=artifact,
            doc_id=doc_id,
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
        document_id=artifact.get("document_id"),
        parse_status=artifact.get("parse_status"),
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
                storage_key = _strip_uri_scheme(uri)
                stored_obj = compliant_store.get(
                    tenant_ctx=ctx_for_store,
                    key=storage_key,
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
                    db_conn=getattr(request.state, "db_conn", None),
                )

                status = RunStatus.SUCCEEDED.value if result.success else RunStatus.FAILED.value
                if result.success and result.document_id is not None:
                    _document_store.set_ingestion_document_id(
                        tenant_ctx.tenant_id, doc_id, str(result.document_id)
                    )
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
                return RunRef(
                    run_id=run["run_id"],
                    status=run["status"],
                    document_id=str(result.document_id)
                    if result.success and result.document_id is not None
                    else None,
                )

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
            except AuditSinkError:
                raise
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


class SpanResponse(BaseModel):
    """Response model for a single DocumentSpan."""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    document_id: str
    span_type: str
    locator: dict[str, Any]
    text_excerpt: str | None = None


class PaginatedSpanList(BaseModel):
    """Paginated list of spans."""

    items: list[SpanResponse]
    total: int


@router.get(
    "/v1/documents/{doc_id}/spans",
    response_model=PaginatedSpanList,
    summary="Get document spans",
    description="Retrieve spans for a document after ingestion.",
    responses={
        200: {"description": "Spans retrieved successfully"},
        404: {"description": "Document not found or not yet ingested"},
    },
)
def get_document_spans(
    doc_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> PaginatedSpanList:
    """Get spans for a document (tenant-scoped).

    Retrieves spans generated by IngestionService for this document.
    Returns 404 if the document does not exist in this tenant scope.
    Returns empty list if ingestion has not yet produced spans.

    Args:
        doc_id: Document ID to retrieve spans for.
        tenant_ctx: Injected tenant context from auth dependency.
        request: FastAPI request for service access.

    Returns:
        PaginatedSpanList with span items and total count.

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

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return PaginatedSpanList(items=[], total=0)

    ingestion_doc_id = _document_store.get_ingestion_document_id(tenant_ctx.tenant_id, doc_id)
    if ingestion_doc_id is None:
        return PaginatedSpanList(items=[], total=0)

    spans = ingestion_service.get_spans(UUID(tenant_ctx.tenant_id), UUID(ingestion_doc_id))

    span_items = [
        SpanResponse(
            span_id=str(span.span_id),
            document_id=str(span.document_id),
            span_type=span.span_type.value,
            locator=span.locator,
            text_excerpt=span.text_excerpt,
        )
        for span in spans
    ]

    return PaginatedSpanList(items=span_items, total=len(span_items))


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
    content_b64: str
    content_sha256: str


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
    import base64
    import hashlib

    from idis.api.auth import TenantContext as TenantCtx
    from idis.compliance.byok import DataClass
    from idis.storage.errors import ObjectNotFoundError

    request.state.audit_resource_id = doc_id

    artifact = _document_store.get_artifact(tenant_ctx.tenant_id, doc_id)
    if artifact is None:
        raise IdisHttpError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found",
        )

    raw_uri = artifact.get("uri")
    if not raw_uri:
        raise IdisHttpError(
            status_code=404,
            code="DOCUMENT_CONTENT_NOT_FOUND",
            message="Document content not found",
        )
    storage_key = _strip_uri_scheme(raw_uri)

    ctx_for_store = TenantCtx(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        name=getattr(tenant_ctx, "name", "api"),
        timezone=getattr(tenant_ctx, "timezone", "UTC"),
        data_region=getattr(tenant_ctx, "data_region", "me-south-1"),
    )

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        raise IdisHttpError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="Ingestion service unavailable",
        )

    compliant_store = getattr(ingestion_service, "_compliant_store", None)
    if compliant_store is None:
        raise IdisHttpError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="Compliance store unavailable",
        )

    try:
        stored_object = compliant_store.get(
            tenant_ctx=ctx_for_store,
            key=storage_key,
            data_class=DataClass.CLASS_2,
        )
    except ObjectNotFoundError as err:
        raise IdisHttpError(
            status_code=404,
            code="DOCUMENT_CONTENT_NOT_FOUND",
            message="Document content not found",
        ) from err

    content_bytes = stored_object.body
    content_b64 = base64.b64encode(content_bytes).decode("ascii")
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()

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
        content_b64=content_b64,
        content_sha256=content_sha256,
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
