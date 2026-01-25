"""Tests for data residency enforcement (v6.3 Task 7.5).

Requirements (per Traceability Matrix DR-001):
- Region mismatch returns 403 + stable code; does not leak sensitive info
- Missing service region config fails closed
- Tenant data stays in assigned region; cross-region operations forbidden

Test strategy:
- Unit tests for residency.py primitives
- Integration tests for ResidencyMiddleware
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.compliance.residency import (
    IDIS_SERVICE_REGION_ENV,
    ResidencyConfigError,
    enforce_region_pin,
    enforce_region_pin_safe,
    get_service_region_from_env,
)


class TestGetServiceRegionFromEnv:
    """Tests for get_service_region_from_env()."""

    def test_returns_region_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns region value from environment."""
        monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, "me-south-1")
        assert get_service_region_from_env() == "me-south-1"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Strips leading/trailing whitespace from region."""
        monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, "  eu-west-1  ")
        assert get_service_region_from_env() == "eu-west-1"

    def test_raises_on_missing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises ResidencyConfigError when env var is missing."""
        monkeypatch.delenv(IDIS_SERVICE_REGION_ENV, raising=False)
        with pytest.raises(ResidencyConfigError) as exc_info:
            get_service_region_from_env()
        assert IDIS_SERVICE_REGION_ENV in str(exc_info.value)

    def test_raises_on_empty_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises ResidencyConfigError when env var is empty string."""
        monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, "")
        with pytest.raises(ResidencyConfigError):
            get_service_region_from_env()

    def test_raises_on_whitespace_only_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises ResidencyConfigError when env var is only whitespace."""
        monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, "   ")
        with pytest.raises(ResidencyConfigError):
            get_service_region_from_env()


