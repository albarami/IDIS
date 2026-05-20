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
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from idis.api.auth import TenantContext
from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.compliance.byok import DataClass
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_artifact import DocType, DocumentArtifact
from idis.models.document_span import DocumentSpan
from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult
from idis.parsers.ocr import OcrConfig
from idis.parsers.registry import parse_bytes
from idis.services.ingestion.span_generator import SpanGenerator
from idis.storage.compliant_store import ComplianceEnforcedStore

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class IngestionErrorCode(StrEnum):
    """Standardized error codes for ingestion failures."""

    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    UNTRUSTED_VALIDATED_SHA256 = "untrusted_validated_sha256"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSE_FAILED = "parse_failed"
    STORAGE_FAILED = "storage_failed"
    PERSISTENCE_FAILED = "persistence_failed"
    INTERNAL_ERROR = "internal_error"


class UploadIngestionPhase(StrEnum):
    """Aggregate timing phases inside the public upload and ingestion path."""

    ROUTE_BODY_READ_HASH_VALIDATION = "route_body_read/hash_validation"
    OBJECT_STORE_WRITE = "object_store_write"
    PARSE = "parse"
    SPAN_GENERATION = "span_generation"
    PERSISTENCE = "persistence"
    AUDIT = "audit"


UPLOAD_INGESTION_PHASES = tuple(phase.value for phase in UploadIngestionPhase)
PARSER_DIAGNOSTIC_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx", ".unknown", ".other"})
PARSER_DIAGNOSTIC_OUTCOMES = frozenset({"parsed", "failed"})
PDF_DIAGNOSTIC_OUTCOME_REASONS = frozenset(
    {
        "parsed_text",
        "parsed_empty_password_encrypted",
        "parsed_ocr",
        "failed_encrypted",
        "failed_no_text",
        "failed_ocr_no_text",
        "failed_corrupted",
        "failed_ocr_unavailable",
        "failed_ocr_timeout",
        "failed_ocr_failed",
        "failed_max_size",
        "failed_max_pages",
        "failed_other",
    }
)


