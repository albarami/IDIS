"""RED composition contracts for the full-strict-v19 erasure API boundary.

The public composition seam is intentionally narrow::

    execute_erasure_for_api(
        tenant_ctx,
        request_id,
        audit_sink,
        *,
        hold_checker,
    )

It returns the existing safe response projection plus
``terminal_lineage_proof``.  That proof is ``None`` only for the historical
non-PostgreSQL twin.  A PostgreSQL success carries the exact durable Task-7
terminal identity consumed by the route-scoped audit suppression predicate.

These tests are hermetic.  They exercise the real FastAPI route and middleware
composition, but never open a database connection or call an external service.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore

_TENANT_A = "11111111-1111-4111-8111-111111111111"
_TENANT_B = "22222222-2222-4222-8222-222222222222"
_DEAL_ID = "33333333-3333-4333-8333-333333333333"
_REQUEST_ID = "44444444-4444-4444-8444-444444444444"
_UNKNOWN_REQUEST_ID = "55555555-5555-4555-8555-555555555555"
_ATTEMPT_ID = "66666666-6666-4666-8666-666666666666"
_EVENT_ID = "77777777-7777-4777-8777-777777777777"
_EXECUTED_AT = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
_COUNTS = {
    "object_rows_reconciled": 2,
    "deals": 1,
}
_ADMIN_A_KEY = "task7-v19-admin-a"
_ADMIN_A_RECOVERY_KEY = "task7-v19-admin-a-recovery"
_ADMIN_B_KEY = "task7-v19-admin-b"
_CLIENT_IP = "testclient"
_USER_AGENT = "slice100-v19-client"


def _task7_module() -> Any:
    from idis.compliance import task7_erasure

    return task7_erasure


def _api_wrapper() -> Callable[..., object]:
    wrapper = getattr(_task7_module(), "execute_erasure_for_api", None)
    assert callable(wrapper), (
        "TASK7_V19_ERASURE_API_WRAPPER_MISSING:"
        "idis.compliance.task7_erasure.execute_erasure_for_api"
    )
    return wrapper


def _ctx(tenant_id: str = _TENANT_A, actor_id: str = "task7-admin-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        name="Fictional Task 7 Administrator",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


def _terminal_proof(
    *,
    actor_ip: str = _CLIENT_IP,
    actor_user_agent: str = _USER_AGENT,
    **overrides: object,
) -> dict[str, object]:
    replacement_event: dict[str, object] = {
        "event_id": _EVENT_ID,
        "occurred_at": _EXECUTED_AT.isoformat().replace("+00:00", "Z"),
        "tenant_id": _TENANT_A,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": "task7-admin-a",
            "roles": ["ADMIN"],
            "ip": actor_ip,
            "user_agent": actor_user_agent,
        },
        "request": {
            "request_id": _REQUEST_ID,
            "method": "POST",
            "path": f"/v1/erasure-requests/{_REQUEST_ID}/execute",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": _REQUEST_ID,
            "deal_id": _DEAL_ID,
        },
        "event_type": "task7.erasure.succeeded",
        "severity": "CRITICAL",
        "payload": {
            "safe": {
                "request_id": _REQUEST_ID,
                "attempt_id": _ATTEMPT_ID,
                "attempt_ordinal": 1,
                "prior_status": None,
                "prior_attempt_event_id": None,
                "prior_attempt_event_hash": None,
                "outcome": "SUCCEEDED",
                "counts": dict(_COUNTS),
            },
            "hashes": [f"object_target_set_sha256:{'e' * 64}"],
            "refs": [f"deal_id:{_DEAL_ID}"],
        },
    }
    proof: dict[str, object] = {
        "schema_version": "task7-erasure-terminal-lineage-proof-v1",
        "request_id": _REQUEST_ID,
        "attempt_id": _ATTEMPT_ID,
        "event_id": _EVENT_ID,
        "event_type": "task7.erasure.succeeded",
        "outcome": "SUCCEEDED",
        "durable": True,
        "replacement_event": replacement_event,
    }
    proof.update(overrides)
    return proof


def _projection(*, proof: Mapping[str, object] | None) -> SimpleNamespace:
    return SimpleNamespace(
        request_id=_REQUEST_ID,
        status="EXECUTED",
        executed_at=_EXECUTED_AT,
        counts=dict(_COUNTS),
        terminal_lineage_proof=None if proof is None else dict(proof),
    )


def _api_keys_json() -> str:
    def entry(tenant_id: str, actor_id: str) -> dict[str, object]:
        return {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "name": "Fictional Task 7 Administrator",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN"],
        }

    return json.dumps(
        {
            _ADMIN_A_KEY: entry(_TENANT_A, "task7-admin-a"),
            _ADMIN_A_RECOVERY_KEY: entry(_TENANT_A, "task7-recovery-admin-a"),
            _ADMIN_B_KEY: entry(_TENANT_B, "task7-admin-b"),
        }
    )


def _headers(api_key: str = _ADMIN_A_KEY) -> dict[str, str]:
    return {
        "X-IDIS-API-Key": api_key,
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


@pytest.fixture
def api_app(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, InMemoryAuditSink]]:
    """Real API/middleware composition with every durable dependency disabled."""

    from idis.api.main import create_app

    for name in (
        "IDIS_DATABASE_URL",
        "IDIS_ERASURE_DATABASE_URL",
        "IDIS_STRICT_DOTENV_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_json())
    sink = InMemoryAuditSink()
    app = create_app(
        audit_sink=sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        service_region="us-east-1",
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, sink


def _poison_legacy_route_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any residual route call to the historical global seam unmistakable."""

    from idis.api.routes import erasure_export

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("TASK7_V19_LEGACY_EXECUTION_REACHED_FROM_API")

    monkeypatch.setattr(erasure_export, "get_erasure_executor", forbidden, raising=False)
    monkeypatch.setattr(erasure_export, "execute_erasure", forbidden, raising=False)


