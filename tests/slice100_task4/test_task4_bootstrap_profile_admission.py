"""RED contracts for the narrowly scoped Task 4 bootstrap API/gate path."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_TASK4_INGESTION_SCOPE = "task4-ingestion-v1"


def _profile_helpers() -> tuple[Any, Any, Any]:
    from tests.slice100_task2.test_strict_profile_launcher import (
        _assert_profile_error,
        _process_env,
        _write_dotenv,
    )

    return _write_dotenv, _process_env, _assert_profile_error


@pytest.mark.parametrize("command", ["api", "gate"])
def test_bootstrap_unpromoted_api_and_gate_are_admitted_only_for_task4_ingestion(
    tmp_path: Path,
    command: str,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    write_dotenv, process_env, _assert_profile_error = _profile_helpers()
    dotenv_path, _values = write_dotenv(
        tmp_path / command,
        generation="bootstrap-v1",
        promoted="0",
    )

    profile = profile_module.load_strict_runtime_profile(
        command=command,
        process_env=process_env(dotenv_path),
    )

    assert profile.generation == "bootstrap-v1"
    assert profile.values["IDIS_REQUIRE_PROMOTED_PROMPTS"] == "0"
    assert profile.process_role == ("api" if command == "api" else None)
    assert profile_module.require_runtime_profile(profile, command=command) is profile

    if command == "gate":
        gate = importlib.import_module("idis.evaluation.real_example_gate")
        assert (
            gate._require_task4_gate_profile(profile)
            == hashlib.sha256(  # noqa: SLF001
                profile.base_profile_hmac.encode("ascii")
            ).hexdigest()
        )


@pytest.mark.parametrize("command", ["api", "gate"])
def test_bootstrap_profile_rejects_promoted_prompt_mode(
    tmp_path: Path,
    command: str,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    write_dotenv, process_env, assert_profile_error = _profile_helpers()
    dotenv_path, _values = write_dotenv(
        tmp_path / command,
        generation="bootstrap-v1",
        promoted="1",
    )

    with pytest.raises(profile_module.StrictProfileError) as exc_info:
        profile_module.load_strict_runtime_profile(
            command=command,
            process_env=process_env(dotenv_path),
        )

    assert_profile_error(exc_info.value, "STRICT_PROFILE_GENERATION_INVALID")


@pytest.mark.parametrize("command", ["api", "gate"])
def test_acceptance_profile_still_rejects_unpromoted_prompt_mode(
    tmp_path: Path,
    command: str,
) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    write_dotenv, process_env, assert_profile_error = _profile_helpers()
    dotenv_path, _values = write_dotenv(
        tmp_path / command,
        generation="acceptance-v1",
        promoted="0",
    )

    with pytest.raises(profile_module.StrictProfileError) as exc_info:
        profile_module.load_strict_runtime_profile(
            command=command,
            process_env=process_env(dotenv_path),
        )

    assert_profile_error(exc_info.value, "STRICT_PROFILE_GENERATION_INVALID")


def test_bootstrap_worker_remains_forbidden(tmp_path: Path) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    write_dotenv, process_env, assert_profile_error = _profile_helpers()
    dotenv_path, _values = write_dotenv(
        tmp_path,
        generation="bootstrap-v1",
        promoted="0",
    )

    with pytest.raises(profile_module.StrictProfileError) as exc_info:
        profile_module.load_strict_runtime_profile(
            command="worker",
            process_env=process_env(dotenv_path),
        )

    assert_profile_error(exc_info.value, "STRICT_PROFILE_GENERATION_INVALID")


def test_acceptance_promoted_api_semantics_are_unchanged(tmp_path: Path) -> None:
    profile_module = importlib.import_module("idis.runtime.profile")
    write_dotenv, process_env, _assert_profile_error = _profile_helpers()
    dotenv_path, _values = write_dotenv(
        tmp_path,
        generation="acceptance-v1",
        promoted="1",
    )

    profile = profile_module.load_strict_runtime_profile(
        command="api",
        process_env=process_env(dotenv_path),
    )

    assert profile.generation == "acceptance-v1"
    assert profile.process_role == "api"
    assert profile.values["IDIS_REQUIRE_PROMOTED_PROMPTS"] == "1"
    assert profile_module.require_runtime_profile(profile, command="api") is profile


@pytest.mark.parametrize("command", ["api", "gate"])
def test_bootstrap_api_and_gate_select_task4_scoped_dependency_admission(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    observed: dict[str, object] = {}
    expected_report = SimpleNamespace(may_proceed=True, blocker_codes=())

    def run_default_durable_dependency_admission(**kwargs: object) -> object:
        observed.update(kwargs)
        return expected_report

    probes_module = SimpleNamespace(
        FULL_DEPENDENCY_ADMISSION_SCOPE="full-v1",
        TASK4_INGESTION_ADMISSION_SCOPE=_TASK4_INGESTION_SCOPE,
        run_default_durable_dependency_admission=run_default_durable_dependency_admission,
    )
    real_import = launcher.importlib.import_module

    def importer(module_name: str) -> object:
        if module_name == "idis.runtime.dependency_probes":
            return probes_module
        return real_import(module_name)

    monkeypatch.setattr(launcher.importlib, "import_module", importer)
    profile = SimpleNamespace(
        command=command,
        generation="bootstrap-v1",
        process_role="api" if command == "api" else None,
        governance_public_key_sha256="a" * 64,
        values={
            "IDIS_RUNTIME_PROFILE_GENERATION": "bootstrap-v1",
            "IDIS_REQUIRE_PROMOTED_PROMPTS": "0",
        },
    )

    # A bootstrap gate is scoped only after the launcher validates the durable Task-4 CLI mode;
    # other bootstrap gates later perform prompt/provider evaluation under full admission.
    report = launcher._default_admission_runner(  # noqa: SLF001
        command,
        profile,
        task4_ingestion_scope=True,
    )()

    assert report is expected_report
    assert observed["expected_role"] == command
    assert observed["admission_scope"] == _TASK4_INGESTION_SCOPE


def test_task4_scoped_admission_runs_exact_ingestion_api_dependency_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("idis.runtime.dependency_probes")
    from tests.slice100_task2.test_dependency_probe_adapters import (
        _complete_probe_set,
        _strict_env,
    )

    calls: list[str] = []
    complete = _complete_probe_set(module, calls)

    def forbidden_probe(name: str) -> Any:
        def probe() -> object:
            calls.append(name)
            raise AssertionError(f"Task 4 ingestion admission called forbidden {name} probe")

        return probe

    probes = module.DurableDependencyProbeAdapters(
        postgres=complete.postgres,
        audit=complete.audit,
        idempotency=complete.idempotency,
        redis=complete.redis,
        object_store=complete.object_store,
        ingestion=complete.ingestion,
        worker=forbidden_probe("worker"),
        budgets=forbidden_probe("budgets"),
    )
    real_auth_check = module.check_auth_readiness

    def observed_auth_check(*, env: object) -> object:
        calls.append("auth")
        return real_auth_check(env=env)

    monkeypatch.setattr(module, "check_auth_readiness", observed_auth_check)
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    env = _strict_env(profile_root)
    env["IDIS_PROCESS_ROLE"] = "api"

    report = module.run_durable_dependency_admission(
        env=env,
        probes=probes,
        expected_role="api",
        worktree_root=(tmp_path / "worktree").resolve(),
        temp_root=(tmp_path / "temp").resolve(),
        admission_scope=_TASK4_INGESTION_SCOPE,
    )

    assert report.may_proceed is True
    assert report.blocker_codes == ()
    assert calls == [
        "postgres",
        "audit",
        "idempotency",
        "redis",
        "object_store",
        "ingestion",
        "auth",
    ]
    assert [check.component for check in report.checks] == [
        "postgres",
        "audit",
        "idempotency",
        "redis",
        "object_store",
        "ingestion",
        "auth",
    ]
