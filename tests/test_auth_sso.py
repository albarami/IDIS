"""Tests for SSO/OIDC JWT authentication.

Required by Phase 7 Task 7.1 roadmap:
- Valid JWT succeeds
- Expired JWT fails
- Wrong issuer/audience fails
- Missing required IDIS claims fails
- JWKS mismatch/no-kid fails
- Config missing fails closed
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any
from unittest import mock

import pytest

from idis.api.auth_sso import (
    DEFAULT_JWKS_CACHE_TTL,
    JwksCache,
    OidcConfig,
    SsoIdentity,
    _decode_jwt_parts,
    _validate_idis_claims,
    _validate_standard_claims,
    clear_jwks_cache,
    is_sso_configured,
    load_oidc_config,
    validate_jwt,
)
from idis.api.errors import IdisHttpError


def _create_test_jwt(
    header: dict[str, Any],
    payload: dict[str, Any],
    signature: bytes = b"test_signature",
) -> str:
    """Create a test JWT string (not cryptographically valid)."""

    def b64_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    header_b64 = b64_encode(json.dumps(header).encode("utf-8"))
    payload_b64 = b64_encode(json.dumps(payload).encode("utf-8"))
    sig_b64 = b64_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _valid_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a valid JWT payload with required IDIS claims."""
    now = time.time()
    payload = {
        "iss": "https://idp.example.com",
        "aud": "idis-api",
        "sub": "user-123",
        "exp": int(now + 3600),
        "iat": int(now),
        "tenant_id": "tenant-abc",
        "roles": ["ANALYST"],
        "email": "user@example.com",
        "name": "Test User",
    }
    if overrides:
        payload.update(overrides)
    return payload


