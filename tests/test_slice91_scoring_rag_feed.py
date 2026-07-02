"""Slice91 Task 4 — scoring consumes safe RAG probe matches (G2, scoring seam only).

Surfaces the existing ``AnalysisContext.rag_evidence`` (attached by the analysis step in
Task 3) in the scoring prompt payload, in the SAME safe IDs/scores-only shape the analysis
payload uses (DEC-C: no text chunks). No new orchestrator threading is needed: scoring
already receives ``_analysis_context`` and ``_run_full_scoring`` passes it to the runner.

Injected fakes only — no real OpenAI, no database, no strict-gate changes.
"""

from __future__ import annotations

import json

from idis.analysis.agents.llm_specialist_agent import (
    _build_context_payload as _analysis_payload,
)
from idis.analysis.models import AnalysisBundle, AnalysisContext, AnalysisRagEvidence
from idis.analysis.scoring.llm_scorecard_runner import (
    _build_context_payload as _scoring_payload,
)
from idis.analysis.scoring.models import Stage
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID
from tests.test_slice91_analysis_rag_feed import _retrieval_summary

_EMPTY_RAG_SECTION = {
    "status": "skipped",
    "retrieval_mode": "probe",
    "match_count": 0,
    "matches": [],
}


def _analysis_context(rag_evidence: AnalysisRagEvidence | None = None) -> AnalysisContext:
    kwargs: dict[str, object] = {
        "deal_id": DEAL_ID,
        "tenant_id": TENANT_ID,
        "run_id": RUN_ID,
        "claim_ids": frozenset(),
        "calc_ids": frozenset(),
    }
    if rag_evidence is not None:
        kwargs["rag_evidence"] = rag_evidence
    return AnalysisContext(**kwargs)


def _bundle() -> AnalysisBundle:
    return AnalysisBundle(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        reports=[],
        timestamp="2026-01-01T00:00:00Z",
    )


def test_scoring_payload_includes_rag_evidence_with_sorted_matches() -> None:
    ctx = _analysis_context(AnalysisRagEvidence.from_retrieval_summary(_retrieval_summary()))
    first = _scoring_payload(ctx, _bundle(), Stage.SERIES_A)
    second = _scoring_payload(ctx, _bundle(), Stage.SERIES_A)
    assert first == second  # deterministic

    section = json.loads(first)["rag_evidence"]
    assert section["status"] == "probed"
    assert section["retrieval_mode"] == "probe"
    assert section["match_count"] == 2
    # Sorted by (source_type, source_id); safe IDs/scores only.
    assert [m["source_id"] for m in section["matches"]] == [
        "aaaaaaaa-1111-1111-1111-111111111111",
        "bbbbbbbb-2222-2222-2222-222222222222",
    ]
    for match in section["matches"]:
        assert set(match) == {"source_type", "source_id", "score"}
    encoded = json.dumps(section)
    assert "PRIVATE" not in encoded
    assert "text_excerpt" not in encoded


def test_scoring_payload_rag_evidence_empty_when_absent() -> None:
    payload = json.loads(_scoring_payload(_analysis_context(), _bundle(), Stage.SERIES_A))
    assert payload["rag_evidence"] == _EMPTY_RAG_SECTION


def test_scoring_rag_section_identical_to_analysis_shape() -> None:
    # The same context must surface the identical rag_evidence section in both
    # the analysis payload and the scoring payload (same safe shape, DEC-C).
    ctx = _analysis_context(AnalysisRagEvidence.from_retrieval_summary(_retrieval_summary()))
    analysis_section = json.loads(_analysis_payload(ctx))["rag_evidence"]
    scoring_section = json.loads(_scoring_payload(ctx, _bundle(), Stage.SERIES_A))["rag_evidence"]
    assert scoring_section == analysis_section
