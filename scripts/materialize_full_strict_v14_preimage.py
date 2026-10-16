"""Materialize the bounded, unfrozen full-strict-v14 reviewed candidate.

V13 never reached append-freeze. V14 changes only the UPDATE call site in
``current-task6-immutable-replacement-v1`` so the retained BEFORE trigger
passes an explicitly normalized NEW jsonb value to the unchanged Task-6
attachment helper. Every semantic vector and all 192 trigger coordinates
remain unchanged.
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
_V13_PATH = _SCRIPT_DIR / "materialize_full_strict_v13_preimage.py"
_V13_SOURCE_SHA512 = (
    "7425c5e7c048a277f6ae8f036a4c3d5f39e35740ed1e5f57168255d3be3c5e4a"
    "29ba9458d3bf63b135643d56ae39d92f2be86a24fe2aac28d9914647eb24d9be"
)
_V13_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v13-sql-authority-registry-v1"
_V14_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v14-sql-authority-registry-v1"
_TASK6_AUTHORITY_ID = "current-task6-immutable-replacement-v1"
_TASK6_AUTHORITY_ORDINAL = 27
_TASK6_TARGETS = (
    "methodology_extraction_tasks",
    "methodology_extraction_task_spans",
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
    "methodology_result_claim_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
)
_RAW_NEW_ARGUMENT = "pg_catalog.to_jsonb(NEW)"
_NORMALIZED_NEW_ARGUMENT = (
    "(pg_catalog.to_jsonb(NEW)-"
    "ARRAY['run_output_object_class','run_output_object_kind']::text[])"
    "||pg_catalog.jsonb_build_object("
    "'run_output_object_class','NATIVE_ROW',"
    "'run_output_object_kind',"
    "CASE TG_TABLE_NAME "
    "WHEN 'methodology_extraction_results' THEN 'METHODOLOGY_EXTRACTION_RESULT' "
    "WHEN 'methodology_result_factual_outputs' THEN 'METHODOLOGY_FACTUAL_OUTPUT' "
    "END)"
)
_RAW_HELPER_CALL = (
    "public.idis_task6_output_root_attach_is_valid_v1("
    "pg_catalog.to_jsonb(OLD),"
    f"{_RAW_NEW_ARGUMENT},"
    "NEW.tenant_id,NEW.deal_id,NEW.run_id)"
)
_NORMALIZED_HELPER_CALL = (
    "public.idis_task6_output_root_attach_is_valid_v1("
    "pg_catalog.to_jsonb(OLD),"
    f"{_NORMALIZED_NEW_ARGUMENT},"
    "NEW.tenant_id,NEW.deal_id,NEW.run_id)"
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v14 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V14_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V13 = _load_script(_V13_PATH, "_task7_v13_materializer_for_v14")
_build_acl_registry = _V13._build_acl_registry
_build_downgrade_authority = _V13._build_downgrade_authority
_column_acl_sql = _V13._column_acl_sql
_retained_0037_catalog_assertion_sql = _V13._retained_0037_catalog_assertion_sql
_retained_0037_catalog_projection = _V13._retained_0037_catalog_projection
_retained_0037_trigger_catalog_projection = _V13._retained_0037_trigger_catalog_projection


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V13._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V13._render(artifact))


def _reviewed_acl_design_tuple() -> list[Any]:
    return list(_V13._reviewed_acl_design_tuple())


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V13._changed_paths(old, new, path))


def _task6_row(artifact: dict[str, Any]) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    matches = [row for row in rows if row.get("authority_id") == _TASK6_AUTHORITY_ID]
    if (
        len(matches) != 1
        or int(matches[0].get("authority_ordinal", -1)) != _TASK6_AUTHORITY_ORDINAL
        or tuple(target[0] for target in matches[0].get("target_tuple", ())) != _TASK6_TARGETS
    ):
        _error("TASK7_V14_TASK6_AUTHORITY_SHAPE_INVALID")
    return matches[0]


def _transform_task6_body(artifact: dict[str, Any], *, reverse: bool) -> None:
    row = _task6_row(artifact)
    statements = list(_V13._V12._decode_statements(row))
    if len(statements) != 1:
        _error("TASK7_V14_TASK6_AUTHORITY_STATEMENT_CENSUS_INVALID")
    body = statements[0]
    old = _NORMALIZED_HELPER_CALL if reverse else _RAW_HELPER_CALL
    new = _RAW_HELPER_CALL if reverse else _NORMALIZED_HELPER_CALL
    if body.count(old) != 1 or body.count(new) != 0:
        _error("TASK7_V14_TASK6_CALL_SITE_BASE_INVALID")
    statements[0] = body.replace(old, new, 1)
    row["body_sql_utf8_hex_tuple"] = list(_V13._V12._encode_statements(statements))
    _V13._V12._rehash_authority_row(row)


def _rebuild_acl_registry(
    artifact: dict[str, Any],
    *,
    sql_registry_hash_domain: str,
) -> None:
    _V13._rebuild_acl_registry(
        artifact,
        sql_registry_hash_domain=sql_registry_hash_domain,
    )


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    old = "full-strict-v14" if reverse else "full-strict-v13"
    new = "full-strict-v13" if reverse else "full-strict-v14"
    expected = [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        _error("TASK7_V14_REVIEWED_DESIGN_BASE_INVALID")
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
    old_ordinal = "14" if reverse else "13"
    new_ordinal = "13" if reverse else "14"
    old_version = "full-strict-v14" if reverse else "full-strict-v13"
    new_version = "full-strict-v13" if reverse else "full-strict-v14"
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V14_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def project_v14_to_v13(v14: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-pinned v13 candidate from v14."""

    projected = copy.deepcopy(v14)
    _transform_task6_body(projected, reverse=True)
    _rebuild_acl_registry(
        projected,
        sql_registry_hash_domain=_V13_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V13_SOURCE_SHA512:
        _error("TASK7_V14_EXACT_V13_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v13: dict[str, Any], v14: dict[str, Any]) -> None:
    if tuple(v14) != tuple(v13):
        _error("TASK7_V14_SECTION_ORDER_DRIFT")
    for section in v13:
        if section in {"metadata", "digest_recipe_registry", "sql_authority_registry"}:
            continue
        if v14[section] != v13[section]:
            _error("TASK7_V14_UNAUTHORIZED_DRIFT", section)
    changed = _changed_paths(v13, v14)
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != {_TASK6_AUTHORITY_ORDINAL}:
        _error("TASK7_V14_SQL_AUTHORITY_DIFF_INVALID", repr(sorted(changed_rows)))
    if (
        v14["sql_authority_registry"]["guard_function_acl_registry"]
        != (v13["sql_authority_registry"]["guard_function_acl_registry"])
    ):
        _error("TASK7_V14_GUARD_ACL_DRIFT")
    if project_v14_to_v13(v14) != v13:
        _error("TASK7_V14_V13_PROJECTION_DRIFT")


def _load_v13(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V13_SOURCE_SHA512:
        _error("TASK7_V14_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V14_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw)
    if not isinstance(value, dict):
        _error("TASK7_V14_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v13-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v13"
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V14_BASE_METADATA_INVALID")
    return value


def build_v14(v13: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v14 candidate."""

    artifact = copy.deepcopy(v13)
    _transform_task6_body(artifact, reverse=False)
    _rebuild_acl_registry(
        artifact,
        sql_registry_hash_domain=_V14_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v13, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V14_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V14_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v13_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v14(_load_v13(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V14_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
