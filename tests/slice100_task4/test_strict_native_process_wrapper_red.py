from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "invoke_strict_native_process.ps1"
PLAN = REPO_ROOT / "docs" / "plans" / "2026-07-15-slice100-strict-real-example-live-dry-run.md"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_wrapper(
    *,
    child: Path,
    stdout_path: Path,
    stderr_path: Path,
    background: bool = False,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:  # pragma: no cover - declared acceptance environment is Windows
        pytest.skip("PowerShell is required for the strict native-process wrapper contract")
    background_arg = " -Background" if background else ""
    wait_for_background = (
        "$childProcess = Get-Process -Id $result.LauncherProcessId; "
        "$result | Add-Member -NotePropertyName WasAliveAtReturn "
        "-NotePropertyValue (-not $childProcess.HasExited); "
        "$exited = $childProcess.WaitForExit(10000); "
        "if (-not $exited) { Stop-Process -Id $result.LauncherProcessId -Force; "
        "throw 'BACKGROUND_CHILD_TIMEOUT' }; "
        if background
        else ""
    )
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"$result = & {_quote(WRAPPER)} "
        f"-FilePath {_quote(sys.executable)} "
        f"-ArgumentList @({_quote(child)}) "
        f"-WorkingDirectory {_quote(child.parent)} "
        f"-StandardOutputPath {_quote(stdout_path)} "
        f"-StandardErrorPath {_quote(stderr_path)}"
        f"{background_arg}; "
        f"{wait_for_background}"
        "$result | ConvertTo-Json -Compress"
    )
    return subprocess.run(  # noqa: S603 - fixed PowerShell binary and local test artifacts
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _last_json_line(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "wrapper did not return safe process metadata"
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def test_stderr_warning_with_zero_exit_is_success_and_fully_captured(tmp_path: Path) -> None:
    child_dir = tmp_path / "child path with spaces"
    child_dir.mkdir()
    child = child_dir / "warn_and_succeed.py"
    child.write_text(
        "import sys\n"
        "sys.stdout.write('captured stdout\\n')\n"
        "sys.stderr.write('admitted warning\\n')\n",
        encoding="utf-8",
    )
    stdout_path = tmp_path / "child.stdout.log"
    stderr_path = tmp_path / "child.stderr.log"

    completed = _run_wrapper(
        child=child,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    metadata = _last_json_line(completed.stdout)
    assert metadata["Mode"] == "foreground"
    assert metadata["ExitCode"] == 0
    assert int(metadata["ProcessId"]) > 0
    assert stdout_path.read_text(encoding="utf-8").splitlines() == ["captured stdout"]
    assert stderr_path.read_text(encoding="utf-8").splitlines() == ["admitted warning"]


def test_clean_stderr_with_nonzero_exit_fails_from_exit_code(tmp_path: Path) -> None:
    child = tmp_path / "clean_and_fail.py"
    child.write_text(
        "import sys\nsys.stdout.write('captured before failure\\n')\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    stdout_path = tmp_path / "child.stdout.log"
    stderr_path = tmp_path / "child.stderr.log"

    completed = _run_wrapper(
        child=child,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert completed.returncode != 0
    assert "IDIS_NATIVE_PROCESS_EXIT_7" in completed.stderr
    assert stdout_path.read_text(encoding="utf-8").splitlines() == ["captured before failure"]
    assert stderr_path.read_bytes() == b""


def test_background_service_stderr_is_isolated_from_the_caller(tmp_path: Path) -> None:
    child = tmp_path / "background_warning.py"
    child.write_text(
        "import sys\n"
        "import time\n"
        "sys.stdout.write('background stdout\\n')\n"
        "sys.stderr.write('background warning\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
        "time.sleep(0.75)\n",
        encoding="utf-8",
    )
    stdout_path = tmp_path / "service.stdout.log"
    stderr_path = tmp_path / "service.stderr.log"

    completed = _run_wrapper(
        child=child,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        background=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    metadata = _last_json_line(completed.stdout)
    assert metadata["Mode"] == "background"
    assert metadata["ExitCode"] is None
    assert metadata["SchemaVersion"] == 2
    assert int(metadata["LauncherProcessId"]) > 0
    assert metadata["WasAliveAtReturn"] is True
    assert stdout_path.read_text(encoding="utf-8").splitlines() == ["background stdout"]
    assert stderr_path.read_text(encoding="utf-8").splitlines() == ["background warning"]


def test_existing_capture_file_is_rejected_without_overwrite(tmp_path: Path) -> None:
    child = tmp_path / "must_not_run.py"
    child.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    stdout_path = tmp_path / "existing.stdout.log"
    stderr_path = tmp_path / "fresh.stderr.log"
    stdout_path.write_text("immutable evidence\n", encoding="utf-8")

    completed = _run_wrapper(
        child=child,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert completed.returncode != 0
    assert "IDIS_NATIVE_PROCESS_STDOUT_ALREADY_EXISTS" in completed.stderr
    assert stdout_path.read_text(encoding="utf-8") == "immutable evidence\n"
    assert not stderr_path.exists()


def test_stdout_and_stderr_capture_paths_must_differ(tmp_path: Path) -> None:
    child = tmp_path / "must_not_run.py"
    child.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    shared_path = tmp_path / "shared.log"

    completed = _run_wrapper(
        child=child,
        stdout_path=shared_path,
        stderr_path=shared_path,
    )

    assert completed.returncode != 0
    assert "IDIS_NATIVE_PROCESS_CAPTURE_PATHS_MUST_DIFFER" in completed.stderr
    assert not shared_path.exists()


def test_section_16_has_no_direct_powershell_native_strict_launcher_calls() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    section_16 = plan.split("## 16. Exact target-state command sequence", maxsplit=1)[1]
    section_16 = section_16.split("## 17.", maxsplit=1)[0]

    assert (
        re.search(
            r"(?m)^\s*python\s+-m\s+idis\.runtime\.strict_launcher\s+",
            section_16,
        )
        is None
    )
    assert re.search(r"(?m)^\s*python\s+-c\s+", section_16) is None
    assert "Get-Command python" not in section_16
    assert re.search(r"(?m)^\s*&\s*\$Python\s+", section_16) is None
    assert re.search(r"(?mi)^\s*Start-Process\b", section_16) is None
    assert "$Python = 'C:\\Projects\\IDIS\\IDIS\\.venv\\Scripts\\python.exe'" in section_16
    assert section_16.count("& $StrictNativeProcess") == 19

    blocks = re.findall(
        r"(?s)& \$StrictNativeProcess(?P<body>.*?)(?=\n```|\n\n)",
        section_16,
    )
    assert len(blocks) == 19
    without_wrapper_blocks = re.sub(
        r"(?s)& \$StrictNativeProcess.*?(?=\n```|\n\n)",
        "",
        section_16,
    )
    assert "idis.runtime.strict_launcher" not in without_wrapper_blocks
    preflight_blocks = [block for block in blocks if "idis.runtime.strict_launcher" not in block]
    assert len(preflight_blocks) == 3
    assert all("'-c'," in block for block in preflight_blocks)
    strict_blocks = [block for block in blocks if "idis.runtime.strict_launcher" in block]
    assert len(strict_blocks) == 16
    commands: list[str] = []
    for block in strict_blocks:
        match = re.search(r"(?m)^\s*'(migrate|governance|gate|api|worker)',\s*$", block)
        assert match is not None
        command = match.group(1)
        commands.append(command)
        assert ("-Background" in block) is (command in {"api", "worker"})

    background_blocks = [block for block in strict_blocks if "-Background" in block]
    assert len(background_blocks) == 6
    assert all(block.count("'--pid-file'") == 1 for block in background_blocks)

    assert Counter(commands) == Counter(
        {"migrate": 2, "governance": 1, "gate": 7, "api": 4, "worker": 2}
    )


def test_task4_api_boots_have_bounded_authenticated_admission_before_gate() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    task4 = plan.split(
        "### 16.0 Current Task-4 acceptance-015 ingestion and restart verification",
        maxsplit=1,
    )[1].split("### 16.1", maxsplit=1)[0]

    assert "PID_MISMATCH" not in task4
    assert task4.count("Wait-StrictServiceProcessIdentity") == 4
    for phase, variable in (("INITIAL", "InitialIdentity"), ("RESTART", "RestartIdentity")):
        assert f"TASK4_API_{phase}_PORT_TIMEOUT" in task4
        assert f"api.{phase.lower()}.process.json" in task4
        assert f"api.{phase.lower()}.admission.stdout.log" in task4
        assert f"api.{phase.lower()}.admission.stderr.log" in task4
        assert f"${variable}.ServiceProcessId" in task4
        assert f"${variable}.LauncherProcessId" in task4

    assert task4.count("TASK4_API_AUTHENTICATED_STRICT_READINESS_GREEN") == 2
    assert "Stop-Process -Id $InitialIdentity.ServiceProcessId -Force" in task4
    assert "Stop-Process -Id $RestartIdentity.ServiceProcessId -Force" in task4
    assert "TASK4_API_INITIAL_SERVICE_PROCESS_STILL_RUNNING" in task4
    assert "TASK4_API_INITIAL_LAUNCHER_PROCESS_STILL_RUNNING" in task4
    assert "TASK4_API_RESTART_SERVICE_PROCESS_STILL_RUNNING" in task4
    assert "TASK4_API_RESTART_LAUNCHER_PROCESS_STILL_RUNNING" in task4
    assert task4.count("TASK4_API_PORT_STILL_LISTENING") == 2
    assert "TASK4_API_INITIAL_IDENTITY_CHANGED_BEFORE_KILL" in task4
    assert "TASK4_API_RESTART_IDENTITY_CHANGED_BEFORE_KILL" in task4
    assert "TASK4_API_RESTART_SERVICE_PID_REUSED" in task4
    assert task4.index("api.initial.process.json") < task4.index("api.initial.admission.stdout.log")
    assert task4.index("api.initial.admission.stdout.log") < task4.index(
        '"$Task4ReportRoot\\gate.initial.stdout.log"'
    )
    assert task4.index("api.restart.process.json") < task4.index("api.restart.admission.stdout.log")
    assert task4.index("api.restart.admission.stdout.log") < task4.index(
        '"$Task4ReportRoot\\gate.restart.stdout.log"'
    )


def test_full_live_api_and_worker_boots_use_the_shared_dual_pid_contract() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    full_live = plan.split("### 16.3 Start role-separated real API and worker", maxsplit=1)[
        1
    ].split("## 17.", maxsplit=1)[0]

    assert full_live.count("Wait-StrictServiceProcessIdentity") == 8
    for role in ("api", "worker"):
        for phase in ("initial", "restart"):
            assert f"{role}.{phase}.process.json" in full_live
    for variable in (
        "ApiInitialIdentity",
        "WorkerInitialIdentity",
        "ApiRestartIdentity",
        "WorkerRestartIdentity",
    ):
        assert f"${variable}.LauncherProcessId" in full_live
        assert f"${variable}.ServiceProcessId" in full_live

    assert "Stop-Process -Id $ApiInitialIdentity.ServiceProcessId -Force" in full_live
    assert "Stop-Process -Id $WorkerInitialIdentity.ServiceProcessId -Force" in full_live
    assert "Stop-Process -Id $ApiRestartIdentity.ServiceProcessId -Force" in full_live
    assert "Stop-Process -Id $WorkerRestartIdentity.ServiceProcessId -Force" in full_live
    assert "API_RESTART_SERVICE_PID_REUSED" in full_live
    assert "WORKER_RESTART_SERVICE_PID_REUSED" in full_live
    assert full_live.count("FULL_LIVE_API_PORT_STILL_LISTENING") == 2
