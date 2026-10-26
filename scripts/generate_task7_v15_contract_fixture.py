"""Validate and derive the unfrozen full-strict-v15 contract candidate.

The reviewed artifact is the sole normative byte source. V15 binds every
hash-bound SQL contract-version literal to one of two governed coordinates and
to the artifact metadata version. Fixture publication remains unavailable
until a separate signed PostgreSQL census freezes the candidate.
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


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V15_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V14 = _load_script(
    _SCRIPT_DIR / "generate_task7_v14_contract_fixture.py",
    "_task7_v14_fixture_helpers_for_v15",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v15_preimage.py",
    "_task7_v15_materializer_helpers",
)

ContractArtifactError = _V14.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V14._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V14._COMPONENT_ORDER)
_V14_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v14-sql-authority-registry-v1"
_V15_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v15-sql-authority-registry-v1"
_VERSION_LITERAL_RE = re.compile(r"^full-strict-v[0-9]+$")
_GOVERNED_VERSION_COORDINATES = (
    (19, "current-receipt-equality-v1", 2, 0),
    (26, "current-task5-coverage-replacement-v1", 0, 0),
)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V14._canonical_bytes(value))


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
    return cast(dict[str, Any], _V14._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V14.invariant_vector(artifact))


def _decoded_statements(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_MATERIALIZER._V14._V13._V12._decode_statements(row))


def contract_version_reference_authority(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Enumerate exact hash-bound SQL version literals with stable coordinates."""

    references: list[dict[str, Any]] = []
    for authority_ordinal, row in enumerate(artifact["sql_authority_registry"]["rows"]):
        if row.get("authority_ordinal") != authority_ordinal or not isinstance(
            row.get("authority_id"), str
        ):
            _error(
                "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                f"authority-shape:{authority_ordinal}",
            )
        for statement_ordinal, statement in enumerate(_decoded_statements(row)):
            tokens = _V14._V13._sql_tokens(statement)
            occurrence_ordinal = 0
            for kind, value in tokens:
                if kind != "STRING":
                    continue
                if "full-strict" in value and _VERSION_LITERAL_RE.fullmatch(value) is None:
                    _error(
                        "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                        f"dynamic-fragment:{authority_ordinal}:{statement_ordinal}",
                    )
                if _VERSION_LITERAL_RE.fullmatch(value) is None:
                    continue
                references.append(
                    {
                        "authority_ordinal": authority_ordinal,
                        "authority_id": row["authority_id"],
                        "statement_ordinal": statement_ordinal,
                        "occurrence_ordinal": occurrence_ordinal,
                        "pipeline_contract_version": value,
                    }
                )
                occurrence_ordinal += 1
    return tuple(references)


def _pipeline_contract_version_comparisons(
    artifact: dict[str, Any],
) -> tuple[tuple[int, int, str], ...]:
    comparisons: list[tuple[int, int, str]] = []
    for (
        authority_ordinal,
        authority_id,
        statement_ordinal,
        _occurrence,
    ) in _GOVERNED_VERSION_COORDINATES:
        row = artifact["sql_authority_registry"]["rows"][authority_ordinal]
        if row.get("authority_id") != authority_id:
            _error(
                "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                f"authority-binding:{authority_ordinal}",
            )
        statements = _decoded_statements(row)
        if statement_ordinal >= len(statements):
            _error(
                "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                f"statement-binding:{authority_ordinal}:{statement_ordinal}",
            )
        tokens = _V14._V13._sql_tokens(statements[statement_ordinal])
        comparison_indices = tuple(
            token_ordinal
            for token_ordinal, token in enumerate(tokens)
            if token == ("IDENT", "pipeline_contract_version")
            and token_ordinal + 1 < len(tokens)
            and tokens[token_ordinal + 1] == ("SYMBOL", "=")
        )
        if len(comparison_indices) != 1:
            _error(
                "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                f"comparison-cardinality:{authority_ordinal}:{statement_ordinal}",
            )
        token_ordinal = comparison_indices[0]
        lhs_qualifier = tokens[token_ordinal - 2 : token_ordinal] if token_ordinal >= 2 else ()
        rhs = tokens[token_ordinal + 2] if token_ordinal + 2 < len(tokens) else None
        if (
            lhs_qualifier != (("IDENT", "r"), ("SYMBOL", "."))
            or rhs is None
            or rhs[0] != "STRING"
            or _VERSION_LITERAL_RE.fullmatch(rhs[1]) is None
        ):
            _error(
                "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
                f"dynamic-or-unbound:{authority_ordinal}:{statement_ordinal}:{token_ordinal}",
            )
        comparisons.append((authority_ordinal, statement_ordinal, rhs[1]))
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
            "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
            "metadata-version",
        )
    references = contract_version_reference_authority(artifact)
    coordinates = tuple(
        (
            row["authority_ordinal"],
            row["authority_id"],
            row["statement_ordinal"],
            row["occurrence_ordinal"],
        )
        for row in references
    )
    if coordinates != _GOVERNED_VERSION_COORDINATES:
        _error(
            "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
            f"coordinate-set:{coordinates!r}",
        )
    if any(row["pipeline_contract_version"] != metadata_version for row in references):
        _error(
            "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
            "stale-version",
        )
    comparisons = _pipeline_contract_version_comparisons(artifact)
    expected_comparisons = tuple(
        (authority_ordinal, statement_ordinal, metadata_version)
        for authority_ordinal, _authority_id, statement_ordinal, _occurrence in (
            _GOVERNED_VERSION_COORDINATES
        )
    )
    if comparisons != expected_comparisons:
        _error(
            "TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
            f"comparison-set:{comparisons!r}",
        )


