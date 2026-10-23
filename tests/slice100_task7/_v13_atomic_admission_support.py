"""Hermetic signed-receipt inputs for v13 atomic-admission tests.

The production census remains the authority for acceptance.  These fixtures
construct the same closed receipt shape from the reviewed artifact so the
publisher's cryptographic and crash-consistency behavior can be exercised
without a database or a real publication target.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CENSUS_ENVELOPE_SCHEMA = "task7-v13-postgres-census-envelope-v1"
CENSUS_PAYLOAD_SCHEMA = "task7-v13-postgres-census-v1"
CENSUS_SIGNING_DOMAIN = "task7-v13-postgres-census-signable-v1"
HARNESS_IDENTITY = "task7-v13-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v1"
RESULT_SCHEMA = "task7-v13-current-guard-app-role-census-result-v1"
INSTALLATION_MODE = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
GUARD_OWNER = "idis_task7_guard_function_owner"
APP_ROLE = "idis_v13_atomic_test_app"
ERASURE_OWNER = "idis_erasure_function_owner"

PRODUCTION_CONSTRUCTORS = (
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


def render(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key


def _current_functions(artifact: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    result = tuple(row for row in rows if row["install_scope"] == "CURRENT_0038")
    assert len(result) == 19
    return result


def trigger_contract(artifact: Mapping[str, Any]) -> list[list[Any]]:
    result: list[list[Any]] = []
    for function in _current_functions(artifact):
        function_name = (
            str(function["function_signature"]).partition("(")[0].removeprefix("public.")
        )
        for coordinate in function["target_event_coordinate_tuple"]:
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
                [
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
                ]
            )
    assert len(result) == 192
    return result


def ordered_targets(contract: Sequence[Sequence[Any]]) -> list[list[str]]:
    result: list[list[str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in contract:
        target = tuple(map(str, row[:3]))
        if target not in seen:
            seen.add(target)
            result.append(list(target))
    assert len(result) == 73
    return result


def trigger_projection(
    contract: Sequence[Sequence[Any]],
    targets: Sequence[Sequence[str]],
) -> list[list[Any]]:
    result: list[list[Any]] = []
    for authority_id, table_name, trigger_name in targets:
        rows = [
            row
            for row in contract
            if tuple(map(str, row[:3])) == (authority_id, table_name, trigger_name)
        ]
        first = rows[0]
        result.append(
            [
                authority_id,
                table_name,
                trigger_name,
                first[3],
                [row[4] for row in rows],
                first[5],
                first[6],
                "CONSTRAINT" if first[7] else "PLAIN",
                first[8],
                first[9],
                first[10],
                first[11],
            ]
        )
    return result


def _event_for_case(
    *,
    kind: str,
    authority_id: str,
    table_name: str,
    events: Sequence[str],
) -> str:
    if authority_id == "current-task6-immutable-replacement-v1" and table_name in {
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
    }:
        return {
            "LEGAL": "UPDATE",
            "ILLEGAL": "INSERT",
            "DIRECT_SQL": "DELETE",
        }[kind]
    if kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def _rejection(authority_id: str, table_name: str, kind: str) -> tuple[str, str]:
    if authority_id == "current-source-exact-one-v1":
        return "23514", "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    if authority_id == "current-output-exact-one-v1":
        return "23514", "STEP_OUTPUT_MISSING"
    if authority_id == "current-receipt-equality-v1":
        return "23514", "CONSUMER_RECEIPT_INCOMPLETE"
    if authority_id in {
        "current-input-binding-admission-v1",
        "current-append-only-lease-v1",
        "current-output-bound-v1",
    }:
        return "23514", "STEP_REHYDRATION_FAILED"
    if authority_id == "current-source-bound-v1":
        return "40001", "TASK7_SOURCE_FREEZE_FENCED"
    if authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        return "23514", "PIPELINE_CONTRACT_HASH_MISMATCH"
    if authority_id == "current-task5-coverage-replacement-v1":
        return "23514", "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE"
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name
        in {
            "methodology_extraction_results",
            "methodology_result_factual_outputs",
        }
        and kind == "ILLEGAL"
    ):
        return "40001", "WORKER_LEASE_FENCED"
    return "23514", "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE"


def _outcomes(
    contract: Sequence[Sequence[Any]],
    targets: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    events_by_target = {
        tuple(target): [
            str(row[4]) for row in contract if tuple(map(str, row[:3])) == tuple(target)
        ]
        for target in targets
    }
    timing_by_event = {(*tuple(map(str, row[:3])), str(row[4])): str(row[3]) for row in contract}
    result: list[dict[str, Any]] = []
    for kind in ("LEGAL", "ILLEGAL", "DIRECT_SQL"):
        for authority_id, table_name, guard_name in targets:
            event = _event_for_case(
                kind=kind,
                authority_id=authority_id,
                table_name=table_name,
                events=events_by_target[(authority_id, table_name, guard_name)],
            )
            timing = timing_by_event[(authority_id, table_name, guard_name, event)]
            if kind == "LEGAL":
                outcome = "ACCEPTED"
                sqlstate = None
                marker = None
                provenance = None
            else:
                outcome = "REJECTED"
                sqlstate, marker = _rejection(authority_id, table_name, kind)
                provenance = "HASH_BOUND_AUTHORITY"
            result.append(
                {
                    "case_kind": kind,
                    "authority_id": authority_id,
                    "table_name": table_name,
                    "guard_name": guard_name,
                    "trigger_event": event,
                    "trigger_timing": timing,
                    "constraint_flush_attempted": timing == "AFTER",
                    "scenario_id": hashlib.sha256(
                        f"{authority_id}:{table_name}:{kind}".encode()
                    ).hexdigest(),
                    "execution_path": (
                        "DIRECT_SQL" if kind == "DIRECT_SQL" else "GUARDED_OPERATION"
                    ),
                    "executed_as_role": APP_ROLE,
                    "observed_session_user": APP_ROLE,
                    "observed_is_superuser": False,
                    "observed_bypass_rls": False,
                    "outcome": outcome,
                    "sqlstate": sqlstate,
                    "authority_rejection_marker": marker,
                    "rejection_provenance": provenance,
                }
            )
    assert len(result) == 219
    return result


def _routine_catalog(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "function_signature": row["function_signature"],
            "function_kind": row["function_kind"],
            "owner": GUARD_OWNER,
            "security_definer": row["security_mode"] == "DEFINER",
            "search_path": "pg_catalog, pg_temp",
            "application_direct_execute": False,
            "public_direct_execute": False,
            "raw_execute_acl_tuple": [
                {
                    "grantee": GUARD_OWNER,
                    "grantor": GUARD_OWNER,
                    "privilege_type": "EXECUTE",
                    "is_grantable": False,
                }
            ],
        }
        for row in _current_functions(artifact)
    ]


def _attribute_acl(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    role = artifact["sql_authority_registry"]["guard_function_acl_registry"]["guard_owner_role"]
    result = [
        {
            "relation": relation,
            "column": column,
            "privilege_type": "SELECT",
            "is_grantable": False,
            "raw_attacl": f"{GUARD_OWNER}=r/{GUARD_OWNER}",
        }
        for relation, columns in role["select_relation_column_tuple"]
        for column in columns
    ]
    assert len(result) == 366
    return result


def _constructors() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ordinal, (schema_id, version, constructor, table) in enumerate(PRODUCTION_CONSTRUCTORS):
        digest = hashlib.sha256(f"constructor:{ordinal}".encode()).hexdigest()
        result.append(
            {
                "schema_id": schema_id,
                "schema_version": version,
                "constructor_qualname": constructor,
                "durable_table": table,
                "outcome": "ROUNDTRIP_MATCH",
                "original_payload_sha256": digest,
                "reloaded_payload_sha256": digest,
            }
        )
    return result


def census_result(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
) -> dict[str, Any]:
    artifact = generator._decode_artifact(candidate_raw)
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    contract = trigger_contract(artifact)
    targets = ordered_targets(contract)
    outcomes = _outcomes(contract, targets)
    constructors = _constructors()
    routines = _routine_catalog(artifact)
    attributes = _attribute_acl(artifact)
    helper_targets = [target for target in targets if target[0] == "current-output-exact-one-v1"]
    helpers = [
        {
            "authority_id": authority_id,
            "table_name": table_name,
            "guard_name": guard_name,
            "case_kind": kind,
            "phase": "COMMITTED_FIXTURE_SETUP",
            "function_signature": (
                "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
            ),
            "executed_as_role": "idis_v13_atomic_test_admin",
            "output_kind": "TEST_NATIVE_OUTPUT",
            "returned_pair_sha256": hashlib.sha256(f"{kind}:{table_name}".encode()).hexdigest(),
        }
        for kind in ("LEGAL", "DIRECT_SQL")
        for authority_id, table_name, guard_name in helper_targets
    ]
    assert len(helpers) == 36
    schema_acl = [
        {
            "schema_name": "public",
            "grantee": GUARD_OWNER,
            "grantor": "idis_v13_atomic_test_admin",
            "privilege_type": "USAGE",
            "is_grantable": False,
        }
    ]
    guard_posture = {
        "role_name": GUARD_OWNER,
        "can_login": False,
        "is_superuser": False,
        "can_create_db": False,
        "can_create_role": False,
        "inherits": False,
        "bypass_rls": False,
        "membership_count": 0,
        "owned_relation_count": 0,
        "owned_routine_count": 19,
        "schema_usage": True,
        "schema_create": False,
        "table_select_privilege_count": 0,
        "table_insert_privilege_count": 0,
        "table_update_privilege_count": 0,
        "table_delete_privilege_count": 0,
        "table_truncate_privilege_count": 0,
        "table_references_privilege_count": 0,
        "table_trigger_privilege_count": 0,
    }
    external = {
        "function_signature": ("public.idis_task7_approved_erasure_context_v1(uuid,uuid)"),
        "guard_owner_execute": True,
        "application_direct_execute": False,
        "public_direct_execute": False,
        "ordinary_guard_call_result": False,
        "function_owner": ERASURE_OWNER,
        "raw_execute_acl_tuple": [
            {
                "grantee": ERASURE_OWNER,
                "grantor": ERASURE_OWNER,
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            },
            {
                "grantee": GUARD_OWNER,
                "grantor": ERASURE_OWNER,
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            },
        ],
    }
    rls_proof = {
        "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
        "executed_as_role": GUARD_OWNER,
        "same_tenant_guard_acceptance_count": 1,
        "cross_tenant_guard_denial_count": 1,
        "unset_tenant_guard_denial_count": 1,
    }
    manifest = dict.fromkeys(
        (
            "artifact_authority_target_tuple",
            "requested_authority_target_tuple",
            "live_catalog_authority_target_tuple",
            "measured_case_authority_target_tuple",
        ),
        targets,
    )
    trigger_event_coverage = [
        list(value)
        for value in sorted(
            {
                (
                    row["authority_id"],
                    row["table_name"],
                    row["guard_name"],
                    row["trigger_event"],
                )
                for row in outcomes
            }
        )
    ]
    catalog = [
        ["TRIGGER", table_name, guard_name, authority_id]
        for authority_id, table_name, guard_name in targets
    ]
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "harness_identity": HARNESS_IDENTITY,
        "base_schema_revision": "0037",
        "alembic_version_before_install": "0037",
        "alembic_version_after_install": "0037",
        "alembic_version_after_cases": "0037",
        "installation_mode": INSTALLATION_MODE,
        "database_product": "PostgreSQL",
        "server_version_num": 160010,
        "artifact_schema": "full-strict-v13-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v13",
        "candidate_append_frozen": False,
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen_raw).hexdigest(),
        "ordered_authority_target_tuple": targets,
        "trigger_contract_coordinate_tuple": contract,
        "observed_trigger_contract_coordinate_tuple": list(reversed(contract)),
        "catalog_projection_tuple": catalog,
        "pg_trigger_projection_tuple": trigger_projection(contract, targets),
        "case_outcome_tuple": outcomes,
        "constructor_proof_tuple": constructors,
        "fixture_rejection_proof_tuple": [
            [
                "UNKNOWN_FIELD",
                "runs",
                "unknown_fixture_field",
                "TASK7_V13_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
            ],
            [
                "OTHER_TARGET_FIELD",
                "runs",
                "step_id",
                "TASK7_V13_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
            ],
            [
                "GENERATED_FIELD",
                "methodology_extraction_results",
                "run_output_object_kind",
                "TASK7_V13_CENSUS_FIXTURE_GENERATED_FIELD:"
                "methodology_extraction_results.run_output_object_kind",
            ],
        ],
        "application_role_name": APP_ROLE,
        "database_actor": {
            "role_name": APP_ROLE,
            "session_user": APP_ROLE,
            "is_superuser": False,
            "bypass_rls": False,
            "case_execution_count": 219,
        },
        "guard_owner_posture": guard_posture,
        "current_routine_catalog_tuple": routines,
        "guard_owner_raw_attribute_acl_tuple": attributes,
        "guard_owner_raw_schema_acl_tuple": schema_acl,
        "external_erasure_helper_proof": external,
        "admin_fixture_helper_projection_tuple": helpers,
        "security_definer_guard_owner_rls_context_proof": rls_proof,
        "manifest_parity_projection": manifest,
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 192,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "application_case_execution_count": 219,
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "constructor_schema_proof_count": 4,
        "unexpected_outcome_count": 0,
        "current_routine_catalog_count": 19,
        "guard_owner_column_acl_relation_count": 36,
        "guard_owner_column_acl_count": 366,
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "force_rls_parent_relation_count": 36,
        "task6_output_root_update_acceptance_count": 2,
        "strict_input_binding_queued_admission_count": 1,
        "expected_edge_queued_admission_count": 1,
        "package_open_to_sealed_binding_count": 1,
        "methodology_commercial_question_count": 21,
        "methodology_financial_question_count": 79,
        "methodology_coverage_count": 100,
        "catalog_projection_sha256": generator._h(
            ["task7-v13-census-catalog-projection-v1", catalog]
        ),
        "case_result_sha256": generator._h(["task7-v13-current-guard-case-results-v1", outcomes]),
        "constructor_roundtrip_sha256": generator._h(
            ["task7-v13-constructor-roundtrips-v1", constructors]
        ),
        "admin_fixture_helper_projection_sha256": generator._h(
            ["task7-v13-admin-fixture-helper-projection-v1", helpers]
        ),
        "manifest_parity_sha256": generator._h(["task7-v13-manifest-parity-v1", manifest]),
        "pg_trigger_inventory_sha256": generator._h(
            ["task7-v13-pg-trigger-projection-v1", trigger_projection(contract, targets)]
        ),
        "trigger_event_coverage_sha256": generator._h(
            ["task7-v13-trigger-event-coverage-v1", trigger_event_coverage]
        ),
        "guard_acl_projection_sha256": generator._h(
            [
                "task7-v13-guard-acl-projection-v1",
                routines,
                attributes,
                schema_acl,
                guard_posture,
                external,
            ]
        ),
        "rls_context_proof_sha256": generator._h(
            [
                "task7-v13-security-definer-guard-owner-rls-context-proof-v1",
                rls_proof,
            ]
        ),
    }
    result["complete_execution_sha256"] = generator._h(["task7-v13-complete-execution-v1", result])
    return result


def database_execution(result: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "application_case_execution_count",
        "admin_case_execution_count",
        "admin_fixture_helper_call_count",
        "current_guard_target_count",
        "current_guard_target_event_count",
        "case_outcome_count",
        "legal_execution_count",
        "illegal_rejection_count",
        "direct_sql_rejection_count",
        "constructor_schema_proof_count",
        "current_routine_catalog_count",
        "guard_owner_column_acl_relation_count",
        "guard_owner_column_acl_count",
        "application_direct_helper_execute_count",
        "public_direct_helper_execute_count",
        "unexpected_outcome_count",
        "catalog_projection_sha256",
        "case_result_sha256",
        "constructor_roundtrip_sha256",
        "admin_fixture_helper_projection_sha256",
        "manifest_parity_sha256",
        "pg_trigger_inventory_sha256",
        "trigger_event_coverage_sha256",
        "guard_acl_projection_sha256",
        "rls_context_proof_sha256",
        "complete_execution_sha256",
    )
    return {field: result[field] for field in fields}


def signed_receipt(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
    mutate_payload: Callable[[dict[str, Any]], None] | None = None,
    signing_key: Ed25519PrivateKey | None = None,
) -> tuple[bytes, dict[str, Any]]:
    artifact = generator._decode_artifact(candidate_raw)
    result = census_result(generator=generator, candidate_raw=candidate_raw)
    payload = {
        "schema_version": CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v13",
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": result["prospective_frozen_artifact_sha256"],
        "database_execution": database_execution(result),
        "census_result": result,
    }
    if mutate_payload is not None:
        mutate_payload(payload)
    payload_sha256 = generator._h(payload)
    public_key_sha256 = hashlib.sha256(public_key_raw).hexdigest()
    signer_id = "task7-v13-test-census-signer-v1"
    signature = (signing_key or private_key).sign(
        generator._canonical_bytes(
            [
                CENSUS_SIGNING_DOMAIN,
                signer_id,
                payload_sha256,
                public_key_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    return render(receipt), payload
