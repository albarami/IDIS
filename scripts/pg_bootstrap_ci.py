#!/usr/bin/env python3
"""PostgreSQL bootstrap script for CI/test environments.

This script creates the app role and test database for IDIS integration tests.
It is idempotent and safe to run repeatedly.

Environment variables required:
    IDIS_DATABASE_ADMIN_URL: Admin connection URL (postgres superuser)
    IDIS_DATABASE_URL: App connection URL (for verification)
    IDIS_PG_APP_USER: App role username to create (default: idis_app)
    IDIS_PG_APP_PASSWORD: App role password (default: idis_app_pw)
    IDIS_PG_DB_NAME: Test database name (default: idis_test)

Usage:
    python scripts/pg_bootstrap_ci.py           # Full bootstrap
    python scripts/pg_bootstrap_ci.py --verify-only  # Verify connectivity only
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

_PREVIEW_KEYS = (
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
)
_GLOBAL_DATABASE_KEYS = (
    "IDIS_DATABASE_URL",
    "IDIS_DATABASE_ADMIN_URL",
    "IDIS_DATABASE_ERASURE_URL",
    "IDIS_ERASURE_DATABASE_URL",
    "IDIS_REQUIRE_POSTGRES",
)
_PREVIEW_DATABASE = re.compile(r"idis_c3_callers_[1-9][0-9]*_[1-9][0-9]*_[0-9a-f]{16}")


@dataclass(frozen=True)
class RequiredPreviewConfig:
    """One explicitly disposable CI child; not production admission authority."""

    context: str
    root: Path
    workspace: Path
    database: str
    host: str
    port: int
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str
    service_container_id: str
    github_env: Path | None

    def database_url(self, *, admin: bool = False, maintenance: bool = False) -> str:
        user, password = (
            (self.admin_user, self.admin_password) if admin else (self.app_user, self.app_password)
        )
        database = "postgres" if maintenance else self.database
        credentials = f"{quote(user, safe='')}:{quote(password, safe='')}"
        return f"postgresql+psycopg2://{credentials}@{self.host}:{self.port}/{database}"


def _unalias_path(value: str) -> Path:
    if any(token in value for token in ("\r", "\n", "<<")):
        raise ValueError("PREVIEW_PATH_INVALID")
    path = Path(value)
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("PREVIEW_PATH_INVALID")
    for parent in (path, *path.parents):
        if parent.is_symlink() or (
            parent.exists()
            and getattr(parent.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("PREVIEW_PATH_ALIAS")
    return path


def required_preview_config(
    context: str, env: Mapping[str, str], *, worker: bool = False
) -> RequiredPreviewConfig:
    """Reject unintended global DB authority before root creation or connection."""
    if context not in {"check", "postgres"}:
        raise ValueError("PREVIEW_CONTEXT_INVALID")
    if any(key in env for key in (*_GLOBAL_DATABASE_KEYS, *_PREVIEW_KEYS)):
        raise ValueError("PREVIEW_GLOBAL_DATABASE_ENV_FORBIDDEN")
    if any(key in env for key in ("RUNNER_DEBUG", "ACTIONS_STEP_DEBUG")):
        raise ValueError("PREVIEW_DEBUG_FORBIDDEN")
    try:
        run, attempt = env["GITHUB_RUN_ID"], env["GITHUB_RUN_ATTEMPT"]
        if not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (run, attempt)):
            raise ValueError("PREVIEW_RUN_INVALID")
        host, port_value = env["IDIS_PG_HOST"], env["IDIS_PG_PORT"]
        if host != "127.0.0.1" or not re.fullmatch(r"[1-9][0-9]*", port_value):
            raise ValueError("PREVIEW_ENDPOINT_INVALID")
        port = int(port_value)
        if not 0 < port <= 65535:
            raise ValueError("PREVIEW_ENDPOINT_INVALID")
        service = env["IDIS_CI_POSTGRES_SERVICE_ID"]
        if not re.fullmatch(r"[0-9a-f]{64}", service):
            raise ValueError("PREVIEW_SERVICE_INVALID")
        runner, workspace = (_unalias_path(env[key]) for key in ("RUNNER_TEMP", "GITHUB_WORKSPACE"))
        root = runner / f"idis-c3-{context}-{run}-{attempt}"
        if root.is_relative_to(workspace) or "_runner_file_commands" in root.parts:
            raise ValueError("PREVIEW_ROOT_INVALID")
        nonce = env.get("IDIS_CI_PREVIEW_NONCE", secrets.token_hex(8))
        database = f"idis_c3_callers_{run}_{attempt}_{nonce}"
        if len(database.encode()) > 63 or _PREVIEW_DATABASE.fullmatch(database) is None:
            raise ValueError("PREVIEW_DATABASE_INVALID")
        admin_user, admin_password, app_user, app_password = (
            env[key]
            for key in (
                "PG_ADMIN_USER",
                "PG_ADMIN_PASSWORD",
                "IDIS_PG_APP_USER",
                "IDIS_PG_APP_PASSWORD",
            )
        )
        if (
            not all((admin_password, app_password))
            or not all(
                re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", user) for user in (admin_user, app_user)
            )
            or admin_user == app_user
        ):
            raise ValueError("PREVIEW_ROLE_INPUT_INVALID")
        github_env = _unalias_path(env["GITHUB_ENV"]) if context == "check" and not worker else None
    except KeyError:
        raise ValueError("PREVIEW_INPUT_MISSING") from None
    return RequiredPreviewConfig(
        context,
        root,
        workspace,
        database,
        host,
        port,
        admin_user,
        admin_password,
        app_user,
        app_password,
        service,
        github_env,
    )


def required_preview_environment(config: RequiredPreviewConfig) -> dict[str, str]:
    """Explicit allowlist: no ambient libpq, Python startup, token or file channels."""
    _, _, _, run, attempt, nonce = config.database.split("_")
    return {
        "PATH": os.pathsep.join((str(config.workspace / ".venv" / "bin"), "/usr/bin", "/bin")),
        "HOME": str(config.root / "home"),
        "TMPDIR": str(config.root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PG_ADMIN_USER": config.admin_user,
        "PG_ADMIN_PASSWORD": config.admin_password,
        "RUNNER_TEMP": str(config.root.parent),
        "GITHUB_WORKSPACE": str(config.workspace),
        "GITHUB_RUN_ID": run,
        "GITHUB_RUN_ATTEMPT": attempt,
        "IDIS_CI_PREVIEW_NONCE": nonce,
        "IDIS_PG_HOST": config.host,
        "IDIS_PG_PORT": str(config.port),
        "IDIS_PG_APP_USER": config.app_user,
        "IDIS_PG_APP_PASSWORD": config.app_password,
        "IDIS_CI_POSTGRES_SERVICE_ID": config.service_container_id,
    }


def required_preview_receipt(config: RequiredPreviewConfig, oid: object) -> dict[str, object]:
    if type(oid) is not int or oid <= 0:
        raise ValueError("PREVIEW_OID_INVALID")
    return {
        "schema_version": "cir04c3-required-frozen-preview-db-v1",
        "owner": "CIR-04 required-caller disposable fixture",
        "absent_before_create": True,
        "service_container_id": config.service_container_id,
        "host": config.host,
        "port": config.port,
        "database": config.database,
        "app_role": config.app_user,
        "database_oid": oid,
        "revision": "0038",
        "role_flags": {"rolsuper": False, "rolbypassrls": False},
    }


def write_required_preview_receipt(
    config: RequiredPreviewConfig, receipt: dict[str, object]
) -> tuple[Path, str]:
    path = config.root / "database-receipt.json"
    raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        if output.write(raw) != len(raw):
            raise OSError("PREVIEW_RECEIPT_SHORT_WRITE")
        output.flush()
        os.fsync(output.fileno())
    if path.read_bytes() != raw:
        raise OSError("PREVIEW_RECEIPT_BYTES_CHANGED")
    return path, hashlib.sha256(raw).hexdigest()


def export_required_preview(config: RequiredPreviewConfig, receipt: Path, digest: str) -> None:
    """One write; a partial file-command write is fatal, not crash-atomic or retried."""
    if config.context != "check" or config.github_env is None:
        raise ValueError("PREVIEW_EXPORT_FORBIDDEN")
    values = (config.database_url(), str(receipt), digest)
    if any(not value or any(token in value for token in ("\r", "\n", "<<")) for value in values):
        raise ValueError("PREVIEW_EXPORT_VALUE_INVALID")
    raw = "".join(
        f"{key}={value}\n" for key, value in zip(_PREVIEW_KEYS, values, strict=True)
    ).encode()
    fd = os.open(config.github_env, os.O_WRONLY | os.O_APPEND)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.write(fd, raw) != len(raw):
            raise OSError("PREVIEW_EXPORT_FAILED")
    finally:
        os.close(fd)


def validate_required_app_posture(row: Sequence[object] | None, memberships: int) -> bool:
    """Validate every existing flag and both membership directions before writes."""
    if memberships != 0:
        raise ValueError("PREVIEW_APP_MEMBERSHIP_FORBIDDEN")
    if row is None:
        return False
    expected = (True, False, False, False, False, False, False)
    if (
        len(row) != len(expected)
        or any(type(value) is not bool for value in row)
        or tuple(row) != expected
    ):
        raise ValueError("PREVIEW_APP_POSTURE_INVALID")
    return True


def create_required_preview_root(config: RequiredPreviewConfig) -> None:
    """Exclusive acquisition before output redirection; never reuse or recursively remove."""
    _unalias_path(str(config.root))
    config.root.mkdir(mode=0o700)
    for name in ("home", "tmp"):
        (config.root / name).mkdir(mode=0o700)
    _validate_required_preview_root(config)


_PREVIEW_FAILURE_CODE_NAME = "preview-failure-code"


def _validate_required_preview_root(config: RequiredPreviewConfig) -> None:
    _unalias_path(str(config.root))
    info = config.root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("PREVIEW_ROOT_INVALID")
    if sys.platform != "win32" and (
        info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("PREVIEW_ROOT_CUSTODY_INVALID")
    allowed = {
        "home",
        "tmp",
        "bootstrap.log",
        "database-receipt.json",
        "revalidate.log",
        "bootstrap-command.sh",
        "revalidate-command.sh",
        "tests-command.sh",
        "c3-tests.log",
        _PREVIEW_FAILURE_CODE_NAME,
    }
    for path in config.root.iterdir():
        if path.name not in allowed or path.is_symlink():
            raise ValueError("PREVIEW_ROOT_CONTENT_INVALID")
        if path.name not in {"home", "tmp"}:
            entry = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                raise ValueError("PREVIEW_ROOT_CONTENT_INVALID")


def _required_app_present(connection: object, app_user: str) -> bool:
    from sqlalchemy import Connection, text

    if not isinstance(connection, Connection):
        raise TypeError("PREVIEW_CONNECTION_REQUIRED")
    row = connection.execute(
        text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
            "rolbypassrls,rolreplication "
            "FROM pg_catalog.pg_roles WHERE rolname=:role"
        ),
        {"role": app_user},
    ).one_or_none()
    memberships = connection.execute(
        text(
            "SELECT count(*) FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
            "JOIN pg_catalog.pg_roles granted ON granted.oid=m.roleid "
            "WHERE member.rolname=:role OR granted.rolname=:role"
        ),
        {"role": app_user},
    ).scalar_one()
    return validate_required_app_posture(tuple(row) if row is not None else None, memberships)


def _prepare_required_roles(config: RequiredPreviewConfig, *, create_missing: bool) -> None:
    from prepare_ci_erasure_roles import (
        _EXPECTED,
        _read_roles,
        _validate_existing,
        prepare_ci_erasure_roles,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(
        config.database_url(admin=True, maintenance=True),
        poolclass=NullPool,
        connect_args={"connect_timeout": 10},
    )
    try:
        with engine.connect() as connection:
            present = _required_app_present(connection, config.app_user)
        if not present:
            if not create_missing:
                raise ValueError("PREVIEW_APP_MISSING")
            create_app_role(
                config.database_url(admin=True, maintenance=True),
                config.app_user,
                config.app_password,
            )
            with engine.connect() as connection:
                if not _required_app_present(connection, config.app_user):
                    raise ValueError("PREVIEW_APP_MISSING")
        with engine.begin() as connection:
            if create_missing:
                prepare_ci_erasure_roles(connection)
            else:
                roles = _read_roles(connection)
                _validate_existing(connection, roles)
                if set(roles) != {role.name for role in _EXPECTED}:
                    raise ValueError("PREVIEW_ERASURE_ROLES_MISSING")
    finally:
        engine.dispose()


def _create_required_database(config: RequiredPreviewConfig) -> int:
    """Exactly one CREATE; a duplicate/race is fatal, never converted into reuse."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    if _PREVIEW_DATABASE.fullmatch(config.database) is None or len(config.database.encode()) > 63:
        raise ValueError("PREVIEW_DATABASE_INVALID")
    engine = create_engine(
        config.database_url(admin=True, maintenance=True),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 10},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{config.database}" TEMPLATE template0'))
            oid = connection.execute(
                text("SELECT oid FROM pg_catalog.pg_database WHERE datname=:name"),
                {"name": config.database},
            ).scalar_one()
            required_preview_receipt(config, oid)  # Exact type/positive OID before any grant.
            connection.execute(
                text(f'GRANT CONNECT ON DATABASE "{config.database}" TO "{config.app_user}"')
            )
            return int(oid)
    finally:
        engine.dispose()


