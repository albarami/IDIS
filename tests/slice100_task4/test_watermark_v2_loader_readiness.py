"""Production loader/readiness contract for the signed watermark manifest v2.

The fixture is fully local and fictional.  It signs real Ed25519 envelopes,
hardens the manifest and trust-root paths with the production ACL helper, and
uses the production filesystem store.  No provider, database, or API call is
made.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from idis.runtime.governance_envelopes import canonicalize_json
from idis.runtime.secure_files import protect_path_for_current_user
from idis.services.ingestion.no_substantive_content_attestation import (
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from idis.services.ingestion.password_manifest import DocumentPasswordManifest
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4 import test_watermark_batch_attestation_red as batch_contract
from tests.slice100_task4.test_ingestion_readiness import _ingestion_env

_TENANT_ID = "30000000-0000-4000-8000-000000000001"
_INGEST_MAX_BYTES = 536_870_912


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


def _seal(path: Path) -> None:
    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


def _write_secure_fixture(
    tmp_path: Path,
    *,
    mutate: Any | None = None,
) -> tuple[dict[str, str], dict[str, object], bytes]:
    private_pem, public_pem = batch_contract._private_and_public_pem()
    manifest = batch_contract._manifest(private_pem)
    if mutate is not None:
        mutate(manifest)

    secure_root = tmp_path / "fictional-governance"
    secure_root.mkdir()
    manifest_path = secure_root / "watermark-admission-v2.json"
    public_key_path = secure_root / "trusted-ed25519-public.pem"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    public_key_path.write_bytes(public_pem)
    _seal(manifest_path)
    _seal(public_key_path)
    env = {
        "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH": str(manifest_path),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": batch_contract.SIGNER_ID,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_key_path),
    }
    return env, manifest, public_pem


def test_loader_accepts_canonical_sorted_manifest_member_order(tmp_path: Path) -> None:
    """Canonical governance JSON sorts members; object order is non-semantic."""

    env, manifest, _public_pem = _write_secure_fixture(tmp_path)
    manifest_path = Path(env["IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH"])
    manifest_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    manifest_path.write_bytes(canonicalize_json(manifest))
    _seal(manifest_path)

    loaded = load_document_no_substantive_content_attestation(
        env=env,
        now=batch_contract.ACTIVE_NOW,
    )

    assert isinstance(loaded, VerifiedDocumentNoSubstantiveContentAttestationManifest)
    assert loaded.manifest_sha256 == _sha256_json(manifest)


def _expected_safe_summary(
    manifest: dict[str, object],
    *,
    public_pem: bytes,
) -> dict[str, object]:
    exemplar = manifest["exemplar_attestation_envelope"]
    batch = manifest["watermark_batch_attestation_envelope"]
    prescan = manifest["watermark_prescan_receipt"]
    assert isinstance(exemplar, dict)
    assert isinstance(batch, dict)
    assert isinstance(prescan, dict)
    batch_payload = batch["payload"]
    assert isinstance(batch_payload, dict)
    return {
        "schema_version": batch_contract.MANIFEST_SCHEMA_VERSION,
        "status": "verified",
        "entry_count": 2,
        "manual_attestation_count": 1,
        "watermark_batch_candidate_count": 2,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": batch_contract.POLICY_VERSION,
        "classifier_policy_sha256": batch_contract.POLICY_SHA256,
        "manifest_sha256": _sha256_json(manifest),
        "exemplar_envelope_sha256": _sha256_json(exemplar),
        "exemplar_canonical_bitmap_sha256": batch_payload["exemplar_canonical_bitmap_sha256"],
        "prescan_receipt_sha256": _sha256_json(prescan),
        "candidate_set_sha256": batch_payload["candidate_set_sha256"],
        "sample_set_sha256": batch_payload["sample_set_sha256"],
        "batch_envelope_sha256": _sha256_json(batch),
        "public_key_sha256": hashlib.sha256(public_pem).hexdigest(),
        "signature_verified": True,
    }


def test_secure_v2_loader_and_real_collector_admit_exact_hash_bound_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.runtime import dependency_probes, dependency_readiness

    env, manifest, public_pem = _write_secure_fixture(tmp_path)
    loaded = load_document_no_substantive_content_attestation(
        env=env,
        now=batch_contract.ACTIVE_NOW,
    )

    assert isinstance(loaded, VerifiedDocumentNoSubstantiveContentAttestationManifest)
    assert loaded.safe_summary() == _expected_safe_summary(manifest, public_pem=public_pem)

    staging_root = tmp_path / "staging"
    object_root = tmp_path / "objects"
    staging_root.mkdir()
    object_root.mkdir()
    free_bytes = 2 * _INGEST_MAX_BYTES
    monkeypatch.setattr(
        dependency_probes.shutil,
        "disk_usage",
        lambda _path: (free_bytes * 2, free_bytes, free_bytes),
    )
    facts = dependency_probes.collect_ingestion_readiness_facts(
        store_factory=lambda: FilesystemObjectStore(object_root),
        tenant_id=_TENANT_ID,
        staging_root=staging_root,
        object_store_root=object_root,
        password_manifest_loader=DocumentPasswordManifest.none_configured,
        no_substantive_content_attestation_loader=lambda: loaded,
    )

    exemplar = manifest["exemplar_attestation_envelope"]
    batch = manifest["watermark_batch_attestation_envelope"]
    assert isinstance(exemplar, dict)
    assert isinstance(batch, dict)
    batch_payload = batch["payload"]
    assert isinstance(batch_payload, dict)
    assert facts.no_substantive_content_attestation_payload_sha256 == _sha256_json(batch_payload)
    assert facts.no_substantive_content_attestation_envelope_sha256 == _sha256_json(exemplar)
    assert facts.no_substantive_content_attestation_manifest_sha256 == _sha256_json(manifest)
    assert facts.no_substantive_content_attestation_classifier_policy_sha256 == (
        batch_contract.POLICY_SHA256
    )
    assert facts.no_substantive_content_attestation_exemplar_envelope_sha256 == _sha256_json(
        exemplar
    )
    assert (
        facts.no_substantive_content_attestation_exemplar_canonical_bitmap_sha256
        == (batch_payload["exemplar_canonical_bitmap_sha256"])
    )
    assert facts.no_substantive_content_attestation_prescan_receipt_sha256 == _sha256_json(
        manifest["watermark_prescan_receipt"]
    )
    assert (
        facts.no_substantive_content_attestation_candidate_set_sha256
        == (batch_payload["candidate_set_sha256"])
    )
    assert (
        facts.no_substantive_content_attestation_sample_set_sha256
        == (batch_payload["sample_set_sha256"])
    )
    assert facts.no_substantive_content_attestation_batch_envelope_sha256 == _sha256_json(batch)
    assert facts.no_substantive_content_attestation_manual_count == 1
    assert facts.no_substantive_content_attestation_watermark_candidate_count == 2
    assert facts.no_substantive_content_attestation_runtime_unseen_count == 0

    readiness = dependency_readiness.check_ingestion_readiness(
        env={**_ingestion_env(), **env},
        probe=lambda: facts,
    )
    assert (readiness.may_proceed, readiness.code) == (True, None)


def test_secure_v2_loader_rejects_invalid_batch_signature(tmp_path: Path) -> None:
    def corrupt_signature(manifest: dict[str, object]) -> None:
        batch = manifest["watermark_batch_attestation_envelope"]
        assert isinstance(batch, dict)
        batch["signature_base64"] = "A" * 86 + "=="

    env, _manifest, _public_pem = _write_secure_fixture(
        tmp_path,
        mutate=corrupt_signature,
    )

    with pytest.raises(DocumentNoSubstantiveContentAttestationError) as caught:
        load_document_no_substantive_content_attestation(
            env=env,
            now=batch_contract.ACTIVE_NOW,
        )

    assert caught.value.code == "GOVERNANCE_SIGNATURE_INVALID"


def test_secure_v2_loader_rejects_expired_batch(tmp_path: Path) -> None:
    env, _manifest, _public_pem = _write_secure_fixture(tmp_path)

    with pytest.raises(DocumentNoSubstantiveContentAttestationError) as caught:
        load_document_no_substantive_content_attestation(
            env=env,
            now=datetime(2026, 10, 15, tzinfo=UTC),
        )

    assert caught.value.code == "WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE"


def test_secure_v2_loader_does_not_mutate_the_caller_manifest(tmp_path: Path) -> None:
    env, manifest, _public_pem = _write_secure_fixture(tmp_path)
    original = copy.deepcopy(manifest)

    load_document_no_substantive_content_attestation(
        env=env,
        now=batch_contract.ACTIVE_NOW,
    )

    assert manifest == original
