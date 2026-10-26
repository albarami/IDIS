"""Materialize the bounded, unfrozen full-strict-v13 reviewed candidate.

V12 never reached append-freeze. V13 changes only the hash-bound
``current-source-bound-v1`` authority so its retained BEFORE triggers treat
stored generated columns according to PostgreSQL's BEFORE-row semantics.
Every semantic vector and all 192 trigger event coordinates remain unchanged.
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
from typing import Any, Never

_SCRIPT_DIR = Path(__file__).resolve().parent
_V12_PATH = _SCRIPT_DIR / "materialize_full_strict_v12_preimage.py"
_V12_SOURCE_SHA512 = (
    "49df285a505c1bc4630864547d2a79a0caed76e659ec56440240378195968fbd0"
    "fcc555cfd5bf481dd22cb4fbbf84763da64eb91edc29441d5170891dc63ba9f"
)
_V12_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v12-sql-authority-registry-v1"
_V13_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v13-sql-authority-registry-v1"
_SOURCE_AUTHORITY_ID = "current-source-bound-v1"
_SOURCE_AUTHORITY_ORDINAL = 22
_SOURCE_TARGETS = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
)

_PACKAGE_MUTABLE_V12 = (
    "ARRAY['status','source_binding_state','source_lineage_object_id','updated_at']::text[]"
)
_PACKAGE_MUTABLE_V13 = (
    "ARRAY['status','source_binding_state','source_lineage_object_id',"
    "'source_corpus_receipt_id','source_lineage_object_kind','updated_at']::text[]"
)
_CORPUS_NONNULL_V12 = (
    "(NEW.source_lineage_object_id IS NULL OR NEW.source_corpus_receipt_id IS NULL)"
)
_CORPUS_NONNULL_V13 = (
    "(NEW.source_lineage_object_id IS NULL OR "
    "(TG_TABLE_NAME NOT IN ('data_room_packages','data_room_package_files') "
    "AND NEW.source_corpus_receipt_id IS NULL))"
)
_SPAN_MUTABLE_V12 = (
    "ARRAY['source_binding_state','source_lineage_object_id',"
    "'source_corpus_receipt_id','parse_attempt_id']::text[]"
)
_SPAN_MUTABLE_V13 = (
    "ARRAY['source_binding_state','source_lineage_object_id',"
    "'source_corpus_receipt_id','source_lineage_object_kind','parse_attempt_id']::text[]"
)
_GENERIC_MUTABLE_V12 = (
    "ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id']::text[]"
)
_GENERIC_MUTABLE_V13 = (
    "ARRAY['source_binding_state','source_lineage_object_id',"
    "'source_corpus_receipt_id','source_lineage_object_kind']::text[]"
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v13 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V13_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V12 = _load_script(_V12_PATH, "_task7_v12_materializer_for_v13")
_build_acl_registry = _V12._build_acl_registry
_build_downgrade_authority = _V12._build_downgrade_authority
_column_acl_sql = _V12._column_acl_sql
_retained_0037_catalog_assertion_sql = _V12._retained_0037_catalog_assertion_sql
_retained_0037_catalog_projection = _V12._retained_0037_catalog_projection
_retained_0037_trigger_catalog_projection = _V12._retained_0037_trigger_catalog_projection


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V12._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V12._render(artifact))


def _reviewed_acl_design_tuple() -> list[Any]:
    return list(_V12._reviewed_acl_design_tuple())


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V12._changed_paths(old, new, path))


def _source_row(artifact: dict[str, Any]) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    matches = [row for row in rows if row.get("authority_id") == _SOURCE_AUTHORITY_ID]
    if (
        len(matches) != 1
        or int(matches[0].get("authority_ordinal", -1)) != _SOURCE_AUTHORITY_ORDINAL
        or tuple(target[0] for target in matches[0].get("target_tuple", ())) != _SOURCE_TARGETS
    ):
        _error("TASK7_V13_SOURCE_AUTHORITY_SHAPE_INVALID")
    return matches[0]


def _replace_exact(
    value: str,
    old: str,
    new: str,
    *,
    count: int,
    label: str,
) -> str:
    if value.count(old) != count or value.count(new) != 0:
        _error("TASK7_V13_SOURCE_BODY_BASE_INVALID", label)
    return value.replace(old, new)


def _transform_source_body(artifact: dict[str, Any], *, reverse: bool) -> None:
    row = _source_row(artifact)
    statements = list(_V12._decode_statements(row))
    if len(statements) != 8:
        _error("TASK7_V13_SOURCE_AUTHORITY_STATEMENT_CENSUS_INVALID")
    body = statements[0]
    replacements = (
        (_PACKAGE_MUTABLE_V13, _PACKAGE_MUTABLE_V12, 2, "package"),
        (_CORPUS_NONNULL_V13, _CORPUS_NONNULL_V12, 1, "nonnull"),
        (_SPAN_MUTABLE_V13, _SPAN_MUTABLE_V12, 2, "span"),
        (_GENERIC_MUTABLE_V13, _GENERIC_MUTABLE_V12, 2, "generic"),
    )
    if not reverse:
        replacements = tuple((new, old, count, label) for old, new, count, label in replacements)
    for old, new, count, label in replacements:
        body = _replace_exact(body, old, new, count=count, label=label)
    statements[0] = body
    row["body_sql_utf8_hex_tuple"] = list(_V12._encode_statements(statements))
    _V12._rehash_authority_row(row)


def _rebuild_acl_registry(
    artifact: dict[str, Any],
    *,
    sql_registry_hash_domain: str,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V13_SQL_REGISTRY_SHAPE_INVALID")
    artifact["sql_authority_registry"] = {
        "rows": section["rows"],
        "expected_sha256": "",
    }
    _V12._install_acl_registry(artifact)
    rebuilt = artifact["sql_authority_registry"]
    rebuilt["expected_sha256"] = _h(
        [
            sql_registry_hash_domain,
            rebuilt["rows"],
            rebuilt["guard_function_acl_registry"],
        ]
    )


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    old = "full-strict-v13" if reverse else "full-strict-v12"
    new = "full-strict-v12" if reverse else "full-strict-v13"
    expected = [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        _error("TASK7_V13_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = f"{new}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new]
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new}-reviewed-preimage-v1"
    metadata["append_frozen"] = False
    metadata["pipeline_contract_version"] = new
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "13" if reverse else "12"
    new_ordinal = "12" if reverse else "13"
    old_version = "full-strict-v13" if reverse else "full-strict-v12"
    new_version = "full-strict-v12" if reverse else "full-strict-v13"
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V13_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def project_v13_to_v12(v13: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-pinned v12 candidate from v13."""

    projected = copy.deepcopy(v13)
    _transform_source_body(projected, reverse=True)
    _rebuild_acl_registry(
        projected,
        sql_registry_hash_domain=_V12_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V12_SOURCE_SHA512:
        _error("TASK7_V13_EXACT_V12_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v12: dict[str, Any], v13: dict[str, Any]) -> None:
    if tuple(v13) != tuple(v12):
        _error("TASK7_V13_SECTION_ORDER_DRIFT")
    for section in v12:
        if section in {"metadata", "digest_recipe_registry", "sql_authority_registry"}:
            continue
        if v13[section] != v12[section]:
            _error("TASK7_V13_UNAUTHORIZED_DRIFT", section)
    changed = _changed_paths(v12, v13)
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != {_SOURCE_AUTHORITY_ORDINAL}:
        _error("TASK7_V13_SQL_AUTHORITY_DIFF_INVALID", repr(sorted(changed_rows)))
    if project_v13_to_v12(v13) != v12:
        _error("TASK7_V13_V12_PROJECTION_DRIFT")


def _load_v12(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V12_SOURCE_SHA512:
        _error("TASK7_V13_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V13_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw)
    if not isinstance(value, dict):
        _error("TASK7_V13_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v12-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v12"
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V13_BASE_METADATA_INVALID")
    return value


def build_v13(v12: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v13 candidate."""

    artifact = copy.deepcopy(v12)
    _transform_source_body(artifact, reverse=False)
    _rebuild_acl_registry(
        artifact,
        sql_registry_hash_domain=_V13_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v12, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V13_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V13_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v12_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v13(_load_v12(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V13_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
