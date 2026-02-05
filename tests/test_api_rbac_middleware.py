"""Tests for IDIS API RBAC/ABAC middleware.

Verifies deny-by-default authorization per v6.3 Security Threat Model:
- AUDITOR cannot perform mutations
- Allowed roles can proceed
- Policy mapping has no gaps vs OpenAPI operationIds
- Admin-only operations are enforced
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.policy import (
    Role,
    get_all_v1_operation_ids,
    policy_check,
)
from idis.audit.sink import JsonlFileAuditSink


def _make_api_keys_json(
    tenant_id: str,
    actor_id: str | None = None,
    name: str = "Test Tenant",
    roles: list[str] | None = None,
) -> str:
    """Create a valid IDIS_API_KEYS_JSON value for testing with roles."""
    if actor_id is None:
        actor_id = f"actor-{tenant_id[:8]}"
    if roles is None:
        roles = [Role.ANALYST.value]
    return json.dumps(
        {
            "test-api-key-rbac": {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


def _get_openapi_spec_path() -> Path:
    """Get the path to the OpenAPI spec file."""
    return Path(__file__).parent.parent / "openapi" / "IDIS_OpenAPI_v6_3.yaml"


def _load_openapi_spec() -> dict[str, Any]:
    """Load the OpenAPI spec from the YAML file."""
    spec_path = _get_openapi_spec_path()
    with open(spec_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_v1_operation_ids(spec: dict[str, Any]) -> set[str]:
    """Extract all /v1 operationIds from the OpenAPI spec."""
    operation_ids: set[str] = set()
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        if not path.startswith("/v1"):
            continue
        if not isinstance(path_item, dict):
            continue

        for method in ["get", "post", "put", "patch", "delete"]:
            operation = path_item.get(method)
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.add(operation["operationId"])

    return operation_ids


class TestAuditorCannotMutate:
    """Test that AUDITOR role cannot perform mutations (HTTP-path tests)."""

    def test_auditor_cannot_create_deal(self, tmp_path: Path) -> None:
        """AUDITOR attempting POST /v1/deals should get 403 RBAC_DENIED."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_rbac.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"name": "Test Deal", "company_name": "Acme Corp"},
            )

            assert response.status_code == 403, f"Expected 403, got {response.status_code}"

            body = response.json()
            assert "code" in body
            assert body["code"] == "RBAC_DENIED"
            assert "message" in body
            assert "details" in body
            assert "request_id" in body

            assert "X-Request-Id" in response.headers
            assert response.headers["X-Request-Id"] == body["request_id"]

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_auditor_cannot_update_deal(self, tmp_path: Path) -> None:
        """AUDITOR attempting PATCH /v1/deals/{dealId} should get 403."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_rbac_patch.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.patch(
                f"/v1/deals/{deal_id}",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"status": "SCREENING"},
            )

            assert response.status_code == 403
            body = response.json()
            assert body["code"] == "RBAC_DENIED"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_auditor_can_read_deals(self, tmp_path: Path) -> None:
        """AUDITOR can perform GET /v1/deals (read operation)."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_rbac_read.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rbac"},
            )

            assert response.status_code == 200

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestAllowedRolesCanProceed:
    """Test that allowed roles can perform mutations (HTTP-path tests)."""

    def test_analyst_can_create_deal(self, tmp_path: Path) -> None:
        """ANALYST can POST /v1/deals successfully."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_analyst.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.ANALYST.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"name": "Test Deal", "company_name": "Acme Corp"},
            )

            assert response.status_code == 201, (
                f"Expected 201, got {response.status_code}: {response.text}"
            )

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_integration_service_can_create_deal(self, tmp_path: Path) -> None:
        """INTEGRATION_SERVICE can POST /v1/deals successfully."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_integration.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.INTEGRATION_SERVICE.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"name": "Integration Deal", "company_name": "Widget Inc"},
            )

            assert response.status_code == 201

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_admin_can_create_deal(self, tmp_path: Path) -> None:
        """ADMIN can POST /v1/deals successfully."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_admin.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id, roles=[Role.ADMIN.value])
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"name": "Admin Deal", "company_name": "Admin Corp"},
            )

            assert response.status_code == 201

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestPolicyMappingNoGaps:
    """Test that POLICY_RULES covers all /v1 OpenAPI operationIds (contract drift guard)."""

    def test_policy_rules_cover_all_v1_operations(self) -> None:
        """POLICY_RULES.keys() must cover all /v1 operationIds in OpenAPI spec."""
        spec = _load_openapi_spec()
        openapi_operation_ids = _extract_v1_operation_ids(spec)
        policy_operation_ids = get_all_v1_operation_ids()

        missing_from_policy = openapi_operation_ids - policy_operation_ids
        assert not missing_from_policy, (
            f"OpenAPI operationIds not covered by POLICY_RULES: {sorted(missing_from_policy)}"
        )

    def test_policy_rules_no_extra_operations(self) -> None:
        """POLICY_RULES should not have extra operations not in OpenAPI spec."""
        spec = _load_openapi_spec()
        openapi_operation_ids = _extract_v1_operation_ids(spec)
        policy_operation_ids = get_all_v1_operation_ids()

        extra_in_policy = policy_operation_ids - openapi_operation_ids
        assert not extra_in_policy, (
            f"POLICY_RULES has operations not in OpenAPI spec: {sorted(extra_in_policy)}"
        )

    def test_policy_rules_exact_match(self) -> None:
        """POLICY_RULES.keys() == OpenAPI /v1 operationIds (exact match)."""
        spec = _load_openapi_spec()
        openapi_operation_ids = _extract_v1_operation_ids(spec)
        policy_operation_ids = get_all_v1_operation_ids()

        assert policy_operation_ids == openapi_operation_ids, (
            f"Policy/OpenAPI mismatch. "
            f"Missing: {sorted(openapi_operation_ids - policy_operation_ids)}, "
            f"Extra: {sorted(policy_operation_ids - openapi_operation_ids)}"
        )


class TestAdminOnlyOperations:
    """Test admin-only operation policy (unit tests)."""

    def test_admin_allowed_create_webhook(self) -> None:
        """ADMIN should be allowed to createWebhook."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="createWebhook",
            method="POST",
        )

        assert decision.allow is True
        assert decision.code == "ALLOWED"

    def test_analyst_denied_create_webhook(self) -> None:
        """ANALYST should be denied createWebhook."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ANALYST.value}),
            operation_id="createWebhook",
            method="POST",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"
        assert "createWebhook" in str(decision.details)

    def test_partner_denied_create_webhook(self) -> None:
        """PARTNER should be denied createWebhook (admin-only)."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.PARTNER.value}),
            operation_id="createWebhook",
            method="POST",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"

    def test_auditor_denied_create_webhook(self) -> None:
        """AUDITOR should be denied createWebhook."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.AUDITOR.value}),
            operation_id="createWebhook",
            method="POST",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"


class TestAuditEventAccess:
    """Test audit event listing access (unit tests)."""

    def test_auditor_allowed_list_audit_events(self) -> None:
        """AUDITOR should be allowed to listAuditEvents."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.AUDITOR.value}),
            operation_id="listAuditEvents",
            method="GET",
        )

        assert decision.allow is True

    def test_admin_allowed_list_audit_events(self) -> None:
        """ADMIN should be allowed to listAuditEvents."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="listAuditEvents",
            method="GET",
        )

        assert decision.allow is True

    def test_analyst_denied_list_audit_events(self) -> None:
        """ANALYST should be denied listAuditEvents (audit readers only)."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ANALYST.value}),
            operation_id="listAuditEvents",
            method="GET",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"


