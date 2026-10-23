"""RED contracts for the offline Ed25519 keypair/envelope-signing helper."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from tests.slice100_task2._support import (
    BUDGET_SCHEMA_VERSION,
    RIGHTS_SCHEMA_VERSION,
    SIGNER_ID,
    budget_payload,
    error_code,
    require_attr,
    require_module,
    rights_payload,
    safe_render,
    write_json,
)


def _helper_api(name: str) -> Any:
    module = require_module("idis.tools.governance_signing")
    return require_attr(module, name)


def _helper_error_type() -> type[Exception]:
    error_type = _helper_api("GovernanceSigningError")
    assert isinstance(error_type, type) and issubclass(error_type, Exception)
    return error_type


def _envelope_api(name: str) -> Any:
    module = require_module("idis.runtime.governance_envelopes")
    return require_attr(module, name)


def _generate(private_path: Path, public_path: Path) -> object:
    return _helper_api("generate_ed25519_keypair")(
        private_key_path=private_path,
        public_key_path=public_path,
    )


def _sign_payload(
    *,
    schema_version: str,
    payload_path: Path,
    private_path: Path,
    output_path: Path,
) -> object:
    return _helper_api("sign_governance_payload")(
        schema_version=schema_version,
        signer_id=SIGNER_ID,
        payload_path=payload_path,
        private_key_path=private_path,
        output_path=output_path,
    )


def test_keypair_helper_writes_real_ed25519_pkcs8_and_spki_pem_under_tmp_path(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"

    _generate(private_path, public_path)

    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    public_key = serialization.load_pem_public_key(public_path.read_bytes())
    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, Ed25519PublicKey)
    assert private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ) == public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def test_private_permissions_are_restrictive_and_public_pem_is_read_only(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    _generate(private_path, public_path)

    if os.name != "nt":
        private_mode = stat.S_IMODE(private_path.stat().st_mode)
        public_mode = stat.S_IMODE(public_path.stat().st_mode)
        assert private_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
        assert private_mode & stat.S_IRUSR
        assert public_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0
    else:
        read_only = stat.FILE_ATTRIBUTE_READONLY
        assert public_path.stat().st_file_attributes & read_only
        for protected_path in (private_path, public_path):
            completed = subprocess.run(
                ["icacls.exe", str(protected_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            assert "(I)" not in completed.stdout
            assert re.findall(r":(?:\([A-Z,]+\))+", completed.stdout) == [":(F)"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent mode contract")
def test_keypair_helper_secures_empty_group_world_writable_parent(tmp_path: Path) -> None:
    key_dir = tmp_path / "operator-keys"
    key_dir.mkdir()
    key_dir.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    _generate(key_dir / "private.pem", key_dir / "public.pem")

    assert stat.S_IMODE(key_dir.stat().st_mode) & (stat.S_IWGRP | stat.S_IWOTH) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing contract")
def test_secure_reader_handle_denies_concurrent_write_and_path_replacement(
    tmp_path: Path,
) -> None:
    """The native read handle shares read only, excluding write and delete/rename."""
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    _generate(private_path, public_path)
    replacement = tmp_path / "replacement.pem"
    replacement.write_bytes(b"replacement")
    secure_files = __import__("idis.runtime.secure_files", fromlist=["secure_files"])
    handle = secure_files._windows_open(private_path, directory=False)
    before = secure_files._windows_identity(secure_files._windows_information(handle))
    original = private_path.read_bytes()
    try:
        with pytest.raises(OSError):
            private_path.write_bytes(b"overwritten")
        with pytest.raises(OSError):
            os.replace(replacement, private_path)
        after = secure_files._windows_identity(secure_files._windows_information(handle))
    finally:
        kernel32, _advapi32, _user32 = secure_files._windows_libraries()
        secure_files._windows_close(kernel32, handle)

    assert before == after
    assert private_path.read_bytes() == original


def test_keypair_helper_rejects_private_destination_inside_git_worktree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    key_dir = worktree / "operator-keys"
    key_dir.mkdir(parents=True)
    (worktree / ".git").mkdir()
    private_path = key_dir / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"

    with pytest.raises(_helper_error_type()) as exc_info:
        _generate(private_path, public_path)

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not private_path.exists()
    assert not public_path.exists()


def test_keypair_helper_rejects_private_destination_inside_runtime_artifact_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_store = tmp_path / "persistent-object-store"
    key_dir = object_store / "operator-keys"
    key_dir.mkdir(parents=True)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(object_store.resolve()))
    private_path = key_dir / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"

    with pytest.raises(_helper_error_type()) as exc_info:
        _generate(private_path, public_path)

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not private_path.exists()
    assert not public_path.exists()


def test_keypair_helper_never_returns_or_logs_key_material(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"

    result = _generate(private_path, public_path)

    private_pem = private_path.read_text(encoding="ascii")
    public_pem = public_path.read_text(encoding="ascii")
    captured = capsys.readouterr()
    emitted = safe_render(result) + captured.out + captured.err + caplog.text
    assert private_pem not in emitted
    assert public_pem not in emitted
    assert str(private_path) not in emitted
    assert str(public_path) not in emitted
    assert hashlib.sha256(public_path.read_bytes()).hexdigest() in safe_render(result)


def test_keypair_helper_refuses_overwrite_without_changing_existing_file(tmp_path: Path) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    sentinel = b"existing-secret-file"
    private_path.write_bytes(sentinel)

    with pytest.raises(_helper_error_type()):
        _generate(private_path, public_path)

    assert private_path.read_bytes() == sentinel
    assert not public_path.exists()


def test_keypair_helper_removes_partial_private_file_if_public_create_fails(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    sentinel = b"existing-public-file"
    public_path.write_bytes(sentinel)

    with pytest.raises(_helper_error_type()):
        _generate(private_path, public_path)

    assert not private_path.exists()
    assert public_path.read_bytes() == sentinel


def test_keypair_helper_rejects_using_one_path_for_both_keys(tmp_path: Path) -> None:
    same_path = tmp_path / "governance.pem"
    with pytest.raises(_helper_error_type()):
        _generate(same_path, same_path)
    assert not same_path.exists()


@pytest.mark.parametrize(
    ("schema_version", "payload_factory"),
    [
        (RIGHTS_SCHEMA_VERSION, rights_payload),
        (BUDGET_SCHEMA_VERSION, budget_payload),
    ],
)
def test_offline_helper_signs_each_pinned_schema_and_writes_canonical_envelope_atomically(
    tmp_path: Path,
    schema_version: str,
    payload_factory: Any,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "signed-envelope.json"
    _generate(private_path, public_path)
    write_json(payload_path, payload_factory())

    result = _sign_payload(
        schema_version=schema_version,
        payload_path=payload_path,
        private_path=private_path,
        output_path=output_path,
    )

    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(envelope) == {"schema_version", "signer_id", "payload", "signature_base64"}
    assert envelope["schema_version"] == schema_version
    assert envelope["signer_id"] == SIGNER_ID
    canonicalize = _envelope_api("canonicalize_json")
    assert output_path.read_bytes() == canonicalize(envelope)
    verify = _envelope_api("verify_envelope")
    verify(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_path.read_bytes(),
    )

    private_pem = private_path.read_text(encoding="ascii")
    rendered = safe_render(result)
    assert private_pem not in rendered
    assert str(private_path) not in rendered
    assert str(payload_path) not in rendered
    assert str(output_path) not in rendered
    assert str(envelope["signature_base64"]) not in rendered


def test_signing_invalid_payload_preserves_an_existing_output_file(tmp_path: Path) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "signed-envelope.json"
    _generate(private_path, public_path)
    payload = budget_payload()
    components = payload["components"]
    assert isinstance(components, dict)
    anthropic = components["anthropic"]
    assert isinstance(anthropic, dict)
    anthropic["max_usd"] = 1.25
    write_json(payload_path, payload)
    sentinel = b"previous-reviewed-envelope"
    output_path.write_bytes(sentinel)

    with pytest.raises(Exception) as exc_info:
        _sign_payload(
            schema_version=BUDGET_SCHEMA_VERSION,
            payload_path=payload_path,
            private_path=private_path,
            output_path=output_path,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert output_path.read_bytes() == sentinel


def test_signing_rejects_duplicate_json_members_before_creating_output(tmp_path: Path) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "signed-envelope.json"
    _generate(private_path, public_path)
    payload = budget_payload()
    components = payload["components"]
    assert isinstance(components, dict)
    component_members = [
        f"{json.dumps(component_id)}:{json.dumps(component, sort_keys=True)}"
        for component_id, component in components.items()
    ]
    component_members.append(
        f"{json.dumps('sec_edgar')}:{json.dumps(components['sec_edgar'], sort_keys=True)}"
    )
    raw_payload = (
        "{"
        + ",".join(
            [
                f"{json.dumps('tenant_id')}:{json.dumps(payload['tenant_id'])}",
                f"{json.dumps('policy_version')}:{json.dumps(payload['policy_version'])}",
                f"{json.dumps('currency')}:{json.dumps(payload['currency'])}",
                f"{json.dumps('total_max_usd')}:{json.dumps(payload['total_max_usd'])}",
                f"{json.dumps('components')}:{{{','.join(component_members)}}}",
            ]
        )
        + "}"
    )
    payload_path.write_text(raw_payload, encoding="utf-8")

    with pytest.raises(_helper_error_type()) as exc_info:
        _sign_payload(
            schema_version=BUDGET_SCHEMA_VERSION,
            payload_path=payload_path,
            private_path=private_path,
            output_path=output_path,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not output_path.exists()


def test_signing_with_invalid_private_pem_preserves_existing_output(tmp_path: Path) -> None:
    private_path = tmp_path / "governance-private.pem"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "signed-envelope.json"
    private_path.write_bytes(b"not-a-private-pem")
    write_json(payload_path, rights_payload())
    sentinel = b"previous-reviewed-envelope"
    output_path.write_bytes(sentinel)

    with pytest.raises(_helper_error_type()):
        _sign_payload(
            schema_version=RIGHTS_SCHEMA_VERSION,
            payload_path=payload_path,
            private_path=private_path,
            output_path=output_path,
        )

    assert output_path.read_bytes() == sentinel


def test_signing_rejects_private_key_as_output_without_changing_the_key(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    payload_path = tmp_path / "payload.json"
    _generate(private_path, public_path)
    write_json(payload_path, rights_payload())
    original_private_key = private_path.read_bytes()

    with pytest.raises(_helper_error_type()) as exc_info:
        _sign_payload(
            schema_version=RIGHTS_SCHEMA_VERSION,
            payload_path=payload_path,
            private_path=private_path,
            output_path=private_path,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert private_path.read_bytes() == original_private_key


def test_signing_rejects_an_insecure_existing_private_key_before_reading_it(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "governance-private.pem"
    public_path = tmp_path / "governance-public.pem"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "signed-envelope.json"
    _generate(private_path, public_path)
    write_json(payload_path, rights_payload())
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(private_path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        private_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    with pytest.raises(_helper_error_type()) as exc_info:
        _sign_payload(
            schema_version=RIGHTS_SCHEMA_VERSION,
            payload_path=payload_path,
            private_path=private_path,
            output_path=output_path,
        )

    assert error_code(exc_info.value) == "GOVERNANCE_SCHEMA_INVALID"
    assert not output_path.exists()
