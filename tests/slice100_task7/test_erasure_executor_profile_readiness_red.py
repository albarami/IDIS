"""RED-first strict-profile and readiness contracts for the Task-7 erasure executor."""

from __future__ import annotations

import importlib
import json
import re
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task2.test_strict_profile_launcher import (
    _process_env,
    _rewrite_integrity_file,
    _write_dotenv,
)

_ERASURE_URL = "postgresql+psycopg2://idis_erasure_executor:secret@db/idis"
_APP_TARGET = {
    "database_name": "idis",
    "server_address": "127.0.0.1",
    "server_port": 15432,
    "authenticated_role": "idis_app",
    "effective_role": "idis_app",
}
_EXECUTOR_TARGET = {
    "database_name": "idis",
    "server_address": "127.0.0.1",
    "server_port": 15432,
    "authenticated_role": "idis_erasure_executor",
    "effective_role": "idis_erasure_executor",
}
_ERASURE_OWNER_UPDATE_GRANT_RE = re.compile(
    r"^GRANT UPDATE \((?P<columns>[a-z0-9_,]+)\) "
    r"ON TABLE public\.erasure_requests TO idis_erasure_function_owner$"
)
_ERASURE_OWNER_UPDATE_COLUMNS = (
    "request_id",
    "status",
    "executed_by",
    "executed_at",
    "counts",
)


def _guard_acl_catalog_rows() -> list[dict[str, object]]:
    artifact_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "full_strict_v21_reviewed_preimage_v1.json"
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    owner = artifact["sql_authority_registry"]["guard_function_acl_registry"]["guard_owner_role"]
    return [
        {
            "relation_name": relation,
            "column_name": column,
            "privilege_type": "SELECT",
            "is_grantable": False,
        }
        for relation, columns in owner["select_relation_column_tuple"]
        for column in columns
    ]


def _guard_routine_postures() -> tuple[tuple[str, bool, tuple[str, ...]], ...]:
    artifact_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "full_strict_v21_reviewed_preimage_v1.json"
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    return tuple(
        sorted(
            (
                row["function_signature"],
                row["security_mode"] == "DEFINER",
                tuple(row["search_path_tuple"]),
            )
            for row in rows
            if row["install_scope"] == "CURRENT_0038"
        )
    )


def _frozen_v21_erasure_owner_update_columns() -> tuple[str, ...]:
    artifact_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "full_strict_v21_reviewed_preimage_v1.json"
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    rows = artifact["erasure_execution_registry"]["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    statements = tuple(
        bytes.fromhex(value).decode("utf-8") for value in rows[0]["current_acl_sql_utf8_hex_tuple"]
    )
    matches = tuple(
        match
        for statement in statements
        if (match := _ERASURE_OWNER_UPDATE_GRANT_RE.fullmatch(statement)) is not None
    )
    assert len(matches) == 1
    return tuple(matches[0].group("columns").split(","))


def _erasure_owner_update_acl_facts(
    module: ModuleType,
    *,
    columns: tuple[str, ...] = _ERASURE_OWNER_UPDATE_COLUMNS,
    grantable_acl_count: int = 0,
    table_update_privilege_count: int = 0,
) -> Any:
    return module.ErasureFunctionOwnerUpdateAclFacts(
        relation_tuple=("erasure_requests",) if columns else (),
        column_update_acl_count=len(columns),
        ordered_column_tuple=columns,
        grantable_acl_count=grantable_acl_count,
        table_update_privilege_count=table_update_privilege_count,
    )


def _named_catalog_arguments(argument_types: str) -> str:
    if not argument_types:
        return ""
    return ", ".join(
        f"p_arg_{ordinal} {argument_type.strip()}"
        for ordinal, argument_type in enumerate(argument_types.split(","), start=1)
    )


def _readiness_module() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _probes_module() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_probes")


