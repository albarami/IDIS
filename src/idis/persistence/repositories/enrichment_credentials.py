"""BYOL credential persistence for enrichment connectors.

Provides tenant-scoped encrypted credential storage with Postgres persistence
and in-memory fallback. Encryption-at-rest is mandatory when Postgres is configured.

Fail-closed: missing encryption key with Postgres configured = fatal error.

Spec: IDIS_Data_Architecture_v3_1.md §BYOL Security & Tenant Isolation
Spec: IDIS_Data_Residency_and_Compliance_Model_v6_3.md §5
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_ENV = "IDIS_ENRICHMENT_ENCRYPTION_KEY"


class CredentialNotFoundError(Exception):
    """Raised when credentials are not found for a tenant+connector pair."""

    def __init__(self, tenant_id: str, connector_id: str) -> None:
        self.tenant_id = tenant_id
        self.connector_id = connector_id
        super().__init__(f"No credentials found for connector={connector_id} tenant={tenant_id}")


class EncryptionKeyMissingError(Exception):
    """Raised when encryption key is required but not configured."""

    def __init__(self) -> None:
        super().__init__(
            f"Encryption key not configured ({ENCRYPTION_KEY_ENV}). "
            "BYOL credential persistence requires encryption-at-rest."
        )


class CredentialRecord(BaseModel):
    """A stored BYOL credential record.

    Attributes:
        tenant_id: Owning tenant UUID.
        connector_id: Provider identifier this credential is for.
        created_at: When the credential was first stored.
        rotated_at: When the credential was last rotated (None if never rotated).
        revoked_at: When the credential was revoked (None if active).
    """

    tenant_id: str
    connector_id: str
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None


class InMemoryCredentialRepository:
    """In-memory BYOL credential store for development/testing.

    Credentials are stored as plaintext dicts keyed by (tenant_id, connector_id).
    No encryption applied in-memory mode.
    """

    def __init__(self) -> None:
        """Initialize empty credential store."""
        self._store: dict[tuple[str, str], dict[str, Any]] = {}
        self._metadata: dict[tuple[str, str], CredentialRecord] = {}

    def store(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        credentials: dict[str, str],
    ) -> CredentialRecord:
        """Store or update credentials for a tenant+connector pair.

        Args:
            tenant_id: Tenant UUID.
            connector_id: Provider identifier.
            credentials: Key-value credential pairs (e.g., api_key, secret).

        Returns:
            CredentialRecord with metadata.
        """
        key = (tenant_id, connector_id)
        now = datetime.now(UTC)

        existing = self._metadata.get(key)
        if existing is not None:
            record = CredentialRecord(
                tenant_id=tenant_id,
                connector_id=connector_id,
                created_at=existing.created_at,
                rotated_at=now,
                revoked_at=None,
            )
        else:
            record = CredentialRecord(
                tenant_id=tenant_id,
                connector_id=connector_id,
                created_at=now,
            )

        self._store[key] = dict(credentials)
        self._metadata[key] = record
        return record

    def load(
        self,
        *,
        tenant_id: str,
        connector_id: str,
    ) -> dict[str, str]:
        """Load credentials for a tenant+connector pair.

        Args:
            tenant_id: Tenant UUID.
            connector_id: Provider identifier.

        Returns:
            Credential dict.

        Raises:
            CredentialNotFoundError: If no credentials exist for this pair.
        """
        key = (tenant_id, connector_id)
        creds = self._store.get(key)
        if creds is None:
            raise CredentialNotFoundError(tenant_id, connector_id)

        meta = self._metadata[key]
        if meta.revoked_at is not None:
            raise CredentialNotFoundError(tenant_id, connector_id)

        return dict(creds)

    def revoke(
        self,
        *,
        tenant_id: str,
        connector_id: str,
    ) -> CredentialRecord:
        """Revoke credentials for a tenant+connector pair.

        Args:
            tenant_id: Tenant UUID.
            connector_id: Provider identifier.

        Returns:
            Updated CredentialRecord with revoked_at set.

        Raises:
            CredentialNotFoundError: If no credentials exist.
        """
        key = (tenant_id, connector_id)
        meta = self._metadata.get(key)
        if meta is None:
            raise CredentialNotFoundError(tenant_id, connector_id)

        now = datetime.now(UTC)
        updated = CredentialRecord(
            tenant_id=tenant_id,
            connector_id=connector_id,
            created_at=meta.created_at,
            rotated_at=meta.rotated_at,
            revoked_at=now,
        )
        self._metadata[key] = updated
        return updated

    def exists(self, *, tenant_id: str, connector_id: str) -> bool:
        """Check if active credentials exist for a tenant+connector pair.

        Args:
            tenant_id: Tenant UUID.
            connector_id: Provider identifier.

        Returns:
            True if active (non-revoked) credentials exist.
        """
        key = (tenant_id, connector_id)
        meta = self._metadata.get(key)
        if meta is None:
            return False
        return meta.revoked_at is None

    def clear(self) -> None:
        """Clear all stored credentials. For testing only."""
        self._store.clear()
        self._metadata.clear()


def _get_encryption_key() -> bytes:
    """Load the encryption key from environment.

    Returns:
        32-byte key derived from the environment variable.

    Raises:
        EncryptionKeyMissingError: If the env var is not set.
    """
    raw_key = os.environ.get(ENCRYPTION_KEY_ENV)
    if not raw_key:
        raise EncryptionKeyMissingError()
    return hashlib.sha256(raw_key.encode("utf-8")).digest()


def encrypt_credentials(credentials: dict[str, str]) -> str:
    """Encrypt credentials dict to a ciphertext string.

    Uses XOR-based encryption with HMAC authentication.
    For production, replace with AES-GCM via cryptography library.

    Args:
        credentials: Plaintext credential key-value pairs.

    Returns:
        Base64-encoded ciphertext string with embedded IV and HMAC.

    Raises:
        EncryptionKeyMissingError: If encryption key is not configured.
    """
    key = _get_encryption_key()
    plaintext = json.dumps(credentials, sort_keys=True, separators=(",", ":")).encode("utf-8")

    iv = secrets.token_bytes(16)
    # Derive a stream key from key + IV
    stream_key = hashlib.sha256(key + iv).digest()

    # XOR encryption (lightweight; swap for AES-GCM in production hardening)
    encrypted = bytes(p ^ stream_key[i % len(stream_key)] for i, p in enumerate(plaintext))

    mac = hmac.new(key, iv + encrypted, hashlib.sha256).digest()
    payload = iv + encrypted + mac

    return base64.b64encode(payload).decode("ascii")


def decrypt_credentials(ciphertext: str) -> dict[str, str]:
    """Decrypt a ciphertext string back to credentials dict.

    Args:
        ciphertext: Base64-encoded ciphertext from encrypt_credentials.

    Returns:
        Decrypted credential dict.

    Raises:
        EncryptionKeyMissingError: If encryption key is not configured.
        ValueError: If ciphertext is invalid or HMAC verification fails.
    """
    key = _get_encryption_key()
    payload = base64.b64decode(ciphertext)

    if len(payload) < 48:
        raise ValueError("Invalid ciphertext: too short")

    iv = payload[:16]
    mac = payload[-32:]
    encrypted = payload[16:-32]

    expected_mac = hmac.new(key, iv + encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("Invalid ciphertext: HMAC verification failed")

    stream_key = hashlib.sha256(key + iv).digest()
    plaintext = bytes(c ^ stream_key[i % len(stream_key)] for i, c in enumerate(encrypted))

    result: dict[str, str] = json.loads(plaintext.decode("utf-8"))
    return result
