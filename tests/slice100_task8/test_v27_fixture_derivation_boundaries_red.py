from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from .test_v27_fixture_derivation_red import (
    REPO_ROOT,
    STAGED_ARTIFACT,
    STAGED_FIXTURE,
    _cli_args,
    _generator,
    _inputs,
    _main_args,
    _run,
    _safe_json,
)


@pytest.mark.parametrize("alias_kind", ("hardlink", "file_symlink", "ancestor_symlink"))
def test_main_rejects_every_canonical_artifact_alias_from_opened_identity(
    alias_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    canonical_root = tmp_path / "canonical-root"
    canonical = canonical_root / "contracts" / "canonical-artifact.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(STAGED_ARTIFACT.read_bytes())
    alias = tmp_path / "artifact-alias.json"
    try:
        if alias_kind == "hardlink":
            os.link(canonical, alias)
        elif alias_kind == "file_symlink":
            alias.symlink_to(canonical)
        else:
            ancestor = tmp_path / "contracts-ancestor-alias"
            ancestor.symlink_to(canonical.parent, target_is_directory=True)
            alias = ancestor / canonical.name
    except OSError as exc:
        pytest.skip(f"TASK8_V27_ALIAS_SETUP_UNAVAILABLE:{type(exc).__name__}")
    monkeypatch.setattr(module, "_CANONICAL_ARTIFACT_PATH", canonical)
    monkeypatch.setattr(module, "_CANONICAL_FIXTURE_PATH", tmp_path / "canonical-fixture.json")
    args = _main_args("--derive-only")
    args[args.index("--staged-artifact") + 1] = str(alias)

    assert module.main(args) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _safe_json(captured.out, "TASK8_V27_CANONICAL_ALIAS_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_CANONICAL_ARTIFACT_INPUT_FORBIDDEN",
        "result": "FAILED",
    }
    assert str(tmp_path) not in captured.out


def test_main_rejects_nonregular_artifact_before_consuming_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    artifact = tmp_path / "private-artifact-directory"
    artifact.mkdir()
    args = _main_args("--derive-only")
    args[args.index("--staged-artifact") + 1] = str(artifact)

    assert module.main(args) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _safe_json(captured.out, "TASK8_V27_NONREGULAR_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_INPUT_NOT_REGULAR",
        "result": "FAILED",
    }
    assert str(tmp_path) not in captured.out


def test_derive_only_rejects_a_staging_output_without_creating_it(tmp_path: Path) -> None:
    output = tmp_path / "fixture.json"
    result = _run("--derive-only", "--staging-output", str(output))
    assert result.returncode == 2
    assert _safe_json(result.stdout, "TASK8_V27_MODE_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_DERIVE_ONLY_OUTPUT_FORBIDDEN",
        "result": "FAILED",
    }
    assert not output.exists()


def test_missing_required_mode_or_output_rejects_stably() -> None:
    result = _run()
    assert result.returncode == 2
    assert _safe_json(result.stdout, "TASK8_V27_MODE_INVALID") == {
        "reason_code": "TASK8_V27_CLI_ARGUMENT_INVALID",
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
    assert _safe_json(result.stdout, "TASK8_V27_PRIVATE_PATH_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_INPUT_MISSING",
        "result": "FAILED",
    }
    combined = result.stdout + result.stderr
    assert canary not in combined and str(tmp_path) not in combined


def test_unexpected_exception_boundary_emits_only_the_stable_safe_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    canary = "PRIVATE_FORMULA_PROSE_CANARY"

    def explode(**_values: bytes) -> tuple[dict[str, Any], dict[str, str]]:
        raise RuntimeError(f"{canary}:C:\\private\\fdd.xlsx")

    monkeypatch.setattr(module, "derive_task8_v27_fixture", explode)
    assert module.main(_main_args("--derive-only")) == 2
    captured = capsys.readouterr()
    assert _safe_json(captured.out, "TASK8_V27_UNEXPECTED_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_UNEXPECTED_FAILURE",
        "result": "FAILED",
    }
    combined = captured.out + captured.err
    assert canary not in combined and "private" not in combined.lower()


@pytest.mark.parametrize("boundary", ("materializer", "derive_fixture", "verify_fixture"))
@pytest.mark.parametrize("existing_stable_error", (False, True))
def test_direct_api_external_errors_are_stable_and_traceback_private(
    boundary: str,
    existing_stable_error: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    canary = "TASK8_V27_PRIVATE_FORMULA_PROSE_CANARY"
    values = _inputs()
    admitted = json.loads(values["v27_artifact_raw"])
    fixture = json.loads(STAGED_FIXTURE.read_bytes())
    expected = (
        "TASK8_V27_MATERIALIZER_ADMISSION_INVALID"
        if boundary == "materializer"
        else "TASK8_V27_INDEPENDENT_VERIFIER_INVALID"
    )

    class ExternalError(RuntimeError):
        code = canary

    def reject(*_args: object, **_kwargs: object) -> dict[str, Any]:
        if existing_stable_error:
            try:
                raise RuntimeError(canary)
            except RuntimeError as cause:
                raise module.Task8V27FixtureError(expected) from cause
        raise ExternalError(f"{canary}:C:\\private\\formula.xlsx")

    if boundary == "materializer":
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=reject),
        )
    else:
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=lambda *_args, **_kwargs: admitted),
        )
        monkeypatch.setattr(
            module,
            "_load_independent_verifier",
            lambda: SimpleNamespace(
                derive_fixture=(
                    reject if boundary == "derive_fixture" else lambda **_kwargs: fixture
                ),
                verify_fixture=reject,
            ),
        )

    with pytest.raises(module.Task8V27FixtureError) as raised:
        module.derive_task8_v27_fixture(**values)
    error = raised.value
    rendered = "".join(traceback.format_exception(error))
    assert error.code == expected
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert canary not in rendered


