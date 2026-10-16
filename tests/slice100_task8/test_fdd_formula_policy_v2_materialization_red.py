"""RED admission for immutable policy-v1 preservation and corrected policy v2."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = Path(r"C:\Projects\IDIS\IDIS\financial Due Diligence.xlsx")
V1_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"
V1_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v1_hash_v1.json"
)
V1_REVIEW_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v1-review-map.md"
)
V1_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v1.py"
V2_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
V2_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
V2_REVIEW_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
V2_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v2.py"

V1_HASHES = {
    V1_ARTIFACT: "750f4375bd68f79fa64ee596189a09a1a9354f2aabfa710893f6d31c85090ee4",
    V1_FIXTURE: "5218927ddab2f58a4e09977c4e320b407bbc6dd870d598953d2b98633d12108f",
    V1_REVIEW_MAP: "653065eebb9c84193fe9aecb7c76304529391ee0095bd766d8e751c651d168fb",
    V1_MATERIALIZER: "7115c9c3fe39c555df969dc8fea105fef8b84161a97ab74aaec2b02113bc6167",
}


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        pytest.fail("TASK8_POLICY_V2_MATERIALIZER_NOT_IMPLEMENTED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_object(path: Path, code: str) -> dict[str, object]:
    if not path.is_file():
        pytest.fail(code)
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        pytest.fail(code)
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_without_versioned_policy_bytes(value: dict[str, object]) -> dict[str, object]:
    copy = json.loads(json.dumps(value))
    copy["metadata"] = {
        key: item
        for key, item in copy["metadata"].items()
        if key not in {"artifact_schema", "policy_id", "policy_version"}
    }
    copy["methodology_transition"]["successor_version_id_preimage"] = [
        "financial_dd",
        copy["methodology_transition"]["source_artifact_sha256"],
        "$POLICY_ID",
        "$POLICY_SHA256",
    ]
    copy["activation_assertions"]["successor_pipeline_contract"] = "$SUCCESSOR_PIPELINE_CONTRACT"
    financial = copy["financial_output_schema"]
    financial["row_payload_preimage"][0] = "$ROW_PAYLOAD_DOMAIN"
    financial["row_payload_preimage"][1] = "$IDENTITY_AND_TRACE_TUPLE"
    financial["identity_and_trace_tuple"] = [
        item
        for item in financial["identity_and_trace_tuple"]
        if item != "dashboard_output_object_id"
    ]
    financial.pop("dashboard_row_uuid_authority", None)
    copy.pop("digest_recipe_registry")
    return copy


def test_policy_v1_frozen_files_keep_pinned_hashes_before_v2_build() -> None:
    observed = {path: _sha(path) for path in V1_HASHES}
    if observed != V1_HASHES:
        raise AssertionError("TASK8_POLICY_V1_ARCHIVE_MUTATED")


def test_policy_v2_build_is_the_exact_bounded_amendment_without_v1_mutation() -> None:
    before = {path: path.read_bytes() for path in V1_HASHES}
    materializer = _load_module(V2_MATERIALIZER, "_task8_policy_v2_red")
    v2 = materializer.build_policy(WORKBOOK)
    after = {path: path.read_bytes() for path in V1_HASHES}
    if before != after:
        raise AssertionError("TASK8_POLICY_V1_ARCHIVE_MUTATED")

    v1 = _load_object(V1_ARTIFACT, "TASK8_POLICY_V1_ARCHIVE_MISSING")
    if _semantic_without_versioned_policy_bytes(v2) != (
        _semantic_without_versioned_policy_bytes(v1)
    ):
        raise AssertionError("TASK8_POLICY_V2_UNBOUNDED_SEMANTIC_DRIFT")
    if v2["metadata"] != {
        **v1["metadata"],
        "artifact_schema": "fdd-formula-policy-v2",
        "policy_id": "fdd-formula-policy-v2",
        "policy_version": 2,
    }:
        raise AssertionError("TASK8_POLICY_V2_METADATA_INVALID")
    if v2["methodology_transition"]["successor_version_id_preimage"] != [
        "financial_dd",
        v1["metadata"]["workbook_sha256"],
        "fdd-formula-policy-v2",
        "$FDD_FORMULA_POLICY_V2_SHA256",
    ]:
        raise AssertionError("TASK8_POLICY_V2_METHODOLOGY_IDENTITY_INVALID")
    financial = v2["financial_output_schema"]
    if financial["row_payload_preimage"][0] != "financial-diligence-row-v2":
        raise AssertionError("TASK8_POLICY_V2_ROW_PAYLOAD_DOMAIN_INVALID")
    if "dashboard_output_object_id" not in financial["row_schema"]["field_tuple"]:
        raise AssertionError("TASK8_POLICY_V2_SERIALIZED_OUTPUT_ID_MISSING")
    if "dashboard_output_object_id" in financial["identity_and_trace_tuple"]:
        raise AssertionError("TASK8_POLICY_V2_SELF_OUTPUT_ID_NOT_REMOVED")
    if financial["row_payload_preimage"][1] != "$identity_and_trace_without_self_output":
        raise AssertionError("TASK8_POLICY_V2_IDENTITY_REFERENCE_INVALID")
    if financial.get("dashboard_row_uuid_authority") != {
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
    }:
        raise AssertionError("TASK8_POLICY_V2_DASHBOARD_ROW_UUID_AUTHORITY_INVALID")
    if v2["activation_assertions"]["successor_pipeline_contract"] != "full-strict-v23":
        raise AssertionError("TASK8_POLICY_V2_SUCCESSOR_CONTRACT_INVALID")


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _all_strings(child)]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in [key, *_all_strings(child)]]
    return []


def test_policy_v2_contains_no_rejected_v22_or_v1_policy_identity_reference() -> None:
    policy = _load_object(V2_ARTIFACT, "TASK8_POLICY_V2_ARTIFACT_NOT_IMPLEMENTED")
    strings = _all_strings(policy)
    if any("full-strict-v22" in value or "full_strict_v22" in value for value in strings):
        raise AssertionError("TASK8_POLICY_V2_REJECTED_V22_IDENTITY_LEAK")
    if any(
        marker in value
        for value in strings
        for marker in (
            "fdd-formula-policy-v1",
            "fdd_formula_policy_v1",
            "FDD_FORMULA_POLICY_V1",
        )
    ):
        raise AssertionError("TASK8_POLICY_V2_V1_IDENTITY_LEAK")


@pytest.mark.parametrize(
    "input_name",
    ("artifact", "fixture", "review_map", "materializer"),
)
@pytest.mark.parametrize("failure_kind", ("missing", "mutated"))
def test_policy_v2_fails_closed_when_any_pinned_v1_input_is_missing_or_mutated(
    tmp_path: Path,
    input_name: str,
    failure_kind: str,
) -> None:
    sources = {
        "artifact": V1_ARTIFACT,
        "fixture": V1_FIXTURE,
        "review_map": V1_REVIEW_MAP,
        "materializer": V1_MATERIALIZER,
    }
    copied: dict[str, Path] = {}
    for name, source in sources.items():
        target = tmp_path / name / source.name
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        copied[name] = target
    if failure_kind == "missing":
        copied[input_name].unlink()
        code = "TASK8_POLICY_V1_INPUT_UNAVAILABLE"
    else:
        copied[input_name].write_bytes(copied[input_name].read_bytes() + b"\n")
        code = "TASK8_POLICY_V1_INPUT_DRIFT"

    materializer = _load_module(V2_MATERIALIZER, f"_task8_policy_v2_{input_name}_{failure_kind}")
    with pytest.raises(materializer.MaterializationError, match=rf"^{code}$"):
        materializer._build_policy_from_v1_inputs(
            WORKBOOK,
            v1_artifact_path=copied["artifact"],
            v1_fixture_path=copied["fixture"],
            v1_review_map_path=copied["review_map"],
            v1_materializer_path=copied["materializer"],
        )


def test_policy_v2_artifact_fixture_and_reviewer_map_are_exact() -> None:
    policy = _load_object(V2_ARTIFACT, "TASK8_POLICY_V2_ARTIFACT_NOT_IMPLEMENTED")
    fixture = _load_object(V2_FIXTURE, "TASK8_POLICY_V2_FIXTURE_NOT_IMPLEMENTED")
    materializer = _load_module(V2_MATERIALIZER, "_task8_policy_v2_exact_red")
    bundle = materializer.build_policy_publication_bundle(WORKBOOK)

    if bundle["policy_artifact_raw"] != V2_ARTIFACT.read_bytes():
        raise AssertionError("TASK8_POLICY_V2_ARTIFACT_DRIFT")
    if bundle["policy_fixture_raw"] != V2_FIXTURE.read_bytes():
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_DRIFT")
    if bundle["policy_review_map_raw"] != V2_REVIEW_MAP.read_bytes():
        raise AssertionError("TASK8_POLICY_V2_REVIEW_MAP_DRIFT")
    if fixture["artifact_schema"] != "fdd-formula-policy-v2-hash-fixture-v1":
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_SCHEMA_INVALID")
    if fixture["policy_id"] != "fdd-formula-policy-v2":
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_ID_INVALID")
    if fixture["artifact_sha256"] != _sha(V2_ARTIFACT):
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_ARTIFACT_HASH_INVALID")
    if fixture["review_map_sha256"] != _sha(V2_REVIEW_MAP):
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_MAP_HASH_INVALID")
    if (
        fixture["fdd_formula_policy_v2_sha256"]
        != (policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"])
    ):
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_POLICY_HASH_INVALID")


def test_policy_v2_publication_targets_are_disjoint_from_v1_archive() -> None:
    if not {V1_ARTIFACT, V1_FIXTURE, V1_REVIEW_MAP}.isdisjoint(
        {V2_ARTIFACT, V2_FIXTURE, V2_REVIEW_MAP}
    ):
        raise AssertionError("TASK8_POLICY_V2_PUBLICATION_TARGET_COLLISION")
    materializer = _load_module(V2_MATERIALIZER, "_task8_policy_v2_paths_red")
    if materializer.POLICY_ARTIFACT != V2_ARTIFACT:
        raise AssertionError("TASK8_POLICY_V2_ARTIFACT_TARGET_INVALID")
    if materializer.POLICY_FIXTURE != V2_FIXTURE:
        raise AssertionError("TASK8_POLICY_V2_FIXTURE_TARGET_INVALID")
    if materializer.REVIEW_MAP != V2_REVIEW_MAP:
        raise AssertionError("TASK8_POLICY_V2_REVIEW_MAP_TARGET_INVALID")