def _verify_required_database(config: RequiredPreviewConfig, oid: int) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    for admin in (True, False):
        engine = create_engine(
            config.database_url(admin=admin),
            poolclass=NullPool,
            connect_args={"connect_timeout": 10},
        )
        try:
            with engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    text(
                        "SELECT current_database(),current_user,d.oid,r.rolsuper,r.rolbypassrls "
                        "FROM pg_catalog.pg_database d "
                        "JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
                        "WHERE d.datname=current_database()"
                    )
                ).one()
                expected_role = config.admin_user if admin else config.app_user
                if tuple(row[:3]) != (config.database, expected_role, oid):
                    raise ValueError("PREVIEW_DATABASE_IDENTITY_CHANGED")
                if not admin and (row[3] is not False or row[4] is not False):
                    raise ValueError("PREVIEW_APP_POSTURE_INVALID")
                if list(
                    connection.execute(
                        text("SELECT version_num FROM public.alembic_version")
                    ).scalars()
                ) != ["0038"]:
                    raise ValueError("PREVIEW_REVISION_INVALID")
        finally:
            engine.dispose()


def _required_preview_worker(config: RequiredPreviewConfig) -> None:
    """Fixed clean child: ceiling, posture, strict CREATE, real migration, verified receipt."""
    _validate_required_preview_root(config)
    subprocess.run(
        [sys.executable, str(config.workspace / "scripts/check_migration_ceiling.py")],
        check=True,
        cwd=config.workspace,
    )
    _prepare_required_roles(config, create_missing=True)
    oid = _create_required_database(config)
    # Legacy migration/grant/table checks are reused only on this newly owned child.
    os.environ["IDIS_DATABASE_ADMIN_URL"] = config.database_url(admin=True)
    os.environ["IDIS_DATABASE_URL"] = config.database_url()
    os.environ["IDIS_REQUIRE_POSTGRES"] = "1"
    run_migrations(config.database_url(admin=True))
    grant_schema_permissions(config.database_url(admin=True), config.app_user)
    verify_tables_exist(config.database_url())
    _verify_required_database(config, oid)
    _prepare_required_roles(config, create_missing=False)
    write_required_preview_receipt(config, required_preview_receipt(config, oid))


