"""Tests for RunOrchestrator step ledger — Phase 5 orchestration.

Covers:
- SNAPSHOT records five steps in order (INGEST_CHECK → DOCUMENT_PREFLIGHT → EXTRACT → GRADE → CALC)
- Step errors persisted and returned
- FULL completes all 10 steps in correct order
- Cross-tenant run step read returns 404 (no existence leak)
- Audit failure aborts run fail-closed

Updated for Phase 3.0 Slice 2: FULL mode now has 10 steps.
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


def _make_preflight_document(
    *,
    document_id: str,
    parse_status: str = "PARSED",
    metadata: dict[str, Any] | None = None,
    spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a full persisted-corpus document for preflight tests."""
    return {
        "tenant_id": TENANT_A,
        "deal_id": "deal-preflight",
        "document_id": document_id,
        "doc_id": f"artifact-{document_id}",
        "doc_type": "DOCX",
        "parse_status": parse_status,
        "document_name": f"{document_id}.docx",
        "sha256": "a" * 64,
        "uri": f"deals/{document_id}.docx",
        "metadata": metadata or {},
        "source_metadata": {},
        "spans": spans
        if spans is not None
        else [
            {
                "span_id": f"span-{document_id}",
                "tenant_id": TENANT_A,
                "deal_id": "deal-preflight",
                "document_id": document_id,
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Highly sensitive raw revenue sentence.",
                "content_hash": "b" * 64,
            }
        ],
    }


