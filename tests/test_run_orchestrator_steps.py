"""Tests for RunOrchestrator step ledger — Phase 5 orchestration.

Covers:
- SNAPSHOT records four steps in order (INGEST_CHECK → EXTRACT → GRADE → CALC)
- Step errors persisted and returned
- FULL completes all 9 steps in correct order
- Cross-tenant run step read returns 404 (no existence leak)
- Audit failure aborts run fail-closed

Updated for Phase X: FULL mode now has 9 steps.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.models.run_step import StepName, StepStatus
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
API_KEY_A = "test-key-orch-a"
API_KEY_B = "test-key-orch-b"


def _make_api_keys() -> dict[str, dict[str, Any]]:
    """Build API key config for two tenants."""
    return {
        API_KEY_A: {
            "tenant_id": TENANT_A,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        API_KEY_B: {
            "tenant_id": TENANT_B,
            "actor_id": "actor-b",
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


def _stub_extract(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic extraction stub returning fixed claim IDs."""
    return {
        "status": "COMPLETED",
        "created_claim_ids": ["claim-001", "claim-002"],
        "chunk_count": 1,
        "unique_claim_count": 2,
        "conflict_count": 0,
    }


def _stub_grade(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    audit_sink: Any,
) -> dict[str, Any]:
    """Deterministic grading stub returning success summary."""
    return {
        "graded_count": len(created_claim_ids),
        "failed_count": 0,
        "total_defects": 0,
        "all_failed": False,
    }


def _stub_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
) -> dict[str, Any]:
    """Deterministic calc stub returning fixed calc IDs."""
    return {
        "calc_ids": ["calc-001", "calc-002"],
        "reproducibility_hashes": ["hash-aaa", "hash-bbb"],
    }


def _stub_extract_failing(**kwargs: Any) -> dict[str, Any]:
    """Extraction stub that always raises."""
    raise RuntimeError("Extraction service unavailable")


def _make_documents() -> list[dict[str, Any]]:
    """Return minimal ingested document list."""
    return [
        {
            "document_id": "doc-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Reset in-memory stores before each test."""
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


class TestSnapshotRecordsFourStepsInOrder:
    """test_snapshot_records_four_steps_in_order."""

    def test_snapshot_records_four_steps_in_order(self) -> None:
        """SNAPSHOT run records INGEST_CHECK, EXTRACT, GRADE, CALC in canonical order."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert len(result.steps) == 4

        expected_names = [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
        ]
        for i, step in enumerate(result.steps):
            assert step.step_name == expected_names[i]
            assert step.status == StepStatus.COMPLETED
            assert step.step_order == i
            assert step.started_at is not None
            assert step.finished_at is not None


class TestSnapshotStepErrorsPersistedAndReturned:
    """test_snapshot_step_errors_persisted_and_returned."""

    def test_snapshot_step_errors_persisted_and_returned(self) -> None:
        """When EXTRACT fails, step error_code and error_message are persisted."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=_stub_extract_failing,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"

        failed_steps = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed_steps) == 1
        failed = failed_steps[0]
        assert failed.step_name == StepName.EXTRACT
        assert failed.error_code == "RUNTIMEERROR"
        assert "unavailable" in (failed.error_message or "").lower()
        assert failed.finished_at is not None

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) == 1
        assert completed_steps[0].step_name == StepName.INGEST_CHECK


def _stub_enrichment(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
) -> dict[str, Any]:
    """Deterministic enrichment stub returning zero results."""
    return {
        "provider_count": 0,
        "result_count": 0,
        "blocked_count": 0,
        "enrichment_refs": {},
    }


def _stub_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
) -> dict[str, Any]:
    """Deterministic debate stub returning fixed output."""
    return {
        "debate_id": run_id,
        "stop_reason": "MAX_ROUNDS",
        "round_number": 5,
        "muhasabah_passed": True,
        "agent_output_count": 10,
    }


def _stub_analysis(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    enrichment_refs: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic analysis stub."""
    return {
        "agent_count": 8,
        "report_ids": ["report-001"],
        "bundle_id": f"bundle-{run_id[:8]}",
    }


def _stub_scoring(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
) -> dict[str, Any]:
    """Deterministic scoring stub."""
    return {
        "composite_score": 72.5,
        "band": "MEDIUM",
        "routing": "HOLD",
    }


def _stub_deliverables(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scorecard: Any,
) -> dict[str, Any]:
    """Deterministic deliverables stub."""
    return {
        "deliverable_count": 4,
        "types": ["IC_MEMO", "QA_BRIEF", "SCREENING_SNAPSHOT", "TRUTH_DASHBOARD"],
        "deliverable_ids": ["del-001", "del-002", "del-003", "del-004"],
    }


class TestFullCompletesAllNineSteps:
    """test_full_completes_all_nine_steps."""

    def test_full_completes_all_nine_steps(self) -> None:
        """FULL run completes all 9 steps in canonical order."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="FULL",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
            enrich_fn=_stub_enrichment,
            debate_fn=_stub_debate,
            analysis_fn=_stub_analysis,
            scoring_fn=_stub_scoring,
            deliverables_fn=_stub_deliverables,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "COMPLETED"
        assert result.block_reason is None

        completed = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed) == 9
        assert [s.step_name for s in completed] == [
            StepName.INGEST_CHECK,
            StepName.EXTRACT,
            StepName.GRADE,
            StepName.CALC,
            StepName.ENRICHMENT,
            StepName.DEBATE,
            StepName.ANALYSIS,
            StepName.SCORING,
            StepName.DELIVERABLES,
        ]


class TestCrossTenantRunStepReadReturns404:
    """test_cross_tenant_run_step_read_returns_404."""

    def test_cross_tenant_run_step_read_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cross-tenant GET /v1/runs/{runId} returns 404 with no existence leak."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Test Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        app.state.deal_documents[deal_id] = _make_documents()

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert run_resp.status_code == 202
        run_id = run_resp.json()["run_id"]

        cross_resp = client.get(
            f"/v1/runs/{run_id}",
            headers={"X-IDIS-API-Key": API_KEY_B},
        )
        assert cross_resp.status_code == 404
        body = cross_resp.json()
        assert body["code"] == "NOT_FOUND"
        details = body.get("details") or {}
        assert "run_id" not in details


class TestAuditFailureAbortsRunFailClosed:
    """test_audit_failure_aborts_run_fail_closed."""

    def test_audit_failure_aborts_run_fail_closed(self) -> None:
        """AuditSinkError during step execution propagates as 500 AUDIT_FAILURE."""

        class FailingAuditSink:
            """Audit sink that raises on every emit call."""

            def emit(self, event: dict[str, Any]) -> None:
                """Always fail."""
                raise AuditSinkError("Disk full")

        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(
            audit_sink=FailingAuditSink(),
            run_steps_repo=repo,
        )

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=_make_documents(),
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        with pytest.raises(AuditSinkError, match="Disk full"):
            orchestrator.execute(ctx)
