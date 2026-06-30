"""Slice89 Task 5 — acceptance proof for the master-plan graph-retrieval acceptance.

Acceptance: the VC package contains graph-derived conclusions and provenance; a strict FULL graph
retrieval blocks safely when retrieval fails.

  - The FULL graph step produces graph_conclusions (per-claim lineage + defect-impact); extracting
    them as `_run_full_deliverables` does and generating the bundle yields IC-memo facts that carry
    existing claim/calc provenance (No-Free-Facts).
  - A strict FULL run blocks (GRAPH_RETRIEVAL_BLOCKED) when retrieval fails.
  - No raw graph records/text/entity names/source-system strings/paths/private values leak into the
    VC package, even when the graph repo returns adversarial data.

Proven with injected fakes — no real Neo4j run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from idis.api.routes.runs import _run_full_graph_evidence
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.persistence.neo4j_driver import Neo4jHealthCheck
from idis.persistence.repositories.claims import (
    InMemoryDefectsRepository,
    clear_defects_in_memory_store,
    clear_sanad_in_memory_store,
)
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_investment_grade_context,
    _make_scorecard,
)
from tests.test_slice61_graph_visibility import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    FailingRetrievalService,
    FakeGraphRepository,
    RecordingProjectionService,
)
from tests.test_slice89_graph_conclusions import _ConclusionsFakeRepo


@pytest.fixture(autouse=True)
def _clear() -> Any:
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()
    yield
    clear_sanad_in_memory_store()
    clear_defects_in_memory_store()


def _full_graph_evidence(*, repo: Any, seed_defect: bool) -> dict[str, Any]:
    """Run the FULL graph step (projection + retrieval) and return its graph_evidence summary."""
    if seed_defect:
        InMemoryDefectsRepository(TENANT_ID).create(
            defect_id="def-1",
            claim_id="claim-fin",
            deal_id=DEAL_ID,
            defect_type="CONTRADICTION",
            severity="MAJOR",
            description="seeded",
            cure_protocol="review",
        )
    return _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-fin"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RecordingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=repo),
    )


def _vc_bundle(graph_evidence: dict[str, Any]) -> Any:
    """Extract graph_conclusions as `_run_full_deliverables` does, then generate the VC bundle."""
    graph_conclusions: dict[str, Any] | None = None
    retrieval = graph_evidence.get("graph_retrieval")
    if isinstance(retrieval, dict) and isinstance(retrieval.get("graph_conclusions"), dict):
        graph_conclusions = retrieval["graph_conclusions"]
    generator = DeliverablesGenerator(audit_sink=InMemoryAuditSink())
    return generator.generate(
        ctx=_make_investment_grade_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-acc",
        graph_conclusions=graph_conclusions,
    )


def _graph_facts(deliverable: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if str(obj.get("text", "")).startswith("Graph-derived"):
                found.append(obj)
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(deliverable.model_dump(mode="json"))
    return found


# --- Acceptance 1: VC package contains graph-derived conclusions + provenance ---


def test_vc_package_contains_graph_derived_conclusions_with_provenance() -> None:
    graph_evidence = _full_graph_evidence(repo=_ConclusionsFakeRepo(), seed_defect=True)

    # The FULL graph step produced both per-claim lineage and defect-impact conclusions.
    conclusions = graph_evidence["graph_retrieval"]["graph_conclusions"]
    assert [claim["claim_id"] for claim in conclusions["claims"]] == ["claim-fin"]
    assert [impact["defect_id"] for impact in conclusions["defect_impacts"]] == ["def-1"]

    graph_facts = _graph_facts(_vc_bundle(graph_evidence).ic_memo)
    assert graph_facts  # graph-derived conclusions reached the VC package

    # Every graph-derived fact is factual and carries existing-ref provenance (No-Free-Facts).
    for fact in graph_facts:
        assert fact["is_factual"] is True
        assert fact["claim_refs"] or fact["calc_refs"]

    # Per-claim lineage cites the existing claim; defect-impact cites affected claim + calc.
    assert any("claim-fin" in fact["claim_refs"] for fact in graph_facts)
    assert any(fact["claim_refs"] and fact["calc_refs"] for fact in graph_facts)


# --- Acceptance 2: strict FULL graph retrieval blocks safely when retrieval fails ---


def test_strict_full_graph_retrieval_blocks_safely_on_failure() -> None:
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_graph_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            created_claim_ids=["claim-fin"],
            calc_ids=[],
            strict_full_live=True,
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
            projection_service=RecordingProjectionService(),
            retrieval_service=FailingRetrievalService(),
        )
    assert exc_info.value.code == "GRAPH_RETRIEVAL_BLOCKED"


# --- Acceptance 3: no raw/private values leak into the VC package ---


def test_vc_package_leaks_no_raw_or_private_values() -> None:
    # FakeGraphRepository returns adversarial private data across all six read paths.
    graph_evidence = _full_graph_evidence(repo=FakeGraphRepository(), seed_defect=True)
    encoded = json.dumps(_vc_bundle(graph_evidence).ic_memo.model_dump(mode="json"))
    for leak in (
        "PRIVATE raw revenue text",
        "secret-host",
        "private_user",
        "/Users/private",
        "C:\\Projects",
    ):
        assert leak not in encoded
