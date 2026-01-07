"""Tests for IDIS API rate limiting middleware.

Verifies tenant-scoped rate limiting per v6.3 API contracts (§4.3):
- User tier: 600 req/min/tenant (burst 2x)
- Integration tier: 1200 req/min/tenant (burst 2x)

Test coverage:
A) User tier hits 429 after capacity
B) Integration tier has higher limit
C) Tenant isolation (tenant A exhausted, tenant B succeeds)
D) Error envelope compliance (429 has code, message, details, request_id)
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.policy import Role
from idis.audit.sink import JsonlFileAuditSink
from idis.rate_limit.limiter import (
    RateLimitConfig,
    RateLimitConfigError,
    RateLimitDecision,
    RateLimitTier,
    TenantRateLimiter,
    classify_tier,
    load_rate_limit_config,
)


def _make_api_keys_json(
    tenant_id: str,
    actor_id: str | None = None,
    name: str = "Test Tenant",
    roles: list[str] | None = None,
    api_key: str = "test-api-key-rate-limit",
) -> str:
    """Create a valid IDIS_API_KEYS_JSON value for testing with roles."""
    if actor_id is None:
        actor_id = f"actor-{tenant_id[:8]}"
    if roles is None:
        roles = [Role.ANALYST.value]
    return json.dumps(
        {
            api_key: {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


def _make_multi_tenant_api_keys_json(
    tenant_a_id: str,
    tenant_b_id: str,
    roles: list[str] | None = None,
) -> str:
    """Create IDIS_API_KEYS_JSON with two tenants for isolation testing."""
    if roles is None:
        roles = [Role.ANALYST.value]
    return json.dumps(
        {
            "api-key-tenant-a": {
                "tenant_id": tenant_a_id,
                "actor_id": f"actor-a-{tenant_a_id[:8]}",
                "name": "Tenant A",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            },
            "api-key-tenant-b": {
                "tenant_id": tenant_b_id,
                "actor_id": f"actor-b-{tenant_b_id[:8]}",
                "name": "Tenant B",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            },
        }
    )


class TestRateLimitConfig:
    """Test rate limit configuration loading and validation."""

    def test_default_config_values(self) -> None:
        """Default config should use documented defaults."""
        for var in [
            "IDIS_RATE_LIMIT_USER_RPM",
            "IDIS_RATE_LIMIT_INTEGRATION_RPM",
            "IDIS_RATE_LIMIT_BURST_MULTIPLIER",
        ]:
            os.environ.pop(var, None)

        try:
            config = load_rate_limit_config()
            assert config.user_rpm == 600
            assert config.integration_rpm == 1200
            assert config.burst_multiplier == 2
        finally:
            pass

    def test_custom_config_from_env(self) -> None:
        """Config should load custom values from environment."""
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "100"
        os.environ["IDIS_RATE_LIMIT_INTEGRATION_RPM"] = "200"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "3"

        try:
            config = load_rate_limit_config()
            assert config.user_rpm == 100
            assert config.integration_rpm == 200
            assert config.burst_multiplier == 3
        finally:
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_INTEGRATION_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)

    def test_invalid_non_integer_raises_error(self) -> None:
        """Non-integer config values should raise RateLimitConfigError."""
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "not-a-number"

        try:
            with pytest.raises(RateLimitConfigError) as exc_info:
                load_rate_limit_config()
            assert "positive integer" in str(exc_info.value)
        finally:
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)

    def test_invalid_zero_raises_error(self) -> None:
        """Zero config values should raise RateLimitConfigError."""
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "0"

        try:
            with pytest.raises(RateLimitConfigError) as exc_info:
                load_rate_limit_config()
            assert "positive integer" in str(exc_info.value)
        finally:
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)

    def test_invalid_negative_raises_error(self) -> None:
        """Negative config values should raise RateLimitConfigError."""
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "-1"

        try:
            with pytest.raises(RateLimitConfigError) as exc_info:
                load_rate_limit_config()
            assert "positive integer" in str(exc_info.value)
        finally:
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestTierClassification:
    """Test rate limit tier classification based on roles."""

    def test_integration_service_role_uses_integration_tier(self) -> None:
        """INTEGRATION_SERVICE role should classify as integration tier."""
        roles = frozenset({"INTEGRATION_SERVICE"})
        assert classify_tier(roles) == RateLimitTier.INTEGRATION

    def test_analyst_role_uses_user_tier(self) -> None:
        """ANALYST role should classify as user tier."""
        roles = frozenset({"ANALYST"})
        assert classify_tier(roles) == RateLimitTier.USER

    def test_admin_role_uses_user_tier(self) -> None:
        """ADMIN role should classify as user tier."""
        roles = frozenset({"ADMIN"})
        assert classify_tier(roles) == RateLimitTier.USER

    def test_mixed_roles_with_integration_uses_integration_tier(self) -> None:
        """Mixed roles including INTEGRATION_SERVICE should use integration tier."""
        roles = frozenset({"ANALYST", "INTEGRATION_SERVICE"})
        assert classify_tier(roles) == RateLimitTier.INTEGRATION

    def test_empty_roles_uses_user_tier(self) -> None:
        """Empty roles should default to user tier."""
        roles: frozenset[str] = frozenset()
        assert classify_tier(roles) == RateLimitTier.USER


class TestTokenBucketLimiter:
    """Test TenantRateLimiter token bucket behavior."""

    def test_allows_requests_within_capacity(self) -> None:
        """Requests within capacity should be allowed."""
        config = RateLimitConfig(user_rpm=10, integration_rpm=20, burst_multiplier=2)
        limiter = TenantRateLimiter(config)

        for i in range(20):
            decision = limiter.check("tenant-1", RateLimitTier.USER)
            assert decision.allowed, f"Request {i + 1} should be allowed"

    def test_denies_requests_beyond_capacity(self) -> None:
        """Requests beyond capacity should be denied."""
        config = RateLimitConfig(user_rpm=2, integration_rpm=4, burst_multiplier=2)
        limiter = TenantRateLimiter(config)

        for _ in range(4):
            decision = limiter.check("tenant-1", RateLimitTier.USER)
            assert decision.allowed

        decision = limiter.check("tenant-1", RateLimitTier.USER)
        assert not decision.allowed
        assert decision.retry_after_seconds is not None
        assert decision.retry_after_seconds >= 1

    def test_decision_includes_required_fields(self) -> None:
        """RateLimitDecision should include all required fields."""
        config = RateLimitConfig(user_rpm=10, integration_rpm=20, burst_multiplier=2)
        limiter = TenantRateLimiter(config)

        decision = limiter.check("tenant-1", RateLimitTier.USER)

        assert isinstance(decision, RateLimitDecision)
        assert isinstance(decision.allowed, bool)
        assert decision.remaining_tokens >= 0
        assert decision.limit_rpm == 10
        assert decision.burst_multiplier == 2
        assert decision.tier == RateLimitTier.USER

    def test_integration_tier_has_higher_limit(self) -> None:
        """Integration tier should have higher capacity than user tier."""
        config = RateLimitConfig(user_rpm=2, integration_rpm=4, burst_multiplier=2)
        limiter = TenantRateLimiter(config)

        for _ in range(8):
            decision = limiter.check("tenant-1", RateLimitTier.INTEGRATION)
            assert decision.allowed

        decision = limiter.check("tenant-1", RateLimitTier.INTEGRATION)
        assert not decision.allowed


class TestUserTierHits429AfterCapacity:
    """Test A: User tier hits 429 after capacity exhausted."""

    def test_user_tier_429_after_burst_capacity(self, tmp_path: Path) -> None:
        """User tier should get 429 after capacity (rpm * burst_multiplier) requests.

        Config: user_rpm=2, burst_multiplier=2 => capacity=4
        First 4 requests => 200
        5th request => 429 with RATE_LIMIT_EXCEEDED
        """
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_rate_limit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "2"
        os.environ["IDIS_RATE_LIMIT_INTEGRATION_RPM"] = "100"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "2"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            responses = []
            for _ in range(5):
                response = client.get(
                    "/v1/deals",
                    headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
                )
                responses.append(response)

            for i, resp in enumerate(responses[:4]):
                assert resp.status_code == 200, f"Request {i + 1} should be 200"

            assert responses[4].status_code == 429

            body = responses[4].json()
            assert body["code"] == "RATE_LIMIT_EXCEEDED"
            assert "request_id" in body
            assert body["request_id"] == responses[4].headers["X-Request-Id"]

            assert "Retry-After" in responses[4].headers
            retry_after = int(responses[4].headers["Retry-After"])
            assert retry_after >= 1

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_INTEGRATION_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestIntegrationTierHigherLimit:
    """Test B: Integration tier has higher limit than user tier."""

    def test_integration_tier_does_not_429_at_user_threshold(self, tmp_path: Path) -> None:
        """Integration tier should not 429 at the same threshold where user would.

        Config: user_rpm=2, integration_rpm=4, burst_multiplier=2
        User capacity: 4, Integration capacity: 8
        5 requests with INTEGRATION_SERVICE role should all succeed.
        """
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_integration_tier.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.INTEGRATION_SERVICE.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "2"
        os.environ["IDIS_RATE_LIMIT_INTEGRATION_RPM"] = "4"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "2"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            responses = []
            for _ in range(5):
                response = client.get(
                    "/v1/deals",
                    headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
                )
                responses.append(response)

            for i, resp in enumerate(responses):
                assert resp.status_code == 200, (
                    f"Integration request {i + 1} should be 200, got {resp.status_code}"
                )

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_INTEGRATION_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestTenantIsolation:
    """Test C: Tenant isolation - tenant A exhausted does not affect tenant B."""

    def test_tenant_isolation_rate_limits(self, tmp_path: Path) -> None:
        """Exhausting tenant A's bucket should not affect tenant B.

        Config: user_rpm=2, burst_multiplier=2 => capacity=4
        Exhaust tenant A with 5 requests (4 succeed, 5th gets 429)
        Tenant B should still succeed on first request.
        """
        tenant_a_id = str(uuid.uuid4())
        tenant_b_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_tenant_isolation.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_multi_tenant_api_keys_json(
            tenant_a_id, tenant_b_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "2"
        os.environ["IDIS_RATE_LIMIT_INTEGRATION_RPM"] = "100"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "2"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            for _ in range(5):
                client.get(
                    "/v1/deals",
                    headers={"X-IDIS-API-Key": "api-key-tenant-a"},
                )

            response_a = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "api-key-tenant-a"},
            )
            assert response_a.status_code == 429, "Tenant A should be rate limited"

            response_b = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "api-key-tenant-b"},
            )
            assert response_b.status_code == 200, (
                f"Tenant B should NOT be affected by tenant A's rate limit, "
                f"got {response_b.status_code}"
            )

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_INTEGRATION_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestErrorEnvelopeCompliance:
    """Test D: Error envelope compliance for 429 responses."""

    def test_429_error_envelope_structure(self, tmp_path: Path) -> None:
        """429 response must include code, message, details, request_id."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_error_envelope.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "1"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "1"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )
            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )

            assert response.status_code == 429
            body = response.json()

            assert "code" in body
            assert "message" in body
            assert "details" in body
            assert "request_id" in body

            assert body["code"] == "RATE_LIMIT_EXCEEDED"
            assert body["message"] == "Rate limit exceeded"
            assert isinstance(body["details"], dict)
            assert "limit_rpm" in body["details"]
            assert "tier" in body["details"]
            assert "retry_after_seconds" in body["details"]
            assert isinstance(body["request_id"], str)
            assert len(body["request_id"]) > 0

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)

    def test_429_no_traceback_leakage(self, tmp_path: Path) -> None:
        """429 response must not leak stack traces."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_no_traceback.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "1"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "1"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )
            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )

            assert response.status_code == 429
            response_text = response.text

            assert "Traceback" not in response_text
            assert 'File "' not in response_text
            assert "line " not in response_text.lower() or "limit" in response_text.lower()

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)

    def test_429_request_id_matches_header(self, tmp_path: Path) -> None:
        """429 response request_id must match X-Request-Id header."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_request_id.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "1"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "1"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )
            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )

            assert response.status_code == 429
            body = response.json()

            assert "X-Request-Id" in response.headers
            assert body["request_id"] == response.headers["X-Request-Id"]

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestRateLimitHeaders:
    """Test rate limit response headers on successful requests."""

    def test_success_includes_rate_limit_headers(self, tmp_path: Path) -> None:
        """Successful requests should include X-IDIS-RateLimit-* headers."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_headers.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "10"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "2"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rate-limit"},
            )

            assert response.status_code == 200
            assert "X-IDIS-RateLimit-Limit" in response.headers
            assert "X-IDIS-RateLimit-Remaining" in response.headers

            assert response.headers["X-IDIS-RateLimit-Limit"] == "10"
            remaining = int(response.headers["X-IDIS-RateLimit-Remaining"])
            assert remaining >= 0

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestNonV1PathsNotRateLimited:
    """Test that non-/v1 paths are not rate limited."""

    def test_health_endpoint_not_rate_limited(self, tmp_path: Path) -> None:
        """/health endpoint should not be rate limited."""
        audit_log_path = tmp_path / "audit_health.jsonl"

        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "1"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "1"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            for _ in range(10):
                response = client.get("/health")
                assert response.status_code == 200

        finally:
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)


class TestUnauthenticatedRequestsSkipRateLimit:
    """Test that unauthenticated requests skip rate limiting."""

    def test_unauthenticated_request_gets_401_not_429(self, tmp_path: Path) -> None:
        """Unauthenticated requests should get 401, not 429."""
        audit_log_path = tmp_path / "audit_unauth.jsonl"

        os.environ.pop("IDIS_API_KEYS_JSON", None)
        os.environ["IDIS_RATE_LIMIT_USER_RPM"] = "1"
        os.environ["IDIS_RATE_LIMIT_BURST_MULTIPLIER"] = "1"

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            limiter = TenantRateLimiter()
            app = create_app(audit_sink=sink, rate_limiter=limiter)
            client = TestClient(app, raise_server_exceptions=False)

            for _ in range(5):
                response = client.get("/v1/deals")
                assert response.status_code == 401, (
                    f"Unauthenticated should get 401, got {response.status_code}"
                )

        finally:
            os.environ.pop("IDIS_RATE_LIMIT_USER_RPM", None)
            os.environ.pop("IDIS_RATE_LIMIT_BURST_MULTIPLIER", None)