_invalid_bare_new_contexts = _V14._invalid_bare_new_contexts


def _project_for_v14_class_guards(artifact: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(artifact)
    _MATERIALIZER._transform_version_authorities(projected, reverse=True)
    _MATERIALIZER._rehash_sql_registry(
        projected,
        hash_domain=_V14_SQL_REGISTRY_HASH_DOMAIN,
    )
    _MATERIALIZER._transform_reviewed_design(projected, reverse=True)
    _MATERIALIZER._transform_digest_recipe(projected, reverse=True)
    return projected


def before_generated_column_coverage(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    try:
        return cast(
            tuple[dict[str, Any], ...],
            _V14.before_generated_column_coverage(_project_for_v14_class_guards(artifact)),
        )
    except ContractArtifactError as exc:
        raise ContractArtifactError(
            "TASK7_V15_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            str(exc),
        ) from exc


def validate_before_generated_column_access(artifact: dict[str, Any]) -> None:
    before_generated_column_coverage(artifact)


def before_generated_column_coverage_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v15-before-generated-column-coverage-v1",
            list(before_generated_column_coverage(artifact)),
        ]
    )


def guard_target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return cast(tuple[tuple[Any, ...], ...], _V14.guard_target_event_authority(artifact))


def _validate_hash_bound_acl(artifact: dict[str, Any]) -> None:
    sql = artifact["sql_authority_registry"]
    acl = sql["guard_function_acl_registry"]
    _expect(
        _h([_V15_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]),
        sql.get("expected_sha256"),
        "v15 SQL authority registry",
    )
    v14_domain_projection = copy.deepcopy(artifact)
    v14_domain_projection["sql_authority_registry"]["expected_sha256"] = _h(
        [_V14_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]
    )
    _V14._validate_hash_bound_acl(v14_domain_projection)


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
        metadata.get("artifact_schema") != "full-strict-v15-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v15"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v15 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V15_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v15 bytes",
        )

    _validate_hash_bound_acl(artifact)
    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v15 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v15 reviewed design",
    )
    if (
        not isinstance(reviewed_value, list)
        or reviewed_value[:2]
        != [
            "full-strict-v15-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v15"],
        ]
        or reviewed_value[-1] != _MATERIALIZER._reviewed_acl_design_tuple()
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v15 reviewed design differs")

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v15","full-strict-v15",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if recipes[5] != expected_recipe or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v15 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v14 = _MATERIALIZER.project_v15_to_v14(candidate)
        _MATERIALIZER._validate_exact_diff(v14, candidate)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V15_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v14 successor",
        ) from exc
    _V14._validate_shape(_render_artifact(v14), v14)
    validate_contract_version_reference_authority(artifact)
    validate_before_generated_column_access(artifact)
    if invariant_vector(artifact) != _V14.invariant_vector(v14):
        _error(
            "TASK7_V15_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V15_UNFROZEN_CANDIDATE_REQUIRED",
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
    """Derive every v15 component solely from the supplied artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v15-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    v14 = _MATERIALIZER.project_v15_to_v14(artifact)
    v13 = _MATERIALIZER._V14.project_v14_to_v13(v14)
    v12 = _MATERIALIZER._V14._V13.project_v13_to_v12(v13)
    v11 = _V14._V13._V12._MATERIALIZER.project_v12_to_v11(v12)
    components = cast(
        dict[str, str],
        _V14._V13._V12._V11._V10._V9._V7._V6._V5._derive_components(
            v11,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v15",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v15-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v15",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V15_CONTRACT_HASH": pipeline_hash,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v15_reviewed_preimage_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.artifact.read_bytes()
        artifact = _decode_artifact(raw)
        _validate_shape(raw, artifact)
        if artifact["metadata"]["append_frozen"] is False:
            print("TASK7_V15_FIXTURE_PUBLICATION_REFUSED_UNFROZEN", file=sys.stderr)
            return 2
    except (ContractArtifactError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("TASK7_V15_FIXTURE_PUBLICATION_REQUIRES_REVIEWED_FREEZE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
