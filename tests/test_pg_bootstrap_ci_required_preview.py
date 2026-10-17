"""Required CI fixtures are exclusive disposable mechanics, not FULL acceptance."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, NoReturn
from unittest.mock import MagicMock

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
KEYS = (
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
)
C3_FILES = (
    "tests/test_cir04c3_inline_claim_postgres.py",
    "tests/test_cir04c3_ingestion_provenance.py",
    "tests/test_cir04c3_frozen_preview_postgres.py",
    "tests/test_cir04c2_proof_admission_postgres.py",
    "tests/test_cir04c2_postgres_compat.py",
)
_PREVIEW_FAILURE_CODE_NAME = "preview-failure-code"


@pytest.fixture
def bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "required_preview_bootstrap", REPO / "scripts/pg_bootstrap_ci.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ci_env(tmp_path: Path) -> dict[str, str]:
    runner = tmp_path / "runner spaced"
    runner.mkdir()
    workspace = tmp_path / "public workspace"
    workspace.mkdir()
    command_file = tmp_path / "commands"
    command_file.write_bytes(b"EXISTING=retained\n")
    return {
        "RUNNER_TEMP": str(runner),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_RUN_ID": "1234",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_ENV": str(command_file),
        "IDIS_PG_HOST": "127.0.0.1",
        "IDIS_PG_PORT": "54321",
        "PG_ADMIN_USER": "postgres",
        "PG_ADMIN_PASSWORD": "public-synthetic-admin",
        "IDIS_PG_APP_USER": "idis_app",
        "IDIS_PG_APP_PASSWORD": "public-synthetic-app",
        "IDIS_CI_POSTGRES_SERVICE_ID": "a" * 64,
        "PATH": "/ambient/not-qualified",
    }


def test_required_preview_mode_exists_before_connecting(bootstrap: ModuleType) -> None:
    assert callable(getattr(bootstrap, "required_preview_config", None))
    assert callable(getattr(bootstrap, "required_preview_main", None))


@pytest.mark.skipif(
    sys.platform != "linux", reason="Real Linux held-child/FIFO signal qualification"
)
@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_required_preview_signal_uses_owned_capture_retirement_and_closed_status(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    tmp_path: Path,
    number: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Linux-only physical signal qualification")
    fifo, ready = tmp_path / "worker.fifo", tmp_path / "ready.json"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    worker = tmp_path / "public synthetic worker.py"
    worker.write_text(
        "import json,os,pathlib,time\n"
        f"fd=os.open({str(fifo)!r},os.O_WRONLY)\n"
        f"p=pathlib.Path({str(ready)!r})\n"
        "temporary=p.with_suffix('.publishing')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid()}))\n"
        "temporary.replace(p)\n"
        "print('SYNTHETIC_WORKER_DIAGNOSTIC_CLOSED',flush=True)\n"
        "time.sleep(30)\n"
    )
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import ci_private_inputs as capture

    real_popen = subprocess.Popen
    peer = real_popen([sys.executable, "-c", "import time;time.sleep(30)"])
    children: list[subprocess.Popen[bytes]] = []
    previous = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}

    def emergency_interrupt(number: int, frame: object) -> None:
        raise KeyboardInterrupt

    def construct(
        arguments: Sequence[str],
        *,
        env: Mapping[str, str],
        stdout: BinaryIO,
        stderr: int,
        start_new_session: bool,
    ) -> subprocess.Popen[bytes]:
        child = real_popen(
            arguments, env=env, stdout=stdout, stderr=stderr, start_new_session=start_new_session
        )
        # Direct Popen custody is recorded BEFORE any readiness/metadata operation can fail.
        children.append(child)
        handed_off = False
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.is_file(), "synthetic worker did not acquire its FIFO"
            os.kill(os.getpid(), number)  # Real signal at capture's construction boundary.
            handed_off = True
            return child
        finally:
            if not handed_off:
                capture.stop_capture_child(child)

    try:
        for n in previous:
            signal.signal(n, emergency_interrupt)
        monkeypatch.setattr(bootstrap, "__file__", str(worker))
        monkeypatch.setattr(bootstrap.os, "environ", ci_env)
        monkeypatch.setattr(capture.subprocess, "Popen", construct)
        assert bootstrap.required_preview_main("check") == 128 + number
        summary = json.loads(capsys.readouterr().out)
        assert summary == {
            "required_preview": "failed",
            "exit": 128 + number,
            "cleanup_failed": False,
            "code": "PREVIEW_UNCLASSIFIED",
            "phase": "bootstrap",
        }
        assert os.read(reader, 1) == b"", "capture left a live FIFO-owning worker"
        assert peer.poll() is None, "unrelated peer was mutated"
        assert Path(ci_env["GITHUB_ENV"]).read_bytes() == b"EXISTING=retained\n"
    finally:
        try:
            for child in children:
                capture.stop_capture_child(child)
        finally:
            if peer.poll() is None:
                peer.terminate()
            peer.wait(timeout=5)
            os.close(reader)
            for n, handler in previous.items():
                signal.signal(n, handler)


def test_preview_config_has_exact_loopback_context_and_exclusive_name(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    assert config.host == "127.0.0.1"
    assert type(config.port) is int and config.port == 54321
    assert config.root == Path(ci_env["RUNNER_TEMP"]) / "idis-c3-check-1234-2"
    assert config.database.startswith("idis_c3_callers_1234_2_")
    assert len(config.database.encode()) <= 63
    assert len(config.database.rsplit("_", 1)[1]) == 16
    assert config.service_container_id == "a" * 64
    assert not config.root.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("IDIS_PG_HOST", "localhost"),
        ("IDIS_PG_PORT", "0"),
        ("IDIS_PG_PORT", "65536"),
        ("GITHUB_RUN_ID", "../foreign"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("IDIS_CI_POSTGRES_SERVICE_ID", "mutable-tag"),
        ("IDIS_DATABASE_URL", "postgresql://foreign/database"),
        ("IDIS_DATABASE_ADMIN_URL", "postgresql://foreign/database"),
        ("IDIS_DATABASE_ERASURE_URL", "postgresql://foreign/database"),
        ("IDIS_ERASURE_DATABASE_URL", "postgresql://foreign/database"),
        ("IDIS_REQUIRE_POSTGRES", "1"),
        (KEYS[0], "preexisting"),
        (KEYS[1], "preexisting"),
        (KEYS[2], "preexisting"),
        ("RUNNER_DEBUG", "1"),
    ],
)
def test_invalid_configuration_rejected_before_root_or_database_mutation(
    bootstrap: ModuleType, ci_env: dict[str, str], key: str, value: str
) -> None:
    ci_env[key] = value
    with pytest.raises(ValueError):
        bootstrap.required_preview_config("check", ci_env)
    assert list(Path(ci_env["RUNNER_TEMP"]).iterdir()) == []


def test_clean_environment_drops_all_libpq_and_private_authority(
    bootstrap: ModuleType, ci_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    dirty = dict(ci_env)
    dirty.update(
        PGOPTIONS="-c role=postgres",
        PGSERVICE="foreign",
        PGPASSFILE="foreign",
        PGSSLMODE="disable",
        IDIS_RECOVERY_READ_KEY="DO_NOT_FORWARD",
        IDIS_CI_READ_TOKEN="DO_NOT_FORWARD",
        GITHUB_TOKEN="DO_NOT_FORWARD",
        PYTHONPATH="foreign",
        BASH_ENV="foreign",
    )
    for key, value in dirty.items():
        monkeypatch.setenv(key, value)
    clean = bootstrap.required_preview_environment(config)
    assert clean["PATH"].split(os.pathsep)[0] == str(config.workspace / ".venv" / "bin")
    assert clean["HOME"] == str(config.root / "home")
    assert clean["TMPDIR"] == str(config.root / "tmp")
    assert (
        not set(dirty)
        .intersection(clean)
        .intersection(
            {
                "PGOPTIONS",
                "PGSERVICE",
                "PGPASSFILE",
                "PGSSLMODE",
                "IDIS_RECOVERY_READ_KEY",
                "IDIS_CI_READ_TOKEN",
                "GITHUB_TOKEN",
                "PYTHONPATH",
                "BASH_ENV",
                "GITHUB_ENV",
            }
        )
    )
    assert {key for key in clean if key.startswith("PG")} == {"PG_ADMIN_USER", "PG_ADMIN_PASSWORD"}


def test_duplicate_create_never_falls_back_to_lookup_grant_or_migration(
    bootstrap: ModuleType, ci_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlalchemy

    config = bootstrap.required_preview_config("check", ci_env)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    duplicate = sqlalchemy.exc.ProgrammingError("CREATE", None, RuntimeError("synthetic duplicate"))
    connection.execute.side_effect = duplicate
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: engine)
    before = Path(ci_env["GITHUB_ENV"]).read_bytes()
    with pytest.raises(sqlalchemy.exc.ProgrammingError) as raised:
        bootstrap._create_required_database(config)
    assert raised.value is duplicate
    assert connection.execute.call_count == 1
    assert (
        str(connection.execute.call_args.args[0])
        == f'CREATE DATABASE "{config.database}" TEMPLATE template0'
    )
    assert Path(ci_env["GITHUB_ENV"]).read_bytes() == before
    assert not config.root.exists()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("flag", range(7))
def test_catalog_posture_failure_stops_role_or_database_mutations(
    bootstrap: ModuleType, ci_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, flag: int
) -> None:
    import sqlalchemy

    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    config = bootstrap.required_preview_config("check", ci_env)
    engine = MagicMock()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: engine)
    row = [True, False, False, False, False, False, False]
    row[flag] = not row[flag]

    def actual_posture_validation(connection: object, name: str) -> NoReturn:
        bootstrap.validate_required_app_posture(row, 0)
        raise AssertionError("Invalid posture was unexpectedly accepted")

    monkeypatch.setattr(bootstrap, "_required_app_present", actual_posture_validation)
    create_role = MagicMock()
    monkeypatch.setattr(bootstrap, "create_app_role", create_role)
    with pytest.raises(ValueError):
        bootstrap._prepare_required_roles(config, create_missing=True)
    create_role.assert_not_called()
    engine.begin.assert_not_called()
    assert not config.root.exists()


@pytest.mark.parametrize("bad", ["\n", "\r", "<<"])
def test_runner_path_command_syntax_is_rejected_before_effects(
    bootstrap: ModuleType, ci_env: dict[str, str], tmp_path: Path, bad: str
) -> None:
    ci_env["RUNNER_TEMP"] = str(tmp_path / ("runner" + bad + "foreign"))
    with pytest.raises(ValueError):
        bootstrap.required_preview_config("check", ci_env)
    assert not Path(ci_env["RUNNER_TEMP"]).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "localhost"),
        ("port", True),
        ("port", "54321"),
        ("database_oid", True),
        ("revision", 38),
        ("revision", "0037"),
        ("database", "idis_c3_callers_9999_2_" + "a" * 16),
        ("service_container_id", "foreign"),
        ("absent_before_create", False),
        ("role_flags", {"rolsuper": True, "rolbypassrls": False}),
    ],
)
def test_receipt_mismatch_rejected_before_consumer_launch(
    bootstrap: ModuleType, ci_env: dict[str, str], field: str, value: object
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    receipt = bootstrap.required_preview_receipt(config, 123)
    receipt[field] = value
    bootstrap.write_required_preview_receipt(config, receipt)
    with pytest.raises(ValueError):
        bootstrap._read_required_preview_receipt(config)


def test_receipt_is_no_clobber_and_exactly_three_exports_after_verification(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    config.root.mkdir(mode=0o700)
    receipt = bootstrap.required_preview_receipt(config, 65432)
    assert receipt["absent_before_create"] is True
    assert receipt["host"] == "127.0.0.1" and type(receipt["port"]) is int
    assert type(receipt["database_oid"]) is int
    assert receipt["revision"] == "0038"
    path, digest = bootstrap.write_required_preview_receipt(config, receipt)
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    assert json.loads(raw) == receipt
    with pytest.raises(FileExistsError):
        bootstrap.write_required_preview_receipt(config, receipt)
    assert path.read_bytes() == raw
    bootstrap.export_required_preview(config, path, digest)
    lines = Path(ci_env["GITHUB_ENV"]).read_text().splitlines()
    assert lines[0] == "EXISTING=retained"
    assert [line.partition("=")[0] for line in lines[1:]] == list(KEYS)
    assert len(lines) == 4 and not any("<<" in line for line in lines)


@pytest.mark.parametrize("oid", [True, "123", 0, -1])
def test_receipt_rejects_non_integer_or_nonpositive_oid(
    bootstrap: ModuleType, ci_env: dict[str, str], oid: object
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    with pytest.raises(ValueError):
        bootstrap.required_preview_receipt(config, oid)


def test_pg_mode_never_exports_to_runner_environment(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    config.root.mkdir(mode=0o700)
    path, digest = bootstrap.write_required_preview_receipt(
        config, bootstrap.required_preview_receipt(config, 123)
    )
    before = Path(ci_env["GITHUB_ENV"]).read_bytes()
    with pytest.raises(ValueError):
        bootstrap.export_required_preview(config, path, digest)
    assert Path(ci_env["GITHUB_ENV"]).read_bytes() == before


def test_required_c3_runner_uses_fixed_captured_test_phase_and_preserves_real_exit(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    bootstrap.write_required_preview_receipt(
        config, bootstrap.required_preview_receipt(config, 123)
    )
    phases = []

    def captured(bound: object, *, phase: str) -> tuple[int, bool]:
        phases.append(phase)
        if phase == "revalidate":
            return 0, False
        assert phase == "tests"
        (config.root / "c3-tests.log").write_bytes(b"PUBLIC_SYNTHETIC_TEST_FAILURE\n")
        return 7, False

    monkeypatch.setattr(bootstrap, "_capture_required_preview", captured)
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    assert bootstrap.run_required_preview_postgres() == 7
    assert phases == ["revalidate", "tests"]
    assert capsys.readouterr().out == "PUBLIC_SYNTHETIC_TEST_FAILURE\n"
    assert Path(ci_env["GITHUB_ENV"]).read_bytes() == b"EXISTING=retained\n"


def test_required_tests_phase_invokes_pinned_modules_fail_closed(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receipt-bound tests phase runs the pinned modules and cannot skip."""
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import ci_private_inputs as capture

    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    seen: dict[str, object] = {}

    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        seen["argv"] = shlex.split(script.read_text(encoding="utf-8").splitlines()[-1])
        seen["environment"] = environment
        seen["log"] = log.name
        del bash
        return 0

    monkeypatch.setattr(capture, "capture_script", fake_capture)
    monkeypatch.setattr(capture, "bash_path", lambda: "/usr/bin/bash")
    assert bootstrap._capture_required_preview(config, phase="tests") == (0, False)
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "exec"
    assert argv[2:4] == ["-m", "pytest"]
    assert argv[4:-2] == list(C3_FILES)
    assert argv[-2:] == ["-v", "--junitxml=c3_postgres_integration_pytest.xml"]
    environment = seen["environment"]
    assert isinstance(environment, Mapping)
    assert environment["IDIS_REQUIRE_POSTGRES"] == "1"
    assert environment["IDIS_DATABASE_URL"] == config.database_url()
    assert environment["IDIS_DATABASE_ADMIN_URL"] == config.database_url(admin=True)
    assert seen["log"] == "c3-tests.log"


