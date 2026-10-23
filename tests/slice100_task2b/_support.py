"""Disposable-PostgreSQL support for the Task 2b RLS proof."""

from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

import idis.persistence.migrations as migrations_pkg

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_TABLES = (
    "audit_events",
    "break_glass_grants",
    "byok_policies",
    "calc_sanads",
    "claims",
    "data_room_package_files",
    "data_room_packages",
    "deal_assignments",
    "deals",
    "debate_sessions",
    "defects",
    "deliverables",
    "deterministic_calculations",
    "document_artifacts",
    "document_spans",
    "documents",
    "enrichment_credentials",
    "enrichment_rights_approvals",
    "erasure_requests",
    "evidence_items",
    "evidence_trust_findings",
    "group_memberships",
    "groups",
    "human_gate_actions",
    "human_gates",
    "idempotency_records",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "legal_holds",
    "muhasabah_records",
    "overrides",
    "provider_budget_policies",
    "provider_budget_usage",
    "run_steps",
    "runs",
    "sanads",
    "strict_preflight_proofs",
    "tenants",
    "validated_evidence_packages",
    "vector_embeddings",
    "webhook_delivery_attempts",
    "webhooks",
)

SEED_TABLE_ORDER = (
    "tenants",
    "audit_events",
    "deals",
    "break_glass_grants",
    "byok_policies",
    "groups",
    "group_memberships",
    "deal_assignments",
    "legal_holds",
    "erasure_requests",
    "webhooks",
    "webhook_delivery_attempts",
    "document_artifacts",
    "documents",
    "document_spans",
    "deterministic_calculations",
    "calc_sanads",
    "claims",
    "sanads",
    "defects",
    "data_room_packages",
    "data_room_package_files",
    "strict_preflight_proofs",
    "runs",
    "run_steps",
    "debate_sessions",
    "deliverables",
    "human_gates",
    "human_gate_actions",
    "overrides",
    "evidence_items",
    "evidence_trust_findings",
    "muhasabah_records",
    "validated_evidence_packages",
    "layer2_ic_challenges",
    "layer2_ic_findings",
    "enrichment_credentials",
    "enrichment_rights_approvals",
    "provider_budget_policies",
    "provider_budget_usage",
    "idempotency_records",
    "vector_embeddings",
)
assert frozenset(SEED_TABLE_ORDER) == frozenset(TENANT_TABLES)


@dataclass(frozen=True)
class DisposablePostgres:
    """Isolated database plus a deliberately unprivileged application role."""

    admin_engine: Engine
    app_engine: Engine
    database_name: str
    role_name: str


def _required_admin_url() -> URL:
    configured = os.environ.get(ADMIN_URL_ENV)
    if configured:
        return make_url(configured)
    message = f"Task 2b PostgreSQL proof requires {ADMIN_URL_ENV}"
    if os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1":
        pytest.fail(f"REQUIRED: {message} ({REQUIRE_POSTGRES_ENV}=1)")
    pytest.skip(message)


def _safe_generated_identifier(prefix: str) -> str:
    identifier = f"{prefix}_{uuid.uuid4().hex[:16]}"
    assert re.fullmatch(r"[a-z][a-z0-9_]+", identifier)
    return identifier


@contextmanager
def disposable_postgres() -> Iterator[DisposablePostgres]:
    """Create a fresh database and NOSUPERUSER/NOBYPASSRLS login role."""
    admin_url = _required_admin_url()
    maintenance_url = admin_url.set(database="postgres")
    database_name = _safe_generated_identifier("idis_t2b_db")
    role_name = _safe_generated_identifier("idis_t2b_app")
    password = secrets.token_hex(24)
    maintenance = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    admin_engine: Engine | None = None
    app_engine: Engine | None = None
    try:
        with maintenance.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE ROLE \"{role_name}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            )
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        target_admin_url = admin_url.set(database=database_name)
        target_app_url = target_admin_url.set(username=role_name, password=password)
        admin_engine = create_engine(target_admin_url)
        app_engine = create_engine(target_app_url)
        yield DisposablePostgres(
            admin_engine=admin_engine,
            app_engine=app_engine,
            database_name=database_name,
            role_name=role_name,
        )
    finally:
        if app_engine is not None:
            app_engine.dispose()
        if admin_engine is not None:
            admin_engine.dispose()
        with maintenance.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
            connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role_name}"')
        maintenance.dispose()


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    return config


def migrate(engine: Engine, target: str) -> None:
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, target)


