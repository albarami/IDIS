"""Operational probe adapters for strict durable dependency admission.

The collectors accept factories so their semantics are fully testable without a
socket.  Default factories are resolved lazily, after the strict profile has been
applied, and exercise the same production stores used by API and worker processes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from idis.runtime.dependency_readiness import (
    FULL_DEPENDENCY_ADMISSION_SCOPE,
    TASK4_INGESTION_ADMISSION_SCOPE,
    TASK7_V21_CURRENT_ERASURE_RELATIONS,
    TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES,
    TASK7_V21_FUTURE_ERASURE_RELATIONS,
    TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES,
    AuditProbeFacts,
    BudgetProbeFacts,
    DependencyAdmissionReport,
    ErasureDatabaseTargetFacts,
    ErasureExecutorProbeFacts,
    ErasureFunctionOwnerUpdateAclFacts,
    ErasureRoleFacts,
    IdempotencyProbeFacts,
    IngestionProbeFacts,
    ObjectStoreProbeFacts,
    PostgresProbeFacts,
    ReadinessCheck,
    RedisProbeFacts,
    Task7ErasureRunnerProbeFacts,
    Task7GuardOwnerProbeFacts,
    WorkerProbeFacts,
    build_dependency_admission_report,
    check_audit_readiness,
    check_auth_readiness,
    check_budget_readiness,
    check_erasure_executor_readiness,
    check_idempotency_readiness,
    check_ingestion_readiness,
    check_object_store_readiness,
    check_postgres_readiness,
    check_redis_readiness,
    check_worker_readiness,
)
from idis.services.ingestion.no_substantive_content_attestation import (
    NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT,
)

Probe = Callable[[], object]

_REVISION_0037_TENANT_RLS_TABLES = frozenset(
    {
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
        "methodology_extraction_attempts",
        "methodology_extraction_results",
        "methodology_extraction_task_spans",
        "methodology_extraction_tasks",
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_result_claim_links",
        "methodology_result_defect_links",
        "methodology_result_evidence_links",
        "methodology_result_factual_outputs",
        "methodology_result_sanad_links",
        "methodology_versions",
        "overrides",
        "provider_budget_policies",
        "provider_budget_usage",
        "run_steps",
        "runs",
        "run_methodology_selections",
        "sanads",
        "strict_preflight_proofs",
        "tenants",
        "validated_evidence_packages",
        "vector_embeddings",
        "webhook_delivery_attempts",
        "webhooks",
    }
)
_TASK7_TENANT_RLS_TABLES = frozenset(
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
TENANT_RLS_TABLES = _REVISION_0037_TENANT_RLS_TABLES | _TASK7_TENANT_RLS_TABLES
_DEFAULT_RLS_TABLES = TENANT_RLS_TABLES


def _statement(sql: str) -> object:
    """Create a SQLAlchemy 2 executable lazily, after strict profile loading."""
    from sqlalchemy import text

    return text(sql)


def _normalise_policy_expression(expression: object) -> str:
    if not isinstance(expression, str):
        return ""
    # ``pg_policies`` already returns PostgreSQL's canonical deparse.  Remove
    # formatting whitespace only: case, quoting, parentheses, and casts all
    # carry SQL meaning and must not be erased by the admission comparison.
    return re.sub(r"\s+", "", expression)


_CANONICAL_RLS_POLICY_EXPRESSION = (
    "((NULLIF(current_setting('idis.tenant_id'::text,true),''::text)ISNOTNULL)AND"
    "(tenant_id=(NULLIF(current_setting('idis.tenant_id'::text,true),''::text))::uuid))"
)


def _has_exact_canonical_tenant_predicate(expression: object) -> bool:
    """Require the complete approved predicate, with no appended bypass/restriction."""
    return _normalise_policy_expression(expression) == _CANONICAL_RLS_POLICY_EXPRESSION


def _is_public_only_policy_roles(roles: object) -> bool:
    if isinstance(roles, (list, tuple)):
        return len(roles) == 1 and str(roles[0]).lower() == "public"
    normalised = re.sub(r"[\s\[\]'\"]+", "", str(roles).lower())
    return normalised in {"public", "{public}"}


def _all_tenant_tables_have_canonical_rls(
    rows: list[object],
    *,
    required_tables: frozenset[str],
) -> bool:
    table_flags: dict[str, tuple[bool, bool]] = {}
    policies: dict[str, list[tuple[object, object, object, object, object]]] = {}
    for row in rows:
        table = str(_row_value(row, "relname", 0))
        enabled = bool(_row_value(row, "relrowsecurity", 1))
        forced = bool(_row_value(row, "relforcerowsecurity", 2))
        previous = table_flags.get(table, (True, True))
        table_flags[table] = (previous[0] and enabled, previous[1] and forced)
        policies.setdefault(table, []).append(
            (
                _row_value(row, "qual", 4),
                _row_value(row, "with_check", 5),
                _row_value(row, "permissive", 6),
                _row_value(row, "roles", 7),
                _row_value(row, "cmd", 8),
            )
        )

    discovered = frozenset(table_flags)
    if not discovered or discovered != required_tables:
        return False
    for table, (enabled, forced) in table_flags.items():
        if not enabled or not forced:
            return False
        table_policies = policies.get(table, [])
        if len(table_policies) != 1:
            return False
        using, with_check, permissive, roles, command = table_policies[0]
        if not (
            _has_exact_canonical_tenant_predicate(using)
            and _has_exact_canonical_tenant_predicate(with_check)
            and str(permissive).upper() == "PERMISSIVE"
            and _is_public_only_policy_roles(roles)
            and str(command).upper() == "ALL"
        ):
            return False
    return True


def _scalar(result: object) -> object:
    for name in ("scalar_one", "scalar_one_or_none", "scalar"):
        method = getattr(result, name, None)
        if callable(method):
            return method()
    row = _first_row(result)
    if row is None:
        return None
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    try:
        return row[0]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return row


def _rows(result: object) -> list[object]:
    mappings = getattr(result, "mappings", None)
    source = mappings() if callable(mappings) else result
    all_rows = getattr(source, "all", None)
    if callable(all_rows):
        return list(all_rows())
    fetchall = getattr(source, "fetchall", None)
    if callable(fetchall):
        return list(fetchall())
    fetchone = getattr(source, "fetchone", None)
    if callable(fetchone):
        row = fetchone()
        return [] if row is None else [row]
    return []


def _first_row(result: object) -> object | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _row_value(row: object, name: str, index: int) -> object:
    if isinstance(row, Mapping):
        return row.get(name)
    value = getattr(row, name, None)
    if value is not None:
        return value
    try:
        return row[index]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return None


def _integer(value: object) -> int:
    if isinstance(value, (int, str)):
        return int(value)
    return int(str(value))


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def collect_postgres_facts(
    *,
    connection_factory: Callable[[], Any],
    tenant_id: str,
    isolation_tenant_id: str,
    expected_schema_revision: str,
    required_rls_tables: frozenset[str],
) -> PostgresProbeFacts:
    """Query connectivity, migration head, role flags, FORCE RLS, and isolation."""
    connected = False
    schema_at_head = False
    superuser = False
    bypass_rls = False
    forced_rls = False
    tenant_context_ok = False
    cross_tenant_denied = False
    try:
        with connection_factory() as connection:
            connected = _scalar(connection.execute(_statement("SELECT 1"))) == 1
            revision = _scalar(
                connection.execute(_statement("SELECT version_num FROM alembic_version"))
            )
            schema_at_head = str(revision) == expected_schema_revision

            role_result = connection.execute(
                _statement(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
            role = _first_row(role_result)
            if role is not None:
                superuser = bool(_row_value(role, "rolsuper", 0))
                bypass_rls = bool(_row_value(role, "rolbypassrls", 1))

            rls_result = connection.execute(
                _statement(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           p.policyname, p.qual, p.with_check,
                           p.permissive, p.roles, p.cmd
                    FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n
                      ON n.oid = c.relnamespace
                    JOIN pg_catalog.pg_attribute AS a
                      ON a.attrelid = c.oid
                     AND a.attname = 'tenant_id'
                     AND a.attnum > 0
                     AND NOT a.attisdropped
                    LEFT JOIN pg_catalog.pg_policies AS p
                      ON p.schemaname = n.nspname
                     AND p.tablename = c.relname
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                    ORDER BY c.relname, p.policyname
                    """
                )
            )
            forced_rls = _all_tenant_tables_have_canonical_rls(
                _rows(rls_result),
                required_tables=required_rls_tables,
            )

            connection.execute(
                _statement("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            current_tenant = _scalar(
                connection.execute(_statement("SELECT current_setting('idis.tenant_id', true)"))
            )
            tenant_context_ok = str(current_tenant) == tenant_id

        sentinel_key = f"strict-rls-canary-{uuid.uuid4().hex}"
        sentinel_params = {
            "tenant_id": isolation_tenant_id,
            "actor_id": "strict-runtime-readiness",
            "method": "POST",
            "operation_id": "dependency-readiness-rls-canary",
            "idempotency_key": sentinel_key,
            "payload_sha256": f"sha256:{hashlib.sha256(sentinel_key.encode()).hexdigest()}",
            "status_code": 200,
            "media_type": "application/json",
            "body_bytes": b"{}",
            "created_at": datetime.now(UTC),
        }
        seeded = False
        try:
            with connection_factory() as canary_connection:
                canary_connection.execute(
                    _statement("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                    {"tenant_id": isolation_tenant_id},
                )
                canary_connection.execute(
                    _statement(
                        """
                        INSERT INTO idempotency_records (
                            tenant_id, actor_id, method, operation_id, idempotency_key,
                            payload_sha256, status_code, media_type, body_bytes, created_at
                        ) VALUES (
                            CAST(:tenant_id AS uuid), :actor_id, :method, :operation_id,
                            :idempotency_key, :payload_sha256, :status_code, :media_type,
                            :body_bytes, :created_at
                        )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    sentinel_params,
                )
                seeded = bool(
                    _rows(
                        canary_connection.execute(
                            _statement(
                                """
                                SELECT tenant_id FROM idempotency_records
                                WHERE tenant_id = CAST(:tenant_id AS uuid)
                                  AND actor_id = :actor_id
                                  AND method = :method
                                  AND operation_id = :operation_id
                                  AND idempotency_key = :idempotency_key
                                """
                            ),
                            sentinel_params,
                        )
                    )
                )
            with connection_factory() as owner_connection:
                owner_connection.execute(
                    _statement("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
                owner_rows = _rows(
                    owner_connection.execute(
                        _statement(
                            """
                            SELECT tenant_id FROM idempotency_records
                            WHERE tenant_id = CAST(:isolation_tenant_id AS uuid)
                              AND actor_id = :actor_id
                              AND method = :method
                              AND operation_id = :operation_id
                              AND idempotency_key = :idempotency_key
                            """
                        ),
                        {**sentinel_params, "isolation_tenant_id": isolation_tenant_id},
                    )
                )
                cross_tenant_denied = seeded and not owner_rows
        finally:
            with connection_factory() as cleanup_connection:
                cleanup_connection.execute(
                    _statement("SELECT set_config('idis.tenant_id', :tenant_id, true)"),
                    {"tenant_id": isolation_tenant_id},
                )
                cleanup_connection.execute(
                    _statement(
                        """
                        DELETE FROM idempotency_records
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND actor_id = :actor_id
                          AND method = :method
                          AND operation_id = :operation_id
                          AND idempotency_key = :idempotency_key
                        """
                    ),
                    sentinel_params,
                )
    except Exception:
        # Error text can contain connection material.  Return only typed booleans;
        # the readiness interpreter selects the stable safe failure code.
        pass
    return PostgresProbeFacts(
        connected=connected,
        schema_at_head=schema_at_head,
        app_role_superuser=superuser,
        app_role_bypass_rls=bypass_rls,
        forced_rls=forced_rls,
        tenant_context_ok=tenant_context_ok,
        cross_tenant_denied=cross_tenant_denied,
    )


def _erasure_database_target(row: object | None) -> ErasureDatabaseTargetFacts | None:
    if row is None:
        return None
    database_name = _row_value(row, "database_name", 0)
    server_address = _row_value(row, "server_address", 1)
    server_port = _row_value(row, "server_port", 2)
    if (
        not isinstance(database_name, str)
        or not database_name
        or not isinstance(server_address, str)
        or not server_address
        or isinstance(server_port, bool)
        or not isinstance(server_port, int)
        or server_port < 0
    ):
        return None
    return ErasureDatabaseTargetFacts(
        database_name=database_name,
        server_address=server_address,
        server_port=server_port,
    )


def _erasure_role_facts(row: object) -> ErasureRoleFacts | None:
    role_name = _row_value(row, "role_name", 0)
    values = tuple(
        _row_value(row, name, index)
        for index, name in enumerate(
            (
                "can_login",
                "superuser",
                "create_database",
                "create_role",
                "inherit",
                "bypass_rls",
                "replication",
            ),
            start=1,
        )
    )
    if not isinstance(role_name, str) or any(type(value) is not bool for value in values):
        return None
    role_values = cast(tuple[bool, bool, bool, bool, bool, bool, bool], values)
    return ErasureRoleFacts(role_name, *role_values)


def _erasure_search_path(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != 1:
        return ()
    setting = value[0]
    if not isinstance(setting, str) or not setting.startswith("search_path="):
        return ()
    return tuple(part.strip() for part in setting.removeprefix("search_path=").split(","))


def _safe_catalog_count(row: object | None, name: str) -> int:
    value = None if row is None else _row_value(row, name, 0)
    return value if type(value) is int and value >= 0 else -1


_ERASURE_DELETE_RELATION_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.([a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)
_ERASURE_RESULT_RELATION_RE = re.compile(
    r"jsonb_build_object\('([a-z][a-z0-9_]*)',deleted_count\)",
    re.IGNORECASE,
)
_TASK7_V21_FUTURE_GUARD_ROUTINE_NAMES = tuple(
    signature.removeprefix("public.").partition("(")[0]
    for signature in TASK7_V21_FUTURE_GUARD_ROUTINE_SIGNATURES
)
_TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURE_BY_SIGNATURE = {
    signature: (security_mode, search_path)
    for signature, security_mode, search_path in TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURES
}


def _routine_signature(
    row: object,
    *,
    canonical_argument_types_required: bool = False,
) -> str:
    schema = _row_value(row, "function_schema", 0)
    name = _row_value(row, "function_name", 1)
    if not all(isinstance(value, str) and value for value in (schema, name)):
        return ""
    canonical_arguments = _row_value(row, "identity_argument_types", 8)
    arguments: object = canonical_arguments
    if not isinstance(arguments, str):
        if canonical_argument_types_required:
            return ""
        arguments = _row_value(row, "identity_arguments", 2)
    if not isinstance(arguments, str):
        return ""
    normalized_arguments = ",".join(
        argument.strip() for argument in arguments.split(",") if argument.strip()
    )
    return f"{schema}.{name}({normalized_arguments})"


def _guard_owner_column_acl_digest(rows: list[object]) -> tuple[int, int, str]:
    projection: list[tuple[str, str, str, bool]] = []
    for row in rows:
        relation = _row_value(row, "relation_name", 0)
        column = _row_value(row, "column_name", 1)
        privilege_type = _row_value(row, "privilege_type", 2)
        is_grantable = _row_value(row, "is_grantable", 3)
        if (
            not isinstance(relation, str)
            or not relation
            or not isinstance(column, str)
            or not column
            or not isinstance(privilege_type, str)
            or type(is_grantable) is not bool
        ):
            return -1, -1, ""
        projection.append((relation, column, privilege_type, is_grantable))
    projection.sort()
    payload = json.dumps(
        projection,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256(b"task7-v18-guard-owner-column-acl-v1\0" + payload).hexdigest()
    return len({row[0] for row in projection}), len(projection), digest


def _blocked_task7_guard_owner_facts() -> Task7GuardOwnerProbeFacts:
    return Task7GuardOwnerProbeFacts(
        role=None,
        membership_count=-1,
        owned_relation_count=-1,
        owned_routine_count=-1,
        schema_usage=False,
        schema_create=True,
        schema_direct_usage_acl_count=-1,
        schema_unexpected_acl_count=-1,
        table_select_privilege_count=-1,
        table_insert_privilege_count=-1,
        table_update_privilege_count=-1,
        table_delete_privilege_count=-1,
        table_truncate_privilege_count=-1,
        table_references_privilege_count=-1,
        table_trigger_privilege_count=-1,
        column_select_acl_relation_count=-1,
        column_select_acl_count=-1,
        column_select_acl_sha256="",
        force_rls_relation_count=-1,
        current_routine_signatures=(),
        current_routine_posture_mismatch_count=-1,
        application_direct_execute_count=-1,
        public_direct_execute_count=-1,
        unexpected_execute_grant_count=-1,
        external_helper_count=-1,
        external_helper_signature="",
        external_helper_owner="",
        external_helper_security_definer=False,
        external_helper_search_path=(),
        external_helper_guard_owner_execute=False,
        external_helper_application_execute=True,
        external_helper_public_execute=True,
        external_helper_unexpected_execute_grant_count=-1,
        future_routine_signatures_present=(),
    )


def _blocked_erasure_function_owner_update_acl_facts() -> ErasureFunctionOwnerUpdateAclFacts:
    return ErasureFunctionOwnerUpdateAclFacts(
        relation_tuple=(),
        column_update_acl_count=-1,
        ordered_column_tuple=(),
        grantable_acl_count=-1,
        table_update_privilege_count=-1,
    )


def _blocked_task7_erasure_runner_facts() -> Task7ErasureRunnerProbeFacts:
    return Task7ErasureRunnerProbeFacts(
        activation_state="",
        delete_relation_tuple=(),
        result_relation_tuple=(),
        future_relation_tuple_present=(),
    )


def collect_erasure_executor_facts(
    *,
    app_connection_factory: Callable[[], Any],
    erasure_connection_factory: Callable[[], Any],
) -> ErasureExecutorProbeFacts:
    """Collect secret-free catalog proof for the dedicated erasure boundary."""

    app_target: ErasureDatabaseTargetFacts | None = None
    executor_target: ErasureDatabaseTargetFacts | None = None
    app_role = ""
    app_effective_role = ""
    authenticated_role = ""
    effective_role = ""
    executor_role: ErasureRoleFacts | None = None
    owner_role: ErasureRoleFacts | None = None
    membership_count = -1
    table_privilege_count = -1
    column_privilege_count = -1
    executor_schema_usage = False
    executor_schema_create = True
    executor_schema_direct_acl_count = -1
    executor_other_direct_execute_count = -1
    function_rows: list[object] = []
    function_owner_update_acl = _blocked_erasure_function_owner_update_acl_facts()
    guard_owner_facts = _blocked_task7_guard_owner_facts()
    runner_contract = _blocked_task7_erasure_runner_facts()
    try:
        target_sql = _statement(
            """
            SELECT current_database()::text AS database_name,
                   COALESCE(inet_server_addr()::text, '<local>') AS server_address,
                   COALESCE(inet_server_port(), 0)::integer AS server_port,
                   session_user::text AS authenticated_role,
                   current_user::text AS effective_role
            """
        )
        with app_connection_factory() as app_connection:
            app_row = _first_row(app_connection.execute(target_sql))
        app_target = _erasure_database_target(app_row)
        app_role_value = None if app_row is None else _row_value(app_row, "authenticated_role", 3)
        app_role = app_role_value if isinstance(app_role_value, str) else ""
        app_effective_role_value = (
            None if app_row is None else _row_value(app_row, "effective_role", 4)
        )
        app_effective_role = (
            app_effective_role_value if isinstance(app_effective_role_value, str) else ""
        )

        with erasure_connection_factory() as erasure_connection:
            executor_row = _first_row(erasure_connection.execute(target_sql))
            executor_target = _erasure_database_target(executor_row)
            authenticated_role_value = (
                None if executor_row is None else _row_value(executor_row, "authenticated_role", 3)
            )
            authenticated_role = (
                authenticated_role_value if isinstance(authenticated_role_value, str) else ""
            )
            effective_role_value = (
                None if executor_row is None else _row_value(executor_row, "effective_role", 4)
            )
            effective_role = effective_role_value if isinstance(effective_role_value, str) else ""

            role_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT rolname::text AS role_name,
                               rolcanlogin AS can_login,
                               rolsuper AS superuser,
                               rolcreatedb AS create_database,
                               rolcreaterole AS create_role,
                               rolinherit AS inherit,
                               rolbypassrls AS bypass_rls,
                               rolreplication AS replication
                        FROM pg_catalog.pg_roles
                        WHERE rolname IN (
                            'idis_erasure_executor',
                            'idis_erasure_function_owner',
                            'idis_task7_guard_function_owner'
                        )
                        ORDER BY rolname
                        """
                    )
                )
            )
            roles = {
                facts.role_name: facts
                for row in role_rows
                if (facts := _erasure_role_facts(row)) is not None
            }
            executor_role = roles.get("idis_erasure_executor")
            owner_role = roles.get("idis_erasure_function_owner")
            guard_owner_role = roles.get("idis_task7_guard_function_owner")

            membership_row = _first_row(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT count(*) FILTER (
                                   WHERE member_role.rolname IN (
                                       'idis_erasure_executor',
                                       'idis_erasure_function_owner'
                                   )
                                      OR granted_role.rolname IN (
                                       'idis_erasure_executor',
                                       'idis_erasure_function_owner'
                                   )
                               )::integer AS erasure_membership_count,
                               count(*) FILTER (
                                   WHERE member_role.rolname =
                                             'idis_task7_guard_function_owner'
                                      OR granted_role.rolname =
                                             'idis_task7_guard_function_owner'
                               )::integer AS guard_owner_membership_count
                        FROM pg_catalog.pg_auth_members AS membership
                        JOIN pg_catalog.pg_roles AS member_role
                          ON member_role.oid = membership.member
                        JOIN pg_catalog.pg_roles AS granted_role
                          ON granted_role.oid = membership.roleid
                        """
                    )
                )
            )
            membership_count = _safe_catalog_count(
                membership_row,
                "erasure_membership_count",
            )
            guard_owner_membership_count = _safe_catalog_count(
                membership_row,
                "guard_owner_membership_count",
            )

            table_privilege_count = _safe_catalog_count(
                _first_row(
                    erasure_connection.execute(
                        _statement(
                            """
                            SELECT count(*)::integer AS table_privilege_count
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p','v','m','f')
                              AND (
                                has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'SELECT'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'INSERT'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'UPDATE'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'DELETE'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'TRUNCATE'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'REFERENCES'
                                )
                                OR has_table_privilege(
                                    'idis_erasure_executor', relation.oid, 'TRIGGER'
                                )
                              )
                            """
                        )
                    )
                ),
                "table_privilege_count",
            )
            column_privilege_count = _safe_catalog_count(
                _first_row(
                    erasure_connection.execute(
                        _statement(
                            """
                            SELECT count(*)::integer AS column_privilege_count
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p','v','m','f')
                              AND (
                                pg_catalog.has_any_column_privilege(
                                    'idis_erasure_executor', relation.oid, 'SELECT'
                                )
                                OR pg_catalog.has_any_column_privilege(
                                    'idis_erasure_executor', relation.oid, 'INSERT'
                                )
                                OR pg_catalog.has_any_column_privilege(
                                    'idis_erasure_executor', relation.oid, 'UPDATE'
                                )
                                OR pg_catalog.has_any_column_privilege(
                                    'idis_erasure_executor', relation.oid, 'REFERENCES'
                                )
                              )
                            """
                        )
                    )
                ),
                "column_privilege_count",
            )
            executor_boundary_row = _first_row(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT
                          pg_catalog.has_schema_privilege(
                            'idis_erasure_executor', 'public', 'USAGE'
                          ) AS executor_schema_usage,
                          pg_catalog.has_schema_privilege(
                            'idis_erasure_executor', 'public', 'CREATE'
                          ) AS executor_schema_create,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_namespace AS namespace
                            CROSS JOIN LATERAL
                              pg_catalog.aclexplode(namespace.nspacl) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE namespace.nspname = 'public'
                              AND grantee.rolname = 'idis_erasure_executor'
                          ) AS executor_schema_direct_acl_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_proc AS procedure
                            CROSS JOIN LATERAL
                              pg_catalog.aclexplode(procedure.proacl) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE grantee.rolname = 'idis_erasure_executor'
                              AND acl.privilege_type = 'EXECUTE'
                              AND procedure.oid <> pg_catalog.to_regprocedure(
                                'public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)'
                              )
                          ) AS executor_other_direct_execute_count
                        """
                    )
                )
            )
            executor_schema_usage_value = (
                None
                if executor_boundary_row is None
                else _row_value(
                    executor_boundary_row,
                    "executor_schema_usage",
                    0,
                )
            )
            executor_schema_usage = (
                executor_schema_usage_value if type(executor_schema_usage_value) is bool else False
            )
            executor_schema_create_value = (
                None
                if executor_boundary_row is None
                else _row_value(
                    executor_boundary_row,
                    "executor_schema_create",
                    1,
                )
            )
            executor_schema_create = (
                executor_schema_create_value if type(executor_schema_create_value) is bool else True
            )
            executor_schema_direct_acl_count = _safe_catalog_count(
                executor_boundary_row,
                "executor_schema_direct_acl_count",
            )
            executor_other_direct_execute_count = _safe_catalog_count(
                executor_boundary_row,
                "executor_other_direct_execute_count",
            )

            function_owner_table_update_row = _first_row(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT count(*)::integer
                               AS erasure_owner_table_update_privilege_count
                        FROM pg_catalog.pg_class AS relation
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relkind IN ('r','p','v','m','f')
                          AND pg_catalog.has_table_privilege(
                              'idis_erasure_function_owner',
                              relation.oid,
                              'UPDATE'
                          )
                        """
                    )
                )
            )
            function_owner_column_update_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT relation.relname::text AS relation_name,
                               attribute.attname::text AS column_name,
                               pg_catalog.has_column_privilege(
                                   'idis_erasure_function_owner',
                                   relation.oid,
                                   attribute.attnum,
                                   'UPDATE WITH GRANT OPTION'
                               ) AS is_grantable
                        FROM pg_catalog.pg_attribute AS attribute
                        JOIN pg_catalog.pg_class AS relation
                          ON relation.oid = attribute.attrelid
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND attribute.attnum > 0
                          AND NOT attribute.attisdropped
                          AND pg_catalog.has_column_privilege(
                              'idis_erasure_function_owner',
                              relation.oid,
                              attribute.attnum,
                              'UPDATE'
                          )
                        ORDER BY relation.relname, attribute.attnum
                        """
                    )
                )
            )
            function_owner_update_projection: list[tuple[str, str, bool]] = []
            for row in function_owner_column_update_rows:
                relation_name = _row_value(row, "relation_name", 0)
                column_name = _row_value(row, "column_name", 1)
                is_grantable = _row_value(row, "is_grantable", 2)
                if (
                    not isinstance(relation_name, str)
                    or not relation_name
                    or not isinstance(column_name, str)
                    or not column_name
                    or type(is_grantable) is not bool
                ):
                    function_owner_update_projection = []
                    break
                function_owner_update_projection.append((relation_name, column_name, is_grantable))
            table_update_privilege_count = _safe_catalog_count(
                function_owner_table_update_row,
                "erasure_owner_table_update_privilege_count",
            )
            if (
                len(function_owner_update_projection) == len(function_owner_column_update_rows)
                and table_update_privilege_count >= 0
            ):
                function_owner_update_acl = ErasureFunctionOwnerUpdateAclFacts(
                    relation_tuple=tuple(
                        dict.fromkeys(
                            relation
                            for relation, _column, _grantable in (function_owner_update_projection)
                        )
                    ),
                    column_update_acl_count=len(function_owner_update_projection),
                    ordered_column_tuple=tuple(
                        column for _relation, column, _grantable in function_owner_update_projection
                    ),
                    grantable_acl_count=sum(
                        int(grantable)
                        for _relation, _column, grantable in function_owner_update_projection
                    ),
                    table_update_privilege_count=table_update_privilege_count,
                )

            function_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT namespace.nspname::text AS function_schema,
                               procedure.proname::text AS function_name,
                               pg_catalog.pg_get_function_identity_arguments(
                                   procedure.oid
                               ) AS identity_arguments,
                               pg_catalog.pg_get_function_result(procedure.oid) AS result_type,
                               owner.rolname::text AS function_owner,
                               procedure.prosecdef AS security_definer,
                               procedure.proconfig AS function_config,
                               pg_catalog.has_function_privilege(
                                   :executor_role, procedure.oid, 'EXECUTE'
                               ) AS executor_has_execute,
                               EXISTS (
                                   SELECT 1
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault('f', procedure.proowner)
                                       )
                                   ) AS acl
                                   WHERE acl.grantee = 0
                                     AND acl.privilege_type = 'EXECUTE'
                               ) AS public_has_execute,
                               pg_catalog.has_function_privilege(
                                   :app_role, procedure.oid, 'EXECUTE'
                               ) AS app_role_has_execute,
                               (
                                   SELECT count(*)::integer
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault('f', procedure.proowner)
                                       )
                                   ) AS acl
                                   LEFT JOIN pg_catalog.pg_roles AS grantee
                                     ON grantee.oid = acl.grantee
                                   WHERE acl.privilege_type = 'EXECUTE'
                                     AND acl.grantee <> 0
                                     AND acl.grantee <> procedure.proowner
                                     AND COALESCE(grantee.rolname, '') <> :executor_role
                               ) AS unexpected_execute_grant_count,
                               procedure.prosrc AS function_source
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        JOIN pg_catalog.pg_roles AS owner
                          ON owner.oid = procedure.proowner
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname =
                              'idis_task7_execute_approved_erasure'
                        ORDER BY procedure.oid
                        """
                    ),
                    {
                        "app_role": app_role,
                        "executor_role": "idis_erasure_executor",
                    },
                )
            )

            guard_posture_row = _first_row(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_roles AS owner
                              ON owner.oid = relation.relowner
                            WHERE owner.rolname = 'idis_task7_guard_function_owner'
                          ) AS owned_relation_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_proc AS procedure
                            JOIN pg_catalog.pg_roles AS owner
                              ON owner.oid = procedure.proowner
                            WHERE owner.rolname = 'idis_task7_guard_function_owner'
                          ) AS owned_routine_count,
                          pg_catalog.has_schema_privilege(
                              'idis_task7_guard_function_owner',
                              'public',
                              'USAGE'
                          ) AS schema_usage,
                          pg_catalog.has_schema_privilege(
                              'idis_task7_guard_function_owner',
                              'public',
                              'CREATE'
                          ) AS schema_create,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'SELECT'
                              )
                          ) AS table_select_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'INSERT'
                              )
                          ) AS table_insert_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'UPDATE'
                              )
                          ) AS table_update_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'DELETE'
                              )
                          ) AS table_delete_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'TRUNCATE'
                              )
                          ) AS table_truncate_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'REFERENCES'
                              )
                          ) AS table_references_privilege_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_class AS relation
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = 'public'
                              AND relation.relkind IN ('r','p')
                              AND pg_catalog.has_table_privilege(
                                  'idis_task7_guard_function_owner',
                                  relation.oid,
                                  'TRIGGER'
                              )
                          ) AS table_trigger_privilege_count,
                          (
                            SELECT count(DISTINCT relation.oid)::integer
                            FROM pg_catalog.pg_attribute AS attribute
                            JOIN pg_catalog.pg_class AS relation
                              ON relation.oid = attribute.attrelid
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = relation.relnamespace
                            CROSS JOIN LATERAL pg_catalog.aclexplode(
                                attribute.attacl
                            ) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE namespace.nspname = 'public'
                              AND attribute.attnum > 0
                              AND NOT attribute.attisdropped
                              AND grantee.rolname =
                                    'idis_task7_guard_function_owner'
                              AND acl.privilege_type = 'SELECT'
                              AND relation.relrowsecurity
                              AND relation.relforcerowsecurity
                          ) AS force_rls_relation_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_namespace AS namespace
                            CROSS JOIN LATERAL pg_catalog.aclexplode(
                                namespace.nspacl
                            ) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE namespace.nspname = 'public'
                              AND grantee.rolname =
                                    'idis_task7_guard_function_owner'
                              AND acl.privilege_type = 'USAGE'
                              AND NOT acl.is_grantable
                          ) AS schema_direct_usage_acl_count,
                          (
                            SELECT count(*)::integer
                            FROM pg_catalog.pg_namespace AS namespace
                            CROSS JOIN LATERAL pg_catalog.aclexplode(
                                namespace.nspacl
                            ) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE namespace.nspname = 'public'
                              AND grantee.rolname =
                                    'idis_task7_guard_function_owner'
                              AND (
                                acl.privilege_type <> 'USAGE'
                                OR acl.is_grantable
                              )
                          ) AS schema_unexpected_acl_count
                        """
                    )
                )
            )
            guard_acl_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT relation.relname::text AS relation_name,
                               attribute.attname::text AS column_name,
                               acl.privilege_type::text AS privilege_type,
                               acl.is_grantable AS is_grantable
                        FROM pg_catalog.pg_attribute AS attribute
                        JOIN pg_catalog.pg_class AS relation
                          ON relation.oid = attribute.attrelid
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        CROSS JOIN LATERAL pg_catalog.aclexplode(
                            attribute.attacl
                        ) AS acl
                        JOIN pg_catalog.pg_roles AS grantee
                          ON grantee.oid = acl.grantee
                        WHERE namespace.nspname = 'public'
                          AND attribute.attnum > 0
                          AND NOT attribute.attisdropped
                          AND grantee.rolname =
                                'idis_task7_guard_function_owner'
                        ORDER BY relation.relname, attribute.attname,
                                 acl.privilege_type
                        """
                    )
                )
            )
            current_routine_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT namespace.nspname::text AS function_schema,
                               procedure.proname::text AS function_name,
                               pg_catalog.pg_get_function_identity_arguments(
                                   procedure.oid
                               ) AS identity_arguments,
                               procedure.prosecdef AS security_definer,
                               procedure.proconfig AS function_config,
                               pg_catalog.has_function_privilege(
                                   :app_role, procedure.oid, 'EXECUTE'
                               ) AS app_role_has_execute,
                               EXISTS (
                                   SELECT 1
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault(
                                               'f', procedure.proowner
                                           )
                                       )
                                   ) AS acl
                                   WHERE acl.grantee = 0
                                     AND acl.privilege_type = 'EXECUTE'
                               ) AS public_has_execute,
                               (
                                   SELECT count(*)::integer
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault(
                                               'f', procedure.proowner
                                           )
                                       )
                                   ) AS acl
                                   WHERE acl.privilege_type = 'EXECUTE'
                                     AND acl.grantee <> procedure.proowner
                                     AND acl.grantee <> 0
                               ) AS unexpected_execute_grant_count,
                               pg_catalog.oidvectortypes(
                                   procedure.proargtypes
                               ) AS identity_argument_types
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        JOIN pg_catalog.pg_roles AS owner
                          ON owner.oid = procedure.proowner
                        WHERE namespace.nspname = 'public'
                          AND owner.rolname =
                                'idis_task7_guard_function_owner'
                        ORDER BY procedure.proname,
                                 pg_catalog.pg_get_function_identity_arguments(
                                     procedure.oid
                                 )
                        """
                    ),
                    {"app_role": app_role},
                )
            )
            external_helper_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT namespace.nspname::text AS function_schema,
                               procedure.proname::text AS function_name,
                               pg_catalog.pg_get_function_identity_arguments(
                                   procedure.oid
                               ) AS identity_arguments,
                               owner.rolname::text AS function_owner,
                               procedure.prosecdef AS security_definer,
                               procedure.proconfig AS function_config,
                               pg_catalog.has_function_privilege(
                                   'idis_task7_guard_function_owner',
                                   procedure.oid,
                                   'EXECUTE'
                               ) AS guard_owner_has_execute,
                               pg_catalog.has_function_privilege(
                                   :app_role, procedure.oid, 'EXECUTE'
                               ) AS app_role_has_execute,
                               EXISTS (
                                   SELECT 1
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault(
                                               'f', procedure.proowner
                                           )
                                       )
                                   ) AS acl
                                   WHERE acl.grantee = 0
                                     AND acl.privilege_type = 'EXECUTE'
                               ) AS public_has_execute,
                               (
                                   SELECT count(*)::integer
                                   FROM pg_catalog.aclexplode(
                                       COALESCE(
                                           procedure.proacl,
                                           pg_catalog.acldefault(
                                               'f', procedure.proowner
                                           )
                                       )
                                   ) AS acl
                                   LEFT JOIN pg_catalog.pg_roles AS grantee
                                     ON grantee.oid = acl.grantee
                                   WHERE acl.privilege_type = 'EXECUTE'
                                     AND acl.grantee <> procedure.proowner
                                     AND acl.grantee <> 0
                                     AND COALESCE(grantee.rolname, '') <>
                                           'idis_task7_guard_function_owner'
                               ) AS unexpected_execute_grant_count
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        JOIN pg_catalog.pg_roles AS owner
                          ON owner.oid = procedure.proowner
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname =
                                'idis_task7_approved_erasure_context_v1'
                        ORDER BY procedure.oid
                        """
                    ),
                    {"app_role": app_role},
                )
            )
            future_relation_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT relation.relname::text AS relation_name
                        FROM unnest(CAST(:future_relations AS text[]))
                             WITH ORDINALITY AS expected(relation_name, ordinal)
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.nspname = 'public'
                        JOIN pg_catalog.pg_class AS relation
                          ON relation.relnamespace = namespace.oid
                         AND relation.relname = expected.relation_name
                        ORDER BY expected.ordinal
                        """
                    ),
                    {"future_relations": list(TASK7_V21_FUTURE_ERASURE_RELATIONS)},
                )
            )
            future_routine_rows = _rows(
                erasure_connection.execute(
                    _statement(
                        """
                        SELECT namespace.nspname::text AS function_schema,
                               procedure.proname::text AS function_name,
                               pg_catalog.pg_get_function_identity_arguments(
                                   procedure.oid
                               ) AS identity_arguments
                        FROM unnest(CAST(:future_names AS text[]))
                             WITH ORDINALITY
                             AS expected(function_name, ordinal)
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.nspname = 'public'
                        JOIN pg_catalog.pg_proc AS procedure
                          ON procedure.pronamespace = namespace.oid
                         AND procedure.proname = expected.function_name
                        ORDER BY expected.ordinal,
                                 pg_catalog.pg_get_function_identity_arguments(
                                     procedure.oid
                                 )
                        """
                    ),
                    {"future_names": list(_TASK7_V21_FUTURE_GUARD_ROUTINE_NAMES)},
                )
            )

            column_relation_count, column_count, column_acl_sha256 = _guard_owner_column_acl_digest(
                guard_acl_rows
            )

            def posture_count(name: str, index: int) -> int:
                value = (
                    None
                    if guard_posture_row is None
                    else _row_value(guard_posture_row, name, index)
                )
                return value if type(value) is int and value >= 0 else -1

            def posture_bool(name: str, index: int, *, invalid: bool) -> bool:
                value = (
                    None
                    if guard_posture_row is None
                    else _row_value(guard_posture_row, name, index)
                )
                return value if type(value) is bool else invalid

            current_routine_signatures = tuple(
                sorted(
                    _routine_signature(
                        row,
                        canonical_argument_types_required=True,
                    )
                    for row in current_routine_rows
                )
            )
            current_routine_posture_mismatch_count = 0
            application_direct_execute_count = 0
            public_direct_execute_count = 0
            unexpected_execute_grant_count = 0
            for row in current_routine_rows:
                signature = _routine_signature(
                    row,
                    canonical_argument_types_required=True,
                )
                security_definer = _row_value(row, "security_definer", 3)
                function_config = _row_value(row, "function_config", 4)
                expected_posture = _TASK7_V21_CURRENT_GUARD_ROUTINE_POSTURE_BY_SIGNATURE.get(
                    signature
                )
                expected_security_definer = (
                    None
                    if expected_posture is None
                    else {"DEFINER": True, "INVOKER": False}.get(expected_posture[0])
                )
                expected_search_path = None if expected_posture is None else expected_posture[1]
                if (
                    expected_security_definer is None
                    or type(security_definer) is not bool
                    or security_definer is not expected_security_definer
                    or _erasure_search_path(function_config) != expected_search_path
                ):
                    current_routine_posture_mismatch_count += 1
                app_execute = _row_value(row, "app_role_has_execute", 5)
                public_execute = _row_value(row, "public_has_execute", 6)
                unexpected_execute = _row_value(
                    row,
                    "unexpected_execute_grant_count",
                    7,
                )
                if type(app_execute) is not bool:
                    application_direct_execute_count = -1
                elif application_direct_execute_count >= 0:
                    application_direct_execute_count += int(app_execute)
                if type(public_execute) is not bool:
                    public_direct_execute_count = -1
                elif public_direct_execute_count >= 0:
                    public_direct_execute_count += int(public_execute)
                if type(unexpected_execute) is not int or unexpected_execute < 0:
                    unexpected_execute_grant_count = -1
                elif unexpected_execute_grant_count >= 0:
                    unexpected_execute_grant_count += unexpected_execute

            external_helper_row = (
                external_helper_rows[0] if len(external_helper_rows) == 1 else None
            )

            def external_string(name: str, index: int) -> str:
                value = (
                    None
                    if external_helper_row is None
                    else _row_value(external_helper_row, name, index)
                )
                return value if isinstance(value, str) else ""

            def external_bool(name: str, index: int, *, invalid: bool) -> bool:
                value = (
                    None
                    if external_helper_row is None
                    else _row_value(external_helper_row, name, index)
                )
                return value if type(value) is bool else invalid

            guard_owner_facts = Task7GuardOwnerProbeFacts(
                role=guard_owner_role,
                membership_count=guard_owner_membership_count,
                owned_relation_count=posture_count("owned_relation_count", 0),
                owned_routine_count=posture_count("owned_routine_count", 1),
                schema_usage=posture_bool("schema_usage", 2, invalid=False),
                schema_create=posture_bool("schema_create", 3, invalid=True),
                schema_direct_usage_acl_count=posture_count("schema_direct_usage_acl_count", 12),
                schema_unexpected_acl_count=posture_count("schema_unexpected_acl_count", 13),
                table_select_privilege_count=posture_count("table_select_privilege_count", 4),
                table_insert_privilege_count=posture_count("table_insert_privilege_count", 5),
                table_update_privilege_count=posture_count("table_update_privilege_count", 6),
                table_delete_privilege_count=posture_count("table_delete_privilege_count", 7),
                table_truncate_privilege_count=posture_count("table_truncate_privilege_count", 8),
                table_references_privilege_count=posture_count(
                    "table_references_privilege_count", 9
                ),
                table_trigger_privilege_count=posture_count("table_trigger_privilege_count", 10),
                column_select_acl_relation_count=column_relation_count,
                column_select_acl_count=column_count,
                column_select_acl_sha256=column_acl_sha256,
                force_rls_relation_count=posture_count("force_rls_relation_count", 11),
                current_routine_signatures=current_routine_signatures,
                current_routine_posture_mismatch_count=(current_routine_posture_mismatch_count),
                application_direct_execute_count=application_direct_execute_count,
                public_direct_execute_count=public_direct_execute_count,
                unexpected_execute_grant_count=unexpected_execute_grant_count,
                external_helper_count=len(external_helper_rows),
                external_helper_signature=(
                    "" if external_helper_row is None else _routine_signature(external_helper_row)
                ),
                external_helper_owner=external_string("function_owner", 3),
                external_helper_security_definer=external_bool(
                    "security_definer", 4, invalid=False
                ),
                external_helper_search_path=_erasure_search_path(
                    None
                    if external_helper_row is None
                    else _row_value(external_helper_row, "function_config", 5)
                ),
                external_helper_guard_owner_execute=external_bool(
                    "guard_owner_has_execute", 6, invalid=False
                ),
                external_helper_application_execute=external_bool(
                    "app_role_has_execute", 7, invalid=True
                ),
                external_helper_public_execute=external_bool("public_has_execute", 8, invalid=True),
                external_helper_unexpected_execute_grant_count=_safe_catalog_count(
                    external_helper_row,
                    "unexpected_execute_grant_count",
                ),
                future_routine_signatures_present=tuple(
                    signature
                    for row in future_routine_rows
                    if (signature := _routine_signature(row))
                ),
            )

            runner_row = function_rows[0] if len(function_rows) == 1 else None
            runner_source = (
                None if runner_row is None else _row_value(runner_row, "function_source", 11)
            )
            if isinstance(runner_source, str):
                delete_relations = tuple(
                    match.group(1).lower()
                    for match in _ERASURE_DELETE_RELATION_RE.finditer(runner_source)
                )
                result_relations = tuple(
                    match.group(1).lower()
                    for match in _ERASURE_RESULT_RELATION_RE.finditer(runner_source)
                )
            else:
                delete_relations = ()
                result_relations = ()
            future_relations_present = tuple(
                str(_row_value(row, "relation_name", 0))
                for row in future_relation_rows
                if isinstance(_row_value(row, "relation_name", 0), str)
            )
            runner_contract = Task7ErasureRunnerProbeFacts(
                activation_state=(
                    "CURRENT48_ACTIVE_FUTURE38_FROZEN"
                    if (
                        delete_relations == TASK7_V21_CURRENT_ERASURE_RELATIONS
                        and result_relations == TASK7_V21_CURRENT_ERASURE_RELATIONS
                        and not future_relations_present
                        and not guard_owner_facts.future_routine_signatures_present
                    )
                    else ""
                ),
                delete_relation_tuple=delete_relations,
                result_relation_tuple=result_relations,
                future_relation_tuple_present=future_relations_present,
            )
    except Exception:
        # Catalog/driver errors can carry a credential.  Preserve only typed
        # defaults; the readiness interpreter emits one reviewed stable code.
        pass

    function_row = function_rows[0] if len(function_rows) == 1 else None
    identity_arguments = (
        None if function_row is None else _row_value(function_row, "identity_arguments", 2)
    )
    function_config = (
        None if function_row is None else _row_value(function_row, "function_config", 6)
    )

    def string_field(name: str, index: int) -> str:
        value = None if function_row is None else _row_value(function_row, name, index)
        return value if isinstance(value, str) else ""

    def bool_field(name: str, index: int, *, invalid_default: bool) -> bool:
        value = None if function_row is None else _row_value(function_row, name, index)
        return value if type(value) is bool else invalid_default

    return ErasureExecutorProbeFacts(
        connected=app_target is not None and executor_target is not None,
        authenticated_role=authenticated_role,
        effective_role=effective_role,
        executor_target=executor_target,
        app_target=app_target,
        app_role=app_role,
        app_effective_role=app_effective_role,
        executor_role=executor_role,
        owner_role=owner_role,
        membership_count=membership_count,
        executor_table_privilege_count=table_privilege_count,
        executor_column_privilege_count=column_privilege_count,
        executor_schema_usage=executor_schema_usage,
        executor_schema_create=executor_schema_create,
        executor_schema_direct_acl_count=executor_schema_direct_acl_count,
        executor_other_direct_execute_count=executor_other_direct_execute_count,
        function_count=len(function_rows),
        function_schema=string_field("function_schema", 0),
        function_name=string_field("function_name", 1),
        function_identity_arguments=(
            tuple(part.strip() for part in identity_arguments.split(","))
            if isinstance(identity_arguments, str)
            else ()
        ),
        function_result_type=string_field("result_type", 3),
        function_owner=string_field("function_owner", 4),
        function_security_definer=bool_field(
            "security_definer",
            5,
            invalid_default=False,
        ),
        function_search_path=_erasure_search_path(function_config),
        executor_has_execute=bool_field(
            "executor_has_execute",
            7,
            invalid_default=False,
        ),
        public_has_execute=bool_field(
            "public_has_execute",
            8,
            invalid_default=True,
        ),
        app_role_has_execute=bool_field(
            "app_role_has_execute",
            9,
            invalid_default=True,
        ),
        unexpected_execute_grant_count=_safe_catalog_count(
            function_row,
            "unexpected_execute_grant_count",
        ),
        function_owner_update_acl=function_owner_update_acl,
        guard_owner=guard_owner_facts,
        runner_contract=runner_contract,
    )


