"""Data-room package service — create a durable package from existing documents (Slice77).

Groups a deal's already-ingested documents (identified by ``document_ids``) into a
durable tenant/deal-scoped :class:`DataRoomPackage` with one per-file ledger row,
deriving parser-triage state from the documents' persisted parser metadata. It never
re-runs parsing/OCR/media/providers, and never stores or returns raw paths, filenames,
object keys, or content: a file's location is captured as a ``path_hash`` + safe
``extension`` only, and the returned summary is the safe package reference.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from idis.models.data_room_package import (
    DataRoomFileStatus,
    DataRoomPackage,
    DataRoomPackageFile,
    DataRoomPackageStatus,
)
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus

_SAFE_EXTENSION_PATTERN = re.compile(r"^\.?[a-z0-9]{1,12}$")
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_BLOCKED_SUPPORT = frozenset(
    {
        DocumentSupportStatus.UNSUPPORTED,
        DocumentSupportStatus.ENCRYPTED,
        DocumentSupportStatus.CORRUPTED,
        DocumentSupportStatus.TOO_LARGE,
    }
)
_BLOCKED_TRIAGE = frozenset(
    {
        DocumentTriageStatus.UNSUPPORTED_SOURCE,
        DocumentTriageStatus.BLOCKED,
        DocumentTriageStatus.TOO_LARGE,
    }
)


class DataRoomPackageError(Exception):
    """Domain error for data-room package creation, carrying a safe reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@runtime_checkable
class DataRoomPackagesRepo(Protocol):
    """Structural interface for the data-room package repository."""

    def create_package(self, package: DataRoomPackage) -> DataRoomPackage: ...

    def add_file(self, file: DataRoomPackageFile) -> DataRoomPackageFile: ...

    def list_files_by_package(self, package_id: str, deal_id: str) -> list[DataRoomPackageFile]: ...


