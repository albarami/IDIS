"""Slice91 Task 7 — acceptance proof for "RAG Retrieval In FULL Context".

Master-plan acceptance, proven end-to-end with injected fakes (no real OpenAI, no DB):
  1. The final package lists retrieved evidence with IDs/scores
     (``evidence_index.rag_evidence.retrieval.matches`` + run_summary counts).
  2. The RAG runtime proof is separate from pgvector connectivity
     (``rag_runtime_proof`` derives from the retrieval outcome alone and can differ
     from connectivity health in both directions).

Scoped consumer outcome (locked decisions DEC-A..DEC-E): the SAME safe probe matches
(IDs/scores only) reach the analysis, scoring, and debate prompt contexts in one
orchestrated FULL run, while extraction remains deliberately deferred by step ordering
(DEC-B) and the strict RAG_*_BLOCKED gates stay unchanged (pinned in Task 1).

GREEN-on-arrival expected: this composes surfaces built in Tasks 2-6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.analysis.agents.llm_specialist_agent import (
    _build_context_payload as _analysis_payload,
)
from idis.analysis.models import AnalysisBundle, AnalysisContext, AnalysisRagEvidence
from idis.analysis.scoring.llm_scorecard_runner import (
    _build_context_payload as _scoring_payload,
)
from idis.analysis.scoring.models import Stage
from idis.audit.sink import InMemoryAuditSink
from idis.debate.roles.llm_role_runner import DebateContext, LLMRoleRunner
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter, _rag_package
from idis.models.debate import DebateRole
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents

_MATCH_A = {
    "source_type": "document_span",
    "source_id": "aaaaaaaa-1111-1111-1111-111111111111",
    "score": 0.99,
}
_MATCH_B = {
    "source_type": "graph_summary",
    "source_id": "bbbbbbbb-2222-2222-2222-222222222222",
    "score": 0.91,
}

_RETRIEVAL = {
    "status": "probed",
    "retrieval_mode": "probe",
    "probe_count": 1,
    "match_count": 2,
    "matches": [_MATCH_B, _MATCH_A],  # unsorted on purpose; consumers sort
}

_RAG_STEP_RESULT = {
    "rag_status": "available",
    "rag_indexing": {"status": "indexed", "indexed_span_count": 2, "skipped_span_count": 0},
    "rag_retrieval": _RETRIEVAL,
}

_SORTED_IDS = [_MATCH_A["source_id"], _MATCH_B["source_id"]]


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


def _export_package(tmp_path: Path, rag_evidence: dict[str, Any]) -> tuple[dict, dict]:
    """Run the real exporter and return (evidence_index, run_summary) JSON."""
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice91-acceptance",
    )
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
    return evidence_index, run_summary


# --- Scoped consumer outcome: one FULL run feeds analysis, scoring, debate; not extraction ---


def test_full_run_threads_safe_rag_matches_to_all_post_rag_consumers() -> None:
    captured: dict[str, Any] = {}

    def extract_fn(**kwargs: Any) -> dict[str, Any]:
        captured["extract"] = kwargs
        return {"created_claim_ids": ["66666666-6666-6666-6666-666666666666"]}

    def analysis_fn(**kwargs: Any) -> dict[str, Any]:
        # Mirrors production _run_full_analysis: attach the retrieval to the context.
        captured["analysis"] = kwargs
        ctx = AnalysisContext(
            deal_id=DEAL_ID,
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            claim_ids=frozenset(),
            calc_ids=frozenset(),
            rag_evidence=AnalysisRagEvidence.from_retrieval_summary(kwargs["rag_retrieval"]),
        )
        bundle = AnalysisBundle(
            deal_id=DEAL_ID,
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            reports=[],
            timestamp="2026-01-01T00:00:00Z",
        )
        return {"_analysis_bundle": bundle, "_analysis_context": ctx}

    def debate_fn(**kwargs: Any) -> dict[str, Any]:
        captured["debate"] = kwargs
        return {"debate_id": RUN_ID, "muhasabah_passed": True}

    def scoring_fn(**kwargs: Any) -> dict[str, Any]:
        captured["scoring"] = kwargs
        return {"_scorecard": {}}

    def deliverables_fn(**kwargs: Any) -> dict[str, Any]:
        captured["deliverables"] = kwargs
        return {"deliverable_count": 1}

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
        rag_fn=lambda **_kwargs: _RAG_STEP_RESULT,
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
        analysis_fn=analysis_fn,
        scoring_fn=scoring_fn,
        deliverables_fn=deliverables_fn,
    )
    result = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    ).execute(ctx)
    assert result.status == "SUCCEEDED"

    # Extraction stays deferred by ordering (DEC-B): run scope + documents only.
    assert set(captured["extract"]) == {"run_id", "tenant_id", "deal_id", "documents"}

    # Analysis and debate receive the retrieval verbatim from accumulated state.
    assert captured["analysis"]["rag_retrieval"] == _RETRIEVAL
    assert captured["debate"]["rag_retrieval"] == _RETRIEVAL

    # Scoring receives the RAG-enriched AnalysisContext (no new threading needed).
    # The model preserves input order; sorting happens at the payload boundary below.
    scoring_ctx = captured["scoring"]["analysis_context"]
    assert {m.source_id for m in scoring_ctx.rag_evidence.matches} == set(_SORTED_IDS)

    # The prompt payloads/blocks each surface the same safe sorted matches.
    analysis_section = json.loads(_analysis_payload(scoring_ctx))["rag_evidence"]
    scoring_section = json.loads(
        _scoring_payload(scoring_ctx, captured["scoring"]["analysis_bundle"], Stage.SERIES_A)
    )["rag_evidence"]
    assert analysis_section == scoring_section
    assert [m["source_id"] for m in analysis_section["matches"]] == _SORTED_IDS
    for match in analysis_section["matches"]:
        assert set(match) == {"source_type", "source_id", "score"}

    debate_block = LLMRoleRunner(
        role=DebateRole.ADVOCATE,
        llm_client=MagicMock(),
        system_prompt="Test system prompt.",
        context=DebateContext(
            deal_name="Acme Corp",
            deal_sector="SaaS",
            deal_stage="Seed",
            deal_summary="",
            claims=[],
            calc_results=[],
            conflicts=[],
            rag_evidence=AnalysisRagEvidence.from_retrieval_summary(
                captured["debate"]["rag_retrieval"]
            ).to_payload_section(),
        ),
    )._serialize_context()
    assert "## RAG RETRIEVAL EVIDENCE (2 matches, status: probed)" in debate_block
    for source_id in _SORTED_IDS:
        assert source_id in debate_block

    # The deliverables step receives the same evidence for the final package.
    assert captured["deliverables"]["rag_evidence"]["rag_retrieval"] == _RETRIEVAL


# --- Acceptance 1: final package lists retrieved evidence with IDs/scores ---


def test_final_package_lists_retrieved_evidence_with_ids_and_scores(tmp_path: Path) -> None:
    evidence_index, run_summary = _export_package(tmp_path, dict(_RAG_STEP_RESULT))

    retrieval = evidence_index["rag_evidence"]["retrieval"]
    assert retrieval["status"] == "probed"
    assert retrieval["retrieval_mode"] == "probe"
    assert retrieval["match_count"] == 2
    listed = {(m["source_type"], m["source_id"], m["score"]) for m in retrieval["matches"]}
    assert listed == {
        ("document_span", _MATCH_A["source_id"], 0.99),
        ("graph_summary", _MATCH_B["source_id"], 0.91),
    }
    for match in retrieval["matches"]:
        assert set(match) == {"source_type", "source_id", "score"}

    assert run_summary["rag_retrieval_status"] == "probed"
    assert run_summary["rag_match_count"] == 2

    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    for forbidden in ("text_excerpt", "embedding", "query_text", "PRIVATE"):
        assert forbidden not in encoded


# --- Acceptance 2: RAG runtime proof is separate from pgvector connectivity ---


def test_rag_runtime_proof_is_separate_from_pgvector_connectivity(tmp_path: Path) -> None:
    # Proof present in the final package, derived from the retrieval outcome.
    evidence_index, run_summary = _export_package(tmp_path, dict(_RAG_STEP_RESULT))
    expected_proof = {"retrieval_ran": True, "retrieval_proved": True, "match_count": 2}
    assert evidence_index["rag_evidence"]["runtime_proof"] == expected_proof
    assert run_summary["rag_runtime_proof"] == expected_proof

    # Healthy connectivity with skipped retrieval is NOT runtime proof...
    healthy_but_skipped = _rag_package(
        {
            "rag_status": "skipped",
            "rag_indexing": {"status": "skipped", "indexed_span_count": 0},
            "rag_retrieval": {
                "status": "skipped",
                "retrieval_mode": "probe",
                "probe_count": 0,
                "match_count": 0,
                "matches": [],
            },
            "pgvector_health_status": "healthy",
        }
    )
    assert healthy_but_skipped["runtime_proof"] == {
        "retrieval_ran": False,
        "retrieval_proved": False,
        "match_count": 0,
    }

    # ...and varying connectivity never changes the proof for the same retrieval.
    proofs = []
    for health in ("healthy", "failed", None):
        evidence: dict[str, Any] = dict(_RAG_STEP_RESULT)
        if health is not None:
            evidence["pgvector_health_status"] = health
        proofs.append(_rag_package(evidence)["runtime_proof"])
    assert proofs[0] == proofs[1] == proofs[2] == expected_proof
