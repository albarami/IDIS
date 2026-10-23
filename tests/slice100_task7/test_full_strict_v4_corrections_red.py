"""Active v6 proofs retaining the reviewed v3-to-v4 correction history."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest
from _historical_v6_fixture import (
    historical_v6_fixture,
    historical_v6_fixture_bytes,
)
from _void_contract_hash_evidence import (
    VOID_HASHES,
    VOID_V3_ARTIFACT_SHA256,
    VOID_V6_ARTIFACT_SHA256,
    VOID_V6_CONTRACT_SHA256,
    VOID_V6_FIXTURE_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V3_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v3_reviewed_preimage_v1.json"
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"
_V5_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"
_BLOCKLISTED_V6_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v6_contract_hash_v1.json"
)
_V4_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v4_preimage.py"
_V5_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v6_contract_fixture.py"

_GRAPH_RAG_STEPS = frozenset({"GRAPH_EVIDENCE", "RAG_EVIDENCE"})
_RIGHTS_INDEX_POLICY_REGISTRY_VERSION = "external-context-rights-index-policy-v1"
_EXPECTED_RIGHTS_INDEX_POLICY_REGISTRY_SHA256 = (
    "ad041ac654596094915408f0448220284ff63cb3d57df0042b0d02769f5cb28e"
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


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _rights_index_policy_rows(artifact: dict[str, Any]) -> tuple[tuple[object, ...], ...]:
    """Read the historical registry from Task 7's sole normative byte source."""

    return tuple(tuple(row) for row in artifact["rights_index_policy_registry"]["rows"])


def _rights_index_policy_sha256(rows: tuple[tuple[object, ...], ...]) -> str:
    return _canonical_hash([_RIGHTS_INDEX_POLICY_REGISTRY_VERSION, rows])


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required Task-7 contract bytes are absent: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _route_counts(rows: list[list[Any]]) -> dict[str, int]:
    return {
        rule_id: sum(row[1] == rule_id for row in rows)
        for rule_id in (
            "E029_PROVIDER_FACT_SEMANTIC",
            "E030_PROVIDER_FACT_DELIVERABLES",
            "E048_PROVIDER_FACT_EXTERNAL_CONTEXT",
        )
    }


def test_historical_v3_to_v4_bytes_are_frozen_while_v5_is_the_active_path() -> None:
    assert hashlib.sha256(_V3_ARTIFACT.read_bytes()).hexdigest() == VOID_V3_ARTIFACT_SHA256
    assert _V4_ARTIFACT != _V3_ARTIFACT
    assert _V4_MATERIALIZER.is_file()
    assert _V5_ARTIFACT != _V4_ARTIFACT
    assert _V5_GENERATOR.is_file()
    assert not _BLOCKLISTED_V6_FIXTURE.exists()


def test_v5_metadata_fixture_and_closed_counts_are_exact() -> None:
    artifact = _load(_V5_ARTIFACT)
    fixture = historical_v6_fixture(_REPO_ROOT)

    assert artifact["metadata"]["artifact_schema"] == "full-strict-v6-reviewed-preimage-v1"
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v6"
    assert fixture["fixture_schema"] == "full-strict-v6-contract-hash-fixture-v1"
    assert len(fixture["FULL_STRICT_V6_CONTRACT_HASH"]) == 64
    assert fixture["artifact_sha256"] == hashlib.sha256(_V5_ARTIFACT.read_bytes()).hexdigest()
    assertions = artifact["activation_assertions"]
    assert assertions["semantic_step_count"] == 28
    assert assertions["edge_rule_count"] == 48
    assert assertions["usage_route_count"] == 1_756
    assert assertions["usage_extractor_count"] == 19
    assert fixture["activation_assertions"]["edge_rule_count"] == 48
    assert fixture["activation_assertions"]["usage_route_count"] == 1_756


