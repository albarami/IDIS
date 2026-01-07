"""Tests for OpenAPI request-body validation middleware.

Phase 2.2: Validates fail-closed behavior for /v1 requests:
- Auth precedence (401 before 400/422)
- JSON parsing (invalid JSON => 400 INVALID_JSON)
- Schema validation (mismatch => 422 INVALID_REQUEST)
"""

import json
import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.openapi_loader import load_openapi_spec


def _find_post_operation_with_required_schema(
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Find the first POST/PUT/PATCH operation with a JSON request body schema.

    The schema must be type=object with a non-empty 'required' list.

    Returns:
        (path_template, schema) tuple.

    Raises:
        ValueError: If no suitable operation found.
    """
    paths = spec.get("paths", {})
    methods = ["post", "put", "patch"]

    for path_template in sorted(paths.keys()):
        if not path_template.startswith("/v1"):
            continue

        path_item = paths[path_template]
        if not isinstance(path_item, dict):
            continue

        for method in methods:
            if method not in path_item:
                continue

            operation = path_item[method]
            if not isinstance(operation, dict):
                continue

            request_body = operation.get("requestBody")
            if not isinstance(request_body, dict):
                continue

            content = request_body.get("content", {})
            if "application/json" not in content:
                continue

            media_type = content["application/json"]
            if not isinstance(media_type, dict):
                continue

            schema = media_type.get("schema")
            if not isinstance(schema, dict):
                continue

            resolved_schema = _resolve_schema_ref(spec, schema)
            if resolved_schema is None:
                continue

            if resolved_schema.get("type") != "object":
                continue

            required = resolved_schema.get("required", [])
            if not isinstance(required, list) or len(required) == 0:
                continue

            return (path_template, resolved_schema)

    raise ValueError("No POST/PUT/PATCH operation with required JSON schema found")


def _resolve_schema_ref(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve $ref in schema."""
    if "$ref" not in schema:
        return schema

    ref = schema["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None

    parts = ref[2:].split("/")
    current: Any = spec

    for part in parts:
        decoded = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(decoded)
        else:
            return None
        if current is None:
            return None

    return current if isinstance(current, dict) else None


def _substitute_path_params(path_template: str) -> str:
    """Substitute {param} segments with UUID strings."""
    import re

    def replacer(match: re.Match[str]) -> str:
        return str(uuid.uuid4())

    return re.sub(r"\{[^}]+\}", replacer, path_template)


@pytest.fixture
def api_key_env() -> str:
    """Set up test API key in environment and return the key."""
    test_key = "test-api-key-phase2-validation"
    tenant_id = str(uuid.uuid4())
    test_tenant = {
        "tenant_id": tenant_id,
        "actor_id": f"actor-{tenant_id[:8]}",
        "name": "Test Tenant",
        "timezone": "UTC",
        "data_region": "us-east-1",
    }
    keys_json = json.dumps({test_key: test_tenant})
    os.environ["IDIS_API_KEYS_JSON"] = keys_json
    yield test_key
    if "IDIS_API_KEYS_JSON" in os.environ:
        del os.environ["IDIS_API_KEYS_JSON"]


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the IDIS API."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def spec() -> dict[str, Any]:
    """Load the OpenAPI spec."""
    return load_openapi_spec()


@pytest.fixture
def target_path_and_schema(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Find target path and schema for validation tests."""
    return _find_post_operation_with_required_schema(spec)


class TestUnauthorizedWins:
    """A) Unauthorized wins - 401 before any JSON/schema validation."""

    def test_post_without_api_key_returns_401(
        self, client: TestClient, spec: dict[str, Any]
    ) -> None:
        """POST to /v1 path without X-IDIS-API-Key returns 401 unauthorized."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path, json={"invalid": "body"}, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "unauthorized"
        assert "request_id" in data

    def test_401_request_id_matches_header(self, client: TestClient, spec: dict[str, Any]) -> None:
        """401 response request_id matches X-Request-Id header."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path, json={"invalid": "body"}, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 401
        data = response.json()
        assert data["request_id"] == response.headers["X-Request-Id"]

    def test_401_even_with_invalid_json_body(
        self, client: TestClient, spec: dict[str, Any]
    ) -> None:
        """401 returned even when request body is invalid JSON (auth first)."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(path, content="{", headers={"Content-Type": "application/json"})

        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "unauthorized"

    def test_401_for_unimplemented_v1_route(self, client: TestClient) -> None:
        """401 returned for unimplemented /v1 route (auth before 404)."""
        response = client.post(
            "/v1/nonexistent/route",
            json={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "unauthorized"


class TestInvalidJson:
    """B) Invalid JSON - 400 INVALID_JSON when authorized."""

    def test_invalid_json_returns_400(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """POST with invalid JSON body returns 400 INVALID_JSON."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            content="{",
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_JSON"
        assert "request_id" in data

    def test_truncated_json_returns_400(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """POST with truncated JSON returns 400 INVALID_JSON."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            content='{"name": "test',
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_JSON"

    def test_non_json_string_returns_400(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """POST with non-JSON string returns 400 INVALID_JSON."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            content="not json at all",
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_JSON"


class TestSchemaInvalid:
    """C) Schema invalid - 422 INVALID_REQUEST when body doesn't match schema."""

    def test_empty_object_missing_required_returns_422(
        self,
        client: TestClient,
        api_key_env: str,
        target_path_and_schema: tuple[str, dict[str, Any]],
    ) -> None:
        """POST with empty {} body to operation with required fields returns 422."""
        path_template, schema = target_path_and_schema
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            json={},
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert "request_id" in data
        assert "details" in data
        assert "path" in data["details"]
        assert "message" in data["details"]

    def test_422_includes_deterministic_error_path(
        self,
        client: TestClient,
        api_key_env: str,
        target_path_and_schema: tuple[str, dict[str, Any]],
    ) -> None:
        """422 response includes deterministic error path (first missing required)."""
        path_template, schema = target_path_and_schema
        path = _substitute_path_params(path_template)

        required_fields = sorted(schema.get("required", []))

        response = client.post(
            path,
            json={},
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 422
        data = response.json()

        if required_fields:
            expected_field = required_fields[0]
            assert f"/{expected_field}" in data["details"]["path"]


class TestContentType:
    """Content-Type validation tests."""

    def test_wrong_content_type_returns_415(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """POST with wrong Content-Type returns 415 INVALID_CONTENT_TYPE."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            content="name=test",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 415
        data = response.json()
        assert data["code"] == "INVALID_CONTENT_TYPE"


class TestSafetyRegression:
    """D) Safety regression - middleware intercepts before route handlers."""

    def test_validation_works_without_implemented_route(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """Validation errors occur even when route handler is not implemented.

        This tests that middleware runs before the route matching, so we get
        proper validation errors (400/422) instead of just 404.
        """
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            content="{invalid json",
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_JSON"

    def test_schema_validation_before_route(
        self,
        client: TestClient,
        api_key_env: str,
        target_path_and_schema: tuple[str, dict[str, Any]],
    ) -> None:
        """Schema validation errors occur before route 404."""
        path_template, _ = target_path_and_schema
        path = _substitute_path_params(path_template)

        response = client.post(
            path,
            json={},
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"


class TestHealthEndpointBypass:
    """Verify /health is not affected by /v1 validation middleware."""

    def test_health_endpoint_not_affected(self, client: TestClient) -> None:
        """GET /health works without API key (not a /v1 path)."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestRequestIdPropagation:
    """Verify request ID is propagated through validation errors."""

    def test_custom_request_id_in_401_response(
        self, client: TestClient, spec: dict[str, Any]
    ) -> None:
        """Custom X-Request-Id is included in 401 response."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)
        custom_id = "custom-request-id-401-test"

        response = client.post(
            path,
            json={},
            headers={"Content-Type": "application/json", "X-Request-Id": custom_id},
        )

        assert response.status_code == 401
        data = response.json()
        assert data["request_id"] == custom_id
        assert response.headers["X-Request-Id"] == custom_id

    def test_custom_request_id_in_400_response(
        self, client: TestClient, api_key_env: str, spec: dict[str, Any]
    ) -> None:
        """Custom X-Request-Id is included in 400 response."""
        path_template, _ = _find_post_operation_with_required_schema(spec)
        path = _substitute_path_params(path_template)
        custom_id = "custom-request-id-400-test"

        response = client.post(
            path,
            content="{",
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
                "X-Request-Id": custom_id,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["request_id"] == custom_id

    def test_custom_request_id_in_422_response(
        self,
        client: TestClient,
        api_key_env: str,
        target_path_and_schema: tuple[str, dict[str, Any]],
    ) -> None:
        """Custom X-Request-Id is included in 422 response."""
        path_template, _ = target_path_and_schema
        path = _substitute_path_params(path_template)
        custom_id = "custom-request-id-422-test"

        response = client.post(
            path,
            json={},
            headers={
                "Content-Type": "application/json",
                "X-IDIS-API-Key": api_key_env,
                "X-Request-Id": custom_id,
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert data["request_id"] == custom_id
