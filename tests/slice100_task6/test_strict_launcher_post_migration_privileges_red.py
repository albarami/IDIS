"""Task 6 RED contracts for strict post-migration application privileges.

The hermetic contracts pin launcher ordering without opening a socket.  The
PostgreSQL contract is opt-in through the repository's existing disposable-
database convention and deliberately starts at 0035 with an ungranted app role.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, text

from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile
from tests.slice100_task2b._support import disposable_postgres, migrate

_CRUD = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
_TASK5_TASK6_TABLES = (
    "methodology_coverage_records",
    "methodology_extraction_attempts",
    "methodology_extraction_results",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_questions",
    "methodology_result_claim_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_factual_outputs",
    "methodology_result_sanad_links",
    "methodology_versions",
    "run_methodology_selections",
)


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


def test_forward_migration_orders_upgrade_privilege_verification_then_success_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-0035 migration must not retain the older table-grant snapshot."""
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    events: list[str] = []
    module = SimpleNamespace(upgrade=lambda _config, revision: events.append(f"upgrade:{revision}"))
    monkeypatch.setattr(
        launcher,
        "_ensure_migration_database",
        lambda _profile: events.append("provision"),
    )
    monkeypatch.setattr(
        launcher,
        "_grant_and_verify_migration_privileges",
        lambda _profile: events.append("privileges"),
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _profile: lambda: events.append("audit"),
    )
    _fake_alembic_config_import(launcher, monkeypatch)

    result = launcher._run_migration(
        module,
        SimpleNamespace(alembic_config="alembic.ini", upgrade="0037"),
        profile,
    )

    assert result == 0
    assert events == ["upgrade:0037", "privileges", "audit"]


def test_forward_migration_privilege_failure_blocks_success_audit(
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

    def fail_privilege_admission(_profile: object) -> None:
        events.append("privileges")
        raise launcher.StrictLauncherError("DATABASE_SCHEMA_NOT_READY")

    monkeypatch.setattr(
        launcher, "_grant_and_verify_migration_privileges", fail_privilege_admission
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _profile: lambda: events.append("audit"),
    )
    _fake_alembic_config_import(launcher, monkeypatch)

    with pytest.raises(launcher.StrictLauncherError, match="DATABASE_SCHEMA_NOT_READY"):
        launcher._run_migration(
            module,
            SimpleNamespace(alembic_config="alembic.ini", upgrade="0037"),
            profile,
        )

    assert events == ["upgrade", "privileges"]


def _skip_or_fail_without_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "Task 6 strict post-migration privilege proof requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES", "0") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def _direct_table_privileges(
    connection: Connection,
    *,
    role_name: str,
) -> dict[str, set[str]]:
    tables = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT c.relname FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                "ORDER BY c.relname"
            )
        ).scalars()
    )
    privileges = {table: set() for table in tables}
    rows = connection.execute(
        text(
            "SELECT c.relname, a.privilege_type FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(c.relacl) AS a "
            "JOIN pg_roles AS grantee ON grantee.oid = a.grantee "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
            "AND grantee.rolname = :role_name "
            "ORDER BY c.relname, a.privilege_type"
        ),
        {"role_name": role_name},
    ).fetchall()
    for table_name, privilege in rows:
        privileges.setdefault(str(table_name), set()).add(str(privilege))
    return privileges


