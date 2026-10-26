"""Provider-spend budget: a minimal per-tenant/provider hard cap (Slice96 DEC-C).

An injectable ``ProviderBudgetStore`` accounting seam tracks the number of live provider calls per
``{tenant}:{provider}``. The production default is the durable, cross-replica Postgres-backed store
when a database is configured; the in-memory store is only a hermetic dev/test fallback (single
process). ``ProviderBudget.charge`` records one unit of spend and raises a safe, fail-closed
``ProviderBudgetExceededError`` (stable code ``PROVIDER_BUDGET_EXCEEDED``) BEFORE the caller
performs the live call when the configured hard cap would be exceeded. ``BudgetedLLMClient`` wraps
any ``LLMClient`` so the gate runs before the wrapped client is invoked. Disabled unless
``IDIS_PROVIDER_BUDGET_MAX_CALLS`` is set to a positive integer -> passthrough, no behavior change.

The Slice96 non-strict compatibility path remains a live-call count. Slice100's strict path binds
calls, USD, and token dimensions to a signed policy and an exact reservation identity. Aggregate
balances remain in ``provider_budget_usage`` while canonical append-only audit events provide the
per-reservation state machine, so retries cannot consume or refund another in-flight reservation.
Neither path is a billing system. Denials and ledger events remain safe-shape: identifiers, hashes,
and numeric balances only, never prompts, provider payloads, credentials, or raw secrets.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

from idis.services.extraction.extractors.llm_client import LLMClient

PROVIDER_BUDGET_EXCEEDED: Final = "PROVIDER_BUDGET_EXCEEDED"
ENV_MAX_CALLS: Final = "IDIS_PROVIDER_BUDGET_MAX_CALLS"
DEFAULT_PROVIDER: Final = "anthropic"


def provider_budget_exceeded_error_code() -> str:
    """Return the centrally defined, secret-safe budget denial code."""

    return PROVIDER_BUDGET_EXCEEDED


class ProviderBudgetExceededError(Exception):
    """Safe, fail-closed provider-budget denial.

    Carries only non-sensitive fields: the stable ``code``, the ``provider``, the ``tenant_id``,
    and the ``limit``/``used`` counts. The string form is a fixed message -- never a prompt,
    provider payload, secret, or raw cost detail. ``code`` is surfaced as the run step's
    ``error_code`` via the orchestrator's ``getattr(exc, "code", ...)`` failure path.
    """

    def __init__(self, *, provider: str, tenant_id: str, limit: int, used: int) -> None:
        self.code: str = PROVIDER_BUDGET_EXCEEDED
        self.provider = provider
        self.tenant_id = tenant_id
        self.limit = limit
        self.used = used
        super().__init__(f"{PROVIDER_BUDGET_EXCEEDED}: provider budget limit reached")


@runtime_checkable
class ProviderBudgetStore(Protocol):
    """Atomic per-key spend accounting behind an injectable seam.

    The production default is the durable, cross-replica Postgres-backed store when a database is
    configured; the in-memory store is only a hermetic dev/test fallback.
    """

    def consume(self, *, key: str, amount: int, cap: int) -> tuple[bool, int]:
        """Atomically add ``amount`` to ``key`` iff it stays within ``cap``.

        Returns ``(allowed, used_after)``. When denied (would exceed ``cap``) the stored total is
        left unchanged and ``used_after`` is the current usage.
        """
        ...


class InMemoryProviderBudgetStore:
    """Per-process ``ProviderBudgetStore``: a ``{key: used}`` dict guarded by a lock."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}
        self._lock = threading.Lock()

    def consume(self, *, key: str, amount: int, cap: int) -> tuple[bool, int]:
        with self._lock:
            used = self._used.get(key, 0)
            if used + amount > cap:
                return (False, used)
            used += amount
            self._used[key] = used
            return (True, used)

    def reset(self) -> None:
        """Clear all accounting (test/maintenance hook)."""
        with self._lock:
            self._used.clear()


def _parse_budget_key(key: str) -> tuple[str, str]:
    """Split a ``{tenant_id}:{provider}`` budget key (provider may itself contain ':')."""
    tenant_id, sep, provider = key.partition(":")
    if not sep or not tenant_id or not provider:
        raise ValueError("provider budget key must be '<tenant_id>:<provider>'")
    return tenant_id, provider


# Race-safe atomic consume-under-cap: one statement that inserts (fresh key) or increments
# (existing key) only while the total stays within the cap, returning the new usage iff allowed.
# On conflict the row is locked, so concurrent consumers serialize and re-check against the current
# value -- a second consumer past the cap gets no RETURNING row (denied), never exceeding it.
_CONSUME_SQL: Final = """
INSERT INTO provider_budget_usage (tenant_id, provider, used, updated_at)
SELECT CAST(:tenant_id AS uuid), :provider, :amount, now()
WHERE :amount <= :cap
ON CONFLICT (tenant_id, provider) DO UPDATE
    SET used = provider_budget_usage.used + :amount, updated_at = now()
    WHERE provider_budget_usage.used + :amount <= :cap
RETURNING used
"""

