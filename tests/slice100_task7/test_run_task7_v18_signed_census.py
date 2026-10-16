"""RED-first contracts for the v18 signed pre-freeze census wrapper."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v18_current_guard_census.py"
_SIGNED_PATH = _REPO_ROOT / "scripts" / "run_task7_v18_signed_census.py"
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_PROOF_FIELD = "downgrade_retained_definition_execution_proof"
_FROZEN_ARTIFACT_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V18_SIGNED_CENSUS_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v18_signed_cli_pins_only_the_v18_runner_and_candidate() -> None:
    cli = _load(_SIGNED_PATH, "_task7_v18_signed_pin_red")

    assert cli._RUNNER_PATH == _RUNNER_PATH
    assert cli._ARTIFACT_PATH == _ARTIFACT_PATH
    assert hashlib.sha256(_RUNNER_PATH.read_bytes()).hexdigest() == cli._APPROVED_RUNNER_SHA256
    frozen_raw = _ARTIFACT_PATH.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == _FROZEN_ARTIFACT_SHA256
    candidate = json.loads(frozen_raw)
    candidate["metadata"]["append_frozen"] = False
    candidate_raw = (
        json.dumps(candidate, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(candidate_raw).hexdigest() == cli._CANDIDATE_ARTIFACT_SHA256
    runner = cli._load_approved_runner()
    assert runner.BASE_SCHEMA_REVISION == "0037"
    artifact = runner._decode_json(candidate_raw)
    runner._validate_v18_candidate(artifact)
    assert artifact["metadata"]["append_frozen"] is False
    expected_manifest = [list(row) for row in runner._V18_MATERIALIZATION_SOURCE_MANIFEST]
    assert (
        runner._digest(["task7-v18-materialization-source-manifest-v1", expected_manifest])
        == cli._APPROVED_MATERIALIZATION_SOURCE_MANIFEST_SHA256
    )


def test_v18_signed_cli_rejects_runner_drift_before_source_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load(_SIGNED_PATH, "_task7_v18_signed_drift_red")
    marker = tmp_path / "executed"
    tampered = tmp_path / "runner.py"
    tampered.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_RUNNER_PATH", tampered)

    with pytest.raises(cli.SignedCensusCliError) as captured:
        cli._load_approved_runner()

    assert captured.value.code == "TASK7_V18_SIGNED_CENSUS_RUNNER_UNAUTHORIZED"
    assert not marker.exists()


def test_v18_signed_cli_passes_the_closed_proof_to_sign_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load(_SIGNED_PATH, "_task7_v18_signed_proof_red")
    artifact_raw = b'{"candidate":"v18"}'
    artifact = {"metadata": {"pipeline_contract_version": "full-strict-v18"}}
    proof = {"execution_sha256": "1" * 64}
    result = {_PROOF_FIELD: proof, "complete_execution_sha256": "2" * 64}
    calls: list[str] = []

    class FakeRunner:
        BASE_SCHEMA_REVISION = "0037"
        HARNESS_IDENTITY = "v18-harness"
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
            assert kwargs["result"][_PROOF_FIELD] == proof
            return {
                "schema_version": "test-v18-envelope",
                "payload": {"census_result": kwargs["result"]},
            }

        @staticmethod
        def verify_signed_receipt(**kwargs: object) -> dict[str, Any]:
            calls.append("verify")
            decoded = json.loads(kwargs["receipt_raw"])
            assert decoded["payload"]["census_result"][_PROOF_FIELD] == proof
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
    assert json.loads(output.read_bytes())["payload"]["census_result"][_PROOF_FIELD] == proof


def test_v18_receipt_wrappers_reject_a_signed_payload_without_the_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER_PATH, "_task7_v18_signed_validation_red")
    artifact = {
        "metadata": {
            "artifact_schema": "full-strict-v18-reviewed-preimage-v1",
            "pipeline_contract_version": "full-strict-v18",
            "append_frozen": False,
        }
    }
    monkeypatch.setattr(
        runner._BASE,
        "verify_signed_receipt",
        lambda **_kwargs: {"census_result": {"complete_execution_sha256": "0" * 64}},
    )
    monkeypatch.setattr(runner, "_decode_json", lambda _raw: artifact)
    monkeypatch.setattr(
        runner._BASE,
        "validate_execution_contract",
        lambda *, result, artifact: None,
    )

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V18_RETAINED_RESTORE_EXECUTION_INVALID",
    ):
        runner.verify_signed_receipt(
            receipt_raw=b"test",
            artifact_raw=b"test",
            trusted_public_key_raw=b"test",
        )
