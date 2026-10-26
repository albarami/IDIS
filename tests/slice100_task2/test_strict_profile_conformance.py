"""Conformance REDs for immutable strict-profile propagation and launch identity."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.slice100_task2.test_strict_profile_launcher import _process_env, _write_dotenv


def _reviewed_profile(
    tmp_path: Path,
    command: str = "worker",
    additions: Mapping[str, str] | None = None,
    *,
    generation: str = "acceptance-v1",
    promoted: str = "1",
) -> Any:
    profile_module = importlib.import_module("idis.runtime.profile")
    dotenv, _ = _write_dotenv(
        tmp_path,
        generation=generation,
        promoted=promoted,
        additions=additions,
    )
    return profile_module.load_strict_runtime_profile(
        command=command,
        process_env=_process_env(dotenv),
    )


def test_dependency_admission_consumes_frozen_profile_not_mutable_process_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "api")
    observed: dict[str, object] = {}

    def admission(**kwargs: object) -> object:
        observed.update(kwargs)
        return SimpleNamespace(may_proceed=True)

    monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql://mutated-after-review")
    real_import = launcher.importlib.import_module
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(run_default_durable_dependency_admission=admission)
            if name == "idis.runtime.dependency_probes"
            else real_import(name)
        ),
    )

    launcher._default_admission_runner("api", profile)()

    assert observed["env"] is profile.values
    assert observed["expected_public_key_sha256"] == profile.governance_public_key_sha256
    assert observed["env"]["IDIS_DATABASE_URL"] != "postgresql://mutated-after-review"  # type: ignore[index]


def test_strict_api_launcher_passes_reviewed_profile_to_composition_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "api")
    observed: list[object] = []

    def create_app(*, strict_profile: object) -> object:
        observed.append(strict_profile)
        return object()

    monkeypatch.setattr(
        launcher,
        "run_admitted_runtime_role",
        lambda **kwargs: int(kwargs["runtime_runner"]()),
    )
    monkeypatch.setattr(launcher, "write_pid_file", lambda *_args, **_kwargs: None)
    real_import = launcher.importlib.import_module
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(run=lambda _app, **_kwargs: None)
            if name == "uvicorn"
            else real_import(name)
        ),
    )
    args = SimpleNamespace(host="127.0.0.1", port=8000, pid_file=tmp_path / "api.pid")

    assert launcher._run_api(SimpleNamespace(create_app=create_app), args, profile) == 0
    assert observed == [profile]


def _refuse_pinned_poppler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host Poppler identity out of this launcher row."""

    from idis.parsers.poppler_runtime import PopplerRuntimeBindingError

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PopplerRuntimeBindingError("POPPLER_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        unavailable,
    )


def test_strict_worker_launcher_passes_profile_to_every_runtime_config_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_pinned_poppler(monkeypatch)
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "worker")
    observed: list[tuple[str, object]] = []

    class Worker:
        def __init__(self, **kwargs: object) -> None:
            observed.append(("worker", kwargs["strict_profile"]))

        async def _process_queued_runs(self) -> int:
            return 0

    runtime = SimpleNamespace(
        load_worker_lease_config=lambda *, env, strict: (
            observed.append(("lease", env)),
            SimpleNamespace(lease_seconds=60, heartbeat_seconds=10),
        )[1],
        get_worker_tenant_ids=lambda *, env: (
            observed.append(("tenants", env)),
            [profile.values["IDIS_ACCEPTANCE_TENANT_ID"]],
        )[1],
        PipelineWorker=Worker,
    )
    monkeypatch.setattr(
        launcher,
        "run_admitted_runtime_role",
        lambda **kwargs: int(kwargs["runtime_runner"]()),
    )
    monkeypatch.setattr(launcher, "write_pid_file", lambda *_args, **_kwargs: None)
    args = SimpleNamespace(
        poll_seconds=2.0,
        watch=False,
        pid_file=tmp_path / "worker.pid",
    )

    assert launcher._run_worker(runtime, args, profile) == 0
    assert observed == [
        ("lease", profile.values),
        ("tenants", profile.values),
        ("worker", profile),
    ]


