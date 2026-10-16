"""Shared run step wiring for canonical execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from idis.audit.sink import AuditSink
from idis.methodology.models import MethodologyRegistry
from idis.models.claim_materialization import (
    MethodologyOutputClaimMaterializationRunResult,
    RunScopedMaterializedClaim,
)
from idis.models.evidence_item_materialization import (
    MethodologyEvidenceItemMaterializationRunResult,
    RunScopedEvidenceItemRecord,
)
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionRunResult,
)
from idis.models.extraction_task import ExtractionTask, ExtractionTaskPlanningRunResult
from idis.models.methodology_coverage import (
    DurableMethodologyCoverageInitializationResult,
    MethodologyCoverageInitializationResult,
    MethodologyCoverageRecord,
)
from idis.models.run_source import RunSource
from idis.models.run_step import StepName
from idis.models.sanad_materialization import (
    MethodologySanadMaterializationRunResult,
    RunScopedSanadDefectRecord,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
)
from idis.models.validated_evidence_package_materialization import (
    RunScopedValidatedEvidencePackageRecord,
)
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.layer1_evidence import get_layer1_evidence_repository
from idis.services.runs.methodology_coverage_init import load_default_methodology_registry
from idis.services.runs.orchestrator import RunContext

CoverageInitFn = Callable[
    ...,
    tuple[
        MethodologyCoverageInitializationResult | DurableMethodologyCoverageInitializationResult,
        list[MethodologyCoverageRecord],
    ],
]
CoverageRehydrateFn = Callable[..., list[MethodologyCoverageRecord]]
TaskPlanningFn = Callable[
    ...,
    tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]],
]
TaskExecutionFn = Callable[
    ...,
    tuple[MethodologyExtractionExecutionRunResult, MethodologyExtractionExecutionResult],
]
ClaimMaterializationFn = Callable[
    ...,
    tuple[MethodologyOutputClaimMaterializationRunResult, list[RunScopedMaterializedClaim]],
]
EvidenceItemMaterializationFn = Callable[
    ...,
    tuple[MethodologyEvidenceItemMaterializationRunResult, list[RunScopedEvidenceItemRecord]],
]
SanadCreationLinkingGradingFn = Callable[
    ...,
    tuple[
        MethodologySanadMaterializationRunResult,
        list[RunScopedSanadRecord],
        list[RunScopedSanadLinkRecord],
        list[RunScopedSanadGradeRecord],
        list[RunScopedSanadDefectRecord],
    ],
]


@dataclass(frozen=True)
class NativeOutputWriteBinding:
    """Existing concrete repository binding used at a fenced completion boundary."""

    row_model_type: Any
    repository_type: type[Any]
    persist_method: str
    load_methods: tuple[str, ...]
    sql_targets: frozenset[str]
    non_sql_methods: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NativeOutputContract:
    """Typed native output that already has an approved durable substrate."""

    step_name: StepName
    output_type_id: str
    persistence_target: str
    output_model_type: Any
    write_bindings: tuple[NativeOutputWriteBinding, ...]
    stage: Callable[..., tuple[StagedNativeOutputWrite, ...]]


@dataclass(frozen=True)
class StagedNativeOutputWrite:
    """A typed row plus its completion-transaction persistence operation."""

    contract: NativeOutputContract
    binding: NativeOutputWriteBinding
    output: Any
    persist: Callable[..., None]


def build_postgres_native_output_stager_registry() -> dict[StepName, NativeOutputContract]:
    """Return contracts only for outputs with pre-Task7 concrete repositories."""
    from idis.models.layer1_durability import ValidatedEvidencePackageRow
    from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
    from idis.persistence.repositories.layer1_evidence import (
        PostgresLayer1EvidenceRepository,
    )
    from idis.persistence.repositories.layer2_challenge import (
        PostgresLayer2ChallengeRepository,
    )

    vep_binding = NativeOutputWriteBinding(
        row_model_type=ValidatedEvidencePackageRow,
        repository_type=PostgresLayer1EvidenceRepository,
        persist_method="upsert_validated_evidence_package",
        load_methods=("list_validated_evidence_packages",),
        sql_targets=frozenset({"validated_evidence_packages"}),
    )
    layer2_challenge_binding = NativeOutputWriteBinding(
        row_model_type=Layer2ChallengeRow,
        repository_type=PostgresLayer2ChallengeRepository,
        persist_method="upsert_challenge",
        load_methods=("list_challenges",),
        sql_targets=frozenset({"layer2_ic_challenges"}),
    )
    layer2_finding_binding = NativeOutputWriteBinding(
        row_model_type=Layer2FindingRow,
        repository_type=PostgresLayer2ChallengeRepository,
        persist_method="upsert_finding",
        load_methods=("list_findings",),
        sql_targets=frozenset({"layer2_ic_findings"}),
    )

    registry: dict[StepName, NativeOutputContract] = {}

    def stage_vep(
        *,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> tuple[StagedNativeOutputWrite, ...]:
        _ = result_summary
        record = ctx.methodology_validated_evidence_package
        if not isinstance(record, RunScopedValidatedEvidencePackageRecord):
            return ()
        row = ValidatedEvidencePackageRow.from_record(record)
        contract = registry[StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE]

        def persist(
            *,
            db_conn: Any,
            lease_owner_id: str,
            lease_fencing_token: int,
        ) -> None:
            _ = (lease_owner_id, lease_fencing_token)
            repository = PostgresLayer1EvidenceRepository(db_conn, ctx.tenant_id)
            repository.upsert_validated_evidence_package(row)

        return (StagedNativeOutputWrite(contract, vep_binding, row, persist),)

    vep_contract = NativeOutputContract(
        step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        output_type_id="methodology_validated_evidence_package_output",
        persistence_target="postgres:validated_evidence_package+content_refs",
        output_model_type=ValidatedEvidencePackageRow,
        write_bindings=(vep_binding,),
        stage=stage_vep,
    )
    registry[vep_contract.step_name] = vep_contract

    def stage_layer2(
        *,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> tuple[StagedNativeOutputWrite, ...]:
        _ = result_summary
        record = getattr(ctx, "layer2_ic_challenge_record", None)
        if record is None:
            return ()
        contract = registry[StepName.LAYER2_IC_CHALLENGE]
        challenge_row = Layer2ChallengeRow.from_record(record)
        rows: list[tuple[NativeOutputWriteBinding, Any, str]] = [
            (layer2_challenge_binding, challenge_row, "upsert_challenge")
        ]
        rows.extend(
            (
                layer2_finding_binding,
                Layer2FindingRow.from_finding(
                    finding,
                    tenant_id=record.tenant_id,
                    deal_id=record.deal_id,
                    run_id=record.run_id,
                    challenge_id=record.layer2_challenge_id,
                ),
                "upsert_finding",
            )
            for finding in record.findings
        )
        staged: list[StagedNativeOutputWrite] = []
        repository_cache: dict[int, tuple[Any, Any]] = {}

        def repository_for(db_conn: Any) -> Any:
            cache_key = id(db_conn)
            cached = repository_cache.get(cache_key)
            if cached is not None and cached[0] is db_conn:
                return cached[1]
            repository = PostgresLayer2ChallengeRepository(db_conn, ctx.tenant_id)
            repository_cache[cache_key] = (db_conn, repository)
            return repository

        for binding, row, method_name in rows:

            def persist(
                *,
                db_conn: Any,
                lease_owner_id: str,
                lease_fencing_token: int,
                _row: Any = row,
                _method_name: str = method_name,
            ) -> None:
                _ = (lease_owner_id, lease_fencing_token)
                repository = repository_for(db_conn)
                getattr(repository, _method_name)(_row)

            staged.append(StagedNativeOutputWrite(contract, binding, row, persist))
        return tuple(staged)

    layer2_contract = NativeOutputContract(
        step_name=StepName.LAYER2_IC_CHALLENGE,
        output_type_id="layer2_ic_challenge_output",
        persistence_target=("postgres:layer2_role_outputs+challenges+findings+dissent+muhasabah"),
        output_model_type=(Layer2ChallengeRow, Layer2FindingRow),
        write_bindings=(layer2_challenge_binding, layer2_finding_binding),
        stage=stage_layer2,
    )
    registry[layer2_contract.step_name] = layer2_contract
    return registry


def build_postgres_native_output_stager() -> Callable[..., tuple[StagedNativeOutputWrite, ...]]:
    """Build the provider-side staging function; it never writes before COMPLETE."""
    registry = build_postgres_native_output_stager_registry()

    def stage(
        *,
        ctx: RunContext,
        step_name: StepName,
        result_summary: dict[str, Any],
    ) -> tuple[StagedNativeOutputWrite, ...]:
        contract = registry.get(step_name)
        if contract is None:
            return ()
        return contract.stage(ctx=ctx, result_summary=result_summary)

    stage.contract_registry = registry  # type: ignore[attr-defined]
    return stage


def build_run_context(
    *,
    db_conn: Any,
    tenant_id: str,
    run_id: str,
    deal_id: str,
    mode: str,
    documents: list[dict[str, Any]],
    deal_metadata: dict[str, Any] | None = None,
    data_room_root_path: str | None = None,
    preflight_corpus: list[dict[str, Any]] | None = None,
    created_by_actor_id: str | None = None,
    created_by_actor_type: str | None = None,
    strict_preflight_proof_id: str | None = None,
    pipeline_contract_version: str | None = None,
    pipeline_contract_hash: str | None = None,
    pipeline_resume_state: str | None = None,
    audit_sink: AuditSink,
    no_substantive_content_receipt_verifier: Callable[[Mapping[str, Any]], bool] | None = None,
    methodology_registry: MethodologyRegistry | None = None,
    methodology_registry_loader_fn: Callable[[], MethodologyRegistry] | None = None,
    methodology_coverage_init_fn: CoverageInitFn | None = None,
    methodology_coverage_rehydrate_fn: CoverageRehydrateFn | None = None,
    methodology_extraction_task_planning_fn: TaskPlanningFn | None = None,
    methodology_extraction_task_execution_fn: TaskExecutionFn | None = None,
    methodology_claim_materialization_fn: ClaimMaterializationFn | None = None,
    methodology_evidence_item_materialization_fn: EvidenceItemMaterializationFn | None = None,
    methodology_sanad_creation_linking_grading_fn: SanadCreationLinkingGradingFn | None = None,
    strict_live_extraction_required: bool = False,
    strict_live_debate_backend_required: bool = False,
    analysis_client_factory: Callable[[Any], Any] | None = None,
    scoring_client_factory: Callable[[Any], Any] | None = None,
    debate_role_runners_factory: Callable[[Any], Any] | None = None,
    lease_owner_id: str | None = None,
    lease_fencing_token: int | None = None,
    step_boundary_uow_fn: Callable[..., None] | None = None,
    after_step_boundary_commit_fn: Callable[..., None] | None = None,
    execute_with_lease_heartbeat_fn: Callable[..., Any] | None = None,
    stage_native_output_writes_fn: Callable[..., tuple[Any, ...]] | None = None,
    provider_output_guard_fn: Callable[..., None] | None = None,
) -> RunContext:
    """Build a RunContext with the canonical step callables.

    The callables still delegate to the existing route helper implementations
    until Phase 2.1 finishes extracting all route-local wiring.
    """
    from idis.api.routes.runs import (
        _run_full_analysis,
        _run_full_debate,
        _run_full_deliverables,
        _run_full_enrichment,
        _run_full_graph_evidence,
        _run_full_layer2_ic_challenge,
        _run_full_methodology_deterministic_calculation,
        _run_full_rag_evidence,
        _run_full_scoring,
        _run_snapshot_auto_grade,
        _run_snapshot_calc,
        _run_snapshot_extraction,
    )

    is_full = mode == "FULL"
    object_store = None
    if is_full:
        from idis.storage.defaults import build_configured_product_export_object_store

        object_store = build_configured_product_export_object_store()
    return RunContext(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode=mode,
        documents=documents,
        layer1_evidence_repository=get_layer1_evidence_repository(db_conn, tenant_id),
        created_by_actor_id=created_by_actor_id,
        created_by_actor_type=created_by_actor_type,
        strict_preflight_proof_id=strict_preflight_proof_id,
        pipeline_contract_version=pipeline_contract_version,
        pipeline_contract_hash=pipeline_contract_hash,
        pipeline_resume_state=pipeline_resume_state,
        no_substantive_content_receipt_verifier=no_substantive_content_receipt_verifier,
        lease_owner_id=lease_owner_id,
        lease_fencing_token=lease_fencing_token,
        step_boundary_uow_fn=step_boundary_uow_fn,
        after_step_boundary_commit_fn=after_step_boundary_commit_fn,
        execute_with_lease_heartbeat_fn=execute_with_lease_heartbeat_fn,
        stage_native_output_writes_fn=stage_native_output_writes_fn,
        provider_output_guard_fn=provider_output_guard_fn,
        strict_resume_validation_required=strict_live_extraction_required,
        deal_metadata=deal_metadata,
        data_room_root_path=data_room_root_path,
        preflight_corpus=preflight_corpus or documents,
        methodology_registry=methodology_registry,
        methodology_registry_loader_fn=methodology_registry_loader_fn
        or load_default_methodology_registry,
        methodology_coverage_init_fn=methodology_coverage_init_fn,
        methodology_coverage_rehydrate_fn=methodology_coverage_rehydrate_fn,
        methodology_extraction_task_planning_fn=methodology_extraction_task_planning_fn,
        methodology_extraction_task_execution_fn=methodology_extraction_task_execution_fn,
        methodology_claim_materialization_fn=methodology_claim_materialization_fn,
        methodology_evidence_item_materialization_fn=methodology_evidence_item_materialization_fn,
        methodology_sanad_creation_linking_grading_fn=methodology_sanad_creation_linking_grading_fn,
        methodology_deterministic_calculation_fn=(
            partial(_run_full_methodology_deterministic_calculation, db_conn=db_conn)
            if is_full
            else None
        ),
        extract_fn=partial(
            _run_snapshot_extraction,
            db_conn=db_conn,
            strict_live_extraction_required=strict_live_extraction_required,
            audit_sink=audit_sink,
        ),
        grade_fn=partial(_run_snapshot_auto_grade, db_conn=db_conn),
        calc_fn=partial(_run_snapshot_calc, db_conn=db_conn),
        graph_fn=(
            partial(_run_full_graph_evidence, db_conn=db_conn, audit_sink=audit_sink)
            if is_full
            else None
        ),
        rag_fn=partial(_run_full_rag_evidence, db_conn=db_conn) if is_full else None,
        enrich_fn=(
            partial(_run_full_enrichment, db_conn=db_conn, audit_sink=audit_sink)
            if is_full
            else None
        ),
        debate_fn=(
            partial(
                _run_full_debate,
                db_conn=db_conn,
                debate_role_runners_factory=debate_role_runners_factory,
                strict_live_debate_backend_required=strict_live_debate_backend_required,
            )
            if is_full
            else None
        ),
        layer2_ic_challenge_fn=(
            partial(_run_full_layer2_ic_challenge, db_conn=db_conn) if is_full else None
        ),
        analysis_fn=(
            partial(
                _run_full_analysis,
                db_conn=db_conn,
                deal_metadata=deal_metadata,
                analysis_client_factory=analysis_client_factory,
                strict_live_debate_backend_required=strict_live_debate_backend_required,
                audit_sink=audit_sink,
            )
            if is_full
            else None
        ),
        scoring_fn=(
            partial(
                _run_full_scoring,
                scoring_client_factory=scoring_client_factory,
                strict_live_debate_backend_required=strict_live_debate_backend_required,
                audit_sink=audit_sink,
            )
            if is_full
            else None
        ),
        deliverables_fn=partial(
            _run_full_deliverables,
            db_conn=db_conn,
            object_store=object_store,
            audit_sink=audit_sink,
        )
        if is_full
        else None,
    )


def load_documents_for_deal(
    *,
    db_conn: Any,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load parsed documents and spans for a deal from Postgres.

    The tenant-scoped repository sets RLS context before querying the corpus.
    """
    if db_conn is None:
        return []

    repo = PostgresDocumentsRepository(db_conn, tenant_id)
    run_documents: list[dict[str, Any]] = []
    for document in repo.list_documents_by_deal(deal_id, parsed_only=True):
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=document["document_id"],
        )
        if not spans:
            continue
        run_documents.append(_document_for_run(document, spans))
    return run_documents


