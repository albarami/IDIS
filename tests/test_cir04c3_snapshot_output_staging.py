"""Synthetic component REDs for fresh worker-only SNAPSHOT output custody.

These protocol doubles exercise actual orchestrator boundaries. They are not
source admission, PostgreSQL commit, native parser or canonical receipt proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import SNAPSHOT_STEPS, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.persistence.repositories.runs import _resolve_run_contract_identity
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError


def _fresh(mode: str = "SNAPSHOT") -> tuple[RunContext, RunOrchestrator]:
    tenant, deal, run, owner = (str(uuid4()) for _ in range(4))
    version, digest, state, error = _resolve_run_contract_identity(
        mode=mode,
        pipeline_contract_version=None,
        pipeline_contract_hash=None,
        pipeline_resume_state=None,
        pipeline_resume_error_code=None,
    )
    ctx = RunContext(
        tenant_id=tenant,
        deal_id=deal,
        run_id=run,
        mode=mode,
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        pipeline_contract_version=version,
        pipeline_contract_hash=digest,
        pipeline_resume_state=state,
        lease_owner_id=owner,
        lease_fencing_token=1,
    )
    persisted = {
        "tenant_id": tenant,
        "deal_id": deal,
        "run_id": run,
        "mode": mode,
        "status": "RUNNING",
        "strict_preflight_proof_id": None,
        "lease_owner_id": owner,
        "lease_fencing_token": 1,
        "pipeline_contract_version": version,
        "pipeline_contract_hash": digest,
        "pipeline_resume_state": state,
        "pipeline_resume_error_code": error,
    }
    return ctx, RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        runs_repo=SimpleNamespace(get=lambda _run_id: persisted),
        run_steps_repo=InMemoryRunStepsRepository(tenant),
    )


def test_required_worker_composition_missing_refuses_after_real_fresh_guard() -> None:
    ctx, orchestrator = _fresh()
    ctx.snapshot_output_composition_required = True  # type: ignore[attr-defined]
    with pytest.raises(RunStepBlockedError) as caught:
        orchestrator._resolve_pipeline_step_sequence(ctx)
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_inline_fresh_snapshot_without_worker_marker_keeps_existing_path() -> None:
    ctx, orchestrator = _fresh()
    assert orchestrator._resolve_pipeline_step_sequence(ctx) == tuple(SNAPSHOT_STEPS)
    assert ctx.strict_resume_validation_required is False
    assert getattr(ctx, "snapshot_output_composition", None) is None


def test_full_is_not_required_to_activate_snapshot_composition() -> None:
    ctx, orchestrator = _fresh("FULL")
    ctx.snapshot_output_composition_required = True  # type: ignore[attr-defined]
    assert StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING in (
        orchestrator._resolve_pipeline_step_sequence(ctx)
    )
    assert getattr(ctx, "fresh_snapshot_initial_execution", False) is False


@dataclass(frozen=True)
class _FailureAuditWrite:
    event_type: str = "sanad.integrity.failed"


class _Composition:
    active = True

    def __init__(self) -> None:
        self.aborted: list[StepName] = []
        self.sealed: list[tuple[StepName, dict[str, Any]]] = []

    def boundary_failed(self, **_kwargs: Any) -> None:
        pass

    def abort_step_and_stage_failure_audits(
        self, *, ctx: RunContext, step_name: StepName
    ) -> tuple[_FailureAuditWrite, ...]:
        assert ctx.mode == "SNAPSHOT"
        self.aborted.append(step_name)
        return (_FailureAuditWrite(),)

    def seal_success(
        self, *, ctx: RunContext, step_name: StepName, result_summary: dict[str, Any]
    ) -> None:
        assert ctx.mode == "SNAPSHOT"
        self.sealed.append((step_name, result_summary))


def test_failed_boundary_aborts_partial_rows_and_passes_only_failure_audits() -> None:
    ctx, orchestrator = _fresh()
    composition = _Composition()
    ctx.fresh_snapshot_initial_execution = True  # type: ignore[attr-defined]
    ctx.snapshot_output_composition_required = True  # type: ignore[attr-defined]
    ctx.snapshot_output_composition = composition  # type: ignore[attr-defined]
    business_write = object()
    ctx.methodology_pending_native_output_writes[StepName.GRADE] = (business_write,)
    ctx.stage_native_output_writes_fn = lambda **_kwargs: (business_write,)
    captured: list[object] = []

    def boundary(**kwargs: Any) -> None:
        captured.extend(kwargs["native_output_writes"])

    ctx.step_boundary_uow_fn = boundary
    step = RunStep(
        step_id=str(uuid4()),
        tenant_id=ctx.tenant_id,
        run_id=ctx.run_id,
        step_name=StepName.GRADE,
        step_order=18,
        status=StepStatus.RUNNING,
    )
    orchestrator._fail_step_boundary(ctx, step, RuntimeError("controlled producer rejection"))
    assert captured == [_FailureAuditWrite()]
    assert composition.aborted == [StepName.GRADE]
    assert StepName.GRADE not in ctx.methodology_pending_native_output_writes


def test_dispatch_seals_post_merge_result_only_after_after_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, orchestrator = _fresh()
    composition = _Composition()
    ctx.snapshot_output_composition = composition  # type: ignore[attr-defined]
    ctx.snapshot_output_composition_required = True  # type: ignore[attr-defined]
    ctx.fresh_snapshot_initial_execution = True  # type: ignore[attr-defined]
    summary = {"created_claim_ids": [str(uuid4())]}
    monkeypatch.setattr(orchestrator, "_dispatch_step", lambda *_args: summary)
    phases: list[str] = []

    def guard(**kwargs: Any) -> None:
        phases.append(kwargs["phase"])
        assert composition.sealed == []

    ctx.provider_output_guard_fn = guard
    assert orchestrator._dispatch_guarded_step(StepName.EXTRACT, ctx, {}) is summary
    assert phases == ["before", "after"]
    assert composition.sealed == [(StepName.EXTRACT, summary)]


def test_after_guard_rejection_never_seals_success(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, orchestrator = _fresh()
    composition = _Composition()
    ctx.snapshot_output_composition = composition  # type: ignore[attr-defined]
    ctx.snapshot_output_composition_required = True  # type: ignore[attr-defined]
    ctx.fresh_snapshot_initial_execution = True  # type: ignore[attr-defined]
    monkeypatch.setattr(orchestrator, "_dispatch_step", lambda *_args: {})

    def guard(**kwargs: Any) -> None:
        if kwargs["phase"] == "after":
            raise RunStepBlockedError("PIPELINE_CONTRACT_UNAVAILABLE", "controlled write latch")

    ctx.provider_output_guard_fn = guard
    with pytest.raises(RunStepBlockedError):
        orchestrator._dispatch_guarded_step(StepName.EXTRACT, ctx, {})
    assert composition.sealed == []


class _CreateWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(kwargs)


def test_claim_service_optional_writer_preserves_uuid_and_nff_before_capture() -> None:
    from idis.services.claims.service import (
        ClaimService,
        CreateClaimInput,
        NoFreeFactsViolationError,
    )

    writer = _CreateWriter()
    service = ClaimService(str(uuid4()), create_writer=writer)
    with pytest.raises(NoFreeFactsViolationError):
        service.create(
            CreateClaimInput(
                deal_id=str(uuid4()), claim_class="OTHER", claim_text="Unbacked fact", ic_bound=True
            )
        )
    assert writer.calls == []
    row = service.create(
        CreateClaimInput(
            deal_id=str(uuid4()),
            claim_class="OTHER",
            claim_text="Synthetic component",
            primary_span_id=str(uuid4()),
        )
    )
    assert len(writer.calls) == 1
    assert row["claim_id"] == writer.calls[0]["claim_id"]
    assert row["primary_span_id"] == writer.calls[0]["primary_span_id"]


def test_sanad_service_optional_writer_keeps_actual_integrity_and_computed_payload() -> None:
    from idis.services.sanad.service import CreateSanadInput, SanadService

    writer = _CreateWriter()
    service = SanadService(str(uuid4()), create_writer=writer)
    row = service.create(
        CreateSanadInput(
            claim_id=str(uuid4()),
            deal_id=str(uuid4()),
            primary_evidence_id=str(uuid4()),
            extraction_confidence=0.81,
            dhabt_score=0.78,
        )
    )
    assert len(writer.calls) == 1
    assert row["sanad_id"] == writer.calls[0]["sanad_id"]
    assert row["computed"]["extraction_confidence"] == 0.81
    assert row["computed"]["dhabt_score"] == 0.78
    assert row["transmission_chain"][0]["input_refs"] == [
        {"evidence_id": row["primary_evidence_id"]}
    ]


def test_defect_service_optional_writer_keeps_severity_and_open_status() -> None:
    from idis.services.defects.service import CreateDefectInput, DefectService

    writer = _CreateWriter()
    row = DefectService(str(uuid4()), create_writer=writer).create(
        CreateDefectInput(
            claim_id=str(uuid4()),
            deal_id=str(uuid4()),
            defect_type="BROKEN_CHAIN",
            description="Synthetic component chain rejection",
            cure_protocol="RECONSTRUCT_CHAIN",
        )
    )
    assert len(writer.calls) == 1
    assert row["defect_id"] == writer.calls[0]["defect_id"]
    assert row["severity"] == "FATAL" and row["status"] == "OPEN"


def test_extraction_poison_callback_observes_failure_before_any_writer_call() -> None:
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.extraction.resolution.deduplicator import DeduplicatedClaim

    writer = _CreateWriter()
    poisoned: list[bool] = []
    # Only _persist_claims is exercised; no extractor/provider/chunker is called.
    pipeline = ExtractionPipeline(
        chunking_service=SimpleNamespace(),
        claim_extractor=SimpleNamespace(),
        deduplicator=SimpleNamespace(),
        conflict_detector=SimpleNamespace(),
        claim_service=SimpleNamespace(tenant_id=str(uuid4()), create=writer.create),
        on_persistence_failure=lambda: poisoned.append(True),
    )
    assert (
        pipeline._persist_claims(
            unique_claims=[
                DeduplicatedClaim(
                    claim_text="",
                    claim_class="OTHER",
                    extraction_confidence=Decimal("0.8"),
                    span_ids=[str(uuid4())],
                    identity_hash=str(uuid4()),
                )
            ],
            tenant_id=str(uuid4()),
            deal_id=str(uuid4()),
        )
        == []
    )
    assert writer.calls == []
    assert poisoned == [True]


def test_auto_grade_injects_only_claim_get_and_grade_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.sanad.auto_grade as module

    claim_id, sanad_id = str(uuid4()), str(uuid4())
    calls: list[tuple[str, dict[str, Any]]] = []

    class Claims:
        def get(self, requested: str) -> dict[str, Any]:
            assert requested == claim_id
            return {
                "claim_id": claim_id,
                "corroboration": {"extraction_confidence": "0.81", "dhabt_score": "0.78"},
            }

        def update_grade(self, requested: str, **kwargs: Any) -> None:
            calls.append((requested, kwargs))

    def grade(**kwargs: Any) -> module.ClaimGradeResult:
        # Explicit component wiring probe, not a numerical/grading algorithm proof.
        assert kwargs["extraction_confidence"] == 0.81
        assert kwargs["dhabt_score"] == 0.78
        return module.ClaimGradeResult(claim_id=claim_id, sanad_id=sanad_id, grade="B")

    monkeypatch.setattr(module, "_grade_single_claim", grade)
    result = module.auto_grade_claims_for_run(
        run_id=str(uuid4()),
        tenant_id=str(uuid4()),
        deal_id=str(uuid4()),
        created_claim_ids=[claim_id],
        evidence_repo=SimpleNamespace(),
        sanad_service=SimpleNamespace(),
        defect_service=SimpleNamespace(),
        audit_sink=InMemoryAuditSink(),
        claims_repo=Claims(),
    )
    assert result.graded_count == 1 and result.failed_count == 0
    assert calls == [(claim_id, {"claim_grade": "B", "sanad_id": sanad_id})]


def test_typed_capture_freezes_json_and_rejects_unbacked_merged_ids() -> None:
    import importlib.util

    assert (
        importlib.util.find_spec("idis.services.runs.snapshot_postgres_output_staging") is not None
    ), "worker SNAPSHOT requires an explicit typed capture composition"
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    ctx, _ = _fresh()
    span_id, claim_id, evidence_id = (str(uuid4()) for _ in range(3))
    ctx.documents = [{"spans": [{"span_id": span_id}]}]
    composition = SnapshotOutputComposition(
        provider_db_conn=SimpleNamespace(execute=lambda *_args, **_kwargs: None),
        audit_sink=InMemoryAuditSink(),
    )
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    value = {"value": "1000", "metadata": {"unit": "USD"}}
    row = draft.claim_writer.create(
        claim_id=claim_id,
        deal_id=ctx.deal_id,
        claim_class="FINANCIAL",
        claim_text="Synthetic typed capture",
        value=value,
        primary_span_id=span_id,
    )
    draft.evidence_repo.create(
        evidence_id=evidence_id,
        tenant_id=ctx.tenant_id,
        deal_id=ctx.deal_id,
        claim_id=claim_id,
        source_span_id=span_id,
    )
    value["metadata"]["unit"] = "MUTATED"
    assert row["claim_id"] == claim_id
    assert draft.claim_rows[0].arguments()["value"]["metadata"]["unit"] == "USD"
    draft.mark_ready({"created_claim_ids": [claim_id], "unique_claim_count": 1})
    with pytest.raises(RunStepBlockedError):
        composition.seal_success(
            ctx=ctx,
            step_name=StepName.EXTRACT,
            result_summary={"created_claim_ids": [claim_id, str(uuid4())], "unique_claim_count": 1},
        )
    assert StepName.EXTRACT not in ctx.methodology_pending_native_output_writes


def test_foreign_evidence_capture_poisons_even_when_producer_swallows_error() -> None:
    import importlib.util

    assert (
        importlib.util.find_spec("idis.services.runs.snapshot_postgres_output_staging") is not None
    ), "typed evidence capture is missing"
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    ctx, _ = _fresh()
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=InMemoryAuditSink())
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    with pytest.raises(RunStepBlockedError):
        draft.evidence_repo.create(
            evidence_id=str(uuid4()),
            tenant_id=str(uuid4()),
            deal_id=ctx.deal_id,
            claim_id=str(uuid4()),
            source_span_id=str(uuid4()),
        )
    draft.mark_ready({"created_claim_ids": [], "unique_claim_count": 0})
    with pytest.raises(RunStepBlockedError):
        composition.seal_success(
            ctx=ctx,
            step_name=StepName.EXTRACT,
            result_summary={"created_claim_ids": [], "unique_claim_count": 0},
        )


def test_complete_custody_assertion_runs_inside_boundary_before_ledger() -> None:
    ctx, orchestrator = _fresh()
    composition = _Composition()
    observations: list[str] = []

    def reject(**_kwargs: Any) -> None:
        observations.append("custody")
        raise RunStepBlockedError("PIPELINE_CONTRACT_UNAVAILABLE", "omitted writer")

    composition.before_complete_step = reject  # type: ignore[attr-defined]
    composition.boundary_failed = lambda **_kwargs: observations.append("invalidated")  # type: ignore[attr-defined]
    ctx.snapshot_output_composition = composition  # type: ignore[assignment]
    ctx.fresh_snapshot_initial_execution = True

    def boundary(**kwargs: Any) -> None:
        observations.append("transaction")
        kwargs["mutation"]()

    ctx.step_boundary_uow_fn = boundary
    step = RunStep(
        step_id=str(uuid4()),
        tenant_id=ctx.tenant_id,
        run_id=ctx.run_id,
        step_name=StepName.EXTRACT,
        step_order=17,
        status=StepStatus.RUNNING,
    )
    with pytest.raises(RunStepBlockedError):
        orchestrator._complete_step_boundary(ctx, step, {})
    assert observations == ["transaction", "custody", "invalidated"]
    assert step.status == StepStatus.RUNNING


@pytest.mark.parametrize("step", ["extract", "grade"])
def test_staged_wrapper_requires_complete_dependencies_before_producer(step: str) -> None:
    from idis.api.routes.runs import _run_snapshot_auto_grade, _run_snapshot_extraction

    kwargs: dict[str, Any] = {
        "run_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "deal_id": str(uuid4()),
    }
    if step == "extract":
        function = _run_snapshot_extraction
        kwargs.update(documents=[], claim_create_writer=_CreateWriter())
    else:
        function = _run_snapshot_auto_grade
        kwargs.update(
            created_claim_ids=[],
            audit_sink=InMemoryAuditSink(),
            sanad_create_writer=_CreateWriter(),
        )
    with pytest.raises(RunStepBlockedError) as caught:
        function(**kwargs)
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_seal_accepts_actual_sorted_merge_but_not_extra_ids() -> None:
    from idis.services.extraction.canonical_methodology_materialization import (
        merge_main_evidence_claim_ids,
    )
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    class Sink(InMemoryAuditSink):
        def emit_in_tx(self, _conn: Any, _event: dict[str, Any]) -> None:
            raise AssertionError("capture/seal must not emit SQL or durable audits")

    ctx, _ = _fresh()
    span = str(uuid4())
    ctx.documents = [{"spans": [{"span_id": span}]}]
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=Sink())
    composition.activate(ctx=ctx)
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    identities = sorted([str(uuid4()), str(uuid4())], reverse=True)
    for claim_id in identities:
        draft.claim_writer.create(
            claim_id=claim_id,
            deal_id=ctx.deal_id,
            claim_class="OTHER",
            claim_text="Synthetic merge component",
            primary_span_id=span,
        )
        draft.evidence_repo.create(
            evidence_id=str(uuid4()),
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            claim_id=claim_id,
            source_span_id=span,
        )
    summary = {"created_claim_ids": identities, "unique_claim_count": 2}
    draft.mark_ready(summary)
    merged = merge_main_evidence_claim_ids(
        methodology_claim_ids=[], generic_new_claim_ids=identities, reused_claim_ids=[]
    )
    composition.seal_success(
        ctx=ctx,
        step_name=StepName.EXTRACT,
        result_summary={**summary, "created_claim_ids": list(merged)},
    )
    assert len(ctx.methodology_pending_native_output_writes[StepName.EXTRACT]) == 4
    with pytest.raises(RunStepBlockedError):
        composition.before_complete_step(ctx=ctx, step_name=StepName.EXTRACT)


def test_grade_cannot_select_claims_before_extract_commit() -> None:
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    ctx, _ = _fresh()
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=InMemoryAuditSink())
    with pytest.raises(RunStepBlockedError):
        composition.begin_step(ctx=ctx, step_name=StepName.GRADE, claim_ids=[str(uuid4())])


def test_returned_draft_cannot_capture_a_late_audit() -> None:
    from idis.audit.canonical import build_canonical_audit_event
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    ctx, _ = _fresh()
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=InMemoryAuditSink())
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    draft.mark_ready({"created_claim_ids": [], "unique_claim_count": 0})
    event = build_canonical_audit_event(
        tenant_id=ctx.tenant_id,
        event_type="integration.extraction.pipeline_completed",
        severity="LOW",
        summary="Synthetic",
        actor_id="component",
        request_id=ctx.run_id,
        method="POST",
        path="/internal/component",
        status_code=200,
        resource_type="run",
        resource_id=ctx.run_id,
        deal_id=ctx.deal_id,
    )
    with pytest.raises(RunStepBlockedError):
        draft.audit_sink.emit(event)
    assert draft.poisoned and draft.operations == []


@pytest.mark.parametrize("step", [StepName.GRADE, StepName.CALC])
def test_seal_rejects_summary_outputs_not_in_actual_captured_operations(step: StepName) -> None:
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    class Sink(InMemoryAuditSink):
        def emit_in_tx(self, _conn: Any, _event: dict[str, Any]) -> None:
            raise AssertionError("empty component packet must not perform writes")

    ctx, _ = _fresh()
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=Sink())
    composition.activate(ctx=ctx)
    extract = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    summary = {"created_claim_ids": [], "unique_claim_count": 0}
    extract.mark_ready(summary)
    composition.seal_success(ctx=ctx, step_name=StepName.EXTRACT, result_summary=summary)
    # An explicit empty packet is present and complete, unlike a missing seal.
    assert ctx.methodology_pending_native_output_writes[StepName.EXTRACT] == ()
    composition.complete_committed(ctx=ctx, step_name=StepName.EXTRACT)
    draft = composition.begin_step(ctx=ctx, step_name=step, claim_ids=[])
    forged = (
        {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}
        if step == StepName.GRADE
        else {
            "calc_ids": [str(uuid4())],
            "reproducibility_hashes": ["0" * 64],
            "persisted_count": 0,
            "blocked_candidates": [],
        }
    )
    draft.mark_ready(forged)
    with pytest.raises(RunStepBlockedError):
        composition.seal_success(ctx=ctx, step_name=step, result_summary=forged)
    assert step not in ctx.methodology_pending_native_output_writes


def test_scoped_binding_non_row_identity_matches_frozen_inventory_and_declares_audits() -> None:
    from idis.audit.postgres_sink import PostgresAuditSink
    from idis.services.runs.snapshot_postgres_output_staging import (
        PendingCanonicalAudit,
        SnapshotOutputComposition,
    )
    from tests.slice100_worker_lease._support import REVIEWED_STEP_OUTPUT_BINDINGS

    ctx, _ = _fresh()
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=PostgresAuditSink())
    assert set(composition.contracts) == {
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        StepName.LAYER2_IC_CHALLENGE,
    }
    original_map = composition.contracts
    composition.configure_base_factories(ctx.tenant_id)
    original_factories = composition.repository_factories
    composition.activate(ctx=ctx)
    assert composition.contracts is original_map
    assert composition.repository_factories is original_factories
    assert composition.stage.contract_registry is original_map
    for step in (StepName.EXTRACT, StepName.GRADE, StepName.CALC):
        contract = composition.contracts[step]
        original = REVIEWED_STEP_OUTPUT_BINDINGS[step].produced_write_bindings
        writes = contract.write_bindings[:-1]
        assert len(writes) == len(original)
        for current, reviewed in zip(writes, original, strict=True):
            assert (
                f"{current.repository_type.__module__}.{current.repository_type.__qualname__}",
                current.persist_method,
                current.load_methods,
                current.sql_targets,
                current.non_sql_methods,
            ) == (
                reviewed.repository_type_qname,
                reviewed.persist_method,
                reviewed.load_methods,
                reviewed.sql_targets,
                reviewed.non_sql_methods,
            )
            # Lossless call payloads deliberately differ from legacy domain models.
            assert (
                f"{current.row_model_type.__module__}.{current.row_model_type.__qualname__}"
                != reviewed.row_type_qname
            )
        audit = contract.write_bindings[-1]
        assert audit.row_model_type is PendingCanonicalAudit
        assert audit.repository_type is PostgresAuditSink
        assert audit.persist_method == "emit_in_tx" and audit.load_methods == ()
        assert audit.sql_targets == frozenset({"audit_events"})
        assert (
            composition.repository_factories[step][PostgresAuditSink](None)
            is composition.audit_sink
        )
        assert contract.stage(ctx=ctx, result_summary={}) == ()


def test_calculation_capture_rejects_foreign_inputs_despite_matching_calc_sanad() -> None:
    from idis.models.deterministic_calculation import CalcType
    from idis.services.runs.snapshot_postgres_output_staging import (
        OutputScope,
        SnapshotOutputComposition,
        SnapshotStepDraft,
    )
    from tests.test_calc_runner import (
        DEAL_ID,
        TENANT_ID,
        FakeCalculationsRepository,
        _money_claim,
        _runner,
        _sanad,
    )

    revenue, cogs = str(uuid4()), str(uuid4())
    repository = FakeCalculationsRepository()
    result = _runner(
        claims={
            revenue: _money_claim(revenue, "revenue", "1000"),
            cogs: _money_claim(cogs, "cogs", "400"),
        },
        sanads={revenue: _sanad(revenue), cogs: _sanad(cogs)},
        calculations_repo=repository,
    ).run(created_claim_ids=[revenue, cogs], calc_types=[CalcType.GROSS_MARGIN])
    assert result["persisted_count"] == 1
    calculation, sanad = repository.created[0]
    ctx, _ = _fresh()
    ctx.tenant_id, ctx.deal_id = TENANT_ID, DEAL_ID
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=InMemoryAuditSink())
    # This is a typed-operation component probe, not a fresh-guard or storage
    # admission fixture: the exact numerical engine above produced both objects.
    draft = SnapshotStepDraft(
        composition,
        OutputScope.from_context(ctx, StepName.CALC),
        frozenset(),
        frozenset({revenue, cogs}),
    )
    wrong = calculation.model_copy(
        update={"inputs": calculation.inputs.model_copy(update={"claim_ids": [str(uuid4())]})}
    )
    with pytest.raises(RunStepBlockedError):
        draft.calculations.create(calculation=wrong, calc_sanad=sanad)
    assert draft.poisoned and draft.operations == []


def _nonempty_component_capture() -> tuple[Any, Any, Any, str, str, str]:
    """Actual typed capture only: no SQL, parser, freeze or runtime admission proof."""
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotOutputComposition

    class NoWriteSink(InMemoryAuditSink):
        def emit_in_tx(self, _conn: Any, _event: dict[str, Any]) -> None:
            raise AssertionError("capture must not execute durable audits")

    ctx, _ = _fresh()
    span, claim, evidence = (str(uuid4()) for _ in range(3))
    ctx.documents = [{"spans": [{"span_id": span}]}]
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=NoWriteSink())
    composition.activate(ctx=ctx)
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    draft.claim_writer.create(
        claim_id=claim,
        deal_id=ctx.deal_id,
        claim_class="OTHER",
        claim_text="Nonempty synthetic protocol capture",
        primary_span_id=span,
    )
    draft.evidence_repo.create(
        evidence_id=evidence,
        tenant_id=ctx.tenant_id,
        deal_id=ctx.deal_id,
        claim_id=claim,
        source_span_id=span,
    )
    return ctx, composition, draft, span, claim, evidence


@pytest.mark.parametrize("returned", ["none", "scoped", "tenant", "deal", "claim", "span"])
def test_unused_evidence_id_read_refuses_without_opening_reader_and_poisons(
    monkeypatch: pytest.MonkeyPatch, returned: str
) -> None:
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotStepDraft

    ctx, _, draft, span, claim, evidence = _nonempty_component_capture()
    row = {
        "evidence_id": evidence,
        "tenant_id": ctx.tenant_id,
        "deal_id": ctx.deal_id,
        "claim_id": claim,
        "source_span_id": span,
    }
    key = {"tenant": "tenant_id", "deal": "deal_id", "claim": "claim_id", "span": "source_span_id"}
    if returned in key:
        row[key[returned]] = str(uuid4())
    reads: list[str] = []

    def reader(_draft: Any) -> Any:
        reads.append("opened")
        return SimpleNamespace(get=lambda _identity: None if returned == "none" else row)

    monkeypatch.setattr(SnapshotStepDraft, "evidence_reader", reader)
    before = tuple(draft.operations)
    with pytest.raises(RunStepBlockedError) as caught:
        draft.evidence_repo.get(evidence)
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert draft.poisoned and tuple(draft.operations) == before
    assert reads == []


def test_swallowed_unused_evidence_lookup_cannot_seal_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.services.runs.snapshot_postgres_output_staging import SnapshotStepDraft

    ctx, composition, draft, _, claim, evidence = _nonempty_component_capture()
    monkeypatch.setattr(
        SnapshotStepDraft, "evidence_reader", lambda _draft: SimpleNamespace(get=lambda _id: None)
    )
    try:
        draft.evidence_repo.get(evidence)
    except RunStepBlockedError as error:
        assert error.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    summary = {"created_claim_ids": [claim], "unique_claim_count": 1}
    draft.mark_ready(summary)
    with pytest.raises(RunStepBlockedError) as caught:
        composition.seal_success(ctx=ctx, step_name=StepName.EXTRACT, result_summary=summary)
    assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
    assert draft.poisoned and StepName.EXTRACT not in ctx.methodology_pending_native_output_writes


def _output_uuid_spelling(spelling: str) -> Any:
    from uuid import UUID

    value = UUID("f1b879f4-951e-4df6-803f-a119206cb73b")
    return {
        "canonical": str(value),
        "uppercase": str(value).upper(),
        "braces": "{" + str(value) + "}",
        "hex": value.hex,
        "urn": value.urn,
        "malformed": "not-a-uuid",
        "none": None,
        "integer": 42,
        "uuid_object": value,
    }[spelling]


@pytest.mark.parametrize("writer", ["claim", "evidence"])
@pytest.mark.parametrize(
    "spelling",
    [
        "canonical",
        "uppercase",
        "braces",
        "hex",
        "urn",
        "malformed",
        "none",
        "integer",
        "uuid_object",
    ],
)
def test_capture_output_ids_require_canonical_uuid_without_normalizing(
    writer: str, spelling: str
) -> None:
    ctx, _, draft, span, claim, _ = _nonempty_component_capture()
    identity = _output_uuid_spelling(spelling)
    before_ops, before_ids = tuple(draft.operations), set(draft.ids)

    def create() -> dict[str, Any]:
        if writer == "claim":
            return draft.claim_writer.create(
                claim_id=identity,
                deal_id=ctx.deal_id,
                claim_class="OTHER",
                claim_text="Canonical output identity component",
                primary_span_id=span,
            )
        return draft.evidence_repo.create(
            evidence_id=identity,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            claim_id=claim,
            source_span_id=span,
        )

    if spelling == "canonical":
        row = create()
        assert row["claim_id" if writer == "claim" else "evidence_id"] == identity
        assert len(draft.operations) == len(before_ops) + 1
        assert draft.ids == before_ids | {identity} and not draft.poisoned
    else:
        with pytest.raises(RunStepBlockedError) as caught:
            create()
        assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
        assert draft.poisoned and tuple(draft.operations) == before_ops and draft.ids == before_ids


@pytest.mark.parametrize("spelling", ["canonical", "uppercase", "braces", "hex", "urn"])
def test_integrity_audit_resource_id_requires_canonical_uuid(spelling: str) -> None:
    from idis.audit.canonical import build_canonical_audit_event
    from idis.services.runs.snapshot_postgres_output_staging import (
        OutputScope,
        SnapshotOutputComposition,
        SnapshotStepDraft,
    )

    ctx, _ = _fresh()
    claim = str(uuid4())
    composition = SnapshotOutputComposition(provider_db_conn=None, audit_sink=InMemoryAuditSink())
    # A protocol-only failure channel with an authorized claim, not storage admission.
    draft = SnapshotStepDraft(
        composition, OutputScope.from_context(ctx, StepName.GRADE), frozenset(), frozenset({claim})
    )
    identity = _output_uuid_spelling(spelling)
    event = build_canonical_audit_event(
        tenant_id=ctx.tenant_id,
        event_type="sanad.integrity.failed",
        severity="CRITICAL",
        summary="Synthetic component integrity refusal",
        actor_id="component",
        request_id=ctx.run_id,
        method="POST",
        path="/internal/component",
        status_code=500,
        resource_type="sanad",
        resource_id=identity,
        deal_id=ctx.deal_id,
        safe={"claim_id": claim, "run_id": ctx.run_id},
    )
    if spelling == "canonical":
        draft.audit_sink.emit(event)
        assert len(draft.operations) == 1 and draft.operations[0].event == event
        assert draft.audit_ids == {event["event_id"]} and not draft.poisoned
    else:
        with pytest.raises(RunStepBlockedError) as caught:
            draft.audit_sink.emit(event)
        assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
        assert draft.poisoned and draft.operations == [] and draft.audit_ids == set()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("packet_kind", ["business", "failure_audit"])
def test_missing_exact_type_binding_refuses_before_any_operation_executes(
    missing: bool, packet_kind: str
) -> None:
    from dataclasses import replace

    from idis.audit.canonical import build_canonical_audit_event
    from idis.services.runs.snapshot_postgres_output_staging import (
        PendingCanonicalAudit,
        PendingEvidenceCreate,
    )

    ctx, composition, draft, _, claim, _ = _nonempty_component_capture()
    summary = {"created_claim_ids": [claim], "unique_claim_count": 1}
    if packet_kind == "business":
        missing_type = PendingEvidenceCreate  # The earlier Claim binding is still present.
        draft.mark_ready(summary)

        def stage() -> Any:
            composition.seal_success(ctx=ctx, step_name=StepName.EXTRACT, result_summary=summary)
            return ctx.methodology_pending_native_output_writes[StepName.EXTRACT]
    else:
        missing_type = PendingCanonicalAudit
        draft.audit_sink.emit(
            build_canonical_audit_event(
                tenant_id=ctx.tenant_id,
                event_type="integration.extraction.chunk_failed",
                severity="HIGH",
                summary="Synthetic component failed chunk",
                actor_id="component",
                request_id=ctx.run_id,
                method="POST",
                path="/internal/component",
                status_code=500,
                resource_type="run",
                resource_id=ctx.run_id,
                deal_id=ctx.deal_id,
            )
        )

        def stage() -> Any:
            return composition.abort_step_and_stage_failure_audits(
                ctx=ctx, step_name=StepName.EXTRACT
            )

    contract = composition.contracts[StepName.EXTRACT]
    if missing:
        composition.contracts[StepName.EXTRACT] = replace(
            contract,
            write_bindings=tuple(
                binding
                for binding in contract.write_bindings
                if binding.row_model_type is not missing_type
            ),
        )
        with pytest.raises(RunStepBlockedError) as caught:
            stage()
        assert caught.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"
        assert StepName.EXTRACT not in ctx.methodology_pending_native_output_writes
    else:
        writes = stage()
        assert len(writes) == (2 if packet_kind == "business" else 1)
        assert all(type(write.output) is write.binding.row_model_type for write in writes)
        assert all(not packet.returned for packet in composition._packets.values())
        assert all(not packet.returned for packet in composition._failure_packets.values())
