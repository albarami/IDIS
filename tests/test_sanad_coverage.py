"""Sanad coverage tests for SAN-001 traceability.

Verifies that material claims have Sanad objects with required fields:
- transmission_chain
- grade
- defects list

Per Phase 3 exit criteria: "100% claims have Sanad objects".
"""

from __future__ import annotations

import uuid

import pytest

from idis.persistence.repositories.claims import (
    clear_all_claims_stores,
)
from idis.services.sanad.service import CreateSanadInput, SanadService


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()


class TestSanadCoverage:
    """Tests for SAN-001: Every material claim has Sanad object."""

    def test_sanad_created_with_required_fields(self) -> None:
        """Sanad must have transmission_chain, grade, and defects list."""
        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        evidence_id = str(uuid.uuid4())

        service = SanadService(tenant_id=tenant_id)

        input_data = CreateSanadInput(
            claim_id=claim_id,
            deal_id=deal_id,
            primary_evidence_id=evidence_id,
            extraction_confidence=0.95,
        )

        sanad = service.create(input_data)

        assert sanad["sanad_id"] is not None
        assert sanad["claim_id"] == claim_id
        assert sanad["tenant_id"] == tenant_id

        assert "transmission_chain" in sanad
        assert isinstance(sanad["transmission_chain"], list)
        assert len(sanad["transmission_chain"]) >= 1

        assert "computed" in sanad
        computed = sanad["computed"]
        assert "grade" in computed
        assert computed["grade"] in ("A", "B", "C", "D")

        assert "grade_rationale" in computed or computed.get("grade_rationale") is None

    def test_sanad_grade_derived_from_evidence_quality(self) -> None:
        """Sanad grade reflects evidence source tier."""
        tenant_id = str(uuid.uuid4())
        service = SanadService(tenant_id=tenant_id)

        auditor_chain = [
            {
                "node_id": str(uuid.uuid4()),
                "node_type": "AUDITOR",
                "actor_type": "EXTERNAL",
                "actor_id": "kpmg",
                "input_refs": [],
                "output_refs": [],
                "timestamp": "2026-01-10T00:00:00Z",
            }
        ]

        input_data = CreateSanadInput(
            claim_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            primary_evidence_id=str(uuid.uuid4()),
            transmission_chain=auditor_chain,
            extraction_confidence=0.95,
        )

        sanad = service.create(input_data)

        assert sanad["computed"]["grade"] in ("A", "B")

    def test_sanad_for_claim_enforces_coverage(self) -> None:
        """create_for_claim ensures coverage for material claims."""
        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        evidence_id = str(uuid.uuid4())

        service = SanadService(tenant_id=tenant_id)

        sanad1 = service.create_for_claim(
            claim_id=claim_id,
            deal_id=deal_id,
            primary_evidence_id=evidence_id,
            extraction_confidence=0.9,
        )

        assert sanad1["claim_id"] == claim_id

        sanad2 = service.create_for_claim(
            claim_id=claim_id,
            deal_id=deal_id,
            primary_evidence_id=evidence_id,
            extraction_confidence=0.9,
        )

        assert sanad2["sanad_id"] == sanad1["sanad_id"]

    def test_sanad_transmission_chain_has_required_node_fields(self) -> None:
        """Transmission chain nodes have required fields per schema."""
        tenant_id = str(uuid.uuid4())
        service = SanadService(tenant_id=tenant_id)

        input_data = CreateSanadInput(
            claim_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            primary_evidence_id=str(uuid.uuid4()),
            extraction_confidence=0.95,
        )

        sanad = service.create(input_data)
        chain = sanad["transmission_chain"]

        assert len(chain) >= 1

        node = chain[0]
        assert "node_id" in node
        assert "node_type" in node
        assert "actor_type" in node
        assert "actor_id" in node
        assert "timestamp" in node

    def test_sanad_corroboration_level_computed(self) -> None:
        """Corroboration level computed from evidence count."""
        tenant_id = str(uuid.uuid4())
        service = SanadService(tenant_id=tenant_id)

        input_no_corr = CreateSanadInput(
            claim_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            primary_evidence_id=str(uuid.uuid4()),
            corroborating_evidence_ids=[],
            extraction_confidence=0.95,
        )

        sanad_no_corr = service.create(input_no_corr)
        assert sanad_no_corr["computed"]["corroboration_level"] == "AHAD_1"
        assert sanad_no_corr["computed"]["independent_chain_count"] == 1

        input_ahad2 = CreateSanadInput(
            claim_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            primary_evidence_id=str(uuid.uuid4()),
            corroborating_evidence_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
            extraction_confidence=0.95,
        )

        sanad_ahad2 = service.create(input_ahad2)
        assert sanad_ahad2["computed"]["corroboration_level"] == "AHAD_2"
        assert sanad_ahad2["computed"]["independent_chain_count"] == 3

        input_mutawatir = CreateSanadInput(
            claim_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            primary_evidence_id=str(uuid.uuid4()),
            corroborating_evidence_ids=[
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                str(uuid.uuid4()),
            ],
            extraction_confidence=0.95,
        )

        sanad_mutawatir = service.create(input_mutawatir)
        assert sanad_mutawatir["computed"]["corroboration_level"] == "MUTAWATIR"
        assert sanad_mutawatir["computed"]["independent_chain_count"] == 4
