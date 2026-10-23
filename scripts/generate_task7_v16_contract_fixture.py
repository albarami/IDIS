"""Validate and derive the unfrozen full-strict-v16 contract candidate.

The reviewed artifact is the sole normative byte source. V16 binds every raw
and decoded SQL contract-version occurrence to one of two governed predicates
and proves that the guard ACL's SQL-body provenance is freshly derived from
the same artifact. Fixture publication remains unavailable until the reviewed
PostgreSQL census freezes the candidate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V16_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V15 = _load_script(
    _SCRIPT_DIR / "generate_task7_v15_contract_fixture.py",
    "_task7_v15_fixture_helpers_for_v16",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v16_preimage.py",
    "_task7_v16_materializer_helpers",
)

ContractArtifactError = _V15.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V15._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V15._COMPONENT_ORDER)
_V15_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v15-sql-authority-registry-v1"
_V16_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v16-sql-authority-registry-v1"
_VERSION_LITERAL_RE = re.compile(r"^full-strict-v[0-9]+$")
_RAW_VERSION_LITERAL_RE = re.compile(r"full-strict-v[0-9]+")
_DOLLAR_DELIMITER_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_GOVERNED_VERSION_COORDINATES = (
    (19, "current-receipt-equality-v1", 2, 0),
    (26, "current-task5-coverage-replacement-v1", 0, 0),
)
_GOVERNED_FUNCTION_IDENTITIES = {
    (19, 2): "public.idis_task7_current_receipt_equality_v1()",
    (26, 0): "public.idis_task5_coverage_scope_guard()",
}
_EXPECTED_FUNCTION_BODY_CENSUS = {
    "statement_count": 29,
    "function_body_count": 32,
    "multi_function_statement_rows": (
        (21, "current-append-only-lease-v1", 0, 2),
        (22, "current-source-bound-v1", 0, 2),
        (27, "current-task6-immutable-replacement-v1", 0, 2),
    ),
}
_CENSUS_RUNNER_PATH = _SCRIPT_DIR / "run_task7_v16_current_guard_census.py"
_CENSUS_RUNNER_SHA256 = "6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43"
_EXPECTED_CENSUS_SIGNER_ID = "slice100-governance-signer-v1"
_EXPECTED_CENSUS_PUBLIC_KEY_SHA256 = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_MAX_CENSUS_RECEIPT_BYTES = 8 * 1024 * 1024
_MAX_TRUSTED_PUBLIC_KEY_BYTES = 16 * 1024
_CENSUS_RUNNER_MODULE: ModuleType | None = None


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V15._canonical_bytes(value))


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
    return cast(dict[str, Any], _V15._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V15.invariant_vector(artifact))


def _decoded_statements(
    row: dict[str, Any],
    *,
    authority_ordinal: int,
) -> tuple[str, ...]:
    encoded = row.get("body_sql_utf8_hex_tuple")
    if not isinstance(encoded, list):
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            f"statement-vector:{authority_ordinal}",
        )
    statements: list[str] = []
    for statement_ordinal, value in enumerate(encoded):
        if (
            not isinstance(value, str)
            or len(value) % 2
            or re.fullmatch(r"[0-9a-f]*", value) is None
        ):
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"statement-hex:{authority_ordinal}:{statement_ordinal}",
            )
        try:
            statements.append(bytes.fromhex(value).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ContractArtifactError(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"statement-utf8:{authority_ordinal}:{statement_ordinal}",
            ) from exc
    return tuple(statements)


def _version_row(
    row: dict[str, Any],
    *,
    authority_ordinal: int,
    statement_ordinal: int,
    occurrence_ordinal: int,
    value: str,
) -> dict[str, Any]:
    return {
        "authority_ordinal": authority_ordinal,
        "authority_id": row["authority_id"],
        "statement_ordinal": statement_ordinal,
        "occurrence_ordinal": occurrence_ordinal,
        "pipeline_contract_version": value,
    }


def _mask_dollar_quoted_literals(sql: str) -> str:
    """Mask nested dollar strings while preserving executable SQL and offsets."""

    masked = list(sql)
    index = 0
    length = len(sql)
    while index < length:
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
            continue
        if sql[index] in {"'", '"'}:
            quote = sql[index]
            index += 1
            while index < length:
                if sql[index] != quote:
                    index += 1
                    continue
                if index + 1 < length and sql[index + 1] == quote:
                    index += 2
                    continue
                index += 1
                break
            continue
        if sql[index] == "$":
            match = _DOLLAR_DELIMITER_RE.match(sql, index)
            if match is not None:
                delimiter = match.group(0)
                end = sql.find(delimiter, match.end())
                if end < 0:
                    _error(
                        "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                        f"unterminated-dollar-string:{delimiter}",
                    )
                stop = end + len(delimiter)
                masked[index:stop] = " " * (stop - index)
                index = stop
                continue
        index += 1
    return "".join(masked)


def _skip_quoted_sql_token(sql: str, index: int) -> int:
    quote = sql[index]
    cursor = index + 1
    while cursor < len(sql):
        if sql[cursor] != quote:
            cursor += 1
            continue
        if cursor + 1 < len(sql) and sql[cursor + 1] == quote:
            cursor += 2
            continue
        return cursor + 1
    _error(
        "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
        "unterminated-quoted-sql-token",
    )


def _skip_block_comment(sql: str, index: int) -> int:
    depth = 1
    cursor = index + 2
    while cursor < len(sql) and depth:
        if sql.startswith("/*", cursor):
            depth += 1
            cursor += 2
        elif sql.startswith("*/", cursor):
            depth -= 1
            cursor += 2
        else:
            cursor += 1
    if depth:
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            "unterminated-block-comment",
        )
    return cursor


def _skip_space_and_comments(sql: str, index: int) -> int:
    cursor = index
    while cursor < len(sql):
        if sql[cursor].isspace():
            cursor += 1
            continue
        if sql.startswith("--", cursor):
            newline = sql.find("\n", cursor + 2)
            cursor = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", cursor):
            cursor = _skip_block_comment(sql, cursor)
            continue
        break
    return cursor


def _parse_function_identity(sql: str, index: int) -> str:
    cursor = _skip_space_and_comments(sql, index)
    parts: list[str] = []
    while True:
        match = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", sql[cursor:])
        if match is None:
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                "function-identity-invalid",
            )
        parts.append(match.group(0).casefold())
        cursor += len(match.group(0))
        cursor = _skip_space_and_comments(sql, cursor)
        if cursor >= len(sql) or sql[cursor] != ".":
            break
        cursor = _skip_space_and_comments(sql, cursor + 1)
    if cursor >= len(sql) or sql[cursor] != "(":
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            "function-argument-list-missing",
        )
    argument_cursor = _skip_space_and_comments(sql, cursor + 1)
    suffix = "()" if argument_cursor < len(sql) and sql[argument_cursor] == ")" else "(<args>)"
    return f"{'.'.join(parts)}{suffix}"


def _next_function_body_opener(
    sql: str,
    start: int,
) -> tuple[int, int, str, str] | None:
    """Find the next top-level CREATE FUNCTION ``AS $tag$`` opener."""

    cursor = start
    create_state = 0
    function_header_active = False
    function_identity: str | None = None
    while cursor < len(sql):
        if sql[cursor].isspace():
            cursor += 1
            continue
        if sql.startswith("--", cursor):
            newline = sql.find("\n", cursor + 2)
            cursor = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", cursor):
            cursor = _skip_block_comment(sql, cursor)
            continue
        if sql[cursor] in {"'", '"'}:
            cursor = _skip_quoted_sql_token(sql, cursor)
            continue
        if sql[cursor] == "$":
            dollar = _DOLLAR_DELIMITER_RE.match(sql, cursor)
            if dollar is not None:
                delimiter = dollar.group(0)
                end = sql.find(delimiter, dollar.end())
                if end < 0:
                    _error(
                        "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                        f"unterminated-top-level-dollar-data:{delimiter}",
                    )
                cursor = end + len(delimiter)
                continue
        if sql[cursor].isalpha() or sql[cursor] == "_":
            end = cursor + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in {"_", "$"}):
                end += 1
            identifier = sql[cursor:end].casefold()
            if function_header_active and identifier == "as":
                delimiter_start = _skip_space_and_comments(sql, end)
                delimiter_match = _DOLLAR_DELIMITER_RE.match(sql, delimiter_start)
                if delimiter_match is not None:
                    if function_identity is None:
                        _error(
                            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                            "function-identity-unbound",
                        )
                    return (
                        delimiter_start,
                        delimiter_match.end(),
                        delimiter_match.group(0),
                        function_identity,
                    )
            if identifier == "create":
                create_state = 1
            elif create_state == 1 and identifier == "or":
                create_state = 2
            elif create_state == 2 and identifier == "replace":
                create_state = 3
            elif create_state in {1, 3} and identifier == "function":
                function_header_active = True
                function_identity = _parse_function_identity(sql, end)
                create_state = 0
            elif not function_header_active:
                create_state = 0
            cursor = end
            continue
        if sql[cursor] == ";":
            create_state = 0
            function_header_active = False
            function_identity = None
        cursor += 1
    return None


def _function_body_spans(
    sql: str,
) -> tuple[tuple[str, int, int, int, str], ...]:
    spans: list[tuple[str, int, int, int, str]] = []
    cursor = 0
    while True:
        opener = _next_function_body_opener(sql, cursor)
        if opener is None:
            break
        delimiter_start, body_start, delimiter, identity = opener
        body_end = sql.find(delimiter, body_start)
        if body_end < 0:
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"function-body-dollar-delimiter-unclosed:{delimiter}",
            )
        spans.append((identity, delimiter_start, body_start, body_end, delimiter))
        cursor = body_end + len(delimiter)
    return tuple(spans)


def _executable_sql_text_and_body_count(sql: str) -> tuple[str, int]:
    executable_segments: list[str] = []
    cursor = 0
    spans = _function_body_spans(sql)
    for (
        _identity,
        delimiter_start,
        body_start,
        body_end,
        delimiter,
    ) in spans:
        executable_segments.extend(
            (
                _mask_dollar_quoted_literals(sql[cursor:delimiter_start]),
                " ",
                _mask_dollar_quoted_literals(sql[body_start:body_end]),
                " ",
            )
        )
        cursor = body_end + len(delimiter)
    executable_segments.append(_mask_dollar_quoted_literals(sql[cursor:]))
    return "".join(executable_segments), len(spans)


def _executable_sql_tokens(sql: str) -> tuple[tuple[str, str], ...]:
    """Lex executable SQL while treating nested dollar strings as opaque data.

    Every top-level ``AS $tag$...$tag$`` function body is retained as
    executable SQL, including when one authority statement installs multiple
    functions. Dollar-quoted values nested inside a body are data and are
    masked before the inherited SQL lexer sees them.
    """

    executable_sql, _body_count = _executable_sql_text_and_body_count(sql)
    return cast(
        tuple[tuple[str, str], ...],
        _V15._V14._V13._sql_tokens(executable_sql),
    )


def executable_function_body_census(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Return the exact current production function-body composition shape."""

    statement_count = 0
    function_body_count = 0
    multi_function_statement_rows: list[tuple[int, str, int, int]] = []
    rows = artifact["sql_authority_registry"]["rows"]
    for authority_ordinal, row in enumerate(rows):
        for statement_ordinal, statement in enumerate(
            _decoded_statements(row, authority_ordinal=authority_ordinal)
        ):
            _executable_sql, body_count = _executable_sql_text_and_body_count(statement)
            if body_count == 0:
                continue
            statement_count += 1
            function_body_count += body_count
            if body_count > 1:
                multi_function_statement_rows.append(
                    (
                        authority_ordinal,
                        row["authority_id"],
                        statement_ordinal,
                        body_count,
                    )
                )
    return {
        "statement_count": statement_count,
        "function_body_count": function_body_count,
        "multi_function_statement_rows": tuple(multi_function_statement_rows),
    }