def test_e029_e048_route_split_conserves_all_1756_routes() -> None:
    v3_rows = _load(_V3_ARTIFACT)["usage_route_registry"]["rows"]
    v4_rows = _load(_V4_ARTIFACT)["usage_route_registry"]["rows"]
    v5_rows = _load(_V5_ARTIFACT)["usage_route_registry"]["rows"]

    assert [row[0] for row in v4_rows] == list(range(1_756))
    assert v5_rows == v4_rows
    counts = _route_counts(v4_rows)
    assert counts == {
        "E029_PROVIDER_FACT_SEMANTIC": 219,
        "E030_PROVIDER_FACT_DELIVERABLES": 100,
        "E048_PROVIDER_FACT_EXTERNAL_CONTEXT": 50,
    }
    moved_ordinals = {
        row[0]
        for row in v3_rows
        if row[1] == "E029_PROVIDER_FACT_SEMANTIC" and row[3] in _GRAPH_RAG_STEPS
    }
    assert len(moved_ordinals) == 50
    assert sum(left == right for left, right in zip(v3_rows, v4_rows, strict=True)) == 1_706
    for old, new in zip(v3_rows, v4_rows, strict=True):
        if old[0] in moved_ordinals:
            assert old[1] == "E029_PROVIDER_FACT_SEMANTIC"
            assert new[1] == "E048_PROVIDER_FACT_EXTERNAL_CONTEXT"
            assert new[3] in _GRAPH_RAG_STEPS
            assert new[3:] == old[3:]
            assert new[2] == old[2] + ";external_context_index_allowed=true"
        else:
            assert new == old


def test_non_index_routes_remain_unconditional_and_e048_is_graph_rag_only() -> None:
    artifact = _load(_V5_ARTIFACT)
    rules = {row["rule_id"]: row for row in artifact["edge_rule_registry"]["rows"]}
    routes = artifact["usage_route_registry"]["rows"]
    e029 = [row for row in routes if row[1] == "E029_PROVIDER_FACT_SEMANTIC"]
    e048 = [row for row in routes if row[1] == "E048_PROVIDER_FACT_EXTERNAL_CONTEXT"]

    assert rules["E029_PROVIDER_FACT_SEMANTIC"]["predicate_kind"] == "UNCONDITIONAL"
    assert rules["E048_PROVIDER_FACT_EXTERNAL_CONTEXT"]["predicate_kind"] == (
        "IMMUTABLE_PROVIDER_OUTCOME_FIELD"
    )
    assert rules["E048_PROVIDER_FACT_EXTERNAL_CONTEXT"]["predicate_id"] == (
        "EXTERNAL_CONTEXT_INDEX_ALLOWED"
    )
    assert not any(row[3] in _GRAPH_RAG_STEPS for row in e029)
    assert {row[3] for row in e048} == _GRAPH_RAG_STEPS
    assert all(row[2].endswith(";external_context_index_allowed=true") for row in e048)


