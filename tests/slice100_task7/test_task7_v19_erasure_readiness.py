"""RED readiness authority for the frozen full-strict-v19 erasure boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v19_contract_hash_v1.json"
)
_ERASURE_URL = "postgresql+psycopg2://idis_erasure_executor:secret@db/idis"
_OWNER = "idis_erasure_function_owner"
_COLUMN_GRANT_RE = re.compile(
    r"^GRANT (?P<privilege>SELECT|UPDATE) \((?P<columns>[a-z0-9_,]+)\) "
    r"ON TABLE public\.(?P<relation>[a-z][a-z0-9_]*) "
    r"TO idis_erasure_function_owner$"
)
_DELETE_GRANT_RE = re.compile(
    r"^GRANT DELETE ON TABLE public\.(?P<relation>[a-z][a-z0-9_]*) "
    r"TO idis_erasure_function_owner$"
)
_OWNER_FACT_FIELDS = (
    "schema_usage",
    "schema_create",
    "schema_direct_usage_acl_count",
    "schema_unexpected_acl_count",
    "owned_relation_count",
    "table_select_privilege_count",
    "table_insert_privilege_count",
    "table_update_privilege_count",
    "table_delete_privilege_count",
    "table_truncate_privilege_count",
    "table_references_privilege_count",
    "table_trigger_privilege_count",
    "column_select_acl_relation_count",
    "column_select_acl_count",
    "column_select_acl_sha256",
    "column_update_acl_relation_count",
    "column_update_acl_count",
    "column_update_acl_sha256",
    "delete_relation_tuple",
    "delete_relation_sha256",
)


def _readiness() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _probes() -> ModuleType:
    return importlib.import_module("idis.runtime.dependency_probes")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _require_frozen_v19() -> tuple[dict[str, Any], dict[str, Any]]:
    missing = tuple(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_ARTIFACT_PATH, _FIXTURE_PATH)
        if not path.is_file()
    )
    assert not missing, "TASK7_V19_READINESS_AUTHORITY_NOT_PUBLISHED:" + ",".join(missing)
    artifact_raw = _ARTIFACT_PATH.read_bytes()
    artifact = _load_json(_ARTIFACT_PATH)
    fixture = _load_json(_FIXTURE_PATH)
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v19"
    assert fixture["pipeline_contract_version"] == "full-strict-v19"
    assert fixture["artifact_sha256"] == hashlib.sha256(artifact_raw).hexdigest()
    return artifact, fixture


def _erasure_row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    section = artifact["erasure_execution_registry"]
    assert isinstance(section, Mapping)
    rows = section["rows"]
    assert isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping)
    return cast(Mapping[str, Any], rows[0])


def _acl_registry(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    sql = artifact["sql_authority_registry"]
    assert isinstance(sql, Mapping)
    acl = sql["guard_function_acl_registry"]
    assert isinstance(acl, Mapping)
    return cast(Mapping[str, Any], acl)


def _decode_hex_tuple(value: object) -> tuple[str, ...]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    assert all(isinstance(item, str) for item in value)
    return tuple(bytes.fromhex(cast(str, item)).decode("utf-8") for item in value)


def _canonical_digest(domain: str, value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + payload).hexdigest()


def _column_projection(
    statements: Sequence[str],
    privilege: str,
) -> tuple[tuple[str, str, str, bool], ...]:
    rows: list[tuple[str, str, str, bool]] = []
    for statement in statements:
        match = _COLUMN_GRANT_RE.fullmatch(statement)
        if match is None or match.group("privilege") != privilege:
            continue
        rows.extend(
            (
                match.group("relation"),
                column,
                privilege,
                False,
            )
            for column in match.group("columns").split(",")
        )
    return tuple(sorted(rows))


def _ordered_columns_for_privilege(
    statements: Sequence[str],
    privilege: str,
) -> tuple[str, ...]:
    return tuple(
        column
        for statement in statements
        if (match := _COLUMN_GRANT_RE.fullmatch(statement)) is not None
        and match.group("privilege") == privilege
        for column in match.group("columns").split(",")
    )


def _delete_relations(statements: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        match.group("relation")
        for statement in statements
        if (match := _DELETE_GRANT_RE.fullmatch(statement)) is not None
    )


def _owner_expected_values(artifact: Mapping[str, Any]) -> dict[str, Any]:
    erasure = _erasure_row(artifact)
    statements = _decode_hex_tuple(erasure["current_acl_sql_utf8_hex_tuple"])
    select_projection = _column_projection(statements, "SELECT")
    update_projection = _column_projection(statements, "UPDATE")
    delete_relations = _delete_relations(statements)
    assert delete_relations == tuple(erasure["current_relation_tuple"])
    table_grant_counts = {
        privilege: sum(
            statement.startswith(f"GRANT {privilege} ON TABLE public.")
            and statement.endswith(f" TO {_OWNER}")
            for statement in statements
        )
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        )
    }
    return {
        "schema_usage": (f"GRANT USAGE ON SCHEMA public TO {_OWNER}" in statements),
        "schema_create": (f"GRANT CREATE ON SCHEMA public TO {_OWNER}" in statements),
        "schema_direct_usage_acl_count": sum(
            statement == f"GRANT USAGE ON SCHEMA public TO {_OWNER}" for statement in statements
        ),
        "schema_unexpected_acl_count": sum(
            " ON SCHEMA public " in statement
            and statement.startswith("GRANT ")
            and statement != f"GRANT USAGE ON SCHEMA public TO {_OWNER}"
            for statement in statements
        ),
        "owned_relation_count": 0,
        "table_select_privilege_count": table_grant_counts["SELECT"],
        "table_insert_privilege_count": table_grant_counts["INSERT"],
        "table_update_privilege_count": table_grant_counts["UPDATE"],
        "table_delete_privilege_count": table_grant_counts["DELETE"],
        "table_truncate_privilege_count": table_grant_counts["TRUNCATE"],
        "table_references_privilege_count": table_grant_counts["REFERENCES"],
        "table_trigger_privilege_count": table_grant_counts["TRIGGER"],
        "column_select_acl_relation_count": len(
            {relation for relation, _column, _privilege, _grantable in select_projection}
        ),
        "column_select_acl_count": len(select_projection),
        "column_select_acl_sha256": _canonical_digest(
            "task7-v19-erasure-function-owner-column-select-acl-v1",
            select_projection,
        ),
        "column_update_acl_relation_count": len(
            {relation for relation, _column, _privilege, _grantable in update_projection}
        ),
        "column_update_acl_count": len(update_projection),
        "column_update_acl_sha256": _canonical_digest(
            "task7-v19-erasure-function-owner-column-update-acl-v1",
            update_projection,
        ),
        "delete_relation_tuple": delete_relations,
        "delete_relation_sha256": _canonical_digest(
            "task7-v19-erasure-function-owner-delete-relations-v1",
            delete_relations,
        ),
    }


def _core_ready_facts(module: ModuleType) -> Any:
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
        executor_schema_direct_acl_count=0,
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
    )


def _check(module: ModuleType, facts: object) -> Any:
    return module.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )


def test_v19_readiness_exposes_only_v19_relation_and_guard_authority() -> None:
    artifact, _fixture = _require_frozen_v19()
    module = _readiness()
    erasure = _erasure_row(artifact)
    acl = _acl_registry(artifact)
    current_signatures = tuple(
        sorted(
            row["function_signature"]
            for row in cast(list[Mapping[str, Any]], acl["function_rows"])
            if row["install_scope"] == "CURRENT_0038"
        )
    )
    future_signatures = tuple(
        sorted(
            row["function_signature"]
            for row in cast(list[Mapping[str, Any]], acl["function_rows"])
            if row["install_scope"] == "FUTURE_INACTIVE"
        )
    )

    assert tuple(erasure["current_relation_tuple"]) == (module.TASK7_V19_CURRENT_ERASURE_RELATIONS)
    assert tuple(erasure["future_relation_tuple"]) == (module.TASK7_V19_FUTURE_ERASURE_RELATIONS)
    assert tuple(erasure["complete_relation_tuple"]) == (
        module.TASK7_V19_COMPLETE_ERASURE_RELATIONS
    )
    assert current_signatures == module.TASK7_V19_CURRENT_GUARD_ROUTINE_SIGNATURES
    assert future_signatures == module.TASK7_V19_FUTURE_GUARD_ROUTINE_SIGNATURES
    assert module.task7_v19_erasure_runner_split_is_valid() is True
    for name in (
        "TASK7_V18_CURRENT_ERASURE_RELATIONS",
        "TASK7_V18_FUTURE_ERASURE_RELATIONS",
        "TASK7_V18_COMPLETE_ERASURE_RELATIONS",
        "TASK7_V18_CURRENT_GUARD_ROUTINE_SIGNATURES",
        "TASK7_V18_FUTURE_GUARD_ROUTINE_SIGNATURES",
        "task7_v18_erasure_runner_split_is_valid",
    ):
        assert not hasattr(module, name)


def test_v19_owner_acl_facts_are_typed_closed_and_default_compatible() -> None:
    artifact, _fixture = _require_frozen_v19()
    module = _readiness()
    expected_values = _owner_expected_values(artifact)

    assert tuple(field.name for field in fields(module.ErasureFunctionOwnerAclFacts)) == (
        _OWNER_FACT_FIELDS
    )
    expected = module.ErasureFunctionOwnerAclFacts(**expected_values)
    facts = _core_ready_facts(module)
    assert facts.function_owner_acl == expected
    assert expected.schema_usage is True
    assert expected.schema_create is False
    assert expected.owned_relation_count == 0
    assert expected.table_select_privilege_count == 0
    assert expected.table_insert_privilege_count == 0
    assert expected.table_update_privilege_count == 0
    assert expected.table_delete_privilege_count == 48
    assert expected.table_truncate_privilege_count == 0
    assert expected.table_references_privilege_count == 0
    assert expected.table_trigger_privilege_count == 0
    assert expected.column_select_acl_relation_count == 51
    # V19 adds audit_events.event to the reviewed read closure; the artifact
    # projection therefore contains 114, not the superseded 113, column grants.
    assert expected.column_select_acl_count == 114
    assert expected.column_update_acl_relation_count == 1
    assert expected.column_update_acl_count == 5
    assert len(expected.delete_relation_tuple) == 48


def test_v19_probe_digest_helpers_match_independent_artifact_projections() -> None:
    artifact, _fixture = _require_frozen_v19()
    probes = _probes()
    erasure = _erasure_row(artifact)
    statements = _decode_hex_tuple(erasure["current_acl_sql_utf8_hex_tuple"])
    expected = _owner_expected_values(artifact)
    select_projection = _column_projection(statements, "SELECT")
    update_projection = _column_projection(statements, "UPDATE")
    column_rows = [
        {
            "relation_name": relation,
            "column_name": column,
            "privilege_type": privilege,
            "is_grantable": is_grantable,
        }
        for relation, column, privilege, is_grantable in (
            *select_projection,
            *update_projection,
        )
    ]
    delete_rows = [
        {
            "relation_name": relation,
            "privilege_type": "DELETE",
            "is_grantable": False,
        }
        for relation in expected["delete_relation_tuple"]
    ]

    assert probes._erasure_function_owner_column_select_acl_digest(column_rows) == (
        expected["column_select_acl_relation_count"],
        expected["column_select_acl_count"],
        expected["column_select_acl_sha256"],
    )
    assert probes._erasure_function_owner_column_update_acl_digest(column_rows) == (
        expected["column_update_acl_relation_count"],
        expected["column_update_acl_count"],
        expected["column_update_acl_sha256"],
    )
    assert probes._erasure_function_owner_delete_acl_digest(delete_rows) == (
        expected["delete_relation_tuple"],
        expected["delete_relation_sha256"],
    )


def test_v19_exact_operator_facts_pass_and_v18_defaults_cannot_pass() -> None:
    artifact, _fixture = _require_frozen_v19()
    module = _readiness()
    facts = _core_ready_facts(module)

    result = _check(module, facts)

    assert facts.function_owner_acl == module.ErasureFunctionOwnerAclFacts(
        **_owner_expected_values(artifact)
    )
    assert result.may_proceed is True
    assert result.code is None


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("schema_usage", False),
        ("schema_create", True),
        ("schema_direct_usage_acl_count", 0),
        ("schema_unexpected_acl_count", 1),
        ("owned_relation_count", 1),
        ("table_select_privilege_count", 1),
        ("table_insert_privilege_count", 1),
        ("table_update_privilege_count", 1),
        ("table_delete_privilege_count", 47),
        ("table_truncate_privilege_count", 1),
        ("table_references_privilege_count", 1),
        ("table_trigger_privilege_count", 1),
        ("column_select_acl_relation_count", 50),
        ("column_select_acl_count", 112),
        ("column_select_acl_sha256", "0" * 64),
        ("column_update_acl_relation_count", 2),
        ("column_update_acl_count", 4),
        ("column_update_acl_sha256", "0" * 64),
        ("delete_relation_tuple", ()),
        ("delete_relation_sha256", "0" * 64),
    ),
)
def test_v19_readiness_rejects_each_owner_acl_drift(
    field_name: str,
    invalid_value: object,
) -> None:
    _require_frozen_v19()
    module = _readiness()
    valid = _core_ready_facts(module)
    invalid_owner_acl = replace(
        valid.function_owner_acl,
        **{field_name: invalid_value},
    )

    result = _check(
        module,
        replace(valid, function_owner_acl=invalid_owner_acl),
    )

    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_POSTURE_BLOCKED"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("app_role_has_execute", True),
        ("public_has_execute", True),
        ("executor_has_execute", False),
        ("executor_table_privilege_count", 1),
        ("executor_column_privilege_count", 1),
        ("app_effective_role", "setup_admin"),
        ("executor_schema_usage", False),
        ("executor_schema_create", True),
        ("executor_schema_direct_acl_count", 1),
        ("executor_other_direct_execute_count", 1),
    ),
)
def test_v19_readiness_preserves_exact_callable_boundary(
    field_name: str,
    invalid_value: object,
) -> None:
    _require_frozen_v19()
    module = _readiness()
    valid = _core_ready_facts(module)

    result = _check(module, replace(valid, **{field_name: invalid_value}))

    assert result.may_proceed is False
    assert result.code == "ERASURE_EXECUTOR_UNAUTHORIZED"


def test_v19_readiness_facts_bind_crash_open_reconstruction_authority() -> None:
    artifact, _fixture = _require_frozen_v19()
    facts = _core_ready_facts(_readiness())
    erasure = _erasure_row(artifact)
    object_contract = erasure["object_reconciliation_contract_tuple"]
    assert isinstance(object_contract, list)

    assert facts.function_owner_acl.column_update_acl_count == 5
    assert _ordered_columns_for_privilege(
        _decode_hex_tuple(erasure["current_acl_sql_utf8_hex_tuple"]),
        "UPDATE",
    ) == (
        "request_id",
        "status",
        "executed_by",
        "executed_at",
        "counts",
    )
    assert "EXECUTED_OPEN_ATTEMPT_RECONSTRUCTS_SUCCEEDED_FROM_PERSISTED_COUNTS_AND_EXECUTED_AT" in (
        object_contract
    )