def _read_required_preview_receipt(
    config: RequiredPreviewConfig,
) -> tuple[RequiredPreviewConfig, Path, str, int]:
    _validate_required_preview_root(config)
    path = config.root / "database-receipt.json"
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if not isinstance(receipt, dict) or type(receipt.get("database_oid")) is not int:
        raise ValueError("PREVIEW_RECEIPT_INVALID")
    database = receipt.get("database")
    if not isinstance(database, str) or _PREVIEW_DATABASE.fullmatch(database) is None:
        raise ValueError("PREVIEW_DATABASE_INVALID")
    # A receipt may supply the random suffix, never a different run/attempt or endpoint.
    if database.rsplit("_", 1)[0] != config.database.rsplit("_", 1)[0]:
        raise ValueError("PREVIEW_DATABASE_CONTEXT_CHANGED")
    bound = replace(config, database=database)
    oid = receipt["database_oid"]
    if type(receipt.get("port")) is not int or receipt != required_preview_receipt(bound, oid):
        raise ValueError("PREVIEW_RECEIPT_INVALID")
    return bound, path, hashlib.sha256(raw).hexdigest(), oid


def _capture_required_preview(
    config: RequiredPreviewConfig,
    *,
    phase: Literal["bootstrap", "revalidate", "tests"] = "bootstrap",
) -> tuple[int, bool]:
    """Reuse held-child capture only, never private admission/fetch authority."""
    from ci_private_inputs import Interrupted, bash_path, capture_script

    if phase != "bootstrap" and config.context != "postgres":
        raise ValueError("PREVIEW_PHASE_FORBIDDEN")
    name = phase
    arguments = [sys.executable, str(Path(__file__).resolve())]
    environment = required_preview_environment(config)
    if phase == "tests":
        arguments = [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_cir04c3_inline_claim_postgres.py",
            "tests/test_cir04c3_ingestion_provenance.py",
            "tests/test_cir04c3_frozen_preview_postgres.py",
            "tests/test_cir04c2_proof_admission_postgres.py",
            "tests/test_cir04c2_postgres_compat.py",
            "-v",
            "--junitxml=c3_postgres_integration_pytest.xml",
        ]
        environment = {
            key: environment[key] for key in ("PATH", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE")
        }
        environment.update(
            IDIS_DATABASE_URL=config.database_url(),
            IDIS_DATABASE_ADMIN_URL=config.database_url(admin=True),
            IDIS_REQUIRE_POSTGRES="1",
        )
    elif phase == "revalidate":
        arguments += ["--required-preview-revalidate"]
    else:
        arguments += ["--required-preview-worker", config.context]
    script = config.root / (name + "-command.sh")
    with script.open("x", encoding="utf-8", newline="\n") as output:
        output.write("cd -- " + shlex.quote(str(config.workspace)) + "\n")
        output.write("exec " + shlex.join(arguments) + "\n")
    try:
        result = capture_script(
            script,
            config.root / ("c3-tests.log" if phase == "tests" else name + ".log"),
            environment=environment,
            bash=bash_path(),
        )
        return result, False
    except Interrupted as exc:
        return 128 + exc.signum, exc.cleanup_failed


_PREVIEW_PUBLIC_CODES = frozenset(
    {
        "PREVIEW_APP_MEMBERSHIP_FORBIDDEN",
        "PREVIEW_APP_MISSING",
        "PREVIEW_APP_POSTURE_INVALID",
        "PREVIEW_CONNECTION_REQUIRED",
        "PREVIEW_CONTEXT_INVALID",
        "PREVIEW_DATABASE_CONTEXT_CHANGED",
        "PREVIEW_DATABASE_IDENTITY_CHANGED",
        "PREVIEW_DATABASE_INVALID",
        "PREVIEW_DEBUG_FORBIDDEN",
        "PREVIEW_ENDPOINT_INVALID",
        "PREVIEW_ERASURE_ROLES_MISSING",
        "PREVIEW_EXPORT_FAILED",
        "PREVIEW_EXPORT_FORBIDDEN",
        "PREVIEW_EXPORT_VALUE_INVALID",
        "PREVIEW_GLOBAL_DATABASE_ENV_FORBIDDEN",
        "PREVIEW_INPUT_MISSING",
        "PREVIEW_OID_INVALID",
        "PREVIEW_PATH_ALIAS",
        "PREVIEW_PATH_INVALID",
        "PREVIEW_PHASE_FORBIDDEN",
        "PREVIEW_RECEIPT_BYTES_CHANGED",
        "PREVIEW_RECEIPT_INVALID",
        "PREVIEW_RECEIPT_SHORT_WRITE",
        "PREVIEW_REVISION_INVALID",
        "PREVIEW_ROLE_INPUT_INVALID",
        "PREVIEW_ROOT_CONTENT_INVALID",
        "PREVIEW_ROOT_CUSTODY_INVALID",
        "PREVIEW_ROOT_INVALID",
        "PREVIEW_RUN_INVALID",
        "PREVIEW_SERVICE_INVALID",
    }
)
_PREVIEW_PHASES = frozenset({"bootstrap", "revalidate", "tests"})


def _preview_public_code(exc: BaseException) -> str:
    """Return one allowlisted code. Never echo exception text, paths, or SQL."""
    if isinstance(exc, (ValueError, OSError, TypeError)) and len(exc.args) == 1:
        code = exc.args[0]
        if isinstance(code, str) and code in _PREVIEW_PUBLIC_CODES:
            return code
    return "PREVIEW_UNCLASSIFIED"


def _preview_failure_line(
    status_key: Literal["required_preview", "required_c3_postgres"],
    status: Literal["failed", "blocked"],
    exc: BaseException,
    phase: Literal["bootstrap", "revalidate", "tests"],
) -> str:
    """Public diagnostic: fixed code and phase only."""
    if phase not in _PREVIEW_PHASES:
        phase = "bootstrap"
    return json.dumps(
        {status_key: status, "code": _preview_public_code(exc), "phase": phase},
        separators=(",", ":"),
    )


def _record_preview_child_code(config: RequiredPreviewConfig, exc: BaseException) -> None:
    """Write one allow-listed code into the owned root. Never write other text."""
    code = _preview_public_code(exc)
    if code not in _PREVIEW_PUBLIC_CODES:
        return
    raw = code.encode("ascii")
    try:
        fd = os.open(
            config.root / _PREVIEW_FAILURE_CODE_NAME,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except OSError:
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or os.write(fd, raw) != len(raw):
            return
    except OSError:
        return
    finally:
        os.close(fd)


def _read_preview_child_code(root: Path) -> str:
    """Re-check the child file. Absent, unreadable, or unknown is unclassified."""
    path = root / _PREVIEW_FAILURE_CODE_NAME
    try:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "PREVIEW_UNCLASSIFIED"
        raw = path.read_bytes()
    except OSError:
        return "PREVIEW_UNCLASSIFIED"
    try:
        code = raw.decode("ascii")
    except UnicodeError:
        return "PREVIEW_UNCLASSIFIED"
    if code not in _PREVIEW_PUBLIC_CODES:
        return "PREVIEW_UNCLASSIFIED"
    return code


def _preview_exit_line(
    status_key: Literal["required_preview", "required_c3_postgres"],
    status: Literal["failed", "blocked", "interrupted"],
    exit_code: int,
    cleanup_failed: bool,
    root: Path,
    phase: Literal["bootstrap", "revalidate", "tests"],
) -> str:
    """Public capture-exit line: fixed keys and allow-listed literals only."""
    if phase not in _PREVIEW_PHASES:
        phase = "bootstrap"
    return json.dumps(
        {
            status_key: status,
            "exit": exit_code,
            "cleanup_failed": cleanup_failed,
            "code": _read_preview_child_code(root),
            "phase": phase,
        },
        separators=(",", ":"),
    )


def _call_preview_child(config: RequiredPreviewConfig, body: Callable[[], None]) -> None:
    """Child boundary: persist an allow-listed code, then preserve the failure."""
    try:
        body()
    except (Exception, SystemExit) as exc:
        _record_preview_child_code(config, exc)
        raise


def required_preview_main(context: str) -> int:
    """Acquire once, capture before fixed worker launch, publish only after qualification."""
    try:
        config = required_preview_config(context, os.environ)
        create_required_preview_root(config)
        result, cleanup_failed = _capture_required_preview(config)
        if result != 0:
            print(
                _preview_exit_line(
                    "required_preview",
                    "failed",
                    result,
                    cleanup_failed,
                    config.root,
                    "bootstrap",
                )
            )
            return result
        config, path, digest, _ = _read_required_preview_receipt(config)
        if context == "check":
            export_required_preview(config, path, digest)
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "required_preview": "failed",
                    "exit": 130,
                    "cleanup_failed": False,
                    "code": "PREVIEW_UNCLASSIFIED",
                    "phase": "bootstrap",
                },
                separators=(",", ":"),
            )
        )
        return 130
    except (Exception, SystemExit) as exc:
        # Raw SQL/credentials/paths remain in the separate owned log, never public diagnostics.
        print(_preview_failure_line("required_preview", "failed", exc, "bootstrap"))
        return 1
    print('{"required_preview":"ready"}')
    return 0


