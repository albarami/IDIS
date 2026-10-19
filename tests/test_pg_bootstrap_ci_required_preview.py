"""Required CI fixtures are exclusive disposable mechanics, not FULL acceptance."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
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
    assert argv[4:-4] == list(C3_FILES)
    assert argv[-4:] == [
        "--deselect",
        (
            "tests/test_cir04c3_frozen_preview_postgres.py::"
            "test_real_native_pdf_source_survives_hermetic_admission_and_restart_cut[1]"
        ),
        "-v",
        "--junitxml=c3_postgres_integration_pytest.xml",
    ]
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


def test_preview_root_accepts_dev0039_receipt_and_rejects_unknown_file(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """Allowed preview-root files plus the dev-0039 receipt validate."""
    config = bootstrap.required_preview_config("check", ci_env)
    bootstrap.create_required_preview_root(config)
    receipt_name = "dev-0039-receipt.json"
    allowed_files = (
        "bootstrap.log",
        "database-receipt.json",
        "revalidate.log",
        "bootstrap-command.sh",
        "revalidate-command.sh",
        "tests-command.sh",
        "c3-tests.log",
        _PREVIEW_FAILURE_CODE_NAME,
        receipt_name,
        "linux-poppler-receipt.json",
        "linux-poppler-revalidate-command.sh",
        "linux-poppler-revalidate.log",
        "linux-poppler-tests-command.sh",
        "linux-poppler-tests.log",
        "linux-poppler-database-tests-command.sh",
        "linux-poppler-database-tests.log",
        "linux-poppler-callers-command.sh",
        "linux-poppler-callers.log",
        "linux-poppler-callers-revalidate-command.sh",
        "linux-poppler-callers-revalidate.log",
    )
    for name in allowed_files:
        descriptor = os.open(config.root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    assert {path.name for path in config.root.iterdir()} == {"home", "tmp", *allowed_files}
    bootstrap._validate_required_preview_root(config)
    assert receipt_name == bootstrap._DEV0039_RECEIPT_NAME
    writer = inspect.getsource(bootstrap.write_dev0039_restart_receipt)
    reader = inspect.getsource(bootstrap._read_dev0039_receipt)
    validator = inspect.getsource(bootstrap._validate_required_preview_root)
    assert "config.root / _DEV0039_RECEIPT_NAME" in writer
    assert "config.root / _DEV0039_RECEIPT_NAME" in reader
    assert "_DEV0039_RECEIPT_NAME" in validator
    assert receipt_name not in writer
    assert receipt_name not in reader
    assert receipt_name not in validator
    descriptor = os.open(
        config.root / "unknown-extra.txt", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    os.close(descriptor)
    with pytest.raises(ValueError, match="^PREVIEW_ROOT_CONTENT_INVALID$"):
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
        "6f704d4a611f0d2a618a208e63179a6164121faa8315db2abefb929f24dd5a6a"
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
    expected = {
        "--required-preview-worker",
        "--required-preview-revalidate",
        "--dev0039-restart-worker",
    }
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
        "create_dev0039_restart_database",
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


def _assert_dev0039_public_argv_branches(main: ast.If) -> None:
    """The two sibling gates exist and neither creates the dev-0039 database."""
    expected = {"--run-required-dev0039-restart", "--run-required-dev0039-historical"}
    found: set[str] = set()
    for stmt in main.body:
        if not isinstance(stmt, ast.If):
            continue
        flags = _string_constants(stmt.test) & expected
        if not flags:
            continue
        assert len(flags) == 1
        loaded = {
            node.id
            for node in ast.walk(stmt)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        assert "create_dev0039_restart_database" not in loaded
        found.update(flags)
    assert found == expected


def _assert_preview_work_stays_inside_child_bodies(main: ast.If) -> None:
    """JC3-05: preview and dev-0039 workers stay inside child bodies."""
    expected = {
        "--required-preview-worker",
        "--required-preview-revalidate",
        "--dev0039-restart-worker",
    }
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
    _assert_dev0039_public_argv_branches(main)
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
        ("c3-dev0039-restart", "c3-dev0039-zero-skip", "c3_dev0039_restart_pytest.xml"),
        (
            "dev0039-historical",
            "dev0039-historical-zero-skip",
            "dev0039_historical_restart_pytest.xml",
        ),
    )
    for previous, current, xml_name in pairs:
        assert previous in by_id
        assert current in by_id
        assert steps.index(by_id[current]) == steps.index(by_id[previous]) + 1
        step = by_id[current]
        assert "continue-on-error" not in step
        assert step["run"] == _zero_skip_run(xml_name)
        if current == "pg-zero-skip":
            assert set(step) == {"name", "id", "run"}
            assert "if" not in step
        else:
            assert set(step) == {"name", "id", "if", "run"}
            assert step["if"] == (
                "${{ !cancelled() && steps." + previous + ".outcome == 'success' }}"
            )
    original = by_id["pg-tests"]["run"]
    line = next(item for item in original.splitlines() if item.startswith("pytest "))
    assert hashlib.sha256(("          " + line).encode()).hexdigest() == (
        "6f704d4a611f0d2a618a208e63179a6164121faa8315db2abefb929f24dd5a6a"
    )


@pytest.mark.parametrize(
    ("step_id", "xml_name"),
    [
        ("pg-zero-skip", "postgres_integration_pytest.xml"),
        ("c3-zero-skip", "c3_postgres_integration_pytest.xml"),
        ("c3-dev0039-zero-skip", "c3_dev0039_restart_pytest.xml"),
        ("dev0039-historical-zero-skip", "dev0039_historical_restart_pytest.xml"),
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


_RESTART_NODE_ID = (
    "tests/test_cir04c3_frozen_preview_postgres.py::"
    "test_real_native_pdf_source_survives_hermetic_admission_and_restart_cut[1]"
)
_HISTORICAL_NODE_ID = (
    "tests/slice100_task7/test_task7_restart_rehydration_red.py::"
    "test_real_postgres_all_27_restart_cuts_reclaim_and_fence_without_repair"
)
_ROLLBACK_NODE_ID = (
    "tests/slice100_task7/test_task7_restart_rehydration_red.py::"
    "test_real_postgres_step_boundary_exception_rolls_back_graph_step_and_audit"
)
_C3_TESTS_IF = (
    "${{ !cancelled() && steps.pg-install.outcome == 'success' "
    "&& steps.pg-ceiling.outcome == 'success' && steps.pg-roles.outcome == 'success' "
    "&& steps.pg-bootstrap.outcome == 'success' && steps.pg-verify.outcome == 'success' "
    "&& steps.c3-setup.outcome == 'success' }}"
)
_PG_NATIVE_INPUTS_RUN = (
    "sudo apt-get update\n"
    "sudo apt-get install -y --no-install-recommends "
    "ffmpeg poppler-utils tesseract-ocr tesseract-ocr-eng\n"
)
_PG_ARCHIVE_FENCE_RUN = (
    ".venv/bin/python -m pip --isolated install --index-url https://pypi.org/simple "
    '-c requirements/constraints-py311.txt "hatchling==1.32.4"\n'
    ".venv/bin/python -m pip show hatchling\n"
    ".venv/bin/python -m hatchling build -t sdist -t wheel "
    '-d "$RUNNER_TEMP/idis-archive-fence"\n'
    ".venv/bin/python - <<'PY'\n"
    "import os\n"
    "import tarfile\n"
    "import zipfile\n"
    "from pathlib import Path\n"
    "\n"
    'root = Path(os.environ["RUNNER_TEMP"]) / "idis-archive-fence"\n'
    "files = [path for path in root.iterdir() if path.is_file()]\n"
    'sdists = [path for path in files if path.name.endswith(".tar.gz")]\n'
    'wheels = [path for path in files if path.suffix == ".whl"]\n'
    "if len(sdists) != 1 or len(wheels) != 1:\n"
    "    raise SystemExit(1)\n"
    "if {path.name for path in files} != {sdists[0].name, wheels[0].name}:\n"
    "    raise SystemExit(1)\n"
    "\n"
    "def names(path: Path) -> list[str]:\n"
    '    if path.name.endswith(".tar.gz"):\n'
    "        with tarfile.open(path) as archive:\n"
    "            return archive.getnames()\n"
    "    with zipfile.ZipFile(path) as archive:\n"
    "        return archive.namelist()\n"
    "\n"
    "def norm(name: str) -> str:\n"
    '    return name.replace(chr(92), "/").lstrip("./")\n'
    "\n"
    "def dev_only(name: str) -> bool:\n"
    "    text = norm(name)\n"
    '    return text.startswith("scripts/dev_only/") or "/scripts/dev_only/" in "/" + text\n'
    "\n"
    "def wheel_scripts(name: str) -> bool:\n"
    "    text = norm(name)\n"
    '    return text == "scripts" or text.startswith("scripts/") or "/scripts/" in "/" + text\n'
    "\n"
    "for member in names(sdists[0]):\n"
    "    if dev_only(member):\n"
    "        raise SystemExit(1)\n"
    "for member in names(wheels[0]):\n"
    "    if dev_only(member) or wheel_scripts(member):\n"
    "        raise SystemExit(1)\n"
    "PY\n"
)
_RESTART_CHILD_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
    "IDIS_DEV0039_RECEIPT",
    "IDIS_DEV0039_RECEIPT_SHA256",
    "IDIS_DEV0039_DATABASE_URL",
    "IDIS_REQUIRE_POSTGRES",
)
_HISTORICAL_CHILD_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
    "IDIS_REQUIRE_POSTGRES",
    "IDIS_DATABASE_ADMIN_URL",
)


def _dev0039_receipt_identity() -> tuple[str, str]:
    script = REPO / "scripts" / "run_cir04_restart_reference_dev_migration.py"
    spec = importlib.util.spec_from_file_location("dev0039_driver_pins", script)
    assert spec is not None and spec.loader is not None and spec.name is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    schema = module.DEV0039_RECEIPT_SCHEMA
    revision = module.DEV0039_RECEIPT_REVISION
    assert isinstance(schema, str) and isinstance(revision, str)
    return schema, revision


def _sibling_pytest_run(flag: str, log_name: str) -> str:
    unset = (
        "env -u IDIS_DATABASE_URL -u IDIS_DATABASE_ADMIN_URL "
        "-u IDIS_DATABASE_ERASURE_URL -u IDIS_ERASURE_DATABASE_URL "
        "-u IDIS_REQUIRE_POSTGRES "
    )
    return (
        unset + f"python scripts/pg_bootstrap_ci.py {flag} 2>&1 | tee {log_name}\n"
        "exit ${PIPESTATUS[0]}\n"
    )


def _dev_env(ci_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(ci_env)
    env["IDIS_CI_PREVIEW_NONCE"] = "ab" * 8
    return env


def test_required_preview_config_rejects_dev0039_context(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="^PREVIEW_CONTEXT_INVALID$"):
        bootstrap.required_preview_config("dev0039", ci_env)
    assert not (Path(ci_env["RUNNER_TEMP"]) / "idis-dev0039-1234-2").exists()


def test_dev0039_restart_config_forces_name_service_and_never_exports(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    assert callable(getattr(bootstrap, "dev0039_restart_config", None))
    before = Path(ci_env["GITHUB_ENV"]).read_bytes()
    config = bootstrap.dev0039_restart_config(_dev_env(ci_env))
    assert config.context == "dev0039"
    assert config.github_env is None
    assert config.database == "idis_dev0039_1234_2_" + ("ab" * 8)
    assert config.root.name == "idis-dev0039-1234-2"
    assert bootstrap._PREVIEW_DATABASE.fullmatch(config.database) is None
    assert len(config.database.encode()) <= 63
    assert Path(ci_env["GITHUB_ENV"]).read_bytes() == before
    missing = dict(ci_env)
    del missing["IDIS_CI_POSTGRES_SERVICE_ID"]
    with pytest.raises(ValueError, match="^PREVIEW_INPUT_MISSING$"):
        bootstrap.dev0039_restart_config(missing)
    invalid = dict(ci_env)
    invalid["IDIS_CI_POSTGRES_SERVICE_ID"] = "abc"
    with pytest.raises(ValueError, match="^PREVIEW_SERVICE_INVALID$"):
        bootstrap.dev0039_restart_config(invalid)
    debug = dict(ci_env)
    debug["RUNNER_DEBUG"] = "1"
    with pytest.raises(ValueError, match="^PREVIEW_DEBUG_FORBIDDEN$"):
        bootstrap.dev0039_restart_config(debug)


@pytest.mark.parametrize(
    "key",
    (
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_DATABASE_ERASURE_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_REQUIRE_POSTGRES",
        *KEYS,
    ),
)
def test_dev0039_restart_config_rejects_global_and_preview_keys(
    bootstrap: ModuleType, ci_env: dict[str, str], key: str
) -> None:
    assert callable(getattr(bootstrap, "dev0039_restart_config", None))
    env = dict(ci_env)
    env[key] = "synthetic-forbidden"
    with pytest.raises(ValueError, match="^PREVIEW_GLOBAL_DATABASE_ENV_FORBIDDEN$"):
        bootstrap.dev0039_restart_config(env)


def test_dev0039_receipt_is_0039_dev_and_leaves_0038_receipt_unchanged(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    assert callable(getattr(bootstrap, "dev0039_restart_receipt", None))
    assert callable(getattr(bootstrap, "write_dev0039_restart_receipt", None))
    schema, revision = _dev0039_receipt_identity()
    preview = bootstrap.required_preview_config("check", ci_env)
    bootstrap.create_required_preview_root(preview)
    preview_path, _digest = bootstrap.write_required_preview_receipt(
        preview, bootstrap.required_preview_receipt(preview, 9)
    )
    preview_bytes = preview_path.read_bytes()
    dev = bootstrap.dev0039_restart_config(_dev_env(ci_env))
    bootstrap.create_required_preview_root(dev)
    planted = dev.root / "database-receipt.json"
    planted.write_bytes(preview_bytes)
    receipt = bootstrap.dev0039_restart_receipt(dev, 11)
    assert receipt["revision"] == "0039-dev" == revision
    assert receipt["schema_version"] == schema
    assert bootstrap._PREVIEW_DATABASE.fullmatch(str(receipt["database"])) is None
    bootstrap.write_dev0039_restart_receipt(dev, receipt)
    assert preview_path.read_bytes() == preview_bytes
    assert planted.read_bytes() == preview_bytes
    assert (dev.root / "dev-0039-receipt.json").is_file()
    preview_path.write_bytes((dev.root / "dev-0039-receipt.json").read_bytes())
    with pytest.raises(ValueError, match="^PREVIEW_(DATABASE_INVALID|RECEIPT_INVALID)$"):
        bootstrap._read_required_preview_receipt(preview)


def test_dev0039_node_id_is_defined_once_and_selected_by_the_sibling(
    bootstrap: ModuleType,
) -> None:
    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    constants = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == _RESTART_NODE_ID
    ]
    assert constants == [_RESTART_NODE_ID]
    assert bootstrap.DEV0039_RESTART_NODE_ID == _RESTART_NODE_ID
    assert source.count("DEV0039_RESTART_NODE_ID") >= 3


def _bind_capture(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake: object,
) -> None:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import ci_private_inputs as capture

    monkeypatch.setattr(capture, "capture_script", fake)
    monkeypatch.setattr(capture, "bash_path", lambda: "/usr/bin/bash")


def test_dev0039_restart_child_allow_list_is_eight_names_without_database_url(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert callable(getattr(bootstrap, "run_required_dev0039_restart", None))
    seen: dict[str, object] = {}

    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        del bash
        command = script.read_text(encoding="utf-8")
        if "--dev0039-restart-worker" in command:
            bound = bootstrap.dev0039_restart_config(environment)
            bootstrap.write_dev0039_restart_receipt(
                bound, bootstrap.dev0039_restart_receipt(bound, 11)
            )
            log.write_bytes(b"worker\n")
            return 0
        seen["environment"] = dict(environment)
        seen["argv"] = shlex.split(command.splitlines()[-1])
        log.write_bytes(b"RESTART_CHILD\n")
        return 0

    _bind_capture(bootstrap, monkeypatch, fake_capture)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_required_dev0039_restart() == 0
    assert capsys.readouterr().out == "RESTART_CHILD\n"
    environment = seen["environment"]
    assert isinstance(environment, dict)
    assert tuple(environment) == _RESTART_CHILD_KEYS
    assert "IDIS_DATABASE_URL" not in environment
    assert environment["IDIS_REQUIRE_POSTGRES"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    receipt = Path(str(environment["IDIS_DEV0039_RECEIPT"]))
    assert receipt.name == "dev-0039-receipt.json"
    assert environment["HOME"] == str(receipt.parent / "home")
    assert environment["TMPDIR"] == str(receipt.parent / "tmp")
    database = str(environment["IDIS_DEV0039_DATABASE_URL"]).rsplit("/", 1)[-1]
    assert database.startswith("idis_dev0039_")
    assert bootstrap._PREVIEW_DATABASE.fullmatch(database) is None
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert _RESTART_NODE_ID in argv
    assert "--deselect" not in argv
    assert _ROLLBACK_NODE_ID not in argv


def test_dev0039_historical_child_allow_list_uses_maintenance_postgres(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert callable(getattr(bootstrap, "run_required_dev0039_historical", None))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("historical sibling created a dev0039 database")

    monkeypatch.setattr(bootstrap, "create_dev0039_restart_database", forbidden, raising=False)
    seen: dict[str, object] = {}

    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        del bash
        seen["environment"] = dict(environment)
        seen["argv"] = shlex.split(script.read_text(encoding="utf-8").splitlines()[-1])
        log.write_bytes(b"HISTORICAL_CHILD\n")
        return 0

    _bind_capture(bootstrap, monkeypatch, fake_capture)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_required_dev0039_historical() == 0
    assert capsys.readouterr().out == "HISTORICAL_CHILD\n"
    assert not (Path(ci_env["RUNNER_TEMP"]) / "idis-dev0039-1234-2").exists()
    environment = seen["environment"]
    assert isinstance(environment, dict)
    assert set(environment) == set(_HISTORICAL_CHILD_KEYS)
    assert "IDIS_DATABASE_URL" not in environment
    assert environment["IDIS_REQUIRE_POSTGRES"] == "1"
    assert environment["IDIS_DATABASE_ADMIN_URL"].rsplit("/", 1)[-1] == "postgres"
    assert Path(str(environment["HOME"])).parent.name == "idis-dev0039-historical-1234-2"
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert _HISTORICAL_NODE_ID in argv
    assert _ROLLBACK_NODE_ID not in argv


def test_dev0039_public_json_pins_code_and_phase(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert callable(getattr(bootstrap, "run_required_dev0039_restart", None))
    assert callable(getattr(bootstrap, "run_required_dev0039_historical", None))
    missing = dict(ci_env)
    del missing["IDIS_CI_POSTGRES_SERVICE_ID"]
    monkeypatch.setattr(bootstrap.os, "environ", missing)
    assert bootstrap.run_required_dev0039_restart() == 1
    assert capsys.readouterr().out == (
        '{"required_dev0039_restart":"failed","code":"PREVIEW_INPUT_MISSING",'
        '"phase":"dev0039-restart"}\n'
    )
    assert bootstrap.run_required_dev0039_historical() == 1
    assert capsys.readouterr().out == (
        '{"required_dev0039_historical":"failed","code":"PREVIEW_INPUT_MISSING",'
        '"phase":"dev0039-historical"}\n'
    )


def test_dev0039_unclassified_failure_hides_exception_text(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(env: Mapping[str, str]) -> object:
        del env
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(bootstrap, "dev0039_restart_config", explode)
    assert bootstrap.run_required_dev0039_restart() == 1
    captured = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in captured
    assert captured == (
        '{"required_dev0039_restart":"failed","code":"PREVIEW_UNCLASSIFIED",'
        '"phase":"dev0039-restart"}\n'
    )


def test_dev0039_pytest_interrupt_uses_interrupted_line(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        del bash, environment
        command = script.read_text(encoding="utf-8")
        if "--dev0039-restart-worker" in command:
            bound = bootstrap.dev0039_restart_config(dict(bootstrap.os.environ))
            bootstrap.write_dev0039_restart_receipt(
                bound, bootstrap.dev0039_restart_receipt(bound, 11)
            )
            log.write_bytes(b"worker\n")
            return 0
        log.write_bytes(b"hidden\n")
        return 130

    _bind_capture(bootstrap, monkeypatch, fake_capture)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_required_dev0039_restart() == 130
    assert capsys.readouterr().out == (
        '{"required_dev0039_restart":"interrupted","exit":130,"cleanup_failed":false,'
        '"code":"PREVIEW_UNCLASSIFIED","phase":"tests"}\n'
    )


def test_dev0039_sibling_steps_follow_c3_zero_skip_with_exact_guards() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    by_id = {step.get("id"): step for step in steps}
    assert "c3-dev0039-restart" in by_id
    assert "dev0039-historical" in by_id
    assert steps.index(by_id["c3-tests"]) < steps.index(by_id["c3-zero-skip"])
    assert steps.index(by_id["c3-zero-skip"]) < steps.index(by_id["c3-dev0039-restart"])
    assert steps.index(by_id["c3-dev0039-restart"]) < steps.index(by_id["dev0039-historical"])
    c3 = by_id["c3-tests"]
    assert c3["if"] == _C3_TESTS_IF
    for step_id, flag, log_name in (
        ("c3-dev0039-restart", "--run-required-dev0039-restart", "c3_dev0039_restart_pytest.log"),
        (
            "dev0039-historical",
            "--run-required-dev0039-historical",
            "dev0039_historical_restart_pytest.log",
        ),
    ):
        step = by_id[step_id]
        assert set(step) == {"name", "id", "if", "env", "run"}
        assert step["if"] == c3["if"] == _C3_TESTS_IF
        assert "pg-tests" not in step["if"]
        assert set(step["env"]) == {"IDIS_CI_POSTGRES_SERVICE_ID"}
        assert step["run"] == _sibling_pytest_run(flag, log_name)
        assert "continue-on-error" not in step
        _assert_closed_github_expressions(step["if"])
        _assert_closed_github_expressions(step["run"])
    summary = next(
        step for step in steps if step.get("name") == "Write explicit pytest summary to job summary"
    )
    assert "${{ steps.c3-dev0039-restart.outcome }}" in summary["run"]
    assert "${{ steps.dev0039-historical.outcome }}" in summary["run"]
    _assert_closed_github_expressions(summary["run"])
    logs = next(step for step in steps if step.get("name") == "Upload pytest log artifact")
    xmls = next(step for step in steps if step.get("name") == "Upload pytest JUnit XML artifact")
    assert "c3_dev0039_restart_pytest.log" in logs["with"]["path"]
    assert "dev0039_historical_restart_pytest.log" in logs["with"]["path"]
    assert "c3_dev0039_restart_pytest.xml" in xmls["with"]["path"]
    assert "dev0039_historical_restart_pytest.xml" in xmls["with"]["path"]


def test_pg_native_inputs_has_no_if_and_fence_follows_dev0039() -> None:
    """Native inputs stay before the ceiling; the archive fence runs after dev-0039."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    by_id = {step.get("id"): step for step in steps}
    assert "pg-native-inputs" in by_id
    assert "pg-archive-fence" in by_id
    native = by_id["pg-native-inputs"]
    fence = by_id["pg-archive-fence"]
    check_steps = workflow["jobs"]["check"]["steps"]
    ocr = next(
        step
        for step in check_steps
        if step.get("name") == "Install OCR and media system dependencies"
    )
    assert steps.index(native) == steps.index(by_id["pg-install"]) + 1
    assert steps.index(by_id["pg-ceiling"]) == steps.index(native) + 1
    assert "if" not in native
    assert set(native) == {"name", "id", "run"}
    assert native["run"] == ocr["run"] == _PG_NATIVE_INPUTS_RUN
    assert "continue-on-error" not in native
    historical_zero = by_id["dev0039-historical-zero-skip"]
    uploads = [
        step
        for step in steps
        if step.get("if") == "always()"
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/upload-artifact@")
    ]
    assert uploads
    zero_index = steps.index(historical_zero)
    fence_index = steps.index(fence)
    upload_index = steps.index(uploads[0])
    fence_if = fence.get("if")
    expected_if = "${{ !cancelled() && steps.pg-install.outcome == 'success' }}"
    assert (
        steps[zero_index + 1] is fence
        and steps[fence_index + 1] is uploads[0]
        and fence_if == expected_if
    ), (zero_index, fence_index, upload_index, fence_if)
    assert fence["name"] == "Fence dev-only scripts out of the sdist and wheel"
    assert set(fence) == {"name", "id", "if", "run"}
    assert "env" not in fence
    assert fence["run"] == _PG_ARCHIVE_FENCE_RUN
    assert "continue-on-error" not in fence
    assert "hatchling==1.32.4" in fence["run"]
    assert "build" not in fence["run"].split("hatchling==1.32.4", 1)[0]