def test_required_preview_failure_json_pins_code_and_phase(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    assert bootstrap.required_preview_main("not-a-context") == 1
    assert capsys.readouterr().out == (
        '{"required_preview":"failed","code":"PREVIEW_CONTEXT_INVALID","phase":"bootstrap"}\n'
    )


def test_required_preview_unclassified_failure_hides_exception_text(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(context: str, env: Mapping[str, str], *, worker: bool = False) -> object:
        del context, env, worker
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(bootstrap, "required_preview_config", explode)
    assert bootstrap.required_preview_main("check") == 1
    captured = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in captured
    assert captured == (
        '{"required_preview":"failed","code":"PREVIEW_UNCLASSIFIED","phase":"bootstrap"}\n'
    )


def test_required_c3_blocked_json_pins_receipt_code_and_revalidate_phase(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    (config.root / "database-receipt.json").write_bytes(b"{}\n")
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    assert bootstrap.run_required_preview_postgres() == 1
    captured = capsys.readouterr().out
    assert "database-receipt" not in captured
    assert captured == (
        '{"required_c3_postgres":"blocked","code":"PREVIEW_RECEIPT_INVALID","phase":"revalidate"}\n'
    )


def test_required_c3_tests_phase_failure_hides_exception_text(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    monkeypatch.setattr(
        bootstrap,
        "_read_required_preview_receipt",
        lambda bound: (bound, bound.root / "database-receipt.json", "a" * 64, 123),
    )

    def captured(bound: object, *, phase: str) -> tuple[int, bool]:
        del bound
        if phase == "revalidate":
            return 0, False
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(bootstrap, "_capture_required_preview", captured)
    assert bootstrap.run_required_preview_postgres() == 1
    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in output
    assert output == (
        '{"required_c3_postgres":"blocked","code":"PREVIEW_UNCLASSIFIED","phase":"tests"}\n'
    )


def _plant_preview_code(root: Path, raw: bytes) -> None:
    fd = os.open(root / _PREVIEW_FAILURE_CODE_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise OSError("short preview-code plant")
    finally:
        os.close(fd)


def _stub_child_code(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    raw: bytes,
    exit_code: int,
) -> None:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import ci_private_inputs as capture

    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        del script, log, environment, bash
        _plant_preview_code(root, raw)
        return exit_code

    monkeypatch.setattr(capture, "capture_script", fake_capture)
    monkeypatch.setattr(capture, "bash_path", lambda: "/usr/bin/bash")


def test_bootstrap_child_failure_code_is_public(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A worker-child allow-listed code reaches the parent exit line."""
    root = Path(ci_env["RUNNER_TEMP"]) / "idis-c3-check-1234-2"
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    _stub_child_code(bootstrap, monkeypatch, root, b"PREVIEW_APP_MISSING", 1)
    assert bootstrap.required_preview_main("check") == 1
    assert capsys.readouterr().out == (
        '{"required_preview":"failed","exit":1,"cleanup_failed":false,'
        '"code":"PREVIEW_APP_MISSING","phase":"bootstrap"}\n'
    )
    info = (root / _PREVIEW_FAILURE_CODE_NAME).stat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    bootstrap._validate_required_preview_root(bootstrap.required_preview_config("check", ci_env))


def test_revalidate_child_failure_code_is_public(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A revalidate-child allow-listed code reaches the blocked exit line."""
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    bootstrap.write_required_preview_receipt(
        config, bootstrap.required_preview_receipt(config, 123)
    )
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    _stub_child_code(bootstrap, monkeypatch, config.root, b"PREVIEW_REVISION_INVALID", 4)
    assert bootstrap.run_required_preview_postgres() == 4
    assert capsys.readouterr().out == (
        '{"required_c3_postgres":"blocked","exit":4,"cleanup_failed":false,'
        '"code":"PREVIEW_REVISION_INVALID","phase":"revalidate"}\n'
    )


def test_non_allow_listed_child_code_is_unclassified(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(ci_env["RUNNER_TEMP"]) / "idis-c3-check-1234-2"
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)
    _stub_child_code(bootstrap, monkeypatch, root, b"PREVIEW_NOT_ALLOW_LISTED", 1)
    assert bootstrap.required_preview_main("check") == 1
    captured = capsys.readouterr().out
    assert "PREVIEW_NOT_ALLOW_LISTED" not in captured
    assert captured == (
        '{"required_preview":"failed","exit":1,"cleanup_failed":false,'
        '"code":"PREVIEW_UNCLASSIFIED","phase":"bootstrap"}\n'
    )
    assert (root / _PREVIEW_FAILURE_CODE_NAME).read_bytes() == b"PREVIEW_NOT_ALLOW_LISTED"


def test_child_boundary_records_allow_listed_code_only(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    bootstrap.create_required_preview_root(config)
    code_path = config.root / bootstrap._PREVIEW_FAILURE_CODE_NAME
    assert bootstrap._PREVIEW_FAILURE_CODE_NAME == _PREVIEW_FAILURE_CODE_NAME

    def unclassified() -> None:
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    with pytest.raises(RuntimeError, match="^SYNTHETIC_PRIVATE_DIAGNOSTIC$"):
        bootstrap._call_preview_child(config, unclassified)
    assert not code_path.exists()

    def allow_listed() -> None:
        raise ValueError("PREVIEW_RECEIPT_SHORT_WRITE")

    with pytest.raises(ValueError, match="^PREVIEW_RECEIPT_SHORT_WRITE$"):
        bootstrap._call_preview_child(config, allow_listed)
    assert code_path.read_bytes() == b"PREVIEW_RECEIPT_SHORT_WRITE"
    bootstrap._validate_required_preview_root(config)


def test_raised_preview_literals_match_public_codes(bootstrap: ModuleType) -> None:
    """Drift guard: raised PREVIEW_* literals and the public allow-list are one set."""
    tree = ast.parse((REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        if not isinstance(func, ast.Name) or func.id not in {"ValueError", "OSError", "TypeError"}:
            continue
        if len(node.exc.args) != 1 or not isinstance(node.exc.args[0], ast.Constant):
            continue
        value = node.exc.args[0].value
        if isinstance(value, str) and re.fullmatch(r"PREVIEW_[A-Z_]+", value):
            found.add(value)
    assert found == set(bootstrap._PREVIEW_PUBLIC_CODES)


@pytest.mark.parametrize("flag", range(7))
def test_every_app_role_flag_rejected_before_write(bootstrap: ModuleType, flag: int) -> None:
    # Catalog order: LOGIN, SUPERUSER, CREATEDB, CREATEROLE, INHERIT, BYPASSRLS, REPLICATION.
    row = [True, False, False, False, False, False, False]
    row[flag] = not row[flag]
    with pytest.raises(ValueError):
        bootstrap.validate_required_app_posture(row, 0)


def test_app_role_membership_rejected_and_missing_role_is_explicit(bootstrap: ModuleType) -> None:
    with pytest.raises(ValueError):
        bootstrap.validate_required_app_posture([True, False, False, False, False, False, False], 1)
    assert bootstrap.validate_required_app_posture(None, 0) is False
    assert (
        bootstrap.validate_required_app_posture([True, False, False, False, False, False, False], 0)
        is True
    )


@pytest.mark.parametrize("boundary", ["root", "receipt", "short-export", "failed-export"])
def test_no_clobber_or_partial_publication_never_reports_success(
    bootstrap: ModuleType, ci_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    config = bootstrap.required_preview_config("check", ci_env)
    before = Path(ci_env["GITHUB_ENV"]).read_bytes()
    if boundary == "root":
        config.root.mkdir()
        sentinel = config.root / "foreign"
        sentinel.write_bytes(b"preserve foreign bytes")
        with pytest.raises(FileExistsError):
            bootstrap.create_required_preview_root(config)
        assert sentinel.read_bytes() == b"preserve foreign bytes"
    else:
        config.root.mkdir(mode=0o700)
        receipt = bootstrap.required_preview_receipt(config, 123)
        path, digest = bootstrap.write_required_preview_receipt(config, receipt)
        if boundary == "receipt":
            with pytest.raises(FileExistsError):
                bootstrap.write_required_preview_receipt(config, receipt)
        elif boundary == "short-export":
            real_write = os.write

            def short_write(fd: int, raw: bytes) -> int:
                return real_write(fd, raw[:7])

            monkeypatch.setattr(bootstrap.os, "write", short_write)
            with pytest.raises(OSError):
                bootstrap.export_required_preview(config, path, digest)
            assert Path(ci_env["GITHUB_ENV"]).read_bytes() == before + b"IDIS_TE"
        else:

            def failed_write(fd: int, raw: bytes) -> int:
                raise OSError("synthetic write failure, must not be printed")

            monkeypatch.setattr(bootstrap.os, "write", failed_write)
            with pytest.raises(OSError):
                bootstrap.export_required_preview(config, path, digest)
    if boundary != "short-export":
        assert Path(ci_env["GITHUB_ENV"]).read_bytes() == before


def test_workflow_uses_independent_required_c3_step_and_no_original_short_circuit() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    by_id = {step.get("id"): step for step in steps}
    assert "c3-setup" in by_id, "exclusive C3 setup is not wired"
    assert "c3-tests" in by_id, "required independent C3 step is not wired"
    second = by_id["c3-tests"]
    # Freeze the complete Actions expression; substring checks permit OR bypasses.
    assert second["if"] == (
        "${{ !cancelled() && steps.pg-install.outcome == 'success' "
        "&& steps.pg-ceiling.outcome == 'success' && steps.pg-roles.outcome == 'success' "
        "&& steps.pg-bootstrap.outcome == 'success' && steps.pg-verify.outcome == 'success' "
        "&& steps.c3-setup.outcome == 'success' }}"
    )
    for prerequisite in (
        "pg-install",
        "pg-ceiling",
        "pg-roles",
        "pg-bootstrap",
        "pg-verify",
        "c3-setup",
    ):
        assert f"steps.{prerequisite}.outcome == 'success'" in second["if"]
    assert "pg-tests" not in second["if"]
    assert second.get("continue-on-error", False) is False
    assert steps.index(by_id["c3-setup"]) < steps.index(by_id["pg-tests"]) < steps.index(second)
    original = by_id["pg-tests"]["run"]
    line = next(line for line in original.splitlines() if line.startswith("pytest "))
    assert hashlib.sha256(("          " + line).encode()).hexdigest() == (
        "e0d37ffb23bb12dca373364834822afef707322533c4bb233491c064b96ee9ce"
    )
    assert all(name not in original for name in C3_FILES)
    assert "--run-required-preview-postgres" in second["run"]


@pytest.mark.parametrize("bypass", ["always()", "failure()"])
def test_required_c3_gate_rejects_appended_success_bypass(
    monkeypatch: pytest.MonkeyPatch, bypass: str
) -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    second = next(step for step in steps if step.get("id") == "c3-tests")
    original = second["if"]
    assert original.endswith(" }}")
    second["if"] = original[:-3] + f" || {bypass}" + " }}"

    def mutated_workflow(_source: object) -> object:
        return workflow

    monkeypatch.setattr(yaml, "safe_load", mutated_workflow)
    with pytest.raises(AssertionError):
        test_workflow_uses_independent_required_c3_step_and_no_original_short_circuit()


@pytest.mark.skipif(sys.platform == "win32", reason="Actual hosted Linux bash semantics")
@pytest.mark.parametrize("explicit_bash", [False, True])
@pytest.mark.parametrize(("first_exit", "second_exit"), [(1, 0), (0, 1), (1, 1), (0, 0)])
def test_real_shell_independent_steps_preserve_both_exits(
    tmp_path: Path, explicit_bash: bool, first_exit: int, second_exit: int
) -> None:
    first = tmp_path / "first.sh"
    marker = tmp_path / "second-executed"
    first.write_text(f"(exit {first_exit}) | tee /dev/null\nexit ${{PIPESTATUS[0]}}\n")
    args = ["bash", "--noprofile", "--norc", "-eo", "pipefail"] if explicit_bash else ["bash", "-e"]
    one = subprocess.run([*args, str(first)], check=False)
    two = subprocess.run([*args, "-c", f"touch '{marker}'; exit {second_exit}"], check=False)
    assert (one.returncode, two.returncode) == (first_exit, second_exit)
    assert marker.is_file()
    assert bool(one.returncode or two.returncode) == bool(first_exit or second_exit)


@pytest.mark.skipif(sys.platform == "win32", reason="Actual hosted Linux bash/tee semantics")
def test_c3_step_real_shell_does_not_hide_tee_failure(tmp_path: Path) -> None:
    steps = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"][
        "postgres-integration"
    ]["steps"]
    second = next(s for s in steps if s.get("id") == "c3-tests")
    script = tmp_path / "tee.sh"
    script.write_text("(exit 0) | (exit 7)\nexit ${PIPESTATUS[0]}\n")
    args = (
        ["bash", "--noprofile", "--norc", "-eo", "pipefail"]
        if second.get("shell") == "bash"
        else ["bash", "-e"]
    )
    assert subprocess.run([*args, str(script)], check=False).returncode == 7


_REQUIRED_JOBS: tuple[str, ...] = (
    "ui-check",
    "check",
    "postgres-integration",
    "evaluation-harness",
    "container-build",
    "k8s-validate",
    "terraform-validate",
    "backup-restore-drill",
)
_RELEASE_GATE_IF = "${{ always() }}"
_TERRAFORM_INIT_RUN = "cd deploy/terraform\nterraform init -backend=false\n"
_TERRAFORM_VALIDATE_RUN = "cd deploy/terraform\nterraform validate\n"
_VALIDATE_SUMMARY_LINE = (
    "echo \"- **Validate:** ${{ steps.validate.outcome == 'success' "
    "&& '[OK] Passed' || '[FAIL] Failed' }}\" >> $GITHUB_STEP_SUMMARY"
)


def _required_job_gate_run() -> str:
    """Exact first step of release-gate: one explicit success comparison per job."""
    lines = ["set -euo pipefail", "failed=0"]
    for job in _REQUIRED_JOBS:
        lines.extend(
            (
                f'if [ "${{{{ needs.{job}.result }}}}" != "success" ]; then',
                f'  echo "{job}"',
                "  failed=1",
                "fi",
            )
        )
    lines.extend(('if [ "$failed" -ne 0 ]; then', "  exit 1", "fi"))
    return "\n".join(lines) + "\n"


def _release_summary_run() -> str:
    """Summary text derived from job and manifest results, never an unconditional green."""
    conjunction = " && ".join(f"needs.{job}.result == 'success'" for job in _REQUIRED_JOBS)
    names = ", ".join(_REQUIRED_JOBS)
    lines = [
        'echo "## Release Promotion Gate" >> $GITHUB_STEP_SUMMARY',
        'echo "" >> $GITHUB_STEP_SUMMARY',
        (
            'echo "- **All CI gates:** ${{ '
            + conjunction
            + " && '[OK] green' || '[FAIL] required job not success' }} ("
            + names
            + ')" >> $GITHUB_STEP_SUMMARY'
        ),
        (
            "echo \"- **Recovery evidence:** ${{ needs.backup-restore-drill.result == 'success' "
            "&& '[OK] backup/restore drill green (dedicated job)' "
            "|| '[FAIL] backup/restore drill not success' }}\" >> $GITHUB_STEP_SUMMARY"
        ),
        (
            "echo \"- **Manifest completeness:** ${{ steps.release-manifest.outcome == 'success' "
            "&& '[OK] all checksum sections real' || '[FAIL] manifest not success' }}\" "
            ">> $GITHUB_STEP_SUMMARY"
        ),
    ]
    return "\n".join(lines) + "\n"


def _zero_skip_run(xml_name: str) -> str:
    """Read one already-produced JUnit file and fail on any skip."""
    return (
        "python3 - <<'PY'\n"
        "import xml.etree.ElementTree as ET\n"
        f'root = ET.parse("{xml_name}").getroot()\n'
        'suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))\n'
        'skipped = sum(int(suite.get("skipped", 0)) for suite in suites)\n'
        "if skipped > 0:\n"
        '    raise SystemExit("REQUIRED_PYTEST_SKIPPED")\n'
        "PY\n"
    )


def _assert_closed_github_expressions(text: str) -> None:
    assert text.count("${{") == text.count("}}")
    for body in re.findall(r"\$\{\{(.*?)\}\}", text, flags=re.DOTALL):
        assert body.strip()
        assert "${{" not in body


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _assert_preview_main_child_boundary(source: str) -> None:
    tree = ast.parse(source)
    mains = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and "__main__" in _string_constants(node.test)
    ]
    assert len(mains) == 1
    found: set[str] = set()
    expected = {"--required-preview-worker", "--required-preview-revalidate"}
    for stmt in mains[0].body:
        if not isinstance(stmt, ast.If):
            continue
        flags = _string_constants(stmt.test) & expected
        if len(flags) != 1:
            continue
        calls = [
            child
            for node in stmt.body
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_call_preview_child"
        ]
        assert len(calls) == 1
        call = calls[0]
        assert len(call.args) == 2
        assert isinstance(call.args[0], ast.Name) and call.args[0].id == "_config"
        found.update(flags)
    assert found == expected
    _assert_preview_work_stays_inside_child_bodies(mains[0])


_PREVIEW_BOUNDARY_WORK = frozenset(
    {
        "_required_preview_worker",
        "_read_required_preview_receipt",
        "_prepare_required_roles",
        "_verify_required_database",
    }
)


def _loaded_boundary_work(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id in _PREVIEW_BOUNDARY_WORK
    }


def _assert_preview_work_stays_inside_child_bodies(main: ast.If) -> None:
    """JC3-05: the four workers are only called from bodies passed to the child."""
    expected = {"--required-preview-worker", "--required-preview-revalidate"}
    inside: set[str] = set()
    for stmt in main.body:
        if not isinstance(stmt, ast.If):
            assert not _loaded_boundary_work(stmt)
            continue
        if len(_string_constants(stmt.test) & expected) != 1:
            assert not _loaded_boundary_work(stmt)
            continue
        functions = {node.name: node for node in stmt.body if isinstance(node, ast.FunctionDef)}
        passed: list[ast.FunctionDef] = []
        for node in stmt.body:
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "_call_preview_child"
                    and len(child.args) == 2
                    and isinstance(child.args[1], ast.Name)
                ):
                    body = functions.get(child.args[1].id)
                    assert body is not None
                    passed.append(body)
        assert passed
        passed_ids = {id(body) for body in passed}
        for body in passed:
            inside.update(_loaded_boundary_work(body))
        assert not _loaded_boundary_work(stmt.test)
        for node in stmt.body:
            if isinstance(node, ast.FunctionDef) and id(node) in passed_ids:
                continue
            assert not _loaded_boundary_work(node)
    assert inside == set(_PREVIEW_BOUNDARY_WORK)


def test_release_gate_runs_always_and_fails_unless_every_required_job_succeeds() -> None:
    """JC2-01: a required failure fails release-gate; the summary is not hard-coded green."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    gate = workflow["jobs"]["release-gate"]
    assert gate["if"] == _RELEASE_GATE_IF
    assert gate["needs"] == list(_REQUIRED_JOBS)
    assert "continue-on-error" not in gate
    step = gate["steps"][0]
    assert step["name"] == "Fail unless every required job succeeded"
    assert set(step) == {"name", "run"}
    assert step["run"] == _required_job_gate_run()
    summary = next(item for item in gate["steps"] if item["name"] == "Write release gate summary")
    assert summary["run"] == _release_summary_run()
    assert 'echo "- **All CI gates:** [OK] green' not in summary["run"]
    manifest = next(
        item
        for item in gate["steps"]
        if item.get("name") == "Generate and completeness-check release manifest"
    )
    assert manifest["id"] == "release-manifest"
    assert "--require-complete" in manifest["run"]
    _assert_closed_github_expressions(gate["if"])
    _assert_closed_github_expressions(step["run"])
    _assert_closed_github_expressions(summary["run"])


def _mutate_release_gate(monkeypatch: pytest.MonkeyPatch, run: str) -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    workflow["jobs"]["release-gate"]["steps"][0]["run"] = run

    def mutated(_source: object) -> object:
        return workflow

    monkeypatch.setattr(yaml, "safe_load", mutated)


def _drop_required_job(run: str, job: str) -> str:
    kept: list[str] = []
    for line in run.splitlines(keepends=True):
        if f"needs.{job}.result" in line or line.strip() == f'echo "{job}"':
            continue
        kept.append(line)
    return "".join(kept)


@pytest.mark.parametrize("job", _REQUIRED_JOBS)
def test_release_gate_pin_rejects_dropped_job_name(
    monkeypatch: pytest.MonkeyPatch, job: str
) -> None:
    """Dropping one explicit job leaves the success comparison elsewhere, and still fails."""
    dropped = _drop_required_job(_required_job_gate_run(), job)
    assert f"needs.{job}.result" not in dropped
    assert '!= "success"' in dropped
    _mutate_release_gate(monkeypatch, dropped)
    with pytest.raises(AssertionError):
        test_release_gate_runs_always_and_fails_unless_every_required_job_succeeds()


def test_release_gate_pin_rejects_weakened_success_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weaker comparison still names every job, so only the exact run text catches it."""
    weakened = _required_job_gate_run().replace('!= "success"', '!= "failure"')
    assert all(job in weakened for job in _REQUIRED_JOBS)
    assert '!= "success"' not in weakened
    _mutate_release_gate(monkeypatch, weakened)
    with pytest.raises(AssertionError):
        test_release_gate_runs_always_and_fails_unless_every_required_job_succeeds()


def test_required_postgres_invocations_have_exact_zero_skip_steps() -> None:
    """JC2-06: both required PostgreSQL invocations fail the job when JUnit reports a skip."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    by_id = {step.get("id"): step for step in steps}
    pairs = (
        ("pg-tests", "pg-zero-skip", "postgres_integration_pytest.xml"),
        ("c3-tests", "c3-zero-skip", "c3_postgres_integration_pytest.xml"),
    )
    for previous, current, xml_name in pairs:
        assert steps.index(by_id[current]) == steps.index(by_id[previous]) + 1
        step = by_id[current]
        assert "continue-on-error" not in step
        assert step["run"] == _zero_skip_run(xml_name)
        if current == "c3-zero-skip":
            assert set(step) == {"name", "id", "if", "run"}
            assert step["if"] == "${{ !cancelled() && steps.c3-tests.outcome == 'success' }}"
        else:
            assert set(step) == {"name", "id", "run"}
            assert "if" not in step
    original = by_id["pg-tests"]["run"]
    line = next(item for item in original.splitlines() if item.startswith("pytest "))
    assert hashlib.sha256(("          " + line).encode()).hexdigest() == (
        "e0d37ffb23bb12dca373364834822afef707322533c4bb233491c064b96ee9ce"
    )


@pytest.mark.parametrize(
    ("step_id", "xml_name"),
    [
        ("pg-zero-skip", "postgres_integration_pytest.xml"),
        ("c3-zero-skip", "c3_postgres_integration_pytest.xml"),
    ],
)
def test_zero_skip_pin_rejects_removed_assertion(
    monkeypatch: pytest.MonkeyPatch, step_id: str, xml_name: str
) -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    step = next(item for item in steps if item.get("id") == step_id)
    removed = step["run"].replace('    raise SystemExit("REQUIRED_PYTEST_SKIPPED")\n', "")
    assert removed != step["run"]
    assert xml_name in removed
    step["run"] = removed

    def mutated(_source: object) -> object:
        return workflow

    monkeypatch.setattr(yaml, "safe_load", mutated)
    with pytest.raises(AssertionError):
        test_required_postgres_invocations_have_exact_zero_skip_steps()


def test_terraform_validate_summary_uses_step_outcome() -> None:
    """JC2-05: fmt stays diagnostic; the Validate summary line follows the validate step."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["terraform-validate"]["steps"]
    fmt = next(step for step in steps if step.get("id") == "fmt")
    validate = next(step for step in steps if step.get("id") == "validate")
    summary = next(step for step in steps if step["name"] == "Write Terraform validation summary")
    assert fmt.get("continue-on-error") is True
    assert "terraform fmt -check -recursive -diff" in fmt["run"]
    assert "[WARN] Needs formatting" in summary["run"]
    init = next(step for step in steps if step["name"] == "Terraform Init")
    assert set(init) == {"name", "id", "run"}
    assert init["id"] == "init"
    assert init["run"] == _TERRAFORM_INIT_RUN
    assert "continue-on-error" not in init
    assert "if" not in init
    assert validate["name"] == "Terraform Validate"
    assert set(validate) == {"name", "id", "run"}
    assert validate["id"] == "validate"
    assert validate["run"] == _TERRAFORM_VALIDATE_RUN
    assert "continue-on-error" not in validate
    assert "if" not in validate
    assert "terraform validate" in validate["run"]
    assert summary["if"] == "always()"
    assert _VALIDATE_SUMMARY_LINE in summary["run"]
    assert 'echo "- **Validate:** [OK] Passed"' not in summary["run"]
    _assert_closed_github_expressions(summary["run"])


def test_terraform_validate_pin_rejects_continue_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JC3-01: continue-on-error on the required validate step fails the exact pin."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["terraform-validate"]["steps"]
    validate = next(step for step in steps if step.get("id") == "validate")
    validate["continue-on-error"] = True

    def mutated(_source: object) -> object:
        return workflow

    monkeypatch.setattr(yaml, "safe_load", mutated)
    with pytest.raises(AssertionError):
        test_terraform_validate_summary_uses_step_outcome()


def test_main_preview_branches_call_child_boundary() -> None:
    """JC2-03: both __main__ preview branches enter through _call_preview_child."""
    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    _assert_preview_main_child_boundary(source)


def test_removing_either_main_child_boundary_fails_pin() -> None:
    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    _assert_preview_main_child_boundary(source)
    for callee in ("_worker", "_revalidate"):
        weakened = source.replace(f"_call_preview_child(_config, {callee})", f"{callee}()")
        assert weakened != source
        with pytest.raises(AssertionError):
            _assert_preview_main_child_boundary(weakened)


def test_boundary_work_outside_the_nested_body_fails_pin() -> None:
    """JC3-05: a direct worker call beside an empty child body fails the pin."""
    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    _assert_preview_main_child_boundary(source)
    weakened = source.replace(
        "_call_preview_child(_config, _worker)",
        "_call_preview_child(_config, lambda: None)\n        _required_preview_worker(_config)",
        1,
    )
    assert weakened != source
    with pytest.raises(AssertionError):
        _assert_preview_main_child_boundary(weakened)


def test_keyboard_interrupt_exit_lines_include_unclassified_code_and_phase(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JC2-07: interrupt lines use the same code and phase keys as the other exit lines."""
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap, "required_preview_config", interrupt)
    assert bootstrap.required_preview_main("check") == 130
    assert capsys.readouterr().out == (
        '{"required_preview":"failed","exit":130,"cleanup_failed":false,'
        '"code":"PREVIEW_UNCLASSIFIED","phase":"bootstrap"}\n'
    )
    assert bootstrap.run_required_preview_postgres() == 130
    assert capsys.readouterr().out == (
        '{"required_c3_postgres":"interrupted","exit":130,"cleanup_failed":false,'
        '"code":"PREVIEW_UNCLASSIFIED","phase":"revalidate"}\n'
    )


def test_keyboard_interrupt_during_required_tests_reports_tests_phase(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = bootstrap.required_preview_config("postgres", ci_env)
    bootstrap.create_required_preview_root(config)
    bootstrap.write_required_preview_receipt(
        config, bootstrap.required_preview_receipt(config, 123)
    )
    monkeypatch.setattr(bootstrap.os, "environ", ci_env)

    def capture(_bound: object, *, phase: str = "bootstrap") -> tuple[int, bool]:
        if phase == "tests":
            raise KeyboardInterrupt
        assert phase == "revalidate"
        return 0, False

    monkeypatch.setattr(bootstrap, "_capture_required_preview", capture)
    assert bootstrap.run_required_preview_postgres() == 130
    assert capsys.readouterr().out == (
        '{"required_c3_postgres":"interrupted","exit":130,"cleanup_failed":false,'
        '"code":"PREVIEW_UNCLASSIFIED","phase":"tests"}\n'
    )