def _stub_linux_poppler(tmp_path: Path) -> object:
    from idis.parsers.poppler_linux_runtime import (
        PINNED_POPPLER_LINUX_LIBRARY_NAME,
        PinnedLinuxPopplerRuntime,
        _identity_sha256,
    )
    from idis.parsers.poppler_runtime import PINNED_POPPLER_VERSION

    prefix = (tmp_path / "prefix").resolve()
    source_sha256 = "a" * 64
    recipe_sha256 = "b" * 64
    pdftoppm_sha256 = "c" * 64
    pdfinfo_sha256 = "d" * 64
    library_sha256 = "e" * 64
    rpath = str(prefix / "lib")
    digest = _identity_sha256(
        version=PINNED_POPPLER_VERSION,
        source_sha256=source_sha256,
        recipe_sha256=recipe_sha256,
        prefix=str(prefix),
        pdftoppm_sha256=pdftoppm_sha256,
        pdfinfo_sha256=pdfinfo_sha256,
        library_sha256=library_sha256,
        rpath=rpath,
    )
    return PinnedLinuxPopplerRuntime(
        version=PINNED_POPPLER_VERSION,
        runtime_identity_sha256=digest,
        pdftoppm_sha256=pdftoppm_sha256,
        pdfinfo_sha256=pdfinfo_sha256,
        poppler_libpoppler_sha256=library_sha256,
        poppler_source_sha256=source_sha256,
        poppler_builder_recipe_sha256=recipe_sha256,
        prefix=str(prefix),
        rpath=rpath,
        _pdftoppm_path=prefix / "bin" / "pdftoppm",
        _pdfinfo_path=prefix / "bin" / "pdfinfo",
        _poppler_library_path=prefix / "lib" / PINNED_POPPLER_LINUX_LIBRARY_NAME,
    )


def test_acceptance_v1_worker_refuses_noncanonical_linux_poppler_before_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "worker")
    assert profile.generation == "acceptance-v1"
    binding = _stub_linux_poppler(tmp_path)
    monkeypatch.setattr(
        "idis.parsers.poppler_runtime.resolve_pinned_poppler_runtime",
        lambda *_args, **_kwargs: binding,
    )
    pid_file = tmp_path / "worker.pid"
    args = SimpleNamespace(poll_seconds=2.0, watch=False, pid_file=pid_file)

    with pytest.raises(launcher.StrictLauncherError) as error:
        launcher._run_worker(SimpleNamespace(), args, profile)

    assert error.value.code == "POPPLER_RUNTIME_NONCANONICAL"
    assert pid_file.exists() is False


def test_direct_api_factory_fails_strict_mode_without_reviewed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api.main import create_app

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")

    with pytest.raises(RuntimeError, match="STRICT_PROFILE_LOAD_FAILED"):
        create_app()


def test_direct_production_worker_fails_strict_mode_without_reviewed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.pipeline.worker import PipelineWorker

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "worker")

    with pytest.raises(RuntimeError, match="STRICT_PROFILE_LOAD_FAILED"):
        PipelineWorker(tenant_ids=["10000000-0000-4000-8000-000000000001"])


def test_manual_worker_script_fails_strict_mode_without_reviewed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.process_queued_runs import process_all_queued

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "worker")
    monkeypatch.setenv("IDIS_WORKER_LEASE_SECONDS", "60")
    monkeypatch.setenv("IDIS_WORKER_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("IDIS_WORKER_TENANT_IDS", "10000000-0000-4000-8000-000000000001")

    with pytest.raises(RuntimeError, match="STRICT_PROFILE_LOAD_FAILED"):
        asyncio.run(
            process_all_queued(
                worker_factory=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("direct strict script constructed a worker")
                )
            )
        )


