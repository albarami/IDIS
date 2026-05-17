"""Tests for Slice 39 media runtime provisioning controls."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_python_dependency_declares_faster_whisper() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "faster-whisper==1.2.1" in pyproject


def test_dockerfile_provisions_ffmpeg_for_builder_and_runtime() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count("ffmpeg") >= 2


def test_ci_check_job_provisions_ffmpeg_without_model_download() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ffmpeg" in workflow
    assert "IDIS_RUN_REAL_MEDIA_STT=1" not in workflow
    assert "IDIS_MEDIA_STT_MODEL_PATH" not in workflow
