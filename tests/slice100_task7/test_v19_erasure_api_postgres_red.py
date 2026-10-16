"""Real API/PostgreSQL RED proof for the full-strict-v19 erasure composition.

This suite closes the seam between the already-separated hermetic API/service
tests and the direct PostgreSQL function proof.  Administrative access is setup
only.  Every behavioral observation runs through either the authenticated API,
the disposable application role under FORCE RLS, or the dedicated executor
credential.

The operator-backed cases intentionally remain gated on the frozen v19
artifact/fixture and migration 0038.  They must not be run against a candidate
or partially materialized authority.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import DisposablePostgres
from tests.slice100_task7.test_v19_erasure_postgres_app_role_red import (
    _assert_app_role,
    _seed_request,
    _v19_database,
)

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.postgres_sink import PostgresAuditSink
from idis.compliance.erasure import reset_erasure_executor
from idis.compliance.retention import reset_legal_hold_registry
from idis.persistence.db import reset_engines, set_tenant_local

_TENANT_A = "11111111-1111-4111-8111-111111111111"
_TENANT_B = "22222222-2222-4222-8222-222222222222"
_DEAL_ID = "33333333-3333-4333-8333-333333333333"
_REQUEST_ID = "44444444-4444-4444-8444-444444444444"

_ADMIN_A_KEY = "task7-v19-api-admin-a"
_ADMIN_A_RETRY_KEY = "task7-v19-api-admin-a-retry"
_ADMIN_B_KEY = "task7-v19-api-admin-b"
_ADMIN_A_ID = "task7-v19-admin-a"
_ADMIN_A_RETRY_ID = "task7-v19-admin-a-retry"
_ADMIN_B_ID = "task7-v19-admin-b"
_CLIENT_IP = "testclient"
_INITIAL_USER_AGENT = "slice100-v19-api-initial"
_CHANGED_USER_AGENT = "slice100-v19-api-retry-changed"

_EXECUTE_PATH = f"/v1/erasure-requests/{_REQUEST_ID}/execute"
_TASK7_TYPES = {
    "task7.erasure.attempted",
    "task7.erasure.failed",
    "task7.erasure.succeeded",
}


@dataclass(frozen=True, slots=True)
class _ApiPostgresRuntime:
    database: DisposablePostgres
    executor_engine: Engine


def _render_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _operator_database_is_configured() -> bool:
    return bool(os.environ.get("IDIS_DATABASE_ADMIN_URL", "").strip()) and bool(
        os.environ.get("IDIS_ERASURE_DATABASE_URL", "").strip()
    )


def _api_key_registry() -> dict[str, dict[str, object]]:
    common: dict[str, object] = {
        "name": "Task 7 v19 API/PostgreSQL proof",
        "timezone": "UTC",
        "data_region": "us-east-1",
        "roles": ["ADMIN"],
    }
    return {
        _ADMIN_A_KEY: {
            **common,
            "tenant_id": _TENANT_A,
            "actor_id": _ADMIN_A_ID,
        },
        _ADMIN_A_RETRY_KEY: {
            **common,
            "tenant_id": _TENANT_A,
            "actor_id": _ADMIN_A_RETRY_ID,
        },
        _ADMIN_B_KEY: {
            **common,
            "tenant_id": _TENANT_B,
            "actor_id": _ADMIN_B_ID,
        },
    }


@pytest.fixture(scope="module")
def v19_api_postgres_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_ApiPostgresRuntime]:
    if not _operator_database_is_configured():
        if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
            pytest.fail(
                "TASK7_V19_API_POSTGRES_CONFIGURATION_MISSING:"
                "IDIS_DATABASE_ADMIN_URL,IDIS_ERASURE_DATABASE_URL",
                pytrace=False,
            )
        pytest.skip(
            "v19 API/PostgreSQL proof requires the operator database and erasure credential"
        )

    environment = pytest.MonkeyPatch()
    object_store_base = tmp_path_factory.mktemp("task7-v19-api-object-store")
    with _v19_database() as (database, executor_engine):
        environment.setenv("IDIS_DATABASE_ADMIN_URL", _render_url(database.admin_engine))
        environment.setenv("IDIS_DATABASE_URL", _render_url(database.app_engine))
        environment.setenv("IDIS_ERASURE_DATABASE_URL", _render_url(executor_engine))
        environment.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
        environment.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(object_store_base))
        environment.setenv(
            "IDIS_AUDIT_LOG_PATH",
            str(object_store_base / "fallback-audit-never-selected.jsonl"),
        )
        environment.setenv("IDIS_REQUIRE_FULL_LIVE", "0")
        environment.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
        # The execute route needs no Redis-backed capability.  Removing an
        # inherited strict-profile URL prevents this disposable proof from
        # reading or mutating any acceptance rate-limit namespace.
        environment.delenv("IDIS_REDIS_URL", raising=False)
        environment.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_key_registry(), sort_keys=True))
        reset_engines()
        try:
            yield _ApiPostgresRuntime(
                database=database,
                executor_engine=executor_engine,
            )
        finally:
            reset_engines()
            environment.undo()


@pytest.fixture(autouse=True)
def _clean_api_postgres_state(
    v19_api_postgres_runtime: _ApiPostgresRuntime,
) -> Iterator[None]:
    """Use admin only to reset the disposable fixture between app-role proofs."""

    reset_engines()
    reset_erasure_executor()
    reset_legal_hold_registry()
    with v19_api_postgres_runtime.database.admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE TABLE public.audit_events, public.erasure_requests, "
            "public.legal_holds, public.document_artifacts, public.deals, "
            "public.tenants CASCADE"
        )
    yield
    reset_engines()
    reset_erasure_executor()
    reset_legal_hold_registry()


def _headers(
    api_key: str,
    *,
    user_agent: str = _INITIAL_USER_AGENT,
) -> dict[str, str]:
    return {
        "X-IDIS-API-Key": api_key,
        "User-Agent": user_agent,
    }


def _client() -> TestClient:
    app = create_app(
        postgres_audit_sink=PostgresAuditSink(),
        service_region="us-east-1",
    )
    return TestClient(app, raise_server_exceptions=False)


def _seed(runtime: _ApiPostgresRuntime) -> None:
    _seed_request(
        runtime.database,
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        request_id=_REQUEST_ID,
    )


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)


def _tenant_events(
    runtime: _ApiPostgresRuntime,
    *,
    tenant_id: str,
) -> tuple[dict[str, Any], ...]:
    with runtime.database.app_engine.begin() as connection:
        _assert_app_role(connection, runtime.database.role_name)
        set_tenant_local(connection, tenant_id)
        rows = connection.execute(
            text(
                """
                SELECT event
                FROM public.audit_events
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                ORDER BY occurred_at, event_id
                """
            ),
            {"tenant_id": tenant_id},
        ).scalars()
        values = tuple(rows)
    assert all(isinstance(value, Mapping) for value in values)
    return tuple(dict(value) for value in values)


def _durable_snapshot(
    runtime: _ApiPostgresRuntime,
) -> tuple[tuple[object, ...], int, tuple[tuple[str, str], ...]]:
    with runtime.database.app_engine.begin() as connection:
        _assert_app_role(connection, runtime.database.role_name)
        set_tenant_local(connection, _TENANT_A)
        request = tuple(
            connection.execute(
                text(
                    """
                    SELECT status, executed_by, executed_at, counts
                    FROM public.erasure_requests
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                      AND request_id = CAST(:request_id AS uuid)
                    """
                ),
                {
                    "tenant_id": _TENANT_A,
                    "deal_id": _DEAL_ID,
                    "request_id": _REQUEST_ID,
                },
            ).one()
        )
        deal_count = int(
            connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.deals
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND deal_id = CAST(:deal_id AS uuid)
                    """
                ),
                {"tenant_id": _TENANT_A, "deal_id": _DEAL_ID},
            ).scalar_one()
        )
        event_projection = tuple(
            (str(row.event_id), str(row.event_type))
            for row in connection.execute(
                text(
                    """
                    SELECT event_id, event_type
                    FROM public.audit_events
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                    ORDER BY event_id
                    """
                ),
                {"tenant_id": _TENANT_A},
            )
        )
    return request, deal_count, event_projection


