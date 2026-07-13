"""Slice98 Task 4 - MFA enforcement hook: verify IdP-issued MFA proof (no first-party MFA).

RED-first. Decision (approved): MFA is enforced at the IdP ("MFA enforced via IdP (MUST)");
IDIS verifies the proof. When ``IDIS_REQUIRE_MFA`` is enabled (default off), ``validate_jwt``
requires the token's RFC 8176 ``amr`` array to intersect the accepted MFA values
(``IDIS_MFA_AMR_VALUES``, default ``mfa``) and fails closed with 401 otherwise - missing ``amr``,
non-array ``amr``, an empty accepted set, or no accepted intersection all deny. API-key (SERVICE)
auth is exempt: the flag gates only Bearer/JWT human SSO. On an MFA-required denial the request
boundary emits exactly one schema-valid ``auth.mfa.failed`` audit event (MEDIUM, resource_type
``session``) carrying no token or raw-claim material; an audit-sink failure still denies.

These tests run the REAL ``validate_jwt`` flow (config -> decode -> kid -> standard claims ->
IDIS claims -> MFA proof) with only the signature-crypto boundary (``_verify_signature``) mocked,
mirroring the existing ``_fetch_jwks`` mocking precedent. PYTHONPATH is pinned to this worktree's
src for every run.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import idis.api.auth_sso as sso
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.auth_sso import (
    JwksCache,
    OidcConfig,
    clear_jwks_cache,
    set_jwks_cache,
    validate_jwt,
)
from idis.api.errors import IdisHttpError
from idis.api.main import create_app
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.validators.audit_event_validator import validate_audit_event

_TENANT = str(uuid.uuid4())
_ISSUER = "https://idp.example.com"
_AUDIENCE = "idis-api"
_JWKS_URI = "https://idp.example.com/.well-known/jwks.json"
_EMAIL = "mfa-user@example.com"
_NAME = "Mfa Test User"

_CONFIG = OidcConfig(issuer=_ISSUER, audience=_AUDIENCE, jwks_uri=_JWKS_URI)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _make_token(payload: dict[str, Any]) -> str:
    header = {"alg": "RS256", "kid": "key-1"}
    return (
        f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}."
        f"{_b64(b'test-signature')}"
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    now = time.time()
    payload: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "user-mfa-1",
        "exp": int(now + 3600),
        "iat": int(now),
        "tenant_id": _TENANT,
        "roles": ["ANALYST"],
        "email": _EMAIL,
        "name": _NAME,
        "data_region": "us-east-1",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def jwt_crypto(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Seed the JWKS cache and mock ONLY the signature check; the rest of validate_jwt is real."""
    set_jwks_cache(
        JwksCache(
            keys={"key-1": {"kty": "RSA", "kid": "key-1", "n": "abc", "e": "AQAB"}},
            fetched_at=time.time(),
            ttl=3600,
        )
    )
    monkeypatch.setattr(sso, "_verify_signature", lambda token, jwk: None)
    monkeypatch.delenv("IDIS_REQUIRE_MFA", raising=False)
    monkeypatch.delenv("IDIS_MFA_AMR_VALUES", raising=False)
    yield
    clear_jwks_cache()


@pytest.mark.usefixtures("jwt_crypto")
class TestMfaProofInValidateJwt:
    """The MFA proof check lives in validate_jwt, after signature/standard/IDIS validation."""

    def test_flag_off_jwt_without_amr_still_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IDIS_REQUIRE_MFA", raising=False)
        identity = validate_jwt(_make_token(_payload()), config=_CONFIG)
        assert identity.tenant_id == _TENANT

    def test_flag_on_amr_containing_mfa_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        identity = validate_jwt(_make_token(_payload(amr=["pwd", "mfa"])), config=_CONFIG)
        assert identity.user_id == "user-mfa-1"

    def test_flag_on_missing_amr_denied_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload()), config=_CONFIG)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "mfa_required"

    def test_flag_on_non_list_amr_denied_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr="mfa")), config=_CONFIG)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "mfa_required"

    def test_flag_on_no_accepted_intersection_denied_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr=["pwd", "pin"])), config=_CONFIG)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "mfa_required"

    def test_flag_on_empty_accepted_set_denies_even_mfa_amr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A configured-but-empty accepted set is a config error: deny everything (fail closed),
        # never fall back to an implicit default.
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        monkeypatch.setenv("IDIS_MFA_AMR_VALUES", " ,  , ")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr=["mfa"])), config=_CONFIG)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "mfa_required"

    def test_alternate_accepted_values_replace_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        monkeypatch.setenv("IDIS_MFA_AMR_VALUES", "otp, hwk")
        identity = validate_jwt(_make_token(_payload(amr=["otp"])), config=_CONFIG)
        assert identity.user_id == "user-mfa-1"
        # the configured set REPLACES the default: literal "mfa" is no longer accepted
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr=["mfa"])), config=_CONFIG)
        assert exc_info.value.code == "mfa_required"

    def test_amr_non_string_entries_are_ignored_not_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr=[42, None, {"m": "fa"}])), config=_CONFIG)
        assert exc_info.value.code == "mfa_required"

    def test_denial_is_generic_and_leaks_no_amr_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        with pytest.raises(IdisHttpError) as exc_info:
            validate_jwt(_make_token(_payload(amr=["pwd", "kba"])), config=_CONFIG)
        err = exc_info.value
        assert "pwd" not in err.message and "kba" not in err.message
        # tenant/actor attribution for the audit boundary must NOT ride in the response
        # envelope's details
        assert err.details is None


