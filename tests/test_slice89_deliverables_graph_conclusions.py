"""Slice89 Task 3 — graph-derived conclusions in the VC package / deliverables.

The deliverables generator renders the Task-2 `graph_conclusions` (safe per-claim lineage +
defect-impact) into IC-memo facts that carry existing claim/calc provenance (No-Free-Facts).
No new RefType; no raw graph records/text/entity names/source-system strings/paths.
"""

from __future__ import annotations

import json
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_investment_grade_context,
    _make_scorecard,
)

_GRAPH_CONCLUSIONS: dict[str, Any] = {
    "claims": [
        {
            "claim_id": "claim-fin",
            "chain_depth": 3,
            "weakest_grade": "B",
            "corroboration_status": "AHAD_2",
            "independent_source_count": 2,
        }
    ],
    "defect_impacts": [
        {
            "defect_id": "def-1",
            "defect_type": "CONTRADICTION",
            "severity": "MAJOR",
            "affected_claim_ids": ["claim-risk"],
            "affected_calc_ids": ["calc-arr"],
        }
    ],
    "co_occurring_entity_count": 2,
}


def _generate(graph_conclusions: dict[str, Any] | None) -> Any:
    generator = DeliverablesGenerator(audit_sink=InMemoryAuditSink())
    return generator.generate(
        ctx=_make_investment_grade_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-run001",
        graph_conclusions=graph_conclusions,
    )


def _facts(deliverable: Any) -> list[dict[str, Any]]:
    """Walk a deliverable's model dump and collect every DeliverableFact-shaped dict."""
    found: list[dict[str, Any]] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "text" in obj and "claim_refs" in obj:
                found.append(obj)
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(deliverable.model_dump(mode="json"))
    return found


def _graph_facts(deliverable: Any) -> list[dict[str, Any]]:
    return [f for f in _facts(deliverable) if str(f.get("text", "")).startswith("Graph-derived")]


def test_ic_memo_contains_graph_derived_facts_with_provenance() -> None:
    bundle = _generate(_GRAPH_CONCLUSIONS)
    graph_facts = _graph_facts(bundle.ic_memo)
    assert graph_facts  # graph-derived conclusions are surfaced

    # Every graph-derived fact is factual and carries existing-ref provenance (No-Free-Facts).
    for fact in graph_facts:
        assert fact.get("is_factual") is True
        assert fact.get("claim_refs") or fact.get("calc_refs")

    # Per-claim lineage cites the existing claim; defect-impact cites affected claim + calc.
    assert any("claim-fin" in fact["claim_refs"] for fact in graph_facts)
    assert any(
        "claim-risk" in fact["claim_refs"] and "calc-arr" in fact["calc_refs"]
        for fact in graph_facts
    )


def test_graph_facts_absent_without_conclusions() -> None:
    bundle = _generate(graph_conclusions=None)
    assert _graph_facts(bundle.ic_memo) == []


def test_generated_bundle_with_graph_facts_passes_no_free_facts() -> None:
    # generate() validates No-Free-Facts internally; a returned bundle means it passed.
    bundle = _generate(_GRAPH_CONCLUSIONS)
    assert bundle.ic_memo is not None


def test_graph_facts_carry_no_raw_or_private_strings() -> None:
    encoded = json.dumps(_generate(_GRAPH_CONCLUSIONS).ic_memo.model_dump(mode="json"))
    # Conclusions are safe-fields-only; the rendering must not introduce raw text/paths/names.
    for leak in ("source_system", "entity.name", "claim_text", "/Users/", "neo4j+s://"):
        assert leak not in encoded
