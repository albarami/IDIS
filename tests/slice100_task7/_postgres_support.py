"""Shared operator-principal bootstrap for Task-7 disposable PostgreSQL proofs."""

from __future__ import annotations

from sqlalchemy import Connection, text


def provision_exact_erasure_principals(connection: Connection) -> None:
    """Provision and re-prove the reviewed cluster roles before revision 0038."""

    connection.exec_driver_sql(
        """
        DO $task7$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_erasure_function_owner'
          ) THEN
            CREATE ROLE idis_erasure_function_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_erasure_executor'
          ) THEN
            CREATE ROLE idis_erasure_executor
              LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $task7$;
        """
    )
    posture = {
        str(row.rolname): tuple(row)[1:]
        for row in connection.execute(
            text(
                "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                "rolinherit,rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname IN "
                "('idis_erasure_function_owner','idis_erasure_executor') "
                "ORDER BY rolname"
            )
        )
    }
    assert posture == {
        "idis_erasure_executor": (True, False, False, False, False, False),
        "idis_erasure_function_owner": (False, False, False, False, False, False),
    }, "TASK7_ERASURE_ROLE_POSTURE_INVALID"
    memberships = connection.execute(
        text(
            "SELECT count(*) FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
            "WHERE member.rolname IN "
            "('idis_erasure_function_owner','idis_erasure_executor')"
        )
    ).scalar_one()
    assert int(memberships) == 0, "TASK7_ERASURE_ROLE_MEMBERSHIP_FORBIDDEN"


__all__ = ["provision_exact_erasure_principals"]
