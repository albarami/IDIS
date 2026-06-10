"""Slice85 Task 2 — AES-256-GCM hardening of BYOL credential encryption (RED-first).

Replaces the stdlib XOR+HMAC cipher in
``idis.persistence.repositories.enrichment_credentials`` with AES-256-GCM via the
``cryptography`` library (declared as an explicit, BOUNDED dependency — formalizing the
implicit dependency auth_sso.py already relies on). Contracts preserved:
  - same env var ``IDIS_ENRICHMENT_ENCRYPTION_KEY`` (SHA256-derived 32-byte key = AES-256);
  - missing key -> ``EncryptionKeyMissingError`` (encrypt AND decrypt);
  - tamper / garbage / legacy-format / wrong-key -> ``ValueError`` (fail-closed, no leak);
  - no plaintext in ciphertext; repository interfaces unchanged (str in/out).
New: an explicit versioned ciphertext format (``v2:`` prefix) so the cipher change is
self-describing; unversioned (legacy XOR) blobs fail closed — no production data exists
(pinned by Task 1 / plan §3 G6), so no migration is required. No real provider calls.
"""

from __future__ import annotations

import inspect
import os
import re
from base64 import b64decode, b64encode
from pathlib import Path
from unittest.mock import patch

import pytest

from idis.persistence.repositories.enrichment_credentials import (
    EncryptionKeyMissingError,
    decrypt_credentials,
    encrypt_credentials,
)

_KEY_ENV = "IDIS_ENRICHMENT_ENCRYPTION_KEY"
_KEY_A = "slice85-key-A-LEAK-marker"
_KEY_B = "slice85-key-B-different"
_PLAINTEXT = {"api_key": "round-trip-secret-LEAK", "token": "unicode-✓-value"}


def _env_with_key(value: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != _KEY_ENV}
    env[_KEY_ENV] = value
    return env


def _env_without_key() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != _KEY_ENV}


# --- dependency formalized: explicit, bounded pin ---


def test_cryptography_declared_in_pyproject_with_bounded_pin() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"cryptography(?P<spec>[^"]*)"', pyproject)
    assert match is not None, "cryptography must be a declared dependency"
    spec = match.group("spec")
    assert ">=" in spec  # explicit floor
    assert "<" in spec  # bounded ceiling — no open upper bound (pypdf lesson)


# --- versioned ciphertext format ---


def test_ciphertext_is_versioned_v2() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
    assert ciphertext.startswith("v2:")


def test_two_encryptions_differ_fresh_nonce_each_time() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        first = encrypt_credentials(_PLAINTEXT)
        second = encrypt_credentials(_PLAINTEXT)
    assert first != second  # unique nonce per encryption (GCM requirement)


# --- round trip + no plaintext at rest ---


def test_round_trip_preserves_credentials() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
        assert decrypt_credentials(ciphertext) == _PLAINTEXT


def test_ciphertext_carries_no_plaintext_or_key_material() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
    for marker in ("round-trip-secret-LEAK", "api_key", _KEY_A):
        assert marker not in ciphertext


# --- fail-closed: tamper / garbage / legacy / wrong key -> ValueError ---


def _tampered(ciphertext: str, index: int) -> str:
    payload = bytearray(b64decode(ciphertext.removeprefix("v2:")))
    payload[index] ^= 0xFF
    return "v2:" + b64encode(bytes(payload)).decode("ascii")


def test_ciphertext_tamper_fails_closed() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
        with pytest.raises(ValueError):
            decrypt_credentials(_tampered(ciphertext, 20))  # inside sealed payload


def test_nonce_tamper_fails_closed() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
        with pytest.raises(ValueError):
            decrypt_credentials(_tampered(ciphertext, 3))  # inside the 12-byte nonce


def test_garbage_and_legacy_unversioned_ciphertext_fail_closed() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        with pytest.raises(ValueError):
            decrypt_credentials("not-even-base64-:::")
        with pytest.raises(ValueError):
            decrypt_credentials("dGVzdA==")  # valid base64, no version prefix (legacy shape)
        with pytest.raises(ValueError):
            decrypt_credentials("v2:dGVzdA==")  # versioned but malformed payload


def test_wrong_key_fails_closed_without_leak() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
    with (
        patch.dict(os.environ, _env_with_key(_KEY_B), clear=True),
        pytest.raises(ValueError) as exc_info,
    ):
        decrypt_credentials(ciphertext)
    surfaced = f"{exc_info.value!s}|{exc_info.value!r}"
    for marker in ("round-trip-secret-LEAK", _KEY_A, _KEY_B):
        assert marker not in surfaced


# --- fail-closed: missing key -> EncryptionKeyMissingError (encrypt AND decrypt) ---


def test_missing_key_fails_closed_on_encrypt_and_decrypt() -> None:
    with patch.dict(os.environ, _env_with_key(_KEY_A), clear=True):
        ciphertext = encrypt_credentials(_PLAINTEXT)
    with patch.dict(os.environ, _env_without_key(), clear=True):
        with pytest.raises(EncryptionKeyMissingError):
            encrypt_credentials(_PLAINTEXT)
        with pytest.raises(EncryptionKeyMissingError):
            decrypt_credentials(ciphertext)


# --- implementation truth: AES-GCM via cryptography; XOR cipher gone ---


def test_implementation_is_aes_gcm_via_cryptography() -> None:
    encrypt_source = inspect.getsource(encrypt_credentials)
    decrypt_source = inspect.getsource(decrypt_credentials)
    assert "AESGCM" in encrypt_source
    assert "AESGCM" in decrypt_source
    assert "XOR" not in encrypt_source
    assert "XOR" not in decrypt_source
    import idis.persistence.repositories.enrichment_credentials as module

    assert "from cryptography" in inspect.getsource(module)
