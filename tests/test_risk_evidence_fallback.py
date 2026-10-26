"""Tests for _build_risks() evidence-link validation.

A risk is built from the links the model returned. A risk with no links is
rejected. Parent supported claim ids are not copied onto it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from idis.analysis.agents.llm_specialist_agent import _build_risks, run_specialist_agent
from idis.analysis.models import AnalysisContext


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


class TestBuildRisksEvidenceLinks:
    """Tests for _build_risks evidence-link behaviour."""

    def test_empty_links_are_rejected(self) -> None:
        """A risk with no evidence links raises ValueError."""
        raw = _make_raw_risk()

        with pytest.raises(ValueError, match="must include at least one evidence link"):
            _build_risks([raw])

    def test_explicit_empty_link_lists_are_rejected(self) -> None:
        """Explicit empty link lists are rejected by the same validation."""
        raw = _make_raw_risk(claim_ids=[], calc_ids=[], enrichment_ref_ids=[])

        with pytest.raises(ValueError, match="must include at least one evidence link"):
            _build_risks([raw])

    def test_existing_claim_ids_are_kept(self) -> None:
        """A risk keeps the claim_ids the model returned."""
        raw = _make_raw_risk(claim_ids=["existing-claim"])

        risks = _build_risks([raw])

        assert len(risks) == 1
        assert risks[0].claim_ids == ["existing-claim"]

    def test_existing_calc_ids_are_kept(self) -> None:
        """A risk with calc_ids keeps those calc_ids and an empty claim list."""
        raw = _make_raw_risk(calc_ids=["calc-01"])

        risks = _build_risks([raw])

        assert len(risks) == 1
        assert risks[0].calc_ids == ["calc-01"]
        assert risks[0].claim_ids == []

    def test_existing_enrichment_ref_ids_are_kept(self) -> None:
        """A risk with enrichment_ref_ids keeps those ids and an empty claim list."""
        raw = _make_raw_risk(enrichment_ref_ids=["enr-01"])

        risks = _build_risks([raw])

        assert len(risks) == 1
        assert risks[0].enrichment_ref_ids == ["enr-01"]
        assert risks[0].claim_ids == []

    def test_no_warning_when_links_present(self, caplog: pytest.LogCaptureFixture) -> None:
        """A linked risk does not emit the removed auto-population warning."""
        raw = _make_raw_risk(claim_ids=["claim-a"])

        with caplog.at_level(logging.WARNING):
            _build_risks([raw])

        assert not any(
            "RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED" in record.message
            for record in caplog.records
        )

    def test_unlinked_risk_in_a_batch_is_rejected(self) -> None:
        """One unlinked risk rejects the batch instead of receiving copied ids."""
        raw_with = _make_raw_risk(risk_id="r1", claim_ids=["c1"])
        raw_without = _make_raw_risk(risk_id="r2")

        with pytest.raises(ValueError, match="must include at least one evidence link"):
            _build_risks([raw_with, raw_without])

    def test_linked_risk_keeps_its_claim_ids(self) -> None:
        """A linked risk is constructed from its own claim_ids."""
        raw = _make_raw_risk(claim_ids=["c1"])

        risks = _build_risks([raw])

        assert len(risks) == 1
        assert risks[0].claim_ids == ["c1"]


class _FixedJsonClient:
    """LLM client that returns one prepared JSON document."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._payload


def _unlinked_risk_response() -> str:
    """Specialist JSON whose risk has no links while the report cites claims."""
    data: dict[str, Any] = {
        "supported_claim_ids": ["claim-c", "claim-a", "claim-b", "claim-d"],
        "supported_calc_ids": ["calc-parent"],
        "analysis_sections": {"summary": "Parent analysis."},
        "risks": [
            {
                "risk_id": "risk-unlinked",
                "description": "A risk returned without evidence links.",
                "severity": "MEDIUM",
            }
        ],
        "questions_for_founder": ["What evidence supports this risk?"],
        "confidence": 0.4,
        "confidence_justification": "The model omitted evidence links on the risk.",
        "muhasabah": {
            "agent_id": "financial-agent-01",
            "output_id": "fin-output-001",
            "supported_claim_ids": ["claim-a"],
            "supported_calc_ids": ["calc-parent"],
            "evidence_summary": "The parent report cites claims.",
            "counter_hypothesis": "The risk may have no supporting source.",
            "falsifiability_tests": [],
            "uncertainties": [],
            "failure_modes": [],
            "confidence": 0.4,
            "confidence_justification": "Links were omitted on the risk.",
            "timestamp": "2026-10-19T00:00:00Z",
            "is_subjective": False,
        },
        "enrichment_ref_ids": [],
    }
    return json.dumps(data)


class TestUnlinkedRiskRejected:
    """An unlinked risk stays unlinked and fails evidence-link validation."""

    def test_unlinked_risk_is_not_auto_populated_and_is_rejected(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Parent supported claim ids are not copied onto a risk that has none."""
        prompt_path = tmp_path / "specialist.txt"
        prompt_path.write_text("Analyze the deal.", encoding="utf-8")
        ctx = AnalysisContext(
            deal_id="deal-001",
            tenant_id="tenant-001",
            run_id="run-001",
            claim_ids=frozenset({"claim-a", "claim-b", "claim-c", "claim-d"}),
            calc_ids=frozenset({"calc-parent"}),
        )

        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(ValueError, match="must include at least one evidence link"),
        ):
            run_specialist_agent(
                agent_id="financial-agent-01",
                agent_type="financial_agent",
                llm_client=_FixedJsonClient(_unlinked_risk_response()),
                prompt_path=prompt_path,
                ctx=ctx,
            )

        assert not any(
            "RISK_MISSING_EVIDENCE_LINKS_AUTOPOPULATED" in record.message
            for record in caplog.records
        )
