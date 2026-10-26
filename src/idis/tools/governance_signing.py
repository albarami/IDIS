"""Offline Ed25519 key generation and governance-envelope signing."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.runtime.governance_envelopes import (
    GOVERNANCE_SCHEMA_INVALID,
    GovernanceEnvelopeError,
    canonicalize_json,
    sign_envelope,
)
from idis.runtime.secure_files import (
    SecureFileError,
    path_acl_is_current_owner_only,
    protect_path_for_current_user,
    secure_read_integrity_file,
)


class GovernanceSigningError(RuntimeError):
    """Secret-safe offline-helper failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class KeypairGenerationResult:
    """Safe key-generation result containing no key bytes or paths."""

    algorithm: str
    public_key_sha256: str


@dataclass(frozen=True, slots=True)
class GovernanceSigningResult:
    """Safe signing result containing hashes only."""

    schema_version: str
    signer_id: str
    payload_sha256: str
    envelope_sha256: str


class _InvalidJson(ValueError):
    pass


_RUNTIME_ARTIFACT_ROOT_ENVS = ("IDIS_OBJECT_STORE_BASE_DIR",)


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJson("duplicate member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _InvalidJson("non-finite number")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_private_key_location(path: Path) -> Path:
    """Return a canonical private-key path outside source and artifact roots."""

    if not path.is_absolute():
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    resolved = path.resolve(strict=False)
    for ancestor in (resolved.parent, *resolved.parents):
        if (ancestor / ".git").exists():
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    for env_name in _RUNTIME_ARTIFACT_ROOT_ENVS:
        raw_root = os.environ.get(env_name, "").strip()
        if not raw_root:
            continue
        root = Path(raw_root)
        if not root.is_absolute():
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
        if _within(resolved, root.resolve(strict=False)):
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    return resolved


def windows_owner_only_acl_is_protected(path: Path) -> bool:
    """Return whether ``path`` has one protected full-control ACE for this user."""

    return path_acl_is_current_owner_only(path)


def _apply_private_key_security(path: Path, descriptor: int) -> None:
    if os.name == "nt":
        try:
            # The file is still held by the exclusive writer here.  Apply the DACL
            # now and verify it after that write handle closes.
            protect_path_for_current_user(path, verify=False)
        except SecureFileError as exc:
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc
        return
    fchmod = getattr(os, "fchmod", None)
    if not callable(fchmod):
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)


def _exclusive_write(path: Path, data: bytes, mode: int, *, private: bool = False) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    completed = False
    try:
        if private:
            _apply_private_key_security(path, descriptor)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if private and os.name == "nt" and not path_acl_is_current_owner_only(path):
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
        completed = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not completed:
            with suppress(OSError):
                path.unlink()


def _remove_created_file(path: Path) -> None:
    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    with suppress(OSError):
        path.unlink()


def _make_public_read_only(path: Path) -> None:
    if os.name == "nt":
        path.chmod(stat.S_IREAD)
    else:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _prepare_key_parent(parent: Path) -> None:
    """Require or create a dedicated current-owner integrity boundary for keys."""

    try:
        if os.name != "nt":
            metadata = parent.stat()
            geteuid = getattr(os, "geteuid", None)
            if not callable(geteuid) or metadata.st_uid != geteuid():
                raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
            if not stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
                return
        elif path_acl_is_current_owner_only(parent, directory=True):
            return
        if any(parent.iterdir()):
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
        protect_path_for_current_user(parent, directory=True)
        if os.name != "nt":
            mode = stat.S_IMODE(parent.stat().st_mode)
            if mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    except GovernanceSigningError:
        raise
    except (OSError, SecureFileError) as exc:
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc


def generate_ed25519_keypair(
    *, private_key_path: Path, public_key_path: Path
) -> KeypairGenerationResult:
    """Create a new keypair using exclusive files and restrictive permissions."""

    private_path = Path(private_key_path)
    public_path = Path(public_key_path)
    _validate_private_key_location(private_path)
    if private_path.resolve(strict=False) == public_path.resolve(strict=False):
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    if private_path.exists() or public_path.exists():
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    if not private_path.parent.is_dir() or not public_path.parent.is_dir():
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    for parent in {private_path.parent, public_path.parent}:
        _prepare_key_parent(parent)

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_created = False
    public_created = False
    try:
        _exclusive_write(
            private_path,
            private_pem,
            stat.S_IRUSR | stat.S_IWUSR,
            private=True,
        )
        private_created = True
        # The public key is integrity-sensitive configuration.  A read-only DOS
        # attribute alone does not stop another Windows principal from replacing it.
        _exclusive_write(public_path, public_pem, stat.S_IRUSR, private=True)
        public_created = True
        _make_public_read_only(public_path)
    except OSError as exc:
        if public_created:
            _remove_created_file(public_path)
        if private_created:
            _remove_created_file(private_path)
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc

    return KeypairGenerationResult(
        algorithm="Ed25519",
        public_key_sha256=hashlib.sha256(public_pem).hexdigest(),
    )


def _load_payload(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(
            raw,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _InvalidJson) as exc:
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc
    if not isinstance(parsed, dict):
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    return parsed


def _atomic_replace(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    try:
        _exclusive_write(temporary, data, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    except OSError as exc:
        with suppress(OSError):
            temporary.unlink()
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc


def _paths_alias(left: Path, right: Path) -> bool:
    """Fail safely when two caller-selected paths name the same file."""

    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError as exc:
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc


def _read_secure_private_key(path: Path) -> bytes:
    private_path = _validate_private_key_location(path)
    if not private_path.is_file() or private_path.is_symlink():
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    if os.name == "nt":
        try:
            return secure_read_integrity_file(
                str(private_path),
                max_bytes=16 * 1024,
                require_readonly=False,
                require_secure_parent=True,
            )
        except SecureFileError as exc:
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(private_path, flags)
    except OSError as exc:
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        geteuid = getattr(os, "geteuid", None)
        if not callable(geteuid) or (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != geteuid()
            or mode != stat.S_IRUSR | stat.S_IWUSR
        ):
            raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def sign_governance_payload(
    *,
    schema_version: str,
    signer_id: str,
    payload_path: Path,
    private_key_path: Path,
    output_path: Path,
) -> GovernanceSigningResult:
    """Validate, sign, and atomically write one exact governance envelope."""

    payload_input = Path(payload_path)
    private_input = Path(private_key_path)
    output = Path(output_path)
    if _paths_alias(private_input, output) or _paths_alias(payload_input, output):
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)

    payload = _load_payload(payload_input)
    try:
        private_pem = _read_secure_private_key(private_input)
        envelope = sign_envelope(
            schema_version=schema_version,
            signer_id=signer_id,
            payload=payload,
            private_key_pem=private_pem,
        )
    except GovernanceEnvelopeError as exc:
        raise GovernanceSigningError(exc.code) from exc
    except OSError as exc:
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID) from exc

    envelope_bytes = canonicalize_json(envelope)
    if not output.parent.is_dir():
        raise GovernanceSigningError(GOVERNANCE_SCHEMA_INVALID)
    _atomic_replace(output, envelope_bytes)
    return GovernanceSigningResult(
        schema_version=schema_version,
        signer_id=signer_id,
        payload_sha256=hashlib.sha256(canonicalize_json(payload)).hexdigest(),
        envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
    )


__all__ = [
    "GovernanceSigningError",
    "GovernanceSigningResult",
    "KeypairGenerationResult",
    "generate_ed25519_keypair",
    "sign_governance_payload",
    "windows_owner_only_acl_is_protected",
]
