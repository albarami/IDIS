"""RED admission gate for the full-strict-v6 usage-projection correction."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from _full_strict_v6_usage_authority import (
    CORRECTED_ROUTE_ORDINALS,
    EDGE_MANIFEST_SHA256,
    ROUTE_REGISTRY_SHA256,
    V5_ACCEPTED_CONSUMER_SHA256,
    V5_CONTRACT_SHA256,
    V5_ENRICHMENT_EXTRACTOR_SHA256,
    V5_EXTRACTOR_REGISTRY_SHA256,
    V6_ACCEPTED_CONSUMER_SHA256,
    V6_ENRICHMENT_EXTRACTOR_SHA256,
    V6_EXTRACTOR_REGISTRY_SHA256,
    accepted_consumers_for_kind,
    canonical_bytes,
    canonical_hash,
    expected_v6_reviewed_design,
)
from _historical_v6_fixture import (
    historical_v6_fixture,
    historical_v6_fixture_bytes,
)
from _void_contract_hash_evidence import (
    VOID_HASHES,
    VOID_V6_ARTIFACT_SHA256,
    VOID_V6_CONTRACT_SHA256,
    VOID_V6_FIXTURE_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V5_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v5_reviewed_preimage_v1.json"
_V6_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"
_V6_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v6_preimage.py"
_V6_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v6_contract_fixture.py"
_BLOCKLISTED_V6_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v6_contract_hash_v1.json"
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
_PRE_V6_VOID_HASHES = tuple(
    value
    for value in VOID_HASHES
    if value
    not in {
        VOID_V6_ARTIFACT_SHA256.encode("ascii"),
        VOID_V6_FIXTURE_SHA256.encode("ascii"),
        VOID_V6_CONTRACT_SHA256.encode("ascii"),
    }
)


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required v6 authority is absent: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required v6 module is absent: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _extractors(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = artifact["usage_extractor_registry"]["rows"]
    return {row["usage_kind"]: row for row in rows}


def test_v6_materialization_assets_exist_before_admission() -> None:
    assert _V6_MATERIALIZER.is_file()
    assert _V6_GENERATOR.is_file()
    assert _V6_ARTIFACT.is_file()
    assert not _BLOCKLISTED_V6_FIXTURE.exists()


def test_all_19_accepted_consumer_sets_are_exact_route_projections() -> None:
    artifact = _load(_V6_ARTIFACT)
    routes = artifact["usage_route_registry"]["rows"]
    extractors = artifact["usage_extractor_registry"]["rows"]

    assert len(routes) == 1_756
    assert len(extractors) == 19
    assert [route[0] for route in routes] == list(range(1_756))
    for extractor in extractors:
        usage_kind = extractor["usage_kind"]
        expected = accepted_consumers_for_kind(routes, usage_kind)
        assert extractor["accepted_consumers"] == expected
        assert extractor["expected_accepted_consumer_sha256"] == canonical_hash(
            ["accepted-consumer-set-v2", usage_kind, expected]
        )
        assert extractor["expected_sha256"] == canonical_hash(
            [
                "usage-extractor-definition-v2",
                usage_kind,
                extractor["extractor_id"],
                extractor["extractor_version"],
                extractor["program"],
                expected,
            ]
        )


def test_v5_to_v6_changes_exactly_50_e048_consumer_tuples() -> None:
    v5 = _load(_V5_ARTIFACT)
    v6 = _load(_V6_ARTIFACT)
    v5_extractors = _extractors(v5)
    v6_extractors = _extractors(v6)

    assert set(v5_extractors) == set(v6_extractors)
    for usage_kind in v5_extractors.keys() - {"ENRICHMENT_INPUT"}:
        assert v6_extractors[usage_kind] == v5_extractors[usage_kind]

    old = v5_extractors["ENRICHMENT_INPUT"]
    new = v6_extractors["ENRICHMENT_INPUT"]
    for field in ("usage_kind", "extractor_id", "extractor_version", "program"):
        assert new[field] == old[field]
    mismatches = [
        (left, right)
        for left, right in zip(
            old["accepted_consumers"],
            new["accepted_consumers"],
            strict=True,
        )
        if left != right
    ]
    assert tuple(left[0] for left, _right in mismatches) == CORRECTED_ROUTE_ORDINALS
    assert len(mismatches) == 50
    for left, right in mismatches:
        assert left[0] == right[0]
        assert left[1] == "E029_PROVIDER_FACT_SEMANTIC"
        assert right[1] == "E048_PROVIDER_FACT_EXTERNAL_CONTEXT"
        assert right[2] == left[2] + ";external_context_index_allowed=true"
        assert right[3:] == left[3:]


def test_v6_exact_diff_preserves_every_non_usage_semantic_byte() -> None:
    v5 = _load(_V5_ARTIFACT)
    v6 = _load(_V6_ARTIFACT)

    assert tuple(v6) == tuple(v5)
    for section in _UNCHANGED_SECTIONS:
        assert v6[section] == v5[section], section

    old_metadata = v5["metadata"]
    new_metadata = v6["metadata"]
    variable_metadata = {
        "artifact_schema",
        "pipeline_contract_version",
        "reviewed_design_sha256",
        "reviewed_design_utf8_hex",
    }
    assert {key: value for key, value in new_metadata.items() if key not in variable_metadata} == {
        key: value for key, value in old_metadata.items() if key not in variable_metadata
    }
    assert new_metadata["artifact_schema"] == "full-strict-v6-reviewed-preimage-v1"
    assert new_metadata["pipeline_contract_version"] == "full-strict-v6"
    old_design = json.loads(bytes.fromhex(old_metadata["reviewed_design_utf8_hex"]))
    new_design = json.loads(bytes.fromhex(new_metadata["reviewed_design_utf8_hex"]))
    assert new_design == expected_v6_reviewed_design(old_design)
    assert (
        new_metadata["reviewed_design_sha256"]
        == hashlib.sha256(canonical_bytes(new_design)).hexdigest()
    )

    old_recipes = v5["digest_recipe_registry"]
    new_recipes = v6["digest_recipe_registry"]
    expected_recipes = json.loads(json.dumps(old_recipes["rows"]))
    expected_recipes[5][1] = (
        '["pipeline-contract-v6","full-strict-v6",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    assert new_recipes["rows"] == expected_recipes
    assert bytes.fromhex(new_recipes["reviewed_literal_utf8_hex"]) == canonical_bytes(
        expected_recipes
    )

    old_assertions = v5["activation_assertions"]
    new_assertions = v6["activation_assertions"]
    assert {
        key: value
        for key, value in new_assertions.items()
        if key != "expected_usage_extractor_sha256"
    } == {
        key: value
        for key, value in old_assertions.items()
        if key != "expected_usage_extractor_sha256"
    }
    assert new_assertions["expected_usage_extractor_sha256"] == V6_EXTRACTOR_REGISTRY_SHA256


def test_v6_usage_hash_chain_is_exact_and_other_vectors_are_invariant() -> None:
    v5 = _load(_V5_ARTIFACT)
    v6 = _load(_V6_ARTIFACT)
    old = _extractors(v5)["ENRICHMENT_INPUT"]
    new = _extractors(v6)["ENRICHMENT_INPUT"]

    assert old["expected_accepted_consumer_sha256"] == V5_ACCEPTED_CONSUMER_SHA256
    assert new["expected_accepted_consumer_sha256"] == V6_ACCEPTED_CONSUMER_SHA256
    assert old["expected_sha256"] == V5_ENRICHMENT_EXTRACTOR_SHA256
    assert new["expected_sha256"] == V6_ENRICHMENT_EXTRACTOR_SHA256
    assert v5["usage_extractor_registry"]["expected_sha256"] == V5_EXTRACTOR_REGISTRY_SHA256
    assert v6["usage_extractor_registry"]["expected_sha256"] == V6_EXTRACTOR_REGISTRY_SHA256
    assert v6["usage_route_registry"]["expected_sha256"] == ROUTE_REGISTRY_SHA256
    assert v6["edge_rule_registry"]["expected_manifest_sha256"] == EDGE_MANIFEST_SHA256
    assert v6["activation_assertions"] == {
        **v5["activation_assertions"],
        "expected_usage_extractor_sha256": V6_EXTRACTOR_REGISTRY_SHA256,
    }


def test_v6_generator_rejects_cross_registry_corruption(tmp_path: Path) -> None:
    artifact = _load(_V6_ARTIFACT)
    corrupt = json.loads(json.dumps(artifact))
    enrichment = _extractors(corrupt)["ENRICHMENT_INPUT"]
    enrichment["accepted_consumers"][0][1] = "E029_PROVIDER_FACT_SEMANTIC"
    corrupt_path = tmp_path / "corrupt-v6.json"
    corrupt_path.write_bytes(
        json.dumps(corrupt, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(_V6_GENERATOR),
            "--artifact",
            str(corrupt_path),
            "--stdout",
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "TASK7_USAGE_ROUTE_PROJECTION_MISMATCH" in completed.stderr


def test_v6_materializer_and_generator_are_hermetic_and_exclusive(
    tmp_path: Path,
) -> None:
    artifact_output = tmp_path / "v6.json"
    materialize = [
        sys.executable,
        str(_V6_MATERIALIZER),
        "--output",
        str(artifact_output),
    ]
    first = subprocess.run(
        materialize,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert artifact_output.read_bytes() == _V6_ARTIFACT.read_bytes()
    second = subprocess.run(
        materialize,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 2
    assert "TASK7_V6_OUTPUT_ALREADY_EXISTS" in second.stderr

    fixture_output = tmp_path / "fixture.json"
    generate = [
        sys.executable,
        str(_V6_GENERATOR),
        "--artifact",
        str(artifact_output),
        "--output",
        str(fixture_output),
    ]
    generated = subprocess.run(
        generate,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert fixture_output.read_bytes() == historical_v6_fixture_bytes(_REPO_ROOT)
    repeated = subprocess.run(
        generate,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 2
    assert "TASK7_V6_FIXTURE_OUTPUT_ALREADY_EXISTS" in repeated.stderr


def test_v6_fixture_is_hash_bound_and_void_hash_free() -> None:
    artifact = _load(_V6_ARTIFACT)
    fixture = historical_v6_fixture(_REPO_ROOT)

    assert artifact["metadata"]["append_frozen"] is True
    assert fixture["fixture_schema"] == "full-strict-v6-contract-hash-fixture-v1"
    assert len(fixture["FULL_STRICT_V6_CONTRACT_HASH"]) == 64
    assert fixture["FULL_STRICT_V6_CONTRACT_HASH"] != V5_CONTRACT_SHA256
    assert fixture["artifact_sha256"] == hashlib.sha256(_V6_ARTIFACT.read_bytes()).hexdigest()
    assert (
        fixture["component_hashes"]["usage_extractor_registry_hash"] == V6_EXTRACTOR_REGISTRY_SHA256
    )
    assert fixture["component_hashes"]["usage_path_route_hash"] == ROUTE_REGISTRY_SHA256
    assert fixture["component_hashes"]["edge_manifest_hash"] == EDGE_MANIFEST_SHA256
    for void_hash in _PRE_V6_VOID_HASHES:
        assert void_hash not in _V6_ARTIFACT.read_bytes()
        assert void_hash not in historical_v6_fixture_bytes(_REPO_ROOT)


def test_v6_generator_module_derives_the_checked_in_fixture() -> None:
    generator = _load_module(_V6_GENERATOR, "task7_v6_generator_under_test")
    derived = generator.derive_contract_bytes(_V6_ARTIFACT.read_bytes())
    assert derived == historical_v6_fixture(_REPO_ROOT)


@pytest.mark.parametrize(
    "section",
    (
        "usage_route_registry",
        "usage_extractor_registry",
        "edge_rule_registry",
        "rights_index_policy_registry",
        "schema_registry",
        "erasure_materialized",
    ),
)
def test_v6_section_corruption_changes_contract_hash(section: str) -> None:
    generator = _load_module(
        _V6_GENERATOR,
        f"task7_v6_generator_corruption_{section}",
    )
    artifact = _load(_V6_ARTIFACT)
    baseline = generator.derive_contract_bytes(_V6_ARTIFACT.read_bytes())
    corrupt = json.loads(json.dumps(artifact))
    corrupt[section]["rows"][0] = json.loads(json.dumps(corrupt[section]["rows"][0]))
    if isinstance(corrupt[section]["rows"][0], list):
        corrupt[section]["rows"][0].append("corruption")
    else:
        corrupt[section]["rows"][0]["corruption"] = True
    raw = json.dumps(corrupt, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    try:
        changed = generator.derive_contract_bytes(raw, validate=False)
    except ValueError as exc:
        # A section-level validator may reject corruption before the final digest.
        assert getattr(exc, "code", None) in {
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            "TASK7_CONTRACT_ARTIFACT_INVALID",
        }
    else:
        assert changed["FULL_STRICT_V6_CONTRACT_HASH"] != (baseline["FULL_STRICT_V6_CONTRACT_HASH"])
