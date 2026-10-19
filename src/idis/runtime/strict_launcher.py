"""Strict, profile-first launcher for Slice100 production roles.

Only standard-library modules and :mod:`idis.runtime.profile` are imported at module
load time.  Every runtime target is selected and imported only after the approved
dotenv has been parsed, validated, applied, and hashed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import tempfile
import uuid
from collections.abc import Callable, MutableMapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Never

from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    load_strict_runtime_profile,
    require_runtime_profile,
)

_TARGET_MODULES = {
    "migrate": "alembic.command",
    "governance": "idis.runtime.governance_import",
    "gate": "idis.evaluation.real_example_gate",
    "api": "idis.api.main",
    "worker": "idis.pipeline.worker",
}

Importer = Callable[[str], object]
ProcessAlive = Callable[[int], bool]
Replace = Callable[[Path, Path], object]
PidFileLock = tuple[Path, BinaryIO]

_PID_FILE_LOCKS: dict[Path, PidFileLock] = {}


class StrictLauncherError(RuntimeError):
    """Secret-safe launcher error."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SafeArgumentError(ValueError):
    """Argument failure marker that deliberately retains no raw parser message."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise _SafeArgumentError


def build_parser() -> argparse.ArgumentParser:
    """Build the five-command launcher interface pinned by the Slice100 plan."""
    parser = _SafeArgumentParser(description="Launch an IDIS strict production role.")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="Apply the approved Alembic upgrade.")
    migrate.add_argument("--alembic-config", required=True)
    migrate.add_argument("--upgrade", required=True)

    governance = commands.add_parser(
        "governance",
        help="Import signed governance envelopes with the pinned admin identity.",
    )
    governance.add_argument("--admin-api-key-env", required=True)
    governance.add_argument("--trusted-signer-id-env", required=True)
    governance.add_argument("--trusted-ed25519-key-env", required=True)
    governance.add_argument("--import-rights-approvals-env", required=True)
    governance.add_argument("--import-budget-policy-env", required=True)
    governance.add_argument("--no-secret-output", action="store_true")

    gate = commands.add_parser("gate", help="Run a strict safe-report gate operation.")
    gate.add_argument("--root")
    gate.add_argument("--api-base-url")
    gate.add_argument("--api-key-env")
    gate.add_argument("--canary-api-key-env")
    gate.add_argument("--safe-report-dir")
    gate.add_argument("--prepare-ledger")
    gate.add_argument("--preflight-proof-ledger")
    gate.add_argument("--resume-ledger")
    gate.add_argument("--company-mode", choices=("isolated",))
    gate.add_argument("--expected-company-count", type=int)
    gate.add_argument("--expected-file-counts")
    gate.add_argument("--expected-total-files", type=int)
    gate.add_argument("--bundle-format", choices=("all",))
    gate.add_argument("--live-preflight-only", action="store_true")
    gate.add_argument("--run-llm-probes", action="store_true")
    gate.add_argument("--run-embedding-graph-persistence-probes", action="store_true")
    gate.add_argument("--run-ocr-media-inference-probes", action="store_true")
    gate.add_argument("--run-all-provider-probes", action="store_true")
    gate.add_argument("--evaluate-runtime-prompts", action="store_true")
    gate.add_argument("--promote-approved-runtime-prompts", action="store_true")
    gate.add_argument("--require-full-live", action="store_true")
    gate.add_argument("--prepare-data-rooms", action="store_true")
    gate.add_argument("--preflight-only", action="store_true")
    gate.add_argument("--run-full", action="store_true")
    gate.add_argument("--stop-on-first-failure", action="store_true")
    gate.add_argument("--verify-only", action="store_true")
    gate.add_argument("--verify-restart-survival", action="store_true")
    gate.add_argument("--verify-tenant-isolation", action="store_true")
    gate.add_argument("--no-secret-output", action="store_true")

    api = commands.add_parser("api", help="Start the API-only process.")
    api.add_argument("--host", required=True)
    api.add_argument("--port", required=True, type=int)
    api.add_argument("--pid-file", required=True)

    worker = commands.add_parser("worker", help="Start the canonical worker-only process.")
    worker.add_argument("--watch", action="store_true")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    worker.add_argument("--pid-file", required=True)

    return parser


def load_profile_then_import_runtime(
    *,
    command: str,
    process_env: MutableMapping[str, str] | None = None,
    importer: Importer = importlib.import_module,
) -> tuple[StrictRuntimeProfile, object]:
    """Apply the strict profile and only then import the selected runtime target."""
    profile = load_strict_runtime_profile(command=command, process_env=process_env)
    try:
        target = _TARGET_MODULES[command]
    except KeyError as exc:
        raise StrictLauncherError("STRICT_PROCESS_ROLE_INVALID") from exc
    return profile, importer(target)


def launch_audit_fields(profile: StrictRuntimeProfile) -> dict[str, str | None]:
    """Return the only profile fields safe for durable launch audit."""
    return {
        "base_profile_hmac": profile.base_profile_hmac,
        "command": profile.command,
        "generation": profile.generation,
        "process_role": profile.process_role,
    }


def _default_is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_existing_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="ascii")
        pid = int(text.strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise StrictLauncherError("PID_FILE_INVALID") from exc
    if pid <= 0:
        raise StrictLauncherError("PID_FILE_INVALID")
    return pid


def _pid_file_key(path: str | Path) -> Path:
    return Path(path).resolve(strict=False)


def _acquire_pid_file_lock(path: str | Path) -> PidFileLock:
    """Take a nonblocking OS lock that survives stale lock-file contents."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    stream = lock_path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        if os.name == "nt":
            lock_module: Any = importlib.import_module("msvcrt")
            lock_module.locking(stream.fileno(), lock_module.LK_NBLCK, 1)
        else:
            lock_module = importlib.import_module("fcntl")
            lock_module.flock(
                stream.fileno(),
                lock_module.LOCK_EX | lock_module.LOCK_NB,
            )
    except (BlockingIOError, OSError) as exc:
        stream.close()
        raise StrictLauncherError("PID_FILE_PROCESS_ALIVE") from exc
    return lock_path, stream


