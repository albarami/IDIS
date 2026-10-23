"""One-shot materializer for the reviewed full-strict-v6 usage closure.

The append-frozen v5 artifact is the sole input. All nineteen accepted-consumer
sets are rederived from the same-ordinal UsageRouteV2 rows by dropping only the
usage-kind field. The script creates v6 exclusively and never mutates v5.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_V5_ARTIFACT_SHA512 = (
    "94cd87983dc0731bb5b8036dc756063e1c2cfdd58127c6102f09497b452324b3e"
    "d78e319af72a3c97b74f3dbdba88ab0cf705e626ecb6c88d0df9795ce5b6236"
)
_V5_SECTION_ORDER = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
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
    "sql_authority_registry",
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
    "erasure_baseline",
    "erasure_materialized",
)
_EXPECTED_ACCEPTED_SHA256 = "8555cadbd073a939f11c7a5992777eb3446b99b5cafca6105ea691cd272ac964"
_EXPECTED_ENRICHMENT_EXTRACTOR_SHA256 = (
    "cf14c89ea95ec619d1565f7127db7e829baa091fd74d69b0ed3a3c9e0dc75c51"
)
_EXPECTED_EXTRACTOR_REGISTRY_SHA256 = (
    "e6542bb692bb4819712bc95bfdd23c2f66f7fd21fa15e0b7881057ff81eeeccc"
)
_EXPECTED_CORRECTED_ORDINALS = (
    437,
    438,
    452,
    453,
    467,
    468,
    482,
    483,
    497,
    498,
    512,
    513,
    527,
    528,
    542,
    543,
    557,
    558,
    572,
    573,
    587,
    588,
    602,
    603,
    617,
    618,
    632,
    633,
    646,
    647,
    661,
    662,
    676,
    677,
    690,
    691,
    705,
    706,
    720,
    721,
    734,
    735,
    749,
    750,
    764,
    765,
    778,
    779,
    792,
    793,
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v6 materialization error."""


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


