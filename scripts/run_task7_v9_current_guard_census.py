"""Execute the full-strict-v9 pre-freeze current-guard census.

This harness deliberately starts from revision 0037 and installs the reviewed
artifact SQL into a disposable test scaffold.  It never imports migration 0038
and never represents the scaffold as a migrated production schema.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from jsonschema import Draft202012Validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._task7_v9_census_scaffold import (  # noqa: E402
    catalog_projection,
    disable_all_current_user_triggers,
    enable_target_trigger,
    install_artifact_sql_scaffold,
)
from tests.slice100_task2b._support import (  # noqa: E402
    disposable_postgres,
    migrate,
)

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v9-current-guard-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v9-current-guard-census-result-v1"

_CENSUS_ENVELOPE_SCHEMA = "task7-v9-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA = "task7-v9-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN = "task7-v9-postgres-census-signable-v1"
_HARNESS_SHA256 = hashlib.sha256(HARNESS_IDENTITY.encode("utf-8")).hexdigest()
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
_CASE_KINDS: Final[tuple[CaseKind, ...]] = (
    "LEGAL",
    "ILLEGAL",
    "DIRECT_SQL",
)

_AUTHORITY_REJECTION_MARKERS: Final = {
    "current-source-exact-one-v1": (
        "TASK7_SOURCE_TERMINAL_STATE_INVALID",
    ),
    "current-output-exact-one-v1": (
        "STEP_OUTPUT_MISSING",
        "PIPELINE_CONTRACT_HASH_MISMATCH",
    ),
    "current-receipt-equality-v1": (
        "CONSUMER_RECEIPT_INCOMPLETE",
    ),
    "current-input-binding-admission-v1": (
        "PIPELINE_CONTRACT_HASH_MISMATCH",
        "STRICT_PREFLIGHT_PROOF_MISMATCH",
        "STEP_REHYDRATION_FAILED",
    ),
    "current-append-only-lease-v1": (
        "WORKER_LEASE_FENCED",
        "STEP_REHYDRATION_FAILED",
    ),
    "current-source-bound-v1": (
        "TASK7_SOURCE_FREEZE_FENCED",
    ),
    "current-output-bound-v1": (
        "STEP_REHYDRATION_FAILED",
    ),
    "current-runs-contract-immutable-v1": (
        "PIPELINE_CONTRACT_HASH_MISMATCH",
    ),
    "current-run-steps-contract-immutable-v1": (
        "PIPELINE_CONTRACT_HASH_MISMATCH",
    ),
    "current-task5-coverage-replacement-v1": (
        "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE",
        "METHODOLOGY_COVERAGE_SCOPE_INVALID",
    ),
    "current-task6-immutable-replacement-v1": (
        "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE",
        "WORKER_LEASE_FENCED",
    ),
}

_NEW_TASK7_TABLES: Final = frozenset(
    {
        "deliverable_manifest_references",
        "document_parse_attempts",
        "run_applicability_decisions",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
        "run_input_bindings",
        "run_output_objects",
        "run_step_outputs",
        "source_lineage_objects",
    }
)


class CensusError(RuntimeError):
    """Secret-safe failure in the executable pre-freeze census."""


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CensusError(f"TASK7_V9_CENSUS_MODULE_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_GENERATOR = _load_module(
    Path(__file__).with_name("generate_task7_v9_contract_fixture.py"),
    "_task7_v9_census_generator",
)


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _GENERATOR._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _decode_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    artifact = cast(dict[str, Any], _GENERATOR._decode_artifact(raw))
    _GENERATOR._validate_shape(raw, artifact)
    if artifact["metadata"]["append_frozen"] is not False:
        raise CensusError("TASK7_V9_CENSUS_REQUIRES_UNFROZEN_CANDIDATE")
    return raw, artifact


def _ensure_admin_url() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        return
    dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not dotenv_path:
        raise CensusError("TASK7_V9_POSTGRES_ADMIN_URL_REQUIRED")
    try:
        from idis.runtime.profile import _parse_dotenv

        values = _parse_dotenv(dotenv_path)
        admin_url = values["IDIS_DATABASE_ADMIN_URL"].strip()
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise CensusError("TASK7_V9_POSTGRES_ADMIN_URL_REQUIRED") from exc
    if not admin_url:
        raise CensusError("TASK7_V9_POSTGRES_ADMIN_URL_REQUIRED")
    os.environ["IDIS_DATABASE_ADMIN_URL"] = admin_url


def _identifier(value: str) -> str:
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise CensusError("TASK7_V9_CENSUS_IDENTIFIER_INVALID")
    return value


def _sqlstate(error: DBAPIError) -> str:
    original = error.orig
    value = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    rendered = str(value or "")
    if re.fullmatch(r"[0-9A-Z]{5}", rendered) is None:
        raise CensusError("TASK7_V9_CENSUS_SQLSTATE_MISSING") from error
    return rendered


def _rejection_marker(error: DBAPIError, authority_id: str) -> str:
    rendered = str(error.orig).upper()
    for marker in _AUTHORITY_REJECTION_MARKERS[authority_id]:
        if marker in rendered:
            return marker
    raise CensusError(
        f"TASK7_V9_CENSUS_UNRELATED_REJECTION:{authority_id}:"
        f"SQLSTATE={_sqlstate(error)}"
    ) from error


def _column_inventory(connection: Connection) -> dict[str, dict[str, tuple[str, bool]]]:
    rows = connection.execute(
        text(
            """
            SELECT table_name,column_name,udt_name,is_generated
            FROM information_schema.columns
            WHERE table_schema='public'
            ORDER BY table_name,ordinal_position
            """
        )
    )
    result: dict[str, dict[str, tuple[str, bool]]] = {}
    for row in rows:
        result.setdefault(str(row.table_name), {})[str(row.column_name)] = (
            str(row.udt_name),
            str(row.is_generated) != "NEVER",
        )
    return result


def _bind_expression(parameter: str, udt_name: str) -> str:
    if udt_name == "uuid":
        return f"CAST(:{parameter} AS uuid)"
    if udt_name == "jsonb":
        return f"CAST(:{parameter} AS jsonb)"
    if udt_name == "json":
        return f"CAST(:{parameter} AS json)"
    if udt_name == "_uuid":
        return f"CAST(:{parameter} AS uuid[])"
    if udt_name == "_text":
        return f"CAST(:{parameter} AS text[])"
    return f":{parameter}"


def _insert_relaxed(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    table_name: str,
    values: Mapping[str, Any],
    *,
    returning: Sequence[str] = (),
) -> Mapping[str, Any] | None:
    table_name = _identifier(table_name)
    available = columns.get(table_name)
    if available is None:
        raise CensusError(f"TASK7_V9_CENSUS_TABLE_MISSING:{table_name}")
    selected: list[tuple[str, Any, str]] = []
    for column_name, value in values.items():
        if column_name not in available:
            continue
        udt_name, generated = available[column_name]
        if generated:
            continue
        selected.append((_identifier(column_name), value, udt_name))
    params: dict[str, Any] = {}
    if selected:
        names: list[str] = []
        expressions: list[str] = []
        for ordinal, (column_name, value, udt_name) in enumerate(selected):
            parameter = f"value_{ordinal}"
            names.append(column_name)
            expressions.append(_bind_expression(parameter, udt_name))
            if udt_name in {"json", "jsonb"} and not isinstance(value, str):
                params[parameter] = json.dumps(
                    value,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            else:
                params[parameter] = value
        statement = (
            f"INSERT INTO public.{table_name} ({','.join(names)}) "
            f"VALUES ({','.join(expressions)})"
        )
    else:
        statement = f"INSERT INTO public.{table_name} DEFAULT VALUES"
    if returning:
        for column_name in returning:
            _identifier(column_name)
            if column_name not in available:
                raise CensusError(
                    f"TASK7_V9_CENSUS_RETURNING_COLUMN_MISSING:"
                    f"{table_name}.{column_name}"
                )
        statement += " RETURNING " + ",".join(returning)
        return connection.execute(text(statement), params).mappings().one()
    connection.execute(text(statement), params)
    return None


def _drop_non_guard_constraints(
    connection: Connection,
    *,
    relation_names: Sequence[str],
) -> None:
    """Make the disposable scaffold isolate the hash-bound trigger bodies."""

    names = tuple(dict.fromkeys(_identifier(name) for name in relation_names))
    existing = {
        str(value)
        for value in connection.execute(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname='public' AND tablename = ANY(:tables)"
            ),
            {"tables": list(names)},
        ).scalars()
    }
    for table_name in names:
        if table_name not in existing:
            continue
        constraints = tuple(
            str(value)
            for value in connection.execute(
                text(
                    """
                    SELECT c.conname
                    FROM pg_catalog.pg_constraint c
                    JOIN pg_catalog.pg_class t ON t.oid=c.conrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
                    WHERE n.nspname='public' AND t.relname=:table_name
                    ORDER BY c.conname
                    """
                ),
                {"table_name": table_name},
            ).scalars()
        )
        for constraint_name in constraints:
            _identifier(constraint_name)
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} "
                f"DROP CONSTRAINT {constraint_name} CASCADE"
            )
        not_null_columns = tuple(
            str(value)
            for value in connection.execute(
                text(
                    """
                    SELECT a.attname
                    FROM pg_catalog.pg_attribute a
                    JOIN pg_catalog.pg_class t ON t.oid=a.attrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
                    WHERE n.nspname='public' AND t.relname=:table_name
                      AND a.attnum>0 AND NOT a.attisdropped
                      AND a.attnotnull AND a.attgenerated=''
                    ORDER BY a.attnum
                    """
                ),
                {"table_name": table_name},
            ).scalars()
        )
        for column_name in not_null_columns:
            _identifier(column_name)
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} "
                f"ALTER COLUMN {column_name} DROP NOT NULL"
            )


def _disable_target_relations(
    connection: Connection,
    ordered_targets: Sequence[tuple[str, str, str]],
) -> None:
    for table_name in dict.fromkeys(target[1] for target in ordered_targets):
        connection.exec_driver_sql(
            f"ALTER TABLE public.{_identifier(table_name)} DISABLE TRIGGER USER"
        )


def _set_replica(connection: Connection, enabled: bool) -> None:
    connection.exec_driver_sql(
        "SET LOCAL session_replication_role="
        + ("replica" if enabled else "origin")
    )


def _flush_deferred(connection: Connection) -> None:
    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")


def _set_worker_lease(
    connection: Connection,
    *,
    owner_id: str,
    fencing_token: int,
) -> None:
    connection.execute(
        text(
            "SELECT pg_catalog.set_config("
            "'idis.worker_lease_owner_id',:owner_id,true),"
            "pg_catalog.set_config("
            "'idis.worker_lease_fencing_token',:fencing_token,true)"
        ),
        {
            "owner_id": owner_id,
            "fencing_token": str(fencing_token),
        },
    )


def _seed_running_run(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "mode": "FULL",
        "status": "RUNNING",
        "idempotency_key": f"task7-v9-census-{uuid.uuid4()}",
        "pipeline_contract_version": "full-strict-v9",
        "pipeline_contract_hash": "a" * 64,
        "pipeline_resume_state": "RESUMABLE",
        "pipeline_resume_error_code": None,
        "lease_owner_id": str(uuid.uuid4()),
        "lease_fencing_token": 7,
        "lease_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "last_heartbeat_at": datetime.now(UTC),
    }
    _insert_relaxed(connection, columns, "runs", values)
    return values


def _seed_source_pair(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    native_table: str,
    include_root: bool,
    include_native: bool,
) -> dict[str, Any]:
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    corpus_id = str(uuid.uuid4())
    native_kind = {
        "data_room_packages": "PACKAGE",
        "data_room_package_files": "PACKAGE_FILE",
        "document_artifacts": "DOCUMENT_ARTIFACT",
        "documents": "DOCUMENT",
        "document_parse_attempts": "DOCUMENT_PARSE_ATTEMPT",
        "document_spans": "DOCUMENT_SPAN",
    }[native_table]
    if include_native:
        persisted = _insert_relaxed(
            connection,
            columns,
            native_table,
            {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "source_binding_state": "BOUND",
                "source_lineage_object_id": root_id,
                "source_corpus_receipt_id": corpus_id,
            },
            returning=("source_lineage_object_kind",),
        )
        if persisted is not None and persisted["source_lineage_object_kind"] is not None:
            native_kind = str(persisted["source_lineage_object_kind"])
    if include_root:
        _insert_relaxed(
            connection,
            columns,
            "source_lineage_objects",
            {
                "source_lineage_object_id": root_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "corpus_receipt_id": corpus_id,
                "object_kind": native_kind,
                "source_version": "task7-v9-census-source-v1",
                "source_disposition": "CORPUS",
                "object_identity_sha256": "1" * 64,
                "content_sha256": "2" * 64,
                "corpus_receipt_sha256": "3" * 64,
                "write_origin": "RUNTIME",
            },
        )
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "root_id": root_id,
        "corpus_id": corpus_id,
        "native_kind": native_kind,
    }


def _source_exact_one_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    native_table = (
        "data_room_packages"
        if table_name == "source_lineage_objects"
        else table_name
    )
    if case_kind == "ILLEGAL":
        include_root = table_name == "source_lineage_objects"
        include_native = not include_root
    else:
        include_root = True
        include_native = True
    _seed_source_pair(
        connection,
        columns,
        native_table=native_table,
        include_root=include_root,
        include_native=include_native,
    )
    if case_kind == "DIRECT_SQL":
        column_name = "source_lineage_object_id"
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} "
            f"SET {column_name}='{uuid.uuid4()}'::uuid"
        )
    else:
        connection.execute(
            text(
                f"UPDATE public.{_identifier(table_name)} "
                "SET tenant_id=tenant_id"
            )
        )
    _flush_deferred(connection)


def _seed_output_pair(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    native_table: str,
    include_root: bool,
    include_native: bool,
) -> dict[str, Any]:
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "output_object_id": str(uuid.uuid4()),
        "pipeline_contract_version": "full-strict-v9",
        "pipeline_contract_hash": "a" * 64,
    }
    output_kind = "METHODOLOGY_COVERAGE_IDENTITY"
    if include_native:
        persisted = _insert_relaxed(
            connection,
            columns,
            native_table,
            {
                **graph,
                "run_output_object_id": graph["output_object_id"],
            },
            returning=("run_output_object_kind",),
        )
        if persisted is None or persisted["run_output_object_kind"] is None:
            raise CensusError(
                f"TASK7_V9_NATIVE_KIND_NOT_MATERIALIZED:{native_table}"
            )
        output_kind = str(persisted["run_output_object_kind"])
    if include_root:
        _insert_relaxed(
            connection,
            columns,
            "run_output_objects",
            {
                **graph,
                "output_class": "NATIVE_ROW",
                "output_kind": output_kind,
                "output_schema_version": "task7-v9-census-v1",
                "native_identity_sha256": "0" * 64,
                "native_output_sha256": "0" * 64,
                "write_origin": "RUNTIME",
                "lease_owner_id": None,
                "lease_fencing_token": 0,
            },
        )
    if include_native and include_root:
        derived = connection.execute(
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
            graph,
        ).mappings().all()
        if len(derived) != 1 or derived[0]["output_kind"] != output_kind:
            raise CensusError(
                f"TASK7_V9_NATIVE_HASH_DISPATCH_INCOMPLETE:{native_table}"
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
                **graph,
                "identity_sha256": derived[0][
                    "expected_native_identity_sha256"
                ],
                "output_sha256": derived[0]["expected_native_output_sha256"],
            },
        )
    return {**graph, "output_kind": output_kind}


def _output_exact_one_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    native_table = (
        "methodology_coverage_records"
        if table_name == "run_output_objects"
        else table_name
    )
    if case_kind == "ILLEGAL":
        include_root = table_name == "run_output_objects"
        include_native = not include_root
    else:
        include_root = True
        include_native = True
    graph = _seed_output_pair(
        connection,
        columns,
        native_table=native_table,
        include_root=include_root,
        include_native=include_native,
    )
    if case_kind == "DIRECT_SQL":
        connection.execute(
            text(
                "UPDATE public.run_output_objects "
                "SET native_output_sha256=:invalid "
                "WHERE output_object_id=CAST(:output_object_id AS uuid)"
            ),
            {**graph, "invalid": "f" * 64},
        )
    connection.execute(
        text(
            f"UPDATE public.{_identifier(table_name)} "
            "SET tenant_id=tenant_id"
        )
    )
    _flush_deferred(connection)


def _seed_receipt_scope(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    completed_step: bool,
) -> dict[str, Any]:
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "step_id": str(uuid.uuid4()),
    }
    if completed_step:
        _insert_relaxed(
            connection,
            columns,
            "runs",
            {
                **graph,
                "mode": "FULL",
                "status": "RUNNING",
                "idempotency_key": f"task7-v9-receipt-{uuid.uuid4()}",
                "pipeline_contract_version": "full-strict-v9",
                "pipeline_contract_hash": "a" * 64,
                "pipeline_resume_state": "RESUMABLE",
            },
        )
        _insert_relaxed(
            connection,
            columns,
            "run_steps",
            {
                **graph,
                "step_name": "INGEST_CHECK",
                "step_order": 2,
                "status": "COMPLETED",
                "pipeline_contract_version": "full-strict-v9",
                "pipeline_contract_hash": "a" * 64,
                "semantic_rule_version": "2",
                "semantic_rule_hash": "b" * 64,
            },
        )
    return graph


def _seed_manifest_group(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    graph: Mapping[str, Any],
    *,
    complete: bool,
) -> None:
    manifest_id = str(uuid.uuid4())
    manifest_root = str(uuid.uuid4())
    count = 15 if complete else 1
    for ordinal in range(count):
        is_reconciliation = complete and ordinal == 14
        _insert_relaxed(
            connection,
            columns,
            "deliverable_manifest_references",
            {
                **graph,
                "manifest_reference_id": str(uuid.uuid4()),
                "manifest_deliverable_id": manifest_id,
                "manifest_output_object_id": manifest_root,
                "run_output_object_id": str(uuid.uuid4()),
                "member_kind": (
                    "RECONCILIATION" if is_reconciliation else "ARTIFACT"
                ),
                "catalog_ordinal": None if is_reconciliation else ordinal,
                "member_step_output_id": str(uuid.uuid4()),
                "member_output_object_id": str(uuid.uuid4()),
                "member_identity_sha256": "1" * 64,
                "member_value_sha256": "2" * 64,
                "member_content_sha256": None if is_reconciliation else "3" * 64,
                "reference_sha256": "4" * 64,
                "lease_owner_id": str(uuid.uuid4()),
                "lease_fencing_token": 1,
            },
        )


def _receipt_equality_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    illegal = case_kind != "LEGAL"
    graph = _seed_receipt_scope(
        connection,
        columns,
        completed_step=illegal,
    )
    if table_name == "run_steps":
        if not illegal:
            _insert_relaxed(
                connection,
                columns,
                table_name,
                {
                    **graph,
                    "step_name": "INGEST_CHECK",
                    "step_order": 2,
                    "status": "PENDING",
                },
            )
    elif table_name == "run_output_objects":
        output_id = str(uuid.uuid4())
        _insert_relaxed(
            connection,
            columns,
            table_name,
            {
                **graph,
                "output_object_id": output_id,
                "output_class": "STEP_RECEIPT",
            },
        )
        if not illegal:
            _insert_relaxed(
                connection,
                columns,
                "run_step_outputs",
                {
                    **graph,
                    "step_output_id": str(uuid.uuid4()),
                    "output_object_id": output_id,
                },
            )
    elif table_name == "deliverable_manifest_references":
        _seed_manifest_group(
            connection,
            columns,
            graph,
            complete=not illegal,
        )
    else:
        _insert_relaxed(
            connection,
            columns,
            table_name,
            {
                **graph,
                "step_output_id": str(uuid.uuid4()),
                "output_object_id": str(uuid.uuid4()),
                "expected_edge_id": str(uuid.uuid4()),
                "receipt_id": str(uuid.uuid4()),
            },
        )
    statement = (
        f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
    )
    if case_kind == "DIRECT_SQL":
        connection.exec_driver_sql(statement)
    else:
        connection.execute(text(statement))
    _flush_deferred(connection)


def _seed_input_binding_graph(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> dict[str, str]:
    from tests.slice100_task7 import (
        test_migration_0038_current_guards_red as guard_support,
    )

    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    package_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    corpus_sha256 = _digest(["task7-v9-input-corpus", package_id])
    _insert_relaxed(
        connection,
        columns,
        "data_room_packages",
        {
            "package_id": package_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "status": "SEALED",
            "source_binding_state": "BOUND",
            "source_lineage_object_id": root_id,
        },
    )
    _insert_relaxed(
        connection,
        columns,
        "source_lineage_objects",
        {
            "source_lineage_object_id": root_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "corpus_receipt_id": package_id,
            "object_kind": "PACKAGE",
            "source_version": "task7-v9-input-source-v1",
            "source_disposition": "CORPUS",
            "object_identity_sha256": "1" * 64,
            "content_sha256": corpus_sha256,
            "corpus_receipt_sha256": corpus_sha256,
            "write_origin": "BACKFILL",
        },
    )
    source = {
        "source_lineage_object_id": root_id,
        "corpus_receipt_id": package_id,
        "source_object_kind": "PACKAGE",
        "source_disposition": "CORPUS",
        "binding_purpose": "INVENTORY",
        "selected_source_version": "task7-v9-input-source-v1",
        "selected_identity_sha256": "1" * 64,
        "selected_content_sha256": corpus_sha256,
        "corpus_receipt_sha256": corpus_sha256,
    }
    graph = guard_support._seed_strict_run_for_source(
        connection,
        tenant_id=tenant_id,
        deal_id=deal_id,
        source=source,
        pipeline_contract_version="full-strict-v9",
        pipeline_contract_hash="a" * 64,
    )
    return cast(dict[str, str], guard_support._binding_values(graph))


def _input_binding_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    case_kind: CaseKind,
) -> None:
    from tests.slice100_task7 import (
        test_migration_0038_current_guards_red as guard_support,
    )

    values = _seed_input_binding_graph(connection, columns)
    if case_kind == "ILLEGAL":
        values["binding_sha256"] = "f" * 64
    if case_kind == "DIRECT_SQL":
        _insert_relaxed(
            connection,
            columns,
            "run_input_bindings",
            values,
        )
        connection.exec_driver_sql(
            "UPDATE public.run_input_bindings "
            "SET binding_sha256='e'"
        )
    else:
        guard_support._insert_binding(connection, values)


def _append_only_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    graph = _seed_running_run(connection, columns)
    values = {
        **graph,
        "output_object_id": str(uuid.uuid4()),
        "step_output_id": str(uuid.uuid4()),
        "applicability_decision_id": str(uuid.uuid4()),
        "expected_edge_id": str(uuid.uuid4()),
        "receipt_id": str(uuid.uuid4()),
        "manifest_reference_id": str(uuid.uuid4()),
        "lease_owner_id": graph["lease_owner_id"],
        "lease_fencing_token": graph["lease_fencing_token"],
    }
    if case_kind == "DIRECT_SQL":
        _insert_relaxed(connection, columns, table_name, values)
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
        return
    _set_worker_lease(
        connection,
        owner_id=str(graph["lease_owner_id"]),
        fencing_token=(
            int(graph["lease_fencing_token"])
            if case_kind == "LEGAL"
            else int(graph["lease_fencing_token"]) + 1
        ),
    )
    _insert_relaxed(connection, columns, table_name, values)


def _source_bound_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    _insert_relaxed(
        connection,
        columns,
        table_name,
        {
            "tenant_id": str(uuid.uuid4()),
            "deal_id": str(uuid.uuid4()),
            "source_binding_state": (
                "UNBOUND" if case_kind == "LEGAL" else "BOUND"
            ),
            "source_lineage_object_id": (
                None if case_kind == "LEGAL" else str(uuid.uuid4())
            ),
            "source_corpus_receipt_id": (
                None if case_kind == "LEGAL" else str(uuid.uuid4())
            ),
        },
    )
    statement = (
        f"UPDATE public.{_identifier(table_name)} "
        "SET source_binding_state=source_binding_state"
    )
    if case_kind == "DIRECT_SQL":
        connection.exec_driver_sql(statement)
    else:
        connection.execute(text(statement))


def _output_bound_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    _insert_relaxed(
        connection,
        columns,
        table_name,
        {
            "tenant_id": str(uuid.uuid4()),
            "deal_id": str(uuid.uuid4()),
            "run_id": str(uuid.uuid4()),
            "run_output_object_id": (
                None if case_kind == "LEGAL" else str(uuid.uuid4())
            ),
        },
    )
    if case_kind == "LEGAL":
        statement = (
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
    else:
        statement = (
            f"UPDATE public.{_identifier(table_name)} "
            f"SET run_output_object_id='{uuid.uuid4()}'::uuid"
        )
    if case_kind == "DIRECT_SQL":
        connection.exec_driver_sql(statement)
    else:
        connection.execute(text(statement))


def _immutable_contract_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    values = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "step_id": str(uuid.uuid4()),
        "mode": "FULL",
        "status": "RUNNING",
        "idempotency_key": f"task7-v9-immutable-{uuid.uuid4()}",
        "step_name": "INGEST_CHECK",
        "step_order": 2,
        "pipeline_contract_version": "full-strict-v9",
        "pipeline_contract_hash": "a" * 64,
        "pipeline_resume_state": "RESUMABLE",
        "pipeline_resume_error_code": None,
        "semantic_rule_version": "2",
        "semantic_rule_hash": "b" * 64,
    }
    _insert_relaxed(connection, columns, table_name, values)
    if case_kind == "LEGAL":
        statement = (
            f"UPDATE public.{_identifier(table_name)} "
            "SET pipeline_contract_hash=pipeline_contract_hash"
        )
    else:
        statement = (
            f"UPDATE public.{_identifier(table_name)} "
            "SET pipeline_contract_hash='f'"
        )
    if case_kind == "DIRECT_SQL":
        connection.exec_driver_sql(statement)
    else:
        connection.execute(text(statement))


def _task5_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    case_kind: CaseKind,
) -> None:
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    selection_id = str(uuid.uuid4())
    version_record_id = str(uuid.uuid4())
    question_record_id = str(uuid.uuid4())
    coverage_id = f"coverage-{uuid.uuid4()}"
    _insert_relaxed(
        connection,
        columns,
        "run_methodology_selections",
        {
            "selection_id": selection_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "commercial_dd_version_record_id": version_record_id,
        },
    )
    _insert_relaxed(
        connection,
        columns,
        "methodology_questions",
        {
            "tenant_id": tenant_id,
            "methodology_question_record_id": question_record_id,
            "methodology_version_record_id": version_record_id,
            "methodology_version_id": "cdd-v1",
            "methodology_question_id": "cdd-q-1",
            "methodology_type": "commercial_dd",
        },
    )
    _insert_relaxed(
        connection,
        columns,
        "methodology_coverage_records",
        {
            "coverage_record_id": coverage_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "selection_id": selection_id,
            "methodology_version_record_id": version_record_id,
            "methodology_version_id": "cdd-v1",
            "methodology_question_record_id": question_record_id,
            "methodology_question_id": "cdd-q-1",
            "methodology_type": "commercial_dd",
        },
    )
    if case_kind == "LEGAL":
        statement = (
            "UPDATE public.methodology_coverage_records "
            "SET coverage_record_id=coverage_record_id"
        )
    else:
        statement = (
            "UPDATE public.methodology_coverage_records "
            f"SET coverage_record_id='changed-{uuid.uuid4()}'"
        )
    if case_kind == "DIRECT_SQL":
        connection.exec_driver_sql(statement)
    else:
        connection.execute(text(statement))


def _task6_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    graph = _seed_running_run(connection, columns)
    values = {
        **graph,
        "task_record_id": str(uuid.uuid4()),
        "task_span_record_id": str(uuid.uuid4()),
        "result_id": str(uuid.uuid4()),
        "factual_output_record_id": str(uuid.uuid4()),
        "claim_link_id": str(uuid.uuid4()),
        "evidence_link_id": str(uuid.uuid4()),
        "sanad_link_id": str(uuid.uuid4()),
        "defect_link_id": str(uuid.uuid4()),
    }
    _set_worker_lease(
        connection,
        owner_id=str(graph["lease_owner_id"]),
        fencing_token=int(graph["lease_fencing_token"]),
    )
    if case_kind == "LEGAL":
        _insert_relaxed(connection, columns, table_name, values)
    else:
        _insert_relaxed(connection, columns, table_name, values)
        statement = f"DELETE FROM public.{_identifier(table_name)}"
        if case_kind == "DIRECT_SQL":
            connection.exec_driver_sql(statement)
        else:
            connection.execute(text(statement))


def _execute_case_operation(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> None:
    if authority_id == "current-source-exact-one-v1":
        _source_exact_one_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-output-exact-one-v1":
        _output_exact_one_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-receipt-equality-v1":
        _receipt_equality_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-input-binding-admission-v1":
        _input_binding_case(
            connection,
            columns,
            case_kind=case_kind,
        )
    elif authority_id == "current-append-only-lease-v1":
        _append_only_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-source-bound-v1":
        _source_bound_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-output-bound-v1":
        _output_bound_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        _immutable_contract_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    elif authority_id == "current-task5-coverage-replacement-v1":
        _task5_case(
            connection,
            columns,
            case_kind=case_kind,
        )
    elif authority_id == "current-task6-immutable-replacement-v1":
        _task6_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    else:
        raise CensusError(
            f"TASK7_V9_CENSUS_AUTHORITY_UNSUPPORTED:{authority_id}"
        )


def _run_one_case(
    *,
    engine: Any,
    artifact: Mapping[str, Any],
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    authority_id: str,
    table_name: str,
    guard_name: str,
    case_kind: CaseKind,
) -> dict[str, Any]:
    expected_rejection = case_kind != "LEGAL"
    error: DBAPIError | None = None
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            disable_all_current_user_triggers(
                connection,
                cast(dict[str, Any], artifact),
            )
            enable_target_trigger(
                connection,
                table_name,
                guard_name,
            )
            _set_replica(connection, True)
            # Seed methods run before the target operation and exact target
            # trigger.  Individual methods restore origin at their boundary.
            _set_replica(connection, False)
            try:
                _execute_case_operation(
                    connection,
                    columns,
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                )
            except DBAPIError as exc:
                error = exc
        finally:
            transaction.rollback()
    if expected_rejection:
        if error is None:
            raise CensusError(
                "TASK7_V9_CENSUS_ILLEGAL_CASE_ACCEPTED:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            )
        sqlstate = _sqlstate(error)
        marker = _rejection_marker(error, authority_id)
        return {
            "case_kind": case_kind,
            "authority_id": authority_id,
            "table_name": table_name,
            "guard_name": guard_name,
            "execution_path": (
                "DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION"
            ),
            "outcome": "REJECTED",
            "sqlstate": sqlstate,
            "authority_rejection_marker": marker,
            "rejection_provenance": "HASH_BOUND_AUTHORITY",
        }
    if error is not None:
        raise CensusError(
            "TASK7_V9_CENSUS_LEGAL_CASE_REJECTED:"
            f"{authority_id}:{table_name}:{guard_name}:"
            f"SQLSTATE={_sqlstate(error)}"
        ) from error
    return {
        "case_kind": case_kind,
        "authority_id": authority_id,
        "table_name": table_name,
        "guard_name": guard_name,
        "execution_path": "GUARDED_OPERATION",
        "outcome": "ACCEPTED",
        "sqlstate": None,
        "authority_rejection_marker": None,
        "rejection_provenance": None,
    }


def _schema_map(artifact: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    ids = {
        "validated-evidence-package-native",
        "muhasabah-native",
        "layer2-challenge-native",
        "debate-session-native",
    }
    return {
        str(row["schema_id"]): cast(
            dict[str, Any],
            row["normalized_schema_snapshot"],
        )
        for row in artifact["schema_registry"]["rows"]
        if row["schema_id"] in ids
    }


def _run_constructor_roundtrips(
    connection: Connection,
    artifact: Mapping[str, Any],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> tuple[dict[str, Any], ...]:
    from idis.models.layer1_durability import (
        MuhasabahRecordRow,
        ValidatedEvidencePackageRow,
    )
    from idis.models.layer2_durability import Layer2ChallengeRow
    from idis.persistence.repositories.layer1_evidence import (
        PostgresLayer1EvidenceRepository,
    )
    from idis.persistence.repositories.layer2_challenge import (
        PostgresLayer2ChallengeRepository,
    )
    from tests.slice100_task7 import test_full_strict_v9_native_schema_red as native

    schemas = _schema_map(artifact)
    if len(schemas) != 4:
        raise CensusError("TASK7_V9_CONSTRUCTOR_SCHEMA_SET_INCOMPLETE")
    vep_row = ValidatedEvidencePackageRow.model_validate(native._vep_payload())
    muhasabah_row = MuhasabahRecordRow.model_validate(
        native._muhasabah_payload()
    )
    layer2_row = Layer2ChallengeRow.model_validate(native._layer2_payload())
    debate_row = native._debate_payload()
    tenant_id = vep_row.tenant_id
    deal_id = vep_row.deal_id
    run_id = vep_row.run_id

    connection.execute(
        text(
            "INSERT INTO tenants (tenant_id,name) "
            "VALUES (:tenant_id,'Task 7 v9 constructor census')"
        ),
        {"tenant_id": tenant_id},
    )
    connection.execute(
        text(
            "INSERT INTO deals (deal_id,tenant_id,name,created_at) "
            "VALUES (:deal_id,:tenant_id,'Task 7 v9 constructor deal',now())"
        ),
        {"deal_id": deal_id, "tenant_id": tenant_id},
    )
    connection.execute(
        text(
            "INSERT INTO runs (run_id,tenant_id,deal_id,mode,status) "
            "VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING')"
        ),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
        },
    )
    layer1 = PostgresLayer1EvidenceRepository(connection, tenant_id)
    layer1.upsert_validated_evidence_package(vep_row)
    layer1.upsert_muhasabah_record(muhasabah_row)
    layer2 = PostgresLayer2ChallengeRepository(connection, tenant_id)
    layer2.upsert_challenge(layer2_row)
    connection.execute(
        text(
            "INSERT INTO debate_sessions "
            "(debate_id,tenant_id,deal_id,run_id,protocol_version,"
            "max_rounds,rounds,status) VALUES "
            "(:debate_id,:tenant_id,:deal_id,:run_id,:protocol_version,"
            ":max_rounds,CAST(:rounds AS jsonb),:status)"
        ),
        {
            **{key: value for key, value in debate_row.items() if key != "rounds"},
            "rounds": json.dumps(
                debate_row["rounds"],
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    )

    persisted_vep = layer1.list_validated_evidence_packages(run_id=run_id)[0]
    persisted_muhasabah = layer1.list_muhasabah_records(run_id=run_id)[0]
    persisted_layer2 = layer2.list_challenges(run_id=run_id)[0]
    persisted_layer2["source_debate_id"] = str(
        persisted_layer2["source_debate_id"]
    )
    debate = connection.execute(
        text(
            "SELECT debate_id,tenant_id,deal_id,run_id,protocol_version,"
            "max_rounds,rounds,status FROM debate_sessions "
            "WHERE debate_id=:debate_id"
        ),
        {"debate_id": debate_row["debate_id"]},
    ).one()
    persisted_debate = {
        "debate_id": str(debate.debate_id),
        "tenant_id": str(debate.tenant_id),
        "deal_id": str(debate.deal_id),
        "run_id": str(debate.run_id),
        "protocol_version": debate.protocol_version,
        "max_rounds": debate.max_rounds,
        "rounds": debate.rounds,
        "status": debate.status,
    }

    originals = {
        "validated-evidence-package-native": vep_row.model_dump(mode="json"),
        "muhasabah-native": muhasabah_row.model_dump(mode="json"),
        "layer2-challenge-native": layer2_row.model_dump(mode="json"),
        "debate-session-native": debate_row,
    }
    persisted = {
        "validated-evidence-package-native": persisted_vep,
        "muhasabah-native": persisted_muhasabah,
        "layer2-challenge-native": persisted_layer2,
        "debate-session-native": persisted_debate,
    }
    specs = tuple(tuple(item) for item in production_constructor_spec_tuple)
    proofs: list[dict[str, Any]] = []
    for schema_id, schema_version, constructor_qualname, durable_table in specs:
        payload = persisted[schema_id]
        errors = tuple(
            Draft202012Validator(schemas[schema_id]).iter_errors(payload)
        )
        if errors:
            raise CensusError(
                f"TASK7_V9_CONSTRUCTOR_SCHEMA_REJECTED:{schema_id}"
            )
        original_sha = _digest(originals[schema_id])
        reloaded_sha = _digest(payload)
        if reloaded_sha != original_sha:
            raise CensusError(
                f"TASK7_V9_CONSTRUCTOR_ROUNDTRIP_DRIFT:{schema_id}"
            )
        proofs.append(
            {
                "schema_id": schema_id,
                "schema_version": schema_version,
                "constructor_qualname": constructor_qualname,
                "durable_table": durable_table,
                "outcome": "ROUNDTRIP_MATCH",
                "original_payload_sha256": original_sha,
                "reloaded_payload_sha256": reloaded_sha,
            }
        )
    return tuple(proofs)


def _manifest_parity_sha256(connection: Connection) -> str:
    record = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "deal_id": "22222222-2222-2222-2222-222222222222",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "pipeline_contract_version": "full-strict-v9",
        "pipeline_contract_hash": "a" * 64,
        "manifest_deliverable_id": "44444444-4444-4444-4444-444444444444",
        "member_kind": "ARTIFACT",
        "catalog_ordinal": 0,
        "member_step_output_id": "55555555-5555-5555-5555-555555555555",
        "member_output_object_id": "66666666-6666-6666-6666-666666666666",
        "member_identity_sha256": "b" * 64,
        "member_value_sha256": "c" * 64,
        "member_content_sha256": "d" * 64,
        "manifest_reference_id": "77777777-7777-7777-7777-777777777777",
        "manifest_output_object_id": "88888888-8888-8888-8888-888888888888",
        "run_output_object_id": "99999999-9999-9999-9999-999999999999",
    }
    python_digest = cast(
        str,
        _GENERATOR.derive_manifest_reference_sha256(record),
    )
    postgres_digest = str(
        connection.execute(
            text(
                """
                SELECT public.idis_task7_manifest_reference_digest_v1(
                    CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),
                    CAST(:run_id AS uuid),:pipeline_contract_version,
                    :pipeline_contract_hash,
                    CAST(:manifest_deliverable_id AS uuid),:member_kind,
                    :catalog_ordinal,CAST(:member_step_output_id AS uuid),
                    CAST(:member_output_object_id AS uuid),
                    :member_identity_sha256,:member_value_sha256,
                    :member_content_sha256
                )
                """
            ),
            record,
        ).scalar_one()
    )
    if postgres_digest != python_digest:
        raise CensusError("TASK7_V9_MANIFEST_DIGEST_PARITY_FAILED")
    reassigned = {
        **record,
        "manifest_reference_id": str(uuid.uuid4()),
        "manifest_output_object_id": str(uuid.uuid4()),
        "run_output_object_id": str(uuid.uuid4()),
    }
    if _GENERATOR.derive_manifest_reference_sha256(reassigned) != python_digest:
        raise CensusError("TASK7_V9_MANIFEST_ROOT_STABILITY_FAILED")
    changed = {**record, "member_value_sha256": "e" * 64}
    if _GENERATOR.derive_manifest_reference_sha256(changed) == python_digest:
        raise CensusError("TASK7_V9_MANIFEST_SCALAR_SENSITIVITY_FAILED")
    return _digest(
        [
            "task7-v9-manifest-parity-v1",
            python_digest,
            postgres_digest,
            True,
            True,
        ]
    )


def _validate_request(
    *,
    artifact: Mapping[str, Any],
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> tuple[tuple[str, str, str], ...]:
    if (
        base_schema_revision != BASE_SCHEMA_REVISION
        or harness_identity != HARNESS_IDENTITY
    ):
        raise CensusError("TASK7_V9_CENSUS_HARNESS_BINDING_MISMATCH")
    current_rows = artifact["sql_authority_registry"]["rows"][17:]
    expected_targets = tuple(
        (
            str(row["authority_id"]),
            str(target[0]),
            str(target[1]),
        )
        for row in current_rows
        for target in row["target_tuple"]
    )
    supplied = tuple(
        cast(tuple[str, str, str], tuple(item))
        for item in ordered_authority_target_tuple
    )
    if supplied != expected_targets or len(supplied) != 73:
        raise CensusError("TASK7_V9_CENSUS_TARGET_INVENTORY_MISMATCH")
    if tuple(_AUTHORITY_REJECTION_MARKERS) != tuple(
        row["authority_id"] for row in current_rows
    ):
        raise CensusError("TASK7_V9_CENSUS_AUTHORITY_ORDER_MISMATCH")
    if len(production_constructor_spec_tuple) != 4:
        raise CensusError("TASK7_V9_CENSUS_CONSTRUCTOR_INVENTORY_MISMATCH")
    return expected_targets


def run_pre_fixture_census(
    *,
    artifact_path: Path,
    base_schema_revision: str,
    harness_identity: str,
    ordered_authority_target_tuple: Sequence[Sequence[str]],
    production_constructor_spec_tuple: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Execute the approved 0037-based guard and constructor census."""

    _ensure_admin_url()
    artifact_raw, artifact = _decode_artifact(Path(artifact_path))
    ordered_targets = _validate_request(
        artifact=artifact,
        base_schema_revision=base_schema_revision,
        harness_identity=harness_identity,
        ordered_authority_target_tuple=ordered_authority_target_tuple,
        production_constructor_spec_tuple=production_constructor_spec_tuple,
    )
    prospective_raw = cast(
        bytes,
        _GENERATOR.derive_prospective_frozen_bytes(artifact_raw),
    )
    constructor_proofs: tuple[dict[str, Any], ...]
    outcomes: list[dict[str, Any]] = []
    with disposable_postgres() as database:
        migrate(database.admin_engine, BASE_SCHEMA_REVISION)
        with database.admin_engine.connect() as connection:
            transaction = connection.begin()
            try:
                constructor_proofs = _run_constructor_roundtrips(
                    connection,
                    artifact,
                    production_constructor_spec_tuple,
                )
            finally:
                transaction.rollback()
        with database.admin_engine.begin() as connection:
            revision = str(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            )
            if revision != BASE_SCHEMA_REVISION:
                raise CensusError("TASK7_V9_CENSUS_BASE_REVISION_DRIFT")
            server_version_num = int(
                connection.execute(
                    text(
                        "SELECT current_setting('server_version_num')::integer"
                    )
                ).scalar_one()
            )
            install_artifact_sql_scaffold(connection, artifact)
            projection = catalog_projection(connection, artifact)
            manifest_parity_sha256 = _manifest_parity_sha256(connection)
            disable_all_current_user_triggers(connection, artifact)
            relation_names = tuple(
                dict.fromkeys(
                    [
                        *(row["table_name"] for row in artifact["current_ddl_registry"]["rows"]),
                        "run_methodology_selections",
                        "methodology_questions",
                    ]
                )
            )
            _drop_non_guard_constraints(
                connection,
                relation_names=relation_names,
            )
            _disable_target_relations(connection, ordered_targets)
            columns = _column_inventory(connection)

        for case_kind in _CASE_KINDS:
            for authority_id, table_name, guard_name in ordered_targets:
                outcomes.append(
                    _run_one_case(
                        engine=database.admin_engine,
                        artifact=artifact,
                        columns=columns,
                        authority_id=authority_id,
                        table_name=table_name,
                        guard_name=guard_name,
                        case_kind=case_kind,
                    )
                )

    legal_count = sum(
        row["case_kind"] == "LEGAL" and row["outcome"] == "ACCEPTED"
        for row in outcomes
    )
    illegal_count = sum(
        row["case_kind"] == "ILLEGAL" and row["outcome"] == "REJECTED"
        for row in outcomes
    )
    direct_count = sum(
        row["case_kind"] == "DIRECT_SQL" and row["outcome"] == "REJECTED"
        for row in outcomes
    )
    unexpected = (
        len(outcomes)
        - legal_count
        - illegal_count
        - direct_count
    )
    return {
        "schema_version": RESULT_SCHEMA,
        "harness_identity": HARNESS_IDENTITY,
        "base_schema_revision": BASE_SCHEMA_REVISION,
        "installation_mode": INSTALLATION_MODE,
        "database_product": "PostgreSQL",
        "server_version_num": server_version_num,
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v9",
        "candidate_append_frozen": False,
        "candidate_artifact_sha256": _sha256_bytes(artifact_raw),
        "prospective_frozen_artifact_sha256": _sha256_bytes(prospective_raw),
        "ordered_authority_target_tuple": [
            list(target) for target in ordered_targets
        ],
        "case_outcome_tuple": outcomes,
        "constructor_proof_tuple": list(constructor_proofs),
        "current_guard_target_count": len(ordered_targets),
        "legal_execution_count": legal_count,
        "illegal_rejection_count": illegal_count,
        "direct_sql_rejection_count": direct_count,
        "constructor_schema_proof_count": len(constructor_proofs),
        "unexpected_outcome_count": unexpected,
        "catalog_projection_sha256": _digest(
            ["task7-v9-census-catalog-projection-v1", projection]
        ),
        "case_result_sha256": _digest(
            ["task7-v9-current-guard-case-results-v1", outcomes]
        ),
        "constructor_roundtrip_sha256": _digest(
            ["task7-v9-constructor-roundtrips-v1", constructor_proofs]
        ),
        "manifest_parity_sha256": manifest_parity_sha256,
    }


