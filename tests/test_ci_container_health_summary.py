"""Execute the container CI summary and preserve its required health gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HEALTH_STEP_NAME = "Test container health check"
SUMMARY_STEP_NAME = "Write container build summary"
HEALTH_RUN_SHA256 = "69742b272e4e396925e69edf09db6dd435705fb99a1dda10b829ddf3fd1651d9"
CONTAINER_STEP_NAMES = (
    "Checkout code",
    "Set up Docker Buildx",
    "Build Docker image",
    "Set up Python 3.11 for composition",
    "Install composition interpreter",
    "Reject image env names outside the reviewed allowlist",
    "Reject gate Task4 and unsigned envelopes before any container start",
    "Restore pinned tiny.en model cache",
    "Fetch pinned tiny.en model on cache miss",
    "Verify pinned tiny.en hashes and install the model tree",
    "Save pinned tiny.en model cache",
    "Prepare synthetic profile as root for uid 1000",
    "Mask maintenance secrets and separate that directory",
    "Create the network-none namespace and disposable datastores",
    "Census loopback-only topology before the API start",
    "Enforce authorized migration ceiling before bootstrap",
    "Create migration 0038 erasure roles on the postgres database",
    "Bootstrap the sealed database to the packaged Alembic head",
    "Qualify startup env and start the API",
    HEALTH_STEP_NAME,
    "Record launch audit, topology, and maintenance absence",
    "Cleanup",
    "Set up Python 3.11 for release manifest",
    "Generate release manifest",
    "Upload release manifest artifact",
    SUMMARY_STEP_NAME,
)
_PRESERVED_STEP_NAMES = (
    "Checkout code",
    "Set up Docker Buildx",
    "Build Docker image",
    "Set up Python 3.11 for release manifest",
    "Generate release manifest",
    "Upload release manifest artifact",
    SUMMARY_STEP_NAME,
)
# Digests of f99595ef053883807f58b703252de25a24e91edb via sort_keys canonical JSON.
_F99595EF_JOB_DIGESTS = {
    "backup-restore-drill": "152593732bb006b84ac1c26fe3a178d27f5d200ca69a2a2c7c48f720d87d70d3",
    "check": "ff34e69e6a162d2d823197f6905a9b291afb54f5ff8ebccdf35a57d5a49ce6a8",
    "evaluation-harness": "ea835316a4d6d092d26f4739f66c75f852f396003cf8ce59f8ddb02d0a54eb35",
    "k8s-validate": "5054b069fee639461e357604d3ff5f7003386045b23703dccbaff80f8485ca19",
    "postgres-integration": "832f49f0129dfe12205ec76eecb4b79746016a77b9cab0bcd2982698f9b3583a",
    "release-gate": "95f9816c495581f5b642ec6932d84ba16d75704d5ca1435f77b4406e87f6c8ab",
    "terraform-validate": "1938b5718ff7e1eff243b5a982c453a7745372ebd4145d55d639dd0af74d47a4",
    "ui-check": "cc279df20f4a53d6426db11701765cb386b2d1e36aae57dcbb23829df89ea03b",
}
_F99595EF_PRESERVED_STEP_DIGESTS = {
    "Build Docker image": "93fcabd76b8615c01409ebe68f4f2cc4cc03d4f3c51063ab87e126b8ee5fb364",
    "Checkout code": "1294060e86df1629fd5c6d3c4b93d960f3138c679239be34edb791ac5ceeba9f",
    "Generate release manifest": "d0193f33f03cc03c87230c70bfb6e993caa0e53880c59c4b93a4cef2c6eebf5d",
    "Set up Docker Buildx": "08108c9e973cb74f5a5287655f02f0209b111793f0666486d48f8eda290d0a80",
    "Set up Python 3.11 for release manifest": (
        "f9cb0af25f26bdc4fff106fce03ecfee67b5d2aabb91ce32668d8ec43dd90ab6"
    ),
    "Upload release manifest artifact": (
        "68d45adde72fa195c9e48debd486577e4a562977e42e26d493b42dbffccb03f6"
    ),
    "Write container build summary": (
        "92f570fef7de253b90221c8d276c094b3a17d7e6b11c653ac7c2c93a3a73c625"
    ),
}
_MODEL_REVISION = "0d3d19a32d3338f10357c0889762bd8d64bbdeba"
_MODEL_MANIFEST_SHA256 = "8a9f6ac2c61d339ea4f5a27d00934ea5fe83bcb0d25b67a01cd3b47b9e1adf34"
_MODEL_FINGERPRINT_LINUX = "4b1662df734cdf3854f6a3de0a9fd9c3fdaadfa644d6c7471f5c63c9cb62f6e5"
_MODEL_FINGERPRINT_WINDOWS = "47e043de5fce0c94a30e264cb503f65ccb69cf03b6db296fa14e937a0ab81aea"
_MODEL_FILES = (
    ("README.md", 1323, "1b7816626bf0548e4b71a467f4112b4592924bc818e3117f19aefcc50ce4b6aa"),
    ("config.json", 2317, "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5"),
    ("model.bin", 75537502, "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a"),
    ("tokenizer.json", 2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
    ("vocabulary.txt", 422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
)
_STEP_IF = {
    "setup-composition": "success()",
    "install-composition": "success()",
    "inspect-image-env": "success()",
    "signature-and-gate-negatives": "success()",
    "model-cache": "success()",
    "model-fetch": "success() && steps.model-cache.outputs.cache-hit != 'true'",
    "model-verify": "success()",
    "model-cache-save": "success() && steps.model-cache.outputs.cache-hit != 'true'",
    "prepare-inputs": "success()",
    "mask-maintenance": "success()",
    "disposable-datastores": "success()",
    "topology-before": "success()",
    "migration-ceiling": "success()",
    "erasure-roles": "success()",
    "pg-bootstrap": "success()",
    "start-api": "success()",
    "container_health": "success()",
    "launch-proof": "always() && steps.start-api.outcome != 'skipped'",
    "cleanup": "always()",
}
_REDIS_START_LITERAL = (
    "sh -c 'printf \"bind 127.0.0.1\\nprotected-mode yes\\n"
    'requirepass %s\\n" "$REDIS_PASSWORD" | exec redis-server -\''
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


def _pinned_release_gate() -> tuple[str, str]:
    """Reuse the J-03 release-gate literals so this pin cannot drift from them."""
    path = ROOT / "tests" / "test_pg_bootstrap_ci_required_preview.py"
    spec = importlib.util.spec_from_file_location("cir04j_release_gate_pins", path)
    if spec is None or spec.loader is None:
        raise AssertionError("release-gate pin module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module._RELEASE_GATE_IF, module._required_job_gate_run()


def test_required_health_invocation_and_release_dependencies_are_unchanged() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    container = jobs["container-build"]
    health = _step(HEALTH_STEP_NAME)

    assert tuple(step["name"] for step in container["steps"]) == CONTAINER_STEP_NAMES
    assert set(health) == {"name", "id", "if", "run"}
    assert health["if"] == "success()"
    assert hashlib.sha256(health["run"].encode("utf-8")).hexdigest() == HEALTH_RUN_SHA256
    assert "continue-on-error" not in container
    assert "if" not in container
    assert set(jobs["release-gate"]["needs"]) == set(jobs) - {"release-gate"}
    expression, guard_run = _pinned_release_gate()
    gate = jobs["release-gate"]
    assert gate.get("if") == expression
    guard = gate["steps"][0]
    assert guard["name"] == "Fail unless every required job succeeded"
    assert guard["run"] == guard_run
    assert (
        'CMD ["python", "-m", "idis.runtime.strict_launcher", "api", '
        '"--host", "0.0.0.0", "--port", "8000", "--pid-file", "/tmp/idis-api.pid"]'
        in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    )


def _string_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _string_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_string_keys(item) for item in value]
    return value


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        _string_keys(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _container_steps() -> list[dict[str, Any]]:
    steps = _workflow()["jobs"]["container-build"]["steps"]
    assert isinstance(steps, list)
    return steps


def _by_id(step_id: str) -> dict[str, Any]:
    return next(step for step in _container_steps() if step.get("id") == step_id)


def test_jobs_other_than_container_build_match_f99595ef() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == set(_F99595EF_JOB_DIGESTS) | {"container-build"}
    for name, expected in _F99595EF_JOB_DIGESTS.items():
        assert _canonical_digest(jobs[name]) == expected


def test_preserved_container_steps_match_f99595ef() -> None:
    assert set(_F99595EF_PRESERVED_STEP_DIGESTS) == set(_PRESERVED_STEP_NAMES)
    for name, expected in _F99595EF_PRESERVED_STEP_DIGESTS.items():
        assert _canonical_digest(_step(name)) == expected


def test_container_build_uses_gain_only_the_model_cache() -> None:
    uses = [step["uses"] for step in _container_steps() if "uses" in step]
    assert uses == [
        "actions/checkout@v4",
        "docker/setup-buildx-action@v3",
        "docker/build-push-action@v5",
        "actions/setup-python@v5",
        "actions/cache/restore@v4",
        "actions/cache/save@v4",
        "actions/setup-python@v5",
        "actions/upload-artifact@v4",
    ]


def test_new_steps_pin_fail_closed_if_and_have_no_continue_on_error() -> None:
    steps = _container_steps()
    assert "continue-on-error" not in _workflow()["jobs"]["container-build"]
    for step in steps:
        assert "continue-on-error" not in step
        run = step.get("run") or ""
        assert "|| true" not in run
        if "|" in run:
            assert "PIPESTATUS" in run
    for step_id, expected_if in _STEP_IF.items():
        step = _by_id(step_id)
        assert step["if"] == expected_if
        assert "id" in step
    restore = _by_id("model-cache")
    assert restore["uses"] == "actions/cache/restore@v4"
    assert set(restore) == {"name", "id", "if", "uses", "with"}
    save = _by_id("model-cache-save")
    assert save["uses"] == "actions/cache/save@v4"
    assert save["with"] == restore["with"]
    assert set(save) == {"name", "id", "if", "uses", "with"}
    assert set(_by_id("setup-composition")) == {"name", "id", "if", "uses", "with"}
    assert set(_by_id("cleanup")) == {"name", "id", "if", "run"}
    assert set(_by_id("pg-bootstrap")) == {"name", "id", "if", "env", "run"}
    assert set(_by_id("launch-proof")) == {"name", "id", "if", "env", "run"}


def test_composition_literals_keep_secrets_off_the_command_line() -> None:
    runs = "\n".join(step.get("run") or "" for step in _container_steps())
    assert "IDIS_SERVICE_REGION" not in runs
    assert "--env-file" not in runs
    assert "docker network create" not in runs
    assert "build-arg" not in runs
    prepare = _by_id("prepare-inputs")["run"]
    assert "install -d -m 0700 -o 1000 -g 1000" in prepare
    assert "--owner-uid" in prepare and "1000" in prepare
    datastores = _by_id("disposable-datastores")["run"]
    assert "--network none" in datastores or '"--network", "none"' in datastores
    assert '"-e", "POSTGRES_PASSWORD"' in datastores
    assert '"-e", "REDIS_PASSWORD"' in datastores
    assert "POSTGRES_PASSWORD=" not in datastores
    assert "REDIS_PASSWORD=" not in datastores
    pg_image = (
        "pgvector/pgvector:pg16@sha256:"
        "a97d77306ff47cc1d8add4d000cec2207da2e110746318df3137ce53c337ca30"
    )
    redis_image = (
        "redis:7-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
    )
    assert pg_image in datastores
    assert redis_image in datastores
    mask = _by_id("mask-maintenance")["run"]
    assert "unquote" in mask
    assert "::add-mask::" in mask
    assert ":/src:ro" in _by_id("erasure-roles")["run"]
    assert ":/src:ro" in _by_id("pg-bootstrap")["run"]
    assert "127.0.0.1:5432/postgres" in _by_id("erasure-roles")["run"]
    assert "scripts/check_migration_ceiling.py" in _by_id("migration-ceiling")["run"]
    assert "prepare_ci_erasure_roles.py" in _by_id("erasure-roles")["run"]
    assert "--disposable-ci" in _by_id("erasure-roles")["run"]
    assert "pg_bootstrap_ci.py" in _by_id("pg-bootstrap")["run"]
    assert set(_by_id("pg-bootstrap")["env"]) == {
        "IDIS_PG_HOST",
        "IDIS_PG_PORT",
        "IDIS_PG_DB_NAME",
        "PG_ADMIN_USER",
        "IDIS_PG_APP_USER",
    }
    start = _by_id("start-api")["run"]
    assert "IDIS_STRICT_DOTENV_PATH" in start
    assert "/proc/1/cmdline" not in start
    assert 'entry.get("Path")' in start
    assert 'entry.get("Args")' in start
    assert '--requirepass "$' not in runs
    assert '-a "$' not in runs
    assert _REDIS_START_LITERAL in datastores
    assert "stdin=handle" not in datastores
    assert '"redis-server", "-"' not in datastores
    assert '"-d", "-i"' not in datastores
    assert "REDISCLI_AUTH" in start
    assert "redis-server" in _by_id("disposable-datastores")["run"]
    assert "redis.conf" in _by_id("cleanup")["run"]
    assert "--tmpfs" in start or "tmpfs" in start
    assert "IDIS_OBJECT_STORE_BASE_DIR" in start or "IDIS_OBJECT_STORE_BASE_DIR" in datastores
    for step_id in ("topology-before", "launch-proof"):
        census = _by_id(step_id)["run"]
        assert "/sys/class/net" in census
        assert "/proc/net/route" in census
        assert "/proc/net/ipv6_route" in census
    proof = _by_id("launch-proof")["run"]
    assert "integration.strict_launch.admitted" in proof
    assert "api_launch_rows" in proof
    assert "gate_launch_rows" in proof
    assert "provider_invocation_attempts" in proof
    assert "NOT_EXECUTED" in proof
    assert "container health with synthetic fixtures" in proof
    assert "AUTH-CI-HEALTH-20261017" in proof
    assert '"cmdline_exact": True' not in proof
    assert "api_created" in proof
    assert 'os.environ["IMAGE_ENV_OUTCOME"]' in proof
    assert 'os.environ["API_START_OUTCOME"]' in proof
    assert 'os.environ["CONTAINER_HEALTH_OUTCOME"]' in proof
    health = _by_id("container_health")["run"]
    assert "sleep 10" in health
    assert "prefixes = " not in health
    assert "POPPLER_RUNTIME_UNAVAILABLE" in health
    assert "STRICT_FULL_LIVE_BLOCKED" in health
    assert "[OK] Health check passed" in health
    assert "[FAIL] Health check failed" in health
    assert "api-container.log" in health
    assert "docker logs idis-test" not in health
    ok_at = health.index("[OK] Health check passed")
    fail_at = health.index("[FAIL] Health check failed")
    assert ok_at < fail_at
    assert "exit 0" in health[:fail_at]
    assert "exit 1" in health[fail_at:]
    cleanup = _by_id("cleanup")["run"]
    assert "/opt/idis-ci-health/" in cleanup
    assert "/var/lib/idis-ci-health/" in cleanup
    assert "datastore.env" in cleanup
    verify = _by_id("model-verify")["run"]
    cache = _by_id("model-cache")["with"]
    assert _MODEL_REVISION in cache["key"]
    assert _MODEL_MANIFEST_SHA256 in cache["key"]
    assert _MODEL_REVISION in verify
    assert _MODEL_FINGERPRINT_LINUX in verify
    assert _MODEL_FINGERPRINT_WINDOWS in verify
    for name, size, digest in _MODEL_FILES:
        assert name in verify
        assert str(size) in verify
        assert digest in verify
    proof_and_verify = proof + "\n" + verify
    assert _MODEL_FINGERPRINT_LINUX in proof_and_verify
    for _name, _size, digest in _MODEL_FILES:
        assert digest in proof


_FAKE_DOCKER = r"""
import os
import sys
from pathlib import Path