def downgrade(engine: Engine, target: str) -> None:
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, target)


def grant_application_access(database: DisposablePostgres) -> None:
    role = database.role_name
    with database.admin_engine.begin() as connection:
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
        )
        connection.exec_driver_sql(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'
        )


def tenant_catalog_snapshot(connection: Connection) -> tuple[tuple[Any, ...], ...]:
    """Capture flags and complete policy definitions for every tenant table."""
    rows = connection.execute(
        text(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   p.polname,
                   p.polpermissive,
                   p.polcmd,
                   p.polroles::text,
                   pg_get_expr(p.polqual, p.polrelid),
                   pg_get_expr(p.polwithcheck, p.polrelid)
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy AS p ON p.polrelid = c.oid
            WHERE n.nspname = 'public'
              AND c.relname IN (
                  SELECT table_name
                  FROM information_schema.columns
                  WHERE table_schema = 'public' AND column_name = 'tenant_id'
              )
            ORDER BY c.relname, p.polname
            """
        )
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def tenant_table_inventory(connection: Connection) -> tuple[str, ...]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'tenant_id'
                ORDER BY table_name
                """
            )
        ).scalars()
    )


def _seed_text(table: str, column: str, shared_values: dict[str, str]) -> str:
    status = {
        "enrichment_rights_approvals": "APPROVED",
        "erasure_requests": "REQUESTED",
        "layer2_ic_challenges": "completed",
        "validated_evidence_packages": "completed",
    }
    explicit = {
        ("byok_policies", "key_state"): "ACTIVE",
        ("claims", "claim_class"): "FACT",
        ("data_room_package_files", "file_status"): "PENDING",
        ("data_room_package_files", "support_status"): "SUPPORTED",
        ("data_room_package_files", "triage_status"): "READY",
        ("deal_assignments", "assignee_type"): "ACTOR",
        ("document_artifacts", "doc_type"): "DATA_ROOM_FILE",
        ("document_spans", "span_type"): "PAGE_TEXT",
        ("documents", "doc_type"): "PDF",
        ("enrichment_credentials", "connector_id"): "fixture_connector",
        ("enrichment_credentials", "ciphertext"): "v2:fixture",
        ("enrichment_rights_approvals", "provider_id"): "wayback",
        ("enrichment_rights_approvals", "environment"): "PROD",
        ("enrichment_rights_approvals", "rights_class"): "YELLOW",
        ("enrichment_rights_approvals", "license_scope"): "attribution",
        ("human_gate_actions", "action"): "APPROVE",
        ("legal_holds", "target_type"): "DEAL",
        ("provider_budget_policies", "component_id"): "anthropic",
        ("provider_budget_policies", "currency"): "USD",
        ("provider_budget_policies", "policy_version"): "fixture-v1",
        ("provider_budget_usage", "provider"): "anthropic",
        ("runs", "mode"): "FULL",
        ("webhooks", "url"): "https://example.invalid/task2b",
    }
    if (table, column) in explicit:
        return explicit[(table, column)]
    if column == "status":
        return status.get(table, "PENDING")
    if (
        column.endswith("_hash")
        or column.endswith("_sha256")
        or column
        in {
            "binding_hash",
            "content_hash",
            "corpus_hash",
            "dependency_receipt_set_hash",
            "envelope_hash",
            "evidence_hash",
            "expected_consumer_edge_rule_hash",
            "formula_hash",
            "key_alias_sha256",
            "path_hash",
            "payload_hash",
            "payload_sha256",
            "policy_hash",
            "promoted_pointer_hash",
            "provider_receipt_set_hash",
            "public_key_hash",
            "reason_hash",
            "reproducibility_hash",
            "static_decision_set_hash",
            "strict_profile_hmac",
            "terms_version_hash",
            "token_sha256",
        }
    ):
        return "a" * 64
    if column == "pipeline_contract_version":
        return "full-strict-v2"
    if column == "strict_profile_generation":
        return "acceptance-v1"
    if column == "record_timestamp":
        return datetime.now(UTC).isoformat()
    if column == "group_id":
        return shared_values.setdefault("group_id", "task2b-fixture-group")
    if column == "primary_evidence_id":
        return shared_values.setdefault("evidence_id", str(uuid.uuid4()))
    if column.endswith("_id"):
        return str(uuid.uuid4())
    return "fixture"