def _install_route_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    wrapper: Callable[..., object],
) -> None:
    from idis.api.routes import erasure_export

    # ``raising=False`` keeps this suite collectable while the route import is
    # still RED.  The API call proves whether production actually uses the seam.
    monkeypatch.setattr(
        erasure_export,
        "execute_erasure_for_api",
        wrapper,
        raising=False,
    )


def _normalized_error(response: Any) -> dict[str, object]:
    body = dict(response.json())
    body.pop("request_id", None)
    return body


def test_public_wrapper_has_no_executor_or_persistence_override_surface() -> None:
    """Production callers cannot smuggle a legacy executor into the PG branch."""

    parameters = inspect.signature(_api_wrapper()).parameters
    assert {
        "tenant_ctx",
        "request_id",
        "audit_sink",
        "hold_checker",
        "client_ip",
        "user_agent",
    } <= set(parameters)
    for parameter in ("hold_checker", "client_ip", "user_agent"):
        assert parameters[parameter].kind is inspect.Parameter.KEYWORD_ONLY
    assert {
        "executor",
        "store",
        "service",
        "connection",
        "connection_factory",
        "database_url",
    }.isdisjoint(parameters)


def test_postgres_wrapper_selects_durable_branch_without_touching_legacy_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured PostgreSQL deployment cannot execute through the old core."""

    from idis.compliance import erasure as legacy_erasure
    from idis.persistence import db as persistence_db

    task7 = _task7_module()
    expected = _projection(proof=_terminal_proof())
    durable_calls: list[tuple[str, str, str, str]] = []

    def durable_branch(**kwargs: object) -> object:
        tenant_ctx = kwargs["tenant_ctx"]
        assert isinstance(tenant_ctx, TenantContext)
        durable_calls.append(
            (
                tenant_ctx.tenant_id,
                str(kwargs["request_id"]),
                str(kwargs["client_ip"]),
                str(kwargs["user_agent"]),
            )
        )
        return expected

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("TASK7_V19_POSTGRES_CALLED_LEGACY_ERASURE")

    monkeypatch.setattr(persistence_db, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(legacy_erasure, "execute_erasure", forbidden)
    monkeypatch.setattr(legacy_erasure, "get_erasure_executor", forbidden)
    monkeypatch.setattr(
        task7,
        "_execute_postgres_erasure_for_api",
        durable_branch,
        raising=False,
    )

    result = _api_wrapper()(
        _ctx(),
        _REQUEST_ID,
        InMemoryAuditSink(),
        hold_checker=lambda _tenant_id, _deal_id: None,
        client_ip=_CLIENT_IP,
        user_agent=_USER_AGENT,
    )

    assert result is expected
    assert durable_calls == [(_TENANT_A, _REQUEST_ID, _CLIENT_IP, _USER_AGENT)]
    assert result.terminal_lineage_proof == _terminal_proof()


def test_postgres_private_composition_is_bound_to_dedicated_dsn_and_service_ports() -> None:
    """The durable branch is visibly constructed from the dedicated credential."""

    task7 = _task7_module()
    durable_branch = getattr(task7, "_execute_postgres_erasure_for_api", None)
    builder = getattr(task7, "_build_postgres_erasure_api_composition", None)
    assert callable(durable_branch), "TASK7_V19_POSTGRES_API_BRANCH_MISSING"
    assert callable(builder), "TASK7_V19_POSTGRES_API_COMPOSITION_MISSING"
    assert "_build_postgres_erasure_api_composition" in inspect.getsource(durable_branch)

    source = inspect.getsource(builder)
    for required_token in (
        "IDIS_ERASURE_DATABASE_URL",
        "Task7DedicatedErasureConnectionFactory",
        "Task7ErasureExecutionService",
        "connection_factory=",
        "port=",
        "object_store=",
    ):
        assert required_token in source, (
            f"TASK7_V19_POSTGRES_API_COMPOSITION_INCOMPLETE:{required_token}"
        )
    assert "IDIS_DATABASE_URL" not in source
    assert "get_erasure_executor" not in source
    assert "PostgresErasureExecutor" not in source
    assert "InMemoryErasureExecutor" not in source


def test_terminal_proof_embeds_the_validated_persisted_replacement_event() -> None:
    """Middleware authority comes from stored Task-7 evidence, never caller input."""

    task7 = _task7_module()
    lineage = task7.derive_attempt_lineage(
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        request_id=_REQUEST_ID,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    terminal = task7.build_erasure_audit_event(
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        actor_id="task7-admin-a",
        actor_ip=_CLIENT_IP,
        actor_user_agent=_USER_AGENT,
        occurred_at=_EXECUTED_AT,
        lineage=lineage,
        event_type="task7.erasure.succeeded",
        object_target_set_sha256="e" * 64,
        counts=_COUNTS,
    )
    command = task7.Task7ErasureExecutionCommand(
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        request_id=_REQUEST_ID,
        actor_id="task7-admin-a",
    )
    result = task7.Task7ErasureExecutionResult(
        request_id=_REQUEST_ID,
        status="EXECUTED",
        executed_at=_EXECUTED_AT,
        counts=_COUNTS,
        terminal_event_id=str(terminal["event_id"]),
        terminal_event_sha256=task7.canonical_event_sha256(terminal),
    )

    proof = task7._terminal_lineage_proof(
        command=command,
        result=result,
        raw_events=(terminal,),
    )

    assert proof["replacement_event"] == terminal
    assert proof["replacement_event"] is not terminal


def test_app_port_enumerates_targets_while_dedicated_connection_stays_table_blind() -> None:
    """The executor credential holds the lock/calls the function but reads no tables."""

    from idis.persistence import erasure_db

    app_port = getattr(erasure_db, "Task7PostgresErasurePort", None)
    assert app_port is not None, "TASK7_V19_APP_ROLE_ERASURE_PORT_MISSING"
    app_source = inspect.getsource(app_port)
    # SQL remains a module-level typed statement so SQLAlchemy can cache it.
    target_sql = str(erasure_db._LIST_OBJECT_TARGETS_SQL)
    assert "FROM public.document_artifacts" in target_sql
    assert 'ORDER BY uri COLLATE "C", doc_id' in target_sql
    assert "DISTINCT" not in target_sql.upper()
    assert "set_tenant_local" in app_source
    assert "require_tenant_session_lock" in app_source
    assert "assert_no_active_holds" in app_source
    hold_sql = str(erasure_db._ASSERT_NO_ACTIVE_HOLDS_SQL)
    for required in (
        "FROM public.legal_holds",
        "target_type = 'DEAL'",
        "target_type = 'DOCUMENT'",
        "target_type = 'ARTIFACT'",
        "FROM public.document_artifacts",
        "lifted_at IS NULL",
    ):
        assert required in hold_sql
    assert "pg_advisory" not in hold_sql

    # ``uri`` is not unique in the current schema: duplicates are preserved,
    # totally ordered by doc_id, and therefore change the bound cardinality.
    duplicate_uris = ("idis://same/object", "idis://same/object")
    duplicate_digest = _task7_module().object_target_set_sha256(
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        ordered_uris=duplicate_uris,
    )
    single_digest = _task7_module().object_target_set_sha256(
        tenant_id=_TENANT_A,
        deal_id=_DEAL_ID,
        ordered_uris=duplicate_uris[:1],
    )
    assert duplicate_digest != single_digest

    dedicated_source = inspect.getsource(erasure_db.Task7DedicatedErasureConnection)
    assert set(re.findall(r"_[A-Z][A-Z0-9_]*_SQL", dedicated_source)) == {
        "_ACQUIRE_TENANT_LOCK_SQL",
        "_RELEASE_TENANT_LOCK_SQL",
        "_INVOKE_APPROVED_ERASURE_SQL",
    }
    assert "document_artifacts" not in dedicated_source
    assert "audit_events" not in dedicated_source
    assert "erasure_requests" not in dedicated_source
    assert "DELETE FROM" not in dedicated_source.upper()


def test_ambiguous_session_lock_acquisition_invalidates_backend_before_pool_return() -> None:
    """A commit failure after pg_advisory_lock can never leak a pooled session lock."""

    from idis.persistence import erasure_db

    class _AmbiguousConnection:
        def __init__(self) -> None:
            self.lock_sql_executed = False
            self.invalidated = False
            self.closed = False

        def __enter__(self) -> _AmbiguousConnection:
            return self

        def __exit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> bool:
            self.closed = True
            return False

        def execute(self, *_args: object, **_kwargs: object) -> object:
            self.lock_sql_executed = True
            return object()

        def commit(self) -> None:
            raise RuntimeError("injected commit failure after server-side lock")

        def invalidate(self) -> None:
            self.invalidated = True

    class _Engine:
        def __init__(self, connection: _AmbiguousConnection) -> None:
            self.connection = connection

        def connect(self) -> _AmbiguousConnection:
            return self.connection

    connection = _AmbiguousConnection()
    factory = erasure_db.Task7DedicatedErasureConnectionFactory(
        engine=_Engine(connection),  # type: ignore[arg-type]
    )

    with (
        pytest.raises(
            erasure_db.DedicatedErasureDatabaseError,
            match="ERASURE_SESSION_LOCK_ACQUIRE_FAILED",
        ),
        factory() as adapter,
    ):
        adapter.acquire_tenant_session_lock(_TENANT_A)

    assert connection.lock_sql_executed is True
    assert connection.invalidated is True
    assert connection.closed is True


def test_non_postgres_wrapper_preserves_exact_legacy_execution_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hermetic twin remains reachable only when PostgreSQL is absent."""

    from idis.compliance import erasure as legacy_erasure
    from idis.persistence import db as persistence_db

    task7 = _task7_module()
    sink = InMemoryAuditSink()
    legacy_calls: list[dict[str, object]] = []

    def hold_checker(_tenant_id: str, _deal_id: str) -> None:
        return None

    def legacy_execute(
        tenant_ctx: TenantContext,
        request_id: str,
        audit_sink: InMemoryAuditSink,
        *,
        executor: object,
        hold_checker: object,
        store: object | None = None,
    ) -> SimpleNamespace:
        legacy_calls.append(
            {
                "tenant_ctx": tenant_ctx,
                "request_id": request_id,
                "audit_sink": audit_sink,
                "executor": executor,
                "hold_checker": hold_checker,
                "store": store,
            }
        )
        return SimpleNamespace(
            request_id=request_id,
            status=SimpleNamespace(value="EXECUTED"),
            executed_at=_EXECUTED_AT,
            counts=dict(_COUNTS),
        )

    legacy_executor = object()

    def forbidden_durable(**_kwargs: object) -> object:
        raise AssertionError("TASK7_V19_NON_POSTGRES_CALLED_DURABLE_BRANCH")

    monkeypatch.setattr(persistence_db, "is_postgres_configured", lambda: False)
    monkeypatch.setattr(legacy_erasure, "execute_erasure", legacy_execute)
    monkeypatch.setattr(legacy_erasure, "get_erasure_executor", lambda: legacy_executor)
    monkeypatch.setattr(
        task7,
        "_execute_postgres_erasure_for_api",
        forbidden_durable,
        raising=False,
    )

    result = _api_wrapper()(
        _ctx(),
        _REQUEST_ID,
        sink,
        hold_checker=hold_checker,
        client_ip=_CLIENT_IP,
        user_agent=_USER_AGENT,
    )

    assert len(legacy_calls) == 1
    call = legacy_calls[0]
    assert call["tenant_ctx"] == _ctx()
    assert call["request_id"] == _REQUEST_ID
    assert call["audit_sink"] is sink
    assert call["executor"] is legacy_executor
    assert call["hold_checker"] is hold_checker
    assert result.request_id == _REQUEST_ID
    assert result.status == "EXECUTED"
    assert result.executed_at == _EXECUTED_AT
    assert result.counts == _COUNTS
    assert result.terminal_lineage_proof is None


