"""Validate and derive the unfrozen full-strict-v13 contract candidate.

The reviewed artifact is the sole normative byte source. This module validates
the v13 ownership/ACL census and can derive prospective frozen bytes for later
review, but its command-line entry point deliberately refuses fixture
publication while the checked-in candidate remains unfrozen.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V13_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V12 = _load_script(
    _SCRIPT_DIR / "generate_task7_v12_contract_fixture.py",
    "_task7_v12_fixture_helpers_for_v13",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v13_preimage.py",
    "_task7_v13_materializer_helpers",
)

ContractArtifactError = _V12.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V12._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V12._COMPONENT_ORDER)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_V13_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v13-sql-authority-registry-v1"
_ACL_ROLE_HASH_DOMAIN = b"full-strict-v12-guard-owner-role-sha256-v1\x00"
_ACL_FUNCTION_HASH_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_HASH_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_GUARD_OWNER = "idis_task7_guard_function_owner"
_APPLICATION_PRINCIPAL = "AUTHENTICATED_APPLICATION_ROLE"
_ERASURE_OWNER = "idis_erasure_function_owner"
_EXPECTED_POSTURE = {
    "login": False,
    "superuser": False,
    "createdb": False,
    "createrole": False,
    "inherit": False,
    "bypassrls": False,
}
_EXPECTED_RLS_CONTRACT = [
    "FORCE_RLS",
    "TRANSACTION_LOCAL_TENANT_CONTEXT",
    "idis.tenant_id",
    "canonical-null-guarded-using-and-with-check-v1",
]


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V12._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> Never:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V12._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V12.invariant_vector(artifact))


_SqlToken = tuple[str, str]
_GENERATED_MARKER = ("IDENT", "generated")
_SOURCE_AUTHORITY_ID = "current-source-bound-v1"
_SOURCE_TARGETS = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
)
_PACKAGE_FIELDS = (
    "status",
    "source_binding_state",
    "source_lineage_object_id",
    "source_corpus_receipt_id",
    "source_lineage_object_kind",
    "updated_at",
)
_SPAN_FIELDS = (
    "source_binding_state",
    "source_lineage_object_id",
    "source_corpus_receipt_id",
    "source_lineage_object_kind",
    "parse_attempt_id",
)
_GENERIC_SOURCE_FIELDS = (
    "source_binding_state",
    "source_lineage_object_id",
    "source_corpus_receipt_id",
    "source_lineage_object_kind",
)
_EXPRESSION_KEYWORDS = frozenset(
    {
        "and",
        "as",
        "case",
        "else",
        "end",
        "false",
        "is",
        "not",
        "null",
        "or",
        "then",
        "true",
        "when",
    }
)


def _sql_tokens(sql: str) -> tuple[_SqlToken, ...]:
    """Lex SQL while retaining literals but discarding comments and quote syntax."""

    tokens: list[_SqlToken] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                _error("TASK7_V13_SQL_LEXER_INVALID", "unterminated block comment")
            continue
        if char == "'":
            index += 1
            value: list[str] = []
            while index < length:
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        value.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                value.append(sql[index])
                index += 1
            else:
                _error("TASK7_V13_SQL_LEXER_INVALID", "unterminated string")
            tokens.append(("STRING", "".join(value)))
            continue
        if char == '"':
            index += 1
            value = []
            while index < length:
                if sql[index] == '"':
                    if index + 1 < length and sql[index + 1] == '"':
                        value.append('"')
                        index += 2
                        continue
                    index += 1
                    break
                value.append(sql[index])
                index += 1
            else:
                _error("TASK7_V13_SQL_LEXER_INVALID", "unterminated identifier")
            tokens.append(("IDENT", "".join(value).casefold()))
            continue
        if char == "$":
            cursor = index + 1
            while cursor < length and (sql[cursor].isalnum() or sql[cursor] == "_"):
                cursor += 1
            if cursor < length and sql[cursor] == "$":
                index = cursor + 1
                continue
        if char.isalpha() or char == "_":
            cursor = index + 1
            while cursor < length and (sql[cursor].isalnum() or sql[cursor] in "_$"):
                cursor += 1
            tokens.append(("IDENT", sql[index:cursor].casefold()))
            index = cursor
            continue
        if char.isdigit():
            cursor = index + 1
            while cursor < length and (sql[cursor].isalnum() or sql[cursor] in "._"):
                cursor += 1
            tokens.append(("NUMBER", sql[index:cursor].casefold()))
            index = cursor
            continue
        if sql.startswith("::", index):
            tokens.append(("SYMBOL", "::"))
            index += 2
            continue
        if sql.startswith("->>", index):
            tokens.append(("SYMBOL", "->>"))
            index += 3
            continue
        if sql.startswith("->", index):
            tokens.append(("SYMBOL", "->"))
            index += 2
            continue
        if (
            sql.startswith("<>", index)
            or sql.startswith("<=", index)
            or sql.startswith(">=", index)
        ):
            tokens.append(("SYMBOL", sql[index : index + 2]))
            index += 2
            continue
        tokens.append(("SYMBOL", char))
        index += 1
    return tuple(tokens)


def _generated_definition(
    definition: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    tokens = _sql_tokens(definition)
    if not tokens or tokens[0][0] != "IDENT" or _GENERATED_MARKER not in tokens:
        return None
    try:
        marker = tokens.index(_GENERATED_MARKER)
        expected = (
            ("IDENT", "generated"),
            ("IDENT", "always"),
            ("IDENT", "as"),
            ("SYMBOL", "("),
        )
        if tokens[marker : marker + 4] != expected:
            _error(
                "TASK7_V13_GENERATED_COLUMN_DEFINITION_INVALID",
                tokens[0][1],
            )
        depth = 1
        cursor = marker + 4
        expression: list[_SqlToken] = []
        while cursor < len(tokens) and depth:
            token = tokens[cursor]
            if token == ("SYMBOL", "("):
                depth += 1
            elif token == ("SYMBOL", ")"):
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            if depth:
                expression.append(token)
            cursor += 1
        if depth or tokens[cursor:] != (("IDENT", "stored"),):
            _error(
                "TASK7_V13_GENERATED_COLUMN_DEFINITION_INVALID",
                tokens[0][1],
            )
    except ValueError as exc:
        raise ContractArtifactError(
            "TASK7_V13_GENERATED_COLUMN_DEFINITION_INVALID",
            tokens[0][1],
        ) from exc
    dependencies: list[str] = []
    for ordinal, token in enumerate(expression):
        if token[0] != "IDENT" or token[1] in _EXPRESSION_KEYWORDS:
            continue
        previous = expression[ordinal - 1] if ordinal else None
        following = expression[ordinal + 1] if ordinal + 1 < len(expression) else None
        if previous == ("SYMBOL", "::") or following == ("SYMBOL", "("):
            continue
        if token[1] not in dependencies:
            dependencies.append(token[1])
    expression_sql = "".join(
        value if kind != "STRING" else json.dumps(value, ensure_ascii=False)
        for kind, value in expression
    )
    return tokens[0][1], expression_sql, tuple(dependencies)


def current_generated_column_census(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Derive the complete generated-column authority from current DDL bytes."""

    rows: list[dict[str, Any]] = []
    for table_ordinal, table in enumerate(artifact["current_ddl_registry"]["rows"]):
        table_name = str(table["table_name"])
        for column_ordinal, definition in enumerate(table["column_tuple"]):
            parsed = _generated_definition(str(definition))
            if parsed is None:
                continue
            column_name, expression_sql, dependencies = parsed
            rows.append(
                {
                    "table_ordinal": table_ordinal,
                    "column_ordinal": column_ordinal,
                    "table_name": table_name,
                    "column_name": column_name,
                    "expression_token_sql": expression_sql,
                    "dependency_column_tuple": list(dependencies),
                    "definition_sha256": _h(
                        [
                            "task7-v13-current-generated-column-definition-v1",
                            table_name,
                            str(definition),
                        ]
                    ),
                }
            )
    if not rows or len({(row["table_name"], row["column_name"]) for row in rows}) != len(rows):
        _error("TASK7_V13_GENERATED_COLUMN_CENSUS_INVALID", "empty or duplicate")
    return tuple(rows)