_PINNED_BOOKWORM = (
    "python:3.11-slim-bookworm@"
    "sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b"
)
_POPPLER_CACHE_KEY = (
    "poppler-linux-prefix-v1|Linux|X64|"
    "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48|"
    "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
)
_POPPLER_CACHE_PATH = "poppler-linux-prefix"
_POPPLER_SAVE_IF = (
    _C3_TESTS_IF[: -len(" }}")]
    + " && steps.pg-governed-poppler.outcome == 'success' "
    + "&& steps.pg-poppler-cache.outputs.cache-hit != 'true' "
    + "&& (github.event_name != 'pull_request' "
    + "|| github.event.pull_request.head.repo.full_name == github.repository) }}"
)
_C3_ENV_UNSET = (
    "env -u IDIS_DATABASE_URL -u IDIS_DATABASE_ADMIN_URL "
    "-u IDIS_DATABASE_ERASURE_URL -u IDIS_ERASURE_DATABASE_URL "
    "-u IDIS_REQUIRE_POSTGRES python scripts/pg_bootstrap_ci.py "
    "--run-required-preview-postgres"
)
_C3_FORWARDED_ENV = (
    "PG_ADMIN_USER",
    "PG_ADMIN_PASSWORD",
    "IDIS_PG_APP_USER",
    "IDIS_PG_APP_PASSWORD",
    "IDIS_PG_DB_NAME",
    "IDIS_PG_HOST",
    "IDIS_PG_PORT",
    "IDIS_CI_POSTGRES_SERVICE_ID",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_WORKSPACE",
    "RUNNER_TEMP",
)
_POPPLER_PUBLIC_ENV = {
    "POPPLER_LINUX_SOURCE_SHA256": (
        "c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48"
    ),
    "POPPLER_LINUX_SOURCE_SIZE_BYTES": "1992456",
    "POPPLER_LINUX_RECIPE_SHA256": (
        "e0ff366be570c7e30c5d354a51af83f76b6c2d05ee2bce73a43e68db8f9ae7fd"
    ),
    "POPPLER_LINUX_PREFIX": "/opt/idis-native/poppler-25.12.0",
}


