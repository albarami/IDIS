"""Slice91 Task 6 — extraction RAG-feed deferral pin (DEC-B, characterization only).

DEC-B locked: extraction is deliberately DESCOPED from the Slice91 RAG consumer feeds.
The reason is structural, not stylistic — EXTRACT runs before RAG_EVIDENCE in the FULL
sequence, so no ``rag_retrieval`` exists when extraction executes. Wiring extraction to
RAG would require reordering the pipeline or a separate pre-indexed retrieval, both out
of scope for this slice. Debate/analysis/scoring (which run after RAG) are the Slice91
consumers (Tasks 3-5); the export lists the matches (Acceptance 1).

This file pins the deferral at every seam so any future change is a conscious flip:
  1. Ordering: EXTRACT strictly precedes RAG_EVIDENCE in FULL; SNAPSHOT has no RAG step.
  2. Orchestrator: ``_execute_extract`` passes exactly run/tenant/deal/documents —
     it does not even receive ``accumulated``, so it structurally cannot thread RAG.
  3. Prompt/production seams: the claim-extractor prompt builder and the shared
     extraction fn carry no RAG parameters or references.
  4. The descope decision is recorded in the Slice91 plan (DEC-B).

GREEN-on-arrival expected (pins current truth; complements the Task 1 ordering pin).
No production changes. Injected fakes only — no real OpenAI, no database.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import idis.services.extraction.extractors.claim_extractor as claim_extractor_mod
from idis.api.routes.runs import _run_snapshot_extraction
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import FULL_STEPS, SNAPSHOT_STEPS, STEP_ORDER, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents

_PLAN_DOC = Path("docs/plans/2026-07-01-slice91-rag-retrieval-full-context.md")


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


# --- 1. Ordering: extraction cannot see RAG output ---


def test_extract_strictly_precedes_rag_evidence_and_snapshot_has_no_rag() -> None:
    assert FULL_STEPS.index(StepName.EXTRACT) < FULL_STEPS.index(StepName.RAG_EVIDENCE)
    assert STEP_ORDER[StepName.EXTRACT] < STEP_ORDER[StepName.RAG_EVIDENCE]
    # SNAPSHOT (extraction-only pipeline) has no RAG step at all.
    assert StepName.EXTRACT in SNAPSHOT_STEPS
    assert StepName.RAG_EVIDENCE not in SNAPSHOT_STEPS


# --- 2. Orchestrator: extract_fn receives no RAG (and no accumulated state) ---


def test_execute_extract_passes_exactly_run_scope_and_documents() -> None:
    extract_calls: list[dict[str, Any]] = []

    def extract_fn(**kwargs: Any) -> dict[str, Any]:
        extract_calls.append(kwargs)
        return {"created_claim_ids": ["claim-001"]}

    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    ctx = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=_documents(),
        deal_metadata={"tenant_id": TENANT_ID, "company_name": "Acme Corp"},
        extract_fn=extract_fn,
        grade_fn=lambda **_kwargs: {},
        calc_fn=lambda **_kwargs: {"calc_ids": ["calc-001"]},
        graph_fn=lambda **_kwargs: {"graph_status": "skipped"},
        rag_fn=lambda **_kwargs: {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 1},
            "rag_retrieval": {
                "status": "probed",
                "retrieval_mode": "probe",
                "probe_count": 1,
                "match_count": 0,
                "matches": [],
            },
        },
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

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    # Exactly the run scope + documents — nothing RAG-ish can reach extraction.
    assert set(extract_calls[0]) == {"run_id", "tenant_id", "deal_id", "documents"}


# --- 3. Prompt/production seams: no RAG parameters or references ---


def test_extraction_prompt_builder_and_production_fn_carry_no_rag() -> None:
    prompt_params = set(inspect.signature(LLMClaimExtractor._build_prompt).parameters) - {"self"}
    assert prompt_params == {
        "document_type",
        "document_name",
        "chunk_locator",
        "chunk_content",
    }

    extraction_params = set(inspect.signature(_run_snapshot_extraction).parameters)
    assert not any("rag" in param for param in extraction_params)

    extractor_src = Path(claim_extractor_mod.__file__).read_text(encoding="utf-8")
    for token in ("rag_evidence", "rag_retrieval", "rag_matches"):
        assert token not in extractor_src


# --- 4. The descope decision is recorded (DEC-B) ---


def test_extraction_descope_recorded_in_slice91_plan() -> None:
    plan = _PLAN_DOC.read_text(encoding="utf-8")
    assert "DEC-B" in plan
    assert "descope extraction" in plan
