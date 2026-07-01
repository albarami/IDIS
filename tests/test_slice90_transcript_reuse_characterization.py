"""Slice90 Task 8 — transcript reuse (prove-before-create): transcripts index via `document_span`.

Prove-before-create outcome: media/STT transcripts already persist as TIMECODE `DocumentSpan`s
(Slice80 — `test_slice80_media_span_persistence`: MP4/media success → `span_type == "TIMECODE"`,
`locator.source == "media_transcript"`, valid UUID `span_id`, `content_hash`, transcript text in
`text_excerpt`). The span indexer keys only on span_id/content_hash/text_excerpt (ignores
span_type/locator), so transcript spans are already indexed as `document_span`. No duplicate
`SOURCE_TYPE_TRANSCRIPT` is added (reuse-before-create).

GREEN-on-arrival (pins current reality). No production code.
"""

from __future__ import annotations

import idis.services.rag.indexing as indexing_mod
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.indexing import index_document_spans_for_deal
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID
from tests.test_slice90_calc_output_indexing import _fake_embed_batch, _RecordingVectorRepo

_TRANSCRIPT_SPAN_UUID = "0c000000-0c00-0c00-0c00-0c0000000a17"


def test_transcript_text_is_indexed_via_document_span_reuse() -> None:
    repo = _RecordingVectorRepo()
    # A transcript span as Slice80 persists it: TIMECODE, locator.source "media_transcript", text.
    transcript_span = {
        "span_id": _TRANSCRIPT_SPAN_UUID,
        "span_type": "TIMECODE",
        "content_hash": "transcript-content-hash-1",
        "text_excerpt": "Transcribed media segment text",
        "locator": {"source": "media_transcript", "start_ms": 1000, "end_ms": 2500},
    }
    summary, _ = index_document_spans_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        documents=[{"document_id": "doc-1", "spans": [transcript_span]}],
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "indexed"
    assert summary["indexed_span_count"] == 1
    upsert = repo.upserts[0]
    # Transcript text is indexed AS document_span via the existing path (reuse, not a new type).
    assert upsert["source_type"] == "document_span"
    assert upsert["source_id"] == _TRANSCRIPT_SPAN_UUID


def test_no_separate_transcript_source_type_is_added() -> None:
    # Reuse-before-create: transcripts flow through document_span, so no duplicate source type.
    assert not hasattr(indexing_mod, "SOURCE_TYPE_TRANSCRIPT")
