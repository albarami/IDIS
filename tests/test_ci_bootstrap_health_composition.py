"""Offline preparation controls, not runtime admission or container health proof."""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol, runtime_checkable

import pytest

from idis.methodology.importers.fdd_excel import import_fdd_workbook
from idis.runtime import profile as profile_module
from idis.runtime.governance_envelopes import GovernanceEnvelopeError, verify_envelope
from idis.runtime.strict_auth import validate_strict_auth_binding
from tests.test_ci_container_health_summary import (
    _by_id,
    _prepare_admission_case,
    _run_admission_case,
)

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / ".github/scripts/prepare_bootstrap_health.py"
AUTHORITY = "AUTH-CI-HEALTH-20261017"
DEFAULT_CMD = (
    "python",
    "-m",
    "idis.runtime.strict_launcher",
    "api",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--pid-file",
    "/tmp/idis-api.pid",
)


def _helper() -> ModuleType:
    if not HELPER_PATH.is_file():
        pytest.fail("H1_PREPARATION_HELPER_MISSING: explicit synthetic custody controls absent")
    spec = importlib.util.spec_from_file_location("ci_bootstrap_health", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def model_path(tmp_path: Path) -> Path:
    # Fixture-only bytes: preparation must NEVER label these as qualified STT assets.
    model = tmp_path / "unit-model-not-qualified"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (model / name).write_bytes(b"unit-test-only-not-a-runtime-model")
    return model


@runtime_checkable
class _Prepared(Protocol):
    root: Path
    manifest_path: Path
    profile_path: Path
    public_key_path: Path


def _prepare(module: ModuleType, tmp_path: Path, model_path: Path) -> _Prepared:
    prepared: object = module.prepare_inputs(
        output_root=tmp_path / "owned",
        model_path=model_path,
        synthetic_ci_health=True,
        authority=AUTHORITY,
    )
    assert isinstance(prepared, _Prepared)
    return prepared


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_pinned_workbook_factory_checkout_retains_exact_lf_bytes(
    tmp_path: Path,
    autocrlf: str,
) -> None:
    git_executable = shutil.which("git")
    if git_executable is None:
        pytest.fail("PINNED_FACTORY_GIT_REQUIRED: install Git in the test runtime")
    source = "tests/slice100_task5/_workbook_factory.py"
    canonical = (ROOT / source).read_bytes()
    expected_sha256 = "812ba3043931b40dfda534ac813ce5f7f4eeac2f8e996f49d17630817f439abf"
    assert hashlib.sha256(canonical).hexdigest() == expected_sha256
    attributes = (ROOT / ".gitattributes").read_bytes()
    repository = tmp_path / "owned-repository"
    repository.mkdir()
    owned_index = tmp_path / "owned.index"
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_ATTR_NOSYSTEM="1",
        GIT_INDEX_FILE=str(owned_index),
    )

    def git(*arguments: str, data: bytes | None = None) -> bytes:
        result = subprocess.run(
            [
                git_executable,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.splitIndex=false",
                *arguments,
            ],
            input=data,
            cwd=repository,
            env=environment,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, "PINNED_FACTORY_GIT_OPERATION_FAILED"
        return result.stdout

    # Only fresh owned Git objects/index are touched; no commit, remote or config edit.
    git("init", "--quiet", "--template=", ".")
    blob = git("hash-object", "-w", "--stdin", data=canonical).decode().strip()
    assert blob == "78c03b4b69642afc789ae495524bd6efcd8f9f88"
    assert git("cat-file", "blob", blob) == canonical
    attribute_blob = git("hash-object", "-w", "--stdin", data=attributes).decode().strip()
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},{source}")
    git("update-index", "--add", "--cacheinfo", f"100644,{attribute_blob},.gitattributes")
    before_index = owned_index.read_bytes()
    destination = tmp_path / "owned-checkout"
    destination.mkdir()
    git(
        "-c",
        f"core.autocrlf={autocrlf}",
        "checkout-index",
        f"--prefix={destination.as_posix()}/",
        "--",
        source,
    )
    checked_out = (destination / source).read_bytes()
    assert owned_index.read_bytes() == before_index
    assert (ROOT / source).read_bytes() == canonical
    assert (ROOT / ".gitattributes").read_bytes() == attributes
    assert checked_out == canonical, "PINNED_FACTORY_CHECKOUT_BYTES_CHANGED"
    assert hashlib.sha256(checked_out).hexdigest() == expected_sha256
    assert b"\r\n" not in checked_out


@pytest.mark.parametrize("selected,authority", [(False, AUTHORITY), (True, "wrong"), (False, "")])
def test_explicit_selection_fails_before_any_output(
    tmp_path: Path,
    model_path: Path,
    selected: bool,
    authority: str,
) -> None:
    module = _helper()
    with pytest.raises(module.PreparationError, match="CI_HEALTH_SELECTION_REQUIRED"):
        module.prepare_inputs(
            output_root=tmp_path / "owned",
            model_path=model_path,
            synthetic_ci_health=selected,
            authority=authority,
        )
    assert not (tmp_path / "owned").exists()


