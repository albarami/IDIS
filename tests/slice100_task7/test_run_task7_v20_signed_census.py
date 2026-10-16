"""RED-first contracts for the v20 signed pre-freeze census wrapper."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v20_current_guard_census.py"
_SIGNED = _REPO_ROOT / "scripts" / "run_task7_v20_signed_census.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_CANDIDATE_SHA256 = "d046026da38b0eddbafc05368f43300b8c4aff7bfe0a8a9c1def0c960927f91a"
_FROZEN_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V20_SIGNED_CENSUS_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_raw() -> bytes:
    frozen_raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_SHA256
    artifact = cast(dict[str, Any], json.loads(frozen_raw))
    assert artifact["metadata"]["append_frozen"] is True
    artifact["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(candidate_raw).hexdigest() == _CANDIDATE_SHA256
    return candidate_raw


def test_v20_signed_cli_pins_only_v20_runner_candidate_and_source_manifest() -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_pins_red")
    runner = cli._load_approved_runner()

    assert cli._RUNNER_PATH == _RUNNER
    assert cli._ARTIFACT_PATH == _ARTIFACT
    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == cli._APPROVED_RUNNER_SHA256
    assert hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest() == _FROZEN_SHA256
    assert hashlib.sha256(_candidate_raw()).hexdigest() == _CANDIDATE_SHA256
    assert cli._CANDIDATE_ARTIFACT_SHA256 == _CANDIDATE_SHA256
    candidate = cast(dict[str, Any], json.loads(_candidate_raw()))
    assert (
        runner._receipt_materialization_source_sha256(candidate)
        == cli._APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256
    )


def test_v20_signed_cli_binds_all_database_roles(monkeypatch: Any) -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_profile_red")
    values = {
        "IDIS_DATABASE_ADMIN_URL": "postgresql://admin:secret@db/acceptance",
        "IDIS_DATABASE_URL": "postgresql://app:secret@db/acceptance",
        "IDIS_ERASURE_DATABASE_URL": ("postgresql://idis_erasure_executor:secret@db/acceptance"),
    }
    for key in values:
        monkeypatch.delenv(key, raising=False)

    cli._bind_migration_profile_database_urls(SimpleNamespace(values=values))

    assert tuple(values) == cli._MIGRATION_DATABASE_PROFILE_KEYS
    assert {key: os.environ[key] for key in values} == values
    adapted = cli._adapt_pinned_v17_cli()
    assert 'command="migrate"' in adapted
    assert 'command="governance"' not in adapted


def test_v20_signed_cli_carries_database_proofs_through_sign_and_verify(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_proofs_red")
    artifact_raw = b'{"candidate":"v20"}'
    artifact = {"metadata": {"pipeline_contract_version": "full-strict-v20"}}
    retained = {"execution_sha256": "a" * 64}
    erasure = {"execution_sha256": "b" * 64}
    result = {
        "admin_case_execution_count": 0,
        "downgrade_retained_definition_execution_proof": retained,
        "erasure_authority_execution_proof": erasure,
        "complete_execution_sha256": "c" * 64,
    }
    calls: list[str] = []

    class FakeRunner:
        BASE_SCHEMA_REVISION = "0037"
        HARNESS_IDENTITY = "v20-harness"
        _PRODUCTION_CONSTRUCTOR_SPECS: tuple[tuple[str, ...], ...] = ()

        @staticmethod
        def artifact_targets(_artifact: object) -> tuple[tuple[object, ...], ...]:
            return ((),)

        @staticmethod
        def artifact_trigger_contract(
            _artifact: object,
        ) -> tuple[tuple[object, ...], ...]:
            return ((),)

        @staticmethod
        def run_pre_fixture_census(**_kwargs: object) -> dict[str, Any]:
            calls.append("run")
            return copy.deepcopy(result)

        @staticmethod
        def build_and_verify_signed_receipt(**kwargs: object) -> dict[str, Any]:
            calls.append("sign")
            typed_kwargs = cast(dict[str, Any], kwargs)
            assert typed_kwargs["result"]["erasure_authority_execution_proof"] == erasure
            return {
                "schema_version": "task7-v20-postgres-census-envelope-v1",
                "signer_id": "slice100-governance-signer-v1",
                "payload": {"census_result": typed_kwargs["result"]},
            }

        @staticmethod
        def verify_signed_receipt(**kwargs: object) -> dict[str, Any]:
            calls.append("verify")
            typed_kwargs = cast(dict[str, Any], kwargs)
            receipt_raw = cast(str | bytes | bytearray, typed_kwargs["receipt_raw"])
            decoded = cast(dict[str, Any], json.loads(receipt_raw))
            return cast(dict[str, Any], decoded["payload"])

    material = SimpleNamespace(
        signer_id="slice100-governance-signer-v1",
        private_key=Ed25519PrivateKey.generate(),
        public_key_raw=b"test-public-key",
    )
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(cli, "_load_approved_runner", lambda: FakeRunner)
    monkeypatch.setattr(cli, "_load_candidate", lambda _runner: (artifact_raw, artifact))
    monkeypatch.setattr(cli, "_load_operator_material", lambda: material)
    monkeypatch.setattr(cli, "_validate_output", lambda _path: output)

    event = cli._execute(output)

    assert calls == ["run", "sign", "verify"]
    assert event["result"] == "PASS"
    persisted = json.loads(output.read_bytes())
    assert persisted["payload"]["census_result"]["erasure_authority_execution_proof"] == erasure


def test_v20_signed_cli_surfaces_only_a_stable_task7_causal_failure_code() -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_causal_code_red")
    inner = RuntimeError("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID")
    outer = cli.SignedCensusCliError("TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED")
    outer.__cause__ = inner

    assert cli._safe_causal_reason_code(outer) == "TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID"

    unsafe = RuntimeError("password=must-never-be-emitted")
    outer.__cause__ = unsafe
    assert cli._safe_causal_reason_code(outer) == "TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED"

    overlong = RuntimeError(f"TASK7_V20_{'A' * 129}")
    outer.__cause__ = overlong
    assert cli._safe_causal_reason_code(outer) == "TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED"

    inherited = RuntimeError("TASK7_V17_EXECUTION_INCOMPLETE")
    outer.__cause__ = inherited
    assert cli._safe_causal_reason_code(outer) == "TASK7_V17_EXECUTION_INCOMPLETE"

    class DatabaseFailure(RuntimeError):
        sqlstate = "42501"

    database = DatabaseFailure("credential-bearing database text")
    outer.__cause__ = database
    assert cli._safe_causal_reason_code(outer) == "TASK7_V20_POSTGRES_SQLSTATE_42501"


def test_v20_signed_cli_preserves_specific_cli_failures(monkeypatch: Any) -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_specific_error_red")
    specific = cli.SignedCensusCliError("TASK7_V20_SIGNED_CENSUS_OUTPUT_EXISTS")
    specific.__cause__ = RuntimeError("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID")

    # The production-shaped validation seam proves specific CLI errors are not remapped.
    def fail(_output: Path) -> Path:
        raise specific

    monkeypatch.setattr(cli, "_validate_output", fail)

    with pytest.raises(cli.SignedCensusCliError) as captured:
        cli._execute(Path("unused.json"))
    assert captured.value is specific


@pytest.mark.parametrize(
    ("failure_phase", "expected_code"),
    (
        ("census", "TASK7_V20_CENSUS_EXECUTION_FAILED"),
        ("build", "TASK7_V20_RECEIPT_BUILD_FAILED"),
        ("verify", "TASK7_V20_RECEIPT_VERIFY_FAILED"),
    ),
)
def test_v20_signed_cli_reports_the_exact_failed_phase(
    failure_phase: str,
    expected_code: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cli = _load(_SIGNED, f"_task7_v20_signed_phase_{failure_phase}_red")

    class PhaseRunner:
        BASE_SCHEMA_REVISION = "0037"
        HARNESS_IDENTITY = "v20-harness"
        _PRODUCTION_CONSTRUCTOR_SPECS: tuple[tuple[str, ...], ...] = ()

        @staticmethod
        def artifact_targets(_artifact: object) -> tuple[tuple[object, ...], ...]:
            return ((),)

        @staticmethod
        def artifact_trigger_contract(
            _artifact: object,
        ) -> tuple[tuple[object, ...], ...]:
            return ((),)

        @staticmethod
        def run_pre_fixture_census(**_kwargs: object) -> dict[str, Any]:
            if failure_phase == "census":
                raise RuntimeError("credential-bearing database text")
            return {"complete_execution_sha256": "a" * 64}

        @staticmethod
        def build_and_verify_signed_receipt(**_kwargs: object) -> dict[str, Any]:
            if failure_phase == "build":
                raise RuntimeError("private-key-bearing signer text")
            return {"schema_version": "test-envelope"}

        @staticmethod
        def verify_signed_receipt(**_kwargs: object) -> dict[str, Any]:
            if failure_phase == "verify":
                raise RuntimeError("public-key-bearing verifier text")
            return {}

    material = SimpleNamespace(
        signer_id="slice100-governance-signer-v1",
        private_key=Ed25519PrivateKey.generate(),
        public_key_raw=b"test-public-key",
    )
    monkeypatch.setattr(cli, "_load_approved_runner", lambda: PhaseRunner)
    monkeypatch.setattr(
        cli,
        "_load_candidate",
        lambda _runner: (
            b'{"candidate":"v20"}',
            {"metadata": {"pipeline_contract_version": "full-strict-v20"}},
        ),
    )
    monkeypatch.setattr(cli, "_load_operator_material", lambda: material)
    monkeypatch.setattr(cli, "_validate_output", lambda _path: tmp_path / "receipt.json")

    with pytest.raises(cli.SignedCensusCliError) as captured:
        cli._execute(tmp_path / "receipt.json")
    assert captured.value.code == expected_code
    assert "bearing" not in str(captured.value)


def test_v20_direct_cli_has_no_nested_pre_override_main_guard() -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_direct_guard_red")
    nested_guard = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'

    assert cli._adapt_pinned_v17_cli().count(nested_guard) == 0

    tree = ast.parse(_SIGNED.read_text(encoding="utf-8"))
    top_level_main_guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    ]
    assert len(top_level_main_guards) == 1


def test_v20_main_emits_only_the_safe_causal_code(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    cli = _load(_SIGNED, "_task7_v20_signed_main_causal_red")

    def fail(_output: Path) -> dict[str, Any]:
        raise cli.SignedCensusCliError("TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID")

    monkeypatch.setattr(cli, "_execute", fail)
    assert cli.main(["--output", str(tmp_path / "first.json")]) == 2
    event = json.loads(capsys.readouterr().out)
    assert event["reason_code"] == "TASK7_V20_ERASURE_AUTHORITY_EXECUTION_INVALID"

    def fail_generic(_output: Path) -> dict[str, Any]:
        raise cli.SignedCensusCliError("TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED")

    monkeypatch.setattr(cli, "_execute", fail_generic)
    assert cli.main(["--output", str(tmp_path / "second.json")]) == 2
    rendered = capsys.readouterr().out
    assert "password" not in rendered
    assert json.loads(rendered)["reason_code"] == "TASK7_V20_SIGNED_CENSUS_EXECUTION_FAILED"
