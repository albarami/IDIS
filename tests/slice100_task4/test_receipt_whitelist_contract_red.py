"""RED contracts for the shared manifest-v2 safe-summary whitelist."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from idis.evaluation import real_example_gate as gate
from idis.runtime import dependency_probes
from idis.services.ingestion import no_substantive_content_attestation as attestation_module
from tests.slice100_task4.test_watermark_privacy_readiness_contract_red import (
    _v2_admission_summary,
)

_CONTRACT_NAME = "NO_SUBSTANTIVE_CONTENT_MANIFEST_V2_SAFE_SUMMARY_CONTRACT"


def _shared_contract() -> Any:
    contract = getattr(attestation_module, _CONTRACT_NAME, None)
    assert contract is not None, "the manifest-v2 safe-summary contract must have one source"
    return contract


def test_gate_and_readiness_resolve_the_identical_manifest_v2_contract_object() -> None:
    shared = _shared_contract()

    assert getattr(gate, _CONTRACT_NAME, None) is shared
    assert getattr(dependency_probes, _CONTRACT_NAME, None) is shared


def test_shared_manifest_v2_contract_accepts_only_the_two_approved_shapes() -> None:
    contract = _shared_contract()
    base = _v2_admission_summary()
    reviewed = {
        **base,
        "reviewed_image_only_page_count": 8,
        "reviewed_image_only_set_sha256": "a" * 64,
    }

    assert contract.is_valid(base) is True
    assert contract.is_valid(reviewed) is True


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("count_only", None),
        ("hash_only", None),
        ("reviewed_count", 0),
        ("reviewed_count", -1),
        ("reviewed_count", True),
        ("reviewed_count", 8.0),
        ("reviewed_count", "8"),
        ("reviewed_hash", "not-a-sha256"),
        ("reviewed_hash", "A" * 64),
        ("base_hash", "not-a-sha256"),
        ("unknown_field", None),
        ("wrong_schema", None),
        ("wrong_entry_count_type", None),
    ],
)
def test_shared_manifest_v2_contract_rejects_every_unapproved_shape_or_value(
    mutation: str,
    value: object,
) -> None:
    contract = _shared_contract()
    summary: dict[str, object] = {
        **copy.deepcopy(_v2_admission_summary()),
        "reviewed_image_only_page_count": 8,
        "reviewed_image_only_set_sha256": "a" * 64,
    }
    if mutation == "count_only":
        summary.pop("reviewed_image_only_set_sha256")
    elif mutation == "hash_only":
        summary.pop("reviewed_image_only_page_count")
    elif mutation == "reviewed_count":
        summary["reviewed_image_only_page_count"] = value
    elif mutation == "reviewed_hash":
        summary["reviewed_image_only_set_sha256"] = value
    elif mutation == "base_hash":
        summary["manifest_sha256"] = value
    elif mutation == "unknown_field":
        summary["unexpected"] = "forbidden"
    elif mutation == "wrong_schema":
        summary["schema_version"] = "document-no-substantive-content-attestation-manifest-v3"
    elif mutation == "wrong_entry_count_type":
        summary["entry_count"] = True
    else:  # pragma: no cover - parameter table and branch list must stay aligned.
        raise AssertionError(mutation)

    assert contract.is_valid(summary) is False