class UploadIngestionPhaseRecorder:
    """Collect aggregate-only elapsed timing for upload internals."""

    def __init__(self) -> None:
        self._phase_elapsed_seconds: dict[str, list[float]] = {
            phase: [] for phase in UPLOAD_INGESTION_PHASES
        }
        self._parser_elapsed_by_extension: dict[str, list[float]] = {}
        self._parser_elapsed_by_outcome: dict[str, list[float]] = {}
        self._parser_counts_by_extension: Counter[str] = Counter()
        self._parser_counts_by_outcome: Counter[str] = Counter()
        self._pdf_elapsed_by_outcome_reason: dict[str, list[float]] = {}
        self._pdf_counts_by_outcome_reason: Counter[str] = Counter()
        self._lock = Lock()

    def record_phase(self, phase: UploadIngestionPhase | str, elapsed_seconds: float) -> None:
        """Record one aggregate elapsed time for an internal upload phase."""
        phase_value = phase.value if isinstance(phase, UploadIngestionPhase) else phase
        if phase_value not in UPLOAD_INGESTION_PHASES:
            return
        with self._lock:
            self._phase_elapsed_seconds[phase_value].append(max(0.0, elapsed_seconds))

    def record_parser_result(
        self,
        *,
        extension: str,
        outcome: str,
        elapsed_seconds: float,
        pdf_outcome_reason: str | None = None,
    ) -> None:
        """Record one aggregate parser diagnostic sample."""
        extension_value = _safe_parser_diagnostic_extension(extension)
        outcome_value = outcome if outcome in PARSER_DIAGNOSTIC_OUTCOMES else "failed"
        elapsed_value = max(0.0, elapsed_seconds)
        with self._lock:
            self._parser_counts_by_extension[extension_value] += 1
            self._parser_counts_by_outcome[outcome_value] += 1
            self._parser_elapsed_by_extension.setdefault(extension_value, []).append(elapsed_value)
            self._parser_elapsed_by_outcome.setdefault(outcome_value, []).append(elapsed_value)
            safe_pdf_reason = _safe_pdf_diagnostic_outcome_reason(pdf_outcome_reason)
            if safe_pdf_reason is not None:
                self._pdf_counts_by_outcome_reason[safe_pdf_reason] += 1
                self._pdf_elapsed_by_outcome_reason.setdefault(safe_pdf_reason, []).append(
                    elapsed_value
                )

    def to_summary(self) -> dict[str, Any]:
        """Return aggregate-only phase timing buckets."""
        with self._lock:
            phase_elapsed = {
                phase: list(values) for phase, values in self._phase_elapsed_seconds.items()
            }
            parser_elapsed_by_extension = {
                extension: list(values)
                for extension, values in self._parser_elapsed_by_extension.items()
            }
            parser_elapsed_by_outcome = {
                outcome: list(values) for outcome, values in self._parser_elapsed_by_outcome.items()
            }
            parser_counts_by_extension = Counter(self._parser_counts_by_extension)
            parser_counts_by_outcome = Counter(self._parser_counts_by_outcome)
            pdf_elapsed_by_outcome_reason = {
                reason: list(values)
                for reason, values in self._pdf_elapsed_by_outcome_reason.items()
            }
            pdf_counts_by_outcome_reason = Counter(self._pdf_counts_by_outcome_reason)
        observed = {phase: values for phase, values in phase_elapsed.items() if values}
        summary = {
            "enabled": True,
            "phase_counts_by_elapsed_bucket": {
                phase: _elapsed_bucket_counts(values) for phase, values in observed.items()
            },
            "phase_total_elapsed_bucket": {
                phase: _elapsed_seconds_bucket(sum(values)) for phase, values in observed.items()
            },
            "phase_max_elapsed_bucket": {
                phase: _elapsed_seconds_bucket(max(values)) for phase, values in observed.items()
            },
            "observable_slowest_phase": _observable_slowest_phase(observed),
        }
        parser_diagnostics = _parser_diagnostics_summary(
            counts_by_extension=parser_counts_by_extension,
            counts_by_outcome=parser_counts_by_outcome,
            elapsed_by_extension=parser_elapsed_by_extension,
            elapsed_by_outcome=parser_elapsed_by_outcome,
            pdf_counts_by_outcome_reason=pdf_counts_by_outcome_reason,
            pdf_elapsed_by_outcome_reason=pdf_elapsed_by_outcome_reason,
        )
        if parser_diagnostics:
            summary["parser_diagnostics"] = parser_diagnostics
        return summary


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


_ROUTE_VALIDATED_SHA256_TOKEN = object()


@dataclass(frozen=True, init=False)
class RouteValidatedSha256:
    """SHA256 digest proven against bytes at the public upload route boundary."""

    value: str

    def __init__(self, *, value: str, _token: object) -> None:
        """Create a route-validated digest; callers must use ``from_bytes``."""
        if _token is not _ROUTE_VALIDATED_SHA256_TOKEN:
            raise ValueError("RouteValidatedSha256 must be created by from_bytes")
        object.__setattr__(self, "value", value)

    @classmethod
    def from_bytes(
        cls,
        *,
        data: bytes,
        expected_sha256: str | None = None,
    ) -> RouteValidatedSha256:
        """Validate optional caller SHA against bytes and return the actual digest."""
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256.lower():
            raise ValueError("SHA256_MISMATCH")
        return cls(value=actual_sha256, _token=_ROUTE_VALIDATED_SHA256_TOKEN)


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


def _record_parser_diagnostics(
    phase_recorder: object | None,
    *,
    filename: str | None,
    parse_result: ParseResult,
    elapsed_seconds: float,
) -> None:
    if phase_recorder is None:
        return
    try:
        record_parser_result = getattr(phase_recorder, "record_parser_result", None)
    except Exception as error:
        logger.warning(
            "Private parser diagnostics recorder lookup failed: exception_type=%s",
            type(error).__name__,
        )
        return
    if not callable(record_parser_result):
        return
    try:
        record_parser_result(
            extension=_parser_diagnostic_extension(filename),
            outcome=_parser_diagnostic_outcome(parse_result),
            elapsed_seconds=elapsed_seconds,
            pdf_outcome_reason=_pdf_diagnostic_outcome_reason(parse_result),
        )
    except Exception as error:
        logger.warning(
            "Private parser diagnostics recorder failed: exception_type=%s",
            type(error).__name__,
        )


