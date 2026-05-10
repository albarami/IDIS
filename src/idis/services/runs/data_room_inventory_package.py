"""Slice 16 in-memory data-room inventory package service."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path, PurePath
from typing import Any

from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryFileStatus,
    DataRoomInventoryPackageConstructionStatus,
    DataRoomInventoryPackageRunResult,
    DataRoomInventoryReason,
    DataRoomInventoryRejection,
    RunScopedDataRoomInventoryBlocker,
    RunScopedDataRoomInventoryFileRecord,
    RunScopedDataRoomInventoryPackageRecord,
    RunScopedDataRoomInventoryPackageSummary,
    deterministic_data_room_file_id,
    deterministic_data_room_inventory_package_id,
)
from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
)
from idis.parsers.base import ParseErrorCode, ParseResult, SpanDraft
from idis.parsers.registry import parse_bytes
from idis.services.documents.parser_capabilities import (
    capability_for_document,
    triage_document,
)


class InMemoryRunDataRoomInventoryPackageService:
    """Build run-scoped data-room inventory packages from local folders."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        root_path: str | Path | None,
        parse_supported_files: bool = True,
    ) -> tuple[
        DataRoomInventoryPackageRunResult,
        list[RunScopedDataRoomInventoryPackageRecord],
        list[dict[str, Any]],
    ]:
        """Recursively inventory a data-room folder and parse supported files in memory."""
        root = Path(root_path) if root_path is not None else None
        early_rejection = self._early_rejection(root)
        if early_rejection is not None:
            return (
                self._empty_result(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    rejection=early_rejection,
                ),
                [],
                [],
            )
        if root is None:
            return self._noop_result(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id), [], []

        files: list[RunScopedDataRoomInventoryFileRecord] = []
        blockers: list[RunScopedDataRoomInventoryBlocker] = []
        corpus: list[dict[str, Any]] = []

        for file_path in sorted(_iter_files(root), key=lambda path: _relative_path(root, path)):
            file_record, document = self._inventory_file(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                root=root,
                file_path=file_path,
                parse_supported_files=parse_supported_files,
            )
            files.append(file_record)
            if file_record.file_status != DataRoomInventoryFileStatus.SUPPORTED:
                blockers.append(_blocker_for_file(file_record))
            if document is not None:
                corpus.append(document)

        file_ids = [file.file_id for file in files]
        package = RunScopedDataRoomInventoryPackageRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            inventory_package_id=deterministic_data_room_inventory_package_id(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                file_ids=file_ids,
            ),
            root_path_hash=_sha256_text(str(root.resolve())),
            files=files,
            blockers=blockers,
            construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
        )
        result = DataRoomInventoryPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
            package_shells=[package.to_shell()],
            rejections=[],
            summary=package.to_summary(),
            file_summaries=list(package.files),
        )
        return result, [package], corpus

    def _early_rejection(self, root: Path | None) -> DataRoomInventoryRejection | None:
        if root is None:
            return None
        if not root.exists():
            return _rejection(
                DataRoomInventoryReason.ROOT_NOT_FOUND,
                "Data-room inventory root path does not exist",
                source_artifact_id=str(root),
            )
        if not root.is_dir():
            return _rejection(
                DataRoomInventoryReason.ROOT_NOT_DIRECTORY,
                "Data-room inventory root path is not a directory",
                source_artifact_id=str(root),
            )
        return None

    def _noop_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
    ) -> DataRoomInventoryPackageRunResult:
        reason = DataRoomInventoryReason.NO_DATA_ROOM_ROOT
        rejection = _rejection(reason, "No data-room root path provided; existing corpus preserved")
        return DataRoomInventoryPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
            package_shells=[],
            rejections=[rejection],
            summary=RunScopedDataRoomInventoryPackageSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                package_count=0,
                file_count=0,
                supported_file_count=0,
                deferred_file_count=0,
                blocked_file_count=0,
                supported_document_count=0,
                construction_status=DataRoomInventoryPackageConstructionStatus.COMPLETED,
                by_extension={},
                by_file_status={},
                by_reason={reason.value: 1},
            ),
        )

    def _empty_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rejection: DataRoomInventoryRejection,
    ) -> DataRoomInventoryPackageRunResult:
        return DataRoomInventoryPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=DataRoomInventoryPackageConstructionStatus.FAILED,
            package_shells=[],
            rejections=[rejection],
            summary=RunScopedDataRoomInventoryPackageSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                package_count=0,
                file_count=0,
                supported_file_count=0,
                deferred_file_count=0,
                blocked_file_count=0,
                supported_document_count=0,
                construction_status=DataRoomInventoryPackageConstructionStatus.FAILED,
                by_extension={},
                by_file_status={},
                by_reason={rejection.reason.value: 1},
            ),
        )

    def _inventory_file(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        root: Path,
        file_path: Path,
        parse_supported_files: bool,
    ) -> tuple[RunScopedDataRoomInventoryFileRecord, dict[str, Any] | None]:
        data = file_path.read_bytes()
        relative_path = _relative_path(root, file_path)
        sha256 = hashlib.sha256(data).hexdigest()
        file_id = deterministic_data_room_file_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            relative_path=relative_path,
            sha256=sha256,
        )
        capability = capability_for_document(
            filename=relative_path,
            file_size_bytes=len(data),
            data=data[:64],
        )
        parse_result: ParseResult | None = None
        if parse_supported_files and _should_parse(capability.support_status):
            parse_result = parse_bytes(data, filename=file_path.name)
            capability = triage_document(
                filename=relative_path,
                parse_result=parse_result,
            )
        status, reason_codes = _status_and_reasons(
            support_status=capability.support_status,
            triage_status=capability.triage_status,
            parse_result=parse_result,
        )
        document_id = (
            _document_id(file_id) if status == DataRoomInventoryFileStatus.SUPPORTED else None
        )
        artifact_id = _artifact_id(file_id) if document_id else None
        record = RunScopedDataRoomInventoryFileRecord(
            file_id=file_id,
            relative_path=relative_path,
            path_hash=_sha256_text(relative_path),
            extension=PurePath(relative_path).suffix.lower() or ".unknown",
            size_bytes=len(data),
            sha256=sha256,
            file_status=status,
            support_status=capability.support_status.value,
            triage_status=capability.triage_status.value,
            reason_codes=reason_codes,
            artifact_id=artifact_id,
            document_id=document_id,
        )
        if document_id is None or parse_result is None:
            return record, None
        return record, _document_from_parse_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            document_id=document_id,
            artifact_id=artifact_id or "",
            relative_path=relative_path,
            size_bytes=len(data),
            sha256=sha256,
            parse_result=parse_result,
        )


