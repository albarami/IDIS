"""Hermetic CLI and signature contracts for the v27 PostgreSQL census."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v27_signed_census.py"
SQL_RUNNER = REPO_ROOT / "scripts" / "run_task8_v27_sql_authority_census.py"
_APPROVED_SQL_RUNNER_SHA256 = "812bfeb229f37b15fe8670339b46c98831b301a895cf6add2227913ec1e9dd4c"
# Names the pre-hardening runner whose bytes are not in this repository; retained as historical evidence (owner decision R5 option B, 2026-10-19).  # noqa: E501
_SUPERSEDED_SQL_RUNNER_SHA256_2026_10_18 = (
    "d12ea370927706fb969fed3bc586e39d8cc212e25e484eae32e4df52c3f9814b"
)


def _load() -> ModuleType:
    assert SIGNED_RUNNER.is_file()
    name = "_task8_v27_signed_census_cli_red"
    spec = importlib.util.spec_from_file_location(name, SIGNED_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v27_cli_requires_explicit_noncanonical_artifact_and_fixture() -> None:
    runner = _load()
    signature = inspect.signature(runner.execute_signed_census)
    assert "artifact" in signature.parameters
    assert "fixture" in signature.parameters
    parser_source = inspect.getsource(runner._parser)
    assert '"--artifact"' in parser_source
    assert '"--fixture"' in parser_source
    assert "required=True" in parser_source


def test_v27_cli_pins_the_frozen_sql_runner_bytes() -> None:
    runner = _load()
    expected = _APPROVED_SQL_RUNNER_SHA256
    assert hashlib.sha256(SQL_RUNNER.read_bytes()).hexdigest() == expected
    assert expected == runner._APPROVED_SQL_RUNNER_SHA256


def test_v27_cli_retains_superseded_sql_runner_pin_as_historical_evidence() -> None:
    runner = _load()
    superseded = _SUPERSEDED_SQL_RUNNER_SHA256_2026_10_18
    assert superseded == runner._SUPERSEDED_SQL_RUNNER_SHA256_2026_10_18
    assert superseded != runner._APPROVED_SQL_RUNNER_SHA256
    assert hashlib.sha256(SQL_RUNNER.read_bytes()).hexdigest() != superseded


def test_v27_cli_refuses_sql_runner_whose_digest_is_not_the_approved_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    approved = runner._APPROVED_SQL_RUNNER_SHA256
    assert runner._current_runner_sha256() == approved
    superseded = runner._SUPERSEDED_SQL_RUNNER_SHA256_2026_10_18
    mutated_path = (tmp_path / "run_task8_v27_sql_authority_census.py").resolve()
    mutated = bytearray(SQL_RUNNER.read_bytes())
    mutated[0] ^= 0xFF
    mutated_path.write_bytes(bytes(mutated))
    mutated_digest = hashlib.sha256(mutated_path.read_bytes()).hexdigest()
    assert mutated_digest != approved
    assert mutated_digest != superseded
    monkeypatch.setattr(runner, "SQL_RUNNER_PATH", mutated_path)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED$",
    ):
        runner._current_runner_sha256()
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED$",
    ):
        runner._load_hashed_module(
            path=mutated_path,
            expected_sha256=approved,
            module_name="_task8_r5_mutated_sql_runner",
            code="TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
        )
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED$",
    ):
        runner._load_hashed_module(
            path=SQL_RUNNER,
            expected_sha256=superseded,
            module_name="_task8_r5_superseded_sql_runner",
            code="TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
        )


def test_v27_cli_private_mapping_has_only_dotenv_locator() -> None:
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
    source = inspect.getsource(runner.execute_signed_census) + inspect.getsource(
        runner._execute_signed_census_impl
    )
    assert "dict(os.environ)" not in source
    assert 'os.environ.get("IDIS_STRICT_DOTENV_PATH", "")' in source
    assert 'os.environ.get("IDIS_DATABASE_URL"' not in source
    assert 'os.environ.get("IDIS_FDD_METHODOLOGY_PATH"' not in source


def test_v27_cli_validates_explicit_assets_before_profile_access(
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
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.execute_signed_census(
            output=tmp_path / "unused.json",
            artifact=artifact,
            fixture=fixture,
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert touched == []


def test_v27_cli_output_is_exclusive_and_task8_scoped(tmp_path: Path) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    runner.write_exclusive(output, b"first\n")
    assert output.read_bytes() == b"first\n"
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_EXISTS$",
    ):
        runner.write_exclusive(output, b"second\n")
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_PATH_INVALID$",
    ):
        runner.validate_output_path(tmp_path / "outside.json")


def test_v27_cli_rejects_output_beneath_linked_report_root_ancestor(
    tmp_path: Path,
) -> None:
    runner = _load()
    outside = tmp_path / "outside"
    outside_report_root = outside / ".local_reports" / "slice100" / "task8"
    outside_report_root.mkdir(parents=True)
    linked_workspace = tmp_path / "linked-workspace"
    try:
        linked_workspace.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symbolic links are unavailable in this environment")
    runner.REPORT_ROOT = linked_workspace / ".local_reports" / "slice100" / "task8"
    output = runner.REPORT_ROOT / "receipt.json"

    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_PATH_INVALID$",
    ):
        runner.write_exclusive(output, b"must-not-escape\n")

    assert not (outside_report_root / output.name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ancestor canary")
def test_v27_cli_rejects_output_beneath_junction_report_root_ancestor(
    tmp_path: Path,
) -> None:
    runner = _load()
    outside = tmp_path / "outside"
    outside_report_root = outside / ".local_reports" / "slice100" / "task8"
    outside_report_root.mkdir(parents=True)
    linked_workspace = tmp_path / "junction-workspace"
    completed = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(linked_workspace),
            str(outside),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("directory junctions are unavailable in this environment")
    runner.REPORT_ROOT = linked_workspace / ".local_reports" / "slice100" / "task8"
    output = runner.REPORT_ROOT / "receipt.json"

    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_PATH_INVALID$",
    ):
        runner.write_exclusive(output, b"must-not-escape\n")

    assert not (outside_report_root / output.name).exists()


@pytest.mark.parametrize("unsupported_platform", ("darwin", "freebsd14", "aix"))
def test_v27_cli_rejects_unsupported_platform_before_any_sensitive_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsupported_platform: str,
) -> None:
    runner = _load()
    touched: list[str] = []

    def forbidden(name: str):
        def call(*_args: object, **_kwargs: object) -> object:
            touched.append(name)
            raise AssertionError(name)

        return call

    monkeypatch.setattr(runner.sys, "platform", unsupported_platform)
    monkeypatch.setattr(
        runner,
        "load_and_verify_preflight_assets",
        forbidden("preflight"),
    )
    monkeypatch.setattr(runner, "_reserve_output", forbidden("reservation"))
    monkeypatch.setattr(runner, "load_operator_material", forbidden("profile"))
    monkeypatch.setattr(runner, "_load_hashed_module", forbidden("database"))

    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PLATFORM_UNSUPPORTED$",
    ) as caught:
        runner.execute_signed_census(
            output=tmp_path / "receipt.json",
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert touched == []
    assert not (tmp_path / "receipt.json").exists()


def test_v27_cli_rejects_hardlink_aliases_to_canonical_inputs(tmp_path: Path) -> None:
    runner = _load()
    canonical = tmp_path / "canonical.json"
    alias = tmp_path / "alias.json"
    canonical.write_bytes(b"{}\n")
    os.link(canonical, alias)
    runner.CANONICAL_ARTIFACT_PATH = canonical
    runner.CANONICAL_FIXTURE_PATH = tmp_path / "absent-fixture.json"
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner._read_exact_regular_file(
            canonical,
            max_bytes=100,
            code="TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID",
            forbidden_paths=(runner.CANONICAL_ARTIFACT_PATH,),
        )
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner.load_and_verify_preflight_assets(
            artifact=alias,
            fixture=tmp_path / "not-reached.json",
        )


def test_v27_cli_reads_inputs_from_one_captured_regular_file_identity() -> None:
    runner = _load()
    source = inspect.getsource(runner._read_exact_regular_file)
    assert "os.open" in source
    assert "os.fstat" in source
    assert "os.read" in source
    assert "_same_file_identity" in source
    assert ".read_bytes()" not in source


def test_v27_cli_regular_file_reader_opens_the_path_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target = tmp_path / "candidate.json"
    target.write_bytes(b"{}\n")
    real_open = os.open
    opened: list[object] = []

    def observe_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(runner.os, "open", observe_open)
    assert (
        runner._read_exact_regular_file(
            target,
            max_bytes=100,
            code="TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID",
        )
        == b"{}\n"
    )
    assert opened == [target]


def test_v27_cli_rejects_any_multilink_input_before_decoding(tmp_path: Path) -> None:
    runner = _load()
    target = tmp_path / "candidate.json"
    alias = tmp_path / "unrelated-alias.json"
    target.write_bytes(b"{}\n")
    os.link(target, alias)

    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner._read_exact_regular_file(
            target,
            max_bytes=100,
            code="TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID",
        )


def test_v27_cli_does_not_resolve_away_candidate_link_evidence(
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
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.load_and_verify_preflight_assets(artifact=alias, fixture=fixture)


def test_v27_cli_rejects_candidate_beneath_a_linked_ancestor(
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
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.load_and_verify_preflight_assets(
            artifact=linked_directory / "artifact.json",
            fixture=fixture,
        )


def test_v27_cli_passes_fixed_assets_as_lexical_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    linked_directory = tmp_path / "linked"
    try:
        linked_directory.symlink_to(target_directory, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable in this environment")
    parent = linked_directory / "parent.json"
    policy = linked_directory / "policy.json"
    reviewer = linked_directory / "reviewer.md"
    observed: list[Path] = []

    def observe(path: Path, **_kwargs: object) -> bytes:
        observed.append(path)
        return b"{}\n"

    monkeypatch.setattr(runner, "PARENT_V21_PATH", parent)
    monkeypatch.setattr(runner, "POLICY_PATH", policy)
    monkeypatch.setattr(runner, "REVIEWER_MAP_PATH", reviewer)
    monkeypatch.setattr(runner, "_read_exact_regular_file", observe)
    monkeypatch.setattr(runner, "validate_preflight_assets", lambda **_kwargs: {})
    runner.load_and_verify_preflight_assets(
        artifact=tmp_path / "artifact.json",
        fixture=tmp_path / "fixture.json",
    )
    expected_fixed = tuple(Path(os.path.abspath(path)) for path in (parent, policy, reviewer))
    assert tuple(observed[2:]) == expected_fixed
    assert all(
        lexical != original.resolve()
        for lexical, original in zip(
            expected_fixed,
            (parent, policy, reviewer),
            strict=True,
        )
    )


def test_v27_cli_input_swap_between_check_and_open_fails_closed(
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
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner._read_exact_regular_file(
            target,
            max_bytes=100,
            code="TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID",
        )


def test_v27_cli_owned_cleanup_failure_uses_one_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    target = report_root / "receipt.json"

    def fail_unlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected cleanup failure")

    if os.name == "nt":
        monkeypatch.setattr(runner, "_mark_windows_descriptor_for_deletion", fail_unlink)
    else:
        monkeypatch.setattr(runner, "_replace_relative", fail_unlink)
    with (
        pytest.raises(
            runner.Task8V27SignedCensusError,
            match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED$",
        ),
        runner._reserve_output(target),
    ):
        pass


def test_v27_cli_holds_and_revalidates_output_parent_identity() -> None:
    runner = _load()
    source = inspect.getsource(runner._reserve_output) + inspect.getsource(
        runner._write_reserved_output
    )
    assert "_hold_directory_identity" in source
    assert "_revalidate_held_directory_identity" in source
    assert "os.fstat" in source
    assert "_same_file_identity" in source


def test_v27_cli_write_failure_replacement_race_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"

    def fail_write(_descriptor: int, _content: object) -> int:
        raise OSError("injected write failure")

    monkeypatch.setattr(runner.os, "write", fail_write)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_WRITE_FAILED$",
    ):
        runner.write_exclusive(output, b"owned\n")
    assert not output.exists()


def test_v27_cli_reserves_output_before_profile_and_cleans_owned_reservation(
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
        "validate_v27_dashboard_requirement_assets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(runner, "_current_runner_sha256", lambda: "0" * 64)
    observed: list[tuple[bool, int]] = []

    def stop_at_profile(_mapping: object) -> object:
        observed.append((output.exists(), output.stat().st_size if output.exists() else -1))
        raise runner.Task8V27SignedCensusError("TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "load_operator_material", stop_at_profile)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID$",
    ):
        runner.execute_signed_census(
            output=output,
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert observed == [(True, 0)]
    assert not output.exists()


def test_v27_cli_failure_event_is_code_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()

    def fail(**_kwargs: object) -> object:
        raise runner.Task8V27SignedCensusError("TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "execute_signed_census", fail)
    assert runner.main(["--artifact", "a", "--fixture", "f", "--output", "o"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID",
        "result": "FAILED",
        "schema_version": "task8-v27-signed-census-cli-result-v1",
    }


def test_v27_signed_projection_binds_closed_ledger_and_pregrant_boundary() -> None:
    runner = _load()
    assert {
        "fixture_dml_transaction_count",
        "fixture_dml_operation_ledger_sha256",
        "fixture_dml_operation_sequence_sha256",
        "application_fixture_dml_count",
        "application_behavior_dml_count",
        "erasure_behavior_dml_count",
        "pregrant_physical_public_relation_count",
        "pregrant_application_relation_count",
        "pregrant_excluded_relation_count",
        "pregrant_excluded_application_direct_privilege_count",
        "pregrant_excluded_application_effective_privilege_count",
        "pregrant_application_catalog_sha256",
        "application_privilege_boundary_sha256",
    }.issubset(runner._SAFE_EXECUTION_FIELDS)


def test_v27_safe_projection_keys_match_frozen_sql_receipt_schema() -> None:
    runner = _load()
    tree = ast.parse(SQL_RUNNER.read_text(encoding="utf-8"))

    def returned_literal_keys(function_name: str) -> tuple[str, ...]:
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        returned = next(
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        )
        return tuple(
            key.value
            for key in returned.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )

    pregrant_fields = (
        "pregrant_physical_public_relation_count",
        "pregrant_application_relation_count",
        "pregrant_excluded_relation_count",
        "pregrant_excluded_application_direct_privilege_count",
        "pregrant_excluded_application_effective_privilege_count",
        "pregrant_application_catalog_sha256",
        "application_privilege_boundary_sha256",
    )
    assert returned_literal_keys("_pregrant_execution_binding") == pregrant_fields
    assert set(pregrant_fields).issubset(runner._SAFE_EXECUTION_FIELDS)

    approved_ledger_fields = {
        "fixture_dml_transaction_count",
        "application_fixture_dml_count",
        "application_behavior_dml_count",
        "erasure_behavior_dml_count",
        "fixture_dml_operation_ledger_sha256",
        "fixture_dml_operation_sequence_sha256",
    }
    sql_ledger_fields = set(returned_literal_keys("_validate_census_dml_closed_ledger"))
    assert approved_ledger_fields.issubset(sql_ledger_fields)
    assert approved_ledger_fields.issubset(runner._SAFE_EXECUTION_FIELDS)
    assert "admin_database_operation_classification_sha256" not in sql_ledger_fields
    assert "admin_database_operation_classification_sha256" not in (runner._SAFE_EXECUTION_FIELDS)


def test_v27_direct_execution_boundary_drops_private_exception_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()

    def fail(**_kwargs: object) -> object:
        raise RuntimeError("private-profile-key-db-prose")

    monkeypatch.setattr(runner, "_execute_signed_census_impl", fail, raising=False)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_FAILED$",
    ) as caught:
        runner.execute_signed_census(
            output=tmp_path / "out.json",
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private-profile-key-db-prose" not in repr(caught.value)


def test_v27_main_sanitizes_unexpected_exception_and_propagates_base_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()
    monkeypatch.setattr(
        runner,
        "execute_signed_census",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private-db-prose")),
    )
    assert runner.main(["--artifact", "a", "--fixture", "f", "--output", "o"]) == 2
    output = capsys.readouterr().out
    assert "private-db-prose" not in output
    assert json.loads(output)["reason_code"] == "TASK8_V27_SIGNED_CENSUS_FAILED"
    monkeypatch.setattr(
        runner,
        "execute_signed_census",
        lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.main(["--artifact", "a", "--fixture", "f", "--output", "o"])


def test_v27_main_sanitizes_known_exception_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()

    def fail(**_kwargs: object) -> object:
        try:
            raise RuntimeError("private-known-main-prose")
        except RuntimeError as exc:
            raise runner.Task8V27SignedCensusError(
                "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID"
            ) from exc

    monkeypatch.setattr(runner, "execute_signed_census", fail)
    assert runner.main(["--artifact", "a", "--fixture", "f", "--output", "o"]) == 2
    output = capsys.readouterr().out
    assert "private-known-main-prose" not in output
    assert json.loads(output)["reason_code"] == "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID"


def test_v27_hashed_module_execution_suppresses_and_restores_bytecode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    module_path = tmp_path / "hashed_module.py"
    module_path.write_text("VALUE = 1\n", encoding="utf-8")
    expected = __import__("hashlib").sha256(module_path.read_bytes()).hexdigest()
    cache_root = tmp_path / "redirected-cache"
    monkeypatch.setattr(sys, "pycache_prefix", str(cache_root))
    original = sys.dont_write_bytecode
    loaded = runner._load_hashed_module(
        path=module_path,
        expected_sha256=expected,
        module_name="_task8_v27_bytecode_canary",
        code="TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
    )
    assert loaded.VALUE == 1
    assert sys.dont_write_bytecode is original
    assert not list(tmp_path.rglob("*.pyc"))


def _valid_safe_execution(runner: ModuleType) -> dict[str, object]:
    result: dict[str, object] = {
        field: (
            __import__("hashlib").sha256(field.encode("ascii")).hexdigest()
            if field.endswith("_sha256")
            else 0
        )
        for field in runner._SAFE_EXECUTION_FIELDS
    }
    result.update(
        {
            "admin_behavior_case_count": 0,
            "admin_observation_case_count": 0,
            "admin_dml_case_count": 0,
            "application_seed_dml_count": 172,
            "fixture_dml_transaction_count": 225,
            "application_fixture_dml_count": 172,
            "application_behavior_dml_count": 47,
            "erasure_behavior_dml_count": 6,
            "fixture_dml_operation_sequence_sha256": (
                "f36eecb83fdfdb61a5fe91c9b4ece935550fd5aa795400dc66450531e0118e42"
            ),
            "application_case_execution_count": 22,
            "erasure_executor_case_execution_count": 3,
            "legal_execution_count": 6,
            "illegal_rejection_count": 11,
            "direct_sql_rejection_count": 8,
            "rejected_case_projection_count": 19,
            "rejected_case_zero_mutation_count": 19,
            "unexpected_outcome_count": 0,
            "behavior_role_match_count": 25,
            "dashboard_coverage_row_count": 100,
            "dashboard_commercial_coverage_row_count": 21,
            "dashboard_financial_coverage_row_count": 79,
            "requirement_legal_case_count": 4,
            "requirement_negative_case_count": 13,
            "requirement_negative_zero_mutation_count": 13,
            "installed_tenant_rls_table_count": 66,
            "partial_authority_target_count": 19,
            "partial_authority_event_count": 57,
            "pregrant_physical_public_relation_count": 67,
            "pregrant_application_relation_count": 66,
            "pregrant_excluded_relation_count": 1,
            "pregrant_excluded_application_direct_privilege_count": 0,
            "pregrant_excluded_application_effective_privilege_count": 0,
            "forward_lock_race_passed": True,
            "reverse_lock_race_passed": True,
            "rls_behavior_passed": True,
            "catalog_observer_application_role": True,
            "partial_authority_udu_passed": True,
            "erasure_authority_udu_passed": True,
            "partial_downgrade_catalog_sha256": "1" * 64,
            "erasure_downgrade_catalog_sha256": "1" * 64,
        }
    )
    result["partial_final_catalog_sha256"] = result["partial_initial_catalog_sha256"]
    result["erasure_final_catalog_sha256"] = result["erasure_initial_catalog_sha256"]
    result["seed_dml_role_attestation_sha256"] = result["fixture_dml_operation_ledger_sha256"]
    root_preimage = [
        "task8-v27-census-complete-execution-v2",
        [[field, result[field]] for field in runner._COMPLETE_EXECUTION_SHA256_FIELDS],
    ]
    result["complete_execution_sha256"] = (
        __import__("hashlib").sha256(runner._canonical_bytes(root_preimage)).hexdigest()
    )
    return result


def test_v27_signed_projection_accepts_only_exact_closed_ledger_and_pregrant_receipts() -> None:
    runner = _load()
    value = _valid_safe_execution(runner)
    assert runner._safe_database_execution(value) == value
    assert "fixture_dml_operation_ledger" not in runner._SAFE_EXECUTION_FIELDS

    tampered_values = {
        "fixture_dml_transaction_count": 224,
        "application_fixture_dml_count": 171,
        "application_behavior_dml_count": 46,
        "erasure_behavior_dml_count": 5,
        "fixture_dml_operation_ledger_sha256": "z" * 64,
        "fixture_dml_operation_sequence_sha256": "0" * 64,
        "application_seed_dml_count": 171,
        "seed_dml_role_attestation_sha256": "2" * 64,
        "admin_dml_case_count": 1,
        "pregrant_physical_public_relation_count": 66,
        "pregrant_application_relation_count": 65,
        "pregrant_excluded_relation_count": 0,
        "pregrant_excluded_application_direct_privilege_count": 1,
        "pregrant_excluded_application_effective_privilege_count": 1,
        "pregrant_application_catalog_sha256": "z" * 64,
        "application_privilege_boundary_sha256": "z" * 64,
    }
    for field, tampered in tampered_values.items():
        with pytest.raises(
            runner.Task8V27SignedCensusError,
            match="^TASK8_V27_SIGNED_CENSUS_RECEIPT_INVALID$",
        ):
            runner._safe_database_execution({**value, field: tampered})


def test_v27_safe_projection_does_not_change_complete_execution_root_shape() -> None:
    runner = _load()
    assert runner._COMPLETE_EXECUTION_SHA256_FIELDS == (
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "rls_behavior_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "task5_roundtrip_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "source_freeze_sha256",
        "main_dashboard_census_sha256",
        "requirement_matrix_sha256",
        "requirement_predicate_sha256",
    )
    assert set(runner._COMPLETE_EXECUTION_SHA256_FIELDS).issubset(runner._SAFE_EXECUTION_FIELDS)


def test_v27_safe_projection_recomputes_complete_execution_root() -> None:
    runner = _load()
    value = _valid_safe_execution(runner)
    assert runner._safe_database_execution(value) == value
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_RECEIPT_INVALID$",
    ):
        runner._safe_database_execution({**value, "complete_execution_sha256": "f" * 64})
    for field in runner._COMPLETE_EXECUTION_SHA256_FIELDS:
        changed = {**value, field: "a" * 64}
        with pytest.raises(
            runner.Task8V27SignedCensusError,
            match="^TASK8_V27_SIGNED_CENSUS_RECEIPT_INVALID$",
        ):
            runner._safe_database_execution(changed)


def test_v27_cli_rejects_any_existing_canonical_path_even_when_unrelated(
    tmp_path: Path,
) -> None:
    runner = _load()
    candidate = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    canonical = tmp_path / "unrelated-canonical.json"
    candidate.write_bytes(b"candidate\n")
    fixture.write_bytes(b"fixture\n")
    canonical.write_bytes(b"unrelated\n")
    runner.CANONICAL_ARTIFACT_PATH = canonical
    runner.CANONICAL_FIXTURE_PATH = tmp_path / "absent.json"
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner.load_and_verify_preflight_assets(artifact=candidate, fixture=fixture)


def test_v27_cli_rejects_reverse_canonical_link_to_candidate(tmp_path: Path) -> None:
    runner = _load()
    candidate = tmp_path / "candidate.json"
    fixture = tmp_path / "fixture.json"
    canonical = tmp_path / "canonical.json"
    candidate.write_bytes(b"candidate\n")
    fixture.write_bytes(b"fixture\n")
    try:
        canonical.symlink_to(candidate)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")
    runner.CANONICAL_ARTIFACT_PATH = canonical
    runner.CANONICAL_FIXTURE_PATH = tmp_path / "absent.json"
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_CANONICAL_INPUT_FORBIDDEN$",
    ):
        runner.load_and_verify_preflight_assets(artifact=candidate, fixture=fixture)


def test_v27_cli_rejects_same_candidate_path_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(b"{}\n")
    runner.CANONICAL_ARTIFACT_PATH = tmp_path / "absent-artifact.json"
    runner.CANONICAL_FIXTURE_PATH = tmp_path / "absent-fixture.json"
    touched: list[str] = []
    monkeypatch.setattr(
        runner,
        "validate_preflight_assets",
        lambda **_kwargs: touched.append("validate"),
    )
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        runner.load_and_verify_preflight_assets(artifact=candidate, fixture=candidate)
    assert touched == []


def test_v27_hashed_module_dependency_import_is_no_bytecode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    dependency_name = "_task8_v27_real_dependency"
    dependency_path = tmp_path / f"{dependency_name}.py"
    module_path = tmp_path / "hashed_module.py"
    dependency_path.write_text("VALUE = 7\n", encoding="utf-8")
    module_path.write_text(
        f"import {dependency_name}\nVALUE = {dependency_name}.VALUE\n",
        encoding="utf-8",
    )
    expected = __import__("hashlib").sha256(module_path.read_bytes()).hexdigest()
    cache_root = tmp_path / "redirected-cache"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "pycache_prefix", str(cache_root))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    sys.modules.pop(dependency_name, None)
    loaded = runner._load_hashed_module(
        path=module_path,
        expected_sha256=expected,
        module_name="_task8_v27_dependency_bytecode_canary",
        code="TASK8_V27_SIGNED_CENSUS_RUNNER_UNAUTHORIZED",
    )
    assert loaded.VALUE == 7
    assert sys.dont_write_bytecode is False
    assert not list(tmp_path.rglob("*.pyc"))
    sys.modules.pop(dependency_name, None)


def test_v27_profile_hashed_runner_and_database_execute_under_no_bytecode(
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
            {
                "artifact_raw": b"{}",
                "fixture_raw": b"{}",
                "parent_artifact_raw": b"{}",
                "policy_artifact_raw": b"{}",
                "reviewer_map_raw": b"{}",
            },
        ),
    )
    monkeypatch.setattr(
        runner,
        "validate_v27_dashboard_requirement_assets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(runner, "_current_runner_sha256", lambda: "0" * 64)
    observed: list[tuple[str, bool]] = []

    def material(_mapping: object) -> SimpleNamespace:
        observed.append(("profile", sys.dont_write_bytecode))
        return SimpleNamespace(
            admin_database_url="admin",
            application_database_url="app",
            erasure_database_url="erasure",
            workbook_raw=b"workbook",
        )

    class FakeRunner:
        @staticmethod
        def run_disposable_postgres_census(*_args: object) -> object:
            observed.append(("database", sys.dont_write_bytecode))
            raise RuntimeError("private-db-prose")

    def load_module(**_kwargs: object) -> FakeRunner:
        observed.append(("hashed-runner", sys.dont_write_bytecode))
        return FakeRunner()

    monkeypatch.setattr(runner, "load_operator_material", material)
    monkeypatch.setattr(runner, "_load_hashed_module", load_module)
    original = sys.dont_write_bytecode
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_EXECUTION_FAILED$",
    ) as caught:
        runner.execute_signed_census(
            output=output,
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )
    assert observed == [("profile", True), ("hashed-runner", True), ("database", True)]
    assert sys.dont_write_bytecode is original
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not output.exists()


def test_v27_direct_boundary_sanitizes_known_and_propagates_base_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()

    def known(**_kwargs: object) -> object:
        try:
            raise RuntimeError("private-known-prose")
        except RuntimeError as exc:
            raise runner.Task8V27SignedCensusError(
                "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID"
            ) from exc

    monkeypatch.setattr(runner, "_execute_signed_census_impl", known, raising=False)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID$",
    ) as caught:
        runner.execute_signed_census(
            output=tmp_path / "out.json",
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private-known-prose" not in repr(caught.value)

    monkeypatch.setattr(
        runner,
        "_execute_signed_census_impl",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner.Task8V27SignedCensusError("private-task-error-prose")
        ),
        raising=False,
    )
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_FAILED$",
    ) as malformed:
        runner.execute_signed_census(
            output=tmp_path / "out.json",
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )
    assert malformed.value.__cause__ is None
    assert malformed.value.__context__ is None
    assert "private-task-error-prose" not in repr(malformed.value)

    monkeypatch.setattr(
        runner,
        "_execute_signed_census_impl",
        lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        raising=False,
    )
    with pytest.raises(KeyboardInterrupt):
        runner.execute_signed_census(
            output=tmp_path / "out.json",
            artifact=tmp_path / "artifact.json",
            fixture=tmp_path / "fixture.json",
            process_env={},
        )


@pytest.mark.parametrize(
    ("failure_body", "expected_code"),
    (
        (
            "raise module.Task8V27SignedCensusError("
            "'TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID') from RuntimeError('private-known')",
            "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID",
        ),
        (
            "raise module.Task8V27SignedCensusError('private-task-error')",
            "TASK8_V27_SIGNED_CENSUS_FAILED",
        ),
        ("raise RuntimeError('private-unexpected')", "TASK8_V27_SIGNED_CENSUS_FAILED"),
    ),
)
def test_v27_subprocess_boundary_is_code_only_for_known_and_unexpected_failures(
    tmp_path: Path,
    failure_body: str,
    expected_code: str,
) -> None:
    cache_root = tmp_path / "redirected-cache"
    script = (
        "import importlib.util, pathlib, sys\n"
        f"path = pathlib.Path({str(SIGNED_RUNNER)!r})\n"
        "spec = importlib.util.spec_from_file_location('_v27_subprocess', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "def fail(**kwargs):\n"
        f"    {failure_body}\n"
        "module._execute_signed_census_impl = fail\n"
        "raise SystemExit(module.main(['--artifact','a','--fixture','f','--output','o']))\n"
    )
    env = {
        **os.environ,
        "PYTHONPYCACHEPREFIX": str(cache_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert "private-known" not in completed.stdout
    assert "private-task-error" not in completed.stdout
    assert "private-unexpected" not in completed.stdout
    assert json.loads(completed.stdout)["reason_code"] == expected_code
    assert not list(cache_root.rglob("*.pyc"))


def test_v27_subprocess_boundary_propagates_base_exception(tmp_path: Path) -> None:
    script = (
        "import importlib.util, pathlib, sys\n"
        f"path = pathlib.Path({str(SIGNED_RUNNER)!r})\n"
        "spec = importlib.util.spec_from_file_location('_v27_subprocess_base', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "class Stop(BaseException): pass\n"
        "def fail(**kwargs): raise Stop()\n"
        "module._execute_signed_census_impl = fail\n"
        "try:\n"
        "    module.main(['--artifact','a','--fixture','f','--output','o'])\n"
        "except Stop:\n"
        "    print('BASE_PROPAGATED')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "BASE_PROPAGATED"
    assert completed.stderr == ""


def test_v27_posix_reservation_source_uses_held_parent_relative_operations() -> None:
    runner = _load()
    source = inspect.getsource(runner._create_reserved_output) + inspect.getsource(
        runner._unlink_owned_relative
    )
    assert "dir_fd=parent_lease.descriptor" in source
    assert ".unlink(" not in inspect.getsource(runner._unlink_owned_output)
    assert "os.unlink(" not in inspect.getsource(runner._unlink_owned_relative)
    assert "quarantine_descriptor = os.open(" in source
    assert "os.fstat(quarantine_descriptor)" in source


def test_v27_output_parent_swap_cannot_create_out_of_scope_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    held_original = tmp_path / "held-original"
    output = report_root / "receipt.json"
    runner.REPORT_ROOT = report_root
    original_validate = runner.validate_output_path

    def swap_parent(path: Path) -> Path:
        checked = original_validate(path)
        try:
            report_root.rename(held_original)
            attacker.rename(report_root)
        except OSError:
            raise runner.Task8V27SignedCensusError(
                "TASK8_V27_SIGNED_CENSUS_OUTPUT_PATH_INVALID"
            ) from None
        return checked

    monkeypatch.setattr(runner, "validate_output_path", swap_parent)
    with pytest.raises(
        runner.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_PATH_INVALID$",
    ):
        runner.write_exclusive(output, b"owned\n")
    assert not (report_root / "receipt.json").exists()
    assert not (held_original / "receipt.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-by-handle canary")
def test_v27_windows_cleanup_identity_cannot_swap_after_handle_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    target = tmp_path / "owned.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"replacement\n")
    runner.REPORT_ROOT = tmp_path
    original_mark = runner._mark_windows_descriptor_for_deletion
    blocked: list[bool] = []

    def attack_then_mark(descriptor: int) -> None:
        try:
            target.unlink()
            replacement.rename(target)
        except OSError:
            blocked.append(True)
        original_mark(descriptor)

    monkeypatch.setattr(runner, "_mark_windows_descriptor_for_deletion", attack_then_mark)
    with runner._reserve_output(target):
        pass
    assert blocked == [True]
    assert not target.exists()
    assert replacement.read_bytes() == b"replacement\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race canary")
def test_v27_cleanup_swap_between_check_and_unlink_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    owned_backup = report_root / "owned-backup.json"
    replacement_source = report_root / "replacement-source.json"
    replacement_source.write_bytes(b"replacement\n")
    original_replace = runner._replace_relative
    swapped = False

    def swap_before_quarantine(
        source_name: str,
        target_name: str,
        *,
        parent_lease: object,
    ) -> None:
        nonlocal swapped
        if not swapped and source_name == output.name:
            swapped = True
            os.replace(
                source_name,
                owned_backup.name,
                src_dir_fd=parent_lease.descriptor,
                dst_dir_fd=parent_lease.descriptor,
            )
            os.replace(
                replacement_source.name,
                source_name,
                src_dir_fd=parent_lease.descriptor,
                dst_dir_fd=parent_lease.descriptor,
            )
        original_replace(source_name, target_name, parent_lease=parent_lease)

    monkeypatch.setattr(runner, "_replace_relative", swap_before_quarantine)
    with (
        pytest.raises(
            runner.Task8V27SignedCensusError,
            match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED$",
        ),
        runner._reserve_output(output),
    ):
        pass
    assert output.read_bytes() == b"replacement\n"
    assert owned_backup.read_bytes() == b""
    retained = [path for path in report_root.iterdir() if ".cleanup-" in path.name]
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"replacement\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine contract")
def test_v27_posix_cleanup_retains_zero_byte_owned_tombstone(tmp_path: Path) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    with runner._reserve_output(output):
        pass
    assert not output.exists()
    tombstones = [path for path in report_root.iterdir() if ".cleanup-" in path.name]
    assert len(tombstones) == 1
    assert tombstones[0].read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="POSIX held-descriptor contract")
def test_v27_posix_swapped_owned_inode_is_zeroed_and_replacement_preserved(
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    owned_backup = report_root / "owned-backup.json"
    replacement = report_root / "replacement.json"
    replacement.write_bytes(b"replacement\n")
    with runner._reserve_output(output) as reservation:
        assert os.write(reservation.descriptor, b"private-partial-receipt") > 0
        os.fsync(reservation.descriptor)
        output.rename(owned_backup)
        replacement.rename(output)
    assert owned_backup.read_bytes() == b""
    assert output.read_bytes() == b"replacement\n"
    assert not [path for path in report_root.iterdir() if ".cleanup-" in path.name]


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine contract")
def test_v27_posix_cleanup_replay_never_clobbers_old_tombstone(tmp_path: Path) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    for expected_count in (1, 2):
        with runner._reserve_output(output):
            pass
        assert not output.exists()
        tombstones = [path for path in report_root.iterdir() if ".cleanup-" in path.name]
        assert len(tombstones) == expected_count
        assert all(path.read_bytes() == b"" for path in tombstones)


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-clobber quarantine contract")
def test_v27_posix_quarantine_collision_retries_without_clobber(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    old_tombstone = report_root / ".receipt.json.cleanup-collision"
    old_tombstone.write_bytes(b"foreign-old-tombstone\n")
    tokens = iter(("collision", "fresh"))
    monkeypatch.setattr(runner.secrets, "token_hex", lambda _size: next(tokens))
    with runner._reserve_output(output):
        pass
    assert not output.exists()
    assert old_tombstone.read_bytes() == b"foreign-old-tombstone\n"
    fresh_tombstone = report_root / ".receipt.json.cleanup-fresh"
    assert fresh_tombstone.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine contract")
def test_v27_posix_post_identity_check_swap_never_deletes_foreign_inode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    foreign_source = report_root / "foreign-source.json"
    owned_backup = report_root / "owned-backup.json"
    foreign_source.write_bytes(b"foreign\n")
    real_stat = os.stat
    swapped = False

    def swap_before_final_identity_check(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal swapped
        if not swapped and isinstance(path, str) and ".cleanup-" in path:
            swapped = True
            parent_fd = kwargs["dir_fd"]
            os.replace(
                path,
                owned_backup.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.replace(
                foreign_source.name,
                path,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(runner.os, "stat", swap_before_final_identity_check)
    with (
        pytest.raises(
            runner.Task8V27SignedCensusError,
            match="^TASK8_V27_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED$",
        ),
        runner._reserve_output(output),
    ):
        pass
    assert output.read_bytes() == b"foreign\n"
    assert owned_backup.read_bytes() == b""
    foreign_tombstones = [path for path in report_root.iterdir() if ".cleanup-" in path.name]
    assert len(foreign_tombstones) == 1
    assert foreign_tombstones[0].read_bytes() == b"foreign\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO quarantine contract")
def test_v27_posix_quarantine_fifo_replacement_never_blocks(tmp_path: Path) -> None:
    script = (
        "import importlib.util, os, pathlib, stat, sys\n"
        f"path = pathlib.Path({str(SIGNED_RUNNER)!r})\n"
        "spec = importlib.util.spec_from_file_location('_v27_fifo_canary', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"root = pathlib.Path({str(tmp_path)!r}) / '.local_reports/slice100/task8'\n"
        "root.mkdir(parents=True)\n"
        "module.REPORT_ROOT = root\n"
        "output = root / 'receipt.json'\n"
        "owned_backup = root / 'owned-backup.json'\n"
        "original_replace = module._replace_relative\n"
        "injected = [False]\n"
        "def replace_then_fifo(source, target, *, parent_lease):\n"
        "    original_replace(source, target, parent_lease=parent_lease)\n"
        "    if not injected[0] and source == output.name:\n"
        "        injected[0] = True\n"
        "        os.replace(target, owned_backup.name, "
        "src_dir_fd=parent_lease.descriptor, dst_dir_fd=parent_lease.descriptor)\n"
        "        os.mkfifo(target, 0o600, dir_fd=parent_lease.descriptor)\n"
        "module._replace_relative = replace_then_fifo\n"
        "try:\n"
        "    with module._reserve_output(output):\n"
        "        pass\n"
        "except module.Task8V27SignedCensusError as exc:\n"
        "    assert exc.code == 'TASK8_V27_SIGNED_CENSUS_OUTPUT_CLEANUP_FAILED'\n"
        "else:\n"
        "    raise AssertionError('FIFO replacement did not fail closed')\n"
        "assert owned_backup.read_bytes() == b''\n"
        "assert stat.S_ISFIFO(os.lstat(output).st_mode)\n"
        "print('FIFO_CANARY_PASS')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "FIFO_CANARY_PASS"
    assert completed.stderr == ""


_STAGED_V27_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_reviewed_preimage_v1.json"
)
_STAGED_V27_FIXTURE = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_contract_hash_v1.json"
)
_POLICY_V2_PATH = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
_REVIEWER_MAP_PATH = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_PARENT_V21_PATH = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_CANONICAL_V27_PATHS = (
    REPO_ROOT / "contracts" / "full_strict_v27_reviewed_preimage_v1.json",
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v27_contract_hash_v1.json",
)
_BINDING_CODE = "TASK8_V27_DASHBOARD_REQUIREMENT_BINDING_INVALID"
_BINDING_MARKER = "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE"
_SYNTHETIC_AUTHORITY_SHA256 = "11" * 32


_OTHER_BINDING_CASE = "LEGACY_FOUR_FIELD_REQUIREMENT"
_BOTH_BINDING_CASES = (_BINDING_MARKER, _OTHER_BINDING_CASE)


def _assert_files_present(paths: tuple[Path, ...], *, label: str) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    assert not missing, f"{label}: " + "; ".join(missing)


def _requirement(*, required: bool = True, extra_field: bool = False) -> dict[str, object]:
    requirement: dict[str, object] = {
        "formula_ordinal": 1,
        "formula_type": "GROSS_MARGIN",
        "required": required,
        "scope": "income",
    }
    if extra_field:
        requirement["note"] = "drift"
    return requirement


def _dashboard_sql(
    *,
    calc_type_count: int = 4,
    formula_type_count: int = 5,
    formula_ordinal_count: int = 2,
) -> str:
    return " ".join(
        ["item->>'calc_type'"] * calc_type_count
        + ["item->>'formula_type'"] * formula_type_count
        + ["item->>'formula_ordinal'"] * formula_ordinal_count
    )


def _binding_requirements(
    *,
    required: bool = True,
    extra_field: bool = False,
) -> tuple[dict[str, object], ...]:
    items = [_requirement() for _index in range(9)]
    items.append(_requirement(required=required, extra_field=extra_field))
    return tuple(items)


def _binding_pair(
    *,
    dashboard_sql: str | None = None,
    negative_cases: tuple[str, ...] = _BOTH_BINDING_CASES,
    illegal_cases: tuple[str, ...] = _BOTH_BINDING_CASES,
    requirements: tuple[dict[str, object], ...] | None = None,
) -> tuple[bytes, bytes, str]:
    body = _dashboard_sql() if dashboard_sql is None else dashboard_sql
    requirement_rows = _binding_requirements() if requirements is None else requirements
    artifact = {
        "sql_authority_registry": {
            "rows": [
                {
                    "authority_id": "dashboard-root-equation-v3",
                    "expected_sha256": _SYNTHETIC_AUTHORITY_SHA256,
                    "body_sql_utf8_hex_tuple": [body.encode("utf-8").hex()],
                    "illegal_red_case_tuple": list(illegal_cases),
                }
            ]
        },
        "task8_fdd_contract": {
            "dashboard_contract": {
                "requirement_binding_contract": {"negative_case_tuple": list(negative_cases)}
            }
        },
    }
    policy = {
        "workbook_row_registry": {"rows": [{"required_calculations": list(requirement_rows)}]}
    }
    return (
        json.dumps(artifact, separators=(",", ":")).encode("utf-8"),
        json.dumps(policy, separators=(",", ":")).encode("utf-8"),
        body,
    )


def _patch_binding_pins(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    artifact_raw: bytes,
    policy_raw: bytes,
    dashboard_sql: str,
) -> None:
    monkeypatch.setattr(runner, "_ARTIFACT_SHA256", hashlib.sha256(artifact_raw).hexdigest())
    monkeypatch.setattr(runner, "_POLICY_ARTIFACT_SHA256", hashlib.sha256(policy_raw).hexdigest())
    monkeypatch.setattr(runner, "_DASHBOARD_AUTHORITY_SHA256", _SYNTHETIC_AUTHORITY_SHA256)
    monkeypatch.setattr(
        runner,
        "_DASHBOARD_AUTHORITY_BODY_SHA256",
        hashlib.sha256(dashboard_sql.encode("utf-8")).hexdigest(),
    )


def test_v27_dashboard_requirement_binding_accepts_staged_real_bytes() -> None:
    runner = _load()
    _assert_files_present(
        (_STAGED_V27_ARTIFACT, _POLICY_V2_PATH),
        label="missing dashboard requirement input",
    )
    runner.validate_v27_dashboard_requirement_assets(
        artifact_raw=_STAGED_V27_ARTIFACT.read_bytes(),
        policy_raw=_POLICY_V2_PATH.read_bytes(),
    )


def test_v27_dashboard_requirement_binding_rejects_each_synthetic_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load()
    happy_artifact, happy_policy, happy_sql = _binding_pair()
    _patch_binding_pins(monkeypatch, runner, happy_artifact, happy_policy, happy_sql)
    runner.validate_v27_dashboard_requirement_assets(
        artifact_raw=happy_artifact,
        policy_raw=happy_policy,
    )
    other = (_OTHER_BINDING_CASE,)
    rejections = (
        (
            "marker missing from negative_case_tuple",
            _binding_pair(negative_cases=other, illegal_cases=_BOTH_BINDING_CASES),
        ),
        (
            "marker missing from illegal_red_case_tuple",
            _binding_pair(negative_cases=_BOTH_BINDING_CASES, illegal_cases=other),
        ),
        (
            "wrong requirement field set",
            _binding_pair(requirements=_binding_requirements(extra_field=True)),
        ),
        (
            "required false",
            _binding_pair(requirements=_binding_requirements(required=False)),
        ),
        (
            "calc_type count off by one",
            _binding_pair(dashboard_sql=_dashboard_sql(calc_type_count=5)),
        ),
        (
            "formula_type count off by one",
            _binding_pair(dashboard_sql=_dashboard_sql(formula_type_count=6)),
        ),
        (
            "formula_ordinal count off by one",
            _binding_pair(dashboard_sql=_dashboard_sql(formula_ordinal_count=3)),
        ),
    )
    for name, (artifact_raw, policy_raw, dashboard_sql) in rejections:
        _patch_binding_pins(monkeypatch, runner, artifact_raw, policy_raw, dashboard_sql)
        rejected = False
        try:
            runner.validate_v27_dashboard_requirement_assets(
                artifact_raw=artifact_raw,
                policy_raw=policy_raw,
            )
        except runner.Task8V27SignedCensusError as exc:
            rejected = True
            assert exc.code == _BINDING_CODE, name
        assert rejected, name


def test_v27_cli_reaches_profile_boundary_on_staged_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()
    _assert_files_present(
        (
            _STAGED_V27_ARTIFACT,
            _STAGED_V27_FIXTURE,
            _POLICY_V2_PATH,
            _REVIEWER_MAP_PATH,
            _PARENT_V21_PATH,
        ),
        label="missing signed census input",
    )
    present = [str(path) for path in _CANONICAL_V27_PATHS if os.path.lexists(path)]
    assert not present, "canonical input must stay absent: " + "; ".join(present)
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    output = report_root / "receipt.json"
    runner.REPORT_ROOT = report_root
    observed: list[dict[str, str]] = []

    def record_profile(process_env: object) -> object:
        assert isinstance(process_env, dict)
        observed.append(dict(process_env))
        raise runner.Task8V27SignedCensusError("TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "load_operator_material", record_profile)
    exit_code = runner.main(
        [
            "--artifact",
            str(_STAGED_V27_ARTIFACT),
            "--fixture",
            str(_STAGED_V27_FIXTURE),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    assert observed == [{"IDIS_STRICT_DOTENV_PATH": os.environ.get("IDIS_STRICT_DOTENV_PATH", "")}]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "reason_code": "TASK8_V27_SIGNED_CENSUS_PROFILE_INVALID",
        "result": "FAILED",
        "schema_version": "task8-v27-signed-census-cli-result-v1",
    }
    assert "Traceback" not in captured.err
    assert not output.exists()
    assert list(report_root.glob("*.json")) == []
