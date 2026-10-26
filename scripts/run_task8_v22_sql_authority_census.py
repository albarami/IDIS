"""Independent disposable-PostgreSQL census for Task-8 v22 authorities."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_SCHEMA = "task8-v22-postgres-authority-census-v1"
_RECEIPT_SCHEMA = "task8-v22-postgres-authority-census-receipt-v1"
_AUTHORITY_IDS = (
    "dashboard-root-equation-v2",
    "dashboard-partial-native-root-exact-one-v1",
    "task8-current48-dashboard-erasure-refusal-v1",
)
_REFUSAL_CODE = "TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED"
_REFUSAL_SQLSTATE = "23514"
_DASHBOARD_REJECTION_CODE = "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
_PARTIAL_EXACT_ONE_CODE = "STEP_OUTPUT_MISSING"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTHORITY_EXECUTION_SCHEMA = "task8-v22-hash-bound-authority-execution-v1"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_ERASURE_OWNER = "idis_erasure_function_owner"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_EXACT_DASHBOARD_ERASURE_ACL = (
    "GRANT SELECT (tenant_id,deal_id) ON TABLE "
    "public.methodology_truth_dashboard_rows TO idis_erasure_function_owner"
)


class Task8V22CensusError(RuntimeError):
    """Raised with a stable safe code when the census cannot be trusted."""


def _fail(code: str) -> None:
    raise Task8V22CensusError(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_v22(artifact: Mapping[str, Any]) -> None:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    if (
        metadata.get("artifact_schema") != "full-strict-v22-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v22"
        or metadata.get("append_frozen") is not True
        or metadata.get("activation_status") != "INACTIVE"
    ):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    section = artifact.get("task8_fdd_contract")
    if not isinstance(section, dict):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    dashboard = section.get("dashboard_contract")
    partial = section.get("partial_exact_one_contract")
    refusal = section.get("erasure_refusal_contract")
    if (
        not isinstance(dashboard, dict)
        or dashboard.get("authority_id") != _AUTHORITY_IDS[0]
        or not isinstance(partial, dict)
        or partial.get("authority_id") != _AUTHORITY_IDS[1]
        or not isinstance(refusal, dict)
        or refusal.get("reason_code") != _REFUSAL_CODE
        or refusal.get("sqlstate") != _REFUSAL_SQLSTATE
    ):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    authority_registry = artifact.get("sql_authority_registry")
    if not isinstance(authority_registry, dict):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    authority_ids = {
        row.get("authority_id")
        for row in authority_registry.get("rows", [])
        if isinstance(row, dict)
    }
    if not set(_AUTHORITY_IDS[:2]).issubset(authority_ids):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")


def _decode_sql_tuple(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _fail(code)
    decoded: list[str] = []
    try:
        for encoded in value:
            if not isinstance(encoded, str) or not encoded or len(encoded) % 2:
                raise ValueError("invalid hex")
            statement = bytes.fromhex(encoded).decode("utf-8")
            if statement != " ".join(statement.split()):
                raise ValueError("noncanonical SQL")
            decoded.append(statement)
    except (UnicodeError, ValueError) as exc:
        raise Task8V22CensusError(code) from exc
    return tuple(decoded)


def _sql_authority_row_hash(row: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        [
            "full-strict-v22-sql-authority-row-v1",
            {key: value for key, value in row.items() if key != "expected_sha256"},
        ]
    )


def _guard_acl_function_row_hash(row: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(_ACL_FUNCTION_HASH_DOMAIN + _canonical_json(payload)).hexdigest()


def _erasure_execution_row_hash(row: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        [
            "full-strict-v22-erasure-execution-row-v1",
            {key: value for key, value in row.items() if key != "expected_sha256"},
        ]
    )


def _guard_select_sql(function_row: Mapping[str, Any]) -> tuple[str, ...]:
    owner = function_row.get("owner_role")
    if owner != _GUARD_OWNER:
        _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
    direct = function_row.get("direct_read_column_tuple")
    if not isinstance(direct, list) or not direct:
        _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
    statements: list[str] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in direct:
        if (
            not isinstance(item, list)
            or len(item) != 3
            or not isinstance(item[0], str)
            or not isinstance(item[1], list)
            or not item[1]
            or any(
                not isinstance(column, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", column) is None
                for column in item[1]
            )
            or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[0]) is None
        ):
            _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
        relation = item[0]
        columns = tuple(item[1])
        key = (relation, columns)
        if key in seen:
            _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
        seen.add(key)
        statements.append(
            f"GRANT SELECT ({','.join(columns)}) ON TABLE public.{relation} TO {_GUARD_OWNER}"
        )
    return tuple(statements)


def build_hash_bound_authority_execution(v22: Mapping[str, Any]) -> dict[str, Any]:
    """Decode and bind the only SQL bytes the Task-8 census may install."""

    _validate_v22(v22)
    sql_registry = v22["sql_authority_registry"]
    rows = sql_registry.get("rows")
    guard_registry = sql_registry.get("guard_function_acl_registry")
    if not isinstance(rows, list) or not isinstance(guard_registry, dict):
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
    guard_rows = guard_registry.get("function_rows")
    if not isinstance(guard_rows, list):
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")

    sql_bindings: list[dict[str, Any]] = []
    for authority_id in _AUTHORITY_IDS[:2]:
        authority_matches = [
            row for row in rows if isinstance(row, dict) and row.get("authority_id") == authority_id
        ]
        guard_matches = [
            row
            for row in guard_rows
            if isinstance(row, dict) and row.get("authority_id") == authority_id
        ]
        if len(authority_matches) != 1 or not guard_matches:
            _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
        authority = authority_matches[0]
        guard_matches.sort(key=lambda row: row.get("function_ordinal", -1))
        if authority.get("expected_sha256") != _sql_authority_row_hash(authority) or any(
            guard.get("expected_sha256") != _guard_acl_function_row_hash(guard)
            for guard in guard_matches
        ):
            _fail("TASK8_V22_CENSUS_AUTHORITY_HASH_INVALID")
        body_hex = authority.get("body_sql_utf8_hex_tuple")
        _decode_sql_tuple(body_hex, code="TASK8_V22_CENSUS_AUTHORITY_SQL_INVALID")
        target_tuple = authority.get("target_tuple")
        if not isinstance(target_tuple, list) or not target_tuple:
            _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
        guard_bindings: list[dict[str, Any]] = []
        for guard in guard_matches:
            function_kind = guard.get("function_kind")
            target_triggers = guard.get("target_trigger_tuple")
            event_coordinates = guard.get("target_event_coordinate_tuple")
            if (
                function_kind not in {"HELPER", "TRIGGER_GUARD"}
                or not isinstance(target_triggers, list)
                or not isinstance(event_coordinates, list)
            ):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            if function_kind == "HELPER":
                if target_triggers or event_coordinates:
                    _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            elif target_triggers != target_tuple or len(event_coordinates) != 3 * len(
                target_triggers
            ):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            owner_sql_hex = guard.get("owner_sql_utf8_hex")
            if not isinstance(owner_sql_hex, str):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            _decode_sql_tuple([owner_sql_hex], code="TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            function_acl_hex = guard.get("acl_sql_utf8_hex_tuple")
            _decode_sql_tuple(function_acl_hex, code="TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            guard_select = _guard_select_sql(guard)
            guard_bindings.append(
                {
                    "function_signature": guard.get("function_signature"),
                    "function_kind": guard.get("function_kind"),
                    "target_trigger_tuple": guard.get("target_trigger_tuple"),
                    "target_event_coordinate_tuple": guard.get("target_event_coordinate_tuple"),
                    "guard_function_acl_sha256": guard["expected_sha256"],
                    "owner_sql_utf8_hex": owner_sql_hex,
                    "function_acl_sql_utf8_hex_tuple": function_acl_hex,
                    "guard_select_sql_tuple": list(guard_select),
                    "guard_select_tuple_sha256": _canonical_sha256(
                        [
                            "task8-v22-guard-select-tuple-v1",
                            authority_id,
                            guard.get("function_signature"),
                            guard_select,
                        ]
                    ),
                }
            )
        downgrade_hex: object = []
        if authority_id == _AUTHORITY_IDS[1]:
            partial_contract = v22.get("task8_fdd_contract", {}).get("partial_exact_one_contract")
            if not isinstance(partial_contract, Mapping):
                _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
            downgrade_hex = partial_contract.get("downgrade_sql_utf8_hex_tuple")
            _decode_sql_tuple(
                downgrade_hex,
                code="TASK8_V22_CENSUS_AUTHORITY_SQL_INVALID",
            )
        sql_bindings.append(
            {
                "authority_id": authority_id,
                "authority_sha256": authority["expected_sha256"],
                "body_sql_utf8_hex_tuple": body_hex,
                "target_tuple": target_tuple,
                "body_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v22-authority-body-tuple-v1",
                        authority_id,
                        body_hex,
                    ]
                ),
                "downgrade_sql_utf8_hex_tuple": downgrade_hex,
                "downgrade_tuple_sha256": _canonical_sha256(
                    [
                        "task8-v22-authority-downgrade-tuple-v1",
                        authority_id,
                        downgrade_hex,
                    ]
                ),
                "guard_function_acl_binding_tuple": guard_bindings,
            }
        )

    erasure_section = v22.get("erasure_execution_registry")
    if not isinstance(erasure_section, dict):
        _fail("TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_rows = erasure_section.get("rows")
    if not isinstance(erasure_rows, list) or len(erasure_rows) != 1:
        _fail("TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure = erasure_rows[0]
    if (
        not isinstance(erasure, dict)
        or erasure.get("expected_sha256") != _erasure_execution_row_hash(erasure)
        or erasure_section.get("expected_sha256")
        != _canonical_sha256(["full-strict-v22-erasure-execution-registry-v1", erasure_rows])
    ):
        _fail("TASK8_V22_CENSUS_ERASURE_AUTHORITY_HASH_INVALID")
    body_hex = erasure.get("current_body_sql_utf8_hex_tuple")
    acl_hex = erasure.get("current_acl_sql_utf8_hex_tuple")
    body_sql = _decode_sql_tuple(
        body_hex,
        code="TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    acl_sql = _decode_sql_tuple(
        acl_hex,
        code="TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    if (
        len(body_sql) != 1
        or body_sql[0].count(_REFUSAL_CODE) != 1
        or body_sql[0].count("public.methodology_truth_dashboard_rows") != 1
        or tuple(
            statement
            for statement in acl_sql
            if "public.methodology_truth_dashboard_rows" in statement
        )
        != (_EXACT_DASHBOARD_ERASURE_ACL,)
    ):
        _fail("TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID")
    erasure_binding = {
        "execution_id": erasure.get("execution_id"),
        "authority_sha256": erasure["expected_sha256"],
        "current_body_sql_utf8_hex_tuple": body_hex,
        "body_tuple_sha256": _canonical_sha256(
            [
                "task8-v22-erasure-body-tuple-v1",
                erasure.get("execution_id"),
                body_hex,
            ]
        ),
        "current_acl_sql_utf8_hex_tuple": acl_hex,
        "acl_tuple_sha256": _canonical_sha256(
            [
                "task8-v22-erasure-acl-tuple-v1",
                erasure.get("execution_id"),
                acl_hex,
            ]
        ),
    }
    payload = {
        "schema_version": _AUTHORITY_EXECUTION_SCHEMA,
        "sql_authorities": sql_bindings,
        "erasure_authority": erasure_binding,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(
            ["task8-v22-hash-bound-authority-execution-v1", payload]
        ),
    }


def execute_hash_bound_authority_sql(
    cursor: Any,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute the exact decoded bundle in reviewed order and return its binding."""

    payload = {key: value for key, value in bundle.items() if key != "expected_sha256"}
    if (
        tuple(bundle)
        != (
            "schema_version",
            "sql_authorities",
            "erasure_authority",
            "expected_sha256",
        )
        or bundle.get("schema_version") != _AUTHORITY_EXECUTION_SCHEMA
        or bundle.get("expected_sha256")
        != _canonical_sha256(["task8-v22-hash-bound-authority-execution-v1", payload])
    ):
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
    statements: list[str] = []
    authority_binding: list[dict[str, Any]] = []
    authority_install_statements: dict[str, list[str]] = {}
    authority_downgrade_statements: dict[str, tuple[str, ...]] = {}
    sql_authorities = bundle.get("sql_authorities")
    erasure = bundle.get("erasure_authority")
    if not isinstance(sql_authorities, list) or not isinstance(erasure, dict):
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
    for row in sql_authorities:
        if not isinstance(row, dict):
            _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
        body = _decode_sql_tuple(
            row.get("body_sql_utf8_hex_tuple"),
            code="TASK8_V22_CENSUS_AUTHORITY_SQL_INVALID",
        )
        downgrade = (
            _decode_sql_tuple(
                row.get("downgrade_sql_utf8_hex_tuple"),
                code="TASK8_V22_CENSUS_AUTHORITY_SQL_INVALID",
            )
            if row.get("downgrade_sql_utf8_hex_tuple")
            else ()
        )
        if row.get("downgrade_tuple_sha256") != _canonical_sha256(
            [
                "task8-v22-authority-downgrade-tuple-v1",
                row.get("authority_id"),
                row.get("downgrade_sql_utf8_hex_tuple"),
            ]
        ):
            _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
        guard_bindings = row.get("guard_function_acl_binding_tuple")
        if not isinstance(guard_bindings, list) or not guard_bindings:
            _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
        guard_receipts: list[dict[str, Any]] = []
        row_statements = list(body)
        for guard in guard_bindings:
            if not isinstance(guard, dict):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            owner = _decode_sql_tuple(
                [guard.get("owner_sql_utf8_hex")],
                code="TASK8_V22_CENSUS_GUARD_ACL_INVALID",
            )
            acl = _decode_sql_tuple(
                guard.get("function_acl_sql_utf8_hex_tuple"),
                code="TASK8_V22_CENSUS_GUARD_ACL_INVALID",
            )
            guard_select = guard.get("guard_select_sql_tuple")
            if not isinstance(guard_select, list) or any(
                not isinstance(statement, str) for statement in guard_select
            ):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            if guard.get("guard_select_tuple_sha256") != _canonical_sha256(
                [
                    "task8-v22-guard-select-tuple-v1",
                    row.get("authority_id"),
                    guard.get("function_signature"),
                    tuple(guard_select),
                ]
            ):
                _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
            row_statements.extend((*owner, *acl, *guard_select))
            guard_receipts.append(
                {
                    "function_signature": guard["function_signature"],
                    "function_kind": guard["function_kind"],
                    "target_trigger_tuple": guard["target_trigger_tuple"],
                    "target_event_coordinate_tuple": guard["target_event_coordinate_tuple"],
                    "guard_select_sql_tuple": list(guard_select),
                    "guard_function_acl_sha256": guard["guard_function_acl_sha256"],
                    "guard_select_tuple_sha256": guard["guard_select_tuple_sha256"],
                }
            )
        for statement in row_statements:
            cursor.execute(statement)
            statements.append(statement)
        authority_install_statements[str(row["authority_id"])] = row_statements
        authority_downgrade_statements[str(row["authority_id"])] = downgrade
        authority_binding.append(
            {
                "authority_id": row["authority_id"],
                "authority_sha256": row["authority_sha256"],
                "body_tuple_sha256": row["body_tuple_sha256"],
                "target_tuple": row["target_tuple"],
                "downgrade_tuple_sha256": row["downgrade_tuple_sha256"],
                "downgrade_statement_count": len(downgrade),
                "guard_function_acl_binding_tuple": guard_receipts,
            }
        )
    erasure_body = _decode_sql_tuple(
        erasure.get("current_body_sql_utf8_hex_tuple"),
        code="TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    erasure_acl = _decode_sql_tuple(
        erasure.get("current_acl_sql_utf8_hex_tuple"),
        code="TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID",
    )
    for statement in (*erasure_body, *erasure_acl):
        cursor.execute(statement)
        statements.append(statement)
    authority_binding.append(
        {
            "authority_id": _AUTHORITY_IDS[2],
            "authority_sha256": erasure["authority_sha256"],
            "body_tuple_sha256": erasure["body_tuple_sha256"],
            "acl_tuple_sha256": erasure["acl_tuple_sha256"],
        }
    )
    partial_restore = authority_downgrade_statements.get(_AUTHORITY_IDS[1], ())
    partial_reinstall = authority_install_statements.get(_AUTHORITY_IDS[1], [])
    if not partial_restore or not partial_reinstall:
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
    for statement in (*partial_restore, *partial_reinstall):
        cursor.execute(statement)
        statements.append(statement)
    partial_cycle_sha256 = _canonical_sha256(
        [
            "task8-v22-partial-restore-reinstall-v1",
            list(partial_restore),
            partial_reinstall,
        ]
    )
    return {
        "schema_version": "task8-v22-authority-execution-binding-receipt-v1",
        "bundle_sha256": bundle["expected_sha256"],
        "authority_binding_tuple": authority_binding,
        "partial_restore_reinstall_statement_tuple_sha256": partial_cycle_sha256,
        "executed_statement_tuple_sha256": _canonical_sha256(
            ["task8-v22-executed-authority-sql-v1", statements]
        ),
    }