_CURRENT_USED_SQL: Final = (
    "SELECT used FROM provider_budget_usage "
    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND provider = :provider"
)

# Slice100 strict admission needs an explicit durable reservation lifecycle. Both mutations are
# single-statement, row-locking operations: reserve cannot cross the combined consumed/reserved
# call cap, while refund cannot drive the reservation balance below zero.
_RESERVE_SQL: Final = """
INSERT INTO provider_budget_usage (
    tenant_id, provider, used, reserved_calls, updated_at
)
SELECT CAST(:tenant_id AS uuid), :provider, 0, :amount, now()
WHERE :amount <= :cap
ON CONFLICT (tenant_id, provider) DO UPDATE
    SET reserved_calls = provider_budget_usage.reserved_calls + :amount,
        updated_at = now()
    WHERE provider_budget_usage.used + provider_budget_usage.reserved_calls + :amount <= :cap
RETURNING reserved_calls
"""

_REFUND_SQL: Final = """
UPDATE provider_budget_usage
SET reserved_calls = reserved_calls - :amount, updated_at = now()
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND provider = :provider
  AND reserved_calls >= :amount
RETURNING reserved_calls
"""

_CURRENT_RESERVED_SQL: Final = (
    "SELECT reserved_calls FROM provider_budget_usage "
    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND provider = :provider"
)

_GOVERNED_POLICY_LOCK_SQL: Final = "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"
_BUDGET_RESERVATION_EVENT_TYPES: Final = (
    "integration.provider_budget.reserved",
    "integration.provider_budget.consumed",
    "integration.provider_budget.refunded",
)
_BUDGET_RESERVATION_EVENTS_SQL: Final = """
SELECT event_type, event
FROM audit_events
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND event_type IN (
      'integration.provider_budget.reserved',
      'integration.provider_budget.consumed',
      'integration.provider_budget.refunded'
  )
  AND event -> 'payload' -> 'safe' ->> 'reservation_id' = :reservation_id
"""
_GOVERNED_ACTIVE_COMPONENT_SQL: Final = """
WITH active_policy AS (
    SELECT policy_hash, MAX(imported_at) AS imported_at
    FROM provider_budget_policies
    WHERE tenant_id = CAST(:tenant_id AS uuid)
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
SELECT p.policy_hash, p.currency, p.total_max_usd, p.max_calls, p.max_usd,
       p.max_input_tokens, p.max_output_tokens, p.max_embedding_tokens
FROM provider_budget_policies AS p
JOIN active_policy AS active ON active.policy_hash = p.policy_hash
WHERE p.tenant_id = CAST(:tenant_id AS uuid)
  AND p.component_id = :component_id
  AND p.policy_hash = :policy_hash
"""
_GOVERNED_USAGE_ROWS_SQL: Final = """
SELECT provider, used, policy_hash, reserved_calls, reserved_usd, consumed_usd,
       reserved_input_tokens, consumed_input_tokens, reserved_output_tokens,
       consumed_output_tokens, reserved_embedding_tokens, consumed_embedding_tokens
FROM provider_budget_usage
WHERE tenant_id = CAST(:tenant_id AS uuid)
FOR UPDATE
"""
_GOVERNED_RESERVE_SQL: Final = """
INSERT INTO provider_budget_usage (
    tenant_id, provider, used, updated_at, policy_hash, reserved_calls,
    reserved_usd, consumed_usd, reserved_input_tokens, consumed_input_tokens,
    reserved_output_tokens, consumed_output_tokens, reserved_embedding_tokens,
    consumed_embedding_tokens
) VALUES (
    CAST(:tenant_id AS uuid), :component_id, 0, now(), :policy_hash, :calls,
    :usd, 0, :input_tokens, 0, :output_tokens, 0, :embedding_tokens, 0
)
ON CONFLICT (tenant_id, provider) DO UPDATE SET
    policy_hash = EXCLUDED.policy_hash,
    reserved_calls = provider_budget_usage.reserved_calls + EXCLUDED.reserved_calls,
    reserved_usd = provider_budget_usage.reserved_usd + EXCLUDED.reserved_usd,
    reserved_input_tokens = provider_budget_usage.reserved_input_tokens
        + EXCLUDED.reserved_input_tokens,
    reserved_output_tokens = provider_budget_usage.reserved_output_tokens
        + EXCLUDED.reserved_output_tokens,
    reserved_embedding_tokens = provider_budget_usage.reserved_embedding_tokens
        + EXCLUDED.reserved_embedding_tokens,
    updated_at = now()
RETURNING reserved_calls
"""
_GOVERNED_CONSUME_SQL: Final = """
UPDATE provider_budget_usage SET
    used = used + :calls,
    reserved_calls = reserved_calls - :calls,
    reserved_usd = reserved_usd - :usd,
    consumed_usd = consumed_usd + :usd,
    reserved_input_tokens = reserved_input_tokens - :input_tokens,
    consumed_input_tokens = consumed_input_tokens + :input_tokens,
    reserved_output_tokens = reserved_output_tokens - :output_tokens,
    consumed_output_tokens = consumed_output_tokens + :output_tokens,
    reserved_embedding_tokens = reserved_embedding_tokens - :embedding_tokens,
    consumed_embedding_tokens = consumed_embedding_tokens + :embedding_tokens,
    updated_at = now()
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND provider = :component_id
  AND policy_hash = :policy_hash
  AND reserved_calls >= :calls
  AND reserved_usd >= :usd
  AND reserved_input_tokens >= :input_tokens
  AND reserved_output_tokens >= :output_tokens
  AND reserved_embedding_tokens >= :embedding_tokens
RETURNING used
"""
_GOVERNED_REFUND_SQL: Final = """
UPDATE provider_budget_usage SET
    reserved_calls = reserved_calls - :calls,
    reserved_usd = reserved_usd - :usd,
    reserved_input_tokens = reserved_input_tokens - :input_tokens,
    reserved_output_tokens = reserved_output_tokens - :output_tokens,
    reserved_embedding_tokens = reserved_embedding_tokens - :embedding_tokens,
    updated_at = now()
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND provider = :component_id
  AND policy_hash = :policy_hash
  AND reserved_calls >= :calls
  AND reserved_usd >= :usd
  AND reserved_input_tokens >= :input_tokens
  AND reserved_output_tokens >= :output_tokens
  AND reserved_embedding_tokens >= :embedding_tokens
RETURNING reserved_calls
"""
_GOVERNED_SNAPSHOT_SQL: Final = """
SELECT used, policy_hash, reserved_calls, reserved_usd, consumed_usd,
       reserved_input_tokens, consumed_input_tokens, reserved_output_tokens,
       consumed_output_tokens, reserved_embedding_tokens, consumed_embedding_tokens
FROM provider_budget_usage
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND provider = :component_id
  AND policy_hash = :policy_hash
"""

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _budget_config_error() -> Exception:
    from idis.providers.budget_policy import (
        PROVIDER_BUDGET_CONFIG_BLOCKED,
        ProviderBudgetPolicyError,
    )

    return ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)


