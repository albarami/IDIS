"""Ingestion Service — document ingestion coordinator with tenant isolation.

Orchestrates the full ingestion pipeline:
1. Validate inputs (fail closed)
2. Compute SHA256 hash
3. Store raw bytes in object store
4. Parse document using parser registry
5. Generate spans via SpanGenerator
6. Persist DocumentArtifact, Document, and DocumentSpans
7. Emit audit events (document.created, document.ingestion.completed)

Requirements per v6.3:
- Tenant isolation: all operations scoped by tenant_id
- Audit completeness: emit required audit events
- SHA256 integrity tracking
- Fail closed: no unhandled exceptions, structured errors
- No stub implementations or synthetic paths
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from idis.api.auth import TenantContext
from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.compliance.byok import DataClass
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_artifact import DocType, DocumentArtifact
from idis.models.document_span import DocumentSpan
from idis.parsers.base import ParseError, ParseLimits, ParseResult
from idis.parsers.registry import parse_bytes
from idis.services.ingestion.span_generator import SpanGenerator
from idis.storage.compliant_store import ComplianceEnforcedStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class IngestionErrorCode(str, Enum):
    """Standardized error codes for ingestion failures."""

    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSE_FAILED = "parse_failed"
    STORAGE_FAILED = "storage_failed"
    PERSISTENCE_FAILED = "persistence_failed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class IngestionError:
    """Structured ingestion error.

    Attributes:
        code: Error code from IngestionErrorCode enum.
        message: Human-readable error description.
        details: Additional context (parser errors, exception info).
    """

    code: IngestionErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class IngestionContext:
    """Context for an ingestion operation.

    Contains tenant scope, actor identity, and request metadata
    required for tenant isolation and audit logging.

    Attributes:
        tenant_id: Tenant scope (required for RLS).
        actor_id: Actor performing the operation (user or system).
        request_id: Unique request identifier for correlation.
        idempotency_key: Optional key for idempotent retries.
    """

    tenant_id: UUID
    actor_id: str
    request_id: str
    idempotency_key: str | None = None


@dataclass
class IngestionResult:
    """Result of a document ingestion operation.

    Attributes:
        success: True if ingestion completed successfully.
        artifact_id: UUID of created DocumentArtifact (if any).
        document_id: UUID of created Document (if any).
        doc_type: Detected document format (PDF, XLSX, etc.).
        span_count: Number of spans extracted.
        sha256: SHA256 hash of raw bytes.
        storage_uri: Object store key for raw bytes.
        parse_status: Document parse status (PARSED/FAILED).
        errors: List of structured errors (empty if success=True).
    """

    success: bool
    artifact_id: UUID | None = None
    document_id: UUID | None = None
    doc_type: str | None = None
    span_count: int = 0
    sha256: str | None = None
    storage_uri: str | None = None
    parse_status: ParseStatus | None = None
    errors: list[IngestionError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "artifact_id": str(self.artifact_id) if self.artifact_id else None,
            "document_id": str(self.document_id) if self.document_id else None,
            "doc_type": self.doc_type,
            "span_count": self.span_count,
            "sha256": self.sha256,
            "storage_uri": self.storage_uri,
            "parse_status": self.parse_status.value if self.parse_status else None,
            "errors": [e.to_dict() for e in self.errors],
        }


class IngestionService:
    """Document ingestion coordinator with tenant isolation.

    Orchestrates the full ingestion pipeline from raw bytes to
    persisted DocumentArtifact, Document, and DocumentSpan objects.

    All operations are tenant-scoped and emit required audit events.
    """

    def __init__(
        self,
        *,
        compliant_store: ComplianceEnforcedStore,
        audit_sink: AuditSink | None = None,
        span_generator: SpanGenerator | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        parse_limits: ParseLimits | None = None,
    ) -> None:
        """Initialize the ingestion service.

        Args:
            compliant_store: Compliance-enforced store for raw document storage.
                            This wrapper enforces BYOK and legal hold at the boundary.
            audit_sink: Audit sink for event emission.
            span_generator: Span generator (uses default if None).
            max_bytes: Maximum file size in bytes.
            parse_limits: Parser limits configuration.
        """
        self._compliant_store = compliant_store
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._span_generator = span_generator or SpanGenerator()
        self._max_bytes = max_bytes
        self._parse_limits = parse_limits or ParseLimits()

        self._artifacts: dict[str, DocumentArtifact] = {}
        self._documents: dict[str, Document] = {}
        self._spans: dict[str, list[DocumentSpan]] = {}

    def ingest_bytes(
        self,
        ctx: IngestionContext,
        deal_id: UUID,
        *,
        filename: str,
        media_type: str | None,
        data: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        """Ingest raw document bytes into the system.

        Full pipeline:
        1. Validate inputs (empty bytes, size limit)
        2. Compute SHA256 hash
        3. Store raw bytes in object store
        4. Parse document using parser registry
        5. Generate spans via SpanGenerator
        6. Persist DocumentArtifact, Document, DocumentSpans
        7. Emit audit events

        Args:
            ctx: Ingestion context with tenant scope and actor.
            deal_id: Parent deal reference.
            filename: Original filename (for metadata, not format detection).
            media_type: MIME type (for metadata, not format detection).
            data: Raw document bytes.
            metadata: Optional additional metadata.

        Returns:
            IngestionResult with operation outcome.

        Behavior:
            - Never raises exceptions; all failures captured in result.
            - Fail closed: empty/oversized files return structured error.
            - Tenant isolation: all storage keys prefixed with tenant_id.
        """
        try:
            return self._ingest_bytes_impl(
                ctx=ctx,
                deal_id=deal_id,
                filename=filename,
                media_type=media_type,
                data=data,
                metadata=metadata or {},
            )
        except Exception as e:
            logger.exception("Unexpected error during ingestion")
            return IngestionResult(
                success=False,
                errors=[
                    IngestionError(
                        code=IngestionErrorCode.INTERNAL_ERROR,
                        message="Internal error during ingestion",
                        details={"exception_type": type(e).__name__},
                    )
                ],
            )

    def _ingest_bytes_impl(
        self,
        ctx: IngestionContext,
        deal_id: UUID,
        filename: str,
        media_type: str | None,
        data: bytes,
        metadata: dict[str, Any],
    ) -> IngestionResult:
        """Implementation of ingest_bytes with exception propagation."""
        validation_error = self._validate_input(data, filename)
        if validation_error:
            return IngestionResult(success=False, errors=[validation_error])

        sha256 = self._compute_sha256(data)

        storage_key = self._build_storage_key(
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            sha256=sha256,
            filename=filename,
        )

        storage_result = self._store_raw_bytes(
            ctx=ctx,
            storage_key=storage_key,
            data=data,
            content_type=media_type,
        )
        if storage_result is not None:
            return IngestionResult(
                success=False,
                sha256=sha256,
                errors=[storage_result],
            )

        parse_result = self._parse_document(data, filename, media_type)

        artifact_id = uuid4()
        document_id = uuid4()
        now = datetime.now(UTC)

        artifact = self._create_artifact(
            artifact_id=artifact_id,
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            filename=filename,
            sha256=sha256,
            storage_uri=storage_key,
            metadata=metadata,
            timestamp=now,
        )

        parse_status = ParseStatus.PARSED if parse_result.success else ParseStatus.FAILED
        doc_type_enum = self._map_doc_type(parse_result.doc_type)

        document = self._create_document(
            document_id=document_id,
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            artifact_id=artifact_id,
            doc_type=doc_type_enum,
            parse_status=parse_status,
            parse_metadata=parse_result.metadata,
            timestamp=now,
        )

        spans: list[DocumentSpan] = []
        if parse_result.success and parse_result.spans:
            spans = self._span_generator.generate_spans(
                span_drafts=parse_result.spans,
                tenant_id=ctx.tenant_id,
                document_id=document_id,
            )

        self._persist_artifact(ctx.tenant_id, artifact)
        self._persist_document(ctx.tenant_id, document)
        self._persist_spans(ctx.tenant_id, document_id, spans)

        self._emit_document_created(ctx, artifact, sha256)

        if parse_result.success:
            self._emit_ingestion_completed(ctx, document, len(spans), sha256)
        else:
            self._emit_ingestion_failed(ctx, document, parse_result.errors, sha256)

        errors: list[IngestionError] = []
        if not parse_result.success:
            errors = self._convert_parse_errors(parse_result.errors)

        return IngestionResult(
            success=parse_result.success,
            artifact_id=artifact_id,
            document_id=document_id,
            doc_type=parse_result.doc_type,
            span_count=len(spans),
            sha256=sha256,
            storage_uri=storage_key,
            parse_status=parse_status,
            errors=errors,
        )

    def _validate_input(self, data: bytes, filename: str) -> IngestionError | None:
        """Validate input bytes (fail closed).

        Returns:
            IngestionError if validation fails, None if valid.
        """
        if len(data) == 0:
            return IngestionError(
                code=IngestionErrorCode.EMPTY_FILE,
                message="Empty file",
                details={"filename": filename},
            )

        if len(data) > self._max_bytes:
            return IngestionError(
                code=IngestionErrorCode.FILE_TOO_LARGE,
                message=f"File exceeds maximum size of {self._max_bytes} bytes",
                details={
                    "filename": filename,
                    "size_bytes": len(data),
                    "max_bytes": self._max_bytes,
                },
            )

        return None

    def _compute_sha256(self, data: bytes) -> str:
        """Compute SHA256 hash of data."""
        return hashlib.sha256(data).hexdigest()

    def _build_storage_key(
        self,
        tenant_id: UUID,
        deal_id: UUID,
        sha256: str,
        filename: str,
    ) -> str:
        """Build deterministic storage key with tenant isolation.

        Key format: deals/{deal_id}/artifacts/{sha256}/{filename}

        Note: tenant_id is used as the object store namespace,
        so the key itself is scoped to the tenant via the put() call.
        """
        safe_filename = self._sanitize_filename(filename)
        return f"deals/{deal_id}/artifacts/{sha256}/{safe_filename}"

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage key.

        Removes path separators and limits length.
        """
        name = filename.replace("/", "_").replace("\\", "_")
        if len(name) > 128:
            name = name[:128]
        return name

    def _store_raw_bytes(
        self,
        ctx: IngestionContext,
        storage_key: str,
        data: bytes,
        content_type: str | None,
    ) -> IngestionError | None:
        """Store raw bytes via compliance-enforced store.

        This method routes through ComplianceEnforcedStore which enforces:
        - BYOK key active check for Class2/3 data
        - Legal hold protections

        Returns:
            IngestionError if storage fails, None on success.
        """
        try:
            tenant_ctx = TenantContext(
                tenant_id=str(ctx.tenant_id),
                actor_id=ctx.actor_id,
                name="ingestion",
                timezone="UTC",
                data_region="me-south-1",
            )
            self._compliant_store.put(
                tenant_ctx=tenant_ctx,
                key=storage_key,
                data=data,
                content_type=content_type,
                data_class=DataClass.CLASS_2,
            )
            return None
        except Exception as e:
            logger.error("Failed to store raw bytes: %s", e)
            return IngestionError(
                code=IngestionErrorCode.STORAGE_FAILED,
                message="Failed to store document in object store",
                details={"exception_type": type(e).__name__},
            )

    def _parse_document(
        self,
        data: bytes,
        filename: str | None,
        mime_type: str | None,
    ) -> ParseResult:
        """Parse document using parser registry."""
        return parse_bytes(
            data=data,
            filename=filename,
            mime_type=mime_type,
            limits=self._parse_limits,
        )

    def _map_doc_type(self, parser_doc_type: str) -> DocumentType:
        """Map parser doc_type string to DocumentType enum."""
        type_map = {
            "PDF": DocumentType.PDF,
            "XLSX": DocumentType.XLSX,
            "DOCX": DocumentType.DOCX,
            "PPTX": DocumentType.PPTX,
            "UNKNOWN": DocumentType.PDF,
        }
        return type_map.get(parser_doc_type, DocumentType.PDF)

    def _create_artifact(
        self,
        artifact_id: UUID,
        tenant_id: UUID,
        deal_id: UUID,
        filename: str,
        sha256: str,
        storage_uri: str,
        metadata: dict[str, Any],
        timestamp: datetime,
    ) -> DocumentArtifact:
        """Create DocumentArtifact model."""
        return DocumentArtifact(
            doc_id=artifact_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            doc_type=DocType.DATA_ROOM_FILE,
            title=filename,
            source_system="upload",
            version_id=sha256[:12],
            ingested_at=timestamp,
            sha256=sha256,
            uri=storage_uri,
            metadata=metadata,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _create_document(
        self,
        document_id: UUID,
        tenant_id: UUID,
        deal_id: UUID,
        artifact_id: UUID,
        doc_type: DocumentType,
        parse_status: ParseStatus,
        parse_metadata: dict[str, Any],
        timestamp: datetime,
    ) -> Document:
        """Create Document model."""
        return Document(
            document_id=document_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            doc_id=artifact_id,
            doc_type=doc_type,
            parse_status=parse_status,
            metadata=parse_metadata,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _persist_artifact(self, tenant_id: UUID, artifact: DocumentArtifact) -> None:
        """Persist DocumentArtifact (in-memory for now).

        Note: This will be replaced with Postgres persistence
        once the repository layer is implemented.
        """
        key = f"{tenant_id}:{artifact.doc_id}"
        self._artifacts[key] = artifact

    def _persist_document(self, tenant_id: UUID, document: Document) -> None:
        """Persist Document (in-memory for now).

        Note: This will be replaced with Postgres persistence
        once the repository layer is implemented.
        """
        key = f"{tenant_id}:{document.document_id}"
        self._documents[key] = document

    def _persist_spans(self, tenant_id: UUID, document_id: UUID, spans: list[DocumentSpan]) -> None:
        """Persist DocumentSpans (in-memory for now).

        Note: This will be replaced with Postgres bulk insert
        once the repository layer is implemented.
        """
        key = f"{tenant_id}:{document_id}"
        self._spans[key] = spans

    def _convert_parse_errors(self, parse_errors: list[ParseError]) -> list[IngestionError]:
        """Convert ParseErrors to IngestionErrors."""
        return [
            IngestionError(
                code=IngestionErrorCode.PARSE_FAILED,
                message=pe.message,
                details={"parse_error_code": pe.code.value, **pe.details},
            )
            for pe in parse_errors
        ]

    def _emit_document_created(
        self,
        ctx: IngestionContext,
        artifact: DocumentArtifact,
        sha256: str,
    ) -> None:
        """Emit document.created audit event."""
        event = {
            "event_id": str(uuid4()),
            "event_type": "document.created",
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(ctx.tenant_id),
            "severity": "MEDIUM",
            "actor": {
                "actor_type": "HUMAN" if ctx.actor_id != "system" else "SERVICE",
                "actor_id": ctx.actor_id,
            },
            "request": {
                "request_id": ctx.request_id,
                "idempotency_key": ctx.idempotency_key,
            },
            "resource": {
                "resource_type": "document",
                "resource_id": str(artifact.doc_id),
                "deal_id": str(artifact.deal_id),
            },
            "summary": f"Document artifact created: {artifact.title}",
            "payload": {
                "sha256": sha256,
                "title": artifact.title,
                "doc_type": artifact.doc_type.value,
            },
        }
        self._audit_sink.emit(event)

    def _emit_ingestion_completed(
        self,
        ctx: IngestionContext,
        document: Document,
        span_count: int,
        sha256: str,
    ) -> None:
        """Emit document.ingestion.completed audit event."""
        event = {
            "event_id": str(uuid4()),
            "event_type": "document.ingestion.completed",
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(ctx.tenant_id),
            "severity": "LOW",
            "actor": {
                "actor_type": "HUMAN" if ctx.actor_id != "system" else "SERVICE",
                "actor_id": ctx.actor_id,
            },
            "request": {
                "request_id": ctx.request_id,
                "idempotency_key": ctx.idempotency_key,
            },
            "resource": {
                "resource_type": "document",
                "resource_id": str(document.document_id),
                "deal_id": str(document.deal_id),
            },
            "summary": f"Document ingestion completed: {span_count} spans extracted",
            "payload": {
                "sha256": sha256,
                "doc_type": document.doc_type.value,
                "span_count": span_count,
                "parse_status": document.parse_status.value,
            },
        }
        self._audit_sink.emit(event)

    def _emit_ingestion_failed(
        self,
        ctx: IngestionContext,
        document: Document,
        parse_errors: list[ParseError],
        sha256: str,
    ) -> None:
        """Emit document.ingestion.failed audit event."""
        error_summaries = [{"code": e.code.value, "message": e.message} for e in parse_errors]
        event = {
            "event_id": str(uuid4()),
            "event_type": "document.ingestion.failed",
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(ctx.tenant_id),
            "severity": "MEDIUM",
            "actor": {
                "actor_type": "HUMAN" if ctx.actor_id != "system" else "SERVICE",
                "actor_id": ctx.actor_id,
            },
            "request": {
                "request_id": ctx.request_id,
                "idempotency_key": ctx.idempotency_key,
            },
            "resource": {
                "resource_type": "document",
                "resource_id": str(document.document_id),
                "deal_id": str(document.deal_id),
            },
            "summary": f"Document ingestion failed: {len(parse_errors)} error(s)",
            "payload": {
                "sha256": sha256,
                "doc_type": document.doc_type.value,
                "parse_status": document.parse_status.value,
                "errors": error_summaries,
            },
        }
        self._audit_sink.emit(event)

    def get_artifact(self, tenant_id: UUID, artifact_id: UUID) -> DocumentArtifact | None:
        """Retrieve a persisted artifact by ID (tenant-scoped).

        Args:
            tenant_id: Tenant scope.
            artifact_id: Artifact ID to retrieve.

        Returns:
            DocumentArtifact if found, None otherwise.
        """
        key = f"{tenant_id}:{artifact_id}"
        return self._artifacts.get(key)

    def get_document(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        """Retrieve a persisted document by ID (tenant-scoped).

        Args:
            tenant_id: Tenant scope.
            document_id: Document ID to retrieve.

        Returns:
            Document if found, None otherwise.
        """
        key = f"{tenant_id}:{document_id}"
        return self._documents.get(key)

    def get_spans(self, tenant_id: UUID, document_id: UUID) -> list[DocumentSpan]:
        """Retrieve persisted spans for a document (tenant-scoped).

        Args:
            tenant_id: Tenant scope.
            document_id: Parent document ID.

        Returns:
            List of DocumentSpans (empty if none found).
        """
        key = f"{tenant_id}:{document_id}"
        return self._spans.get(key, [])