state = Path(os.environ["DOCKER_FAKE_STATE"])
args = sys.argv[1:]
with (state / "actions.log").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\n")


def _exists(kind: str, name: str) -> bool:
    return (state / kind / name).is_file()


if args[:1] == ["exec"]:
    raise SystemExit(1)
if args[:2] == ["container", "inspect"]:
    raise SystemExit(0 if _exists("containers", args[2]) else 1)
if args[:2] == ["network", "inspect"]:
    raise SystemExit(0 if _exists("networks", args[2]) else 1)
if args[:1] == ["logs"]:
    sys.stdout.write((state / "logs" / args[1]).read_text(encoding="utf-8"))
    raise SystemExit(0)
if args[:2] == ["inspect", "--format"] and "ExitCode" in args[2]:
    sys.stdout.write((state / "exit-codes" / args[3]).read_text(encoding="utf-8"))
    raise SystemExit(0)
if args[:2] == ["rm", "-f"]:
    name = args[2]
    code_path = state / "rm-codes" / name
    code = int(code_path.read_text(encoding="utf-8")) if code_path.is_file() else 0
    stuck = (state / "stuck" / name).is_file()
    if code == 0 and not stuck:
        target = state / "containers" / name
        if target.exists():
            target.unlink()
    raise SystemExit(code)
