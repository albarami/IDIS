"""Task 4 RED contract for fresh strict-migration database admission.

The hermetic tests never open a socket.  The disposable-Postgres proof is opt-in
through the project's existing ``IDIS_DATABASE_ADMIN_URL`` convention and creates
only a uniquely named database and login role.  It never targets the configured
application database.
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

_CRUD = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def _fake_alembic_config_import(
    launcher: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = launcher.importlib.import_module
    fake_config = SimpleNamespace(Config=lambda path: ("config", path))
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: fake_config if name == "alembic.config" else real_import(name),
    )


def test_strict_migration_orders_provision_upgrade_privileges_then_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both first use and replay must pass the complete admission sequence."""
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    events: list[tuple[str, str]] = []
    module = SimpleNamespace(upgrade=lambda _config, revision: events.append(("upgrade", revision)))

    # Named seams keep this test hermetic while checking the security-relevant ordering.
    # The disposable proof below checks their database effects rather than their shape.
    monkeypatch.setattr(
        launcher,
        "_ensure_migration_database",
        lambda reviewed: events.append(("provision", reviewed.command)),
        raising=False,
    )
    monkeypatch.setattr(
        launcher,
        "_grant_and_verify_migration_privileges",
        lambda reviewed: events.append(("privileges", reviewed.command)),
        raising=False,
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda reviewed: lambda: events.append(("audit", reviewed.command)),
    )
    _fake_alembic_config_import(launcher, monkeypatch)
    args = SimpleNamespace(alembic_config="alembic.ini", upgrade="0035")

    assert launcher._run_migration(module, args, profile) == 0
    assert launcher._run_migration(module, args, profile) == 0

    one_run = [
        ("provision", "migrate"),
        ("upgrade", "0035"),
        ("privileges", "migrate"),
        ("audit", "migrate"),
    ]
    assert events == one_run + one_run


def test_privilege_admission_failure_blocks_success_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    events: list[str] = []
    module = SimpleNamespace(upgrade=lambda _config, _revision: events.append("upgrade"))
    monkeypatch.setattr(
        launcher,
        "_ensure_migration_database",
        lambda _reviewed: events.append("provision"),
        raising=False,
    )

    def reject_incomplete_catalog(_reviewed: object) -> None:
        events.append("privileges")
        raise launcher.StrictLauncherError("DATABASE_SCHEMA_NOT_READY")

    monkeypatch.setattr(
        launcher,
        "_grant_and_verify_migration_privileges",
        reject_incomplete_catalog,
        raising=False,
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _reviewed: lambda: events.append("audit"),
    )
    _fake_alembic_config_import(launcher, monkeypatch)

    with pytest.raises(launcher.StrictLauncherError, match="DATABASE_SCHEMA_NOT_READY"):
        launcher._run_migration(
            module,
            SimpleNamespace(alembic_config="alembic.ini", upgrade="0035"),
            profile,
        )

    assert events == ["provision", "upgrade", "privileges"]


def test_fresh_database_sql_is_template0_and_never_grants_by_default_or_public() -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")

    provision = inspect.getsource(launcher._ensure_migration_database)
    privileges = inspect.getsource(launcher._grant_and_verify_migration_privileges)
    normalized_provision = " ".join(provision.upper().split())
    normalized_privileges = " ".join(privileges.upper().split())

    # PostgreSQL does not retain the source-template identity in the target catalog;
    # pinning this one SQL clause is therefore the only direct proof of TEMPLATE template0.
    assert re.search(r"CREATE DATABASE.+TEMPLATE TEMPLATE0", normalized_provision)
    assert "TEMPLATE TEMPLATE1" not in normalized_provision
    assert "ALTER DEFAULT PRIVILEGES" not in normalized_privileges
    assert " TO PUBLIC" not in normalized_privileges
    assert "ON ALL SEQUENCES" not in normalized_privileges
    assert "ALL PRIVILEGES" not in normalized_privileges


def _skip_or_fail_without_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "Task 4 fresh-database admission requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES", "0") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def _safe_identifier(prefix: str) -> str:
    value = f"{prefix}_{uuid.uuid4().hex[:16]}"
    assert re.fullmatch(r"[a-z][a-z0-9_]+", value)
    return value