def _postgres_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    assert isinstance(steps, list)
    return steps


def test_pg_governed_poppler_is_between_zero_skip_and_c3_tests() -> None:
    """Option (i): build and admit the prefix after pg-zero-skip, then c3-tests."""

    steps = _postgres_steps()
    by_id = {step.get("id"): step for step in steps}
    order = (
        "pg-zero-skip",
        "pg-poppler-cache",
        "pg-governed-poppler",
        "pg-poppler-cache-save",
        "c3-tests",
        "c3-zero-skip",
    )
    indexes = [steps.index(by_id[step_id]) for step_id in order]
    assert indexes == list(range(indexes[0], indexes[0] + len(order)))
    governed = by_id["pg-governed-poppler"]
    c3 = by_id["c3-tests"]
    restore = by_id["pg-poppler-cache"]
    save = by_id["pg-poppler-cache-save"]
    assert governed["if"] == c3["if"] == _C3_TESTS_IF
    assert restore["if"] == _C3_TESTS_IF
    assert save["if"] == _POPPLER_SAVE_IF
    for step in (restore, governed, save, c3):
        assert "continue-on-error" not in step
        assert "|| true" not in str(step.get("run") or "")
    container = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    container_steps = container["jobs"]["container-build"]["steps"]
    model_restore = next(step for step in container_steps if step.get("id") == "model-cache")
    model_save = next(step for step in container_steps if step.get("id") == "model-cache-save")
    assert restore["uses"] == model_restore["uses"] == "actions/cache/restore@v4"
    assert save["uses"] == model_save["uses"] == "actions/cache/save@v4"
    assert restore["with"]["path"] == save["with"]["path"] == _POPPLER_CACHE_PATH
    assert restore["with"]["key"] == save["with"]["key"] == _POPPLER_CACHE_KEY
    assert "restore-keys" not in restore["with"]
    assert "restore-keys" not in save["with"]
    assert set(restore["with"]) == {"path", "key"}
    assert set(save["with"]) == {"path", "key"}


def test_c3_tests_run_inside_the_pinned_bookworm_with_the_governed_prefix() -> None:
    """The c3 run string is option (i); the zero-skip JUnit path stays."""

    steps = _postgres_steps()
    by_id = {step.get("id"): step for step in steps}
    governed = str(by_id["pg-governed-poppler"]["run"])
    c3_step = by_id["c3-tests"]
    c3 = str(c3_step["run"])
    assert _PINNED_BOOKWORM in governed
    assert _PINNED_BOOKWORM in c3
    assert "docker run --rm" in governed
    assert "docker run --rm --network host" in c3
    assert "--network host" not in governed
    assert "${GITHUB_WORKSPACE}:${GITHUB_WORKSPACE}" in governed
    assert "${GITHUB_WORKSPACE}:${GITHUB_WORKSPACE}" in c3
    assert "/opt/idis-native/poppler-25.12.0:ro" in governed
    assert "/opt/idis-native/poppler-25.12.0:ro" in c3
    assert "--prefix /opt/idis-native/poppler-25.12.0" in governed
    assert "--recipe /opt/idis-poppler-build/recipe-v2.json" in governed
    assert "--source /opt/idis-poppler-build/poppler-25.12.0.tar.xz" in governed
    assert (
        "--manifest-out /opt/idis-native/poppler-25.12.0/share/idis/"
        "poppler-linux-prefix-identity-v1.json"
    ) in governed
    assert "admit_linux_poppler_prefix" in governed
    assert "PYTHONPATH" in governed
    assert "POPPLER_LINUX_MANIFEST_SHA256=" in governed
    assert "GITHUB_STEP_SUMMARY" in governed
    assert "POPPLER_LINUX_RECIPE_SHA256=" in governed
    assert "POPPLER_LINUX_SOURCE_SHA256=" in governed
    assert "POPPLER_LINUX_SNAPSHOT_ROOT_" in governed
    assert "POPPLER_CACHE_HIT" in governed
    assert governed.count("https://poppler.freedesktop.org/poppler-25.12.0.tar.xz") == 1
    assert "poppler-utils" not in governed
    assert "poppler-utils" not in c3
    assert _C3_ENV_UNSET in c3
    assert "tee c3_postgres_integration_pytest.log" in c3
    assert "exit ${PIPESTATUS[0]}" in c3
    assert "--junitxml" not in c3
    assert by_id["c3-zero-skip"]["run"] == _zero_skip_run("c3_postgres_integration_pytest.xml")
    for name in _C3_FORWARDED_ENV:
        assert f"-e {name}" in c3
    for absent in ("-e CI", "-e GITHUB_ACTIONS", "-e GITHUB_JOB", "-e GITHUB_ENV"):
        assert absent not in c3
    assert "pip==26.2.1" in c3
    assert "--isolated" in c3
    assert "--index-url https://pypi.org/simple" in c3
    assert "-c requirements/constraints-py311.txt" in c3
    assert "--build-constraint requirements/constraints-py311.txt" in c3
    assert '-e ".[dev]"' in c3
    assert "python -m venv" in c3
    assert "${RUNNER_TEMP}/idis-c3-venv:${GITHUB_WORKSPACE}/.venv" in c3
    assert "tesseract-ocr" in c3
    assert "tesseract-ocr-eng" in c3
    assert "ffmpeg" not in c3
    assert "libpq5" not in c3
    assert "--install-runtime-closure" in governed
    assert "--install-runtime-closure" in c3
    env = c3_step["env"]
    assert set(env) == {"IDIS_CI_POSTGRES_SERVICE_ID"}
    public = by_id["pg-governed-poppler"]["env"]
    for name, value in _POPPLER_PUBLIC_ENV.items():
        assert public[name] == value
    assert public["POPPLER_CACHE_HIT"] == "${{ steps.pg-poppler-cache.outputs.cache-hit }}"


