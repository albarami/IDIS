"""Runtime supersession contract for current full-strict-v21."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from idis import contracts
from idis.models.run_step import RunStep, StepStatus
from idis.pipeline import contract_registry, task7_producer_selectors_generated
from idis.pipeline.task7_lifecycle import (
    STEP_REHYDRATION_FAILED,
    Task7LifecycleError,
    rehydrate_strict_run,
)
from idis.pipeline.task7_uuid import (
    TASK7_UUID_STRICT_ADMISSION_REQUIRED,
    Task7UuidError,
    derive_step_id_for_admission,
)
from idis.pipeline.worker import (
    WorkerLeaseConfig,
    build_postgres_claimed_run_runtime,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V16_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V16_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_V17_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_V17_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v17_contract_hash_v1.json"
)
_V18_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v18_contract_hash_v1.json"
)
_V20_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_V21_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
_V21_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)

_V16 = "full-strict-v16"
_V17 = "full-strict-v17"
_V18 = "full-strict-v18"
_V20 = "full-strict-v20"
_V21 = "full-strict-v21"
_UNAVAILABLE = "PIPELINE_CONTRACT_UNAVAILABLE"
_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_DEAL_ID = "22222222-2222-4222-8222-222222222222"
_RUN_ID = "33333333-3333-4333-8333-333333333333"
_LEASE_OWNER_ID = "44444444-4444-4444-8444-444444444444"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _v17_fixture() -> dict[str, Any]:
    assert _V17_FIXTURE_PATH.is_file(), (
        "full-strict-v17 must not enter runtime or the contract lock before its "
        "append-frozen fixture is atomically published"
    )
    fixture = _load_json(_V17_FIXTURE_PATH)
    assert fixture["fixture_schema"] == "full-strict-v17-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V17
    return fixture


def _v18_fixture() -> dict[str, Any]:
    assert _V18_FIXTURE_PATH.is_file(), (
        "full-strict-v18 must not enter runtime or the contract lock before its "
        "append-frozen fixture is atomically published"
    )
    fixture = _load_json(_V18_FIXTURE_PATH)
    assert fixture["fixture_schema"] == "full-strict-v18-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V18
    return fixture


def _v20_fixture() -> dict[str, Any]:
    assert _V20_FIXTURE_PATH.is_file(), (
        "full-strict-v20 must not enter runtime or the contract lock before its "
        "append-frozen fixture is atomically published"
    )
    fixture = _load_json(_V20_FIXTURE_PATH)
    assert fixture["fixture_schema"] == "full-strict-v20-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V20
    return fixture


def _v21_fixture() -> dict[str, Any]:
    assert _V21_FIXTURE_PATH.is_file()
    fixture = _load_json(_V21_FIXTURE_PATH)
    assert fixture["fixture_schema"] == "full-strict-v21-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == _V21
    return fixture


def _assert_unavailable(error: BaseException) -> None:
    code = getattr(error, "code", None)
    assert code == _UNAVAILABLE or str(error) == _UNAVAILABLE


def _step_values(*, version: str, contract_hash: str) -> dict[str, object]:
    return {
        "tenant_id": _TENANT_ID,
        "deal_id": _DEAL_ID,
        "run_id": _RUN_ID,
        "pipeline_contract_version": version,
        "pipeline_contract_hash": contract_hash,
        "step_name": "DATA_ROOM_INVENTORY_PACKAGE",
        "step_order": 0,
    }


def _pending_steps(*, version: str, contract_hash: str) -> tuple[RunStep, ...]:
    contract = contract_registry.PIPELINE_CONTRACTS[version]
    return tuple(
        RunStep(
            step_id=f"{version}-step-{step.step_order:02d}",
            tenant_id=_TENANT_ID,
            run_id=_RUN_ID,
            step_name=step.step_name,
            step_order=step.step_order,
            status=StepStatus.PENDING,
            pipeline_contract_version=version,
            pipeline_contract_hash=contract_hash,
            semantic_rule_version=step.semantic_rule_version,
            semantic_rule_hash=step.semantic_rule_hash,
        )
        for step in contract.steps
    )


def _compose_claimed_runtime(*, version: str, contract_hash: str) -> Any:
    def no_database_access() -> Any:
        raise AssertionError("contract selection must not open a database connection")

    def context_factory(**components: Any) -> SimpleNamespace:
        run_data = components["run_data"]
        return SimpleNamespace(
            tenant_id=components["tenant_id"],
            deal_id=run_data["deal_id"],
            run_id=run_data["run_id"],
            pipeline_contract_version=run_data["pipeline_contract_version"],
            pipeline_contract_hash=run_data["pipeline_contract_hash"],
        )

    return build_postgres_claimed_run_runtime(
        tenant_id=_TENANT_ID,
        run_data={
            "tenant_id": _TENANT_ID,
            "deal_id": _DEAL_ID,
            "run_id": _RUN_ID,
            "status": "RUNNING",
            "mode": "FULL",
            "lease_owner_id": _LEASE_OWNER_ID,
            "lease_fencing_token": 1,
            "pipeline_contract_version": version,
            "pipeline_contract_hash": contract_hash,
            "pipeline_resume_state": "RESUMABLE",
        },
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=20),
        audit_sink=object(),
        connection_factory=no_database_access,
        provider_context_factory=context_factory,
    )


def test_frozen_v16_is_immutable_historical_and_never_selectable() -> None:
    fixture = _load_json(_V16_FIXTURE_PATH)
    artifact_raw = _V16_ARTIFACT_PATH.read_bytes()
    artifact = json.loads(artifact_raw)
    v16 = contract_registry.PIPELINE_CONTRACTS[_V16]

    assert artifact["metadata"]["append_frozen"] is True
    assert hashlib.sha256(artifact_raw).hexdigest() == fixture["artifact_sha256"]
    assert v16.state is contract_registry.PipelineContractState.INACTIVE
    assert v16.contract_hash == fixture["FULL_STRICT_V16_CONTRACT_HASH"]

    caller_marked_active = dict(contract_registry.PIPELINE_CONTRACTS)
    caller_marked_active[_V16] = replace(
        v16,
        state=contract_registry.PipelineContractState.ACTIVE,
    )
    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=caller_marked_active,
            contract_version=_V16,
            proof_contract_hash=fixture["FULL_STRICT_V16_CONTRACT_HASH"],
        )
    _assert_unavailable(caught.value)

    legacy_activate = getattr(contract_registry, "activate_full_strict_v16", None)
    if callable(legacy_activate):
        legacy_verify = contract_registry.verify_full_strict_v16_activation_census
        try:
            receipt = legacy_verify(contract_registry.FULL_STRICT_V16_ACTIVATION_CENSUS_V1)
        except contract_registry.PipelineContractError as error:
            _assert_unavailable(error)
        else:
            with pytest.raises(contract_registry.PipelineContractError) as activation:
                legacy_activate(
                    registry=contract_registry.PIPELINE_CONTRACTS,
                    census_receipt=receipt,
                    separately_reviewed=True,
                )
            _assert_unavailable(activation.value)


def test_frozen_v17_is_immutable_historical_and_never_selectable() -> None:
    fixture = _v17_fixture()
    artifact_raw = _V17_ARTIFACT_PATH.read_bytes()
    artifact = json.loads(artifact_raw)
    v17 = contract_registry.PIPELINE_CONTRACTS[_V17]

    assert artifact["metadata"]["append_frozen"] is True
    assert hashlib.sha256(artifact_raw).hexdigest() == fixture["artifact_sha256"]
    assert v17.state is contract_registry.PipelineContractState.INACTIVE
    assert v17.contract_hash == fixture["FULL_STRICT_V17_CONTRACT_HASH"]

    caller_marked_active = dict(contract_registry.PIPELINE_CONTRACTS)
    caller_marked_active[_V17] = replace(
        v17,
        state=contract_registry.PipelineContractState.ACTIVE,
    )
    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=caller_marked_active,
            contract_version=_V17,
            proof_contract_hash=fixture["FULL_STRICT_V17_CONTRACT_HASH"],
        )
    _assert_unavailable(caught.value)


def test_frozen_v18_is_immutable_historical_and_never_selectable() -> None:
    fixture = _v18_fixture()
    artifact_raw = _V18_ARTIFACT_PATH.read_bytes()
    artifact = json.loads(artifact_raw)
    v18 = contract_registry.PIPELINE_CONTRACTS[_V18]

    assert artifact["metadata"]["append_frozen"] is True
    assert hashlib.sha256(artifact_raw).hexdigest() == fixture["artifact_sha256"]
    assert v18.state is contract_registry.PipelineContractState.INACTIVE
    assert v18.contract_hash == fixture["FULL_STRICT_V18_CONTRACT_HASH"]

    caller_marked_active = dict(contract_registry.PIPELINE_CONTRACTS)
    caller_marked_active[_V18] = replace(
        v18,
        state=contract_registry.PipelineContractState.ACTIVE,
    )
    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=caller_marked_active,
            contract_version=_V18,
            proof_contract_hash=fixture["FULL_STRICT_V18_CONTRACT_HASH"],
        )
    _assert_unavailable(caught.value)


def test_v21_is_the_only_governed_full_strict_lock_target_and_stays_inactive() -> None:
    fixture = _v21_fixture()
    artifact_raw = _V21_ARTIFACT_PATH.read_bytes()
    artifact = json.loads(artifact_raw)
    expected_locked_file = _V21_ARTIFACT_PATH.relative_to(_REPO_ROOT).as_posix()

    assert artifact["metadata"]["append_frozen"] is True
    assert hashlib.sha256(artifact_raw).hexdigest() == fixture["artifact_sha256"]

    generated_files = contracts.build_lock_document(_REPO_ROOT)["files"]
    committed_files = _load_json(_REPO_ROOT / contracts.CONTRACT_LOCK_RELATIVE_PATH)["files"]
    for locked_files in (generated_files, committed_files):
        governed = {
            path: file_hash
            for path, file_hash in locked_files.items()
            if path.startswith("contracts/full_strict_")
            and path.endswith("_reviewed_preimage_v1.json")
        }
        assert governed == {expected_locked_file: fixture["artifact_sha256"]}

    strict_versions = {
        version
        for version in contract_registry.PIPELINE_CONTRACTS
        if version.startswith("full-strict-")
    }
    assert strict_versions == {_V16, _V17, _V18, _V20, _V21}
    v21 = contract_registry.PIPELINE_CONTRACTS[_V21]
    expected_contract_hash = fixture["FULL_STRICT_V21_CONTRACT_HASH"]
    expected_artifact_hash = fixture["artifact_sha256"]
    expected_fixture_hash = hashlib.sha256(_V21_FIXTURE_PATH.read_bytes()).hexdigest()
    assert v21.state is contract_registry.PipelineContractState.INACTIVE
    assert v21.contract_hash == expected_contract_hash
    assert expected_contract_hash == contract_registry.FULL_STRICT_V21_CONTRACT_HASH
    assert expected_artifact_hash == contract_registry.FULL_STRICT_V21_ARTIFACT_SHA256
    assert expected_fixture_hash == contract_registry.FULL_STRICT_V21_FIXTURE_SHA256
    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_VERSION == _V21
    assert expected_contract_hash == task7_producer_selectors_generated.PIPELINE_CONTRACT_HASH
    assert expected_artifact_hash == task7_producer_selectors_generated.SOURCE_ARTIFACT_SHA256

    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version=_V21,
            proof_contract_hash=fixture["FULL_STRICT_V21_CONTRACT_HASH"],
        )
    _assert_unavailable(caught.value)


def test_admission_identity_mints_v21_but_never_superseded_history() -> None:
    superseded = (
        (_V16, _load_json(_V16_FIXTURE_PATH)["FULL_STRICT_V16_CONTRACT_HASH"]),
        (_V17, _v17_fixture()["FULL_STRICT_V17_CONTRACT_HASH"]),
        (_V18, _v18_fixture()["FULL_STRICT_V18_CONTRACT_HASH"]),
        (_V20, _v20_fixture()["FULL_STRICT_V20_CONTRACT_HASH"]),
    )
    for version, contract_hash in superseded:
        with pytest.raises(Task7UuidError) as caught:
            derive_step_id_for_admission(
                pipeline_contract_version=version,
                values=_step_values(version=version, contract_hash=contract_hash),
            )
        assert caught.value.code == TASK7_UUID_STRICT_ADMISSION_REQUIRED

    v21_hash = _v21_fixture()["FULL_STRICT_V21_CONTRACT_HASH"]
    values = _step_values(version=_V21, contract_hash=v21_hash)
    first = derive_step_id_for_admission(
        pipeline_contract_version=_V21,
        values=values,
    )
    second = derive_step_id_for_admission(
        pipeline_contract_version=_V21,
        values=values,
    )
    assert first == second
    assert first.value.version == 5


def test_restart_rehydrates_v21_but_never_superseded_history() -> None:
    superseded = (
        (_V16, _load_json(_V16_FIXTURE_PATH)["FULL_STRICT_V16_CONTRACT_HASH"]),
        (_V17, _v17_fixture()["FULL_STRICT_V17_CONTRACT_HASH"]),
        (_V18, _v18_fixture()["FULL_STRICT_V18_CONTRACT_HASH"]),
        (_V20, _v20_fixture()["FULL_STRICT_V20_CONTRACT_HASH"]),
    )
    for version, contract_hash in superseded:
        with pytest.raises(Task7LifecycleError) as caught:
            rehydrate_strict_run(
                tenant_id=_TENANT_ID,
                run_id=_RUN_ID,
                pipeline_contract_version=version,
                pipeline_contract_hash=contract_hash,
                pipeline_resume_state="RESUMABLE",
                steps=_pending_steps(version=version, contract_hash=contract_hash),
                validate_completed=lambda _step: True,
            )
        assert caught.value.code == STEP_REHYDRATION_FAILED

    v21_hash = _v21_fixture()["FULL_STRICT_V21_CONTRACT_HASH"]
    plan = rehydrate_strict_run(
        tenant_id=_TENANT_ID,
        run_id=_RUN_ID,
        pipeline_contract_version=_V21,
        pipeline_contract_hash=v21_hash,
        pipeline_resume_state="RESUMABLE",
        steps=_pending_steps(version=_V21, contract_hash=v21_hash),
        validate_completed=lambda _step: True,
    )
    assert plan.completed_count == 0
    assert plan.next_step_order == 0
    assert plan.replay_uncompleted_boundary is False
    assert plan.finalization_required is False


def test_worker_composes_v21_completion_but_never_superseded_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    superseded = (
        (_V16, _load_json(_V16_FIXTURE_PATH)["FULL_STRICT_V16_CONTRACT_HASH"]),
        (_V17, _v17_fixture()["FULL_STRICT_V17_CONTRACT_HASH"]),
        (_V18, _v18_fixture()["FULL_STRICT_V18_CONTRACT_HASH"]),
        (_V20, _v20_fixture()["FULL_STRICT_V20_CONTRACT_HASH"]),
    )
    for version, contract_hash in superseded:
        with pytest.raises(RuntimeError) as caught:
            _compose_claimed_runtime(version=version, contract_hash=contract_hash)
        _assert_unavailable(caught.value)

    v21_hash = _v21_fixture()["FULL_STRICT_V21_CONTRACT_HASH"]
    runtime = _compose_claimed_runtime(version=_V21, contract_hash=v21_hash)
    assert runtime.context.pipeline_contract_version == _V21
    assert runtime.context.pipeline_contract_hash == v21_hash
    assert callable(runtime.context.task7_step_completion_fn)
