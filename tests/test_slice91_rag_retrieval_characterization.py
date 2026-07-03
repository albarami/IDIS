"""Slice91 Task 1 — characterization pinning the CURRENT RAG-retrieval-in-FULL-context truth.

Slice90 landed indexing + a probe-mode retrieval whose safe ``{source_type, source_id, score}``
matches are already surfaced in the VC package. This pins the as-built reality Slice91 builds on,
under the locked decisions (DEC-A..DEC-E):

  1. (G1 / DEC-A) Retrieval is probe-only: ``retrieve_rag_probe_evidence`` consumes indexed
     ``probe_embeddings`` (not an external query); there is no query-driven retriever. Slice91
     reuses these probe matches as the "retrieved evidence" rather than adding a query retriever.
  2. (DEC-C) A probe call returns matches carrying only ``source_type``/``source_id``/``score`` —
     no text/vectors/query. Slice91 feeds these safe IDs/scores, not text chunks.
  3. (G2-analysis closed, Task 3) Analysis context/payload carry safe RAG evidence
     (IDs/scores only) via ``AnalysisContext.rag_evidence``.
  4. (G2-scoring closed, Task 4) Scoring payload carries the same safe ``rag_evidence``
     section as analysis (IDs/scores only), read from the shared ``AnalysisContext``.
  5. (G2-debate closed, Task 5) ``DebateContext.rag_evidence`` carries the same safe
     IDs/scores-only section, serialized into the debate prompt.
  6. (G3 / DEC-B) EXTRACT is ordered before RAG_EVIDENCE in FULL, so extraction cannot consume
     RAG retrieval this slice — the ordering deferral is pinned, not wired.
  7. (Acceptance 1) The export already lists retrieved evidence with IDs/scores via
     ``_safe_rag_retrieval``, which also sanitizes away any text.
  8. (G4 closed, Task 2 / DEC-D) ``rag_runtime_proof`` exists as a bundle summary signal,
     derived from the retrieval outcome, distinct from pgvector connectivity.
  9. (DEC-D / DEC-E) The strict RAG_*_BLOCKED gate set is intact and no new runtime-proof gate is
     added (runtime proof is a summary/readiness signal only).

GREEN-on-arrival expected (characterization pins current truth). Any RED → STOP + report.
No production changes. Injected fakes only — no real OpenAI, no database.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields as dataclass_fields
from pathlib import Path

import idis.api.routes.runs as runs_mod
import idis.deliverables.product_bundle as product_bundle_mod
import idis.services.rag.retrieval as retrieval_mod
from idis.analysis.agents.llm_specialist_agent import _build_context_payload as _analysis_payload
from idis.analysis.models import AnalysisBundle, AnalysisContext
from idis.analysis.scoring.llm_scorecard_runner import _build_context_payload as _scoring_payload
from idis.analysis.scoring.models import Stage
from idis.debate.roles.llm_role_runner import DebateContext
from idis.deliverables.product_bundle import _safe_rag_retrieval
from idis.models.run_step import FULL_STEPS, STEP_ORDER, StepName
from idis.services.rag.retrieval import RETRIEVAL_MODE_PROBE, retrieve_rag_probe_evidence
from tests.test_slice63_rag_full_wiring import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingVectorRepository,
)

_READINESS_DOC = Path("docs/architecture/strict_full_live_readiness.md")


def _analysis_context() -> AnalysisContext:
    return AnalysisContext(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        claim_ids=frozenset(),
        calc_ids=frozenset(),
    )


# --- G1 / DEC-A: retrieval is probe-only; no query-driven retriever ---


def test_retrieval_is_probe_only_no_query_driven_retriever() -> None:
    assert RETRIEVAL_MODE_PROBE == "probe"
    params = set(inspect.signature(retrieve_rag_probe_evidence).parameters)
    # Consumes indexed probe embeddings, not an external query.
    assert "probe_embeddings" in params
    assert "query" not in params
    assert "query_text" not in params
    assert "query_embedding" not in params
    # No separate query-driven retriever exists (Slice91 reuses probe matches instead).
    assert not hasattr(retrieval_mod, "retrieve_rag_evidence")


# --- DEC-C: probe matches carry only safe IDs/scores (no text/vectors/query) ---


def test_probe_retrieval_returns_safe_ids_scores_only() -> None:
    result = retrieve_rag_probe_evidence(
        deal_id=DEAL_ID,
        probe_embeddings=[[0.1, 0.2, 0.3]],
        repository=RecordingVectorRepository(),
        limit=5,
    )
    assert result["retrieval_mode"] == "probe"
    assert result["status"] == "probed"
    assert result["match_count"] == 1
    for match in result["matches"]:
        assert set(match) == {"source_type", "source_id", "score"}


# --- G2-analysis closed (Task 3): analysis context/payload carry safe RAG evidence ---


def test_analysis_context_and_payload_carry_rag_evidence() -> None:
    assert "rag_evidence" in AnalysisContext.model_fields
    payload = json.loads(_analysis_payload(_analysis_context()))
    assert payload["rag_evidence"]["retrieval_mode"] == "probe"
    assert payload["rag_evidence"]["matches"] == []


# --- G2-scoring closed (Task 4): scoring payload carries safe RAG evidence ---


def test_scoring_payload_carries_rag_evidence() -> None:
    bundle = AnalysisBundle(
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        reports=[],
        timestamp="2026-01-01T00:00:00Z",
    )
    payload = json.loads(_scoring_payload(_analysis_context(), bundle, Stage.SERIES_A))
    assert payload["rag_evidence"]["retrieval_mode"] == "probe"
    assert payload["rag_evidence"]["matches"] == []


# --- G2-debate closed (Task 5): debate context carries the safe RAG field ---


def test_debate_context_carries_rag_evidence_field() -> None:
    field_names = {f.name for f in dataclass_fields(DebateContext)}
    assert "rag_evidence" in field_names


# --- G3 / DEC-B: EXTRACT is ordered before RAG_EVIDENCE (extraction descoped) ---


def test_extraction_runs_before_rag_evidence() -> None:
    assert FULL_STEPS.index(StepName.EXTRACT) < FULL_STEPS.index(StepName.RAG_EVIDENCE)
    assert STEP_ORDER[StepName.EXTRACT] < STEP_ORDER[StepName.RAG_EVIDENCE]


# --- Acceptance 1: export lists retrieved evidence with IDs/scores (and drops text) ---


def test_export_rag_retrieval_lists_matches_with_ids_and_scores() -> None:
    safe = _safe_rag_retrieval(
        {
            "status": "probed",
            "retrieval_mode": "probe",
            "probe_count": 1,
            "match_count": 1,
            "matches": [
                {
                    "source_type": "document_span",
                    "source_id": "span-uuid-1",
                    "score": 0.99,
                    "text_excerpt": "SENSITIVE TEXT THAT MUST NOT LEAK",
                }
            ],
        }
    )
    assert safe["status"] == "probed"
    assert safe["retrieval_mode"] == "probe"
    assert len(safe["matches"]) == 1
    match = safe["matches"][0]
    # IDs/scores are listed; any text is sanitized away (DEC-C safety).
    assert set(match) == {"source_type", "source_id", "score"}
    assert match["source_id"] == "span-uuid-1"


# --- G4 closed (Task 2 / DEC-D): rag_runtime_proof is a bundle summary signal ---


def test_rag_runtime_proof_signal_present_as_summary_only() -> None:
    bundle_src = Path(product_bundle_mod.__file__).read_text(encoding="utf-8")
    assert "rag_runtime_proof" in bundle_src


# --- Task 8: readiness doc reconciled to the Slice91 as-built consumption reality ---


def test_readiness_doc_reconciled_slice91_rag_consumption() -> None:
    doc = _READINESS_DOC.read_text(encoding="utf-8")
    # Consumer feeds are wired (safe IDs/scores only) and the package lists them.
    assert "feed the analysis, scoring, and debate prompt contexts" in doc
    assert "rag_runtime_proof" in doc
    assert "distinct from pgvector connectivity" in doc
    # Extraction stays honestly deferred; retriever/text recovery stay out of scope.
    assert "deferred by step ordering" in doc
    assert "Query-driven retrieval and text-chunk recovery remain out of scope" in doc
    # The Slice90 indexing wording is preserved (its pins stay green).
    assert "Indexes `document_span`" in doc
    assert "Enrichment-record indexing is deferred" in doc


# --- DEC-D / DEC-E: strict gate set intact; no new runtime-proof gate ---


def test_strict_rag_blocked_gates_unchanged_no_new_runtime_gate() -> None:
    runs_src = Path(runs_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "RAG_CONFIG_BLOCKED",
        "RAG_HEALTH_BLOCKED",
        "RAG_DATABASE_BLOCKED",
        "RAG_INDEXING_BLOCKED",
        "RAG_PROBE_RETRIEVAL_BLOCKED",
    ):
        assert code in runs_src
    # Runtime proof (DEC-D) is a summary/readiness signal, not a new strict gate.
    assert "RAG_RUNTIME_PROOF_BLOCKED" not in runs_src
    assert "RAG_RETRIEVAL_PROOF_BLOCKED" not in runs_src
