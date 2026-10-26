"""Hermetic contracts for the production v16 signed-census CLI.

The real 219-case PostgreSQL census is deliberately replaced at the single
execution seam.  Receipt construction and verification still run through the
hash-pinned production census runner over the exact repository candidate.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.slice100_task2.test_strict_profile_launcher import (
    _make_integrity_file_writable,
    _profile_values,
    _rewrite_integrity_file,
    _seal_integrity_file,
)
from tests.slice100_task7 import _v16_atomic_admission_support as receipt_support

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_PATH = _REPO_ROOT / "scripts" / "run_task7_v16_signed_census.py"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v16_contract_fixture.py"
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_APPROVED_RUNNER_SHA256 = "6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43"
_APPROVED_HARNESS_IDENTITY = (
    "task7-v16-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v2:"
    "ed15c8f2391017cf42753be102dd1a4d69d6ea31345baa491cf9e86c6c4ed59c"
)
_APPROVED_SIGNER_ID = "slice100-governance-signer-v1"
_SECRET_SENTINEL = "postgresql+psycopg2://admin:do-not-print@db/private"


def _load_cli(name: str = "_task7_v16_signed_census_cli_test") -> ModuleType:
    assert _CLI_PATH.is_file(), "TASK7_V16_SIGNED_CENSUS_CLI_MISSING"
    spec = importlib.util.spec_from_file_location(name, _CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_fixture_generator() -> ModuleType:
    # The census runner deliberately no longer exports its mutable generator;
    # receipt fixtures use the independently tested publisher module instead.
    name = "_task7_v16_signed_census_fixture_generator"
    existing = sys.modules.get(name)
    if isinstance(existing, ModuleType):
        return existing
    spec = importlib.util.spec_from_file_location(name, _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _replay_constructor_authority_loads(runner: ModuleType) -> None:
    """Complete only the source-loading half skipped by the fake census seam."""

    # The real PostgreSQL census reaches these exact authorities through
    # _run_constructors_as_app; the unit seam replaces the database half only.
    with runner._HARNESS_SOURCE_STORE.activation():
        native = importlib.import_module(
            "tests.slice100_task7.test_full_strict_v9_native_schema_red"
        )
        payload = native._vep_payload()
        assert payload["tenant_id"]
        runner._HARNESS_SOURCE_STORE.assert_exact_closure()


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _write_secure_private_key(path: Path, raw: bytes) -> None:
    from idis.runtime.secure_files import protect_path_for_current_user

    path.write_bytes(raw)
    protect_path_for_current_user(path)
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _operator_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    private_key: Ed25519PrivateKey | None = None,
    public_key: Ed25519PrivateKey | None = None,
    signer_id: str = _APPROVED_SIGNER_ID,
    omit_erasure_executor: bool = False,
) -> tuple[Path, Path, bytes, dict[str, str]]:
    operator_root = tmp_path / "operator"
    values = _profile_values(operator_root, generation="bootstrap-v1", promoted="0")
    signing_key = private_key or Ed25519PrivateKey.generate()
    trust_key = public_key or signing_key
    public_raw = _public_pem(trust_key)
    public_path = Path(values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"])
    _rewrite_integrity_file(public_path, public_raw)

    private_path = operator_root / "governance-signing-private.pem"
    _write_secure_private_key(private_path, _private_pem(signing_key))
    values["IDIS_GOVERNANCE_TRUSTED_SIGNER_ID"] = signer_id
    values["IDIS_DATABASE_ADMIN_URL"] = _SECRET_SENTINEL
    if omit_erasure_executor:
        values.pop("IDIS_ERASURE_DATABASE_URL", None)

    dotenv_path = operator_root / "strict-bootstrap-v1.env"
    dotenv_path.write_text(
        "\n".join(f"{name}={value}" for name, value in sorted(values.items())) + "\n",
        encoding="utf-8",
    )
    _seal_integrity_file(dotenv_path)

    for name in (*values, "IDIS_PROCESS_ROLE", "IDIS_STRICT_DOTENV_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IDIS_STRICT_DOTENV_PATH", str(dotenv_path.resolve()))
    return dotenv_path, private_path, public_raw, values


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_cli()
    generator = _load_fixture_generator()
    candidate_path = tmp_path / _ARTIFACT_PATH.name
    candidate_path.write_bytes(generator._candidate_from_frozen(_ARTIFACT_PATH.read_bytes()))
    monkeypatch.setattr(module, "_ARTIFACT_PATH", candidate_path)
    return module


@pytest.fixture
def approved_result(cli: ModuleType) -> dict[str, Any]:
    runner = cli._load_approved_runner()
    artifact_raw, _artifact = runner._decode_artifact(cli._ARTIFACT_PATH)
    return receipt_support.census_result(
        generator=_load_fixture_generator(),
        candidate_raw=artifact_raw,
    )


@pytest.fixture
def output_root(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "safe-reports"
    root.mkdir()
    monkeypatch.setattr(cli, "_REPORT_ROOT", root.resolve())
    return root


def _invoke(
    cli: ModuleType,
    output: Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    status = cli.main(["--output", str(output)])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert isinstance(event, dict)
    assert captured.err == ""
    return status, event, captured.out


def _assert_code(exc: BaseException, expected: str) -> None:
    assert getattr(exc, "code", None) == expected
    assert str(exc) == expected


def test_cli_pins_the_approved_runner_before_import_or_use(cli: ModuleType) -> None:
    assert hashlib.sha256(_RUNNER_PATH.read_bytes()).hexdigest() == _APPROVED_RUNNER_SHA256
    assert cli._APPROVED_RUNNER_SHA256 == _APPROVED_RUNNER_SHA256
    runner = cli._load_approved_runner()
    assert runner.HARNESS_IDENTITY == _APPROVED_HARNESS_IDENTITY


def test_runner_hash_mismatch_fails_before_untrusted_source_executes(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "source-executed"
    tampered = tmp_path / "tampered_runner.py"
    tampered.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_RUNNER_PATH", tampered)

    with pytest.raises(cli.SignedCensusCliError) as caught:
        cli._load_approved_runner()

    _assert_code(caught.value, "TASK7_V16_SIGNED_CENSUS_RUNNER_UNAUTHORIZED")
    assert not marker.exists()


def test_existing_output_is_rejected_before_profile_or_census(
    cli: ModuleType,
    output_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = output_root / "receipt.json"
    output.write_bytes(b"sentinel")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    calls = 0

    def should_not_run(**_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("census must not run")

    monkeypatch.setattr(cli, "_run_census_once", should_not_run)
    status, event, _rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_OUTPUT_EXISTS"
    assert output.read_bytes() == b"sentinel"
    assert calls == 0


def test_output_outside_the_fixed_report_root_is_rejected(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside.json"
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)

    status, event, _rendered = _invoke(cli, outside, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_OUTPUT_PATH_INVALID"
    assert not outside.exists()
    assert output_root.is_dir()


def test_writable_strict_profile_fails_closed_before_census(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotenv_path, private_path, _public_raw, _values = _operator_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    _make_integrity_file_writable(dotenv_path)
    calls = 0

    def should_not_run(**_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("census must not run")

    monkeypatch.setattr(cli, "_run_census_once", should_not_run)
    output = output_root / "receipt.json"
    status, event, rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_PROFILE_INVALID"
    assert calls == 0
    assert not output.exists()
    assert str(dotenv_path) not in rendered


def test_unapproved_profile_signer_fails_before_census(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dotenv, private_path, _public_raw, _values = _operator_profile(
        tmp_path,
        monkeypatch,
        signer_id="fictional-unapproved-signer",
    )
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    calls = 0

    def should_not_run(**_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("census must not run")

    monkeypatch.setattr(cli, "_run_census_once", should_not_run)
    output = output_root / "receipt.json"
    status, event, _rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_SIGNER_INVALID"
    assert calls == 0
    assert not output.exists()


def test_private_and_trusted_public_key_mismatch_fails_before_census(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_key = Ed25519PrivateKey.generate()
    different_public_key = Ed25519PrivateKey.generate()
    _dotenv, private_path, _public_raw, _values = _operator_profile(
        tmp_path,
        monkeypatch,
        private_key=private_key,
        public_key=different_public_key,
    )
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    calls = 0

    def should_not_run(**_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("census must not run")

    monkeypatch.setattr(cli, "_run_census_once", should_not_run)
    output = output_root / "receipt.json"
    status, event, _rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_KEY_MISMATCH"
    assert calls == 0
    assert not output.exists()


def test_invalid_private_key_path_fails_closed_and_leaves_no_receipt(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dotenv, _private_path, _public_raw, _values = _operator_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", Path("relative-private-key.pem"))
    output = output_root / "receipt.json"

    status, event, _rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_KEY_INVALID"
    assert not output.exists()


def test_census_is_called_exactly_once_and_written_receipt_verifies(
    cli: ModuleType,
    approved_result: dict[str, Any],
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dotenv, private_path, public_raw, values = _operator_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    calls: list[tuple[Path, str]] = []

    def fake_census_once(
        *,
        runner: ModuleType,
        artifact_path: Path,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((artifact_path, artifact["metadata"]["pipeline_contract_version"]))
        assert runner.HARNESS_IDENTITY == _APPROVED_HARNESS_IDENTITY
        _replay_constructor_authority_loads(runner)
        return copy.deepcopy(approved_result)

    monkeypatch.setattr(cli, "_run_census_once", fake_census_once)
    output = output_root / "receipt.json"
    status, event, rendered = _invoke(cli, output, capsys)

    assert status == 0
    assert event["result"] == "PASS"
    assert event["receipt_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert calls == [(cli._ARTIFACT_PATH.resolve(), "full-strict-v16")]

    runner = cli._load_approved_runner()
    artifact_raw, _artifact = runner._decode_artifact(cli._ARTIFACT_PATH)
    payload = runner.verify_signed_receipt(
        receipt_raw=output.read_bytes(),
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_raw,
    )
    assert payload["result"] == "PASS"
    assert payload["census_result"] == approved_result
    assert json.loads(output.read_bytes())["signer_id"] == _APPROVED_SIGNER_ID
    # Only secret-bearing profile values are non-emission canaries; literals
    # such as "1" and "en" necessarily occur in the safe JSON envelope.
    sensitive_values = {
        values["IDIS_DATABASE_URL"],
        values["IDIS_DATABASE_ADMIN_URL"],
        values["IDIS_ERASURE_DATABASE_URL"],
        values["IDIS_REDIS_URL"],
        values["NEO4J_PASSWORD"],
        values["IDIS_REAL_EXAMPLE_API_KEY"],
        values["IDIS_ISOLATION_CANARY_API_KEY"],
        values["IDIS_GOVERNANCE_ADMIN_API_KEY"],
        values["IDIS_RUNTIME_PROFILE_HMAC_KEY"],
    }
    assert all(secret not in rendered for secret in sensitive_values)
    private_header = _private_pem(Ed25519PrivateKey.generate()).decode("ascii").splitlines()[0]
    assert private_header not in rendered


def test_pre_freeze_signing_uses_governance_profile_without_erasure_credential(
    cli: ModuleType,
    approved_result: dict[str, Any],
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dotenv, private_path, _public_raw, values = _operator_profile(
        tmp_path,
        monkeypatch,
        omit_erasure_executor=True,
    )
    assert "IDIS_ERASURE_DATABASE_URL" not in values
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    calls = 0

    def fake_census_once(**kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        _replay_constructor_authority_loads(cast(ModuleType, kwargs["runner"]))
        return copy.deepcopy(approved_result)

    monkeypatch.setattr(cli, "_run_census_once", fake_census_once)
    output = output_root / "receipt.json"

    status, event, _rendered = _invoke(cli, output, capsys)

    assert status == 0
    assert event["result"] == "PASS"
    assert calls == 1
    assert output.is_file()


def test_census_failure_leaves_zero_receipt_and_emits_only_safe_code(
    cli: ModuleType,
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotenv_path, private_path, _public_raw, values = _operator_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)
    secret = values["IDIS_DATABASE_ADMIN_URL"]
    calls = 0

    def failing_census(**_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"database failure at {secret}")

    monkeypatch.setattr(cli, "_run_census_once", failing_census)
    output = output_root / "receipt.json"
    status, event, rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event == {
        "reason_code": "TASK7_V16_SIGNED_CENSUS_EXECUTION_FAILED",
        "result": "FAILED",
        "schema_version": "task7-v16-signed-census-cli-result-v1",
    }
    assert calls == 1
    assert not output.exists()
    sensitive = {
        secret,
        str(dotenv_path),
        str(private_path),
        values["IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH"],
        values["IDIS_RUNTIME_PROFILE_HMAC_KEY"],
    }
    assert all(value not in rendered for value in sensitive)


def test_durable_write_failure_removes_partially_created_receipt(
    cli: ModuleType,
    approved_result: dict[str, Any],
    output_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dotenv, private_path, _public_raw, _values = _operator_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_PRIVATE_KEY_PATH", private_path)

    def fake_census_once(**kwargs: object) -> dict[str, Any]:
        _replay_constructor_authority_loads(cast(ModuleType, kwargs["runner"]))
        return copy.deepcopy(approved_result)

    monkeypatch.setattr(cli, "_run_census_once", fake_census_once)

    def fail_flush(_descriptor: int) -> None:
        raise OSError("fictional flush failure with private detail")

    monkeypatch.setattr(cli.os, "fsync", fail_flush)
    output = output_root / "receipt.json"
    status, event, rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_OUTPUT_WRITE_FAILED"
    assert not output.exists()
    assert "fictional flush failure" not in rendered


def test_process_crash_during_write_never_creates_partial_final_receipt(
    cli: ModuleType,
    output_root: Path,
) -> None:
    output = output_root / "receipt.json"
    script = r"""