def current_generated_column_authority(
    artifact: dict[str, Any],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {
        str(row["table_name"]): [] for row in artifact["current_ddl_registry"]["rows"]
    }
    for row in current_generated_column_census(artifact):
        result[str(row["table_name"])].append(str(row["column_name"]))
    return {table: tuple(columns) for table, columns in result.items()}


def current_generated_column_authority_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v13-current-generated-column-authority-v1",
            list(current_generated_column_census(artifact)),
        ]
    )


def _function_sql(
    artifact: dict[str, Any],
    function_signature: str,
) -> str:
    matches: list[str] = []
    for row in artifact["sql_authority_registry"]["rows"]:
        for statement in _MATERIALIZER._V12._decode_statements(row):
            for match in _MATERIALIZER._V12._FUNCTION_RE.finditer(statement):
                if _MATERIALIZER._V12._signature(match) == function_signature:
                    matches.append(match.group(0))
    if len(matches) != 1:
        _error("TASK7_V13_FUNCTION_DEFINITION_CENSUS_INVALID", function_signature)
    return matches[0]


def _token_values(tokens: tuple[_SqlToken, ...]) -> tuple[str, ...]:
    return tuple(f"{kind}:{value}" for kind, value in tokens)


def _subsequence_count(
    tokens: tuple[_SqlToken, ...],
    expected: tuple[_SqlToken, ...],
) -> int:
    return sum(
        tokens[index : index + len(expected)] == expected
        for index in range(max(0, len(tokens) - len(expected) + 1))
    )


