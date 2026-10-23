"""RED-first admission contracts for the bounded full-strict-v16 successor."""

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
_V15_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v15_reviewed_preimage_v1.json"
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V16_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v16_preimage.py"
_V16_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v16_contract_fixture.py"
_V16_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_PLAN = _REPO_ROOT / "docs" / "plans" / ("2026-07-15-slice100-strict-real-example-live-dry-run.md")
_DESIGN = (
    _REPO_ROOT / "docs" / "plans" / ("2026-07-21-slice100-task7-receipt-edge-schema-design.md")
)
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)

_VERSION_COORDINATES = (
    (19, "current-receipt-equality-v1", 2, 0, "full-strict-v16"),
    (26, "current-task5-coverage-replacement-v1", 0, 0, "full-strict-v16"),
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


def _v16_candidate_raw(generator: ModuleType) -> bytes:
    repository_raw = _V16_ARTIFACT.read_bytes()
    repository_artifact = generator._decode_artifact(repository_raw)
    return (
        generator._candidate_from_frozen(repository_raw)
        if repository_artifact["metadata"]["append_frozen"] is True
        else repository_raw
    )


def _authority(artifact: dict[str, Any], ordinal: int) -> dict[str, Any]:
    row = artifact["sql_authority_registry"]["rows"][ordinal]
    assert row["authority_ordinal"] == ordinal
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
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    assert needle in statement
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = (
        statement.replace(
            needle,
            replacement,
            1,
        )
        .encode()
        .hex()
    )
    return mutated


def test_v15_candidate_identifiers_are_permanently_blocklisted_under_v16() -> None:
    identifiers = {
        getattr(void_evidence, "VOID_V15_ARTIFACT_SHA256", None),
        getattr(void_evidence, "VOID_V15_PROSPECTIVE_FROZEN_ARTIFACT_SHA256", None),
        getattr(void_evidence, "VOID_V15_PROSPECTIVE_CONTRACT_SHA256", None),
    }
    assert None not in identifiers
    assert identifiers.issubset(set(void_evidence.VOID_CONTRACT_HASHES))


def test_v16_assets_are_append_frozen_versioned_files() -> None:
    for path in (_V16_ARTIFACT, _V16_MATERIALIZER, _V16_GENERATOR):
        assert path.is_file(), path
    artifact = _load_json(_V16_ARTIFACT)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v16-reviewed-preimage-v1",
        "append_frozen": True,
        "pipeline_contract_version": "full-strict-v16",
    }
    fixture = _load_json(_V16_FIXTURE)
    assert fixture["artifact_sha256"] == hashlib.sha256(_V16_ARTIFACT.read_bytes()).hexdigest()
    assert (
        fixture["FULL_STRICT_V16_CONTRACT_HASH"]
        == "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
    )
    assert {
        key: fixture["postgres_authority_census"][key]
        for key in (
            "application_case_execution_count",
            "admin_case_execution_count",
            "legal_execution_count",
            "illegal_rejection_count",
            "direct_sql_rejection_count",
            "constructor_schema_proof_count",
            "unexpected_outcome_count",
        )
    } == {
        "application_case_execution_count": 219,
        "admin_case_execution_count": 0,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_schema_proof_count": 4,
        "unexpected_outcome_count": 0,
    }


