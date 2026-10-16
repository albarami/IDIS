"""Materialize the bounded, unfrozen full-strict-v21 reviewed candidate.

Frozen v20 remains immutable and superseded.  V21 adds direct schema USAGE
for every erasure-runner EXECUTE grantee in each authoritative active ACL
copy.  It also adds a generation-time executable-call closure that binds the
schema revoke and composed function-drop inverses.  No migration, runtime,
table, role, environment, or semantic-vector bytes are sourced here.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never, cast

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V20_PATH = _SCRIPT_DIR / "materialize_full_strict_v20_preimage.py"
_V20_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_SOURCE_SHA512 = (
    "6d4d421e975c7eb6b159faf6cd86535d3929bc11d6c882f5ac0f1eb649e16082"
    "e7f2132643491c75a3d5e67f871855750114480d26ce63f260eeac0cadfc6e42"
)
_V20_ARTIFACT_SHA512 = (
    "5d1026a05dc1bf4979f8e3561f96bc459df7841698fe14007f36fca54b865d624"
    "20eac45dae025042494eb6a120d0357a9496dc719fe3a00b333c86730e6a674"
)
_V20 = "full-strict-v20"
_V21 = "full-strict-v21"
_V21_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v21-sql-authority-registry-v1"
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
_DIRECT_ACL_FIELDS = (
    "current_acl_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
)
_COMPOSED_ACL_FIELDS = (
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
)
_ALL_ACL_FIELDS = (*_DIRECT_ACL_FIELDS, *_COMPOSED_ACL_FIELDS)
_OWNER_USAGE = "GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner"
_EXECUTOR_USAGE = "GRANT USAGE ON SCHEMA public TO idis_erasure_executor"
_RUNNER_DROP = "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_EXECUTE_GRANT_RE = re.compile(
    r"\AGRANT EXECUTE ON FUNCTION "
    r"(?P<schema>[a-z][a-z0-9_]*)\."
    r"(?P<function>[a-z][a-z0-9_]*\([^)]*\)) "
    r"TO (?P<role>[a-z][a-z0-9_]*)\Z"
)
_CLOSURE_DESIGN_ROW = [
    "erasure_executable_call_acl_closure",
    [
        "task7-executable-call-acl-closure-v1",
        "TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
        "CURRENT_FUTURE_ACTIVATION_RESTORATION",
        "EVERY_EXPLICIT_FUNCTION_EXECUTE_GRANTEE_HAS_DIRECT_SCHEMA_USAGE",
        "EXACT_SCHEMA_REVOKE_PRECEDES_SCHEMA_USAGE_AND_FUNCTION_EXECUTE",
        "ACTIVE_ACL_COPIES_ARE_BYTE_IDENTICAL_TO_COMPOSED_TAILS",
        "COMPOSED_FUNCTION_DROP_PRECEDES_REINSTALL",
        "CURRENT_RESTORES_THROUGH_FUTURE_RESTORATION",
        "FUTURE_ACTIVATES_THROUGH_FUTURE_ACTIVATION",
    ],
]

# Filled only after deterministic candidate construction; it is not an input
# to materialization and therefore cannot circularly influence candidate bytes.
V21_CANDIDATE_SHA256: Final = "6812ddcc97086d1d5089c8af41d94a9e4a9aede17db143f4f461dce3f3cf800b"


class MaterializationError(RuntimeError):
    """Stable fail-closed v21 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_pinned_script(path: Path, sha512: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(f"TASK7_V21_HELPER_MISSING:{path.name}") from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V21_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V21_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_M20 = _load_pinned_script(
    _V20_PATH,
    _V20_SOURCE_SHA512,
    "_task7_v20_materializer_for_v21",
)
_H = _M20._H


def _canonical_bytes(value: object) -> bytes:
    return bytes(_M20._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: Mapping[str, Any]) -> bytes:
    return bytes(_M20._render(dict(artifact)))


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_M20._changed_paths(old, new, path))