@contextmanager
def _absent_database_and_app_role() -> Iterator[dict[str, Any]]:
    """Create only the existing app role; the launcher must create the database."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    configured = make_url(os.environ["IDIS_DATABASE_ADMIN_URL"])
    maintenance_name = "postgres" if configured.database != "postgres" else "template1"
    maintenance = create_engine(
        configured.set(database=maintenance_name),
        isolation_level="AUTOCOMMIT",
    )
    database_name = _safe_identifier("idis_t4_fresh")
    role_name = _safe_identifier("idis_t4_app")
    password = secrets.token_hex(24)
    target_admin = configured.set(database=database_name)
    target_app = target_admin.set(username=role_name, password=password)
    try:
        with maintenance.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE ROLE \"{role_name}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            )
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            ).scalar_one_or_none()
            assert exists is None
        yield {
            "maintenance": maintenance,
            "database_name": database_name,
            "role_name": role_name,
            "admin_url": target_admin.render_as_string(hide_password=False),
            "app_url": target_app.render_as_string(hide_password=False),
        }
    finally:
        with maintenance.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
            connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role_name}"')
        maintenance.dispose()


def _catalog_assertions(connection: Any, *, role_name: str, database_name: str) -> None:
    from sqlalchemy import text

    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "0035"

    tables = tuple(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                "ORDER BY c.relname"
            )
        ).scalars()
    )
    assert tables
    privilege_rows = connection.execute(
        text(
            "SELECT table_name, privilege_type "
            "FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND grantee = :role_name "
            "ORDER BY table_name, privilege_type"
        ),
        {"role_name": role_name},
    ).fetchall()
    by_table: dict[str, set[str]] = {str(table): set() for table in tables}
    for table_name, privilege in privilege_rows:
        by_table.setdefault(str(table_name), set()).add(str(privilege))
    assert set(by_table) == set(tables)
    assert all(privileges == _CRUD for privileges in by_table.values())

    public_table_grants = connection.execute(
        text(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND grantee = 'PUBLIC'"
        )
    ).scalar_one()
    assert public_table_grants == 0

    role = connection.execute(
        text(
            "SELECT r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid) AS member "
            "FROM pg_roles r WHERE r.rolname = :role_name"
        ),
        {"role_name": role_name},
    ).one()
    assert tuple(bool(value) for value in role) == (False, False, False, False, False)

    capability = connection.execute(
        text(
            "SELECT has_schema_privilege(:role_name, 'public', 'USAGE'), "
            "has_schema_privilege(:role_name, 'public', 'CREATE'), "
            "has_database_privilege(:role_name, :database_name, 'CREATE')"
        ),
        {"role_name": role_name, "database_name": database_name},
    ).one()
    assert tuple(bool(value) for value in capability) == (True, False, False)

    forbidden_table_privileges = connection.execute(
        text(
            "SELECT c.relname, privilege "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN unnest(ARRAY['TRUNCATE','REFERENCES','TRIGGER']) AS privilege "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
            "AND has_table_privilege(:role_name, c.oid, privilege)"
        ),
        {"role_name": role_name},
    ).fetchall()
    assert forbidden_table_privileges == []

    sequence_privileges = connection.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'S' "
            "AND (has_sequence_privilege(:role_name, c.oid, 'USAGE') "
            "OR has_sequence_privilege(:role_name, c.oid, 'SELECT') "
            "OR has_sequence_privilege(:role_name, c.oid, 'UPDATE'))"
        ),
        {"role_name": role_name},
    ).fetchall()
    assert sequence_privileges == []

    default_acl = connection.execute(
        text(
            "SELECT count(*) FROM pg_default_acl d "
            "CROSS JOIN LATERAL aclexplode(COALESCE(d.defaclacl, "
            "acldefault(d.defaclobjtype, d.defaclrole))) a "
            "LEFT JOIN pg_roles grantee ON grantee.oid = a.grantee "
            "WHERE grantee.rolname = :role_name OR a.grantee = 0"
        ),
        {"role_name": role_name},
    ).scalar_one()
    assert default_acl == 0

    owner = connection.execute(
        text(
            "SELECT owner.rolname FROM pg_database d "
            "JOIN pg_roles owner ON owner.oid = d.datdba "
            "WHERE d.datname = :database_name"
        ),
        {"database_name": database_name},
    ).scalar_one()
    assert owner != role_name


def test_disposable_postgres_fresh_migrate_grant_audit_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real catalog proof; intentionally not run during the RED-authoring checkpoint."""
    _skip_or_fail_without_postgres()

    from alembic import command
    from sqlalchemy import create_engine, text

    from idis.persistence.db import reset_engines

    launcher = importlib.import_module("idis.runtime.strict_launcher")
    alembic_ini = Path("src/idis/persistence/migrations/alembic.ini").resolve()
    with _absent_database_and_app_role() as target:
        additions = {
            "IDIS_DATABASE_ADMIN_URL": target["admin_url"],
            "IDIS_DATABASE_URL": target["app_url"],
        }
        profile = _reviewed_profile(
            tmp_path,
            "migrate",
            additions=additions,
            generation="bootstrap-v1",
            promoted="0",
        )
        monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", target["admin_url"])
        monkeypatch.setenv("IDIS_DATABASE_URL", target["app_url"])
        reset_engines()
        target_admin = create_engine(target["admin_url"])
        real_audit_runner = launcher._launch_audit_runner

        def catalog_guarded_audit(reviewed: object) -> Callable[[], None]:
            durable_audit = real_audit_runner(reviewed)

            def run() -> None:
                with target_admin.connect() as connection:
                    _catalog_assertions(
                        connection,
                        role_name=target["role_name"],
                        database_name=target["database_name"],
                    )
                durable_audit()

            return run

        monkeypatch.setattr(launcher, "_launch_audit_runner", catalog_guarded_audit)
        args = SimpleNamespace(alembic_config=str(alembic_ini), upgrade="0035")
        try:
            assert launcher._run_migration(command, args, profile) == 0
            reset_engines()
            assert launcher._run_migration(command, args, profile) == 0
            reset_engines()

            with target_admin.connect() as connection:
                _catalog_assertions(
                    connection,
                    role_name=target["role_name"],
                    database_name=target["database_name"],
                )
                audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "AND event_type = 'integration.strict_launch.admitted' "
                        "AND event->'payload'->'safe'->>'command' = 'migrate'"
                    ),
                    {"tenant_id": profile.values["IDIS_ACCEPTANCE_TENANT_ID"]},
                ).scalar_one()
                assert audit_count == 2
        finally:
            reset_engines()
            target_admin.dispose()
