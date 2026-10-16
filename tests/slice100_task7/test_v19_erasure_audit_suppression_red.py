"""RED contract for route-scoped replacement of the generic erasure audit event."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from idis.api.auth import TenantContext
from idis.api.middleware import audit as audit_middleware
from idis.compliance import task7_erasure

_REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_ATTEMPT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_SUCCEEDED_EVENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_DEAL_ID = "22222222-2222-4222-8222-222222222222"
_ACTOR_ID = "reviewed-admin"
_EXECUTED_AT = "2026-07-24T10:00:00Z"
_CLIENT_IP = "203.0.113.40"
_USER_AGENT = "actual-v19-client"


def _request(
    *,
    operation_id: str = "executeErasureRequest",
    method: str = "POST",
    path: str = f"/v1/erasure-requests/{_REQUEST_ID}/execute",
    proof: object | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        openapi_operation_id=operation_id,
        task7_erasure_terminal_lineage_proof=proof,
    )
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        state=state,
    )


def _response(status_code: int = 200) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code)


def _replacement_event(**overrides: Any) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": _SUCCEEDED_EVENT_ID,
        "occurred_at": _EXECUTED_AT,
        "tenant_id": _TENANT_ID,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": _ACTOR_ID,
            "roles": ["ADMIN"],
            "ip": _CLIENT_IP,
            "user_agent": _USER_AGENT,
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
                "counts": {"object_rows_reconciled": 2, "deals": 1},
            },
            "hashes": [f"object_target_set_sha256:{'e' * 64}"],
            "refs": [f"deal_id:{_DEAL_ID}"],
        },
    }
    event.update(overrides)
    return event


def _generic_event(**overrides: Any) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "occurred_at": "2026-07-24T10:00:01Z",
        "tenant_id": _TENANT_ID,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": _ACTOR_ID,
            "roles": ["ADMIN"],
            "ip": _CLIENT_IP,
            "user_agent": _USER_AGENT,
        },
        "request": {
            "request_id": "http-correlation-id",
            "method": "POST",
            "path": f"/v1/erasure-requests/{_REQUEST_ID}/execute",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": _REQUEST_ID,
        },
        "event_type": "erasure.executed",
        "severity": "CRITICAL",
        "payload": {"hashes": [], "refs": []},
    }
    event.update(overrides)
    return event


def _proof(**overrides: Any) -> dict[str, object]:
    proof: dict[str, object] = {
        "schema_version": "task7-erasure-terminal-lineage-proof-v1",
        "request_id": _REQUEST_ID,
        "attempt_id": _ATTEMPT_ID,
        "event_id": _SUCCEEDED_EVENT_ID,
        "event_type": "task7.erasure.succeeded",
        "outcome": "SUCCEEDED",
        "durable": True,
        "replacement_event": _replacement_event(),
    }
    proof.update(overrides)
    return proof


def _suppression_helper() -> Any:
    helper = getattr(
        audit_middleware,
        "_should_suppress_generic_erasure_executed",
        None,
    )
    assert callable(helper), (
        "v19 requires one explicit route-scoped suppression predicate; removing "
        "executeErasureRequest from the mutation map is forbidden"
    )
    return helper


def test_generic_execute_event_mapping_remains_present_until_exact_route_proof() -> None:
    assert audit_middleware.OPERATION_ID_TO_EVENT_TYPE["executeErasureRequest"] == (
        "erasure.executed",
        "CRITICAL",
        "erasure_request",
    )
    helper = _suppression_helper()
    assert helper(_request(proof=_proof()), _response(), _generic_event()) is True


@pytest.mark.parametrize(
    ("candidate_request", "candidate_response"),
    [
        (_request(operation_id="createErasureRequest", proof=_proof()), _response()),
        (_request(method="PUT", proof=_proof()), _response()),
        (_request(path="/v1/compliance-exports", proof=_proof()), _response()),
        (
            _request(path=f"/v1/erasure-requests/{_REQUEST_ID}/execute/extra", proof=_proof()),
            _response(),
        ),
        (_request(proof=None), _response()),
        (_request(proof=_proof(durable=False)), _response()),
        (_request(proof=_proof(event_type="task7.erasure.failed")), _response()),
        (_request(proof=_proof(outcome="FAILED")), _response()),
        (_request(proof=_proof(request_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd")), _response()),
        (
            _request(
                proof=_proof(
                    replacement_event=_replacement_event(
                        actor={
                            "actor_type": "HUMAN",
                            "actor_id": "different-recovery-admin",
                            "roles": ["ADMIN"],
                            "ip": "internal",
                            "user_agent": "idis-compliance",
                        }
                    )
                )
            ),
            _response(),
        ),
        (_request(proof=_proof(extra="unknown")), _response()),
        (_request(proof=_proof()), _response(409)),
        (_request(proof=_proof()), _response(500)),
    ],
)
def test_suppression_rejects_every_nonexact_route_or_lineage_shape(
    candidate_request: SimpleNamespace,
    candidate_response: SimpleNamespace,
) -> None:
    assert _suppression_helper()(candidate_request, candidate_response, _generic_event()) is False


def test_task7_terminal_event_field_parity_is_strictly_richer_than_generic_event() -> None:
    parity = getattr(audit_middleware, "_task7_erasure_replacement_covers_generic_event", None)
    assert callable(parity), "v19 suppression requires field-level replacement-lineage proof"

    generic = _generic_event(
        resource={
            "resource_type": "erasure_request",
            "resource_id": _REQUEST_ID,
            "deal_id": _DEAL_ID,
        }
    )
    richer = _replacement_event()
    assert parity(generic, richer) is True

    for mutation in (
        {
            "actor": {
                "actor_type": "HUMAN",
                "actor_id": "other-admin",
                "roles": ["ADMIN"],
                "ip": _CLIENT_IP,
                "user_agent": _USER_AGENT,
            }
        },
        {
            "actor": {
                "actor_type": "HUMAN",
                "actor_id": _ACTOR_ID,
                "roles": ["ADMIN"],
                "ip": "198.51.100.27",
                "user_agent": _USER_AGENT,
            }
        },
        {
            "actor": {
                "actor_type": "HUMAN",
                "actor_id": _ACTOR_ID,
                "roles": ["ADMIN"],
                "ip": _CLIENT_IP,
                "user_agent": "different-client",
            }
        },
        {"tenant_id": "33333333-3333-4333-8333-333333333333"},
        {
            "resource": {
                "resource_type": "erasure_request",
                "resource_id": _REQUEST_ID,
                "deal_id": "44444444-4444-4444-8444-444444444444",
            }
        },
        {"occurred_at": "2026-07-24T10:00:02Z"},
        {
            "payload": {
                **richer["payload"],
                "safe": {**richer["payload"]["safe"], "outcome": "FAILED"},
            }
        },
    ):
        candidate = {**richer, **mutation}
        assert parity(generic, candidate) is False


@pytest.mark.parametrize(
    "generic",
    (
        _generic_event(
            request={
                **_generic_event()["request"],
                "idempotency_key_sha256": "f" * 64,
            }
        ),
        _generic_event(payload={"hashes": ["e" * 64], "refs": []}),
        _generic_event(payload={"hashes": [], "refs": ["request-body"]}),
        _generic_event(payload={"hashes": [], "refs": [], "extra": "unknown"}),
    ),
)
def test_suppression_rejects_generic_evidence_absent_from_task7_event(
    generic: dict[str, object],
) -> None:
    assert (
        audit_middleware._task7_erasure_replacement_covers_generic_event(
            generic,
            _replacement_event(),
        )
        is False
    )
    assert _suppression_helper()(_request(proof=_proof()), _response(), generic) is False


def test_actual_middleware_and_task7_builders_preserve_the_full_actor_projection() -> None:
    """A suppressed generic event loses no actor transport evidence."""

    tenant_id = "11111111-1111-4111-8111-111111111111"
    deal_id = "22222222-2222-4222-8222-222222222222"
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": f"/v1/erasure-requests/{_REQUEST_ID}/execute",
        "raw_path": f"/v1/erasure-requests/{_REQUEST_ID}/execute".encode(),
        "query_string": b"",
        "headers": [(b"user-agent", b"actual-v19-client")],
        "client": ("203.0.113.40", 443),
        "server": ("idis.invalid", 443),
    }
    request = Request(scope)
    request.state.request_id = "http-correlation-id-not-erasure-request-id"
    request.state.audit_resource_id = _REQUEST_ID
    request.state.tenant_context = TenantContext(
        tenant_id=tenant_id,
        actor_id="reviewed-service-admin",
        actor_type="SERVICE",
        name="Reviewed service administrator",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )
    generic = audit_middleware._build_audit_event(
        request=request,
        response=Response(status_code=200),
        event_type="erasure.executed",
        severity="CRITICAL",
        resource_type="erasure_request",
    )
    assert generic is not None
    middleware_time = datetime.fromisoformat(str(generic["occurred_at"]).replace("Z", "+00:00"))
    lineage = task7_erasure.derive_attempt_lineage(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=_REQUEST_ID,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    replacement = task7_erasure.build_erasure_audit_event(
        tenant_id=tenant_id,
        deal_id=deal_id,
        actor_id="reviewed-service-admin",
        actor_type="SERVICE",
        actor_roles=("ADMIN",),
        actor_ip=_CLIENT_IP,
        actor_user_agent=_USER_AGENT,
        occurred_at=middleware_time - timedelta(seconds=1),
        lineage=lineage,
        event_type="task7.erasure.succeeded",
        object_target_set_sha256="e" * 64,
        counts={"object_rows_reconciled": 2, "deals": 1},
    )

    # The HTTP correlation id and occurrence time retain their distinct meanings,
    # while every actor field that generic audit would have persisted is preserved.
    assert generic["request"]["request_id"] != replacement["request"]["request_id"]
    assert generic["actor"] == replacement["actor"]
    assert generic["occurred_at"] != replacement["occurred_at"]
    assert (
        audit_middleware._task7_erasure_replacement_covers_generic_event(
            generic,
            replacement,
        )
        is True
    )

    late_replacement = {
        **replacement,
        "occurred_at": (middleware_time + timedelta(seconds=1))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    assert (
        audit_middleware._task7_erasure_replacement_covers_generic_event(
            generic,
            late_replacement,
        )
        is False
    )
