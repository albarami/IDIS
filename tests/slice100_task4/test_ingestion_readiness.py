"""Task 4 RED contract for bounded large-upload dependency readiness.

The checks are hermetic: typed observations are injected and the collector uses
only temporary local files.  No provider, database, Redis, OCR, or STT call is
made here.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

_APPROVED_MAX_BYTES = 536_870_912
_APPROVED_OCR_MAX_PAGES = 72
_APPROVED_OCR_TIMEOUT_SECONDS = 120
_APPROVED_OCR_DPI = 200
_SAFE_BLOCKER = "MEDIA_LARGE_UPLOAD_UNSUPPORTED"
_TENANT_ID = "10000000-0000-4000-8000-000000000001"


def _readiness() -> Any:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _probes() -> Any:
    return importlib.import_module("idis.runtime.dependency_probes")


def _ingestion_env(**overrides: str) -> dict[str, str]:
    values = {
        "IDIS_INGEST_MAX_BYTES": str(_APPROVED_MAX_BYTES),
        "IDIS_OCR_MAX_PAGES": str(_APPROVED_OCR_MAX_PAGES),
        "IDIS_OCR_TIMEOUT_SECONDS": str(_APPROVED_OCR_TIMEOUT_SECONDS),
        "IDIS_OCR_DPI": str(_APPROVED_OCR_DPI),
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("IDIS_OCR_MAX_PAGES", "10.5"),
        ("IDIS_OCR_DPI", "200.5"),
        ("IDIS_MEDIA_LANGUAGE", "ar"),
        ("IDIS_MEDIA_MAX_DURATION_SECONDS", "127.999"),
    ],
)
def test_strict_profile_rejects_runtime_incompatible_ocr_and_stt_values(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    from idis.runtime.profile import (
        STRICT_PROFILE_LOAD_FAILED,
        StrictProfileError,
        _validate_profile,
    )
    from tests.slice100_task2.test_strict_profile_launcher import _profile_values

    values = _profile_values(tmp_path)
    values[name] = value

    with pytest.raises(StrictProfileError) as caught:
        _validate_profile(command="api", values=values)

    assert caught.value.code == STRICT_PROFILE_LOAD_FAILED


@pytest.mark.parametrize(
    "name",
    (
        "IDIS_OCR_TIMEOUT_SECONDS",
        "IDIS_MEDIA_TIMEOUT_SECONDS",
        "IDIS_MEDIA_MAX_DURATION_SECONDS",
    ),
)
@pytest.mark.parametrize("value", ("nan", "inf", "-inf"))
def test_strict_profile_rejects_nonfinite_ocr_and_stt_bounds(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    from idis.runtime.profile import (
        STRICT_PROFILE_LOAD_FAILED,
        StrictProfileError,
        _validate_profile,
    )
    from tests.slice100_task2.test_strict_profile_launcher import _profile_values

    values = _profile_values(tmp_path)
    values[name] = value

    with pytest.raises(StrictProfileError) as caught:
        _validate_profile(command="api", values=values)

    assert caught.value.code == STRICT_PROFILE_LOAD_FAILED


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("IDIS_OCR_TIMEOUT_SECONDS", "119"),
        ("IDIS_OCR_TIMEOUT_SECONDS", "121"),
        ("IDIS_OCR_TIMEOUT_SECONDS", "300"),
        ("IDIS_OCR_DPI", "199"),
        ("IDIS_OCR_DPI", "201"),
        ("IDIS_OCR_DPI", "300"),
    ],
)
def test_strict_profile_pins_the_reviewed_pdf_ocr_resource_budget(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    from idis.runtime.profile import (
        STRICT_PROFILE_LOAD_FAILED,
        StrictProfileError,
        _validate_profile,
    )
    from tests.slice100_task2.test_strict_profile_launcher import _profile_values

    values = _profile_values(tmp_path)
    values[name] = value

    with pytest.raises(StrictProfileError) as caught:
        _validate_profile(command="api", values=values)

    assert caught.value.code == STRICT_PROFILE_LOAD_FAILED


def _facts(module: Any, **overrides: object) -> object:
    values: dict[str, object] = {
        "backend": "filesystem",
        "put_stream_supported": True,
        "bounded_read_ok": True,
        "stream_write_ok": True,
        "stream_read_ok": True,
        "stream_hash_ok": True,
        "stream_cleanup_ok": True,
        "staging_write_ok": True,
        "staging_cleanup_ok": True,
        "staging_free_bytes": _APPROVED_MAX_BYTES,
        "object_store_free_bytes": _APPROVED_MAX_BYTES,
        "shared_filesystem": False,
        "password_manifest_status": "none_configured",
    }
    values.update(overrides)
    return module.IngestionProbeFacts(**values)


def test_configured_no_substantive_content_attestation_requires_verified_hash_bound_probe() -> None:
    module = _readiness()
    env = _ingestion_env(
        IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH="C:/secure/attestation.json"
    )

    blocked = module.check_ingestion_readiness(env=env, probe=lambda: _facts(module))
    allowed = module.check_ingestion_readiness(
        env=env,
        probe=lambda: _facts(
            module,
            no_substantive_content_attestation_status="verified",
            no_substantive_content_attestation_entry_count=1,
            no_substantive_content_attestation_schema_version=(
                "document-no-substantive-content-attestation-v1"
            ),
            no_substantive_content_attestation_disposition=(
                "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
            ),
            no_substantive_content_attestation_variant="watermark_only",
            no_substantive_content_attestation_payload_sha256="a" * 64,
            no_substantive_content_attestation_envelope_sha256="b" * 64,
            no_substantive_content_attestation_public_key_sha256="c" * 64,
        ),
    )

    assert blocked.may_proceed is False
    assert blocked.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"
    assert allowed.may_proceed is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("no_substantive_content_attestation_schema_version", "superseded-v0"),
        ("no_substantive_content_attestation_disposition", "DOCUMENT_ILLEGIBLE_CONFIRMED"),
        ("no_substantive_content_attestation_variant", "unknown"),
    ],
)
def test_configured_attestation_probe_rejects_schema_disposition_or_variant_drift(
    field: str,
    value: str,
) -> None:
    module = _readiness()
    facts = {
        "no_substantive_content_attestation_status": "verified",
        "no_substantive_content_attestation_entry_count": 1,
        "no_substantive_content_attestation_schema_version": (
            "document-no-substantive-content-attestation-v1"
        ),
        "no_substantive_content_attestation_disposition": (
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
        ),
        "no_substantive_content_attestation_variant": "watermark_only",
        "no_substantive_content_attestation_payload_sha256": "a" * 64,
        "no_substantive_content_attestation_envelope_sha256": "b" * 64,
        "no_substantive_content_attestation_public_key_sha256": "c" * 64,
    }
    facts[field] = value

    check = module.check_ingestion_readiness(
        env=_ingestion_env(
            IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH=(
                "C:/secure/attestation.json"
            )
        ),
        probe=lambda: _facts(module, **facts),
    )

    assert check.may_proceed is False
    assert check.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"


def test_unconfigured_no_substantive_content_attestation_rejects_phantom_verified_probe() -> None:
    module = _readiness()
    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(
            module,
            no_substantive_content_attestation_status="verified",
            no_substantive_content_attestation_entry_count=1,
            no_substantive_content_attestation_payload_sha256="a" * 64,
            no_substantive_content_attestation_envelope_sha256="b" * 64,
            no_substantive_content_attestation_public_key_sha256="c" * 64,
        ),
    )

    assert check.may_proceed is False
    assert check.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "configured",
    [None, "", "0", "-1", "536870911", "536870913", "not-an-int"],
)
def test_ingestion_readiness_requires_the_approved_bound_before_probe(
    configured: str | None,
) -> None:
    module = _readiness()
    env = {"IDIS_OCR_MAX_PAGES": str(_APPROVED_OCR_MAX_PAGES)}
    if configured is not None:
        env["IDIS_INGEST_MAX_BYTES"] = configured
    calls = 0

    def probe() -> object:
        nonlocal calls
        calls += 1
        return _facts(module)

    check = module.check_ingestion_readiness(env=env, probe=probe)

    assert check.component == "ingestion"
    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER
    assert calls == 0


@pytest.mark.parametrize("configured", [None, "", "0", "71", "73", "not-an-int"])
def test_ingestion_readiness_requires_the_audited_pdf_ocr_page_bound(
    configured: str | None,
) -> None:
    module = _readiness()
    env = {"IDIS_INGEST_MAX_BYTES": str(_APPROVED_MAX_BYTES)}
    if configured is not None:
        env["IDIS_OCR_MAX_PAGES"] = configured
    calls = 0

    def probe() -> object:
        nonlocal calls
        calls += 1
        return _facts(module)

    check = module.check_ingestion_readiness(env=env, probe=probe)

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER
    assert calls == 0


@pytest.mark.parametrize(
    ("name", "configured"),
    [
        ("IDIS_OCR_TIMEOUT_SECONDS", None),
        ("IDIS_OCR_TIMEOUT_SECONDS", "119"),
        ("IDIS_OCR_TIMEOUT_SECONDS", "121"),
        ("IDIS_OCR_TIMEOUT_SECONDS", "300"),
        ("IDIS_OCR_DPI", None),
        ("IDIS_OCR_DPI", "199"),
        ("IDIS_OCR_DPI", "201"),
        ("IDIS_OCR_DPI", "300"),
    ],
)
def test_ingestion_readiness_pins_the_reviewed_pdf_ocr_resource_budget_before_probe(
    name: str,
    configured: str | None,
) -> None:
    module = _readiness()
    env = _ingestion_env()
    if configured is None:
        del env[name]
    else:
        env[name] = configured
    calls = 0

    def probe() -> object:
        nonlocal calls
        calls += 1
        return _facts(module)

    check = module.check_ingestion_readiness(env=env, probe=probe)

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER
    assert calls == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "memory"},
        {"put_stream_supported": False},
        {"bounded_read_ok": False},
        {"stream_write_ok": False},
        {"stream_read_ok": False},
        {"stream_hash_ok": False},
        {"stream_cleanup_ok": False},
        {"staging_write_ok": False},
        {"staging_cleanup_ok": False},
    ],
)
def test_ingestion_readiness_requires_stream_and_spool_capabilities(
    overrides: dict[str, object],
) -> None:
    module = _readiness()
    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(module, **overrides),
    )

    assert check.component == "ingestion"
    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER


@pytest.mark.parametrize("low_side", ["staging", "object_store"])
def test_separate_volumes_each_require_one_full_upload_of_headroom(low_side: str) -> None:
    module = _readiness()
    overrides = {
        f"{low_side}_free_bytes": _APPROVED_MAX_BYTES - 1,
        "shared_filesystem": False,
    }

    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(module, **overrides),
    )

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER


def test_shared_volume_requires_headroom_for_spool_and_object_copy() -> None:
    module = _readiness()
    required = 2 * _APPROVED_MAX_BYTES

    blocked = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(
            module,
            staging_free_bytes=required - 1,
            object_store_free_bytes=required - 1,
            shared_filesystem=True,
        ),
    )
    allowed = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(
            module,
            staging_free_bytes=required,
            object_store_free_bytes=required,
            shared_filesystem=True,
        ),
    )

    assert (blocked.may_proceed, blocked.code) == (False, _SAFE_BLOCKER)
    assert (allowed.may_proceed, allowed.code) == (True, None)


@pytest.mark.parametrize(
    "bad_value",
    [True, False, -1, "536870912", None],
)
def test_disk_observations_are_typed_non_negative_integers(bad_value: object) -> None:
    module = _readiness()
    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(module, staging_free_bytes=bad_value),
    )

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER


def test_probe_exception_is_redacted_to_one_stable_code() -> None:
    module = _readiness()
    secret_path = r"C:\private\company-one\largest-video.mp4"

    def unavailable() -> object:
        raise OSError(f"disk probe failed for {secret_path}")

    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=unavailable,
    )

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER
    assert secret_path not in repr(check)


@pytest.mark.parametrize("status", ["configured", "invalid", "unknown", ""])
def test_ingestion_readiness_requires_no_password_manifest_for_audited_source(
    status: str,
) -> None:
    module = _readiness()

    check = module.check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: _facts(module, password_manifest_status=status),
    )

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER


def test_configured_password_manifest_path_blocks_before_ingestion_probe() -> None:
    module = _readiness()
    calls = 0

    def probe() -> object:
        nonlocal calls
        calls += 1
        return _facts(module)

    check = module.check_ingestion_readiness(
        env=_ingestion_env(IDIS_REAL_EXAMPLE_PASSWORD_MANIFEST_PATH=r"C:\private\passwords.json"),
        probe=probe,
    )

    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER
    assert calls == 0


def test_local_collector_proves_bounded_put_stream_and_spool_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _probes()
    from idis.storage.filesystem_store import FilesystemObjectStore

    staging_root = tmp_path / "staging"
    object_root = tmp_path / "objects"
    staging_root.mkdir()
    object_root.mkdir()
    free_bytes = 2 * _APPROVED_MAX_BYTES
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda _path: (free_bytes * 2, free_bytes, free_bytes),
    )

    facts = module.collect_ingestion_readiness_facts(
        store_factory=lambda: FilesystemObjectStore(object_root),
        tenant_id=_TENANT_ID,
        staging_root=staging_root,
        object_store_root=object_root,
        password_manifest_loader=lambda: __import__(
            "idis.services.ingestion.password_manifest",
            fromlist=["DocumentPasswordManifest"],
        ).DocumentPasswordManifest.none_configured(),
    )

    assert facts == module.IngestionProbeFacts(
        backend="filesystem",
        put_stream_supported=True,
        bounded_read_ok=True,
        stream_write_ok=True,
        stream_read_ok=True,
        stream_hash_ok=True,
        stream_cleanup_ok=True,
        staging_write_ok=True,
        staging_cleanup_ok=True,
        staging_free_bytes=free_bytes,
        object_store_free_bytes=free_bytes,
        shared_filesystem=True,
        password_manifest_status="none_configured",
    )
    assert not list(staging_root.iterdir())
    assert not list(object_root.rglob("*.data"))


def test_collector_reports_missing_stream_interface_without_raising(tmp_path: Path) -> None:
    module = _probes()

    class LegacyBytesOnlyStore:
        backend_name = "filesystem"

    staging_root = tmp_path / "staging"
    object_root = tmp_path / "objects"
    staging_root.mkdir()
    object_root.mkdir()

    facts = module.collect_ingestion_readiness_facts(
        store_factory=LegacyBytesOnlyStore,
        tenant_id=_TENANT_ID,
        staging_root=staging_root,
        object_store_root=object_root,
        password_manifest_loader=lambda: __import__(
            "idis.services.ingestion.password_manifest",
            fromlist=["DocumentPasswordManifest"],
        ).DocumentPasswordManifest.none_configured(),
    )
    check = _readiness().check_ingestion_readiness(
        env=_ingestion_env(),
        probe=lambda: facts,
    )

    assert facts.put_stream_supported is False
    assert facts.stream_write_ok is False
    assert check.may_proceed is False
    assert check.code == _SAFE_BLOCKER


def test_default_adapter_binds_the_strict_temp_and_object_store_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _probes()
    staging_root = (tmp_path / "staging").resolve()
    object_root = (tmp_path / "objects").resolve()
    staging_root.mkdir()
    object_root.mkdir()
    observed: dict[str, object] = {}

    def collect(**values: object) -> object:
        observed.update(values)
        return _facts(_readiness())

    monkeypatch.setattr(module, "collect_ingestion_readiness_facts", collect)
    adapters = module.build_default_dependency_probe_adapters(
        {
            "IDIS_ACCEPTANCE_TENANT_ID": _TENANT_ID,
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(object_root),
        },
        expected_public_key_sha256="a" * 64,
        staging_root=staging_root,
    )

    assert adapters.ingestion() == _facts(_readiness())
    assert observed["tenant_id"] == _TENANT_ID
    assert observed["staging_root"] == staging_root
    assert observed["object_store_root"] == object_root
    assert callable(observed["store_factory"])
    assert callable(observed["password_manifest_loader"])
    assert callable(observed["no_substantive_content_attestation_loader"])
