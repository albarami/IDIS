"""RED-first contracts for the v19 signed pre-freeze census wrapper."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v19_current_guard_census.py"
_SIGNED = _REPO_ROOT / "scripts" / "run_task7_v19_signed_census.py"
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_CANDIDATE_SHA256 = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V19_SIGNED_CENSUS_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v19_signed_cli_pins_only_the_v19_runner_candidate_and_source_manifest() -> None:
    cli = _load(_SIGNED, "_task7_v19_signed_pins_red")
    runner = cli._load_approved_runner()

    assert cli._RUNNER_PATH == _RUNNER
    assert cli._ARTIFACT_PATH == _ARTIFACT
    assert hashlib.sha256(_RUNNER.read_bytes()).hexdigest() == cli._APPROVED_RUNNER_SHA256
    assert hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest() == _CANDIDATE_SHA256
    assert cli._CANDIDATE_ARTIFACT_SHA256 == _CANDIDATE_SHA256
    assert (
        runner.materialization_source_manifest_sha256()
        == cli._APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256
    )


def test_v19_signed_cli_uses_migrate_profile_and_binds_all_database_roles(
    monkeypatch: Any,
) -> None:
    cli = _load(_SIGNED, "_task7_v19_signed_migrate_profile_red")
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


def test_v19_signed_cli_carries_both_database_proofs_through_sign_and_verify(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cli = _load(_SIGNED, "_task7_v19_signed_proofs_red")
    artifact_raw = b'{"candidate":"v19"}'
    artifact = {"metadata": {"pipeline_contract_version": "full-strict-v19"}}
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
        HARNESS_IDENTITY = "v19-harness"
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
            assert result["admin_case_execution_count"] == 0
            return copy.deepcopy(result)

        @staticmethod
        def build_and_verify_signed_receipt(**kwargs: object) -> dict[str, Any]:
            calls.append("sign")
            assert kwargs["result"]["downgrade_retained_definition_execution_proof"] == retained
            assert kwargs["result"]["erasure_authority_execution_proof"] == erasure
            return {
                "schema_version": "task7-v19-postgres-census-envelope-v1",
                "signer_id": "slice100-governance-signer-v1",
                "payload": {"census_result": kwargs["result"]},
            }

        @staticmethod
        def verify_signed_receipt(**kwargs: object) -> dict[str, Any]:
            calls.append("verify")
            decoded = json.loads(kwargs["receipt_raw"])
            assert (
                decoded["payload"]["census_result"]["erasure_authority_execution_proof"] == erasure
            )
            return decoded["payload"]

    key = Ed25519PrivateKey.generate()
    material = SimpleNamespace(
        signer_id="slice100-governance-signer-v1",
        private_key=key,
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
