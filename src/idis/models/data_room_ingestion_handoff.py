"""Slice 18 run-scoped data-room ingestion handoff models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "DataRoomIngestionHandoffReason",
    "DataRoomIngestionHandoffRunResult",
    "DataRoomIngestionHandoffStatus",
    "RunScopedDataRoomIngestionHandoffFileResult",
]


class DataRoomIngestionHandoffStatus(StrEnum):
    """Top-level durable data-room handoff status."""

    DEFERRED = "deferred"
    DURABLE_INGESTED = "durable_ingested"
    DURABLE_REUSED = "durable_reused"
    IN_MEMORY_FALLBACK = "in_memory_fallback"
    FAILED = "failed"


class DataRoomIngestionHandoffReason(StrEnum):
    """Stable reason codes for Slice 18 handoff outcomes."""

    DURABLE_DEPENDENCIES_NOT_CONFIGURED = "durable_dependencies_not_configured"
    NO_INVENTORY_PACKAGE = "no_inventory_package"
    NO_SUPPORTED_FILES = "no_supported_files"
    UNSUPPORTED_FILES_SUMMARY_ONLY = "unsupported_files_summary_only"
    DURABLE_DOCUMENT_REUSED = "durable_document_reused"
    DURABLE_DOCUMENT_INGESTED = "durable_document_ingested"
    IN_MEMORY_FALLBACK_USED = "in_memory_fallback_used"
    DURABLE_INGESTION_FAILED = "durable_ingestion_failed"


class DataRoomIngestionHandoffBaseModel(BaseModel):
    """Base model for safe handoff records."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedDataRoomIngestionHandoffFileResult(DataRoomIngestionHandoffBaseModel):
    """Safe per-file result for data-room durable ingestion handoff."""

    inventory_file_id: str
    relative_path: str
    path_hash: str
    sha256: str
    file_status: str
    handoff_status: DataRoomIngestionHandoffStatus
    reason_codes: list[str]
    durable_artifact_id: str | None = None
    durable_document_id: str | None = None
    storage_uri: str | None = None
    parse_status: str | None = None
    error_codes: list[str] = Field(default_factory=list)

    @field_validator(
        "inventory_file_id",
        "relative_path",
        "path_hash",
        "sha256",
        "file_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes", "error_codes")
    @classmethod
    def _sorted_strings(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    def to_safe_dict(self) -> dict[str, object]:
        """Return summary-safe file result data."""
        return {
            "inventory_file_id": self.inventory_file_id,
            "relative_path": self.relative_path,
            "path_hash": self.path_hash,
            "sha256": self.sha256,
            "file_status": self.file_status,
            "handoff_status": self.handoff_status.value,
            "reason_codes": list(self.reason_codes),
            "durable_artifact_id": self.durable_artifact_id,
            "durable_document_id": self.durable_document_id,
            "storage_uri": self.storage_uri,
            "parse_status": self.parse_status,
            "error_codes": list(self.error_codes),
        }


class DataRoomIngestionHandoffRunResult(DataRoomIngestionHandoffBaseModel):
    """Safe run-step result for data-room durable ingestion handoff."""

    tenant_id: str
    deal_id: str
    run_id: str
    handoff_status: DataRoomIngestionHandoffStatus
    supported_file_count: int = Field(..., ge=0)
    deferred_file_count: int = Field(..., ge=0)
    blocked_file_count: int = Field(..., ge=0)
    durable_ingested_file_count: int = Field(..., ge=0)
    durable_reused_file_count: int = Field(..., ge=0)
    in_memory_fallback_file_count: int = Field(..., ge=0)
    file_results: list[RunScopedDataRoomIngestionHandoffFileResult]
    reason_codes: list[str]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes")
    @classmethod
    def _reasons_sorted(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary with no raw content."""
        return {
            "handoff_status": self.handoff_status.value,
            "supported_file_count": self.supported_file_count,
            "deferred_file_count": self.deferred_file_count,
            "blocked_file_count": self.blocked_file_count,
            "durable_ingested_file_count": self.durable_ingested_file_count,
            "durable_reused_file_count": self.durable_reused_file_count,
            "in_memory_fallback_file_count": self.in_memory_fallback_file_count,
            "reason_codes": list(self.reason_codes),
            "file_results": [file.to_safe_dict() for file in self.file_results],
        }