def _row_value(row: object, name: str, index: int) -> object:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and name in mapping:
        return mapping[name]
    if isinstance(row, Mapping) and name in row:
        return row[name]
    return row[index]  # type: ignore[index]


def _validate_governed_values(
    values: Mapping[str, object],
    *,
    require_reservation_id: bool = True,
) -> dict[str, object]:
    expected = {
        "tenant_id",
        "policy_hash",
        "component_id",
        "calls",
        "usd",
        "input_tokens",
        "output_tokens",
        "embedding_tokens",
        "global_max_calls",
    }
    if require_reservation_id:
        expected.add("reservation_id")
    if set(values) != expected:
        raise _budget_config_error()
    tenant_id = values["tenant_id"]
    try:
        if not isinstance(tenant_id, str) or str(UUID(tenant_id)) != tenant_id:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise _budget_config_error() from exc
    policy_hash = values["policy_hash"]
    component_id = values["component_id"]
    if (
        not isinstance(policy_hash, str)
        or _SHA256_RE.fullmatch(policy_hash) is None
        or not isinstance(component_id, str)
        or not component_id.strip()
    ):
        raise _budget_config_error()
    if require_reservation_id:
        reservation_id = values["reservation_id"]
        try:
            if not isinstance(reservation_id, str) or str(UUID(reservation_id)) != reservation_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise _budget_config_error() from exc
    for name in ("calls", "global_max_calls"):
        raw_integer = values[name]
        if not isinstance(raw_integer, int) or isinstance(raw_integer, bool) or raw_integer <= 0:
            raise _budget_config_error()
    for name in ("input_tokens", "output_tokens", "embedding_tokens"):
        raw_integer = values[name]
        if not isinstance(raw_integer, int) or isinstance(raw_integer, bool) or raw_integer < 0:
            raise _budget_config_error()
    usd = values["usd"]
    if not isinstance(usd, Decimal) or not usd.is_finite() or usd < 0:
        raise _budget_config_error()
    exponent = usd.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -8:
        raise _budget_config_error()
    return dict(values)