sys.stderr.write("unexpected docker args\n")
raise SystemExit(97)
"""
_FAKE_SUDO = r"""
#!/usr/bin/env bash
set -euo pipefail
cmd=()
for arg in "$@"; do
  if [ "$arg" = "-n" ] || [ "$arg" = "-E" ]; then
    continue
  fi
  if [ "${#cmd[@]}" -eq 0 ] && [[ "$arg" == *=* ]]; then
    export "$arg"
    continue
  fi
  cmd+=("$arg")
done
exec "${cmd[@]}"
"""
_FAKE_PY = r"""
#!/usr/bin/env bash
if [ -n "${H1B_SHIM:-}" ]; then
  export PYTHONPATH="$H1B_SHIM"
fi
exec "$H1B_PY" "$@"
"""
_WINDOWS_DOCKER_SHIM = r"""
import os
import subprocess
import sys
from pathlib import Path

_Popen = subprocess.Popen


class _DockerPopen(_Popen):
    def __init__(self, args, *pos, **kwargs):
        if isinstance(args, (list, tuple)) and args:
            command = Path(str(args[0])).name.lower()
            if command in {"docker", "docker.exe"}:
                args = [sys.executable, os.environ["H1B_DOCKER_PY"], *list(args)[1:]]
        super().__init__(args, *pos, **kwargs)


