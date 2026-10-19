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
import importlib
import importlib.metadata
import logging
import os
import platform
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.canonical import build_canonical_audit_event
from idis.audit.sink import AuditSink, AuditSinkError, InMemoryAuditSink
from idis.compliance.byok import DataClass
from idis.models.document import Document, DocumentType, ParseStatus
from idis.models.document_artifact import DocType, DocumentArtifact
from idis.models.document_span import DocumentSpan
from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult
from idis.parsers.media import FASTER_WHISPER_ADAPTER_NAME, FasterWhisperMediaAdapter, MediaConfig
from idis.parsers.ocr import OcrConfig
from idis.parsers.pdf import (
    PDF_ENCRYPTION_CONFIGURED_CREDENTIAL,
    PDF_ENCRYPTION_CREDENTIAL_INVALID,
    PDF_ENCRYPTION_CREDENTIAL_REQUIRED,
    PDF_ENCRYPTION_DISPOSITION_KEY,
    PDF_ENCRYPTION_EMPTY_CREDENTIAL,
    PDF_ENCRYPTION_NOT_MARKED,
)
from idis.parsers.registry import parse_bytes
from idis.parsers.watermark_page_classifier import WatermarkPageRuntimeConfig
from idis.runtime.governance_envelopes import canonicalize_json
from idis.services.ingestion.image_only_page import (
    ImageOnlyPageContractError,
    project_safe_image_only_page_metadata,
)
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED,
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION,
    WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE,
    WATERMARK_PAGE_SANITY_CAP_EXCEEDED,
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    build_document_no_substantive_content_receipt,
    validate_document_no_substantive_content_receipt_v2,
)
from idis.services.ingestion.password_manifest import DocumentPasswordManifest
from idis.services.ingestion.span_generator import SpanGenerator
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.models import StoredObjectMetadata

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

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

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
STREAMING_CONTRACT_VERSION = "bounded-streaming-v1"
_STREAMING_PROOF_KEYS = frozenset(
    {
        "streaming_contract_version",
        "streaming_size_verified",
        "streaming_sha256_verified",
    }
)


@dataclass(frozen=True, slots=True)
class _VerifiedStagedFile:
    """One bounded digest plus a mutation-sensitive file identity snapshot."""

    data: bytes | None
    stat_signature: tuple[int, int, int, int, int]


