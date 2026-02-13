"""Tests for _build_risks() evidence-link fallback logic.

Verifies that:
1. Risks with empty links + fallback_claim_ids → auto-populated, passes validation.
2. Risks with empty links + empty fallback → still fails closed (ValueError).
3. Risks with existing claim_ids → fallback NOT applied.
4. Auto-populated claim_ids are sorted and limited to 3.
5. WARNING log emitted with token RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from idis.analysis.agents.llm_specialist_agent import _build_risks


def _make_raw_risk(
    *,
    risk_id: str = "risk-01",
    description: str = "Test risk",
    severity: str = "MEDIUM",
    claim_ids: list[str] | None = None,
    calc_ids: list[str] | None = None,
    enrichment_ref_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a raw risk dict matching LLM output shape."""
    result: dict[str, Any] = {
        "risk_id": risk_id,
        "description": description,
        "severity": severity,
    }
    if claim_ids is not None:
        result["claim_ids"] = claim_ids
    if calc_ids is not None:
        result["calc_ids"] = calc_ids
    if enrichment_ref_ids is not None:
        result["enrichment_ref_ids"] = enrichment_ref_ids
    return result


class TestBuildRisksFallback:
    """Tests for _build_risks evidence-link fallback behavior."""

    def test_empty_links_with_fallback_auto_populates(self) -> None:
        """Risk with no evidence links should get fallback claim_ids."""
        raw = _make_raw_risk()
        fallback = ["claim-c", "claim-a", "claim-b"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].claim_ids == ["claim-a", "claim-b", "claim-c"]

    def test_empty_links_no_fallback_fails_closed(self) -> None:
        """Risk with no evidence links and no fallback should raise ValueError."""
        raw = _make_raw_risk()

        with pytest.raises(ValueError, match="must include at least one evidence link"):
            _build_risks([raw], fallback_claim_ids=None)

    def test_empty_links_empty_fallback_fails_closed(self) -> None:
        """Risk with no evidence links and empty fallback list should raise ValueError."""
        raw = _make_raw_risk()

        with pytest.raises(ValueError, match="must include at least one evidence link"):
            _build_risks([raw], fallback_claim_ids=[])

    def test_existing_claim_ids_not_overridden(self) -> None:
        """Risk with existing claim_ids should not be overridden by fallback."""
        raw = _make_raw_risk(claim_ids=["existing-claim"])
        fallback = ["fallback-a", "fallback-b"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].claim_ids == ["existing-claim"]

    def test_existing_calc_ids_not_overridden(self) -> None:
        """Risk with existing calc_ids should not trigger fallback."""
        raw = _make_raw_risk(calc_ids=["calc-01"])
        fallback = ["fallback-a"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].calc_ids == ["calc-01"]
        assert risks[0].claim_ids == []

    def test_existing_enrichment_ref_ids_not_overridden(self) -> None:
        """Risk with existing enrichment_ref_ids should not trigger fallback."""
        raw = _make_raw_risk(enrichment_ref_ids=["enr-01"])
        fallback = ["fallback-a"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].enrichment_ref_ids == ["enr-01"]
        assert risks[0].claim_ids == []

    def test_fallback_sorted_and_limited_to_3(self) -> None:
        """Auto-populated claim_ids should be sorted and limited to first 3."""
        raw = _make_raw_risk()
        fallback = ["z-claim", "a-claim", "m-claim", "b-claim", "x-claim"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].claim_ids == ["a-claim", "b-claim", "m-claim"]

    def test_fallback_with_fewer_than_3_claims(self) -> None:
        """When fallback has fewer than 3 claims, use all of them."""
        raw = _make_raw_risk()
        fallback = ["only-claim"]

        risks = _build_risks([raw], fallback_claim_ids=fallback)

        assert len(risks) == 1
        assert risks[0].claim_ids == ["only-claim"]

    def test_warning_log_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """WARNING with RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED should be logged."""
        raw = _make_raw_risk(risk_id="gap-risk-01")
        fallback = ["claim-a"]

        with caplog.at_level(logging.WARNING):
            _build_risks([raw], fallback_claim_ids=fallback)

        assert any(
            "RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED" in record.message
            for record in caplog.records
        )
        assert any("gap-risk-01" in record.message for record in caplog.records)

    def test_no_warning_when_links_present(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warning should be emitted when risk already has evidence links."""
        raw = _make_raw_risk(claim_ids=["claim-a"])
        fallback = ["fallback-a"]

        with caplog.at_level(logging.WARNING):
            _build_risks([raw], fallback_claim_ids=fallback)

        assert not any(
            "RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED" in record.message
            for record in caplog.records
        )

    def test_multiple_risks_mixed_fallback(self) -> None:
        """Mix of risks with and without links — only empty ones get fallback."""
        raw_with = _make_raw_risk(risk_id="r1", claim_ids=["c1"])
        raw_without = _make_raw_risk(risk_id="r2")
        fallback = ["fb-b", "fb-a"]

        risks = _build_risks([raw_with, raw_without], fallback_claim_ids=fallback)

        assert len(risks) == 2
        assert risks[0].claim_ids == ["c1"]
        assert risks[1].claim_ids == ["fb-a", "fb-b"]

    def test_backward_compat_no_fallback_param(self) -> None:
        """Calling without fallback_claim_ids should work (backward compat)."""
        raw = _make_raw_risk(claim_ids=["c1"])

        risks = _build_risks([raw])

        assert len(risks) == 1
        assert risks[0].claim_ids == ["c1"]
