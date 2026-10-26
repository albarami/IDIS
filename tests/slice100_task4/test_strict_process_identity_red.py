from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "invoke_strict_native_process.ps1"
IDENTITY_MODULE = REPO_ROOT / "scripts" / "strict_process_identity.psm1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(command: str, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:  # pragma: no cover - acceptance environment is Windows
        pytest.skip("PowerShell is required for strict process-identity tests")
    return subprocess.run(  # noqa: S603 - fixed local PowerShell executable
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_process_fixture(
    root: Path,
    *,
    shim_delay_seconds: float = 0.0,
) -> tuple[Path, Path]:
    service = root / "durable_service.py"
    service.write_text(
        "import os\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "index = sys.argv.index('--pid-file') + 1\n"
        "target = Path(sys.argv[index])\n"
        "staged = target.with_suffix(target.suffix + '.staged')\n"
        "staged.write_text(str(os.getpid()) + '\\n', encoding='ascii')\n"
        "os.replace(staged, target)\n"
        "sys.stdout.write('service stdout\\n')\n"
        "sys.stderr.write('service warning\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    shim = root / "launcher_shim.py"
    shim.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "service = sys.argv[1]\n"
        f"time.sleep({shim_delay_seconds!r})\n"
        "child = subprocess.Popen([sys.executable, service, *sys.argv[2:]])\n"  # noqa: S603
        "raise SystemExit(child.wait())\n",
        encoding="utf-8",
    )
    return shim, service


def _launch_expression(
    *,
    name: str,
    shim: Path,
    service: Path,
    pid_file: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> str:
    return (
        f"${name}Launch = & {_quote(WRAPPER)} "
        f"-FilePath {_quote(sys.executable)} "
        f"-ArgumentList @({_quote(shim)}, {_quote(service)}, '--pid-file', {_quote(pid_file)}) "
        f"-WorkingDirectory {_quote(shim.parent)} "
        f"-StandardOutputPath {_quote(stdout_path)} "
        f"-StandardErrorPath {_quote(stderr_path)} -Background; "
        f"${name}Identity = Wait-StrictServiceProcessIdentity "
        f"-LauncherProcessId ${name}Launch.LauncherProcessId "
        f"-ServicePidFilePath {_quote(pid_file)} "
        f"-ExpectedLauncherPath {_quote(sys.executable)}; "
    )


def _last_json_line(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def test_wrapper_and_validator_accept_shim_spawned_service_descendant(tmp_path: Path) -> None:
    shim, service = _write_process_fixture(tmp_path)
    pid_file = tmp_path / "service.pid"
    stdout_path = tmp_path / "service.stdout.log"
    stderr_path = tmp_path / "service.stderr.log"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
        + _launch_expression(
            name="accepted",
            shim=shim,
            service=service,
            pid_file=pid_file,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        + "$serviceAlive = [bool](Get-Process -Id $acceptedIdentity.ServiceProcessId "
        "-ErrorAction SilentlyContinue); "
        "$launcherAlive = [bool](Get-Process -Id $acceptedIdentity.LauncherProcessId "
        "-ErrorAction SilentlyContinue); "
        "$receipt = [ordered]@{ Launch = $acceptedLaunch; Identity = $acceptedIdentity; "
        "ServiceAlive = $serviceAlive; LauncherAlive = $launcherAlive; "
        "PidFileValue = [int](Get-Content -LiteralPath "
        f"{_quote(pid_file)} -Raw) }}; "
        "Stop-Process -Id $acceptedIdentity.ServiceProcessId -Force; "
        "$deadline = [DateTime]::UtcNow.AddSeconds(10); "
        "while ((Get-Process -Id $acceptedIdentity.LauncherProcessId "
        "-ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) "
        "{ Start-Sleep -Milliseconds 100 }; "
        "$receipt | ConvertTo-Json -Depth 8 -Compress"
    )

    completed = _run_powershell(command)

    assert completed.returncode == 0, completed.stderr
    receipt = _last_json_line(completed.stdout)
    launch = receipt["Launch"]
    identity = receipt["Identity"]
    assert isinstance(launch, dict)
    assert isinstance(identity, dict)
    assert set(launch) == {
        "SchemaVersion",
        "Mode",
        "LauncherProcessId",
        "ExitCode",
    }
    assert launch["SchemaVersion"] == 2
    assert launch["Mode"] == "background"
    assert launch["ExitCode"] is None
    assert identity["LauncherProcessId"] == launch["LauncherProcessId"]
    assert set(identity) == {
        "SchemaVersion",
        "LauncherProcessId",
        "ServiceProcessId",
        "LineageDepth",
        "PidRelationship",
        "PidRelationshipValidated",
        "LauncherExecutableValidated",
        "ServiceExecutableValidated",
        "LauncherExecutableSha256",
        "ServiceExecutableSha256",
        "PyvenvConfigSha256",
    }
    assert int(identity["ServiceProcessId"]) != int(identity["LauncherProcessId"])
    assert receipt["PidFileValue"] == identity["ServiceProcessId"]
    assert identity["PidRelationship"] == "launcher_ancestor"
    assert identity["PidRelationshipValidated"] is True
    assert identity["LauncherExecutableValidated"] is True
    assert identity["ServiceExecutableValidated"] is True
    assert int(identity["LineageDepth"]) >= 1
    assert len(str(identity["ServiceExecutableSha256"])) == 64
    assert receipt["ServiceAlive"] is True
    assert receipt["LauncherAlive"] is True
    forbidden = {"ExecutablePath", "CommandLine", "ArgumentList", "Environment"}
    assert forbidden.isdisjoint(identity)
    assert stdout_path.read_text(encoding="utf-8").splitlines() == ["service stdout"]
    assert stderr_path.read_text(encoding="utf-8").splitlines() == ["service warning"]


def test_validator_rejects_same_interpreter_unrelated_live_service(tmp_path: Path) -> None:
    shim, service = _write_process_fixture(tmp_path)
    first_pid = tmp_path / "first.pid"
    second_pid = tmp_path / "second.pid"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
        + _launch_expression(
            name="first",
            shim=shim,
            service=service,
            pid_file=first_pid,
            stdout_path=tmp_path / "first.stdout.log",
            stderr_path=tmp_path / "first.stderr.log",
        )
        + _launch_expression(
            name="second",
            shim=shim,
            service=service,
            pid_file=second_pid,
            stdout_path=tmp_path / "second.stdout.log",
            stderr_path=tmp_path / "second.stderr.log",
        )
        + "$rejected = $null; "
        "try { Get-StrictServiceProcessIdentity "
        "-LauncherProcessId $firstIdentity.LauncherProcessId "
        "-ServiceProcessId $secondIdentity.ServiceProcessId "
        f"-ExpectedLauncherPath {_quote(sys.executable)} | Out-Null }} "
        "catch { $rejected = $_.Exception.Message }; "
        "$unrelatedStillAlive = [bool](Get-Process -Id $secondIdentity.ServiceProcessId "
        "-ErrorAction SilentlyContinue); "
        "$receipt = [ordered]@{ Rejected = $rejected; "
        "UnrelatedStillAlive = $unrelatedStillAlive }; "
        "Stop-Process -Id $firstIdentity.ServiceProcessId -Force -ErrorAction SilentlyContinue; "
        "Stop-Process -Id $secondIdentity.ServiceProcessId -Force -ErrorAction SilentlyContinue; "
        "$cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10); "
        "while (((Get-Process -Id $firstIdentity.LauncherProcessId -ErrorAction SilentlyContinue) "
        "-or (Get-Process -Id $secondIdentity.LauncherProcessId -ErrorAction SilentlyContinue)) "
        "-and [DateTime]::UtcNow -lt $cleanupDeadline) { Start-Sleep -Milliseconds 100 }; "
        "$receipt | ConvertTo-Json -Compress"
    )

    completed = _run_powershell(command)

    assert completed.returncode == 0, completed.stderr
    receipt = _last_json_line(completed.stdout)
    assert receipt["Rejected"] == "IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED"
    assert receipt["UnrelatedStillAlive"] is True


def test_validator_rejects_service_executable_mismatch() -> None:
    executable = Path(sys.executable)
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
        "$module = Get-Module strict_process_identity; "
        "& $module { "
        "$base = [DateTime]::UtcNow.AddMinutes(-5); "
        f"$launcherExe = {_quote(executable)}; "
        "$script:InjectedProcesses = @{ "
        "100 = [pscustomobject]@{ ProcessId=100; ParentProcessId=1; "
        "ExecutablePath=$launcherExe; CreationDate=$base }; "
        "200 = [pscustomobject]@{ ProcessId=200; ParentProcessId=100; "
        "ExecutablePath=$launcherExe; CreationDate=$base.AddSeconds(1) } }; "
        "function Get-StrictCimProcess { param([int] $ProcessId) "
        "return $script:InjectedProcesses[$ProcessId] }; "
        "$rejected = $null; try { Get-StrictServiceProcessIdentity "
        "-LauncherProcessId 100 -ServiceProcessId 200 "
        f"-ExpectedLauncherPath {_quote(executable)} | Out-Null }} "
        "catch { $rejected = $_.Exception.Message }; "
        "[ordered]@{ Rejected = $rejected } | ConvertTo-Json -Compress; }"
    )

    completed = _run_powershell(command)

    assert completed.returncode == 0, completed.stderr
    receipt = _last_json_line(completed.stdout)
    assert receipt["Rejected"] == "IDIS_NATIVE_PROCESS_SERVICE_EXECUTABLE_MISMATCH"


def test_wait_ignores_stale_live_pid_file_until_new_launcher_replaces_it(
    tmp_path: Path,
) -> None:
    shim, service = _write_process_fixture(tmp_path, shim_delay_seconds=1.0)
    pid_file = tmp_path / "stale-live.pid"
    unrelated = subprocess.Popen(  # noqa: S603 - fixed declared interpreter fixture
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        pid_file.write_text(f"{unrelated.pid}\n", encoding="ascii")
        stale_time = time.time() - 3600
        os.utime(pid_file, (stale_time, stale_time))
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
            + _launch_expression(
                name="fresh",
                shim=shim,
                service=service,
                pid_file=pid_file,
                stdout_path=tmp_path / "fresh.stdout.log",
                stderr_path=tmp_path / "fresh.stderr.log",
            )
            + "$receipt = [ordered]@{ ServiceProcessId = $freshIdentity.ServiceProcessId; "
            "PidFileValue = [int](Get-Content -LiteralPath "
            f"{_quote(pid_file)} -Raw); UnrelatedStillAlive = [bool](Get-Process "
            f"-Id {unrelated.pid} -ErrorAction SilentlyContinue) }}; "
            "Stop-Process -Id $freshIdentity.ServiceProcessId -Force; "
            "$cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10); "
            "while ((Get-Process -Id $freshIdentity.LauncherProcessId "
            "-ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $cleanupDeadline) "
            "{ Start-Sleep -Milliseconds 100 }; "
            "$receipt | ConvertTo-Json -Compress"
        )

        completed = _run_powershell(command)

        assert completed.returncode == 0, completed.stderr
        receipt = _last_json_line(completed.stdout)
        assert receipt["ServiceProcessId"] != unrelated.pid
        assert receipt["PidFileValue"] == receipt["ServiceProcessId"]
        assert receipt["UnrelatedStillAlive"] is True
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)


