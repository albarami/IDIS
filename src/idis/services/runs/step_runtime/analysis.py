"""S8 analysis, debate, and scoring steps moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import logging
from typing import Any

from idis.audit.sink import AuditSink
from idis.services.runs.step_runtime.common import (
    _STAGE_UNKNOWN_MESSAGE_LIMIT,
    _analysis_context_stage_text,
    _calc_assumptions,
    _calc_input_claim_ids,
    _calc_output_payload,
    _calc_output_summary,
    _claim_source_summary,
    _resolve_run_audit_sink,
)
from idis.services.runs.step_runtime.roles import (
    AnalysisClientFactory,
    DebateRoleRunnersFactory,
    ScoringClientFactory,
    _build_analysis_llm_client,
    _build_analysis_provenance,
    _build_debate_observability,
    _build_debate_provenance,
    _build_debate_role_runners,
    _build_scoring_llm_client,
    _build_scoring_provenance,
    _resolve_analysis_selection,
    _resolve_debate_selection,
    _resolve_scoring_selection,
)

logger = logging.getLogger(__name__)


def _retrieve_claims_for_debate(
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any = None,
) -> list[dict[str, Any]]:
    """Look up full claim data for debate context.

    Uses Postgres repositories when db_conn is available, otherwise
    falls back to in-memory stores.

    Maps repository field names to DebateContext expected fields:
      claim_grade → sanad_grade
      primary_span_id → source_doc
      extraction_confidence (from sanad) → confidence

    Args:
        tenant_id: Tenant UUID for scoped lookups.
        created_claim_ids: Claim IDs produced by extraction/grading.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        List of claim dicts with keys matching DebateContext serialization:
        claim_id, claim_text, claim_class, sanad_grade, source_doc, confidence.
    """
    from idis.persistence.repositories.claims import (
        ClaimsRepository,
        InMemoryClaimsRepository,
        InMemorySanadsRepository,
        SanadsRepository,
    )

    if db_conn is not None:
        claims_repo: ClaimsRepository | InMemoryClaimsRepository = ClaimsRepository(
            db_conn, tenant_id
        )
        sanads_repo: SanadsRepository | InMemorySanadsRepository = SanadsRepository(
            db_conn, tenant_id
        )
    else:
        claims_repo = InMemoryClaimsRepository(tenant_id)
        sanads_repo = InMemorySanadsRepository(tenant_id)

    debate_claims: list[dict[str, Any]] = []
    for cid in created_claim_ids:
        claim = claims_repo.get(cid)
        if claim is None:
            logger.warning("Claim %s not found in store for debate context", cid)
            debate_claims.append(
                {
                    "claim_id": cid,
                    "claim_text": "",
                    "claim_class": "",
                    "sanad_grade": "",
                    "source_doc": "",
                    "confidence": 0.0,
                }
            )
            continue

        sanad = sanads_repo.get_by_claim(cid)
        confidence = 0.0
        if sanad is not None:
            computed = sanad.get("computed", {})
            confidence = computed.get(
                "extraction_confidence",
                sanad.get("extraction_confidence", 0.0),
            )

        debate_claims.append(
            {
                "claim_id": cid,
                "claim_text": claim.get("claim_text", ""),
                "claim_class": claim.get("claim_class", ""),
                "sanad_grade": claim.get("claim_grade", ""),
                "source_doc": claim.get("primary_span_id", "") or "",
                "confidence": float(confidence),
            }
        )

    return debate_claims


def _run_full_debate(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    rag_retrieval: dict[str, Any] | None = None,
    deal_metadata: dict[str, Any] | None = None,
    db_conn: Any = None,
    debate_role_runners_factory: DebateRoleRunnersFactory | None = None,
    strict_live_debate_backend_required: bool = False,
) -> dict[str, Any]:
    """Run DebateOrchestrator for a FULL pipeline run.

    Constructs a DebateState from pipeline context, executes the debate,
    and converts the returned DebateState to a dict summary for the step ledger.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        rag_retrieval: Safe probe-retrieval summary from the RAG_EVIDENCE step
            (Slice91); whitelist-converted to an IDs/scores-only context section.
        deal_metadata: Deal record fields for identity. None keeps the prior
            literal deal name, sector, and stage.
        db_conn: SQLAlchemy connection (None for in-memory fallback).

    Returns:
        Dict with debate_id, stop_reason, round_number, muhasabah_passed,
        and agent_output_count.
    """
    from idis.analysis.models import AnalysisRagEvidence
    from idis.debate.orchestrator import DebateOrchestrator
    from idis.debate.roles.llm_role_runner import DebateContext
    from idis.models.debate import DebateConfig, DebateState

    debate_claims = _retrieve_claims_for_debate(tenant_id, created_claim_ids, db_conn=db_conn)
    if deal_metadata is None:
        deal_name = deal_id
        deal_sector = "Unknown"
        deal_stage = "Unknown"
        deal_summary = ""
    else:
        deal_name = str(deal_metadata.get("company_name") or "") or deal_id
        deal_sector = str(deal_metadata.get("sector") or "") or "Unknown"
        deal_stage = str(deal_metadata.get("stage") or "") or "Unknown"
        raw_summary = deal_metadata.get("description") or deal_metadata.get("summary") or ""
        deal_summary = str(raw_summary)

    calc_registry = _build_analysis_calc_registry(
        tenant_id=tenant_id,
        deal_id=deal_id,
        calc_ids=calc_ids,
        db_conn=db_conn,
    )
    calc_results: list[dict[str, Any]] = []
    for calc_id in calc_ids:
        reference = calc_registry.get(calc_id)
        if reference is None:
            calc_results.append(
                {
                    "calc_id": calc_id,
                    "calc_name": "",
                    "result_value": "",
                    "input_claim_ids": [],
                    "missing": True,
                }
            )
            continue
        calc_results.append(
            {
                "calc_id": calc_id,
                "calc_name": reference.calc_type,
                "result_value": reference.output_summary,
                "input_claim_ids": list(reference.input_claim_ids),
                "calc_grade": reference.calc_grade or "",
                "formula_version": reference.formula_version or "",
            }
        )

    context = DebateContext(
        deal_name=deal_name,
        deal_sector=deal_sector,
        deal_stage=deal_stage,
        deal_summary=deal_summary,
        claims=debate_claims,
        calc_results=calc_results,
        conflicts=[],
        rag_evidence=AnalysisRagEvidence.from_retrieval_summary(rag_retrieval).to_payload_section(),
    )

    state = DebateState(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_registry_ref=f"claims://{run_id}",
        sanad_graph_ref=f"sanad://{run_id}",
        round_number=1,
    )

    role_runners = _build_debate_role_runners(
        context=context,
        debate_role_runners_factory=debate_role_runners_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        tenant_id=tenant_id,
    )
    orchestrator = DebateOrchestrator(config=DebateConfig(), role_runners=role_runners)
    final_state = orchestrator.run(state)

    gate_failure = orchestrator.get_gate_failure()
    muhasabah_passed = gate_failure is None

    return {
        "debate_id": run_id,
        "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
        "round_number": final_state.round_number,
        "muhasabah_passed": muhasabah_passed,
        "agent_output_count": len(final_state.agent_outputs),
        "debate_provenance": _build_debate_provenance(
            selection=_resolve_debate_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            role_runners=role_runners,
        ),
        "debate_observability": _build_debate_observability(final_state),
    }


def _run_full_analysis(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    calc_ids: list[str],
    enrichment_refs: dict[str, Any],
    rag_retrieval: dict[str, Any] | None = None,
    deal_metadata: dict[str, Any] | None = None,
    db_conn: Any = None,
    analysis_client_factory: AnalysisClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
    audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    """Run analysis agents for a FULL pipeline run.

    Builds LLM client (using IDIS_DEBATE_BACKEND), registers all 8 specialist agents,
    constructs AnalysisContext from pipeline data, and runs AnalysisEngine.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        created_claim_ids: Claim IDs from extraction/grading.
        calc_ids: Calc IDs from calculation step.
        enrichment_refs: Enrichment references from enrichment step.
        rag_retrieval: Safe probe-retrieval summary from the RAG_EVIDENCE step
            (Slice91); IDs/scores only, whitelist-converted onto the context.

    Returns:
        Dict with agent_count, report_ids, bundle_id, and internal state
        (_analysis_bundle, _analysis_context) for downstream steps.
    """
    from idis.analysis.agents import build_default_specialist_agents
    from idis.analysis.models import AnalysisContext, AnalysisRagEvidence, EnrichmentRef
    from idis.analysis.registry import AnalysisAgentRegistry
    from idis.analysis.runner import AnalysisEngine

    llm_client = _build_analysis_llm_client(
        analysis_client_factory=analysis_client_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        tenant_id=tenant_id,
    )
    agents = build_default_specialist_agents(llm_client=llm_client)

    registry = AnalysisAgentRegistry()
    for agent in agents:
        registry.register(agent)

    enrichment_ref_models: dict[str, EnrichmentRef] = {}
    for ref_id, ref_data in enrichment_refs.items():
        if isinstance(ref_data, dict):
            enrichment_ref_models[ref_id] = EnrichmentRef(
                ref_id=ref_data.get("ref_id", ref_id),
                provider_id=ref_data.get("provider_id", "unknown"),
                source_id=ref_data.get("source_id", "unknown"),
            )

    analysis_ctx = AnalysisContext(
        deal_id=deal_id,
        tenant_id=tenant_id,
        run_id=run_id,
        claim_ids=frozenset(created_claim_ids),
        calc_ids=frozenset(calc_ids),
        enrichment_refs=enrichment_ref_models,
        claim_registry=_build_analysis_claim_registry(
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        ),
        calc_registry=_build_analysis_calc_registry(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_ids=calc_ids,
            db_conn=db_conn,
        ),
        rag_evidence=AnalysisRagEvidence.from_retrieval_summary(rag_retrieval),
        company_name=str((deal_metadata or {}).get("company_name") or ""),
        stage=str((deal_metadata or {}).get("stage") or ""),
        sector=str((deal_metadata or {}).get("sector") or ""),
    )

    effective_audit_sink = _resolve_run_audit_sink(audit_sink)
    engine = AnalysisEngine(registry=registry, audit_sink=effective_audit_sink)
    agent_ids = [a.agent_id for a in registry.list_agents()]
    bundle = engine.run(analysis_ctx, agent_ids)

    return {
        "agent_count": len(bundle.reports),
        "report_ids": [r.agent_id for r in bundle.reports],
        "bundle_id": f"bundle-{run_id[:8]}",
        "analysis_provenance": _build_analysis_provenance(
            selection=_resolve_analysis_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            client=llm_client,
        ),
        "_analysis_bundle": bundle,
        "_analysis_context": analysis_ctx,
    }


def _build_analysis_claim_registry(
    *,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    db_conn: Any = None,
) -> dict[str, Any]:
    """Load readable claim summaries for Layer 2 analysis context."""
    from idis.analysis.models import AnalysisClaimReference
    from idis.persistence.repositories.claims import ClaimsRepository, InMemoryClaimsRepository

    claims_repo = (
        ClaimsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryClaimsRepository(tenant_id)
    )
    registry: dict[str, AnalysisClaimReference] = {}
    for claim_id in sorted(set(created_claim_ids)):
        claim = claims_repo.get(claim_id)
        if not claim or str(claim.get("deal_id")) != deal_id:
            continue
        source_span_id = claim.get("primary_span_id")
        claim_class = str(claim.get("claim_class") or "")
        claim_text = str(claim.get("claim_text") or "")
        registry[claim_id] = AnalysisClaimReference(
            claim_id=claim_id,
            claim_text=claim_text,
            claim_class=claim_class,
            source_summary=_claim_source_summary(claim_class, source_span_id),
            sanad_grade=claim.get("claim_grade"),
            materiality=claim.get("materiality"),
            claim_verdict=claim.get("claim_verdict"),
            source_span_id=str(source_span_id) if source_span_id else None,
        )
    return registry


def _build_analysis_calc_registry(
    *,
    tenant_id: str,
    deal_id: str,
    calc_ids: list[str],
    db_conn: Any = None,
) -> dict[str, Any]:
    """Load readable calculation summaries for Layer 2 analysis context."""
    from idis.analysis.models import AnalysisCalcReference
    from idis.calc.formulas.core import resolve_formula_version
    from idis.persistence.repositories.calculations import get_calculations_repository

    requested_ids = set(calc_ids)
    if not requested_ids:
        return {}
    repo = get_calculations_repository(db_conn, tenant_id)
    registry: dict[str, AnalysisCalcReference] = {}
    calc_sanads_by_calc_id = {
        str(calc_sanad.get("calc_id") or ""): calc_sanad
        for calc_sanad in repo.list_calc_sanads_by_deal(deal_id)
    }
    for calc in repo.list_by_deal(deal_id):
        calc_id = str(calc.get("calc_id") or "")
        if calc_id not in requested_ids:
            continue
        output = calc.get("output")
        input_claim_ids = _calc_input_claim_ids(calc.get("inputs"))
        calc_sanad = calc_sanads_by_calc_id.get(calc_id, {})
        registry[calc_id] = AnalysisCalcReference(
            calc_id=calc_id,
            calc_type=str(calc.get("calc_type") or ""),
            output_summary=_calc_output_summary(output),
            input_claim_ids=input_claim_ids,
            source_summary=(
                f"{calc.get('calc_type') or 'calculation'} from {len(input_claim_ids)} input claims"
            ),
            reproducibility_hash=calc.get("reproducibility_hash"),
            calc_sanad_id=calc_sanad.get("calc_sanad_id"),
            formula_hash=calc.get("formula_hash"),
            code_version=calc.get("code_version"),
            formula_version=resolve_formula_version(
                calc.get("calc_type"), calc.get("formula_hash")
            ),
            output=_calc_output_payload(output),
            assumptions=_calc_assumptions(calc.get("inputs")),
            calc_grade=calc_sanad.get("calc_grade"),
            input_min_sanad_grade=calc_sanad.get("input_min_sanad_grade"),
        )
    return registry


def _run_full_scoring(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scoring_client_factory: ScoringClientFactory | None = None,
    strict_live_debate_backend_required: bool = False,
    audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    """Run scoring engine for a FULL pipeline run.

    Builds LLM client (using IDIS_DEBATE_BACKEND), constructs ScoringEngine,
    and scores the analysis bundle with the stage resolved from the analysis
    context. An unmapped stage blocks the step before any scoring call.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        analysis_bundle: AnalysisBundle from analysis step.
        analysis_context: AnalysisContext from analysis step.

    Returns:
        Dict with composite_score, band, routing, stage, and internal state
        (_scorecard) for downstream steps.

    Raises:
        RunStepBlockedError: When the analysis context stage does not map.
    """
    from idis.analysis.scoring.models import resolve_stage
    from idis.services.runs.orchestrator import RunStepBlockedError

    raw_stage = _analysis_context_stage_text(analysis_context)
    stage = resolve_stage(raw_stage)
    if stage is None:
        visible = raw_stage if raw_stage is not None else ""
        raise RunStepBlockedError(
            "STAGE_UNKNOWN",
            f"scoring stage did not map: {visible[:_STAGE_UNKNOWN_MESSAGE_LIMIT]}",
            result_summary={"outcome": "STAGE_UNKNOWN", "stage_text": visible},
        )

    from idis.analysis.scoring.engine import ScoringEngine
    from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner

    llm_client = _build_scoring_llm_client(
        scoring_client_factory=scoring_client_factory,
        strict_live_debate_backend_required=strict_live_debate_backend_required,
        tenant_id=tenant_id,
    )
    runner = LLMScorecardRunner(llm_client=llm_client)
    effective_audit_sink = _resolve_run_audit_sink(audit_sink)
    engine = ScoringEngine(runner=runner, audit_sink=effective_audit_sink)

    scorecard = engine.score(analysis_context, analysis_bundle, stage)

    return {
        "composite_score": scorecard.composite_score,
        "band": scorecard.score_band.value,
        "routing": scorecard.routing.value,
        "stage": stage.value,
        "scoring_provenance": _build_scoring_provenance(
            selection=_resolve_scoring_selection(),
            strict_live_debate_backend_required=strict_live_debate_backend_required,
            client=llm_client,
        ),
        "_scorecard": scorecard,
    }