def validate_executable_function_body_census(
    artifact: dict[str, Any],
) -> None:
    census = executable_function_body_census(artifact)
    if census != _EXPECTED_FUNCTION_BODY_CENSUS:
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            f"function-body-census:{census!r}",
        )


def contract_version_reference_inventory(
    artifact: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Return independent raw-byte and decoded-SQL version inventories."""

    try:
        rows = artifact["sql_authority_registry"]["rows"]
    except (KeyError, TypeError) as exc:
        raise ContractArtifactError(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            "authority-registry",
        ) from exc
    if not isinstance(rows, list):
        _error("TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID", "authority-vector")
    raw_rows: list[dict[str, Any]] = []
    decoded_rows: list[dict[str, Any]] = []
    for authority_ordinal, row in enumerate(rows):
        if not isinstance(row, dict) or (
            row.get("authority_ordinal") != authority_ordinal
            or not isinstance(row.get("authority_id"), str)
        ):
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"authority-shape:{authority_ordinal}",
            )
        for statement_ordinal, statement in enumerate(
            _decoded_statements(row, authority_ordinal=authority_ordinal)
        ):
            for occurrence_ordinal, match in enumerate(_RAW_VERSION_LITERAL_RE.finditer(statement)):
                raw_rows.append(
                    _version_row(
                        row,
                        authority_ordinal=authority_ordinal,
                        statement_ordinal=statement_ordinal,
                        occurrence_ordinal=occurrence_ordinal,
                        value=match.group(0),
                    )
                )
            try:
                tokens = _executable_sql_tokens(statement)
            except ContractArtifactError as exc:
                raise ContractArtifactError(
                    "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                    f"sql-lexer:{authority_ordinal}:{statement_ordinal}:{exc}",
                ) from exc
            decoded_ordinal = 0
            for kind, value in tokens:
                if kind != "STRING":
                    continue
                if "full-strict" in value and _VERSION_LITERAL_RE.fullmatch(value) is None:
                    _error(
                        "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                        f"dynamic-fragment:{authority_ordinal}:{statement_ordinal}",
                    )
                if _VERSION_LITERAL_RE.fullmatch(value) is None:
                    continue
                decoded_rows.append(
                    _version_row(
                        row,
                        authority_ordinal=authority_ordinal,
                        statement_ordinal=statement_ordinal,
                        occurrence_ordinal=decoded_ordinal,
                        value=value,
                    )
                )
                decoded_ordinal += 1
    return {
        "raw_rows": tuple(raw_rows),
        "decoded_literal_rows": tuple(decoded_rows),
    }


def contract_version_reference_authority(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Compatibility surface: decoded governed literal authority."""

    return contract_version_reference_inventory(artifact)["decoded_literal_rows"]


def _pipeline_contract_version_comparisons(
    artifact: dict[str, Any],
) -> tuple[tuple[int, int, str], ...]:
    comparisons: list[tuple[int, int, str]] = []
    rows = artifact["sql_authority_registry"]["rows"]
    for (
        authority_ordinal,
        authority_id,
        statement_ordinal,
        _occurrence,
    ) in _GOVERNED_VERSION_COORDINATES:
        try:
            row = rows[authority_ordinal]
        except (IndexError, TypeError) as exc:
            raise ContractArtifactError(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"authority-binding:{authority_ordinal}",
            ) from exc
        if row.get("authority_id") != authority_id:
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"authority-binding:{authority_ordinal}",
            )
        statements = _decoded_statements(row, authority_ordinal=authority_ordinal)
        if statement_ordinal >= len(statements):
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"statement-binding:{authority_ordinal}:{statement_ordinal}",
            )
        statement = statements[statement_ordinal]
        expected_identity = _GOVERNED_FUNCTION_IDENTITIES.get(
            (authority_ordinal, statement_ordinal)
        )
        if expected_identity is None:
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"function-authority-unbound:{authority_ordinal}:{statement_ordinal}",
            )
        matching_spans = tuple(
            span for span in _function_body_spans(statement) if span[0] == expected_identity
        )
        if len(matching_spans) != 1:
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"function-identity-binding:{authority_ordinal}:{statement_ordinal}:"
                f"{tuple(span[0] for span in _function_body_spans(statement))!r}",
            )
        _identity, _delimiter_start, body_start, body_end, _delimiter = matching_spans[0]
        raw_matches = tuple(_RAW_VERSION_LITERAL_RE.finditer(statement))
        if (
            len(raw_matches) != 1
            or raw_matches[0].group(0) != artifact["metadata"]["pipeline_contract_version"]
            or raw_matches[0].start() < body_start
            or raw_matches[0].end() > body_end
        ):
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"version-outside-governed-body:{authority_ordinal}:{statement_ordinal}",
            )
        try:
            tokens = _V15._V14._V13._sql_tokens(
                _mask_dollar_quoted_literals(statement[body_start:body_end])
            )
        except ContractArtifactError as exc:
            raise ContractArtifactError(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"sql-lexer:{authority_ordinal}:{statement_ordinal}:{exc}",
            ) from exc
        exact_predicates = tuple(
            token_ordinal
            for token_ordinal in range(len(tokens) - 4)
            if tokens[token_ordinal : token_ordinal + 5]
            == (
                ("IDENT", "r"),
                ("SYMBOL", "."),
                ("IDENT", "pipeline_contract_version"),
                ("SYMBOL", "="),
                ("STRING", artifact["metadata"]["pipeline_contract_version"]),
            )
        )
        comparison_mentions = tuple(
            token_ordinal
            for token_ordinal, token in enumerate(tokens)
            if token == ("IDENT", "pipeline_contract_version")
            and token_ordinal + 1 < len(tokens)
            and tokens[token_ordinal + 1] == ("SYMBOL", "=")
        )
        if len(exact_predicates) != 1 or comparison_mentions != (exact_predicates[0] + 2,):
            _error(
                "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
                f"predicate-binding:{authority_ordinal}:{statement_ordinal}:"
                f"exact={exact_predicates!r}:comparisons={comparison_mentions!r}",
            )
        comparisons.append(
            (
                authority_ordinal,
                statement_ordinal,
                artifact["metadata"]["pipeline_contract_version"],
            )
        )
    return tuple(comparisons)