def _ready_facts(module: ModuleType) -> Any:
    readiness = _readiness_module()
    target = module.ErasureDatabaseTargetFacts(
        database_name="idis",
        server_address="127.0.0.1",
        server_port=15432,
    )
    return module.ErasureExecutorProbeFacts(
        connected=True,
        authenticated_role="idis_erasure_executor",
        effective_role="idis_erasure_executor",
        executor_target=target,
        app_target=target,
        app_role="idis_app",
        app_effective_role="idis_app",
        executor_role=module.ErasureRoleFacts(
            role_name="idis_erasure_executor",
            can_login=True,
            superuser=False,
            create_database=False,
            create_role=False,
            inherit=False,
            bypass_rls=False,
            replication=False,
        ),
        owner_role=module.ErasureRoleFacts(
            role_name="idis_erasure_function_owner",
            can_login=False,
            superuser=False,
            create_database=False,
            create_role=False,
            inherit=False,
            bypass_rls=False,
            replication=False,
        ),
        membership_count=0,
        executor_table_privilege_count=0,
        executor_column_privilege_count=0,
        executor_schema_usage=True,
        executor_schema_create=False,
        executor_schema_direct_acl_count=1,
        executor_other_direct_execute_count=0,
        function_count=1,
        function_schema="public",
        function_name="idis_task7_execute_approved_erasure",
        function_identity_arguments=("uuid", "uuid", "uuid"),
        function_result_type="jsonb",
        function_owner="idis_erasure_function_owner",
        function_security_definer=True,
        function_search_path=("pg_catalog", "pg_temp"),
        executor_has_execute=True,
        public_has_execute=False,
        app_role_has_execute=False,
        unexpected_execute_grant_count=0,
        function_owner_update_acl=_erasure_owner_update_acl_facts(module),
        guard_owner=module.Task7GuardOwnerProbeFacts(
            role=module.ErasureRoleFacts(
                role_name="idis_task7_guard_function_owner",
                can_login=False,
                superuser=False,
                create_database=False,
                create_role=False,
                inherit=False,
                bypass_rls=False,
                replication=False,
            ),
            membership_count=0,
            owned_relation_count=0,
            owned_routine_count=24,
            schema_usage=True,
            schema_create=False,
            schema_direct_usage_acl_count=1,
            schema_unexpected_acl_count=0,
            table_select_privilege_count=0,
            table_insert_privilege_count=0,
            table_update_privilege_count=0,
            table_delete_privilege_count=0,
            table_truncate_privilege_count=0,
            table_references_privilege_count=0,
            table_trigger_privilege_count=0,
            column_select_acl_relation_count=36,
            column_select_acl_count=424,
            column_select_acl_sha256=(
                "0e15aa84b19ba4f63f95001049702b54279f9d1ffebf984394979c602b271da6"
            ),
            force_rls_relation_count=36,
            current_routine_signatures=(readiness.TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES),
            current_routine_posture_mismatch_count=0,
            application_direct_execute_count=0,
            public_direct_execute_count=0,
            unexpected_execute_grant_count=0,
            external_helper_count=1,
            external_helper_signature=("public.idis_task7_approved_erasure_context_v1(uuid,uuid)"),
            external_helper_owner="idis_erasure_function_owner",
            external_helper_security_definer=True,
            external_helper_search_path=("pg_catalog", "pg_temp"),
            external_helper_guard_owner_execute=True,
            external_helper_application_execute=False,
            external_helper_public_execute=False,
            external_helper_unexpected_execute_grant_count=0,
            future_routine_signatures_present=(),
        ),
        runner_contract=module.Task7ErasureRunnerProbeFacts(
            activation_state="CURRENT48_ACTIVE_FUTURE38_FROZEN",
            delete_relation_tuple=module.TASK7_V21_CURRENT_ERASURE_RELATIONS,
            result_relation_tuple=module.TASK7_V21_CURRENT_ERASURE_RELATIONS,
            future_relation_tuple_present=(),
        ),
    )


