"""RED contracts for Task 2's two approved RLS governance repositories."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from tests.slice100_task2._support import (
    ACCEPTANCE_TENANT_ID,
    BUDGET_SCHEMA_VERSION,
    RIGHTS_SCHEMA_VERSION,
    SIGNER_ID,
    budget_payload,
    error_code,
    rights_payload,
)

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
AUDIT_EVENT_ID = "30000000-0000-4000-8000-000000000001"


class _Result:
    def __init__(
        self,
        *,
        rows: list[object] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self) -> list[object]:
        return list(self._rows)

    def fetchone(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> object:
        assert len(self._rows) == 1
        row = self._rows[0]
        if isinstance(row, tuple):
            return row[0]
        return row


class _Connection:
    def __init__(
        self,
        *,
        missing_credential_version: str | None = None,
        unbound_budget_usage: bool = False,
        durable_budget_imported_at: datetime | None = None,
        inactive_budget_policy: bool = False,
    ) -> None:
        self.missing_credential_version = missing_credential_version
        self.unbound_budget_usage = unbound_budget_usage
        self.durable_budget_imported_at = durable_budget_imported_at or NOW
        self.inactive_budget_policy = inactive_budget_policy
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: object, params: Any = None) -> _Result:
        sql = re.sub(r"\s+", " ", str(statement)).strip()
        self.calls.append((sql, params))
        if "FROM enrichment_credentials" in sql:
            version_id = str(params["credential_version_id"])
            if version_id == self.missing_credential_version:
                return _Result()
            return _Result(rows=[(1,)])
        if "FROM provider_budget_usage" in sql:
            if self.unbound_budget_usage and "policy_hash IS NULL" in sql:
                return _Result(rows=[(1,)])
            return _Result(rows=[(0,)])
        if "AS next_imported_at" in sql:
            return _Result(rows=[(self.durable_budget_imported_at,)])
        if sql.startswith("WITH active_policy AS"):
            return _Result(rows=[(not self.inactive_budget_policy,)])
        if sql.startswith("INSERT INTO enrichment_rights_approvals"):
            return _Result(rowcount=len(params) if isinstance(params, list) else 1)
        if sql.startswith("INSERT INTO provider_budget_policies"):
            return _Result(rowcount=len(params) if isinstance(params, list) else 1)
        return _Result()


class _UnknownRowcountConnection(_Connection):
    """Driver double that requires repositories to reselect after unknown rowcount."""

    def execute(self, statement: object, params: Any = None) -> _Result:
        sql = re.sub(r"\s+", " ", str(statement)).strip()
        if sql.startswith("INSERT INTO enrichment_rights_approvals"):
            self.calls.append((sql, params))
            return _Result(rowcount=-1)
        if sql.startswith("INSERT INTO provider_budget_policies"):
            self.calls.append((sql, params))
            return _Result(rowcount=-1)
        if sql.startswith("SELECT import_audit_event_id"):
            self.calls.append((sql, params))
            return _Result(rows=[(AUDIT_EVENT_ID,)])
        return super().execute(statement, params)


def _verified(
    schema_version: str,
    payload: dict[str, object],
    *,
    payload_hash: str = "a" * 64,
    envelope_hash: str = "b" * 64,
) -> object:
    return SimpleNamespace(
        schema_version=schema_version,
        signer_id=SIGNER_ID,
        payload=payload,
        payload_sha256=payload_hash,
        envelope_sha256=envelope_hash,
        public_key_sha256="c" * 64,
    )


class _RightsLifecycleConnection(_Connection):
    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        previous_payload: dict[str, object],
        historical_payloads: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self.rows = {(str(row["provider_id"]), str(row["terms_version_hash"])): row for row in rows}
        self.previous_payload = previous_payload
        self.historical_payloads = historical_payloads or []

    def execute(self, statement: object, params: Any = None) -> _Result:
        sql = re.sub(r"\s+", " ", str(statement)).strip()
        if sql.startswith("SELECT rights_approval_id"):
            self.calls.append((sql, params))
            row = self.rows.get((str(params["provider_id"]), str(params["terms_version_hash"])))
            if row is None:
                return _Result()
            return _Result(rows=[SimpleNamespace(_mapping=row)])
        if sql.startswith("UPDATE enrichment_rights_approvals"):
            self.calls.append((sql, params))
            assert isinstance(params, list)
            for updated in params:
                key = (str(updated["provider_id"]), str(updated["terms_version_hash"]))
                self.rows[key] = {
                    name: value
                    for name, value in updated.items()
                    if name != "previous_import_audit_event_id"
                }
            return _Result(rowcount=len(params))
        if sql.startswith("SELECT EXISTS") and "FROM audit_events" in sql:
            self.calls.append((sql, params))
            candidate = json.loads(str(params["rights_payload_json"]))
            return _Result(rows=[(candidate in self.historical_payloads,)])
        if "FROM audit_events" in sql and "jsonb_array_elements" in sql:
            self.calls.append((sql, params))
            return _Result()
        if "FROM audit_events" in sql:
            self.calls.append((sql, params))
            return _Result(
                rows=[SimpleNamespace(_mapping={"rights_payload": self.previous_payload})]
            )
        return super().execute(statement, params)


class _CrossTermsRevocationConnection(_RightsLifecycleConnection):
    """Lifecycle double exposing the latest revocation across terms versions."""

    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        previous_payload: dict[str, object],
        latest_revocations: dict[tuple[str, str], datetime],
        current_revocations: dict[tuple[str, str], datetime] | None = None,
        historical_payloads: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(
            rows,
            previous_payload=previous_payload,
            historical_payloads=historical_payloads,
        )
        self.latest_revocations = latest_revocations
        self.current_revocations = (
            latest_revocations if current_revocations is None else current_revocations
        )

    def execute(self, statement: object, params: Any = None) -> _Result:
        sql = re.sub(r"\s+", " ", str(statement)).strip()
        if sql.startswith("SELECT revoked_at") and "FROM enrichment_rights_approvals" in sql:
            self.calls.append((sql, params))
            revoked_at = self.current_revocations.get(
                (str(params["provider_id"]), str(params["environment"]))
            )
            if revoked_at is None:
                return _Result()
            return _Result(rows=[SimpleNamespace(_mapping={"revoked_at": revoked_at})])
        if "FROM audit_events" in sql and "jsonb_array_elements" in sql:
            self.calls.append((sql, params))
            revoked_at = self.latest_revocations.get(
                (str(params["provider_id"]), str(params["environment"]))
            )
            if revoked_at is None:
                return _Result()
            return _Result(rows=[SimpleNamespace(_mapping={"revoked_at": revoked_at})])
        return super().execute(statement, params)


def _stored_rights_rows(
    payload: dict[str, object],
    *,
    payload_hash: str,
    envelope_hash: str,
    audit_event_id: str,
) -> list[dict[str, object]]:
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    rows: list[dict[str, object]] = []
    for approval in approvals:
        assert isinstance(approval, dict)
        provider_id = str(approval["provider_id"])
        terms_hash = str(approval["terms_version_hash"])
        rows.append(
            {
                "rights_approval_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"idis:rights:{ACCEPTANCE_TENANT_ID}:{provider_id}:PROD:{terms_hash}",
                    )
                ),
                "tenant_id": ACCEPTANCE_TENANT_ID,
                **approval,
                "signer_id": SIGNER_ID,
                "approver_actor_id": "slice100-governance-admin",
                "approved_at": datetime.fromisoformat(
                    str(approval["approved_at"]).replace("Z", "+00:00")
                ),
                "expires_at": datetime.fromisoformat(
                    str(approval["expires_at"]).replace("Z", "+00:00")
                ),
                "revoked_at": (
                    None
                    if approval.get("revoked_at") is None
                    else datetime.fromisoformat(str(approval["revoked_at"]).replace("Z", "+00:00"))
                ),
                "envelope_hash": envelope_hash,
                "payload_hash": payload_hash,
                "public_key_hash": "c" * 64,
                "import_audit_event_id": audit_event_id,
                "imported_at": NOW,
            }
        )
    return rows


def _rights_repository(connection: object) -> object:
    module = importlib.import_module("idis.persistence.repositories.enrichment_rights_approvals")
    return module.PostgresEnrichmentRightsApprovalsRepository(
        connection,
        ACCEPTANCE_TENANT_ID,
    )


def _budget_repository(connection: object) -> object:
    module = importlib.import_module("idis.persistence.repositories.provider_budget_policies")
    return module.PostgresProviderBudgetPoliciesRepository(
        connection,
        ACCEPTANCE_TENANT_ID,
    )


def test_rights_repository_imports_four_rows_with_exact_credential_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "idis.persistence.db.set_tenant_local",
        lambda *_args: None,
    )
    connection = _Connection()
    repository = _rights_repository(connection)

    count = repository.import_verified(
        verified_envelope=_verified(RIGHTS_SCHEMA_VERSION, rights_payload()),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert repository.is_durable is True
    assert count == 4
    credential_checks = [
        params for sql, params in connection.calls if "FROM enrichment_credentials" in sql
    ]
    assert {params["connector_id"] for params in credential_checks} == {"finnhub", "fmp"}
    insert_sql, insert_params = next(
        (sql, params)
        for sql, params in connection.calls
        if sql.startswith("INSERT INTO enrichment_rights_approvals")
    )
    assert "ON CONFLICT" in insert_sql
    assert isinstance(insert_params, list) and len(insert_params) == 4
    assert {row["provider_id"] for row in insert_params} == {
        "finnhub",
        "fmp",
        "wayback",
        "google_news_rss",
    }
    assert all(row["tenant_id"] == ACCEPTANCE_TENANT_ID for row in insert_params)
    assert all(row["import_audit_event_id"] == AUDIT_EVENT_ID for row in insert_params)
    assert all(row["envelope_hash"] == "b" * 64 for row in insert_params)


def test_rights_repository_fails_before_insert_when_red_credential_version_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    missing = "20000000-0000-4000-8000-000000000002"
    connection = _Connection(missing_credential_version=missing)
    repository = _rights_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(RIGHTS_SCHEMA_VERSION, rights_payload()),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW,
        )

    assert error_code(exc_info.value) == "ENRICHMENT_LICENSE_BLOCKED"
    assert not any(
        sql.startswith("INSERT INTO enrichment_rights_approvals")
        for sql, _params in connection.calls
    )


def test_rights_repository_locks_active_red_credential_until_import_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    connection = _Connection()
    repository = _rights_repository(connection)

    repository.import_verified(
        verified_envelope=_verified(RIGHTS_SCHEMA_VERSION, rights_payload()),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    checks = [sql for sql, _params in connection.calls if "FROM enrichment_credentials" in sql]
    assert checks
    assert all("FOR UPDATE" in sql for sql in checks)


def test_revoked_red_right_does_not_require_credential_to_remain_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    payload = copy.deepcopy(rights_payload())
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    missing = str(revoked["credential_version_id"])
    connection = _Connection(missing_credential_version=missing)
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(RIGHTS_SCHEMA_VERSION, payload),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert imported == 4
    checks = [params for sql, params in connection.calls if "FROM enrichment_credentials" in sql]
    assert {params["connector_id"] for params in checks} == {"fmp"}


def test_rights_repository_transitions_current_snapshot_and_exposes_prior_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    previous_payload = rights_payload()
    previous_event_id = "30000000-0000-4000-8000-000000000081"
    from idis.runtime.governance_envelopes import canonicalize_json

    previous_payload_hash = hashlib.sha256(canonicalize_json(previous_payload)).hexdigest()
    connection = _RightsLifecycleConnection(
        _stored_rights_rows(
            previous_payload,
            payload_hash=previous_payload_hash,
            envelope_hash="2" * 64,
            audit_event_id=previous_event_id,
        ),
        previous_payload=previous_payload,
    )
    next_payload = copy.deepcopy(previous_payload)
    approvals = next_payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(
            RIGHTS_SCHEMA_VERSION,
            next_payload,
            payload_hash="3" * 64,
            envelope_hash="4" * 64,
        ),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert imported == 4
    assert repository.last_import_disposition == "transitioned"
    assert repository.last_import_audit_event_id == AUDIT_EVENT_ID
    assert repository.last_previous_import_audit_event_id == previous_event_id
    assert repository.last_previous_payload == previous_payload
    assert any("FROM audit_events" in sql for sql, _params in connection.calls)
    updates = [
        params
        for sql, params in connection.calls
        if sql.startswith("UPDATE enrichment_rights_approvals")
    ]
    assert len(updates) == 1
    assert len(updates[0]) == 4


def test_rights_repository_exact_current_replay_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anti-rollback guard must not turn an exact current replay into a transition."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    current_payload = rights_payload()
    from idis.runtime.governance_envelopes import canonicalize_json

    current_hash = hashlib.sha256(canonicalize_json(current_payload)).hexdigest()
    current_event_id = "30000000-0000-4000-8000-000000000083"
    connection = _RightsLifecycleConnection(
        _stored_rights_rows(
            current_payload,
            payload_hash=current_hash,
            envelope_hash="8" * 64,
            audit_event_id=current_event_id,
        ),
        previous_payload=current_payload,
        historical_payloads=[current_payload],
    )
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(
            RIGHTS_SCHEMA_VERSION,
            current_payload,
            payload_hash=current_hash,
            envelope_hash="8" * 64,
        ),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert imported == 4
    assert repository.last_import_disposition == "replayed"
    assert repository.last_import_audit_event_id == current_event_id
    assert not any(sql.startswith(("INSERT INTO", "UPDATE ")) for sql, _params in connection.calls)


def test_rights_repository_rejects_historical_approval_after_revocation_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-valid old signature cannot roll a durable revocation back."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    original_payload = rights_payload()
    revoked_payload = copy.deepcopy(original_payload)
    approvals = revoked_payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    from idis.runtime.governance_envelopes import canonicalize_json

    revoked_hash = hashlib.sha256(canonicalize_json(revoked_payload)).hexdigest()
    original_hash = hashlib.sha256(canonicalize_json(original_payload)).hexdigest()
    connection = _RightsLifecycleConnection(
        _stored_rights_rows(
            revoked_payload,
            payload_hash=revoked_hash,
            envelope_hash="9" * 64,
            audit_event_id="30000000-0000-4000-8000-000000000084",
        ),
        previous_payload=revoked_payload,
        historical_payloads=[original_payload, revoked_payload],
    )
    repository = _rights_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(
                RIGHTS_SCHEMA_VERSION,
                original_payload,
                payload_hash=original_hash,
                envelope_hash="a" * 64,
            ),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW + timedelta(seconds=1),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not any(sql.startswith(("INSERT INTO", "UPDATE ")) for sql, _params in connection.calls)


def test_rights_repository_allows_new_reapproval_strictly_after_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new signed approval remains possible when its effective time advances."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    original_payload = rights_payload()
    revoked_payload = copy.deepcopy(original_payload)
    approvals = revoked_payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    reapproved_payload = copy.deepcopy(original_payload)
    reapprovals = reapproved_payload["approvals"]
    assert isinstance(reapprovals, list)
    reapproved = reapprovals[0]
    assert isinstance(reapproved, dict)
    reapproved["approved_at"] = "2026-07-15T12:00:01Z"
    from idis.runtime.governance_envelopes import canonicalize_json

    revoked_hash = hashlib.sha256(canonicalize_json(revoked_payload)).hexdigest()
    reapproved_hash = hashlib.sha256(canonicalize_json(reapproved_payload)).hexdigest()
    connection = _RightsLifecycleConnection(
        _stored_rights_rows(
            revoked_payload,
            payload_hash=revoked_hash,
            envelope_hash="b" * 64,
            audit_event_id="30000000-0000-4000-8000-000000000085",
        ),
        previous_payload=revoked_payload,
        historical_payloads=[original_payload, revoked_payload],
    )
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(
            RIGHTS_SCHEMA_VERSION,
            reapproved_payload,
            payload_hash=reapproved_hash,
            envelope_hash="c" * 64,
        ),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW + timedelta(seconds=1),
    )

    assert imported == 4
    assert repository.last_import_disposition == "transitioned"
    assert any(
        sql.startswith("UPDATE enrichment_rights_approvals") for sql, _params in connection.calls
    )


def test_rights_repository_rejects_backdated_reapproval_after_terms_version_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new terms hash cannot bypass the latest durable provider revocation clock."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    original_payload = rights_payload()
    revoked_payload = copy.deepcopy(original_payload)
    revoked_at = NOW
    revoked_approvals = revoked_payload["approvals"]
    assert isinstance(revoked_approvals, list)
    for approval in revoked_approvals:
        assert isinstance(approval, dict)
        approval["status"] = "REVOKED"
        approval["revoked_at"] = revoked_at.isoformat().replace("+00:00", "Z")

    rotated_payload = copy.deepcopy(original_payload)
    rotated_approvals = rotated_payload["approvals"]
    assert isinstance(rotated_approvals, list)
    latest_revocations: dict[tuple[str, str], datetime] = {}
    for index, approval in enumerate(rotated_approvals, start=1):
        assert isinstance(approval, dict)
        approval["terms_version_hash"] = f"{index:064x}"
        latest_revocations[(str(approval["provider_id"]), str(approval["environment"]))] = (
            revoked_at
        )

    from idis.runtime.governance_envelopes import canonicalize_json

    revoked_hash = hashlib.sha256(canonicalize_json(revoked_payload)).hexdigest()
    rotated_hash = hashlib.sha256(canonicalize_json(rotated_payload)).hexdigest()
    connection = _CrossTermsRevocationConnection(
        _stored_rights_rows(
            revoked_payload,
            payload_hash=revoked_hash,
            envelope_hash="d" * 64,
            audit_event_id="30000000-0000-4000-8000-000000000086",
        ),
        previous_payload=revoked_payload,
        latest_revocations=latest_revocations,
        historical_payloads=[original_payload, revoked_payload],
    )
    repository = _rights_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(
                RIGHTS_SCHEMA_VERSION,
                rotated_payload,
                payload_hash=rotated_hash,
                envelope_hash="e" * 64,
            ),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW + timedelta(seconds=1),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not any(sql.startswith(("INSERT INTO", "UPDATE ")) for sql, _params in connection.calls)


def test_rights_repository_allows_later_reapproval_after_terms_version_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely later signed approval remains valid under a new terms hash."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    original_payload = rights_payload()
    revoked_payload = copy.deepcopy(original_payload)
    revoked_at = NOW
    revoked_approvals = revoked_payload["approvals"]
    assert isinstance(revoked_approvals, list)
    for approval in revoked_approvals:
        assert isinstance(approval, dict)
        approval["status"] = "REVOKED"
        approval["revoked_at"] = revoked_at.isoformat().replace("+00:00", "Z")

    rotated_payload = copy.deepcopy(original_payload)
    rotated_approvals = rotated_payload["approvals"]
    assert isinstance(rotated_approvals, list)
    latest_revocations: dict[tuple[str, str], datetime] = {}
    for index, approval in enumerate(rotated_approvals, start=1):
        assert isinstance(approval, dict)
        approval["terms_version_hash"] = f"{index:064x}"
        approval["approved_at"] = (
            (revoked_at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        )
        latest_revocations[(str(approval["provider_id"]), str(approval["environment"]))] = (
            revoked_at
        )

    from idis.runtime.governance_envelopes import canonicalize_json

    revoked_hash = hashlib.sha256(canonicalize_json(revoked_payload)).hexdigest()
    rotated_hash = hashlib.sha256(canonicalize_json(rotated_payload)).hexdigest()
    connection = _CrossTermsRevocationConnection(
        _stored_rights_rows(
            revoked_payload,
            payload_hash=revoked_hash,
            envelope_hash="f" * 64,
            audit_event_id="30000000-0000-4000-8000-000000000087",
        ),
        previous_payload=revoked_payload,
        latest_revocations=latest_revocations,
    )
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(
            RIGHTS_SCHEMA_VERSION,
            rotated_payload,
            payload_hash=rotated_hash,
            envelope_hash="1" * 64,
        ),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW + timedelta(seconds=2),
    )

    assert imported == 4
    assert repository.last_import_disposition == "created"
    revocation_queries = [
        sql
        for sql, _params in connection.calls
        if sql.startswith("SELECT revoked_at") and "FROM enrichment_rights_approvals" in sql
    ]
    assert len(revocation_queries) == 4
    assert all("FOR UPDATE" in sql for sql in revocation_queries)


def test_rights_repository_retains_revocation_clock_after_same_terms_reapproval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later row update cannot erase the append-only revocation authority."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    original_payload = rights_payload()
    revoked_payload = copy.deepcopy(original_payload)
    revoked_at = NOW
    revoked_approvals = revoked_payload["approvals"]
    assert isinstance(revoked_approvals, list)
    for approval in revoked_approvals:
        assert isinstance(approval, dict)
        approval["status"] = "REVOKED"
        approval["revoked_at"] = revoked_at.isoformat().replace("+00:00", "Z")

    current_payload = copy.deepcopy(original_payload)
    current_approvals = current_payload["approvals"]
    assert isinstance(current_approvals, list)
    latest_revocations: dict[tuple[str, str], datetime] = {}
    for approval in current_approvals:
        assert isinstance(approval, dict)
        approval["approved_at"] = (
            (revoked_at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        )
        latest_revocations[(str(approval["provider_id"]), str(approval["environment"]))] = (
            revoked_at
        )

    rotated_backdated = copy.deepcopy(original_payload)
    rotated_approvals = rotated_backdated["approvals"]
    assert isinstance(rotated_approvals, list)
    for index, approval in enumerate(rotated_approvals, start=1):
        assert isinstance(approval, dict)
        approval["terms_version_hash"] = f"{index:064x}"

    from idis.runtime.governance_envelopes import canonicalize_json

    current_hash = hashlib.sha256(canonicalize_json(current_payload)).hexdigest()
    rotated_hash = hashlib.sha256(canonicalize_json(rotated_backdated)).hexdigest()
    connection = _CrossTermsRevocationConnection(
        _stored_rights_rows(
            current_payload,
            payload_hash=current_hash,
            envelope_hash="2" * 64,
            audit_event_id="30000000-0000-4000-8000-000000000088",
        ),
        previous_payload=current_payload,
        latest_revocations=latest_revocations,
        current_revocations={},
        historical_payloads=[original_payload, revoked_payload, current_payload],
    )
    repository = _rights_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(
                RIGHTS_SCHEMA_VERSION,
                rotated_backdated,
                payload_hash=rotated_hash,
                envelope_hash="3" * 64,
            ),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW + timedelta(seconds=2),
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert any("jsonb_array_elements" in sql for sql, _params in connection.calls)
    assert not any(sql.startswith(("INSERT INTO", "UPDATE ")) for sql, _params in connection.calls)


def test_rights_transition_matches_prior_exact_payload_by_composite_key_not_list_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical array order is signed, but snapshot identity is the approved DB key."""
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    previous_payload = rights_payload()
    previous_event_id = "30000000-0000-4000-8000-000000000082"
    from idis.runtime.governance_envelopes import canonicalize_json

    previous_payload_hash = hashlib.sha256(canonicalize_json(previous_payload)).hexdigest()
    connection = _RightsLifecycleConnection(
        _stored_rights_rows(
            previous_payload,
            payload_hash=previous_payload_hash,
            envelope_hash="5" * 64,
            audit_event_id=previous_event_id,
        ),
        previous_payload=previous_payload,
    )
    next_payload = copy.deepcopy(previous_payload)
    approvals = next_payload["approvals"]
    assert isinstance(approvals, list)
    approvals.reverse()
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"
    repository = _rights_repository(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(
            RIGHTS_SCHEMA_VERSION,
            next_payload,
            payload_hash="6" * 64,
            envelope_hash="7" * 64,
        ),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert imported == 4
    assert repository.last_import_disposition == "transitioned"
    assert repository.last_previous_payload == previous_payload


def test_budget_repository_imports_exactly_seventeen_policy_rows_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    connection = _Connection()
    repository = _budget_repository(connection)

    count = repository.import_verified(
        verified_envelope=_verified(BUDGET_SCHEMA_VERSION, budget_payload()),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert repository.is_durable is True
    assert count == 17
    insert_sql, insert_params = next(
        (sql, params)
        for sql, params in connection.calls
        if sql.startswith("INSERT INTO provider_budget_policies")
    )
    assert "ON CONFLICT" in insert_sql
    assert isinstance(insert_params, list) and len(insert_params) == 17
    assert len({row["policy_hash"] for row in insert_params}) == 1
    assert len({row["policy_version"] for row in insert_params}) == 1
    assert {row["component_id"] for row in insert_params} == set(budget_payload()["components"])
    assert all(row["currency"] == "USD" for row in insert_params)
    assert all(row["import_audit_event_id"] == AUDIT_EVENT_ID for row in insert_params)
    assert not any(
        sql.startswith("UPDATE provider_budget_policies") for sql, _params in connection.calls
    )


def test_budget_repository_uses_database_monotonic_time_and_proves_active_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    durable_time = NOW + timedelta(seconds=30)
    connection = _Connection(durable_budget_imported_at=durable_time)
    repository = _budget_repository(connection)

    repository.import_verified(
        verified_envelope=_verified(BUDGET_SCHEMA_VERSION, budget_payload()),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW - timedelta(days=30),
    )

    insert_params = next(
        params
        for sql, params in connection.calls
        if sql.startswith("INSERT INTO provider_budget_policies")
    )
    assert isinstance(insert_params, list)
    assert {row["imported_at"] for row in insert_params} == {durable_time}
    assert any("AS next_imported_at" in sql for sql, _params in connection.calls)
    assert any(sql.startswith("WITH active_policy AS") for sql, _params in connection.calls)


def test_budget_repository_rejects_signed_replay_that_is_not_the_active_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    connection = _Connection(inactive_budget_policy=True)
    repository = _budget_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(BUDGET_SCHEMA_VERSION, budget_payload()),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW,
        )

    assert error_code(exc_info.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"


def test_budget_repository_rejects_unbound_legacy_usage_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    connection = _Connection(unbound_budget_usage=True)
    repository = _budget_repository(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(BUDGET_SCHEMA_VERSION, budget_payload()),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW,
        )

    assert error_code(exc_info.value) == "PROVIDER_BUDGET_CONFIG_BLOCKED"
    assert not any(
        sql.startswith("INSERT INTO provider_budget_policies") for sql, _params in connection.calls
    )


@pytest.mark.parametrize(
    ("factory", "schema_version", "payload"),
    [
        (_rights_repository, RIGHTS_SCHEMA_VERSION, rights_payload()),
        (_budget_repository, BUDGET_SCHEMA_VERSION, budget_payload()),
    ],
)
def test_unknown_driver_rowcount_is_verified_by_exact_durable_reselect(
    monkeypatch: pytest.MonkeyPatch,
    factory: Any,
    schema_version: str,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    connection = _UnknownRowcountConnection()
    repository = factory(connection)

    imported = repository.import_verified(
        verified_envelope=_verified(schema_version, payload),
        imported_by_actor_id="slice100-governance-admin",
        import_audit_event_id=AUDIT_EVENT_ID,
        imported_at=NOW,
    )

    assert imported == (4 if schema_version == RIGHTS_SCHEMA_VERSION else 17)
    assert any(sql.startswith("SELECT import_audit_event_id") for sql, _params in connection.calls)


@pytest.mark.parametrize(
    ("factory", "schema_version", "payload"),
    [
        (_rights_repository, RIGHTS_SCHEMA_VERSION, rights_payload()),
        (_budget_repository, BUDGET_SCHEMA_VERSION, budget_payload()),
    ],
)
def test_governance_repositories_reject_cross_tenant_payload_before_insert(
    monkeypatch: pytest.MonkeyPatch,
    factory: Any,
    schema_version: str,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr("idis.persistence.db.set_tenant_local", lambda *_args: None)
    payload = copy_payload = dict(payload)
    copy_payload["tenant_id"] = "10000000-0000-4000-8000-000000000099"
    connection = _Connection()
    repository = factory(connection)

    with pytest.raises(Exception) as exc_info:
        repository.import_verified(
            verified_envelope=_verified(schema_version, payload),
            imported_by_actor_id="slice100-governance-admin",
            import_audit_event_id=AUDIT_EVENT_ID,
            imported_at=NOW,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not any(sql.startswith("INSERT INTO") for sql, _params in connection.calls)
