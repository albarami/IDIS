"""Durable tenant-scoped imports for signed provider budget policies."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text

from idis.persistence import db

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from idis.runtime.governance_envelopes import VerifiedGovernanceEnvelope


@dataclass(frozen=True, slots=True)
class VerifiedActiveProviderBudgetPolicy:
    """Read-only, trust-root-bound view of the complete active policy."""

    tenant_id: str
    policy_version: str
    policy_hash: str
    signer_id: str
    signature_verified: bool
    currency: str
    total_max_usd: Decimal
    components: Mapping[str, Mapping[str, object]]
    imported_by_actor_id: str
    envelope_hash: str
    payload_hash: str
    public_key_hash: str
    import_audit_event_id: str
    imported_at: datetime


class ProviderBudgetPolicyPersistenceError(RuntimeError):
    """Safe fail-closed error for a policy persistence mismatch."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_SERIALIZE_POLICY_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))")

_NEXT_IMPORT_TIME_SQL = text(
    """
    SELECT CASE
        WHEN MAX(imported_at) IS NULL THEN clock_timestamp()
        ELSE GREATEST(
            clock_timestamp(),
            MAX(imported_at) + INTERVAL '1 microsecond'
        )
    END AS next_imported_at
    FROM provider_budget_policies
    WHERE tenant_id = CAST(:tenant_id AS UUID)
    """
)

_BLOCKING_USAGE_SQL = text(
    """
    SELECT COUNT(*)
    FROM provider_budget_usage
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND (
          policy_hash IS NULL
          OR (
              policy_hash <> :policy_hash
              AND (
                  reserved_calls <> 0 OR reserved_usd <> 0
                  OR reserved_input_tokens <> 0 OR reserved_output_tokens <> 0
                  OR reserved_embedding_tokens <> 0
              )
          )
      )
    """
)

_IMPORT_SQL = text(
    """
    INSERT INTO provider_budget_policies (
        tenant_id, policy_hash, component_id, policy_version, currency,
        total_max_usd, max_calls, max_usd, max_input_tokens,
        max_output_tokens, max_embedding_tokens, signer_id,
        imported_by_actor_id, envelope_hash, payload_hash, public_key_hash,
        import_audit_event_id, imported_at
    ) VALUES (
        CAST(:tenant_id AS UUID), :policy_hash, :component_id, :policy_version,
        :currency, :total_max_usd, :max_calls, :max_usd, :max_input_tokens,
        :max_output_tokens, :max_embedding_tokens, :signer_id,
        :imported_by_actor_id, :envelope_hash, :payload_hash, :public_key_hash,
        CAST(:import_audit_event_id AS UUID), :imported_at
    )
    ON CONFLICT (tenant_id, policy_hash, component_id)
    DO NOTHING
    """
)

_VERIFY_ROW_SQL = text(
    """
    SELECT import_audit_event_id
    FROM provider_budget_policies
    WHERE tenant_id = CAST(:tenant_id AS UUID)
      AND policy_hash = :policy_hash
      AND component_id = :component_id
      AND policy_version = :policy_version
      AND currency = :currency
      AND total_max_usd = :total_max_usd
      AND max_calls = :max_calls
      AND max_usd = :max_usd
      AND max_input_tokens IS NOT DISTINCT FROM :max_input_tokens
      AND max_output_tokens IS NOT DISTINCT FROM :max_output_tokens
      AND max_embedding_tokens IS NOT DISTINCT FROM :max_embedding_tokens
      AND signer_id = :signer_id
      AND imported_by_actor_id = :imported_by_actor_id
      AND envelope_hash = :envelope_hash
      AND payload_hash = :payload_hash
      AND public_key_hash = :public_key_hash
    """
)

_REQUIRE_ACTIVE_POLICY_SQL = text(
    """
    WITH active_policy AS (
        SELECT policy_hash
        FROM provider_budget_policies
        WHERE tenant_id = CAST(:tenant_id AS UUID)
        GROUP BY policy_hash
        HAVING COUNT(*) = :component_count
           AND COUNT(DISTINCT policy_version) = 1
           AND COUNT(DISTINCT currency) = 1
           AND COUNT(DISTINCT total_max_usd) = 1
           AND COUNT(DISTINCT signer_id) = 1
           AND COUNT(DISTINCT envelope_hash) = 1
           AND COUNT(DISTINCT payload_hash) = 1
           AND COUNT(DISTINCT public_key_hash) = 1
           AND COUNT(DISTINCT import_audit_event_id) = 1
           AND MIN(imported_at) = MAX(imported_at)
        ORDER BY MAX(imported_at) DESC, policy_hash DESC
        LIMIT 1
    )
    SELECT EXISTS (
        SELECT 1 FROM active_policy WHERE policy_hash = :policy_hash
    ) AS is_active
    """
)