def _assert_exact_least_privilege_catalog(
    connection: Connection,
    *,
    role_name: str,
    database_name: str,
) -> None:
    assert (
        connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0037"
    )

    table_privileges = _direct_table_privileges(connection, role_name=role_name)
    assert set(_TASK5_TASK6_TABLES).issubset(table_privileges)
    assert all(table_privileges[table] == _CRUD for table in _TASK5_TASK6_TABLES)
    assert all(privileges == _CRUD for privileges in table_privileges.values())

    schema_privileges = {
        str(value)
        for value in connection.execute(
            text(
                "SELECT a.privilege_type FROM pg_namespace AS n "
                "CROSS JOIN LATERAL aclexplode(n.nspacl) AS a "
                "JOIN pg_roles AS grantee ON grantee.oid = a.grantee "
                "WHERE n.nspname = 'public' AND grantee.rolname = :role_name"
            ),
            {"role_name": role_name},
        ).scalars()
    }
    assert schema_privileges == {"USAGE"}

    role = connection.execute(
        text(
            "SELECT r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid) "
            "FROM pg_roles r WHERE r.rolname = :role_name"
        ),
        {"role_name": role_name},
    ).one()
    assert tuple(bool(value) for value in role) == (False, False, False, False, False)

    capabilities = connection.execute(
        text(
            "SELECT has_schema_privilege(CAST(:role_name AS name), 'public', 'USAGE'), "
            "has_schema_privilege(CAST(:role_name AS name), 'public', 'CREATE'), "
            "has_database_privilege(CAST(:role_name AS name), :database_name, 'CREATE')"
        ),
        {"role_name": role_name, "database_name": database_name},
    ).one()
    assert tuple(bool(value) for value in capabilities) == (True, False, False)

    owned_objects = connection.execute(
        text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_roles owner ON owner.oid = c.relowner "
            "WHERE n.nspname = 'public' AND owner.rolname = :role_name "
            "UNION ALL SELECT 1 FROM pg_namespace n "
            "JOIN pg_roles owner ON owner.oid = n.nspowner "
            "WHERE n.nspname = 'public' AND owner.rolname = :role_name "
            "UNION ALL SELECT 1 FROM pg_database d "
            "JOIN pg_roles owner ON owner.oid = d.datdba "
            "WHERE d.datname = :database_name AND owner.rolname = :role_name"
        ),
        {"role_name": role_name, "database_name": database_name},
    ).fetchall()
    assert owned_objects == []

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

    forbidden_table_privileges = connection.execute(
        text(
            "SELECT c.relname, forbidden.privilege FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN unnest(ARRAY['TRUNCATE','REFERENCES','TRIGGER']) "
            "AS forbidden(privilege) "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
            "AND has_table_privilege(CAST(:role_name AS name), c.oid, forbidden.privilege)"
        ),
        {"role_name": role_name},
    ).fetchall()
    assert forbidden_table_privileges == []

    sequence_privileges = connection.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'S' "
            "AND (has_sequence_privilege(CAST(:role_name AS name), c.oid, 'USAGE') "
            "OR has_sequence_privilege(CAST(:role_name AS name), c.oid, 'SELECT') "
            "OR has_sequence_privilege(CAST(:role_name AS name), c.oid, 'UPDATE'))"
        ),
        {"role_name": role_name},
    ).fetchall()
    assert sequence_privileges == []


def test_disposable_postgres_strict_launcher_refreshes_0035_grants_through_0037(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real launcher grants new Task 5/6 tables without a test-only grant helper."""
    _skip_or_fail_without_postgres()

    from idis.persistence.db import reset_engines

    launcher = importlib.import_module("idis.runtime.strict_launcher")
    alembic_ini = Path("src/idis/persistence/migrations/alembic.ini").resolve()
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0035")
        with database.admin_engine.connect() as connection:
            before = _direct_table_privileges(connection, role_name=database.role_name)
            assert before
            assert all(not privileges for privileges in before.values())

        admin_url = database.admin_engine.url.render_as_string(hide_password=False)
        app_url = database.app_engine.url.render_as_string(hide_password=False)
        profile = _reviewed_profile(
            tmp_path,
            "migrate",
            additions={
                "IDIS_DATABASE_ADMIN_URL": admin_url,
                "IDIS_DATABASE_URL": app_url,
            },
            generation="bootstrap-v1",
            promoted="0",
        )
        monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", admin_url)
        monkeypatch.setenv("IDIS_DATABASE_URL", app_url)
        audit_events: list[str] = []

        def catalog_guarded_audit(_profile: object) -> Callable[[], None]:
            def audit() -> None:
                with database.admin_engine.connect() as connection:
                    _assert_exact_least_privilege_catalog(
                        connection,
                        role_name=database.role_name,
                        database_name=database.database_name,
                    )
                audit_events.append("audit")

            return audit

        monkeypatch.setattr(launcher, "_launch_audit_runner", catalog_guarded_audit)
        reset_engines()
        try:
            result = launcher._run_migration(
                command,
                SimpleNamespace(alembic_config=str(alembic_ini), upgrade="0037"),
                profile,
            )
            assert result == 0
            assert audit_events == ["audit"]

            with database.app_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                    {"tenant_id": profile.values["IDIS_ACCEPTANCE_TENANT_ID"]},
                )
                for table_name in _TASK5_TASK6_TABLES:
                    connection.exec_driver_sql(f'SELECT count(*) FROM "{table_name}"')

            with database.admin_engine.connect() as connection:
                _assert_exact_least_privilege_catalog(
                    connection,
                    role_name=database.role_name,
                    database_name=database.database_name,
                )
        finally:
            reset_engines()
            # The disposable context owns the database/role teardown; only global
            # application engine caches are reset here.
