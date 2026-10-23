"""Execute the unfrozen full-strict-v12 guard census as the application role.

Administrator connections are setup-only: revision 0037 migration, principal
provisioning, artifact/RLS/ACL installation, committed trigger toggles, and
between-case cleanup.  Constructor writes and every legal, illegal, and direct
SQL guard operation execute through the generated NOSUPERUSER/NOBYPASSRLS
application engine.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from scripts._task7_v12_census_scaffold import (
    GUARD_OWNER_ROLE,
    catalog_projection,
    current_function_rows,
    disable_all_current_user_triggers,
    disable_target_trigger,
    enable_target_trigger,
    install_artifact_sql_scaffold,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import (
    DisposablePostgres,
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task7._postgres_support import (
    provision_exact_erasure_principals,
)

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v12-current-guard-app-role-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_APP_ROLE_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v12-current-guard-app-role-census-result-v1"

TASK6_WRITE_CONTEXT_GUCS: Final = (
    "idis.worker_lease_tenant_id",
    "idis.worker_lease_deal_id",
    "idis.worker_lease_run_id",
    "idis.worker_lease_owner_id",
    "idis.worker_lease_fencing_token",
)
_TASK6_OUTPUT_ROOT_UPDATE_TARGETS: Final = (
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
)
_CASE_KINDS: Final = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
_EVENT_ORDER: Final = ("INSERT", "UPDATE", "DELETE")
_PRODUCTION_CONSTRUCTOR_SPECS: Final = (
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
_HISTORICAL_RUNNER_PATH: Final = Path(__file__).with_name("run_task7_v11_current_guard_census.py")
_HISTORICAL_RUNNER_SHA256: Final = (
    "7c3ced9c8a61923ea70aeee1a30cb71584d152fc7d56f4ac95dffc24d56d307e"
)
_GENERATOR_PATH: Final = Path(__file__).with_name("generate_task7_v12_contract_fixture.py")
_EXPECTED_ARTIFACT_SHA512: Final = (
    "49df285a505c1bc4630864547d2a79a0caed76e659ec56440240378195968fbd0"
    "fcc555cfd5bf481dd22cb4fbbf84763da64eb91edc29441d5170891dc63ba9f"
)
_CENSUS_ENVELOPE_SCHEMA: Final = "task7-v12-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v12-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN: Final = "task7-v12-postgres-census-signable-v1"
_IDENTIFIER_RE: Final = re.compile(r"[a-z][a-z0-9_]*")

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]


class CensusError(RuntimeError):
    """Secret-safe v12 application-role census failure."""


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise CensusError("TASK7_V12_CENSUS_IDENTIFIER_INVALID")
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CensusError(f"TASK7_V12_CENSUS_MODULE_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_historical_execution_engine() -> ModuleType:
    raw = _HISTORICAL_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_RUNNER_SHA256:
        raise CensusError("TASK7_V12_HISTORICAL_CENSUS_ENGINE_DRIFT")
    module = _load_module(
        _HISTORICAL_RUNNER_PATH,
        "_task7_v11_current_guard_engine_for_v12",
    )
    if module.BASE_SCHEMA_REVISION != BASE_SCHEMA_REVISION:
        raise CensusError("TASK7_V12_HISTORICAL_CENSUS_ENGINE_IDENTITY_MISMATCH")
    return module


_V11 = _load_historical_execution_engine()
_HISTORICAL_NATIVE_SOURCE_VALUES: Final = _V11._native_source_values
_HISTORICAL_UNBOUND_SOURCE_VALUES: Final = _V11._unbound_source_values
_HISTORICAL_NATIVE_OUTPUT_VALUES: Final = _V11._native_output_values
_GENERATOR = _load_module(
    _GENERATOR_PATH,
    "_task7_v12_census_receipt_generator",
)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _GENERATOR._canonical_bytes(value))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CensusError(f"TASK7_V12_CENSUS_NON_JSON_EVIDENCE:{type(value).__name__}")


def _decode_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if hashlib.sha512(raw).hexdigest() != _EXPECTED_ARTIFACT_SHA512:
        raise CensusError("TASK7_V12_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED")
    artifact = cast(dict[str, Any], _GENERATOR._decode_artifact(raw))
    _GENERATOR._validate_shape(raw, artifact)
    metadata = artifact["metadata"]
    if (
        metadata["artifact_schema"] != "full-strict-v12-reviewed-preimage-v1"
        or metadata["pipeline_contract_version"] != "full-strict-v12"
        or metadata["append_frozen"] is not False
    ):
        raise CensusError("TASK7_V12_CENSUS_ARTIFACT_VERSION_MISMATCH")
    return raw, artifact


def _ensure_admin_url() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        return
    dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not dotenv_path:
        raise CensusError("TASK7_V12_POSTGRES_ADMIN_URL_REQUIRED")
    try:
        from idis.runtime.profile import _parse_dotenv

        values = _parse_dotenv(dotenv_path)
        admin_url = values["IDIS_DATABASE_ADMIN_URL"].strip()
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise CensusError("TASK7_V12_POSTGRES_ADMIN_URL_REQUIRED") from exc
    if not admin_url:
        raise CensusError("TASK7_V12_POSTGRES_ADMIN_URL_REQUIRED")
    os.environ["IDIS_DATABASE_ADMIN_URL"] = admin_url


def validate_fixture_values(
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    table_name: str,
    values: Mapping[str, Any],
) -> tuple[tuple[str, Any, str], ...]:
    """Delegate the strict seam while projecting only the v12 error domain."""

    try:
        return cast(
            tuple[tuple[str, Any, str], ...],
            _V11.validate_fixture_values(columns, table_name, values),
        )
    except _V11.CensusError as exc:
        raise CensusError(str(exc).replace("TASK7_V11_", "TASK7_V12_")) from exc


def corrected_native_source_values(
    *,
    table_name: str,
    tenant_id: str,
    deal_id: str,
    root_id: str,
    corpus_id: str,
) -> dict[str, Any]:
    """Correct the historical package seed without weakening strict inserts."""

    values = dict(
        _HISTORICAL_NATIVE_SOURCE_VALUES(
            table_name=table_name,
            tenant_id=tenant_id,
            deal_id=deal_id,
            root_id=root_id,
            corpus_id=corpus_id,
        )
    )
    if table_name == "data_room_packages":
        if values.pop("source_corpus_receipt_id", None) != corpus_id:
            raise CensusError("TASK7_V12_PACKAGE_NATIVE_SEED_INVALID")
        values["package_id"] = corpus_id
        values["status"] = "SEALED"
    elif table_name == "data_room_package_files":
        if values.pop("source_corpus_receipt_id", None) != corpus_id:
            raise CensusError("TASK7_V12_PACKAGE_FILE_NATIVE_SEED_INVALID")
        values.update(
            {
                "file_entry_id": str(uuid.uuid4()),
                "package_id": corpus_id,
                "sequence": 1,
                "path_hash": "4" * 64,
                "file_status": "PENDING",
                "support_status": "SUPPORTED",
                "triage_status": "READY",
                "doc_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
            }
        )
    return values


def corrected_unbound_source_values(*, table_name: str) -> dict[str, Any]:
    """Let PostgreSQL derive the package corpus root from package_id."""

    values = dict(_HISTORICAL_UNBOUND_SOURCE_VALUES(table_name=table_name))
    if table_name == "data_room_packages":
        corpus_id = values.pop("source_corpus_receipt_id", None)
        if not isinstance(corpus_id, str) or "package_id" not in values:
            raise CensusError("TASK7_V12_PACKAGE_UNBOUND_SEED_INVALID")
    return values


def artifact_native_output_discriminators(
    artifact: Mapping[str, Any],
) -> dict[str, str]:
    patterns = {
        "deterministic_calculations": re.compile(
            r"producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' "
            r"AND run_output_object_kind='([^']+)'"
        ),
        "muhasabah_records": re.compile(
            r"source_step='METHODOLOGY_EVIDENCE_TRUST_COURT' AND "
            r"run_output_object_kind='([^']+)'"
        ),
        "vector_embeddings": re.compile(
            r"producer_step_name='EXTRACT' AND embedding_namespace="
            r"'internal_evidence' AND run_output_object_kind='([^']+)'"
        ),
    }
    rows = {
        str(row["table_name"]): row
        for row in artifact["current_ddl_registry"]["rows"]
        if str(row["table_name"]) in patterns
    }
    if set(rows) != set(patterns):
        raise CensusError("TASK7_V12_NATIVE_DISCRIMINATOR_TARGET_DRIFT")
    result: dict[str, str] = {}
    for table_name, pattern in patterns.items():
        reviewed_checks = " ".join(str(value) for value in rows[table_name]["check_tuple"])
        match = pattern.search(reviewed_checks)
        if match is None or not match.group(1):
            raise CensusError(f"TASK7_V12_NATIVE_DISCRIMINATOR_MISSING:{table_name}")
        result[table_name] = match.group(1)
    return result


def corrected_native_output_values(
    *,
    table_name: str,
    graph: Mapping[str, Any],
    discriminator_by_table: Mapping[str, str],
) -> dict[str, Any]:
    """Populate the three reviewed discriminator columns that are not generated."""

    values = dict(
        _HISTORICAL_NATIVE_OUTPUT_VALUES(
            table_name=table_name,
            graph=graph,
        )
    )
    if set(discriminator_by_table) != {
        "deterministic_calculations",
        "muhasabah_records",
        "vector_embeddings",
    }:
        raise CensusError("TASK7_V12_NATIVE_DISCRIMINATOR_TARGET_DRIFT")
    discriminator = discriminator_by_table.get(table_name)
    if discriminator is not None:
        values["run_output_object_kind"] = discriminator
    return values


def _fixture_rejection_proofs(
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> tuple[tuple[str, str, str, str], ...]:
    cases = (
        (
            "UNKNOWN_FIELD",
            "runs",
            "unknown_fixture_field",
            {"unknown_fixture_field": "rejected"},
        ),
        (
            "OTHER_TARGET_FIELD",
            "runs",
            "step_id",
            {"step_id": str(uuid.uuid4())},
        ),
        (
            "GENERATED_FIELD",
            "methodology_extraction_results",
            "run_output_object_kind",
            {"run_output_object_kind": "rejected"},
        ),
    )
    result: list[tuple[str, str, str, str]] = []
    for kind, table_name, field, values in cases:
        try:
            validate_fixture_values(columns, table_name, values)
        except CensusError as exc:
            result.append((kind, table_name, field, str(exc)))
        else:
            raise CensusError(f"TASK7_V12_CENSUS_FIXTURE_REJECTION_MISSING:{table_name}.{field}")
    return tuple(result)


def _artifact_trigger_contract(
    artifact: Mapping[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    result: list[tuple[Any, ...]] = []
    for function in current_function_rows(artifact):
        signature = str(function["function_signature"])
        function_name = signature.partition("(")[0].removeprefix("public.")
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
    contract = tuple(result)
    if len(contract) != 192 or len(set(contract)) != 192:
        raise CensusError("TASK7_V12_TRIGGER_CONTRACT_INVENTORY_INVALID")
    return contract


def _artifact_targets(
    artifact: Mapping[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        dict.fromkeys(
            (str(row[0]), str(row[1]), str(row[2])) for row in _artifact_trigger_contract(artifact)
        )
    )


def _expected_trigger_projection(
    contract: Sequence[Sequence[Any]],
    targets: Sequence[tuple[str, str, str]],
) -> tuple[tuple[Any, ...], ...]:
    result: list[tuple[Any, ...]] = []
    for authority_id, table_name, trigger_name in targets:
        rows = tuple(
            tuple(row)
            for row in contract
            if tuple(row[:3]) == (authority_id, table_name, trigger_name)
        )
        if not rows:
            raise CensusError("TASK7_V12_TRIGGER_CONTRACT_TARGET_MISSING")
        first = rows[0]
        result.append(
            (
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
            )
        )
    return tuple(result)


def _validate_request(
    *,
    artifact: Mapping[str, Any],
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> tuple[
    tuple[tuple[str, str, str], ...],
    tuple[tuple[Any, ...], ...],
]:
    if base_schema_revision != BASE_SCHEMA_REVISION or harness_identity != HARNESS_IDENTITY:
        raise CensusError("TASK7_V12_CENSUS_HARNESS_BINDING_MISMATCH")
    targets = tuple(tuple(map(str, row)) for row in ordered_authority_target_tuple)
    contract = tuple(tuple(row) for row in ordered_trigger_contract_coordinate_tuple)
    if targets != _artifact_targets(artifact) or len(targets) != 73:
        raise CensusError("TASK7_V12_CENSUS_TARGET_INVENTORY_MISMATCH")
    if contract != _artifact_trigger_contract(artifact):
        raise CensusError("TASK7_V12_CENSUS_TRIGGER_CONTRACT_MISMATCH")
    if len(production_constructor_spec_tuple) != 4:
        raise CensusError("TASK7_V12_CENSUS_CONSTRUCTOR_INVENTORY_MISMATCH")
    return targets, contract


def _live_trigger_projection(
    connection: Connection,
    contract: Sequence[Sequence[Any]],
    targets: Sequence[tuple[str, str, str]],
) -> tuple[tuple[Any, ...], ...]:
    records = {
        (str(row.table_name), str(row.trigger_name)): row
        for row in connection.execute(
            text(
                """
                SELECT c.relname AS table_name,t.tgname AS trigger_name,
                       t.tgtype,t.tgenabled,
                       (t.tgconstraint<>0) AS is_constraint,
                       t.tgdeferrable,t.tginitdeferred,
                       pn.nspname AS function_schema,
                       p.proname AS function_name
                FROM pg_catalog.pg_trigger AS t
                JOIN pg_catalog.pg_class AS c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_proc AS p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace AS pn ON pn.oid=p.pronamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname
                """
            )
        )
    }
    projection: list[tuple[Any, ...]] = []
    for authority_id, table_name, trigger_name in targets:
        record = records.get((table_name, trigger_name))
        if record is None:
            raise CensusError(f"TASK7_V12_CENSUS_TRIGGER_MISSING:{table_name}.{trigger_name}")
        rows = tuple(
            tuple(row)
            for row in contract
            if tuple(row[:3]) == (authority_id, table_name, trigger_name)
        )
        tgtype = int(record.tgtype)
        actual_events = tuple(
            event for event, bit in (("INSERT", 4), ("UPDATE", 16), ("DELETE", 8)) if tgtype & bit
        )
        expected_events = tuple(str(row[4]) for row in rows)
        if actual_events != expected_events:
            raise CensusError(f"TASK7_V12_CENSUS_TRIGGER_EVENT_DRIFT:{table_name}.{trigger_name}")
        actual_common = (
            "BEFORE" if tgtype & 2 else "AFTER",
            "ROW" if tgtype & 1 else "STATEMENT",
            str(record.tgenabled),
            bool(record.is_constraint),
            bool(record.tgdeferrable),
            bool(record.tginitdeferred),
            str(record.function_schema),
            str(record.function_name),
        )
        expected_common = (
            str(rows[0][3]),
            str(rows[0][5]),
            str(rows[0][6]),
            bool(rows[0][7]),
            bool(rows[0][8]),
            bool(rows[0][9]),
            str(rows[0][10]),
            str(rows[0][11]),
        )
        if actual_common != expected_common or any(
            (
                str(row[3]),
                str(row[5]),
                str(row[6]),
                bool(row[7]),
                bool(row[8]),
                bool(row[9]),
                str(row[10]),
                str(row[11]),
            )
            != expected_common
            for row in rows
        ):
            raise CensusError(
                f"TASK7_V12_CENSUS_TRIGGER_PROJECTION_DRIFT:{table_name}.{trigger_name}"
            )
        projection.append(
            (
                authority_id,
                table_name,
                trigger_name,
                actual_common[0],
                list(actual_events),
                actual_common[1],
                actual_common[2],
                "CONSTRAINT" if actual_common[3] else "PLAIN",
                actual_common[4],
                actual_common[5],
                actual_common[6],
                actual_common[7],
            )
        )
    if len(projection) != 73:
        raise CensusError("TASK7_V12_CENSUS_TRIGGER_PROJECTION_INCOMPLETE")
    return tuple(projection)


def _current_routine_catalog(
    connection: Connection,
    artifact: Mapping[str, Any],
    *,
    application_role: str,
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for expected in current_function_rows(artifact):
        signature = str(expected["function_signature"])
        row = connection.execute(
            text(
                """
                SELECT p.oid,n.nspname,p.proname,owner.rolname AS owner,
                       p.prosecdef,p.proconfig
                FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
                WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                """
            ),
            {"signature": signature},
        ).one_or_none()
        if row is None:
            raise CensusError(f"TASK7_V12_CURRENT_ROUTINE_MISSING:{signature}")
        configuration = tuple(str(value) for value in (row.proconfig or ()))
        search_path = next(
            (
                value.removeprefix("search_path=")
                for value in configuration
                if value.startswith("search_path=")
            ),
            "",
        )
        app_execute = bool(
            connection.execute(
                text(
                    "SELECT pg_catalog.has_function_privilege("
                    ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
                ),
                {"role_name": application_role, "signature": signature},
            ).scalar_one()
        )
        public_execute = bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_proc p
                      CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                      ) acl
                      WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                        AND acl.grantee=0 AND acl.privilege_type='EXECUTE'
                    )
                    """
                ),
                {"signature": signature},
            ).scalar_one()
        )
        raw_execute_acl = tuple(
            {
                "grantee": str(acl.grantee),
                "grantor": str(acl.grantor),
                "privilege_type": str(acl.privilege_type),
                "is_grantable": bool(acl.is_grantable),
            }
            for acl in connection.execute(
                text(
                    """
                    SELECT grantee.rolname AS grantee,
                           grantor.rolname AS grantor,
                           acl.privilege_type,acl.is_grantable
                    FROM pg_catalog.pg_proc p
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                    ) acl
                    JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                    JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                    WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                    ORDER BY grantee.rolname,acl.privilege_type,grantor.rolname
                    """
                ),
                {"signature": signature},
            )
        )
        projected = {
            "function_signature": signature,
            "function_kind": str(expected["function_kind"]),
            "owner": str(row.owner),
            "security_definer": bool(row.prosecdef),
            "search_path": search_path,
            "application_direct_execute": app_execute,
            "public_direct_execute": public_execute,
            "raw_execute_acl_tuple": list(raw_execute_acl),
        }
        if (
            projected["owner"] != GUARD_OWNER_ROLE
            or projected["security_definer"] != (expected["security_mode"] == "DEFINER")
            or projected["search_path"] != "pg_catalog, pg_temp"
            or app_execute
            or public_execute
            or len(raw_execute_acl) != 1
            or {
                key: raw_execute_acl[0][key]
                for key in ("grantee", "grantor", "privilege_type", "is_grantable")
            }
            != {
                "grantee": GUARD_OWNER_ROLE,
                "grantor": GUARD_OWNER_ROLE,
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
        ):
            raise CensusError(f"TASK7_V12_CURRENT_ROUTINE_ACL_DRIFT:{signature}")
        result.append(projected)
    if len(result) != 19:
        raise CensusError("TASK7_V12_CURRENT_ROUTINE_CATALOG_INCOMPLETE")
    return tuple(result)


def _guard_owner_raw_attribute_acl(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    role = artifact["sql_authority_registry"]["guard_function_acl_registry"]["guard_owner_role"]
    expected = {
        (str(relation), str(column))
        for relation, columns in role["select_relation_column_tuple"]
        for column in columns
    }
    rows = tuple(
        {
            "relation": str(row.relation),
            "column": str(row.column_name),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
            "raw_attacl": str(row.raw_attacl),
        }
        for row in connection.execute(
            text(
                """
                SELECT c.relname AS relation,a.attname AS column_name,
                       acl.privilege_type,acl.is_grantable,
                       a.attacl::text AS raw_attacl
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
                  AND grantee.rolname=:role_name
                ORDER BY c.relname,a.attnum,acl.privilege_type
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        )
    )
    actual = {
        (row["relation"], row["column"])
        for row in rows
        if row["privilege_type"] == "SELECT" and not row["is_grantable"]
    }
    if actual != expected or len(rows) != 366:
        raise CensusError("TASK7_V12_GUARD_OWNER_COLUMN_ACL_DRIFT")
    if any(row["privilege_type"] != "SELECT" or row["is_grantable"] for row in rows):
        raise CensusError("TASK7_V12_GUARD_OWNER_NONSELECT_ACL_FORBIDDEN")
    return rows


def _guard_owner_raw_schema_acl(connection: Connection) -> tuple[dict[str, Any], ...]:
    rows = tuple(
        {
            "schema_name": str(row.schema_name),
            "grantee": str(row.grantee),
            "grantor": str(row.grantor),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
        }
        for row in connection.execute(
            text(
                """
                SELECT n.nspname AS schema_name,grantee.rolname AS grantee,
                       grantor.rolname AS grantor,
                       acl.privilege_type,acl.is_grantable
                FROM pg_catalog.pg_namespace n
                CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                WHERE n.nspname='public' AND grantee.rolname=:role_name
                ORDER BY acl.privilege_type,grantor.rolname
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        )
    )
    if len(rows) != 1 or {
        key: rows[0][key] for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
    } != {
        "schema_name": "public",
        "grantee": GUARD_OWNER_ROLE,
        "privilege_type": "USAGE",
        "is_grantable": False,
    }:
        raise CensusError("TASK7_V12_GUARD_OWNER_SCHEMA_ACL_DRIFT")
    return rows


def _guard_owner_posture(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    record = connection.execute(
        text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=:role_name"
        ),
        {"role_name": GUARD_OWNER_ROLE},
    ).one()
    membership_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_auth_members m "
                "JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member "
                "JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
                "WHERE member_role.rolname=:role_name "
                "OR granted_role.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    owned_relation_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_roles owner ON owner.oid=c.relowner "
                "WHERE owner.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    owned_routine_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner "
                "WHERE owner.rolname=:role_name"
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).scalar_one()
    )
    schema_usage, schema_create = connection.execute(
        text(
            "SELECT pg_catalog.has_schema_privilege(:role_name,'public','USAGE'),"
            "pg_catalog.has_schema_privilege(:role_name,'public','CREATE')"
        ),
        {"role_name": GUARD_OWNER_ROLE},
    ).one()
    privilege_counts = tuple(
        int(value)
        for value in connection.execute(
            text(
                """
                SELECT
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'SELECT'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'INSERT'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'UPDATE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'DELETE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'TRUNCATE'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'REFERENCES'
                    )
                  ),
                  count(*) FILTER (
                    WHERE pg_catalog.has_table_privilege(
                      :role_name,c.oid,'TRIGGER'
                    )
                  )
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                """
            ),
            {"role_name": GUARD_OWNER_ROLE},
        ).one()
    )
    relation_names = [
        str(row[0])
        for row in artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "guard_owner_role"
        ]["select_relation_column_tuple"]
    ]
    force_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=ANY(:relations) "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            ),
            {"relations": relation_names},
        ).scalar_one()
    )
    if force_count != 36:
        raise CensusError("TASK7_V12_GUARD_PARENT_FORCE_RLS_INCOMPLETE")
    posture = {
        "role_name": GUARD_OWNER_ROLE,
        "can_login": bool(record.rolcanlogin),
        "is_superuser": bool(record.rolsuper),
        "can_create_db": bool(record.rolcreatedb),
        "can_create_role": bool(record.rolcreaterole),
        "inherits": bool(record.rolinherit),
        "bypass_rls": bool(record.rolbypassrls),
        "membership_count": membership_count,
        "owned_relation_count": owned_relation_count,
        "owned_routine_count": owned_routine_count,
        "schema_usage": bool(schema_usage),
        "schema_create": bool(schema_create),
        "table_select_privilege_count": privilege_counts[0],
        "table_insert_privilege_count": privilege_counts[1],
        "table_update_privilege_count": privilege_counts[2],
        "table_delete_privilege_count": privilege_counts[3],
        "table_truncate_privilege_count": privilege_counts[4],
        "table_references_privilege_count": privilege_counts[5],
        "table_trigger_privilege_count": privilege_counts[6],
    }
    expected = {
        "role_name": GUARD_OWNER_ROLE,
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
    if posture != expected:
        raise CensusError("TASK7_V12_GUARD_OWNER_POSTURE_INVALID")
    return posture, force_count


def _sqlstate(error: DBAPIError) -> str:
    value = getattr(error.orig, "sqlstate", None) or getattr(
        error.orig,
        "pgcode",
        None,
    )
    rendered = str(value or "")
    if not re.fullmatch(r"[0-9A-Z]{5}", rendered):
        raise CensusError("TASK7_V12_CENSUS_SQLSTATE_MISSING") from error
    return rendered


def _rejection_marker(error: DBAPIError, authority_id: str) -> str:
    try:
        return str(_V11._rejection_marker(error, authority_id))
    except _V11.CensusError as exc:
        raise CensusError(str(exc).replace("TASK7_V11_", "TASK7_V12_")) from exc


def _set_tenant_context(connection: Connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT pg_catalog.set_config('idis.tenant_id',:tenant_id,true)"),
        {"tenant_id": tenant_id},
    )


class _CommittedTriggerToggle:
    """Translate the historical replica seam into committed admin DDL phases."""

    def __init__(
        self,
        *,
        app_connection: Connection,
        admin_engine: Any,
        table_name: str,
        trigger_name: str,
    ) -> None:
        self.app_connection = app_connection
        self.admin_engine = admin_engine
        self.table_name = table_name
        self.trigger_name = trigger_name
        self.tenant_id: str | None = None
        self.fixture_helper_projections: list[dict[str, Any]] = []

    def observe_tenant(self, tenant_id: Any) -> None:
        rendered = str(tenant_id)
        if self.tenant_id is not None and self.tenant_id != rendered:
            raise CensusError("TASK7_V12_CASE_TENANT_DRIFT")
        self.tenant_id = rendered
        _set_tenant_context(self.app_connection, rendered)

    def set_replica(self, _connection: Connection, enabled: bool) -> None:
        if self.app_connection.in_transaction():
            self.app_connection.commit()
        with self.admin_engine.begin() as admin:
            if enabled:
                disable_target_trigger(
                    admin,
                    self.table_name,
                    self.trigger_name,
                )
            else:
                enable_target_trigger(
                    admin,
                    self.table_name,
                    self.trigger_name,
                )
        if not enabled and self.tenant_id is not None:
            _set_tenant_context(self.app_connection, self.tenant_id)

    def restore_tenant_context(self) -> None:
        if self.tenant_id is None:
            raise CensusError("TASK7_V12_CASE_TENANT_CONTEXT_MISSING")
        _set_tenant_context(self.app_connection, self.tenant_id)


def _observe_case_actor(connection: Connection, *, expected_role: str) -> dict[str, Any]:
    row = connection.execute(
        text(
            "SELECT session_user,current_user,r.rolsuper,r.rolbypassrls "
            "FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"
        )
    ).one()
    observed = {
        "session_user": str(row.session_user),
        "current_user": str(row.current_user),
        "is_superuser": bool(row.rolsuper),
        "bypass_rls": bool(row.rolbypassrls),
    }
    if observed != {
        "session_user": expected_role,
        "current_user": expected_role,
        "is_superuser": False,
        "bypass_rls": False,
    }:
        raise CensusError("TASK7_V12_CASE_APPLICATION_ROLE_POSTURE_INVALID")
    return observed


def _v12_output_root_values(
    graph: Mapping[str, Any],
    *,
    output_kind: str,
    identity_sha256: str,
    output_sha256: str,
) -> dict[str, Any]:
    values = dict(
        _V11._output_root_values(
            graph,
            output_kind=output_kind,
            identity_sha256=identity_sha256,
            output_sha256=output_sha256,
        )
    )
    values["output_schema_version"] = "task7-v12-census-v1"
    values["pipeline_contract_version"] = "full-strict-v12"
    return values


def _fixture_native_hashes_as_admin(
    *,
    database: DisposablePostgres,
    toggle: _CommittedTriggerToggle,
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    graph: Mapping[str, Any],
    output_kind: str,
) -> tuple[str, str]:
    signature = "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
    with database.admin_engine.begin() as admin:
        _set_tenant_context(admin, str(graph["tenant_id"]))
        actor = admin.execute(text("SELECT current_user")).scalar_one()
        rows = (
            admin.execute(
                text(
                    """
                SELECT output_kind,expected_native_identity_sha256,
                       expected_native_output_sha256
                FROM public.idis_task7_current_native_hashes_v1(
                    CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                    CAST(:run_id AS uuid),CAST(:output_object_id AS uuid)
                )
                """
                ),
                dict(graph),
            )
            .mappings()
            .all()
        )
    if len(rows) != 1 or str(rows[0]["output_kind"]) != output_kind:
        raise CensusError("TASK7_V12_FIXTURE_NATIVE_HASH_DISPATCH_INCOMPLETE")
    identity_sha256 = str(rows[0]["expected_native_identity_sha256"])
    output_sha256 = str(rows[0]["expected_native_output_sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", identity_sha256) or not re.fullmatch(
        r"[0-9a-f]{64}",
        output_sha256,
    ):
        raise CensusError("TASK7_V12_FIXTURE_NATIVE_HASH_INVALID")
    toggle.fixture_helper_projections.append(
        {
            "authority_id": authority_id,
            "table_name": table_name,
            "guard_name": guard_name,
            "case_kind": case_kind,
            "phase": "COMMITTED_FIXTURE_SETUP",
            "function_signature": signature,
            "executed_as_role": str(actor),
            "output_kind": output_kind,
            "returned_pair_sha256": _digest(
                [
                    "task7-v12-fixture-native-hash-pair-v1",
                    identity_sha256,
                    output_sha256,
                ]
            ),
        }
    )
    return identity_sha256, output_sha256


def _commit_fixture_setup(connection: Connection, toggle: _CommittedTriggerToggle) -> None:
    if connection.in_transaction():
        connection.commit()
    toggle.restore_tenant_context()


def _output_exact_one_case_as_app(
    *,
    database: DisposablePostgres,
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    toggle: _CommittedTriggerToggle,
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, int]]:
    """Exercise exact-one DML as app while admin only derives fixture hashes."""

    native_table = (
        "methodology_coverage_records" if table_name == "run_output_objects" else table_name
    )
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "output_object_id": str(uuid.uuid4()),
    }
    toggle.set_replica(connection, True)

    if case_kind == "ILLEGAL":
        if table_name == "run_output_objects":
            toggle.observe_tenant(graph["tenant_id"])
            _V11._insert_strict(
                connection,
                columns,
                table_name,
                _v12_output_root_values(
                    graph,
                    output_kind="METHODOLOGY_COVERAGE_IDENTITY",
                    identity_sha256="0" * 64,
                    output_sha256="0" * 64,
                ),
            )
        else:
            _V11._insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
        _commit_fixture_setup(connection, toggle)
        toggle.set_replica(connection, False)
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
        _V11._flush_deferred(connection)
        return "OUTPUT_EXACT_ONE_EVENT", {}

    output_kind = _V11._insert_native_output(
        connection,
        columns,
        table_name=native_table,
        graph=graph,
    )
    _V11._insert_strict(
        connection,
        columns,
        "run_output_objects",
        _v12_output_root_values(
            graph,
            output_kind=output_kind,
            identity_sha256="0" * 64,
            output_sha256="0" * 64,
        ),
    )
    _commit_fixture_setup(connection, toggle)
    identity_sha256, output_sha256 = _fixture_native_hashes_as_admin(
        database=database,
        toggle=toggle,
        authority_id=authority_id,
        table_name=table_name,
        guard_name=guard_name,
        case_kind=case_kind,
        graph=graph,
        output_kind=output_kind,
    )
    connection.execute(
        text(
            """
            UPDATE public.run_output_objects
            SET native_identity_sha256=:identity_sha256,
                native_output_sha256=:output_sha256
            WHERE output_object_id=CAST(:output_object_id AS uuid)
            """
        ),
        {
            "identity_sha256": identity_sha256,
            "output_sha256": output_sha256,
            "output_object_id": graph["output_object_id"],
        },
    )
    connection.commit()
    toggle.restore_tenant_context()

    if case_kind == "LEGAL":
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
        connection.commit()
        toggle.set_replica(connection, False)
        if table_name == "run_output_objects":
            _V11._insert_strict(
                connection,
                columns,
                table_name,
                _v12_output_root_values(
                    graph,
                    output_kind=output_kind,
                    identity_sha256=identity_sha256,
                    output_sha256=output_sha256,
                ),
            )
        else:
            _V11._insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
        _V11._flush_deferred(connection)
        return "OUTPUT_EXACT_ONE_EVENT", {}

    toggle.set_replica(connection, False)
    connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    _V11._flush_deferred(connection)
    return "OUTPUT_EXACT_ONE_EVENT", {}