def _without_streaming_proof(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove service-owned proof keys from untrusted caller metadata."""
    return {key: value for key, value in metadata.items() if key not in _STREAMING_PROOF_KEYS}


def _with_streaming_proof(metadata: dict[str, Any]) -> dict[str, Any]:
    """Stamp proof only after bounded object-store size/hash validation succeeds."""
    stamped = _without_streaming_proof(metadata)
    stamped.update(
        {
            "streaming_contract_version": STREAMING_CONTRACT_VERSION,
            "streaming_size_verified": True,
            "streaming_sha256_verified": True,
        }
    )
    return stamped


_NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_ID = "no-substantive-content-receipt"
_REGISTERED_NO_SUBSTANTIVE_CONTENT_RECEIPT_VERSION = "document-no-substantive-content-receipt-v1"


class ReceiptSchemaRefError(ValueError):
    """Applicable schema refs were not a list of dict refs."""

    def __init__(self) -> None:
        super().__init__("RECEIPT_SCHEMA_REF_INVALID")


def _loaded_distribution_version(module_name: str, distribution_name: str) -> str | None:
    """Return a distribution version only when the executing file belongs to it."""
    try:
        module = importlib.import_module(module_name)
        distribution = importlib.metadata.distribution(distribution_name)
    except (ImportError, OSError, importlib.metadata.PackageNotFoundError):
        return None
    if not module.__file__ or not distribution.version or not distribution.files:
        return None
    executing = Path(module.__file__).resolve()
    if not any(
        Path(str(distribution.locate_file(entry))).resolve() == executing
        for entry in distribution.files
    ):
        return None
    return distribution.version


def _receipt_payloads_match(existing_payload: object, receipt: Mapping[str, object]) -> bool:
    """Return whether two receipt payloads are the same canonical JSON value."""
    from idis.persistence.repositories.task7_source_freeze import (
        Task7SourceFreezeError,
        _canonical_json_bytes,
    )

    try:
        return _canonical_json_bytes(existing_payload) == _canonical_json_bytes(receipt)
    except Task7SourceFreezeError:
        raise ReceiptSchemaRefError() from None


def _attested_pre_ocr_watermark_receipt(receipt: Mapping[str, object] | None) -> bool:
    """Return whether every classified page is an attested pre-OCR exclusion."""
    if receipt is None:
        return False
    rows = receipt.get("watermark_page_classifications")
    if not isinstance(rows, list) or not rows:
        return False
    return all(
        isinstance(row, Mapping)
        and row.get("batch_membership") == "ATTESTED"
        and row.get("classification_phase") == "pre_ocr"
        and row.get("full_resolution_ocr_attempted") is False
        for row in rows
    )


def _schema_ref_sort_key(item: Mapping[str, object]) -> tuple[str, str, str, bytes]:
    """Sort with the freeze key: id, version, sha256, canonical payload."""
    from idis.persistence.repositories.task7_source_freeze import _canonical_json_bytes

    schema_id = item.get("schema_id")
    schema_version = item.get("schema_version")
    schema_sha256 = item.get("schema_sha256")
    if (
        not isinstance(schema_id, str)
        or not isinstance(schema_version, str)
        or not isinstance(schema_sha256, str)
        or "validated_payload" not in item
    ):
        raise ReceiptSchemaRefError()
    return (
        schema_id,
        schema_version,
        schema_sha256,
        _canonical_json_bytes(item["validated_payload"]),
    )


def _receipt_schema_refs(
    existing: object, receipt: Mapping[str, object]
) -> list[dict[str, object]]:
    """Attach the registered receipt ref without dropping other document metadata."""
    from idis.persistence.repositories.task7_source_freeze import _SOURCE_SCHEMA_AUTHORITIES

    if not isinstance(existing, list):
        raise ReceiptSchemaRefError()
    schema_sha256 = _SOURCE_SCHEMA_AUTHORITIES[
        (
            _NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_ID,
            _REGISTERED_NO_SUBSTANTIVE_CONTENT_RECEIPT_VERSION,
        )
    ]
    ref: dict[str, object] = {
        "schema_id": _NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_ID,
        "schema_version": _REGISTERED_NO_SUBSTANTIVE_CONTENT_RECEIPT_VERSION,
        "schema_sha256": schema_sha256,
        "validated_payload": receipt,
    }
    kept: list[dict[str, object]] = []
    for item in existing:
        if not isinstance(item, dict):
            raise ReceiptSchemaRefError()
        if item.get("schema_id") == _NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_ID:
            if "validated_payload" not in item or not _receipt_payloads_match(
                item["validated_payload"], receipt
            ):
                raise ReceiptSchemaRefError()
            continue
        copied: dict[str, object] = {}
        for key, value in item.items():
            if not isinstance(key, str):
                raise ReceiptSchemaRefError()
            copied[key] = value
        kept.append(copied)
    return sorted([*kept, ref], key=_schema_ref_sort_key)


class IngestionErrorCode(StrEnum):
    """Standardized error codes for ingestion failures."""

    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    UNTRUSTED_VALIDATED_SHA256 = "untrusted_validated_sha256"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSE_FAILED = "parse_failed"
    STORAGE_FAILED = "storage_failed"
    PERSISTENCE_FAILED = "persistence_failed"
    NO_SUBSTANTIVE_CONTENT_ATTESTATION_FAILED = "document_no_substantive_content_attestation_failed"
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
        "parsed_password_encrypted",
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
PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY = "pdf_subphase_elapsed_seconds"
PDF_PARSE_SUBPHASES = frozenset(
    {
        "reader_init",
        "decrypt_empty_password",
        "decrypt_configured_password",
        "page_count",
        "text_extraction/span_build",
    }
)
PDF_ENCRYPTION_DISPOSITIONS = frozenset(
    {
        PDF_ENCRYPTION_NOT_MARKED,
        PDF_ENCRYPTION_EMPTY_CREDENTIAL,
        PDF_ENCRYPTION_CONFIGURED_CREDENTIAL,
        PDF_ENCRYPTION_CREDENTIAL_REQUIRED,
        PDF_ENCRYPTION_CREDENTIAL_INVALID,
    }
)
SAFE_PARSER_WARNING_CODES = frozenset(
    {
        "pdf_page_text_extraction_failed",
        "xlsx_sheet_cell_limit_truncated",
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
        self._pdf_subphase_elapsed_by_outcome_reason: dict[str, dict[str, list[float]]] = {}
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
        pdf_subphase_elapsed_seconds: dict[str, float] | None = None,
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
                for subphase, subphase_elapsed in _safe_pdf_subphase_elapsed_seconds(
                    pdf_subphase_elapsed_seconds
                ).items():
                    subphase_values = self._pdf_subphase_elapsed_by_outcome_reason.setdefault(
                        safe_pdf_reason,
                        {},
                    ).setdefault(subphase, [])
                    subphase_values.append(subphase_elapsed)

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
            pdf_subphase_elapsed_by_outcome_reason = {
                reason: {subphase: list(values) for subphase, values in subphase_values.items()}
                for reason, subphase_values in self._pdf_subphase_elapsed_by_outcome_reason.items()
            }
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
            pdf_subphase_elapsed_by_outcome_reason=pdf_subphase_elapsed_by_outcome_reason,
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
    actor_type: str = "SERVICE"
    actor_roles: tuple[str, ...] = ()


def _audit_request_metadata(
    *,
    request_id: str,
    idempotency_key: str | None,
) -> dict[str, str]:
    """Build audit request metadata without raw idempotency keys."""
    request_metadata = {"request_id": request_id}
    if idempotency_key:
        request_metadata["idempotency_key_sha256"] = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
    return request_metadata


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

    @classmethod
    def from_digest(
        cls,
        *,
        actual_sha256: str,
        expected_sha256: str | None = None,
    ) -> RouteValidatedSha256:
        """Bind a digest already computed while streaming at the public route."""
        normalized = actual_sha256.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("SHA256_MISMATCH")
        if expected_sha256 is not None and normalized != expected_sha256.lower():
            raise ValueError("SHA256_MISMATCH")
        return cls(value=normalized, _token=_ROUTE_VALIDATED_SHA256_TOKEN)


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
    terminal_disposition: str | None = None
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
            "terminal_disposition": self.terminal_disposition,
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
            pdf_subphase_elapsed_seconds=_pdf_subphase_elapsed_seconds(parse_result),
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
        reason = parse_result.private_diagnostics.get("pdf_diagnostic_reason")
        if isinstance(reason, str):
            return _safe_pdf_diagnostic_outcome_reason(reason)
        if parse_result.metadata.get("ocr_performed") is True:
            return "parsed_ocr"
        return "parsed_text"
    if not parse_result.errors:
        return "failed_other"
    return _pdf_failure_reason(parse_result.errors[0].code)


def _pdf_subphase_elapsed_seconds(parse_result: ParseResult) -> dict[str, float] | None:
    if parse_result.doc_type != "PDF":
        return None
    value = parse_result.private_diagnostics.get(PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY)
    if not isinstance(value, dict):
        return None
    safe_values: dict[str, float] = {}
    for subphase, elapsed_seconds in value.items():
        safe_subphase = _safe_pdf_parse_subphase(subphase)
        if safe_subphase is None or not isinstance(elapsed_seconds, int | float):
            continue
        safe_values[safe_subphase] = max(0.0, float(elapsed_seconds))
    return safe_values


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


def _safe_pdf_parse_subphase(subphase: object) -> str | None:
    if not isinstance(subphase, str):
        return None
    subphase_value = subphase.strip().lower()
    if subphase_value in PDF_PARSE_SUBPHASES:
        return subphase_value
    return None


def _safe_parser_warning_codes(warnings: list[str]) -> list[str]:
    """Reduce parser warnings to a bounded, locator-free code set."""
    safe: list[str] = []
    for warning in warnings:
        code = warning if warning in SAFE_PARSER_WARNING_CODES else "parser_incomplete_warning"
        if code not in safe:
            safe.append(code)
    return safe


def _safe_pdf_subphase_elapsed_seconds(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, float] = {}
    for subphase, elapsed_seconds in value.items():
        safe_subphase = _safe_pdf_parse_subphase(subphase)
        if safe_subphase is None or not isinstance(elapsed_seconds, int | float):
            continue
        safe[safe_subphase] = max(0.0, float(elapsed_seconds))
    return safe


def _parser_diagnostics_summary(
    *,
    counts_by_extension: Counter[str],
    counts_by_outcome: Counter[str],
    elapsed_by_extension: dict[str, list[float]],
    elapsed_by_outcome: dict[str, list[float]],
    pdf_counts_by_outcome_reason: Counter[str],
    pdf_elapsed_by_outcome_reason: dict[str, list[float]],
    pdf_subphase_elapsed_by_outcome_reason: dict[str, dict[str, list[float]]],
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
        subphase_elapsed_by_outcome_reason=pdf_subphase_elapsed_by_outcome_reason,
    )
    if pdf_diagnostics:
        summary["pdf_diagnostics"] = pdf_diagnostics
    return summary


def _pdf_diagnostics_summary(
    *,
    counts_by_outcome_reason: Counter[str],
    elapsed_by_outcome_reason: dict[str, list[float]],
    subphase_elapsed_by_outcome_reason: dict[str, dict[str, list[float]]],
) -> dict[str, Any]:
    if not counts_by_outcome_reason:
        return {}
    observed = {reason: values for reason, values in elapsed_by_outcome_reason.items() if values}
    summary: dict[str, Any] = {
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
    observed_subphases = _observed_pdf_subphase_elapsed_by_outcome_reason(
        subphase_elapsed_by_outcome_reason
    )
    if observed_subphases:
        summary["parse_subphase_elapsed_by_outcome_reason"] = {
            reason: {
                subphase: _elapsed_bucket_counts(values)
                for subphase, values in sorted(subphase_values.items())
                if values
            }
            for reason, subphase_values in sorted(observed_subphases.items())
        }
        summary["parse_subphase_total_elapsed_bucket_by_outcome_reason"] = {
            reason: {
                subphase: _elapsed_seconds_bucket(sum(values))
                for subphase, values in sorted(subphase_values.items())
                if values
            }
            for reason, subphase_values in sorted(observed_subphases.items())
        }
        summary["parse_subphase_max_elapsed_bucket_by_outcome_reason"] = {
            reason: {
                subphase: _elapsed_seconds_bucket(max(values))
                for subphase, values in sorted(subphase_values.items())
                if values
            }
            for reason, subphase_values in sorted(observed_subphases.items())
        }
        summary["observable_slowest_subphase_by_outcome_reason"] = {
            reason: _observable_slowest_phase(subphase_values)
            for reason, subphase_values in sorted(observed_subphases.items())
        }
    return summary


def _observed_pdf_subphase_elapsed_by_outcome_reason(
    value: dict[str, dict[str, list[float]]],
) -> dict[str, dict[str, list[float]]]:
    observed: dict[str, dict[str, list[float]]] = {}
    for reason, subphase_values in value.items():
        safe_reason = _safe_pdf_diagnostic_outcome_reason(reason)
        if safe_reason is None:
            continue
        observed_subphases = {
            subphase: values
            for subphase, values in subphase_values.items()
            if subphase in PDF_PARSE_SUBPHASES and values
        }
        if observed_subphases:
            observed[safe_reason] = observed_subphases
    return observed


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
        media_config: MediaConfig | None = None,
        password_manifest: DocumentPasswordManifest | None = None,
        no_substantive_content_attestation: (
            VerifiedDocumentNoSubstantiveContentAttestation
            | VerifiedDocumentNoSubstantiveContentAttestationManifest
            | None
        ) = None,
        watermark_page_config: WatermarkPageRuntimeConfig | None = None,
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
            media_config: Explicit media transcription config. Disabled by default.
            password_manifest: Optional content-hash keyed confidential PDF passwords.
            no_substantive_content_attestation: Optional signed one-document terminal exclusion.
            watermark_page_config: Verified typed watermark classifier material.
        """
        self._compliant_store = compliant_store
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._span_generator = span_generator or SpanGenerator()
        self._db_conn = db_conn
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._parse_limits = parse_limits or ParseLimits(max_bytes=max_bytes)
        if self._parse_limits.max_bytes != self._max_bytes:
            raise ValueError("ingestion and parser byte limits must match")
        self._ocr_config = ocr_config
        self._media_config = media_config
        self._password_manifest = password_manifest or DocumentPasswordManifest.none_configured()
        self._no_substantive_content_attestation = no_substantive_content_attestation
        if watermark_page_config is not None and not isinstance(
            watermark_page_config, WatermarkPageRuntimeConfig
        ):
            raise TypeError("watermark_page_config must be verified typed configuration")
        self._watermark_page_config = watermark_page_config
        self._watermark_admission_lock = Lock()

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
            - AuditSinkError propagates so the request transaction rolls back.
            - Other operational failures are captured in the result.
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
        except AuditSinkError:
            logger.error("Durable audit failed during ingestion: AuditSinkError")
            raise
        except DocumentNoSubstantiveContentAttestationError as error:
            return self._no_substantive_content_attestation_failure(error)
        except Exception as e:
            if (
                isinstance(e, IdisHttpError)
                and e.status_code == 403
                and e.code in _COMPLIANCE_DENIAL_CODES
            ):
                raise
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

    def ingest_file(
        self,
        ctx: IngestionContext,
        deal_id: UUID,
        *,
        filename: str,
        media_type: str | None,
        source_path: str | Path,
        size_bytes: int,
        validated_sha256: RouteValidatedSha256,
        metadata: dict[str, Any] | None = None,
        phase_recorder: object | None = None,
        db_conn: Connection | None = None,
    ) -> IngestionResult:
        """Ingest one route-staged file without rematerializing large media bytes."""
        try:
            return self._ingest_file_impl(
                ctx=ctx,
                deal_id=deal_id,
                filename=filename,
                media_type=media_type,
                source_path=Path(source_path),
                size_bytes=size_bytes,
                validated_sha256=validated_sha256,
                metadata=metadata or {},
                phase_recorder=phase_recorder,
                db_conn=db_conn,
            )
        except AuditSinkError:
            logger.error("Durable audit failed during streamed ingestion: AuditSinkError")
            raise
        except DocumentNoSubstantiveContentAttestationError as error:
            return self._no_substantive_content_attestation_failure(error)
        except Exception as error:
            if (
                isinstance(error, IdisHttpError)
                and error.status_code == 403
                and error.code in _COMPLIANCE_DENIAL_CODES
            ):
                raise
            logger.exception("Unexpected error during streamed ingestion")
            return IngestionResult(
                success=False,
                errors=[
                    IngestionError(
                        code=IngestionErrorCode.INTERNAL_ERROR,
                        message="Internal error during ingestion",
                        details={"exception_type": type(error).__name__},
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
        if isinstance(storage_result, IngestionError):
            return IngestionResult(
                success=False,
                sha256=sha256,
                errors=[storage_result],
            )

        phase_started_at = time.monotonic()
        parser_started_at = datetime.now(UTC)
        try:
            parse_result = self._parse_document(
                data,
                filename,
                media_type,
                sha256=sha256,
            )
        finally:
            parser_finished_at = datetime.now(UTC)
            _record_upload_phase(phase_recorder, UploadIngestionPhase.PARSE, phase_started_at)
        _record_parser_diagnostics(
            phase_recorder,
            filename=filename,
            parse_result=parse_result,
            elapsed_seconds=time.monotonic() - phase_started_at,
        )

        return self._materialize_parse_result(
            ctx=ctx,
            deal_id=deal_id,
            filename=filename,
            metadata=_without_streaming_proof(metadata),
            sha256=sha256,
            storage_key=storage_key,
            parse_result=parse_result,
            stored_object=storage_result,
            parser_started_at=parser_started_at,
            parser_finished_at=parser_finished_at,
            phase_recorder=phase_recorder,
            db_conn=db_conn,
        )

    def _ingest_file_impl(
        self,
        *,
        ctx: IngestionContext,
        deal_id: UUID,
        filename: str,
        media_type: str | None,
        source_path: Path,
        size_bytes: int,
        validated_sha256: RouteValidatedSha256,
        metadata: dict[str, Any],
        phase_recorder: object | None,
        db_conn: Connection | None,
    ) -> IngestionResult:
        trusted_sha_error = self._reject_untrusted_validated_sha256(validated_sha256)
        if trusted_sha_error is not None:
            return IngestionResult(success=False, errors=[trusted_sha_error])
        validation_error = self._validate_file_input(
            source_path=source_path,
            size_bytes=size_bytes,
            filename=filename,
        )
        if validation_error is not None:
            return IngestionResult(success=False, errors=[validation_error])

        sha256 = validated_sha256.value
        storage_key = self._build_storage_key(
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            sha256=sha256,
            filename=filename,
        )
        phase_started_at = time.monotonic()
        try:
            storage_error = self._store_raw_file(
                ctx=ctx,
                storage_key=storage_key,
                source_path=source_path,
                size_bytes=size_bytes,
                sha256=sha256,
                content_type=media_type,
            )
        finally:
            _record_upload_phase(
                phase_recorder,
                UploadIngestionPhase.OBJECT_STORE_WRITE,
                phase_started_at,
            )
        if isinstance(storage_error, IngestionError):
            return IngestionResult(success=False, sha256=sha256, errors=[storage_error])
        durable_metadata = _with_streaming_proof(metadata)

        phase_started_at = time.monotonic()
        parser_started_at = datetime.now(UTC)
        try:
            parse_result = self._parse_document_file(
                source_path=source_path,
                size_bytes=size_bytes,
                filename=filename,
                mime_type=media_type,
                sha256=sha256,
            )
        finally:
            parser_finished_at = datetime.now(UTC)
            _record_upload_phase(phase_recorder, UploadIngestionPhase.PARSE, phase_started_at)
        _record_parser_diagnostics(
            phase_recorder,
            filename=filename,
            parse_result=parse_result,
            elapsed_seconds=time.monotonic() - phase_started_at,
        )
        return self._materialize_parse_result(
            ctx=ctx,
            deal_id=deal_id,
            filename=filename,
            metadata=durable_metadata,
            sha256=sha256,
            storage_key=storage_key,
            parse_result=parse_result,
            stored_object=storage_error,
            parser_started_at=parser_started_at,
            parser_finished_at=parser_finished_at,
            phase_recorder=phase_recorder,
            db_conn=db_conn,
        )

    def _materialize_parse_result(
        self,
        *,
        ctx: IngestionContext,
        deal_id: UUID,
        filename: str,
        metadata: dict[str, Any],
        sha256: str,
        storage_key: str,
        parse_result: ParseResult,
        stored_object: StoredObjectMetadata,
        parser_started_at: datetime,
        parser_finished_at: datetime,
        phase_recorder: object | None,
        db_conn: Connection | None,
    ) -> IngestionResult:
        """Persist one parser result and its durable evidence/audit records."""
        if "watermark_page_classifications" not in parse_result.metadata:
            return self._materialize_parse_result_locked(
                ctx=ctx,
                deal_id=deal_id,
                filename=filename,
                metadata=metadata,
                sha256=sha256,
                storage_key=storage_key,
                parse_result=parse_result,
                stored_object=stored_object,
                parser_started_at=parser_started_at,
                parser_finished_at=parser_finished_at,
                phase_recorder=phase_recorder,
                db_conn=db_conn,
            )
        with self._watermark_admission_lock:
            return self._materialize_parse_result_locked(
                ctx=ctx,
                deal_id=deal_id,
                filename=filename,
                metadata=metadata,
                sha256=sha256,
                storage_key=storage_key,
                parse_result=parse_result,
                stored_object=stored_object,
                parser_started_at=parser_started_at,
                parser_finished_at=parser_finished_at,
                phase_recorder=phase_recorder,
                db_conn=db_conn,
            )

    def _materialize_parse_result_locked(
        self,
        *,
        ctx: IngestionContext,
        deal_id: UUID,
        filename: str,
        metadata: dict[str, Any],
        sha256: str,
        storage_key: str,
        parse_result: ParseResult,
        stored_object: StoredObjectMetadata,
        parser_started_at: datetime,
        parser_finished_at: datetime,
        phase_recorder: object | None,
        db_conn: Connection | None,
    ) -> IngestionResult:
        """Materialize after serializing any classifier cap check and write."""
        artifact_id = uuid4()
        document_id = uuid4()
        now = datetime.now(UTC)
        verified_object = self._compliant_store.verify_integrity(
            tenant_ctx=TenantContext(
                tenant_id=str(ctx.tenant_id),
                actor_id=ctx.actor_id,
                name="ingestion",
                timezone="UTC",
                data_region="me-south-1",
            ),
            key=storage_key,
            version_id=stored_object.version_id,
        )
        if verified_object != stored_object or verified_object.sha256 != sha256:
            raise ValueError("stored object integrity receipt does not match upload")
        metadata = {**metadata, "size_bytes": verified_object.size_bytes}
        try:
            actual_version = str(UUID(verified_object.version_id))
        except (ValueError, TypeError, AttributeError):
            actual_version = None
        if actual_version == verified_object.version_id:
            metadata["durable_object_integrity"] = {
                "schema_version": "durable-object-integrity-v1",
                "doc_id": str(artifact_id),
                "content_sha256": sha256,
                "size_bytes": verified_object.size_bytes,
                "uri_sha256": hashlib.sha256(storage_key.encode("utf-8")).hexdigest(),
                "version_id": actual_version,
                "backend": self._compliant_store.backend_name,
                "streaming_verified": True,
            }
        parser_identity = self._runtime_parser_identity(parse_result)
        watermark_identity = self._watermark_pre_ocr_parser_identity(parse_result)
        durable_identity_unavailable = actual_version != verified_object.version_id
        prerequisite = (
            "DURABLE_OBJECT_IDENTITY_UNAVAILABLE"
            if durable_identity_unavailable
            else "PARSER_NATIVE_IDENTITY_UNAVAILABLE"
            if parser_identity is None
            else None
        )
        if prerequisite is not None:
            metadata["source_preparation_prerequisite"] = prerequisite

        no_substantive_content_receipt: dict[str, object] | None = None
        if "watermark_page_classifications" in parse_result.metadata:
            no_substantive_content_receipt = self._build_watermark_page_receipt(
                ctx=ctx,
                filename=filename,
                deal_id=deal_id,
                metadata=metadata,
                sha256=sha256,
                parse_result=parse_result,
                now=now,
                db_conn=db_conn,
            )
        elif self._should_attempt_no_substantive_content_confirmation(parse_result):
            attestation = self._no_substantive_content_attestation
            if not isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestation):
                raise DocumentNoSubstantiveContentAttestationError(
                    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED
                )
            no_substantive_content_receipt = build_document_no_substantive_content_receipt(
                attestation=attestation,
                tenant_id=str(ctx.tenant_id),
                source_tree_fingerprint=str(metadata.get("source_tree_fingerprint", "")),
                safe_alias=filename,
                content_sha256=sha256,
                parse_result=parse_result,
                now=now,
            )

        attested_watermark = _attested_pre_ocr_watermark_receipt(no_substantive_content_receipt)
        if (
            not durable_identity_unavailable
            and parser_identity is None
            and watermark_identity is not None
            and attested_watermark
        ):
            # This member's classification identified the qualified closure.
            # Genuine OCR never reaches this branch: its identity stays None.
            prerequisite = None
            metadata.pop("source_preparation_prerequisite", None)
        attempt_identity = parser_identity
        if attempt_identity is None and prerequisite is None and attested_watermark:
            attempt_identity = watermark_identity

        artifact = self._create_artifact(
            artifact_id=artifact_id,
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            filename=filename,
            sha256=sha256,
            storage_uri=storage_key,
            metadata=metadata,
            version_id=verified_object.version_id,
            timestamp=now,
        )

        parse_status = ParseStatus.PARSED if parse_result.success else ParseStatus.FAILED
        doc_type_enum = self._map_doc_type(parse_result.doc_type)
        parse_metadata = self._parse_metadata_for_persistence(
            parse_result,
            document_id=document_id,
            artifact_id=artifact_id,
            content_sha256=sha256,
        )
        if prerequisite is not None:
            parse_metadata["source_preparation_prerequisite"] = prerequisite
        if no_substantive_content_receipt is not None:
            parse_metadata.pop("watermark_page_classifications", None)
            parse_metadata["document_no_substantive_content_receipt"] = (
                no_substantive_content_receipt
            )
            existing_refs: object = parse_metadata.get("applicable_schema_refs", [])
            parse_metadata["applicable_schema_refs"] = _receipt_schema_refs(
                existing_refs,
                no_substantive_content_receipt,
            )

        document = self._create_document(
            document_id=document_id,
            tenant_id=ctx.tenant_id,
            deal_id=deal_id,
            artifact_id=artifact_id,
            doc_type=doc_type_enum,
            parse_status=parse_status,
            parse_metadata=parse_metadata,
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

        if prerequisite is None and document.doc_type is DocumentType.PDF and parse_result.success:
            from idis.evaluation.real_example_terminal_reconciliation import (
                TerminalIngestionReconciliationError,
            )

            try:
                terminal_metadata = self._native_pdf_terminal_metadata(document, spans, sha256)
            except TerminalIngestionReconciliationError:
                prerequisite = "PARSER_TERMINAL_RECEIPT_UNAVAILABLE"
                terminal_metadata = {
                    **document.metadata,
                    "source_preparation_prerequisite": prerequisite,
                }
                artifact = artifact.model_copy(
                    update={
                        "metadata": {
                            **artifact.metadata,
                            "source_preparation_prerequisite": prerequisite,
                        },
                    }
                )
            document = document.model_copy(update={"metadata": terminal_metadata})

        try:
            phase_started_at = time.monotonic()
            try:
                self._persist_artifact(ctx.tenant_id, artifact, db_conn=db_conn)
                self._persist_document(ctx.tenant_id, document, db_conn=db_conn)
                if prerequisite is None and attempt_identity is not None:
                    self._persist_runtime_attempt(
                        ctx=ctx,
                        document=document,
                        spans=spans,
                        input_sha256=sha256,
                        parser_identity=attempt_identity,
                        started_at=parser_started_at,
                        finished_at=parser_finished_at,
                        db_conn=db_conn,
                    )
                self._persist_spans(
                    ctx.tenant_id,
                    deal_id,
                    document_id,
                    spans,
                    db_conn=db_conn,
                )
            finally:
                _record_upload_phase(
                    phase_recorder,
                    UploadIngestionPhase.PERSISTENCE,
                    phase_started_at,
                )

            phase_started_at = time.monotonic()
            try:
                self._emit_document_created(ctx, artifact, sha256, db_conn=db_conn)

                if no_substantive_content_receipt is not None and (
                    no_substantive_content_receipt.get("schema_version")
                    == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
                ):
                    self._emit_watermark_pages_classified(
                        ctx,
                        document,
                        no_substantive_content_receipt,
                        sha256,
                        db_conn=db_conn,
                    )

                if parse_result.success:
                    self._emit_ingestion_completed(
                        ctx,
                        document,
                        len(spans),
                        sha256,
                        db_conn=db_conn,
                    )
                else:
                    self._emit_ingestion_failed(
                        ctx,
                        document,
                        parse_result.errors,
                        sha256,
                        db_conn=db_conn,
                    )
                    if no_substantive_content_receipt is not None and bool(
                        no_substantive_content_receipt.get("whole_document_terminal", True)
                    ):
                        self._emit_no_substantive_content_confirmed(
                            ctx,
                            document,
                            no_substantive_content_receipt,
                            sha256,
                            db_conn=db_conn,
                        )
            finally:
                _record_upload_phase(phase_recorder, UploadIngestionPhase.AUDIT, phase_started_at)
        except Exception:
            self._discard_materialized_state(
                tenant_id=ctx.tenant_id,
                artifact_id=artifact_id,
                document_id=document_id,
            )
            raise

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
            terminal_disposition=(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
                if no_substantive_content_receipt is not None
                and bool(no_substantive_content_receipt.get("whole_document_terminal", True))
                else None
            ),
            errors=errors,
        )

    def _should_attempt_no_substantive_content_confirmation(
        self, parse_result: ParseResult
    ) -> bool:
        """Enter the signed terminal seam only for the exact rescue failure class."""
        if (
            not isinstance(
                self._no_substantive_content_attestation,
                VerifiedDocumentNoSubstantiveContentAttestation,
            )
            or parse_result.success
        ):
            return False
        return any(
            error.code == ParseErrorCode.OCR_RESCUE_NO_CONSENSUS for error in parse_result.errors
        )

    def _build_watermark_page_receipt(
        self,
        *,
        ctx: IngestionContext,
        filename: str,
        deal_id: UUID,
        metadata: Mapping[str, object],
        sha256: str,
        parse_result: ParseResult,
        now: datetime,
        db_conn: Connection | None,
    ) -> dict[str, object]:
        """Bind parser classifications to the current signed batch admission."""

        manifest = self._no_substantive_content_attestation
        if not isinstance(manifest, VerifiedDocumentNoSubstantiveContentAttestationManifest):
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED
            )
        manifest.exemplar.require_active(now=now)
        instant = now.astimezone(UTC)
        if instant < manifest.batch.approved_at or instant >= manifest.batch.expires_at:
            raise DocumentNoSubstantiveContentAttestationError(
                WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE
            )
        summary = manifest.runtime_summary()
        raw_classifications = parse_result.metadata.get("watermark_page_classifications")
        if not isinstance(raw_classifications, list) or not raw_classifications:
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )

        classifications: list[dict[str, object]] = []
        candidates = summary.get("candidates")
        if not isinstance(candidates, list):
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        for raw in raw_classifications:
            if not isinstance(raw, Mapping):
                raise DocumentNoSubstantiveContentAttestationError(
                    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
                )
            classification = dict(raw)
            identity_matches = [
                candidate
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and candidate.get("safe_alias") == filename
                and candidate.get("content_sha256") == sha256
                and candidate.get("page_number") == classification.get("page_number")
            ]
            exact_observation_match = len(identity_matches) == 1 and (
                identity_matches[0].get("canonical_render_sha256")
                == classification.get("canonical_render_sha256")
                and identity_matches[0].get("similarity_score")
                == classification.get("similarity_score")
            )
            pre_ocr_v2 = (
                classification.get("schema_version") == "watermark-page-classification-v2"
                and classification.get("classification_phase") == "pre_ocr"
                and classification.get("full_resolution_ocr_attempted") is False
            )
            attested = len(identity_matches) == 1 if pre_ocr_v2 else exact_observation_match
            signed_candidate = identity_matches[0] if len(identity_matches) == 1 else None
            classification.update(
                {
                    "batch_membership": "ATTESTED" if attested else "RUNTIME_UNSEEN",
                    "confirmation_source": (
                        ("reviewer_batch_identity" if pre_ocr_v2 else "reviewer_batch")
                        if attested
                        else "signed_exemplar_runtime_match"
                    ),
                    "post_run_review_required": not attested,
                    "review_status": (
                        "BATCH_ATTESTED"
                        if attested
                        else "WATERMARK_RUNTIME_CANDIDATE_REVIEW_REQUIRED"
                    ),
                }
            )
            if pre_ocr_v2:
                classification.update(
                    {
                        "batch_candidate_canonical_render_sha256": (
                            signed_candidate.get("canonical_render_sha256")
                            if isinstance(signed_candidate, Mapping)
                            else None
                        ),
                        "batch_candidate_similarity_score": (
                            signed_candidate.get("similarity_score")
                            if isinstance(signed_candidate, Mapping)
                            else None
                        ),
                    }
                )
            classifications.append(classification)

        page_count = parse_result.metadata.get("page_count")
        if type(page_count) is not int or page_count < 1:
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        whole_document_terminal = len(parse_result.spans) == 0
        if whole_document_terminal == parse_result.success:
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED
            )
        receipt: dict[str, object] = {
            "schema_version": NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION,
            "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
            "variant": "watermark_only",
            "safe_alias": filename,
            "content_sha256": sha256,
            "source_tree_fingerprint": str(metadata.get("source_tree_fingerprint", "")),
            "manifest_schema_version": summary["schema_version"],
            "manifest_sha256": summary["manifest_sha256"],
            "batch_envelope_sha256": summary["batch_envelope_sha256"],
            "candidate_set_sha256": summary["candidate_set_sha256"],
            "classifier_policy_id": summary["classifier_policy_id"],
            "classifier_policy_sha256": summary["classifier_policy_sha256"],
            "exemplar_envelope_sha256": summary["exemplar_envelope_sha256"],
            "exemplar_canonical_bitmap_sha256": summary["exemplar_canonical_bitmap_sha256"],
            "public_key_sha256": summary["public_key_sha256"],
            "signature_verified": True,
            "page_count": page_count,
            "classified_page_count": len(classifications),
            "substantive_span_count": len(parse_result.spans),
            "whole_document_terminal": whole_document_terminal,
            "watermark_page_classifications": classifications,
            "watermark_page_classifications_sha256": hashlib.sha256(
                canonicalize_json(classifications)
            ).hexdigest(),
        }
        validate_document_no_substantive_content_receipt_v2(
            receipt=receipt,
            verified_manifest=summary,
            tenant_id=str(ctx.tenant_id),
            source_tree_fingerprint=str(metadata.get("source_tree_fingerprint", "")),
            safe_alias=filename,
            content_sha256=sha256,
            now=now,
        )
        self._enforce_watermark_page_sanity_cap(
            ctx=ctx,
            deal_id=deal_id,
            safe_alias=filename,
            current_receipt=receipt,
            verified_manifest=summary,
            db_conn=db_conn,
        )
        return receipt

    def _enforce_watermark_page_sanity_cap(
        self,
        *,
        ctx: IngestionContext,
        deal_id: UUID,
        safe_alias: str,
        current_receipt: Mapping[str, object],
        verified_manifest: Mapping[str, object],
        db_conn: Connection | None,
    ) -> None:
        """Fail before document persistence when a company's signed 15% cap is exceeded."""

        raw_company_counts = verified_manifest.get("company_file_counts")
        if not isinstance(raw_company_counts, Mapping):
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        company_counts = {
            str(alias): count
            for alias, count in raw_company_counts.items()
            if isinstance(alias, str) and type(count) is int and count > 0
        }
        if len(company_counts) != len(raw_company_counts):
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        company_alias = self._watermark_company_alias(
            safe_alias=safe_alias,
            company_counts=company_counts,
        )

        repo = self._documents_repo(ctx.tenant_id, db_conn=db_conn)
        if repo is not None:
            repo.acquire_watermark_company_admission_lock(
                deal_id=str(deal_id),
                company_alias=company_alias,
            )
            prior_documents: list[object] = repo.list_documents_by_deal(
                str(deal_id),
                parsed_only=False,
            )
        else:
            prior_documents = [
                document
                for key, document in self._documents.items()
                if key.startswith(f"{ctx.tenant_id}:") and document.deal_id == deal_id
            ]

        prior_page_count = 0
        for document in prior_documents:
            document_metadata = (
                document.get("metadata")
                if isinstance(document, Mapping)
                else getattr(document, "metadata", None)
            )
            if not isinstance(document_metadata, Mapping):
                continue
            prior_receipt = document_metadata.get("document_no_substantive_content_receipt")
            if not isinstance(prior_receipt, Mapping):
                continue
            if (
                prior_receipt.get("schema_version")
                != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
            ):
                continue
            prior_alias = prior_receipt.get("safe_alias")
            prior_classifications = prior_receipt.get("watermark_page_classifications")
            prior_count = prior_receipt.get("classified_page_count")
            if (
                not isinstance(prior_alias, str)
                or not isinstance(prior_classifications, list)
                or type(prior_count) is not int
                or prior_count < 1
                or prior_count != len(prior_classifications)
                or prior_receipt.get("source_tree_fingerprint")
                != current_receipt.get("source_tree_fingerprint")
                or prior_receipt.get("manifest_sha256") != current_receipt.get("manifest_sha256")
            ):
                raise DocumentNoSubstantiveContentAttestationError(
                    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
                )
            prior_company_alias = self._watermark_company_alias(
                safe_alias=prior_alias,
                company_counts=company_counts,
            )
            if prior_company_alias == company_alias:
                prior_page_count += prior_count

        current_count = current_receipt.get("classified_page_count")
        if type(current_count) is not int or current_count < 1:
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        if 100 * (prior_page_count + current_count) > 15 * company_counts[company_alias]:
            raise DocumentNoSubstantiveContentAttestationError(WATERMARK_PAGE_SANITY_CAP_EXCEEDED)

    @staticmethod
    def _watermark_company_alias(
        *,
        safe_alias: str,
        company_counts: Mapping[str, int],
    ) -> str:
        matches = [alias for alias in company_counts if safe_alias.startswith(f"{alias}-document-")]
        if len(matches) != 1:
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE
            )
        return matches[0]

    @staticmethod
    def _no_substantive_content_attestation_failure(
        error: DocumentNoSubstantiveContentAttestationError,
    ) -> IngestionResult:
        """Return only the stable signed-admission failure code."""
        return IngestionResult(
            success=False,
            errors=[
                IngestionError(
                    code=IngestionErrorCode.NO_SUBSTANTIVE_CONTENT_ATTESTATION_FAILED,
                    message="Document no-substantive-content attestation was not admitted",
                    details={"attestation_error_code": error.code},
                )
            ],
        )

    def _validate_file_input(
        self,
        *,
        source_path: Path,
        size_bytes: int,
        filename: str,
    ) -> IngestionError | None:
        if size_bytes <= 0:
            return IngestionError(
                code=IngestionErrorCode.EMPTY_FILE,
                message="Empty file",
                details={"filename": filename},
            )
        if size_bytes > self._max_bytes:
            return IngestionError(
                code=IngestionErrorCode.FILE_TOO_LARGE,
                message=f"File exceeds maximum size of {self._max_bytes} bytes",
                details={"size_bytes": size_bytes, "max_bytes": self._max_bytes},
            )
        try:
            actual_size = source_path.stat().st_size
        except OSError:
            return IngestionError(
                code=IngestionErrorCode.STORAGE_FAILED,
                message="Staged upload is unavailable",
            )
        if actual_size != size_bytes:
            return IngestionError(
                code=IngestionErrorCode.STORAGE_FAILED,
                message="Staged upload size changed",
            )
        return None

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
    ) -> StoredObjectMetadata | IngestionError:
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
        try:
            tenant_ctx = TenantContext(
                tenant_id=str(ctx.tenant_id),
                actor_id=ctx.actor_id,
                name="ingestion",
                timezone="UTC",
                data_region="me-south-1",
            )
            stored = self._compliant_store.put(
                tenant_ctx=tenant_ctx,
                key=storage_key,
                data=data,
                content_type=content_type,
                data_class=DataClass.CLASS_2,
            )
            if (
                stored.tenant_id != str(ctx.tenant_id)
                or stored.key != storage_key
                or stored.sha256 != hashlib.sha256(data).hexdigest()
                or stored.size_bytes != len(data)
            ):
                raise ValueError("object store returned inconsistent upload metadata")
            return stored
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

    def _store_raw_file(
        self,
        *,
        ctx: IngestionContext,
        storage_key: str,
        source_path: Path,
        size_bytes: int,
        sha256: str,
        content_type: str | None,
    ) -> StoredObjectMetadata | IngestionError:
        """Stream a staged upload through the compliance-enforced object store."""
        from idis.api.errors import IdisHttpError

        try:
            tenant_ctx = TenantContext(
                tenant_id=str(ctx.tenant_id),
                actor_id=ctx.actor_id,
                name="ingestion",
                timezone="UTC",
                data_region="me-south-1",
            )
            with source_path.open("rb") as source:
                stored = self._compliant_store.put_stream(
                    tenant_ctx=tenant_ctx,
                    key=storage_key,
                    source=source,
                    content_type=content_type,
                    data_class=DataClass.CLASS_2,
                    expected_sha256=sha256,
                    expected_size_bytes=size_bytes,
                )
            if (
                stored.tenant_id != str(ctx.tenant_id)
                or stored.key != storage_key
                or stored.sha256 != sha256
                or stored.size_bytes != size_bytes
            ):
                raise ValueError("object store returned inconsistent upload metadata")
            return stored
        except IdisHttpError as error:
            if error.status_code == 403 and error.code in _COMPLIANCE_DENIAL_CODES:
                raise
            logger.error("HTTP error during streamed storage: %s", type(error).__name__)
        except Exception as error:
            logger.error("Failed to stream raw document: %s", type(error).__name__)
        return IngestionError(
            code=IngestionErrorCode.STORAGE_FAILED,
            message="Failed to store document in object store",
        )

    def _parse_document(
        self,
        data: bytes,
        filename: str | None,
        mime_type: str | None,
        *,
        sha256: str | None = None,
        source_path: Path | None = None,
    ) -> ParseResult:
        """Parse document using parser registry."""
        return parse_bytes(
            data=data,
            filename=filename,
            mime_type=mime_type,
            limits=self._parse_limits,
            ocr_config=self._ocr_config,
            media_config=self._media_config,
            pdf_password=(self._password_manifest.resolve(sha256) if sha256 is not None else None),
            source_path=source_path,
            watermark_page_config=self._watermark_page_config,
        )

    def _parse_document_file(
        self,
        *,
        source_path: Path,
        size_bytes: int,
        filename: str,
        mime_type: str | None,
        sha256: str,
    ) -> ParseResult:
        """Parse only bytes that remain bound to the route-validated object digest."""
        from idis.parsers.registry import is_media_source

        is_media = is_media_source(filename=filename, mime_type=mime_type)
        if is_media:
            return self._parse_verified_media_file(
                source_path=source_path,
                size_bytes=size_bytes,
                sha256=sha256,
            )

        verified_before, integrity_error = self._read_verified_staged_file(
            source_path=source_path,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            materialize=True,
        )
        if integrity_error is not None:
            return integrity_error
        assert verified_before is not None

        assert verified_before.data is not None
        parse_result = self._parse_document(
            verified_before.data,
            filename,
            mime_type,
            sha256=sha256,
            source_path=source_path,
        )

        verified_after, integrity_error = self._read_verified_staged_file(
            source_path=source_path,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            materialize=False,
        )
        if integrity_error is not None:
            return integrity_error
        assert verified_after is not None
        if verified_after.stat_signature != verified_before.stat_signature:
            return self._staged_file_integrity_failure()
        return parse_result

    def _parse_verified_media_file(
        self,
        *,
        source_path: Path,
        size_bytes: int,
        sha256: str,
    ) -> ParseResult:
        """Parse a private hard-link bound to the already verified staged inode."""
        from idis.parsers.media import parse_media_file

        verified_source, integrity_error = self._read_verified_staged_file(
            source_path=source_path,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            materialize=False,
        )
        if integrity_error is not None:
            return integrity_error
        assert verified_source is not None

        snapshot_dir = source_path.parent / f".idis-media-parse-{uuid4().hex}"
        snapshot_path = snapshot_dir / "media.mp4"
        directory_created = False
        parse_result: ParseResult | None = None
        source_linked: _VerifiedStagedFile | None = None
        cleanup_failed = False
        try:
            snapshot_dir.mkdir(mode=0o700)
            directory_created = True
            os.link(source_path, snapshot_path, follow_symlinks=False)

            source_linked, source_error = self._read_verified_staged_file(
                source_path=source_path,
                expected_size_bytes=size_bytes,
                expected_sha256=sha256,
                materialize=False,
            )
            snapshot_before, snapshot_error = self._read_verified_staged_file(
                source_path=snapshot_path,
                expected_size_bytes=size_bytes,
                expected_sha256=sha256,
                materialize=False,
            )
            if source_error is not None or snapshot_error is not None:
                parse_result = self._staged_file_integrity_failure()
            else:
                assert source_linked is not None and snapshot_before is not None
                if source_linked.stat_signature != snapshot_before.stat_signature:
                    parse_result = self._staged_file_integrity_failure()
                else:
                    parse_result = parse_media_file(
                        snapshot_path,
                        size_bytes=size_bytes,
                        limits=self._parse_limits,
                        media_config=self._media_config,
                    )
                    snapshot_after, snapshot_error = self._read_verified_staged_file(
                        source_path=snapshot_path,
                        expected_size_bytes=size_bytes,
                        expected_sha256=sha256,
                        materialize=False,
                    )
                    source_after, source_error = self._read_verified_staged_file(
                        source_path=source_path,
                        expected_size_bytes=size_bytes,
                        expected_sha256=sha256,
                        materialize=False,
                    )
                    if source_error is not None or snapshot_error is not None:
                        parse_result = self._staged_file_integrity_failure()
                    else:
                        assert source_after is not None and snapshot_after is not None
                        if (
                            snapshot_after.stat_signature != snapshot_before.stat_signature
                            or source_after.stat_signature != source_linked.stat_signature
                            or source_after.stat_signature != snapshot_after.stat_signature
                        ):
                            parse_result = self._staged_file_integrity_failure()
        except OSError:
            parse_result = self._staged_file_integrity_failure()
        finally:
            try:
                if snapshot_path.exists():
                    # Windows hard links share the staged file's read-only bit.
                    snapshot_path.chmod(0o600)
            except OSError:
                cleanup_failed = True
            try:
                snapshot_path.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
            if directory_created:
                try:
                    snapshot_dir.rmdir()
                except OSError:
                    cleanup_failed = True

        if cleanup_failed:
            return self._staged_file_integrity_failure()
        if parse_result is None or source_linked is None:
            return self._staged_file_integrity_failure()

        verified_final, integrity_error = self._read_verified_staged_file(
            source_path=source_path,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            materialize=False,
        )
        if integrity_error is not None:
            return integrity_error
        assert verified_final is not None
        # Removing our hard link legitimately changes ctime/link-count state only.
        if verified_final.stat_signature[:4] != source_linked.stat_signature[:4]:
            return self._staged_file_integrity_failure()
        return parse_result

    def _read_verified_staged_file(
        self,
        *,
        source_path: Path,
        expected_size_bytes: int,
        expected_sha256: str,
        materialize: bool,
    ) -> tuple[_VerifiedStagedFile | None, ParseResult | None]:
        """Read at most expected-size+1 and prove digest/stat stability for one pass."""
        chunks: list[bytes] | None = [] if materialize else None
        digest = hashlib.sha256()
        total = 0
        try:
            stat_before = source_path.stat()
            with source_path.open("rb") as source:
                while True:
                    remaining = expected_size_bytes - total
                    chunk = source.read(min(1024 * 1024, max(1, remaining + 1)))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_size_bytes or total > self._parse_limits.max_bytes:
                        return None, self._staged_file_integrity_failure()
                    digest.update(chunk)
                    if chunks is not None:
                        chunks.append(chunk)
            stat_after = source_path.stat()
        except OSError:
            return None, self._staged_file_integrity_failure()

        before_signature = self._staged_file_stat_signature(stat_before)
        after_signature = self._staged_file_stat_signature(stat_after)
        if (
            total != expected_size_bytes
            or digest.hexdigest() != expected_sha256
            or before_signature != after_signature
        ):
            return None, self._staged_file_integrity_failure()
        return (
            _VerifiedStagedFile(
                data=b"".join(chunks) if chunks is not None else None,
                stat_signature=after_signature,
            ),
            None,
        )

    @staticmethod
    def _staged_file_stat_signature(stat: Any) -> tuple[int, int, int, int, int]:
        return (
            int(stat.st_dev),
            int(stat.st_ino),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
        )

    @staticmethod
    def _staged_file_integrity_failure() -> ParseResult:
        return ParseResult(
            doc_type="UNKNOWN",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.CORRUPTED_FILE,
                    message="Staged upload failed parser integrity verification",
                )
            ],
        )

    def _map_doc_type(self, parser_doc_type: str) -> DocumentType:
        """Map parser doc_type string to DocumentType enum."""
        type_map = {
            "PDF": DocumentType.PDF,
            "XLSX": DocumentType.XLSX,
            "DOCX": DocumentType.DOCX,
            "PPTX": DocumentType.PPTX,
            "HTML": DocumentType.HTML,
            "TEXT": DocumentType.TEXT,
            "IMAGE": DocumentType.IMAGE,
            "MEDIA": DocumentType.VIDEO,
            "UNKNOWN": DocumentType.PDF,
        }
        return type_map.get(parser_doc_type, DocumentType.PDF)

    def _parse_metadata_for_persistence(
        self,
        parse_result: ParseResult,
        *,
        document_id: UUID,
        artifact_id: UUID,
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Return safe parser metadata needed for persisted preflight triage."""
        from idis.services.documents.parser_capabilities import triage_document

        capability = triage_document(parse_result=parse_result)
        parser_runtime = self._parser_runtime(parse_result)
        parser_source_type = self._parser_source_type(parse_result)
        error_codes = [error.code.value for error in parse_result.errors]
        parser_metadata = dict(parse_result.metadata)
        image_only_keys = frozenset({"image_only_page_receipts", "image_only_page_evaluation"})
        if image_only_keys & parser_metadata.keys():
            safe_image_only = project_safe_image_only_page_metadata(
                receipts=parser_metadata.pop("image_only_page_receipts", None),
                evaluation=parser_metadata.pop("image_only_page_evaluation", None),
                parse_status=("PARSED" if parse_result.success else "FAILED"),
            )
            if safe_image_only is None:
                raise ImageOnlyPageContractError
            evaluation = safe_image_only["image_only_page_evaluation"]
            if not isinstance(evaluation, dict) or (
                content_sha256 is not None
                and evaluation.get("content_sha256") != content_sha256
                or evaluation.get("substantive_span_count") != len(parse_result.spans)
            ):
                raise ImageOnlyPageContractError
            parser_metadata.update(safe_image_only)
        metadata = {
            **parser_metadata,
            "parse_error_codes": error_codes,
            "parse_warning_codes": _safe_parser_warning_codes(parse_result.warnings),
            "detected_format": parse_result.doc_type,
            "parser_doc_type": parse_result.doc_type,
            "parser_mode": self._parser_mode(parse_result),
            "parser_runtime": parser_runtime,
            "parser_runtime_status": "completed" if parse_result.success else "failed",
            "parser_source_type": parser_source_type,
            "parser_support_status": capability.support_status.value,
            "parser_triage_status": capability.triage_status.value,
            "parser_reason_codes": capability.reason_codes,
            "parser_requires_ocr": capability.requires_ocr,
            "parser_requires_conversion": capability.requires_conversion,
            "source_evidence_id": str(document_id),
            "source_document_id": str(document_id),
            "source_artifact_id": str(artifact_id),
        }
        encryption_disposition = parse_result.private_diagnostics.get(
            PDF_ENCRYPTION_DISPOSITION_KEY
        )
        if parse_result.doc_type == "PDF" and encryption_disposition in PDF_ENCRYPTION_DISPOSITIONS:
            metadata[PDF_ENCRYPTION_DISPOSITION_KEY] = encryption_disposition
        return metadata

    def _parser_mode(self, parse_result: ParseResult) -> str:
        if parse_result.metadata.get(
            "media_transcription_performed"
        ) is True and self._ocr_participated(parse_result):
            return "media_multitrack"
        if parse_result.metadata.get("media_transcription_performed") is True:
            return "media_stt"
        if self._ocr_participated(parse_result):
            return "ocr"
        if parse_result.doc_type in {"IMAGE"}:
            return "ocr"
        if parse_result.doc_type in {"MEDIA"}:
            return "media_stt"
        return "native"

    @staticmethod
    def _ocr_participated(parse_result: ParseResult) -> bool:
        """Recognize unsuccessful dispatch too; absence of success is not native proof."""
        required = parse_result.metadata.get("ocr_required_page_count")
        required_pages = parse_result.metadata.get("ocr_required_page_numbers")
        return (
            parse_result.metadata.get("ocr_performed") is True
            or parse_result.metadata.get("media_frame_ocr_performed") is True
            or any(
                error.code.name.startswith(("OCR_", "MEDIA_FRAME_OCR_"))
                for error in parse_result.errors
            )
            or (type(required) is int and required > 0)
            or (isinstance(required_pages, (list, tuple, set, frozenset)) and bool(required_pages))
        )

    def _runtime_parser_identity(self, parse_result: ParseResult) -> tuple[str, str] | None:
        """Bind native code actually loaded; unqualified OCR/media has no attempt."""
        if self._parser_mode(parse_result) != "native":
            # Poppler has a real binding but Tesseract/tessdata does not yet.
            # Native/H2 qualification must supply the complete closure first.
            return None
        backing = {
            "PDF": ("pypdf", "pypdf"),
            "XLSX": ("openpyxl", "openpyxl"),
            "DOCX": ("docx", "python-docx"),
            "PPTX": ("pptx", "python-pptx"),
        }.get(parse_result.doc_type)
        versions = []
        if backing is not None:
            module_name, distribution_name = backing
            try:
                module = importlib.import_module(module_name)
                distribution = importlib.metadata.distribution(distribution_name)
                if not module.__file__ or not distribution.version or not distribution.files:
                    return None
                executing = Path(module.__file__).resolve()
                if not any(
                    Path(str(distribution.locate_file(entry))).resolve() == executing
                    for entry in distribution.files
                ):
                    return None
                versions.append([distribution_name, distribution.version])
            except (ImportError, OSError, importlib.metadata.PackageNotFoundError):
                return None
        elif parse_result.doc_type not in {"TEXT", "HTML"}:
            return None
        digest = hashlib.sha256(
            canonicalize_json(
                [
                    "idis-parser-runtime-v1",
                    sys.implementation.name,
                    platform.python_version(),
                    sorted(versions),
                    [],
                ]
            )
        ).hexdigest()
        name = (
            f"idis-ingestion/{self._parser_runtime(parse_result)}/"
            f"{self._parser_source_type(parse_result)}/{parse_result.doc_type.lower()}"
        )
        return name, "idis-parser-runtime-v1:" + digest

    def _watermark_pre_ocr_parser_identity(
        self, parse_result: ParseResult
    ) -> tuple[str, str] | None:
        """Bind pillow, pdf2image, and the governed Poppler closure.

        Classification that did not run, and any parse that actually performed
        OCR, get no identity. A Poppler build other than the governed closure
        is not named. This path does not name pypdf or Tesseract.
        """
        if parse_result.metadata.get("ocr_performed") is True:
            return None
        classifications = parse_result.metadata.get("watermark_page_classifications")
        if not isinstance(classifications, list) or not classifications:
            return None
        if any(
            not isinstance(row, Mapping)
            or row.get("classification_phase") != "pre_ocr"
            or row.get("full_resolution_ocr_attempted") is not False
            for row in classifications
        ):
            return None
        pillow_version = _loaded_distribution_version("PIL", "pillow")
        renderer_version = _loaded_distribution_version("pdf2image", "pdf2image")
        if pillow_version is None or renderer_version is None:
            return None
        try:
            from idis.evaluation.real_example_terminal_reconciliation import (
                _PINNED_POPPLER_RUNTIME_IDENTITY_SHA256,
            )
            from idis.parsers.poppler_runtime import (
                PopplerRuntimeBindingError,
                resolve_pinned_poppler_runtime,
            )

            binding = resolve_pinned_poppler_runtime()
        except PopplerRuntimeBindingError:
            return None
        from idis.parsers.poppler_linux_runtime import (
            LINUX_PREFIX_IDENTITY_SCHEMA_VERSION,
            PinnedLinuxPopplerRuntime,
            pinned_linux_poppler_identity_sha256,
        )
        from idis.parsers.poppler_runtime import (
            POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION,
            PinnedPopplerRuntime,
        )

        if type(binding) is PinnedPopplerRuntime:
            if binding.runtime_identity_sha256 != _PINNED_POPPLER_RUNTIME_IDENTITY_SHA256:
                return None
            poppler_label = POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION
        elif type(binding) is PinnedLinuxPopplerRuntime:
            recomputed = pinned_linux_poppler_identity_sha256(binding)
            if (
                binding.runtime_identity_sha256 != recomputed
                or binding.runtime_identity_sha256 == _PINNED_POPPLER_RUNTIME_IDENTITY_SHA256
            ):
                return None
            poppler_label = LINUX_PREFIX_IDENTITY_SCHEMA_VERSION
        else:
            return None
        digest = hashlib.sha256(
            canonicalize_json(
                [
                    "idis-watermark-pre-ocr-runtime-v1",
                    sys.implementation.name,
                    platform.python_version(),
                    sorted(
                        [
                            ["pdf2image", renderer_version],
                            ["pillow", pillow_version],
                        ]
                    ),
                    [
                        poppler_label,
                        binding.version,
                        binding.runtime_identity_sha256,
                    ],
                ]
            )
        ).hexdigest()
        return (
            "idis-ingestion/pre-ocr-watermark/pdf",
            "idis-watermark-pre-ocr-runtime-v1:" + digest,
        )

    def _persist_runtime_attempt(
        self,
        *,
        ctx: IngestionContext,
        document: Document,
        spans: list[DocumentSpan],
        input_sha256: str,
        parser_identity: tuple[str, str],
        started_at: datetime,
        finished_at: datetime,
        db_conn: Connection | None,
    ) -> str:
        """Reuse the freezer's exact terminal projection and persist ordinal one."""
        from idis.persistence.repositories.task7_source_freeze import (
            _sha256_json,
            _terminal_document_projection,
        )

        document_id = str(document.document_id)
        attempt_id = str(
            uuid5(
                NAMESPACE_URL,
                canonicalize_json(
                    [
                        "idis-runtime-parse-attempt-v1",
                        str(ctx.tenant_id),
                        str(document.deal_id),
                        document_id,
                        1,
                    ]
                ).decode("utf-8"),
            )
        )
        projection = _terminal_document_projection(
            document.metadata,
            doc_type=document.doc_type.value,
            parse_status=document.parse_status.value,
            spans=[
                {
                    "locator": span.locator,
                    "span_type": span.span_type.value,
                    "content_hash": span.content_hash,
                }
                for span in spans
            ],
        )
        receipt = _sha256_json(
            [
                "terminal-document-metadata-v2",
                document.doc_type.value,
                document.parse_status.value,
                projection,
            ]
        )
        repo = self._documents_repo(ctx.tenant_id, db_conn=db_conn)
        if repo is not None:
            repo.create_runtime_parse_attempt(
                parse_attempt_id=attempt_id,
                deal_id=str(document.deal_id),
                document_id=document_id,
                parser_name=parser_identity[0],
                parser_version=parser_identity[1],
                input_sha256=input_sha256,
                receipt_sha256=receipt,
                status=document.parse_status.value,
                started_at=started_at,
                finished_at=finished_at,
            )
        return attempt_id

    @staticmethod
    def _native_pdf_terminal_metadata(
        document: Document,
        spans: list[DocumentSpan],
        content_sha256: str,
    ) -> dict[str, Any]:
        """Compose existing receipt builders over the exact rows being persisted."""
        from idis.evaluation.real_example_terminal_reconciliation import (
            _safe_durable_span_receipts,
            _safe_pdf_ocr_evidence_v5,
        )
        from idis.persistence.repositories.task7_source_freeze import _SOURCE_SCHEMA_AUTHORITIES

        _, locator_proof = _safe_durable_span_receipts(
            {"items": [span.model_dump(mode="json") for span in spans], "total": len(spans)},
            document_id=str(document.document_id),
            is_media=False,
            extension="pdf",
            ocr_performed=False,
            require_pdf_page_census=True,
        )
        evidence = _safe_pdf_ocr_evidence_v5(
            document.metadata,
            content_sha256=content_sha256,
            parse_status=document.parse_status.value,
            locator_proof=locator_proof,
            watermark_receipt=None,
            expected_page_count=None,
            whole_document_terminal=False,
        )
        payloads = {
            "ocr-terminal-evidence": evidence,
            "pdf-page-disposition-census": evidence["pdf_page_census"],
        }
        refs = [
            {
                "schema_id": schema_id,
                "schema_version": payload["schema_version"],
                "schema_sha256": _SOURCE_SCHEMA_AUTHORITIES[(schema_id, payload["schema_version"])],
                "validated_payload": payload,
            }
            for schema_id, payload in sorted(payloads.items())
        ]
        return {**document.metadata, "applicable_schema_refs": refs}

    def _parser_runtime(self, parse_result: ParseResult) -> str:
        if self._parser_mode(parse_result) == "ocr":
            return "tesseract" if self._ocr_config is not None else "unconfigured_ocr"
        if self._parser_mode(parse_result) == "media_multitrack":
            if isinstance(getattr(self._media_config, "adapter", None), FasterWhisperMediaAdapter):
                return f"{FASTER_WHISPER_ADAPTER_NAME}+tesseract"
            if self._media_config is not None:
                return "configured_media_multitrack_adapter"
            return "unconfigured_media_multitrack"
        if self._parser_mode(parse_result) == "media_stt":
            if isinstance(getattr(self._media_config, "adapter", None), FasterWhisperMediaAdapter):
                return FASTER_WHISPER_ADAPTER_NAME
            if self._media_config is not None:
                return "configured_media_adapter"
            return "unconfigured_media_stt"
        return "built_in"

    def _parser_source_type(self, parse_result: ParseResult) -> str:
        if parse_result.doc_type == "IMAGE":
            return "image_ocr"
        if parse_result.doc_type == "MEDIA":
            if self._parser_mode(parse_result) == "media_multitrack":
                return "media_dual_track"
            return "media_stt"
        if self._parser_mode(parse_result) == "ocr":
            return "pdf_ocr_fallback"
        return "native_parser"

    def _create_artifact(
        self,
        artifact_id: UUID,
        tenant_id: UUID,
        deal_id: UUID,
        filename: str,
        sha256: str,
        storage_uri: str,
        metadata: dict[str, Any],
        version_id: str,
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
            version_id=version_id,
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

    def _discard_materialized_state(
        self,
        *,
        tenant_id: UUID,
        artifact_id: UUID,
        document_id: UUID,
    ) -> None:
        """Remove memory mirrors when the enclosing durable transaction must roll back."""
        self._artifacts.pop(f"{tenant_id}:{artifact_id}", None)
        self._documents.pop(f"{tenant_id}:{document_id}", None)
        self._spans.pop(f"{tenant_id}:{document_id}", None)

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
        *,
        db_conn: Connection | None = None,
    ) -> None:
        """Emit document.created audit event."""
        event = build_canonical_audit_event(
            tenant_id=str(ctx.tenant_id),
            event_type="document.created",
            severity="MEDIUM",
            summary="Document artifact created by ingestion service",
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            actor_roles=ctx.actor_roles,
            request_id=ctx.request_id,
            method="POST",
            path="/internal/ingestion/documents",
            status_code=201,
            resource_type="document",
            resource_id=str(artifact.doc_id),
            deal_id=str(artifact.deal_id),
            idempotency_key_sha256=(
                hashlib.sha256(ctx.idempotency_key.encode("utf-8")).hexdigest()
                if ctx.idempotency_key
                else None
            ),
            safe={"doc_type": artifact.doc_type.value},
            hashes=[sha256],
            refs=[f"deal_id:{artifact.deal_id}"],
        )
        self._emit_audit_event(event, db_conn=db_conn)

    def _emit_ingestion_completed(
        self,
        ctx: IngestionContext,
        document: Document,
        span_count: int,
        sha256: str,
        *,
        db_conn: Connection | None = None,
    ) -> None:
        """Emit document.ingestion.completed audit event."""
        event = build_canonical_audit_event(
            tenant_id=str(ctx.tenant_id),
            event_type="document.ingestion.completed",
            severity="LOW",
            summary="Document ingestion completed",
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            actor_roles=ctx.actor_roles,
            request_id=ctx.request_id,
            method="POST",
            path="/internal/ingestion/documents",
            status_code=200,
            resource_type="document",
            resource_id=str(document.document_id),
            deal_id=str(document.deal_id),
            idempotency_key_sha256=(
                hashlib.sha256(ctx.idempotency_key.encode("utf-8")).hexdigest()
                if ctx.idempotency_key
                else None
            ),
            safe={
                "doc_type": document.doc_type.value,
                "span_count": span_count,
                "parse_status": document.parse_status.value,
            },
            hashes=[sha256],
            refs=[f"deal_id:{document.deal_id}"],
        )
        self._emit_audit_event(event, db_conn=db_conn)

    def _emit_ingestion_failed(
        self,
        ctx: IngestionContext,
        document: Document,
        parse_errors: list[ParseError],
        sha256: str,
        *,
        db_conn: Connection | None = None,
    ) -> None:
        """Emit document.ingestion.failed audit event."""
        event = build_canonical_audit_event(
            tenant_id=str(ctx.tenant_id),
            event_type="document.ingestion.failed",
            severity="MEDIUM",
            summary="Document ingestion failed",
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            actor_roles=ctx.actor_roles,
            request_id=ctx.request_id,
            method="POST",
            path="/internal/ingestion/documents",
            status_code=422,
            resource_type="document",
            resource_id=str(document.document_id),
            deal_id=str(document.deal_id),
            idempotency_key_sha256=(
                hashlib.sha256(ctx.idempotency_key.encode("utf-8")).hexdigest()
                if ctx.idempotency_key
                else None
            ),
            safe={
                "doc_type": document.doc_type.value,
                "parse_status": document.parse_status.value,
                "failure_count": len(parse_errors),
                "failure_codes": [error.code.value for error in parse_errors],
            },
            hashes=[sha256],
            refs=[f"deal_id:{document.deal_id}"],
        )
        self._emit_audit_event(event, db_conn=db_conn)

    def _emit_no_substantive_content_confirmed(
        self,
        ctx: IngestionContext,
        document: Document,
        receipt: dict[str, object],
        sha256: str,
        *,
        db_conn: Connection | None = None,
    ) -> None:
        """Emit the distinct signed terminal-exclusion audit event in the write tx."""
        is_v2 = receipt.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
        if is_v2:
            safe = {
                "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
                "variant": receipt["variant"],
                "receipt_schema_version": receipt["schema_version"],
                "page_count": receipt["page_count"],
                "classified_page_count": receipt["classified_page_count"],
                "substantive_span_count": receipt["substantive_span_count"],
                "whole_document_terminal": receipt["whole_document_terminal"],
            }
            integrity_hashes = [
                sha256,
                str(receipt["manifest_sha256"]),
                str(receipt["batch_envelope_sha256"]),
                str(receipt["candidate_set_sha256"]),
                str(receipt["classifier_policy_sha256"]),
                str(receipt["watermark_page_classifications_sha256"]),
            ]
        else:
            safe = {
                "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
                "variant": receipt["variant"],
                "receipt_schema_version": receipt["schema_version"],
                "parser_runtime": receipt["parser_runtime"],
                "page_count": receipt["page_count"],
                "span_count": 0,
            }
            integrity_hashes = [
                sha256,
                str(receipt["attestation_text_sha256"]),
                str(receipt["payload_sha256"]),
                str(receipt["envelope_sha256"]),
                str(receipt["public_key_sha256"]),
                str(receipt["ocr_result_sha256"]),
            ]
        event = build_canonical_audit_event(
            tenant_id=str(ctx.tenant_id),
            event_type="document.no_substantive_content.confirmed",
            severity="HIGH",
            summary=(
                "Document no-substantive-content state confirmed by signed reviewer attestation"
            ),
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            actor_roles=ctx.actor_roles,
            request_id=ctx.request_id,
            method="POST",
            path="/internal/ingestion/documents",
            status_code=201,
            resource_type="document",
            resource_id=str(document.document_id),
            deal_id=str(document.deal_id),
            idempotency_key_sha256=(
                hashlib.sha256(ctx.idempotency_key.encode("utf-8")).hexdigest()
                if ctx.idempotency_key
                else None
            ),
            safe=safe,
            hashes=integrity_hashes,
            refs=[f"deal_id:{document.deal_id}"],
        )
        self._emit_audit_event(event, db_conn=db_conn)

    def _emit_watermark_pages_classified(
        self,
        ctx: IngestionContext,
        document: Document,
        receipt: Mapping[str, object],
        sha256: str,
        *,
        db_conn: Connection | None = None,
    ) -> None:
        """Emit one text-free provenance event for every admitted watermark page."""

        classifications = receipt.get("watermark_page_classifications")
        if not isinstance(classifications, list):
            raise DocumentNoSubstantiveContentAttestationError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED
            )
        for classification in classifications:
            if not isinstance(classification, Mapping):
                raise DocumentNoSubstantiveContentAttestationError(
                    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED
                )
            event = build_canonical_audit_event(
                tenant_id=str(ctx.tenant_id),
                event_type="document.watermark_page.classified",
                severity="HIGH",
                summary="Document page classified as watermark-only",
                actor_id=ctx.actor_id,
                actor_type=ctx.actor_type,
                actor_roles=ctx.actor_roles,
                request_id=ctx.request_id,
                method="POST",
                path="/internal/ingestion/documents",
                status_code=201,
                resource_type="document",
                resource_id=str(document.document_id),
                deal_id=str(document.deal_id),
                idempotency_key_sha256=(
                    hashlib.sha256(ctx.idempotency_key.encode("utf-8")).hexdigest()
                    if ctx.idempotency_key
                    else None
                ),
                safe={
                    "classification": "watermark_only",
                    "page_number": classification["page_number"],
                    "classification_phase": classification.get("classification_phase", "post_ocr"),
                    "batch_membership": classification["batch_membership"],
                    "review_status": classification["review_status"],
                    "post_run_review_required": classification["post_run_review_required"],
                    "similarity_metric": classification["similarity_metric"],
                    "similarity_score": classification["similarity_score"],
                },
                hashes=[
                    sha256,
                    str(classification["canonical_render_sha256"]),
                    *(
                        [str(classification["ocr_terminal_proof_sha256"])]
                        if "ocr_terminal_proof_sha256" in classification
                        else [
                            str(classification["runtime_candidate_policy_sha256"]),
                            str(classification["signed_exemplar_policy_sha256"]),
                        ]
                    ),
                    str(receipt["manifest_sha256"]),
                    str(receipt["batch_envelope_sha256"]),
                ],
                refs=[f"deal_id:{document.deal_id}"],
            )
            self._emit_audit_event(event, db_conn=db_conn)

    def _emit_audit_event(
        self,
        event: dict[str, Any],
        *,
        db_conn: Connection | None,
    ) -> None:
        """Emit through the corpus-write transaction when the sink supports it."""
        effective_conn = db_conn or getattr(self, "_db_conn", None)
        emit_in_tx = getattr(self._audit_sink, "emit_in_tx", None)
        if effective_conn is not None and callable(emit_in_tx):
            emit_in_tx(effective_conn, event)
            return
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
