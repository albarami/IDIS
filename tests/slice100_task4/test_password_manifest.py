"""Task 4 RED contract for password-required and corrupt terminal dispositions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from idis.parsers.base import ParseErrorCode
from idis.parsers.pdf import parse_pdf
from tests.test_pdf_parser import create_encrypted_test_pdf

_LOCKED_PASSWORD = "-".join(("fictional", "correct", "credential"))


def _target() -> object:
    from idis.services.ingestion import password_manifest

    return password_manifest


def _seal(path: Path) -> None:
    from idis.runtime.secure_files import protect_path_for_current_user

    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


def test_absent_manifest_is_none_configured_none_required() -> None:
    module = _target()
    manifest = module.load_document_password_manifest(env={})

    assert manifest.status.value == "none_configured"
    assert manifest.entry_count == 0
    assert manifest.resolve("0" * 64) is None
    assert manifest.safe_summary() == {
        "schema_version": "document-password-manifest-v1",
        "status": "none_configured",
        "entry_count": 0,
    }


def test_empty_password_openable_pdf_remains_success_without_manifest() -> None:
    data = create_encrypted_test_pdf(
        ["FICTIONAL EMPTY PASSWORD EVIDENCE"],
        user_password="",
    )

    result = parse_pdf(data)

    assert result.success is True
    assert result.private_diagnostics["pdf_diagnostic_reason"] == (
        "parsed_empty_password_encrypted"
    )


def test_password_required_and_wrong_password_are_distinct_safe_failures() -> None:
    data = create_encrypted_test_pdf(
        ["FICTIONAL LOCKED EVIDENCE"],
        user_password=_LOCKED_PASSWORD,
    )

    missing = parse_pdf(data)
    wrong_credential = "-".join(("fictional", "wrong", "credential"))
    wrong = parse_pdf(data, password=wrong_credential)

    assert [error.code for error in missing.errors] == [ParseErrorCode.ENCRYPTED_PDF]
    assert missing.errors[0].details == {"password_disposition": "required"}
    assert [error.code for error in wrong.errors] == [ParseErrorCode.ENCRYPTED_PDF]
    assert wrong.errors[0].details == {"password_disposition": "invalid"}
    encoded = json.dumps([missing.to_dict(), wrong.to_dict()])
    assert _LOCKED_PASSWORD not in encoded
    assert wrong_credential not in encoded


def test_correct_manifest_password_unlocks_by_content_hash() -> None:
    module = _target()
    data = create_encrypted_test_pdf(
        ["FICTIONAL UNLOCKED EVIDENCE"],
        user_password=_LOCKED_PASSWORD,
    )
    digest = hashlib.sha256(data).hexdigest()
    manifest = module.DocumentPasswordManifest.from_mapping({digest: _LOCKED_PASSWORD})

    result = parse_pdf(data, password=manifest.resolve(digest))

    assert result.success is True
    assert result.private_diagnostics["pdf_diagnostic_reason"] == ("parsed_password_encrypted")
    assert _LOCKED_PASSWORD not in repr(manifest)
    assert _LOCKED_PASSWORD not in json.dumps(manifest.safe_summary())


def test_ingestion_service_resolves_password_only_by_validated_content_hash() -> None:
    module = _target()
    from idis.services.ingestion.service import IngestionService

    data = create_encrypted_test_pdf(
        ["FICTIONAL SERVICE UNLOCK EVIDENCE"],
        user_password=_LOCKED_PASSWORD,
    )
    digest = hashlib.sha256(data).hexdigest()
    service = IngestionService(
        compliant_store=object(),  # type: ignore[arg-type]
        password_manifest=module.DocumentPasswordManifest.from_mapping({digest: _LOCKED_PASSWORD}),
    )

    result = service._parse_document(  # noqa: SLF001 -- contract boundary under test.
        data,
        "fictional-locked.pdf",
        "application/pdf",
        sha256=digest,
    )

    assert result.success is True
    assert result.private_diagnostics["pdf_diagnostic_reason"] == ("parsed_password_encrypted")


def test_http_terminal_codes_distinguish_missing_wrong_and_corrupt() -> None:
    from idis.api.routes.documents import _terminal_upload_error_code

    missing = SimpleNamespace(
        details={
            "parse_error_code": "encrypted_pdf",
            "password_disposition": "required",
        }
    )
    wrong = SimpleNamespace(
        details={
            "parse_error_code": "encrypted_pdf",
            "password_disposition": "invalid",
        }
    )
    corrupt = SimpleNamespace(details={"parse_error_code": "corrupted_file"})

    assert _terminal_upload_error_code([missing]) == "DOCUMENT_PASSWORD_REQUIRED"
    assert _terminal_upload_error_code([wrong]) == "DOCUMENT_PASSWORD_INVALID"
    assert _terminal_upload_error_code([corrupt]) == "DOCUMENT_UNREADABLE"


def test_secure_manifest_loader_never_surfaces_path_or_password(tmp_path: Path) -> None:
    module = _target()
    digest = "a" * 64
    path = tmp_path / "passwords.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "document-password-manifest-v1",
                "passwords_by_sha256": {digest: _LOCKED_PASSWORD},
            }
        ),
        encoding="utf-8",
    )
    _seal(path)

    manifest = module.load_document_password_manifest(
        env={"IDIS_REAL_EXAMPLE_PASSWORD_MANIFEST_PATH": str(path.resolve())}
    )

    assert manifest.resolve(digest) == _LOCKED_PASSWORD
    safe = json.dumps(manifest.safe_summary(), sort_keys=True) + repr(manifest)
    assert str(path) not in safe
    assert _LOCKED_PASSWORD not in safe


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": "wrong", "passwords_by_sha256": {}},
        {
            "schema_version": "document-password-manifest-v1",
            "passwords_by_sha256": {"not-a-hash": "fictional-password"},
        },
    ],
)
def test_invalid_manifest_fails_closed_without_echoing_values(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    module = _target()
    path = tmp_path / "invalid-passwords.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _seal(path)

    with pytest.raises(module.DocumentPasswordManifestError) as caught:
        module.load_document_password_manifest(
            env={"IDIS_REAL_EXAMPLE_PASSWORD_MANIFEST_PATH": str(path.resolve())}
        )

    assert caught.value.code == "DOCUMENT_PASSWORD_MANIFEST_INVALID"
    assert str(path) not in str(caught.value)
    assert "fictional-password" not in str(caught.value)