class _Result:
    def __init__(self, rows: list[object] | None = None) -> None:
        self._rows = list(rows or [])

    def fetchall(self) -> list[object]:
        return list(self._rows)


class _LaunchConnection:
    def __init__(self, existing: list[dict[str, str]]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: dict[str, object]) -> _Result:
        sql = " ".join(str(statement).lower().split())
        self.calls.append((sql, dict(parameters)))
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "from audit_events" in sql:
            return _Result([SimpleNamespace(**row) for row in self.existing])
        raise AssertionError(f"unexpected launch SQL: {sql}")


class _ConnectionFactory:
    def __init__(self, connection: _LaunchConnection) -> None:
        self.connection = connection

    @contextmanager
    def __call__(self) -> Any:
        yield self.connection


class _AuditSink:
    def __init__(self) -> None:
        self.emitted: list[tuple[object, dict[str, object]]] = []

    def emit_in_tx(self, connection: object, event: dict[str, object]) -> None:
        self.emitted.append((connection, event))


def test_acceptance_launch_identity_is_checked_and_audited_in_one_transaction(
    tmp_path: Path,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "api")
    connection = _LaunchConnection(
        [
            {
                "command": "worker",
                "generation": "acceptance-v1",
                "base_profile_hmac": profile.base_profile_hmac,
            }
        ]
    )
    sink = _AuditSink()
    tenants: list[str] = []

    launcher.persist_strict_launch_identity(
        profile,
        connection_factory=_ConnectionFactory(connection),
        tenant_setter=lambda _conn, tenant: tenants.append(tenant),
        audit_sink=sink,
    )

    assert tenants == [profile.values["IDIS_ACCEPTANCE_TENANT_ID"]]
    assert "pg_advisory_xact_lock" in connection.calls[0][0]
    assert "from audit_events" in connection.calls[1][0]
    assert sink.emitted[0][0] is connection
    assert sink.emitted[0][1]["payload"]["safe"] == launcher.launch_audit_fields(profile)  # type: ignore[index]


@pytest.mark.parametrize("existing_command", ["api", "worker", "gate"])
def test_acceptance_launch_rejects_conflicting_peer_profile_atomically(
    tmp_path: Path,
    existing_command: str,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "gate")
    connection = _LaunchConnection(
        [
            {
                "command": existing_command,
                "generation": "acceptance-v1",
                "base_profile_hmac": "f" * 64,
            }
        ]
    )
    sink = _AuditSink()

    with pytest.raises(launcher.StrictLauncherError, match="STRICT_PROFILE_MISMATCH"):
        launcher.persist_strict_launch_identity(
            profile,
            connection_factory=_ConnectionFactory(connection),
            tenant_setter=lambda *_args: None,
            audit_sink=sink,
        )

    assert sink.emitted == []
    assert "pg_advisory_xact_lock" in connection.calls[0][0]