def _iter_files(root: Path) -> Iterator[Path]:
    return (path for path in root.rglob("*") if path.is_file())


def _status_and_reasons(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    parse_result: ParseResult | None,
) -> tuple[DataRoomInventoryFileStatus, list[str]]:
    if parse_result is not None and parse_result.errors:
        error_codes = {error.code for error in parse_result.errors}
        if error_codes & {
            ParseErrorCode.CORRUPTED_FILE,
            ParseErrorCode.INVALID_XLSX,
            ParseErrorCode.ENCRYPTED_PDF,
            ParseErrorCode.INTERNAL_ERROR,
        }:
            return DataRoomInventoryFileStatus.BLOCKED, [
                DataRoomInventoryReason.PARSER_FAILED.value
            ]
        if error_codes & {
            ParseErrorCode.NO_TEXT_EXTRACTED,
            ParseErrorCode.SCANNED_PDF_UNSUPPORTED,
        }:
            return DataRoomInventoryFileStatus.DEFERRED, [
                DataRoomInventoryReason.OCR_REQUIRED.value
            ]
        if error_codes & {
            ParseErrorCode.MAX_SIZE_EXCEEDED,
            ParseErrorCode.MAX_PAGES_EXCEEDED,
            ParseErrorCode.MAX_SHEETS_EXCEEDED,
            ParseErrorCode.MAX_CELLS_EXCEEDED,
        }:
            return DataRoomInventoryFileStatus.DEFERRED, [
                DataRoomInventoryReason.FILE_TOO_LARGE.value
            ]
        return DataRoomInventoryFileStatus.DEFERRED, [
            DataRoomInventoryReason.UNSUPPORTED_FORMAT.value
        ]

    if support_status in {
        DocumentSupportStatus.SUPPORTED,
        DocumentSupportStatus.PARTIALLY_SUPPORTED,
    } and triage_status in {DocumentTriageStatus.READY, DocumentTriageStatus.PARTIAL}:
        return DataRoomInventoryFileStatus.SUPPORTED, [
            DataRoomInventoryReason.SUPPORTED_PARSER_AVAILABLE.value
        ]
    if support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return DataRoomInventoryFileStatus.DEFERRED, [
            DataRoomInventoryReason.CONVERSION_REQUIRED.value
        ]
    if triage_status == DocumentTriageStatus.OCR_REQUIRED:
        return DataRoomInventoryFileStatus.DEFERRED, [DataRoomInventoryReason.OCR_REQUIRED.value]
    if support_status == DocumentSupportStatus.TOO_LARGE:
        return DataRoomInventoryFileStatus.DEFERRED, [DataRoomInventoryReason.FILE_TOO_LARGE.value]
    if support_status == DocumentSupportStatus.UNSUPPORTED:
        return DataRoomInventoryFileStatus.DEFERRED, [
            DataRoomInventoryReason.UNSUPPORTED_FORMAT.value
        ]
    return DataRoomInventoryFileStatus.DEFERRED, [DataRoomInventoryReason.UNKNOWN_FORMAT.value]


