"""Materialize the bounded, unfrozen full-strict-v16 reviewed candidate.

V15 never reached append-freeze. V16 updates the two governed runtime
contract-version literals and, unlike v15, deterministically rebuilds the
derived guard-function ACL body provenance from the corrected SQL bodies.
Every semantic ACL grant and every semantic vector remains byte-identical.
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
_REPO_ROOT = _SCRIPT_DIR.parent
_V15_PATH = _SCRIPT_DIR / "materialize_full_strict_v15_preimage.py"
_V15_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v15_reviewed_preimage_v1.json"
_V15_SOURCE_SHA512 = (
    "3712f694542600901b241b2ada905eeb5a9494d27ed9d11d4ae71d9e7dae52cd"
    "88cd2df14aa2984357a60d3ed490faf605aa4f484db327ca1fd7072eb4268a5e"
)
_V15_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v15-sql-authority-registry-v1"
_V16_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v16-sql-authority-registry-v1"
_VERSION_LITERAL_V15 = "full-strict-v15"
_VERSION_LITERAL_V16 = "full-strict-v16"
_VERSION_AUTHORITIES = (
    (19, "current-receipt-equality-v1", 2),
    (26, "current-task5-coverage-replacement-v1", 0),
)
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
    """Stable fail-closed v16 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _error("TASK7_V16_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V15 = _load_script(_V15_PATH, "_task7_v15_materializer_for_v16")
_build_acl_registry = _V15._build_acl_registry
_build_downgrade_authority = _V15._build_downgrade_authority
_column_acl_sql = _V15._column_acl_sql
_retained_0037_catalog_assertion_sql = _V15._retained_0037_catalog_assertion_sql
_retained_0037_catalog_projection = _V15._retained_0037_catalog_projection
_retained_0037_trigger_catalog_projection = _V15._retained_0037_trigger_catalog_projection


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V15._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V15._render(artifact))


def _reviewed_acl_design_tuple() -> list[Any]:
    return list(_V15._reviewed_acl_design_tuple())


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V15._changed_paths(old, new, path))


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V16_VERSION_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if row.get("authority_ordinal") != authority_ordinal or row.get("authority_id") != authority_id:
        _error("TASK7_V16_VERSION_AUTHORITY_SHAPE_INVALID", authority_id)
    return row


