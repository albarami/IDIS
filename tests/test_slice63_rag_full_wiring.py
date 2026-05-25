"""Slice 63 FULL pgvector indexing/probe-retrieval wiring tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.api.routes.runs import _run_full_rag_evidence
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.models.run_step import FULL_STEPS, IMPLEMENTED_STEPS, STEP_ORDER, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.indexing import index_document_spans_for_deal
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from idis.services.rag.retrieval import retrieve_rag_probe_evidence
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
)
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository

TENANT_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
SPAN_ID = "44444444-4444-4444-4444-444444444444"
SPAN_ID_2 = "55555555-5555-5555-5555-555555555555"


def _vector(seed: float) -> list[float]:
    return [seed + (index * 0.0001) for index in range(VECTOR_EMBEDDING_DIMENSIONS)]


def _documents(*, include_empty: bool = False) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = [
        {
            "span_id": SPAN_ID,
            "document_id": "doc-001",
            "span_type": "PARAGRAPH",
            "text_excerpt": "PRIVATE revenue span text must not leak",
            "content_hash": "hash-span-1",
        }
    ]
    if include_empty:
        spans.append(
            {
                "span_id": SPAN_ID_2,
                "document_id": "doc-001",
                "span_type": "PARAGRAPH",
                "text_excerpt": "   ",
                "content_hash": "hash-span-2",
            }
        )
    else:
        spans.append(
            {
                "span_id": SPAN_ID_2,
                "document_id": "doc-001",
                "span_type": "CELL",
                "text_excerpt": "PRIVATE margin span text must not leak",
                "content_hash": "hash-span-2",
            }
        )
    return [
        {
            "document_id": "doc-001",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "parse_status": "PARSED",
            "spans": spans,
        }
    ]


class RecordingVectorRepository:
    """Repository fake that records upserts and returns deterministic matches."""

    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    def upsert_embedding(self, **kwargs: Any) -> dict[str, Any]:
        self.upserts.append(kwargs)
        return {
            "embedding_id": f"emb-{len(self.upserts)}",
            "tenant_id": kwargs["deal_id"],
            "deal_id": kwargs["deal_id"],
            "source_type": kwargs["source_type"],
            "source_id": kwargs["source_id"],
            "content_hash": kwargs["content_hash"],
        }

    def similarity_search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "source_type": "document_span",
                "source_id": SPAN_ID,
                "score": 0.99,
            }
        ]


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


def test_rag_evidence_step_order_and_registry() -> None:
    """RAG_EVIDENCE is FULL-only, implemented, and ordered after graph before enrichment."""
    graph_idx = FULL_STEPS.index(StepName.GRAPH_EVIDENCE)
    rag_idx = FULL_STEPS.index(StepName.RAG_EVIDENCE)
    enrich_idx = FULL_STEPS.index(StepName.ENRICHMENT)
    assert graph_idx < rag_idx < enrich_idx
    assert STEP_ORDER[StepName.RAG_EVIDENCE] == rag_idx
    assert StepName.RAG_EVIDENCE in IMPLEMENTED_STEPS
    assert StepName.RAG_EVIDENCE in FULL_STEPS
    assert len(FULL_STEPS) == 27


def test_orchestrator_calls_injected_rag_fn() -> None:
    """Orchestrator executes RAG_EVIDENCE via injected callable."""
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
                "matches": [{"source_type": "document_span", "source_id": SPAN_ID, "score": 1.0}],
            },
        }

    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
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
        graph_fn=lambda **_kwargs: {"graph_status": "skipped"},
        rag_fn=rag_fn,
        enrich_fn=lambda **_kwargs: {},
        debate_fn=lambda **_kwargs: {"stop_reason": "complete"},
        analysis_fn=lambda **_kwargs: {"_analysis_bundle": {}, "_analysis_context": {}},
        scoring_fn=lambda **_kwargs: {"_scorecard": {}},
        deliverables_fn=lambda **_kwargs: {"deliverable_count": 1},
    )

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert calls
    assert calls[0]["run_id"] == RUN_ID
    assert calls[0]["tenant_id"] == TENANT_ID
    assert calls[0]["deal_id"] == DEAL_ID
    rag_steps = [step for step in result.steps if step.step_name == StepName.RAG_EVIDENCE]
    assert len(rag_steps) == 1
    assert rag_steps[0].result_summary["rag_status"] == "available"


def test_indexing_skips_empty_spans_and_records_probe_embeddings() -> None:
    """Indexing uses persisted span text only and keeps probe vectors internal."""
    repo = RecordingVectorRepository()
    texts: list[str] = []

    def embed_batch(batch: list[str]) -> list[list[float]]:
        texts.extend(batch)
        return [_vector(0.1) for _ in batch]

    summary, probe_embeddings = index_document_spans_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        documents=_documents(include_empty=True),
        repository=repo,
        embed_batch=embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )

    assert summary["status"] == "indexed"
    assert summary["indexed_span_count"] == 1
    assert summary["skipped_span_count"] == 1
    assert len(repo.upserts) == 1
    assert repo.upserts[0]["run_id"] == RUN_ID
    assert repo.upserts[0]["source_type"] == "document_span"
    assert len(texts) == 1
    assert len(probe_embeddings) == 1
    assert "text_excerpt" not in json.dumps(summary)
    assert "PRIVATE" not in json.dumps(summary)


def test_probe_retrieval_returns_safe_matches_only() -> None:
    """Probe retrieval exposes plumbing proof matches without private content."""
    repo = RecordingVectorRepository()
    summary = retrieve_rag_probe_evidence(
        deal_id=DEAL_ID,
        probe_embeddings=[_vector(0.5)],
        repository=repo,
        limit=3,
    )

    assert summary["status"] == "probed"
    assert summary["retrieval_mode"] == "probe"
    assert summary["probe_count"] == 1
    assert summary["match_count"] == 1
    assert summary["matches"] == [
        {"source_type": "document_span", "source_id": SPAN_ID, "score": 0.99}
    ]
    encoded = json.dumps(summary)
    assert "embedding" not in encoded
    assert "text" not in encoded.lower()


def test_run_full_rag_evidence_strict_blocks_on_unhealthy_pgvector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict FULL runs fail closed when pgvector health is not ready."""
    monkeypatch.setenv("IDIS_ENABLE_VECTOR_SEARCH", "true")
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_rag_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=_documents(),
            db_conn=MagicMock(),
            strict_full_live=True,
            pgvector_health_checker=lambda _env: PgvectorHealthCheck.failed(),
            embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
                model="text-embedding-3-small",
                dimensions=VECTOR_EMBEDDING_DIMENSIONS,
            ),
        )

    assert exc_info.value.code == "RAG_HEALTH_BLOCKED"
    assert "text_excerpt" not in json.dumps(exc_info.value.result_summary or {})


