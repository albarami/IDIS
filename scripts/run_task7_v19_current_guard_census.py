"""Execute and sign the unfrozen full-strict-v19 PostgreSQL census.

The append-frozen v18 census engine is immutable.  V19 hash-pins and adapts
that reviewed engine for the unchanged 219 application-role guard cases, four
production constructors, source admission, receipt equality, and retained
definition proof.  It adds one closed proof for the v19 erasure authority.

Setup-admin access is limited to disposable-database setup and catalog
inspection.  Guard behavior is executed as the disposable application role;
the erasure function is executed as the dedicated erasure-executor role, and
the application role must be denied direct execution with zero mutation.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast
from uuid import UUID, uuid5

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError

_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_V18_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v18_current_guard_census.py")
_V18_RUNNER_SHA256: Final = "10988bb9cbbc368b496c7d9e55177b5d7390219d8d38dc70e40e10583a6cf006"
_V19_SCAFFOLD_PATH: Final = Path(__file__).with_name("_task7_v19_census_scaffold.py")
_V19_SCAFFOLD_SHA256: Final = "a7460f38928b52ed5f31b2838e28c0fbdab5d8134d38b67845da0a695e808b8f"
_V19_CANDIDATE_PATH: Final = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"
_V19_CANDIDATE_SHA256: Final = "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
_V19_NESTED_ADAPTER_DIGEST_REPLACEMENTS: Final = (
    (
        "74aa26548edff669726c729ede2e339e3d6a162142ee63878f94cddc476c5317",
        "58e3bbbda00e6653bd2e4eeecd2ca7859d2340c3e40ed9d62e69038f903a64cc",
    ),
    (
        "a0e19f70a497cf75ac970c3d035326abd7ef5284f4a6a1a8d81dc12443929dac",
        "b3e5fa9474be385b794cfeb0514a344040b001063302b156f2e081dbe5e08c94",
    ),
)
_V19_MATERIALIZATION_SOURCE_MANIFEST: Final[tuple[tuple[str, int, str, str], ...]] = (
    (
        "contracts/full_strict_v19_reviewed_preimage_v1.json",
        6_513_111,
        _V19_CANDIDATE_SHA256,
        "unfrozen-candidate",
    ),
    (
        "scripts/_task7_v19_census_scaffold.py",
        8_076,
        "a7460f38928b52ed5f31b2838e28c0fbdab5d8134d38b67845da0a695e808b8f",
        "postgres-census-scaffold",
    ),
    (
        "scripts/materialize_full_strict_v19_preimage.py",
        53_969,
        "a94fe22a271498ac2276f4435271fb7db6d4160f2cabfd886cb5a7d6558620ae",
        "candidate-materializer",
    ),
    (
        "scripts/generate_task7_v19_contract_fixture.py",
        17_928,
        "5f1217e5327ac04c11d1fe30be973a36ec76235e146c9bf869483f08a7fa2206",
        "candidate-admission-generator",
    ),
    (
        "scripts/task7_v17_receipt_guard_sql.py",
        9_380,
        "93ed39c6d8ad018ee8629ea3989381ad39b7a4193f58354c161e7b400a27db1c",
        "immutable-receipt-guard-sql-constructor",
    ),
    (
        "scripts/task7_v17_source_projection_sql.py",
        52_970,
        "3d87ec27b57956ec3e3f1fad47681479616462b963f68303cfd3931b92161f11",
        "immutable-source-projection-sql-constructor",
    ),
)

_V19_ARTIFACT_SCHEMA: Final = "full-strict-v19-reviewed-preimage-v1"
_V19_CONTRACT_VERSION: Final = "full-strict-v19"
_ERASURE_PROOF_SCHEMA: Final = "task7-v19-erasure-authority-app-role-census-v1"
_ERASURE_PROOF_DOMAIN: Final = _ERASURE_PROOF_SCHEMA
_RETAINED_PROOF_SCHEMA: Final = "task7-v19-downgrade-retained-definition-app-role-proof-v1"
_RETAINED_PROOF_DOMAIN: Final = _RETAINED_PROOF_SCHEMA
_RETAINED_CATALOG_DOMAIN: Final = "task7-v19-retained-catalog-projection-v1"
_RETAINED_PROOF_ERROR: Final = "TASK7_V19_RETAINED_RESTORE_EXECUTION_INVALID"
_COMPLETE_EXECUTION_DOMAIN: Final = "task7-v19-complete-execution-v1"
_CENSUS_ENVELOPE_SCHEMA: Final = "task7-v19-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v19-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN: Final = "task7-v19-postgres-census-signable-v1"
_CENSUS_PAYLOAD_KEYS: Final = (
    "schema_version",
    "result",
    "artifact_schema",
    "pipeline_contract_version",
    "candidate_artifact_sha256",
    "prospective_frozen_artifact_sha256",
    "database_execution",
    "census_result",
)
_RUNNER_SIGNATURE: Final = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ATTEMPT_NAMESPACE: Final = "4520d8ed-e929-5486-821c-82f8901ae7fa"
_EVENT_NAMESPACE: Final = "dd76cee1-28d5-55f8-b21a-eb64bc2e4be4"
_ERASURE_OWNER_ROLE: Final = "idis_erasure_function_owner"
_ERASURE_EXECUTOR_ROLE: Final = "idis_erasure_executor"
_GUARD_OWNER_ROLE: Final = "idis_task7_guard_function_owner"
_FIXED_TENANT_ID: Final = "11111111-1111-4111-8111-111111111111"
_FIXED_DEAL_ID: Final = "22222222-2222-4222-8222-222222222222"
_FIXED_REQUEST_ID: Final = "33333333-3333-4333-8333-333333333333"
_FIXED_ARTIFACT_ID: Final = "44444444-4444-4444-8444-444444444444"
_FIXED_ATTEMPT_ID: Final = "fbb3b87f-d9d0-56b4-a983-084fb804d4ae"
_FIXED_EVENT_IDS: Final = (
    ("task7.erasure.attempted", "ATTEMPTED", "593d952b-1aff-5e4e-b673-fe02a28e8f2c"),
    ("task7.erasure.failed", "FAILED", "1407c4f8-0229-5b82-97ff-5f44eeb29e97"),
    ("task7.erasure.succeeded", "SUCCEEDED", "70e0d93e-ecb4-5099-944d-37c786d57854"),
)
_FIXED_CANONICAL_FAILED_EVENT_SHA256: Final = (
    "f51eb2892ee7a6a30dca061d18abdf530d28216fc3848d80dfe275a6bd96a766"
)
_FIXED_ARTIFACT_URI: Final = f"idis://task7-v19-census/{_FIXED_ARTIFACT_ID}"
_FIXED_ACTOR_ID: Final = "task7-v19-census-admin"
_FIXED_OCCURRED_AT: Final = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
_MALFORMED_RETRY_SCOPE: Final = (
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
    "88888888-8888-4888-8888-888888888888",
)
_LEGAL_RETRY_SCOPE: Final = (
    "99999999-9999-4999-8999-999999999999",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
)
_APPLICATION_ROLE_PATTERN: Final = re.compile(r"^idis_t2b_app_[0-9a-f]{16}$")
_ERASURE_CASE_PLAN: Final = (
    (
        "missing_attempt",
        _ERASURE_EXECUTOR_ROLE,
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
        _ERASURE_EXECUTOR_ROLE,
        "00000",
        "EXPLICIT_ROLLBACK",
    ),
    (
        "legal_commit",
        _ERASURE_EXECUTOR_ROLE,
        "00000",
        "COMMIT",
    ),
    (
        "malformed_failed_retry",
        _ERASURE_EXECUTOR_ROLE,
        "23514",
        "ROLLBACK_ZERO_MUTATION",
    ),
    (
        "legal_failed_retry",
        _ERASURE_EXECUTOR_ROLE,
        "00000",
        "COMMIT",
    ),
)
_EVENT_OUTCOMES: Final = (
    ("task7.erasure.attempted", "ATTEMPTED"),
    ("task7.erasure.failed", "FAILED"),
    ("task7.erasure.succeeded", "SUCCEEDED"),
)
_READ_CLOSURE: Final = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
    "audit_events",
)
_UPDATE_COLUMNS: Final = (
    "request_id",
    "status",
    "executed_by",
    "executed_at",
    "counts",
)
_ERASURE_PROOF_ERROR: Final = "TASK7_V19_ERASURE_AUTHORITY_EXECUTION_INVALID"
_CENSUS_RECEIPT_ERROR: Final = "TASK7_V19_CENSUS_RECEIPT_INVALID"
_HEX_DIGITS: Final = frozenset("0123456789abcdef")

_ERASURE_PROOF_KEYS: Final = (
    "schema_version",
    "base_schema_revision",
    "artifact_sha256",
    "runner_function_signature",
    "current_body_sha256",
    "current_acl_sha256",
    "current_relation_count",
    "declared_read_relation_tuple",
    "event_outcome_tuple",
    "attempt_namespace",
    "event_namespace",
    "owner_update_column_tuple",
    "setup_actor_mode",
    "application_role_name",
    "behavior_actor_tuple",
    "admin_behavior_case_count",
    "application_guard_case_count",
    "application_direct_runner_denial",
    "missing_attempt_rejection",
    "rollback_atomicity",
    "legal_execution",
    "malformed_prior_rejection",
    "failed_retry_execution",
    "fixed_vector_execution",
    "acl_execution",
    "catalog_projection_sha256",
    "case_result_sha256",
    "execution_sha256",
)
_RETAINED_ACTOR_KEYS: Final = (
    "session_user",
    "current_user",
)
_RETAINED_PROOF_KEYS: Final = (
    "schema_version",
    "base_schema_revision",
    "transaction_model",
    "setup_actor_mode",
    "behavior_actor_mode",
    "admin_behavior_case_count",
    "application_catalog_observation_count",
    "function_restore_count",
    "trigger_restore_count",
    "function_restore_tuple",
    "trigger_restore_tuple",
    "committed_before_actor",
    "committed_restored_actor",
    "rollback_before_actor",
    "rollback_after_actor",
    "committed_catalog_before_projection",
    "committed_catalog_restored_projection",
    "rollback_catalog_before_projection",
    "rollback_catalog_after_projection",
    "postrestore_assertion_sha256",
    "committed_catalog_before_sha256",
    "committed_catalog_restored_sha256",
    "rollback_catalog_before_sha256",
    "rollback_catalog_after_sha256",
    "committed_restored_catalog_equals_before",
    "rollback_catalog_equals_before",
    "restore_committed",
    "transaction_rolled_back",
    "execution_sha256",
)


def _load_adapted_v18_runner() -> ModuleType:
    """Load the exact v18 engine under a metadata-only v19 adaptation."""

    try:
        raw = _V18_RUNNER_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeError("TASK7_V19_V18_CENSUS_RUNNER_DRIFT") from exc
    if not secrets.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        _V18_RUNNER_SHA256,
    ):
        raise RuntimeError("TASK7_V19_V18_CENSUS_RUNNER_DRIFT")
    try:
        source = raw.decode("utf-8").replace("V18", "V19").replace("v18", "v19")
    except UnicodeDecodeError as exc:
        raise RuntimeError("TASK7_V19_V18_CENSUS_RUNNER_INVALID") from exc
    name = "_task7_v19_hash_pinned_v18_census_engine"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V18_RUNNER_PATH),
    )
    if spec is None:
        raise RuntimeError("TASK7_V19_V18_CENSUS_RUNNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(Path(__file__).resolve())
    sys.modules[name] = module
    exec(compile(source, str(_V18_RUNNER_PATH), "exec"), module.__dict__)  # noqa: S102
    adapted = cast(Any, module)
    adapted._V19_MATERIALIZATION_SOURCE_MANIFEST = _V19_MATERIALIZATION_SOURCE_MANIFEST
    adapted._REPO_ROOT = _REPO_ROOT
    adapted._V19_SCAFFOLD_PATH = _V19_SCAFFOLD_PATH
    nested = cast(Any, adapted._BASE)
    nested._V19_MATERIALIZATION_SOURCE_MANIFEST = _V19_MATERIALIZATION_SOURCE_MANIFEST
    nested._REPO_ROOT = _REPO_ROOT
    nested._V19_SCAFFOLD_PATH = _V19_SCAFFOLD_PATH
    return module


_BASE = _load_adapted_v18_runner()
_V19_ADAPT_V18_HARNESS_SOURCE = cast(Any, _BASE._BASE)._adapt_v16_runner_source


def _adapt_v19_harness_source(raw: bytes, candidate_raw: bytes) -> str:
    """Retarget the nested held-source coordinates to their exact v19 bytes."""

    source = cast(str, _V19_ADAPT_V18_HARNESS_SOURCE(raw, candidate_raw))
    for prior_sha256, v19_sha256 in _V19_NESTED_ADAPTER_DIGEST_REPLACEMENTS:
        if source.count(prior_sha256) != 2 or v19_sha256 in source:
            raise RuntimeError("TASK7_V19_HARNESS_SOURCE_ADAPTATION_INVALID")
        source = source.replace(prior_sha256, v19_sha256)
    return source


cast(Any, _BASE._BASE)._adapt_v16_runner_source = _adapt_v19_harness_source
CensusError = _BASE.CensusError
BASE_SCHEMA_REVISION: Final = cast(str, _BASE.BASE_SCHEMA_REVISION)
HARNESS_IDENTITY: Final = cast(str, _BASE.HARNESS_IDENTITY)
INSTALLATION_MODE: Final = cast(str, _BASE.INSTALLATION_MODE)
RESULT_SCHEMA: Final = "task7-v19-current-guard-app-role-census-result-v1"
_PRODUCTION_CONSTRUCTOR_SPECS: Final = cast(
    tuple[tuple[str, ...], ...],
    _BASE._PRODUCTION_CONSTRUCTOR_SPECS,
)
_BASE_DATABASE_EXECUTION_FIELDS: Final = cast(
    tuple[str, ...],
    _BASE._DATABASE_EXECUTION_FIELDS,
)
_DATABASE_EXECUTION_FIELDS: Final = (
    *_BASE_DATABASE_EXECUTION_FIELDS,
    "downgrade_retained_definition_execution_proof_sha256",
    "erasure_authority_execution_proof_sha256",
)
_canonical_bytes = _BASE._canonical_bytes
_decode_json = _BASE._decode_json
_digest = _BASE._digest
artifact_targets = _BASE.artifact_targets
artifact_trigger_contract = _BASE.artifact_trigger_contract
execution_expectations = _BASE.execution_expectations
receipt_equality_execution_plan = _BASE.receipt_equality_execution_plan
receipt_fixture_coordinates = _BASE.receipt_fixture_coordinates
validate_receipt_equality_execution_proof = _BASE.validate_receipt_equality_execution_proof
validate_source_admission_execution_proof = _BASE.validate_source_admission_execution_proof
_validate_v19_candidate = _BASE._validate_v19_candidate
_load_execution_engine = _BASE._BASE._load_execution_engine


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and frozenset(value) <= _HEX_DIGITS


def _validate_retained_actor(value: object) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or tuple(value) != _RETAINED_ACTOR_KEYS
        or not isinstance(value["session_user"], str)
        or not isinstance(value["current_user"], str)
        or value["session_user"] != value["current_user"]
        or _APPLICATION_ROLE_PATTERN.fullmatch(value["session_user"]) is None
    ):
        raise CensusError(_RETAINED_PROOF_ERROR)
    return {
        "session_user": value["session_user"],
        "current_user": value["current_user"],
    }


def _retained_catalog_sha256(value: Mapping[str, Any]) -> str:
    return _digest([_RETAINED_CATALOG_DOMAIN, value])


def validate_downgrade_retained_definition_execution_proof(
    proof: object,
    *,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate app-role catalog evidence around admin-only restore DDL.

    PostgreSQL cannot expose an uncommitted DDL catalog state to a separate
    application-role session.  The proof therefore uses two disposable
    databases: one commits the exact retained restore bytes so an application
    session can observe the restored state, and one rolls the same bytes back
    so another application session can observe zero mutation.
    """

    try:
        if not isinstance(proof, Mapping) or tuple(proof) != _RETAINED_PROOF_KEYS:
            raise CensusError(_RETAINED_PROOF_ERROR)
        committed_before_actor = _validate_retained_actor(proof["committed_before_actor"])
        committed_restored_actor = _validate_retained_actor(proof["committed_restored_actor"])
        rollback_before_actor = _validate_retained_actor(proof["rollback_before_actor"])
        rollback_after_actor = _validate_retained_actor(proof["rollback_after_actor"])
        if (
            committed_before_actor != committed_restored_actor
            or rollback_before_actor != rollback_after_actor
        ):
            raise CensusError(_RETAINED_PROOF_ERROR)
        committed_before = _BASE._validate_catalog_projection(
            proof["committed_catalog_before_projection"],
            artifact=artifact,
        )
        committed_restored = _BASE._validate_catalog_projection(
            proof["committed_catalog_restored_projection"],
            artifact=artifact,
        )
        rollback_before = _BASE._validate_catalog_projection(
            proof["rollback_catalog_before_projection"],
            artifact=artifact,
        )
        rollback_after = _BASE._validate_catalog_projection(
            proof["rollback_catalog_after_projection"],
            artifact=artifact,
        )
        committed_before_sha256 = _retained_catalog_sha256(committed_before)
        committed_restored_sha256 = _retained_catalog_sha256(committed_restored)
        rollback_before_sha256 = _retained_catalog_sha256(rollback_before)
        rollback_after_sha256 = _retained_catalog_sha256(rollback_after)
        downgrade = _BASE._downgrade_authority(artifact)
        supplied_execution = proof["execution_sha256"]
        derived_execution = _digest(
            [
                _RETAINED_PROOF_DOMAIN,
                {key: value for key, value in proof.items() if key != "execution_sha256"},
            ]
        )
        if (
            proof["schema_version"] != _RETAINED_PROOF_SCHEMA
            or proof["base_schema_revision"] != BASE_SCHEMA_REVISION
            or proof["transaction_model"] != "TWO_DISPOSABLE_DATABASES_COMMIT_AND_ROLLBACK"
            or proof["setup_actor_mode"] != "ADMIN_DDL_ONLY"
            or proof["behavior_actor_mode"] != "APPLICATION_ROLE_ONLY"
            or proof["admin_behavior_case_count"] != 0
            or proof["application_catalog_observation_count"] != 4
            or proof["function_restore_count"] != 8
            or proof["trigger_restore_count"] != 9
            or proof["function_restore_tuple"] != _BASE._expected_function_restore_rows(artifact)
            or proof["trigger_restore_tuple"] != _BASE._expected_trigger_restore_rows(artifact)
            or proof["postrestore_assertion_sha256"]
            != _BASE._sha256_utf8_hex(
                downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
            )
            or proof["committed_catalog_before_sha256"] != committed_before_sha256
            or proof["committed_catalog_restored_sha256"] != committed_restored_sha256
            or proof["rollback_catalog_before_sha256"] != rollback_before_sha256
            or proof["rollback_catalog_after_sha256"] != rollback_after_sha256
            or committed_restored != committed_before
            or rollback_after != rollback_before
            or proof["committed_restored_catalog_equals_before"] is not True
            or proof["rollback_catalog_equals_before"] is not True
            or proof["restore_committed"] is not True
            or proof["transaction_rolled_back"] is not True
            or not _is_sha256(supplied_execution)
            or supplied_execution != derived_execution
        ):
            raise CensusError(_RETAINED_PROOF_ERROR)
    except CensusError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError(_RETAINED_PROOF_ERROR) from exc
    return dict(proof)


