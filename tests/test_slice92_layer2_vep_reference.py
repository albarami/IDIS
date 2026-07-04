"""Slice92 Task 4 — durable Layer-2 reference: VEP package ids thread into LAYER2_IC_CHALLENGE.

Acceptance seam ("Layer 1 output is durable and referenced by Layer 2"): the persisted VEP
package ids — already emitted by the VEP step under ``layer1_persistence.package_ids`` and
merged into ``accumulated`` — are threaded into the Layer-2 step (the Slice91
accumulated-threading pattern, null-safe) and surfaced as safe, sorted, deduped
``vep_ref_ids`` in the Layer-2 result, which lands in the run-steps ledger.

Scope boundary: no product bundle/export, no repository/schema changes, no service
changes (the route fn shapes the result). Injected fakes only — no real LLM, no DB.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

from idis.api.routes.runs import _run_full_layer2_ic_challenge
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import StepName, StepStatus
from idis.persistence.repositories.layer1_evidence import (
    InMemoryLayer1EvidenceRepository,
    clear_in_memory_layer1_evidence_store,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_slice63_rag_full_wiring import _documents
from tests.test_slice92_layer1_persistence_wiring import _ctx_with_finding_inputs

DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
VEP_ID_A = "55555555-5555-5555-5555-555555555555"
VEP_ID_B = "99999999-5555-5555-5555-555555555555"

_DEBATE_SUMMARY = {
    "debate_id": "deb-1",
    "stop_reason": "consensus",
    "round_number": 1,
    "muhasabah_passed": True,
    "agent_output_count": 2,
}


def setup_function() -> None:
    clear_run_steps_store()
    clear_in_memory_layer1_evidence_store()


def _layer2_ctx(capture: list[dict[str, Any]]) -> RunContext:
    def layer2_fn(**kwargs: Any) -> dict[str, Any]:
        capture.append(kwargs)
        return {"status": "completed", "layer2_challenge_ids": ["layer2-001"]}

    return RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        layer2_ic_challenge_fn=layer2_fn,
    )


def _execute_layer2(accumulated: dict[str, Any]) -> dict[str, Any]:
    capture: list[dict[str, Any]] = []
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    orchestrator._execute_layer2_ic_challenge(  # noqa: SLF001
        _layer2_ctx(capture), accumulated
    )
    assert len(capture) == 1
    return capture[0]


# --- Orchestrator threading: accumulated["layer1_persistence"]["package_ids"] -> layer2 ---


def test_execute_layer2_threads_vep_package_ids_from_accumulated() -> None:
    received = _execute_layer2(
        {
            "created_claim_ids": ["claim_mth_0123456789abcdef01234567"],
            "calc_ids": ["calc-1"],
            "layer1_persistence": {
                "status": "persisted",
                "package_row_count": 2,
                "package_ids": [VEP_ID_B, VEP_ID_A],
            },
        }
    )
    assert received["vep_package_ids"] == [VEP_ID_B, VEP_ID_A]


def test_execute_layer2_threads_none_when_absent_or_malformed() -> None:
    # Absent key -> None (null-safe, Slice91 pattern).
    assert _execute_layer2({})["vep_package_ids"] is None
    # Malformed shapes degrade to None, never crash.
    assert _execute_layer2({"layer1_persistence": "not-a-dict"})["vep_package_ids"] is None
    assert (
        _execute_layer2({"layer1_persistence": {"package_ids": "not-a-list"}})["vep_package_ids"]
        is None
    )


# --- Route fn: result surfaces safe, sorted, deduped vep_ref_ids for the ledger ---


def test_layer2_result_surfaces_safe_sorted_vep_ref_ids() -> None:
    result = _run_full_layer2_ic_challenge(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        debate_summary=dict(_DEBATE_SUMMARY),
        created_claim_ids=["claim_mth_0123456789abcdef01234567"],
        calc_ids=["calc-1"],
        vep_package_ids=[VEP_ID_B, VEP_ID_A, VEP_ID_A, "", 7],  # type: ignore[list-item]
    )
    # Sorted, deduped, strings only — junk entries are dropped, never coerced.
    assert result["vep_ref_ids"] == sorted([VEP_ID_A, VEP_ID_B])
    assert result["status"] == "completed"


def test_layer2_result_vep_ref_ids_empty_when_absent() -> None:
    result = _run_full_layer2_ic_challenge(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        debate_summary=dict(_DEBATE_SUMMARY),
        created_claim_ids=["claim_mth_0123456789abcdef01234567"],
        calc_ids=["calc-1"],
    )
    assert result["vep_ref_ids"] == []


# --- Resume proof: the REAL existing.result_summary skip path feeds Layer 2 ---


def _full_run_ctx(
    run_id: str,
    *,
    layer2_fn: Any,
    vep_call_counter: list[int] | None = None,
) -> RunContext:
    """Minimal FULL RunContext (slice63-style injected fns) for orchestrated runs."""

    def counting_vep_fn(**kwargs: Any) -> Any:
        if vep_call_counter is not None:
            vep_call_counter.append(1)
        from idis.services.runs.methodology_validated_evidence_package import (
            InMemoryRunMethodologyValidatedEvidencePackageService,
        )

        return InMemoryRunMethodologyValidatedEvidencePackageService().run(**kwargs)

    return RunContext(
        run_id=run_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=_documents(),
        deal_metadata={"tenant_id": TENANT_ID, "company_name": "Acme Corp"},
        extract_fn=lambda **_kwargs: {"created_claim_ids": ["claim-001"]},
        grade_fn=lambda **_kwargs: {},
        calc_fn=lambda **_kwargs: {"calc_ids": ["calc-001"]},
        graph_fn=lambda **_kwargs: {"graph_status": "skipped"},
        rag_fn=lambda **_kwargs: {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 1},
            "rag_retrieval": {
                "status": "probed",
                "retrieval_mode": "probe",
                "probe_count": 1,
                "match_count": 0,
                "matches": [],
            },
        },
        enrich_fn=lambda **_kwargs: {},
        debate_fn=lambda **_kwargs: {
            "debate_id": run_id,
            "stop_reason": "consensus",
            "round_number": 1,
            "muhasabah_passed": True,
            "agent_output_count": 2,
        },
        methodology_validated_evidence_package_fn=counting_vep_fn,
        layer2_ic_challenge_fn=layer2_fn,
        analysis_fn=lambda **_kwargs: {"_analysis_bundle": {}, "_analysis_context": {}},
        scoring_fn=lambda **_kwargs: {"_scorecard": {}},
        deliverables_fn=lambda **_kwargs: {"deliverable_count": 1},
    )


def _seed_completed_run_then_reset_layer2(run_id: str) -> InMemoryRunStepsRepository:
    """Run a FULL pipeline once, inject VEP package ids into its COMPLETED ledger
    row, and reset only LAYER2_IC_CHALLENGE so a resume re-executes just that step."""
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    first = orchestrator.execute(
        _full_run_ctx(run_id, layer2_fn=lambda **_kwargs: {"status": "completed"})
    )
    assert first.status == "SUCCEEDED"

    vep_step = repo.get_step(run_id, StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE)
    assert vep_step is not None and vep_step.status == StepStatus.COMPLETED
    vep_step.result_summary = {
        **(vep_step.result_summary or {}),
        "layer1_persistence": {
            "status": "persisted",
            "package_row_count": 1,
            "package_ids": [VEP_ID_A],
        },
    }
    repo.update(vep_step)

    layer2_step = repo.get_step(run_id, StepName.LAYER2_IC_CHALLENGE)
    assert layer2_step is not None
    layer2_step.status = StepStatus.PENDING
    repo.update(layer2_step)
    return repo


def test_resume_skips_completed_vep_and_threads_its_ledger_package_ids() -> None:
    run_id = str(uuid_mod.uuid4())
    repo = _seed_completed_run_then_reset_layer2(run_id)

    # Resume: every completed step (including VEP) is SKIPPED and rehydrated via the
    # real accumulated.update(existing.result_summary) path; only LAYER2 executes.
    captured: list[dict[str, Any]] = []
    vep_calls: list[int] = []

    def capturing_layer2(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"status": "completed", "layer2_challenge_ids": ["layer2-001"]}

    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    result = orchestrator.execute(
        _full_run_ctx(run_id, layer2_fn=capturing_layer2, vep_call_counter=vep_calls)
    )

    assert result.status == "SUCCEEDED"
    assert vep_calls == []  # the completed VEP step was skipped, not re-executed
    assert len(captured) == 1
    # Layer 2 received EXACTLY the ids from the completed VEP step's ledger summary.
    assert captured[0]["vep_package_ids"] == [VEP_ID_A]


def test_resume_layer2_ledger_row_emits_vep_ref_ids_via_real_fn() -> None:
    run_id = str(uuid_mod.uuid4())
    repo = _seed_completed_run_then_reset_layer2(run_id)

    # Resume with the REAL Layer-2 route fn: its result (with vep_ref_ids) is what
    # _complete_step persists into the LAYER2 ledger row.
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    result = orchestrator.execute(_full_run_ctx(run_id, layer2_fn=_run_full_layer2_ic_challenge))

    assert result.status == "SUCCEEDED"
    layer2_step = repo.get_step(run_id, StepName.LAYER2_IC_CHALLENGE)
    assert layer2_step is not None and layer2_step.status == StepStatus.COMPLETED
    assert layer2_step.result_summary["vep_ref_ids"] == [VEP_ID_A]


# --- End-to-end: the PERSISTED package id is the one Layer 2 receives ---


def test_persisted_vep_id_reaches_layer2_end_to_end() -> None:
    run_id = str(uuid_mod.uuid4())
    ctx = _ctx_with_finding_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    court_summary = orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001
    vep_summary = orchestrator._execute_methodology_validated_evidence_package(  # noqa: SLF001
        ctx, accumulated=court_summary
    )

    # Mirror the orchestrator main loop: step summaries merge into accumulated.
    accumulated: dict[str, Any] = {}
    accumulated.update(court_summary)
    accumulated.update(vep_summary)

    received = _execute_layer2(accumulated)

    twin = InMemoryLayer1EvidenceRepository(TENANT_ID)
    persisted = twin.list_validated_evidence_packages(run_id=run_id)
    assert [item["package_id"] for item in persisted] == received["vep_package_ids"]
    assert received["vep_package_ids"] == [ctx.methodology_validated_evidence_package.package_id]
