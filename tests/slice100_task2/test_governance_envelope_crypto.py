"""RED contracts for RFC8785-compatible Ed25519 governance envelopes."""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.slice100_task2._support import (
    BUDGET_SCHEMA_VERSION,
    RIGHTS_SCHEMA_VERSION,
    SIGNER_ID,
    budget_payload,
    error_code,
    require_attr,
    require_module,
    rights_payload,
)


def _module() -> object:
    return require_module("idis.runtime.governance_envelopes")


def _api(name: str) -> Any:
    return require_attr(_module(), name)


def _key_material() -> tuple[bytes, bytes]:
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
    return private_pem, public_pem


def _canonicalize(value: object) -> bytes:
    result = _api("canonicalize_json")(value)
    assert isinstance(result, bytes), "canonicalize_json must return UTF-8 bytes"
    return result


def _sign(
    *, schema_version: str, payload: Mapping[str, object], private_key_pem: bytes
) -> dict[str, object]:
    result = _api("sign_envelope")(
        schema_version=schema_version,
        signer_id=SIGNER_ID,
        payload=payload,
        private_key_pem=private_key_pem,
    )
    assert isinstance(result, dict)
    return result


def _verify(envelope: Mapping[str, object], public_key_pem: bytes) -> object:
    return _api("verify_envelope")(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_key_pem,
    )


def _assert_error_code(call: Callable[[], object], expected_code: str) -> BaseException:
    with pytest.raises(Exception) as exc_info:
        call()
    assert error_code(exc_info.value) == expected_code
    return exc_info.value


def _result_value(result: object, name: str) -> object:
    value = getattr(result, name, None)
    if value is None and isinstance(result, Mapping):
        value = result.get(name)
    assert value is not None, f"verified envelope must expose {name}"
    return value


def test_canonical_json_uses_utf16_property_order_not_python_codepoint_order() -> None:
    value = {"\ue000": 1, "😀": 2, "€": 3}
    assert _canonicalize(value) == '{"€":3,"😀":2,"\ue000":1}'.encode()


def test_canonical_json_uses_minimal_rfc8785_string_and_literal_encoding() -> None:
    value = {
        "z": '\u000f\n"\\/€',
        "a": [None, True, False, 0, 17, -3],
    }
    expected = '{"a":[null,true,false,0,17,-3],"z":"\\u000f\\n\\"\\\\/€"}'
    assert _canonicalize(value) == expected.encode("utf-8")


@pytest.mark.parametrize("value", [1.0, -0.0, 1.25, float("inf"), float("-inf"), float("nan")])
def test_restricted_envelope_canonicalizer_rejects_all_floats(value: float) -> None:
    _assert_error_code(
        lambda: _canonicalize({"payload": {"not_allowed": value}}),
        "GOVERNANCE_SCHEMA_INVALID",
    )


def test_rights_envelope_has_exact_top_level_shape_and_a_real_ed25519_signature() -> None:
    private_pem, public_pem = _key_material()
    payload = rights_payload()

    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=payload,
        private_key_pem=private_pem,
    )

    assert set(envelope) == {"schema_version", "signer_id", "payload", "signature_base64"}
    assert envelope["schema_version"] == RIGHTS_SCHEMA_VERSION
    assert envelope["signer_id"] == SIGNER_ID
    assert envelope["payload"] == payload
    signature = base64.b64decode(str(envelope["signature_base64"]), validate=True)
    assert len(signature) == 64
    _verify(envelope, public_pem)


def test_budget_envelope_uses_the_same_pinned_trust_contract() -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=BUDGET_SCHEMA_VERSION,
        payload=budget_payload(),
        private_key_pem=private_pem,
    )

    assert set(envelope) == {"schema_version", "signer_id", "payload", "signature_base64"}
    assert envelope["schema_version"] == BUDGET_SCHEMA_VERSION
    _verify(envelope, public_pem)


def test_signature_covers_exactly_schema_signer_and_payload_canonical_bytes() -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    signable = {
        "schema_version": envelope["schema_version"],
        "signer_id": envelope["signer_id"],
        "payload": envelope["payload"],
    }
    signature = base64.b64decode(str(envelope["signature_base64"]), validate=True)
    public_key = serialization.load_pem_public_key(public_pem)
    public_key.verify(signature, _canonicalize(signable))


def test_equivalent_member_order_produces_the_same_deterministic_signature() -> None:
    private_pem, public_pem = _key_material()
    payload = rights_payload()
    reordered_payload = {"approvals": payload["approvals"], "tenant_id": payload["tenant_id"]}

    first = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=payload,
        private_key_pem=private_pem,
    )
    second = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=reordered_payload,
        private_key_pem=private_pem,
    )

    assert first["signature_base64"] == second["signature_base64"]
    assert _canonicalize(first) == _canonicalize(second)
    _verify(second, public_pem)


def test_verified_envelope_exposes_exact_safe_hash_provenance() -> None:
    private_pem, public_pem = _key_material()
    payload = rights_payload()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=payload,
        private_key_pem=private_pem,
    )

    verified = _verify(envelope, public_pem)

    assert _result_value(verified, "schema_version") == RIGHTS_SCHEMA_VERSION
    assert _result_value(verified, "signer_id") == SIGNER_ID
    assert (
        _result_value(verified, "payload_sha256")
        == hashlib.sha256(_canonicalize(payload)).hexdigest()
    )
    assert (
        _result_value(verified, "envelope_sha256")
        == hashlib.sha256(_canonicalize(envelope)).hexdigest()
    )
    assert _result_value(verified, "public_key_sha256") == hashlib.sha256(public_pem).hexdigest()