def create_data_room_package(
    *,
    repo: DataRoomPackagesRepo,
    tenant_id: str,
    deal_id: str,
    created_by_actor_id: str | None,
    created_by_actor_type: str | None,
    document_ids: Sequence[str],
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a data-room package from ``document_ids`` within ``deal_id``.

    Args:
        repo: Tenant-scoped data-room package repository.
        tenant_id: Tenant UUID string.
        deal_id: Deal UUID string the package belongs to.
        created_by_actor_id/created_by_actor_type: Originating authenticated actor.
        document_ids: Selected durable document ids (deduped before grouping).
        documents: The deal's preflight corpus (doc dicts) used for validation + triage.

    Returns:
        The safe package summary/ref (no raw paths/filenames/object keys/content).

    Raises:
        DataRoomPackageError: NO_DOCUMENTS_SELECTED or INVALID_DOCUMENT_SELECTION
            (masked — does not reveal which ids or whether they exist elsewhere).
    """
    corpus_by_id = {str(doc.get("document_id")): doc for doc in documents}
    requested = list(dict.fromkeys(str(doc_id) for doc_id in document_ids))
    if not requested:
        raise DataRoomPackageError(
            "NO_DOCUMENTS_SELECTED",
            "No documents were selected for the data-room package",
        )
    missing = [doc_id for doc_id in requested if doc_id not in corpus_by_id]
    if missing:
        raise DataRoomPackageError(
            "INVALID_DOCUMENT_SELECTION",
            "One or more selected documents are not part of this deal",
        )

    now = datetime.now(UTC)
    package_id = uuid4()
    package = DataRoomPackage(
        package_id=package_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        status=DataRoomPackageStatus.OPEN,
        created_by_actor_id=created_by_actor_id,
        created_by_actor_type=created_by_actor_type,
        created_at=now,
        updated_at=now,
    )
    repo.create_package(package)

    for sequence, doc_id in enumerate(requested):
        doc = corpus_by_id[doc_id]
        repo.add_file(
            _build_file(
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=str(package_id),
                sequence=sequence,
                doc=doc,
                created_at=now,
            )
        )

    return build_data_room_package_summary(
        package, repo.list_files_by_package(str(package_id), deal_id)
    )


def _build_file(
    *,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    sequence: int,
    doc: Mapping[str, Any],
    created_at: datetime,
) -> DataRoomPackageFile:
    metadata = doc.get("metadata") or {}
    support_status = _to_support_status(metadata.get("parser_support_status"))
    triage_status = _to_triage_status(metadata.get("parser_triage_status"))
    parse_status = _to_parse_status(doc.get("parse_status"))
    return DataRoomPackageFile(
        file_entry_id=uuid4(),
        tenant_id=tenant_id,
        package_id=package_id,
        deal_id=deal_id,
        sequence=sequence,
        path_hash=_derive_path_hash(doc),
        extension=_derive_extension(doc),
        sha256=doc.get("sha256"),
        file_status=_rollup_file_status(support_status, triage_status, parse_status),
        support_status=support_status,
        triage_status=triage_status,
        parse_status=parse_status,
        reason_codes=_safe_codes(metadata.get("parser_reason_codes")),
        error_codes=_safe_codes(metadata.get("parse_error_codes")),
        doc_id=_opt_str(doc.get("doc_id")),
        document_id=_opt_str(doc.get("document_id")),
        storage_uri=None,
        created_at=created_at,
    )


def _derive_path_hash(doc: Mapping[str, Any]) -> str:
    """Stable SHA-256 over the file's object key (or document id) — never the raw value."""
    raw = doc.get("uri") or str(doc.get("document_id"))
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def _derive_extension(doc: Mapping[str, Any]) -> str | None:
    """Safe extension token derived from the parsed doc_type (e.g. 'pdf')."""
    candidate = str(doc.get("doc_type") or "").strip().lower()
    return candidate if _SAFE_EXTENSION_PATTERN.match(candidate) else None


def _rollup_file_status(
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    parse_status: ParseStatus,
) -> DataRoomFileStatus:
    """Deterministic file-status rollup — intentionally STRICTER than the run-scoped
    inventory precedent (``RunDataRoomInventoryPackageService``); see plan §3.6.

    For a durable product package we err toward caution/visibility:
      - ``supported`` ONLY when support==SUPPORTED AND triage==READY AND parse==PARSED;
      - ``blocked`` when support is unsupported/encrypted/corrupted/too-large, OR triage
        is unsupported_source/blocked/too-large, OR parse FAILED (the run-scoped
        precedent treats unsupported/too-large as *deferred*);
      - ``deferred`` for everything else (partial / conversion / OCR / unknown).
    Nothing is silently dropped: unknown/partial degrade to deferred, never supported.
    """
    if (
        support_status in _BLOCKED_SUPPORT
        or triage_status in _BLOCKED_TRIAGE
        or parse_status == ParseStatus.FAILED
    ):
        return DataRoomFileStatus.BLOCKED
    if (
        support_status == DocumentSupportStatus.SUPPORTED
        and triage_status == DocumentTriageStatus.READY
        and parse_status == ParseStatus.PARSED
    ):
        return DataRoomFileStatus.SUPPORTED
    return DataRoomFileStatus.DEFERRED


def build_data_room_package_summary(
    package: DataRoomPackage, files: Sequence[DataRoomPackageFile]
) -> dict[str, Any]:
    counts_by_status: dict[str, int] = {}
    counts_by_reason_code: dict[str, int] = {}
    for file in files:
        status = file.file_status.value
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        for code in file.reason_codes:
            counts_by_reason_code[code] = counts_by_reason_code.get(code, 0) + 1
    summary = package.safe_dict()
    summary["file_count"] = len(files)
    summary["counts_by_status"] = counts_by_status
    summary["counts_by_reason_code"] = counts_by_reason_code
    return summary


def _to_support_status(value: Any) -> DocumentSupportStatus:
    try:
        return DocumentSupportStatus(str(value))
    except ValueError:
        return DocumentSupportStatus.UNKNOWN


def _to_triage_status(value: Any) -> DocumentTriageStatus:
    try:
        return DocumentTriageStatus(str(value))
    except ValueError:
        return DocumentTriageStatus.UNKNOWN


def _to_parse_status(value: Any) -> ParseStatus:
    try:
        return ParseStatus(str(value))
    except ValueError:
        return ParseStatus.PENDING


def _safe_codes(codes: Any) -> list[str]:
    if not isinstance(codes, (list, tuple)):
        return []
    return sorted({code for code in codes if _SAFE_CODE_PATTERN.match(str(code))})


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
