"""S4 graph and RAG evidence steps moved verbatim from ``idis.api.routes.runs``."""

from __future__ import annotations

import os
from typing import Any, cast

from idis.audit.sink import AuditSink
from idis.services.runs.step_runtime.common import (
    _calc_input_claim_ids,
    _resolve_run_audit_sink,
)
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
)


def _calc_evidence_block(calc_ids: list[str] | None) -> dict[str, Any]:
    """Additive, sanitized reflection of the deterministic calc ids available to a step."""
    ids = sorted({str(calc_id) for calc_id in (calc_ids or []) if str(calc_id).strip()})
    return {
        "status": "calc_evidence_available" if ids else "no_calc_evidence",
        "calc_count": len(ids),
        "calc_ids": ids,
    }


def _graph_calculations_by_claim(
    *,
    tenant_id: str,
    deal_id: str,
    calc_ids: list[str],
    db_conn: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Load PERSISTED deterministic calcs for the given ids, grouped by input claim id.

    Only real persisted calculations are returned (never invented), so the graph projection
    attaches calc edges only when the persisted calc relationships actually exist.
    """
    wanted = {str(calc_id) for calc_id in calc_ids}
    if not wanted:
        return {}
    from idis.persistence.repositories.calculations import get_calculations_repository

    repo = get_calculations_repository(db_conn, tenant_id)
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for calc in repo.list_by_deal(deal_id):
        if str(calc.get("calc_id") or "") not in wanted:
            continue
        for claim_id in _calc_input_claim_ids(calc.get("inputs")):
            by_claim.setdefault(claim_id, []).append(calc)
    return by_claim


# The Sanad service persists transmission-node input_refs ENTITY-KEYED (e.g. {"evidence_id": ...}),
# while the graph repo routes INPUT edges on ref["type"]. Map each known entity key to its node
# type so the persisted lineage actually materializes as INPUT edges.
_INPUT_REF_KEY_TO_TYPE: tuple[tuple[str, str], ...] = (
    ("span_id", "span"),
    ("source_span_id", "span"),
    ("evidence_id", "evidence"),
    ("claim_id", "claim"),
    ("calc_id", "calculation"),
    ("calculation_id", "calculation"),
)


def _normalize_input_ref(ref: dict[str, Any]) -> dict[str, str] | None:
    """Normalize a persisted Sanad input_ref into the graph-repo ``{type, id}`` shape.

    Maps the entity-keyed refs the Sanad service persists (e.g. ``{"evidence_id": ...}``) to the
    ``{type, id}`` shape the graph repo routes INPUT edges on, preserves refs already in
    ``{type, id}`` form, and drops malformed refs (returns ``None``).
    """
    for key, ref_type in _INPUT_REF_KEY_TO_TYPE:
        ref_id = ref.get(key)
        if ref_id:
            return {"type": ref_type, "id": str(ref_id)}
    explicit_type = ref.get("type")
    explicit_id = ref.get("id")
    if explicit_type and explicit_id:
        return {"type": str(explicit_type), "id": str(explicit_id)}
    return None


def _graph_transmission_nodes_by_claim(
    *,
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Load each claim's PERSISTED Sanad transmission chain, shaped for project_claim_sanad.

    The chain is durable on the sanad (`transmission_chain`). Only real persisted transmission
    nodes are returned (never invented); the projection writes HAS_SANAD_STEP/INPUT/OUTPUT edges
    only when the persisted chain actually exists. input_refs are normalized from the persisted
    entity-keyed shape into the graph-repo `{type, id}` shape (see `_normalize_input_ref`). Uses
    the existing schema — no dedicated Sanad node.
    """
    from idis.persistence.repositories.claims import (
        InMemorySanadsRepository,
        SanadsRepository,
    )

    repo: Any = (
        SanadsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemorySanadsRepository(tenant_id)
    )
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for claim_id in sorted(set(created_claim_ids)):
        sanad = repo.get_by_claim(claim_id)
        chain = (sanad or {}).get("transmission_chain") or []
        nodes: list[dict[str, Any]] = []
        for node in chain:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("node_id") or "")
            if not node_id:
                continue
            input_refs: list[dict[str, str]] = []
            for ref in node.get("input_refs") or []:
                if not isinstance(ref, dict):
                    continue
                normalized = _normalize_input_ref(ref)
                if normalized is not None:
                    input_refs.append(normalized)
            nodes.append(
                {
                    "node_id": node_id,
                    "timestamp": str(node.get("timestamp") or ""),
                    "input_refs": input_refs,
                }
            )
        by_claim[claim_id] = nodes
    return by_claim