def _artifact_row(
    artifact: Mapping[str, Any],
    registry: str,
    *,
    field: str,
    value: str,
) -> Mapping[str, Any]:
    section = artifact.get(registry)
    rows = section.get("rows") if isinstance(section, Mapping) else None
    matches = [row for row in rows or () if isinstance(row, Mapping) and row.get(field) == value]
    if len(matches) != 1:
        _fail("TASK8_V22_CENSUS_ARTIFACT_ROW_INVALID")
    return matches[0]


def _column_definition_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    groups = (
        row.get("primary_key", ()),
        row.get("scope_columns", ()),
        row.get("immutable_fields", ()),
        row.get("column_tuple", ()),
    )
    result: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if (
                not isinstance(item, list)
                or len(item) != 3
                or not isinstance(item[0], int)
                or not isinstance(item[1], str)
                or not isinstance(item[2], str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[1]) is None
                or item[1] in seen
            ):
                _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
            seen.add(item[1])
            result.append((len(result), item[1], item[2]))
    if not result:
        _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
    return tuple(f"{name} {definition}" for _ordinal, name, definition in result)


def _table_constraint_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for item in row.get("check_tuple", ()):
        if not isinstance(item, list) or len(item) != 2:
            _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
        result.append(f"CONSTRAINT {item[0]} CHECK ({item[1]})")
    unique_rows = row.get("unique_tuple", ())
    for item in unique_rows:
        if not isinstance(item, list) or len(item) != 3 or not item[1]:
            _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
        columns = ",".join(item[1])
        authority_kind = "PRIMARY KEY" if str(item[0]).startswith("pk_") else "UNIQUE"
        result.append(f"CONSTRAINT {item[0]} {authority_kind} ({columns})")
    references = row.get("reference_fields", row.get("foreign_key_tuple", ()))
    for item in references:
        if not isinstance(item, list) or len(item) != 8:
            _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
        name, columns, parent, _authority, parent_columns, on_update, on_delete, _index = item
        result.append(
            f"CONSTRAINT {name} FOREIGN KEY ({','.join(columns)}) "
            f"REFERENCES public.{parent} ({','.join(parent_columns)}) "
            f"ON UPDATE {on_update} ON DELETE {on_delete}"
        )
    return tuple(result)


def build_future_table_scaffold(v22: Mapping[str, Any], application_role: str) -> dict[str, Any]:
    """Derive the two Task-8 future tables from the reviewed registry bytes."""

    _validate_v22(v22)
    role = _quoted_identifier(application_role)
    native = _artifact_row(
        v22,
        "future_native_authority_registry",
        field="planned_table",
        value="methodology_truth_dashboard_rows",
    )
    child = _artifact_row(
        v22,
        "future_child_authority_registry",
        field="table_name",
        value="methodology_truth_dashboard_root_links",
    )
    statements: list[str] = []
    for row, field in ((native, "planned_table"), (child, "table_name")):
        relation = str(row[field])
        definitions = (*_column_definition_tuple(row), *_table_constraint_tuple(row))
        statements.append(f"CREATE TABLE public.{relation} ({','.join(definitions)})")
        index_rows = list(row.get("index_tuple", ()))
        for reference in row.get("reference_fields", row.get("foreign_key_tuple", ())):
            if not isinstance(reference, list) or len(reference) != 8:
                _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
            index_rows.append([reference[7], reference[1], None, False])
        seen_indexes: set[str] = set()
        for index in index_rows:
            if not isinstance(index, list) or len(index) != 4:
                _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
            name, columns, predicate, unique = index
            if name in seen_indexes:
                continue
            seen_indexes.add(name)
            unique_sql = "UNIQUE " if unique else ""
            predicate_sql = f" WHERE {predicate}" if predicate else ""
            statements.append(
                f"CREATE {unique_sql}INDEX {name} ON public.{relation} "
                f"({','.join(columns)}){predicate_sql}"
            )
        rls = row.get("rls_tuple")
        if not isinstance(rls, list) or len(rls) != 1:
            _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
        for statement in rls[0]:
            if not isinstance(statement, str):
                _fail("TASK8_V22_CENSUS_FUTURE_DDL_INVALID")
            for part in statement.replace("<configured_app_role>", role).split(";"):
                if part.strip():
                    statements.append(part.strip())
    payload = {
        "schema_version": "task8-v22-future-table-scaffold-v1",
        "relation_tuple": [
            "methodology_truth_dashboard_rows",
            "methodology_truth_dashboard_root_links",
        ],
        "statement_tuple": statements,
        "installed_tenant_rls_table_count": 69,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v22-future-table-scaffold-v1", payload]),
    }


def build_application_behavior_grants(
    v22: Mapping[str, Any],
    application_role: str,
) -> dict[str, Any]:
    """Bind the reviewed 0038 app grants needed by the behavior fixture."""

    _validate_v22(v22)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", application_role) is None:
        _fail("TASK8_V22_CENSUS_ROLE_INVALID")
    relations = ("run_output_objects", "run_step_outputs")
    statements: list[str] = []
    rows = v22.get("current_ddl_registry", {}).get("rows", ())
    for relation in relations:
        matches = [
            row for row in rows if isinstance(row, Mapping) and row.get("table_name") == relation
        ]
        if len(matches) != 1:
            _fail("TASK8_V22_CENSUS_APPLICATION_GRANT_INVALID")
        rls_tuple = matches[0].get("rls_tuple")
        if not isinstance(rls_tuple, list):
            _fail("TASK8_V22_CENSUS_APPLICATION_GRANT_INVALID")
        expected = f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {relation} TO <configured_app_role>"
        if rls_tuple.count(expected) != 1:
            _fail("TASK8_V22_CENSUS_APPLICATION_GRANT_INVALID")
        statements.append(expected.replace("<configured_app_role>", application_role))
    payload = {
        "schema_version": "task8-v22-application-behavior-grants-v1",
        "relation_tuple": list(relations),
        "statement_tuple": statements,
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v22-application-behavior-grants-v1", payload]),
    }


