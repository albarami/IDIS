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
    PostgresCredentialRepository,
    decrypt_credentials,
    encrypt_credentials,
    get_enrichment_credentials_repository,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
CONNECTOR_ID = "sec_edgar"


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row


class _FakePostgresConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.rows: dict[tuple[str, str], dict[str, object]] = {}

    def execute(self, statement: object, params: dict[str, object] | None = None) -> _FakeResult:
        sql = str(statement)
        bound = params or {}
        self.statements.append((sql, bound))
        if "INSERT INTO enrichment_credentials" in sql:
            key = (str(bound["tenant_id"]), str(bound["connector_id"]))
            existing = self.rows.get(key)
            self.rows[key] = {
                "tenant_id": bound["tenant_id"],
                "connector_id": bound["connector_id"],
                "ciphertext": bound["ciphertext"],
                "created_at": existing["created_at"] if existing else bound["created_at"],
                "rotated_at": bound["rotated_at"] if existing else None,
                "revoked_at": None,
            }
            return _FakeResult(None)
        if "UPDATE enrichment_credentials" in sql:
            key = (str(bound["tenant_id"]), str(bound["connector_id"]))
            if key in self.rows:
                self.rows[key]["revoked_at"] = bound["revoked_at"]
            return _FakeResult(None)
        if "SELECT ciphertext" in sql:
            row = self.rows.get((str(bound["tenant_id"]), str(bound["connector_id"])))
            if row is None or row.get("revoked_at") is not None:
                return _FakeResult(None)
            return _FakeResult(type("Row", (), row)())
        if "SELECT 1" in sql:
            row = self.rows.get((str(bound["tenant_id"]), str(bound["connector_id"])))
            return _FakeResult(None if row is None or row.get("revoked_at") is not None else (1,))
        if "SELECT tenant_id" in sql:
            row = self.rows.get((str(bound["tenant_id"]), str(bound["connector_id"])))
            return _FakeResult(None if row is None else type("Row", (), row)())
        return _FakeResult(None)


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


class TestPostgresCredentialRepository:
    """Postgres BYOL credential repository operations."""

    def test_store_load_rotate_revoke_and_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "postgres-test-key")
        conn = _FakePostgresConnection()
        repo = PostgresCredentialRepository(conn, TENANT_ID)

        first = repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "v1"},
        )
        assert first.rotated_at is None
        assert repo.exists(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) is True
        assert repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) == {"api_key": "v1"}

        second = repo.store(
            tenant_id=TENANT_ID,
            connector_id=CONNECTOR_ID,
            credentials={"api_key": "v2"},
        )
        assert second.created_at == first.created_at
        assert second.rotated_at is not None
        assert repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) == {"api_key": "v2"}

        revoked = repo.revoke(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)
        assert revoked.revoked_at is not None
        assert repo.exists(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) is False
        with pytest.raises(CredentialNotFoundError):
            repo.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)

        joined_sql = "\n".join(sql for sql, _params in conn.statements)
        assert "SET LOCAL idis.tenant_id" in joined_sql
        assert "INSERT INTO enrichment_credentials" in joined_sql
        assert "ON CONFLICT" in joined_sql

    def test_postgres_repo_requires_encryption_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", raising=False)
        repo = PostgresCredentialRepository(_FakePostgresConnection(), TENANT_ID)

        with pytest.raises(EncryptionKeyMissingError):
            repo.store(
                tenant_id=TENANT_ID,
                connector_id=CONNECTOR_ID,
                credentials={"api_key": "value"},
            )

    def test_factory_uses_postgres_repo_when_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _FakePostgresConnection()
        monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql://example.invalid/app")

        repo = get_enrichment_credentials_repository(conn, TENANT_ID)

        assert isinstance(repo, PostgresCredentialRepository)


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