def test_gate_launch_is_durably_audited_before_forwarded_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "gate")
    events: list[str] = []
    received_profiles: list[object] = []

    def gate_main(_args: list[str], *, strict_profile: object) -> int:
        received_profiles.append(strict_profile)
        events.append("gate")
        return 0

    module = SimpleNamespace(main=gate_main)
    monkeypatch.setattr(
        launcher,
        "_default_admission_runner",
        lambda command, reviewed: (
            lambda: (
                SimpleNamespace(may_proceed=True)
                if command == "gate" and reviewed is profile
                else None
            )
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda reviewed: lambda: events.append("audit") if reviewed is profile else None,
    )

    result = launcher._run_gate(module, ["gate", "--verify-only"], profile)

    assert result == 0
    assert events == ["audit", "gate"]
    assert received_profiles == [profile]


def test_gate_dependency_blocker_prevents_audit_and_gate_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "gate")
    events: list[str] = []
    module = SimpleNamespace(
        main=lambda _args, *, strict_profile: events.append("gate") or int(strict_profile is None)
    )
    monkeypatch.setattr(
        launcher,
        "_default_admission_runner",
        lambda command, reviewed: (
            lambda: (
                SimpleNamespace(may_proceed=False)
                if command == "gate" and reviewed is profile
                else None
            )
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda _reviewed: lambda: events.append("audit"),
    )

    with pytest.raises(launcher.StrictLauncherError, match="STRICT_FULL_LIVE_BLOCKED"):
        launcher._run_gate(module, ["gate", "--verify-only"], profile)

    assert events == []


def test_governance_launcher_passes_reviewed_profile_to_import_entrypoint(
    tmp_path: Path,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(tmp_path, "governance")
    observed: list[tuple[list[str], object]] = []
    module = SimpleNamespace(
        main=lambda args, *, strict_profile: observed.append((args, strict_profile)) or 0
    )

    result = launcher._run_governance(
        module,
        ["governance", "--no-secret-output"],
        profile,
    )

    assert result == 0
    assert observed == [(["--no-secret-output"], profile)]


def test_successful_migration_persists_exact_bootstrap_identity_after_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    events: list[object] = []
    module = SimpleNamespace(upgrade=lambda _config, revision: events.append(("upgrade", revision)))
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda reviewed: lambda: events.append(("audit", launcher.launch_audit_fields(reviewed))),
    )
    monkeypatch.setattr(
        launcher,
        "_grant_and_verify_migration_privileges",
        lambda reviewed: events.append(("privileges", reviewed.command)),
    )
    fake_config = SimpleNamespace(Config=lambda path: ("config", path))
    real_import = launcher.importlib.import_module
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: fake_config if name == "alembic.config" else real_import(name),
    )

    result = launcher._run_migration(
        module,
        SimpleNamespace(alembic_config="alembic.ini", upgrade="head"),
        profile,
    )

    assert result == 0
    assert events == [
        ("upgrade", "head"),
        ("privileges", "migrate"),
        (
            "audit",
            {
                "base_profile_hmac": profile.base_profile_hmac,
                "command": "migrate",
                "generation": "bootstrap-v1",
                "process_role": None,
            },
        ),
    ]


def test_failed_migration_never_emits_a_success_launch_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    audited: list[object] = []
    module = SimpleNamespace(
        upgrade=lambda _config, _revision: (_ for _ in ()).throw(RuntimeError("upgrade failed"))
    )
    monkeypatch.setattr(
        launcher,
        "_launch_audit_runner",
        lambda reviewed: lambda: audited.append(reviewed),
    )
    fake_config = SimpleNamespace(Config=lambda path: ("config", path))
    real_import = launcher.importlib.import_module
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: fake_config if name == "alembic.config" else real_import(name),
    )

    with pytest.raises(RuntimeError, match="upgrade failed"):
        launcher._run_migration(
            module,
            SimpleNamespace(alembic_config="alembic.ini", upgrade="head"),
            profile,
        )

    assert audited == []


def test_migration_audit_failure_fails_command_after_schema_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    events: list[str] = []
    module = SimpleNamespace(upgrade=lambda _config, _revision: events.append("upgrade"))

    def fail_audit() -> None:
        events.append("audit")
        raise launcher.StrictLauncherError("AUDIT_EMIT_FAILED")

    monkeypatch.setattr(launcher, "_launch_audit_runner", lambda _reviewed: fail_audit)
    monkeypatch.setattr(
        launcher,
        "_grant_and_verify_migration_privileges",
        lambda _reviewed: events.append("privileges"),
    )
    fake_config = SimpleNamespace(Config=lambda path: ("config", path))
    real_import = launcher.importlib.import_module
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda name: fake_config if name == "alembic.config" else real_import(name),
    )

    with pytest.raises(launcher.StrictLauncherError, match="AUDIT_EMIT_FAILED"):
        launcher._run_migration(
            module,
            SimpleNamespace(alembic_config="alembic.ini", upgrade="head"),
            profile,
        )

    assert events == ["upgrade", "privileges", "audit"]


