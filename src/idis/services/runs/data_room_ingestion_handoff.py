"""Slice 18 data-room durable ingestion handoff service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from idis.models.data_room_ingestion_handoff import (
    DataRoomIngestionHandoffReason,
    DataRoomIngestionHandoffRunResult,
    DataRoomIngestionHandoffStatus,
    RunScopedDataRoomIngestionHandoffFileResult,
)
from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryFileStatus,
    RunScopedDataRoomInventoryFileRecord,
    RunScopedDataRoomInventoryPackageRecord,
    RunScopedDataRoomInventoryPackageShell,
)

DataRoomIngestBytesFn = Callable[..., dict[str, Any]]
ExistingDocumentLookupFn = Callable[[RunScopedDataRoomInventoryFileRecord], dict[str, Any] | None]
PreflightCorpusLoaderFn = Callable[[], list[dict[str, Any]]]


class InMemoryRunDataRoomIngestionHandoffService:
    """Hand supported inventory files to a durable ingestion adapter."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        root_path: str | Path | None,
        inventory_package: RunScopedDataRoomInventoryPackageRecord
        | RunScopedDataRoomInventoryPackageShell
        | None,
        inventory_corpus: list[dict[str, Any]] | None = None,
        ingest_bytes_fn: DataRoomIngestBytesFn | None = None,
        existing_document_lookup_fn: ExistingDocumentLookupFn | None = None,
        preflight_corpus_loader_fn: PreflightCorpusLoaderFn | None = None,
    ) -> tuple[DataRoomIngestionHandoffRunResult, list[dict[str, Any]]]:
        """Persist supported inventory files when durable dependencies are injected."""
        if inventory_package is None:
            return (
                _empty_result(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    handoff_status=DataRoomIngestionHandoffStatus.DEFERRED,
                    reason=DataRoomIngestionHandoffReason.NO_INVENTORY_PACKAGE,
                ),
                [],
            )

        files = _package_files(inventory_package)
        supported_files = [
            file for file in files if file.file_status == DataRoomInventoryFileStatus.SUPPORTED
        ]
        unsupported_results = [
            _summary_only_file_result(file)
            for file in files
            if file.file_status != DataRoomInventoryFileStatus.SUPPORTED
        ]
        if ingest_bytes_fn is None:
            return (
                _result(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    handoff_status=DataRoomIngestionHandoffStatus.DEFERRED,
                    files=unsupported_results
                    + [
                        _deferred_supported_file_result(
                            file,
                            DataRoomIngestionHandoffReason.DURABLE_DEPENDENCIES_NOT_CONFIGURED,
                        )
                        for file in supported_files
                    ],
                    package_files=files,
                    reason_codes=[
                        DataRoomIngestionHandoffReason.DURABLE_DEPENDENCIES_NOT_CONFIGURED.value
                    ],
                ),
                [],
            )
        if not supported_files:
            return (
                _result(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    handoff_status=DataRoomIngestionHandoffStatus.DEFERRED,
                    files=unsupported_results,
                    package_files=files,
                    reason_codes=[DataRoomIngestionHandoffReason.NO_SUPPORTED_FILES.value],
                ),
                [],
            )

        root = Path(root_path) if root_path is not None else None
        file_results: list[RunScopedDataRoomIngestionHandoffFileResult] = list(unsupported_results)
        for file in supported_files:
            existing = existing_document_lookup_fn(file) if existing_document_lookup_fn else None
            if existing is not None:
                file_results.append(_durable_file_result(file, existing, reused=True))
                continue

            if root is None:
                file_results.append(
                    _deferred_supported_file_result(
                        file,
                        DataRoomIngestionHandoffReason.DURABLE_DEPENDENCIES_NOT_CONFIGURED,
                    )
                )
                continue

            data = (root / file.relative_path).read_bytes()
            metadata = _provenance_metadata(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                inventory_package_id=inventory_package.inventory_package_id,
                file=file,
            )
            ingested = ingest_bytes_fn(file_record=file, data=data, metadata=metadata)
            file_results.append(
                _durable_file_result(
                    file,
                    ingested,
                    reused=False,
                    in_memory_fallback=existing_document_lookup_fn is None,
                )
            )

        handoff_status = _handoff_status(file_results, existing_document_lookup_fn)
        output_corpus = preflight_corpus_loader_fn() if preflight_corpus_loader_fn else []
        if not output_corpus and handoff_status in {
            DataRoomIngestionHandoffStatus.DURABLE_INGESTED,
            DataRoomIngestionHandoffStatus.DURABLE_REUSED,
        }:
            output_corpus = list(inventory_corpus or [])

        return (
            _result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                handoff_status=handoff_status,
                files=file_results,
                package_files=files,
                reason_codes=_result_reasons(file_results),
            ),
            output_corpus,
        )


def _package_files(
    inventory_package: RunScopedDataRoomInventoryPackageRecord
    | RunScopedDataRoomInventoryPackageShell,
) -> list[RunScopedDataRoomInventoryFileRecord]:
    files = getattr(inventory_package, "files", None)
    if files is None:
        return []
    return list(files)


def _provenance_metadata(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    inventory_package_id: str,
    file: RunScopedDataRoomInventoryFileRecord,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "source_system": "data_room_inventory",
        "inventory_package_id": inventory_package_id,
        "inventory_file_id": file.file_id,
        "relative_path": file.relative_path,
        "path_hash": file.path_hash,
        "sha256": file.sha256,
    }


