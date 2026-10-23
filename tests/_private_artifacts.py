"""Inapplicability gate for one closed list of private slice100 artifacts.

A present file runs the wrapped test unchanged, including its identity checks.
Absence prints one ``PRIVATE_ARTIFACT NOT_EXECUTED: <relative path> absent``
line per missing path, records that line, and returns. Required mode
``IDIS_REQUIRE_PRIVATE_ARTIFACTS=1`` fails the node. The wrapper never skips.
"""

from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SLICE100 = Path(".local_reports") / "slice100"
_REQUIRE_PRIVATE_ARTIFACTS_ENV = "IDIS_REQUIRE_PRIVATE_ARTIFACTS"
_RECORD_PROPERTY: Callable[[str, object], None] | None = None

PRIVATE_ARTIFACT_GATED_NODES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_v20_authorizer_finalizes_exact_census_runner_source_and_receipt_pins",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_full_strict_v17_materialization_red.py",
        "test_v16_frozen_authority_remains_byte_immutable_before_v17_materialization",
        ("task7_v16_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_task7_v16_reconciliation_history.py",
        "test_v16_source_supersession_signed_census_and_freeze_are_append_only",
        ("task7_v16_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_task7_v17_supersession_history_red.py",
        "test_v17_successor_history_is_separate_and_preserves_v16_history_bytes",
        ("task7_contract_materialization_history_v2_v16.safe.json",),
    ),
    (
        "tests/slice100_task7/test_task7_v20_publication_history.py",
        "test_rejected_and_accepted_receipts_are_distinct_append_only_evidence",
        ("task7_v20_current_guard_postgres_census_receipt.rejected-null-proof-projection.json",),
    ),
    (
        "tests/slice100_task8/test_task8_v25_void_boundary_red.py",
        "test_v25_materializer_verifier_oracles_tests_and_golden_are_byte_frozen",
        ("task8/v25-candidate/v25_independent_fixture_red.txt",),
    ),
    (
        "tests/slice100_task8/test_task8_v25_void_boundary_red.py",
        "test_v25_canonical_outputs_locks_receipts_and_contract_lock_entries_are_absent",
        ("task8/v25-candidate/v25_independent_fixture_red.txt",),
    ),
    (
        "tests/slice100_task8/test_task8_v26_void_boundary_red.py",
        "test_v26_rendered_candidate_identities_are_void_but_equation_v3_hashes_are_reusable",
        (
            "task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json",
            "task8/v26-candidate/full_strict_v26_contract_hash_v1.json",
        ),
    ),
    (
        "tests/slice100_task8/test_task8_v26_void_boundary_red.py",
        "test_v26_sources_execution_suites_and_staged_candidate_are_byte_frozen",
        (
            "task8/v26-candidate/full_strict_v26_reviewed_preimage_v1.json",
            "task8/v26-candidate/full_strict_v26_contract_hash_v1.json",
        ),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_real_v20_receipt_verifies_31_field_projection_and_authorizes_fixture",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_v20_receipt_pin_drift_fails_before_signature_verification",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_v20_source_manifest_pin_drift_rejects_the_real_receipt",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_v20_signed_payload_drift_cannot_authorize_fixture",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py",
        "test_v20_untrusted_public_key_cannot_verify_the_real_receipt",
        ("task7_v20_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v21_contract_publication_red.py",
        "test_real_v21_receipt_verifies_and_authorizes_exact_fixture",
        ("task7_v21_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v21_contract_publication_red.py",
        "test_v21_receipt_pin_drift_fails_before_signature_verification",
        ("task7_v21_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v21_contract_publication_red.py",
        "test_v21_source_manifest_pin_drift_rejects_the_real_receipt",
        ("task7_v21_current_guard_postgres_census_receipt.json",),
    ),
    (
        "tests/slice100_task7/test_authorize_task7_v21_contract_publication_red.py",
        "test_v21_untrusted_public_key_cannot_verify_the_real_receipt",
        ("task7_v21_current_guard_postgres_census_receipt.json",),
    ),
)


def private_artifact_path(relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``REPO_ROOT/.local_reports/slice100``."""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("private artifact path must stay under slice100")
    return REPO_ROOT / _SLICE100 / relative


def _absent_line(relative_path: str) -> str:
    return f"PRIVATE_ARTIFACT NOT_EXECUTED: {relative_path} absent"


def _record_absent_private_artifact(
    relative_path: str,
    record_property: Callable[[str, object], None] | None,
) -> str:
    """Print and record one absence line. Never skips."""

    line = _absent_line(relative_path)
    print(line)
    if record_property is not None:
        record_property("PRIVATE_ARTIFACT", line)
    return line


def _published_signature(test_fn: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(test_fn)
    if "record_property" in signature.parameters:
        return signature
    parameters = [
        *signature.parameters.values(),
        inspect.Parameter("record_property", inspect.Parameter.KEYWORD_ONLY),
    ]
    return signature.replace(parameters=parameters)


def requires_private_artifact(
    *relative_paths: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Run the test when every listed slice100 artifact is present.

    Absence prints one ``PRIVATE_ARTIFACT NOT_EXECUTED`` line per missing path,
    records it with ``record_property``, and returns. ``IDIS_REQUIRE_PRIVATE_ARTIFACTS=1``
    fails the node. The wrapper never skips.
    """

    if not relative_paths:
        raise ValueError("requires_private_artifact requires at least one path")

    def _decorate(test_fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(test_fn)
        accepts_recorder = "record_property" in signature.parameters

        @functools.wraps(test_fn)
        def _run(*args: Any, **kwargs: Any) -> Any:
            supplied = kwargs.get("record_property")
            recorder = supplied if isinstance(supplied, Callable) else _RECORD_PROPERTY
            missing = [
                relative
                for relative in relative_paths
                if not private_artifact_path(relative).is_file()
            ]
            if not missing:
                call_kwargs = kwargs
                if not accepts_recorder:
                    call_kwargs = {
                        key: value for key, value in kwargs.items() if key != "record_property"
                    }
                return test_fn(*args, **call_kwargs)
            for relative in missing:
                _record_absent_private_artifact(relative, recorder)
            if os.environ.get(_REQUIRE_PRIVATE_ARTIFACTS_ENV) == "1":
                pytest.fail("\n".join(_absent_line(relative) for relative in missing))
            return None

        _run.__signature__ = _published_signature(test_fn)  # type: ignore[attr-defined]
        # Pytest follows __wrapped__ back to the undecorated signature, which
        # would drop the injected record_property fixture.
        del _run.__wrapped__
        return _run

    return _decorate
