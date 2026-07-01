"""Slice90 Task 5 — index graph summaries (Slice89 graph_conclusions) into pgvector.

Adds `SOURCE_TYPE_GRAPH_SUMMARY` + `index_graph_summaries_for_deal`: constructs SAFE embedding text
from `graph_conclusions` (per claim / per defect), keyed by the claim/defect UUID. Records without a
safe UUID source id are skipped and counted. No raw graph rows, evidence text, or private payloads.

Wiring (proven first): `_execute_rag_evidence` does not pass graph_conclusions to the RAG fn today —
graph_conclusions are available at `accumulated["graph_retrieval"]["graph_conclusions"]` (GRAPH runs
before RAG). Task 5 threads them and indexes after calc outputs. Tests inject a fake embed batch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.api.routes.runs import _run_full_rag_evidence
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.indexing import (
    SOURCE_TYPE_GRAPH_SUMMARY,
    index_document_spans_for_deal,
    index_graph_summaries_for_deal,
)
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents
from tests.test_slice90_calc_output_indexing import (
    _FAKE_VECTOR,
    _LIVE_OPENAI_ENV,
    _fake_embed_batch,
    _RecordingVectorRepo,
)

_CLAIM_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEFECT_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"

_GRAPH_CONCLUSIONS: dict[str, Any] = {
    "claims": [
        {
            "claim_id": _CLAIM_UUID,
            "chain_depth": 4,
            "weakest_grade": "B",
            "corroboration_status": "MUTAWATIR",
            "independent_source_count": 3,
        },
        {  # non-UUID claim id → skipped
            "claim_id": "claim-not-a-uuid",
            "chain_depth": 1,
            "weakest_grade": "C",
            "corroboration_status": "AHAD_1",
            "independent_source_count": 1,
        },
    ],
    "defect_impacts": [
        {
            "defect_id": _DEFECT_UUID,
            "defect_type": "CONTRADICTION",
            "severity": "MAJOR",
            "affected_claim_ids": [_CLAIM_UUID],
            "affected_calc_ids": ["calc-1"],
        }
    ],
    "co_occurring_entity_count": 2,  # deal-level, no per-claim/defect source id → not indexed
}


@pytest.fixture(autouse=True)
def _forbid_real_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_kwargs: Any) -> Any:
        raise AssertionError("real OpenAI embed batch must not be constructed in tests")

    monkeypatch.setattr("idis.services.rag.indexing.create_openai_embed_batch", _raise)


# --- Unit: index_graph_summaries_for_deal persists per-claim/per-defect graph summaries ---


def test_index_graph_summaries_persists_claim_and_defect() -> None:
    assert SOURCE_TYPE_GRAPH_SUMMARY == "graph_summary"
    repo = _RecordingVectorRepo()
    summary, probes = index_graph_summaries_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        graph_conclusions=_GRAPH_CONCLUSIONS,
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "indexed"
    assert summary["indexed_graph_summary_count"] == 2  # the UUID claim + the UUID defect
    assert summary["skipped_graph_summary_count"] == 1  # the non-UUID claim
    assert probes  # probe vectors collected

    assert all(upsert["source_type"] == "graph_summary" for upsert in repo.upserts)
    assert sorted(u["source_id"] for u in repo.upserts) == sorted([_CLAIM_UUID, _DEFECT_UUID])
    assert all(u["content_hash"] for u in repo.upserts)  # deterministic content hash of the text
    assert all(u["embedding"] == list(_FAKE_VECTOR) for u in repo.upserts)
    assert all(u["run_id"] == RUN_ID for u in repo.upserts)


def test_index_graph_summaries_skips_records_without_uuid_source_id() -> None:
    repo = _RecordingVectorRepo()
    summary, _ = index_graph_summaries_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        graph_conclusions={"claims": [{"claim_id": "not-a-uuid"}], "defect_impacts": []},
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "skipped"
    assert summary["indexed_graph_summary_count"] == 0
    assert summary["skipped_graph_summary_count"] == 1
    assert repo.upserts == []


def test_graph_summary_embed_text_contains_no_source_ids_or_raw() -> None:
    # The repository never stores the embed text (only vector + source_id + content_hash). The
    # constructed text uses safe fields only; assert it carries no claim/defect id or raw payload.
    from idis.services.rag.indexing import _graph_claim_text, _graph_defect_text

    claim_text = _graph_claim_text(_GRAPH_CONCLUSIONS["claims"][0])
    defect_text = _graph_defect_text(_GRAPH_CONCLUSIONS["defect_impacts"][0])
    assert _CLAIM_UUID not in claim_text and _DEFECT_UUID not in defect_text
    assert "B" in claim_text and "MUTAWATIR" in claim_text  # safe grade/status tokens
    assert "MAJOR" in defect_text and "CONTRADICTION" in defect_text  # safe severity/type tokens


# --- Wiring: FULL RAG step indexes graph summaries after calc outputs ---


def _fake_span_indexing_service(**kwargs: Any) -> Any:
    return index_document_spans_for_deal(embed_batch=_fake_embed_batch, **kwargs)


def _fake_graph_indexing_service(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    graph_conclusions: dict[str, Any] | None,
    repository: Any,
    embedding_model: str,
    embedding_dimensions: int,
) -> Any:
    return index_graph_summaries_for_deal(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        graph_conclusions=graph_conclusions,
        repository=repository,
        embed_batch=_fake_embed_batch,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def test_full_strict_rag_indexes_graph_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _LIVE_OPENAI_ENV.items():
        monkeypatch.setenv(key, value)
    repo = _RecordingVectorRepo()
    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=_documents(),
        db_conn=MagicMock(),
        strict_full_live=True,
        graph_conclusions=_GRAPH_CONCLUSIONS,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small", dimensions=VECTOR_EMBEDDING_DIMENSIONS
        ),
        vector_repository_factory=lambda _conn, _tenant: repo,
        indexing_service=_fake_span_indexing_service,
        graph_indexing_service=_fake_graph_indexing_service,
    )
    assert summary["rag_status"] == "available"
    graph_upserts = [u for u in repo.upserts if u["source_type"] == "graph_summary"]
    assert sorted(u["source_id"] for u in graph_upserts) == sorted([_CLAIM_UUID, _DEFECT_UUID])
    assert summary["rag_graph_indexing"]["indexed_graph_summary_count"] == 2


# --- Orchestrator: graph_conclusions are threaded from accumulated state into the RAG fn ---


def test_orchestrator_threads_graph_conclusions_to_rag() -> None:
    from idis.audit.sink import InMemoryAuditSink
    from idis.persistence.repositories.run_steps import (
        InMemoryRunStepsRepository,
        clear_run_steps_store,
    )
    from idis.services.runs.orchestrator import RunContext, RunOrchestrator

    clear_run_steps_store()
    calls: list[dict[str, Any]] = []

    def rag_fn(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 1},
            "rag_retrieval": {
                "status": "probed",
                "retrieval_mode": "probe",
                "probe_count": 1,
                "match_count": 1,
                "matches": [
                    {"source_type": "graph_summary", "source_id": _CLAIM_UUID, "score": 1.0}
                ],
            },
        }

    conclusions = {
        "claims": [{"claim_id": _CLAIM_UUID, "chain_depth": 4, "weakest_grade": "B"}],
        "defect_impacts": [],
        "co_occurring_entity_count": 0,
    }
    ctx = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=_documents(),
        deal_metadata={"tenant_id": TENANT_ID, "company_name": "Acme Corp"},
        extract_fn=lambda **_kwargs: {"created_claim_ids": ["claim-001"]},
        grade_fn=lambda **_kwargs: {},
        calc_fn=lambda **_kwargs: {"calc_ids": ["calc-001"]},
        graph_fn=lambda **_kwargs: {
            "graph_status": "available",
            "graph_projection": {"status": "projected"},
            "graph_retrieval": {"status": "retrieved", "graph_conclusions": conclusions},
        },
        rag_fn=rag_fn,
        enrich_fn=lambda **_kwargs: {},
        debate_fn=lambda **_kwargs: {"debate_id": RUN_ID, "muhasabah_passed": True},
        layer2_ic_challenge_fn=lambda **_kwargs: {
            "status": "completed",
            "layer2_challenge_ids": ["layer2-001"],
            "source_debate_ids": [RUN_ID],
            "claim_ids": ["claim-001"],
            "calc_ids": ["calc-001"],
            "finding_count": 1,
            "unresolved_question_count": 1,
            "muhasabah_passed": True,
        },
        analysis_fn=lambda **_kwargs: {"_analysis_bundle": {}, "_analysis_context": {}},
        scoring_fn=lambda **_kwargs: {"_scorecard": {}},
        deliverables_fn=lambda **_kwargs: {"deliverable_count": 1},
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(), run_steps_repo=InMemoryRunStepsRepository(TENANT_ID)
    )
    orchestrator.execute(ctx)

    assert calls
    # The RAG fn received the graph_conclusions accumulated by the prior GRAPH step.
    assert calls[0]["graph_conclusions"] == conclusions
