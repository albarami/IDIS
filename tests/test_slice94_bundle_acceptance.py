"""Slice94 Task 3 — end-to-end Full VC Bundle acceptance proof (G2).

Exports a full investor bundle on BOTH routings (INVEST and DECLINE, injected fakes) and proves
the master-plan acceptance. The material-assertion proof walks the FULL generated bundle model —
the screening snapshot, the IC memo (every section, including ``recommendation`` and
``truth_dashboard_summary``), the truth dashboard, the QA brief, AND the ``decline_letter`` when
a DECLINE routing produces one — so "every material assertion" is proven for every ``is_factual``
fact in the deliverables, not merely the JSON diligence section artifacts:

  1. Every material assertion (each ``is_factual`` deliverable fact, across every section of
     every deliverable, decline_letter included) links to safe evidence IDs (claim/calc refs),
     and each fact's OWN refs resolve through the exported ``evidence_index`` (per-fact, not
     merely pooled); every ref used anywhere (facts + truth rows + QA items) resolves too.
  2. The run-level provenance appendix is present and cross-referenced (provenance IDs).
  3. Financial diligence carries reproducible calc lineage (sha256 reproducibility hash +
     resolvable input-claim lineage), and the bundle is deterministically reproducible.

Injected fakes only — no real Anthropic; filesystem object store; no database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from idis.analysis.scoring.models import RoutingAction
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.models.deliverables import (
    DeliverableFact,
    DeliverablesBundle,
    DeliverableSection,
)
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import _TIMESTAMP, _make_bundle, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
)
from tests.test_slice87_financial_tables import _HASH_A, _context_with_eligible_calc
from tests.test_slice94_provenance_appendix import _BLOCKS, _safe_provenance

# The object store requires UUID-shaped storage ids (distinct from the context's ids).
_TENANT = TENANT_ID
_DEAL = DEAL_ID
_RUN = RUN_ID


def _export_full_bundle(
    tmp_path: Path, routing: RoutingAction = RoutingAction.INVEST
) -> tuple[DeliverablesBundle, FilesystemObjectStore]:
    ctx = _context_with_eligible_calc()
    scorecard = _make_scorecard(routing=routing)
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=ctx,
        bundle=_make_bundle(),
        scorecard=scorecard,
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-accept",
    )
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    ).export_bundle(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        run_id=_RUN,
        bundle=bundle,
        analysis_context=ctx,
        scorecard=scorecard,
        export_timestamp=_TIMESTAMP,
        run_provenance=_safe_provenance(),
    )
    return bundle, object_store


def _read(store: FilesystemObjectStore, filename: str) -> Any:
    obj = store.get(tenant_id=_TENANT, key=f"runs/{_RUN}/product_bundle/{filename}")
    return json.loads(obj.body.decode("utf-8"))


def _iter_sections(deliverable: Any) -> Iterator[DeliverableSection]:
    """Yield every DeliverableSection on a deliverable model, incl. optional + list fields."""
    for name in type(deliverable).model_fields:
        value = getattr(deliverable, name)
        if isinstance(value, DeliverableSection):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, DeliverableSection):
                    yield item


def _all_facts(bundle: DeliverablesBundle) -> list[DeliverableFact]:
    """Every fact across every section of every material deliverable in the generated bundle."""
    deliverables: list[Any] = [
        bundle.screening_snapshot,
        bundle.ic_memo,
        bundle.truth_dashboard,
        bundle.qa_brief,
    ]
    if bundle.decline_letter is not None:
        deliverables.append(bundle.decline_letter)
    facts: list[DeliverableFact] = []
    for deliverable in deliverables:
        for section in _iter_sections(deliverable):
            facts.extend(section.facts)
    return facts


def _all_refs(bundle: DeliverablesBundle) -> set[str]:
    """Every claim/calc ref used anywhere: section facts, truth rows, QA items."""
    refs: set[str] = set()
    for fact in _all_facts(bundle):
        refs |= set(fact.claim_refs) | set(fact.calc_refs)
    for row in bundle.truth_dashboard.rows:
        refs |= set(row.claim_refs) | set(row.calc_refs)
    for item in bundle.qa_brief.items:
        refs |= set(item.claim_refs) | set(item.calc_refs)
    return refs


def _resolvable_ids(store: FilesystemObjectStore) -> set[str]:
    evidence_index = _read(store, "evidence_index.json")
    resolvable = {entry["ref_id"] for entry in evidence_index["entries"]}
    resolvable |= {calc["calc_id"] for calc in evidence_index["calc_entries"]}
    return resolvable


def _assert_facts_resolve(material: list[DeliverableFact], resolvable: set[str]) -> None:
    """Per-fact (not just pooled): each material fact is referenced AND its OWN refs resolve."""
    for fact in material:
        fact_refs = set(fact.claim_refs) | set(fact.calc_refs)
        assert fact_refs, f"unreferenced material fact: {fact.text!r}"
        assert fact_refs & resolvable, (
            f"material fact not grounded in evidence_index: {fact.text!r}"
        )
        unresolved = fact_refs - resolvable
        assert not unresolved, (
            f"material fact carries unresolved refs {sorted(unresolved)}: {fact.text!r}"
        )


# --- (1) every material assertion links to safe evidence IDs that resolve ---


def test_every_material_assertion_links_to_resolvable_evidence(tmp_path: Path) -> None:
    bundle, store = _export_full_bundle(tmp_path)
    resolvable = _resolvable_ids(store)

    # (a) Walk the FULL generated bundle — screening snapshot, every IC-memo section (incl.
    # recommendation + truth_dashboard_summary), truth dashboard, QA brief — not just the JSON
    # diligence artifacts, so "every material assertion" is proven for every is_factual fact.
    # Per-fact (not merely pooled): each fact is referenced AND its own refs resolve.
    material = [fact for fact in _all_facts(bundle) if fact.is_factual]
    assert material, "expected real material content across the generated bundle"
    _assert_facts_resolve(material, resolvable)

    # Coverage guard: the previously-omitted sections are genuinely in the walk and carry
    # material content, so this proof cannot silently shrink back to the old 6-section subset.
    walked_ids = {id(section) for section in _iter_sections(bundle.ic_memo)}
    assert id(bundle.ic_memo.recommendation) in walked_ids
    assert id(bundle.ic_memo.truth_dashboard_summary) in walked_ids
    assert any(f.is_factual for f in bundle.ic_memo.recommendation.facts), (
        "IC-memo recommendation must contribute material facts"
    )
    assert any(f.is_factual for s in _iter_sections(bundle.screening_snapshot) for f in s.facts), (
        "screening snapshot must contribute material facts"
    )

    # (b) EVERY ref used anywhere (section facts + truth rows + QA items) resolves through the
    # exported evidence_index.
    all_refs = _all_refs(bundle)
    assert all_refs, "expected at least one evidence reference in the bundle"
    dangling = all_refs - resolvable
    assert not dangling, f"refs do not resolve through evidence_index: {sorted(dangling)}"


# --- (1b) DECLINE-routed bundle: decline_letter material facts are covered too ---


def test_decline_routed_bundle_material_assertions_resolve(tmp_path: Path) -> None:
    bundle, store = _export_full_bundle(tmp_path, routing=RoutingAction.DECLINE)
    assert bundle.decline_letter is not None, "DECLINE routing must emit a decline letter"
    resolvable = _resolvable_ids(store)

    # Coverage guard (M2): the decline letter's factual facts are genuinely walked, so the proof
    # covers EVERY deliverable — including decline_letter — not only the INVEST four.
    decline_material = [
        f for s in _iter_sections(bundle.decline_letter) for f in s.facts if f.is_factual
    ]
    assert decline_material, "decline letter must contribute material facts to the proof"
    walked_ids = {id(f) for f in _all_facts(bundle)}
    assert all(id(f) in walked_ids for f in decline_material), (
        "decline-letter material facts must be included in the walked bundle"
    )

    # Per-fact resolution over the FULL decline bundle (incl. decline_letter), plus pooled refs.
    material = [fact for fact in _all_facts(bundle) if fact.is_factual]
    _assert_facts_resolve(material, resolvable)
    dangling = _all_refs(bundle) - resolvable
    assert not dangling, f"refs do not resolve through evidence_index: {sorted(dangling)}"


# --- (2) the run-level provenance appendix is present + cross-referenced ---


def test_provenance_appendix_present_and_cross_referenced(tmp_path: Path) -> None:
    _bundle, store = _export_full_bundle(tmp_path)
    appendix = _read(store, "provenance_appendix.json")
    assert appendix["status"] == "present"
    assert sorted(appendix["blocks_present"]) == sorted(_BLOCKS)
    # Cross-referenced from run_summary + evidence_index (the source/provenance appendix).
    assert _read(store, "run_summary.json")["provenance_status"] == "present"
    evidence_index = _read(store, "evidence_index.json")
    assert set(evidence_index["provenance_appendix"]["provenance"]).issuperset(set(_BLOCKS))
    # Safe metadata only — no prompt bodies / model output / keys leak.
    encoded = json.dumps(appendix)
    for forbidden in ("SECRET", "sk-", "prompt_body", "response_text", "raw_"):
        assert forbidden not in encoded


# --- (3) financial diligence has reproducible, resolvable calc lineage ---


def test_financial_diligence_has_reproducible_calc_lineage(tmp_path: Path) -> None:
    _bundle, store = _export_full_bundle(tmp_path)
    financial = _read(store, "financial_diligence.json")
    rows = financial["financial_table"]["rows"]
    assert rows, "expected at least one eligible financial-table row"

    resolvable = _resolvable_ids(store)
    for row in rows:
        # SHA256 reproducibility hash + deterministic formula/version identity.
        assert len(row["reproducibility_hash"]) == 64
        assert int(row["reproducibility_hash"], 16) >= 0  # valid hex
        assert row["formula_hash"] and row["code_version"]
        # Claim lineage is present AND resolves through the evidence index.
        assert row["input_claim_ids"]
        assert set(row["input_claim_ids"]).issubset(resolvable)


def test_bundle_financials_are_deterministically_reproducible(tmp_path: Path) -> None:
    first = _read(_export_full_bundle(tmp_path / "a")[1], "financial_diligence.json")
    second = _read(_export_full_bundle(tmp_path / "b")[1], "financial_diligence.json")
    # Byte-identical financial diligence across independent exports (reproducible).
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert [row["reproducibility_hash"] for row in first["financial_table"]["rows"]] == [_HASH_A]
