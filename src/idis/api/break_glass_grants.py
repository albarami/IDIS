"""Durable break-glass grant store: issuance records + strict single-use consumption (Slice98).

The break-glass core (``break_glass.py``) mints stateless HMAC tokens; historically a valid token
was replayable until expiry and nothing recorded issuance. This module provides the durable,
cross-replica grant record behind a seam mirroring the ABAC assignment-store pattern:

- ``BreakGlassGrant``: the recorded grant (grant_id = the token's token_id; token_sha256 = the
  FULL 64-char SHA-256 of the raw token string - enforcement never uses a truncated hash).
- ``BreakGlassGrantStore`` Protocol: ``record_grant`` / ``consume_grant`` / ``get_grant``.
- ``InMemoryBreakGlassGrantStore``: hermetic twin (lock-guarded so single-use holds under race).
- ``PostgresBreakGlassGrantStore``: durable twin over ``break_glass_grants`` (migration 0028);
  consumption is one atomic conditional UPDATE, so exactly one caller can ever win.
- Seam ``get_/set_/reset_/build_default_break_glass_grant_store``: Postgres when configured.

Enforcement is gated by ``IDIS_ENABLE_DURABLE_BREAK_GLASS`` (default off): when off, the legacy
stateless-token behavior stays the active authorization path and this store is never consulted by
RBACMiddleware; when on, a break-glass override REQUIRES an unconsumed, unexpired recorded grant
and consumption marks it used (strict single-use). Grants are consumed only when they actually
supply the ABAC override - never merely because a token rides on an already-authorized request.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from idis.api.errors import IdisHttpError

logger = logging.getLogger(__name__)

IDIS_DURABLE_BREAK_GLASS_ENV = "IDIS_ENABLE_DURABLE_BREAK_GLASS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_durable_break_glass_enabled() -> bool:
    """True when durable single-use break-glass enforcement is explicitly enabled (default off)."""
    return os.environ.get(IDIS_DURABLE_BREAK_GLASS_ENV, "").strip().lower() in _TRUTHY


@dataclass(frozen=True, slots=True)
class BreakGlassGrant:
    """A durable break-glass grant record.

    Attributes:
        grant_id: The token's token_id (server-generated uuid).
        tenant_id: Tenant scope.
        deal_id: Deal the grant is bound to.
        actor_id: The admin the token is bound to (self-issuance).
        justification: Plaintext justification (RLS-protected governance record; the audit
            trail keeps its hash+length-only posture and logs never carry it).
        token_sha256: FULL 64-char SHA-256 hex of the raw token string (enforcement key).
        issued_at: Unix timestamp of issuance.
        expires_at: Unix timestamp of expiry.
        consumed_at: Unix timestamp of single-use consumption, or None while unconsumed.
        consumed_request_id: Request that consumed the grant, if any.
    """

    grant_id: str
    tenant_id: str
    deal_id: str
    actor_id: str
    justification: str
    token_sha256: str
    issued_at: float
    expires_at: float
    consumed_at: float | None = None
    consumed_request_id: str | None = None


@runtime_checkable
class BreakGlassGrantStore(Protocol):
    """Seam for durable break-glass grants (single-use consumption)."""

    def record_grant(self, grant: BreakGlassGrant) -> None:
        """Persist a newly issued grant. Raises on failure (issuance must fail loudly)."""
        ...

    def consume_grant(
        self, tenant_id: str, token_sha256: str, *, request_id: str | None = None
    ) -> bool:
        """Atomically mark the grant consumed; True only for THE one winning consumption.

        False for unknown, already-consumed, or expired grants (uniform - no oracle
        distinguishing which). Implementations MUST raise on backend failure so the caller
        denies (fail closed), never returning False for an infrastructure error silently.
        """
        ...

    def get_grant(self, tenant_id: str, grant_id: str) -> BreakGlassGrant | None:
        """Return the grant (consumed or not), or None if absent under this tenant."""
        ...


class InMemoryBreakGlassGrantStore:
    """Process-local twin. Not durable; for tests and non-Postgres deployments."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], BreakGlassGrant] = {}  # (tenant_id, grant_id)
        self._by_token: dict[tuple[str, str], str] = {}  # (tenant_id, token_sha256) -> grant_id
        self._lock = threading.Lock()

    def record_grant(self, grant: BreakGlassGrant) -> None:
        with self._lock:
            token_key = (grant.tenant_id, grant.token_sha256)
            if token_key in self._by_token:
                raise IdisHttpError(
                    status_code=500,
                    code="break_glass_grant_record_failed",
                    message="Break-glass grant could not be recorded",
                )
            self._grants[(grant.tenant_id, grant.grant_id)] = grant
            self._by_token[token_key] = grant.grant_id

    def consume_grant(
        self, tenant_id: str, token_sha256: str, *, request_id: str | None = None
    ) -> bool:
        import time

        with self._lock:
            grant_id = self._by_token.get((tenant_id, token_sha256))
            if grant_id is None:
                return False
            grant = self._grants[(tenant_id, grant_id)]
            now = time.time()
            if grant.consumed_at is not None or now >= grant.expires_at:
                return False
            self._grants[(tenant_id, grant_id)] = replace(
                grant, consumed_at=now, consumed_request_id=request_id
            )
            return True

    def get_grant(self, tenant_id: str, grant_id: str) -> BreakGlassGrant | None:
        with self._lock:
            return self._grants.get((tenant_id, grant_id))


