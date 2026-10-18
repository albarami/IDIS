"""Task-8 test lifecycle and historical-candidate boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V27_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v27_reviewed_preimage_v1.json"
_V27_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v27_contract_hash_v1.json"
)
_V27_ARTIFACT_SHA256: Final[str] = (
    "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
)
_V27_FIXTURE_SHA256: Final[str] = "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
_V27_FIXTURE_SIZE_BYTES: Final[int] = 310_306
_V27_ARTIFACT_SECTION_ORDER: Final[tuple[str, ...]] = (
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
_V27_METADATA_FIELDS: Final[tuple[str, ...]] = (
    "artifact_schema",
    "append_frozen",
    "pipeline_contract_version",
    "pydantic_version",
    "hash_protocol",
    "reviewed_design_sha256",
    "reviewed_design_utf8_hex",
    "section_order",
    "activation_status",
)
_V27_FIXTURE_FIELDS: Final[tuple[str, ...]] = (
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
_V27_COMPONENT_HASH_FIELDS: Final[tuple[str, ...]] = tuple(
    f"{section}_hash" for section in _V27_ARTIFACT_SECTION_ORDER
) + ("pipeline_contract_hash",)
_V27_IDENTITY_GRAPH_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "node_tuple",
    "edge_tuple",
    "leaf_tuple",
    "source_pointer_tuple",
    "counts",
    "graph_sha256",
)
_V27_CONSERVATION_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "entry_count",
    "sha256",
)
_V27_POLICY_BINDING_FIELDS: Final[tuple[str, ...]] = (
    "policy_id",
    "artifact_sha256",
    "policy_sha256",
    "reviewer_map_relative_path",
    "reviewer_map_sha256",
    "binding_sha256",
)
_V27_COUNT_FIELDS: Final[tuple[str, ...]] = (
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

ARCHIVED_V22_MODULES: Final[frozenset[str]] = frozenset(
    {
        "test_full_strict_v22_materialization_red.py",
        "test_v22_dashboard_identity_cycle_red.py",
        "test_v22_fdd_schema_execution_red.py",
        "test_v22_sql_authority_postgres_census_red.py",
    }
)
ARCHIVED_V23_MODULES: Final[frozenset[str]] = frozenset(
    {
        "test_full_strict_v23_materialization_red.py",
        "test_task8_identity_hash_dependency_graph_red.py",
        "test_v23_fdd_schema_execution_red.py",
        "test_v23_general_graph_and_sql_identity_red.py",
    }
)
ARCHIVED_V24_MODULES: Final[frozenset[str]] = frozenset(
    {
        "test_task8_atomic_publication_red.py",
        "test_v24_sql_authority_postgres_census_red.py",
    }
)
ARCHIVED_V25_MODULES: Final[frozenset[str]] = frozenset(
    {
        "test_full_strict_v25_materialization_red.py",
        "test_v25_dashboard_requirement_matrix_red.py",
        "test_v25_independent_fixture_verification_red.py",
    }
)
ARCHIVED_V26_MODULES: Final[frozenset[str]] = frozenset(
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


def _module_paths(names: frozenset[str]) -> frozenset[str]:
    return frozenset(f"tests/slice100_task8/{name}" for name in names)


ARCHIVED_V22_MODULE_PATHS: Final[frozenset[str]] = _module_paths(ARCHIVED_V22_MODULES)
ARCHIVED_V23_MODULE_PATHS: Final[frozenset[str]] = _module_paths(ARCHIVED_V23_MODULES)
ARCHIVED_V24_MODULE_PATHS: Final[frozenset[str]] = _module_paths(ARCHIVED_V24_MODULES)
ARCHIVED_V25_MODULE_PATHS: Final[frozenset[str]] = _module_paths(ARCHIVED_V25_MODULES)
ARCHIVED_V26_MODULE_PATHS: Final[frozenset[str]] = _module_paths(ARCHIVED_V26_MODULES)
_ARCHIVED_MODULE_PATHS: Final[frozenset[str]] = (
    ARCHIVED_V22_MODULE_PATHS
    | ARCHIVED_V23_MODULE_PATHS
    | ARCHIVED_V24_MODULE_PATHS
    | ARCHIVED_V25_MODULE_PATHS
    | ARCHIVED_V26_MODULE_PATHS
)


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reviewed_design_is_bound(metadata: dict[str, Any]) -> bool:
    raw_hex = metadata.get("reviewed_design_utf8_hex")
    if not isinstance(raw_hex, str):
        return False
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return False
    return hashlib.sha256(raw).hexdigest() == metadata.get("reviewed_design_sha256")


def successor_publication_is_complete(
    *,
    artifact_path: Path = _V27_ARTIFACT,
    fixture_path: Path = _V27_FIXTURE,
) -> bool:
    """Return true only for an exact, frozen, fixture-bound v27 pair."""

    artifact = _load_object(artifact_path)
    fixture = _load_object(fixture_path)
    if artifact is None or fixture is None:
        return False
    try:
        artifact_raw = artifact_path.read_bytes()
        fixture_raw = fixture_path.read_bytes()
    except OSError:
        return False
    if hashlib.sha256(artifact_raw).hexdigest() != _V27_ARTIFACT_SHA256:
        return False
    metadata = artifact.get("metadata")
    activation_assertions = artifact.get("activation_assertions")
    if (
        tuple(artifact) != _V27_ARTIFACT_SECTION_ORDER
        or not all(
            isinstance(artifact.get(section), dict) and bool(artifact[section])
            for section in _V27_ARTIFACT_SECTION_ORDER
        )
        or not isinstance(metadata, dict)
        or tuple(metadata) != _V27_METADATA_FIELDS
        or not isinstance(activation_assertions, dict)
    ):
        return False
    if (
        metadata.get("artifact_schema") != "full-strict-v27-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v27"
        or metadata.get("append_frozen") is not True
        or metadata.get("hash_protocol") != "idis-task7-canonical-json-sha256-v1"
        or not isinstance(metadata.get("pydantic_version"), str)
        or not metadata["pydantic_version"]
        or metadata.get("section_order") != list(_V27_ARTIFACT_SECTION_ORDER)
        or metadata.get("activation_status") != "INACTIVE"
        or not _reviewed_design_is_bound(metadata)
        or activation_assertions.get("activation_status") != "INACTIVE"
        or activation_assertions.get("runtime_selectable") is not False
    ):
        return False
    if tuple(fixture) != _V27_FIXTURE_FIELDS:
        return False
    component_hashes = fixture.get("component_hashes")
    identity_graph = fixture.get("identity_hash_dependency_graph")
    conservation = fixture.get("approved_semantics_conservation")
    policy_binding = fixture.get("policy_binding")
    counts = fixture.get("counts")
    if not all(
        isinstance(value, dict)
        for value in (component_hashes, identity_graph, conservation, policy_binding, counts)
    ):
        return False
    assert isinstance(component_hashes, dict)
    assert isinstance(identity_graph, dict)
    assert isinstance(conservation, dict)
    assert isinstance(policy_binding, dict)
    assert isinstance(counts, dict)
    if (
        fixture.get("artifact_schema") != "full-strict-v27-contract-hash-fixture-v1"
        or fixture.get("pipeline_contract_version") != "full-strict-v27"
        or fixture.get("activation_status") != "INACTIVE"
        or not _is_sha256(fixture.get("artifact_sha256"))
        or not _is_sha256(fixture.get("FULL_STRICT_V27_CONTRACT_HASH"))
        or tuple(component_hashes) != _V27_COMPONENT_HASH_FIELDS
        or not all(_is_sha256(value) for value in component_hashes.values())
        or component_hashes["pipeline_contract_hash"] != fixture["FULL_STRICT_V27_CONTRACT_HASH"]
        or tuple(identity_graph) != _V27_IDENTITY_GRAPH_FIELDS
        or not _is_sha256(identity_graph.get("graph_sha256"))
        or tuple(conservation) != _V27_CONSERVATION_FIELDS
        or not _is_sha256(conservation.get("sha256"))
        or tuple(policy_binding) != _V27_POLICY_BINDING_FIELDS
        or policy_binding.get("policy_id") != "fdd-formula-policy-v2"
        or not all(
            _is_sha256(policy_binding.get(field))
            for field in (
                "artifact_sha256",
                "policy_sha256",
                "reviewer_map_sha256",
                "binding_sha256",
            )
        )
        or tuple(counts) != _V27_COUNT_FIELDS
        or not all(type(value) is int and value >= 0 for value in counts.values())
    ):
        return False
    return (
        fixture.get("artifact_sha256") == _V27_ARTIFACT_SHA256
        and len(fixture_raw) == _V27_FIXTURE_SIZE_BYTES
        and hashlib.sha256(fixture_raw).hexdigest() == _V27_FIXTURE_SHA256
    )


def should_ignore_historical_v22(
    collection_path: Path,
    *,
    successor_complete: bool | None = None,
) -> bool | None:
    """Archive only the exact pinned suites whose canonical fixture is retired."""

    del successor_complete
    try:
        lexical_absolute = Path(os.path.abspath(collection_path))
        lexical_repo_root = Path(os.path.abspath(_REPO_ROOT))
        relative_path = lexical_absolute.relative_to(lexical_repo_root).as_posix()
    except (OSError, ValueError, TypeError):
        return None
    if relative_path in _ARCHIVED_MODULE_PATHS:
        return True
    return None


@pytest.fixture(autouse=True)
def bind_operator_workbook_recorder(
    record_property: pytest.RecordProperty,
) -> Iterator[None]:
    """Give the workbook absence gate a recorder for every Task 8 test."""

    from tests.slice100_task8 import _artifact_oracle as oracle

    previous = oracle._RECORD_PROPERTY
    oracle._RECORD_PROPERTY = record_property
    yield
    oracle._RECORD_PROPERTY = previous


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:
    """Archive the exact non-runnable v22-v26 execution inventories and nothing else."""

    del config
    return should_ignore_historical_v22(collection_path)
