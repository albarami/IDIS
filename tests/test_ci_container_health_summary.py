"""Execute the container CI summary and preserve its required health gate."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HEALTH_STEP_NAME = "Test container health check"
SUMMARY_STEP_NAME = "Write container build summary"
HEALTH_RUN_SHA256 = "5f0b1fa657cb2c80be6adb31315768b5409379ffd7e3e3bfa4bb0a8ad2d8c25f"
CONTAINER_STEP_NAMES = (
    "Checkout code",
    "Set up Docker Buildx",
    "Build Docker image",
    HEALTH_STEP_NAME,
    "Cleanup",
    "Set up Python 3.11 for release manifest",
    "Generate release manifest",
    "Upload release manifest artifact",
    SUMMARY_STEP_NAME,
)


def _workflow() -> dict[str, Any]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _step(name: str) -> dict[str, Any]:
    return next(
        step for step in _workflow()["jobs"]["container-build"]["steps"] if step["name"] == name
    )


def _required_bash() -> str:
    if os.name == "nt":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
        pytest.fail("Required Git Bash is missing; container summary execution must not skip")
    executable = shutil.which("bash")
    if executable is None:
        pytest.fail("Required Bash is missing; container summary execution must not skip")
    return executable


def _render_summary(tmp_path: Path, outcome: str | None, *, manifest: bool = False) -> str:
    bash = _required_bash()
    summary_path = tmp_path / "summary.md"
    script = _step(SUMMARY_STEP_NAME)["run"].replace("${{ github.sha }}", "a" * 40)
    assert "${{" not in script, "Only the fixed image identity may be rendered into shell code"
    bash_path = Path(bash)
    search_path = [str(bash_path.parent), str(bash_path.parent.parent / "usr/bin"), os.defpath]
    env = {"PATH": os.pathsep.join(search_path), "GITHUB_STEP_SUMMARY": summary_path.as_posix()}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    if outcome is not None:
        env["CONTAINER_HEALTH_OUTCOME"] = outcome
    if manifest:
        (tmp_path / "release_manifest.json").write_text(
            '{"version":"ci-fixture"}\n', encoding="utf-8"
        )
    completed = subprocess.run(  # noqa: S603 - fixed local Bash and checked-in workflow script
        [bash, "--noprofile", "--norc", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return summary_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", "[OK] Passed"),
        ("failure", "[FAIL] Failed"),
        ("cancelled", "[NOT PASSED] Cancelled"),
        ("skipped", "[NOT PASSED] Skipped"),
        ("", "[NOT PASSED] Not run"),
        (None, "[NOT PASSED] Not run"),
        ("unexpected", "[NOT PASSED] Unknown outcome"),
    ],
    ids=("success", "failure", "cancelled", "skipped", "empty", "missing", "unknown"),
)
def test_rendered_health_summary_matches_actual_outcome(
    tmp_path: Path, outcome: str | None, expected: str
) -> None:
    summary = _render_summary(tmp_path, outcome)

    health_lines = [line for line in summary.splitlines() if line.startswith("- **Health Check:**")]
    assert health_lines == [f"- **Health Check:** {expected}"]
    assert "## Container Build Results" in summary
    assert f"- **Image:** idis:ci-{'a' * 40}" in summary


def test_unknown_outcome_is_not_echoed_or_executed_as_shell_code(tmp_path: Path) -> None:
    untrusted = "$(printf injected > summary-injection-marker)"

    summary = _render_summary(tmp_path, untrusted)

    assert "- **Health Check:** [NOT PASSED] Unknown outcome" in summary
    assert untrusted not in summary
    assert not (tmp_path / "summary-injection-marker").exists()


def test_summary_keeps_existing_release_manifest_section(tmp_path: Path) -> None:
    summary = _render_summary(tmp_path, "failure", manifest=True)

    assert "- **Health Check:** [FAIL] Failed" in summary
    assert '### Release Manifest\n```json\n{"version":"ci-fixture"}\n```' in summary


def test_summary_binds_actual_step_outcome_as_environment_data() -> None:
    health = _step(HEALTH_STEP_NAME)
    summary = _step(SUMMARY_STEP_NAME)

    assert health.get("id") == "container_health"
    assert summary["if"] == "always()"
    assert summary.get("env") == {
        "CONTAINER_HEALTH_OUTCOME": "${{ steps.container_health.outcome }}"
    }
    assert "${{ steps." not in summary["run"]


def test_required_health_invocation_and_release_dependencies_are_unchanged() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    container = jobs["container-build"]
    health = _step(HEALTH_STEP_NAME)

    assert tuple(step["name"] for step in container["steps"]) == CONTAINER_STEP_NAMES
    assert set(health) == {"name", "id", "run"}
    assert hashlib.sha256(health["run"].encode("utf-8")).hexdigest() == HEALTH_RUN_SHA256
    assert "continue-on-error" not in container
    assert "if" not in container
    assert set(jobs["release-gate"]["needs"]) == set(jobs) - {"release-gate"}
    assert jobs["release-gate"].get("if") is None
    assert (
        'CMD ["python", "-m", "idis.runtime.strict_launcher", "api", '
        '"--host", "0.0.0.0", "--port", "8000", "--pid-file", "/tmp/idis-api.pid"]'
        in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    )
