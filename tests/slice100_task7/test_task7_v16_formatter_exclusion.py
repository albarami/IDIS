"""Narrow formatter-only protection for append-frozen harness bytes."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from tests.slice100_task8._task8_void_contract_hash_evidence import (
    VOID_V26_FROZEN_FILE_SHA256_BY_PATH,
)

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
_V27_BOUNDARY_RELATIVE_PATH = "tests/slice100_task8/test_v27_fixture_derivation_boundaries_red.py"
_EXPECTED_V27_BOUNDARY_CASE_COUNT = 48
_V27_COLLECTION_TIMEOUT_SECONDS = 120
_TASK7_FORMATTER_EXCLUSIONS = (
    "scripts/generate_task7_v6_contract_fixture.py",
    "scripts/generate_task7_v9_contract_fixture.py",
    "scripts/generate_task7_v21_contract_fixture.py",
    "scripts/materialize_full_strict_v21_preimage.py",
    "scripts/run_task7_v11_current_guard_census.py",
    "scripts/run_task7_v21_current_guard_census.py",
    "scripts/run_task7_v9_current_guard_census.py",
    "tests/slice100_task7/test_full_strict_v9_native_schema_red.py",
)
_TASK8_FORMATTER_EXCLUSIONS = (
    "scripts/materialize_full_strict_v26_preimage.py",
    "scripts/run_task8_v26_signed_census.py",
    "scripts/run_task8_v26_sql_authority_census.py",
    "tests/slice100_task8/test_full_strict_v26_materialization_red.py",
    "tests/slice100_task8/test_v26_bound_runtime_graph_red.py",
    "tests/slice100_task8/test_v26_sql_authority_postgres_census_red.py",
)
_EXPECTED_FORMATTER_EXCLUSIONS = _TASK7_FORMATTER_EXCLUSIONS + _TASK8_FORMATTER_EXCLUSIONS
_TASK8_TRACKED_PINS = {
    path: digest
    for path, digest in VOID_V26_FROZEN_FILE_SHA256_BY_PATH
    if not path.startswith(".local_reports/")
}
_EXPECTED_LINT_EXCLUSIONS = {
    "scripts/run_task7_v11_current_guard_census.py": ["B009", "B010", "F401"],
    "scripts/run_task7_v9_current_guard_census.py": ["I001"],
    "scripts/run_task8_v26_sql_authority_census.py": ["E501", "I001"],
    "tests/slice100_task8/test_v26_sql_authority_postgres_census_red.py": [
        "E501",
        "F841",
        "I001",
        "SIM300",
    ],
}


def _signed_manifest_paths() -> frozenset[str]:
    tree = ast.parse(_RUNNER.read_text("utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "HARNESS_SOURCE_MANIFEST"
            and node.value is not None
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
    assert set(formatter_exclusions) <= (
        _signed_manifest_paths() | _v21_signed_manifest_paths() | _TASK8_TRACKED_PINS.keys()
    )
    assert not set(formatter_exclusions) & set(config.get("exclude", ()))
    assert all((_REPO_ROOT / path).is_file() for path in formatter_exclusions)


def test_lint_exclusion_is_exact_signed_byte_protection_not_global_masking() -> None:
    config = tomllib.loads(_PYPROJECT.read_text("utf-8"))["tool"]["ruff"]
    per_file_ignores = config["lint"].get("per-file-ignores", {})

    assert per_file_ignores == _EXPECTED_LINT_EXCLUSIONS
    assert set(per_file_ignores) <= (_signed_manifest_paths() | _TASK8_TRACKED_PINS.keys())
    assert set(per_file_ignores) <= set(_EXPECTED_FORMATTER_EXCLUSIONS)
    assert all((_REPO_ROOT / path).is_file() for path in per_file_ignores)
    assert all(
        code in {"B009", "B010", "E501", "F401", "F841", "I001", "SIM300"}
        for codes in per_file_ignores.values()
        for code in codes
    )


def _assert_task8_tracked_frozen_bytes(root: Path) -> None:
    for path, digest in _TASK8_TRACKED_PINS.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, path


def test_task8_tracked_frozen_byte_guard_requires_no_local_reports() -> None:
    config = tomllib.loads(_PYPROJECT.read_text("utf-8"))["tool"]["ruff"]
    exclusions = tuple(config["format"].get("exclude", ()))
    assert exclusions == _EXPECTED_FORMATTER_EXCLUSIONS
    assert set(_TASK8_FORMATTER_EXCLUSIONS) <= _TASK8_TRACKED_PINS.keys()
    assert not set(exclusions) & set(config.get("exclude", ()))
    assert config.get("exclude") == ["docs"]
    assert not config.get("extend-exclude")
    assert config["lint"].get("ignore") == ["UP038"]
    assert not config["lint"].get("extend-ignore")
    _assert_task8_tracked_frozen_bytes(_REPO_ROOT)


@pytest.mark.parametrize("path", _TASK8_FORMATTER_EXCLUSIONS)
@pytest.mark.parametrize("missing", (False, True))
def test_task8_tracked_byte_guard_rejects_drift_or_missing_source(
    path: str, missing: bool, tmp_path: Path
) -> None:
    for relative_path in _TASK8_TRACKED_PINS:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (missing and relative_path == path):
            raw = (_REPO_ROOT / relative_path).read_bytes()
            target.write_bytes(raw + b" " if relative_path == path else raw)
    with pytest.raises((AssertionError, FileNotFoundError)):
        _assert_task8_tracked_frozen_bytes(tmp_path)


def test_check_targets_verify_format_without_changing_explicit_format_targets() -> None:
    makefile = (_REPO_ROOT / "Makefile").read_text("utf-8")
    batch = (_REPO_ROOT / "make.bat").read_text("utf-8")
    assert "format:\n\truff format .\n" in makefile
    assert (
        "check:\n\truff format --check .\n\truff check .\n"
        "\tmypy src/idis --ignore-missing-imports\n\tpytest\n"
        "\tpython scripts/forbidden_scan.py\n"
    ) in makefile
    explicit_format = batch.split(":format\n", 1)[1].split("\n:lint", 1)[0]
    assert '"%PYTHON_BIN%" -m ruff format .\n' in explicit_format
    assert ':format_internal\n"%PYTHON_BIN%" -m ruff format --check .\n' in batch
    for name in ("format", "lint", "typecheck", "test", "forbidden_scan"):
        assert f"call :{name}_internal\nif errorlevel 1 exit /b 1" in batch


@pytest.mark.parametrize(
    "path",
    (
        "src/active.py",
        "tests/active.py",
        "scripts/run_task8_v27_sql_authority_census.py",
        "tests/slice100_task8/test_v27_fixture_derivation_red.py",
    ),
)
def test_active_source_formatting_and_lint_remain_nonmutating_failures(
    path: str, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_bytes(_PYPROJECT.read_bytes())
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    raw = ("value=missing_name\nlong_value = '" + "x" * 101 + "'\n").encode()
    source.write_bytes(raw)
    formatting = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", "--no-cache", "."],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format=json", "."],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert formatting.returncode == lint.returncode == 1
    assert {row["code"] for row in json.loads(lint.stdout)} >= {"F821", "E501"}
    assert source.read_bytes() == raw


def test_v27_split_boundary_module_remains_lf_and_collected() -> None:
    module = _REPO_ROOT / _V27_BOUNDARY_RELATIVE_PATH
    assert module.is_file(), "TASK8_V27_BOUNDARY_TEST_MODULE_MISSING"
    raw = module.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n"), "TASK8_V27_BOUNDARY_TEST_MODULE_NOT_LF"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_socket",
            "--disable-socket",
            "--allow-hosts=127.0.0.1,::1",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            _V27_BOUNDARY_RELATIVE_PATH,
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=_V27_COLLECTION_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, "TASK8_V27_BOUNDARY_TEST_COLLECTION_FAILED"
    nodeids = tuple(
        line
        for line in result.stdout.splitlines()
        if line.startswith(f"{_V27_BOUNDARY_RELATIVE_PATH}::")
    )
    assert len(nodeids) == len(set(nodeids)) == _EXPECTED_V27_BOUNDARY_CASE_COUNT, (
        "TASK8_V27_BOUNDARY_TEST_CASE_COUNT_INVALID"
    )
