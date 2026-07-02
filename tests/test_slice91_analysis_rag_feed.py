"""Slice91 Task 3 — analysis consumes safe RAG probe matches (G2, analysis seam only).

Feeds the Slice90 probe-retrieval matches into the analysis prompt context per the locked
decisions: DEC-A reuse probe matches (no query retriever), DEC-C safe IDs/scores only (no text
chunks), no strict-gate changes, injected fakes only (no real OpenAI, no database).

Wiring under test:
  - ``AnalysisContext.rag_evidence`` — typed, frozen, defaults to the empty probe shape.
  - ``AnalysisRagEvidence.from_retrieval_summary`` — whitelist conversion from the RAG step's
    ``rag_retrieval`` summary; malformed/unsafe fields never enter the model.
  - ``llm_specialist_agent._build_context_payload`` — deterministic sorted ``rag_evidence``
    section, always present (explicit emptiness when absent).
  - ``RunOrchestrator._execute_analysis`` — threads only ``accumulated["rag_retrieval"]``
    (null-safe) into the injected analysis fn.
  - ``_run_full_analysis`` — accepts ``rag_retrieval`` and attaches it to the built context.

Scoring, debate, and extraction stay RAG-free (their Task 1 pins remain green).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from idis.analysis.agents.llm_specialist_agent import _build_context_payload
from idis.analysis.models import AnalysisContext
from idis.api.routes.runs import _run_full_analysis
from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.extraction.extractors.llm_client import DeterministicAnalysisLLMClient
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_slice63_rag_full_wiring import (
    DEAL_ID,
    RUN_ID,
    SPAN_ID,
    TENANT_ID,
    _documents,
)

_EMPTY_RAG_SECTION = {
    "status": "skipped",
    "retrieval_mode": "probe",
    "match_count": 0,
    "matches": [],
}


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


def _analysis_context(**overrides: Any) -> AnalysisContext:
    kwargs: dict[str, Any] = {
        "deal_id": DEAL_ID,
        "tenant_id": TENANT_ID,
        "run_id": RUN_ID,
        "claim_ids": frozenset(),
        "calc_ids": frozenset(),
    }
    kwargs.update(overrides)
    return AnalysisContext(**kwargs)


def _retrieval_summary() -> dict[str, Any]:
    # Unsorted matches + unsafe/malformed rows the converter must drop or ignore.
    return {
        "status": "probed",
        "retrieval_mode": "probe",
        "probe_count": 1,
        "match_count": 2,
        "matches": [
            {
                "source_type": "graph_summary",
                "source_id": "bbbbbbbb-2222-2222-2222-222222222222",
                "score": 0.91,
                "text_excerpt": "PRIVATE graph text must not enter analysis",
            },
            {
                "source_type": "document_span",
                "source_id": "aaaaaaaa-1111-1111-1111-111111111111",
                "score": 0.99,
                "embedding": [0.1, 0.2],
            },
            {"source_type": "", "source_id": "missing-type"},
            "not-a-dict",
        ],
    }


# --- AnalysisContext.rag_evidence: typed field with safe empty default ---


def test_analysis_context_rag_evidence_defaults_to_empty_probe_shape() -> None:
    ctx = _analysis_context()
    assert ctx.rag_evidence.status == "skipped"
    assert ctx.rag_evidence.retrieval_mode == "probe"
    assert ctx.rag_evidence.matches == []


def test_from_retrieval_summary_keeps_safe_ids_scores_only() -> None:
    from idis.analysis.models import AnalysisRagEvidence

    evidence = AnalysisRagEvidence.from_retrieval_summary(_retrieval_summary())
    assert evidence.status == "probed"
    assert evidence.retrieval_mode == "probe"
    assert len(evidence.matches) == 2
    for match in evidence.matches:
        assert set(match.model_dump(mode="json")) == {"source_type", "source_id", "score"}
    dumped = json.dumps(evidence.model_dump(mode="json"))
    assert "PRIVATE" not in dumped
    assert "text_excerpt" not in dumped
    assert "embedding" not in dumped


def test_from_retrieval_summary_absent_or_malformed_degrades_empty() -> None:
    from idis.analysis.models import AnalysisRagEvidence

    for value in (None, "not-a-dict", 7, {"status": "weird-status", "matches": "nope"}):
        evidence = AnalysisRagEvidence.from_retrieval_summary(value)
        assert evidence.status == "skipped"
        assert evidence.retrieval_mode == "probe"
        assert evidence.matches == []


def test_from_retrieval_summary_skips_malformed_or_non_finite_scores() -> None:
    # A bad score must never crash the analysis step, enter the model as NaN/inf,
    # or be silently rewritten to a misleading real score — the match is skipped.
    from idis.analysis.models import AnalysisRagEvidence

    evidence = AnalysisRagEvidence.from_retrieval_summary(
        {
            "status": "probed",
            "retrieval_mode": "probe",
            "matches": [
                {"source_type": "document_span", "source_id": "keep-1", "score": 0.9},
                {"source_type": "document_span", "source_id": "bad-str", "score": "not-a-number"},
                {"source_type": "document_span", "source_id": "bad-nan", "score": float("nan")},
                {"source_type": "document_span", "source_id": "bad-inf", "score": float("inf")},
                {"source_type": "document_span", "source_id": "bad-ninf", "score": float("-inf")},
                {"source_type": "document_span", "source_id": "bad-type", "score": [0.9]},
                {"source_type": "document_span", "source_id": "keep-2", "score": "0.5"},
            ],
        }
    )
    kept = {match.source_id: match.score for match in evidence.matches}
    # Valid matches survive (including coercible numeric strings); bad-score rows are dropped.
    assert kept == {"keep-1": 0.9, "keep-2": 0.5}


def test_from_retrieval_summary_skips_non_string_ids() -> None:
    # Review fix (Task 9): non-string IDs are skipped, never repr-stringified into
    # prompt-visible fields (a dict id would otherwise carry its payload as str(dict)).
    from idis.analysis.models import AnalysisRagEvidence

    evidence = AnalysisRagEvidence.from_retrieval_summary(
        {
            "status": "probed",
            "retrieval_mode": "probe",
            "matches": [
                {"source_type": "document_span", "source_id": "keep-1", "score": 0.9},
                {
                    "source_type": {"text_excerpt": "PRIVATE SPAN TEXT"},
                    "source_id": "bad-dict-type",
                    "score": 0.9,
                },
                {"source_type": "document_span", "source_id": ["list-id"], "score": 0.9},
                {"source_type": 7, "source_id": "bad-int-type", "score": 0.9},
                {"source_type": "document_span", "source_id": {"PRIVATE": "id"}, "score": 0.9},
            ],
        }
    )
    assert [match.source_id for match in evidence.matches] == ["keep-1"]
    assert "PRIVATE" not in json.dumps(evidence.model_dump(mode="json"))


# --- Analysis payload: deterministic sorted rag_evidence section ---


def test_analysis_payload_includes_rag_evidence_with_sorted_matches() -> None:
    from idis.analysis.models import AnalysisRagEvidence

    ctx = _analysis_context(
        rag_evidence=AnalysisRagEvidence.from_retrieval_summary(_retrieval_summary())
    )
    first = _build_context_payload(ctx)
    second = _build_context_payload(ctx)
    assert first == second  # deterministic

    payload = json.loads(first)
    section = payload["rag_evidence"]
    assert section["status"] == "probed"
    assert section["retrieval_mode"] == "probe"
    assert section["match_count"] == 2
    # Sorted by (source_type, source_id) regardless of input order.
    assert [m["source_id"] for m in section["matches"]] == [
        "aaaaaaaa-1111-1111-1111-111111111111",
        "bbbbbbbb-2222-2222-2222-222222222222",
    ]
    for match in section["matches"]:
        assert set(match) == {"source_type", "source_id", "score"}


def test_analysis_payload_rag_evidence_empty_when_absent() -> None:
    payload = json.loads(_build_context_payload(_analysis_context()))
    assert payload["rag_evidence"] == _EMPTY_RAG_SECTION


# --- Orchestrator: threads only accumulated["rag_retrieval"] into analysis ---


def test_orchestrator_threads_rag_retrieval_into_analysis() -> None:
    analysis_calls: list[dict[str, Any]] = []

    def analysis_fn(**kwargs: Any) -> dict[str, Any]:
        analysis_calls.append(kwargs)
        return {"_analysis_bundle": {}, "_analysis_context": {}}

    def _ctx(rag_fn_result: dict[str, Any]) -> RunContext:
        return RunContext(
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
            rag_fn=lambda **_kwargs: rag_fn_result,
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
            analysis_fn=analysis_fn,
            scoring_fn=lambda **_kwargs: {"_scorecard": {}},
            deliverables_fn=lambda **_kwargs: {"deliverable_count": 1},
        )

    def _run(rag_fn_result: dict[str, Any]) -> None:
        repo = InMemoryRunStepsRepository(TENANT_ID)
        orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
        result = orchestrator.execute(_ctx(rag_fn_result))
        assert result.status == "SUCCEEDED"

    retrieval = {
        "status": "probed",
        "retrieval_mode": "probe",
        "probe_count": 1,
        "match_count": 1,
        "matches": [{"source_type": "document_span", "source_id": SPAN_ID, "score": 1.0}],
    }
    _run(
        {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 1},
            "rag_retrieval": retrieval,
        }
    )
    assert analysis_calls[0]["rag_retrieval"] == retrieval

    # No rag_retrieval in the RAG step result -> analysis receives None (null-safe).
    clear_run_steps_store()
    _run({"rag_status": "available", "rag_indexing": {"status": "indexed"}})
    assert analysis_calls[1]["rag_retrieval"] is None


# --- Production analysis fn: rag_retrieval lands on the built AnalysisContext ---


def test_run_full_analysis_attaches_rag_evidence_to_context() -> None:
    result = _run_full_analysis(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        created_claim_ids=["66666666-6666-6666-6666-666666666666"],
        calc_ids=[],
        enrichment_refs={},
        rag_retrieval=_retrieval_summary(),
        analysis_client_factory=lambda _selection: DeterministicAnalysisLLMClient(),
    )
    ctx = result["_analysis_context"]
    assert ctx.rag_evidence.status == "probed"
    assert {m.source_id for m in ctx.rag_evidence.matches} == {
        "aaaaaaaa-1111-1111-1111-111111111111",
        "bbbbbbbb-2222-2222-2222-222222222222",
    }
    assert "PRIVATE" not in json.dumps(ctx.rag_evidence.model_dump(mode="json"))