def _read_audit_event(
    *,
    connection_factory: Callable[[], Any],
    tenant_setter: Callable[[Any, str], None],
    tenant_id: str,
    event_id: str,
    expected_event: Mapping[str, object],
) -> bool:
    with connection_factory() as connection:
        tenant_setter(connection, tenant_id)
        result = connection.execute(
            _statement(
                """
                SELECT event_id, tenant_id, occurred_at, event_type,
                       request_id, idempotency_key, event
                FROM audit_events
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_id = CAST(:event_id AS uuid)
                """
            ),
            {"tenant_id": tenant_id, "event_id": event_id},
        )
        row = _first_row(result)
        if row is None:
            return False
        stored_event = _row_value(row, "event", 6)
        if isinstance(stored_event, str):
            stored_event = json.loads(stored_event)
        if not isinstance(stored_event, Mapping) or dict(stored_event) != dict(expected_event):
            return False
        request = expected_event.get("request")
        if not isinstance(request, Mapping):
            return False

        def instant(value: object) -> datetime | None:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    return None
            else:
                return None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return parsed.astimezone(UTC)

        return all(
            (
                str(_row_value(row, "event_id", 0)) == event_id,
                str(_row_value(row, "tenant_id", 1)) == tenant_id,
                instant(_row_value(row, "occurred_at", 2))
                == instant(expected_event.get("occurred_at")),
                str(_row_value(row, "event_type", 3)) == expected_event.get("event_type"),
                str(_row_value(row, "request_id", 4)) == str(request.get("request_id")),
                _row_value(row, "idempotency_key", 5) == request.get("idempotency_key_sha256"),
            )
        )


