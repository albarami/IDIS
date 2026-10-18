"""Admission REDs for the development-only 0039 revision."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from scripts.check_migration_ceiling import (
    AUTHORIZED_HEAD,
    MigrationCeilingError,
    _literal_metadata,
    check_migration_ceiling,
)
from scripts.run_cir04_restart_reference_dev_migration import (
    DEV0039_DATABASE_PREFIX,
    DEV0039_MIGRATION_SHA256,
    DEV0039_RECEIPT_REVISION,
    DEV0039_RECEIPT_SCHEMA,
    DEV0039_VERSIONS_DIR,
    Dev0039SiblingProvenance,
    upgrade_disposable_to_dev_0039,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from tests.slice100_task2b._support import DisposablePostgres

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_VERSIONS = _ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
_PREIMAGE = _ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
# scripts/materialize_full_strict_v5_preimage.py:23 and :223-225
_ROW_SCHEMA_DOMAIN = b"full-strict-v4-authority-row-schema-sha256-v1\x00"
# scripts/materialize_full_strict_v12_preimage.py:27 and :579-583
_AUTHORITY_ROW_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_RECORDED_ROW_SCHEMA_SHA256 = "fe53467996dd3c3b7fe7ab888f857a01595ea2dd70e3b33e142f3833c6231ab0"
_AUTHORITY_SHA256_PREFIX = "d70799842e56404f"
_PREVIEW_SCHEMA = "cir04c3-required-frozen-preview-db-v1"
_FORBIDDEN = (
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
    "IDIS_DATABASE_URL",
)


class _Result:
    def __init__(self, value: Any, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = [value] if rows is None else rows

    def scalar_one(self) -> Any:
        return self._value

    def scalars(self) -> list[Any]:
        return self._rows


class _Connection:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.statements: list[str] = []

    def execute(self, statement: object, *_args: object, **_kwargs: object) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        lowered = sql.lower()
        if not lowered.startswith("select ") or any(
            token in lowered
            for token in ("insert ", "update ", "delete ", "create ", "alter ", "drop ")
        ):
            raise RuntimeError("DEV0039_UNEXPECTED_SQL")
        if "alembic_version" in lowered:
            return _Result(None, list(self.answers["versions"]))
        if " oid " in f" {lowered} " or lowered.startswith("select oid"):
            return _Result(self.answers["oid"])
        if "current_database" in lowered:
            return _Result(self.answers["database"])
        if "current_user" in lowered:
            return _Result(self.answers["user"])
        raise RuntimeError("DEV0039_UNEXPECTED_SQL")

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _Engine:
    def __init__(self, database: str, host: str, answers: dict[str, Any]) -> None:
        rendered_host = f"[{host}]" if ":" in host else host
        self.url = make_url(f"postgresql://role@{rendered_host}/{database}")
        self.connection = _Connection(answers)
        self.began = 0

    def begin(self) -> _Connection:
        self.began += 1
        return self.connection


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": DEV0039_RECEIPT_SCHEMA,
        "revision": DEV0039_RECEIPT_REVISION,
        "database": f"{DEV0039_DATABASE_PREFIX}abc",
        "database_oid": 42,
        "app_role": "idis_dev0039_app",
    }
    receipt.update(overrides)
    return receipt


def _engine(database: str = f"{DEV0039_DATABASE_PREFIX}abc", host: str = "127.0.0.1") -> _Engine:
    return _Engine(
        database,
        host,
        {"database": database, "oid": 42, "user": "admin_role", "versions": ["0038"]},
    )


def _clear_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FORBIDDEN:
        monkeypatch.delenv(name, raising=False)


def test_dev_directory_pins_one_literal_revision() -> None:
    files = sorted(path for path in DEV0039_VERSIONS_DIR.glob("*.py"))
    assert len(files) == 1
    assert not any(
        path.name == "__init__.py" for path in (_ROOT / "scripts" / "dev_only").rglob("*")
    )
    metadata = _literal_metadata(files[0])
    assert metadata == {
        "revision": "0039",
        "down_revision": "0038",
        "branch_labels": None,
        "depends_on": None,
    }
    digest = hashlib.sha256(files[0].read_bytes()).hexdigest()
    assert digest == DEV0039_MIGRATION_SHA256


def _get_alembic_config() -> Any:
    """Load the real config helper without env.py's Alembic runner."""
    path = _ROOT / "src" / "idis" / "persistence" / "migrations" / "env.py"
    source = path.read_text(encoding="utf-8").split("if context.is_offline_mode():", 1)[0]
    module = types.ModuleType("dev0039_env_config")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module.get_alembic_config()


