"""Tests for GDBS pre-built sanad wiring into GRADE step [P6-T04 Fix 1].

Verifies that:
- Clean deal (deal_001) produces B/C grades, not all D
- Prebuilt sanads bypass chain building and use GDBS data
- Claims in the in-memory store get their grade updated after grading
- Missing prebuilt data falls back to normal chain-build path
"""

from __future__ import annotations

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import (
    InMemoryClaimsRepository,
    InMemoryEvidenceRepository,
    clear_all_claims_stores,
    seed_claim_in_memory,
)
from idis.services.defects.service import DefectService
from idis.services.sanad.auto_grade import auto_grade_claims_for_run
from idis.services.sanad.service import SanadService

TENANT_ID = "a0000000-0000-0000-0000-000000000001"
DEAL_ID = "b0000000-0000-0000-0000-000000000001"
RUN_ID = "c0000000-0000-0000-0000-000000000001"
CLAIM_ID_B = "d0000000-0000-0000-0000-000000000001"
CLAIM_ID_C = "d0000000-0000-0000-0000-000000000002"
CLAIM_ID_UPD = "d0000000-0000-0000-0000-000000000003"
CLAIM_ID_EMPTY = "d0000000-0000-0000-0000-000000000004"
CLAIM_ID_PRE = "d0000000-0000-0000-0000-000000000005"
CLAIM_ID_NORM = "d0000000-0000-0000-0000-000000000006"
CLAIM_ID_AUDIT = "d0000000-0000-0000-0000-000000000007"
EVIDENCE_ID_1 = "e0000000-0000-0000-0000-000000000001"
EVIDENCE_ID_2 = "e0000000-0000-0000-0000-000000000002"
EVIDENCE_ID_3 = "e0000000-0000-0000-0000-000000000003"
NODE_ID_1 = "f0000000-0000-0000-0000-000000000001"


def _make_prebuilt_sanad(
    *,
    sanad_grade: str = "B",
    primary_evidence_id: str = EVIDENCE_ID_1,
    corroborating_evidence_ids: list[str] | None = None,
    extraction_confidence: float = 0.97,
    dhabt_score: float = 0.94,
    defects: list[dict] | None = None,
) -> dict:
    """Build a minimal GDBS-style sanad dict for testing."""
    return {
        "sanad_id": "a1000000-0000-0000-0000-000000000001",
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "primary_evidence_id": primary_evidence_id,
        "corroborating_evidence_ids": corroborating_evidence_ids or [],
        "extraction_confidence": extraction_confidence,
        "dhabt_score": dhabt_score,
        "sanad_grade": sanad_grade,
        "transmission_chain": [
            {
                "node_id": NODE_ID_1,
                "node_type": "SOURCE",
                "input_refs": [],
                "output_ref": primary_evidence_id,
            }
        ],
        "defects": defects or [],
    }


def _make_evidence_item(
    evidence_id: str = EVIDENCE_ID_1,
    source_system: str = "Deck",
    source_grade: str = "C",
) -> dict:
    """Build a minimal GDBS evidence item for testing."""
    return {
        "evidence_id": evidence_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "source_system": source_system,
        "source_grade": source_grade,
        "verification_status": "VERIFIED",
    }


def _setup():
    """Clear stores and create fresh service instances."""
    clear_all_claims_stores()
    sink = InMemoryAuditSink()
    ev_repo = InMemoryEvidenceRepository(TENANT_ID)
    sanad_svc = SanadService(tenant_id=TENANT_ID, audit_sink=sink)
    defect_svc = DefectService(tenant_id=TENANT_ID, audit_sink=sink)
    return sink, ev_repo, sanad_svc, defect_svc