subprocess.Popen = _DockerPopen
"""
_LINUX_DOCKER_BYTES = b'#!/bin/sh\nexec "$H1B_PY" "$H1B_DOCKER_PY" "$@"\n'
_HEALTH_LOG = (
    "token_urlsafe_Ab9SECRET\n"
    "STRICT_SECRETTOKEN\n"
    "POPPLER_RUNTIME_UNAVAILABLE\n"
    "POPPLER_RUNTIME_UNAVAILABLE_EXTRA\n"
)
_SECRET_MARKERS = (
    "STRICT_SECRETTOKEN",
    "POPPLER_RUNTIME_UNAVAILABLE_EXTRA",
    "token_urlsafe_Ab9SECRET",
)


_OPT_LITERAL = "/opt/idis-ci-health"
_VAR_LITERAL = "/var/lib/idis-ci-health"
_PARSED_ROOT_REPLACEMENTS = {
    "container_health": {_OPT_LITERAL: 3, _VAR_LITERAL: 0},
    "cleanup": {_OPT_LITERAL: 1, _VAR_LITERAL: 2},
}
_EXECUTABLE_MODE = 0o755


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(path, _EXECUTABLE_MODE)


def _install_fake_toolchain(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_executable(tmp_path / "docker_fake.py", _FAKE_DOCKER)
    docker = bindir / "docker"
    docker.write_bytes(_LINUX_DOCKER_BYTES)
    os.chmod(docker, _EXECUTABLE_MODE)
    _write_executable(bindir / "sudo", _FAKE_SUDO)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_executable(venv_bin / "python", _FAKE_PY)
    if sys.platform == "win32":
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(_WINDOWS_DOCKER_SHIM, encoding="utf-8", newline="\n")
    if sys.platform != "win32":
        for path in (bindir / "docker", bindir / "sudo", venv_bin / "python"):
            assert os.stat(path).st_mode & 0o111


def test_linux_docker_script_bytes_are_posix_lf(tmp_path: Path) -> None:
    _install_fake_toolchain(tmp_path)
    payload = (tmp_path / "bin" / "docker").read_bytes()
    assert payload.startswith(b"#!/bin/sh\n")
    assert b"\r" not in payload
    assert b"H1B_SHIM" not in payload
    if sys.platform != "win32":
        assert not (tmp_path / "shim").exists()


def _sandbox_roots(tmp_path: Path) -> dict[str, Path]:
    return {
        _OPT_LITERAL: tmp_path / "opt-idis-ci-health",
        _VAR_LITERAL: tmp_path / "var-lib-idis-ci-health",
    }


def _rewrite_parsed_roots(script: str, step_id: str, tmp_path: Path) -> str:
    expected = _PARSED_ROOT_REPLACEMENTS[step_id]
    roots = _sandbox_roots(tmp_path)
    rewritten = script
    for literal, count in expected.items():
        found = rewritten.count(literal)
        assert found == count, (step_id, literal, found, count)
        if count == 0:
            assert literal not in rewritten
            continue
        destination = roots[literal].as_posix()
        assert literal not in destination
        rewritten = rewritten.replace(literal, destination, count)
        assert rewritten.count(destination) == count
        assert literal not in rewritten
    return rewritten


def _docker_state(tmp_path: Path) -> Path:
    state = tmp_path / "docker-state"
    for name in ("containers", "logs", "exit-codes", "stuck", "rm-codes", "networks"):
        (state / name).mkdir(parents=True)
    (state / "actions.log").write_text("", encoding="utf-8")
    return state


def _run_parsed_step(
    tmp_path: Path, step_id: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    script = _rewrite_parsed_roots(_by_id(step_id)["run"], step_id, tmp_path)
    assert "${{" not in script
    bash = Path(_required_bash())
    path = os.pathsep.join(
        [str(tmp_path / "bin"), str(bash.parent), str(bash.parent.parent / "usr" / "bin")]
    )
    full_env = {
        "PATH": path,
        "HOME": str(tmp_path),
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "H1B_PY": Path(sys.executable).as_posix(),
        "H1B_DOCKER_PY": (tmp_path / "docker_fake.py").as_posix(),
    }
    if sys.platform == "win32":
        full_env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        full_env["WINDIR"] = os.environ.get("WINDIR", os.environ["SYSTEMROOT"])
        full_env["PATHEXT"] = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        full_env["H1B_SHIM"] = (tmp_path / "shim").as_posix()
    full_env.update(env)
    return subprocess.run(
        [str(bash), "--noprofile", "--norc", "-c", script],
        cwd=tmp_path,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )


def _owned_run(root: Path, run_id: str) -> Path:
    if not run_id.isalnum() or len(run_id) < 8:
        raise AssertionError("run id must stay inside the owned health root")
    path = root / run_id
    if path.parent != root:
        raise AssertionError("run path escaped the owned health root")
    return path


def test_health_step_fails_closed_and_extracts_only_allowlisted_codes(tmp_path: Path) -> None:
    run_id = f"h1b1ehealth{os.getpid()}"
    run_root = _owned_run(_sandbox_roots(tmp_path)[_OPT_LITERAL], run_id)
    state = _docker_state(tmp_path)
    api = f"idis-h1b-api-{run_id}"
    (state / "containers" / api).write_text("present", encoding="utf-8")
    (state / "logs" / api).write_text(_HEALTH_LOG, encoding="utf-8")
    (state / "exit-codes" / api).write_text("7\n", encoding="utf-8")
    _install_fake_toolchain(tmp_path)
    run_root.mkdir(parents=True)
    (run_root / "maintenance").mkdir()
    (run_root / "maintenance" / "api-container.log").write_text(
        _HEALTH_LOG, encoding="utf-8", newline="\n"
    )
    try:
        completed = _run_parsed_step(
            tmp_path,
            "container_health",
            {
                "GITHUB_RUN_ID": run_id,
                "GITHUB_SHA": "a" * 40,
                "GITHUB_WORKSPACE": str(tmp_path),
                "DOCKER_FAKE_STATE": str(state),
            },
        )
        receipt = json.loads((run_root / "api-exit.json").read_text(encoding="utf-8"))
    finally:
        if run_root.exists():
            shutil.rmtree(run_root)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "[FAIL] Health check failed" in completed.stdout
    assert "[OK]" not in output
    assert receipt["code"] == "POPPLER_RUNTIME_UNAVAILABLE"
    assert receipt["exit"] == 7
    assert all(marker not in json.dumps(receipt) for marker in _SECRET_MARKERS)


def test_cleanup_checks_existence_and_fails_when_a_resource_remains(tmp_path: Path) -> None:
    run_id = f"h1b1eclean{os.getpid()}"
    roots = _sandbox_roots(tmp_path)
    secret_root = _owned_run(roots[_OPT_LITERAL], run_id)
    object_root = roots[_VAR_LITERAL]
    object_name = f"h1bobj{os.getpid()}"
    object_parent = _owned_run(object_root, object_name)
    var_run = _owned_run(object_root, run_id)
    state = _docker_state(tmp_path)
    _install_fake_toolchain(tmp_path)
    secret_root.mkdir(parents=True)
    (secret_root / "datastore.env").write_text("REDIS_PASSWORD=secretvalue\n", encoding="utf-8")
    (secret_root / "redis.conf").write_text("requirepass secretvalue\n", encoding="utf-8")
    (secret_root / "object-store-parent").write_text(
        object_parent.as_posix() + "\n",
        encoding="utf-8",
    )
    object_parent.mkdir(parents=True)
    (object_parent / "marker").write_text("object", encoding="utf-8")
    var_run.mkdir(parents=True)
    api = f"idis-h1b-api-{run_id}"
    redis = f"idis-h1b-redis-{run_id}"
    netns = f"idis-h1b-netns-{run_id}"
    for name in (api, redis, netns):
        (state / "containers" / name).write_text("present", encoding="utf-8")
    (state / "stuck" / api).write_text("stuck", encoding="utf-8")
    (state / "rm-codes" / api).write_text("1", encoding="utf-8")
    try:
        completed = _run_parsed_step(
            tmp_path,
            "cleanup",
            {
                "GITHUB_RUN_ID": run_id,
                "GITHUB_SHA": "b" * 40,
                "GITHUB_WORKSPACE": str(tmp_path),
                "DOCKER_FAKE_STATE": str(state),
            },
        )
        actions = (state / "actions.log").read_text(encoding="utf-8")
        output = completed.stdout + completed.stderr
        assert completed.returncode == 1, output
        assert "container remains" in output, output + "\n" + actions
        redis_inspect = actions.index(f"container inspect {redis}")
        redis_rm = actions.index(f"rm -f {redis}")
        assert redis_inspect < redis_rm
        assert f"container inspect idis-h1b-pg-{run_id}" in actions
        assert f"rm -f idis-h1b-pg-{run_id}" not in actions
        assert completed.returncode == 1
        assert "container remains" in output
        assert not secret_root.exists()
        assert not object_parent.exists()
        assert not var_run.exists()
    finally:
        for path in (secret_root, object_parent, var_run):
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
