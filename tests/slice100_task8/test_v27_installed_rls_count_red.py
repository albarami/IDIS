"""RED-first proof of the v27 installed-RLS count-domain correction."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from . import _v27_oracle as oracle

MIGRATION_ROOT = oracle.REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"
SOURCE_INVENTORY_AUTHORITIES = (
    (MIGRATION_ROOT / "0034_normalize_tenant_rls.py", "TENANT_TABLES", 42),
    (MIGRATION_ROOT / "0036_durable_methodology_selection.py", "TASK5_TABLES", 4),
    (MIGRATION_ROOT / "0037_durable_methodology_extraction.py", "TASK6_TABLES", 9),
    (MIGRATION_ROOT / "0038_full_strict_v16_receipts_and_edges.py", "TASK7_TABLES_V1", 9),
)


def _literal_tuple(path: Path, name: str) -> tuple[str, ...]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and node.value is not None
        and (
            (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == name for target in node.targets
                )
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
            )
        )
    ]
    oracle.require(len(values) == 1, "TASK8_V27_SOURCE_INVENTORY_LITERAL_MISSING")
    value = values[0]
    oracle.require(isinstance(value, tuple), "TASK8_V27_SOURCE_INVENTORY_LITERAL_INVALID")
    oracle.require(
        all(isinstance(item, str) for item in value),
        "TASK8_V27_SOURCE_INVENTORY_LITERAL_INVALID",
    )
    return tuple(value)


def test_v27_post_0038_inventory_is_source_derived_42_plus_4_plus_9_plus_9() -> None:
    source_rows: list[str] = []
    for path, name, expected_count in SOURCE_INVENTORY_AUTHORITIES:
        rows = _literal_tuple(path, name)
        oracle.require(
            len(rows) == expected_count,
            "TASK8_V27_SOURCE_INVENTORY_COUNT_DRIFT",
        )
        source_rows.extend(rows)
    oracle.require(
        len(source_rows) == len(set(source_rows)) == 64,
        "TASK8_V27_POST_0038_INVENTORY_NOT_DISJOINT",
    )
    oracle.require(
        tuple(sorted(source_rows)) == oracle.POST_0038_APPLICATION_RELATION_TUPLE,
        "TASK8_V27_POST_0038_INVENTORY_DRIFT",
    )


def test_v27_successor_inventory_is_exact_64_plus_two_dashboard_relations() -> None:
    oracle.require(
        len(oracle.POST_0038_APPLICATION_RELATION_TUPLE) == 64,
        "TASK8_V27_PRIOR_APPLICATION_COUNT_DRIFT",
    )
    oracle.require(
        len(oracle.DASHBOARD_RELATION_TUPLE) == 2,
        "TASK8_V27_DASHBOARD_RELATION_COUNT_DRIFT",
    )
    oracle.require(
        not set(oracle.POST_0038_APPLICATION_RELATION_TUPLE).intersection(
            oracle.DASHBOARD_RELATION_TUPLE
        ),
        "TASK8_V27_DASHBOARD_RELATION_ALREADY_INSTALLED",
    )
    oracle.require(
        tuple(
            sorted((*oracle.POST_0038_APPLICATION_RELATION_TUPLE, *oracle.DASHBOARD_RELATION_TUPLE))
        )
        == oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE,
        "TASK8_V27_SUCCESSOR_APPLICATION_INVENTORY_DRIFT",
    )
    oracle.require(
        oracle.canonical_sha256(
            [oracle.V27_INVENTORY_DOMAIN, oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE]
        )
        == oracle.V27_INVENTORY_SHA256,
        "TASK8_V27_SUCCESSOR_APPLICATION_INVENTORY_HASH_DRIFT",
    )


def test_v27_reproduces_historical_audit_domain_without_admitting_it() -> None:
    oracle.require(
        oracle.canonical_sha256(
            [
                oracle.V26_HISTORICAL_INVENTORY_DOMAIN,
                oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE,
            ]
        )
        == oracle.V26_HISTORICAL_INVENTORY_SHA256,
        "TASK8_V27_HISTORICAL_INVENTORY_HASH_DRIFT",
    )
    oracle.require(
        oracle.V26_HISTORICAL_INVENTORY_SHA256 != oracle.V27_INVENTORY_SHA256,
        "TASK8_V27_INVENTORY_DOMAIN_COLLISION",
    )


def test_v27_physical_catalog_is_67_with_alembic_excluded_from_application_domain() -> None:
    oracle.require(
        len(oracle.PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE) == 67,
        "TASK8_V27_PHYSICAL_PUBLIC_CATALOG_COUNT_DRIFT",
    )
    oracle.require(
        set(oracle.PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE)
        - set(oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE)
        == set(oracle.EXCLUDED_APPLICATION_RELATION_TUPLE),
        "TASK8_V27_PHYSICAL_PUBLIC_CATALOG_EXCLUSION_DRIFT",
    )
    oracle.require(
        oracle.canonical_sha256(
            [
                oracle.V27_PHYSICAL_CATALOG_DOMAIN,
                oracle.PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE,
            ]
        )
        == oracle.V27_PHYSICAL_CATALOG_SHA256,
        "TASK8_V27_PHYSICAL_PUBLIC_CATALOG_HASH_DRIFT",
    )


def test_v27_candidate_changes_exactly_the_three_installed_rls_literals() -> None:
    artifact, _materializer = oracle.build_candidate()
    oracle.require(
        oracle.named_design_value(artifact, "task8_fdd_successor", "installed_rls_delta")
        == [64, 66],
        "TASK8_V27_REVIEWED_DESIGN_INSTALLED_RLS_DRIFT",
    )
    partition = artifact["task8_fdd_contract"]["installed_partition"]
    oracle.require(
        partition["prior_tenant_rls_table_count"] == 64
        and partition["successor_tenant_rls_table_count"] == 66,
        "TASK8_V27_INSTALLED_PARTITION_COUNT_DRIFT",
    )
    oracle.require(
        artifact["activation_assertions"]["installed_tenant_rls_table_count"] == 66,
        "TASK8_V27_ACTIVATION_INSTALLED_RLS_COUNT_DRIFT",
    )

    observed_paths: dict[str, object] = {}

    def collect(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}/{key}"
                if key in {
                    "installed_tenant_rls_table_count",
                    "prior_tenant_rls_table_count",
                    "successor_tenant_rls_table_count",
                }:
                    observed_paths[child_path] = child
                collect(child, child_path)
        elif isinstance(value, list):
            for ordinal, child in enumerate(value):
                collect(child, f"{path}/{ordinal}")

    collect(artifact)
    oracle.require(
        observed_paths
        == {
            "$/activation_assertions/installed_tenant_rls_table_count": 66,
            "$/task8_fdd_contract/installed_partition/prior_tenant_rls_table_count": 64,
            "$/task8_fdd_contract/installed_partition/successor_tenant_rls_table_count": 66,
        },
        "TASK8_V27_INSTALLED_RLS_SURFACE_INVENTORY_DRIFT",
    )


def test_v27_preserves_unrelated_physical_and_graph_values_of_67() -> None:
    artifact, materializer = oracle.build_candidate()
    oracle.require(
        artifact["erasure_baseline"]["physical_table_count"] == 67,
        "TASK8_V27_ERASURE_PHYSICAL_COUNT_DRIFT",
    )
    oracle.require(
        artifact["activation_assertions"]["baseline_physical_table_count"] == 67,
        "TASK8_V27_ACTIVATION_BASELINE_PHYSICAL_COUNT_DRIFT",
    )
    graph = materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=artifact,
        policy_artifact=oracle.load_json(
            oracle.POLICY_V2_ARTIFACT,
            "TASK8_V27_POLICY_V2_MISSING",
        ),
    )
    oracle.require(
        graph["counts"]
        == {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "TASK8_V27_GRAPH_COUNTS_DRIFT",
    )


def test_v27_application_grants_never_include_alembic_metadata() -> None:
    artifact, _materializer = oracle.build_candidate()
    app_grants = [
        value
        for value in oracle.walk_strings(artifact)
        if "GRANT " in value and "<configured_app_role>" in value
    ]
    oracle.require(bool(app_grants), "TASK8_V27_APPLICATION_GRANT_CENSUS_EMPTY")
    oracle.require(
        all("alembic_version" not in statement for statement in app_grants),
        "TASK8_V27_ALEMBIC_APPLICATION_PRIVILEGE_FORBIDDEN",
    )
    oracle.require(
        not set(oracle.EXCLUDED_APPLICATION_RELATION_TUPLE).intersection(
            oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE
        ),
        "TASK8_V27_ALEMBIC_APPLICATION_DOMAIN_FORBIDDEN",
    )


def test_v27_count_evidence_hashes_are_sha256_literals() -> None:
    for digest in (
        oracle.V27_INVENTORY_SHA256,
        oracle.V26_HISTORICAL_INVENTORY_SHA256,
        oracle.V27_PHYSICAL_CATALOG_SHA256,
    ):
        oracle.require(
            len(digest) == 64 and hashlib.sha256(bytes.fromhex(digest)).digest_size == 32,
            "TASK8_V27_COUNT_EVIDENCE_HASH_INVALID",
        )