def _parser_diagnostic_extension(filename: str | None) -> str:
    if not filename:
        return ".unknown"
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if "." not in basename:
        return ".unknown"
    extension = f".{basename.rsplit('.', 1)[-1].lower()}"
    return _safe_parser_diagnostic_extension(extension)


def _safe_parser_diagnostic_extension(extension: str) -> str:
    extension_value = extension.strip().lower()
    if not extension_value.startswith("."):
        extension_value = f".{extension_value}"
    if extension_value in PARSER_DIAGNOSTIC_EXTENSIONS:
        return extension_value
    return ".other"


def _parser_diagnostic_outcome(parse_result: ParseResult) -> str:
    return "parsed" if parse_result.success else "failed"


def _pdf_diagnostic_outcome_reason(parse_result: ParseResult) -> str | None:
    if parse_result.doc_type != "PDF":
        return None
    if parse_result.success:
        reason = parse_result.metadata.get("pdf_diagnostic_reason")
        if isinstance(reason, str):
            return _safe_pdf_diagnostic_outcome_reason(reason)
        if parse_result.metadata.get("ocr_performed") is True:
            return "parsed_ocr"
        return "parsed_text"
    if not parse_result.errors:
        return "failed_other"
    return _pdf_failure_reason(parse_result.errors[0].code)


def _pdf_failure_reason(code: ParseErrorCode) -> str:
    if code == ParseErrorCode.ENCRYPTED_PDF:
        return "failed_encrypted"
    if code == ParseErrorCode.NO_TEXT_EXTRACTED:
        return "failed_no_text"
    if code == ParseErrorCode.OCR_NO_TEXT_EXTRACTED:
        return "failed_ocr_no_text"
    if code == ParseErrorCode.CORRUPTED_FILE:
        return "failed_corrupted"
    if code == ParseErrorCode.OCR_UNAVAILABLE:
        return "failed_ocr_unavailable"
    if code == ParseErrorCode.OCR_TIMEOUT:
        return "failed_ocr_timeout"
    if code == ParseErrorCode.OCR_FAILED:
        return "failed_ocr_failed"
    if code == ParseErrorCode.MAX_SIZE_EXCEEDED:
        return "failed_max_size"
    if code == ParseErrorCode.MAX_PAGES_EXCEEDED:
        return "failed_max_pages"
    return "failed_other"


def _safe_pdf_diagnostic_outcome_reason(reason: str | None) -> str | None:
    if not isinstance(reason, str):
        return None
    reason_value = reason.strip().lower()
    if reason_value in PDF_DIAGNOSTIC_OUTCOME_REASONS:
        return reason_value
    return "failed_other"


def _parser_diagnostics_summary(
    *,
    counts_by_extension: Counter[str],
    counts_by_outcome: Counter[str],
    elapsed_by_extension: dict[str, list[float]],
    elapsed_by_outcome: dict[str, list[float]],
    pdf_counts_by_outcome_reason: Counter[str],
    pdf_elapsed_by_outcome_reason: dict[str, list[float]],
) -> dict[str, Any]:
    if not counts_by_extension and not counts_by_outcome:
        return {}
    observed_extensions = {
        extension: values for extension, values in elapsed_by_extension.items() if values
    }
    summary: dict[str, Any] = {
        "counts_by_extension": dict(sorted(counts_by_extension.items())),
        "counts_by_outcome": dict(sorted(counts_by_outcome.items())),
        "parse_elapsed_by_extension": {
            extension: _elapsed_bucket_counts(values)
            for extension, values in sorted(observed_extensions.items())
        },
        "parse_elapsed_by_outcome": {
            outcome: _elapsed_bucket_counts(values)
            for outcome, values in sorted(elapsed_by_outcome.items())
            if values
        },
        "parse_total_elapsed_bucket_by_extension": {
            extension: _elapsed_seconds_bucket(sum(values))
            for extension, values in sorted(observed_extensions.items())
        },
        "parse_max_elapsed_bucket_by_extension": {
            extension: _elapsed_seconds_bucket(max(values))
            for extension, values in sorted(observed_extensions.items())
        },
        "observable_slowest_extension": _observable_slowest_phase(observed_extensions),
    }
    pdf_diagnostics = _pdf_diagnostics_summary(
        counts_by_outcome_reason=pdf_counts_by_outcome_reason,
        elapsed_by_outcome_reason=pdf_elapsed_by_outcome_reason,
    )
    if pdf_diagnostics:
        summary["pdf_diagnostics"] = pdf_diagnostics
    return summary


