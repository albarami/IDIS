"""Regression contract for retiring superseded pre-freeze execution harnesses.

The archived modules remain on disk as immutable evidence.  They are not
current acceptance tests: v9-v15 were voided before publication, while the
v16 modules named here authenticate candidate bytes that were intentionally
replaced by the signed frozen artifact during publication.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from tests.slice100_task7 import conftest as task7_conftest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADMIN_URL_BASELINE = os.environ.get("IDIS_DATABASE_ADMIN_URL")

_EXPECTED_ARCHIVED_MODULES = frozenset(
    {
        "test_full_strict_v9_current_guard_postgres_census.py",
        "test_full_strict_v10_current_guard_postgres_census.py",
        "test_full_strict_v11_current_guard_postgres_census.py",
        "test_full_strict_v12_current_guard_postgres_census.py",
        "test_full_strict_v13_atomic_admission_red.py",
        "test_full_strict_v13_current_guard_postgres_census.py",
        "test_full_strict_v14_current_guard_postgres_census.py",
        "test_full_strict_v8_current_guard_postgres_census_red.py",
        "test_task7_v19_erasure_readiness.py",
        "test_full_strict_v16_current_guard_postgres_census.py",
        "test_full_strict_v16_task6_before_generated_postgres_red.py",
    }
)
_EXPECTED_ARCHIVED_NODE_SUFFIXES = frozenset(
    {
        (
            "test_full_strict_v2_registry_red.py::"
            "test_exact_census_and_separately_reviewed_activation_enable_admission"
        ),
        ("test_full_strict_v2_registry_red.py::test_wrong_proof_hash_is_rejected_after_activation"),
        (
            "test_full_strict_v13_materialization_red.py::"
            "test_v13_generated_columns_are_derived_only_from_current_ddl"
        ),
        (
            "test_full_strict_v13_materialization_red.py::"
            "test_v13_semantic_vectors_and_candidate_state_are_invariant"
        ),
        (
            "test_full_strict_v8_current_guard_authority_red.py::"
            "test_v8_fixture_is_bound_to_the_executing_postgres_census"
        ),
        (
            "test_full_strict_v8_current_guard_authority_red.py::"
            "test_v8_corruption_in_every_relevant_section_changes_contract_hash"
            "[semantic_step_registry]"
        ),
        (
            "test_full_strict_v8_current_guard_authority_red.py::"
            "test_v8_corruption_in_every_relevant_section_changes_contract_hash"
            "[usage_route_registry]"
        ),
    }
)


def test_archived_pre_freeze_module_inventory_is_exact() -> None:
    """Only the reviewed historical candidate executors leave current collection."""

    assert task7_conftest.ARCHIVED_PREFREEZE_MODULES == _EXPECTED_ARCHIVED_MODULES
    task7_root = _REPO_ROOT / "tests" / "slice100_task7"
    assert all((task7_root / name).is_file() for name in _EXPECTED_ARCHIVED_MODULES)


def test_archived_pre_freeze_modules_are_ignored_after_v21_freeze() -> None:
    """The directory suite cannot execute known-void/frozen-candidate bytes."""

    task7_root = _REPO_ROOT / "tests" / "slice100_task7"
    for name in sorted(_EXPECTED_ARCHIVED_MODULES):
        assert task7_conftest.pytest_ignore_collect(task7_root / name, None) is True


def test_current_and_historical_integrity_suites_remain_collected() -> None:
    """The boundary cannot hide current v21 or immutable-history verification."""

    task7_root = _REPO_ROOT / "tests" / "slice100_task7"
    required_current = (
        "test_task7_v21_runtime_retarget_red.py",
        "test_full_strict_v21_materialization_red.py",
        "test_full_strict_v16_atomic_admission_red.py",
        "test_task7_void_contract_hash_blocklist.py",
    )
    for name in required_current:
        assert task7_conftest.pytest_ignore_collect(task7_root / name, None) is None


def test_task7_snapshot_has_no_task9_periphery_import_dependency() -> None:
    """A reviewed Task-7 checkout must collect before uncommitted Track B lands."""

    task7_root = _REPO_ROOT / "tests" / "slice100_task7"
    offenders: list[str] = []
    for path in sorted(task7_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        if any(module.startswith("idis.services.enrichment.task9") for module in imported_modules):
            offenders.append(path.name)
    assert offenders == []


def test_only_exact_void_candidate_nodes_are_retired_from_mixed_modules() -> None:
    """Useful v13 static checks stay collected while its known blocker stays historical."""

    assert task7_conftest.ARCHIVED_PREFREEZE_NODEID_SUFFIXES == _EXPECTED_ARCHIVED_NODE_SUFFIXES

    class _Item:
        def __init__(self, nodeid: str) -> None:
            self.nodeid = nodeid
            self.marker_count = 0

        def add_marker(self, marker: object) -> None:
            del marker
            self.marker_count += 1

    items = [
        _Item(f"tests/slice100_task7/{suffix}")
        for suffix in sorted(_EXPECTED_ARCHIVED_NODE_SUFFIXES)
    ]
    current = _Item(
        "tests/slice100_task7/test_full_strict_v21_materialization_red.py::"
        "test_v21_candidate_is_frozen"
    )
    items.append(current)

    task7_conftest.pytest_collection_modifyitems(None, items)

    assert [item.marker_count for item in items[:-1]] == [1] * len(_EXPECTED_ARCHIVED_NODE_SUFFIXES)
    assert current.marker_count == 0


def test_environment_isolation_probe_a_mutates_existing_database_setting() -> None:
    """Deliberately emulate a strict-profile test that bypasses monkeypatch."""

    os.environ["IDIS_DATABASE_ADMIN_URL"] = "postgresql://synthetic:secret@db/test-only"


def test_environment_isolation_probe_b_observes_the_original_setting() -> None:
    """The autouse boundary must undo the preceding test's direct mutation."""

    assert os.environ.get("IDIS_DATABASE_ADMIN_URL") == _ADMIN_URL_BASELINE
