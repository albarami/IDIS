"""Slice79 Task 2 — strict OCR health-check module tests.

TDD RED-first. Covers: healthy, disabled/config-missing, missing tesseract,
missing poppler/pdfinfo, missing Python deps, missing tessdata/language,
runtime timeout/command failure, and sanitized/truncated + safe-field results.

All probes are injected fakes — no real tesseract/poppler/Python OCR deps are
required in CI. The result model must never expose raw paths, env values, OCR
text, or secrets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from idis.services.ocr_health import OcrHealthCheck, OcrHealthStatus, check_ocr_health


def _present_resolver(name: str) -> str | None:
    """Fake binary resolver: every binary present (fake path, never surfaced)."""
    return f"/opt/bin/{name}"


def _module_present(name: str) -> bool:
    return True


def _language_present(language: str) -> bool:
    return True


def _noop_runtime() -> None:
    return None


def _healthy_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "env": {"IDIS_OCR_ENABLED": "1"},
        "binary_resolver": _present_resolver,
        "module_probe": _module_present,
        "language_probe": _language_present,
        "runtime_probe": _noop_runtime,
    }
    kwargs.update(overrides)
    return kwargs


def test_disabled_when_ocr_not_enabled() -> None:
    for env in ({}, {"IDIS_OCR_ENABLED": "0"}, {"IDIS_OCR_ENABLED": "false"}):
        result = check_ocr_health(env=env)
        assert result.status is OcrHealthStatus.DISABLED
        assert result.enabled is False
        assert result.missing_dependencies == []
        assert result.error is None


def test_healthy_when_enabled_and_all_dependencies_present() -> None:
    result = check_ocr_health(**_healthy_kwargs())
    assert result.status is OcrHealthStatus.HEALTHY
    assert result.enabled is True
    assert result.missing_dependencies == []
    assert result.error is None


def test_missing_tesseract_binary() -> None:
    def resolver(name: str) -> str | None:
        return None if name == "tesseract" else f"/opt/bin/{name}"

    result = check_ocr_health(**_healthy_kwargs(binary_resolver=resolver))
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == ["tesseract"]
    assert result.error is None


def test_missing_poppler_pdfinfo_binary() -> None:
    def resolver(name: str) -> str | None:
        return None if name == "pdfinfo" else f"/opt/bin/{name}"

    result = check_ocr_health(**_healthy_kwargs(binary_resolver=resolver))
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == ["poppler"]


def test_missing_python_dependencies() -> None:
    def module_probe(name: str) -> bool:
        return False

    result = check_ocr_health(**_healthy_kwargs(module_probe=module_probe))
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert {"pytesseract", "pdf2image", "pillow"}.issubset(set(result.missing_dependencies))


def test_missing_tessdata_language() -> None:
    def language_probe(language: str) -> bool:
        return False

    result = check_ocr_health(**_healthy_kwargs(language_probe=language_probe))
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert "tessdata" in result.missing_dependencies


def test_runtime_failure_is_failed_and_does_not_leak() -> None:
    confidential = "CONFIDENTIAL_OCR_REVENUE_5M at /var/secret/scan.png sk-LEAK123"

    def boom() -> None:
        raise RuntimeError(confidential)

    result = check_ocr_health(**_healthy_kwargs(runtime_probe=boom))
    assert result.status is OcrHealthStatus.FAILED
    assert result.error is not None
    assert "CONFIDENTIAL_OCR_REVENUE_5M" not in result.error
    assert "/var/secret" not in result.error
    assert "scan.png" not in result.error
    assert "sk-LEAK123" not in result.error


def test_runtime_timeout_is_failed() -> None:
    def slow() -> None:
        raise TimeoutError("ocr command timed out")

    result = check_ocr_health(**_healthy_kwargs(runtime_probe=slow))
    assert result.status is OcrHealthStatus.FAILED
    assert result.error is not None


def test_failed_error_is_sanitized_and_truncated() -> None:
    nasty = (
        "boom at C:\\secret\\tessdata\\key.txt and "
        "/usr/share/secret/tessdata/eng.traineddata token sk-ABC123def456 " + ("x" * 400)
    )
    result = OcrHealthCheck.failed(error=nasty)
    assert result.status is OcrHealthStatus.FAILED
    assert result.error is not None
    assert "C:\\secret" not in result.error
    assert "/usr/share/secret" not in result.error
    assert "sk-ABC123def456" not in result.error
    assert len(result.error) <= 240


def test_unsupported_adapter_is_failed_without_echoing_value() -> None:
    result = check_ocr_health(env={"IDIS_OCR_ENABLED": "1", "IDIS_OCR_ADAPTER": "paddleocr"})
    assert result.status is OcrHealthStatus.FAILED
    assert result.error is not None
    assert "paddleocr" not in result.error


def test_result_exposes_only_safe_fields_and_no_env_values() -> None:
    env: Mapping[str, str] = {
        "IDIS_OCR_ENABLED": "1",
        "IDIS_OCR_LANGUAGE": "SECRETLANGMARKER",
        "TESSDATA_PREFIX": "C:\\secret\\tessdata",
    }

    def resolver(name: str) -> str | None:
        return None

    def module_probe(name: str) -> bool:
        return False

    def language_probe(language: str) -> bool:
        return False

    result = check_ocr_health(
        env=env,
        binary_resolver=resolver,
        module_probe=module_probe,
        language_probe=language_probe,
    )
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert set(result.model_dump().keys()) == {
        "status",
        "enabled",
        "missing_dependencies",
        "error",
    }
    blob = result.model_dump_json()
    assert "SECRETLANGMARKER" not in blob
    assert "C:\\secret" not in blob
    assert "secret" not in blob


def test_missing_dependencies_are_sorted_and_deduplicated() -> None:
    def resolver(name: str) -> str | None:
        return None

    def module_probe(name: str) -> bool:
        return False

    def language_probe(language: str) -> bool:
        return False

    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1"},
        binary_resolver=resolver,
        module_probe=module_probe,
        language_probe=language_probe,
    )
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES
    assert result.missing_dependencies == sorted(result.missing_dependencies)
    assert result.missing_dependencies == [
        "pdf2image",
        "pillow",
        "poppler",
        "pytesseract",
        "tessdata",
        "tesseract",
    ]


def test_default_language_probe_honors_supplied_env(tmp_path: Path) -> None:
    (tmp_path / "eng.traineddata").write_bytes(b"fake-traineddata")

    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1", "TESSDATA_PREFIX": str(tmp_path)},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        runtime_probe=_noop_runtime,
    )

    assert "tessdata" not in result.missing_dependencies
    assert result.status is OcrHealthStatus.HEALTHY


def test_default_language_probe_supports_nested_tessdata_dir(tmp_path: Path) -> None:
    nested = tmp_path / "tessdata"
    nested.mkdir()
    (nested / "eng.traineddata").write_bytes(b"fake-traineddata")

    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1", "TESSDATA_PREFIX": str(tmp_path)},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        runtime_probe=_noop_runtime,
    )

    assert "tessdata" not in result.missing_dependencies


def test_default_language_probe_reports_missing_without_tessdata_prefix() -> None:
    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1"},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        runtime_probe=_noop_runtime,
    )

    assert "tessdata" in result.missing_dependencies
    assert result.status is OcrHealthStatus.MISSING_DEPENDENCIES


def test_default_runtime_path_runs_version_checks_for_both_binaries() -> None:
    calls: list[list[str]] = []

    def runner(args: Sequence[str]) -> None:
        calls.append(list(args))

    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1"},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        language_probe=_language_present,
        command_runner=runner,
    )

    assert result.status is OcrHealthStatus.HEALTHY
    assert {call[0] for call in calls} == {"tesseract", "pdfinfo"}


def test_default_runtime_path_catches_command_failure_without_leak() -> None:
    confidential = "TESSERACT FATAL /opt/secret/scan.png sk-LEAK123 stdout-content"

    def runner(args: Sequence[str]) -> None:
        raise RuntimeError(confidential)

    result = check_ocr_health(
        env={"IDIS_OCR_ENABLED": "1"},
        binary_resolver=_present_resolver,
        module_probe=_module_present,
        language_probe=_language_present,
        command_runner=runner,
    )

    assert result.status is OcrHealthStatus.FAILED
    assert result.error is not None
    for marker in ("TESSERACT FATAL", "/opt/secret", "scan.png", "sk-LEAK123", "stdout-content"):
        assert marker not in result.error