def _observe_retained_catalog_as_application(
    database: Any,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    with database.app_engine.connect() as connection:
        actor = _validate_retained_actor(
            {
                "session_user": str(connection.execute(text("SELECT session_user")).scalar_one()),
                "current_user": str(connection.execute(text("SELECT current_user")).scalar_one()),
            }
        )
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != BASE_SCHEMA_REVISION:
            raise CensusError(_RETAINED_PROOF_ERROR)
        projection = _BASE._catalog_projection(connection, artifact)
    return actor, projection


def _apply_retained_definition_restore(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    downgrade = _BASE._downgrade_authority(artifact)
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if revision != BASE_SCHEMA_REVISION:
        raise CensusError(_RETAINED_PROOF_ERROR)
    for statement_hex in downgrade["retained_trigger_drop_sql_utf8_hex_tuple"]:
        _BASE._execute_literal(connection, statement_hex)
    for statement_hex in downgrade["retained_function_restore_sql_utf8_hex_tuple"]:
        _BASE._execute_literal(connection, statement_hex)
    for statement_hex in downgrade["retained_trigger_restore_sql_utf8_hex_tuple"]:
        _BASE._execute_literal(connection, statement_hex)
    _BASE._execute_literal(
        connection,
        downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"],
    )


def execute_downgrade_retained_definition_execution_proof(
    *,
    artifact_path: Path,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove retained restore and rollback using app-role observations only."""

    execution_engine = _load_execution_engine(Path(artifact_path))
    with execution_engine._cluster_safety_boundary():
        with execution_engine.disposable_postgres() as committed_database:
            execution_engine.migrate(
                committed_database.admin_engine,
                BASE_SCHEMA_REVISION,
            )
            execution_engine.grant_application_access(committed_database)
            committed_before_actor, committed_before = _observe_retained_catalog_as_application(
                committed_database,
                artifact,
            )
            with committed_database.admin_engine.begin() as admin:
                _apply_retained_definition_restore(admin, artifact)
            committed_restored_actor, committed_restored = _observe_retained_catalog_as_application(
                committed_database,
                artifact,
            )

        with execution_engine.disposable_postgres() as rollback_database:
            execution_engine.migrate(
                rollback_database.admin_engine,
                BASE_SCHEMA_REVISION,
            )
            execution_engine.grant_application_access(rollback_database)
            rollback_before_actor, rollback_before = _observe_retained_catalog_as_application(
                rollback_database,
                artifact,
            )
            admin = rollback_database.admin_engine.connect()
            transaction = admin.begin()
            try:
                _apply_retained_definition_restore(admin, artifact)
            except Exception:
                if transaction.is_active:
                    transaction.rollback()
                admin.close()
                raise
            transaction.rollback()
            admin.close()
            rollback_after_actor, rollback_after = _observe_retained_catalog_as_application(
                rollback_database,
                artifact,
            )

    downgrade = _BASE._downgrade_authority(artifact)
    proof: dict[str, Any] = {
        "schema_version": _RETAINED_PROOF_SCHEMA,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "transaction_model": "TWO_DISPOSABLE_DATABASES_COMMIT_AND_ROLLBACK",
        "setup_actor_mode": "ADMIN_DDL_ONLY",
        "behavior_actor_mode": "APPLICATION_ROLE_ONLY",
        "admin_behavior_case_count": 0,
        "application_catalog_observation_count": 4,
        "function_restore_count": 8,
        "trigger_restore_count": 9,
        "function_restore_tuple": _BASE._expected_function_restore_rows(artifact),
        "trigger_restore_tuple": _BASE._expected_trigger_restore_rows(artifact),
        "committed_before_actor": committed_before_actor,
        "committed_restored_actor": committed_restored_actor,
        "rollback_before_actor": rollback_before_actor,
        "rollback_after_actor": rollback_after_actor,
        "committed_catalog_before_projection": committed_before,
        "committed_catalog_restored_projection": committed_restored,
        "rollback_catalog_before_projection": rollback_before,
        "rollback_catalog_after_projection": rollback_after,
        "postrestore_assertion_sha256": _BASE._sha256_utf8_hex(
            downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
        ),
        "committed_catalog_before_sha256": _retained_catalog_sha256(committed_before),
        "committed_catalog_restored_sha256": _retained_catalog_sha256(committed_restored),
        "rollback_catalog_before_sha256": _retained_catalog_sha256(rollback_before),
        "rollback_catalog_after_sha256": _retained_catalog_sha256(rollback_after),
        "committed_restored_catalog_equals_before": (committed_restored == committed_before),
        "rollback_catalog_equals_before": rollback_after == rollback_before,
        "restore_committed": True,
        "transaction_rolled_back": True,
    }
    proof["execution_sha256"] = _digest([_RETAINED_PROOF_DOMAIN, proof])
    return validate_downgrade_retained_definition_execution_proof(
        proof,
        artifact=artifact,
    )


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CensusError(_ERASURE_PROOF_ERROR)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid5_from_preimage(namespace: str, preimage: Sequence[object]) -> str:
    return str(uuid5(UUID(namespace), _canonical_bytes(list(preimage)).decode("utf-8")))


def _object_target_set_sha256(
    *,
    tenant_id: str,
    deal_id: str,
    ordered_uris: Sequence[str],
) -> str:
    if isinstance(ordered_uris, (str, bytes, bytearray)) or any(
        not isinstance(uri, str) or not uri or not uri.startswith("idis://") for uri in ordered_uris
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    uri_hashes = [hashlib.sha256(uri.encode("utf-8")).hexdigest() for uri in ordered_uris]
    return hashlib.sha256(
        _canonical_bytes(
            [
                "task7-erasure-object-target-set-v1",
                tenant_id,
                deal_id,
                len(uri_hashes),
                uri_hashes,
            ]
        )
    ).hexdigest()


def _build_lineage_event(
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
    actor_id: str,
    occurred_at: datetime,
    ordered_uris: Sequence[str],
    attempt_ordinal: int,
    prior_status: str | None,
    prior_attempt_event_id: str | None,
    prior_attempt_event_hash: str | None,
    event_type: str,
    outcome: str,
) -> dict[str, Any]:
    attempt_id = _uuid5_from_preimage(
        _ATTEMPT_NAMESPACE,
        (
            "task7-erasure-attempt-v1",
            tenant_id,
            deal_id,
            request_id,
            attempt_ordinal,
            prior_status,
            prior_attempt_event_id,
            prior_attempt_event_hash,
        ),
    )
    event_id = _uuid5_from_preimage(
        _EVENT_NAMESPACE,
        (
            "task7-erasure-audit-event-v1",
            attempt_id,
            event_type,
            outcome,
        ),
    )
    target_sha256 = _object_target_set_sha256(
        tenant_id=tenant_id,
        deal_id=deal_id,
        ordered_uris=ordered_uris,
    )
    return {
        "event_id": event_id,
        "occurred_at": _canonical_timestamp(occurred_at),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": actor_id,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": request_id,
            "method": "POST",
            "path": f"/v1/erasure-requests/{request_id}/execute",
            "status_code": 200 if outcome == "ATTEMPTED" else 500,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": request_id,
            "deal_id": deal_id,
        },
        "event_type": event_type,
        "severity": "CRITICAL",
        "summary": f"Task-7 erasure {outcome.lower()}",
        "payload": {
            "safe": {
                "request_id": request_id,
                "attempt_id": attempt_id,
                "attempt_ordinal": attempt_ordinal,
                "prior_status": prior_status,
                "prior_attempt_event_id": prior_attempt_event_id,
                "prior_attempt_event_hash": prior_attempt_event_hash,
                "outcome": outcome,
            },
            "hashes": [f"object_target_set_sha256:{target_sha256}"],
            "refs": [f"deal_id:{deal_id}"],
        },
    }


def _build_attempted_event(
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
    actor_id: str,
    occurred_at: datetime,
    ordered_uris: Sequence[str],
) -> dict[str, Any]:
    return _build_lineage_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
        actor_id=actor_id,
        occurred_at=occurred_at,
        ordered_uris=ordered_uris,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
        event_type="task7.erasure.attempted",
        outcome="ATTEMPTED",
    )


def _fixed_vector_execution_observation() -> dict[str, Any]:
    attempt_id = _uuid5_from_preimage(
        _ATTEMPT_NAMESPACE,
        (
            "task7-erasure-attempt-v1",
            _FIXED_TENANT_ID,
            _FIXED_DEAL_ID,
            _FIXED_REQUEST_ID,
            1,
            None,
            None,
            None,
        ),
    )
    event_ids = tuple(
        (
            event_type,
            outcome,
            _uuid5_from_preimage(
                _EVENT_NAMESPACE,
                (
                    "task7-erasure-audit-event-v1",
                    attempt_id,
                    event_type,
                    outcome,
                ),
            ),
        )
        for event_type, outcome, _expected in _FIXED_EVENT_IDS
    )
    failed_event = {
        "event_id": "1407c4f8-0229-5b82-97ff-5f44eeb29e97",
        "occurred_at": "2026-07-17T16:00:00.000000Z",
        "tenant_id": _FIXED_TENANT_ID,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": "admin-1",
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": _FIXED_REQUEST_ID,
            "method": "POST",
            "path": f"/v1/erasure-requests/{_FIXED_REQUEST_ID}/execute",
            "status_code": 500,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": _FIXED_REQUEST_ID,
            "deal_id": _FIXED_DEAL_ID,
        },
        "event_type": "task7.erasure.failed",
        "severity": "CRITICAL",
        "summary": "Task-7 erasure failed",
        "payload": {
            "safe": {
                "request_id": _FIXED_REQUEST_ID,
                "attempt_id": _FIXED_ATTEMPT_ID,
                "attempt_ordinal": 1,
                "prior_status": None,
                "prior_attempt_event_id": None,
                "prior_attempt_event_hash": None,
                "outcome": "FAILED",
            },
            "hashes": [f"object_target_set_sha256:{'b' * 64}"],
            "refs": [f"deal_id:{_FIXED_DEAL_ID}"],
        },
    }
    return {
        "attempt_vector_count": 1,
        "event_vector_count": 3,
        "canonical_failed_event_vector_count": 1,
        "attempt_vector_accepted": attempt_id == _FIXED_ATTEMPT_ID,
        "event_vectors_accepted": event_ids == _FIXED_EVENT_IDS,
        "canonical_failed_event_accepted": (
            hashlib.sha256(_canonical_bytes(failed_event)).hexdigest()
            == _FIXED_CANONICAL_FAILED_EVENT_SHA256
        ),
    }


def _executor_url_for_disposable(
    *,
    configured_url: str,
    admin_url: URL,
    database_name: str,
) -> URL:
    try:
        configured = make_url(configured_url)
    except Exception as exc:
        raise CensusError("TASK7_V19_ERASURE_EXECUTOR_DATABASE_INVALID") from exc
    if (
        configured.username != _ERASURE_EXECUTOR_ROLE
        or not configured.password
        or configured.host != admin_url.host
        or configured.port != admin_url.port
        or not database_name
        or re.fullmatch(r"[a-z][a-z0-9_]+", database_name) is None
    ):
        raise CensusError("TASK7_V19_ERASURE_EXECUTOR_DATABASE_INVALID")
    return configured.set(database=database_name)


def _verify_materialization_source_manifest(
    *,
    candidate_raw: bytes | None,
) -> tuple[tuple[str, int, str, str], ...]:
    """Verify live census sources or receipt-bound candidate bytes.

    Census execution passes ``None`` and therefore requires every source at
    its live path.  Receipt revalidation supplies the signed candidate bytes:
    that one row is verified from the receipt while every executable helper
    remains live and hash-pinned.  Publication recovery never rewrites or
    temporarily substitutes the live artifact path.
    """

    candidate_relative_path = _V19_MATERIALIZATION_SOURCE_MANIFEST[0][0]
    for relative_path, byte_length, sha256, _role in _V19_MATERIALIZATION_SOURCE_MANIFEST:
        try:
            raw = (
                candidate_raw
                if candidate_raw is not None and relative_path == candidate_relative_path
                else (_REPO_ROOT / relative_path).read_bytes()
            )
        except OSError as exc:
            raise CensusError("TASK7_V19_MATERIALIZATION_SOURCE_DRIFT") from exc
        if len(raw) != byte_length or not secrets.compare_digest(
            hashlib.sha256(raw).hexdigest(),
            sha256,
        ):
            raise CensusError("TASK7_V19_MATERIALIZATION_SOURCE_DRIFT")
    return _V19_MATERIALIZATION_SOURCE_MANIFEST


def materialization_source_manifest() -> tuple[tuple[str, int, str, str], ...]:
    """Require the complete live source closure before census execution."""

    return _verify_materialization_source_manifest(candidate_raw=None)


def materialization_source_manifest_evidence() -> list[list[Any]]:
    return [list(row) for row in materialization_source_manifest()]


def materialization_source_manifest_sha256() -> str:
    return _digest(
        [
            "task7-v19-materialization-source-manifest-v1",
            materialization_source_manifest_evidence(),
        ]
    )


def _receipt_materialization_source_evidence(
    artifact: Mapping[str, Any],
) -> list[list[Any]]:
    raw = (
        json.dumps(
            dict(artifact),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return [list(row) for row in _verify_materialization_source_manifest(candidate_raw=raw)]


def _receipt_materialization_source_sha256(
    artifact: Mapping[str, Any],
) -> str:
    return _digest(
        [
            "task7-v19-materialization-source-manifest-v1",
            _receipt_materialization_source_evidence(artifact),
        ]
    )


def verify_receipt_materialization_source_closure(
    artifact_raw: bytes,
) -> dict[str, Any]:
    """Verify receipt-bound candidate bytes plus every live executable source."""

    artifact = _decode_json(artifact_raw)
    _candidate_artifact(artifact)
    evidence = _receipt_materialization_source_evidence(artifact)
    return {
        "materialization_source_manifest_tuple": evidence,
        "materialization_source_manifest_sha256": _digest(
            [
                "task7-v19-materialization-source-manifest-v1",
                evidence,
            ]
        ),
    }


def _candidate_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    _validate_v19_candidate(artifact)
    rendered = (
        json.dumps(
            dict(artifact),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if not secrets.compare_digest(
        hashlib.sha256(rendered).hexdigest(),
        _V19_CANDIDATE_SHA256,
    ):
        raise CensusError("TASK7_V19_CENSUS_ARTIFACT_VERSION_MISMATCH")
    return copy.deepcopy(dict(artifact))


def _erasure_row(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    _candidate_artifact(artifact)
    try:
        rows = artifact["erasure_execution_registry"]["rows"]
    except (KeyError, TypeError) as exc:
        raise CensusError(_ERASURE_PROOF_ERROR) from exc
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes, bytearray))
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    row = cast(Mapping[str, Any], rows[0])
    if (
        row.get("execution_version") != "2"
        or row.get("runner_function_signature") != _RUNNER_SIGNATURE
        or tuple(row.get("current_relation_tuple", ()))
        != tuple(dict.fromkeys(row.get("current_relation_tuple", ())))
        or len(tuple(row.get("current_relation_tuple", ()))) != 48
        or tuple(row.get("declared_read_relation_tuple", ())) != _READ_CLOSURE
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return row


def _decoded_sql_tuple(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    try:
        encoded = row[field]
        if (
            not isinstance(encoded, Sequence)
            or isinstance(encoded, (str, bytes, bytearray))
            or not encoded
        ):
            raise ValueError("invalid SQL tuple")
        decoded = tuple(bytes.fromhex(str(item)).decode("utf-8") for item in encoded)
    except (KeyError, UnicodeError, ValueError) as exc:
        raise CensusError(_ERASURE_PROOF_ERROR) from exc
    if any(statement != " ".join(statement.split()) for statement in decoded):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return decoded


def _acl_projection(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    acl = _decoded_sql_tuple(_erasure_row(artifact), "current_acl_sql_utf8_hex_tuple")
    select_rows: list[tuple[str, ...]] = []
    update_columns: tuple[str, ...] | None = None
    select_pattern = re.compile(
        r"^GRANT SELECT \((?P<columns>[a-z0-9_,]+)\) "
        r"ON TABLE public\.(?P<table>[a-z][a-z0-9_]*) "
        r"TO idis_erasure_function_owner$"
    )
    update_pattern = re.compile(
        r"^GRANT UPDATE \((?P<columns>[a-z0-9_,]+)\) "
        r"ON TABLE public\.erasure_requests TO idis_erasure_function_owner$"
    )
    for statement in acl:
        select_match = select_pattern.fullmatch(statement)
        if select_match:
            select_rows.append(
                (
                    select_match.group("table"),
                    *select_match.group("columns").split(","),
                )
            )
        update_match = update_pattern.fullmatch(statement)
        if update_match:
            if update_columns is not None:
                raise CensusError(_ERASURE_PROOF_ERROR)
            update_columns = tuple(update_match.group("columns").split(","))
    if (
        len(select_rows) != 51
        or len({row[0] for row in select_rows}) != 51
        or sum(len(row) - 1 for row in select_rows) != 114
        or update_columns != _UPDATE_COLUMNS
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return tuple(select_rows), cast(tuple[str, ...], update_columns)


def _current_relation_tuple(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    relations = tuple(_erasure_row(artifact).get("current_relation_tuple", ()))
    if (
        len(relations) != 48
        or len(set(relations)) != 48
        or any(
            not isinstance(relation, str) or re.fullmatch(r"[a-z][a-z0-9_]*", relation) is None
            for relation in relations
        )
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return cast(tuple[str, ...], relations)


def _artifact_acl_expectations(artifact: Mapping[str, Any]) -> dict[str, Any]:
    row = _erasure_row(artifact)
    body = _decoded_sql_tuple(row, "current_body_sql_utf8_hex_tuple")
    acl = _decoded_sql_tuple(row, "current_acl_sql_utf8_hex_tuple")
    select_rows, update_columns = _acl_projection(artifact)
    select_projection = tuple(sorted((item[0], *sorted(item[1:])) for item in select_rows))
    delete_pattern = re.compile(
        r"^GRANT DELETE ON TABLE public\.(?P<table>[a-z][a-z0-9_]*) "
        rf"TO {_ERASURE_OWNER_ROLE}$"
    )
    delete_relations = tuple(
        sorted(
            match.group("table")
            for statement in acl
            if (match := delete_pattern.fullmatch(statement)) is not None
        )
    )
    relation_tuple = _current_relation_tuple(artifact)
    if (
        len(body) != 1
        or len(delete_relations) != 48
        or frozenset(delete_relations) != frozenset(relation_tuple)
        or (f"ALTER FUNCTION {_RUNNER_SIGNATURE} OWNER TO {_ERASURE_OWNER_ROLE}" not in acl)
        or (f"GRANT EXECUTE ON FUNCTION {_RUNNER_SIGNATURE} TO {_ERASURE_EXECUTOR_ROLE}" not in acl)
        or f"REVOKE ALL ON FUNCTION {_RUNNER_SIGNATURE} FROM PUBLIC" not in acl
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    marker = " AS $$"
    if body[0].count(marker) != 1 or not body[0].endswith("$$"):
        raise CensusError(_ERASURE_PROOF_ERROR)
    source = body[0].partition(marker)[2][:-2]
    force_rls_relations = tuple(sorted(row[0] for row in select_projection))
    return {
        "runner_definition_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "select_relation_column_tuple": [list(item) for item in select_projection],
        "select_acl_sha256": _digest(
            [
                "task7-v19-erasure-select-acl-v1",
                [list(item) for item in select_projection],
            ]
        ),
        "update_column_tuple": list(update_columns),
        "update_acl_sha256": _digest(["task7-v19-erasure-update-acl-v1", list(update_columns)]),
        "delete_relation_tuple": list(delete_relations),
        "delete_acl_sha256": _digest(["task7-v19-erasure-delete-acl-v1", list(delete_relations)]),
        "force_rls_relation_tuple": list(force_rls_relations),
    }


def _expected_deletion_counts(artifact: Mapping[str, Any]) -> dict[str, int]:
    counts = dict.fromkeys(_current_relation_tuple(artifact), 0)
    counts["document_artifacts"] = 1
    counts["deals"] = 1
    counts["object_rows_reconciled"] = 1
    return counts


def _retry_lineage_fixture(
    *,
    scope: tuple[str, str, str, str],
    malformed_prior: bool,
) -> dict[str, Any]:
    tenant_id, deal_id, request_id, artifact_id = scope
    uri = f"idis://task7-v19-retry/{artifact_id}"
    first_attempted = _build_lineage_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
        actor_id=_FIXED_ACTOR_ID,
        occurred_at=_FIXED_OCCURRED_AT,
        ordered_uris=(uri,),
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
        event_type="task7.erasure.attempted",
        outcome="ATTEMPTED",
    )
    first_failed = _build_lineage_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
        actor_id=_FIXED_ACTOR_ID,
        occurred_at=_FIXED_OCCURRED_AT + timedelta(seconds=1),
        ordered_uris=(uri,),
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
        event_type="task7.erasure.failed",
        outcome="FAILED",
    )
    failed_sha256 = hashlib.sha256(_canonical_bytes(first_failed)).hexdigest()
    prior_event_id = (
        "dddddddd-dddd-4ddd-8ddd-dddddddddddd" if malformed_prior else str(first_failed["event_id"])
    )
    second_attempted = _build_lineage_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=request_id,
        actor_id=_FIXED_ACTOR_ID,
        occurred_at=_FIXED_OCCURRED_AT + timedelta(seconds=2),
        ordered_uris=(uri,),
        attempt_ordinal=2,
        prior_status="FAILED",
        prior_attempt_event_id=prior_event_id,
        prior_attempt_event_hash=failed_sha256,
        event_type="task7.erasure.attempted",
        outcome="ATTEMPTED",
    )
    events = (first_attempted, first_failed, second_attempted)
    projection = {
        "request": [
            "FAILED",
            _FIXED_ACTOR_ID,
            _canonical_timestamp(_FIXED_OCCURRED_AT + timedelta(seconds=1)),
            {"object_rows_reconciled": 1},
        ],
        "relation_row_count": 2,
        "audit_event_tuple": sorted(
            [[str(event["event_id"]), str(event["event_type"])] for event in events]
        ),
    }
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "request_id": request_id,
        "artifact_id": artifact_id,
        "uri": uri,
        "events": events,
        "prior_failed_event_id": str(first_failed["event_id"]),
        "prior_failed_event_sha256": failed_sha256,
        "retry_attempt_id": str(second_attempted["payload"]["safe"]["attempt_id"]),
        "state_projection": projection,
        "state_sha256": _state_projection_sha256(projection),
    }


def _failed_retry_expectations(artifact: Mapping[str, Any]) -> dict[str, Any]:
    _expected_deletion_counts(artifact)
    malformed = _retry_lineage_fixture(
        scope=_MALFORMED_RETRY_SCOPE,
        malformed_prior=True,
    )
    legal = _retry_lineage_fixture(
        scope=_LEGAL_RETRY_SCOPE,
        malformed_prior=False,
    )
    return {
        "malformed_state_sha256": malformed["state_sha256"],
        "legal_state_sha256": legal["state_sha256"],
        "prior_failed_event_id": legal["prior_failed_event_id"],
        "prior_failed_event_sha256": legal["prior_failed_event_sha256"],
        "retry_attempt_id": legal["retry_attempt_id"],
    }


def _normalize_owner_column_acl_rows(
    *,
    artifact: Mapping[str, Any],
    rows: Sequence[Sequence[str]],
) -> dict[str, Any]:
    expected = _artifact_acl_expectations(artifact)
    expected_rows = {
        (relation, column, "SELECT")
        for relation, *columns in expected["select_relation_column_tuple"]
        for column in columns
    }
    expected_rows.update(
        ("erasure_requests", column, "UPDATE") for column in expected["update_column_tuple"]
    )
    actual_rows: set[tuple[str, str, str]] = set()
    for row in rows:
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes, bytearray))
            or len(row) != 3
            or any(not isinstance(value, str) or not value for value in row)
        ):
            raise CensusError(_ERASURE_PROOF_ERROR)
        normalized = (str(row[0]), str(row[1]), str(row[2]))
        if normalized in actual_rows:
            raise CensusError(_ERASURE_PROOF_ERROR)
        actual_rows.add(normalized)
    if actual_rows != expected_rows:
        raise CensusError(_ERASURE_PROOF_ERROR)
    return {
        "select_relation_column_tuple": copy.deepcopy(expected["select_relation_column_tuple"]),
        "select_relation_count": len(expected["select_relation_column_tuple"]),
        "select_column_count": sum(
            len(row) - 1 for row in expected["select_relation_column_tuple"]
        ),
        "select_acl_sha256": expected["select_acl_sha256"],
        "update_column_tuple": copy.deepcopy(expected["update_column_tuple"]),
        "update_acl_sha256": expected["update_acl_sha256"],
    }


def _normalize_table_acl_rows(
    *,
    artifact: Mapping[str, Any],
    owner_rows: Sequence[Sequence[str]],
    executor_rows: Sequence[Sequence[str]],
) -> dict[str, Any]:
    expected = _artifact_acl_expectations(artifact)
    normalized_owner = {
        (str(row[0]), str(row[1]))
        for row in owner_rows
        if (
            isinstance(row, Sequence)
            and not isinstance(row, (str, bytes, bytearray))
            and len(row) == 2
            and all(isinstance(value, str) and value for value in row)
        )
    }
    normalized_executor = {
        (str(row[0]), str(row[1]))
        for row in executor_rows
        if (
            isinstance(row, Sequence)
            and not isinstance(row, (str, bytes, bytearray))
            and len(row) == 2
            and all(isinstance(value, str) and value for value in row)
        )
    }
    if (
        len(normalized_owner) != len(owner_rows)
        or len(normalized_executor) != len(executor_rows)
        or normalized_owner
        != {(relation, "DELETE") for relation in expected["delete_relation_tuple"]}
        or normalized_executor
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    delete_relations = copy.deepcopy(expected["delete_relation_tuple"])
    return {
        "delete_relation_tuple": delete_relations,
        "delete_relation_count": len(delete_relations),
        "delete_acl_sha256": expected["delete_acl_sha256"],
        "forbidden_table_privilege_count": 0,
        "executor_table_privilege_count": 0,
    }


def _initial_state_sha256(*, attempted_event_present: bool) -> str:
    return _digest(
        [
            "task7-v19-erasure-state-v1",
            {
                "request": [
                    "REQUESTED",
                    None,
                    None,
                    {"object_rows_reconciled": 1},
                ],
                "relation_row_count": 2,
                "audit_event_tuple": (
                    [
                        [
                            "593d952b-1aff-5e4e-b673-fe02a28e8f2c",
                            "task7.erasure.attempted",
                        ]
                    ]
                    if attempted_event_present
                    else []
                ),
            },
        ]
    )


def _catalog_projection_sha256(
    *,
    application_role_name: str,
    acl_execution: object,
) -> str:
    return _digest(
        [
            "task7-v19-erasure-catalog-projection-v1",
            application_role_name,
            acl_execution,
        ]
    )


def _case_result_sha256(observation: Mapping[str, Any]) -> str:
    fields = (
        "application_direct_runner_denial",
        "missing_attempt_rejection",
        "rollback_atomicity",
        "legal_execution",
        "malformed_prior_rejection",
        "failed_retry_execution",
        "fixed_vector_execution",
    )
    try:
        projection = {field: observation[field] for field in fields}
    except KeyError as exc:
        raise CensusError(_ERASURE_PROOF_ERROR) from exc
    return _digest(
        [
            "task7-v19-erasure-case-results-v1",
            [list(row) for row in _ERASURE_CASE_PLAN],
            projection,
        ]
    )


def erasure_authority_execution_expectations(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive every stable erasure-proof coordinate from candidate bytes."""

    row = _erasure_row(artifact)
    body = _decoded_sql_tuple(row, "current_body_sql_utf8_hex_tuple")
    acl = _decoded_sql_tuple(row, "current_acl_sql_utf8_hex_tuple")
    select_rows, update_columns = _acl_projection(artifact)
    if len(body) != 1 or len(acl) != 208:
        raise CensusError(_ERASURE_PROOF_ERROR)
    audit_contract = row.get("audit_lineage_contract_tuple")
    if (
        not isinstance(audit_contract, Sequence)
        or len(audit_contract) < 2
        or tuple(tuple(item[:2]) for item in audit_contract[1]) != _EVENT_OUTCOMES
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return {
        "runner_function_signature": _RUNNER_SIGNATURE,
        "current_body_sha256": hashlib.sha256(body[0].encode("utf-8")).hexdigest(),
        "current_acl_sha256": hashlib.sha256(
            b"\0".join(statement.encode("utf-8") for statement in acl)
        ).hexdigest(),
        "current_relation_count": 48,
        "declared_read_relation_tuple": list(_READ_CLOSURE),
        "event_outcome_tuple": [list(item) for item in _EVENT_OUTCOMES],
        "attempt_namespace": _ATTEMPT_NAMESPACE,
        "event_namespace": _EVENT_NAMESPACE,
        "owner_update_column_tuple": list(update_columns),
        "owner_select_relation_count": len(select_rows),
        "owner_select_column_count": sum(len(row) - 1 for row in select_rows),
    }


def _closed_mapping(
    value: object,
    keys: tuple[str, ...],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or tuple(value) != keys:
        raise CensusError(_ERASURE_PROOF_ERROR)
    return cast(Mapping[str, Any], value)


def _same_sha(value: Mapping[str, Any], left: str, right: str) -> bool:
    return _is_sha256(value.get(left)) and value.get(left) == value.get(right)


def _validate_erasure_observation(
    proof: Mapping[str, Any],
    *,
    expectations: Mapping[str, Any],
    acl_expectations: Mapping[str, Any],
    expected_counts: Mapping[str, int],
    retry_expectations: Mapping[str, Any],
) -> None:
    direct = _closed_mapping(
        proof["application_direct_runner_denial"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "sqlstate",
            "marker",
            "state_before_sha256",
            "state_after_sha256",
            "zero_mutation",
        ),
    )
    missing = _closed_mapping(
        proof["missing_attempt_rejection"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "sqlstate",
            "marker",
            "state_before_sha256",
            "state_after_sha256",
            "zero_mutation",
        ),
    )
    rollback = _closed_mapping(
        proof["rollback_atomicity"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "inside_status",
            "inside_counts_sha256",
            "inside_relation_rows_remaining",
            "after_status",
            "after_counts_sha256",
            "after_relation_rows_remaining",
            "transaction_rolled_back",
            "zero_mutation",
        ),
    )
    legal = _closed_mapping(
        proof["legal_execution"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "attempted_event_precondition_count",
            "request_status_before",
            "request_status_after",
            "actor_bound_to_attempt",
            "object_rows_reconciled",
            "artifact_row_count",
            "deleted_relation_count",
            "returned_counts_sha256",
            "persisted_counts_sha256",
            "counts_equal",
            "executed_at_equal",
            "committed",
        ),
    )
    malformed_retry = _closed_mapping(
        proof["malformed_prior_rejection"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "sqlstate",
            "marker",
            "state_before_sha256",
            "state_after_sha256",
            "zero_mutation",
        ),
    )
    failed_retry = _closed_mapping(
        proof["failed_retry_execution"],
        (
            "executed_as_role",
            "session_user",
            "current_user",
            "request_status_before",
            "request_status_after",
            "attempt_ordinal",
            "prior_failed_event_id",
            "prior_failed_event_sha256",
            "retry_attempt_id",
            "prior_event_identity_rederived",
            "prior_event_hash_rederived",
            "retry_attempt_identity_rederived",
            "returned_counts_sha256",
            "persisted_counts_sha256",
            "counts_equal",
            "executed_at_equal",
            "committed",
        ),
    )
    vectors = _closed_mapping(
        proof["fixed_vector_execution"],
        (
            "attempt_vector_count",
            "event_vector_count",
            "canonical_failed_event_vector_count",
            "attempt_vector_accepted",
            "event_vectors_accepted",
            "canonical_failed_event_accepted",
        ),
    )
    acl = _closed_mapping(
        proof["acl_execution"],
        (
            "catalog_session_user",
            "catalog_current_user",
            "owner_role",
            "owner_posture_tuple",
            "executor_role",
            "executor_posture_tuple",
            "executor_effective_schema_usage",
            "executor_effective_schema_create",
            "executor_direct_schema_acl_count",
            "executor_other_routine_execute_count",
            "application_role",
            "application_role_posture_tuple",
            "role_membership_count",
            "owner_schema_usage",
            "owner_schema_create",
            "owner_owned_relation_count",
            "runner_definition_sha256",
            "runner_owner",
            "runner_security_definer",
            "runner_proconfig_tuple",
            "execute_grantee_tuple",
            "application_execute",
            "public_execute",
            "executor_execute",
            "owner_helper_execute_count",
            "select_relation_column_tuple",
            "select_relation_count",
            "select_column_count",
            "select_acl_sha256",
            "update_column_tuple",
            "update_acl_sha256",
            "delete_relation_tuple",
            "delete_relation_count",
            "delete_acl_sha256",
            "forbidden_table_privilege_count",
            "executor_table_privilege_count",
            "executor_column_privilege_count",
            "force_rls_relation_tuple",
            "force_rls_relation_count",
        ),
    )
    owner_posture = [
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOBYPASSRLS",
    ]
    executor_posture = [
        "LOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOBYPASSRLS",
    ]
    application_role = proof["application_role_name"]
    expected_counts_sha256 = _digest(expected_counts)
    staged_counts_sha256 = _digest({"object_rows_reconciled": 1})
    missing_state_sha256 = _initial_state_sha256(attempted_event_present=False)
    attempted_state_sha256 = _initial_state_sha256(attempted_event_present=True)
    acl_exact = (
        acl["runner_definition_sha256"] == acl_expectations["runner_definition_sha256"]
        and acl["select_relation_column_tuple"] == acl_expectations["select_relation_column_tuple"]
        and acl["select_acl_sha256"] == acl_expectations["select_acl_sha256"]
        and acl["update_column_tuple"] == acl_expectations["update_column_tuple"]
        and acl["update_acl_sha256"] == acl_expectations["update_acl_sha256"]
        and acl["delete_relation_tuple"] == acl_expectations["delete_relation_tuple"]
        and acl["delete_acl_sha256"] == acl_expectations["delete_acl_sha256"]
        and acl["force_rls_relation_tuple"] == acl_expectations["force_rls_relation_tuple"]
    )
    valid = bool(
        proof["setup_actor_mode"] == "ADMIN_DDL_ONLY"
        and isinstance(application_role, str)
        and _APPLICATION_ROLE_PATTERN.fullmatch(application_role) is not None
        and proof["behavior_actor_tuple"] == [application_role, _ERASURE_EXECUTOR_ROLE]
        and proof["admin_behavior_case_count"] == 0
        and proof["application_guard_case_count"] == 219
        and direct["executed_as_role"] == application_role
        and direct["session_user"] == application_role
        and direct["current_user"] == application_role
        and direct["sqlstate"] == "42501"
        and direct["marker"] == "FUNCTION_EXECUTE_PRIVILEGE_DENIED"
        and direct["zero_mutation"] is True
        and _same_sha(direct, "state_before_sha256", "state_after_sha256")
        and direct["state_before_sha256"] == attempted_state_sha256
        and missing["executed_as_role"] == _ERASURE_EXECUTOR_ROLE
        and missing["session_user"] == _ERASURE_EXECUTOR_ROLE
        and missing["current_user"] == _ERASURE_EXECUTOR_ROLE
        and missing["sqlstate"] == "23514"
        and missing["marker"] == "TASK7_ERASURE_ATTEMPTED_EVENT_INVALID"
        and missing["zero_mutation"] is True
        and _same_sha(missing, "state_before_sha256", "state_after_sha256")
        and missing["state_before_sha256"] == missing_state_sha256
        and rollback["executed_as_role"] == _ERASURE_EXECUTOR_ROLE
        and rollback["session_user"] == _ERASURE_EXECUTOR_ROLE
        and rollback["current_user"] == _ERASURE_EXECUTOR_ROLE
        and rollback["inside_status"] == "EXECUTED"
        and rollback["inside_counts_sha256"] == expected_counts_sha256
        and rollback["inside_relation_rows_remaining"] == 0
        and rollback["after_status"] == "REQUESTED"
        and rollback["after_counts_sha256"] == staged_counts_sha256
        and rollback["after_relation_rows_remaining"] == 2
        and rollback["transaction_rolled_back"] is True
        and rollback["zero_mutation"] is True
        and legal["executed_as_role"] == _ERASURE_EXECUTOR_ROLE
        and legal["session_user"] == _ERASURE_EXECUTOR_ROLE
        and legal["current_user"] == _ERASURE_EXECUTOR_ROLE
        and legal["attempted_event_precondition_count"] == 1
        and legal["request_status_before"] == "REQUESTED"
        and legal["request_status_after"] == "EXECUTED"
        and legal["actor_bound_to_attempt"] is True
        and legal["object_rows_reconciled"] == 1
        and legal["artifact_row_count"] == legal["object_rows_reconciled"]
        and legal["deleted_relation_count"] == 48
        and _same_sha(legal, "returned_counts_sha256", "persisted_counts_sha256")
        and legal["returned_counts_sha256"] == expected_counts_sha256
        and legal["counts_equal"] is True
        and legal["executed_at_equal"] is True
        and legal["committed"] is True
        and malformed_retry["executed_as_role"] == _ERASURE_EXECUTOR_ROLE
        and malformed_retry["session_user"] == _ERASURE_EXECUTOR_ROLE
        and malformed_retry["current_user"] == _ERASURE_EXECUTOR_ROLE
        and malformed_retry["sqlstate"] == "23514"
        and malformed_retry["marker"] == "TASK7_ERASURE_PRIOR_EVENT_INVALID"
        and malformed_retry["state_before_sha256"] == retry_expectations["malformed_state_sha256"]
        and _same_sha(
            malformed_retry,
            "state_before_sha256",
            "state_after_sha256",
        )
        and malformed_retry["zero_mutation"] is True
        and failed_retry["executed_as_role"] == _ERASURE_EXECUTOR_ROLE
        and failed_retry["session_user"] == _ERASURE_EXECUTOR_ROLE
        and failed_retry["current_user"] == _ERASURE_EXECUTOR_ROLE
        and failed_retry["request_status_before"] == "FAILED"
        and failed_retry["request_status_after"] == "EXECUTED"
        and failed_retry["attempt_ordinal"] == 2
        and failed_retry["prior_failed_event_id"] == retry_expectations["prior_failed_event_id"]
        and failed_retry["prior_failed_event_sha256"]
        == retry_expectations["prior_failed_event_sha256"]
        and failed_retry["retry_attempt_id"] == retry_expectations["retry_attempt_id"]
        and failed_retry["prior_event_identity_rederived"] is True
        and failed_retry["prior_event_hash_rederived"] is True
        and failed_retry["retry_attempt_identity_rederived"] is True
        and failed_retry["returned_counts_sha256"] == expected_counts_sha256
        and failed_retry["persisted_counts_sha256"] == expected_counts_sha256
        and failed_retry["counts_equal"] is True
        and failed_retry["executed_at_equal"] is True
        and failed_retry["committed"] is True
        and vectors
        == {
            "attempt_vector_count": 1,
            "event_vector_count": 3,
            "canonical_failed_event_vector_count": 1,
            "attempt_vector_accepted": True,
            "event_vectors_accepted": True,
            "canonical_failed_event_accepted": True,
        }
        and acl["catalog_session_user"] == application_role
        and acl["catalog_current_user"] == application_role
        and acl["owner_role"] == _ERASURE_OWNER_ROLE
        and acl["owner_posture_tuple"] == owner_posture
        and acl["executor_role"] == _ERASURE_EXECUTOR_ROLE
        and acl["executor_posture_tuple"] == executor_posture
        and acl["executor_effective_schema_usage"] is True
        and acl["executor_effective_schema_create"] is False
        and acl["executor_direct_schema_acl_count"] == 0
        and acl["executor_other_routine_execute_count"] == 0
        and acl["application_role"] == application_role
        and acl["application_role_posture_tuple"] == executor_posture
        and acl["role_membership_count"] == 0
        and acl["owner_schema_usage"] is True
        and acl["owner_schema_create"] is False
        and acl["owner_owned_relation_count"] == 0
        and acl["runner_owner"] == _ERASURE_OWNER_ROLE
        and acl["runner_security_definer"] is True
        and acl["runner_proconfig_tuple"] == ["search_path=pg_catalog, pg_temp"]
        and acl["execute_grantee_tuple"] == [_ERASURE_EXECUTOR_ROLE]
        and acl["application_execute"] is False
        and acl["public_execute"] is False
        and acl["executor_execute"] is True
        and acl["owner_helper_execute_count"] == 0
        and acl_exact
        and acl["select_relation_count"] == expectations["owner_select_relation_count"]
        and acl["select_column_count"] == expectations["owner_select_column_count"]
        and acl["update_column_tuple"] == list(_UPDATE_COLUMNS)
        and acl["delete_relation_count"] == 48
        and acl["forbidden_table_privilege_count"] == 0
        and acl["executor_table_privilege_count"] == 0
        and acl["executor_column_privilege_count"] == 0
        and acl["force_rls_relation_count"] == expectations["owner_select_relation_count"]
        and proof["catalog_projection_sha256"]
        == _catalog_projection_sha256(
            application_role_name=application_role,
            acl_execution=acl,
        )
        and proof["case_result_sha256"] == _case_result_sha256(proof)
    )
    if not valid:
        raise CensusError(_ERASURE_PROOF_ERROR)


def build_erasure_authority_execution_proof(
    *,
    artifact: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Close and hash one already-executed disposable-Postgres observation."""

    expectations = erasure_authority_execution_expectations(artifact)
    observation_keys = _ERASURE_PROOF_KEYS[12:-1]
    if not isinstance(observation, Mapping) or tuple(observation) != observation_keys:
        raise CensusError(_ERASURE_PROOF_ERROR)
    proof: dict[str, Any] = {
        "schema_version": _ERASURE_PROOF_SCHEMA,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "artifact_sha256": _V19_CANDIDATE_SHA256,
        **{
            key: copy.deepcopy(expectations[key])
            for key in (
                "runner_function_signature",
                "current_body_sha256",
                "current_acl_sha256",
                "current_relation_count",
                "declared_read_relation_tuple",
                "event_outcome_tuple",
                "attempt_namespace",
                "event_namespace",
                "owner_update_column_tuple",
            )
        },
        **copy.deepcopy(dict(observation)),
    }
    proof["execution_sha256"] = _digest([_ERASURE_PROOF_DOMAIN, proof])
    return validate_erasure_authority_execution_proof(proof, artifact=artifact)


def validate_erasure_authority_execution_proof(
    proof: object,
    *,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed v19 application-role/dedicated-executor proof."""

    try:
        if not isinstance(proof, Mapping) or tuple(proof) != _ERASURE_PROOF_KEYS:
            raise CensusError(_ERASURE_PROOF_ERROR)
        expectations = erasure_authority_execution_expectations(artifact)
        for key in (
            "runner_function_signature",
            "current_body_sha256",
            "current_acl_sha256",
            "current_relation_count",
            "declared_read_relation_tuple",
            "event_outcome_tuple",
            "attempt_namespace",
            "event_namespace",
            "owner_update_column_tuple",
        ):
            if proof[key] != expectations[key]:
                raise CensusError(_ERASURE_PROOF_ERROR)
        supplied = proof["execution_sha256"]
        derived = _digest(
            [
                _ERASURE_PROOF_DOMAIN,
                {key: value for key, value in proof.items() if key != "execution_sha256"},
            ]
        )
        if (
            proof["schema_version"] != _ERASURE_PROOF_SCHEMA
            or proof["base_schema_revision"] != BASE_SCHEMA_REVISION
            or proof["artifact_sha256"] != _V19_CANDIDATE_SHA256
            or not _is_sha256(supplied)
            or supplied != derived
        ):
            raise CensusError(_ERASURE_PROOF_ERROR)
        _validate_erasure_observation(
            proof,
            expectations=expectations,
            acl_expectations=_artifact_acl_expectations(artifact),
            expected_counts=_expected_deletion_counts(artifact),
            retry_expectations=_failed_retry_expectations(artifact),
        )
    except CensusError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError(_ERASURE_PROOF_ERROR) from exc
    return copy.deepcopy(dict(proof))


def _load_v19_scaffold() -> ModuleType:
    try:
        raw = _V19_SCAFFOLD_PATH.read_bytes()
    except OSError as exc:
        raise CensusError("TASK7_V19_CENSUS_SCAFFOLD_DRIFT") from exc
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), _V19_SCAFFOLD_SHA256):
        raise CensusError("TASK7_V19_CENSUS_SCAFFOLD_DRIFT")
    name = "_task7_v19_erasure_execution_scaffold"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=str(_V19_SCAFFOLD_PATH),
    )
    if spec is None:
        raise CensusError("TASK7_V19_CENSUS_SCAFFOLD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(_V19_SCAFFOLD_PATH)
    sys.modules[name] = module
    exec(compile(raw, str(_V19_SCAFFOLD_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


def _required_erasure_database_url() -> str:
    value = os.environ.get("IDIS_ERASURE_DATABASE_URL", "").strip()
    if not value:
        raise CensusError("TASK7_V19_ERASURE_EXECUTOR_DATABASE_MISSING")
    return value


def _caller_identity(connection: Connection) -> tuple[str, str]:
    row = connection.execute(text("SELECT session_user::text,current_user::text")).one()
    return str(row[0]), str(row[1])


def _role_posture_tuple(connection: Connection, role_name: str) -> list[str]:
    row = connection.execute(
        text(
            """
            SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
                   rolinherit,rolbypassrls
            FROM pg_catalog.pg_roles
            WHERE rolname=:role_name
            """
        ),
        {"role_name": role_name},
    ).one()
    values = tuple(bool(value) for value in row)
    return [
        "LOGIN" if values[0] else "NOLOGIN",
        "SUPERUSER" if values[1] else "NOSUPERUSER",
        "CREATEDB" if values[2] else "NOCREATEDB",
        "CREATEROLE" if values[3] else "NOCREATEROLE",
        "INHERIT" if values[4] else "NOINHERIT",
        "BYPASSRLS" if values[5] else "NOBYPASSRLS",
    ]


def _set_tenant_context(connection: Connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT pg_catalog.set_config('idis.tenant_id',:tenant_id,true)"),
        {"tenant_id": tenant_id},
    )


def _seed_erasure_fixture(database: Any) -> None:
    with database.app_engine.begin() as connection:
        if _caller_identity(connection) != (database.role_name, database.role_name):
            raise CensusError(_ERASURE_PROOF_ERROR)
        _set_tenant_context(connection, _FIXED_TENANT_ID)
        connection.execute(
            text(
                """
                INSERT INTO public.tenants (tenant_id,name,created_at)
                VALUES (
                  CAST(:tenant_id AS uuid),
                  'Fictional Task 7 v19 census tenant',
                  CAST(:created_at AS timestamptz)
                )
                """
            ),
            {
                "tenant_id": _FIXED_TENANT_ID,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.deals (deal_id,tenant_id,name,created_at)
                VALUES (
                  CAST(:deal_id AS uuid),
                  CAST(:tenant_id AS uuid),
                  'Fictional Task 7 v19 census deal',
                  CAST(:created_at AS timestamptz)
                )
                """
            ),
            {
                "deal_id": _FIXED_DEAL_ID,
                "tenant_id": _FIXED_TENANT_ID,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.document_artifacts (
                  doc_id,tenant_id,deal_id,doc_type,title,source_system,
                  version_id,ingested_at,sha256,uri,metadata,created_at,
                  updated_at,source_binding_state
                ) VALUES (
                  CAST(:doc_id AS uuid),
                  CAST(:tenant_id AS uuid),
                  CAST(:deal_id AS uuid),
                  'DATA_ROOM_FILE',
                  'Fictional Task 7 v19 census artifact',
                  'task7-v19-census',
                  'v1',
                  CAST(:created_at AS timestamptz),
                  :sha256,
                  :uri,
                  '{}'::jsonb,
                  CAST(:created_at AS timestamptz),
                  CAST(:created_at AS timestamptz),
                  'UNBOUND'
                )
                """
            ),
            {
                "doc_id": _FIXED_ARTIFACT_ID,
                "tenant_id": _FIXED_TENANT_ID,
                "deal_id": _FIXED_DEAL_ID,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
                "sha256": "a" * 64,
                "uri": _FIXED_ARTIFACT_URI,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.erasure_requests (
                  tenant_id,request_id,deal_id,status,requested_by,
                  requested_at,reason_hash,reason_length,counts
                ) VALUES (
                  CAST(:tenant_id AS uuid),
                  CAST(:request_id AS uuid),
                  CAST(:deal_id AS uuid),
                  'REQUESTED',
                  :requested_by,
                  CAST(:requested_at AS timestamptz),
                  :reason_hash,
                  1,
                  pg_catalog.jsonb_build_object('object_rows_reconciled', 1)
                )
                """
            ),
            {
                "tenant_id": _FIXED_TENANT_ID,
                "request_id": _FIXED_REQUEST_ID,
                "deal_id": _FIXED_DEAL_ID,
                "requested_by": _FIXED_ACTOR_ID,
                "requested_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
                "reason_hash": "b" * 64,
            },
        )


def _scoped_state_projection(
    database: Any,
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
) -> dict[str, Any]:
    with database.app_engine.begin() as connection:
        if _caller_identity(connection) != (database.role_name, database.role_name):
            raise CensusError(_ERASURE_PROOF_ERROR)
        _set_tenant_context(connection, tenant_id)
        request = connection.execute(
            text(
                """
                SELECT status,executed_by,executed_at,counts
                FROM public.erasure_requests
                WHERE tenant_id=CAST(:tenant_id AS uuid)
                  AND deal_id=CAST(:deal_id AS uuid)
                  AND request_id=CAST(:request_id AS uuid)
                """
            ),
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "request_id": request_id,
            },
        ).one()
        relation_row_count = int(
            connection.execute(
                text(
                    """
                    SELECT
                      (
                        SELECT count(*) FROM public.deals
                        WHERE tenant_id=CAST(:tenant_id AS uuid)
                          AND deal_id=CAST(:deal_id AS uuid)
                      )
                      +
                      (
                        SELECT count(*) FROM public.document_artifacts
                        WHERE tenant_id=CAST(:tenant_id AS uuid)
                          AND deal_id=CAST(:deal_id AS uuid)
                      )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                },
            ).scalar_one()
        )
        event_rows = connection.execute(
            text(
                """
                SELECT event_id::text,event_type
                FROM public.audit_events
                WHERE tenant_id=CAST(:tenant_id AS uuid)
                  AND event #>> '{payload,safe,request_id}'=:request_id
                ORDER BY event_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
            },
        )
        executed_at = request[2]
        projection = {
            "request": [
                str(request[0]),
                None if request[1] is None else str(request[1]),
                (
                    None
                    if executed_at is None
                    else _canonical_timestamp(cast(datetime, executed_at))
                ),
                dict(cast(Mapping[str, int], request[3])),
            ],
            "relation_row_count": relation_row_count,
            "audit_event_tuple": [[str(row[0]), str(row[1])] for row in event_rows],
        }
    return projection


def _state_projection(database: Any) -> dict[str, Any]:
    return _scoped_state_projection(
        database,
        tenant_id=_FIXED_TENANT_ID,
        deal_id=_FIXED_DEAL_ID,
        request_id=_FIXED_REQUEST_ID,
    )


def _state_projection_sha256(projection: Mapping[str, Any]) -> str:
    return _digest(["task7-v19-erasure-state-v1", projection])


def _insert_lineage_event(
    connection: Connection,
    event: Mapping[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.audit_events (
              event_id,tenant_id,occurred_at,event_type,request_id,
              idempotency_key,event
            ) VALUES (
              CAST(:event_id AS uuid),
              CAST(:tenant_id AS uuid),
              CAST(:occurred_at AS timestamptz),
              :event_type,
              :request_id,
              NULL,
              CAST(:event AS jsonb)
            )
            """
        ),
        {
            "event_id": event["event_id"],
            "tenant_id": event["tenant_id"],
            "occurred_at": event["occurred_at"],
            "event_type": event["event_type"],
            "request_id": event["request"]["request_id"],
            "event": json.dumps(
                event,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    )


def _persist_attempted_event(database: Any) -> dict[str, Any]:
    event = _build_attempted_event(
        tenant_id=_FIXED_TENANT_ID,
        deal_id=_FIXED_DEAL_ID,
        request_id=_FIXED_REQUEST_ID,
        actor_id=_FIXED_ACTOR_ID,
        occurred_at=_FIXED_OCCURRED_AT,
        ordered_uris=(_FIXED_ARTIFACT_URI,),
    )
    with database.app_engine.begin() as connection:
        if _caller_identity(connection) != (database.role_name, database.role_name):
            raise CensusError(_ERASURE_PROOF_ERROR)
        _set_tenant_context(connection, _FIXED_TENANT_ID)
        _insert_lineage_event(connection, event)
    return event


def _seed_retry_fixture(
    database: Any,
    *,
    fixture: Mapping[str, Any],
) -> None:
    tenant_id = str(fixture["tenant_id"])
    deal_id = str(fixture["deal_id"])
    request_id = str(fixture["request_id"])
    artifact_id = str(fixture["artifact_id"])
    with database.app_engine.begin() as connection:
        if _caller_identity(connection) != (database.role_name, database.role_name):
            raise CensusError(_ERASURE_PROOF_ERROR)
        _set_tenant_context(connection, tenant_id)
        connection.execute(
            text(
                """
                INSERT INTO public.tenants (tenant_id,name,created_at)
                VALUES (
                  CAST(:tenant_id AS uuid),
                  'Fictional Task 7 v19 retry census tenant',
                  CAST(:created_at AS timestamptz)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.deals (deal_id,tenant_id,name,created_at)
                VALUES (
                  CAST(:deal_id AS uuid),
                  CAST(:tenant_id AS uuid),
                  'Fictional Task 7 v19 retry census deal',
                  CAST(:created_at AS timestamptz)
                )
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.document_artifacts (
                  doc_id,tenant_id,deal_id,doc_type,title,source_system,
                  version_id,ingested_at,sha256,uri,metadata,created_at,
                  updated_at,source_binding_state
                ) VALUES (
                  CAST(:doc_id AS uuid),
                  CAST(:tenant_id AS uuid),
                  CAST(:deal_id AS uuid),
                  'DATA_ROOM_FILE',
                  'Fictional Task 7 v19 retry census artifact',
                  'task7-v19-census',
                  'v1',
                  CAST(:created_at AS timestamptz),
                  :sha256,
                  :uri,
                  '{}'::jsonb,
                  CAST(:created_at AS timestamptz),
                  CAST(:created_at AS timestamptz),
                  'UNBOUND'
                )
                """
            ),
            {
                "doc_id": artifact_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "created_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
                "sha256": "c" * 64,
                "uri": fixture["uri"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.erasure_requests (
                  tenant_id,request_id,deal_id,status,requested_by,
                  requested_at,reason_hash,reason_length,executed_by,
                  executed_at,counts
                ) VALUES (
                  CAST(:tenant_id AS uuid),
                  CAST(:request_id AS uuid),
                  CAST(:deal_id AS uuid),
                  'FAILED',
                  :requested_by,
                  CAST(:requested_at AS timestamptz),
                  :reason_hash,
                  1,
                  :executed_by,
                  CAST(:executed_at AS timestamptz),
                  pg_catalog.jsonb_build_object('object_rows_reconciled', 1)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "deal_id": deal_id,
                "requested_by": _FIXED_ACTOR_ID,
                "requested_at": _canonical_timestamp(_FIXED_OCCURRED_AT),
                "reason_hash": "d" * 64,
                "executed_by": _FIXED_ACTOR_ID,
                "executed_at": _canonical_timestamp(_FIXED_OCCURRED_AT + timedelta(seconds=1)),
            },
        )
        for event in cast(Sequence[Mapping[str, Any]], fixture["events"]):
            _insert_lineage_event(connection, event)


def _sqlstate(error: DBAPIError) -> str:
    value = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    return "" if value is None else str(value)


def _invoke_runner(
    connection: Connection,
    *,
    request_id: str = _FIXED_REQUEST_ID,
    tenant_id: str = _FIXED_TENANT_ID,
    deal_id: str = _FIXED_DEAL_ID,
) -> tuple[tuple[str, str], Mapping[str, Any]]:
    identity = _caller_identity(connection)
    value = connection.execute(
        text(
            """
            SELECT public.idis_task7_execute_approved_erasure(
              CAST(:request_id AS uuid),
              CAST(:tenant_id AS uuid),
              CAST(:deal_id AS uuid)
            )
            """
        ),
        {
            "request_id": request_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
        },
    ).scalar_one()
    if not isinstance(value, Mapping) or set(value) != {
        "status",
        "executed_by",
        "executed_at",
        "counts",
    }:
        raise CensusError(_ERASURE_PROOF_ERROR)
    return identity, cast(Mapping[str, Any], value)


def _validate_runner_result(
    value: Mapping[str, Any],
    *,
    expected_counts: Mapping[str, int],
) -> dict[str, Any]:
    counts = value.get("counts")
    if (
        value.get("status") != "EXECUTED"
        or value.get("executed_by") != _FIXED_ACTOR_ID
        or not isinstance(value.get("executed_at"), str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
            cast(str, value["executed_at"]),
        )
        is None
        or not isinstance(counts, Mapping)
        or dict(counts) != dict(expected_counts)
        or any(type(item) is not int or item < 0 for item in counts.values())
    ):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return {
        "status": "EXECUTED",
        "executed_by": _FIXED_ACTOR_ID,
        "executed_at": value["executed_at"],
        "counts": dict(cast(Mapping[str, int], counts)),
    }


def _catalog_acl_observation(
    connection: Connection,
    *,
    artifact: Mapping[str, Any],
    application_role: str,
) -> dict[str, Any]:
    identity = _caller_identity(connection)
    if identity != (application_role, application_role):
        raise CensusError(_ERASURE_PROOF_ERROR)
    expected = _artifact_acl_expectations(artifact)
    role_names = (
        _ERASURE_OWNER_ROLE,
        _ERASURE_EXECUTOR_ROLE,
        _GUARD_OWNER_ROLE,
        application_role,
    )
    membership_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_auth_members AS membership
                JOIN pg_catalog.pg_roles AS member
                  ON member.oid=membership.member
                JOIN pg_catalog.pg_roles AS granted
                  ON granted.oid=membership.roleid
                WHERE member.rolname=ANY(CAST(:role_names AS text[]))
                   OR granted.rolname=ANY(CAST(:role_names AS text[]))
                """
            ),
            {"role_names": list(role_names)},
        ).scalar_one()
    )
    schema_usage, schema_create = connection.execute(
        text(
            """
            SELECT
              pg_catalog.has_schema_privilege(:owner,'public','USAGE'),
              pg_catalog.has_schema_privilege(:owner,'public','CREATE')
            """
        ),
        {"owner": _ERASURE_OWNER_ROLE},
    ).one()
    owner_owned_relation_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                JOIN pg_catalog.pg_roles AS owner
                  ON owner.oid=relation.relowner
                WHERE namespace.nspname='public'
                  AND relation.relkind IN ('r','p')
                  AND owner.rolname=:owner
                """
            ),
            {"owner": _ERASURE_OWNER_ROLE},
        ).scalar_one()
    )
    executor_schema_usage, executor_schema_create = connection.execute(
        text(
            """
            SELECT
              pg_catalog.has_schema_privilege(:executor,'public','USAGE'),
              pg_catalog.has_schema_privilege(:executor,'public','CREATE')
            """
        ),
        {"executor": _ERASURE_EXECUTOR_ROLE},
    ).one()
    executor_direct_schema_acl_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_namespace AS namespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    namespace.nspacl,
                    pg_catalog.acldefault('n',namespace.nspowner)
                  )
                ) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND grantee.rolname=:executor
                """
            ),
            {"executor": _ERASURE_EXECUTOR_ROLE},
        ).scalar_one()
    )
    routine = connection.execute(
        text(
            """
            SELECT procedure.oid,owner.rolname,procedure.prosecdef,
                   procedure.proconfig,procedure.prosrc
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_roles AS owner ON owner.oid=procedure.proowner
            WHERE procedure.oid=pg_catalog.to_regprocedure(:signature)
            """
        ),
        {"signature": _RUNNER_SIGNATURE},
    ).one()
    execute_grantees = [
        str(row[0])
        for row in connection.execute(
            text(
                """
                SELECT CASE
                         WHEN acl.grantee=0 THEN 'PUBLIC'
                         ELSE grantee.rolname
                       END AS grantee
                FROM pg_catalog.pg_proc AS procedure
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f',procedure.proowner)
                  )
                ) AS acl
                LEFT JOIN pg_catalog.pg_roles AS grantee
                  ON grantee.oid=acl.grantee
                WHERE procedure.oid=CAST(:routine_oid AS oid)
                  AND acl.privilege_type='EXECUTE'
                  AND grantee.rolname<>:owner
                ORDER BY 1
                """
            ),
            {
                "routine_oid": int(routine[0]),
                "owner": _ERASURE_OWNER_ROLE,
            },
        )
    ]
    privilege_row = connection.execute(
        text(
            """
            SELECT
              pg_catalog.has_function_privilege(
                CAST(:application_role AS name),
                pg_catalog.to_regprocedure(:signature),
                'EXECUTE'
              ),
              EXISTS (
                SELECT 1
                FROM pg_catalog.pg_proc AS procedure
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f',procedure.proowner)
                  )
                ) AS acl
                WHERE procedure.oid=pg_catalog.to_regprocedure(:signature)
                  AND acl.grantee=0
                  AND acl.privilege_type='EXECUTE'
              ),
              pg_catalog.has_function_privilege(
                CAST(:executor_role AS name),
                pg_catalog.to_regprocedure(:signature),
                'EXECUTE'
              )
            """
        ),
        {
            "application_role": application_role,
            "executor_role": _ERASURE_EXECUTOR_ROLE,
            "signature": _RUNNER_SIGNATURE,
        },
    ).one()
    owner_helper_execute_count = int(
        connection.execute(
            text(
                """
                SELECT count(DISTINCT procedure.oid)
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_roles AS owner ON owner.oid=procedure.proowner
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f',procedure.proowner)
                  )
                ) AS acl
                LEFT JOIN pg_catalog.pg_roles AS grantee
                  ON grantee.oid=acl.grantee
                WHERE owner.rolname=:owner
                  AND acl.privilege_type='EXECUTE'
                  AND (
                    acl.grantee=0
                    OR grantee.rolname=:application_role
                  )
                """
            ),
            {
                "owner": _ERASURE_OWNER_ROLE,
                "application_role": application_role,
            },
        ).scalar_one()
    )
    executor_other_routine_execute_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=procedure.pronamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f',procedure.proowner)
                  )
                ) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND grantee.rolname=:executor
                  AND acl.privilege_type='EXECUTE'
                  AND procedure.oid<>CAST(:runner_oid AS oid)
                """
            ),
            {
                "executor": _ERASURE_EXECUTOR_ROLE,
                "runner_oid": int(routine[0]),
            },
        ).scalar_one()
    )
    column_rows = tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            text(
                """
                SELECT relation.relname,attribute.attname,acl.privilege_type
                FROM pg_catalog.pg_attribute AS attribute
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid=attribute.attrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(attribute.attacl,'{}'::aclitem[])
                ) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND attribute.attnum>0
                  AND NOT attribute.attisdropped
                  AND grantee.rolname=:owner
                ORDER BY relation.relname,attribute.attname,acl.privilege_type
                """
            ),
            {"owner": _ERASURE_OWNER_ROLE},
        )
    )
    normalized_column_acl = _normalize_owner_column_acl_rows(
        artifact=artifact,
        rows=column_rows,
    )
    executor_column_privilege_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_attribute AS attribute
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid=attribute.attrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(attribute.attacl,'{}'::aclitem[])
                ) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND attribute.attnum>0
                  AND NOT attribute.attisdropped
                  AND grantee.rolname=:executor
                """
            ),
            {"executor": _ERASURE_EXECUTOR_ROLE},
        ).scalar_one()
    )
    table_acl_rows = tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            text(
                """
                SELECT relation.relname,acl.privilege_type,grantee.rolname
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r',relation.relowner)
                  )
                ) AS acl
                JOIN pg_catalog.pg_roles AS grantee ON grantee.oid=acl.grantee
                WHERE namespace.nspname='public'
                  AND relation.relkind IN ('r','p')
                  AND grantee.rolname IN (:owner,:executor)
                ORDER BY grantee.rolname,relation.relname,acl.privilege_type
                """
            ),
            {
                "owner": _ERASURE_OWNER_ROLE,
                "executor": _ERASURE_EXECUTOR_ROLE,
            },
        )
    )
    normalized_table_acl = _normalize_table_acl_rows(
        artifact=artifact,
        owner_rows=[
            [relation, privilege]
            for relation, privilege, grantee in table_acl_rows
            if grantee == _ERASURE_OWNER_ROLE
        ],
        executor_rows=[
            [relation, privilege]
            for relation, privilege, grantee in table_acl_rows
            if grantee == _ERASURE_EXECUTOR_ROLE
        ],
    )
    force_rls_relations = [
        str(row[0])
        for row in connection.execute(
            text(
                """
                SELECT relation.relname
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname='public'
                  AND relation.relname=ANY(CAST(:relation_names AS text[]))
                  AND relation.relrowsecurity
                  AND relation.relforcerowsecurity
                ORDER BY relation.relname
                """
            ),
            {"relation_names": [row[0] for row in expected["select_relation_column_tuple"]]},
        )
    ]
    return {
        "catalog_session_user": identity[0],
        "catalog_current_user": identity[1],
        "owner_role": _ERASURE_OWNER_ROLE,
        "owner_posture_tuple": _role_posture_tuple(connection, _ERASURE_OWNER_ROLE),
        "executor_role": _ERASURE_EXECUTOR_ROLE,
        "executor_posture_tuple": _role_posture_tuple(
            connection,
            _ERASURE_EXECUTOR_ROLE,
        ),
        "executor_effective_schema_usage": bool(executor_schema_usage),
        "executor_effective_schema_create": bool(executor_schema_create),
        "executor_direct_schema_acl_count": executor_direct_schema_acl_count,
        "executor_other_routine_execute_count": (executor_other_routine_execute_count),
        "application_role": application_role,
        "application_role_posture_tuple": _role_posture_tuple(
            connection,
            application_role,
        ),
        "role_membership_count": membership_count,
        "owner_schema_usage": bool(schema_usage),
        "owner_schema_create": bool(schema_create),
        "owner_owned_relation_count": owner_owned_relation_count,
        "runner_definition_sha256": hashlib.sha256(str(routine[4]).encode("utf-8")).hexdigest(),
        "runner_owner": str(routine[1]),
        "runner_security_definer": bool(routine[2]),
        "runner_proconfig_tuple": list(routine[3] or ()),
        "execute_grantee_tuple": execute_grantees,
        "application_execute": bool(privilege_row[0]),
        "public_execute": bool(privilege_row[1]),
        "executor_execute": bool(privilege_row[2]),
        "owner_helper_execute_count": owner_helper_execute_count,
        **normalized_column_acl,
        **normalized_table_acl,
        "executor_column_privilege_count": executor_column_privilege_count,
        "force_rls_relation_tuple": force_rls_relations,
        "force_rls_relation_count": len(force_rls_relations),
    }


def _install_v19_erasure_authority_scaffold(
    *,
    execution_engine: ModuleType,
    scaffold: ModuleType,
    admin: Connection,
    artifact: Mapping[str, Any],
    application_role: str,
) -> None:
    """Compose the isolated proof with the reviewed external-helper boundary."""

    execution_engine.provision_exact_erasure_principals(admin)
    scaffold.install_artifact_sql_scaffold(
        admin,
        artifact,
        application_role=application_role,
    )
    execution_engine._install_external_erasure_helper_acl(admin, artifact)
    execution_engine._grant_external_erasure_helper_read_closure(admin)
    scaffold.install_current_erasure_authority(admin, artifact)


def _execute_v19_erasure_authority_observation(
    *,
    execution_engine: ModuleType,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    _candidate_artifact(artifact)
    scaffold = _load_v19_scaffold()
    expected_counts = _expected_deletion_counts(artifact)
    with (
        execution_engine._cluster_safety_boundary(),
        execution_engine.disposable_postgres() as database,
    ):
        execution_engine.migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        with database.admin_engine.begin() as admin:
            _install_v19_erasure_authority_scaffold(
                execution_engine=execution_engine,
                scaffold=scaffold,
                admin=admin,
                artifact=artifact,
                application_role=database.role_name,
            )
        execution_engine.grant_application_access(database)
        executor_url = _executor_url_for_disposable(
            configured_url=_required_erasure_database_url(),
            admin_url=database.admin_engine.url,
            database_name=database.database_name,
        )
        executor_engine: Engine = create_engine(executor_url, pool_pre_ping=True)
        try:
            _seed_erasure_fixture(database)
            missing_before = _state_projection(database)
            missing_identity: tuple[str, str] | None = None
            try:
                with executor_engine.begin() as connection:
                    missing_identity = _caller_identity(connection)
                    _invoke_runner(connection)
            except DBAPIError as exc:
                if _sqlstate(exc) != "23514" or "TASK7_ERASURE_ATTEMPTED_EVENT_INVALID" not in str(
                    exc.orig
                ):
                    raise CensusError(_ERASURE_PROOF_ERROR) from exc
            else:
                raise CensusError(_ERASURE_PROOF_ERROR)
            missing_after = _state_projection(database)
            if missing_identity is None:
                raise CensusError(_ERASURE_PROOF_ERROR)

            attempted_event = _persist_attempted_event(database)
            direct_before = _state_projection(database)
            direct_identity: tuple[str, str] | None = None
            try:
                with database.app_engine.begin() as connection:
                    direct_identity = _caller_identity(connection)
                    _invoke_runner(connection)
            except DBAPIError as exc:
                if _sqlstate(exc) != "42501":
                    raise CensusError(_ERASURE_PROOF_ERROR) from exc
            else:
                raise CensusError(_ERASURE_PROOF_ERROR)
            direct_after = _state_projection(database)
            if direct_identity is None:
                raise CensusError(_ERASURE_PROOF_ERROR)

            rollback_identity: tuple[str, str] | None = None
            with executor_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    rollback_identity, raw_rollback_result = _invoke_runner(connection)
                    rollback_result = _validate_runner_result(
                        raw_rollback_result,
                        expected_counts=expected_counts,
                    )
                finally:
                    transaction.rollback()
            rollback_after = _state_projection(database)
            if rollback_identity is None:
                raise CensusError(_ERASURE_PROOF_ERROR)

            legal_before = _state_projection(database)
            with executor_engine.begin() as connection:
                legal_identity, raw_legal_result = _invoke_runner(connection)
                legal_result = _validate_runner_result(
                    raw_legal_result,
                    expected_counts=expected_counts,
                )
            legal_after = _state_projection(database)

            malformed_fixture = _retry_lineage_fixture(
                scope=_MALFORMED_RETRY_SCOPE,
                malformed_prior=True,
            )
            _seed_retry_fixture(database, fixture=malformed_fixture)
            malformed_before = _scoped_state_projection(
                database,
                tenant_id=str(malformed_fixture["tenant_id"]),
                deal_id=str(malformed_fixture["deal_id"]),
                request_id=str(malformed_fixture["request_id"]),
            )
            malformed_identity: tuple[str, str] | None = None
            try:
                with executor_engine.begin() as connection:
                    malformed_identity = _caller_identity(connection)
                    _invoke_runner(
                        connection,
                        request_id=str(malformed_fixture["request_id"]),
                        tenant_id=str(malformed_fixture["tenant_id"]),
                        deal_id=str(malformed_fixture["deal_id"]),
                    )
            except DBAPIError as exc:
                if _sqlstate(exc) != "23514" or "TASK7_ERASURE_PRIOR_EVENT_INVALID" not in str(
                    exc.orig
                ):
                    raise CensusError(_ERASURE_PROOF_ERROR) from exc
            else:
                raise CensusError(_ERASURE_PROOF_ERROR)
            malformed_after = _scoped_state_projection(
                database,
                tenant_id=str(malformed_fixture["tenant_id"]),
                deal_id=str(malformed_fixture["deal_id"]),
                request_id=str(malformed_fixture["request_id"]),
            )
            if malformed_identity is None:
                raise CensusError(_ERASURE_PROOF_ERROR)

            retry_fixture = _retry_lineage_fixture(
                scope=_LEGAL_RETRY_SCOPE,
                malformed_prior=False,
            )
            _seed_retry_fixture(database, fixture=retry_fixture)
            retry_before = _scoped_state_projection(
                database,
                tenant_id=str(retry_fixture["tenant_id"]),
                deal_id=str(retry_fixture["deal_id"]),
                request_id=str(retry_fixture["request_id"]),
            )
            with executor_engine.begin() as connection:
                retry_identity, raw_retry_result = _invoke_runner(
                    connection,
                    request_id=str(retry_fixture["request_id"]),
                    tenant_id=str(retry_fixture["tenant_id"]),
                    deal_id=str(retry_fixture["deal_id"]),
                )
                retry_result = _validate_runner_result(
                    raw_retry_result,
                    expected_counts=expected_counts,
                )
            retry_after = _scoped_state_projection(
                database,
                tenant_id=str(retry_fixture["tenant_id"]),
                deal_id=str(retry_fixture["deal_id"]),
                request_id=str(retry_fixture["request_id"]),
            )
            with database.app_engine.connect() as connection:
                acl_execution = _catalog_acl_observation(
                    connection,
                    artifact=artifact,
                    application_role=database.role_name,
                )
        finally:
            executor_engine.dispose()

    missing_before_sha256 = _state_projection_sha256(missing_before)
    missing_after_sha256 = _state_projection_sha256(missing_after)
    direct_before_sha256 = _state_projection_sha256(direct_before)
    direct_after_sha256 = _state_projection_sha256(direct_after)
    expected_counts_sha256 = _digest(expected_counts)
    persisted_counts = cast(Mapping[str, int], legal_after["request"][3])
    persisted_executed_at = legal_after["request"][2]
    retry_persisted_counts = cast(Mapping[str, int], retry_after["request"][3])
    retry_events = cast(
        Sequence[Mapping[str, Any]],
        retry_fixture["events"],
    )
    retry_prior_failed = retry_events[1]
    retry_attempted = retry_events[2]
    observation: dict[str, Any] = {
        "setup_actor_mode": "ADMIN_DDL_ONLY",
        "application_role_name": database.role_name,
        "behavior_actor_tuple": [
            database.role_name,
            _ERASURE_EXECUTOR_ROLE,
        ],
        "admin_behavior_case_count": 0,
        "application_guard_case_count": 219,
        "application_direct_runner_denial": {
            "executed_as_role": database.role_name,
            "session_user": direct_identity[0],
            "current_user": direct_identity[1],
            "sqlstate": "42501",
            "marker": "FUNCTION_EXECUTE_PRIVILEGE_DENIED",
            "state_before_sha256": direct_before_sha256,
            "state_after_sha256": direct_after_sha256,
            "zero_mutation": direct_before == direct_after,
        },
        "missing_attempt_rejection": {
            "executed_as_role": _ERASURE_EXECUTOR_ROLE,
            "session_user": missing_identity[0],
            "current_user": missing_identity[1],
            "sqlstate": "23514",
            "marker": "TASK7_ERASURE_ATTEMPTED_EVENT_INVALID",
            "state_before_sha256": missing_before_sha256,
            "state_after_sha256": missing_after_sha256,
            "zero_mutation": missing_before == missing_after,
        },
        "rollback_atomicity": {
            "executed_as_role": _ERASURE_EXECUTOR_ROLE,
            "session_user": rollback_identity[0],
            "current_user": rollback_identity[1],
            "inside_status": rollback_result["status"],
            "inside_counts_sha256": _digest(rollback_result["counts"]),
            "inside_relation_rows_remaining": (
                2
                - int(rollback_result["counts"]["deals"])
                - int(rollback_result["counts"]["document_artifacts"])
            ),
            "after_status": rollback_after["request"][0],
            "after_counts_sha256": _digest(rollback_after["request"][3]),
            "after_relation_rows_remaining": rollback_after["relation_row_count"],
            "transaction_rolled_back": True,
            "zero_mutation": rollback_after == direct_after,
        },
        "legal_execution": {
            "executed_as_role": _ERASURE_EXECUTOR_ROLE,
            "session_user": legal_identity[0],
            "current_user": legal_identity[1],
            "attempted_event_precondition_count": sum(
                event[1] == "task7.erasure.attempted" for event in legal_before["audit_event_tuple"]
            ),
            "request_status_before": legal_before["request"][0],
            "request_status_after": legal_after["request"][0],
            "actor_bound_to_attempt": (
                legal_result["executed_by"]
                == attempted_event["actor"]["actor_id"]
                == legal_after["request"][1]
            ),
            "object_rows_reconciled": legal_result["counts"]["object_rows_reconciled"],
            "artifact_row_count": direct_after["relation_row_count"] - 1,
            "deleted_relation_count": len(_current_relation_tuple(artifact)),
            "returned_counts_sha256": expected_counts_sha256,
            "persisted_counts_sha256": _digest(persisted_counts),
            "counts_equal": dict(persisted_counts) == legal_result["counts"],
            "executed_at_equal": (persisted_executed_at == legal_result["executed_at"]),
            "committed": (
                legal_after["relation_row_count"] == 0 and legal_after["request"][0] == "EXECUTED"
            ),
        },
        "malformed_prior_rejection": {
            "executed_as_role": _ERASURE_EXECUTOR_ROLE,
            "session_user": malformed_identity[0],
            "current_user": malformed_identity[1],
            "sqlstate": "23514",
            "marker": "TASK7_ERASURE_PRIOR_EVENT_INVALID",
            "state_before_sha256": _state_projection_sha256(malformed_before),
            "state_after_sha256": _state_projection_sha256(malformed_after),
            "zero_mutation": malformed_before == malformed_after,
        },
        "failed_retry_execution": {
            "executed_as_role": _ERASURE_EXECUTOR_ROLE,
            "session_user": retry_identity[0],
            "current_user": retry_identity[1],
            "request_status_before": retry_before["request"][0],
            "request_status_after": retry_after["request"][0],
            "attempt_ordinal": retry_attempted["payload"]["safe"]["attempt_ordinal"],
            "prior_failed_event_id": retry_fixture["prior_failed_event_id"],
            "prior_failed_event_sha256": retry_fixture["prior_failed_event_sha256"],
            "retry_attempt_id": retry_fixture["retry_attempt_id"],
            "prior_event_identity_rederived": (
                retry_prior_failed["event_id"]
                == _uuid5_from_preimage(
                    _EVENT_NAMESPACE,
                    (
                        "task7-erasure-audit-event-v1",
                        retry_prior_failed["payload"]["safe"]["attempt_id"],
                        "task7.erasure.failed",
                        "FAILED",
                    ),
                )
            ),
            "prior_event_hash_rederived": (
                retry_fixture["prior_failed_event_sha256"]
                == hashlib.sha256(_canonical_bytes(retry_prior_failed)).hexdigest()
            ),
            "retry_attempt_identity_rederived": (
                retry_fixture["retry_attempt_id"]
                == _uuid5_from_preimage(
                    _ATTEMPT_NAMESPACE,
                    (
                        "task7-erasure-attempt-v1",
                        retry_fixture["tenant_id"],
                        retry_fixture["deal_id"],
                        retry_fixture["request_id"],
                        2,
                        "FAILED",
                        retry_fixture["prior_failed_event_id"],
                        retry_fixture["prior_failed_event_sha256"],
                    ),
                )
            ),
            "returned_counts_sha256": _digest(retry_result["counts"]),
            "persisted_counts_sha256": _digest(retry_persisted_counts),
            "counts_equal": (retry_result["counts"] == dict(retry_persisted_counts)),
            "executed_at_equal": (retry_result["executed_at"] == retry_after["request"][2]),
            "committed": (
                retry_after["relation_row_count"] == 0 and retry_after["request"][0] == "EXECUTED"
            ),
        },
        "fixed_vector_execution": _fixed_vector_execution_observation(),
        "acl_execution": acl_execution,
        "catalog_projection_sha256": "",
        "case_result_sha256": "",
    }
    observation["catalog_projection_sha256"] = _catalog_projection_sha256(
        application_role_name=database.role_name,
        acl_execution=acl_execution,
    )
    observation["case_result_sha256"] = _case_result_sha256(observation)
    return observation


def execute_v19_erasure_authority_proof(
    *,
    artifact_path: Path,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute the v19 erasure proof.

    The hash-pinned execution engine creates the disposable database and
    application role.  This module owns the closed v19 dedicated-executor
    observation, so a predecessor harness cannot substitute a synthetic seam.
    """

    execution_engine = _load_execution_engine(Path(artifact_path))
    observation = _execute_v19_erasure_authority_observation(
        execution_engine=execution_engine,
        artifact=artifact,
    )
    if not isinstance(observation, Mapping):
        raise CensusError(_ERASURE_PROOF_ERROR)
    return build_erasure_authority_execution_proof(
        artifact=artifact,
        observation=observation,
    )


