"""Audit resource-id coverage tests for mutating routes (Sprint 1 Wave 1, Task 3).

Two levels of coverage:

1. `TestWebhookCreateAuditResourceId` — focused end-to-end proof that
   POST /v1/webhooks no longer trips the audit middleware's fail-closed
   check for a missing `request.state.audit_resource_id`.

2. `TestOperationIdAuditResourceIdSweep` — parametric static sweep across
   every operation in OPERATION_ID_TO_EVENT_TYPE. For each entry we locate
   the corresponding FastAPI route via the OpenAPI spec and assert the
   route handler body assigns `audit_resource_id` — or that the operation
   is explicitly documented as an OpenAPI-only orphan (no handler mounted).
"""

from __future__ import annotations

import inspect
import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.middleware.audit import OPERATION_ID_TO_EVENT_TYPE
from idis.api.openapi_loader import load_openapi_spec
from idis.audit.sink import JsonlFileAuditSink

_PATH_PARAM_RE = re.compile(r"\{[^/}]+\}")


def _normalize_path(path: str) -> str:
    """Normalize path parameter names so OpenAPI {dealId} matches FastAPI {deal_id}."""
    return _PATH_PARAM_RE.sub("{_}", path)


# operationId values that appear in OPERATION_ID_TO_EVENT_TYPE AND in the
# OpenAPI spec but deliberately have no handler in src/idis/api/routes/.
# A request to these paths falls through FastAPI's default 404/405 before
# reaching audit middleware, so no audit_resource_id is possible or required.
# Any future PR that wires these up MUST also set audit_resource_id.
DOCUMENTED_ORPHAN_OPERATIONS: frozenset[str] = frozenset(
    {
        "updateDeal",  # PATCH /v1/deals/{dealId} — spec only
        "runCalc",  # POST /v1/deals/{dealId}/calcs — spec only
    }
)


def _api_keys_json(tenant_id: str, actor_id: str, roles: list[str]) -> str:
    return json.dumps(
        {
            "test-audit-sweep-key": {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": "Sweep Tenant",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


def _read_one_event(path: Path) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one audit event; got {len(lines)}: {lines!r}"
    return json.loads(lines[0])


class TestWebhookCreateAuditResourceId:
    """End-to-end proof for the original bug: createWebhook must audit cleanly."""

    def test_post_webhook_success_emits_matching_audit_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit_path = tmp_path / "audit.jsonl"
        tenant_id = str(uuid.uuid4())
        actor_id = f"actor-{uuid.uuid4().hex[:12]}"
        # createWebhook requires ADMIN per policy.py.
        monkeypatch.setenv(
            "IDIS_API_KEYS_JSON", _api_keys_json(tenant_id, actor_id, ["ADMIN"])
        )
        monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_path))

        sink = JsonlFileAuditSink(str(audit_path))
        app = create_app(audit_sink=sink, service_region="us-east-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/v1/webhooks",
            headers={
                "X-IDIS-API-Key": "test-audit-sweep-key",
                "Content-Type": "application/json",
            },
            content=json.dumps(
                {
                    "url": "https://example.test/hook",
                    "events": ["deal.created"],
                }
            ),
        )

        # Business logic must succeed; old bug turned this into 500 AUDIT_EMIT_FAILED.
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["webhook_id"], "response must carry a webhook_id"

        event = _read_one_event(audit_path)
        assert event["event_type"] == "webhook.created"
        assert event["resource"]["resource_type"] == "webhook"
        assert event["resource"]["resource_id"] == body["webhook_id"]
        # Sanity: audit must not be the generic fail-closed "unknown" marker.
        assert event["resource"]["resource_id"] != "unknown"


def _build_operation_index() -> dict[str, tuple[str, str]]:
    """Return {operation_id: (http_method_upper, path_template)} from the spec."""
    spec = load_openapi_spec()
    index: dict[str, tuple[str, str]] = {}
    for path_template, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if op_id:
                index[op_id] = (method.upper(), path_template)
    return index


def _find_api_route(
    app_routes: list, method: str, path: str
) -> APIRoute | None:
    """Find the FastAPI route matching (method, path) after normalizing
    path-parameter naming (OpenAPI uses camelCase, FastAPI uses snake_case).
    """
    target = _normalize_path(path)
    for route in app_routes:
        if not isinstance(route, APIRoute):
            continue
        if method not in (route.methods or set()):
            continue
        if _normalize_path(route.path) == target:
            return route
    return None


class TestOperationIdAuditResourceIdSweep:
    """Every mutating operation_id must route to a handler that sets
    audit_resource_id, or be explicitly documented as an orphan.
    """

    @pytest.fixture(scope="class")
    def app_routes(self) -> list:
        app = create_app(service_region="us-east-1")
        return list(app.router.routes)

    @pytest.fixture(scope="class")
    def op_index(self) -> dict[str, tuple[str, str]]:
        return _build_operation_index()

    @pytest.mark.parametrize("operation_id", sorted(OPERATION_ID_TO_EVENT_TYPE.keys()))
    def test_handler_sets_audit_resource_id(
        self,
        operation_id: str,
        app_routes: list,
        op_index: dict[str, tuple[str, str]],
    ) -> None:
        if operation_id in DOCUMENTED_ORPHAN_OPERATIONS:
            # Spec-only entries: no handler should exist. Document that
            # invariant explicitly so a newly-added handler forces re-review.
            method, path = op_index.get(operation_id, ("", ""))
            route = _find_api_route(app_routes, method, path) if path else None
            assert route is None, (
                f"operation_id={operation_id!r} is listed as a documented orphan "
                f"but a handler now exists at {method} {path}. "
                f"Remove it from DOCUMENTED_ORPHAN_OPERATIONS and ensure the "
                f"handler sets request.state.audit_resource_id."
            )
            return

        method_path = op_index.get(operation_id)
        assert method_path is not None, (
            f"operation_id={operation_id!r} is in OPERATION_ID_TO_EVENT_TYPE "
            f"but is missing from the OpenAPI spec"
        )
        method, path = method_path

        route = _find_api_route(app_routes, method, path)
        assert route is not None, (
            f"operation_id={operation_id!r} ({method} {path}) has no FastAPI "
            f"route handler. Either mount it or list it in "
            f"DOCUMENTED_ORPHAN_OPERATIONS with rationale."
        )

        source = inspect.getsource(route.endpoint)
        assert "audit_resource_id" in source, (
            f"operation_id={operation_id!r} ({method} {path}) handler "
            f"{route.endpoint.__qualname__} never assigns "
            f"request.state.audit_resource_id. The audit middleware will "
            f"fail-closed with AUDIT_EMIT_FAILED on any successful mutation."
        )