def test_preparation_reuses_real_readers_with_fifty_dotenv_sources_and_one_launcher_role(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    startup = {"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    receipt = module.qualify_inputs(prepared, startup_env=startup, image_defaults={})
    loaded = profile_module.load_strict_runtime_profile(command="api", process_env=dict(startup))
    required = profile_module._REQUIRED_NAMES | profile_module._COMMAND_REQUIRED_NAMES["api"]
    assert len(required) == 50
    assert set(loaded.values) == required | {"IDIS_PROCESS_ROLE"}
    assert all(loaded.source_for(name) == "dotenv" for name in required)
    assert loaded.source_for("IDIS_PROCESS_ROLE") == "launcher"
    assert receipt["source_counts"] == {"dotenv": 50, "launcher": 1}
    assert validate_strict_auth_binding(loaded.values).may_proceed
    api_file_has_admin_credential = "IDIS_DATABASE_ADMIN_URL" in prepared.profile_path.read_text()
    assert api_file_has_admin_credential is False
    assert "IDIS_DATABASE_ADMIN_URL" not in loaded.values
    assert loaded.values["IDIS_PROVIDER_BUDGET_MAX_CALLS"] == "0"
    assert loaded.values["IDIS_UI_DEV_AUTH_BYPASS"] == "false"
    assert loaded.values["IDIS_MEDIA_STT_ALLOW_DOWNLOAD"] == "0"
    workbook = Path(loaded.values["IDIS_FDD_METHODOLOGY_PATH"])
    before = hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert len(import_fdd_workbook(workbook).current_version.questions) == 79
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == before
    manifest = json.loads(prepared.manifest_path.read_text())
    assert manifest["factory_sha256"] == (
        "812ba3043931b40dfda534ac813ce5f7f4eeac2f8e996f49d17630817f439abf"
    )
    assert receipt["public_key_sha256"] == loaded.governance_public_key_sha256
    assert receipt["public_key_sha256"] != (
        "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
    )
    assert (
        receipt["profile_identity_sha256"]
        == hashlib.sha256(loaded.base_profile_hmac.encode("ascii")).hexdigest()
    )
    serialized = json.dumps(receipt)
    leaked = (
        any(
            loaded.values[name] in serialized
            for name in (
                "IDIS_RUNTIME_PROFILE_HMAC_KEY",
                "IDIS_REAL_EXAMPLE_API_KEY",
                "IDIS_DATABASE_URL",
                "NEO4J_PASSWORD",
            )
        )
        or loaded.base_profile_hmac in serialized
    )
    assert leaked is False, "CI_HEALTH_RECEIPT_EXPOSED_VALUE"
    assert all(value == "NOT_EXECUTED" for value in receipt["runtime_phases"].values())
    assert receipt["provider_invocation_attempts"] == "NOT_EXECUTED"
    assert receipt["purpose"] == "container health with synthetic fixtures"


@pytest.mark.parametrize(
    "name",
    [
        "IDIS_DATABASE_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_PROCESS_ROLE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "IDIS_OTEL_ENABLED",
        "https_proxy",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "no_proxy",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "IDIS_DATABASE_ADMIN_URL",
        "UNKNOWN_STARTUP_OVERRIDE",
    ],
)
def test_startup_overrides_reject_before_child_creation(
    tmp_path: Path,
    model_path: Path,
    name: str,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    calls: list[object] = []
    startup = {"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path), name: "injected"}
    with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
        module.dispatch_api(
            prepared,
            command=DEFAULT_CMD,
            startup_env=startup,
            image_defaults={},
            runner=lambda *args: calls.append(args),
        )
    assert calls == []


def test_equal_required_override_is_still_process_sourced_and_refused(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    startup = {"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    loaded = profile_module.load_strict_runtime_profile(command="api", process_env=dict(startup))
    for name in ("IDIS_DATABASE_URL", "IDIS_ERASURE_DATABASE_URL"):
        overridden = {**startup, name: loaded.values[name]}
        actual = profile_module.load_strict_runtime_profile(command="api", process_env=overridden)
        assert actual.source_for(name) == "process"
        with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
            module.qualify_inputs(prepared, startup_env=overridden, image_defaults={})


@pytest.mark.parametrize(
    "command",
    [
        ("python", "-m", "idis.runtime.strict_launcher", "gate"),
        ("python", "-m", "idis.evaluation.real_example_gate"),
        ("python", "scripts/run_real_example_gate.py"),
        ("python", "scripts/run_slice100_real_example.py"),
        ("python", "-m", "idis.runtime.strict_launcher", "worker"),
        ("python", "-m", "idis.runtime.strict_launcher", "governance"),
        ("python", "-m", "idis.runtime.strict_launcher", "api", "--full"),
        ("sh", "-c", "python -m idis.runtime.strict_launcher api"),
    ],
)
def test_only_unchanged_default_api_command_may_cross_consumer_boundary(
    tmp_path: Path,
    model_path: Path,
    command: tuple[str, ...],
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    calls: list[object] = []
    with pytest.raises(module.PreparationError, match="CI_HEALTH_CONSUMER_REJECTED"):
        module.dispatch_api(
            prepared,
            command=command,
            startup_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
            image_defaults={},
            runner=lambda *args: calls.append(args),
        )
    assert calls == []


def test_dispatch_binds_manifest_key_and_exact_resolved_environment_without_inheritance(
    tmp_path: Path,
    model_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    image_defaults = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}
    startup = {**image_defaults, "IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    module.dispatch_api(
        prepared,
        command=DEFAULT_CMD,
        startup_env=startup,
        image_defaults=image_defaults,
        runner=lambda command, env: calls.append((command, env)),
    )
    assert calls == [(DEFAULT_CMD, startup)]
    assert "OPENAI_API_KEY" not in calls[0][1]
    changed = {**startup, "PATH": "/unreviewed"}
    with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
        module.qualify_inputs(prepared, startup_env=changed, image_defaults=image_defaults)
    with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
        module.qualify_inputs(
            prepared, startup_env=startup, image_defaults={**image_defaults, "HF_TOKEN": "x"}
        )


def test_pythonpath_cannot_be_laundered_through_matching_image_defaults(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    calls: list[object] = []
    image_defaults = {"PYTHONPATH": str(tmp_path / "alternate-code-not-executed")}
    startup = {**image_defaults, "IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
        module.dispatch_api(
            prepared,
            command=DEFAULT_CMD,
            startup_env=startup,
            image_defaults=image_defaults,
            runner=lambda *args: calls.append(args),
        )
    assert calls == []


def test_preparation_does_not_claim_unexecuted_maintenance_mount_exclusion(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    manifest = json.loads(prepared.manifest_path.read_text())
    assert manifest["maintenance_custody"] == "SEPARATE_API_MOUNT_EXCLUSION_REQUIRED_UNVERIFIED"
    receipt = module.qualify_inputs(
        prepared,
        startup_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
        image_defaults={},
    )
    assert "SEPARATE_NOT_MOUNTED_IN_API" not in json.dumps(manifest)
    assert "maintenance_mount_exclusion" not in receipt
    assert all(value == "NOT_EXECUTED" for value in receipt["runtime_phases"].values())


@pytest.mark.parametrize(
    "artifact", ["profile", "public_key", "workbook", "rights", "budget", "manifest"]
)
def test_changed_artifact_or_manifest_is_rejected_before_child_start(
    tmp_path: Path,
    model_path: Path,
    artifact: str,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    manifest = json.loads(prepared.manifest_path.read_text())
    path = (
        prepared.manifest_path
        if artifact == "manifest"
        else prepared.root / manifest["artifacts"][artifact]["path"]
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    path.write_bytes(path.read_bytes() + b"tamper")
    path.chmod(stat.S_IRUSR)
    calls: list[object] = []
    with pytest.raises(module.PreparationError, match="CI_HEALTH_INPUT_INTEGRITY_REJECTED"):
        module.dispatch_api(
            prepared,
            command=DEFAULT_CMD,
            startup_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
            image_defaults={},
            runner=lambda *args: calls.append(args),
        )
    assert calls == []


def test_no_clobber_and_missing_model_fail_without_creating_output(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    original = prepared.manifest_path.read_bytes()
    with pytest.raises(module.PreparationError, match="CI_HEALTH_FRESH_ROOT_REQUIRED"):
        _prepare(module, tmp_path, model_path)
    assert prepared.manifest_path.read_bytes() == original
    with pytest.raises(module.PreparationError, match="CI_HEALTH_MODEL_INPUT_REQUIRED"):
        module.prepare_inputs(
            output_root=tmp_path / "missing-model-run",
            model_path=tmp_path / "missing",
            synthetic_ci_health=True,
            authority=AUTHORITY,
        )
    assert not (tmp_path / "missing-model-run").exists()


def test_secret_material_cannot_be_prepared_inside_public_source_or_build_context(
    model_path: Path,
) -> None:
    module = _helper()
    unsafe_root = ROOT / ".local_reports/never-create-synthetic-inputs-in-build-context"
    with pytest.raises(module.PreparationError, match="CI_HEALTH_FRESH_ROOT_REQUIRED"):
        module.prepare_inputs(
            output_root=unsafe_root,
            model_path=model_path,
            synthetic_ci_health=True,
            authority=AUTHORITY,
        )
    assert not unsafe_root.exists()


@pytest.mark.parametrize(
    "missing", ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]
)
def test_incomplete_model_presence_is_rejected_without_download_or_output(
    tmp_path: Path,
    model_path: Path,
    missing: str,
) -> None:
    module = _helper()
    (model_path / missing).unlink()
    with pytest.raises(module.PreparationError, match="CI_HEALTH_MODEL_INPUT_REQUIRED"):
        _prepare(module, tmp_path, model_path)
    assert not (tmp_path / "owned").exists()


def test_cli_prepares_only_safe_metadata_and_does_not_clobber(
    tmp_path: Path,
    model_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _helper()
    argv = [
        "--synthetic-ci-health",
        "--authority",
        AUTHORITY,
        "--output-root",
        str(tmp_path / "owned"),
        "--model-path",
        str(model_path),
    ]
    assert module.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {"authority", "purpose", "manifest_sha256", "runtime"}
    assert summary["runtime"] == "NOT_EXECUTED"
    assert module.main(argv) == 1
    assert capsys.readouterr().out.strip() == "CI_HEALTH_FRESH_ROOT_REQUIRED"
    with pytest.raises(SystemExit):
        module.main(argv[1:])


def test_direct_preparation_cli_uses_only_the_selected_checkout_and_safe_output(
    tmp_path: Path,
    model_path: Path,
) -> None:
    child_env = {"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt":
        child_env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            "--synthetic-ci-health",
            "--authority",
            AUTHORITY,
            "--output-root",
            str(tmp_path / "direct-owned"),
            "--model-path",
            str(model_path),
        ],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "CI_HEALTH_DIRECT_PREPARATION_FAILED"
    summary = json.loads(result.stdout)
    assert summary["runtime"] == "NOT_EXECUTED"


def test_zero_call_cap_does_not_become_full_budget_admission(
    tmp_path: Path,
    model_path: Path,
) -> None:
    from idis.providers.budget_policy import (
        ProviderBudgetPolicyError,
        validate_global_provider_budget_max_calls,
    )

    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    loaded = profile_module.load_strict_runtime_profile(
        command="api",
        process_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
    )
    with pytest.raises(ProviderBudgetPolicyError, match="PROVIDER_BUDGET_CONFIG_BLOCKED"):
        validate_global_provider_budget_max_calls(loaded.values["IDIS_PROVIDER_BUDGET_MAX_CALLS"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode assertions execute on Linux")
def test_posix_custody_and_symlink_or_writable_file_fail_closed(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    assert stat.S_IMODE(prepared.profile_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(prepared.profile_path.parent.stat().st_mode) == 0o700
    assert prepared.profile_path.stat().st_uid == os.geteuid()
    startup = {"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    prepared.profile_path.chmod(0o600)
    with pytest.raises(module.PreparationError, match="CI_HEALTH_INPUT_INTEGRITY_REJECTED"):
        module.qualify_inputs(prepared, startup_env=startup, image_defaults={})
    prepared.profile_path.chmod(0o400)
    data = prepared.profile_path.read_bytes()
    prepared.profile_path.unlink()
    target = prepared.root / "unreviewed.env"
    target.write_bytes(data)
    target.chmod(0o400)
    prepared.profile_path.symlink_to(target)
    with pytest.raises(module.PreparationError, match="CI_HEALTH_INPUT_INTEGRITY_REJECTED"):
        module.qualify_inputs(prepared, startup_env=startup, image_defaults={})


def test_unsigned_deny_files_and_invalid_signatures_remain_canonically_rejected(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    loaded = profile_module.load_strict_runtime_profile(
        command="api",
        process_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
    )
    public_key = prepared.public_key_path.read_bytes()
    for name in ("IDIS_ENRICHMENT_RIGHTS_APPROVALS_PATH", "IDIS_PROVIDER_BUDGET_POLICY_PATH"):
        declaration = json.loads(Path(loaded.values[name]).read_text())
        assert declaration["provider_execution"] == "DENY"
        assert declaration["spend_authorized"] is False
        assert declaration["human_approval_claimed"] is False
        with pytest.raises(GovernanceEnvelopeError, match="GOVERNANCE_SCHEMA_INVALID"):
            verify_envelope(
                envelope=declaration,
                trusted_signer_id="ci-no-authority",
                trusted_public_key_pem=public_key,
            )
    # Schema-valid budget payload borrowed from the actual closed component contract;
    # these are INVALID signature negatives, never generated approvals/signatures.
    from idis.providers.budget_policy import EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS

    components = {
        name: {"max_calls": 1, "max_usd": "0.00"} for name in EXPECTED_PROVIDER_BUDGET_COMPONENT_IDS
    }
    components["anthropic"].update(max_input_tokens=1, max_output_tokens=1)
    components["openai_embeddings"].update(max_embedding_tokens=1)
    envelope = {
        "schema_version": "provider-budget-policy-v1",
        "signer_id": "ci-no-authority",
        "payload": {
            "tenant_id": loaded.values["IDIS_ACCEPTANCE_TENANT_ID"],
            "policy_version": "negative-only",
            "currency": "USD",
            "total_max_usd": "0.00",
            "components": components,
        },
        "signature_base64": base64.b64encode(bytes(64)).decode("ascii"),
    }
    with pytest.raises(GovernanceEnvelopeError, match="GOVERNANCE_SIGNATURE_INVALID"):
        verify_envelope(
            envelope=envelope,
            trusted_signer_id="ci-no-authority",
            trusted_public_key_pem=public_key,
        )
    envelope.pop("signature_base64")
    with pytest.raises(GovernanceEnvelopeError, match="GOVERNANCE_SCHEMA_INVALID"):
        verify_envelope(
            envelope=envelope,
            trusted_signer_id="ci-no-authority",
            trusted_public_key_pem=public_key,
        )
    with pytest.raises(
        profile_module.StrictProfileError, match="STRICT_PROFILE_GENERATION_INVALID"
    ):
        profile_module.load_strict_runtime_profile(
            command="worker",
            process_env={"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)},
        )


# Names from the runtime-stage ENV merge on the local base image
# python:3.11-slim-bookworm@sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b.
# The full Dockerfile build did not finish offline; apt does not add ENV names.
_INSPECTED_IMAGE_ENV_NAMES = frozenset(
    {
        "GPG_KEY",
        "IDIS_VERSION",
        "LANG",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "PYTHON_SHA256",
        "PYTHON_VERSION",
    }
)
_LOADER_NAMES = frozenset({"LD_AUDIT", "LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"})


def test_inspected_image_env_names_are_accepted_and_unlisted_name_is_rejected(
    tmp_path: Path,
    model_path: Path,
) -> None:
    module = _helper()
    assert _INSPECTED_IMAGE_ENV_NAMES <= module._IMAGE_CONTROL_NAMES
    assert _LOADER_NAMES.isdisjoint(module._IMAGE_CONTROL_NAMES)
    prepared = _prepare(module, tmp_path, model_path)
    image_defaults = {name: f"observed-{name}" for name in sorted(_INSPECTED_IMAGE_ENV_NAMES)}
    startup = {**image_defaults, "IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    module.qualify_inputs(prepared, startup_env=startup, image_defaults=image_defaults)
    rejected_defaults = {**image_defaults, "UNLISTED_IMAGE_ENV": "rejected"}
    rejected_startup = {**startup, "UNLISTED_IMAGE_ENV": "rejected"}
    with pytest.raises(module.PreparationError, match="CI_HEALTH_STARTUP_ENV_REJECTED"):
        module.qualify_inputs(
            prepared,
            startup_env=rejected_startup,
            image_defaults=rejected_defaults,
        )


def _stand_in_poppler_runtime(root: Path) -> object:
    """Absolute stand-in so the factory can reach the route gate without a host pin."""
    from idis.parsers.poppler_runtime import (
        PINNED_POPPLER_VERSION,
        PinnedPopplerRuntime,
        _runtime_identity_sha256,
    )

    digest = "ab" * 32
    identity = _runtime_identity_sha256(
        version=PINNED_POPPLER_VERSION,
        pdftoppm_sha256=digest,
        poppler_dll_sha256=digest,
    )
    return PinnedPopplerRuntime(
        version=PINNED_POPPLER_VERSION,
        pdftoppm_sha256=digest,
        poppler_dll_sha256=digest,
        runtime_identity_sha256=identity,
        _pdftoppm_path=(root / "pdftoppm").resolve(),
        _pdfinfo_path=(root / "pdfinfo").resolve(),
        _poppler_library_path=(root / "library").resolve(),
        _platform_name="composition-test",
    )


def _admitted_route_observation(app: object) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Return admitted method/path keys and gate anomaly tokens. No paths from disk."""
    from fastapi.routing import APIRoute, iter_route_contexts

    from idis.runtime.task4_ingestion_scope import TASK4_SERVER_ROUTE_INVENTORY

    observed: list[tuple[str, str]] = []
    anomalies: list[str] = []
    routes = app.router.routes  # type: ignore[attr-defined]
    for context in iter_route_contexts(routes):
        path = context.path
        methods = context.methods
        if not isinstance(path, str) or not methods:
            continue
        keys = tuple((str(method).upper(), path) for method in methods)
        admitted = tuple(key for key in keys if key in TASK4_SERVER_ROUTE_INVENTORY)
        if not admitted:
            continue
        if len(admitted) != len(keys):
            anomalies.append("MIXED_METHODS")
        if not isinstance(context.original_route, APIRoute):
            anomalies.append("NON_APIROUTE")
        if not callable(context.endpoint):
            anomalies.append("UNCALLABLE")
        if context.stream_item_type is not None:
            anomalies.append("STREAM_ITEM")
        if context.is_json_stream:
            anomalies.append("JSON_STREAM")
        if context.is_sse_stream:
            anomalies.append("SSE_STREAM")
        observed.extend(admitted)
    return tuple(observed), tuple(anomalies)


def _raising_admission_probe() -> object:
    """Fail closed without a socket, a DSN, or a filesystem write."""
    raise RuntimeError("stubbed admission probe")


def _stub_admission_adapters(*_args: object, **_kwargs: object) -> object:
    """Replace production adapters before any real dependency probe runs."""
    from idis.runtime.dependency_probes import DurableDependencyProbeAdapters

    return DurableDependencyProbeAdapters(
        postgres=_raising_admission_probe,
        audit=_raising_admission_probe,
        idempotency=_raising_admission_probe,
        redis=_raising_admission_probe,
        object_store=_raising_admission_probe,
        ingestion=_raising_admission_probe,
        worker=_raising_admission_probe,
        budgets=_raising_admission_probe,
        erasure_executor=_raising_admission_probe,
    )


def _outside_tmp(path: Path, tmp_path: Path) -> Path | None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(tmp_path.resolve())
    except ValueError:
        return resolved
    return None


_CI_HEALTH_ROOT = Path("/var/lib/idis-ci-health")
_ADMISSION_CONTAINER_EXPR = 'f"idis-ci-admission-codes-{run}"'
_ADMISSION_DOCKER_TIMEOUT_SECONDS = 480
_ADMISSION_CODES = frozenset(
    {
        "AUDIT_EMIT_FAILED",
        "AUDIT_FAILURE",
        "AUTH_ACCEPTANCE_KEY_MISMATCH",
        "AUTH_CANARY_KEY_MISMATCH",
        "AUTH_CONFIG_BLOCKED",
        "DATABASE_SCHEMA_NOT_READY",
        "DATABASE_UNAVAILABLE",
        "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID",
        "ERASURE_EXECUTOR_CONFIG_BLOCKED",
        "ERASURE_EXECUTOR_DATABASE_MISMATCH",
        "ERASURE_EXECUTOR_POSTURE_BLOCKED",
        "ERASURE_EXECUTOR_UNAUTHORIZED",
        "GOVERNANCE_ADMIN_UNAUTHORIZED",
        "IDEMPOTENCY_NON_DURABLE_BLOCKED",
        "IDEMPOTENCY_STORE_FAILED",
        "MEDIA_LARGE_UPLOAD_UNSUPPORTED",
        "OBJECT_STORE_HEALTH_BLOCKED",
        "PROVIDER_BUDGET_CONFIG_BLOCKED",
        "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED",
        "RATE_LIMIT_STORE_UNAVAILABLE",
        "RESIDENCY_SERVICE_REGION_UNSET",
        "RLS_HEALTH_BLOCKED",
        "STRICT_FULL_LIVE_BLOCKED",
        "STRICT_PROCESS_ROLE_INVALID",
        "WATERMARK_BATCH_ATTESTATION_MISMATCH",
        "WORKER_CLAIM_NOT_PROVEN",
        "WORKER_LEASE_CONFIG_INVALID",
        "WORKER_TENANT_SCOPE_MISSING",
    }
)
_DIAGNOSTIC_UNAVAILABLE = json.dumps(
    {"code": "DIAGNOSTIC_UNAVAILABLE", "phase": "admission"},
    sort_keys=True,
)
_INJECTED_REVISION = "postgresql://idis_app:s3cret-token@127.0.0.1/idis"


def _path_inventory(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.exists():
        return ()
    if path.is_file():
        return (path.name,)
    entries = tuple(sorted(str(item.relative_to(path)) for item in path.rglob("*")))
    if entries:
        return entries
    if path.is_dir():
        return (".",)
    return ()


def _outside_inventory_root(path: Path, tmp_path: Path) -> Path | None:
    """First ancestor outside tmp. Paths under the CI health root inventory that root."""
    outside = _outside_tmp(path, tmp_path)
    if outside is None:
        return None
    health = _CI_HEALTH_ROOT.resolve(strict=False)
    resolved = outside.resolve(strict=False)
    try:
        resolved.relative_to(health)
    except ValueError:
        return outside
    return health


def test_composition_api_closed_code_is_admission_before_the_route_inventory(
    tmp_path: Path,
    model_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composition launcher raises before create_app; the route gate admits 13 keys.

    The synthetic profile is the real helper file loaded by ``load_strict_runtime_profile``.
    Product gate and inventory are not modified.
    """
    import argparse

    from idis.api import main as api_main
    from idis.runtime import strict_launcher as launcher
    from idis.runtime.task4_ingestion_scope import TASK4_SERVER_ROUTE_INVENTORY
    from idis.services.ingestion import defaults as ingestion_defaults

    module = _helper()
    prepared = _prepare(module, tmp_path, model_path)
    startup = {"IDIS_STRICT_DOTENV_PATH": str(prepared.profile_path)}
    profile = profile_module.load_strict_runtime_profile(command="api", process_env=dict(startup))
    assert profile.generation == "bootstrap-v1"
    assert profile.command == "api"
    assert profile.values["IDIS_REQUIRE_FULL_LIVE"] == "1"
    base = Path(profile.values["IDIS_OBJECT_STORE_BASE_DIR"])
    outside_store = _outside_inventory_root(base, tmp_path)
    store_before = _path_inventory(outside_store)

    factory_code = "CREATE_APP_OK"
    try:
        api_main.create_app(strict_profile=profile)
    except Exception as exc:
        factory_code = str(getattr(exc, "code", "") or exc)
    assert factory_code != "STRICT_FULL_LIVE_BLOCKED"
    assert "STRICT_FULL_LIVE_BLOCKED" not in factory_code

    observed: list[tuple[str, str]] = []
    anomalies: list[str] = []
    gate_calls: list[str] = []
    original = api_main._restrict_task4_ingestion_routes

    def probe(app: object) -> None:
        gate_calls.append("gate")
        found, flags = _admitted_route_observation(app)
        observed.extend(found)
        anomalies.extend(flags)
        original(app)

    monkeypatch.setattr(api_main, "_restrict_task4_ingestion_routes", probe)
    monkeypatch.setattr(
        ingestion_defaults,
        "resolve_pinned_poppler_runtime",
        lambda: _stand_in_poppler_runtime(tmp_path / "poppler-stand-in"),
    )
    api_main.create_app(strict_profile=profile)
    assert gate_calls == ["gate"]
    assert anomalies == []
    assert len(observed) == len(TASK4_SERVER_ROUTE_INVENTORY)
    assert frozenset(observed) == TASK4_SERVER_ROUTE_INVENTORY

    launch_gate: list[str] = []

    def launch_probe(app: object) -> None:
        launch_gate.append("gate")
        original(app)

    monkeypatch.setattr(api_main, "_restrict_task4_ingestion_routes", launch_probe)
    from idis.runtime import dependency_probes

    monkeypatch.setattr(
        dependency_probes,
        "build_default_dependency_probe_adapters",
        _stub_admission_adapters,
    )
    seen: dict[str, object] = {}
    real_builder = launcher._default_admission_runner

    def builder(
        command: str,
        admitted_profile: object,
        *,
        task4_ingestion_scope: bool = False,
    ) -> object:
        inner = real_builder(
            command,
            admitted_profile,  # type: ignore[arg-type]
            task4_ingestion_scope=task4_ingestion_scope,
        )

        def run() -> object:
            report = inner()
            seen["may"] = report.may_proceed
            seen["codes"] = tuple(report.blocker_codes)
            seen["scope"] = report.admission_scope
            seen["checks"] = tuple((check.component, check.code) for check in report.checks)
            return report

        return run

    monkeypatch.setattr(launcher, "_default_admission_runner", builder)
    args = argparse.Namespace(host="0.0.0.0", port=8000, pid_file=str(tmp_path / "api.pid"))
    with pytest.raises(launcher.StrictLauncherError) as caught:
        launcher._run_api(api_main, args, profile)
    assert caught.value.code == "STRICT_FULL_LIVE_BLOCKED"
    assert str(caught.value) == "STRICT_FULL_LIVE_BLOCKED"
    assert launch_gate == []
    assert seen["may"] is False
    assert seen["scope"] == "task4-ingestion-v1"
    codes = seen["codes"]
    assert isinstance(codes, tuple)
    assert "STRICT_FULL_LIVE_BLOCKED" not in codes
    # A raised idempotency probe is not IdempotencyProbeFacts, so readiness
    # selects IDEMPOTENCY_NON_DURABLE_BLOCKED (dependency_readiness.py:1050-1052).
    assert codes == (
        "DATABASE_UNAVAILABLE",
        "AUDIT_FAILURE",
        "IDEMPOTENCY_NON_DURABLE_BLOCKED",
        "RATE_LIMIT_STORE_UNAVAILABLE",
        "OBJECT_STORE_HEALTH_BLOCKED",
        "MEDIA_LARGE_UPLOAD_UNSUPPORTED",
    )
    checks = seen["checks"]
    assert isinstance(checks, tuple)
    assert [component for component, _code in checks] == [
        "postgres",
        "audit",
        "idempotency",
        "redis",
        "object_store",
        "ingestion",
        "auth",
    ]
    assert dict(checks)["auth"] is None
    assert [code for _component, code in checks] == [*codes, None]
    assert _path_inventory(outside_store) == store_before


# Image install layout from Dockerfile: non-editable /opt/venv and schemas at /app/schemas.
_IMAGE_SITE_PACKAGES = Path("opt/venv/lib/python3.11/site-packages")
_INSTALLED_SCHEMA_MODULE = "h1b_installed_schema_validator"
_FROZEN_PREVIEW_ENV = (
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_URL",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT",
    "IDIS_TEST_FROZEN_PREVIEW_DATABASE_RECEIPT_SHA256",
)
_REQUIRE_COMPOSITION_AUDIT_INSERT = "IDIS_REQUIRE_COMPOSITION_AUDIT_INSERT"


def _readiness_audit_event() -> dict[str, object]:
    """Same event collect_postgres_audit_facts builds before validate_audit_event."""
    event_id = str(uuid.uuid4())
    return {
        "event_id": event_id,
        "tenant_id": "10000000-0000-4000-8000-000000000001",
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_type": "integration.dependency_readiness.probed",
        "severity": "LOW",
        "summary": "Durable dependency audit round-trip probed",
        "actor": {"actor_id": "strict-runtime-readiness", "actor_type": "SERVICE"},
        "request": {
            "request_id": f"readiness-{event_id}",
            "method": "POST",
            "path": "/internal/dependency-readiness/audit",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "integration",
            "resource_id": f"dependency-readiness:{event_id}",
        },
        "payload": {"safe": {"backend": "postgres"}, "hashes": [], "refs": []},
    }


def _load_installed_schema_validator(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(_INSTALLED_SCHEMA_MODULE, path)
    if spec is None or spec.loader is None:
        raise AssertionError("installed schema validator spec missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_INSTALLED_SCHEMA_MODULE] = module
    spec.loader.exec_module(module)
    return module


def test_noneditable_image_install_loads_the_readiness_audit_schema(tmp_path: Path) -> None:
    """Copied schema_validator.py loads idis/_schemas beside that module.

    Form (1) resolves a path next to the loaded file, so this copy uses the
    temporary site-packages tree. It does not use importlib.resources.
    """
    from idis.validators.schema_validator import SchemaValidator

    event = _readiness_audit_event()
    source = SchemaValidator().validate("audit_event", event)
    assert source.passed, [error.code for error in source.errors]

    site = tmp_path / _IMAGE_SITE_PACKAGES
    module_path = site / "idis" / "validators" / "schema_validator.py"
    module_path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "src/idis/validators/schema_validator.py", module_path)
    packaged = site / "idis" / "_schemas"
    image_schemas = tmp_path / "app" / "schemas"
    packaged.mkdir()
    image_schemas.mkdir(parents=True)
    for schema in sorted((ROOT / "schemas").glob("*.schema.json")):
        shutil.copyfile(schema, packaged / schema.name)
        shutil.copyfile(schema, image_schemas / schema.name)

    module = _load_installed_schema_validator(module_path)
    try:
        validator = module.SchemaValidator()
        loaded = module.__file__
        assert isinstance(loaded, str)
        chosen = Path(validator._schema_dir).resolve()
        walk = Path(loaded).resolve().parents[3] / "schemas"
        assert (walk / "audit_event.schema.json").is_file() is False
        assert (image_schemas / "audit_event.schema.json").is_file()
        assert (packaged / "audit_event.schema.json").is_file()
        assert chosen == packaged.resolve()
        assert chosen != image_schemas.resolve()
        result = validator.validate("audit_event", event)
    finally:
        sys.modules.pop(_INSTALLED_SCHEMA_MODULE, None)

    details = [f"{error.code}:{error.message}" for error in result.errors]
    assert result.passed, details
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    openapi_line = '"openapi/IDIS_OpenAPI_v6_3.yaml" = "idis/_openapi/IDIS_OpenAPI_v6_3.yaml"'
    assert project.count(openapi_line) == 1
    for schema in sorted((ROOT / "schemas").glob("*.schema.json")):
        mapping = f'"schemas/{schema.name}" = "idis/_schemas/{schema.name}"'
        assert mapping in project


def _assert_composition_audit_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.persistence import db
    from idis.runtime.dependency_probes import collect_postgres_audit_facts
    from tests._cir04c3_required_frozen_preview import required_frozen_preview

    with required_frozen_preview(monkeypatch):
        facts = collect_postgres_audit_facts(
            audit_sink_factory=PostgresAuditSink,
            connection_factory=db.begin_app_conn,
            tenant_setter=db.set_tenant_local,
            tenant_id=str(uuid.uuid4()),
        )
    assert facts.backend == "postgres"
    assert facts.emit_ok is True
    assert facts.read_ok is True
    assert facts.restart_read_ok is True


def test_composition_audit_sink_insert_records_not_executed_without_a_qualified_database(
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    """Opt-in app-role INSERT. The default records NOT_EXECUTED and does not connect."""
    if os.environ.get(_REQUIRE_COMPOSITION_AUDIT_INSERT) != "1":
        reason = "IDIS_REQUIRE_COMPOSITION_AUDIT_INSERT is not 1"
        record_property("audit_sink_insert", f"NOT_EXECUTED: {reason}")
        print(f"AUDIT_SINK_INSERT NOT_EXECUTED: {reason}")
        return
    missing = [name for name in _FROZEN_PREVIEW_ENV if not os.environ.get(name)]
    if missing:
        pytest.fail("qualified database unavailable")
    _assert_composition_audit_insert(monkeypatch)


def _literal_blocked_codes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_blocked" or len(node.args) < 2:
            continue
        code = node.args[1]
        if isinstance(code, ast.Constant) and isinstance(code.value, str):
            codes.add(code.value)
    return codes


def _public_token_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        value = node.value.value
        if not isinstance(value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == value:
                found.add(value)
    return found


def _assigned_frozenset(source: str, name: str) -> frozenset[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        call = node.value
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Name)
            or call.func.id != "frozenset"
            or len(call.args) != 1
            or not isinstance(call.args[0], ast.Set)
        ):
            raise AssertionError(f"{name} is not a frozenset of literals")
        values: set[str] = set()
        for element in call.args[0].elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                raise AssertionError(f"{name} contains a non-literal")
            values.add(element.value)
        return frozenset(values)
    raise AssertionError(f"{name} is missing")


def _h1b_body(run: str) -> str:
    lines = run.splitlines()
    start = next(index for index, line in enumerate(lines) if "<<'H1B_PY'" in line)
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line == "H1B_PY":
            return "\n".join(body) + "\n"
        body.append(line)
    raise AssertionError("H1B_PY heredoc is unterminated")


def _admission_host() -> str:
    run = _by_id("admission-codes")["run"]
    assert isinstance(run, str)
    return _h1b_body(run)


def _inner_admission_script(host: str) -> str:
    marker = 'script = r"""'
    start = host.find(marker)
    if start < 0:
        raise AssertionError("admission step has no inner script")
    rest = host[start + len(marker) :]
    end = rest.find('"""')
    if end < 0:
        raise AssertionError("inner script is unterminated")
    return rest[:end]


def _workflow_admission_codes() -> frozenset[str]:
    return _assigned_frozenset(_admission_host(), "_ADMISSION_CODES")


def test_admission_allowlist_covers_blocked_sites_and_strict_auth() -> None:
    """The 28 public codes cover every readiness _blocked literal and strict_auth token."""
    codes = _workflow_admission_codes()
    blocked = _literal_blocked_codes(ROOT / "src/idis/runtime/dependency_readiness.py")
    blocked |= _literal_blocked_codes(ROOT / "src/idis/runtime/strict_launcher.py")
    auth = _public_token_constants(ROOT / "src/idis/runtime/strict_auth.py")
    assert auth == {
        "AUTH_ACCEPTANCE_KEY_MISMATCH",
        "AUTH_CANARY_KEY_MISMATCH",
        "AUTH_CONFIG_BLOCKED",
        "GOVERNANCE_ADMIN_UNAUTHORIZED",
        "RESIDENCY_SERVICE_REGION_UNSET",
    }
    assert sorted((blocked | auth) - codes) == []
    assert codes == _ADMISSION_CODES
    assert len(codes) == 28


def _execute_admission(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    exit_code: str = "STRICT_FULL_LIVE_BLOCKED",
    exit_document: dict[str, object] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    stdout = "{}\n" if payload is None else json.dumps(payload, sort_keys=True) + "\n"
    run_root, state = _prepare_admission_case(
        tmp_path,
        stdout=stdout,
        stderr="",
        code=0,
        exit_code=exit_code,
    )
    if exit_document is not None:
        (run_root / "api-exit.json").write_text(
            json.dumps(exit_document, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    completed, _script_path = _run_admission_case(tmp_path, run_root, state)
    return completed, state


def test_admission_auth_reason_codes_stay_public(tmp_path: Path) -> None:
    """The four strict_auth tokens are printed, not collapsed to UNLISTED."""
    payload = {
        "phase": "admission",
        "checks": [
            ["auth", "AUTH_ACCEPTANCE_KEY_MISMATCH"],
            ["auth", "AUTH_CANARY_KEY_MISMATCH"],
            ["auth", "GOVERNANCE_ADMIN_UNAUTHORIZED"],
            ["auth", "RESIDENCY_SERVICE_REGION_UNSET"],
            ["postgres", "DATABASE_UNAVAILABLE"],
        ],
    }
    completed, _state = _execute_admission(tmp_path, payload=payload)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == json.dumps(payload, sort_keys=True)
    assert "UNLISTED" not in output


def _postgres_payload(version: object) -> dict[str, object]:
    return {
        "phase": "admission",
        "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        "postgres": {
            "alembic_version": version,
            "relforcerowsecurity": True,
            "schema_at_head": True,
        },
    }


def _postgres_public(*, version: str | None) -> dict[str, object]:
    postgres: dict[str, object] = {
        "relforcerowsecurity": True,
        "schema_at_head": True,
    }
    if version is not None:
        postgres["alembic_version"] = version
    return {
        "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        "phase": "admission",
        "postgres": postgres,
    }


@pytest.mark.parametrize(
    ("version", "kept"),
    [
        ("0038_head", "0038_head"),
        ("rev\u00e9", None),
        ("a" * 65, None),
        (_INJECTED_REVISION, None),
    ],
    ids=("ascii", "non_ascii", "overlong", "injected"),
)
def test_admission_revision_id_drops_non_ascii_and_overlong(
    tmp_path: Path,
    version: str,
    kept: str | None,
) -> None:
    """A revision outside [A-Za-z0-9_]{1,64} is omitted and never echoed."""
    completed, _state = _execute_admission(tmp_path, payload=_postgres_payload(version))
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == json.dumps(_postgres_public(version=kept), sort_keys=True)
    if kept is None:
        assert version not in output


def test_admission_postgres_non_bool_is_closed(tmp_path: Path) -> None:
    """A non-bool Postgres fact closes the line and is not echoed."""
    payload = {
        "phase": "admission",
        "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        "postgres": {"relforcerowsecurity": "not-a-bool", "schema_at_head": True},
    }
    completed, _state = _execute_admission(tmp_path, payload=payload)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == _DIAGNOSTIC_UNAVAILABLE
    assert "not-a-bool" not in output


def test_admission_postgres_extra_key_is_closed(tmp_path: Path) -> None:
    """An extra Postgres key closes the line and the value is not echoed."""
    payload = {
        "phase": "admission",
        "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        "postgres": {"connection": _INJECTED_REVISION, "schema_at_head": True},
    }
    completed, _state = _execute_admission(tmp_path, payload=payload)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == _DIAGNOSTIC_UNAVAILABLE
    assert _INJECTED_REVISION not in output


def test_admission_extra_top_level_key_is_closed(tmp_path: Path) -> None:
    """An extra top-level key is closed by the host issubset check.

    ``.github/workflows/ci.yml`` line 1758: ``set(payload).issubset`` of
    ``{"checks", "phase", "postgres"}`` calls ``_unavailable()``, which prints
    ``DIAGNOSTIC_UNAVAILABLE``. The extra key is not echoed.
    """
    payload = {
        "phase": "admission",
        "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        "unlisted_top_level": "must-not-echo",
    }
    completed, _state = _execute_admission(tmp_path, payload=payload)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == _DIAGNOSTIC_UNAVAILABLE
    assert "must-not-echo" not in output
    assert "unlisted_top_level" not in output


@pytest.mark.parametrize(
    "exit_document",
    [
        {"code": "POPPLER_RUNTIME_UNAVAILABLE", "exit": 1},
        {"exit": 0},
    ],
    ids=("other_code", "api_started"),
)
def test_admission_step_is_a_noop_for_other_exit_codes(
    tmp_path: Path,
    exit_document: dict[str, object],
) -> None:
    """Step 23 prints nothing and does not start docker unless admission blocked."""
    completed, state = _execute_admission(
        tmp_path,
        payload={"phase": "admission", "checks": [["auth", "AUTH_ACCEPTANCE_KEY_MISMATCH"]]},
        exit_document=exit_document,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout == ""
    assert "AUTH_ACCEPTANCE_KEY_MISMATCH" not in output
    assert not (state / "argv.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "phase": "admission",
            "checks": [["not_a_component", "DATABASE_UNAVAILABLE"]],
        },
        {
            "phase": "admission",
            "checks": [["postgres", "DATABASE_UNAVAILABLE", _INJECTED_REVISION]],
        },
    ],
    ids=("unknown_component", "extra_check_field"),
)
def test_admission_unknown_component_or_extra_check_key_is_closed(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    """An unknown component or a wider check row closes to DIAGNOSTIC_UNAVAILABLE."""
    completed, _state = _execute_admission(tmp_path, payload=payload)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == _DIAGNOSTIC_UNAVAILABLE
    assert "not_a_component" not in output
    assert _INJECTED_REVISION not in output


def test_admission_step_names_the_container_and_times_out(tmp_path: Path) -> None:
    """Step 23 is bounded and names the probe with the run id Cleanup removes."""
    step = _by_id("admission-codes")
    assert step.get("timeout-minutes") == 10
    host = _admission_host()
    assert f"timeout={_ADMISSION_DOCKER_TIMEOUT_SECONDS}" in host
    assert f'"--name", {_ADMISSION_CONTAINER_EXPR}' in host
    cleanup = _h1b_body(_by_id("cleanup")["run"])
    assert _ADMISSION_CONTAINER_EXPR in cleanup
    assert '"rm", "-f"' in cleanup
    completed, state = _execute_admission(
        tmp_path,
        payload={
            "phase": "admission",
            "checks": [["postgres", "DATABASE_UNAVAILABLE"]],
        },
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    args = json.loads((state / "argv.json").read_text(encoding="utf-8"))
    assert args[0] == "run"
    assert "--name" in args
    network = args[args.index("--network") + 1]
    assert network.startswith("container:idis-h1b-netns-")
    run_id = network.removeprefix("container:idis-h1b-netns-")
    assert args[args.index("--name") + 1] == f"idis-ci-admission-codes-{run_id}"


_INNER_PRELUDE = r"""
import os
import sys
from types import ModuleType


def _mod(name, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    if "." in name:
        parent_name, child = name.rsplit(".", 1)
        parent = sys.modules[parent_name]
        setattr(parent, child, module)
    return module


class _Check:
    def __init__(self, component, code):
        self.component = component
        self.code = code


class _Report:
    checks = (
        _Check("auth", "AUTH_ACCEPTANCE_KEY_MISMATCH"),
        _Check("postgres", "DATABASE_UNAVAILABLE"),
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        if not self._rows:
            return None
        return self._rows[0]

    def all(self):
        return list(self._rows)


class _Conn:
    def execute(self, statement):
        sql = str(statement)
        if "alembic_version" in sql:
            return _Rows([(os.environ["H1B_FAKE_REVISION"],)])
        if "pg_roles" in sql:
            return _Rows([(False, False)])
        if "pg_class" in sql:
            return _Rows([("tenant_table", True)])
        raise RuntimeError("unexpected sql")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _begin():
    if os.environ.get("H1B_FAKE_QUERY_FAIL") == "1":
        raise RuntimeError("postgresql://idis_app:s3cret-token@127.0.0.1/idis")
    return _Conn()


def _load(command="api"):
    if command != "api":
        raise RuntimeError("unexpected command")
    return ({}, None)


def _runner(command, profile, task4_ingestion_scope=False):
    if command != "api" or task4_ingestion_scope is not True:
        raise RuntimeError("unexpected admission call")
    return lambda: _Report()


def _head():
    return "0038_head"


_mod("idis")
_mod("idis.persistence")
_mod("idis.persistence.db", begin_app_conn=_begin)
_mod("idis.runtime")
_mod(
    "idis.runtime.dependency_probes",
    TENANT_RLS_TABLES=("tenant_table",),
    _expected_alembic_head=_head,
)
_mod(
    "idis.runtime.strict_launcher",
    _default_admission_runner=_runner,
    load_profile_then_import_runtime=_load,
)
_mod("sqlalchemy", text=lambda sql: sql)
"""


def _inner_env(revision: str, *, fail_query: bool = False) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
        "H1B_FAKE_REVISION": revision,
    }
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        env["WINDIR"] = os.environ.get("WINDIR", os.environ["SYSTEMROOT"])
        env["PATHEXT"] = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    if fail_query:
        env["H1B_FAKE_QUERY_FAIL"] = "1"
    return env


def _run_inner_admission(
    tmp_path: Path,
    *,
    revision: str,
    fail_query: bool = False,
) -> subprocess.CompletedProcess[str]:
    host = _admission_host()
    script = tmp_path / "inner_admission.py"
    script.write_text(
        _INNER_PRELUDE + "\n" + _inner_admission_script(host),
        encoding="utf-8",
        newline="\n",
    )
    return subprocess.run(
        [sys.executable, os.fspath(script)],
        cwd=tmp_path,
        env=_inner_env(revision, fail_query=fail_query),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _inner_public(*, version: str | None, schema_at_head: bool) -> str:
    postgres: dict[str, object] = {
        "relforcerowsecurity": True,
        "rolbypassrls": False,
        "rolsuper": False,
        "schema_at_head": schema_at_head,
    }
    if version is not None:
        postgres["alembic_version"] = version
    payload = {
        "checks": [
            ["auth", "AUTH_ACCEPTANCE_KEY_MISMATCH"],
            ["postgres", "DATABASE_UNAVAILABLE"],
        ],
        "phase": "admission",
        "postgres": postgres,
    }
    return json.dumps(payload, sort_keys=True)


@pytest.mark.parametrize(
    ("revision", "kept", "at_head"),
    [
        ("0038_head", "0038_head", True),
        ("rev\u00e9", None, False),
        ("a" * 65, None, False),
    ],
    ids=("ascii", "non_ascii", "overlong"),
)
def test_admission_inner_script_prints_exact_public_line(
    tmp_path: Path,
    revision: str,
    kept: str | None,
    at_head: bool,
) -> None:
    """The inner Python prints one filtered admission line for the canned facts."""
    completed = _run_inner_admission(tmp_path, revision=revision)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == _inner_public(version=kept, schema_at_head=at_head)
    if kept is None:
        assert revision not in output


def test_admission_inner_script_omits_postgres_when_the_query_fails(tmp_path: Path) -> None:
    """A probe exception drops the facts and does not echo the exception text."""
    completed = _run_inner_admission(tmp_path, revision="0038_head", fail_query=True)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert completed.stdout.strip() == json.dumps(
        {
            "checks": [
                ["auth", "AUTH_ACCEPTANCE_KEY_MISMATCH"],
                ["postgres", "DATABASE_UNAVAILABLE"],
            ],
            "phase": "admission",
        },
        sort_keys=True,
    )
    assert "s3cret-token" not in output
    assert "postgresql://" not in output


def _mkdir_missing(path: Path) -> list[Path]:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
    return missing


def _remove_created(created: list[Path]) -> None:
    for directory in created:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def test_path_inventory_distinguishes_absent_from_empty_directory(tmp_path: Path) -> None:
    """An empty directory outside tmp is visible; a missing path stays empty."""
    parent = tmp_path.parent / f"h1bo-outside-{os.getpid()}"
    created = _mkdir_missing(parent)
    empty = parent / "empty"
    empty.mkdir()
    created.insert(0, empty)
    absent = parent / "absent"
    try:
        assert _outside_tmp(empty, tmp_path) is not None
        assert _path_inventory(absent) == ()
        assert _path_inventory(empty) == (".",)
    finally:
        _remove_created(created)


def test_empty_directory_under_the_health_root_is_inventoried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing object leaf does not hide an empty directory on the health root."""
    real_health = _CI_HEALTH_ROOT
    health = tmp_path / "health-root"
    health.mkdir()
    boundary = tmp_path / "inventory-boundary"
    boundary.mkdir()
    monkeypatch.setattr(sys.modules[__name__], "_CI_HEALTH_ROOT", health)
    name = f"h1bo{os.getpid()}"
    empty = health / name
    created = _mkdir_missing(empty)
    leaf = empty / "objects"
    try:
        outside = [path for path in created if _outside_tmp(path, tmp_path) is not None]
        assert outside == [], outside
        assert _path_inventory(leaf) == ()
        found = _path_inventory(_outside_inventory_root(leaf, boundary))
        assert any(item.replace("\\", "/").split("/")[0] == name for item in found)
    finally:
        _remove_created(created)
    assert real_health.exists() is False
