"""Disposable-CI role preparation boundaries and real PostgreSQL regressions."""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
import yaml
from scripts import prepare_ci_erasure_roles as preparation
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

EXECUTOR = "idis_erasure_executor"
OWNER = "idis_erasure_function_owner"
GUARD_OWNER = "idis_task7_guard_function_owner"
PEER = "cir04b_membership_peer"
EXPECTED = {
    EXECUTOR: (True, False, False, False, False, False),
    OWNER: (False, False, False, False, False, False),
    GUARD_OWNER: (False, False, False, False, False, False, False),
}
ROLE_QUERY = text(
    "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolbypassrls,"
    "rolreplication "
    "FROM pg_catalog.pg_roles WHERE rolname IN "
    "('idis_erasure_executor','idis_erasure_function_owner','idis_task7_guard_function_owner')"
)
ROOT = Path(__file__).resolve().parents[1]


def _postures(connection: Connection) -> dict[str, tuple[bool, ...]]:
    return {
        str(row[0]): tuple(row[1:]) if row[0] == GUARD_OWNER else tuple(row[1:7])
        for row in connection.execute(ROLE_QUERY)
    }


def _create_role(connection: Connection, role: str) -> None:
    login = "LOGIN" if role == EXECUTOR else "NOLOGIN"
    connection.execute(
        text(
            f"CREATE ROLE {role} {login} NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOBYPASSRLS NOREPLICATION"
        )
    )


def _frozen_guard_preflight() -> tuple[str, ...]:
    path = (
        ROOT / "src/idis/persistence/migrations/versions/0038_full_strict_v16_receipts_and_edges.py"
    )
    module = ast.parse(path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1"
    )
    assert isinstance(assignment.value, ast.Tuple)
    statements: list[str] = []
    for node in assignment.value.elts[:2]:
        value: object = ast.literal_eval(node)
        assert isinstance(value, str)
        statements.append(value)
    return tuple(statements)


@pytest.fixture
def role_connection() -> Iterator[Connection]:
    """Use only the explicitly opted-in disposable endpoint, rolling back each case."""
    value = os.environ.get(preparation.ADMIN_URL_ENV)
    if not value:
        if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
            pytest.fail("Required disposable-CI role endpoint is missing")
        pytest.skip("Explicit disposable-CI role endpoint is not configured")
    engine = create_engine(preparation.validate_ci_admin_url(value), poolclass=NullPool)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            assert _postures(connection) == {}, "Use a fresh owned cluster before migrations"
            yield connection
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "private-marker",
        "sqlite:///private-marker",
        "postgresql://postgres:private-marker@db.example:5432/postgres",
        "postgresql://postgres:private-marker@127.0.0.1/postgres",
        "postgresql://postgres:private-marker@127.0.0.1:invalid/postgres",
        "postgresql://postgres:private-marker@127.0.0.1:65536/postgres",
        "postgresql://postgres:private-marker@127.0.0.1:5432/",
        "postgresql://postgres@127.0.0.1:5432/postgres",
        "postgresql://postgres:private-marker@127.0.0.1:5432/postgres?host=db.example",
    ],
)
def test_rejects_implicit_or_nonlocal_targets_without_echoing_input(value: str | None) -> None:
    """Reject ambiguous endpoints without including connection material in the error."""
    with pytest.raises(preparation.RolePreparationError) as error:
        preparation.validate_ci_admin_url(value)
    assert "private-marker" not in str(error.value)
    assert "postgresql" not in str(error.value)


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg2", "postgresql+psycopg"])
def test_accepts_explicit_loopback_target(driver: str) -> None:
    """Normalize supported PostgreSQL URL forms without importing application code."""
    url = preparation.validate_ci_admin_url(f"{driver}://postgres:ci-only@127.0.0.1:15432/postgres")
    assert url.drivername == "postgresql+psycopg2"
    assert url.host == "127.0.0.1"
    assert url.port == 15432


def test_cli_requires_disposable_acknowledgement() -> None:
    """Never connect without the explicit disposable-service acknowledgement."""
    with pytest.raises(SystemExit) as error:
        preparation.main([])
    assert error.value.code == 2


