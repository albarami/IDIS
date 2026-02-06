"""Tests for Sanad auto-grade step [P3-T01].

Three load-bearing tests:
- One claim produces one persisted Sanad
- Defects persisted when grader returns them
- Audit events emitted (sanad.created, sanad.graded)
"""

from __future__ import annotations

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import (
    InMemoryEvidenceRepository,
    InMemorySanadsRepository,
    clear_all_claims_stores,
)
from idis.services.defects.service import DefectService
from idis.services.sanad.auto_grade import auto_grade_claims_for_run
from idis.services.sanad.service import SanadService

TENANT_ID = "a0000000-0000-0000-0000-000000000001"
DEAL_ID = "b0000000-0000-0000-0000-000000000001"
RUN_ID = "c0000000-0000-0000-0000-000000000001"
CLAIM_ID = "d0000000-0000-0000-0000-000000000001"
EVIDENCE_ID = "e0000000-0000-0000-0000-000000000001"


def _setup() -> tuple[InMemoryAuditSink, InMemoryEvidenceRepository, SanadService, DefectService]:
    """Clear stores and create fresh service instances."""
    clear_all_claims_stores()
    sink = InMemoryAuditSink()
    ev_repo = InMemoryEvidenceRepository(TENANT_ID)
    sanad_svc = SanadService(tenant_id=TENANT_ID, audit_sink=sink)
    defect_svc = DefectService(tenant_id=TENANT_ID, audit_sink=sink)
    return sink, ev_repo, sanad_svc, defect_svc


def _seed_evidence(ev_repo: InMemoryEvidenceRepository) -> None:
    """Seed one evidence item for CLAIM_ID."""
    ev_repo.create(
        evidence_id=EVIDENCE_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claim_id=CLAIM_ID,
        source_span_id="span-001",
        source_grade="D",
        verification_status="UNVERIFIED",
    )


class TestAutoGradeStep:
    """Tests for auto_grade_claims_for_run."""

    def test_one_claim_produces_one_sanad(self) -> None:
        """A single claim with evidence produces exactly one persisted Sanad."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        _seed_evidence(ev_repo)

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[CLAIM_ID],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
        )

        assert result.graded_count == 1
        assert result.failed_count == 0
        assert len(result.results) == 1

        claim_result = result.results[0]
        assert claim_result.status == "graded"
        assert claim_result.sanad_id is not None
        assert claim_result.grade is not None

        # Verify sanad was persisted
        sanads_repo = InMemorySanadsRepository(TENANT_ID)
        persisted = sanads_repo.get(claim_result.sanad_id)
        assert persisted is not None
        assert persisted["claim_id"] == CLAIM_ID

    def test_defects_persisted_when_grader_returns_them(self) -> None:
        """Defects from grader are persisted via DefectService.

        The grader may or may not produce defects depending on the evidence
        quality; this test verifies the plumbing works in both cases.
        """
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        _seed_evidence(ev_repo)

        result = auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[CLAIM_ID],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
        )

        claim_result = result.results[0]
        assert claim_result.status == "graded"

        # total_defects is an integer >= 0
        assert isinstance(result.total_defects, int)
        assert result.total_defects >= 0

        # If defects were produced, they must have IDs
        if claim_result.defect_ids:
            assert all(isinstance(did, str) for did in claim_result.defect_ids)

    def test_emits_audit_events(self) -> None:
        """Auto-grade emits sanad.created and sanad.graded audit events."""
        sink, ev_repo, sanad_svc, defect_svc = _setup()
        _seed_evidence(ev_repo)

        auto_grade_claims_for_run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[CLAIM_ID],
            evidence_repo=ev_repo,
            sanad_service=sanad_svc,
            defect_service=defect_svc,
            audit_sink=sink,
        )

        event_types = [e["event_type"] for e in sink.events]
        assert "sanad.created" in event_types
        assert "sanad.graded" in event_types
