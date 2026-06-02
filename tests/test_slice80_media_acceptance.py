"""Slice80 acceptance proof — generated media fixture transcribes; real_example media
files are transcribed or explicitly blocked with safe reasons.

Maps to the Slice80 master-plan acceptance:
  * "Generated media fixture produces transcript/timecode spans"
  * "Private real_example media files are transcribed or explicitly blocked with safe reasons"

Confirmation coverage (existing behavior; no production change). Uses a deterministic
mocked ``MediaAdapter`` for the fixture proof and the ``run_real_example_gate`` safe
aggregate (PARSE_SUPPORTED) for the transcribe/block reason-count proof. No real
faster-whisper is ever loaded; no real ffmpeg/ffprobe is required except the one
inject-seam test, which is skip-guarded on their presence.

Semantic guard: INVENTORY_ONLY is inventory-only — it never classifies media, so it is
NOT the acceptance reason-count path. The reason-count acceptance proof lives on the
PARSE_SUPPORTED safe aggregates below; INVENTORY_ONLY appears here only as a guard.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from idis.evaluation.real_example_gate import GateMode, run_real_example_gate
from idis.evaluation.real_example_gate_runtime import ParseAttempt
from idis.parsers.media import MediaConfig, MediaSegmentText, parse_media

# Minimal generated MP4 fixture (ftyp box header); the mocked adapter ignores the bytes.
_MP4_STUB = b"\x00\x00\x00\x18ftypmp42"
_FASTER_WHISPER = "faster-whisper"


class _StubMediaAdapter:
    """Deterministic media adapter returning configured segments (no real STT)."""

    def __init__(self, segments: list[MediaSegmentText]) -> None:
        self._segments = segments

    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        return list(self._segments)


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


# --- 1. Generated media fixture + deterministic adapter -> transcript/timecode spans ---


def test_generated_media_fixture_produces_timecode_spans() -> None:
    adapter = _StubMediaAdapter(
        [
            MediaSegmentText(start_ms=0, end_ms=2000, text="acceptance ARR grew 40 percent"),
            MediaSegmentText(start_ms=2000, end_ms=4200, text="runway is eighteen months"),
        ]
    )

    result = parse_media(_MP4_STUB, media_config=MediaConfig(enabled=True, adapter=adapter))

    assert result.success is True
    assert result.doc_type == "MEDIA"
    assert [span.span_type for span in result.spans] == ["TIMECODE", "TIMECODE"]
    assert all(span.locator["source"] == "media_transcript" for span in result.spans)
    assert all("start_ms" in span.locator and "end_ms" in span.locator for span in result.spans)
    assert result.metadata["media_transcription_performed"] is True
    assert result.metadata["media_segment_count"] == 2


# --- 2. PARSE_SUPPORTED + media disabled -> explicitly blocked with safe reason ---


def test_parse_supported_media_disabled_blocks_with_conversion_required(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "secret-board-call.mp4", _MP4_STUB)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=False,
    )

    assert summary["counts_by_reason_code"].get("conversion_required", 0) >= 1
    assert summary["counts_by_status"].get("deferred", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-call", "secret"])


# --- 3. PARSE_SUPPORTED + media enabled but unavailable -> safe media reason ---


def test_parse_supported_media_enabled_unavailable_blocks_with_media_reason(
    tmp_path: Path,
) -> None:
    root = _data_room(tmp_path, "secret-board-call.mp4", _MP4_STUB)

    # Enabled but no model/binaries provisioned -> _media_adapter_attemptable is False
    # (deterministic regardless of local ffmpeg) -> media_required / unavailable.
    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=True,
        media_adapter=_FASTER_WHISPER,
    )

    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) >= 1
    assert summary["counts_by_parser_outcome"].get("media_required", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-call", "secret"])


# --- 4. PARSE_SUPPORTED + media enabled + injected parse success -> blocked count zero ---


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; media is not attemptable so the inject seam is unreached",
)
def test_parse_supported_media_enabled_transcribed_drives_blocked_count_to_zero(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"fake-model")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    root = _data_room(tmp_path, "secret-board-call.mp4", _MP4_STUB)

    # The injected parse seam stands in for a successful transcription (no real STT).
    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=True,
        media_adapter=_FASTER_WHISPER,
        media_model_path=str(model_dir),
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) == 0
    assert summary["counts_by_reason_code"].get("conversion_required", 0) == 0
    assert summary["counts_by_status"].get("parsed", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["secret-board-call", "secret"])


# --- 5. INVENTORY_ONLY guard (NOT the acceptance reason-count path) ---


def test_inventory_only_is_a_guard_not_the_acceptance_path(tmp_path: Path) -> None:
    # INVENTORY_ONLY only inventories; it never classifies media. The acceptance
    # reason-count proof therefore belongs on the PARSE_SUPPORTED safe aggregates above,
    # not here. This test guards that INVENTORY_ONLY does not emit media classifications.
    root = _data_room(tmp_path, "secret-board-call.mp4", _MP4_STUB)

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
    )

    assert summary["mode"] == "inventory_only"
    assert summary["counts_by_reason_code"].get("inventory_only", 0) >= 1
    assert summary["counts_by_parser_outcome"].get("not_attempted", 0) >= 1
    assert summary["counts_by_reason_code"].get("conversion_required", 0) == 0
    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) == 0


# --- 6. Leak guard: no filename/path/model/secret in the safe aggregate ---


def test_safe_aggregate_has_no_filename_path_model_or_secret_leak(tmp_path: Path) -> None:
    secret_model = tmp_path / "SECRET_MODEL_DIR_sk-LEAK123"
    root = _data_room(tmp_path, "CONFIDENTIAL-DEAL-RECORDING.mp4", _MP4_STUB)

    # Media enabled with an invalid (non-existent) model path -> not attemptable -> blocked.
    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        media_enabled=True,
        media_adapter=_FASTER_WHISPER,
        media_model_path=str(secret_model),
    )

    assert summary["counts_by_reason_code"].get("media_transcription_unavailable", 0) >= 1
    assert summary["safe_summary"] is True

    blob = json.dumps(summary, default=str)
    assert str(root) not in blob
    assert str(secret_model) not in blob
    assert "CONFIDENTIAL-DEAL-RECORDING" not in blob
    assert "SECRET_MODEL_DIR" not in blob
    assert "sk-LEAK123" not in blob

    # Only safe aggregate fields are present — no per-file records / paths / outputs.
    assert set(summary.keys()) == {
        "gate",
        "safe_summary",
        "mode",
        "total_files",
        "processed_files",
        "ledger_entry_count",
        "counts_by_extension",
        "counts_by_status",
        "counts_by_parser_outcome",
        "counts_by_reason_code",
    }