def _transform_version_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    old = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V15
    new = _VERSION_LITERAL_V15 if reverse else _VERSION_LITERAL_V16
    helpers = _V15._V14._V13._V12
    for authority_ordinal, authority_id, statement_ordinal in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(helpers._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V16_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        statement = statements[statement_ordinal]
        if statement.count(old) != 1 or statement.count(new) != 0:
            _error("TASK7_V16_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = statement.replace(old, new, 1)
        row["body_sql_utf8_hex_tuple"] = list(helpers._encode_statements(statements))
        helpers._rehash_authority_row(row)


def _rehash_sql_registry(
    artifact: dict[str, Any],
    *,
    hash_domain: str,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V16_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            hash_domain,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _transform_reviewed_design(artifact: dict[str, Any], *, reverse: bool) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V16_REVIEWED_DESIGN_BASE_INVALID") from exc
    old = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V15
    new = _VERSION_LITERAL_V15 if reverse else _VERSION_LITERAL_V16
    expected = [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]
    if not isinstance(design, list) or design[:2] != expected:
        _error("TASK7_V16_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = f"{new}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new]
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = new
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any], *, reverse: bool) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "16" if reverse else "15"
    new_ordinal = "15" if reverse else "16"
    old_version = _VERSION_LITERAL_V16 if reverse else _VERSION_LITERAL_V15
    new_version = _VERSION_LITERAL_V15 if reverse else _VERSION_LITERAL_V16
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V16_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _load_pinned_v15() -> dict[str, Any]:
    raw = _V15_ARTIFACT.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _V15_SOURCE_SHA512:
        _error("TASK7_V16_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V16_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw)
    if not isinstance(value, dict):
        _error("TASK7_V16_BASE_ARTIFACT_INVALID")
    return value


def acl_semantic_projection(acl: dict[str, Any]) -> dict[str, Any]:
    """Remove only derived SQL-body hashes from the ACL authority."""

    projected = copy.deepcopy(acl)
    projected.pop("expected_sha256", None)
    for row in projected["function_rows"]:
        row.pop("expected_sha256", None)
        for direct in row["direct_read_column_tuple"]:
            evidence = direct[2]
            if evidence[0] == "SQL_BODY_SHA256":
                evidence[1] = "<DERIVED_SQL_BODY_SHA256>"
        for transitive in row["transitive_read_column_tuple"]:
            for evidence in transitive[2]:
                if evidence[0] == "SQL_BODY_SHA256":
                    evidence[1] = "<DERIVED_SQL_BODY_SHA256>"
    return projected


def acl_provenance_changed_paths(
    old_acl: dict[str, Any],
    new_acl: dict[str, Any],
) -> tuple[tuple[str | int, ...], ...]:
    """Return the exact leaf paths changed by ACL provenance regeneration."""

    return tuple(
        sorted(
            _changed_paths(old_acl, new_acl),
            key=lambda value: tuple(f"{type(part).__name__}:{part}" for part in value),
        )
    )


def _expected_acl_provenance_paths() -> frozenset[tuple[str | int, ...]]:
    paths: set[tuple[str | int, ...]] = {("expected_sha256",)}
    for row_ordinal, direct_count, transitive_ordinals in (
        (20, 8, range(8)),
        (29, 3, range(1, 4)),
    ):
        paths.add(("function_rows", row_ordinal, "expected_sha256"))
        for ordinal in range(direct_count):
            paths.add(
                (
                    "function_rows",
                    row_ordinal,
                    "direct_read_column_tuple",
                    ordinal,
                    2,
                    1,
                )
            )
        for ordinal in transitive_ordinals:
            paths.add(
                (
                    "function_rows",
                    row_ordinal,
                    "transitive_read_column_tuple",
                    ordinal,
                    2,
                    0,
                    1,
                )
            )
    return frozenset(paths)


def validate_exact_acl_provenance_delta(
    v15: dict[str, Any],
    v16: dict[str, Any],
) -> None:
    """Prove that ACL differences are exactly the derived body provenance."""

    old_acl = v15["sql_authority_registry"]["guard_function_acl_registry"]
    new_acl = v16["sql_authority_registry"]["guard_function_acl_registry"]
    try:
        derived_acl = _build_acl_registry(v16)
    except Exception as exc:
        raise MaterializationError("TASK7_V16_ACL_PROVENANCE_DERIVATION_FAILED") from exc
    if new_acl != derived_acl:
        _error("TASK7_V16_ACL_PROVENANCE_INVALID", "registry-not-derived")
    if acl_semantic_projection(new_acl) != acl_semantic_projection(old_acl):
        _error("TASK7_V16_ACL_SEMANTIC_DRIFT")
    changed = frozenset(acl_provenance_changed_paths(old_acl, new_acl))
    expected = _expected_acl_provenance_paths()
    if changed != expected:
        _error(
            "TASK7_V16_ACL_PROVENANCE_DIFF_INVALID",
            repr(sorted(map(repr, changed ^ expected))),
        )


def project_v16_to_v15(v16: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact reviewer-recorded v15 candidate from v16."""

    projected = copy.deepcopy(v16)
    append_frozen = projected["metadata"].get("append_frozen")
    _transform_version_authorities(projected, reverse=True)
    projected["sql_authority_registry"]["guard_function_acl_registry"] = copy.deepcopy(
        _load_pinned_v15()["sql_authority_registry"]["guard_function_acl_registry"]
    )
    _rehash_sql_registry(projected, hash_domain=_V15_SQL_REGISTRY_HASH_DOMAIN)
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    hash_projection = copy.deepcopy(projected)
    hash_projection["metadata"]["append_frozen"] = False
    if hashlib.sha512(_render(hash_projection)).hexdigest() != _V15_SOURCE_SHA512:
        _error("TASK7_V16_EXACT_V15_PROJECTION_HASH_MISMATCH")
    projected["metadata"]["append_frozen"] = append_frozen
    return projected


def _validate_exact_diff(v15: dict[str, Any], v16: dict[str, Any]) -> None:
    if tuple(v16) != tuple(v15):
        _error("TASK7_V16_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v16[section] != v15[section]:
            _error("TASK7_V16_INVARIANT_DRIFT", section)
    changed_rows = {
        path[2]
        for path in _changed_paths(v15, v16)
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != {19, 26}:
        _error("TASK7_V16_SQL_AUTHORITY_DIFF_INVALID", repr(sorted(changed_rows)))
    validate_exact_acl_provenance_delta(v15, v16)
    if project_v16_to_v15(v16) != v15:
        _error("TASK7_V16_V15_PROJECTION_DRIFT")


def build_v16(v15: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v16 candidate."""

    artifact = copy.deepcopy(v15)
    _transform_version_authorities(artifact, reverse=False)
    artifact["sql_authority_registry"]["guard_function_acl_registry"] = _build_acl_registry(
        artifact
    )
    _rehash_sql_registry(artifact, hash_domain=_V16_SQL_REGISTRY_HASH_DOMAIN)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    _validate_exact_diff(v15, artifact)
    return artifact


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V16_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V16_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V15_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if hashlib.sha512(raw).hexdigest() != _V15_SOURCE_SHA512:
            _error("TASK7_V16_BASE_ARTIFACT_HASH_MISMATCH")
        v15 = json.loads(raw)
        if not isinstance(v15, dict):
            _error("TASK7_V16_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v16(v15))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V16_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