def test_v16_exact_successor_changes_only_versions_and_derived_acl_provenance() -> None:
    materializer = _load_module(_V16_MATERIALIZER, "_task7_v16_exact_diff_red")
    generator = _load_module(_V16_GENERATOR, "_task7_v16_exact_diff_generator")
    v15 = _load_json(_V15_ARTIFACT)
    v16 = generator._decode_artifact(_v16_candidate_raw(generator))

    assert materializer.project_v16_to_v15(v16) == v15
    assert materializer._render(materializer.build_v16(v15)) == _v16_candidate_raw(generator)
    assert materializer.validate_exact_acl_provenance_delta(v15, v16) is None
    assert v16["sql_authority_registry"]["guard_function_acl_registry"] == (
        materializer._build_acl_registry(v16)
    )
    changed_rows = {
        path[2]
        for path in materializer._changed_paths(v15, v16)
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_rows == {19, 26}
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v16[section] == v15[section], section


def test_v16_acl_semantics_are_invariant_while_body_provenance_is_current() -> None:
    materializer = _load_module(_V16_MATERIALIZER, "_task7_v16_acl_delta_red")
    v15 = _load_json(_V15_ARTIFACT)
    v16 = _load_json(_V16_ARTIFACT)
    old_acl = v15["sql_authority_registry"]["guard_function_acl_registry"]
    new_acl = v16["sql_authority_registry"]["guard_function_acl_registry"]

    assert materializer.acl_semantic_projection(new_acl) == (
        materializer.acl_semantic_projection(old_acl)
    )
    changed = materializer.acl_provenance_changed_paths(old_acl, new_acl)
    assert len(changed) == 25
    assert {path[:2] for path in changed if path[:1] == ("function_rows",)} == {
        ("function_rows", 20),
        ("function_rows", 29),
    }


def test_v16_raw_and_decoded_version_inventories_are_exact_and_equal() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_version_inventory_red")
    artifact = _load_json(_V16_ARTIFACT)
    inventory = generator.contract_version_reference_inventory(artifact)

    assert (
        tuple(
            (
                row["authority_ordinal"],
                row["authority_id"],
                row["statement_ordinal"],
                row["occurrence_ordinal"],
                row["pipeline_contract_version"],
            )
            for row in inventory["decoded_literal_rows"]
        )
        == _VERSION_COORDINATES
    )
    assert (
        tuple(
            (
                row["authority_ordinal"],
                row["authority_id"],
                row["statement_ordinal"],
                row["occurrence_ordinal"],
                row["pipeline_contract_version"],
            )
            for row in inventory["raw_rows"]
        )
        == _VERSION_COORDINATES
    )
    generator.validate_contract_version_reference_authority(artifact)


def test_v16_production_function_body_census_covers_multi_function_statements() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_function_census_red")
    artifact = _load_json(_V16_ARTIFACT)
    census = generator.executable_function_body_census(artifact)

    assert census["statement_count"] == 29
    assert census["function_body_count"] == 32
    assert census["multi_function_statement_rows"] == (
        (21, "current-append-only-lease-v1", 0, 2),
        (22, "current-source-bound-v1", 0, 2),
        (27, "current-task6-immutable-replacement-v1", 0, 2),
    )


@pytest.mark.parametrize(
    ("authority_ordinal", "statement_ordinal", "needle", "replacement"),
    (
        (19, 2, "'full-strict-v16'", "'full-strict-v15'"),
        (26, 0, "'full-strict-v16'", "'other-contract'"),
        (
            19,
            2,
            "'full-strict-v16'",
            "'full-strict-v16' OR r.pipeline_contract_version='full-strict-v16'",
        ),
        (
            26,
            0,
            "'full-strict-v16'",
            "('full-strict-'||'v16')",
        ),
        (
            19,
            2,
            "r.pipeline_contract_version='full-strict-v16'",
            "shadow.pipeline_contract_version='full-strict-v16'",
        ),
    ),
)
def test_v16_version_reference_mutations_fail_closed(
    authority_ordinal: int,
    statement_ordinal: int,
    needle: str,
    replacement: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_version_mutation_red")
    artifact = _load_json(_V16_ARTIFACT)
    mutated = _mutate_statement(
        artifact,
        authority_ordinal=authority_ordinal,
        statement_ordinal=statement_ordinal,
        needle=needle,
        replacement=replacement,
    )

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(mutated)


@pytest.mark.parametrize(
    ("smuggling_kind", "smuggling"),
    (
        ("line_comment", " -- full-strict-v16 decoy\n"),
        ("block_comment", " /* full-strict-v16 decoy */"),
        ("dollar_string", " PERFORM $tag$full-strict-v16$tag$;"),
        ("unbound_literal", " PERFORM 'full-strict-v16';"),
    ),
)
def test_v16_extra_raw_or_decoded_version_token_cannot_be_smuggled(
    smuggling_kind: str,
    smuggling: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_smuggling_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, 19)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][2]).decode("utf-8")
    assert statement.count("r.pipeline_contract_version='full-strict-v16'") == 1
    other = bytes.fromhex(_authority(artifact, 26)["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    assert other.count("r.pipeline_contract_version='full-strict-v16'") == 1
    statement = statement.replace("END$$", f"{smuggling} END$$", 1)
    row["body_sql_utf8_hex_tuple"][2] = statement.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize(
    ("authority_ordinal", "statement_ordinal", "dollar_tag"),
    (
        (19, 2, "$tag$"),
        (19, 2, "$predicate$"),
        (26, 0, "$tag$"),
        (26, 0, "$predicate$"),
    ),
)
def test_v16_nested_dollar_string_cannot_impersonate_executable_predicate(
    authority_ordinal: int,
    statement_ordinal: int,
    dollar_tag: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_nested_dollar_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, authority_ordinal)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    predicate = "r.pipeline_contract_version='full-strict-v16'"
    assert statement.count(predicate) == 1
    statement = statement.replace(predicate, "TRUE", 1)
    decoy = f" PERFORM {dollar_tag}{predicate}{dollar_tag};"
    statement = statement.replace("END$$", f"{decoy} END$$", 1)
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = statement.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize("authority_ordinal,statement_ordinal", ((19, 2), (26, 0)))
@pytest.mark.parametrize(
    ("decoy_kind", "decoy"),
    (
        ("line_comment", " -- r.pipeline_contract_version='full-strict-v16'\n"),
        ("block_comment", " /* r.pipeline_contract_version='full-strict-v16' */"),
        ("ordinary_literal", " PERFORM 'full-strict-v16';"),
    ),
)
def test_v16_non_executable_text_cannot_replace_required_predicate(
    authority_ordinal: int,
    statement_ordinal: int,
    decoy_kind: str,
    decoy: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_non_executable_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, authority_ordinal)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    predicate = "r.pipeline_contract_version='full-strict-v16'"
    assert statement.count(predicate) == 1, decoy_kind
    statement = statement.replace(predicate, "TRUE", 1)
    statement = statement.replace("END$$", f"{decoy} END$$", 1)
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = statement.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize(
    "decoy_kind",
    (
        "line_comment_outer_decoy",
        "block_comment_outer_decoy",
        "ordinary_string_outer_decoy",
        "quoted_identifier_outer_decoy",
        "non_as_dollar_data_outer_decoy",
    ),
)
def test_v16_outer_function_delimiter_discovery_ignores_quoted_decoys(
    decoy_kind: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_outer_delimiter_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, 19)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][2]).decode("utf-8")
    predicate = "r.pipeline_contract_version='full-strict-v16'"
    assert statement.count(predicate) == 1
    statement = statement.replace(predicate, "TRUE", 1)
    if decoy_kind == "line_comment_outer_decoy":
        statement = statement.replace(
            " AS $$",
            f" -- AS $tag${predicate}$tag$\n AS $$",
            1,
        )
    elif decoy_kind == "block_comment_outer_decoy":
        statement = statement.replace(
            " AS $$",
            f" /* AS $tag${predicate}$tag$ */ AS $$",
            1,
        )
    elif decoy_kind == "ordinary_string_outer_decoy":
        ordinary_string = "'AS $tag$r.pipeline_contract_version=''full-strict-v16''$tag$'"
        statement = statement.replace(
            "() RETURNS",
            f"(p_decoy text DEFAULT {ordinary_string}) RETURNS",
            1,
        )
    elif decoy_kind == "quoted_identifier_outer_decoy":
        statement = statement.replace(
            "() RETURNS",
            f'(p_decoy text DEFAULT "AS $tag${predicate}$tag$") RETURNS',
            1,
        )
    else:
        statement = statement.replace(
            "() RETURNS",
            (f"(p_decoy text DEFAULT $data$AS $tag${predicate}$tag$$data$) RETURNS"),
            1,
        )
    row["body_sql_utf8_hex_tuple"][2] = statement.encode().hex()

    _executable_sql, body_count = generator._executable_sql_text_and_body_count(statement)
    assert body_count == 1
    tokens = generator._executable_sql_tokens(statement)
    predicate_tokens = (
        ("IDENT", "r"),
        ("SYMBOL", "."),
        ("IDENT", "pipeline_contract_version"),
        ("SYMBOL", "="),
        ("STRING", "full-strict-v16"),
    )
    assert all(
        tokens[ordinal : ordinal + len(predicate_tokens)] != predicate_tokens
        for ordinal in range(len(tokens) - len(predicate_tokens) + 1)
    )
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize("authority_ordinal,statement_ordinal", ((19, 2), (26, 0)))
@pytest.mark.parametrize(
    "smuggling_kind",
    ("external_select", "comment_fake_outer", "ordinary_string_fake_outer"),
)
def test_v16_predicate_must_be_inside_exact_governed_function_body(
    authority_ordinal: int,
    statement_ordinal: int,
    smuggling_kind: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_body_binding_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, authority_ordinal)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    predicate = "r.pipeline_contract_version='full-strict-v16'"
    assert statement.count(predicate) == 1
    statement = statement.replace(predicate, "TRUE", 1)
    if smuggling_kind == "external_select":
        statement = f"{statement}; SELECT {predicate}"
    elif smuggling_kind == "comment_fake_outer":
        statement = f"-- fake AS $tag$\n{statement}$tag$; SELECT {predicate}"
    else:
        statement = f"SELECT 'fake AS $tag$'; {statement}$tag$; SELECT {predicate}"
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = statement.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize(
    ("authority_ordinal", "statement_ordinal", "function_name"),
    (
        (19, 2, "public.idis_task7_current_receipt_equality_v1"),
        (26, 0, "public.idis_task5_coverage_scope_guard"),
    ),
)
def test_v16_predicate_authority_is_bound_to_exact_function_identity(
    authority_ordinal: int,
    statement_ordinal: int,
    function_name: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_function_identity_red")
    artifact = _load_json(_V16_ARTIFACT)
    row = _authority(artifact, authority_ordinal)
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][statement_ordinal]).decode("utf-8")
    assert statement.count(function_name) == 1
    statement = statement.replace(function_name, f"{function_name}_shadow", 1)
    row["body_sql_utf8_hex_tuple"][statement_ordinal] = statement.encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


