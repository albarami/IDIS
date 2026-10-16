"""Durable tenant-scoped imports for governed enrichment rights approvals."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.persistence import db

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from idis.runtime.governance_envelopes import VerifiedGovernanceEnvelope


class RightsApprovalPersistenceError(RuntimeError):
    """Safe failure raised before an invalid rights row can become active."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ACTIVE_CREDENTIAL_SQL = text(
    """
    SELECT 1
    FROM enrichment_credentials
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND connector_id = :connector_id
      AND credential_version_id = CAST(:credential_version_id AS UUID)
      AND rotated_at IS NULL
      AND revoked_at IS NULL
    FOR UPDATE
    """
)

_SERIALIZE_RIGHTS_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))")

_LOCK_CURRENT_SQL = text(
    """
    SELECT rights_approval_id::text AS rights_approval_id,
           tenant_id::text AS tenant_id, provider_id, environment,
           terms_version_hash, status, rights_class, license_scope,
           attribution_policy_version, no_store_policy_version, evidence_hash,
           signer_id, approver_actor_id, approved_at, expires_at, revoked_at,
           credential_version_id::text AS credential_version_id,
           envelope_hash, payload_hash, public_key_hash,
           import_audit_event_id::text AS import_audit_event_id, imported_at
    FROM enrichment_rights_approvals
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND provider_id = :provider_id
      AND environment = :environment
      AND terms_version_hash = :terms_version_hash
    FOR UPDATE
    """
)

_LOCK_LATEST_REVOCATION_SQL = text(
    """
    SELECT revoked_at
    FROM enrichment_rights_approvals
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND provider_id = :provider_id
      AND environment = :environment
      AND revoked_at IS NOT NULL
    ORDER BY revoked_at DESC, rights_approval_id
    LIMIT 1
    FOR UPDATE
    """
)

_LATEST_AUDIT_REVOCATION_SQL = text(
    """
    SELECT (approval ->> 'revoked_at')::TIMESTAMPTZ AS revoked_at
    FROM audit_events AS governance_event
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(
                governance_event.event -> 'payload' -> 'safe'
                    -> 'rights_payload' -> 'approvals'
            ) = 'array'
            THEN governance_event.event -> 'payload' -> 'safe'
                -> 'rights_payload' -> 'approvals'
            ELSE '[]'::JSONB
        END
    ) AS approval
    WHERE governance_event.tenant_id = CAST(:tenant_id AS UUID)
      AND governance_event.event_type IN (
          'integration.governance.imported',
          'integration.governance.replayed'
      )
      AND jsonb_typeof(approval) = 'object'
      AND approval ->> 'provider_id' = :provider_id
      AND approval ->> 'environment' = :environment
      AND approval ->> 'revoked_at' IS NOT NULL
    ORDER BY (approval ->> 'revoked_at')::TIMESTAMPTZ DESC,
             governance_event.event_id DESC
    LIMIT 1
    """
)

_LOAD_PRIOR_AUDIT_SQL = text(
    """
    SELECT event -> 'payload' -> 'safe' -> 'rights_payload' AS rights_payload
    FROM audit_events
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND event_id = CAST(:event_id AS UUID)
      AND event_type = 'integration.governance.imported'
    """
)

