"""Materialize the reviewed, inactive Task-8 FDD formula-policy v2 artifact.

Policy v2 is a deliberately bounded successor to the append-frozen,
unconsumed v1 artifact.  The v1 materializer is a pinned hermetic input: this
module refuses to build if those reviewed source bytes drift, then applies only
the approved identity and acyclic row-payload amendment.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = Path(r"C:\Projects\IDIS\IDIS\financial Due Diligence.xlsx")
POLICY_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
POLICY_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
REVIEW_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)

POLICY_ID = "fdd-formula-policy-v2"
POLICY_HASH_KEY = "fdd_formula_policy_v2_sha256"
POLICY_HASH_PLACEHOLDER = "$FDD_FORMULA_POLICY_V2_SHA256"
ROW_PAYLOAD_DOMAIN = "financial-diligence-row-v2"
COMPONENT_HASH_DOMAIN = "fdd-formula-policy-v2-component-v1"

_V1_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v1.py"
_V1_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
_V1_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v1_hash_v1.json"
)
_V1_REVIEW_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v1-review-map.md"
)
_V1_MATERIALIZER_SHA256 = "7115c9c3fe39c555df969dc8fea105fef8b84161a97ab74aaec2b02113bc6167"
_V1_ARTIFACT_SHA256 = "750f4375bd68f79fa64ee596189a09a1a9354f2aabfa710893f6d31c85090ee4"
_V1_FIXTURE_SHA256 = "5218927ddab2f58a4e09977c4e320b407bbc6dd870d598953d2b98633d12108f"
_V1_REVIEW_MAP_SHA256 = "653065eebb9c84193fe9aecb7c76304529391ee0095bd766d8e751c651d168fb"
_V1_POLICY_SHA256 = "537949a3327152241dedbb3871f4e098ab080e18370d0cfce331ad574efda9d2"


class MaterializationError(RuntimeError):
    """Raised with a stable reason code when v2 materialization fails closed."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MaterializationError(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_pinned_v1(path: Path, expected_sha256: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError:
        raise MaterializationError("TASK8_POLICY_V1_INPUT_UNAVAILABLE") from None
    _require(
        _sha256_bytes(raw) == expected_sha256,
        "TASK8_POLICY_V1_INPUT_DRIFT",
    )
    return raw


def _load_v1_materializer(path: Path) -> ModuleType:
    _read_pinned_v1(path, _V1_MATERIALIZER_SHA256)
    spec = importlib.util.spec_from_file_location("_task8_fdd_policy_v1_pinned", path)
    _require(
        spec is not None and spec.loader is not None,
        "TASK8_POLICY_V1_INPUT_UNAVAILABLE",
    )
    module = importlib.util.module_from_spec(cast(Any, spec))
    try:
        cast(Any, spec.loader).exec_module(module)
    except Exception:
        raise MaterializationError("TASK8_POLICY_V1_INPUT_UNAVAILABLE") from None
    return module


def _v1() -> ModuleType:
    return _load_v1_materializer(_V1_MATERIALIZER)