@pytest.mark.parametrize(
    "invalid_hex",
    (
        "0",
        "zz",
        "ff",
    ),
)
def test_v16_version_inventory_rejects_malformed_structural_hex(
    invalid_hex: str,
) -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_bad_hex_red")
    artifact = _load_json(_V16_ARTIFACT)
    _authority(artifact, 19)["body_sql_utf8_hex_tuple"][2] = invalid_hex

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_CONTRACT_VERSION_REFERENCE_INVALID",
    ):
        generator.validate_contract_version_reference_authority(artifact)


def _rehash_acl_and_sql_registry(
    materializer: ModuleType,
    artifact: dict[str, Any],
    *,
    function_ordinal: int | None,
) -> None:
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    if function_ordinal is not None:
        row = acl["function_rows"][function_ordinal]
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        row["expected_sha256"] = hashlib.sha256(
            b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
            + materializer._canonical_bytes(payload)
        ).hexdigest()
    acl["expected_sha256"] = materializer._h(
        [
            "full-strict-v12-guard-function-acl-registry-v1",
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
    sql = artifact["sql_authority_registry"]
    sql["expected_sha256"] = materializer._h(
        [
            "full-strict-v16-sql-authority-registry-v1",
            sql["rows"],
            acl,
        ]
    )


def test_v16_rejects_stale_or_corrupt_acl_body_provenance() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_acl_provenance_red")
    materializer = _load_module(_V16_MATERIALIZER, "_task7_v16_acl_rehash_red")
    v15 = _load_json(_V15_ARTIFACT)
    artifact = _load_json(_V16_ARTIFACT)

    stale = copy.deepcopy(artifact)
    stale["sql_authority_registry"]["guard_function_acl_registry"] = copy.deepcopy(
        v15["sql_authority_registry"]["guard_function_acl_registry"]
    )
    _rehash_acl_and_sql_registry(materializer, stale, function_ordinal=None)
    corrupt = copy.deepcopy(artifact)
    corrupt["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"][20][
        "direct_read_column_tuple"
    ][0][2][1] = "0" * 64
    _rehash_acl_and_sql_registry(materializer, corrupt, function_ordinal=20)
    for candidate in (stale, corrupt):
        with pytest.raises(
            generator.ContractArtifactError,
            match="TASK7_V16_ACL_PROVENANCE_INVALID",
        ):
            generator.validate_acl_provenance(candidate)


def test_v16_rolls_forward_global_bare_new_guard() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_bare_new_red")
    artifact = _load_json(_V16_ARTIFACT)

    generator.validate_before_generated_column_access(artifact)
    assert generator._invalid_bare_new_contexts(
        generator._V15._V14._V13._sql_tokens("IF NEW IS DISTINCT FROM OLD THEN RETURN NEW; END IF")
    )


def test_v16_contract_derivation_rejects_unfrozen_candidate_bytes() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_unfrozen_derivation_red")

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V16_APPEND_FREEZE_REQUIRED",
    ):
        generator.derive_contract_bytes(_v16_candidate_raw(generator))


def test_v16_contract_derivation_exposes_no_validation_bypass() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_validation_bypass_red")

    with pytest.raises(TypeError):
        generator.derive_contract_bytes(
            _v16_candidate_raw(generator),
            validate=False,
        )


def test_v16_prospective_derivation_succeeds_and_corruption_fails_closed() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_derivation_red")
    candidate_raw = _v16_candidate_raw(generator)
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    fixture = generator.derive_contract_bytes(frozen_raw)
    frozen = generator._decode_artifact(frozen_raw)

    assert fixture["fixture_schema"] == "full-strict-v16-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v16"
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen_raw).hexdigest()
    assert (
        fixture["component_hashes"]["sql_authority_registry_hash"]
        == (frozen["sql_authority_registry"]["expected_sha256"])
    )
    assert (
        fixture["FULL_STRICT_V16_CONTRACT_HASH"]
        == (fixture["component_hashes"]["pipeline_contract_hash"])
    )

    corrupted = copy.deepcopy(frozen)
    corrupted["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"][29][
        "transitive_read_column_tuple"
    ][1][2][0][1] = "f" * 64
    with pytest.raises(generator.ContractArtifactError):
        generator.derive_contract_bytes(generator._render_artifact(corrupted))


def test_v16_semantic_vectors_and_frozen_state_are_invariant() -> None:
    generator = _load_module(_V16_GENERATOR, "_task7_v16_vectors_red")
    v15 = _load_json(_V15_ARTIFACT)
    v16_raw = _V16_ARTIFACT.read_bytes()
    v16 = generator._decode_artifact(v16_raw)

    generator._validate_shape(v16_raw, v16)
    assert generator.invariant_vector(v16) == generator.invariant_vector(v15)
    assert v16["metadata"]["append_frozen"] is True
    assert _V16_FIXTURE.is_file()


def test_v16_approval_and_v15_void_are_recorded() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    design = _DESIGN.read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in _RECONCILIATION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "full-strict-v16 ACL-provenance closure" in plan
    assert "full-strict-v16 ACL-provenance closure" in design
    record = next(
        row
        for row in records
        if row["event"] == "V15_PRE_FREEZE_STALE_ACL_PROVENANCE_CANDIDATE_VOIDED"
    )
    assert record["artifact_sha256"] == void_evidence.VOID_V15_ARTIFACT_SHA256
    assert record["superseding_contract_version"] == "full-strict-v16"
    assert record["candidate_voided"] is True
    assert record["never_frozen"] is True
    assert record["never_consumed"] is True