class TestPolicyCheckEdgeCases:
    """Test policy_check edge cases and fail-closed behavior."""

    def test_unknown_operation_denied(self) -> None:
        """Unknown operationId should be denied (fail-closed)."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="unknownOperation",
            method="POST",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"
        assert "unknown_operation" in str(decision.details)

    def test_missing_tenant_id_denied(self) -> None:
        """Missing tenant_id should be denied."""
        decision = policy_check(
            tenant_id="",
            actor_id="actor-123",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="listDeals",
            method="GET",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"

    def test_missing_actor_id_denied(self) -> None:
        """Missing actor_id should be denied."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="listDeals",
            method="GET",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"

    def test_empty_roles_denied(self) -> None:
        """Empty roles set should be denied."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset(),
            operation_id="listDeals",
            method="GET",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"

    def test_multiple_roles_with_one_allowed(self) -> None:
        """Actor with multiple roles should succeed if any role is allowed."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.AUDITOR.value, Role.ADMIN.value}),
            operation_id="createDeal",
            method="POST",
        )

        assert decision.allow is True


class TestOverrideOperationPolicy:
    """Test createOverride policy (Partner or Admin only per v6.3)."""

    def test_partner_allowed_create_override(self) -> None:
        """PARTNER should be allowed to createOverride."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.PARTNER.value}),
            operation_id="createOverride",
            method="POST",
            deal_id="deal-123",
        )

        assert decision.allow is True

    def test_admin_allowed_create_override(self) -> None:
        """ADMIN should be allowed to createOverride."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ADMIN.value}),
            operation_id="createOverride",
            method="POST",
            deal_id="deal-123",
        )

        assert decision.allow is True

    def test_analyst_denied_create_override(self) -> None:
        """ANALYST should be denied createOverride (Partner+ only)."""
        decision = policy_check(
            tenant_id="tenant-123",
            actor_id="actor-123",
            roles=frozenset({Role.ANALYST.value}),
            operation_id="createOverride",
            method="POST",
            deal_id="deal-123",
        )

        assert decision.allow is False
        assert decision.code == "RBAC_DENIED"


class TestRBACMiddlewareIntegration:
    """Integration tests for RBAC middleware behavior."""

    def test_no_roles_returns_403(self, tmp_path: Path) -> None:
        """Actor with no roles should get 403."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_no_roles.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id, roles=[])
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                "/v1/deals",
                headers={"X-IDIS-API-Key": "test-api-key-rbac"},
            )

            assert response.status_code == 403
            body = response.json()
            assert body["code"] == "RBAC_DENIED"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_error_envelope_structure(self, tmp_path: Path) -> None:
        """RBAC denial should return normative error envelope."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_envelope.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-rbac",
                    "Content-Type": "application/json",
                },
                json={"name": "Test", "company_name": "Test"},
            )

            assert response.status_code == 403
            body = response.json()

            assert "code" in body
            assert "message" in body
            assert "details" in body
            assert "request_id" in body

            assert isinstance(body["code"], str)
            assert isinstance(body["message"], str)
            assert body["details"] is None or isinstance(body["details"], dict)
            assert isinstance(body["request_id"], str)

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