def _database_execution(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "database_product": result["database_product"],
        "server_version_num": result["server_version_num"],
        "base_schema_revision": result["base_schema_revision"],
        "harness_identity": result["harness_identity"],
        "harness_sha256": _HARNESS_SHA256,
        "current_guard_target_count": result["current_guard_target_count"],
        "legal_execution_count": result["legal_execution_count"],
        "illegal_rejection_count": result["illegal_rejection_count"],
        "direct_sql_rejection_count": result["direct_sql_rejection_count"],
        "constructor_schema_proof_count": result[
            "constructor_schema_proof_count"
        ],
        "unexpected_outcome_count": result["unexpected_outcome_count"],
        "catalog_projection_sha256": result["catalog_projection_sha256"],
        "case_result_sha256": result["case_result_sha256"],
        "constructor_roundtrip_sha256": result[
            "constructor_roundtrip_sha256"
        ],
        "manifest_parity_sha256": result["manifest_parity_sha256"],
    }


def _load_keypair(
    *,
    private_key_path: Path,
    public_key_path: Path,
) -> tuple[Ed25519PrivateKey, bytes]:
    private_raw = private_key_path.read_bytes()
    public_raw = public_key_path.read_bytes()
    private_key = serialization.load_pem_private_key(
        private_raw,
        password=None,
    )
    public_key = serialization.load_pem_public_key(public_raw)
    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(
        public_key,
        Ed25519PublicKey,
    ):
        raise CensusError("TASK7_V9_CENSUS_SIGNING_KEY_INVALID")
    derived_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trusted_public = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if derived_public != trusted_public:
        raise CensusError("TASK7_V9_CENSUS_SIGNING_KEY_MISMATCH")
    return private_key, public_raw


