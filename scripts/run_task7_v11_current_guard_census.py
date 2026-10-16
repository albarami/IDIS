"""Execute and sign the full-strict-v11 pre-freeze PostgreSQL census.

The harness starts from migration 0037, installs only SQL carried by the
unfrozen v11 artifact, proves the exact 73-target/192-event inventory, and
executes 219 event-labelled outcomes plus four production constructor
round-trips.  It neither imports migration 0038 nor freezes/publishes a
contract fixture.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
import re
import struct
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, Never, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._task7_v11_census_scaffold import (  # noqa: E402
    catalog_projection,
    disable_all_current_user_triggers,
    enable_target_trigger,
    install_artifact_sql_scaffold,
)
from tests.slice100_task2b._support import disposable_postgres, migrate  # noqa: E402

BASE_SCHEMA_REVISION: Final = "0037"
HARNESS_IDENTITY: Final = "task7-v11-current-guard-pre-freeze-artifact-sql-on-0037-v1"
INSTALLATION_MODE: Final = "ARTIFACT_SQL_TEST_SCAFFOLD"
RESULT_SCHEMA: Final = "task7-v11-current-guard-census-result-v1"

_CENSUS_ENVELOPE_SCHEMA: Final = "task7-v11-postgres-census-envelope-v1"
_CENSUS_PAYLOAD_SCHEMA: Final = "task7-v11-postgres-census-v1"
_CENSUS_SIGNING_DOMAIN: Final = "task7-v11-postgres-census-signable-v1"
_HARNESS_SHA256: Final = hashlib.sha256(HARNESS_IDENTITY.encode("utf-8")).hexdigest()
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

CaseKind = Literal["LEGAL", "ILLEGAL", "DIRECT_SQL"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]
_CASE_KINDS: Final[tuple[CaseKind, ...]] = ("LEGAL", "ILLEGAL", "DIRECT_SQL")
_EVENT_ORDER: Final[tuple[TriggerEvent, ...]] = ("INSERT", "UPDATE", "DELETE")

TASK6_WRITE_CONTEXT_GUCS: Final = (
    "idis.worker_lease_tenant_id",
    "idis.worker_lease_deal_id",
    "idis.worker_lease_run_id",
    "idis.worker_lease_owner_id",
    "idis.worker_lease_fencing_token",
)
_TASK6_OUTPUT_ROOT_UPDATE_TARGETS: Final = frozenset(
    {
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
    }
)

_AUTHORITY_REJECTION_MARKERS: Final = {
    "current-source-exact-one-v1": ("TASK7_SOURCE_TERMINAL_STATE_INVALID",),
    "current-output-exact-one-v1": (
        "STEP_OUTPUT_MISSING",
        "PIPELINE_CONTRACT_HASH_MISMATCH",
    ),
    "current-receipt-equality-v1": ("CONSUMER_RECEIPT_INCOMPLETE",),
    "current-input-binding-admission-v1": (
        "PIPELINE_CONTRACT_HASH_MISMATCH",
        "STRICT_PREFLIGHT_PROOF_MISMATCH",
        "STEP_REHYDRATION_FAILED",
    ),
    "current-append-only-lease-v1": (
        "WORKER_LEASE_FENCED",
        "STEP_REHYDRATION_FAILED",
    ),
    "current-source-bound-v1": ("TASK7_SOURCE_FREEZE_FENCED",),
    "current-output-bound-v1": ("STEP_REHYDRATION_FAILED",),
    "current-runs-contract-immutable-v1": ("PIPELINE_CONTRACT_HASH_MISMATCH",),
    "current-run-steps-contract-immutable-v1": ("PIPELINE_CONTRACT_HASH_MISMATCH",),
    "current-task5-coverage-replacement-v1": (
        "METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE",
        "METHODOLOGY_COVERAGE_SCOPE_INVALID",
    ),
    "current-task6-immutable-replacement-v1": (
        "METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE",
        "WORKER_LEASE_FENCED",
    ),
}
_AUTHORITY_FUNCTIONS: Final = {
    "current-source-exact-one-v1": "idis_task7_current_source_exact_one_v1",
    "current-output-exact-one-v1": "idis_task7_current_output_exact_one_v1",
    "current-receipt-equality-v1": "idis_task7_current_receipt_equality_v1",
    "current-input-binding-admission-v1": "idis_task7_current_input_binding_admission_v1",
    "current-append-only-lease-v1": "idis_task7_current_append_only_lease_v1",
    "current-source-bound-v1": "idis_task7_current_source_bound_v1",
    "current-output-bound-v1": "idis_task7_current_output_bound_v1",
    "current-runs-contract-immutable-v1": "idis_task7_current_runs_contract_immutable_v1",
    "current-run-steps-contract-immutable-v1": (
        "idis_task7_current_run_steps_contract_immutable_v1"
    ),
    "current-task5-coverage-replacement-v1": "idis_task5_coverage_scope_guard",
    "current-task6-immutable-replacement-v1": "idis_task6_immutable_guard",
}

_SOURCE_NATIVE_KIND: Final = {
    "data_room_packages": "PACKAGE",
    "data_room_package_files": "PACKAGE_FILE",
    "document_artifacts": "DOCUMENT_ARTIFACT",
    "documents": "DOCUMENT",
    "document_parse_attempts": "DOCUMENT_PARSE_ATTEMPT",
    "document_spans": "DOCUMENT_SPAN",
}

_HISTORICAL_RUNNER_PATH = Path(__file__).with_name(
    "run_task7_v9_current_guard_census.py"
)
_HISTORICAL_RUNNER_SHA256: Final = (
    "8954f62c37d31089aab50c6506d2c1273ae938c3982c49fd64b3b8cbdd30869d"
)
_GENERATOR_PATH = Path(__file__).with_name("generate_task7_v11_contract_fixture.py")


class CensusError(RuntimeError):
    """Secret-safe v11 census admission failure."""


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise CensusError("TASK7_V11_CENSUS_IDENTIFIER_INVALID")
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CensusError(f"TASK7_V11_CENSUS_MODULE_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_execution_engine() -> ModuleType:
    """Project hash-pinned historical constructor/hash helpers to v11."""

    raw = _HISTORICAL_RUNNER_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _HISTORICAL_RUNNER_SHA256:
        raise CensusError("TASK7_V11_HISTORICAL_CENSUS_ENGINE_DRIFT")
    source = raw.decode("utf-8")
    constructor_seed = (
        '"INSERT INTO runs (run_id,tenant_id,deal_id,mode,status) "\n'
        "            \"VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING')\""
    )
    lease_aware_constructor_seed = (
        '"INSERT INTO runs "\n'
        '            "(run_id,tenant_id,deal_id,mode,status,lease_owner_id,"\n'
        '            "lease_fencing_token,lease_expires_at,last_heartbeat_at) "\n'
        "            \"VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING',\"\n"
        "            \"'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,1,\"\n"
        "            \"now()+interval '1 hour',now())\""
    )
    unconstrained_scan = (
        "                    WHERE n.nspname='public' AND t.relname=:table_name\n"
        "                    ORDER BY c.conname"
    )
    non_guard_scan = (
        "                    WHERE n.nspname='public' AND t.relname=:table_name\n"
        "                      AND NOT EXISTS (\n"
        "                        SELECT 1 FROM pg_catalog.pg_trigger AS guard_trigger\n"
        "                        WHERE guard_trigger.tgconstraint=c.oid\n"
        "                          AND NOT guard_trigger.tgisinternal\n"
        "                      )\n"
        "                    ORDER BY c.conname"
    )
    replacements = (
        (constructor_seed, lease_aware_constructor_seed),
        (unconstrained_scan, non_guard_scan),
        (
            "from scripts._task7_v9_census_scaffold import",
            "from scripts._task7_v11_census_scaffold import",
        ),
        (
            'Path(__file__).with_name("generate_task7_v9_contract_fixture.py")',
            'Path(__file__).with_name("generate_task7_v11_contract_fixture.py")',
        ),
        ("_task7_v9_census_generator", "_task7_v11_census_generator"),
        ("full-strict-v9", "full-strict-v11"),
        ("task7-v9", "task7-v11"),
        ("TASK7_V9", "TASK7_V11"),
        ("Task 7 v9", "Task 7 v11"),
    )
    for old, new in replacements:
        if old not in source:
            raise CensusError(f"TASK7_V11_HISTORICAL_PROJECTION_TOKEN_MISSING:{old}")
        source = source.replace(old, new)
    if "v9" in source.lower():
        allowed = (
            "test_full_strict_v9_native_schema_red",
            "_task7_v9_census_scaffold.py",
        )
        scrubbed = source
        for value in allowed:
            scrubbed = scrubbed.replace(value, "")
        if "v9" in scrubbed.lower():
            raise CensusError("TASK7_V11_HISTORICAL_LABEL_PROJECTION_INCOMPLETE")

    spec = importlib.util.spec_from_loader(
        "_task7_v11_current_guard_census_engine",
        loader=None,
        origin=str(Path(__file__).resolve()),
    )
    if spec is None:
        raise CensusError("TASK7_V11_CENSUS_ENGINE_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(Path(__file__).resolve())
    module.__package__ = ""
    sys.modules[spec.name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


_GENERATOR = _load_module(_GENERATOR_PATH, "_task7_v11_census_receipt_generator")
_ENGINE = _load_execution_engine()
setattr(
    _GENERATOR,
    "derive_manifest_reference_sha256",
    _GENERATOR._V10.derive_manifest_reference_sha256,
)
setattr(
    _ENGINE._GENERATOR,
    "derive_manifest_reference_sha256",
    _GENERATOR._V10.derive_manifest_reference_sha256,
)


def _normalize_digest_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CensusError("TASK7_V11_CENSUS_NONFINITE_BINARY64")
        normalized = 0.0 if value == 0.0 else value
        return ["binary64", struct.pack("!d", normalized).hex()]
    if isinstance(value, Mapping):
        return {str(key): _normalize_digest_value(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_digest_value(child) for child in value]
    raise CensusError(
        f"TASK7_V11_CENSUS_DIGEST_VALUE_INVALID:{type(value).__name__}"
    )


def _canonical_bytes(value: Any) -> bytes:
    canonical = getattr(_GENERATOR, "_canonical_bytes")
    return cast(bytes, canonical(_normalize_digest_value(value)))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


setattr(_ENGINE, "_canonical_bytes", _canonical_bytes)


def _decode_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    artifact = cast(dict[str, Any], _GENERATOR._decode_artifact(raw))
    _GENERATOR._validate_shape(raw, artifact)
    if artifact["metadata"]["append_frozen"] is not False:
        raise CensusError("TASK7_V11_CENSUS_REQUIRES_UNFROZEN_CANDIDATE")
    return raw, artifact


def _require_isolated_admin_url() -> None:
    if not os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip():
        raise CensusError("TASK7_V11_POSTGRES_ADMIN_URL_REQUIRED")


def _sqlstate(error: DBAPIError) -> str:
    value = getattr(error.orig, "sqlstate", None) or getattr(
        error.orig,
        "pgcode",
        None,
    )
    if not isinstance(value, str) or len(value) != 5:
        raise CensusError("TASK7_V11_CENSUS_SQLSTATE_MISSING") from error
    return value


def _rejection_marker(error: DBAPIError, authority_id: str) -> str:
    rendered = str(error.orig).upper()
    for marker in _AUTHORITY_REJECTION_MARKERS[authority_id]:
        if marker in rendered:
            return marker
    raise CensusError(
        f"TASK7_V11_CENSUS_UNRELATED_REJECTION:{authority_id}:"
        f"SQLSTATE={_sqlstate(error)}"
    ) from error


def validate_fixture_values(
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    table_name: str,
    values: Mapping[str, Any],
) -> tuple[tuple[str, Any, str], ...]:
    """Reject every unknown, cross-target, or generated fixture field."""

    table = _identifier(table_name)
    available = columns.get(table)
    if available is None:
        raise CensusError(f"TASK7_V11_CENSUS_TABLE_MISSING:{table}")
    all_other_fields = {
        field
        for other_table, other_columns in columns.items()
        if other_table != table
        for field in other_columns
    }
    selected: list[tuple[str, Any, str]] = []
    for raw_name, value in values.items():
        name = _identifier(raw_name)
        definition = available.get(name)
        if definition is None:
            if name in all_other_fields:
                raise CensusError(
                    f"TASK7_V11_CENSUS_FIXTURE_OTHER_TARGET_FIELD:{table}.{name}"
                )
            raise CensusError(
                f"TASK7_V11_CENSUS_FIXTURE_UNKNOWN_FIELD:{table}.{name}"
            )
        udt_name, generated = definition
        if generated:
            raise CensusError(
                f"TASK7_V11_CENSUS_FIXTURE_GENERATED_FIELD:{table}.{name}"
            )
        selected.append((name, value, udt_name))
    return tuple(selected)


def _insert_strict(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    table_name: str,
    values: Mapping[str, Any],
    *,
    returning: Sequence[str] = (),
) -> Mapping[str, Any] | None:
    table = _identifier(table_name)
    selected = validate_fixture_values(columns, table, values)
    params: dict[str, Any] = {}
    names: list[str] = []
    expressions: list[str] = []
    for ordinal, (name, value, udt_name) in enumerate(selected):
        parameter = f"value_{ordinal}"
        names.append(name)
        expressions.append(cast(str, _ENGINE._bind_expression(parameter, udt_name)))
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
        f"INSERT INTO public.{table} ({','.join(names)}) "
        f"VALUES ({','.join(expressions)})"
        if names
        else f"INSERT INTO public.{table} DEFAULT VALUES"
    )
    if returning:
        available = columns[table]
        normalized = tuple(_identifier(value) for value in returning)
        for name in normalized:
            if name not in available:
                raise CensusError(
                    f"TASK7_V11_CENSUS_RETURNING_COLUMN_MISSING:{table}.{name}"
                )
        statement += " RETURNING " + ",".join(normalized)
        return connection.execute(text(statement), params).mappings().one()
    connection.execute(text(statement), params)
    return None


def _set_replica(connection: Connection, enabled: bool) -> None:
    connection.exec_driver_sql(
        "SET LOCAL session_replication_role="
        + ("replica" if enabled else "origin")
    )


def _flush_deferred(connection: Connection) -> None:
    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")


def _set_task6_write_context(
    connection: Connection,
    graph: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str, bool], ...]:
    connection.execute(
        text(
            """
            SELECT
                pg_catalog.set_config(
                    'idis.worker_lease_tenant_id', :tenant_id, true
                ),
                pg_catalog.set_config(
                    'idis.worker_lease_deal_id', :deal_id, true
                ),
                pg_catalog.set_config(
                    'idis.worker_lease_run_id', :run_id, true
                ),
                pg_catalog.set_config(
                    'idis.worker_lease_owner_id', :lease_owner_id, true
                ),
                pg_catalog.set_config(
                    'idis.worker_lease_fencing_token',
                    CAST(:lease_fencing_token AS text), true
                )
            """
        ),
        dict(graph),
    )
    observed = tuple(
        str(value)
        for value in connection.execute(
            text(
                """
                SELECT
                    current_setting('idis.worker_lease_tenant_id', true),
                    current_setting('idis.worker_lease_deal_id', true),
                    current_setting('idis.worker_lease_run_id', true),
                    current_setting('idis.worker_lease_owner_id', true),
                    current_setting('idis.worker_lease_fencing_token', true)
                """
            )
        ).one()
    )
    expected = (
        str(graph["tenant_id"]),
        str(graph["deal_id"]),
        str(graph["run_id"]),
        str(graph["lease_owner_id"]),
        str(graph["lease_fencing_token"]),
    )
    if observed != expected:
        raise CensusError("TASK7_V11_TASK6_WRITE_CONTEXT_MISMATCH")
    result: list[tuple[str, str, str, str, bool]] = []
    for ordinal, (name, expected_value, observed_value) in enumerate(
        zip(TASK6_WRITE_CONTEXT_GUCS, expected, observed, strict=True)
    ):
        expected_sha256 = _digest(
            ["task7-v11-task6-guc-value-v1", name, expected_value]
        )
        observed_sha256 = _digest(
            ["task7-v11-task6-guc-value-v1", name, observed_value]
        )
        result.append(
            (
                name,
                "BIGINT" if ordinal == 4 else "UUID",
                expected_sha256,
                observed_sha256,
                expected_sha256 == observed_sha256,
            )
        )
    return tuple(result)


def _seed_tenant_deal(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    tenant_id: str,
    deal_id: str,
) -> None:
    _insert_strict(
        connection,
        columns,
        "tenants",
        {
            "tenant_id": tenant_id,
            "name": "Task 7 v11 census tenant",
        },
    )
    _insert_strict(
        connection,
        columns,
        "deals",
        {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "name": "Fictional Task 7 v11 census deal",
            "created_at": datetime.now(UTC),
        },
    )


def _seed_run(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    status: str,
    include_tenant_deal: bool = False,
) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "lease_owner_id": str(uuid.uuid4()),
        "lease_fencing_token": 7,
        "pipeline_contract_version": "full-strict-v11",
        "pipeline_contract_hash": "a" * 64,
    }
    if include_tenant_deal:
        _seed_tenant_deal(
            connection,
            columns,
            tenant_id=str(graph["tenant_id"]),
            deal_id=str(graph["deal_id"]),
        )
    values: dict[str, Any] = {
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "mode": "FULL",
        "status": status,
        "idempotency_key": f"task7-v11-census-{uuid.uuid4()}",
        "pipeline_contract_version": graph["pipeline_contract_version"],
        "pipeline_contract_hash": graph["pipeline_contract_hash"],
        "pipeline_resume_state": "RESUMABLE",
        "pipeline_resume_error_code": None,
    }
    if status == "RUNNING":
        values.update(
            {
                "lease_owner_id": graph["lease_owner_id"],
                "lease_fencing_token": graph["lease_fencing_token"],
                "lease_expires_at": datetime.now(UTC) + timedelta(hours=1),
                "last_heartbeat_at": datetime.now(UTC),
            }
        )
    _insert_strict(connection, columns, "runs", values)
    return graph


def _target_event_map(
    ordered_target_events: Sequence[Sequence[str]],
) -> dict[tuple[str, str, str], tuple[TriggerEvent, ...]]:
    result: dict[tuple[str, str, str], list[TriggerEvent]] = {}
    for raw in ordered_target_events:
        if len(raw) != 4:
            raise CensusError("TASK7_V11_CENSUS_TARGET_EVENT_SHAPE_INVALID")
        authority_id, table_name, guard_name, event_raw = map(str, raw)
        if event_raw not in _EVENT_ORDER:
            raise CensusError("TASK7_V11_CENSUS_TARGET_EVENT_INVALID")
        key = (authority_id, _identifier(table_name), _identifier(guard_name))
        result.setdefault(key, []).append(cast(TriggerEvent, event_raw))
    normalized = {key: tuple(events) for key, events in result.items()}
    if (
        len(ordered_target_events) != 192
        or len(normalized) != 73
        or any(len(events) == 0 for events in normalized.values())
    ):
        raise CensusError("TASK7_V11_CENSUS_TARGET_EVENT_INVENTORY_MISMATCH")
    return normalized


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
        return {
            "LEGAL": "UPDATE",
            "ILLEGAL": "INSERT",
            "DIRECT_SQL": "DELETE",
        }[case_kind]
    if case_kind == "LEGAL":
        return "INSERT" if "INSERT" in events else "UPDATE"
    if case_kind == "ILLEGAL":
        return "UPDATE" if "UPDATE" in events else events[0]
    return "DELETE" if "DELETE" in events else "UPDATE"


def _postgres_trigger_inventory(
    connection: Connection,
    *,
    ordered_targets: Sequence[tuple[str, str, str]],
    expected_target_events: Mapping[
        tuple[str, str, str],
        Sequence[TriggerEvent],
    ],
) -> tuple[
    tuple[tuple[str, str, str, str], ...],
    tuple[tuple[Any, ...], ...],
]:
    """Prove the reviewed event bytes against the live 0037+scaffold catalog."""

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
    result: list[tuple[str, str, str, str]] = []
    projection: list[tuple[Any, ...]] = []
    for authority_id, table_name, guard_name in ordered_targets:
        record = records.get((table_name, guard_name))
        if record is None:
            raise CensusError(
                f"TASK7_V11_CENSUS_TRIGGER_MISSING:{table_name}.{guard_name}"
            )
        tgtype = int(record.tgtype)
        actual = tuple(
            event
            for event, bit in (
                ("INSERT", 4),
                ("UPDATE", 16),
                ("DELETE", 8),
            )
            if tgtype & bit
        )
        expected = tuple(expected_target_events[(authority_id, table_name, guard_name)])
        if actual != expected:
            raise CensusError(
                f"TASK7_V11_CENSUS_TRIGGER_EVENT_DRIFT:{table_name}.{guard_name}"
            )
        result.extend(
            (authority_id, table_name, guard_name, event) for event in actual
        )
        constraint = authority_id in {
            "current-source-exact-one-v1",
            "current-output-exact-one-v1",
            "current-receipt-equality-v1",
        }
        row = (
            authority_id,
            table_name,
            guard_name,
            "BEFORE" if tgtype & 2 else "AFTER",
            list(actual),
            "ROW" if tgtype & 1 else "STATEMENT",
            str(record.tgenabled),
            "CONSTRAINT" if bool(record.is_constraint) else "PLAIN",
            bool(record.tgdeferrable),
            bool(record.tginitdeferred),
            str(record.function_schema),
            str(record.function_name),
        )
        expected_row = (
            authority_id,
            table_name,
            guard_name,
            "AFTER" if constraint else "BEFORE",
            list(expected),
            "ROW",
            "O",
            "CONSTRAINT" if constraint else "PLAIN",
            constraint,
            constraint,
            "public",
            _AUTHORITY_FUNCTIONS[authority_id],
        )
        if row != expected_row:
            raise CensusError(
                f"TASK7_V11_CENSUS_TRIGGER_PROJECTION_DRIFT:{table_name}.{guard_name}"
            )
        projection.append(row)
    inventory = tuple(result)
    if len(inventory) != 192 or len(set(inventory)) != 192:
        raise CensusError("TASK7_V11_CENSUS_TRIGGER_EVENT_COVERAGE_INVALID")
    if len(projection) != 73:
        raise CensusError("TASK7_V11_CENSUS_TRIGGER_PROJECTION_INCOMPLETE")
    return inventory, tuple(projection)


def _native_source_values(
    *,
    table_name: str,
    tenant_id: str,
    deal_id: str,
    root_id: str,
    corpus_id: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "source_binding_state": "BOUND",
        "source_lineage_object_id": root_id,
        "source_corpus_receipt_id": corpus_id,
    }


def _source_root_values(
    *,
    tenant_id: str,
    deal_id: str,
    root_id: str,
    corpus_id: str,
    native_kind: str,
) -> dict[str, Any]:
    return {
        "source_lineage_object_id": root_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "corpus_receipt_id": corpus_id,
        "object_kind": native_kind,
        "source_version": "task7-v11-census-source-v1",
        "source_disposition": "CORPUS",
        "object_identity_sha256": "1" * 64,
        "content_sha256": "2" * 64,
        "corpus_receipt_sha256": "3" * 64,
        "write_origin": "RUNTIME",
    }


def _source_exact_one_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, int]]:
    native_table = (
        "data_room_packages"
        if table_name == "source_lineage_objects"
        else table_name
    )
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "root_id": str(uuid.uuid4()),
        "corpus_id": str(uuid.uuid4()),
    }
    native_values = _native_source_values(
        table_name=native_table,
        tenant_id=graph["tenant_id"],
        deal_id=graph["deal_id"],
        root_id=graph["root_id"],
        corpus_id=graph["corpus_id"],
    )
    root_values = _source_root_values(
        tenant_id=graph["tenant_id"],
        deal_id=graph["deal_id"],
        root_id=graph["root_id"],
        corpus_id=graph["corpus_id"],
        native_kind=_SOURCE_NATIVE_KIND[native_table],
    )
    target_values = root_values if table_name == "source_lineage_objects" else native_values
    counterpart_table = (
        native_table if table_name == "source_lineage_objects" else "source_lineage_objects"
    )
    counterpart_values = (
        native_values if table_name == "source_lineage_objects" else root_values
    )

    _set_replica(connection, True)
    if case_kind in {"LEGAL", "DIRECT_SQL"}:
        _insert_strict(
            connection,
            columns,
            counterpart_table,
            counterpart_values,
        )
    if case_kind != "LEGAL":
        _insert_strict(connection, columns, table_name, target_values)
    _set_replica(connection, False)

    if trigger_event == "INSERT":
        _insert_strict(connection, columns, table_name, target_values)
    elif trigger_event == "UPDATE":
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
    else:
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    _flush_deferred(connection)
    return "SOURCE_EXACT_ONE_EVENT", {}


def _native_output_values(
    *,
    table_name: str,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "run_output_object_id": graph["output_object_id"],
    }
    if table_name == "deterministic_calculations":
        values.update(
            {
                "producer_step_name": "METHODOLOGY_DETERMINISTIC_CALCULATION",
                "coverage_record_id": "task7-v11-census-coverage",
                "methodology_question_record_id": (
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
                ),
            }
        )
    elif table_name == "vector_embeddings":
        values.update(
            {
                "producer_step_name": "EXTRACT",
                "embedding_namespace": "internal_evidence",
            }
        )
    elif table_name == "muhasabah_records":
        values["source_step"] = "METHODOLOGY_EVIDENCE_TRUST_COURT"
    return values


def _insert_native_output(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    graph: Mapping[str, Any],
) -> str:
    persisted = _insert_strict(
        connection,
        columns,
        table_name,
        _native_output_values(table_name=table_name, graph=graph),
        returning=("run_output_object_kind",),
    )
    if persisted is None or persisted["run_output_object_kind"] is None:
        raise CensusError(f"TASK7_V11_NATIVE_KIND_NOT_MATERIALIZED:{table_name}")
    return str(persisted["run_output_object_kind"])


def _derived_native_hashes(
    connection: Connection,
    graph: Mapping[str, Any],
    *,
    output_kind: str,
) -> tuple[str, str]:
    rows = connection.execute(
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
    ).mappings().all()
    if len(rows) != 1 or str(rows[0]["output_kind"]) != output_kind:
        raise CensusError("TASK7_V11_NATIVE_HASH_DISPATCH_INCOMPLETE")
    return (
        str(rows[0]["expected_native_identity_sha256"]),
        str(rows[0]["expected_native_output_sha256"]),
    )


def _output_root_values(
    graph: Mapping[str, Any],
    *,
    output_kind: str,
    identity_sha256: str,
    output_sha256: str,
) -> dict[str, Any]:
    return {
        "output_object_id": graph["output_object_id"],
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "output_class": "NATIVE_ROW",
        "output_kind": output_kind,
        "output_schema_version": "task7-v11-census-v1",
        "native_identity_sha256": identity_sha256,
        "native_output_sha256": output_sha256,
        "pipeline_contract_version": "full-strict-v11",
        "pipeline_contract_hash": "a" * 64,
        "write_origin": "RUNTIME",
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _seed_valid_output_pair(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    native_table: str,
    graph: Mapping[str, Any],
) -> None:
    output_kind = _insert_native_output(
        connection,
        columns,
        table_name=native_table,
        graph=graph,
    )
    identity_sha256, output_sha256 = _derived_native_hashes(
        connection,
        graph,
        output_kind=output_kind,
    )
    _insert_strict(
        connection,
        columns,
        "run_output_objects",
        _output_root_values(
            graph,
            output_kind=output_kind,
            identity_sha256=identity_sha256,
            output_sha256=output_sha256,
        ),
    )


def _output_exact_one_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, int]]:
    native_table = (
        "methodology_coverage_records"
        if table_name == "run_output_objects"
        else table_name
    )
    graph = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "output_object_id": str(uuid.uuid4()),
    }
    _set_replica(connection, True)
    if case_kind == "LEGAL":
        if table_name == "run_output_objects":
            output_kind = _insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
            identity_sha256, output_sha256 = _derived_native_hashes(
                connection,
                graph,
                output_kind=output_kind,
            )
            target_values = _output_root_values(
                graph,
                output_kind=output_kind,
                identity_sha256=identity_sha256,
                output_sha256=output_sha256,
            )
            _set_replica(connection, False)
            _insert_strict(connection, columns, table_name, target_values)
        else:
            _set_replica(connection, False)
            output_kind = _insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
            identity_sha256, output_sha256 = _derived_native_hashes(
                connection,
                graph,
                output_kind=output_kind,
            )
            _set_replica(connection, True)
            _insert_strict(
                connection,
                columns,
                "run_output_objects",
                _output_root_values(
                    graph,
                    output_kind=output_kind,
                    identity_sha256=identity_sha256,
                    output_sha256=output_sha256,
                ),
            )
            _set_replica(connection, False)
    elif case_kind == "ILLEGAL":
        if table_name == "run_output_objects":
            _insert_strict(
                connection,
                columns,
                table_name,
                _output_root_values(
                    graph,
                    output_kind="METHODOLOGY_COVERAGE_IDENTITY",
                    identity_sha256="0" * 64,
                    output_sha256="0" * 64,
                ),
            )
        else:
            _insert_native_output(
                connection,
                columns,
                table_name=native_table,
                graph=graph,
            )
        _set_replica(connection, False)
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
    else:
        _seed_valid_output_pair(
            connection,
            columns,
            native_table=native_table,
            graph=graph,
        )
        _set_replica(connection, False)
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    _flush_deferred(connection)
    return "OUTPUT_EXACT_ONE_EVENT", {}


def _receipt_graph(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> dict[str, Any]:
    graph = _seed_run(connection, columns, status="RUNNING")
    graph["step_id"] = str(uuid.uuid4())
    return graph


def _step_values(
    graph: Mapping[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    return {
        "step_id": graph["step_id"],
        "tenant_id": graph["tenant_id"],
        "run_id": graph["run_id"],
        "step_name": "INGEST_CHECK",
        "step_order": 2,
        "status": status,
        "pipeline_contract_version": "full-strict-v11",
        "pipeline_contract_hash": "a" * 64,
        "semantic_rule_version": "2",
        "semantic_rule_hash": "b" * 64,
    }


def _receipt_root_values(
    graph: Mapping[str, Any],
    *,
    output_object_id: str,
) -> dict[str, Any]:
    return {
        "output_object_id": output_object_id,
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "output_class": "STEP_RECEIPT",
        "output_kind": "INGEST_CHECK_RECEIPT_V1",
        "output_schema_version": "task7-v11-census-v1",
        "native_identity_sha256": "1" * 64,
        "native_output_sha256": "2" * 64,
        "pipeline_contract_version": "full-strict-v11",
        "pipeline_contract_hash": "a" * 64,
        "write_origin": "RUNTIME",
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _step_output_values(
    graph: Mapping[str, Any],
    *,
    output_object_id: str,
    subrole: str,
) -> dict[str, Any]:
    return {
        "step_output_id": str(uuid.uuid4()),
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "step_id": graph["step_id"],
        "output_object_id": output_object_id,
        "step_name": "INGEST_CHECK",
        "subrole": subrole,
        "output_kind": "INGEST_CHECK_RECEIPT_V1",
        "output_ordinal": 0,
        "semantic_rule_version": "2",
        "semantic_rule_hash": "b" * 64,
        "summary_sha256": "2" * 64,
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _edge_values(
    graph: Mapping[str, Any],
    *,
    expected_edge_id: str,
) -> dict[str, Any]:
    return {
        "expected_edge_id": expected_edge_id,
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "producer_input_binding_id": str(uuid.uuid4()),
        "producer_output_object_id": None,
        "consumer_step_name": "INGEST_CHECK",
        "consumer_subrole": "__step__",
        "usage_kind": "SOURCE_INVENTORY",
        "contract_rule_id": "E001_SOURCE_INVENTORY_ADMISSION",
        "contract_rule_version": "1",
        "contract_rule_hash": "3" * 64,
        "edge_tuple_sha256": "4" * 64,
        "materialization_origin": "ADMISSION",
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _receipt_values(
    graph: Mapping[str, Any],
    *,
    expected_edge_id: str,
    consumer_subrole: str = "__step__",
) -> dict[str, Any]:
    return {
        "receipt_id": str(uuid.uuid4()),
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "expected_edge_id": expected_edge_id,
        "consumer_step_output_id": str(uuid.uuid4()),
        "consumer_output_object_id": str(uuid.uuid4()),
        "consumer_step_name": "INGEST_CHECK",
        "consumer_subrole": consumer_subrole,
        "usage_evidence_sha256": "5" * 64,
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _seed_orphan_receipt_root(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    graph: Mapping[str, Any],
) -> None:
    _insert_strict(
        connection,
        columns,
        "run_output_objects",
        _receipt_root_values(graph, output_object_id=str(uuid.uuid4())),
    )


def _manifest_values(
    graph: Mapping[str, Any],
    *,
    manifest_id: str,
    manifest_root: str,
    ordinal: int,
) -> dict[str, Any]:
    reconciliation = ordinal == 14
    return {
        "manifest_reference_id": str(uuid.uuid4()),
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
        "manifest_deliverable_id": manifest_id,
        "manifest_output_object_id": manifest_root,
        "run_output_object_id": str(uuid.uuid4()),
        "member_kind": "RECONCILIATION" if reconciliation else "ARTIFACT",
        "catalog_ordinal": None if reconciliation else ordinal,
        "member_step_output_id": str(uuid.uuid4()),
        "member_output_object_id": str(uuid.uuid4()),
        "member_identity_sha256": "1" * 64,
        "member_value_sha256": "2" * 64,
        "member_content_sha256": None if reconciliation else "3" * 64,
        "reference_sha256": "4" * 64,
        "lease_owner_id": None,
        "lease_fencing_token": 0,
    }


def _receipt_equality_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, int]]:
    graph = _receipt_graph(connection, columns)
    _set_replica(connection, True)

    if table_name == "run_steps":
        if case_kind == "LEGAL":
            target_values = _step_values(graph, status="PENDING")
        else:
            target_values = _step_values(
                graph,
                status="PENDING" if case_kind == "ILLEGAL" else "COMPLETED",
            )
            _insert_strict(connection, columns, table_name, target_values)
            if case_kind == "DIRECT_SQL":
                _seed_orphan_receipt_root(connection, columns, graph)
    elif table_name == "run_step_outputs":
        output_id = str(uuid.uuid4())
        _insert_strict(
            connection,
            columns,
            "run_steps",
            _step_values(
                graph,
                status="PENDING" if case_kind == "LEGAL" else "COMPLETED",
            ),
        )
        _insert_strict(
            connection,
            columns,
            "run_output_objects",
            _receipt_root_values(graph, output_object_id=output_id),
        )
        target_values = _step_output_values(
            graph,
            output_object_id=output_id,
            subrole="__step__" if case_kind != "ILLEGAL" else "wrong",
        )
        if case_kind != "LEGAL":
            _insert_strict(connection, columns, table_name, target_values)
    elif table_name == "run_output_objects":
        output_id = str(uuid.uuid4())
        _insert_strict(
            connection,
            columns,
            "run_steps",
            _step_values(
                graph,
                status="PENDING" if case_kind == "LEGAL" else "COMPLETED",
            ),
        )
        target_values = _receipt_root_values(graph, output_object_id=output_id)
        if case_kind == "LEGAL":
            _insert_strict(
                connection,
                columns,
                "run_step_outputs",
                _step_output_values(
                    graph,
                    output_object_id=output_id,
                    subrole="__step__",
                ),
            )
        else:
            _insert_strict(connection, columns, table_name, target_values)
            if case_kind == "DIRECT_SQL":
                _insert_strict(
                    connection,
                    columns,
                    "run_step_outputs",
                    _step_output_values(
                        graph,
                        output_object_id=output_id,
                        subrole="__step__",
                    ),
                )
    elif table_name == "run_expected_consumer_edges":
        edge_id = str(uuid.uuid4())
        _insert_strict(
            connection,
            columns,
            "run_steps",
            _step_values(
                graph,
                status="PENDING" if case_kind == "LEGAL" else "COMPLETED",
            ),
        )
        target_values = _edge_values(graph, expected_edge_id=edge_id)
        if case_kind != "LEGAL":
            _insert_strict(connection, columns, table_name, target_values)
            if case_kind == "DIRECT_SQL":
                _insert_strict(
                    connection,
                    columns,
                    "run_consumer_receipts",
                    _receipt_values(graph, expected_edge_id=edge_id),
                )
                _seed_orphan_receipt_root(connection, columns, graph)
    elif table_name == "run_consumer_receipts":
        edge_id = str(uuid.uuid4())
        _insert_strict(
            connection,
            columns,
            "run_steps",
            _step_values(
                graph,
                status="PENDING" if case_kind == "LEGAL" else "COMPLETED",
            ),
        )
        _insert_strict(
            connection,
            columns,
            "run_expected_consumer_edges",
            _edge_values(graph, expected_edge_id=edge_id),
        )
        target_values = _receipt_values(
            graph,
            expected_edge_id=edge_id,
            consumer_subrole="wrong" if case_kind == "ILLEGAL" else "__step__",
        )
        if case_kind != "LEGAL":
            _insert_strict(connection, columns, table_name, target_values)
    else:
        manifest_id = str(uuid.uuid4())
        manifest_root = str(uuid.uuid4())
        seed_count = 14 if case_kind == "LEGAL" else (15 if case_kind == "DIRECT_SQL" else 1)
        for ordinal in range(seed_count):
            _insert_strict(
                connection,
                columns,
                table_name,
                _manifest_values(
                    graph,
                    manifest_id=manifest_id,
                    manifest_root=manifest_root,
                    ordinal=ordinal,
                ),
            )
        target_values = _manifest_values(
            graph,
            manifest_id=manifest_id,
            manifest_root=manifest_root,
            ordinal=14 if case_kind == "LEGAL" else 0,
        )

    _set_replica(connection, False)
    if trigger_event == "INSERT":
        _insert_strict(connection, columns, table_name, target_values)
    elif trigger_event == "UPDATE":
        if table_name == "run_steps":
            connection.exec_driver_sql(
                "UPDATE public.run_steps SET status='COMPLETED'"
            )
        else:
            connection.exec_driver_sql(
                f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
            )
    else:
        if table_name == "deliverable_manifest_references":
            connection.exec_driver_sql(
                """
                DELETE FROM public.deliverable_manifest_references
                WHERE manifest_reference_id=(
                    SELECT manifest_reference_id
                    FROM public.deliverable_manifest_references
                    ORDER BY manifest_reference_id
                    LIMIT 1
                )
                """
            )
        else:
            connection.exec_driver_sql(
                f"DELETE FROM public.{_identifier(table_name)}"
            )
    _flush_deferred(connection)
    return "RECEIPT_EQUALITY_EVENT", {}


def _seed_strict_input_binding(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    package_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    proof_id = str(uuid.uuid4())
    corpus_sha256 = _digest(["task7-v11-strict-input-corpus", package_id])
    repeated_hash = _digest(["task7-v11-strict-proof"])
    _seed_tenant_deal(
        connection,
        columns,
        tenant_id=tenant_id,
        deal_id=deal_id,
    )
    _insert_strict(
        connection,
        columns,
        "data_room_packages",
        {
            "package_id": package_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "status": "SEALED",
            "metadata": {},
            "source_binding_state": "BOUND",
            "source_lineage_object_id": root_id,
        },
    )
    _insert_strict(
        connection,
        columns,
        "source_lineage_objects",
        {
            "source_lineage_object_id": root_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "corpus_receipt_id": package_id,
            "object_kind": "PACKAGE",
            "source_version": "task7-v11-input-source-v1",
            "source_disposition": "CORPUS",
            "object_identity_sha256": repeated_hash,
            "content_sha256": corpus_sha256,
            "corpus_receipt_sha256": corpus_sha256,
            "write_origin": "BACKFILL",
        },
    )
    _insert_strict(
        connection,
        columns,
        "strict_preflight_proofs",
        {
            "proof_id": proof_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "corpus_hash": corpus_sha256,
            "strict_profile_generation": "bootstrap-v1",
            "strict_profile_hmac": repeated_hash,
            "pipeline_contract_version": "full-strict-v11",
            "pipeline_contract_hash": "a" * 64,
            "expected_consumer_edge_rule_hash": repeated_hash,
            "static_decision_set_hash": repeated_hash,
            "methodology_version_hashes": [repeated_hash, repeated_hash],
            "promoted_pointer_hash": repeated_hash,
            "provider_receipt_set_hash": repeated_hash,
            "dependency_receipt_set_hash": repeated_hash,
            "binding_hash": repeated_hash,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    _insert_strict(
        connection,
        columns,
        "runs",
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "mode": "FULL",
            "status": "QUEUED",
            "idempotency_key": f"task7-v11-binding-{run_id}",
            "strict_preflight_proof_id": proof_id,
            "pipeline_contract_version": "full-strict-v11",
            "pipeline_contract_hash": "a" * 64,
            "pipeline_resume_state": "RESUMABLE",
            "pipeline_resume_error_code": None,
        },
    )
    connection.execute(
        text(
            """
            UPDATE public.strict_preflight_proofs
            SET consumed_at=statement_timestamp(),
                consumed_by_run_id=CAST(:run_id AS uuid)
            WHERE proof_id=CAST(:proof_id AS uuid)
            """
        ),
        {"run_id": run_id, "proof_id": proof_id},
    )
    preimage = [
        "run-input-binding-digest-v2",
        tenant_id,
        deal_id,
        run_id,
        "full-strict-v11",
        "a" * 64,
        package_id,
        root_id,
        "PACKAGE",
        "CORPUS",
        "INVENTORY",
        "task7-v11-input-source-v1",
        repeated_hash,
        corpus_sha256,
        corpus_sha256,
    ]
    values = {
        "binding_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "source_lineage_object_id": root_id,
        "source_object_kind": "PACKAGE",
        "source_disposition": "CORPUS",
        "binding_purpose": "INVENTORY",
        "corpus_receipt_id": package_id,
        "selected_source_version": "task7-v11-input-source-v1",
        "selected_identity_sha256": repeated_hash,
        "selected_content_sha256": corpus_sha256,
        "corpus_receipt_sha256": corpus_sha256,
        "pipeline_contract_version": "full-strict-v11",
        "pipeline_contract_hash": "a" * 64,
        "binding_sha256": _digest(preimage),
    }
    return values, {}


def _input_binding_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    _set_replica(connection, True)
    values, _seed_metadata = _seed_strict_input_binding(connection, columns)
    if case_kind != "LEGAL":
        _insert_strict(
            connection,
            columns,
            "run_input_bindings",
            values,
        )
    _set_replica(connection, False)
    if trigger_event == "INSERT":
        _insert_strict(
            connection,
            columns,
            "run_input_bindings",
            values,
        )
    elif trigger_event == "UPDATE":
        connection.exec_driver_sql(
            "UPDATE public.run_input_bindings SET binding_sha256=binding_sha256"
        )
    else:
        connection.exec_driver_sql("DELETE FROM public.run_input_bindings")
    if case_kind == "LEGAL":
        row = connection.execute(
            text(
                """
                SELECT r.status AS run_status,pkg.status AS package_status,
                       pkg.source_binding_state AS package_binding_state,
                       (p.consumed_by_run_id=r.run_id) AS proof_consumed,
                       p.pipeline_contract_version AS proof_pipeline_contract_version,
                       (
                         p.pipeline_contract_version=r.pipeline_contract_version
                         AND r.pipeline_contract_version=b.pipeline_contract_version
                       ) AS contract_version_match,
                       (
                         p.pipeline_contract_hash=r.pipeline_contract_hash
                         AND r.pipeline_contract_hash=b.pipeline_contract_hash
                       ) AS contract_hash_match,
                       (
                         root.object_identity_sha256=b.selected_identity_sha256
                         AND root.content_sha256=b.selected_content_sha256
                       ) AS source_hash_match,
                       ((p.corpus_hash=b.corpus_receipt_sha256)::integer
                        +(root.content_sha256=b.corpus_receipt_sha256)::integer
                        +(root.corpus_receipt_sha256=b.corpus_receipt_sha256)::integer
                        +(b.selected_content_sha256=b.corpus_receipt_sha256)::integer)
                        AS corpus_hash_equality_count
                FROM public.run_input_bindings b
                JOIN public.runs r
                  ON r.tenant_id=b.tenant_id AND r.deal_id=b.deal_id
                 AND r.run_id=b.run_id
                JOIN public.strict_preflight_proofs p
                  ON p.proof_id=r.strict_preflight_proof_id
                 AND p.consumed_by_run_id=r.run_id
                JOIN public.data_room_packages pkg
                  ON pkg.package_id=b.corpus_receipt_id
                 AND pkg.tenant_id=b.tenant_id AND pkg.deal_id=b.deal_id
                JOIN public.source_lineage_objects root
                  ON root.tenant_id=b.tenant_id AND root.deal_id=b.deal_id
                 AND root.source_lineage_object_id=b.source_lineage_object_id
                 AND root.object_kind=b.source_object_kind
                """
            )
        ).mappings().one()
        proof = {
            "schema_version": "task7-v11-strict-input-seed-proof-v1",
            "run_status": str(row["run_status"]),
            "package_status": str(row["package_status"]),
            "package_binding_state": str(row["package_binding_state"]),
            "proof_consumed": bool(row["proof_consumed"]),
            "proof_pipeline_contract_version": str(
                row["proof_pipeline_contract_version"]
            ),
            "contract_version_match": bool(row["contract_version_match"]),
            "contract_hash_match": bool(row["contract_hash_match"]),
            "source_hash_match": bool(row["source_hash_match"]),
            "corpus_hash_equality_count": int(
                row["corpus_hash_equality_count"]
            ),
        }
        if proof != {
            "schema_version": "task7-v11-strict-input-seed-proof-v1",
            "run_status": "QUEUED",
            "package_status": "SEALED",
            "package_binding_state": "BOUND",
            "proof_consumed": True,
            "proof_pipeline_contract_version": "full-strict-v11",
            "contract_version_match": True,
            "contract_hash_match": True,
            "source_hash_match": True,
            "corpus_hash_equality_count": 4,
        }:
            raise CensusError("TASK7_V11_STRICT_INPUT_SEED_PROOF_FAILED")
        return "STRICT_INPUT_BINDING_QUEUED_ADMISSION", {
            "strict_input_binding_queued_admission_count": 1,
            "strict_input_binding_seed_proof": proof,
        }
    return "STRICT_INPUT_BINDING_IMMUTABILITY", {}


def _append_target_values(
    *,
    table_name: str,
    graph: Mapping[str, Any],
    admission_edge: bool,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
    }
    if table_name == "run_output_objects":
        base["output_object_id"] = str(uuid.uuid4())
    elif table_name == "run_step_outputs":
        base["step_output_id"] = str(uuid.uuid4())
    elif table_name == "run_applicability_decisions":
        base["applicability_decision_id"] = str(uuid.uuid4())
    elif table_name == "run_expected_consumer_edges":
        base.update(
            {
                "expected_edge_id": str(uuid.uuid4()),
                "producer_input_binding_id": str(uuid.uuid4()),
                "producer_output_object_id": None,
                "consumer_step_name": "SOURCE_INVENTORY",
                "consumer_subrole": "__step__",
                "usage_kind": "SOURCE_INVENTORY",
                "contract_rule_id": "E001_SOURCE_INVENTORY_ADMISSION",
                "contract_rule_version": "1",
                "contract_rule_hash": "3" * 64,
                "edge_tuple_sha256": "4" * 64,
                "materialization_origin": "ADMISSION",
            }
        )
    elif table_name == "run_consumer_receipts":
        base["receipt_id"] = str(uuid.uuid4())
    else:
        base["manifest_reference_id"] = str(uuid.uuid4())
    if admission_edge:
        base.update({"lease_owner_id": None, "lease_fencing_token": 0})
    else:
        base.update(
            {
                "lease_owner_id": graph["lease_owner_id"],
                "lease_fencing_token": graph["lease_fencing_token"],
            }
        )
    return base


def _append_only_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    admission_edge = (
        table_name == "run_expected_consumer_edges" and case_kind == "LEGAL"
    )
    graph = _seed_run(
        connection,
        columns,
        status="QUEUED" if admission_edge else "RUNNING",
    )
    values = _append_target_values(
        table_name=table_name,
        graph=graph,
        admission_edge=admission_edge,
    )
    _set_replica(connection, True)
    if case_kind != "LEGAL":
        _insert_strict(connection, columns, table_name, values)
    _set_replica(connection, False)
    if not admission_edge:
        _set_task6_write_context(connection, graph)
    if trigger_event == "INSERT":
        _insert_strict(connection, columns, table_name, values)
    elif trigger_event == "UPDATE":
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
    else:
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    if admission_edge:
        count = int(
            connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.run_expected_consumer_edges e
                    JOIN public.runs r
                      ON r.tenant_id=e.tenant_id AND r.deal_id=e.deal_id
                     AND r.run_id=e.run_id
                    WHERE r.status='QUEUED'
                      AND e.materialization_origin='ADMISSION'
                      AND e.contract_rule_id='E001_SOURCE_INVENTORY_ADMISSION'
                      AND e.usage_kind='SOURCE_INVENTORY'
                      AND e.producer_input_binding_id IS NOT NULL
                      AND e.producer_output_object_id IS NULL
                      AND e.lease_owner_id IS NULL
                      AND e.lease_fencing_token=0
                    """
                )
            ).scalar_one()
        )
        if count != 1:
            raise CensusError("TASK7_V11_EXPECTED_EDGE_ADMISSION_PROOF_FAILED")
        return "EXPECTED_EDGE_QUEUED_ADMISSION", {
            "expected_edge_queued_admission_count": count,
            "expected_edge_seed_proof": {
                "schema_version": "task7-v11-expected-edge-seed-proof-v1",
                "run_status": "QUEUED",
                "materialization_origin": "ADMISSION",
                "contract_rule_id": "E001_SOURCE_INVENTORY_ADMISSION",
                "usage_kind": "SOURCE_INVENTORY",
                "lease_state": "ADMISSION_NULL_OWNER_ZERO_TOKEN",
            },
        }
    return "APPEND_ONLY_LEASE_EVENT", {}


