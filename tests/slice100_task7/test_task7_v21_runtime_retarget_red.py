"""RED-first contract for atomically retargeting Task-7 runtime authority to v21."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import idis.contracts as contracts
from idis.pipeline import contract_registry, task7_producer_selectors_generated
from idis.runtime import dependency_readiness

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V20_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v20_reviewed_preimage_v1.json"
_V20_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v20_contract_hash_v1.json"
)
_V21_ARTIFACT_RELATIVE_PATH = Path("contracts/full_strict_v21_reviewed_preimage_v1.json")
_V21_ARTIFACT_PATH = _REPO_ROOT / _V21_ARTIFACT_RELATIVE_PATH
_V21_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
_CONTRACT_LOCK_PATH = _REPO_ROOT / "contracts" / "contract_lock.json"

_V20_ARTIFACT_SHA256 = "48b90375487f9d3eef382e781fe267454e4f58d89df4ff168ab13b248b281cfd"
_V20_FIXTURE_SHA256 = "b8ba48b067a52ab5015ae1edca4943597670e1e2b828ccb640fccbda1b1207e3"
_V20_CONTRACT_SHA256 = "0bcdc93540507a2935e92eef9691f44d061ebffb58743462e6eb2ea7a8825178"
_ERASURE_URL = "postgresql+psycopg2://idis_erasure_executor:secret@db/idis"

_ACTIVE_COMPOSITION_MARKERS: dict[str, tuple[str, ...]] = {
    "src/idis/contracts.py": ("full_strict_v21_reviewed_preimage_v1.json",),
    "src/idis/persistence/repositories/runs.py": (
        "superseded_full_strict_v20_version",
        "full_strict_v21_version",
        "full_strict_v21_hash",
        "FULL_STRICT_V21_CONTRACT_HASH",
    ),
    # Preflight resolves the code-owned registry entry, so the version key is the
    # composition marker; duplicating the registry hash here would create drift.
    "src/idis/persistence/repositories/strict_preflight_proofs.py": ("full-strict-v21",),
    "src/idis/persistence/repositories/task7_completion.py": ("full-strict-v21",),
    "src/idis/persistence/repositories/task7_run_graph.py": ("full-strict-v21",),
    "src/idis/pipeline/task7_completion.py": ("full-strict-v21",),
    "src/idis/pipeline/task7_edges.py": ("full-strict-v21",),
    "src/idis/pipeline/task7_lifecycle.py": ("full-strict-v21",),
    "src/idis/pipeline/task7_usage.py": (
        "full-strict-v21",
        "FULL_STRICT_V21_CONTRACT_HASH",
    ),
    "src/idis/pipeline/task7_uuid.py": (
        "full-strict-v21",
        "FULL_STRICT_V21_CONTRACT_HASH",
    ),
    "src/idis/pipeline/worker.py": (
        "full-strict-v21",
        "FULL_STRICT_V21_CONTRACT_HASH",
    ),
    "src/idis/runtime/dependency_probes.py": ("TASK7_V21_",),
    "src/idis/runtime/dependency_readiness.py": ("TASK7_V21_",),
    "src/idis/services/runs/execution.py": ("full-strict-v21",),
    "src/idis/services/runs/orchestrator.py": ("full-strict-v21",),
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _require_v21_fixture() -> dict[str, Any]:
    assert _V21_FIXTURE_PATH.is_file(), "TASK7_V21_FROZEN_FIXTURE_NOT_PUBLISHED"
    return _load_json(_V21_FIXTURE_PATH)


def _ready_executor_facts(*, direct_schema_acl_count: int) -> Any:
    target = dependency_readiness.ErasureDatabaseTargetFacts(
        database_name="idis",
        server_address="127.0.0.1",
        server_port=15432,
    )
    return dependency_readiness.ErasureExecutorProbeFacts(
        connected=True,
        authenticated_role="idis_erasure_executor",
        effective_role="idis_erasure_executor",
        executor_target=target,
        app_target=target,
        app_role="idis_app",
        app_effective_role="idis_app",
        executor_role=dependency_readiness.ErasureRoleFacts(
            role_name="idis_erasure_executor",
            can_login=True,
            superuser=False,
            create_database=False,
            create_role=False,
            inherit=False,
            bypass_rls=False,
            replication=False,
        ),
        owner_role=dependency_readiness.ErasureRoleFacts(
            role_name="idis_erasure_function_owner",
            can_login=False,
            superuser=False,
            create_database=False,
            create_role=False,
            inherit=False,
            bypass_rls=False,
            replication=False,
        ),
        membership_count=0,
        executor_table_privilege_count=0,
        executor_column_privilege_count=0,
        executor_schema_usage=True,
        executor_schema_create=False,
        executor_schema_direct_acl_count=direct_schema_acl_count,
        executor_other_direct_execute_count=0,
        function_count=1,
        function_schema="public",
        function_name="idis_task7_execute_approved_erasure",
        function_identity_arguments=("uuid", "uuid", "uuid"),
        function_result_type="jsonb",
        function_owner="idis_erasure_function_owner",
        function_security_definer=True,
        function_search_path=("pg_catalog", "pg_temp"),
        executor_has_execute=True,
        public_has_execute=False,
        app_role_has_execute=False,
        unexpected_execute_grant_count=0,
        function_owner_update_acl=(
            dependency_readiness.ErasureFunctionOwnerUpdateAclFacts(
                relation_tuple=("erasure_requests",),
                column_update_acl_count=5,
                ordered_column_tuple=(
                    "request_id",
                    "status",
                    "executed_by",
                    "executed_at",
                    "counts",
                ),
                grantable_acl_count=0,
                table_update_privilege_count=0,
            )
        ),
        guard_owner=dependency_readiness._DEFAULT_TASK7_GUARD_OWNER_FACTS,
        runner_contract=dependency_readiness._DEFAULT_TASK7_ERASURE_RUNNER_FACTS,
    )


def _check_executor(facts: Any) -> dependency_readiness.ReadinessCheck:
    return dependency_readiness.check_erasure_executor_readiness(
        env={"IDIS_ERASURE_DATABASE_URL": _ERASURE_URL},
        expected_role="api",
        probe=lambda: facts,
    )


def test_v21_artifact_must_be_append_frozen_before_runtime_admission() -> None:
    artifact = _load_json(_V21_ARTIFACT_PATH)

    assert artifact["metadata"]["artifact_schema"] == ("full-strict-v21-reviewed-preimage-v1")
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v21"
    assert artifact["metadata"]["append_frozen"] is True


def test_v21_fixture_and_runtime_hash_constants_are_atomically_bound() -> None:
    artifact = _load_json(_V21_ARTIFACT_PATH)
    fixture = _require_v21_fixture()
    artifact_sha256 = hashlib.sha256(_V21_ARTIFACT_PATH.read_bytes()).hexdigest()
    fixture_sha256 = hashlib.sha256(_V21_FIXTURE_PATH.read_bytes()).hexdigest()
    contract_hash = fixture["FULL_STRICT_V21_CONTRACT_HASH"]

    assert fixture["pipeline_contract_version"] == "full-strict-v21"
    assert fixture["artifact_sha256"] == artifact_sha256
    assert isinstance(contract_hash, str) and len(contract_hash) == 64
    assert artifact["metadata"]["append_frozen"] is True
    assert artifact_sha256 == contract_registry.FULL_STRICT_V21_ARTIFACT_SHA256
    assert fixture_sha256 == contract_registry.FULL_STRICT_V21_FIXTURE_SHA256
    assert contract_hash == contract_registry.FULL_STRICT_V21_CONTRACT_HASH


def test_contract_lock_governs_only_frozen_v21_among_full_strict_artifacts() -> None:
    full_strict_targets = tuple(
        path
        for path in contracts.GOVERNED_CONTRACT_RELATIVE_PATHS
        if path.name.startswith("full_strict_v")
    )
    lock = _load_json(_CONTRACT_LOCK_PATH)
    locked_full_strict = {
        path: digest
        for path, digest in lock["files"].items()
        if Path(path).name.startswith("full_strict_v")
    }
    generated_lock = contracts.build_lock_document(_REPO_ROOT)

    assert full_strict_targets == (_V21_ARTIFACT_RELATIVE_PATH,)
    assert locked_full_strict == {
        _V21_ARTIFACT_RELATIVE_PATH.as_posix(): generated_lock["files"][
            _V21_ARTIFACT_RELATIVE_PATH.as_posix()
        ]
    }
    assert lock == generated_lock


def test_frozen_v20_remains_exact_inactive_history_and_cannot_be_reactivated() -> None:
    assert hashlib.sha256(_V20_ARTIFACT_PATH.read_bytes()).hexdigest() == (_V20_ARTIFACT_SHA256)
    assert hashlib.sha256(_V20_FIXTURE_PATH.read_bytes()).hexdigest() == (_V20_FIXTURE_SHA256)
    assert contract_registry.FULL_STRICT_V20_ARTIFACT_SHA256 == _V20_ARTIFACT_SHA256
    assert contract_registry.FULL_STRICT_V20_FIXTURE_SHA256 == _V20_FIXTURE_SHA256
    assert contract_registry.FULL_STRICT_V20_CONTRACT_HASH == _V20_CONTRACT_SHA256

    historical = contract_registry.PIPELINE_CONTRACTS["full-strict-v20"]
    assert historical.state is contract_registry.PipelineContractState.INACTIVE
    forged_active_registry = dict(contract_registry.PIPELINE_CONTRACTS)
    forged_active_registry["full-strict-v20"] = replace(
        historical,
        state=contract_registry.PipelineContractState.ACTIVE,
    )

    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=forged_active_registry,
            contract_version="full-strict-v20",
            proof_contract_hash=_V20_CONTRACT_SHA256,
        )
    assert caught.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE
    assert "superseded" in str(caught.value)


def test_v21_is_the_only_current_full_strict_selector_and_starts_inactive() -> None:
    fixture = _require_v21_fixture()
    strict_versions = {
        version
        for version in contract_registry.PIPELINE_CONTRACTS
        if version.startswith("full-strict-")
    }

    assert strict_versions == {
        "full-strict-v16",
        "full-strict-v17",
        "full-strict-v18",
        "full-strict-v20",
        "full-strict-v21",
    }
    assert all(
        contract_registry.PIPELINE_CONTRACTS[version].state
        is contract_registry.PipelineContractState.INACTIVE
        for version in strict_versions
    )
    current = contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]
    assert current.contract_hash == fixture["FULL_STRICT_V21_CONTRACT_HASH"]

    with pytest.raises(contract_registry.PipelineContractError) as caught:
        contract_registry.require_pipeline_contract_for_admission(
            registry=contract_registry.PIPELINE_CONTRACTS,
            contract_version="full-strict-v21",
            proof_contract_hash=str(fixture["FULL_STRICT_V21_CONTRACT_HASH"]),
        )
    assert caught.value.code == contract_registry.PIPELINE_CONTRACT_UNAVAILABLE
    assert "inactive" in str(caught.value)


def test_generated_selector_is_the_frozen_v21_projection() -> None:
    fixture = _require_v21_fixture()

    assert task7_producer_selectors_generated.PIPELINE_CONTRACT_VERSION == ("full-strict-v21")
    assert (
        fixture["FULL_STRICT_V21_CONTRACT_HASH"]
        == task7_producer_selectors_generated.PIPELINE_CONTRACT_HASH
    )
    assert fixture["artifact_sha256"] == task7_producer_selectors_generated.SOURCE_ARTIFACT_SHA256


def test_all_active_composition_surfaces_explicitly_target_v21() -> None:
    missing = {
        relative_path: tuple(
            marker
            for marker in markers
            if marker not in (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        )
        for relative_path, markers in _ACTIVE_COMPOSITION_MARKERS.items()
    }

    assert not {relative_path: markers for relative_path, markers in missing.items() if markers}, (
        "TASK7_V21_RUNTIME_RETARGET_INCOMPLETE"
    )


def test_readiness_exports_v21_as_current_authority() -> None:
    assert dependency_readiness.task7_v21_erasure_runner_split_is_valid()
    assert len(dependency_readiness.TASK7_V21_CURRENT_ERASURE_RELATIONS) == 48
    assert len(dependency_readiness.TASK7_V21_FUTURE_ERASURE_RELATIONS) == 38
    assert len(dependency_readiness.TASK7_V21_COMPLETE_ERASURE_RELATIONS) == 86
    assert len(dependency_readiness.TASK7_V21_CURRENT_GUARD_ROUTINE_SIGNATURES) == 24


def test_v21_readiness_rejects_effective_usage_without_direct_schema_acl() -> None:
    result = _check_executor(_ready_executor_facts(direct_schema_acl_count=0))

    assert result == dependency_readiness.ReadinessCheck(
        component="erasure_executor",
        may_proceed=False,
        code="ERASURE_EXECUTOR_POSTURE_BLOCKED",
    )


def test_v21_readiness_accepts_the_exact_direct_schema_usage_closure() -> None:
    result = _check_executor(_ready_executor_facts(direct_schema_acl_count=1))

    assert result == dependency_readiness.ReadinessCheck(
        component="erasure_executor",
        may_proceed=True,
        code=None,
    )
