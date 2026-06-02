"""Slice80 Task 3 — wire media/STT health into strict readiness + provisioning truth.

TDD RED-first. Mirrors test_slice79_ocr_strict_wiring. Verifies that strict readiness
and the provisioning-truth report consume ``check_media_health`` (via an injectable
checker), fail closed when a media-required corpus is present but health is not HEALTHY,
treat disabled media as an expected non-error state, surface only safe fixed dependency
identifiers, and never leak paths/secrets/transcript text/model paths/command output.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

from idis.services.media_health import MediaHealthCheck
from idis.services.ocr_health import OcrHealthCheck
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
    build_strict_provisioning_truth_report,
)

_MEDIA_CORPUS: list[Mapping[str, Any]] = [
    {
        "document_id": "doc-media-1",
        "document_name": "redacted.mp4",
        "doc_type": "MEDIA",
        "metadata": {"parser_reason_codes": ["media_transcription_unavailable"]},
    }
]


def _readiness(
    media_health: MediaHealthCheck,
    *,
    corpus: list[Mapping[str, Any]] | None = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    return build_strict_full_live_readiness_report(
        preflight_corpus=_MEDIA_CORPUS if corpus is None else corpus,
        env={} if env is None else env,
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        media_health_checker=lambda _env: media_health,
    )


def _media_inventory(report: Any) -> Any:
    return next(item for item in report.component_inventory if item.component_name == "MP4/STT")


# ----- strict readiness -----


def test_readiness_builder_accepts_media_health_checker() -> None:
    params = inspect.signature(build_strict_full_live_readiness_report).parameters
    assert "media_health_checker" in params


def test_media_required_healthy_is_live() -> None:
    media = _readiness(MediaHealthCheck.healthy()).component("mp4_stt")
    assert media.status is StrictComponentStatus.LIVE_WIRED_AND_USED
    assert media.may_proceed is True
    assert media.blocker_message == ""


def test_media_required_disabled_blocks_with_safe_names() -> None:
    media = _readiness(MediaHealthCheck.disabled()).component("mp4_stt")
    assert media.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert media.may_proceed is False
    assert "IDIS_MEDIA_ADAPTER=faster-whisper" in media.required_env_vars
    blob = media.model_dump_json()
    for token in ("C:\\", "/var/", "/home/", "sk-"):
        assert token not in blob


def test_media_required_missing_dependencies_surfaces_safe_identifiers_only() -> None:
    media = _readiness(
        MediaHealthCheck.missing(
            dependencies=["ffmpeg", "ffprobe", "faster_whisper", "media_model"]
        )
    ).component("mp4_stt")
    assert media.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert media.may_proceed is False
    assert set(media.required_services) >= {"ffmpeg", "ffprobe", "faster_whisper", "media_model"}
    blob = media.model_dump_json()
    for token in ("C:\\", "/var/", "/home/", "model.bin", "sk-"):
        assert token not in blob


def test_media_required_failed_blocks_without_leaking_error() -> None:
    confidential = "C:\\secret\\whisper.bin MEDIA-TRANSCRIPT-MARKER sk-LEAK123"
    media = _readiness(MediaHealthCheck.failed(error=confidential)).component("mp4_stt")
    assert media.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert media.may_proceed is False
    blob = media.model_dump_json()
    assert "C:\\secret" not in blob
    assert "whisper.bin" not in blob
    assert "MEDIA-TRANSCRIPT-MARKER" not in blob
    assert "sk-LEAK123" not in blob
    assert "[redacted]" not in blob


def test_media_not_required_may_proceed_even_if_health_failed() -> None:
    media = _readiness(
        MediaHealthCheck.failed(error="boom"),
        corpus=[{"document_id": "doc-pdf", "doc_type": "PDF", "metadata": {}}],
    ).component("mp4_stt")
    assert media.status is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert media.may_proceed is True
    assert media.blocker_message == ""


def test_media_inventory_reflects_health() -> None:
    healthy_inv = _media_inventory(_readiness(MediaHealthCheck.healthy()))
    assert healthy_inv.full_wired is True
    assert healthy_inv.health_check_status == "healthy"

    disabled_inv = _media_inventory(_readiness(MediaHealthCheck.disabled()))
    assert disabled_inv.full_wired is False
    assert disabled_inv.health_check_status == "missing_config"

    failed_inv = _media_inventory(_readiness(MediaHealthCheck.failed(error="boom")))
    assert failed_inv.full_wired is False
    assert failed_inv.health_check_status == "configured_failed"


# ----- provisioning truth -----


def _provisioning(
    media_health: MediaHealthCheck | None = None,
    *,
    allow: bool = False,
    corpus: list[Mapping[str, Any]] | None = None,
) -> Any:
    return build_strict_provisioning_truth_report(
        env={},
        preflight_corpus=corpus,
        allow_local_strict_health_probes=allow,
        media_health_checker=(lambda _env: media_health) if media_health is not None else None,
    )


def _component(report: Any, name: str) -> Any:
    return next(c for c in report["components"] if c["component_name"] == name)


def test_provisioning_accepts_media_health_checker() -> None:
    params = inspect.signature(build_strict_provisioning_truth_report).parameters
    assert "media_health_checker" in params


def test_provisioning_includes_static_media_health_safely() -> None:
    report = _provisioning(corpus=_MEDIA_CORPUS)
    media = _component(report, "MP4/STT")
    assert media["component_name"] == "MP4/STT"
    assert media["local_probe_attempted"] is False
    blob = json.dumps(report)
    assert "C:\\" not in blob
    assert "redacted.mp4" not in blob


def test_provisioning_media_not_probed_by_default() -> None:
    media = _component(_provisioning(allow=False), "MP4/STT")
    assert media["local_probe_attempted"] is False
    assert media["health_checked"] is False
    assert media["health_check_status"] in {"not_run", "configured_not_checked"}


def test_provisioning_media_local_probe_healthy_when_opted_in() -> None:
    media = _component(_provisioning(MediaHealthCheck.healthy(), allow=True), "MP4/STT")
    assert media["local_probe_attempted"] is True
    assert media["local_probe_passed"] is True
    assert media["health_check_status"] == "healthy"
    assert media["local_probe_label"] == "media_local_health"


def test_provisioning_media_disabled_is_expected_non_error() -> None:
    media = _component(_provisioning(MediaHealthCheck.disabled(), allow=True), "MP4/STT")
    assert media["local_probe_attempted"] is False
    assert media["local_probe_passed"] is False
    assert media["local_probe_blocker"] == "media_disabled"


def test_provisioning_media_missing_dependencies_is_configured_failed() -> None:
    media = _component(
        _provisioning(MediaHealthCheck.missing(dependencies=["ffmpeg"]), allow=True), "MP4/STT"
    )
    assert media["local_probe_attempted"] is True
    assert media["local_probe_passed"] is False
    assert media["health_check_status"] == "configured_failed"


def test_provisioning_media_health_does_not_leak() -> None:
    report = _provisioning(
        MediaHealthCheck.failed(error="C:\\secret\\whisper.bin sk-LEAK123 MEDIA-TRANSCRIPT-MARKER"),
        allow=True,
    )
    blob = json.dumps(report)
    assert "C:\\secret" not in blob
    assert "sk-LEAK123" not in blob
    assert "MEDIA-TRANSCRIPT-MARKER" not in blob


# ----- OCR regression (must still work alongside media wiring) -----


def test_ocr_wiring_still_works_with_media_wiring_present() -> None:
    report = build_strict_full_live_readiness_report(
        preflight_corpus=[
            {
                "document_id": "doc-ocr",
                "document_name": "scanned.png",
                "doc_type": "IMAGE",
                "metadata": {"parser_requires_ocr": True, "parser_reason_codes": ["ocr_required"]},
            }
        ],
        env={},
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        ocr_health_checker=lambda _env: OcrHealthCheck.healthy(),
        media_health_checker=lambda _env: MediaHealthCheck.disabled(),
    )
    assert report.component("ocr").status is StrictComponentStatus.LIVE_WIRED_AND_USED
    assert report.component("ocr").may_proceed is True