def _load_object(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise MaterializationError(code) from None
    _require(isinstance(value, dict), code)
    return cast(dict[str, Any], value)


def _validated_v1_authority(
    workbook_path: Path,
    *,
    v1_artifact_path: Path,
    v1_fixture_path: Path,
    v1_review_map_path: Path,
    v1_materializer_path: Path,
) -> tuple[dict[str, Any], ModuleType, bytes]:
    """Load normative v1 bytes and independently prove their reproducibility."""

    artifact_raw = _read_pinned_v1(v1_artifact_path, _V1_ARTIFACT_SHA256)
    fixture_raw = _read_pinned_v1(v1_fixture_path, _V1_FIXTURE_SHA256)
    review_map_raw = _read_pinned_v1(v1_review_map_path, _V1_REVIEW_MAP_SHA256)
    v1 = _load_v1_materializer(v1_materializer_path)
    artifact = _load_object(artifact_raw, "TASK8_POLICY_V1_INPUT_DRIFT")
    fixture = _load_object(fixture_raw, "TASK8_POLICY_V1_INPUT_DRIFT")

    try:
        regenerated = v1.build_policy(workbook_path)
        regenerated_raw = v1._render_json(regenerated)
        regenerated_map_raw = v1.render_review_map(regenerated)
    except Exception:
        raise MaterializationError("TASK8_POLICY_V1_REGENERATION_FAILED") from None
    _require(regenerated_raw == artifact_raw, "TASK8_POLICY_V1_REGENERATION_DRIFT")
    _require(regenerated_map_raw == review_map_raw, "TASK8_POLICY_V1_REVIEW_MAP_DRIFT")
    _require(
        fixture.get("artifact_schema") == "fdd-formula-policy-v1-hash-fixture-v1"
        and fixture.get("policy_id") == "fdd-formula-policy-v1"
        and fixture.get("artifact_sha256") == _V1_ARTIFACT_SHA256
        and fixture.get("fdd_formula_policy_v1_sha256") == _V1_POLICY_SHA256
        and fixture.get("review_map_sha256") == _V1_REVIEW_MAP_SHA256
        and artifact.get("digest_recipe_registry", {}).get("fdd_formula_policy_v1_sha256")
        == _V1_POLICY_SHA256,
        "TASK8_POLICY_V1_FIXTURE_BINDING_DRIFT",
    )
    return artifact, v1, review_map_raw


def _component_hashes(policy: dict[str, Any]) -> dict[str, str]:
    v1 = _v1()
    return {
        section: v1._sha256_canonical([COMPONENT_HASH_DOMAIN, section, policy[section]])
        for section in v1.HASHED_COMPONENTS
    }


def _policy_hash(policy: dict[str, Any]) -> str:
    v1 = _v1()
    component_hashes = _component_hashes(policy)
    return v1._sha256_canonical(
        [
            POLICY_ID,
            [[section, component_hashes[section]] for section in v1.HASHED_COMPONENTS],
        ]
    )


def _digest_registry(policy: dict[str, Any]) -> dict[str, Any]:
    v1 = _v1()
    component_hashes = _component_hashes(policy)
    return {
        "schema_version": "fdd-formula-policy-digests-v1",
        "algorithm": "SHA-256",
        "component_order": list(v1.HASHED_COMPONENTS),
        "component_hashes": component_hashes,
        "policy_hash_preimage": [
            POLICY_ID,
            [[section, component_hashes[section]] for section in v1.HASHED_COMPONENTS],
        ],
        POLICY_HASH_KEY: _policy_hash(policy),
    }


def _build_policy_from_v1_inputs(
    workbook_path: Path,
    *,
    v1_artifact_path: Path,
    v1_fixture_path: Path,
    v1_review_map_path: Path,
    v1_materializer_path: Path,
) -> dict[str, Any]:
    """Apply v2 only after admitting all exact preserved v1 authority bytes."""

    admitted, _v1_module, _v1_map = _validated_v1_authority(
        workbook_path,
        v1_artifact_path=v1_artifact_path,
        v1_fixture_path=v1_fixture_path,
        v1_review_map_path=v1_review_map_path,
        v1_materializer_path=v1_materializer_path,
    )
    policy = copy.deepcopy(admitted)
    metadata = policy["metadata"]
    metadata["artifact_schema"] = POLICY_ID
    metadata["policy_id"] = POLICY_ID
    metadata["policy_version"] = 2

    transition = policy["methodology_transition"]
    transition["successor_version_id_preimage"] = [
        "financial_dd",
        transition["source_artifact_sha256"],
        POLICY_ID,
        POLICY_HASH_PLACEHOLDER,
    ]

    financial = policy["financial_output_schema"]
    identity = financial["identity_and_trace_tuple"]
    _require(
        identity.count("dashboard_output_object_id") == 1,
        "TASK8_POLICY_V2_IDENTITY_SOURCE_INVALID",
    )
    financial["identity_and_trace_tuple"] = [
        item for item in identity if item != "dashboard_output_object_id"
    ]
    financial["row_payload_preimage"][0] = ROW_PAYLOAD_DOMAIN
    financial["row_payload_preimage"][1] = "$identity_and_trace_without_self_output"
    financial["dashboard_row_uuid_authority"] = {
        "authority_id": "methodology-truth-dashboard-row-v2",
        "target_field": "dashboard_row_id",
        "namespace_base": "NAMESPACE_URL",
        "namespace_url": "https://idis.local/contracts/methodology-truth-dashboard-row-v2",
        "namespace_uuid": "6aef4159-c49a-560a-9f92-6c6089d7f7c3",
        "name_preimage": [
            "methodology-truth-dashboard-row-v2",
            "tenant_id",
            "deal_id",
            "run_id",
            "pipeline_contract_version",
            "pipeline_contract_hash",
            "coverage_record_id",
            "methodology_version_record_id",
            "methodology_question_record_id",
            "question_payload_hash",
        ],
        "uuid_name_derivation": "LOWERCASE_SHA256_OF_CANONICAL_JSON_BYTES",
    }
    activation = policy["activation_assertions"]
    _require(
        activation.get("successor_pipeline_contract") == "full-strict-v22",
        "TASK8_POLICY_V2_SUCCESSOR_SOURCE_INVALID",
    )
    activation["successor_pipeline_contract"] = "full-strict-v23"

    policy.pop("digest_recipe_registry", None)
    policy["digest_recipe_registry"] = _digest_registry(policy)
    return cast(dict[str, Any], policy)


def build_policy(workbook_path: Path) -> dict[str, Any]:
    """Build exactly the approved v1-to-v2 amendment from pinned inputs."""

    return _build_policy_from_v1_inputs(
        workbook_path,
        v1_artifact_path=_V1_ARTIFACT,
        v1_fixture_path=_V1_FIXTURE,
        v1_review_map_path=_V1_REVIEW_MAP,
        v1_materializer_path=_V1_MATERIALIZER,
    )


def _render_review_map(policy: dict[str, Any], workbook_path: Path) -> bytes:
    """Render the same private mapping body under the v2 identity header."""

    v1_policy, _v1_module, raw = _validated_v1_authority(
        workbook_path,
        v1_artifact_path=_V1_ARTIFACT,
        v1_fixture_path=_V1_FIXTURE,
        v1_review_map_path=_V1_REVIEW_MAP,
        v1_materializer_path=_V1_MATERIALIZER,
    )
    stable_v2 = copy.deepcopy(policy)
    stable_v1 = copy.deepcopy(v1_policy)
    for candidate in (stable_v1, stable_v2):
        candidate.pop("metadata")
        candidate.pop("methodology_transition")
        candidate.pop("financial_output_schema")
        candidate.pop("digest_recipe_registry")
        candidate["activation_assertions"]["successor_pipeline_contract"] = (
            "$SUCCESSOR_PIPELINE_CONTRACT"
        )
    _require(stable_v1 == stable_v2, "TASK8_POLICY_V2_SEMANTIC_DRIFT")

    replacements = (
        (
            b"# Slice100 Task 8 FDD Formula Policy v1 \xe2\x80\x94 reviewer mapping",
            b"# Slice100 Task 8 FDD Formula Policy v2 \xe2\x80\x94 reviewer mapping",
        ),
        (
            b"contracts/fdd_formula_policy_v1.json",
            b"contracts/fdd_formula_policy_v2.json",
        ),
        (b"- Policy ID: `fdd-formula-policy-v1`", b"- Policy ID: `fdd-formula-policy-v2`"),
        (
            (
                b"- Policy SHA-256: `"
                + v1_policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"].encode(
                    "ascii"
                )
                + b"`"
            ),
            b"- Policy SHA-256: `" + _policy_hash(policy).encode("ascii") + b"`",
        ),
    )
    for old, new in replacements:
        _require(raw.count(old) == 1, "TASK8_POLICY_V2_REVIEW_MAP_SOURCE_INVALID")
        raw = raw.replace(old, new, 1)
    return raw