_LOAD_ACTIVE_POLICY_SQL = text(
    """
    WITH latest_complete AS (
        SELECT policy_hash, import_audit_event_id, MAX(imported_at) AS imported_at
        FROM provider_budget_policies
        WHERE tenant_id = CAST(:tenant_id AS UUID)
        GROUP BY policy_hash, import_audit_event_id
        HAVING COUNT(*) = :component_count
           AND COUNT(DISTINCT component_id) = :component_count
        ORDER BY MAX(imported_at) DESC, import_audit_event_id DESC
        LIMIT 1
    )
    SELECT p.policy_hash, p.component_id, p.policy_version, p.currency,
           p.total_max_usd, p.max_calls, p.max_usd,
           p.max_input_tokens, p.max_output_tokens, p.max_embedding_tokens,
           p.signer_id, p.imported_by_actor_id, p.envelope_hash,
           p.payload_hash, p.public_key_hash, p.import_audit_event_id,
           p.imported_at
    FROM provider_budget_policies AS p
    JOIN latest_complete AS active
      ON active.policy_hash = p.policy_hash
     AND active.import_audit_event_id = p.import_audit_event_id
    WHERE p.tenant_id = CAST(:tenant_id AS UUID)
    ORDER BY p.component_id
    """
)

_REQUIRE_IMPORT_AUDIT_SQL = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM audit_events
        WHERE tenant_id = CAST(:tenant_id AS UUID)
          AND event_id = CAST(:event_id AS UUID)
          AND event_type = 'integration.governance.imported'
    )
    """
)

_REQUIRE_USAGE_BINDING_SQL = text(
    """
    SELECT NOT EXISTS (
        SELECT 1 FROM provider_budget_usage
        WHERE tenant_id = CAST(:tenant_id AS UUID)
          AND (
              policy_hash IS NULL
              OR (
                  (reserved_calls <> 0 OR reserved_usd <> 0
                   OR reserved_input_tokens <> 0 OR reserved_output_tokens <> 0
                   OR reserved_embedding_tokens <> 0)
                  AND policy_hash <> :active_policy_hash
              )
          )
    )
    """
)


def _mapping(row: object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    value = getattr(row, "_mapping", None)
    if isinstance(value, Mapping):
        return value
    raise ProviderBudgetPolicyPersistenceError("PROVIDER_BUDGET_CONFIG_BLOCKED")


class PostgresProviderBudgetPoliciesRepository:
    """Import a complete 17-component policy in the caller's transaction."""

    is_durable = True

    def __init__(
        self,
        connection: Connection,
        tenant_id: str,
        *,
        tenant_context_is_set: bool = False,
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self.last_import_audit_event_id: str | None = None
        self.last_import_was_new = False
        self.last_import_disposition = "uninitialized"
        if not tenant_context_is_set:
            db.set_tenant_local(connection, tenant_id)

    def load_active_verified_policy(
        self,
        *,
        verified_envelope: VerifiedGovernanceEnvelope,
        trusted_signer_id: str,
        trusted_public_key_sha256: str,
        expected_imported_by_actor_id: str,
    ) -> VerifiedActiveProviderBudgetPolicy:
        """Load the complete active policy and re-prove every durable binding."""
        from idis.providers.budget_policy import (
            EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS,
            validate_provider_budget_policy_payload,
        )
        from idis.runtime.governance_envelopes import BUDGET_SCHEMA_VERSION

        blocked = ProviderBudgetPolicyPersistenceError("PROVIDER_BUDGET_CONFIG_BLOCKED")
        if (
            verified_envelope.schema_version != BUDGET_SCHEMA_VERSION
            or verified_envelope.payload.get("tenant_id") != self._tenant_id
            or not trusted_signer_id
            or len(trusted_public_key_sha256) != 64
            or not secrets.compare_digest(verified_envelope.signer_id, trusted_signer_id)
            or not secrets.compare_digest(
                verified_envelope.public_key_sha256, trusted_public_key_sha256
            )
        ):
            raise blocked
        try:
            validated = validate_provider_budget_policy_payload(verified_envelope.payload)
        except Exception as exc:
            raise blocked from exc
        expected_components = EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
        rows = self._connection.execute(
            _LOAD_ACTIVE_POLICY_SQL,
            {"tenant_id": self._tenant_id, "component_count": len(expected_components)},
        ).fetchall()
        if len(rows) != len(expected_components):
            raise blocked
        mapped = [_mapping(row) for row in rows]
        headers = {
            (
                str(row["policy_hash"]),
                str(row["policy_version"]),
                str(row["currency"]),
                Decimal(str(row["total_max_usd"])),
                str(row["signer_id"]),
                str(row["imported_by_actor_id"]),
                str(row["envelope_hash"]),
                str(row["payload_hash"]),
                str(row["public_key_hash"]),
                str(row["import_audit_event_id"]),
                row["imported_at"],
            )
            for row in mapped
        }
        if len(headers) != 1:
            raise blocked
        header = next(iter(headers))
        imported_at_raw = header[10]
        if isinstance(imported_at_raw, str):
            try:
                imported_at = datetime.fromisoformat(imported_at_raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise blocked from exc
        elif isinstance(imported_at_raw, datetime):
            imported_at = imported_at_raw
        else:
            raise blocked
        if imported_at.tzinfo is None:
            raise blocked
        expected_header = (
            verified_envelope.payload_sha256,
            validated.policy_version,
            "USD",
            validated.total_max_usd,
            verified_envelope.signer_id,
            expected_imported_by_actor_id,
            verified_envelope.envelope_sha256,
            verified_envelope.payload_sha256,
            verified_envelope.public_key_sha256,
        )
        if header[:9] != expected_header:
            raise blocked
        audit_event_id = str(header[9])
        try:
            UUID(audit_event_id)
        except ValueError as exc:
            raise blocked from exc

        component_ids: set[str] = set()
        for row in mapped:
            component_id = str(row["component_id"])
            component_ids.add(component_id)
            expected = validated.components.get(component_id)
            if expected is None:
                raise blocked
            max_calls = row["max_calls"]
            if type(max_calls) is not int:
                raise blocked
            actual_caps = (
                max_calls,
                Decimal(str(row["max_usd"])),
                row["max_input_tokens"],
                row["max_output_tokens"],
                row["max_embedding_tokens"],
            )
            expected_caps = (
                expected.max_calls,
                expected.max_usd,
                expected.max_input_tokens,
                expected.max_output_tokens,
                expected.max_embedding_tokens,
            )
            if actual_caps != expected_caps:
                raise blocked
        if frozenset(component_ids) != expected_components:
            raise blocked
        audit_exists = self._connection.execute(
            _REQUIRE_IMPORT_AUDIT_SQL,
            {"tenant_id": self._tenant_id, "event_id": audit_event_id},
        ).scalar_one()
        usage_bound = self._connection.execute(
            _REQUIRE_USAGE_BINDING_SQL,
            {
                "tenant_id": self._tenant_id,
                "active_policy_hash": verified_envelope.payload_sha256,
            },
        ).scalar_one()
        if audit_exists is not True or usage_bound is not True:
            raise blocked

        raw_components = verified_envelope.payload.get("components")
        if not isinstance(raw_components, Mapping):
            raise blocked
        immutable_components = MappingProxyType(
            {
                component_id: MappingProxyType(dict(raw_components[component_id]))
                for component_id in sorted(expected_components)
                if isinstance(raw_components[component_id], Mapping)
            }
        )
        if frozenset(immutable_components) != expected_components:
            raise blocked
        return VerifiedActiveProviderBudgetPolicy(
            tenant_id=self._tenant_id,
            policy_version=validated.policy_version,
            policy_hash=verified_envelope.payload_sha256,
            signer_id=verified_envelope.signer_id,
            signature_verified=True,
            currency="USD",
            total_max_usd=validated.total_max_usd,
            components=immutable_components,
            imported_by_actor_id=expected_imported_by_actor_id,
            envelope_hash=verified_envelope.envelope_sha256,
            payload_hash=verified_envelope.payload_sha256,
            public_key_hash=verified_envelope.public_key_sha256,
            import_audit_event_id=audit_event_id,
            imported_at=imported_at,
        )

    def import_verified(
        self,
        *,
        verified_envelope: VerifiedGovernanceEnvelope,
        imported_by_actor_id: str,
        import_audit_event_id: str,
        imported_at: datetime,
    ) -> int:
        schema_version = getattr(verified_envelope, "schema_version", None)
        payload = getattr(verified_envelope, "payload", None)
        if schema_version != "provider-budget-policy-v1" or not isinstance(payload, Mapping):
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        if payload.get("tenant_id") != self._tenant_id:
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")

        from idis.providers.budget_policy import (
            EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS,
            validate_provider_budget_policy_payload,
        )

        validate_provider_budget_policy_payload(payload)
        components = payload.get("components")
        if not isinstance(components, Mapping) or frozenset(components) != frozenset(
            EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
        ):
            raise ProviderBudgetPolicyPersistenceError("PROVIDER_BUDGET_CONFIG_BLOCKED")
        policy_hash = str(verified_envelope.payload_sha256)
        if imported_at.tzinfo is None:
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")

        self.last_import_audit_event_id = None
        self.last_import_was_new = False
        self.last_import_disposition = "uninitialized"

        self._connection.execute(
            _SERIALIZE_POLICY_SQL,
            {"lock_key": f"idis:provider-budget:{self._tenant_id}"},
        )
        blocking_usage = self._connection.execute(
            _BLOCKING_USAGE_SQL,
            {"tenant_id": self._tenant_id, "policy_hash": policy_hash},
        )
        scalar_one = getattr(blocking_usage, "scalar_one", None)
        if callable(scalar_one) and int(scalar_one()) != 0:
            raise ProviderBudgetPolicyPersistenceError("PROVIDER_BUDGET_CONFIG_BLOCKED")

        durable_imported_at = self._connection.execute(
            _NEXT_IMPORT_TIME_SQL,
            {"tenant_id": self._tenant_id},
        ).scalar_one()
        if not isinstance(durable_imported_at, datetime) or durable_imported_at.tzinfo is None:
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")

        rows = [
            self._row(
                component_id=component_id,
                component=components[component_id],
                payload=payload,
                verified_envelope=verified_envelope,
                imported_by_actor_id=imported_by_actor_id,
                import_audit_event_id=import_audit_event_id,
                imported_at=durable_imported_at,
            )
            for component_id in sorted(components)
        ]
        result = self._connection.execute(_IMPORT_SQL, rows)
        rowcount = getattr(result, "rowcount", len(rows))
        if rowcount == len(rows):
            lineage_id = import_audit_event_id
            was_new = True
            disposition = "created"
        elif rowcount in (-1, 0):
            existing_ids = self._verify_rows(rows)
            if len(existing_ids) != 1:
                raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            lineage_id = next(iter(existing_ids))
            was_new = lineage_id == import_audit_event_id
            disposition = "created" if was_new else "replayed"
        else:
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        self._require_active_policy(
            policy_hash,
            component_count=len(EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS),
        )
        self.last_import_audit_event_id = lineage_id
        self.last_import_was_new = was_new
        self.last_import_disposition = disposition
        return len(rows)

    def _require_active_policy(self, policy_hash: str, *, component_count: int) -> None:
        result = self._connection.execute(
            _REQUIRE_ACTIVE_POLICY_SQL,
            {
                "tenant_id": self._tenant_id,
                "policy_hash": policy_hash,
                "component_count": component_count,
            },
        )
        if result.scalar_one() is not True:
            raise ProviderBudgetPolicyPersistenceError("PROVIDER_BUDGET_CONFIG_BLOCKED")

    def _verify_rows(self, rows: list[dict[str, object]]) -> set[str]:
        import_ids: set[str] = set()
        for row in rows:
            existing = self._connection.execute(_VERIFY_ROW_SQL, row).fetchone()
            if existing is None:
                raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")
            mapping = getattr(existing, "_mapping", None)
            value = mapping["import_audit_event_id"] if mapping is not None else existing[0]
            import_ids.add(str(value))
        return import_ids

    def _row(
        self,
        *,
        component_id: str,
        component: object,
        payload: Mapping[str, Any],
        verified_envelope: VerifiedGovernanceEnvelope,
        imported_by_actor_id: str,
        import_audit_event_id: str,
        imported_at: datetime,
    ) -> dict[str, object]:
        if not isinstance(component, Mapping):
            raise ProviderBudgetPolicyPersistenceError("GOVERNANCE_SCHEMA_INVALID")
        return {
            "tenant_id": self._tenant_id,
            "policy_hash": str(verified_envelope.payload_sha256),
            "component_id": component_id,
            "policy_version": str(payload["policy_version"]),
            "currency": str(payload["currency"]),
            "total_max_usd": Decimal(str(payload["total_max_usd"])),
            "max_calls": int(component["max_calls"]),
            "max_usd": Decimal(str(component["max_usd"])),
            "max_input_tokens": component.get("max_input_tokens"),
            "max_output_tokens": component.get("max_output_tokens"),
            "max_embedding_tokens": component.get("max_embedding_tokens"),
            "signer_id": str(verified_envelope.signer_id),
            "imported_by_actor_id": imported_by_actor_id,
            "envelope_hash": str(verified_envelope.envelope_sha256),
            "payload_hash": str(verified_envelope.payload_sha256),
            "public_key_hash": str(verified_envelope.public_key_sha256),
            "import_audit_event_id": import_audit_event_id,
            "imported_at": imported_at,
        }


__all__ = [
    "PostgresProviderBudgetPoliciesRepository",
    "ProviderBudgetPolicyPersistenceError",
    "VerifiedActiveProviderBudgetPolicy",
]
