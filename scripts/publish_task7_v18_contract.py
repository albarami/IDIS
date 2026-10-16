"""Crash-safe atomic publication for an authorized full-strict-v18 contract.

The signed PostgreSQL census remains the authority boundary. This module
loads a separate v18 publication-authorizer from one inspected byte buffer,
asks it to verify the receipt and derive the authorized frozen
artifact/fixture pair, independently checks that pair, and then publishes the
fixture before replacing the exact candidate artifact as the commit point.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import secrets
import stat
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Never, cast

_SCRIPT_DIR: Final = Path(__file__).resolve().parent
_AUTHORIZER_PATH: Final = _SCRIPT_DIR / "authorize_task7_v18_contract_publication.py"
_EXPECTED_AUTHORIZER_SHA256: Final = (
    "ae2937339c663cb46012307993938e90e7260910840ff9edeedaebc5a9ae8a6c"
)
# Finalized only after the authorizer pinned the final v18 runner, admission
# generator, receipt contract, candidate, signer, and governance trust root.
_PUBLICATION_PINS_FINALIZED: Final = True
_ARTIFACT_SCHEMA: Final = "full-strict-v18-reviewed-preimage-v1"
_CONTRACT_VERSION: Final = "full-strict-v18"
_EXPECTED_SIGNER_ID: Final = "slice100-governance-signer-v1"
_EXPECTED_PUBLIC_KEY_SHA256: Final = (
    "05b0e896e64b4f9344e65e1ec45309907a0ef4825c193a253877f522d216260c"
)
_FIXTURE_SCHEMA: Final = "full-strict-v18-contract-hash-fixture-v1"
_FIXTURE_FIELDS: Final = (
    "fixture_schema",
    "pipeline_contract_version",
    "artifact_sha256",
    "section_hashes",
    "component_hashes",
    "activation_assertions",
    "FULL_STRICT_V18_CONTRACT_HASH",
    "postgres_authority_census",
)
_DATABASE_EXECUTION_FIELDS: Final = (
    "application_case_execution_count",
    "admin_case_execution_count",
    "admin_fixture_helper_call_count",
    "current_guard_target_count",
    "current_guard_target_event_count",
    "case_outcome_count",
    "legal_execution_count",
    "illegal_rejection_count",
    "direct_sql_rejection_count",
    "constructor_schema_proof_count",
    "current_routine_catalog_count",
    "guard_owner_column_acl_relation_count",
    "guard_owner_column_acl_count",
    "application_direct_helper_execute_count",
    "public_direct_helper_execute_count",
    "unexpected_outcome_count",
    "catalog_projection_sha256",
    "case_result_sha256",
    "constructor_roundtrip_sha256",
    "admin_fixture_helper_projection_sha256",
    "manifest_parity_sha256",
    "pg_trigger_inventory_sha256",
    "trigger_event_coverage_sha256",
    "guard_acl_projection_sha256",
    "rls_context_proof_sha256",
    "complete_execution_sha256",
    "source_admission_execution_proof_sha256",
    "receipt_equality_execution_proof_sha256",
    "materialization_source_manifest_sha256",
)
_DATABASE_EXECUTION_COUNTS: Final = {
    "application_case_execution_count": 219,
    "admin_case_execution_count": 0,
    "admin_fixture_helper_call_count": 36,
    "current_guard_target_count": 73,
    "current_guard_target_event_count": 193,
    "case_outcome_count": 219,
    "legal_execution_count": 73,
    "illegal_rejection_count": 73,
    "direct_sql_rejection_count": 73,
    "constructor_schema_proof_count": 4,
    "current_routine_catalog_count": 24,
    "guard_owner_column_acl_relation_count": 36,
    "guard_owner_column_acl_count": 424,
    "application_direct_helper_execute_count": 0,
    "public_direct_helper_execute_count": 0,
    "unexpected_outcome_count": 0,
}
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


class PublicationError(RuntimeError):
    """Secret-safe full-strict-v18 publication failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _error(code: str, detail: str = "") -> Never:
    raise PublicationError(code, detail)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _decode_object(raw: bytes, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise PublicationError(code, "JSON decode failed") from exc
    if not isinstance(value, dict):
        _error(code, "JSON root differs")
    return value


def _render_artifact(value: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
    except (TypeError, ValueError) as exc:
        raise PublicationError(
            "TASK7_V18_AUTHORIZATION_INVALID",
            "artifact is not closed JSON",
        ) from exc
    return (rendered + "\n").encode("utf-8")


def _render_fixture(value: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PublicationError(
            "TASK7_V18_AUTHORIZATION_INVALID",
            "fixture is not closed JSON",
        ) from exc
    return (rendered + "\n").encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and frozenset(value) <= _HEX_DIGITS


def _validate_candidate_value(
    value: Mapping[str, Any],
    *,
    append_frozen: bool,
) -> None:
    metadata = value.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("artifact_schema") != _ARTIFACT_SCHEMA
        or metadata.get("pipeline_contract_version") != _CONTRACT_VERSION
        or metadata.get("append_frozen") is not append_frozen
    ):
        _error("TASK7_V18_CANDIDATE_REQUIRED", "candidate metadata differs")


def _candidate_from_current(current_raw: bytes) -> tuple[bytes, bool]:
    current = _decode_object(current_raw, code="TASK7_V18_CANDIDATE_REQUIRED")
    metadata = current.get("metadata")
    frozen = isinstance(metadata, Mapping) and metadata.get("append_frozen") is True
    _validate_candidate_value(current, append_frozen=frozen)
    if _render_artifact(current) != current_raw:
        _error("TASK7_V18_CANDIDATE_REQUIRED", "candidate rendering differs")
    if not frozen:
        return current_raw, False
    candidate = copy.deepcopy(current)
    cast(dict[str, Any], candidate["metadata"])["append_frozen"] = False
    return _render_artifact(candidate), True


def _validate_receipt_signer(receipt_raw: bytes) -> None:
    receipt = _decode_object(
        receipt_raw,
        code="TASK7_V18_POSTGRES_CENSUS_SIGNER_INVALID",
    )
    if receipt.get("signer_id") != _EXPECTED_SIGNER_ID:
        _error("TASK7_V18_POSTGRES_CENSUS_SIGNER_INVALID")


def _validate_trusted_public_key(trusted_public_key_raw: bytes) -> None:
    if not secrets.compare_digest(
        _sha256(trusted_public_key_raw),
        _EXPECTED_PUBLIC_KEY_SHA256,
    ):
        _error("TASK7_V18_POSTGRES_CENSUS_KEY_INVALID")


def _load_publication_authorizer() -> ModuleType:
    _assert_trusted_parent(_AUTHORIZER_PATH)
    _assert_regular_file(_AUTHORIZER_PATH, allow_absent=False)
    try:
        raw = _AUTHORIZER_PATH.read_bytes()
        if not secrets.compare_digest(_sha256(raw), _EXPECTED_AUTHORIZER_SHA256):
            raise PublicationError(
                "TASK7_V18_AUTHORIZER_UNAVAILABLE",
                "v18 publication-authorizer hash differs",
            )
        spec = importlib.util.spec_from_loader(
            "_task7_v18_atomic_publication_authorizer",
            loader=None,
            origin=str(_AUTHORIZER_PATH),
        )
        if spec is None:
            raise ImportError("publication-authorizer spec unavailable")
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(_AUTHORIZER_PATH)
        sys.modules[spec.name] = module
        exec(compile(raw, str(_AUTHORIZER_PATH), "exec"), module.__dict__)  # noqa: S102
    except PublicationError:
        raise
    except (ImportError, OSError, SyntaxError) as exc:
        raise PublicationError(
            "TASK7_V18_AUTHORIZER_UNAVAILABLE",
            "v18 publication-authorizer could not be loaded",
        ) from exc
    return module


def _authorize_from_pinned_module(
    *,
    artifact_raw: bytes,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    if not _PUBLICATION_PINS_FINALIZED:
        _error(
            "TASK7_V18_PUBLICATION_PINS_PENDING",
            "final v18 publication-authorizer pin is not admitted",
        )
    authorizer = _load_publication_authorizer()
    try:
        payload = authorizer.verify_postgres_census_receipt(
            receipt_raw=receipt_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )
        authorization = authorizer.authorize_fixture_publication(
            candidate_artifact=_decode_object(
                artifact_raw,
                code="TASK7_V18_CANDIDATE_REQUIRED",
            ),
            verified_census_payload=payload,
        )
    except PublicationError:
        raise
    except Exception as exc:
        raise PublicationError(
            "TASK7_V18_AUTHORIZATION_INVALID",
            "signed v18 authorization failed",
        ) from exc
    if not isinstance(authorization, Mapping):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "authorization root differs")
    return dict(authorization)


def _validate_database_execution(value: Any) -> None:
    if not isinstance(value, Mapping):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "census projection differs")
    if tuple(value) != _DATABASE_EXECUTION_FIELDS:
        _error("TASK7_V18_AUTHORIZATION_INVALID", "census field set differs")
    for field, expected in _DATABASE_EXECUTION_COUNTS.items():
        if (
            isinstance(value.get(field), bool)
            or value.get(field) != expected
            or not isinstance(value.get(field), int)
        ):
            _error("TASK7_V18_AUTHORIZATION_INVALID", "census count differs")
    for field in _DATABASE_EXECUTION_FIELDS:
        if field.endswith("_sha256") and not _is_sha256(value.get(field)):
            _error("TASK7_V18_AUTHORIZATION_INVALID", "census hash differs")


def _validate_authorization(
    *,
    candidate_raw: bytes,
    authorization: Mapping[str, Any],
) -> tuple[bytes, bytes, dict[str, Any]]:
    if tuple(authorization) != ("frozen_artifact_bytes", "fixture"):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "authorization fields differ")
    frozen_raw = authorization.get("frozen_artifact_bytes")
    fixture_value = authorization.get("fixture")
    if not isinstance(frozen_raw, bytes) or not isinstance(fixture_value, Mapping):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "authorization shape differs")

    candidate = _decode_object(
        candidate_raw,
        code="TASK7_V18_CANDIDATE_REQUIRED",
    )
    _validate_candidate_value(candidate, append_frozen=False)
    expected_frozen = copy.deepcopy(candidate)
    cast(dict[str, Any], expected_frozen["metadata"])["append_frozen"] = True
    if frozen_raw != _render_artifact(expected_frozen):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "freeze transition differs")

    frozen = _decode_object(
        frozen_raw,
        code="TASK7_V18_AUTHORIZATION_INVALID",
    )
    _validate_candidate_value(frozen, append_frozen=True)
    fixture = dict(fixture_value)
    if tuple(fixture) != _FIXTURE_FIELDS:
        _error("TASK7_V18_AUTHORIZATION_INVALID", "fixture fields differ")
    components = fixture.get("component_hashes")
    pipeline_hash = fixture.get("FULL_STRICT_V18_CONTRACT_HASH")
    if (
        fixture.get("fixture_schema") != _FIXTURE_SCHEMA
        or fixture.get("pipeline_contract_version") != _CONTRACT_VERSION
        or fixture.get("artifact_sha256") != _sha256(frozen_raw)
        or not _is_sha256(pipeline_hash)
        or not isinstance(components, Mapping)
        or components.get("pipeline_contract_hash") != pipeline_hash
    ):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "fixture binding differs")
    _validate_database_execution(fixture.get("postgres_authority_census"))
    return frozen_raw, _render_fixture(fixture), fixture


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _path_is_reparse_or_symlink(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(value.st_mode):
        return True
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_trusted_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        _error("TASK7_V18_PUBLICATION_PATH_INVALID", "target parent is missing")
    cursor = parent
    while True:
        if _path_is_reparse_or_symlink(cursor):
            _error(
                "TASK7_V18_PUBLICATION_PATH_INVALID",
                "target parent is a symlink or reparse point",
            )
        try:
            if not stat.S_ISDIR(os.lstat(cursor).st_mode):
                _error(
                    "TASK7_V18_PUBLICATION_PATH_INVALID",
                    "target ancestor is not a directory",
                )
        except OSError as exc:
            raise PublicationError(
                "TASK7_V18_PUBLICATION_PATH_INVALID",
                "target ancestor cannot be inspected",
            ) from exc
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def _assert_regular_file(path: Path, *, allow_absent: bool) -> None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        if allow_absent:
            return
        _error("TASK7_V18_PUBLICATION_PATH_INVALID", "required file is missing")
    except OSError as exc:
        raise PublicationError(
            "TASK7_V18_PUBLICATION_PATH_INVALID",
            "file cannot be inspected",
        ) from exc
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or _path_is_reparse_or_symlink(path)
    ):
        _error(
            "TASK7_V18_PUBLICATION_PATH_INVALID",
            "target is not a regular non-reparse file",
        )