def _unbound_source_values(
    *,
    table_name: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "source_binding_state": "UNBOUND",
        "source_lineage_object_id": None,
        "source_corpus_receipt_id": str(uuid.uuid4()),
    }
    if table_name == "data_room_packages":
        values.update(
            {
                "package_id": str(uuid.uuid4()),
                "status": "OPEN",
                "metadata": {},
            }
        )
    return values


def _source_bound_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    values = _unbound_source_values(table_name=table_name)
    root_id = str(uuid.uuid4())
    if case_kind == "ILLEGAL":
        values["source_binding_state"] = "BOUND"
        values["source_lineage_object_id"] = root_id
        if table_name == "data_room_packages":
            values["status"] = "SEALED"
    _set_replica(connection, True)
    _insert_strict(connection, columns, table_name, values)
    _set_replica(connection, False)
    if trigger_event == "UPDATE":
        if case_kind == "LEGAL":
            assignments = (
                "status='SEALED',source_binding_state='BOUND',"
                f"source_lineage_object_id='{root_id}'::uuid"
                if table_name == "data_room_packages"
                else (
                    "source_binding_state='BOUND',"
                    f"source_lineage_object_id='{root_id}'::uuid"
                )
            )
            connection.exec_driver_sql(
                f"UPDATE public.{_identifier(table_name)} SET {assignments}"
            )
        else:
            connection.exec_driver_sql(
                f"UPDATE public.{_identifier(table_name)} "
                "SET source_binding_state=source_binding_state"
            )
    else:
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    if case_kind == "LEGAL" and table_name == "data_room_packages":
        row = connection.execute(
            text(
                """
                SELECT status,source_binding_state,
                       source_lineage_object_id IS NOT NULL,
                       source_corpus_receipt_id IS NOT NULL
                FROM public.data_room_packages
                """
            )
        ).one()
        proof = tuple(row)
        if proof != ("SEALED", "BOUND", True, True):
            raise CensusError("TASK7_V11_PACKAGE_BINDING_PROOF_FAILED")
        return "PACKAGE_OPEN_TO_SEALED_BINDING", {
            "package_open_to_sealed_binding_count": 1,
            "package_binding_seed_proof": {
                "schema_version": "task7-v11-package-binding-proof-v1",
                "old_status": "OPEN",
                "new_status": "SEALED",
                "old_binding_state": "UNBOUND",
                "new_binding_state": "BOUND",
                "root_and_corpus_present": True,
            },
        }
    return "SOURCE_BOUND_EVENT", {}


