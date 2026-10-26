"""RED contracts for non-destructive BYOL credential rotation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from idis.persistence.repositories.enrichment_credentials import (
    PostgresCredentialRepository,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
CONNECTOR_ID = "finnhub"


class _Result:
    def __init__(self, row: object | None = None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row


class _VersionedCredentialConnection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = re.sub(r"\s+", " ", str(statement)).strip()
        bound = dict(params or {})
        self.statements.append((sql, bound))

        if sql.startswith("SELECT tenant_id"):
            active = self._active(bound)
            return _Result(None if active is None else SimpleNamespace(**active))
        if sql.startswith("SELECT ciphertext"):
            active = self._active(bound)
            return _Result(None if active is None else SimpleNamespace(**active))
        if sql.startswith("SELECT 1"):
            return _Result(None if self._active(bound) is None else (1,))
        if sql.startswith("UPDATE enrichment_credentials") and "SET revoked_at" in sql:
            active = self._active(bound)
            if active is not None:
                active["revoked_at"] = bound["revoked_at"]
            return _Result()
        if sql.startswith("UPDATE enrichment_credentials") and "SET rotated_at" in sql:
            active = self._active(bound)
            if active is not None:
                active["rotated_at"] = bound["rotated_at"]
            return _Result()
        if sql.startswith("INSERT INTO enrichment_credentials"):
            self.rows.append(
                {
                    "tenant_id": bound["tenant_id"],
                    "connector_id": bound["connector_id"],
                    "credential_version_id": bound["credential_version_id"],
                    "ciphertext": bound["ciphertext"],
                    "created_at": bound["created_at"],
                    "rotated_at": None,
                    "revoked_at": None,
                }
            )
            return _Result()
        return _Result()

    def _active(self, params: dict[str, Any]) -> dict[str, Any] | None:
        tenant_id = str(params.get("tenant_id", TENANT_ID))
        connector_id = str(params.get("connector_id", CONNECTOR_ID))
        return next(
            (
                row
                for row in reversed(self.rows)
                if str(row["tenant_id"]) == tenant_id
                and str(row["connector_id"]) == connector_id
                and row["rotated_at"] is None
                and row["revoked_at"] is None
            ),
            None,
        )


def test_postgres_rotation_preserves_ciphertext_and_returns_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "task2-version-lineage-key")
    monkeypatch.setattr(
        "idis.persistence.repositories.enrichment_credentials.set_tenant_local",
        lambda *_args: None,
    )
    connection = _VersionedCredentialConnection()
    repository = PostgresCredentialRepository(connection, TENANT_ID)

    first = repository.store(
        tenant_id=TENANT_ID,
        connector_id=CONNECTOR_ID,
        credentials={"api_key": "first-secret"},
    )
    second = repository.store(
        tenant_id=TENANT_ID,
        connector_id=CONNECTOR_ID,
        credentials={"api_key": "second-secret"},
    )

    assert first.credential_version_id != second.credential_version_id
    assert first.credential_version_id is not None
    assert second.credential_version_id is not None
    assert len(connection.rows) == 2
    assert connection.rows[0]["ciphertext"] != connection.rows[1]["ciphertext"]
    assert connection.rows[0]["rotated_at"] is not None
    assert connection.rows[0]["revoked_at"] is None
    assert connection.rows[1]["rotated_at"] is None
    assert connection.rows[1]["revoked_at"] is None
    assert second.created_at == connection.rows[1]["created_at"]
    assert second.rotated_at is connection.rows[1]["rotated_at"] is None
    assert second.revoked_at is connection.rows[1]["revoked_at"] is None
    assert repository.load(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID) == {
        "api_key": "second-secret"
    }
    combined_sql = "\n".join(sql for sql, _params in connection.statements)
    assert "ON CONFLICT" not in combined_sql
    assert "SET rotated_at" in combined_sql


def test_revoke_targets_only_the_active_credential_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "task2-version-lineage-key")
    monkeypatch.setattr(
        "idis.persistence.repositories.enrichment_credentials.set_tenant_local",
        lambda *_args: None,
    )
    connection = _VersionedCredentialConnection()
    repository = PostgresCredentialRepository(connection, TENANT_ID)
    repository.store(
        tenant_id=TENANT_ID,
        connector_id=CONNECTOR_ID,
        credentials={"api_key": "first-secret"},
    )
    active = repository.store(
        tenant_id=TENANT_ID,
        connector_id=CONNECTOR_ID,
        credentials={"api_key": "second-secret"},
    )

    revoked = repository.revoke(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)

    assert revoked.credential_version_id == active.credential_version_id
    assert connection.rows[0]["revoked_at"] is None
    assert connection.rows[1]["revoked_at"] is not None
    update_sql, update_params = next(
        (sql, params)
        for sql, params in connection.statements
        if sql.startswith("UPDATE enrichment_credentials") and "SET revoked_at" in sql
    )
    assert "credential_version_id" in update_sql
    assert update_params["credential_version_id"] == active.credential_version_id


def test_active_credential_metadata_exposes_version_for_rights_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_ENRICHMENT_ENCRYPTION_KEY", "task2-version-lineage-key")
    monkeypatch.setattr(
        "idis.persistence.repositories.enrichment_credentials.set_tenant_local",
        lambda *_args: None,
    )
    connection = _VersionedCredentialConnection()
    repository = PostgresCredentialRepository(connection, TENANT_ID)
    stored = repository.store(
        tenant_id=TENANT_ID,
        connector_id=CONNECTOR_ID,
        credentials={"api_key": "secret"},
    )

    metadata = repository.active_metadata(tenant_id=TENANT_ID, connector_id=CONNECTOR_ID)

    assert metadata.credential_version_id == stored.credential_version_id
    assert metadata.rotated_at is None
    assert metadata.revoked_at is None
    assert isinstance(metadata.created_at, datetime)
    assert metadata.created_at.tzinfo is UTC