def _pdf_diagnostics_summary(
    *,
    counts_by_outcome_reason: Counter[str],
    elapsed_by_outcome_reason: dict[str, list[float]],
) -> dict[str, Any]:
    if not counts_by_outcome_reason:
        return {}
    observed = {reason: values for reason, values in elapsed_by_outcome_reason.items() if values}
    return {
        "counts_by_outcome_reason": dict(sorted(counts_by_outcome_reason.items())),
        "parse_elapsed_by_outcome_reason": {
            reason: _elapsed_bucket_counts(values) for reason, values in sorted(observed.items())
        },
        "parse_total_elapsed_bucket_by_outcome_reason": {
            reason: _elapsed_seconds_bucket(sum(values))
            for reason, values in sorted(observed.items())
        },
        "parse_max_elapsed_bucket_by_outcome_reason": {
            reason: _elapsed_seconds_bucket(max(values))
            for reason, values in sorted(observed.items())
        },
        "observable_slowest_outcome_reason": _observable_slowest_phase(observed),
    }


def _elapsed_seconds_bucket(elapsed_seconds: float | None) -> str | None:
    if elapsed_seconds is None:
        return None
    if elapsed_seconds < 1:
        return "under_1s"
    if elapsed_seconds < 5:
        return "1_to_5s"
    if elapsed_seconds < 30:
        return "5_to_30s"
    if elapsed_seconds < 60:
        return "30_to_60s"
    if elapsed_seconds < 300:
        return "60_to_300s"
    return "over_300s"


def _elapsed_bucket_counts(elapsed_seconds_values: list[float]) -> dict[str, int]:
    buckets = [
        bucket
        for bucket in (_elapsed_seconds_bucket(value) for value in elapsed_seconds_values)
        if bucket is not None
    ]
    return dict(sorted(Counter(buckets).items()))


