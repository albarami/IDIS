"""Slice90 Task 7 — OCR text reuse (prove-before-create): OCR is indexed via `document_span`.

Prove-before-create outcome: OCR text already persists as PAGE_TEXT `DocumentSpan`s (Slice79 —
`test_slice79_ocr_span_persistence`: scanned-PDF/image OCR → `span_type == "PAGE_TEXT"`,
`locator.source` in {"ocr", "ocr_image"}, OCR text in `text_excerpt`). The span indexer indexes any
span with span_id/content_hash/text_excerpt as `document_span`, regardless of span_type/locator — so
OCR text is already indexed. No duplicate `SOURCE_TYPE_OCR_TEXT` is added (reuse-before-create).

GREEN-on-arrival (pins current reality). No production code.
"""

from __future__ import annotations

import idis.services.rag.indexing as indexing_mod
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.indexing import index_document_spans_for_deal
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID
from tests.test_slice90_calc_output_indexing import _fake_embed_batch, _RecordingVectorRepo

_OCR_SPAN_UUID = "0c000000-0c00-0c00-0c00-0c00000000ce"


def test_ocr_text_is_indexed_via_document_span_reuse() -> None:
    repo = _RecordingVectorRepo()
    # An OCR-derived span as Slice79 persists it: PAGE_TEXT, locator.source "ocr", text in excerpt.
    ocr_span = {
        "span_id": _OCR_SPAN_UUID,
        "span_type": "PAGE_TEXT",
        "content_hash": "ocr-content-hash-1",
        "text_excerpt": "OCR-extracted page text",
        "locator": {"source": "ocr", "page": 1},
    }
    summary, _ = index_document_spans_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        documents=[{"document_id": "doc-1", "spans": [ocr_span]}],
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "indexed"
    assert summary["indexed_span_count"] == 1
    upsert = repo.upserts[0]
    # OCR text is indexed AS document_span via the existing path (reuse, not a new source type).
    assert upsert["source_type"] == "document_span"
    assert upsert["source_id"] == _OCR_SPAN_UUID


def test_no_separate_ocr_source_type_is_added() -> None:
    # Reuse-before-create: OCR flows through document_span, so no duplicate OCR source type.
    assert not hasattr(indexing_mod, "SOURCE_TYPE_OCR_TEXT")


def test_span_indexing_ignores_span_type_and_locator() -> None:
    # The span indexer keys only on span_id/content_hash/text_excerpt — so OCR (PAGE_TEXT) and any
    # other text span are indexed identically as document_span.
    source = index_document_spans_for_deal.__doc__ or ""
    assert "span" in source.lower()
    # No span_type/locator gate in the indexing function (would otherwise exclude OCR spans).
    import inspect

    body = inspect.getsource(index_document_spans_for_deal)
    assert "span_type" not in body
    assert "locator" not in body
