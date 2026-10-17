"""Pytest configuration and fixtures for IDIS tests.

This module provides common fixtures and configuration for all tests.
"""

from __future__ import annotations

import pytest

from idis.compliance.residency import IDIS_SERVICE_REGION_ENV

TEST_SERVICE_REGION = "me-south-1"


@pytest.fixture(autouse=True)
def set_test_service_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set default service region for all tests.

    Uses "me-south-1" to match the data_region in most test API key configs.
    This ensures residency middleware doesn't block test requests.
    Tests that need to verify residency behavior should override this.
    """
    monkeypatch.setenv(IDIS_SERVICE_REGION_ENV, TEST_SERVICE_REGION)


@pytest.fixture
def test_tenant_data_region() -> str:
    """Return the test tenant data region that matches the service region."""
    return TEST_SERVICE_REGION