def _truncate_case_data(
    connection: Connection,
    artifact: Mapping[str, Any],
) -> None:
    disable_all_current_user_triggers(connection, artifact)
    tables = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname='public' AND tablename<>'alembic_version' "
                "ORDER BY tablename"
            )
        ).scalars()
    )
    if tables:
        rendered = ",".join(f"public.{_identifier(name)}" for name in tables)
        connection.exec_driver_sql(f"TRUNCATE TABLE {rendered} CASCADE")


def _event_for_case(
    *,
    case_kind: CaseKind,
    authority_id: str,
    table_name: str,
    events: Sequence[TriggerEvent],
) -> TriggerEvent:
    if (
        authority_id == "current-task6-immutable-replacement-v1"
        and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    ):
        return cast(
            TriggerEvent,
            {"LEGAL": "UPDATE", "ILLEGAL": "INSERT", "DIRECT_SQL": "DELETE"}[case_kind],
        )
    if case_kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if case_kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def _expected_rejection(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> tuple[str, str]:
    if case_kind == "LEGAL":
        raise CensusError("TASK7_V12_CENSUS_REJECTION_CASE_INVALID")
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
        and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
        and case_kind == "ILLEGAL"
    ):
        return "40001", "WORKER_LEASE_FENCED"
    return "23514", "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE"


