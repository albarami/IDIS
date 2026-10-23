"""RED-first generator-derived RETURNING-to-SELECT privilege closure.

The real v19 runner atomically returns persisted ``executed_by`` and
``executed_at`` but its function owner lacks SELECT on those two columns.
These tests make that PostgreSQL privilege dependency a generated admission
authority so the same class cannot recur in another frozen runner.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V20_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v20_preimage.py"
_V20_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v20_contract_fixture.py"
_V20_CANDIDATE_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_V20_FROZEN_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"

_TABLE = "public.erasure_requests"
_OWNER = "idis_erasure_function_owner"
_EXPECTED_RETURNING = ("status", "executed_by", "executed_at", "counts")
_V19_SELECT = ("request_id", "tenant_id", "deal_id", "status", "counts")
_MISSING_V19 = ("executed_by", "executed_at")
_UPDATE_GRANT = ("request_id", "status", "executed_by", "executed_at", "counts")
_UPDATE_RETURNING_RE = re.compile(
    r"\bUPDATE\s+(?P<table>public\.[a-z][a-z0-9_]*)\b"
    r".*?\bRETURNING\s+(?P<columns>[a-z0-9_, ]+?)\s+INTO\b",
    re.IGNORECASE | re.DOTALL,
)
_GRANT_RE = re.compile(
    r"\AGRANT (?P<privilege>SELECT|UPDATE) "
    r"\((?P<columns>[a-z0-9_,]+)\) ON TABLE "
    r"(?P<table>public\.[a-z][a-z0-9_]*) TO "
    r"(?P<role>[a-z][a-z0-9_]*)\Z"
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_v20_modules() -> tuple[ModuleType, ModuleType]:
    missing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_V20_MATERIALIZER, _V20_GENERATOR)
        if not path.is_file()
    ]
    assert not missing, "TASK7_V20_MATERIALIZATION_NOT_IMPLEMENTED:" + ",".join(missing)
    return (
        _load_module(_V20_MATERIALIZER, "_task7_v20_returning_materializer_red"),
        _load_module(_V20_GENERATOR, "_task7_v20_returning_generator_red"),
    )


def _erasure_row(artifact: dict[str, Any]) -> dict[str, Any]:
    rows = artifact["erasure_execution_registry"]["rows"]
    assert len(rows) == 1 and isinstance(rows[0], dict)
    return rows[0]


def _decode_body(artifact: dict[str, Any], field: str) -> str:
    values = _erasure_row(artifact)[field]
    assert len(values) == 1
    return bytes.fromhex(values[0]).decode("utf-8")


def _decode_statements(artifact: dict[str, Any], field: str) -> tuple[str, ...]:
    return tuple(bytes.fromhex(value).decode("utf-8") for value in _erasure_row(artifact)[field])


def _returning_projection(artifact: dict[str, Any], field: str) -> tuple[str, tuple[str, ...]]:
    body = _decode_body(artifact, field)
    matches = list(_UPDATE_RETURNING_RE.finditer(body))
    assert len(matches) == 1
    match = matches[0]
    columns = tuple(column.strip() for column in match.group("columns").split(","))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", column) for column in columns)
    return match.group("table"), columns


def _grant_projection(
    artifact: dict[str, Any],
    field: str,
    privilege: str,
) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for statement in _decode_statements(artifact, field):
        match = _GRANT_RE.fullmatch(statement)
        if (
            match is not None
            and match.group("privilege") == privilege
            and match.group("table") == _TABLE
            and match.group("role") == _OWNER
        ):
            matches.append(tuple(match.group("columns").split(",")))
    assert len(matches) == 1
    return matches[0]


def _missing_in_body_order(
    returning: tuple[str, ...],
    selected: tuple[str, ...],
) -> tuple[str, ...]:
    selected_set = frozenset(selected)
    return tuple(column for column in returning if column not in selected_set)


def _require_v20_artifact() -> dict[str, Any]:
    assert _V20_ARTIFACT.is_file(), (
        "TASK7_V20_MATERIALIZATION_NOT_IMPLEMENTED:"
        "contracts/full_strict_v20_reviewed_preimage_v1.json"
    )
    frozen_raw = _V20_ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _V20_FROZEN_SHA256
    artifact = _load_json(_V20_ARTIFACT)
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v20"
    assert artifact["metadata"]["append_frozen"] is True
    artifact["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(candidate_raw).hexdigest() == _V20_CANDIDATE_SHA256
    return artifact


def test_real_v19_negative_control_identifies_exact_two_missing_returning_columns() -> None:
    v19 = _load_json(_V19_ARTIFACT)
    table, returning = _returning_projection(v19, "current_body_sql_utf8_hex_tuple")
    selected = _grant_projection(v19, "current_acl_sql_utf8_hex_tuple", "SELECT")

    assert table == _TABLE
    assert returning == _EXPECTED_RETURNING
    assert selected == _V19_SELECT
    assert _missing_in_body_order(returning, selected) == _MISSING_V19

    materializer, _generator = _require_v20_modules()
    with pytest.raises(
        materializer.MaterializationError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            r"public\.erasure_requests:executed_by,executed_at\Z"
        ),
    ):
        materializer.validate_returning_select_closure(v19)


def test_v20_derives_returning_columns_and_admits_the_exact_select_closure() -> None:
    v20 = _require_v20_artifact()
    materializer, generator = _require_v20_modules()

    expected: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            _TABLE,
            _EXPECTED_RETURNING,
            _grant_projection(v20, "current_acl_sql_utf8_hex_tuple", "SELECT"),
        ),
        (
            _TABLE,
            _EXPECTED_RETURNING,
            _grant_projection(v20, "future_acl_sql_utf8_hex_tuple", "SELECT"),
        ),
    )
    assert materializer.derive_returning_select_closure(v20) == expected
    assert materializer.validate_returning_select_closure(v20) == expected
    assert generator.validate_returning_select_closure(v20) == expected

    for _table, returning, selected in expected:
        assert _table == _TABLE
        assert _missing_in_body_order(returning, selected) == ()
        assert tuple(column for column in selected if column not in _MISSING_V19) == _V19_SELECT
        assert frozenset(selected) - frozenset(_V19_SELECT) == frozenset(_MISSING_V19)


def test_v20_returning_derivation_tracks_the_hash_bound_runner_not_a_literal() -> None:
    v20 = _require_v20_artifact()
    materializer, _generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    body = _decode_body(corrupted, "current_body_sql_utf8_hex_tuple")
    body = body.replace(
        "RETURNING status,executed_by,executed_at,counts",
        "RETURNING status,executed_by,executed_at,requested_by,counts",
        1,
    )
    row["current_body_sql_utf8_hex_tuple"] = [body.encode("utf-8").hex()]

    derived = materializer.derive_returning_select_closure(corrupted)
    assert derived[0][1] == (
        "status",
        "executed_by",
        "executed_at",
        "requested_by",
        "counts",
    )
    with pytest.raises(
        materializer.MaterializationError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            r"public\.erasure_requests:requested_by\Z"
        ),
    ):
        materializer.validate_returning_select_closure(corrupted)


@pytest.mark.parametrize(
    ("field", "missing_column"),
    [
        ("current_acl_sql_utf8_hex_tuple", "executed_by"),
        ("future_acl_sql_utf8_hex_tuple", "executed_at"),
    ],
)
def test_v20_generator_rejects_each_missing_returning_select_column(
    field: str,
    missing_column: str,
) -> None:
    v20 = _require_v20_artifact()
    _materializer, generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    statements = list(_decode_statements(corrupted, field))
    statements = [
        statement.replace(f",{missing_column}", "")
        if statement.startswith("GRANT SELECT ") and _TABLE in statement
        else statement
        for statement in statements
    ]
    row[field] = [statement.encode("utf-8").hex() for statement in statements]

    with pytest.raises(
        generator.ContractArtifactError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            rf"public\.erasure_requests:{missing_column}\Z"
        ),
    ):
        generator.validate_returning_select_closure(corrupted)


def test_v20_rejects_an_unread_extra_select_column_and_preserves_update_acl() -> None:
    v20 = _require_v20_artifact()
    materializer, generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    statements = list(_decode_statements(corrupted, "current_acl_sql_utf8_hex_tuple"))
    statements = [
        statement.replace(
            ") ON TABLE public.erasure_requests",
            ",requested_by) ON TABLE public.erasure_requests",
            1,
        )
        if statement.startswith("GRANT SELECT ") and _TABLE in statement
        else statement
        for statement in statements
    ]
    row["current_acl_sql_utf8_hex_tuple"] = [
        statement.encode("utf-8").hex() for statement in statements
    ]

    with pytest.raises(
        generator.ContractArtifactError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            r"public\.erasure_requests:unexpected=requested_by\Z"
        ),
    ):
        generator.validate_returning_select_closure(corrupted)

    v19 = _load_json(_V19_ARTIFACT)
    for field in (
        "current_acl_sql_utf8_hex_tuple",
        "future_acl_sql_utf8_hex_tuple",
    ):
        assert _grant_projection(v20, field, "UPDATE") == _UPDATE_GRANT
        assert _grant_projection(v20, field, "UPDATE") == _grant_projection(
            v19,
            field,
            "UPDATE",
        )


@pytest.mark.parametrize(
    "field",
    [
        "future_activation_sql_utf8_hex_tuple",
        "future_restoration_sql_utf8_hex_tuple",
    ],
)
def test_v20_closure_rejects_each_composed_acl_copy_drift(field: str) -> None:
    v20 = _require_v20_artifact()
    _materializer, generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    statements = list(_decode_statements(corrupted, field))
    statements = [
        statement.replace(",executed_by", "")
        if statement.startswith("GRANT SELECT ") and _TABLE in statement
        else statement
        for statement in statements
    ]
    row[field] = [statement.encode("utf-8").hex() for statement in statements]

    with pytest.raises(
        generator.ContractArtifactError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            rf"{re.escape(field)}:paired-acl-drift\Z"
        ),
    ):
        generator.validate_returning_select_closure(corrupted)


@pytest.mark.parametrize(
    "replacement",
    [
        "RETURNING * INTO",
        "RETURNING status,upper(executed_by),executed_at,counts INTO",
        "RETURNING status,executed_by AS actor,executed_at,counts INTO",
    ],
)
def test_v20_closure_rejects_non_identifier_returning_shapes(replacement: str) -> None:
    v20 = _require_v20_artifact()
    materializer, _generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    body = _decode_body(corrupted, "current_body_sql_utf8_hex_tuple")
    body = body.replace(
        "RETURNING status,executed_by,executed_at,counts INTO",
        replacement,
        1,
    )
    row["current_body_sql_utf8_hex_tuple"] = [body.encode("utf-8").hex()]

    with pytest.raises(
        materializer.MaterializationError,
        match=r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:",
    ):
        materializer.validate_returning_select_closure(corrupted)


def test_v20_closure_rejects_duplicate_owner_select_grant() -> None:
    v20 = _require_v20_artifact()
    materializer, _generator = _require_v20_modules()
    corrupted = copy.deepcopy(v20)
    row = _erasure_row(corrupted)
    statements = list(_decode_statements(corrupted, "current_acl_sql_utf8_hex_tuple"))
    select = next(
        statement
        for statement in statements
        if statement.startswith("GRANT SELECT ") and _TABLE in statement
    )
    statements.append(select)
    row["current_acl_sql_utf8_hex_tuple"] = [
        statement.encode("utf-8").hex() for statement in statements
    ]

    with pytest.raises(
        materializer.MaterializationError,
        match=(
            r"\ATASK7_RETURNING_SELECT_CLOSURE_INVALID:"
            r"public\.erasure_requests:select-grant\Z"
        ),
    ):
        materializer.validate_returning_select_closure(corrupted)