def _graph_defects_by_claim(
    *,
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Load each claim's PERSISTED defects, shaped for project_claim_sanad.

    Only real persisted defects are returned (never invented); the projection writes Defect /
    HAS_DEFECT edges only when the persisted defects actually exist. Uses the existing schema
    (Defect node already exists) — no schema change.
    """
    from idis.persistence.repositories.claims import (
        DefectsRepository,
        InMemoryDefectsRepository,
    )

    repo: Any = (
        DefectsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryDefectsRepository(tenant_id)
    )
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for claim_id in sorted(set(created_claim_ids)):
        defects, _ = repo.list_by_claim(claim_id, limit=200)
        items: list[dict[str, Any]] = []
        for defect in defects:
            defect_id = str(defect.get("defect_id") or "")
            if not defect_id:
                continue
            items.append(
                {
                    "defect_id": defect_id,
                    "defect_type": str(defect.get("defect_type") or ""),
                    "severity": str(defect.get("severity") or "MINOR"),
                }
            )
        by_claim[claim_id] = items
    return by_claim


def _run_full_graph_evidence(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    created_claim_ids: list[str],
    calc_ids: list[str],
    db_conn: Any = None,
    strict_full_live: bool | None = None,
    neo4j_health_checker: Any = None,
    projection_service: Any = None,
    retrieval_service: Any = None,
    audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    """Project and retrieve Neo4j graph evidence for product visibility."""
    from idis.persistence.neo4j_driver import Neo4jHealthStatus, check_neo4j_health
    from idis.services.runs.orchestrator import RunStepBlockedError

    health = (
        neo4j_health_checker(os.environ)
        if neo4j_health_checker is not None
        else check_neo4j_health()
    )
    strict = (
        strict_full_live
        if strict_full_live is not None
        else is_strict_full_live_required(
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
        )
    )
    if health.status != Neo4jHealthStatus.HEALTHY:
        summary = {
            "graph_status": "skipped",
            "graph_projection": {"status": "skipped"},
            "graph_retrieval": {"status": "skipped"},
            "neo4j_health_status": health.status.value,
            "missing_env_vars": health.missing_env_vars,
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_HEALTH_BLOCKED",
                "Neo4j graph evidence is not health-check ready",
                result_summary=summary,
            )
        return summary

    projection_summary = _project_graph_evidence(
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=documents,
        created_claim_ids=created_claim_ids,
        db_conn=db_conn,
        projection_service=projection_service,
        calc_ids=calc_ids,
        audit_sink=audit_sink,
    )
    projection_status = projection_summary["status"]
    if projection_status != "projected":
        graph_status = "skipped" if projection_status == "skipped" else "blocked"
        summary = {
            "graph_status": graph_status,
            "graph_projection": projection_summary,
            "graph_retrieval": {"status": "not_attempted"},
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_PROJECTION_BLOCKED",
                "Neo4j graph projection failed",
                result_summary=summary,
            )
        return summary

    retrieval_summary = _retrieve_graph_evidence(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_ids=created_claim_ids,
        defect_ids=projection_summary.get("projected_defect_ids"),
        retrieval_service=retrieval_service,
    )
    retrieval_status = retrieval_summary["status"]
    if retrieval_status != "retrieved":
        graph_status = "skipped" if retrieval_status == "skipped" else "blocked"
        summary = {
            "graph_status": graph_status,
            "graph_projection": projection_summary,
            "graph_retrieval": retrieval_summary,
        }
        if strict:
            raise RunStepBlockedError(
                "GRAPH_RETRIEVAL_BLOCKED",
                "Neo4j graph retrieval failed",
                result_summary=summary,
            )
        return summary

    return {
        "graph_status": "available",
        "graph_projection": projection_summary,
        "graph_retrieval": retrieval_summary,
    }


def _index_full_calc_outputs(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    calc_ids: list[str] | None,
    repository: Any,
    env: Any,
    db_conn: Any,
    embedding_model: str,
    embedding_dimensions: int,
    calc_indexing_service: Any,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Index the run's durable calc outputs into pgvector (additive; same provider as spans)."""
    from idis.services.rag.indexing import create_openai_embed_batch, index_calc_outputs_for_deal

    safe_calc_ids = sorted({str(calc_id) for calc_id in (calc_ids or []) if str(calc_id).strip()})
    if not safe_calc_ids:
        return ({"status": "skipped", "indexed_calc_count": 0, "skipped_calc_count": 0}, [])
    if calc_indexing_service is not None:
        injected: tuple[dict[str, Any], list[list[float]]] = calc_indexing_service(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            calc_ids=safe_calc_ids,
            repository=repository,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        return injected
    from idis.persistence.repositories.calculations import get_calculations_repository

    calc_repo = get_calculations_repository(db_conn, tenant_id)
    wanted = set(safe_calc_ids)
    calculations = [
        calc for calc in calc_repo.list_by_deal(deal_id) if str(calc.get("calc_id")) in wanted
    ]
    return index_calc_outputs_for_deal(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        calculations=calculations,
        repository=repository,
        embed_batch=create_openai_embed_batch(env=env),
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def _index_full_graph_summaries(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    graph_conclusions: dict[str, Any] | None,
    repository: Any,
    env: Any,
    embedding_model: str,
    embedding_dimensions: int,
    graph_indexing_service: Any,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Index the run's graph_conclusions into pgvector (additive; same provider/repo as spans)."""
    from idis.services.rag.indexing import create_openai_embed_batch, index_graph_summaries_for_deal

    conclusions = graph_conclusions if isinstance(graph_conclusions, dict) else None
    if not conclusions:
        return (
            {
                "status": "skipped",
                "indexed_graph_summary_count": 0,
                "skipped_graph_summary_count": 0,
            },
            [],
        )
    if graph_indexing_service is not None:
        injected: tuple[dict[str, Any], list[list[float]]] = graph_indexing_service(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            graph_conclusions=conclusions,
            repository=repository,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        return injected
    return index_graph_summaries_for_deal(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        graph_conclusions=conclusions,
        repository=repository,
        embed_batch=create_openai_embed_batch(env=env),
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def _run_full_rag_evidence(
    *,
    calc_ids: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """RAG FULL-step seam; surfaces calc evidence and indexes durable calc outputs into pgvector.

    Threads accumulated calc ids through the seam: attaches a sanitized rag_calc_evidence block to
    the step summary (and any blocked summary), and indexes the run's durable calc outputs into
    pgvector after span indexing (Slice90).
    """
    from idis.services.runs.orchestrator import RunStepBlockedError

    calc_evidence = _calc_evidence_block(calc_ids)
    try:
        summary = _run_full_rag_evidence_inner(calc_ids=calc_ids, **kwargs)
    except RunStepBlockedError as exc:
        result_summary = getattr(exc, "result_summary", None)
        if isinstance(result_summary, dict):
            result_summary["rag_calc_evidence"] = calc_evidence
        raise
    summary["rag_calc_evidence"] = calc_evidence
    return summary


def _run_full_rag_evidence_inner(
    *,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    db_conn: Any = None,
    strict_full_live: bool | None = None,
    calc_ids: list[str] | None = None,
    graph_conclusions: dict[str, Any] | None = None,
    pgvector_health_checker: Any = None,
    embedding_health_checker: Any = None,
    indexing_service: Any = None,
    retrieval_service: Any = None,
    vector_repository_factory: Any = None,
    calc_indexing_service: Any = None,
    graph_indexing_service: Any = None,
) -> dict[str, Any]:
    """Index persisted spans, calc outputs, and graph summaries; then run probe retrieval."""
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
        is_vector_search_enabled,
    )
    from idis.services.rag.indexing import (
        VectorEmbeddingsRepository,
        build_postgres_vector_repository,
        create_openai_embed_batch,
        index_document_spans_for_deal,
    )
    from idis.services.rag.pgvector_health import PgvectorHealthStatus, check_pgvector_health
    from idis.services.rag.retrieval import retrieve_rag_probe_evidence
    from idis.services.runs.orchestrator import RunStepBlockedError

    env = os.environ
    strict = (
        strict_full_live
        if strict_full_live is not None
        else is_strict_full_live_required(
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
        )
    )
    skipped_summary = {
        "rag_status": "skipped",
        "rag_indexing": {"status": "skipped", "indexed_span_count": 0, "skipped_span_count": 0},
        "rag_retrieval": {
            "status": "skipped",
            "retrieval_mode": "probe",
            "probe_count": 0,
            "match_count": 0,
            "matches": [],
        },
    }

    if not is_vector_search_enabled(env):
        if strict:
            raise RunStepBlockedError(
                "RAG_CONFIG_BLOCKED",
                "Vector search is disabled for strict FULL runs",
                result_summary=skipped_summary,
            )
        return skipped_summary

    pgvector_health = (
        pgvector_health_checker(env)
        if pgvector_health_checker is not None
        else check_pgvector_health(env=env)
    )
    embedding_health = (
        embedding_health_checker(env)
        if embedding_health_checker is not None
        else check_embedding_health(env=env)
    )

    if (
        pgvector_health.status != PgvectorHealthStatus.HEALTHY
        or embedding_health.status != EmbeddingHealthStatus.HEALTHY
    ):
        summary = {
            **skipped_summary,
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
            "missing_env_vars": sorted(
                set(pgvector_health.missing_env_vars) | set(embedding_health.missing_env_vars)
            ),
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_HEALTH_BLOCKED",
                "pgvector/RAG evidence is not health-check ready",
                result_summary=summary,
            )
        return summary

    if db_conn is None:
        if strict:
            raise RunStepBlockedError(
                "RAG_DATABASE_BLOCKED",
                "pgvector indexing requires Postgres run persistence",
                result_summary=skipped_summary,
            )
        return skipped_summary

    repository_factory = vector_repository_factory or build_postgres_vector_repository
    repository = cast(
        VectorEmbeddingsRepository,
        repository_factory(db_conn, tenant_id),
    )
    embedding_model = embedding_health.model or "text-embedding-3-small"
    embedding_dimensions = embedding_health.dimensions or 1536

    if indexing_service is not None:
        indexing_summary, probe_embeddings = indexing_service(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            documents=documents,
            repository=repository,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
    else:
        indexing_summary, probe_embeddings = index_document_spans_for_deal(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            documents=documents,
            repository=repository,
            embed_batch=create_openai_embed_batch(env=env),
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )

    indexing_status = indexing_summary["status"]
    if indexing_status != "indexed":
        summary = {
            "rag_status": "skipped" if indexing_status == "skipped" else "blocked",
            "rag_indexing": indexing_summary,
            "rag_retrieval": {
                "status": "not_attempted",
                "retrieval_mode": "probe",
                "probe_count": 0,
                "match_count": 0,
                "matches": [],
            },
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_INDEXING_BLOCKED",
                "pgvector span indexing did not complete",
                result_summary=summary,
            )
        return summary

    # Additive: index the run's durable calc outputs into pgvector after spans (Slice90 Task 4).
    calc_indexing_summary, calc_probe_embeddings = _index_full_calc_outputs(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        calc_ids=calc_ids,
        repository=repository,
        env=env,
        db_conn=db_conn,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        calc_indexing_service=calc_indexing_service,
    )
    probe_embeddings = [*probe_embeddings, *calc_probe_embeddings]

    # Additive: index the run's graph_conclusions into pgvector after calc outputs (Slice90 Task 5).
    graph_indexing_summary, graph_probe_embeddings = _index_full_graph_summaries(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        graph_conclusions=graph_conclusions,
        repository=repository,
        env=env,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        graph_indexing_service=graph_indexing_service,
    )
    probe_embeddings = [*probe_embeddings, *graph_probe_embeddings]

    if retrieval_service is not None:
        retrieval_summary = retrieval_service(
            deal_id=deal_id,
            probe_embeddings=probe_embeddings,
            repository=repository,
        )
    else:
        retrieval_summary = retrieve_rag_probe_evidence(
            deal_id=deal_id,
            probe_embeddings=probe_embeddings,
            repository=repository,
        )

    retrieval_status = retrieval_summary["status"]
    if retrieval_status != "probed":
        summary = {
            "rag_status": "blocked",
            "rag_indexing": indexing_summary,
            "rag_calc_indexing": calc_indexing_summary,
            "rag_graph_indexing": graph_indexing_summary,
            "rag_retrieval": retrieval_summary,
            "pgvector_health_status": pgvector_health.status.value,
            "embedding_health_status": embedding_health.status.value,
        }
        if strict:
            raise RunStepBlockedError(
                "RAG_PROBE_RETRIEVAL_BLOCKED",
                "pgvector probe retrieval did not complete",
                result_summary=summary,
            )
        return summary

    return {
        "rag_status": "available",
        "rag_indexing": indexing_summary,
        "rag_calc_indexing": calc_indexing_summary,
        "rag_graph_indexing": graph_indexing_summary,
        "rag_retrieval": retrieval_summary,
        "pgvector_health_status": pgvector_health.status.value,
        "embedding_health_status": embedding_health.status.value,
    }


def _project_graph_evidence(
    *,
    tenant_id: str,
    deal_id: str,
    documents: list[dict[str, Any]],
    created_claim_ids: list[str],
    db_conn: Any,
    projection_service: Any = None,
    calc_ids: list[str] | None = None,
    audit_sink: AuditSink | None = None,
) -> dict[str, Any]:
    from idis.persistence.graph_consistency import GraphProjectionService, ProjectionStatus

    service = projection_service or GraphProjectionService(
        audit_sink=_resolve_run_audit_sink(audit_sink)
    )
    calcs_by_claim = _graph_calculations_by_claim(
        tenant_id=tenant_id,
        deal_id=deal_id,
        calc_ids=calc_ids or [],
        db_conn=db_conn,
    )
    transmission_by_claim = _graph_transmission_nodes_by_claim(
        tenant_id=tenant_id,
        created_claim_ids=created_claim_ids,
        db_conn=db_conn,
    )
    defects_by_claim = _graph_defects_by_claim(
        tenant_id=tenant_id,
        created_claim_ids=created_claim_ids,
        db_conn=db_conn,
    )
    safe_documents = _graph_documents(documents)
    safe_spans = _graph_spans(documents)
    try:
        deal_result = service.project_deal(
            tenant_id=tenant_id,
            deal_id=deal_id,
            documents=safe_documents,
            spans=safe_spans,
        )
    except Exception:
        return {
            "status": "failed",
            "projected_document_count": 0,
            "projected_span_count": 0,
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    if deal_result.status == ProjectionStatus.SKIPPED:
        return {
            "status": "skipped",
            "projected_document_count": 0,
            "projected_span_count": 0,
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    if deal_result.status != ProjectionStatus.SUCCESS:
        return {"status": "failed", "projected_document_count": 0, "projected_span_count": 0}

    try:
        claims = _graph_claims(
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        )
        evidence_by_claim = _graph_evidence_by_claim(
            tenant_id=tenant_id,
            created_claim_ids=created_claim_ids,
            db_conn=db_conn,
        )
    except Exception:
        return {
            "status": "failed",
            "projected_document_count": len(safe_documents),
            "projected_span_count": len(safe_spans),
            "projected_claim_count": 0,
            "projected_calculation_count": 0,
        }
    projected_claim_count = 0
    projected_calc_ids: set[str] = set()
    projected_sanad_step_ids: set[str] = set()
    projected_defect_ids: set[str] = set()
    for claim in claims:
        claim_calculations = calcs_by_claim.get(claim["claim_id"], [])
        claim_transmission_nodes = transmission_by_claim.get(claim["claim_id"], [])
        claim_defects = defects_by_claim.get(claim["claim_id"], [])
        try:
            result = service.project_claim_sanad(
                tenant_id=tenant_id,
                claim=claim,
                evidence_items=evidence_by_claim.get(claim["claim_id"], []),
                transmission_nodes=claim_transmission_nodes,
                defects=claim_defects,
                calculations=claim_calculations,
            )
        except Exception:
            return {
                "status": "failed",
                "projected_document_count": len(safe_documents),
                "projected_span_count": len(safe_spans),
                "projected_claim_count": projected_claim_count,
                "projected_calculation_count": 0,
            }
        if result.status != ProjectionStatus.SUCCESS:
            return {
                "status": "failed",
                "projected_document_count": len(safe_documents),
                "projected_span_count": len(safe_spans),
                "projected_claim_count": projected_claim_count,
                "projected_calculation_count": 0,
            }
        projected_claim_count += 1
        projected_calc_ids.update(str(calc.get("calc_id") or "") for calc in claim_calculations)
        projected_sanad_step_ids.update(
            str(node.get("node_id") or "") for node in claim_transmission_nodes
        )
        projected_defect_ids.update(str(defect.get("defect_id") or "") for defect in claim_defects)

    return {
        "status": "projected",
        "projected_document_count": len(safe_documents),
        "projected_span_count": len(safe_spans),
        "projected_claim_count": projected_claim_count,
        "projected_calculation_count": len(projected_calc_ids - {""}),
        "projected_sanad_step_count": len(projected_sanad_step_ids - {""}),
        "projected_defect_count": len(projected_defect_ids - {""}),
        "projected_defect_ids": sorted(projected_defect_ids - {""}),
    }


def _retrieve_graph_evidence(
    *,
    tenant_id: str,
    deal_id: str,
    claim_ids: list[str],
    defect_ids: list[str] | None = None,
    retrieval_service: Any = None,
) -> dict[str, Any]:
    from idis.services.graph.retrieval import GraphRetrievalService

    service = retrieval_service or GraphRetrievalService()
    try:
        return service.retrieve_deal_graph_summary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_ids=claim_ids,
            defect_ids=defect_ids,
        )
    except Exception:
        return {"status": "failed", "retrieval_count": 0, "query_summaries": []}


def _graph_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(document["document_id"]),
            "doc_type": str(document.get("doc_type") or ""),
        }
        for document in documents
        if document.get("document_id")
    ]


def _graph_spans(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for document in documents:
        document_id = str(document.get("document_id") or "")
        if not document_id:
            continue
        for span in document.get("spans") or []:
            if not isinstance(span, dict) or not span.get("span_id"):
                continue
            spans.append(
                {
                    "span_id": str(span["span_id"]),
                    "document_id": document_id,
                    "span_type": str(span.get("span_type") or ""),
                }
            )
    return spans


def _graph_claims(
    *,
    tenant_id: str,
    deal_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> list[dict[str, Any]]:
    from idis.persistence.repositories.claims import ClaimsRepository, InMemoryClaimsRepository

    repo = (
        ClaimsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryClaimsRepository(tenant_id)
    )
    claims: list[dict[str, Any]] = []
    for claim_id in sorted(set(created_claim_ids)):
        claim = repo.get(claim_id)
        claims.append(
            {
                "claim_id": claim_id,
                "claim_text": str((claim or {}).get("claim_text") or ""),
                "claim_grade": str((claim or {}).get("claim_grade") or "D"),
                "claim_verdict": str((claim or {}).get("claim_verdict") or "UNVERIFIED"),
                "materiality": str((claim or {}).get("materiality") or "MEDIUM"),
                "claim_class": str((claim or {}).get("claim_class") or "OTHER"),
                "deal_id": deal_id,
            }
        )
    return claims


def _graph_evidence_by_claim(
    *,
    tenant_id: str,
    created_claim_ids: list[str],
    db_conn: Any,
) -> dict[str, list[dict[str, Any]]]:
    from idis.persistence.repositories.evidence import get_evidence_repository

    repo = get_evidence_repository(db_conn, tenant_id)
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for claim_id in sorted(set(created_claim_ids)):
        evidence_items = []
        for item in repo.get_by_claim(claim_id):
            evidence_items.append(
                {
                    "evidence_id": str(item.get("evidence_id") or ""),
                    "source_grade": str(item.get("source_grade") or "D"),
                    "source_system": "idis",
                    "upstream_origin_id": str(item.get("source_span_id") or ""),
                }
            )
        evidence_by_claim[claim_id] = [item for item in evidence_items if item["evidence_id"]]
    return evidence_by_claim