def build_zero_mutation_projection_manifest(v22: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the rejection projection to CURRENT48 plus Task-8/control rows."""

    _validate_v22(v22)
    erasure = v22["erasure_execution_registry"]["rows"][0]
    current = list(erasure["current_relation_tuple"])
    payload = {
        "schema_version": "task8-v22-zero-mutation-projection-v1",
        "current_relation_tuple": current,
        "indirect_relation_tuple": [
            "calc_sanads",
            "human_gate_actions",
            "run_steps",
        ],
        "task8_relation_tuple": [
            "methodology_truth_dashboard_rows",
            "methodology_truth_dashboard_root_links",
        ],
        "control_relation_tuple": sorted(
            set(current)
            | {
                "audit_events",
                "deals",
                "erasure_requests",
                "methodology_truth_dashboard_rows",
                "methodology_truth_dashboard_root_links",
            }
        ),
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(["task8-v22-zero-mutation-projection-v1", payload]),
    }


def build_advisory_lock_binding(v22: Mapping[str, Any]) -> dict[str, Any]:
    """Prove dashboard completion and erasure use one exact advisory key."""

    _validate_v22(v22)
    dashboard = _artifact_row(
        v22,
        "sql_authority_registry",
        field="authority_id",
        value=_AUTHORITY_IDS[0],
    )
    erasure = v22["erasure_execution_registry"]["rows"][0]
    dashboard_sql = " ".join(
        _decode_sql_tuple(
            dashboard["body_sql_utf8_hex_tuple"],
            code="TASK8_V22_CENSUS_AUTHORITY_SQL_INVALID",
        )
    )
    erasure_sql = " ".join(
        _decode_sql_tuple(
            erasure["current_body_sql_utf8_hex_tuple"],
            code="TASK8_V22_CENSUS_ERASURE_AUTHORITY_INVALID",
        )
    )
    pattern = re.compile(r"pg_catalog\.hashtextextended\(\s*'([^']+)'\|\|[^,]+,(\d+)\s*\)")
    dashboard_matches = pattern.findall(dashboard_sql)
    erasure_matches = pattern.findall(erasure_sql)
    if len(dashboard_matches) != 1 or len(erasure_matches) != 1:
        _fail("TASK8_V22_CENSUS_ADVISORY_LOCK_INVALID")
    if dashboard_matches[0] != erasure_matches[0]:
        _fail("TASK8_V22_CENSUS_ADVISORY_LOCK_INVALID")
    prefix, seed = dashboard_matches[0]
    payload = {
        "schema_version": "task8-v22-shared-advisory-lock-binding-v1",
        "dashboard_authority_id": _AUTHORITY_IDS[0],
        "erasure_execution_id": erasure["execution_id"],
        "prefix": prefix,
        "seed": int(seed),
        "dashboard_occurrence_count": len(dashboard_matches),
        "erasure_occurrence_count": len(erasure_matches),
    }
    return {
        **payload,
        "expected_sha256": _canonical_sha256(
            ["task8-v22-shared-advisory-lock-binding-v1", payload]
        ),
    }


def _case(
    case_id: str,
    authority_id: str,
    case_kind: str,
    semantic_case: str,
    execution_role: str,
    expected: str,
    *,
    expected_sqlstate: str | None = None,
    expected_reason_code: str | None = None,
    exercised_target_event_tuple: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    if expected_reason_code is None and authority_id == _AUTHORITY_IDS[0] and expected != "ACCEPT":
        expected_reason_code = _DASHBOARD_REJECTION_CODE
    if (
        expected_reason_code is None
        and authority_id == _AUTHORITY_IDS[1]
        and semantic_case in {"ZERO_DASHBOARD_NATIVE_ROOTS", "DIRECT_SQL_ROOT_MISMATCH"}
    ):
        expected_reason_code = _PARTIAL_EXACT_ONE_CODE
    return {
        "case_id": case_id,
        "authority_id": authority_id,
        "case_kind": case_kind,
        "semantic_case": semantic_case,
        "execution_role": execution_role,
        "expected": expected,
        "expected_sqlstate": expected_sqlstate,
        "expected_reason_code": expected_reason_code,
        "exercised_target_event_tuple": [
            list(coordinate) for coordinate in exercised_target_event_tuple
        ],
    }


def _global_dashboard_census_cases(dashboard_id: str) -> list[dict[str, Any]]:
    """Return the exact global-census rejection catalog without reading contract bytes."""

    return [
        _case(
            "dashboard-global-missing-row",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_DASHBOARD_ROW_MISSING",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-source-count-99",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_COVERAGE_SOURCE_COUNT_99",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-extra-noncoverage",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_EXTRA_NONCOVERAGE_ROW",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23503",
            expected_reason_code="",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
        _case(
            "dashboard-global-duplicate-coverage",
            dashboard_id,
            "ILLEGAL",
            "GLOBAL_DUPLICATE_COVERAGE_ROW",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23505",
            expected_reason_code="",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "INSERT"),),
        ),
    ]


@dataclass(frozen=True)
class _GlobalDashboardCensusPlan:
    semantic_case: str
    seed_coverage_row_count: int
    seed_financial_coverage_row_count: int
    omit_focus_output: bool
    extra_row_disposition: str | None


def _global_dashboard_census_plan(
    semantic_case: str,
    *,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> _GlobalDashboardCensusPlan:
    """Derive the negative fixture shape independently of PostgreSQL and artifact loading."""

    if coverage_row_count != 100 or financial_coverage_row_count != 79:
        _fail("TASK8_V22_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    if semantic_case == "GLOBAL_DASHBOARD_ROW_MISSING":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, True, None)
    if semantic_case == "GLOBAL_COVERAGE_SOURCE_COUNT_99":
        return _GlobalDashboardCensusPlan(semantic_case, 99, 78, False, None)
    if semantic_case == "GLOBAL_EXTRA_NONCOVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "NONCOVERAGE")
    if semantic_case == "GLOBAL_DUPLICATE_COVERAGE_ROW":
        return _GlobalDashboardCensusPlan(semantic_case, 100, 79, False, "DUPLICATE")
    _fail("TASK8_V22_CENSUS_GLOBAL_CASE_INVALID")


def build_census_case_manifest(v22: Mapping[str, Any]) -> dict[str, Any]:
    _validate_v22(v22)
    dashboard_id, partial_id, erasure_id = _AUTHORITY_IDS
    changed_authority_targets = {
        authority_id: _artifact_row(
            v22,
            "sql_authority_registry",
            field="authority_id",
            value=authority_id,
        )["target_tuple"]
        for authority_id in (dashboard_id, partial_id)
    }
    partial_focus_targets = [
        target
        for target in changed_authority_targets[partial_id]
        if target[0] in {"run_output_objects", "methodology_truth_dashboard_rows"}
    ]
    if partial_focus_targets != [
        ["run_output_objects", "ctrg_task7_run_output_objects_exact_one"],
        [
            "methodology_truth_dashboard_rows",
            "ctrg_task8_partial_native_root_dashboard_rows",
        ],
    ]:
        _fail("TASK8_V22_CENSUS_PARTIAL_TARGET_INVALID")

    def target_event_tuple(authority_id: str) -> list[list[str]]:
        return sorted(
            [
                [target, event]
                for target, _trigger in changed_authority_targets[authority_id]
                for event in ("INSERT", "UPDATE", "DELETE")
            ]
        )

    def trigger_guard(authority_id: str) -> Mapping[str, Any]:
        rows = [
            row
            for row in v22["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
            if row.get("authority_id") == authority_id
            and row.get("function_kind") == "TRIGGER_GUARD"
        ]
        if len(rows) != 1:
            _fail("TASK8_V22_CENSUS_GUARD_ACL_INVALID")
        return rows[0]

    dashboard_guard = trigger_guard(dashboard_id)
    partial_guard = trigger_guard(partial_id)
    dashboard_contract = v22["task8_fdd_contract"]["dashboard_contract"]
    coverage_row_count = dashboard_contract.get("coverage_row_count")
    if coverage_row_count != 100:
        _fail("TASK8_V22_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    financial_coverage_row_count = dashboard_contract.get("financial_row_count")
    if financial_coverage_row_count != 79:
        _fail("TASK8_V22_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    all_guard_rows = v22["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    changed_guard_rows = [
        row for row in all_guard_rows if row.get("authority_id") in _AUTHORITY_IDS[:2]
    ]
    installed_guard_rows = [
        row
        for row in all_guard_rows
        if row.get("install_scope") == "CURRENT_0038"
        or row.get("authority_id") in _AUTHORITY_IDS[:2]
    ]
    guard_owner_parent_select_acl_tuple = sorted(
        [
            [relation, sorted(columns)]
            for relation, columns in {
                relation: {
                    column
                    for row in installed_guard_rows
                    for row_relation, row_columns, _source in row["direct_read_column_tuple"]
                    if row_relation == relation
                    for column in row_columns
                }
                for relation in {
                    relation
                    for row in installed_guard_rows
                    for relation, _columns, _source in row["direct_read_column_tuple"]
                }
            }.items()
        ]
    )
    trigger_function_binding_tuple = sorted(
        [
            [authority_id, target, trigger, guard["function_signature"]]
            for authority_id, guard in (
                (dashboard_id, dashboard_guard),
                (partial_id, partial_guard),
            )
            for target, trigger in guard["target_trigger_tuple"]
        ]
    )
    dashboard_legal = [
        _case(
            "dashboard-gap-all-not-found",
            dashboard_id,
            "LEGAL",
            "ALL_NOT_FOUND_GAP",
            "application",
            "ACCEPT",
            exercised_target_event_tuple=(
                ("methodology_truth_dashboard_rows", "INSERT"),
                ("methodology_truth_dashboard_root_links", "INSERT"),
            ),
        ),
        _case(
            "dashboard-mixed-answered",
            dashboard_id,
            "LEGAL",
            "MIXED_FOUND_NOT_FOUND_ANSWERED",
            "application",
            "ACCEPT",
        ),
        _case(
            "dashboard-mixed-red-flag",
            dashboard_id,
            "LEGAL",
            "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG",
            "application",
            "ACCEPT",
        ),
        _case(
            "dashboard-all-found-answered",
            dashboard_id,
            "LEGAL",
            "ALL_FOUND_ANSWERED",
            "application",
            "ACCEPT",
        ),
    ]
    dashboard_illegal = [
        _case(
            "dashboard-required-formula-missing",
            dashboard_id,
            "ILLEGAL",
            "REQUIRED_FORMULA_MISSING",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-gap-with-found-result",
            dashboard_id,
            "ILLEGAL",
            "GAP_WITH_FOUND_RESULT",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-answered-without-evidence",
            dashboard_id,
            "ILLEGAL",
            "ANSWERED_WITHOUT_EVIDENCE_SANAD",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-red-flag-without-defect",
            dashboard_id,
            "ILLEGAL",
            "RED_FLAG_WITHOUT_ADVERSE_ROOT",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        *_global_dashboard_census_cases(dashboard_id),
    ]
    dashboard_direct = [
        _case(
            "dashboard-direct-insert-bypass",
            dashboard_id,
            "DIRECT_SQL",
            "DIRECT_INSERT_TRIGGER_BYPASS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
        ),
        _case(
            "dashboard-direct-update",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROW_UPDATE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "UPDATE"),),
        ),
        _case(
            "dashboard-direct-delete",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROW_DELETE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_rows", "DELETE"),),
        ),
        _case(
            "dashboard-root-link-direct-update",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROOT_LINK_UPDATE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_root_links", "UPDATE"),),
        ),
        _case(
            "dashboard-root-link-direct-delete",
            dashboard_id,
            "DIRECT_SQL",
            "APPEND_ONLY_ROOT_LINK_DELETE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("methodology_truth_dashboard_root_links", "DELETE"),),
        ),
    ]
    partial_legal = [
        _case(
            "partial-root-exactly-one",
            partial_id,
            "LEGAL",
            "EXACTLY_ONE_DASHBOARD_NATIVE_ROOT",
            "application",
            "ACCEPT",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        )
    ]
    partial_illegal = [
        _case(
            "partial-root-missing",
            partial_id,
            "ILLEGAL",
            "ZERO_DASHBOARD_NATIVE_ROOTS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(("run_output_objects", "INSERT"),),
        ),
        _case(
            "partial-root-duplicate",
            partial_id,
            "ILLEGAL",
            "MULTIPLE_DASHBOARD_NATIVE_ROOTS",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23505",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        ),
    ]
    partial_direct = [
        _case(
            "partial-root-direct-sql",
            partial_id,
            "DIRECT_SQL",
            "DIRECT_SQL_ROOT_MISMATCH",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="23514",
            exercised_target_event_tuple=(
                ("run_output_objects", "INSERT"),
                ("methodology_truth_dashboard_rows", "INSERT"),
            ),
        )
    ]
    erasure_legal = [
        _case(
            "erasure-current48-without-dashboard",
            erasure_id,
            "LEGAL",
            "CURRENT48_NO_DASHBOARD_ROWS",
            "erasure_executor",
            "ACCEPT",
        )
    ]
    erasure_illegal = [
        _case(
            "erasure-dashboard-refusal",
            erasure_id,
            "ILLEGAL",
            "DASHBOARD_ROWS_REQUIRE_FUTURE38",
            "erasure_executor",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate=_REFUSAL_SQLSTATE,
            expected_reason_code=_REFUSAL_CODE,
        )
    ]
    erasure_direct = [
        _case(
            "erasure-application-execute-denied",
            erasure_id,
            "DIRECT_SQL",
            "APPLICATION_RUNNER_EXECUTE",
            "application",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate="42501",
        ),
        _case(
            "erasure-executor-direct-refusal",
            erasure_id,
            "DIRECT_SQL",
            "EXECUTOR_CANNOT_BYPASS_DASHBOARD_REFUSAL",
            "erasure_executor",
            "REJECT_ZERO_MUTATION",
            expected_sqlstate=_REFUSAL_SQLSTATE,
            expected_reason_code=_REFUSAL_CODE,
        ),
    ]
    return {
        "schema_version": _SCHEMA,
        "setup_role": "admin_setup_only",
        "behavior_role_contract": {
            "guard_default": "application",
            "erasure_function": "erasure_executor",
            "admin_behavior_case_count": 0,
        },
        "dashboard_coverage_row_count": coverage_row_count,
        "dashboard_financial_coverage_row_count": financial_coverage_row_count,
        "authorities": [
            {
                "authority_ordinal": 1,
                "authority_id": dashboard_id,
                "target_event_tuple": target_event_tuple(dashboard_id),
                "legal_case_tuple": dashboard_legal,
                "illegal_case_tuple": dashboard_illegal,
                "direct_sql_case_tuple": dashboard_direct,
            },
            {
                "authority_ordinal": 2,
                "authority_id": partial_id,
                # The successor authority replaces the function reached by all
                # 18 existing 0038 triggers and adds the dashboard trigger.  Its
                # executable surface is therefore the complete 19 x I/U/D set;
                # the two focus targets below are only the semantic delta cases.
                "target_event_tuple": target_event_tuple(partial_id),
                "legal_case_tuple": partial_legal,
                "illegal_case_tuple": partial_illegal,
                "direct_sql_case_tuple": partial_direct,
            },
            {
                "authority_ordinal": 3,
                "authority_id": erasure_id,
                "target_event_tuple": [
                    [
                        "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)",
                        "EXECUTE",
                    ]
                ],
                "legal_case_tuple": erasure_legal,
                "illegal_case_tuple": erasure_illegal,
                "direct_sql_case_tuple": erasure_direct,
            },
        ],
        "catalog_assertions": {
            "installed_tenant_rls_table_count": 69,
            "dashboard_enable_rls": True,
            "dashboard_force_rls": True,
            "guard_owner_superuser": False,
            "guard_owner_bypassrls": False,
            "guard_owner_role_membership_count": 0,
            "erasure_owner_superuser": False,
            "erasure_owner_bypassrls": False,
            "erasure_owner_role_membership_count": 0,
            "application_superuser": False,
            "application_bypassrls": False,
            "erasure_executor_superuser": False,
            "erasure_executor_bypassrls": False,
            "erasure_executor_role_membership_count": 0,
            "guard_owner_dashboard_select_columns": sorted(
                {
                    column
                    for row in v22["sql_authority_registry"]["guard_function_acl_registry"][
                        "function_rows"
                    ]
                    if row.get("authority_id") in _AUTHORITY_IDS[:2]
                    for relation, columns, _source in row["direct_read_column_tuple"]
                    if relation == "methodology_truth_dashboard_rows"
                    for column in columns
                }
            ),
            "guard_owner_dashboard_insert": False,
            "guard_owner_dashboard_update": False,
            "guard_owner_dashboard_delete": False,
            "guard_owner_dashboard_write_privilege_count": 0,
            "erasure_owner_dashboard_select_columns": ["tenant_id", "deal_id"],
            "erasure_owner_dashboard_write_privilege_count": 0,
            "erasure_executor_dashboard_table_privilege_count": 0,
            "application_runner_execute_count": 0,
            "erasure_executor_runner_execute_count": 1,
            "erasure_executor_schema_usage": True,
            "erasure_executor_schema_create": False,
            "dashboard_policy_count": 2,
            "dashboard_policy_using_with_check_equal_count": 2,
            "dashboard_guard_function_owner_count": 3,
            "dashboard_guard_security_definer_count": sum(
                row["security_mode"] == "DEFINER" for row in changed_guard_rows
            ),
            "application_guard_function_execute_count": 0,
            "guard_owner_parent_select_acl_tuple": guard_owner_parent_select_acl_tuple,
            "guard_owner_parent_write_privilege_count": 0,
            "dashboard_trigger_event_tuple": target_event_tuple(dashboard_id),
            "partial_trigger_event_tuple": target_event_tuple(partial_id),
            "dashboard_trigger_event_coordinate_tuple": dashboard_guard[
                "target_event_coordinate_tuple"
            ],
            "partial_trigger_event_coordinate_tuple": partial_guard[
                "target_event_coordinate_tuple"
            ],
            "trigger_function_binding_tuple": trigger_function_binding_tuple,
            "partial_target_application_dml_reachability_tuple": sorted(
                [
                    [
                        target,
                        trigger,
                        True,
                        True,
                        True,
                        partial_guard["function_signature"],
                    ]
                    for target, trigger in partial_guard["target_trigger_tuple"]
                ]
            ),
            "partial_target_application_dml_reachability_count": 19,
            "trigger_catalog_application_role_match": True,
            "erasure_runner_owner_valid": True,
            "erasure_runner_security_definer": True,
            "current_erasure_relation_count": 48,
            "future_erasure_relation_count": 38,
            "complete_erasure_relation_count": 86,
        },
        "race_cases": [
            {
                "case_id": "hold-erasure-lock-then-dashboard-write",
                "first_actor": "erasure_executor",
                "second_actor": "application",
                "expected": "SECOND_BLOCKS_THEN_PARENT_REVALIDATION_REJECTS",
            },
            {
                "case_id": "hold-dashboard-lock-then-erasure",
                "first_actor": "application",
                "second_actor": "erasure_executor",
                "expected": "SECOND_BLOCKS_THEN_DASHBOARD_REFUSAL",
            },
        ],
    }


def validate_census_case_manifest(
    manifest: Mapping[str, Any],
    v22: Mapping[str, Any],
) -> None:
    expected = build_census_case_manifest(v22)
    if dict(manifest) != expected:
        _fail("TASK8_V22_CENSUS_MANIFEST_INVALID")
    cases = [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        _fail("TASK8_V22_CENSUS_CASE_ID_DUPLICATE")
    if any(case["execution_role"] == "admin" for case in cases):
        _fail("TASK8_V22_CENSUS_ADMIN_BEHAVIOR_FORBIDDEN")


@dataclass(frozen=True)
class _DisposableUrls:
    database_name: str
    admin: str
    application: str
    erasure: str
    application_role: str
    erasure_role: str


def _role_from_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.username:
        _fail("TASK8_V22_CENSUS_DATABASE_URL_INVALID")
    return unquote(parsed.username)


def _replace_database(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        _fail("TASK8_V22_CENSUS_DATABASE_URL_INVALID")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{quote(database_name, safe='')}",
            parsed.query,
            parsed.fragment,
        )
    )


def _disposable_urls(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
) -> _DisposableUrls:
    name = f"idis_task8_census_{uuid.uuid4().hex[:16]}"
    return _DisposableUrls(
        database_name=name,
        admin=_replace_database(admin_database_url, name),
        application=_replace_database(application_database_url, name),
        erasure=_replace_database(erasure_database_url, name),
        application_role=_role_from_url(application_database_url),
        erasure_role=_role_from_url(erasure_database_url),
    )


def _connect(database_url: str) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise Task8V22CensusError("TASK8_V22_CENSUS_PSYCOPG2_UNAVAILABLE") from exc
    return psycopg2.connect(database_url)


def _execute_statements(connection: Any, statements: list[str]) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        _fail("TASK8_V22_CENSUS_ROLE_INVALID")
    return f'"{value}"'


def _create_disposable_database(admin_database_url: str, database_name: str) -> None:
    connection = _connect(admin_database_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE {_quoted_identifier(database_name)} TEMPLATE template0"
            )
    finally:
        connection.close()


def _drop_disposable_database(admin_database_url: str, database_name: str) -> None:
    connection = _connect(admin_database_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                  FROM pg_stat_activity
                 WHERE datname = %s
                   AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            cursor.execute(f"DROP DATABASE IF EXISTS {_quoted_identifier(database_name)}")
    finally:
        connection.close()


class _SqlAlchemyCursor:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.exec_driver_sql(statement.replace("%", "%%"))


def _run_migrations_through_0038(engine: Any) -> None:
    """Drive the project's real Alembic environment on the disposable engine."""

    try:
        from alembic import command
        from alembic.config import Config

        import idis.persistence.migrations as migrations_package
    except ImportError as exc:
        raise Task8V22CensusError("TASK8_V22_CENSUS_MIGRATION_RUNNER_UNAVAILABLE") from exc
    package_file = getattr(migrations_package, "__file__", None)
    if not isinstance(package_file, str):
        _fail("TASK8_V22_CENSUS_MIGRATION_RUNNER_UNAVAILABLE")
    config = Config()
    config.set_main_option("script_location", str(Path(package_file).resolve().parent))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0038")


def _setup_disposable_schema(
    admin_database_url: str,
    *,
    application_role: str,
    erasure_role: str,
    v22: Mapping[str, Any],
    authority_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Install the real v21 source schema plus exact reviewed v22 authority bytes."""

    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise Task8V22CensusError("TASK8_V22_CENSUS_SQLALCHEMY_UNAVAILABLE") from exc

    future = build_future_table_scaffold(v22, application_role)
    application_grants = build_application_behavior_grants(v22, application_role)
    engine = create_engine(admin_database_url)
    try:
        # The exact v22 guards reference the real 0038 parent authorities.  An
        # empty/toy schema cannot prove their PostgreSQL or RLS semantics.
        _run_migrations_through_0038(engine)
        with engine.begin() as connection:
            for statement in application_grants["statement_tuple"]:
                connection.exec_driver_sql(statement)
            for statement in future["statement_tuple"]:
                connection.exec_driver_sql(statement.replace("%", "%%"))
            # 0038 installs the v21 runner under this frozen signature.  The
            # v22 body is CREATE FUNCTION by contract, so remove exactly that
            # predecessor before executing the reviewed replacement bytes.
            connection.exec_driver_sql(
                "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
            )
            binding = execute_hash_bound_authority_sql(
                _SqlAlchemyCursor(connection),
                authority_execution,
            )
    finally:
        engine.dispose()
    if binding["bundle_sha256"] != authority_execution["expected_sha256"]:
        _fail("TASK8_V22_CENSUS_AUTHORITY_BINDING_INVALID")
    return binding


def _set_tenant(cursor: Any, tenant_id: uuid.UUID) -> None:
    cursor.execute(
        "SELECT pg_catalog.set_config('idis.tenant_id', %s, true)",
        (str(tenant_id),),
    )


@dataclass(frozen=True)
class _CaseScope:
    tenant_id: uuid.UUID
    deal_id: uuid.UUID
    run_id: uuid.UUID
    lease_owner_id: uuid.UUID
    parent_step_id: uuid.UUID
    dashboard_step_id: uuid.UUID


@dataclass(frozen=True)
class _DashboardGraph:
    scope: _CaseScope
    coverage_record_id: str
    dashboard_row_id: uuid.UUID
    dashboard_output_object_id: uuid.UUID
    root_links: tuple[tuple[str, int, uuid.UUID, str, uuid.UUID], ...]
    root_projection_sha256: str
    row_disposition: str
    dashboard_output_ordinal: int


def _seed_scope(
    admin_url: str,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    run_id: uuid.UUID,
) -> _CaseScope:
    lease_owner_id = uuid.uuid4()
    parent_step_id = uuid.uuid4()
    dashboard_step_id = uuid.uuid4()
    connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SET LOCAL session_replication_role='replica';
                INSERT INTO public.tenants(tenant_id,name)
                VALUES (%s,'Task8 census tenant');
                INSERT INTO public.deals(deal_id,tenant_id,name,created_at,status)
                VALUES (%s,%s,'Task8 census deal',statement_timestamp(),'NEW');
                INSERT INTO public.runs(
                    run_id,tenant_id,deal_id,mode,status,lease_owner_id,
                    lease_fencing_token,lease_expires_at,last_heartbeat_at,
                    pipeline_contract_version,pipeline_contract_hash,
                    pipeline_resume_state
                ) VALUES (
                    %s,%s,%s,'FULL','RUNNING',%s,1,
                    statement_timestamp()+interval '1 hour',statement_timestamp(),
                    'task8-v22-census-v1',%s,'RESUMABLE'
                );
                INSERT INTO public.run_steps(
                    step_id,tenant_id,run_id,step_name,step_order,status,
                    pipeline_contract_version,pipeline_contract_hash,
                    semantic_rule_version,semantic_rule_hash
                ) VALUES
                    (%s,%s,%s,'TASK8_CENSUS_PARENT',100,'RUNNING',
                     'task8-v22-census-v1',%s,'task8-v22-census-v1',%s),
                    (%s,%s,%s,'METHODOLOGY_TRUTH_DASHBOARD',101,'RUNNING',
                     'task8-v22-census-v1',%s,'task8-v22-census-v1',%s)
                """,
                (
                    tenant_id,
                    deal_id,
                    tenant_id,
                    run_id,
                    tenant_id,
                    deal_id,
                    lease_owner_id,
                    "a" * 64,
                    parent_step_id,
                    tenant_id,
                    run_id,
                    "a" * 64,
                    "a" * 64,
                    dashboard_step_id,
                    tenant_id,
                    run_id,
                    "a" * 64,
                    "a" * 64,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return _CaseScope(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        lease_owner_id=lease_owner_id,
        parent_step_id=parent_step_id,
        dashboard_step_id=dashboard_step_id,
    )


def _set_worker_lease(cursor: Any, scope: _CaseScope) -> None:
    _set_tenant(cursor, scope.tenant_id)
    for name, value in (
        ("idis.worker_lease_tenant_id", scope.tenant_id),
        ("idis.worker_lease_deal_id", scope.deal_id),
        ("idis.worker_lease_run_id", scope.run_id),
        ("idis.worker_lease_owner_id", scope.lease_owner_id),
        ("idis.worker_lease_fencing_token", 1),
    ):
        cursor.execute(
            "SELECT pg_catalog.set_config(%s,%s,true)",
            (name, str(value)),
        )


def _insert_seed_output(
    cursor: Any,
    *,
    scope: _CaseScope,
    output_object_id: uuid.UUID,
    output_kind: str,
    output_ordinal: int,
) -> None:
    # The real 0038 semantic-identity UNIQUE is live even while trigger firing
    # is disabled for FK-compatible fixture ordering.  Use root-bound temporary
    # values, then replace them from the exact installed helper once every
    # native source row exists.
    placeholder_identity_sha256 = hashlib.sha256(
        b"task8-v22-native-identity-placeholder-v1\0" + output_object_id.bytes
    ).hexdigest()
    placeholder_output_sha256 = hashlib.sha256(
        b"task8-v22-native-output-placeholder-v1\0" + output_object_id.bytes
    ).hexdigest()
    cursor.execute(
        """
        INSERT INTO public.run_output_objects(
            output_object_id,tenant_id,deal_id,run_id,output_class,output_kind,
            output_schema_version,native_identity_sha256,native_output_sha256,
            pipeline_contract_version,pipeline_contract_hash,write_origin,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,'NATIVE_ROW',%s,'task8-v22-census-parent-v1',
            %s,%s,'task8-v22-census-v1',%s,'BACKFILL',NULL,0
        );
        INSERT INTO public.run_step_outputs(
            step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
            step_name,subrole,output_kind,output_ordinal,
            semantic_rule_version,semantic_rule_hash,summary_sha256,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,%s,%s,'TASK8_CENSUS_PARENT','__step__',%s,%s,
            'task8-v22-census-v1',%s,%s,%s,1
        )
        """,
        (
            output_object_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            output_kind,
            placeholder_identity_sha256,
            placeholder_output_sha256,
            "a" * 64,
            uuid.uuid4(),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            scope.parent_step_id,
            output_object_id,
            output_kind,
            output_ordinal,
            "a" * 64,
            "a" * 64,
            scope.lease_owner_id,
        ),
    )


def _refresh_seed_output_hashes(
    cursor: Any,
    *,
    scope: _CaseScope,
    roots: list[tuple[str, uuid.UUID, str]],
) -> None:
    """Replace bootstrap placeholders with hashes from the installed authority.

    Parent rows must exist before the Task-6 native rows because the real 0038
    schema retains their foreign keys.  The seed transaction therefore inserts
    placeholders with trigger execution disabled, creates the complete native
    graph, and then asks the *exact v22 helper installed from artifact bytes* for
    the values production validates.  No Python shadow recipe is used for the
    0038 source kinds.
    """

    for _root_kind, output_object_id, output_kind in roots:
        cursor.execute(
            """
            SELECT output_kind,expected_native_identity_sha256,
                   expected_native_output_sha256
              FROM public.idis_task7_current_native_hashes_v1(%s,%s,%s,%s)
            """,
            (
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                output_object_id,
            ),
        )
        expected = cursor.fetchall()
        if (
            len(expected) != 1
            or str(expected[0][0]) != output_kind
            or not all(
                isinstance(value, str) and _SHA256_RE.fullmatch(value) for value in expected[0][1:]
            )
        ):
            _fail("TASK8_V22_CENSUS_PARENT_NATIVE_HASH_INVALID")
        cursor.execute(
            """
            UPDATE public.run_output_objects
               SET native_identity_sha256=%s,
                   native_output_sha256=%s
             WHERE tenant_id=%s AND deal_id=%s AND run_id=%s
               AND output_object_id=%s AND output_kind=%s
            """,
            (
                expected[0][1],
                expected[0][2],
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                output_object_id,
                output_kind,
            ),
        )
        if cursor.rowcount != 1:
            _fail("TASK8_V22_CENSUS_PARENT_NATIVE_HASH_INVALID")


def _dashboard_case_shape(semantic_case: str) -> tuple[str, tuple[str, ...], bool, bool]:
    if semantic_case == "ALL_NOT_FOUND_GAP":
        return "GAP", ("EVIDENCE_NOT_FOUND",), False, False
    if semantic_case == "MIXED_FOUND_NOT_FOUND_ANSWERED":
        return "ANSWERED", ("EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"), False, False
    if semantic_case == "MIXED_FOUND_NOT_FOUND_WITH_DEFECT_RED_FLAG":
        return "RED_FLAG", ("EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"), False, True
    if semantic_case == "REQUIRED_FORMULA_MISSING":
        return "ANSWERED", ("EVIDENCE_FOUND",), True, False
    if semantic_case in {"GAP_WITH_FOUND_RESULT", "DIRECT_INSERT_TRIGGER_BYPASS"}:
        return "GAP", ("EVIDENCE_FOUND",), False, False
    if semantic_case == "ANSWERED_WITHOUT_EVIDENCE_SANAD":
        return "ANSWERED", ("EVIDENCE_FOUND",), False, False
    if semantic_case == "RED_FLAG_WITHOUT_ADVERSE_ROOT":
        return "RED_FLAG", ("EVIDENCE_FOUND",), False, False
    return "ANSWERED", ("EVIDENCE_FOUND",), False, False


def _seed_dashboard_graph(
    admin_url: str,
    *,
    scope: _CaseScope,
    semantic_case: str,
    methodology_type: str = "commercial_dd",
    methodology_question_ordinal: int = 1,
    dashboard_output_ordinal: int = 0,
    connection: Any | None = None,
    parent_output_ordinals: Counter[str] | None = None,
) -> _DashboardGraph:
    if methodology_type not in {"commercial_dd", "financial_dd"}:
        _fail("TASK8_V22_CENSUS_METHODOLOGY_TYPE_INVALID")
    if methodology_question_ordinal <= 0:
        _fail("TASK8_V22_CENSUS_METHODOLOGY_ORDINAL_INVALID")
    disposition, outcomes, calculation_required, adverse = _dashboard_case_shape(semantic_case)
    coverage_record_id = f"mc_{uuid.uuid4().hex[:24]}"
    question_id = uuid.uuid4()
    version_id = uuid.uuid4()
    selection_id = uuid.uuid4()
    dashboard_row_id = uuid.uuid4()
    dashboard_output_id = uuid.uuid4()
    methodology_version_id = f"task8-census-{methodology_type}-v1"
    methodology_question_id = (
        f"task8_census_{methodology_type}_question_{methodology_question_ordinal:04d}"
    )
    question_payload = {
        "required_calculations": [
            {"calc_type": "TASK8_CENSUS_BRIDGE", "required": calculation_required}
        ]
    }
    roots: list[tuple[str, uuid.UUID, str]] = []
    output_ordinals = parent_output_ordinals if parent_output_ordinals is not None else Counter()
    owns_connection = connection is None
    if connection is None:
        connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role='replica'")
            cursor.execute(
                """
                INSERT INTO public.methodology_questions(
                    tenant_id,methodology_question_record_id,
                    methodology_version_record_id,methodology_version_id,
                    methodology_question_id,methodology_id,methodology_type,
                    question_ordinal,section,source_sheet_or_section,
                    source_row_number,source_anchor_cell,source_sha256,
                    semantic_row_hash,question_payload_hash,question_payload
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,'Task8 census',
                    'Task8 census',NULL,NULL,%s,%s,%s,%s::jsonb
                );
                INSERT INTO public.methodology_coverage_records(
                    coverage_record_id,tenant_id,deal_id,run_id,selection_id,
                    methodology_version_record_id,methodology_version_id,
                    methodology_question_record_id,methodology_question_id,
                    methodology_type,status,reason_code,evidence_links,answer,
                    conflict_ids,defect_ids
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,'not_started',NULL,
                    '[]'::jsonb,NULL,ARRAY[]::text[],ARRAY[]::text[]
                )
                """,
                (
                    scope.tenant_id,
                    question_id,
                    version_id,
                    methodology_version_id,
                    methodology_question_id,
                    methodology_type,
                    methodology_type,
                    methodology_question_ordinal,
                    "a" * 64,
                    "a" * 64,
                    "a" * 64,
                    json.dumps(question_payload, separators=(",", ":")),
                    coverage_record_id,
                    scope.tenant_id,
                    scope.deal_id,
                    scope.run_id,
                    selection_id,
                    version_id,
                    methodology_version_id,
                    question_id,
                    methodology_question_id,
                    methodology_type,
                ),
            )
            for outcome_ordinal, outcome in enumerate(outcomes, start=1):
                task_id = uuid.uuid4()
                result_id = uuid.uuid4()
                attempt_id = uuid.uuid4()
                document_id = uuid.uuid4()
                span_id = uuid.uuid4()
                result_root = uuid.uuid4()
                _insert_seed_output(
                    cursor,
                    scope=scope,
                    output_object_id=result_root,
                    output_kind="METHODOLOGY_EXTRACTION_RESULT",
                    output_ordinal=output_ordinals["METHODOLOGY_EXTRACTION_RESULT"],
                )
                output_ordinals["METHODOLOGY_EXTRACTION_RESULT"] += 1
                roots.append(("RESULT", result_root, "METHODOLOGY_EXTRACTION_RESULT"))
                gap = {"code": "GOVERNED_EVIDENCE_GAP"} if outcome == "EVIDENCE_NOT_FOUND" else None
                cursor.execute(
                    """
                    INSERT INTO public.methodology_extraction_tasks(
                        task_record_id,tenant_id,deal_id,run_id,selection_id,
                        coverage_record_id,methodology_version_record_id,
                        methodology_version_id,methodology_question_record_id,
                        methodology_question_id,methodology_type,extraction_task_id,
                        document_id,document_classification_id,
                        document_classification_sha256,planner_contract_version,
                        task_contract_version,task_payload,task_input_sha256,
                        candidate_span_set_sha256,status
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,
                        'task8_census_document',%s,'task8-census-planner-v1',
                        'task8-census-task-v1',%s::jsonb,%s,%s,'READY'
                    );
                    INSERT INTO public.methodology_extraction_results(
                        result_id,tenant_id,deal_id,run_id,task_record_id,attempt_id,
                        task_input_sha256,outcome,result_payload,result_sha256,
                        searched_document_ids,searched_span_ids,searched_scope_sha256,
                        provider_provenance_sha256,gap_finding,run_output_object_id
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                        ARRAY[%s]::uuid[],ARRAY[%s]::uuid[],%s,%s,%s::jsonb,%s
                    )
                    """,
                    (
                        task_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        selection_id,
                        coverage_record_id,
                        version_id,
                        methodology_version_id,
                        question_id,
                        methodology_question_id,
                        methodology_type,
                        f"task8_census_task_{dashboard_output_ordinal}_{outcome_ordinal}",
                        document_id,
                        "a" * 64,
                        json.dumps(
                            {"coverage_record_id": coverage_record_id},
                            separators=(",", ":"),
                        ),
                        "a" * 64,
                        "a" * 64,
                        result_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        task_id,
                        attempt_id,
                        "a" * 64,
                        outcome,
                        json.dumps({"outcome": outcome}, separators=(",", ":")),
                        "a" * 64,
                        document_id,
                        span_id,
                        "a" * 64,
                        "a" * 64,
                        None if gap is None else json.dumps(gap, separators=(",", ":")),
                        result_root,
                    ),
                )
                if outcome != "EVIDENCE_FOUND":
                    continue
                factual_id = uuid.uuid4()
                factual_record_id = uuid.uuid4()
                claim_link_id = uuid.uuid4()
                evidence_id = uuid.uuid4()
                sanad_id = uuid.uuid4()
                evidence_link_id = uuid.uuid4()
                sanad_link_id = uuid.uuid4()
                cursor.execute(
                    """
                    INSERT INTO public.methodology_result_factual_outputs(
                        factual_output_record_id,tenant_id,deal_id,run_id,result_id,
                        task_record_id,output_ordinal,factual_output_id,
                        typed_output_payload,typed_output_sha256,
                        accepted_source_span_ids,accepted_locator_sha256s,
                        accepted_locator_set_sha256
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,1,%s,%s::jsonb,%s,
                        ARRAY[%s]::uuid[],ARRAY[%s]::text[],%s
                    )
                    """,
                    (
                        factual_record_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        task_id,
                        factual_id,
                        json.dumps({"value": "task8-census"}, separators=(",", ":")),
                        "a" * 64,
                        span_id,
                        "b" * 64,
                        "a" * 64,
                    ),
                )
                if semantic_case == "ANSWERED_WITHOUT_EVIDENCE_SANAD":
                    continue
                evidence_root = uuid.uuid4()
                sanad_root = uuid.uuid4()
                for output_id, output_kind in (
                    (evidence_root, "METHODOLOGY_EVIDENCE_LINK"),
                    (sanad_root, "METHODOLOGY_SANAD_LINK"),
                ):
                    _insert_seed_output(
                        cursor,
                        scope=scope,
                        output_object_id=output_id,
                        output_kind=output_kind,
                        output_ordinal=output_ordinals[output_kind],
                    )
                    output_ordinals[output_kind] += 1
                cursor.execute(
                    """
                    INSERT INTO public.methodology_result_evidence_links(
                        evidence_link_id,tenant_id,deal_id,run_id,result_id,
                        factual_output_record_id,claim_link_id,evidence_id,document_id,
                        source_span_id,locator_sha256,run_output_object_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                    INSERT INTO public.methodology_result_sanad_links(
                        sanad_link_id,tenant_id,deal_id,run_id,result_id,
                        factual_output_record_id,claim_link_id,sanad_id,
                        primary_evidence_id,run_output_object_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        evidence_link_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        factual_record_id,
                        claim_link_id,
                        evidence_id,
                        document_id,
                        span_id,
                        "a" * 64,
                        evidence_root,
                        sanad_link_id,
                        scope.tenant_id,
                        scope.deal_id,
                        scope.run_id,
                        result_id,
                        factual_record_id,
                        claim_link_id,
                        sanad_id,
                        evidence_id,
                        sanad_root,
                    ),
                )
                roots.extend(
                    (
                        ("EVIDENCE", evidence_root, "METHODOLOGY_EVIDENCE_LINK"),
                        ("SANAD", sanad_root, "METHODOLOGY_SANAD_LINK"),
                    )
                )
                if adverse:
                    defect_root = uuid.uuid4()
                    defect_link_id = uuid.uuid4()
                    _insert_seed_output(
                        cursor,
                        scope=scope,
                        output_object_id=defect_root,
                        output_kind="METHODOLOGY_DEFECT_LINK",
                        output_ordinal=output_ordinals["METHODOLOGY_DEFECT_LINK"],
                    )
                    output_ordinals["METHODOLOGY_DEFECT_LINK"] += 1
                    cursor.execute(
                        """
                        INSERT INTO public.methodology_result_defect_links(
                            defect_link_id,tenant_id,deal_id,run_id,result_id,
                            factual_output_record_id,sanad_link_id,defect_id,
                            run_output_object_id
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            defect_link_id,
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            result_id,
                            factual_record_id,
                            sanad_link_id,
                            uuid.uuid4(),
                            defect_root,
                        ),
                    )
                    roots.append(
                        (
                            "ADVERSE_CONTRADICTION",
                            defect_root,
                            "METHODOLOGY_DEFECT_LINK",
                        )
                    )
            _refresh_seed_output_hashes(cursor, scope=scope, roots=roots)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()

    root_links: list[tuple[str, int, uuid.UUID, str, uuid.UUID]] = []
    for root_kind in sorted({row[0] for row in roots}):
        matching = sorted(
            (row for row in roots if row[0] == root_kind),
            key=lambda row: (row[1].int, row[2]),
        )
        for ordinal, (_kind, output_id, output_kind) in enumerate(matching):
            root_links.append((root_kind, ordinal, output_id, output_kind, uuid.uuid4()))
    root_links.sort(key=lambda row: (row[0], row[1], row[2].int, row[4].int))
    projection_preimage = json.dumps(
        [
            "methodology-truth-dashboard-root-projection-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            str(dashboard_row_id),
            str(dashboard_output_id),
            coverage_record_id,
            disposition,
            len(root_links),
            [
                [kind, ordinal, str(output_id), output_kind, str(link_id)]
                for kind, ordinal, output_id, output_kind, link_id in root_links
            ],
        ],
        separators=(",", ":"),
    )
    return _DashboardGraph(
        scope=scope,
        coverage_record_id=coverage_record_id,
        dashboard_row_id=dashboard_row_id,
        dashboard_output_object_id=dashboard_output_id,
        root_links=tuple(root_links),
        root_projection_sha256=hashlib.sha256(projection_preimage.encode("utf-8")).hexdigest(),
        row_disposition=disposition,
        dashboard_output_ordinal=dashboard_output_ordinal,
    )


def _seed_dashboard_batch(
    admin_url: str,
    *,
    scope: _CaseScope,
    focus_semantic_case: str,
    coverage_row_count: int,
    financial_coverage_row_count: int,
) -> tuple[_DashboardGraph, ...]:
    """Seed one exact scoped coverage census in a single disposable transaction."""

    commercial_coverage_row_count = coverage_row_count - financial_coverage_row_count
    if (
        coverage_row_count <= 0
        or financial_coverage_row_count <= 0
        or commercial_coverage_row_count <= 0
    ):
        _fail("TASK8_V22_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    connection = _connect(admin_url)
    parent_output_ordinals: Counter[str] = Counter()
    try:
        graphs = tuple(
            _seed_dashboard_graph(
                admin_url,
                scope=scope,
                semantic_case=(focus_semantic_case if ordinal == 0 else "ALL_NOT_FOUND_GAP"),
                methodology_type=(
                    "commercial_dd" if ordinal < commercial_coverage_row_count else "financial_dd"
                ),
                methodology_question_ordinal=(
                    ordinal + 1
                    if ordinal < commercial_coverage_row_count
                    else ordinal - commercial_coverage_row_count + 1
                ),
                dashboard_output_ordinal=ordinal,
                connection=connection,
                parent_output_ordinals=parent_output_ordinals,
            )
            for ordinal in range(coverage_row_count)
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return graphs


def _insert_dashboard_output_and_row(
    cursor: Any,
    graph: _DashboardGraph,
    *,
    include_row: bool = True,
    corrupt_native_hashes: bool = False,
) -> None:
    scope = graph.scope
    row_payload_sha256 = "a" * 64
    pipeline_contract_version = "task8-v22-census-v1"
    pipeline_contract_hash = "a" * 64
    output_kind = "METHODOLOGY_TRUTH_DASHBOARD_ROW"
    native_identity_sha256 = _canonical_sha256(
        [
            "native-identity-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            output_kind,
            [str(graph.dashboard_row_id)],
        ]
    )
    native_output_sha256 = _canonical_sha256(
        [
            "native-output-v1",
            str(scope.tenant_id),
            str(scope.deal_id),
            str(scope.run_id),
            pipeline_contract_version,
            pipeline_contract_hash,
            output_kind,
            [str(graph.dashboard_row_id)],
            [
                graph.coverage_record_id,
                graph.root_projection_sha256,
                graph.row_disposition,
                row_payload_sha256,
            ],
        ]
    )
    if corrupt_native_hashes:
        native_output_sha256 = "f" * 64
    cursor.execute(
        """
        INSERT INTO public.run_output_objects(
            output_object_id,tenant_id,deal_id,run_id,output_class,output_kind,
            output_schema_version,native_identity_sha256,native_output_sha256,
            pipeline_contract_version,pipeline_contract_hash,write_origin,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,'NATIVE_ROW','METHODOLOGY_TRUTH_DASHBOARD_ROW',
            'task8-methodology-truth-dashboard-row-v1',%s,%s,
            %s,%s,'RUNTIME',%s,1
        );
        INSERT INTO public.run_step_outputs(
            step_output_id,tenant_id,deal_id,run_id,step_id,output_object_id,
            step_name,subrole,output_kind,output_ordinal,
            semantic_rule_version,semantic_rule_hash,summary_sha256,
            lease_owner_id,lease_fencing_token
        ) VALUES (
            %s,%s,%s,%s,%s,%s,'METHODOLOGY_TRUTH_DASHBOARD','__step__',
            'METHODOLOGY_TRUTH_DASHBOARD_ROW',%s,'task8-v22-census-v1',%s,%s,%s,1
        )
        """,
        (
            graph.dashboard_output_object_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            native_identity_sha256,
            native_output_sha256,
            pipeline_contract_version,
            pipeline_contract_hash,
            scope.lease_owner_id,
            uuid.uuid4(),
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            scope.dashboard_step_id,
            graph.dashboard_output_object_id,
            graph.dashboard_output_ordinal,
            row_payload_sha256,
            "a" * 64,
            scope.lease_owner_id,
        ),
    )
    if not include_row:
        return
    cursor.execute(
        """
        INSERT INTO public.methodology_truth_dashboard_rows(
            dashboard_row_id,tenant_id,deal_id,run_id,run_output_object_id,
            lease_owner_id,lease_fencing_token,coverage_record_id,
            root_projection_sha256,row_disposition,row_payload_sha256
        ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
        """,
        (
            graph.dashboard_row_id,
            scope.tenant_id,
            scope.deal_id,
            scope.run_id,
            graph.dashboard_output_object_id,
            scope.lease_owner_id,
            graph.coverage_record_id,
            graph.root_projection_sha256,
            graph.row_disposition,
            row_payload_sha256,
        ),
    )
    for kind, ordinal, output_id, output_kind, link_id in graph.root_links:
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_root_links(
                dashboard_root_link_id,tenant_id,deal_id,run_id,dashboard_row_id,
                dashboard_output_object_id,root_kind,root_ordinal,
                root_output_object_id,root_output_object_kind,
                lease_owner_id,lease_fencing_token
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (
                link_id,
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_row_id,
                graph.dashboard_output_object_id,
                kind,
                ordinal,
                output_id,
                output_kind,
                scope.lease_owner_id,
            ),
        )


def _insert_dashboard_batch(
    cursor: Any,
    graphs: tuple[_DashboardGraph, ...],
    *,
    include_focus_row: bool = True,
    omit_focus_output: bool = False,
    corrupt_focus_native_hashes: bool = False,
) -> None:
    if not graphs or tuple(graph.dashboard_output_ordinal for graph in graphs) != tuple(
        range(len(graphs))
    ):
        _fail("TASK8_V22_CENSUS_DASHBOARD_BATCH_INVALID")
    for ordinal, graph in enumerate(graphs):
        if omit_focus_output and ordinal == 0:
            continue
        _insert_dashboard_output_and_row(
            cursor,
            graph,
            include_row=include_focus_row or ordinal != 0,
            corrupt_native_hashes=corrupt_focus_native_hashes and ordinal == 0,
        )


def _additional_dashboard_graph(
    graph: _DashboardGraph,
    *,
    coverage_record_id: str,
    dashboard_output_ordinal: int,
) -> _DashboardGraph:
    """Build a shape-valid extra row used only to reach an immediate FK/UNIQUE guard."""

    dashboard_row_id = uuid.uuid4()
    dashboard_output_object_id = uuid.uuid4()
    root_projection_sha256 = _canonical_sha256(
        [
            "methodology-truth-dashboard-root-projection-v1",
            str(graph.scope.tenant_id),
            str(graph.scope.deal_id),
            str(graph.scope.run_id),
            str(dashboard_row_id),
            str(dashboard_output_object_id),
            coverage_record_id,
            "GAP",
            0,
            [],
        ]
    )
    return replace(
        graph,
        coverage_record_id=coverage_record_id,
        dashboard_row_id=dashboard_row_id,
        dashboard_output_object_id=dashboard_output_object_id,
        root_links=(),
        root_projection_sha256=root_projection_sha256,
        row_disposition="GAP",
        dashboard_output_ordinal=dashboard_output_ordinal,
    )


def _seed_erasure_request(admin_url: str, scope: _CaseScope) -> uuid.UUID:
    try:
        from idis.compliance.task7_erasure import (
            build_erasure_audit_event,
            derive_attempt_lineage,
            object_target_set_sha256,
        )
    except ImportError as exc:
        raise Task8V22CensusError("TASK8_V22_CENSUS_ERASURE_FIXTURE_UNAVAILABLE") from exc
    request_id = uuid.uuid4()
    actor_id = "task8-census-admin"
    lineage = derive_attempt_lineage(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        request_id=str(request_id),
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    occurred_at = datetime.now(UTC)
    event = build_erasure_audit_event(
        tenant_id=str(scope.tenant_id),
        deal_id=str(scope.deal_id),
        actor_id=actor_id,
        occurred_at=occurred_at,
        lineage=lineage,
        event_type="task7.erasure.attempted",
        object_target_set_sha256=object_target_set_sha256(
            tenant_id=str(scope.tenant_id),
            deal_id=str(scope.deal_id),
            ordered_uris=(),
        ),
        counts=None,
    )
    connection = _connect(admin_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role='replica'")
            cursor.execute(
                """
                INSERT INTO public.erasure_requests(
                    tenant_id,request_id,deal_id,status,requested_by,requested_at,
                    reason_hash,reason_length
                ) VALUES (%s,%s,%s,'REQUESTED',%s,%s,%s,1);
                INSERT INTO public.audit_events(
                    event_id,tenant_id,occurred_at,event_type,request_id,
                    idempotency_key,event
                ) VALUES (%s,%s,%s,'task7.erasure.attempted',%s,%s,%s::jsonb)
                """,
                (
                    scope.tenant_id,
                    request_id,
                    scope.deal_id,
                    actor_id,
                    occurred_at,
                    "a" * 64,
                    uuid.UUID(event["event_id"]),
                    scope.tenant_id,
                    occurred_at,
                    str(request_id),
                    event["event_id"],
                    json.dumps(event, separators=(",", ":")),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return request_id


def _call_erasure(
    cursor: Any,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    request_id: uuid.UUID,
) -> None:
    cursor.execute(
        """
        SELECT public.idis_task7_execute_approved_erasure(%s, %s, %s)
        """,
        # The frozen function's positional contract is request, tenant, deal;
        # this matches the production dedicated-connection adapter.
        (request_id, tenant_id, deal_id),
    )


def _insert_future_rls_probe(
    cursor: Any,
    *,
    graph: _DashboardGraph,
    relation: str,
) -> None:
    scope = graph.scope
    if relation == "methodology_truth_dashboard_rows":
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_rows(
                dashboard_row_id,tenant_id,deal_id,run_id,run_output_object_id,
                lease_owner_id,lease_fencing_token,coverage_record_id,
                root_projection_sha256,row_disposition,row_payload_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
            """,
            (
                uuid.uuid4(),
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_output_object_id,
                scope.lease_owner_id,
                graph.coverage_record_id,
                graph.root_projection_sha256,
                graph.row_disposition,
                "a" * 64,
            ),
        )
        return
    if relation == "methodology_truth_dashboard_root_links":
        kind, ordinal, output_id, output_kind, _link_id = graph.root_links[0]
        cursor.execute(
            """
            INSERT INTO public.methodology_truth_dashboard_root_links(
                dashboard_root_link_id,tenant_id,deal_id,run_id,dashboard_row_id,
                dashboard_output_object_id,root_kind,root_ordinal,
                root_output_object_id,root_output_object_kind,
                lease_owner_id,lease_fencing_token
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (
                uuid.uuid4(),
                scope.tenant_id,
                scope.deal_id,
                scope.run_id,
                graph.dashboard_row_id,
                graph.dashboard_output_object_id,
                kind,
                ordinal + 1000,
                output_id,
                output_kind,
                scope.lease_owner_id,
            ),
        )
        return
    _fail("TASK8_V22_CENSUS_RLS_RELATION_INVALID")


def _tenant_deal_projection(
    admin_database_url: str,
    *,
    tenant_id: uuid.UUID,
    deal_id: uuid.UUID,
    projection_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Hash the complete reviewed erasure/control scope via the setup role."""

    relation_tuple = projection_manifest.get("control_relation_tuple")
    current_relation_tuple = projection_manifest.get("current_relation_tuple")
    if (
        not isinstance(relation_tuple, list)
        or not isinstance(current_relation_tuple, list)
        or not set(current_relation_tuple).issubset(relation_tuple)
    ):
        _fail("TASK8_V22_CENSUS_PROJECTION_MANIFEST_INVALID")
    connection = _connect(admin_database_url)
    try:
        with connection.cursor() as cursor:
            rows: list[list[Any]] = []
            for table_name in relation_tuple:
                if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name) is None:
                    _fail("TASK8_V22_CENSUS_PROJECTION_RELATION_INVALID")
                cursor.execute(
                    """
                    SELECT array_agg(column_name::text ORDER BY ordinal_position)::text[]
                      FROM information_schema.columns
                     WHERE table_schema='public' AND table_name=%s
                    """,
                    (table_name,),
                )
                columns = set(cursor.fetchone()[0] or ())
                if not columns:
                    _fail("TASK8_V22_CENSUS_PROJECTION_RELATION_MISSING")
                if {"tenant_id", "deal_id"}.issubset(columns):
                    scope_sql = "scoped.tenant_id=%s AND scoped.deal_id=%s"
                    from_sql = f"public.{table_name} AS scoped"
                elif table_name == "calc_sanads" and {"tenant_id", "calc_id"}.issubset(columns):
                    from_sql = (
                        "public.calc_sanads AS scoped JOIN "
                        "public.deterministic_calculations AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.calc_id=scoped.calc_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "human_gate_actions" and {
                    "tenant_id",
                    "gate_id",
                }.issubset(columns):
                    from_sql = (
                        "public.human_gate_actions AS scoped JOIN "
                        "public.human_gates AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.gate_id=scoped.gate_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "run_steps" and {"tenant_id", "run_id"}.issubset(columns):
                    from_sql = (
                        "public.run_steps AS scoped JOIN public.runs AS parent ON "
                        "parent.tenant_id=scoped.tenant_id AND parent.run_id=scoped.run_id"
                    )
                    scope_sql = "parent.tenant_id=%s AND parent.deal_id=%s"
                elif table_name == "audit_events" and {"tenant_id", "event"}.issubset(columns):
                    from_sql = "public.audit_events AS scoped"
                    scope_sql = "scoped.tenant_id=%s AND scoped.event->'resource'->>'deal_id'=%s"
                else:
                    _fail(f"TASK8_V22_CENSUS_PROJECTION_SCOPE_UNPROVEN:{table_name}")
                cursor.execute(
                    f"""
                    SELECT count(*),
                           COALESCE(
                             jsonb_agg(
                               to_jsonb(scoped)
                               ORDER BY to_jsonb(scoped)::text COLLATE "C"
                             )::text,
                             '[]'
                           )
                      FROM (
                        SELECT scoped.*
                          FROM {from_sql}
                         WHERE {scope_sql}
                      ) AS scoped
                    """,
                    (tenant_id, str(deal_id) if table_name == "audit_events" else deal_id),
                )
                count, canonical_rows = cursor.fetchone()
                rows.append(
                    [
                        table_name,
                        int(count),
                        hashlib.sha256(str(canonical_rows).encode("utf-8")).hexdigest(),
                    ]
                )
        connection.rollback()
    finally:
        connection.close()
    return {
        "relation_count": len(rows),
        "row_count": sum(row[1] for row in rows),
        "projection_sha256": _canonical_sha256(
            [
                "task8-v22-tenant-deal-projection-v1",
                projection_manifest["expected_sha256"],
                str(tenant_id),
                str(deal_id),
                rows,
            ]
        ),
    }


def _run_rls_behavior_matrix(
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, list[dict[str, Any]]]:
    scope = _seed_scope(urls.admin, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    graphs = _seed_dashboard_batch(
        urls.admin,
        scope=scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    graph = graphs[0]
    baseline = _connect(urls.application)
    try:
        with baseline.cursor() as cursor:
            _set_worker_lease(cursor, scope)
            _insert_dashboard_batch(cursor, graphs)
        baseline.commit()
    finally:
        baseline.close()

    relations = (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    )
    results: list[dict[str, Any]] = []
    for mode, tenant_value in (
        ("WRONG_TENANT", str(uuid.uuid4())),
        ("UNSET_TENANT", ""),
    ):
        read_connection = _connect(urls.application)
        try:
            with read_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_catalog.set_config('idis.tenant_id',%s,true)",
                    (tenant_value,),
                )
                for relation in relations:
                    cursor.execute(f"SELECT count(*) FROM public.{relation}")
                    visible = int(cursor.fetchone()[0])
                    results.append(
                        {
                            "mode": mode,
                            "relation": relation,
                            "operation": "READ",
                            "visible_row_count": visible,
                            "passed": visible == 0,
                        }
                    )
            read_connection.rollback()
        finally:
            read_connection.close()

        for relation in relations:
            pre = _tenant_deal_projection(
                urls.admin,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                projection_manifest=projection_manifest,
            )
            write_connection = _connect(urls.application)
            sqlstate: str | None = None
            try:
                with write_connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_catalog.set_config('idis.tenant_id',%s,true)",
                        (tenant_value,),
                    )
                    _insert_future_rls_probe(cursor, graph=graph, relation=relation)
                write_connection.commit()
            except Exception as exc:
                sqlstate = getattr(exc, "pgcode", None)
                write_connection.rollback()
            finally:
                write_connection.close()
            post = _tenant_deal_projection(
                urls.admin,
                tenant_id=scope.tenant_id,
                deal_id=scope.deal_id,
                projection_manifest=projection_manifest,
            )
            results.append(
                {
                    "mode": mode,
                    "relation": relation,
                    "operation": "WRITE",
                    "sqlstate": sqlstate,
                    "zero_mutation": pre == post,
                    "passed": sqlstate == "42501" and pre == post,
                }
            )

    same_tenant = _connect(urls.application)
    try:
        with same_tenant.cursor() as cursor:
            _set_tenant(cursor, scope.tenant_id)
            for relation in relations:
                cursor.execute(f"SELECT count(*) FROM public.{relation}")
                visible = int(cursor.fetchone()[0])
                results.append(
                    {
                        "mode": "SAME_TENANT",
                        "relation": relation,
                        "operation": "READ",
                        "visible_row_count": visible,
                        "passed": visible > 0,
                    }
                )
        same_tenant.rollback()
    finally:
        same_tenant.close()
    return all(row["passed"] for row in results), results


def _execute_behavior_case(
    case: Mapping[str, Any],
    *,
    urls: _DisposableUrls,
    projection_manifest: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> dict[str, Any]:
    tenant_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    run_id = uuid.uuid4()
    scope = _seed_scope(urls.admin, tenant_id, deal_id, run_id)
    role = case["execution_role"]
    rejected = case["expected"] != "ACCEPT"
    semantic = str(case["semantic_case"])
    graph: _DashboardGraph | None = None
    graphs: tuple[_DashboardGraph, ...] = ()
    global_dashboard_plan: _GlobalDashboardCensusPlan | None = None
    request_id: uuid.UUID | None = None

    if case["authority_id"] in _AUTHORITY_IDS[:2]:
        if semantic.startswith("GLOBAL_"):
            global_dashboard_plan = _global_dashboard_census_plan(
                semantic,
                coverage_row_count=dashboard_coverage_row_count,
                financial_coverage_row_count=dashboard_financial_coverage_row_count,
            )
        graph_semantic = (
            "ALL_FOUND_ANSWERED"
            if case["authority_id"] == _AUTHORITY_IDS[1] or global_dashboard_plan is not None
            else semantic
        )
        seed_coverage_row_count = (
            global_dashboard_plan.seed_coverage_row_count
            if global_dashboard_plan is not None
            else dashboard_coverage_row_count
        )
        seed_financial_coverage_row_count = (
            global_dashboard_plan.seed_financial_coverage_row_count
            if global_dashboard_plan is not None
            else dashboard_financial_coverage_row_count
        )
        graphs = _seed_dashboard_batch(
            urls.admin,
            scope=scope,
            focus_semantic_case=graph_semantic,
            coverage_row_count=seed_coverage_row_count,
            financial_coverage_row_count=seed_financial_coverage_row_count,
        )
        graph = graphs[0]
    elif case["authority_id"] == _AUTHORITY_IDS[2]:
        if semantic in {
            "DASHBOARD_ROWS_REQUIRE_FUTURE38",
            "EXECUTOR_CANNOT_BYPASS_DASHBOARD_REFUSAL",
        }:
            graphs = _seed_dashboard_batch(
                urls.admin,
                scope=scope,
                focus_semantic_case="ALL_FOUND_ANSWERED",
                coverage_row_count=dashboard_coverage_row_count,
                financial_coverage_row_count=dashboard_financial_coverage_row_count,
            )
            graph = graphs[0]
            app_connection = _connect(urls.application)
            try:
                with app_connection.cursor() as app_cursor:
                    _set_worker_lease(app_cursor, scope)
                    _insert_dashboard_batch(app_cursor, graphs)
                app_connection.commit()
            finally:
                app_connection.close()
        request_id = _seed_erasure_request(urls.admin, scope)

    pre_projection = None
    if rejected:
        pre_projection = _tenant_deal_projection(
            urls.admin,
            tenant_id=tenant_id,
            deal_id=deal_id,
            projection_manifest=projection_manifest,
        )
    database_url = urls.application if role == "application" else urls.erasure
    connection = _connect(database_url)
    observed_sqlstate: str | None = None
    observed_reason: str | None = None
    observed_current_user: str | None = None
    observed_session_user: str | None = None
    expected_database_role = urls.application_role if role == "application" else urls.erasure_role
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user,session_user")
            observed_current_user, observed_session_user = map(str, cursor.fetchone())
            if (
                observed_current_user != expected_database_role
                or observed_session_user != expected_database_role
            ):
                _fail("TASK8_V22_CENSUS_BEHAVIOR_ROLE_INVALID")
            if role == "application":
                _set_worker_lease(cursor, scope)
            if case["authority_id"] == _AUTHORITY_IDS[0]:
                if graph is None:
                    _fail("TASK8_V22_CENSUS_FIXTURE_INVALID")
                if global_dashboard_plan is not None and global_dashboard_plan.omit_focus_output:
                    _insert_dashboard_batch(cursor, graphs, omit_focus_output=True)
                elif global_dashboard_plan is not None and (
                    global_dashboard_plan.extra_row_disposition is None
                ):
                    _insert_dashboard_batch(cursor, graphs)
                elif global_dashboard_plan is not None:
                    _insert_dashboard_batch(cursor, graphs)
                    extra_coverage_record_id = (
                        graph.coverage_record_id
                        if global_dashboard_plan.extra_row_disposition == "DUPLICATE"
                        else f"mc_{uuid.uuid4().hex[:24]}"
                    )
                    extra_graph = _additional_dashboard_graph(
                        graph,
                        coverage_record_id=extra_coverage_record_id,
                        dashboard_output_ordinal=len(graphs),
                    )
                    _insert_dashboard_output_and_row(cursor, extra_graph)
                elif semantic in {
                    "APPEND_ONLY_ROW_UPDATE",
                    "APPEND_ONLY_ROW_DELETE",
                    "APPEND_ONLY_ROOT_LINK_UPDATE",
                    "APPEND_ONLY_ROOT_LINK_DELETE",
                }:
                    _insert_dashboard_batch(cursor, graphs)
                    connection.commit()
                    if rejected:
                        pre_projection = _tenant_deal_projection(
                            urls.admin,
                            tenant_id=tenant_id,
                            deal_id=deal_id,
                            projection_manifest=projection_manifest,
                        )
                    with connection.cursor() as cursor2:
                        _set_worker_lease(cursor2, scope)
                        if semantic == "APPEND_ONLY_ROW_UPDATE":
                            cursor2.execute(
                                """
                                UPDATE public.methodology_truth_dashboard_rows
                                   SET row_disposition = 'GAP'
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE"
                            )
                        elif semantic == "APPEND_ONLY_ROW_DELETE":
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_root_links
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_rows
                                 WHERE dashboard_row_id = %s
                                """,
                                (graph.dashboard_row_id,),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE"
                            )
                        elif semantic == "APPEND_ONLY_ROOT_LINK_UPDATE":
                            cursor2.execute(
                                """
                                UPDATE public.methodology_truth_dashboard_root_links
                                   SET root_ordinal = root_ordinal + 1000
                                 WHERE dashboard_root_link_id = %s
                                """,
                                (graph.root_links[0][4],),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE"
                            )
                        else:
                            cursor2.execute(
                                """
                                DELETE FROM public.methodology_truth_dashboard_root_links
                                 WHERE dashboard_root_link_id = %s
                                """,
                                (graph.root_links[0][4],),
                            )
                            cursor2.execute(
                                "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE"
                            )
                else:
                    _insert_dashboard_batch(cursor, graphs)
            elif case["authority_id"] == _AUTHORITY_IDS[1]:
                if graph is None:
                    _fail("TASK8_V22_CENSUS_FIXTURE_INVALID")
                include_row = semantic not in {
                    "ZERO_DASHBOARD_NATIVE_ROOTS",
                }
                _insert_dashboard_batch(
                    cursor,
                    graphs,
                    include_focus_row=include_row,
                    corrupt_focus_native_hashes=semantic == "DIRECT_SQL_ROOT_MISMATCH",
                )
                if semantic == "MULTIPLE_DASHBOARD_NATIVE_ROOTS":
                    cursor.execute(
                        """
                        INSERT INTO public.methodology_truth_dashboard_rows(
                            dashboard_row_id,tenant_id,deal_id,run_id,
                            run_output_object_id,lease_owner_id,
                            lease_fencing_token,coverage_record_id,
                            root_projection_sha256,row_disposition,
                            row_payload_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
                        """,
                        (
                            uuid.uuid4(),
                            scope.tenant_id,
                            scope.deal_id,
                            scope.run_id,
                            graph.dashboard_output_object_id,
                            scope.lease_owner_id,
                            graph.coverage_record_id,
                            graph.root_projection_sha256,
                            graph.row_disposition,
                            "a" * 64,
                        ),
                    )
            else:
                if request_id is None:
                    _fail("TASK8_V22_CENSUS_ERASURE_FIXTURE_INVALID")
                _call_erasure(cursor, tenant_id, deal_id, request_id)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        observed_sqlstate = getattr(exc, "pgcode", None)
        observed_reason = str(exc)
        if case["expected"] == "ACCEPT":
            raise Task8V22CensusError(
                "TASK8_V22_CENSUS_LEGAL_CASE_REJECTED:"
                f"{case['case_id']}:{observed_sqlstate or 'UNKNOWN'}"
            ) from exc
    finally:
        connection.close()

    post_projection = (
        _tenant_deal_projection(
            urls.admin,
            tenant_id=tenant_id,
            deal_id=deal_id,
            projection_manifest=projection_manifest,
        )
        if rejected
        else None
    )
    if case["expected"] == "ACCEPT":
        if observed_sqlstate is not None:
            _fail("TASK8_V22_CENSUS_UNEXPECTED_REJECTION")
        outcome = "ACCEPTED"
    else:
        expected_sqlstate = case.get("expected_sqlstate")
        if observed_sqlstate != expected_sqlstate:
            _fail("TASK8_V22_CENSUS_REJECTION_SQLSTATE_INVALID")
        reason_code = case.get("expected_reason_code")
        if reason_code and reason_code not in (observed_reason or ""):
            observed_code = (observed_reason or "UNKNOWN").splitlines()[0]
            _fail(f"TASK8_V22_CENSUS_REJECTION_REASON_INVALID:{case['case_id']}:{observed_code}")
        if pre_projection is None or post_projection is None:
            _fail("TASK8_V22_CENSUS_REJECTION_PROJECTION_MISSING")
        if pre_projection != post_projection:
            _fail("TASK8_V22_CENSUS_REJECTION_MUTATED")
        outcome = "REJECTED_ZERO_MUTATION"
    result: dict[str, Any] = {
        "case_id": case["case_id"],
        "case_kind": case["case_kind"],
        "execution_role": role,
        "outcome": outcome,
        "sqlstate": observed_sqlstate,
        "reason_code": case.get("expected_reason_code"),
        "expected_database_role": expected_database_role,
        "observed_current_user": observed_current_user,
        "observed_session_user": observed_session_user,
    }
    if rejected:
        result.update(
            {
                "pre_projection_sha256": pre_projection["projection_sha256"],
                "post_projection_sha256": post_projection["projection_sha256"],
                "zero_mutation": pre_projection == post_projection,
            }
        )
    return result


def _catalog_projection(
    connection: Any,
    urls: _DisposableUrls,
    authority_execution_binding: Mapping[str, Any],
    expected_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = 'public'
               AND relation.relkind = 'r'
               AND relation.relrowsecurity
               AND relation.relforcerowsecurity
            """
        )
        rls_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT relrowsecurity, relforcerowsecurity
              FROM pg_class
             WHERE oid = 'public.methodology_truth_dashboard_rows'::regclass
            """
        )
        enable_rls, force_rls = cursor.fetchone()
        cursor.execute(
            """
            SELECT rolname, rolsuper, rolbypassrls
              FROM pg_roles
             WHERE rolname IN (
                'idis_task7_guard_function_owner',
                'idis_erasure_function_owner',
                %s,
                %s
             )
            """,
            (urls.application_role, urls.erasure_role),
        )
        posture = {
            str(role): (bool(superuser), bool(bypassrls))
            for role, superuser, bypassrls in cursor.fetchall()
        }
        if set(posture) != {
            _GUARD_OWNER,
            _ERASURE_OWNER,
            urls.application_role,
            urls.erasure_role,
        }:
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
        cursor.execute(
            """
            SELECT role.rolname,
                   (SELECT count(*)
                      FROM pg_auth_members AS membership
                     WHERE membership.member=role.oid
                        OR membership.roleid=role.oid)
              FROM pg_roles AS role
             WHERE role.rolname IN (
                'idis_task7_guard_function_owner',
                'idis_erasure_function_owner',
                %s
             )
            """,
            (urls.erasure_role,),
        )
        memberships = {str(role): int(count) for role, count in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'methodology_truth_dashboard_rows'
               AND has_column_privilege(
                    'idis_task7_guard_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    column_name,
                    'SELECT'
               )
             ORDER BY column_name
            """
        )
        guard_owner_select = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','INSERT'
                   ),
                   has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','UPDATE'
                   ),
                   has_table_privilege(
                       'idis_task7_guard_function_owner',
                       'public.methodology_truth_dashboard_rows','DELETE'
                   )
            """
        )
        guard_owner_insert, guard_owner_update, guard_owner_delete = map(
            bool,
            cursor.fetchone(),
        )
        guard_owner_write_count = sum((guard_owner_insert, guard_owner_update, guard_owner_delete))
        expected_parent_acl = expected_catalog.get("guard_owner_parent_select_acl_tuple")
        if not isinstance(expected_parent_acl, list):
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
        guard_owner_parent_select_acl: list[list[Any]] = []
        guard_owner_parent_write_count = 0
        for item in expected_parent_acl:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", item[0]) is None
                or not isinstance(item[1], list)
            ):
                _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
            relation = item[0]
            cursor.execute(
                """
                SELECT column_name::text
                  FROM information_schema.columns
                 WHERE table_schema='public' AND table_name=%s
                   AND has_column_privilege(
                       'idis_task7_guard_function_owner',
                       'public.' || %s,
                       column_name,
                       'SELECT'
                   )
                 ORDER BY column_name
                """,
                (relation, relation),
            )
            observed_columns = [str(row[0]) for row in cursor.fetchall()]
            guard_owner_parent_select_acl.append([relation, observed_columns])
            cursor.execute(
                """
                SELECT has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'INSERT'
                       )::int
                     + has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'UPDATE'
                       )::int
                     + has_table_privilege(
                           'idis_task7_guard_function_owner',%s,'DELETE'
                       )::int
                """,
                (f"public.{relation}",) * 3,
            )
            guard_owner_parent_write_count += int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'methodology_truth_dashboard_rows'
               AND has_column_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    column_name,
                    'SELECT'
               )
             ORDER BY ordinal_position
            """
        )
        erasure_owner_select = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT
                has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'INSERT'
                )::int
              + has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'UPDATE'
                )::int
              + has_table_privilege(
                    'idis_erasure_function_owner',
                    'public.methodology_truth_dashboard_rows',
                    'DELETE'
                )::int
            """
        )
        erasure_owner_write_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT
                has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'SELECT'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'INSERT'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'UPDATE'
                )::int
              + has_table_privilege(
                    %s,
                    'public.methodology_truth_dashboard_rows',
                    'DELETE'
                )::int
            """,
            (urls.erasure_role,) * 4,
        )
        executor_table_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT has_function_privilege(
                %s,
                'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)',
                'EXECUTE'
            )::int
            """,
            (urls.application_role,),
        )
        app_execute_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT has_function_privilege(
                %s,
                'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)',
                'EXECUTE'
            )::int,
            has_schema_privilege(%s, 'public', 'USAGE'),
            has_schema_privilege(%s, 'public', 'CREATE')
            """,
            (urls.erasure_role, urls.erasure_role, urls.erasure_role),
        )
        executor_execute_count, executor_schema_usage, executor_schema_create = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE qual IS NOT NULL
                                     AND with_check IS NOT NULL
                                     AND qual=with_check)
              FROM pg_policies
             WHERE schemaname='public'
               AND tablename IN (
                    'methodology_truth_dashboard_rows',
                    'methodology_truth_dashboard_root_links'
               )
               AND permissive='PERMISSIVE'
               AND cmd='ALL'
               AND roles=ARRAY['public']::name[]
            """
        )
        dashboard_policy_count, policy_equal_count = map(int, cursor.fetchone())
        authority_rows = authority_execution_binding.get("authority_binding_tuple")
        if not isinstance(authority_rows, list):
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
        function_signatures = [
            guard["function_signature"]
            for row in authority_rows
            if isinstance(row, Mapping) and row.get("authority_id") in _AUTHORITY_IDS[:2]
            for guard in row.get("guard_function_acl_binding_tuple", ())
            if isinstance(guard, Mapping)
        ]
        if len(function_signatures) != len(set(function_signatures)) or not function_signatures:
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
        cursor.execute(
            """
            SELECT count(*) FILTER (WHERE pg_get_userbyid(proowner)=%s),
                   count(*) FILTER (WHERE prosecdef)
              FROM pg_proc
             WHERE oid=ANY(%s::regprocedure[])
            """,
            (_GUARD_OWNER, function_signatures),
        )
        guard_function_owner_count, guard_security_definer_count = map(int, cursor.fetchone())
        application_guard_execute_count = 0
        for signature in function_signatures:
            cursor.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')::int",
                (urls.application_role, signature),
            )
            application_guard_execute_count += int(cursor.fetchone()[0])
        trigger_guards: dict[str, Mapping[str, Any]] = {}
        for authority in authority_rows:
            if (
                not isinstance(authority, Mapping)
                or authority.get("authority_id") not in (_AUTHORITY_IDS[:2])
            ):
                continue
            guards = [
                guard
                for guard in authority.get("guard_function_acl_binding_tuple", ())
                if isinstance(guard, Mapping) and guard.get("function_kind") == "TRIGGER_GUARD"
            ]
            if len(guards) != 1:
                _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
            guard = guards[0]
            if guard.get("target_trigger_tuple") != authority.get("target_tuple"):
                _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
            trigger_guards[str(authority["authority_id"])] = guard
        if set(trigger_guards) != set(_AUTHORITY_IDS[:2]):
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")

        expected_pairs = {
            (str(target), str(trigger))
            for guard in trigger_guards.values()
            for target, trigger in guard["target_trigger_tuple"]
        }
        app_catalog_connection = _connect(urls.application)
        try:
            with app_catalog_connection.cursor() as catalog_cursor:
                catalog_cursor.execute("SELECT current_user,session_user")
                catalog_current_user, catalog_session_user = map(
                    str,
                    catalog_cursor.fetchone(),
                )
                if (
                    catalog_current_user != urls.application_role
                    or catalog_session_user != urls.application_role
                ):
                    _fail("TASK8_V22_CENSUS_CATALOG_ROLE_INVALID")
                catalog_cursor.execute(
                    """
            SELECT relation.relname, trigger.tgname, trigger.tgtype,
                   trigger.tgenabled, trigger.tgconstraint<>0,
                   trigger.tgdeferrable, trigger.tginitdeferred,
                   function_namespace.nspname || '.' || function.proname || '(' ||
                   pg_catalog.replace(
                       pg_catalog.oidvectortypes(function.proargtypes), ', ', ','
                   ) || ')'
              FROM pg_trigger AS trigger
              JOIN pg_class AS relation ON relation.oid=trigger.tgrelid
              JOIN pg_namespace AS relation_namespace
                ON relation_namespace.oid=relation.relnamespace
              JOIN pg_proc AS function ON function.oid=trigger.tgfoid
              JOIN pg_namespace AS function_namespace
                ON function_namespace.oid=function.pronamespace
             WHERE NOT trigger.tgisinternal
               AND relation_namespace.nspname='public'
             ORDER BY relation.relname,trigger.tgname
                    """
                )
                raw_trigger_rows = catalog_cursor.fetchall()
                app_dml_reachability: dict[str, tuple[bool, bool, bool]] = {}
                for target in sorted({target for target, _trigger in expected_pairs}):
                    catalog_cursor.execute(
                        """
                        SELECT has_table_privilege(current_user,%s,'INSERT'),
                               has_table_privilege(current_user,%s,'UPDATE'),
                               has_table_privilege(current_user,%s,'DELETE')
                        """,
                        (f"public.{target}",) * 3,
                    )
                    app_dml_reachability[target] = tuple(map(bool, catalog_cursor.fetchone()))
            app_catalog_connection.rollback()
        finally:
            app_catalog_connection.close()
        trigger_rows = {
            (str(relation), str(trigger)): (
                int(mask),
                str(enabled),
                bool(is_constraint),
                bool(deferrable),
                bool(initially_deferred),
                str(function_signature),
            )
            for (
                relation,
                trigger,
                mask,
                enabled,
                is_constraint,
                deferrable,
                initially_deferred,
                function_signature,
            ) in raw_trigger_rows
            if (str(relation), str(trigger)) in expected_pairs
        }
        if set(trigger_rows) != expected_pairs:
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")

        observed_coordinates: dict[str, list[list[Any]]] = {}
        trigger_function_bindings: list[list[str]] = []
        partial_app_reachability: list[list[Any]] = []
        for authority_id, guard in trigger_guards.items():
            expected_function = str(guard["function_signature"])
            coordinates: list[list[Any]] = []
            for target, trigger in guard["target_trigger_tuple"]:
                mask, enabled, is_constraint, deferrable, initially_deferred, function = (
                    trigger_rows[(str(target), str(trigger))]
                )
                if function != expected_function:
                    _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
                trigger_function_bindings.append(
                    [authority_id, str(target), str(trigger), function]
                )
                if authority_id == _AUTHORITY_IDS[1]:
                    dml = app_dml_reachability[str(target)]
                    partial_app_reachability.append([str(target), str(trigger), *dml, function])
                actual_events = [
                    event
                    for bit, event in ((4, "INSERT"), (16, "UPDATE"), (8, "DELETE"))
                    if mask & bit
                ]
                if mask & (2 | 32 | 64) or not mask & 1:
                    _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
                coordinates.extend(
                    [
                        str(target),
                        str(trigger),
                        "AFTER",
                        event,
                        "ROW",
                        enabled,
                        is_constraint,
                        deferrable,
                        initially_deferred,
                    ]
                    for event in actual_events
                )
            expected_coordinates = guard.get("target_event_coordinate_tuple")
            if coordinates != expected_coordinates:
                _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
            observed_coordinates[authority_id] = coordinates
        dashboard_trigger_coordinates = observed_coordinates[_AUTHORITY_IDS[0]]
        partial_trigger_coordinates = observed_coordinates[_AUTHORITY_IDS[1]]
        dashboard_trigger_events = sorted(
            [[row[0], row[3]] for row in dashboard_trigger_coordinates]
        )
        partial_trigger_events = sorted([[row[0], row[3]] for row in partial_trigger_coordinates])
        trigger_function_bindings.sort()
        partial_app_reachability.sort()
        cursor.execute(
            """
            SELECT pg_get_userbyid(proowner)=%s, prosecdef
              FROM pg_proc
             WHERE oid='public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)'::regprocedure
            """,
            (_ERASURE_OWNER,),
        )
        erasure_runner_owner_valid, erasure_runner_security_definer = cursor.fetchone()
    return {
        "installed_tenant_rls_table_count": rls_count,
        "dashboard_enable_rls": bool(enable_rls),
        "dashboard_force_rls": bool(force_rls),
        "guard_owner_superuser": posture[_GUARD_OWNER][0],
        "guard_owner_bypassrls": posture[_GUARD_OWNER][1],
        "guard_owner_role_membership_count": memberships[_GUARD_OWNER],
        "erasure_owner_superuser": posture[_ERASURE_OWNER][0],
        "erasure_owner_bypassrls": posture[_ERASURE_OWNER][1],
        "erasure_owner_role_membership_count": memberships[_ERASURE_OWNER],
        "application_superuser": posture[urls.application_role][0],
        "application_bypassrls": posture[urls.application_role][1],
        "erasure_executor_superuser": posture[urls.erasure_role][0],
        "erasure_executor_bypassrls": posture[urls.erasure_role][1],
        "erasure_executor_role_membership_count": memberships[urls.erasure_role],
        "guard_owner_dashboard_select_columns": guard_owner_select,
        "guard_owner_dashboard_insert": guard_owner_insert,
        "guard_owner_dashboard_update": guard_owner_update,
        "guard_owner_dashboard_delete": guard_owner_delete,
        "guard_owner_dashboard_write_privilege_count": guard_owner_write_count,
        "erasure_owner_dashboard_select_columns": erasure_owner_select,
        "erasure_owner_dashboard_write_privilege_count": erasure_owner_write_count,
        "erasure_executor_dashboard_table_privilege_count": executor_table_count,
        "application_runner_execute_count": app_execute_count,
        "erasure_executor_runner_execute_count": int(executor_execute_count),
        "erasure_executor_schema_usage": bool(executor_schema_usage),
        "erasure_executor_schema_create": bool(executor_schema_create),
        "dashboard_policy_count": dashboard_policy_count,
        "dashboard_policy_using_with_check_equal_count": policy_equal_count,
        "dashboard_guard_function_owner_count": guard_function_owner_count,
        "dashboard_guard_security_definer_count": guard_security_definer_count,
        "application_guard_function_execute_count": application_guard_execute_count,
        "guard_owner_parent_select_acl_tuple": guard_owner_parent_select_acl,
        "guard_owner_parent_write_privilege_count": guard_owner_parent_write_count,
        "dashboard_trigger_event_tuple": dashboard_trigger_events,
        "partial_trigger_event_tuple": partial_trigger_events,
        "dashboard_trigger_event_coordinate_tuple": dashboard_trigger_coordinates,
        "partial_trigger_event_coordinate_tuple": partial_trigger_coordinates,
        "trigger_function_binding_tuple": trigger_function_bindings,
        "partial_target_application_dml_reachability_tuple": partial_app_reachability,
        "partial_target_application_dml_reachability_count": sum(
            all(row[2:5]) for row in partial_app_reachability
        ),
        "trigger_catalog_application_role_match": (
            catalog_current_user == urls.application_role
            and catalog_session_user == urls.application_role
        ),
        "erasure_runner_owner_valid": bool(erasure_runner_owner_valid),
        "erasure_runner_security_definer": bool(erasure_runner_security_definer),
    }


def _run_lock_races(
    urls: _DisposableUrls,
    lock_binding: Mapping[str, Any],
    dashboard_coverage_row_count: int,
    dashboard_financial_coverage_row_count: int,
) -> tuple[bool, bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    prefix = lock_binding.get("prefix")
    seed = lock_binding.get("seed")
    if not isinstance(prefix, str) or not isinstance(seed, int):
        _fail("TASK8_V22_CENSUS_ADVISORY_LOCK_INVALID")

    forward_scope = _seed_scope(
        urls.admin,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    forward_graphs = _seed_dashboard_batch(
        urls.admin,
        scope=forward_scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    forward_request_id = _seed_erasure_request(urls.admin, forward_scope)
    executor_connection = _connect(urls.erasure)
    application_connection = _connect(urls.application)
    forward_blocked = False
    forward_revalidated = False
    forward_revalidation_sqlstate: str | None = None
    try:
        with executor_connection.cursor() as executor_cursor:
            # This is the exact key expression decoded from both frozen bodies.
            executor_cursor.execute(
                """
                SELECT pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(%s || %s::text, %s)
                )
                """,
                (prefix, forward_scope.tenant_id, seed),
            )
        try:
            with application_connection.cursor() as app_cursor:
                _set_worker_lease(app_cursor, forward_scope)
                app_cursor.execute("SET LOCAL lock_timeout = '250ms'")
                _insert_dashboard_batch(app_cursor, forward_graphs)
                app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        except Exception as exc:
            forward_blocked = getattr(exc, "pgcode", None) == "55P03"
            application_connection.rollback()

        with executor_connection.cursor() as executor_cursor:
            _call_erasure(
                executor_cursor,
                forward_scope.tenant_id,
                forward_scope.deal_id,
                forward_request_id,
            )
        executor_connection.commit()

        try:
            with application_connection.cursor() as app_cursor:
                _set_worker_lease(app_cursor, forward_scope)
                _insert_dashboard_batch(app_cursor, forward_graphs)
                app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
            application_connection.commit()
        except Exception as exc:
            forward_revalidation_sqlstate = getattr(exc, "pgcode", None)
            forward_revalidated = forward_revalidation_sqlstate in {"23503", "40001"}
            application_connection.rollback()
        forward_passed = forward_blocked and forward_revalidated
    finally:
        executor_connection.close()
        application_connection.close()
    results.append(
        {
            "case_id": "hold-erasure-lock-then-dashboard-write",
            "blocked_sqlstate": "55P03" if forward_blocked else None,
            "terminal_revalidation_sqlstate": forward_revalidation_sqlstate,
            "passed": forward_passed,
        }
    )

    reverse_scope = _seed_scope(
        urls.admin,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    reverse_graphs = _seed_dashboard_batch(
        urls.admin,
        scope=reverse_scope,
        focus_semantic_case="ALL_FOUND_ANSWERED",
        coverage_row_count=dashboard_coverage_row_count,
        financial_coverage_row_count=dashboard_financial_coverage_row_count,
    )
    reverse_request_id = _seed_erasure_request(urls.admin, reverse_scope)
    application_connection = _connect(urls.application)
    executor_connection = _connect(urls.erasure)
    reverse_blocked = False
    reverse_refused = False
    try:
        with application_connection.cursor() as app_cursor:
            _set_worker_lease(app_cursor, reverse_scope)
            _insert_dashboard_batch(app_cursor, reverse_graphs)
            app_cursor.execute("SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE")
        try:
            with executor_connection.cursor() as executor_cursor:
                executor_cursor.execute("SET LOCAL lock_timeout = '250ms'")
                _call_erasure(
                    executor_cursor,
                    reverse_scope.tenant_id,
                    reverse_scope.deal_id,
                    reverse_request_id,
                )
        except Exception as exc:
            reverse_blocked = getattr(exc, "pgcode", None) == "55P03"
            executor_connection.rollback()
        application_connection.commit()
        try:
            with executor_connection.cursor() as executor_cursor:
                _call_erasure(
                    executor_cursor,
                    reverse_scope.tenant_id,
                    reverse_scope.deal_id,
                    reverse_request_id,
                )
        except Exception as exc:
            reverse_refused = getattr(
                exc, "pgcode", None
            ) == _REFUSAL_SQLSTATE and _REFUSAL_CODE in str(exc)
            executor_connection.rollback()
        reverse_passed = reverse_blocked and reverse_refused
    finally:
        application_connection.close()
        executor_connection.close()
    results.append(
        {
            "case_id": "hold-dashboard-lock-then-erasure",
            "blocked_sqlstate": "55P03" if reverse_blocked else None,
            "terminal_refusal_sqlstate": (_REFUSAL_SQLSTATE if reverse_refused else None),
            "terminal_refusal_reason_code": _REFUSAL_CODE if reverse_refused else None,
            "passed": reverse_passed,
        }
    )
    return forward_passed, reverse_passed, results


def _case_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        case
        for authority in manifest["authorities"]
        for family in ("legal_case_tuple", "illegal_case_tuple", "direct_sql_case_tuple")
        for case in authority[family]
    ]


def _run_database_execution(
    urls: _DisposableUrls,
    manifest: Mapping[str, Any],
    authority_execution_binding: Mapping[str, Any],
    projection_manifest: Mapping[str, Any],
    lock_binding: Mapping[str, Any],
) -> dict[str, Any]:
    dashboard_coverage_row_count = manifest.get("dashboard_coverage_row_count")
    if dashboard_coverage_row_count != 100:
        _fail("TASK8_V22_CENSUS_COVERAGE_ROW_COUNT_INVALID")
    dashboard_financial_coverage_row_count = manifest.get("dashboard_financial_coverage_row_count")
    if dashboard_financial_coverage_row_count != 79:
        _fail("TASK8_V22_CENSUS_FINANCIAL_COVERAGE_ROW_COUNT_INVALID")
    case_results = [
        _execute_behavior_case(
            case,
            urls=urls,
            projection_manifest=projection_manifest,
            dashboard_coverage_row_count=dashboard_coverage_row_count,
            dashboard_financial_coverage_row_count=(dashboard_financial_coverage_row_count),
        )
        for case in _case_rows(manifest)
    ]
    rls_behavior_passed, rls_behavior_results = _run_rls_behavior_matrix(
        urls,
        projection_manifest,
        dashboard_coverage_row_count,
        dashboard_financial_coverage_row_count,
    )
    admin_connection = _connect(urls.admin)
    try:
        catalog = _catalog_projection(
            admin_connection,
            urls,
            authority_execution_binding,
            manifest["catalog_assertions"],
        )
    finally:
        admin_connection.close()
    forward_passed, reverse_passed, race_results = _run_lock_races(
        urls,
        lock_binding,
        dashboard_coverage_row_count,
        dashboard_financial_coverage_row_count,
    )
    counts = Counter(result["case_kind"] for result in case_results)
    unexpected = sum(
        result["outcome"]
        not in {
            "ACCEPTED",
            "REJECTED_ZERO_MUTATION",
        }
        for result in case_results
    )
    rejected_results = [
        result for result in case_results if result["case_kind"] in {"ILLEGAL", "DIRECT_SQL"}
    ]
    rejected_zero_mutation_count = sum(
        result.get("zero_mutation") is True for result in rejected_results
    )
    rejected_projection_hash = _canonical_sha256(
        [
            "task8-v22-rejected-case-projections-v1",
            [
                [
                    result["case_id"],
                    result.get("pre_projection_sha256"),
                    result.get("post_projection_sha256"),
                    result.get("zero_mutation"),
                ]
                for result in rejected_results
            ],
        ]
    )
    dashboard_refusal_results = [
        result
        for result in rejected_results
        if result["case_id"]
        in {
            "erasure-dashboard-refusal",
            "erasure-executor-direct-refusal",
        }
    ]
    dashboard_refusal_zero_mutation = bool(dashboard_refusal_results) and all(
        result.get("zero_mutation") is True
        and result.get("sqlstate") == _REFUSAL_SQLSTATE
        and result.get("reason_code") == _REFUSAL_CODE
        for result in dashboard_refusal_results
    )
    case_hash = _canonical_sha256(case_results)
    behavior_role_rows = [
        [
            result["case_id"],
            result["execution_role"],
            result["expected_database_role"],
            result["observed_current_user"],
            result["observed_session_user"],
        ]
        for result in case_results
    ]
    behavior_role_match_count = sum(
        expected_role == observed_current == observed_session
        for (
            _case_id,
            _execution_role,
            expected_role,
            observed_current,
            observed_session,
        ) in behavior_role_rows
    )
    catalog_hash = _canonical_sha256(catalog)
    race_hash = _canonical_sha256(race_results)
    rls_behavior_hash = _canonical_sha256(
        ["task8-v22-rls-behavior-matrix-v1", rls_behavior_results]
    )
    execution = {
        "admin_behavior_case_count": 0,
        "application_case_execution_count": sum(
            result["execution_role"] == "application" for result in case_results
        ),
        "erasure_executor_case_execution_count": sum(
            result["execution_role"] == "erasure_executor" for result in case_results
        ),
        "legal_execution_count": counts["LEGAL"],
        "illegal_rejection_count": counts["ILLEGAL"],
        "direct_sql_rejection_count": counts["DIRECT_SQL"],
        "rejected_case_projection_count": len(rejected_results),
        "rejected_case_zero_mutation_count": rejected_zero_mutation_count,
        "rejected_case_projection_sha256": rejected_projection_hash,
        "unexpected_outcome_count": unexpected,
        "behavior_role_match_count": behavior_role_match_count,
        "behavior_role_attestation_sha256": _canonical_sha256(
            ["task8-v22-behavior-role-attestation-v1", behavior_role_rows]
        ),
        "dashboard_refusal_sqlstate": _REFUSAL_SQLSTATE,
        "dashboard_refusal_reason_code": _REFUSAL_CODE,
        "dashboard_refusal_zero_mutation": dashboard_refusal_zero_mutation,
        "forward_lock_race_passed": forward_passed,
        "reverse_lock_race_passed": reverse_passed,
        "rls_behavior_passed": rls_behavior_passed,
        "rls_behavior_sha256": rls_behavior_hash,
        "installed_tenant_rls_table_count": catalog["installed_tenant_rls_table_count"],
        "guard_owner_dashboard_select_columns": catalog["guard_owner_dashboard_select_columns"],
        "guard_owner_dashboard_write_privilege_count": catalog[
            "guard_owner_dashboard_write_privilege_count"
        ],
        "erasure_owner_dashboard_select_columns": catalog["erasure_owner_dashboard_select_columns"],
        "erasure_executor_dashboard_table_privilege_count": catalog[
            "erasure_executor_dashboard_table_privilege_count"
        ],
        "application_runner_execute_count": catalog["application_runner_execute_count"],
        "catalog_assertion_receipt": catalog,
        "authority_execution_binding": dict(authority_execution_binding),
        "advisory_lock_binding": dict(lock_binding),
        "case_result_sha256": case_hash,
        "catalog_projection_sha256": catalog_hash,
        "race_result_sha256": race_hash,
        "complete_execution_sha256": _canonical_sha256(
            [
                "task8-v22-census-execution-v1",
                case_hash,
                catalog_hash,
                race_hash,
                rls_behavior_hash,
            ]
        ),
    }
    expected_catalog = manifest["catalog_assertions"]
    for field, observed in catalog.items():
        if field not in expected_catalog or observed != expected_catalog[field]:
            _fail("TASK8_V22_CENSUS_CATALOG_INVALID")
    if not forward_passed or not reverse_passed:
        _fail("TASK8_V22_CENSUS_LOCK_RACE_INVALID")
    if not rls_behavior_passed:
        _fail("TASK8_V22_CENSUS_RLS_BEHAVIOR_INVALID")
    if behavior_role_match_count != len(case_results):
        _fail("TASK8_V22_CENSUS_BEHAVIOR_ROLE_INVALID")
    if rejected_zero_mutation_count != len(rejected_results) or not dashboard_refusal_zero_mutation:
        _fail("TASK8_V22_CENSUS_REJECTION_MUTATED")
    return execution


def run_disposable_postgres_census(
    admin_database_url: str,
    application_database_url: str,
    erasure_database_url: str,
    artifact_raw: bytes,
) -> dict[str, Any]:
    try:
        artifact = json.loads(artifact_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Task8V22CensusError("TASK8_V22_CENSUS_ARTIFACT_INVALID") from exc
    if not isinstance(artifact, dict):
        _fail("TASK8_V22_CENSUS_ARTIFACT_INVALID")
    _validate_v22(artifact)
    manifest = build_census_case_manifest(artifact)
    validate_census_case_manifest(manifest, artifact)
    urls = _disposable_urls(
        admin_database_url,
        application_database_url,
        erasure_database_url,
    )
    created = False
    dropped = False
    execution: dict[str, Any]
    try:
        _create_disposable_database(admin_database_url, urls.database_name)
        created = True
        authority_execution = build_hash_bound_authority_execution(artifact)
        authority_execution_binding = _setup_disposable_schema(
            urls.admin,
            application_role=urls.application_role,
            erasure_role=urls.erasure_role,
            v22=artifact,
            authority_execution=authority_execution,
        )
        execution = _run_database_execution(
            urls,
            manifest,
            authority_execution_binding=authority_execution_binding,
            projection_manifest=build_zero_mutation_projection_manifest(artifact),
            lock_binding=build_advisory_lock_binding(artifact),
        )
    finally:
        if created:
            _drop_disposable_database(admin_database_url, urls.database_name)
            dropped = True
    receipt = {
        "schema_version": _RECEIPT_SCHEMA,
        "result": "PASS",
        "pipeline_contract_version": "full-strict-v22",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "manifest_sha256": _canonical_sha256(manifest),
        "database_execution": execution,
        "disposable_database_dropped": dropped,
    }
    validate_census_receipt(receipt, artifact)
    return receipt


def validate_census_receipt(
    receipt: Mapping[str, Any],
    v22: Mapping[str, Any],
) -> None:
    _validate_v22(v22)
    if tuple(receipt) != (
        "schema_version",
        "result",
        "pipeline_contract_version",
        "artifact_sha256",
        "manifest_sha256",
        "database_execution",
        "disposable_database_dropped",
    ):
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    if (
        receipt["schema_version"] != _RECEIPT_SCHEMA
        or receipt["result"] != "PASS"
        or receipt["pipeline_contract_version"] != "full-strict-v22"
        or receipt["disposable_database_dropped"] is not True
    ):
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    manifest = build_census_case_manifest(v22)
    if receipt["manifest_sha256"] != _canonical_sha256(manifest):
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    canonical_artifact_raw = (
        json.dumps(
            dict(v22),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if receipt["artifact_sha256"] != hashlib.sha256(canonical_artifact_raw).hexdigest():
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    execution = receipt.get("database_execution")
    if not isinstance(execution, dict):
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    cases = _case_rows(manifest)
    counts = Counter(case["case_kind"] for case in cases)
    rejected_count = counts["ILLEGAL"] + counts["DIRECT_SQL"]
    expected_catalog = {
        key: value
        for key, value in manifest["catalog_assertions"].items()
        if key
        not in {
            "current_erasure_relation_count",
            "future_erasure_relation_count",
            "complete_erasure_relation_count",
        }
    }

    class _ReceiptCursor:
        def execute(self, _statement: str) -> None:
            return None

    expected_authority_binding = execute_hash_bound_authority_sql(
        _ReceiptCursor(),
        build_hash_bound_authority_execution(v22),
    )
    if (
        execution.get("admin_behavior_case_count") != 0
        or execution.get("application_case_execution_count", 0) <= 0
        or execution.get("erasure_executor_case_execution_count", 0) <= 0
        or execution.get("legal_execution_count") != counts["LEGAL"]
        or execution.get("illegal_rejection_count") != counts["ILLEGAL"]
        or execution.get("direct_sql_rejection_count") != counts["DIRECT_SQL"]
        or execution.get("rejected_case_projection_count") != rejected_count
        or execution.get("rejected_case_zero_mutation_count") != rejected_count
        or execution.get("unexpected_outcome_count") != 0
        or execution.get("behavior_role_match_count") != len(cases)
        or execution.get("dashboard_refusal_sqlstate") != _REFUSAL_SQLSTATE
        or execution.get("dashboard_refusal_reason_code") != _REFUSAL_CODE
        or execution.get("dashboard_refusal_zero_mutation") is not True
        or execution.get("forward_lock_race_passed") is not True
        or execution.get("reverse_lock_race_passed") is not True
        or execution.get("rls_behavior_passed") is not True
        or execution.get("installed_tenant_rls_table_count") != 69
        or execution.get("guard_owner_dashboard_select_columns")
        != manifest["catalog_assertions"]["guard_owner_dashboard_select_columns"]
        or execution.get("guard_owner_dashboard_write_privilege_count") != 0
        or execution.get("erasure_executor_dashboard_table_privilege_count") != 0
        or execution.get("application_runner_execute_count") != 0
        or execution.get("catalog_assertion_receipt") != expected_catalog
        or execution.get("authority_execution_binding") != expected_authority_binding
        or execution.get("advisory_lock_binding") != build_advisory_lock_binding(v22)
    ):
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    for field in (
        "rejected_case_projection_sha256",
        "behavior_role_attestation_sha256",
        "rls_behavior_sha256",
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "complete_execution_sha256",
    ):
        if not isinstance(execution.get(field), str) or not _SHA256_RE.fullmatch(execution[field]):
            _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
    expected_complete_execution_sha256 = _canonical_sha256(
        [
            "task8-v22-census-execution-v1",
            execution["case_result_sha256"],
            execution["catalog_projection_sha256"],
            execution["race_result_sha256"],
            execution["rls_behavior_sha256"],
        ]
    )
    if execution["complete_execution_sha256"] != expected_complete_execution_sha256:
        _fail("TASK8_V22_CENSUS_RECEIPT_INVALID")
