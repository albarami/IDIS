"""Safe, durable source-manifest contract for a two-company acceptance package.

The manifest intentionally has no source path or private filename field.  It
binds generated upload names and durable document identifiers to content and
source-locator digests, while aggregate aliases make the contract usable in
safe reports.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION = "source-package-manifest-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_EXTENSION_PATTERN = re.compile(r"^[a-z0-9]{1,12}$")
_COMPANY_ALIAS_PATTERN = re.compile(r"^company-[0-9]{3}$")
_DEAL_ALIAS_PATTERN = re.compile(r"^deal-[0-9]{3}$")
_DOCUMENT_SET_ALIAS_PATTERN = re.compile(r"^documents-[0-9]{3}$")
_RUN_ALIAS_PATTERN = re.compile(r"^run-[0-9]{3}$")
_LEDGER_ALIAS_PATTERN = re.compile(r"^ledger-[0-9]{3}$")
_PACKAGE_ALIAS_PATTERN = re.compile(r"^package-[0-9]{3}$")
_UPLOAD_NAME_PATTERN = re.compile(
    r"^(?P<company>company-[0-9]{3})-document-(?P<sequence>[0-9]{5})\."
    r"(?P<extension>[a-z0-9]{1,12})$"
)


class SourcePackageManifestFile(BaseModel):
    """One safe source row bound to its durable document and content digest."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sequence: int = Field(ge=0)
    generated_upload_name: str
    source_locator_sha256: str
    content_sha256: str
    size_bytes: int = Field(ge=0)
    extension: str
    document_id: UUID
    doc_id: UUID

    @field_validator("source_locator_sha256", "content_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("source manifest hashes must be lowercase sha-256 digests")
        return value

    @field_validator("extension")
    @classmethod
    def _validate_extension(cls, value: str) -> str:
        normalized = value.lower()
        if not _SAFE_EXTENSION_PATTERN.fullmatch(normalized):
            raise ValueError("source manifest extension must be a safe token")
        return normalized

    @field_validator("generated_upload_name")
    @classmethod
    def _validate_generated_upload_name(cls, value: str) -> str:
        if not _UPLOAD_NAME_PATTERN.fullmatch(value):
            raise ValueError("generated upload name must use the safe company/document pattern")
        return value

    @model_validator(mode="after")
    def _validate_name_fields(self) -> SourcePackageManifestFile:
        match = _UPLOAD_NAME_PATTERN.fullmatch(self.generated_upload_name)
        if match is None:  # field validator normally catches this; keep the invariant local.
            raise ValueError("invalid generated upload name")
        if int(match.group("sequence")) != self.sequence + 1:
            raise ValueError("generated upload sequence does not match manifest sequence")
        if match.group("extension") != self.extension:
            raise ValueError("generated upload extension does not match manifest extension")
        return self


class SourcePackageManifest(BaseModel):
    """Self-hashed, leakage-safe source manifest persisted with a package."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str
    company_alias: str
    deal_alias: str
    document_set_alias: str
    run_alias: str
    ledger_alias: str
    package_alias: str
    source_tree_fingerprint: str
    file_count: int = Field(gt=0)
    total_bytes: int = Field(ge=0)
    files: list[SourcePackageManifestFile] = Field(min_length=1)
    manifest_sha256: str

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        if value != SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported source package manifest schema")
        return value

    @field_validator("source_tree_fingerprint", "manifest_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("source manifest hashes must be lowercase sha-256 digests")
        return value

    @field_validator("company_alias")
    @classmethod
    def _validate_company_alias(cls, value: str) -> str:
        if not _COMPANY_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe company alias")
        return value

    @field_validator("deal_alias")
    @classmethod
    def _validate_deal_alias(cls, value: str) -> str:
        if not _DEAL_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe deal alias")
        return value

    @field_validator("document_set_alias")
    @classmethod
    def _validate_document_set_alias(cls, value: str) -> str:
        if not _DOCUMENT_SET_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe document-set alias")
        return value

    @field_validator("run_alias")
    @classmethod
    def _validate_run_alias(cls, value: str) -> str:
        if not _RUN_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe run alias")
        return value

    @field_validator("ledger_alias")
    @classmethod
    def _validate_ledger_alias(cls, value: str) -> str:
        if not _LEDGER_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe ledger alias")
        return value

    @field_validator("package_alias")
    @classmethod
    def _validate_package_alias(cls, value: str) -> str:
        if not _PACKAGE_ALIAS_PATTERN.fullmatch(value):
            raise ValueError("invalid safe package alias")
        return value

    @model_validator(mode="after")
    def _validate_contract(self) -> SourcePackageManifest:
        expected_suffix = self.company_alias.removeprefix("company-")
        if (
            self.deal_alias.removeprefix("deal-") != expected_suffix
            or self.document_set_alias.removeprefix("documents-") != expected_suffix
            or self.run_alias.removeprefix("run-") != expected_suffix
            or self.ledger_alias.removeprefix("ledger-") != expected_suffix
            or self.package_alias.removeprefix("package-") != expected_suffix
        ):
            raise ValueError("source manifest aliases do not identify one company package")
        if self.file_count != len(self.files):
            raise ValueError("source manifest file count does not match its rows")
        if self.total_bytes != sum(row.size_bytes for row in self.files):
            raise ValueError("source manifest byte count does not match its rows")
        if [row.sequence for row in self.files] != list(range(len(self.files))):
            raise ValueError("source manifest sequences must be contiguous and ordered")
        if any(
            not row.generated_upload_name.startswith(f"{self.company_alias}-document-")
            for row in self.files
        ):
            raise ValueError("generated upload name does not match its company alias")
        _require_unique(self.files, "generated_upload_name")
        _require_unique(self.files, "source_locator_sha256")
        _require_unique(self.files, "content_sha256")
        _require_unique(self.files, "document_id")
        _require_unique(self.files, "doc_id")
        if self.manifest_sha256 != canonical_source_manifest_sha256(self):
            raise ValueError("source manifest digest does not match its canonical payload")
        return self

    def safe_summary(self) -> dict[str, Any]:
        """Return aggregate proof only; per-file rows stay in the authenticated record."""
        return {
            "schema_version": self.schema_version,
            "company_alias": self.company_alias,
            "deal_alias": self.deal_alias,
            "document_set_alias": self.document_set_alias,
            "run_alias": self.run_alias,
            "ledger_alias": self.ledger_alias,
            "package_alias": self.package_alias,
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "manifest_sha256": self.manifest_sha256,
        }


def canonical_source_manifest_sha256(
    manifest: SourcePackageManifest | Mapping[str, Any],
) -> str:
    """Hash the canonical JSON manifest payload, excluding its self-hash field."""
    if isinstance(manifest, SourcePackageManifest):
        payload = manifest.model_dump(mode="json")
    else:
        payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_unique(files: list[SourcePackageManifestFile], attribute: str) -> None:
    values = [getattr(row, attribute) for row in files]
    if len(values) != len(set(values)):
        raise ValueError(f"source manifest {attribute} values must be unique")
