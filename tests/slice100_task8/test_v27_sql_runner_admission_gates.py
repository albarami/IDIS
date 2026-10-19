"""Bounded admission gates of the hardened v27 SQL census runner.

These checks call the runner directly. They do not open PostgreSQL, load an
operator profile, or read a signing key.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_RUNNER = REPO_ROOT / "scripts" / "run_task8_v27_sql_authority_census.py"

_LEGAL_IDENTIFIER = "idis_task8_census_ab12"
_REJECTED_IDENTIFIERS = (
    "",
    "1bad",
    "has space",
    'has"quote',
    "has;semi",
    "has-dash",
    "a" * 64,
)


def _load() -> ModuleType:
    name = "_task8_v27_sql_runner_admission_gates"
    spec = importlib.util.spec_from_file_location(name, SQL_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v27_sql_runner_quotes_only_a_closed_identifier() -> None:
    runner = _load()
    assert runner._quoted_identifier(_LEGAL_IDENTIFIER) == f'"{_LEGAL_IDENTIFIER}"'
    for rejected in _REJECTED_IDENTIFIERS:
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_ROLE_INVALID$",
        ):
            runner._quoted_identifier(rejected)


def _minimal_v27(runner: ModuleType) -> dict[str, Any]:
    return {
        "metadata": {
            "artifact_schema": "full-strict-v27-reviewed-preimage-v1",
            "pipeline_contract_version": "full-strict-v27",
            "append_frozen": True,
            "activation_status": "INACTIVE",
        },
        "task8_fdd_contract": {
            "dashboard_contract": {"authority_id": runner._AUTHORITY_IDS[0]},
            "partial_exact_one_contract": {"authority_id": runner._AUTHORITY_IDS[1]},
            "erasure_refusal_contract": {
                "reason_code": runner._REFUSAL_CODE,
                "sqlstate": runner._REFUSAL_SQLSTATE,
            },
        },
        "sql_authority_registry": {
            "rows": [
                {"authority_id": runner._AUTHORITY_IDS[0]},
                {"authority_id": runner._AUTHORITY_IDS[1]},
            ]
        },
    }


def test_v27_sql_runner_refuses_unclosed_role_before_grant_or_scaffold_sql() -> None:
    runner = _load()
    artifact = _minimal_v27(runner)
    for rejected in ('app";DROP', "app role"):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_ROLE_INVALID$",
        ):
            runner.build_application_behavior_grants(artifact, rejected)
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_ROLE_INVALID$",
        ):
            runner.build_future_table_scaffold(artifact, rejected)


def test_v27_sql_runner_database_urls_stay_on_postgres_and_drop_is_parameterised() -> None:
    runner = _load()
    admin = "postgresql://admin_role@127.0.0.1/postgres"
    application = "postgresql://app_role@127.0.0.1/postgres"
    erasure = "postgresql://erasure_role@127.0.0.1/postgres"
    urls = runner._disposable_urls(admin, application, erasure)
    assert runner._quoted_identifier(urls.database_name) == f'"{urls.database_name}"'
    assert urls.application_role == "app_role"
    assert urls.erasure_role == "erasure_role"
    assert urls.admin.endswith("/" + urls.database_name)
    assert runner._replace_database("postgres://admin_role@127.0.0.1/postgres", "kept") == (
        "postgres://admin_role@127.0.0.1/kept"
    )
    for invalid in (
        "mysql://admin_role@127.0.0.1/postgres",
        "postgresql:///postgres",
    ):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_DATABASE_URL_INVALID$",
        ):
            runner._replace_database(invalid, _LEGAL_IDENTIFIER)
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_DATABASE_URL_INVALID$",
    ):
        runner._role_from_url("postgresql://127.0.0.1/postgres")
    drop_source = inspect.getsource(runner._drop_disposable_database)
    create_source = inspect.getsource(runner._create_disposable_database)
    assert "datname = %s" in drop_source
    assert "(database_name,)" in drop_source
    assert "_quoted_identifier(database_name)" in drop_source
    assert "_quoted_identifier(database_name)" in create_source


class _RecordedCursor:
    def __init__(self, statements: list[tuple[str, object]]) -> None:
        self._statements = statements

    def execute(self, statement: object, parameters: object = None) -> None:
        self._statements.append((str(statement), parameters))

    def __enter__(self) -> _RecordedCursor:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


class _RecordedConnection:
    def __init__(self, statements: list[tuple[str, object]]) -> None:
        self._statements = statements

    def cursor(self, *_args: object, **_kwargs: object) -> _RecordedCursor:
        return _RecordedCursor(self._statements)

    def close(self) -> None:
        return None


def test_v27_create_and_drop_bind_terminate_and_quote_closed_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load()
    statements: list[tuple[str, object]] = []

    def connect_raw(database_url: str) -> _RecordedConnection:
        assert database_url == "postgresql://admin_role@127.0.0.1/postgres"
        return _RecordedConnection(statements)

    monkeypatch.setattr(runner, "_connect_raw", connect_raw)
    admin = "postgresql://admin_role@127.0.0.1/postgres"
    name = _LEGAL_IDENTIFIER
    quoted = f'"{name}"'
    runner._create_disposable_database(admin, name)
    runner._drop_disposable_database(admin, name)
    assert len(statements) == 3
    create_sql, create_parameters = statements[0]
    terminate_sql, terminate_parameters = statements[1]
    drop_sql, drop_parameters = statements[2]
    assert create_sql == f"CREATE DATABASE {quoted} TEMPLATE template0"
    assert create_parameters is None
    assert "datname = %s" in terminate_sql
    assert terminate_parameters == (name,)
    assert drop_sql == f"DROP DATABASE IF EXISTS {quoted}"
    assert drop_parameters is None

    for rejected in _REJECTED_IDENTIFIERS:
        statements.clear()
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_ROLE_INVALID$",
        ):
            runner._create_disposable_database(admin, rejected)
        assert statements == []
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_ROLE_INVALID$",
        ):
            runner._drop_disposable_database(admin, rejected)
        assert len(statements) == 1
        rejected_sql, rejected_parameters = statements[0]
        assert rejected_sql == terminate_sql
        assert "DROP DATABASE" not in rejected_sql
        assert "CREATE DATABASE" not in rejected_sql
        assert "datname = %s" in rejected_sql
        assert rejected_parameters == (rejected,)


def test_v27_fk_observation_refuses_an_unclosed_catalog_identifier_before_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load()
    artifact = _minimal_v27(runner)
    artifact["future_native_authority_registry"] = {
        "rows": [
            {
                "planned_table": "methodology_truth_dashboard_rows",
                "schema_sha256": "b69c13dfc8a41eee6b403116b1bc971b917905705e4c5a4a457b8b4bf1f7f221",
            }
        ]
    }
    artifact["future_child_authority_registry"] = {
        "rows": [
            {
                "table_name": "methodology_truth_dashboard_root_links",
                "schema_sha256": "f33c3a3172bd66c9dedd354c2aedc6b735bd0b4edcbcc468d7211f41179929f8",
            }
        ]
    }
    manifest = runner.build_seed_fk_closure_manifest(artifact)
    executed: list[str] = []

    class Cursor:
        def __init__(self) -> None:
            self._fetchall_calls = 0

        def execute(self, statement: object, _parameters: object = None) -> None:
            executed.append(str(statement))

        def fetchone(self) -> tuple[object, ...]:
            return ("app_role", "app_role", False, False, "origin")

        def fetchall(self) -> list[tuple[object, ...]]:
            self._fetchall_calls += 1
            if self._fetchall_calls == 1:
                return []
            return [
                (
                    "public",
                    "bad-name",
                    "fk_bad",
                    ["tenant_id"],
                    "public",
                    "tenants",
                    ["tenant_id"],
                    True,
                    False,
                    False,
                    "NO ACTION",
                    "NO ACTION",
                    "SIMPLE",
                )
            ]

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(runner, "_connect", lambda _url: Connection())
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_SEED_FK_CLOSURE_INVALID$",
    ):
        runner._observe_seed_fk_closure(
            "postgresql://app_role@127.0.0.1/postgres",
            expected_application_role="app_role",
            tenant_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            deal_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            closure_manifest=manifest,
        )
    assert not any("bad-name" in statement for statement in executed)
    assert any("relname=ANY(%s::text[])" in statement for statement in executed)


def test_v27_admin_authority_ddl_refuses_statements_outside_the_prefix_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("connect")

    monkeypatch.setattr(runner, "_connect_raw", forbidden)
    for statements in (
        (),
        ("DROP TABLE public.tenants",),
        ("CREATE TABLE public.tenants(id int)",),
        ("SELECT 1",),
    ):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_AUTHORITY_SQL_INVALID$",
        ):
            runner._apply_admin_authority_ddl(
                "postgresql://admin_role@127.0.0.1/postgres",
                statements,
            )