def test_production_discovery_stays_on_0038() -> None:
    assert check_migration_ceiling(_PACKAGE_VERSIONS) == 38
    assert AUTHORIZED_HEAD == "0038"
    assert not any(_PACKAGE_VERSIONS.glob("0039*"))
    config = _get_alembic_config()
    assert config.get_main_option("version_locations") in {None, ""}
    assert ScriptDirectory.from_config(config).get_heads() == ["0038"]


def test_production_upgrade_to_0039_issues_no_sql() -> None:
    """Production script directory rejects 0039 before any database call."""
    config = _get_alembic_config()
    recorder = _Connection({})
    config.attributes["connection"] = recorder
    with pytest.raises(CommandError, match="0039"):
        if ScriptDirectory.from_config(config).get_revision("0039") is not None:
            command.upgrade(config, "0039")
    assert recorder.statements == []


def test_ceiling_rejects_injected_dev_revision(tmp_path: Path) -> None:
    copied = tmp_path / "versions"
    copied.mkdir()
    for path in _PACKAGE_VERSIONS.glob("*.py"):
        shutil.copyfile(path, copied / path.name)
    shutil.copyfile(next(DEV0039_VERSIONS_DIR.glob("*.py")), copied / "0039_injected.py")
    with pytest.raises(
        MigrationCeilingError, match="migration count is outside the authorized chain"
    ):
        check_migration_ceiling(copied)
    with pytest.raises(
        MigrationCeilingError, match="migration count is outside the authorized chain"
    ):
        check_migration_ceiling(DEV0039_VERSIONS_DIR)


def test_wheel_and_image_exclude_dev_revision() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/idis"]
    force_include = wheel["force-include"]
    assert all("scripts/dev_only" not in f"{key} {value}" for key, value in force_include.items())
    copy_lines = [
        line.strip()
        for line in (_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("COPY ")
    ]
    assert copy_lines
    assert all("scripts/dev_only" not in line and "0039" not in line for line in copy_lines)
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert sdist["exclude"] == ["scripts/dev_only/"]
    if importlib.util.find_spec("hatchling") is None:
        assert sdist["exclude"] == ["scripts/dev_only/"]
        return
    raise AssertionError("archive sdist check must be implemented when hatchling is importable")


@pytest.mark.parametrize(
    ("database", "host", "receipt", "answers", "code", "connects"),
    [
        ("other_db", "127.0.0.1", None, None, "DEV0039_NAME_INVALID", False),
        (
            "idis_c3_callers_1_2_0123456789abcdef",
            "127.0.0.1",
            None,
            None,
            "DEV0039_NAME_INVALID",
            False,
        ),
        ("postgres", "127.0.0.1", None, None, "DEV0039_NAME_INVALID", False),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "10.1.2.3",
            None,
            None,
            "DEV0039_NAME_INVALID",
            False,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "localhost",
            {"schema_version": _PREVIEW_SCHEMA},
            None,
            "DEV0039_RECEIPT_INVALID",
            False,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "127.0.0.1",
            {"revision": "0038"},
            None,
            "DEV0039_RECEIPT_INVALID",
            False,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "::1",
            {"database": "other"},
            None,
            "DEV0039_RECEIPT_INVALID",
            False,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "127.0.0.1",
            {},
            {"oid": 99},
            "DEV0039_RECEIPT_INVALID",
            True,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "127.0.0.1",
            {},
            {"database": "somewhere_else"},
            "DEV0039_NAME_INVALID",
            True,
        ),
        (
            f"{DEV0039_DATABASE_PREFIX}abc",
            "127.0.0.1",
            {},
            {"user": "idis_dev0039_app"},
            "DEV0039_RECEIPT_INVALID",
            True,
        ),
    ],
)
def test_sibling_rejections_do_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
    database: str,
    host: str,
    receipt: dict[str, object] | None,
    answers: dict[str, Any] | None,
    code: str,
    connects: bool,
) -> None:
    _clear_database_env(monkeypatch)
    engine = _engine(database, host)
    if answers:
        engine.connection.answers.update(answers)
    provenance = Dev0039SiblingProvenance(engine, _receipt(**(receipt or {})))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match=f"^{code}$"):
        upgrade_disposable_to_dev_0039(provenance)
    assert engine.began == (1 if connects else 0)
    assert engine.connection.statements or not connects


@pytest.mark.parametrize("name", _FORBIDDEN)
def test_forbidden_environment_is_refused_before_connection(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv(name, "present")
    engine = _engine()
    provenance = Dev0039SiblingProvenance(engine, _receipt())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="^DEV0039_PREVIEW_KEY_FORBIDDEN$"):
        upgrade_disposable_to_dev_0039(provenance)
    assert engine.began == 0


