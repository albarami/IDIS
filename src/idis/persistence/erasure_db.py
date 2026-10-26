"""Dedicated PostgreSQL connection seam for the Task-7 erasure authority.

The adapter intentionally exposes only the reviewed session advisory lock and
the hash-bound database function.  It contains no row-deletion SQL and does not
resolve application or administrative credentials.  The strict composition
root must inject an engine authenticated by the dedicated erasure credential.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text

from idis.compliance.task7_erasure import (
    TASK7_ERASURE_EVENT_TYPES,
    Task7ErasureExecutionCommand,
    canonical_event_sha256,
)
from idis.persistence.db import set_tenant_local
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine

TENANT_LOCK_DOMAIN = "idis-task7-erasure-tenant-lock-v1:"
APPROVED_ERASURE_FUNCTION_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"

_ACQUIRE_TENANT_LOCK_SQL = text(
    """
    SELECT pg_catalog.pg_advisory_lock(
        pg_catalog.hashtextextended(:lock_name, 0)
    )
    """
)
_RELEASE_TENANT_LOCK_SQL = text(
    """
    SELECT pg_catalog.pg_advisory_unlock(
        pg_catalog.hashtextextended(:lock_name, 0)
    )
    """
)
_INVOKE_APPROVED_ERASURE_SQL = text(
    """
    SELECT public.idis_task7_execute_approved_erasure(
        CAST(:request_id AS uuid),
        CAST(:tenant_id AS uuid),
        CAST(:deal_id AS uuid)
    ) AS result
    """
)
_RESOLVE_REQUEST_SQL = text(
    """
    SELECT tenant_id, deal_id, request_id, status, executed_by, executed_at, counts
    FROM public.erasure_requests
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND request_id = CAST(:request_id AS uuid)
    """
)
_LOAD_REQUEST_SQL = text(
    """
    SELECT tenant_id, deal_id, request_id, status, executed_by, executed_at, counts
    FROM public.erasure_requests
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
      AND request_id = CAST(:request_id AS uuid)
    """
)
_LOAD_LINEAGE_SQL = text(
    """
    SELECT event
    FROM public.audit_events
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND event_type IN (
          'task7.erasure.attempted',
          'task7.erasure.failed',
          'task7.erasure.succeeded'
      )
      AND event->'payload'->'safe'->>'request_id' = :request_id
      AND event->'resource'->>'deal_id' = :deal_id
    ORDER BY
      CAST(event->'payload'->'safe'->>'attempt_ordinal' AS integer),
      CASE event_type
        WHEN 'task7.erasure.attempted' THEN 0
        WHEN 'task7.erasure.failed' THEN 1
        WHEN 'task7.erasure.succeeded' THEN 2
        ELSE 3
      END,
      event_id
    """
)
_LIST_OBJECT_TARGETS_SQL = text(
    """
    SELECT doc_id, uri
    FROM public.document_artifacts
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
    ORDER BY uri COLLATE "C", doc_id
    """
)
_ASSERT_NO_ACTIVE_HOLDS_SQL = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM public.legal_holds AS h
        WHERE h.tenant_id = CAST(:tenant_id AS uuid)
          AND h.lifted_at IS NULL
          AND (
              (h.target_type = 'DEAL' AND h.target_id = :deal_id)
              OR (
                  h.target_type = 'DOCUMENT'
                  AND EXISTS (
                      SELECT 1
                      FROM public.document_artifacts AS a
                      WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                        AND a.deal_id = CAST(:deal_id AS uuid)
                        AND a.doc_id::text = h.target_id
                  )
              )
              OR (
                  h.target_type = 'ARTIFACT'
                  AND EXISTS (
                      SELECT 1
                      FROM public.document_artifacts AS a
                      WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                        AND a.deal_id = CAST(:deal_id AS uuid)
                        AND a.uri = 'idis://' || h.target_id
                  )
              )
          )
    ) AS blocked
    """
)
_INSERT_LINEAGE_SQL = text(
    """
    INSERT INTO public.audit_events (
        event_id, tenant_id, occurred_at, event_type,
        request_id, idempotency_key, event
    ) VALUES (
        CAST(:event_id AS uuid), CAST(:tenant_id AS uuid), :occurred_at, :event_type,
        :request_id, NULL, CAST(:event AS jsonb)
    )
    ON CONFLICT (event_id) DO NOTHING
    RETURNING event
    """
)
_LOAD_EVENT_BY_ID_SQL = text(
    """
    SELECT event
    FROM public.audit_events
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND event_id = CAST(:event_id AS uuid)
    """
)
_MARK_FAILED_SQL = text(
    """
    UPDATE public.erasure_requests
    SET status = 'FAILED',
        executed_by = :executed_by,
        executed_at = :executed_at
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
      AND request_id = CAST(:request_id AS uuid)
      AND status IN ('REQUESTED', 'FAILED')
    """
)
_STAGE_OBJECT_COUNT_SQL = text(
    """
    UPDATE public.erasure_requests
    SET counts = pg_catalog.jsonb_build_object(
        'object_rows_reconciled',
        CAST(:object_rows_reconciled AS bigint)
    )
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
      AND request_id = CAST(:request_id AS uuid)
      AND status IN ('REQUESTED', 'FAILED')
    """
)


