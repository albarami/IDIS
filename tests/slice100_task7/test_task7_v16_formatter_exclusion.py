"""Narrow formatter-only protection for append-frozen harness bytes."""

from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
_V21_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v21_current_guard_census.py"
_V21_RECEIPT = (
    _REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task7_v21_current_guard_postgres_census_receipt.json"
)
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_EXPECTED_FORMATTER_EXCLUSIONS = (
    "scripts/generate_task7_v6_contract_fixture.py",
    "scripts/generate_task7_v9_contract_fixture.py",
    "scripts/generate_task7_v21_contract_fixture.py",
    "scripts/materialize_full_strict_v21_preimage.py",
    "scripts/run_task7_v11_current_guard_census.py",
    "scripts/run_task7_v21_current_guard_census.py",
    "scripts/run_task7_v9_current_guard_census.py",
    "tests/slice100_task7/test_full_strict_v9_native_schema_red.py",
)
_EXPECTED_LINT_EXCLUSIONS = {
    "scripts/run_task7_v11_current_guard_census.py": ["B009", "B010", "F401"],
    "scripts/run_task7_v9_current_guard_census.py": ["I001"],
}


def _signed_manifest_paths() -> frozenset[str]:
    tree = ast.parse(_RUNNER.read_text("utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "HARNESS_SOURCE_MANIFEST"
        ):
            rows = ast.literal_eval(node.value)
            return frozenset(str(row[0]) for row in rows)
    raise AssertionError("HARNESS_SOURCE_MANIFEST not found")


def _v21_signed_manifest_paths() -> frozenset[str]:
    receipt = json.loads(_V21_RECEIPT.read_text("utf-8"))
    census = receipt["payload"]["census_result"]
    assert (
        hashlib.sha256(_V21_RUNNER.read_bytes()).hexdigest()
        == (census["v21_adapter_source_sha256"])
    )
    return frozenset(
        {
            *(str(row[0]) for row in census["materialization_source_manifest_tuple"]),
            "scripts/run_task7_v21_current_guard_census.py",
        }
    )


def test_formatter_exclusion_is_exact_signed_byte_protection_not_lint_masking() -> None:
    config = tomllib.loads(_PYPROJECT.read_text("utf-8"))["tool"]["ruff"]
    formatter_exclusions = tuple(config["format"].get("exclude", ()))

    assert formatter_exclusions == _EXPECTED_FORMATTER_EXCLUSIONS
    assert set(formatter_exclusions) <= (_signed_manifest_paths() | _v21_signed_manifest_paths())
    assert not set(formatter_exclusions) & set(config.get("exclude", ()))
    assert all((_REPO_ROOT / path).is_file() for path in formatter_exclusions)


def test_lint_exclusion_is_exact_signed_byte_protection_not_global_masking() -> None:
    config = tomllib.loads(_PYPROJECT.read_text("utf-8"))["tool"]["ruff"]
    per_file_ignores = config["lint"].get("per-file-ignores", {})

    assert per_file_ignores == _EXPECTED_LINT_EXCLUSIONS
    assert set(per_file_ignores) <= _signed_manifest_paths()
    assert set(per_file_ignores) <= set(_EXPECTED_FORMATTER_EXCLUSIONS)
    assert all((_REPO_ROOT / path).is_file() for path in per_file_ignores)
    assert all(
        code in {"B009", "B010", "F401", "I001"}
        for codes in per_file_ignores.values()
        for code in codes
    )