def test_provider_outcome_flag_is_immutable_and_hash_bound() -> None:
    artifact = _load(_V5_ARTIFACT)
    provider_outcome = next(
        row
        for row in artifact["future_native_authority_registry"]["rows"]
        if row["planned_table"] == "enrichment_provider_outcomes"
    )
    definitions = {
        name: definition for _ordinal, name, definition in provider_outcome["immutable_fields"]
    }

    assert definitions["rights_approval_id"] == "UUID"
    assert definitions["rights_version_hash"].startswith("TEXT CHECK")
    assert definitions["rights_class"].startswith("TEXT CHECK")
    assert definitions["no_store_policy_version"].startswith("TEXT CHECK")
    assert definitions["attribution_policy_version"].startswith("TEXT CHECK")
    assert definitions["external_context_index_allowed"] == "BOOLEAN NOT NULL"
    assert definitions["outcome_hash_recipe_version"] == (
        "TEXT GENERATED ALWAYS AS ('enrichment-provider-outcome-sha256-v1') STORED"
    )
    assert definitions["native_output_recipe_version"] == (
        "TEXT GENERATED ALWAYS AS ('provider-outcome-native-output-v1') STORED"
    )
    assert definitions["native_output_sha256"] == (
        "TEXT NOT NULL CHECK (native_output_sha256~'^[0-9a-f]{64}$')"
    )
    assert definitions["outcome_sha256"] == (
        "TEXT NOT NULL CHECK (outcome_sha256~'^[0-9a-f]{64}$')"
    )
    assert any(
        "outcome_sha256~'^[0-9a-f]{64}$'" in check[1] for check in provider_outcome["check_tuple"]
    )
    rights_check = next(
        body
        for name, body in provider_outcome["check_tuple"]
        if name == "ck_v4_enrichment_provider_outcomes_rights_index"
    )
    assert "outcome='NOT_APPLICABLE'" in rights_check
    assert "rights_approval_id IS NULL" in rights_check
    assert "external_context_index_allowed=false" in rights_check
    assert "outcome IN ('HIT','MISS')" in rights_check
    assert "no_store_policy_version='external-context-store-v1'" in rights_check
    assert "external_context_index_allowed=true" in rights_check
    assert "no_store_policy_version='external-context-no-store-v1'" in rights_check
    assert rights_check.count("(provider_id='") == 18
    for policy_row in _rights_index_policy_rows(artifact):
        _ordinal, provider_id, rights_class, _requirement, _store, attribution, _allowed = (
            policy_row
        )
        assert (
            f"provider_id='{provider_id}' AND rights_class='{rights_class}' AND "
            f"attribution_policy_version='{attribution}'"
        ) in rights_check

    digest_recipes = dict(artifact["digest_recipe_registry"]["rows"])
    assert digest_recipes["enrichment_provider_outcome_sha256"] == (
        'H(["enrichment-provider-outcome-sha256-v1",tenant_id,deal_id,run_id,'
        "provider_outcome_id,provider_id,outcome,identity_output_object_id,"
        "applicability_decision_id,rights_approval_id,rights_version_hash,rights_class,"
        "no_store_policy_version,attribution_policy_version,budget_hash,probe_hash,request_hash,"
        "provenance_hash,external_context_index_allowed,outcome_hash_recipe_version])"
    )
    assert digest_recipes["provider_outcome_native_output_sha256"] == (
        'H(["provider-outcome-native-output-v1",tenant_id,deal_id,run_id,'
        "provider_outcome_id,provider_id,outcome,identity_output_object_id,"
        "applicability_decision_id,outcome_sha256,native_output_recipe_version])"
    )

    census = next(
        row
        for row in artifact["schema_registry"]["rows"]
        if row["schema_id"] == "provider-outcome-census-v1"
    )
    snapshot = census["normalized_schema_snapshot"]
    for field_name in (
        "rights_approval_id",
        "rights_version_hash",
        "rights_class",
        "no_store_policy_version",
        "attribution_policy_version",
        "external_context_index_allowed",
        "outcome_hash_recipe_version",
        "outcome_sha256",
        "native_output_recipe_version",
        "native_output_sha256",
    ):
        assert field_name in snapshot["properties"]
        assert field_name in snapshot["required"]