def run_required_preview_postgres() -> int:
    """Pinned required modules on the receipt-bound child; never a general runner."""
    phase: Literal["bootstrap", "revalidate", "tests"] = "revalidate"
    try:
        config = required_preview_config("postgres", os.environ)
        config, _, _, _ = _read_required_preview_receipt(config)
        # Revalidation runs in its own clean worker, before launching pytest.
        checked, cleanup_failed = _capture_required_preview(config, phase=phase)
        if checked != 0:
            print(
                _preview_exit_line(
                    "required_c3_postgres",
                    "blocked",
                    checked,
                    cleanup_failed,
                    config.root,
                    "revalidate",
                )
            )
            return checked
        phase = "tests"
        result, cleanup_failed = _capture_required_preview(config, phase=phase)
        if result in {130, 143} or cleanup_failed:
            print(
                _preview_exit_line(
                    "required_c3_postgres",
                    "interrupted",
                    result,
                    cleanup_failed,
                    config.root,
                    "tests",
                )
            )
            return result
        # Only this explicit public-synthetic PG phase is replayed to the existing public tee.
        # Bootstrap/revalidation/private check logs are never replayed or uploaded here.
        with (config.root / "c3-tests.log").open("rb") as log:
            shutil.copyfileobj(log, sys.stdout.buffer)
        return result
    except KeyboardInterrupt:
        interrupt_phase: Literal["bootstrap", "revalidate", "tests"] = (
            phase if phase in _PREVIEW_PHASES else "bootstrap"
        )
        print(
            json.dumps(
                {
                    "required_c3_postgres": "interrupted",
                    "exit": 130,
                    "cleanup_failed": False,
                    "code": "PREVIEW_UNCLASSIFIED",
                    "phase": interrupt_phase,
                },
                separators=(",", ":"),
            )
        )
        return 130
    except (Exception, SystemExit) as exc:
        # Raw SQL/credentials/paths remain in the separate owned log, never public diagnostics.
        print(_preview_failure_line("required_c3_postgres", "blocked", exc, phase))
        return 1