class TestLoadOidcConfig:
    """Tests for load_oidc_config."""

    def test_returns_none_when_env_vars_missing(self) -> None:
        """Config missing fails closed (returns None for fallback to API key)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = load_oidc_config()
            assert config is None

    def test_returns_none_when_partial_env_vars(self) -> None:
        """Partial config fails closed."""
        with mock.patch.dict(
            os.environ,
            {"IDIS_OIDC_ISSUER": "https://idp.example.com"},
            clear=True,
        ):
            config = load_oidc_config()
            assert config is None

    def test_returns_config_when_all_env_vars_set(self) -> None:
        """Valid config is loaded correctly."""
        env = {
            "IDIS_OIDC_ISSUER": "https://idp.example.com",
            "IDIS_OIDC_AUDIENCE": "idis-api",
            "IDIS_OIDC_JWKS_URI": "https://idp.example.com/.well-known/jwks.json",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_oidc_config()
            assert config is not None
            assert config.issuer == "https://idp.example.com"
            assert config.audience == "idis-api"
            assert config.jwks_uri == "https://idp.example.com/.well-known/jwks.json"
            assert config.jwks_cache_ttl == DEFAULT_JWKS_CACHE_TTL

    def test_custom_cache_ttl(self) -> None:
        """Custom cache TTL is respected."""
        env = {
            "IDIS_OIDC_ISSUER": "https://idp.example.com",
            "IDIS_OIDC_AUDIENCE": "idis-api",
            "IDIS_OIDC_JWKS_URI": "https://idp.example.com/.well-known/jwks.json",
            "IDIS_OIDC_JWKS_CACHE_TTL": "1800",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_oidc_config()
            assert config is not None
            assert config.jwks_cache_ttl == 1800


class TestIsSsoConfigured:
    """Tests for is_sso_configured."""

    def test_returns_false_when_not_configured(self) -> None:
        """Returns False when OIDC not configured."""
        with mock.patch.dict(os.environ, {}, clear=True):
            assert is_sso_configured() is False

    def test_returns_true_when_configured(self) -> None:
        """Returns True when OIDC is configured."""
        env = {
            "IDIS_OIDC_ISSUER": "https://idp.example.com",
            "IDIS_OIDC_AUDIENCE": "idis-api",
            "IDIS_OIDC_JWKS_URI": "https://idp.example.com/.well-known/jwks.json",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            assert is_sso_configured() is True


class TestDecodeJwtParts:
    """Tests for _decode_jwt_parts."""

    def test_decodes_valid_jwt(self) -> None:
        """Valid JWT is decoded correctly."""
        header = {"alg": "RS256", "typ": "JWT", "kid": "key-1"}
        payload = _valid_payload()
        token = _create_test_jwt(header, payload)

        decoded_header, decoded_payload, signature = _decode_jwt_parts(token)

        assert decoded_header["alg"] == "RS256"
        assert decoded_header["kid"] == "key-1"
        assert decoded_payload["tenant_id"] == "tenant-abc"
        assert decoded_payload["sub"] == "user-123"

    def test_rejects_malformed_jwt_missing_parts(self) -> None:
        """Malformed JWT (missing parts) fails closed."""
        with pytest.raises(IdisHttpError) as exc_info:
            _decode_jwt_parts("only.two")

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_token"

    def test_rejects_malformed_jwt_invalid_base64(self) -> None:
        """Malformed JWT (invalid base64) fails closed."""
        with pytest.raises(IdisHttpError) as exc_info:
            _decode_jwt_parts("!!!.@@@.###")

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_token"


class TestValidateStandardClaims:
    """Tests for _validate_standard_claims."""

    @pytest.fixture
    def config(self) -> OidcConfig:
        """Create test OIDC config."""
        return OidcConfig(
            issuer="https://idp.example.com",
            audience="idis-api",
            jwks_uri="https://idp.example.com/.well-known/jwks.json",
        )

    def test_valid_claims_pass(self, config: OidcConfig) -> None:
        """Valid standard claims pass validation."""
        payload = _valid_payload()
        _validate_standard_claims(payload, config)

    def test_wrong_issuer_fails(self, config: OidcConfig) -> None:
        """Wrong issuer fails closed."""
        payload = _valid_payload({"iss": "https://wrong-idp.example.com"})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_token"
        assert "issuer" in exc_info.value.message.lower()

    def test_wrong_audience_fails(self, config: OidcConfig) -> None:
        """Wrong audience fails closed."""
        payload = _valid_payload({"aud": "wrong-audience"})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_token"
        assert "audience" in exc_info.value.message.lower()

    def test_audience_as_list_valid(self, config: OidcConfig) -> None:
        """Audience as list with valid entry passes."""
        payload = _valid_payload({"aud": ["idis-api", "other-api"]})
        _validate_standard_claims(payload, config)

    def test_audience_as_list_invalid(self, config: OidcConfig) -> None:
        """Audience as list without valid entry fails."""
        payload = _valid_payload({"aud": ["wrong-api", "other-api"]})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401

    def test_expired_token_fails(self, config: OidcConfig) -> None:
        """Expired JWT fails closed."""
        payload = _valid_payload({"exp": int(time.time() - 3600)})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "token_expired"

    def test_missing_exp_fails(self, config: OidcConfig) -> None:
        """Missing expiration fails closed."""
        payload = _valid_payload()
        del payload["exp"]

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401
        assert "expiration" in exc_info.value.message.lower()

    def test_nbf_in_future_fails(self, config: OidcConfig) -> None:
        """Token not yet valid (nbf in future) fails closed."""
        payload = _valid_payload({"nbf": int(time.time() + 3600)})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_standard_claims(payload, config)

        assert exc_info.value.status_code == 401
        assert "not yet valid" in exc_info.value.message.lower()


class TestValidateIdisClaims:
    """Tests for _validate_idis_claims."""

    def test_valid_claims_return_identity(self) -> None:
        """Valid IDIS claims return SsoIdentity."""
        payload = _valid_payload()

        identity = _validate_idis_claims(payload)

        assert identity.tenant_id == "tenant-abc"
        assert identity.user_id == "user-123"
        assert "ANALYST" in identity.roles
        assert identity.email == "user@example.com"
        assert identity.name == "Test User"

    def test_missing_tenant_id_fails(self) -> None:
        """Missing tenant_id fails closed."""
        payload = _valid_payload()
        del payload["tenant_id"]

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_idis_claims(payload)

        assert exc_info.value.status_code == 401
        assert "tenant_id" in exc_info.value.message

    def test_missing_user_id_fails(self) -> None:
        """Missing user_id (sub) fails closed."""
        payload = _valid_payload()
        del payload["sub"]

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_idis_claims(payload)

        assert exc_info.value.status_code == 401
        assert "user_id" in exc_info.value.message

    def test_missing_roles_fails(self) -> None:
        """Missing roles fails closed."""
        payload = _valid_payload()
        del payload["roles"]

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_idis_claims(payload)

        assert exc_info.value.status_code == 401
        assert "roles" in exc_info.value.message

    def test_empty_roles_fails(self) -> None:
        """Empty roles list fails closed."""
        payload = _valid_payload({"roles": []})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_idis_claims(payload)

        assert exc_info.value.status_code == 401

    def test_unknown_role_fails_closed(self) -> None:
        """Unknown role fails closed (not silently ignored)."""
        payload = _valid_payload({"roles": ["ANALYST", "SUPER_ADMIN"]})

        with pytest.raises(IdisHttpError) as exc_info:
            _validate_idis_claims(payload)

        assert exc_info.value.status_code == 401
        assert "SUPER_ADMIN" in exc_info.value.message

    def test_namespaced_claims_supported(self) -> None:
        """Namespaced IDIS claims (https://idis.io/...) are supported."""
        payload = _valid_payload()
        del payload["tenant_id"]
        del payload["roles"]
        payload["https://idis.io/tenant_id"] = "tenant-xyz"
        payload["https://idis.io/roles"] = ["PARTNER"]

        identity = _validate_idis_claims(payload)

        assert identity.tenant_id == "tenant-xyz"
        assert "PARTNER" in identity.roles

    def test_all_valid_roles_accepted(self) -> None:
        """All valid roles are accepted."""
        valid_roles = ["ANALYST", "PARTNER", "IC_MEMBER", "ADMIN", "AUDITOR", "INTEGRATION_SERVICE"]
        for role in valid_roles:
            payload = _valid_payload({"roles": [role]})
            identity = _validate_idis_claims(payload)
            assert role in identity.roles

    def test_optional_data_region(self) -> None:
        """Optional data_region is extracted."""
        payload = _valid_payload({"data_region": "eu-west-1"})

        identity = _validate_idis_claims(payload)

        assert identity.data_region == "eu-west-1"

    def test_optional_policy_tags(self) -> None:
        """Optional policy_tags are extracted."""
        payload = _valid_payload({"policy_tags": ["fund_a", "restricted"]})

        identity = _validate_idis_claims(payload)

        assert "fund_a" in identity.policy_tags
        assert "restricted" in identity.policy_tags


class TestValidateJwt:
    """Tests for validate_jwt (integration tests with mocked JWKS)."""

    def setup_method(self) -> None:
        """Clear JWKS cache before each test."""
        clear_jwks_cache()

    def test_config_missing_fails_closed(self) -> None:
        """SSO not configured fails closed."""
        with mock.patch.dict(os.environ, {}, clear=True):
            token = _create_test_jwt(
                {"alg": "RS256", "kid": "key-1"},
                _valid_payload(),
            )

            with pytest.raises(IdisHttpError) as exc_info:
                validate_jwt(token)

            assert exc_info.value.status_code == 401
            assert exc_info.value.code == "sso_not_configured"

    def test_missing_kid_fails(self) -> None:
        """JWT without kid fails closed."""
        config = OidcConfig(
            issuer="https://idp.example.com",
            audience="idis-api",
            jwks_uri="https://idp.example.com/.well-known/jwks.json",
        )
        token = _create_test_jwt(
            {"alg": "RS256"},
            _valid_payload(),
        )

        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(token, config=config)

        assert exc_info.value.status_code == 401
        assert "key ID" in exc_info.value.message


class TestJwksCache:
    """Tests for JwksCache."""

    def test_empty_cache_is_invalid(self) -> None:
        """Empty cache is invalid."""
        cache = JwksCache()
        assert cache.is_valid() is False

    def test_cache_with_keys_is_valid(self) -> None:
        """Cache with keys and fresh timestamp is valid."""
        cache = JwksCache(
            keys={"key-1": {"kty": "RSA", "kid": "key-1"}},
            fetched_at=time.time(),
            ttl=3600,
        )
        assert cache.is_valid() is True

    def test_expired_cache_is_invalid(self) -> None:
        """Cache past TTL is invalid."""
        cache = JwksCache(
            keys={"key-1": {"kty": "RSA", "kid": "key-1"}},
            fetched_at=time.time() - 7200,
            ttl=3600,
        )
        assert cache.is_valid() is False

    def test_get_key_returns_key(self) -> None:
        """get_key returns the key by kid."""
        cache = JwksCache(
            keys={"key-1": {"kty": "RSA", "kid": "key-1", "n": "abc"}},
            fetched_at=time.time(),
        )
        key = cache.get_key("key-1")
        assert key is not None
        assert key["n"] == "abc"

    def test_get_key_returns_none_for_unknown(self) -> None:
        """get_key returns None for unknown kid."""
        cache = JwksCache(
            keys={"key-1": {"kty": "RSA", "kid": "key-1"}},
            fetched_at=time.time(),
        )
        key = cache.get_key("key-2")
        assert key is None


class TestSsoIdentity:
    """Tests for SsoIdentity dataclass."""

    def test_immutable(self) -> None:
        """SsoIdentity is immutable (frozen)."""
        identity = SsoIdentity(
            tenant_id="tenant-1",
            user_id="user-1",
            roles=frozenset(["ANALYST"]),
        )

        with pytest.raises(AttributeError):
            identity.tenant_id = "tenant-2"  # type: ignore[misc]

    def test_defaults(self) -> None:
        """SsoIdentity has sensible defaults."""
        identity = SsoIdentity(
            tenant_id="tenant-1",
            user_id="user-1",
            roles=frozenset(["ANALYST"]),
        )

        assert identity.email is None
        assert identity.name is None
        assert identity.data_region is None
        assert identity.policy_tags == frozenset()
        assert identity.token_hash == ""