def _output_bound_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    values = {
        "tenant_id": str(uuid.uuid4()),
        "deal_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "run_output_object_id": (
            str(uuid.uuid4()) if case_kind == "ILLEGAL" else None
        ),
    }
    _set_replica(connection, True)
    _insert_strict(connection, columns, table_name, values)
    _set_replica(connection, False)
    if trigger_event == "UPDATE":
        if case_kind == "LEGAL":
            statement = (
                f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
            )
        else:
            statement = (
                f"UPDATE public.{_identifier(table_name)} "
                f"SET run_output_object_id='{uuid.uuid4()}'::uuid"
            )
        connection.exec_driver_sql(statement)
    else:
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    return "OUTPUT_BOUND_EVENT", {}


def _immutable_contract_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
) -> tuple[str, dict[str, Any]]:
    if table_name == "runs":
        _seed_run(connection, columns, status="RUNNING")
    else:
        graph = _receipt_graph(connection, columns)
        _set_replica(connection, True)
        _insert_strict(
            connection,
            columns,
            "run_steps",
            _step_values(graph, status="PENDING"),
        )
        _set_replica(connection, False)
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
    connection.exec_driver_sql(statement)
    return "CONTRACT_IMMUTABILITY_UPDATE", {}


def _methodology_version_values(
    *,
    tenant_id: str,
    methodology_type: str,
    record_id: str,
    version_id: str,
    question_count: int,
) -> dict[str, Any]:
    financial = methodology_type == "financial_dd"
    repeated_hash = _digest(
        ["task7-v11-methodology-version", methodology_type, record_id]
    )
    return {
        "tenant_id": tenant_id,
        "methodology_version_record_id": record_id,
        "methodology_version_id": version_id,
        "methodology_id": methodology_type,
        "methodology_type": methodology_type,
        "version_label": "Task 7 v11 census frozen version",
        "source_type": "operator_workbook" if financial else "checked_in_template",
        "source_alias": "task7-v11-census-source",
        "source_sha256": repeated_hash,
        "source_byte_count": 1,
        "source_contract_hash": repeated_hash,
        "source_trace_digest": repeated_hash,
        "registry_hash": repeated_hash,
        "version_hash": repeated_hash,
        "question_count": question_count,
        "source_section_count": 1,
        "header_row": 1 if financial else None,
        "status": "FROZEN",
        "imported_by_actor_id": "task7-v11-census-importer",
        "import_audit_event_id": str(uuid.uuid4()),
        "frozen_by_actor_id": "task7-v11-census-reviewer",
        "freeze_audit_event_id": str(uuid.uuid4()),
        "frozen_at": datetime.now(UTC),
    }