def _release_pid_file_lock(lock: PidFileLock) -> None:
    """Release one process-held PID ownership lock without deleting its inode."""
    _lock_path, stream = lock
    try:
        stream.seek(0)
        if os.name == "nt":
            lock_module: Any = importlib.import_module("msvcrt")
            lock_module.locking(stream.fileno(), lock_module.LK_UNLCK, 1)
        else:
            lock_module = importlib.import_module("fcntl")
            lock_module.flock(stream.fileno(), lock_module.LOCK_UN)
    except OSError:
        # Closing the descriptor is the authoritative release on process teardown.
        pass
    finally:
        stream.close()


def release_pid_file(path: str | Path) -> None:
    """Remove a PID file while still holding, then release, its ownership lock."""
    target = Path(path)
    lock = _PID_FILE_LOCKS.pop(_pid_file_key(target), None)
    if lock is None:
        return
    try:
        target.unlink(missing_ok=True)
    finally:
        _release_pid_file_lock(lock)


def write_pid_file(
    path: str | Path,
    *,
    pid: int | None = None,
    is_process_alive: ProcessAlive = _default_is_process_alive,
    replace: Replace = os.replace,
) -> None:
    """Atomically publish a PID, refusing to replace a live owner's file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = _pid_file_key(target)
    lock = _PID_FILE_LOCKS.get(key)
    acquired_here = lock is None
    if lock is None:
        lock = _acquire_pid_file_lock(target)
    selected_pid = os.getpid() if pid is None else pid
    try:
        if selected_pid <= 0:
            raise StrictLauncherError("PID_FILE_INVALID")

        existing_pid = _read_existing_pid(target)
        if (
            existing_pid is not None
            and existing_pid != selected_pid
            and is_process_alive(existing_pid)
        ):
            raise StrictLauncherError("PID_FILE_PROCESS_ALIVE")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(f"{selected_pid}\n")
                stream.flush()
                os.fsync(stream.fileno())
            replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except BaseException:
        if acquired_here:
            _release_pid_file_lock(lock)
        raise
    if acquired_here:
        _PID_FILE_LOCKS[key] = lock


def _forwarded_arguments(raw: Sequence[str]) -> list[str]:
    """Return command-local arguments without interpreting or exposing their values."""
    return list(raw[1:])


def _migration_database_identity(
    profile: StrictRuntimeProfile,
) -> tuple[Any, str, str]:
    """Return the reviewed admin URL, target database, and application role."""
    try:
        require_runtime_profile(profile, command="migrate")
        make_url = importlib.import_module("sqlalchemy.engine").make_url
        admin_url = make_url(profile.values["IDIS_DATABASE_ADMIN_URL"])
        app_url = make_url(profile.values["IDIS_DATABASE_URL"])
        target_database = admin_url.database
        app_database = app_url.database
        app_role = app_url.username
    except StrictProfileError as exc:
        raise StrictLauncherError(exc.code) from exc
    except Exception:
        raise StrictLauncherError("DATABASE_SCHEMA_NOT_READY") from None

    if (
        admin_url.get_backend_name() != "postgresql"
        or app_url.get_backend_name() != "postgresql"
        or admin_url.host != app_url.host
        or admin_url.port != app_url.port
        or not isinstance(target_database, str)
        or target_database != app_database
        or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target_database) is None
        or target_database in {"postgres", "template0", "template1"}
        or not isinstance(app_role, str)
        or not app_role
        or "\x00" in app_role
        or len(app_role.encode("utf-8")) > 63
    ):
        raise StrictLauncherError("DATABASE_SCHEMA_NOT_READY")
    return admin_url, target_database, app_role


def _ensure_migration_database(profile: StrictRuntimeProfile) -> None:
    """Idempotently create the reviewed target from pristine template zero."""
    admin_url, target_database, _app_role = _migration_database_identity(profile)
    engine: Any | None = None
    try:
        sqlalchemy = importlib.import_module("sqlalchemy")
        from idis.persistence.db import _ensure_psycopg_driver

        engine = sqlalchemy.create_engine(
            _ensure_psycopg_driver(
                admin_url.set(database="postgres").render_as_string(hide_password=False)
            ),
            isolation_level="AUTOCOMMIT",
        )
        with engine.connect() as connection:
            exists = connection.execute(
                sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": target_database},
            ).scalar_one_or_none()
            if exists is None:
                quoted_database = engine.dialect.identifier_preparer.quote(target_database)
                connection.exec_driver_sql(f"CREATE DATABASE {quoted_database} TEMPLATE template0")
    except StrictLauncherError:
        raise
    except Exception:
        raise StrictLauncherError("DATABASE_UNAVAILABLE") from None
    finally:
        if engine is not None:
            engine.dispose()


def _alembic_metadata_privileges_absent(
    connection: Any,
    *,
    app_role: str,
    text: Callable[[str], Any],
) -> bool:
    """Require zero effective table or column privilege on Alembic metadata."""
    row = connection.execute(
        text(
            """
            SELECT NOT (
                has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'SELECT'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'INSERT'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'UPDATE'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'DELETE'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'TRUNCATE'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'REFERENCES'
                )
                OR has_table_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'TRIGGER'
                )
                OR has_any_column_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'SELECT'
                )
                OR has_any_column_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'INSERT'
                )
                OR has_any_column_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'UPDATE'
                )
                OR has_any_column_privilege(
                    CAST(:app_role AS name), 'public.alembic_version', 'REFERENCES'
                )
            ) AS privileges_absent
            """
        ),
        {"app_role": app_role},
    ).one()
    return bool(_row_value(row, "privileges_absent", 0))


def _migration_privilege_catalog_is_exact(
    connection: Any,
    *,
    table_names: tuple[str, ...],
    app_role: str,
    target_database: str,
    text: Callable[[str], Any],
) -> bool:
    """Verify the direct and effective privilege boundary for the app role."""
    if not _alembic_metadata_privileges_absent(
        connection,
        app_role=app_role,
        text=text,
    ):
        return False
    expected_crud = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    direct_rows = connection.execute(
        text(
            """
            SELECT c.relname, a.privilege_type
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) AS a
            JOIN pg_roles AS grantee ON grantee.oid = a.grantee
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND grantee.rolname = :app_role
            ORDER BY c.relname, a.privilege_type
            """
        ),
        {"app_role": app_role},
    ).fetchall()
    privileges_by_table: dict[str, set[str]] = {table: set() for table in table_names}
    for row in direct_rows:
        table = str(_row_value(row, "relname", 0))
        privilege = str(_row_value(row, "privilege_type", 1))
        privileges_by_table.setdefault(table, set()).add(privilege)
    if set(privileges_by_table) != set(table_names) or any(
        privileges != expected_crud for privileges in privileges_by_table.values()
    ):
        return False

    schema_rows = connection.execute(
        text(
            """
            SELECT a.privilege_type
            FROM pg_namespace AS n
            CROSS JOIN LATERAL aclexplode(n.nspacl) AS a
            JOIN pg_roles AS grantee ON grantee.oid = a.grantee
            WHERE n.nspname = 'public' AND grantee.rolname = :app_role
            ORDER BY a.privilege_type
            """
        ),
        {"app_role": app_role},
    ).fetchall()
    direct_schema_privileges = {str(_row_value(row, "privilege_type", 0)) for row in schema_rows}
    if direct_schema_privileges != {"USAGE"}:
        return False

    role = connection.execute(
        text(
            """
            SELECT r.rolsuper,
                   r.rolbypassrls,
                   r.rolcreatedb,
                   r.rolcreaterole,
                   EXISTS (
                       SELECT 1 FROM pg_auth_members AS membership
                       WHERE membership.member = r.oid
                   ) AS has_membership
            FROM pg_roles AS r
            WHERE r.rolname = :app_role
            """
        ),
        {"app_role": app_role},
    ).one_or_none()
    if role is None or any(
        bool(_row_value(role, name, index))
        for index, name in enumerate(
            ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "has_membership")
        )
    ):
        return False

    capabilities = connection.execute(
        text(
            """
            SELECT has_schema_privilege(
                       CAST(:app_role AS name), 'public', 'USAGE'
                   ) AS schema_usage,
                   has_schema_privilege(
                       CAST(:app_role AS name), 'public', 'CREATE'
                   ) AS schema_create,
                   has_database_privilege(
                       CAST(:app_role AS name), :target_database, 'CREATE'
                   ) AS database_create
            """
        ),
        {"app_role": app_role, "target_database": target_database},
    ).one()
    if (
        bool(_row_value(capabilities, "schema_usage", 0)) is not True
        or bool(_row_value(capabilities, "schema_create", 1))
        or bool(_row_value(capabilities, "database_create", 2))
    ):
        return False

    forbidden_relations = connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) AS a
            JOIN pg_roles AS grantee ON grantee.oid = a.grantee
            WHERE n.nspname = 'public'
              AND c.relkind NOT IN ('r', 'p')
              AND grantee.rolname = :app_role
            """
        ),
        {"app_role": app_role},
    ).first()
    if forbidden_relations is not None:
        return False

    public_table_grants = connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) AS a
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND a.grantee = 0
            """
        )
    ).first()
    if public_table_grants is not None:
        return False

    default_grants = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_default_acl AS defaults
            CROSS JOIN LATERAL aclexplode(
                COALESCE(
                    defaults.defaclacl,
                    acldefault(defaults.defaclobjtype, defaults.defaclrole)
                )
            ) AS a
            LEFT JOIN pg_roles AS grantee ON grantee.oid = a.grantee
            WHERE grantee.rolname = :app_role OR a.grantee = 0
            """
        ),
        {"app_role": app_role},
    ).first()
    if default_grants is not None:
        return False

    owned_objects = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_roles AS owner ON owner.oid = c.relowner
            WHERE n.nspname = 'public' AND owner.rolname = :app_role
            UNION ALL
            SELECT 1
            FROM pg_namespace AS n
            JOIN pg_roles AS owner ON owner.oid = n.nspowner
            WHERE n.nspname = 'public' AND owner.rolname = :app_role
            UNION ALL
            SELECT 1
            FROM pg_database AS database
            JOIN pg_roles AS owner ON owner.oid = database.datdba
            WHERE database.datname = :target_database
              AND owner.rolname = :app_role
            """
        ),
        {"app_role": app_role, "target_database": target_database},
    ).first()
    if owned_objects is not None:
        return False

    forbidden_table_privileges = connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN unnest(
                ARRAY['TRUNCATE', 'REFERENCES', 'TRIGGER']
            ) AS forbidden(privilege)
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND has_table_privilege(
                  CAST(:app_role AS name), c.oid, forbidden.privilege
              )
            """
        ),
        {"app_role": app_role},
    ).first()
    if forbidden_table_privileges is not None:
        return False

    sequence_privileges = connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'S'
              AND (
                  has_sequence_privilege(CAST(:app_role AS name), c.oid, 'USAGE')
                  OR has_sequence_privilege(CAST(:app_role AS name), c.oid, 'SELECT')
                  OR has_sequence_privilege(CAST(:app_role AS name), c.oid, 'UPDATE')
              )
            """
        ),
        {"app_role": app_role},
    ).first()
    return sequence_privileges is None


def _grant_and_verify_migration_privileges(profile: StrictRuntimeProfile) -> None:
    """Grant only direct runtime CRUD and verify the complete catalog boundary."""
    admin_url, target_database, app_role = _migration_database_identity(profile)
    engine: Any | None = None
    try:
        sqlalchemy = importlib.import_module("sqlalchemy")
        from idis.persistence.db import _ensure_psycopg_driver

        engine = sqlalchemy.create_engine(
            _ensure_psycopg_driver(admin_url.render_as_string(hide_password=False))
        )
        with engine.begin() as connection:
            table_names = tuple(
                str(value)
                for value in connection.execute(
                    sqlalchemy.text(
                        """
                        SELECT c.relname
                        FROM pg_class AS c
                        JOIN pg_namespace AS n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relkind IN ('r', 'p')
                          AND c.relname <> 'alembic_version'
                        ORDER BY c.relname
                        """
                    )
                ).scalars()
            )
            if not table_names:
                raise StrictLauncherError("DATABASE_SCHEMA_NOT_READY")
            preparer = engine.dialect.identifier_preparer
            quoted_role = preparer.quote(app_role)
            connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
            connection.exec_driver_sql(
                "REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                f"ON TABLE public.alembic_version FROM {quoted_role}"
            )
            connection.exec_driver_sql(
                "REVOKE SELECT (version_num), INSERT (version_num), "
                "UPDATE (version_num), REFERENCES (version_num) "
                f"ON TABLE public.alembic_version FROM {quoted_role}"
            )
            for table_name in table_names:
                quoted_table = preparer.quote(table_name)
                connection.exec_driver_sql(
                    "GRANT SELECT, INSERT, UPDATE, DELETE "
                    f"ON TABLE public.{quoted_table} TO {quoted_role}"
                )
            if not _migration_privilege_catalog_is_exact(
                connection,
                table_names=table_names,
                app_role=app_role,
                target_database=target_database,
                text=sqlalchemy.text,
            ):
                raise StrictLauncherError("DATABASE_SCHEMA_NOT_READY")
    except StrictLauncherError:
        raise
    except Exception:
        raise StrictLauncherError("DATABASE_SCHEMA_NOT_READY") from None
    finally:
        if engine is not None:
            engine.dispose()


def _run_migration(
    module: Any,
    args: argparse.Namespace,
    profile: StrictRuntimeProfile,
) -> int:
    """Apply the migration before writing its durable bootstrap identity."""
    try:
        require_runtime_profile(profile, command="migrate")
    except StrictProfileError as exc:
        raise StrictLauncherError(exc.code) from exc
    config_module = importlib.import_module("alembic.config")
    config_class = config_module.Config
    config = config_class(args.alembic_config)
    task4_fresh_migration = profile.generation == "bootstrap-v1" and args.upgrade == "0035"
    if task4_fresh_migration:
        _ensure_migration_database(profile)
    module.upgrade(config, args.upgrade)
    _grant_and_verify_migration_privileges(profile)
    _launch_audit_runner(profile)()
    return 0


def _run_forwarded(module: object, raw: Sequence[str]) -> int:
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise StrictLauncherError("STRICT_LAUNCH_TARGET_INVALID")
    result = entrypoint(_forwarded_arguments(raw))
    return int(result) if isinstance(result, int) else 0


def _run_governance(
    module: object,
    raw: Sequence[str],
    profile: StrictRuntimeProfile,
) -> int:
    """Pass the reviewed trust-root identity into the governance import CLI."""
    try:
        require_runtime_profile(profile, command="governance")
    except StrictProfileError as exc:
        raise StrictLauncherError(exc.code) from exc
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise StrictLauncherError("STRICT_LAUNCH_TARGET_INVALID")
    result = entrypoint(_forwarded_arguments(raw), strict_profile=profile)
    return int(result) if isinstance(result, int) else 0


def run_admitted_runtime_role(
    *,
    command: str,
    admission_runner: Callable[[], object],
    launch_audit_runner: Callable[[], None],
    runtime_runner: Callable[[], int],
) -> int:
    """Enter an API, worker, or gate target only after durable admission."""
    if command not in {"api", "worker", "gate"}:
        raise StrictLauncherError("STRICT_PROCESS_ROLE_INVALID")
    report = admission_runner()
    if getattr(report, "may_proceed", None) is not True:
        raise StrictLauncherError("STRICT_FULL_LIVE_BLOCKED")
    launch_audit_runner()
    return runtime_runner()


def _default_admission_runner(
    command: str,
    profile: StrictRuntimeProfile,
    *,
    task4_ingestion_scope: bool = False,
) -> Callable[[], object]:
    def run() -> object:
        probes = importlib.import_module("idis.runtime.dependency_probes")
        kwargs: dict[str, object] = {
            "env": profile.values,
            "expected_role": command,
            "worktree_root": Path.cwd(),
            "temp_root": Path(tempfile.gettempdir()).resolve(),
            "expected_public_key_sha256": profile.governance_public_key_sha256,
        }
        if task4_ingestion_scope:
            kwargs["admission_scope"] = probes.TASK4_INGESTION_ADMISSION_SCOPE
        return probes.run_default_durable_dependency_admission(
            **kwargs,
        )

    return run


def _is_task4_durable_gate_invocation(raw: Sequence[str]) -> bool:
    """Recognize only the two plan-pinned, provider-free Task-4 gate modes."""

    tokens = frozenset(raw)
    common = {
        "--api-base-url",
        "--api-key-env",
        "--canary-api-key-env",
        "--require-full-live",
        "--verify-tenant-isolation",
        "--company-mode",
        "--expected-company-count",
        "--expected-file-counts",
        "--expected-total-files",
        "--safe-report-dir",
        "--no-secret-output",
    }
    live_or_semantic = {
        "--live-preflight-only",
        "--run-llm-probes",
        "--run-embedding-graph-persistence-probes",
        "--run-ocr-media-inference-probes",
        "--run-all-provider-probes",
        "--evaluate-runtime-prompts",
        "--promote-approved-runtime-prompts",
        "--run-full",
    }
    initial = {
        "--root",
        "--prepare-data-rooms",
        "--preflight-only",
        "--prepare-ledger",
    }.issubset(tokens) and not {
        "--verify-only",
        "--verify-restart-survival",
        "--resume-ledger",
    }.intersection(tokens)
    restart = {
        "--root",
        "--verify-only",
        "--verify-restart-survival",
        "--resume-ledger",
    }.issubset(tokens) and not {
        "--prepare-data-rooms",
        "--preflight-only",
        "--prepare-ledger",
    }.intersection(tokens)
    return (
        common.issubset(tokens)
        and not live_or_semantic.intersection(tokens)
        and (initial != restart)
    )


def _launch_event(profile: StrictRuntimeProfile) -> dict[str, object]:
    tenant_id = profile.values["IDIS_ACCEPTANCE_TENANT_ID"]
    event_id = uuid.uuid4()
    return {
        "event_id": str(event_id),
        "tenant_id": tenant_id,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_type": "integration.strict_launch.admitted",
        "severity": "HIGH",
        "summary": "Strict launcher command admitted",
        "actor": {
            "actor_id": "strict-runtime-launcher",
            "actor_type": "SERVICE",
        },
        "request": {
            "request_id": f"strict-launch-{event_id}",
            "method": "POST",
            "path": "/internal/runtime/strict-launch",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "integration",
            "resource_id": f"strict-runtime:{profile.command}",
        },
        "payload": {
            "safe": launch_audit_fields(profile),
            "hashes": [],
            "refs": [],
        },
    }


def _row_value(row: object, name: str, index: int) -> object:
    if isinstance(row, dict):
        return row.get(name)
    if hasattr(row, name):
        return getattr(row, name)
    return row[index]  # type: ignore[index]


def persist_strict_launch_identity(
    profile: StrictRuntimeProfile,
    *,
    connection_factory: Callable[[], Any] | None = None,
    tenant_setter: Callable[[Any, str], None] | None = None,
    audit_sink: Any | None = None,
) -> None:
    """Atomically reject final-profile drift and append the canonical launch audit."""
    require_runtime_profile(profile, command=profile.command)
    if profile.command not in {"migrate", "governance", "gate", "api", "worker"}:
        raise StrictLauncherError("STRICT_PROCESS_ROLE_INVALID")

    try:
        if connection_factory is None or tenant_setter is None:
            db_module = importlib.import_module("idis.persistence.db")
            connection_factory = connection_factory or db_module.begin_app_conn
            tenant_setter = tenant_setter or db_module.set_tenant_local
        if audit_sink is None:
            audit_module = importlib.import_module("idis.audit.postgres_sink")
            audit_sink = audit_module.PostgresAuditSink()
        validator_module = importlib.import_module("idis.validators.audit_event_validator")
        text = importlib.import_module("sqlalchemy").text
        tenant_id = profile.values["IDIS_ACCEPTANCE_TENANT_ID"]
        event = _launch_event(profile)
        validation = validator_module.validate_audit_event(event)
        if getattr(validation, "passed", False) is not True:
            raise StrictLauncherError("AUDIT_EMIT_FAILED")

        lock_key = f"idis:strict-launch:{tenant_id}:{profile.generation}"
        with connection_factory() as connection:
            tenant_setter(connection, tenant_id)
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            if profile.generation == "acceptance-v1" and profile.command in {
                "api",
                "worker",
                "gate",
            }:
                rows = connection.execute(
                    text(
                        """
                        SELECT event->'payload'->'safe'->>'command' AS command,
                               event->'payload'->'safe'->>'generation' AS generation,
                               event->'payload'->'safe'->>'base_profile_hmac'
                                   AS base_profile_hmac
                        FROM audit_events
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND event_type = 'integration.strict_launch.admitted'
                          AND event->'payload'->'safe'->>'generation' = 'acceptance-v1'
                          AND event->'payload'->'safe'->>'command'
                              IN ('api', 'worker', 'gate')
                        """
                    ),
                    {"tenant_id": tenant_id},
                ).fetchall()
                for row in rows:
                    existing_hmac = str(_row_value(row, "base_profile_hmac", 2) or "")
                    if existing_hmac != profile.base_profile_hmac:
                        raise StrictLauncherError("STRICT_PROFILE_MISMATCH")
            emit_in_tx = getattr(audit_sink, "emit_in_tx", None)
            if not callable(emit_in_tx):
                raise StrictLauncherError("AUDIT_EMIT_FAILED")
            emit_in_tx(connection, event)
    except StrictLauncherError:
        raise
    except Exception:
        raise StrictLauncherError("AUDIT_EMIT_FAILED") from None


def _launch_audit_runner(profile: StrictRuntimeProfile) -> Callable[[], None]:
    def emit() -> None:
        persist_strict_launch_identity(profile)

    return emit


def _run_api(module: Any, args: argparse.Namespace, profile: StrictRuntimeProfile) -> int:
    def serve() -> int:
        write_pid_file(args.pid_file)
        try:
            uvicorn = importlib.import_module("uvicorn")
            app_factory = getattr(module, "create_app", None)
            if not callable(app_factory):
                raise StrictLauncherError("STRICT_LAUNCH_TARGET_INVALID")
            uvicorn.run(app_factory(strict_profile=profile), host=args.host, port=args.port)
            return 0
        finally:
            release_pid_file(args.pid_file)

    admission_runner = (
        _default_admission_runner("api", profile, task4_ingestion_scope=True)
        if profile.generation == "bootstrap-v1"
        else _default_admission_runner("api", profile)
    )
    return run_admitted_runtime_role(
        command="api",
        admission_runner=admission_runner,
        launch_audit_runner=_launch_audit_runner(profile),
        runtime_runner=serve,
    )


def _run_worker(module: Any, args: argparse.Namespace, profile: StrictRuntimeProfile) -> int:
    if args.poll_seconds <= 0:
        raise StrictLauncherError("WORKER_LEASE_CONFIG_INVALID")
    if profile.generation == "acceptance-v1":
        from idis.parsers.poppler_linux_runtime import PinnedLinuxPopplerRuntime
        from idis.parsers.poppler_runtime import (
            PopplerRuntimeBindingError,
            resolve_pinned_poppler_runtime,
        )

        try:
            governed_poppler = resolve_pinned_poppler_runtime()
        except PopplerRuntimeBindingError:
            governed_poppler = None
        if type(governed_poppler) is PinnedLinuxPopplerRuntime:
            raise StrictLauncherError("POPPLER_RUNTIME_NONCANONICAL")

    def poll() -> int:
        write_pid_file(args.pid_file)
        try:
            asyncio = importlib.import_module("asyncio")

            async def process() -> None:
                lease_config = module.load_worker_lease_config(env=profile.values, strict=True)
                tenant_ids = module.get_worker_tenant_ids(env=profile.values)
                if not tenant_ids:
                    raise StrictLauncherError("WORKER_TENANT_SCOPE_MISSING")
                worker = module.PipelineWorker(
                    poll_interval=args.poll_seconds if args.watch else 0,
                    tenant_ids=tenant_ids,
                    lease_config=lease_config,
                    strict_profile=profile,
                )
                while True:
                    processed = await worker._process_queued_runs()  # noqa: SLF001
                    if not args.watch and processed == 0:
                        return
                    if args.watch:
                        await asyncio.sleep(args.poll_seconds)

            asyncio.run(process())
            return 0
        finally:
            release_pid_file(args.pid_file)

    return run_admitted_runtime_role(
        command="worker",
        admission_runner=_default_admission_runner("worker", profile),
        launch_audit_runner=_launch_audit_runner(profile),
        runtime_runner=poll,
    )


def _run_gate(module: object, raw: Sequence[str], profile: StrictRuntimeProfile) -> int:
    """Admit and persist the reviewed gate identity before forwarded gate entry."""
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise StrictLauncherError("STRICT_LAUNCH_TARGET_INVALID")

    def run_gate() -> int:
        result = entrypoint(
            _forwarded_arguments(raw),
            strict_profile=profile,
        )
        return int(result) if isinstance(result, int) else 0

    task4_ingestion_scope = profile.generation == "bootstrap-v1" and (
        _is_task4_durable_gate_invocation(raw)
    )
    admission_runner = (
        _default_admission_runner("gate", profile, task4_ingestion_scope=True)
        if task4_ingestion_scope
        else _default_admission_runner("gate", profile)
    )
    return run_admitted_runtime_role(
        command="gate",
        admission_runner=admission_runner,
        launch_audit_runner=_launch_audit_runner(profile),
        runtime_runner=run_gate,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint; profile application always precedes target import."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(raw)
    except _SafeArgumentError:
        code = (
            "GOVERNANCE_SCHEMA_INVALID" if raw[:1] == ["governance"] else "STRICT_FULL_LIVE_BLOCKED"
        )
        print(
            json.dumps({"may_proceed": False, "code": code}, separators=(",", ":")),
            file=sys.stderr,
        )
        return 2
    profile, module = load_profile_then_import_runtime(command=args.command)
    if args.command == "migrate":
        return _run_migration(module, args, profile)
    if args.command == "governance":
        return _run_governance(module, raw, profile)
    if args.command == "gate":
        return _run_gate(module, raw, profile)
    if args.command == "api":
        return _run_api(module, args, profile)
    if args.command == "worker":
        return _run_worker(module, args, profile)
    raise StrictLauncherError("STRICT_PROCESS_ROLE_INVALID")


if __name__ == "__main__":  # pragma: no cover - exercised by production invocation
    raise SystemExit(main())


__all__ = [
    "StrictLauncherError",
    "build_parser",
    "launch_audit_fields",
    "load_profile_then_import_runtime",
    "main",
    "persist_strict_launch_identity",
    "run_admitted_runtime_role",
    "release_pid_file",
    "write_pid_file",
]
