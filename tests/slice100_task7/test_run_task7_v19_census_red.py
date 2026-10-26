"""RED-first contracts for the full-strict-v19 PostgreSQL census.

The immutable v18 census remains the guard/constructor engine.  V19 must bind
that evidence to a second, application-role/dedicated-executor proof for the
reviewed erasure function; setup-admin DDL is never behavior evidence.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v19_census_scaffold.py"
_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v19_current_guard_census.py"
_V18_RUNNER = _REPO_ROOT / "scripts" / "run_task7_v18_current_guard_census.py"
_V18_SCAFFOLD = _REPO_ROOT / "scripts" / "_task7_v18_census_scaffold.py"

_CANDIDATE_SHA256 = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_V18_RUNNER_SHA256 = "10988bb9cbbc368b496c7d9e55177b5d7390219d8d38dc70e40e10583a6cf006"
_V18_SCAFFOLD_SHA256 = "f789c074b850f94f80d0740e7934c787a943739fe6fb008c3b54ea7c02bd5702"
_V18_ADAPTED_V11_SHA256 = "74aa26548edff669726c729ede2e339e3d6a162142ee63878f94cddc476c5317"
_V18_ADAPTED_V9_SHA256 = "a0e19f70a497cf75ac970c3d035326abd7ef5284f4a6a1a8d81dc12443929dac"
_V19_ADAPTED_V11_SHA256 = "58e3bbbda00e6653bd2e4eeecd2ca7859d2340c3e40ed9d62e69038f903a64cc"
_V19_ADAPTED_V9_SHA256 = "b3e5fa9474be385b794cfeb0514a344040b001063302b156f2e081dbe5e08c94"
_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ATTEMPT_NAMESPACE = "4520d8ed-e929-5486-821c-82f8901ae7fa"
_EVENT_NAMESPACE = "dd76cee1-28d5-55f8-b21a-eb64bc2e4be4"
_EVENT_OUTCOMES = (
    ("task7.erasure.attempted", "ATTEMPTED"),
    ("task7.erasure.failed", "FAILED"),
    ("task7.erasure.succeeded", "SUCCEEDED"),
)
_READ_CLOSURE = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
    "audit_events",
)
_UPDATE_COLUMNS = ("request_id", "status", "executed_by", "executed_at", "counts")


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK7_V19_CENSUS_MODULE_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> dict[str, Any]:
    raw = _ARTIFACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    value = json.loads(raw)
    assert value["metadata"]["append_frozen"] is False
    return value


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _retained_catalog(
    runner: ModuleType,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "function_tuple": [
            {
                "ordinal": row["ordinal"],
                "signature": row["signature"],
                "definition_sha256": _sha(f"function-{row['ordinal']}"),
                "owner": "idis_app_owner",
                "proconfig": None,
                "security_definer": False,
                "volatility": "v",
                "acl": None,
            }
            for row in runner._BASE._expected_function_restore_rows(artifact)
        ],
        "trigger_tuple": [
            {
                "ordinal": row["ordinal"],
                "function_signature": row["function_signature"],
                "table_name": row["table_name"],
                "trigger_name": row["trigger_name"],
                "definition_sha256": _sha(f"trigger-{row['ordinal']}"),
                "enabled": "O",
                "type_bits": 7,
                "constraint_oid": 0,
                "deferrable": False,
                "initially_deferred": False,
            }
            for row in runner._BASE._expected_trigger_restore_rows(artifact)
        ],
    }


def _retained_proof(
    runner: ModuleType,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    catalog = _retained_catalog(runner, artifact)
    committed_role = "idis_t2b_app_0123456789abcdef"
    rollback_role = "idis_t2b_app_fedcba9876543210"
    downgrade = runner._BASE._downgrade_authority(artifact)
    proof = {
        "schema_version": ("task7-v19-downgrade-retained-definition-app-role-proof-v1"),
        "base_schema_revision": "0037",
        "transaction_model": "TWO_DISPOSABLE_DATABASES_COMMIT_AND_ROLLBACK",
        "setup_actor_mode": "ADMIN_DDL_ONLY",
        "behavior_actor_mode": "APPLICATION_ROLE_ONLY",
        "admin_behavior_case_count": 0,
        "application_catalog_observation_count": 4,
        "function_restore_count": 8,
        "trigger_restore_count": 9,
        "function_restore_tuple": runner._BASE._expected_function_restore_rows(artifact),
        "trigger_restore_tuple": runner._BASE._expected_trigger_restore_rows(artifact),
        "committed_before_actor": {
            "session_user": committed_role,
            "current_user": committed_role,
        },
        "committed_restored_actor": {
            "session_user": committed_role,
            "current_user": committed_role,
        },
        "rollback_before_actor": {
            "session_user": rollback_role,
            "current_user": rollback_role,
        },
        "rollback_after_actor": {
            "session_user": rollback_role,
            "current_user": rollback_role,
        },
        "committed_catalog_before_projection": copy.deepcopy(catalog),
        "committed_catalog_restored_projection": copy.deepcopy(catalog),
        "rollback_catalog_before_projection": copy.deepcopy(catalog),
        "rollback_catalog_after_projection": copy.deepcopy(catalog),
        "postrestore_assertion_sha256": runner._BASE._sha256_utf8_hex(
            downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ),
        "committed_catalog_before_sha256": runner._retained_catalog_sha256(catalog),
        "committed_catalog_restored_sha256": runner._retained_catalog_sha256(catalog),
        "rollback_catalog_before_sha256": runner._retained_catalog_sha256(catalog),
        "rollback_catalog_after_sha256": runner._retained_catalog_sha256(catalog),
        "committed_restored_catalog_equals_before": True,
        "rollback_catalog_equals_before": True,
        "restore_committed": True,
        "transaction_rolled_back": True,
    }
    proof["execution_sha256"] = runner._digest([runner._RETAINED_PROOF_DOMAIN, proof])
    return proof


def _observation(runner: ModuleType, artifact: dict[str, Any]) -> dict[str, Any]:
    expectations = runner.erasure_authority_execution_expectations(artifact)
    acl_expectations = runner._artifact_acl_expectations(artifact)
    missing_state = runner._initial_state_sha256(attempted_event_present=False)
    attempted_state = runner._initial_state_sha256(attempted_event_present=True)
    counts = runner._digest(runner._expected_deletion_counts(artifact))
    staged_counts = runner._digest({"object_rows_reconciled": 1})
    retry = runner._failed_retry_expectations(artifact)
    application_role = "idis_t2b_app_a1b2c3d4e5f6a7b8"
    observation = {
        "setup_actor_mode": "ADMIN_DDL_ONLY",
        "application_role_name": application_role,
        "behavior_actor_tuple": [
            application_role,
            "idis_erasure_executor",
        ],
        "admin_behavior_case_count": 0,
        "application_guard_case_count": 219,
        "application_direct_runner_denial": {
            "executed_as_role": application_role,
            "session_user": application_role,
            "current_user": application_role,
            "sqlstate": "42501",
            "marker": "FUNCTION_EXECUTE_PRIVILEGE_DENIED",
            "state_before_sha256": attempted_state,
            "state_after_sha256": attempted_state,
            "zero_mutation": True,
        },
        "missing_attempt_rejection": {
            "executed_as_role": "idis_erasure_executor",
            "session_user": "idis_erasure_executor",
            "current_user": "idis_erasure_executor",
            "sqlstate": "23514",
            "marker": "TASK7_ERASURE_ATTEMPTED_EVENT_INVALID",
            "state_before_sha256": missing_state,
            "state_after_sha256": missing_state,
            "zero_mutation": True,
        },
        "rollback_atomicity": {
            "executed_as_role": "idis_erasure_executor",
            "session_user": "idis_erasure_executor",
            "current_user": "idis_erasure_executor",
            "inside_status": "EXECUTED",
            "inside_counts_sha256": counts,
            "inside_relation_rows_remaining": 0,
            "after_status": "REQUESTED",
            "after_counts_sha256": staged_counts,
            "after_relation_rows_remaining": 2,
            "transaction_rolled_back": True,
            "zero_mutation": True,
        },
        "legal_execution": {
            "executed_as_role": "idis_erasure_executor",
            "session_user": "idis_erasure_executor",
            "current_user": "idis_erasure_executor",
            "attempted_event_precondition_count": 1,
            "request_status_before": "REQUESTED",
            "request_status_after": "EXECUTED",
            "actor_bound_to_attempt": True,
            "object_rows_reconciled": 1,
            "artifact_row_count": 1,
            "deleted_relation_count": 48,
            "returned_counts_sha256": counts,
            "persisted_counts_sha256": counts,
            "counts_equal": True,
            "executed_at_equal": True,
            "committed": True,
        },
        "malformed_prior_rejection": {
            "executed_as_role": "idis_erasure_executor",
            "session_user": "idis_erasure_executor",
            "current_user": "idis_erasure_executor",
            "sqlstate": "23514",
            "marker": "TASK7_ERASURE_PRIOR_EVENT_INVALID",
            "state_before_sha256": retry["malformed_state_sha256"],
            "state_after_sha256": retry["malformed_state_sha256"],
            "zero_mutation": True,
        },
        "failed_retry_execution": {
            "executed_as_role": "idis_erasure_executor",
            "session_user": "idis_erasure_executor",
            "current_user": "idis_erasure_executor",
            "request_status_before": "FAILED",
            "request_status_after": "EXECUTED",
            "attempt_ordinal": 2,
            "prior_failed_event_id": retry["prior_failed_event_id"],
            "prior_failed_event_sha256": retry["prior_failed_event_sha256"],
            "retry_attempt_id": retry["retry_attempt_id"],
            "prior_event_identity_rederived": True,
            "prior_event_hash_rederived": True,
            "retry_attempt_identity_rederived": True,
            "returned_counts_sha256": counts,
            "persisted_counts_sha256": counts,
            "counts_equal": True,
            "executed_at_equal": True,
            "committed": True,
        },
        "fixed_vector_execution": {
            "attempt_vector_count": 1,
            "event_vector_count": 3,
            "canonical_failed_event_vector_count": 1,
            "attempt_vector_accepted": True,
            "event_vectors_accepted": True,
            "canonical_failed_event_accepted": True,
        },
        "acl_execution": {
            "catalog_session_user": application_role,
            "catalog_current_user": application_role,
            "owner_role": "idis_erasure_function_owner",
            "owner_posture_tuple": [
                "NOLOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOINHERIT",
                "NOBYPASSRLS",
            ],
            "executor_role": "idis_erasure_executor",
            "executor_posture_tuple": [
                "LOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOINHERIT",
                "NOBYPASSRLS",
            ],
            "executor_effective_schema_usage": True,
            "executor_effective_schema_create": False,
            "executor_direct_schema_acl_count": 0,
            "executor_other_routine_execute_count": 0,
            "application_role": application_role,
            "application_role_posture_tuple": [
                "LOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOINHERIT",
                "NOBYPASSRLS",
            ],
            "role_membership_count": 0,
            "owner_schema_usage": True,
            "owner_schema_create": False,
            "owner_owned_relation_count": 0,
            "runner_definition_sha256": acl_expectations["runner_definition_sha256"],
            "runner_owner": "idis_erasure_function_owner",
            "runner_security_definer": True,
            "runner_proconfig_tuple": ["search_path=pg_catalog, pg_temp"],
            "execute_grantee_tuple": ["idis_erasure_executor"],
            "application_execute": False,
            "public_execute": False,
            "executor_execute": True,
            "owner_helper_execute_count": 0,
            "select_relation_column_tuple": acl_expectations["select_relation_column_tuple"],
            "select_relation_count": expectations["owner_select_relation_count"],
            "select_column_count": expectations["owner_select_column_count"],
            "select_acl_sha256": acl_expectations["select_acl_sha256"],
            "update_column_tuple": list(_UPDATE_COLUMNS),
            "update_acl_sha256": acl_expectations["update_acl_sha256"],
            "delete_relation_tuple": acl_expectations["delete_relation_tuple"],
            "delete_relation_count": 48,
            "delete_acl_sha256": acl_expectations["delete_acl_sha256"],
            "forbidden_table_privilege_count": 0,
            "executor_table_privilege_count": 0,
            "executor_column_privilege_count": 0,
            "force_rls_relation_tuple": acl_expectations["force_rls_relation_tuple"],
            "force_rls_relation_count": expectations["owner_select_relation_count"],
        },
        "catalog_projection_sha256": "",
        "case_result_sha256": "",
    }
    observation["catalog_projection_sha256"] = runner._catalog_projection_sha256(
        application_role_name=application_role,
        acl_execution=observation["acl_execution"],
    )
    observation["case_result_sha256"] = runner._case_result_sha256(observation)
    return observation


def test_v19_retained_definition_proof_uses_only_application_catalog_observers() -> None:
    runner = _load(_RUNNER, "_task7_v19_retained_app_role_accept_red")
    artifact = _artifact()
    proof = _retained_proof(runner, artifact)

    assert (
        runner.validate_downgrade_retained_definition_execution_proof(
            proof,
            artifact=artifact,
        )
        == proof
    )
    assert proof["setup_actor_mode"] == "ADMIN_DDL_ONLY"
    assert proof["behavior_actor_mode"] == "APPLICATION_ROLE_ONLY"
    assert proof["admin_behavior_case_count"] == 0
    assert proof["application_catalog_observation_count"] == 4
    assert proof["restore_committed"] is True
    assert proof["transaction_rolled_back"] is True


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("admin_behavior_case_count",), 1),
        (("behavior_actor_mode",), "SETUP_ADMIN"),
        (("committed_before_actor", "session_user"), "setup_admin"),
        (("committed_restored_actor", "current_user"), "setup_admin"),
        (("rollback_before_actor", "session_user"), "setup_admin"),
        (("rollback_after_actor", "current_user"), "setup_admin"),
        (
            ("committed_catalog_restored_projection", "function_tuple", 0, "owner"),
            "tampered_owner",
        ),
        (
            ("rollback_catalog_after_projection", "trigger_tuple", 0, "enabled"),
            "D",
        ),
        (("committed_catalog_restored_sha256",), _sha("masked-committed")),
        (("rollback_catalog_after_sha256",), _sha("masked-rollback")),
        (("restore_committed",), False),
        (("transaction_rolled_back",), False),
        (("execution_sha256",), "0" * 64),
    ),
)
def test_v19_retained_definition_proof_rejects_admin_or_masked_catalog_evidence(
    path: tuple[object, ...],
    replacement: object,
) -> None:
    runner = _load(
        _RUNNER,
        f"_task7_v19_retained_reject_{len(path)}_{replacement!s}",
    )
    artifact = _artifact()
    changed = copy.deepcopy(_retained_proof(runner, artifact))
    target: Any = changed
    for coordinate in path[:-1]:
        target = target[coordinate]
    target[path[-1]] = replacement

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_RETAINED_RESTORE_EXECUTION_INVALID",
    ):
        runner.validate_downgrade_retained_definition_execution_proof(
            changed,
            artifact=artifact,
        )


def test_v19_concrete_executor_case_plan_is_closed() -> None:
    runner = _load(_RUNNER, "_task7_v19_executor_case_plan_red")

    assert runner._ERASURE_CASE_PLAN == (
        (
            "missing_attempt",
            "idis_erasure_executor",
            "23514",
            "ROLLBACK_ZERO_MUTATION",
        ),
        (
            "application_direct",
            "APPLICATION_ROLE",
            "42501",
            "ROLLBACK_ZERO_MUTATION",
        ),
        (
            "rollback_atomicity",
            "idis_erasure_executor",
            "00000",
            "EXPLICIT_ROLLBACK",
        ),
        (
            "legal_commit",
            "idis_erasure_executor",
            "00000",
            "COMMIT",
        ),
        (
            "malformed_failed_retry",
            "idis_erasure_executor",
            "23514",
            "ROLLBACK_ZERO_MUTATION",
        ),
        (
            "legal_failed_retry",
            "idis_erasure_executor",
            "00000",
            "COMMIT",
        ),
    )


def test_v19_fixed_vector_execution_is_rederived_without_runtime_imports() -> None:
    runner = _load(_RUNNER, "_task7_v19_fixed_vector_executor_red")

    assert runner._fixed_vector_execution_observation() == {
        "attempt_vector_count": 1,
        "event_vector_count": 3,
        "canonical_failed_event_vector_count": 1,
        "attempt_vector_accepted": True,
        "event_vectors_accepted": True,
        "canonical_failed_event_accepted": True,
    }


def test_v19_attempted_event_constructor_is_closed_and_deterministic() -> None:
    runner = _load(_RUNNER, "_task7_v19_attempted_event_executor_red")
    event = runner._build_attempted_event(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        request_id="33333333-3333-4333-8333-333333333333",
        actor_id="task7-v19-census-admin",
        occurred_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        ordered_uris=("idis://task7-v19-census/44444444-4444-4444-8444-444444444444",),
    )

    assert tuple(event) == (
        "event_id",
        "occurred_at",
        "tenant_id",
        "actor",
        "request",
        "resource",
        "event_type",
        "severity",
        "summary",
        "payload",
    )
    assert event["event_id"] == "593d952b-1aff-5e4e-b673-fe02a28e8f2c"
    assert event["occurred_at"] == "2026-07-24T12:00:00.000000Z"
    assert event["event_type"] == "task7.erasure.attempted"
    assert event["payload"]["safe"] == {
        "request_id": "33333333-3333-4333-8333-333333333333",
        "attempt_id": "fbb3b87f-d9d0-56b4-a983-084fb804d4ae",
        "attempt_ordinal": 1,
        "prior_status": None,
        "prior_attempt_event_id": None,
        "prior_attempt_event_hash": None,
        "outcome": "ATTEMPTED",
    }
    target = runner._object_target_set_sha256(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        ordered_uris=("idis://task7-v19-census/44444444-4444-4444-8444-444444444444",),
    )
    assert event["payload"]["hashes"] == [f"object_target_set_sha256:{target}"]


def test_v19_executor_url_is_bound_to_dedicated_role_and_disposable_database() -> None:
    runner = _load(_RUNNER, "_task7_v19_executor_url_red")
    admin = make_url("postgresql+psycopg://admin:admin@127.0.0.1:15432/template")
    configured = "postgresql+psycopg://idis_erasure_executor:secret@127.0.0.1:15432/operator"

    rebound = runner._executor_url_for_disposable(
        configured_url=configured,
        admin_url=admin,
        database_name="idis_t2b_db_0123456789abcdef",
    )
    assert rebound.username == "idis_erasure_executor"
    assert rebound.host == "127.0.0.1"
    assert rebound.port == 15432
    assert rebound.database == "idis_t2b_db_0123456789abcdef"

    for invalid in (
        "postgresql+psycopg://idis_app:secret@127.0.0.1:15432/operator",
        "postgresql+psycopg://idis_erasure_executor:secret@127.0.0.2:15432/operator",
        "postgresql+psycopg://idis_erasure_executor:secret@127.0.0.1:5432/operator",
    ):
        with pytest.raises(
            runner.CensusError,
            match="TASK7_V19_ERASURE_EXECUTOR_DATABASE_INVALID",
        ):
            runner._executor_url_for_disposable(
                configured_url=invalid,
                admin_url=admin,
                database_name="idis_t2b_db_0123456789abcdef",
            )


@pytest.mark.parametrize("forbidden_privilege", ("INSERT", "REFERENCES"))
def test_v19_column_acl_projection_rejects_every_unreviewed_privilege(
    forbidden_privilege: str,
) -> None:
    runner = _load(
        _RUNNER,
        f"_task7_v19_forbidden_column_acl_{forbidden_privilege.casefold()}_red",
    )
    artifact = _artifact()
    expected = runner._artifact_acl_expectations(artifact)
    rows = [
        (relation, column, "SELECT")
        for relation, *columns in expected["select_relation_column_tuple"]
        for column in columns
    ]
    rows.extend(
        ("erasure_requests", column, "UPDATE") for column in expected["update_column_tuple"]
    )
    accepted = runner._normalize_owner_column_acl_rows(
        artifact=artifact,
        rows=rows,
    )
    assert accepted["select_relation_column_tuple"] == expected["select_relation_column_tuple"]
    assert accepted["update_column_tuple"] == expected["update_column_tuple"]

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_ERASURE_AUTHORITY_EXECUTION_INVALID",
    ):
        runner._normalize_owner_column_acl_rows(
            artifact=artifact,
            rows=[
                *rows,
                ("erasure_requests", "status", forbidden_privilege),
            ],
        )


def test_v19_table_acl_projection_rejects_any_executor_grant() -> None:
    runner = _load(_RUNNER, "_task7_v19_forbidden_executor_table_acl_red")
    artifact = _artifact()
    expected = runner._artifact_acl_expectations(artifact)
    owner_rows = [(relation, "DELETE") for relation in expected["delete_relation_tuple"]]
    accepted = runner._normalize_table_acl_rows(
        artifact=artifact,
        owner_rows=owner_rows,
        executor_rows=(),
    )
    assert accepted["delete_relation_tuple"] == expected["delete_relation_tuple"]
    assert accepted["executor_table_privilege_count"] == 0

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_ERASURE_AUTHORITY_EXECUTION_INVALID",
    ):
        runner._normalize_table_acl_rows(
            artifact=artifact,
            owner_rows=owner_rows,
            executor_rows=(("erasure_requests", "SELECT"),),
        )


def test_v19_execution_proof_invokes_the_local_concrete_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER, "_task7_v19_local_executor_red")
    artifact = _artifact()
    engine = object()
    called: dict[str, object] = {}
    monkeypatch.setattr(runner, "_load_execution_engine", lambda _path: engine)

    def observe(*, execution_engine: object, artifact: dict[str, Any]) -> dict[str, Any]:
        called["execution_engine"] = execution_engine
        called["artifact"] = artifact
        return _observation(runner, artifact)

    monkeypatch.setattr(
        runner,
        "_execute_v19_erasure_authority_observation",
        observe,
    )

    proof = runner.execute_v19_erasure_authority_proof(
        artifact_path=_ARTIFACT,
        artifact=artifact,
    )
    assert proof["application_role_name"].startswith("idis_t2b_app_")
    assert called == {
        "execution_engine": engine,
        "artifact": artifact,
    }


def test_v19_erasure_scaffold_installs_external_helper_owner_before_runner() -> None:
    """The isolated erasure proof must compose the v12 helper ownership boundary."""

    runner = _load(_RUNNER, "_task7_v19_external_helper_composition_red")
    artifact = _artifact()
    calls: list[str] = []

    execution_engine = SimpleNamespace(
        provision_exact_erasure_principals=lambda _admin: calls.append("provision_principals"),
        _install_external_erasure_helper_acl=lambda _admin, _artifact: calls.append(
            "install_external_helper_owner_acl"
        ),
        _grant_external_erasure_helper_read_closure=lambda _admin: calls.append(
            "grant_external_helper_read_closure"
        ),
    )
    scaffold = SimpleNamespace(
        install_artifact_sql_scaffold=lambda _admin, _artifact, **_kwargs: calls.append(
            "install_artifact_scaffold"
        ),
        install_current_erasure_authority=lambda _admin, _artifact: calls.append(
            "install_current_erasure_authority"
        ),
    )

    runner._install_v19_erasure_authority_scaffold(
        execution_engine=execution_engine,
        scaffold=scaffold,
        admin=object(),
        artifact=artifact,
        application_role="idis_t2b_app_0123456789abcdef",
    )

    assert calls == [
        "provision_principals",
        "install_artifact_scaffold",
        "install_external_helper_owner_acl",
        "grant_external_helper_read_closure",
        "install_current_erasure_authority",
    ]


def test_v19_signed_receipt_payload_is_closed_even_when_unknown_field_is_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER, "_task7_v19_closed_receipt_payload_red")
    artifact_raw = b'{"metadata":{}}\n'
    frozen_raw = b'{"metadata":{"append_frozen":true}}\n'
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(runner, "_candidate_artifact", lambda artifact: dict(artifact))
    monkeypatch.setattr(runner, "_prospective_frozen_bytes", lambda _raw: frozen_raw)
    monkeypatch.setattr(
        runner,
        "validate_execution_contract",
        lambda *, result, artifact: None,
    )
    monkeypatch.setattr(
        runner,
        "_database_execution",
        lambda result, artifact: {},
    )
    payload = {
        "schema_version": "task7-v19-postgres-census-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v19-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v19",
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
        "database_execution": {},
        "census_result": {},
        "unknown": "signed-but-unreviewed",
    }
    payload_sha256 = runner._digest(payload)
    public_sha256 = hashlib.sha256(public_raw).hexdigest()
    signature = private_key.sign(
        runner._canonical_bytes(
            [
                runner._CENSUS_SIGNING_DOMAIN,
                "slice100-governance-signer-v1",
                payload_sha256,
                public_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": "task7-v19-postgres-census-envelope-v1",
        "signer_id": "slice100-governance-signer-v1",
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_CENSUS_RECEIPT_INVALID",
    ):
        runner.verify_signed_receipt(
            receipt_raw=(json.dumps(receipt, indent=2) + "\n").encode(),
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_raw,
        )


@pytest.mark.parametrize("live_candidate_state", ("missing", "frozen"))
def test_v19_receipt_source_closure_uses_signed_candidate_bytes_during_recovery(
    live_candidate_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(
        _RUNNER,
        f"_task7_v19_receipt_source_recovery_{live_candidate_state}_red",
    )
    artifact = {"metadata": {"append_frozen": False}}
    candidate_raw = (json.dumps(artifact, indent=2) + "\n").encode()
    frozen = {"metadata": {"append_frozen": True}}
    frozen_raw = (json.dumps(frozen, indent=2) + "\n").encode()
    helper_raw = b"pinned helper\n"
    artifact_relative = "contracts/candidate.json"
    helper_relative = "scripts/helper.py"
    helper_path = tmp_path / helper_relative
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(helper_raw)
    if live_candidate_state == "frozen":
        artifact_path = tmp_path / artifact_relative
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_bytes(frozen_raw)
    manifest = (
        (
            artifact_relative,
            len(candidate_raw),
            hashlib.sha256(candidate_raw).hexdigest(),
            "unfrozen-candidate",
        ),
        (
            helper_relative,
            len(helper_raw),
            hashlib.sha256(helper_raw).hexdigest(),
            "helper",
        ),
    )
    monkeypatch.setattr(runner, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "_V19_MATERIALIZATION_SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(
        runner,
        "_V19_CANDIDATE_SHA256",
        hashlib.sha256(candidate_raw).hexdigest(),
    )
    monkeypatch.setattr(
        runner,
        "_validate_v19_candidate",
        lambda value: dict(value),
    )

    closure = runner.verify_receipt_materialization_source_closure(candidate_raw)
    assert closure["materialization_source_manifest_tuple"] == [list(row) for row in manifest]
    assert len(closure["materialization_source_manifest_sha256"]) == 64
    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_MATERIALIZATION_SOURCE_DRIFT",
    ):
        runner.materialization_source_manifest()


def test_v19_scaffold_and_runner_pin_immutable_v18_sources_and_exact_candidate() -> None:
    scaffold = _load(_SCAFFOLD, "_task7_v19_scaffold_pins_red")
    runner = _load(_RUNNER, "_task7_v19_runner_pins_red")

    assert hashlib.sha256(_V18_SCAFFOLD.read_bytes()).hexdigest() == _V18_SCAFFOLD_SHA256
    assert hashlib.sha256(_V18_RUNNER.read_bytes()).hexdigest() == _V18_RUNNER_SHA256
    assert scaffold._V18_SCAFFOLD_SHA256 == _V18_SCAFFOLD_SHA256
    assert runner._V18_RUNNER_SHA256 == _V18_RUNNER_SHA256
    assert runner._V19_CANDIDATE_SHA256 == _CANDIDATE_SHA256
    assert hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest() == _CANDIDATE_SHA256

    artifact = _artifact()
    runner._validate_v19_candidate(artifact)
    manifest = runner.materialization_source_manifest()
    assert tuple(row[0] for row in manifest) == (
        "contracts/full_strict_v19_reviewed_preimage_v1.json",
        "scripts/_task7_v19_census_scaffold.py",
        "scripts/materialize_full_strict_v19_preimage.py",
        "scripts/generate_task7_v19_contract_fixture.py",
        "scripts/task7_v17_receipt_guard_sql.py",
        "scripts/task7_v17_source_projection_sql.py",
    )


def test_v19_actual_candidate_loads_through_exact_nested_execution_engine() -> None:
    runner = _load(_RUNNER, "_task7_v19_nested_execution_composition_red")
    adapted_source = runner._BASE._BASE._adapt_v16_runner_source(
        runner._BASE._BASE._V16_RUNNER_PATH.read_bytes(),
        _ARTIFACT.read_bytes(),
    )

    assert _V18_ADAPTED_V11_SHA256 not in adapted_source
    assert _V18_ADAPTED_V9_SHA256 not in adapted_source
    assert adapted_source.count(_V19_ADAPTED_V11_SHA256) == 2
    assert adapted_source.count(_V19_ADAPTED_V9_SHA256) == 2

    engine = runner._BASE._BASE._load_execution_engine(_ARTIFACT)
    try:
        assert engine.RESULT_SCHEMA == "task7-v19-current-guard-app-role-census-result-v1"
        assert callable(engine.run_pre_fixture_census)
        coordinates = engine.HARNESS_SOURCE_EXECUTION_COORDINATES
        assert any(row[-1] == _V19_ADAPTED_V11_SHA256 for row in coordinates)
        assert any(row[-1] == _V19_ADAPTED_V9_SHA256 for row in coordinates)
    finally:
        engine._HARNESS_SOURCE_STORE.close()


@pytest.mark.parametrize(
    ("fixture_name", "expected_binding_coordinates"),
    (
        (
            "initial",
            (
                ("created_at", "tenant_id"),
                ("created_at", "deal_id", "tenant_id"),
                ("created_at", "deal_id", "doc_id", "sha256", "tenant_id", "uri"),
                (
                    "deal_id",
                    "reason_hash",
                    "request_id",
                    "requested_at",
                    "requested_by",
                    "tenant_id",
                ),
            ),
        ),
        (
            "retry",
            (
                ("created_at", "tenant_id"),
                ("created_at", "deal_id", "tenant_id"),
                ("created_at", "deal_id", "doc_id", "sha256", "tenant_id", "uri"),
                (
                    "deal_id",
                    "executed_at",
                    "executed_by",
                    "reason_hash",
                    "request_id",
                    "requested_at",
                    "requested_by",
                    "tenant_id",
                ),
            ),
        ),
    ),
)
def test_v19_erasure_fixture_statement_bindings_are_exact_and_closed(
    fixture_name: str,
    expected_binding_coordinates: tuple[tuple[str, ...], ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER, f"_task7_v19_fixture_bindings_{fixture_name}_red")
    observed: list[tuple[str, ...]] = []

    class BindingClosureConnection:
        def execute(
            self,
            statement: Any,
            supplied: dict[str, Any] | None = None,
        ) -> None:
            supplied = {} if supplied is None else supplied
            compiled = statement.compile(dialect=postgresql.dialect())
            compiled.construct_params(supplied)
            declared = tuple(sorted(compiled.params))
            assert declared == tuple(sorted(supplied))
            assert all(not name.isdecimal() for name in declared)
            observed.append(declared)

    connection = BindingClosureConnection()

    class BindingClosureEngine:
        @contextmanager
        def begin(self) -> Any:
            yield connection

    database = SimpleNamespace(
        app_engine=BindingClosureEngine(),
        role_name="idis_t2b_app_0123456789abcdef",
    )
    monkeypatch.setattr(
        runner,
        "_caller_identity",
        lambda _connection: (database.role_name, database.role_name),
    )
    monkeypatch.setattr(runner, "_set_tenant_context", lambda *_args: None)

    if fixture_name == "initial":
        runner._seed_erasure_fixture(database)
    else:
        runner._seed_retry_fixture(
            database,
            fixture={
                "tenant_id": "11111111-1111-4111-8111-111111111111",
                "deal_id": "22222222-2222-4222-8222-222222222222",
                "request_id": "33333333-3333-4333-8333-333333333333",
                "artifact_id": "44444444-4444-4444-8444-444444444444",
                "uri": "idis://task7-v19-bind-closure/artifact",
                "events": (),
            },
        )

    assert tuple(observed) == expected_binding_coordinates
    assert sum(len(row) for row in observed) == (17 if fixture_name == "initial" else 19)


def test_v19_scaffold_decodes_only_the_exact_current_runner_and_acl() -> None:
    scaffold = _load(_SCAFFOLD, "_task7_v19_scaffold_authority_red")
    artifact = _artifact()

    authority = scaffold.erasure_authority(artifact)
    assert authority["runner_function_signature"] == _RUNNER_SIGNATURE
    assert tuple(authority["declared_read_relation_tuple"]) == _READ_CLOSURE
    assert len(authority["current_relation_tuple"]) == 48
    body, acl = scaffold.current_erasure_sql(artifact)
    assert len(body) == 1
    assert body[0].startswith(f"CREATE FUNCTION {_RUNNER_SIGNATURE.partition('(')[0]}(")
    assert len(acl) == 208
    assert acl[-1] == f"GRANT EXECUTE ON FUNCTION {_RUNNER_SIGNATURE} TO idis_erasure_executor"


def test_v19_erasure_execution_expectations_are_derived_from_candidate_bytes() -> None:
    runner = _load(_RUNNER, "_task7_v19_expectations_red")
    artifact = _artifact()

    expected = runner.erasure_authority_execution_expectations(artifact)
    row = artifact["erasure_execution_registry"]["rows"][0]
    body = bytes.fromhex(row["current_body_sql_utf8_hex_tuple"][0])
    acl = b"\0".join(bytes.fromhex(item) for item in row["current_acl_sql_utf8_hex_tuple"])
    assert expected == {
        "runner_function_signature": _RUNNER_SIGNATURE,
        "current_body_sha256": hashlib.sha256(body).hexdigest(),
        "current_acl_sha256": hashlib.sha256(acl).hexdigest(),
        "current_relation_count": 48,
        "declared_read_relation_tuple": list(_READ_CLOSURE),
        "event_outcome_tuple": [list(item) for item in _EVENT_OUTCOMES],
        "attempt_namespace": _ATTEMPT_NAMESPACE,
        "event_namespace": _EVENT_NAMESPACE,
        "owner_update_column_tuple": list(_UPDATE_COLUMNS),
        "owner_select_relation_count": 51,
        "owner_select_column_count": 114,
    }


def test_v19_erasure_execution_proof_is_closed_hashed_and_revalidated() -> None:
    runner = _load(_RUNNER, "_task7_v19_proof_accept_red")
    artifact = _artifact()
    proof = runner.build_erasure_authority_execution_proof(
        artifact=artifact,
        observation=_observation(runner, artifact),
    )

    assert tuple(proof)[-1] == "execution_sha256"
    assert (
        runner.validate_erasure_authority_execution_proof(
            proof,
            artifact=artifact,
        )
        == proof
    )
    assert proof["schema_version"] == "task7-v19-erasure-authority-app-role-census-v1"
    assert proof["base_schema_revision"] == "0037"
    assert proof["artifact_sha256"] == _CANDIDATE_SHA256


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("admin_behavior_case_count",), 1),
        (("application_guard_case_count",), 218),
        (("behavior_actor_tuple", 0), "postgres"),
        (("application_direct_runner_denial", "sqlstate"), "00000"),
        (("missing_attempt_rejection", "zero_mutation"), False),
        (("rollback_atomicity", "transaction_rolled_back"), False),
        (("legal_execution", "counts_equal"), False),
        (("malformed_prior_rejection", "zero_mutation"), False),
        (("failed_retry_execution", "retry_attempt_identity_rederived"), False),
        (("fixed_vector_execution", "canonical_failed_event_accepted"), False),
        (("acl_execution", "application_execute"), True),
        (("acl_execution", "owner_helper_execute_count"), 1),
        (("acl_execution", "owner_owned_relation_count"), 1),
        (("acl_execution", "executor_effective_schema_usage"), False),
        (("acl_execution", "executor_effective_schema_create"), True),
        (("acl_execution", "executor_direct_schema_acl_count"), 1),
        (("acl_execution", "executor_other_routine_execute_count"), 1),
        (("acl_execution", "executor_column_privilege_count"), 1),
        (("acl_execution", "executor_table_privilege_count"), 1),
        (("acl_execution", "catalog_current_user"), "setup_admin"),
        (("acl_execution", "select_acl_sha256"), _sha("tampered-select-acl")),
        (("acl_execution", "delete_relation_tuple", 0), "wrong_relation"),
        (("catalog_projection_sha256",), _sha("tampered-catalog")),
        (("case_result_sha256",), _sha("tampered-cases")),
        (("execution_sha256",), "0" * 64),
    ),
)
def test_v19_erasure_execution_proof_rejects_masking_or_privileged_evidence(
    path: tuple[object, ...],
    replacement: object,
) -> None:
    runner = _load(_RUNNER, f"_task7_v19_proof_reject_{len(path)}_{replacement!s}")
    artifact = _artifact()
    proof = runner.build_erasure_authority_execution_proof(
        artifact=artifact,
        observation=_observation(runner, artifact),
    )
    changed = copy.deepcopy(proof)
    target: Any = changed
    for coordinate in path[:-1]:
        target = target[coordinate]
    target[path[-1]] = replacement

    with pytest.raises(
        runner.CensusError,
        match="TASK7_V19_ERASURE_AUTHORITY_EXECUTION_INVALID",
    ):
        runner.validate_erasure_authority_execution_proof(changed, artifact=artifact)


def test_v19_complete_execution_and_database_projection_require_erasure_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load(_RUNNER, "_task7_v19_complete_contract_red")
    artifact = _artifact()
    erasure_proof = runner.build_erasure_authority_execution_proof(
        artifact=artifact,
        observation=_observation(runner, artifact),
    )
    retained_proof = _retained_proof(runner, artifact)
    base_result = {
        "complete_execution_sha256": _sha("base-complete"),
        "materialization_source_manifest_tuple": (
            runner.materialization_source_manifest_evidence()
        ),
        "materialization_source_manifest_sha256": (runner.materialization_source_manifest_sha256()),
        "downgrade_retained_definition_execution_proof": retained_proof,
    }

    monkeypatch.setattr(
        runner,
        "execution_expectations",
        lambda artifact: {},
    )
    monkeypatch.setattr(
        runner,
        "validate_source_admission_execution_proof",
        lambda proof: None,
    )
    monkeypatch.setattr(
        runner,
        "validate_receipt_equality_execution_proof",
        lambda proof, *, artifact: None,
    )
    result = dict(base_result)
    result["erasure_authority_execution_proof"] = erasure_proof
    runner._rehash_complete_execution(result)
    runner.validate_execution_contract(result=result, artifact=artifact)

    projection = runner._database_execution(result, artifact)
    assert projection["downgrade_retained_definition_execution_proof_sha256"] == runner._digest(
        retained_proof
    )
    assert projection["erasure_authority_execution_proof_sha256"] == runner._digest(erasure_proof)

    for missing in (
        "erasure_authority_execution_proof",
        "downgrade_retained_definition_execution_proof",
    ):
        changed = copy.deepcopy(result)
        changed.pop(missing)
        runner._rehash_complete_execution(changed)
        with pytest.raises(runner.CensusError):
            runner.validate_execution_contract(result=changed, artifact=artifact)
