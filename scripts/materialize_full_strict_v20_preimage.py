"""Materialize the bounded, unfrozen full-strict-v20 reviewed candidate.

V19 is an unfrozen, void predecessor. V20 changes only the erasure runner
owner's column-limited SELECT grants needed by its existing RETURNING clause,
records the derived closure authority in the reviewed design, updates the
version-bearing authorities/recipe, and rehashes the affected registries.
This command cannot freeze, sign, publish, or generate a fixture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V19_PATH = _SCRIPT_DIR / "materialize_full_strict_v19_preimage.py"
_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V19_SOURCE_SHA512 = (
    "354ba2f7603376340f3b9f8774d1aeb572ffae86b9fe95f7897ba80c8b4b2e234"
    "056fc7235a6588082c13c08242d3cc0cdcb4915377bf001c16971e4a0fdc3f6"
)
_V19_ARTIFACT_SHA512 = (
    "1b6816457a809a6158a04428d08dbbc4138720fdfddeb9c8be11c524c747d62e"
    "58007a9090d8fbb2d80a22a34adf7a319649db414bfb81e9552c5d8b9ca14de8"
)
_V19 = "full-strict-v19"
_V20 = "full-strict-v20"
_V20_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v20-sql-authority-registry-v1"
_ERASURE_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_REGISTRY_HASH_DOMAIN = "full-strict-v4-erasure-execution-registry-v1"
_VERSION_AUTHORITIES = (
    (19, "current-receipt-equality-v1", 2, 3),
    (26, "current-task5-coverage-replacement-v1", 0, 1),
)
_ALLOWED_CHANGED_AUTHORITY_ORDINALS = {19, 26}
_APPROVED_CHANGED_SECTIONS = {
    "metadata",
    "digest_recipe_registry",
    "sql_authority_registry",
    "erasure_execution_registry",
}
_SEMANTIC_INVARIANT_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
    "activation_assertions",
)
_ERASURE_REQUESTS = "public.erasure_requests"
_ERASURE_OWNER = "idis_erasure_function_owner"
_BASE_SELECT_COLUMNS = (
    "request_id",
    "tenant_id",
    "deal_id",
    "status",
    "counts",
)
_RETURNING_SELECT_COLUMNS = (
    "request_id",
    "tenant_id",
    "deal_id",
    "status",
    "executed_by",
    "executed_at",
    "counts",
)
_BODY_ACL_PAIRS = (
    ("current_body_sql_utf8_hex_tuple", "current_acl_sql_utf8_hex_tuple"),
    ("future_body_sql_utf8_hex_tuple", "future_acl_sql_utf8_hex_tuple"),
)
_ACL_FIELDS = (
    "current_acl_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
)
_COMPOSED_ACL_FIELDS = (
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
)
_RETURNING_RE = re.compile(
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
_CLOSURE_DESIGN_ROW = [
    "erasure_returning_select_closure",
    [
        "task7-returning-select-closure-v1",
        "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
        _ERASURE_REQUESTS,
        ["status", "executed_by", "executed_at", "counts"],
        list(_RETURNING_SELECT_COLUMNS),
        "CURRENT_AND_FUTURE",
        "DERIVED_FROM_HASH_BOUND_RETURNING_AND_PAIRED_OWNER_SELECT_ACL",
        "EXACT_NO_UNREAD_COLUMN_GRANTS",
    ],
]


class MaterializationError(RuntimeError):
    """Stable fail-closed v20 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_pinned_script(path: Path, sha512: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(f"TASK7_V20_HELPER_MISSING:{path.name}") from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V20_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V20_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_M19 = _load_pinned_script(
    _V19_PATH,
    _V19_SOURCE_SHA512,
    "_task7_v19_materializer_for_v20",
)
_H = _M19._H


def _canonical_bytes(value: object) -> bytes:
    return bytes(_M19._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_M19._render(artifact))


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_M19._changed_paths(old, new, path))


