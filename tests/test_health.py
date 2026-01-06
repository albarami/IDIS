"""Tests for the /health endpoint."""

from fastapi.testclient import TestClient

from idis.app import app

client = TestClient(app)


def test_health_returns_200() -> None:
    """Health endpoint should return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_status_ok() -> None:
    """Health endpoint should return status='ok'."""
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"


def test_health_version() -> None:
    """Health endpoint should return version='6.3'."""
    response = client.get("/health")
    data = response.json()
    assert data["version"] == "6.3"


def test_health_time_exists() -> None:
    """Health endpoint should return a non-empty time field."""
    response = client.get("/health")
    data = response.json()
    assert "time" in data
    assert data["time"] is not None
    assert len(data["time"]) > 0