def _lock_path(artifact_path: Path, fixture_path: Path | None = None) -> Path:
    del fixture_path
    path = Path(artifact_path)
    return path.with_name(f".{path.name}.task7-v18-publication.lock")


def _journal_path(artifact_path: Path, fixture_path: Path | None = None) -> Path:
    del fixture_path
    path = Path(artifact_path)
    return path.with_name(f".{path.name}.task7-v18-publication-journal.json")


def _stage_path(target: Path) -> Path:
    path = Path(target)
    return path.with_name(f".{path.name}.task7-v18-publication-stage")


def _prep_path(authoritative_sidecar: Path) -> Path:
    path = Path(authoritative_sidecar)
    return path.with_name(f".{path.name}.task7-v18-publication-prep")


def _candidate_backup_path(artifact_path: Path) -> Path:
    path = Path(artifact_path)
    return path.with_name(f".{path.name}.task7-v18-candidate-backup")


def _validate_publication_paths(
    artifact_path: Path,
    fixture_path: Path,
) -> tuple[Path, Path]:
    artifact = Path(_normalized_path(artifact_path))
    fixture = Path(_normalized_path(fixture_path))
    if _normalized_path(artifact) == _normalized_path(fixture):
        _error("TASK7_V18_PUBLICATION_PATH_INVALID", "targets alias")
    _assert_trusted_parent(artifact)
    _assert_trusted_parent(fixture)
    _assert_regular_file(artifact, allow_absent=True)
    _assert_regular_file(fixture, allow_absent=True)
    journal = _journal_path(artifact)
    artifact_stage = _stage_path(artifact)
    fixture_stage = _stage_path(fixture)
    reserved = (
        artifact,
        fixture,
        _lock_path(artifact),
        journal,
        artifact_stage,
        fixture_stage,
        _candidate_backup_path(artifact),
        _prep_path(journal),
        _prep_path(artifact_stage),
        _prep_path(fixture_stage),
    )
    identities = tuple(_normalized_path(path) for path in reserved)
    if len(set(identities)) != len(identities):
        _error("TASK7_V18_PUBLICATION_PATH_INVALID", "reserved paths alias")
    return artifact, fixture