def get_env_required(name: str) -> str:
    """Get required environment variable or exit."""
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: Required environment variable {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def get_env_optional(name: str, default: str) -> str:
    """Get optional environment variable with default."""
    return os.environ.get(name, default)


def _normalize_url_for_psycopg2(url: str) -> str:
    """Convert SQLAlchemy URL to psycopg2-compatible URL.

    SQLAlchemy uses postgresql+psycopg2:// but raw psycopg2 needs postgresql://.
    """
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def wait_for_postgres(admin_url: str, max_retries: int = 10, delay: float = 2.0) -> None:
    """Wait for PostgreSQL to be ready."""
    import psycopg2

    print("Waiting for PostgreSQL to be ready...")
    url = _normalize_url_for_psycopg2(admin_url)

    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(url)
            conn.close()
            print("PostgreSQL is ready")
            return
        except psycopg2.OperationalError as e:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: not ready, waiting...")
                time.sleep(delay)
            else:
                print(f"ERROR: PostgreSQL not ready after {max_retries} attempts", file=sys.stderr)
                print(f"  Last error: {e}", file=sys.stderr)
                sys.exit(1)


def create_app_role(admin_url: str, app_user: str, app_password: str) -> None:
    """Create the app role if it doesn't exist (idempotent)."""
    import psycopg2

    print(f"Creating app role '{app_user}' (if not exists)...")
    url = _normalize_url_for_psycopg2(admin_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        create_role_sql = """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = %s) THEN
                    EXECUTE format(
                        'CREATE ROLE %%I WITH LOGIN PASSWORD %%L '
                        'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
                        %s, %s
                    );
                    RAISE NOTICE 'Role created';
                ELSE
                    RAISE NOTICE 'Role already exists';
                END IF;
            END
            $$;
        """
        cur.execute(create_role_sql, (app_user, app_user, app_password))
        print(f"  App role '{app_user}' ready (NOSUPERUSER, NOBYPASSRLS)")
    finally:
        cur.close()
        conn.close()


def create_database(admin_url: str, db_name: str, app_user: str) -> None:
    """Create the test database if it doesn't exist (idempotent)."""
    import psycopg2

    print(f"Creating database '{db_name}' (if not exists)...")
    url = _normalize_url_for_psycopg2(admin_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{db_name}"')
            print(f"  Database '{db_name}' created")
        else:
            print(f"  Database '{db_name}' already exists")

        cur.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{app_user}"')
        print(f"  CONNECT granted to '{app_user}'")
    finally:
        cur.close()
        conn.close()


def grant_schema_permissions(db_url: str, app_user: str) -> None:
    """Grant schema permissions to the app role."""
    import psycopg2

    print(f"Granting schema permissions to '{app_user}'...")
    url = _normalize_url_for_psycopg2(db_url)

    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{app_user}"')
        cur.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{app_user}"'
        )
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_user}"'
        )
        cur.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_user}"')
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{app_user}"'
        )
        print("  Schema permissions granted")
    finally:
        cur.close()
        conn.close()


