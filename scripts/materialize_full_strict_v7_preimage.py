"""One-shot materializer for the reviewed full-strict-v7 SQL correction.

The append-frozen v6 artifact is the sole input. V7 inserts exactly one UTF-8
``)`` byte into authority ordinal 8 immediately before its first ``THEN RAISE``.
Only the directly dependent row/registry hashes and contract-version metadata
may change. The script creates v7 exclusively and never mutates v6.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V6_ARTIFACT_SHA512 = (
    "2d7e20b6b8d1552991c74572e20353c1827696d2d6667f7c249cd95facb10ce87"
    "3ecc317c568ad9798143273bc77b904456bd6b714ad409620672f600faa6a9e"
)
_GRADING_AUTHORITY_ID = "grading-dispatch-and-consistency-v1"
_SQL_AUTHORITY_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_SQL_AUTHORITY_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"
_UNCHANGED_SECTIONS = (
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
    "erasure_execution_registry",
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


class MaterializationError(RuntimeError):
    """Stable fail-closed v7 materialization error."""


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


def _sha512(value: bytes) -> str:
    return hashlib.sha512(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _load_v6(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if _sha512(raw) != _V6_ARTIFACT_SHA512:
        raise MaterializationError("TASK7_V7_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V7_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MaterializationError("TASK7_V7_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v6-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v6"
        or metadata.get("append_frozen") is not True
    ):
        raise MaterializationError("TASK7_V7_BASE_METADATA_INVALID")
    return raw, value


def _sql_authority_row_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return _sha256(_SQL_AUTHORITY_ROW_HASH_DOMAIN + _canonical_bytes(payload))


def _patch_grading_authority(artifact: dict[str, Any]) -> None:
    section = artifact.get("sql_authority_registry")
    if not isinstance(section, dict) or tuple(section) != (
        "rows",
        "expected_sha256",
    ):
        raise MaterializationError("TASK7_V7_SQL_AUTHORITY_REGISTRY_INVALID")
    rows = section.get("rows")
    if not isinstance(rows, list) or len(rows) != 17:
        raise MaterializationError("TASK7_V7_SQL_AUTHORITY_CENSUS_INVALID")
    row = rows[8]
    if not isinstance(row, dict) or (
        row.get("authority_ordinal") != 8 or row.get("authority_id") != _GRADING_AUTHORITY_ID
    ):
        raise MaterializationError("TASK7_V7_GRADING_AUTHORITY_NOT_ORDINAL_8")
    encoded = row.get("body_sql_utf8_hex_tuple")
    if not isinstance(encoded, list) or len(encoded) != 5:
        raise MaterializationError("TASK7_V7_GRADING_SQL_TUPLE_INVALID")
    try:
        body = bytes.fromhex(encoded[0])
    except (TypeError, ValueError) as exc:
        raise MaterializationError("TASK7_V7_GRADING_SQL_HEX_INVALID") from exc
    marker = b"THEN RAISE"
    insertion = body.find(marker)
    if insertion < 0 or body.find(marker, insertion + 1) >= 0:
        raise MaterializationError("TASK7_V7_GRADING_FIRST_THEN_RAISE_AMBIGUOUS")
    if body[insertion - 4 : insertion] != b">1) ":
        raise MaterializationError("TASK7_V7_GRADING_MISSING_PARENTHESIS_SHAPE_DRIFT")
    patched = body[:insertion] + b")" + body[insertion:]
    encoded[0] = patched.hex()
    row["expected_sha256"] = _sql_authority_row_hash(row)
    section["expected_sha256"] = _h([_SQL_AUTHORITY_REGISTRY_HASH_DOMAIN, rows])


def _transform_metadata(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V7_REVIEWED_DESIGN_BASE_INVALID") from exc
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v6-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v6"],
    ]:
        raise MaterializationError("TASK7_V7_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = "full-strict-v7-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", "full-strict-v7"]
    design.append(
        [
            "sql_authority_postgres_executability_correction",
            [
                ["authority_ordinal", 8],
                ["authority_id", _GRADING_AUTHORITY_ID],
                [
                    "exact_utf8_delta",
                    "insert_one_right_parenthesis_before_first_then_raise",
                ],
                ["check_authority_group_count", 3],
                ["check_expression_count", 5],
                ["function_trigger_group_count", 14],
                ["create_function_count", 14],
                ["create_trigger_count", 81],
                [
                    "fixture_admission_receipt_schema",
                    "task7-sql-authority-postgres-census-v1",
                ],
            ],
        ]
    )
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v7-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v7"
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v6","full-strict-v6",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]:
        raise MaterializationError("TASK7_V7_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _validate_exact_diff(
    v6: dict[str, Any],
    v7: dict[str, Any],
) -> None:
    if tuple(v7) != tuple(v6):
        raise MaterializationError("TASK7_V7_SECTION_ORDER_DRIFT")
    for section in _UNCHANGED_SECTIONS:
        if v7[section] != v6[section]:
            raise MaterializationError(f"TASK7_V7_UNAUTHORIZED_DRIFT:{section}")

    old_rows = v6["sql_authority_registry"]["rows"]
    new_rows = v7["sql_authority_registry"]["rows"]
    for ordinal, (old_row, new_row) in enumerate(zip(old_rows, new_rows, strict=True)):
        if ordinal != 8 and new_row != old_row:
            raise MaterializationError(f"TASK7_V7_UNAUTHORIZED_AUTHORITY_DRIFT:{ordinal}")
        if ordinal == 8:
            old_body = bytes.fromhex(old_row["body_sql_utf8_hex_tuple"][0])
            new_body = bytes.fromhex(new_row["body_sql_utf8_hex_tuple"][0])
            insertion = old_body.index(b"THEN RAISE")
            if new_body != (old_body[:insertion] + b")" + old_body[insertion:]):
                raise MaterializationError("TASK7_V7_EXACT_BYTE_DELTA_INVALID")
            for key in old_row.keys() - {
                "body_sql_utf8_hex_tuple",
                "expected_sha256",
            }:
                if new_row[key] != old_row[key]:
                    raise MaterializationError(f"TASK7_V7_UNAUTHORIZED_GRADING_FIELD_DRIFT:{key}")
            if new_row["body_sql_utf8_hex_tuple"][1:] != old_row["body_sql_utf8_hex_tuple"][1:]:
                raise MaterializationError("TASK7_V7_UNAUTHORIZED_GRADING_SQL_DRIFT")


def _build_v7(v6: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v6)
    _patch_grading_authority(artifact)
    _transform_metadata(artifact)
    _validate_exact_diff(v6, artifact)
    return artifact


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


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V7_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V7_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Materialize append-frozen full-strict-v7 exactly once.",
    )
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=(repo_root / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"),
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _raw, v6 = _load_v6(args.base_artifact)
        artifact = _build_v7(v6)
        rendered = _render(artifact)
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V7_ARTIFACT_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
