"""Slice94 Task 1 — characterization: pin the already-built Full VC Bundle truth.

The exported product bundle already emits the investor-package artifacts and the *evidence*
side of the source/provenance appendix (evidence_index + audit appendices + calc/graph/rag/
layer2/enrichment/vep evidence); No-Free-Facts and financial reproducibility are enforced at
generation. The one genuine gap (G1) — the *run-level provenance* appendix — is pinned ABSENT
here and flips in Task 2.

GREEN-on-arrival (pins current truth). Any RED → STOP + report. No production changes.
Injected fakes only — no real Anthropic; filesystem object store; no database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import _make_context, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
    _make_deliverables_bundle,
)

_TIMESTAMP = "2026-01-01T00:00:00Z"

_INVESTOR_PACKAGE_ARTIFACTS = {
    "executive_summary",
    "commercial_diligence",
    "financial_diligence",
    "risk_register",
    "ic_memo",
    "truth_dashboard",
    "qa_brief",
    "evidence_index",
    "run_summary",
    "screening_snapshot",
}


def _export(tmp_path: Path) -> tuple[dict[str, Any], FilesystemObjectStore]:
    from idis.deliverables.product_bundle import ProductBundleExporter

    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )
    manifest = object_store.get(
        tenant_id=TENANT_ID, key=f"runs/{RUN_ID}/product_bundle/manifest.json"
    )
    return json.loads(manifest.body.decode("utf-8")), object_store


def _read_artifact(store: FilesystemObjectStore, filename: str) -> Any:
    obj = store.get(tenant_id=TENANT_ID, key=f"runs/{RUN_ID}/product_bundle/{filename}")
    return json.loads(obj.body.decode("utf-8"))


# --- Already built: investor-package artifacts emit, durable + SHA256-addressed ---


def test_bundle_emits_investor_package_artifacts(tmp_path: Path) -> None:
    manifest, _store = _export(tmp_path)
    types = {artifact["type"] for artifact in manifest["artifacts"]}
    assert _INVESTOR_PACKAGE_ARTIFACTS.issubset(types)
    for artifact in manifest["artifacts"]:
        assert artifact["sha256"]
        assert artifact["size_bytes"] > 0


# --- Already built: the EVIDENCE side of the source/provenance appendix ships ---


def test_evidence_index_carries_evidence_side(tmp_path: Path) -> None:
    _manifest, store = _export(tmp_path)
    evidence_index = _read_artifact(store, "evidence_index.json")
    assert isinstance(evidence_index.get("entries"), list)  # audit-appendix consolidation
    for key in (
        "graph_evidence",
        "rag_evidence",
        "layer2_evidence",
        "enrichment_evidence",
        "vep_evidence",
    ):
        assert key in evidence_index
    # Entries carry safe ref ids/types only (never claim text).
    for entry in evidence_index["entries"]:
        assert set(entry).issuperset({"ref_id", "ref_type"})


# --- Already built: NFF gates every exported factual assertion at generation ---


def test_nff_enforced_on_deliverables_at_generation() -> None:
    generator_src = Path("src/idis/deliverables/generator.py").read_text(encoding="utf-8")
    assert "_validate_nff" in generator_src
    assert "validate_deliverable_no_free_facts" in generator_src
    from idis.models.deliverables import DeliverableFact

    assert "claim_refs" in DeliverableFact.model_fields
    assert "calc_refs" in DeliverableFact.model_fields


# --- Already built: financial reproducibility fields are present (assumptions reproducible) ---


def test_financial_table_row_carries_reproducibility_fields() -> None:
    from idis.models.deliverables import FinancialTableRow

    fields = FinancialTableRow.model_fields
    for name in (
        "reproducibility_hash",
        "formula_hash",
        "code_version",
        "input_claim_ids",
        "calc_sanad_id",
    ):
        assert name in fields


# --- G1 CLOSED (Task 2): the run-level provenance appendix mechanism now exists ---


def test_run_level_provenance_appendix_now_available(tmp_path: Path) -> None:
    # Without run provenance the bundle stays unchanged (conditional emission)...
    manifest, _store = _export(tmp_path)
    types = {artifact["type"] for artifact in manifest["artifacts"]}
    assert "provenance_appendix" not in types
    # ...but the export layer now builds + registers a safe run-level provenance appendix
    # (was absent at Task 1). The safe emit + cross-reference behavior is exercised in
    # tests/test_slice94_provenance_appendix.py.
    product_bundle_src = Path("src/idis/deliverables/product_bundle.py").read_text(encoding="utf-8")
    assert "_provenance_appendix" in product_bundle_src
    catalog_src = Path("src/idis/deliverables/artifact_catalog.py").read_text(encoding="utf-8")
    assert "provenance_appendix" in catalog_src


# --- Task 5: readiness doc reconciled to the post-Slice94 as-built state ---


def test_readiness_doc_reconciled_post_slice94() -> None:
    doc = Path("docs/architecture/strict_full_live_readiness.md").read_text(encoding="utf-8")
    # A post-Slice94 banner reconciles the complete investor bundle + provenance appendix.
    assert "post-Slice94" in doc
    assert "provenance_appendix" in doc
    assert "evidence_index" in doc  # material refs resolve through the evidence/provenance surfaces
    assert "reproducib" in doc.lower()  # financial reproducibility
    assert "assumptions" in doc  # frozen assumptions
    # Accurate Slice94 test boundary: injected fakes, no real Anthropic, filesystem store, no DB.
    assert "injected fakes" in doc.lower()
    assert "filesystem object store" in doc.lower()
    assert "no database" in doc.lower()
    # The frozen Slice-53 census row + prior banners stay preserved verbatim.
    assert "Debate layer 2 / IC challenge | `not-implemented`" in doc
    assert "post-Slice93" in doc
    assert "post-Slice92" in doc
    assert "Indexes `document_span`" in doc
