"""Task-7 test lifecycle boundaries."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest

_PREFREEZE_CENSUS_TEST = "test_full_strict_v16_current_guard_postgres_census.py"
ARCHIVED_PREFREEZE_MODULES: Final[frozenset[str]] = frozenset(
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
        _PREFREEZE_CENSUS_TEST,
        "test_full_strict_v16_task6_before_generated_postgres_red.py",
    }
)
ARCHIVED_PREFREEZE_NODEID_SUFFIXES: Final[frozenset[str]] = frozenset(
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
_PREFREEZE_CODEC_NODEID_SUFFIX = (
    "test_task7_v16_harness_source_closure.py::test_local_candidate_codec_matches_reviewed_oracle"
)
_V16_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
)
_V21_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
)


def _is_append_frozen(path: Path) -> bool:
    try:
        artifact = json.loads(path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return artifact.get("metadata", {}).get("append_frozen") is True


@pytest.fixture(autouse=True)
def _restore_idis_environment_after_each_test() -> Iterator[None]:
    """Prevent direct strict-profile mutations from contaminating later tests."""

    snapshot = {name: value for name, value in os.environ.items() if name.startswith("IDIS")}
    try:
        yield
    finally:
        for name in tuple(os.environ):
            if name.startswith("IDIS"):
                os.environ.pop(name, None)
        os.environ.update(snapshot)


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:
    """Preserve superseded pre-freeze bytes without executing them as current tests."""

    del config
    name = collection_path.name
    if name == _PREFREEZE_CENSUS_TEST and _is_append_frozen(_V16_ARTIFACT):
        return True
    if name in ARCHIVED_PREFREEZE_MODULES and _is_append_frozen(_V21_ARTIFACT):
        return True
    return None


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Retire only the pre-freeze candidate-state assertion after publication.

    The containing source file remains byte-pinned by the signed receipt, so its
    other closure tests continue to run.  The frozen-state and exact prospective
    transition are proved by the v16 fixture/publisher suites.
    """

    del config
    artifact = json.loads(_V16_ARTIFACT.read_bytes())
    if artifact["metadata"]["append_frozen"] is not True:
        return
    marker = pytest.mark.skip(
        reason=(
            "pre-freeze candidate-state assertion retired after the signed atomic "
            "v16 freeze; protected source bytes remain receipt-pinned"
        )
    )
    for item in items:
        if item.nodeid.replace("\\", "/").endswith(_PREFREEZE_CODEC_NODEID_SUFFIX):
            item.add_marker(marker)

    if not _is_append_frozen(_V21_ARTIFACT):
        return
    archived_marker = pytest.mark.skip(
        reason=(
            "void v13 candidate blocker retained as historical RED evidence; "
            "current acceptance starts from the frozen v21 authority"
        )
    )
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if any(nodeid.endswith(suffix) for suffix in ARCHIVED_PREFREEZE_NODEID_SUFFIXES):
            item.add_marker(archived_marker)