@pytest.mark.parametrize("command", ["migrate", "api", "gate"])
def test_erasure_database_url_is_required_and_retained_only_by_authorized_commands(
    tmp_path: Path,
    command: str,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    dotenv_path, values = _write_dotenv(
        tmp_path,
        additions={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
    )
    process_env = _process_env(dotenv_path)

    profile = profile_module.load_strict_runtime_profile(
        command=command,
        process_env=process_env,
    )

    assert profile.values["IDIS_ERASURE_DATABASE_URL"] == _ERASURE_URL
    assert process_env["IDIS_ERASURE_DATABASE_URL"] == _ERASURE_URL

    values.pop("IDIS_ERASURE_DATABASE_URL")
    _rewrite_integrity_file(
        dotenv_path,
        "\n".join(f"{name}={value}" for name, value in sorted(values.items())) + "\n",
    )
    with pytest.raises(
        profile_module.StrictProfileError,
        match="ERASURE_EXECUTOR_CONFIG_BLOCKED",
    ):
        profile_module.load_strict_runtime_profile(
            command=command,
            process_env=_process_env(dotenv_path),
        )


@pytest.mark.parametrize("command", ["worker", "governance"])
def test_erasure_database_url_is_stripped_from_non_authorized_commands(
    tmp_path: Path,
    command: str,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    dotenv_path, _ = _write_dotenv(
        tmp_path,
        additions={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
    )
    process_env = _process_env(dotenv_path)

    profile = profile_module.load_strict_runtime_profile(
        command=command,
        process_env=process_env,
    )

    assert "IDIS_ERASURE_DATABASE_URL" not in profile.values
    assert "IDIS_ERASURE_DATABASE_URL" not in process_env


def test_erasure_database_url_is_hash_bound_and_in_secret_safe_inventory(
    tmp_path: Path,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    strict_full_live = importlib.import_module("idis.services.runs.strict_full_live")
    first_path, _ = _write_dotenv(
        tmp_path / "first",
        additions={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
    )
    second_path, _ = _write_dotenv(
        tmp_path / "second",
        additions={
            "IDIS_ERASURE_DATABASE_URL": (
                "postgresql+psycopg2://idis_erasure_executor:other@db/idis"
            )
        },
    )

    first = profile_module.load_strict_runtime_profile(
        command="api",
        process_env=_process_env(first_path),
    )
    second = profile_module.load_strict_runtime_profile(
        command="api",
        process_env=_process_env(second_path),
    )

    assert first.base_profile_hmac != second.base_profile_hmac
    assert "IDIS_ERASURE_DATABASE_URL" in strict_full_live.TRACKED_ENV_VARS
    assert "IDIS_ERASURE_DATABASE_URL" in strict_full_live.STRICT_ENV_EXAMPLE_REQUIRED_NAMES


def test_erasure_readiness_accepts_only_the_reviewed_exact_contract() -> None:
    module = _readiness_module()

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: _ready_facts(module),
    )

    assert result == module.ReadinessCheck(
        component="erasure_executor",
        may_proceed=True,
        code=None,
    )


def test_v21_erasure_owner_update_acl_matches_frozen_authority_and_four_fails_closed() -> None:
    module = _readiness_module()
    assert _frozen_v21_erasure_owner_update_columns() == _ERASURE_OWNER_UPDATE_COLUMNS
    valid = _ready_facts(module)

    assert valid.function_owner_update_acl.column_update_acl_count == 5
    assert valid.function_owner_update_acl.ordered_column_tuple == (
        "request_id",
        "status",
        "executed_by",
        "executed_at",
        "counts",
    )

    four_columns = _ERASURE_OWNER_UPDATE_COLUMNS[:-1]
    invalid = replace(
        valid,
        function_owner_update_acl=_erasure_owner_update_acl_facts(
            module,
            columns=four_columns,
        ),
    )
    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: invalid,
    )

    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_v21_erasure_owner_update_acl_rejects_same_count_wrong_column() -> None:
    module = _readiness_module()
    wrong_columns = (*_ERASURE_OWNER_UPDATE_COLUMNS[:-1], "requested_by")
    invalid = replace(
        _ready_facts(module),
        function_owner_update_acl=_erasure_owner_update_acl_facts(
            module,
            columns=wrong_columns,
        ),
    )

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: invalid,
    )

    assert invalid.function_owner_update_acl.column_update_acl_count == 5
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    "owner_acl",
    [
        {"grantable_acl_count": 1},
        {"table_update_privilege_count": 1},
    ],
)
def test_v21_erasure_owner_update_acl_rejects_broader_update_authority(
    owner_acl: dict[str, int],
) -> None:
    module = _readiness_module()
    invalid = replace(
        _ready_facts(module),
        function_owner_update_acl=_erasure_owner_update_acl_facts(
            module,
            **owner_acl,
        ),
    )

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: invalid,
    )

    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    "configured",
    ["", "sqlite:///private.db", "redis://localhost/0"],
)
def test_erasure_readiness_rejects_missing_or_non_postgres_configuration(
    configured: str,
) -> None:
    module = _readiness_module()
    calls: list[str] = []

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": configured},
        expected_role="api",
        probe=lambda: calls.append("called"),
    )

    assert result.code == "ERASURE_EXECUTOR_CONFIG_BLOCKED"
    assert calls == []


