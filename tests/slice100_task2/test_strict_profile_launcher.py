"""Slice100 Task 2 RED tests for the strict profile and launcher.

These tests are deliberately hermetic.  They create an operator-like dotenv in a
temporary directory, never connect to a service, and import the not-yet-written
Task 2 modules only inside test bodies so RED is reported as ordinary failures.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

_TENANT = "10000000-0000-4000-8000-000000000001"
_CANARY_TENANT = "20000000-0000-4000-8000-000000000002"
_OWNER_KEY = "slice100-owner-key-secret"
_CANARY_KEY = "slice100-canary-key-secret"
_ADMIN_KEY = "slice100-governance-key-secret"
_HMAC_KEY = "slice100-profile-hmac-secret-at-least-32-bytes"


def _make_integrity_file_writable(path: Path) -> None:
    if os.name == "nt":
        path.chmod(stat.S_IWRITE)
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _seal_integrity_file(path: Path) -> None:
    from idis.runtime.secure_files import protect_path_for_current_user

    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    if os.name == "nt":
        path.chmod(stat.S_IREAD)
    else:
        path.chmod(stat.S_IRUSR)


def _rewrite_integrity_file(path: Path, data: str | bytes) -> None:
    _make_integrity_file_writable(path)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    _seal_integrity_file(path)


def _pid_race_contender(
    path: str,
    pid: int,
    start: Any,
    release: Any,
    results: Any,
) -> None:
    from idis.runtime.strict_launcher import (
        StrictLauncherError,
        release_pid_file,
        write_pid_file,
    )

    start.wait(10)
    try:
        write_pid_file(path, pid=pid, is_process_alive=lambda _pid: False)
    except StrictLauncherError as exc:
        results.put(("blocked", exc.code, pid))
        return
    results.put(("owned", None, pid))
    release.wait(10)
    release_pid_file(path)


def _pid_owner_with_late_cleanup(
    path: str,
    owned: Any,
    first_release: Any,
    released: Any,
    late_release: Any,
    late_done: Any,
) -> None:
    from idis.runtime.strict_launcher import release_pid_file, write_pid_file

    write_pid_file(path, pid=11111, is_process_alive=lambda _pid: False)
    owned.set()
    first_release.wait(10)
    release_pid_file(path)
    released.set()
    late_release.wait(10)
    release_pid_file(path)
    late_done.set()


def _profile_module() -> Any:
    return importlib.import_module("idis.runtime.profile")


def _launcher_module() -> Any:
    return importlib.import_module("idis.runtime.strict_launcher")


def _registry() -> str:
    return json.dumps(
        {
            _OWNER_KEY: {
                "tenant_id": _TENANT,
                "actor_id": "slice100-acceptance-service",
                "name": "Slice100 Acceptance Service",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["INTEGRATION_SERVICE"],
            },
            _CANARY_KEY: {
                "tenant_id": _CANARY_TENANT,
                "actor_id": "slice100-isolation-canary",
                "name": "Slice100 Isolation Canary",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["INTEGRATION_SERVICE"],
            },
            _ADMIN_KEY: {
                "tenant_id": _TENANT,
                "actor_id": "slice100-governance-admin",
                "name": "Slice100 Governance Admin",
                "timezone": "Asia/Qatar",
                "data_region": "me-south-1",
                "roles": ["ADMIN"],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _profile_values(
    tmp_path: Path,
    *,
    generation: str = "acceptance-v1",
    promoted: str = "1",
) -> dict[str, str]:
    from idis.runtime.secure_files import protect_path_for_current_user

    tmp_path.mkdir(parents=True, exist_ok=True)
    protect_path_for_current_user(tmp_path, directory=True)
    object_root = tmp_path / "persistent-object-store"
    object_root.mkdir(exist_ok=True)
    media_model = tmp_path / "fictional-media-model"
    media_model.mkdir(exist_ok=True)
    (media_model / "config.json").write_text("{}", encoding="utf-8")
    (media_model / "model.bin").write_bytes(b"fictional-model")
    public_key = tmp_path / "governance-public.pem"
    public_key.write_text("non-secret-test-public-key", encoding="utf-8")
    rights = tmp_path / "rights-envelope.json"
    rights.write_text("{}", encoding="utf-8")
    budget = tmp_path / "budget-envelope.json"
    budget.write_text("{}", encoding="utf-8")
    fdd_methodology = tmp_path / "fictional-financial-dd.xlsx"
    fdd_methodology.write_bytes(b"fictional-task5-workbook")
    for integrity_file in (public_key, rights, budget):
        _seal_integrity_file(integrity_file)

    return {
        "IDIS_REQUIRE_FULL_LIVE": "1",
        "IDIS_RUNTIME_PROFILE_HMAC_KEY": _HMAC_KEY,
        "IDIS_RUNTIME_PROFILE_GENERATION": generation,
        "IDIS_REQUIRE_PROMOTED_PROMPTS": promoted,
        "IDIS_FDD_METHODOLOGY_PATH": str(fdd_methodology.resolve()),
        "IDIS_INGEST_MAX_BYTES": "536870912",
        "IDIS_OCR_ENABLED": "1",
        "IDIS_OCR_ADAPTER": "tesseract",
        "IDIS_OCR_LANGUAGE": "eng",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_OCR_DPI": "200",
        "IDIS_MEDIA_ADAPTER": "faster-whisper",
        "IDIS_MEDIA_STT_MODEL_PATH": str(media_model.resolve()),
        "IDIS_MEDIA_STT_ALLOW_DOWNLOAD": "false",
        "IDIS_MEDIA_TIMEOUT_SECONDS": "3600",
        "IDIS_MEDIA_LANGUAGE": "en",
        "IDIS_MEDIA_COMPUTE_TYPE": "int8",
        "IDIS_MEDIA_MAX_DURATION_SECONDS": "7200",
        "IDIS_DATABASE_URL": "postgresql+psycopg2://app:secret@db/idis",
        "IDIS_DATABASE_ADMIN_URL": "postgresql+psycopg2://admin:secret@db/idis",
        "IDIS_ERASURE_DATABASE_URL": ("postgresql+psycopg2://idis_erasure_executor:secret@db/idis"),
        "IDIS_IDEMPOTENCY_TTL_DAYS": "30",
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": str(object_root.resolve()),
        "IDIS_REDIS_URL": "redis://redis.internal:6379/0",
        "IDIS_RATE_LIMIT_USER_RPM": "600",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM": "1200",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "2",
        "IDIS_API_KEYS_JSON": _registry(),
        "IDIS_REAL_EXAMPLE_API_KEY": _OWNER_KEY,
        "IDIS_ISOLATION_CANARY_API_KEY": _CANARY_KEY,
        "IDIS_GOVERNANCE_ADMIN_API_KEY": _ADMIN_KEY,
        "IDIS_ACCEPTANCE_TENANT_ID": _TENANT,
        "IDIS_ISOLATION_CANARY_TENANT_ID": _CANARY_TENANT,
        "IDIS_SERVICE_REGION": "me-south-1",
        "IDIS_UI_DEV_AUTH_BYPASS": "false",
        "IDIS_WORKER_TENANT_IDS": _TENANT,
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "10",
        "IDIS_PROVIDER_BUDGET_MAX_CALLS": "500",
        "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(budget.resolve()),
        "IDIS_PROVIDER_BUDGET_CURRENCY": "USD",
        "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH": str(rights.resolve()),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": "slice100-governance-v1",
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_key.resolve()),
        "IDIS_ACCEPTANCE_API_BASE_URL": "http://127.0.0.1:8000",
        "NEO4J_URI": "neo4j+s://graph.internal",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "neo4j-secret",
        "NEO4J_DATABASE": "idis-acceptance",
    }


def _write_dotenv(
    tmp_path: Path,
    *,
    generation: str = "acceptance-v1",
    promoted: str = "1",
    additions: Mapping[str, str] | None = None,
) -> tuple[Path, dict[str, str]]:
    values = _profile_values(tmp_path, generation=generation, promoted=promoted)
    values.update(dict(additions or {}))
    path = tmp_path / f"strict-{generation}.env"
    path.write_text(
        "\n".join(f"{name}={value}" for name, value in sorted(values.items())) + "\n",
        encoding="utf-8",
    )
    _seal_integrity_file(path)
    return path, values


def _process_env(dotenv_path: Path, **overrides: str) -> dict[str, str]:
    return {"IDIS_STRICT_DOTENV_PATH": str(dotenv_path.resolve()), **overrides}


def _assert_profile_error(exc: BaseException, code: str) -> None:
    assert getattr(exc, "code", None) == code
    assert code in str(exc)


def test_profile_and_launcher_import_no_runtime_components_in_fresh_interpreter() -> None:
    """The importable bootstrap surface must remain safe before dotenv has been loaded."""
    repo_root = Path(__file__).resolve().parents[2]
    script = """
