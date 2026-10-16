"""PostgreSQL-only Task 7 source graph freeze.

The ingestion path stages an ``OPEN/UNBOUND`` package graph.  This module owns
the sole short, ordered-lock transition to ``SEALED/BOUND``.  It deliberately
contains no parser, network, object-store, or filesystem calls: all source
bytes and parser receipts must already be durable before this boundary opens.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, NoReturn, Protocol, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from idis.models.data_room_package import DataRoomFileStatus
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.models.source_package_manifest import (
    SourcePackageManifest,
    SourcePackageManifestFile,
    canonical_source_manifest_sha256,
)
from idis.persistence.db import set_tenant_local
from idis.pipeline.task7_uuid import derive_task7_uuid
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    build_document_no_substantive_content_receipt_v2_verifier,
    build_document_no_substantive_content_receipt_verifier,
    project_safe_document_no_substantive_content_receipt,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine

TASK7_SOURCE_FREEZE_FENCED: Final = "TASK7_SOURCE_FREEZE_FENCED"
TASK7_SOURCE_TERMINAL_STATE_INVALID: Final = "TASK7_SOURCE_TERMINAL_STATE_INVALID"
TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE: Final = (
    "TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE"
)

LOCKED_SOURCE_QUERY_ORDER: Final = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
)
_ROOT_KIND_ORDER: Final = {
    "PACKAGE": 0,
    "PACKAGE_FILE": 1,
    "ARTIFACT": 2,
    "DOCUMENT": 3,
    "PARSE_ATTEMPT": 4,
    "SPAN": 5,
}
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_CELL_REF_RE: Final = re.compile(r"^[A-Z]{1,4}[1-9][0-9]*$")
_BLOCKED_SUPPORT: Final = frozenset(
    {
        DocumentSupportStatus.UNSUPPORTED,
        DocumentSupportStatus.ENCRYPTED,
        DocumentSupportStatus.CORRUPTED,
        DocumentSupportStatus.TOO_LARGE,
    }
)
_BLOCKED_TRIAGE: Final = frozenset(
    {
        DocumentTriageStatus.UNSUPPORTED_SOURCE,
        DocumentTriageStatus.BLOCKED,
        DocumentTriageStatus.TOO_LARGE,
    }
)
_SOURCE_SCHEMA_AUTHORITIES: Final = {
    (
        "ocr-terminal-evidence",
        "ocr-terminal-evidence-v5",
    ): "5e94193a427aa45530b450e2c3c2711580e7f52d3a56bc68c076fcfd21acf81f",
    (
        "pdf-page-disposition-census",
        "pdf-page-disposition-census-v1",
    ): "8897c621c5bddc43aafb6642978c6516708c11222ffcdf2a14c3346f47545576",
    (
        "ocr-render-admission",
        "ocr-render-admission-v2",
    ): "00339ad79fc75cb508413fe23e467c98ba0d69d439396780187a29192b2f4f23",
    (
        "ocr-page-execution-receipt",
        "ocr-page-execution-receipt-v1",
    ): "ab1e6191c29aa7613c572d4294d4e026d19e00cff8009341340d081b60daea67",
    (
        "image-only-page-receipt",
        "image-only-page-receipt-v2",
    ): "9d2b98bfab4916fafb0446afb4da008282222bbed455877d28bda4dd0e4ce135",
    (
        "watermark-pre-ocr-classification",
        "watermark-page-classification-v2",
    ): "935dc710f3efc9d0e96b8e625eec0b9a8c2365ecbe072c1638f2a70bab8c7154",
    (
        "no-substantive-content-receipt",
        "document-no-substantive-content-receipt-v1",
    ): "d706bf06bd6df5b6185a327297f70258bbca56bc723941a8e352ba9836f3d8d3",
    (
        "media-runtime-provenance",
        "faster-whisper-runtime-provenance-v2",
    ): "e620ac826d2e04e2dbf53a5e5dde0179133bf416fa1bbf9fd8699b495c9f5cc6",
    (
        "media-stt-receipt",
        "media-stt-receipt-v1",
    ): "41a6c94a8461e9057e6ac2144b8084f8c91a0b8d5d2ae2b6ea7503d2280b249c",
    (
        "media-frame-ocr-receipt",
        "media-frame-ocr-receipt-v1",
    ): "499643a5deb4f7c70ef4cabe0daf0255a73df34651b5dd059a41b2036199e2e3",
}


class Task7SourceFreezeError(RuntimeError):
    """Leakage-safe failure from the source-freeze boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Task 7 source freeze rejected")


@dataclass(frozen=True, slots=True)
class SourceFreezeObservation:
    """Package state observed before the caller can wait on the package lock."""

    tenant_id: str
    deal_id: str
    package_id: str
    observed_updated_at: datetime
    observed_source_manifest_sha256: str
    observed_status: str
    observed_source_binding_state: str


@dataclass(frozen=True, slots=True)
class SourceFreezeSnapshot:
    """Complete persisted package graph read in the reviewed relation order."""

    package: Mapping[str, Any]
    files: tuple[Mapping[str, Any], ...]
    artifacts: tuple[Mapping[str, Any], ...]
    documents: tuple[Mapping[str, Any], ...]
    attempts: tuple[Mapping[str, Any], ...]
    spans: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SourceLineageRoot:
    """One deterministic row for ``source_lineage_objects``."""

    source_lineage_object_id: str
    tenant_id: str
    deal_id: str
    corpus_receipt_id: str
    object_kind: str
    source_version: str
    source_disposition: str
    object_identity_sha256: str
    content_sha256: str
    corpus_receipt_sha256: str
    write_origin: str = "RUNTIME"

    def canonical_tuple(self) -> list[object]:
        """Return the exact safe scalar tuple used by the freeze-set receipt."""

        return [
            self.source_lineage_object_id,
            self.tenant_id,
            self.deal_id,
            self.corpus_receipt_id,
            self.object_kind,
            self.source_version,
            self.source_disposition,
            self.object_identity_sha256,
            self.content_sha256,
            self.corpus_receipt_sha256,
            self.write_origin,
        ]

    def as_row(self) -> dict[str, object]:
        """Return SQL parameter names for the physical root row."""

        return {
            "source_lineage_object_id": self.source_lineage_object_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "corpus_receipt_id": self.corpus_receipt_id,
            "object_kind": self.object_kind,
            "source_version": self.source_version,
            "source_disposition": self.source_disposition,
            "object_identity_sha256": self.object_identity_sha256,
            "content_sha256": self.content_sha256,
            "corpus_receipt_sha256": self.corpus_receipt_sha256,
            "write_origin": self.write_origin,
        }


@dataclass(frozen=True, slots=True)
class SourceFreezeBinding:
    """Expected native-row state after the atomic freeze."""

    table_name: str
    native_id: str
    object_kind: str
    root_id: str
    parse_attempt_id: str | None = None
    doc_id: str | None = None
    document_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceFreezePlan:
    """Deterministic roots and native mutations derived from one locked graph."""

    snapshot: SourceFreezeSnapshot
    tenant_id: str
    deal_id: str
    package_id: str
    manifest_sha256: str
    roots: tuple[SourceLineageRoot, ...]
    bindings: tuple[SourceFreezeBinding, ...]
    selected_parse_attempts: Mapping[str, str]
    root_set_sha256: str

    def expected_bound_snapshot(
        self,
        source: SourceFreezeSnapshot | None = None,
    ) -> SourceFreezeSnapshot:
        """Project only the approved nullable/state fields to their bound values."""

        current = source or self.snapshot
        roots = {(binding.table_name, binding.native_id): binding for binding in self.bindings}

        package_binding = roots[("data_room_packages", self.package_id)]
        package = {
            **current.package,
            "status": "SEALED",
            "source_binding_state": "BOUND",
            "source_lineage_object_id": package_binding.root_id,
            "source_corpus_receipt_id": self.package_id,
        }

        def bind_rows(
            table_name: str,
            rows: tuple[Mapping[str, Any], ...],
            key: str,
        ) -> tuple[Mapping[str, Any], ...]:
            bound: list[Mapping[str, Any]] = []
            for row in rows:
                native_id = _uuid_text(row.get(key))
                binding = roots[(table_name, native_id)]
                values: dict[str, Any] = {
                    **row,
                    "source_binding_state": "BOUND",
                    "source_lineage_object_id": binding.root_id,
                    "source_corpus_receipt_id": self.package_id,
                }
                if table_name == "document_spans":
                    values["parse_attempt_id"] = binding.parse_attempt_id
                if table_name == "data_room_package_files":
                    values["doc_id"] = binding.doc_id
                    values["document_id"] = binding.document_id
                bound.append(values)
            return tuple(bound)

        return SourceFreezeSnapshot(
            package=package,
            files=bind_rows("data_room_package_files", current.files, "file_entry_id"),
            artifacts=bind_rows("document_artifacts", current.artifacts, "doc_id"),
            documents=bind_rows("documents", current.documents, "document_id"),
            attempts=bind_rows(
                "document_parse_attempts",
                current.attempts,
                "parse_attempt_id",
            ),
            spans=bind_rows("document_spans", current.spans, "span_id"),
        )


@dataclass(frozen=True, slots=True)
class SourceFreezeReceipt:
    """Safe return value after exact reload verification."""

    package_id: str
    manifest_sha256: str
    root_set_sha256: str
    root_count: int
    file_count: int
    status: str = "SEALED"