def _build_signed_receipt(
    *,
    signer_id: str,
    artifact_raw: bytes,
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
) -> dict[str, Any]:
    payload = {
        "schema_version": _CENSUS_PAYLOAD_SCHEMA,
        "result": "PASS",
        "artifact_schema": artifact["metadata"]["artifact_schema"],
        "pipeline_contract_version": "full-strict-v9",
        "candidate_artifact_sha256": result["candidate_artifact_sha256"],
        "prospective_frozen_artifact_sha256": result[
            "prospective_frozen_artifact_sha256"
        ],
        "authority_inventory": _GENERATOR.derive_authority_inventory(
            cast(dict[str, Any], artifact)
        ),
        "database_execution": _database_execution(result),
    }
    payload_sha256 = _digest(payload)
    public_key_sha256 = _sha256_bytes(public_key_raw)
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
    rendered = (
        json.dumps(
            receipt,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _GENERATOR.verify_postgres_census_receipt(
        receipt_raw=rendered,
        artifact_raw=artifact_raw,
        trusted_public_key_raw=public_key_raw,
    )
    return receipt


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise CensusError("TASK7_V9_CENSUS_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CensusError("TASK7_V9_CENSUS_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    parser.add_argument("--signer-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if not args.signer_id.strip():
            raise CensusError("TASK7_V9_CENSUS_SIGNER_ID_INVALID")
        artifact_raw, artifact = _decode_artifact(args.artifact)
        rows = artifact["sql_authority_registry"]["rows"][17:]
        targets = tuple(
            (row["authority_id"], target[0], target[1])
            for row in rows
            for target in row["target_tuple"]
        )
        constructor_specs = (
            (
                "validated-evidence-package-native",
                "task7-validated-evidence-package-native-v1",
                "idis.models.layer1_durability."
                "ValidatedEvidencePackageRow.from_record",
                "validated_evidence_packages",
            ),
            (
                "muhasabah-native",
                "task7-muhasabah-native-v1",
                "idis.models.layer1_durability."
                "MuhasabahRecordRow.from_debate_record",
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
        result = run_pre_fixture_census(
            artifact_path=args.artifact,
            base_schema_revision=BASE_SCHEMA_REVISION,
            harness_identity=HARNESS_IDENTITY,
            ordered_authority_target_tuple=targets,
            production_constructor_spec_tuple=constructor_specs,
        )
        private_key, public_key_raw = _load_keypair(
            private_key_path=args.signing_private_key,
            public_key_path=args.trusted_public_key,
        )
        receipt = _build_signed_receipt(
            signer_id=args.signer_id,
            artifact_raw=artifact_raw,
            artifact=artifact,
            result=result,
            private_key=private_key,
            public_key_raw=public_key_raw,
        )
        rendered = (
            json.dumps(
                receipt,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        _write_exclusive(args.output, rendered)
    except (
        CensusError,
        DBAPIError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "TASK7_V9_POSTGRES_CENSUS_PASSED:"
        f"targets={result['current_guard_target_count']}:"
        f"cases={len(result['case_outcome_tuple'])}:"
        f"constructors={result['constructor_schema_proof_count']}:"
        f"receipt_sha256={_sha256_bytes(rendered)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
