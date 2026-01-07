"""Tests for IDIS API health endpoint."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the IDIS API."""
    app = create_app()
    return TestClient(app)


def test_health_returns_200(client: TestClient) -> None:
    """GET /health returns 200 OK."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_contains_required_fields(client: TestClient) -> None:
    """GET /health response contains status, time, and version."""
    response = client.get("/health")
    data = response.json()

    assert "status" in data
    assert "time" in data
    assert "version" in data


def test_health_status_is_ok(client: TestClient) -> None:
    """GET /health returns status 'ok'."""
    response = client.get("/health")
    data = response.json()

    assert data["status"] == "ok"


def test_health_version_is_6_3(client: TestClient) -> None:
    """GET /health returns version '6.3'."""
    response = client.get("/health")
    data = response.json()

    assert data["version"] == "6.3"


def test_health_time_is_iso8601(client: TestClient) -> None:
    """GET /health returns time in ISO-8601 format."""
    response = client.get("/health")
    data = response.json()

    datetime.fromisoformat(data["time"])


def test_health_includes_request_id_header(client: TestClient) -> None:
    """GET /health response includes X-Request-Id header."""
    response = client.get("/health")

    assert "X-Request-Id" in response.headers
    assert len(response.headers["X-Request-Id"]) > 0


def test_health_echoes_provided_request_id(client: TestClient) -> None:
    """GET /health with X-Request-Id header echoes it back."""
    custom_request_id = "test-request-id-12345"
    response = client.get("/health", headers={"X-Request-Id": custom_request_id})

    assert response.headers["X-Request-Id"] == custom_request_id


def test_health_generates_request_id_when_not_provided(client: TestClient) -> None:
    """GET /health generates a UUID request ID when not provided."""
    response = client.get("/health")
    request_id = response.headers["X-Request-Id"]

    assert len(request_id) == 36
    assert request_id.count("-") == 4


def test_health_generates_request_id_for_empty_header(client: TestClient) -> None:
    """GET /health generates a new request ID when provided header is empty."""
    response = client.get("/health", headers={"X-Request-Id": ""})
    request_id = response.headers["X-Request-Id"]

    assert len(request_id) == 36
    assert request_id.count("-") == 4


def test_health_generates_request_id_for_whitespace_header(client: TestClient) -> None:
    """GET /health generates a new request ID when provided header is whitespace."""
    response = client.get("/health", headers={"X-Request-Id": "   "})
    request_id = response.headers["X-Request-Id"]

    assert len(request_id) == 36
    assert request_id.count("-") == 4
