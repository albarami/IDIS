"""S2 snapshot calculation and grading, and S7 extraction.

Moved verbatim from ``idis.api.routes.runs``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from idis.audit.sink import AuditSink
from idis.services.runs.step_runtime.common import (
    _find_project_root,
    _resolve_run_audit_sink,
)
from idis.services.runs.step_runtime.roles import (
    ExtractorClientFactory,
    _build_extraction_llm_client,
    _build_extraction_provenance,
    _resolve_extraction_selection,
)

if TYPE_CHECKING:
    from idis.persistence.repositories.evidence import EvidenceRepo
    from idis.services.calc.runner import CalculationsWriter
    from idis.services.claims.service import ClaimCreateWriter
    from idis.services.defects.service import DefectCreateWriter
    from idis.services.sanad.auto_grade import ClaimsGradingRepository
    from idis.services.sanad.service import SanadCreateWriter

logger = logging.getLogger(__name__)


def _run_snapshot_calc(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_types: list[Any] | None = None,
    db_conn: Any = None,
    calculations_writer: CalculationsWriter | None = None,
) -> dict[str, Any]:
    """Run deterministic calculations for claims produced by extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        calc_types: Optional list of CalcType to run. None means run all.
        db_conn: SQLAlchemy connection for Postgres persistence.

    Returns:
        Dict with calc_ids and reproducibility_hashes.
    """
    from idis.models.deterministic_calculation import CalcType
    from idis.persistence.repositories.calculations import get_calculations_repository
    from idis.persistence.repositories.claims import (
        ClaimsRepository,
        InMemoryClaimsRepository,
        InMemorySanadsRepository,
        SanadsRepository,
    )
    from idis.services.calc.runner import CalcRunner

    typed_calc_types = (
        [
            calc_type if isinstance(calc_type, CalcType) else CalcType(str(calc_type))
            for calc_type in calc_types
        ]
        if calc_types
        else None
    )

    claims_repo: Any
    sanads_repo: Any
    if db_conn is not None:
        claims_repo = ClaimsRepository(db_conn, tenant_id)
        sanads_repo = SanadsRepository(db_conn, tenant_id)
    else:
        claims_repo = InMemoryClaimsRepository(tenant_id)
        sanads_repo = InMemorySanadsRepository(tenant_id)

    runner = CalcRunner(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claims_repo=claims_repo,
        sanads_repo=sanads_repo,
        calculations_repo=(
            calculations_writer
            if calculations_writer is not None
            else get_calculations_repository(db_conn, tenant_id)
        ),
    )
    result = runner.run(
        created_claim_ids=created_claim_ids,
        calc_types=typed_calc_types,
    )
    result["claim_count"] = len(created_claim_ids)
    return result


def _run_full_methodology_deterministic_calculation(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    materialized_claims: list[Any],
    sanads: list[Any],
    sanad_grades: list[Any],
    extraction_tasks: list[Any],
    db_conn: Any = None,
) -> tuple[Any, list[Any], list[Any]]:
    """Run FULL methodology deterministic calculations and persist them durably.

    The methodology service stays pure (it returns run-scoped in-memory records). This wrapper
    persists each calculation and its CalcSanad through the existing durable persistence path,
    preserving the deterministic UUID5 methodology calc_ids and skipping any id already stored so
    resume never double-writes. The returned tuple is unchanged for the orchestrator.
    """
    from idis.persistence.repositories.calculations import get_calculations_repository
    from idis.services.runs.methodology_deterministic_calculation import (
        InMemoryRunMethodologyDeterministicCalculationService,
    )

    service = InMemoryRunMethodologyDeterministicCalculationService()
    run_result, calculations, calc_sanads = service.run(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        materialized_claims=materialized_claims,
        sanads=sanads,
        sanad_grades=sanad_grades,
        extraction_tasks=extraction_tasks,
    )

    repo = get_calculations_repository(db_conn, tenant_id)
    existing_ids = {row["calc_id"] for row in repo.list_by_deal(deal_id)}
    calc_sanad_by_id = {record.calc_id: record.calc_sanad for record in calc_sanads}
    for record in calculations:
        calculation = record.calculation
        if calculation.calc_id in existing_ids:
            continue
        repo.create(
            calculation=calculation,
            calc_sanad=calc_sanad_by_id[calculation.calc_id],
        )
        existing_ids.add(calculation.calc_id)

    return run_result, calculations, calc_sanads


def _run_snapshot_auto_grade(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    audit_sink: AuditSink,
    db_conn: Any = None,
    claims_grading_repo: ClaimsGradingRepository | None = None,
    sanad_create_writer: SanadCreateWriter | None = None,
    defect_create_writer: DefectCreateWriter | None = None,
    evidence_repo: EvidenceRepo | None = None,
    staged_audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    """Run Sanad auto-grading for all claims produced by SNAPSHOT extraction.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction.
        audit_sink: App-level audit sink (required).
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with grading summary stats.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from idis.services.runs.orchestrator import RunStepBlockedError
    from idis.services.sanad.auto_grade import auto_grade_claims_for_run

    staged_dependencies = (
        claims_grading_repo,
        sanad_create_writer,
        defect_create_writer,
        evidence_repo,
        staged_audit_sink,
    )
    staged = any(value is not None for value in staged_dependencies)
    if staged and any(value is None for value in staged_dependencies):
        raise RunStepBlockedError(
            "PIPELINE_CONTRACT_UNAVAILABLE", "SNAPSHOT grading staged dependencies are incomplete"
        )

    if not created_claim_ids:
        return {
            "graded_count": 0,
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    try:
        if staged:
            from idis.services.defects.service import DefectService
            from idis.services.sanad.service import SanadService

            assert staged_audit_sink is not None
            grade_result = auto_grade_claims_for_run(
                run_id=run_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
                created_claim_ids=created_claim_ids,
                db_conn=db_conn,
                claims_repo=claims_grading_repo,
                evidence_repo=evidence_repo,
                sanad_service=SanadService(
                    tenant_id=tenant_id,
                    db_conn=db_conn,
                    audit_sink=staged_audit_sink,
                    create_writer=sanad_create_writer,
                ),
                defect_service=DefectService(
                    tenant_id=tenant_id,
                    db_conn=db_conn,
                    audit_sink=staged_audit_sink,
                    create_writer=defect_create_writer,
                ),
                audit_sink=staged_audit_sink,
            )
        elif db_conn is not None:
            with db_conn.begin_nested():
                grade_result = auto_grade_claims_for_run(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    created_claim_ids=created_claim_ids,
                    audit_sink=audit_sink,
                    db_conn=db_conn,
                )
        else:
            grade_result = auto_grade_claims_for_run(
                run_id=run_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
                created_claim_ids=created_claim_ids,
                audit_sink=audit_sink,
                db_conn=db_conn,
            )
    except SQLAlchemyError as exc:
        logger.warning("Sanad auto-grade blocked by persistence error", exc_info=True)
        raise RunStepBlockedError(
            "SANAD_AUTO_GRADE_PERSISTENCE_BLOCKED",
            "Sanad auto-grade is blocked by downstream persistence schema",
            result_summary={
                "graded_count": 0,
                "failed_count": len(created_claim_ids),
                "total_defects": 0,
                "all_failed": True,
                "blocker": "defects_schema_query",
            },
        ) from exc

    return {
        "graded_count": grade_result.graded_count,
        "failed_count": grade_result.failed_count,
        "total_defects": grade_result.total_defects,
        "all_failed": grade_result.all_failed,
    }


def _run_snapshot_extraction(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    db_conn: Any = None,
    extractor_client_factory: ExtractorClientFactory | None = None,
    strict_live_extraction_required: bool = False,
    audit_sink: AuditSink | None = None,
    claim_create_writer: ClaimCreateWriter | None = None,
    evidence_repo: EvidenceRepo | None = None,
    staged_audit_sink: AuditSink | None = None,
    on_persistence_failure: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute SNAPSHOT extraction pipeline synchronously.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        documents: List of document dicts with doc_type, document_id, spans.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with pipeline result status and stats.
    """
    from idis.persistence.repositories.evidence import get_evidence_repository
    from idis.services.claims.service import ClaimService
    from idis.services.extraction.chunking.service import ChunkingService
    from idis.services.extraction.confidence.scorer import ConfidenceScorer
    from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.extraction.resolution.conflict_detector import ConflictDetector
    from idis.services.extraction.resolution.deduplicator import Deduplicator

    staged_dependencies = (
        claim_create_writer,
        evidence_repo,
        staged_audit_sink,
        on_persistence_failure,
    )
    staged = any(value is not None for value in staged_dependencies)
    if staged and any(value is None for value in staged_dependencies):
        from idis.services.runs.orchestrator import RunStepBlockedError

        raise RunStepBlockedError(
            "PIPELINE_CONTRACT_UNAVAILABLE",
            "SNAPSHOT extraction staged dependencies are incomplete",
        )

    prompt_text = _get_extraction_prompt()
    output_schema = _get_extraction_output_schema()

    selection = _resolve_extraction_selection()
    llm_client = _build_extraction_llm_client(
        extractor_client_factory=extractor_client_factory,
        strict_live_extraction_required=strict_live_extraction_required,
        tenant_id=tenant_id,
    )
    scorer = ConfidenceScorer()
    extractor = LLMClaimExtractor(
        llm_client=llm_client,
        prompt_text=prompt_text,
        output_schema=output_schema,
        confidence_scorer=scorer,
    )

    effective_audit_sink = staged_audit_sink if staged else _resolve_run_audit_sink(audit_sink)
    assert effective_audit_sink is not None
    claim_service = ClaimService(
        tenant_id=tenant_id,
        db_conn=db_conn,
        audit_sink=effective_audit_sink,
        create_writer=claim_create_writer,
    )
    if evidence_repo is None:
        evidence_repo = get_evidence_repository(db_conn, tenant_id)

    pipeline = ExtractionPipeline(
        chunking_service=ChunkingService(),
        claim_extractor=extractor,
        deduplicator=Deduplicator(),
        conflict_detector=ConflictDetector(),
        claim_service=claim_service,
        evidence_repo=evidence_repo,
        audit_sink=effective_audit_sink,
        on_persistence_failure=on_persistence_failure,
    )

    result = pipeline.run(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=documents,
    )

    return {
        "status": result.status,
        "created_claim_ids": result.created_claim_ids,
        "chunk_count": result.chunk_count,
        "unique_claim_count": result.unique_claim_count,
        "conflict_count": result.conflict_count,
        "extraction_provenance": _build_extraction_provenance(
            selection=selection,
            strict_live_extraction_required=strict_live_extraction_required,
            client=llm_client,
        ),
    }


def _get_extraction_prompt() -> str:
    """Load EXTRACT_CLAIMS_V1 prompt text from disk.

    Fail-closed: raises FileNotFoundError if prompt file is missing.

    Returns:
        Prompt template string.
    """
    root = _find_project_root()
    prompt_path = root / "prompts" / "extract_claims" / "1.0.0" / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _get_extraction_output_schema() -> dict[str, Any]:
    """Load EXTRACT_CLAIMS_V1 output schema from disk.

    Fail-closed: raises FileNotFoundError if schema file is missing.

    Returns:
        JSON schema dict.
    """
    import json

    root = _find_project_root()
    schema_path = root / "schemas" / "extraction" / "extract_claims_output.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


_METHODOLOGY_EXTRACTION_PROMPT_ID = "METHODOLOGY_QUESTION_EXTRACTION_V1"
