"""Closed contract for the full-strict-v13 application-role PostgreSQL census."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, cast

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v13_reviewed_preimage_v1.json"
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v13_current_guard_census.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v13_contract_fixture.py"

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v13-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v13-current-guard-app-role-census-result-v1"
GUARD_OWNER_ROLE: Final = "idis_task7_guard_function_owner"
EXPECTED_ARTIFACT_SHA512: Final = (
    "7425c5e7c048a277f6ae8f036a4c3d5f39e35740ed1e5f57168255d3be3c5e4a"
    "29ba9458d3bf63b135643d56ae39d92f2be86a24fe2aac28d9914647eb24d9be"
)

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]
CASE_KINDS: Final[tuple[CaseKind, ...]] = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
EVENT_ORDER: Final[tuple[TriggerEvent, ...]] = ("INSERT", "UPDATE", "DELETE")

TASK6_WRITE_CONTEXT_GUCS: Final = (
    "idis.worker_lease_tenant_id",
    "idis.worker_lease_deal_id",
    "idis.worker_lease_run_id",
    "idis.worker_lease_owner_id",
    "idis.worker_lease_fencing_token",
)
TASK6_OUTPUT_ROOT_UPDATE_TARGETS: Final = (
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
)
PRODUCTION_CONSTRUCTOR_SPECS: Final = (
    (
        "validated-evidence-package-native",
        "task7-validated-evidence-package-native-v1",
        "idis.models.layer1_durability.ValidatedEvidencePackageRow.from_record",
        "validated_evidence_packages",
    ),
    (
        "muhasabah-native",
        "task7-muhasabah-native-v1",
        "idis.models.layer1_durability.MuhasabahRecordRow.from_debate_record",
        "muhasabah_records",
    ),
    (
        "layer2-challenge-native",
        "task7-layer2-challenge-native-v1",
        "idis.models.layer2_durability.Layer2ChallengeRow.from_record",
        "layer2_ic_challenges",
    ),
    (
        "debate-session-native",
        "task7-debate-session-native-v1",
        "idis.debate.orchestrator.DebateOrchestrator.run",
        "debate_sessions",
    ),
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")


def _decode_without_duplicate_members(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in result, f"TASK7_V13_CENSUS_ARTIFACT_DUPLICATE_MEMBER:{key}"
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    assert isinstance(value, dict)
    return value


def artifact_and_raw() -> tuple[dict[str, Any], bytes]:
    assert _ARTIFACT_PATH.is_file(), (
        "TASK7_V13_CENSUS_ARTIFACT_MISSING:contracts/full_strict_v13_reviewed_preimage_v1.json"
    )
    raw = _ARTIFACT_PATH.read_bytes()
    assert hashlib.sha512(raw).hexdigest() == EXPECTED_ARTIFACT_SHA512
    artifact = _decode_without_duplicate_members(raw)
    assert artifact["metadata"]["artifact_schema"] == ("full-strict-v13-reviewed-preimage-v1")
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v13"
    assert artifact["metadata"]["append_frozen"] is False
    return artifact, raw


def current_function_rows() -> tuple[dict[str, Any], ...]:
    artifact, _raw = artifact_and_raw()
    rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    current = tuple(
        cast(dict[str, Any], row) for row in rows if row["install_scope"] == "CURRENT_0038"
    )
    assert len(current) == 19
    assert sum(len(row["target_trigger_tuple"]) for row in current) == 73
    assert sum(len(row["target_event_coordinate_tuple"]) for row in current) == 192
    return current


def expected_trigger_contract_coordinates() -> tuple[tuple[Any, ...], ...]:
    """Return literal hash-bound v13 target/event coordinates without inference."""

    result: list[tuple[Any, ...]] = []
    for function in current_function_rows():
        signature = str(function["function_signature"])
        function_name = signature.partition("(")[0].removeprefix("public.")
        for coordinate in function["target_event_coordinate_tuple"]:
            assert len(coordinate) == 9
            (
                table_name,
                trigger_name,
                timing,
                event,
                level,
                enabled,
                constraint,
                deferrable,
                initially_deferred,
            ) = coordinate
            result.append(
                (
                    str(function["authority_id"]),
                    str(table_name),
                    str(trigger_name),
                    str(timing),
                    str(event),
                    str(level),
                    str(enabled),
                    bool(constraint),
                    bool(deferrable),
                    bool(initially_deferred),
                    "public",
                    function_name,
                )
            )
    coordinates = tuple(result)
    assert len(coordinates) == 192
    assert len(set(coordinates)) == 192
    return coordinates


def expected_trigger_event_inventory() -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (authority, table, trigger, event)
        for (
            authority,
            table,
            trigger,
            _timing,
            event,
            _level,
            _enabled,
            _constraint,
            _deferrable,
            _initially_deferred,
            _schema,
            _function,
        ) in expected_trigger_contract_coordinates()
    )


def expected_ordered_targets() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        dict.fromkeys(
            (authority, table, trigger)
            for authority, table, trigger, _event in expected_trigger_event_inventory()
        )
    )


EXPECTED_ORDERED_TARGETS: Final = expected_ordered_targets()


def expected_trigger_projection() -> tuple[tuple[Any, ...], ...]:
    projection: list[tuple[Any, ...]] = []
    coordinates = expected_trigger_contract_coordinates()
    for authority, table, trigger in EXPECTED_ORDERED_TARGETS:
        rows = tuple(row for row in coordinates if row[:3] == (authority, table, trigger))
        first = rows[0]
        projection.append(
            (
                authority,
                table,
                trigger,
                first[3],
                [row[4] for row in rows],
                first[5],
                first[6],
                "CONSTRAINT" if first[7] else "PLAIN",
                first[8],
                first[9],
                first[10],
                first[11],
            )
        )
    assert len(projection) == 73
    return tuple(projection)


def expected_event_for_case(
    case_kind: CaseKind,
    authority_id: str,
    table_name: str,
    guard_name: str,
) -> TriggerEvent:
    events = tuple(
        cast(TriggerEvent, event)
        for authority, table, guard, event in expected_trigger_event_inventory()
        if (authority, table, guard) == (authority_id, table_name, guard_name)
    )
    assert events
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    ):
        mapping: dict[CaseKind, TriggerEvent] = {
            "LEGAL": "UPDATE",
            "ILLEGAL": "INSERT",
            "DIRECT_SQL": "DELETE",
        }
        assert frozenset(mapping.values()) == frozenset(events)
        return mapping[case_kind]
    if case_kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if case_kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def expected_case_coordinates() -> tuple[tuple[str, str, str, str, str], ...]:
    return tuple(
        (
            case_kind,
            authority_id,
            table_name,
            guard_name,
            expected_event_for_case(
                case_kind,
                authority_id,
                table_name,
                guard_name,
            ),
        )
        for case_kind in CASE_KINDS
        for authority_id, table_name, guard_name in EXPECTED_ORDERED_TARGETS
    )


def expected_rejection(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> tuple[str, str]:
    """Return the stable SQLSTATE/domain marker for every rejected case."""

    assert case_kind != "LEGAL"
    if authority_id == "current-source-exact-one-v1":
        return "23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    if authority_id == "current-output-exact-one-v1":
        return "23514", "STEP_OUTPUT_MISSING"
    if authority_id == "current-receipt-equality-v1":
        return "23514", "CONSUMER_RECEIPT_INCOMPLETE"
    if authority_id == "current-input-binding-admission-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-append-only-lease-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-source-bound-v1":
        return "40001", "TASK7_SOURCE_FREEZE_FENCED"
    if authority_id == "current-output-bound-v1":
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        return "23514", "PIPELINE_CONTRACT_HASH_MISMATCH"
    if authority_id == "current-task5-coverage-replacement-v1":
        return "23514", "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE"
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in TASK6_OUTPUT_ROOT_UPDATE_TARGETS
        and case_kind == "ILLEGAL"
    ):
        return "40001", "WORKER_LEASE_FENCED"
    return "23514", "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE"


@cache
def _generator() -> ModuleType:
    assert _GENERATOR_PATH.is_file(), "TASK7_V13_CENSUS_GENERATOR_MISSING"
    spec = importlib.util.spec_from_file_location(
        "_task7_v13_census_test_generator",
        _GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: Any) -> str:
    return hashlib.sha256(_generator()._canonical_bytes(value)).hexdigest()


@cache
def _load_runner() -> ModuleType | None:
    if not _RUNNER_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_task7_v13_current_guard_census_runner",
        _RUNNER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def runner() -> ModuleType:
    module = _load_runner()
    assert module is not None, (
        "TASK7_V13_EXECUTABLE_CENSUS_RUNNER_MISSING:scripts/run_task7_v13_current_guard_census.py"
    )
    assert getattr(module, "HARNESS_IDENTITY", None) == HARNESS_IDENTITY
    assert getattr(module, "BASE_SCHEMA_REVISION", None) == BASE_SCHEMA_REVISION
    assert getattr(module, "RESULT_SCHEMA", None) == RESULT_SCHEMA
    assert getattr(module, "GUARD_OWNER_ROLE", None) == GUARD_OWNER_ROLE
    run = getattr(module, "run_pre_fixture_census", None)
    assert callable(run), "TASK7_V13_CENSUS_ENTRYPOINT_MISSING"
    return module


@cache
def census_result() -> dict[str, Any]:
    value = runner().run_pre_fixture_census(
        artifact_path=_ARTIFACT_PATH,
        base_schema_revision=BASE_SCHEMA_REVISION,
        harness_identity=HARNESS_IDENTITY,
        ordered_authority_target_tuple=EXPECTED_ORDERED_TARGETS,
        ordered_trigger_contract_coordinate_tuple=(expected_trigger_contract_coordinates()),
        production_constructor_spec_tuple=PRODUCTION_CONSTRUCTOR_SPECS,
    )
    assert isinstance(value, Mapping)
    result = dict(value)
    assert_complete_census_result(result)
    return result


def _assert_digest(value: Any, *, field: str) -> None:
    assert isinstance(value, str) and _SHA256_RE.fullmatch(value), (
        f"TASK7_V13_CENSUS_DIGEST_INVALID:{field}"
    )


def assert_complete_census_result(result: Mapping[str, Any]) -> None:
    """Reject admin-masked, partial, ACL-drifted, or event-incomplete evidence."""

    artifact, raw = artifact_and_raw()
    runner()._validate_complete_census_result(result, artifact_raw=raw)
    assert result["schema_version"] == RESULT_SCHEMA
    assert result["harness_identity"] == HARNESS_IDENTITY
    assert result["base_schema_revision"] == BASE_SCHEMA_REVISION
    assert result["alembic_version_before_install"] == BASE_SCHEMA_REVISION
    assert result["alembic_version_after_install"] == BASE_SCHEMA_REVISION
    assert result["alembic_version_after_cases"] == BASE_SCHEMA_REVISION
    assert result["installation_mode"] == INSTALLATION_MODE
    assert "migration_revision" not in result
    assert result["database_product"] == "PostgreSQL"
    assert isinstance(result["server_version_num"], int)
    assert result["server_version_num"] >= 160000
    assert result["artifact_schema"] == artifact["metadata"]["artifact_schema"]
    assert result["pipeline_contract_version"] == "full-strict-v13"
    assert result["candidate_append_frozen"] is False
    assert result["candidate_artifact_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["ordered_authority_target_tuple"] == [
        list(row) for row in EXPECTED_ORDERED_TARGETS
    ]
    assert result["trigger_contract_coordinate_tuple"] == [
        list(row) for row in expected_trigger_contract_coordinates()
    ]
    assert {tuple(row) for row in result["observed_trigger_contract_coordinate_tuple"]} == set(
        expected_trigger_contract_coordinates()
    )
    assert len(result["observed_trigger_contract_coordinate_tuple"]) == 192
    assert tuple(tuple(row) for row in result["pg_trigger_projection_tuple"]) == (
        expected_trigger_projection()
    )

    outcomes = cast(Sequence[Mapping[str, Any]], result["case_outcome_tuple"])
    assert len(outcomes) == 219
    assert (
        tuple(
            (
                row["case_kind"],
                row["authority_id"],
                row["table_name"],
                row["guard_name"],
                row["trigger_event"],
            )
            for row in outcomes
        )
        == expected_case_coordinates()
    )
    for row in outcomes:
        assert row["executed_as_role"] == result["application_role_name"]
        assert row["observed_session_user"] == result["application_role_name"]
        assert row["observed_is_superuser"] is False
        assert row["observed_bypass_rls"] is False
        if row["case_kind"] == "LEGAL":
            assert row["outcome"] == "ACCEPTED"
            assert row["sqlstate"] is None
            assert row["authority_rejection_marker"] is None
        else:
            assert row["outcome"] == "REJECTED"
            assert isinstance(row["sqlstate"], str)
            assert _SQLSTATE_RE.fullmatch(row["sqlstate"])
            assert row["sqlstate"] != "42501"
            expected_sqlstate, expected_marker = expected_rejection(
                authority_id=str(row["authority_id"]),
                table_name=str(row["table_name"]),
                case_kind=cast(CaseKind, row["case_kind"]),
            )
            assert row["sqlstate"] == expected_sqlstate
            assert row["authority_rejection_marker"] == expected_marker
            assert row["rejection_provenance"] == "HASH_BOUND_AUTHORITY"

    assert result["current_guard_target_count"] == 73
    assert result["current_guard_target_event_count"] == 192
    assert result["case_outcome_count"] == 219
    assert result["legal_execution_count"] == 73
    assert result["illegal_rejection_count"] == 73
    assert result["direct_sql_rejection_count"] == 73
    assert result["application_case_execution_count"] == 219
    assert result["admin_case_execution_count"] == 0
    helper_projections = cast(
        Sequence[Mapping[str, Any]],
        result["admin_fixture_helper_projection_tuple"],
    )
    assert result["admin_fixture_helper_call_count"] == len(helper_projections) == 36
    assert all(
        row["phase"] == "COMMITTED_FIXTURE_SETUP"
        and row["function_signature"]
        == "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
        and row["case_kind"] in {"LEGAL", "DIRECT_SQL"}
        for row in helper_projections
    )
    assert result["constructor_schema_proof_count"] == 4
    assert result["unexpected_outcome_count"] == 0
    assert result["current_routine_catalog_count"] == 19
    assert result["guard_owner_column_acl_relation_count"] == 36
    assert result["guard_owner_column_acl_count"] == 366
    assert result["application_direct_helper_execute_count"] == 0
    assert result["public_direct_helper_execute_count"] == 0
    assert result["force_rls_parent_relation_count"] == 36
    rls_proof = result["security_definer_guard_owner_rls_context_proof"]
    assert rls_proof["proof_scope"] == "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS"
    assert rls_proof["executed_as_role"] == GUARD_OWNER_ROLE
    assert rls_proof["same_tenant_guard_acceptance_count"] == 1
    assert rls_proof["cross_tenant_guard_denial_count"] == 1
    assert rls_proof["unset_tenant_guard_denial_count"] == 1
    schema_acl = cast(
        Sequence[Mapping[str, Any]],
        result["guard_owner_raw_schema_acl_tuple"],
    )
    assert len(schema_acl) == 1
    assert {
        key: schema_acl[0][key]
        for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
    } == {
        "schema_name": "public",
        "grantee": GUARD_OWNER_ROLE,
        "privilege_type": "USAGE",
        "is_grantable": False,
    }
    routines = cast(
        Sequence[Mapping[str, Any]],
        result["current_routine_catalog_tuple"],
    )
    assert all(
        len(row["raw_execute_acl_tuple"]) == 1
        and row["raw_execute_acl_tuple"][0]["grantee"] == GUARD_OWNER_ROLE
        and row["raw_execute_acl_tuple"][0]["grantor"] == GUARD_OWNER_ROLE
        and row["raw_execute_acl_tuple"][0]["privilege_type"] == "EXECUTE"
        and row["raw_execute_acl_tuple"][0]["is_grantable"] is False
        for row in routines
    )
    parity = cast(Mapping[str, Any], result["manifest_parity_projection"])
    parity_fields = (
        "artifact_authority_target_tuple",
        "requested_authority_target_tuple",
        "live_catalog_authority_target_tuple",
        "measured_case_authority_target_tuple",
    )
    assert len({tuple(tuple(value) for value in parity[field]) for field in parity_fields}) == 1
    assert result["manifest_parity_sha256"] == digest(["task7-v13-manifest-parity-v1", parity])

    fixture_rejections = tuple(tuple(row) for row in result["fixture_rejection_proof_tuple"])
    assert fixture_rejections == (
        (
            "UNKNOWN_FIELD",
            "runs",
            "unknown_fixture_field",
            "TASK7_V13_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
        ),
        (
            "OTHER_TARGET_FIELD",
            "runs",
            "step_id",
            "TASK7_V13_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
        ),
        (
            "GENERATED_FIELD",
            "methodology_extraction_results",
            "run_output_object_kind",
            "TASK7_V13_CENSUS_FIXTURE_GENERATED_FIELD:"
            "methodology_extraction_results.run_output_object_kind",
        ),
    )
    proofs = cast(Sequence[Mapping[str, Any]], result["constructor_proof_tuple"])
    assert len(proofs) == 4
    assert all(
        proof["original_payload_sha256"] == proof["reloaded_payload_sha256"] for proof in proofs
    )
    for field in (
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "manifest_parity_sha256",
        "pg_trigger_inventory_sha256",
        "trigger_event_coverage_sha256",
        "guard_acl_projection_sha256",
        "rls_context_proof_sha256",
        "complete_execution_sha256",
    ):
        _assert_digest(result[field], field=field)
    assert result["complete_execution_sha256"] == digest(
        [
            "task7-v13-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    assert "v11" not in json.dumps(result, sort_keys=True).lower()


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CASE_KINDS",
    "EXPECTED_ORDERED_TARGETS",
    "GUARD_OWNER_ROLE",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "PRODUCTION_CONSTRUCTOR_SPECS",
    "RESULT_SCHEMA",
    "TASK6_OUTPUT_ROOT_UPDATE_TARGETS",
    "TASK6_WRITE_CONTEXT_GUCS",
    "artifact_and_raw",
    "assert_complete_census_result",
    "census_result",
    "current_function_rows",
    "digest",
    "expected_case_coordinates",
    "expected_event_for_case",
    "expected_ordered_targets",
    "expected_rejection",
    "expected_trigger_contract_coordinates",
    "expected_trigger_event_inventory",
    "expected_trigger_projection",
    "runner",
]