def _observable_slowest_phase(phase_elapsed: dict[str, list[float]]) -> str | None:
    phase_totals = {phase: sum(values) for phase, values in phase_elapsed.items() if values}
    if not phase_totals:
        return None
    return max(sorted(phase_totals), key=lambda phase: phase_totals[phase])


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
        db_conn: Connection | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        parse_limits: ParseLimits | None = None,
        ocr_config: OcrConfig | None = None,
    ) -> None:
        """Initialize the ingestion service.

        Args:
            compliant_store: Compliance-enforced store for raw document storage.
                            This wrapper enforces BYOK and legal hold at the boundary.
            audit_sink: Audit sink for event emission.
            span_generator: Span generator (uses default if None).
            db_conn: Optional Postgres connection for durable corpus persistence.
            max_bytes: Maximum file size in bytes.
            parse_limits: Parser limits configuration.
            ocr_config: Explicit OCR execution config. Disabled by default.
        """
        self._compliant_store = compliant_store
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._span_generator = span_generator or SpanGenerator()
        self._db_conn = db_conn
        self._max_bytes = max_bytes
        self._parse_limits = parse_limits or ParseLimits()
        self._ocr_config = ocr_config

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
        validated_sha256: RouteValidatedSha256 | None = None,
        phase_recorder: object | None = None,
        db_conn: Connection | None = None,
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
            validated_sha256: Optional digest object already validated against
                ``data`` by a trusted API boundary. When provided, ingestion reuses
                it instead of hashing the same bytes again.
            phase_recorder: Optional private aggregate phase timing recorder.
            db_conn: Optional request-scoped Postgres connection for durable corpus writes.

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
                validated_sha256=validated_sha256,
                phase_recorder=phase_recorder,
                db_conn=db_conn,
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
        validated_sha256: RouteValidatedSha256 | None,
        phase_recorder: object | None,
        db_conn: Connection | None,
    ) -> IngestionResult:
        """Implementation of ingest_bytes with exception propagation."""
        trusted_sha_error = self._reject_untrusted_validated_sha256(validated_sha256)
        if trusted_sha_error is not None:
            return IngestionResult(success=False, errors=[trusted_sha_error])

        validation_error = self._validate_input(data, filename)
        if validation_error:
            return IngestionResult(success=False, errors=[validation_error])

        sha256 = self._validated_or_computed_sha256(
            data=data,
            validated_sha256=validated_sha256,
        )

        storage_key = self._build_storage_key(
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            sha256=sha256,
            filename=filename,
        )

        phase_started_at = time.monotonic()
        try:
            storage_result = self._store_raw_bytes(
                ctx=ctx,
                storage_key=storage_key,
                data=data,
                content_type=media_type,
            )
        finally:
            _record_upload_phase(
                phase_recorder,
                UploadIngestionPhase.OBJECT_STORE_WRITE,
                phase_started_at,
            )
        if storage_result is not None:
            return IngestionResult(
                success=False,
                sha256=sha256,
                errors=[storage_result],
            )

        phase_started_at = time.monotonic()
        try:
            parse_result = self._parse_document(data, filename, media_type)
        finally:
            _record_upload_phase(phase_recorder, UploadIngestionPhase.PARSE, phase_started_at)
        _record_parser_diagnostics(
            phase_recorder,
            filename=filename,
            parse_result=parse_result,
            elapsed_seconds=time.monotonic() - phase_started_at,
        )

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
            parse_metadata=self._parse_metadata_for_persistence(parse_result),
            timestamp=now,
        )

        spans: list[DocumentSpan] = []
        phase_started_at = time.monotonic()
        try:
            if parse_result.success and parse_result.spans:
                spans = self._span_generator.generate_spans(
                    span_drafts=parse_result.spans,
                    tenant_id=ctx.tenant_id,
                    document_id=document_id,
                )
        finally:
            _record_upload_phase(
                phase_recorder,
                UploadIngestionPhase.SPAN_GENERATION,
                phase_started_at,
            )

        phase_started_at = time.monotonic()
        try:
            self._persist_artifact(ctx.tenant_id, artifact, db_conn=db_conn)
            self._persist_document(ctx.tenant_id, document, db_conn=db_conn)
            self._persist_spans(ctx.tenant_id, deal_id, document_id, spans, db_conn=db_conn)
        finally:
            _record_upload_phase(
                phase_recorder,
                UploadIngestionPhase.PERSISTENCE,
                phase_started_at,
            )

        phase_started_at = time.monotonic()
        try:
            self._emit_document_created(ctx, artifact, sha256)

            if parse_result.success:
                self._emit_ingestion_completed(ctx, document, len(spans), sha256)
            else:
                self._emit_ingestion_failed(ctx, document, parse_result.errors, sha256)
        finally:
            _record_upload_phase(phase_recorder, UploadIngestionPhase.AUDIT, phase_started_at)

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

    def _validated_or_computed_sha256(
        self,
        *,
        data: bytes,
        validated_sha256: RouteValidatedSha256 | None,
    ) -> str:
        """Return a trusted boundary-validated SHA256 or compute one locally."""
        if validated_sha256 is None:
            return self._compute_sha256(data)
        return validated_sha256.value

    def _reject_untrusted_validated_sha256(
        self,
        validated_sha256: object,
    ) -> IngestionError | None:
        """Fail closed when a caller passes a digest without route validation proof."""
        if validated_sha256 is None or isinstance(validated_sha256, RouteValidatedSha256):
            return None
        return IngestionError(
            code=IngestionErrorCode.UNTRUSTED_VALIDATED_SHA256,
            message="Validated SHA256 must be produced by the public upload route boundary",
        )

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

        Raises:
            IdisHttpError: Re-raised for compliance denials (403 BYOK/hold errors).
                          These must NOT be swallowed into run failures.
        """
        from idis.api.errors import IdisHttpError

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
        except IdisHttpError as e:
            if e.status_code == 403 and e.code in _COMPLIANCE_DENIAL_CODES:
                logger.warning("Compliance denial during storage (re-raising): code=%s", e.code)
                raise
            logger.error("IdisHttpError during storage: %s", e)
            return IngestionError(
                code=IngestionErrorCode.STORAGE_FAILED,
                message="Failed to store document in object store",
                details={"exception_type": type(e).__name__},
            )
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
            ocr_config=self._ocr_config,
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

    def _parse_metadata_for_persistence(self, parse_result: ParseResult) -> dict[str, Any]:
        """Return safe parser metadata needed for persisted preflight triage."""
        from idis.services.documents.parser_capabilities import triage_document

        capability = triage_document(parse_result=parse_result)
        return {
            **parse_result.metadata,
            "parse_error_codes": [error.code.value for error in parse_result.errors],
            "parse_warning_codes": [str(warning) for warning in parse_result.warnings],
            "detected_format": parse_result.doc_type,
            "parser_doc_type": parse_result.doc_type,
            "parser_support_status": capability.support_status.value,
            "parser_triage_status": capability.triage_status.value,
            "parser_reason_codes": capability.reason_codes,
            "parser_requires_ocr": capability.requires_ocr,
            "parser_requires_conversion": capability.requires_conversion,
        }

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
        doc_type = self._artifact_doc_type(metadata)
        source_system = metadata.get("source_system")
        return DocumentArtifact(
            doc_id=artifact_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            doc_type=doc_type,
            title=filename,
            source_system=source_system if isinstance(source_system, str) else "upload",
            version_id=sha256[:12],
            ingested_at=timestamp,
            sha256=sha256,
            uri=storage_uri,
            metadata=metadata,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _artifact_doc_type(self, metadata: dict[str, Any]) -> DocType:
        """Resolve public artifact classification from safe caller metadata."""
        raw_doc_type = metadata.get("doc_type")
        if isinstance(raw_doc_type, str):
            try:
                return DocType(raw_doc_type)
            except ValueError:
                return DocType.DATA_ROOM_FILE
        return DocType.DATA_ROOM_FILE

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

    def _persist_artifact(
        self,
        tenant_id: UUID,
        artifact: DocumentArtifact,
        *,
        db_conn: Connection | None,
    ) -> None:
        """Persist DocumentArtifact to memory and optional Postgres corpus."""
        key = f"{tenant_id}:{artifact.doc_id}"
        self._artifacts[key] = artifact
        repo = self._documents_repo(tenant_id, db_conn=db_conn)
        if repo is not None:
            repo.create_artifact(
                doc_id=str(artifact.doc_id),
                deal_id=str(artifact.deal_id),
                doc_type=artifact.doc_type.value,
                title=artifact.title,
                source_system=artifact.source_system,
                version_id=artifact.version_id,
                ingested_at=artifact.ingested_at,
                sha256=artifact.sha256,
                uri=artifact.uri,
                metadata=artifact.metadata,
            )

    def _persist_document(
        self,
        tenant_id: UUID,
        document: Document,
        *,
        db_conn: Connection | None,
    ) -> None:
        """Persist Document to memory and optional Postgres corpus."""
        key = f"{tenant_id}:{document.document_id}"
        self._documents[key] = document
        repo = self._documents_repo(tenant_id, db_conn=db_conn)
        if repo is not None:
            repo.create_document(
                document_id=str(document.document_id),
                deal_id=str(document.deal_id),
                doc_id=str(document.doc_id),
                doc_type=document.doc_type.value,
                parse_status=document.parse_status.value,
                metadata=document.metadata,
            )

    def _persist_spans(
        self,
        tenant_id: UUID,
        deal_id: UUID,
        document_id: UUID,
        spans: list[DocumentSpan],
        *,
        db_conn: Connection | None,
    ) -> None:
        """Persist DocumentSpans to memory and optional Postgres corpus."""
        key = f"{tenant_id}:{document_id}"
        scoped_spans = [span.model_copy(update={"deal_id": deal_id}) for span in spans]
        self._spans[key] = scoped_spans
        repo = self._documents_repo(tenant_id, db_conn=db_conn)
        if repo is not None:
            for span in scoped_spans:
                repo.create_document_span(
                    span_id=str(span.span_id),
                    deal_id=str(deal_id),
                    document_id=str(span.document_id),
                    span_type=span.span_type.value,
                    locator=span.locator,
                    text_excerpt=span.text_excerpt,
                    content_hash=span.content_hash,
                )

    def _documents_repo(self, tenant_id: UUID, *, db_conn: Connection | None) -> Any | None:
        """Return tenant-scoped Postgres document repository when configured."""
        effective_conn = db_conn or self._db_conn
        if effective_conn is None:
            return None
        from idis.persistence.repositories.documents import PostgresDocumentsRepository

        return PostgresDocumentsRepository(effective_conn, str(tenant_id))

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
            "summary": "Document ingestion completed",
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
