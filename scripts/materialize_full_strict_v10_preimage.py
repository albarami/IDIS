"""Materialize the unfrozen full-strict-v10 reviewed preimage candidate.

V9 never reached append-freeze and is never an admissible contract.  It is used
only as the reviewer-pinned, SHA-512-authenticated historical input to this
one-defect correction.  V10 changes the invalid schema-qualified multi-array
``unnest`` call to PostgreSQL's qualified ``ROWS FROM`` form, advances the
version-bound literals, and re-derives only their dependent hashes.

The command always emits ``append_frozen=false`` with exclusive-create
semantics.  Freeze and fixture publication remain a separate atomic admission
step after the complete signed PostgreSQL census.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V9_SOURCE_SHA512 = (
    "5b51205be91b013c6804f4f186a5f5413764a33c243457ff293617680772d6fa"
    "3a02e2ca1a455171ab9f2e8d08ebbc7ea0c08dd354eb93ed03a76d7de0e25878"
)
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
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
_VERSION_SQL_COORDINATES = (
    ("current-receipt-equality-v1", 2),
    ("current-task5-coverage-replacement-v1", 0),
)
_EXPECTED_CHANGED_PATHS = {
    ("metadata", "artifact_schema"),
    ("metadata", "pipeline_contract_version"),
    ("metadata", "reviewed_design_sha256"),
    ("metadata", "reviewed_design_utf8_hex"),
    ("digest_recipe_registry", "rows", 5, 1),
    ("digest_recipe_registry", "reviewed_literal_utf8_hex"),
    ("sql_authority_registry", "rows", 18, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 18, "expected_sha256"),
    ("sql_authority_registry", "rows", 19, "body_sql_utf8_hex_tuple", 2),
    ("sql_authority_registry", "rows", 19, "expected_sha256"),
    ("sql_authority_registry", "rows", 26, "body_sql_utf8_hex_tuple", 0),
    ("sql_authority_registry", "rows", 26, "expected_sha256"),
    ("sql_authority_registry", "expected_sha256"),
}


class MaterializationError(RuntimeError):
    """Stable fail-closed v10 materialization error."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _decode_statements(row: dict[str, Any]) -> list[str]:
    try:
        return [bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]]
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise MaterializationError("TASK7_V10_SQL_AUTHORITY_BODY_INVALID") from exc


def _encode_statements(statements: list[str]) -> list[str]:
    return [statement.encode("utf-8").hex() for statement in statements]


def _rehash_authority_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = hashlib.sha256(
        _ROW_HASH_DOMAIN + _canonical_bytes(payload)
    ).hexdigest()


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if type(old) is not type(new):
        return {path}
    if isinstance(old, dict):
        if tuple(old) != tuple(new):
            return {path}
        changed: set[tuple[str | int, ...]] = set()
        for key in old:
            changed.update(_changed_paths(old[key], new[key], (*path, key)))
        return changed
    if isinstance(old, list):
        if len(old) != len(new):
            return {path}
        changed = set()
        for ordinal, (old_child, new_child) in enumerate(zip(old, new, strict=True)):
            changed.update(
                _changed_paths(
                    old_child,
                    new_child,
                    (*path, ordinal),
                )
            )
        return changed
    return set() if old == new else {path}


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    old_version = "full-strict-v10" if reverse else "full-strict-v9"
    new_version = "full-strict-v9" if reverse else "full-strict-v10"
    expected = [
        f"{old_version}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old_version],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        raise MaterializationError("TASK7_V10_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = f"{new_version}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new_version]
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new_version}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = new_version
    metadata["append_frozen"] = False
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    recipes = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "10" if reverse else "9"
    new_ordinal = "9" if reverse else "10"
    old_version = "full-strict-v10" if reverse else "full-strict-v9"
    new_version = "full-strict-v9" if reverse else "full-strict-v10"
    expected = (
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    if recipes[5] != ["pipeline_contract_hash", expected]:
        raise MaterializationError("TASK7_V10_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _transform_sql_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    section = artifact["sql_authority_registry"]
    rows = section["rows"]
    if len(rows) != 28:
        raise MaterializationError("TASK7_V10_AUTHORITY_CENSUS_INVALID")
    by_id = {row["authority_id"]: row for row in rows}
    output = by_id.get("current-output-exact-one-v1")
    if output is None or output["authority_ordinal"] != 18:
        raise MaterializationError("TASK7_V10_OUTPUT_AUTHORITY_MISSING")

    output_statements = _decode_statements(output)
    old_expression = _ROWS_FROM_REPLACEMENT if reverse else _OLD_MULTI_ARRAY
    new_expression = _OLD_MULTI_ARRAY if reverse else _ROWS_FROM_REPLACEMENT
    if (
        len(output_statements) <= 2
        or output_statements[2].count(old_expression) != 1
        or new_expression in output_statements[2]
    ):
        raise MaterializationError("TASK7_V10_ROWS_FROM_REPLACEMENT_SHAPE_INVALID")
    output_statements[2] = output_statements[2].replace(
        old_expression,
        new_expression,
        1,
    )
    output["body_sql_utf8_hex_tuple"] = _encode_statements(output_statements)
    _rehash_authority_row(output)

    old_version = "full-strict-v10" if reverse else "full-strict-v9"
    new_version = "full-strict-v9" if reverse else "full-strict-v10"
    for authority_id, statement_ordinal in _VERSION_SQL_COORDINATES:
        row = by_id.get(authority_id)
        if row is None:
            raise MaterializationError(f"TASK7_V10_VERSION_AUTHORITY_MISSING:{authority_id}")
        statements = _decode_statements(row)
        if (
            len(statements) <= statement_ordinal
            or statements[statement_ordinal].count(old_version) != 1
            or new_version in statements[statement_ordinal]
        ):
            raise MaterializationError(f"TASK7_V10_VERSION_SQL_SHAPE_INVALID:{authority_id}")
        statements[statement_ordinal] = statements[statement_ordinal].replace(
            old_version,
            new_version,
            1,
        )
        row["body_sql_utf8_hex_tuple"] = _encode_statements(statements)
        _rehash_authority_row(row)

    section["expected_sha256"] = _h([_SQL_REGISTRY_HASH_DOMAIN, rows])


def project_v10_to_v9(v10: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact authenticated v9 bytes from a v10 candidate."""

    projected = copy.deepcopy(v10)
    _transform_sql_authorities(projected, reverse=True)
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V9_SOURCE_SHA512:
        raise MaterializationError("TASK7_V10_EXACT_V9_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v9: dict[str, Any], v10: dict[str, Any]) -> None:
    changed = _changed_paths(v9, v10)
    if changed != _EXPECTED_CHANGED_PATHS:
        rendered = ",".join("/".join(map(str, path)) for path in sorted(changed))
        raise MaterializationError(f"TASK7_V10_UNAUTHORIZED_DRIFT:{rendered}")
    projected = project_v10_to_v9(v10)
    if projected != v9:
        raise MaterializationError("TASK7_V10_V9_PROJECTION_DRIFT")


def _load_v9(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V9_SOURCE_SHA512:
        raise MaterializationError("TASK7_V10_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V10_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V10_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v9-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v9"
        or metadata.get("append_frozen") is not False
    ):
        raise MaterializationError("TASK7_V10_BASE_METADATA_INVALID")
    return value


def build_v10(v9: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v10 candidate."""

    artifact = copy.deepcopy(v9)
    _transform_sql_authorities(artifact, reverse=False)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v9, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V10_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V10_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v9_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v10(_load_v9(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V10_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