@pytest.mark.parametrize(
    "surface",
    (
        "policy_read",
        "policy_decode",
        "policy_render",
        "materializer_loader",
        "design_decode",
        "count_helper",
        "identity_helper",
    ),
)
@pytest.mark.parametrize("existing_stable_error", (False, True))
def test_exported_direct_api_sanitizes_every_internal_helper_error(
    surface: str,
    existing_stable_error: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    canary = "TASK8_V27_EXPORTED_API_PRIVATE_CANARY"
    values = _inputs()
    admitted = json.loads(values["v27_artifact_raw"])
    fixture = json.loads(STAGED_FIXTURE.read_bytes())
    stable_codes = {
        "policy_read": "TASK8_V27_POLICY_FIXTURE_PREREQUISITE_INVALID",
        "policy_decode": "TASK8_V27_POLICY_FIXTURE_PREREQUISITE_INVALID",
        "policy_render": "TASK8_V27_FIXTURE_INVALID",
        "materializer_loader": "TASK8_V27_MATERIALIZER_ADMISSION_INVALID",
        "design_decode": "TASK8_V27_INSTALLED_RLS_COUNT_DISTINCTION_INVALID",
        "count_helper": "TASK8_V27_INSTALLED_RLS_COUNT_DISTINCTION_INVALID",
        "identity_helper": "TASK8_V27_FIXTURE_IDENTITY_MISMATCH",
    }
    stable_code = stable_codes[surface]
    expected = stable_code if existing_stable_error else "TASK8_V27_UNEXPECTED_FAILURE"

    def reject(*_args: object, **_kwargs: object) -> Any:
        if existing_stable_error:
            try:
                raise RuntimeError(canary)
            except RuntimeError as cause:
                raise module.Task8V27FixtureError(stable_code) from cause
        raise RuntimeError(canary)

    if surface == "policy_read":
        monkeypatch.setattr(
            module,
            "_POLICY_FIXTURE_PATH",
            SimpleNamespace(read_bytes=reject),
        )
    elif surface == "policy_decode":
        monkeypatch.setattr(module, "_decode", reject)
    elif surface == "policy_render":
        monkeypatch.setattr(module, "_render", reject)
    elif surface == "materializer_loader":
        monkeypatch.setattr(module, "_load_admission_materializer", reject)
    else:
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=lambda *_args, **_kwargs: admitted),
        )
        if surface == "design_decode":
            monkeypatch.setattr(module, "_reviewed_design_installed_rls_delta", reject)
        elif surface == "count_helper":
            monkeypatch.setattr(module, "_validate_admitted_count_distinction", reject)
        else:
            monkeypatch.setattr(
                module,
                "_load_independent_verifier",
                lambda: SimpleNamespace(
                    derive_fixture=lambda **_kwargs: fixture,
                    verify_fixture=lambda **_kwargs: {},
                ),
            )
            monkeypatch.setattr(module, "_identity_set", reject)

    with pytest.raises(module.Task8V27FixtureError) as captured:
        module.derive_task8_v27_fixture(**values)
    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert error.code == expected
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert canary not in rendered