def test_durable_api_success_sets_exact_proof_and_suppresses_only_generic_event(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """The real route carries durable lineage to the real audit middleware."""

    from idis.api.middleware import audit as audit_middleware

    client, sink = api_app
    proof = _terminal_proof()
    wrapper_calls: list[tuple[str, str, str, str]] = []
    suppression_inputs: list[object | None] = []

    def durable_wrapper(
        tenant_ctx: TenantContext,
        request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> SimpleNamespace:
        assert callable(hold_checker)
        wrapper_calls.append((tenant_ctx.tenant_id, request_id, client_ip, user_agent))
        return _projection(proof=proof)

    original_suppression = audit_middleware._should_suppress_generic_erasure_executed

    def recording_suppression(
        request: Any,
        response: object,
        generic_event: Mapping[str, object],
    ) -> bool:
        state = request.state
        suppression_inputs.append(getattr(state, "task7_erasure_terminal_lineage_proof", None))
        return original_suppression(request, response, generic_event)

    _install_route_wrapper(monkeypatch, durable_wrapper)
    _poison_legacy_route_seams(monkeypatch)
    monkeypatch.setattr(
        audit_middleware,
        "_should_suppress_generic_erasure_executed",
        recording_suppression,
    )

    response = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "request_id": _REQUEST_ID,
        "status": "EXECUTED",
        "executed_at": "2026-07-24T10:30:00Z",
        "counts": _COUNTS,
    }
    assert wrapper_calls == [(_TENANT_A, _REQUEST_ID, _CLIENT_IP, _USER_AGENT)]
    assert suppression_inputs == [proof]
    assert [event for event in sink.events if event.get("event_type") == "erasure.executed"] == []


def test_durable_api_idempotency_evidence_retains_generic_event(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """Suppression cannot discard the generic event's idempotency-key digest."""

    from idis.api.middleware import audit as audit_middleware

    client, sink = api_app
    proof = _terminal_proof()
    observed_generic: list[dict[str, object]] = []

    def durable_wrapper(
        _tenant_ctx: TenantContext,
        _request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> SimpleNamespace:
        assert callable(hold_checker)
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        return _projection(proof=proof)

    _install_route_wrapper(monkeypatch, durable_wrapper)
    _poison_legacy_route_seams(monkeypatch)
    original_suppression = audit_middleware._should_suppress_generic_erasure_executed

    def recording_suppression(
        request: Any,
        response: object,
        generic_event: Mapping[str, object],
    ) -> bool:
        observed_generic.append(dict(generic_event))
        return original_suppression(request, response, generic_event)

    monkeypatch.setattr(
        audit_middleware,
        "_should_suppress_generic_erasure_executed",
        recording_suppression,
    )
    idempotency_key = "task7-v19-execute-idempotency"

    response = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers={**_headers(), "Idempotency-Key": idempotency_key},
    )

    assert response.status_code == 200, response.text
    assert len(observed_generic) == 1
    assert (
        observed_generic[0]["request"]["idempotency_key_sha256"]
        == sha256(idempotency_key.encode("utf-8")).hexdigest()
    )
    generic = [event for event in sink.events if event.get("event_type") == "erasure.executed"]
    assert len(generic) == 1
    assert (
        generic[0]["request"]["idempotency_key_sha256"]
        == sha256(idempotency_key.encode("utf-8")).hexdigest()
    )


def test_cross_admin_reconstruction_retains_generic_event_for_current_http_actor(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """Persisted Task-7 lineage cannot suppress the distinct retrying ADMIN."""

    client, sink = api_app
    proof = _terminal_proof()

    def reconstructed_wrapper(
        tenant_ctx: TenantContext,
        request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> SimpleNamespace:
        assert callable(hold_checker)
        assert tenant_ctx.actor_id == "task7-recovery-admin-a"
        assert request_id == _REQUEST_ID
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        # The deterministic terminal event correctly preserves the original
        # attempted actor, not the ADMIN handling crash reconstruction.
        return _projection(proof=proof)

    _install_route_wrapper(monkeypatch, reconstructed_wrapper)
    _poison_legacy_route_seams(monkeypatch)

    response = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers=_headers(_ADMIN_A_RECOVERY_KEY),
    )

    assert response.status_code == 200, response.text
    generic = [event for event in sink.events if event.get("event_type") == "erasure.executed"]
    assert len(generic) == 1
    assert generic[0]["actor"]["actor_id"] == "task7-recovery-admin-a"
    assert generic[0]["tenant_id"] == _TENANT_A
    assert generic[0]["resource"] == {
        "resource_type": "erasure_request",
        "resource_id": _REQUEST_ID,
    }


def test_same_admin_reconstruction_with_changed_transport_retains_generic_event(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """A retry from a changed client cannot erase either transport observation."""

    client, sink = api_app
    original_user_agent = "slice100-v19-original-client"
    proof = _terminal_proof(actor_user_agent=original_user_agent)

    def reconstructed_wrapper(
        tenant_ctx: TenantContext,
        request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> SimpleNamespace:
        assert callable(hold_checker)
        assert tenant_ctx.actor_id == "task7-admin-a"
        assert request_id == _REQUEST_ID
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        return _projection(proof=proof)

    _install_route_wrapper(monkeypatch, reconstructed_wrapper)
    _poison_legacy_route_seams(monkeypatch)

    response = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    generic = [event for event in sink.events if event.get("event_type") == "erasure.executed"]
    assert len(generic) == 1
    assert generic[0]["actor"] == {
        "actor_type": "SERVICE",
        "actor_id": "task7-admin-a",
        "roles": ["ADMIN"],
        "ip": _CLIENT_IP,
        "user_agent": _USER_AGENT,
    }
    replacement = proof["replacement_event"]
    assert isinstance(replacement, Mapping)
    replacement_actor = replacement["actor"]
    assert isinstance(replacement_actor, Mapping)
    assert replacement_actor["user_agent"] == original_user_agent


def test_non_postgres_api_result_retains_generic_mutation_audit(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """No Task-7 proof means the historical generic event is not suppressed."""

    client, sink = api_app

    def legacy_wrapper(
        _tenant_ctx: TenantContext,
        _request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> SimpleNamespace:
        assert callable(hold_checker)
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        return _projection(proof=None)

    _install_route_wrapper(monkeypatch, legacy_wrapper)
    _poison_legacy_route_seams(monkeypatch)

    response = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    generic = [event for event in sink.events if event.get("event_type") == "erasure.executed"]
    assert len(generic) == 1
    assert generic[0]["resource"] == {
        "resource_type": "erasure_request",
        "resource_id": _REQUEST_ID,
    }


def test_route_never_sets_terminal_proof_when_durable_wrapper_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proof is an after-success fact, never a predeclared intent marker."""

    from idis.api.routes import erasure_export

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_sink=InMemoryAuditSink())),
        state=SimpleNamespace(),
        client=SimpleNamespace(host=_CLIENT_IP),
        headers={"User-Agent": _USER_AGENT},
    )

    def failed_wrapper(
        _tenant_ctx: TenantContext,
        _request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> object:
        assert callable(hold_checker)
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_EXECUTION_FAILED",
            message="Erasure execution failed",
        )

    _install_route_wrapper(monkeypatch, failed_wrapper)
    _poison_legacy_route_seams(monkeypatch)

    with pytest.raises(IdisHttpError) as exc_info:
        erasure_export.execute_erasure_request(
            request_id=_REQUEST_ID,
            tenant_ctx=_ctx(),
            request=request,
        )

    assert exc_info.value.code == "ERASURE_EXECUTION_FAILED"
    assert not hasattr(request.state, "task7_erasure_terminal_lineage_proof")
    assert not hasattr(request.state, "audit_resource_id")


def test_unknown_and_cross_tenant_requests_are_api_indistinguishable(
    monkeypatch: pytest.MonkeyPatch,
    api_app: tuple[TestClient, InMemoryAuditSink],
) -> None:
    """The durable resolver exposes no request-existence oracle."""

    client, sink = api_app
    seen: list[tuple[str, str]] = []

    def nonenumerating_wrapper(
        tenant_ctx: TenantContext,
        request_id: str,
        _audit_sink: object,
        *,
        hold_checker: object,
        client_ip: str,
        user_agent: str,
    ) -> object:
        assert callable(hold_checker)
        assert client_ip == _CLIENT_IP
        assert user_agent == _USER_AGENT
        seen.append((tenant_ctx.tenant_id, request_id))
        raise IdisHttpError(
            status_code=404,
            code="ERASURE_REQUEST_NOT_FOUND",
            message="Erasure request not found",
        )

    _install_route_wrapper(monkeypatch, nonenumerating_wrapper)
    _poison_legacy_route_seams(monkeypatch)

    cross_tenant = client.post(
        f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        headers=_headers(_ADMIN_B_KEY),
    )
    unknown = client.post(
        f"/v1/erasure-requests/{_UNKNOWN_REQUEST_ID}/execute",
        headers=_headers(_ADMIN_A_KEY),
    )

    assert cross_tenant.status_code == unknown.status_code == 404
    assert _normalized_error(cross_tenant) == _normalized_error(unknown)
    assert _normalized_error(cross_tenant)["code"] == "ERASURE_REQUEST_NOT_FOUND"
    assert seen == [
        (_TENANT_B, _REQUEST_ID),
        (_TENANT_A, _UNKNOWN_REQUEST_ID),
    ]
    assert [event for event in sink.events if event.get("event_type") == "erasure.executed"] == []