def _load_v5(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if _sha512(raw) != _V5_ARTIFACT_SHA512:
        raise MaterializationError("TASK7_V6_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V6_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or tuple(value) != _V5_SECTION_ORDER:
        raise MaterializationError("TASK7_V6_BASE_SECTION_ORDER_MISMATCH")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v5-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v5"
        or metadata.get("append_frozen") is not True
    ):
        raise MaterializationError("TASK7_V6_BASE_METADATA_INVALID")
    return raw, value


def _accepted_consumer(route: list[Any]) -> list[Any]:
    if len(route) != 12:
        raise MaterializationError("TASK7_V6_USAGE_ROUTE_SHAPE_INVALID")
    return [*route[:5], *route[6:]]


def _derive_usage_extractors(artifact: dict[str, Any]) -> None:
    routes = artifact["usage_route_registry"]["rows"]
    extractors = artifact["usage_extractor_registry"]["rows"]
    if len(routes) != 1_756 or [route[0] for route in routes] != list(range(1_756)):
        raise MaterializationError("TASK7_V6_USAGE_ROUTE_ORDINALS_INVALID")
    if len(extractors) != 19:
        raise MaterializationError("TASK7_V6_USAGE_EXTRACTOR_COUNT_INVALID")

    changed: list[tuple[list[Any], list[Any]]] = []
    registry_rows: list[list[Any]] = []
    seen_kinds: set[str] = set()
    for extractor in extractors:
        usage_kind = extractor["usage_kind"]
        if not isinstance(usage_kind, str) or usage_kind in seen_kinds:
            raise MaterializationError("TASK7_V6_USAGE_KIND_INVALID")
        seen_kinds.add(usage_kind)
        accepted = [_accepted_consumer(route) for route in routes if route[5] == usage_kind]
        previous = extractor["accepted_consumers"]
        if len(previous) != len(accepted):
            raise MaterializationError(f"TASK7_V6_ACCEPTED_CARDINALITY_DRIFT:{usage_kind}")
        changed.extend(
            (left, right) for left, right in zip(previous, accepted, strict=True) if left != right
        )
        extractor["accepted_consumers"] = accepted
        extractor["expected_accepted_consumer_sha256"] = _h(
            ["accepted-consumer-set-v2", usage_kind, accepted]
        )
        extractor["expected_sha256"] = _h(
            [
                "usage-extractor-definition-v2",
                usage_kind,
                extractor["extractor_id"],
                extractor["extractor_version"],
                extractor["program"],
                accepted,
            ]
        )
        registry_rows.append(
            [
                usage_kind,
                extractor["extractor_id"],
                extractor["extractor_version"],
                extractor["expected_sha256"],
                accepted,
            ]
        )

    if tuple(left[0] for left, _right in changed) != _EXPECTED_CORRECTED_ORDINALS:
        raise MaterializationError("TASK7_V6_CORRECTED_ORDINAL_SET_INVALID")
    for left, right in changed:
        if not (
            left[0] == right[0]
            and left[1] == "E029_PROVIDER_FACT_SEMANTIC"
            and right[1] == "E048_PROVIDER_FACT_EXTERNAL_CONTEXT"
            and right[2] == left[2] + ";external_context_index_allowed=true"
            and right[3:] == left[3:]
        ):
            raise MaterializationError("TASK7_V6_CORRECTION_SHAPE_INVALID")
    enrichment = next(row for row in extractors if row["usage_kind"] == "ENRICHMENT_INPUT")
    if (
        enrichment["expected_accepted_consumer_sha256"] != _EXPECTED_ACCEPTED_SHA256
        or enrichment["expected_sha256"] != _EXPECTED_ENRICHMENT_EXTRACTOR_SHA256
    ):
        raise MaterializationError("TASK7_V6_ENRICHMENT_HASH_VECTOR_INVALID")
    registry_hash = _h(
        [
            "task7-usage-extractor-registry-v2",
            registry_rows,
            artifact["usage_route_registry"]["expected_sha256"],
        ]
    )
    if registry_hash != _EXPECTED_EXTRACTOR_REGISTRY_SHA256:
        raise MaterializationError("TASK7_V6_EXTRACTOR_REGISTRY_HASH_INVALID")
    artifact["usage_extractor_registry"]["expected_sha256"] = registry_hash
    artifact["activation_assertions"]["expected_usage_extractor_sha256"] = registry_hash


def _transform_metadata(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v5-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v5"],
    ]:
        raise MaterializationError("TASK7_V6_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = "full-strict-v6-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", "full-strict-v6"]
    design.append(
        [
            "usage_extractor_route_projection_closure",
            [
                ["projection", "same_ordinal_route_drop_usage_kind_only"],
                ["usage_extractor_count", 19],
                ["usage_route_count", 1756],
                ["corrected_enrichment_tuple_count", 50],
            ],
        ]
    )
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v6-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v6"
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v5","full-strict-v5",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]:
        raise MaterializationError("TASK7_V6_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        '["pipeline-contract-v6","full-strict-v6",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


def _validate_exact_diff(v5: dict[str, Any], v6: dict[str, Any]) -> None:
    if tuple(v6) != tuple(v5):
        raise MaterializationError("TASK7_V6_SECTION_ORDER_DRIFT")
    for section in _UNCHANGED_SECTIONS:
        if v6[section] != v5[section]:
            raise MaterializationError(f"TASK7_V6_UNAUTHORIZED_DRIFT:{section}")
    old_extractors = {row["usage_kind"]: row for row in v5["usage_extractor_registry"]["rows"]}
    new_extractors = {row["usage_kind"]: row for row in v6["usage_extractor_registry"]["rows"]}
    for usage_kind in old_extractors.keys() - {"ENRICHMENT_INPUT"}:
        if old_extractors[usage_kind] != new_extractors[usage_kind]:
            raise MaterializationError(f"TASK7_V6_UNAUTHORIZED_EXTRACTOR_DRIFT:{usage_kind}")
    old_assertions = v5["activation_assertions"]
    new_assertions = v6["activation_assertions"]
    if {
        key: value
        for key, value in old_assertions.items()
        if key != "expected_usage_extractor_sha256"
    } != {
        key: value
        for key, value in new_assertions.items()
        if key != "expected_usage_extractor_sha256"
    }:
        raise MaterializationError("TASK7_V6_ACTIVATION_ASSERTION_DRIFT")


def _build_v6(v5: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v5)
    _derive_usage_extractors(artifact)
    _transform_metadata(artifact)
    _validate_exact_diff(v5, artifact)
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
        raise MaterializationError("TASK7_V6_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V6_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Materialize append-frozen full-strict-v6 exactly once.",
    )
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=repo_root / "contracts" / "full_strict_v5_reviewed_preimage_v1.json",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _raw, v5 = _load_v5(args.base_artifact)
        artifact = _build_v6(v5)
        rendered = _render(artifact)
        _write_exclusive(args.output, rendered)
    except (MaterializationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V6_ARTIFACT_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