def _seed_value(
    table: str,
    column: str,
    udt_name: str,
    *,
    tenant_id: str,
    shared_values: dict[str, str],
) -> Any:
    now = datetime.now(UTC)
    if column == "tenant_id":
        raise AssertionError("tenant_id is supplied explicitly")
    if udt_name == "uuid":
        identity = {
            "import_audit_event_id": "event_id",
            "source_span_id": "span_id",
        }.get(column, column)
        return shared_values.setdefault(identity, str(uuid.uuid4()))
    if udt_name in {"text", "varchar"}:
        return _seed_text(table, column, shared_values)
    if udt_name in {"int2", "int4", "int8"}:
        if column == "embedding_dimensions":
            return 1536
        if column in {"key_alias_length", "reason_length"}:
            return 64
        if column == "status_code":
            return 200
        return 1
    if udt_name in {"float4", "float8"}:
        return 0.5
    if udt_name == "numeric":
        return Decimal("1")
    if udt_name in {"timestamp", "timestamptz"}:
        if column == "expires_at":
            return now + timedelta(days=1)
        return now
    if udt_name == "jsonb":
        if table == "audit_events" and column == "event":
            return json.dumps(
                {
                    "event_id": shared_values["event_id"],
                    "occurred_at": now.isoformat().replace("+00:00", "Z"),
                    "tenant_id": tenant_id,
                    "actor": {"actor_type": "SERVICE", "actor_id": "task2b-seeder"},
                    "request": {
                        "request_id": "task2b-seed",
                        "method": "POST",
                        "path": "/internal/task2b-seed",
                        "status_code": 200,
                    },
                    "resource": {"resource_type": "integration", "resource_id": "task2b"},
                    "event_type": "integration.task2b.seeded",
                    "severity": "LOW",
                    "summary": "Task 2b policy seed",
                    "payload": {"refs": []},
                }
            )
        if column in {"input_claim_ids"}:
            return json.dumps([])
        if column == "methodology_version_hashes":
            return json.dumps(["b" * 64])
        return json.dumps({})
    if udt_name == "bytea":
        return b"{}"
    if udt_name == "_text":
        return ["fixture.event"]
    if udt_name == "vector":
        return f"[{','.join(['0'] * 1536)}]"
    raise AssertionError(f"No Task 2b seed value for {table}.{column} ({udt_name})")


def _cast_parameter(name: str, udt_name: str) -> str:
    casts = {
        "uuid": "uuid",
        "jsonb": "jsonb",
        "vector": "vector",
        "_text": "text[]",
    }
    cast = casts.get(udt_name)
    return f"CAST(:{name} AS {cast})" if cast else f":{name}"


def seed_all_tenant_tables(connection: Connection, tenant_id: str) -> None:
    """Seed one constraint-valid row per table through the real trigger/FK posture."""
    required = connection.execute(
        text(
            """
            SELECT table_name, column_name, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                  SELECT table_name
                  FROM information_schema.columns
                  WHERE table_schema = 'public' AND column_name = 'tenant_id'
              )
              AND is_nullable = 'NO'
              AND column_default IS NULL
            ORDER BY table_name, ordinal_position
            """
        )
    ).fetchall()
    grouped: dict[str, list[tuple[str, str]]] = {table: [] for table in TENANT_TABLES}
    for table, column, udt_name in required:
        grouped[str(table)].append((str(column), str(udt_name)))

    shared_values: dict[str, str] = {"tenant_id": tenant_id}
    for table in SEED_TABLE_ORDER:
        columns = grouped[table]
        names: list[str] = []
        placeholders: list[str] = []
        params: dict[str, Any] = {}
        for index, (column, udt_name) in enumerate(columns):
            parameter = f"p{index}"
            names.append(f'"{column}"')
            placeholders.append(_cast_parameter(parameter, udt_name))
            params[parameter] = (
                tenant_id
                if column == "tenant_id"
                else _seed_value(
                    table,
                    column,
                    udt_name,
                    tenant_id=tenant_id,
                    shared_values=shared_values,
                )
            )
        connection.execute(
            text(f'INSERT INTO "{table}" ({", ".join(names)}) VALUES ({", ".join(placeholders)})'),
            params,
        )


def set_tenant_context(connection: Connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def set_strict_proof_insert_trigger(connection: Connection, *, enabled: bool) -> None:
    """Toggle the sole BEFORE-INSERT trigger that precedes the policy check."""
    action = "ENABLE" if enabled else "DISABLE"
    connection.exec_driver_sql(
        'ALTER TABLE "strict_preflight_proofs" '
        f'{action} TRIGGER "strict_preflight_proofs_immutability"'
    )
