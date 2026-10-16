"""Strict Poppler runtime identity and composition-root binding contracts."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import pytest

from idis.parsers.poppler_runtime import (
    PINNED_POPPLER_VERSION,
    POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION,
    PinnedPopplerRuntime,
    PopplerRuntimeBindingError,
    resolve_pinned_poppler_runtime,
    verify_pinned_poppler_runtime,
)
from idis.services.ingestion import defaults


def _write_windows_runtime(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True)
    paths = {
        "pdftoppm.exe": root / "pdftoppm.exe",
        "pdfinfo.exe": root / "pdfinfo.exe",
        "poppler.dll": root / "poppler.dll",
    }
    paths["pdftoppm.exe"].write_bytes(b"fictional-pdftoppm-v25.12.0")
    paths["pdfinfo.exe"].write_bytes(b"fictional-pdfinfo-v25.12.0")
    paths["poppler.dll"].write_bytes(b"fictional-poppler-dll-v25.12.0")
    return paths


def _resolve_from(paths: dict[str, Path], observed: list[str] | None = None) -> Any:
    def resolve(name: str) -> str | None:
        if observed is not None:
            observed.append(name)
        path = paths.get(name)
        return str(path) if path is not None else None

    return resolve


def _version_25_12(_path: Path) -> str:
    return PINNED_POPPLER_VERSION


def test_windows_resolution_uses_real_exe_names_and_binds_dll(
    tmp_path: Path,
) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    observed: list[str] = []

    binding = resolve_pinned_poppler_runtime(
        binary_resolver=_resolve_from(paths, observed),
        version_probe=_version_25_12,
        platform_name="Windows",
    )

    assert observed == ["pdftoppm.exe", "pdfinfo.exe"]
    assert binding.pdftoppm_path == paths["pdftoppm.exe"].resolve()
    assert binding.pdfinfo_path == paths["pdfinfo.exe"].resolve()
    assert binding.poppler_library_path == paths["poppler.dll"].resolve()
    assert binding.binary_dir == (tmp_path / "poppler-bin").resolve()
    assert binding.version == "25.12.0"
    assert binding.pdftoppm_sha256 == hashlib.sha256(paths["pdftoppm.exe"].read_bytes()).hexdigest()
    assert (
        binding.poppler_dll_sha256 == hashlib.sha256(paths["poppler.dll"].read_bytes()).hexdigest()
    )


def test_runtime_identity_is_canonical_and_path_free(tmp_path: Path) -> None:
    paths = _write_windows_runtime(tmp_path / "private-poppler-location")
    binding = resolve_pinned_poppler_runtime(
        binary_resolver=_resolve_from(paths),
        version_probe=_version_25_12,
        platform_name="Windows",
    )
    expected_payload = {
        "schema_version": POPPLER_RUNTIME_IDENTITY_SCHEMA_VERSION,
        "poppler_version": PINNED_POPPLER_VERSION,
        "pdftoppm_sha256": binding.pdftoppm_sha256,
        "poppler_dll_sha256": binding.poppler_dll_sha256,
    }
    expected_identity = hashlib.sha256(
        json.dumps(
            expected_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert binding.runtime_identity_sha256 == expected_identity
    assert binding.safe_provenance() == {
        "poppler_version": PINNED_POPPLER_VERSION,
        "poppler_executable_sha256": binding.pdftoppm_sha256,
        "poppler_runtime_identity_sha256": expected_identity,
    }
    safe_blob = json.dumps(binding.safe_provenance(), sort_keys=True)
    assert str(tmp_path) not in repr(binding)
    assert str(tmp_path) not in safe_blob
    assert "pdftoppm.exe" not in repr(binding)
    assert "poppler.dll" not in repr(binding)


def test_binding_is_child_process_serializable_and_reverifiable(tmp_path: Path) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    binding = resolve_pinned_poppler_runtime(
        binary_resolver=_resolve_from(paths),
        version_probe=_version_25_12,
        platform_name="Windows",
    )

    restored = pickle.loads(pickle.dumps(binding))  # noqa: S301 - internal typed IPC proof

    assert restored == binding
    verify_pinned_poppler_runtime(restored, version_probe=_version_25_12)


def test_operator_runtime_identity_canonical_value() -> None:
    payload = {
        "schema_version": "poppler-runtime-identity-v1",
        "poppler_version": "25.12.0",
        "pdftoppm_sha256": ("0955bd692f0bcf4ad59d94bbd2cf2d745eceac451d1566ea2e102316a714ae56"),
        "poppler_dll_sha256": ("a94e2d23c1b6d67417b8fc7dc8585b33d0d2ab9538417452f796a857c8f21af5"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert digest == "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"


def test_resolution_rejects_non_colocated_pdfinfo(tmp_path: Path) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    other = tmp_path / "other" / "pdfinfo.exe"
    other.parent.mkdir()
    other.write_bytes(b"fictional-pdfinfo")
    paths["pdfinfo.exe"] = other

    with pytest.raises(PopplerRuntimeBindingError) as exc_info:
        resolve_pinned_poppler_runtime(
            binary_resolver=_resolve_from(paths),
            version_probe=_version_25_12,
            platform_name="Windows",
        )

    assert exc_info.value.code == "POPPLER_RUNTIME_IDENTITY_INVALID"
    assert str(tmp_path) not in str(exc_info.value)


def test_resolution_rejects_wrong_component_version(tmp_path: Path) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")

    with pytest.raises(PopplerRuntimeBindingError) as exc_info:
        resolve_pinned_poppler_runtime(
            binary_resolver=_resolve_from(paths),
            version_probe=lambda path: (
                "25.11.0" if path.name.casefold() == "pdfinfo.exe" else "25.12.0"
            ),
            platform_name="Windows",
        )

    assert exc_info.value.code == "POPPLER_RUNTIME_VERSION_MISMATCH"


def test_resolution_rejects_missing_poppler_dll(tmp_path: Path) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    paths["poppler.dll"].unlink()

    with pytest.raises(PopplerRuntimeBindingError) as exc_info:
        resolve_pinned_poppler_runtime(
            binary_resolver=_resolve_from(paths),
            version_probe=_version_25_12,
            platform_name="Windows",
        )

    assert exc_info.value.code == "POPPLER_RUNTIME_UNAVAILABLE"


@pytest.mark.parametrize("drifted_name", ["pdftoppm.exe", "poppler.dll"])
def test_child_verification_rejects_executable_or_dll_drift(
    tmp_path: Path,
    drifted_name: str,
) -> None:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    binding = resolve_pinned_poppler_runtime(
        binary_resolver=_resolve_from(paths),
        version_probe=_version_25_12,
        platform_name="Windows",
    )
    paths[drifted_name].write_bytes(b"drifted-runtime-component")

    with pytest.raises(PopplerRuntimeBindingError) as exc_info:
        verify_pinned_poppler_runtime(
            binding,
            version_probe=_version_25_12,
        )

    assert exc_info.value.code == "POPPLER_RUNTIME_DRIFTED"


class _RecordingAdapter:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _binding(tmp_path: Path) -> PinnedPopplerRuntime:
    paths = _write_windows_runtime(tmp_path / "poppler-bin")
    return resolve_pinned_poppler_runtime(
        binary_resolver=_resolve_from(paths),
        version_probe=_version_25_12,
        platform_name="Windows",
    )


def test_build_default_ocr_config_threads_explicit_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(tmp_path)
    monkeypatch.setattr(defaults, "TesseractOcrAdapter", _RecordingAdapter)

    config = defaults.build_default_ocr_config(
        {"IDIS_OCR_ENABLED": "1"},
        poppler_runtime=binding,
    )

    assert config is not None
    assert isinstance(config.adapter, _RecordingAdapter)
    assert config.adapter.kwargs["poppler_runtime"] is binding


def test_strict_builder_resolves_once_and_non_strict_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(tmp_path)
    calls: list[str] = []

    def resolve_once() -> PinnedPopplerRuntime:
        calls.append("resolve")
        return binding

    monkeypatch.setattr(defaults, "resolve_pinned_poppler_runtime", resolve_once)
    monkeypatch.setattr(defaults, "TesseractOcrAdapter", _RecordingAdapter)
    monkeypatch.setattr(
        defaults,
        "load_document_no_substantive_content_attestation",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(defaults, "load_document_password_manifest", lambda **_kwargs: None)
    monkeypatch.setattr(defaults, "build_default_compliance_store", object)
    monkeypatch.setattr(defaults, "IngestionService", lambda **kwargs: kwargs)

    strict_result = defaults.build_default_ingestion_service(
        env={
            "IDIS_INGEST_MAX_BYTES": str(512 * 1024 * 1024),
            "IDIS_OCR_ENABLED": "1",
        },
        strict=True,
    )
    assert calls == ["resolve"]
    strict_config = strict_result["ocr_config"]
    assert strict_config.adapter.kwargs["poppler_runtime"] is binding

    calls.clear()
    non_strict_result = defaults.build_default_ingestion_service(
        env={"IDIS_OCR_ENABLED": "1"},
        strict=False,
    )
    assert calls == []
    non_strict_config = non_strict_result["ocr_config"]
    assert non_strict_config.adapter.kwargs["poppler_runtime"] is None