def test_cli_has_no_existing_store_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed even when legacy store URLs are present."""
    monkeypatch.delenv(preparation.ADMIN_URL_ENV, raising=False)
    monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", "private-marker")
    monkeypatch.setenv("IDIS_DATABASE_URL", "private-marker")
    assert preparation.main(["--disposable-ci"]) == 1
    output = capsys.readouterr()
    assert "CI_ERASURE_ROLES_REJECTED" in output.err
    assert "private-marker" not in output.err + output.out
    assert "Traceback" not in output.err


def test_cli_sanitizes_database_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a driver failure's connection material out of CLI diagnostics."""

    def reject_connection(*args: object, **kwargs: object) -> NoReturn:
        raise SQLAlchemyError("postgresql://private-marker:private-marker@127.0.0.1:5432/db")

    monkeypatch.setenv(
        preparation.ADMIN_URL_ENV, "postgresql://postgres:ci-only@127.0.0.1:15432/postgres"
    )
    monkeypatch.setattr(preparation, "create_engine", reject_connection)
    assert preparation.main(["--disposable-ci"]) == 1
    output = capsys.readouterr()
    assert "DATABASE_OPERATION_FAILED" in output.err
    assert "private-marker" not in output.err + output.out
    assert "Traceback" not in output.err


def test_workflow_prepares_roles_before_both_migration_entry_paths() -> None:
    """Retain both required jobs and explicitly prepare each owned service first."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    for job_name, consumer in (
        ("postgres-integration", "scripts/pg_bootstrap_ci.py --export-github-env"),
        ("backup-restore-drill", "pytest tests/test_slice99_backup_restore_postgres.py"),
    ):
        steps = jobs[job_name]["steps"]
        producer_index = next(
            index
            for index, step in enumerate(steps)
            if "scripts/prepare_ci_erasure_roles.py --disposable-ci" in step.get("run", "")
        )
        consumer_index = next(
            index for index, step in enumerate(steps) if consumer in step.get("run", "")
        )
        assert producer_index < consumer_index
        assert any(
            "scripts/check_migration_ceiling.py" in step.get("run", "")
            for step in steps[:producer_index]
        )
        assert preparation.ADMIN_URL_ENV in steps[producer_index]["env"]
        assert steps[producer_index]["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert "-p pytest_socket" in steps[producer_index]["run"]
        assert jobs[job_name]["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert set(jobs["release-gate"]["needs"]) == set(jobs) - {"release-gate"}


def test_creates_exact_roles_without_memberships(role_connection: Connection) -> None:
    """Prepare exactly the frozen role tuples without granting any role."""
    result = preparation.prepare_ci_erasure_roles(role_connection)
    assert set(result.created) == set(EXPECTED)
    assert result.existing == ()
    assert _postures(role_connection) == EXPECTED
    memberships = role_connection.execute(
        text(
            "SELECT count(*) FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles r ON r.oid=m.roleid OR r.oid=m.member "
            "WHERE r.rolname IN "
            "('idis_erasure_executor','idis_erasure_function_owner','idis_task7_guard_function_owner')"
        )
    ).scalar_one()
    assert memberships == 0


@pytest.mark.parametrize(
    "present",
    [
        (),
        (EXECUTOR,),
        (OWNER,),
        (GUARD_OWNER,),
        (EXECUTOR, OWNER),
        (EXECUTOR, GUARD_OWNER),
        (OWNER, GUARD_OWNER),
        (EXECUTOR, OWNER, GUARD_OWNER),
    ],
)
def test_idempotent_and_partial_valid_state(
    role_connection: Connection, present: tuple[str, ...]
) -> None:
    """Keep valid pre-existing roles unchanged and create only absent roles."""
    for role in present:
        _create_role(role_connection, role)
    first = preparation.prepare_ci_erasure_roles(role_connection)
    assert set(first.existing) == set(present)
    assert set(first.created) == set(EXPECTED) - set(present)
    before = role_connection.execute(
        text(
            "SELECT oid,rolname,rolconfig FROM pg_catalog.pg_roles "
            "WHERE rolname IN "
            "('idis_erasure_executor','idis_erasure_function_owner',"
            "'idis_task7_guard_function_owner') "
            "ORDER BY rolname"
        )
    ).fetchall()
    second = preparation.prepare_ci_erasure_roles(role_connection)
    after = role_connection.execute(
        text(
            "SELECT oid,rolname,rolconfig FROM pg_catalog.pg_roles "
            "WHERE rolname IN "
            "('idis_erasure_executor','idis_erasure_function_owner',"
            "'idis_task7_guard_function_owner') "
            "ORDER BY rolname"
        )
    ).fetchall()
    assert second.created == ()
    assert set(second.existing) == set(EXPECTED)
    assert before == after


@pytest.mark.parametrize("role", [EXECUTOR, OWNER, GUARD_OWNER])
@pytest.mark.parametrize(
    "attribute", ["LOGIN_TOGGLE", "SUPERUSER", "CREATEDB", "CREATEROLE", "INHERIT", "BYPASSRLS"]
)
def test_rejects_invalid_posture_without_partial_repair(
    role_connection: Connection, role: str, attribute: str
) -> None:
    """Reject every incompatible flag before creating any missing role."""
    _create_role(role_connection, role)
    if attribute == "LOGIN_TOGGLE":
        attribute = "NOLOGIN" if role == EXECUTOR else "LOGIN"
    role_connection.execute(text(f"ALTER ROLE {role} {attribute}"))
    before = _postures(role_connection)
    with pytest.raises(preparation.RolePreparationError, match="POSTURE_INVALID"):
        preparation.prepare_ci_erasure_roles(role_connection)
    assert _postures(role_connection) == before


@pytest.mark.parametrize("role", [EXECUTOR, OWNER, GUARD_OWNER])
@pytest.mark.parametrize("direction", ["member", "granted_role"])
def test_rejects_memberships_in_both_directions_without_repair(
    role_connection: Connection, role: str, direction: str
) -> None:
    """Preserve incompatible grants and leave all missing roles absent."""
    _create_role(role_connection, role)
    role_connection.execute(text(f"CREATE ROLE {PEER} NOLOGIN"))
    granted, member = (PEER, role) if direction == "member" else (role, PEER)
    role_connection.execute(text(f"GRANT {granted} TO {member}"))
    before = _postures(role_connection)
    grants = role_connection.execute(text("SELECT * FROM pg_catalog.pg_auth_members")).fetchall()
    with pytest.raises(preparation.RolePreparationError, match="MEMBERSHIP_FORBIDDEN"):
        preparation.prepare_ci_erasure_roles(role_connection)
    assert _postures(role_connection) == before
    assert (
        role_connection.execute(text("SELECT * FROM pg_catalog.pg_auth_members")).fetchall()
        == grants
    )


def test_idempotence_preserves_object_grants(role_connection: Connection) -> None:
    """Leave existing object ACLs intact rather than attempting a privilege repair."""
    preparation.prepare_ci_erasure_roles(role_connection)
    role_connection.execute(text("CREATE TEMP TABLE cir04b_acl_probe (value integer)"))
    role_connection.execute(text(f"GRANT SELECT ON cir04b_acl_probe TO {EXECUTOR}"))
    before = role_connection.execute(
        text("SELECT relacl FROM pg_class WHERE oid='cir04b_acl_probe'::regclass")
    ).scalar_one()
    preparation.prepare_ci_erasure_roles(role_connection)
    after = role_connection.execute(
        text("SELECT relacl FROM pg_class WHERE oid='cir04b_acl_probe'::regclass")
    ).scalar_one()
    assert before == after


def test_rejects_membership_between_both_prepared_roles(role_connection: Connection) -> None:
    """Reject cross-role membership even when both roles have valid posture flags."""
    preparation.prepare_ci_erasure_roles(role_connection)
    role_connection.execute(text(f"GRANT {OWNER} TO {EXECUTOR}"))
    with pytest.raises(preparation.RolePreparationError, match="MEMBERSHIP_FORBIDDEN"):
        preparation.prepare_ci_erasure_roles(role_connection)
    assert _postures(role_connection) == EXPECTED


def test_creates_pristine_guard_owner_for_frozen_preflight(role_connection: Connection) -> None:
    """Pass the unchanged migration's exact cleanliness and seven-flag predicates."""
    preparation.prepare_ci_erasure_roles(role_connection)
    for statement in _frozen_guard_preflight():
        role_connection.execute(text(statement))
    assert _postures(role_connection)[GUARD_OWNER] == EXPECTED[GUARD_OWNER]


