"""Bind canonical API-inline audit channels to their actual request transaction."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from sqlalchemy import Connection

from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.validators.audit_event_validator import validate_audit_event


class RequestBoundPostgresAuditSink(PostgresAuditSink):
    """No new connection, commit, worker impersonation, or malformed-event repair."""

    def __init__(
        self,
        *,
        connection: Connection,
        delegate: PostgresAuditSink,
        tenant_id: str,
        actor_id: str,
        actor_type: str,
        actor_roles: Iterable[str],
        request_id: str,
        method: str,
        path: str,
        status_code: int,
    ) -> None:
        self._connection = connection
        self._delegate = delegate
        self._tenant_id = tenant_id
        self._failure: AuditSinkError | None = None
        self._actor = {
            "actor_id": actor_id,
            "actor_type": actor_type,
            "roles": sorted(set(actor_roles)),
        }
        self._request = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
        }

    def emit(self, event: dict[str, Any]) -> None:
        try:
            if event.get("tenant_id") != self._tenant_id:
                raise AuditSinkError("REQUEST_AUDIT_TENANT_MISMATCH")
            if not validate_audit_event(event).passed:
                raise AuditSinkError("REQUEST_AUDIT_CANONICAL_EVENT_REQUIRED")
            bound = deepcopy(event)
            bound["actor"] = dict(self._actor)
            bound["request"] = {**bound["request"], **self._request}
            bound["payload"]["safe"]["execution_origin"] = "API_INLINE"
            self._delegate.emit_in_tx(self._connection, bound)
        except AuditSinkError as exc:
            self._failure = exc
            raise

    def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
        if conn is not self._connection:
            self._failure = AuditSinkError("REQUEST_AUDIT_CONNECTION_MISMATCH")
            raise self._failure
        self.emit(event)

    def raise_if_failed(self) -> None:
        """A best-effort observer cannot suppress a durable request failure."""
        if self._failure is not None:
            raise self._failure