def validate_contract_version_reference_authority(
    artifact: dict[str, Any],
) -> None:
    metadata_version = artifact.get("metadata", {}).get("pipeline_contract_version")
    if (
        not isinstance(metadata_version, str)
        or _VERSION_LITERAL_RE.fullmatch(metadata_version) is None
    ):
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            "metadata-version",
        )
    validate_executable_function_body_census(artifact)
    inventory = contract_version_reference_inventory(artifact)
    raw_rows = inventory["raw_rows"]
    decoded_rows = inventory["decoded_literal_rows"]
    raw_projection = tuple(
        (
            row["authority_ordinal"],
            row["authority_id"],
            row["statement_ordinal"],
            row["occurrence_ordinal"],
            row["pipeline_contract_version"],
        )
        for row in raw_rows
    )
    decoded_projection = tuple(
        (
            row["authority_ordinal"],
            row["authority_id"],
            row["statement_ordinal"],
            row["occurrence_ordinal"],
            row["pipeline_contract_version"],
        )
        for row in decoded_rows
    )
    expected = tuple(
        (*coordinate, metadata_version) for coordinate in _GOVERNED_VERSION_COORDINATES
    )
    if raw_projection != expected or decoded_projection != expected:
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            f"inventory:raw={raw_projection!r}:decoded={decoded_projection!r}",
        )
    expected_comparisons = tuple(
        (authority_ordinal, statement_ordinal, metadata_version)
        for authority_ordinal, _authority_id, statement_ordinal, _occurrence in (
            _GOVERNED_VERSION_COORDINATES
        )
    )
    comparisons = _pipeline_contract_version_comparisons(artifact)
    if comparisons != expected_comparisons:
        _error(
            "TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
            f"comparison-set:{comparisons!r}",
        )