def _task7_events(events: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(event for event in events if event.get("event_type") in _TASK7_TYPES)


def _generic_success_events(
    events: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "erasure.executed":
            continue
        request = event.get("request")
        if isinstance(request, Mapping) and request.get("status_code") == 200:
            matches.append(event)
    return tuple(matches)


def _actor_id(event: Mapping[str, Any]) -> str:
    actor = event.get("actor")
    assert isinstance(actor, Mapping)
    value = actor.get("actor_id")
    assert isinstance(value, str)
    return value


def _assert_succeeded_semantics(
    event: Mapping[str, Any],
    *,
    actor_id: str,
    client_ip: str = _CLIENT_IP,
    user_agent: str = _INITIAL_USER_AGENT,
) -> None:
    assert event["tenant_id"] == _TENANT_A
    assert _actor_id(event) == actor_id
    actor = event["actor"]
    assert isinstance(actor, Mapping)
    assert actor["ip"] == client_ip
    assert actor["user_agent"] == user_agent
    assert event["occurred_at"]
    resource = event["resource"]
    payload = event["payload"]
    assert isinstance(resource, Mapping)
    assert isinstance(payload, Mapping)
    safe = payload["safe"]
    assert isinstance(safe, Mapping)
    assert resource["resource_id"] == _REQUEST_ID
    assert resource["deal_id"] == _DEAL_ID
    assert safe["outcome"] == "SUCCEEDED"
    assert safe["request_id"] == _REQUEST_ID
    assert isinstance(safe["counts"], Mapping)


def test_application_role_cannot_execute_the_dedicated_function(
    v19_api_postgres_runtime: _ApiPostgresRuntime,
) -> None:
    """The app credential has RLS data access but no erasure function authority."""

    _seed(v19_api_postgres_runtime)
    before = _durable_snapshot(v19_api_postgres_runtime)

    with (
        pytest.raises(DBAPIError) as caught,
        v19_api_postgres_runtime.database.app_engine.begin() as connection,
    ):
        _assert_app_role(connection, v19_api_postgres_runtime.database.role_name)
        set_tenant_local(connection, _TENANT_A)
        connection.execute(
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
                "request_id": _REQUEST_ID,
                "tenant_id": _TENANT_A,
                "deal_id": _DEAL_ID,
            },
        ).scalar_one()

    assert _sqlstate(caught.value) == "42501"
    assert _durable_snapshot(v19_api_postgres_runtime) == before


def test_same_tenant_api_uses_only_dedicated_authority_and_persists_terminal_lineage(
    v19_api_postgres_runtime: _ApiPostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth -> RLS -> dedicated function -> durable audit is one real route."""

    from idis.compliance import erasure as legacy_erasure
    from idis.compliance import erasure_postgres as legacy_postgres

    def forbidden_legacy(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("legacy erasure executor became production-reachable")

    monkeypatch.setattr(legacy_erasure, "execute_erasure", forbidden_legacy)
    monkeypatch.setattr(legacy_postgres.PostgresErasureExecutor, "erase_deal", forbidden_legacy)
    _seed(v19_api_postgres_runtime)

    with _client() as client:
        response = client.post(_EXECUTE_PATH, headers=_headers(_ADMIN_A_KEY))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == _REQUEST_ID
    assert body["status"] == "EXECUTED"
    assert body["counts"]["deals"] == 1
    assert body["counts"]["object_rows_reconciled"] == 0

    request, deal_count, _event_projection = _durable_snapshot(v19_api_postgres_runtime)
    assert request[0] == "EXECUTED"
    assert request[1] == _ADMIN_A_ID
    assert deal_count == 0

    events = _tenant_events(v19_api_postgres_runtime, tenant_id=_TENANT_A)
    assert [event["event_type"] for event in _task7_events(events)] == [
        "task7.erasure.attempted",
        "task7.erasure.succeeded",
    ]
    attempted, succeeded = _task7_events(events)
    for event in (attempted, succeeded):
        actor = event["actor"]
        assert isinstance(actor, Mapping)
        assert actor["ip"] == _CLIENT_IP
        assert actor["user_agent"] == _INITIAL_USER_AGENT
    _assert_succeeded_semantics(succeeded, actor_id=_ADMIN_A_ID)
    assert _generic_success_events(events) == ()


def test_cross_tenant_api_returns_non_enumerating_404_with_zero_mutation(
    v19_api_postgres_runtime: _ApiPostgresRuntime,
) -> None:
    _seed(v19_api_postgres_runtime)
    before = _durable_snapshot(v19_api_postgres_runtime)

    with _client() as client:
        response = client.post(_EXECUTE_PATH, headers=_headers(_ADMIN_B_KEY))

    assert response.status_code == 404
    assert response.json()["code"] == "ERASURE_REQUEST_NOT_FOUND"
    assert _durable_snapshot(v19_api_postgres_runtime) == before
    assert _tenant_events(v19_api_postgres_runtime, tenant_id=_TENANT_A) == ()
    assert _tenant_events(v19_api_postgres_runtime, tenant_id=_TENANT_B) == ()


@pytest.mark.parametrize(
    ("retry_key", "retry_actor_id", "retry_user_agent", "expect_generic_success"),
    (
        (_ADMIN_A_KEY, _ADMIN_A_ID, _INITIAL_USER_AGENT, False),
        (_ADMIN_A_KEY, _ADMIN_A_ID, _CHANGED_USER_AGENT, True),
        (_ADMIN_A_RETRY_KEY, _ADMIN_A_RETRY_ID, _INITIAL_USER_AGENT, True),
    ),
    ids=(
        "same-admin-same-transport-suppresses",
        "same-admin-changed-transport-keeps-generic",
        "cross-admin-keeps-generic",
    ),
)
def test_post_function_crash_reconstructs_terminal_once_and_suppresses_only_same_admin(
    v19_api_postgres_runtime: _ApiPostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
    retry_key: str,
    retry_actor_id: str,
    retry_user_agent: str,
    expect_generic_success: bool,
) -> None:
    """Committed deletion is never replayed; audit replacement remains actor-honest."""

    from idis.persistence import erasure_db

    _seed(v19_api_postgres_runtime)
    original_persist = erasure_db.Task7PostgresErasurePort.persist_lineage_event
    original_invoke = erasure_db.Task7DedicatedErasureConnection.invoke_approved_erasure
    function_calls = 0
    crash_pending = True

    def count_invoke(
        self: object,
        *,
        request_id: str,
        tenant_id: str,
        deal_id: str,
    ) -> Mapping[str, Any]:
        nonlocal function_calls
        function_calls += 1
        return cast(
            Mapping[str, Any],
            original_invoke(
                self,
                request_id=request_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
            ),
        )

    def crash_before_terminal_insert(
        self: object,
        *,
        event: Mapping[str, object],
        request_status: str | None = None,
    ) -> str:
        nonlocal crash_pending
        if event.get("event_type") == "task7.erasure.succeeded" and crash_pending:
            crash_pending = False
            raise RuntimeError("INJECTED_POST_FUNCTION_TERMINAL_CRASH")
        return cast(
            str,
            original_persist(
                self,
                event=event,
                request_status=request_status,
            ),
        )

    monkeypatch.setattr(
        erasure_db.Task7DedicatedErasureConnection,
        "invoke_approved_erasure",
        count_invoke,
    )
    monkeypatch.setattr(
        erasure_db.Task7PostgresErasurePort,
        "persist_lineage_event",
        crash_before_terminal_insert,
    )

    with _client() as client:
        crashed = client.post(_EXECUTE_PATH, headers=_headers(_ADMIN_A_KEY))
        assert crashed.status_code == 500
        assert function_calls == 1

        post_commit_request, post_commit_deal_count, _ = _durable_snapshot(v19_api_postgres_runtime)
        assert post_commit_request[0] == "EXECUTED"
        assert post_commit_request[1] == _ADMIN_A_ID
        assert post_commit_deal_count == 0
        assert [
            event["event_type"]
            for event in _task7_events(
                _tenant_events(v19_api_postgres_runtime, tenant_id=_TENANT_A)
            )
        ] == ["task7.erasure.attempted"]

        monkeypatch.setattr(
            erasure_db.Task7PostgresErasurePort,
            "persist_lineage_event",
            original_persist,
        )
        recovered = client.post(
            _EXECUTE_PATH,
            headers=_headers(retry_key, user_agent=retry_user_agent),
        )

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "EXECUTED"
    assert function_calls == 1, "terminal reconstruction must not re-enter the delete function"

    events = _tenant_events(v19_api_postgres_runtime, tenant_id=_TENANT_A)
    task7_events = _task7_events(events)
    assert [event["event_type"] for event in task7_events] == [
        "task7.erasure.attempted",
        "task7.erasure.succeeded",
    ]
    attempted_actor = task7_events[0]["actor"]
    assert isinstance(attempted_actor, Mapping)
    assert attempted_actor["ip"] == _CLIENT_IP
    assert attempted_actor["user_agent"] == _INITIAL_USER_AGENT
    _assert_succeeded_semantics(task7_events[1], actor_id=_ADMIN_A_ID)

    generic_success = _generic_success_events(events)
    if expect_generic_success:
        assert len(generic_success) == 1
        assert _actor_id(generic_success[0]) == retry_actor_id
        generic_actor = generic_success[0]["actor"]
        assert isinstance(generic_actor, Mapping)
        assert generic_actor["ip"] == _CLIENT_IP
        assert generic_actor["user_agent"] == retry_user_agent
    else:
        assert generic_success == ()