def test_erasure_readiness_rejects_database_target_mismatch() -> None:
    module = _readiness_module()
    facts = _ready_facts(module)
    facts = replace(
        facts,
        executor_target=replace(facts.executor_target, database_name="other_database"),
    )

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="gate",
        probe=lambda: facts,
    )

    assert result.code == "ERASURE_EXECUTOR_DATABASE_MISMATCH"


@pytest.mark.parametrize(
    "mutation",
    [
        {"authenticated_role": "wrong_role"},
        {"effective_role": "wrong_role"},
        {"executor_has_execute": False},
        {"public_has_execute": True},
        {"app_role_has_execute": True},
        {"unexpected_execute_grant_count": 1},
    ],
)
def test_erasure_readiness_rejects_unauthorized_identity_or_execute_acl(
    mutation: dict[str, object],
) -> None:
    module = _readiness_module()
    facts = replace(_ready_facts(module), **mutation)

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )

    assert result.code == "ERASURE_EXECUTOR_UNAUTHORIZED"


def test_erasure_readiness_rejects_every_posture_and_function_shape_drift() -> None:
    module = _readiness_module()
    valid = _ready_facts(module)
    invalid = (
        replace(valid, connected=False),
        replace(valid, executor_role=replace(valid.executor_role, superuser=True)),
        replace(valid, executor_role=replace(valid.executor_role, bypass_rls=True)),
        replace(valid, executor_role=replace(valid.executor_role, inherit=True)),
        replace(valid, owner_role=replace(valid.owner_role, can_login=True)),
        replace(valid, owner_role=replace(valid.owner_role, superuser=True)),
        replace(valid, membership_count=1),
        replace(valid, executor_table_privilege_count=1),
        replace(valid, executor_column_privilege_count=1),
        replace(valid, app_effective_role="setup_admin"),
        replace(valid, executor_schema_usage=False),
        replace(valid, executor_schema_create=True),
        replace(valid, executor_schema_direct_acl_count=0),
        replace(valid, executor_other_direct_execute_count=1),
        replace(valid, function_count=2),
        replace(valid, function_schema="private"),
        replace(valid, function_name="other"),
        replace(valid, function_identity_arguments=("uuid", "uuid")),
        replace(valid, function_result_type="text"),
        replace(valid, function_owner="idis_app"),
        replace(valid, function_security_definer=False),
        replace(valid, function_search_path=("public", "pg_catalog")),
    )

    for facts in invalid:
        result = module.check_erasure_executor_readiness(
            env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
            expected_role="api",
            probe=lambda facts=facts: facts,
        )
        assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_erasure_readiness_collapses_probe_exception_without_leaking_text() -> None:
    module = _readiness_module()

    def fail() -> object:
        raise RuntimeError("postgresql://idis_erasure_executor:secret@db/idis")

    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=fail,
    )

    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"
    assert "secret" not in repr(result)


