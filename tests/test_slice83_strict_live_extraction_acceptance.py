"""Slice83 acceptance proof — Strict Live Extraction.

Master-plan acceptance (verbatim):
  - "Synthetic selected FULL uses live extraction under opt-in strict profile."
  - "Missing/failed provider blocks before or during strict run with safe reason."

This proves the acceptance at the extraction seam (the honest deterministic level — a real
strict FULL run would require all components configured and a real provider call, which is out
of scope). It exercises only the extraction wiring with an injected fake live extractor — never
a real provider call, never a real FULL run. Provenance is additive in the open result_summary
(no schema change). Mostly GREEN-on-arrival from Tasks 2–4.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    STRICT_LIVE_EXTRACTION_PROVIDER_FAILED,
    STRICT_LIVE_EXTRACTION_REQUIRED,
    ExtractorClientSelection,
    StrictLiveExtractionError,
    _run_snapshot_extraction,
)
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

_EXISTING_SUMMARY_FIELDS = (
    "status",
    "created_claim_ids",
    "chunk_count",
    "unique_claim_count",
    "conflict_count",
)
_LEAK_MARKERS = (
    "sk-LEAK123",
    "sk-ant-",
    "C:\\secret",
    "/var/secret",
    "PROMPT-BODY",
    "RESPONSE-BODY",
    "boom",
)


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeLiveClient:
    """Injected fake live extractor — never touches the network."""

    provider_request_id = "msg_safe_request_id"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "[]"


def _extract(**kwargs: Any) -> dict[str, Any]:
    return _run_snapshot_extraction(
        run_id="run-1", tenant_id="tenant-1", deal_id="deal-1", documents=[], db_conn=None, **kwargs
    )


# ===== Acceptance 1: synthetic selected FULL uses live extraction under opt-in strict =====


def test_acceptance_strict_full_uses_injected_live_extractor_not_deterministic() -> None:
    captured: dict[str, Any] = {}

    def fake_live_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        captured["selection"] = selection
        return _FakeLiveClient()

    # Opt-in strict profile (strict_live_extraction_required=True) + anthropic backend, but NO
    # ANTHROPIC_API_KEY: the real client would raise, so reaching the summary proves the injected
    # fake live client was used (no real provider call, no deterministic fallback).
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    env["IDIS_ANTHROPIC_MODEL_EXTRACT"] = "claude-sonnet-4-20250514"
    with patch.dict(os.environ, env, clear=True):
        summary = _extract(
            extractor_client_factory=fake_live_factory, strict_live_extraction_required=True
        )

    assert captured["selection"].backend == "anthropic"  # live extractor selected
    prov = summary["extraction_provenance"]
    assert prov["provider"] == "anthropic"  # not deterministic
    assert prov["backend"] == "anthropic"
    assert prov["model"] == "claude-sonnet-4-20250514"
    assert prov["strict_live_extraction_required"] is True
    assert prov["provider_request_id"] == "msg_safe_request_id"


# ===== Acceptance 2: strict flag is threaded through the shared API/worker context funnel =====


def test_acceptance_strict_flag_threaded_into_extract_fn() -> None:
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.runs.steps import build_run_context

    # build_run_context is the shared funnel both API start_run and the worker use; it binds the
    # strict flag into the extract step's partial (mode-gated by the callers to FULL).
    strict_ctx = build_run_context(
        db_conn=None,
        tenant_id="t",
        run_id="r",
        deal_id="d",
        mode="SNAPSHOT",
        documents=[],
        audit_sink=InMemoryAuditSink(),
        strict_live_extraction_required=True,
    )
    assert strict_ctx.extract_fn.keywords.get("strict_live_extraction_required") is True

    default_ctx = build_run_context(
        db_conn=None,
        tenant_id="t",
        run_id="r",
        deal_id="d",
        mode="SNAPSHOT",
        documents=[],
        audit_sink=InMemoryAuditSink(),
    )
    assert default_ctx.extract_fn.keywords.get("strict_live_extraction_required") is False


# ===== Acceptance 3: strict FULL + missing/unset provider blocks safely =====


def test_acceptance_strict_unset_provider_blocks_with_required_code_safely() -> None:
    env = _env_without("IDIS_EXTRACT_BACKEND")
    env["ANTHROPIC_API_KEY"] = "sk-LEAK123-secret-value"  # present but backend unset
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _extract(strict_live_extraction_required=True)
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_REQUIRED
    blob = f"{exc_info.value!s}|{exc_info.value!r}|{exc_info.value.message}"
    for marker in (*_LEAK_MARKERS, "sk-LEAK123-secret-value"):
        assert marker not in blob


# ===== Acceptance 4: strict FULL + failed provider blocks safely =====


def test_acceptance_strict_failed_provider_blocks_with_provider_failed_code_safely() -> None:
    def failing_factory(selection: ExtractorClientSelection) -> Any:
        raise RuntimeError("boom sk-LEAK123 PROMPT-BODY RESPONSE-BODY")

    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-fake-not-real"}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _extract(extractor_client_factory=failing_factory, strict_live_extraction_required=True)
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_PROVIDER_FAILED
    blob = f"{exc_info.value!s}|{exc_info.value!r}|{exc_info.value.message}"
    for marker in _LEAK_MARKERS:
        assert marker not in blob


# ===== Acceptance 5: non-strict FULL / SNAPSHOT still use deterministic =====


def test_acceptance_non_strict_and_snapshot_use_deterministic() -> None:
    # Non-strict (the SNAPSHOT path and non-strict FULL never set the flag).
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        summary = _extract(strict_live_extraction_required=False)
    assert summary["extraction_provenance"]["provider"] == "deterministic"

    # Even with IDIS_REQUIRE_FULL_LIVE=1, the SNAPSHOT path (no explicit flag) stays deterministic.
    env = _env_without("IDIS_EXTRACT_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        from idis.api.routes.runs import _build_extraction_llm_client

        assert isinstance(_build_extraction_llm_client(), DeterministicLLMClient)


# ===== Acceptance 6: step summary records safe additive provenance =====


def test_acceptance_step_summary_records_safe_additive_provenance() -> None:
    def fake_live_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        return _FakeLiveClient()

    env = {
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
        "ANTHROPIC_API_KEY": "sk-ant-CONFIDENTIAL-not-real",
    }
    with patch.dict(os.environ, env, clear=False):
        summary = _extract(
            extractor_client_factory=fake_live_factory, strict_live_extraction_required=True
        )
    prov = summary["extraction_provenance"]
    assert set(prov.keys()) == {
        "provider",
        "backend",
        "model",
        "prompt_id",
        "prompt_version",
        "strict_live_extraction_required",
        "provider_request_id",
    }
    assert prov["prompt_id"] == "EXTRACT_CLAIMS_V1"
    assert prov["prompt_version"] == "1.0.0"
    # the API key (and any prompt/response/payload) never reaches the summary
    blob = json.dumps(summary)
    assert "CONFIDENTIAL" not in blob
    assert "sk-ant-" not in blob


# ===== Acceptance 7: provenance is additive in the open result_summary (no schema change) =====


def test_acceptance_provenance_is_additive_no_schema_change() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        summary = _extract(strict_live_extraction_required=False)
    assert isinstance(summary, dict)  # open dict; RunStep.result_summary is dict[str, Any]
    for key in _EXISTING_SUMMARY_FIELDS:
        assert key in summary  # existing fields unchanged
    assert "extraction_provenance" in summary  # additive only