def test_validator_rejects_reused_intermediate_pid_created_after_its_child() -> None:
    # PID reuse cannot be forced safely, so inject only the OS process snapshots while
    # exercising the exported production relationship validator end to end.
    executable = Path(sys.executable)
    service_executable = Path(getattr(sys, "_base_executable", sys.executable))
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
        "$module = Get-Module strict_process_identity; "
        "& $module { "
        "$base = [DateTime]::UtcNow.AddMinutes(-5); "
        f"$launcherExe = {_quote(executable)}; "
        f"$serviceExe = {_quote(service_executable)}; "
        "$script:InjectedProcesses = @{ "
        "100 = [pscustomobject]@{ ProcessId=100; ParentProcessId=1; "
        "ExecutablePath=$launcherExe; CreationDate=$base }; "
        "300 = [pscustomobject]@{ ProcessId=300; ParentProcessId=100; "
        "ExecutablePath=$serviceExe; CreationDate=$base.AddSeconds(30) }; "
        "400 = [pscustomobject]@{ ProcessId=400; ParentProcessId=300; "
        "ExecutablePath=$serviceExe; CreationDate=$base.AddSeconds(20) } }; "
        "function Get-StrictCimProcess { param([int] $ProcessId) "
        "return $script:InjectedProcesses[$ProcessId] }; "
        "$rejected = $null; try { Get-StrictServiceProcessIdentity "
        "-LauncherProcessId 100 -ServiceProcessId 400 "
        f"-ExpectedLauncherPath {_quote(executable)} | Out-Null }} "
        "catch { $rejected = $_.Exception.Message }; "
        "[ordered]@{ Rejected = $rejected } | ConvertTo-Json -Compress; }"
    )

    completed = _run_powershell(command)

    assert completed.returncode == 0, completed.stderr
    receipt = _last_json_line(completed.stdout)
    assert receipt["Rejected"] == "IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED"


