"""Exact collection boundary for rejected Task-8.1 v22-v26 candidates.

The v22/v23/v24/v25/v26 sources remain immutable evidence. Their exact execution-
module inventory is archived because their canonical fixtures are permanently
retired or never existed; every current void and v27 integrity suite remains
collected.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.slice100_task8 import _v27_independent_oracle as v27_independent
from tests.slice100_task8 import conftest as task8_conftest
from tests.slice100_task8._task8_void_contract_hash_evidence import (
    VOID_V22_FIXTURE_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK8_ROOT = _REPO_ROOT / "tests" / "slice100_task8"
_RECONCILIATION_LOG = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task8" / "task8_contract_reconciliation_log.jsonl"
)
_ARCHIVED_FIXTURE = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v22-current-output-extension-regeneration-002"
    / "full_strict_v22_contract_hash_v1.json"
)
_CANONICAL_V22_FIXTURE = _TASK8_ROOT / "fixtures" / "full_strict_v22_contract_hash_v1.json"
_EXPECTED_V22_FIXTURE_SHA256 = VOID_V22_FIXTURE_SHA256
_EXPECTED_ARCHIVED_MODULES = frozenset(
    {
        "test_full_strict_v22_materialization_red.py",
        "test_v22_dashboard_identity_cycle_red.py",
        "test_v22_fdd_schema_execution_red.py",
        "test_v22_sql_authority_postgres_census_red.py",
    }
)
_EXPECTED_ARCHIVED_V23_MODULES = frozenset(
    {
        "test_full_strict_v23_materialization_red.py",
        "test_task8_identity_hash_dependency_graph_red.py",
        "test_v23_fdd_schema_execution_red.py",
        "test_v23_general_graph_and_sql_identity_red.py",
    }
)
_EXPECTED_ARCHIVED_V24_MODULE_SHA256 = {
    "test_task8_atomic_publication_red.py": (
        "89c287fe62e7f9e5e281ffd4edb4799506047250418575037225d2872f999182"
    ),
    "test_v24_sql_authority_postgres_census_red.py": (
        "6bea316ef53b6b9b328fc1801fe273a93d540d6d541facf3399c958a0efd9c5e"
    ),
}
_EXPECTED_ARCHIVED_V24_MODULES = frozenset(_EXPECTED_ARCHIVED_V24_MODULE_SHA256)
_EXPECTED_ARCHIVED_V25_MODULES = frozenset(
    {
        "test_full_strict_v25_materialization_red.py",
        "test_v25_dashboard_requirement_matrix_red.py",
        "test_v25_independent_fixture_verification_red.py",
    }
)
_EXPECTED_ARCHIVED_V26_MODULES = frozenset(
    {
        "test_full_strict_v26_materialization_red.py",
        "test_v26_bound_runtime_graph_red.py",
        "test_v26_independent_fixture_verification_red.py",
        "test_task8_fixture_derivation_red.py",
        "test_v26_sql_authority_postgres_census_red.py",
        "test_run_task8_v26_signed_census.py",
        "test_task8_v26_atomic_publication_red.py",
    }
)
_EXPECTED_ARCHIVED_MODULE_PATHS = frozenset(
    f"tests/slice100_task8/{name}" for name in _EXPECTED_ARCHIVED_MODULES
)
_EXPECTED_ARCHIVED_V23_MODULE_PATHS = frozenset(
    f"tests/slice100_task8/{name}" for name in _EXPECTED_ARCHIVED_V23_MODULES
)
_EXPECTED_ARCHIVED_V24_MODULE_PATHS = frozenset(
    f"tests/slice100_task8/{name}" for name in _EXPECTED_ARCHIVED_V24_MODULES
)
_EXPECTED_ARCHIVED_V25_MODULE_PATHS = frozenset(
    f"tests/slice100_task8/{name}" for name in _EXPECTED_ARCHIVED_V25_MODULES
)
_EXPECTED_ARCHIVED_V26_MODULE_PATHS = frozenset(
    f"tests/slice100_task8/{name}" for name in _EXPECTED_ARCHIVED_V26_MODULES
)
_V27_ARTIFACT_SECTION_ORDER = (
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
    "task8_fdd_contract",
    "task8_structural_admission",
)
_V27_FIXTURE_FIELD_ORDER = (
    "artifact_schema",
    "pipeline_contract_version",
    "artifact_sha256",
    "FULL_STRICT_V27_CONTRACT_HASH",
    "component_hashes",
    "identity_hash_dependency_graph",
    "approved_semantics_conservation",
    "policy_binding",
    "counts",
    "activation_status",
)
_V27_COUNT_FIELDS = (
    "semantic_step_count",
    "edge_rule_count",
    "usage_route_count",
    "usage_extractor_count",
    "provider_count",
    "schema_count",
    "current_ddl_table_count",
    "installed_tenant_rls_table_count",
    "erasure_current_relation_count",
    "erasure_future_relation_count",
    "erasure_complete_relation_count",
    "task11_restart_cut_count",
    "task11_undischarged_rule_count",
)
_EXPECTED_V22_SOURCE_SHA256 = {
    "_v22_oracle.py": "d5ed4721a5635b22232499633511713cc4fae6c0f394b04e7bf498d69ba5f2e6",
    "test_full_strict_v22_materialization_red.py": (
        "dabb5fb28f3032b172cbe1f2a5bd0a3a7a0b93abd67283d466ec2132a3583cf6"
    ),
    "test_v22_fdd_schema_execution_red.py": (
        "c7a238a5c4c927588db49d4b2fbce72c651981e0268756fbdf3c7a4af4166a9d"
    ),
    "test_v22_sql_authority_postgres_census_red.py": (
        "6e7b8ff451769baf89582741ae66fece4d0e3fe443a52890629d28b7b46db77f"
    ),
    "test_v22_dashboard_identity_cycle_red.py": (
        "c3441dc8b2d7612b2cc4e4268ffdd2e0aa57441c8b58802799cd4f23c21954b0"
    ),
}
_EXPECTED_V22_SCRIPT_SHA256 = {
    "materialize_full_strict_v22_preimage.py": (
        "78add79548bd4ef47f26290f63325f4b0744f659cfcc0f8c47deb30ebc282c29"
    ),
    "run_task8_v22_sql_authority_census.py": (
        "4f614ab2788cb0137430dba11c1a82ecb0fdd0e3693186fe6242bafd5b186268"
    ),
}
_EXPECTED_V23_SOURCE_SHA256 = {
    "_v23_oracle.py": "e4b62772671840ff78d305e257cab6ae946e3e294577509e7eb0804df326a120",
    "test_full_strict_v23_materialization_red.py": (
        "f69676a9360c297cda0dff0352400e24911529f41c808873073faf309e41cb25"
    ),
    "test_task8_identity_hash_dependency_graph_red.py": (
        "ae54ae4260b96490eb350185fd14e221cfd87e26b989a74eae06a94cd2e78681"
    ),
    "test_v23_fdd_schema_execution_red.py": (
        "2bd2810a56d6641c7b841791abedae9fc3aad2fe3c18f40a9c08eae747d2453f"
    ),
    "test_v23_general_graph_and_sql_identity_red.py": (
        "9170eed7936dfb22192039fdaad139a19083ddb9fa000e01c67718c4ba200ed0"
    ),
}
_EXPECTED_V23_SCRIPT_SHA256 = {
    "materialize_full_strict_v23_preimage.py": (
        "9c546a58b903b39f7652a0d861876b22eb736cc3d522488de72928a1acd170e6"
    )
}
_EXPECTED_V25_SOURCE_SHA256 = {
    "test_full_strict_v25_materialization_red.py": (
        "905fb641b28c0e4c3380c892d365ff4329b2fe07f079596351bcb2261ad76618"
    ),
    "test_v25_dashboard_requirement_matrix_red.py": (
        "0ab3a477df943e387b45fb1670e689be206cbe6457e574d9a51a610a87351ed5"
    ),
    "test_v25_independent_fixture_verification_red.py": (
        "5b5c64f7ada0c11cc5ec63c1947c65427faab1794913d37611cb25563255d95d"
    ),
}
_EXPECTED_V26_SOURCE_SHA256 = {
    "test_full_strict_v26_materialization_red.py": (
        "d1b1cebe06b0b96a38dceb06a16c182f7c1882e4618ad742081aa98654107a7c"
    ),
    "test_v26_bound_runtime_graph_red.py": (
        "fb7675178711f5c29cfa6d45545857cff925e3a6676e605e249105e6cc824ac2"
    ),
    "test_v26_independent_fixture_verification_red.py": (
        "b5cdf29ce81e631dc5f73787719505aabebc7d56a0a9704c5ee32d7230a49227"
    ),
    "test_task8_fixture_derivation_red.py": (
        "cff1d8276ef8f30bcf40c3d266f0f1e6bcff91c86b2cdbe29c3ac3f91b55a09f"
    ),
    "test_v26_sql_authority_postgres_census_red.py": (
        "19f5cfee9c10cdf47745c65a617f2a1e4567be769a71197f6029bea24e69d39a"
    ),
    "test_run_task8_v26_signed_census.py": (
        "8738bfa56bf7f339c577ab549d0136573f71752f1edaa99c576b802e158e1e13"
    ),
    "test_task8_v26_atomic_publication_red.py": (
        "d6d62c03f7e910b5d7593a0b3d9277cd43d2fc35cab3f29f7eebf46ef195eccb"
    ),
}
_V23_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v23_reviewed_preimage_v1.json"
_V23_FIXTURE = _TASK8_ROOT / "fixtures" / "full_strict_v23_contract_hash_v1.json"
_V23_ARTIFACT_SHA256 = "ddbe234bfbac246e65b2b1c0dd30eae018e88754446d713f1defdb4d70232dfc"
_V23_FIXTURE_SHA256 = "b7782392676e884560840fc8314dbc4a7673800e5cbeaa04a85eabdb329879c5"
_V25_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v25_reviewed_preimage_v1.json"
_V25_FIXTURE = _TASK8_ROOT / "fixtures" / "full_strict_v25_contract_hash_v1.json"


def _write_successor_pair(
    root: Path,
    *,
    version: int,
    frozen: object,
    fixture_artifact_sha256: str | None,
    omitted_artifact_section: str | None = None,
    omitted_fixture_field: str | None = None,
) -> tuple[Path, Path]:
    artifact = root / f"full_strict_v{version}_reviewed_preimage_v1.json"
    reviewed_design_raw = f"full-strict-v{version}-synthetic-completeness-proof-v1".encode()
    artifact_value: dict[str, object] = {
        section: {"schema_version": f"synthetic-{section}-v1"}
        for section in _V27_ARTIFACT_SECTION_ORDER
        if section != "metadata"
    }
    artifact_value = {
        "metadata": {
            "artifact_schema": f"full-strict-v{version}-reviewed-preimage-v1",
            "append_frozen": frozen,
            "pipeline_contract_version": f"full-strict-v{version}",
            "pydantic_version": "2.12.5",
            "hash_protocol": "idis-task7-canonical-json-sha256-v1",
            "reviewed_design_sha256": hashlib.sha256(reviewed_design_raw).hexdigest(),
            "reviewed_design_utf8_hex": reviewed_design_raw.hex(),
            "section_order": list(_V27_ARTIFACT_SECTION_ORDER),
            "activation_status": "INACTIVE",
        },
        **artifact_value,
    }
    artifact_value["activation_assertions"] = {
        "activation_status": "INACTIVE",
        "runtime_selectable": False,
    }
    if omitted_artifact_section is not None:
        artifact_value.pop(omitted_artifact_section)
    artifact.write_text(
        json.dumps(artifact_value, separators=(",", ":")),
        encoding="utf-8",
    )
    fixture = root / f"full_strict_v{version}_contract_hash_v1.json"
    if fixture_artifact_sha256 is not None:
        contract_hash = hashlib.sha256(f"full-strict-v{version}-contract".encode()).hexdigest()
        component_hashes = {
            f"{section}_hash": hashlib.sha256(section.encode()).hexdigest()
            for section in _V27_ARTIFACT_SECTION_ORDER
        }
        component_hashes["pipeline_contract_hash"] = contract_hash
        counts = dict.fromkeys(_V27_COUNT_FIELDS, 0)
        counts["installed_tenant_rls_table_count"] = 66
        fixture_value: dict[str, object] = {
            "artifact_schema": f"full-strict-v{version}-contract-hash-fixture-v1",
            "pipeline_contract_version": f"full-strict-v{version}",
            "artifact_sha256": fixture_artifact_sha256,
            f"FULL_STRICT_V{version}_CONTRACT_HASH": contract_hash,
            "component_hashes": component_hashes,
            "identity_hash_dependency_graph": {
                "schema_version": "task8-identity-hash-dependency-graph-v1",
                "node_tuple": [],
                "edge_tuple": [],
                "leaf_tuple": [],
                "source_pointer_tuple": [],
                "counts": {},
                "graph_sha256": hashlib.sha256(b"synthetic-graph").hexdigest(),
            },
            "approved_semantics_conservation": {
                "schema_version": "task8-approved-semantics-conservation-v1",
                "entry_count": 0,
                "sha256": hashlib.sha256(b"synthetic-conservation").hexdigest(),
            },
            "policy_binding": {
                "policy_id": "fdd-formula-policy-v2",
                "artifact_sha256": hashlib.sha256(b"synthetic-policy-artifact").hexdigest(),
                "policy_sha256": hashlib.sha256(b"synthetic-policy").hexdigest(),
                "reviewer_map_relative_path": "docs/plans/synthetic-review-map.md",
                "reviewer_map_sha256": hashlib.sha256(b"synthetic-review-map").hexdigest(),
                "binding_sha256": hashlib.sha256(b"synthetic-policy-binding").hexdigest(),
            },
            "counts": counts,
            "activation_status": "INACTIVE",
        }
        if omitted_fixture_field is not None:
            fixture_value.pop(omitted_fixture_field)
        fixture.write_text(
            json.dumps(fixture_value, separators=(",", ":")),
            encoding="utf-8",
        )
    return artifact, fixture


def test_historical_v22_module_and_source_inventory_is_exact_and_byte_pinned() -> None:
    assert task8_conftest.ARCHIVED_V22_MODULES == _EXPECTED_ARCHIVED_MODULES
    assert task8_conftest.ARCHIVED_V22_MODULE_PATHS == _EXPECTED_ARCHIVED_MODULE_PATHS
    assert all((_TASK8_ROOT / name).is_file() for name in _EXPECTED_ARCHIVED_MODULES)
    for name, expected_sha256 in _EXPECTED_V22_SOURCE_SHA256.items():
        assert hashlib.sha256((_TASK8_ROOT / name).read_bytes()).hexdigest() == expected_sha256
    for name, expected_sha256 in _EXPECTED_V22_SCRIPT_SHA256.items():
        path = _REPO_ROOT / "scripts" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def test_historical_v23_draft_inventory_is_exact_and_byte_pinned() -> None:
    assert getattr(task8_conftest, "ARCHIVED_V23_MODULES", None) == (_EXPECTED_ARCHIVED_V23_MODULES)
    assert task8_conftest.ARCHIVED_V23_MODULE_PATHS == _EXPECTED_ARCHIVED_V23_MODULE_PATHS
    assert all((_TASK8_ROOT / name).is_file() for name in _EXPECTED_ARCHIVED_V23_MODULES)
    for name, expected_sha256 in _EXPECTED_V23_SOURCE_SHA256.items():
        assert hashlib.sha256((_TASK8_ROOT / name).read_bytes()).hexdigest() == expected_sha256
    for name, expected_sha256 in _EXPECTED_V23_SCRIPT_SHA256.items():
        path = _REPO_ROOT / "scripts" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    assert hashlib.sha256(_V23_ARTIFACT.read_bytes()).hexdigest() == _V23_ARTIFACT_SHA256
    assert hashlib.sha256(_V23_FIXTURE.read_bytes()).hexdigest() == _V23_FIXTURE_SHA256


def test_historical_v24_execution_inventory_is_exact_and_byte_pinned() -> None:
    assert task8_conftest.ARCHIVED_V24_MODULES == _EXPECTED_ARCHIVED_V24_MODULES
    assert task8_conftest.ARCHIVED_V24_MODULE_PATHS == _EXPECTED_ARCHIVED_V24_MODULE_PATHS
    for name, expected_sha256 in _EXPECTED_ARCHIVED_V24_MODULE_SHA256.items():
        path = _TASK8_ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
        assert task8_conftest.should_ignore_historical_v22(path) is True
    records = [json.loads(line) for line in _RECONCILIATION_LOG.read_text("utf-8").splitlines()]
    record = next(
        value
        for value in records
        if value.get("event")
        == "V24_OBSOLETE_EXECUTION_SUITES_ARCHIVED_CURRENT_VOID_EVIDENCE_RETAINED"
    )
    assert record["v24_void_boundary_source_sha256_after"] == (
        "846f2b2cda6933ff5a1bde2dad20196821c817646eb629eba4871d601e15ebd0"
    )


def test_historical_v25_execution_inventory_is_exact_byte_pinned_and_unpublished() -> None:
    assert getattr(task8_conftest, "ARCHIVED_V25_MODULES", None) == (_EXPECTED_ARCHIVED_V25_MODULES)
    assert task8_conftest.ARCHIVED_V25_MODULE_PATHS == _EXPECTED_ARCHIVED_V25_MODULE_PATHS
    assert all((_TASK8_ROOT / name).is_file() for name in _EXPECTED_ARCHIVED_V25_MODULES)
    for name, expected_sha256 in _EXPECTED_V25_SOURCE_SHA256.items():
        assert hashlib.sha256((_TASK8_ROOT / name).read_bytes()).hexdigest() == expected_sha256
    assert not _V25_ARTIFACT.exists(), "TASK8_VOID_V25_CANONICAL_ARTIFACT_PRESENT"
    assert not _V25_FIXTURE.exists(), "TASK8_VOID_V25_CANONICAL_FIXTURE_PRESENT"


def test_historical_v26_execution_inventory_is_exact_and_byte_pinned() -> None:
    assert getattr(task8_conftest, "ARCHIVED_V26_MODULES", None) == (_EXPECTED_ARCHIVED_V26_MODULES)
    assert task8_conftest.ARCHIVED_V26_MODULE_PATHS == _EXPECTED_ARCHIVED_V26_MODULE_PATHS
    assert all((_TASK8_ROOT / name).is_file() for name in _EXPECTED_ARCHIVED_V26_MODULES)
    for name, expected_sha256 in _EXPECTED_V26_SOURCE_SHA256.items():
        assert hashlib.sha256((_TASK8_ROOT / name).read_bytes()).hexdigest() == expected_sha256


def test_v22_fixture_has_an_exact_ignored_archive_before_canonical_removal() -> None:
    assert _ARCHIVED_FIXTURE.is_file(), "TASK8_V22_ARCHIVED_FIXTURE_MISSING"
    assert hashlib.sha256(_ARCHIVED_FIXTURE.read_bytes()).hexdigest() == (
        _EXPECTED_V22_FIXTURE_SHA256
    )
    assert not _CANONICAL_V22_FIXTURE.exists(), "TASK8_VOID_V22_FIXTURE_STILL_PUBLISHED"


def test_successor_boundary_refuses_void_v23_v26_and_partial_unfrozen_or_unbound_v27(
    tmp_path: Path,
) -> None:
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=_V23_ARTIFACT,
        fixture_path=_V23_FIXTURE,
    ), "TASK8_VOID_V23_RECOGNIZED_AS_SUCCESSOR"

    missing_artifact = tmp_path / "missing-artifact.json"
    missing_fixture = tmp_path / "missing-fixture.json"
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=missing_artifact,
        fixture_path=missing_fixture,
    )

    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=False,
        fixture_artifact_sha256=None,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    )

    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256="0" * 64,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    )

    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=26,
        frozen=True,
        fixture_artifact_sha256=None,
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=26,
        frozen=True,
        fixture_artifact_sha256=artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_VOID_V26_RECOGNIZED_AS_SUCCESSOR"


def test_successor_boundary_rejects_a_structurally_complete_synthetic_v27_pair(
    tmp_path: Path,
) -> None:
    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=None,
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_V27_SYNTHETIC_FIXTURE_IDENTITY_ADMITTED"
    archived = (
        _EXPECTED_ARCHIVED_MODULES
        | _EXPECTED_ARCHIVED_V23_MODULES
        | _EXPECTED_ARCHIVED_V24_MODULES
        | _EXPECTED_ARCHIVED_V25_MODULES
        | _EXPECTED_ARCHIVED_V26_MODULES
    )
    for name in archived:
        for successor_complete in (False, True):
            assert (
                task8_conftest.should_ignore_historical_v22(
                    _TASK8_ROOT / name,
                    successor_complete=successor_complete,
                )
                is True
            )


def test_successor_boundary_accepts_only_the_independently_rendered_exact_v27_pair(
    tmp_path: Path,
) -> None:
    parent_raw, policy_raw, reviewer_map_raw, artifact_raw = v27_independent.candidate_bytes()
    verifier = v27_independent.load_verifier()
    fixture_raw = verifier.derive_fixture_bytes(
        parent_artifact_raw=parent_raw,
        policy_artifact_raw=policy_raw,
        reviewer_map_raw=reviewer_map_raw,
        v27_artifact_raw=artifact_raw,
    )
    assert len(fixture_raw) == 310_306
    assert hashlib.sha256(fixture_raw).hexdigest() == (
        "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
    )
    artifact_path = tmp_path / "full_strict_v27_reviewed_preimage_v1.json"
    fixture_path = tmp_path / "full_strict_v27_contract_hash_v1.json"
    artifact_path.write_bytes(artifact_raw)
    fixture_path.write_bytes(fixture_raw + b" ")
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
    ), "TASK8_V27_NONEXACT_FIXTURE_BYTES_ADMITTED"
    fixture_path.write_bytes(fixture_raw)
    assert task8_conftest.successor_publication_is_complete(
        artifact_path=artifact_path,
        fixture_path=fixture_path,
    )


def test_successor_boundary_rejects_non_boolean_or_shape_incomplete_v27(tmp_path: Path) -> None:
    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=1,
        fixture_artifact_sha256=None,
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=27,
        frozen=1,
        fixture_artifact_sha256=artifact_sha256,
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_V27_INTEGER_APPEND_FROZEN_ACCEPTED"

    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=None,
        omitted_artifact_section="task8_structural_admission",
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=artifact_sha256,
        omitted_artifact_section="task8_structural_admission",
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_V27_INCOMPLETE_ARTIFACT_ACCEPTED"

    artifact, fixture = _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=None,
    )
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _write_successor_pair(
        tmp_path,
        version=27,
        frozen=True,
        fixture_artifact_sha256=artifact_sha256,
        omitted_fixture_field="counts",
    )
    assert not task8_conftest.successor_publication_is_complete(
        artifact_path=artifact,
        fixture_path=fixture,
    ), "TASK8_V27_INCOMPLETE_FIXTURE_ACCEPTED"


def test_archive_boundary_matches_exact_repo_relative_paths_not_basenames() -> None:
    archived = (
        _EXPECTED_ARCHIVED_MODULES
        | _EXPECTED_ARCHIVED_V23_MODULES
        | _EXPECTED_ARCHIVED_V24_MODULES
        | _EXPECTED_ARCHIVED_V25_MODULES
        | _EXPECTED_ARCHIVED_V26_MODULES
    )
    for name in archived:
        assert task8_conftest.should_ignore_historical_v22(_TASK8_ROOT / name) is True
        assert task8_conftest.should_ignore_historical_v22(_TASK8_ROOT / "nested" / name) is None
        assert (
            task8_conftest.should_ignore_historical_v22(
                _REPO_ROOT / "tests" / "slice100_task9_periphery" / name
            )
            is None
        )


def test_archive_boundary_does_not_resolve_a_lexical_alias_into_an_archived_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _TASK8_ROOT / "test_task8_atomic_publication_red.py"
    alias = _TASK8_ROOT / "nested-reparse-alias" / target.name
    original_resolve = Path.resolve

    def collapse_alias(path: Path, strict: bool = False) -> Path:
        if path == alias:
            return target
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", collapse_alias)
    assert alias != target
    assert alias.resolve() == target
    assert task8_conftest.should_ignore_historical_v22(alias) is None


def test_collection_boundary_never_hides_current_void_or_v27_integrity_suites() -> None:
    required_current = (
        "test_task8_v24_void_boundary_red.py",
        "test_v24_structural_admission_red.py",
        "test_task8_v25_void_boundary_red.py",
        "test_task8_v26_void_boundary_red.py",
        "test_task8_void_contract_hash_blocklist.py",
        "test_task8_historical_candidate_collection_boundary.py",
        "test_full_strict_v27_materialization_red.py",
        "test_v27_installed_rls_count_red.py",
        "test_v27_fixture_derivation_red.py",
    )
    for name in required_current:
        assert (_TASK8_ROOT / name).is_file(), "TASK8_REQUIRED_CURRENT_SUITE_MISSING"
        for successor_complete in (False, True):
            assert (
                task8_conftest.should_ignore_historical_v22(
                    _TASK8_ROOT / name,
                    successor_complete=successor_complete,
                )
                is None
            )

    planned_v27_current = (
        "test_v27_independent_fixture_verification_red.py",
        "test_v27_sql_authority_postgres_census_red.py",
        "test_run_task8_v27_signed_census.py",
        "test_task8_v27_atomic_publication_red.py",
    )
    assert all(
        task8_conftest.should_ignore_historical_v22(_TASK8_ROOT / name) is None
        for name in planned_v27_current
    )
