"""Defect severity tests for DEF-001 traceability.

Tests the severity matrix behavior:
- FATAL: BROKEN_CHAIN, CONCEALMENT, CIRCULARITY
- MAJOR: INCONSISTENCY, ANOMALY_VS_STRONGER_SOURCES, UNKNOWN_SOURCE
- MINOR: STALENESS, UNIT_MISMATCH, TIME_WINDOW_MISMATCH, SCOPE_DRIFT
"""

from __future__ import annotations

import uuid

import pytest

from idis.persistence.repositories.claims import clear_all_claims_stores
from idis.services.defects.service import (
    CreateDefectInput,
    DefectService,
    get_severity_for_type,
)


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()


class TestDefectSeverityMatrix:
    """Tests for DEF-001 severity matrix."""

    def test_fatal_defect_types(self) -> None:
        """FATAL severity for BROKEN_CHAIN, CONCEALMENT, CIRCULARITY."""
        fatal_types = ["BROKEN_CHAIN", "CONCEALMENT", "CIRCULARITY"]

        for defect_type in fatal_types:
            severity = get_severity_for_type(defect_type)
            assert severity == "FATAL", f"{defect_type} should be FATAL"

    def test_major_defect_types(self) -> None:
        """MAJOR severity for INCONSISTENCY, ANOMALY_VS_STRONGER_SOURCES, UNKNOWN_SOURCE."""
        major_types = ["INCONSISTENCY", "ANOMALY_VS_STRONGER_SOURCES", "UNKNOWN_SOURCE"]

        for defect_type in major_types:
            severity = get_severity_for_type(defect_type)
            assert severity == "MAJOR", f"{defect_type} should be MAJOR"

    def test_minor_defect_types(self) -> None:
        """MINOR severity for STALENESS, UNIT_MISMATCH, TIME_WINDOW_MISMATCH, SCOPE_DRIFT."""
        minor_types = ["STALENESS", "UNIT_MISMATCH", "TIME_WINDOW_MISMATCH", "SCOPE_DRIFT"]

        for defect_type in minor_types:
            severity = get_severity_for_type(defect_type)
            assert severity == "MINOR", f"{defect_type} should be MINOR"

    def test_service_applies_severity_matrix_on_create(self) -> None:
        """DefectService.create applies severity matrix."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id)

        fatal_input = CreateDefectInput(
            defect_type="BROKEN_CHAIN",
            description="Chain is broken",
            cure_protocol="RECONSTRUCT_CHAIN",
        )
        fatal_defect = service.create(fatal_input)
        assert fatal_defect["severity"] == "FATAL"

        major_input = CreateDefectInput(
            defect_type="INCONSISTENCY",
            description="Data inconsistent",
            cure_protocol="HUMAN_ARBITRATION",
        )
        major_defect = service.create(major_input)
        assert major_defect["severity"] == "MAJOR"

        minor_input = CreateDefectInput(
            defect_type="STALENESS",
            description="Data outdated",
            cure_protocol="REQUEST_SOURCE",
        )
        minor_defect = service.create(minor_input)
        assert minor_defect["severity"] == "MINOR"

    def test_severity_override_respected(self) -> None:
        """Explicit severity override is respected."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id)

        input_data = CreateDefectInput(
            defect_type="STALENESS",
            severity="MAJOR",
            description="Critical stale data affecting IC decision",
            cure_protocol="REQUEST_SOURCE",
        )

        defect = service.create(input_data)
        assert defect["severity"] == "MAJOR"


class TestDefectCreation:
    """Tests for defect creation with proper fields."""

    def test_defect_created_with_required_fields(self) -> None:
        """Defect has all required fields after creation."""
        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())

        service = DefectService(tenant_id=tenant_id)

        input_data = CreateDefectInput(
            claim_id=claim_id,
            deal_id=deal_id,
            defect_type="BROKEN_CHAIN",
            description="Evidence chain is incomplete",
            cure_protocol="RECONSTRUCT_CHAIN",
        )

        defect = service.create(input_data)

        assert defect["defect_id"] is not None
        assert defect["tenant_id"] == tenant_id
        assert defect["claim_id"] == claim_id
        assert defect["deal_id"] == deal_id
        assert defect["defect_type"] == "BROKEN_CHAIN"
        assert defect["severity"] == "FATAL"
        assert defect["description"] == "Evidence chain is incomplete"
        assert defect["cure_protocol"] == "RECONSTRUCT_CHAIN"
        assert defect["status"] == "OPEN"

    def test_defect_initial_status_is_open(self) -> None:
        """New defects have OPEN status."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id)

        input_data = CreateDefectInput(
            defect_type="INCONSISTENCY",
            description="Test defect",
            cure_protocol="HUMAN_ARBITRATION",
        )

        defect = service.create(input_data)
        assert defect["status"] == "OPEN"
        assert defect["waived"] is False
        assert defect["waived_at"] is None
        assert defect["waived_by"] is None