def _load_pinned_v20() -> dict[str, Any]:
    try:
        raw = _V20_ARTIFACT.read_bytes()
    except OSError as exc:
        raise MaterializationError("TASK7_V21_BASE_ARTIFACT_MISSING") from exc
    if hashlib.sha512(raw).hexdigest() != _V20_ARTIFACT_SHA512:
        _error("TASK7_V21_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V21_BASE_ARTIFACT_NOT_CANONICAL")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V21_BASE_ARTIFACT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("metadata", {}).get("artifact_schema")
        != "full-strict-v20-reviewed-preimage-v1"
        or value.get("metadata", {}).get("pipeline_contract_version") != _V20
        or value.get("metadata", {}).get("append_frozen") is not True
    ):
        _error("TASK7_V21_BASE_ARTIFACT_INVALID")
    return value


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V21_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if (
        not isinstance(row, dict)
        or row.get("authority_ordinal") != authority_ordinal
        or row.get("authority_id") != authority_id
    ):
        _error("TASK7_V21_AUTHORITY_SHAPE_INVALID", authority_id)
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
            _error("TASK7_V21_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        observed_count = sum(statement.count(_V20) for statement in statements)
        replacement = statements[statement_ordinal]
        if (
            observed_count != expected_count
            or replacement.count(_V20) != expected_count
            or replacement.count(_V21) != 0
        ):
            _error("TASK7_V21_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = replacement.replace(_V20, _V21)
        row["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
        _H._rehash_authority_row(row)


def _rehash_sql_registry(artifact: dict[str, Any]) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V21_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            _V21_SQL_REGISTRY_HASH_DOMAIN,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _decode_hex_tuple(row: Mapping[str, Any], field: str) -> list[str]:
    try:
        values = row[field]
        if not isinstance(values, list) or not values:
            _error("TASK7_V21_ERASURE_SQL_BASE_INVALID", field)
        return [bytes.fromhex(str(item)).decode("utf-8") for item in values]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError(f"TASK7_V21_ERASURE_SQL_BASE_INVALID:{field}") from exc


def _encode_hex_tuple(statements: list[str]) -> list[str]:
    if any(statement != " ".join(statement.split()) for statement in statements):
        _error("TASK7_V21_ERASURE_SQL_NOT_CANONICAL")
    return [statement.encode("utf-8").hex() for statement in statements]


def _erasure_row(artifact: Mapping[str, Any]) -> dict[str, Any]:
    section = artifact.get("erasure_execution_registry")
    if (
        not isinstance(section, dict)
        or tuple(section) != ("rows", "expected_sha256")
        or not isinstance(section.get("rows"), list)
        or len(section["rows"]) != 1
        or not isinstance(section["rows"][0], dict)
    ):
        _error("TASK7_V21_ERASURE_REGISTRY_BASE_INVALID")
    return cast(dict[str, Any], section["rows"][0])


def _insert_executor_schema_usage(statements: list[str], field: str) -> list[str]:
    if statements.count(_OWNER_USAGE) != 1 or _EXECUTOR_USAGE in statements:
        _error("TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID", f"{field}:base-usage")
    index = statements.index(_OWNER_USAGE) + 1
    return [*statements[:index], _EXECUTOR_USAGE, *statements[index:]]


def _transform_erasure_registry(artifact: dict[str, Any]) -> None:
    section = artifact["erasure_execution_registry"]
    row = _erasure_row(artifact)
    for field in _ALL_ACL_FIELDS:
        row[field] = _encode_hex_tuple(
            _insert_executor_schema_usage(_decode_hex_tuple(row, field), field)
        )
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _sha256(_ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload))
    section["expected_sha256"] = _h([_ERASURE_REGISTRY_HASH_DOMAIN, section["rows"]])


def _transform_reviewed_design(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V21_REVIEWED_DESIGN_BASE_INVALID") from exc
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v20-reviewed-design-literal-v1",
        ["pipeline_contract_version", _V20],
    ]:
        _error("TASK7_V21_REVIEWED_DESIGN_BASE_INVALID")
    if any(
        isinstance(row, list) and row and row[0] == "erasure_executable_call_acl_closure"
        for row in design
    ):
        _error("TASK7_V21_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = "full-strict-v21-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", _V21]
    design.append(copy.deepcopy(_CLOSURE_DESIGN_ROW))
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v21-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = _V21
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any]) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    expected = [
        "pipeline_contract_hash",
        '["pipeline-contract-v20","full-strict-v20",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V21_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        '["pipeline-contract-v21","full-strict-v21",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _execute_grants(statements: tuple[str, ...], field: str) -> tuple[tuple[str, str], ...]:
    grants: list[tuple[str, str]] = []
    for statement in statements:
        if not statement.startswith("GRANT EXECUTE ON FUNCTION "):
            continue
        match = _EXECUTE_GRANT_RE.fullmatch(statement)
        if match is None:
            _error("TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID", f"{field}:execute-shape")
        grants.append(
            (
                f"{match.group('schema')}.{match.group('function')}",
                match.group("role"),
            )
        )
    if not grants or len(grants) != len(set(grants)):
        _error("TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID", f"{field}:execute-census")
    return tuple(grants)


def validate_executable_call_acl_closure(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    """Bind EXECUTE, schema USAGE, direct zeroing, and composed restoration."""

    row = _erasure_row(artifact)
    decoded = {field: tuple(_decode_hex_tuple(row, field)) for field in _ALL_ACL_FIELDS}
    expected_composed = {
        "future_activation_sql_utf8_hex_tuple": (
            _RUNNER_DROP,
            *_decode_hex_tuple(row, "future_body_sql_utf8_hex_tuple"),
            *decoded["future_acl_sql_utf8_hex_tuple"],
        ),
        "future_restoration_sql_utf8_hex_tuple": (
            _RUNNER_DROP,
            *_decode_hex_tuple(row, "current_body_sql_utf8_hex_tuple"),
            *decoded["current_acl_sql_utf8_hex_tuple"],
        ),
    }
    for field, expected in expected_composed.items():
        if decoded[field] != expected:
            _error("TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID", f"{field}:composition")

    closure: list[tuple[str, str, str, str]] = []
    inverse_field = {
        "current_acl_sql_utf8_hex_tuple": "future_restoration_sql_utf8_hex_tuple",
        "future_acl_sql_utf8_hex_tuple": "future_activation_sql_utf8_hex_tuple",
        "future_activation_sql_utf8_hex_tuple": "future_activation_sql_utf8_hex_tuple",
        "future_restoration_sql_utf8_hex_tuple": "future_restoration_sql_utf8_hex_tuple",
    }
    for field in _ALL_ACL_FIELDS:
        statements = decoded[field]
        for function, role in _execute_grants(statements, field):
            schema = function.split(".", 1)[0]
            usage = f"GRANT USAGE ON SCHEMA {schema} TO {role}"
            zero = f"REVOKE ALL PRIVILEGES ON SCHEMA {schema} FROM {role}"
            if statements.count(usage) != 1 or statements.count(zero) != 1:
                _error(
                    "TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
                    f"{field}:{role}:schema-pair",
                )
            if not (
                statements.index(zero)
                < statements.index(usage)
                < statements.index(
                    f"GRANT EXECUTE ON FUNCTION {function} TO {role}"
                )
            ):
                _error(
                    "TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
                    f"{field}:{role}:order",
                )
            paired = inverse_field[field]
            if decoded[paired][0] != f"DROP FUNCTION {function}":
                _error(
                    "TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID",
                    f"{field}:{role}:function-inverse",
                )
            closure.append((field, function, role, paired))
    if len(closure) != len(_ALL_ACL_FIELDS):
        _error("TASK7_EXECUTABLE_CALL_ACL_CLOSURE_INVALID", "active-copy-census")
    return tuple(closure)


def _build_v21_core(v20: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v20)
    artifact["metadata"]["append_frozen"] = False
    _transform_version_authorities(artifact)
    _rehash_sql_registry(artifact)
    _transform_erasure_registry(artifact)
    _transform_reviewed_design(artifact)
    _transform_digest_recipe(artifact)
    _M20.validate_returning_select_closure(artifact)
    validate_executable_call_acl_closure(artifact)
    return artifact


def _validate_exact_diff(v20: dict[str, Any], v21: dict[str, Any]) -> None:
    if _render(v20) != _V20_ARTIFACT.read_bytes():
        _error("TASK7_V21_BASE_ARTIFACT_HASH_MISMATCH")
    if tuple(v21) != tuple(v20):
        _error("TASK7_V21_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v21[section] != v20[section]:
            _error("TASK7_V21_INVARIANT_DRIFT", section)
    changed = _changed_paths(v20, v21)
    if {path[0] for path in changed} != _APPROVED_CHANGED_SECTIONS:
        _error("TASK7_V21_EXACT_DIFF_INVALID", "top-level")
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != _ALLOWED_CHANGED_AUTHORITY_ORDINALS:
        _error("TASK7_V21_EXACT_DIFF_INVALID", "authority rows")
    if (
        v21.get("metadata", {}).get("artifact_schema") != "full-strict-v21-reviewed-preimage-v1"
        or v21.get("metadata", {}).get("pipeline_contract_version") != _V21
        or v21.get("metadata", {}).get("append_frozen") is not False
    ):
        _error("TASK7_V21_UNFROZEN_CANDIDATE_REQUIRED")
    expected = _build_v21_core(v20)
    if _render(expected) != _render(v21):
        _error("TASK7_V21_EXACT_DIFF_INVALID", "candidate bytes")
    validate_executable_call_acl_closure(v21)


def build_v21(v20: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v21 executable-call ACL candidate."""

    pinned = _load_pinned_v20()
    if _render(v20) != _render(pinned):
        _error("TASK7_V21_BASE_ARTIFACT_HASH_MISMATCH")
    artifact = _build_v21_core(v20)
    _validate_exact_diff(v20, artifact)
    return artifact


def project_v21_to_v20(v21: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact immutable frozen v20 bytes from v21."""

    v20 = _load_pinned_v20()
    _validate_exact_diff(v20, v21)
    projected = copy.deepcopy(v21)
    for section in _APPROVED_CHANGED_SECTIONS:
        projected[section] = copy.deepcopy(v20[section])
    if _render(projected) != _V20_ARTIFACT.read_bytes():
        _error("TASK7_V21_EXACT_V20_PROJECTION_HASH_MISMATCH")
    return projected


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V21_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V21_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V20_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if hashlib.sha512(raw).hexdigest() != _V20_ARTIFACT_SHA512:
            _error("TASK7_V21_BASE_ARTIFACT_HASH_MISMATCH")
        v20 = json.loads(raw)
        if not isinstance(v20, dict):
            _error("TASK7_V21_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v21(v20))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V21_UNFROZEN_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaterializationError",
    "V21_CANDIDATE_SHA256",
    "_load_pinned_v20",
    "_render",
    "_validate_exact_diff",
    "build_v21",
    "main",
    "project_v21_to_v20",
    "validate_executable_call_acl_closure",
]