def test_bootstrap_migration_identity_is_accepted_by_durable_audit_boundary(
    tmp_path: Path,
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    profile = _reviewed_profile(
        tmp_path,
        "migrate",
        generation="bootstrap-v1",
        promoted="0",
    )
    connection = _LaunchConnection([])
    sink = _AuditSink()

    launcher.persist_strict_launch_identity(
        profile,
        connection_factory=_ConnectionFactory(connection),
        tenant_setter=lambda *_args: None,
        audit_sink=sink,
    )

    assert len(sink.emitted) == 1
    assert sink.emitted[0][1]["payload"]["safe"] == launcher.launch_audit_fields(profile)  # type: ignore[index]


def test_launcher_malformed_governance_invocation_never_echoes_argument_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = importlib.import_module("idis.runtime.strict_launcher")
    sensitive_path = r"C:\operator-secrets\rights-envelope.json"

    result = launcher.main(
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
            "--unexpected",
            sensitive_path,
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert json.loads(captured.err) == {
        "may_proceed": False,
        "code": "GOVERNANCE_SCHEMA_INVALID",
    }
    assert captured.out == ""
    assert sensitive_path not in captured.err


def test_worker_reciprocal_proof_verification_binds_current_profile_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.pipeline.worker as worker_module

    profile = _reviewed_profile(tmp_path, "worker")
    calls: list[dict[str, object]] = []
    repository = SimpleNamespace(require_consumed_for_run=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        worker_module,
        "get_strict_preflight_proofs_repository",
        lambda _conn, _tenant: repository,
    )
    run_data = {
        "strict_preflight_proof_id": "44444444-4444-4444-8444-444444444444",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "deal_id": "33333333-3333-4333-8333-333333333333",
    }

    worker_module._verify_worker_strict_preflight_proof(
        db_conn=object(),
        tenant_id=profile.values["IDIS_ACCEPTANCE_TENANT_ID"],
        run_data=run_data,
        strict_profile=profile,
    )

    assert calls == [
        {
            "proof_id": run_data["strict_preflight_proof_id"],
            "run_id": run_data["run_id"],
            "deal_id": run_data["deal_id"],
            "strict_profile_generation": "acceptance-v1",
            "strict_profile_hmac": profile.base_profile_hmac,
        }
    ]


def test_strict_api_request_auth_uses_reviewed_registry_after_ambient_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.postgres_sink import PostgresAuditSink

    profile = _reviewed_profile(tmp_path / "profile", "api")
    admitted_key = profile.values["IDIS_REAL_EXAMPLE_API_KEY"]
    attacker_key = "ambient-attacker-key"
    attacker_registry = {
        attacker_key: {
            "tenant_id": "90000000-0000-4000-8000-000000000009",
            "actor_id": "ambient-attacker",
            "name": "Ambient Attacker",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ADMIN"],
        }
    }

    class HermeticPostgresAuditSink(PostgresAuditSink):
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        def emit(self, event: dict[str, object]) -> None:
            self.events.append(dict(event))

        def emit_in_tx(self, _connection: object, event: dict[str, object]) -> None:
            self.emit(event)

    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv("IDIS_PROCESS_ROLE", "api")
    monkeypatch.setenv("IDIS_API_KEYS_JSON", json.dumps(attacker_registry))
    monkeypatch.setattr("idis.persistence.db.is_postgres_configured", lambda: False)
    app = create_app(
        audit_sink=HermeticPostgresAuditSink(),
        ingestion_service=object(),
        strict_profile=profile,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        admitted = client.get(
            "/v1/tenants/me",
            headers={"X-IDIS-API-Key": admitted_key},
        )
        attacker = client.get(
            "/v1/tenants/me",
            headers={"X-IDIS-API-Key": attacker_key},
        )

    assert admitted.status_code == 200
    assert admitted.json()["tenant_id"] == profile.values["IDIS_ACCEPTANCE_TENANT_ID"]
    assert attacker.status_code == 401