class DedicatedErasureDatabaseError(RuntimeError):
    """Fail-closed dedicated-connection contract error."""


class Task7DedicatedErasureConnection:
    """One backend that retains the session lock across short transactions."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._locked_tenant_id: str | None = None
        self._transaction_open = False
        self._backend_state_ambiguous = False

    def acquire_tenant_session_lock(self, tenant_id: str) -> None:
        if self._locked_tenant_id is not None or self._transaction_open:
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")
        self._backend_state_ambiguous = True
        try:
            self._connection.execute(
                _ACQUIRE_TENANT_LOCK_SQL,
                {"lock_name": f"{TENANT_LOCK_DOMAIN}{tenant_id}"},
            )
            # A session advisory lock survives this transaction boundary.
            # Ending the implicit SQLAlchemy transaction permits external
            # object I/O without a database transaction remaining open.
            self._connection.commit()
        except Exception as exc:
            # Once the lock statement was sent, a transport/commit failure
            # cannot prove whether the backend retains the session lock.
            # Invalidate now and leave the marker set so the factory retries
            # invalidation before this backend could return to the pool.
            try:
                self._connection.invalidate()
            finally:
                raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_ACQUIRE_FAILED") from exc
        self._locked_tenant_id = tenant_id
        self._backend_state_ambiguous = False

    def release_tenant_session_lock(self, tenant_id: str) -> None:
        if self._transaction_open or self._locked_tenant_id != tenant_id:
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")
        released = self._connection.execute(
            _RELEASE_TENANT_LOCK_SQL,
            {"lock_name": f"{TENANT_LOCK_DOMAIN}{tenant_id}"},
        ).scalar_one()
        self._connection.commit()
        if released is not True:
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_RELEASE_FAILED")
        self._locked_tenant_id = None

    def require_tenant_session_lock(self, tenant_id: str) -> None:
        """Prove this adapter currently owns the expected lock token."""

        if self._transaction_open or self._locked_tenant_id != tenant_id:
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")

    @contextmanager
    def begin(self) -> Generator[Task7DedicatedErasureConnection, None, None]:
        if self._locked_tenant_id is None or self._transaction_open:
            raise DedicatedErasureDatabaseError("ERASURE_TRANSACTION_STATE_INVALID")
        self._transaction_open = True
        try:
            with self._connection.begin():
                yield self
        finally:
            self._transaction_open = False

    def invoke_approved_erasure(
        self,
        *,
        request_id: str,
        tenant_id: str,
        deal_id: str,
    ) -> Mapping[str, Any]:
        if not self._transaction_open or self._locked_tenant_id != tenant_id:
            raise DedicatedErasureDatabaseError("ERASURE_TRANSACTION_STATE_INVALID")
        value = self._connection.execute(
            _INVOKE_APPROVED_ERASURE_SQL,
            {
                "request_id": request_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
            },
        ).scalar_one()
        if not isinstance(value, Mapping):
            raise DedicatedErasureDatabaseError("ERASURE_FUNCTION_RESULT_INVALID")
        return dict(value)


@dataclass(frozen=True, slots=True)
class Task7DedicatedErasureConnectionFactory:
    """Injectable factory over an already-authenticated dedicated engine."""

    engine: Engine

    def __call__(
        self,
    ) -> AbstractContextManager[Task7DedicatedErasureConnection]:
        return self._connect()

    @contextmanager
    def _connect(self) -> Generator[Task7DedicatedErasureConnection, None, None]:
        with self.engine.connect() as connection:
            adapter = Task7DedicatedErasureConnection(connection)
            try:
                yield adapter
            finally:
                if adapter._locked_tenant_id is not None or adapter._backend_state_ambiguous:
                    # Backend close also releases a session lock.  Explicit
                    # release remains the successful-path contract; this close
                    # path prevents a leaked or ambiguous pooled session.
                    connection.invalidate()


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DedicatedErasureDatabaseError("ERASURE_DATABASE_ROW_INVALID") from exc
    if not isinstance(value, Mapping):
        raise DedicatedErasureDatabaseError("ERASURE_DATABASE_ROW_INVALID")
    return dict(value)


def _request_mapping(row: object) -> dict[str, object]:
    if not isinstance(row, Mapping):
        raise DedicatedErasureDatabaseError("ERASURE_DATABASE_ROW_INVALID")
    return {
        "tenant_id": str(row["tenant_id"]),
        "deal_id": str(row["deal_id"]),
        "request_id": str(row["request_id"]),
        "status": row["status"],
        "executed_by": row["executed_by"],
        "executed_at": row["executed_at"],
        "counts": _json_mapping(row["counts"] or {}),
    }


@dataclass(frozen=True, slots=True)
class Task7PostgresErasurePort:
    """Application-role RLS port used while the dedicated session lock is held."""

    app_engine: Engine

    def resolve_request(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> Mapping[str, object] | None:
        with self.app_engine.connect() as connection, connection.begin():
            set_tenant_local(connection, tenant_id)
            row = (
                connection.execute(
                    _RESOLVE_REQUEST_SQL,
                    {"tenant_id": tenant_id, "request_id": request_id},
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _request_mapping(row)

    def load_request_state(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> Mapping[str, object]:
        with self.app_engine.connect() as connection, connection.begin():
            set_tenant_local(connection, command.tenant_id)
            row = (
                connection.execute(
                    _LOAD_REQUEST_SQL,
                    {
                        "tenant_id": command.tenant_id,
                        "deal_id": command.deal_id,
                        "request_id": command.request_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DedicatedErasureDatabaseError("ERASURE_REQUEST_NOT_FOUND")
        return _request_mapping(row)

    def load_lineage_events(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> Sequence[Mapping[str, object]]:
        with self.app_engine.connect() as connection, connection.begin():
            set_tenant_local(connection, command.tenant_id)
            rows = connection.execute(
                _LOAD_LINEAGE_SQL,
                {
                    "tenant_id": command.tenant_id,
                    "deal_id": command.deal_id,
                    "request_id": command.request_id,
                },
            )
            events = tuple(_json_mapping(row.event) for row in rows)
        return events

    def list_object_targets(
        self,
        *,
        connection: object,
        command: Task7ErasureExecutionCommand,
    ) -> tuple[str, ...]:
        if not isinstance(connection, Task7DedicatedErasureConnection):
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")
        connection.require_tenant_session_lock(command.tenant_id)
        with self.app_engine.connect() as app_connection, app_connection.begin():
            set_tenant_local(app_connection, command.tenant_id)
            rows = tuple(
                app_connection.execute(
                    _LIST_OBJECT_TARGETS_SQL,
                    {
                        "tenant_id": command.tenant_id,
                        "deal_id": command.deal_id,
                    },
                )
            )
        ordered: list[str] = []
        for row in rows:
            uri = row.uri
            if type(uri) is not str or not uri.startswith("idis://") or uri == "idis://":
                raise DedicatedErasureDatabaseError("ERASURE_ARTIFACT_LOCATOR_INVALID")
            ordered.append(uri)
        return tuple(ordered)

    def assert_no_active_holds(
        self,
        *,
        connection: object,
        command: Task7ErasureExecutionCommand,
    ) -> None:
        if not isinstance(connection, Task7DedicatedErasureConnection):
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")
        connection.require_tenant_session_lock(command.tenant_id)
        with self.app_engine.connect() as app_connection, app_connection.begin():
            set_tenant_local(app_connection, command.tenant_id)
            blocked = app_connection.execute(
                _ASSERT_NO_ACTIVE_HOLDS_SQL,
                {
                    "tenant_id": command.tenant_id,
                    "deal_id": command.deal_id,
                },
            ).scalar_one()
        if blocked is not False:
            from idis.api.errors import IdisHttpError

            raise IdisHttpError(
                status_code=403,
                code="DELETION_BLOCKED_BY_HOLD",
                message="Access denied.",
            )

    def persist_lineage_event(
        self,
        *,
        event: Mapping[str, object],
        request_status: str | None = None,
    ) -> str:
        exact_event = dict(event)
        validation = validate_audit_event(exact_event)
        if not validation.passed:
            raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_INVALID")
        if exact_event.get("event_type") not in TASK7_ERASURE_EVENT_TYPES:
            raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_INVALID")
        if request_status not in {None, "FAILED"}:
            raise DedicatedErasureDatabaseError("ERASURE_REQUEST_STATUS_INVALID")

        tenant_id = str(exact_event["tenant_id"])
        event_id = str(exact_event["event_id"])
        request = _json_mapping(exact_event["request"])
        resource = _json_mapping(exact_event["resource"])
        actor = _json_mapping(exact_event["actor"])
        occurred_at = exact_event["occurred_at"]
        if type(occurred_at) is not str:
            raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_INVALID")
        try:
            occurred_at_value = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_INVALID") from exc

        serialized = json.dumps(
            exact_event,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_hash = canonical_event_sha256(exact_event)
        with self.app_engine.connect() as connection, connection.begin():
            set_tenant_local(connection, tenant_id)
            inserted = connection.execute(
                _INSERT_LINEAGE_SQL,
                {
                    "event_id": event_id,
                    "tenant_id": tenant_id,
                    "occurred_at": occurred_at_value,
                    "event_type": exact_event["event_type"],
                    "request_id": request.get("request_id"),
                    "event": serialized,
                },
            ).scalar_one_or_none()
            if inserted is None:
                inserted = connection.execute(
                    _LOAD_EVENT_BY_ID_SQL,
                    {"tenant_id": tenant_id, "event_id": event_id},
                ).scalar_one_or_none()
            if inserted is None:
                raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_CONFLICT")
            stored = _json_mapping(inserted)
            if canonical_event_sha256(stored) != expected_hash or stored != exact_event:
                raise DedicatedErasureDatabaseError("ERASURE_AUDIT_EVENT_CONFLICT")

            if request_status == "FAILED":
                updated = connection.execute(
                    _MARK_FAILED_SQL,
                    {
                        "tenant_id": tenant_id,
                        "deal_id": resource.get("deal_id"),
                        "request_id": request.get("request_id"),
                        "executed_by": actor.get("actor_id"),
                        "executed_at": occurred_at_value,
                    },
                ).rowcount
                if updated != 1:
                    raise DedicatedErasureDatabaseError("ERASURE_REQUEST_STATE_CONFLICT")
        return expected_hash

    def stage_object_rows_reconciled(
        self,
        *,
        command: Task7ErasureExecutionCommand,
        object_rows_reconciled: int,
    ) -> None:
        if type(object_rows_reconciled) is not int or object_rows_reconciled < 0:
            raise DedicatedErasureDatabaseError("ERASURE_OBJECT_RECONCILIATION_INVALID")
        with self.app_engine.connect() as connection, connection.begin():
            set_tenant_local(connection, command.tenant_id)
            updated = connection.execute(
                _STAGE_OBJECT_COUNT_SQL,
                {
                    "tenant_id": command.tenant_id,
                    "deal_id": command.deal_id,
                    "request_id": command.request_id,
                    "object_rows_reconciled": object_rows_reconciled,
                },
            ).rowcount
            if updated != 1:
                raise DedicatedErasureDatabaseError("ERASURE_REQUEST_STATE_CONFLICT")

    def invoke_approved_erasure(
        self,
        *,
        connection: object,
        command: Task7ErasureExecutionCommand,
        attempt_id: str,
    ) -> Mapping[str, object]:
        try:
            UUID(attempt_id)
        except ValueError as exc:
            raise DedicatedErasureDatabaseError("ERASURE_ATTEMPT_ID_INVALID") from exc
        if not isinstance(connection, Task7DedicatedErasureConnection):
            raise DedicatedErasureDatabaseError("ERASURE_SESSION_LOCK_STATE_INVALID")
        return connection.invoke_approved_erasure(
            request_id=command.request_id,
            tenant_id=command.tenant_id,
            deal_id=command.deal_id,
        )