def verify_app_role_security(db_url: str, app_user: str) -> None:
    """Verify the app role has correct security settings."""
    import psycopg2

    print(f"Verifying app role '{app_user}' security settings...")
    url = _normalize_url_for_psycopg2(db_url)

    conn = psycopg2.connect(url)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
            (app_user,),
        )
        row = cur.fetchone()

        if row is None:
            print(f"ERROR: Role '{app_user}' not found", file=sys.stderr)
            sys.exit(1)

        rolsuper, rolbypassrls = row

        if rolsuper:
            print(f"ERROR: Role '{app_user}' is SUPERUSER - RLS will be bypassed!", file=sys.stderr)
            sys.exit(2)

        if rolbypassrls:
            print(
                f"ERROR: Role '{app_user}' has BYPASSRLS - RLS will be bypassed!", file=sys.stderr
            )
            sys.exit(2)

        print(f"  Role '{app_user}' is secure: NOSUPERUSER, NOBYPASSRLS")
    finally:
        cur.close()
        conn.close()


def _safe_url_info(url: str) -> str:
    """Extract safe (no password) info from URL for logging."""
    try:
        parsed = urlparse(url)
        db = parsed.path.lstrip("/")
        return f"host={parsed.hostname}, port={parsed.port}, db={db}, user={parsed.username}"
    except Exception:
        return "(could not parse URL)"


