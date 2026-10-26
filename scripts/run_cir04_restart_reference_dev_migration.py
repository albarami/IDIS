"""Connection-bound development upgrade from 0038 to revision 0039.

The function reads no database URL from the environment and never calls
get_admin_engine. Production discovery stays on the package migration tree.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ProgrammingError

DEV0039_DATABASE_PREFIX = "idis_dev0039_"
DEV0039_RECEIPT_SCHEMA = "cir04-dev0039-restart-db-v1"
DEV0039_RECEIPT_REVISION = "0039-dev"
DEV0039_REVISION = "0039"
DEV0039_VERSIONS_DIR = (
    Path(__file__).resolve().parent / "dev_only" / "cir04_restart_reference" / "versions"
)
DEV0039_MIGRATION_SHA256 = "d1a111c0312d396e559ef5559f992ebe077163c8dee2afcd534e982b64d9fa63"

_FORBIDDEN_ENV = (
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
    "IDIS_DATABASE_URL",
)
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_MAINTENANCE_DATABASES = frozenset({"postgres", "template0", "template1", "idis_test"})
_PREVIEW_DATABASE = re.compile(r"idis_c3_callers_[1-9][0-9]*_[1-9][0-9]*_[0-9a-f]{16}\Z")
_ROLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_CURRENT_DATABASE_SQL = "SELECT current_database()"
_DATABASE_OID_SQL = "SELECT oid FROM pg_catalog.pg_database WHERE datname = :database"
_CURRENT_USER_SQL = "SELECT current_user"
_ALEMBIC_VERSION_SQL = "SELECT version_num FROM public.alembic_version ORDER BY version_num"


@dataclass(frozen=True)
class Dev0039SiblingProvenance:
    """Admin engine and in-memory receipt for context c3_sibling."""

    engine: Engine
    receipt: Mapping[str, object]


def upgrade_disposable_to_dev_0039(provenance: object) -> None:
    """Upgrade one validated disposable database from 0038 to development 0039.

    Args:
        provenance: Sibling receipt provenance, or the historical disposable database.

    Raises:
        RuntimeError: A fixed refusal code, with no other message text.
    """
    _refuse_forbidden_environment()
    if isinstance(provenance, Dev0039SiblingProvenance):
        _upgrade_sibling(provenance)
        return
    if type(provenance) is _disposable_type():
        _upgrade_historical(provenance)
        return
    raise RuntimeError("DEV0039_CONTEXT_INVALID")


def _refuse_forbidden_environment() -> None:
    if any(name in os.environ for name in _FORBIDDEN_ENV):
        raise RuntimeError("DEV0039_PREVIEW_KEY_FORBIDDEN")


def _disposable_type() -> type[object]:
    from tests.slice100_task2b._support import DisposablePostgres

    return DisposablePostgres


def _script_location() -> str:
    import idis.persistence.migrations as migrations_pkg

    return str(Path(migrations_pkg.__file__).resolve().parent)


def _upgrade_sibling(provenance: Dev0039SiblingProvenance) -> None:
    database = _require_sibling_database(provenance.engine.url.database)
    if provenance.engine.url.host not in _LOOPBACK:
        raise RuntimeError("DEV0039_NAME_INVALID")
    receipt = provenance.receipt
    try:
        schema = receipt["schema_version"]
        revision = receipt["revision"]
        receipt_database = receipt["database"]
        app_role = receipt["app_role"]
        database_oid = receipt["database_oid"]
    except KeyError:
        raise RuntimeError("DEV0039_RECEIPT_INVALID") from None
    if (
        schema != DEV0039_RECEIPT_SCHEMA
        or revision != DEV0039_RECEIPT_REVISION
        or receipt_database != database
        or not isinstance(app_role, str)
        or _ROLE_NAME.fullmatch(app_role) is None
    ):
        raise RuntimeError("DEV0039_RECEIPT_INVALID")
    with provenance.engine.begin() as connection:
        actual_database = connection.execute(text(_CURRENT_DATABASE_SQL)).scalar_one()
        if actual_database != database:
            raise RuntimeError("DEV0039_NAME_INVALID")
        actual_oid = connection.execute(
            text(_DATABASE_OID_SQL), {"database": database}
        ).scalar_one()
        try:
            oid_matches = int(str(actual_oid)) == int(str(database_oid))
        except (TypeError, ValueError):
            oid_matches = False
        if not oid_matches:
            raise RuntimeError("DEV0039_RECEIPT_INVALID")
        if str(connection.execute(text(_CURRENT_USER_SQL)).scalar_one()) == app_role:
            raise RuntimeError("DEV0039_RECEIPT_INVALID")
    _upgrade_bound(provenance.engine, app_role)


def _require_sibling_database(database: object) -> str:
    if (
        not isinstance(database, str)
        or not database.startswith(DEV0039_DATABASE_PREFIX)
        or database in _MAINTENANCE_DATABASES
        or _PREVIEW_DATABASE.fullmatch(database) is not None
    ):
        raise RuntimeError("DEV0039_NAME_INVALID")
    return database


def _upgrade_historical(provenance: object) -> None:
    database = getattr(provenance, "database_name", None)
    engine = getattr(provenance, "admin_engine", None)
    role = getattr(provenance, "role_name", None)
    if not isinstance(database, str) or not database.startswith("idis_t2b_db_"):
        raise RuntimeError("DEV0039_NAME_INVALID")
    if engine is None or engine.url.host not in _LOOPBACK:
        raise RuntimeError("DEV0039_NAME_INVALID")
    if not isinstance(role, str) or _ROLE_NAME.fullmatch(role) is None:
        raise RuntimeError("DEV0039_NAME_INVALID")
    with engine.begin() as connection:
        if connection.execute(text(_CURRENT_DATABASE_SQL)).scalar_one() != database:
            raise RuntimeError("DEV0039_NAME_INVALID")
    _upgrade_bound(engine, role)


def _require_migration_bytes() -> None:
    """Refuse unless the versions directory is the one reviewed file."""
    try:
        entries = list(DEV0039_VERSIONS_DIR.iterdir())
    except OSError:
        raise RuntimeError("DEV0039_REVISION_INVALID") from None
    if len(entries) != 1:
        raise RuntimeError("DEV0039_REVISION_INVALID")
    path = entries[0]
    if path.is_symlink() or not path.is_file() or path.suffix != ".py":
        raise RuntimeError("DEV0039_REVISION_INVALID")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise RuntimeError("DEV0039_REVISION_INVALID") from None
    if digest != DEV0039_MIGRATION_SHA256:
        raise RuntimeError("DEV0039_REVISION_INVALID")


def _upgrade_bound(engine: Engine, app_role: str) -> None:
    _require_migration_bytes()
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        script = _script_location()
        with engine.begin() as connection:
            config = Config()
            config.set_main_option("script_location", script)
            config.attributes["connection"] = connection
            command.upgrade(config, "0038")
        with engine.begin() as connection:
            if _version_rows(connection) != ["0038"]:
                raise RuntimeError("DEV0039_REVISION_INVALID")
            config = Config()
            config.set_main_option("script_location", script)
            config.set_main_option(
                "version_locations",
                f"{Path(script) / 'versions'} {DEV0039_VERSIONS_DIR}",
            )
            config.attributes["connection"] = connection
            config.attributes["dev0039_app_role"] = app_role
            command.upgrade(config, DEV0039_REVISION)
            if _version_rows(connection) != [DEV0039_REVISION]:
                raise RuntimeError("DEV0039_UPGRADE_INCOMPLETE")
    finally:
        sys.dont_write_bytecode = write_bytecode


def _version_rows(connection: object) -> list[str]:
    try:
        rows = connection.execute(text(_ALEMBIC_VERSION_SQL)).scalars()  # type: ignore[attr-defined]
    except ProgrammingError:
        raise RuntimeError("DEV0039_REVISION_INVALID") from None
    return [str(row) for row in rows]
