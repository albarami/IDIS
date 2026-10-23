"""RED-first boundary for the permanently void full-strict-v26 candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests._private_artifacts import requires_private_artifact
from tests.slice100_task8 import _task8_void_contract_hash_evidence as void_evidence
from tests.slice100_task8 import conftest as task8_conftest
from tests.slice100_task8.test_task8_historical_candidate_collection_boundary import (
    _write_successor_pair as _write_complete_successor_pair,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK8_ROOT = _REPO_ROOT / "tests" / "slice100_task8"
_STAGED_ROOT = _REPO_ROOT / ".local_reports" / "slice100" / "task8" / "v26-candidate"
_STAGED_ARTIFACT = _STAGED_ROOT / "full_strict_v26_reviewed_preimage_v1.json"
_STAGED_FIXTURE = _STAGED_ROOT / "full_strict_v26_contract_hash_v1.json"
_CANONICAL_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
_CANONICAL_FIXTURE = _TASK8_ROOT / "fixtures" / "full_strict_v26_contract_hash_v1.json"
_SIGNED_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "task8_v26_postgres_census_signed_receipt.json"
)
_RECONCILIATION_LOG = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task8" / "task8_contract_reconciliation_log.jsonl"
)

_V26_CONSTANT_NAMES = (
    "VOID_V26_ARTIFACT_SHA256",
    "VOID_V26_FIXTURE_SHA256",
    "VOID_V26_CANDIDATE_CONTRACT_SHA256",
    "VOID_V26_TASK8_CONTRACT_SHA256",
    "VOID_V26_STRUCTURAL_ADMISSION_SHA256",
    "VOID_V26_PRODUCER_MANIFEST_SHA256",
    "VOID_V26_EXTERNAL_MANIFEST_SHA256",
    "VOID_V26_IDENTITY_GRAPH_SHA256",
    "VOID_V26_CONSERVATION_SHA256",
    "VOID_V26_POLICY_BINDING_SHA256",
    "VOID_V26_REQUIREMENT_LITERAL_SHA256",
    "VOID_V26_EQUATION_AUTHORITY_SHA256",
    "VOID_V26_EQUATION_BODY_SHA256",
)
_V26_IDENTIFIERS = (
    "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27",
    "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7",
    "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc",
    "cab23cdd8b6df52cd4abce81e4d36c90e44771f91341dfe16d7f6123b310d233",
    "e8c92fbd053a173db56f14aad4bc1a185fb69e7ef779c81daa883db49a69a3fa",
    "2cd98224e13fedb8163115eb8b36f0bbcd89a8d2349e2862dfc2a1ce51799bad",
    "73184db5100e400407713e370795956bfdd7fd51c34ca745ee486d925116af25",
    "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece",
    "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235",
    "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467",
    "2dfe78afe688ff1ed5380556183fa13a54879740d40f127d72f99a131b7350a1",
    "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5",
    "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7",
)
_V26_CANDIDATE_IDENTIFIERS = _V26_IDENTIFIERS[:-2]
_V26_INHERITED_SEMANTIC_IDENTIFIERS = _V26_IDENTIFIERS[-2:]
_V26_FROZEN_FILE_SHA256_BY_PATH = {
    "scripts/materialize_full_strict_v26_preimage.py": (
        "1d70cb380ff2cefcec8b096523731b2143f9dc9cfa21810fff41619d6db0ff00"
    ),
    "scripts/verify_full_strict_v26_fixture_independently.py": (
        "6bd55cdf12b0e879d3fc5ea3c79324ba2122be8f8cc4304631d5f28d6c9e9fd6"
    ),
    "scripts/generate_task8_contract_fixtures.py": (
        "71157e79df2cbddaba217726657076394382bd7f7865455c8c21e4bd8a445b06"
    ),
    "scripts/run_task8_v26_sql_authority_census.py": (
        "e3cb7f6ee07f42a845280e8e0e2599b899d303d8ba4329e1b113f8e212217a18"
    ),
    "scripts/run_task8_v26_signed_census.py": (
        "899a93d91566605755a2dca0e99e674b4ded62f96cdbb97973876683fa4751f4"
    ),
    "scripts/publish_task8_v26_contract_artifact_gate.py": (
        "928f09a5c8f6886b2c3b15f9296c7ca41135aef43026bebbc21957cdaac01ee8"
    ),
    "tests/slice100_task8/_v26_oracle.py": (
        "7ee95be761484571770a4bbe0ae54a87c96bc0d28de59ac5f1f10e7733b47225"
    ),
    "tests/slice100_task8/_v26_independent_oracle.py": (
        "be43d068e04847bd3546e05707694478a968487ce51a79a251431cf42258db75"
    ),
    "tests/slice100_task8/fixtures/full_strict_v26_independent_golden_v1.json": (
        "e87f9df899b8457b5d1017f3f9f4c19c9824d27b3a48f89475b5477ba8da6d43"
    ),
    "tests/slice100_task8/test_full_strict_v26_materialization_red.py": (
        "d1b1cebe06b0b96a38dceb06a16c182f7c1882e4618ad742081aa98654107a7c"
    ),
    "tests/slice100_task8/test_v26_bound_runtime_graph_red.py": (
        "fb7675178711f5c29cfa6d45545857cff925e3a6676e605e249105e6cc824ac2"
    ),
    "tests/slice100_task8/test_v26_independent_fixture_verification_red.py": (
        "b5cdf29ce81e631dc5f73787719505aabebc7d56a0a9704c5ee32d7230a49227"
    ),
    "tests/slice100_task8/test_task8_fixture_derivation_red.py": (
        "cff1d8276ef8f30bcf40c3d266f0f1e6bcff91c86b2cdbe29c3ac3f91b55a09f"
    ),
    "tests/slice100_task8/test_v26_sql_authority_postgres_census_red.py": (
        "19f5cfee9c10cdf47745c65a617f2a1e4567be769a71197f6029bea24e69d39a"
    ),
    "tests/slice100_task8/test_run_task8_v26_signed_census.py": (
        "8738bfa56bf7f339c577ab549d0136573f71752f1edaa99c576b802e158e1e13"
    ),
    "tests/slice100_task8/test_task8_v26_atomic_publication_red.py": (
        "d6d62c03f7e910b5d7593a0b3d9277cd43d2fc35cab3f29f7eebf46ef195eccb"
    ),
    ".local_reports/slice100/task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json": (
        "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27"
    ),
    ".local_reports/slice100/task8/v26-candidate/full_strict_v26_contract_hash_v1.json": (
        "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7"
    ),
}
_ARCHIVED_V26_MODULE_SHA256 = {
    Path(path).name: digest
    for path, digest in _V26_FROZEN_FILE_SHA256_BY_PATH.items()
    if path.startswith("tests/slice100_task8/test_")
}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rendered_v26_identifiers() -> tuple[str, ...]:
    artifact = _load_object(_STAGED_ARTIFACT)
    fixture = _load_object(_STAGED_FIXTURE)
    structural = artifact["task8_structural_admission"]
    graph_contract = structural["identity_hash_dependency_contract"]
    requirement = artifact["task8_fdd_contract"]["dashboard_contract"][
        "requirement_binding_contract"
    ]
    equation = next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    )
    statements = tuple(
        bytes.fromhex(value).decode("utf-8") for value in equation["body_sql_utf8_hex_tuple"]
    )
    create_statement = next(
        statement
        for statement in statements
        if statement.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v3()")
    )
    marker = " AS $$"
    marker_index = create_statement.find(marker)
    body_end = create_statement.rfind("$$")
    assert marker_index >= 0 and body_end > marker_index + len(marker)
    semantic_body = create_statement[marker_index + len(marker) : body_end]
    return (
        hashlib.sha256(_STAGED_ARTIFACT.read_bytes()).hexdigest(),
        hashlib.sha256(_STAGED_FIXTURE.read_bytes()).hexdigest(),
        fixture["FULL_STRICT_V26_CONTRACT_HASH"],
        artifact["task8_fdd_contract"]["expected_sha256"],
        structural["expected_sha256"],
        graph_contract["producer_authority_sha256"],
        graph_contract["allowed_external_input_sha256"],
        fixture["identity_hash_dependency_graph"]["graph_sha256"],
        fixture["approved_semantics_conservation"]["sha256"],
        fixture["policy_binding"]["binding_sha256"],
        requirement["policy_v2_requirement_literal_sha256"],
        equation["expected_sha256"],
        hashlib.sha256(semantic_body.encode("utf-8")).hexdigest(),
    )


def _records(event: str) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in _RECONCILIATION_LOG.read_text("utf-8").splitlines()]
    return [value for value in values if value.get("event") == event]


def _write_candidate_pair(root: Path, *, version: int) -> tuple[Path, Path]:
    artifact = root / f"full_strict_v{version}_reviewed_preimage_v1.json"
    artifact.write_text(
        json.dumps(
            {
                "metadata": {
                    "artifact_schema": f"full-strict-v{version}-reviewed-preimage-v1",
                    "pipeline_contract_version": f"full-strict-v{version}",
                    "append_frozen": True,
                    "activation_status": "INACTIVE",
                }
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    fixture = root / f"full_strict_v{version}_contract_hash_v1.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact_schema": f"full-strict-v{version}-contract-hash-fixture-v1",
                "pipeline_contract_version": f"full-strict-v{version}",
                "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "activation_status": "INACTIVE",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return artifact, fixture


@requires_private_artifact(
    "task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json",
    "task8/v26-candidate/full_strict_v26_contract_hash_v1.json",
)
def test_v26_rendered_candidate_identities_are_void_but_equation_v3_hashes_are_reusable() -> None:
    assert _rendered_v26_identifiers() == _V26_IDENTIFIERS
    assert tuple(getattr(void_evidence, name, None) for name in _V26_CONSTANT_NAMES) == (
        _V26_IDENTIFIERS
    )
    assert tuple(getattr(void_evidence, "VOID_V26_CANDIDATE_IDENTIFIERS", ())) == (
        _V26_CANDIDATE_IDENTIFIERS
    )
    assert tuple(getattr(void_evidence, "VOID_V26_INHERITED_SEMANTIC_IDENTIFIERS", ())) == (
        _V26_INHERITED_SEMANTIC_IDENTIFIERS
    )
    active_blocklist = set(void_evidence.TASK8_VOID_CONTRACT_HASHES)
    assert set(_V26_CANDIDATE_IDENTIFIERS) < active_blocklist
    assert not (set(_V26_INHERITED_SEMANTIC_IDENTIFIERS) & active_blocklist)


@requires_private_artifact(
    "task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json",
    "task8/v26-candidate/full_strict_v26_contract_hash_v1.json",
)
def test_v26_sources_execution_suites_and_staged_candidate_are_byte_frozen() -> None:
    recorded = dict(getattr(void_evidence, "VOID_V26_FROZEN_FILE_SHA256_BY_PATH", ()))
    assert recorded == _V26_FROZEN_FILE_SHA256_BY_PATH
    for relative, expected_sha256 in recorded.items():
        assert hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest() == expected_sha256


def test_v26_canonical_outputs_receipt_locks_runtime_and_migration_are_absent() -> None:
    assert not _CANONICAL_ARTIFACT.exists(), "TASK8_VOID_V26_CANONICAL_ARTIFACT_PRESENT"
    assert not _CANONICAL_FIXTURE.exists(), "TASK8_VOID_V26_CANONICAL_FIXTURE_PRESENT"
    assert not _SIGNED_RECEIPT.exists(), "TASK8_VOID_V26_SIGNED_CENSUS_RECEIPT_PRESENT"
    assert not _CANONICAL_ARTIFACT.with_name(
        f".{_CANONICAL_ARTIFACT.name}.task8-v26.lock"
    ).exists(), "TASK8_VOID_V26_PUBLICATION_LOCK_PRESENT"
    assert not _CANONICAL_ARTIFACT.with_name(
        f".{_CANONICAL_ARTIFACT.name}.task8-v26.journal.json"
    ).exists(), "TASK8_VOID_V26_PUBLICATION_JOURNAL_PRESENT"
    allowed_fixture_evidence = (
        _TASK8_ROOT / "fixtures" / "full_strict_v26_independent_golden_v1.json"
    )
    canonical_v26_files = {
        path.relative_to(_REPO_ROOT).as_posix()
        for root in (_REPO_ROOT / "contracts", _TASK8_ROOT / "fixtures")
        for path in root.rglob("*")
        if path.is_file() and "v26" in path.name.lower() and path != allowed_fixture_evidence
    }
    assert canonical_v26_files == set(), "TASK8_VOID_V26_PUBLICATION_SIDECAR_PRESENT"

    locked = set(_load_object(_REPO_ROOT / "contracts" / "contract_lock.json")["files"])
    assert _CANONICAL_ARTIFACT.relative_to(_REPO_ROOT).as_posix() not in locked
    assert _CANONICAL_FIXTURE.relative_to(_REPO_ROOT).as_posix() not in locked

    forbidden = tuple(value.encode("ascii") for value in _V26_CANDIDATE_IDENTIFIERS) + (
        b"full-strict-v26",
        b"full_strict_v26",
        b"task8-v26",
        b"task8_v26",
    )
    runtime_offenders = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_REPO_ROOT / "src" / "idis").rglob("*")
        if path.is_file() and any(marker in path.read_bytes() for marker in forbidden)
    }
    assert runtime_offenders == set(), "TASK8_VOID_V26_RUNTIME_SELECTOR_PRESENT"
    assert not list(
        (_REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions").glob("0039*.py")
    ), "TASK8_MIGRATION_0039_EXISTS_BEFORE_CHECKPOINT"


def test_v26_void_record_is_exact_mechanical_nonsemantic_and_advances_to_v27() -> None:
    matches = _records("V26_DEFECTIVE_RENDERED_CANDIDATE_PERMANENTLY_VOID")
    assert len(matches) == 1, "TASK8_VOID_V26_RECONCILIATION_RECORD_INVALID"
    record = matches[0]
    assert tuple(record["void_identifiers"].values()) == _V26_IDENTIFIERS
    assert record["standing_delegation_record"] == "T1-20260815-010"
    assert record["void_contract_version"] == "full-strict-v26"
    assert record["defect_code"] == "TASK8_V26_INSTALLED_RLS_COUNT_DOMAIN_CONFLATED"
    assert record["recorded_installed_rls_delta"] == [67, 69]
    assert record["required_installed_rls_delta"] == [64, 66]
    assert record["future_physical_census_preserved"] == 67
    assert record["semantic_vector_changed"] is False
    assert record["successor_contract_version"] == "full-strict-v27"
    assert record["successor_parent_version"] == "full-strict-v21"
    assert record["canonical_artifact_existed"] is False
    assert record["canonical_fixture_existed"] is False
    assert record["signed_census_receipt_existed"] is False
    assert record["postgres_census_run"] is False
    assert record["publication_run"] is False
    assert record["migration_0039_written"] is False
    assert record["activation_status"] == "NEVER_ACTIVATED"
    assert record["archive_match_mode"] == "EXACT_REPO_RELATIVE_MODULE_PATH"
    assert record["archive_path_comparison"] == "LEXICAL_ABSOLUTE_REPO_RELATIVE_NO_RESOLVE"
    assert record["archive_alias_resolution_allowed"] is False
    assert record["v27_successor_artifact_section_count"] == 29
    assert record["v27_successor_fixture_field_count"] == 10
    assert record["v27_successor_append_frozen_strict_boolean"] is True
    assert record["v27_successor_inactive_nonselectable_required"] is True
    assert record["v27_successor_artifact_sha256"] == (
        "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
    )
    assert record["v27_successor_fixture_sha256"] == (
        "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
    )
    assert record["v27_successor_fixture_size_bytes"] == 310_306
    assert record["v27_successor_fixture_identity_status"] == (
        "INDEPENDENTLY_DERIVED_EXACT_IDENTITY_ADMITTED_CANONICAL_ABSENT"
    )
    assert record["v27_exact_pair_positive_regression"] is True
    assert record["v27_synthetic_pair_admission"] is False
    assert record["v27_canonical_artifact_existed"] is False
    assert record["v27_canonical_fixture_existed"] is False
    assert record["v25_void_boundary_source_sha256_after"] == (
        "c9fac0a39122faaf86671970620fe981df6e05b920282b3c5c75aa16d25c8e53"
    )


def test_authority_narrowing_record_wires_pending_alembic_metadata_exclusion() -> None:
    matches = _records("TASK8_APPLICATION_AUTHORITY_ALEMBIC_METADATA_EXCLUDED")
    assert len(matches) == 1, "TASK8_T1_011_RECONCILIATION_RECORD_INVALID"
    record = matches[0]
    assert record["standing_delegation_record"] == "T1-20260815-011"
    assert record["precedent_tuple"] == [
        "T1-20260814-002_PRODUCTION_COMPOSITION_REWIRE",
        "T1-20260814-004_MINIMAL_EXECUTABILITY_NO_SUPERSET",
        "STANDING_DELEGATION_V2_EVIDENCE_STRICTENING",
    ]
    assert record["required_physical_public_ordinary_table_count"] == 67
    assert record["required_application_domain_table_count"] == 66
    assert record["observed_physical_public_ordinary_table_count"] is None
    assert record["observed_application_domain_table_count"] is None
    assert record["excluded_relation_tuple"] == ["public.alembic_version"]
    assert record["required_application_role_metadata_privilege_count"] == 0
    assert record["observed_application_role_metadata_privilege_count"] is None
    assert record["prior_direct_privilege_revoke_wired"] is True
    assert record["grant_order"] == (
        "REVOKE_EXCLUDED_METADATA_THEN_GRANT_ENUMERATED_NONMETADATA_PUBLIC_TABLES"
    )
    assert record["pre_grant_exact_domain_census_status"] == (
        "PENDING_SIGNED_V27_APPLICATION_ROLE_CENSUS"
    )
    assert record["database_proof_status"] == "PENDING_SIGNED_V27_APPLICATION_ROLE_CENSUS"
    assert record["database_call_made"] is False
    assert record["authority_expanded"] is False
    assert record["schema_or_predicate_changed"] is False
    assert record["verification"] == {
        "red_first": True,
        "hermetic_passed": 8,
        "environment_dependent_skipped": 3,
        "independent_review": "PASS_NO_CRITICAL_OR_IMPORTANT",
        "ruff_check": "PASS",
        "ruff_format": "PASS",
        "strict_mypy": "PASS",
        "py_compile": "PASS",
        "diff_check": "PASS",
    }
    assert record["source_sha256"] == {
        "strict_launcher": "5fc4b70aa79a5d55d2940ac9cbf321ff50b0aa24b9482b6eecbf3852b4516f11",
        "task8_boundary_test": "4dd60d3dce64e1560678d7c20e87f9072e1dd990a628216c5743d57bfb8d3eb0",
        "task4_fresh_database_test": (
            "54ac4f3436a7d02abc5f3938cca58a91edbfdbe798775003832333d97a89b34d"
        ),
        "task6_post_migration_privilege_test": (
            "3bf90296fd1061e3aaf25916c96a8208f7f070a5f6dd04f9992578b0d00c6cfc"
        ),
    }


def test_v26_execution_suites_are_archived_and_synthetic_v27_fixture_remains_unadmitted(
    tmp_path: Path,
) -> None:
    expected_archived = frozenset(_ARCHIVED_V26_MODULE_SHA256)
    assert getattr(task8_conftest, "ARCHIVED_V26_MODULES", None) == expected_archived
    for name, expected_sha256 in _ARCHIVED_V26_MODULE_SHA256.items():
        path = _TASK8_ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
        assert task8_conftest.should_ignore_historical_v22(path) is True

    v26_artifact, v26_fixture = _write_candidate_pair(tmp_path, version=26)
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=v26_artifact,
        fixture_path=v26_fixture,
    ), "TASK8_VOID_V26_RECOGNIZED_AS_SUCCESSOR"
    v27_artifact, v27_fixture = _write_complete_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=None,
    )
    v27_artifact_sha256 = hashlib.sha256(v27_artifact.read_bytes()).hexdigest()
    _write_complete_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=v27_artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=v27_artifact,
        fixture_path=v27_fixture,
    ), "TASK8_V27_SYNTHETIC_FIXTURE_IDENTITY_ADMITTED"