class PostgresProviderBudgetStore:
    """Production call/USD/token accounting bound to the active signed policy.

    Construction with ``global_max_calls`` creates a governed-only store: its public methods
    accept only the rich signed-policy contract, so caller-supplied legacy caps cannot bypass
    policy reservations or audit.  Construction without that bound is the explicitly non-strict
    Slice96 compatibility mode and accepts only the legacy call-count contract.  The modes are
    mutually exclusive.
    """

    is_durable: Final = True
    signed_policy_accounting_version: Final = "provider-budget-policy-v1"

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] | None = None,
        tenant_setter: Callable[[Any, str], None] | None = None,
        global_max_calls: int | None = None,
    ) -> None:
        if global_max_calls is not None and (
            not isinstance(global_max_calls, int)
            or isinstance(global_max_calls, bool)
            or global_max_calls <= 0
        ):
            raise _budget_config_error()
        self._connection_factory = connection_factory
        self._tenant_setter = tenant_setter
        self._trusted_global_max_calls = global_max_calls

    @property
    def trusted_global_max_calls(self) -> int | None:
        """Return the construction-bound hard cap used by governed operations."""
        return self._trusted_global_max_calls

    @property
    def governed_only(self) -> bool:
        """Prove this instance cannot dispatch to caller-capped legacy accounting."""
        return self._trusted_global_max_calls is not None

    def _trusted_governed_values(
        self,
        values: Mapping[str, object],
        *,
        require_reservation_id: bool = True,
        require_current_cap: bool = True,
    ) -> dict[str, object]:
        governed = _validate_governed_values(
            values,
            require_reservation_id=require_reservation_id,
        )
        trusted_cap = self._trusted_global_max_calls
        if trusted_cap is None or (
            require_current_cap and governed["global_max_calls"] != trusted_cap
        ):
            raise _budget_config_error()
        if require_current_cap:
            # New-spend SQL and audit receive the construction-bound value, never the
            # operation's untrusted copy, even after the exact-match check above.
            governed["global_max_calls"] = trusted_cap
        return governed

    def _connection(self) -> Any:
        if self._connection_factory is not None:
            return self._connection_factory()
        from idis.persistence.db import begin_app_conn

        return begin_app_conn()

    def _set_tenant(self, connection: Any, tenant_id: str) -> None:
        if self._tenant_setter is not None:
            self._tenant_setter(connection, tenant_id)
            return
        from idis.persistence.db import set_tenant_local

        set_tenant_local(connection, tenant_id)

    def reserve(
        self,
        *,
        key: str | None = None,
        amount: int | None = None,
        cap: int | None = None,
        **values: object,
    ) -> tuple[bool, int] | bool:
        """Reserve legacy calls or a complete signed-policy accounting vector."""
        if key is not None or amount is not None or cap is not None:
            if self.governed_only:
                raise _budget_config_error()
            if values or key is None or amount is None or cap is None:
                raise ValueError("provider budget reservation arguments are incomplete")
            return self._reserve_legacy(key=key, amount=amount, cap=cap)
        governed = self._trusted_governed_values(values, require_current_cap=False)
        with self._connection() as connection:
            return self.reserve_in_tx(connection, **governed)

    def consume(
        self,
        *,
        key: str | None = None,
        amount: int | None = None,
        cap: int | None = None,
        **values: object,
    ) -> tuple[bool, int] | bool:
        """Consume a reservation; strict mode never uses the legacy call-only branch."""
        if key is not None or amount is not None or cap is not None:
            if self.governed_only:
                raise _budget_config_error()
            if values or key is None or amount is None or cap is None:
                raise ValueError("provider budget consume arguments are incomplete")
            return self._consume_legacy(key=key, amount=amount, cap=cap)
        governed = self._trusted_governed_values(values, require_current_cap=False)
        with self._connection() as connection:
            return self.consume_in_tx(connection, **governed)

    def refund(
        self,
        *,
        key: str | None = None,
        amount: int | None = None,
        **values: object,
    ) -> tuple[bool, int] | bool:
        """Release legacy or signed-policy reservation balances."""
        if key is not None or amount is not None:
            if self.governed_only:
                raise _budget_config_error()
            if values or key is None or amount is None:
                raise ValueError("provider budget refund arguments are incomplete")
            return self._refund_legacy(key=key, amount=amount)
        governed = self._trusted_governed_values(values, require_current_cap=False)
        with self._connection() as connection:
            return self.refund_in_tx(connection, **governed)

    def reserve_in_tx(self, connection: Any, **values: object) -> bool:
        """Reserve a complete vector in the caller-owned transaction."""
        governed = self._trusted_governed_values(values, require_current_cap=False)
        tenant_id = str(governed["tenant_id"])
        self._lock_policy(connection, tenant_id)
        reservation_state = self._reservation_state(connection, governed)
        if reservation_state == "RESERVED":
            return True
        if reservation_state is not None:
            raise _budget_config_error()
        # Only a new reservation needs current-policy admission. Exact retries are authorized
        # by their immutable ledger vector even if governance has since promoted a replacement.
        governed = self._trusted_governed_values(governed)
        caps = self._active_component_caps(connection, governed)
        usage_rows = self._usage_rows(connection, tenant_id)
        if any(_row_value(row, "policy_hash", 2) is None for row in usage_rows):
            raise _budget_config_error()

        component_id = str(governed["component_id"])
        current = next(
            (row for row in usage_rows if str(_row_value(row, "provider", 0)) == component_id),
            None,
        )
        current_values = self._usage_balances(current)
        current_policy_hash = current_values["policy_hash"]
        if current_policy_hash not in (None, governed["policy_hash"]) and any(
            current_values[name] != 0
            for name in (
                "reserved_calls",
                "reserved_usd",
                "reserved_input_tokens",
                "reserved_output_tokens",
                "reserved_embedding_tokens",
            )
        ):
            raise _budget_config_error()

        calls = cast(int, governed["calls"])
        usd = cast(Decimal, governed["usd"])
        input_tokens = cast(int, governed["input_tokens"])
        output_tokens = cast(int, governed["output_tokens"])
        embedding_tokens = cast(int, governed["embedding_tokens"])
        global_calls = sum(
            int(str(_row_value(row, "used", 1) or 0))
            + int(str(_row_value(row, "reserved_calls", 3) or 0))
            for row in usage_rows
        )
        global_usd = sum(
            (
                Decimal(str(_row_value(row, "reserved_usd", 4) or 0))
                + Decimal(str(_row_value(row, "consumed_usd", 5) or 0))
                for row in usage_rows
            ),
            Decimal("0"),
        )
        allowed = (
            global_calls + calls <= cast(int, self._trusted_global_max_calls)
            and global_usd + usd <= Decimal(str(caps["total_max_usd"]))
            and int(str(current_values["used"]))
            + int(str(current_values["reserved_calls"]))
            + calls
            <= int(str(caps["max_calls"]))
            and Decimal(str(current_values["consumed_usd"]))
            + Decimal(str(current_values["reserved_usd"]))
            + usd
            <= Decimal(str(caps["max_usd"]))
            and self._token_within_cap(
                current_values,
                "input",
                input_tokens,
                caps["max_input_tokens"],
            )
            and self._token_within_cap(
                current_values,
                "output",
                output_tokens,
                caps["max_output_tokens"],
            )
            and self._token_within_cap(
                current_values,
                "embedding",
                embedding_tokens,
                caps["max_embedding_tokens"],
            )
        )
        if not allowed:
            return False

        from sqlalchemy import text

        row = connection.execute(text(_GOVERNED_RESERVE_SQL), governed).fetchone()
        if row is None:
            return False
        self._emit_reservation_event(connection, governed, state="RESERVED")
        return True

    def consume_in_tx(self, connection: Any, **values: object) -> bool:
        governed = self._trusted_governed_values(values, require_current_cap=False)
        tenant_id = str(governed["tenant_id"])
        self._lock_policy(connection, tenant_id)
        reservation_state = self._reservation_state(connection, governed)
        if reservation_state == "CONSUMED":
            return True
        if reservation_state != "RESERVED":
            raise _budget_config_error()
        # A previously admitted reservation must remain consumable after policy replacement;
        # the exact ledger vector and aggregate WHERE guards fence the terminal mutation.
        from sqlalchemy import text

        row = connection.execute(text(_GOVERNED_CONSUME_SQL), governed).fetchone()
        if row is None:
            raise _budget_config_error()
        self._emit_reservation_event(connection, governed, state="CONSUMED")
        return True

    def refund_in_tx(self, connection: Any, **values: object) -> bool:
        governed = self._trusted_governed_values(values, require_current_cap=False)
        tenant_id = str(governed["tenant_id"])
        self._lock_policy(connection, tenant_id)
        reservation_state = self._reservation_state(connection, governed)
        if reservation_state == "REFUNDED":
            return True
        if reservation_state != "RESERVED":
            raise _budget_config_error()
        # Refund follows the same immutable admission authority as consume, preventing an
        # old-policy reservation from becoming permanently stranded.
        from sqlalchemy import text

        row = connection.execute(text(_GOVERNED_REFUND_SQL), governed).fetchone()
        if row is None:
            raise _budget_config_error()
        self._emit_reservation_event(connection, governed, state="REFUNDED")
        return True

    @staticmethod
    def _reservation_vector(governed: Mapping[str, object]) -> dict[str, object]:
        return {
            "reservation_id": str(governed["reservation_id"]),
            "policy_hash": str(governed["policy_hash"]),
            "component_id": str(governed["component_id"]),
            "calls": int(str(governed["calls"])),
            "usd": format(Decimal(str(governed["usd"])), "f"),
            "input_tokens": int(str(governed["input_tokens"])),
            "output_tokens": int(str(governed["output_tokens"])),
            "embedding_tokens": int(str(governed["embedding_tokens"])),
            "global_max_calls": int(str(governed["global_max_calls"])),
        }

    def _reservation_state(
        self,
        connection: Any,
        governed: Mapping[str, object],
    ) -> str | None:
        from sqlalchemy import text

        from idis.validators.audit_event_validator import validate_audit_event

        vector = self._reservation_vector(governed)
        rows = connection.execute(
            text(_BUDGET_RESERVATION_EVENTS_SQL),
            {
                "tenant_id": str(governed["tenant_id"]),
                "reservation_id": vector["reservation_id"],
            },
        ).fetchall()
        if not rows:
            return None

        counts = dict.fromkeys(("RESERVED", "CONSUMED", "REFUNDED"), 0)
        state_by_event_type = {
            event_type: event_type.rsplit(".", maxsplit=1)[-1].upper()
            for event_type in _BUDGET_RESERVATION_EVENT_TYPES
        }
        for row in rows:
            event_type = str(_row_value(row, "event_type", 0))
            raw_event = _row_value(row, "event", 1)
            if isinstance(raw_event, str):
                try:
                    raw_event = json.loads(raw_event)
                except json.JSONDecodeError as exc:
                    raise _budget_config_error() from exc
            if not isinstance(raw_event, dict):
                raise _budget_config_error()
            validation = validate_audit_event(raw_event)
            state = state_by_event_type.get(event_type)
            payload = raw_event.get("payload")
            safe = payload.get("safe") if isinstance(payload, Mapping) else None
            resource = raw_event.get("resource")
            if (
                not validation.passed
                or state is None
                or raw_event.get("event_type") != event_type
                or raw_event.get("tenant_id") != governed["tenant_id"]
                or not isinstance(resource, Mapping)
                or resource.get("resource_id") != vector["reservation_id"]
                or not isinstance(safe, Mapping)
                or dict(safe) != {**vector, "state": state}
            ):
                raise _budget_config_error()
            counts[state] += 1

        if counts["RESERVED"] != 1 or counts["CONSUMED"] + counts["REFUNDED"] > 1:
            raise _budget_config_error()
        if counts["CONSUMED"] == 1:
            return "CONSUMED"
        if counts["REFUNDED"] == 1:
            return "REFUNDED"
        return "RESERVED"

    def _emit_reservation_event(
        self,
        connection: Any,
        governed: Mapping[str, object],
        *,
        state: str,
    ) -> None:
        from idis.audit.postgres_sink import PostgresAuditSink
        from idis.validators.audit_event_validator import validate_audit_event

        event_id = str(uuid4())
        vector = self._reservation_vector(governed)
        operation = state.lower()
        event: dict[str, object] = {
            "event_id": event_id,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tenant_id": str(governed["tenant_id"]),
            "event_type": f"integration.provider_budget.{operation}",
            "severity": "HIGH",
            "summary": f"Provider budget reservation {operation}",
            "actor": {
                "actor_type": "SERVICE",
                "actor_id": "provider-budget-store",
                "roles": ["INTEGRATION_SERVICE"],
            },
            "request": {
                "request_id": f"provider-budget-{event_id[:8]}",
                "method": "POST",
                "path": "/internal/provider-budget/reservations",
                "status_code": 200,
            },
            "resource": {
                "resource_type": "integration",
                "resource_id": vector["reservation_id"],
            },
            "payload": {
                "safe": {**vector, "state": state},
                "hashes": [f"sha256:{vector['policy_hash']}"],
                "refs": [
                    f"component_id:{vector['component_id']}",
                    f"reservation_id:{vector['reservation_id']}",
                ],
            },
        }
        if not validate_audit_event(event).passed:
            raise _budget_config_error()
        try:
            PostgresAuditSink().emit_in_tx(connection, event)
        except Exception as exc:
            raise _budget_config_error() from exc

    def snapshot(self, **values: object) -> dict[str, object]:
        required = {"tenant_id", "policy_hash", "component_id"}
        if set(values) != required:
            raise _budget_config_error()
        trusted_cap = self._trusted_global_max_calls
        if trusted_cap is None:
            raise _budget_config_error()
        probe = self._trusted_governed_values(
            {
                **values,
                "calls": 1,
                "usd": Decimal("0"),
                "input_tokens": 0,
                "output_tokens": 0,
                "embedding_tokens": 0,
                "global_max_calls": trusted_cap,
            },
            require_reservation_id=False,
        )
        from sqlalchemy import text

        with self._connection() as connection:
            self._set_tenant(connection, str(probe["tenant_id"]))
            row = connection.execute(text(_GOVERNED_SNAPSHOT_SQL), probe).fetchone()
        if row is None:
            return {"reserved": -1, "consumed": 0}
        names = (
            "used",
            "policy_hash",
            "reserved_calls",
            "reserved_usd",
            "consumed_usd",
            "reserved_input_tokens",
            "consumed_input_tokens",
            "reserved_output_tokens",
            "consumed_output_tokens",
            "reserved_embedding_tokens",
            "consumed_embedding_tokens",
        )
        result = {name: _row_value(row, name, index) for index, name in enumerate(names)}
        result["reserved"] = int(str(result["reserved_calls"] or 0))
        result["consumed"] = int(str(result["used"] or 0))
        return result

    def _lock_policy(self, connection: Any, tenant_id: str) -> None:
        from sqlalchemy import text

        self._set_tenant(connection, tenant_id)
        connection.execute(
            text(_GOVERNED_POLICY_LOCK_SQL),
            {"lock_key": f"idis:provider-budget:{tenant_id}"},
        )

    @staticmethod
    def _usage_balances(row: object | None) -> dict[str, object]:
        if row is None:
            return {
                "used": 0,
                "policy_hash": None,
                "reserved_calls": 0,
                "reserved_usd": Decimal("0"),
                "consumed_usd": Decimal("0"),
                "reserved_input_tokens": 0,
                "consumed_input_tokens": 0,
                "reserved_output_tokens": 0,
                "consumed_output_tokens": 0,
                "reserved_embedding_tokens": 0,
                "consumed_embedding_tokens": 0,
            }
        names = (
            "provider",
            "used",
            "policy_hash",
            "reserved_calls",
            "reserved_usd",
            "consumed_usd",
            "reserved_input_tokens",
            "consumed_input_tokens",
            "reserved_output_tokens",
            "consumed_output_tokens",
            "reserved_embedding_tokens",
            "consumed_embedding_tokens",
        )
        values = {name: _row_value(row, name, index) for index, name in enumerate(names)}
        for name in ("reserved_usd", "consumed_usd"):
            values[name] = Decimal(str(values[name] or 0))
        for name in names:
            if name not in {"provider", "policy_hash", "reserved_usd", "consumed_usd"}:
                values[name] = int(str(values[name] or 0))
        return values

    @staticmethod
    def _token_within_cap(
        current: Mapping[str, object],
        dimension: str,
        amount: int,
        cap: object,
    ) -> bool:
        if amount == 0 and cap is None:
            return True
        if cap is None:
            return False
        consumed = int(str(current[f"consumed_{dimension}_tokens"]))
        reserved = int(str(current[f"reserved_{dimension}_tokens"]))
        cap_value = int(str(cap))
        return consumed + reserved + amount <= cap_value

    def _active_component_caps(
        self, connection: Any, governed: Mapping[str, object]
    ) -> dict[str, object]:
        from sqlalchemy import text

        from idis.providers.budget_policy import EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS

        row = connection.execute(
            text(_GOVERNED_ACTIVE_COMPONENT_SQL),
            {**governed, "component_count": len(EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS)},
        ).fetchone()
        if row is None or str(_row_value(row, "currency", 1)) != "USD":
            raise _budget_config_error()
        names = (
            "policy_hash",
            "currency",
            "total_max_usd",
            "max_calls",
            "max_usd",
            "max_input_tokens",
            "max_output_tokens",
            "max_embedding_tokens",
        )
        return {name: _row_value(row, name, index) for index, name in enumerate(names)}

    def _usage_rows(self, connection: Any, tenant_id: str) -> list[object]:
        from sqlalchemy import text

        return list(
            connection.execute(text(_GOVERNED_USAGE_ROWS_SQL), {"tenant_id": tenant_id}).fetchall()
        )

    def _reserve_legacy(self, *, key: str, amount: int, cap: int) -> tuple[bool, int]:
        """Atomically reserve call capacity without crossing the consumed-plus-reserved cap."""
        if self.governed_only:
            raise _budget_config_error()
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        if amount <= 0 or cap <= 0:
            raise ValueError("provider budget reservation values must be positive")
        tenant_id, provider = _parse_budget_key(key)
        params = {"tenant_id": tenant_id, "provider": provider, "amount": amount, "cap": cap}
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            row = conn.execute(text(_RESERVE_SQL), params).fetchone()
            if row is not None:
                return (True, int(row[0]))
            current = conn.execute(
                text(_CURRENT_RESERVED_SQL), {"tenant_id": tenant_id, "provider": provider}
            ).scalar()
        return (False, int(current or 0))

    def _consume_legacy(self, *, key: str, amount: int, cap: int) -> tuple[bool, int]:
        if self.governed_only:
            raise _budget_config_error()
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        if amount <= 0 or cap <= 0:
            raise ValueError("provider budget consume values must be positive")
        tenant_id, provider = _parse_budget_key(key)
        params = {"tenant_id": tenant_id, "provider": provider, "amount": amount, "cap": cap}
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            row = conn.execute(text(_CONSUME_SQL), params).fetchone()
            if row is not None:
                return (True, int(row[0]))
            current = conn.execute(
                text(_CURRENT_USED_SQL), {"tenant_id": tenant_id, "provider": provider}
            ).scalar()
        return (False, int(current or 0))

    def _refund_legacy(self, *, key: str, amount: int) -> tuple[bool, int]:
        """Atomically release reserved call capacity without allowing a negative balance."""
        if self.governed_only:
            raise _budget_config_error()
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        if amount <= 0:
            raise ValueError("provider budget refund amount must be positive")
        tenant_id, provider = _parse_budget_key(key)
        params = {"tenant_id": tenant_id, "provider": provider, "amount": amount}
        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            row = conn.execute(text(_REFUND_SQL), params).fetchone()
            if row is not None:
                return (True, int(row[0]))
            current = conn.execute(
                text(_CURRENT_RESERVED_SQL), {"tenant_id": tenant_id, "provider": provider}
            ).scalar()
        return (False, int(current or 0))


