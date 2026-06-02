"""Slice80 Task 2 — strict media/STT health-check module tests.

TDD RED-first. Mirrors tests/test_ocr_health.py. Covers: disabled (adapter unset),
healthy, missing ffmpeg, missing ffprobe, missing faster_whisper module, missing model,
runtime probe (both ffmpeg/ffprobe -version via injectable command_runner), runtime
command-failure (no leak), unsupported adapter, sanitized+truncated + only-safe-fields
result, and sorted/deduped missing dependencies.

All probes are injected fakes — no real ffmpeg/ffprobe/faster-whisper/model is required
in CI. The result model must never expose raw paths, env values, transcript text,
command output, or secrets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from idis.services.media_health import MediaHealthCheck, MediaHealthStatus, check_media_health


def _present_resolver(name: str) -> str | None:
    return f"/opt/bin/{name}"


def _module_present(name: str) -> bool:
    return True


def _model_present(env: Mapping[str, str]) -> bool:
    return True


def _noop_runtime() -> None:
    return None


def _healthy_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "env": {"IDIS_MEDIA_ADAPTER": "faster-whisper"},
        "binary_resolver": _present_resolver,
        "module_probe": _module_present,
        "model_probe": _model_present,
        "runtime_probe": _noop_runtime,
    }
    kwargs.update(overrides)
    return kwargs


def test_disabled_when_adapter_not_set() -> None:
    for env in ({}, {"IDIS_MEDIA_ADAPTER": ""}, {"IDIS_MEDIA_ADAPTER": "  "}):
        result = check_media_health(env=env)
        assert result.status is MediaHealthStatus.DISABLED
        assert result.enabled is False
        assert result.missing_dependencies == []
        assert result.error is None


def test_healthy_when_adapter_binaries_module_and_model_present() -> None:
    result = check_media_health(**_healthy_kwargs())
    assert result.status is MediaHealthStatus.HEALTHY
    assert result.enabled is True
    assert result.missing_dependencies == []
    assert result.error is None


def test_missing_ffmpeg_binary() -> None:
    def resolver(name: str) -> str | None:
        return None if name == "ffmpeg" else f"/opt/bin/{name}"

    result = check_media_health(**_healthy_kwargs(binary_resolver=resolver))
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == ["ffmpeg"]
    assert result.error is None


def test_missing_ffprobe_binary() -> None:
    def resolver(name: str) -> str | None:
        return None if name == "ffprobe" else f"/opt/bin/{name}"

    result = check_media_health(**_healthy_kwargs(binary_resolver=resolver))
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == ["ffprobe"]


def test_missing_faster_whisper_module() -> None:
    def module_probe(name: str) -> bool:
        return False

    result = check_media_health(**_healthy_kwargs(module_probe=module_probe))
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert "faster_whisper" in result.missing_dependencies


def test_missing_model_uses_safe_identifier() -> None:
    def model_probe(env: Mapping[str, str]) -> bool:
        return False

    result = check_media_health(**_healthy_kwargs(model_probe=model_probe))
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert "media_model" in result.missing_dependencies


def test_runtime_probe_checks_both_ffmpeg_and_ffprobe_versions() -> None:
    calls: list[list[str]] = []

    def runner(args: Any) -> None:
        calls.append(list(args))

    result = check_media_health(
        env={"IDIS_MEDIA_ADAPTER": "faster-whisper"},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        model_probe=_model_present,
        command_runner=runner,
    )

    assert result.status is MediaHealthStatus.HEALTHY
    assert ["ffmpeg", "-version"] in calls
    assert ["ffprobe", "-version"] in calls


def test_runtime_command_failure_is_failed_without_leak() -> None:
    confidential = "FFMPEG FATAL C:\\secret\\model sk-LEAK123 /var/secret/clip.mp4 stdout"

    def runner(args: Any) -> None:
        raise RuntimeError(confidential)

    result = check_media_health(
        env={"IDIS_MEDIA_ADAPTER": "faster-whisper"},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        model_probe=_model_present,
        command_runner=runner,
    )

    assert result.status is MediaHealthStatus.FAILED
    assert result.error is not None
    for marker in ("FFMPEG FATAL", "C:\\secret", "sk-LEAK123", "/var/secret", "stdout"):
        assert marker not in result.error


def test_failed_error_is_sanitized_and_truncated() -> None:
    nasty = (
        "boom at C:\\secret\\models\\whisper.bin and /usr/share/secret/models/base "
        "token sk-ABC123def456 " + ("x" * 400)
    )
    result = MediaHealthCheck.failed(error=nasty)
    assert result.status is MediaHealthStatus.FAILED
    assert result.error is not None
    assert "C:\\secret" not in result.error
    assert "/usr/share/secret" not in result.error
    assert "sk-ABC123def456" not in result.error
    assert len(result.error) <= 240


def test_unsupported_adapter_is_failed_without_echoing_value() -> None:
    result = check_media_health(env={"IDIS_MEDIA_ADAPTER": "whisper-cpp"})
    assert result.status is MediaHealthStatus.FAILED
    assert result.error is not None
    assert "whisper-cpp" not in result.error


def test_result_exposes_only_safe_fields_and_no_env_values() -> None:
    env: Mapping[str, str] = {
        "IDIS_MEDIA_ADAPTER": "faster-whisper",
        "IDIS_MEDIA_STT_MODEL_PATH": "C:\\secret\\models\\whisper",
    }

    def resolver(name: str) -> str | None:
        return None

    def module_probe(name: str) -> bool:
        return False

    def model_probe(env_map: Mapping[str, str]) -> bool:
        return False

    result = check_media_health(
        env=env,
        binary_resolver=resolver,
        module_probe=module_probe,
        model_probe=model_probe,
    )
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert set(result.model_dump().keys()) == {
        "status",
        "enabled",
        "missing_dependencies",
        "error",
    }
    blob = result.model_dump_json()
    assert "C:\\secret" not in blob
    assert "secret" not in blob


def test_missing_dependencies_are_sorted_and_deduplicated() -> None:
    def resolver(name: str) -> str | None:
        return None

    def module_probe(name: str) -> bool:
        return False

    def model_probe(env_map: Mapping[str, str]) -> bool:
        return False

    result = check_media_health(
        env={"IDIS_MEDIA_ADAPTER": "faster-whisper"},
        binary_resolver=resolver,
        module_probe=module_probe,
        model_probe=model_probe,
    )
    assert result.status is MediaHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == sorted(result.missing_dependencies)
    assert result.missing_dependencies == ["faster_whisper", "ffmpeg", "ffprobe", "media_model"]
