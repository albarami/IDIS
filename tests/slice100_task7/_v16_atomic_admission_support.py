"""Hermetic full-shape receipts for v16 atomic-admission tests.

The production PostgreSQL census remains the only acceptance authority.  This
module constructs the same closed 6-field envelope, 8-field payload, and
66-field result graph so the publisher can be tested without a database or a
repository publication target.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
RUNNER_SHA256 = "6974b148b4e7f8b7fcd85ab476283f117c246e39058ff0acc920766180747e43"

CENSUS_ENVELOPE_SCHEMA = "task7-v16-postgres-census-envelope-v1"
CENSUS_PAYLOAD_SCHEMA = "task7-v16-postgres-census-v1"
CENSUS_SIGNING_DOMAIN = "task7-v16-postgres-census-signable-v1"
HARNESS_IDENTITY = (
    "task7-v16-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v2:"
    "ed15c8f2391017cf42753be102dd1a4d69d6ea31345baa491cf9e86c6c4ed59c"
)
RESULT_SCHEMA = "task7-v16-current-guard-app-role-census-result-v1"
INSTALLATION_MODE = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
GUARD_OWNER = "idis_task7_guard_function_owner"
ERASURE_OWNER = "idis_erasure_function_owner"
APP_ROLE = "idis_v16_atomic_test_app"
ADMIN_ROLE = "idis_v16_atomic_test_admin"
_RESULT_CACHE: dict[str, dict[str, Any]] = {}


def render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key


@cache
def load_runner() -> ModuleType:
    raw = RUNNER_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RUNNER_SHA256
    module_name = "_task7_v16_atomic_receipt_runner"
    module = ModuleType(module_name)
    module.__file__ = str(RUNNER_PATH)
    module.__package__ = ""
    sys.modules[module_name] = module
    code = compile(raw, str(RUNNER_PATH), "exec")
    exec(code, module.__dict__)  # noqa: S102 - exact hash-pinned source bytes
    return module


def _current_functions(artifact: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = artifact["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
    result = tuple(row for row in rows if row["install_scope"] == "CURRENT_0038")
    assert len(result) == 19
    return result


def _outcomes(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    runner = load_runner()
    contract = runner._artifact_trigger_contract(artifact)
    targets = runner._artifact_targets(artifact)
    timing = {(str(row[0]), str(row[1]), str(row[2]), str(row[4])): str(row[3]) for row in contract}
    events_by_target = {
        target: tuple(str(row[4]) for row in contract if tuple(map(str, row[:3])) == target)
        for target in targets
    }
    for case_kind in ("LEGAL", "ILLEGAL", "DIRECT_SQL"):
        for authority_id, table_name, guard_name in targets:
            trigger_event = runner._event_for_case(
                case_kind=case_kind,
                authority_id=authority_id,
                table_name=table_name,
                events=events_by_target[(authority_id, table_name, guard_name)],
            )
            trigger_timing = timing[(authority_id, table_name, guard_name, trigger_event)]
            if case_kind == "LEGAL":
                outcome = "ACCEPTED"
                sqlstate = None
                marker = None
                provenance = None
            else:
                outcome = "REJECTED"
                sqlstate, marker = runner._expected_rejection(
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
                provenance = "HASH_BOUND_AUTHORITY"
            result.append(
                {
                    "case_kind": case_kind,
                    "authority_id": authority_id,
                    "table_name": table_name,
                    "guard_name": guard_name,
                    "trigger_event": trigger_event,
                    "trigger_timing": trigger_timing,
                    "constraint_flush_attempted": trigger_timing == "AFTER",
                    "scenario_id": runner._scenario_id(
                        authority_id=authority_id,
                        table_name=table_name,
                        case_kind=case_kind,
                    ),
                    "execution_path": (
                        "DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION"
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
    runner = load_runner()
    for ordinal, (schema_id, version, constructor, table) in enumerate(
        runner._PRODUCTION_CONSTRUCTOR_SPECS
    ):
        digest = hashlib.sha256(f"v16-constructor:{ordinal}".encode()).hexdigest()
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


def _external_helper() -> dict[str, Any]:
    return {
        "function_signature": ("public.idis_task7_approved_erasure_context_v1(uuid,uuid)"),
        "guard_owner_execute": True,
        "application_direct_execute": False,
        "public_direct_execute": False,
        "application_direct_call_denied": True,
        "ordinary_guard_call_result": False,
        "function_owner": ERASURE_OWNER,
        "security_definer": True,
        "search_path_tuple": ["pg_catalog", "pg_temp"],
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


def _recompute_digests(generator: ModuleType, result: dict[str, Any]) -> None:
    outcomes = result["case_outcome_tuple"]
    constructors = result["constructor_proof_tuple"]
    helpers = result["admin_fixture_helper_projection_tuple"]
    manifest = result["manifest_parity_projection"]
    routines = result["current_routine_catalog_tuple"]
    attributes = result["guard_owner_raw_attribute_acl_tuple"]
    schema_acl = result["guard_owner_raw_schema_acl_tuple"]
    external = result["external_erasure_helper_proof"]
    rls = result["security_definer_guard_owner_rls_context_proof"]
    result["catalog_projection_sha256"] = generator._h(
        ["task7-v16-census-catalog-projection-v1", result["catalog_projection_tuple"]]
    )
    result["case_result_sha256"] = generator._h(
        ["task7-v16-current-guard-case-results-v1", outcomes]
    )
    result["constructor_roundtrip_sha256"] = generator._h(
        ["task7-v16-constructor-roundtrips-v1", constructors]
    )
    result["admin_fixture_helper_projection_sha256"] = generator._h(
        ["task7-v16-admin-fixture-helper-projection-v1", helpers]
    )
    result["manifest_parity_sha256"] = generator._h(["task7-v16-manifest-parity-v1", manifest])
    result["pg_trigger_inventory_sha256"] = generator._h(
        ["task7-v16-pg-trigger-projection-v1", result["pg_trigger_projection_tuple"]]
    )
    event_coverage = [
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
    result["trigger_event_coverage_sha256"] = generator._h(
        ["task7-v16-trigger-event-coverage-v1", event_coverage]
    )
    result["guard_acl_projection_sha256"] = generator._h(
        [
            "task7-v16-guard-acl-projection-v1",
            routines,
            attributes,
            schema_acl,
            result["guard_owner_posture"],
            external,
        ]
    )
    result["rls_context_proof_sha256"] = generator._h(
        ["task7-v16-security-definer-guard-owner-rls-context-proof-v1", rls]
    )
    result["complete_execution_sha256"] = generator._h(
        [
            "task7-v16-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )


def census_result(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
) -> dict[str, Any]:
    cache_key = hashlib.sha256(candidate_raw).hexdigest()
    cached = _RESULT_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    artifact = generator._decode_artifact(candidate_raw)
    frozen = generator.derive_prospective_frozen_bytes(candidate_raw)
    runner = load_runner()
    contract_tuple = runner._artifact_trigger_contract(artifact)
    targets_tuple = runner._artifact_targets(artifact)
    contract = [list(row) for row in contract_tuple]
    targets = [list(row) for row in targets_tuple]
    outcomes = _outcomes(artifact)
    constructors = _constructors()
    routines = _routine_catalog(artifact)
    attributes = _attribute_acl(artifact)
    schema_acl = [
        {
            "schema_name": "public",
            "grantee": GUARD_OWNER,
            "grantor": ADMIN_ROLE,
            "privilege_type": "USAGE",
            "is_grantable": False,
        }
    ]
    external = _external_helper()
    rls = {
        "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
        "executed_as_role": GUARD_OWNER,
        "same_tenant_guard_acceptance_count": 1,
        "cross_tenant_guard_denial_count": 1,
        "unset_tenant_guard_denial_count": 1,
    }
    helper_targets = [
        target for target in targets_tuple if target[0] == "current-output-exact-one-v1"
    ]
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
            "executed_as_role": ADMIN_ROLE,
            "output_kind": "TEST_NATIVE_OUTPUT",
            "returned_pair_sha256": hashlib.sha256(f"v16:{kind}:{table_name}".encode()).hexdigest(),
        }
        for kind in ("LEGAL", "DIRECT_SQL")
        for authority_id, table_name, guard_name in helper_targets
    ]
    assert len(helpers) == 36
    manifest = dict.fromkeys(
        (
            "artifact_authority_target_tuple",
            "requested_authority_target_tuple",
            "live_catalog_authority_target_tuple",
            "measured_case_authority_target_tuple",
        ),
        targets,
    )
    catalog = [
        ["TRIGGER", table_name, guard_name, authority_id]
        for authority_id, table_name, guard_name in targets_tuple
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
        "artifact_schema": "full-strict-v16-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v16",
        "candidate_append_frozen": False,
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(frozen).hexdigest(),
        "ordered_authority_target_tuple": targets,
        "trigger_contract_coordinate_tuple": contract,
        "observed_trigger_contract_coordinate_tuple": list(reversed(contract)),
        "catalog_projection_tuple": catalog,
        "pg_trigger_projection_tuple": [
            list(row)
            for row in runner._expected_trigger_projection(
                contract_tuple,
                targets_tuple,
            )
        ],
        "case_outcome_tuple": outcomes,
        "constructor_proof_tuple": constructors,
        "fixture_rejection_proof_tuple": [
            [
                "UNKNOWN_FIELD",
                "runs",
                "unknown_fixture_field",
                "TASK7_V16_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
            ],
            [
                "OTHER_TARGET_FIELD",
                "runs",
                "step_id",
                "TASK7_V16_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
            ],
            [
                "GENERATED_FIELD",
                "methodology_extraction_results",
                "run_output_object_kind",
                "TASK7_V16_CENSUS_FIXTURE_GENERATED_FIELD:"
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
        "security_definer_guard_owner_rls_context_proof": rls,
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
        "catalog_projection_sha256": "",
        "case_result_sha256": "",
        "constructor_roundtrip_sha256": "",
        "admin_fixture_helper_projection_sha256": "",
        "manifest_parity_sha256": "",
        "pg_trigger_inventory_sha256": "",
        "trigger_event_coverage_sha256": "",
        "guard_acl_projection_sha256": "",
        "rls_context_proof_sha256": "",
        "complete_execution_sha256": "",
    }
    _recompute_digests(generator, result)
    runner = load_runner()
    runner._validate_complete_census_result(result, artifact_raw=candidate_raw)
    assert len(result) == 66
    _RESULT_CACHE[cache_key] = copy.deepcopy(result)
    return result


def database_execution(result: Mapping[str, Any]) -> dict[str, Any]:
    runner = load_runner()
    value = runner._database_execution(result)
    assert len(value) == 26
    return value


def resign(
    *,
    generator: ModuleType,
    receipt: dict[str, Any],
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
    recompute_result: bool = False,
) -> bytes:
    payload = receipt["payload"]
    if recompute_result:
        result = payload["census_result"]
        _recompute_digests(generator, result)
        payload["database_execution"] = database_execution(result)
    payload_sha256 = generator._h(payload)
    public_key_sha256 = hashlib.sha256(public_key_raw).hexdigest()
    receipt["payload_sha256"] = payload_sha256
    receipt["public_key_sha256"] = public_key_sha256
    receipt["signature_base64"] = base64.b64encode(
        private_key.sign(
            generator._canonical_bytes(
                [
                    CENSUS_SIGNING_DOMAIN,
                    receipt["signer_id"],
                    payload_sha256,
                    public_key_sha256,
                    payload,
                ]
            )
        )
    ).decode("ascii")
    return render(receipt)


def signed_receipt(
    *,
    generator: ModuleType,
    candidate_raw: bytes,
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
    mutate_payload: Callable[[dict[str, Any]], None] | None = None,
    signing_key: Ed25519PrivateKey | None = None,
) -> tuple[bytes, dict[str, Any]]:
    result = census_result(generator=generator, candidate_raw=candidate_raw)
    payload = {
        "schema_version": CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": "full-strict-v16-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v16",
        "candidate_artifact_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": result["prospective_frozen_artifact_sha256"],
        "database_execution": database_execution(result),
        "census_result": result,
    }
    if mutate_payload is not None:
        mutate_payload(payload)
    signer = "task7-v16-test-census-signer-v1"
    receipt: dict[str, Any] = {
        "schema_version": CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer,
        "payload": payload,
        "payload_sha256": "",
        "public_key_sha256": "",
        "signature_base64": "",
    }
    raw = resign(
        generator=generator,
        receipt=receipt,
        private_key=signing_key or private_key,
        public_key_raw=public_key_raw,
    )
    return raw, copy.deepcopy(payload)