_C3_HOST_DROP = 'setpriv --reuid="${HOST_UID}" --regid="${HOST_GID}" --clear-groups --'
_C3_SETPRIV_MISSING = "setpriv is required in the pinned image and was not found"
_POPPLER_PINNED_SOURCE_LINE = (
    'print("POPPLER_LINUX_SOURCE_SHA256_PINNED=" + os.environ["POPPLER_LINUX_SOURCE_SHA256"])'
)
_POPPLER_MEASURED_SOURCE_LINE = (
    'print("POPPLER_LINUX_SOURCE_SHA256=" + hashlib.sha256(destination.read_bytes()).hexdigest())'
)
_POPPLER_UNMEASURED_SOURCE_LINE = (
    'print("POPPLER_LINUX_SOURCE_SHA256=" + os.environ["POPPLER_LINUX_SOURCE_SHA256"])'
)


def test_c3_tests_drops_to_host_uid_after_closure_before_preview() -> None:
    """The c3 run string drops to the host uid/gid after closure and before preview."""

    c3 = str(next(step for step in _postgres_steps() if step.get("id") == "c3-tests")["run"])
    closure = "--assert-runtime-closure"
    preview = "python scripts/pg_bootstrap_ci.py --run-required-preview-postgres"
    assert closure in c3
    assert _C3_HOST_DROP in c3, (
        "c3-tests does not drop to the host uid/gid with setpriv --reuid/--regid/--clear-groups"
    )
    assert 'HOST_UID="$(id -u)"' in c3
    assert 'HOST_GID="$(id -g)"' in c3
    assert "-e HOST_UID" in c3
    assert "-e HOST_GID" in c3
    assert _C3_SETPRIV_MISSING in c3
    closure_at = c3.index(closure)
    drop_at = c3.index(_C3_HOST_DROP)
    preview_at = c3.index(preview)
    assert closure_at < drop_at < preview_at, (
        "setpriv drop must follow the closure assertion and precede pg_bootstrap_ci.py"
    )
    before_closure = c3[:closure_at]
    assert "setpriv" not in before_closure
    assert "--reuid" not in before_closure
    assert "--regid" not in before_closure
    assert "--clear-groups" not in before_closure
    home = 'HOME="${RUNNER_TEMP}/idis-c3-home"'
    assert home in c3
    assert drop_at < c3.index(home) < c3.index("python -m venv") < preview_at
    assert "poppler-utils" not in c3


_C3_POPPLER_PATH_ABSENT = (
    "if command -v pdfinfo >/dev/null 2>&1 || command -v pdftoppm >/dev/null 2>&1; "
    'then echo "Poppler binaries must not be on PATH in the c3 container" >&2; exit 1; fi'
)


def test_c3_run_does_not_link_poppler_onto_path() -> None:
    """The c3 run string does not link the governed prefix onto PATH."""

    c3 = str(next(step for step in _postgres_steps() if step.get("id") == "c3-tests")["run"])
    assert "ln -s /opt/idis-native/poppler-25.12.0/" not in c3, (
        "c3-tests must not ln -s the governed prefix onto PATH"
    )
    assert "/usr/bin/pdfinfo" not in c3
    assert "/usr/bin/pdftoppm" not in c3


def test_c3_run_asserts_poppler_is_absent_from_path() -> None:
    """The c3 run string asserts Poppler is absent from PATH after closure and before the drop."""

    c3 = str(next(step for step in _postgres_steps() if step.get("id") == "c3-tests")["run"])
    closure_at = c3.index("--assert-runtime-closure")
    drop_at = c3.index(_C3_HOST_DROP)
    assert _C3_POPPLER_PATH_ABSENT in c3
    absent_at = c3.index(_C3_POPPLER_PATH_ABSENT)
    assert closure_at < absent_at < drop_at, (
        "the empty PATH assertion must follow closure and precede the setpriv drop"
    )


