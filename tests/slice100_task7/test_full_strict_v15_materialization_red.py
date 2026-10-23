"""RED-first admission contracts for the bounded full-strict-v15 successor."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V14_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v14_reviewed_preimage_v1.json"
_V15_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v15_reviewed_preimage_v1.json"
_V15_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v15_preimage.py"
_V15_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v15_contract_fixture.py"
_V15_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v15_contract_hash_v1.json"
)
_PLAN = _REPO_ROOT / "docs" / "plans" / ("2026-07-15-slice100-strict-real-example-live-dry-run.md")
_DESIGN = (
    _REPO_ROOT / "docs" / "plans" / ("2026-07-21-slice100-task7-receipt-edge-schema-design.md")
)
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)

_VERSION_AUTHORITY_COORDINATES = (
    {
        "authority_ordinal": 19,
        "authority_id": "current-receipt-equality-v1",
        "statement_ordinal": 2,
        "occurrence_ordinal": 0,
        "pipeline_contract_version": "full-strict-v15",
    },
    {
        "authority_ordinal": 26,
        "authority_id": "current-task5-coverage-replacement-v1",
        "statement_ordinal": 0,
        "occurrence_ordinal": 0,
        "pipeline_contract_version": "full-strict-v15",
    },
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


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _authority(
    artifact: dict[str, Any],
    authority_ordinal: int,
) -> dict[str, Any]:
    row = artifact["sql_authority_registry"]["rows"][authority_ordinal]
    assert row["authority_ordinal"] == authority_ordinal
    return row


def _mutate_statement(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    statement_ordinal: int,
    needle: str,
    replacement: str,
) -> dict[str, Any]:
    mutated = copy.deepcopy(artifact)
    row = _authority(mutated, authority_ordinal)
    body = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    assert needle in body
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = (
        body.replace(
            needle,
            replacement,
            1,
        )
        .encode()
        .hex()
    )
    return mutated


def test_v14_candidate_identifiers_are_permanently_blocklisted_under_v15() -> None:
    identifiers = {
        getattr(void_evidence, "VOID_V14_ARTIFACT_SHA256", None),
        getattr(void_evidence, "VOID_V14_PROSPECTIVE_FROZEN_ARTIFACT_SHA256", None),
        getattr(void_evidence, "VOID_V14_PROSPECTIVE_CONTRACT_SHA256", None),
    }
    assert None not in identifiers
    assert identifiers.issubset(set(void_evidence.VOID_CONTRACT_HASHES))


def test_v15_assets_are_new_unfrozen_versioned_files() -> None:
    for path in (_V15_ARTIFACT, _V15_MATERIALIZER, _V15_GENERATOR):
        assert path.is_file(), path
    artifact = _load_json(_V15_ARTIFACT)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v15-reviewed-preimage-v1",
        "append_frozen": False,
        "pipeline_contract_version": "full-strict-v15",
    }
    assert not _V15_FIXTURE.exists()


def test_v15_exact_successor_changes_only_two_version_guards_and_dependent_hashes() -> None:
    materializer = _load_module(_V15_MATERIALIZER, "_task7_v15_exact_diff_red")
    v14 = _load_json(_V14_ARTIFACT)
    v15 = _load_json(_V15_ARTIFACT)

    assert materializer.project_v15_to_v14(v15) == v14
    assert materializer._render(materializer.build_v15(v14)) == _V15_ARTIFACT.read_bytes()
    changed = materializer._changed_paths(v14, v15)
    assert changed
    assert all(
        path[:1] in {("metadata",), ("digest_recipe_registry",), ("sql_authority_registry",)}
        for path in changed
    )
    changed_authorities = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authorities == {19, 26}
    assert (
        v15["sql_authority_registry"]["guard_function_acl_registry"]
        == (v14["sql_authority_registry"]["guard_function_acl_registry"])
    )
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v15[section] == v14[section], section


def test_v15_contract_version_references_have_exact_governed_coordinates() -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_version_coordinates_red")
    artifact = _load_json(_V15_ARTIFACT)

    assert generator.contract_version_reference_authority(artifact) == (
        _VERSION_AUTHORITY_COORDINATES
    )
    generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize(
    ("authority_ordinal", "statement_ordinal", "needle", "replacement"),
    (
        (19, 2, "'full-strict-v15'", "'full-strict-v14'"),
        (26, 0, "'full-strict-v15'", "'full-strict-v12'"),
        (19, 2, "'full-strict-v15'", "'other-contract'"),
        (
            19,
            2,
            "'full-strict-v15'",
            "'full-strict-v15' OR r.pipeline_contract_version='full-strict-v15'",
        ),
        (
            26,
            0,
            "'full-strict-v15'",
            "('full-strict-'||'v15')",
        ),
        (
            19,
            2,
            "END$$",
            "PERFORM 'full-strict-v15'; END$$",
        ),
        (
            26,
            0,
            "r.pipeline_contract_version='full-strict-v15'",
            "r.pipeline_contract_version=pg_catalog.current_setting("
            "'idis.pipeline_contract_version',true)",
        ),
        (
            19,
            2,
            "r.pipeline_contract_version='full-strict-v15'",
            "shadow.pipeline_contract_version='full-strict-v15'",
        ),
    ),
)
def test_v15_admission_rejects_stale_missing_duplicate_dynamic_or_unbound_versions(
    authority_ordinal: int,
    statement_ordinal: int,
    needle: str,
    replacement: str,
) -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_version_mutation_red")
    artifact = _load_json(_V15_ARTIFACT)
    mutated = _mutate_statement(
        artifact,
        authority_ordinal=authority_ordinal,
        statement_ordinal=statement_ordinal,
        needle=needle,
        replacement=replacement,
    )

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(mutated)


def test_v15_dynamic_binding_cannot_be_smuggled_by_an_unbound_literal() -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_version_smuggling_red")
    artifact = _load_json(_V15_ARTIFACT)
    row = _authority(artifact, 19)
    body = bytes.fromhex(row["body_sql_utf8_hex_tuple"][2]).decode("utf-8")
    body = body.replace(
        "r.pipeline_contract_version='full-strict-v15'",
        "r.pipeline_contract_version=pg_catalog.current_setting("
        "'idis.pipeline_contract_version',true)",
        1,
    )
    body = body.replace("END$$", "PERFORM 'full-strict-v15'; END$$", 1)
    row["body_sql_utf8_hex_tuple"][2] = body.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V15_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


def test_v15_global_bare_new_allowlist_is_rolled_forward() -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_bare_new_rollforward_red")
    artifact = _load_json(_V15_ARTIFACT)

    generator.validate_before_generated_column_access(artifact)
    assert generator._invalid_bare_new_contexts(
        generator._V14._V13._sql_tokens("IF NEW IS DISTINCT FROM OLD THEN RETURN NEW; END IF")
    )


def test_v15_prospective_derivation_succeeds_and_corruption_fails_closed() -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_derivation_red")
    candidate_raw = _V15_ARTIFACT.read_bytes()
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    fixture = generator.derive_contract_bytes(frozen_raw, validate=True)
    frozen = generator._decode_artifact(frozen_raw)

    assert fixture["fixture_schema"] == "full-strict-v15-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v15"
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen_raw).hexdigest()
    assert (
        fixture["component_hashes"]["sql_authority_registry_hash"]
        == (frozen["sql_authority_registry"]["expected_sha256"])
    )
    assert (
        fixture["FULL_STRICT_V15_CONTRACT_HASH"]
        == (fixture["component_hashes"]["pipeline_contract_hash"])
    )

    corrupted = _mutate_statement(
        frozen,
        authority_ordinal=19,
        statement_ordinal=2,
        needle="'full-strict-v15'",
        replacement="'full-strict-v14'",
    )
    with pytest.raises(generator.ContractArtifactError):
        generator.derive_contract_bytes(
            generator._render_artifact(corrupted),
            validate=True,
        )


def test_v15_semantic_vectors_and_candidate_state_are_invariant() -> None:
    generator = _load_module(_V15_GENERATOR, "_task7_v15_vectors_red")
    v14 = _load_json(_V14_ARTIFACT)
    v15_raw = _V15_ARTIFACT.read_bytes()
    v15 = generator._decode_artifact(v15_raw)

    generator._validate_shape(v15_raw, v15)
    assert (
        generator.invariant_vector(v15)
        == generator.invariant_vector(v14)
        == {
            "semantic_step_count": 28,
            "edge_rule_count": 48,
            "usage_route_count": 1756,
            "usage_extractor_count": 19,
            "provider_count": 18,
            "current_ddl_count": 34,
            "materialized_physical_table_count": 72,
            "erasure_phase_count": 66,
        }
    )
    assert v15["metadata"]["append_frozen"] is False
    assert not _V15_FIXTURE.exists()


def test_v15_approval_and_v14_void_are_recorded() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    design = _DESIGN.read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in _RECONCILIATION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "full-strict-v15 contract-version reference closure" in plan
    assert "full-strict-v15 contract-version reference closure" in design
    record = next(
        row
        for row in records
        if row["event"] == "V14_PRE_FREEZE_CONTRACT_VERSION_LITERAL_CANDIDATE_VOIDED"
    )
    assert record["artifact_sha256"] == void_evidence.VOID_V14_ARTIFACT_SHA256
    assert record["disposition"] == "PRE_FREEZE_VOID_NEVER_ADMITTED"
    assert record["superseding_contract_version"] == "full-strict-v15"
    assert record["candidate_voided"] is True
    assert record["never_frozen"] is True
    assert record["never_consumed"] is True