def test_run_full_rag_evidence_non_strict_skips_when_unhealthy() -> None:
    """Non-strict runs skip RAG evidence when health checks fail."""
    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=_documents(),
        db_conn=MagicMock(),
        strict_full_live=False,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.missing(
            missing_env_vars=["IDIS_DATABASE_URL"]
        ),
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.missing(
            missing_env_vars=["OPENAI_API_KEY"]
        ),
    )

    assert summary["rag_status"] == "skipped"
    assert summary["rag_indexing"]["status"] == "skipped"
    assert summary["rag_retrieval"]["status"] == "skipped"


def test_run_full_rag_evidence_happy_path_uses_probe_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healthy strict path indexes spans and performs bounded probe retrieval."""
    monkeypatch.setenv("IDIS_ENABLE_VECTOR_SEARCH", "true")
    repo = RecordingVectorRepository()

    def indexing_service(**kwargs: Any) -> tuple[dict[str, Any], list[list[float]]]:
        summary, probes = index_document_spans_for_deal(
            embed_batch=lambda texts: [_vector(0.3) for _ in texts],
            **kwargs,
        )
        return summary, probes

    def retrieval_service(**kwargs: Any) -> dict[str, Any]:
        return retrieve_rag_probe_evidence(**kwargs)

    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=_documents(),
        db_conn=MagicMock(),
        strict_full_live=True,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small",
            dimensions=VECTOR_EMBEDDING_DIMENSIONS,
        ),
        indexing_service=indexing_service,
        retrieval_service=retrieval_service,
        vector_repository_factory=lambda _conn, _tenant_id: repo,
    )

    assert summary["rag_status"] == "available"
    assert summary["rag_indexing"]["status"] == "indexed"
    assert summary["rag_indexing"]["indexed_span_count"] == 2
    assert summary["rag_retrieval"]["status"] == "probed"
    assert summary["rag_retrieval"]["retrieval_mode"] == "probe"
    assert summary["rag_retrieval"]["match_count"] >= 1
    assert "PRIVATE" not in json.dumps(summary)


def test_product_bundle_includes_safe_rag_visibility(tmp_path: Path) -> None:
    """Product bundle exports probe-retrieval visibility without span text or vectors."""
    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice63",
    )

    rag_evidence = {
        "rag_status": "available",
        "rag_indexing": {
            "status": "indexed",
            "indexed_span_count": 2,
            "text_excerpt": "PRIVATE revenue span text must not leak",
            "embedding": _vector(0.1),
        },
        "rag_retrieval": {
            "status": "probed",
            "retrieval_mode": "probe",
            "probe_count": 1,
            "match_count": 1,
            "matches": [{"source_type": "document_span", "source_id": SPAN_ID, "score": 0.99}],
            "query_text": "PRIVATE query text must not leak",
        },
    }

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        rag_evidence=rag_evidence,
    )

    evidence_index = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/evidence_index.json",
        ).body.decode("utf-8")
    )
    run_summary = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/run_summary.json",
        ).body.decode("utf-8")
    )

    assert evidence_index["rag_evidence"]["status"] == "available"
    assert evidence_index["rag_evidence"]["retrieval"]["retrieval_mode"] == "probe"
    assert evidence_index["rag_evidence"]["retrieval"]["match_count"] == 1
    assert run_summary["rag_status"] == "available"
    assert run_summary["rag_indexing_status"] == "indexed"
    assert run_summary["rag_retrieval_status"] == "probed"

    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    assert "PRIVATE" not in encoded
    assert "embedding" not in encoded
    assert "query_text" not in encoded
    assert "text_excerpt" not in encoded


def test_strict_readiness_clears_rag_only_with_code_path_and_health() -> None:
    """Strict inventory clears RAG when health and FULL wiring code paths are proven."""
    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:pass@localhost/db",
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        },
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small",
            dimensions=VECTOR_EMBEDDING_DIMENSIONS,
        ),
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
    )
    inventory = {item.component_name: item for item in report.component_inventory}

    rag = inventory["pgvector/RAG"]
    assert rag.full_wired is True
    assert rag.output_visible is True
    assert rag.health_check_status == "healthy"

    rag_component = report.component("rag_evidence_retrieval")
    assert rag_component.status == StrictComponentStatus.LIVE_WIRED_AND_USED
    assert rag_component.may_proceed is True


def test_openai_llm_audit_stays_config_only_with_rag_embedding_sdk() -> None:
    """Embedding-only OpenAI SDK use under services/rag must not upgrade openai_llm."""
    from scripts.audit_full_system_wiring import collect_wiring_inventory

    repo_root = Path(__file__).resolve().parents[1]
    inventory = collect_wiring_inventory(repo_root)

    assert inventory["openai_llm"].status == "CONFIG_ONLY"
    assert inventory["rag_vector_retrieval"].status == "WIRED"


def test_rag_code_path_wired_returns_false_when_getsource_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source inspection failures must fail closed instead of crashing readiness."""
    import inspect

    from idis.services.runs.strict_full_live import _rag_code_path_wired

    def raise_oserror(*_args: object, **_kwargs: object) -> str:
        raise OSError("source unavailable test")

    monkeypatch.setattr(inspect, "getsource", raise_oserror)

    assert _rag_code_path_wired() is False