@dataclass(frozen=True)
class ProviderBudgetConfig:
    """Resolved budget configuration. ``max_calls`` is ``None`` when budgeting is disabled."""

    max_calls: int | None


def load_provider_budget_config(env: Mapping[str, str] | None = None) -> ProviderBudgetConfig:
    """Resolve the budget config from the environment.

    ``IDIS_PROVIDER_BUDGET_MAX_CALLS`` set to a positive integer enables a hard cap of that many
    live provider calls per ``{tenant}:{provider}``. Unset, empty, non-integer, or non-positive
    values leave budgeting disabled (fail-safe: a misconfigured cap never blocks live runs).
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    raw = source.get(ENV_MAX_CALLS)
    if raw is None or not raw.strip():
        return ProviderBudgetConfig(max_calls=None)
    try:
        value = int(raw.strip())
    except ValueError:
        return ProviderBudgetConfig(max_calls=None)
    if value <= 0:
        return ProviderBudgetConfig(max_calls=None)
    return ProviderBudgetConfig(max_calls=value)


class ProviderBudget:
    """A per-tenant/provider hard cap over an injectable accounting store."""

    def __init__(
        self,
        *,
        config: ProviderBudgetConfig | None = None,
        store: ProviderBudgetStore | None = None,
    ) -> None:
        self._config = config if config is not None else ProviderBudgetConfig(max_calls=None)
        self._store: ProviderBudgetStore = (
            store if store is not None else InMemoryProviderBudgetStore()
        )

    @property
    def enabled(self) -> bool:
        return self._config.max_calls is not None

    def charge(self, *, tenant_id: str, provider: str, amount: int = 1) -> None:
        """Record ``amount`` of live-provider spend, or raise before it is incurred.

        No-op when disabled. When enabled, atomically consumes against the hard cap for
        ``{tenant_id}:{provider}`` and raises ``ProviderBudgetExceededError`` (leaving the total
        unchanged) if the cap would be exceeded -- so the caller never performs the live call.
        """
        cap = self._config.max_calls
        if cap is None:
            return
        from idis.services.runs.strict_full_live import (
            IDIS_STRICT_DOTENV_PATH_ENV,
            is_strict_full_live_required,
        )

        if is_strict_full_live_required(dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)):
            # A call-only charge cannot represent the signed USD/token reservation vector.
            # Strict callers must use the governed reserve/consume/refund contract instead.
            raise _budget_config_error()
        key = f"{tenant_id}:{provider}"
        allowed, used = self._store.consume(key=key, amount=amount, cap=cap)
        if not allowed:
            raise ProviderBudgetExceededError(
                provider=provider, tenant_id=tenant_id, limit=cap, used=used
            )


class BudgetedLLMClient:
    """Wraps an ``LLMClient`` and enforces the provider budget BEFORE delegating ``.call``."""

    def __init__(
        self, inner: LLMClient, *, tenant_id: str, provider: str, budget: ProviderBudget
    ) -> None:
        self._inner = inner
        self._tenant_id = tenant_id
        self._provider = provider
        self._budget = budget

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        # Gate first: raises PROVIDER_BUDGET_EXCEEDED before the wrapped client is invoked.
        self._budget.charge(tenant_id=self._tenant_id, provider=self._provider)
        return self._inner.call(prompt, json_mode=json_mode)


def build_default_provider_budget_store(
    *, config: ProviderBudgetConfig | None = None
) -> ProviderBudgetStore:
    """Select the durable Postgres store when a database is configured, else the in-memory fallback.

    DEC-C: a DB-configured deployment uses the Postgres-backed store so the hard cap holds across
    replicas and survives restarts; single-process dev / tests without a database fall back to the
    in-memory store -- a hermetic dev/test fallback, never the production proof.
    """
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        from idis.services.runs.strict_full_live import (
            IDIS_STRICT_DOTENV_PATH_ENV,
            is_strict_full_live_required,
        )

        if not is_strict_full_live_required(
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
        ):
            # Slice96 compatibility is deliberately unbound and therefore cannot be certified
            # by strict admission. It remains available only while strict full-live is off.
            return cast(ProviderBudgetStore, PostgresProviderBudgetStore())
        resolved_config = config if config is not None else load_provider_budget_config()
        return cast(
            ProviderBudgetStore,
            PostgresProviderBudgetStore(global_max_calls=resolved_config.max_calls),
        )
    return InMemoryProviderBudgetStore()


_DEFAULT_BUDGET: ProviderBudget | None = None
_DEFAULT_LOCK = threading.Lock()


def default_provider_budget() -> ProviderBudget:
    """Return the lazily-built process-default budget.

    Config comes from the environment; the store is chosen by
    ``build_default_provider_budget_store`` (Postgres-backed when a database is configured,
    in-memory fallback otherwise).
    """
    global _DEFAULT_BUDGET
    budget = _DEFAULT_BUDGET
    if budget is None:
        with _DEFAULT_LOCK:
            budget = _DEFAULT_BUDGET
            if budget is None:
                config = load_provider_budget_config()
                budget = ProviderBudget(
                    config=config,
                    store=build_default_provider_budget_store(config=config),
                )
                _DEFAULT_BUDGET = budget
    return budget


def reset_default_provider_budget() -> None:
    """Rebuild the process-default budget from the current environment (wiring/test hook)."""
    global _DEFAULT_BUDGET
    with _DEFAULT_LOCK:
        _DEFAULT_BUDGET = None


def wrap_with_provider_budget(
    client: LLMClient,
    *,
    tenant_id: str,
    provider: str = DEFAULT_PROVIDER,
    budget: ProviderBudget | None = None,
) -> LLMClient:
    """Wrap ``client`` so live calls are budget-gated, or return it unchanged when disabled.

    Uses the process-default budget unless one is injected. When the budget is disabled (no cap
    configured) the client is returned as-is -- a true passthrough, so normal runs and tests are
    unaffected until a cap is set.
    """
    effective = budget if budget is not None else default_provider_budget()
    if not effective.enabled:
        return client
    return BudgetedLLMClient(client, tenant_id=tenant_id, provider=provider, budget=effective)
