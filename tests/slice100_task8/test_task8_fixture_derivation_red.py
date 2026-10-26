"""RED-first staging-only fixture generation for full-strict-v26."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from tests.slice100_task8._task8_void_contract_hash_evidence import (
    TASK8_VOID_CONTRACT_HASHES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_task8_contract_fixtures.py"
VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v26_fixture_independently.py"
MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v26_preimage.py"
PARENT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
POLICY_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v26-candidate"
    / "full_strict_v26_reviewed_preimage_v1.json"
)
CANONICAL_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v26_reviewed_preimage_v1.json"
CANONICAL_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v26_contract_hash_v1.json"
)

EXPECTED_IDENTITIES = {
    "artifact_sha256": "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27",
    "fixture_sha256": "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7",
    "contract_hash": "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc",
    "graph_sha256": "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece",
    "conservation_sha256": "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235",
    "policy_binding_sha256": "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467",
}
EXPECTED_POLICY_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _generator() -> ModuleType:
    return _load_module(GENERATOR, "_task8_v26_fixture_generator_red")


def _verifier() -> ModuleType:
    return _load_module(VERIFIER, "_task8_v26_fixture_generator_verifier")


def _inputs() -> dict[str, bytes]:
    return {
        "parent_artifact_raw": PARENT.read_bytes(),
        "policy_artifact_raw": POLICY.read_bytes(),
        "reviewer_map_raw": REVIEWER_MAP.read_bytes(),
        "v26_artifact_raw": STAGED_ARTIFACT.read_bytes(),
    }


def _derive(module: ModuleType | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    target = _generator() if module is None else module
    fixture, identities = target.derive_task8_fixture(**_inputs())
    assert isinstance(fixture, dict)
    assert isinstance(identities, dict)
    return fixture, identities


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _safe_snapshot(path: Path) -> tuple[bool, int, str | None]:
    if not path.exists():
        return False, 0, None
    raw = path.read_bytes()
    return True, len(raw), hashlib.sha256(raw).hexdigest()


def _cli_args(*extra: str) -> list[str]:
    return [
        sys.executable,
        str(GENERATOR),
        "--parent-artifact",
        str(PARENT),
        "--policy-artifact",
        str(POLICY),
        "--reviewer-map",
        str(REVIEWER_MAP),
        "--staged-artifact",
        str(STAGED_ARTIFACT),
        *extra,
    ]


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _cli_args(*extra),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _main_args(*extra: str) -> list[str]:
    args = _cli_args(*extra)
    return args[args.index("--parent-artifact") :]


def _safe_json(text: str, code: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raise AssertionError(f"{code}:{len(text)}:{digest}") from exc
    if not isinstance(value, dict):
        raise AssertionError(code)
    return value


def test_generator_source_is_v26_only_and_calls_both_validation_paths() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "verify_full_strict_v26_fixture_independently.py" in string_literals
    assert "materialize_full_strict_v26_preimage.py" in string_literals
    assert "full-strict-v26" in string_literals
    assert not any(identifier in source for identifier in TASK8_VOID_CONTRACT_HASHES)
    retired_markers = ("full-strict-v22", "full-strict-v23", "full-strict-v24", "full-strict-v25")
    assert all(marker not in source for marker in retired_markers)

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"validate_frozen_artifact", "derive_fixture", "verify_fixture"} <= calls


def test_derive_pins_exact_fixture_bytes_and_all_six_identities() -> None:
    fixture, identities = _derive()
    independent = _verifier()
    expected_raw = independent.derive_fixture_bytes(**_inputs())

    assert _render(fixture) == expected_raw
    assert identities == EXPECTED_IDENTITIES
    assert hashlib.sha256(expected_raw).hexdigest() == EXPECTED_IDENTITIES["fixture_sha256"]
    assert fixture["artifact_sha256"] == EXPECTED_IDENTITIES["artifact_sha256"]
    assert fixture["FULL_STRICT_V26_CONTRACT_HASH"] == EXPECTED_IDENTITIES["contract_hash"]
    assert (
        fixture["identity_hash_dependency_graph"]["graph_sha256"]
        == EXPECTED_IDENTITIES["graph_sha256"]
    )
    assert (
        fixture["approved_semantics_conservation"]["sha256"]
        == EXPECTED_IDENTITIES["conservation_sha256"]
    )
    assert (
        fixture["policy_binding"]["binding_sha256"] == EXPECTED_IDENTITIES["policy_binding_sha256"]
    )
    assert fixture["activation_status"] == "INACTIVE"


def test_derivation_is_repeatable_and_writes_nothing() -> None:
    before = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))
    first = _derive()
    second = _derive()
    after = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))

    assert first == second
    assert after == before


def test_exact_policy_fixture_is_an_immutable_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    prerequisite = tmp_path / "policy-fixture.json"
    prerequisite.write_bytes(POLICY_FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(module, "_POLICY_FIXTURE_PATH", prerequisite)

    with pytest.raises(
        module.Task8FixtureError,
        match="^TASK8_V26_POLICY_FIXTURE_PREREQUISITE_INVALID$",
    ):
        _derive(module)

    assert hashlib.sha256(POLICY_FIXTURE.read_bytes()).hexdigest() == EXPECTED_POLICY_FIXTURE_SHA256


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("parent_artifact_raw", "TASK8_V26_PARENT_MUST_BE_FROZEN_V21"),
        ("policy_artifact_raw", "TASK8_V26_POLICY_MUST_BE_FROZEN_V2"),
        ("reviewer_map_raw", "TASK8_V26_REVIEWER_MAP_BYTES_DRIFT"),
        ("v26_artifact_raw", "TASK8_V26_CONTRACT_ARTIFACT_INVALID"),
    ),
)
def test_each_frozen_input_rejects_mutated_bytes_with_a_stable_code(field: str, code: str) -> None:
    module = _generator()
    values = _inputs()
    values[field] += b" "
    with pytest.raises(module.Task8FixtureError, match=f"^{code}$"):
        module.derive_task8_fixture(**values)


def test_derive_only_cli_fully_validates_reports_six_identities_and_writes_nothing() -> None:
    before = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))
    result = _run("--derive-only")
    after = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))

    if result.returncode != 0:
        raise AssertionError(f"TASK8_V26_DERIVE_ONLY_EXIT_INVALID:{result.returncode}")
    if result.stderr:
        digest = hashlib.sha256(result.stderr.encode("utf-8")).hexdigest()
        raise AssertionError(
            f"TASK8_V26_DERIVE_ONLY_STDERR_NOT_EMPTY:{len(result.stderr)}:{digest}"
        )
    payload = _safe_json(result.stdout, "TASK8_V26_DERIVE_ONLY_OUTPUT_INVALID")
    assert payload == {
        "activation_status": "INACTIVE",
        **EXPECTED_IDENTITIES,
        "result": "DERIVED",
        "verification_path": "TASK8_V26_PRODUCTION_PLUS_INDEPENDENT_V1",
    }
    assert after == before


def test_optional_staging_write_is_exact_exclusive_and_preserves_policy_fixture(
    tmp_path: Path,
) -> None:
    output = tmp_path / "staging" / "fixture.json"
    policy_before = _safe_snapshot(POLICY_FIXTURE)
    canonical_before = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))

    result = _run("--staging-output", str(output))
    if result.returncode != 0:
        raise AssertionError(f"TASK8_V26_STAGING_EXIT_INVALID:{result.returncode}")
    fixture, _identities = _derive()
    assert output.read_bytes() == _render(fixture)
    assert _safe_snapshot(POLICY_FIXTURE) == policy_before
    canonical_after = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))
    assert canonical_after == canonical_before

    repeated = _run("--staging-output", str(output))
    assert repeated.returncode == 2
    repeated_payload = _safe_json(repeated.stdout, "TASK8_V26_NO_CLOBBER_OUTPUT_INVALID")
    assert repeated_payload == {
        "reason_code": "TASK8_V26_FIXTURE_TARGET_EXISTS",
        "result": "FAILED",
    }
    assert output.read_bytes() == _render(fixture)


@pytest.mark.parametrize("forbidden", (CANONICAL_ARTIFACT, CANONICAL_FIXTURE))
def test_staging_write_refuses_every_canonical_commit_path(forbidden: Path) -> None:
    before = _safe_snapshot(forbidden)
    result = _run("--staging-output", str(forbidden))
    assert result.returncode == 2
    failure = _safe_json(result.stdout, "TASK8_V26_CANONICAL_OUTPUT_INVALID")
    assert failure == {
        "reason_code": "TASK8_V26_CANONICAL_PUBLICATION_PATH_FORBIDDEN",
        "result": "FAILED",
    }
    assert _safe_snapshot(forbidden) == before


def test_canonical_artifact_is_forbidden_as_generator_input(
    tmp_path: Path,
) -> None:
    canonical_input = tmp_path / "canonical-placeholder.json"
    canonical_input.write_bytes(STAGED_ARTIFACT.read_bytes())
    args = _cli_args("--derive-only")
    artifact_index = args.index("--staged-artifact") + 1
    args[artifact_index] = str(canonical_input)

    module = _generator()
    original = module.__dict__["_CANONICAL_ARTIFACT_PATH"]
    module.__dict__["_CANONICAL_ARTIFACT_PATH"] = canonical_input
    try:
        assert module.main(args[args.index("--parent-artifact") :]) == 2
    finally:
        module.__dict__["_CANONICAL_ARTIFACT_PATH"] = original


def test_derive_only_rejects_a_staging_output_without_creating_it(tmp_path: Path) -> None:
    output = tmp_path / "fixture.json"
    result = _run("--derive-only", "--staging-output", str(output))
    assert result.returncode == 2
    failure = _safe_json(result.stdout, "TASK8_V26_MODE_OUTPUT_INVALID")
    assert failure == {
        "reason_code": "TASK8_V26_DERIVE_ONLY_OUTPUT_FORBIDDEN",
        "result": "FAILED",
    }
    assert not output.exists()


def test_missing_required_mode_or_output_rejects_stably() -> None:
    result = _run()
    assert result.returncode == 2
    failure = _safe_json(result.stdout, "TASK8_V26_MODE_INVALID")
    assert failure == {
        "reason_code": "TASK8_V26_CLI_ARGUMENT_INVALID",
        "result": "FAILED",
    }


def test_cli_missing_private_path_is_redacted(tmp_path: Path) -> None:
    canary = "PRIVATE_FORMULA_PROSE_CANARY"
    args = _cli_args("--derive-only")
    policy_index = args.index("--policy-artifact") + 1
    args[policy_index] = str(tmp_path / f"{canary}.json")
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 2
    payload = _safe_json(result.stdout, "TASK8_V26_PRIVATE_PATH_OUTPUT_INVALID")
    assert payload == {"reason_code": "TASK8_V26_INPUT_MISSING", "result": "FAILED"}
    combined = result.stdout + result.stderr
    if canary in combined or str(tmp_path) in combined:
        raise AssertionError("TASK8_V26_PRIVATE_PATH_LEAK")


def test_unexpected_exception_boundary_emits_only_the_stable_safe_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    canary = "PRIVATE_FORMULA_PROSE_CANARY"

    def explode(**_values: bytes) -> tuple[dict[str, Any], dict[str, str]]:
        raise RuntimeError(f"{canary}:C:\\private\\fdd.xlsx")

    monkeypatch.setattr(module, "derive_task8_fixture", explode)
    assert module.main(_main_args("--derive-only")) == 2
    captured = capsys.readouterr()
    payload = _safe_json(captured.out, "TASK8_V26_UNEXPECTED_OUTPUT_INVALID")
    assert payload == {"reason_code": "TASK8_V26_UNEXPECTED_FAILURE", "result": "FAILED"}
    combined = captured.out + captured.err
    if canary in combined or "private" in combined.lower():
        raise AssertionError("TASK8_V26_UNEXPECTED_PRIVATE_OUTPUT_LEAK")


@pytest.mark.parametrize("boundary", ("materializer", "verifier"))
def test_external_exception_codes_are_never_trusted(
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    canary = "TASK8_V26_PRIVATE_FORMULA_PROSE_CANARY"

    class ExternalError(RuntimeError):
        code = canary

    def reject(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise ExternalError(f"{canary}:C:\\private\\formula.xlsx")

    if boundary == "materializer":
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=reject),
        )
        expected = "TASK8_V26_MATERIALIZER_ADMISSION_INVALID"
    else:
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=lambda *_args, **_kwargs: {}),
        )
        monkeypatch.setattr(
            module,
            "_load_independent_verifier",
            lambda: SimpleNamespace(derive_fixture=reject, verify_fixture=reject),
        )
        expected = "TASK8_V26_INDEPENDENT_VERIFIER_INVALID"

    with pytest.raises(module.Task8FixtureError) as raised:
        module.derive_task8_fixture(**_inputs())
    assert raised.value.code == expected
    if canary in str(raised.value) or "private" in str(raised.value).lower():
        raise AssertionError("TASK8_V26_EXTERNAL_CODE_TRUSTED")


def test_existing_hardlink_to_canonical_artifact_is_forbidden_as_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    canonical = tmp_path / "canonical-artifact.json"
    alias = tmp_path / "hardlink-artifact.json"
    canonical.write_bytes(STAGED_ARTIFACT.read_bytes())
    os.link(canonical, alias)
    monkeypatch.setattr(module, "_CANONICAL_ARTIFACT_PATH", canonical)

    with pytest.raises(
        module.Task8FixtureError,
        match="^TASK8_V26_CANONICAL_ARTIFACT_INPUT_FORBIDDEN$",
    ):
        module._reject_canonical_artifact_input(alias)


@pytest.mark.parametrize("alias_kind", ("hardlink", "symlink"))
def test_existing_alias_to_canonical_fixture_is_forbidden_as_output(
    alias_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    canonical = tmp_path / "canonical-fixture.json"
    alias = tmp_path / f"{alias_kind}-fixture.json"
    canonical.write_bytes(b"canonical")
    try:
        if alias_kind == "hardlink":
            os.link(canonical, alias)
        else:
            alias.symlink_to(canonical)
    except OSError as exc:
        pytest.skip(f"TASK8_V26_ALIAS_SETUP_UNAVAILABLE:{type(exc).__name__}")
    monkeypatch.setattr(module, "_CANONICAL_FIXTURE_PATH", canonical)

    with pytest.raises(
        module.Task8FixtureError,
        match="^TASK8_V26_CANONICAL_PUBLICATION_PATH_FORBIDDEN$",
    ):
        module._validate_staging_output_path(alias)


def test_staging_write_detects_parent_swap_between_check_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    parent = tmp_path / "staging"
    moved_parent = tmp_path / "staging-before-swap"
    parent.mkdir()
    output = parent / "fixture.json"
    real_identity = module._file_identity
    swap_pending = True

    def identity_with_swap(path: Path, code: str) -> tuple[int, int]:
        nonlocal swap_pending
        identity = cast(tuple[int, int], real_identity(path, code))
        if swap_pending and path == parent:
            swap_pending = False
            parent.rename(moved_parent)
            parent.mkdir()
        return identity

    monkeypatch.setattr(module, "_file_identity", identity_with_swap)
    with pytest.raises(
        module.Task8FixtureError,
        match="^TASK8_V26_FIXTURE_PARENT_CHANGED$",
    ):
        module._write_staging_fixture(output, b"fixture\n")

    assert not output.exists()
    assert not (moved_parent / output.name).exists()


def test_dynamic_imports_write_no_bytecode_to_redirected_empty_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    cache = tmp_path / "pycache-prefix"
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(cache))
    monkeypatch.setattr(sys, "pycache_prefix", str(cache))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    assert module.main(_main_args("--derive-only")) == 0
    captured = capsys.readouterr()
    payload = _safe_json(captured.out, "TASK8_V26_REDIRECTED_CACHE_OUTPUT_INVALID")
    assert payload["result"] == "DERIVED"
    if captured.err:
        raise AssertionError("TASK8_V26_REDIRECTED_CACHE_STDERR_NOT_EMPTY")

    cache_files = tuple(path for path in cache.rglob("*") if path.is_file())
    assert cache_files == ()
    assert sys.dont_write_bytecode is False
