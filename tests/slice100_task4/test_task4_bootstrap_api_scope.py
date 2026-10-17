"""RED contract for the Task-4-only bootstrap API composition.

The expected inventory is deliberately duplicated here as a literal.  It must not
be derived from the production scope policy: a broadened production constant must
make this test fail until the plan is explicitly amended.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request, Security
from fastapi.middleware.asyncexitstack import AsyncExitStackMiddleware
from fastapi.responses import EventSourceResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.security import APIKeyHeader
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.routing import Route

from idis.api import main as api_main
from idis.audit.postgres_sink import PostgresAuditSink
from idis.rate_limit.limiter import RateLimitConfig, TenantRateLimiter

_EXPECTED_TASK4_HTTP_ROUTES = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/v1/strict-readiness"),
        ("POST", "/v1/deals"),
        ("GET", "/v1/deals/{deal_id}"),
        ("POST", "/v1/deals/{deal_id}/documents/upload"),
        ("GET", "/v1/deals/{deal_id}/documents/{document_id}"),
        ("POST", "/v1/deals/{deal_id}/data-room-packages"),
        ("GET", "/v1/deals/{deal_id}/data-room-packages/{package_id}"),
        ("GET", "/v1/documents/{doc_id}/content-integrity"),
        ("GET", "/v1/documents/{doc_id}/spans"),
        ("GET", "/v1/documents/{doc_id}/content"),
        ("GET", "/v1/documents/{doc_id}"),
        ("DELETE", "/v1/documents/{doc_id}"),
    }
)


class _NoopPostgresAuditSink(PostgresAuditSink):
    """Type-correct durable sink seam without opening a database connection."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def emit_in_tx(self, _connection: object, event: dict[str, Any]) -> None:
        self.emit(event)