class TestPrebuiltSanadGrading:
    """Tests for auto_grade_claims_for_run with prebuilt_sanads."""

    def test_prebuilt_sanad_produces_expected_grade(self) -> None:
        """Prebuilt sanad with grade B should produce grade B, not D."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_id = CLAIM_ID_B
        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "ARR is $4,500,000",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_id: {
                "sanad": _make_prebuilt_sanad(sanad_grade="B"),
                "sources": [
                    _make_evidence_item(EVIDENCE_ID_1, "Deck", "C"),
                    _make_evidence_item(EVIDENCE_ID_2, "FinModel", "B"),
                ],
                "claim": {"claim_id": claim_id, "claim_text": "ARR is $4,500,000"},
            }
        }

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_id],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        assert result.graded_count == 1
        assert result.failed_count == 0
        claim_result = result.results[0]
        assert claim_result.grade == "B"
        assert claim_result.status == "graded"
        assert claim_result.sanad_id is not None

    def test_prebuilt_sanad_grade_c_for_single_source(self) -> None:
        """Prebuilt sanad with grade C (no corroboration) should produce C."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_id = CLAIM_ID_C
        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "Runway is 22.5 months",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_id: {
                "sanad": _make_prebuilt_sanad(sanad_grade="C"),
                "sources": [_make_evidence_item(EVIDENCE_ID_3, "Deck", "C")],
            }
        }

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_id],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        assert result.graded_count == 1
        claim_result = result.results[0]
        assert claim_result.grade == "C"

    def test_claim_grade_updated_in_memory_store(self) -> None:
        """After grading, claim_grade in the in-memory store should be updated."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_id = CLAIM_ID_UPD
        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "Gross Margin is 72.50%",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_id: {
                "sanad": _make_prebuilt_sanad(sanad_grade="B"),
                "sources": [_make_evidence_item()],
            }
        }

        auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_id],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        claims_repo = InMemoryClaimsRepository(TENANT_ID)
        claim = claims_repo.get(claim_id)
        assert claim is not None
        assert claim["claim_grade"] == "B"

    def test_empty_prebuilt_sanad_produces_grade_failed(self) -> None:
        """Empty sanad dict in prebuilt data should result in grade_failed."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_id = CLAIM_ID_EMPTY
        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "Test claim",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_id: {
                "sanad": {},
                "sources": [],
            }
        }

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_id],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        assert result.failed_count == 1
        assert result.graded_count == 0
        assert result.results[0].status == "grade_failed"

    def test_mixed_prebuilt_and_normal_claims(self) -> None:
        """Claims with prebuilt data use it; claims without fall back normally."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_prebuilt = CLAIM_ID_PRE
        claim_normal = CLAIM_ID_NORM

        seed_claim_in_memory(
            {
                "claim_id": claim_prebuilt,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "ARR",
                "claim_grade": "D",
            }
        )
        seed_claim_in_memory(
            {
                "claim_id": claim_normal,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "Burn rate",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_prebuilt: {
                "sanad": _make_prebuilt_sanad(sanad_grade="B"),
                "sources": [_make_evidence_item()],
            }
        }

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_prebuilt, claim_normal],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        pre_result = result.results[0]
        assert pre_result.grade == "B"
        assert pre_result.status == "graded"

        norm_result = result.results[1]
        assert norm_result.status == "grade_failed"

    def test_audit_events_emitted_for_prebuilt(self) -> None:
        """Prebuilt grading should emit sanad.created and sanad.graded audit events."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        claim_id = CLAIM_ID_AUDIT
        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "claim_class": "FINANCIAL",
                "claim_text": "NRR is 115%",
                "claim_grade": "D",
            }
        )

        prebuilt_sanads = {
            claim_id: {
                "sanad": _make_prebuilt_sanad(sanad_grade="C"),
                "sources": [_make_evidence_item()],
            }
        }

        auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[claim_id],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
            prebuilt_sanads=prebuilt_sanads,
        )

        event_types = [e["event_type"] for e in sink.events]
        assert "sanad.created" in event_types
        assert "sanad.graded" in event_types

        graded_event = next(e for e in sink.events if e["event_type"] == "sanad.graded")
        assert graded_event["details"]["grade"] == "C"