def _seed_methodology_pair(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    seed_all_coverage: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    selection_id = str(uuid.uuid4())
    cdd_record_id = str(uuid.uuid4())
    fdd_record_id = str(uuid.uuid4())
    cdd_version_id = "cdd-task7-v11-census"
    fdd_version_id = "fdd-task7-v11-census"
    _seed_tenant_deal(
        connection,
        columns,
        tenant_id=tenant_id,
        deal_id=deal_id,
    )
    cdd_version = _methodology_version_values(
        tenant_id=tenant_id,
        methodology_type="commercial_dd",
        record_id=cdd_record_id,
        version_id=cdd_version_id,
        question_count=21,
    )
    fdd_version = _methodology_version_values(
        tenant_id=tenant_id,
        methodology_type="financial_dd",
        record_id=fdd_record_id,
        version_id=fdd_version_id,
        question_count=79,
    )
    _insert_strict(connection, columns, "methodology_versions", cdd_version)
    _insert_strict(connection, columns, "methodology_versions", fdd_version)
    _insert_strict(
        connection,
        columns,
        "runs",
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "mode": "FULL",
            "status": "RUNNING",
            "idempotency_key": f"task7-v11-methodology-{run_id}",
            "pipeline_contract_version": "full-strict-v11",
            "pipeline_contract_hash": "a" * 64,
            "pipeline_resume_state": "RESUMABLE",
        },
    )
    _insert_strict(
        connection,
        columns,
        "run_methodology_selections",
        {
            "selection_id": selection_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "commercial_dd_version_record_id": cdd_record_id,
            "commercial_methodology_version_id": cdd_version_id,
            "commercial_version_hash": cdd_version["version_hash"],
            "financial_dd_version_record_id": fdd_record_id,
            "financial_methodology_version_id": fdd_version_id,
            "financial_version_hash": fdd_version["version_hash"],
            "selection_hash": _digest(
                ["task7-v11-methodology-selection", selection_id]
            ),
            "selected_question_count": 100,
            "created_by_actor_id": "task7-v11-census-selector",
            "selection_audit_event_id": str(uuid.uuid4()),
        },
    )
    questions: list[dict[str, Any]] = []
    for methodology_type, version, count in (
        ("commercial_dd", cdd_version, 21),
        ("financial_dd", fdd_version, 79),
    ):
        for ordinal in range(1, count + 1):
            question_record_id = str(uuid.uuid4())
            question_id = (
                ("cdd" if methodology_type == "commercial_dd" else "fdd")
                + f"-q-{ordinal:03d}"
            )
            trace_hash = _digest(
                [
                    "task7-v11-methodology-question",
                    methodology_type,
                    ordinal,
                ]
            )
            question = {
                "tenant_id": tenant_id,
                "methodology_question_record_id": question_record_id,
                "methodology_version_record_id": version[
                    "methodology_version_record_id"
                ],
                "methodology_version_id": version["methodology_version_id"],
                "methodology_question_id": question_id,
                "methodology_id": methodology_type,
                "methodology_type": methodology_type,
                "question_ordinal": ordinal,
                "section": "Task 7 v11 census",
                "source_sheet_or_section": (
                    "Financial DD"
                    if methodology_type == "financial_dd"
                    else "Commercial DD"
                ),
                "source_row_number": (
                    ordinal if methodology_type == "financial_dd" else None
                ),
                "source_anchor_cell": (
                    f"A{ordinal}"
                    if methodology_type == "financial_dd"
                    else None
                ),
                "source_sha256": version["source_sha256"],
                "semantic_row_hash": trace_hash,
                "question_payload_hash": trace_hash,
                "question_payload": {"question_id": question_id},
            }
            _insert_strict(
                connection,
                columns,
                "methodology_questions",
                question,
            )
            questions.append(question)
    target_question = questions[-1]
    for ordinal, question in enumerate(
        questions if seed_all_coverage else questions[:-1],
        start=1,
    ):
        _insert_strict(
            connection,
            columns,
            "methodology_coverage_records",
            {
                "coverage_record_id": f"mc_{ordinal:024x}",
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "selection_id": selection_id,
                "methodology_version_record_id": question[
                    "methodology_version_record_id"
                ],
                "methodology_version_id": question["methodology_version_id"],
                "methodology_question_record_id": question[
                    "methodology_question_record_id"
                ],
                "methodology_question_id": question["methodology_question_id"],
                "methodology_type": question["methodology_type"],
                "status": "not_started",
                "evidence_links": [],
                "conflict_ids": [],
                "defect_ids": [],
                "run_output_object_id": str(uuid.uuid4()),
            },
        )
    target_values = {
        "coverage_record_id": "mc_" + "f" * 24,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "selection_id": selection_id,
        "methodology_version_record_id": target_question[
            "methodology_version_record_id"
        ],
        "methodology_version_id": target_question["methodology_version_id"],
        "methodology_question_record_id": target_question[
            "methodology_question_record_id"
        ],
        "methodology_question_id": target_question["methodology_question_id"],
        "methodology_type": target_question["methodology_type"],
        "status": "not_started",
        "evidence_links": [],
        "conflict_ids": [],
        "defect_ids": [],
        "run_output_object_id": str(uuid.uuid4()),
    }
    return target_values, {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "selection_id": selection_id,
    }


