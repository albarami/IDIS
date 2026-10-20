"""S3 deliverables step moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from idis.audit.sink import AuditSink
from idis.services.runs.step_runtime.common import _resolve_run_audit_sink


def _run_full_deliverables(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    analysis_bundle: Any,
    analysis_context: Any,
    scorecard: Any,
    graph_evidence: dict[str, Any] | None = None,
    rag_evidence: dict[str, Any] | None = None,
    layer2_evidence: dict[str, Any] | None = None,
    enrichment_evidence: dict[str, Any] | None = None,
    vep_evidence: dict[str, Any] | None = None,
    run_provenance: dict[str, Any] | None = None,
    db_conn: Any = None,
    object_store: Any = None,
    audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    """Run deliverables generation for a FULL pipeline run.

    Uses DeliverablesGenerator to produce the full bundle of deliverables
    from analysis reports and scorecard.

    Args:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal UUID.
        analysis_bundle: AnalysisBundle from analysis step.
        analysis_context: AnalysisContext from analysis step.
        scorecard: Scorecard from scoring step.

    Returns:
        Dict with deliverable_count, types, deliverable_ids.
    """
    from idis.deliverables.generator import DeliverablesGenerator

    effective_audit_sink = _resolve_run_audit_sink(audit_sink)
    generator = DeliverablesGenerator(audit_sink=effective_audit_sink)

    graph_conclusions: dict[str, Any] | None = None
    if isinstance(graph_evidence, dict):
        retrieval = graph_evidence.get("graph_retrieval")
        if isinstance(retrieval, dict):
            conclusions = retrieval.get("graph_conclusions")
            if isinstance(conclusions, dict):
                graph_conclusions = conclusions

    from idis.services.webhooks.lifecycle import (
        DELIVERABLE_FAILED,
        DELIVERABLE_PRODUCED,
        notify_webhook_lifecycle,
    )

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        bundle = generator.generate(
            ctx=analysis_context,
            bundle=analysis_bundle,
            scorecard=scorecard,
            deal_name=deal_id,
            generated_at=generated_at,
            deliverable_id_prefix=f"del-{run_id[:8]}",
            graph_conclusions=graph_conclusions,
            layer2_evidence=layer2_evidence,
        )
    except Exception:
        # Best-effort webhook (never raises); the generation failure itself still propagates.
        notify_webhook_lifecycle(
            tenant_id=tenant_id,
            event_type=DELIVERABLE_FAILED,
            resource_type="run",
            resource_id=run_id,
            conn=db_conn,
        )
        raise

    types: list[str] = [
        bundle.screening_snapshot.deliverable_type,
        bundle.ic_memo.deliverable_type,
        bundle.truth_dashboard.deliverable_type,
        bundle.qa_brief.deliverable_type,
    ]
    deliverable_ids: list[str] = [
        bundle.screening_snapshot.deliverable_id,
        bundle.ic_memo.deliverable_id,
        bundle.truth_dashboard.deliverable_id,
        bundle.qa_brief.deliverable_id,
    ]

    if bundle.decline_letter is not None:
        types.append(bundle.decline_letter.deliverable_type)
        deliverable_ids.append(bundle.decline_letter.deliverable_id)

    notify_webhook_lifecycle(
        tenant_id=tenant_id,
        event_type=DELIVERABLE_PRODUCED,
        resource_type="run",
        resource_id=run_id,
        data={"deliverable_count": len(deliverable_ids)},
        conn=db_conn,
    )

    if db_conn is not None and object_store is not None:
        from idis.deliverables.product_bundle import ProductBundleExporter
        from idis.persistence.repositories.deliverables import PostgresDeliverablesRepository

        exporter = ProductBundleExporter(
            deliverables_repo=PostgresDeliverablesRepository(db_conn, tenant_id),
            object_store=object_store,
            object_store_backend=object_store.backend_name,
        )
        export_summary = exporter.export_bundle(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            bundle=bundle,
            analysis_context=analysis_context,
            scorecard=scorecard,
            export_timestamp=generated_at,
            graph_evidence=graph_evidence,
            rag_evidence=rag_evidence,
            layer2_evidence=layer2_evidence,
            enrichment_evidence=enrichment_evidence,
            vep_evidence=vep_evidence,
            run_provenance=run_provenance,
        )
        export_summary["durable_export"] = True
        return export_summary

    return {
        "deliverable_count": len(types),
        "types": sorted(types),
        "deliverable_ids": sorted(deliverable_ids),
        "durable_export": False,
    }