_invalid_bare_new_contexts = _V15._invalid_bare_new_contexts


def _project_for_v15_class_guards(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    projected = copy.deepcopy(artifact)
    projected["metadata"]["append_frozen"] = False
    return cast(dict[str, Any], _MATERIALIZER.project_v16_to_v15(projected))


def before_generated_column_coverage(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    try:
        return cast(
            tuple[dict[str, Any], ...],
            _V15.before_generated_column_coverage(_project_for_v15_class_guards(artifact)),
        )
    except ContractArtifactError as exc:
        raise ContractArtifactError(
            "TASK7_V16_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            str(exc),
        ) from exc


def validate_before_generated_column_access(artifact: dict[str, Any]) -> None:
    before_generated_column_coverage(artifact)


def before_generated_column_coverage_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v16-before-generated-column-coverage-v1",
            list(before_generated_column_coverage(artifact)),
        ]
    )


def guard_target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return cast(tuple[tuple[Any, ...], ...], _V15.guard_target_event_authority(artifact))


def validate_acl_provenance(artifact: dict[str, Any]) -> None:
    """Require the exact ACL registry freshly derived from current SQL bodies."""

    try:
        expected = _MATERIALIZER._build_acl_registry(artifact)
        actual = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V16_ACL_PROVENANCE_INVALID",
            f"derivation:{type(exc).__name__}",
        ) from exc
    if actual != expected:
        _error(
            "TASK7_V16_ACL_PROVENANCE_INVALID",
            "guard-function ACL does not equal current-body derivation",
        )


