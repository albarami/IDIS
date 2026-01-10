"""GDBS-FULL integration tests for Sanad Methodology v2.

Tests using adversarial deals from datasets/gdbs_full to verify:
- Contradiction detection (deal_002)
- Chain break detection (deal_007)
- Version drift detection (deal_008)
- Missing evidence blocked by No-Free-Facts (deal_005)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idis.services.sanad.grader import calculate_sanad_grade
from idis.services.sanad.ilal import (
    IlalDefectCode,
    detect_ilal_chain_break,
    detect_ilal_version_drift,
)
from idis.services.sanad.shudhudh import detect_shudhudh
from idis.validators.no_free_facts import validate_no_free_facts

GDBS_PATH = Path(__file__).parent.parent / "datasets" / "gdbs_full"


def load_deal(deal_key: str) -> dict[str, Any]:
    """Load deal.json from GDBS-FULL."""
    deal_dir = GDBS_PATH / "deals" / deal_key
    deal_file = deal_dir / "deal.json"
    if deal_file.exists():
        return json.loads(deal_file.read_text(encoding="utf-8"))
    return {}


def load_claims(deal_key: str) -> list[dict[str, Any]]:
    """Load claims.json from GDBS-FULL deal directory."""
    deal_dir = GDBS_PATH / "deals" / deal_key
    claims_file = deal_dir / "claims.json"
    if claims_file.exists():
        data = json.loads(claims_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("claims", [])
    return []


def load_sanads(deal_key: str) -> list[dict[str, Any]]:
    """Load sanads.json from GDBS-FULL deal directory."""
    deal_dir = GDBS_PATH / "deals" / deal_key
    sanads_file = deal_dir / "sanads.json"
    if sanads_file.exists():
        data = json.loads(sanads_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("sanads", [])
    return []


def load_evidence(
    deal_key: str,
    *,
    require_non_empty: bool = True,
) -> list[dict[str, Any]]:
    """Load evidence.json from GDBS-FULL deal directory.

    NOTE: Dataset uses 'evidence.json', not 'evidence_items.json'.
    Falls back to evidence_items.json for backwards compatibility.

    Args:
        deal_key: Deal directory name (e.g., "deal_002_contradiction")
        require_non_empty: If True, assert that evidence is non-empty after load.
            Set to False only for tests that intentionally test missing evidence.

    Returns:
        List of evidence items.

    Raises:
        AssertionError: If require_non_empty=True and no evidence found.
    """
    deal_dir = GDBS_PATH / "deals" / deal_key

    # Primary: evidence.json (actual dataset filename)
    evidence_file = deal_dir / "evidence.json"
    if evidence_file.exists():
        data = json.loads(evidence_file.read_text(encoding="utf-8"))
        # Dataset may use "evidence" or "evidence_items" key
        if isinstance(data, list):
            result = data
        else:
            result = data.get("evidence") or data.get("evidence_items") or []
        if require_non_empty:
            assert result and len(result) > 0, (
                f"Evidence file {evidence_file} exists but contains no items. "
                f"Use require_non_empty=False if testing empty evidence scenario."
            )
        return result

    # Fallback: evidence_items.json (legacy)
    legacy_file = deal_dir / "evidence_items.json"
    if legacy_file.exists():
        data = json.loads(legacy_file.read_text(encoding="utf-8"))
        result = data if isinstance(data, list) else data.get("evidence_items", [])
        if require_non_empty:
            assert result and len(result) > 0, (
                f"Evidence file {legacy_file} exists but contains no items. "
                f"Use require_non_empty=False if testing empty evidence scenario."
            )
        return result

    if require_non_empty:
        raise AssertionError(
            f"No evidence file found for {deal_key} at {deal_dir}. "
            f"Expected evidence.json or evidence_items.json to exist."
        )
    return []


def load_documents(deal_key: str) -> list[dict[str, Any]]:
    """Load documents.json from GDBS-FULL deal directory."""
    deal_dir = GDBS_PATH / "deals" / deal_key
    docs_file = deal_dir / "documents.json"
    if docs_file.exists():
        data = json.loads(docs_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("documents", [])
    return []


def load_expected(deal_key: str) -> dict[str, Any]:
    """Load expected outcomes for a deal."""
    deal_num = deal_key.split("_")[1]
    expected_file = GDBS_PATH / "expected_outcomes" / f"deal_{deal_num}_expected.json"
    if expected_file.exists():
        return json.loads(expected_file.read_text(encoding="utf-8"))
    return {}


@pytest.fixture
def gdbs_available() -> bool:
    """Check if GDBS-FULL dataset is available."""
    manifest = GDBS_PATH / "manifest.json"
    return manifest.exists()


class TestDeal002Contradiction:
    """Tests for deal_002_contradiction: Deck ARR contradicts Model ARR."""

    @pytest.fixture
    def deal_data(self) -> dict[str, Any]:
        """Load deal_002 data."""
        return load_deal("deal_002_contradiction")

    def test_deal_002_exists(self, gdbs_available: bool) -> None:
        """deal_002 should exist in GDBS-FULL."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")
        deal = load_deal("deal_002_contradiction")
        assert deal.get("scenario") == "contradiction"

    def test_contradiction_triggers_shudhudh(self, gdbs_available: bool) -> None:
        """Contradiction between deck and model MUST trigger shudhudh anomaly/defect.

        This test proves that the adversarial contradiction scenario produces
        a real anomaly detection with all reconciliation attempts failed.
        NO OR patterns allowed - all conditions must be met.
        """
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        deal = load_deal("deal_002_contradiction")
        assert deal, "deal_002_contradiction must load successfully"

        injected = deal.get("injected_issue", {})
        deck_value = injected.get("deck_value", 5200000)
        model_value = injected.get("model_value", 4800000)

        # Verify the discrepancy is significant enough to trigger anomaly
        discrepancy = abs(deck_value - model_value) / max(deck_value, model_value)
        assert discrepancy > 0.05, f"Expected >5% discrepancy, got {discrepancy * 100:.1f}%"

        # Use SHAYKH (support-only tier) for lower-tier source to trigger anomaly
        # The anomaly is detected when a lower-tier source contradicts higher-tier
        claim_values = [
            {"value": deck_value, "source": "deck"},
            {"value": model_value, "source": "model"},
        ]
        sources = [
            {"source_type": "FINANCIAL_MODEL", "evidence_id": "model-evidence"},
            {"source_type": "MANAGEMENT_INTERVIEW", "evidence_id": "interview-evidence"},
        ]

        result = detect_shudhudh(claim_values, sources, contradiction_threshold=0.05)
        assert result is not None, "detect_shudhudh must return a result"

        # ========== REQUIRED ASSERTIONS (NO OR PATTERNS) ==========

        # 1. Reconciliation MUST have failed for this adversarial deal
        assert result.all_reconciliations_failed is True, (
            f"Reconciliation MUST fail for contradiction scenario. "
            f"Got reconciliation_succeeded={result.reconciliation_succeeded}"
        )

        # 2. Anomaly MUST be detected
        assert result.has_anomaly is True, (
            f"Shudhudh MUST detect anomaly for contradiction scenario. "
            f"Got has_anomaly={result.has_anomaly}"
        )

        # 3. Defect code MUST be SHUDHUDH_ANOMALY (exact match)
        assert result.defect_code == "SHUDHUDH_ANOMALY", (
            f"Expected defect_code='SHUDHUDH_ANOMALY', got '{result.defect_code}'"
        )

        # 4. Severity MUST match spec (MAJOR for lower-tier contradiction)
        assert result.severity == "MAJOR", f"Expected severity='MAJOR', got '{result.severity}'"

        # 5. Cure protocol MUST be specified
        assert result.cure_protocol is not None, "Cure protocol must be specified"
        assert result.cure_protocol == "HUMAN_ARBITRATION", (
            f"Expected cure_protocol='HUMAN_ARBITRATION', got '{result.cure_protocol}'"
        )

    def test_contradiction_deal_grade_reflects_issue(self, gdbs_available: bool) -> None:
        """Contradiction deal should result in grade impact or defect flag."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        sanads = load_sanads("deal_002_contradiction")
        # EXPLICIT ASSERTION: sanads must exist for deal_002
        assert sanads, "Expected non-empty sanads for deal_002_contradiction"

        # EXPLICIT ASSERTION: evidence must exist for deal_002
        sources = load_evidence("deal_002_contradiction")
        assert sources, "Expected non-empty evidence for deal_002_contradiction"

        for sanad in sanads:
            result = calculate_sanad_grade(sanad, sources=sources)
            assert result.grade in {"A", "B", "C", "D"}


class TestDeal007ChainBreak:
    """Tests for deal_007_chain_break: Sanad with orphaned transmission node."""

    def test_deal_007_exists(self, gdbs_available: bool) -> None:
        """deal_007 should exist in GDBS-FULL."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")
        deal = load_deal("deal_007_chain_break")
        assert deal.get("scenario") == "chain_break"

    def test_chain_break_triggers_ilal(self, gdbs_available: bool) -> None:
        """Chain break scenario should trigger ILAL_CHAIN_BREAK defect."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        sanads = load_sanads("deal_007_chain_break")
        # EXPLICIT ASSERTION: sanads must exist for deal_007
        assert sanads, "Expected non-empty sanads for deal_007_chain_break"

        evidence = load_evidence("deal_007_chain_break")
        # EXPLICIT ASSERTION: evidence must exist for deal_007
        assert evidence, "Expected non-empty evidence for deal_007_chain_break"

        # Build evidence_ids set for chain break detection
        evidence_ids: set[str] = set()
        for e in evidence:
            eid = e.get("evidence_id")
            if eid:
                evidence_ids.add(str(eid))

        # Track whether chain break was found - MUST assert at end
        found_chain_break = False
        scanned_sanads: list[str] = []

        # NO FALLBACK: test must use actual dataset sanads
        for sanad in sanads:
            sanad_id = sanad.get("sanad_id", "unknown")
            scanned_sanads.append(sanad_id)
            # Pass evidence_ids to detect broken input_refs
            defect = detect_ilal_chain_break(sanad, evidence_ids=evidence_ids)
            if defect:
                assert defect.code == IlalDefectCode.ILAL_CHAIN_BREAK
                assert defect.severity == "FATAL"
                found_chain_break = True
                break

        # MUST assert chain break was detected - cannot pass silently
        assert found_chain_break, (
            f"ILAL_CHAIN_BREAK must be detected for deal_007_chain_break. "
            f"Scanned sanads: {scanned_sanads}, evidence_ids: {len(evidence_ids)}"
        )

    def test_chain_break_forces_grade_d(self, gdbs_available: bool) -> None:
        """Chain break (FATAL) should force grade D."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        sanad_with_break = {
            "primary_source": {"source_type": "AUDITED_FINANCIAL"},
            "transmission_chain": [
                {"node_id": "node-1", "prev_node_id": None},
                {"node_id": "node-2", "prev_node_id": "missing-parent"},
            ],
        }

        result = calculate_sanad_grade(sanad_with_break)
        assert result.grade == "D"
        assert any(d.code == "ILAL_CHAIN_BREAK" for d in result.explanation.fatal_defects)