def _make_failed_preflight_document() -> dict[str, Any]:
    """Return a failed persisted document with safe parser metadata only."""
    return _make_preflight_document(
        document_id="doc-failed",
        parse_status="FAILED",
        metadata={
            "parse_error_codes": ["encrypted_pdf"],
            "parse_warning_codes": [],
            "detected_format": "PDF",
            "parser_doc_type": "PDF",
        },
        spans=[],
    )


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Reset in-memory stores before each test."""
    clear_deals_store()
    clear_runs_store()
    clear_run_steps_store()


class TestSnapshotRecordsFiveStepsInOrder:
    """test_snapshot_records_five_steps_in_order."""

    def test_snapshot_records_four_steps_in_order(self) -> None:
        """SNAPSHOT run records DOCUMENT_PREFLIGHT before EXTRACT."""
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

        assert result.status == "SUCCEEDED"
        assert len(result.steps) == 5

        expected_names = [
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
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
        assert result.error_code == "RUNTIMEERROR"
        assert result.block_reason is None

        failed_steps = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed_steps) == 1
        failed = failed_steps[0]
        assert failed.step_name == StepName.EXTRACT
        assert failed.error_code == "RUNTIMEERROR"
        assert "unavailable" in (failed.error_message or "").lower()
        assert failed.finished_at is not None

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) == 2
        assert completed_steps[0].step_name == StepName.INGEST_CHECK
        assert completed_steps[1].step_name == StepName.DOCUMENT_PREFLIGHT

    def test_empty_documents_sets_no_ingested_documents_block_reason(self) -> None:
        """Empty corpus is an intentional blocked condition, not a generic runtime error."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            mode="SNAPSHOT",
            documents=[],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.block_reason == "NO_INGESTED_DOCUMENTS"
        assert result.error_code == "NO_INGESTED_DOCUMENTS"

        failed_steps = [s for s in result.steps if s.status == StepStatus.FAILED]
        assert len(failed_steps) == 1
        assert failed_steps[0].step_name == StepName.INGEST_CHECK
        assert failed_steps[0].error_code == "NO_INGESTED_DOCUMENTS"

    def test_no_usable_preflight_corpus_sets_no_usable_documents_block_reason(self) -> None:
        """Failed/no-span corpus rows fail at DOCUMENT_PREFLIGHT, not INGEST_CHECK."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_failed_preflight_document()],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.block_reason == "NO_USABLE_DOCUMENTS"
        assert result.error_code == "NO_USABLE_DOCUMENTS"
        assert [step.step_name for step in result.steps] == [
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
        ]

    def test_mixed_corpus_sends_only_eligible_documents_to_extract(self) -> None:
        """Broken documents must not leak into EXTRACT inputs."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        seen_documents: list[dict[str, Any]] = []

        def recording_extract(**kwargs: Any) -> dict[str, Any]:
            seen_documents.extend(kwargs["documents"])
            return _stub_extract(**kwargs)

        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
        usable = _make_preflight_document(document_id="doc-usable")

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[usable, _make_failed_preflight_document()],
            extract_fn=recording_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        assert [doc["document_id"] for doc in seen_documents] == ["doc-usable"]
        assert ctx.documents == [usable]

    def test_document_preflight_step_summary_has_no_raw_span_text(self) -> None:
        """Run step summary keeps safe span references, not text excerpts."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "SUCCEEDED"
        preflight_step = next(
            step for step in result.steps if step.step_name == StepName.DOCUMENT_PREFLIGHT
        )
        assert "Highly sensitive raw revenue sentence" not in str(preflight_step.result_summary)
        assert "text_excerpt" not in str(preflight_step.result_summary)

    def test_generic_preflight_runtime_failure_does_not_become_block_reason(self) -> None:
        """Unexpected preflight exceptions are not intentional business blockers."""
        audit_sink = InMemoryAuditSink()
        repo = InMemoryRunStepsRepository(TENANT_A)
        orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)

        def failing_preflight(**kwargs: Any) -> Any:
            raise RuntimeError("classification dependency failed")

        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            deal_id="deal-preflight",
            mode="SNAPSHOT",
            documents=[],
            preflight_corpus=[_make_preflight_document(document_id="doc-usable")],
            document_preflight_fn=failing_preflight,
            extract_fn=_stub_extract,
            grade_fn=_stub_grade,
            calc_fn=_stub_calc,
        )

        result = orchestrator.execute(ctx)

        assert result.status == "FAILED"
        assert result.error_code == "RUNTIMEERROR"
        assert result.block_reason is None
        assert result.steps[-1].step_name == StepName.DOCUMENT_PREFLIGHT


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

    def test_full_completes_all_ten_steps(self) -> None:
        """FULL run completes all 10 steps in canonical order."""
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

        assert result.status == "SUCCEEDED"
        assert result.block_reason is None

        completed = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed) == 10
        assert [s.step_name for s in completed] == [
            StepName.INGEST_CHECK,
            StepName.DOCUMENT_PREFLIGHT,
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


class TestStartRunPreflightCorpusBehavior:
    """API start-run behavior for full preflight corpus checks."""

    def test_api_no_corpus_fails_before_run_creation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No corpus rows still returns NO_INGESTED_DOCUMENTS before a run response exists."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "No Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 400
        body = run_resp.json()
        assert body["code"] == "NO_INGESTED_DOCUMENTS"
        assert "run_id" not in body

    def test_api_corpus_exists_but_no_usable_docs_fails_document_preflight(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No usable docs creates a run and fails at DOCUMENT_PREFLIGHT."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Failed Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        failed_doc = _make_failed_preflight_document()
        failed_doc["deal_id"] = deal_id
        app.state.deal_documents[deal_id] = [failed_doc]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 202
        body = run_resp.json()
        assert body["status"] == "FAILED"
        assert body["block_reason"] == "NO_USABLE_DOCUMENTS"
        assert [step["step_name"] for step in body["steps"]] == [
            "INGEST_CHECK",
            "DOCUMENT_PREFLIGHT",
        ]

    def test_api_mixed_corpus_continues_with_eligible_docs_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mixed usable/unusable corpus should continue through extraction."""
        monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_make_api_keys()))
        audit_sink = InMemoryAuditSink()
        app = create_app(audit_sink=audit_sink, service_region="me-south-1")
        app.state.deal_documents = {}
        client = TestClient(app)

        create_resp = client.post(
            "/v1/deals",
            json={"name": "Mixed Corpus Deal", "company_name": "TestCo"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )
        assert create_resp.status_code == 201
        deal_id = create_resp.json()["deal_id"]
        usable_doc = _make_preflight_document(document_id="doc-usable")
        usable_doc["deal_id"] = deal_id
        usable_doc["spans"][0]["deal_id"] = deal_id
        failed_doc = _make_failed_preflight_document()
        failed_doc["deal_id"] = deal_id
        app.state.deal_documents[deal_id] = [usable_doc, failed_doc]

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": API_KEY_A},
        )

        assert run_resp.status_code == 202
        body = run_resp.json()
        assert body["status"] == "SUCCEEDED"
        assert body["block_reason"] is None
        assert [step["step_name"] for step in body["steps"]] == [
            "INGEST_CHECK",
            "DOCUMENT_PREFLIGHT",
            "EXTRACT",
            "GRADE",
            "CALC",
        ]


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