def load_document_preflight_corpus_for_deal(
    *,
    db_conn: Any,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load the full persisted corpus for document preflight.

    This includes parsed and failed document rows. Failed/no-span rows are needed
    so parser triage can reconstruct blockers from safe metadata.
    """
    if db_conn is None:
        return []

    repo = PostgresDocumentsRepository(db_conn, tenant_id)
    corpus: list[dict[str, Any]] = []
    for document in repo.list_documents_by_deal(deal_id, parsed_only=False):
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=document["document_id"],
        )
        corpus.append(_document_for_preflight(document, spans))
    return corpus


def extraction_ready_documents_from_preflight_corpus(
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return parsed documents with spans in the legacy extraction-ready shape."""
    documents: list[dict[str, Any]] = []
    for document in corpus:
        spans = list(document.get("spans") or [])
        if str(document.get("parse_status", "PARSED")) != "PARSED":
            continue
        if not spans:
            continue
        documents.append(_document_for_run(document, spans))
    return documents


def filter_preflight_corpus_by_run_source(
    corpus: list[dict[str, Any]],
    source: dict[str, Any] | RunSource | None,
) -> list[dict[str, Any]]:
    """Apply a persisted run-source contract to a deal preflight corpus."""
    if source is None:
        return list(corpus)
    run_source = source if isinstance(source, RunSource) else RunSource.model_validate(source)
    requested_ids = set(run_source.document_ids)
    return [document for document in corpus if str(document.get("document_id")) in requested_ids]


def missing_document_ids_for_run_source(
    corpus: list[dict[str, Any]],
    source: RunSource,
) -> list[str]:
    """Return selected document IDs absent from the loaded deal corpus."""
    present_ids = {str(document.get("document_id")) for document in corpus}
    return [document_id for document_id in source.document_ids if document_id not in present_ids]


def _span_for_run(span: Any) -> dict[str, Any]:
    """Convert a repository span row to the run document span shape."""
    span_dict = (
        dict(span)
        if isinstance(span, dict)
        else {
            "span_id": str(span.span_id),
            "text_excerpt": span.text_excerpt,
            "locator": span.locator if isinstance(span.locator, dict) else {},
            "span_type": str(span.span_type),
            "content_hash": getattr(span, "content_hash", None),
        }
    )
    result: dict[str, Any] = {
        "span_id": str(span_dict["span_id"]),
        "text_excerpt": span_dict.get("text_excerpt"),
        "locator": span_dict.get("locator") if isinstance(span_dict.get("locator"), dict) else {},
        "span_type": str(span_dict["span_type"]),
    }
    if span_dict.get("content_hash"):
        result["content_hash"] = span_dict["content_hash"]
    return result


def _document_for_run(document: dict[str, Any], spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a repository document and spans to the run document shape."""
    result = {
        "document_id": document["document_id"],
        "doc_type": document["doc_type"],
        "document_name": document["document_name"],
        "spans": [_span_for_run(span) for span in spans],
    }
    metadata = dict(document.get("metadata") or {})
    if "parser_mode" in metadata:
        result["metadata"] = metadata
    return result


def _span_for_preflight(span: dict[str, Any]) -> dict[str, Any]:
    """Convert a repository span row to full preflight span shape."""
    return {
        "span_id": str(span["span_id"]),
        "tenant_id": str(span.get("tenant_id")) if span.get("tenant_id") is not None else None,
        "deal_id": str(span.get("deal_id")) if span.get("deal_id") is not None else None,
        "document_id": str(span["document_id"]),
        "span_type": str(span["span_type"]),
        "locator": span.get("locator") if isinstance(span.get("locator"), dict) else {},
        "text_excerpt": span.get("text_excerpt"),
        "content_hash": span.get("content_hash"),
    }


def _document_for_preflight(
    document: dict[str, Any],
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert repository document and spans to full preflight corpus shape."""
    return {
        "tenant_id": str(document["tenant_id"]),
        "deal_id": str(document["deal_id"]),
        "document_id": str(document["document_id"]),
        "doc_id": str(document["doc_id"]),
        "doc_type": str(document["doc_type"]),
        "parse_status": str(document["parse_status"]),
        "document_name": str(document.get("document_name") or document["document_id"]),
        "sha256": document.get("sha256"),
        "uri": document.get("uri"),
        "metadata": dict(document.get("metadata") or {}),
        "source_metadata": dict(
            document.get("source_metadata") or document.get("artifact_metadata") or {}
        ),
        "spans": [_span_for_preflight(span) for span in spans],
    }
