"""Hermetic CLI and signature contracts for the v26 PostgreSQL census."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v26_signed_census.py"


def _load() -> ModuleType:
    assert SIGNED_RUNNER.is_file()
    name = "_task8_v26_signed_census_cli_red"
    spec = importlib.util.spec_from_file_location(name, SIGNED_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v26_cli_requires_explicit_noncanonical_artifact_and_fixture() -> None:
    runner = _load()
    signature = inspect.signature(runner.execute_signed_census)
    assert "artifact" in signature.parameters
    assert "fixture" in signature.parameters
    parser_source = inspect.getsource(runner._parser)
    assert '"--artifact"' in parser_source
    assert '"--fixture"' in parser_source
    assert "required=True" in parser_source


def test_v26_cli_private_mapping_has_only_dotenv_locator() -> None:
    runner = _load()
    assert runner.private_profile_mapping(
        {
            "IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env",
            "IDIS_DATABASE_ADMIN_URL": "not-forwarded",
            "IDIS_DATABASE_URL": "not-forwarded",
            "IDIS_ERASURE_DATABASE_URL": "not-forwarded",
            "IDIS_FDD_METHODOLOGY_PATH": "not-forwarded",
        }
    ) == {"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"}
    source = inspect.getsource(runner.execute_signed_census)
    assert "dict(os.environ)" not in source
    assert 'os.environ.get("IDIS_STRICT_DOTENV_PATH", "")' in source
    assert 'os.environ.get("IDIS_DATABASE_URL"' not in source
    assert 'os.environ.get("IDIS_FDD_METHODOLOGY_PATH"' not in source


def test_v26_cli_validates_explicit_assets_before_profile_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    artifact = tmp_path / "artifact.json"
    fixture = tmp_path / "fixture.json"
    artifact.write_bytes(b"invalid")
    fixture.write_bytes(b"invalid")
    touched: list[str] = []
    monkeypatch.setattr(
        runner,
        "load_operator_material",
        lambda *_args, **_kwargs: touched.append("profile"),
    )
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.execute_signed_census(
            output=tmp_path / "unused.json",
            artifact=artifact,
            fixture=fixture,
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert touched == []


def test_v26_cli_output_is_exclusive_and_task8_scoped(tmp_path: Path) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    runner.write_exclusive(output, b"first\n")
    assert output.read_bytes() == b"first\n"
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_OUTPUT_EXISTS$",
    ):
        runner.write_exclusive(output, b"second\n")
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_OUTPUT_PATH_INVALID$",
    ):
        runner.validate_output_path(tmp_path / "outside.json")


def test_v26_cli_rejects_hardlink_aliases_to_canonical_inputs(tmp_path: Path) -> None:
    runner = _load()
    canonical = tmp_path / "canonical.json"
    alias = tmp_path / "alias.json"
    canonical.write_bytes(b"{}\n")
    os.link(canonical, alias)
    runner.CANONICAL_ARTIFACT_PATH = canonical
    runner.CANONICAL_FIXTURE_PATH = tmp_path / "absent-fixture.json"
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner._read_exact_regular_file(
            canonical,
            max_bytes=100,
            code="TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID",
            forbidden_paths=(runner.CANONICAL_ARTIFACT_PATH,),
        )
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner.load_and_verify_preflight_assets(
            artifact=alias,
            fixture=tmp_path / "not-reached.json",
        )


def test_v26_cli_reads_inputs_from_one_captured_regular_file_identity() -> None:
    runner = _load()
    source = inspect.getsource(runner._read_exact_regular_file)
    assert "os.open" in source
    assert "os.fstat" in source
    assert "os.read" in source
    assert "_same_file_identity" in source
    assert ".read_bytes()" not in source


def test_v26_cli_rejects_any_multilink_input_before_decoding(tmp_path: Path) -> None:
    runner = _load()
    target = tmp_path / "candidate.json"
    alias = tmp_path / "unrelated-alias.json"
    target.write_bytes(b"{}\n")
    os.link(target, alias)

    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner._read_exact_regular_file(
            target,
            max_bytes=100,
            code="TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID",
        )


def test_v26_cli_does_not_resolve_away_candidate_link_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target = tmp_path / "target.json"
    alias = tmp_path / "alias.json"
    fixture = tmp_path / "fixture.json"
    target.write_bytes(b"{}\n")
    fixture.write_bytes(b"{}\n")
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.load_and_verify_preflight_assets(artifact=alias, fixture=fixture)


def test_v26_cli_rejects_candidate_beneath_a_linked_ancestor(
    tmp_path: Path,
) -> None:
    runner = _load()
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    (target_directory / "artifact.json").write_bytes(b"{}\n")
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b"{}\n")
    linked_directory = tmp_path / "linked"
    try:
        linked_directory.symlink_to(target_directory, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable in this environment")

    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.load_and_verify_preflight_assets(
            artifact=linked_directory / "artifact.json",
            fixture=fixture,
        )


def test_v26_cli_input_swap_between_check_and_open_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target = tmp_path / "candidate.json"
    replacement = tmp_path / "replacement.json"
    original = tmp_path / "original.json"
    target.write_bytes(b"original\n")
    replacement.write_bytes(b"replacement\n")
    real_open = os.open
    swapped = False

    def swap_then_open(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal swapped
        if not swapped and Path(path) == target:
            swapped = True
            target.rename(original)
            replacement.rename(target)
        return real_open(path, flags, mode)

    monkeypatch.setattr(runner.os, "open", swap_then_open)
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner._read_exact_regular_file(
            target,
            max_bytes=100,
            code="TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID",
        )


def test_v26_cli_cleanup_never_unlinks_a_replacement_identity(tmp_path: Path) -> None:
    runner = _load()
    target = tmp_path / "receipt.json"
    target.write_bytes(b"owned")
    owned_identity = runner._path_file_identity(target)
    target.unlink()
    target.write_bytes(b"replacement")
    runner._unlink_owned_path(target, owned_identity)
    assert target.read_bytes() == b"replacement"
    assert "_unlink_owned_path" in inspect.getsource(runner._reserve_output)
    assert ".unlink(" not in inspect.getsource(runner.write_exclusive)


def test_v26_cli_owned_cleanup_failure_uses_one_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target = tmp_path / "receipt.json"
    target.write_bytes(b"owned")
    owned_identity = runner._path_file_identity(target)

    def fail_unlink(_self: Path) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED$",
    ):
        runner._unlink_owned_path(target, owned_identity)
    assert target.exists()


def test_v26_cli_holds_and_revalidates_output_parent_identity() -> None:
    runner = _load()
    source = inspect.getsource(runner._reserve_output) + inspect.getsource(
        runner._write_reserved_output
    )
    assert "_hold_directory_identity" in source
    assert "_revalidate_held_directory_identity" in source
    assert "os.fstat" in source
    assert "_same_file_identity" in source


def test_v26_cli_write_failure_replacement_race_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    original_unlink_owned = runner._unlink_owned_path

    def fail_write(_descriptor: int, _content: object) -> int:
        raise OSError("injected write failure")

    def replace_before_cleanup(path: Path, identity: tuple[int, int] | None) -> None:
        path.unlink()
        path.write_bytes(b"replacement\n")
        original_unlink_owned(path, identity)

    monkeypatch.setattr(runner.os, "write", fail_write)
    monkeypatch.setattr(runner, "_unlink_owned_path", replace_before_cleanup)
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_OUTPUT_WRITE_FAILED$",
    ):
        runner.write_exclusive(output, b"owned\n")
    assert output.read_bytes() == b"replacement\n"


def test_v26_cli_reserves_output_before_profile_and_cleans_owned_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    output = report_root / "receipt.json"
    runner.REPORT_ROOT = report_root
    monkeypatch.setattr(
        runner,
        "load_and_verify_preflight_assets",
        lambda **_kwargs: (
            {"schema_version": "preflight"},
            {"artifact_raw": b"{}", "policy_artifact_raw": b"{}"},
        ),
    )
    monkeypatch.setattr(
        runner,
        "validate_v26_dashboard_requirement_assets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(runner, "_current_runner_sha256", lambda: "0" * 64)
    observed: list[tuple[bool, int]] = []

    def stop_at_profile(_mapping: object) -> object:
        observed.append((output.exists(), output.stat().st_size if output.exists() else -1))
        raise runner.Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "load_operator_material", stop_at_profile)
    with pytest.raises(
        runner.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID$",
    ):
        runner.execute_signed_census(
            output=output,
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert observed == [(True, 0)]
    assert not output.exists()


def test_v26_cli_failure_event_is_code_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()

    def fail(**_kwargs: object) -> object:
        raise runner.Task8V26SignedCensusError("TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "execute_signed_census", fail)
    assert runner.main(["--artifact", "a", "--fixture", "f", "--output", "o"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "TASK8_V26_SIGNED_CENSUS_PROFILE_INVALID",
        "result": "FAILED",
        "schema_version": "task8-v26-signed-census-cli-result-v1",
    }
