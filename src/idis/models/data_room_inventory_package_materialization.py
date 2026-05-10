"""Slice 16 in-memory run-scoped data-room inventory package models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATA_ROOM_FILE_NAMESPACE = UUID("e2130538-fb4f-5ce8-bbe7-c93876feb8cc")
DATA_ROOM_PACKAGE_NAMESPACE = UUID("2d582e79-e45a-5e83-86be-9567f97b9499")

__all__ = [
    "DataRoomInventoryFileStatus",
    "DataRoomInventoryPackageConstructionStatus",
    "DataRoomInventoryPackageRunResult",
    "DataRoomInventoryReason",
    "DataRoomInventoryRejection",
    "RunScopedDataRoomInventoryBlocker",
    "RunScopedDataRoomInventoryFileRecord",
    "RunScopedDataRoomInventoryPackageRecord",
    "RunScopedDataRoomInventoryPackageShell",
    "RunScopedDataRoomInventoryPackageSummary",
    "counter",
    "deterministic_data_room_file_id",
    "deterministic_data_room_inventory_package_id",
]


class DataRoomInventoryPackageConstructionStatus(StrEnum):
    """Data-room inventory package construction status."""

    COMPLETED = "completed"
    FAILED = "failed"


class DataRoomInventoryFileStatus(StrEnum):
    """Per-file inventory status."""

    SUPPORTED = "supported"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


class DataRoomInventoryReason(StrEnum):
    """Stable Slice 16 inventory reason codes."""

    NO_DATA_ROOM_ROOT = "no_data_room_root"
    ROOT_NOT_FOUND = "root_not_found"
    ROOT_NOT_DIRECTORY = "root_not_directory"
    FILE_SCAN_FAILED = "file_scan_failed"
    SUPPORTED_PARSER_AVAILABLE = "supported_parser_available"
    PARSER_FAILED = "parser_failed"
    CONVERSION_REQUIRED = "conversion_required"
    OCR_REQUIRED = "ocr_required"
    UNSUPPORTED_FORMAT = "unsupported_format"
    FILE_TOO_LARGE = "file_too_large"
    UNKNOWN_FORMAT = "unknown_format"


class DataRoomInventoryBaseModel(BaseModel):
    """Base model for deterministic Slice 16 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedDataRoomInventoryBlocker(DataRoomInventoryBaseModel):
    """Safe data-room inventory blocker record."""

    blocker_id: str
    file_id: str | None = None
    reason: DataRoomInventoryReason
    severity: str

    @field_validator("blocker_id", "severity")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RunScopedDataRoomInventoryFileRecord(DataRoomInventoryBaseModel):
    """Safe per-file inventory record."""

    file_id: str
    relative_path: str
    path_hash: str
    extension: str
    size_bytes: int = Field(..., ge=0)
    sha256: str
    file_status: DataRoomInventoryFileStatus
    support_status: str
    triage_status: str
    reason_codes: list[str]
    artifact_id: str | None = None
    document_id: str | None = None

    @field_validator(
        "file_id",
        "relative_path",
        "path_hash",
        "extension",
        "sha256",
        "support_status",
        "triage_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_sorted(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        return _sorted_strings(value)

    @field_validator("extension")
    @classmethod
    def _extension_lowercase(cls, value: str) -> str:
        return value.strip().lower()


class RunScopedDataRoomInventoryPackageSummary(DataRoomInventoryBaseModel):
    """Safe aggregate summary for data-room inventory packages."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_count: int = Field(..., ge=0)
    file_count: int = Field(..., ge=0)
    supported_file_count: int = Field(..., ge=0)
    deferred_file_count: int = Field(..., ge=0)
    blocked_file_count: int = Field(..., ge=0)
    supported_document_count: int = Field(..., ge=0)
    construction_status: DataRoomInventoryPackageConstructionStatus
    by_extension: dict[str, int]
    by_file_status: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("by_extension", "by_file_status", "by_reason")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)

    def to_safe_dict(self) -> dict[str, object]:
        """Return a summary-safe dictionary."""
        return {
            "package_count": self.package_count,
            "file_count": self.file_count,
            "supported_file_count": self.supported_file_count,
            "deferred_file_count": self.deferred_file_count,
            "blocked_file_count": self.blocked_file_count,
            "supported_document_count": self.supported_document_count,
            "construction_status": self.construction_status.value,
            "by_extension": dict(self.by_extension),
            "by_file_status": dict(self.by_file_status),
            "by_reason": dict(self.by_reason),
        }


class RunScopedDataRoomInventoryPackageShell(DataRoomInventoryBaseModel):
    """Safe resume shell for a run-scoped data-room inventory package."""

    tenant_id: str
    deal_id: str
    run_id: str
    inventory_package_id: str
    root_path_hash: str
    construction_status: DataRoomInventoryPackageConstructionStatus
    file_ids: list[str]
    supported_document_ids: list[str]
    deferred_file_ids: list[str]
    blocked_file_ids: list[str]
    reason_codes: list[str]
    by_extension: dict[str, int]
    by_file_status: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id", "inventory_package_id", "root_path_hash")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "file_ids",
        "supported_document_ids",
        "deferred_file_ids",
        "blocked_file_ids",
        "reason_codes",
    )
    @classmethod
    def _lists_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("by_extension", "by_file_status", "by_reason")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)


class RunScopedDataRoomInventoryPackageRecord(DataRoomInventoryBaseModel):
    """In-memory governed data-room inventory/intake package."""

    tenant_id: str
    deal_id: str
    run_id: str
    inventory_package_id: str
    root_path_hash: str
    files: list[RunScopedDataRoomInventoryFileRecord]
    blockers: list[RunScopedDataRoomInventoryBlocker]
    construction_status: DataRoomInventoryPackageConstructionStatus

    @field_validator("tenant_id", "deal_id", "run_id", "inventory_package_id", "root_path_hash")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("files")
    @classmethod
    def _files_sorted(
        cls,
        value: list[RunScopedDataRoomInventoryFileRecord],
    ) -> list[RunScopedDataRoomInventoryFileRecord]:
        return sorted(value, key=lambda file: file.relative_path.casefold())

    @property
    def file_ids(self) -> list[str]:
        """All inventory file IDs."""
        return _sorted_strings(file.file_id for file in self.files)

    @property
    def supported_document_ids(self) -> list[str]:
        """Document IDs that are supported and parsed for downstream preflight."""
        return _sorted_strings(
            file.document_id
            for file in self.files
            if file.file_status == DataRoomInventoryFileStatus.SUPPORTED and file.document_id
        )

    @property
    def deferred_file_ids(self) -> list[str]:
        """File IDs that require future conversion/OCR/unsupported handling."""
        return _sorted_strings(
            file.file_id
            for file in self.files
            if file.file_status == DataRoomInventoryFileStatus.DEFERRED
        )

    @property
    def blocked_file_ids(self) -> list[str]:
        """File IDs that are blocked due to parser failure or unreadability."""
        return _sorted_strings(
            file.file_id
            for file in self.files
            if file.file_status == DataRoomInventoryFileStatus.BLOCKED
        )

    @property
    def reason_codes(self) -> list[str]:
        """Stable reason codes represented by this package."""
        return _sorted_strings(code for file in self.files for code in file.reason_codes)

    @model_validator(mode="after")
    def _blocker_file_ids_exist(self) -> RunScopedDataRoomInventoryPackageRecord:
        file_ids = set(self.file_ids)
        unknown = [
            blocker.file_id
            for blocker in self.blockers
            if blocker.file_id is not None and blocker.file_id not in file_ids
        ]
        if unknown:
            raise ValueError("blocker file_id must reference a package file")
        return self

    def to_shell(self) -> RunScopedDataRoomInventoryPackageShell:
        """Build a safe shell without raw file contents or parsed text."""
        summary = self.to_summary()
        return RunScopedDataRoomInventoryPackageShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            inventory_package_id=self.inventory_package_id,
            root_path_hash=self.root_path_hash,
            construction_status=self.construction_status,
            file_ids=self.file_ids,
            supported_document_ids=self.supported_document_ids,
            deferred_file_ids=self.deferred_file_ids,
            blocked_file_ids=self.blocked_file_ids,
            reason_codes=self.reason_codes,
            by_extension=summary.by_extension,
            by_file_status=summary.by_file_status,
            by_reason=summary.by_reason,
        )

    def to_summary(self) -> RunScopedDataRoomInventoryPackageSummary:
        """Build a summary-safe aggregate view."""
        return RunScopedDataRoomInventoryPackageSummary(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            package_count=1,
            file_count=len(self.files),
            supported_file_count=len(
                [
                    file
                    for file in self.files
                    if file.file_status == DataRoomInventoryFileStatus.SUPPORTED
                ]
            ),
            deferred_file_count=len(
                [
                    file
                    for file in self.files
                    if file.file_status == DataRoomInventoryFileStatus.DEFERRED
                ]
            ),
            blocked_file_count=len(
                [
                    file
                    for file in self.files
                    if file.file_status == DataRoomInventoryFileStatus.BLOCKED
                ]
            ),
            supported_document_count=len(self.supported_document_ids),
            construction_status=self.construction_status,
            by_extension=counter(file.extension for file in self.files),
            by_file_status=counter(file.file_status.value for file in self.files),
            by_reason=counter(code for file in self.files for code in file.reason_codes),
        )

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary for the data-room inventory boundary."""
        shell = self.to_shell()
        return {
            "construction_status": self.construction_status.value,
            "boundary": "data-room inventory/intake boundary",
            "inventory_package_ids": [self.inventory_package_id],
            "root_path_hash": self.root_path_hash,
            "file_ids": shell.file_ids,
            "supported_document_ids": shell.supported_document_ids,
            "deferred_file_ids": shell.deferred_file_ids,
            "blocked_file_ids": shell.blocked_file_ids,
            "reason_codes": shell.reason_codes,
            "files": [file.model_dump(mode="json") for file in self.files],
            "package_shells": [shell.model_dump(mode="json")],
            "summary": self.to_summary().to_safe_dict(),
        }


class DataRoomInventoryRejection(DataRoomInventoryBaseModel):
    """Stable reason-coded data-room inventory rejection."""

    source_artifact_id: str | None = None
    reason: DataRoomInventoryReason
    reason_codes: list[str]
    message: str

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_sorted(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        return _sorted_strings(value)


class DataRoomInventoryPackageRunResult(DataRoomInventoryBaseModel):
    """Result for one data-room inventory package run."""

    tenant_id: str
    deal_id: str
    run_id: str
    construction_status: DataRoomInventoryPackageConstructionStatus
    package_shells: list[RunScopedDataRoomInventoryPackageShell]
    rejections: list[DataRoomInventoryRejection]
    summary: RunScopedDataRoomInventoryPackageSummary
    file_summaries: list[RunScopedDataRoomInventoryFileRecord] = Field(default_factory=list)

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary."""
        file_ids = _sorted_strings(
            file_id for shell in self.package_shells for file_id in shell.file_ids
        )
        supported_document_ids = _sorted_strings(
            document_id
            for shell in self.package_shells
            for document_id in shell.supported_document_ids
        )
        return {
            "construction_status": self.construction_status.value,
            "boundary": "data-room inventory/intake boundary",
            "inventory_package_ids": [shell.inventory_package_id for shell in self.package_shells],
            "root_path_hash": (
                self.package_shells[0].root_path_hash if self.package_shells else None
            ),
            "file_ids": file_ids,
            "supported_document_ids": supported_document_ids,
            "deferred_file_ids": _sorted_strings(
                file_id for shell in self.package_shells for file_id in shell.deferred_file_ids
            ),
            "blocked_file_ids": _sorted_strings(
                file_id for shell in self.package_shells for file_id in shell.blocked_file_ids
            ),
            "reason_codes": _sorted_strings(
                code for shell in self.package_shells for code in shell.reason_codes
            )
            or _sorted_strings(
                code for rejection in self.rejections for code in rejection.reason_codes
            ),
            "files": [file.model_dump(mode="json") for file in self.file_summaries],
            "rejections": [rejection.model_dump(mode="json") for rejection in self.rejections],
            "package_shells": [shell.model_dump(mode="json") for shell in self.package_shells],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_data_room_file_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    relative_path: str,
    sha256: str,
) -> str:
    """Return a deterministic run-scoped data-room file ID."""
    return _uuid5(
        DATA_ROOM_FILE_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "relative_path": _canonical_relative_path(relative_path),
            "sha256": sha256.lower(),
        },
    )


def deterministic_data_room_inventory_package_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    file_ids: list[str],
) -> str:
    """Return a deterministic run-scoped data-room inventory package ID."""
    return _uuid5(
        DATA_ROOM_PACKAGE_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "file_ids": _sorted_strings(file_ids),
        },
    )


def counter(values: Iterable[str]) -> dict[str, int]:
    """Return stable sorted counts for string values."""
    return _sorted_counts(dict(Counter(values)))


def _canonical_relative_path(value: str) -> str:
    return "/".join(part for part in str(value).replace("\\", "/").split("/") if part)


def _sorted_strings(values: Iterable[object | None]) -> list[str]:
    return sorted(
        {str(value).strip() for value in values if value is not None and str(value).strip()}
    )


def _sorted_counts(value: dict[str, int]) -> dict[str, int]:
    return {key: int(value[key]) for key in sorted(value)}


def _uuid5(namespace: UUID, payload: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(payload)))


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
