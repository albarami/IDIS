"""Operator-backed RED contract for the signed production watermark exemplar.

The test is skipped in ordinary offline CI when no strict operator profile is
configured.  When configured, it uses the production strict-profile and secure
governance loaders.  It never prints a profile value, filesystem path, signature,
or exemplar bitmap.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from idis.evaluation import real_example_gate as gate
from idis.parsers.watermark_page_classifier import WatermarkPageRuntimeConfig
from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    load_strict_runtime_profile,
)
from idis.services.ingestion import defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    load_document_no_substantive_content_attestation,
)
from tests.slice100_task4.test_pdf_page_census_gate import _safe_v6_proof

_STRICT_DOTENV_PATH_ENV = "IDIS_STRICT_DOTENV_PATH"


def _operator_profile() -> StrictRuntimeProfile:
    if not os.environ.get(_STRICT_DOTENV_PATH_ENV, "").strip():
        pytest.skip("strict operator profile is not configured")

    try:
        return load_strict_runtime_profile(command="api")
    except StrictProfileError as exc:
        pytest.fail(
            f"configured strict operator profile failed production admission: {exc.code}",
            pytrace=False,
        )


def _verified_operator_manifest() -> VerifiedDocumentNoSubstantiveContentAttestationManifest:
    profile = _operator_profile()
    try:
        verified = load_document_no_substantive_content_attestation(env=profile.values)
    except DocumentNoSubstantiveContentAttestationError as exc:
        pytest.fail(
            f"configured operator manifest failed production verification: {exc.code}",
            pytrace=False,
        )
    except Exception:
        pytest.fail("configured operator manifest could not be securely loaded", pytrace=False)

    if not isinstance(verified, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        pytest.fail("configured operator artifact is not the signed production manifest v2")
    if verified.safe_summary().get("signature_verified") is not True:
        pytest.fail("configured operator manifest lacks verified governance provenance")
    return verified


def test_signed_operator_exemplar_builds_the_production_watermark_runtime_config() -> None:
    """The verified operator exemplar must survive production runtime construction."""

    verified = _verified_operator_manifest()

    # RED: the current real manifest reaches WatermarkPageRuntimeConfig.__post_init__
    # and raises WATERMARK_PAGE_EXEMPLAR_INVALID at the production construction seam.
    runtime = defaults._watermark_page_runtime_config(verified)  # noqa: SLF001

    assert isinstance(runtime, WatermarkPageRuntimeConfig)
    assert (
        runtime.exemplar_reference.canonical_bitmap_sha256
        == verified.exemplar_canonical_bitmap_sha256
    )


def test_signed_operator_manifest_persists_through_the_final_v6_receipt_writer(
    tmp_path: Path,
) -> None:
    """The exact production summary must survive the same writer used by acceptance."""

    verified = _verified_operator_manifest()
    summary = verified.safe_summary()
    reviewed_set_sha256 = summary.get("reviewed_image_only_set_sha256")
    assert summary.get("reviewed_image_only_page_count") == 8
    assert isinstance(reviewed_set_sha256, str)
    assert len(reviewed_set_sha256) == 64
    assert all(character in "0123456789abcdef" for character in reviewed_set_sha256)

    receipt = _safe_v6_proof()
    receipt["no_substantive_content_attestation"] = summary
    receipt["no_substantive_content_attestation_sha256"] = gate._canonical_json_sha256(summary)
    report_path = tmp_path / "task4-durable-ingestion-proof.json"

    # RED: acceptance-012 reached this production writer and failed with
    # DATA_ROOM_RECONCILIATION_INCOMPLETE because its private v2 whitelist omitted
    # the signed manifest's paired reviewed-image fields.
    gate._write_safe_durable_ingestion_receipt(report_path, receipt)

    assert json.loads(report_path.read_text(encoding="utf-8")) == receipt