def _should_parse(support_status: DocumentSupportStatus) -> bool:
    return support_status in {
        DocumentSupportStatus.SUPPORTED,
        DocumentSupportStatus.PARTIALLY_SUPPORTED,
    }


def _document_from_parse_result(
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str,
    artifact_id: str,
    relative_path: str,
    size_bytes: int,
    sha256: str,
    parse_result: ParseResult,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "document_id": document_id,
        "document_name": relative_path,
        "doc_type": parse_result.doc_type,
        "uri": relative_path,
        "metadata": {
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "parser_doc_type": parse_result.doc_type,
            "detected_format": parse_result.doc_type,
            "parse_error_codes": [error.code.value for error in parse_result.errors],
            "parse_warning_codes": list(parse_result.warnings),
        },
        "spans": [
            _span_dict(
                tenant_id=tenant_id,
                deal_id=deal_id,
                document_id=document_id,
                span_index=index,
                span=span,
            )
            for index, span in enumerate(parse_result.spans, start=1)
        ],
    }


def _span_dict(
    *,
    tenant_id: str,
    deal_id: str,
    document_id: str,
    span_index: int,
    span: SpanDraft,
) -> dict[str, Any]:
    content_hash = span.content_hash or _sha256_text(span.text_excerpt)
    span_id = _sha256_text(f"{document_id}:{span_index}:{content_hash}")[:32]
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "document_id": document_id,
        "span_id": f"span-{span_id}",
        "span_type": span.span_type,
        "locator": span.locator,
        "text_excerpt": span.text_excerpt,
        "content_hash": content_hash,
    }


def _blocker_for_file(
    file: RunScopedDataRoomInventoryFileRecord,
) -> RunScopedDataRoomInventoryBlocker:
    reason = DataRoomInventoryReason(file.reason_codes[0])
    severity = "blocking" if file.file_status == DataRoomInventoryFileStatus.BLOCKED else "deferred"
    return RunScopedDataRoomInventoryBlocker(
        blocker_id=f"blocker-{file.file_id}",
        file_id=file.file_id,
        reason=reason,
        severity=severity,
    )


def _relative_path(root: Path, file_path: Path) -> str:
    return file_path.relative_to(root).as_posix()


def _document_id(file_id: str) -> str:
    return f"document-{file_id}"


def _artifact_id(file_id: str) -> str:
    return f"artifact-{file_id}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rejection(
    reason: DataRoomInventoryReason,
    message: str,
    *,
    source_artifact_id: str | None = None,
) -> DataRoomInventoryRejection:
    return DataRoomInventoryRejection(
        source_artifact_id=source_artifact_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
