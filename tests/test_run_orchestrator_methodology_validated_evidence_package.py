"""Run orchestrator tests for Slice 12 Validated Evidence Package wiring."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.models.validated_evidence_package_materialization import (
    RunScopedValidatedEvidencePackageShell,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_methodology_validated_evidence_package_service import (
    _court_record,
    _court_summary,
)
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def _ctx_with_vep_inputs(run_id: str) -> Any:
    ctx = _ctx(run_id)
    ctx.methodology_evidence_trust_court = _court_record().model_copy(update={"run_id": run_id})
    return ctx


def test_full_step_order_places_vep_after_court_before_extract() -> None:
    assert StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE in FULL_STEPS
    assert StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_EVIDENCE_TRUST_COURT) < FULL_STEPS.index(
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_vep_step_attaches_record_and_safe_summary() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_vep_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_validated_evidence_package(ctx)  # noqa: SLF001

    assert ctx.methodology_validated_evidence_package is not None
    assert summary["summary"]["packaged_claim_count"] == 4
    assert summary["summary"]["by_disposition"] == {
        "disputed": 1,
        "rejected": 1,
        "trusted": 1,
        "unverified": 1,
    }
    assert summary["claim_ids_by_disposition"]["trusted"] == ["claim_mth_trusted"]
    assert summary["package_ids"]
    assert "AgentOutput" not in str(summary)
    assert "content" not in summary
    assert "recommendation" not in str(summary)
    assert "GO" not in str(summary)


def test_missing_or_shell_only_court_context_blocks_vep() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    missing_court_ctx = _ctx(str(uuid.uuid4()))

    with pytest.raises(RunStepBlockedError) as missing_court:
        orchestrator._execute_methodology_validated_evidence_package(missing_court_ctx)  # noqa: SLF001

    shell_only_ctx = _ctx_with_vep_inputs(str(uuid.uuid4()))
    court = shell_only_ctx.methodology_evidence_trust_court
    shell_only_ctx.methodology_evidence_trust_court = court.to_shell(summary=_court_summary(court))  # type: ignore[union-attr]

    with pytest.raises(RunStepBlockedError) as shell_only:
        orchestrator._execute_methodology_validated_evidence_package(shell_only_ctx)  # noqa: SLF001

    assert missing_court.value.code == "METHODOLOGY_EVIDENCE_TRUST_COURT_MISSING"
    assert shell_only.value.code == "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE_FAILED"
    assert shell_only.value.result_summary["package_ids"] == []
    assert shell_only_ctx.methodology_validated_evidence_package is None


def test_prior_completed_empty_court_summary_returns_completed_vep_noop() -> None:
    """Only a prior completed empty Court output may bypass full-court construction."""
    run_id = str(uuid.uuid4())
    ctx = _ctx(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    accumulated = {
        "status": "completed",
        "court_ids": [],
        "summary": {
            "total_claims": 0,
            "assessed_claim_count": 0,
            "finding_count": 0,
            "rejected_count": 0,
            "by_disposition": {},
            "by_reason": {},
            "by_grade": {},
            "by_dashboard_verdict": {},
        },
    }

    summary = orchestrator._execute_methodology_validated_evidence_package(  # noqa: SLF001
        ctx,
        accumulated,
    )

    assert ctx.methodology_validated_evidence_package is None
    assert summary["status"] == "completed"
    assert summary["package_ids"] == []
    assert summary["package_shells"] == []
    assert summary["rejections"] == []
    assert summary["summary"]["package_count"] == 0
    assert summary["summary"]["packaged_claim_count"] == 0


def test_rehydrate_vep_uses_safe_shell_only() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_vep_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_validated_evidence_package(ctx)  # noqa: SLF001
    ctx2 = _ctx(run_id)
    orchestrator._rehydrate_methodology_validated_evidence_package(ctx2, summary)  # noqa: SLF001

    assert isinstance(
        ctx2.methodology_validated_evidence_package, RunScopedValidatedEvidencePackageShell
    )
    assert ctx2.methodology_validated_evidence_package.claim_ids_by_disposition["trusted"] == [
        "claim_mth_trusted"
    ]