def collect_postgres_audit_facts(
    *,
    audit_sink_factory: Callable[[], Any],
    connection_factory: Callable[[], Any],
    tenant_setter: Callable[[Any, str], None],
    tenant_id: str,
) -> AuditProbeFacts:
    """Emit one tenant event, read it, then prove a fresh connection can read it."""
    event_id = str(uuid.uuid4())
    event = {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_type": "integration.dependency_readiness.probed",
        "severity": "LOW",
        "summary": "Durable dependency audit round-trip probed",
        "actor": {"actor_id": "strict-runtime-readiness", "actor_type": "SERVICE"},
        "request": {
            "request_id": f"readiness-{event_id}",
            "method": "POST",
            "path": "/internal/dependency-readiness/audit",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "integration",
            "resource_id": f"dependency-readiness:{event_id}",
        },
        "payload": {"safe": {"backend": "postgres"}, "hashes": [], "refs": []},
    }
    emit_ok = False
    read_ok = False
    restart_read_ok = False
    try:
        from idis.validators.audit_event_validator import validate_audit_event

        validation = validate_audit_event(event)
        if not validation.passed:
            raise RuntimeError("AUDIT_EMIT_FAILED")
        audit_sink_factory().emit(event)
        emit_ok = True
        read_ok = _read_audit_event(
            connection_factory=connection_factory,
            tenant_setter=tenant_setter,
            tenant_id=tenant_id,
            event_id=event_id,
            expected_event=event,
        )
        restart_read_ok = _read_audit_event(
            connection_factory=connection_factory,
            tenant_setter=tenant_setter,
            tenant_id=tenant_id,
            event_id=event_id,
            expected_event=event,
        )
    except Exception:
        pass
    return AuditProbeFacts(
        backend="postgres",
        emit_ok=emit_ok,
        read_ok=read_ok,
        restart_read_ok=restart_read_ok,
    )