def test_service_pid_restart_ignores_dead_stale_pid_and_requires_new_service(
    tmp_path: Path,
) -> None:
    shim, service = _write_process_fixture(tmp_path)
    pid_file = tmp_path / "restart.pid"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"Import-Module {_quote(IDENTITY_MODULE)} -Force; "
        + _launch_expression(
            name="initial",
            shim=shim,
            service=service,
            pid_file=pid_file,
            stdout_path=tmp_path / "initial.stdout.log",
            stderr_path=tmp_path / "initial.stderr.log",
        )
        + "$oldService = $initialIdentity.ServiceProcessId; "
        "$oldLauncher = $initialIdentity.LauncherProcessId; "
        "Stop-Process -Id $oldService -Force; "
        "$deadline = [DateTime]::UtcNow.AddSeconds(10); "
        "while (((Get-Process -Id $oldService -ErrorAction SilentlyContinue) -or "
        "(Get-Process -Id $oldLauncher -ErrorAction SilentlyContinue)) -and "
        "[DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }; "
        "$oldServiceDead = -not [bool](Get-Process -Id $oldService -ErrorAction SilentlyContinue); "
        "$oldLauncherDead = -not [bool](Get-Process -Id $oldLauncher "
        "-ErrorAction SilentlyContinue); "
        + _launch_expression(
            name="restart",
            shim=shim,
            service=service,
            pid_file=pid_file,
            stdout_path=tmp_path / "restart.stdout.log",
            stderr_path=tmp_path / "restart.stderr.log",
        )
        + "$receipt = [ordered]@{ OldServiceDead = $oldServiceDead; "
        "OldLauncherDead = $oldLauncherDead; OldServiceProcessId = $oldService; "
        "RestartServiceProcessId = $restartIdentity.ServiceProcessId; "
        "RestartPidFileValue = [int](Get-Content -LiteralPath "
        f"{_quote(pid_file)} -Raw); OldServiceStillDead = -not [bool](Get-Process "
        "-Id $oldService -ErrorAction SilentlyContinue) }; "
        "Stop-Process -Id $restartIdentity.ServiceProcessId -Force; "
        "$cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10); "
        "while ((Get-Process -Id $restartIdentity.LauncherProcessId "
        "-ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $cleanupDeadline) "
        "{ Start-Sleep -Milliseconds 100 }; "
        "$receipt | ConvertTo-Json -Compress"
    )

    completed = _run_powershell(command)

    assert completed.returncode == 0, completed.stderr
    receipt = _last_json_line(completed.stdout)
    assert receipt["OldServiceDead"] is True
    assert receipt["OldLauncherDead"] is True
    assert receipt["OldServiceStillDead"] is True
    assert receipt["RestartServiceProcessId"] != receipt["OldServiceProcessId"]
    assert receipt["RestartPidFileValue"] == receipt["RestartServiceProcessId"]
