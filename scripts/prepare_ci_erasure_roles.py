"""Prepare migration 0038's pre-existing roles on explicitly owned disposable CI only."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import URL, Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.pool import NullPool

ADMIN_URL_ENV = "IDIS_CI_ERASURE_ADMIN_URL"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_MAX_PORT = 65535
_CONNECT_TIMEOUT_SECONDS = 10
_POSTGRES_DRIVERS = frozenset({"postgresql", "postgresql+psycopg2", "postgresql+psycopg"})


class RolePreparationError(ValueError):
    """Disposable preparation was rejected without modifying incompatible roles."""


@dataclass(frozen=True)
class RolePosture:
    """Flags enforced by 0038; replication is pinned only for the guard owner."""

    name: str
    can_login: bool = False
    superuser: bool = False
    create_database: bool = False
    create_role: bool = False
    inherit: bool = False
    bypass_rls: bool = False
    replication: bool | None = None


@dataclass(frozen=True)
class PreparationResult:
    """Role names created or preserved by one transactional preparation."""

    created: tuple[str, ...]
    existing: tuple[str, ...]


_EXPECTED = (
    RolePosture(name="idis_erasure_executor", can_login=True),
    RolePosture(name="idis_erasure_function_owner"),
    RolePosture(name=_GUARD_OWNER, replication=False),
)
_ROLE_NAMES = tuple(role.name for role in _EXPECTED)
_ROLE_PARAMETERS = {
    "executor": _ROLE_NAMES[0],
    "owner": _ROLE_NAMES[1],
    "guard_owner": _GUARD_OWNER,
}
_ROLE_QUERY = text(
    "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolbypassrls,"
    "rolreplication FROM pg_catalog.pg_roles "
    "WHERE rolname IN (:executor,:owner,:guard_owner) ORDER BY rolname"
)
_MEMBERSHIP_QUERY = text(
    "SELECT count(*) FROM pg_catalog.pg_auth_members m "
    "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
    "JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
    "WHERE member.rolname IN (:executor,:owner,:guard_owner) "
    "OR granted_role.rolname IN (:executor,:owner,:guard_owner)"
)


def validate_ci_admin_url(value: str | None) -> URL:
    """Require dedicated credentials and an explicit loopback port, without fallbacks."""
    if not value:
        raise RolePreparationError("DEDICATED_DISPOSABLE_ENDPOINT_REQUIRED")
    try:
        url = make_url(value)
        valid = (
            url.drivername in _POSTGRES_DRIVERS
            and url.host in {"127.0.0.1", "::1"}
            and url.port is not None
            and 0 < url.port <= _MAX_PORT
            and bool(url.database)
            and bool(url.username)
            and bool(url.password)
            and not url.query
        )
    except (ArgumentError, ValueError):
        raise RolePreparationError("DISPOSABLE_ENDPOINT_INVALID") from None
    if not valid:
        raise RolePreparationError("DISPOSABLE_ENDPOINT_INVALID")
    return url.set(drivername="postgresql+psycopg2")


def _read_roles(connection: Connection) -> dict[str, RolePosture]:
    rows = connection.execute(_ROLE_QUERY, _ROLE_PARAMETERS)
    return {
        str(row[0]): RolePosture(
            name=str(row[0]),
            can_login=bool(row[1]),
            superuser=bool(row[2]),
            create_database=bool(row[3]),
            create_role=bool(row[4]),
            inherit=bool(row[5]),
            bypass_rls=bool(row[6]),
            replication=bool(row[7]) if row[0] == _GUARD_OWNER else None,
        )
        for row in rows
    }


def _validate_existing(connection: Connection, existing: dict[str, RolePosture]) -> None:
    for expected in _EXPECTED:
        if expected.name in existing and existing[expected.name] != expected:
            raise RolePreparationError(f"POSTURE_INVALID: {expected.name}")
    memberships = connection.execute(_MEMBERSHIP_QUERY, _ROLE_PARAMETERS).scalar_one()
    if memberships != 0:
        raise RolePreparationError("MEMBERSHIP_FORBIDDEN")


def prepare_ci_erasure_roles(connection: Connection) -> PreparationResult:
    """Validate before creating missing roles; caller must commit or roll back the transaction."""
    if not connection.in_transaction():
        raise RolePreparationError("TRANSACTION_REQUIRED")
    existing = _read_roles(connection)
    _validate_existing(connection, existing)
    created: list[str] = []
    for expected in _EXPECTED:
        if expected.name in existing:
            continue
        login = "LOGIN" if expected.can_login else "NOLOGIN"
        connection.execute(
            text(
                f"CREATE ROLE {expected.name} {login} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOBYPASSRLS NOREPLICATION"
            )
        )
        created.append(expected.name)
    final = _read_roles(connection)
    _validate_existing(connection, final)
    if set(final) != set(_ROLE_NAMES):
        raise RolePreparationError("PREPARATION_INCOMPLETE")
    return PreparationResult(created=tuple(created), existing=tuple(existing))


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare only the explicitly acknowledged disposable endpoint, with sanitized errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-ci", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        url = validate_ci_admin_url(os.environ.get(ADMIN_URL_ENV))
        engine = create_engine(
            url, poolclass=NullPool, connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS}
        )
        try:
            with engine.begin() as connection:
                result = prepare_ci_erasure_roles(connection)
        finally:
            engine.dispose()
    except RolePreparationError as exc:
        print(f"CI_ERASURE_ROLES_REJECTED: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("CI_ERASURE_ROLES_REJECTED: DATABASE_OPERATION_FAILED", file=sys.stderr)
        return 1
    print(f"CI_ERASURE_ROLES_READY: created={len(result.created)} existing={len(result.existing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