def test_rejects_guard_owner_replication_without_partial_repair(
    role_connection: Connection,
) -> None:
    """Reject REPLICATION without creating the other missing roles or repairing flags."""
    _create_role(role_connection, GUARD_OWNER)
    role_connection.execute(text(f"ALTER ROLE {GUARD_OWNER} REPLICATION"))
    before = _postures(role_connection)
    with pytest.raises(preparation.RolePreparationError, match="POSTURE_INVALID"):
        preparation.prepare_ci_erasure_roles(role_connection)
    assert _postures(role_connection) == before


def test_migration_remains_authority_for_guard_object_cleanliness(
    role_connection: Connection,
) -> None:
    """Do not repair existing ownership or hide rejection by the frozen predicate."""
    _create_role(role_connection, GUARD_OWNER)
    role_connection.execute(text("CREATE TEMP TABLE cir04b_guard_owned (value integer)"))
    role_connection.execute(text(f"ALTER TABLE cir04b_guard_owned OWNER TO {GUARD_OWNER}"))
    preparation.prepare_ci_erasure_roles(role_connection)
    with (
        pytest.raises(DBAPIError, match="TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN"),
        role_connection.begin_nested(),
    ):
        role_connection.execute(text(_frozen_guard_preflight()[0]))
    owner = role_connection.execute(
        text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid='cir04b_guard_owned'::regclass"
        )
    ).scalar_one()
    assert owner == GUARD_OWNER
