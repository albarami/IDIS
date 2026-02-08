"""Tests for BYOL credential persistence (in-memory + encryption).

Verifies:
- Store and load credentials (happy path)
- Credential not found raises CredentialNotFoundError
- Revoked credentials cannot be loaded
- Credential rotation updates rotated_at
- Encryption round-trip produces correct plaintext
- Missing encryption key raises EncryptionKeyMissingError
- HMAC tamper detection on ciphertext
- exists() returns correct status for active/revoked/missing credentials
"""

from __future__ import annotations

import pytest

from idis.persistence.repositories.enrichment_credentials import (
    CredentialNotFoundError,
    EncryptionKeyMissingError,
    InMemoryCredentialRepository,
    decrypt_credentials,
    encrypt_credentials,
)

TENANT_ID = "tenant-cred-001"
CONNECTOR_ID = "sec_edgar"


class TestInMemoryCredentialRepository:
    """In-memory credential repo operations."""

    def test_store_and_load(self) -> None:
        repo = InMemoryCredentialRepository()
        creds = {"api_key": "abc123", "secret": "xyz789"}
        repo.store(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID, credentials=creds)

        loaded = repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)
        assert loaded == creds

    def test_load_nonexistent_raises(self) -> None:
        repo = InMemoryCredentialRepository()
        with pytest.raises(CredentialNotFoundError):
            repo.load(tenant_id=TENANT_ID, connector_id="nonexistent")

    def test_revoked_credential_not_loadable(self) -> None:
        repo = InMemoryCredentialRepository()
        repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "test"},
        )
        repo.revoke(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)

        with pytest.raises(CredentialNotFoundError):
            repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)

    def test_revoke_nonexistent_raises(self) -> None:
        repo = InMemoryCredentialRepository()
        with pytest.raises(CredentialNotFoundError):
            repo.revoke(tenant_id=TENANT_ID, connector_id="nonexistent")

    def test_rotation_updates_timestamp(self) -> None:
        repo = InMemoryCredentialRepository()
        record1 = repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "v1"},
        )
        assert record1.rotated_at is None

        record2 = repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "v2"},
        )
        assert record2.rotated_at is not None
        assert record2.created_at == record1.created_at

        loaded = repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)
        assert loaded["api_key"] == "v2"

    def test_exists_returns_true_for_active(self) -> None:
        repo = InMemoryCredentialRepository()
        repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "test"},
        )
        assert repo.exists(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) is True

    def test_exists_returns_false_for_missing(self) -> None:
        repo = InMemoryCredentialRepository()
        assert repo.exists(tenant_id=TENANT_ID, connector_id="missing") is False

    def test_exists_returns_false_for_revoked(self) -> None:
        repo = InMemoryCredentialRepository()
        repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "test"},
        )
        repo.revoke(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)
        assert repo.exists(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) is False

    def test_tenant_isolation(self) -> None:
        """Credentials for one tenant are not accessible by another."""
        repo = InMemoryCredentialRepository()
        repo.store(
            tenant_id="tenant-a",
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "a-key"},
        )

        with pytest.raises(CredentialNotFoundError):
            repo.load(tenant_id="tenant-b", connector_id=CONNECTOR_ID)

    def test_clear(self) -> None:
        repo = InMemoryCredentialRepository()
        repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "test"},
        )
        repo.clear()
        assert repo.exists(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) is False


class TestEncryptionRoundTrip:
    """Encryption/decryption with env key."""

    def test_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "test-encryption-key-32chars!!")
        creds = {"api_key": "secret-value-123", "token": "bearer-xyz"}

        ciphertext = encrypt_credentials(creds)
        assert isinstance(ciphertext, str)
        assert ciphertext != str(creds)

        decrypted = decrypt_credentials(ciphertext)
        assert decrypted == creds

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", raising=False)
        with pytest.raises(EncryptionKeyMissingError):
            encrypt_credentials({"api_key": "test"})

    def test_missing_key_on_decrypt_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", raising=False)
        with pytest.raises(EncryptionKeyMissingError):
            decrypt_credentials("dGVzdA==")

    def test_tampered_ciphertext_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "test-key-for-tamper-check!!")
        creds = {"api_key": "original"}
        ciphertext = encrypt_credentials(creds)

        import base64

        raw = bytearray(base64.b64decode(ciphertext))
        # Tamper with encrypted bytes (between IV and HMAC)
        if len(raw) > 20:
            raw[17] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode("ascii")

        with pytest.raises(ValueError, match="HMAC verification failed"):
            decrypt_credentials(tampered)

    def test_too_short_ciphertext_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "test-key")
        import base64

        short = base64.b64encode(b"too_short").decode("ascii")
        with pytest.raises(ValueError, match="too short"):
            decrypt_credentials(short)

    def test_different_keys_cannot_decrypt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "key-one-for-encrypt!!")
        creds = {"api_key": "secret"}
        ciphertext = encrypt_credentials(creds)

        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "key-two-different!!")
        with pytest.raises(ValueError):
            decrypt_credentials(ciphertext)