def test_rag_code_path_wired_returns_false_when_read_text_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File read failures during code-path proof must fail closed."""
    from pathlib import Path

    from idis.services.runs.strict_full_live import _rag_code_path_wired

    original_read_text = Path.read_text

    def fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name in {"indexing.py", "retrieval.py", "product_bundle.py"}:
            raise OSError("read unavailable test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert _rag_code_path_wired() is False


def test_rag_code_path_wired_returns_false_when_import_module_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import failures during code-path proof must fail closed."""
    import importlib

    from idis.services.runs.strict_full_live import _rag_code_path_wired

    original_import_module = importlib.import_module

    def fail_import_module(name: str, *args: object, **kwargs: object) -> object:
        if name == "idis.services.runs.steps":
            raise ImportError("module unavailable test")
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fail_import_module)

    assert _rag_code_path_wired() is False


def test_strict_readiness_report_survives_rag_code_path_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict readiness must not raise when RAG code-path inspection is unavailable."""
    import inspect

    def raise_oserror(*_args: object, **_kwargs: object) -> str:
        raise OSError("source unavailable test")

    monkeypatch.setattr(inspect, "getsource", raise_oserror)

    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:pass@localhost/db",
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        },
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small",
            dimensions=VECTOR_EMBEDDING_DIMENSIONS,
        ),
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
    )
    inventory = {item.component_name: item for item in report.component_inventory}

    assert inventory["pgvector/RAG"].full_wired is False
    assert inventory["pgvector/RAG"].output_visible is False
    assert report.component("rag_evidence_retrieval").may_proceed is False