import sys
import idis.runtime.profile
import idis.runtime.strict_launcher
blocked = (
    "idis.api", "idis.pipeline", "idis.persistence", "idis.services",
    "alembic", "uvicorn", "neo4j", "redis", "sqlalchemy",
)
loaded = sorted(name for name in sys.modules if name.startswith(blocked))
if loaded:
    raise SystemExit("runtime-imported-before-profile:" + ",".join(loaded))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    result = subprocess.run(  # noqa: S603 - fixed interpreter and literal test program
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_loads_dotenv_applies_process_override_and_tracks_sources(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    process_env = _process_env(
        dotenv_path,
        IDIS_DATABASE_URL="postgresql+psycopg2://override:secret@db/idis",
    )

    profile = module.load_strict_runtime_profile(command="api", process_env=process_env)

    assert profile.values["IDIS_DATABASE_URL"].startswith("postgresql+psycopg2://override:")
    assert process_env["IDIS_REDIS_URL"] == "redis://redis.internal:6379/0"
    assert process_env["IDIS_PROCESS_ROLE"] == "api"
    assert profile.source_for("IDIS_DATABASE_URL") == "process"
    assert profile.source_for("IDIS_REDIS_URL") == "dotenv"


@pytest.mark.parametrize("command", ["api", "worker", "gate", "governance"])
def test_admin_database_dsn_is_not_required_or_propagated_outside_migration(
    tmp_path: Path,
    command: str,
) -> None:
    module = _profile_module()
    dotenv_path, values = _write_dotenv(tmp_path)
    values.pop("IDIS_DATABASE_ADMIN_URL")
    _rewrite_integrity_file(
        dotenv_path,
        "\n".join(f"{name}={value}" for name, value in sorted(values.items())) + "\n",
    )
    process_env = _process_env(dotenv_path)

    profile = module.load_strict_runtime_profile(command=command, process_env=process_env)

    assert "IDIS_DATABASE_ADMIN_URL" not in profile.values
    assert "IDIS_DATABASE_ADMIN_URL" not in process_env


def test_admin_database_dsn_is_required_and_propagated_for_migration(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, values = _write_dotenv(tmp_path)
    process_env = _process_env(dotenv_path)

    profile = module.load_strict_runtime_profile(command="migrate", process_env=process_env)

    assert profile.values["IDIS_DATABASE_ADMIN_URL"] == values["IDIS_DATABASE_ADMIN_URL"]
    assert process_env["IDIS_DATABASE_ADMIN_URL"] == values["IDIS_DATABASE_ADMIN_URL"]

    values.pop("IDIS_DATABASE_ADMIN_URL")
    _rewrite_integrity_file(
        dotenv_path,
        "\n".join(f"{name}={value}" for name, value in sorted(values.items())) + "\n",
    )
    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="migrate",
            process_env=_process_env(dotenv_path),
        )
    _assert_profile_error(exc_info.value, "STRICT_PROFILE_LOAD_FAILED")


def test_launcher_loads_and_applies_profile_before_dynamic_runtime_import(tmp_path: Path) -> None:
    launcher = _launcher_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    process_env = _process_env(dotenv_path)
    observed: list[str] = []
    sentinel = object()

    def importer(module_name: str) -> object:
        assert process_env["IDIS_REQUIRE_FULL_LIVE"] == "1"
        assert process_env["IDIS_DATABASE_URL"].startswith("postgresql+psycopg2://app:")
        assert process_env["IDIS_PROCESS_ROLE"] == "api"
        observed.append(module_name)
        return sentinel

    profile, imported = launcher.load_profile_then_import_runtime(
        command="api",
        process_env=process_env,
        importer=importer,
    )

    assert profile.command == "api"
    assert imported is sentinel
    assert observed == ["idis.api.main"]


@pytest.mark.parametrize(
    ("path_factory", "expected_code"),
    [
        (lambda root: root / "missing.env", "STRICT_PROFILE_LOAD_FAILED"),
        (lambda root: root, "STRICT_PROFILE_LOAD_FAILED"),
    ],
)
def test_profile_fails_closed_when_dotenv_is_not_loaded(
    tmp_path: Path,
    path_factory: Any,
    expected_code: str,
) -> None:
    module = _profile_module()
    process_env = _process_env(path_factory(tmp_path))

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(command="gate", process_env=process_env)

    _assert_profile_error(exc_info.value, expected_code)


def test_profile_rejects_malformed_dotenv_instead_of_using_partial_values(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    _rewrite_integrity_file(
        dotenv_path,
        dotenv_path.read_text(encoding="utf-8") + "this line has no equals sign\n",
    )

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="gate",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROFILE_LOAD_FAILED")


def test_profile_rejects_writable_dotenv_bootstrap_material(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    _make_integrity_file_writable(dotenv_path)

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="gate",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROFILE_LOAD_FAILED")


@pytest.mark.skipif(os.name == "nt", reason="POSIX secret mode contract")
def test_profile_rejects_group_or_world_readable_secret_dotenv(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    dotenv_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="gate",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROFILE_LOAD_FAILED")


def test_profile_rejects_replaced_dotenv_bootstrap_material(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    replacement = tmp_path / "replacement.env"
    replacement.write_bytes(dotenv_path.read_bytes())
    _make_integrity_file_writable(dotenv_path)
    os.replace(replacement, dotenv_path)

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="gate",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROFILE_LOAD_FAILED")


def test_profile_values_are_immutable_after_validation(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)
    profile = module.load_strict_runtime_profile(
        command="api",
        process_env=_process_env(dotenv_path),
    )

    with pytest.raises(TypeError):
        profile.values["IDIS_DATABASE_URL"] = "postgresql://tampered"  # type: ignore[index]


def test_base_profile_hmac_excludes_command_and_launcher_injected_role(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)

    api_env = _process_env(dotenv_path)
    worker_env = _process_env(dotenv_path)
    gate_env = _process_env(dotenv_path)
    api = module.load_strict_runtime_profile(command="api", process_env=api_env)
    worker = module.load_strict_runtime_profile(command="worker", process_env=worker_env)
    gate = module.load_strict_runtime_profile(command="gate", process_env=gate_env)

    assert api.base_profile_hmac == worker.base_profile_hmac == gate.base_profile_hmac
    assert api.process_role == "api"
    assert worker.process_role == "worker"
    assert gate.process_role is None
    assert "IDIS_PROCESS_ROLE" not in gate_env
    assert len(api.base_profile_hmac) == 64
    int(api.base_profile_hmac, 16)


def test_base_profile_hmac_changes_with_required_config_but_not_unrelated_ambient_env(
    tmp_path: Path,
) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path)

    baseline = module.load_strict_runtime_profile(
        command="gate",
        process_env=_process_env(dotenv_path, PYTHONHASHSEED="1"),
    )
    unrelated = module.load_strict_runtime_profile(
        command="gate",
        process_env=_process_env(dotenv_path, PYTHONHASHSEED="999"),
    )
    changed = module.load_strict_runtime_profile(
        command="gate",
        process_env=_process_env(dotenv_path, IDIS_REDIS_URL="redis://other.internal:6379/0"),
    )

    assert baseline.base_profile_hmac == unrelated.base_profile_hmac
    assert baseline.base_profile_hmac != changed.base_profile_hmac


def test_profile_hmac_binds_actual_governance_public_key_bytes(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, values = _write_dotenv(tmp_path)
    public_key = Path(values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    original_bytes = public_key.read_bytes()

    original = module.load_strict_runtime_profile(
        command="governance",
        process_env=_process_env(dotenv_path),
    )
    _rewrite_integrity_file(public_key, b"different-governance-public-key")
    changed = module.load_strict_runtime_profile(
        command="governance",
        process_env=_process_env(dotenv_path),
    )

    assert original.governance_public_key_sha256 == hashlib.sha256(original_bytes).hexdigest()
    assert (
        changed.governance_public_key_sha256
        == hashlib.sha256(b"different-governance-public-key").hexdigest()
    )
    assert original.base_profile_hmac != changed.base_profile_hmac


def test_profile_generation_is_bound_into_hmac_and_bootstrap_cannot_start_runtime_roles(
    tmp_path: Path,
) -> None:
    module = _profile_module()
    acceptance_path, _ = _write_dotenv(tmp_path / "acceptance")
    bootstrap_path, _ = _write_dotenv(
        tmp_path / "bootstrap",
        generation="bootstrap-v1",
        promoted="0",
    )

    acceptance = module.load_strict_runtime_profile(
        command="gate",
        process_env=_process_env(acceptance_path),
    )
    bootstrap = module.load_strict_runtime_profile(
        command="gate",
        process_env=_process_env(bootstrap_path),
    )
    assert acceptance.base_profile_hmac != bootstrap.base_profile_hmac

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="worker",
            process_env=_process_env(bootstrap_path),
        )
    _assert_profile_error(exc_info.value, "STRICT_PROFILE_GENERATION_INVALID")


def test_acceptance_runtime_requires_promoted_prompts(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(tmp_path, promoted="0")

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="api",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROFILE_GENERATION_INVALID")


def test_dotenv_cannot_choose_or_override_the_process_role(tmp_path: Path) -> None:
    module = _profile_module()
    dotenv_path, _ = _write_dotenv(
        tmp_path,
        additions={"IDIS_PROCESS_ROLE": "worker"},
    )

    with pytest.raises(module.StrictProfileError) as exc_info:
        module.load_strict_runtime_profile(
            command="api",
            process_env=_process_env(dotenv_path),
        )

    _assert_profile_error(exc_info.value, "STRICT_PROCESS_ROLE_INVALID")


def test_safe_profile_projection_and_repr_never_expose_values_or_loader_path(
    tmp_path: Path,
) -> None:
    module = _profile_module()
    dotenv_path, values = _write_dotenv(tmp_path)
    profile = module.load_strict_runtime_profile(
        command="api",
        process_env=_process_env(dotenv_path),
    )

    rendered = repr(profile) + json.dumps(profile.safe_summary(), sort_keys=True)
    sensitive_values = {
        values["IDIS_RUNTIME_PROFILE_HMAC_KEY"],
        values["IDIS_DATABASE_URL"],
        values["IDIS_DATABASE_ADMIN_URL"],
        values["IDIS_ERASURE_DATABASE_URL"],
        values["IDIS_REDIS_URL"],
        values["NEO4J_PASSWORD"],
        values["IDIS_REAL_EXAMPLE_API_KEY"],
        values["IDIS_ISOLATION_CANARY_API_KEY"],
        values["IDIS_GOVERNANCE_ADMIN_API_KEY"],
        values["IDIS_OBJECT_STORE_BASE_DIR"],
        str(dotenv_path.resolve()),
    }
    assert all(secret not in rendered for secret in sensitive_values)
    assert profile.base_profile_hmac in rendered


def test_launcher_parser_exposes_only_the_five_planned_commands() -> None:
    launcher = _launcher_module()
    parser = launcher.build_parser()
    choices = parser._subparsers._group_actions[0].choices  # noqa: SLF001

    assert set(choices) == {"migrate", "governance", "gate", "api", "worker"}


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["migrate", "--alembic-config", "alembic.ini", "--upgrade", "head"],
            {"command": "migrate", "alembic_config": "alembic.ini", "upgrade": "head"},
        ),
        (
            [
                "governance",
                "--admin-api-key-env",
                "IDIS_GOVERNANCE_ADMIN_API_KEY",
                "--trusted-signer-id-env",
                "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID",
                "--trusted-ed25519-key-env",
                "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH",
                "--import-rights-approvals-env",
                "IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH",
                "--import-budget-policy-env",
                "IDIS_PROVIDER_BUDGET_POLICY_PATH",
                "--no-secret-output",
            ],
            {"command": "governance", "no_secret_output": True},
        ),
        (
            ["api", "--host", "127.0.0.1", "--port", "8000", "--pid-file", "api.pid"],
            {"command": "api", "host": "127.0.0.1", "port": 8000, "pid_file": "api.pid"},
        ),
        (
            ["worker", "--watch", "--poll-seconds", "2", "--pid-file", "worker.pid"],
            {"command": "worker", "watch": True, "poll_seconds": 2.0, "pid_file": "worker.pid"},
        ),
        (
            ["gate", "--safe-report-dir", "reports", "--live-preflight-only"],
            {"command": "gate", "safe_report_dir": "reports", "live_preflight_only": True},
        ),
    ],
)
def test_launcher_parser_accepts_the_plan_commands_and_exact_core_flags(
    argv: list[str],
    expected: dict[str, Any],
) -> None:
    launcher = _launcher_module()

    parsed = vars(launcher.build_parser().parse_args(argv))

    assert expected.items() <= parsed.items()


@pytest.mark.parametrize(
    ("command", "module_name"),
    [
        ("migrate", "alembic.command"),
        ("governance", "idis.runtime.governance_import"),
        ("gate", "idis.evaluation.real_example_gate"),
        ("api", "idis.api.main"),
        ("worker", "idis.pipeline.worker"),
    ],
)
def test_launcher_dynamically_imports_each_target_only_after_profile_apply(
    tmp_path: Path,
    command: str,
    module_name: str,
) -> None:
    launcher = _launcher_module()
    generation = "acceptance-v1" if command in {"api", "worker"} else "bootstrap-v1"
    dotenv_path, _ = _write_dotenv(
        tmp_path,
        generation=generation,
        promoted="1" if generation == "acceptance-v1" else "0",
    )
    process_env = _process_env(dotenv_path)
    imported: list[str] = []

    def importer(name: str) -> object:
        assert process_env["IDIS_DATABASE_URL"].startswith("postgresql+psycopg2://app:")
        imported.append(name)
        return object()

    launcher.load_profile_then_import_runtime(
        command=command,
        process_env=process_env,
        importer=importer,
    )

    assert imported == [module_name]


def test_pid_file_is_atomic_and_refuses_to_replace_a_live_process(tmp_path: Path) -> None:
    launcher = _launcher_module()
    pid_path = tmp_path / "runtime.pid"
    replacements: list[tuple[Path, Path]] = []

    launcher.write_pid_file(
        pid_path,
        pid=12345,
        is_process_alive=lambda _pid: False,
        replace=lambda source, target: (
            replacements.append((source, target)),
            source.replace(target),
        ),
    )

    assert pid_path.read_text(encoding="ascii") == "12345\n"
    assert len(replacements) == 1
    assert replacements[0][0] != pid_path
    assert replacements[0][1] == pid_path

    with pytest.raises(launcher.StrictLauncherError, match="PID_FILE_PROCESS_ALIVE"):
        launcher.write_pid_file(
            pid_path,
            pid=67890,
            is_process_alive=lambda pid: pid == 12345,
        )
    assert pid_path.read_text(encoding="ascii") == "12345\n"
    launcher.release_pid_file(pid_path)


def test_pid_file_replaces_matching_current_pid_after_container_restart(
    tmp_path: Path,
) -> None:
    launcher = _launcher_module()
    pid_path = tmp_path / "runtime.pid"
    pid_path.write_text("1\n", encoding="ascii")

    # PID namespaces commonly reuse PID 1 after a container restart.  A matching selected PID
    # is this launcher reclaiming its own configured file, not evidence of another live owner.
    launcher.write_pid_file(
        pid_path,
        pid=1,
        is_process_alive=lambda _pid: True,
    )

    assert pid_path.read_text(encoding="ascii") == "1\n"
    launcher.release_pid_file(pid_path)


def test_pid_file_holds_exclusive_process_lock_until_release(tmp_path: Path) -> None:
    launcher = _launcher_module()
    pid_path = tmp_path / "runtime.pid"

    launcher.write_pid_file(
        pid_path,
        pid=12345,
        is_process_alive=lambda _pid: False,
    )

    with pytest.raises(launcher.StrictLauncherError, match="PID_FILE_PROCESS_ALIVE"):
        launcher._acquire_pid_file_lock(pid_path)

    launcher.release_pid_file(pid_path)
    assert not pid_path.exists()

    lock = launcher._acquire_pid_file_lock(pid_path)
    launcher._release_pid_file_lock(lock)


def test_pid_file_two_process_barrier_has_exactly_one_owner(tmp_path: Path) -> None:
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    pid_path = tmp_path / "runtime.pid"
    start = context.Event()
    release = context.Event()
    results = context.Queue()
    contenders = [
        context.Process(
            target=_pid_race_contender,
            args=(str(pid_path), pid, start, release, results),
        )
        for pid in (11111, 22222)
    ]
    try:
        for contender in contenders:
            contender.start()
        start.set()
        outcomes = [results.get(timeout=15), results.get(timeout=15)]

        assert sorted(outcome[0] for outcome in outcomes) == ["blocked", "owned"]
        blocked = next(outcome for outcome in outcomes if outcome[0] == "blocked")
        owner = next(outcome for outcome in outcomes if outcome[0] == "owned")
        assert blocked[1] == "PID_FILE_PROCESS_ALIVE"
        assert pid_path.read_text(encoding="ascii") == f"{owner[2]}\n"
    finally:
        release.set()
        for contender in contenders:
            contender.join(timeout=15)
            if contender.is_alive():
                contender.terminate()
                contender.join(timeout=5)


def test_prior_owner_late_cleanup_cannot_remove_successor_pid(tmp_path: Path) -> None:
    import multiprocessing

    launcher = _launcher_module()
    context = multiprocessing.get_context("spawn")
    pid_path = tmp_path / "runtime.pid"
    owned = context.Event()
    first_release = context.Event()
    released = context.Event()
    late_release = context.Event()
    late_done = context.Event()
    prior_owner = context.Process(
        target=_pid_owner_with_late_cleanup,
        args=(
            str(pid_path),
            owned,
            first_release,
            released,
            late_release,
            late_done,
        ),
    )
    prior_owner.start()
    try:
        assert owned.wait(15)
        first_release.set()
        assert released.wait(15)
        launcher.write_pid_file(
            pid_path,
            pid=22222,
            is_process_alive=lambda _pid: False,
        )

        late_release.set()
        assert late_done.wait(15)
        assert pid_path.read_text(encoding="ascii") == "22222\n"
    finally:
        first_release.set()
        late_release.set()
        launcher.release_pid_file(pid_path)
        prior_owner.join(timeout=15)
        if prior_owner.is_alive():
            prior_owner.terminate()
            prior_owner.join(timeout=5)


def test_launcher_audit_projection_separates_command_from_base_profile_hmac(
    tmp_path: Path,
) -> None:
    launcher = _launcher_module()
    profile_module = _profile_module()
    dotenv_path, values = _write_dotenv(tmp_path)
    profile = profile_module.load_strict_runtime_profile(
        command="api",
        process_env=_process_env(dotenv_path),
    )

    fields = launcher.launch_audit_fields(profile)

    assert fields == {
        "base_profile_hmac": profile.base_profile_hmac,
        "command": "api",
        "generation": "acceptance-v1",
        "process_role": "api",
    }
    rendered = json.dumps(fields, sort_keys=True)
    for name in (
        "IDIS_RUNTIME_PROFILE_HMAC_KEY",
        "IDIS_DATABASE_URL",
        "IDIS_DATABASE_ADMIN_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_REDIS_URL",
        "NEO4J_PASSWORD",
        "IDIS_REAL_EXAMPLE_API_KEY",
        "IDIS_ISOLATION_CANARY_API_KEY",
        "IDIS_GOVERNANCE_ADMIN_API_KEY",
    ):
        assert values[name] not in rendered


def test_production_container_default_enters_the_strict_api_launcher() -> None:
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert (
        'CMD ["python", "-m", "idis.runtime.strict_launcher", "api", '
        '"--host", "0.0.0.0", "--port", "8000", "--pid-file", "/tmp/idis-api.pid"]' in content
    )
    assert 'CMD ["uvicorn", "idis.api.main:create_app"' not in content
