"""Durable data-room package + file-ledger models (Slice77).

A :class:`DataRoomPackage` groups a deal's already-ingested documents under a
durable, tenant-scoped product package keyed by ``package_id`` (no user-supplied
name). A :class:`DataRoomPackageFile` is one per-file ledger row carrying
parser-triage state and safe references only: a file's location is stored as a
``path_hash`` plus a safe ``extension`` — raw folder paths, filenames, storage
keys, manifest URIs, and content never appear in the safe/public dicts.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_EXTENSION_PATTERN = re.compile(r"^\.?[a-z0-9]{1,12}$")
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_ALLOWED_ACTOR_TYPES = frozenset({"HUMAN", "SERVICE"})


class DataRoomPackageStatus(StrEnum):
    """Lifecycle status of a durable data-room package."""

    OPEN = "OPEN"
    SEALED = "SEALED"


class DataRoomFileStatus(StrEnum):
    """Per-file rollup status in the package ledger (mirrors the ingestion handoff)."""

    SUPPORTED = "supported"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


class DataRoomPackage(BaseModel):
    """Durable tenant/deal-scoped data-room package header (keyed by ``package_id``).

    Carries safe aggregate counts for operator/public reference. Internal fields
    (``manifest_uri``, ``metadata``, ``tenant_id``, originating actor) are stored
    on the model but excluded from :meth:`safe_dict`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    package_id: UUID
    tenant_id: UUID
    deal_id: UUID
    status: DataRoomPackageStatus = DataRoomPackageStatus.OPEN
    created_by_actor_id: str | None = None
    created_by_actor_type: str | None = None
    file_count: int = Field(default=0, ge=0)
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    counts_by_reason_code: dict[str, int] = Field(default_factory=dict)
    manifest_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_by_actor_type")
    @classmethod
    def _validate_actor_type(cls, value: str | None) -> str | None:
        if value is not None and value not in _ALLOWED_ACTOR_TYPES:
            raise ValueError("created_by_actor_type must be HUMAN or SERVICE")
        return value

    def safe_dict(self) -> dict[str, Any]:
        """Operator/public-safe reference — no tenant id, actor ids, manifest uri, or metadata."""
        return {
            "package_id": str(self.package_id),
            "deal_id": str(self.deal_id),
            "status": self.status.value,
            "file_count": self.file_count,
            "counts_by_status": dict(self.counts_by_status),
            "counts_by_reason_code": dict(self.counts_by_reason_code),
            "created_at": self.created_at.isoformat(),
        }


class DataRoomPackageFile(BaseModel):
    """Per-file ledger row — location stored as ``path_hash`` + safe ``extension`` only.

    Internal ``storage_uri`` and ``tenant_id``/``package_id``/``deal_id`` are
    stored on the model but excluded from :meth:`safe_dict`. ``reason_codes`` and
    ``error_codes`` are normalised to safe lowercase tokens, sorted and de-duped.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    file_entry_id: UUID
    tenant_id: UUID
    package_id: UUID
    deal_id: UUID
    sequence: int = Field(ge=0)
    path_hash: str
    extension: str | None = None
    sha256: str | None = None
    file_status: DataRoomFileStatus
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    parse_status: ParseStatus = ParseStatus.PENDING
    reason_codes: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)
    doc_id: UUID | None = None
    document_id: UUID | None = None
    storage_uri: str | None = None
    created_at: datetime

    @field_validator("path_hash")
    @classmethod
    def _validate_path_hash(cls, value: str) -> str:
        if not _SHA256_PATTERN.match(value):
            raise ValueError("path_hash must be a 64-char lowercase hex sha-256 digest")
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_PATTERN.match(value):
            raise ValueError("sha256 must be a 64-char lowercase hex digest")
        return value

    @field_validator("extension")
    @classmethod
    def _validate_extension(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not _SAFE_EXTENSION_PATTERN.match(normalized):
            raise ValueError("extension must be a short safe token, not a path or filename")
        return normalized

    @field_validator("reason_codes", "error_codes")
    @classmethod
    def _sorted_unique_safe_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not _SAFE_CODE_PATTERN.match(code):
                raise ValueError("codes must be safe lowercase tokens (no paths or raw values)")
        return sorted(set(value))

    def safe_dict(self) -> dict[str, Any]:
        """Operator/public-safe row — no tenant/package/deal ids, sequence, or storage uri."""
        safe: dict[str, Any] = {
            "file_entry_id": str(self.file_entry_id),
            "path_hash": self.path_hash,
            "extension": self.extension,
            "file_status": self.file_status.value,
            "support_status": self.support_status.value,
            "triage_status": self.triage_status.value,
            "parse_status": self.parse_status.value,
            "reason_codes": list(self.reason_codes),
            "error_codes": list(self.error_codes),
            "sha256": self.sha256,
        }
        if self.doc_id is not None:
            safe["doc_id"] = str(self.doc_id)
        if self.document_id is not None:
            safe["document_id"] = str(self.document_id)
        return safe