class _SourceFreezeRepository(Protocol):
    def observe_source_package(
        self,
        *,
        deal_id: str,
        package_id: str,
    ) -> SourceFreezeObservation: ...

    def freeze_observed_package(
        self,
        observation: SourceFreezeObservation,
    ) -> object: ...


TerminalExclusionAuthority = (
    VerifiedDocumentNoSubstantiveContentAttestation
    | VerifiedDocumentNoSubstantiveContentAttestationManifest
)


class RepositoryFactory(Protocol):
    def __call__(
        self,
        connection: Connection,
        tenant_id: str,
        *,
        terminal_exclusion_authority: TerminalExclusionAuthority | None = None,
    ) -> _SourceFreezeRepository: ...


def _fail(code: str) -> NoReturn:
    raise Task7SourceFreezeError(code)


def _normalize_json(value: object) -> object:
    if isinstance(value, UUID):
        return str(value).lower()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = unicodedata.normalize("NFC", str(raw_key))
            if key in normalized:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            normalized[key] = _normalize_json(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        normalized_float = 0.0 if value == 0.0 else value
        return {"float8_be": struct.pack(">d", normalized_float).hex()}
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _normalize_json(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_text(value: object) -> str:
    if not isinstance(value, str):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _uuid_text(value: object) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _hash_text(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return value


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if not isinstance(value, Mapping):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return {str(key): item for key, item in value.items()}


def _json_sequence(value: object) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if not isinstance(value, list):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return value


def _utc_rfc3339(value: object) -> str:
    if not isinstance(value, datetime):
        _fail(TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE)
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _metadata_codes(metadata: Mapping[str, Any], field: str) -> list[str]:
    value = metadata.get(field)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or _SAFE_CODE_RE.fullmatch(item) is None for item in value)
        or value != sorted(set(value))
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return value


def _row_codes(value: object) -> list[str]:
    rows = _json_sequence(value)
    if any(not isinstance(item, str) or _SAFE_CODE_RE.fullmatch(item) is None for item in rows):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if rows != sorted(set(rows)):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return rows


def _support_status(value: object) -> DocumentSupportStatus:
    try:
        return DocumentSupportStatus(str(value))
    except ValueError:
        return DocumentSupportStatus.UNKNOWN


def _triage_status(value: object) -> DocumentTriageStatus:
    try:
        return DocumentTriageStatus(str(value))
    except ValueError:
        return DocumentTriageStatus.UNKNOWN


def _parse_status(value: object) -> ParseStatus:
    try:
        return ParseStatus(str(value))
    except ValueError:
        return ParseStatus.PENDING


def _project_file_status(
    support: DocumentSupportStatus,
    triage: DocumentTriageStatus,
    parse_status: ParseStatus,
) -> DataRoomFileStatus:
    if (
        support in _BLOCKED_SUPPORT
        or triage in _BLOCKED_TRIAGE
        or parse_status is ParseStatus.FAILED
    ):
        return DataRoomFileStatus.BLOCKED
    if (
        support is DocumentSupportStatus.SUPPORTED
        and triage is DocumentTriageStatus.READY
        and parse_status is ParseStatus.PARSED
    ):
        return DataRoomFileStatus.SUPPORTED
    return DataRoomFileStatus.DEFERRED


def _validate_typed_schema_payloads(
    *,
    doc_type: object,
    parse_status: object,
    metadata: Mapping[str, Any],
    spans: Sequence[Mapping[str, Any]],
    refs: Sequence[Mapping[str, Any]],
) -> None:
    payloads: dict[str, object] = {}
    for ref in refs:
        schema_id = str(ref["schema_id"])
        if schema_id in payloads:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        payloads[schema_id] = ref["validated_payload"]

    ocr_authorities = {"ocr-terminal-evidence"}
    pdf_authorities = {
        "pdf-page-disposition-census",
        "ocr-render-admission",
        "ocr-page-execution-receipt",
        "image-only-page-receipt",
        "watermark-pre-ocr-classification",
    }
    media_authorities = {
        "media-runtime-provenance",
        "media-stt-receipt",
        "media-frame-ocr-receipt",
    }
    if doc_type not in {"PDF", "IMAGE"} and ocr_authorities & set(payloads):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if doc_type != "PDF" and pdf_authorities & set(payloads):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if doc_type != "VIDEO" and media_authorities & set(payloads):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if doc_type != "PDF" and "no-substantive-content-receipt" in payloads:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if parse_status != "FAILED" and "no-substantive-content-receipt" in payloads:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    try:
        from idis.evaluation.real_example_terminal_reconciliation import (
            _safe_media_runtime_provenance,
            _validate_media_dual_track_receipts,
            _validate_pre_ocr_watermark_page_classification,
            _validate_safe_ocr_evidence,
            _validated_ocr_render_admission,
            _validated_pdf_page_census,
        )
        from idis.parsers.image_only_page import project_safe_image_only_page_receipts
        from idis.parsers.ocr import validate_ocr_page_execution_receipt_payload

        if "ocr-render-admission" in payloads:
            raw_admissions = payloads["ocr-render-admission"]
            if not isinstance(raw_admissions, list):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            admissions: list[Mapping[str, object]] = []
            pages: list[int] = []
            for raw_admission in raw_admissions:
                admission = _validated_ocr_render_admission(
                    raw_admission,
                    required_schema_version="ocr-render-admission-v2",
                )
                if admission is None:
                    _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
                page = admission.get("page")
                if type(page) is not int:
                    _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
                admissions.append(admission)
                pages.append(page)
            if (
                not admissions
                or pages != sorted(set(pages))
                or _normalize_json(admissions) != _normalize_json(raw_admissions)
            ):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        if "ocr-page-execution-receipt" in payloads:
            execution = validate_ocr_page_execution_receipt_payload(
                payloads["ocr-page-execution-receipt"]
            ).to_payload()
            if _normalize_json(execution) != _normalize_json(
                payloads["ocr-page-execution-receipt"]
            ):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        if "image-only-page-receipt" in payloads:
            raw_receipts = payloads["image-only-page-receipt"]
            projected = project_safe_image_only_page_receipts(raw_receipts)
            if not isinstance(raw_receipts, list) or projected is None or not projected:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            receipt_pages: list[int] = []
            for receipt in projected:
                page = receipt.get("page_number")
                if type(page) is not int:
                    _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
                receipt_pages.append(page)
            if receipt_pages != sorted(set(receipt_pages)) or _normalize_json(
                projected
            ) != _normalize_json(raw_receipts):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        if "watermark-pre-ocr-classification" in payloads:
            watermark_receipt = project_safe_document_no_substantive_content_receipt(
                metadata.get("document_no_substantive_content_receipt")
            )
            required_context = (
                "content_sha256",
                "classifier_policy_sha256",
                "exemplar_envelope_sha256",
                "exemplar_canonical_bitmap_sha256",
            )
            if watermark_receipt is None or any(
                not isinstance(watermark_receipt.get(key), str) for key in required_context
            ):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            classification, _exemplar = _validate_pre_ocr_watermark_page_classification(
                cast(
                    Mapping[str, Any],
                    payloads["watermark-pre-ocr-classification"],
                ),
                content_sha256=str(watermark_receipt["content_sha256"]),
                classifier_policy_sha256=str(watermark_receipt["classifier_policy_sha256"]),
                exemplar_envelope_sha256=str(watermark_receipt["exemplar_envelope_sha256"]),
                exemplar_bitmap_sha256=str(watermark_receipt["exemplar_canonical_bitmap_sha256"]),
            )
            if _normalize_json(classification) != _normalize_json(
                payloads["watermark-pre-ocr-classification"]
            ):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

        if doc_type == "PDF":
            required = {"ocr-terminal-evidence", "pdf-page-disposition-census"}
            present = required & set(payloads)
            if present and present != required:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            if parse_status == "PARSED" and present != required:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            if present:
                census = _validated_pdf_page_census(payloads["pdf-page-disposition-census"])
                ocr_evidence = payloads["ocr-terminal-evidence"]
                _validate_safe_ocr_evidence(
                    ocr_evidence,
                    extension="pdf",
                    durable_span_count=len(spans),
                    watermark_receipt=project_safe_document_no_substantive_content_receipt(
                        metadata.get("document_no_substantive_content_receipt")
                    ),
                )
                if (
                    not isinstance(ocr_evidence, Mapping)
                    or ocr_evidence.get("pdf_page_census") != census
                ):
                    _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        if doc_type == "IMAGE":
            required = {"ocr-terminal-evidence"}
            present = required & set(payloads)
            if parse_status == "PARSED" and present != required:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            if present:
                _validate_safe_ocr_evidence(
                    payloads["ocr-terminal-evidence"],
                    extension="png",
                    durable_span_count=len(spans),
                )
        if doc_type == "VIDEO":
            present = media_authorities & set(payloads)
            if present and present != media_authorities:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            if parse_status == "PARSED" and present != media_authorities:
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
            if present:
                runtime = _safe_media_runtime_provenance(payloads["media-runtime-provenance"])
                _validate_media_dual_track_receipts(
                    stt_receipt=payloads["media-stt-receipt"],
                    frame_receipt=payloads["media-frame-ocr-receipt"],
                    spans=spans,
                    runtime_provenance=runtime,
                )
    except Task7SourceFreezeError:
        raise
    except Exception:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _validate_schema_refs(
    metadata: Mapping[str, Any],
    *,
    doc_type: object,
    parse_status: object,
    spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    raw_refs = metadata.get("applicable_schema_refs", [])
    if not isinstance(raw_refs, list):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    normalized: list[dict[str, Any]] = []
    validated_refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_ref in raw_refs:
        ref = _json_mapping(raw_ref)
        if set(ref) != {
            "schema_id",
            "schema_version",
            "schema_sha256",
            "validated_payload",
        }:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        schema_id = ref.get("schema_id")
        schema_version = ref.get("schema_version")
        schema_sha256 = ref.get("schema_sha256")
        if (
            not isinstance(schema_id, str)
            or not isinstance(schema_version, str)
            or not isinstance(schema_sha256, str)
            or _SOURCE_SCHEMA_AUTHORITIES.get((schema_id, schema_version)) != schema_sha256
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        key = (schema_id, schema_version, schema_sha256)
        if key in seen:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        _canonical_json_bytes(ref["validated_payload"])
        seen.add(key)
        validated_refs.append(ref)
        normalized.append(
            {
                "schema_id": schema_id,
                "schema_version": schema_version,
                "schema_sha256": schema_sha256,
                "validated_payload": _normalize_json(ref["validated_payload"]),
            }
        )
    _validate_typed_schema_payloads(
        doc_type=doc_type,
        parse_status=parse_status,
        metadata=metadata,
        spans=spans,
        refs=validated_refs,
    )
    expected = sorted(
        normalized,
        key=lambda item: (
            item["schema_id"],
            item["schema_version"],
            item["schema_sha256"],
            _canonical_json_bytes(item["validated_payload"]),
        ),
    )
    if normalized != expected:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return normalized


def _terminal_document_projection(
    metadata: Mapping[str, Any],
    *,
    doc_type: object,
    parse_status: object,
    spans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "parser_support_status": _support_status(metadata.get("parser_support_status")).value,
        "parser_triage_status": _triage_status(metadata.get("parser_triage_status")).value,
        "applicable_schema_refs": _validate_schema_refs(
            metadata,
            doc_type=doc_type,
            parse_status=parse_status,
            spans=spans,
        ),
    }


def _durable_object_integrity(
    artifact: Mapping[str, Any],
    *,
    doc_id: str,
    content_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    metadata = _json_mapping(artifact.get("metadata"))
    value = _json_mapping(metadata.get("durable_object_integrity"))
    expected_fields = {
        "schema_version",
        "doc_id",
        "content_sha256",
        "size_bytes",
        "uri_sha256",
        "version_id",
        "backend",
        "streaming_verified",
    }
    uri = artifact.get("uri")
    version_id = _uuid_text(artifact.get("version_id"))
    normalized = {
        **value,
        "doc_id": _uuid_text(value.get("doc_id")),
        "version_id": _uuid_text(value.get("version_id")),
    }
    if (
        set(value) != expected_fields
        or normalized["schema_version"] != "durable-object-integrity-v1"
        or normalized["doc_id"] != doc_id
        or normalized["content_sha256"] != content_sha256
        or type(normalized["size_bytes"]) is not int
        or normalized["size_bytes"] != size_bytes
        or not isinstance(uri, str)
        or not uri
        or normalized["uri_sha256"] != _sha256_text(uri)
        or normalized["version_id"] != version_id
        or not isinstance(normalized["backend"], str)
        or not normalized["backend"].startswith("compliant:")
        or normalized["streaming_verified"] is not True
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    _hash_text(str(normalized["content_sha256"]))
    _hash_text(str(normalized["uri_sha256"]))
    return normalized


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and int(value) >= 0


def _positive_int(value: object) -> bool:
    return type(value) is int and int(value) > 0


def _validated_span_locator(
    span_type: object,
    raw_value: object,
    *,
    doc_type: object,
) -> dict[str, Any]:
    if not isinstance(span_type, str):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    document_type = str(doc_type)
    allowed_span_types = {
        "PDF": frozenset({"PAGE_TEXT"}),
        "IMAGE": frozenset({"PAGE_TEXT"}),
        "VIDEO": frozenset({"TIMECODE"}),
        "XLSX": frozenset({"CELL"}),
        "DOCX": frozenset({"PARAGRAPH", "CELL"}),
        "PPTX": frozenset({"PARAGRAPH", "CELL"}),
        "HTML": frozenset({"PARAGRAPH"}),
        "TEXT": frozenset({"PARAGRAPH"}),
    }
    if span_type not in allowed_span_types.get(document_type, frozenset()):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    value = _json_mapping(raw_value)
    fields = set(value)
    valid = False
    if span_type == "PARAGRAPH":
        if document_type == "DOCX":
            valid = fields == {"paragraph"} and _nonnegative_int(value.get("paragraph"))
        elif document_type == "PPTX":
            valid = fields == {"slide", "shape", "paragraph"} and all(
                _nonnegative_int(value.get(name)) for name in fields
            )
        elif document_type == "HTML":
            valid = (
                fields == {"node", "source"}
                and _positive_int(value.get("node"))
                and value.get("source") == "html"
            )
        elif document_type == "TEXT":
            valid = (
                fields == {"line", "source"}
                and _positive_int(value.get("line"))
                and value.get("source") == "text"
            )
    elif span_type == "CELL":
        if document_type == "DOCX":
            valid = fields == {"table", "row", "col"} and all(
                _nonnegative_int(value.get(name)) for name in fields
            )
        elif document_type == "XLSX":
            valid = (
                fields == {"sheet", "cell", "row", "col"}
                and isinstance(value.get("sheet"), str)
                and bool(str(value["sheet"]).strip())
                and isinstance(value.get("cell"), str)
                and _CELL_REF_RE.fullmatch(str(value["cell"])) is not None
                and _positive_int(value.get("row"))
                and _positive_int(value.get("col"))
            )
        elif document_type == "PPTX":
            valid = fields == {"slide", "table", "row", "col"} and all(
                _nonnegative_int(value.get(name)) for name in fields
            )
    elif span_type == "TIMECODE":
        base_fields = {"start_ms", "end_ms", "source"}
        frame_fields = base_fields | {"sample_index", "confidence"}
        start_ms = value.get("start_ms")
        end_ms = value.get("end_ms")
        source = value.get("source")
        valid = (
            frozenset(fields) in {frozenset(base_fields), frozenset(frame_fields)}
            and _nonnegative_int(start_ms)
            and _positive_int(end_ms)
            and cast(int, end_ms) > cast(int, start_ms)
            and source in {"media_transcript", "video_frame_ocr"}
        )
        if valid and fields == frame_fields:
            confidence = value.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                valid = False
            else:
                normalized_confidence = float(confidence)
                valid = (
                    source == "video_frame_ocr"
                    and _nonnegative_int(value.get("sample_index"))
                    and math.isfinite(normalized_confidence)
                    and 0.0 <= normalized_confidence <= 1.0
                )
                if valid:
                    value["confidence"] = normalized_confidence
    elif span_type == "PAGE_TEXT":
        extension = {"PDF": "pdf", "IMAGE": "png"}.get(document_type)
        if extension is None:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        try:
            from idis.evaluation.real_example_terminal_reconciliation import (
                _validated_raw_ocr_locator,
            )

            normalized, _is_ocr = _validated_raw_ocr_locator(
                value,
                extension=extension,
                span_type=span_type,
            )
        except Exception:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        value = normalized
        valid = True
    if not valid:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    _canonical_json_bytes(value)
    return value


def _validate_evidence_document(
    *,
    document: Mapping[str, Any],
    metadata: Mapping[str, Any],
    file_row: Mapping[str, Any],
    spans: tuple[Mapping[str, Any], ...],
) -> None:
    if (
        document.get("parse_status") != "PARSED"
        or metadata.get("parser_runtime_status") != "completed"
        or _metadata_codes(metadata, "parse_warning_codes")
        or _metadata_codes(metadata, "parse_error_codes")
        or _support_status(metadata.get("parser_support_status"))
        is not DocumentSupportStatus.SUPPORTED
        or _triage_status(metadata.get("parser_triage_status")) is not DocumentTriageStatus.READY
        or not spans
        or file_row.get("file_status") != "supported"
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    span_count = metadata.get("span_count")
    if span_count is not None and (type(span_count) is not int or span_count != len(spans)):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _validate_confirmed_exclusion(
    *,
    manifest: SourcePackageManifest,
    manifest_file: SourcePackageManifestFile,
    document: Mapping[str, Any],
    metadata: Mapping[str, Any],
    file_row: Mapping[str, Any],
    spans: tuple[Mapping[str, Any], ...],
    projection: Mapping[str, Any],
    terminal_exclusion_authority: TerminalExclusionAuthority | None,
) -> None:
    receipt = project_safe_document_no_substantive_content_receipt(
        metadata.get("document_no_substantive_content_receipt")
    )
    receipt_ref = next(
        (
            ref
            for ref in projection["applicable_schema_refs"]
            if ref["schema_id"] == "no-substantive-content-receipt"
        ),
        None,
    )
    whole_document_terminal = receipt is not None and (
        receipt.get("whole_document_terminal") is True
        or (
            receipt.get("schema_version") == "document-no-substantive-content-receipt-v1"
            and receipt.get("span_count") == 0
            and receipt.get("accepted_text_page_count") == 0
        )
    )
    if (
        document.get("parse_status") != "FAILED"
        or spans
        or file_row.get("file_status") != "blocked"
        or receipt is None
        or receipt.get("disposition") != DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
        or not whole_document_terminal
        or receipt.get("safe_alias") != manifest_file.generated_upload_name
        or receipt.get("content_sha256") != manifest_file.content_sha256
        or receipt.get("source_tree_fingerprint") != manifest.source_tree_fingerprint
        or receipt_ref is None
        or receipt_ref["validated_payload"] != _normalize_json(receipt)
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if isinstance(
        terminal_exclusion_authority,
        VerifiedDocumentNoSubstantiveContentAttestation,
    ):
        terminal_exclusion_verifier = build_document_no_substantive_content_receipt_verifier(
            terminal_exclusion_authority
        )
    elif isinstance(
        terminal_exclusion_authority,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        terminal_exclusion_verifier = build_document_no_substantive_content_receipt_v2_verifier(
            terminal_exclusion_authority
        )
    else:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    verification_input = {
        "tenant_id": _uuid_text(document.get("tenant_id")),
        "document_name": manifest_file.generated_upload_name,
        "sha256": manifest_file.content_sha256,
        "metadata": {
            "document_no_substantive_content_receipt": receipt,
        },
        "source_metadata": {
            "upload_filename": manifest_file.generated_upload_name,
            "source_tree_fingerprint": manifest.source_tree_fingerprint,
        },
    }
    if terminal_exclusion_verifier(verification_input) is not True:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _validate_span(
    span: Mapping[str, Any],
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str,
    doc_type: object,
) -> tuple[str, dict[str, Any]]:
    span_id = _uuid_text(span.get("span_id"))
    text_excerpt = span.get("text_excerpt")
    if (
        _uuid_text(span.get("tenant_id")) != tenant_id
        or _uuid_text(span.get("deal_id")) != deal_id
        or _uuid_text(span.get("document_id")) != document_id
        or not isinstance(text_excerpt, str)
        or not text_excerpt.strip()
        or _hash_text(span.get("content_hash"))
        != hashlib.sha256(text_excerpt.encode("utf-8")).hexdigest()
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    locator = _validated_span_locator(
        span.get("span_type"),
        span.get("locator"),
        doc_type=doc_type,
    )
    return span_id, locator


def _source_root(
    *,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    object_kind: str,
    native_scoped_id: Sequence[str],
    source_version: str,
    source_disposition: str,
    object_identity_sha256: str,
    content_sha256: str,
    manifest_sha256: str,
) -> SourceLineageRoot:
    values = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "corpus_receipt_id": package_id,
        "object_kind": object_kind,
        "native_scoped_id": list(native_scoped_id),
        "source_version": source_version,
        "source_disposition": source_disposition,
        "object_identity_sha256": _hash_text(object_identity_sha256),
        "content_sha256": _hash_text(content_sha256),
        "corpus_receipt_sha256": _hash_text(manifest_sha256),
    }
    root_id = derive_task7_uuid(
        id_kind="source_lineage_object_id",
        values=values,
    ).value
    return SourceLineageRoot(
        source_lineage_object_id=str(root_id),
        tenant_id=tenant_id,
        deal_id=deal_id,
        corpus_receipt_id=package_id,
        object_kind=object_kind,
        source_version=source_version,
        source_disposition=source_disposition,
        object_identity_sha256=object_identity_sha256,
        content_sha256=content_sha256,
        corpus_receipt_sha256=manifest_sha256,
    )


def _manifest_from_package(package: Mapping[str, Any]) -> SourcePackageManifest:
    try:
        metadata = _json_mapping(package.get("metadata"))
        manifest = SourcePackageManifest.model_validate(metadata.get("source_manifest"))
        digest = canonical_source_manifest_sha256(manifest)
    except (Task7SourceFreezeError, ValidationError, TypeError, ValueError):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    if manifest.manifest_sha256 != digest:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return manifest


def _scope_row(
    row: Mapping[str, Any],
    *,
    tenant_id: str,
    deal_id: str,
) -> None:
    if _uuid_text(row.get("tenant_id")) != tenant_id or _uuid_text(row.get("deal_id")) != deal_id:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _unique_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identifier = _uuid_text(row.get(key))
        if identifier in result:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        result[identifier] = row
    return result


def _selected_attempt(
    attempts: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str,
    disposition: str,
    content_sha256: str,
    receipt_sha256: str,
) -> Mapping[str, Any]:
    candidates: list[Mapping[str, Any]] = []
    expected_status = "PARSED" if disposition == "EVIDENCE_BEARING" else "FAILED"
    for attempt in attempts:
        try:
            attempt_document_id = _uuid_text(attempt.get("document_id"))
        except Task7SourceFreezeError:
            _fail(TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE)
        if attempt_document_id != document_id:
            continue
        try:
            _scope_row(attempt, tenant_id=tenant_id, deal_id=deal_id)
            attempt_id = _uuid_text(attempt.get("parse_attempt_id"))
            ordinal = attempt.get("attempt_ordinal")
            parser_name = attempt.get("parser_name")
            parser_version = attempt.get("parser_version")
            started_at = attempt.get("started_at")
            finished_at = attempt.get("finished_at")
            valid = (
                _positive_int(ordinal)
                and isinstance(parser_name, str)
                and bool(parser_name.strip())
                and isinstance(parser_version, str)
                and bool(parser_version.strip())
                and attempt.get("input_sha256") == content_sha256
                and attempt.get("receipt_sha256") == receipt_sha256
                and attempt.get("status") == expected_status
                and attempt.get("write_origin") == "RUNTIME"
                and isinstance(started_at, datetime)
                and isinstance(finished_at, datetime)
                and finished_at >= started_at
            )
            if valid:
                _hash_text(attempt.get("input_sha256"))
                _hash_text(attempt.get("receipt_sha256"))
                candidates.append({**attempt, "parse_attempt_id": attempt_id})
        except (Task7SourceFreezeError, TypeError):
            continue
    document_attempts = [row for row in attempts if str(row.get("document_id")) == document_id]
    if len(candidates) != 1 or len(document_attempts) != 1:
        _fail(TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE)
    return candidates[0]


def _root_and_binding_for_file(
    *,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    manifest: SourcePackageManifest,
    manifest_file: SourcePackageManifestFile,
    file_row: Mapping[str, Any],
    artifact: Mapping[str, Any],
    document: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    document_spans: tuple[Mapping[str, Any], ...],
    terminal_exclusion_authority: TerminalExclusionAuthority | None,
) -> tuple[
    list[SourceLineageRoot],
    list[SourceFreezeBinding],
    str,
    list[object],
]:
    file_entry_id = _uuid_text(file_row.get("file_entry_id"))
    doc_id = str(manifest_file.doc_id)
    document_id = str(manifest_file.document_id)
    file_binding_state = file_row.get("source_binding_state")
    file_document_pair_valid = (
        file_binding_state == "UNBOUND"
        and file_row.get("doc_id") is None
        and file_row.get("document_id") is None
        or file_binding_state == "BOUND"
        and _uuid_text(file_row.get("doc_id")) == doc_id
        and _uuid_text(file_row.get("document_id")) == document_id
    )
    _scope_row(file_row, tenant_id=tenant_id, deal_id=deal_id)
    _scope_row(artifact, tenant_id=tenant_id, deal_id=deal_id)
    _scope_row(document, tenant_id=tenant_id, deal_id=deal_id)
    if (
        _uuid_text(file_row.get("package_id")) != package_id
        or file_row.get("sequence") != manifest_file.sequence
        or file_row.get("path_hash") != manifest_file.source_locator_sha256
        or file_row.get("extension") != manifest_file.extension
        or file_row.get("sha256") != manifest_file.content_sha256
        or not file_document_pair_valid
        or _uuid_text(artifact.get("doc_id")) != doc_id
        or _uuid_text(document.get("document_id")) != document_id
        or _uuid_text(document.get("doc_id")) != doc_id
        or artifact.get("sha256") != manifest_file.content_sha256
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    metadata = _json_mapping(document.get("metadata"))
    projection = _terminal_document_projection(
        metadata,
        doc_type=document.get("doc_type"),
        parse_status=document.get("parse_status"),
        spans=document_spans,
    )
    support = _support_status(metadata.get("parser_support_status"))
    triage = _triage_status(metadata.get("parser_triage_status"))
    parse = _parse_status(document.get("parse_status"))
    reason_codes = _metadata_codes(metadata, "parser_reason_codes")
    error_codes = _metadata_codes(metadata, "parse_error_codes")
    _metadata_codes(metadata, "parse_warning_codes")
    if (
        file_row.get("support_status") != support.value
        or file_row.get("triage_status") != triage.value
        or file_row.get("parse_status") != parse.value
        or file_row.get("file_status") != _project_file_status(support, triage, parse).value
        or _row_codes(file_row.get("reason_codes")) != reason_codes
        or _row_codes(file_row.get("error_codes")) != error_codes
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    terminal_receipt_sha256 = _sha256_json(
        [
            "terminal-document-metadata-v2",
            document.get("doc_type"),
            document.get("parse_status"),
            projection,
        ]
    )
    if parse is ParseStatus.PARSED:
        disposition = "EVIDENCE_BEARING"
        _validate_evidence_document(
            document=document,
            metadata=metadata,
            file_row=file_row,
            spans=document_spans,
        )
    elif parse is ParseStatus.FAILED:
        disposition = "CONFIRMED_EXCLUSION"
        _validate_confirmed_exclusion(
            manifest=manifest,
            manifest_file=manifest_file,
            document=document,
            metadata=metadata,
            file_row=file_row,
            spans=document_spans,
            projection=projection,
            terminal_exclusion_authority=terminal_exclusion_authority,
        )
    else:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    integrity = _durable_object_integrity(
        artifact,
        doc_id=doc_id,
        content_sha256=manifest_file.content_sha256,
        size_bytes=manifest_file.size_bytes,
    )
    attempt = _selected_attempt(
        attempts,
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_id=document_id,
        disposition=disposition,
        content_sha256=manifest_file.content_sha256,
        receipt_sha256=terminal_receipt_sha256,
    )
    parse_attempt_id = _uuid_text(attempt.get("parse_attempt_id"))

    roots: list[SourceLineageRoot] = []
    bindings: list[SourceFreezeBinding] = []
    for span in document_spans:
        if disposition != "EVIDENCE_BEARING":
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        span_id, locator = _validate_span(
            span,
            tenant_id=tenant_id,
            deal_id=deal_id,
            document_id=document_id,
            doc_type=document.get("doc_type"),
        )
        locator_sha256 = _sha256_json(["span-locator-v1", span.get("span_type"), locator])
        span_identity_sha256 = _sha256_json(
            [
                "document-span-identity-v1",
                span_id,
                document_id,
                parse_attempt_id,
                span.get("span_type"),
                locator_sha256,
                span.get("content_hash"),
            ]
        )
        span_root = _source_root(
            tenant_id=tenant_id,
            deal_id=deal_id,
            package_id=package_id,
            object_kind="SPAN",
            native_scoped_id=[tenant_id, deal_id, document_id, span_id],
            source_version=(f"document-span-v1:{parse_attempt_id}:{span.get('span_type')}"),
            source_disposition="EVIDENCE_BEARING",
            object_identity_sha256=span_identity_sha256,
            content_sha256=_hash_text(span.get("content_hash")),
            manifest_sha256=manifest.manifest_sha256,
        )
        roots.append(span_root)
        bindings.append(
            SourceFreezeBinding(
                table_name="document_spans",
                native_id=span_id,
                object_kind="SPAN",
                root_id=span_root.source_lineage_object_id,
                parse_attempt_id=parse_attempt_id,
            )
        )

    artifact_metadata_sha256 = _sha256_json(
        [
            "document-artifact-metadata-v2",
            _sha256_text(artifact.get("title")),
            artifact.get("source_system"),
            artifact.get("doc_type"),
            _uuid_text(artifact.get("version_id")),
            integrity,
        ]
    )
    artifact_identity_sha256 = _sha256_json(
        [
            "document-artifact-identity-v1",
            doc_id,
            artifact.get("doc_type"),
            artifact.get("source_system"),
            _uuid_text(artifact.get("version_id")),
            _utc_rfc3339(artifact.get("ingested_at")),
            manifest_file.content_sha256,
            _sha256_text(artifact.get("uri")),
            artifact_metadata_sha256,
        ]
    )
    artifact_root = _source_root(
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        object_kind="ARTIFACT",
        native_scoped_id=[tenant_id, deal_id, doc_id],
        source_version=f"document-artifact-v1:{_uuid_text(artifact.get('version_id'))}",
        source_disposition=disposition,
        object_identity_sha256=artifact_identity_sha256,
        content_sha256=manifest_file.content_sha256,
        manifest_sha256=manifest.manifest_sha256,
    )
    document_identity_sha256 = _sha256_json(
        [
            "parsed-document-identity-v1",
            document_id,
            doc_id,
            document.get("doc_type"),
            document.get("parse_status"),
            terminal_receipt_sha256,
            parse_attempt_id,
            attempt.get("receipt_sha256"),
        ]
    )
    document_root = _source_root(
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        object_kind="DOCUMENT",
        native_scoped_id=[tenant_id, deal_id, document_id],
        source_version=f"parsed-document-v1:{terminal_receipt_sha256}",
        source_disposition=disposition,
        object_identity_sha256=document_identity_sha256,
        content_sha256=manifest_file.content_sha256,
        manifest_sha256=manifest.manifest_sha256,
    )
    parse_attempt_identity_sha256 = _sha256_json(
        [
            "parse-attempt-identity-v1",
            parse_attempt_id,
            document_id,
            attempt.get("attempt_ordinal"),
            attempt.get("parser_name"),
            attempt.get("parser_version"),
            attempt.get("input_sha256"),
            attempt.get("receipt_sha256"),
            attempt.get("status"),
            _utc_rfc3339(attempt.get("started_at")),
            _utc_rfc3339(attempt.get("finished_at")),
        ]
    )
    parse_attempt_root = _source_root(
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        object_kind="PARSE_ATTEMPT",
        native_scoped_id=[tenant_id, deal_id, document_id, parse_attempt_id],
        source_version=(
            f"parser-attempt-v1:{attempt.get('parser_name')}:{attempt.get('parser_version')}"
        ),
        source_disposition=disposition,
        object_identity_sha256=parse_attempt_identity_sha256,
        content_sha256=manifest_file.content_sha256,
        manifest_sha256=manifest.manifest_sha256,
    )
    storage_uri = file_row.get("storage_uri")
    if storage_uri is not None and not isinstance(storage_uri, str):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    storage_uri_sha256 = None if storage_uri is None else _sha256_text(storage_uri)
    file_identity_sha256 = _sha256_json(
        [
            "source-package-file-identity-v1",
            package_id,
            file_entry_id,
            manifest_file.sequence,
            manifest_file.source_locator_sha256,
            manifest_file.extension,
            manifest_file.content_sha256,
            manifest_file.size_bytes,
            str(manifest_file.document_id),
            str(manifest_file.doc_id),
            file_row.get("file_status"),
            file_row.get("support_status"),
            file_row.get("triage_status"),
            file_row.get("parse_status"),
            reason_codes,
            error_codes,
            storage_uri_sha256,
            disposition,
            terminal_receipt_sha256,
        ]
    )
    file_root = _source_root(
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        object_kind="PACKAGE_FILE",
        native_scoped_id=[tenant_id, deal_id, package_id, file_entry_id],
        source_version=(
            f"source-package-file-v1:{manifest.manifest_sha256}:{manifest_file.sequence}"
        ),
        source_disposition=disposition,
        object_identity_sha256=file_identity_sha256,
        content_sha256=manifest_file.content_sha256,
        manifest_sha256=manifest.manifest_sha256,
    )
    roots.extend((file_root, artifact_root, document_root, parse_attempt_root))
    bindings.extend(
        (
            SourceFreezeBinding(
                "data_room_package_files",
                file_entry_id,
                "PACKAGE_FILE",
                file_root.source_lineage_object_id,
                doc_id=doc_id,
                document_id=document_id,
            ),
            SourceFreezeBinding(
                "document_artifacts",
                doc_id,
                "ARTIFACT",
                artifact_root.source_lineage_object_id,
            ),
            SourceFreezeBinding(
                "documents",
                document_id,
                "DOCUMENT",
                document_root.source_lineage_object_id,
            ),
            SourceFreezeBinding(
                "document_parse_attempts",
                parse_attempt_id,
                "PARSE_ATTEMPT",
                parse_attempt_root.source_lineage_object_id,
            ),
        )
    )
    package_member = [
        manifest_file.sequence,
        manifest_file.source_locator_sha256,
        manifest_file.content_sha256,
        manifest_file.size_bytes,
        manifest_file.extension,
        str(manifest_file.document_id),
        str(manifest_file.doc_id),
    ]
    return roots, bindings, parse_attempt_id, package_member


def build_source_freeze_plan(
    snapshot: SourceFreezeSnapshot,
    *,
    terminal_exclusion_authority: TerminalExclusionAuthority | None = None,
) -> SourceFreezePlan:
    """Derive the complete source root graph from already-durable locked rows."""

    package = snapshot.package
    tenant_id = _uuid_text(package.get("tenant_id"))
    deal_id = _uuid_text(package.get("deal_id"))
    package_id = _uuid_text(package.get("package_id"))
    manifest = _manifest_from_package(package)
    if len(snapshot.files) != manifest.file_count:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    files_by_sequence: dict[int, Mapping[str, Any]] = {}
    for row in snapshot.files:
        sequence = row.get("sequence")
        if type(sequence) is not int or sequence in files_by_sequence:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        files_by_sequence[sequence] = row
    if set(files_by_sequence) != set(range(manifest.file_count)):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    artifacts = _unique_rows(snapshot.artifacts, key="doc_id")
    documents = _unique_rows(snapshot.documents, key="document_id")
    try:
        _unique_rows(snapshot.attempts, key="parse_attempt_id")
    except Task7SourceFreezeError:
        _fail(TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE)
    _unique_rows(snapshot.spans, key="span_id")
    spans_by_document: dict[str, list[Mapping[str, Any]]] = {}
    for span in snapshot.spans:
        spans_by_document.setdefault(
            _uuid_text(span.get("document_id")),
            [],
        ).append(span)

    roots: list[SourceLineageRoot] = []
    bindings: list[SourceFreezeBinding] = []
    selected_attempts: dict[str, str] = {}
    package_members: list[object] = []
    consumed_artifacts: set[str] = set()
    consumed_documents: set[str] = set()
    consumed_attempts: set[str] = set()
    consumed_spans: set[str] = set()
    for manifest_file in manifest.files:
        file_row = files_by_sequence[manifest_file.sequence]
        doc_id = str(manifest_file.doc_id)
        document_id = str(manifest_file.document_id)
        artifact = artifacts.get(doc_id)
        document = documents.get(document_id)
        if artifact is None or document is None:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        document_spans = tuple(
            sorted(
                spans_by_document.get(document_id, []),
                key=lambda row: _uuid_text(row.get("span_id")),
            )
        )
        member_roots, member_bindings, attempt_id, package_member = _root_and_binding_for_file(
            tenant_id=tenant_id,
            deal_id=deal_id,
            package_id=package_id,
            manifest=manifest,
            manifest_file=manifest_file,
            file_row=file_row,
            artifact=artifact,
            document=document,
            attempts=snapshot.attempts,
            document_spans=document_spans,
            terminal_exclusion_authority=terminal_exclusion_authority,
        )
        roots.extend(member_roots)
        bindings.extend(member_bindings)
        selected_attempts[document_id] = attempt_id
        package_members.append(package_member)
        consumed_artifacts.add(doc_id)
        consumed_documents.add(document_id)
        consumed_attempts.add(attempt_id)
        consumed_spans.update(_uuid_text(row.get("span_id")) for row in document_spans)

    if (
        consumed_artifacts != set(artifacts)
        or consumed_documents != set(documents)
        or consumed_attempts
        != {_uuid_text(row.get("parse_attempt_id")) for row in snapshot.attempts}
        or consumed_spans != {_uuid_text(row.get("span_id")) for row in snapshot.spans}
    ):
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    package_identity_sha256 = _sha256_json(
        [
            "source-package-identity-v1",
            package_id,
            manifest.schema_version,
            manifest.manifest_sha256,
            manifest.source_tree_fingerprint,
            manifest.file_count,
            manifest.total_bytes,
            package_members,
        ]
    )
    package_root = _source_root(
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        object_kind="PACKAGE",
        native_scoped_id=[tenant_id, deal_id, package_id],
        source_version=f"source-package-manifest-v1:{manifest.manifest_sha256}",
        source_disposition="CORPUS",
        object_identity_sha256=package_identity_sha256,
        content_sha256=manifest.manifest_sha256,
        manifest_sha256=manifest.manifest_sha256,
    )
    roots.append(package_root)
    bindings.append(
        SourceFreezeBinding(
            "data_room_packages",
            package_id,
            "PACKAGE",
            package_root.source_lineage_object_id,
        )
    )
    ordered_roots = tuple(
        sorted(
            roots,
            key=lambda root: (
                _ROOT_KIND_ORDER[root.object_kind],
                root.source_lineage_object_id,
            ),
        )
    )
    ordered_bindings = tuple(
        sorted(
            bindings,
            key=lambda binding: (
                _ROOT_KIND_ORDER[binding.object_kind],
                binding.native_id,
            ),
        )
    )
    root_set_sha256 = _sha256_json(
        [
            "task7-source-freeze-root-set-v1",
            [root.canonical_tuple() for root in ordered_roots],
        ]
    )
    return SourceFreezePlan(
        snapshot=snapshot,
        tenant_id=tenant_id,
        deal_id=deal_id,
        package_id=package_id,
        manifest_sha256=manifest.manifest_sha256,
        roots=ordered_roots,
        bindings=ordered_bindings,
        selected_parse_attempts=dict(sorted(selected_attempts.items())),
        root_set_sha256=root_set_sha256,
    )


def _same_instant(left: object, right: object) -> bool:
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    left_aware = left if left.tzinfo is not None else left.replace(tzinfo=UTC)
    right_aware = right if right.tzinfo is not None else right.replace(tzinfo=UTC)
    return left_aware.astimezone(UTC) == right_aware.astimezone(UTC)


def _validate_unbound_children(snapshot: SourceFreezeSnapshot) -> None:
    for row in snapshot.files:
        if (
            row.get("source_binding_state") != "UNBOUND"
            or row.get("source_lineage_object_id") is not None
            or _uuid_text(row.get("source_corpus_receipt_id")) != _uuid_text(row.get("package_id"))
            or row.get("doc_id") is not None
            or row.get("document_id") is not None
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    for rows in (snapshot.artifacts, snapshot.documents, snapshot.attempts):
        for row in rows:
            if (
                row.get("source_binding_state") != "UNBOUND"
                or row.get("source_lineage_object_id") is not None
                or row.get("source_corpus_receipt_id") is not None
            ):
                _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    for row in snapshot.spans:
        if (
            row.get("source_binding_state") != "UNBOUND"
            or row.get("source_lineage_object_id") is not None
            or row.get("source_corpus_receipt_id") is not None
            or row.get("parse_attempt_id") is not None
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


def _validate_locked_package_fence(
    observation: SourceFreezeObservation,
    package: Mapping[str, Any],
) -> SourcePackageManifest:
    """Fence a stale package immediately after its row lock is acquired."""

    if (
        _uuid_text(package.get("tenant_id")) != _uuid_text(observation.tenant_id)
        or _uuid_text(package.get("deal_id")) != _uuid_text(observation.deal_id)
        or _uuid_text(package.get("package_id")) != _uuid_text(observation.package_id)
        or not _same_instant(package.get("updated_at"), observation.observed_updated_at)
    ):
        _fail(TASK7_SOURCE_FREEZE_FENCED)
    try:
        locked_manifest = _manifest_from_package(package)
    except Task7SourceFreezeError:
        _fail(TASK7_SOURCE_FREEZE_FENCED)
    if locked_manifest.manifest_sha256 != observation.observed_source_manifest_sha256:
        _fail(TASK7_SOURCE_FREEZE_FENCED)

    package_state = (
        package.get("status"),
        package.get("source_binding_state"),
    )
    observed_state = (
        observation.observed_status,
        observation.observed_source_binding_state,
    )
    if package_state != observed_state:
        _fail(TASK7_SOURCE_FREEZE_FENCED)
    if package_state not in {("OPEN", "UNBOUND"), ("SEALED", "BOUND")}:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    return locked_manifest


def validate_locked_source_snapshot(
    observation: SourceFreezeObservation,
    snapshot: SourceFreezeSnapshot,
) -> None:
    """Apply the package-row fence and checked staging-state contract."""

    package = snapshot.package
    _validate_locked_package_fence(observation, package)
    package_state = (
        package.get("status"),
        package.get("source_binding_state"),
    )
    if package_state == ("OPEN", "UNBOUND"):
        if package.get("source_lineage_object_id") is not None:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        _validate_unbound_children(snapshot)
        return


def _normalize_equality_value(value: object) -> object:
    if isinstance(value, datetime):
        return _utc_rfc3339(value)
    return _normalize_json(value)


def _normalized_row(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        str(key): _normalize_equality_value(value)
        for key, value in sorted(row.items(), key=lambda item: str(item[0]))
    }


def _normalized_snapshot(snapshot: SourceFreezeSnapshot) -> dict[str, object]:
    return {
        "package": _normalized_row(snapshot.package),
        "files": [_normalized_row(row) for row in snapshot.files],
        "artifacts": [_normalized_row(row) for row in snapshot.artifacts],
        "documents": [_normalized_row(row) for row in snapshot.documents],
        "attempts": [_normalized_row(row) for row in snapshot.attempts],
        "spans": [_normalized_row(row) for row in snapshot.spans],
    }


def _normalized_root_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    expected_fields = {
        "source_lineage_object_id",
        "tenant_id",
        "deal_id",
        "corpus_receipt_id",
        "object_kind",
        "source_version",
        "source_disposition",
        "object_identity_sha256",
        "content_sha256",
        "corpus_receipt_sha256",
        "write_origin",
    }
    normalized: list[dict[str, object]] = []
    for row in rows:
        if set(row) != expected_fields:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        normalized.append(_normalized_row(row))
    normalized.sort(
        key=lambda row: (
            _ROOT_KIND_ORDER.get(str(row["object_kind"]), 999),
            str(row["source_lineage_object_id"]),
        )
    )
    return normalized


def validate_reloaded_source_freeze(
    plan: SourceFreezePlan,
    snapshot: SourceFreezeSnapshot,
    root_rows: Sequence[Mapping[str, Any]],
    *,
    terminal_exclusion_authority: TerminalExclusionAuthority | None = None,
) -> None:
    """Require exact native/root equality after a write or idempotent retry."""

    try:
        expected_snapshot = plan.expected_bound_snapshot()
        if _normalized_snapshot(snapshot) != _normalized_snapshot(expected_snapshot):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        rederived = build_source_freeze_plan(
            snapshot,
            terminal_exclusion_authority=terminal_exclusion_authority,
        )
        if (
            rederived.manifest_sha256 != plan.manifest_sha256
            or rederived.root_set_sha256 != plan.root_set_sha256
            or tuple(root.as_row() for root in rederived.roots)
            != tuple(root.as_row() for root in plan.roots)
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        expected_roots = _normalized_root_rows(tuple(root.as_row() for root in plan.roots))
        if _normalized_root_rows(root_rows) != expected_roots:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
    except Task7SourceFreezeError:
        raise
    except Exception:
        _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)


_PACKAGE_COLUMNS: Final = """
    package_id, tenant_id, deal_id, status, metadata, updated_at,
    source_binding_state, source_lineage_object_id, source_corpus_receipt_id
"""
_FILE_COLUMNS: Final = """
    file_entry_id, tenant_id, package_id, deal_id, sequence, path_hash,
    extension, sha256, file_status, support_status, triage_status,
    parse_status, reason_codes, error_codes, doc_id, document_id, storage_uri,
    source_binding_state, source_lineage_object_id, source_corpus_receipt_id
"""
_ARTIFACT_COLUMNS: Final = """
    doc_id, tenant_id, deal_id, doc_type, title, source_system, version_id,
    ingested_at, sha256, uri, metadata, source_binding_state,
    source_lineage_object_id, source_corpus_receipt_id
"""
_DOCUMENT_COLUMNS: Final = """
    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status, metadata,
    created_at, updated_at, source_binding_state, source_lineage_object_id,
    source_corpus_receipt_id
"""
_ATTEMPT_COLUMNS: Final = """
    parse_attempt_id, tenant_id, deal_id, document_id, attempt_ordinal,
    parser_name, parser_version, input_sha256, receipt_sha256, status,
    started_at, finished_at, source_binding_state, source_lineage_object_id,
    source_corpus_receipt_id, write_origin
"""
_SPAN_COLUMNS: Final = """
    span_id, tenant_id, deal_id, document_id, span_type, locator, text_excerpt,
    content_hash, parse_attempt_id, source_binding_state,
    source_lineage_object_id, source_corpus_receipt_id
"""
_ROOT_COLUMNS: Final = """
    source_lineage_object_id, tenant_id, deal_id, corpus_receipt_id,
    object_kind, source_version, source_disposition, object_identity_sha256,
    content_sha256, corpus_receipt_sha256, write_origin
"""


def _mapping_rows(result: Any) -> tuple[Mapping[str, Any], ...]:
    mappings = result.mappings()
    return tuple(dict(row) for row in mappings.all())


class PostgresTask7SourceFreezeRepository:
    """Tenant-scoped PostgreSQL implementation with no fallback repository."""

    def __init__(
        self,
        connection: Connection,
        tenant_id: str,
        *,
        terminal_exclusion_authority: TerminalExclusionAuthority | None = None,
    ) -> None:
        self._connection = connection
        self._tenant_id = _uuid_text(tenant_id)
        self._terminal_exclusion_authority = terminal_exclusion_authority
        set_tenant_local(connection, self._tenant_id)

    def observe_source_package(
        self,
        *,
        deal_id: str,
        package_id: str,
    ) -> SourceFreezeObservation:
        """Read the manifest fence before any package-lock wait occurs."""

        deal = _uuid_text(deal_id)
        package = _uuid_text(package_id)
        rows = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_PACKAGE_COLUMNS}
                    FROM data_room_packages
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND package_id=CAST(:package_id AS uuid)
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": deal,
                    "package_id": package,
                },
            )
        )
        if len(rows) != 1:
            _fail(TASK7_SOURCE_FREEZE_FENCED)
        row = rows[0]
        manifest = _manifest_from_package(row)
        updated_at = row.get("updated_at")
        if not isinstance(updated_at, datetime):
            _fail(TASK7_SOURCE_FREEZE_FENCED)
        status = row.get("status")
        binding_state = row.get("source_binding_state")
        if (status, binding_state) not in {
            ("OPEN", "UNBOUND"),
            ("SEALED", "BOUND"),
        }:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        return SourceFreezeObservation(
            tenant_id=self._tenant_id,
            deal_id=deal,
            package_id=package,
            observed_updated_at=updated_at,
            observed_source_manifest_sha256=manifest.manifest_sha256,
            observed_status=str(status),
            observed_source_binding_state=str(binding_state),
        )

    def lock_source_graph(
        self,
        observation: SourceFreezeObservation,
    ) -> SourceFreezeSnapshot:
        """Lock all six source relations in the reviewed deterministic order."""

        params = {
            "tenant_id": self._tenant_id,
            "deal_id": _uuid_text(observation.deal_id),
            "package_id": _uuid_text(observation.package_id),
        }
        packages = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_PACKAGE_COLUMNS}
                    FROM data_room_packages
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND package_id=CAST(:package_id AS uuid)
                    FOR UPDATE
                    """
                ),
                params,
            )
        )
        if len(packages) != 1:
            _fail(TASK7_SOURCE_FREEZE_FENCED)
        manifest = _validate_locked_package_fence(observation, packages[0])
        files = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_FILE_COLUMNS}
                    FROM data_room_package_files
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND package_id=CAST(:package_id AS uuid)
                    ORDER BY sequence, file_entry_id
                    FOR UPDATE
                    """
                ),
                params,
            )
        )
        doc_ids = [str(member.doc_id) for member in manifest.files]
        document_ids = [str(member.document_id) for member in manifest.files]
        graph_params = {
            **params,
            "doc_ids": doc_ids,
            "document_ids": document_ids,
        }
        artifacts = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_ARTIFACT_COLUMNS}
                    FROM document_artifacts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND doc_id=ANY(CAST(:doc_ids AS uuid[]))
                    ORDER BY doc_id
                    FOR UPDATE
                    """
                ),
                graph_params,
            )
        )
        documents = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_DOCUMENT_COLUMNS}
                    FROM documents
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id
                    FOR UPDATE
                    """
                ),
                graph_params,
            )
        )
        attempts = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_ATTEMPT_COLUMNS}
                    FROM document_parse_attempts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id, attempt_ordinal, parse_attempt_id
                    FOR UPDATE
                    """
                ),
                graph_params,
            )
        )
        spans = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_SPAN_COLUMNS}
                    FROM document_spans
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id, span_id
                    FOR UPDATE
                    """
                ),
                graph_params,
            )
        )
        return SourceFreezeSnapshot(
            package=packages[0],
            files=files,
            artifacts=artifacts,
            documents=documents,
            attempts=attempts,
            spans=spans,
        )

    def _reload_source_graph(self, plan: SourceFreezePlan) -> SourceFreezeSnapshot:
        params = {
            "tenant_id": plan.tenant_id,
            "deal_id": plan.deal_id,
            "package_id": plan.package_id,
        }
        packages = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_PACKAGE_COLUMNS}
                    FROM data_room_packages
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND package_id=CAST(:package_id AS uuid)
                    """
                ),
                params,
            )
        )
        if len(packages) != 1:
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        manifest = _manifest_from_package(packages[0])
        files = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_FILE_COLUMNS}
                    FROM data_room_package_files
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND package_id=CAST(:package_id AS uuid)
                    ORDER BY sequence, file_entry_id
                    """
                ),
                params,
            )
        )
        doc_ids = [str(member.doc_id) for member in manifest.files]
        document_ids = [str(member.document_id) for member in manifest.files]
        graph_params = {**params, "doc_ids": doc_ids, "document_ids": document_ids}
        artifacts = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_ARTIFACT_COLUMNS}
                    FROM document_artifacts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND doc_id=ANY(CAST(:doc_ids AS uuid[]))
                    ORDER BY doc_id
                    """
                ),
                graph_params,
            )
        )
        documents = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_DOCUMENT_COLUMNS}
                    FROM documents
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id
                    """
                ),
                graph_params,
            )
        )
        attempts = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_ATTEMPT_COLUMNS}
                    FROM document_parse_attempts
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id, attempt_ordinal, parse_attempt_id
                    """
                ),
                graph_params,
            )
        )
        spans = _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_SPAN_COLUMNS}
                    FROM document_spans
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND document_id=ANY(CAST(:document_ids AS uuid[]))
                    ORDER BY document_id, span_id
                    """
                ),
                graph_params,
            )
        )
        return SourceFreezeSnapshot(
            package=packages[0],
            files=files,
            artifacts=artifacts,
            documents=documents,
            attempts=attempts,
            spans=spans,
        )

    def _load_roots(self, plan: SourceFreezePlan) -> tuple[Mapping[str, Any], ...]:
        return _mapping_rows(
            self._connection.execute(
                text(
                    f"""
                    SELECT {_ROOT_COLUMNS}
                    FROM source_lineage_objects
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                      AND deal_id=CAST(:deal_id AS uuid)
                      AND corpus_receipt_id=CAST(:package_id AS uuid)
                    ORDER BY
                      CASE object_kind
                        WHEN 'PACKAGE' THEN 0
                        WHEN 'PACKAGE_FILE' THEN 1
                        WHEN 'ARTIFACT' THEN 2
                        WHEN 'DOCUMENT' THEN 3
                        WHEN 'PARSE_ATTEMPT' THEN 4
                        WHEN 'SPAN' THEN 5
                        ELSE 999
                      END,
                      source_lineage_object_id
                    """
                ),
                {
                    "tenant_id": plan.tenant_id,
                    "deal_id": plan.deal_id,
                    "package_id": plan.package_id,
                },
            )
        )

    def _insert_roots(self, plan: SourceFreezePlan) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO source_lineage_objects (
                    source_lineage_object_id, tenant_id, deal_id,
                    corpus_receipt_id, object_kind, source_version,
                    source_disposition, object_identity_sha256, content_sha256,
                    corpus_receipt_sha256, write_origin
                ) VALUES (
                    CAST(:source_lineage_object_id AS uuid),
                    CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid),
                    CAST(:corpus_receipt_id AS uuid),
                    :object_kind, :source_version, :source_disposition,
                    :object_identity_sha256, :content_sha256,
                    :corpus_receipt_sha256, :write_origin
                )
                ON CONFLICT (source_lineage_object_id) DO NOTHING
                """
            ),
            [root.as_row() for root in plan.roots],
        )
        if _normalized_root_rows(self._load_roots(plan)) != _normalized_root_rows(
            tuple(root.as_row() for root in plan.roots)
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)

    def _require_update_count(
        self,
        statement: str,
        parameters: Mapping[str, object],
        *,
        expected_count: int,
        fenced: bool = False,
    ) -> None:
        result = self._connection.execute(text(statement), parameters)
        if getattr(result, "rowcount", None) != expected_count:
            _fail(TASK7_SOURCE_FREEZE_FENCED if fenced else TASK7_SOURCE_TERMINAL_STATE_INVALID)

    def _bind_native_rows(
        self,
        plan: SourceFreezePlan,
        observation: SourceFreezeObservation,
    ) -> None:
        common = {
            "tenant_id": plan.tenant_id,
            "deal_id": plan.deal_id,
            "package_id": plan.package_id,
        }
        grouped = {
            table_name: tuple(
                binding for binding in plan.bindings if binding.table_name == table_name
            )
            for table_name in (
                "data_room_package_files",
                "document_artifacts",
                "documents",
                "document_parse_attempts",
                "document_spans",
            )
        }

        file_bindings = grouped["data_room_package_files"]
        self._require_update_count(
            """
            UPDATE data_room_package_files
            SET source_binding_state='BOUND',
                source_lineage_object_id=expected.root_id,
                doc_id=expected.doc_id,
                document_id=expected.document_id
            FROM unnest(
                CAST(:native_ids AS uuid[]),
                CAST(:root_ids AS uuid[]),
                CAST(:doc_ids AS uuid[]),
                CAST(:document_ids AS uuid[])
            ) AS expected(native_id, root_id, doc_id, document_id)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND package_id=CAST(:package_id AS uuid)
              AND file_entry_id=expected.native_id
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND data_room_package_files.doc_id IS NULL
              AND data_room_package_files.document_id IS NULL
            """,
            {
                **common,
                "native_ids": [binding.native_id for binding in file_bindings],
                "root_ids": [binding.root_id for binding in file_bindings],
                "doc_ids": [binding.doc_id for binding in file_bindings],
                "document_ids": [binding.document_id for binding in file_bindings],
            },
            expected_count=len(file_bindings),
        )

        artifact_bindings = grouped["document_artifacts"]
        self._require_update_count(
            """
            UPDATE document_artifacts
            SET source_binding_state='BOUND',
                source_lineage_object_id=expected.root_id,
                source_corpus_receipt_id=CAST(:package_id AS uuid)
            FROM unnest(
                CAST(:native_ids AS uuid[]),
                CAST(:root_ids AS uuid[])
            ) AS expected(native_id, root_id)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND doc_id=expected.native_id
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND source_corpus_receipt_id IS NULL
            """,
            {
                **common,
                "native_ids": [binding.native_id for binding in artifact_bindings],
                "root_ids": [binding.root_id for binding in artifact_bindings],
            },
            expected_count=len(artifact_bindings),
        )

        document_bindings = grouped["documents"]
        self._require_update_count(
            """
            UPDATE documents
            SET source_binding_state='BOUND',
                source_lineage_object_id=expected.root_id,
                source_corpus_receipt_id=CAST(:package_id AS uuid)
            FROM unnest(
                CAST(:native_ids AS uuid[]),
                CAST(:root_ids AS uuid[])
            ) AS expected(native_id, root_id)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND document_id=expected.native_id
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND source_corpus_receipt_id IS NULL
            """,
            {
                **common,
                "native_ids": [binding.native_id for binding in document_bindings],
                "root_ids": [binding.root_id for binding in document_bindings],
            },
            expected_count=len(document_bindings),
        )

        attempt_bindings = grouped["document_parse_attempts"]
        self._require_update_count(
            """
            UPDATE document_parse_attempts
            SET source_binding_state='BOUND',
                source_lineage_object_id=expected.root_id,
                source_corpus_receipt_id=CAST(:package_id AS uuid)
            FROM unnest(
                CAST(:native_ids AS uuid[]),
                CAST(:root_ids AS uuid[])
            ) AS expected(native_id, root_id)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND parse_attempt_id=expected.native_id
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND source_corpus_receipt_id IS NULL
            """,
            {
                **common,
                "native_ids": [binding.native_id for binding in attempt_bindings],
                "root_ids": [binding.root_id for binding in attempt_bindings],
            },
            expected_count=len(attempt_bindings),
        )

        span_bindings = grouped["document_spans"]
        if any(binding.parse_attempt_id is None for binding in span_bindings):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        self._require_update_count(
            """
            UPDATE document_spans
            SET source_binding_state='BOUND',
                source_lineage_object_id=expected.root_id,
                source_corpus_receipt_id=CAST(:package_id AS uuid),
                parse_attempt_id=expected.parse_attempt_id
            FROM unnest(
                CAST(:native_ids AS uuid[]),
                CAST(:root_ids AS uuid[]),
                CAST(:parse_attempt_ids AS uuid[])
            ) AS expected(native_id, root_id, parse_attempt_id)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND span_id=expected.native_id
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND source_corpus_receipt_id IS NULL
              AND document_spans.parse_attempt_id IS NULL
            """,
            {
                **common,
                "native_ids": [binding.native_id for binding in span_bindings],
                "root_ids": [binding.root_id for binding in span_bindings],
                "parse_attempt_ids": [binding.parse_attempt_id for binding in span_bindings],
            },
            expected_count=len(span_bindings),
        )
        package_binding = next(
            binding for binding in plan.bindings if binding.table_name == "data_room_packages"
        )
        self._require_update_count(
            """
            UPDATE data_room_packages
            SET status='SEALED',
                source_binding_state='BOUND',
                source_lineage_object_id=CAST(:root_id AS uuid)
            WHERE tenant_id=CAST(:tenant_id AS uuid)
              AND deal_id=CAST(:deal_id AS uuid)
              AND package_id=CAST(:package_id AS uuid)
              AND status='OPEN'
              AND source_binding_state='UNBOUND'
              AND source_lineage_object_id IS NULL
              AND updated_at=:observed_updated_at
            """,
            {
                **common,
                "root_id": package_binding.root_id,
                "observed_updated_at": observation.observed_updated_at,
            },
            expected_count=1,
            fenced=True,
        )

    def freeze_observed_package(
        self,
        observation: SourceFreezeObservation,
    ) -> SourceFreezeReceipt:
        """Freeze one observed package or prove an exact idempotent retry."""

        snapshot = self.lock_source_graph(observation)
        validate_locked_source_snapshot(observation, snapshot)
        terminal_exclusion_authority = getattr(
            self,
            "_terminal_exclusion_authority",
            None,
        )
        plan = build_source_freeze_plan(
            snapshot,
            terminal_exclusion_authority=terminal_exclusion_authority,
        )
        if (
            plan.tenant_id != self._tenant_id
            or plan.tenant_id != _uuid_text(observation.tenant_id)
            or plan.deal_id != _uuid_text(observation.deal_id)
            or plan.package_id != _uuid_text(observation.package_id)
        ):
            _fail(TASK7_SOURCE_TERMINAL_STATE_INVALID)
        if (
            observation.observed_status,
            observation.observed_source_binding_state,
        ) == ("OPEN", "UNBOUND"):
            self._insert_roots(plan)
            self._bind_native_rows(plan, observation)
        reloaded = self._reload_source_graph(plan)
        roots = self._load_roots(plan)
        validate_reloaded_source_freeze(
            plan,
            reloaded,
            roots,
            terminal_exclusion_authority=terminal_exclusion_authority,
        )
        return SourceFreezeReceipt(
            package_id=plan.package_id,
            manifest_sha256=plan.manifest_sha256,
            root_set_sha256=plan.root_set_sha256,
            root_count=len(plan.roots),
            file_count=len(plan.snapshot.files),
        )


def freeze_source_package(
    *,
    engine: Engine,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    repository_factory: RepositoryFactory = PostgresTask7SourceFreezeRepository,
    terminal_exclusion_authority: TerminalExclusionAuthority | None = None,
) -> object:
    """Observe first, then freeze in a distinct short PostgreSQL transaction."""

    try:
        with engine.begin() as observation_connection:
            observation_repository = repository_factory(
                observation_connection,
                tenant_id,
                terminal_exclusion_authority=terminal_exclusion_authority,
            )
            observation = observation_repository.observe_source_package(
                deal_id=deal_id,
                package_id=package_id,
            )
        with engine.begin() as freeze_connection:
            freeze_repository = repository_factory(
                freeze_connection,
                tenant_id,
                terminal_exclusion_authority=terminal_exclusion_authority,
            )
            return freeze_repository.freeze_observed_package(observation)
    except Task7SourceFreezeError:
        raise
    except Exception:
        raise Task7SourceFreezeError(TASK7_SOURCE_TERMINAL_STATE_INVALID) from None


__all__ = [
    "LOCKED_SOURCE_QUERY_ORDER",
    "PostgresTask7SourceFreezeRepository",
    "SourceFreezeBinding",
    "SourceFreezeObservation",
    "SourceFreezePlan",
    "SourceFreezeReceipt",
    "SourceFreezeSnapshot",
    "SourceLineageRoot",
    "TerminalExclusionAuthority",
    "TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE",
    "TASK7_SOURCE_FREEZE_FENCED",
    "TASK7_SOURCE_TERMINAL_STATE_INVALID",
    "Task7SourceFreezeError",
    "build_source_freeze_plan",
    "freeze_source_package",
    "validate_locked_source_snapshot",
    "validate_reloaded_source_freeze",
]
