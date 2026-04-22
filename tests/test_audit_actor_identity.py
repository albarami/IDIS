"""Regression tests for audit actor identity (Sprint 1 Wave 1, Task 2).

Proves that successful authenticated mutations emit the real authenticated
principal in the audit event's `actor` block:

* API-key mutation -> actor_type=SERVICE,
  actor_id=ApiKeyRecord.actor_id,
  roles=sorted(ApiKeyRecord.roles).
* JWT mutation     -> actor_type=HUMAN,
  actor_id=SsoIdentity.user_id,
  roles=sorted(SsoIdentity.roles).

The old bug (actor_id = tenant_ctx.name, roles = ["INTEGRATION_SERVICE"]) is
specifically regression-guarded so a silent revert would fail the suite.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import JsonlFileAuditSink

API_KEY = "test-audit-actor-key"


def _api_keys_env_value(
    *,
    tenant_id: str,
    actor_id: str,
    roles: list[str],
    name: str = "Tenant Display Name",
) -> str:
    return json.dumps(
        {
            API_KEY: {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


def _read_one_event(path: Path) -> dict:
    assert path.exists(), f"expected audit log at {path}"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one audit event; got {len(lines)}: {lines!r}"
    return json.loads(lines[0])


@pytest.fixture
def audit_log_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def api_key_client(
    audit_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict]:
    """TestClient with a single API key whose record is returned alongside."""
    tenant_id = str(uuid.uuid4())
    # Use an actor_id that is demonstrably NOT the tenant display name and not
    # the synthetic INTEGRATION_SERVICE role, so the test catches both bugs.
    record = {
        "tenant_id": tenant_id,
        "actor_id": f"actor-{uuid.uuid4().hex[:12]}",
        "name": "Tenant Display Name",
        "roles": ["ANALYST", "ADMIN"],
    }
    monkeypatch.setenv(
        "IDIS_API_KEYS_JSON",
        _api_keys_env_value(
            tenant_id=record["tenant_id"],
            actor_id=record["actor_id"],
            roles=list(record["roles"]),
            name=record["name"],
        ),
    )
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))

    sink = JsonlFileAuditSink(str(audit_log_path))
    app = create_app(audit_sink=sink, service_region="us-east-1")
    client = TestClient(app, raise_server_exceptions=False)
    return client, record


class TestApiKeyActorIdentity:
    """Authenticated API-key mutations must record the real principal."""

    def test_actor_type_is_SERVICE_and_actor_id_is_api_key_record_actor_id(
        self, api_key_client: tuple[TestClient, dict], audit_log_path: Path
    ) -> None:
        client, record = api_key_client

        response = client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            content=json.dumps({"name": "Actor Identity Test", "company_name": "Acme"}),
        )
        # Handler may return 201 or 500 (Postgres not configured in some envs);
        # we only need a 2xx so the audit path runs.
        assert response.status_code < 400, response.text

        event = _read_one_event(audit_log_path)
        actor = event["actor"]

        assert actor["actor_type"] == "SERVICE"
        assert actor["actor_id"] == record["actor_id"]

    def test_roles_are_sorted_api_key_record_roles(
        self, api_key_client: tuple[TestClient, dict], audit_log_path: Path
    ) -> None:
        client, record = api_key_client

        client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            content=json.dumps({"name": "Roles Test", "company_name": "Acme"}),
        )

        event = _read_one_event(audit_log_path)
        actor = event["actor"]

        # Deterministic ordering: sorted, not arbitrary set iteration.
        assert actor["roles"] == sorted(record["roles"])

    def test_actor_id_is_not_tenant_display_name(
        self, api_key_client: tuple[TestClient, dict], audit_log_path: Path
    ) -> None:
        """Regression guard: old code set actor_id = tenant_ctx.name."""
        client, record = api_key_client

        client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            content=json.dumps({"name": "Not Name", "company_name": "Acme"}),
        )

        event = _read_one_event(audit_log_path)
        assert event["actor"]["actor_id"] != record["name"]

    def test_roles_do_not_contain_hardcoded_integration_service(
        self, api_key_client: tuple[TestClient, dict], audit_log_path: Path
    ) -> None:
        """Regression guard: old code hardcoded roles=['INTEGRATION_SERVICE']."""
        client, _record = api_key_client

        client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            content=json.dumps({"name": "NoIS", "company_name": "Acme"}),
        )

        event = _read_one_event(audit_log_path)
        assert "INTEGRATION_SERVICE" not in event["actor"]["roles"], (
            "audit must not hardcode INTEGRATION_SERVICE role for authenticated actors"
        )


class TestJwtActorIdentity:
    """Authenticated JWT mutations must record actor_type=HUMAN and JWT claims."""

    def test_jwt_mutation_emits_HUMAN_actor_with_sorted_roles(
        self,
        audit_log_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from idis.api.auth_sso import SsoIdentity

        jwt_tenant_id = str(uuid.uuid4())
        jwt_user_id = f"user-{uuid.uuid4().hex[:12]}"
        jwt_roles = frozenset({"ANALYST", "PARTNER"})

        fake_identity = SsoIdentity(
            tenant_id=jwt_tenant_id,
            user_id=jwt_user_id,
            roles=jwt_roles,
            email="analyst@example.com",
            name="Analyst User",
            data_region="us-east-1",
        )

        def _fake_validate_jwt(token: str, config=None):  # type: ignore[no-untyped-def]
            # Token contents are opaque; this test proxies the validated identity.
            return fake_identity

        # Patch the symbol where auth.py imports it (inside _extract_tenant_from_jwt).
        monkeypatch.setattr(
            "idis.api.auth_sso.validate_jwt", _fake_validate_jwt, raising=True
        )
        # Also clear API-key env so Bearer is the only credential.
        monkeypatch.delenv("IDIS_API_KEYS_JSON", raising=False)
        monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))

        sink = JsonlFileAuditSink(str(audit_log_path))
        app = create_app(audit_sink=sink, service_region="us-east-1")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/v1/deals",
            headers={
                "Authorization": "Bearer fake.jwt.token",
                "Content-Type": "application/json",
            },
            content=json.dumps({"name": "JWT Actor", "company_name": "Acme"}),
        )
        assert response.status_code < 400, response.text

        event = _read_one_event(audit_log_path)
        actor = event["actor"]

        assert actor["actor_type"] == "HUMAN"
        assert actor["actor_id"] == jwt_user_id
        assert actor["roles"] == sorted(jwt_roles)
        # Regression guard as well on the JWT path.
        assert "INTEGRATION_SERVICE" not in actor["roles"]
        # Sanity: tenant_id reflects the JWT, not any API-key registry.
        assert event["tenant_id"] == jwt_tenant_id
