"""Provider-spend budget: a minimal per-tenant/provider hard cap (Slice96 DEC-C).

An injectable ``ProviderBudgetStore`` accounting seam tracks the number of live provider calls per
``{tenant}:{provider}``. The production default is the durable, cross-replica Postgres-backed store
when a database is configured; the in-memory store is only a hermetic dev/test fallback (single
process). ``ProviderBudget.charge`` records one unit of spend and raises a safe, fail-closed
``ProviderBudgetExceededError`` (stable code ``PROVIDER_BUDGET_EXCEEDED``) BEFORE the caller
performs the live call when the configured hard cap would be exceeded. ``BudgetedLLMClient`` wraps
any ``LLMClient`` so the gate runs before the wrapped client is invoked. Disabled unless
``IDIS_PROVIDER_BUDGET_MAX_CALLS`` is set to a positive integer -> passthrough, no behavior change.

The unit is a live-call count (a coarse spend proxy) -- deliberately minimal: no token/cost
accounting, no model price table, no billing system (Slice96 DEC-C locked scope). The denial is
safe-shape: only a stable code plus non-sensitive counts (provider, tenant, limit, used) -- never
prompt text, provider payloads, secrets, or raw cost details.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from idis.services.extraction.extractors.llm_client import LLMClient

PROVIDER_BUDGET_EXCEEDED: Final = "PROVIDER_BUDGET_EXCEEDED"
ENV_MAX_CALLS: Final = "IDIS_PROVIDER_BUDGET_MAX_CALLS"
DEFAULT_PROVIDER: Final = "anthropic"


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


class PostgresProviderBudgetStore:
    """Durable, cross-replica ``ProviderBudgetStore`` backed by ``provider_budget_usage`` (RLS).

    Each consume runs the race-safe atomic upsert-under-cap in its own committed, tenant-RLS-scoped
    transaction on the application connection, so the hard cap holds across replicas and survives
    restarts (unlike a per-process in-memory counter). Connects lazily per consume; constructing the
    store touches no database.
    """

    def consume(self, *, key: str, amount: int, cap: int) -> tuple[bool, int]:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

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


def build_default_provider_budget_store() -> ProviderBudgetStore:
    """Select the durable Postgres store when a database is configured, else the in-memory fallback.

    DEC-C: a DB-configured deployment uses the Postgres-backed store so the hard cap holds across
    replicas and survives restarts; single-process dev / tests without a database fall back to the
    in-memory store -- a hermetic dev/test fallback, never the production proof.
    """
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresProviderBudgetStore()
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
                budget = ProviderBudget(
                    config=load_provider_budget_config(),
                    store=build_default_provider_budget_store(),
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
