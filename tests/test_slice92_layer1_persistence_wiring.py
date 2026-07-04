"""Slice92 Task 3 — the court/VEP step path persists through the Layer-1 repository.

Wires the EXISTING Evidence Trust Court / VEP steps (orchestrator defaults, unchanged
judging logic) to the Task 2 twin repositories:

  - The court step persists its findings and the court-scoped Muḥāsabah records
    (surfaced from the court's governed Layer-1 debate via an additive service sink).
  - The VEP step persists the durable VEP candidate row.
  - Deterministic ids make re-execution (retry/resume) idempotent — no duplicate rows.
  - A repository write failure fails the step CLOSED with the reason-coded blocker
    ``METHODOLOGY_LAYER1_PERSISTENCE_FAILED`` (DEC-F). The default InMemory twin cannot
    fail, so non-DB runs stay green.
  - Step summaries gain only safe ids/counts (``layer1_persistence`` block).

Production-shape proof (explicit requirement): the rows the STEP actually sends into
the repository carry the real id shapes — ``claim_mth_...`` claim ids and
``finding-...`` finding ids — not just shapes the repo could store.

No Layer-2 threading, no export. Injected fakes only — no real LLM, no database.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.layer1_durability import (
    EvidenceTrustFindingRow,
    MuhasabahRecordRow,
    ValidatedEvidencePackageRow,
)
from idis.persistence.repositories.layer1_evidence import (
    InMemoryLayer1EvidenceRepository,
    clear_in_memory_layer1_evidence_store,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_orchestrator_methodology_evidence_trust_court import (
    _ctx_with_court_inputs,
)

SANAD_UUID = "aaaaaaa1-1111-1111-1111-1111111111aa"


def setup_function() -> None:
    clear_run_steps_store()
    clear_in_memory_layer1_evidence_store()


def _orchestrator() -> RunOrchestrator:
    return RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )


def _ctx_with_finding_inputs(run_id: str) -> Any:
    """Court inputs whose grade carries a major defect -> a real court finding."""
    ctx = _ctx_with_court_inputs(run_id)
    ctx.methodology_sanad_grades = [
        ctx.methodology_sanad_grades[0].model_copy(
            update={
                "sanad_id": SANAD_UUID,
                "major_defect_count": 1,
                "defect_ids": ["defect-1"],
            }
        )
    ]
    return ctx


def _run_court_and_vep(ctx: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    orchestrator = _orchestrator()
    court_summary = orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001
    vep_summary = orchestrator._execute_methodology_validated_evidence_package(  # noqa: SLF001
        ctx, accumulated=court_summary
    )
    return court_summary, vep_summary


class _RecordingRepo:
    """Repository double that records rows routed through the ctx injection seam."""

    def __init__(self) -> None:
        self.packages: list[ValidatedEvidencePackageRow] = []
        self.findings: list[EvidenceTrustFindingRow] = []
        self.muhasabah: list[MuhasabahRecordRow] = []

    def upsert_validated_evidence_package(self, row: ValidatedEvidencePackageRow) -> dict[str, Any]:
        self.packages.append(row)
        return {"package_id": row.package_id, "tenant_id": row.tenant_id}

    def upsert_evidence_trust_finding(self, row: EvidenceTrustFindingRow) -> dict[str, Any]:
        self.findings.append(row)
        return {"finding_id": row.finding_id, "tenant_id": row.tenant_id}

    def upsert_muhasabah_record(self, row: MuhasabahRecordRow) -> dict[str, Any]:
        self.muhasabah.append(row)
        return {"record_id": row.record_id, "tenant_id": row.tenant_id}


# Adversarial exception text: raw driver/repository errors can carry SQL fragments,
# credentials, or row payloads — none of it may reach the blocker message, because
# _fail_step persists str(exc)[:500] into the ledger-visible step.error_message.
_ADVERSARIAL_ERROR = (
    "FREE TEXT MUST NOT LEAK | api_key=sk-fake-secret-marker-12345 | "
    'INSERT failed for row claim_text="Transcribed founder call: revenue is fabricated"'
)
_LEAK_MARKERS = (
    "FREE TEXT MUST NOT LEAK",
    "sk-fake-secret-marker-12345",
    "Transcribed founder call",
)


class _FailingRepo:
    """Repository double whose writes fail with adversarial exception text."""

    def upsert_validated_evidence_package(self, row: Any) -> dict[str, Any]:
        raise RuntimeError(_ADVERSARIAL_ERROR)

    def upsert_evidence_trust_finding(self, row: Any) -> dict[str, Any]:
        raise RuntimeError(_ADVERSARIAL_ERROR)

    def upsert_muhasabah_record(self, row: Any) -> dict[str, Any]:
        raise RuntimeError(_ADVERSARIAL_ERROR)


# --- The step path persists production-shaped rows via the default twin ---


def test_steps_persist_production_shaped_rows_via_default_twin() -> None:
    run_id = str(uuid_mod.uuid4())
    ctx = _ctx_with_finding_inputs(run_id)

    court_summary, vep_summary = _run_court_and_vep(ctx)

    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)

    # VEP candidate row: durable, UUID package/court ids, safe summary only.
    packages = twin.list_validated_evidence_packages(run_id=run_id)
    assert len(packages) == 1
    package = packages[0]
    assert package["package_id"] == ctx.methodology_validated_evidence_package.package_id
    uuid_mod.UUID(package["package_id"])
    uuid_mod.UUID(package["court_id"])
    assert "claim_text" not in str(package)

    # Court findings: THE production-shape proof — the step sends claim_mth_/finding-
    # shaped ids into the repository, exactly as the court produced them.
    findings = twin.list_evidence_trust_findings(run_id=run_id)
    assert len(findings) >= 1
    assert len(findings) == len(ctx.methodology_evidence_trust_court.findings)
    for finding in findings:
        assert finding["finding_id"].startswith("finding-")
        assert finding["claim_id"].startswith("claim_mth_")
        assert finding["sanad_id"] == SANAD_UUID

    # Court-scoped Muhasabah rows from the governed Layer-1 debate.
    records = twin.list_muhasabah_records(run_id=run_id)
    assert len(records) >= 1
    for record in records:
        assert record["source_step"] == "METHODOLOGY_EVIDENCE_TRUST_COURT"
        assert record["agent_id"]
        assert 0.0 <= record["confidence"] <= 1.0
        assert isinstance(record["record_timestamp"], str) and record["record_timestamp"]

    # Step summaries gain only safe ids/counts.
    assert court_summary["layer1_persistence"] == {
        "status": "persisted",
        "finding_row_count": len(findings),
        "muhasabah_row_count": len(records),
    }
    assert vep_summary["layer1_persistence"] == {
        "status": "persisted",
        "package_row_count": 1,
        "package_ids": [package["package_id"]],
    }
    assert "claim_text" not in str(court_summary)
    assert "AgentOutput" not in str(court_summary)


# --- Idempotency: re-executing the steps (retry/resume) never duplicates rows ---


def test_persistence_is_idempotent_on_step_retry() -> None:
    run_id = str(uuid_mod.uuid4())
    _run_court_and_vep(_ctx_with_finding_inputs(run_id))
    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    first_counts = (
        len(twin.list_validated_evidence_packages(run_id=run_id)),
        len(twin.list_evidence_trust_findings(run_id=run_id)),
        len(twin.list_muhasabah_records(run_id=run_id)),
    )

    # Retry: rebuild the same-run context and re-execute both steps.
    _run_court_and_vep(_ctx_with_finding_inputs(run_id))
    second_counts = (
        len(twin.list_validated_evidence_packages(run_id=run_id)),
        len(twin.list_evidence_trust_findings(run_id=run_id)),
        len(twin.list_muhasabah_records(run_id=run_id)),
    )
    assert first_counts == second_counts
    assert first_counts[0] == 1


# --- The ctx-injected repository is the write target when provided ---


def test_injected_repository_receives_the_rows() -> None:
    run_id = str(uuid_mod.uuid4())
    ctx = _ctx_with_finding_inputs(run_id)
    recording = _RecordingRepo()
    ctx.layer1_evidence_repository = recording

    _run_court_and_vep(ctx)

    assert [row.package_id for row in recording.packages] == [
        ctx.methodology_validated_evidence_package.package_id
    ]
    assert all(row.claim_id.startswith("claim_mth_") for row in recording.findings)
    assert all(row.finding_id.startswith("finding-") for row in recording.findings)
    assert recording.muhasabah
    # Nothing leaked into the default twin.
    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    assert twin.list_validated_evidence_packages(run_id=run_id) == []
    assert twin.list_evidence_trust_findings(run_id=run_id) == []


# --- DEC-F: write failure fails the step closed with the reason-coded blocker ---


def test_write_failure_fails_closed_with_reason_code() -> None:
    run_id = str(uuid_mod.uuid4())
    ctx = _ctx_with_finding_inputs(run_id)
    ctx.layer1_evidence_repository = _FailingRepo()
    orchestrator = _orchestrator()

    with pytest.raises(RunStepBlockedError) as court_exc:
        orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001
    assert court_exc.value.code == "METHODOLOGY_LAYER1_PERSISTENCE_FAILED"
    # str(exc) is EXACTLY what _fail_step persists as the ledger-visible
    # step.error_message (str(exc)[:500]) — raw repository text must not leak.
    court_message = str(court_exc.value)
    assert court_message == "Layer 1 evidence persistence failed closed"
    for marker in _LEAK_MARKERS:
        assert marker not in court_message
    # The raw exception stays chained for logs/debugging only.
    assert isinstance(court_exc.value.__cause__, RuntimeError)

    # VEP step: court succeeds via a working repo, then writes start failing.
    ctx2 = _ctx_with_finding_inputs(run_id)
    ctx2.layer1_evidence_repository = _RecordingRepo()
    orchestrator2 = _orchestrator()
    court_summary = orchestrator2._execute_methodology_evidence_trust_court(ctx2)  # noqa: SLF001
    ctx2.layer1_evidence_repository = _FailingRepo()
    with pytest.raises(RunStepBlockedError) as vep_exc:
        orchestrator2._execute_methodology_validated_evidence_package(  # noqa: SLF001
            ctx2, accumulated=court_summary
        )
    assert vep_exc.value.code == "METHODOLOGY_LAYER1_PERSISTENCE_FAILED"
    vep_message = str(vep_exc.value)
    assert vep_message == "Layer 1 evidence persistence failed closed"
    for marker in _LEAK_MARKERS:
        assert marker not in vep_message
    assert isinstance(vep_exc.value.__cause__, RuntimeError)


# --- The empty no-claims path skips persistence entirely ---


def test_empty_no_claims_path_skips_persistence() -> None:
    run_id = str(uuid_mod.uuid4())
    ctx = _ctx_with_court_inputs(run_id)
    ctx.methodology_materialized_claims = []
    ctx.methodology_sanads = []
    ctx.methodology_sanad_grades = []
    ctx.methodology_truth_dashboard = None

    orchestrator = _orchestrator()
    court_summary = orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001
    vep_summary = orchestrator._execute_methodology_validated_evidence_package(  # noqa: SLF001
        ctx, accumulated=court_summary
    )

    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    assert twin.list_validated_evidence_packages(run_id=run_id) == []
    assert twin.list_evidence_trust_findings(run_id=run_id) == []
    assert twin.list_muhasabah_records(run_id=run_id) == []
    assert "layer1_persistence" not in court_summary
    assert "layer1_persistence" not in vep_summary


# --- Production binding: build_run_context wires the repository selector ---


def test_steps_builder_binds_layer1_repository() -> None:
    from pathlib import Path

    steps_src = Path("src/idis/services/runs/steps.py").read_text(encoding="utf-8")
    assert "get_layer1_evidence_repository" in steps_src
    compact = steps_src.replace("\n", "").replace(" ", "")
    assert "layer1_evidence_repository=get_layer1_evidence_repository(db_conn,tenant_id)" in compact