def _fixture(
    policy: dict[str, Any],
    *,
    artifact_bytes: bytes,
    review_map_bytes: bytes,
) -> dict[str, Any]:
    metadata = policy["metadata"]
    return {
        "artifact_schema": "fdd-formula-policy-v2-hash-fixture-v1",
        "policy_id": POLICY_ID,
        "artifact_sha256": _sha256_bytes(artifact_bytes),
        POLICY_HASH_KEY: _policy_hash(policy),
        "component_hashes": _component_hashes(policy),
        "review_map_sha256": _sha256_bytes(review_map_bytes),
        "counts": {
            "question_count": metadata["question_count"],
            "required_question_count": metadata["required_question_count"],
            "empty_requirement_count": metadata["empty_requirement_count"],
            "formula_count": metadata["formula_count"],
            "expanded_source_binding_count": metadata["expanded_source_binding_count"],
            "distinct_source_mapping_count": metadata["distinct_source_mapping_count"],
            "parent_binding_count": metadata["parent_binding_count"],
            "direct_signed_binding_count": metadata["direct_signed_binding_count"],
            "completeness_binding_count": metadata["completeness_binding_count"],
        },
        "activation_status": "INACTIVE",
    }


def build_policy_publication_bundle(workbook_path: Path) -> dict[str, bytes]:
    """Build the exact frozen v2 policy, fixture, and reviewer-map bytes."""

    v1 = _v1()
    policy = build_policy(workbook_path)
    artifact_bytes = v1._render_json(policy)
    review_map_bytes = _render_review_map(policy, workbook_path)
    fixture_bytes = v1._render_json(
        _fixture(
            policy,
            artifact_bytes=artifact_bytes,
            review_map_bytes=review_map_bytes,
        )
    )
    return {
        "policy_artifact_raw": artifact_bytes,
        "policy_fixture_raw": fixture_bytes,
        "policy_review_map_raw": review_map_bytes,
    }


