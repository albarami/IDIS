"""Materialize the bounded, unfrozen full-strict-v18 reviewed candidate.

V17 remains byte-immutable, append-frozen, inactive, and historically
superseded. V18 corrects the one retained downgrade definition that omitted
PostgreSQL's explicit LANGUAGE clause, advances governed version literals,
and updates only dependent metadata and hashes. Semantic registries remain
byte-identical.
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
_V17_PATH = _SCRIPT_DIR / "materialize_full_strict_v17_preimage.py"
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_SOURCE_SHA512 = (
    "23435d561028ab7aa28a5a524c0cd5498f1818a7cfeeff722dc4c280a15961310"
    "5d6e1457cec7b46cb56fe63757175ed2c751b8b90e26c124552cf88e1ef5f3b"
)
_V17_ARTIFACT_SHA256 = "d6218f7a48f2170099b461ee152410852de9e0f29cfe78fb71627b968c1465e1"
_V17_ARTIFACT_SHA512 = (
    "08663ffe16e6e6515e0bebff161bb30a1dfc496573fda8cd4cfcb556b2acfeae"
    "5c611f7141b83e0264a5871844f11d85724dc289a7a5d8adab2a4bcf5b4a6d8a"
)
_V18_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v18-sql-authority-registry-v1"
_VERSION_LITERAL_V17 = "full-strict-v17"
_VERSION_LITERAL_V18 = "full-strict-v18"
_VERSION_AUTHORITIES = (
    (19, "current-receipt-equality-v1", 2, 3),
    (26, "current-task5-coverage-replacement-v1", 0, 1),
)
_STRICT_PREFLIGHT_SIGNATURE = "public.idis_prevent_strict_preflight_proof_mutation()"
_STRICT_PREFLIGHT_RETAINED_ORDINAL = 0
_STRICT_PREFLIGHT_ORDERED_ORDINAL = 172
_BAD_V17_STRICT_PREFLIGHT_BYTES = 4_261
_BAD_V17_STRICT_PREFLIGHT_SHA256 = (
    "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484"
)
_FRESH_0037_STRICT_PREFLIGHT_BYTES = 4_287
_FRESH_0037_STRICT_PREFLIGHT_SHA256 = (
    "c589089618af8d486acb41f046f1bd6969cff9279e6abfd4bdaa4b006752655e"
)
_STRICT_PREFLIGHT_PROSRC_BYTES = 4_147
_STRICT_PREFLIGHT_PROSRC_SHA256 = "b2a1ab9b247bdf45a7028584fc8adb722f4672990698e7d9780700cf9704498e"
_STRICT_PREFLIGHT_CATALOG_PREFIX = (
    "CREATE OR REPLACE FUNCTION "
    "public.idis_prevent_strict_preflight_proof_mutation()\n"
    " RETURNS trigger\n"
    " LANGUAGE plpgsql\n"
    "AS $function$"
)
_STRICT_PREFLIGHT_CATALOG_SUFFIX = "$function$\n"
_LANGUAGE_CLAUSE = re.compile(r"(?i)\bLANGUAGE\s+[a-z][a-z0-9_]*\b")
_ALLOWED_CHANGED_AUTHORITY_ORDINALS = {19, 26}
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
    """Stable fail-closed v18 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_pinned_script(path: Path, sha512: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(f"TASK7_V18_HELPER_MISSING:{path.name}") from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V18_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V18_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_V17 = _load_pinned_script(
    _V17_PATH,
    _V17_SOURCE_SHA512,
    "_task7_v17_materializer_for_v18",
)
_H = _V17._H


def _canonical_bytes(value: object) -> bytes:
    return bytes(_V17._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_V17._render(artifact))


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_V17._changed_paths(old, new, path))


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V18_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if (
        not isinstance(row, dict)
        or row.get("authority_ordinal") != authority_ordinal
        or row.get("authority_id") != authority_id
    ):
        _error("TASK7_V18_AUTHORITY_SHAPE_INVALID", authority_id)
    return cast(dict[str, Any], row)