def test_payload_mutation_after_signing_fails_signature_verification() -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    tampered = copy.deepcopy(envelope)
    payload = tampered["payload"]
    assert isinstance(payload, dict)
    payload["tenant_id"] = "10000000-0000-4000-8000-000000000099"

    _assert_error_code(
        lambda: _verify(tampered, public_pem),
        "GOVERNANCE_SIGNATURE_INVALID",
    )


def test_wrong_public_key_fails_with_signature_code() -> None:
    private_pem, _public_pem = _key_material()
    _other_private, other_public = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    _assert_error_code(
        lambda: _verify(envelope, other_public),
        "GOVERNANCE_SIGNATURE_INVALID",
    )


def test_untrusted_signer_id_is_distinct_from_bad_signature() -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    verify = _api("verify_envelope")
    _assert_error_code(
        lambda: verify(
            envelope=envelope,
            trusted_signer_id="different-trusted-signer",
            trusted_public_key_pem=public_pem,
        ),
        "GOVERNANCE_SIGNER_UNTRUSTED",
    )


@pytest.mark.parametrize("bad_signature", ["", "not-base64!", "YQ=="])
def test_malformed_signature_uses_stable_signature_code(bad_signature: str) -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    envelope["signature_base64"] = bad_signature
    _assert_error_code(
        lambda: _verify(envelope, public_pem),
        "GOVERNANCE_SIGNATURE_INVALID",
    )


@pytest.mark.parametrize("mutation", ["missing_signature", "extra_field", "wrong_version"])
def test_envelope_shape_and_version_fail_with_schema_code(mutation: str) -> None:
    private_pem, public_pem = _key_material()
    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=rights_payload(),
        private_key_pem=private_pem,
    )
    if mutation == "missing_signature":
        del envelope["signature_base64"]
    elif mutation == "extra_field":
        envelope["unreviewed"] = True
    else:
        envelope["schema_version"] = "enrichment-rights-approvals-v2"

    _assert_error_code(
        lambda: _verify(envelope, public_pem),
        "GOVERNANCE_SCHEMA_INVALID",
    )


def test_signing_rejects_float_payload_before_any_signature_is_created() -> None:
    private_pem, _public_pem = _key_material()
    payload = budget_payload()
    components = payload["components"]
    assert isinstance(components, dict)
    anthropic = components["anthropic"]
    assert isinstance(anthropic, dict)
    anthropic["max_usd"] = 1.25

    _assert_error_code(
        lambda: _sign(
            schema_version=BUDGET_SCHEMA_VERSION,
            payload=payload,
            private_key_pem=private_pem,
        ),
        "GOVERNANCE_SCHEMA_INVALID",
    )


@pytest.mark.parametrize(
    "expires_at",
    ["2026-07-15T00:00:00Z", "2026-07-14T23:59:59Z"],
    ids=["equal-to-approval", "before-approval"],
)
def test_rights_verification_rejects_nonpositive_approval_window(expires_at: str) -> None:
    """A valid signature cannot admit a zero-length or inverted rights window."""
    private_pem, public_pem = _key_material()
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    approval = approvals[0]
    assert isinstance(approval, dict)
    approval["expires_at"] = expires_at
    signable = {
        "schema_version": RIGHTS_SCHEMA_VERSION,
        "signer_id": SIGNER_ID,
        "payload": payload,
    }
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    envelope = {
        **signable,
        "signature_base64": base64.b64encode(private_key.sign(_canonicalize(signable))).decode(
            "ascii"
        ),
    }

    _assert_error_code(
        lambda: _verify(envelope, public_pem),
        "GOVERNANCE_SCHEMA_INVALID",
    )


def test_rights_envelope_accepts_an_exact_signed_revocation_snapshot() -> None:
    """Revocation is a signed current state, not a mutation of unsigned runtime state."""
    private_pem, public_pem = _key_material()
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-15T12:00:00Z"

    envelope = _sign(
        schema_version=RIGHTS_SCHEMA_VERSION,
        payload=payload,
        private_key_pem=private_pem,
    )

    verified = _verify(envelope, public_pem)
    assert _result_value(verified, "payload") == payload


def test_rights_verification_rejects_revocation_before_approval() -> None:
    """A signed revocation timestamp cannot predate the approval it revokes."""
    private_pem, public_pem = _key_material()
    payload = rights_payload()
    approvals = payload["approvals"]
    assert isinstance(approvals, list)
    revoked = approvals[0]
    assert isinstance(revoked, dict)
    revoked["status"] = "REVOKED"
    revoked["revoked_at"] = "2026-07-14T23:59:59Z"
    signable = {
        "schema_version": RIGHTS_SCHEMA_VERSION,
        "signer_id": SIGNER_ID,
        "payload": payload,
    }
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    envelope = {
        **signable,
        "signature_base64": base64.b64encode(private_key.sign(_canonicalize(signable))).decode(
            "ascii"
        ),
    }

    _assert_error_code(
        lambda: _verify(envelope, public_pem),
        "GOVERNANCE_SCHEMA_INVALID",
    )