def _scenario_id(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> str:
    return str(
        _V11._scenario_id(
            authority_id=authority_id,
            table_name=table_name,
            case_kind=case_kind,
        )
    )


def _run_one_case_as_app(
    *,
    database: DisposablePostgres,
    artifact: Mapping[str, Any],
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_rejection = case_kind != "LEGAL"
    expected_scenario = _scenario_id(
        authority_id=authority_id,
        table_name=table_name,
        case_kind=case_kind,
    )
    with database.admin_engine.begin() as admin:
        enable_target_trigger(admin, table_name, guard_name)

    error: DBAPIError | None = None
    metrics: dict[str, Any] = {}
    original_insert = _V11._insert_strict
    original_replica = _V11._set_replica
    original_native_source_values = _V11._native_source_values
    original_unbound_source_values = _V11._unbound_source_values
    original_native_output_values = _V11._native_output_values
    native_output_discriminators = artifact_native_output_discriminators(artifact)
    with database.app_engine.connect() as app:
        case_actor = _observe_case_actor(
            app,
            expected_role=database.role_name,
        )
        toggle = _CommittedTriggerToggle(
            app_connection=app,
            admin_engine=database.admin_engine,
            table_name=table_name,
            trigger_name=guard_name,
        )

        def insert_with_tenant_context(
            connection: Connection,
            column_map: Mapping[str, Mapping[str, tuple[str, bool]]],
            target_table: str,
            values: Mapping[str, Any],
            *,
            returning: Sequence[str] = (),
        ) -> Mapping[str, Any] | None:
            if values.get("tenant_id") is not None:
                toggle.observe_tenant(values["tenant_id"])
            return cast(
                Mapping[str, Any] | None,
                original_insert(
                    connection,
                    column_map,
                    target_table,
                    values,
                    returning=returning,
                ),
            )

        _V11._insert_strict = insert_with_tenant_context
        _V11._set_replica = toggle.set_replica
        _V11._native_source_values = corrected_native_source_values
        _V11._unbound_source_values = corrected_unbound_source_values
        _V11._native_output_values = lambda *, table_name, graph: corrected_native_output_values(
            table_name=table_name,
            graph=graph,
            discriminator_by_table=native_output_discriminators,
        )
        try:
            try:
                if authority_id == "current-output-exact-one-v1":
                    scenario, metrics = _output_exact_one_case_as_app(
                        database=database,
                        connection=app,
                        columns=columns,
                        toggle=toggle,
                        authority_id=authority_id,
                        table_name=table_name,
                        guard_name=guard_name,
                        case_kind=case_kind,
                        trigger_event=trigger_event,
                    )
                else:
                    scenario, metrics = _V11._execute_case_operation(
                        app,
                        columns,
                        authority_id=authority_id,
                        table_name=table_name,
                        case_kind=case_kind,
                        trigger_event=trigger_event,
                    )
                if scenario != expected_scenario:
                    raise CensusError(f"TASK7_V12_CENSUS_SCENARIO_DRIFT:{expected_scenario}")
            except DBAPIError as exc:
                error = exc
        finally:
            _V11._insert_strict = original_insert
            _V11._set_replica = original_replica
            _V11._native_source_values = original_native_source_values
            _V11._unbound_source_values = original_unbound_source_values
            _V11._native_output_values = original_native_output_values
            if app.in_transaction():
                app.rollback()
        metrics["admin_fixture_helper_projection_tuple"] = tuple(toggle.fixture_helper_projections)

    with database.admin_engine.begin() as admin:
        _truncate_case_data(admin, artifact)

    timing = (
        "AFTER"
        if authority_id
        in {
            "current-source-exact-one-v1",
            "current-output-exact-one-v1",
            "current-receipt-equality-v1",
        }
        else "BEFORE"
    )
    base = {
        "case_kind": case_kind,
        "authority_id": authority_id,
        "table_name": table_name,
        "guard_name": guard_name,
        "trigger_event": trigger_event,
        "trigger_timing": timing,
        "constraint_flush_attempted": timing == "AFTER",
        "scenario_id": expected_scenario,
        "execution_path": ("DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION"),
        "executed_as_role": case_actor["current_user"],
        "observed_session_user": case_actor["session_user"],
        "observed_is_superuser": case_actor["is_superuser"],
        "observed_bypass_rls": case_actor["bypass_rls"],
    }
    if expected_rejection:
        if error is None:
            raise CensusError(
                "TASK7_V12_CENSUS_ILLEGAL_CASE_ACCEPTED:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            )
        sqlstate = _sqlstate(error)
        if sqlstate == "42501":
            raise CensusError(
                "TASK7_V12_CENSUS_PRIVILEGE_MASKING_FORBIDDEN:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            ) from error
        return (
            {
                **base,
                "outcome": "REJECTED",
                "sqlstate": sqlstate,
                "authority_rejection_marker": _rejection_marker(
                    error,
                    authority_id,
                ),
                "rejection_provenance": "HASH_BOUND_AUTHORITY",
            },
            {},
        )
    if error is not None:
        sqlstate = _sqlstate(error)
        raise CensusError(
            "TASK7_V12_CENSUS_LEGAL_CASE_REJECTED:"
            f"{authority_id}:{table_name}:{guard_name}:SQLSTATE={sqlstate}"
        ) from error
    return (
        {
            **base,
            "outcome": "ACCEPTED",
            "sqlstate": None,
            "authority_rejection_marker": None,
            "rejection_provenance": None,
        },
        metrics,
    )


def _run_constructors_as_app(
    database: DisposablePostgres,
    artifact: Mapping[str, Any],
    specs: Sequence[Sequence[str]],
) -> tuple[dict[str, Any], ...]:
    from tests.slice100_task7 import (
        test_full_strict_v9_native_schema_red as native,
    )

    tenant_id = str(native._vep_payload()["tenant_id"])
    with database.app_engine.connect() as app:
        transaction = app.begin()
        try:
            _set_tenant_context(app, tenant_id)
            result = tuple(
                _V11._ENGINE._run_constructor_roundtrips(
                    app,
                    artifact,
                    specs,
                )
            )
        finally:
            transaction.rollback()
    return cast(tuple[dict[str, Any], ...], result)


def _rls_context_proof(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> dict[str, Any]:
    graph = _V11._seed_run(connection, columns, status="RUNNING")
    tenant_id = str(graph["tenant_id"])
    cross_tenant = str(uuid.uuid4())
    connection.exec_driver_sql(f"SET LOCAL ROLE {GUARD_OWNER_ROLE}")
    try:
        _set_tenant_context(connection, tenant_id)
        same = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
        _set_tenant_context(connection, cross_tenant)
        cross = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
        connection.execute(text("SELECT pg_catalog.set_config('idis.tenant_id','',true)"))
        unset = int(
            connection.execute(
                text("SELECT count(run_id) FROM public.runs WHERE run_id=CAST(:run_id AS uuid)"),
                {"run_id": graph["run_id"]},
            ).scalar_one()
        )
    finally:
        connection.exec_driver_sql("RESET ROLE")
    if (same, cross, unset) != (1, 0, 0):
        raise CensusError("TASK7_V12_GUARD_OWNER_RLS_CONTEXT_INVALID")
    return {
        "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
        "executed_as_role": GUARD_OWNER_ROLE,
        "same_tenant_guard_acceptance_count": same,
        "cross_tenant_guard_denial_count": 1 if cross == 0 else 0,
        "unset_tenant_guard_denial_count": 1 if unset == 0 else 0,
    }


def _external_erasure_helper_proof(
    connection: Connection,
    *,
    application_role: str,
) -> dict[str, Any]:
    signature = "public.idis_task7_approved_erasure_context_v1(uuid,uuid)"
    guard_execute = bool(
        connection.execute(
            text(
                "SELECT pg_catalog.has_function_privilege("
                ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
            ),
            {"role_name": GUARD_OWNER_ROLE, "signature": signature},
        ).scalar_one()
    )
    app_execute = bool(
        connection.execute(
            text(
                "SELECT pg_catalog.has_function_privilege("
                ":role_name,pg_catalog.to_regprocedure(:signature),'EXECUTE')"
            ),
            {"role_name": application_role, "signature": signature},
        ).scalar_one()
    )
    public_execute = bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_proc p
                  CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                  ) acl
                  WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                    AND acl.grantee=0 AND acl.privilege_type='EXECUTE'
                )
                """
            ),
            {"signature": signature},
        ).scalar_one()
    )
    raw_execute_acl = tuple(
        {
            "grantee": str(row.grantee),
            "grantor": str(row.grantor),
            "privilege_type": str(row.privilege_type),
            "is_grantable": bool(row.is_grantable),
        }
        for row in connection.execute(
            text(
                """
                SELECT grantee.rolname AS grantee,
                       grantor.rolname AS grantor,
                       acl.privilege_type,acl.is_grantable
                FROM pg_catalog.pg_proc p
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
                ) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee
                JOIN pg_catalog.pg_roles grantor ON grantor.oid=acl.grantor
                WHERE p.oid=pg_catalog.to_regprocedure(:signature)
                ORDER BY grantee.rolname,acl.privilege_type,grantor.rolname
                """
            ),
            {"signature": signature},
        )
    )
    function_owner = str(
        connection.execute(
            text(
                "SELECT owner.rolname FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner "
                "WHERE p.oid=pg_catalog.to_regprocedure(:signature)"
            ),
            {"signature": signature},
        ).scalar_one()
    )
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    connection.exec_driver_sql(f"SET LOCAL ROLE {GUARD_OWNER_ROLE}")
    try:
        _set_tenant_context(connection, tenant_id)
        ordinary = bool(
            connection.execute(
                text(
                    "SELECT public.idis_task7_approved_erasure_context_v1("
                    "CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid))"
                ),
                {"tenant_id": tenant_id, "deal_id": deal_id},
            ).scalar_one()
        )
    finally:
        connection.exec_driver_sql("RESET ROLE")
    proof = {
        "function_signature": signature,
        "guard_owner_execute": guard_execute,
        "application_direct_execute": app_execute,
        "public_direct_execute": public_execute,
        "ordinary_guard_call_result": ordinary,
        "function_owner": function_owner,
        "raw_execute_acl_tuple": list(raw_execute_acl),
    }
    if (
        {key: proof[key] for key in proof if key != "raw_execute_acl_tuple"}
        != {
            "function_signature": signature,
            "guard_owner_execute": True,
            "application_direct_execute": False,
            "public_direct_execute": False,
            "ordinary_guard_call_result": False,
            "function_owner": function_owner,
        }
        or len(raw_execute_acl) != 2
        or {
            (
                str(row["grantee"]),
                str(row["grantor"]),
                str(row["privilege_type"]),
                bool(row["is_grantable"]),
            )
            for row in raw_execute_acl
        }
        != {
            (
                function_owner,
                function_owner,
                "EXECUTE",
                False,
            ),
            (
                GUARD_OWNER_ROLE,
                function_owner,
                "EXECUTE",
                False,
            ),
        }
    ):
        raise CensusError("TASK7_V12_EXTERNAL_ERASURE_HELPER_ACL_INVALID")
    return proof


def _grant_external_erasure_helper_read_closure(connection: Connection) -> None:
    """Install only the existing erasure helper's reviewed 0037 dependency."""

    connection.exec_driver_sql(
        "REVOKE ALL PRIVILEGES ON TABLE public.erasure_requests FROM idis_erasure_function_owner"
    )
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner")
    connection.exec_driver_sql(
        "GRANT SELECT (request_id,tenant_id,deal_id,status) "
        "ON TABLE public.erasure_requests TO idis_erasure_function_owner"
    )


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    ordered_trigger_contract_coordinate_tuple: Sequence[Sequence[Any]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Run 73×3 app-role guard cases plus four app-role constructors."""

    _ensure_admin_url()
    artifact_raw, artifact = _decode_artifact(Path(artifact_path))
    targets, trigger_contract = _validate_request(
        artifact=artifact,
        base_schema_revision=base_schema_revision,
        harness_identity=harness_identity,
        ordered_authority_target_tuple=ordered_authority_target_tuple,
        ordered_trigger_contract_coordinate_tuple=(ordered_trigger_contract_coordinate_tuple),
        production_constructor_spec_tuple=production_constructor_spec_tuple,
    )
    prospective_raw = cast(
        bytes,
        _GENERATOR.derive_prospective_frozen_bytes(artifact_raw),
    )
    outcomes: list[dict[str, Any]] = []
    aggregate_metrics: dict[str, int] = {}
    admin_fixture_helper_projections: list[dict[str, Any]] = []

    with disposable_postgres() as database:
        migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        grant_application_access(database)
        constructors = _run_constructors_as_app(
            database,
            artifact,
            production_constructor_spec_tuple,
        )
        with database.admin_engine.begin() as admin:
            provision_exact_erasure_principals(admin)
            revision_before = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
            server_version_num = int(
                admin.execute(
                    text("SELECT current_setting('server_version_num')::integer")
                ).scalar_one()
            )
            install_artifact_sql_scaffold(
                admin,
                artifact,
                application_role=database.role_name,
            )
            _grant_external_erasure_helper_read_closure(admin)
            revision_after_install = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
            projection = catalog_projection(admin, artifact)
            trigger_projection = _live_trigger_projection(
                admin,
                trigger_contract,
                targets,
            )
            routine_catalog = _current_routine_catalog(
                admin,
                artifact,
                application_role=database.role_name,
            )
            attribute_acl = _guard_owner_raw_attribute_acl(admin, artifact)
            schema_acl = _guard_owner_raw_schema_acl(admin)
            guard_posture, force_rls_count = _guard_owner_posture(
                admin,
                artifact,
            )
            external_helper = _external_erasure_helper_proof(
                admin,
                application_role=database.role_name,
            )
            disable_all_current_user_triggers(admin, artifact)
            relation_names = tuple(
                dict.fromkeys(
                    [
                        *(
                            str(row["table_name"])
                            for row in artifact["current_ddl_registry"]["rows"]
                        ),
                        "run_methodology_selections",
                        "methodology_questions",
                    ]
                )
            )
            _V11._ENGINE._drop_non_guard_constraints(
                admin,
                relation_names=relation_names,
            )
            _V11._ENGINE._disable_target_relations(admin, targets)
            columns = cast(
                dict[str, dict[str, tuple[str, bool]]],
                _V11._ENGINE._column_inventory(admin),
            )
            fixture_rejections = _fixture_rejection_proofs(columns)

        event_map: dict[
            tuple[str, str, str],
            tuple[TriggerEvent, ...],
        ] = {}
        for authority, table_name, guard_name, _timing, event, *_tail in trigger_contract:
            key = (str(authority), str(table_name), str(guard_name))
            event_map.setdefault(key, ())
            event_map[key] = (
                *event_map[key],
                cast(TriggerEvent, str(event)),
            )

        for case_kind_raw in _CASE_KINDS:
            case_kind = cast(CaseKind, case_kind_raw)
            for authority_id, table_name, guard_name in targets:
                trigger_event = _event_for_case(
                    case_kind=case_kind,
                    authority_id=authority_id,
                    table_name=table_name,
                    events=event_map[(authority_id, table_name, guard_name)],
                )
                outcome, metrics = _run_one_case_as_app(
                    database=database,
                    artifact=artifact,
                    columns=columns,
                    authority_id=authority_id,
                    table_name=table_name,
                    guard_name=guard_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                )
                outcomes.append(outcome)
                for key, value in metrics.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        aggregate_metrics[key] = aggregate_metrics.get(key, 0) + value
                    elif key == "admin_fixture_helper_projection_tuple":
                        admin_fixture_helper_projections.extend(
                            cast(Sequence[dict[str, Any]], value)
                        )

        with database.admin_engine.begin() as admin:
            _truncate_case_data(admin, artifact)
            rls_context = _rls_context_proof(admin, columns)
            revision_after_cases = str(
                admin.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )

    legal_count = sum(
        row["case_kind"] == "LEGAL" and row["outcome"] == "ACCEPTED" for row in outcomes
    )
    illegal_count = sum(
        row["case_kind"] == "ILLEGAL" and row["outcome"] == "REJECTED" for row in outcomes
    )
    direct_count = sum(
        row["case_kind"] == "DIRECT_SQL" and row["outcome"] == "REJECTED" for row in outcomes
    )
    unexpected = len(outcomes) - legal_count - illegal_count - direct_count
    observed_roles = {
        (
            str(row["observed_session_user"]),
            str(row["executed_as_role"]),
            bool(row["observed_is_superuser"]),
            bool(row["observed_bypass_rls"]),
        )
        for row in outcomes
    }
    if observed_roles != {(database.role_name, database.role_name, False, False)}:
        raise CensusError("TASK7_V12_CASE_APPLICATION_ROLE_PROJECTION_INVALID")
    application_case_count = sum(
        row["observed_session_user"] == database.role_name
        and row["executed_as_role"] == database.role_name
        for row in outcomes
    )
    admin_case_count = len(outcomes) - application_case_count
    if (application_case_count, admin_case_count) != (219, 0):
        raise CensusError("TASK7_V12_CASE_ACTOR_COUNT_INVALID")
    actor = {
        "role_name": database.role_name,
        "session_user": database.role_name,
        "is_superuser": False,
        "bypass_rls": False,
        "case_execution_count": application_case_count,
    }
    observed_event_keys = {
        (
            str(row["authority_id"]),
            str(row["table_name"]),
            str(row["guard_name"]),
            str(row["trigger_event"]),
        )
        for row in outcomes
    }
    observed_trigger_contract = tuple(
        row
        for row in trigger_contract
        if (str(row[0]), str(row[1]), str(row[2]), str(row[4])) in observed_event_keys
    )
    if len(observed_trigger_contract) != 192 or set(observed_trigger_contract) != set(
        trigger_contract
    ):
        raise CensusError("TASK7_V12_CENSUS_TRIGGER_EVENT_COVERAGE_INCOMPLETE")
    case_target_projection = tuple(
        dict.fromkeys(
            (
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
            )
            for row in outcomes
        )
    )
    live_target_projection = tuple(tuple(map(str, row[:3])) for row in trigger_projection)
    artifact_target_projection = _artifact_targets(artifact)
    manifest_parity_projection = {
        "artifact_authority_target_tuple": [list(row) for row in artifact_target_projection],
        "requested_authority_target_tuple": [list(row) for row in targets],
        "live_catalog_authority_target_tuple": [list(row) for row in live_target_projection],
        "measured_case_authority_target_tuple": [list(row) for row in case_target_projection],
    }
    if not (
        artifact_target_projection == targets == live_target_projection == case_target_projection
    ):
        raise CensusError("TASK7_V12_CENSUS_MANIFEST_PARITY_INVALID")
    if (revision_before, revision_after_install, revision_after_cases) != (
        BASE_SCHEMA_REVISION,
    ) * 3 or (legal_count, illegal_count, direct_count, unexpected) != (73, 73, 73, 0):
        raise CensusError("TASK7_V12_CENSUS_EXECUTION_INCOMPLETE")

    catalog_projection_json = _json_safe(projection)
    trigger_projection_json = _json_safe(trigger_projection)
    outcomes_json = _json_safe(outcomes)
    constructors_json = _json_safe(constructors)
    routine_catalog_json = _json_safe(routine_catalog)
    attribute_acl_json = _json_safe(attribute_acl)
    schema_acl_json = _json_safe(schema_acl)
    external_helper_json = _json_safe(external_helper)
    rls_context_json = _json_safe(rls_context)
    helper_projection_json = _json_safe(admin_fixture_helper_projections)
    trigger_event_coverage_json = [
        list(row)
        for row in sorted(
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

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "harness_identity": HARNESS_IDENTITY,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "alembic_version_before_install": revision_before,
        "alembic_version_after_install": revision_after_install,
        "alembic_version_after_cases": revision_after_cases,
        "installation_mode": INSTALLATION_MODE,
        "database_product": "PostgreSQL",
        "server_version_num": server_version_num,
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v12",
        "candidate_append_frozen": False,
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": hashlib.sha256(prospective_raw).hexdigest(),
        "ordered_authority_target_tuple": [list(row) for row in targets],
        "trigger_contract_coordinate_tuple": [list(row) for row in trigger_contract],
        "observed_trigger_contract_coordinate_tuple": [
            list(row) for row in observed_trigger_contract
        ],
        "catalog_projection_tuple": catalog_projection_json,
        "pg_trigger_projection_tuple": trigger_projection_json,
        "case_outcome_tuple": outcomes_json,
        "constructor_proof_tuple": constructors_json,
        "fixture_rejection_proof_tuple": [list(row) for row in fixture_rejections],
        "application_role_name": actor["role_name"],
        "database_actor": actor,
        "guard_owner_posture": guard_posture,
        "current_routine_catalog_tuple": routine_catalog_json,
        "guard_owner_raw_attribute_acl_tuple": attribute_acl_json,
        "guard_owner_raw_schema_acl_tuple": schema_acl_json,
        "external_erasure_helper_proof": external_helper_json,
        "admin_fixture_helper_projection_tuple": helper_projection_json,
        "security_definer_guard_owner_rls_context_proof": rls_context_json,
        "manifest_parity_projection": manifest_parity_projection,
        "current_guard_target_count": len(targets),
        "current_guard_target_event_count": len(trigger_contract),
        "case_outcome_count": len(outcomes),
        "legal_execution_count": legal_count,
        "illegal_rejection_count": illegal_count,
        "direct_sql_rejection_count": direct_count,
        "application_case_execution_count": application_case_count,
        "admin_case_execution_count": admin_case_count,
        "admin_fixture_helper_call_count": len(admin_fixture_helper_projections),
        "constructor_schema_proof_count": len(constructors),
        "unexpected_outcome_count": unexpected,
        "current_routine_catalog_count": len(routine_catalog),
        "guard_owner_column_acl_relation_count": len(
            {str(row["relation"]) for row in attribute_acl}
        ),
        "guard_owner_column_acl_count": len(attribute_acl),
        "application_direct_helper_execute_count": sum(
            bool(row["application_direct_execute"]) for row in routine_catalog
        )
        + int(external_helper["application_direct_execute"]),
        "public_direct_helper_execute_count": sum(
            bool(row["public_direct_execute"]) for row in routine_catalog
        )
        + int(external_helper["public_direct_execute"]),
        "force_rls_parent_relation_count": force_rls_count,
        "task6_output_root_update_acceptance_count": aggregate_metrics.get(
            "task6_output_root_update_acceptance_count",
            0,
        ),
        "strict_input_binding_queued_admission_count": aggregate_metrics.get(
            "strict_input_binding_queued_admission_count",
            0,
        ),
        "expected_edge_queued_admission_count": aggregate_metrics.get(
            "expected_edge_queued_admission_count",
            0,
        ),
        "package_open_to_sealed_binding_count": aggregate_metrics.get(
            "package_open_to_sealed_binding_count",
            0,
        ),
        "methodology_commercial_question_count": aggregate_metrics.get(
            "methodology_commercial_question_count",
            0,
        ),
        "methodology_financial_question_count": aggregate_metrics.get(
            "methodology_financial_question_count",
            0,
        ),
        "methodology_coverage_count": aggregate_metrics.get(
            "methodology_coverage_count",
            0,
        ),
        "catalog_projection_sha256": _digest(
            ["task7-v12-census-catalog-projection-v1", catalog_projection_json]
        ),
        "case_result_sha256": _digest(["task7-v12-current-guard-case-results-v1", outcomes_json]),
        "constructor_roundtrip_sha256": _digest(
            ["task7-v12-constructor-roundtrips-v1", constructors_json]
        ),
        "admin_fixture_helper_projection_sha256": _digest(
            [
                "task7-v12-admin-fixture-helper-projection-v1",
                helper_projection_json,
            ]
        ),
        "manifest_parity_sha256": _digest(
            [
                "task7-v12-manifest-parity-v1",
                manifest_parity_projection,
            ]
        ),
        "pg_trigger_inventory_sha256": _digest(
            ["task7-v12-pg-trigger-projection-v1", trigger_projection_json]
        ),
        "trigger_event_coverage_sha256": _digest(
            [
                "task7-v12-trigger-event-coverage-v1",
                trigger_event_coverage_json,
            ]
        ),
        "guard_acl_projection_sha256": _digest(
            [
                "task7-v12-guard-acl-projection-v1",
                routine_catalog_json,
                attribute_acl_json,
                schema_acl_json,
                guard_posture,
                external_helper_json,
            ]
        ),
        "rls_context_proof_sha256": _digest(
            [
                "task7-v12-security-definer-guard-owner-rls-context-proof-v1",
                rls_context_json,
            ]
        ),
    }
    result["complete_execution_sha256"] = _digest(["task7-v12-complete-execution-v1", result])
    if "v11" in json.dumps(result, sort_keys=True).lower():
        raise CensusError("TASK7_V12_CENSUS_RESULT_PROVENANCE_INVALID")
    return result


def _database_execution(result: Mapping[str, Any]) -> dict[str, Any]:
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


def _validate_database_execution(execution: Mapping[str, Any]) -> None:
    required = {
        "application_case_execution_count": 219,
        "admin_case_execution_count": 0,
        "admin_fixture_helper_call_count": 36,
        "current_guard_target_count": 73,
        "current_guard_target_event_count": 192,
        "case_outcome_count": 219,
        "legal_execution_count": 73,
        "illegal_rejection_count": 73,
        "direct_sql_rejection_count": 73,
        "constructor_schema_proof_count": 4,
        "current_routine_catalog_count": 19,
        "guard_owner_column_acl_relation_count": 36,
        "guard_owner_column_acl_count": 366,
        "application_direct_helper_execute_count": 0,
        "public_direct_helper_execute_count": 0,
        "unexpected_outcome_count": 0,
    }
    if any(execution.get(key) != value for key, value in required.items()):
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
    for key in execution:
        if key.endswith("_sha256") and (
            not isinstance(execution[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", execution[key]) is None
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")


def _validate_complete_execution_result(result: Mapping[str, Any]) -> None:
    supplied = result.get("complete_execution_sha256")
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
    expected = _digest(
        [
            "task7-v12-complete-execution-v1",
            {key: value for key, value in result.items() if key != "complete_execution_sha256"},
        ]
    )
    if supplied != expected:
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")


_CLOSED_RESULT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "harness_identity",
        "base_schema_revision",
        "alembic_version_before_install",
        "alembic_version_after_install",
        "alembic_version_after_cases",
        "installation_mode",
        "database_product",
        "server_version_num",
        "artifact_schema",
        "pipeline_contract_version",
        "candidate_append_frozen",
        "candidate_artifact_sha256",
        "prospective_frozen_artifact_sha256",
        "ordered_authority_target_tuple",
        "trigger_contract_coordinate_tuple",
        "observed_trigger_contract_coordinate_tuple",
        "catalog_projection_tuple",
        "pg_trigger_projection_tuple",
        "case_outcome_tuple",
        "constructor_proof_tuple",
        "fixture_rejection_proof_tuple",
        "application_role_name",
        "database_actor",
        "guard_owner_posture",
        "current_routine_catalog_tuple",
        "guard_owner_raw_attribute_acl_tuple",
        "guard_owner_raw_schema_acl_tuple",
        "external_erasure_helper_proof",
        "admin_fixture_helper_projection_tuple",
        "security_definer_guard_owner_rls_context_proof",
        "manifest_parity_projection",
        "current_guard_target_count",
        "current_guard_target_event_count",
        "case_outcome_count",
        "legal_execution_count",
        "illegal_rejection_count",
        "direct_sql_rejection_count",
        "application_case_execution_count",
        "admin_case_execution_count",
        "admin_fixture_helper_call_count",
        "constructor_schema_proof_count",
        "unexpected_outcome_count",
        "current_routine_catalog_count",
        "guard_owner_column_acl_relation_count",
        "guard_owner_column_acl_count",
        "application_direct_helper_execute_count",
        "public_direct_helper_execute_count",
        "force_rls_parent_relation_count",
        "task6_output_root_update_acceptance_count",
        "strict_input_binding_queued_admission_count",
        "expected_edge_queued_admission_count",
        "package_open_to_sealed_binding_count",
        "methodology_commercial_question_count",
        "methodology_financial_question_count",
        "methodology_coverage_count",
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
    }
)


def _validate_complete_census_result(
    result: Mapping[str, Any],
    *,
    artifact_raw: bytes,
) -> None:
    """Re-derive the closed evidence graph before any signature is accepted."""

    try:
        if frozenset(result) != _CLOSED_RESULT_FIELDS:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if (
            result["schema_version"] != RESULT_SCHEMA
            or result["harness_identity"] != HARNESS_IDENTITY
            or result["base_schema_revision"] != BASE_SCHEMA_REVISION
            or result["alembic_version_before_install"] != BASE_SCHEMA_REVISION
            or result["alembic_version_after_install"] != BASE_SCHEMA_REVISION
            or result["alembic_version_after_cases"] != BASE_SCHEMA_REVISION
            or result["installation_mode"] != INSTALLATION_MODE
            or result["database_product"] != "PostgreSQL"
            or not isinstance(result["server_version_num"], int)
            or int(result["server_version_num"]) < 160000
            or result["artifact_schema"] != "full-strict-v12-reviewed-preimage-v1"
            or result["pipeline_contract_version"] != "full-strict-v12"
            or result["candidate_append_frozen"] is not False
            or result["candidate_artifact_sha256"] != hashlib.sha256(artifact_raw).hexdigest()
            or hashlib.sha512(artifact_raw).hexdigest() != _EXPECTED_ARTIFACT_SHA512
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        artifact = cast(dict[str, Any], _GENERATOR._decode_artifact(artifact_raw))
        _GENERATOR._validate_shape(artifact_raw, artifact)
        expected_trigger_contract = _artifact_trigger_contract(artifact)
        expected_targets = _artifact_targets(artifact)
        prospective = cast(
            bytes,
            _GENERATOR.derive_prospective_frozen_bytes(artifact_raw),
        )
        if result["prospective_frozen_artifact_sha256"] != hashlib.sha256(prospective).hexdigest():
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        outcomes = cast(Sequence[Mapping[str, Any]], result["case_outcome_tuple"])
        constructors = cast(Sequence[Mapping[str, Any]], result["constructor_proof_tuple"])
        helper_projection = cast(
            Sequence[Mapping[str, Any]],
            result["admin_fixture_helper_projection_tuple"],
        )
        routine_catalog = cast(
            Sequence[Mapping[str, Any]],
            result["current_routine_catalog_tuple"],
        )
        attribute_acl = cast(
            Sequence[Mapping[str, Any]],
            result["guard_owner_raw_attribute_acl_tuple"],
        )
        schema_acl = cast(
            Sequence[Mapping[str, Any]],
            result["guard_owner_raw_schema_acl_tuple"],
        )
        trigger_contract = tuple(
            tuple(row)
            for row in cast(Sequence[Sequence[Any]], result["trigger_contract_coordinate_tuple"])
        )
        observed_contract = tuple(
            tuple(row)
            for row in cast(
                Sequence[Sequence[Any]],
                result["observed_trigger_contract_coordinate_tuple"],
            )
        )
        targets = tuple(
            tuple(map(str, row))
            for row in cast(
                Sequence[Sequence[Any]],
                result["ordered_authority_target_tuple"],
            )
        )
        live_trigger_projection = tuple(
            tuple(row)
            for row in cast(
                Sequence[Sequence[Any]],
                result["pg_trigger_projection_tuple"],
            )
        )
        expected_live_trigger_projection = _expected_trigger_projection(
            expected_trigger_contract,
            expected_targets,
        )
        if (
            len(outcomes) != 219
            or len(constructors) != 4
            or len(helper_projection) != 36
            or len(routine_catalog) != 19
            or len(attribute_acl) != 366
            or len(schema_acl) != 1
            or len(trigger_contract) != 192
            or len(set(trigger_contract)) != 192
            or set(observed_contract) != set(trigger_contract)
            or len(observed_contract) != 192
            or len(targets) != 73
            or trigger_contract != expected_trigger_contract
            or targets != expected_targets
            or live_trigger_projection != expected_live_trigger_projection
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        role_name = str(result["application_role_name"])
        event_map: dict[tuple[str, str, str], tuple[TriggerEvent, ...]] = {}
        for authority, table_name, guard_name, _timing, event, *_tail in expected_trigger_contract:
            key = (str(authority), str(table_name), str(guard_name))
            event_map.setdefault(key, ())
            event_map[key] = (*event_map[key], cast(TriggerEvent, str(event)))
        expected_case_coordinates = tuple(
            (
                case_kind,
                authority_id,
                table_name,
                guard_name,
                _event_for_case(
                    case_kind=case_kind,
                    authority_id=authority_id,
                    table_name=table_name,
                    events=event_map[(authority_id, table_name, guard_name)],
                ),
            )
            for case_kind in cast(tuple[CaseKind, ...], _CASE_KINDS)
            for authority_id, table_name, guard_name in expected_targets
        )
        actual_case_coordinates = tuple(
            (
                str(row["case_kind"]),
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
                str(row["trigger_event"]),
            )
            for row in outcomes
        )
        if actual_case_coordinates != expected_case_coordinates:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        expected_outcome_fields = {
            "case_kind",
            "authority_id",
            "table_name",
            "guard_name",
            "trigger_event",
            "trigger_timing",
            "constraint_flush_attempted",
            "scenario_id",
            "execution_path",
            "executed_as_role",
            "observed_session_user",
            "observed_is_superuser",
            "observed_bypass_rls",
            "outcome",
            "sqlstate",
            "authority_rejection_marker",
            "rejection_provenance",
        }
        for row in outcomes:
            if set(row) != expected_outcome_fields:
                raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
            case_kind = cast(CaseKind, str(row["case_kind"]))
            authority_id = str(row["authority_id"])
            table_name = str(row["table_name"])
            guard_name = str(row["guard_name"])
            trigger_event = str(row["trigger_event"])
            coordinate = tuple(
                value
                for value in expected_trigger_contract
                if (
                    str(value[0]),
                    str(value[1]),
                    str(value[2]),
                    str(value[4]),
                )
                == (authority_id, table_name, guard_name, trigger_event)
            )
            if len(coordinate) != 1:
                raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
            timing = str(coordinate[0][3])
            if (
                row["trigger_timing"] != timing
                or row["constraint_flush_attempted"] is not (timing == "AFTER")
                or row["scenario_id"]
                != _scenario_id(
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
                or row["execution_path"]
                != ("DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION")
            ):
                raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
            if case_kind == "LEGAL":
                if (
                    row["outcome"] != "ACCEPTED"
                    or row["sqlstate"] is not None
                    or row["authority_rejection_marker"] is not None
                    or row["rejection_provenance"] is not None
                ):
                    raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
            else:
                expected_sqlstate, expected_marker = _expected_rejection(
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
                if (
                    row["outcome"] != "REJECTED"
                    or row["sqlstate"] != expected_sqlstate
                    or row["sqlstate"] == "42501"
                    or row["authority_rejection_marker"] != expected_marker
                    or row["rejection_provenance"] != "HASH_BOUND_AUTHORITY"
                ):
                    raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if any(
            row.get("executed_as_role") != role_name
            or row.get("observed_session_user") != role_name
            or row.get("observed_is_superuser") is not False
            or row.get("observed_bypass_rls") is not False
            for row in outcomes
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if result["database_actor"] != {
            "role_name": role_name,
            "session_user": role_name,
            "is_superuser": False,
            "bypass_rls": False,
            "case_execution_count": len(outcomes),
        }:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        expected_functions = current_function_rows(artifact)
        if tuple(str(row["function_signature"]) for row in routine_catalog) != tuple(
            str(row["function_signature"]) for row in expected_functions
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        for actual, expected in zip(routine_catalog, expected_functions, strict=True):
            raw_acl = cast(Sequence[Mapping[str, Any]], actual["raw_execute_acl_tuple"])
            if (
                set(actual)
                != {
                    "function_signature",
                    "function_kind",
                    "owner",
                    "security_definer",
                    "search_path",
                    "application_direct_execute",
                    "public_direct_execute",
                    "raw_execute_acl_tuple",
                }
                or actual["function_kind"] != expected["function_kind"]
                or actual["owner"] != GUARD_OWNER_ROLE
                or actual["security_definer"] != (expected["security_mode"] == "DEFINER")
                or actual["search_path"] != "pg_catalog, pg_temp"
                or actual["application_direct_execute"] is not False
                or actual["public_direct_execute"] is not False
                or len(raw_acl) != 1
                or raw_acl[0]
                != {
                    "grantee": GUARD_OWNER_ROLE,
                    "grantor": GUARD_OWNER_ROLE,
                    "privilege_type": "EXECUTE",
                    "is_grantable": False,
                }
            ):
                raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        expected_column_acl = {
            (str(relation), str(column))
            for relation, columns in artifact["sql_authority_registry"][
                "guard_function_acl_registry"
            ]["guard_owner_role"]["select_relation_column_tuple"]
            for column in columns
        }
        actual_column_acl = {(str(row["relation"]), str(row["column"])) for row in attribute_acl}
        if (
            actual_column_acl != expected_column_acl
            or len(actual_column_acl) != len(attribute_acl)
            or any(
                set(row)
                != {
                    "relation",
                    "column",
                    "privilege_type",
                    "is_grantable",
                    "raw_attacl",
                }
                or row["privilege_type"] != "SELECT"
                or row["is_grantable"] is not False
                or not isinstance(row["raw_attacl"], str)
                or not row["raw_attacl"]
                for row in attribute_acl
            )
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if (
            len(schema_acl) != 1
            or {
                key: schema_acl[0][key]
                for key in ("schema_name", "grantee", "privilege_type", "is_grantable")
            }
            != {
                "schema_name": "public",
                "grantee": GUARD_OWNER_ROLE,
                "privilege_type": "USAGE",
                "is_grantable": False,
            }
            or set(schema_acl[0])
            != {
                "schema_name",
                "grantee",
                "grantor",
                "privilege_type",
                "is_grantable",
            }
            or not isinstance(schema_acl[0]["grantor"], str)
            or not schema_acl[0]["grantor"]
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        expected_posture = {
            "role_name": GUARD_OWNER_ROLE,
            "can_login": False,
            "is_superuser": False,
            "can_create_db": False,
            "can_create_role": False,
            "inherits": False,
            "bypass_rls": False,
            "membership_count": 0,
            "owned_relation_count": 0,
            "owned_routine_count": len(expected_functions),
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
        if result["guard_owner_posture"] != expected_posture:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        external = cast(Mapping[str, Any], result["external_erasure_helper_proof"])
        external_owner = str(external["function_owner"])
        external_acl = cast(Sequence[Mapping[str, Any]], external["raw_execute_acl_tuple"])
        if (
            {key: external[key] for key in external if key != "raw_execute_acl_tuple"}
            != {
                "function_signature": ("public.idis_task7_approved_erasure_context_v1(uuid,uuid)"),
                "guard_owner_execute": True,
                "application_direct_execute": False,
                "public_direct_execute": False,
                "ordinary_guard_call_result": False,
                "function_owner": external_owner,
            }
            or len(external_acl) != 2
            or {
                (
                    row["grantee"],
                    row["grantor"],
                    row["privilege_type"],
                    row["is_grantable"],
                )
                for row in external_acl
            }
            != {
                (external_owner, external_owner, "EXECUTE", False),
                (GUARD_OWNER_ROLE, external_owner, "EXECUTE", False),
            }
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if result["security_definer_guard_owner_rls_context_proof"] != {
            "proof_scope": "SECURITY_DEFINER_GUARD_OWNER_FORCE_RLS",
            "executed_as_role": GUARD_OWNER_ROLE,
            "same_tenant_guard_acceptance_count": 1,
            "cross_tenant_guard_denial_count": 1,
            "unset_tenant_guard_denial_count": 1,
        }:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        constructor_coordinates = tuple(
            (
                proof["schema_id"],
                proof["schema_version"],
                proof["constructor_qualname"],
                proof["durable_table"],
            )
            for proof in constructors
        )
        if constructor_coordinates != _PRODUCTION_CONSTRUCTOR_SPECS or any(
            set(proof)
            != {
                "schema_id",
                "schema_version",
                "constructor_qualname",
                "durable_table",
                "outcome",
                "original_payload_sha256",
                "reloaded_payload_sha256",
            }
            or proof["outcome"] != "ROUNDTRIP_MATCH"
            or proof["original_payload_sha256"] != proof["reloaded_payload_sha256"]
            or re.fullmatch(r"[0-9a-f]{64}", str(proof["original_payload_sha256"])) is None
            for proof in constructors
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if result["fixture_rejection_proof_tuple"] != [
            [
                "UNKNOWN_FIELD",
                "runs",
                "unknown_fixture_field",
                "TASK7_V12_CENSUS_FIXTURE_UNKNOWN_FIELD:runs.unknown_fixture_field",
            ],
            [
                "OTHER_TARGET_FIELD",
                "runs",
                "step_id",
                "TASK7_V12_CENSUS_FIXTURE_OTHER_TARGET_FIELD:runs.step_id",
            ],
            [
                "GENERATED_FIELD",
                "methodology_extraction_results",
                "run_output_object_kind",
                "TASK7_V12_CENSUS_FIXTURE_GENERATED_FIELD:"
                "methodology_extraction_results.run_output_object_kind",
            ],
        ]:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        expected_helper_coordinates = tuple(
            (authority_id, table_name, guard_name, case_kind)
            for case_kind in ("LEGAL", "DIRECT_SQL")
            for authority_id, table_name, guard_name in expected_targets
            if authority_id == "current-output-exact-one-v1"
        )
        actual_helper_coordinates = tuple(
            (
                str(row["authority_id"]),
                str(row["table_name"]),
                str(row["guard_name"]),
                str(row["case_kind"]),
            )
            for row in helper_projection
        )
        if actual_helper_coordinates != expected_helper_coordinates or any(
            set(row)
            != {
                "authority_id",
                "table_name",
                "guard_name",
                "case_kind",
                "phase",
                "function_signature",
                "executed_as_role",
                "output_kind",
                "returned_pair_sha256",
            }
            or row["phase"] != "COMMITTED_FIXTURE_SETUP"
            or row["function_signature"]
            != "public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)"
            or not isinstance(row["executed_as_role"], str)
            or not row["executed_as_role"]
            or row["executed_as_role"] == role_name
            or not isinstance(row["output_kind"], str)
            or not row["output_kind"]
            or re.fullmatch(r"[0-9a-f]{64}", str(row["returned_pair_sha256"])) is None
            for row in helper_projection
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        catalog_projection = cast(
            Sequence[Sequence[Any]],
            result["catalog_projection_tuple"],
        )
        if not catalog_projection or any(
            not isinstance(row, list) or not row or any(not isinstance(value, str) for value in row)
            for row in catalog_projection
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        catalog_trigger_targets = {
            (str(row[1]), str(row[2]))
            for row in catalog_projection
            if len(row) >= 3 and row[0] == "TRIGGER"
        }
        if catalog_trigger_targets != {
            (table_name, guard_name) for _authority_id, table_name, guard_name in expected_targets
        }:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        legal_count = sum(
            row.get("case_kind") == "LEGAL" and row.get("outcome") == "ACCEPTED" for row in outcomes
        )
        illegal_count = sum(
            row.get("case_kind") == "ILLEGAL" and row.get("outcome") == "REJECTED"
            for row in outcomes
        )
        direct_count = sum(
            row.get("case_kind") == "DIRECT_SQL" and row.get("outcome") == "REJECTED"
            for row in outcomes
        )
        application_case_count = sum(
            row.get("observed_session_user") == role_name
            and row.get("executed_as_role") == role_name
            for row in outcomes
        )
        admin_case_count = len(outcomes) - application_case_count
        derived_counts = {
            "current_guard_target_count": len(targets),
            "current_guard_target_event_count": len(trigger_contract),
            "case_outcome_count": len(outcomes),
            "legal_execution_count": legal_count,
            "illegal_rejection_count": illegal_count,
            "direct_sql_rejection_count": direct_count,
            "application_case_execution_count": application_case_count,
            "admin_case_execution_count": admin_case_count,
            "admin_fixture_helper_call_count": len(helper_projection),
            "constructor_schema_proof_count": len(constructors),
            "unexpected_outcome_count": len(outcomes) - legal_count - illegal_count - direct_count,
            "current_routine_catalog_count": len(routine_catalog),
            "guard_owner_column_acl_relation_count": len(
                {str(row["relation"]) for row in attribute_acl}
            ),
            "guard_owner_column_acl_count": len(attribute_acl),
            "application_direct_helper_execute_count": sum(
                bool(row["application_direct_execute"]) for row in routine_catalog
            )
            + int(result["external_erasure_helper_proof"]["application_direct_execute"]),
            "public_direct_helper_execute_count": sum(
                bool(row["public_direct_execute"]) for row in routine_catalog
            )
            + int(result["external_erasure_helper_proof"]["public_direct_execute"]),
            "force_rls_parent_relation_count": len(
                {relation for relation, _column in expected_column_acl}
            ),
        }
        expected_metrics = {
            "task6_output_root_update_acceptance_count": 2,
            "strict_input_binding_queued_admission_count": 1,
            "expected_edge_queued_admission_count": 1,
            "package_open_to_sealed_binding_count": 1,
            "methodology_commercial_question_count": 21,
            "methodology_financial_question_count": 79,
            "methodology_coverage_count": 100,
        }
        if any(result[key] != value for key, value in derived_counts.items()):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if any(result[key] != value for key, value in expected_metrics.items()):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")

        manifest = result["manifest_parity_projection"]
        if set(manifest) != {
            "artifact_authority_target_tuple",
            "requested_authority_target_tuple",
            "live_catalog_authority_target_tuple",
            "measured_case_authority_target_tuple",
        }:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        manifest_values = tuple(
            tuple(tuple(row) for row in manifest[key])
            for key in (
                "artifact_authority_target_tuple",
                "requested_authority_target_tuple",
                "live_catalog_authority_target_tuple",
                "measured_case_authority_target_tuple",
            )
        )
        if len(set(manifest_values)) != 1 or manifest_values[0] != targets:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        digest_expectations = {
            "catalog_projection_sha256": _digest(
                [
                    "task7-v12-census-catalog-projection-v1",
                    result["catalog_projection_tuple"],
                ]
            ),
            "case_result_sha256": _digest(["task7-v12-current-guard-case-results-v1", outcomes]),
            "constructor_roundtrip_sha256": _digest(
                ["task7-v12-constructor-roundtrips-v1", constructors]
            ),
            "admin_fixture_helper_projection_sha256": _digest(
                ["task7-v12-admin-fixture-helper-projection-v1", helper_projection]
            ),
            "manifest_parity_sha256": _digest(["task7-v12-manifest-parity-v1", manifest]),
            "pg_trigger_inventory_sha256": _digest(
                [
                    "task7-v12-pg-trigger-projection-v1",
                    result["pg_trigger_projection_tuple"],
                ]
            ),
            "trigger_event_coverage_sha256": _digest(
                [
                    "task7-v12-trigger-event-coverage-v1",
                    [
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
                    ],
                ]
            ),
            "guard_acl_projection_sha256": _digest(
                [
                    "task7-v12-guard-acl-projection-v1",
                    routine_catalog,
                    attribute_acl,
                    schema_acl,
                    result["guard_owner_posture"],
                    result["external_erasure_helper_proof"],
                ]
            ),
            "rls_context_proof_sha256": _digest(
                [
                    "task7-v12-security-definer-guard-owner-rls-context-proof-v1",
                    result["security_definer_guard_owner_rls_context_proof"],
                ]
            ),
        }
        if any(result[key] != value for key, value in digest_expectations.items()):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        _validate_complete_execution_result(result)
        if "v11" in json.dumps(result, sort_keys=True).lower():
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID") from exc


def build_and_verify_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Issue and immediately verify an artifact-bound v12 census receipt."""

    if not signer_id.strip():
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
    _validate_complete_census_result(result, artifact_raw=artifact_raw)
    execution = _database_execution(result)
    _validate_database_execution(execution)
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v12",
        "candidate_artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "prospective_frozen_artifact_sha256": result["prospective_frozen_artifact_sha256"],
        "database_execution": execution,
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
    rendered = (json.dumps(receipt, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    verify_signed_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    return receipt


def _decode_receipt(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (CensusError, UnicodeError, ValueError) as exc:
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID") from exc
    if not isinstance(value, dict):
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
    return value


def verify_signed_receipt(
    *,
    receipt_raw: bytes,
    artifact_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Verify signature, artifact binding, shape, and semantic census counts."""

    try:
        receipt = _decode_receipt(receipt_raw)
        if tuple(receipt) != (
            "schema_version",
            "signer_id",
            "payload",
            "payload_sha256",
            "public_key_sha256",
            "signature_base64",
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if receipt["schema_version"] != _CENSUS_ENVELOPE_SCHEMA:
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        payload = receipt["payload"]
        if not isinstance(payload, Mapping) or tuple(payload) != (
            "schema_version",
            "result",
            "artifact_schema",
            "pipeline_contract_version",
            "candidate_artifact_sha256",
            "prospective_frozen_artifact_sha256",
            "database_execution",
            "census_result",
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        if (
            payload["schema_version"] != _CENSUS_PAYLOAD_SCHEMA
            or payload["result"] != "PASS"
            or payload["artifact_schema"] != "full-strict-v12-reviewed-preimage-v1"
            or payload["pipeline_contract_version"] != "full-strict-v12"
            or payload["candidate_artifact_sha256"] != hashlib.sha256(artifact_raw).hexdigest()
            or receipt["payload_sha256"] != _digest(payload)
            or receipt["public_key_sha256"] != hashlib.sha256(trusted_public_key_raw).hexdigest()
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        execution = payload["database_execution"]
        census_result = payload["census_result"]
        if not isinstance(execution, Mapping) or not isinstance(census_result, Mapping):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        _validate_complete_census_result(
            census_result,
            artifact_raw=artifact_raw,
        )
        if (
            dict(execution) != _database_execution(census_result)
            or census_result.get("candidate_artifact_sha256")
            != payload["candidate_artifact_sha256"]
            or census_result.get("prospective_frozen_artifact_sha256")
            != payload["prospective_frozen_artifact_sha256"]
        ):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        _validate_database_execution(execution)
        public_key = serialization.load_pem_public_key(trusted_public_key_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID")
        signature = base64.b64decode(
            str(receipt["signature_base64"]),
            validate=True,
        )
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
        if isinstance(exc, CensusError) and str(exc) == ("TASK7_V12_CENSUS_RECEIPT_INVALID"):
            raise
        raise CensusError("TASK7_V12_CENSUS_RECEIPT_INVALID") from exc
    return dict(payload)


__all__ = [
    "BASE_SCHEMA_REVISION",
    "CensusError",
    "GUARD_OWNER_ROLE",
    "HARNESS_IDENTITY",
    "INSTALLATION_MODE",
    "RESULT_SCHEMA",
    "TASK6_WRITE_CONTEXT_GUCS",
    "_CENSUS_SIGNING_DOMAIN",
    "_canonical_bytes",
    "_digest",
    "build_and_verify_signed_receipt",
    "run_pre_fixture_census",
    "validate_fixture_values",
    "verify_signed_receipt",
]
