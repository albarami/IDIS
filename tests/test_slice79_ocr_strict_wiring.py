"""Slice79 Task 3 — wire OCR health into strict readiness + provisioning truth.

TDD RED-first. Verifies that strict readiness and the provisioning-truth report
consume ``check_ocr_health`` (via an injectable checker), fail closed when an
OCR-required corpus is present but health is not ready, treat disabled OCR as an
expected non-error state, and never leak paths/secrets/OCR content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from idis.services.ocr_health import OcrHealthCheck
from idis.services.runs.strict_full_live import (
    StrictComponentStatus,
    build_strict_full_live_readiness_report,
    build_strict_provisioning_truth_report,
)

_OCR_REQUIRED_CORPUS: list[Mapping[str, Any]] = [
    {
        "document_id": "doc-ocr-1",
        "document_name": "scanned.png",
        "doc_type": "IMAGE",
        "metadata": {"parser_requires_ocr": True, "parser_reason_codes": ["ocr_required"]},
    }
]


def _readiness(
    ocr_health: OcrHealthCheck,
    *,
    corpus: list[Mapping[str, Any]] | None = None,
) -> Any:
    return build_strict_full_live_readiness_report(
        preflight_corpus=_OCR_REQUIRED_CORPUS if corpus is None else corpus,
        env={},
        load_byol_env_credentials=False,
        binary_resolver=lambda _name: None,
        ocr_health_checker=lambda _env: ocr_health,
    )


def _ocr_inventory(report: Any) -> Any:
    return next(item for item in report.component_inventory if item.component_name == "OCR")


# ----- strict readiness -----


def test_ocr_required_healthy_is_live() -> None:
    ocr = _readiness(OcrHealthCheck.healthy()).component("ocr")
    assert ocr.status is StrictComponentStatus.LIVE_WIRED_AND_USED
    assert ocr.may_proceed is True


def test_ocr_required_missing_dependencies_blocks_with_safe_services() -> None:
    ocr = _readiness(OcrHealthCheck.missing(dependencies=["poppler", "tesseract"])).component("ocr")
    assert ocr.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert ocr.may_proceed is False
    assert "OCR-required documents" in ocr.blocker_message
    assert "tesseract" in ocr.required_services
    assert "poppler" in ocr.required_services


def test_ocr_required_disabled_blocks_with_enable_env() -> None:
    ocr = _readiness(OcrHealthCheck.disabled()).component("ocr")
    assert ocr.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    assert ocr.may_proceed is False
    assert "IDIS_OCR_ENABLED=1" in ocr.required_env_vars


def test_ocr_required_failed_blocks_without_leaking_error() -> None:
    confidential = "C:\\secret\\scan.png OCR-CONTENT-MARKER sk-LEAK123"
    ocr = _readiness(OcrHealthCheck.failed(error=confidential)).component("ocr")
    assert ocr.status is StrictComponentStatus.MISSING_INFRASTRUCTURE
    blob = ocr.model_dump_json()
    assert "C:\\secret" not in blob
    assert "OCR-CONTENT-MARKER" not in blob
    assert "sk-LEAK123" not in blob


def test_ocr_not_required_may_proceed_even_if_health_failed() -> None:
    ocr = _readiness(OcrHealthCheck.failed(error="boom"), corpus=[]).component("ocr")
    assert ocr.status is StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert ocr.may_proceed is True


def test_ocr_inventory_reflects_health() -> None:
    healthy_inv = _ocr_inventory(_readiness(OcrHealthCheck.healthy()))
    assert healthy_inv.full_wired is True
    assert healthy_inv.health_check_status == "healthy"

    blocked_inv = _ocr_inventory(_readiness(OcrHealthCheck.missing(dependencies=["tesseract"])))
    assert blocked_inv.full_wired is False
    assert blocked_inv.health_check_status == "configured_failed"


def test_default_ocr_readiness_requires_full_health_not_just_binary() -> None:
    # tesseract present + OCR enabled, but poppler absent: old binary-only logic would pass;
    # the full health check must fail-close.
    report = build_strict_full_live_readiness_report(
        preflight_corpus=_OCR_REQUIRED_CORPUS,
        env={"IDIS_OCR_ENABLED": "1"},
        load_byol_env_credentials=False,
        binary_resolver=lambda name: "/usr/bin/tesseract" if name == "tesseract" else None,
    )
    assert report.component("ocr").status is StrictComponentStatus.MISSING_INFRASTRUCTURE


# ----- provisioning truth -----


def _provisioning(ocr_health: OcrHealthCheck | None = None, *, allow: bool = False) -> Any:
    return build_strict_provisioning_truth_report(
        env={},
        allow_local_strict_health_probes=allow,
        ocr_health_checker=(lambda _env: ocr_health) if ocr_health is not None else None,
    )


def _component(report: Any, name: str) -> Any:
    return next(c for c in report["components"] if c["component_name"] == name)


def test_provisioning_ocr_not_probed_by_default() -> None:
    ocr = _component(_provisioning(allow=False), "OCR")
    assert ocr["local_probe_attempted"] is False
    assert ocr["health_checked"] is False
    assert ocr["health_check_status"] in {"not_run", "configured_not_checked"}


def test_provisioning_ocr_local_probe_healthy_when_opted_in() -> None:
    ocr = _component(_provisioning(OcrHealthCheck.healthy(), allow=True), "OCR")
    assert ocr["local_probe_attempted"] is True
    assert ocr["local_probe_passed"] is True
    assert ocr["health_check_status"] == "healthy"
    assert ocr["local_probe_label"] == "ocr_local_health"


def test_provisioning_ocr_disabled_is_expected_non_error() -> None:
    ocr = _component(_provisioning(OcrHealthCheck.disabled(), allow=True), "OCR")
    assert ocr["local_probe_attempted"] is False
    assert ocr["local_probe_passed"] is False
    assert ocr["local_probe_blocker"] == "ocr_disabled"


def test_provisioning_ocr_missing_dependencies_is_configured_failed() -> None:
    ocr = _component(
        _provisioning(OcrHealthCheck.missing(dependencies=["poppler"]), allow=True), "OCR"
    )
    assert ocr["local_probe_attempted"] is True
    assert ocr["local_probe_passed"] is False
    assert ocr["health_check_status"] == "configured_failed"


def test_provisioning_ocr_health_does_not_leak() -> None:
    report = _provisioning(
        OcrHealthCheck.failed(error="C:\\secret\\scan.png sk-LEAK123 OCR-CONTENT-MARKER"),
        allow=True,
    )
    blob = json.dumps(report)
    assert "C:\\secret" not in blob
    assert "sk-LEAK123" not in blob
    assert "OCR-CONTENT-MARKER" not in blob