def collect_postgres_idempotency_facts(
    *,
    store_factory: Callable[[], Any],
    tenant_id: str,
    isolation_tenant_id: str,
) -> IdempotencyProbeFacts:
    """Prove durable replay, mismatch conflict detection, and tenant isolation."""
    from idis.idempotency.store import (
        IdempotencyKeyConflictError,
        IdempotencyRecord,
        ScopeKey,
    )

    key_suffix = uuid.uuid4().hex
    scope = ScopeKey(
        tenant_id=tenant_id,
        actor_id="strict-runtime-readiness",
        method="POST",
        operation_id="dependency-readiness",
        idempotency_key=f"readiness-{key_suffix}",
    )
    payload_hash = f"sha256:{hashlib.sha256(b'readiness-request').hexdigest()}"
    conflicting_hash = f"sha256:{hashlib.sha256(b'conflicting-request').hexdigest()}"
    record = IdempotencyRecord(
        payload_sha256=payload_hash,
        status_code=200,
        media_type="application/json",
        body_bytes=b'{"status":"ok"}',
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    conflicting_record = IdempotencyRecord(
        payload_sha256=conflicting_hash,
        status_code=201,
        media_type="application/json",
        body_bytes=b'{"status":"conflicting"}',
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    put_ok = False
    replay_ok = False
    conflict_ok = False
    restart_replay_ok = False
    isolation_ok = False
    try:
        first_store = store_factory()
        first_store.put(scope, record)
        put_ok = True
        replay = first_store.get(scope)
        replay_ok = replay == record
        conflict_raised = False
        try:
            first_store.put(scope, conflicting_record)
        except IdempotencyKeyConflictError:
            conflict_raised = True
        except Exception:
            pass
        conflict_replay = first_store.get(scope)
        conflict_ok = conflict_raised and conflict_replay == record

        second_store = store_factory()
        restart_replay_ok = second_store.get(scope) == record
        isolation_scope = ScopeKey(
            tenant_id=isolation_tenant_id,
            actor_id=scope.actor_id,
            method=scope.method,
            operation_id=scope.operation_id,
            idempotency_key=scope.idempotency_key,
        )
        isolation_ok = second_store.get(isolation_scope) is None
    except Exception:
        pass
    return IdempotencyProbeFacts(
        backend="postgres",
        put_ok=put_ok,
        replay_ok=replay_ok,
        conflict_ok=conflict_ok,
        restart_replay_ok=restart_replay_ok,
        tenant_isolation_ok=isolation_ok,
    )


def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def collect_redis_facts(*, client_factory: Callable[[], Any]) -> RedisProbeFacts:
    """PING two clients and consume one shared atomic Lua token bucket."""
    from idis.rate_limit.limiter import RedisTokenBucketStore

    first_client: Any | None = None
    second_client: Any | None = None
    ping_ok = False
    atomic_ok = False
    cross_process_ok = False
    try:
        first_client = client_factory()
        second_client = client_factory()
        ping_ok = bool(first_client.ping()) and bool(second_client.ping())
        key = f"dependency-readiness:{uuid.uuid4().hex}"
        first = RedisTokenBucketStore(first_client).consume(
            key=key,
            capacity=1,
            refill_rate_per_sec=0.000001,
            cost=1,
        )
        second = RedisTokenBucketStore(second_client).consume(
            key=key,
            capacity=1,
            refill_rate_per_sec=0.000001,
            cost=1,
        )
        atomic_ok = first[0] is True and first[2] == 0
        cross_process_ok = atomic_ok and second[0] is False
    except Exception:
        pass
    finally:
        if first_client is not None:
            _close_client(first_client)
        if second_client is not None:
            _close_client(second_client)
    return RedisProbeFacts(
        backend="redis",
        ping_ok=ping_ok,
        atomic_bucket_ok=atomic_ok,
        cross_process_ok=cross_process_ok,
    )


def collect_filesystem_object_store_facts(
    *,
    store_factory: Callable[[], Any],
    tenant_id: str,
) -> ObjectStoreProbeFacts:
    """Put/read/hash/restart/delete a namespaced object through two store instances."""
    payload = f"strict-readiness:{uuid.uuid4().hex}".encode()
    expected_hash = hashlib.sha256(payload).hexdigest()
    key = f"dependency-readiness/{uuid.uuid4().hex}.bin"
    backend = "unknown"
    write_ok = False
    read_ok = False
    hash_ok = False
    delete_ok = False
    restart_read_ok = False
    first_store: Any | None = None
    second_store: Any | None = None
    try:
        first_store = store_factory()
        backend = str(getattr(first_store, "backend_name", "unknown"))
        metadata = first_store.put(
            tenant_id,
            key,
            payload,
            content_type="application/octet-stream",
        )
        write_ok = True
        first_read = first_store.get(tenant_id, key)
        body = bytes(getattr(first_read, "body", b""))
        read_ok = body == payload
        hash_ok = (
            getattr(metadata, "sha256", None) == expected_hash
            and hashlib.sha256(body).hexdigest() == expected_hash
        )

        from idis.storage.errors import ObjectNotFoundError

        second_store = store_factory()
        restart_read = second_store.get(tenant_id, key)
        restart_read_ok = bytes(getattr(restart_read, "body", b"")) == payload
        second_store.delete(tenant_id, key)
        try:
            second_store.get(tenant_id, key)
        except ObjectNotFoundError:
            delete_ok = True
    except Exception:
        pass
    finally:
        if not delete_ok:
            cleanup_store = second_store or first_store
            if cleanup_store is not None:
                with suppress(Exception):
                    cleanup_store.delete(tenant_id, key)
    return ObjectStoreProbeFacts(
        backend=backend,
        write_ok=write_ok,
        read_ok=read_ok,
        hash_ok=hash_ok,
        delete_ok=delete_ok,
        restart_read_ok=restart_read_ok,
    )


class _BoundedProbeReader(io.BytesIO):
    """Reject any accidental whole-stream read during readiness collection."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.read_sizes.append(size)
        if size is None or size <= 0:
            raise RuntimeError("MEDIA_LARGE_UPLOAD_UNSUPPORTED")
        return super().read(size)


def _probe_staging_volume(staging_root: Path) -> tuple[bool, bool]:
    """Write, fsync, read, and remove one private sentinel on the spool volume."""

    directory: Path | None = None
    write_ok = False
    cleanup_ok = False
    try:
        if not staging_root.is_absolute() or not staging_root.is_dir():
            return False, False
        directory = Path(
            tempfile.mkdtemp(prefix="idis-ingestion-readiness-", dir=str(staging_root))
        )
        directory.chmod(0o700)
        sentinel = directory / "sentinel.bin"
        payload = secrets.token_bytes(32)
        descriptor = os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        write_ok = sentinel.read_bytes() == payload
    except Exception:
        write_ok = False
    finally:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
            cleanup_ok = not directory.exists()
    return write_ok, cleanup_ok


def _disk_headroom_facts(
    *,
    staging_root: Path,
    object_store_root: Path,
) -> tuple[int, int, bool]:
    """Return safe numeric capacity facts without serializing either locator."""

    try:
        staging = staging_root.resolve(strict=True)
        object_store = object_store_root.resolve(strict=True)
        staging_usage = shutil.disk_usage(staging)
        object_store_usage = shutil.disk_usage(object_store)
        staging_free = int(getattr(staging_usage, "free", staging_usage[2]))
        object_store_free = int(getattr(object_store_usage, "free", object_store_usage[2]))
        shared_filesystem = staging.stat().st_dev == object_store.stat().st_dev
    except (OSError, TypeError, ValueError):
        return 0, 0, False
    if staging_free < 0 or object_store_free < 0:
        return 0, 0, False
    return staging_free, object_store_free, shared_filesystem


def collect_ingestion_readiness_facts(
    *,
    store_factory: Callable[[], Any],
    tenant_id: str,
    staging_root: Path,
    object_store_root: Path,
    password_manifest_loader: Callable[[], Any],
    no_substantive_content_attestation_loader: Callable[[], Any] | None = None,
) -> IngestionProbeFacts:
    """Prove bounded spool and streaming-object capabilities without a large allocation."""

    payload = f"ingestion-readiness:{uuid.uuid4().hex}".encode("ascii")
    expected_hash = hashlib.sha256(payload).hexdigest()
    key = f"dependency-readiness/{uuid.uuid4().hex}.stream"
    backend = "unknown"
    put_stream_supported = False
    bounded_read_ok = False
    stream_write_ok = False
    stream_read_ok = False
    stream_hash_ok = False
    stream_cleanup_ok = False
    reader = _BoundedProbeReader(payload)
    store: Any | None = None
    try:
        store = store_factory()
        backend = str(getattr(store, "backend_name", "unknown"))
        put_stream = getattr(store, "put_stream", None)
        if not callable(put_stream):
            raise RuntimeError("MEDIA_LARGE_UPLOAD_UNSUPPORTED")
        put_stream_supported = True
        metadata = put_stream(
            tenant_id,
            key,
            reader,
            content_type="application/octet-stream",
            expected_sha256=expected_hash,
            expected_size_bytes=len(payload),
        )
        bounded_read_ok = bool(reader.read_sizes) and all(
            isinstance(size, int) and size > 0 for size in reader.read_sizes
        )
        stream_write_ok = True
        stored = store.get(tenant_id, key)
        body = bytes(getattr(stored, "body", b""))
        stream_read_ok = body == payload
        stream_hash_ok = (
            getattr(metadata, "sha256", None) == expected_hash
            and hashlib.sha256(body).hexdigest() == expected_hash
        )

        from idis.storage.errors import ObjectNotFoundError

        store.delete(tenant_id, key)
        try:
            store.get(tenant_id, key)
        except ObjectNotFoundError:
            stream_cleanup_ok = True
    except Exception:
        pass
    finally:
        if store is not None and not stream_cleanup_ok:
            with suppress(Exception):
                store.delete(tenant_id, key)

    staging_write_ok, staging_cleanup_ok = _probe_staging_volume(staging_root)
    staging_free, object_store_free, shared_filesystem = _disk_headroom_facts(
        staging_root=staging_root,
        object_store_root=object_store_root,
    )
    password_manifest_status = "invalid"
    try:
        password_manifest = password_manifest_loader()
        safe_manifest = password_manifest.safe_summary()
        if (
            isinstance(safe_manifest, Mapping)
            and set(safe_manifest) == {"schema_version", "status", "entry_count"}
            and safe_manifest.get("schema_version") == "document-password-manifest-v1"
            and safe_manifest.get("status") in {"none_configured", "configured"}
            and isinstance(safe_manifest.get("entry_count"), int)
            and not isinstance(safe_manifest.get("entry_count"), bool)
            and int(safe_manifest["entry_count"]) >= 0
        ):
            password_manifest_status = str(safe_manifest["status"])
    except Exception:
        pass
    attestation_status = "not_configured"
    attestation_entry_count = 0
    attestation_schema_version: str | None = None
    attestation_disposition: str | None = None
    attestation_variant: str | None = None
    attestation_payload_sha256: str | None = None
    attestation_envelope_sha256: str | None = None
    attestation_public_key_sha256: str | None = None
    attestation_manifest_sha256: str | None = None
    attestation_classifier_policy_sha256: str | None = None
    attestation_exemplar_envelope_sha256: str | None = None
    attestation_exemplar_canonical_bitmap_sha256: str | None = None
    attestation_prescan_receipt_sha256: str | None = None
    attestation_candidate_set_sha256: str | None = None
    attestation_sample_set_sha256: str | None = None
    attestation_batch_envelope_sha256: str | None = None
    attestation_manual_count = 0
    attestation_watermark_candidate_count = 0
    attestation_runtime_unseen_count = 0
    if no_substantive_content_attestation_loader is not None:
        try:
            attestation = no_substantive_content_attestation_loader()
            if attestation is not None:
                safe_attestation = attestation.safe_summary()
                v1_fields = {
                    "schema_version",
                    "status",
                    "entry_count",
                    "disposition",
                    "variant",
                    "payload_sha256",
                    "envelope_sha256",
                    "public_key_sha256",
                    "signature_verified",
                }
                safe_fields = (
                    set(safe_attestation) if isinstance(safe_attestation, Mapping) else set()
                )
                if (
                    isinstance(safe_attestation, Mapping)
                    and safe_fields == v1_fields
                    and safe_attestation.get("status") == "verified"
                    and safe_attestation.get("entry_count") == 1
                    and safe_attestation.get("signature_verified") is True
                ):
                    attestation_status = "verified"
                    attestation_entry_count = 1
                    attestation_schema_version = str(safe_attestation["schema_version"])
                    attestation_disposition = str(safe_attestation["disposition"])
                    attestation_variant = str(safe_attestation["variant"])
                    attestation_payload_sha256 = str(safe_attestation["payload_sha256"])
                    attestation_envelope_sha256 = str(safe_attestation["envelope_sha256"])
                    attestation_public_key_sha256 = str(safe_attestation["public_key_sha256"])
                elif NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT.is_valid(
                    safe_attestation
                ):
                    batch = getattr(attestation, "batch", None)
                    batch_payload_sha256 = getattr(batch, "payload_sha256", None)
                    if not isinstance(batch_payload_sha256, str):
                        raise ValueError("WATERMARK_BATCH_ATTESTATION_MISMATCH")
                    attestation_status = "verified"
                    attestation_entry_count = 2
                    attestation_schema_version = str(safe_attestation["schema_version"])
                    attestation_disposition = str(safe_attestation["disposition"])
                    attestation_variant = str(safe_attestation["variant"])
                    attestation_payload_sha256 = batch_payload_sha256
                    attestation_envelope_sha256 = str(safe_attestation["exemplar_envelope_sha256"])
                    attestation_public_key_sha256 = str(safe_attestation["public_key_sha256"])
                    attestation_manifest_sha256 = str(safe_attestation["manifest_sha256"])
                    attestation_classifier_policy_sha256 = str(
                        safe_attestation["classifier_policy_sha256"]
                    )
                    attestation_exemplar_envelope_sha256 = str(
                        safe_attestation["exemplar_envelope_sha256"]
                    )
                    attestation_exemplar_canonical_bitmap_sha256 = str(
                        safe_attestation["exemplar_canonical_bitmap_sha256"]
                    )
                    attestation_prescan_receipt_sha256 = str(
                        safe_attestation["prescan_receipt_sha256"]
                    )
                    attestation_candidate_set_sha256 = str(safe_attestation["candidate_set_sha256"])
                    attestation_sample_set_sha256 = str(safe_attestation["sample_set_sha256"])
                    attestation_batch_envelope_sha256 = str(
                        safe_attestation["batch_envelope_sha256"]
                    )
                    attestation_manual_count = int(safe_attestation["manual_attestation_count"])
                    attestation_watermark_candidate_count = int(
                        safe_attestation["watermark_batch_candidate_count"]
                    )
                else:
                    attestation_status = "invalid"
        except Exception:
            attestation_status = "invalid"
    return IngestionProbeFacts(
        backend=backend,
        put_stream_supported=put_stream_supported,
        bounded_read_ok=bounded_read_ok,
        stream_write_ok=stream_write_ok,
        stream_read_ok=stream_read_ok,
        stream_hash_ok=stream_hash_ok,
        stream_cleanup_ok=stream_cleanup_ok,
        staging_write_ok=staging_write_ok,
        staging_cleanup_ok=staging_cleanup_ok,
        staging_free_bytes=staging_free,
        object_store_free_bytes=object_store_free,
        shared_filesystem=shared_filesystem,
        password_manifest_status=password_manifest_status,
        no_substantive_content_attestation_status=attestation_status,
        no_substantive_content_attestation_entry_count=attestation_entry_count,
        no_substantive_content_attestation_schema_version=attestation_schema_version,
        no_substantive_content_attestation_disposition=attestation_disposition,
        no_substantive_content_attestation_variant=attestation_variant,
        no_substantive_content_attestation_payload_sha256=attestation_payload_sha256,
        no_substantive_content_attestation_envelope_sha256=attestation_envelope_sha256,
        no_substantive_content_attestation_public_key_sha256=attestation_public_key_sha256,
        no_substantive_content_attestation_manifest_sha256=attestation_manifest_sha256,
        no_substantive_content_attestation_classifier_policy_sha256=(
            attestation_classifier_policy_sha256
        ),
        no_substantive_content_attestation_exemplar_envelope_sha256=(
            attestation_exemplar_envelope_sha256
        ),
        no_substantive_content_attestation_exemplar_canonical_bitmap_sha256=(
            attestation_exemplar_canonical_bitmap_sha256
        ),
        no_substantive_content_attestation_prescan_receipt_sha256=(
            attestation_prescan_receipt_sha256
        ),
        no_substantive_content_attestation_candidate_set_sha256=attestation_candidate_set_sha256,
        no_substantive_content_attestation_sample_set_sha256=attestation_sample_set_sha256,
        no_substantive_content_attestation_batch_envelope_sha256=(
            attestation_batch_envelope_sha256
        ),
        no_substantive_content_attestation_manual_count=attestation_manual_count,
        no_substantive_content_attestation_watermark_candidate_count=(
            attestation_watermark_candidate_count
        ),
        no_substantive_content_attestation_runtime_unseen_count=attestation_runtime_unseen_count,
    )


def _collect_worker_boundary_rollback_facts(
    *,
    transaction_factory: Callable[[], Any],
    tenant_setter: Callable[[Any, str], None],
    audit_sink_factory: Callable[[], Any],
    tenant_id: str,
    deal_id: str,
    run_id: str,
    lease_owner_id: str,
    step_id: str,
    lease_seconds: int,
) -> tuple[bool, bool]:
    """Prove a failed real completion boundary leaves its committed prefix untouched."""
    from idis.services.runs.execution import build_postgres_step_boundary_uow

    evidence_id = str(uuid.uuid5(uuid.UUID(run_id), "worker-boundary-probe-evidence"))
    claim_id = str(uuid.uuid5(uuid.UUID(run_id), "worker-boundary-probe-claim"))
    source_span_id = str(uuid.uuid5(uuid.UUID(run_id), "worker-boundary-probe-span"))
    prior_summary = {"boundary": "START", "probe": True}
    completion_summary = {"boundary": "COMPLETE", "probe": True}
    boundary_rollback_ok = False
    no_residue = False

    class _InjectedBoundaryAuditFailure(RuntimeError):
        pass

    try:
        with transaction_factory() as setup_connection:
            tenant_setter(setup_connection, tenant_id)
            setup_connection.execute(
                _statement(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (
                        CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                        :name, statement_timestamp()
                    )
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": tenant_id,
                    "name": "strict-worker-boundary-readiness",
                },
            )
            setup_connection.execute(
                _statement(
                    """
                    INSERT INTO runs (
                        run_id, tenant_id, deal_id, mode, status, started_at,
                        idempotency_key, created_at, lease_owner_id,
                        lease_fencing_token, lease_expires_at, last_heartbeat_at
                    ) VALUES (
                        CAST(:run_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), 'SNAPSHOT', 'RUNNING',
                        statement_timestamp(), :idempotency_key, statement_timestamp(),
                        CAST(:lease_owner_id AS uuid), 1,
                        statement_timestamp() + make_interval(secs => :lease_seconds),
                        statement_timestamp()
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "idempotency_key": f"strict-worker-boundary-readiness-{run_id}",
                    "lease_owner_id": lease_owner_id,
                    "lease_seconds": lease_seconds,
                },
            )
            setup_connection.execute(
                _statement(
                    """
                    INSERT INTO run_steps (
                        step_id, tenant_id, run_id, step_name, step_order, status,
                        started_at, result_summary
                    ) VALUES (
                        CAST(:step_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:run_id AS uuid), 'INGEST_CHECK', 2, 'RUNNING',
                        statement_timestamp(), CAST(:result_summary AS jsonb)
                    )
                    """
                ),
                {
                    "step_id": step_id,
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "result_summary": json.dumps(prior_summary, separators=(",", ":")),
                },
            )
            prior_run = _first_row(
                setup_connection.execute(
                    _statement(
                        """
                        SELECT status, lease_owner_id::text AS lease_owner_id,
                               lease_fencing_token, last_heartbeat_at, lease_expires_at
                        FROM runs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id},
                )
            )
        if prior_run is None:
            raise RuntimeError("WORKER_CLAIM_NOT_PROVEN")

        real_audit_sink = audit_sink_factory()

        class _FailAfterDurableAudit:
            def emit_in_tx(self, connection: Any, event: Mapping[str, object]) -> None:
                real_audit_sink.emit_in_tx(connection, event)
                raise _InjectedBoundaryAuditFailure

            def emit(self, _event: Mapping[str, object]) -> None:
                raise _InjectedBoundaryAuditFailure

        def persist_native_output(*, db_conn: Any, **_lease: object) -> None:
            db_conn.execute(
                _statement(
                    """
                    INSERT INTO evidence_items (
                        evidence_id, tenant_id, deal_id, claim_id,
                        source_span_id, source_grade, created_at
                    ) VALUES (
                        CAST(:evidence_id AS uuid), CAST(:tenant_id AS uuid),
                        CAST(:deal_id AS uuid), CAST(:claim_id AS uuid),
                        CAST(:source_span_id AS uuid), 'D', statement_timestamp()
                    )
                    """
                ),
                {
                    "evidence_id": evidence_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "claim_id": claim_id,
                    "source_span_id": source_span_id,
                },
            )

        def complete_step(*, db_conn: Any, **_lease: object) -> None:
            result = db_conn.execute(
                _statement(
                    """
                    UPDATE run_steps
                    SET status = 'COMPLETED',
                        finished_at = statement_timestamp(),
                        result_summary = CAST(:result_summary AS jsonb)
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND step_id = CAST(:step_id AS uuid)
                      AND status = 'RUNNING'
                    RETURNING step_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "step_id": step_id,
                    "result_summary": json.dumps(completion_summary, separators=(",", ":")),
                },
            )
            if _first_row(result) is None:
                raise RuntimeError("WORKER_CLAIM_NOT_PROVEN")

        boundary_uow = build_postgres_step_boundary_uow(
            audit_sink=_FailAfterDurableAudit(),
            connection_factory=transaction_factory,
            lease_seconds=lease_seconds,
        )
        caught_code: str | None = None
        try:
            boundary_uow(
                run_id=run_id,
                tenant_id=tenant_id,
                step_name="INGEST_CHECK",
                boundary="COMPLETE",
                lease_owner_id=lease_owner_id,
                lease_fencing_token=1,
                mutation=complete_step,
                native_output_writes=(persist_native_output,),
            )
        except Exception as exc:
            caught_code = getattr(exc, "code", None)

        with transaction_factory() as verification_connection:
            tenant_setter(verification_connection, tenant_id)
            run_after = _first_row(
                verification_connection.execute(
                    _statement(
                        """
                        SELECT status, lease_owner_id::text AS lease_owner_id,
                               lease_fencing_token, last_heartbeat_at, lease_expires_at
                        FROM runs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id},
                )
            )
            step_after = _first_row(
                verification_connection.execute(
                    _statement(
                        """
                        SELECT status, result_summary
                        FROM run_steps
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                          AND step_id = CAST(:step_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id, "step_id": step_id},
                )
            )
            output_exists = _scalar(
                verification_connection.execute(
                    _statement(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM evidence_items
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND evidence_id = CAST(:evidence_id AS uuid)
                        )
                        """
                    ),
                    {"tenant_id": tenant_id, "evidence_id": evidence_id},
                )
            )
            boundary_audit_count = _integer(
                _scalar(
                    verification_connection.execute(
                        _statement(
                            """
                            SELECT COUNT(*) FROM audit_events
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND request_id = :run_id
                              AND event_type = 'worker.lease.heartbeat_renewed'
                            """
                        ),
                        {"tenant_id": tenant_id, "run_id": run_id},
                    )
                )
                or 0
            )

        raw_summary = _row_value(step_after, "result_summary", 1)
        if isinstance(raw_summary, str):
            raw_summary = json.loads(raw_summary)
        boundary_rollback_ok = bool(
            caught_code == "WORKER_LEASE_HEARTBEAT_FAILED"
            and run_after is not None
            and step_after is not None
            and _row_value(run_after, "status", 0) == "RUNNING"
            and str(_row_value(run_after, "lease_owner_id", 1)) == lease_owner_id
            and _integer(_row_value(run_after, "lease_fencing_token", 2)) == 1
            and _aware_datetime(_row_value(run_after, "last_heartbeat_at", 3))
            == _aware_datetime(_row_value(prior_run, "last_heartbeat_at", 3))
            and _aware_datetime(_row_value(run_after, "lease_expires_at", 4))
            == _aware_datetime(_row_value(prior_run, "lease_expires_at", 4))
            and _row_value(step_after, "status", 0) == "RUNNING"
            and raw_summary == prior_summary
            and output_exists is False
            and boundary_audit_count == 0
        )
    except Exception:
        boundary_rollback_ok = False
    finally:
        try:
            with transaction_factory() as cleanup_connection:
                tenant_setter(cleanup_connection, tenant_id)
                cleanup_connection.execute(
                    _statement(
                        """
                        DELETE FROM evidence_items
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND evidence_id = CAST(:evidence_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "evidence_id": evidence_id},
                )
                cleanup_connection.execute(
                    _statement(
                        """
                        DELETE FROM run_steps
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id},
                )
                cleanup_connection.execute(
                    _statement(
                        """
                        DELETE FROM runs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id},
                )
                cleanup_connection.execute(
                    _statement(
                        """
                        DELETE FROM deals
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND deal_id = CAST(:deal_id AS uuid)
                        """
                    ),
                    {"tenant_id": tenant_id, "deal_id": deal_id},
                )
            with transaction_factory() as residue_connection:
                tenant_setter(residue_connection, tenant_id)
                residue = _scalar(
                    residue_connection.execute(
                        _statement(
                            """
                            SELECT
                                EXISTS (
                                    SELECT 1 FROM deals
                                    WHERE deal_id = CAST(:deal_id AS uuid)
                                )
                                OR EXISTS (
                                    SELECT 1 FROM runs
                                    WHERE run_id = CAST(:run_id AS uuid)
                                )
                                OR EXISTS (
                                    SELECT 1 FROM run_steps
                                    WHERE run_id = CAST(:run_id AS uuid)
                                )
                                OR EXISTS (
                                    SELECT 1 FROM evidence_items
                                    WHERE evidence_id = CAST(:evidence_id AS uuid)
                                )
                                OR EXISTS (
                                    SELECT 1 FROM audit_events
                                    WHERE request_id = :run_id
                                      AND event_type = 'worker.lease.heartbeat_renewed'
                                )
                            """
                        ),
                        {
                            "deal_id": deal_id,
                            "run_id": run_id,
                            "evidence_id": evidence_id,
                        },
                    )
                )
                no_residue = residue is False
        except Exception:
            no_residue = False
    return boundary_rollback_ok, no_residue


def collect_worker_claim_facts(
    *,
    connection_factory: Callable[[], Any],
    boundary_transaction_factory: Callable[[], Any],
    tenant_setter: Callable[[Any, str], None],
    runs_repository_factory: Callable[[Any, str], Any],
    run_steps_repository_factory: Callable[[Any, str], Any],
    run_step_factory: Callable[..., Any],
    worker_lease_fenced_error: type[BaseException],
    audit_sink_factory: Callable[[], Any],
    lease_audit_emitter_factory: Callable[..., Callable[..., None]],
    lease_seconds: int,
    boundary_rollback_probe: Callable[..., tuple[bool, bool]] | None = None,
    uuid_factory: Callable[[], object] = uuid.uuid4,
) -> WorkerProbeFacts:
    """Prove the canonical queued-run claim without leaving product rows behind.

    The probe uses a fresh tenant namespace so it cannot claim an operator's queued
    run.  The deal seed, queued run, atomic lease claim, and audit callback all live
    in one transaction that is unconditionally rolled back.  A second transaction
    verifies that neither seed row survived.  A separate committed-prefix probe
    invokes the production fenced step-boundary UoW and injects an audit failure
    only after its staged output, RunStep completion, heartbeat, and audit writes.
    """

    def next_uuid() -> str:
        candidate = uuid_factory()
        raw_hex = getattr(candidate, "hex", None)
        if isinstance(raw_hex, str):
            return str(uuid.UUID(hex=raw_hex))
        return str(candidate)

    probe_tenant_id = next_uuid()
    deal_id = next_uuid()
    run_id = next_uuid()
    lease_owner_id = next_uuid()
    successor_owner_id = next_uuid()
    step_id = next_uuid()
    queued_row_ok = False
    claim_ok = False
    fencing_ok = False
    claim_audit_ok = False
    heartbeat_ok = False
    reclaim_ok = False
    fresh_claim_ok = False
    stale_write_rejected = False
    claim_probe_rolled_back = False
    claim_probe_no_residue = False

    try:
        with connection_factory() as connection:
            transaction = connection.begin()
            try:
                tenant_setter(connection, probe_tenant_id)
                connection.execute(
                    _statement(
                        """
                        INSERT INTO deals (deal_id, tenant_id, name, created_at)
                        VALUES (
                            CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                            :name, statement_timestamp()
                        )
                        """
                    ),
                    {
                        "deal_id": deal_id,
                        "tenant_id": probe_tenant_id,
                        "name": "strict-worker-claim-readiness",
                    },
                )
                repository = runs_repository_factory(connection, probe_tenant_id)
                queued = repository.create(
                    run_id=run_id,
                    deal_id=deal_id,
                    mode="SNAPSHOT",
                    idempotency_key=f"strict-worker-claim-readiness-{run_id}",
                    source={"type": "dependency_readiness_probe"},
                    created_by_actor_id="strict-worker-claim-readiness",
                    created_by_actor_type="SERVICE",
                )
                queued_row_ok = (
                    isinstance(queued, Mapping)
                    and queued.get("status") == "QUEUED"
                    and str(queued.get("tenant_id")) == probe_tenant_id
                )

                audit_sink = audit_sink_factory()
                claim_audit_emitter = lease_audit_emitter_factory(
                    audit_sink=audit_sink,
                    tenant_id=probe_tenant_id,
                )

                claimed_rows = repository.claim_queued_runs(
                    limit=1,
                    lease_owner_id=lease_owner_id,
                    lease_seconds=lease_seconds,
                    lease_audit_emitter=claim_audit_emitter,
                )
                claimed = claimed_rows[0] if len(claimed_rows) == 1 else None
                claim_ok = (
                    isinstance(claimed, Mapping)
                    and claimed.get("status") == "RUNNING"
                    and str(claimed.get("lease_owner_id")) == lease_owner_id
                )
                fencing_ok = (
                    isinstance(claimed, Mapping)
                    and _integer(claimed.get("lease_fencing_token") or 0) == 1
                    and bool(claimed.get("lease_expires_at"))
                    and bool(claimed.get("last_heartbeat_at"))
                )

                heartbeat_params = {
                    "tenant_id": probe_tenant_id,
                    "run_id": run_id,
                    "lease_owner_id": lease_owner_id,
                }
                heartbeat_before = _first_row(
                    connection.execute(
                        _statement(
                            """
                            SELECT last_heartbeat_at, lease_expires_at,
                                   statement_timestamp() AS observed_at
                            FROM runs
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND run_id = CAST(:run_id AS uuid)
                              AND status = 'RUNNING'
                              AND lease_owner_id = CAST(:lease_owner_id AS uuid)
                              AND lease_fencing_token = 1
                            """
                        ),
                        heartbeat_params,
                    )
                )
                renewed = repository.renew_worker_lease(
                    run_id=run_id,
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=1,
                    lease_seconds=lease_seconds,
                    lease_audit_emitter=claim_audit_emitter,
                )
                heartbeat_after = _first_row(
                    connection.execute(
                        _statement(
                            """
                            SELECT last_heartbeat_at, lease_expires_at,
                                   statement_timestamp() AS observed_at
                            FROM runs
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND run_id = CAST(:run_id AS uuid)
                              AND status = 'RUNNING'
                              AND lease_owner_id = CAST(:lease_owner_id AS uuid)
                              AND lease_fencing_token = 1
                            """
                        ),
                        heartbeat_params,
                    )
                )
                before_heartbeat = _aware_datetime(
                    _row_value(heartbeat_before, "last_heartbeat_at", 0)
                )
                before_expiry = _aware_datetime(_row_value(heartbeat_before, "lease_expires_at", 1))
                after_heartbeat = _aware_datetime(
                    _row_value(heartbeat_after, "last_heartbeat_at", 0)
                )
                after_expiry = _aware_datetime(_row_value(heartbeat_after, "lease_expires_at", 1))
                after_observed_at = _aware_datetime(_row_value(heartbeat_after, "observed_at", 2))
                heartbeat_ok = bool(
                    renewed is True
                    and before_heartbeat is not None
                    and before_expiry is not None
                    and after_heartbeat is not None
                    and after_expiry is not None
                    and after_observed_at is not None
                    and after_heartbeat > before_heartbeat
                    and after_expiry > before_expiry
                    and after_expiry > after_observed_at
                )
                expired = _first_row(
                    connection.execute(
                        _statement(
                            """
                            UPDATE runs
                            SET lease_expires_at = statement_timestamp() - INTERVAL '1 second'
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND run_id = CAST(:run_id AS uuid)
                              AND status = 'RUNNING'
                              AND lease_owner_id = CAST(:lease_owner_id AS uuid)
                              AND lease_fencing_token = 1
                            RETURNING run_id
                            """
                        ),
                        {
                            "tenant_id": probe_tenant_id,
                            "run_id": run_id,
                            "lease_owner_id": lease_owner_id,
                        },
                    )
                )
                reclaimed_rows = (
                    repository.requeue_expired_worker_leases(
                        limit=1,
                        lease_audit_emitter=claim_audit_emitter,
                    )
                    if expired is not None
                    else []
                )
                reclaimed = reclaimed_rows[0] if len(reclaimed_rows) == 1 else None
                reclaim_ok = (
                    isinstance(reclaimed, Mapping)
                    and reclaimed.get("status") == "QUEUED"
                    and reclaimed.get("lease_owner_id") is None
                    and _integer(reclaimed.get("lease_fencing_token") or 0) == 2
                )

                successor_rows = repository.claim_queued_runs(
                    limit=1,
                    lease_owner_id=successor_owner_id,
                    lease_seconds=lease_seconds,
                    lease_audit_emitter=claim_audit_emitter,
                )
                successor = successor_rows[0] if len(successor_rows) == 1 else None
                fresh_claim_ok = (
                    isinstance(successor, Mapping)
                    and successor.get("status") == "RUNNING"
                    and str(successor.get("lease_owner_id")) == successor_owner_id
                    and _integer(successor.get("lease_fencing_token") or 0) == 3
                )

                step_repository = run_steps_repository_factory(connection, probe_tenant_id)
                probe_step = run_step_factory(
                    step_id=step_id,
                    run_id=run_id,
                    tenant_id=probe_tenant_id,
                    step_name="INGEST_CHECK",
                    step_order=2,
                )
                try:
                    step_repository.create(
                        probe_step,
                        lease_owner_id=lease_owner_id,
                        lease_fencing_token=1,
                    )
                except worker_lease_fenced_error:
                    stale_write_rejected = True

                audit_rows = _rows(
                    connection.execute(
                        _statement(
                            """
                            SELECT event_type, request_id, event
                            FROM audit_events
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND request_id = :run_id
                              AND event_type IN (
                                  'worker.lease.claimed',
                                  'worker.lease.heartbeat_renewed',
                                  'worker.lease.expired',
                                  'worker.lease.reclaimed'
                              )
                            """
                        ),
                        {
                            "tenant_id": probe_tenant_id,
                            "run_id": run_id,
                        },
                    )
                )
                safe_events: list[tuple[str, Mapping[str, object]]] = []
                for audit_row in audit_rows:
                    raw_event = _row_value(audit_row, "event", 2)
                    if isinstance(raw_event, str):
                        raw_event = json.loads(raw_event)
                    safe = (
                        raw_event.get("payload", {}).get("safe", {})
                        if isinstance(raw_event, Mapping)
                        else {}
                    )
                    actor = raw_event.get("actor") if isinstance(raw_event, Mapping) else None
                    request = raw_event.get("request") if isinstance(raw_event, Mapping) else None
                    if not (
                        isinstance(raw_event, Mapping)
                        and isinstance(safe, Mapping)
                        and isinstance(actor, Mapping)
                        and actor.get("actor_type") == "SERVICE"
                        and actor.get("actor_id") == "idis-pipeline-worker"
                        and isinstance(request, Mapping)
                        and request.get("request_id") == run_id
                        and request.get("method") == "POST"
                        and request.get("path") == f"/v1/runs/{run_id}/worker-lease"
                        and request.get("status_code") == 200
                        and "execution_origin" not in safe
                        and raw_event.get("tenant_id") == probe_tenant_id
                        and str(_row_value(audit_row, "request_id", 1)) == run_id
                        and str(safe.get("run_id")) == run_id
                    ):
                        continue
                    safe_events.append((str(_row_value(audit_row, "event_type", 0)), safe))

                claims = [
                    safe for event_type, safe in safe_events if event_type == "worker.lease.claimed"
                ]
                heartbeat = [
                    safe
                    for event_type, safe in safe_events
                    if event_type == "worker.lease.heartbeat_renewed"
                ]
                expired_events = [
                    safe for event_type, safe in safe_events if event_type == "worker.lease.expired"
                ]
                reclaimed_events = [
                    safe
                    for event_type, safe in safe_events
                    if event_type == "worker.lease.reclaimed"
                ]
                claim_audit_ok = all(
                    (
                        len(safe_events) == 5,
                        sorted(_integer(item.get("new_fencing_token") or 0) for item in claims)
                        == [1, 3],
                        {str(item.get("lease_owner_id")) for item in claims}
                        == {lease_owner_id, successor_owner_id},
                        len(heartbeat) == 1,
                        _integer(heartbeat[0].get("current_fencing_token") or 0) == 1
                        if heartbeat
                        else False,
                        len(expired_events) == 1,
                        _integer(expired_events[0].get("prior_fencing_token") or 0) == 1
                        if expired_events
                        else False,
                        _integer(expired_events[0].get("new_fencing_token") or 0) == 2
                        if expired_events
                        else False,
                        len(reclaimed_events) == 1,
                        _integer(reclaimed_events[0].get("prior_fencing_token") or 0) == 1
                        if reclaimed_events
                        else False,
                        _integer(reclaimed_events[0].get("new_fencing_token") or 0) == 2
                        if reclaimed_events
                        else False,
                    )
                )
            finally:
                transaction.rollback()
                claim_probe_rolled_back = True
    except Exception:
        pass

    if claim_probe_rolled_back:
        try:
            with connection_factory() as verification_connection:
                verification_transaction = verification_connection.begin()
                try:
                    tenant_setter(verification_connection, probe_tenant_id)
                    residue = _scalar(
                        verification_connection.execute(
                            _statement(
                                """
                                SELECT
                                    EXISTS (
                                        SELECT 1 FROM deals
                                        WHERE deal_id = CAST(:deal_id AS uuid)
                                    )
                                    OR EXISTS (
                                        SELECT 1 FROM runs
                                        WHERE run_id = CAST(:run_id AS uuid)
                                    )
                                    OR EXISTS (
                                        SELECT 1 FROM audit_events
                                        WHERE request_id = :run_id
                                          AND event_type LIKE 'worker.lease.%'
                                    )
                                    OR EXISTS (
                                        SELECT 1 FROM run_steps
                                        WHERE run_id = CAST(:run_id AS uuid)
                                    )
                                """
                            ),
                            {"deal_id": deal_id, "run_id": run_id},
                        )
                    )
                    claim_probe_no_residue = residue is False
                finally:
                    verification_transaction.rollback()
        except Exception:
            claim_probe_no_residue = False

    boundary_rollback_ok = False
    boundary_probe_no_residue = False
    if claim_probe_no_residue:
        probe = boundary_rollback_probe or _collect_worker_boundary_rollback_facts
        try:
            boundary_result = probe(
                transaction_factory=boundary_transaction_factory,
                tenant_setter=tenant_setter,
                audit_sink_factory=audit_sink_factory,
                tenant_id=probe_tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                lease_owner_id=successor_owner_id,
                step_id=step_id,
                lease_seconds=lease_seconds,
            )
            if isinstance(boundary_result, tuple) and len(boundary_result) == 2:
                boundary_rollback_ok = boundary_result[0] is True
                boundary_probe_no_residue = boundary_result[1] is True
        except Exception:
            pass

    return WorkerProbeFacts(
        backend="postgres",
        queued_row_ok=queued_row_ok,
        claim_ok=claim_ok,
        fencing_ok=fencing_ok,
        claim_audit_ok=claim_audit_ok,
        heartbeat_ok=heartbeat_ok,
        reclaim_ok=reclaim_ok,
        fresh_claim_ok=fresh_claim_ok,
        stale_write_rejected=stale_write_rejected,
        boundary_rollback_ok=boundary_rollback_ok,
        no_residue=claim_probe_no_residue and boundary_probe_no_residue,
    )


def _operation_ok(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, tuple) and value:
        return bool(value[0])
    return bool(getattr(value, "ok", False))


def _token_caps_present(components: Mapping[str, object]) -> bool:
    anthropic = components.get("anthropic")
    embeddings = components.get("openai_embeddings")
    if not isinstance(anthropic, Mapping) or not isinstance(embeddings, Mapping):
        return False
    return all(
        isinstance(value, int) and value > 0
        for value in (
            anthropic.get("max_input_tokens"),
            anthropic.get("max_output_tokens"),
            embeddings.get("max_embedding_tokens"),
        )
    )


_BUDGET_PROBE_DIMENSIONS = (
    "calls",
    "usd",
    "input_tokens",
    "output_tokens",
    "embedding_tokens",
)


def _budget_number(snapshot: Mapping[str, object], name: str) -> Decimal | None:
    value = snapshot.get(name)
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def _budget_delta_matches(
    *,
    before: Mapping[str, object],
    after: Mapping[str, object],
    prefix: str,
    expected: Mapping[str, object],
) -> bool:
    for dimension in _BUDGET_PROBE_DIMENSIONS:
        before_value = _budget_number(before, f"{prefix}_{dimension}")
        after_value = _budget_number(after, f"{prefix}_{dimension}")
        expected_value = _budget_number(expected, dimension)
        if (
            before_value is None
            or after_value is None
            or expected_value is None
            or after_value - before_value != expected_value
        ):
            return False
    return True


def _budget_field_delta_matches(
    *,
    before: Mapping[str, object],
    after: Mapping[str, object],
    field: str,
    expected: object,
) -> bool:
    before_value = _budget_number(before, field)
    after_value = _budget_number(after, field)
    expected_value = _budget_number({"expected": expected}, "expected")
    return bool(
        before_value is not None
        and after_value is not None
        and expected_value is not None
        and after_value - before_value == expected_value
    )


def _budget_prefix_equal(
    left: Mapping[str, object],
    right: Mapping[str, object],
    prefix: str,
) -> bool:
    for dimension in _BUDGET_PROBE_DIMENSIONS:
        left_value = _budget_number(left, f"{prefix}_{dimension}")
        right_value = _budget_number(right, f"{prefix}_{dimension}")
        if left_value is None or right_value is None or left_value != right_value:
            return False
    return True


def _budget_dimensions_equal(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> bool:
    return _budget_prefix_equal(left, right, "reserved") and _budget_prefix_equal(
        left, right, "consumed"
    )


def _budget_snapshot(store: Any, values: Mapping[str, object]) -> Mapping[str, object] | None:
    snapshot = store.snapshot(
        tenant_id=values["tenant_id"],
        policy_hash=values["policy_hash"],
        component_id=values["component_id"],
    )
    return snapshot if isinstance(snapshot, Mapping) else None


def _collect_generic_budget_lifecycle(
    *,
    first_store: Any,
    store_factory: Callable[[], Any],
    common: Mapping[str, object],
    consumed_reservation: str,
    refunded_reservation: str,
) -> tuple[bool, bool, bool, bool, bool]:
    """Exercise generic injected stores that do not expose the Postgres proof API."""
    call_reservation_ok = False
    usd_reservation_ok = False
    consume_ok = False
    refund_ok = False
    observer = store_factory()
    baseline = _budget_snapshot(observer, common)
    if baseline is None:
        raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
    reserve_result = first_store.reserve(
        **common,
        reservation_id=consumed_reservation,
    )
    reserve_operation_ok = _operation_ok(reserve_result)
    reserved = _budget_snapshot(observer, common)
    if reserved is not None and reserve_operation_ok:
        call_reservation_ok = _budget_field_delta_matches(
            before=baseline,
            after=reserved,
            field="reserved_calls",
            expected=1,
        )
        usd_reservation_ok = _budget_field_delta_matches(
            before=baseline,
            after=reserved,
            field="reserved_usd",
            expected=Decimal("0.01"),
        )
    if reserve_operation_ok:
        consume_result = first_store.consume(
            **common,
            reservation_id=consumed_reservation,
        )
        consumed = _budget_snapshot(observer, common)
        consume_ok = bool(
            _operation_ok(consume_result)
            and consumed is not None
            and _budget_prefix_equal(consumed, baseline, "reserved")
            and _budget_delta_matches(
                before=baseline,
                after=consumed,
                prefix="consumed",
                expected=common,
            )
            and all(
                _budget_number(consumed, f"reserved_{dimension}")
                == _budget_number(baseline, f"reserved_{dimension}")
                for dimension in _BUDGET_PROBE_DIMENSIONS
            )
        )
    else:
        consumed = None
    second_reserve = first_store.reserve(
        **common,
        reservation_id=refunded_reservation,
    )
    second_reserved = _budget_snapshot(observer, common)
    if _operation_ok(second_reserve):
        refund_result = first_store.refund(
            **common,
            reservation_id=refunded_reservation,
        )
        refunded = _budget_snapshot(observer, common)
        refund_ok = bool(
            consumed is not None
            and second_reserved is not None
            and refunded is not None
            and _operation_ok(refund_result)
            and _budget_delta_matches(
                before=consumed,
                after=second_reserved,
                prefix="reserved",
                expected=common,
            )
            and all(
                _budget_number(second_reserved, f"consumed_{dimension}")
                == _budget_number(consumed, f"consumed_{dimension}")
                for dimension in _BUDGET_PROBE_DIMENSIONS
            )
            and _budget_dimensions_equal(refunded, consumed)
        )
    else:
        refunded = None

    restart_store = store_factory()
    snapshot = _budget_snapshot(restart_store, common)
    restart_read_ok = bool(
        snapshot is not None
        and consumed is not None
        and refunded is not None
        and snapshot.get("policy_hash") == common["policy_hash"]
        and _budget_dimensions_equal(snapshot, consumed)
        and call_reservation_ok
        and usd_reservation_ok
        and consume_ok
        and refund_ok
    )
    return (
        call_reservation_ok,
        usd_reservation_ok,
        consume_ok,
        refund_ok,
        restart_read_ok,
    )


def collect_provider_budget_facts(
    *,
    policy_loader: Callable[[str], object | None],
    store_factory: Callable[[], Any],
    tenant_id: str,
    global_max_calls: int,
) -> BudgetProbeFacts:
    """Load signed 17-row policy and prove reserve/consume/refund/restart semantics."""
    try:
        policy = policy_loader(tenant_id)
    except Exception:
        policy = None
    policy_loaded = bool(
        policy is not None
        and getattr(policy, "signature_verified", False) is True
        and getattr(policy, "tenant_id", None) == tenant_id
    )
    components_object = getattr(policy, "components", {}) if policy_loaded else {}
    components = components_object if isinstance(components_object, Mapping) else {}
    configured = frozenset(str(component) for component in components)
    currency = str(getattr(policy, "currency", "")) if policy_loaded else ""
    token_caps = policy_loaded and _token_caps_present(components)

    backend = "memory"
    call_reservation_ok = False
    usd_reservation_ok = False
    consume_ok = False
    refund_ok = False
    restart_read_ok = False
    first_store: Any | None = None
    if not policy_loaded:
        return BudgetProbeFacts(
            backend=backend,
            policy_loaded=False,
            policy_currency=currency,
            configured_provider_ids=frozenset(),
            token_caps_present=False,
            call_reservation_ok=False,
            usd_reservation_ok=False,
            consume_ok=False,
            refund_ok=False,
            restart_read_ok=False,
        )

    common = {
        "tenant_id": tenant_id,
        "policy_hash": str(getattr(policy, "policy_hash", "")),
        "component_id": "anthropic",
        "calls": 1,
        "usd": Decimal("0.01"),
        "input_tokens": 1,
        "output_tokens": 1,
        "embedding_tokens": 0,
        "global_max_calls": global_max_calls,
    }
    # Reservation IDs bind every authority-bearing field in the immutable ledger vector.
    # In particular, changing the construction-bound global cap must create a new lifecycle
    # instead of colliding with an old reservation ID whose recorded vector cannot change.
    reservation_scope = json.dumps(
        {
            **common,
            "usd": format(Decimal(str(common["usd"])), "f"),
            "probe_contract": "budget-readiness-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    consumed_reservation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{reservation_scope}:consumed"))
    refunded_reservation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{reservation_scope}:refunded"))
    try:
        first_store = store_factory()
        backend = "postgres" if getattr(first_store, "is_durable", False) is True else "memory"
        prepare = getattr(first_store, "prepare_probe_state", None)
        if callable(prepare):
            prepare(**common)
        durable_proof = getattr(first_store, "prove_probe_lifecycle", None)
        if callable(durable_proof):
            proof = durable_proof(
                **common,
                consumed_reservation_id=consumed_reservation,
                refunded_reservation_id=refunded_reservation,
            )
            if not isinstance(proof, Mapping):
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            call_reservation_ok = proof.get("call_reservation_ok") is True
            usd_reservation_ok = proof.get("usd_reservation_ok") is True
            consume_ok = proof.get("consume_ok") is True
            refund_ok = proof.get("refund_ok") is True
            restart_read_ok = proof.get("restart_read_ok") is True
        else:
            (
                call_reservation_ok,
                usd_reservation_ok,
                consume_ok,
                refund_ok,
                restart_read_ok,
            ) = _collect_generic_budget_lifecycle(
                first_store=first_store,
                store_factory=store_factory,
                common=common,
                consumed_reservation=consumed_reservation,
                refunded_reservation=refunded_reservation,
            )
    except Exception:
        pass
    finally:
        if first_store is not None:
            finalize = getattr(first_store, "finalize_probe_state", None)
            if callable(finalize):
                try:
                    if finalize(**common) is not True:
                        restart_read_ok = False
                except Exception:
                    restart_read_ok = False
    return BudgetProbeFacts(
        backend=backend,
        policy_loaded=policy_loaded,
        policy_currency=currency,
        configured_provider_ids=configured,
        token_caps_present=token_caps,
        call_reservation_ok=call_reservation_ok,
        usd_reservation_ok=usd_reservation_ok,
        consume_ok=consume_ok,
        refund_ok=refund_ok,
        restart_read_ok=restart_read_ok,
    )


@dataclass(frozen=True, slots=True)
class DurableDependencyProbeAdapters:
    """Injected operational observations required by strict admission."""

    postgres: Callable[[], PostgresProbeFacts]
    audit: Callable[[], AuditProbeFacts]
    idempotency: Callable[[], IdempotencyProbeFacts]
    redis: Callable[[], RedisProbeFacts]
    object_store: Callable[[], ObjectStoreProbeFacts]
    ingestion: Callable[[], IngestionProbeFacts]
    worker: Callable[[], WorkerProbeFacts]
    budgets: Callable[[], BudgetProbeFacts]
    erasure_executor: Callable[[], ErasureExecutorProbeFacts] | None = None


def run_durable_dependency_admission(
    *,
    env: Mapping[str, str],
    probes: DurableDependencyProbeAdapters,
    expected_role: str,
    worktree_root: Path,
    temp_root: Path,
    admission_scope: str = FULL_DEPENDENCY_ADMISSION_SCOPE,
) -> DependencyAdmissionReport:
    """Run the exact typed probe inventory authorized for one admission scope."""
    postgres_check = check_postgres_readiness(env=env, probe=probes.postgres)

    def foundation_tail() -> tuple[ReadinessCheck, ...]:
        return (
            check_audit_readiness(probe=probes.audit),
            check_idempotency_readiness(env=env, probe=probes.idempotency),
            check_redis_readiness(env=env, probe=probes.redis),
            check_object_store_readiness(
                env=env,
                probe=probes.object_store,
                worktree_root=worktree_root,
                temp_root=temp_root,
            ),
            check_ingestion_readiness(env=env, probe=probes.ingestion),
            check_auth_readiness(env=env),
        )

    checks: tuple[ReadinessCheck, ...]
    if admission_scope == TASK4_INGESTION_ADMISSION_SCOPE:
        checks = (postgres_check, *foundation_tail())
    else:
        erasure_probe = probes.erasure_executor
        if erasure_probe is None:

            def unavailable_erasure_probe() -> ErasureExecutorProbeFacts:
                raise RuntimeError("ERASURE_EXECUTOR_POSTURE_BLOCKED")

            erasure_probe = unavailable_erasure_probe
        checks = (
            postgres_check,
            check_erasure_executor_readiness(
                env=env,
                expected_role=expected_role,
                probe=erasure_probe,
            ),
            *foundation_tail(),
            check_worker_readiness(
                env=env,
                expected_role=expected_role,
                probe=probes.worker,
            ),
            check_budget_readiness(env=env, probe=probes.budgets),
        )
    return build_dependency_admission_report(checks, admission_scope=admission_scope)


def _load_durable_budget_policy(
    *,
    connection_factory: Callable[[], Any],
    tenant_setter: Callable[[Any, str], None],
    tenant_id: str,
    env: Mapping[str, str],
    expected_public_key_sha256: str,
) -> object | None:
    from idis.persistence.repositories.provider_budget_policies import (
        PostgresProviderBudgetPoliciesRepository,
        ProviderBudgetPolicyPersistenceError,
    )
    from idis.runtime.governance_envelopes import BUDGET_SCHEMA_VERSION, verify_envelope
    from idis.runtime.governance_import import _load_json_object, _read_public_key

    try:
        public_pem = _read_public_key(
            str(env.get("IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH", ""))
        )
        opened_public_key_sha256 = hashlib.sha256(public_pem).hexdigest()
        if len(expected_public_key_sha256) != 64 or not secrets.compare_digest(
            opened_public_key_sha256,
            expected_public_key_sha256,
        ):
            return None
        verified = verify_envelope(
            envelope=_load_json_object(str(env.get("IDIS_PROVIDER_BUDGET_POLICY_PATH", ""))),
            trusted_signer_id=str(env.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", "")),
            trusted_public_key_pem=public_pem,
        )
        if (
            verified.schema_version != BUDGET_SCHEMA_VERSION
            or verified.payload.get("tenant_id") != tenant_id
        ):
            return None
    except Exception:
        return None

    try:
        with connection_factory() as connection:
            tenant_setter(connection, tenant_id)
            repository = PostgresProviderBudgetPoliciesRepository(
                connection,
                tenant_id,
                tenant_context_is_set=True,
            )
            return repository.load_active_verified_policy(
                verified_envelope=verified,
                trusted_signer_id=str(env.get("IDIS_GOVERNANCE_TRUSTED_SIGNER_ID", "")),
                trusted_public_key_sha256=expected_public_key_sha256,
                expected_imported_by_actor_id="slice100-governance-admin",
            )
    except ProviderBudgetPolicyPersistenceError:
        return None


def _budget_ledger_vector(safe: Mapping[str, object]) -> dict[str, object] | None:
    expected_fields = {
        "reservation_id",
        "policy_hash",
        "component_id",
        "calls",
        "usd",
        "input_tokens",
        "output_tokens",
        "embedding_tokens",
        "global_max_calls",
        "state",
    }
    if set(safe) != expected_fields:
        return None
    try:
        reservation_id = str(uuid.UUID(str(safe["reservation_id"])))
        usd = Decimal(str(safe["usd"]))
    except (ArithmeticError, KeyError, ValueError):
        return None
    integers = {
        name: safe[name]
        for name in (
            "calls",
            "input_tokens",
            "output_tokens",
            "embedding_tokens",
            "global_max_calls",
        )
    }
    if (
        reservation_id != safe["reservation_id"]
        or type(integers["calls"]) is not int
        or integers["calls"] <= 0
        or type(integers["global_max_calls"]) is not int
        or integers["global_max_calls"] <= 0
        or any(
            type(integers[name]) is not int or integers[name] < 0  # type: ignore[operator]
            for name in ("input_tokens", "output_tokens", "embedding_tokens")
        )
        or not usd.is_finite()
        or usd < 0
    ):
        return None
    policy_hash = safe["policy_hash"]
    component_id = safe["component_id"]
    state = safe["state"]
    if (
        not isinstance(policy_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", policy_hash) is None
        or not isinstance(component_id, str)
        or not component_id
        or state not in {"RESERVED", "CONSUMED", "REFUNDED"}
    ):
        return None
    return {
        "reservation_id": reservation_id,
        "policy_hash": policy_hash,
        "component_id": component_id,
        "calls": integers["calls"],
        "usd": usd,
        "input_tokens": integers["input_tokens"],
        "output_tokens": integers["output_tokens"],
        "embedding_tokens": integers["embedding_tokens"],
        "global_max_calls": integers["global_max_calls"],
        "state": state,
    }


def _budget_ledger_balances(
    rows: list[object],
    *,
    tenant_id: str,
    component_id: str,
) -> dict[str, object] | None:
    from idis.validators.audit_event_validator import validate_audit_event

    event_state = {
        "integration.provider_budget.reserved": "RESERVED",
        "integration.provider_budget.consumed": "CONSUMED",
        "integration.provider_budget.refunded": "REFUNDED",
    }
    # A cap/profile promotion does not rewrite historical evidence. Each reservation's cap is
    # part of its exact identity below, while aggregate reconciliation intentionally spans all
    # previously admitted cap values for this tenant/component.
    reservations: dict[str, tuple[dict[str, object], set[str]]] = {}
    for row in rows:
        event_type = str(_row_value(row, "event_type", 0))
        raw_event = _row_value(row, "event", 1)
        if isinstance(raw_event, str):
            try:
                raw_event = json.loads(raw_event)
            except json.JSONDecodeError:
                return None
        if not isinstance(raw_event, dict) or not validate_audit_event(raw_event).passed:
            return None
        payload = raw_event.get("payload")
        safe = payload.get("safe") if isinstance(payload, Mapping) else None
        resource = raw_event.get("resource")
        vector = _budget_ledger_vector(safe) if isinstance(safe, Mapping) else None
        state = event_state.get(event_type)
        if (
            vector is None
            or state is None
            or raw_event.get("event_type") != event_type
            or raw_event.get("tenant_id") != tenant_id
            or vector["component_id"] != component_id
            or vector["state"] != state
            or not isinstance(resource, Mapping)
            or resource.get("resource_id") != vector["reservation_id"]
        ):
            return None
        reservation_id = str(vector["reservation_id"])
        identity = {key: value for key, value in vector.items() if key != "state"}
        existing = reservations.get(reservation_id)
        if existing is None:
            reservations[reservation_id] = (identity, {state})
        elif existing[0] != identity or state in existing[1]:
            return None
        else:
            existing[1].add(state)

    balances: dict[str, object] = {
        "event_count": len(rows),
        "used": 0,
        "reserved_calls": 0,
        "reserved_usd": Decimal("0"),
        "consumed_usd": Decimal("0"),
        "reserved_input_tokens": 0,
        "consumed_input_tokens": 0,
        "reserved_output_tokens": 0,
        "consumed_output_tokens": 0,
        "reserved_embedding_tokens": 0,
        "consumed_embedding_tokens": 0,
    }
    active_policies: set[str] = set()
    all_policies: set[str] = set()
    for vector, states in reservations.values():
        policy_hash = str(vector["policy_hash"])
        all_policies.add(policy_hash)
        if states == {"RESERVED"}:
            prefix = "reserved"
            active_policies.add(policy_hash)
        elif states == {"RESERVED", "CONSUMED"}:
            prefix = "consumed"
            balances["used"] = _integer(balances["used"]) + _integer(vector["calls"])
        elif states == {"RESERVED", "REFUNDED"}:
            continue
        else:
            return None
        if prefix == "reserved":
            balances["reserved_calls"] = _integer(balances["reserved_calls"]) + _integer(
                vector["calls"]
            )
        usd_key = f"{prefix}_usd"
        balances[usd_key] = Decimal(str(balances[usd_key])) + Decimal(str(vector["usd"]))
        for dimension in ("input", "output", "embedding"):
            key = f"{prefix}_{dimension}_tokens"
            balances[key] = _integer(balances[key]) + _integer(vector[f"{dimension}_tokens"])
    balances["active_policies"] = active_policies
    balances["all_policies"] = all_policies
    return balances


def _budget_ledger_matches_aggregate(
    connection: Any,
    *,
    tenant_id: str,
    component_id: str,
    expected_global_max_calls: int,
) -> bool:
    if expected_global_max_calls <= 0:
        return False
    event_rows = _rows(
        connection.execute(
            _statement(
                """
                SELECT event_type, event
                FROM audit_events
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_type IN (
                      'integration.provider_budget.reserved',
                      'integration.provider_budget.consumed',
                      'integration.provider_budget.refunded'
                  )
                  AND event #>> '{payload,safe,component_id}' = :component_id
                ORDER BY occurred_at, event_id
                """
            ),
            {"tenant_id": tenant_id, "component_id": component_id},
        )
    )
    balances = _budget_ledger_balances(
        event_rows,
        tenant_id=tenant_id,
        component_id=component_id,
    )
    if balances is None:
        return False
    aggregate = _first_row(
        connection.execute(
            _statement(
                """
                SELECT used, policy_hash, reserved_calls, reserved_usd,
                       consumed_usd, reserved_input_tokens,
                       consumed_input_tokens, reserved_output_tokens,
                       consumed_output_tokens, reserved_embedding_tokens,
                       consumed_embedding_tokens
                FROM provider_budget_usage
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND provider = :component_id
                """
            ),
            {"tenant_id": tenant_id, "component_id": component_id},
        )
    )
    if aggregate is None:
        return balances["event_count"] == 0
    fields = (
        "used",
        "policy_hash",
        "reserved_calls",
        "reserved_usd",
        "consumed_usd",
        "reserved_input_tokens",
        "consumed_input_tokens",
        "reserved_output_tokens",
        "consumed_output_tokens",
        "reserved_embedding_tokens",
        "consumed_embedding_tokens",
    )
    actual = {name: _row_value(aggregate, name, index) for index, name in enumerate(fields)}
    exact_integer_fields = (
        "reserved_calls",
        "reserved_input_tokens",
        "consumed_input_tokens",
        "reserved_output_tokens",
        "consumed_output_tokens",
        "reserved_embedding_tokens",
        "consumed_embedding_tokens",
    )
    if any(_integer(actual[name] or 0) != balances[name] for name in exact_integer_fields):
        return False
    if any(
        Decimal(str(actual[name] or 0)) != balances[name]
        for name in ("reserved_usd", "consumed_usd")
    ):
        return False
    # Migration 0033 deliberately preserves the pre-ledger call-only ``used``
    # balance.  Without adding a forbidden provenance column, the only safe
    # reconciliation is to treat any non-negative excess over canonical CONSUMED
    # events as a conservative legacy baseline.  It still counts against both the
    # component and global cap and is never reset or made reusable by this probe.
    actual_used = _integer(actual["used"] or 0)
    ledger_used = _integer(balances["used"])
    if actual_used < ledger_used:
        return False
    if balances["event_count"] == 0:
        return True
    policy_hash = str(actual["policy_hash"] or "")
    active_policies = balances["active_policies"]
    all_policies = balances["all_policies"]
    if not isinstance(active_policies, set) or not isinstance(all_policies, set):
        return False
    if active_policies:
        return len(active_policies) == 1 and policy_hash in active_policies
    return policy_hash in all_policies


@dataclass(slots=True)
class _BudgetProbeSession:
    connection: Any | None = None


class PostgresBudgetLifecycleProbeStore:
    """Idempotent committed lifecycle proof with exact append-only reconciliation.

    A session advisory lock serializes the proof with production mutations.  One
    deterministic consumed debit is retained per tenant/policy and one deterministic
    reserve/refund lifecycle nets to zero.  Aggregate usage is never rewound: every
    cleanup verifies it exactly against immutable reservation events before unlocking.
    """

    is_durable = True

    def __init__(
        self,
        *,
        engine: Any,
        tenant_setter: Callable[[Any, str], None],
        global_max_calls: int,
        session: _BudgetProbeSession,
    ) -> None:
        from idis.providers.budget import PostgresProviderBudgetStore

        if global_max_calls <= 0:
            raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
        self._engine = engine
        self._tenant_setter = tenant_setter
        self._global_max_calls = global_max_calls
        self._session = session
        self._governed_store = PostgresProviderBudgetStore(
            connection_factory=engine.connect,
            tenant_setter=tenant_setter,
            global_max_calls=global_max_calls,
        )
        if self._governed_store.signed_policy_accounting_version != "provider-budget-policy-v1":
            raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")

    @staticmethod
    def _lock_key(tenant_id: str, _component_id: str) -> str:
        return f"idis:provider-budget:{tenant_id}"

    def _owner(self) -> Any:
        if self._session.connection is None:
            raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
        return self._session.connection

    def _commit(self, connection: Any) -> None:
        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()

    def _rollback(self, connection: Any) -> None:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()

    def prepare_probe_state(self, **values: object) -> None:
        if self._session.connection is not None:
            return
        tenant_id = str(values["tenant_id"])
        component_id = str(values["component_id"])
        connection = self._engine.connect()
        self._session.connection = connection
        try:
            connection.execute(
                _statement("SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": self._lock_key(tenant_id, component_id)},
            )
            self._commit(connection)
            self._tenant_setter(connection, tenant_id)
            if not _budget_ledger_matches_aggregate(
                connection,
                tenant_id=tenant_id,
                component_id=component_id,
                expected_global_max_calls=self._global_max_calls,
            ):
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            self._commit(connection)
        except Exception:
            self._rollback(connection)
            self._unlock_and_close(tenant_id=tenant_id, component_id=component_id)
            raise

    def _params(self, values: Mapping[str, object]) -> dict[str, object]:
        return {
            "tenant_id": str(values["tenant_id"]),
            "component_id": str(values["component_id"]),
            "policy_hash": str(values["policy_hash"]),
            "reservation_id": str(values["reservation_id"]),
            "calls": _integer(values["calls"]),
            "usd": Decimal(str(values["usd"])),
            "input_tokens": _integer(values["input_tokens"]),
            "output_tokens": _integer(values["output_tokens"]),
            "embedding_tokens": _integer(values["embedding_tokens"]),
            "global_max_calls": self._global_max_calls,
        }

    def reserve(self, **values: object) -> bool:
        params = self._params(values)
        connection = self._owner()
        try:
            allowed = self._governed_store.reserve_in_tx(connection, **params)
            self._commit(connection)
            return allowed
        except Exception:
            self._rollback(connection)
            raise

    def consume(self, **values: object) -> bool:
        params = self._params(values)
        connection = self._owner()
        try:
            consumed = self._governed_store.consume_in_tx(connection, **params)
            self._commit(connection)
            return consumed
        except Exception:
            self._rollback(connection)
            raise

    def refund(self, **values: object) -> bool:
        params = self._params(values)
        connection = self._owner()
        try:
            refunded = self._governed_store.refund_in_tx(connection, **params)
            self._commit(connection)
            return refunded
        except Exception:
            self._rollback(connection)
            raise

    def snapshot(self, **values: object) -> dict[str, object]:
        raw = self._governed_store.snapshot(
            tenant_id=str(values["tenant_id"]),
            policy_hash=str(values["policy_hash"]),
            component_id=str(values["component_id"]),
        )
        if raw.get("reserved") == -1:
            snapshot: dict[str, object] = {
                "policy_hash": None,
                "reserved_calls": 0,
                "consumed_calls": 0,
                "reserved_usd": Decimal("0"),
                "consumed_usd": Decimal("0"),
                "reserved_input_tokens": 0,
                "consumed_input_tokens": 0,
                "reserved_output_tokens": 0,
                "consumed_output_tokens": 0,
                "reserved_embedding_tokens": 0,
                "consumed_embedding_tokens": 0,
            }
        else:
            snapshot = self._normalize_snapshot(raw)
        return snapshot

    @staticmethod
    def _normalize_snapshot(source: Mapping[str, object]) -> dict[str, object]:
        return {
            "policy_hash": source.get("policy_hash"),
            "reserved_calls": _integer(source.get("reserved_calls") or 0),
            "consumed_calls": _integer(source.get("used") or 0),
            "reserved_usd": Decimal(str(source.get("reserved_usd") or 0)),
            "consumed_usd": Decimal(str(source.get("consumed_usd") or 0)),
            "reserved_input_tokens": _integer(source.get("reserved_input_tokens") or 0),
            "consumed_input_tokens": _integer(source.get("consumed_input_tokens") or 0),
            "reserved_output_tokens": _integer(source.get("reserved_output_tokens") or 0),
            "consumed_output_tokens": _integer(source.get("consumed_output_tokens") or 0),
            "reserved_embedding_tokens": _integer(source.get("reserved_embedding_tokens") or 0),
            "consumed_embedding_tokens": _integer(source.get("consumed_embedding_tokens") or 0),
        }

    def prove_probe_lifecycle(self, **values: object) -> dict[str, bool]:
        connection = self._owner()
        consumed_id = str(values.pop("consumed_reservation_id"))
        refunded_id = str(values.pop("refunded_reservation_id"))
        consumed = self._params({**values, "reservation_id": consumed_id})
        refunded = self._params({**values, "reservation_id": refunded_id})
        tenant_id = str(values["tenant_id"])
        component_id = str(values["component_id"])

        def state(target: Mapping[str, object], target_connection: Any = connection) -> str | None:
            self._tenant_setter(target_connection, tenant_id)
            return self._governed_store._reservation_state(target_connection, target)

        try:
            consumed_state = state(consumed)
            if consumed_state not in (None, "RESERVED", "CONSUMED"):
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            if consumed_state is None:
                if self._governed_store.reserve_in_tx(connection, **consumed) is not True:
                    raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
                self._commit(connection)
                consumed_state = "RESERVED"
            if consumed_state == "RESERVED":
                if self._governed_store.consume_in_tx(connection, **consumed) is not True:
                    raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
                self._commit(connection)
            if state(consumed) != "CONSUMED":
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")

            refunded_state = state(refunded)
            if refunded_state not in (None, "RESERVED", "REFUNDED"):
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            if refunded_state is None:
                if self._governed_store.reserve_in_tx(connection, **refunded) is not True:
                    raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
                self._commit(connection)
                refunded_state = "RESERVED"
            if refunded_state == "RESERVED":
                if self._governed_store.refund_in_tx(connection, **refunded) is not True:
                    raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
                self._commit(connection)
            if state(refunded) != "REFUNDED":
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")

            self._tenant_setter(connection, tenant_id)
            reconciled = _budget_ledger_matches_aggregate(
                connection,
                tenant_id=tenant_id,
                component_id=component_id,
                expected_global_max_calls=self._global_max_calls,
            )
            self._commit(connection)
            with self._engine.connect() as restart_connection:
                self._tenant_setter(restart_connection, tenant_id)
                restart_reconciled = _budget_ledger_matches_aggregate(
                    restart_connection,
                    tenant_id=tenant_id,
                    component_id=component_id,
                    expected_global_max_calls=self._global_max_calls,
                )
                restart_consumed = state(consumed, restart_connection) == "CONSUMED"
                restart_refunded = state(refunded, restart_connection) == "REFUNDED"
            lifecycle_ok = bool(reconciled and restart_reconciled)
            return {
                "call_reservation_ok": lifecycle_ok,
                "usd_reservation_ok": lifecycle_ok,
                "consume_ok": lifecycle_ok and restart_consumed,
                "refund_ok": lifecycle_ok and restart_refunded,
                "restart_read_ok": lifecycle_ok and restart_consumed and restart_refunded,
            }
        except Exception:
            self._rollback(connection)
            raise

    def finalize_probe_state(self, **values: object) -> bool:
        tenant_id = str(values["tenant_id"])
        component_id = str(values["component_id"])
        connection = self._session.connection
        if connection is None:
            return False
        try:
            self._tenant_setter(connection, tenant_id)
            reconciled = _budget_ledger_matches_aggregate(
                connection,
                tenant_id=tenant_id,
                component_id=component_id,
                expected_global_max_calls=self._global_max_calls,
            )
            self._commit(connection)
            if not reconciled:
                raise RuntimeError("PROVIDER_BUDGET_CONFIG_BLOCKED")
            return True
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._unlock_and_close(tenant_id=tenant_id, component_id=component_id)

    def _unlock_and_close(self, *, tenant_id: str, component_id: str) -> None:
        connection = self._session.connection
        if connection is None:
            return
        try:
            connection.execute(
                _statement("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                {"lock_key": self._lock_key(tenant_id, component_id)},
            )
            self._commit(connection)
        finally:
            connection.close()
            self._session.connection = None


def build_postgres_budget_probe_store_factory(
    *,
    engine: Any,
    tenant_setter: Callable[[Any, str], None],
    global_max_calls: int,
) -> Callable[[], PostgresBudgetLifecycleProbeStore]:
    """Bind one serialized, ledger-reconciled wrapper to the governed store."""
    session = _BudgetProbeSession()

    def build() -> PostgresBudgetLifecycleProbeStore:
        return PostgresBudgetLifecycleProbeStore(
            engine=engine,
            tenant_setter=tenant_setter,
            global_max_calls=global_max_calls,
            session=session,
        )

    return build


def _expected_alembic_head() -> str:
    """Resolve the packaged migration head instead of pinning a stale revision."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config_path = Path(__file__).resolve().parents[1] / "persistence" / "migrations" / "alembic.ini"
    head = ScriptDirectory.from_config(Config(str(config_path))).get_current_head()
    if not head:
        raise RuntimeError("DATABASE_SCHEMA_NOT_READY")
    return str(head)


def build_default_dependency_probe_adapters(
    env: Mapping[str, str],
    *,
    expected_public_key_sha256: str,
    staging_root: Path | None = None,
) -> DurableDependencyProbeAdapters:
    """Build lazy production adapters from the already-applied strict profile."""
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
    from idis.models.run_step import RunStep
    from idis.persistence import db
    from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
    from idis.persistence.repositories.runs import (
        PostgresRunsRepository,
        WorkerLeaseFencedError,
    )
    from idis.pipeline.worker import _lease_transition_audit_emitter
    from idis.services.ingestion.no_substantive_content_attestation import (
        load_document_no_substantive_content_attestation,
    )
    from idis.services.ingestion.password_manifest import load_document_password_manifest
    from idis.storage.defaults import build_configured_product_export_object_store

    tenant_id = env.get("IDIS_ACCEPTANCE_TENANT_ID", "")
    isolation_tenant_id = env.get("IDIS_ISOLATION_CANARY_TENANT_ID", "")
    resolved_staging_root = (
        Path(tempfile.gettempdir()).resolve() if staging_root is None else staging_root.resolve()
    )
    object_store_root = Path(env.get("IDIS_OBJECT_STORE_BASE_DIR", ""))
    erasure_engine: Any | None = None

    def connection_factory() -> Any:
        return db.begin_app_conn()

    def erasure_connection_factory() -> Any:
        nonlocal erasure_engine
        if erasure_engine is None:
            from sqlalchemy import create_engine

            erasure_engine = create_engine(
                db._ensure_psycopg_driver(env.get("IDIS_ERASURE_DATABASE_URL", "")),
                pool_size=1,
                max_overflow=0,
                pool_pre_ping=True,
                echo=False,
            )
        return erasure_engine.connect()

    def worker_connection_factory() -> Any:
        return db.get_app_engine().connect()

    def worker_boundary_transaction_factory() -> Any:
        return db.get_app_engine().begin()

    def redis_client_factory() -> object:
        redis_module = __import__("redis")
        return redis_module.Redis.from_url(env.get("IDIS_REDIS_URL", ""), protocol=2)

    def object_store_factory() -> object:
        store = build_configured_product_export_object_store(env)
        if store is None:
            raise RuntimeError("OBJECT_STORE_HEALTH_BLOCKED")
        return store

    def budget_probe() -> BudgetProbeFacts:
        store_factory: Callable[[], PostgresBudgetLifecycleProbeStore] | None = None
        global_max_calls = _integer(env.get("IDIS_PROVIDER_BUDGET_MAX_CALLS", "0"))

        def policy_loader(requested_tenant: str) -> object | None:
            policy = _load_durable_budget_policy(
                connection_factory=connection_factory,
                tenant_setter=db.set_tenant_local,
                tenant_id=requested_tenant,
                env=env,
                expected_public_key_sha256=expected_public_key_sha256,
            )
            return policy

        def build_store() -> PostgresBudgetLifecycleProbeStore:
            nonlocal store_factory
            if store_factory is None:
                store_factory = build_postgres_budget_probe_store_factory(
                    engine=db.get_app_engine(),
                    tenant_setter=db.set_tenant_local,
                    global_max_calls=global_max_calls,
                )
            return store_factory()

        return collect_provider_budget_facts(
            policy_loader=policy_loader,
            store_factory=build_store,
            tenant_id=tenant_id,
            global_max_calls=global_max_calls,
        )

    return DurableDependencyProbeAdapters(
        postgres=lambda: collect_postgres_facts(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            isolation_tenant_id=isolation_tenant_id,
            expected_schema_revision=_expected_alembic_head(),
            required_rls_tables=_DEFAULT_RLS_TABLES,
        ),
        erasure_executor=lambda: collect_erasure_executor_facts(
            app_connection_factory=connection_factory,
            erasure_connection_factory=erasure_connection_factory,
        ),
        audit=lambda: collect_postgres_audit_facts(
            audit_sink_factory=PostgresAuditSink,
            connection_factory=connection_factory,
            tenant_setter=db.set_tenant_local,
            tenant_id=tenant_id,
        ),
        idempotency=lambda: collect_postgres_idempotency_facts(
            store_factory=PostgresIdempotencyStore,
            tenant_id=tenant_id,
            isolation_tenant_id=isolation_tenant_id,
        ),
        redis=lambda: collect_redis_facts(client_factory=redis_client_factory),
        object_store=lambda: collect_filesystem_object_store_facts(
            store_factory=object_store_factory,
            tenant_id=tenant_id,
        ),
        ingestion=lambda: collect_ingestion_readiness_facts(
            store_factory=object_store_factory,
            tenant_id=tenant_id,
            staging_root=resolved_staging_root,
            object_store_root=object_store_root,
            password_manifest_loader=lambda: load_document_password_manifest(env=env),
            no_substantive_content_attestation_loader=lambda: (
                load_document_no_substantive_content_attestation(env=env)
            ),
        ),
        worker=lambda: collect_worker_claim_facts(
            connection_factory=worker_connection_factory,
            boundary_transaction_factory=worker_boundary_transaction_factory,
            tenant_setter=db.set_tenant_local,
            runs_repository_factory=PostgresRunsRepository,
            run_steps_repository_factory=PostgresRunStepsRepository,
            run_step_factory=RunStep,
            worker_lease_fenced_error=WorkerLeaseFencedError,
            audit_sink_factory=PostgresAuditSink,
            lease_audit_emitter_factory=_lease_transition_audit_emitter,
            lease_seconds=_integer(env.get("IDIS_WORKER_LEASE_SECONDS", "0")),
        ),
        budgets=budget_probe,
    )


def run_default_durable_dependency_admission(
    *,
    env: Mapping[str, str],
    expected_role: str,
    worktree_root: Path,
    temp_root: Path,
    expected_public_key_sha256: str,
    admission_scope: str = FULL_DEPENDENCY_ADMISSION_SCOPE,
) -> DependencyAdmissionReport:
    """Run strict admission with production dependency adapters."""
    return run_durable_dependency_admission(
        env=env,
        probes=build_default_dependency_probe_adapters(
            env,
            expected_public_key_sha256=expected_public_key_sha256,
            staging_root=temp_root,
        ),
        expected_role=expected_role,
        worktree_root=worktree_root,
        temp_root=temp_root,
        admission_scope=admission_scope,
    )


__all__ = [
    "AuditProbeFacts",
    "BudgetProbeFacts",
    "DependencyAdmissionReport",
    "DurableDependencyProbeAdapters",
    "ErasureDatabaseTargetFacts",
    "ErasureExecutorProbeFacts",
    "ErasureRoleFacts",
    "FULL_DEPENDENCY_ADMISSION_SCOPE",
    "IdempotencyProbeFacts",
    "IngestionProbeFacts",
    "ObjectStoreProbeFacts",
    "PostgresBudgetLifecycleProbeStore",
    "PostgresProbeFacts",
    "RedisProbeFacts",
    "TASK4_INGESTION_ADMISSION_SCOPE",
    "WorkerProbeFacts",
    "build_default_dependency_probe_adapters",
    "build_postgres_budget_probe_store_factory",
    "collect_filesystem_object_store_facts",
    "collect_erasure_executor_facts",
    "collect_ingestion_readiness_facts",
    "collect_postgres_audit_facts",
    "collect_postgres_facts",
    "collect_postgres_idempotency_facts",
    "collect_provider_budget_facts",
    "collect_redis_facts",
    "collect_worker_claim_facts",
    "run_default_durable_dependency_admission",
    "run_durable_dependency_admission",
]