def _task4_bootstrap_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Compose the app at the reviewed boundary while keeping the RED test offline."""
    profile: Any = SimpleNamespace(
        command="api",
        generation="bootstrap-v1",
        process_role="api",
        base_profile_hmac="a" * 64,
        values={
            "IDIS_DATABASE_URL": "postgresql+psycopg2://offline.invalid/idis",
            "IDIS_REQUIRE_FULL_LIVE": "1",
            "IDIS_RUNTIME_PROFILE_GENERATION": "bootstrap-v1",
            "IDIS_REQUIRE_PROMOTED_PROMPTS": "0",
            "IDIS_PROCESS_ROLE": "api",
            "IDIS_SERVICE_REGION": "me-south-1",
        },
    )
    monkeypatch.setattr(
        api_main,
        "require_runtime_profile",
        lambda reviewed, *, command: reviewed,
    )
    monkeypatch.setattr(api_main, "validate_api_key_registry_config", lambda **_kwargs: None)
    monkeypatch.setattr(api_main, "require_strict_process_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_main, "configure_tracing", lambda: None)
    monkeypatch.setattr(api_main, "instrument_httpx", lambda: None)
    monkeypatch.setattr(api_main, "instrument_fastapi", lambda _app: None)

    sink = _NoopPostgresAuditSink()
    limiter = TenantRateLimiter(
        RateLimitConfig(user_rpm=600, integration_rpm=1200, burst_multiplier=2)
    )
    return api_main.create_app(
        audit_sink=sink,
        postgres_audit_sink=sink,
        rate_limiter=limiter,
        ingestion_service=object(),
        strict_profile=profile,
    )


def _http_route_inventory(app: Any) -> frozenset[tuple[str, str]]:
    inventory: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not isinstance(path, str) or not methods:
            continue
        inventory.update((str(method).upper(), path) for method in methods)
    return frozenset(inventory)


def test_task4_bootstrap_api_exposes_exactly_the_literal_thirteen_route_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _task4_bootstrap_app(monkeypatch)

    observed = _http_route_inventory(app)

    assert observed == _EXPECTED_TASK4_HTTP_ROUTES, (
        f"missing={sorted(_EXPECTED_TASK4_HTTP_ROUTES - observed)!r}; "
        f"extra={sorted(observed - _EXPECTED_TASK4_HTTP_ROUTES)!r}"
    )


def test_task4_bootstrap_api_starts_and_stops_no_background_lifecycle_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _task4_bootstrap_app(monkeypatch)
    events: list[str] = []

    async def record(name: str, *_args: object, **_kwargs: object) -> None:
        events.append(name)

    from idis.persistence import db
    from idis.services.compliance import janitor

    monkeypatch.setattr(db, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(api_main, "start_worker", lambda: record("pipeline.start"))
    monkeypatch.setattr(api_main, "stop_worker", lambda: record("pipeline.stop"))
    monkeypatch.setattr(
        api_main,
        "start_webhook_dispatcher_worker",
        lambda: record("webhook.start"),
    )
    monkeypatch.setattr(
        api_main,
        "stop_webhook_dispatcher_worker",
        lambda: record("webhook.stop"),
    )
    monkeypatch.setattr(
        janitor,
        "start_compliance_janitor_worker",
        lambda **_kwargs: record("janitor.start"),
    )
    monkeypatch.setattr(
        janitor,
        "stop_compliance_janitor_worker",
        lambda: record("janitor.stop"),
    )

    async def exercise_lifecycle() -> None:
        for callback in app.router.on_startup:
            await callback()
        for callback in reversed(app.router.on_shutdown):
            await callback()

    asyncio.run(exercise_lifecycle())

    assert events == []


def test_task4_bootstrap_rejects_full_run_route_before_contract_or_auth_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _task4_bootstrap_app(monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/deals/11111111-1111-4111-8111-111111111111/runs",
            json={},
        )

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Not found"}


_EXCLUDED_CALLS = (
    ("POST", "/v1/deals/example/runs", 404),
    ("GET", "/metrics", 404),
    ("POST", "/v1/methodology-versions", 404),
    ("POST", "/v1/enrichment/providers", 404),
    ("GET", "/docs", 404),
    ("GET", "/redoc", 404),
    ("GET", "/openapi.json", 404),
    ("GET", "/v1/deals", 405),
    ("PATCH", "/health", 405),
)


def _recording_leaf(calls: list[str], marker: str) -> Callable[..., Any]:
    async def endpoint(request: Request) -> dict[str, Any]:
        calls.append(marker)
        return {"operation": marker, "parameters": request.path_params}

    return endpoint


def _fixture_scope_router(calls: list[str], *, omit: tuple[str, str] | None = None) -> APIRouter:
    router = APIRouter()
    for method, path in sorted(_EXPECTED_TASK4_HTTP_ROUTES):
        if (method, path) != omit:
            router.add_api_route(path, _recording_leaf(calls, f"{method} {path}"), methods=[method])
    return router


def _fixture_scope_app(calls: list[str]) -> FastAPI:
    app = FastAPI()
    app.include_router(_fixture_scope_router(calls))
    return app


def test_real_factory_router_has_no_residual_excluded_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise actual factory routing without allowing middleware to hide a leak."""
    calls: list[str] = []
    excluded_dependencies: list[str] = []
    fixture = _fixture_scope_router(calls)

    def excluded_dependency() -> None:
        excluded_dependencies.append("called")

    for method, path, status in _EXCLUDED_CALLS:
        if status == 404 and path not in {"/docs", "/redoc", "/openapi.json"}:
            fixture.add_api_route(
                path,
                _recording_leaf(calls, "EXCLUDED"),
                methods=[method],
                dependencies=[Depends(excluded_dependency)],
            )
    # Only leaf handlers are test-owned. The real factory includes every other
    # production router, restricts the surface, and keeps its real lifecycle.
    monkeypatch.setattr(api_main, "health_router", fixture)
    for name in (
        "readiness_router",
        "deals_router",
        "documents_router",
        "data_room_packages_router",
    ):
        monkeypatch.setattr(api_main, name, APIRouter())
    app = _task4_bootstrap_app(monkeypatch)
    for warm_schema in (False, True):
        if warm_schema:
            app.openapi()
        # Only FastAPI's required resource stack, never the product middleware.
        with TestClient(AsyncExitStackMiddleware(app.router)) as client:
            for method, path in sorted(_EXPECTED_TASK4_HTTP_ROUTES):
                concrete = path
                for parameter in ("deal_id", "document_id", "package_id", "doc_id"):
                    concrete = concrete.replace("{" + parameter + "}", "example")
                calls.clear()
                response = client.request(method, concrete)
                assert response.status_code == 200
                assert response.json()["operation"] == f"{method} {path}"
                assert calls == [f"{method} {path}"]
            for method, path, status in _EXCLUDED_CALLS:
                calls.clear()
                response = client.request(method, path)
                assert response.status_code == status
                assert calls == []
                assert excluded_dependencies == []