def _direct_new_columns(tokens: tuple[_SqlToken, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for index in range(len(tokens) - 2):
        if (
            tokens[index] == ("IDENT", "new")
            and tokens[index + 1] == ("SYMBOL", ".")
            and tokens[index + 2][0] == "IDENT"
        ):
            result.append(tokens[index + 2][1])
    return tuple(result)


def _whole_new_sink_count(tokens: tuple[_SqlToken, ...]) -> int:
    count = _subsequence_count(
        tokens,
        (("IDENT", "new"), ("SYMBOL", "."), ("SYMBOL", "*")),
    )
    for function_name in ("to_jsonb", "row_to_json"):
        count += _subsequence_count(
            tokens,
            (
                ("IDENT", function_name),
                ("SYMBOL", "("),
                ("IDENT", "new"),
                ("SYMBOL", ")"),
            ),
        )
    return count


def _json_subtraction_fields(
    tokens: tuple[_SqlToken, ...],
    record_name: str,
) -> tuple[tuple[str, ...], ...]:
    results: list[tuple[str, ...]] = []
    for index in range(len(tokens) - 7):
        is_function = (
            tokens[index][0] == "IDENT"
            and tokens[index][1] in {"to_jsonb", "row_to_json"}
            and tokens[index + 1 : index + 4]
            == (
                ("SYMBOL", "("),
                ("IDENT", record_name),
                ("SYMBOL", ")"),
            )
        )
        is_parameter = tokens[index] == ("IDENT", record_name)
        sink_end = index + 4 if is_function else index + 1
        if not (is_function or is_parameter):
            continue
        if tokens[sink_end : sink_end + 3] != (
            ("SYMBOL", "-"),
            ("IDENT", "array"),
            ("SYMBOL", "["),
        ):
            continue
        cursor = sink_end + 3
        fields: list[str] = []
        while cursor < len(tokens) and tokens[cursor] != ("SYMBOL", "]"):
            token = tokens[cursor]
            if token[0] == "STRING":
                fields.append(token[1])
            elif token != ("SYMBOL", ","):
                _error(
                    "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                    "nonliteral normalization array",
                )
            cursor += 1
        if cursor == len(tokens):
            _error(
                "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                "unterminated normalization array",
            )
        results.append(tuple(fields))
    return tuple(results)


def _source_generated_coverage(
    artifact: dict[str, Any],
    function_row: dict[str, Any],
    sql: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    tokens = _sql_tokens(sql)
    helper_tokens = _sql_tokens(
        _function_sql(
            artifact,
            "public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)",
        )
    )
    generated = current_generated_column_authority(artifact)
    coordinates = tuple(function_row["target_event_coordinate_tuple"])
    if (
        tuple(dict.fromkeys(str(row[0]) for row in coordinates)) != _SOURCE_TARGETS
        or len(coordinates) != 12
        or {str(row[2]) for row in coordinates} != {"BEFORE"}
        or {str(row[3]) for row in coordinates} != {"UPDATE", "DELETE"}
    ):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source target/event coverage",
        )

    direct = _direct_new_columns(tokens)
    generated_names = {column for table in _SOURCE_TARGETS for column in generated.get(table, ())}
    if tuple(column for column in direct if column in generated_names) != (
        "source_corpus_receipt_id",
    ):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source direct NEW coverage",
        )
    exemption = (
        ("IDENT", "tg_table_name"),
        ("IDENT", "not"),
        ("IDENT", "in"),
        ("SYMBOL", "("),
        ("STRING", "data_room_packages"),
        ("SYMBOL", ","),
        ("STRING", "data_room_package_files"),
        ("SYMBOL", ")"),
        ("IDENT", "and"),
        ("IDENT", "new"),
        ("SYMBOL", "."),
        ("IDENT", "source_corpus_receipt_id"),
        ("IDENT", "is"),
        ("IDENT", "null"),
    )
    if _subsequence_count(tokens, exemption) != 1:
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "generated corpus exemption",
        )
    if _whole_new_sink_count(tokens) != 3:
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source whole NEW/OLD sink count",
        )
    new_fields = _json_subtraction_fields(tokens, "new")
    old_fields = _json_subtraction_fields(tokens, "old")
    p_new_fields = _json_subtraction_fields(helper_tokens, "p_new")
    p_old_fields = _json_subtraction_fields(helper_tokens, "p_old")
    if (
        new_fields != (_SPAN_FIELDS, _GENERIC_SOURCE_FIELDS)
        or old_fields != (_SPAN_FIELDS, _GENERIC_SOURCE_FIELDS)
        or p_new_fields != (_PACKAGE_FIELDS,)
        or p_old_fields != (_PACKAGE_FIELDS,)
    ):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source normalization fields",
        )
    branch_markers = (
        (
            ("IDENT", "tg_table_name"),
            ("SYMBOL", "="),
            ("STRING", "data_room_packages"),
            ("IDENT", "and"),
            ("IDENT", "not"),
            ("IDENT", "public"),
            ("SYMBOL", "."),
            ("IDENT", "idis_task7_package_source_transition_is_valid_v1"),
        ),
        (
            ("IDENT", "tg_table_name"),
            ("SYMBOL", "="),
            ("STRING", "document_spans"),
            ("IDENT", "and"),
        ),
        (
            ("IDENT", "tg_table_name"),
            ("IDENT", "not"),
            ("IDENT", "in"),
            ("SYMBOL", "("),
            ("STRING", "data_room_packages"),
            ("SYMBOL", ","),
            ("STRING", "document_spans"),
            ("SYMBOL", ")"),
            ("IDENT", "and"),
        ),
    )
    if any(_subsequence_count(tokens, marker) != 1 for marker in branch_markers):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source target normalization branch",
        )
    normalization_by_target = {
        "data_room_packages": _PACKAGE_FIELDS,
        "data_room_package_files": _GENERIC_SOURCE_FIELDS,
        "document_artifacts": _GENERIC_SOURCE_FIELDS,
        "documents": _GENERIC_SOURCE_FIELDS,
        "document_parse_attempts": _GENERIC_SOURCE_FIELDS,
        "document_spans": _SPAN_FIELDS,
    }
    coverage = tuple(
        (
            table,
            tuple(
                column
                for column in generated.get(table, ())
                if column in normalization_by_target[table]
            ),
        )
        for table in _SOURCE_TARGETS
    )
    if tuple(table for table, _columns in coverage) != _SOURCE_TARGETS or any(
        columns != generated[table] for table, columns in coverage
    ):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source generated coverage is not exact/disjoint",
        )
    return coverage