import importlib.util
import os
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("_task7_v16_signed_census_crash", module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
real_write = module.os.write

def crash_after_partial_write(descriptor, content):
    real_write(descriptor, bytes(content[:7]))
    os._exit(91)

module.os.write = crash_after_partial_write
module._write_durable_exclusive(output_path, b"complete-signed-receipt")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(cli.__file__), str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 91
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not output.exists()


def test_complete_hard_link_is_commit_even_if_stage_cleanup_is_locked(
    cli: ModuleType,
    output_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = output_root / "receipt.json"
    content = b"complete-signed-receipt"
    real_unlink = Path.unlink

    def lock_only_stage(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.endswith(".stage"):
            raise PermissionError("fictional scanner lock")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", lock_only_stage)

    cli._write_durable_exclusive(output, content)

    assert output.read_bytes() == content


def test_failure_events_are_canonical_single_line_json_without_secret_values(
    cli: ModuleType,
    output_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    output = output_root / "receipt.json"

    status, event, rendered = _invoke(cli, output, capsys)

    assert status == 2
    assert event["result"] == "FAILED"
    assert event["reason_code"] == "TASK7_V16_SIGNED_CENSUS_PROFILE_INVALID"
    assert rendered == (
        json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    )
    assert "IDIS_" not in rendered
    assert "postgresql" not in rendered.lower()


@pytest.fixture(autouse=True)
def _assert_approved_runner_unchanged_after_each_test() -> Iterator[None]:
    before = hashlib.sha256(_RUNNER_PATH.read_bytes()).hexdigest()
    yield
    assert before == _APPROVED_RUNNER_SHA256
    assert hashlib.sha256(_RUNNER_PATH.read_bytes()).hexdigest() == _APPROVED_RUNNER_SHA256