def _methodology_seed_projection(
    connection: Connection,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM public.run_methodology_selections s
               WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                 AND s.run_id=CAST(:run_id AS uuid)) AS selection_count,
              (SELECT max(selected_question_count)
               FROM public.run_methodology_selections s
               WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                 AND s.run_id=CAST(:run_id AS uuid)) AS selected_question_count,
              (SELECT count(*) FROM public.methodology_versions v
               WHERE v.tenant_id=CAST(:tenant_id AS uuid)
                 AND v.status='FROZEN') AS frozen_version_count,
              (SELECT count(*) FROM public.methodology_versions v
               WHERE v.tenant_id=CAST(:tenant_id AS uuid)
                 AND v.methodology_type='commercial_dd') AS commercial_version_count,
              (SELECT count(*) FROM public.methodology_versions v
               WHERE v.tenant_id=CAST(:tenant_id AS uuid)
                 AND v.methodology_type='financial_dd') AS financial_version_count,
              (SELECT count(*) FROM public.methodology_questions q
               WHERE q.tenant_id=CAST(:tenant_id AS uuid)
                 AND q.methodology_type='commercial_dd') AS commercial_question_count,
              (SELECT count(*) FROM public.methodology_questions q
               WHERE q.tenant_id=CAST(:tenant_id AS uuid)
                 AND q.methodology_type='financial_dd') AS financial_question_count,
              (SELECT count(*) FROM public.methodology_coverage_records c
               WHERE c.tenant_id=CAST(:tenant_id AS uuid)
                 AND c.run_id=CAST(:run_id AS uuid)) AS coverage_count,
              (SELECT count(*)
               FROM public.methodology_coverage_records c
               JOIN public.run_methodology_selections s
                 ON s.tenant_id=c.tenant_id AND s.deal_id=c.deal_id
                AND s.run_id=c.run_id AND s.selection_id=c.selection_id
               JOIN public.methodology_questions q
                 ON q.tenant_id=c.tenant_id
                AND q.methodology_question_record_id=c.methodology_question_record_id
                AND q.methodology_version_record_id=s.commercial_dd_version_record_id
               WHERE c.tenant_id=CAST(:tenant_id AS uuid)
                 AND c.run_id=CAST(:run_id AS uuid)
                 AND c.methodology_type='commercial_dd') AS commercial_lineage_match_count,
              (SELECT count(*)
               FROM public.methodology_coverage_records c
               JOIN public.run_methodology_selections s
                 ON s.tenant_id=c.tenant_id AND s.deal_id=c.deal_id
                AND s.run_id=c.run_id AND s.selection_id=c.selection_id
               JOIN public.methodology_questions q
                 ON q.tenant_id=c.tenant_id
                AND q.methodology_question_record_id=c.methodology_question_record_id
                AND q.methodology_version_record_id=s.financial_dd_version_record_id
               WHERE c.tenant_id=CAST(:tenant_id AS uuid)
                 AND c.run_id=CAST(:run_id AS uuid)
                 AND c.methodology_type='financial_dd') AS financial_lineage_match_count,
              (SELECT s.commercial_version_hash=v.version_hash
               FROM public.run_methodology_selections s
               JOIN public.methodology_versions v
                 ON v.tenant_id=s.tenant_id
                AND v.methodology_version_record_id=s.commercial_dd_version_record_id
               WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                 AND s.run_id=CAST(:run_id AS uuid))
                 AS commercial_version_hash_match,
              (SELECT s.financial_version_hash=v.version_hash
               FROM public.run_methodology_selections s
               JOIN public.methodology_versions v
                 ON v.tenant_id=s.tenant_id
                AND v.methodology_version_record_id=s.financial_dd_version_record_id
               WHERE s.tenant_id=CAST(:tenant_id AS uuid)
                 AND s.run_id=CAST(:run_id AS uuid))
                 AS financial_version_hash_match,
              (SELECT count(*)
               FROM public.methodology_questions q
               JOIN public.methodology_versions v
                 ON v.tenant_id=q.tenant_id
                AND v.methodology_version_record_id=q.methodology_version_record_id
               WHERE q.tenant_id=CAST(:tenant_id AS uuid)
                 AND q.methodology_type='commercial_dd'
                 AND q.source_sha256=v.source_sha256)
                 AS commercial_source_hash_match_count,
              (SELECT count(*)
               FROM public.methodology_questions q
               JOIN public.methodology_versions v
                 ON v.tenant_id=q.tenant_id
                AND v.methodology_version_record_id=q.methodology_version_record_id
               WHERE q.tenant_id=CAST(:tenant_id AS uuid)
                 AND q.methodology_type='financial_dd'
                 AND q.source_sha256=v.source_sha256)
                 AS financial_source_hash_match_count
            """
        ),
        dict(graph),
    ).mappings().one()
    return {
        "schema_version": "task7-v11-methodology-seed-proof-v1",
        **{
            key: (
                bool(value)
                if key.endswith("_version_hash_match")
                else int(value)
            )
            for key, value in row.items()
        },
    }


def _task5_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    _set_replica(connection, True)
    target_values, graph = _seed_methodology_pair(
        connection,
        columns,
        seed_all_coverage=case_kind != "LEGAL",
    )
    _set_replica(connection, False)
    if trigger_event == "INSERT":
        _insert_strict(
            connection,
            columns,
            "methodology_coverage_records",
            target_values,
        )
    elif trigger_event == "UPDATE":
        connection.exec_driver_sql(
            "UPDATE public.methodology_coverage_records "
            "SET coverage_record_id='changed-task7-v11'"
        )
    else:
        connection.exec_driver_sql(
            "DELETE FROM public.methodology_coverage_records"
        )
    if case_kind == "LEGAL":
        projection = _methodology_seed_projection(connection, graph)
        if projection != {
            "schema_version": "task7-v11-methodology-seed-proof-v1",
            "selection_count": 1,
            "selected_question_count": 100,
            "frozen_version_count": 2,
            "commercial_version_count": 1,
            "financial_version_count": 1,
            "commercial_question_count": 21,
            "financial_question_count": 79,
            "coverage_count": 100,
            "commercial_lineage_match_count": 21,
            "financial_lineage_match_count": 79,
            "commercial_version_hash_match": True,
            "financial_version_hash_match": True,
            "commercial_source_hash_match_count": 21,
            "financial_source_hash_match_count": 79,
        }:
            raise CensusError("TASK7_V11_METHODOLOGY_SEED_PROOF_FAILED")
        return "METHODOLOGY_FROZEN_PAIR_INSERT", {
            "methodology_commercial_question_count": 21,
            "methodology_financial_question_count": 79,
            "methodology_coverage_count": 100,
            "methodology_seed_proof": projection,
        }
    return "METHODOLOGY_COVERAGE_IMMUTABILITY", {}


def _task6_values(
    *,
    table_name: str,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tenant_id": graph["tenant_id"],
        "deal_id": graph["deal_id"],
        "run_id": graph["run_id"],
    }
    identifiers = {
        "methodology_extraction_tasks": ("task_record_id", str(uuid.uuid4())),
        "methodology_extraction_task_spans": (
            "task_span_record_id",
            str(uuid.uuid4()),
        ),
        "methodology_extraction_results": ("result_id", str(uuid.uuid4())),
        "methodology_result_factual_outputs": (
            "factual_output_record_id",
            str(uuid.uuid4()),
        ),
        "methodology_result_claim_links": ("claim_link_id", str(uuid.uuid4())),
        "methodology_result_evidence_links": (
            "evidence_link_id",
            str(uuid.uuid4()),
        ),
        "methodology_result_sanad_links": ("sanad_link_id", str(uuid.uuid4())),
        "methodology_result_defect_links": ("defect_link_id", str(uuid.uuid4())),
    }
    name, value = identifiers[table_name]
    values[name] = value
    if table_name == "methodology_extraction_task_spans":
        values.update(
            {
                "task_record_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "span_id": str(uuid.uuid4()),
                "span_ordinal": 1,
                "content_sha256": _digest(
                    ["task7-v11-task-span-content", value]
                ),
                "locator_sha256": _digest(
                    ["task7-v11-task-span-locator", value]
                ),
            }
        )
    if table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS:
        values["run_output_object_id"] = None
    return values


def _seed_task6_step(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    graph: dict[str, Any],
) -> None:
    graph["step_id"] = str(uuid.uuid4())
    _insert_strict(
        connection,
        columns,
        "run_steps",
        {
            "step_id": graph["step_id"],
            "tenant_id": graph["tenant_id"],
            "run_id": graph["run_id"],
            "step_name": "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
            "step_order": 6,
            "status": "RUNNING",
            "pipeline_contract_version": "full-strict-v11",
            "pipeline_contract_hash": "a" * 64,
            "semantic_rule_version": "2",
            "semantic_rule_hash": "b" * 64,
        },
    )


def _task6_output_update(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    graph: dict[str, Any],
    values: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str, bool], ...]:
    _set_replica(connection, True)
    _seed_task6_step(connection, columns, graph)
    _insert_strict(connection, columns, table_name, values)
    generated = connection.execute(
        text(
            f"SELECT run_output_object_class,run_output_object_kind "
            f"FROM public.{_identifier(table_name)}"
        )
    ).one()
    output_object_id = str(uuid.uuid4())
    _insert_strict(
        connection,
        columns,
        "run_output_objects",
        {
            "output_object_id": output_object_id,
            "tenant_id": graph["tenant_id"],
            "deal_id": graph["deal_id"],
            "run_id": graph["run_id"],
            "output_class": str(generated.run_output_object_class),
            "output_kind": str(generated.run_output_object_kind),
            "output_schema_version": "task7-v11-task6-output-v1",
            "native_identity_sha256": "1" * 64,
            "native_output_sha256": "2" * 64,
            "pipeline_contract_version": "full-strict-v11",
            "pipeline_contract_hash": "a" * 64,
            "write_origin": "RUNTIME",
            "lease_owner_id": graph["lease_owner_id"],
            "lease_fencing_token": graph["lease_fencing_token"],
        },
    )
    _set_replica(connection, False)
    guc_projection = _set_task6_write_context(connection, graph)
    connection.execute(
        text(
            f"UPDATE public.{_identifier(table_name)} "
            "SET run_output_object_id=CAST(:output_object_id AS uuid)"
        ),
        {"output_object_id": output_object_id},
    )
    attached = int(
        connection.execute(
            text(
                f"SELECT count(*) FROM public.{_identifier(table_name)} "
                "WHERE run_output_object_id=CAST(:output_object_id AS uuid)"
            ),
            {"output_object_id": output_object_id},
        ).scalar_one()
    )
    if attached != 1:
        raise CensusError("TASK7_V11_TASK6_OUTPUT_UPDATE_PROOF_FAILED")
    return guc_projection


def _task6_case(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    graph = _seed_run(connection, columns, status="RUNNING")
    values = _task6_values(table_name=table_name, graph=graph)
    if case_kind == "LEGAL" and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS:
        guc_projection = _task6_output_update(
            connection,
            columns,
            table_name=table_name,
            graph=graph,
            values=values,
        )
        return "TASK6_OUTPUT_ROOT_ATTACH_UPDATE", {
            "task6_output_root_update_acceptance_count": 1,
            "task6_guc_projection_tuple": guc_projection,
        }

    if case_kind == "LEGAL":
        _set_task6_write_context(connection, graph)
        _insert_strict(connection, columns, table_name, values)
        return "TASK6_LEASED_INSERT", {}

    if (
        case_kind == "ILLEGAL"
        and table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS
    ):
        fenced_graph = dict(graph)
        fenced_graph["lease_fencing_token"] = (
            int(graph["lease_fencing_token"]) + 1
        )
        _set_task6_write_context(connection, fenced_graph)
        _insert_strict(connection, columns, table_name, values)
        raise CensusError("TASK7_V11_TASK6_FENCED_INSERT_ACCEPTED")

    _set_replica(connection, True)
    _insert_strict(connection, columns, table_name, values)
    _set_replica(connection, False)
    _set_task6_write_context(connection, graph)
    if trigger_event == "UPDATE":
        connection.exec_driver_sql(
            f"UPDATE public.{_identifier(table_name)} SET tenant_id=tenant_id"
        )
    else:
        connection.exec_driver_sql(f"DELETE FROM public.{_identifier(table_name)}")
    scenario = (
        "TASK6_SPAN_IMMUTABLE_UPDATE"
        if table_name == "methodology_extraction_task_spans"
        and trigger_event == "UPDATE"
        else "TASK6_IMMUTABILITY_EVENT"
    )
    return scenario, {}


def _scenario_id(
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
) -> str:
    if (
        authority_id == "current-input-binding-admission-v1"
        and case_kind == "LEGAL"
    ):
        return "STRICT_INPUT_BINDING_QUEUED_ADMISSION"
    if (
        authority_id == "current-append-only-lease-v1"
        and table_name == "run_expected_consumer_edges"
        and case_kind == "LEGAL"
    ):
        return "EXPECTED_EDGE_QUEUED_ADMISSION"
    if (
        authority_id == "current-source-bound-v1"
        and table_name == "data_room_packages"
        and case_kind == "LEGAL"
    ):
        return "PACKAGE_OPEN_TO_SEALED_BINDING"
    if authority_id == "current-task5-coverage-replacement-v1":
        return (
            "METHODOLOGY_FROZEN_PAIR_INSERT"
            if case_kind == "LEGAL"
            else "METHODOLOGY_COVERAGE_IMMUTABILITY"
        )
    if authority_id == "current-task6-immutable-replacement-v1":
        if table_name in _TASK6_OUTPUT_ROOT_UPDATE_TARGETS and case_kind == "LEGAL":
            return "TASK6_OUTPUT_ROOT_ATTACH_UPDATE"
        if (
            table_name == "methodology_extraction_task_spans"
            and case_kind == "ILLEGAL"
        ):
            return "TASK6_SPAN_IMMUTABLE_UPDATE"
        if case_kind == "LEGAL":
            return "TASK6_LEASED_INSERT"
        return "TASK6_IMMUTABILITY_EVENT"
    return {
        "current-source-exact-one-v1": "SOURCE_EXACT_ONE_EVENT",
        "current-output-exact-one-v1": "OUTPUT_EXACT_ONE_EVENT",
        "current-receipt-equality-v1": "RECEIPT_EQUALITY_EVENT",
        "current-input-binding-admission-v1": "STRICT_INPUT_BINDING_IMMUTABILITY",
        "current-append-only-lease-v1": "APPEND_ONLY_LEASE_EVENT",
        "current-source-bound-v1": "SOURCE_BOUND_EVENT",
        "current-output-bound-v1": "OUTPUT_BOUND_EVENT",
        "current-runs-contract-immutable-v1": "CONTRACT_IMMUTABILITY_UPDATE",
        "current-run-steps-contract-immutable-v1": "CONTRACT_IMMUTABILITY_UPDATE",
    }[authority_id]


def _execute_case_operation(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
    *,
    authority_id: str,
    table_name: str,
    case_kind: CaseKind,
    trigger_event: TriggerEvent,
) -> tuple[str, dict[str, Any]]:
    if authority_id == "current-source-exact-one-v1":
        return _source_exact_one_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-output-exact-one-v1":
        return _output_exact_one_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-receipt-equality-v1":
        return _receipt_equality_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-input-binding-admission-v1":
        return _input_binding_case(
            connection,
            columns,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-append-only-lease-v1":
        return _append_only_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-source-bound-v1":
        return _source_bound_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-output-bound-v1":
        return _output_bound_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id in {
        "current-runs-contract-immutable-v1",
        "current-run-steps-contract-immutable-v1",
    }:
        return _immutable_contract_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
        )
    if authority_id == "current-task5-coverage-replacement-v1":
        return _task5_case(
            connection,
            columns,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    if authority_id == "current-task6-immutable-replacement-v1":
        return _task6_case(
            connection,
            columns,
            table_name=table_name,
            case_kind=case_kind,
            trigger_event=trigger_event,
        )
    raise CensusError(f"TASK7_V11_CENSUS_AUTHORITY_UNSUPPORTED:{authority_id}")


def _run_one_case(
    *,
    engine: Any,
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
    error: DBAPIError | None = None
    metrics: dict[str, Any] = {}
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            disable_all_current_user_triggers(
                connection,
                cast(dict[str, Any], artifact),
            )
            enable_target_trigger(connection, table_name, guard_name)
            try:
                scenario, metrics = _execute_case_operation(
                    connection,
                    columns,
                    authority_id=authority_id,
                    table_name=table_name,
                    case_kind=case_kind,
                    trigger_event=trigger_event,
                )
                if scenario != expected_scenario:
                    raise CensusError(
                        f"TASK7_V11_CENSUS_SCENARIO_DRIFT:{expected_scenario}"
                    )
            except DBAPIError as exc:
                error = exc
        finally:
            transaction.rollback()

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
        "execution_path": (
            "DIRECT_SQL" if case_kind == "DIRECT_SQL" else "GUARDED_OPERATION"
        ),
    }
    if expected_rejection:
        if error is None:
            raise CensusError(
                "TASK7_V11_CENSUS_ILLEGAL_CASE_ACCEPTED:"
                f"{authority_id}:{table_name}:{guard_name}:{case_kind}"
            )
        return (
            {
                **base,
                "outcome": "REJECTED",
                "sqlstate": _sqlstate(error),
                "authority_rejection_marker": _rejection_marker(
                    error,
                    authority_id,
                ),
                "rejection_provenance": "HASH_BOUND_AUTHORITY",
            },
            {},
        )
    if error is not None:
        raise CensusError(
            "TASK7_V11_CENSUS_LEGAL_CASE_REJECTED:"
            f"{authority_id}:{table_name}:{guard_name}:"
            f"SQLSTATE={_sqlstate(error)}"
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


def _fixture_rejection_proofs(
    connection: Connection,
    columns: Mapping[str, Mapping[str, tuple[str, bool]]],
) -> tuple[tuple[str, str, str, str], ...]:
    """Exercise strict fixture rejection through the real insert seam."""

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
            _insert_strict(
                connection,
                columns,
                table_name,
                values,
            )
        except CensusError as exc:
            result.append((kind, table_name, field, str(exc)))
        else:
            raise CensusError(
                f"TASK7_V11_CENSUS_FIXTURE_REJECTION_MISSING:{table_name}.{field}"
            )
    return tuple(result)