def _output_bound_whole_row_exemption(tokens: tuple[_SqlToken, ...]) -> bool:
    expected = (
        ("IDENT", "old"),
        ("SYMBOL", "."),
        ("IDENT", "run_output_object_id"),
        ("IDENT", "is"),
        ("IDENT", "not"),
        ("IDENT", "null"),
        ("IDENT", "and"),
        ("IDENT", "pg_catalog"),
        ("SYMBOL", "."),
        ("IDENT", "to_jsonb"),
        ("SYMBOL", "("),
        ("IDENT", "new"),
        ("SYMBOL", ")"),
        ("IDENT", "is"),
        ("IDENT", "distinct"),
        ("IDENT", "from"),
        ("IDENT", "pg_catalog"),
        ("SYMBOL", "."),
        ("IDENT", "to_jsonb"),
        ("SYMBOL", "("),
        ("IDENT", "old"),
        ("SYMBOL", ")"),
        ("IDENT", "then"),
        ("IDENT", "raise"),
        ("IDENT", "exception"),
    )
    return _whole_new_sink_count(tokens) == 1 and _subsequence_count(tokens, expected) == 1


def _append_only_admission_projection_exemption(
    tokens: tuple[_SqlToken, ...],
    generated: dict[str, tuple[str, ...]],
) -> bool:
    expected = (
        ("IDENT", "if"),
        ("IDENT", "tg_table_name"),
        ("SYMBOL", "="),
        ("STRING", "run_expected_consumer_edges"),
        ("IDENT", "and"),
        ("IDENT", "public"),
        ("SYMBOL", "."),
        ("IDENT", "idis_task7_expected_edge_admission_is_valid_v1"),
        ("SYMBOL", "("),
        ("IDENT", "pg_catalog"),
        ("SYMBOL", "."),
        ("IDENT", "to_jsonb"),
        ("SYMBOL", "("),
        ("IDENT", "new"),
        ("SYMBOL", ")"),
    )
    return (
        not generated.get("run_expected_consumer_edges", ())
        and _whole_new_sink_count(tokens) == 1
        and _subsequence_count(tokens, expected) == 1
    )