class _VisiblePayload(BaseModel):
    value: str
    optional: str | None = None


class _ContextJSONResponse(JSONResponse):
    def __init__(self, content: Any, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self.headers["X-Response-Context"] = "preserved"


class _WrappedRoute(APIRoute):
    def get_route_handler(self) -> Callable[..., Any]:
        original = super().get_route_handler()

        async def wrapped(request: Request) -> Any:
            response = await original(request)
            response.headers["X-Route-Wrapper"] = "preserved"
            return response

        return wrapped


def test_nested_include_context_dispatch_schema_security_and_custom_route_are_conserved() -> None:
    events: list[str] = []

    def dependency(name: str) -> Callable[[], None]:
        def record() -> None:
            events.append(name)

        return record

    app = FastAPI(dependencies=[Depends(dependency("app"))])
    app.include_router(_fixture_scope_router([], omit=("POST", "/v1/deals")))
    outer = APIRouter(prefix="/v1", tags=["outer"], dependencies=[Depends(dependency("outer"))])
    inner = APIRouter(
        prefix="/deals",
        tags=["inner"],
        dependencies=[Depends(dependency("inner"))],
        route_class=_WrappedRoute,
        default_response_class=_ContextJSONResponse,
    )
    key_header = APIKeyHeader(name="X-Scope-Key")

    async def create(payload: _VisiblePayload, key: str = Security(key_header)) -> dict[str, Any]:
        return {"value": payload.value + ":" + key, "optional": None, "private": "filtered"}

    callback = APIRoute("/callback", _recording_leaf([], "callback"), methods=["POST"])
    inner.add_api_route(
        "",
        create,
        methods=["POST"],
        status_code=201,
        response_model=_VisiblePayload,
        response_model_exclude_none=True,
        tags=["leaf"],
        summary="Nested creation",
        description="Effective nested metadata",
        response_description="Created fixture",
        responses={409: {"description": "leaf conflict"}},
        operation_id="nestedCreateDeal",
        dependencies=[Depends(dependency("leaf"))],
        callbacks=[callback],
        openapi_extra={"x-scope-fixture": True},
        strict_content_type=False,
    )
    outer.include_router(inner, responses={418: {"description": "outer response"}})
    app.include_router(
        outer, tags=["include"], responses={429: {"description": "include response"}}
    )
    source_routes = tuple(inner.routes)
    source_route = source_routes[0]
    assert isinstance(source_route, APIRoute)
    source_tags = list(source_route.tags)
    before_context = next(x for x in iter_route_contexts(app.routes) if x.path == "/v1/deals")
    before_ids = (before_context.operation_id, before_context.unique_id)
    before = app.openapi()
    request: dict[str, Any] = {"content": b'{"value":"body"}', "headers": {"X-Scope-Key": "local"}}
    with TestClient(app) as client:
        original_response = client.post("/v1/deals", **request)
    original_events = events.copy()
    events.clear()

    api_main._restrict_task4_ingestion_routes(app)

    assert app.openapi_schema is None
    assert tuple(inner.routes) == source_routes
    assert source_route.tags == source_tags
    assert _http_route_inventory(app) == _EXPECTED_TASK4_HTTP_ROUTES
    after_context = next(x for x in iter_route_contexts(app.routes) if x.path == "/v1/deals")
    assert (after_context.operation_id, after_context.unique_id) == before_ids
    assert isinstance(after_context.original_route, _WrappedRoute)
    assert after_context.original_route is not before_context.original_route
    after = app.openapi()
    for method, path in _EXPECTED_TASK4_HTTP_ROUTES:
        assert after["paths"][path][method.lower()] == before["paths"][path][method.lower()]
    assert after["components"] == before["components"]
    with TestClient(app) as client:
        response = client.post("/v1/deals", **request)
        assert client.post("/v1/deals", json={"value": "missing-security"}).status_code == 401
    assert response.status_code == original_response.status_code == 201
    assert response.json() == original_response.json() == {"value": "body:local"}
    assert response.headers["X-Route-Wrapper"] == "preserved"
    assert response.headers["X-Response-Context"] == "preserved"
    assert events[:4] == original_events == ["app", "outer", "inner", "leaf"]


@pytest.mark.parametrize("construction_order", ["control-first", "bootstrap-first"])
def test_shared_router_and_dependency_overrides_remain_app_local(construction_order: str) -> None:
    calls: list[str] = []

    def identity() -> str:
        return "unoverridden"

    shared = _fixture_scope_router(calls, omit=("GET", "/health"))

    async def health(value: str = Depends(identity)) -> dict[str, str]:
        return {"identity": value}

    shared.add_api_route("/health", health, methods=["GET"])
    shared.add_api_route("/excluded-control", _recording_leaf(calls, "control"), methods=["GET"])
    source_routes = tuple(shared.routes)

    def build(value: str, restrict: bool) -> FastAPI:
        app = FastAPI()
        app.dependency_overrides[identity] = lambda: value
        app.include_router(shared)
        if restrict:
            api_main._restrict_task4_ingestion_routes(app)
        return app

    if construction_order == "control-first":
        control, first = build("control", False), build("first", True)
    else:
        first, control = build("first", True), build("control", False)
    second = build("second", True)
    for app, expected in ((first, "first"), (second, "second"), (control, "control")):
        app.openapi()
        with TestClient(AsyncExitStackMiddleware(app.router)) as client:
            assert client.get("/health").json() == {"identity": expected}
            assert client.get("/excluded-control").status_code == (200 if app is control else 404)
    assert tuple(shared.routes) == source_routes
    assert len(first.routes) == len(second.routes) == 13


@pytest.mark.parametrize("kind", ["missing", "duplicate", "mixed-method", "non-api-route"])
def test_invalid_inventory_fails_before_mutating_app(kind: str) -> None:
    app = FastAPI()
    omit = ("POST", "/v1/deals")
    app.router.routes.extend(_fixture_scope_router([], omit=omit).routes)
    leaf = _recording_leaf([], "invalid")
    if kind == "duplicate":
        app.add_api_route(omit[1], leaf, methods=[omit[0]])
        app.add_api_route(omit[1], leaf, methods=[omit[0]])
    elif kind == "mixed-method":
        app.add_api_route(omit[1], leaf, methods=["POST", "PATCH"])
    elif kind == "non-api-route":
        app.router.routes.append(Route(omit[1], leaf, methods=["POST"]))
    original = tuple(app.routes)
    if kind in {"duplicate", "mixed-method"}:
        with pytest.warns(UserWarning, match="Duplicate Operation ID"):
            schema = app.openapi()
    else:
        schema = app.openapi()

    with pytest.raises(RuntimeError, match="^STRICT_FULL_LIVE_BLOCKED$"):
        api_main._restrict_task4_ingestion_routes(app)

    assert tuple(app.routes) == original
    assert app.openapi_schema is schema


@pytest.mark.parametrize("response_class", [None, EventSourceResponse], ids=["jsonl", "sse"])
def test_generator_stream_metadata_fails_closed_before_mutation(response_class: Any) -> None:
    app = FastAPI()
    app.router.routes.extend(_fixture_scope_router([], omit=("POST", "/v1/deals")).routes)

    async def stream() -> AsyncIterator[dict[str, str]]:
        yield {"value": "must-not-be-reconstructed"}

    kwargs = {} if response_class is None else {"response_class": response_class}
    app.add_api_route("/v1/deals", stream, methods=["POST"], **kwargs)
    context = next(x for x in iter_route_contexts(app.routes) if x.path == "/v1/deals")
    assert context.stream_item_type is not None
    assert context.is_json_stream or context.is_sse_stream
    original = tuple(app.routes)
    schema = app.openapi()
    with pytest.raises(RuntimeError, match="^STRICT_FULL_LIVE_BLOCKED$"):
        api_main._restrict_task4_ingestion_routes(app)
    assert tuple(app.routes) == original
    assert app.openapi_schema is schema


def test_ordinary_streaming_response_bytes_and_headers_are_conserved() -> None:
    app = FastAPI()
    app.include_router(_fixture_scope_router([], omit=("GET", "/v1/documents/{doc_id}/content")))
    content = APIRouter()

    async def stream(doc_id: str) -> StreamingResponse:
        return StreamingResponse(
            iter([b"exact-", doc_id.encode()]),
            media_type="application/octet-stream",
            headers={"X-Stream-Fixture": "preserved"},
        )

    content.add_api_route(
        "/v1/documents/{doc_id}/content", stream, methods=["GET"], response_class=StreamingResponse
    )
    app.include_router(content)
    api_main._restrict_task4_ingestion_routes(app)
    with TestClient(app) as client:
        response = client.get("/v1/documents/example/content")
    assert response.status_code == 200
    assert response.content == b"exact-example"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["X-Stream-Fixture"] == "preserved"


def test_bootstrap_real_handlers_keep_auth_tenant_path_and_durable_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from idis.api.auth import IDIS_API_KEYS_ENV
    from idis.api.routes import deals, documents
    from idis.storage.compliant_store import ComplianceEnforcedStore
    from idis.storage.filesystem_store import FilesystemObjectStore

    tenant = "11111111-1111-4111-8111-111111111111"
    deal_id = "22222222-2222-4222-8222-222222222222"
    doc_id = "33333333-3333-4333-8333-333333333333"
    key = "route-test-owned-local-key"
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                key: {
                    "tenant_id": tenant,
                    "actor_id": "route-reader",
                    "name": "Route Reader",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    seen: list[tuple[str, str]] = []

    def repository(_request: Request, tenant_id: str) -> Any:
        def get(identifier: str) -> dict[str, str]:
            seen.append((tenant_id, identifier))
            return {
                "deal_id": identifier,
                "name": "Fixture",
                "company_name": "Fictional",
                "status": "NEW",
                "created_at": "2026-10-17T00:00:00Z",
            }

        return SimpleNamespace(get=get)

    monkeypatch.setattr(deals, "_get_repository", repository)
    data = b"test-owned durable stream bytes"
    store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    storage_key = "route-fixture/content"
    store.put(tenant, storage_key, data, content_type="application/octet-stream")
    artifact = {
        "uri": storage_key,
        "sha256": hashlib.sha256(data).hexdigest(),
        "metadata": {"size_bytes": len(data)},
    }
    monkeypatch.setattr(
        documents, "_load_download_artifact", lambda *_args, **_kwargs: (artifact, True)
    )
    app = _task4_bootstrap_app(monkeypatch)
    # Existing offline composition seam, not a production-admitted profile.
    # Real auth deliberately reads profile values rather than ambient keys.
    app.state.strict_runtime_profile.values[IDIS_API_KEYS_ENV] = json.dumps(
        {
            key: {
                "tenant_id": tenant,
                "actor_id": "route-reader",
                "name": "Route Reader",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            }
        }
    )
    app.state.ingestion_service = SimpleNamespace(
        _compliant_store=ComplianceEnforcedStore(inner_store=store)
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get(f"/v1/deals/{deal_id}").status_code == 401
        assert (
            client.get(f"/v1/deals/{deal_id}", headers={"X-IDIS-API-Key": "invalid"}).status_code
            == 401
        )
        response = client.get(f"/v1/deals/{deal_id}", headers={"X-IDIS-API-Key": key})
        assert response.status_code == 200
        assert response.json()["deal_id"] == deal_id
        stream = client.get(f"/v1/documents/{doc_id}/content", headers={"X-IDIS-API-Key": key})
        assert stream.status_code == 200
        assert stream.content == data
        assert stream.headers["x-idis-content-sha256"] == hashlib.sha256(data).hexdigest()
        assert stream.headers["x-idis-stream-contract"] == "durable-object-stream-v1"
        for method, path, _status in _EXCLUDED_CALLS:
            denied = client.request(method, path, headers={"X-IDIS-API-Key": key})
            assert denied.status_code == 404
            assert denied.json() == {"code": "NOT_FOUND", "message": "Not found"}
    assert seen == [(tenant, deal_id)]