def _session_event() -> dict[str, Any]:
    """A fully-populated auth.mfa.failed event with resource_type=session."""
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": "2026-07-13T00:00:00Z",
        "tenant_id": str(uuid.uuid4()),
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": "user-mfa-1",
            "roles": ["ANALYST"],
            "ip": "127.0.0.1",
            "user_agent": "pytest",
        },
        "request": {
            "request_id": "req-mfa-1",
            "method": "GET",
            "path": "/v1/tenants/me",
            "status_code": 401,
        },
        "resource": {"resource_type": "session", "resource_id": "req-mfa-1"},
        "event_type": "auth.mfa.failed",
        "severity": "MEDIUM",
        "summary": "MFA-required denial: bearer token lacks accepted MFA proof",
    }


class TestSessionResourceType:
    """resource_type=session is valid in BOTH the Python validator and the JSON schema."""

    def test_validator_accepts_session_resource_type(self) -> None:
        result = validate_audit_event(_session_event())
        assert result.passed, [e.code for e in result.errors]

    def test_json_schema_enum_includes_session(self) -> None:
        from pathlib import Path

        schema = json.loads(Path("schemas/audit_event.schema.json").read_text(encoding="utf-8"))
        enum = schema["properties"]["resource"]["properties"]["resource_type"]["enum"]
        assert "session" in enum


class _ExplodingSink:
    """Audit sink whose emission always fails; the request must still be denied."""

    def emit(self, event: dict[str, Any]) -> None:
        raise AuditSinkError("sink unavailable")


def _api_keys_json() -> str:
    return json.dumps(
        {
            "api-key-mfa": {
                "tenant_id": _TENANT,
                "actor_id": "svc-mfa-1",
                "name": "Service",
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ADMIN"],
            }
        }
    )


@pytest.fixture
def mfa_app(
    monkeypatch: pytest.MonkeyPatch, jwt_crypto: None
) -> Iterator[tuple[TestClient, InMemoryAuditSink]]:
    """Full app with IDIS_REQUIRE_MFA on, real validate_jwt (sig mocked), in-memory audit sink."""
    monkeypatch.setenv("IDIS_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("IDIS_OIDC_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("IDIS_OIDC_JWKS_URI", _JWKS_URI)
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_json())
    monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
    monkeypatch.delenv("IDIS_ENABLE_DURABLE_RESIDENCY", raising=False)
    sink = InMemoryAuditSink()
    app = create_app(audit_sink=sink, service_region="us-east-1")
    yield TestClient(app, raise_server_exceptions=False), sink


def _bearer(payload: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(payload)}"}


class TestMfaDenialFullRequestPath:
    """Boundary behavior: 401 + exactly one schema-valid auth.mfa.failed; API keys exempt."""

    def test_denial_emits_single_schema_valid_event_without_claim_material(
        self, mfa_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        client, sink = mfa_app
        token_payload = _payload()  # no amr claim
        resp = client.get("/v1/tenants/me", headers=_bearer(token_payload))
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "mfa_required"

        events = [e for e in sink.events if e.get("event_type") == "auth.mfa.failed"]
        assert len(events) == 1, f"expected exactly one auth.mfa.failed, got {len(events)}"
        event = events[0]

        result = validate_audit_event(event)
        assert result.passed, [e.code for e in result.errors]
        assert event["severity"] == "MEDIUM"
        assert event["tenant_id"] == _TENANT
        assert event["actor"]["actor_type"] == "HUMAN"
        assert event["actor"]["actor_id"] == "user-mfa-1"
        assert event["resource"]["resource_type"] == "session"
        assert event["request"]["status_code"] == 401

        # no token or raw-claim material anywhere in the event
        raw = json.dumps(events[0])
        assert _make_token(token_payload) not in raw
        assert "amr" not in raw
        assert _EMAIL not in raw
        assert _NAME not in raw

    def test_mfa_pass_emits_no_event_and_succeeds(
        self, mfa_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        client, sink = mfa_app
        resp = client.get("/v1/tenants/me", headers=_bearer(_payload(amr=["pwd", "mfa"])))
        assert resp.status_code == 200, resp.text
        assert [e for e in sink.events if e.get("event_type") == "auth.mfa.failed"] == []

    def test_api_key_auth_unchanged_when_mfa_required(
        self, mfa_app: tuple[TestClient, InMemoryAuditSink]
    ) -> None:
        client, sink = mfa_app
        resp = client.get("/v1/tenants/me", headers={"X-IDIS-API-Key": "api-key-mfa"})
        assert resp.status_code == 200, resp.text
        assert [e for e in sink.events if e.get("event_type") == "auth.mfa.failed"] == []

    def test_audit_sink_failure_still_denies_the_request(
        self, monkeypatch: pytest.MonkeyPatch, jwt_crypto: None
    ) -> None:
        monkeypatch.setenv("IDIS_OIDC_ISSUER", _ISSUER)
        monkeypatch.setenv("IDIS_OIDC_AUDIENCE", _AUDIENCE)
        monkeypatch.setenv("IDIS_OIDC_JWKS_URI", _JWKS_URI)
        monkeypatch.setenv("IDIS_REQUIRE_MFA", "1")
        app = create_app(audit_sink=_ExplodingSink(), service_region="us-east-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/tenants/me", headers=_bearer(_payload()))
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "mfa_required"
