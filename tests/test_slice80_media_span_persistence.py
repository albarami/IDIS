"""Slice80 Task 4 — durable media TIMECODE span persistence (confirmation, NARROW).

Confirmation coverage (existing behavior; no production change). Drives media bytes
through the ingestion persistence seam with a deterministic mocked ``MediaAdapter`` and
a recording documents repository (the same seam exercised in Task 1) to prove media
transcript/timecode spans persist durably via ``repo.create_document_span``:

  1. MP4/media success persists TIMECODE spans (locator start_ms/end_ms/source, stable
     content_hash provenance, valid span_id).
  2. parser doc_type "MEDIA" persists as DocumentType VIDEO.
  3. persisted metadata is safe and carries media diagnostics (parser_mode media_stt,
     media_transcription_performed, media_segment_count) with no transcript/path/secret.
  4. transcript text lives only in span text_excerpt, never in result/metadata/audit.
  5. spans persist even though NARROW scope leaves the document triage UNKNOWN /
     non-eligible for extraction/claims (asserted + documented, not changed here).
  6. multi-segment: every start/end locator survives and ordering/provenance is
     deterministic across ingestion runs.

No real ffmpeg/faster-whisper is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from idis.audit.sink import InMemoryAuditSink
from idis.models.document import ParseStatus
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.parsers.media import MediaConfig, MediaSegmentText
from idis.services.ingestion import IngestionContext, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

_MP4_STUB = b"\x00\x00\x00\x18ftypmp42"
_TENANT = UUID("11111111-1111-4111-8111-111111111111")
_DEAL = UUID("33333333-3333-4333-8333-333333333333")


class _StubMediaAdapter:
    """Deterministic media adapter returning configured segments (no real STT)."""

    def __init__(self, segments: list[MediaSegmentText]) -> None:
        self._segments = segments

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        return list(self._segments)


class _RecordingDocumentsRepo:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []

    def create_artifact(self, **kwargs: Any) -> None:
        self.artifacts.append(kwargs)

    def create_document(self, **kwargs: Any) -> None:
        self.documents.append(kwargs)

    def create_document_span(self, **kwargs: Any) -> None:
        self.spans.append(kwargs)


class _RecordingRepoIngestionService(IngestionService):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.recording_repo = _RecordingDocumentsRepo()

    def _documents_repo(self, tenant_id: UUID, *, db_conn: Any | None = None) -> Any:
        return self.recording_repo


def _ingest_media(
    root: Path,
    segments: list[MediaSegmentText],
    *,
    filename: str = "board-call.mp4",
) -> tuple[_RecordingRepoIngestionService, InMemoryAuditSink, Any]:
    root.mkdir(parents=True, exist_ok=True)
    store = FilesystemObjectStore(base_dir=root / "objects")
    audit = InMemoryAuditSink()
    service = _RecordingRepoIngestionService(
        compliant_store=ComplianceEnforcedStore(inner_store=store),
        audit_sink=audit,
        media_config=MediaConfig(enabled=True, adapter=_StubMediaAdapter(segments)),
    )
    ctx = IngestionContext(tenant_id=_TENANT, actor_id="tester", request_id="req-slice80-t4")
    result = service.ingest_bytes(
        ctx=ctx,
        deal_id=_DEAL,
        filename=filename,
        media_type="video/mp4",
        data=_MP4_STUB,
    )
    return service, audit, result


# --- 1. media success persists TIMECODE spans via repo.create_document_span ---


def test_media_success_persists_timecode_spans_via_repo(tmp_path: Path) -> None:
    service, _audit, result = _ingest_media(
        tmp_path,
        [MediaSegmentText(start_ms=1000, end_ms=2500, text="ARR grew 40 percent")],
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    spans = service.recording_repo.spans
    assert len(spans) == 1
    row = spans[0]
    assert row["span_type"] == "TIMECODE"
    assert row["locator"]["start_ms"] == 1000
    assert row["locator"]["end_ms"] == 2500
    assert row["locator"]["source"] == "media_transcript"
    assert row["document_id"] == str(result.document_id)
    assert row["deal_id"] == str(_DEAL)
    # span_id is a valid UUID; content_hash provides stable, reproducible provenance.
    assert UUID(row["span_id"])
    assert row["content_hash"]


# --- 2. parser doc_type MEDIA persists as VIDEO ---


def test_media_doc_type_persists_as_video(tmp_path: Path) -> None:
    service, _audit, _result = _ingest_media(
        tmp_path,
        [MediaSegmentText(1000, 2500, "revenue grew")],
    )
    doc = service.recording_repo.documents[0]
    assert doc["doc_type"] == "VIDEO"


# --- 3. persisted metadata is safe and includes media diagnostics ---


def test_media_metadata_is_safe_and_includes_diagnostics(tmp_path: Path) -> None:
    service, _audit, _result = _ingest_media(
        tmp_path,
        [
            MediaSegmentText(1000, 2500, "SECRET_TRANSCRIPT_TEXT one"),
            MediaSegmentText(2500, 4000, "SECRET_TRANSCRIPT_TEXT two"),
        ],
    )
    metadata = service.recording_repo.documents[0]["metadata"]
    assert metadata["parser_mode"] == "media_stt"
    assert metadata["media_transcription_performed"] is True
    assert metadata["media_segment_count"] == 2

    blob = json.dumps(metadata, default=str)
    for marker in (
        "SECRET_TRANSCRIPT_TEXT",  # transcript text
        "board-call.mp4",  # raw filename
        "C:\\",  # windows path
        "/var/",  # unix path
        "whisper.bin",  # model file
        "sk-",  # secret token
    ):
        assert marker not in blob


# --- 4. transcript text only in span text_excerpt, never elsewhere ---


def test_transcript_text_only_in_span_text_excerpt(tmp_path: Path) -> None:
    marker = "CONFIDENTIAL_MEDIA_TRANSCRIPT_MARKER"
    service, audit, result = _ingest_media(tmp_path, [MediaSegmentText(1000, 2500, marker)])

    spans = service.recording_repo.spans
    assert any(marker in (row["text_excerpt"] or "") for row in spans)

    assert marker not in json.dumps(result.to_dict(), default=str)
    assert marker not in json.dumps(service.recording_repo.documents[0]["metadata"], default=str)
    assert marker not in json.dumps(audit.events, default=str)


# --- 5. spans persist even though NARROW triage is non-eligible (documented, unchanged) ---


def test_media_spans_persist_but_triage_is_non_eligible_narrow(tmp_path: Path) -> None:
    # NARROW asymmetry: media TIMECODE spans persist durably, but the document triages as
    # UNKNOWN/unknown_format -> it is NOT extraction/claims-eligible. Task 4 confirms this
    # reality; it does NOT change triage/extraction behavior.
    service, _audit, _result = _ingest_media(tmp_path, [MediaSegmentText(1000, 2500, "data")])

    spans = service.recording_repo.spans
    assert spans and all(row["span_type"] == "TIMECODE" for row in spans)

    metadata = service.recording_repo.documents[0]["metadata"]
    assert metadata["parser_triage_status"] == DocumentTriageStatus.UNKNOWN.value
    assert metadata["parser_support_status"] == DocumentSupportStatus.UNKNOWN.value
    assert metadata["parser_reason_codes"] == ["unknown_format"]
    assert metadata["parser_requires_ocr"] is False
    assert metadata["parser_requires_conversion"] is False


# --- 6. multi-segment: locators survive + deterministic ordering/provenance ---


def test_multi_segment_locators_survive_and_ordering_is_deterministic(tmp_path: Path) -> None:
    segments = [
        MediaSegmentText(1000, 2500, "first segment"),
        MediaSegmentText(2500, 4000, "second segment"),
        MediaSegmentText(4000, 6000, "third segment"),
    ]
    service_a, _a, _ra = _ingest_media(tmp_path / "a", segments)
    service_b, _b, _rb = _ingest_media(tmp_path / "b", segments)
    spans_a = service_a.recording_repo.spans
    spans_b = service_b.recording_repo.spans

    assert len(spans_a) == 3
    # Every input (start_ms, end_ms) locator survives persistence with the media source.
    persisted = {(row["locator"]["start_ms"], row["locator"]["end_ms"]) for row in spans_a}
    assert persisted == {(1000, 2500), (2500, 4000), (4000, 6000)}
    assert all(row["locator"]["source"] == "media_transcript" for row in spans_a)

    # Ordering and content-hash provenance are deterministic across ingestion runs.
    order_a = [(row["locator"]["start_ms"], row["locator"]["end_ms"]) for row in spans_a]
    order_b = [(row["locator"]["start_ms"], row["locator"]["end_ms"]) for row in spans_b]
    assert order_a == order_b
    assert [row["content_hash"] for row in spans_a] == [row["content_hash"] for row in spans_b]
