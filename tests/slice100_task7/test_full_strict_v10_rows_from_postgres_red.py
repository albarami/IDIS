"""Real-PostgreSQL regression for full-strict-v10's ROWS FROM correction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import disposable_postgres

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V10_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v10_reviewed_preimage_v1.json"
_OLD_MULTI_ARRAY = (
    "FROM pg_catalog.unnest("
    "f.accepted_source_span_ids,f.accepted_locator_sha256s"
    ") zipped(span_id,locator_sha256)"
)
_ROWS_FROM_REPLACEMENT = (
    "FROM ROWS FROM ("
    "pg_catalog.unnest(f.accepted_source_span_ids), "
    "pg_catalog.unnest(f.accepted_locator_sha256s)"
    ") AS zipped(span_id,locator_sha256)"
)

_ARRAY_CASES = (
    (
        "equal arrays",
        (
            "ARRAY["
            "'00000000-0000-0000-0000-000000000001'::uuid,"
            "'00000000-0000-0000-0000-000000000002'::uuid"
            "]::uuid[]"
        ),
        "ARRAY['locator-a',NULL]::text[]",
    ),
    (
        "left longer",
        (
            "ARRAY["
            "'00000000-0000-0000-0000-000000000003'::uuid,"
            "NULL::uuid,"
            "'00000000-0000-0000-0000-000000000005'::uuid"
            "]::uuid[]"
        ),
        "ARRAY['locator-c']::text[]",
    ),
    (
        "right longer",
        ("ARRAY['00000000-0000-0000-0000-000000000006'::uuid]::uuid[]"),
        "ARRAY['locator-f',NULL,'locator-h']::text[]",
    ),
    (
        "both empty",
        "ARRAY[]::uuid[]",
        "ARRAY[]::text[]",
    ),
    (
        "null left array",
        "NULL::uuid[]",
        "ARRAY['locator-null-left']::text[]",
    ),
    (
        "null right array",
        ("ARRAY['00000000-0000-0000-0000-000000000009'::uuid]::uuid[]"),
        "NULL::text[]",
    ),
)


def _load_artifact() -> dict[str, Any]:
    value = json.loads(_V10_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _v10_output_hash_function() -> str:
    artifact = _load_artifact()
    encoded = artifact["sql_authority_registry"]["rows"][18]["body_sql_utf8_hex_tuple"][2]
    return bytes.fromhex(encoded).decode("utf-8")


def _pair_rows(
    connection: Any,
    *,
    table_expression: str,
    left: str,
    right: str,
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.exec_driver_sql(
            "SELECT span_id::text, locator_sha256, ordinal "
            f"FROM {table_expression} "
            "WITH ORDINALITY AS zipped(span_id,locator_sha256,ordinal) "
            "ORDER BY ordinal",
        ).fetchall()
    )


def test_v10_rows_from_is_pairwise_equivalent_for_postgres_array_semantics() -> None:
    function_sql = _v10_output_hash_function()
    assert _OLD_MULTI_ARRAY not in function_sql
    assert function_sql.count(_ROWS_FROM_REPLACEMENT) == 1

    with disposable_postgres() as database, database.admin_engine.connect() as connection:
        for label, left, right in _ARRAY_CASES:
            old = _pair_rows(
                connection,
                table_expression=f"unnest({left},{right})",
                left=left,
                right=right,
            )
            corrected = _pair_rows(
                connection,
                table_expression=(
                    f"ROWS FROM (pg_catalog.unnest({left}),pg_catalog.unnest({right}))"
                ),
                left=left,
                right=right,
            )
            assert corrected == old, label


def test_postgres_rejects_schema_qualified_multi_array_unnest_as_42883() -> None:
    with (
        disposable_postgres() as database,
        database.admin_engine.connect() as connection,
        pytest.raises(DBAPIError) as captured,
    ):
        connection.exec_driver_sql(
            "SELECT * FROM pg_catalog.unnest("
            "ARRAY['00000000-0000-0000-0000-000000000001'::uuid],"
            "ARRAY['locator-a']::text[]"
            ")"
        ).fetchall()

    original = captured.value.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    assert sqlstate == "42883"