def _rehash_complete_execution(result: dict[str, Any]) -> None:
    result["complete_execution_sha256"] = _digest(
        [
            _COMPLETE_EXECUTION_DOMAIN,
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )


def validate_execution_contract(
    *,
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    """Require the predecessor evidence and both v19 database proofs."""

    if not isinstance(
        result.get("downgrade_retained_definition_execution_proof"),
        Mapping,
    ) or not isinstance(
        result.get("erasure_authority_execution_proof"),
        Mapping,
    ):
        raise CensusError("TASK7_V19_EXECUTION_INCOMPLETE")
    expected = execution_expectations(artifact)
    if any(result.get(key) != value for key, value in expected.items()):
        raise CensusError("TASK7_V19_EXECUTION_INCOMPLETE")
    if result.get(
        "materialization_source_manifest_tuple"
    ) != _receipt_materialization_source_evidence(artifact) or result.get(
        "materialization_source_manifest_sha256"
    ) != _receipt_materialization_source_sha256(artifact):
        raise CensusError("TASK7_V19_MATERIALIZATION_SOURCE_DRIFT")
    validate_source_admission_execution_proof(result.get("source_admission_execution_proof"))
    validate_receipt_equality_execution_proof(
        result.get("receipt_equality_execution_proof"),
        artifact=artifact,
    )
    validate_downgrade_retained_definition_execution_proof(
        result.get("downgrade_retained_definition_execution_proof"),
        artifact=artifact,
    )
    validate_erasure_authority_execution_proof(
        result.get("erasure_authority_execution_proof"),
        artifact=artifact,
    )
    supplied = result.get("complete_execution_sha256")
    derived = _digest(
        [
            _COMPLETE_EXECUTION_DOMAIN,
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    if supplied != derived:
        raise CensusError("TASK7_V19_EXECUTION_INCOMPLETE")


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run the immutable guard census and the v19 erasure proof."""

    raw = Path(artifact_path).read_bytes()
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), _V19_CANDIDATE_SHA256):
        raise CensusError("TASK7_V19_CENSUS_ARTIFACT_VERSION_MISMATCH")
    artifact = _decode_json(raw)
    _candidate_artifact(artifact)
    result = dict(
        _BASE._BASE.run_pre_fixture_census(
            artifact_path=Path(artifact_path),
            base_schema_revision=base_schema_revision,
            harness_identity=harness_identity,
            ordered_authority_target_tuple=ordered_authority_target_tuple,
            ordered_trigger_contract_coordinate_tuple=(ordered_trigger_contract_coordinate_tuple),
            production_constructor_spec_tuple=production_constructor_spec_tuple,
        )
    )
    result["downgrade_retained_definition_execution_proof"] = (
        execute_downgrade_retained_definition_execution_proof(
            artifact_path=Path(artifact_path),
            artifact=artifact,
        )
    )
    result["materialization_source_manifest_tuple"] = materialization_source_manifest_evidence()
    result["materialization_source_manifest_sha256"] = materialization_source_manifest_sha256()
    result["v19_adapter_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["v19_scaffold_source_sha256"] = hashlib.sha256(
        _V19_SCAFFOLD_PATH.read_bytes()
    ).hexdigest()
    result["erasure_authority_execution_proof"] = execute_v19_erasure_authority_proof(
        artifact_path=Path(artifact_path),
        artifact=artifact,
    )
    _rehash_complete_execution(result)
    validate_execution_contract(result=result, artifact=artifact)
    return result


def _database_execution(
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Project only safe counts and digests into the frozen fixture."""

    projection = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}
    projection["downgrade_retained_definition_execution_proof_sha256"] = _digest(
        result.get("downgrade_retained_definition_execution_proof")
    )
    projection["erasure_authority_execution_proof_sha256"] = _digest(
        result.get("erasure_authority_execution_proof")
    )
    if all(projection[field] is not None for field in _BASE_DATABASE_EXECUTION_FIELDS):
        validate_database_execution_projection(
            projection=projection,
            result=result,
            artifact=artifact,
        )
    return projection


def validate_database_execution_projection(
    *,
    projection: Mapping[str, Any],
    result: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    validate_execution_contract(result=result, artifact=artifact)
    expected = {field: result.get(field) for field in _BASE_DATABASE_EXECUTION_FIELDS}
    expected["downgrade_retained_definition_execution_proof_sha256"] = _digest(
        result.get("downgrade_retained_definition_execution_proof")
    )
    expected["erasure_authority_execution_proof_sha256"] = _digest(
        result.get("erasure_authority_execution_proof")
    )
    if (
        tuple(projection) != _DATABASE_EXECUTION_FIELDS
        or dict(projection) != expected
        or projection.get("materialization_source_manifest_sha256")
        != _receipt_materialization_source_sha256(artifact)
        or any(
            not _is_sha256(projection[field])
            for field in _DATABASE_EXECUTION_FIELDS
            if field.endswith("_sha256")
        )
    ):
        raise CensusError("TASK7_V19_DATABASE_EXECUTION_PROJECTION_INVALID")


def _prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    artifact = _decode_json(candidate_raw)
    _candidate_artifact(artifact)
    frozen = copy.deepcopy(artifact)
    frozen["metadata"]["append_frozen"] = True
    return (
        json.dumps(
            frozen,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Build a signed receipt only after both database proofs validate."""

    _candidate_artifact(artifact)
    validate_execution_contract(result=result, artifact=artifact)
    if not isinstance(signer_id, str) or not signer_id.strip():
        raise CensusError(_CENSUS_RECEIPT_ERROR)
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": _V19_ARTIFACT_SCHEMA,
        "pipeline_contract_version": _V19_CONTRACT_VERSION,
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(
            _prospective_frozen_bytes(artifact_raw)
        ).hexdigest(),
        "database_execution": _database_execution(result, artifact),
        "census_result": dict(result),
    }
    payload_sha256 = _digest(payload)
    public_key_sha256 = hashlib.sha256(trusted_public_key_raw).hexdigest()
    signature = private_key.sign(
        _canonical_bytes(
            [
                _CENSUS_SIGNING_DOMAIN,
                signer_id,
                payload_sha256,
                public_key_sha256,
                payload,
            ]
        )
    )
    receipt = {
        "schema_version": _CENSUS_ENVELOPE_SCHEMA,
        "signer_id": signer_id,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    verify_signed_receipt(
        receipt_raw=(
            json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        artifact_raw=artifact_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    return receipt


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify the Ed25519 envelope and revalidate both database proofs."""

    try:
        artifact = _decode_json(artifact_raw)
        _candidate_artifact(artifact)
        receipt = _decode_json(receipt_raw)
        if tuple(receipt) != (
            "schema_version",
            "signer_id",
            "payload",
            "payload_sha256",
            "public_key_sha256",
            "signature_base64",
        ):
            raise CensusError(_CENSUS_RECEIPT_ERROR)
        payload = receipt["payload"]
        if (
            receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA
            or not isinstance(payload, Mapping)
            or tuple(payload) != _CENSUS_PAYLOAD_KEYS
            or payload.get("schema_version") != _CENSUS_PAYLOAD_SCHEMA
            or payload.get("result") != "PASS"
            or payload.get("artifact_schema") != _V19_ARTIFACT_SCHEMA
            or payload.get("pipeline_contract_version") != _V19_CONTRACT_VERSION
            or payload.get("candidate_artifact_sha256") != hashlib.sha256(artifact_raw).hexdigest()
            or payload.get("prospective_frozen_artifact_sha256")
            != hashlib.sha256(_prospective_frozen_bytes(artifact_raw)).hexdigest()
            or receipt["payload_sha256"] != _digest(payload)
            or receipt["public_key_sha256"] != hashlib.sha256(trusted_public_key_raw).hexdigest()
        ):
            raise CensusError(_CENSUS_RECEIPT_ERROR)
        result = payload.get("census_result")
        if not isinstance(result, Mapping):
            raise CensusError(_CENSUS_RECEIPT_ERROR)
        validate_execution_contract(result=result, artifact=artifact)
        if payload.get("database_execution") != _database_execution(result, artifact):
            raise CensusError(_CENSUS_RECEIPT_ERROR)
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise CensusError(_CENSUS_RECEIPT_ERROR)
        signature = base64.b64decode(str(receipt["signature_base64"]), validate=True)
        public_key.verify(
            signature,
            _canonical_bytes(
                [
                    _CENSUS_SIGNING_DOMAIN,
                    receipt["signer_id"],
                    receipt["payload_sha256"],
                    receipt["public_key_sha256"],
                    payload,
                ]
            ),
        )
    except (
        CensusError,
        InvalidSignature,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        if isinstance(exc, CensusError) and str(exc) == _CENSUS_RECEIPT_ERROR:
            raise
        raise CensusError(_CENSUS_RECEIPT_ERROR) from exc
    return dict(payload)


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "_BASE",
    "_CENSUS_SIGNING_DOMAIN",
    "_DATABASE_EXECUTION_FIELDS",
    "_PRODUCTION_CONSTRUCTOR_SPECS",
    "_V18_RUNNER_SHA256",
    "_V19_CANDIDATE_SHA256",
    "_canonical_bytes",
    "_database_execution",
    "_digest",
    "_rehash_complete_execution",
    "_validate_v19_candidate",
    "artifact_targets",
    "artifact_trigger_contract",
    "build_and_verify_signed_receipt",
    "build_erasure_authority_execution_proof",
    "erasure_authority_execution_expectations",
    "execute_downgrade_retained_definition_execution_proof",
    "execute_v19_erasure_authority_proof",
    "execution_expectations",
    "materialization_source_manifest",
    "materialization_source_manifest_evidence",
    "materialization_source_manifest_sha256",
    "receipt_equality_execution_plan",
    "receipt_fixture_coordinates",
    "run_pre_fixture_census",
    "validate_database_execution_projection",
    "validate_downgrade_retained_definition_execution_proof",
    "validate_erasure_authority_execution_proof",
    "validate_execution_contract",
    "validate_receipt_equality_execution_proof",
    "validate_source_admission_execution_proof",
    "verify_receipt_materialization_source_closure",
    "verify_signed_receipt",
]