def _summary_only_file_result(
    file: RunScopedDataRoomInventoryFileRecord,
) -> RunScopedDataRoomIngestionHandoffFileResult:
    return RunScopedDataRoomIngestionHandoffFileResult(
        inventory_file_id=file.file_id,
        relative_path=file.relative_path,
        path_hash=file.path_hash,
        sha256=file.sha256,
        file_status=file.file_status.value,
        handoff_status=DataRoomIngestionHandoffStatus.DEFERRED,
        reason_codes=list(file.reason_codes)
        + [DataRoomIngestionHandoffReason.UNSUPPORTED_FILES_SUMMARY_ONLY.value],
    )


def _deferred_supported_file_result(
    file: RunScopedDataRoomInventoryFileRecord,
    reason: DataRoomIngestionHandoffReason,
) -> RunScopedDataRoomIngestionHandoffFileResult:
    return RunScopedDataRoomIngestionHandoffFileResult(
        inventory_file_id=file.file_id,
        relative_path=file.relative_path,
        path_hash=file.path_hash,
        sha256=file.sha256,
        file_status=file.file_status.value,
        handoff_status=DataRoomIngestionHandoffStatus.DEFERRED,
        reason_codes=[reason.value],
    )


def _durable_file_result(
    file: RunScopedDataRoomInventoryFileRecord,
    document: dict[str, Any],
    *,
    reused: bool,
    in_memory_fallback: bool = False,
) -> RunScopedDataRoomIngestionHandoffFileResult:
    if in_memory_fallback:
        status = DataRoomIngestionHandoffStatus.IN_MEMORY_FALLBACK
        reason = DataRoomIngestionHandoffReason.IN_MEMORY_FALLBACK_USED
    elif reused:
        status = DataRoomIngestionHandoffStatus.DURABLE_REUSED
        reason = DataRoomIngestionHandoffReason.DURABLE_DOCUMENT_REUSED
    else:
        status = DataRoomIngestionHandoffStatus.DURABLE_INGESTED
        reason = DataRoomIngestionHandoffReason.DURABLE_DOCUMENT_INGESTED
    return RunScopedDataRoomIngestionHandoffFileResult(
        inventory_file_id=file.file_id,
        relative_path=file.relative_path,
        path_hash=file.path_hash,
        sha256=file.sha256,
        file_status=file.file_status.value,
        handoff_status=status,
        reason_codes=[reason.value],
        durable_artifact_id=_string_value(document, "artifact_id", "doc_id"),
        durable_document_id=_string_value(document, "document_id"),
        storage_uri=_string_value(document, "storage_uri", "uri"),
        parse_status=_string_value(document, "parse_status"),
        error_codes=_error_codes(document),
    )


def _string_value(document: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = document.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _error_codes(document: dict[str, Any]) -> list[str]:
    errors = document.get("errors")
    if not isinstance(errors, list):
        return []
    codes: list[str] = []
    for error in errors:
        if isinstance(error, dict) and error.get("code"):
            codes.append(str(error["code"]))
    return codes


def _handoff_status(
    file_results: list[RunScopedDataRoomIngestionHandoffFileResult],
    existing_document_lookup_fn: ExistingDocumentLookupFn | None,
) -> DataRoomIngestionHandoffStatus:
    statuses = {file.handoff_status for file in file_results}
    if DataRoomIngestionHandoffStatus.DURABLE_INGESTED in statuses:
        return DataRoomIngestionHandoffStatus.DURABLE_INGESTED
    if DataRoomIngestionHandoffStatus.DURABLE_REUSED in statuses:
        return DataRoomIngestionHandoffStatus.DURABLE_REUSED
    if (
        existing_document_lookup_fn is None
        and DataRoomIngestionHandoffStatus.IN_MEMORY_FALLBACK in statuses
    ):
        return DataRoomIngestionHandoffStatus.IN_MEMORY_FALLBACK
    return DataRoomIngestionHandoffStatus.DEFERRED


def _result_reasons(files: list[RunScopedDataRoomIngestionHandoffFileResult]) -> list[str]:
    reasons: list[str] = []
    for file in files:
        reasons.extend(file.reason_codes)
    return sorted(set(reasons))


def _result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    handoff_status: DataRoomIngestionHandoffStatus,
    files: list[RunScopedDataRoomIngestionHandoffFileResult],
    package_files: list[RunScopedDataRoomInventoryFileRecord],
    reason_codes: list[str],
) -> DataRoomIngestionHandoffRunResult:
    return DataRoomIngestionHandoffRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        handoff_status=handoff_status,
        supported_file_count=sum(
            1 for file in package_files if file.file_status == DataRoomInventoryFileStatus.SUPPORTED
        ),
        deferred_file_count=sum(
            1 for file in package_files if file.file_status == DataRoomInventoryFileStatus.DEFERRED
        ),
        blocked_file_count=sum(
            1 for file in package_files if file.file_status == DataRoomInventoryFileStatus.BLOCKED
        ),
        durable_ingested_file_count=sum(
            1
            for file in files
            if file.handoff_status == DataRoomIngestionHandoffStatus.DURABLE_INGESTED
        ),
        durable_reused_file_count=sum(
            1
            for file in files
            if file.handoff_status == DataRoomIngestionHandoffStatus.DURABLE_REUSED
        ),
        in_memory_fallback_file_count=sum(
            1
            for file in files
            if file.handoff_status == DataRoomIngestionHandoffStatus.IN_MEMORY_FALLBACK
        ),
        file_results=sorted(files, key=lambda file: file.relative_path.casefold()),
        reason_codes=reason_codes,
    )


def _empty_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    handoff_status: DataRoomIngestionHandoffStatus,
    reason: DataRoomIngestionHandoffReason,
) -> DataRoomIngestionHandoffRunResult:
    return _result(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        handoff_status=handoff_status,
        files=[],
        package_files=[],
        reason_codes=[reason.value],
    )
