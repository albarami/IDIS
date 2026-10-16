"""Offline CIR-03 regressions for driver selection and bootstrap diagnostics."""

from __future__ import annotations

import builtins
from collections.abc import Callable, Generator, Mapping, Sequence
from types import ModuleType
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from scripts import pg_bootstrap_ci
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import QueuePool

from idis.persistence import db


@pytest.fixture(autouse=True)
def _isolated_engines(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    for name in (
        db.IDIS_DATABASE_URL_ENV,
        db.IDIS_DATABASE_ADMIN_URL_ENV,
        db.IDIS_ERASURE_DATABASE_URL_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    db.reset_engines()
    yield
    db.reset_engines()


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_bare_postgres_url_selects_declared_driver_without_changing_content(scheme: str) -> None:
    suffix = "user:p%40ss@[::1]:5433/example?sslmode=require&application_name=offline%20test"
    assert db._ensure_psycopg_driver(f"{scheme}://{suffix}") == f"postgresql+psycopg2://{suffix}"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://user:example@localhost/example",
        "postgresql+psycopg://user:example@localhost/example",
        "postgresql+asyncpg://user:example@localhost/example",
        "sqlite:///:memory:",
    ],
)
def test_explicit_drivers_and_non_postgres_urls_are_preserved(url: str) -> None:
    assert db._ensure_psycopg_driver(url) == url


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
@pytest.mark.parametrize(
    ("env_name", "factory"),
    [
        (db.IDIS_DATABASE_URL_ENV, db.get_app_engine),
        (db.IDIS_DATABASE_ADMIN_URL_ENV, db.get_admin_engine),
        (db.IDIS_ERASURE_DATABASE_URL_ENV, db.get_erasure_engine),
    ],
)
def test_actual_engine_construction_uses_psycopg2_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    env_name: str,
    factory: Callable[[], Engine],
) -> None:
    monkeypatch.setenv(env_name, f"{scheme}://user:example@127.0.0.1:1/example")
    engine = factory()
    assert engine.dialect.driver == "psycopg2"
    assert engine.dialect.dbapi is not None
    assert engine.dialect.dbapi.__name__ == "psycopg2"
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("admin", [False, True])
def test_database_url_preserves_explicit_driver(
    monkeypatch: pytest.MonkeyPatch, admin: bool
) -> None:
    env_name = db.IDIS_DATABASE_ADMIN_URL_ENV if admin else db.IDIS_DATABASE_URL_ENV
    url = "postgresql+psycopg://user:example@127.0.0.1:1/example"
    monkeypatch.setenv(env_name, url)
    assert db.get_database_url(admin=admin) == url


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg2"])
def test_migration_config_uses_engine_driver_and_preserves_percent_encoding(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    suffix = "user:p%40ss@127.0.0.1:1/example?application_name=offline%20test"
    observed: list[str] = []

    def inspect_upgrade(config: Config, revision: str) -> None:
        url = config.get_main_option("sqlalchemy.url")
        assert url is not None
        assert revision == "head"
        observed.append(url)
        engine = create_engine(url)
        try:
            assert engine.dialect.driver == "psycopg2"
            assert engine.url.password == "p@ss"
            assert engine.url.query["application_name"] == "offline test"
            assert isinstance(engine.pool, QueuePool)
            assert engine.pool.checkedout() == 0
        finally:
            engine.dispose()

    monkeypatch.setattr(command, "upgrade", inspect_upgrade)
    pg_bootstrap_ci.run_migrations(f"{scheme}://{suffix}")
    assert observed == [f"postgresql+psycopg2://{suffix}"]


@pytest.mark.parametrize("scheme", ["postgresql+psycopg2", "postgresql+psycopg"])
def test_raw_psycopg2_connections_keep_their_separate_normalization(scheme: str) -> None:
    suffix = "user:p%40ss@127.0.0.1:1/example?application_name=offline%20test"
    assert pg_bootstrap_ci._normalize_url_for_psycopg2(f"{scheme}://{suffix}") == (
        f"postgresql://{suffix}"
    )


def test_missing_alembic_import_is_reported_without_exception_contents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_import = builtins.__import__

    def fail_alembic(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "alembic":
            raise ModuleNotFoundError("secret-bearing dependency diagnostic", name="alembic")
        return real_import(name, globals, locals, fromlist, level)

    with (
        patch("builtins.__import__", side_effect=fail_alembic),
        pytest.raises(SystemExit) as raised,
    ):
        pg_bootstrap_ci.run_migrations("postgresql://user:example@127.0.0.1:1/example")
    assert raised.value.code == 1
    assert capsys.readouterr().err == "ERROR: Alembic dependency import failed\n"


@pytest.mark.parametrize("missing_module", ["psycopg", "psycopg2", "migration_dependency"])
def test_upgrade_import_error_is_not_misreported_as_missing_alembic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_module: str,
) -> None:
    def fail_upgrade(config: Config, revision: str) -> None:
        raise ModuleNotFoundError(
            "postgresql://user:private@localhost/private", name=missing_module
        )

    monkeypatch.setattr(command, "upgrade", fail_upgrade)
    with pytest.raises(SystemExit) as raised:
        pg_bootstrap_ci.run_migrations("postgresql://user:example@127.0.0.1:1/example")
    assert raised.value.code == 1
    assert capsys.readouterr().err == "ERROR: Migration dependency import failed\n"


def test_migration_execution_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_upgrade(config: Config, revision: str) -> None:
        raise RuntimeError("postgresql://user:private@localhost/private confidential SQL")

    monkeypatch.setattr(command, "upgrade", fail_upgrade)
    with pytest.raises(SystemExit) as raised:
        pg_bootstrap_ci.run_migrations("postgresql://user:example@127.0.0.1:1/example")
    assert raised.value.code == 1
    assert capsys.readouterr().err == "ERROR: Migration failed\n"