def _validate_hash_bound_acl(artifact: dict[str, Any]) -> None:
    sql = artifact["sql_authority_registry"]
    acl = sql["guard_function_acl_registry"]
    _expect(
        _h([_V16_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]),
        sql.get("expected_sha256"),
        "v16 SQL authority registry",
    )
    historical_domain_projection = copy.deepcopy(artifact)
    historical_domain_projection["sql_authority_registry"]["expected_sha256"] = _h(
        [_V15_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]
    )
    _V15._validate_hash_bound_acl(historical_domain_projection)
    validate_acl_provenance(artifact)


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
        metadata.get("artifact_schema") != "full-strict-v16-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v16"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v16 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V16_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v16 bytes",
        )

    _validate_hash_bound_acl(artifact)
    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v16 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v16 reviewed design",
    )
    if (
        not isinstance(reviewed_value, list)
        or reviewed_value[:2]
        != [
            "full-strict-v16-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v16"],
        ]
        or reviewed_value[-1] != _MATERIALIZER._reviewed_acl_design_tuple()
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v16 reviewed design differs")

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v16","full-strict-v16",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    try:
        recipe_literal = bytes.fromhex(
            artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
        )
    except (TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v16 digest recipe encoding differs",
        ) from exc
    if recipes[5] != expected_recipe or recipe_literal != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v16 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v15 = _MATERIALIZER.project_v16_to_v15(candidate)
        _MATERIALIZER._validate_exact_diff(v15, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V16_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v15 successor",
        ) from exc
    _V15._validate_shape(_render_artifact(v15), v15)
    validate_contract_version_reference_authority(artifact)
    validate_before_generated_column_access(artifact)
    if invariant_vector(artifact) != _V15.invariant_vector(v15):
        _error(
            "TASK7_V16_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V16_UNFROZEN_CANDIDATE_REQUIRED",
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
) -> dict[str, Any]:
    """Derive components only from fully validated, append-frozen v16 bytes."""

    artifact = _decode_artifact(raw)
    _validate_shape(raw, artifact, require_frozen=True)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v16-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    v15 = _MATERIALIZER.project_v16_to_v15(artifact)
    v14 = _MATERIALIZER._V15.project_v15_to_v14(v15)
    v13 = _MATERIALIZER._V15._V14.project_v14_to_v13(v14)
    v12 = _MATERIALIZER._V15._V14._V13.project_v13_to_v12(v13)
    v11 = _V15._V14._V13._V12._MATERIALIZER.project_v12_to_v11(v12)
    components = cast(
        dict[str, str],
        _V15._V14._V13._V12._V11._V10._V9._V7._V6._V5._derive_components(
            v11,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v16",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v16-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v16",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V16_CONTRACT_HASH": pipeline_hash,
    }


def _render_fixture(value: dict[str, Any]) -> str:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _load_census_runner() -> ModuleType:
    global _CENSUS_RUNNER_MODULE
    try:
        raw = _CENSUS_RUNNER_PATH.read_bytes()
        if _sha256_bytes(raw) != _CENSUS_RUNNER_SHA256:
            raise ValueError("runner digest differs")
        if _CENSUS_RUNNER_MODULE is not None:
            return _CENSUS_RUNNER_MODULE
        module_name = "_task7_v16_atomic_admission_census_verifier"
        module = ModuleType(module_name)
        module.__file__ = str(_CENSUS_RUNNER_PATH)
        module.__package__ = ""
        sys.modules[module_name] = module
        # Compile the already-hashed buffer so neither a second path read nor a
        # pre-populated sys.modules entry can substitute verifier code.
        code = compile(raw, str(_CENSUS_RUNNER_PATH), "exec")
        exec(code, module.__dict__)  # noqa: S102 - exact hash-pinned source bytes
        if not callable(getattr(module, "verify_signed_receipt", None)):
            raise ValueError("runner verifier missing")
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed PostgreSQL authority census did not verify",
        ) from exc
    _CENSUS_RUNNER_MODULE = module
    return module


