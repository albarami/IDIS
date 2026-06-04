"""Slice83 Task 4 — safe provider/model/prompt-version provenance in the extraction summary.

TDD RED-first. The EXTRACT step summary returned by ``_run_snapshot_extraction`` gains an
additive ``extraction_provenance`` block (provider/backend/model/prompt_id/prompt_version/
strict flag + a sanitized provider_request_id only if the client safely exposes one). It is
built from the configured selection context + the (injected/fake) client — never a real
provider call, never the API key/prompt body/response text/raw payload/exception message/path.
Existing summary fields are unchanged (additive only).
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    STRICT_LIVE_EXTRACTION_PROVIDER_FAILED,
    ExtractorClientSelection,
    StrictLiveExtractionError,
    _build_extraction_provenance,
    _run_snapshot_extraction,
)

_EXISTING_SUMMARY_FIELDS = (
    "status",
    "created_claim_ids",
    "chunk_count",
    "unique_claim_count",
    "conflict_count",
)
_LEAK_MARKERS = ("sk-LEAK123", "sk-ant-", "C:\\secret", "/var/secret", "PROMPT-BODY", "boom")


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeLiveClient:
    provider_request_id = "msg_safe_request_id"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "[]"


# --- default deterministic path records safe deterministic provenance ---


def test_default_deterministic_path_records_safe_provenance() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        summary = _run_snapshot_extraction(
            run_id="run-1", tenant_id="tenant-1", deal_id="deal-1", documents=[], db_conn=None
        )
    prov = summary["extraction_provenance"]
    assert prov["provider"] == "deterministic"
    assert prov["backend"] == "deterministic"
    assert prov["model"] is None
    assert prov["prompt_id"] == "EXTRACT_CLAIMS_V1"
    assert prov["prompt_version"] == "1.0.0"
    assert prov["provider_request_id"] is None
    assert prov["strict_live_extraction_required"] is False


def test_existing_summary_fields_unchanged_and_provenance_is_additive() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        summary = _run_snapshot_extraction(
            run_id="run-1", tenant_id="tenant-1", deal_id="deal-1", documents=[], db_conn=None
        )
    for key in _EXISTING_SUMMARY_FIELDS:
        assert key in summary
    assert "extraction_provenance" in summary


# --- strict live + injected fake records live provenance (no real call) ---


def test_strict_live_injected_fake_records_live_provenance() -> None:
    def fake_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        return _FakeLiveClient()

    env = {
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-sonnet-4-20250514",
    }
    with patch.dict(os.environ, env, clear=False):
        summary = _run_snapshot_extraction(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            documents=[],
            db_conn=None,
            extractor_client_factory=fake_factory,
            strict_live_extraction_required=True,
        )
    prov = summary["extraction_provenance"]
    assert prov["provider"] == "anthropic"
    assert prov["backend"] == "anthropic"
    assert prov["model"] == "claude-sonnet-4-20250514"
    assert prov["prompt_id"] == "EXTRACT_CLAIMS_V1"
    assert prov["prompt_version"] == "1.0.0"
    assert prov["strict_live_extraction_required"] is True
    assert prov["provider_request_id"] == "msg_safe_request_id"


# --- provenance is safe (no leaks); sanitizes a hostile request id ---


def test_provenance_is_safe_and_sanitizes_request_id() -> None:
    class _HostileClient:
        provider_request_id = "msg sk-LEAK123 C:\\secret\\k /var/secret/x"

    selection = ExtractorClientSelection(backend="anthropic", model="claude-x", max_tokens=4096)
    prov = _build_extraction_provenance(
        selection=selection, strict_live_extraction_required=True, client=_HostileClient()
    )
    assert set(prov.keys()) == {
        "provider",
        "backend",
        "model",
        "prompt_id",
        "prompt_version",
        "strict_live_extraction_required",
        "provider_request_id",
    }
    blob = json.dumps(prov)
    for marker in _LEAK_MARKERS:
        assert marker not in blob
    # the id is still surfaced, but redacted (no raw secret/path)
    assert prov["provider_request_id"] is not None
    assert "sk-LEAK123" not in prov["provider_request_id"]


def test_deterministic_client_has_no_provider_request_id() -> None:
    selection = ExtractorClientSelection(backend="deterministic", model=None, max_tokens=4096)
    prov = _build_extraction_provenance(
        selection=selection, strict_live_extraction_required=False, client=object()
    )
    assert prov["provider"] == "deterministic"
    assert prov["provider_request_id"] is None


# --- provider failure path stays safe; Task 3 enforcement code intact ---


def test_strict_provider_failure_does_not_leak_and_keeps_enforcement_code() -> None:
    def failing_factory(selection: ExtractorClientSelection) -> Any:
        raise RuntimeError("boom sk-LEAK123 PROMPT-BODY")

    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-fake-not-real"}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _run_snapshot_extraction(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            documents=[],
            db_conn=None,
            extractor_client_factory=failing_factory,
            strict_live_extraction_required=True,
        )
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_PROVIDER_FAILED
    blob = f"{exc_info.value!s}|{exc_info.value!r}|{exc_info.value.message}"
    for marker in _LEAK_MARKERS:
        assert marker not in blob