def test_provider_outcome_hash_to_native_root_derivation_is_acyclic_and_rehydratable() -> None:
    artifact = _load(_V5_ARTIFACT)
    values: dict[str, object] = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "deal_id": "22222222-2222-4222-8222-222222222222",
        "run_id": "33333333-3333-4333-8333-333333333333",
        "provider_outcome_id": "44444444-4444-4444-8444-444444444444",
        "provider_id": "sec_edgar",
        "outcome": "HIT",
        "identity_output_object_id": "55555555-5555-4555-8555-555555555555",
        "applicability_decision_id": "66666666-6666-4666-8666-666666666666",
        "rights_approval_id": "77777777-7777-4777-8777-777777777777",
        "rights_version_hash": "1" * 64,
        "rights_class": "GREEN",
        "no_store_policy_version": "external-context-store-v1",
        "attribution_policy_version": "attribution-not-required-v1",
        "budget_hash": "2" * 64,
        "probe_hash": "3" * 64,
        "request_hash": "4" * 64,
        "provenance_hash": "5" * 64,
        "external_context_index_allowed": True,
        "outcome_hash_recipe_version": "enrichment-provider-outcome-sha256-v1",
        "native_output_recipe_version": "provider-outcome-native-output-v1",
    }

    def derive(source: dict[str, object]) -> tuple[str, str, str]:
        outcome_sha256 = _canonical_hash(
            [
                source[key]
                for key in (
                    "outcome_hash_recipe_version",
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "provider_outcome_id",
                    "provider_id",
                    "outcome",
                    "identity_output_object_id",
                    "applicability_decision_id",
                    "rights_approval_id",
                    "rights_version_hash",
                    "rights_class",
                    "no_store_policy_version",
                    "attribution_policy_version",
                    "budget_hash",
                    "probe_hash",
                    "request_hash",
                    "provenance_hash",
                    "external_context_index_allowed",
                    "outcome_hash_recipe_version",
                )
            ]
        )
        native_output_sha256 = _canonical_hash(
            [
                source[key]
                for key in (
                    "native_output_recipe_version",
                    "tenant_id",
                    "deal_id",
                    "run_id",
                    "provider_outcome_id",
                    "provider_id",
                    "outcome",
                    "identity_output_object_id",
                    "applicability_decision_id",
                )
            ]
            + [outcome_sha256, source["native_output_recipe_version"]]
        )
        output_recipe = next(
            row
            for row in artifact["uuid_namespace_registry"]["rows"]
            if row[0] == "output_object_id"
        )
        assert output_recipe[2][-1] == "native_output_sha256"
        output_object_id = str(
            uuid5(
                UUID(output_recipe[1]),
                _canonical_json(
                    [
                        "run-output-object-v1",
                        source["tenant_id"],
                        source["deal_id"],
                        source["run_id"],
                        "full-strict-v6",
                        "6" * 64,
                        "NATIVE_ROW",
                        "ENRICHMENT_PROVIDER_OUTCOME",
                        "7" * 64,
                        native_output_sha256,
                    ]
                ),
            )
        )
        return outcome_sha256, native_output_sha256, output_object_id

    first = derive(values)
    assert derive(dict(values)) == first
    changed = dict(values)
    changed["no_store_policy_version"] = "external-context-no-store-v1"
    changed["external_context_index_allowed"] = False
    second = derive(changed)
    assert all(left != right for left, right in zip(first, second, strict=True))


def test_v5_rights_registry_is_complete_and_hash_bound_in_the_preimage() -> None:
    artifact = _load(_V5_ARTIFACT)
    rows = artifact["rights_index_policy_registry"]["rows"]
    provider_ids = [row[0] for row in artifact["provider_registry"]["rows"]]

    assert len(rows) == 18
    assert [row[0] for row in rows] == list(range(18))
    assert [row[1] for row in rows] == provider_ids
    assert [row[-1] for row in rows] == [True] * 17 + [False]
    normalized_rows = _rights_index_policy_rows(artifact)
    assert _rights_index_policy_sha256(normalized_rows) == (
        _EXPECTED_RIGHTS_INDEX_POLICY_REGISTRY_SHA256
    )
    assert artifact["rights_index_policy_registry"]["expected_sha256"] == (
        _EXPECTED_RIGHTS_INDEX_POLICY_REGISTRY_SHA256
    )


def test_v4_materializer_refuses_to_overwrite_append_frozen_output(tmp_path: Path) -> None:
    output = tmp_path / "v4.json"
    command = [sys.executable, str(_V4_MATERIALIZER), "--output", str(output)]
    first = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()

    second = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 2
    assert "TASK7_V4_OUTPUT_ALREADY_EXISTS" in second.stderr
    assert output.read_bytes() == first_bytes


def test_v5_fixture_generator_refuses_to_overwrite_output(tmp_path: Path) -> None:
    output = tmp_path / "fixture.json"
    command = [
        sys.executable,
        str(_V5_GENERATOR),
        "--artifact",
        str(_V5_ARTIFACT),
        "--output",
        str(output),
    ]
    first = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()

    second = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 2
    assert "TASK7_V6_FIXTURE_OUTPUT_ALREADY_EXISTS" in second.stderr
    assert output.read_bytes() == first_bytes


@pytest.mark.parametrize("void_hash", _PRE_V6_VOID_HASHES)
def test_historical_v6_bytes_never_reused_an_earlier_void_hash(
    void_hash: bytes,
) -> None:
    assert void_hash not in _V5_ARTIFACT.read_bytes()
    assert void_hash not in historical_v6_fixture_bytes(_REPO_ROOT)
