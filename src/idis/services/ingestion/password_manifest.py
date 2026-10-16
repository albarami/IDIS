"""Secret-safe document-password manifest for strict real-data ingestion.

The manifest is keyed only by the already validated content SHA-256.  Paths,
filenames, and password values never appear in its public representation or
error messages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from idis.runtime.secure_files import SecureFileError, secure_read_integrity_file

DOCUMENT_PASSWORD_MANIFEST_PATH_ENV = "IDIS_REAL_EXAMPLE_PASSWORD_MANIFEST_PATH"
DOCUMENT_PASSWORD_MANIFEST_SCHEMA = "document-password-manifest-v1"
DOCUMENT_PASSWORD_MANIFEST_INVALID = "DOCUMENT_PASSWORD_MANIFEST_INVALID"
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_MANIFEST_ENTRIES = 1024
_MAX_PASSWORD_CHARS = 4096
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DocumentPasswordManifestStatus(StrEnum):
    """Safe operator-facing manifest state."""

    NONE_CONFIGURED = "none_configured"
    CONFIGURED = "configured"


class DocumentPasswordManifestError(RuntimeError):
    """A password manifest failed its confidential integrity contract."""

    code = DOCUMENT_PASSWORD_MANIFEST_INVALID

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class DocumentPasswordManifest:
    """Validated in-memory password lookup with secret-free observations."""

    _passwords_by_sha256: Mapping[str, str]
    status: DocumentPasswordManifestStatus
    schema_version: str = DOCUMENT_PASSWORD_MANIFEST_SCHEMA

    @classmethod
    def none_configured(cls) -> DocumentPasswordManifest:
        """Return the approved no-secret configuration."""
        return cls(
            _passwords_by_sha256=MappingProxyType({}),
            status=DocumentPasswordManifestStatus.NONE_CONFIGURED,
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> DocumentPasswordManifest:
        """Validate and copy a SHA-256-to-password mapping."""
        if not isinstance(values, Mapping) or len(values) > _MAX_MANIFEST_ENTRIES:
            raise DocumentPasswordManifestError
        validated: dict[str, str] = {}
        for digest, password in values.items():
            if not isinstance(digest, str) or not isinstance(password, str):
                raise DocumentPasswordManifestError
            normalized_digest = digest.strip().lower()
            if (
                _SHA256_PATTERN.fullmatch(normalized_digest) is None
                or not password
                or len(password) > _MAX_PASSWORD_CHARS
            ):
                raise DocumentPasswordManifestError
            if normalized_digest in validated:
                raise DocumentPasswordManifestError
            validated[normalized_digest] = password
        return cls(
            _passwords_by_sha256=MappingProxyType(validated),
            status=DocumentPasswordManifestStatus.CONFIGURED,
        )

    @property
    def entry_count(self) -> int:
        """Return only the safe aggregate entry count."""
        return len(self._passwords_by_sha256)

    def resolve(self, content_sha256: str) -> str | None:
        """Resolve a password only from a validated content digest."""
        if not isinstance(content_sha256, str):
            return None
        normalized_digest = content_sha256.strip().lower()
        if _SHA256_PATTERN.fullmatch(normalized_digest) is None:
            return None
        return self._passwords_by_sha256.get(normalized_digest)

    def safe_summary(self) -> dict[str, object]:
        """Return a serializable summary containing no secret or locator."""
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "entry_count": self.entry_count,
        }

    def __repr__(self) -> str:
        return (
            "DocumentPasswordManifest("
            f"schema_version={self.schema_version!r}, "
            f"status={self.status.value!r}, entry_count={self.entry_count})"
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def load_document_password_manifest(
    *,
    env: Mapping[str, str],
) -> DocumentPasswordManifest:
    """Load an optional manifest through the strict secure-file boundary."""
    path_value = str(env.get(DOCUMENT_PASSWORD_MANIFEST_PATH_ENV, "")).strip()
    if not path_value:
        return DocumentPasswordManifest.none_configured()
    try:
        raw = secure_read_integrity_file(
            path_value,
            max_bytes=_MAX_MANIFEST_BYTES,
            require_readonly=True,
            require_secure_parent=True,
            require_owner_only=True,
        )
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "passwords_by_sha256",
        }:
            raise ValueError("invalid manifest shape")
        if payload.get("schema_version") != DOCUMENT_PASSWORD_MANIFEST_SCHEMA:
            raise ValueError("invalid manifest schema")
        passwords = payload.get("passwords_by_sha256")
        if not isinstance(passwords, dict):
            raise ValueError("invalid password mapping")
        return DocumentPasswordManifest.from_mapping(passwords)
    except (
        DocumentPasswordManifestError,
        SecureFileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise DocumentPasswordManifestError from None


__all__ = [
    "DOCUMENT_PASSWORD_MANIFEST_INVALID",
    "DOCUMENT_PASSWORD_MANIFEST_PATH_ENV",
    "DOCUMENT_PASSWORD_MANIFEST_SCHEMA",
    "DocumentPasswordManifest",
    "DocumentPasswordManifestError",
    "DocumentPasswordManifestStatus",
    "load_document_password_manifest",
]