def test_worker_readiness_requires_erasure_credential_to_be_stripped_without_probe() -> None:
    module = _readiness_module()
    calls: list[str] = []

    allowed = module.check_erasure_executor_readiness(
        env={},
        expected_role="worker",
        probe=lambda: calls.append("called"),
    )
    exposed = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="worker",
        probe=lambda: calls.append("called"),
    )

    assert allowed.may_proceed is True
    assert exposed.code == "ERASURE_EXECUTOR_CONFIG_BLOCKED"
    assert calls == []


class _RowsResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _RowsResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _CatalogConnection:
    def __init__(
        self,
        *,
        executor: bool,
        future_relation_rows: list[dict[str, object]] | None = None,
        future_routine_rows: list[dict[str, object]] | None = None,
        fail_guard_catalog: bool = False,
        guard_security_mode_overrides: dict[str, bool] | None = None,
        guard_argument_type_overrides: dict[str, str | None] | None = None,
        executor_column_privilege_count: int = 0,
        executor_schema_usage: bool = True,
        executor_schema_create: bool = False,
        executor_schema_direct_acl_count: int = 1,
        executor_other_direct_execute_count: int = 0,
        owner_update_columns: tuple[str, ...] = _ERASURE_OWNER_UPDATE_COLUMNS,
        owner_update_grantable_columns: tuple[str, ...] = (),
        owner_table_update_privilege_count: int = 0,
    ) -> None:
        self.executor = executor
        self.future_relation_rows = list(future_relation_rows or [])
        self.future_routine_rows = list(future_routine_rows or [])
        self.fail_guard_catalog = fail_guard_catalog
        self.guard_security_mode_overrides = dict(guard_security_mode_overrides or {})
        self.guard_argument_type_overrides = dict(guard_argument_type_overrides or {})
        self.executor_column_privilege_count = executor_column_privilege_count
        self.executor_schema_usage = executor_schema_usage
        self.executor_schema_create = executor_schema_create
        self.executor_schema_direct_acl_count = executor_schema_direct_acl_count
        self.executor_other_direct_execute_count = executor_other_direct_execute_count
        self.owner_update_columns = owner_update_columns
        self.owner_update_grantable_columns = owner_update_grantable_columns
        self.owner_table_update_privilege_count = owner_table_update_privilege_count
        self.statements: list[str] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _RowsResult:
        sql = " ".join(str(statement).lower().split())
        self.statements.append(sql)
        if "current_database()" in sql:
            return _RowsResult([_EXECUTOR_TARGET if self.executor else _APP_TARGET])
        if "from pg_catalog.pg_roles" in sql:
            return _RowsResult(
                [
                    {
                        "role_name": "idis_erasure_executor",
                        "can_login": True,
                        "superuser": False,
                        "create_database": False,
                        "create_role": False,
                        "inherit": False,
                        "bypass_rls": False,
                        "replication": False,
                    },
                    {
                        "role_name": "idis_erasure_function_owner",
                        "can_login": False,
                        "superuser": False,
                        "create_database": False,
                        "create_role": False,
                        "inherit": False,
                        "bypass_rls": False,
                        "replication": False,
                    },
                    {
                        "role_name": "idis_task7_guard_function_owner",
                        "can_login": False,
                        "superuser": False,
                        "create_database": False,
                        "create_role": False,
                        "inherit": False,
                        "bypass_rls": False,
                        "replication": False,
                    },
                ]
            )
        if "from pg_catalog.pg_auth_members" in sql:
            return _RowsResult(
                [
                    {
                        "erasure_membership_count": 0,
                        "guard_owner_membership_count": 0,
                    }
                ]
            )
        if "erasure_owner_table_update_privilege_count" in sql:
            return _RowsResult(
                [
                    {
                        "erasure_owner_table_update_privilege_count": (
                            self.owner_table_update_privilege_count
                        )
                    }
                ]
            )
        if (
            "attribute.attname::text as column_name" in sql
            and "has_column_privilege(" in sql
            and "'idis_erasure_function_owner'" in sql
            and "'update'" in sql
        ):
            return _RowsResult(
                [
                    {
                        "relation_name": "erasure_requests",
                        "column_name": column,
                        "is_grantable": column in self.owner_update_grantable_columns,
                    }
                    for column in self.owner_update_columns
                ]
            )
        if "owned_relation_count" in sql and "force_rls_relation_count" in sql:
            if self.fail_guard_catalog:
                raise RuntimeError("catalog unavailable")
            return _RowsResult(
                [
                    {
                        "owned_relation_count": 0,
                        "owned_routine_count": 24,
                        "schema_usage": True,
                        "schema_create": False,
                        "schema_direct_usage_acl_count": 1,
                        "schema_unexpected_acl_count": 0,
                        "table_select_privilege_count": 0,
                        "table_insert_privilege_count": 0,
                        "table_update_privilege_count": 0,
                        "table_delete_privilege_count": 0,
                        "table_truncate_privilege_count": 0,
                        "table_references_privilege_count": 0,
                        "table_trigger_privilege_count": 0,
                        "force_rls_relation_count": 36,
                    }
                ]
            )
        if "from pg_catalog.pg_attribute" in sql:
            return _RowsResult(_guard_acl_catalog_rows())
        if (
            "owner.rolname = 'idis_task7_guard_function_owner'" in sql
            and "from pg_catalog.pg_proc" in sql
        ):
            rows: list[dict[str, object]] = []
            for signature, security_definer, search_path in _guard_routine_postures():
                match = re.fullmatch(r"public\.([a-z0-9_]+)\((.*)\)", signature)
                assert match is not None
                argument_types = match.group(2).replace(",", ", ")
                row: dict[str, object] = {
                    "function_schema": "public",
                    "function_name": match.group(1),
                    "identity_arguments": _named_catalog_arguments(argument_types),
                    "security_definer": self.guard_security_mode_overrides.get(
                        signature,
                        security_definer,
                    ),
                    "function_config": [f"search_path={', '.join(search_path)}"],
                    "app_role_has_execute": False,
                    "public_has_execute": False,
                    "unexpected_execute_grant_count": 0,
                }
                if "identity_argument_types" in sql:
                    row["identity_argument_types"] = self.guard_argument_type_overrides.get(
                        signature,
                        argument_types,
                    )
                rows.append(row)
            return _RowsResult(rows)
        if "idis_task7_approved_erasure_context_v1" in sql:
            return _RowsResult(
                [
                    {
                        "function_schema": "public",
                        "function_name": "idis_task7_approved_erasure_context_v1",
                        "identity_arguments": "uuid, uuid",
                        "function_owner": "idis_erasure_function_owner",
                        "security_definer": True,
                        "function_config": ["search_path=pg_catalog, pg_temp"],
                        "guard_owner_has_execute": True,
                        "app_role_has_execute": False,
                        "public_has_execute": False,
                        "unexpected_execute_grant_count": 0,
                    }
                ]
            )
        if "unnest(cast(:future_relations as text[]))" in sql:
            assert parameters == {
                "future_relations": list(_readiness_module().TASK7_V21_FUTURE_ERASURE_RELATIONS)
            }
            assert "relation.relkind" not in sql
            return _RowsResult(self.future_relation_rows)
        if "unnest(cast(:future_names as text[]))" in sql:
            future_names = [
                signature.removeprefix("public.").partition("(")[0]
                for signature in (_readiness_module().TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES)
            ]
            assert parameters == {
                "future_names": future_names,
            }
            return _RowsResult(self.future_routine_rows)
        if "has_any_column_privilege" in sql:
            return _RowsResult([{"column_privilege_count": self.executor_column_privilege_count}])
        if "executor_schema_direct_acl_count" in sql:
            return _RowsResult(
                [
                    {
                        "executor_schema_usage": self.executor_schema_usage,
                        "executor_schema_create": self.executor_schema_create,
                        "executor_schema_direct_acl_count": (self.executor_schema_direct_acl_count),
                        "executor_other_direct_execute_count": (
                            self.executor_other_direct_execute_count
                        ),
                    }
                ]
            )
        if "has_table_privilege" in sql:
            return _RowsResult([{"table_privilege_count": 0}])
        if "from pg_catalog.pg_proc" in sql:
            assert parameters == {
                "app_role": "idis_app",
                "executor_role": "idis_erasure_executor",
            }
            current = _readiness_module().TASK7_V21_CURRENT_ERASURE_RELATIONS
            function_source = ";".join(
                part
                for relation in current
                for part in (
                    f"DELETE FROM public.{relation} WHERE tenant_id=$2",
                    (
                        "deletion_counts := deletion_counts || "
                        f"pg_catalog.jsonb_build_object('{relation}',deleted_count)"
                    ),
                )
            )
            return _RowsResult(
                [
                    {
                        "function_schema": "public",
                        "function_name": "idis_task7_execute_approved_erasure",
                        "identity_arguments": "uuid, uuid, uuid",
                        "result_type": "jsonb",
                        "function_owner": "idis_erasure_function_owner",
                        "security_definer": True,
                        "function_config": ["search_path=pg_catalog, pg_temp"],
                        "executor_has_execute": True,
                        "public_has_execute": False,
                        "app_role_has_execute": False,
                        "unexpected_execute_grant_count": 0,
                        "function_source": function_source,
                    }
                ]
            )
        raise AssertionError(f"unexpected catalog SQL: {sql}")


