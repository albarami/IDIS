"""Tests for the Slice 38 media transcription provisioning decision."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISION_DOC = REPO_ROOT / "docs" / "architecture" / "media_transcription_provisioning_decision.md"
GO_LIVE_PLAN = REPO_ROOT / "docs" / "IDIS_COMPLETE_SYSTEM_GO_LIVE_PLAN.md"


def test_media_transcription_decision_records_current_blockers_and_deferral_reason() -> None:
    content = DECISION_DOC.read_text(encoding="utf-8")

    required_tokens = [
        ".mp4|media_transcription_unavailable: 5",
        ".mp4|file_too_large: 3",
        ".pdf|ocr_no_text_extracted: 2",
        "local ffmpeg/ffprobe unavailable",
        "Docker/CI do not provision media dependencies",
        "STT engine/model/runtime decision is not made",
        "Whisper/model provisioning is larger than a safe slice",
        "local ffmpeg + faster-whisper/whisper.cpp",
        "cloud STT provider with BYOK/privacy constraints",
        "human-supplied transcripts as first-class documents",
        "opt-in private gate first",
        "no public upload expansion until approved",
        "bounded file size/duration/runtime",
        "no raw transcript leakage in logs/gate summaries",
        "tenant isolation and audit artifacts",
        "deterministic provenance from media segment to claim/evidence",
        "Next implementation slice",
    ]
    for token in required_tokens:
        assert token in content


def test_go_live_plan_records_slice_38_decision_and_next_slice() -> None:
    content = GO_LIVE_PLAN.read_text(encoding="utf-8")

    assert "481e549def5cb9ef42469e7980c99d06f8968cec" in content
    for slice_number in range(29, 38):
        assert f"Slice {slice_number} completed" in content
    assert "Slice 38" in content
    assert "real MP4 transcription deferred pending media/STT provisioning decision" in content
    assert "media transcription provisioning implementation, after choosing runtime" in content
    assert "Slice 39 completed the opt-in private-gate `faster-whisper` runtime boundary" in content
    assert "IDIS_MEDIA_STT_MODEL_PATH" in content
    assert "IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1" in content
    assert "normal CI must not download a Whisper model" in content