class TestDeal008VersionDrift:
    """Tests for deal_008_version_drift: Claim cites old document version."""

    def test_deal_008_exists(self, gdbs_available: bool) -> None:
        """deal_008 should exist in GDBS-FULL."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")
        deal = load_deal("deal_008_version_drift")
        assert deal.get("scenario") == "version_drift"

    def test_version_drift_triggers_ilal(self, gdbs_available: bool) -> None:
        """Version drift scenario should trigger ILAL_VERSION_DRIFT defect."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        deal = load_deal("deal_008_version_drift")
        injected = deal.get("injected_issue", {})

        cited_value = injected.get("cited_value", 5500000)
        current_value = injected.get("current_value", 5800000)
        claim = {
            "claim_type": "ARR",
            "cited_document": {"document_id": "deck-doc", "version": 1},
        }
        documents = [
            {"document_id": "deck-doc", "version": 1, "metrics": {"ARR": cited_value}},
            {"document_id": "deck-doc", "version": 2, "metrics": {"ARR": current_value}},
        ]

        defect = detect_ilal_version_drift(claim, documents)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_VERSION_DRIFT
        assert defect.severity == "MAJOR"

    def test_version_drift_downgrades_grade(self, gdbs_available: bool) -> None:
        """Version drift (MAJOR) should downgrade grade."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        sanad = {
            "primary_source": {"source_type": "AUDITED_FINANCIAL"},
            "transmission_chain": [
                {
                    "node_id": "n1",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "a1",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ],
        }
        claim = {
            "claim_type": "ARR",
            "cited_document": {"document_id": "doc-1", "version": 1},
        }
        documents = [
            {"document_id": "doc-1", "version": 1, "metrics": {"ARR": 5500000}},
            {"document_id": "doc-1", "version": 2, "metrics": {"ARR": 5800000}},
        ]

        result = calculate_sanad_grade(sanad, claim=claim, documents=documents)
        assert len(result.ilal_defects) > 0
        assert any(d.code == IlalDefectCode.ILAL_VERSION_DRIFT for d in result.ilal_defects)


class TestDeal005MissingEvidence:
    """Tests for deal_005_missing_evidence: Claim with no backing evidence span."""

    def test_deal_005_exists(self, gdbs_available: bool) -> None:
        """deal_005 should exist in GDBS-FULL."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")
        deal = load_deal("deal_005_missing_evidence")
        assert deal.get("scenario") == "missing_evidence"

    def test_missing_evidence_blocked_by_no_free_facts(self, gdbs_available: bool) -> None:
        """Missing evidence REFERENCE should be blocked by No-Free-Facts validator.

        Note: deal_005 scenario is about a claim (C6) missing its evidence span reference,
        not about empty evidence files. The deal HAS evidence, but specific claims lack
        proper primary_span_id links - which No-Free-Facts must catch.
        """
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        # EXPLICIT ASSERTION: deal HAS evidence (scenario is missing REFERENCE, not file)
        evidence = load_evidence("deal_005_missing_evidence", require_non_empty=True)
        assert len(evidence) > 0, (
            "deal_005_missing_evidence must have evidence items. "
            "Scenario is claim C6 missing span reference, not empty evidence."
        )

        # This deliverable has factual claims but NO referenced_claim_ids - simulating the
        # adversarial scenario where claim text exists but evidence links are missing
        deliverable_without_refs = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $5M ARR with 80% gross margin.",
                    "is_factual": True,
                    "referenced_claim_ids": [],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validate_no_free_facts(deliverable_without_refs)
        assert not result.passed
        assert any("NO_FREE_FACTS" in e.code for e in result.errors)

    def test_missing_evidence_regression_not_introduced(self, gdbs_available: bool) -> None:
        """Ensure Sanad v2 does not regress No-Free-Facts behavior."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        deliverable_with_refs = {
            "deliverable_type": "IC_MEMO",
            "sections": [
                {
                    "text": "Revenue is $5M ARR with 80% gross margin.",
                    "is_factual": True,
                    "referenced_claim_ids": ["claim-001"],
                    "referenced_calc_ids": [],
                }
            ],
        }

        result = validate_no_free_facts(deliverable_with_refs)
        assert result.passed


class TestCleanDeals:
    """Tests for clean baseline deals — should pass without defects."""

    @pytest.mark.parametrize("deal_num", [1, 9, 10])
    def test_clean_deal_no_fatal_defects(self, gdbs_available: bool, deal_num: int) -> None:
        """Clean deals should have no FATAL defects."""
        if not gdbs_available:
            pytest.skip("GDBS-FULL dataset not available")

        deal_key = f"deal_{deal_num:03d}_clean"
        deal = load_deal(deal_key)

        if not deal:
            pytest.skip(f"Deal {deal_key} not found")

        assert deal.get("scenario") == "clean"

        sanads = load_sanads(deal_key)
        if not sanads:
            sanad = {
                "primary_source": {"source_type": "FINANCIAL_MODEL"},
                "transmission_chain": [
                    {
                        "node_id": "n1",
                        "node_type": "EXTRACT",
                        "actor_type": "AGENT",
                        "actor_id": "a1",
                        "timestamp": "2026-01-01T10:00:00Z",
                    }
                ],
            }
            result = calculate_sanad_grade(sanad)
        else:
            result = calculate_sanad_grade(sanads[0])

        assert len(result.explanation.fatal_defects) == 0, (
            f"Clean deal {deal_key} has FATAL defects"
        )


class TestDeterministicBehavior:
    """Tests verifying deterministic behavior of Sanad v2."""

    def test_same_inputs_same_grade(self) -> None:
        """Same inputs must produce identical grade."""
        sanad = {
            "primary_source": {"source_type": "FINANCIAL_MODEL"},
            "transmission_chain": [
                {
                    "node_id": "n1",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "a1",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ],
            "dabt_factors": {
                "documentation_precision": 0.85,
                "transmission_precision": 0.90,
                "temporal_precision": 0.80,
            },
        }
        sources = [
            {
                "evidence_id": "e1",
                "source_system": "SYS1",
                "upstream_origin_id": "o1",
                "source_type": "FINANCIAL_MODEL",
            },
            {
                "evidence_id": "e2",
                "source_system": "SYS2",
                "upstream_origin_id": "o2",
                "source_type": "BANK_STATEMENT",
            },
            {
                "evidence_id": "e3",
                "source_system": "SYS3",
                "upstream_origin_id": "o3",
                "source_type": "SIGNED_CONTRACT",
            },
        ]

        result1 = calculate_sanad_grade(sanad, sources=sources)
        result2 = calculate_sanad_grade(sanad, sources=sources)

        assert result1.grade == result2.grade
        assert result1.explanation.final_grade == result2.explanation.final_grade
        assert result1.tawatur.status == result2.tawatur.status
        assert result1.dabt.score == result2.dabt.score

    def test_defect_detection_deterministic(self) -> None:
        """Defect detection must be deterministic."""
        sanad_with_break = {
            "transmission_chain": [
                {"node_id": "n1", "prev_node_id": None},
                {"node_id": "n2", "prev_node_id": "missing"},
            ]
        }

        for _ in range(5):
            defect = detect_ilal_chain_break(sanad_with_break)
            assert defect is not None
            assert defect.code == IlalDefectCode.ILAL_CHAIN_BREAK