def _ts(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


class PostgresBreakGlassGrantStore:
    """Durable twin over ``break_glass_grants`` (migration 0028, guarded RLS).

    Consumption is a single conditional UPDATE (``consumed_at IS NULL AND expires_at > now()``),
    so under concurrency exactly one caller wins - the database is the arbiter, not the process.
    A backend failure raises fail-closed (deny), mirroring the ABAC-store precedent.
    """

    def record_grant(self, grant: BreakGlassGrant) -> None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, grant.tenant_id)
                conn.execute(
                    text(
                        """
                        INSERT INTO break_glass_grants (
                            tenant_id, grant_id, deal_id, actor_id, justification,
                            token_sha256, issued_at, expires_at
                        ) VALUES (
                            CAST(:tenant_id AS uuid), CAST(:grant_id AS uuid),
                            CAST(:deal_id AS uuid), :actor_id, :justification,
                            :token_sha256, :issued_at, :expires_at
                        )
                        """
                    ),
                    {
                        "tenant_id": grant.tenant_id,
                        "grant_id": grant.grant_id,
                        "deal_id": grant.deal_id,
                        "actor_id": grant.actor_id,
                        "justification": grant.justification,
                        "token_sha256": grant.token_sha256,
                        "issued_at": _ts(grant.issued_at),
                        "expires_at": _ts(grant.expires_at),
                    },
                )
        except Exception as e:
            logger.error("PostgresBreakGlassGrantStore record failed: %s", str(e))
            raise IdisHttpError(
                status_code=500,
                code="break_glass_grant_record_failed",
                message="Break-glass grant could not be recorded",
            ) from e

    def consume_grant(
        self, tenant_id: str, token_sha256: str, *, request_id: str | None = None
    ) -> bool:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, tenant_id)
                row = conn.execute(
                    text(
                        """
                        UPDATE break_glass_grants
                        SET consumed_at = now(), consumed_request_id = :request_id
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                            AND token_sha256 = :token_sha256
                            AND consumed_at IS NULL
                            AND expires_at > now()
                        RETURNING grant_id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "token_sha256": token_sha256,
                        "request_id": request_id,
                    },
                ).fetchone()
                return row is not None
        except Exception as e:
            logger.error("PostgresBreakGlassGrantStore consume failed: %s", str(e))
            raise IdisHttpError(
                status_code=403,
                code="BREAK_GLASS_RESOLUTION_FAILED",
                message="Access denied.",
            ) from e

    def get_grant(self, tenant_id: str, grant_id: str) -> BreakGlassGrant | None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            row = conn.execute(
                text(
                    """
                    SELECT grant_id, tenant_id, deal_id, actor_id, justification, token_sha256,
                           issued_at, expires_at, consumed_at, consumed_request_id
                    FROM break_glass_grants
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                        AND grant_id = CAST(:grant_id AS uuid)
                    """
                ),
                {"tenant_id": tenant_id, "grant_id": grant_id},
            ).fetchone()
        if row is None:
            return None
        return BreakGlassGrant(
            grant_id=str(row.grant_id),
            tenant_id=str(row.tenant_id),
            deal_id=str(row.deal_id),
            actor_id=row.actor_id,
            justification=row.justification,
            token_sha256=row.token_sha256,
            issued_at=row.issued_at.timestamp(),
            expires_at=row.expires_at.timestamp(),
            consumed_at=row.consumed_at.timestamp() if row.consumed_at is not None else None,
            consumed_request_id=row.consumed_request_id,
        )


_store: BreakGlassGrantStore | None = None


def build_default_break_glass_grant_store() -> BreakGlassGrantStore:
    """Select the durable Postgres store when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresBreakGlassGrantStore()
    return InMemoryBreakGlassGrantStore()


def get_break_glass_grant_store() -> BreakGlassGrantStore:
    """Return the process-wide grant store, building the default on first use."""
    global _store
    if _store is None:
        _store = build_default_break_glass_grant_store()
    return _store


def set_break_glass_grant_store(store: BreakGlassGrantStore) -> None:
    """Override the process-wide store (tests / explicit wiring)."""
    global _store
    _store = store


def reset_break_glass_grant_store() -> None:
    """Clear the process-wide store so the next access rebuilds the default."""
    global _store
    _store = None
