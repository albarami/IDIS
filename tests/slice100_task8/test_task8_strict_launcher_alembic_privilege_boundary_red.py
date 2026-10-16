"""Task-8 RED for the post-migration Alembic privilege boundary."""

from __future__ import annotations

import importlib
import inspect
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from tests.slice100_task2b._support import disposable_postgres, migrate


class _ScalarResult:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = values

    def scalars(self) -> _ScalarResult:
        return self

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._values)


class _PrivilegeResult:
    def __init__(self, absent: bool) -> None:
        self._absent = absent

    def one(self) -> tuple[bool]:
        return (self._absent,)


class _PrivilegeConnection:
    def __init__(self, *, absent: bool) -> None:
        self.absent = absent
        self.sql = ""

    def execute(self, statement: object, _parameters: object) -> _PrivilegeResult:
        self.sql = str(statement)
        return _PrivilegeResult(self.absent)


class _Connection:
    def __init__(self) -> None:
        self.driver_sql: list[str] = []

    def execute(self, statement: object) -> _ScalarResult:
        sql = str(statement)
        if "c.relname <> 'alembic_version'" in sql:
            return _ScalarResult(("runs",))
        return _ScalarResult(("alembic_version", "runs"))

    def exec_driver_sql(self, sql: str) -> None:
        self.driver_sql.append(sql)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection
        self.dialect = SimpleNamespace(
            identifier_preparer=SimpleNamespace(quote=lambda value: f'"{value}"')
        )
        self.disposed = False

    def begin(self) -> Any:
        return nullcontext(self._connection)

    def dispose(self) -> None:
        self.disposed = True


def test_post_migration_grants_exclude_and_revoke_alembic_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    connection = _Connection()
    engine = _Engine(connection)
    observed_tables: list[tuple[str, ...]] = []
    real_import = launcher.importlib.import_module

    fake_sqlalchemy = SimpleNamespace(
        create_engine=lambda _url: engine,
        text=lambda sql: sql,
    )
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: fake_sqlalchemy if name == "sqlalchemy" else real_import(name),
    )
    monkeypatch.setattr(
        launcher,
        "_migration_database_identity",
        lambda _profile: ("postgresql://admin", "idis", "idis_application"),
    )

    def exact_catalog(
        _connection: object,
        *,
        table_names: tuple[str, ...],
        **_kwargs: object,
    ) -> bool:
        observed_tables.append(table_names)
        return table_names == ("runs",)

    monkeypatch.setattr(launcher, "_migration_privilege_catalog_is_exact", exact_catalog)

    launcher._grant_and_verify_migration_privileges(object())

    assert observed_tables == [("runs",)]
    assert connection.driver_sql == [
        'GRANT USAGE ON SCHEMA public TO "idis_application"',
        (
            "REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
            'ON TABLE public.alembic_version FROM "idis_application"'
        ),
        (
            "REVOKE SELECT (version_num), INSERT (version_num), "
            "UPDATE (version_num), REFERENCES (version_num) "
            'ON TABLE public.alembic_version FROM "idis_application"'
        ),
        ('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."runs" TO "idis_application"'),
    ]
    assert engine.disposed is True


def test_alembic_metadata_effective_table_and_column_privileges_are_closed() -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    normalized = ""
    for absent in (True, False):
        connection = _PrivilegeConnection(absent=absent)
        observed = launcher._alembic_metadata_privileges_absent(
            connection,
            app_role="idis_application",
            text=lambda sql: sql,
        )
        assert observed is absent
        normalized = " ".join(connection.sql.split()).upper()
    assert "HAS_TABLE_PRIVILEGE" in normalized
    assert "HAS_ANY_COLUMN_PRIVILEGE" in normalized
    for privilege in (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ):
        assert f"'{privilege}'" in normalized


def test_exact_catalog_invokes_the_alembic_effective_privilege_guard() -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    source = inspect.getsource(launcher._migration_privilege_catalog_is_exact)

    assert "_alembic_metadata_privileges_absent(" in source


def test_disposable_postgres_alembic_acl_matrix_and_cleanup_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0001")
        role = database.role_name
        inherited_role = f"{role}_inherited"

        def metadata_absent() -> bool:
            with database.admin_engine.connect() as connection:
                return bool(
                    launcher._alembic_metadata_privileges_absent(
                        connection,
                        app_role=role,
                        text=text,
                    )
                )

        acl_cases = (
            (
                f'GRANT SELECT ON TABLE public.alembic_version TO "{role}"',
                f'REVOKE SELECT ON TABLE public.alembic_version FROM "{role}"',
            ),
            (
                f'GRANT UPDATE (version_num) ON public.alembic_version TO "{role}"',
                f'REVOKE UPDATE (version_num) ON public.alembic_version FROM "{role}"',
            ),
            (
                "GRANT SELECT ON TABLE public.alembic_version TO PUBLIC",
                "REVOKE SELECT ON TABLE public.alembic_version FROM PUBLIC",
            ),
            (
                "GRANT UPDATE (version_num) ON public.alembic_version TO PUBLIC",
                "REVOKE UPDATE (version_num) ON public.alembic_version FROM PUBLIC",
            ),
        )
        assert metadata_absent() is True
        for grant_sql, revoke_sql in acl_cases:
            with database.admin_engine.begin() as connection:
                connection.exec_driver_sql(grant_sql)
            assert metadata_absent() is False
            with database.admin_engine.begin() as connection:
                connection.exec_driver_sql(revoke_sql)
            assert metadata_absent() is True

        try:
            with database.admin_engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE ROLE "{inherited_role}" NOLOGIN')
                connection.exec_driver_sql(
                    f'GRANT SELECT ON TABLE public.alembic_version TO "{inherited_role}"'
                )
                connection.exec_driver_sql(f'ALTER ROLE "{role}" INHERIT')
                connection.exec_driver_sql(f'GRANT "{inherited_role}" TO "{role}"')
            assert metadata_absent() is False
        finally:
            with database.admin_engine.begin() as connection:
                connection.exec_driver_sql(f'REVOKE "{inherited_role}" FROM "{role}"')
                connection.exec_driver_sql(f'ALTER ROLE "{role}" NOINHERIT')
                connection.exec_driver_sql(
                    f'REVOKE SELECT ON TABLE public.alembic_version FROM "{inherited_role}"'
                )
                connection.exec_driver_sql(f'DROP ROLE "{inherited_role}"')
        assert metadata_absent() is True

        with database.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'GRANT SELECT ON TABLE public.alembic_version TO "{role}"')
            connection.exec_driver_sql(
                f'GRANT UPDATE (version_num) ON public.alembic_version TO "{role}"'
            )

        admin_url = database.admin_engine.url.render_as_string(hide_password=False)
        monkeypatch.setattr(
            launcher,
            "_migration_database_identity",
            lambda _profile: (admin_url, database.database_name, role),
        )
        launcher._grant_and_verify_migration_privileges(object())
        assert metadata_absent() is True
        launcher._grant_and_verify_migration_privileges(object())
        assert metadata_absent() is True
