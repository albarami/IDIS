"""Derive the frozen full-strict-v6 fixture from its sole byte authority."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_v5_helpers() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v5_contract_fixture.py")
    spec = importlib.util.spec_from_file_location("_task7_v5_fixture_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V6_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V5 = _load_v5_helpers()
ContractArtifactError = _V5.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V5._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V5._COMPONENT_ORDER)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V5._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        raise ContractArtifactError(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _accepted_consumer(route: list[Any]) -> list[Any]:
    if len(route) != 12:
        raise ContractArtifactError(
            "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
            "UsageRouteV2 row does not contain twelve fields",
        )
    return [*route[:5], *route[6:]]


def _validate_usage_projection(artifact: dict[str, Any]) -> None:
    routes = artifact["usage_route_registry"]["rows"]
    extractors = artifact["usage_extractor_registry"]["rows"]
    if len(routes) != 1_756 or [route[0] for route in routes] != list(range(1_756)):
        raise ContractArtifactError(
            "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
            "usage-route ordinals are not the closed 0..1755 sequence",
        )
    if len(extractors) != 19:
        raise ContractArtifactError(
            "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
            "usage-extractor registry does not contain nineteen rows",
        )

    seen: set[str] = set()
    registry_rows: list[list[Any]] = []
    covered_ordinals: list[int] = []
    for extractor in extractors:
        usage_kind = extractor["usage_kind"]
        if not isinstance(usage_kind, str) or usage_kind in seen:
            raise ContractArtifactError(
                "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
                "usage-extractor kinds are not unique strings",
            )
        seen.add(usage_kind)
        same_kind_routes = [route for route in routes if route[5] == usage_kind]
        accepted = [_accepted_consumer(route) for route in same_kind_routes]
        covered_ordinals.extend(route[0] for route in same_kind_routes)
        if extractor["accepted_consumers"] != accepted:
            raise ContractArtifactError(
                "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
                f"{usage_kind} accepted consumers differ from same-ordinal routes",
            )
        accepted_hash = _h(["accepted-consumer-set-v2", usage_kind, accepted])
        _expect(
            accepted_hash,
            extractor["expected_accepted_consumer_sha256"],
            f"{usage_kind} accepted-consumer projection",
        )
        extractor_hash = _h(
            [
                "usage-extractor-definition-v2",
                usage_kind,
                extractor["extractor_id"],
                extractor["extractor_version"],
                extractor["program"],
                accepted,
            ]
        )
        _expect(
            extractor_hash,
            extractor["expected_sha256"],
            f"{usage_kind} extractor",
        )
        registry_rows.append(
            [
                usage_kind,
                extractor["extractor_id"],
                extractor["extractor_version"],
                extractor_hash,
                accepted,
            ]
        )
    if sorted(covered_ordinals) != list(range(1_756)):
        raise ContractArtifactError(
            "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH",
            "usage extractors do not partition all literal routes exactly once",
        )
    registry_hash = _h(
        [
            "task7-usage-extractor-registry-v2",
            registry_rows,
            artifact["usage_route_registry"]["expected_sha256"],
        ]
    )
    _expect(
        registry_hash,
        artifact["usage_extractor_registry"]["expected_sha256"],
        "usage-extractor registry",
    )
    _expect(
        registry_hash,
        artifact["activation_assertions"]["expected_usage_extractor_sha256"],
        "activation usage-extractor registry",
    )


def _validate_shape(raw: bytes, artifact: dict[str, Any]) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "top-level section order mismatch",
        )
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v6-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v6"
        or metadata.get("append_frozen") is not True
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v6 metadata mismatch",
        )
    try:
        reviewed_design = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "reviewed-design bytes are invalid",
        ) from exc
    _expect(
        _sha256_bytes(reviewed_design),
        metadata["reviewed_design_sha256"],
        "reviewed_design_sha256",
    )

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v6","full-strict-v6",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or recipes[-1] != [
        "baseline_parent_authority_registry_hash",
        "lowercase_hex(SHA256(CJ(ORDERED[baseline_parent_authority_rows])))",
    ]:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v6 digest recipe mismatch",
        )
    if bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "digest reviewed bytes mismatch",
        )

    counts = {
        "semantic_step_registry": 28,
        "edge_rule_registry": 48,
        "usage_route_registry": 1_756,
        "usage_extractor_registry": 19,
        "rights_index_policy_registry": 18,
        "current_ddl_registry": 34,
        "future_native_authority_registry": 18,
        "future_reference_authority_registry": 15,
        "future_child_authority_registry": 5,
        "sql_authority_registry": 17,
        "erasure_execution_registry": 1,
    }
    for section, expected in counts.items():
        if len(artifact[section]["rows"]) != expected:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"{section} count mismatch",
            )
    if (
        len(artifact["erasure_baseline"]["rows"]) != 59
        or len(artifact["erasure_materialized"]["rows"]) != 66
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "erasure census mismatch",
        )
    _V5._validate_baseline_registry(artifact)
    _V5._validate_parent_authority_closure(artifact)
    _V5._V4.validate_sql_authority_registry(artifact)
    _V5._V4.validate_erasure_execution_registry(artifact)
    _V5._V4._validate_reviewed_row_bytes(artifact)
    _V5._V4._validate_scalar_tree(artifact)
    _validate_usage_projection(artifact)


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(raw: bytes, *, validate: bool = True) -> dict[str, Any]:
    """Derive v6 exclusively from append-frozen artifact bytes."""

    artifact = cast(dict[str, Any], _V5._V4._decode(raw))
    artifact_sha256 = _sha256_bytes(raw)
    section_hashes = _section_hashes(artifact)
    reviewed_preimage_artifact_hash = _h(
        [
            "full-strict-v6-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [
                [row["ordinal"], row["name"], row["sha256"]]
                for row in section_hashes
            ],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = cast(
        dict[str, str],
        _V5._derive_components(artifact, reviewed_preimage_artifact_hash),
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v6",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_preimage_artifact_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v6-contract-hash-fixture-v1",
        "artifact_sha256": artifact_sha256,
        "section_hashes": section_hashes,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V6_CONTRACT_HASH": pipeline_hash,
    }


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_exclusive(path: Path, rendered: str) -> None:
    if not path.parent.is_dir():
        raise ContractArtifactError(
            "TASK7_V6_FIXTURE_OUTPUT_PARENT_MISSING",
            str(path.parent),
        )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V6_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(path),
        ) from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = derive_contract_bytes(args.artifact.read_bytes(), validate=True)
        rendered = _render_fixture(result)
        if args.stdout:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
        else:
            if args.output is None:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    "fixture output path is absent",
                )
            _write_exclusive(args.output, rendered)
        return 0
    except (ContractArtifactError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
