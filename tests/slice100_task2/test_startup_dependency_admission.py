"""RED startup-order contracts for Task 2 durable dependency admission."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _launcher() -> ModuleType:
    return importlib.import_module("idis.runtime.strict_launcher")


def _admitted_runtime_helper(launcher: ModuleType) -> Any:
    helper = getattr(launcher, "run_admitted_runtime_role", None)
    if helper is None:
        pytest.fail(
            "TASK2_API_MISSING: idis.runtime.strict_launcher.run_admitted_runtime_role",
            pytrace=False,
        )
    return helper


@pytest.mark.parametrize("command", ["api", "worker"])
def test_runtime_role_runs_admission_before_starting_api_or_worker(command: str) -> None:
    launcher = _launcher()
    helper = _admitted_runtime_helper(launcher)
    events: list[str] = []
    report = SimpleNamespace(may_proceed=True, blocker_codes=())

    def admit() -> object:
        events.append("admit")
        return report

    def start() -> int:
        events.append("start")
        return 17

    def audit() -> None:
        events.append("audit")

    result = helper(
        command=command,
        admission_runner=admit,
        launch_audit_runner=audit,
        runtime_runner=start,
    )

    assert result == 17
    assert events == ["admit", "audit", "start"]


@pytest.mark.parametrize("command", ["api", "worker"])
def test_runtime_role_never_starts_when_durable_admission_blocks(command: str) -> None:
    launcher = _launcher()
    helper = _admitted_runtime_helper(launcher)
    events: list[str] = []

    def admit() -> object:
        events.append("admit")
        return SimpleNamespace(
            may_proceed=False,
            blocker_codes=("DATABASE_UNAVAILABLE",),
        )

    def must_not_start() -> int:
        events.append("started")
        raise AssertionError("runtime started after blocked durable admission")

    def must_not_audit() -> None:
        events.append("audited")
        raise AssertionError("launch audited as admitted after blocked durable admission")

    with pytest.raises(launcher.StrictLauncherError):
        helper(
            command=command,
            admission_runner=admit,
            launch_audit_runner=must_not_audit,
            runtime_runner=must_not_start,
        )

    assert events == ["admit"]


def test_api_main_path_crosses_admission_boundary_before_uvicorn_serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher()
    _admitted_runtime_helper(launcher)
    events: list[str] = []

    def admitted_boundary(
        *,
        command: str,
        admission_runner: object,
        launch_audit_runner: Any,
        runtime_runner: Any,
    ) -> int:
        assert callable(admission_runner)
        events.append(f"admit:{command}")
        launch_audit_runner()
        return int(runtime_runner())

    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(tmp_path / "profile", "api")
    runtime_module = SimpleNamespace(
        create_app=lambda *, strict_profile: (
            (events.append("profile:api") if strict_profile is profile else None) or object()
        )
    )
    monkeypatch.setattr(launcher, "run_admitted_runtime_role", admitted_boundary)
    monkeypatch.setattr(
        launcher,
        "load_profile_then_import_runtime",
        lambda *, command: (profile, runtime_module),
    )
    monkeypatch.setattr(launcher, "write_pid_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _profile: lambda: events.append("audit"),
    )
    real_import = launcher.importlib.import_module

    def importer(name: str) -> object:
        if name == "uvicorn":
            return SimpleNamespace(
                run=lambda _app, **_kwargs: events.append("serve"),
            )
        return real_import(name)

    monkeypatch.setattr(launcher.importlib, "import_module", importer)

    result = launcher.main(
        [
            "api",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--pid-file",
            str(tmp_path / "api.pid"),
        ]
    )

    assert result == 0
    assert events == ["admit:api", "audit", "profile:api", "serve"]


def _refuse_pinned_poppler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host Poppler identity out of this launcher row."""

    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        unavailable,
    )


def test_worker_main_path_crosses_admission_boundary_before_first_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_pinned_poppler(monkeypatch)
    launcher = _launcher()
    _admitted_runtime_helper(launcher)
    events: list[str] = []

    def admitted_boundary(
        *,
        command: str,
        admission_runner: object,
        launch_audit_runner: Any,
        runtime_runner: Any,
    ) -> int:
        assert callable(admission_runner)
        events.append(f"admit:{command}")
        launch_audit_runner()
        return int(runtime_runner())

    class Worker:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["strict_profile"] is profile

        async def _process_queued_runs(self) -> int:
            events.append("poll")
            return 0

    runtime_module = SimpleNamespace(
        load_worker_lease_config=lambda *, env, strict: object(),
        get_worker_tenant_ids=lambda *, env: ("10000000-0000-4000-8000-000000000001",),
        PipelineWorker=Worker,
    )
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(tmp_path / "profile", "worker")
    monkeypatch.setattr(launcher, "run_admitted_runtime_role", admitted_boundary)
    monkeypatch.setattr(
        launcher,
        "load_profile_then_import_runtime",
        lambda *, command: (profile, runtime_module),
    )
    monkeypatch.setattr(launcher, "write_pid_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _profile: lambda: events.append("audit"),
    )

    result = launcher.main(
        [
            "worker",
            "--poll-seconds",
            "2",
            "--pid-file",
            str(tmp_path / "worker.pid"),
        ]
    )

    assert result == 0
    assert events == ["admit:worker", "audit", "poll"]


def test_launch_audit_persists_only_safe_profile_identity(
    tmp_path: Path,
) -> None:
    launcher = _launcher()
    from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

    profile = _reviewed_profile(tmp_path, "api")
    event = launcher._launch_event(profile)
    from idis.validators.audit_event_validator import validate_audit_event

    validation = validate_audit_event(event)
    assert validation.passed, [error.code for error in validation.errors]
    assert event["event_type"] == "integration.strict_launch.admitted"
    assert event["payload"]["safe"] == {  # type: ignore[index]
        "base_profile_hmac": profile.base_profile_hmac,
        "command": "api",
        "generation": "acceptance-v1",
        "process_role": "api",
    }
    rendered = repr(event)
    for name in (
        "IDIS_DATABASE_URL",
        "IDIS_REAL_EXAMPLE_API_KEY",
        "IDIS_RUNTIME_PROFILE_HMAC_KEY",
        "NEO4J_PASSWORD",
    ):
        assert profile.values[name] not in rendered