def test_canonical_bitmap_passes_governed_binary_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signer passes poppler_path from a governed binary directory."""

    from tests._cir04c3_exclusion_signer import _canonical_bitmap

    class _InlineBinding:
        binary_dir = Path("/opt/idis-native/poppler-25.12.0/bin")

    binding = _InlineBinding()

    def resolve() -> _InlineBinding:
        return binding

    recorded: dict[str, object] = {}

    def _record(*_args: object, **kwargs: object) -> list[object]:
        recorded["kwargs"] = kwargs
        raise RuntimeError("CANONICAL_BITMAP_CALL_RECORDED")

    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        resolve,
    )
    monkeypatch.setattr("pdf2image.convert_from_bytes", _record)
    with pytest.raises(RuntimeError, match="CANONICAL_BITMAP_CALL_RECORDED"):
        _canonical_bitmap(b"%PDF-1.4\n")
    kwargs = recorded["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("poppler_path") == str(binding.binary_dir)


def test_pg_governed_poppler_labels_pin_and_compares_manifest_lines() -> None:
    """Cache-hit source line is the pin; a miss fails unless the manifest lines match."""

    governed = str(
        next(step for step in _postgres_steps() if step.get("id") == "pg-governed-poppler")["run"]
    )
    assert _POPPLER_PINNED_SOURCE_LINE in governed, (
        "cache-hit source digest must be labeled POPPLER_LINUX_SOURCE_SHA256_PINNED="
    )
    assert _POPPLER_UNMEASURED_SOURCE_LINE not in governed, (
        "the pinned constant must not be printed as POPPLER_LINUX_SOURCE_SHA256="
    )
    assert _POPPLER_MEASURED_SOURCE_LINE in governed, (
        "measured POPPLER_LINUX_SOURCE_SHA256= must be printed where the tarball is hashed"
    )
    build_capture = (
        "build_manifest_line=\"$(grep '^POPPLER_LINUX_MANIFEST_SHA256=' "
        '"${RUNNER_TEMP}/pg-governed-poppler-build.log")"'
    )
    admission_capture = (
        "admission_manifest_line=\"$(grep '^POPPLER_LINUX_MANIFEST_SHA256=' "
        '"${RUNNER_TEMP}/pg-governed-poppler-admit.log")"'
    )
    assert build_capture in governed, "the build manifest line must be captured on a miss"
    assert admission_capture in governed, "the admission manifest line must be captured"
    assert "printf '%s\\n' \"${build_manifest_line}\"" in governed
    assert "printf '%s\\n' \"${admission_manifest_line}\"" in governed
    assert '[[ "${build_manifest_line}" != "${admission_manifest_line}" ]]' in governed, (
        "the step must fail unless the build manifest line equals the admission line"
    )
    assert (
        "exit 1"
        in governed.split('[[ "${build_manifest_line}" != "${admission_manifest_line}" ]]', 1)[1]
    )


def test_pg_governed_poppler_workspace_mounts_are_read_only() -> None:
    """Build and admit mount the workspace read-only; the build prefix stays writable."""

    governed = str(
        next(step for step in _postgres_steps() if step.get("id") == "pg-governed-poppler")["run"]
    )
    workspace_ro = '-v "${GITHUB_WORKSPACE}:${GITHUB_WORKSPACE}:ro"'
    assert governed.count(workspace_ro) == 2, (
        "build and admit containers must mount the workspace read-only"
    )
    assert '-v "${prefix_host}:/opt/idis-native/poppler-25.12.0" \\' in governed
    assert '-v "${prefix_host}:/opt/idis-native/poppler-25.12.0:ro"' in governed
    assert '-v "${RUNNER_TEMP}:${RUNNER_TEMP}"' in governed


_LINUX_POPPLER_IF = "${{ !cancelled() && steps.pg-governed-poppler.outcome == 'success' }}"
_LINUX_POPPLER_MANIFEST_LINE = "POPPLER_LINUX_MANIFEST_SHA256="


def test_postgres_integration_trigger_is_not_narrower_than_check() -> None:
    """postgres-integration stays on the same push and pull_request branches as check."""

    text = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "\non:\n  push:\n    branches: [main]\n  pull_request:\n    branches: [main]\n" in text
    workflow = yaml.safe_load(text)
    assert "if" not in workflow["jobs"]["check"]
    assert "if" not in workflow["jobs"]["postgres-integration"]
    assert "c3-linux-poppler" not in str(workflow["jobs"]["check"])


def test_linux_poppler_closed_list_is_a_second_invocation() -> None:
    """The closed list is its own invocation after the prefix, with its own count step."""

    steps = _postgres_steps()
    by_id = {step.get("id"): step for step in steps}
    run_step = by_id["c3-linux-poppler"]
    count_step = by_id["c3-linux-poppler-count"]
    assert isinstance(run_step, dict)
    assert isinstance(count_step, dict)
    assert run_step["if"] == count_step["if"] == _LINUX_POPPLER_IF
    assert "steps.c3-tests.outcome" not in str(run_step["if"])
    assert "steps.c3-linux-poppler.outcome" not in str(count_step["if"])
    assert "continue-on-error" not in run_step
    assert "continue-on-error" not in count_step
    script = str(run_step["run"])
    assert "|| true" not in script
    assert "|| true" not in str(count_step["run"])
    assert "python scripts/pg_bootstrap_ci.py --run-linux-poppler-closed-list" in script
    assert _LINUX_POPPLER_MANIFEST_LINE in script
    assert _C3_HOST_DROP in script
    assert _PINNED_BOOKWORM in script
    assert "python -m venv" not in script
    assert "verify_dependency_resolution.py" not in script
    assert "setuptools wheel" not in script
    assert '"pip==26.2.1"' not in script
    assert "--build-constraint" not in script
    assert script.index(_LINUX_POPPLER_MANIFEST_LINE) < script.index("--install-runtime-closure")
    assert script.index("--assert-runtime-closure") < script.index(_C3_POPPLER_PATH_ABSENT)
    assert script.index(_C3_POPPLER_PATH_ABSENT) < script.index(_C3_HOST_DROP)
    ids = [step.get("id") for step in steps]
    assert ids.index("c3-zero-skip") < ids.index("c3-linux-poppler")
    assert ids.index("c3-linux-poppler") < ids.index("c3-linux-poppler-count")
    assert ids.index("c3-linux-poppler-count") < ids.index("c3-dev0039-restart")
    c3 = str(by_id["c3-tests"]["run"])
    assert c3.index(_LINUX_POPPLER_MANIFEST_LINE) < c3.index("--install-runtime-closure")
    assert "setpriv" not in c3[: c3.index("--assert-runtime-closure")]
    count = str(count_step["run"])
    for fragment in (
        "len(expected) == 0",
        "collected != len(expected)",
        "passed != len(expected)",
        "failed != 0",
        "errors != 0",
        "skipped != 0",
        "not_executed != 0",
        'prop.get("name") == "linux_poppler"',
        "NOT_EXECUTED",
        "<- tests/_linux_poppler_gate.py",
        "len(actual) != len(expected)",
        "set(actual) != set(expected)",
        "--database-class",
        "--no-database-class",
        "c3_linux_poppler_database_pytest.xml",
        "set(no_database) & set(database)",
        "set(file_one) | set(file_two) != set(expected)",
    ):
        assert fragment in count
    assert "c3_linux_poppler_pytest.xml" in count
    assert '[sys.executable, "-m", "tests._linux_poppler_gate", "--node-ids"]' in count
    assert ">=" not in count


_PROBE_BODY_SHA256 = "164bd48f8666e3f014d6486baa364c834cb86510a8ae6aa20c34de4fb8b22e5b"
# H3-6d pinned the first eight names. H3-6e retargeted that pin to eleven keys.
# H3-6g retargets the H3-6e eleven-key pin to the two child key sets named below.
_LINUX_POPPLER_NO_DATABASE_CHILD_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
    "IDIS_REQUIRE_LINUX_POPPLER",
)
_LINUX_POPPLER_DATABASE_CHILD_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
    "IDIS_DATABASE_URL",
    "IDIS_DATABASE_ADMIN_URL",
    "IDIS_REQUIRE_POSTGRES",
    "IDIS_REQUIRE_LINUX_POPPLER",
)
_LINUX_POPPLER_PRINTER_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
)
_LINUX_POPPLER_DATABASE_PATTERN = r"idis_linux_poppler_[1-9][0-9]*_[1-9][0-9]*_[0-9a-f]{16}"
_LINUX_POPPLER_RECEIPT_NAME = "linux-poppler-receipt.json"


def _probe_body(script: str) -> str:
    marker = "python - <<'PY'\n"
    start = script.index(marker) + len(marker)
    return script[start : script.index("\nPY\n", start)]


def test_linux_poppler_closed_list_mode_shape(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """The closed list owns its bootstrap, revalidates, then runs the two children.

    The H3-6e eleven-key pin is retargeted to the two H3-6g child key sets.
    The database-class child is the H3-6d eight-key set.
    """

    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    assert '["--run-linux-poppler-closed-list"]' in source
    runner = inspect.getsource(bootstrap.run_linux_poppler_closed_list)
    assert "linux_poppler_config" in runner
    assert '{"required_linux_poppler":"ready"}' in runner
    assert "_read_required_preview_receipt" not in runner
    assert "database-receipt.json" not in runner
    assert "required_preview_config" not in runner
    ready = runner.index('{"required_linux_poppler":"ready"}')
    full_ids = runner.index("_linux_poppler_node_ids(config)")
    database_ids = runner.index('_linux_poppler_node_ids(config, selection="database")')
    no_database_ids = runner.index('_linux_poppler_node_ids(config, selection="no-database")')
    partition = runner.index("_require_linux_poppler_partition(")
    first_pytest = runner.index("_capture_linux_poppler_pytest(")
    second_pytest = runner.index("_capture_linux_poppler_pytest(", first_pytest + 1)
    assert runner.count("_capture_linux_poppler_pytest(") == 2
    assert ready < full_ids < database_ids < no_database_ids < partition < first_pytest
    assert first_pytest < second_pytest
    between = runner[first_pytest:second_pytest]
    assert between.count("return ") == 1
    return_at = between.index("return ")
    assert between[return_at:].splitlines()[0].strip() == "return no_database_result"
    before_return = between[:return_at]
    assert "if no_database_result in {130, 143} or no_database_cleanup:" in before_return
    assert "_preview_exit_line(" in before_return
    preview_call = before_return[before_return.index("_preview_exit_line(") :]
    assert '"required_linux_poppler"' in preview_call
    assert '"interrupted"' in preview_call
    assert "_capture_linux_poppler_pytest(" not in between[len("_capture_linux_poppler_pytest(") :]
    revalidate = inspect.getsource(bootstrap._capture_linux_poppler_revalidate)
    assert "--linux-poppler-revalidate" in revalidate
    assert "--required-preview-revalidate" not in revalidate
    assert "database-receipt.json" not in revalidate
    assert "0o600" in inspect.getsource(bootstrap._write_mode_600)
    config = bootstrap.required_preview_config("postgres", ci_env)
    preview_database = "idis_c3_callers_9_8_" + ("cd" * 8)
    preview_url = config.database_url().replace(config.database, preview_database)
    preview_receipt = str(config.root / "database-receipt.json")
    preview_digest = "cd" * 32
    no_database_environment = bootstrap.linux_poppler_pytest_environment(
        config,
        preview_url=preview_url,
        preview_receipt=preview_receipt,
        preview_digest=preview_digest,
        database_class=False,
    )
    database_environment = bootstrap.linux_poppler_pytest_environment(
        config,
        preview_url=preview_url,
        preview_receipt=preview_receipt,
        preview_digest=preview_digest,
        database_class=True,
    )
    assert tuple(no_database_environment) == _LINUX_POPPLER_NO_DATABASE_CHILD_KEYS
    assert tuple(database_environment) == _LINUX_POPPLER_DATABASE_CHILD_KEYS
    qualified = os.pathsep.join((str(config.workspace / ".venv" / "bin"), "/usr/bin", "/bin"))
    for child in (no_database_environment, database_environment):
        assert child["PATH"] == qualified
        assert child["PATH"] != ci_env["PATH"]
        assert child["IDIS_REQUIRE_LINUX_POPPLER"] == "1"
        assert "IDIS_TEST_REDIS_URL" not in child
        assert "PG_ADMIN_PASSWORD" not in child
        assert "IDIS_PG_APP_PASSWORD" not in child
    assert no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"] == preview_url
    assert no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT"] == preview_receipt
    assert (
        no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256"]
        == preview_digest
    )
    for absent in ("IDIS_DATABASE_URL", "IDIS_DATABASE_ADMIN_URL", "IDIS_REQUIRE_POSTGRES"):
        assert absent not in no_database_environment
    assert database_environment["IDIS_DATABASE_URL"] == config.database_url()
    assert database_environment["IDIS_DATABASE_ADMIN_URL"] == config.database_url(admin=True)
    assert database_environment["IDIS_REQUIRE_POSTGRES"] == "1"
    for absent in KEYS:
        assert absent not in database_environment
    no_database_node = "tests/example_closed_list.py::test_closed_list_row"
    database_node = "tests/example_closed_list.py::test_database_row"
    no_database_arguments = bootstrap.linux_poppler_pytest_arguments(
        config.workspace,
        (no_database_node,),
        junit_name="c3_linux_poppler_pytest.xml",
    )
    database_arguments = bootstrap.linux_poppler_pytest_arguments(
        config.workspace,
        (database_node,),
        junit_name="c3_linux_poppler_database_pytest.xml",
    )
    assert no_database_arguments == [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-rA",
        "-o",
        "junit_family=xunit1",
        "--junitxml=" + str(config.workspace / "c3_linux_poppler_pytest.xml"),
        no_database_node,
    ]
    assert database_arguments == [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-rA",
        "-o",
        "junit_family=xunit1",
        "--junitxml=" + str(config.workspace / "c3_linux_poppler_database_pytest.xml"),
        database_node,
    ]
    printer = inspect.getsource(bootstrap._linux_poppler_node_ids)
    assert '"--node-ids"' in printer
    assert '"--database-class"' in printer
    assert '"--no-database-class"' in printer
    assert "tests._linux_poppler_gate" in printer
    validator = inspect.getsource(bootstrap._validate_required_preview_root)
    assert "_LINUX_POPPLER_DATABASE_TESTS_SCRIPT" in validator
    assert "_LINUX_POPPLER_DATABASE_TESTS_LOG" in validator
    assert (
        bootstrap._LINUX_POPPLER_DATABASE_TESTS_SCRIPT == "linux-poppler-database-tests-command.sh"
    )
    assert bootstrap._LINUX_POPPLER_DATABASE_TESTS_LOG == "linux-poppler-database-tests.log"
    steps = _postgres_steps()
    run = str(next(step["run"] for step in steps if step.get("id") == "c3-linux-poppler"))
    c3 = str(next(step["run"] for step in steps if step.get("id") == "c3-tests"))
    assert "python scripts/pg_bootstrap_ci.py --run-linux-poppler-closed-list" in run
    for banned in (
        'receipt["user"]',
        'receipt["password"]',
        "idis-linux-poppler-db.env",
        "IDIS_DATABASE_URL",
        "0o600",
        "IDIS_REQUIRE_POSTGRES=1",
        "IDIS_REQUIRE_LINUX_POPPLER=1",
    ):
        assert banned not in run
    first = _probe_body(c3)
    second = _probe_body(run)
    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == _PROBE_BODY_SHA256
    assert "0o600" in source
    assert "IDIS_REQUIRE_POSTGRES=1" in source


def test_linux_poppler_root_name_and_custody(bootstrap: ModuleType, ci_env: dict[str, str]) -> None:
    """The mode root is idis-linux-poppler-{run}-{attempt}, mode 0700, uid custody."""

    runner = inspect.getsource(bootstrap.run_linux_poppler_closed_list)
    assert "linux_poppler_config" in runner
    assert "create_required_preview_root" in runner
    assert callable(getattr(bootstrap, "linux_poppler_config", None))
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    assert config.context == "linux-poppler"
    assert config.github_env is None
    assert config.root == Path(ci_env["RUNNER_TEMP"]) / "idis-linux-poppler-1234-2"
    assert config.root.parent == Path(ci_env["RUNNER_TEMP"])
    assert not config.root.is_relative_to(Path(ci_env["GITHUB_WORKSPACE"]))
    assert not config.root.exists()
    assert "mode=0o700" in inspect.getsource(bootstrap.create_required_preview_root)
    validator = inspect.getsource(bootstrap._validate_required_preview_root)
    assert "st_uid != os.getuid()" in validator
    assert "0o700" in validator
    bootstrap.create_required_preview_root(config)
    if sys.platform != "win32":
        info = config.root.stat(follow_symlinks=False)
        assert info.st_uid == os.getuid()
        assert stat.S_IMODE(info.st_mode) == 0o700
    bootstrap._validate_required_preview_root(config)


def test_linux_poppler_database_pattern_is_disjoint(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """The database name matches one regex, misses the other two, and stays within 63 bytes."""

    pattern = getattr(bootstrap, "_LINUX_POPPLER_DATABASE", None)
    assert getattr(pattern, "pattern", None) == _LINUX_POPPLER_DATABASE_PATTERN
    assert callable(getattr(bootstrap, "linux_poppler_config", None))
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    samples = {
        bootstrap._LINUX_POPPLER_DATABASE: "idis_linux_poppler_1234_2_" + ("ab" * 8),
        bootstrap._PREVIEW_DATABASE: "idis_c3_callers_1234_2_" + ("ab" * 8),
        bootstrap._DEV0039_DATABASE: "idis_dev0039_1234_2_" + ("ab" * 8),
    }
    assert config.database == samples[bootstrap._LINUX_POPPLER_DATABASE]
    assert len(config.database.encode()) <= 63
    for selected, name in samples.items():
        assert selected.fullmatch(name)
        assert len(name.encode()) <= 63
        for other in samples:
            if other is not selected:
                assert other.fullmatch(name) is None
    carried = bootstrap.required_preview_environment(config)
    rebuilt = bootstrap.linux_poppler_config(carried)
    assert rebuilt.database == config.database
    assert rebuilt.root == config.root
    long_run = dict(_dev_env(ci_env))
    long_run["GITHUB_RUN_ID"] = "1" * 40
    with pytest.raises(ValueError, match="^PREVIEW_DATABASE_INVALID$"):
        bootstrap.linux_poppler_config(long_run)
    bad_nonce = dict(_dev_env(ci_env))
    bad_nonce["IDIS_CI_PREVIEW_NONCE"] = "zz"
    with pytest.raises(ValueError, match="^PREVIEW_DATABASE_INVALID$"):
        bootstrap.linux_poppler_config(bad_nonce)


def test_linux_poppler_create_duplicate_is_fatal(
    bootstrap: ModuleType, ci_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One CREATE. A duplicate SQL error is fatal and is not turned into a lookup."""

    import sqlalchemy

    assert callable(getattr(bootstrap, "_create_linux_poppler_database", None))
    creator = inspect.getsource(bootstrap._create_linux_poppler_database)
    assert creator.count("CREATE DATABASE") == 1
    assert "DROP" not in creator
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    duplicate = sqlalchemy.exc.ProgrammingError("CREATE", None, RuntimeError("synthetic duplicate"))
    connection.execute.side_effect = duplicate
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: engine)
    with pytest.raises(sqlalchemy.exc.ProgrammingError) as raised:
        bootstrap._create_linux_poppler_database(config)
    assert raised.value is duplicate
    assert connection.execute.call_count == 1
    assert (
        str(connection.execute.call_args.args[0])
        == f'CREATE DATABASE "{config.database}" TEMPLATE template0'
    )
    assert not config.root.exists()
    engine.dispose.assert_called_once()


