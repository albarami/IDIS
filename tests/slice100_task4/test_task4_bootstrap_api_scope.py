"""RED contract for the Task-4-only bootstrap API composition.

The expected inventory is deliberately duplicated here as a literal.  It must not
be derived from the production scope policy: a broadened production constant must
make this test fail until the plan is explicitly amended.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
    profile = SimpleNamespace(
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