class _ConnectionContext(AbstractContextManager[_CatalogConnection]):
    def __init__(self, connection: _CatalogConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _CatalogConnection:
        return self.connection

    def __exit__(self, *args: object) -> None:
        return None


def test_erasure_catalog_collector_produces_typed_facts_from_two_connections() -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(executor=True)

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts == _ready_facts(module)
    assert len(app.statements) == 1
    assert len(executor.statements) == 15
    owner_column_query = next(
        statement
        for statement in executor.statements
        if "has_column_privilege(" in statement
        and "'idis_erasure_function_owner'" in statement
        and "'update'" in statement
        and "attribute.attname::text as column_name" in statement
    )
    assert "'update with grant option'" in owner_column_query
    assert "grantee.rolname" not in owner_column_query
    assert "order by relation.relname, attribute.attnum" in owner_column_query
    current_guard_query = next(
        statement
        for statement in executor.statements
        if "owner.rolname = 'idis_task7_guard_function_owner'" in statement
        and "from pg_catalog.pg_proc" in statement
        and "procedure.proname::text as function_name" in statement
    )
    assert "pg_catalog.oidvectortypes(" in current_guard_query
    assert "procedure.proargtypes" in current_guard_query
    assert "as identity_argument_types" in current_guard_query


def test_erasure_catalog_collector_rejects_four_function_owner_update_columns() -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        owner_update_columns=_ERASURE_OWNER_UPDATE_COLUMNS[:-1],
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts.function_owner_update_acl.column_update_acl_count == 4
    result = _readiness_module().check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_erasure_boundary_acl_catalog_supports_null_and_non_null_acl_arrays() -> None:
    """NULL ACLs yield no lateral rows while the executor's explicit ACL remains countable."""

    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        executor_schema_direct_acl_count=1,
        executor_other_direct_execute_count=0,
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    boundary_query = next(
        statement
        for statement in executor.statements
        if "executor_schema_direct_acl_count" in statement
    )
    assert "pg_catalog.aclexplode(namespace.nspacl)" in boundary_query
    assert "pg_catalog.aclexplode(procedure.proacl)" in boundary_query
    assert "'{}'::aclitem[]" not in boundary_query
    assert facts.executor_schema_direct_acl_count == 1
    assert facts.executor_other_direct_execute_count == 0


def test_erasure_catalog_collector_rejects_executor_column_privilege() -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        executor_column_privilege_count=1,
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts.executor_table_privilege_count == 0
    assert facts.executor_column_privilege_count == 1
    result = _readiness_module().check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    ("connection_overrides", "fact_name", "expected_value"),
    (
        ({"executor_schema_usage": False}, "executor_schema_usage", False),
        ({"executor_schema_create": True}, "executor_schema_create", True),
        (
            {"executor_schema_direct_acl_count": 0},
            "executor_schema_direct_acl_count",
            0,
        ),
        (
            {"executor_other_direct_execute_count": 1},
            "executor_other_direct_execute_count",
            1,
        ),
    ),
)
def test_erasure_catalog_collector_rejects_executor_schema_or_routine_authority(
    connection_overrides: dict[str, object],
    fact_name: str,
    expected_value: object,
) -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(executor=True, **connection_overrides)

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert getattr(facts, fact_name) == expected_value
    result = _readiness_module().check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize("expected_security_definer", [True, False])