def _load_pinned_v19() -> dict[str, Any]:
    try:
        raw = _V19_ARTIFACT.read_bytes()
    except OSError as exc:
        raise MaterializationError("TASK7_V20_BASE_ARTIFACT_MISSING") from exc
    if hashlib.sha512(raw).hexdigest() != _V19_ARTIFACT_SHA512:
        _error("TASK7_V20_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V20_BASE_ARTIFACT_NOT_CANONICAL")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V20_BASE_ARTIFACT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("metadata", {}).get("artifact_schema")
        != "full-strict-v19-reviewed-preimage-v1"
        or value.get("metadata", {}).get("pipeline_contract_version") != _V19
        or value.get("metadata", {}).get("append_frozen") is not False
    ):
        _error("TASK7_V20_BASE_ARTIFACT_INVALID")
    return value


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V20_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if (
        not isinstance(row, dict)
        or row.get("authority_ordinal") != authority_ordinal
        or row.get("authority_id") != authority_id
    ):
        _error("TASK7_V20_AUTHORITY_SHAPE_INVALID", authority_id)
    return cast(dict[str, Any], row)


def _transform_version_authorities(artifact: dict[str, Any]) -> None:
    for authority_ordinal, authority_id, statement_ordinal, expected_count in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(_H._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V20_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        observed_count = sum(statement.count(_V19) for statement in statements)
        replacement = statements[statement_ordinal]
        if (
            observed_count != expected_count
            or replacement.count(_V19) != expected_count
            or replacement.count(_V20) != 0
        ):
            _error("TASK7_V20_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = replacement.replace(_V19, _V20)
        row["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
        _H._rehash_authority_row(row)


def _rehash_sql_registry(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V20_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            _V20_SQL_REGISTRY_HASH_DOMAIN,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _decode_hex_tuple(row: dict[str, Any], field: str) -> list[str]:
    try:
        values = row[field]
        if not isinstance(values, list) or not values:
            _error("TASK7_V20_ERASURE_SQL_BASE_INVALID", field)
        return [bytes.fromhex(item).decode("utf-8") for item in values]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError(f"TASK7_V20_ERASURE_SQL_BASE_INVALID:{field}") from exc


def _encode_hex_tuple(statements: list[str]) -> list[str]:
    if any(statement != " ".join(statement.split()) for statement in statements):
        _error("TASK7_V20_ERASURE_SQL_NOT_CANONICAL")
    return [statement.encode("utf-8").hex() for statement in statements]


def _erasure_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact.get("erasure_execution_registry")
    if (
        not isinstance(section, dict)
        or tuple(section) != ("rows", "expected_sha256")
        or not isinstance(section.get("rows"), list)
        or len(section["rows"]) != 1
        or not isinstance(section["rows"][0], dict)
    ):
        _error("TASK7_V20_ERASURE_REGISTRY_BASE_INVALID")
    return cast(dict[str, Any], section["rows"][0])


def _select_grant_columns(
    statements: tuple[str, ...],
    *,
    table: str,
) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for statement in statements:
        match = _GRANT_RE.fullmatch(statement)
        if (
            match is not None
            and match.group("privilege") == "SELECT"
            and match.group("table") == table
            and match.group("role") == _ERASURE_OWNER
        ):
            matches.append(tuple(match.group("columns").split(",")))
    if len(matches) != 1:
        _error("TASK7_RETURNING_SELECT_CLOSURE_INVALID", f"{table}:select-grant")
    return matches[0]


def _returning_columns(body: str) -> tuple[str, tuple[str, ...]]:
    matches = list(_RETURNING_RE.finditer(body))
    if len(matches) != 1:
        _error(
            "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
            f"{_ERASURE_REQUESTS}:returning-clause",
        )
    match = matches[0]
    columns = tuple(column.strip() for column in match.group("columns").split(","))
    if (
        not columns
        or len(columns) != len(set(columns))
        or any(re.fullmatch(r"[a-z][a-z0-9_]*", column) is None for column in columns)
    ):
        _error(
            "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
            f"{match.group('table')}:returning-shape",
        )
    return match.group("table"), columns


def derive_returning_select_closure(
    artifact: dict[str, Any],
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Derive the current/future runner RETURNING and paired SELECT projections."""

    row = _erasure_row(artifact)
    derived: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for body_field, acl_field in _BODY_ACL_PAIRS:
        body_values = _decode_hex_tuple(row, body_field)
        if len(body_values) != 1:
            _error("TASK7_RETURNING_SELECT_CLOSURE_INVALID", f"{body_field}:body-count")
        table, returning = _returning_columns(body_values[0])
        selected = _select_grant_columns(
            tuple(_decode_hex_tuple(row, acl_field)),
            table=table,
        )
        derived.append((table, returning, selected))
    return tuple(derived)


def validate_returning_select_closure(
    artifact: dict[str, Any],
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Require exact least-privilege SELECT closure for both runner variants."""

    row = _erasure_row(artifact)
    derived = derive_returning_select_closure(artifact)
    for table, returning, selected in derived:
        if table != _ERASURE_REQUESTS:
            _error("TASK7_RETURNING_SELECT_CLOSURE_INVALID", f"{table}:relation")
        selected_set = frozenset(selected)
        missing = tuple(column for column in returning if column not in selected_set)
        if missing:
            _error("TASK7_RETURNING_SELECT_CLOSURE_INVALID", f"{table}:{','.join(missing)}")
        unexpected = tuple(column for column in selected if column not in _RETURNING_SELECT_COLUMNS)
        if unexpected:
            _error(
                "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
                f"{table}:unexpected={','.join(unexpected)}",
            )
        if selected != _RETURNING_SELECT_COLUMNS:
            _error("TASK7_RETURNING_SELECT_CLOSURE_INVALID", f"{table}:select-order")
    for composed_field, body_field, acl_field in (
        (
            "future_activation_sql_utf8_hex_tuple",
            "future_body_sql_utf8_hex_tuple",
            "future_acl_sql_utf8_hex_tuple",
        ),
        (
            "future_restoration_sql_utf8_hex_tuple",
            "current_body_sql_utf8_hex_tuple",
            "current_acl_sql_utf8_hex_tuple",
        ),
    ):
        composed = _decode_hex_tuple(row, composed_field)
        expected_tail = [
            *_decode_hex_tuple(row, body_field),
            *_decode_hex_tuple(row, acl_field),
        ]
        if len(composed) != len(expected_tail) + 1 or composed[1:] != expected_tail:
            _error(
                "TASK7_RETURNING_SELECT_CLOSURE_INVALID",
                f"{composed_field}:paired-acl-drift",
            )
    return derived


def _replace_select_grant(statements: list[str]) -> list[str]:
    old = (
        "GRANT SELECT (request_id,tenant_id,deal_id,status,counts) ON TABLE "
        "public.erasure_requests TO idis_erasure_function_owner"
    )
    new = (
        "GRANT SELECT "
        "(request_id,tenant_id,deal_id,status,executed_by,executed_at,counts) ON TABLE "
        "public.erasure_requests TO idis_erasure_function_owner"
    )
    if statements.count(old) != 1 or new in statements:
        _error("TASK7_V20_ERASURE_ACL_BASE_INVALID")
    return [new if statement == old else statement for statement in statements]


def _transform_erasure_registry(artifact: dict[str, Any]) -> None:
    section = artifact["erasure_execution_registry"]
    row = _erasure_row(artifact)
    for field in (*_ACL_FIELDS, *_COMPOSED_ACL_FIELDS):
        row[field] = _encode_hex_tuple(_replace_select_grant(_decode_hex_tuple(row, field)))
    row["legal_red_case_tuple"] = [
        "tests/slice100_task7/"
        "test_task7_v20_returning_acl_closure_red.py::"
        "test_v20_derives_returning_columns_and_admits_the_exact_select_closure"
    ]
    row["illegal_red_case_tuple"] = [
        "tests/slice100_task7/"
        "test_task7_v20_returning_acl_closure_red.py::"
        "test_v20_rejects_an_unread_extra_select_column_and_preserves_update_acl"
    ]
    row["direct_sql_red_case_tuple"] = [
        "tests/slice100_task7/"
        "test_task7_v20_returning_acl_closure_red.py::"
        "test_real_v19_negative_control_identifies_exact_two_missing_returning_columns"
    ]
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _sha256(_ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload))
    section["expected_sha256"] = _h([_ERASURE_REGISTRY_HASH_DOMAIN, section["rows"]])


def _transform_reviewed_design(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V20_REVIEWED_DESIGN_BASE_INVALID") from exc
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v19-reviewed-design-literal-v1",
        ["pipeline_contract_version", _V19],
    ]:
        _error("TASK7_V20_REVIEWED_DESIGN_BASE_INVALID")
    if any(
        isinstance(row, list) and row and row[0] == "erasure_returning_select_closure"
        for row in design
    ):
        _error("TASK7_V20_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = "full-strict-v20-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", _V20]
    design.append(copy.deepcopy(_CLOSURE_DESIGN_ROW))
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v20-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = _V20
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any]) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    expected = [
        "pipeline_contract_hash",
        '["pipeline-contract-v19","full-strict-v19",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V20_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        '["pipeline-contract-v20","full-strict-v20",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _build_v20_core(v19: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v19)
    artifact["metadata"]["append_frozen"] = False
    _transform_version_authorities(artifact)
    _rehash_sql_registry(artifact)
    _transform_erasure_registry(artifact)
    _transform_reviewed_design(artifact)
    _transform_digest_recipe(artifact)
    validate_returning_select_closure(artifact)
    return artifact


def _validate_exact_diff(v19: dict[str, Any], v20: dict[str, Any]) -> None:
    if _render(v19) != _V19_ARTIFACT.read_bytes():
        _error("TASK7_V20_BASE_ARTIFACT_HASH_MISMATCH")
    if tuple(v20) != tuple(v19):
        _error("TASK7_V20_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v20[section] != v19[section]:
            _error("TASK7_V20_INVARIANT_DRIFT", section)
    changed = _changed_paths(v19, v20)
    if {path[0] for path in changed} != _APPROVED_CHANGED_SECTIONS:
        _error("TASK7_V20_EXACT_DIFF_INVALID", "top-level")
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != _ALLOWED_CHANGED_AUTHORITY_ORDINALS:
        _error("TASK7_V20_EXACT_DIFF_INVALID", "authority rows")
    if (
        v20.get("metadata", {}).get("artifact_schema") != "full-strict-v20-reviewed-preimage-v1"
        or v20.get("metadata", {}).get("pipeline_contract_version") != _V20
        or v20.get("metadata", {}).get("append_frozen") is not False
    ):
        _error("TASK7_V20_UNFROZEN_CANDIDATE_REQUIRED")
    expected = _build_v20_core(v19)
    if _render(expected) != _render(v20):
        _error("TASK7_V20_EXACT_DIFF_INVALID", "candidate bytes")
    validate_returning_select_closure(v20)


def build_v20(v19: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v20 RETURNING-ACL candidate."""

    pinned = _load_pinned_v19()
    if _render(v19) != _render(pinned):
        _error("TASK7_V20_BASE_ARTIFACT_HASH_MISMATCH")
    artifact = _build_v20_core(v19)
    _validate_exact_diff(v19, artifact)
    return artifact


def project_v20_to_v19(v20: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact void, unfrozen v19 bytes from v20."""

    v19 = _load_pinned_v19()
    _validate_exact_diff(v19, v20)
    projected = copy.deepcopy(v20)
    for section in _APPROVED_CHANGED_SECTIONS:
        projected[section] = copy.deepcopy(v19[section])
    if _render(projected) != _V19_ARTIFACT.read_bytes():
        _error("TASK7_V20_EXACT_V19_PROJECTION_HASH_MISMATCH")
    return projected


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V20_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V20_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V19_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if hashlib.sha512(raw).hexdigest() != _V19_ARTIFACT_SHA512:
            _error("TASK7_V20_BASE_ARTIFACT_HASH_MISMATCH")
        v19 = json.loads(raw)
        if not isinstance(v19, dict):
            _error("TASK7_V20_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v20(v19))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V20_UNFROZEN_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaterializationError",
    "_render",
    "_validate_exact_diff",
    "build_v20",
    "derive_returning_select_closure",
    "main",
    "project_v20_to_v19",
    "validate_returning_select_closure",
]