def test_bare_engine_and_tuple_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", "present")
    monkeypatch.setenv("IDIS_REQUIRE_POSTGRES", "1")
    bare = create_engine("sqlite://")
    with pytest.raises(RuntimeError, match="^DEV0039_CONTEXT_INVALID$"):
        upgrade_disposable_to_dev_0039(bare)
    with pytest.raises(RuntimeError, match="^DEV0039_CONTEXT_INVALID$"):
        upgrade_disposable_to_dev_0039((bare, _receipt()))


def test_historical_name_and_database_mismatches_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_database_env(monkeypatch)
    engine = _engine("idis_t2b_db_abc")
    wrong = DisposablePostgres(engine, engine, "not_t2b", "idis_t2b_app_abc")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="^DEV0039_NAME_INVALID$"):
        upgrade_disposable_to_dev_0039(wrong)
    assert engine.began == 0
    engine.connection.answers["database"] = "other"
    drifted = DisposablePostgres(engine, engine, "idis_t2b_db_abc", "idis_t2b_app_abc")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="^DEV0039_NAME_INVALID$"):
        upgrade_disposable_to_dev_0039(drifted)
    assert all(sql.lower().startswith("select ") for sql in engine.connection.statements)


def _maintenance_state(url: str) -> object:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            try:
                return list(
                    connection.execute(
                        text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
                    ).scalars()
                )
            except ProgrammingError:
                return "no table"
    finally:
        engine.dispose()


