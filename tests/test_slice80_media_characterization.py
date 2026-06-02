"""Slice80 Task 1 — characterization tests pinning CURRENT media/STT behavior.

RED-as-discovery: these tests document existing behavior so later tasks change it
deliberately. They use a deterministic mocked ``MediaAdapter`` / injected gate parse
seam — NO real ffmpeg/faster-whisper is required in normal CI. **No production code is
changed by Task 1.**

Maps to the Slice80 plan §2.4 characterization questions:
  1. Generated MP4 fixture + mocked STT adapter -> parse_media emits TIMECODE spans
     {start_ms, end_ms, source:"media_transcript"}.
  2. Ingestion persists media TIMECODE spans (doc_type VIDEO, parser_mode media_stt);
     transcript text lives only in span text_excerpt, never in metadata/result/audit.
  3. real_example PARSE_SUPPORTED safe aggregate: media disabled -> conversion_required;
     enabled-but-unavailable -> media_transcription_unavailable; enabled+transcribed
     (injected) -> media-blocked count zero. INVENTORY_ONLY only as an inventory-only guard.
  4. media_health.py exists (Task 2) and is wired into strict readiness via an
     injectable media_health_checker (Task 3).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.evaluation.real_example_gate import GateMode, run_real_example_gate
from idis.evaluation.real_example_gate_runtime import ParseAttempt
from idis.models.document import ParseStatus
from idis.parsers.media import MediaConfig, MediaSegmentText, parse_media
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.media_health import MediaHealthStatus, check_media_health
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

# Minimal generated MP4 fixture (ftyp box header); the mocked adapter ignores the bytes.
_MP4_STUB = b"\x00\x00\x00\x18ftypmp42"
_TENANT = UUID("11111111-1111-4111-8111-111111111111")
_DEAL = UUID("33333333-3333-4333-8333-333333333333")


class _StubMediaAdapter:
    """Deterministic media adapter returning configured segments (no real STT)."""

    def __init__(self, segments: list[MediaSegmentText]) -> None:
        self._segments = segments
        self.calls: list[int] = []

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        self.calls.append(len(data))
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


def _data_room(tmp_path: Path, filename: str, data: bytes) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / filename).write_bytes(data)
    return root


def _assert_safe_aggregate(summary: dict[str, Any], *, root: Path, forbidden: list[str]) -> None:
    assert summary["safe_summary"] is True
    blob = json.dumps(summary, default=str)
    assert str(root) not in blob
    for token in forbidden:
        assert token not in blob


# --- 1. Generated fixture + mocked adapter -> TIMECODE spans ---


def test_generated_media_fixture_produces_timecode_spans() -> None:
    adapter = _StubMediaAdapter(
        [
            MediaSegmentText(start_ms=1000, end_ms=2500, text="ARR grew 40 percent"),
            MediaSegmentText(start_ms=2500, end_ms=4000, text="runway is 18 months"),
        ]
    )

    result = parse_media(_MP4_STUB, media_config=MediaConfig(enabled=True, adapter=adapter))

    assert result.success is True
    assert result.doc_type == "MEDIA"
    assert [s.span_type for s in result.spans] == ["TIMECODE", "TIMECODE"]
    assert all(s.locator.get("source") == "media_transcript" for s in result.spans)
    assert all("start_ms" in s.locator and "end_ms" in s.locator for s in result.spans)
    assert result.metadata["media_transcription_performed"] is True


# --- 2. Ingestion persists TIMECODE spans as VIDEO; transcript only in span text ---


def test_media_ingestion_persists_timecode_spans_as_video(tmp_path: Path) -> None:
    confidential = "CONFIDENTIAL_MEDIA_TRANSCRIPT_MARKER"
    store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    audit = InMemoryAuditSink()
    service = _RecordingRepoIngestionService(
        compliant_store=ComplianceEnforcedStore(inner_store=store),
        audit_sink=audit,
        media_config=MediaConfig(
            enabled=True,
            adapter=_StubMediaAdapter([MediaSegmentText(1000, 2500, confidential)]),
        ),
    )
    ctx = IngestionContext(tenant_id=_TENANT, actor_id="tester", request_id="req-slice80-t1")

    result = service.ingest_bytes(
        ctx=ctx,
        deal_id=_DEAL,
        filename="board-call.mp4",
        media_type="video/mp4",
        data=_MP4_STUB,
    )

    assert result.success is True
    assert result.parse_status == ParseStatus.PARSED
    spans = service.recording_repo.spans
    assert spans, "media TIMECODE spans must be persisted via the documents repository"
    assert all(row["span_type"] == "TIMECODE" for row in spans)
    assert all(row["locator"]["source"] == "media_transcript" for row in spans)

    doc = service.recording_repo.documents[0]
    assert doc["doc_type"] == "VIDEO"  # _map_doc_type("MEDIA") -> DocumentType.VIDEO
    assert doc["metadata"]["parser_mode"] == "media_stt"

    # Transcript text lives only in span text_excerpt — never in metadata/result/audit.
    assert any(confidential in row["text_excerpt"] for row in spans)
    assert confidential not in json.dumps(doc["metadata"], default=str)
    assert confidential not in json.dumps(result.to_dict(), default=str)
    assert confidential not in json.dumps(audit.events, default=str)


# --- 3. real_example PARSE_SUPPORTED safe aggregate (deterministic) ---


def test_real_example_media_disabled_is_blocked_with_conversion_required(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "secret-board-recording.mp4", _MP4_STUB)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=False,
    )

    assert summary["counts_by_reason_code"].get("conversion_required", 0) >= 1
    assert summary["counts_by_status"].get("deferred", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-recording", "secret"])


def test_real_example_media_enabled_but_unavailable_blocks_with_media_reason(
    tmp_path: Path,
) -> None:
    root = _data_room(tmp_path, "secret-board-recording.mp4", _MP4_STUB)

    # Enabled but no model provisioned -> _media_adapter_attemptable False -> media_required.
    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=True,
        media_adapter="faster-whisper",
    )

    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-recording", "secret"])


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; media is not attemptable so the inject seam is unreached",
)
def test_real_example_media_enabled_transcribed_drives_blocked_count_to_zero(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"fake-model")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    root = _data_room(tmp_path, "secret-board-recording.mp4", _MP4_STUB)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=True,
        media_adapter="faster-whisper",
        media_model_path=str(model_dir),
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) == 0
    assert summary["counts_by_reason_code"].get("conversion_required", 0) == 0
    assert summary["counts_by_status"].get("parsed", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-recording", "secret"])


def test_inventory_only_does_not_classify_media(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "secret-board-recording.mp4", _MP4_STUB)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
    )

    assert summary["counts_by_reason_code"].get("inventory_only", 0) >= 1
    assert summary["counts_by_reason_code"].get("conversion_required", 0) == 0
    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) == 0


# --- 4. media_health.py exists (Task 2) and is wired into strict readiness (Task 3) ---


def test_media_health_module_and_strict_readiness_checker_are_wired() -> None:
    # Task 2 added the dedicated standalone media health module with a safe, off-by-default
    # result whose surface mirrors ocr_health (status/enabled/missing_dependencies/error).
    assert importlib.util.find_spec("idis.services.media_health") is not None
    disabled = check_media_health(env={})
    assert disabled.status is MediaHealthStatus.DISABLED
    assert set(disabled.model_dump()) == {
        "status",
        "enabled",
        "missing_dependencies",
        "error",
    }

    # Task 3 wired media health into strict readiness via an injectable checker, mirroring
    # the Slice79 OCR health checker.
    params = inspect.signature(build_strict_full_live_readiness_report).parameters
    assert "ocr_health_checker" in params
    assert "media_health_checker" in params