def _load_pinned_v17() -> dict[str, Any]:
    try:
        raw = _V17_ARTIFACT.read_bytes()
    except OSError as exc:
        raise MaterializationError("TASK7_V18_BASE_ARTIFACT_MISSING") from exc
    if (
        _sha256(raw) != _V17_ARTIFACT_SHA256
        or hashlib.sha512(raw).hexdigest() != _V17_ARTIFACT_SHA512
    ):
        _error("TASK7_V18_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V18_BASE_ARTIFACT_NOT_CANONICAL")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V18_BASE_ARTIFACT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("metadata", {}).get("artifact_schema")
        != "full-strict-v17-reviewed-preimage-v1"
        or value.get("metadata", {}).get("pipeline_contract_version") != _VERSION_LITERAL_V17
        or value.get("metadata", {}).get("append_frozen") is not True
    ):
        _error("TASK7_V18_BASE_ARTIFACT_INVALID")
    return value


def _transform_version_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    old = _VERSION_LITERAL_V18 if reverse else _VERSION_LITERAL_V17
    new = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V18
    for authority_ordinal, authority_id, statement_ordinal, expected_count in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(_H._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V18_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        observed_count = sum(statement.count(old) for statement in statements)
        replacement_statement = statements[statement_ordinal]
        if (
            observed_count != expected_count
            or replacement_statement.count(old) != expected_count
            or any(
                statement.count(new)
                for ordinal, statement in enumerate(statements)
                if ordinal != statement_ordinal
            )
            or replacement_statement.count(new) != 0
        ):
            _error("TASK7_V18_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = replacement_statement.replace(old, new)
        row["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
        _H._rehash_authority_row(row)


def _fresh_0037_strict_preflight_definition(acl: dict[str, Any]) -> bytes:
    """Derive PostgreSQL's exact 0037 catalog definition from its frozen prosrc."""

    projection = acl.get("retained_0037_catalog_projection_tuple")
    if not isinstance(projection, list) or len(projection) != 8:
        _error("TASK7_V18_RETAINED_CATALOG_CENSUS_INVALID")
    row = projection[_STRICT_PREFLIGHT_RETAINED_ORDINAL]
    if (
        not isinstance(row, list)
        or len(row) != 9
        or row[0] != _STRICT_PREFLIGHT_SIGNATURE
        or not isinstance(row[2], str)
    ):
        _error("TASK7_V18_RETAINED_CATALOG_SHAPE_INVALID")
    try:
        prosrc = bytes.fromhex(row[2])
        prosrc.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK7_V18_RETAINED_PROSRC_INVALID") from exc
    if (
        len(prosrc) != _STRICT_PREFLIGHT_PROSRC_BYTES
        or _sha256(prosrc) != _STRICT_PREFLIGHT_PROSRC_SHA256
    ):
        _error("TASK7_V18_RETAINED_PROSRC_DRIFT")
    definition = (
        _STRICT_PREFLIGHT_CATALOG_PREFIX.encode("utf-8")
        + prosrc
        + _STRICT_PREFLIGHT_CATALOG_SUFFIX.encode("utf-8")
    )
    if (
        len(definition) != _FRESH_0037_STRICT_PREFLIGHT_BYTES
        or _sha256(definition) != _FRESH_0037_STRICT_PREFLIGHT_SHA256
    ):
        _error("TASK7_V18_FRESH_0037_DEFINITION_DRIFT")
    return definition


def _rehash_downgrade_authority(downgrade: dict[str, Any]) -> None:
    downgrade["expected_sha256"] = _h(
        [
            "task7-guard-function-downgrade-authority-v1",
            {key: value for key, value in downgrade.items() if key != "expected_sha256"},
        ]
    )


def _rehash_acl_registry(acl: dict[str, Any]) -> None:
    acl["expected_sha256"] = _h(
        [
            _H._ACL_REGISTRY_HASH_DOMAIN,
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
    )


def _correct_retained_restore(acl: dict[str, Any]) -> None:
    downgrade = acl.get("downgrade_authority")
    if not isinstance(downgrade, dict):
        _error("TASK7_V18_DOWNGRADE_AUTHORITY_INVALID")
    restores = downgrade.get("retained_function_restore_sql_utf8_hex_tuple")
    ordered = downgrade.get("ordered_sql_utf8_hex_tuple")
    if (
        not isinstance(restores, list)
        or len(restores) != 8
        or not isinstance(ordered, list)
        or len(ordered) != 190
    ):
        _error("TASK7_V18_DOWNGRADE_RESTORE_CENSUS_INVALID")
    bad_hex = restores[_STRICT_PREFLIGHT_RETAINED_ORDINAL]
    if not isinstance(bad_hex, str) or ordered[_STRICT_PREFLIGHT_ORDERED_ORDINAL] != bad_hex:
        _error("TASK7_V18_DOWNGRADE_RESTORE_COORDINATE_INVALID")
    try:
        bad = bytes.fromhex(bad_hex)
    except ValueError as exc:
        raise MaterializationError("TASK7_V18_DOWNGRADE_RESTORE_INVALID") from exc
    if (
        len(bad) != _BAD_V17_STRICT_PREFLIGHT_BYTES
        or _sha256(bad) != _BAD_V17_STRICT_PREFLIGHT_SHA256
        or _LANGUAGE_CLAUSE.search(bad.decode("utf-8"))
    ):
        _error("TASK7_V18_DOWNGRADE_RESTORE_BASE_INVALID")
    corrected_hex = _fresh_0037_strict_preflight_definition(acl).hex()
    restores[_STRICT_PREFLIGHT_RETAINED_ORDINAL] = corrected_hex
    ordered[_STRICT_PREFLIGHT_ORDERED_ORDINAL] = corrected_hex
    _rehash_downgrade_authority(downgrade)
    _rehash_acl_registry(acl)


def validate_retained_restore_language(artifact: dict[str, Any]) -> None:
    """Reject any retained restore whose function definition omits LANGUAGE."""

    try:
        downgrade = artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "downgrade_authority"
        ]
        signatures = downgrade["retained_function_signature_tuple"]
        restores = downgrade["retained_function_restore_sql_utf8_hex_tuple"]
    except (KeyError, TypeError) as exc:
        raise MaterializationError("TASK7_V18_RETAINED_FUNCTION_LANGUAGE_CENSUS_INVALID") from exc
    if (
        not isinstance(signatures, list)
        or not isinstance(restores, list)
        or len(signatures) != 8
        or len(restores) != 8
    ):
        _error("TASK7_V18_RETAINED_FUNCTION_LANGUAGE_CENSUS_INVALID")
    for signature, encoded in zip(signatures, restores, strict=True):
        if not isinstance(signature, str) or not isinstance(encoded, str):
            _error("TASK7_V18_RETAINED_FUNCTION_LANGUAGE_CENSUS_INVALID")
        try:
            definition = bytes.fromhex(encoded).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise MaterializationError(
                f"TASK7_V18_RETAINED_FUNCTION_LANGUAGE_REQUIRED:{signature}"
            ) from exc
        if (
            "CREATE" not in definition.upper()
            or "FUNCTION" not in definition.upper()
            or not _LANGUAGE_CLAUSE.search(definition)
        ):
            _error("TASK7_V18_RETAINED_FUNCTION_LANGUAGE_REQUIRED", signature)


def _transform_reviewed_design(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V18_REVIEWED_DESIGN_BASE_INVALID") from exc
    old = _VERSION_LITERAL_V18 if reverse else _VERSION_LITERAL_V17
    new = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V18
    if not isinstance(design, list) or design[:2] != [
        f"{old}-reviewed-design-literal-v1",
        ["pipeline_contract_version", old],
    ]:
        _error("TASK7_V18_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = f"{new}-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", new]
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = f"{new}-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = new
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    old_ordinal = "18" if reverse else "17"
    new_ordinal = "17" if reverse else "18"
    old_version = _VERSION_LITERAL_V18 if reverse else _VERSION_LITERAL_V17
    new_version = _VERSION_LITERAL_V17 if reverse else _VERSION_LITERAL_V18
    expected = [
        "pipeline_contract_hash",
        f'["pipeline-contract-v{old_ordinal}","{old_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V18_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        f'["pipeline-contract-v{new_ordinal}","{new_version}",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _rehash_sql_registry(
    artifact: dict[str, Any],
    *,
    hash_domain: str = _V18_SQL_REGISTRY_HASH_DOMAIN,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V18_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            hash_domain,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _build_v18_core(v17: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v17)
    artifact["metadata"]["append_frozen"] = False
    _transform_version_authorities(artifact, reverse=False)
    acl = cast(dict[str, Any], _V17._build_acl_registry(artifact))
    _correct_retained_restore(acl)
    artifact["sql_authority_registry"]["guard_function_acl_registry"] = acl
    _rehash_sql_registry(artifact)
    _transform_reviewed_design(artifact, reverse=False)
    _transform_digest_recipe(artifact, reverse=False)
    return artifact


def _validate_exact_diff(v17: dict[str, Any], v18: dict[str, Any]) -> None:
    if _render(v17) != _V17_ARTIFACT.read_bytes():
        _error("TASK7_V18_BASE_ARTIFACT_HASH_MISMATCH")
    if tuple(v18) != tuple(v17):
        _error("TASK7_V18_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v18[section] != v17[section]:
            _error("TASK7_V18_INVARIANT_DRIFT", section)
    changed = _changed_paths(v17, v18)
    if {path[0] for path in changed} != {
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
    }:
        _error("TASK7_V18_EXACT_DIFF_INVALID", "top-level")
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != _ALLOWED_CHANGED_AUTHORITY_ORDINALS:
        _error("TASK7_V18_EXACT_DIFF_INVALID", "authority rows")
    if (
        v18.get("metadata", {}).get("artifact_schema") != "full-strict-v18-reviewed-preimage-v1"
        or v18.get("metadata", {}).get("pipeline_contract_version") != _VERSION_LITERAL_V18
        or v18.get("metadata", {}).get("append_frozen") is not False
    ):
        _error("TASK7_V18_UNFROZEN_CANDIDATE_REQUIRED")
    expected = _build_v18_core(v17)
    if _render(expected) != _render(v18):
        _error("TASK7_V18_EXACT_DIFF_INVALID", "candidate bytes")
    validate_retained_restore_language(v18)


def build_v18(v17: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v18 correction candidate."""

    pinned = _load_pinned_v17()
    if _render(v17) != _render(pinned):
        _error("TASK7_V18_BASE_ARTIFACT_HASH_MISMATCH")
    artifact = _build_v18_core(v17)
    _validate_exact_diff(v17, artifact)
    return artifact


def project_v18_to_v17(v18: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact immutable append-frozen v17 bytes from v18."""

    v17 = _load_pinned_v17()
    _validate_exact_diff(v17, v18)
    projected = copy.deepcopy(v18)
    _transform_version_authorities(projected, reverse=True)
    projected["sql_authority_registry"]["guard_function_acl_registry"] = copy.deepcopy(
        v17["sql_authority_registry"]["guard_function_acl_registry"]
    )
    _rehash_sql_registry(
        projected,
        hash_domain="full-strict-v17-sql-authority-registry-v1",
    )
    _transform_reviewed_design(projected, reverse=True)
    _transform_digest_recipe(projected, reverse=True)
    projected["metadata"]["append_frozen"] = True
    if _render(projected) != _V17_ARTIFACT.read_bytes():
        _error("TASK7_V18_EXACT_V17_PROJECTION_HASH_MISMATCH")
    return projected


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V18_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V18_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V17_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if (
            _sha256(raw) != _V17_ARTIFACT_SHA256
            or hashlib.sha512(raw).hexdigest() != _V17_ARTIFACT_SHA512
        ):
            _error("TASK7_V18_BASE_ARTIFACT_HASH_MISMATCH")
        v17 = json.loads(raw)
        if not isinstance(v17, dict):
            _error("TASK7_V18_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v18(v17))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V18_UNFROZEN_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaterializationError",
    "build_v18",
    "main",
    "project_v18_to_v17",
    "validate_retained_restore_language",
]
