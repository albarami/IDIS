"""Materialize the bounded, unfrozen full-strict-v15 reviewed candidate.

V14 never reached append-freeze. V15 changes only two stale, hash-bound
pipeline-contract version literals in the current receipt-equality and Task-5
coverage guards. The complete ACL registry and every semantic vector remain
byte-identical.
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
_V14_PATH = _SCRIPT_DIR / "materialize_full_strict_v14_preimage.py"
_V14_SOURCE_SHA512 = (
    "0b6c26711b30bb19dea9b068d0c207d66a1a979d422c3cfbba095f8bbe50ece6"
    "b3f0109193e666083a5e6b01ad94ebfe91767016d0f8b15b5487cd399ff85abc"
)
_V14_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v14-sql-authority-registry-v1"
_V15_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v15-sql-authority-registry-v1"
_VERSION_LITERAL_V14 = "full-strict-v12"
_VERSION_LITERAL_V15 = "full-strict-v15"
_VERSION_AUTHORITIES = (
    (19, "current-receipt-equality-v1", 2),
    (26, "current-task5-coverage-replacement-v1", 0),
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v15 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V15_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V14 = _load_script(_V14_PATH, "_task7_v14_materializer_for_v15")
_build_acl_registry = _V14._build_acl_registry
_build_downgrade_authority = _V14._build_downgrade_authority
_column_acl_sql = _V14._column_acl_sql
_retained_0037_catalog_assertion_sql = _V14._retained_0037_catalog_assertion_sql
_retained_0037_catalog_projection = _V14._retained_0037_catalog_projection
_retained_0037_trigger_catalog_projection = _V14._retained_0037_trigger_catalog_projection


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V14._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V14._render(artifact))


def _reviewed_acl_design_tuple() -> list[Any]:
    return list(_V14._reviewed_acl_design_tuple())


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V14._changed_paths(old, new, path))


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V15_VERSION_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if row.get("authority_ordinal") != authority_ordinal or row.get("authority_id") != authority_id:
        _error("TASK7_V15_VERSION_AUTHORITY_SHAPE_INVALID", authority_id)
    return row


def _transform_version_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    old = _VERSION_LITERAL_V15 if reverse else _VERSION_LITERAL_V14
    new = _VERSION_LITERAL_V14 if reverse else _VERSION_LITERAL_V15
    for authority_ordinal, authority_id, statement_ordinal in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(_V14._V13._V12._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V15_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        statement = statements[statement_ordinal]
        if statement.count(old) != 1 or statement.count(new) != 0:
            _error("TASK7_V15_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = statement.replace(old, new, 1)
        row["body_sql_utf8_hex_tuple"] = list(_V14._V13._V12._encode_statements(statements))
        _V14._V13._V12._rehash_authority_row(row)


def _rehash_sql_registry(
    artifact: dict[str, Any],
    *,
    hash_domain: str,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V15_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            hash_domain,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    old = "full-strict-v15" if reverse else "full-strict-v14"
    new = "full-strict-v14" if reverse else "full-strict-v15"
    expected = [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        _error("TASK7_V15_REVIEWED_DESIGN_BASE_INVALID")
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
    old_ordinal = "15" if reverse else "14"
    new_ordinal = "14" if reverse else "15"
    old_version = "full-strict-v15" if reverse else "full-strict-v14"
    new_version = "full-strict-v14" if reverse else "full-strict-v15"
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V15_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def project_v15_to_v14(v15: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-pinned v14 candidate from v15."""

    projected = copy.deepcopy(v15)
    _transform_version_authorities(projected, reverse=True)
    _rehash_sql_registry(
        projected,
        hash_domain=_V14_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    if hashlib.sha512(_render(projected)).hexdigest() != _V14_SOURCE_SHA512:
        _error("TASK7_V15_EXACT_V14_PROJECTION_HASH_MISMATCH")
    return projected


def _validate_exact_diff(v14: dict[str, Any], v15: dict[str, Any]) -> None:
    if tuple(v15) != tuple(v14):
        _error("TASK7_V15_SECTION_ORDER_DRIFT")
    for section in v14:
        if section in {"metadata", "digest_recipe_registry", "sql_authority_registry"}:
            continue
        if v15[section] != v14[section]:
            _error("TASK7_V15_UNAUTHORIZED_DRIFT", section)
    changed = _changed_paths(v14, v15)
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != {19, 26}:
        _error("TASK7_V15_SQL_AUTHORITY_DIFF_INVALID", repr(sorted(changed_rows)))
    if (
        v15["sql_authority_registry"]["guard_function_acl_registry"]
        != (v14["sql_authority_registry"]["guard_function_acl_registry"])
    ):
        _error("TASK7_V15_GUARD_ACL_DRIFT")
    if project_v15_to_v14(v15) != v14:
        _error("TASK7_V15_V14_PROJECTION_DRIFT")


def _load_v14(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V14_SOURCE_SHA512:
        _error("TASK7_V15_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V15_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw)
    if not isinstance(value, dict):
        _error("TASK7_V15_BASE_ARTIFACT_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v14-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v14"
        or metadata.get("append_frozen") is not False
    ):
        _error("TASK7_V15_BASE_METADATA_INVALID")
    return value


def build_v15(v14: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v15 candidate."""

    artifact = copy.deepcopy(v14)
    _transform_version_authorities(artifact, reverse=False)
    _rehash_sql_registry(
        artifact,
        hash_domain=_V15_SQL_REGISTRY_HASH_DOMAIN,
    )
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v14, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V15_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V15_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v14_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rendered = _render(build_v15(_load_v14(args.base_artifact)))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V15_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