class TestEnforceRegionPin:
    """Tests for enforce_region_pin()."""

    def _make_tenant_ctx(
        self,
        tenant_id: str = "tenant-123",
        data_region: str = "me-south-1",
    ) -> TenantContext:
        """Create a TenantContext for testing."""
        return TenantContext(
            tenant_id=tenant_id,
            actor_id="actor-1",
            name="Test Tenant",
            timezone="UTC",
            data_region=data_region,
        )

    def test_allows_matching_region(self) -> None:
        """No exception when tenant region matches service region."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        enforce_region_pin(ctx, "me-south-1")

    def test_allows_matching_region_case_insensitive(self) -> None:
        """Region comparison is case-insensitive."""
        ctx = self._make_tenant_ctx(data_region="ME-SOUTH-1")
        enforce_region_pin(ctx, "me-south-1")

    def test_allows_matching_region_with_whitespace(self) -> None:
        """Strips whitespace from service region."""
        ctx = self._make_tenant_ctx(data_region="eu-west-1")
        enforce_region_pin(ctx, "  eu-west-1  ")

    def test_denies_mismatched_region(self) -> None:
        """Raises 403 when tenant region doesn't match service region."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin(ctx, "us-east-1")

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "RESIDENCY_REGION_MISMATCH"

    def test_denies_mismatched_region_generic_message(self) -> None:
        """Error message is generic to prevent existence leakage."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin(ctx, "us-east-1")

        assert exc_info.value.message == "Access denied"
        assert "me-south-1" not in exc_info.value.message
        assert "us-east-1" not in exc_info.value.message

    def test_denies_empty_tenant_region(self) -> None:
        """Raises 403 when tenant data_region is empty string."""
        ctx = self._make_tenant_ctx(data_region="")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin(ctx, "us-east-1")

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "RESIDENCY_INVALID_TENANT_CONTEXT"

    def test_denies_whitespace_tenant_region(self) -> None:
        """Raises 403 when tenant data_region is only whitespace."""
        ctx = self._make_tenant_ctx(data_region="   ")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin(ctx, "us-east-1")

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "RESIDENCY_INVALID_TENANT_CONTEXT"

    def test_denies_empty_service_region(self) -> None:
        """Raises 403 when service region is empty."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin(ctx, "")

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "RESIDENCY_CONFIG_ERROR"

    def test_stable_error_code(self) -> None:
        """Error code is stable for client handling."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        errors = []
        for _ in range(3):
            try:
                enforce_region_pin(ctx, "us-east-1")
            except IdisHttpError as e:
                errors.append(e.code)

        assert all(code == "RESIDENCY_REGION_MISMATCH" for code in errors)


class TestEnforceRegionPinSafe:
    """Tests for enforce_region_pin_safe() variant."""

    def _make_tenant_ctx(self, data_region: str = "me-south-1") -> TenantContext:
        return TenantContext(
            tenant_id="tenant-123",
            actor_id="actor-1",
            name="Test Tenant",
            timezone="UTC",
            data_region=data_region,
        )

    def test_skips_enforcement_when_no_service_region(self) -> None:
        """Does not raise when service_region is None."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        enforce_region_pin_safe(ctx, None)

    def test_skips_enforcement_when_empty_service_region(self) -> None:
        """Does not raise when service_region is empty string."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        enforce_region_pin_safe(ctx, "")

    def test_enforces_when_service_region_set(self) -> None:
        """Enforces region when service_region is provided."""
        ctx = self._make_tenant_ctx(data_region="me-south-1")
        with pytest.raises(IdisHttpError) as exc_info:
            enforce_region_pin_safe(ctx, "us-east-1")

        assert exc_info.value.code == "RESIDENCY_REGION_MISMATCH"


class TestResidencyMiddleware:
    """Integration tests for ResidencyMiddleware."""

    def _create_app(self, service_region: str | None = None) -> FastAPI:
        """Create a test FastAPI app with residency middleware."""
        from idis.api.middleware.residency import ResidencyMiddleware

        app = FastAPI()

        @app.get("/v1/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        @app.get("/health")
        async def health_endpoint() -> dict:
            return {"status": "healthy"}

        app.add_middleware(ResidencyMiddleware, service_region=service_region)

        return app

    def _inject_tenant_context(
        self,
        app: FastAPI,
        tenant_id: str = "tenant-123",
        data_region: str = "me-south-1",
    ) -> None:
        """Add middleware to inject tenant context for testing."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class TenantInjectorMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.tenant_context = TenantContext(
                    tenant_id=tenant_id,
                    actor_id="actor-1",
                    name="Test",
                    timezone="UTC",
                    data_region=data_region,
                )
                request.state.request_id = "req-test-123"
                return await call_next(request)

        app.add_middleware(TenantInjectorMiddleware)

    def test_allows_matching_region(self) -> None:
        """Request succeeds when regions match."""
        app = self._create_app(service_region="me-south-1")
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_denies_mismatched_region(self) -> None:
        """Request fails with 403 when regions don't match."""
        app = self._create_app(service_region="us-east-1")
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "RESIDENCY_REGION_MISMATCH"
        assert data["message"] == "Access denied"

    def test_skips_non_v1_paths(self) -> None:
        """Health endpoint bypasses residency check."""
        app = self._create_app(service_region="us-east-1")
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200

    def test_skips_when_no_tenant_context(self) -> None:
        """Skips enforcement when tenant context not set."""
        app = self._create_app(service_region="us-east-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        assert response.status_code == 200

    def test_denies_when_no_service_region_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Denies with 403 when service region not configured (fail-closed)."""
        monkeypatch.delenv(IDIS_SERVICE_REGION_ENV, raising=False)
        app = self._create_app(service_region=None)
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "RESIDENCY_SERVICE_REGION_UNSET"
        assert data["message"] == "Access denied"

    def test_error_includes_request_id(self) -> None:
        """Error response includes request_id for tracing."""
        app = self._create_app(service_region="us-east-1")
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        data = response.json()
        assert "request_id" in data

    def test_no_sensitive_data_in_error(self) -> None:
        """Error response does not leak region names."""
        app = self._create_app(service_region="us-east-1")
        self._inject_tenant_context(app, data_region="me-south-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/test")
        response_text = response.text

        assert "me-south-1" not in response_text
        assert "us-east-1" not in response_text
        assert "tenant-123" not in response_text