def _sync_parent_directory(path: Path) -> str:
    """Flush directory metadata where the operating system exposes it."""

    if os.name == "nt":
        return "WINDOWS_DIRECTORY_FLUSH_NOT_CLAIMED"
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return "POSIX_DIRECTORY_FSYNC"


def _write_through_create_new(path: Path, content: bytes) -> None:
    """Create a durable file with no overwrite-capable primitive."""

    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DSYNC", getattr(os, "O_SYNC", 0))
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short durable write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_write = 0x40000000
    file_share_read = 0x00000001
    create_new = 1
    file_attribute_normal = 0x00000080
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    invalid_handle_value = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        generic_write,
        file_share_read,
        None,
        create_new,
        file_attribute_normal | FILE_FLAG_WRITE_THROUGH,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "target already exists", str(path))
        raise OSError(error, "CreateFileW failed", str(path))
    descriptor = msvcrt.open_osfhandle(
        int(handle),
        os.O_WRONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short durable write")
            view = view[written:]
        os_handle = msvcrt.get_osfhandle(descriptor)
        if not kernel32.FlushFileBuffers(wintypes.HANDLE(os_handle)):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
    finally:
        os.close(descriptor)


def _flush_existing_file(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    import ctypes
    from ctypes import wintypes

    generic_write = 0x40000000
    file_share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    invalid_handle_value = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(
        str(path),
        generic_write,
        file_share_all,
        None,
        open_existing,
        file_attribute_normal | FILE_FLAG_WRITE_THROUGH,
        None,
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "CreateFileW flush open failed")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
    finally:
        kernel32.CloseHandle(handle)


def _write_durable_exact(
    path: Path,
    content: bytes,
    *,
    mismatch_code: str,
) -> bool:
    """Publish complete sidecar bytes from a discardable preparation file."""

    _assert_trusted_parent(path)
    if path.exists():
        _assert_regular_file(path, allow_absent=False)
        if path.read_bytes() != content:
            _error(mismatch_code, "durable sidecar differs")
        return False
    prep = _prep_path(path)
    if prep.exists():
        _assert_regular_file(prep, allow_absent=False)
        prep.unlink()
        _sync_parent_directory(prep)
    try:
        _write_through_create_new(prep, content)
        _assert_regular_file(prep, allow_absent=False)
        if prep.read_bytes() != content:
            raise OSError("durable preparation bytes differ")
        try:
            os.link(prep, path, follow_symlinks=False)
            created = True
        except FileExistsError:
            _assert_regular_file(path, allow_absent=False)
            if path.read_bytes() != content:
                _error(mismatch_code, "durable sidecar differs")
            created = False
        _flush_existing_file(path)
        _sync_parent_directory(path)
        _remove_transient(prep, content)
    except FileExistsError:
        _assert_regular_file(path, allow_absent=False)
        if path.read_bytes() != content:
            _error(mismatch_code, "durable sidecar differs")
        return False
    except PublicationError:
        raise
    except OSError as exc:
        raise PublicationError(
            "TASK7_V18_ATOMIC_PUBLICATION_STAGE_FAILED",
            "durable sidecar write failed",
        ) from exc
    return created


def _remove_transient(path: Path, expected: bytes | None = None) -> None:
    if not path.exists():
        return
    _assert_regular_file(path, allow_absent=False)
    if expected is not None and path.read_bytes() != expected:
        _error("TASK7_V18_ATOMIC_PUBLICATION_CLEANUP_FAILED", "sidecar differs")
    path.unlink()
    _sync_parent_directory(path)


def _cleanup_transients(paths: tuple[tuple[Path, bytes | None], ...]) -> None:
    try:
        for path, expected in paths:
            _remove_transient(path, expected)
        if any(path.exists() for path, _expected in paths):
            raise OSError("transient publication sidecar remains")
    except (OSError, PublicationError) as exc:
        raise PublicationError(
            "TASK7_V18_ATOMIC_PUBLICATION_CLEANUP_FAILED",
            "transient publication cleanup failed",
        ) from exc


def _publish_fixture_no_clobber(stage: Path, target: Path) -> None:
    _assert_regular_file(stage, allow_absent=False)
    if target.exists():
        _error("TASK7_V18_FIXTURE_OUTPUT_ALREADY_EXISTS")
    try:
        os.link(stage, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise PublicationError(
            "TASK7_V18_FIXTURE_OUTPUT_ALREADY_EXISTS",
        ) from exc
    except OSError as exc:
        raise PublicationError(
            "TASK7_V18_ATOMIC_PUBLICATION_FAILED",
            "fixture no-clobber publication failed",
        ) from exc
    _flush_existing_file(target)
    _sync_parent_directory(target)
    _remove_transient(stage)


def _move_no_replace(source: Path, target: Path) -> None:
    """Move one regular file without an overwrite-capable OS primitive."""

    _assert_regular_file(source, allow_absent=False)
    _assert_trusted_parent(target)
    if target.exists():
        raise FileExistsError(str(target))
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        MOVEFILE_WRITE_THROUGH = 0x00000008
        ERROR_FILE_EXISTS = 80
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file = kernel32.MoveFileExW
        move_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        )
        move_file.restype = wintypes.BOOL
        if not move_file(str(source), str(target), MOVEFILE_WRITE_THROUGH):
            error = ctypes.get_last_error()
            if error in {ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS}:
                raise FileExistsError(error, "target already exists", str(target))
            raise OSError(error, "MoveFileExW no-replace move failed", str(source))
        _sync_parent_directory(target)
        return

    try:
        import ctypes
        import errno

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError):
        renameat2 = None
    if renameat2 is not None:
        AT_FDCWD = -100
        RENAME_NOREPLACE = 1
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        if (
            renameat2(
                AT_FDCWD,
                os.fsencode(source),
                AT_FDCWD,
                os.fsencode(target),
                RENAME_NOREPLACE,
            )
            != 0
        ):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(error, "target already exists", str(target))
            if error not in {errno.ENOSYS, errno.EINVAL}:
                raise OSError(error, "renameat2 no-replace move failed", str(source))
        else:
            _sync_parent_directory(target)
            return

    os.link(source, target, follow_symlinks=False)
    source.unlink()
    _sync_parent_directory(target)


def _install_frozen_artifact_no_clobber(stage: Path, target: Path) -> None:
    """Install the frozen artifact only while its live path remains absent."""

    _assert_regular_file(stage, allow_absent=False)
    expected_frozen = stage.read_bytes()
    if target.exists():
        _error(
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
            "artifact path was recreated before frozen install",
        )
    try:
        os.link(stage, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise PublicationError(
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
            "artifact path was recreated before frozen install",
        ) from exc
    except OSError as exc:
        raise PublicationError(
            "TASK7_V18_ATOMIC_PUBLICATION_FAILED",
            "artifact no-clobber publication failed",
        ) from exc
    _flush_existing_file(target)
    _sync_parent_directory(target)
    _assert_regular_file(target, allow_absent=False)
    if target.read_bytes() != expected_frozen:
        _error(
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
            "installed frozen artifact changed during finalization",
        )
    _remove_transient(stage)


def _restore_candidate_backup_if_absent(
    backup: Path,
    target: Path,
    expected_candidate: bytes,
) -> bool:
    if target.exists():
        return False
    _assert_regular_file(backup, allow_absent=False)
    if backup.read_bytes() != expected_candidate:
        _error(
            "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
            "candidate backup differs before restore",
        )
    try:
        _move_no_replace(backup, target)
    except FileExistsError:
        return False
    _assert_regular_file(target, allow_absent=False)
    if target.read_bytes() != expected_candidate:
        _error(
            "TASK7_V18_ATOMIC_PUBLICATION_ROLLBACK_MISMATCH",
            "restored candidate differs",
        )
    return True


def _commit_frozen_artifact(
    stage: Path,
    target: Path,
    expected_candidate: bytes,
) -> None:
    _assert_regular_file(stage, allow_absent=False)
    _assert_regular_file(target, allow_absent=False)
    if target.read_bytes() != expected_candidate:
        _error(
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
            "candidate bytes changed before commit",
        )
    backup = _candidate_backup_path(target)
    if backup.exists():
        _error(
            "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
            "candidate backup already exists before move",
        )
    try:
        _move_no_replace(target, backup)
    except FileExistsError as exc:
        raise PublicationError(
            "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
            "candidate backup already exists before move",
        ) from exc
    _assert_regular_file(backup, allow_absent=False)
    if backup.read_bytes() != expected_candidate:
        _error(
            "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
            "moved candidate backup differs",
        )
    try:
        _install_frozen_artifact_no_clobber(stage, target)
    except Exception:
        _restore_candidate_backup_if_absent(backup, target, expected_candidate)
        raise


def _remove_published_fixture(path: Path, expected: bytes) -> None:
    _assert_regular_file(path, allow_absent=False)
    if path.read_bytes() != expected:
        _error(
            "TASK7_V18_ATOMIC_PUBLICATION_ROLLBACK_MISMATCH",
            "published fixture differs",
        )
    path.unlink()
    _sync_parent_directory(path)


def _journal_bytes(
    *,
    artifact_path: Path,
    fixture_path: Path,
    candidate_raw: bytes,
    frozen_raw: bytes,
    fixture_raw: bytes,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> bytes:
    candidate_backup = _candidate_backup_path(artifact_path)
    value = {
        "schema_version": "task7-v18-atomic-publication-journal-v2",
        "artifact_identity_sha256": _sha256(_normalized_path(artifact_path).encode("utf-8")),
        "fixture_identity_sha256": _sha256(_normalized_path(fixture_path).encode("utf-8")),
        "candidate_artifact_sha256": _sha256(candidate_raw),
        "candidate_backup_identity_sha256": _sha256(
            _normalized_path(candidate_backup).encode("utf-8")
        ),
        "candidate_backup_sha256": _sha256(candidate_raw),
        "frozen_artifact_sha256": _sha256(frozen_raw),
        "fixture_sha256": _sha256(fixture_raw),
        "receipt_sha256": _sha256(receipt_raw),
        "trusted_public_key_sha256": _sha256(trusted_public_key_raw),
        "publisher_source_sha256": _sha256(Path(__file__).read_bytes()),
    }
    return _render_fixture(value)


@contextmanager
def _exclusive_publication_lock(artifact_path: Path) -> Iterator[None]:
    lock = _lock_path(artifact_path)
    try:
        _write_through_create_new(lock, b"\0")
        _sync_parent_directory(lock)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PublicationError(
            "TASK7_V18_PUBLICATION_LOCK_UNAVAILABLE",
            "stable publication lock could not be created",
        ) from exc
    _assert_regular_file(lock, allow_absent=False)
    if lock.read_bytes() != b"\0":
        _error(
            "TASK7_V18_PUBLICATION_LOCK_MISMATCH",
            "stable publication lock differs",
        )
    deadline = time.monotonic() + 30.0
    while True:
        try:
            stream = lock.open("r+b", buffering=0)
            break
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise PublicationError(
                    "TASK7_V18_PUBLICATION_LOCK_UNAVAILABLE",
                    "stable publication lock could not be opened",
                ) from exc
            time.sleep(0.01)
    with stream:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl = cast(Any, __import__("fcntl"))
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class _PublicationPlan:
    candidate_raw: bytes
    frozen_raw: bytes
    fixture_raw: bytes
    fixture: dict[str, Any]
    journal_raw: bytes


def _read_candidate_or_recovery_backup(artifact_path: Path) -> bytes:
    if artifact_path.exists():
        _assert_regular_file(artifact_path, allow_absent=False)
        return artifact_path.read_bytes()
    backup = _candidate_backup_path(artifact_path)
    if not backup.exists():
        _error(
            "TASK7_V18_PUBLICATION_PATH_INVALID",
            "candidate artifact is missing",
        )
    if not _journal_path(artifact_path).exists():
        _error(
            "TASK7_V18_RECOVERY_JOURNAL_REQUIRED",
            "moved candidate backup has no recovery journal",
        )
    _assert_regular_file(backup, allow_absent=False)
    _assert_regular_file(_journal_path(artifact_path), allow_absent=False)
    return backup.read_bytes()


def _derive_plan(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> _PublicationPlan:
    current_raw = _read_candidate_or_recovery_backup(artifact_path)
    candidate_raw, _is_frozen = _candidate_from_current(current_raw)
    _validate_trusted_public_key(trusted_public_key_raw)
    _validate_receipt_signer(receipt_raw)
    try:
        authorization = _authorize_from_pinned_module(
            artifact_raw=candidate_raw,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        )
    except PublicationError:
        raise
    except Exception as exc:
        raise PublicationError(
            "TASK7_V18_AUTHORIZATION_INVALID",
            "authorization seam failed",
        ) from exc
    if not isinstance(authorization, Mapping):
        _error("TASK7_V18_AUTHORIZATION_INVALID", "authorization root differs")
    frozen_raw, fixture_raw, fixture = _validate_authorization(
        candidate_raw=candidate_raw,
        authorization=authorization,
    )
    return _PublicationPlan(
        candidate_raw=candidate_raw,
        frozen_raw=frozen_raw,
        fixture_raw=fixture_raw,
        fixture=fixture,
        journal_raw=_journal_bytes(
            artifact_path=artifact_path,
            fixture_path=fixture_path,
            candidate_raw=candidate_raw,
            frozen_raw=frozen_raw,
            fixture_raw=fixture_raw,
            receipt_raw=receipt_raw,
            trusted_public_key_raw=trusted_public_key_raw,
        ),
    )


def _assert_existing_exact(path: Path, expected: bytes, code: str) -> bool:
    if not path.exists():
        return False
    _assert_regular_file(path, allow_absent=False)
    if path.read_bytes() != expected:
        _error(code, "recovery material differs")
    return True


def _rollback_before_commit(
    *,
    artifact_path: Path,
    fixture_path: Path,
    plan: _PublicationPlan,
) -> None:
    journal = _journal_path(artifact_path)
    artifact_stage = _stage_path(artifact_path)
    fixture_stage = _stage_path(fixture_path)
    backup = _candidate_backup_path(artifact_path)
    prep_paths = (
        _prep_path(journal),
        _prep_path(artifact_stage),
        _prep_path(fixture_stage),
    )
    artifact_raw = artifact_path.read_bytes() if artifact_path.exists() else None
    if artifact_raw == plan.frozen_raw:
        return
    if fixture_path.exists():
        try:
            _remove_published_fixture(fixture_path, plan.fixture_raw)
        except Exception as exc:
            raise PublicationError(
                "TASK7_V18_ATOMIC_PUBLICATION_ROLLBACK_FAILED",
                "fixture rollback failed; journal retained",
            ) from exc
    if artifact_raw is None and backup.exists():
        _restore_candidate_backup_if_absent(
            backup,
            artifact_path,
            plan.candidate_raw,
        )
        artifact_raw = artifact_path.read_bytes() if artifact_path.exists() else None
    if artifact_raw == plan.candidate_raw:
        _cleanup_transients(
            (
                (artifact_stage, plan.frozen_raw),
                (fixture_stage, plan.fixture_raw),
                (backup, plan.candidate_raw),
                *((path, None) for path in prep_paths),
                (journal, plan.journal_raw),
            )
        )


def _publish_locked(
    *,
    artifact_path: Path,
    fixture_path: Path,
    plan: _PublicationPlan,
) -> dict[str, Any]:
    journal = _journal_path(artifact_path)
    artifact_stage = _stage_path(artifact_path)
    fixture_stage = _stage_path(fixture_path)
    backup = _candidate_backup_path(artifact_path)
    prep_paths = (
        _prep_path(journal),
        _prep_path(artifact_stage),
        _prep_path(fixture_stage),
    )
    for prep in prep_paths:
        _remove_transient(prep)

    backup_exists = _assert_existing_exact(
        backup,
        plan.candidate_raw,
        "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
    )
    if artifact_path.exists():
        _assert_regular_file(artifact_path, allow_absent=False)
        current_raw = artifact_path.read_bytes()
        if current_raw == plan.candidate_raw:
            state = "pre-move"
        elif current_raw == plan.frozen_raw:
            state = "installed"
        else:
            _error(
                "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
                "artifact differs from the authorized plan",
            )
    elif backup_exists:
        state = "moved"
    else:
        _error(
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
            "candidate and its journal-bound backup are absent",
        )

    journal_exists = _assert_existing_exact(
        journal,
        plan.journal_raw,
        "TASK7_V18_RECOVERY_JOURNAL_MISMATCH",
    )
    _assert_existing_exact(
        artifact_stage,
        plan.frozen_raw,
        "TASK7_V18_ARTIFACT_STAGE_MISMATCH",
    )
    _assert_existing_exact(
        fixture_stage,
        plan.fixture_raw,
        "TASK7_V18_FIXTURE_STAGE_MISMATCH",
    )
    if state in {"moved", "installed"} and not journal_exists:
        _error(
            "TASK7_V18_RECOVERY_JOURNAL_REQUIRED",
            "moved or installed artifact has no exact recovery journal",
        )
    if state == "pre-move" and backup_exists:
        try:
            same_file = os.path.samefile(artifact_path, backup)
        except OSError:
            same_file = False
        if not journal_exists or not same_file:
            _error(
                "TASK7_V18_CANDIDATE_BACKUP_MISMATCH",
                "candidate and backup coexist before the move boundary",
            )
        artifact_path.unlink()
        _sync_parent_directory(artifact_path)
        state = "moved"

    fixture_exists = fixture_path.exists()
    if fixture_exists:
        _assert_regular_file(fixture_path, allow_absent=False)
        if fixture_path.read_bytes() != plan.fixture_raw:
            code = (
                "TASK7_V18_INTERMEDIATE_FIXTURE_CHANGED"
                if journal_exists
                else "TASK7_V18_FIXTURE_OUTPUT_ALREADY_EXISTS"
            )
            _error(code, "fixture bytes differ")
        if not journal_exists and state == "pre-move":
            _error(
                "TASK7_V18_FIXTURE_OUTPUT_ALREADY_EXISTS",
                "fixture exists without a recovery journal",
            )
    if not journal_exists and (artifact_stage.exists() or fixture_stage.exists()):
        _error(
            "TASK7_V18_RECOVERY_JOURNAL_REQUIRED",
            "orphan stage has no journal",
        )
    if not journal_exists:
        _write_durable_exact(
            journal,
            plan.journal_raw,
            mismatch_code="TASK7_V18_RECOVERY_JOURNAL_MISMATCH",
        )
        journal_exists = True

    try:
        if state == "pre-move":
            if not fixture_exists:
                _write_durable_exact(
                    fixture_stage,
                    plan.fixture_raw,
                    mismatch_code="TASK7_V18_FIXTURE_STAGE_MISMATCH",
                )
            _write_durable_exact(
                artifact_stage,
                plan.frozen_raw,
                mismatch_code="TASK7_V18_ARTIFACT_STAGE_MISMATCH",
            )
            if artifact_path.read_bytes() != plan.candidate_raw:
                _error(
                    "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
                    "candidate changed before fixture publication",
                )
            if not fixture_exists:
                _publish_fixture_no_clobber(fixture_stage, fixture_path)
            if fixture_path.read_bytes() != plan.fixture_raw:
                _error(
                    "TASK7_V18_INTERMEDIATE_FIXTURE_CHANGED",
                    "fixture changed before artifact commit",
                )
            _commit_frozen_artifact(
                artifact_stage,
                artifact_path,
                plan.candidate_raw,
            )
        elif state == "moved":
            if not fixture_exists:
                _error(
                    "TASK7_V18_RECOVERY_JOURNAL_MISMATCH",
                    "moved candidate has no fixture-first publication",
                )
            _write_durable_exact(
                artifact_stage,
                plan.frozen_raw,
                mismatch_code="TASK7_V18_ARTIFACT_STAGE_MISMATCH",
            )
            _install_frozen_artifact_no_clobber(artifact_stage, artifact_path)
        elif not fixture_exists:
            _error(
                "TASK7_V18_RECOVERY_JOURNAL_MISMATCH",
                "installed artifact has no fixture-first publication",
            )
        _assert_existing_exact(
            artifact_path,
            plan.frozen_raw,
            "TASK7_V18_CANDIDATE_CHANGED_DURING_PUBLICATION",
        )
        _assert_existing_exact(
            fixture_path,
            plan.fixture_raw,
            "TASK7_V18_INTERMEDIATE_FIXTURE_CHANGED",
        )
        _cleanup_transients(
            (
                (artifact_stage, plan.frozen_raw),
                (fixture_stage, plan.fixture_raw),
                (backup, plan.candidate_raw),
                *((path, None) for path in prep_paths),
                (journal, plan.journal_raw),
            )
        )
    except PublicationError:
        if state != "installed" and journal_exists:
            _rollback_before_commit(
                artifact_path=artifact_path,
                fixture_path=fixture_path,
                plan=plan,
            )
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        if state != "installed" and journal_exists:
            _rollback_before_commit(
                artifact_path=artifact_path,
                fixture_path=fixture_path,
                plan=plan,
            )
        raise PublicationError(
            "TASK7_V18_ATOMIC_PUBLICATION_FAILED",
            "atomic fixture/artifact publication failed",
        ) from exc
    return copy.deepcopy(plan.fixture)


def publish_candidate_atomically(
    *,
    artifact_path: Path,
    fixture_path: Path,
    receipt_raw: bytes,
    trusted_public_key_raw: bytes,
) -> dict[str, Any]:
    """Authorize, fixture-publish, and append-freeze one exact v18 candidate."""

    artifact, fixture = _validate_publication_paths(
        Path(artifact_path),
        Path(fixture_path),
    )
    current_raw = _read_candidate_or_recovery_backup(artifact)
    _candidate_from_current(current_raw)
    _validate_trusted_public_key(trusted_public_key_raw)
    _validate_receipt_signer(receipt_raw)
    plan = _derive_plan(
        artifact_path=artifact,
        fixture_path=fixture,
        receipt_raw=receipt_raw,
        trusted_public_key_raw=trusted_public_key_raw,
    )
    with _exclusive_publication_lock(artifact):
        return _publish_locked(
            artifact_path=artifact,
            fixture_path=fixture,
            plan=plan,
        )


def _read_trusted_input(path: Path) -> bytes:
    normalized = Path(_normalized_path(path))
    _assert_trusted_parent(normalized)
    _assert_regular_file(normalized, allow_absent=False)
    return normalized.read_bytes()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-census-receipt", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        publish_candidate_atomically(
            artifact_path=args.artifact,
            fixture_path=args.output,
            receipt_raw=_read_trusted_input(args.authority_census_receipt),
            trusted_public_key_raw=_read_trusted_input(args.trusted_public_key),
        )
    except (OSError, PublicationError, TypeError, UnicodeError, ValueError) as exc:
        code = (
            exc.code
            if isinstance(exc, PublicationError)
            else ("TASK7_V18_ATOMIC_PUBLICATION_FAILED")
        )
        print(code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PublicationError",
    "main",
    "publish_candidate_atomically",
]
