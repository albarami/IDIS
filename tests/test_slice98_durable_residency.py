"""Slice98 Task 3 - Durable tenant data_region + residency source of truth (hermetic).

RED-first. Two concerns proven here without real Postgres:

A) The JWT/SSO ``data_region="default"`` fallback is REMOVED. A validated identity that carries no
   ``data_region`` claim must NOT be silently assigned "default"; residency denies it fail-closed
   (403 RESIDENCY_INVALID_TENANT_CONTEXT), and the old sentinel can no longer collide with a service
   region literally named "default".

B) Durable residency: when ``IDIS_ENABLE_DURABLE_RESIDENCY`` is on, the tenant's region is read from
   the durable ``tenants.data_region`` store (not the request claim); match -> allow,
   mismatch/NULL/DB-error -> 403 fail-closed. Flag off preserves the existing claim-based behavior.

The durable store is exercised through its in-memory twin + the app seam (no real DB); the Postgres
twin and the real read path are proven in ``test_slice98_durable_residency_postgres.py``. PYTHONPATH
is pinned to this worktree's src for every run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from idis.api.auth import TenantContext
from idis.api.auth_sso import SsoIdentity
from idis.api.main import create_app
from idis.compliance.residency import IDIS_SERVICE_REGION_ENV

_TENANT = "11111111-1111-1111-1111-111111111111"
_DURABLE_FLAG = "IDIS_ENABLE_DURABLE_RESIDENCY"


def _identity(data_region: str | None) -> SsoIdentity:
    return SsoIdentity(
        tenant_id=_TENANT,
        user_id="user-1",
        roles=frozenset({"ANALYST"}),
        name="JWT User",
        data_region=data_region,
    )


def _full_app_client(
    monkeypatch: pytest.MonkeyPatch, *, service_region: str, identity: SsoIdentity
) -> TestClient:
    """Full app with the JWT validation boundary mocked to a chosen identity (no JWT crypto)."""
    import idis.api.auth_sso as sso

    monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, service_region)
    monkeypatch.setattr(sso, "validate_jwt", lambda token: identity)
    return TestClient(create_app(service_region=service_region), raise_server_exceptions=False)


_BEARER = {"Authorization": "Bearer test.jwt.token"}


class TestJwtDefaultRegionFallbackRemoved:
    """Concern A: the 'default' data_region fallback in the JWT path is gone (fail-closed)."""

    def test_jwt_identity_without_region_yields_none_not_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A validated identity with no data_region claim yields a TenantContext with None."""
        import idis.api.auth_sso as sso
        from idis.api.auth import _extract_tenant_from_jwt

        monkeypatch.setattr(sso, "validate_jwt", lambda token: _identity(None))
        ctx = _extract_tenant_from_jwt("test.jwt.token")
        assert ctx.data_region is None

    def test_jwt_without_region_is_denied_invalid_not_defaulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No-region identity -> residency 403 RESIDENCY_INVALID_TENANT_CONTEXT (not a mismatch)."""
        client = _full_app_client(
            monkeypatch, service_region="me-south-1", identity=_identity(None)
        )
        resp = client.get("/v1/tenants/me", headers=_BEARER)
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "RESIDENCY_INVALID_TENANT_CONTEXT"

    def test_jwt_without_region_denied_even_when_service_region_named_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old sentinel would collide with a service region named 'default' and ALLOW a
        region-less identity. It must fail closed (403) regardless of the service region value."""
        client = _full_app_client(monkeypatch, service_region="default", identity=_identity(None))
        resp = client.get("/v1/tenants/me", headers=_BEARER)
        assert resp.status_code == 403, resp.text

    def test_jwt_with_matching_region_still_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: an identity that carries a matching region still authenticates and passes."""
        client = _full_app_client(
            monkeypatch, service_region="me-south-1", identity=_identity("me-south-1")
        )
        resp = client.get("/v1/tenants/me", headers=_BEARER)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data_region"] == "me-south-1"


@pytest.fixture
def _reset_region_store() -> Iterator[None]:
    """Isolate the module-global durable tenant-region store between tests."""
    from idis.compliance.tenant_region import reset_tenant_region_store

    reset_tenant_region_store()
    yield
    reset_tenant_region_store()


@pytest.mark.usefixtures("_reset_region_store")
class TestDurableTenantRegionStore:
    """Concern B: the durable tenant-region store twin + app seam."""

    def test_in_memory_get_returns_seeded_region(self) -> None:
        from idis.compliance.tenant_region import InMemoryTenantRegionStore

        store = InMemoryTenantRegionStore()
        store.set_region(_TENANT, "me-south-1")
        assert store.get_data_region(_TENANT) == "me-south-1"

    def test_in_memory_get_returns_none_for_unknown_tenant(self) -> None:
        from idis.compliance.tenant_region import InMemoryTenantRegionStore

        assert InMemoryTenantRegionStore().get_data_region(_TENANT) is None

    def test_seam_set_and_get_roundtrip(self) -> None:
        from idis.compliance.tenant_region import (
            InMemoryTenantRegionStore,
            get_tenant_region_store,
            set_tenant_region_store,
        )

        store = InMemoryTenantRegionStore()
        set_tenant_region_store(store)
        assert get_tenant_region_store() is store

    def test_build_default_is_in_memory_when_postgres_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from idis.compliance.tenant_region import (
            InMemoryTenantRegionStore,
            build_default_tenant_region_store,
        )

        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
        assert isinstance(build_default_tenant_region_store(), InMemoryTenantRegionStore)


def _probe_app(service_region: str | None) -> FastAPI:
    """Minimal app exercising ResidencyMiddleware on a /v1 route (no auth/RBAC)."""
    from idis.api.middleware.residency import ResidencyMiddleware

    app = FastAPI()

    @app.get("/v1/probe")
    async def _probe() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(ResidencyMiddleware, service_region=service_region)
    return app


def _inject_claim(app: FastAPI, *, data_region: str | None) -> None:
    """Wrap the app so request.state.tenant_context carries a chosen claim region."""

    class _Injector(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            request.state.tenant_context = TenantContext(
                tenant_id=_TENANT,
                actor_id="actor-1",
                name="Test",
                timezone="UTC",
                data_region=data_region,
            )
            request.state.request_id = "req-durable-test"
            return await call_next(request)

    app.add_middleware(_Injector)


def _probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service_region: str,
    claim_region: str | None,
    durable_on: bool,
    seed: object = "__unset__",
) -> tuple[int, dict]:
    """Drive one /v1/probe request; optionally seed the durable store; return (status, body)."""
    from idis.compliance.tenant_region import (
        InMemoryTenantRegionStore,
        set_tenant_region_store,
    )

    monkeypatch.setenv(_DURABLE_FLAG, "1" if durable_on else "0")
    if seed != "__unset__":
        store = InMemoryTenantRegionStore()
        store.set_region(_TENANT, seed)  # type: ignore[arg-type]
        set_tenant_region_store(store)
    app = _probe_app(service_region)
    _inject_claim(app, data_region=claim_region)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/v1/probe")
    body = resp.json() if resp.content else {}
    return resp.status_code, body


@pytest.mark.usefixtures("_reset_region_store")
class TestDurableResidencyMiddleware:
    """Concern B: residency prefers the durable region under the flag; fail-closed matrix."""

    def test_flag_off_uses_claim_ignores_durable_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Flag off: the claim is authoritative and the durable store is never consulted, even if
        # it holds a conflicting region.
        status, _ = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region="me-south-1",
            durable_on=False,
            seed="us-east-1",
        )
        assert status == 200

    def test_flag_on_durable_match_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        status, _ = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region=None,
            durable_on=True,
            seed="me-south-1",
        )
        assert status == 200

    def test_flag_on_durable_mismatch_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        status, body = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region="me-south-1",
            durable_on=True,
            seed="us-east-1",
        )
        assert status == 403
        assert body["code"] == "RESIDENCY_REGION_MISMATCH"

    def test_flag_on_durable_overrides_claim_source_of_truth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The claim says us-east-1 (would mismatch) but the durable value is the source of truth.
        status, _ = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region="us-east-1",
            durable_on=True,
            seed="me-south-1",
        )
        assert status == 200

    def test_flag_on_missing_durable_row_denies_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Flag on but no durable region for the tenant (no row / never provisioned): deny, even
        # though the claim carries a matching region. The durable value is required.
        status, body = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region="me-south-1",
            durable_on=True,
            seed="__unset__",
        )
        assert status == 403
        assert body["code"] == "RESIDENCY_TENANT_REGION_UNSET"

    def test_flag_on_empty_durable_region_denies_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status, body = _probe(
            monkeypatch,
            service_region="me-south-1",
            claim_region="me-south-1",
            durable_on=True,
            seed="",
        )
        assert status == 403
        assert body["code"] == "RESIDENCY_TENANT_REGION_UNSET"

    def test_flag_on_store_error_denies_fail_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from idis.compliance.tenant_region import set_tenant_region_store

        class _ExplodingStore:
            def get_data_region(self, tenant_id: str) -> str | None:
                raise RuntimeError("db down")

        monkeypatch.setenv(_DURABLE_FLAG, "1")
        set_tenant_region_store(_ExplodingStore())
        app = _probe_app("me-south-1")
        _inject_claim(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/probe")
        assert resp.status_code == 403
        assert resp.json()["code"] == "RESIDENCY_RESOLUTION_FAILED"
