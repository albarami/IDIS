"""RED-first publication safety for the standalone Task-8 policy builder."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ._artifact_oracle import (
    OPERATOR_WORKBOOK,
    POLICY_ARTIFACT,
    POLICY_FIXTURE,
    REVIEW_MAP,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER = REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v1.py"
_OWNED_PRIVACY_TESTS = (
    Path(__file__),
    Path(__file__).with_name("test_task8_atomic_publication_red.py"),
    Path(__file__).with_name("test_task8_fixture_derivation_red.py"),
)
_PRIVATE_ASSERTION_TOKENS = (
    "stdout",
    "stderr",
    "sidecar_raw",
    "emitted",
    "formatted",
    "private_canary",
    "canary",
    "captured.out",
    "captured.err",
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_task8_policy_publication_red",
        MATERIALIZER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _retarget(module: ModuleType, tmp_path: Path) -> dict[str, Path]:
    targets = {
        "fixture": tmp_path / "fixtures" / "fdd_formula_policy_v1_hash_v1.json",
        "review_map": tmp_path / "plans" / "fdd_formula_policy_v1_review_map.md",
        "artifact": tmp_path / "contracts" / "fdd_formula_policy_v1.json",
    }
    for target in targets.values():
        target.parent.mkdir(parents=True, exist_ok=True)
    module.POLICY_FIXTURE = targets["fixture"]
    module.REVIEW_MAP = targets["review_map"]
    module.POLICY_ARTIFACT = targets["artifact"]
    return targets


def _assert_safe_exact_bytes(actual: bytes, expected: bytes, code: str) -> None:
    if actual != expected:
        actual_sha256 = hashlib.sha256(actual).hexdigest()
        expected_sha256 = hashlib.sha256(expected).hexdigest()
        raise AssertionError(
            f"{code}:actual_sha256={actual_sha256}:expected_sha256={expected_sha256}"
        )


def _safe_text_snapshot(value: str) -> tuple[int, str]:
    raw = value.encode("utf-8")
    return len(raw), hashlib.sha256(raw).hexdigest()


def _assert_safe_json_equal(actual: object, expected: object, code: str) -> None:
    actual_raw = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_raw = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _assert_safe_exact_bytes(actual_raw, expected_raw, code)


def test_task8_private_outputs_never_enter_pytest_assert_rewriting() -> None:
    violations: list[str] = []
    for path in _OWNED_PRIVACY_TESTS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            expression = ast.get_source_segment(source, node.test) or ""
            if any(token in expression for token in _PRIVATE_ASSERTION_TOKENS):
                violations.append(f"{path.name}:{node.lineno}")
    if violations:
        raise AssertionError("TASK8_PRIVATE_ASSERTION_OUTPUT_FORBIDDEN:" + ",".join(violations))


def test_safe_exact_byte_comparison_never_echoes_private_payload() -> None:
    canary = b"PRIVATE_WORKBOOK_ROW_PROSE_CANARY"

    with pytest.raises(AssertionError) as raised:
        _assert_safe_exact_bytes(canary, b"different", "TASK8_PRIVATE_BYTES_DIFFER")

    emitted = str(raised.value)
    if "TASK8_PRIVATE_BYTES_DIFFER" not in emitted:
        raise AssertionError("TASK8_PRIVATE_SAFE_CODE_MISSING")
    if canary.decode("ascii") in emitted:
        raise AssertionError("TASK8_PRIVATE_BYTES_EMITTED")


def test_standalone_policy_publication_is_fixture_map_then_artifact_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    targets = _retarget(module, tmp_path)
    observed: list[Path] = []
    real_write = module._write_exclusive

    def record_write(path: Path, raw: bytes) -> None:
        real_write(path, raw)
        observed.append(Path(path))

    monkeypatch.setattr(module, "_write_exclusive", record_write)
    result = module.materialize(OPERATOR_WORKBOOK)

    assert observed == [
        targets["fixture"],
        targets["review_map"],
        targets["artifact"],
    ]
    assert result["publication_order"] == [
        "fdd_formula_policy_fixture",
        "fdd_formula_policy_review_map",
        "fdd_formula_policy_artifact",
    ]
    assert result["commit_point"] == "fdd_formula_policy_artifact"
    assert result["activation_status"] == "INACTIVE"


def test_standalone_policy_publication_preflights_differing_target_before_write(
    tmp_path: Path,
) -> None:
    module = _module()
    targets = _retarget(module, tmp_path)
    targets["artifact"].write_bytes(b"different-preexisting-bytes")

    with pytest.raises(
        module.MaterializationError,
        match="TASK8_POLICY_PUBLICATION_TARGET_MISMATCH",
    ):
        module.materialize(OPERATOR_WORKBOOK)

    assert targets["artifact"].read_bytes() == b"different-preexisting-bytes"
    assert not targets["fixture"].exists()
    assert not targets["review_map"].exists()


def test_standalone_policy_exact_replay_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    targets = _retarget(module, tmp_path)
    first = module.materialize(OPERATOR_WORKBOOK)
    before = {
        name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for name, path in targets.items()
    }

    def must_not_write(_path: Path, _raw: bytes) -> None:
        raise AssertionError("TASK8_POLICY_EXACT_REPLAY_REWROTE_FROZEN_BYTES")

    monkeypatch.setattr(module, "_write_exclusive", must_not_write)
    second = module.materialize(OPERATOR_WORKBOOK)

    assert second == first
    after = {
        name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for name, path in targets.items()
    }
    if after != before:
        raise AssertionError("TASK8_POLICY_EXACT_REPLAY_OUTPUT_DRIFT")


def test_standalone_policy_rejects_partial_or_symlink_publication_state(
    tmp_path: Path,
) -> None:
    module = _module()
    targets = _retarget(module, tmp_path / "partial")
    targets["fixture"].write_bytes(b"partial")
    with pytest.raises(
        module.MaterializationError,
        match="TASK8_POLICY_PUBLICATION_TARGET_MISMATCH",
    ):
        module.materialize(OPERATOR_WORKBOOK)
    assert not targets["artifact"].exists()

    module = _module()
    targets = _retarget(module, tmp_path / "symlink")
    external = tmp_path / "external.json"
    external.write_bytes(b"external")
    try:
        targets["artifact"].symlink_to(external)
    except OSError:
        return
    with pytest.raises(
        module.MaterializationError,
        match="TASK8_POLICY_PUBLICATION_PATH_INVALID",
    ):
        module.materialize(OPERATOR_WORKBOOK)
    assert external.read_bytes() == b"external"


def test_standalone_policy_result_contains_only_safe_hashes_and_gate_metadata(
    tmp_path: Path,
) -> None:
    module = _module()
    _retarget(module, tmp_path)
    result: dict[str, Any] = module.materialize(OPERATOR_WORKBOOK)

    assert set(result) == {
        "artifact_sha256",
        "policy_sha256",
        "review_map_sha256",
        "fixture_sha256",
        "publication_order",
        "commit_point",
        "activation_status",
    }
    for field in (
        "artifact_sha256",
        "policy_sha256",
        "review_map_sha256",
        "fixture_sha256",
    ):
        assert len(result[field]) == 64


def test_policy_builder_exposes_exact_frozen_production_publication_bundle() -> None:
    module = _module()
    bundle = module.build_policy_publication_bundle(OPERATOR_WORKBOOK)

    assert set(bundle) == {
        "policy_artifact_raw",
        "policy_fixture_raw",
        "policy_review_map_raw",
    }
    _assert_safe_exact_bytes(
        bundle["policy_artifact_raw"],
        POLICY_ARTIFACT.read_bytes(),
        "TASK8_POLICY_ARTIFACT_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        bundle["policy_fixture_raw"],
        POLICY_FIXTURE.read_bytes(),
        "TASK8_POLICY_FIXTURE_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        bundle["policy_review_map_raw"],
        REVIEW_MAP.read_bytes(),
        "TASK8_POLICY_REVIEW_MAP_BYTES_DIFFER",
    )
    policy = json.loads(bundle["policy_artifact_raw"])
    assert policy["metadata"]["append_frozen"] is True
    assert policy["metadata"]["activation_status"] == "INACTIVE"


def test_policy_cli_missing_input_emits_only_stable_safe_failure(
    tmp_path: Path,
) -> None:
    canary = "PRIVATE_WORKBOOK_ROW_PROSE_CANARY"
    missing = tmp_path / f"{canary}.xlsx"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(MATERIALIZER),
            "--workbook",
            str(missing),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    if result.returncode != 2:
        raise AssertionError(f"TASK8_POLICY_CLI_EXIT_INVALID:{result.returncode}")
    if result.stderr:
        size, digest = _safe_text_snapshot(result.stderr)
        raise AssertionError(f"TASK8_POLICY_CLI_STDERR_NOT_EMPTY:{size}:{digest}")
    payload = json.loads(result.stdout)
    _assert_safe_json_equal(
        payload,
        {
            "reason_code": "TASK8_POLICY_INPUT_UNAVAILABLE",
            "result": "FAILED",
        },
        "TASK8_POLICY_CLI_FAILURE_PAYLOAD_INVALID",
    )
    emitted = result.stdout + result.stderr
    if canary in emitted or str(tmp_path) in emitted:
        raise AssertionError("TASK8_POLICY_CLI_PRIVATE_OUTPUT_LEAK")


def test_policy_cli_unexpected_failure_redacts_exception_prose(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    canary = "PRIVATE_WORKBOOK_ROW_PROSE_CANARY"

    def explode(_workbook: Path) -> dict[str, bytes]:
        raise RuntimeError(f"{canary}:C:\\private\\source.xlsx")

    monkeypatch.setattr(module, "build_policy_publication_bundle", explode)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(MATERIALIZER), "--workbook", str(OPERATOR_WORKBOOK)],
    )
    assert module.main() == 2
    captured = capsys.readouterr()
    if captured.err:
        size, digest = _safe_text_snapshot(captured.err)
        raise AssertionError(f"TASK8_POLICY_MAIN_STDERR_NOT_EMPTY:{size}:{digest}")
    _assert_safe_json_equal(
        json.loads(captured.out),
        {
            "reason_code": "TASK8_POLICY_MATERIALIZATION_FAILED",
            "result": "FAILED",
        },
        "TASK8_POLICY_MAIN_FAILURE_PAYLOAD_INVALID",
    )
    combined = captured.out + captured.err
    if canary in combined or "private" in combined.lower():
        raise AssertionError("TASK8_POLICY_MAIN_PRIVATE_OUTPUT_LEAK")
