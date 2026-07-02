"""Slice91 Task 2 — RAG runtime proof as a summary signal, distinct from pgvector connectivity.

Acceptance 2: "RAG runtime proof is separate from pgvector connectivity." Connectivity
(``pgvector_health_status`` / strict readiness ``_rag_foundation_layer``) says the vector store is
reachable; the runtime proof says probe retrieval actually ran in THIS run and whether it returned
matches. Per DEC-D the proof is a deterministic summary/readiness signal only — derived solely
from the sanitized ``rag_retrieval`` outcome, never from health checks — and adds no strict gate
(``RAG_*_BLOCKED`` behavior unchanged, pinned by the Task 1 characterization).

Shape (additive, safe fields only):
  ``{"retrieval_ran": bool, "retrieval_proved": bool, "match_count": int}``
  - ``retrieval_ran``: probes executed (status ``probed`` or ``failed``)
  - ``retrieval_proved``: executed AND returned matches (status ``probed``)

Surfaces: ``evidence_index.rag_evidence.runtime_proof`` and ``run_summary.rag_runtime_proof``.

Injected fakes only — no real OpenAI, no database, no migration.
"""

from __future__ import annotations

import json
from pathlib import Path

from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import (
    ProductBundleExporter,
    _rag_package,
    _safe_rag_retrieval,
)
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, SPAN_ID, TENANT_ID

_PROOF_KEYS = {"retrieval_ran", "retrieval_proved", "match_count"}


def _retrieval(status: str, match_count: int) -> dict[str, object]:
    matches = [
        {"source_type": "document_span", "source_id": f"span-uuid-{i}", "score": 0.9}
        for i in range(match_count)
    ]
    return {
        "status": status,
        "retrieval_mode": "probe",
        "probe_count": 1,
        "match_count": match_count,
        "matches": matches,
    }


# --- Proof derivation from the retrieval outcome ---


def test_rag_package_carries_runtime_proof_for_probed_retrieval() -> None:
    package = _rag_package(
        {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 2},
            "rag_retrieval": _retrieval("probed", 2),
        }
    )
    assert package["runtime_proof"] == {
        "retrieval_ran": True,
        "retrieval_proved": True,
        "match_count": 2,
    }


def test_runtime_proof_failed_retrieval_ran_without_matches() -> None:
    # "failed" = probes executed but returned no matches: ran, but not proved.
    package = _rag_package(
        {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 2},
            "rag_retrieval": _retrieval("failed", 0),
        }
    )
    assert package["runtime_proof"] == {
        "retrieval_ran": True,
        "retrieval_proved": False,
        "match_count": 0,
    }


def test_runtime_proof_skipped_retrieval_and_empty_package() -> None:
    skipped = _rag_package(
        {
            "rag_status": "skipped",
            "rag_indexing": {"status": "skipped", "indexed_span_count": 0},
            "rag_retrieval": _retrieval("skipped", 0),
        }
    )
    assert skipped["runtime_proof"] == {
        "retrieval_ran": False,
        "retrieval_proved": False,
        "match_count": 0,
    }
    # Absent RAG evidence degrades to the same not-ran proof (schema-consistent empty package).
    assert _rag_package(None)["runtime_proof"] == {
        "retrieval_ran": False,
        "retrieval_proved": False,
        "match_count": 0,
    }


# --- Review fix (Task 9): export sanitizer hardened like the consumer converter ---


def test_safe_rag_retrieval_skips_non_string_ids_and_bad_scores() -> None:
    # The package acceptance path must never crash on a malformed score, emit
    # NaN/Infinity into bundle JSON, or repr-stringify non-string IDs.
    safe = _safe_rag_retrieval(
        {
            "status": "probed",
            "retrieval_mode": "probe",
            "probe_count": 1,
            "match_count": 8,
            "matches": [
                {"source_type": "document_span", "source_id": "keep-1", "score": 0.9},
                {"source_type": "document_span", "source_id": "bad-str", "score": "not-a-number"},
                {"source_type": "document_span", "source_id": "bad-nan", "score": float("nan")},
                {"source_type": "document_span", "source_id": "bad-inf", "score": float("inf")},
                {"source_type": "document_span", "source_id": "bad-list", "score": [0.9]},
                {"source_type": {"text_excerpt": "PRIVATE"}, "source_id": "bad-dict", "score": 0.9},
                {"source_type": "document_span", "source_id": ["oops"], "score": 0.9},
                {"source_type": "document_span", "source_id": "keep-2", "score": "0.5"},
            ],
        }
    )
    kept = {(m["source_type"], m["source_id"], m["score"]) for m in safe["matches"]}
    assert kept == {("document_span", "keep-1", 0.9), ("document_span", "keep-2", 0.5)}
    encoded = json.dumps(safe)
    assert "PRIVATE" not in encoded
    assert "NaN" not in encoded
    assert "Infinity" not in encoded


# --- Acceptance 2: proof is separate from pgvector connectivity ---


def test_runtime_proof_is_independent_of_pgvector_connectivity() -> None:
    # Identical retrieval outcome + different connectivity values -> identical proof.
    proofs = []
    for health in ("healthy", "failed", None):
        evidence: dict[str, object] = {
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 1},
            "rag_retrieval": _retrieval("probed", 1),
        }
        if health is not None:
            evidence["pgvector_health_status"] = health
        proofs.append(_rag_package(evidence)["runtime_proof"])
    assert proofs[0] == proofs[1] == proofs[2]

    # Healthy connectivity with skipped retrieval -> NOT proof (connectivity != runtime proof).
    healthy_but_skipped = _rag_package(
        {
            "rag_status": "skipped",
            "rag_indexing": {"status": "skipped", "indexed_span_count": 0},
            "rag_retrieval": _retrieval("skipped", 0),
            "pgvector_health_status": "healthy",
        }
    )
    assert healthy_but_skipped["runtime_proof"]["retrieval_ran"] is False
    assert healthy_but_skipped["runtime_proof"]["retrieval_proved"] is False


# --- Export surfaces: evidence_index + run_summary ---


def test_bundle_exports_rag_runtime_proof_in_evidence_index_and_run_summary(
    tmp_path: Path,
) -> None:
    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice91",
    )

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        rag_evidence={
            "rag_status": "available",
            "rag_indexing": {"status": "indexed", "indexed_span_count": 2},
            "rag_retrieval": {
                "status": "probed",
                "retrieval_mode": "probe",
                "probe_count": 1,
                "match_count": 1,
                "matches": [{"source_type": "document_span", "source_id": SPAN_ID, "score": 0.99}],
            },
            "pgvector_health_status": "healthy",
        },
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

    expected_proof = {"retrieval_ran": True, "retrieval_proved": True, "match_count": 1}
    assert evidence_index["rag_evidence"]["runtime_proof"] == expected_proof
    assert run_summary["rag_runtime_proof"] == expected_proof
    # The proof carries exactly the safe fields — no health/text/vector keys leak into it.
    assert set(evidence_index["rag_evidence"]["runtime_proof"]) == _PROOF_KEYS
    assert set(run_summary["rag_runtime_proof"]) == _PROOF_KEYS
    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    assert "pgvector_health_status" not in encoded
