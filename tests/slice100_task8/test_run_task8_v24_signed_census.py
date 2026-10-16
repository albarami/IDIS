"""Hermetic CLI and exclusive-output contracts for the v24 signed census."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v24_signed_census.py"


def _load() -> ModuleType:
    assert SIGNED_RUNNER.is_file()
    name = "_task8_v24_signed_census_cli_red"
    spec = importlib.util.spec_from_file_location(name, SIGNED_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v24_cli_private_mapping_has_only_dotenv_locator() -> None:
    runner = _load()
    assert runner.private_profile_mapping(
        {
            "IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env",
            "IDIS_DATABASE_ADMIN_URL": "not-forwarded",
            "IDIS_DATABASE_URL": "not-forwarded",
            "IDIS_ERASURE_DATABASE_URL": "not-forwarded",
        }
    ) == {"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"}
    source = inspect.getsource(runner.execute_signed_census)
    assert "dict(os.environ)" not in source
    assert 'os.environ.get("IDIS_STRICT_DOTENV_PATH", "")' in source
    assert 'os.environ.get("IDIS_DATABASE_URL"' not in source
    assert 'os.environ.get("IDIS_DATABASE_ADMIN_URL"' not in source
    assert 'os.environ.get("IDIS_ERASURE_DATABASE_URL"' not in source


def test_v24_cli_output_is_exclusive_and_task8_scoped(tmp_path: Path) -> None:
    runner = _load()
    report_root = tmp_path / ".local_reports" / "slice100" / "task8"
    report_root.mkdir(parents=True)
    runner.REPORT_ROOT = report_root
    output = report_root / "receipt.json"
    runner.write_exclusive(output, b"first\n")
    assert output.read_bytes() == b"first\n"
    with pytest.raises(
        runner.Task8V24SignedCensusError,
        match="TASK8_V24_SIGNED_CENSUS_OUTPUT_EXISTS",
    ):
        runner.write_exclusive(output, b"second\n")
    outside = tmp_path / "outside.json"
    with pytest.raises(
        runner.Task8V24SignedCensusError,
        match="TASK8_V24_SIGNED_CENSUS_OUTPUT_PATH_INVALID",
    ):
        runner.validate_output_path(outside)


def test_v24_cli_success_event_is_safe_and_hash_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()
    monkeypatch.setattr(
        runner,
        "execute_signed_census",
        lambda **_kwargs: {
            "schema_version": "task8-v24-signed-census-cli-result-v1",
            "result": "PASS",
            "receipt_sha256": "a" * 64,
        },
    )
    assert runner.main(["--output", str(runner.DEFAULT_OUTPUT)]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event == {
        "receipt_sha256": "a" * 64,
        "result": "PASS",
        "schema_version": "task8-v24-signed-census-cli-result-v1",
    }


def test_v24_cli_failure_event_never_exposes_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()

    def fail(**_kwargs: object) -> object:
        raise runner.Task8V24SignedCensusError("TASK8_V24_SIGNED_CENSUS_PROFILE_INVALID")

    monkeypatch.setattr(runner, "execute_signed_census", fail)
    assert runner.main(["--output", str(runner.DEFAULT_OUTPUT)]) == 2
    event = json.loads(capsys.readouterr().out)
    assert event == {
        "reason_code": "TASK8_V24_SIGNED_CENSUS_PROFILE_INVALID",
        "result": "FAILED",
        "schema_version": "task8-v24-signed-census-cli-result-v1",
    }


def test_v24_cli_argument_failure_is_closed_and_safe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load()
    assert runner.main(["--unknown"]) == 2
    event = json.loads(capsys.readouterr().out)
    assert event["result"] == "FAILED"
    assert event["reason_code"] == "TASK8_V24_SIGNED_CENSUS_ARGUMENTS_INVALID"
    assert set(event) == {"schema_version", "result", "reason_code"}