def before_generated_column_coverage(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Validate every current BEFORE guard against generated-column timing."""

    generated = current_generated_column_authority(artifact)
    function_rows = tuple(_acl(artifact)["function_rows"])
    coverage: list[dict[str, Any]] = []
    for function_row in function_rows:
        if (
            function_row["install_scope"] != "CURRENT_0038"
            or function_row["function_kind"] != "TRIGGER_GUARD"
        ):
            continue
        before_targets = tuple(
            dict.fromkeys(
                str(coordinate[0])
                for coordinate in function_row["target_event_coordinate_tuple"]
                if str(coordinate[2]) == "BEFORE"
            )
        )
        if not before_targets:
            continue
        sql = _function_sql(artifact, str(function_row["function_signature"]))
        tokens = _sql_tokens(sql)
        generated_targets = tuple(table for table in before_targets if generated.get(table, ()))
        if function_row["authority_id"] == _SOURCE_AUTHORITY_ID:
            source_coverage = _source_generated_coverage(
                artifact,
                function_row,
                sql,
            )
            coverage.extend(
                {
                    "authority_id": _SOURCE_AUTHORITY_ID,
                    "function_signature": function_row["function_signature"],
                    "table_name": table,
                    "generated_column_tuple": list(columns),
                    "mode": (
                        "EXEMPT_GENERATED_CORPUS_AND_NORMALIZE_WHOLE_NEW"
                        if table in {"data_room_packages", "data_room_package_files"}
                        else "NORMALIZE_WHOLE_NEW"
                    ),
                }
                for table, columns in source_coverage
            )
            continue
        direct = set(_direct_new_columns(tokens))
        invalid_direct = {
            (table, column)
            for table in generated_targets
            for column in generated[table]
            if column in direct
        }
        whole_sink_count = _whole_new_sink_count(tokens)
        if invalid_direct:
            _error(
                "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                f"{function_row['authority_id']}:direct:{sorted(invalid_direct)!r}",
            )
        if whole_sink_count and generated_targets:
            output_exempt = function_row[
                "authority_id"
            ] == "current-output-bound-v1" and _output_bound_whole_row_exemption(tokens)
            admission_projection_exempt = function_row[
                "authority_id"
            ] == "current-append-only-lease-v1" and _append_only_admission_projection_exemption(
                tokens, generated
            )
            if not (output_exempt or admission_projection_exempt):
                _error(
                    "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                    f"{function_row['authority_id']}:whole-new",
                )
            if output_exempt:
                coverage.extend(
                    {
                        "authority_id": function_row["authority_id"],
                        "function_signature": function_row["function_signature"],
                        "table_name": table,
                        "generated_column_tuple": list(generated[table]),
                        "mode": "IMMUTABILITY_REJECTION_ONLY",
                    }
                    for table in generated_targets
                )
    source_rows = [row for row in coverage if row["authority_id"] == _SOURCE_AUTHORITY_ID]
    if len(source_rows) != 6 or {row["table_name"] for row in source_rows} != set(_SOURCE_TARGETS):
        _error(
            "TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "source coverage census",
        )
    return tuple(coverage)


def before_generated_column_coverage_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v13-before-generated-column-coverage-v1",
            list(before_generated_column_coverage(artifact)),
        ]
    )


def validate_before_generated_column_access(artifact: dict[str, Any]) -> None:
    current_generated_column_census(artifact)
    before_generated_column_coverage(artifact)


def _acl(artifact: dict[str, Any]) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            artifact["sql_authority_registry"]["guard_function_acl_registry"],
        )
    except (KeyError, TypeError) as exc:
        raise ContractArtifactError(
            "TASK7_V13_ACL_REGISTRY_MISSING",
            "guard-function ACL authority is absent",
        ) from exc


def guard_function_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(row["function_signature"]),
            str(row["authority_id"]),
            str(row["owner_role"]),
            str(row["install_scope"]),
        )
        for row in _acl(artifact)["function_rows"]
    )


def guard_target_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row["function_signature"]), str(table), str(trigger))
        for row in _acl(artifact)["function_rows"]
        for table, trigger in row["target_trigger_tuple"]
    )


def guard_target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (str(row["function_signature"]), *tuple(coordinate))
        for row in _acl(artifact)["function_rows"]
        for coordinate in row["target_event_coordinate_tuple"]
    )


def guard_helper_call_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row["function_signature"]), str(callee))
        for row in _acl(artifact)["function_rows"]
        for callee in row["helper_call_signature_tuple"]
    )


def _validate_hash_bound_acl(artifact: dict[str, Any]) -> None:
    sql = artifact["sql_authority_registry"]
    for ordinal, row in enumerate(sql["rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        expected = hashlib.sha256(_ROW_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest()
        _expect(expected, row.get("expected_sha256"), f"v13 SQL authority {ordinal}")

    acl = _acl(artifact)
    role = acl["guard_owner_role"]
    role_payload = {key: value for key, value in role.items() if key != "expected_sha256"}
    _expect(
        hashlib.sha256(_ACL_ROLE_HASH_DOMAIN + _canonical_bytes(role_payload)).hexdigest(),
        role.get("expected_sha256"),
        "v13 guard owner role",
    )
    for ordinal, row in enumerate(acl["function_rows"]):
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        _expect(
            hashlib.sha256(_ACL_FUNCTION_HASH_DOMAIN + _canonical_bytes(payload)).hexdigest(),
            row.get("expected_sha256"),
            f"v13 guard function ACL {ordinal}",
        )
    downgrade = acl["downgrade_authority"]
    downgrade_payload = {key: value for key, value in downgrade.items() if key != "expected_sha256"}
    _expect(
        _h(
            [
                "task7-guard-function-downgrade-authority-v1",
                downgrade_payload,
            ]
        ),
        downgrade.get("expected_sha256"),
        "v13 guard function downgrade authority",
    )
    _expect(
        _h(
            [
                _ACL_REGISTRY_HASH_DOMAIN,
                acl["guard_owner_role"],
                acl["function_rows"],
                acl["current_install_function_signature_tuple"],
                acl["current_column_grant_sql_utf8_hex_tuple"],
                acl["current_column_revoke_sql_utf8_hex_tuple"],
                acl["retained_0037_catalog_projection_tuple"],
                acl["retained_0037_trigger_catalog_projection_tuple"],
                acl["retained_0037_catalog_preflight_sql_utf8_hex"],
                acl["installation_sql_utf8_hex_tuple"],
                acl["downgrade_authority"],
            ]
        ),
        acl.get("expected_sha256"),
        "v13 guard function ACL registry",
    )
    _expect(
        _h([_V13_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]),
        sql.get("expected_sha256"),
        "v13 SQL authority registry",
    )


def _validate_guard_function_acl_registry(artifact: dict[str, Any]) -> None:
    acl = _acl(artifact)
    rows = acl.get("function_rows")
    if not isinstance(rows, list) or len(rows) != 34:
        _error(
            "TASK7_V13_ACL_FUNCTION_CENSUS_INVALID",
            f"rows={len(rows) if isinstance(rows, list) else 'invalid'}",
        )
    signatures = [str(row.get("function_signature")) for row in rows]
    ordinals = [row.get("function_ordinal") for row in rows]
    if len(signatures) != len(set(signatures)) or ordinals != list(range(34)):
        _error(
            "TASK7_V13_ACL_FUNCTION_CENSUS_INVALID",
            "signature or ordinal census differs",
        )
    authority_ids = {str(row["authority_id"]) for row in artifact["sql_authority_registry"]["rows"]}
    for row in rows:
        if str(row.get("authority_id")) not in authority_ids:
            _error(
                "TASK7_V13_ACL_AUTHORITY_ORPHAN",
                str(row.get("authority_id")),
            )

    role = acl.get("guard_owner_role")
    if not isinstance(role, dict) or (
        role.get("role_name") != _GUARD_OWNER
        or role.get("provisioning_mode") != "OPERATOR_BOOTSTRAP_REQUIRED"
        or role.get("posture") != _EXPECTED_POSTURE
        or role.get("role_membership_tuple") != []
        or role.get("table_ownership_tuple") != []
        or role.get("schema_usage_tuple") != ["public"]
        or role.get("schema_create") is not False
        or role.get("insert_relation_tuple") != []
        or role.get("update_relation_tuple") != []
        or role.get("delete_relation_tuple") != []
        or role.get("truncate_relation_tuple") != []
        or role.get("references_relation_tuple") != []
        or role.get("trigger_relation_tuple") != []
        or role.get("rls_contract_tuple") != _EXPECTED_RLS_CONTRACT
    ):
        _error("TASK7_V13_GUARD_OWNER_POSTURE_INVALID", "owner posture differs")
    preflight = bytes.fromhex(str(role.get("clean_role_preflight_sql_utf8_hex"))).decode("utf-8")
    installation = [
        bytes.fromhex(value).decode("utf-8") for value in acl["installation_sql_utf8_hex_tuple"]
    ]
    if (
        installation[0] != preflight
        or "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN" not in preflight
        or any(
            catalog not in preflight
            for catalog in (
                "pg_catalog.pg_auth_members",
                "pg_catalog.pg_database",
                "pg_catalog.pg_namespace",
                "pg_catalog.pg_class",
                "pg_catalog.pg_attribute",
                "pg_catalog.pg_proc",
                "pg_catalog.pg_default_acl",
            )
        )
    ):
        _error(
            "TASK7_V13_GUARD_OWNER_CLEAN_PREFLIGHT_INVALID",
            "clean-role catalog preflight differs",
        )
    retained_projection = _MATERIALIZER._retained_0037_catalog_projection(artifact)
    retained_trigger_projection = _MATERIALIZER._retained_0037_trigger_catalog_projection(artifact)
    retained_preflight = _MATERIALIZER._retained_0037_catalog_assertion_sql(retained_projection)
    if (
        acl.get("retained_0037_catalog_projection_tuple") != retained_projection
        or acl.get("retained_0037_trigger_catalog_projection_tuple") != retained_trigger_projection
        or acl.get("retained_0037_catalog_preflight_sql_utf8_hex")
        != retained_preflight.encode("utf-8").hex()
        or installation[2] != retained_preflight
    ):
        _error(
            "TASK7_V13_RETAINED_0037_CATALOG_BASELINE_INVALID",
            "retained function owner/body/catalog projection differs",
        )

    scope_counts = {
        scope: sum(row.get("install_scope") == scope for row in rows)
        for scope in ("CURRENT_0038", "FUTURE_INACTIVE", "EXTERNAL_DEPENDENCY")
    }
    if scope_counts != {
        "CURRENT_0038": 19,
        "FUTURE_INACTIVE": 14,
        "EXTERNAL_DEPENDENCY": 1,
    }:
        _error("TASK7_V13_ACL_ACTIVATION_SCOPE_INVALID", repr(scope_counts))
    current_signatures = [
        row["function_signature"] for row in rows if row["install_scope"] == "CURRENT_0038"
    ]
    if current_signatures != acl.get("current_install_function_signature_tuple"):
        _error("TASK7_V13_CURRENT_FUNCTION_CENSUS_INVALID", "current routines differ")

    by_signature = {str(row["function_signature"]): row for row in rows}
    helpers = {
        signature for signature, row in by_signature.items() if row.get("function_kind") == "HELPER"
    }
    trigger_guards = {
        signature
        for signature, row in by_signature.items()
        if row.get("function_kind") == "TRIGGER_GUARD"
    }
    if len(trigger_guards) != 25 or len(helpers) != 9:
        _error(
            "TASK7_V13_ACL_FUNCTION_CENSUS_INVALID",
            f"guards={len(trigger_guards)} helpers={len(helpers)}",
        )

    called_helpers = {
        str(callee) for row in rows for callee in row.get("helper_call_signature_tuple", [])
    }
    if not called_helpers <= helpers or any(
        helper not in called_helpers
        for helper in helpers
        if helper not in {"public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)"}
    ):
        _error("TASK7_V13_ACL_HELPER_ORPHAN", repr(sorted(helpers - called_helpers)))

    targets = guard_target_authority(artifact)
    if len(targets) != len(set(targets)):
        _error("TASK7_V13_ACL_TARGET_CENSUS_INVALID", "duplicate target")
    target_events = guard_target_event_authority(artifact)
    if len(target_events) != len(set(target_events)):
        _error("TASK7_V13_ACL_TARGET_EVENT_CENSUS_INVALID", "duplicate coordinate")
    scope_target_census = {
        scope: (
            sum(len(row["target_trigger_tuple"]) for row in rows if row["install_scope"] == scope),
            sum(
                len(row["target_event_coordinate_tuple"])
                for row in rows
                if row["install_scope"] == scope
            ),
        )
        for scope in ("CURRENT_0038", "FUTURE_INACTIVE")
    }
    if scope_target_census != {
        "CURRENT_0038": (73, 192),
        "FUTURE_INACTIVE": (81, 242),
    }:
        _error(
            "TASK7_V13_ACL_TARGET_EVENT_CENSUS_INVALID",
            repr(scope_target_census),
        )
    for signature in trigger_guards:
        row = by_signature[signature]
        if row.get("security_mode") != "DEFINER" or not row.get("target_trigger_tuple"):
            _error("TASK7_V13_TRIGGER_AUTHORITY_INVALID", signature)
        target_set = {tuple(map(str, target)) for target in row["target_trigger_tuple"]}
        coordinate_target_set = {
            (str(value[0]), str(value[1])) for value in row["target_event_coordinate_tuple"]
        }
        if target_set != coordinate_target_set:
            _error("TASK7_V13_TRIGGER_COORDINATE_CLOSURE_INVALID", signature)
    for signature in helpers:
        if by_signature[signature].get("target_trigger_tuple") != []:
            _error("TASK7_V13_HELPER_TARGET_INVALID", signature)

    direct_execute = {
        str(row["function_signature"]): tuple(row["explicit_execute_grantee_tuple"])
        for row in rows
        if row["explicit_execute_grantee_tuple"]
    }
    if direct_execute != {
        "public.idis_task7_approved_erasure_context_v1(uuid,uuid)": (_GUARD_OWNER,)
    }:
        _error("TASK7_V13_HELPER_EXECUTE_GRANT_INVALID", repr(direct_execute))

    current_relations: set[str] = set()
    current_columns: dict[str, set[str]] = {}
    for row in rows:
        if (
            row.get("public_execute") is not False
            or row.get("application_role") != _APPLICATION_PRINCIPAL
            or row.get("application_role_binding") != "RUNTIME_SESSION_USER"
            or row.get("application_role_execute") is not False
            or row.get("search_path_tuple") != ["pg_catalog", "pg_temp"]
            or row.get("insert_relation_tuple") != []
            or row.get("update_relation_tuple") != []
            or row.get("delete_relation_tuple") != []
            or row.get("truncate_relation_tuple") != []
            or row.get("references_relation_tuple") != []
            or row.get("trigger_relation_tuple") != []
        ):
            _error(
                "TASK7_V13_FUNCTION_ACL_INVALID",
                str(row.get("function_signature")),
            )
        if [item[0] for item in row["direct_read_column_tuple"]] != row["select_relation_tuple"]:
            _error(
                "TASK7_V13_DIRECT_READ_CLOSURE_INVALID",
                str(row["function_signature"]),
            )
        transitive = {
            str(relation): set(map(str, columns))
            for relation, columns, _evidence in row["transitive_read_column_tuple"]
        }
        for relation, columns, _evidence in row["direct_read_column_tuple"]:
            if not columns:
                _error(
                    "TASK7_V13_DIRECT_READ_COLUMN_CLOSURE_INCOMPLETE",
                    f"{row['function_signature']}:{relation}",
                )
            if not set(map(str, columns)) <= transitive.get(str(relation), set()):
                _error(
                    "TASK7_V13_TRANSITIVE_READ_CLOSURE_INVALID",
                    str(row["function_signature"]),
                )
        for callee in row["helper_call_signature_tuple"]:
            if callee not in by_signature:
                _error("TASK7_V13_ACL_HELPER_ORPHAN", str(callee))
            for relation, columns, _evidence in by_signature[callee][
                "transitive_read_column_tuple"
            ]:
                if not set(map(str, columns)) <= transitive.get(str(relation), set()):
                    _error(
                        "TASK7_V13_TRANSITIVE_READ_CLOSURE_INVALID",
                        f"{row['function_signature']}->{callee}",
                    )
        if row["install_scope"] == "CURRENT_0038":
            current_relations.update(map(str, row["select_relation_tuple"]))
            for relation, columns, _evidence in row["direct_read_column_tuple"]:
                current_columns.setdefault(str(relation), set()).update(map(str, columns))
    if len(current_relations) != 36 or set(role["select_relation_tuple"]) != current_relations:
        _error(
            "TASK7_V13_CURRENT_READ_CLOSURE_INVALID",
            f"derived={len(current_relations)} role={len(role['select_relation_tuple'])}",
        )
    expected_relation_columns = [
        [relation, sorted(current_columns[relation])] for relation in role["select_relation_tuple"]
    ]
    if (
        role.get("select_relation_column_tuple") != expected_relation_columns
        or sum(len(columns) for _relation, columns in expected_relation_columns) != 366
    ):
        _error(
            "TASK7_V13_CURRENT_COLUMN_CLOSURE_INVALID",
            "current relation/column union differs from 36/366",
        )
    table_revokes, column_revokes, column_grants = _MATERIALIZER._column_acl_sql(
        expected_relation_columns
    )
    encoded_grants = [value.encode("utf-8").hex() for value in column_grants]
    encoded_revokes = [value.encode("utf-8").hex() for value in column_revokes]
    installation = [
        bytes.fromhex(value).decode("utf-8") for value in acl["installation_sql_utf8_hex_tuple"]
    ]
    if (
        acl.get("current_column_grant_sql_utf8_hex_tuple") != encoded_grants
        or acl.get("current_column_revoke_sql_utf8_hex_tuple") != encoded_revokes
        or any("GRANT SELECT ON TABLE" in value for value in installation)
        or any(value not in installation for value in (*column_revokes, *column_grants))
    ):
        _error(
            "TASK7_V13_COLUMN_ACL_AUTHORITY_INVALID",
            "column-scoped installation bytes differ",
        )
    expected_downgrade = _MATERIALIZER._build_downgrade_authority(
        artifact,
        rows,
        table_revokes,
        column_revokes,
    )
    if acl.get("downgrade_authority") != expected_downgrade:
        _error(
            "TASK7_V13_DOWNGRADE_AUTHORITY_INVALID",
            "downgrade bytes differ from deterministic derivation",
        )
    downgrade_zero = bytes.fromhex(
        acl["downgrade_authority"]["zero_privilege_assertion_sql_utf8_hex"]
    ).decode("utf-8")
    if (
        downgrade_zero.replace(
            "TASK7_GUARD_FUNCTION_OWNER_DOWNGRADE_ACL_INVALID",
            "TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN",
        )
        != preflight
    ):
        _error(
            "TASK7_V13_DOWNGRADE_ZERO_PRIVILEGE_CLOSURE_INVALID",
            "install and downgrade clean-role catalog predicates differ",
        )
    if (
        bytes.fromhex(
            acl["downgrade_authority"]["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ).decode("utf-8")
        != retained_preflight
    ):
        _error(
            "TASK7_V13_RETAINED_0037_CATALOG_RESTORE_INVALID",
            "downgrade post-restore assertion differs from pre-mutation baseline",
        )

    expected = _MATERIALIZER._build_acl_registry(artifact)
    if acl != expected:
        _error(
            "TASK7_V13_ACL_REGISTRY_DERIVATION_DRIFT",
            "literal ACL bytes differ from the reviewed derivation",
        )


def _validate_shape(
    raw: bytes,
    artifact: dict[str, Any],
    *,
    require_frozen: bool = False,
) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "section order differs")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v13-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v13"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v13 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V13_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v13 bytes",
        )

    _validate_hash_bound_acl(artifact)
    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v13 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v13 reviewed design",
    )
    if (
        not isinstance(reviewed_value, list)
        or reviewed_value[:2]
        != [
            "full-strict-v13-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v13"],
        ]
        or reviewed_value[-1] != _MATERIALIZER._reviewed_acl_design_tuple()
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v13 reviewed design differs")

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v13","full-strict-v13",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if recipes[5] != expected_recipe or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v13 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v12 = _MATERIALIZER.project_v13_to_v12(candidate)
        _MATERIALIZER._validate_exact_diff(v12, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V13_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v12 successor",
        ) from exc
    _V12._validate_shape(_render_artifact(v12), v12)
    _validate_guard_function_acl_registry(artifact)
    validate_before_generated_column_access(artifact)
    if invariant_vector(artifact) != _V12.invariant_vector(v12):
        _error(
            "TASK7_V13_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V13_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(
    raw: bytes,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Derive every v13 component solely from the supplied artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v13-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    v12_projection = _MATERIALIZER.project_v13_to_v12(artifact)
    components = cast(
        dict[str, str],
        _V12._V11._V10._V9._V7._V6._V5._derive_components(
            v12_projection,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v13",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v13-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v13",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V13_CONTRACT_HASH": pipeline_hash,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v13_reviewed_preimage_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.artifact.read_bytes()
        artifact = _decode_artifact(raw)
        _validate_shape(raw, artifact)
        if artifact["metadata"]["append_frozen"] is False:
            print("TASK7_V13_FIXTURE_PUBLICATION_REFUSED_UNFROZEN", file=sys.stderr)
            return 2
    except (ContractArtifactError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("TASK7_V13_FIXTURE_PUBLICATION_REQUIRES_REVIEWED_FREEZE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