def verify_postgres_census_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify the exact signed 6/8/66 PostgreSQL census graph."""

    try:
        if (
            not isinstance(receipt_raw, bytes)
            or not 0 < len(receipt_raw) <= _MAX_CENSUS_RECEIPT_BYTES
            or not isinstance(trusted_public_key_raw, bytes)
            or not 0 < len(trusted_public_key_raw) <= _MAX_TRUSTED_PUBLIC_KEY_BYTES
        ):
            raise ValueError("receipt or key size differs")
        if not secrets.compare_digest(
            _sha256_bytes(trusted_public_key_raw),
            _EXPECTED_CENSUS_PUBLIC_KEY_SHA256,
        ):
            raise ValueError("trusted public key differs")

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate receipt field")
                result[key] = value
            return result

        receipt = json.loads(
            receipt_raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(receipt, dict) or not secrets.compare_digest(
            str(receipt.get("signer_id", "")),
            _EXPECTED_CENSUS_SIGNER_ID,
        ):
            raise ValueError("receipt signer differs")
        runner = _load_census_runner()
        payload = runner.verify_signed_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )
        if (
            not isinstance(payload, dict)
            or len(payload) != 8
            or not isinstance(payload.get("census_result"), dict)
            or len(payload["census_result"]) != 66
            or not isinstance(payload.get("database_execution"), dict)
            or len(payload["database_execution"]) != 26
        ):
            raise ValueError("closed receipt shape differs")
        return cast(dict[str, Any], payload)
    except Exception as exc:
        raise ContractArtifactError(
            "TASK7_V16_POSTGRES_CENSUS_RECEIPT_INVALID",
            "signed PostgreSQL authority census did not verify",
        ) from exc


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _path_is_reparse_or_symlink(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(value.st_mode):
        return True
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_trusted_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        _error("TASK7_V16_ADMISSION_PATH_INVALID", "target parent is missing")
    cursor = parent
    while True:
        if _path_is_reparse_or_symlink(cursor):
            _error(
                "TASK7_V16_ADMISSION_PATH_INVALID",
                "target parent is a symlink or reparse point",
            )
        try:
            if not stat.S_ISDIR(os.lstat(cursor).st_mode):
                _error(
                    "TASK7_V16_ADMISSION_PATH_INVALID",
                    "target ancestor is not a directory",
                )
        except OSError as exc:
            raise ContractArtifactError(
                "TASK7_V16_ADMISSION_PATH_INVALID",
                "target ancestor cannot be inspected",
            ) from exc
        if cursor.parent == cursor:
            break
        cursor = cursor.parent


def _assert_regular_file(path: Path, *, allow_absent: bool) -> None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        if allow_absent:
            return
        _error("TASK7_V16_ADMISSION_PATH_INVALID", "required file is missing")
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V16_ADMISSION_PATH_INVALID",
            "file cannot be inspected",
        ) from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or _path_is_reparse_or_symlink(path)
    ):
        _error(
            "TASK7_V16_ADMISSION_PATH_INVALID",
            "target is not a regular non-reparse file",
        )


def _lock_path(artifact_path: Path, fixture_path: Path | None = None) -> Path:
    del fixture_path
    artifact_path = Path(artifact_path)
    return artifact_path.with_name(f".{artifact_path.name}.task7-v16-admission.lock")


def _journal_path(artifact_path: Path, fixture_path: Path | None = None) -> Path:
    del fixture_path
    artifact_path = Path(artifact_path)
    return artifact_path.with_name(f".{artifact_path.name}.task7-v16-admission-journal.json")


def _stage_path(target: Path) -> Path:
    target = Path(target)
    return target.with_name(f".{target.name}.task7-v16-admission-stage")


def _validate_admission_paths(
    artifact_path: Path,
    fixture_path: Path,
) -> tuple[Path, Path]:
    artifact = Path(_normalized_path(artifact_path))
    fixture = Path(_normalized_path(fixture_path))
    if _normalized_path(artifact) == _normalized_path(fixture):
        _error("TASK7_V16_ADMISSION_PATH_INVALID", "artifact and fixture alias")
    _assert_trusted_parent(artifact)
    _assert_trusted_parent(fixture)
    _assert_regular_file(artifact, allow_absent=False)
    _assert_regular_file(fixture, allow_absent=True)
    reserved = (
        artifact,
        fixture,
        _lock_path(artifact),
        _journal_path(artifact),
        _stage_path(artifact),
        _stage_path(fixture),
    )
    identities = tuple(_normalized_path(path) for path in reserved)
    if len(set(identities)) != len(identities):
        _error("TASK7_V16_ADMISSION_PATH_INVALID", "reserved admission paths alias")
    return artifact, fixture


def _sync_parent_directory(path: Path) -> str:
    """Flush directory metadata where the platform exposes that primitive."""

    if os.name == "nt":
        return "WINDOWS_DIRECTORY_FLUSH_NOT_CLAIMED"
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return "POSIX_DIRECTORY_FSYNC"


def _write_through_create_new(path: Path, content: bytes) -> None:
    """Create new bytes durably; Windows uses FILE_FLAG_WRITE_THROUGH."""

    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DSYNC", getattr(os, "O_SYNC", 0))
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short durable write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    CREATE_NEW = 1
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        GENERIC_WRITE,
        FILE_SHARE_READ,
        None,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "target already exists", str(path))
        raise OSError(error, "CreateFileW failed", str(path))
    descriptor = msvcrt.open_osfhandle(
        int(handle),
        os.O_WRONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short durable write")
            view = view[written:]
        os_handle = msvcrt.get_osfhandle(descriptor)
        if not kernel32.FlushFileBuffers(wintypes.HANDLE(os_handle)):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
    finally:
        os.close(descriptor)


def _flush_existing_file(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    import ctypes
    from ctypes import wintypes

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_WRITE,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW flush open failed")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
    finally:
        kernel32.CloseHandle(handle)


def _write_durable_exact(
    path: Path,
    content: bytes,
    *,
    mismatch_code: str,
) -> bool:
    _assert_trusted_parent(path)
    if path.exists():
        _assert_regular_file(path, allow_absent=False)
        if path.read_bytes() != content:
            _error(mismatch_code, "durable sidecar differs")
        return False
    try:
        _write_through_create_new(path, content)
        _sync_parent_directory(path)
    except FileExistsError:
        _assert_regular_file(path, allow_absent=False)
        if path.read_bytes() != content:
            _error(mismatch_code, "durable sidecar differs")
        return False
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V16_ATOMIC_ADMISSION_STAGE_FAILED",
            "durable sidecar write failed",
        ) from exc
    return True


def _remove_transient(path: Path, expected: bytes | None = None) -> None:
    if not path.exists():
        return
    _assert_regular_file(path, allow_absent=False)
    if expected is not None and path.read_bytes() != expected:
        _error("TASK7_V16_ATOMIC_ADMISSION_CLEANUP_FAILED", "sidecar differs")
    path.unlink()
    _sync_parent_directory(path)


def _cleanup_transients(paths: tuple[tuple[Path, bytes | None], ...]) -> None:
    try:
        for path, expected in paths:
            _remove_transient(path, expected)
        if any(path.exists() for path, _expected in paths):
            raise OSError("transient sidecar remains")
    except (ContractArtifactError, OSError) as exc:
        raise ContractArtifactError(
            "TASK7_V16_ATOMIC_ADMISSION_CLEANUP_FAILED",
            "transient admission cleanup failed",
        ) from exc


def _atomic_publish_no_clobber(stage: Path, target: Path) -> None:
    """Publish a fixture atomically without an overwrite-capable primitive."""

    _assert_regular_file(stage, allow_absent=False)
    if target.exists():
        _error(
            "TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS",
            "fixture target already exists",
        )
    try:
        os.link(stage, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS",
            "fixture target already exists",
        ) from exc
    except OSError as exc:
        raise ContractArtifactError(
            "TASK7_V16_ATOMIC_ADMISSION_FAILED",
            "fixture no-clobber publication failed",
        ) from exc
    _flush_existing_file(target)
    _sync_parent_directory(target)
    _remove_transient(stage)


def _replace_artifact_stage(
    stage: Path,
    target: Path,
    expected_current: bytes,
) -> None:
    _assert_regular_file(stage, allow_absent=False)
    if not target.is_file() or target.read_bytes() != expected_current:
        _error(
            "TASK7_V16_CANDIDATE_CHANGED_DURING_ADMISSION",
            "artifact bytes changed before commit",
        )
    os.replace(stage, target)
    _flush_existing_file(target)
    _sync_parent_directory(target)


def _remove_published_fixture(path: Path, expected: bytes) -> None:
    _assert_regular_file(path, allow_absent=False)
    if path.read_bytes() != expected:
        _error(
            "TASK7_V16_ATOMIC_ADMISSION_ROLLBACK_MISMATCH",
            "published fixture differs",
        )
    path.unlink()
    _sync_parent_directory(path)


def _journal_bytes(
    *,
    artifact_path: Path,
    fixture_path: Path,
    candidate_raw: bytes,
    frozen_raw: bytes,
    fixture_raw: bytes,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": "task7-v16-atomic-admission-journal-v1",
                "artifact_identity_sha256": _sha256_bytes(
                    _normalized_path(artifact_path).encode("utf-8")
                ),
                "fixture_identity_sha256": _sha256_bytes(
                    _normalized_path(fixture_path).encode("utf-8")
                ),
                "candidate_artifact_sha256": _sha256_bytes(candidate_raw),
                "frozen_artifact_sha256": _sha256_bytes(frozen_raw),
                "fixture_sha256": _sha256_bytes(fixture_raw),
                "receipt_sha256": _sha256_bytes(receipt_raw),
                "trusted_public_key_sha256": _sha256_bytes(trusted_public_key_raw),
                "census_runner_sha256": _CENSUS_RUNNER_SHA256,
            },
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


@contextmanager
def _exclusive_admission_lock(artifact_path: Path) -> Iterator[None]:
    lock = _lock_path(artifact_path)
    _write_durable_exact(
        lock,
        b"\0",
        mismatch_code="TASK7_V16_ADMISSION_LOCK_MISMATCH",
    )
    _assert_regular_file(lock, allow_absent=False)
    with lock.open("r+b", buffering=0) as stream:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = cast(Any, __import__("fcntl"))
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _candidate_from_frozen(frozen_raw: bytes) -> bytes:
    artifact = _decode_artifact(frozen_raw)
    _validate_shape(frozen_raw, artifact, require_frozen=True)
    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    candidate_raw = _render_artifact(candidate)
    if derive_prospective_frozen_bytes(candidate_raw) != frozen_raw:
        _error(
            "TASK7_V16_FROZEN_ARTIFACT_RECOVERY_MISMATCH",
            "frozen artifact is not the exact candidate transition",
        )
    return candidate_raw


@dataclass(frozen=True)
class _AdmissionPlan:
    candidate_raw: bytes
    frozen_raw: bytes
    fixture_raw: bytes
    journal_raw: bytes
    result: dict[str, Any]


def _derive_admission_plan(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> _AdmissionPlan:
    current_raw = artifact_path.read_bytes()
    current = _decode_artifact(current_raw)
    _validate_shape(current_raw, current)
    if current["metadata"]["append_frozen"] is True:
        candidate_raw = _candidate_from_frozen(current_raw)
        frozen_raw = current_raw
    else:
        candidate_raw = current_raw
        frozen_raw = derive_prospective_frozen_bytes(candidate_raw)
    payload = verify_postgres_census_receipt(
        receipt_raw=receipt_raw,
        artifact_raw=candidate_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    result = derive_contract_bytes(frozen_raw)
    result["postgres_authority_census"] = payload["database_execution"]
    fixture_raw = _render_fixture(result).encode("utf-8")
    journal_raw = _journal_bytes(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        fixture_raw=fixture_raw,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    return _AdmissionPlan(
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        fixture_raw=fixture_raw,
        journal_raw=journal_raw,
        result=result,
    )


def _assert_existing_exact(path: Path, expected: bytes, code: str) -> bool:
    if not path.exists():
        return False
    _assert_regular_file(path, allow_absent=False)
    if path.read_bytes() != expected:
        _error(code, "recovery material differs")
    return True


def _admit_locked(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
    plan: _AdmissionPlan,
) -> dict[str, Any]:
    del receipt_raw, trusted_public_key_raw
    current_raw = artifact_path.read_bytes()
    if current_raw not in {plan.candidate_raw, plan.frozen_raw}:
        _error(
            "TASK7_V16_CANDIDATE_CHANGED_DURING_ADMISSION",
            "artifact bytes differ from the validated plan",
        )
    is_frozen = current_raw == plan.frozen_raw
    journal = _journal_path(artifact_path)
    artifact_stage = _stage_path(artifact_path)
    fixture_stage = _stage_path(fixture_path)
    fixture_exists = fixture_path.exists()
    journal_exists = _assert_existing_exact(
        journal,
        plan.journal_raw,
        "TASK7_V16_RECOVERY_JOURNAL_MISMATCH",
    )
    _assert_existing_exact(
        artifact_stage,
        plan.frozen_raw,
        "TASK7_V16_ARTIFACT_STAGE_MISMATCH",
    )
    _assert_existing_exact(
        fixture_stage,
        plan.fixture_raw,
        "TASK7_V16_FIXTURE_STAGE_MISMATCH",
    )

    if is_frozen and not journal_exists:
        _error(
            "TASK7_V16_RECOVERY_JOURNAL_REQUIRED",
            "frozen artifact has no exact recovery journal",
        )
    if fixture_exists:
        _assert_regular_file(fixture_path, allow_absent=False)
        fixture_exact = fixture_path.read_bytes() == plan.fixture_raw
        if not fixture_exact:
            code = (
                "TASK7_V16_INTERMEDIATE_FIXTURE_CHANGED"
                if journal_exists
                else "TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS"
            )
            _error(code, "fixture bytes differ")
        if not journal_exists and not is_frozen:
            _error(
                "TASK7_V16_FIXTURE_OUTPUT_ALREADY_EXISTS",
                "fixture exists without a recovery journal",
            )
    if not journal_exists and (artifact_stage.exists() or fixture_stage.exists()):
        _error(
            "TASK7_V16_RECOVERY_JOURNAL_REQUIRED",
            "orphan admission stage has no journal",
        )

    if not journal_exists:
        _write_durable_exact(
            journal,
            plan.journal_raw,
            mismatch_code="TASK7_V16_RECOVERY_JOURNAL_MISMATCH",
        )
        journal_exists = True
    try:
        if not is_frozen:
            if not fixture_exists:
                _write_durable_exact(
                    fixture_stage,
                    plan.fixture_raw,
                    mismatch_code="TASK7_V16_FIXTURE_STAGE_MISMATCH",
                )
            _write_durable_exact(
                artifact_stage,
                plan.frozen_raw,
                mismatch_code="TASK7_V16_ARTIFACT_STAGE_MISMATCH",
            )
            if artifact_path.read_bytes() != plan.candidate_raw:
                _error(
                    "TASK7_V16_CANDIDATE_CHANGED_DURING_ADMISSION",
                    "candidate changed before fixture publication",
                )
            if not fixture_exists:
                _atomic_publish_no_clobber(fixture_stage, fixture_path)
            if fixture_path.read_bytes() != plan.fixture_raw:
                _error(
                    "TASK7_V16_INTERMEDIATE_FIXTURE_CHANGED",
                    "fixture changed before artifact commit",
                )
            _replace_artifact_stage(
                artifact_stage,
                artifact_path,
                plan.candidate_raw,
            )
        else:
            if not fixture_exists:
                _write_durable_exact(
                    fixture_stage,
                    plan.fixture_raw,
                    mismatch_code="TASK7_V16_FIXTURE_STAGE_MISMATCH",
                )
                _atomic_publish_no_clobber(fixture_stage, fixture_path)
        _cleanup_transients(
            (
                (artifact_stage, plan.frozen_raw),
                (fixture_stage, plan.fixture_raw),
                (journal, plan.journal_raw),
            )
        )
    except ContractArtifactError:
        if (
            not is_frozen
            and journal_exists
            and fixture_path.exists()
            and artifact_path.read_bytes() == plan.candidate_raw
        ):
            try:
                _remove_published_fixture(fixture_path, plan.fixture_raw)
            except Exception as rollback_exc:
                raise ContractArtifactError(
                    "TASK7_V16_ATOMIC_ADMISSION_ROLLBACK_FAILED",
                    "fixture rollback failed; journal retained",
                ) from rollback_exc
        if not is_frozen and artifact_path.read_bytes() == plan.candidate_raw:
            _cleanup_transients(
                (
                    (artifact_stage, plan.frozen_raw),
                    (fixture_stage, plan.fixture_raw),
                    (journal, plan.journal_raw),
                )
            )
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        if (
            not is_frozen
            and journal_exists
            and fixture_path.exists()
            and artifact_path.read_bytes() == plan.candidate_raw
        ):
            try:
                _remove_published_fixture(fixture_path, plan.fixture_raw)
            except Exception as rollback_exc:
                raise ContractArtifactError(
                    "TASK7_V16_ATOMIC_ADMISSION_ROLLBACK_FAILED",
                    "fixture rollback failed; journal retained",
                ) from rollback_exc
        if not is_frozen and artifact_path.read_bytes() == plan.candidate_raw:
            _cleanup_transients(
                (
                    (artifact_stage, plan.frozen_raw),
                    (fixture_stage, plan.fixture_raw),
                    (journal, plan.journal_raw),
                )
            )
        raise ContractArtifactError(
            "TASK7_V16_ATOMIC_ADMISSION_FAILED",
            "atomic fixture/artifact publication failed",
        ) from exc
    return plan.result


def admit_candidate_atomically(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Publish fixture first and frozen artifact last after signed admission."""

    artifact, fixture = _validate_admission_paths(
        Path(artifact_path),
        Path(fixture_path),
    )
    plan = _derive_admission_plan(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    with _exclusive_admission_lock(artifact):
        return _admit_locked(
            artifact_path=artifact,
            fixture_path=fixture,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=trusted_public_key_raw,
            plan=plan,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-census-receipt", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        admit_candidate_atomically(
            artifact_path=args.artifact,
            fixture_path=args.output,
            receipt_raw=args.authority_census_receipt.read_bytes(),
            trusted_public_key_raw=args.trusted_public_key.read_bytes(),
        )
        return 0
    except (
        ContractArtifactError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