def test_linux_poppler_receipt_is_exclusive_and_canonical(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """The receipt name is exclusive, mode 0o600, revision 0038, and canonical on read."""

    assert getattr(bootstrap, "_LINUX_POPPLER_RECEIPT_NAME", None) == _LINUX_POPPLER_RECEIPT_NAME
    assert callable(getattr(bootstrap, "write_linux_poppler_receipt", None))
    assert callable(getattr(bootstrap, "_read_linux_poppler_receipt", None))
    writer = inspect.getsource(bootstrap.write_linux_poppler_receipt)
    reader = inspect.getsource(bootstrap._read_linux_poppler_receipt)
    assert "os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600" in writer
    assert "raw != canonical" in reader
    assert "database-receipt.json" not in writer
    assert "database-receipt.json" not in reader
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    bootstrap.create_required_preview_root(config)
    receipt = bootstrap.linux_poppler_receipt(config, 11)
    assert receipt["revision"] == "0038"
    assert receipt["database"] == config.database
    path, digest = bootstrap.write_linux_poppler_receipt(config, receipt)
    assert path.name == _LINUX_POPPLER_RECEIPT_NAME
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    assert json.loads(raw) == receipt
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        bootstrap.write_linux_poppler_receipt(config, receipt)
    assert path.read_bytes() == raw
    _read_path, read_digest, oid = bootstrap._read_linux_poppler_receipt(config)
    assert _read_path == path
    assert read_digest == digest
    assert oid == 11
    path.write_bytes(raw.replace(b'"0038"', b'"0037"'))
    with pytest.raises(ValueError, match="^PREVIEW_RECEIPT_INVALID$"):
        bootstrap._read_linux_poppler_receipt(config)


def test_linux_poppler_worker_uses_existing_migration_path(bootstrap: ModuleType) -> None:
    """The worker reaches revision 0038 through ceiling, migration, and the 0038 verifier."""

    assert callable(getattr(bootstrap, "create_linux_poppler_database", None))
    assert callable(getattr(bootstrap, "revalidate_linux_poppler_database", None))
    worker = inspect.getsource(bootstrap.create_linux_poppler_database)
    revalidate = inspect.getsource(bootstrap.revalidate_linux_poppler_database)
    assert "check_migration_ceiling.py" in worker
    assert "run_migrations(" in worker
    assert "_create_linux_poppler_database(" in worker
    assert "_verify_required_database(" in worker
    assert "write_linux_poppler_receipt(" in worker
    assert "downgrade" not in worker.lower()
    assert "DROP" not in worker
    assert "_read_linux_poppler_receipt" in revalidate
    assert "_verify_required_database" in revalidate
    assert "_read_required_preview_receipt" not in revalidate
    assert "database-receipt.json" not in revalidate
    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    assert '["--linux-poppler-worker"]' in source
    assert '["--linux-poppler-revalidate"]' in source


def test_linux_poppler_printer_environment_is_four_keys(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """The node-id printer receives four keys and no database URL or credential."""

    assert callable(getattr(bootstrap, "linux_poppler_printer_environment", None))
    config = bootstrap.required_preview_config("postgres", _dev_env(ci_env))
    environment = bootstrap.linux_poppler_printer_environment(config)
    assert tuple(environment) == _LINUX_POPPLER_PRINTER_KEYS
    for absent in (
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_REQUIRE_POSTGRES",
        "IDIS_REQUIRE_LINUX_POPPLER",
        "PG_ADMIN_PASSWORD",
        "IDIS_PG_APP_PASSWORD",
    ):
        assert absent not in environment
    printer = inspect.getsource(bootstrap._linux_poppler_node_ids)
    assert "linux_poppler_printer_environment(config)" in printer


def test_pg_bootstrap_ci_script_has_no_drop_statement() -> None:
    """The bootstrap script has no DROP statement."""

    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    assert "DROP" not in source


def test_linux_poppler_mode_does_not_reference_c3_receipt(bootstrap: ModuleType) -> None:
    """The closed-list mode does not read the c3 root receipt."""

    runner = inspect.getsource(bootstrap.run_linux_poppler_closed_list)
    revalidate = inspect.getsource(bootstrap._capture_linux_poppler_revalidate)
    assert "database-receipt.json" not in runner
    assert "_read_required_preview_receipt" not in runner
    assert "required_preview_config" not in runner
    assert "database-receipt.json" not in revalidate
    assert "--required-preview-revalidate" not in revalidate


def _arm_linux_poppler_capture(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pytest_result: int | BaseException = 0,
    pytest_results: Sequence[int] | None = None,
    pytest_cleanup: Sequence[bool] | None = None,
) -> dict[str, object]:
    seen: dict[str, object] = {}
    pending = list(pytest_results) if pytest_results is not None else None
    pending_cleanup = list(pytest_cleanup) if pytest_cleanup is not None else None

    def fake_capture(script: Path, log: Path, *, environment: Mapping[str, str], bash: str) -> int:
        del bash
        command = script.read_text(encoding="utf-8")
        if "--linux-poppler-callers-worker" in command:
            bound = bootstrap.linux_poppler_callers_config(environment)
            bootstrap.write_required_preview_receipt(
                bound, bootstrap.required_preview_receipt(bound, 22)
            )
            seen["callers_worker_environment"] = dict(environment)
            log.write_bytes(b"callers-worker\n")
            return 0
        if "--linux-poppler-callers-revalidate" in command:
            seen["callers_revalidate_environment"] = dict(environment)
            log.write_bytes(b"callers-revalidate\n")
            return 0
        if "--linux-poppler-worker" in command:
            bound = bootstrap.linux_poppler_config(environment)
            bootstrap.write_linux_poppler_receipt(bound, bootstrap.linux_poppler_receipt(bound, 11))
            seen["worker_environment"] = dict(environment)
            log.write_bytes(b"worker\n")
            return 0
        if "--linux-poppler-revalidate" in command:
            seen["revalidate_environment"] = dict(environment)
            log.write_bytes(b"revalidate\n")
            return 0
        argv = shlex.split(command.splitlines()[-1])
        invocations = seen.setdefault("pytest_invocations", [])
        assert isinstance(invocations, list)
        invocations.append(
            {
                "environment": dict(environment),
                "argv": argv,
                "script": script.name,
                "log": log.name,
            }
        )
        seen["pytest_environment"] = dict(environment)
        seen["pytest_argv"] = argv
        if isinstance(pytest_result, BaseException):
            raise pytest_result
        result = pending.pop(0) if pending is not None else pytest_result
        log.write_bytes(b"CLOSED_LIST\n")
        return result

    def node_ids(_config: object, *, selection: str = "") -> tuple[str, ...]:
        selections = seen.setdefault("printer_selections", [])
        assert isinstance(selections, list)
        selections.append(selection)
        no_database = ("tests/example_closed_list.py::test_closed_list_row",)
        database = ("tests/example_closed_list.py::test_database_row",)
        if selection == "database":
            return database
        if selection == "no-database":
            return no_database
        if selection == "":
            return no_database + database
        raise ValueError("PREVIEW_LINUX_POPPLER_NODE_IDS_INVALID")

    _bind_capture(bootstrap, monkeypatch, fake_capture)
    monkeypatch.setattr(bootstrap, "_linux_poppler_node_ids", node_ids)
    if pending_cleanup is not None:
        original_capture = bootstrap._capture_linux_poppler
        pytest_scripts = {
            bootstrap._LINUX_POPPLER_TESTS_SCRIPT,
            bootstrap._LINUX_POPPLER_DATABASE_TESTS_SCRIPT,
        }

        def capture_with_cleanup(
            config: object,
            *,
            script_name: str,
            log_name: str,
            arguments: Sequence[str],
            environment: Mapping[str, str],
        ) -> tuple[int, bool]:
            result, cleanup_failed = original_capture(
                config,
                script_name=script_name,
                log_name=log_name,
                arguments=arguments,
                environment=environment,
            )
            if script_name in pytest_scripts and pending_cleanup:
                cleanup_failed = pending_cleanup.pop(0)
            return result, cleanup_failed

        monkeypatch.setattr(bootstrap, "_capture_linux_poppler", capture_with_cleanup)
    return seen


def test_linux_poppler_ready_line_follows_owned_bootstrap(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After both revalidations it prints ready, then runs the two children.

    The H3-6e eleven-key pin is retargeted to the two H3-6g child key sets.
    The H3-6d absence pin for database-receipt.json is retargeted: that file is
    database 2's receipt inside the mode root.
    """

    seen = _arm_linux_poppler_capture(bootstrap, monkeypatch, pytest_result=0)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_linux_poppler_closed_list() == 0
    assert seen["printer_selections"] == ["", "database", "no-database"]
    invocations = seen["pytest_invocations"]
    assert isinstance(invocations, list)
    assert len(invocations) == 2
    assert capsys.readouterr().out == (
        '{"required_linux_poppler":"ready"}\nCLOSED_LIST\nCLOSED_LIST\n'
    )
    runner_temp = Path(ci_env["RUNNER_TEMP"])
    root = runner_temp / "idis-linux-poppler-1234-2"
    assert root.is_dir()
    assert not (runner_temp / "idis-c3-postgres-1234-2").exists()
    assert not (runner_temp / "idis-dev0039-1234-2").exists()
    receipt_path = root / "database-receipt.json"
    assert receipt_path.is_file()
    assert (root / _LINUX_POPPLER_RECEIPT_NAME).is_file()
    no_database_environment = invocations[0]["environment"]
    database_environment = invocations[1]["environment"]
    assert isinstance(no_database_environment, dict)
    assert isinstance(database_environment, dict)
    assert tuple(no_database_environment) == _LINUX_POPPLER_NO_DATABASE_CHILD_KEYS
    assert tuple(database_environment) == _LINUX_POPPLER_DATABASE_CHILD_KEYS
    assert no_database_environment["IDIS_REQUIRE_LINUX_POPPLER"] == "1"
    assert database_environment["IDIS_REQUIRE_POSTGRES"] == "1"
    assert database_environment["IDIS_REQUIRE_LINUX_POPPLER"] == "1"
    database_name = str(database_environment["IDIS_DATABASE_URL"]).rsplit("/", 1)[-1]
    admin_database = str(database_environment["IDIS_DATABASE_ADMIN_URL"]).rsplit("/", 1)[-1]
    assert admin_database == database_name
    assert bootstrap._LINUX_POPPLER_DATABASE.fullmatch(database_name)
    assert bootstrap._PREVIEW_DATABASE.fullmatch(database_name) is None
    assert bootstrap._DEV0039_DATABASE.fullmatch(database_name) is None
    preview_database = str(no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"]).rsplit(
        "/", 1
    )[-1]
    assert preview_database != database_name
    assert bootstrap._PREVIEW_DATABASE.fullmatch(preview_database)
    assert bootstrap._LINUX_POPPLER_DATABASE.fullmatch(preview_database) is None
    assert "IDIS_DATABASE_URL" not in no_database_environment
    assert "IDIS_DATABASE_ADMIN_URL" not in no_database_environment
    assert "IDIS_REQUIRE_POSTGRES" not in no_database_environment
    assert no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT"].endswith(
        "database-receipt.json"
    )
    for absent in KEYS:
        assert absent not in database_environment
    assert json.loads(receipt_path.read_bytes())["database"] == preview_database
    for child in (no_database_environment, database_environment):
        assert "IDIS_TEST_REDIS_URL" not in child
        assert "PG_ADMIN_PASSWORD" not in child
        assert "IDIS_PG_APP_PASSWORD" not in child
    worker_environment = seen["worker_environment"]
    assert isinstance(worker_environment, dict)
    assert "PG_ADMIN_USER" in worker_environment
    assert "IDIS_CI_PREVIEW_NONCE" in worker_environment
    assert "IDIS_DATABASE_URL" not in worker_environment
    no_database_argv = invocations[0]["argv"]
    database_argv = invocations[1]["argv"]
    assert isinstance(no_database_argv, list)
    assert isinstance(database_argv, list)
    assert invocations[0]["script"] == "linux-poppler-tests-command.sh"
    assert invocations[0]["log"] == "linux-poppler-tests.log"
    assert invocations[1]["script"] == "linux-poppler-database-tests-command.sh"
    assert invocations[1]["log"] == "linux-poppler-database-tests.log"
    assert "tests/example_closed_list.py::test_closed_list_row" in no_database_argv
    assert "tests/example_closed_list.py::test_database_row" not in no_database_argv
    assert "tests/example_closed_list.py::test_database_row" in database_argv
    assert "tests/example_closed_list.py::test_closed_list_row" not in database_argv
    assert "junit_family=xunit1" in no_database_argv
    assert "junit_family=xunit1" in database_argv
    assert any(item.endswith("c3_linux_poppler_pytest.xml") for item in no_database_argv)
    assert any(item.endswith("c3_linux_poppler_database_pytest.xml") for item in database_argv)


@pytest.mark.parametrize(("signum", "cleanup_failed"), [(2, False), (15, True)])
def test_linux_poppler_interrupt_line_is_128_plus_signum(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    signum: int,
    cleanup_failed: bool,
) -> None:
    """A closed-list signal prints the interrupted line with 128+signum and cleanup_failed."""

    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import ci_private_inputs as capture

    _arm_linux_poppler_capture(
        bootstrap,
        monkeypatch,
        pytest_result=capture.Interrupted(signum, cleanup_failed),
    )
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_linux_poppler_closed_list() == 128 + signum
    assert capsys.readouterr().out == (
        '{"required_linux_poppler":"ready"}\n'
        + json.dumps(
            {
                "required_linux_poppler": "interrupted",
                "exit": 128 + signum,
                "cleanup_failed": cleanup_failed,
                "code": "PREVIEW_UNCLASSIFIED",
                "phase": "linux-poppler",
            },
            separators=(",", ":"),
        )
        + "\n"
    )


@pytest.mark.parametrize(("results", "exit_code"), [((1, 0), 1), ((0, 4), 4)])
def test_linux_poppler_second_invocation_runs_whatever_the_first_result(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    results: tuple[int, int],
    exit_code: int,
) -> None:
    """Invocation 2 runs when invocation 1 has any result, and the exit is the worse one.

    The H3-6e eleven-key pin is retargeted to the two H3-6g child key sets.
    """

    seen = _arm_linux_poppler_capture(bootstrap, monkeypatch, pytest_results=results)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_linux_poppler_closed_list() == exit_code
    invocations = seen["pytest_invocations"]
    assert isinstance(invocations, list)
    assert len(invocations) == 2
    assert tuple(invocations[0]["environment"]) == _LINUX_POPPLER_NO_DATABASE_CHILD_KEYS
    assert tuple(invocations[1]["environment"]) == _LINUX_POPPLER_DATABASE_CHILD_KEYS
    assert invocations[0]["script"] == "linux-poppler-tests-command.sh"
    assert invocations[1]["script"] == "linux-poppler-database-tests-command.sh"
    assert capsys.readouterr().out.count("CLOSED_LIST\n") == 2


@pytest.mark.parametrize(
    ("exit_code", "cleanup_failed"),
    [(130, False), (143, False), (0, True)],
)
def test_linux_poppler_no_second_capture_after_interrupt_or_cleanup_failure(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_code: int,
    cleanup_failed: bool,
) -> None:
    """Invocation 2 stays unstarted after an interrupt or a cleanup failure of invocation 1."""

    seen = _arm_linux_poppler_capture(
        bootstrap,
        monkeypatch,
        pytest_results=(exit_code, 0),
        pytest_cleanup=(True,) if cleanup_failed else None,
    )
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_linux_poppler_closed_list() == exit_code
    invocations = seen["pytest_invocations"]
    assert isinstance(invocations, list)
    assert len(invocations) == 1
    assert tuple(invocations[0]["environment"]) == _LINUX_POPPLER_NO_DATABASE_CHILD_KEYS
    assert invocations[0]["script"] == "linux-poppler-tests-command.sh"
    assert capsys.readouterr().out == (
        '{"required_linux_poppler":"ready"}\n'
        + json.dumps(
            {
                "required_linux_poppler": "interrupted",
                "exit": exit_code,
                "cleanup_failed": cleanup_failed,
                "code": "PREVIEW_UNCLASSIFIED",
                "phase": "linux-poppler",
            },
            separators=(",", ":"),
        )
        + "\n"
    )


def test_linux_poppler_partition_blocks_before_pytest(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Overlapping printer classes block after the ready line and before pytest."""

    seen = _arm_linux_poppler_capture(bootstrap, monkeypatch, pytest_result=0)

    def overlap(_config: object, *, selection: str = "") -> tuple[str, ...]:
        selections = seen.setdefault("printer_selections", [])
        assert isinstance(selections, list)
        selections.append(selection)
        return ("tests/example_closed_list.py::test_closed_list_row",)

    monkeypatch.setattr(bootstrap, "_linux_poppler_node_ids", overlap)
    monkeypatch.setattr(bootstrap.os, "environ", _dev_env(ci_env))
    assert bootstrap.run_linux_poppler_closed_list() == 1
    assert seen["printer_selections"] == ["", "database", "no-database"]
    assert seen.get("pytest_invocations", []) == []
    assert capsys.readouterr().out == (
        '{"required_linux_poppler":"ready"}\n'
        '{"required_linux_poppler":"blocked","code":"PREVIEW_LINUX_POPPLER_NODE_IDS_INVALID",'
        '"phase":"linux-poppler"}\n'
    )


def test_linux_poppler_ready_line_follows_both_revalidations(bootstrap: ModuleType) -> None:
    """One ready line is printed after both read-only revalidations and before pytest."""

    runner = inspect.getsource(bootstrap.run_linux_poppler_closed_list)
    ready = runner.index('{"required_linux_poppler":"ready"}')
    pytest_at = runner.index("_capture_linux_poppler_pytest(")
    db1 = runner.index("_capture_linux_poppler_revalidate(")
    assert runner.count('{"required_linux_poppler":"ready"}') == 1
    assert db1 < ready < pytest_at
    db2 = runner.index("_capture_linux_poppler_callers_revalidate(")
    assert db2 < ready < pytest_at
    assert "DROP" not in runner
    assert "IDIS_TEST_REDIS_URL" not in runner
    assert "required_preview_config(" not in runner
    assert "idis-c3-postgres-" not in runner
    assert "idis-c3-check-" not in runner
    assert '"required_linux_poppler": "interrupted"' in runner
    assert '"exit": 130' in runner
    assert '"phase": "linux-poppler"' in runner


def test_linux_poppler_callers_database_uses_existing_worker_and_reader(
    bootstrap: ModuleType, ci_env: dict[str, str]
) -> None:
    """Database 2 is the existing required-caller worker and reader under the mode root."""

    source = (REPO / "scripts/pg_bootstrap_ci.py").read_text(encoding="utf-8")
    assert '["--linux-poppler-callers-worker"]' in source
    assert '["--linux-poppler-callers-revalidate"]' in source
    worker_at = source.index('["--linux-poppler-callers-worker"]')
    revalidate_at = source.index('["--linux-poppler-callers-revalidate"]')
    closed_at = source.index('["--run-linux-poppler-closed-list"]')
    worker_window = source[worker_at:revalidate_at]
    revalidate_window = source[revalidate_at:closed_at]
    assert "create_linux_poppler_callers_database(" in worker_window
    assert "required_preview_config(" not in worker_window
    creator = inspect.getsource(bootstrap.create_linux_poppler_callers_database)
    assert "_required_preview_worker(" in creator
    assert "required_preview_config(" not in creator
    assert "idis-c3-postgres-" not in creator
    assert "idis-c3-check-" not in creator
    assert "DROP" not in creator
    assert "revalidate_linux_poppler_callers_database(" in revalidate_window
    assert "required_preview_config(" not in revalidate_window
    config_source = inspect.getsource(bootstrap.linux_poppler_callers_config)
    revalidate_source = inspect.getsource(bootstrap.revalidate_linux_poppler_callers_database)
    values_source = inspect.getsource(bootstrap.linux_poppler_frozen_preview_values)
    for body in (config_source, revalidate_source, values_source):
        assert "required_preview_config(" not in body
        assert "idis-c3-postgres-" not in body
        assert "idis-c3-check-" not in body
        assert "DROP" not in body
    assert "_read_required_preview_receipt(" in revalidate_source
    assert "_verify_required_database(" in revalidate_source
    assert "export_required_preview(" not in values_source
    assert "_read_required_preview_receipt(" in values_source
    assert 'token in value for token in ("\\r", "\\n", "<<")' in values_source
    owned = bootstrap.linux_poppler_config(_dev_env(ci_env))
    callers = bootstrap.linux_poppler_callers_config(_dev_env(ci_env), owned=owned)
    assert callers.root == owned.root
    assert callers.root.name == "idis-linux-poppler-1234-2"
    assert "idis-c3-postgres-" not in callers.root.name
    assert "idis-c3-check-" not in callers.root.name
    assert callers.host == owned.host
    assert callers.port == owned.port
    assert callers.service_container_id == owned.service_container_id
    assert callers.admin_user == owned.admin_user
    assert callers.app_user == owned.app_user
    assert callers.admin_password == owned.admin_password
    assert callers.app_password == owned.app_password
    assert callers.github_env is None
    assert callers.context == "linux-poppler"
    assert callers.database != owned.database
    assert bootstrap._PREVIEW_DATABASE.fullmatch(callers.database)
    assert bootstrap._LINUX_POPPLER_DATABASE.fullmatch(callers.database) is None
    assert bootstrap._DEV0039_DATABASE.fullmatch(callers.database) is None
    assert callers.database.rsplit("_", 1)[-1] != owned.database.rsplit("_", 1)[-1]
    carried = bootstrap.required_preview_environment(callers)
    rebuilt = bootstrap.linux_poppler_callers_config(carried)
    assert rebuilt.database == callers.database
    assert rebuilt.root == callers.root
    bootstrap.create_required_preview_root(owned)
    receipt = bootstrap.required_preview_receipt(callers, 22)
    path, digest = bootstrap.write_required_preview_receipt(callers, receipt)
    assert path == callers.root / "database-receipt.json"
    bound, read_path, read_digest, oid = bootstrap._read_required_preview_receipt(callers)
    assert bound.database == callers.database
    assert read_path == path
    assert read_digest == digest
    assert oid == 22
    preview_url, preview_receipt, preview_digest = bootstrap.linux_poppler_frozen_preview_values(
        callers
    )
    assert preview_url == callers.database_url()
    assert preview_url != owned.database_url()
    assert preview_receipt == str(path)
    assert preview_digest == digest
    assert preview_digest == hashlib.sha256(path.read_bytes()).hexdigest()
    no_database_environment = bootstrap.linux_poppler_pytest_environment(
        owned,
        preview_url=preview_url,
        preview_receipt=preview_receipt,
        preview_digest=preview_digest,
        database_class=False,
    )
    database_environment = bootstrap.linux_poppler_pytest_environment(
        owned,
        preview_url=preview_url,
        preview_receipt=preview_receipt,
        preview_digest=preview_digest,
        database_class=True,
    )
    assert tuple(no_database_environment) == _LINUX_POPPLER_NO_DATABASE_CHILD_KEYS
    assert tuple(database_environment) == _LINUX_POPPLER_DATABASE_CHILD_KEYS
    assert no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"] == preview_url
    assert (
        no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"] == callers.database_url()
    )
    assert no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"] != owned.database_url()
    preview_name = no_database_environment["IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL"].rsplit("/", 1)[
        -1
    ]
    owned_name = database_environment["IDIS_DATABASE_URL"].rsplit("/", 1)[-1]
    assert owned_name != preview_name
    assert database_environment["IDIS_DATABASE_URL"] == owned.database_url()
    assert database_environment["IDIS_DATABASE_ADMIN_URL"] == owned.database_url(admin=True)
    assert database_environment["IDIS_REQUIRE_POSTGRES"] == "1"
    assert bootstrap._LINUX_POPPLER_DATABASE.fullmatch(owned_name)
    assert bootstrap._PREVIEW_DATABASE.fullmatch(preview_name)
    for absent in ("IDIS_DATABASE_URL", "IDIS_DATABASE_ADMIN_URL", "IDIS_REQUIRE_POSTGRES"):
        assert absent not in no_database_environment
    for absent in KEYS:
        assert absent not in database_environment
    for child in (no_database_environment, database_environment):
        assert "IDIS_TEST_REDIS_URL" not in child
        assert "PG_ADMIN_PASSWORD" not in child
        assert "IDIS_PG_APP_PASSWORD" not in child
    callers_capture = inspect.getsource(bootstrap._capture_linux_poppler_callers)
    callers_revalidate = inspect.getsource(bootstrap._capture_linux_poppler_callers_revalidate)
    assert "_capture_linux_poppler(" in callers_capture
    assert "_LINUX_POPPLER_CALLERS_SCRIPT" in callers_capture
    assert "_LINUX_POPPLER_CALLERS_LOG" in callers_capture
    assert "--linux-poppler-callers-worker" in callers_capture
    assert "--linux-poppler-callers-revalidate" in callers_revalidate
    assert "_LINUX_POPPLER_CALLERS_REVALIDATE_SCRIPT" in callers_revalidate
    assert "_LINUX_POPPLER_CALLERS_REVALIDATE_LOG" in callers_revalidate
    assert "_write_mode_600(" in inspect.getsource(bootstrap._capture_linux_poppler)
    assert bootstrap._LINUX_POPPLER_CALLERS_SCRIPT == "linux-poppler-callers-command.sh"
    assert bootstrap._LINUX_POPPLER_CALLERS_LOG == "linux-poppler-callers.log"
    assert (
        bootstrap._LINUX_POPPLER_CALLERS_REVALIDATE_SCRIPT
        == "linux-poppler-callers-revalidate-command.sh"
    )
    assert bootstrap._LINUX_POPPLER_CALLERS_REVALIDATE_LOG == "linux-poppler-callers-revalidate.log"
    assert "database-receipt.json" in inspect.getsource(bootstrap._validate_required_preview_root)


_LAMBDA_BATCH_NODE = (
    "tests/slice100_task4/test_watermark_batch_attestation_red.py::"
    "test_batch_rejects_order_duplicates_and_schema_drift"
)
_LAMBDA_REVIEW_NODE = (
    "tests/slice100_task4/test_watermark_review_resolution.py::"
    "test_resolved_prescan_mutation_fails_closed"
)
_ACCEPTED_LAMBDA_NODE_IDS = (
    f"{_LAMBDA_BATCH_NODE}[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID0]",
    f"{_LAMBDA_BATCH_NODE}[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID1]",
    f"{_LAMBDA_BATCH_NODE}[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID2]",
    f"{_LAMBDA_BATCH_NODE}[<lambda>-WATERMARK_BATCH_ATTESTATION_INVALID3]",
    f"{_LAMBDA_REVIEW_NODE}[<lambda>0]",
    f"{_LAMBDA_REVIEW_NODE}[<lambda>1]",
    f"{_LAMBDA_REVIEW_NODE}[<lambda>2]",
    f"{_LAMBDA_REVIEW_NODE}[<lambda>3]",
    "x.py::t[<lambda>0]",
)
_REFUSED_LINUX_POPPLER_NODE_IDS = (
    ("leading-dash", "-x.py::t"),
    ("space", "x.py::t a"),
    ("tab", "x.py::t\ta"),
    ("semicolon", "x.py::t;a"),
    ("dollar", "x.py::t$a"),
    ("backtick", "x.py::t`a"),
    ("single-quote", "x.py::t'a"),
    ("double-quote", 'x.py::t"a'),
    ("pipe", "x.py::t|a"),
    ("ampersand", "x.py::t&a"),
    ("backslash", "x.py::t\\a"),
    ("open-paren", "x.py::t(a"),
    ("equals", "x.py::t=a"),
    ("missing-separator", "x.py:t"),
    ("open-angle-before-bracket", "a<b.py::t[0]"),
    ("close-angle-before-bracket", "a>b.py::t[0]"),
)


def _bind_linux_poppler_node_id_printer(
    bootstrap: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    lines: Sequence[str],
) -> None:
    """Return these printer lines from the node-id child."""

    completed = subprocess.CompletedProcess(
        args=[sys.executable, "-m", "tests._linux_poppler_gate", "--node-ids"],
        returncode=0,
        stdout="".join(f"{line}\n" for line in lines),
        stderr="",
    )

    def run(
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, check, text
        assert capture_output is True
        assert list(command) == [
            sys.executable,
            "-m",
            "tests._linux_poppler_gate",
            "--node-ids",
        ]
        return completed

    monkeypatch.setattr(bootstrap.subprocess, "run", run)


def test_linux_poppler_node_ids_accept_lambda_parameter_ids(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eight real lambda ids and the synthetic parameter id are accepted."""

    _bind_linux_poppler_node_id_printer(bootstrap, monkeypatch, _ACCEPTED_LAMBDA_NODE_IDS)
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    assert bootstrap._linux_poppler_node_ids(config) == _ACCEPTED_LAMBDA_NODE_IDS


@pytest.mark.parametrize(
    "node_id",
    [node_id for _name, node_id in _REFUSED_LINUX_POPPLER_NODE_IDS],
    ids=[name for name, _node_id in _REFUSED_LINUX_POPPLER_NODE_IDS],
)
def test_linux_poppler_node_ids_refuse_unquoted_shell_text(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    node_id: str,
) -> None:
    """A leading dash, shell metacharacters, and angles before '[' are refused."""

    _bind_linux_poppler_node_id_printer(bootstrap, monkeypatch, (node_id,))
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    with pytest.raises(ValueError, match="^PREVIEW_LINUX_POPPLER_NODE_IDS_INVALID$"):
        bootstrap._linux_poppler_node_ids(config)


def test_mode_command_shlex_join_preserves_lambda_node_ids(
    bootstrap: ModuleType,
    ci_env: dict[str, str],
) -> None:
    """_mode_command still quotes with shlex.join, and each lambda id round-trips."""

    assert "shlex.join(arguments)" in inspect.getsource(bootstrap._mode_command)
    config = bootstrap.linux_poppler_config(_dev_env(ci_env))
    arguments = bootstrap.linux_poppler_pytest_arguments(
        config.workspace, _ACCEPTED_LAMBDA_NODE_IDS
    )
    command = bootstrap._mode_command(config, arguments)
    restored = shlex.split(command.splitlines()[1])
    assert restored[0] == "exec"
    assert restored[1:] == arguments
    assert tuple(restored[-len(_ACCEPTED_LAMBDA_NODE_IDS) :]) == _ACCEPTED_LAMBDA_NODE_IDS