def materialize(workbook_path: Path) -> dict[str, Any]:
    """Publish v2 to its disjoint no-clobber targets."""

    v1 = _v1()
    bundle = build_policy_publication_bundle(workbook_path)
    artifact_bytes = bundle["policy_artifact_raw"]
    fixture_bytes = bundle["policy_fixture_raw"]
    review_map_bytes = bundle["policy_review_map_raw"]
    policy = json.loads(artifact_bytes)
    POLICY_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_MAP.parent.mkdir(parents=True, exist_ok=True)
    v1._publish_policy_outputs(
        (
            ("fdd_formula_policy_fixture", POLICY_FIXTURE, fixture_bytes),
            ("fdd_formula_policy_review_map", REVIEW_MAP, review_map_bytes),
            ("fdd_formula_policy_artifact", POLICY_ARTIFACT, artifact_bytes),
        )
    )
    return {
        "artifact_sha256": _sha256_bytes(artifact_bytes),
        "policy_sha256": _policy_hash(policy),
        "review_map_sha256": _sha256_bytes(review_map_bytes),
        "fixture_sha256": _sha256_bytes(fixture_bytes),
        "publication_order": [
            "fdd_formula_policy_fixture",
            "fdd_formula_policy_review_map",
            "fdd_formula_policy_artifact",
        ],
        "commit_point": "fdd_formula_policy_artifact",
        "activation_status": "INACTIVE",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        args = _parse_args()
        if args.write:
            result = materialize(args.workbook)
        else:
            bundle = build_policy_publication_bundle(args.workbook)
            policy = json.loads(bundle["policy_artifact_raw"])
            result = {
                "policy_sha256": _policy_hash(policy),
                "question_count": 79,
                "formula_count": 10,
                "expanded_source_binding_count": 107,
                "activation_status": "INACTIVE",
            }
    except Exception as exc:
        if isinstance(exc, MaterializationError) and re.fullmatch(r"TASK8_[A-Z0-9_]+", str(exc)):
            reason_code = str(exc)
        elif isinstance(exc, OSError):
            reason_code = "TASK8_POLICY_INPUT_UNAVAILABLE"
        else:
            reason_code = "TASK8_POLICY_MATERIALIZATION_FAILED"
        print(json.dumps({"reason_code": reason_code, "result": "FAILED"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