def test_erasure_catalog_collector_rejects_guard_security_mode_flip(
    expected_security_definer: bool,
) -> None:
    module = _probes_module()
    signature, security_definer, _ = next(
        posture for posture in _guard_routine_postures() if posture[1] is expected_security_definer
    )
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        guard_security_mode_overrides={signature: not security_definer},
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts.guard_owner.current_routine_posture_mismatch_count == 1
    result = _readiness_module().check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize("canonical_argument_types", ["text", None])
def test_erasure_catalog_collector_rejects_unknown_or_ambiguous_argument_types(
    canonical_argument_types: str | None,
) -> None:
    module = _probes_module()
    signature = "public.idis_task7_hash_jsonb_v1(jsonb)"
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        guard_argument_type_overrides={signature: canonical_argument_types},
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts.guard_owner.current_routine_posture_mismatch_count == 1
    assert facts.guard_owner.current_routine_signatures != (
        _readiness_module().TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES
    )
    result = _readiness_module().check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_erasure_catalog_collector_fails_closed_when_guard_catalog_is_unavailable() -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(executor=True, fail_guard_catalog=True)

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    assert facts.guard_owner.role is None
    assert facts.guard_owner.schema_direct_usage_acl_count == -1
    assert facts.guard_owner.external_helper_signature == ""
    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    ("future_relation_rows", "future_routine_rows", "expected"),
    [
        ([{"relation_name": "agent_execution_receipts"}], [], "relation"),
        (
            [],
            [
                {
                    "function_schema": "public",
                    "function_name": "idis_task7_agent_receipt_exact_one_v1",
                    "identity_arguments": "text",
                }
            ],
            "routine",
        ),
    ],
)
def test_erasure_catalog_collector_detects_any_future_object_or_helper_overload(
    future_relation_rows: list[dict[str, object]],
    future_routine_rows: list[dict[str, object]],
    expected: str,
) -> None:
    module = _probes_module()
    app = _CatalogConnection(executor=False)
    executor = _CatalogConnection(
        executor=True,
        future_relation_rows=future_relation_rows,
        future_routine_rows=future_routine_rows,
    )

    facts = module.collect_erasure_executor_facts(
        app_connection_factory=lambda: _ConnectionContext(app),
        erasure_connection_factory=lambda: _ConnectionContext(executor),
    )

    if expected == "relation":
        assert facts.runner_contract.future_relation_tuple_present == ("agent_execution_receipts",)
    else:
        assert facts.guard_owner.future_routine_signatures_present == (
            "public.idis_task7_agent_receipt_exact_one_v1(text)",
        )
    result = module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


def test_default_adapter_threads_both_connection_factories_without_opening_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _probes_module()
    expected = _ready_facts(module)
    observed: dict[str, object] = {}

    def collect(**kwargs: object) -> object:
        observed.update(kwargs)
        return expected

    monkeypatch.setattr(module, "collect_erasure_executor_facts", collect)
    adapters = module.build_default_dependency_probe_adapters(
        {
            "IDIS_ACCEPTANCE_TENANT_ID": "10000000-0000-4000-8000-000000000001",
            "IDIS_ISOLATION_CANARY_TENANT_ID": "20000000-0000-4000-8000-000000000002",
            "IDIS_ERASURE_DATABASE_URL": _ERASURE_URL,
        },
        expected_public_key_sha256="a" * 64,
    )

    assert adapters.erasure_executor is not None
    assert adapters.erasure_executor() is expected
    assert callable(observed["app_connection_factory"])
    assert callable(observed["erasure_connection_factory"])
