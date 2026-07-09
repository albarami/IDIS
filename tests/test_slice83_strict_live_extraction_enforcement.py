"""Slice83 Task 3 — execution-time strict live extraction enforcement (no provenance yet).

TDD RED-first. When ``strict_live_extraction_required=True`` (threaded only from the strict
FULL execution path), ``_build_extraction_llm_client`` must NEVER return a deterministic
client: a non-anthropic backend fails closed with the safe code
``STRICT_LIVE_EXTRACTION_REQUIRED``; an anthropic provider construction/call failure fails
closed with ``STRICT_LIVE_EXTRACTION_PROVIDER_FAILED`` — both carrying only safe, fixed
strings (no API key/env value/prompt/response/provider payload/raw exception message). An
injected fake live extractor is allowed (no real provider call). Non-strict and SNAPSHOT
paths are unchanged.
"""

from __future__ import annotations

import inspect
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    STRICT_LIVE_EXTRACTION_PROVIDER_FAILED,
    STRICT_LIVE_EXTRACTION_REQUIRED,
    ExtractorClientSelection,
    StrictLiveExtractionError,
    _build_extraction_llm_client,
    _run_snapshot_extraction,
)
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

_FAKE_KEY = "sk-ant-test-fake-key-for-unit-test"
_LEAK_MARKERS = (
    "sk-LEAK123",
    "PROMPT-BODY",
    "RESPONSE-BODY",
    "boom",
    "secret-value",
)


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeLiveClient:
    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "[]"


class _StopForTest(Exception):
    pass


def _surfaced(err: StrictLiveExtractionError) -> str:
    return f"{err.code}|{err.message}|{err!s}|{err!r}"


# --- strict + non-anthropic backend is blocked (no deterministic client) ---


def test_strict_required_blocks_unset_backend_without_deterministic() -> None:
    with (
        patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _build_extraction_llm_client(strict_live_extraction_required=True)
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_REQUIRED


def test_strict_required_blocks_explicit_deterministic_backend() -> None:
    with (
        patch.dict(os.environ, {"IDIS_EXTRACT_BACKEND": "deterministic"}, clear=False),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _build_extraction_llm_client(strict_live_extraction_required=True)
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_REQUIRED


def test_strict_required_block_message_is_safe() -> None:
    env = _env_without("IDIS_EXTRACT_BACKEND")
    env["ANTHROPIC_API_KEY"] = "sk-LEAK123-secret-value"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _build_extraction_llm_client(strict_live_extraction_required=True)
    blob = _surfaced(exc_info.value)
    for marker in (*_LEAK_MARKERS, "sk-LEAK123-secret-value"):
        assert marker not in blob


# --- strict + anthropic + injected fake live extractor is allowed (no real call) ---


def test_strict_required_anthropic_uses_injected_fake_no_real_call() -> None:
    captured: dict[str, Any] = {}

    def fake_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        captured["selection"] = selection
        return _FakeLiveClient()

    # No ANTHROPIC_API_KEY: proves the real client is NOT constructed (it would raise).
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        client = _build_extraction_llm_client(
            strict_live_extraction_required=True, extractor_client_factory=fake_factory
        )
    assert isinstance(client, _FakeLiveClient)
    assert not isinstance(client, DeterministicLLMClient)
    assert captured["selection"].backend == "anthropic"


# --- strict + anthropic provider construction/call failure fails safely ---


def test_strict_required_anthropic_provider_failure_is_safe() -> None:
    confidential = "boom sk-LEAK123 PROMPT-BODY RESPONSE-BODY secret-value"

    def failing_factory(selection: ExtractorClientSelection) -> Any:
        raise RuntimeError(confidential)

    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _build_extraction_llm_client(
            strict_live_extraction_required=True, extractor_client_factory=failing_factory
        )
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_PROVIDER_FAILED
    blob = _surfaced(exc_info.value)
    for marker in _LEAK_MARKERS:
        assert marker not in blob


def test_strict_required_anthropic_missing_key_no_factory_is_provider_failed() -> None:
    # The real AnthropicLLMClient raises ValueError mentioning ANTHROPIC_API_KEY; the wrapper
    # must surface only the safe code/message and never that raw message.
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(StrictLiveExtractionError) as exc_info,
    ):
        _build_extraction_llm_client(strict_live_extraction_required=True)
    assert exc_info.value.code == STRICT_LIVE_EXTRACTION_PROVIDER_FAILED
    assert "ANTHROPIC_API_KEY" not in _surfaced(exc_info.value)


# --- non-strict + SNAPSHOT paths are unchanged ---


def test_non_strict_unset_backend_still_deterministic() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        client = _build_extraction_llm_client(strict_live_extraction_required=False)
    assert isinstance(client, DeterministicLLMClient)


def test_snapshot_path_allows_deterministic_even_when_require_full_live_set() -> None:
    # SNAPSHOT execution never passes strict_live_extraction_required=True, so deterministic
    # remains allowed even when IDIS_REQUIRE_FULL_LIVE=1 (no global strict inference here).
    env = _env_without("IDIS_EXTRACT_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        client = _build_extraction_llm_client(strict_live_extraction_required=False)
    assert isinstance(client, DeterministicLLMClient)


# --- threading: signatures + forwarding ---


def test_seam_signatures_accept_strict_flag() -> None:
    for fn in (_build_extraction_llm_client, _run_snapshot_extraction):
        params = inspect.signature(fn).parameters
        assert "strict_live_extraction_required" in params
        assert params["strict_live_extraction_required"].default is False


def test_build_run_context_accepts_strict_flag() -> None:
    from idis.services.runs.steps import build_run_context

    params = inspect.signature(build_run_context).parameters
    assert "strict_live_extraction_required" in params
    assert params["strict_live_extraction_required"].default is False


def test_run_snapshot_extraction_forwards_strict_flag_to_builder() -> None:
    captured: dict[str, Any] = {}

    def fake_build(
        *,
        extractor_client_factory: Any = None,
        strict_live_extraction_required: bool = False,
        tenant_id: Any = None,
    ) -> Any:
        captured["strict"] = strict_live_extraction_required
        captured["tenant_id"] = tenant_id
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_extraction_llm_client", fake_build),
        pytest.raises(_StopForTest),
    ):
        _run_snapshot_extraction(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            documents=[],
            db_conn=None,
            strict_live_extraction_required=True,
        )
    assert captured["strict"] is True
    assert captured["tenant_id"] == "tenant-1"  # tenant flows to the budget-wrapping helper
