"""Short-lived, tenant-scoped database reads for provider execution.

The leased worker must not retain a PostgreSQL transaction while a provider or
LLM is running.  It also must not let legacy producer code write outside the
fenced completion-boundary unit of work.  ``ProviderReadConnection`` provides
the narrow SQLAlchemy-connection surface needed by existing read repositories:
each read is executed and fully detached in its own transaction, while every
mutation or explicit transaction request fails closed.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from idis.persistence.db import DatabaseConfigError, _validate_tenant_id, set_tenant_local


class UnstagedProviderPersistenceError(RuntimeError):
    """A provider attempted persistence outside its fenced completion boundary."""

    code = "PIPELINE_CONTRACT_UNAVAILABLE"


_READ_PREFIXES = ("SELECT", "SHOW", "EXPLAIN")
_MUTATING_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|CALL|COPY|CREATE|ALTER|DROP|TRUNCATE|"
    r"GRANT|REVOKE|REFRESH|VACUUM|ANALYZE|REINDEX|CLUSTER|LOCK)\b",
    re.IGNORECASE,
)
_SIDE_EFFECTING_SELECT = re.compile(
    r"\b(?:NEXTVAL|SETVAL|PG_ADVISORY_LOCK|PG_TRY_ADVISORY_LOCK|LO_CREATE|LO_UNLINK)\s*\(",
    re.IGNORECASE,
)
_ROW_LOCKING_SELECT = re.compile(
    r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b|\bFOR\s+(?:KEY\s+)?SHARE\b",
    re.IGNORECASE,
)


def _assert_detachable_read(statement: Any) -> None:
    sql = str(statement).strip()
    normalized = sql.upper()
    if not normalized.startswith(_READ_PREFIXES):
        raise UnstagedProviderPersistenceError(
            "provider persistence requires a reviewed completion-boundary stager"
        )
    if ";" in sql.rstrip(";"):
        raise UnstagedProviderPersistenceError(
            "provider database reads must contain exactly one statement"
        )
    if (
        _MUTATING_SQL.search(sql)
        or _SIDE_EFFECTING_SELECT.search(sql)
        or _ROW_LOCKING_SELECT.search(sql)
    ):
        raise UnstagedProviderPersistenceError(
            "provider persistence requires a reviewed completion-boundary stager"
        )


class _DetachedResult:
    """Delegate SQLAlchemy result consumption to a fully buffered result."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def __iter__(self) -> Any:
        return iter(self._result)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._result, name)


class ProviderReadConnection:
    """Connection-like facade whose reads never span provider execution."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        tenant_id: str,
    ) -> None:
        if not _validate_tenant_id(tenant_id):
            raise DatabaseConfigError("Invalid tenant_id format")
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id
        self._write_attempted = threading.Event()

    def _guard_statement(self, statement: Any) -> None:
        try:
            _assert_detachable_read(statement)
        except UnstagedProviderPersistenceError:
            self._write_attempted.set()
            raise

    def execute(
        self,
        statement: Any,
        parameters: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run one read-only statement and detach all rows before returning."""
        if (
            type(statement) is TextClause
            and statement.text == f"SET LOCAL idis.tenant_id = '{self._tenant_id}'"
            and parameters is None
            and not args
            and not kwargs
        ):
            # A repository reasserts the facade's already validated scope. No
            # SQL or connection is needed until the next real read transaction.
            return None
        self._guard_statement(statement)
        with self._connection_factory() as connection:
            # Database enforcement complements the SQL allow-list and prevents a
            # read-looking function from mutating durable state.
            connection.execute(text("SET TRANSACTION READ ONLY"))
            set_tenant_local(connection, self._tenant_id)
            if parameters is None:
                live_result = connection.execute(statement, *args, **kwargs)
            else:
                live_result = connection.execute(statement, parameters, *args, **kwargs)
            freeze = getattr(live_result, "freeze", None)
            if not callable(freeze):
                raise UnstagedProviderPersistenceError(
                    "provider database result cannot be detached from its read transaction"
                )
            frozen = freeze()
        return _DetachedResult(frozen())

    def exec_driver_sql(
        self,
        statement: str,
        parameters: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Apply the same guard to SQLAlchemy's driver-SQL convenience path."""
        self._guard_statement(statement)
        with self._connection_factory() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            set_tenant_local(connection, self._tenant_id)
            if parameters is None:
                live_result = connection.exec_driver_sql(statement, *args, **kwargs)
            else:
                live_result = connection.exec_driver_sql(
                    statement,
                    parameters,
                    *args,
                    **kwargs,
                )
            freeze = getattr(live_result, "freeze", None)
            if not callable(freeze):
                raise UnstagedProviderPersistenceError(
                    "provider database result cannot be detached from its read transaction"
                )
            frozen = freeze()
        return _DetachedResult(frozen())

    def begin(self) -> Any:
        """Reject attempts to stretch a transaction across provider execution."""
        self._write_attempted.set()
        raise UnstagedProviderPersistenceError(
            "provider execution cannot own a database transaction"
        )

    def begin_nested(self) -> Any:
        """Reject savepoints, which would likewise hide out-of-boundary writes."""
        self._write_attempted.set()
        raise UnstagedProviderPersistenceError(
            "provider execution cannot own a database transaction"
        )

    def commit(self) -> None:
        """Reject direct commits from provider code."""
        self._write_attempted.set()
        raise UnstagedProviderPersistenceError(
            "provider persistence requires a reviewed completion-boundary stager"
        )

    def rollback(self) -> None:
        """Reject transaction ownership; each read transaction is already closed."""
        self._write_attempted.set()
        raise UnstagedProviderPersistenceError(
            "provider execution cannot own a database transaction"
        )

    def close(self) -> None:
        """No-op because the facade never owns a retained connection."""

    @property
    def closed(self) -> bool:
        """The reusable facade remains available while its runtime exists."""
        return False

    def assert_no_write_attempt(self) -> None:
        """Fail even when legacy producer code swallowed the original rejection."""
        if self._write_attempted.is_set():
            raise UnstagedProviderPersistenceError(
                "provider attempted persistence outside its completion boundary"
            )