def verify_connectivity(admin_url: str, app_url: str) -> None:
    """Verify connectivity for both admin and app roles with diagnostics."""
    import psycopg2

    print("=" * 60)
    print("Verifying PostgreSQL Connectivity")
    print("=" * 60)

    # Check env vars are set (no values printed)
    print("Environment check:")
    print(f"  IDIS_DATABASE_ADMIN_URL set: {bool(admin_url)}")
    print(f"  IDIS_DATABASE_URL set: {bool(app_url)}")

    # Admin connection check
    print("\nAdmin connection:")
    print(f"  Target: {_safe_url_info(admin_url)}")
    admin_normalized = _normalize_url_for_psycopg2(admin_url)

    try:
        conn = psycopg2.connect(admin_normalized)
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        admin_user = cur.fetchone()[0]
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        print(f"  Connected as: {admin_user}")
        print(f"  rolsuper={row[0]}, rolbypassrls={row[1]}")
        cur.close()
        conn.close()
        print("  Status: OK")
    except psycopg2.Error as e:
        print("  Status: FAILED")
        print(f"  Error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    # App connection check
    print("\nApp connection:")
    print(f"  Target: {_safe_url_info(app_url)}")
    app_normalized = _normalize_url_for_psycopg2(app_url)

    try:
        conn = psycopg2.connect(app_normalized)
        cur = conn.cursor()
        cur.execute("SELECT current_user")
        app_user = cur.fetchone()[0]
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        print(f"  Connected as: {app_user}")
        print(f"  rolsuper={row[0]}, rolbypassrls={row[1]}")

        # Hard gate: app role must NOT be superuser or bypassrls
        if row[0]:
            print("  FATAL: App role is SUPERUSER - RLS bypassed!", file=sys.stderr)
            cur.close()
            conn.close()
            sys.exit(2)
        if row[1]:
            print("  FATAL: App role has BYPASSRLS - RLS bypassed!", file=sys.stderr)
            cur.close()
            conn.close()
            sys.exit(2)

        cur.close()
        conn.close()
        print("  Status: OK (non-superuser, no bypassrls)")
    except psycopg2.Error as e:
        print("  Status: FAILED")
        print(f"  Error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Connectivity verification PASSED")
    print("=" * 60)


def run_migrations(db_url: str) -> None:
    """Run Alembic migrations on the target database using programmatic API."""
    print("Running database migrations...")

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        print("ERROR: Alembic dependency import failed", file=sys.stderr)
        sys.exit(1)

    try:
        # Find migrations directory relative to idis package
        import idis.persistence.migrations
        from idis.persistence.db import _ensure_psycopg_driver

        migrations_dir = os.path.dirname(idis.persistence.migrations.__file__)

        # Create Alembic config programmatically
        config = Config()
        config.set_main_option("script_location", migrations_dir)
        # This is a SQLAlchemy URL, not a raw psycopg2 connection string.
        # ConfigParser interpolation requires literal percent signs to be escaped.
        url = _ensure_psycopg_driver(db_url)
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

        # Run upgrade to head
        command.upgrade(config, "head")
        print("  Migrations completed successfully")
    except ImportError:
        print("ERROR: Migration dependency import failed", file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("ERROR: Migration failed", file=sys.stderr)
        sys.exit(1)


def verify_tables_exist(db_url: str) -> None:
    """Verify that required tables exist in the database."""
    import psycopg2

    print("Verifying required tables exist...")
    url = _normalize_url_for_psycopg2(db_url)

    required_tables = [
        # 0001: foundation
        "audit_events",
        "idempotency_records",
        "deals",
        "alembic_version",
        # 0003: webhooks
        "webhooks",
        "webhook_delivery_attempts",
        # 0004: ingestion
        "document_artifacts",
        "documents",
        "document_spans",
        # 0005: calculations
        "tenants",
        "deterministic_calculations",
        "calc_sanads",
        # 0007: claims/sanad/defects
        "claims",
        "sanads",
        "defects",
        # 0009: runs + debate + deliverables + human gates + overrides
        "runs",
        "debate_sessions",
        "deliverables",
        "human_gates",
        "human_gate_actions",
        "overrides",
        # 0010: run_steps + evidence_items
        "run_steps",
        "evidence_items",
    ]

    conn = psycopg2.connect(url)
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        existing_tables = {row[0] for row in cur.fetchall()}

        missing = [t for t in required_tables if t not in existing_tables]
        if missing:
            print(f"ERROR: Missing tables: {missing}", file=sys.stderr)
            sys.exit(1)

        print(f"  Found tables: {sorted(existing_tables & set(required_tables))}")
    finally:
        cur.close()
        conn.close()


def export_github_env(
    host: str,
    port: str,
    db_name: str,
    admin_user: str,
    admin_password: str,
    app_user: str,
    app_password: str,
) -> None:
    """Write environment variables to GITHUB_ENV file (no secrets in stdout)."""
    github_env_path = os.environ.get("GITHUB_ENV")
    if not github_env_path:
        print("ERROR: GITHUB_ENV not set (not running in GitHub Actions?)", file=sys.stderr)
        sys.exit(1)

    # Build URLs pointing to idis_test (not postgres)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{db_name}"
    app_url = f"postgresql://{app_user}:{app_password}@{host}:{port}/{db_name}"

    with open(github_env_path, "a") as f:
        f.write(f"IDIS_PG_HOST={host}\n")
        f.write(f"IDIS_PG_PORT={port}\n")
        f.write("IDIS_REQUIRE_POSTGRES=1\n")
        f.write(f"IDIS_DATABASE_ADMIN_URL={admin_url}\n")
        f.write(f"IDIS_DATABASE_URL={app_url}\n")

    # Log only safe info (no passwords)
    print("Exported to GITHUB_ENV:")
    print(f"  IDIS_PG_HOST={host}")
    print(f"  IDIS_PG_PORT={port}")
    print("  IDIS_REQUIRE_POSTGRES=1")
    print(f"  IDIS_DATABASE_ADMIN_URL=postgresql://{admin_user}:***@{host}:{port}/{db_name}")
    print(f"  IDIS_DATABASE_URL=postgresql://{app_user}:***@{host}:{port}/{db_name}")


def main() -> None:
    """Main bootstrap entry point."""
    verify_only = "--verify-only" in sys.argv
    export_env = "--export-github-env" in sys.argv

    # Get common config
    host = get_env_optional("IDIS_PG_HOST", "127.0.0.1")
    port = get_env_optional("IDIS_PG_PORT", "5432")
    db_name = get_env_optional("IDIS_PG_DB_NAME", "idis_test")
    admin_user = get_env_optional("PG_ADMIN_USER", "postgres")
    admin_password = get_env_optional("PG_ADMIN_PASSWORD", "postgres")
    app_user = get_env_optional("IDIS_PG_APP_USER", "idis_app")
    app_password = get_env_optional("IDIS_PG_APP_PASSWORD", "idis_app_pw")

    # Build cluster admin URL (points to /postgres for initial bootstrap)
    cluster_admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"

    if verify_only:
        # Verification mode: check connectivity using env vars
        admin_url = os.environ.get("IDIS_DATABASE_ADMIN_URL", "")
        app_url = os.environ.get("IDIS_DATABASE_URL", "")
        if not admin_url or not app_url:
            print(
                "ERROR: IDIS_DATABASE_ADMIN_URL and IDIS_DATABASE_URL required for --verify-only",
                file=sys.stderr,
            )
            sys.exit(1)
        verify_connectivity(admin_url, app_url)
        return

    # Full bootstrap mode
    print("=" * 60)
    print("IDIS PostgreSQL CI Bootstrap")
    print("=" * 60)

    wait_for_postgres(cluster_admin_url)

    create_app_role(cluster_admin_url, app_user, app_password)

    create_database(cluster_admin_url, db_name, app_user)

    # Build URL for idis_test database (admin credentials)
    db_admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{db_name}"
    # Build app URL for idis_test database
    db_app_url = f"postgresql://{app_user}:{app_password}@{host}:{port}/{db_name}"

    # Set env vars IN-PROCESS before migrations (required by idis.persistence.db)
    os.environ["IDIS_DATABASE_ADMIN_URL"] = db_admin_url
    os.environ["IDIS_DATABASE_URL"] = db_app_url
    os.environ["IDIS_REQUIRE_POSTGRES"] = "1"
    print(f"Set in-process env: IDIS_DATABASE_ADMIN_URL -> {db_name} (admin)")
    print(f"Set in-process env: IDIS_DATABASE_URL -> {db_name} (app)")
    print("Set in-process env: IDIS_REQUIRE_POSTGRES=1")

    # Run migrations on idis_test
    run_migrations(db_admin_url)

    # Grant permissions after tables exist
    grant_schema_permissions(db_admin_url, app_user)

    # Verify tables exist
    verify_tables_exist(db_admin_url)

    verify_app_role_security(db_admin_url, app_user)

    # Export env vars to GITHUB_ENV if requested
    if export_env:
        print("\n" + "-" * 60)
        export_github_env(
            host=host,
            port=port,
            db_name=db_name,
            admin_user=admin_user,
            admin_password=admin_password,
            app_user=app_user,
            app_password=app_password,
        )

    print("=" * 60)
    print("Bootstrap complete")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--required-preview":
        raise SystemExit(required_preview_main(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--required-preview-worker":
        _config = required_preview_config(sys.argv[2], os.environ, worker=True)

        def _worker() -> None:
            _required_preview_worker(_config)

        _call_preview_child(_config, _worker)
        raise SystemExit(0)
    if sys.argv[1:] == ["--required-preview-revalidate"]:
        _config = required_preview_config("postgres", os.environ, worker=True)

        def _revalidate() -> None:
            bound, _receipt_path, _receipt_digest, oid = _read_required_preview_receipt(_config)
            _prepare_required_roles(bound, create_missing=False)
            _verify_required_database(bound, oid)

        _call_preview_child(_config, _revalidate)
        raise SystemExit(0)
    if sys.argv[1:] == ["--run-required-preview-postgres"]:
        raise SystemExit(run_required_preview_postgres())
    main()