@pytest.mark.parametrize("base_exception", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_exported_direct_api_propagates_internal_base_exceptions(
    base_exception: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()

    def reject() -> None:
        raise base_exception()

    monkeypatch.setattr(module, "_validate_policy_fixture_prerequisite", reject)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    with pytest.raises(base_exception):
        module.derive_task8_v27_fixture(**_inputs())
    assert sys.dont_write_bytecode is False


@pytest.mark.parametrize("boundary", ("materializer", "derive_fixture", "verify_fixture"))
@pytest.mark.parametrize("base_exception", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_direct_api_propagates_base_exceptions_and_restores_bytecode_state(
    boundary: str,
    base_exception: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    values = _inputs()
    admitted = json.loads(values["v27_artifact_raw"])
    fixture = json.loads(STAGED_FIXTURE.read_bytes())

    def reject(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise base_exception()

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    if boundary == "materializer":
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=reject),
        )
    else:
        monkeypatch.setattr(
            module,
            "_load_admission_materializer",
            lambda: SimpleNamespace(validate_frozen_artifact=lambda *_args, **_kwargs: admitted),
        )
        monkeypatch.setattr(
            module,
            "_load_independent_verifier",
            lambda: SimpleNamespace(
                derive_fixture=(
                    reject if boundary == "derive_fixture" else lambda **_kwargs: fixture
                ),
                verify_fixture=reject,
            ),
        )

    with pytest.raises(base_exception):
        module.derive_task8_v27_fixture(**values)
    assert sys.dont_write_bytecode is False


@pytest.mark.parametrize("base_exception", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_main_propagates_base_exceptions_and_restores_derivation_bytecode_state(
    base_exception: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()

    def reject(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise base_exception()

    monkeypatch.setattr(
        module,
        "_load_admission_materializer",
        lambda: SimpleNamespace(validate_frozen_artifact=reject),
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    with pytest.raises(base_exception):
        module.main(_main_args("--derive-only"))
    assert sys.dont_write_bytecode is False


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
        module.Task8V27FixtureError,
        match="^TASK8_V27_CANONICAL_ARTIFACT_INPUT_FORBIDDEN$",
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
        pytest.skip(f"TASK8_V27_ALIAS_SETUP_UNAVAILABLE:{type(exc).__name__}")
    monkeypatch.setattr(module, "_CANONICAL_FIXTURE_PATH", canonical)

    with pytest.raises(
        module.Task8V27FixtureError,
        match="^TASK8_V27_CANONICAL_PUBLICATION_PATH_FORBIDDEN$",
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
        module.Task8V27FixtureError,
        match="^TASK8_V27_FIXTURE_PARENT_CHANGED$",
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
    payload = _safe_json(captured.out, "TASK8_V27_REDIRECTED_CACHE_OUTPUT_INVALID")
    assert payload["result"] == "DERIVED"
    assert captured.err == ""
    assert tuple(path for path in cache.rglob("*") if path.is_file()) == ()
    assert sys.dont_write_bytecode is False