def test_maintenance_database_revision_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.slice100_task2b._support import disposable_postgres, migrate
    from tests.slice100_task7._postgres_support import provision_exact_erasure_principals

    admin = os.environ.get("IDIS_DATABASE_ADMIN_URL")
    if not admin:
        if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
            pytest.fail("REQUIRED: IDIS_DATABASE_ADMIN_URL (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip("IDIS_DATABASE_ADMIN_URL is not configured")
    maintenance = make_url(admin).set(database="postgres").render_as_string(hide_password=False)
    monkeypatch.setenv("IDIS_DATABASE_ADMIN_URL", maintenance)
    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    for name in _FORBIDDEN[:3]:
        monkeypatch.delenv(name, raising=False)
    before = _maintenance_state(maintenance)
    with disposable_postgres() as database:
        with database.admin_engine.begin() as connection:
            provision_exact_erasure_principals(connection)
            _ensure_guard_owner(connection)
        migrate(database.admin_engine, "0038")
        assert "IDIS_DATABASE_URL" not in os.environ
        upgrade_disposable_to_dev_0039(database)
        with database.admin_engine.connect() as connection:
            assert list(
                connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars()
            ) == ["0039"]
    assert _maintenance_state(maintenance) == before


def _ensure_guard_owner(connection: Any) -> None:
    connection.exec_driver_sql(
        """
        DO $task7$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'idis_task7_guard_function_owner'
          ) THEN
            CREATE ROLE idis_task7_guard_function_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $task7$;
        """
    )


def test_revision_metadata_assignments_are_plain_literals() -> None:
    source = next(DEV0039_VERSIONS_DIR.glob("*.py")).read_text(encoding="utf-8")
    module = ast.parse(source)
    assigned: dict[str, object] = {}
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            assigned[node.targets[0].id] = node.value.value
    assert assigned["revision"] == "0039"
    assert assigned["down_revision"] == "0038"
    assert assigned["branch_labels"] is None
    assert assigned["depends_on"] is None


def _dev0039_migration() -> Any:
    """Load the development revision without writing bytecode beside it."""
    path = next(DEV0039_VERSIONS_DIR.glob("*.py"))
    spec = importlib.util.spec_from_file_location("dev0039_revision", path)
    if spec is None or spec.loader is None:
        raise AssertionError("DEV0039_REVISION_INVALID")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _canonical_row_bytes(value: object) -> bytes:
    """Match scripts/materialize_full_strict_v5_preimage.py:197-204.

    The authority rehash uses the same rendering at
    scripts/materialize_full_strict_v12_preimage.py:336-343.
    """
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _domain_sha256(domain: bytes, row: dict[str, Any], hash_field: str) -> str:
    payload = {key: value for key, value in row.items() if key != hash_field}
    return hashlib.sha256(domain + _canonical_row_bytes(payload)).hexdigest()


def _reviewed_sections() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = json.loads(_PREIMAGE.read_bytes().decode("utf-8"))
    row = payload["future_reference_authority_registry"]["rows"][14]
    authority = next(
        item
        for item in payload["sql_authority_registry"]["rows"]
        if item["authority_id"] == "approved-erasure-append-only-v1"
    )
    acl = next(
        item
        for item in payload["sql_authority_registry"]["guard_function_acl_registry"][
            "function_rows"
        ]
        if item["function_signature"] == "public.idis_task7_approved_erasure_append_only_v1()"
    )
    return row, authority, acl


def _decoded_hex_tuple(values: list[str]) -> list[str]:
    return [bytes.fromhex(value).decode("utf-8") for value in values]


def test_preimage_file_sha256_matches_migration_pin() -> None:
    module = _dev0039_migration()
    digest = hashlib.sha256(_PREIMAGE.read_bytes()).hexdigest()
    assert digest == module._PREIMAGE_SHA256
    assert digest == "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"


def test_row_14_schema_sha256_matches_producer() -> None:
    module = _dev0039_migration()
    row, _authority, _acl = _reviewed_sections()
    recomputed = _domain_sha256(_ROW_SCHEMA_DOMAIN, row, "schema_sha256")
    assert recomputed == module._ROW_SCHEMA_SHA256
    assert recomputed == row["schema_sha256"]
    assert recomputed == _RECORDED_ROW_SCHEMA_SHA256


def test_authority_row_hash_matches_producer() -> None:
    module = _dev0039_migration()
    _row, authority, _acl = _reviewed_sections()
    recomputed = _domain_sha256(_AUTHORITY_ROW_DOMAIN, authority, "expected_sha256")
    assert recomputed == module._AUTHORITY_SHA256
    assert recomputed == authority["expected_sha256"]
    assert recomputed.startswith(_AUTHORITY_SHA256_PREFIX)


def test_executed_guard_sql_matches_reviewed_bodies() -> None:
    module = _dev0039_migration()
    _row, authority, acl = _reviewed_sections()
    bodies = _decoded_hex_tuple(authority["body_sql_utf8_hex_tuple"])
    assert bodies[0] == module._FUNCTION_SQL
    assert bodies[33] == module._TRIGGER_SQL
    assert bodies[39] == module._OWNER_SQL
    assert bodies[40] == module._REVOKE_SQL
    assert tuple(_decoded_hex_tuple(acl["acl_sql_utf8_hex_tuple"])) == tuple(module._ACL_SQL)


def test_row_14_tuples_match_migration_constants() -> None:
    module = _dev0039_migration()
    row, _authority, _acl = _reviewed_sections()
    assert row["column_tuple"] == module._COLUMN_TUPLE
    assert row["reference_kind_tuple"] == module._KIND_TUPLE
    assert row["check_tuple"] == module._CHECK_TUPLE
    assert row["unique_tuple"] == module._UNIQUE_TUPLE
    assert row["foreign_key_tuple"] == module._FOREIGN_KEY_TUPLE
    assert row["index_tuple"] == module._INDEX_TUPLE
    assert row["rls_tuple"] == module._RLS_TUPLE


def _versions_copy(tmp_path: Path, mutated: bool) -> Path:
    directory = tmp_path / "versions"
    directory.mkdir()
    source = next(DEV0039_VERSIONS_DIR.glob("*.py")).read_bytes()
    if mutated:
        source = source[:-1] + bytes([source[-1] ^ 0x01])
    (directory / "0039_task7_restart_reference_authority.py").write_bytes(source)
    return directory


def _upgrade_calls(monkeypatch: pytest.MonkeyPatch, directory: Path | None) -> list[str]:
    import scripts.run_cir04_restart_reference_dev_migration as driver

    _clear_database_env(monkeypatch)
    if directory is not None:
        monkeypatch.setattr(driver, "DEV0039_VERSIONS_DIR", directory)
    calls: list[str] = []

    def _reached(_config: object, revision: str) -> None:
        calls.append(revision)
        raise RuntimeError("REACHED_UPGRADE")

    monkeypatch.setattr(driver.command, "upgrade", _reached)
    provenance = Dev0039SiblingProvenance(_engine(), _receipt())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as caught:
        upgrade_disposable_to_dev_0039(provenance)
    if str(caught.value) == "DEV0039_REVISION_INVALID":
        assert calls == []
        return []
    assert str(caught.value) == "REACHED_UPGRADE"
    return calls


def test_modified_migration_file_is_refused_before_upgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _upgrade_calls(monkeypatch, _versions_copy(tmp_path, mutated=True))
    assert calls == []


def test_real_migration_file_reaches_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _upgrade_calls(monkeypatch, None)
    assert calls == ["0038"]


def test_versions_directory_with_two_files_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _versions_copy(tmp_path, mutated=False)
    (directory / "extra.py").write_text("revision = '0039'\n", encoding="utf-8")
    assert _upgrade_calls(monkeypatch, directory) == []


def test_versions_subdirectory_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = _versions_copy(tmp_path, mutated=False)
    (directory / "nested").mkdir()
    assert _upgrade_calls(monkeypatch, directory) == []