_HISTORICAL_RIGHTS_PAYLOAD_SQL = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM audit_events
        WHERE tenant_id = CAST(:tenant_id AS UUID)
          AND event_type IN (
              'integration.governance.imported',
              'integration.governance.replayed'
          )
          AND event -> 'payload' -> 'safe' -> 'rights_payload'
              = CAST(:rights_payload_json AS JSONB)
    ) AS historical_payload_seen
    """
)

_IMPORT_SQL = text(
    """
    INSERT INTO enrichment_rights_approvals (
        rights_approval_id, tenant_id, provider_id, environment,
        terms_version_hash, status, rights_class, license_scope,
        attribution_policy_version, no_store_policy_version, evidence_hash,
        signer_id, approver_actor_id, approved_at, expires_at, revoked_at,
        credential_version_id, envelope_hash, payload_hash, public_key_hash,
        import_audit_event_id, imported_at
    ) VALUES (
        CAST(:rights_approval_id AS UUID), CAST(:tenant_id AS UUID), :provider_id,
        :environment, :terms_version_hash, :status, :rights_class, :license_scope,
        :attribution_policy_version, :no_store_policy_version, :evidence_hash,
        :signer_id, :approver_actor_id, :approved_at, :expires_at, :revoked_at,
        CAST(:credential_version_id AS UUID), :envelope_hash, :payload_hash,
        :public_key_hash, CAST(:import_audit_event_id AS UUID), :imported_at
    )
    ON CONFLICT (tenant_id, provider_id, environment, terms_version_hash)
    DO NOTHING
    """
)

_VERIFY_ROW_SQL = text(
    """
    SELECT import_audit_event_id
    FROM enrichment_rights_approvals
    WHERE rights_approval_id = CAST(:rights_approval_id AS UUID)
      AND tenant_id = CAST(:tenant_id AS UUID)
      AND provider_id = :provider_id
      AND environment = :environment
      AND terms_version_hash = :terms_version_hash
      AND status = :status
      AND rights_class = :rights_class
      AND license_scope = :license_scope
      AND attribution_policy_version IS NOT DISTINCT FROM :attribution_policy_version
      AND no_store_policy_version IS NOT DISTINCT FROM :no_store_policy_version
      AND evidence_hash = :evidence_hash
      AND signer_id = :signer_id
      AND approver_actor_id = :approver_actor_id
      AND approved_at = :approved_at
      AND expires_at = :expires_at
      AND revoked_at IS NOT DISTINCT FROM :revoked_at
      AND credential_version_id IS NOT DISTINCT FROM CAST(:credential_version_id AS UUID)
      AND envelope_hash = :envelope_hash
      AND payload_hash = :payload_hash
      AND public_key_hash = :public_key_hash
    """
)

_UPDATE_SQL = text(
    """
    UPDATE enrichment_rights_approvals
    SET status = :status,
        rights_class = :rights_class,
        license_scope = :license_scope,
        attribution_policy_version = :attribution_policy_version,
        no_store_policy_version = :no_store_policy_version,
        evidence_hash = :evidence_hash,
        signer_id = :signer_id,
        approver_actor_id = :approver_actor_id,
        approved_at = :approved_at,
        expires_at = :expires_at,
        revoked_at = :revoked_at,
        credential_version_id = CAST(:credential_version_id AS UUID),
        envelope_hash = :envelope_hash,
        payload_hash = :payload_hash,
        public_key_hash = :public_key_hash,
        import_audit_event_id = CAST(:import_audit_event_id AS UUID),
        imported_at = :imported_at
    WHERE rights_approval_id = CAST(:rights_approval_id AS UUID)
      AND tenant_id = CAST(:tenant_id AS UUID)
      AND import_audit_event_id = CAST(:previous_import_audit_event_id AS UUID)
    """
)

_SNAPSHOT_MATCH_FIELDS = (
    "rights_approval_id",
    "tenant_id",
    "provider_id",
    "environment",
    "terms_version_hash",
    "status",
    "rights_class",
    "license_scope",
    "attribution_policy_version",
    "no_store_policy_version",
    "evidence_hash",
    "signer_id",
    "approver_actor_id",
    "approved_at",
    "expires_at",
    "revoked_at",
    "credential_version_id",
    "envelope_hash",
    "payload_hash",
    "public_key_hash",
)

_APPROVAL_PAYLOAD_FIELDS = (
    "provider_id",
    "environment",
    "terms_version_hash",
    "status",
    "rights_class",
    "license_scope",
    "attribution_policy_version",
    "no_store_policy_version",
    "evidence_hash",
    "approved_at",
    "expires_at",
    "revoked_at",
    "credential_version_id",
)


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID") from exc
    if parsed.tzinfo is None:
        raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
    return parsed


def _row_mapping(row: object) -> dict[str, object]:
    mapping = getattr(row, "_mapping", None)
    if isinstance(mapping, Mapping):
        return {str(key): value for key, value in mapping.items()}
    if isinstance(row, Mapping):
        return {str(key): value for key, value in row.items()}
    raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")


def _comparison_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        return value.astimezone(UTC)
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


class PostgresEnrichmentRightsApprovalsRepository:
    """Append-or-prove-identical rights approval import repository."""

    is_durable = True

    def __init__(self, connection: Connection, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self.last_import_audit_event_id: str | None = None
        self.last_import_was_new = False
        self.last_import_disposition = "uninitialized"
        self.last_previous_import_audit_event_id: str | None = None
        self.last_previous_payload: dict[str, object] | None = None
        db.set_tenant_local(connection, tenant_id)

    def import_verified(
        self,
        *,
        verified_envelope: VerifiedGovernanceEnvelope,
        imported_by_actor_id: str,
        import_audit_event_id: str,
        imported_at: datetime,
    ) -> int:
        """Import the four verified rows atomically in the caller's transaction."""
        schema_version = getattr(verified_envelope, "schema_version", None)
        payload = getattr(verified_envelope, "payload", None)
        if schema_version != "enrichment-rights-approvals-v1" or not isinstance(payload, Mapping):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        if payload.get("tenant_id") != self._tenant_id:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        approvals = payload.get("approvals")
        if not isinstance(approvals, list) or len(approvals) != 4:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")

        self.last_import_audit_event_id = None
        self.last_import_was_new = False
        self.last_import_disposition = "uninitialized"
        self.last_previous_import_audit_event_id = None
        self.last_previous_payload = None
        self._connection.execute(
            _SERIALIZE_RIGHTS_SQL,
            {"lock_key": f"idis:enrichment-rights:{self._tenant_id}"},
        )

        for approval in approvals:
            if not isinstance(approval, Mapping):
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            credential_version_id = approval.get("credential_version_id")
            if approval.get("rights_class") == "RED" and approval.get("status") == "APPROVED":
                result = self._connection.execute(
                    _ACTIVE_CREDENTIAL_SQL,
                    {
                        "tenant_id": self._tenant_id,
                        "connector_id": approval.get("provider_id"),
                        "credential_version_id": credential_version_id,
                    },
                )
                if result.fetchone() is None:
                    raise RightsApprovalPersistenceError("ENRICHMENT_LICENSE_BLOCKED")

        rows = [
            self._row(
                approval=approval,
                verified_envelope=verified_envelope,
                imported_by_actor_id=imported_by_actor_id,
                import_audit_event_id=import_audit_event_id,
                imported_at=imported_at,
            )
            for approval in approvals
        ]
        self._require_approvals_after_latest_revocation(rows)
        existing_rows = [self._load_current(row) for row in rows]
        if all(existing is None for existing in existing_rows):
            self._insert_new(rows, import_audit_event_id=import_audit_event_id)
            return len(rows)
        if any(existing is None for existing in existing_rows):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")

        current_rows = [existing for existing in existing_rows if existing is not None]
        lineage_ids = {str(existing.get("import_audit_event_id")) for existing in current_rows}
        if len(lineage_ids) != 1:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        previous_event_id = next(iter(lineage_ids))
        if all(
            self._matches(existing, desired)
            for existing, desired in zip(current_rows, rows, strict=True)
        ):
            self.last_import_audit_event_id = previous_event_id
            self.last_import_was_new = False
            self.last_import_disposition = "replayed"
            return len(rows)

        previous_payload_hashes = {str(existing.get("payload_hash")) for existing in current_rows}
        if len(previous_payload_hashes) != 1:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        previous_payload = self._load_previous_payload(
            previous_event_id,
            expected_payload_hash=next(iter(previous_payload_hashes)),
            current_rows=current_rows,
        )
        self._require_monotonic_reapproval(current_rows=current_rows, desired_rows=rows)
        if not self._payload_matches_rows(payload, current_rows) and self._historical_payload_seen(
            payload
        ):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        updates = [
            {**desired, "previous_import_audit_event_id": previous_event_id} for desired in rows
        ]
        result = self._connection.execute(_UPDATE_SQL, updates)
        rowcount = getattr(result, "rowcount", len(updates))
        if rowcount not in (-1, len(updates)):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        if rowcount == -1:
            resulting_ids = self._verify_rows(rows)
            if resulting_ids != {import_audit_event_id}:
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        self.last_import_audit_event_id = import_audit_event_id
        self.last_import_was_new = True
        self.last_import_disposition = "transitioned"
        self.last_previous_import_audit_event_id = previous_event_id
        self.last_previous_payload = previous_payload
        return len(rows)

    def _require_approvals_after_latest_revocation(
        self,
        desired_rows: list[dict[str, object]],
    ) -> None:
        """Prevent a terms-version change from bypassing a durable revocation."""
        for desired in desired_rows:
            if desired.get("status") != "APPROVED":
                continue
            query_values = {
                "tenant_id": self._tenant_id,
                "provider_id": desired.get("provider_id"),
                "environment": desired.get("environment"),
            }
            revocation_times: list[datetime] = []
            for query in (_LOCK_LATEST_REVOCATION_SQL, _LATEST_AUDIT_REVOCATION_SQL):
                latest = self._connection.execute(query, query_values).fetchone()
                if latest is None:
                    continue
                revoked_at = _row_mapping(latest).get("revoked_at")
                if not isinstance(revoked_at, datetime) or revoked_at.tzinfo is None:
                    raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
                revocation_times.append(revoked_at.astimezone(UTC))
            if not revocation_times:
                continue
            revoked_at = max(revocation_times)
            approved_at = desired.get("approved_at")
            if (
                not isinstance(approved_at, datetime)
                or approved_at.tzinfo is None
                or approved_at.astimezone(UTC) <= revoked_at
            ):
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")

    def _insert_new(
        self,
        rows: list[dict[str, object]],
        *,
        import_audit_event_id: str,
    ) -> None:
        result = self._connection.execute(_IMPORT_SQL, rows)
        rowcount = getattr(result, "rowcount", len(rows))
        if rowcount == len(rows):
            lineage_id = import_audit_event_id
        elif rowcount in (-1, 0):
            existing_ids = self._verify_rows(rows)
            if len(existing_ids) != 1:
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            lineage_id = next(iter(existing_ids))
        else:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        self.last_import_audit_event_id = lineage_id
        self.last_import_was_new = lineage_id == import_audit_event_id
        self.last_import_disposition = "created" if self.last_import_was_new else "replayed"

    def _load_current(self, desired: Mapping[str, object]) -> dict[str, object] | None:
        result = self._connection.execute(
            _LOCK_CURRENT_SQL,
            {
                "tenant_id": self._tenant_id,
                "provider_id": desired["provider_id"],
                "environment": desired["environment"],
                "terms_version_hash": desired["terms_version_hash"],
            },
        )
        row = result.fetchone()
        return None if row is None else _row_mapping(row)

    @staticmethod
    def _matches(existing: Mapping[str, object], desired: Mapping[str, object]) -> bool:
        return all(
            _comparison_value(existing.get(field)) == _comparison_value(desired.get(field))
            for field in _SNAPSHOT_MATCH_FIELDS
        )

    def _load_previous_payload(
        self,
        previous_event_id: str,
        *,
        expected_payload_hash: str,
        current_rows: list[dict[str, object]],
    ) -> dict[str, object]:
        result = self._connection.execute(
            _LOAD_PRIOR_AUDIT_SQL,
            {"tenant_id": self._tenant_id, "event_id": previous_event_id},
        )
        row = result.fetchone()
        if row is None:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        payload_value = _row_mapping(row).get("rights_payload")
        if isinstance(payload_value, str):
            try:
                payload_value = json.loads(payload_value)
            except json.JSONDecodeError as exc:
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID") from exc
        if not isinstance(payload_value, Mapping):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        previous_payload = {
            "tenant_id": payload_value.get("tenant_id"),
            "approvals": payload_value.get("approvals"),
        }
        from idis.runtime.governance_envelopes import canonicalize_json

        actual_hash = hashlib.sha256(canonicalize_json(previous_payload)).hexdigest()
        if actual_hash != expected_payload_hash or not self._payload_matches_rows(
            previous_payload, current_rows
        ):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        return previous_payload

    @staticmethod
    def _require_monotonic_reapproval(
        *,
        current_rows: list[dict[str, object]],
        desired_rows: list[dict[str, object]],
    ) -> None:
        """Require a new effective approval after any durable revocation."""
        if len(current_rows) != len(desired_rows):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        current_by_key = {
            (
                current.get("provider_id"),
                current.get("environment"),
                current.get("terms_version_hash"),
            ): current
            for current in current_rows
        }
        if len(current_by_key) != len(current_rows):
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        for desired in desired_rows:
            key = (
                desired.get("provider_id"),
                desired.get("environment"),
                desired.get("terms_version_hash"),
            )
            current = current_by_key.get(key)
            if current is None:
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            if current.get("status") != "REVOKED" or desired.get("status") != "APPROVED":
                continue
            revoked_at = current.get("revoked_at")
            approved_at = desired.get("approved_at")
            if (
                not isinstance(revoked_at, datetime)
                or revoked_at.tzinfo is None
                or not isinstance(approved_at, datetime)
                or approved_at.tzinfo is None
                or approved_at.astimezone(UTC) <= revoked_at.astimezone(UTC)
            ):
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")

    def _historical_payload_seen(self, payload: Mapping[str, object]) -> bool:
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID") from exc
        seen = self._connection.execute(
            _HISTORICAL_RIGHTS_PAYLOAD_SQL,
            {
                "tenant_id": self._tenant_id,
                "rights_payload_json": encoded,
            },
        ).scalar_one()
        if seen is True:
            return True
        if seen is False:
            return False
        raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")

    def _payload_matches_rows(
        self,
        payload: Mapping[str, object],
        current_rows: list[dict[str, object]],
    ) -> bool:
        if payload.get("tenant_id") != self._tenant_id:
            return False
        approvals = payload.get("approvals")
        if not isinstance(approvals, list) or len(approvals) != len(current_rows):
            return False
        current_by_key = {
            (
                current.get("provider_id"),
                current.get("environment"),
                current.get("terms_version_hash"),
            ): current
            for current in current_rows
        }
        if len(current_by_key) != len(current_rows):
            return False
        seen_keys: set[tuple[object, object, object]] = set()
        for approval in approvals:
            if not isinstance(approval, Mapping):
                return False
            key = (
                approval.get("provider_id"),
                approval.get("environment"),
                approval.get("terms_version_hash"),
            )
            if key in seen_keys:
                return False
            seen_keys.add(key)
            current = current_by_key.get(key)
            if current is None:
                return False
            for field in _APPROVAL_PAYLOAD_FIELDS:
                expected = approval.get(field)
                if field in {"approved_at", "expires_at", "revoked_at"}:
                    expected = _timestamp(expected)
                if _comparison_value(expected) != _comparison_value(current.get(field)):
                    return False
        return True

    def _verify_rows(self, rows: list[dict[str, object]]) -> set[str]:
        import_ids: set[str] = set()
        for row in rows:
            existing = self._connection.execute(_VERIFY_ROW_SQL, row).fetchone()
            if existing is None:
                raise RightsApprovalPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            mapping = getattr(existing, "_mapping", None)
            value = mapping["import_audit_event_id"] if mapping is not None else existing[0]
            import_ids.add(str(value))
        return import_ids

    def _row(
        self,
        *,
        approval: Mapping[str, Any],
        verified_envelope: VerifiedGovernanceEnvelope,
        imported_by_actor_id: str,
        import_audit_event_id: str,
        imported_at: datetime,
    ) -> dict[str, object]:
        provider_id = str(approval["provider_id"])
        terms_version_hash = str(approval["terms_version_hash"])
        stable_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"idis:rights:{self._tenant_id}:{provider_id}:PROD:{terms_version_hash}",
        )
        return {
            "rights_approval_id": str(stable_id),
            "tenant_id": self._tenant_id,
            "provider_id": provider_id,
            "environment": str(approval["environment"]),
            "terms_version_hash": terms_version_hash,
            "status": str(approval["status"]),
            "rights_class": str(approval["rights_class"]),
            "license_scope": str(approval["license_scope"]),
            "attribution_policy_version": approval.get("attribution_policy_version"),
            "no_store_policy_version": approval.get("no_store_policy_version"),
            "evidence_hash": str(approval["evidence_hash"]),
            "signer_id": str(verified_envelope.signer_id),
            "approver_actor_id": imported_by_actor_id,
            "approved_at": _timestamp(approval.get("approved_at")),
            "expires_at": _timestamp(approval.get("expires_at")),
            "revoked_at": _timestamp(approval.get("revoked_at")),
            "credential_version_id": approval.get("credential_version_id"),
            "envelope_hash": str(verified_envelope.envelope_sha256),
            "payload_hash": str(verified_envelope.payload_sha256),
            "public_key_hash": str(verified_envelope.public_key_sha256),
            "import_audit_event_id": import_audit_event_id,
            "imported_at": imported_at,
        }


__all__ = [
    "PostgresEnrichmentRightsApprovalsRepository",
    "RightsApprovalPersistenceError",
]
