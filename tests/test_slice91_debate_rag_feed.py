"""Slice91 Task 5 — debate consumes safe RAG probe matches (G2, debate seam only).

Feeds the probe-retrieval matches into the debate prompt context per the locked decisions:
DEC-A reuse probe matches, DEC-C safe IDs/scores only (no text chunks), no strict-gate
changes, injected fakes only (no real OpenAI, no database).

Wiring under test:
  - ``DebateContext.rag_evidence`` — plain-dict section (DebateContext idiom), defaulting
    empty; holds the SAME safe section shape the analysis/scoring payloads emit, produced
    by ``AnalysisRagEvidence.to_payload_section()``.
  - ``LLMRoleRunner._serialize_context`` — renders a RAG markdown block with IDs/scores only.
  - ``RunOrchestrator._execute_debate`` — threads only ``accumulated["rag_retrieval"]``.
  - ``_run_full_debate`` — accepts ``rag_retrieval`` and attaches the safe section to the
    built ``DebateContext``.

Extraction stays RAG-free (its Task 1 pin remains green).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import idis.api.routes.runs as runs_mod
from idis.api.routes.runs import _run_full_debate
from idis.audit.sink import InMemoryAuditSink
from idis.debate.roles.llm_role_runner import DebateContext, LLMRoleRunner
from idis.models.debate import DebateRole
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_slice63_rag_full_wiring import (
    DEAL_ID,
    RUN_ID,
    SPAN_ID,
    TENANT_ID,
    _documents,
)
from tests.test_slice91_analysis_rag_feed import _retrieval_summary

_SORTED_SECTION = {
    "status": "probed",
    "retrieval_mode": "probe",
    "match_count": 2,
    "matches": [
        {
            "source_type": "document_span",
            "source_id": "aaaaaaaa-1111-1111-1111-111111111111",
            "score": 0.99,
        },
        {
            "source_type": "graph_summary",
            "source_id": "bbbbbbbb-2222-2222-2222-222222222222",
            "score": 0.91,
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


def _debate_context(**overrides: Any) -> DebateContext:
    kwargs: dict[str, Any] = {
        "deal_name": "Acme Corp",
        "deal_sector": "SaaS",
        "deal_stage": "Seed",
        "deal_summary": "",
        "claims": [],
        "calc_results": [],
        "conflicts": [],
    }
    kwargs.update(overrides)
    return DebateContext(**kwargs)


def _runner(context: DebateContext) -> LLMRoleRunner:
    return LLMRoleRunner(
        role=DebateRole.ADVOCATE,
        llm_client=MagicMock(),
        system_prompt="Test system prompt.",
        context=context,
    )


# --- DebateContext.rag_evidence: additive plain-dict field ---


def test_debate_context_rag_evidence_defaults_empty() -> None:
    assert _debate_context().rag_evidence == {}


# --- Shared section shape: debate consumes exactly what analysis/scoring emit ---


def test_rag_payload_section_helper_matches_analysis_shape() -> None:
    import json

    from idis.analysis.agents.llm_specialist_agent import _build_context_payload
    from idis.analysis.models import AnalysisContext, AnalysisRagEvidence

    evidence = AnalysisRagEvidence.from_retrieval_summary(_retrieval_summary())
    section = evidence.to_payload_section()
    assert section == _SORTED_SECTION

    analysis_ctx = AnalysisContext(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        claim_ids=frozenset(),
        calc_ids=frozenset(),
        rag_evidence=evidence,
    )
    assert json.loads(_build_context_payload(analysis_ctx))["rag_evidence"] == section


# --- Serialized debate prompt block: IDs/scores only ---


def test_serialize_context_renders_rag_section_with_ids_scores() -> None:
    serialized = _runner(_debate_context(rag_evidence=_SORTED_SECTION))._serialize_context()
    assert "## RAG RETRIEVAL EVIDENCE (2 matches, status: probed)" in serialized
    assert "- document_span aaaaaaaa-1111-1111-1111-111111111111 (score: 0.99)" in serialized
    assert "- graph_summary bbbbbbbb-2222-2222-2222-222222222222 (score: 0.91)" in serialized
    assert "PRIVATE" not in serialized
    assert "text_excerpt" not in serialized


def test_serialize_context_rag_section_when_empty() -> None:
    serialized = _runner(_debate_context())._serialize_context()
    assert "## RAG RETRIEVAL EVIDENCE (0 matches, status: skipped)" in serialized
    assert "(no retrieval matches for this run)" in serialized


# --- Orchestrator: threads only accumulated["rag_retrieval"] into debate ---


def test_orchestrator_threads_rag_retrieval_into_debate() -> None:
    debate_calls: list[dict[str, Any]] = []

    def debate_fn(**kwargs: Any) -> dict[str, Any]:
        debate_calls.append(kwargs)
        return {"debate_id": RUN_ID, "muhasabah_passed": True}

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
            debate_fn=debate_fn,
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
    assert debate_calls[0]["rag_retrieval"] == retrieval

    # No rag_retrieval in the RAG step result -> debate receives None (null-safe).
    clear_run_steps_store()
    _run({"rag_status": "available", "rag_indexing": {"status": "indexed"}})
    assert debate_calls[1]["rag_retrieval"] is None


# --- Production debate fn: safe section lands on the built DebateContext ---


def test_run_full_debate_attaches_rag_section_to_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _StopForTest(Exception):
        pass

    def fake_build_role_runners(context: Any = None, **_kw: Any) -> Any:
        captured["context"] = context
        raise _StopForTest

    monkeypatch.setattr(runs_mod, "_build_debate_role_runners", fake_build_role_runners)
    with pytest.raises(_StopForTest):
        _run_full_debate(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            created_claim_ids=[],
            calc_ids=[],
            rag_retrieval=_retrieval_summary(),
        )
    context = captured["context"]
    assert context.rag_evidence == _SORTED_SECTION
