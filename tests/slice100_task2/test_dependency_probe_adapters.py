"""Slice100 Task 2 RED tests for operational dependency-probe adapters.

The existing readiness tests validate typed fact interpretation.  These contracts
require adapters to gather those facts through real dependency semantics.  Every
engine, connection, Redis client, and durable store is injected; no socket, live
database, or external provider is touched.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from idis.runtime.dependency_probes import TENANT_RLS_TABLES
from tests.slice100_task2._support import BUDGET_SCHEMA_VERSION, SIGNER_ID, budget_payload

_TENANT = "10000000-0000-4000-8000-000000000001"
_CANARY_TENANT = "20000000-0000-4000-8000-000000000002"
_OWNER_KEY = "slice100-owner-key-secret"
_CANARY_KEY = "slice100-canary-key-secret"
_ADMIN_KEY = "slice100-governance-key-secret"

_BUDGET_COMPONENTS = frozenset(
    {
        "anthropic",
        "openai_embeddings",
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }
)


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.runtime.dependency_probes")
    except ModuleNotFoundError as exc:
        pytest.fail(f"TASK2_API_MISSING: idis.runtime.dependency_probes: {exc}", pytrace=False)


class _Result:
    def __init__(self, *, scalar: object = None, rows: list[object] | None = None) -> None:
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalar(self) -> object:
        return self._scalar

    def scalar_one(self) -> object:
        return self._scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def fetchone(self) -> object | None:
        if self._rows:
            return self._rows[0]
        return self._scalar

    def all(self) -> list[object]:
        return list(self._rows)

    def fetchall(self) -> list[object]:
        return list(self._rows)

    def mappings(self) -> _Result:
        return self

    def one(self) -> object:
        if self._rows:
            return self._rows[0]
        return self._scalar


@dataclass
class _PostgresState:
    revision: str = "0033"
    superuser: bool = False
    bypass_rls: bool = False
    tenant_tables: frozenset[str] = TENANT_RLS_TABLES
    enabled_tables: frozenset[str] = tenant_tables
    forced_tables: frozenset[str] = tenant_tables
    guarded_tables: frozenset[str] = tenant_tables
    extra_bypass_policy_tables: frozenset[str] = frozenset()
    leak_cross_tenant: bool = False
    sentinels: set[tuple[str, str]] = field(default_factory=set)


class _PostgresConnection:
    def __init__(self, state: _PostgresState) -> None:
        self.state = state
        self.current_tenant: str | None = None
        self.operations: list[str] = []

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        assert not isinstance(statement, str), "SQLAlchemy 2 probes require executable statements"
        sql = str(statement).lower()
        params = dict(parameters or {})
        if "alembic_version" in sql:
            self.operations.append("schema_head")
            return _Result(scalar=self.state.revision)
        if "pg_roles" in sql:
            self.operations.append("role_attributes")
            return _Result(
                rows=[
                    {
                        "rolsuper": self.state.superuser,
                        "rolbypassrls": self.state.bypass_rls,
                    }
                ]
            )
        if "pg_class" in sql and "relforcerowsecurity" in sql:
            self.operations.append("force_rls")
            guarded = """
                ((NULLIF(current_setting('idis.tenant_id'::text, true), ''::text) IS NOT NULL)
                AND (tenant_id =
                    (NULLIF(current_setting('idis.tenant_id'::text, true), ''::text))::uuid))
            """
            rows = [
                {
                    "relname": table,
                    "relrowsecurity": table in self.state.enabled_tables,
                    "relforcerowsecurity": table in self.state.forced_tables,
                    "policyname": f"{table}_tenant_isolation",
                    "qual": guarded
                    if table in self.state.guarded_tables
                    else "tenant_id IS NOT NULL",
                    "with_check": (
                        guarded if table in self.state.guarded_tables else "tenant_id IS NOT NULL"
                    ),
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                }
                for table in sorted(self.state.tenant_tables)
            ]
            rows.extend(
                {
                    "relname": table,
                    "relrowsecurity": True,
                    "relforcerowsecurity": True,
                    "policyname": f"{table}_bypass",
                    "qual": "true",
                    "with_check": "true",
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                }
                for table in sorted(self.state.extra_bypass_policy_tables)
            )
            return _Result(rows=rows)
        if "set_config" in sql or "set local" in sql:
            self.operations.append("set_tenant")
            tenant = params.get("tenant_id") or params.get("tenant")
            self.current_tenant = str(tenant)
            return _Result(scalar=self.current_tenant)
        if "current_setting" in sql:
            self.operations.append("tenant_context")
            return _Result(scalar=self.current_tenant)
        if "insert into idempotency_records" in sql:
            self.operations.append("seed_canary")
            self.state.sentinels.add((str(params["tenant_id"]), str(params["idempotency_key"])))
            return _Result()
        if "select tenant_id from idempotency_records" in sql:
            canary_key = (str(params["tenant_id"]), str(params["idempotency_key"]))
            is_owner_probe = "isolation_tenant_id" in params
            self.operations.append("cross_tenant" if is_owner_probe else "verify_canary")
            visible = canary_key in self.state.sentinels and (
                self.current_tenant == canary_key[0]
                or (is_owner_probe and self.state.leak_cross_tenant)
            )
            rows = [{"tenant_id": canary_key[0]}] if visible else []
            return _Result(rows=rows)
        if "delete from idempotency_records" in sql:
            self.operations.append("cleanup_canary")
            self.state.sentinels.discard((str(params["tenant_id"]), str(params["idempotency_key"])))
            return _Result()
        if "select 1" in sql:
            self.operations.append("connect")
            return _Result(scalar=1)
        raise AssertionError(f"unexpected hermetic PostgreSQL probe query: {sql}")


class _ConnectionContext(AbstractContextManager[_PostgresConnection]):
    def __init__(self, connection: _PostgresConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _PostgresConnection:
        return self.connection

    def __exit__(self, *args: object) -> None:
        return None


class _PostgresConnectionFactory:
    def __init__(self, state: _PostgresState) -> None:
        self.state = state
        self.connections: list[_PostgresConnection] = []

    def __call__(self) -> _ConnectionContext:
        connection = _PostgresConnection(self.state)
        self.connections.append(connection)
        return _ConnectionContext(connection)


@pytest.mark.parametrize(
    ("mutation", "failed_field"),
    [
        (lambda state: setattr(state, "revision", "0032"), "schema_at_head"),
        (lambda state: setattr(state, "superuser", True), "app_role_superuser"),
        (lambda state: setattr(state, "bypass_rls", True), "app_role_bypass_rls"),
        (
            lambda state: setattr(
                state,
                "forced_tables",
                state.forced_tables - {"idempotency_records"},
            ),
            "forced_rls",
        ),
        (lambda state: setattr(state, "leak_cross_tenant", True), "cross_tenant_denied"),
    ],
)
def test_postgres_adapter_collects_schema_role_force_rls_and_isolation_facts(
    mutation: Callable[[_PostgresState], None],
    failed_field: str,
) -> None:
    module = _module()
    state = _PostgresState()
    mutation(state)
    factory = _PostgresConnectionFactory(state)

    facts = module.collect_postgres_facts(
        connection_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
        expected_schema_revision="0033",
        required_rls_tables=state.tenant_tables,
    )

    assert facts.connected is True
    if failed_field in {"app_role_superuser", "app_role_bypass_rls"}:
        assert getattr(facts, failed_field) is True
    else:
        assert getattr(facts, failed_field) is False
    operations = {
        operation for connection in factory.connections for operation in connection.operations
    }
    assert operations == {
        "connect",
        "schema_head",
        "role_attributes",
        "force_rls",
        "set_tenant",
        "tenant_context",
        "seed_canary",
        "verify_canary",
        "cross_tenant",
        "cleanup_canary",
    }
    assert state.sentinels == set()


def test_postgres_adapter_returns_all_true_only_after_every_required_query() -> None:
    module = _module()
    factory = _PostgresConnectionFactory(_PostgresState())

    facts = module.collect_postgres_facts(
        connection_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
        expected_schema_revision="0033",
        required_rls_tables=factory.state.tenant_tables,
    )

    assert facts == module.PostgresProbeFacts(
        connected=True,
        schema_at_head=True,
        app_role_superuser=False,
        app_role_bypass_rls=False,
        forced_rls=True,
        tenant_context_ok=True,
        cross_tenant_denied=True,
    )


@pytest.mark.parametrize(
    "defect", ["rls_disabled", "policy_not_null_guarded", "extra_permissive_bypass"]
)
def test_postgres_adapter_censuses_every_public_tenant_table_not_only_minimum_set(
    defect: str,
) -> None:
    module = _module()
    state = _PostgresState()
    if defect == "rls_disabled":
        state.enabled_tables = state.enabled_tables - {"run_steps"}
    elif defect == "extra_permissive_bypass":
        state.extra_bypass_policy_tables = frozenset({"run_steps"})
    else:
        state.guarded_tables = state.guarded_tables - {"run_steps"}
    factory = _PostgresConnectionFactory(state)

    facts = module.collect_postgres_facts(
        connection_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
        expected_schema_revision="0033",
        required_rls_tables=state.tenant_tables,
    )

    assert facts.forced_rls is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qual", "{canonical} OR true"),
        ("with_check", "{canonical} AND false"),
        ("permissive", "RESTRICTIVE"),
        ("roles", ["idis_app"]),
        ("cmd", "SELECT"),
    ],
)
def test_postgres_rls_catalog_rejects_noncanonical_single_policy_variants(
    field: str,
    value: object,
) -> None:
    module = _module()
    canonical = """
        ((NULLIF(current_setting('idis.tenant_id'::text, true), ''::text) IS NOT NULL)
        AND (tenant_id =
            (NULLIF(current_setting('idis.tenant_id'::text, true), ''::text))::uuid))
    """
    row: dict[str, object] = {
        "relname": "runs",
        "relrowsecurity": True,
        "relforcerowsecurity": True,
        "policyname": "tenant_isolation_runs",
        "qual": canonical,
        "with_check": canonical,
        "permissive": "PERMISSIVE",
        "roles": ["public"],
        "cmd": "ALL",
    }
    row[field] = value.format(canonical=canonical) if isinstance(value, str) else value

    assert (
        module._all_tenant_tables_have_canonical_rls(
            [row],
            required_tables=frozenset({"runs"}),
        )
        is False
    )


def test_postgres_rls_catalog_preserves_quoted_identifier_semantics() -> None:
    module = _module()
    canonical = """
        ((NULLIF(current_setting('idis.tenant_id'::text, true), ''::text) IS NOT NULL)
        AND (tenant_id =
            (NULLIF(current_setting('idis.tenant_id'::text, true), ''::text))::uuid))
    """
    quoted_other_column = canonical.replace("tenant_id =", '"TENANT_ID" =')

    assert (
        module._all_tenant_tables_have_canonical_rls(
            [
                {
                    "relname": "deals",
                    "relrowsecurity": True,
                    "relforcerowsecurity": True,
                    "policyname": "tenant_isolation_deals",
                    "qual": quoted_other_column,
                    "with_check": quoted_other_column,
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                }
            ],
            required_tables=frozenset({"deals"}),
        )
        is False
    )


def test_default_postgres_adapter_derives_the_current_alembic_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "_expected_alembic_head", lambda: "0099", raising=False)

    def collect(**values: object) -> object:
        observed.update(values)
        return module.PostgresProbeFacts(True, True, False, False, True, True, True)

    monkeypatch.setattr(module, "collect_postgres_facts", collect)
    adapters = module.build_default_dependency_probe_adapters(
        {
            "IDIS_ACCEPTANCE_TENANT_ID": _TENANT,
            "IDIS_ISOLATION_CANARY_TENANT_ID": _CANARY_TENANT,
        },
        expected_public_key_sha256="a" * 64,
    )

    adapters.postgres()

    assert observed["expected_schema_revision"] == "0099"


class _AuditSink:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows

    def emit(self, event: dict[str, object]) -> None:
        assert event["tenant_id"] == _TENANT
        self.rows[str(event["event_id"])] = dict(event)


class _ManglingAuditSink(_AuditSink):
    def emit(self, event: dict[str, object]) -> None:
        super().emit(event)
        self.rows[str(event["event_id"])]["summary"] = "mangled after insert"


class _AuditReadConnection:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.tenant_id: str | None = None

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        assert "audit_events" in str(statement).lower()
        params = dict(parameters or {})
        event = self.rows.get(str(params.get("event_id")))
        if event is None or event.get("tenant_id") != self.tenant_id:
            return _Result(rows=[])
        request = event.get("request", {})
        return _Result(
            rows=[
                {
                    "event_id": event.get("event_id"),
                    "tenant_id": event.get("tenant_id"),
                    "occurred_at": event.get("occurred_at"),
                    "event_type": event.get("event_type"),
                    "request_id": request.get("request_id")
                    if isinstance(request, Mapping)
                    else None,
                    "idempotency_key": request.get("idempotency_key_sha256")
                    if isinstance(request, Mapping)
                    else None,
                    "event": event,
                }
            ]
        )


class _AuditConnectionContext(AbstractContextManager[_AuditReadConnection]):
    def __init__(self, connection: _AuditReadConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _AuditReadConnection:
        return self.connection

    def __exit__(self, *args: object) -> None:
        return None


class _AuditConnectionFactory:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.connections: list[_AuditReadConnection] = []

    def __call__(self) -> _AuditConnectionContext:
        connection = _AuditReadConnection(self.rows)
        self.connections.append(connection)
        return _AuditConnectionContext(connection)


def test_audit_adapter_emits_tenant_event_and_reads_it_on_a_new_connection() -> None:
    module = _module()
    rows: dict[str, dict[str, object]] = {}
    connections = _AuditConnectionFactory(rows)
    tenant_sets: list[str] = []

    def set_tenant(connection: _AuditReadConnection, tenant_id: str) -> None:
        connection.tenant_id = tenant_id
        tenant_sets.append(tenant_id)

    facts = module.collect_postgres_audit_facts(
        audit_sink_factory=lambda: _AuditSink(rows),
        connection_factory=connections,
        tenant_setter=set_tenant,
        tenant_id=_TENANT,
    )

    assert facts == module.AuditProbeFacts(
        backend="postgres",
        emit_ok=True,
        read_ok=True,
        restart_read_ok=True,
    )
    assert len(rows) == 1
    from idis.validators.audit_event_validator import validate_audit_event

    event = next(iter(rows.values()))
    validation = validate_audit_event(event)
    assert validation.passed, [error.code for error in validation.errors]
    assert len(connections.connections) >= 2
    assert tenant_sets == [_TENANT] * len(connections.connections)


def test_audit_adapter_rejects_mangled_durable_event_content() -> None:
    module = _module()
    rows: dict[str, dict[str, object]] = {}
    connections = _AuditConnectionFactory(rows)

    facts = module.collect_postgres_audit_facts(
        audit_sink_factory=lambda: _ManglingAuditSink(rows),
        connection_factory=connections,
        tenant_setter=lambda connection, tenant_id: setattr(connection, "tenant_id", tenant_id),
        tenant_id=_TENANT,
    )

    assert facts.emit_ok is True
    assert facts.read_ok is False
    assert facts.restart_read_ok is False


class _IdempotencyStore:
    def __init__(
        self,
        rows: dict[tuple[str, ...], object],
        reads: list[tuple[str, ...]],
        conflict_attempts: list[tuple[str, ...]],
        *,
        overwrite_conflicts: bool,
        silent_conflicts: bool,
    ) -> None:
        self.rows = rows
        self.reads = reads
        self.conflict_attempts = conflict_attempts
        self.overwrite_conflicts = overwrite_conflicts
        self.silent_conflicts = silent_conflicts

    @staticmethod
    def _key(scope: object) -> tuple[str, ...]:
        return (
            str(scope.tenant_id),
            str(scope.actor_id),
            str(scope.method),
            str(scope.operation_id),
            str(scope.idempotency_key),
        )

    def put(self, scope: object, record: object) -> None:
        key = self._key(scope)
        existing = self.rows.get(key)
        if existing is not None and getattr(existing, "payload_sha256", None) != getattr(
            record, "payload_sha256", None
        ):
            from idis.idempotency.store import IdempotencyKeyConflictError

            self.conflict_attempts.append(key)
            if not self.overwrite_conflicts:
                if self.silent_conflicts:
                    return
                raise IdempotencyKeyConflictError()
        self.rows[key] = record

    def get(self, scope: object) -> object | None:
        key = self._key(scope)
        self.reads.append(key)
        return self.rows.get(key)


class _IdempotencyStoreFactory:
    def __init__(
        self,
        *,
        overwrite_conflicts: bool = False,
        silent_conflicts: bool = False,
    ) -> None:
        self.rows: dict[tuple[str, ...], object] = {}
        self.reads: list[tuple[str, ...]] = []
        self.conflict_attempts: list[tuple[str, ...]] = []
        self.instances: list[_IdempotencyStore] = []
        self.overwrite_conflicts = overwrite_conflicts
        self.silent_conflicts = silent_conflicts

    def __call__(self) -> _IdempotencyStore:
        store = _IdempotencyStore(
            self.rows,
            self.reads,
            self.conflict_attempts,
            overwrite_conflicts=self.overwrite_conflicts,
            silent_conflicts=self.silent_conflicts,
        )
        self.instances.append(store)
        return store


def test_idempotency_adapter_proves_put_replay_conflict_restart_and_isolation() -> None:
    module = _module()
    factory = _IdempotencyStoreFactory()

    facts = module.collect_postgres_idempotency_facts(
        store_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
    )

    assert facts == module.IdempotencyProbeFacts(
        backend="postgres",
        put_ok=True,
        replay_ok=True,
        conflict_ok=True,
        restart_replay_ok=True,
        tenant_isolation_ok=True,
    )
    assert len(factory.instances) >= 2
    assert len(factory.conflict_attempts) == 1
    assert any(key[0] == _CANARY_TENANT for key in factory.reads)
    assert len(factory.rows) == 1


def test_idempotency_adapter_rejects_last_writer_wins_as_conflict_proof() -> None:
    module = _module()
    factory = _IdempotencyStoreFactory(overwrite_conflicts=True)

    facts = module.collect_postgres_idempotency_facts(
        store_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
    )

    assert len(factory.conflict_attempts) == 1
    assert facts.conflict_ok is False


def test_idempotency_adapter_rejects_silent_first_writer_wins_as_conflict_proof() -> None:
    module = _module()
    factory = _IdempotencyStoreFactory(silent_conflicts=True)

    facts = module.collect_postgres_idempotency_facts(
        store_factory=factory,
        tenant_id=_TENANT,
        isolation_tenant_id=_CANARY_TENANT,
    )

    assert len(factory.conflict_attempts) == 1
    assert facts.conflict_ok is False


class _RedisClient:
    def __init__(self, buckets: dict[str, int], *, ping_ok: bool = True) -> None:
        self.buckets = buckets
        self.ping_ok = ping_ok
        self.closed = False

    def ping(self) -> bool:
        return self.ping_ok

    def eval(self, _script: str, _key_count: int, key: str, *args: object) -> list[int]:
        capacity = int(args[0])
        cost = int(args[2])
        remaining = self.buckets.get(key, capacity)
        allowed = remaining >= cost
        if allowed:
            remaining -= cost
            self.buckets[key] = remaining
        return [int(allowed), remaining, 0 if allowed else 1]

    def close(self) -> None:
        self.closed = True


class _RedisClientFactory:
    def __init__(self, *, shared: bool) -> None:
        self.shared = shared
        self._shared_buckets: dict[str, int] = {}
        self.clients: list[_RedisClient] = []

    def __call__(self) -> _RedisClient:
        buckets = self._shared_buckets if self.shared else {}
        client = _RedisClient(buckets)
        self.clients.append(client)
        return client


@pytest.mark.parametrize(("shared", "cross_process_ok"), [(True, True), (False, False)])
def test_redis_adapter_pings_and_proves_one_atomic_bucket_across_two_clients(
    shared: bool,
    cross_process_ok: bool,
) -> None:
    module = _module()
    factory = _RedisClientFactory(shared=shared)

    facts = module.collect_redis_facts(client_factory=factory)

    assert facts.backend == "redis"
    assert facts.ping_ok is True
    assert facts.atomic_bucket_ok is True
    assert facts.cross_process_ok is cross_process_ok
    assert len(factory.clients) == 2
    assert all(client.closed for client in factory.clients)


def test_default_redis_probe_adapter_pins_resp2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    shared_buckets: dict[str, int] = {}
    observed: list[dict[str, object]] = []

    class _Redis:
        @staticmethod
        def from_url(url: str, **kwargs: object) -> _RedisClient:
            observed.append({"url": url, **kwargs})
            return _RedisClient(shared_buckets)

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_Redis))
    adapters = module.build_default_dependency_probe_adapters(
        _strict_env(tmp_path),
        expected_public_key_sha256="0" * 64,
        staging_root=tmp_path / "staging",
    )

    facts = adapters.redis()

    assert facts == module.RedisProbeFacts(
        backend="redis",
        ping_ok=True,
        atomic_bucket_ok=True,
        cross_process_ok=True,
    )
    assert observed == [
        {"url": "redis://redis.internal:6379/0", "protocol": 2},
        {"url": "redis://redis.internal:6379/0", "protocol": 2},
    ]


def test_filesystem_adapter_proves_hash_delete_and_new_instance_read(tmp_path: Path) -> None:
    module = _module()
    from idis.storage.filesystem_store import FilesystemObjectStore

    root = tmp_path / "persistent-objects"

    facts = module.collect_filesystem_object_store_facts(
        store_factory=lambda: FilesystemObjectStore(root),
        tenant_id=_TENANT,
    )

    assert facts == module.ObjectStoreProbeFacts(
        backend="filesystem",
        write_ok=True,
        read_ok=True,
        hash_ok=True,
        delete_ok=True,
        restart_read_ok=True,
    )
    assert not list(root.rglob("*.data"))


def test_filesystem_adapter_does_not_treat_arbitrary_post_delete_error_as_not_found(
    tmp_path: Path,
) -> None:
    module = _module()
    from idis.storage.filesystem_store import FilesystemObjectStore

    root = tmp_path / "persistent-objects"
    deleted = {"value": False}

    class TransientAfterDeleteStore(FilesystemObjectStore):
        def delete(self, tenant_id: str, key: str, version_id: str | None = None) -> None:
            super().delete(tenant_id, key, version_id=version_id)
            deleted["value"] = True

        def get(self, tenant_id: str, key: str, version_id: str | None = None) -> object:
            if deleted["value"]:
                raise RuntimeError("transient backend failure")
            return super().get(tenant_id, key, version_id=version_id)

    facts = module.collect_filesystem_object_store_facts(
        store_factory=lambda: TransientAfterDeleteStore(root),
        tenant_id=_TENANT,
    )

    assert facts.delete_ok is False


def _signed_budget_policy(*, signature_verified: bool = True) -> SimpleNamespace:
    components: dict[str, dict[str, object]] = {
        component: {"max_calls": 4, "max_usd": "1.00"} for component in _BUDGET_COMPONENTS
    }
    components["anthropic"].update({"max_input_tokens": 100_000, "max_output_tokens": 25_000})
    components["openai_embeddings"].update({"max_embedding_tokens": 100_000})
    return SimpleNamespace(
        tenant_id=_TENANT,
        policy_version="slice100-acceptance-v1",
        policy_hash="a" * 64,
        envelope_hash="b" * 64,
        signer_id="slice100-governance-v1",
        signature_verified=signature_verified,
        currency="USD",
        components=components,
    )


_BUDGET_FIELDS = (
    "calls",
    "usd",
    "input_tokens",
    "output_tokens",
    "embedding_tokens",
)


class _BudgetStore:
    is_durable = True

    def __init__(
        self,
        state: dict[str, Any],
        reservations: dict[str, dict[str, Any]],
        operations: list[str],
        *,
        persist_usd: bool,
    ) -> None:
        self.state = state
        self.reservations = reservations
        self.operations = operations
        self.persist_usd = persist_usd

    @staticmethod
    def _reservation(values: Mapping[str, object]) -> str:
        return str(values.get("reservation_id"))

    def reserve(self, **values: object) -> bool:
        reservation_id = self._reservation(values)
        reservation = {field: values[field] for field in _BUDGET_FIELDS}
        if not self.persist_usd:
            reservation["usd"] = Decimal("0")
        self.reservations[reservation_id] = reservation
        for dimension in _BUDGET_FIELDS:
            self.state[f"reserved_{dimension}"] += reservation[dimension]
        self.state["policy_hash"] = values["policy_hash"]
        self.operations.append("reserve")
        return True

    def consume(self, **values: object) -> bool:
        reservation = self.reservations.pop(self._reservation(values))
        for dimension in _BUDGET_FIELDS:
            self.state[f"reserved_{dimension}"] -= reservation[dimension]
            self.state[f"consumed_{dimension}"] += reservation[dimension]
        self.operations.append("consume")
        return True

    def refund(self, **values: object) -> bool:
        reservation = self.reservations.pop(self._reservation(values))
        for dimension in _BUDGET_FIELDS:
            self.state[f"reserved_{dimension}"] -= reservation[dimension]
        self.operations.append("refund")
        return True

    def snapshot(self, **_values: object) -> dict[str, object]:
        self.operations.append("snapshot")
        return dict(self.state)


class _BudgetStoreFactory:
    def __init__(self, *, persist_usd: bool = True) -> None:
        self.state: dict[str, Any] = {
            "policy_hash": None,
            **{
                f"reserved_{field}": Decimal("0") if field == "usd" else 0
                for field in _BUDGET_FIELDS
            },
            **{
                f"consumed_{field}": Decimal("0") if field == "usd" else 0
                for field in _BUDGET_FIELDS
            },
        }
        self.reservations: dict[str, dict[str, Any]] = {}
        self.operations: list[str] = []
        self.instances: list[_BudgetStore] = []
        self.persist_usd = persist_usd

    def __call__(self) -> _BudgetStore:
        store = _BudgetStore(
            self.state,
            self.reservations,
            self.operations,
            persist_usd=self.persist_usd,
        )
        self.instances.append(store)
        return store


def test_budget_adapter_requires_complete_signed_set_and_proves_full_lifecycle() -> None:
    module = _module()
    stores = _BudgetStoreFactory()

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda tenant_id: _signed_budget_policy() if tenant_id == _TENANT else None,
        store_factory=stores,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts == module.BudgetProbeFacts(
        backend="postgres",
        policy_loaded=True,
        policy_currency="USD",
        configured_provider_ids=_BUDGET_COMPONENTS,
        token_caps_present=True,
        call_reservation_ok=True,
        usd_reservation_ok=True,
        consume_ok=True,
        refund_ok=True,
        restart_read_ok=True,
    )
    assert len(stores.instances) >= 2
    assert stores.operations.count("reserve") == 2
    assert {"consume", "refund", "snapshot"} <= set(stores.operations)


def test_budget_adapter_proves_usd_persistence_independently_from_call_reservation() -> None:
    module = _module()
    stores = _BudgetStoreFactory(persist_usd=False)

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda _tenant_id: _signed_budget_policy(),
        store_factory=stores,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts.call_reservation_ok is True
    assert facts.usd_reservation_ok is False


class _BudgetSqlTransaction:
    def __enter__(self) -> _BudgetSqlTransaction:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _BudgetSqlConnection:
    def __init__(self, engine: _BudgetSqlEngine) -> None:
        self.engine = engine
        self.closed = False

    def __enter__(self) -> _BudgetSqlConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def begin(self) -> _BudgetSqlTransaction:
        return _BudgetSqlTransaction()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        assert not isinstance(statement, str), "SQLAlchemy 2 requires executable statements"
        sql = " ".join(str(statement).lower().split())
        params = dict(parameters or {})
        self.engine.statements.append(sql)
        self.engine.parameters.append(params)
        if (
            "pg_advisory_lock" in sql
            or "pg_advisory_xact_lock" in sql
            or "pg_advisory_unlock" in sql
        ):
            return _Result(scalar=True)
        if sql.startswith("with active_policy as"):
            return _Result(
                rows=[
                    {
                        "policy_hash": params["policy_hash"],
                        "currency": "USD",
                        "total_max_usd": Decimal("100.00"),
                        "max_calls": 4,
                        "max_usd": Decimal("1.00"),
                        "max_input_tokens": 100_000,
                        "max_output_tokens": 25_000,
                        "max_embedding_tokens": None,
                    }
                ]
            )
        if sql.startswith("select event_type, event from audit_events"):
            reservation_id = params.get("reservation_id")
            component_id = params.get("component_id")
            rows = []
            for event in self.engine.events:
                safe = event["payload"]["safe"]
                if reservation_id is not None and safe["reservation_id"] != reservation_id:
                    continue
                if component_id is not None and safe["component_id"] != component_id:
                    continue
                rows.append({"event_type": event["event_type"], "event": event})
            return _Result(rows=rows)
        if sql.startswith("insert into audit_events"):
            raw_event = params["event"]
            assert isinstance(raw_event, str)
            self.engine.events.append(json.loads(raw_event))
            return _Result()
        if sql.startswith("select provider, used"):
            rows = []
            if self.engine.row is not None:
                rows.append({"provider": "anthropic", **self.engine.row})
            return _Result(rows=rows)
        if sql.startswith("select used, policy_hash"):
            row = self.engine.row
            if row is None or (
                "and policy_hash = :policy_hash" in sql
                and row["policy_hash"] != params["policy_hash"]
            ):
                return _Result(rows=[])
            return _Result(rows=[dict(row)])
        if sql.startswith("insert into provider_budget_usage"):
            row = self.engine.row
            if row is None:
                row = _budget_sql_empty_row(policy_hash=str(params["policy_hash"]))
                self.engine.row = row
            row["policy_hash"] = params["policy_hash"]
            row["reserved_calls"] += params["calls"]
            row["reserved_usd"] += params["usd"]
            row["reserved_input_tokens"] += params["input_tokens"]
            row["reserved_output_tokens"] += params["output_tokens"]
            row["reserved_embedding_tokens"] += params["embedding_tokens"]
            return _Result(rows=[{"reserved_calls": row["reserved_calls"]}])
        if sql.startswith("update provider_budget_usage set used = used +"):
            row = self.engine.require_row()
            row["used"] += params["calls"]
            row["reserved_calls"] -= params["calls"]
            row["reserved_usd"] -= params["usd"]
            row["consumed_usd"] += params["usd"]
            for dimension in ("input", "output", "embedding"):
                key = f"{dimension}_tokens"
                row[f"reserved_{key}"] -= params[key]
                row[f"consumed_{key}"] += params[key]
            return _Result(rows=[{"used": row["used"]}])
        if sql.startswith("update provider_budget_usage set reserved_calls = reserved_calls -"):
            row = self.engine.require_row()
            row["reserved_calls"] -= params["calls"]
            row["reserved_usd"] -= params["usd"]
            for dimension in ("input", "output", "embedding"):
                key = f"{dimension}_tokens"
                row[f"reserved_{key}"] -= params[key]
            return _Result(rows=[{"reserved_calls": row["reserved_calls"]}])
        raise AssertionError(f"unexpected budget lifecycle SQL: {sql}")


class _BudgetSqlEngine:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = None if row is None else dict(row)
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []
        self.connections: list[_BudgetSqlConnection] = []
        self.events: list[dict[str, Any]] = []

    def connect(self) -> _BudgetSqlConnection:
        connection = _BudgetSqlConnection(self)
        self.connections.append(connection)
        return connection

    def require_row(self) -> dict[str, Any]:
        assert self.row is not None
        return self.row


def _budget_sql_empty_row(*, policy_hash: str | None) -> dict[str, Any]:
    return {
        "used": 0,
        "policy_hash": policy_hash,
        "reserved_calls": 0,
        "reserved_usd": Decimal("0"),
        "consumed_usd": Decimal("0"),
        "reserved_input_tokens": 0,
        "consumed_input_tokens": 0,
        "reserved_output_tokens": 0,
        "consumed_output_tokens": 0,
        "reserved_embedding_tokens": 0,
        "consumed_embedding_tokens": 0,
        "updated_at": "2026-07-15T00:00:00Z",
    }


def test_default_postgres_budget_store_retains_one_exact_ledger_backed_debit() -> None:
    module = _module()
    engine = _BudgetSqlEngine(None)
    tenant_sets: list[str] = []
    factory = module.build_postgres_budget_probe_store_factory(
        engine=engine,
        tenant_setter=lambda _connection, tenant_id: tenant_sets.append(tenant_id),
        global_max_calls=500,
    )

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda _tenant_id: _signed_budget_policy(),
        store_factory=factory,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts.call_reservation_ok is True
    assert facts.usd_reservation_ok is True
    assert facts.consume_ok is True
    assert facts.refund_ok is True
    assert facts.restart_read_ok is True
    assert engine.row is not None
    assert engine.row["used"] == 1
    assert engine.row["reserved_calls"] == 0
    assert engine.row["consumed_usd"] == Decimal("0.01")
    assert engine.row["consumed_input_tokens"] == 1
    assert engine.row["consumed_output_tokens"] == 1
    assert engine.row["consumed_embedding_tokens"] == 0
    assert len(engine.events) == 4
    assert len(engine.connections) >= 2
    assert tenant_sets
    assert any("pg_advisory_xact_lock" in sql for sql in engine.statements)
    assert any(sql.startswith("with active_policy as") for sql in engine.statements)
    assert not any(sql.startswith("delete from provider_budget_usage") for sql in engine.statements)
    assert not any(
        sql.startswith("update provider_budget_usage set used = :used") for sql in engine.statements
    )
    assert any(parameters.get("global_max_calls") == 500 for parameters in engine.parameters)
    lock_keys = {
        str(parameters["lock_key"]) for parameters in engine.parameters if "lock_key" in parameters
    }
    assert lock_keys == {f"idis:provider-budget:{_TENANT}"}


def test_budget_readiness_cap_change_preserves_old_evidence_and_uses_a_new_scope() -> None:
    module = _module()
    engine = _BudgetSqlEngine(None)

    def run(cap: int) -> object:
        return module.collect_provider_budget_facts(
            policy_loader=lambda _tenant_id: _signed_budget_policy(),
            store_factory=module.build_postgres_budget_probe_store_factory(
                engine=engine,
                tenant_setter=lambda _connection, _tenant_id: None,
                global_max_calls=cap,
            ),
            tenant_id=_TENANT,
            global_max_calls=cap,
        )

    first = run(500)
    old_events = json.dumps(engine.events, sort_keys=True)
    old_ids = {event["payload"]["safe"]["reservation_id"] for event in engine.events}

    changed = run(600)
    changed_event_count = len(engine.events)
    changed_used = engine.require_row()["used"]
    current_ids = {
        event["payload"]["safe"]["reservation_id"]
        for event in engine.events
        if event["payload"]["safe"]["global_max_calls"] == 600
    }
    repeated = run(600)

    assert first.restart_read_ok is True
    assert changed.restart_read_ok is True
    assert repeated == changed
    assert json.dumps(engine.events[:4], sort_keys=True) == old_events
    assert old_ids.isdisjoint(current_ids)
    assert len(old_ids) == len(current_ids) == 2
    assert changed_event_count == len(engine.events) == 8
    assert changed_used == engine.require_row()["used"] == 2


def test_default_postgres_budget_store_rejects_unbound_legacy_usage() -> None:
    module = _module()
    original = _budget_sql_empty_row(policy_hash=None)
    original["used"] = 7
    engine = _BudgetSqlEngine(original)
    factory = module.build_postgres_budget_probe_store_factory(
        engine=engine,
        tenant_setter=lambda _connection, _tenant_id: None,
        global_max_calls=500,
    )

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda _tenant_id: _signed_budget_policy(),
        store_factory=factory,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts.call_reservation_ok is False
    assert engine.row == original
    assert engine.row is not None and engine.row["used"] == 7
    assert any("select event_type, event from audit_events" in sql for sql in engine.statements)


def test_default_postgres_budget_store_rejects_unledgered_aggregate_without_rewriting() -> None:
    module = _module()
    original = _budget_sql_empty_row(policy_hash="c" * 64)
    original["consumed_usd"] = Decimal("0.25")
    engine = _BudgetSqlEngine(original)
    factory = module.build_postgres_budget_probe_store_factory(
        engine=engine,
        tenant_setter=lambda _connection, _tenant_id: None,
        global_max_calls=500,
    )

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda _tenant_id: _signed_budget_policy(),
        store_factory=factory,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts.restart_read_ok is False
    assert engine.row == original
    assert not any(sql.startswith("delete from provider_budget_usage") for sql in engine.statements)
    assert not any(
        sql.startswith("update provider_budget_usage set used = :used") for sql in engine.statements
    )


class _PolicyReadConnection:
    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        audit_exists: bool,
        usage_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.rows = rows
        self.audit_exists = audit_exists
        self.usage_rows = list(usage_rows or [])
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []

    def __enter__(self) -> _PolicyReadConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        assert not isinstance(statement, str)
        sql = " ".join(str(statement).lower().split())
        self.statements.append(sql)
        self.parameters.append(dict(parameters or {}))
        if "from provider_budget_policies" in sql:
            return _Result(rows=self.rows)
        if "from audit_events" in sql:
            return _Result(scalar=self.audit_exists)
        if "from provider_budget_usage" in sql:
            active_hash = str((parameters or {})["active_policy_hash"])
            valid = all(
                usage["policy_hash"] is not None
                and not (
                    any(
                        usage[field] != 0
                        for field in (
                            "reserved_calls",
                            "reserved_usd",
                            "reserved_input_tokens",
                            "reserved_output_tokens",
                            "reserved_embedding_tokens",
                        )
                    )
                    and usage["policy_hash"] != active_hash
                )
                for usage in self.usage_rows
            )
            return _Result(scalar=valid)
        raise AssertionError(f"unexpected policy-read SQL: {sql}")


def _verified_policy_rows(tmp_path: Path) -> tuple[dict[str, str], list[dict[str, object]]]:
    from idis.runtime.governance_envelopes import sign_envelope, verify_envelope
    from idis.tools.governance_signing import generate_ed25519_keypair

    key_dir = tmp_path / "operator-keys"
    key_dir.mkdir()
    private_path = key_dir / "governance-private.pem"
    public_path = key_dir / "governance-public.pem"
    generate_ed25519_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )
    private_pem = private_path.read_bytes()
    public_pem = public_path.read_bytes()
    payload = budget_payload()
    envelope = sign_envelope(
        schema_version=BUDGET_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_pem,
    )
    verified = verify_envelope(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
    )
    envelope_path = tmp_path / "budget-envelope.json"
    envelope_path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    audit_event_id = "30000000-0000-4000-8000-000000000003"
    raw_components = payload["components"]
    assert isinstance(raw_components, Mapping)
    rows: list[dict[str, object]] = []
    for component_id, raw_component in sorted(raw_components.items()):
        assert isinstance(raw_component, Mapping)
        rows.append(
            {
                "policy_hash": verified.payload_sha256,
                "component_id": component_id,
                "policy_version": payload["policy_version"],
                "currency": "USD",
                "total_max_usd": Decimal(str(payload["total_max_usd"])),
                "max_calls": raw_component["max_calls"],
                "max_usd": Decimal(str(raw_component["max_usd"])),
                "max_input_tokens": raw_component.get("max_input_tokens"),
                "max_output_tokens": raw_component.get("max_output_tokens"),
                "max_embedding_tokens": raw_component.get("max_embedding_tokens"),
                "signer_id": verified.signer_id,
                "imported_by_actor_id": "slice100-governance-admin",
                "envelope_hash": verified.envelope_sha256,
                "payload_hash": verified.payload_sha256,
                "public_key_hash": verified.public_key_sha256,
                "import_audit_event_id": audit_event_id,
                "imported_at": "2026-07-15T00:00:00+00:00",
            }
        )
    env = {
        "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(envelope_path.resolve()),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": SIGNER_ID,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_path.resolve()),
    }
    return env, rows


def test_durable_budget_policy_loader_reverifies_envelope_and_exact_persisted_headers(
    tmp_path: Path,
) -> None:
    module = _module()
    env, rows = _verified_policy_rows(tmp_path)
    connection = _PolicyReadConnection(rows, audit_exists=True)

    policy = module._load_durable_budget_policy(
        connection_factory=lambda: connection,
        tenant_setter=lambda _connection, _tenant_id: None,
        tenant_id=_TENANT,
        env=env,
        expected_public_key_sha256=str(rows[0]["public_key_hash"]),
    )

    assert policy is not None
    assert policy.signature_verified is True
    assert policy.policy_hash == rows[0]["policy_hash"]
    assert frozenset(policy.components) == _BUDGET_COMPONENTS
    assert all("revoked_at" not in sql for sql in connection.statements)
    assert any("latest_complete" in sql for sql in connection.statements)
    assert any("integration.governance.imported" in sql for sql in connection.statements)


@pytest.mark.parametrize("defect", ["header", "cap", "audit"])
def test_durable_budget_policy_loader_fails_closed_on_persistence_or_audit_mismatch(
    tmp_path: Path,
    defect: str,
) -> None:
    module = _module()
    env, rows = _verified_policy_rows(tmp_path)
    audit_exists = defect != "audit"
    if defect == "header":
        rows[0]["total_max_usd"] = Decimal("101.00")
    elif defect == "cap":
        rows[0]["max_calls"] = int(rows[0]["max_calls"]) + 1
    connection = _PolicyReadConnection(rows, audit_exists=audit_exists)

    policy = module._load_durable_budget_policy(
        connection_factory=lambda: connection,
        tenant_setter=lambda _connection, _tenant_id: None,
        tenant_id=_TENANT,
        env=env,
        expected_public_key_sha256=str(rows[0]["public_key_hash"]),
    )

    assert policy is None


@pytest.mark.parametrize("violation", ["null-policy-hash", "inactive-policy-reservation"])
def test_durable_budget_policy_loader_rejects_unbound_or_inactive_reserved_usage(
    tmp_path: Path,
    violation: str,
) -> None:
    module = _module()
    env, rows = _verified_policy_rows(tmp_path)
    usage = {
        "policy_hash": None if violation == "null-policy-hash" else "f" * 64,
        "reserved_calls": 0 if violation == "null-policy-hash" else 1,
        "reserved_usd": Decimal("0"),
        "reserved_input_tokens": 0,
        "reserved_output_tokens": 0,
        "reserved_embedding_tokens": 0,
    }
    connection = _PolicyReadConnection(
        rows,
        audit_exists=True,
        usage_rows=[usage],
    )

    policy = module._load_durable_budget_policy(
        connection_factory=lambda: connection,
        tenant_setter=lambda _connection, _tenant_id: None,
        tenant_id=_TENANT,
        env=env,
        expected_public_key_sha256=str(rows[0]["public_key_hash"]),
    )

    assert policy is None, violation
    usage_queries = [sql for sql in connection.statements if "provider_budget_usage" in sql]
    assert len(usage_queries) == 1
    query = usage_queries[0]
    assert "policy_hash is null" in query
    assert "policy_hash <> :active_policy_hash" in query
    for reservation_field in (
        "reserved_calls",
        "reserved_usd",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_embedding_tokens",
    ):
        assert f"{reservation_field} <> 0" in query
    query_index = connection.statements.index(query)
    assert connection.parameters[query_index]["active_policy_hash"] == rows[0]["policy_hash"]


def test_durable_budget_policy_loader_allows_zero_reservation_on_historical_policy(
    tmp_path: Path,
) -> None:
    module = _module()
    env, rows = _verified_policy_rows(tmp_path)
    usage = {
        "policy_hash": "f" * 64,
        "reserved_calls": 0,
        "reserved_usd": Decimal("0"),
        "reserved_input_tokens": 0,
        "reserved_output_tokens": 0,
        "reserved_embedding_tokens": 0,
    }
    connection = _PolicyReadConnection(rows, audit_exists=True, usage_rows=[usage])

    policy = module._load_durable_budget_policy(
        connection_factory=lambda: connection,
        tenant_setter=lambda _connection, _tenant_id: None,
        tenant_id=_TENANT,
        env=env,
        expected_public_key_sha256=str(rows[0]["public_key_hash"]),
    )

    assert policy is not None


def test_durable_budget_policy_loader_rejects_key_swap_against_profile_digest(
    tmp_path: Path,
) -> None:
    module = _module()
    env, rows = _verified_policy_rows(tmp_path)
    connection = _PolicyReadConnection(rows, audit_exists=True)

    policy = module._load_durable_budget_policy(
        connection_factory=lambda: connection,
        tenant_setter=lambda _connection, _tenant_id: None,
        tenant_id=_TENANT,
        env=env,
        expected_public_key_sha256="f" * 64,
    )

    assert policy is None
    assert connection.statements == []


def test_budget_adapter_does_not_treat_an_unsigned_policy_as_loaded() -> None:
    module = _module()
    stores = _BudgetStoreFactory()

    facts = module.collect_provider_budget_facts(
        policy_loader=lambda _tenant_id: _signed_budget_policy(signature_verified=False),
        store_factory=stores,
        tenant_id=_TENANT,
        global_max_calls=500,
    )

    assert facts.policy_loaded is False
    assert facts.configured_provider_ids == frozenset()
    assert stores.operations == []


def _api_registry() -> str:
    common = {
        "timezone": "Asia/Qatar",
        "data_region": "me-south-1",
    }
    return json.dumps(
        {
            _OWNER_KEY: {
                **common,
                "tenant_id": _TENANT,
                "actor_id": "slice100-acceptance-service",
                "name": "Slice100 Acceptance Service",
                "roles": ["INTEGRATION_SERVICE"],
            },
            _CANARY_KEY: {
                **common,
                "tenant_id": _CANARY_TENANT,
                "actor_id": "slice100-isolation-canary",
                "name": "Slice100 Isolation Canary",
                "roles": ["INTEGRATION_SERVICE"],
            },
            _ADMIN_KEY: {
                **common,
                "tenant_id": _TENANT,
                "actor_id": "slice100-governance-admin",
                "name": "Slice100 Governance Admin",
                "roles": ["ADMIN"],
            },
        },
        sort_keys=True,
    )


def _strict_env(tmp_path: Path) -> dict[str, str]:
    object_root = tmp_path / "persistent-objects"
    object_root.mkdir()
    policy_path = tmp_path / "signed-budget-policy.json"
    policy_path.write_text("{}", encoding="utf-8")
    return {
        "IDIS_DATABASE_URL": "postgresql+psycopg2://app:secret@db/idis",
        "IDIS_ERASURE_DATABASE_URL": ("postgresql+psycopg2://idis_erasure_executor:secret@db/idis"),
        "IDIS_REDIS_URL": "redis://redis.internal:6379/0",
        "IDIS_RATE_LIMIT_USER_RPM": "600",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM": "1200",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "2",
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": str(object_root.resolve()),
        # Task 4 controlled drift: the startup aggregator now proves the
        # bounded large-upload path and local headroom.
        "IDIS_INGEST_MAX_BYTES": "536870912",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_OCR_DPI": "200",
        "IDIS_API_KEYS_JSON": _api_registry(),
        "IDIS_REAL_EXAMPLE_API_KEY": _OWNER_KEY,
        "IDIS_ISOLATION_CANARY_API_KEY": _CANARY_KEY,
        "IDIS_GOVERNANCE_ADMIN_API_KEY": _ADMIN_KEY,
        "IDIS_ACCEPTANCE_TENANT_ID": _TENANT,
        "IDIS_ISOLATION_CANARY_TENANT_ID": _CANARY_TENANT,
        "IDIS_SERVICE_REGION": "me-south-1",
        "IDIS_UI_DEV_AUTH_BYPASS": "false",
        "IDIS_PROCESS_ROLE": "worker",
        "IDIS_WORKER_TENANT_IDS": _TENANT,
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "10",
        "IDIS_PROVIDER_BUDGET_MAX_CALLS": "500",
        "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(policy_path.resolve()),
        "IDIS_PROVIDER_BUDGET_CURRENCY": "USD",
    }


def _complete_probe_set(module: ModuleType, calls: list[str]) -> object:
    def observed(name: str, facts: object) -> Callable[[], object]:
        def probe() -> object:
            calls.append(name)
            return facts

        return probe

    return module.DurableDependencyProbeAdapters(
        postgres=observed(
            "postgres",
            module.PostgresProbeFacts(True, True, False, False, True, True, True),
        ),
        audit=observed("audit", module.AuditProbeFacts("postgres", True, True, True)),
        idempotency=observed(
            "idempotency",
            module.IdempotencyProbeFacts("postgres", True, True, True, True, True),
        ),
        redis=observed("redis", module.RedisProbeFacts("redis", True, True, True)),
        object_store=observed(
            "object_store",
            module.ObjectStoreProbeFacts("filesystem", True, True, True, True, True),
        ),
        ingestion=observed(
            "ingestion",
            module.IngestionProbeFacts(
                backend="filesystem",
                put_stream_supported=True,
                bounded_read_ok=True,
                stream_write_ok=True,
                stream_read_ok=True,
                stream_hash_ok=True,
                stream_cleanup_ok=True,
                staging_write_ok=True,
                staging_cleanup_ok=True,
                staging_free_bytes=536870912,
                object_store_free_bytes=536870912,
                shared_filesystem=False,
                password_manifest_status="none_configured",  # Task 4 source audit.
            ),
        ),
        worker=observed(
            "worker",
            module.WorkerProbeFacts(
                backend="postgres",
                queued_row_ok=True,
                claim_ok=True,
                fencing_ok=True,
                claim_audit_ok=True,
                heartbeat_ok=True,
                reclaim_ok=True,
                fresh_claim_ok=True,
                stale_write_rejected=True,
                boundary_rollback_ok=True,
                no_residue=True,
            ),
        ),
        budgets=observed(
            "budgets",
            module.BudgetProbeFacts(
                "postgres",
                True,
                "USD",
                _BUDGET_COMPONENTS,
                True,
                True,
                True,
                True,
                True,
                True,
            ),
        ),
        erasure_executor=observed(
            "erasure_executor",
            module.ErasureExecutorProbeFacts(
                connected=True,
                authenticated_role="idis_erasure_executor",
                effective_role="idis_erasure_executor",
                executor_target=module.ErasureDatabaseTargetFacts(
                    database_name="idis",
                    server_address="127.0.0.1",
                    server_port=15432,
                ),
                app_target=module.ErasureDatabaseTargetFacts(
                    database_name="idis",
                    server_address="127.0.0.1",
                    server_port=15432,
                ),
                app_role="idis_app",
                app_effective_role="idis_app",
                executor_role=module.ErasureRoleFacts(
                    "idis_erasure_executor",
                    True,
                    False,
                    False,
                    False,
                    False,
                    False,
                ),
                owner_role=module.ErasureRoleFacts(
                    "idis_erasure_function_owner",
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                ),
                membership_count=0,
                executor_table_privilege_count=0,
                executor_column_privilege_count=0,
                executor_schema_usage=True,
                executor_schema_create=False,
                executor_schema_direct_acl_count=1,
                executor_other_direct_execute_count=0,
                function_count=1,
                function_schema="public",
                function_name="idis_task7_execute_approved_erasure",
                function_identity_arguments=("uuid", "uuid", "uuid"),
                function_result_type="jsonb",
                function_owner="idis_erasure_function_owner",
                function_security_definer=True,
                function_search_path=("pg_catalog", "pg_temp"),
                executor_has_execute=True,
                public_has_execute=False,
                app_role_has_execute=False,
                unexpected_execute_grant_count=0,
                function_owner_update_acl=module.ErasureFunctionOwnerUpdateAclFacts(
                    relation_tuple=("erasure_requests",),
                    column_update_acl_count=5,
                    ordered_column_tuple=(
                        "request_id",
                        "status",
                        "executed_by",
                        "executed_at",
                        "counts",
                    ),
                    grantable_acl_count=0,
                    table_update_privilege_count=0,
                ),
            ),
        ),
    )


@pytest.mark.parametrize("expected_role", ["api", "worker", "gate"])
def test_startup_aggregator_executes_all_operational_probes_before_success(
    tmp_path: Path,
    expected_role: str,
) -> None:
    module = _module()
    calls: list[str] = []
    probes = _complete_probe_set(module, calls)
    env = _strict_env(tmp_path)
    if expected_role == "gate":
        env.pop("IDIS_PROCESS_ROLE")
    else:
        env["IDIS_PROCESS_ROLE"] = expected_role
    if expected_role == "worker":
        env.pop("IDIS_ERASURE_DATABASE_URL")

    report = module.run_durable_dependency_admission(
        env=env,
        probes=probes,
        expected_role=expected_role,
        worktree_root=(tmp_path / "worktree").resolve(),
        temp_root=(tmp_path / "temp").resolve(),
    )

    assert report.may_proceed is True
    assert report.blocker_codes == ()
    expected_calls = [
        "postgres",
        "audit",
        "idempotency",
        "redis",
        "object_store",
        "ingestion",
        "worker",
        "budgets",
    ]
    if expected_role != "worker":
        expected_calls.insert(1, "erasure_executor")
    assert calls == expected_calls


def test_startup_aggregator_cannot_report_config_only_success_when_a_probe_fails(
    tmp_path: Path,
) -> None:
    module = _module()
    calls: list[str] = []
    probes = _complete_probe_set(module, calls)

    def unavailable_budget() -> object:
        calls.append("budgets-unavailable")
        raise RuntimeError("hermetic durable store unavailable")

    probes = module.DurableDependencyProbeAdapters(
        postgres=probes.postgres,
        audit=probes.audit,
        idempotency=probes.idempotency,
        redis=probes.redis,
        object_store=probes.object_store,
        ingestion=probes.ingestion,
        worker=probes.worker,
        budgets=unavailable_budget,
        erasure_executor=probes.erasure_executor,
    )
    report = module.run_durable_dependency_admission(
        env=_strict_env(tmp_path),
        probes=probes,
        expected_role="worker",
        worktree_root=(tmp_path / "worktree").resolve(),
        temp_root=(tmp_path / "temp").resolve(),
    )

    assert report.may_proceed is False
    assert "PROVIDER_BUDGET_CONFIG_BLOCKED" in report.blocker_codes
    assert "budgets-unavailable" in calls
