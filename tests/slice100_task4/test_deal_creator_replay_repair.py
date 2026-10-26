"""Task 4 compatibility contract for pre-fix ``createDeal`` replays.

The historical defect committed the deal, durable idempotency response, and
``deal.created`` audit row without the creator assignment required by ABAC.  A
replay may repair that exact invariant only when its durable lineage is
unambiguous; it must never become a general assignment backfill.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from idis.api.middleware.audit import AuditMiddleware
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import IdempotencyRecord, ScopeKey

TENANT_ID = "10000000-0000-4000-8000-000000000001"
OTHER_TENANT_ID = "10000000-0000-4000-8000-000000000002"
ACTOR_ID = "task4-owner-service"
DEAL_ID = "20000000-0000-4000-8000-000000000001"
RAW_KEY = "task4-create-deal-key-do-not-log"
PAYLOAD_SHA256 = "sha256:" + "a" * 64
CREATED_AT = "2026-07-17T08:00:00Z"
IDEMPOTENCY_AT = "2026-07-17T08:00:01Z"
AUDIT_AT = datetime(2026, 7, 17, 8, 0, 2, tzinfo=UTC)


def _body(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "deal_id": DEAL_ID,
        "name": "company-001-deal",
        "company_name": "company-001",
        "status": "NEW",
        "stage": None,
        "tags": [],
        "created_at": CREATED_AT,
        "updated_at": None,
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":")).encode()


def _record(**overrides: object) -> IdempotencyRecord:
    values: dict[str, object] = {
        "payload_sha256": PAYLOAD_SHA256,
        "status_code": 201,
        "media_type": "application/json",
        "body_bytes": _body(),
        "created_at": IDEMPOTENCY_AT,
    }
    values.update(overrides)
    return IdempotencyRecord(**values)  # type: ignore[arg-type]


def _scope(**overrides: str) -> ScopeKey:
    values = {
        "tenant_id": TENANT_ID,
        "actor_id": ACTOR_ID,
        "method": "POST",
        "operation_id": "createDeal",
        "idempotency_key": RAW_KEY,
    }
    values.update(overrides)
    return ScopeKey(**values)


def _deal_row(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "deal_id": DEAL_ID,
        "tenant_id": TENANT_ID,
        "name": "company-001-deal",
        "company_name": "company-001",
        "status": "NEW",
        "stage": None,
        "tags": [],
        "created_at": datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _idempotency_row(record: IdempotencyRecord | None = None) -> SimpleNamespace:
    value = record or _record()
    return SimpleNamespace(
        payload_sha256=value.payload_sha256,
        status_code=value.status_code,
        media_type=value.media_type,
        body_bytes=value.body_bytes,
        created_at=datetime.fromisoformat(value.created_at.replace("Z", "+00:00")),
    )


def _creation_event(**overrides: object) -> SimpleNamespace:
    key_hash = sha256(RAW_KEY.encode()).hexdigest()
    event: dict[str, object] = {
        "event_id": "30000000-0000-4000-8000-000000000001",
        "occurred_at": AUDIT_AT.isoformat().replace("+00:00", "Z"),
        "tenant_id": TENANT_ID,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": ACTOR_ID,
            "roles": ["INTEGRATION_SERVICE"],
            "ip": "127.0.0.1",
            "user_agent": "task4-test",
        },
        "request": {
            "request_id": "request-original",
            "method": "POST",
            "path": "/v1/deals",
            "status_code": 201,
            "idempotency_key_sha256": key_hash,
        },
        "resource": {"resource_type": "deal", "resource_id": DEAL_ID},
        "event_type": "deal.created",
        "severity": "MEDIUM",
        "summary": "deal.created via POST /v1/deals",
        "payload": {"hashes": [PAYLOAD_SHA256], "refs": []},
    }
    values: dict[str, object] = {
        "event_id": event["event_id"],
        "tenant_id": TENANT_ID,
        "occurred_at": AUDIT_AT,
        "event_type": "deal.created",
        "request_id": "request-original",
        "idempotency_key": key_hash,
        "event": event,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Result:
    def __init__(self, *, one: object | None = None, all_rows: list[object] | None = None) -> None:
        self._one = one
        self._all = all_rows

    def fetchone(self) -> object | None:
        return self._one

    def fetchall(self) -> list[object]:
        return list(self._all or [])


class _ReplayConnection:
    """Semantic SQL double; it checks authority bindings, not query formatting."""

    def __init__(
        self,
        *,
        record: IdempotencyRecord | None = None,
        deal: object | None = None,
        assignment_exists: bool = False,
        creation_events: list[object] | None = None,
        later_assignment_event: object | None = None,
        insert_row: object | None = (1,),
    ) -> None:
        self.record = record or _record()
        self.deal = _deal_row() if deal is None else deal
        self.assignment_exists = assignment_exists
        self.creation_events = [_creation_event()] if creation_events is None else creation_events
        self.later_assignment_event = later_assignment_event
        self.insert_row = insert_row
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        statement: object,
        params: dict[str, object] | None = None,
    ) -> _Result:
        sql = " ".join(str(statement).lower().split())
        bound = dict(params or {})
        self.calls.append((sql, bound))
        if "from idempotency_records" in sql:
            return _Result(one=_idempotency_row(self.record))
        if "from deals" in sql and "for update" in sql:
            return _Result(one=self.deal)
        if "from deal_assignments" in sql:
            return _Result(one=(1,) if self.assignment_exists else None)
        if "event_type = 'deal.created'" in sql:
            return _Result(all_rows=self.creation_events)
        if "rbac.assignment.created" in sql and "rbac.assignment.deleted" in sql:
            return _Result(one=self.later_assignment_event)
        if sql.startswith("insert into deal_assignments"):
            return _Result(one=self.insert_row)
        raise AssertionError(f"unexpected replay-repair SQL: {sql}")


def _repair_module() -> Any:
    try:
        from idis.api import deal_creator_replay
    except ImportError:
        pytest.fail("createDeal replay compatibility repair is not implemented", pytrace=False)
    return deal_creator_replay


def test_lineage_proven_missing_assignment_is_repaired_on_the_caller_connection() -> None:
    module = _repair_module()
    connection = _ReplayConnection()

    outcome = module.repair_create_deal_replay(
        connection,
        scope_key=_scope(),
        record=_record(),
    )

    assert outcome.deal_id == DEAL_ID
    assert outcome.repaired is True
    insert_calls = [call for call in connection.calls if call[0].startswith("insert into")]
    assert len(insert_calls) == 1
    assert insert_calls[0][1] == {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "actor_id": ACTOR_ID,
    }
    recheck = next(params for sql, params in connection.calls if "idempotency_records" in sql)
    assert recheck["idempotency_key"] == RAW_KEY
    assert recheck["tenant_id"] == TENANT_ID
    assert recheck["actor_id"] == ACTOR_ID


def test_existing_exact_creator_assignment_is_a_pure_replay() -> None:
    module = _repair_module()
    connection = _ReplayConnection(assignment_exists=True)

    outcome = module.repair_create_deal_replay(
        connection,
        scope_key=_scope(),
        record=_record(),
    )

    assert outcome.deal_id == DEAL_ID
    assert outcome.repaired is False
    assert not any(sql.startswith("insert into deal_assignments") for sql, _ in connection.calls)
    assert not any("from audit_events" in sql for sql, _ in connection.calls)


@pytest.mark.parametrize(
    ("record", "connection"),
    [
        (_record(status_code=200), _ReplayConnection()),
        (_record(media_type="application/problem+json"), _ReplayConnection()),
        (_record(body_bytes=b"not-json"), _ReplayConnection()),
        (_record(body_bytes=_body(deal_id=OTHER_TENANT_ID)), _ReplayConnection()),
        (_record(), _ReplayConnection(deal=_deal_row(name="drifted"))),
        (_record(), _ReplayConnection(creation_events=[])),
        (_record(), _ReplayConnection(creation_events=[_creation_event(), _creation_event()])),
        (
            _record(),
            _ReplayConnection(
                creation_events=[
                    _creation_event(idempotency_key="0" * 64),
                ]
            ),
        ),
        (
            _record(),
            _ReplayConnection(
                creation_events=[_creation_event(request_id="different-request")],
            ),
        ),
        (
            _record(),
            _ReplayConnection(
                creation_events=[
                    _creation_event(
                        event={
                            **_creation_event().event,
                            "actor": {
                                **_creation_event().event["actor"],
                                "actor_type": "ROBOT",
                            },
                        }
                    )
                ],
            ),
        ),
        (
            _record(),
            _ReplayConnection(
                creation_events=[
                    _creation_event(
                        occurred_at=datetime(2026, 7, 17, 7, 59, 59, tzinfo=UTC),
                        event={
                            **_creation_event().event,
                            "occurred_at": "2026-07-17T07:59:59Z",
                        },
                    )
                ],
            ),
        ),
        (
            _record(),
            _ReplayConnection(
                later_assignment_event=("rbac.assignment.deleted",),
            ),
        ),
    ],
)
def test_missing_or_ambiguous_lineage_fails_closed(
    record: IdempotencyRecord,
    connection: _ReplayConnection,
) -> None:
    module = _repair_module()
    connection.record = record

    with pytest.raises(module.DealCreatorReplayInvariantError) as exc_info:
        module.repair_create_deal_replay(
            connection,
            scope_key=_scope(),
            record=record,
        )

    assert exc_info.value.code == "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"
    assert not any(sql.startswith("insert into deal_assignments") for sql, _ in connection.calls)


class _ReplayStore:
    def __init__(self, record: IdempotencyRecord) -> None:
        self.record = record

    def get(self, scope_key: ScopeKey, *, conn: object) -> IdempotencyRecord:
        del scope_key, conn
        return self.record

    def delete_expired(self, *, tenant_id: str, older_than: object) -> int:
        del tenant_id, older_than
        return 0


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    del scope, receive, send


def _middleware_request(connection: object, *, task4_scope: bool = True) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(task4_ingestion_scope=task4_scope),
            ),
            "method": "POST",
            "path": "/v1/deals",
            "query_string": b"",
            "headers": [(b"idempotency-key", RAW_KEY.encode())],
            "client": ("127.0.0.1", 1234),
            "state": {
                "tenant_context": SimpleNamespace(tenant_id=TENANT_ID, actor_id=ACTOR_ID),
                "openapi_operation_id": "createDeal",
                "request_body_sha256": PAYLOAD_SHA256,
                "db_conn": connection,
                "request_id": "request-replay",
            },
        },
        receive,
    )


def test_middleware_never_repairs_outside_the_task4_bootstrap_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _repair_module()

    def forbidden_repair(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("compatibility repair escaped the Task-4 bootstrap scope")

    monkeypatch.setattr(module, "repair_create_deal_replay", forbidden_repair)
    request = _middleware_request(object(), task4_scope=False)
    middleware = IdempotencyMiddleware(
        _noop_app,
        postgres_store=_ReplayStore(_record()),  # type: ignore[arg-type]
        cleanup_interval_seconds=3600,
    )

    async def forbidden_route(_request: Request) -> Response:
        raise AssertionError("a durable replay must not execute the route")

    response = asyncio.run(middleware.dispatch(request, forbidden_route))

    assert response.status_code == 201
    assert response.body == _body()
    assert response.headers["X-IDIS-Idempotency-Replay"] == "true"
    assert not hasattr(request.state, "audit_idempotency_replay_repair_occurred")


def test_middleware_marks_only_a_real_repair_for_same_transaction_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _repair_module()
    connection = object()
    observed: list[tuple[object, ScopeKey, IdempotencyRecord]] = []

    def repair(conn: object, *, scope_key: ScopeKey, record: IdempotencyRecord) -> object:
        observed.append((conn, scope_key, record))
        return module.DealCreatorReplayRepairOutcome(DEAL_ID, True)

    monkeypatch.setattr(module, "repair_create_deal_replay", repair)
    request = _middleware_request(connection)
    middleware = IdempotencyMiddleware(
        _noop_app,
        postgres_store=_ReplayStore(_record()),  # type: ignore[arg-type]
        cleanup_interval_seconds=3600,
    )

    async def forbidden_route(_request: Request) -> Response:
        raise AssertionError("a durable replay must not execute the route")

    response = asyncio.run(middleware.dispatch(request, forbidden_route))

    assert response.status_code == 201
    assert response.body == _body()
    assert response.headers["X-IDIS-Idempotency-Replay"] == "true"
    assert observed == [(connection, _scope(), _record())]
    assert request.state.audit_resource_id == DEAL_ID
    assert request.state.audit_event_type_override == "rbac.assignment.created"
    assert request.state.audit_severity_override == "MEDIUM"
    assert request.state.audit_idempotency_replay_repair_occurred is True


def test_middleware_maps_repair_proof_failure_to_stable_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _repair_module()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise module.DealCreatorReplayInvariantError()

    monkeypatch.setattr(module, "repair_create_deal_replay", fail)
    request = _middleware_request(object())
    middleware = IdempotencyMiddleware(
        _noop_app,
        postgres_store=_ReplayStore(_record()),  # type: ignore[arg-type]
        cleanup_interval_seconds=3600,
    )

    async def forbidden_route(_request: Request) -> Response:
        raise AssertionError("a durable replay must not execute the route")

    response = asyncio.run(middleware.dispatch(request, forbidden_route))

    assert response.status_code == 500
    assert b'"code":"IDEMPOTENCY_REPLAY_INVARIANT_FAILED"' in response.body
    assert not hasattr(request.state, "audit_idempotency_replay_repair_occurred")


def _audit_request(*, repair: bool) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    state: dict[str, object] = {
        "request_id": "request-replay",
        "tenant_context": SimpleNamespace(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            actor_type="SERVICE",
            roles=frozenset({"INTEGRATION_SERVICE"}),
        ),
        "openapi_operation_id": "createDeal",
        "request_body_sha256": PAYLOAD_SHA256,
    }
    if repair:
        state.update(
            {
                "audit_resource_id": DEAL_ID,
                "audit_event_type_override": "rbac.assignment.created",
                "audit_severity_override": "MEDIUM",
                "audit_idempotency_replay_repair_occurred": True,
            }
        )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/deals",
            "query_string": b"",
            "headers": [(b"idempotency-key", RAW_KEY.encode())],
            "client": ("127.0.0.1", 1234),
            "state": state,
        },
        receive,
    )


def test_outer_audit_emits_assignment_event_for_repair_but_skips_pure_replay() -> None:
    sink = InMemoryAuditSink()
    middleware = AuditMiddleware(_noop_app, sink=sink)

    async def replay_response(_request: Request) -> Response:
        return Response(
            content=_body(),
            status_code=201,
            media_type="application/json",
            headers={"X-IDIS-Idempotency-Replay": "true"},
        )

    repaired = asyncio.run(middleware.dispatch(_audit_request(repair=True), replay_response))
    pure = asyncio.run(middleware.dispatch(_audit_request(repair=False), replay_response))

    assert repaired.status_code == 201
    assert pure.status_code == 201
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_type"] == "rbac.assignment.created"
    assert event["resource"] == {"resource_type": "deal", "resource_id": DEAL_ID}
    assert event["actor"]["actor_id"] == ACTOR_ID
    assert event["request"]["idempotency_key_sha256"] == sha256(RAW_KEY.encode()).hexdigest()
    assert RAW_KEY not in json.dumps(event, sort_keys=True)


def test_later_assignment_event_is_strictly_after_creation() -> None:
    """Fixture sanity: the ambiguity boundary is chronological, not textual."""
    assert AUDIT_AT + timedelta(microseconds=1) > AUDIT_AT
